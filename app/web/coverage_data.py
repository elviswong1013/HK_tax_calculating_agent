"""REQ-1 覆盖盘点数据源（PRD §3.3 盘点＋§3.1 支持矩阵；数据驱动）。

S002 P2（005:927–930/002 §1.3）：五条普通类别由 `unimplemented` 迁移为
`support_level ∈ {computable, needs_input, manual_handoff}`＋`phase`（1/2/3）＋
`limits_zh`（非空中文边界）。阶段 2/3 项目一律 `manual_handoff` 并显式写明
「另行需求与规格审批／当前不支持计算」；本阶段尚未实现的范围不得称已支持。
`computable` 项携带指向既有计算路由的 `calc_entry`；非 `computable` 项无入口。

`chat_coverage_guidance(items)` 为同文件纯内部 helper（005:927/:930）：只读取
`support_level/phase/limits_zh/tax/subtopic/applicable_periods` 白名单键，输出
确定性的中文本地指引；不读取用户 facts/消息/模型文本，不做 I/O。API／覆盖页／
首页聊天面板共用同一 `coverage_items()` 数据源。
"""

from __future__ import annotations

_DIRECT_PERIODS = ["2024/25", "2025/26", "2026/27"]
_STAMP_PERIODS = ["2024-04-01..2026-10-07"]

# 005:930：本地指引的确定性标签（阶段/支持等级/PA 限定；不做启发式 NLP）。
_STAGE_BOUNDARY_ZH = "后续阶段，当前不支持计算"
_UNKNOWN_LABEL_ZH = "支持范围尚未确认；不作可计算承诺"
_UNKNOWN_LINK_ZH = "覆盖详情：/coverage"
_UNSUPPORTED_ZH = "当前不支持计算"
_KNOWN_LEVELS = frozenset({"computable", "needs_input", "manual_handoff"})


def _guidance_labels(item: dict) -> list[str]:
    """单条覆盖项的确定性中文指引（只读白名单键；无 I/O）。

    005:930 有限映射＋保守 fallback：
    - phase1/ computable → 列明范围可计算，但仍须规则可用、资料及资格确认；
    - needs_input → 缺资料待确认，未知不是否或零；
    - manual_handoff → 需人工核实／本阶段范围外（limits_zh 随行输出）；
    - unimplemented → 当前不支持计算；
    - phase2/3 → 后续阶段，当前不支持计算（不生成计算入口）；
    - 未识别/缺失条目 → 支持范围尚未确认＋/coverage 链接。
    """
    level = item.get("support_level")
    phase = item.get("phase")
    subtopic = item.get("subtopic")
    tax = item.get("tax")
    try:
        phase_value = int(phase)
    except (TypeError, ValueError):
        phase_value = None
    if isinstance(phase, bool) or phase_value not in (1, 2, 3):
        phase_value = None
    if (
        not isinstance(tax, str)
        or not tax.strip()
        or (
            level not in _KNOWN_LEVELS
            and level != "unimplemented"
        )
        or (phase_value is None and level != "unimplemented")
    ):
        return [_UNKNOWN_LABEL_ZH, _UNKNOWN_LINK_ZH]
    labels: list[str] = []
    if level == "unimplemented":
        labels.append(_UNSUPPORTED_ZH)
    if phase_value == 1 and level == "computable":
        labels.append("可计算：列明范围可计算；仍须规则可用、资料及资格确认")
    if level == "needs_input":
        labels.append("缺资料待确认：未知不是否或零")
    if level == "manual_handoff":
        labels.append("需人工核实／本阶段范围外")
    if phase_value is not None and phase_value >= 2:
        labels.append(_STAGE_BOUNDARY_ZH)
    if tax == "个人入息课税" or "评税选择" in str(subtopic or ""):
        labels.extend(
            [
                "评税选择：各评税方式并列比较",
                "本阶段仅 API（网页不提供计算工作台）",
                "有限方案、部分可算，不代表完整最优",
            ]
        )
    return labels


