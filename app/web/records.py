"""记录核心构建、打印渲染与原包重放计算（REQ-13；Annex C C3.1/C3.3/C3.4/C6）。

REQ: REQ-13（record 全字段：税项/期间/输入摘要/binding/步骤（税率、扣除、
暂缴、宽减）/官方出处/未覆盖/免责；同快照可重现；回放兼容＝引擎版本完全
相等；回放≠当前重评）。
规格锚点:
  - Annex C C2.8：CalculationCore 封闭 11 字段为 hash 对象；period／
    questions／warnings／disclaimer 属运行时信封字段（同存于记录、不入 hash）。
  - Annex C C3.3/C3.4：status 权威四态；amounts 恰六键 canonical 字符串或
    None；partial/blocked 不填零。
  - Annex C C3.1 replay 行：按确认事实＋原 binding 校验后重算；引擎版本
    完全相等才兼容（不完全相等 → 409 E_BUNDLE_INCOMPATIBLE，不降级、不迁移）；
    不信任导出税额字段。
  - Annex A §6.6：打印保留输入摘要、步骤、版本、出处、警告；无交互控件。
期望值来源: 结构性契约；引擎为只读调用的纯函数（app/engines/*）。
"""

from __future__ import annotations

import html
import json
from decimal import Decimal
from fractions import Fraction
from typing import Any, Mapping

from app.core.errors import AppError
from app.core.hashing import bundle_content_hash
from app.engines.personal_assessment import compare_personal_assessment
from app.engines.profits import calculate_profits_tax
from app.engines.property import calculate_property_tax
from app.engines.salaries import calculate_salaries_tax
from app.engines.stamps.lease import calculate_lease_stamp_duty
from app.engines.stamps.property import calculate_property_avd
from app.engines.stamps.stock import calculate_stock_stamp_duty

# REQ-13：免责声明（记录字段与打印渲染共用；非官方认证、仅为估算）
DISCLAIMER = (
    "本工具不宣称获官方认证；所有输出仅为按已发布规则束的估算，"
    "不构成税务意见。"
)

# Annex A §12.2：重放历史记录的权威中文标示（回放≠当前重评）
REPLAY_MARKER = "历史估算，非当前重新评估"

# Annex A §6/§12.1：四状态徽标权威文案（打印按服务器 canonical status 直映）
STATUS_BADGES = {
    "complete": "估算完成",
    "partial": "部分结果",
    "needs_input": "需要补充资料",
    "blocked": "暂不可计算",
}

# C3.4 六金额键中文标签（打印直读分项；None → 「未能估算」，禁零填补）
AMOUNT_LABELS: tuple[tuple[str, str], ...] = (
    ("before_reduction", "宽减前税款"),
    ("reduction", "年度宽减"),
    ("final_after_reduction", "最终税款"),
    ("provisional_paid", "已缴暂缴税"),
    ("next_provisional", "下年度暂缴税"),
    ("balance", "结欠／退税（正＝欠款，负＝估算退税）"),
)


# ------------------------------------------------------------------ 引擎调用
# 六个已批准税项（7 个执行目标）→ app/engines/* 纯函数（只读调用）。
_ENGINE_BY_TAX_TYPE = {
    "salaries_tax": calculate_salaries_tax,
    "profits_tax": calculate_profits_tax,
    "property_tax": calculate_property_tax,
    "personal_assessment": compare_personal_assessment,
    "stamp_property": calculate_property_avd,
    "stamp_stock": calculate_stock_stamp_duty,
    "stamp_lease": calculate_lease_stamp_duty,
}


def run_engine(tax_type: str, facts: Mapping, bundle: Mapping) -> dict:
    """按税项调用对应纯函数引擎（app/engines/*，只读调用）。

    事实校验入口见 app.api.validation（C3.5，六税项全部开放）；未知税项在此
    显式拒绝（无死分支、不静默 blocked）。
    """
    engine = _ENGINE_BY_TAX_TYPE.get(tax_type)
    if engine is None:
        raise AppError(
            "E_INPUT_TYPE",
            f"未知税项：{tax_type!r}（不在已批准税项执行表内）",
            field="tax_type",
        )
    return engine(facts, bundle)


