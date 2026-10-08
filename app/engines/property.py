"""REQ-5 物业税引擎（M2 Green；Annex D D5 为税法语义权威）。

REQ: REQ-5（SDD §9：NAV 口径（s.5(1A)：业主同意且已缴差饷可扣、20% 按
（AV−差饷）计、不可追回租金 s.7C 含超额部分以前年度回扣、按金抵销限不可追回、
不得扣地租/修葺/保险/利息）；不套用他税宽减；详见 Annex D D5）。
规格锚点:
  - SDD §6 物业税行＋§9 REQ-5；Annex D D5.0（s.5/s.5B/s.7C＋BIR57 Note 3）。
  - NAV＝（租金收入−不可追回租金−业主同意且已缴差饷）×80%（s.5(1A) 括号：
    先扣坏账/差饷，再计 20% 法定修葺及支出免税额）。
  - 评税单位＝逐项物业（BIR57 Note 1(b)＋pty.htm Q4）：逐物业
    floor(NAV)→floor(NAV×15%)；聚合顺序不恒等（两物业 AV 各 17 → 逐物业
    合计 2 vs 聚合一次取整 4），不得按输入聚合字段一次取整。
  - s.7C(3)：当年应评税值不足扣全部不可追回代价 → 未扣部分自最近一个租金
    收入足够的以前课税年度回扣（修订评税/退税；无未来结转）；目标以前年度
    事实缺失 → needs_input（不得改向未来年度或静默丢弃）。
  - 按金抵销仅限不可追回部分（Q27/BIR57 Note 3(b)(i)，同一欠租只扣一次）；
    先前已扣坏账追回 → 追回年度计回收入（s.7C(2)/Q27）。
  - 附表 43 无物业税行 → reduction 恒 "0"（不套用他税宽减）。
  - 不支持扣除（地租/修葺/保险/按揭利息）正数申索 →
    E_DEDUCTION_UNSUPPORTED（REQ-10：拒算受影响税项；显式 "0" 合法无效果）。
  - 规则数值经注入 bundle.data.property（app/rules/bundles/property_.py）。
期望值来源: Q7/Q26/Q27/BIR57 Note 3＝T1 官方；PAM54 Q1＝T2 台账值；
  s.7C(3) 回扣例＝GovHK irrecoverable.htm Example 2（T1 佐证）。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from typing import Any

from app.core.errors import AppError
from app.core.money import floor_at_point, format_canonical, parse_amount, parse_ratio

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


def calculate_property_tax(
    facts: Mapping[str, Any], bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """逐项物业物业税：NAV（s.5(1A)）→ 逐物业 floor 链 → 税额/暂缴；纯函数。"""
    rules = bundle["data"]["property"]
    year = facts.get("year_of_assessment")
    if not isinstance(year, str):
        raise AppError("E_INPUT_MISSING", "缺少 year_of_assessment", field="year_of_assessment")
    year_config = rules.get("years", {}).get(year)
    if year_config is None:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"年度 {year!r} 不在物业税支持范围（2024/25–2026/27）",
            field="year_of_assessment",
        )
    rate = parse_ratio(year_config["standard_rate"])
    allowance = parse_ratio(rules["repair_allowance_ratio"])
    reduction_text = parse_amount(year_config["reduction"])
    reduction = Fraction(Decimal(reduction_text))

    raw_properties = facts.get("properties")
    if not isinstance(raw_properties, list) or not raw_properties:
        raise AppError(
            "E_INPUT_MISSING",
            "缺少 properties[]（评税单位＝逐项物业，BIR57 Note 1(b)）",
            field="properties",
        )

    questions: list[str] = []
    entries: list[tuple[str, int | None, int | None]] = []
    total = 0
    unresolved = False
    revision: dict[str, str] | None = None

    for prop in raw_properties:
        if not isinstance(prop, Mapping):
            raise AppError("E_INPUT_TYPE", "properties[] 元素须为对象", field="properties")
        pid = prop.get("property_id")
        if not isinstance(pid, str) or not pid:
            raise AppError("E_INPUT_MISSING", "物业缺少 property_id", field="properties")
        _reject_unsupported_deductions(prop, pid)
        where = f"properties[{pid}]"
        rent = _required_amount(prop, "rent_received", where)
        irrecoverable = _amount(
            prop.get("irrecoverable_rent", "0"), f"{where}.irrecoverable_rent"
        )
        deposits = _amount(prop.get("deposit_offsets", "0"), f"{where}.deposit_offsets")
        recovered = _amount(
            prop.get("recovered_previously_deducted_rent", "0"),
            f"{where}.recovered_previously_deducted_rent",
        )

        rates = _resolve_rates(prop, pid, questions)
        if rates is None:  # 差饷两条件三态未定 → 追问（不得默认可扣/不可扣）
            entries.append((pid, None, None))
            unresolved = True
            continue

        av = rent + recovered  # s.7C(2)：追回租金计入追回年度收入（AV）
        net_irrecoverable = max(Fraction(0), irrecoverable - deposits)
        excess = max(Fraction(0), net_irrecoverable - av)
        nav, tax = _tax_from_av(av, net_irrecoverable, rates, rate, allowance)
        if excess > 0:  # s.7C(3)：超额部分自以前课税年度回扣
            found = _preceding_revision(facts, pid, excess, year, rate, allowance, questions)
            if found is None:
                entries.append((pid, None, None))
                unresolved = True
                continue
            if revision is None:
                revision = found
        entries.append((pid, nav, tax))
        total += tax

    properties_detail = [
        {
            "property_id": pid,
            "nav": None if unresolved else format_canonical(Decimal(nav)),
            "tax": None if unresolved else format_canonical(Decimal(tax)),
        }
        for pid, nav, tax in entries
    ]

    if unresolved or questions:
        return {
            "status": "needs_input",
            "amounts": {key: None for key in _AMOUNT_KEYS},
            "questions": questions,
            "properties": properties_detail,
            "warnings": [],
            "unsupported": [],
            "pending_verification": [],
            "missing_components": [],
        }

    final = max(Fraction(0), Fraction(total) - reduction)
    # C3.5 物业税事实族无 provisional_paid（已缴暂缴）事实——provisional_paid/
    # balance 无事实来源、恒 null（不得以零填补，C3.4）；可算组件齐备即 complete。
    result: dict[str, Any] = {
        "status": "complete",
        "amounts": {
            "before_reduction": format_canonical(Decimal(total)),
            "reduction": reduction_text,
            "final_after_reduction": _fraction_text(final),
            "provisional_paid": None,
            "next_provisional": format_canonical(Decimal(total)),
            "balance": None,
        },
        "questions": [],
        "properties": properties_detail,
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }
    if revision is not None:
        result["preceding_year_revision"] = revision
    return result


def _tax_from_av(
    av: Fraction,
    net_irrecoverable: Fraction,
    rates: Fraction,
    rate: Fraction,
    allowance: Fraction,
) -> tuple[int, int]:
    """逐物业 floor 链（D5.0 冻结）：floor(NAV)→floor(NAV×15%)。"""
    chargeable = max(Fraction(0), av - net_irrecoverable - rates)
    nav = floor_at_point(chargeable * (1 - allowance))
    return nav, floor_at_point(nav * rate)


def _preceding_revision(
    facts: Mapping[str, Any],
    pid: str,
    excess: Fraction,
    year: str,
    rate: Fraction,
    allowance: Fraction,
    questions: list[str],
) -> dict[str, str] | None:
    """s.7C(3)：超额坏账自最近一个租金收入足够的以前课税年度回扣。

    缺足够目标年度事实 → 追加追问并返回 None（不得改向未来或静默丢弃）。
    """
    raw_list = facts.get("preceding_year_property_facts")
    candidates: list[Mapping[str, Any]] = []
    if isinstance(raw_list, list):
        for entry in raw_list:
            if not isinstance(entry, Mapping):
                continue
            entry_year = entry.get("year_of_assessment")
            if entry.get("property_id") != pid or not isinstance(entry_year, str):
                continue
            if entry_year >= year:
                continue  # 只允许以前课税年度
            candidates.append(entry)

    for entry in sorted(candidates, key=lambda e: e["year_of_assessment"], reverse=True):
        entry_year = str(entry["year_of_assessment"])
        where = f"preceding_year_property_facts[{entry_year}]"
        rent = _required_amount(entry, "rent_received", where)
        irrecoverable = _amount(
            entry.get("irrecoverable_rent", "0"), f"{where}.irrecoverable_rent"
        )
        deposits = _amount(entry.get("deposit_offsets", "0"), f"{where}.deposit_offsets")
        rates = _resolve_rates(entry, f"{pid}（以前年度 {entry_year}）", questions)
        if rates is None:
            continue
        net_irrecoverable = max(Fraction(0), irrecoverable - deposits)
        if rent - net_irrecoverable < excess:
            continue  # 租金收入不足以吸收超额 → 试更早年度
        _, original_tax = _tax_from_av(rent, net_irrecoverable, rates, rate, allowance)
        _, revised_tax = _tax_from_av(
            rent - excess, net_irrecoverable, rates, rate, allowance
        )
        return {
            "year_of_assessment": entry_year,
            "original_tax": format_canonical(Decimal(original_tax)),
            "revised_tax": format_canonical(Decimal(revised_tax)),
            "refund": format_canonical(Decimal(original_tax - revised_tax)),
        }

    questions.append(
        f"物业 {pid}：{year} 年度不可追回租金超出当年应评税值，超额 "
        f"{_fraction_text(excess)} 须自最近一个租金收入足够的以前课税年度回扣"
        f"（s.7C(3)）；请提供目标以前年度的应评税值/差饷/已评税额事实。"
    )
    return None


def _resolve_rates(
    container: Mapping[str, Any], pid: str, questions: list[str]
) -> Fraction | None:
    """s.5(1A)(b)(i)：差饷扣除须业主同意缴付且已缴付（两条件）方可扣除。

    正数申报而任一条件 unknown → 追加追问并返回 None（不得默认）。
    """
    raw = container.get("rates_paid_by_owner")
    if raw is None:
        return Fraction(0)
    if not isinstance(raw, Mapping):
        raise AppError(
            "E_INPUT_TYPE",
            "rates_paid_by_owner 须为对象 {amount, agreed{value}, actually_paid{value}}",
            field="rates_paid_by_owner",
        )
    amount_text = parse_amount(raw.get("amount", "0"))
    amount = Fraction(Decimal(amount_text))
    agreed = _tri_state(raw.get("agreed"), "rates_paid_by_owner.agreed")
    paid = _tri_state(raw.get("actually_paid"), "rates_paid_by_owner.actually_paid")
    if amount > 0 and (agreed == "unknown" or paid == "unknown"):
        questions.append(
            f"物业 {pid}：申报差饷 {amount_text}（正数），请确认业主是否同意缴付"
            f"（s.5(1A)(b)(i)）以及差饷是否已实际缴付——两条件未确认前不得扣除。"
        )
        return None
    if agreed is True and paid is True:
        return amount
    return Fraction(0)


def _tri_state(raw: object, field: str) -> bool | str:
    """C3.5 三态事实：true/false/"unknown"；缺省（None）视为 unknown。"""
    value = raw.get("value") if isinstance(raw, Mapping) else raw
    if value is True or value is False:
        return value
    if value is None or value == "unknown":
        return "unknown"
    raise AppError(
        "E_INPUT_TYPE", f"{field} 三态值须为 true/false/\"unknown\"", field=field
    )


def _reject_unsupported_deductions(prop: Mapping[str, Any], pid: str) -> None:
    """地租/修葺/保险/按揭利息不得在物业税下扣除（REQ-5；Q26/Q27/PAM54）。

    显式 "0"（或零值）合法无效果；正数申索 → E_DEDUCTION_UNSUPPORTED（拒算）。
    """
    direct = prop.get("mortgage_interest")
    if direct is not None:
        field = f"properties[{pid}].mortgage_interest"
        interest = _amount(direct, field)
        if interest > 0:
            raise AppError(
                "E_DEDUCTION_UNSUPPORTED",
                f"物业 {pid}：按揭利息不得在物业税下扣除（仅可在个人入息课税下"
                f"按物业 NAV 设限扣除，PAM55）",
                field=field,
            )
    items = prop.get("deduction_items")
    if items is None:
        return
    if not isinstance(items, list):
        raise AppError(
            "E_INPUT_TYPE",
            f"物业 {pid}：deduction_items 须为数组",
            field=f"properties[{pid}].deduction_items",
        )
    for index, item in enumerate(items):
        field = f"properties[{pid}].deduction_items[{index}]"
        if not isinstance(item, Mapping):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        if "amount" not in item:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 amount", field=field)
        amount = _amount(item["amount"], f"{field}.amount")
        if amount > 0:
            raise AppError(
                "E_DEDUCTION_UNSUPPORTED",
                f"物业税不得扣除 {item.get('item_type')!r}（地租/修葺/保险/按揭利息"
                f"均不可扣，REQ-5/Q26/Q27；显式 \"0\" 合法无效果）",
                field=field,
            )


def _amount(value: object, field: str) -> Fraction:
    """C2.1 金额 → 精确有理（金额链路零浮点）。"""
    try:
        text = parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    return Fraction(Decimal(text))


def _required_amount(container: Mapping[str, Any], key: str, where: str) -> Fraction:
    if key not in container:
        raise AppError("E_INPUT_MISSING", f"{where} 缺少 {key}", field=f"{where}.{key}")
    return _amount(container[key], f"{where}.{key}")


def _fraction_text(value: Fraction) -> str:
    """canonical 字符串（金额分母均为 10 的幂或整数，Decimal 除法精确）。"""
    return format_canonical(Decimal(value.numerator) / Decimal(value.denominator))