def coverage_items() -> list[dict]:
    """返回覆盖清单条目（REQ-1 规范八字段＋phase/limits_zh/calc_entry）。"""
    return [
        {
            "tax": "薪俸税",
            "subtopic": "应课税入息、MPF 强制性供款扣除、免税额、两径择低与年度宽减",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": (
                "本阶段按已实现范围可计算受雇入息、MPF 强制性供款、Part 5 "
                "免税额与两径择低；在册扣除项、居所贷款等属阶段 2，另行需求与"
                "规格审批；阶段 3 的跨年度比较当前不支持计算。"
            ),
            "calc_entry": "/salaries",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/tax/budget.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": (
                "阶段 2 扣除项（Part 4A 在册扣除）与阶段 3 跨年度比较未实现；"
                "官方一手数值未晋升 T1 者不进入规则数据"
            ),
        },
        {
            "tax": "利得税",
            "subtopic": "法团／非法团两级制、合伙业务份额（资格须显式确认）",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": (
                "本阶段按已实现范围可计算两级制税额；两级资格事实须显式确认，"
                "缺失不默认；进一步的分摊与跨境事宜属阶段 2，另行需求与规格"
                "审批，当前不支持计算。"
            ),
            "calc_entry": "/profits",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/tax/bus_pft.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "阶段 2 分摊／跨境事宜未实现；不分摊表由确认事实直接代入",
        },
        {
            "tax": "物业税",
            "subtopic": "NAV 口径与 15% 标准税率",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": (
                "本阶段按已实现范围可计算逐项物业 NAV 与标准税率；居所贷款"
                "利息扣除属阶段 2，另行需求与规格审批，当前不支持计算。"
            ),
            "calc_entry": "/property",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "阶段 2 居所贷款利息扣除未实现",
        },
        {
            "tax": "个人入息课税",
            "subtopic": "评税选择（personal assessment）：各评税方式并列比较，非第四个独立税种",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": (
                "评税选择：本阶段仅纠正既有 PA/no-PA 方案的免税额与两径择低；"
                "网页仍为 API-only（仅接口，不提供计算工作台）；有限方案、"
                "部分可算，不代表最优；完整缴款比较属后续阶段，另行需求与"
                "规格审批，当前不支持计算。"
            ),
            "calc_entry": "/personal-assessment",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "verified_date": "",
            "applicable_periods": list(_DIRECT_PERIODS),
            "gaps": "阶段 3 的 PA 免税额取得与缴款比较未实现；本阶段仅 API",
        },
        {
            "tax": "印花税",
            "subtopic": "住宅／非住宅 AVD、香港股票普通转让、普通租约",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": (
                "本阶段按已实现范围可计算住宅／非住宅 AVD、股票普通转让与"
                "普通租约的应缴印花税；其他印花税算法与可扣／比较属阶段 2/3，"
                "另行需求与规格审批，当前不支持计算。"
            ),
            "calc_entry": "/stamps/property",
            "authority": "香港税务局（IRD）",
            "official_source": "https://www.gov.hk/en/residents/taxes/stamp/stamp_duty_rates.htm",
            "verified_date": "",
            "applicable_periods": list(_STAMP_PERIODS),
            "gaps": "阶段 2/3 其他印花税算法与可扣／比较未实现；文书日期初始窗 2024-04-01 至 2026-10-07",
        },
    ]


def chat_coverage_guidance(items: list[dict]) -> dict:
    """005:927/:930：聊天本地覆盖指引（纯函数；只读有限白名单键）。

    输入额外携带 facts/消息/模型文本等键不会改变输出（不读取、不泄漏）；
    输出仅含税项/子项/支持等级/阶段/边界/适用期间＋确定性标签。
    """
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        periods = item.get("applicable_periods")
        rows.append(
            {
                "tax": item.get("tax"),
                "subtopic": item.get("subtopic"),
                "support_level": item.get("support_level"),
                "phase": item.get("phase"),
                "limits_zh": item.get("limits_zh"),
                "applicable_periods": list(periods or []),
                "labels_zh": _guidance_labels(item),
            }
        )
    return {
        "items": rows,
        "note_zh": "本地确定性支持范围说明（非模型回答；不读取用户资料）。",
    }


__all__ = ["coverage_items", "chat_coverage_guidance"]
