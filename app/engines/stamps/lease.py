"""REQ-7 印花税 — 租约引擎（M4 Green；Annex D D3 为税法语义权威）。

REQ: REQ-7（SDD §9：普通租约按文书日期选制度；档位/取整链/premium 期间费率/
复本 head 4 已法定化（Annex D D3，T1-现行法例文本）；档位/每 $100 或其部分/
按金排除/期限周年日判定按 D3.1/D3.1a；关键租期事实缺失 → 追问）。
规格锚点:
  - SDD §6 租约行＋§9 REQ-7；Annex D D3.1（计税规则：GovHK＋Cap.117 head 1
    sub-head (2)）、D3.1a（官方租约计算器：周年日档位、≤1 年总租金、月租×12、
    总租金按日历天数年化 4 位＋ceil100→率→ceil1）、D3.2（premium 冲突法定
    闭合）、D3.3（复本 HEAD 4 法定闭合）、D3.4（租约事实字段）。
  - 档位按周年日（非 days/365；起讫两日都算）：indefinite → 0.25%×年租/
    平均年租；≤1 年（含恰 1 年）→ 0.25%×租期内总租金（不年化）；>1–≤3 年
    → 0.5%；>3 年 → 1%。恰 1 年 1 天起即「超过 1 年」，恰 3 年 1 天起即
    「超过 3 年」。
  - 取整链：基数「每 $100 或其部分」ceil100 → 乘率 → 税额 ceil1；按金不计
    （仅记录）；平均年租分支先 round4（官方计算器行为）再入链。
  - premium 与 rent 两笔独立 ceil1 后相加（s.10(4)＋s.18A），合计不再统一
    取整；含租费率：2024-04-01–2026-02-25 → 4.25%；2026-02-26 起住宅 6.5%／
    非住宅 4.25%（3 of 2026 s.14 现行 Note 1）。premium 必须显式（"0" 或
    金额；缺失 ≠ 零 → 追问）；premium 无租金 → 按文书日期适用 AVD 表：延迟
    调用物业泳道 app.engines.stamps.property.calculate_property_avd（同一
    AVD 表逻辑，避免在两处复制档表）。
  - 复本（HEAD 4）：原本税额 < $5 → 每份与原本同额；否则每份 $5。
  - 规则数值经注入 bundle.data.stamps.lease（app/rules/bundles/stamp_lease.py）；
    文书日期窗口取 bundle.applicability.stamp_duty_instrument_window，超窗
    → E_PERIOD_NOT_SUPPORTED。
期望值来源: 档位费率/ceil100/ceil1/按金排除/复本 head 4＝T1（Cap.117 head 1
  现行文本＋GovHK；台账 1.14）；档位周年日判定与 ≤1 年总租金/月租×12/平均
  年租年化＝T1-observed（官方租约计算器，台账 1.14）；premium 期间费率＝T1
  （3 of 2026 s.14 Note 1）；端点期望值＝T3 独立手算（可审计复算）。
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from app.core.errors import AppError
from app.core.money import (
    ceil_at_point,
    floor_at_point,
    format_canonical,
    parse_amount,
    parse_ratio,
)

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)
_BREAKDOWN_KEYS = (
    "rounded_rent_base",
    "rent_duty",
    "premium_duty",
    "duplicate_duty",
    "total",
)
_RENT_MODES = frozenset({"fixed_monthly", "total_rent", "annual_rent"})
_TERM_KINDS = frozenset({"indefinite", "fixed"})
_ONE_DAY = timedelta(days=1)
# 官方计算器非固定分支 leaseTermYears = -0.5（D3.1a；恒落 0.25% 档）
_INDEFINITE_TERM_YEARS = Fraction(-1, 2)


def calculate_lease_stamp_duty(
    facts: Mapping[str, Any], bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """普通租约印花税：租金档位税＋premium 税＋复本（HEAD 4）；纯函数。

    返回信封：status ∈ {complete, needs_input}；amounts 恰含 C3.4 六键
    （合计＝before_reduction＝final_after_reduction、reduction="0"、三个暂缴
    组件恒 None）；另带 duty_breakdown 五项分项与 rent_basis/deposit 记录。
    """
    rules = bundle["data"]["stamps"]["lease"]
    instrument_date = _require_instrument_date(facts, bundle)
    questions: list[str] = []

    term_kind = facts.get("term_kind")
    if term_kind is None:
        questions.append(
            "缺少 term_kind（indefinite＝未界定/不确定期限；fixed＝固定期限）；"
            "关键租期事实缺失不得默认低税档。"
        )
    elif term_kind not in _TERM_KINDS:
        raise AppError(
            "E_INPUT_TYPE", "term_kind 须为 indefinite 或 fixed", field="term_kind"
        )

    deposit_text = _optional_deposit(facts)

    rent: dict[str, Any] | None = None
    if term_kind == "indefinite":
        rent = _resolve_indefinite(facts, questions)
    elif term_kind == "fixed":
        rent = _resolve_fixed(facts, questions)

    premium: Fraction | None = None
    if facts.get("premium") is None:
        questions.append(
            "缺少 premium（非租金代价）：无 premium 请显式填 \"0\"；"
            "缺失不等于零，不得静默按零计税。"
        )
    else:
        premium = _amount(facts["premium"], "premium")

    property_class: str | None = None
    if premium is not None and premium > 0:
        property_class = _require_property_class(facts, rules, questions)

    if questions:
        return _needs_input(questions, deposit_text)
    if rent is None:  # 防御性：各 None 返回分支均已登记中文追问
        return _needs_input(["租约租金事实未解析，请补充。"], deposit_text)

    rate = _rent_rate(rules, rent["term_years"])
    base = _rent_base(rent["amount"], rules)
    rent_duty = ceil_at_point(Fraction(base) * rate)

    premium_duty = 0
    if premium is not None and premium > 0:
        if rent["amount"] > 0:
            # 含租：Note 1 期间费率（历史 4.25%；2026-02-26 起住宅 6.5%/非住宅 4.25%）
            premium_duty = ceil_at_point(
                premium
                * _premium_with_rent_rate(rules, instrument_date, property_class)
            )
        else:
            # 无租：按文书日期适用 AVD 表（延迟调用物业泳道同表逻辑）
            premium_duty, avd_questions = _premium_without_rent_duty(
                bundle, instrument_date, premium, property_class
            )
            if avd_questions:
                return _needs_input(avd_questions, deposit_text)

    duplicate_duty = _duplicate_duty(facts, rules, rent_duty + premium_duty)
    total_text = _fraction_text(Fraction(rent_duty + premium_duty + duplicate_duty))
    return {
        "status": "complete",
        "amounts": {
            "before_reduction": total_text,
            "reduction": "0",
            "final_after_reduction": total_text,
            "provisional_paid": None,
            "next_provisional": None,
            "balance": None,
        },
        "questions": [],
        "duty_breakdown": {
            "rounded_rent_base": _fraction_text(Fraction(base)),
            "rent_duty": _fraction_text(Fraction(rent_duty)),
            "premium_duty": _fraction_text(Fraction(premium_duty)),
            "duplicate_duty": _fraction_text(Fraction(duplicate_duty)),
            "total": total_text,
        },
        "deposit": deposit_text,
        "rent_basis": rent["basis"],
    }


def _require_instrument_date(
    facts: Mapping[str, Any], bundle: Mapping[str, Any]
) -> date:
    """文书日期必须存在且落在 bundle 印花税文书窗口内（超窗 → 拒算）。"""
    raw = facts.get("instrument_date")
    if raw is None:
        raise AppError(
            "E_INPUT_MISSING", "缺少 instrument_date（文书日期）", field="instrument_date"
        )
    when = _parse_date(raw, "instrument_date")
    window = bundle["applicability"]["stamp_duty_instrument_window"]
    window_from = _parse_date(window["from"], "stamp_duty_instrument_window.from")
    window_to = _parse_date(window["to"], "stamp_duty_instrument_window.to")
    if when < window_from or when > window_to:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"文书日期 {when.isoformat()} 超出印花税文书支持窗口 "
            f"{window_from.isoformat()}–{window_to.isoformat()}",
            field="instrument_date",
        )
    return when


def _parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise AppError("E_INPUT_TYPE", f"{field} 须为 ISO 日期字符串", field=field)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 日期语法非法（须 ISO 8601）", field=field
        ) from exc


def _resolve_indefinite(
    facts: Mapping[str, Any], questions: list[str]
) -> dict[str, Any] | None:
    """indefinite → annual_rent（年租或平均年租）；不得强制 total_rent（D3.4）。"""
    mode = facts.get("rent_input_mode")
    if mode is None:
        questions.append(
            "缺少 rent_input_mode：term_kind=indefinite 须以 annual_rent "
            "提供年租（或平均年租）。"
        )
        return None
    if mode not in _RENT_MODES:
        raise AppError(
            "E_INPUT_TYPE",
            "rent_input_mode 须为 fixed_monthly/total_rent/annual_rent",
            field="rent_input_mode",
        )
    if mode != "annual_rent":
        questions.append(
            "term_kind=indefinite：请以 rent_input_mode=annual_rent 提供年租/"
            "平均年租（不得强制 total_rent，也不得按月租年化）。"
        )
        return None
    amount = _required_amount(facts, "annual_rent", questions)
    if amount is None:
        return None
    return {"term_years": _INDEFINITE_TERM_YEARS, "basis": "annual", "amount": amount}


def _resolve_fixed(
    facts: Mapping[str, Any], questions: list[str]
) -> dict[str, Any] | None:
    """fixed 租期：≤1 年/分段/免租 → total_rent；>1 年月租×12 或总租金年化。"""
    raw_start = facts.get("term_start")
    raw_end = facts.get("term_end")
    start = _parse_date(raw_start, "term_start") if raw_start is not None else None
    end = _parse_date(raw_end, "term_end") if raw_end is not None else None
    if start is None:
        questions.append("fixed 租期缺少 term_start（起租日；起讫两日都算）。")
    if end is None:
        questions.append("fixed 租期缺少 term_end（终止日；免租期计入租期）。")
    if start is None or end is None:
        return None
    if end < start:
        raise AppError("E_INPUT_CONTRADICTORY", "term_end 早于 term_start", field="term_end")
    term_years = _fixed_term_years(start, end)

    schedule = facts.get("rent_schedule")
    if _fact_present(schedule):
        # 分段租金即走总租金分支（D3.4；>1 年再按日历天数年化）
        total = _schedule_total(schedule)
        if term_years <= 1:
            return {"term_years": term_years, "basis": "total", "amount": total}
        return {
            "term_years": term_years,
            "basis": "average",
            "amount": _average_annual_rent(total, start, end),
        }

    mode = facts.get("rent_input_mode")
    if mode is None:
        questions.append("缺少 rent_input_mode（fixed_monthly/total_rent/annual_rent）。")
        return None
    if mode not in _RENT_MODES:
        raise AppError(
            "E_INPUT_TYPE",
            "rent_input_mode 须为 fixed_monthly/total_rent/annual_rent",
            field="rent_input_mode",
        )

    if term_years <= 1:
        if mode != "total_rent":
            questions.append(
                "租期不超过 1 年（含恰 1 年）：必须以 rent_input_mode=total_rent "
                "提供租期内总租金（总租金直接作基数、不年化）。"
            )
            return None
        amount = _required_amount(facts, "total_rent", questions)
        if amount is None:
            return None
        return {"term_years": term_years, "basis": "total", "amount": amount}

    if mode == "fixed_monthly":
        # 仅限 >1 年、全期固定月租、无免租期（D3.4）
        if _fact_present(facts.get("rent_free_period")):
            questions.append(
                "含免租期：必须以 total_rent 提供租期内总租金（D3.4），"
                "不得按月租×12。"
            )
            return None
        monthly = _required_amount(facts, "monthly_rent", questions)
        if monthly is None:
            return None
        return {"term_years": term_years, "basis": "annual", "amount": monthly * 12}

    if mode == "total_rent":
        total = _required_amount(facts, "total_rent", questions)
        if total is None:
            return None
        return {
            "term_years": term_years,
            "basis": "average",
            "amount": _average_annual_rent(total, start, end),
        }

    amount = _required_amount(facts, "annual_rent", questions)
    if amount is None:
        return None
    return {"term_years": term_years, "basis": "annual", "amount": amount}


def _fixed_term_years(start: date, end: date) -> Fraction:
    """周年日判定档位（D3.1a）：恰 N 年 → N；超过 N 年 → N+0.5（非 days/365）。"""
    n = end.year - start.year + 1
    while n >= 0:
        boundary = _add_years(start, n) - _ONE_DAY
        if end == boundary:
            return Fraction(n)
        if end > boundary:
            return Fraction(n) + Fraction(1, 2)
        n -= 1
    raise AppError("E_INPUT_CONTRADICTORY", "term_end 早于 term_start", field="term_end")


def _add_years(day: date, years: int) -> date:
    """起始日 + N 年；含 2/29 时周年界以 3/1 计（D3.1a 闰日规则）。"""
    year = day.year + years
    if day.month == 2 and day.day == 29 and not calendar.isleap(year):
        return date(year, 3, 1)
    return date(year, day.month, day.day)


def _whole_years_and_tail(start: date, end: date) -> tuple[int, int]:
    """整年数 N 与尾部天数（N＝完整 N 年以「起始日+N 年−1 天」为界）。"""
    n = 0
    while _add_years(start, n + 1) - _ONE_DAY <= end:
        n += 1
    boundary = _add_years(start, n) - _ONE_DAY
    return n, (end - boundary).days


def _average_annual_rent(total: Fraction, start: date, end: date) -> Fraction:
    """总租金分支年化（D3.1a）：total×尾年天数/(N×尾年天数+尾部天数)，round4。"""
    years, tail_days = _whole_years_and_tail(start, end)
    tail_year_days = 366 if calendar.isleap(end.year) else 365
    annual = total * tail_year_days / (years * tail_year_days + tail_days)
    return _round4(annual)


def _round4(value: Fraction) -> Fraction:
    """官方计算器 Math.round(avgRent×10000)/10000（半值向上，全程精确有理）。"""
    return Fraction(floor_at_point(value * 10000 + Fraction(1, 2)), 10000)


def _rent_base(value: Fraction, rules: Mapping[str, Any]) -> int:
    """「每 $100 或其部分」：基数向上取整至 bundle 单位（D3.1 法定基础）。"""
    unit = int(parse_amount(rules["rent_ceiling_unit"]))
    return ceil_at_point(value / unit) * unit


def _rent_rate(rules: Mapping[str, Any], term_years: Fraction) -> Fraction:
    """自最高档向下扫描：term_years > min_years 即取该档（D3.1a）。"""
    tiers = sorted(rules["rent_rate_tiers"], key=lambda tier: tier["min_years"])
    for tier in reversed(tiers):
        if term_years > Fraction(tier["min_years"]):
            return parse_ratio(tier["rate"])
    raise AppError("E_BUNDLE_INCOMPATIBLE", "租约档位表未覆盖该租期")


def _premium_with_rent_rate(
    rules: Mapping[str, Any], instrument_date: date, property_class: str | None
) -> Fraction:
    """含租 premium 费率（D3.2）：历史 4.25%；2026-02-26 起住宅 6.5%/非住宅 4.25%。"""
    premium_rules = rules["premium"]
    regime_from = date.fromisoformat(premium_rules["current_regime_from"])
    if instrument_date < regime_from:
        return parse_ratio(premium_rules["with_rent_rate_historical"])
    if property_class == "residential":
        return parse_ratio(premium_rules["with_rent_rate_residential"])
    return parse_ratio(premium_rules["with_rent_rate_nonresidential"])


def _premium_without_rent_duty(
    bundle: Mapping[str, Any],
    instrument_date: date,
    premium: Fraction,
    property_class: str | None,
) -> tuple[int, list[str]]:
    """无租 premium：按文书日期适用 AVD 表（D3.2）——物业泳道同表逻辑。

    延迟导入 app.engines.stamps.property（由物业泳道维护），以 premium 作为
    conveyance on sale 的 consideration/value 计价；其 needs_input 直接透传。
    """
    from app.engines.stamps.property import calculate_property_avd

    premium_text = _fraction_text(premium)
    conveyance: dict[str, Any] = {
        "instrument_date": instrument_date.isoformat(),
        "instrument_kind": "conveyance_on_sale",
        "consideration": premium_text,
        "value": premium_text,
    }
    if property_class is not None:
        conveyance["property_class"] = property_class
    result = calculate_property_avd(conveyance, bundle)
    if result.get("status") != "complete":
        avd_questions = result.get("questions")
        if not isinstance(avd_questions, list) or not avd_questions:
            avd_questions = ["premium 无租金：按 AVD 表计税所需物业事实不足，请补充。"]
        return 0, [str(question) for question in avd_questions]
    return int(result["instrument_detail"]["duty"]), []


def _require_property_class(
    facts: Mapping[str, Any], rules: Mapping[str, Any], questions: list[str]
) -> str | None:
    """premium>0 时 property_class 必需（住宅 6.5%／非住宅 4.25% 分派）。"""
    value = facts.get("property_class")
    if value is None:
        questions.append(
            "premium 计税需要 property_class（residential／nonresidential）。"
        )
        return None
    classes = rules["premium"]["property_classes"]
    if value not in classes:
        raise AppError(
            "E_INPUT_TYPE",
            "property_class 须为 residential 或 nonresidential",
            field="property_class",
        )
    return str(value)


def _duplicate_duty(
    facts: Mapping[str, Any], rules: Mapping[str, Any], original_duty: int
) -> int:
    """复本（HEAD 4）：原本税额 < $5 → 每份与原本同额；否则每份 $5。"""
    copies = facts.get("copies")
    if copies is None:
        return 0
    if isinstance(copies, bool) or not isinstance(copies, int) or copies < 0:
        raise AppError(
            "E_INPUT_TYPE", "copies 须为非负整数（复本份数）", field="copies"
        )
    if copies == 0:
        return 0
    per_copy = int(parse_amount(rules["duplicate"]["per_copy"]))
    low_below = int(parse_amount(rules["duplicate"]["low_original_below"]))
    unit = original_duty if original_duty < low_below else per_copy
    return copies * unit


def _schedule_total(schedule: object) -> Fraction:
    """分段租金合计（每段 amount 精确有理和）；分段即走总租金分支。"""
    if not isinstance(schedule, list):
        raise AppError("E_INPUT_TYPE", "rent_schedule 须为分段数组", field="rent_schedule")
    total = Fraction(0)
    for index, item in enumerate(schedule):
        field = f"rent_schedule[{index}]"
        if not isinstance(item, Mapping):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        if item.get("amount") is None:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 amount", field=f"{field}.amount")
        total += _amount(item["amount"], f"{field}.amount")
    return total


def _fact_present(value: object) -> bool:
    """事实是否显式提供（None/False/零/空串/空容器视为未提供）。"""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no")
    if isinstance(value, (int, float, Decimal, Fraction)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, (list, tuple, dict, set)) and not value:
        return False
    return True


def _optional_deposit(facts: Mapping[str, Any]) -> str | None:
    """按金仅记录、不计入计税租金（D3.1）。"""
    value = facts.get("deposit")
    if value is None:
        return None
    try:
        return parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"deposit：{exc.message_zh}", field="deposit") from exc


def _required_amount(
    container: Mapping[str, Any], key: str, questions: list[str]
) -> Fraction | None:
    if container.get(key) is None:
        questions.append(f"缺少 {key} 金额；请按 rent_input_mode 提供。")
        return None
    return _amount(container[key], key)


def _amount(value: object, field: str) -> Fraction:
    """C2.1 金额 → 精确有理（金额链路零浮点）。"""
    try:
        text = parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    return Fraction(Decimal(text))


def _needs_input(questions: list[str], deposit_text: str | None) -> dict[str, Any]:
    """needs_input 信封：amounts 六键一律显式 None（不得默认低税档）。"""
    return {
        "status": "needs_input",
        "amounts": {key: None for key in _AMOUNT_KEYS},
        "questions": questions,
        "duty_breakdown": {key: None for key in _BREAKDOWN_KEYS},
        "deposit": deposit_text,
        "rent_basis": None,
    }


def _fraction_text(value: Fraction) -> str:
    """canonical 字符串（金额分母均为 10 的幂或整数，Decimal 除法精确）。"""
    return format_canonical(Decimal(value.numerator) / Decimal(value.denominator))