# ------------------------------------------------------------------ 记录核心
def build_core(
    tax_type: str,
    facts: Mapping,
    binding: Mapping,
    result: Mapping,
    bundle_content: Mapping,
) -> dict:
    """REQ-13 全字段记录核心（C2.8 十一字段入 hash；信封字段并列存储）。"""
    status = str(result.get("status") or "blocked")
    period = facts.get("year_of_assessment") or facts.get("instrument_date")
    return {
        "tax_type": tax_type,
        "period": period,
        "input": facts,
        "binding": dict(binding),
        "status": status,
        "amounts": dict(result.get("amounts") or {}),
        "blocked_reason": result.get("blocked_reason"),
        "steps": build_steps(tax_type, bundle_content, facts, result)
        if status in ("complete", "partial")
        else [],
        "evidence_refs": list(bundle_content.get("evidence_digests") or []),
        "unsupported": list(result.get("unsupported") or []),
        "pending_verification": list(result.get("pending_verification") or []),
        "missing_components": list(result.get("missing_components") or []),
        "questions": list(result.get("questions") or []),
        "warnings": list(result.get("warnings") or []),
        "disclaimer": DISCLAIMER,
    }


def build_steps(
    tax_type: str,
    bundle_content: Mapping,
    facts: Mapping,
    result: Mapping,
) -> list[dict]:
    """complete/partial 记录的计算步骤（§6.4/Annex A §6.6：计税基数、适用
    税率、扣除/免税额、取整及中间结果；依据＝确认事实＋规则束数值，金额
    直读引擎 canonical 分项——不编造）。

    partial（部分结果，C3.3/C3.4）：已算组件照常呈现——缺组件（None 金额）
    的步骤省略，不以零或猜测填补。
    """
    builder = _STEPS_BUILDERS.get(tax_type)
    if builder is None:
        return []
    return builder(bundle_content, facts, result)


def _step(number: int, label: str) -> dict:
    return {"step": number, "label": label}


