"""REQ-4 利得税引擎（M2 Green；Annex D D4 税法语义权威，Annex C C3.3/C3.4 信封）。

REQ: REQ-4（SDD §9：法团/非法团与两级制资格显式确认；应评税利润为确认事实；
普通合伙可算并明示不等同合伙人个人税；详见 Annex D D4）。
规格锚点:
  - D4.1 税率：法团 8.25%/16.5%、非法团 7.5%/15%，首个 2,000,000（2018/19 起）。
  - D4.2 两级制资格：`two_tier` 三态事实（election_made／
    no_other_election_same_year／connected_entities[]，基期末＋控制理由）任一
    缺失或 unknown → needs_input，不得默认符合/不符合；无效选择/未选择＝事实
    分支 → 全额 16.5%／15%（不是错误、不追问）。
  - D4.3 合伙业务层：以前年度亏损先抵业务层（抵亏后才适用两级门槛）；
    混合合伙门槛 h_kind＝2,000,000×该类合计分享比例（按 partner_kind 聚合，
    不得按合伙人逐个拆档）；PA 相关税＝业务税×PA 份额比例（floor），余额以
    合伙名义征收；next_provisional 按抵亏前利润（不得复用已消耗亏损）。
  - 取整：非法团＝三年度官方计算器 floor 链（择档 floor(净利)→低档全率→税额
    raw 后 floor→宽减 ceil 后扣；T1-observed 已闭）；法团/混合＝产品精度约定
    （floor 至整元；pending_verification 须含「产品约定」标识；相反官方证据 →
    fail-closed）。
  - 附表 43：宽减每业务一个（s.100(3)）；2024/25 cap 1,500、2025/26 cap 3,000、
    2026/27 无条目；宽减不适用于暂缴。
  - 信封：status/amounts 六键（C3.3/C3.4）；needs_input 六键一律显式 None
    （不填零、不部分计算）。

【拟名】被测契约（tests/engines/test_profits_engine.py）:
  calculate_profits_tax(facts: Mapping, bundle: Mapping) -> dict
  - 纯函数、注入不可变 RuleBundle；金额全程 Decimal/Fraction，无 float。
  - facts 字段名＝C3.5/D4.4：entity_kind／assessable_profit／partners[]
    （partner_id／partner_kind／share:RatioStr）／two_tier／
    loss_brought_forward／partner_assessable_allocations[]／
    pa_election_partners[]。
  - partnership 结果携带 two_tier_allocation[]（按 partner_kind 聚合）；
    存在 pa_election_partners 时另带 pa_related_tax／remaining_business_tax。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction

from app.core.errors import AppError
from app.core.money import (
    ceil_at_point,
    floor_at_point,
    parse_amount,
    parse_ratio,
    validate_shares,
)

_AMOUNT_KEYS: tuple[str, ...] = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)

_ENTITY_KINDS = frozenset({"corporation", "partnership", "sole_proprietorship"})
_PARTNER_KINDS = frozenset({"corporation", "individual"})

# D4.3/D10：法团/混合取整无适用官方证据 → 产品精度约定（须显式标注）。
_PRODUCT_CONVENTION_NOTE = (
    "法团/混合主体最终税额取整＝产品约定：floor 至整元、择档 floor(计税利润)、"
    "宽减 ceil 后扣；未经 IRD 文字证明适用于法团评税，列用户 Gate2 确认项"
    "（Annex D D4.3/D10；相反官方证据 → fail-closed）。"
)

# REQ-4：合伙业务层税额不等同各合伙人个人税（D4.3）。
_PARTNERSHIP_NOTE = (
    "本金额为合伙业务层税额，不等同各合伙人个人税；PA 相关税为税前结构拆分"
    "（Annex D D4.3）。"
)


def _amount(value: int) -> str:
    """整元税额 → canonical 字符串（C2.10；整数无小数展开）。"""
    return str(value)


def _parse_money(value: object) -> Fraction:
    """AmountStr → 精确 Fraction（C2.1/C2.3；Decimal 构造，禁 float）。"""
    return Fraction(Decimal(parse_amount(value)))


def _required(facts: Mapping, key: str) -> object:
    if key not in facts:
        raise AppError("E_INPUT_MISSING", f"缺少必填事实字段：{key}", field=key)
    return facts[key]


def _profits_data(bundle: Mapping) -> Mapping:
    data = bundle.get("data")
    profits = data.get("profits") if isinstance(data, Mapping) else None
    if isinstance(profits, Mapping) and profits:
        return profits
    raise AppError(
        "E_RULES_STATE_UNVERIFIABLE",
        "利得税规则数据缺失（bundle.data.profits）；拒算。",
    )


def _threshold(profits: Mapping) -> Fraction:
    return _parse_money(profits["two_tier_threshold"])


def _rates(profits: Mapping, kind: str) -> tuple[Fraction, Fraction]:
    rates = profits["two_tier_rates_bp"][kind]
    return Fraction(int(rates["low"]), 10000), Fraction(int(rates["high"]), 10000)


def _tri_state_value(container: Mapping, key: str) -> bool | None:
    """三态事实 `{value: true|false|"unknown"}`；缺失/未知/非法形态 → None。"""
    fact = container.get(key)
    if not isinstance(fact, Mapping):
        return None
    value = fact.get("value")
    if value is True:
        return True
    if value is False:
        return False
    return None


def _two_tier_eligibility(facts: Mapping) -> tuple[bool, str | None]:
    """D4.2：返回 (两级制适用, 待追问问题)。任一资格事实缺失/unknown → 问题。"""
    two_tier = facts.get("two_tier")
    if not isinstance(two_tier, Mapping):
        return False, (
            "请确认两级制资格事实（two_tier）：基期结束时关联实体（无关联也须"
            "声明）、本年度是否选择两级制、是否无其他关联实体选择（D4.2 Q13）。"
        )
    election = _tri_state_value(two_tier, "election_made")
    if election is None:
        return False, (
            "请确认本年度是否已选择以两级制课税（election_made；未知不得默认，D4.2）。"
        )
    no_other = _tri_state_value(two_tier, "no_other_election_same_year")
    if no_other is None:
        return False, (
            "请确认本年度是否无其他关联实体选择两级制（no_other_election_same_year；"
            "未知不得默认，D4.2）。"
        )
    connected = two_tier.get("connected_entities")
    if not isinstance(connected, list):
        return False, (
            "请声明基期结束时的关联实体列表（connected_entities；无关联请给空"
            "列表，D4.2 Q13）。"
        )
    for index, entity in enumerate(connected):
        if not isinstance(entity, Mapping):
            return False, (
                f"关联实体第 {index + 1} 项事实不完整：请补全 entity_id／"
                "basis_period_end／control_basis（D4.2）。"
            )
        basis_end = entity.get("basis_period_end")
        control = entity.get("control_basis")
        if (
            not isinstance(basis_end, str)
            or not basis_end
            or basis_end == "unknown"
            or not isinstance(control, str)
            or not control
            or control == "unknown"
        ):
            return False, (
                f"关联实体 {entity.get('entity_id', index + 1)!r} 的基期末／控制理由"
                "未确认（basis_period_end／control_basis），请补全（D4.2）。"
            )
    return (election is True and no_other is True), None


def _bracket_tax(
    slice_amount: Fraction,
    threshold: Fraction,
    low_rate: Fraction,
    high_rate: Fraction,
) -> tuple[int, int, int]:
    """两档税额，均 floor 至整元；返回 (low_tier_tax, high_tier_tax, 合计)。

    择档取整点＝floor(分片利润) ≤ 分摊门槛 → 低档全率（跨界区间低档全率，
    D6.2a）；否则 门槛×低率 ＋ (利润−门槛)×高率（D4.1/D4.3）。
    """
    if floor_at_point(slice_amount) <= threshold:
        low = floor_at_point(slice_amount * low_rate)
        return low, 0, low
    low = floor_at_point(threshold * low_rate)
    high = floor_at_point((slice_amount - threshold) * high_rate)
    return low, high, low + high


def _flat_tax(slice_amount: Fraction, rate: Fraction) -> int:
    """非两级制分支：全额按一般税率（法团 16.5%／非法团 15%），floor。"""
    if slice_amount <= 0:
        return 0
    return floor_at_point(slice_amount * rate)


def _parse_partners(partners: object) -> list[tuple[str, str, Fraction]]:
    if not isinstance(partners, list) or not partners:
        raise AppError(
            "E_INPUT_MISSING",
            "合伙业务须提供 partners[]（partner_id／partner_kind／share）",
            field="partners",
        )
    raw_shares: list[object] = []
    parsed: list[tuple[str, str, Fraction]] = []
    for index, partner in enumerate(partners):
        if not isinstance(partner, Mapping):
            raise AppError(
                "E_INPUT_TYPE", f"partners[{index}] 必须为对象", field="partners"
            )
        partner_id = partner.get("partner_id")
        partner_kind = partner.get("partner_kind")
        if not isinstance(partner_id, str) or not partner_id:
            raise AppError(
                "E_INPUT_MISSING",
                f"partners[{index}].partner_id 缺失",
                field="partners",
            )
        if not isinstance(partner_kind, str) or partner_kind not in _PARTNER_KINDS:
            raise AppError(
                "E_INPUT_TYPE",
                f"partners[{index}].partner_kind 必须为 corporation／individual",
                field="partners",
            )
        raw_shares.append(partner.get("share"))
        parsed.append((partner_id, partner_kind, parse_ratio(partner.get("share"))))
    validate_shares(raw_shares)
    return parsed


def _parse_allocations(
    facts: Mapping, base: Fraction
) -> dict[str, Fraction]:
    """D4.3：分配表 `assessable_amount`＝抵亏后税基份额；Σ＝B，否则拒算。"""
    allocations = facts.get("partner_assessable_allocations")
    by_partner: dict[str, Fraction] = {}
    if allocations is None:
        return by_partner
    if not isinstance(allocations, list):
        raise AppError(
            "E_INPUT_TYPE",
            "partner_assessable_allocations 必须为列表",
            field="partner_assessable_allocations",
        )
    total = Fraction(0)
    for index, row in enumerate(allocations):
        if not isinstance(row, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"partner_assessable_allocations[{index}] 必须为对象",
                field="partner_assessable_allocations",
            )
        partner_id = row.get("partner_id")
        if not isinstance(partner_id, str) or not partner_id:
            raise AppError(
                "E_INPUT_MISSING",
                f"partner_assessable_allocations[{index}].partner_id 缺失",
                field="partner_assessable_allocations",
            )
        amount = _parse_money(row.get("assessable_amount"))
        by_partner[partner_id] = by_partner.get(partner_id, Fraction(0)) + amount
        total += amount
    if total != base:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            "分配表份额合计必须等于抵亏后业务计税利润 B（Σ(份额)＝B；D4.3）",
            field="partner_assessable_allocations",
        )
    return by_partner


def _reduction(profits: Mapping, year: object, before: int) -> int:
    """附表 43：每业务一个宽减；percent×税额 ceil 后按年 cap，扣除后 floor。"""
    entries = profits.get("reductions")
    if not isinstance(entries, Mapping) or not isinstance(year, str):
        return 0
    entry = entries.get(year)
    if not isinstance(entry, Mapping):
        return 0
    percent = Fraction(int(entry["percent_bp"]), 10000)
    cap = floor_at_point(_parse_money(entry["cap"]))
    return min(ceil_at_point(percent * before), cap, before)


def _needs_input(question: str) -> dict:
    """C3.3：needs_input＋中文问题清单；六金额键一律显式 None（不填零）。"""
    return {
        "status": "needs_input",
        "amounts": {key: None for key in _AMOUNT_KEYS},
        "questions": [question],
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": list(_AMOUNT_KEYS),
        "steps": [],
        "evidence_refs": [],
    }


def calculate_profits_tax(facts: Mapping, bundle: Mapping) -> dict:
    """REQ-4 利得税主入口：纯函数、确定性；金额从注入 bundle 数据取值。"""
    profits = _profits_data(bundle)

    year = _required(facts, "year_of_assessment")
    supported_years = (bundle.get("applicability") or {}).get("years") or []
    if supported_years and year not in supported_years:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"课税年度 {year!r} 不在已核验支持范围 {supported_years!r}",
            field="year_of_assessment",
        )

    entity_kind = _required(facts, "entity_kind")
    if not isinstance(entity_kind, str) or entity_kind not in _ENTITY_KINDS:
        raise AppError(
            "E_INPUT_TYPE",
            "entity_kind 必须为 corporation／partnership／sole_proprietorship",
            field="entity_kind",
        )

    profit = _parse_money(_required(facts, "assessable_profit"))
    eligibility, question = _two_tier_eligibility(facts)
    if question is not None:
        return _needs_input(question)

    loss = Fraction(0)
    if "loss_brought_forward" in facts:
        loss = _parse_money(facts["loss_brought_forward"])
    base = profit - loss
    tax_base = base if base > 0 else Fraction(0)
    threshold = _threshold(profits)

    pending: list[str] = []
    warnings: list[str] = []
    allocation_rows: list[dict] | None = None
    pa_related: int | None = None
    pa_remaining: int | None = None

    if entity_kind == "partnership":
        parsed_partners = _parse_partners(facts.get("partners"))
        allocation_by_partner = _parse_allocations(facts, base)

        groups: dict[str, Fraction] = {}
        for _, partner_kind, share in parsed_partners:
            groups[partner_kind] = groups.get(partner_kind, Fraction(0)) + share
        group_order = sorted(groups)

        mixed = any(kind == "corporation" for _, kind, _ in parsed_partners)
        rounding = profits.get("rounding")
        if (
            mixed
            and isinstance(rounding, Mapping)
            and rounding.get("mixed_partnership") == "product_convention_floor"
        ):
            pending.append(_PRODUCT_CONVENTION_NOTE)
        warnings.append(_PARTNERSHIP_NOTE)

        before = 0
        provisional = 0
        if eligibility:
            allocation_rows = []
        for kind in group_order:
            share_sum = groups[kind]
            rates_kind = "corporation" if kind == "corporation" else "unincorporated"
            low_rate, high_rate = _rates(profits, rates_kind)
            if eligibility:
                low, high, group_total = _bracket_tax(
                    tax_base * share_sum, threshold * share_sum, low_rate, high_rate
                )
                if allocation_rows is not None:
                    allocation_rows.append(
                        {
                            "partner_kind": kind,
                            "threshold": _amount(
                                floor_at_point(threshold * share_sum)
                            ),
                            "low_tier_tax": _amount(low),
                            "high_tier_tax": _amount(high),
                        }
                    )
                _, _, provisional_group = _bracket_tax(
                    profit * share_sum, threshold * share_sum, low_rate, high_rate
                )
            else:
                group_total = _flat_tax(tax_base * share_sum, high_rate)
                provisional_group = _flat_tax(profit * share_sum, high_rate)
            before += group_total
            provisional += provisional_group

        pa_election = facts.get("pa_election_partners")
        if pa_election:
            if not isinstance(pa_election, list):
                raise AppError(
                    "E_INPUT_TYPE",
                    "pa_election_partners 必须为列表",
                    field="pa_election_partners",
                )
            known_ids = {partner_id for partner_id, _, _ in parsed_partners}
            for partner_id in pa_election:
                if partner_id not in known_ids:
                    raise AppError(
                        "E_INPUT_CONTRADICTORY",
                        f"pa_election_partners 含未知伙伴：{partner_id!r}",
                        field="pa_election_partners",
                    )
            if allocation_by_partner:
                pa_sum = sum(
                    (allocation_by_partner.get(pid, Fraction(0)) for pid in pa_election),
                    Fraction(0),
                )
                pa_ratio = pa_sum / base if base != 0 else Fraction(0)
            else:
                pa_ids = set(pa_election)
                pa_ratio = sum(
                    (share for pid, _, share in parsed_partners if pid in pa_ids),
                    Fraction(0),
                )
            pa_related = floor_at_point(Fraction(before) * pa_ratio)
            pa_remaining = before - pa_related
    else:
        rates_kind = "corporation" if entity_kind == "corporation" else "unincorporated"
        low_rate, high_rate = _rates(profits, rates_kind)
        rounding = profits.get("rounding")
        if (
            entity_kind == "corporation"
            and isinstance(rounding, Mapping)
            and rounding.get("corporation") == "product_convention_floor"
        ):
            pending.append(_PRODUCT_CONVENTION_NOTE)
        if eligibility:
            _, _, before = _bracket_tax(tax_base, threshold, low_rate, high_rate)
            _, _, provisional = _bracket_tax(profit, threshold, low_rate, high_rate)
        else:
            before = _flat_tax(tax_base, high_rate)
            provisional = _flat_tax(profit, high_rate)

    reduction = _reduction(profits, year, before)
    final = before - reduction

    # C3.5 利得税事实族无 provisional_paid（已缴暂缴）事实——provisional_paid/
    # balance 无事实来源、恒 null（不得以零填补，C3.4）；可算组件齐备即 complete。
    result: dict = {
        "status": "complete",
        "amounts": {
            "before_reduction": _amount(before),
            "reduction": _amount(reduction),
            "final_after_reduction": _amount(final),
            "provisional_paid": None,
            "next_provisional": _amount(provisional),
            "balance": None,
        },
        "questions": [],
        "warnings": warnings,
        "unsupported": [],
        "pending_verification": pending,
        "missing_components": [],
        "steps": [],
        "evidence_refs": [],
    }
    if allocation_rows is not None:
        result["two_tier_allocation"] = allocation_rows
    if pa_related is not None and pa_remaining is not None:
        result["pa_related_tax"] = _amount(pa_related)
        result["remaining_business_tax"] = _amount(pa_remaining)
    return result