def _salaries_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    data = bundle_content["data"]["salaries"]
    year = str(facts.get("year_of_assessment") or "")
    year_data = data["allowances"].get(year) or {}
    amounts = result["amounts"]
    progressive = "／".join(
        _percent(band["rate_bp"]) for band in data["progressive_bands"]
    )
    standard = "／".join(
        _percent(band["rate_bp"]) for band in data["standard_rate_bands"]
    )
    entry = data["reductions"].get(year)
    if entry is None:
        reduction_desc = f"{year} 无宽减条目（宽减 0）"
    else:
        reduction_desc = (
            f"税额×{_percent(entry['percent_bp'])}、上限 {entry['cap']} 港元封顶"
        )
    parent_desc = ""
    if year_data.get("parent_aged_60_or_above"):
        parent_desc = (
            f"；受养父母（s.30/30A）：60+ {year_data.get('parent_aged_60_or_above')}"
            f"／55–59 {year_data.get('parent_aged_55_to_59')} 港元"
            "（全年连续同住额外同额，s.30(3)(b)）"
        )
    steps = [
        _step(
            1,
            "输入事实：应课税收入 "
            f"{facts.get('employment_income')} 港元；强积金强制性供款 "
            f"{facts.get('mpf_mandatory_contributions')} 港元。"
        ),
        _step(
            2,
            "扣除（deduction）：强积金强制性供款按附表 3B 每人上限 "
            f"{data['mpf_deduction_cap']} 港元封顶后从收入中扣除。"
        ),
        _step(
            3,
            "免税额（allowance）：按附表 4 "
            f"{year} 行套用（基本 {year_data.get('basic')}／已婚 "
            f"{year_data.get('married')}／每名子女 {year_data.get('child')}"
            f" 港元{parent_desc}）。"
        ),
        _step(
            4,
            f"累进税率（rate）计税：按附表 2 累进带（{progressive}）"
            "逐级计算，各径结果向下取整（floor，取整口径）。"
        ),
        _step(
            5,
            f"标准税率（rate）计税：按附表 1 两级标准（{standard}）"
            "计算并向下取整（floor）。"
        ),
        _step(
            6,
            "两径择低：取累进径与标准径较小者（floor 后相等取累进）→ 宽减前税款 "
            f"{amounts.get('before_reduction')} 港元。"
        ),
        _step(
            7,
            f"年度宽减（reduction，附表 43）：{reduction_desc} → 宽减 "
            f"{amounts.get('reduction')} 港元；最终税款 "
            f"{amounts.get('final_after_reduction')} 港元。"
        ),
    ]
    if amounts.get("next_provisional") is not None:
        steps.append(
            _step(
                len(steps) + 1,
                "暂缴税（provisional）：以同一事实按下一年度规则估算"
                "（附表 43 宽减不适用暂缴）→ 下年度暂缴税 "
                f"{amounts.get('next_provisional')} 港元。"
            )
        )
    if amounts.get("provisional_paid") is not None:
        steps.append(
            _step(
                len(steps) + 1,
                "结欠／退税 balance ＝ 最终税款 − 已缴暂缴税 "
                f"{amounts.get('provisional_paid')} 港元 ＝ "
                f"{amounts.get('balance')}（正＝欠款，负＝估算退税）。"
            )
        )
    return steps


def _profits_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    data = bundle_content["data"]["profits"]
    amounts = result["amounts"]
    year = str(facts.get("year_of_assessment") or "")
    kind_zh = {
        "corporation": "法团",
        "partnership": "合伙（非法团）",
        "sole_proprietorship": "独资（非法团）",
    }.get(str(facts.get("entity_kind") or ""), str(facts.get("entity_kind") or ""))
    corp = data["two_tier_rates_bp"]["corporation"]
    uninc = data["two_tier_rates_bp"]["unincorporated"]
    entry = (data.get("reductions") or {}).get(year)
    if entry is None:
        reduction_desc = f"{year} 无宽减条目（宽减 0）"
    else:
        reduction_desc = (
            f"每业务一个：税额×{_percent(entry['percent_bp'])} 后按上限 "
            f"{entry['cap']} 港元封顶"
        )
    return [
        _step(
            1,
            f"输入事实：{kind_zh}；应评税利润 "
            f"{facts.get('assessable_profit')} 港元（亏损结转/分配表等按"
            "确认事实代入）。"
        ),
        _step(
            2,
            f"税率（两级制，2018/19 起）：两级门槛 "
            f"{data['two_tier_threshold']} 港元——法团低档 "
            f"{_percent(corp['low'])}／高档 {_percent(corp['high'])}、"
            f"非法团低档 {_percent(uninc['low'])}／高档 "
            f"{_percent(uninc['high'])}；择档＝floor(计税利润)≤门槛 → "
            "低档全率，否则门槛×低档率＋余额×高档率。"
        ),
        _step(
            3,
            "取整口径：非法团＝官方计算器 floor 链（择档 floor(净利)、税额 "
            "raw 计算后 floor）；法团/混合＝产品约定 floor 至整元；宽减 "
            "ceil 后扣。"
        ),
        _step(
            4,
            f"宽减前税款 {amounts.get('before_reduction')} 港元；附表 43 "
            f"宽减（reduction）：{reduction_desc} → 宽减 "
            f"{amounts.get('reduction')} 港元；最终税款 "
            f"{amounts.get('final_after_reduction')} 港元。"
        ),
        _step(
            5,
            "下年度暂缴（provisional）＝同一事实按下一年度规则估算"
            "（宽减不适用暂缴）→ "
            f"{amounts.get('next_provisional')} 港元。"
        ),
    ]


def _property_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    rules = bundle_content["data"]["property"]
    amounts = result["amounts"]
    year = str(facts.get("year_of_assessment") or "")
    rate = ((rules.get("years") or {}).get(year) or {}).get("standard_rate")
    details = result.get("properties") or []
    per_property = "；".join(
        f"物业 {item.get('property_id')}：NAV {item.get('nav')}、税款 {item.get('tax')}"
        for item in details
    )
    return [
        _step(
            1,
            "输入事实（评税单位＝逐项物业，BIR57 Note 1(b)）："
            f"{len(details)} 项物业，逐项租金收入/业主同意且已缴差饷/"
            "不可追回租金/按金抵销按确认事实代入。"
        ),
        _step(
            2,
            "逐物业 NAV（应评税净值）＝（租金−同意且已缴差饷−不可追回租金）"
            "×(1−1/5) 法定修葺及支出免税额（s.5(1A)），先括号后 floor"
            f"（取整点一）：{per_property}。"
        ),
        _step(
            3,
            f"税率＝标准税率 {rate}（15%）：逐物业税款＝floor(NAV×15%)"
            "（取整点二，逐项取整后相加）→ 宽减前税款 "
            f"{amounts.get('before_reduction')} 港元。"
        ),
        _step(
            4,
            "附表 43 无物业税行 → 宽减 "
            f"{amounts.get('reduction')}（不套用他税宽减）；最终税款 "
            f"{amounts.get('final_after_reduction')} 港元；下年度暂缴"
            f"（同口径）{amounts.get('next_provisional')} 港元。"
        ),
    ]


def _pa_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    data = bundle_content["data"]["personal_assessment"]
    amounts = result["amounts"]
    year = str(facts.get("year_of_assessment") or "")
    year_data = data["allowances"].get(year) or {}
    progressive = "／".join(
        _percent(band["rate_bp"]) for band in data["progressive_bands"]
    )
    standard = "／".join(
        _percent(band["rate_bp"]) for band in data["standard_rate_bands"]
    )
    entry = data["reductions"].get(year)
    if entry is None:
        reduction_desc = f"{year} 无宽减条目（宽减 0）"
    else:
        reduction_desc = (
            f"税额×{_percent(entry['percent_bp'])}、上限 {entry['cap']} 港元封顶"
        )
    persons = (facts.get("persons") or []) or []
    best = result.get("best_scenario")
    best_desc = (
        "个人入息课税（personal_assessment）"
        if best == "personal_assessment"
        else "分别评税（no_pa）"
        if best == "no_pa"
        else "两方案比较后择优"
    )
    return [
        _step(
            1,
            "输入事实：按人汇聚（"
            f"{len(persons)} 名人士；婚姻状况/物业/业务/受养人按确认事实"
            "代入）。"
        ),
        _step(
            2,
            "方案比较：个人入息课税（PA）与不选 PA 的分别评税（no_pa）"
            "两方案各自全链路计算，取宽减后终额较小者（平局取 PA；"
            "s.100(4)/(5) 先减宽减再比较）。"
        ),
        _step(
            3,
            "免税额（allowance）：按附表 4 "
            f"{year} 行（基本 {year_data.get('basic')}／已婚 "
            f"{year_data.get('married')} 港元；已婚合并按 s.42A 一份已婚"
            "免税额覆盖两人）。"
        ),
        _step(
            4,
            f"税率两径：累进（附表 2：{progressive}）对减少后总入息−免税额，"
            f"标准上限（附表 1：{standard}）对 PA 减少后总入息（s.43(1A)）；"
            "两径分别 floor 后择低、平局取累进（取整口径）。"
        ),
        _step(
            5,
            f"宽减（附表 43）：{reduction_desc} → 宽减前税款 "
            f"{amounts.get('before_reduction')} 港元、宽减 "
            f"{amounts.get('reduction')} 港元、宽减后终额（权威金额）"
            f"{amounts.get('final_after_reduction')} 港元；所选方案＝{best_desc}。"
        ),
        _step(
            6,
            "下年度暂缴（provisional）＝同一事实按下一年度规则估算"
            "（宽减不适用暂缴）→ "
            f"{amounts.get('next_provisional')} 港元。"
        ),
    ]


def _stamp_property_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    amounts = result["amounts"]
    detail = result.get("instrument_detail") or {}
    property_class = facts.get("property_class")
    class_desc = (
        f"、物业类别 {property_class}" if property_class else ""
    )
    return [
        _step(
            1,
            "输入事实：文书日期 "
            f"{facts.get('instrument_date')}、文书类别 "
            f"{facts.get('instrument_kind')}{class_desc}。"
        ),
        _step(
            2,
            "计税基数 B＝max(consideration, value) 精确金额＝"
            f"{detail.get('base_amount')} 港元（1999-04-01 起不向上取整至 "
            "100）。"
        ),
        _step(
            3,
            "税率：按文书日期选定分档从价税率表（区间下开上闭选行，"
            "行公式 duty＝fixed＋率×(B−excess_over)；禁止相邻公式取最小"
            "或插值）。"
        ),
        _step(
            4,
            "取整：选行后仅对税额 ceil 至 1 元（不足 $1 按 $1 计）→ 应缴"
            f"印花税 {amounts.get('before_reduction')} 港元（一次性宽减 "
            f"{amounts.get('reduction')}，最终 {amounts.get('final_after_reduction')} 港元）。"
        ),
    ]


def _stamp_stock_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    rules = bundle_content["data"]["stamps"]["stock"]
    amounts = result["amounts"]
    documents = result.get("documents") or []
    docs_desc = "；".join(
        f"{item.get('doc_kind')}：{item.get('duty')} 港元" for item in documents
    )
    return [
        _step(
            1,
            "输入事实：每份文书独立计税（"
            f"{len(documents)} 份）——{docs_desc}。"
        ),
        _step(
            2,
            "税率（Cap.117 First Schedule head 2 现行文本）：contract note＝"
            "代价或价值 × "
            f"{rules['contract_note_rate']}（按代价额口径、非取较高者）；"
            "voluntary inter vivos＝$5＋价值×"
            f"{rules['voluntary_inter_vivos_rate']}；其他转让 $5/份。"
        ),
        _step(
            3,
            "取整：每份分别 ceil 至 1 元（不足 $1 向上取整至 $1，"
            "s.18A），sold／bought 不合并、不互摊 → 合计应缴印花税 "
            f"{amounts.get('before_reduction')} 港元（一次性宽减 "
            f"{amounts.get('reduction')}）。"
        ),
    ]


def _stamp_lease_steps(
    bundle_content: Mapping, facts: Mapping, result: Mapping
) -> list[dict]:
    rules = bundle_content["data"]["stamps"]["lease"]
    amounts = result["amounts"]
    breakdown = result.get("duty_breakdown") or {}
    tier_labels = {
        -1: "期限未界定／不确定",
        0: "租期不超过 1 年",
        1: "租期超过 1 年至 3 年",
        3: "租期超过 3 年",
    }
    tiers_desc = "、".join(
        f"{tier_labels.get(int(tier['min_years']), tier['min_years'])} → "
        f"{_ratio_percent(tier['rate'])}"
        for tier in rules["rent_rate_tiers"]
    )
    return [
        _step(
            1,
            "输入事实：文书日期 "
            f"{facts.get('instrument_date')}、租期模式 "
            f"{facts.get('term_kind')}、租金口径 {result.get('rent_basis')}"
            "（按金不计入）。"
        ),
        _step(
            2,
            f"税率（Cap.117 head 1 sub-head (2)）：{tiers_desc}"
            "（周年日判定）。"
        ),
        _step(
            3,
            "取整：租金基数按「每 $100 或其部分」向上取整（ceil100，"
            "法定基础）→ 乘率 → 税额 ceil 至 1 元；premium 与租金独立"
            "课征（s.10(4)）、各自 ceil1。"
        ),
        _step(
            4,
            f"租金印花税 {breakdown.get('rent_duty')}＋premium 印花税 "
            f"{breakdown.get('premium_duty')}＋复本 "
            f"{breakdown.get('duplicate_duty')} → 合计应缴印花税 "
            f"{amounts.get('before_reduction')} 港元（一次性宽减 "
            f"{amounts.get('reduction')}）。"
        ),
    ]


_STEPS_BUILDERS = {
    "salaries_tax": _salaries_steps,
    "profits_tax": _profits_steps,
    "property_tax": _property_steps,
    "personal_assessment": _pa_steps,
    "stamp_property": _stamp_property_steps,
    "stamp_stock": _stamp_stock_steps,
    "stamp_lease": _stamp_lease_steps,
}


# ------------------------------------------------------------------ 原包重放
def recompute_for_replay(core: Mapping, store: Any, engine_version: str) -> dict:
    """C3.1 replay：按记录确认事实＋原 binding 校验后由引擎重算。

    - 引擎版本不完全相等 → 409 E_BUNDLE_INCOMPATIBLE（不降级、不迁移）；
    - 原规则束缺失/内容哈希与 binding 不符 → 同样拒绝；
    - 不读取记录保存的 amounts（不信任导出税额）。
    """
    binding = core.get("binding") or {}
    if binding.get("engine_version") != engine_version:
        raise AppError(
            "E_BUNDLE_INCOMPATIBLE",
            "该记录由不同的引擎版本产生，拒绝重算（不降级、不迁移）",
        )
    try:
        bundle = store.get_bundle(binding["bundle_id"])
    except AppError as exc:
        raise AppError(
            "E_BUNDLE_INCOMPATIBLE",
            "记录原规则束已不存在，拒绝重算（不降级、不迁移）",
        ) from exc
    if bundle_content_hash(bundle) != binding.get("bundle_hash"):
        raise AppError(
            "E_BUNDLE_INCOMPATIBLE",
            "原规则束内容与记录 binding 不符，拒绝重算（不降级、不迁移）",
        )
    return run_engine(str(core.get("tax_type")), core.get("input") or {}, bundle)


# ------------------------------------------------------------------ 打印渲染
def render_print_html(record_id: str, core: Mapping) -> str:
    """打印报告（§6.6：保留输入摘要/步骤/版本/出处/警告；无交互控件）。

    全部数值直读服务器 canonical 字段；缺组件/None 金额一律标「未能估算」，
    不以零填补、不做任何前端重建合计（§12.4）。
    """
    esc = html.escape
    status = str(core.get("status") or "")
    badge = STATUS_BADGES.get(status, status or "未知状态")
    period = str(
        core.get("period")
        or (core.get("input") or {}).get("year_of_assessment")
        or ""
    )
    binding = core.get("binding") or {}
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant"><head><meta charset="utf-8">',
        "<title>计算记录（打印版）</title></head><body>",
        f"<h1>计算记录 {esc(record_id)}</h1>",
        f'<p class="print-status-badge">{esc(badge)}</p>',
    ]
    if period:
        parts.append(f"<p>课税年度：{esc(period.replace('_', '/'))}</p>")

    # 输入摘要（§6.6）
    parts.append("<h2>输入摘要</h2><ul>")
    for key, value in (core.get("input") or {}).items():
        parts.append(f"<li>{esc(str(key))}：{esc(_plain(value))}</li>")
    parts.append("</ul>")

    # 版本（§6.6：规则/引擎版本）
    parts.append("<h2>版本</h2><ul>")
    parts.append(
        f"<li>引擎版本：{esc(str(binding.get('engine_version', '')))}</li>"
    )
    parts.append(
        "<li>规则 schema 版本："
        f"{esc(str(binding.get('rules_schema_version', '')))}</li>"
    )
    parts.append(f"<li>规则束：{esc(str(binding.get('bundle_id', '')))}</li>")
    parts.append(
        f"<li>规则束哈希：{esc(str(binding.get('bundle_hash', '')))}</li>"
    )
    parts.append("</ul>")

    # 来源（§6.6：官方出处）
    parts.append("<h2>来源</h2><ul>")
    refs = core.get("evidence_refs") or []
    if refs:
        for ref in refs:
            parts.append(f"<li>{esc(_plain(ref))}</li>")
    else:
        parts.append("<li>本记录无附加官方出处摘要（初始规则束）。</li>")
    parts.append("</ul>")

    # 计算步骤（§6.6；complete/partial 记录含已算组件的步骤）
    steps = core.get("steps") or []
    if steps:
        parts.append("<h2>计算步骤</h2><ol>")
        for step in steps:
            label = step.get("label") if isinstance(step, Mapping) else str(step)
            parts.append(f"<li>{esc(str(label))}</li>")
        parts.append("</ol>")

    # 警告与未覆盖（§6.6）
    parts.append("<h2>警告与未覆盖事项</h2><ul>")
    warnings = core.get("warnings") or []
    unsupported = core.get("unsupported") or []
    pending = core.get("pending_verification") or []
    if warnings or unsupported or pending:
        for text in warnings:
            parts.append(f"<li>警告：{esc(_plain(text))}</li>")
        for text in unsupported:
            parts.append(f"<li>未覆盖：{esc(_plain(text))}</li>")
        for text in pending:
            parts.append(f"<li>待核验：{esc(_plain(text))}</li>")
    else:
        parts.append("<li>无警告。</li>")
    parts.append("</ul>")

    # 金额分项（C3.4 直读；None → 未能估算，不重建合计）
    amounts = core.get("amounts") or {}
    parts.append("<h2>金额分项</h2><ul>")
    for key, label in AMOUNT_LABELS:
        value = amounts.get(key)
        text = "未能估算" if value is None else f"{value} 港元"
        parts.append(f"<li>{esc(label)}（{esc(key)}）：{esc(text)}</li>")
    parts.append("</ul>")

    missing = core.get("missing_components") or []
    if missing:
        joined = "、".join(str(item) for item in missing)
        parts.append(
            f"<p>未能估算：{esc(joined)}（缺组件不以往值或零填补，§12.4）</p>"
        )
    if core.get("blocked_reason"):
        parts.append(
            f"<p>暂不可计算原因：{esc(str(core['blocked_reason']))}</p>"
        )
    questions = core.get("questions") or []
    for question in questions:
        parts.append(f"<p>待确认：{esc(_plain(question))}</p>")

    disclaimer = str(core.get("disclaimer") or DISCLAIMER)
    parts.append(f"<p>免责声明：{esc(disclaimer)}</p>")
    parts.append("</body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------- 工具
def _percent(rate_bp: Any) -> str:
    """基点整数 → 百分数文本（200 bp → 2%；1500 bp → 15%）。"""
    return _ratio_percent(Fraction(int(rate_bp), 10000))


def _ratio_percent(value: Any) -> str:
    """精确比率（bp 整数或 num/den 字符串）→ 百分数文本（1/1000 → 0.1%）。"""
    fraction = value if isinstance(value, Fraction) else Fraction(str(value))
    scaled = fraction * 100
    text = format(
        Decimal(scaled.numerator) / Decimal(scaled.denominator), ".10f"
    ).rstrip("0").rstrip(".")
    return f"{text}%"


def _plain(value: Any) -> str:
    """任意 JSON 值 → 单行文本（对象/列表以 canonical JSON 呈现）。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
