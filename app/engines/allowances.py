# -*- coding: utf-8 -*-
"""共享 Part 5 免税额解析器与 v2 事实原语（002 P1；Annex 005 §3–§9/§13/§14）。

- TF 左闭右开、默认窗口＝该 YA [4月1日, 次年4月1日)；谓词按日求值（ANY/ALL/
  SAMEPOINT 有限量词，无通用 DSL）；缺键/未覆盖＝unknown，≠ false ≠ "0"。
- Tri：原始 true/false/"unknown"；缺失＝未提供；四者互不相代。
- 免税额矩阵读取 `bundle.data.allowances.<YA>`：节点缺键＝规则不可核验 →
  E_RULES_STATE_UNVERIFIABLE（fail-closed）；not_applicable_verified＝计 0。
- claim ledger 校验与决策（entitlement/grant 四态不合并）；金额全程 Fraction/
  Decimal，无浮点；Exact 串 "num/den"。
- AvailabilityContext 显式参数校验：缺 context 或与 bundle/engine/目标年不匹配
  → E_RULES_STATE_UNVERIFIABLE（无默认放行）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable, Iterable, Mapping

from app.config import ENGINE_VERSION
from app.core.errors import AppError
from app.core.hashing import bundle_content_hash
from app.core.money import ceil_at_point, floor_at_point, format_canonical, parse_amount
from app.rules.admission import (
    MAX_REDUCTION_PERCENT_BP,
    REDUCTION_STATUS_BY_YEAR,
    verify_bundle_admission,
)

MANUAL = "manual"

AMOUNT_KEYS: tuple[str, ...] = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)

_CLAIM_KINDS = frozenset(
    {
        "child",
        "parent",
        "grandparent",
        "sibling",
        "single_parent",
        "personal_disability",
        "disabled_dependant",
        "parent_cohabitation_additional",
    }
)
_MUTEX_PAIRS = (frozenset({"child", "sibling"}), frozenset({"parent", "grandparent"}))
_QUALIFIED_HOME_BASES = frozenset(
    {
        "cap459_licence",
        "cap459_s3_excluded",
        "cap633_scheduled_nursing_s128_exemption",
        "cap613_licence",
        "cap613_exemption",
    }
)
_PA_MODES = frozenset({"none", "individual", "joint", "unknown"})


# ---------------------------------------------------------------------------
# 日期／金额原语
# ---------------------------------------------------------------------------


def year_window(year: str) -> tuple[date, date]:
    start = int(year[:4])
    return date(start, 4, 1), date(start + 1, 4, 1)


def following_year(year: str) -> str:
    start = int(year[:4])
    return f"{start + 1}_{(start + 2) % 100:02d}"


def previous_year(year: str) -> str:
    start = int(year[:4])
    return f"{start - 1}_{start % 100:02d}"


def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 2/29 → 平年 2/28
        return day.replace(year=day.year + years, day=28)


def add_months(day: date, months: int, *, truncate: bool) -> date | None:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    try:
        return date(year, month, day.day)
    except ValueError:
        if not truncate:
            return None
        # 月末不存在同日：截短至该月最后一日（仅用于 OPEN 边界判定）
        if month == 12:
            next_first = date(year + 1, 1, 1)
        else:
            next_first = date(year, month + 1, 1)
        return next_first - timedelta(days=1)


def _days(window: tuple[date, date]) -> Iterable[date]:
    day, end = window
    while day < end:
        yield day
        day += timedelta(days=1)


def parse_money(value: object, field: str) -> Fraction:
    """C2.1 金额（非负）→ 精确有理；类型/语法非法 → E_INPUT_TYPE。"""
    try:
        text = parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    return Fraction(Decimal(text))


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 必须为 YYYY-MM-DD 全匹配字符串", field=field
        )
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AppError("E_INPUT_TYPE", f"{field} 日期格式非法：{value!r}", field=field) from None


def exact_text(value: Fraction) -> str:
    fr = Fraction(value)
    return f"{fr.numerator}/{fr.denominator}"


def canonical_amount(value: Fraction) -> str:
    fr = Fraction(value)
    if fr.denominator == 1:
        return format_canonical(Decimal(fr.numerator))
    return format_canonical(Decimal(fr.numerator) / Decimal(fr.denominator))


def parse_ratio(value: object, field: str) -> Fraction:
    from app.core.money import parse_ratio as _parse_ratio

    try:
        parsed = _parse_ratio(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    if parsed <= 0 or parsed > 1:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 份额域须 0<num≤den（C2.3）", field=field
        )
    return parsed


# ---------------------------------------------------------------------------
# 封闭结构/词法辅助（R3.1：逐层 extra、ID 前缀、数组边界、显式 null）
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
_MAX_ARRAY_ITEMS = 1000

_SUBJECT_FACT_KEYS = (
    "unmarried",
    "govt_disability_allowance",
    "full_time_education",
    "incapacitated_for_work",
    "ordinarily_resident_hk",
    "both_parents_dead",
    "death_date",
)
_SUBJECT_KEYS = ("subject_id", "birth_date")
_PARTICIPANT_KEYS = ("person_id", "role")
_HOUSEHOLD_KEYS = ("marriage", "living_apart", "child_nomination")
_NOMINATION_KEYS = ("person_id", "status")
_RELATION_KEYS = (
    "relation_id",
    "actor_id",
    "subject_id",
    "kinship",
    "maintained",
    "cash_maintenance",
    "cohabit_non_full_consideration",
    "sole_or_predominant_care",
)
_KINSHIP_KEYS = ("kind", "basis", "during")
_KINSHIP_KINDS = ("child", "parent", "grandparent", "sibling", "none", "unknown")
_KINSHIP_BASES = (
    "birth",
    "adoption",
    "step",
    "full_blood",
    "half_blood",
    "child_of_adoptive_parent",
    "other",
    "unknown",
)
# 普通已核映射（005 §4.5；其余组合可提交但在核清前定位 narrow manual）
_KINSHIP_BASIS_MAP: dict[str, set[str]] = {
    "child": {"birth", "adoption", "step"},
    "parent": {"birth", "adoption"},
    "grandparent": {"birth"},
    "sibling": {
        "full_blood",
        "half_blood",
        "adoption",
        "step",
        "child_of_adoptive_parent",
    },
}


def kinship_unverified(kind: str, basis: str) -> bool:
    verified = _KINSHIP_BASIS_MAP.get(kind)
    return verified is not None and basis not in verified
_FINANCE_KEYS = (
    "employment_income_nai",
    "mpf_mandatory_contributions",
    "has_assessable_salary_income",
    "has_assessable_property_income",
    "has_assessable_business_income",
    "has_income_assessable_under_ordinance",
    "source_classification",
    "properties",
    "businesses",
    "deduction_items",
)
_SOURCE_CLASS_KEYS = ("salary", "property", "business")
_SOURCE_CLASS_VALUES = ("confirmed_in_scope", "unknown", "manual")
_PROPERTY_KEYS = (
    "property_id",
    "share",
    "rent_received",
    "rates_paid_by_owner",
    "irrecoverable_rent",
    "deposit_offsets",
    "mortgage_interest",
)
_RATES_KEYS = ("amount", "agreed", "actually_paid")
_BUSINESS_KEYS = ("business_id", "entity_kind", "assessable_profit", "share")
_CARE_KEYS = (
    "payment_id",
    "payer_id",
    "subject_id",
    "paid_on",
    "amount",
    "care_from",
    "care_to",
    "residential_care_received",
    "paid_to_home_or_agent",
    "home_basis",
    "home_status_in_force",
)
_HOME_BASES = (
    "cap459_licence",
    "cap459_s3_excluded",
    "cap633_scheduled_nursing_s128_exemption",
    "cap613_licence",
    "cap613_exemption",
    "other",
    "unknown",
)
_CLAIM_KEYS = ("claim_id", "kind", "claimant_id", "subject_id", "special_situation")
_DEDUCTION_ITEM_KEYS = ("item_type", "amount", "eligibility", "subject_id", "payment_ids")
_ASSESSMENT_KEYS = (
    "basis",
    "candidates",
    "selection",
    "reference_date",
    "actual",
    "hypothetical_salary_joint_nomination",
)
_ACTUAL_KEYS = (
    "pa",
    "history",
    "salary_joint_elected",
    "salary_joint_assessed",
    "salary_joint_nomination",
)
_PA_ENTRY_KEYS = ("mode", "date")
_HISTORY_VALUES = ("ordinary", "withdrawn", "disputed", "unknown")
_MANUAL_ISSUE_KEYS = ("scope", "id", "code")
_MANUAL_SCOPES = ("person", "subject", "relation", "property", "business", "election")
_MANUAL_CODES = (
    "death_in_year",
    "dispute",
    "source_uncertain",
    "loss_or_transfer",
    "other",
)
_ASSUMPTION_KEYS = ("assumption_id", "kind", "person_id", "source_year", "target_year")
_PRESENCE_KEYS = ("person_id", "year", "days")
_TOP_LEVEL_KEYS = ("year_of_assessment", "subjects", "years", "hk_presence_days", "assumptions")
_YEAR_KEYS = (
    "participants",
    "subject_facts",
    "household",
    "relations",
    "financials",
    "care_payments",
    "claims",
    "assessment",
    "manual_issues",
    "provisional_paid",
)


def check_id(value: object, field: str, prefixes: tuple[str, ...]) -> str:
    """ID 前缀＋词法（§3：前缀＋1–31 个小写字母/数字/连字符）。"""
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 须为 前缀＋1–31 个小写字母/数字/连字符 的 ID",
            field=field,
        )
    if not value.startswith(prefixes):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 前缀不合法（允许 {prefixes}）",
            field=field,
        )
    return value


def reject_extra_keys(obj: Mapping, allowed: tuple[str, ...], field: str) -> None:
    for key in obj:
        if key not in allowed:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未列字段 {key!r}（封闭键集合）", field=field
            )


def limit_array(raw: list, field: str) -> None:
    if len(raw) > _MAX_ARRAY_ITEMS:
        raise AppError(
            "E_INPUT_OVERSIZE",
            f"{field} 超过既有上限 {_MAX_ARRAY_ITEMS} 项（C2.9）",
            field=field,
        )


def tf_field(container: Mapping, key: str, field: str) -> list[tuple[date, date, Any]]:
    """键存在时解析 TF；显式 null 拒绝（null≠省略）。"""
    if key not in container:
        return []
    raw = container[key]
    if raw is None:
        raise AppError("E_INPUT_TYPE", f"{field} 显式 null 非法（null≠省略）", field=field)
    return parse_tf(raw, field)


def _validate_tf_domain(
    intervals: list[tuple[date, date, Any]],
    field: str,
    window: tuple[date, date],
    extension_days: set[date],
    *,
    extendable: bool = False,
) -> None:
    """TF 允许域＝键年窗口；仅列明字段可额外覆盖参考/选择日——每个外部日期只
    开放该日的一天区间（§7），更长越域一律拒绝。"""
    for start, end, _ in intervals:
        if window[0] <= start and end <= window[1]:
            continue
        if extendable:
            if end - start == timedelta(days=1):
                continue
            if any(start >= day and end <= day + timedelta(days=1) for day in extension_days):
                continue
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field} 区间超出允许域（默认键年窗口或单日参考/选择日区间）",
            field=field,
        )


# ---------------------------------------------------------------------------
# 三态工具
# ---------------------------------------------------------------------------


def tri_not(value: Any) -> Any:
    if value is True:
        return False
    if value is False:
        return True
    return None


def tri_and(*values: Any) -> Any:
    manual = False
    unknown = False
    for value in values:
        if value is False:
            return False
        if value is None:
            unknown = True
        elif value == MANUAL:
            manual = True
    if manual:
        return MANUAL
    if unknown:
        return None
    return True


def tri_or(*values: Any) -> Any:
    manual = False
    unknown = False
    for value in values:
        if value is True:
            return True
        if value is None:
            unknown = True
        elif value == MANUAL:
            manual = True
    if manual:
        return MANUAL
    if unknown:
        return None
    return False


def tri_from_raw(raw: Any, field: str) -> Any:
    """原始三态：true/false/"unknown"；缺失 → None（未提供）。"""
    if raw is True or raw is False:
        return raw
    if raw == "unknown":
        return None
    if raw is None:
        return None
    raise AppError(
        "E_INPUT_TYPE", f'{field} 三态值须为 true/false/"unknown"', field=field
    )


# ---------------------------------------------------------------------------
# TF 区间
# ---------------------------------------------------------------------------


def parse_tf(raw: object, field: str) -> list[tuple[date, date, Any]]:
    """Tri TF：`[{from,to,value}]`；左闭右开、按序且互不重叠；unknown → None。

    元素必须为对象（嵌套数组不得兼容展开，005 §3/A3）；显式 null 由调用方在
    「键存在」时拒绝，不冒充省略。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", f"{field} 须为数组", field=field)
    intervals: list[tuple[date, date, Any]] = []
    for index, item in enumerate(raw):
        where = f"{field}[{index}]"
        if not isinstance(item, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
        start = parse_date(item.get("from"), f"{where}.from")
        end = parse_date(item.get("to"), f"{where}.to")
        if start >= end:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{where} 区间须 from<to（左闭右开）", field=where
            )
        value = item.get("value")
        if value is True or value is False:
            pass
        elif value == "unknown":
            value = None
        else:
            raise AppError(
                "E_INPUT_TYPE",
                f'{where}.value 三态值须为 true/false/"unknown"',
                field=where,
            )
        intervals.append((start, end, value))
    _check_sorted_non_overlapping(intervals, field)
    return intervals


def parse_marriage_tf(
    raw: object, field: str
) -> list[tuple[date, date, Any, str | None]]:
    """MarriageTF：value ∈ unmarried/married/unknown；married 必带 spouse_id。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", f"{field} 须为数组", field=field)
    intervals: list[tuple[date, date, Any, str | None]] = []
    for index, item in enumerate(raw):
        where = f"{field}[{index}]"
        if not isinstance(item, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
        reject_extra_keys(item, ("from", "to", "value", "spouse_id"), where)
        start = parse_date(item.get("from"), f"{where}.from")
        end = parse_date(item.get("to"), f"{where}.to")
        if start >= end:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{where} 区间须 from<to（左闭右开）", field=where
            )
        value = item.get("value")
        spouse = item.get("spouse_id")
        if value == "married":
            if not isinstance(spouse, str) or not spouse:
                raise AppError(
                    "E_INPUT_CONTRADICTORY", f"{where} married 区间必须带 spouse_id", field=where
                )
        elif value == "unmarried":
            value = "unmarried"
            if "spouse_id" in item:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where} 非 married 区间禁止 spouse_id",
                    field=where,
                )
            spouse = None
        elif value == "unknown":
            value = None
            if "spouse_id" in item:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where} 非 married 区间禁止 spouse_id",
                    field=where,
                )
            spouse = None
        else:
            raise AppError(
                "E_INPUT_TYPE",
                f'{where}.value 须为 "unmarried"/"married"/"unknown"',
                field=where,
            )
        intervals.append((start, end, value, spouse))
    _check_sorted_non_overlapping([(a, b, v) for a, b, v, _ in intervals], field)
    return intervals


def _check_sorted_non_overlapping(
    intervals: list[tuple[date, date, Any]], field: str
) -> None:
    """按提交顺序核验：须非降序起点且互不重叠（TF 按序合同；逆序拒绝）。"""
    previous_start: date | None = None
    previous_end: date | None = None
    for start, end, _ in intervals:
        if previous_start is not None and start < previous_start:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{field} 区间须按起点升序（不得逆序）", field=field
            )
        if previous_end is not None and start < previous_end:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{field} 区间重叠（须按序且互不重叠）", field=field
            )
        previous_start = start
        previous_end = end


def tf_at(intervals: list[tuple[date, date, Any]], day: date) -> Any:
    for start, end, value in intervals:
        if day < start:
            return None
        if day < end:
            return value
    return None


def marriage_at(
    intervals: list[tuple[date, date, Any, str | None]], day: date
) -> tuple[Any, str | None]:
    for start, end, value, spouse in intervals:
        if day < start:
            return None, None
        if day < end:
            return value, spouse
    return None, None


def any_over(fn: Callable[[date], Any], window: tuple[date, date]) -> Any:
    unknown = False
    for day in _days(window):
        value = fn(day)
        if value is True:
            return True
        if value is None:
            unknown = True
    return None if unknown else False


def all_over(fn: Callable[[date], Any], window: tuple[date, date]) -> Any:
    unknown = False
    for day in _days(window):
        value = fn(day)
        if value is False:
            return False
        if value is None:
            unknown = True
    return None if unknown else True


def samepoint(fns: list[Callable[[date], Any]], window: tuple[date, date]) -> Any:
    """ANY(各谓词在同一日期的 AND, W)：任一日期全真→true；全部日期已证假→false。"""
    unknown = False
    for day in _days(window):
        values = [fn(day) for fn in fns]
        if any(value is False for value in values):
            continue
        if any(value is None for value in values):
            unknown = True
            continue
        return True
    return None if unknown else False


def merge_true_intervals(
    intervals: list[tuple[date, date, Any]], window: tuple[date, date]
) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for start, end, value in intervals:
        if value is not True:
            continue
        a = max(start, window[0])
        b = min(end, window[1])
        if a >= b:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def has_uncovered_date(
    intervals: list[tuple[date, date, Any]], window: tuple[date, date]
) -> bool:
    for day in _days(window):
        if tf_at(intervals, day) is None:
            return True
    return False


def six_calendar_months(
    intervals: list[tuple[date, date, Any]], window: tuple[date, date]
) -> Any:
    """连续六个日历月非足值对价同住（s.30(4)(a)(ii)）。

    已证连续 true 区间包含 [d, 相隔 6 个月同日) 即成立；start 月末同日不存在
    （如 8/31 → 2/31）＝ OPEN 边界：区间已达到截短月末但无精确同日 → manual，
    不发明统短规则；短于六个月的已证区间＝false；存在未覆盖日期＝unknown。
    """
    documented = merge_true_intervals(intervals, window)
    open_boundary = False
    for start, end in documented:
        target = add_months(start, 6, truncate=False)
        if target is not None:
            if end >= target:
                return True
        else:
            truncated = add_months(start, 6, truncate=True)
            if truncated is not None and end >= truncated:
                open_boundary = True
    if open_boundary:
        return MANUAL
    if has_uncovered_date(intervals, window):
        return None
    return False


# ---------------------------------------------------------------------------
# AvailabilityContext 与输入信封
# ---------------------------------------------------------------------------


def check_availability(
    availability: object, bundle: Mapping, year: str, *, tax_type: str
) -> dict:
    """显式 context 校验：None 或与 bundle/engine/目标年不匹配 → 拒算。

    bundle 携带 `bundle_hash` 时双向核对；测试注入束不携带哈希时仅核对
    schema/engine/year（015 §2.1：不得只检查字段存在，也不得默认放行）。
    """
    if not isinstance(availability, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"{tax_type}：缺少可用性上下文（AvailabilityContext 为显式参数，"
            "无默认放行）",
            field="availability",
        )
    binding = availability.get("binding")
    current = availability.get("current")
    upcoming = availability.get("next")
    for name, part in (("binding", binding), ("current", current), ("next", upcoming)):
        if not isinstance(part, Mapping):
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                f"可用性上下文缺少 {name}（不默认放行）",
                field=f"availability.{name}",
            )
    if binding.get("engine_version") != ENGINE_VERSION:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"可用性上下文 engine_version 与实际执行引擎不一致"
            f"（{binding.get('engine_version')!r} ≠ {ENGINE_VERSION!r}）",
            field="availability.binding.engine_version",
        )
    if binding.get("rules_schema_version") != bundle.get("rules_schema_version"):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "可用性上下文 rules_schema_version 与实际规则束不一致",
            field="availability.binding.rules_schema_version",
        )
    try:
        actual_hash = bundle_content_hash(bundle)
    except ValueError as exc:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"实际规则束内容不完整（C2.8 六字段）：{exc}",
            field="bundle",
        ) from exc
    if binding.get("bundle_hash") != actual_hash:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "可用性上下文 bundle_hash 与实际六字段 canonical 内容 hash 不一致"
            "（不得只凭字段存在或旧 hash 放行）",
            field="availability.binding.bundle_hash",
        )
    # 中立准入（E1）：实际 schema 必须为 2.0.0、固定 P＝不可变冻结锚、
    # 目标年活动规则可核验；HTTP adapter 共用同一权威。
    verify_bundle_admission(bundle, tax_type=tax_type, year=year)
    if current.get("year") != year:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"可用性上下文 current.year 与目标年不一致"
            f"（{current.get('year')!r} ≠ {year!r}）",
            field="availability.current.year",
        )
    expected_next = following_year(year)
    if upcoming.get("year") != expected_next:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "可用性上下文 next.year 与目标年紧邻下一年不一致",
            field="availability.next.year",
        )
    if upcoming.get("status") == "available":
        # E7/§2.1：next 声明 available 时，其目标年度必须与 current 共用同一
        # 权威准入（runtime.py:235–254 同语义）；仅规则不可核验可局部化为
        # missing_rules，保留 current 判定；其它错误原样传播。
        try:
            verify_bundle_admission(
                bundle, tax_type=tax_type, year=upcoming.get("year")
            )
        except AppError as exc:
            if exc.code != "E_RULES_STATE_UNVERIFIABLE":
                raise
            reasons = sorted(
                set(
                    list(upcoming.get("reason_codes") or [])
                    + ["RULES_UNVERIFIABLE"]
                )
            )
            upcoming = {
                "year": upcoming.get("year"),
                "status": "missing_rules",
                "reason_codes": reasons,
            }
    return {
        "binding": dict(binding),
        "current": dict(current),
        "next": dict(upcoming),
    }


def make_question(
    field: str,
    year: str,
    text: str,
    *,
    claim_id: str | None = None,
    subject_id: str | None = None,
) -> dict:
    question: dict[str, Any] = {"field": field, "year": year}
    if claim_id is not None:
        question["claim_id"] = claim_id
    if subject_id is not None:
        question["subject_id"] = subject_id
    question["question_zh"] = text
    return question


def dedupe_questions(questions: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for question in questions:
        key = repr(sorted(question.items(), key=lambda item: item[0]))
        if key in seen:
            continue
        seen.add(key)
        result.append(question)
    return result


# ---------------------------------------------------------------------------
# 规则矩阵读取
# ---------------------------------------------------------------------------


def matrix_for(bundle: Mapping, year: str) -> Mapping:
    data = bundle.get("data") if isinstance(bundle, Mapping) else None
    allowances = data.get("allowances") if isinstance(data, Mapping) else None
    matrix = allowances.get(year) if isinstance(allowances, Mapping) else None
    if not isinstance(matrix, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"规则矩阵缺少 allowances.{year}（缺键＝不可核验，拒算）",
            field=f"data.allowances.{year}",
        )
    return matrix


def node(matrix: Mapping, key: str, year: str) -> Mapping:
    entry = matrix.get(key)
    if not isinstance(entry, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"规则矩阵缺少 allowances.{year}.{key}（缺键＝不可核验，拒算）",
            field=f"data.allowances.{year}.{key}",
        )
    return entry


def node_amount(matrix: Mapping, key: str, year: str) -> int:
    entry = node(matrix, key, year)
    amount = entry.get("amount")
    if not isinstance(amount, str):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"allowances.{year}.{key}.amount 缺失或非法",
            field=f"data.allowances.{year}.{key}.amount",
        )
    try:
        return int(Decimal(amount))
    except (ArithmeticError, ValueError):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"allowances.{year}.{key}.amount 畸形（非 Money）：规则不可核验，拒算。",
            field=f"data.allowances.{year}.{key}.amount",
        ) from None


def node_cap(matrix: Mapping, key: str, year: str) -> int | None:
    entry = node(matrix, key, year)
    cap = entry.get("cap")
    if cap is None:
        return None
    if not isinstance(cap, str):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"allowances.{year}.{key}.cap 非法",
            field=f"data.allowances.{year}.{key}.cap",
        )
    try:
        return int(Decimal(cap))
    except (ArithmeticError, ValueError):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"allowances.{year}.{key}.cap 畸形（非 Money）：规则不可核验，拒算。",
            field=f"data.allowances.{year}.{key}.cap",
        ) from None


def node_not_applicable(matrix: Mapping, key: str, year: str) -> bool:
    entry = node(matrix, key, year)
    return entry.get("status") == "not_applicable_verified"


def mpf_cap(bundle: Mapping, year: str) -> int:
    data = bundle.get("data") if isinstance(bundle, Mapping) else None
    allowances = data.get("allowances") if isinstance(data, Mapping) else None
    text = allowances.get("mpf_deduction_cap") if isinstance(allowances, Mapping) else None
    if not isinstance(text, str):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "规则数据缺少 allowances.mpf_deduction_cap（缺键＝不可核验，拒算）",
            field="data.allowances.mpf_deduction_cap",
        )
    try:
        return int(Decimal(text))
    except (ArithmeticError, ValueError):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "allowances.mpf_deduction_cap 畸形（非 Money）：规则不可核验，拒算。",
            field="data.allowances.mpf_deduction_cap",
        ) from None


def _validated_reduction_numbers(
    entry: Mapping, *, year: str | None
) -> tuple[int, str]:
    """共享宽减表数值域（005:839）：percent_bp 0..10000 整数；cap 非负 Money。

    准入层已在 `verify_bundle_admission` 核验；此处是消费侧双保险，防止内部
    直接调用 `reduction_entry`/`salary_unit_tax` 绕过准入后消费非法值。
    `year=None` 仅用于无年度上下文的纯内部消费点（字段定位省略年度）。
    """
    prefix = f"data.reductions.{year}" if year is not None else "data.reductions"
    percent_bp = entry.get("percent_bp")
    if (
        isinstance(percent_bp, bool)
        or not isinstance(percent_bp, int)
        or not 0 <= percent_bp <= MAX_REDUCTION_PERCENT_BP
    ):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"reductions.percent_bp 须为 0..{MAX_REDUCTION_PERCENT_BP} 的整数"
            "（基点；规则不可核验，拒算）。",
            field=f"{prefix}.percent_bp",
        )
    cap = entry.get("cap")
    if not isinstance(cap, str):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "reductions.cap 缺失或非法（规则不可核验，拒算）。",
            field=f"{prefix}.cap",
        )
    try:
        parse_amount(cap, allow_negative=False)
    except AppError:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "reductions.cap 畸形（非 Money）：规则不可核验，拒算。",
            field=f"{prefix}.cap",
        ) from None
    return percent_bp, cap


def reduction_entry(bundle: Mapping, year: str) -> Mapping | None:
    """唯一宽减表读取：条目缺失/非法＝规则不可核验（拒算；§15.2）。"""
    data = bundle.get("data") if isinstance(bundle, Mapping) else None
    reductions = data.get("reductions") if isinstance(data, Mapping) else None
    if not isinstance(reductions, Mapping) or year not in reductions:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"规则束缺少 reductions.{year}（宽减节点缺键＝不可核验，拒算）",
            field=f"data.reductions.{year}",
        )
    entry = reductions[year]
    if not isinstance(entry, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"reductions.{year} 形状非法（不可核验，拒算）",
            field=f"data.reductions.{year}",
        )
    # §15.2/005:839：status 语义与准入同源（错误年度已由 verify_bundle_admission
    # 拒绝）；此处双保险，防止直接调用本助手绕过准入把本年宽减静默置零。
    status = entry.get("status")
    expected_status = REDUCTION_STATUS_BY_YEAR.get(year)
    if expected_status is None or status != expected_status:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"reductions.{year}.status 非法（不可核验，拒算）",
            field=f"data.reductions.{year}.status",
        )
    # 数值域不论 status 均核验（NA 仍须可核验；合法 NA 返回 None 的合同语义保留）。
    _validated_reduction_numbers(entry, year=year)
    if status == "not_applicable_verified":
        return None
    return entry


# ---------------------------------------------------------------------------
# 两径计税（真实执行分支：salary_floor_min_v1 / pa_unit_floor_min_v1 /
# pa_stc_comp_tp_v1；Annex 005 §14 / ADR-6）
# ---------------------------------------------------------------------------


def band_tax(base: Fraction, bands: Iterable[Mapping]) -> Fraction:
    tax = Fraction(0)
    remaining = base
    for band in bands:
        rate = Fraction(int(band["rate_bp"]), 10000)
        width = band.get("width")
        if width is None:
            tax += remaining * rate
            break
        portion = min(remaining, int(width))
        tax += portion * rate
        remaining -= portion
        if remaining <= 0:
            break
    return tax


def salary_unit_tax(
    *,
    branch: str,
    nai: Fraction,
    part4a: Fraction,
    allowances: int,
    bands: Iterable[Mapping],
    standard_bands: Iterable[Mapping],
    reduction: Mapping | None,
) -> dict:
    """单一薪俸/PA 评税单位两径择低＋年度宽减（按真实分支记 trace）。"""
    allowances_frac = Fraction(allowances)
    standard_base = nai - part4a
    nci = standard_base - allowances_frac
    if nci < 0:
        nci = Fraction(0)
    std_base_taxable = standard_base if standard_base > 0 else Fraction(0)
    progressive_raw = band_tax(nci, bands)
    standard_raw = band_tax(std_base_taxable, standard_bands)

    operations: list[dict] = []

    def op(name: str, inputs: list[Fraction], output: Fraction) -> None:
        operations.append(
            {
                "op": name,
                "inputs": [exact_text(value) for value in inputs],
                "output": exact_text(output),
            }
        )

    if branch in ("salary_floor_min_v1", "pa_unit_floor_min_v1"):
        op("progressive_tax", [nci], progressive_raw)
        op("standard_tax", [std_base_taxable], standard_raw)
        progressive = floor_at_point(progressive_raw)
        standard = floor_at_point(standard_raw)
        op("floor", [progressive_raw], Fraction(progressive))
        op("floor", [standard_raw], Fraction(standard))
        chosen = progressive if progressive <= standard else standard
        chosen_frac = Fraction(chosen)
        op(
            "choose_floor_min",
            [Fraction(progressive), Fraction(standard)],
            Fraction(chosen),
        )
    elif branch == "pa_stc_comp_tp_v1":
        op("progressive_tax", [nci], progressive_raw)
        op("standard_tax", [std_base_taxable], standard_raw)
        standard = floor_at_point(standard_raw)
        op("floor", [standard_raw], Fraction(standard))
        chosen_frac = Fraction(standard) if Fraction(standard) < progressive_raw else progressive_raw
        op(
            "choose_standard_lt",
            [progressive_raw, Fraction(standard)],
            chosen_frac,
        )
        chosen = floor_at_point(chosen_frac)
    else:
        raise ValueError(f"未知计税分支：{branch!r}")

    # E6：真实执行现场——外层 floor 记录 floor 前 Exact（CompTP raw 分数）。
    op("floor", [chosen_frac], Fraction(chosen))
    if reduction:
        # 纯内部直接调用亦不得消费非法数值（无年度上下文，字段定位省略年度）。
        _validated_reduction_numbers(reduction, year=None)
        rate = Fraction(reduction["percent_bp"], 10000)
        cap = Fraction(Decimal(parse_amount(reduction["cap"], allow_negative=False)))
    else:
        rate = Fraction(0)
        cap = Fraction(0)
    if branch == "salary_floor_min_v1":
        rebate_raw = min(Fraction(chosen) * rate, cap)
        rebate = floor_at_point(rebate_raw)
        op("rebate_min", [Fraction(chosen), rate, cap], rebate_raw)
        op("floor", [rebate_raw], Fraction(rebate))
    else:
        rate_value = Fraction(chosen) * rate
        ceil_value = ceil_at_point(rate_value)
        cap_int = floor_at_point(cap)
        rebate = min(ceil_value, cap_int, chosen)
        op("ceil", [rate_value], Fraction(ceil_value))
        op(
            "rebate_min",
            [Fraction(ceil_value), Fraction(cap_int), Fraction(chosen)],
            Fraction(rebate),
        )
    final = chosen - rebate
    op("subtract", [Fraction(chosen), Fraction(rebate)], Fraction(final))

    bases = [
        {"name": "nai", "value": exact_text(nai)},
        {"name": "part4a", "value": exact_text(part4a)},
        {"name": "allowances", "value": exact_text(allowances_frac)},
        {"name": "nci", "value": exact_text(nci)},
        {"name": "standard_base", "value": exact_text(standard_base)},
    ]
    return {
        "branch": branch,
        "bases": bases,
        "operations": operations,
        "before": chosen,
        "reduction": rebate,
        "final": final,
    }


# ---------------------------------------------------------------------------
# 年度事实解析（v2 subject 注册表＋逐年事实）
# ---------------------------------------------------------------------------


class Facts:
    """v2 facts 信封解析（目标年＋紧邻下一年；结构/引用/词法校验）。"""

    def __init__(self, facts: Mapping, bundle: Mapping) -> None:
        if not isinstance(facts, Mapping):
            raise AppError("E_INPUT_TYPE", "facts 须为对象", field="facts")
        reject_extra_keys(facts, _TOP_LEVEL_KEYS, "input")
        self.facts = facts
        self.bundle = bundle
        year = facts.get("year_of_assessment")
        if not isinstance(year, str):
            raise AppError("E_INPUT_MISSING", "缺少 year_of_assessment", field="year_of_assessment")
        self.year = year
        self.following = following_year(year)
        years = facts.get("years")
        if not isinstance(years, Mapping):
            raise AppError("E_INPUT_MISSING", "缺少 years", field="years")
        allowed = {year, self.following}
        for key in years:
            if key not in allowed:
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"years 键 {key!r} 不受理（仅目标年＋紧邻下一年键）",
                    field=f"years.{key}",
                )
        if year not in years:
            raise AppError("E_INPUT_MISSING", f"缺少目标年 years.{year}", field=f"years.{year}")
        # subjects 注册表
        raw_subjects = facts.get("subjects")
        if not isinstance(raw_subjects, list):
            raise AppError("E_INPUT_MISSING", "缺少 subjects[]", field="subjects")
        limit_array(raw_subjects, "subjects")
        self.subjects: dict[str, date | None] = {}
        for index, subject in enumerate(raw_subjects):
            where = f"subjects[{index}]"
            if not isinstance(subject, Mapping):
                raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
            reject_extra_keys(subject, _SUBJECT_KEYS, where)
            subject_id = subject.get("subject_id")
            if not isinstance(subject_id, str) or not subject_id:
                raise AppError("E_INPUT_MISSING", f"{where} 缺少 subject_id", field=where)
            check_id(subject_id, f"{where}.subject_id", ("p", "d"))
            if subject_id in self.subjects:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"subject_id 重复：{subject_id!r}",
                    field=where,
                )
            birth_raw = subject.get("birth_date")
            birth = None
            if birth_raw is not None:
                birth = parse_date(birth_raw, f"{where}.birth_date")
            self.subjects[subject_id] = birth
        self.y = parse_year(self.subjects, years[year], year, bundle)
        self.window = year_window(year)
        self.next_year_view = years.get(self.following)
        self.presence = self._parse_presence(facts.get("hk_presence_days"))
        self.assumptions = self._parse_assumptions(facts.get("assumptions"), years)

    # -- 下一年 --

    def build_next(self) -> dict | None:
        """解析下一年并按具名假设投射（仅 same_income/same_mpf）。

        下一年对象与本年使用同一结构权威（normalize_v2_facts 已在引擎入口完成
        每键年先验）：TF 域核验按该 Year 自身窗口执行。
        """
        if self.next_year_view is None:
            return None
        next_y = parse_year(
            self.subjects,
            self.next_year_view,
            self.following,
            self.bundle,
        )
        self._apply_assumptions(next_y)
        return next_y

    def _apply_assumptions(self, next_y: dict) -> None:
        for assumption in self.assumptions:
            person_id = assumption["person_id"]
            source = self.y["financials"].get(person_id)
            target = next_y["financials"].get(person_id)
            if source is None or target is None:
                continue
            if assumption["kind"] == "same_income":
                key, present_key = "nai", "nai_present"
            else:
                key, present_key = "mpf", "mpf_present"
            # E3：假设只能投射**已知** source——源缺失＝未提供（≠0），不得制造零，
            # 也不得与显式目标伪造矛盾（缺失未决由候选消费者追问）。
            if not source[present_key]:
                continue
            if target[present_key]:
                if target[key] != source[key]:
                    raise AppError(
                        "E_INPUT_CONTRADICTORY",
                        f"下一年 {assumption['target_year']} 的显式 {person_id} "
                        f"{key} 与假设 {assumption['kind']} 异额；不得最后写入者优先",
                        field=f"years.{assumption['target_year']}.financials.{person_id}.{key}",
                    )
            else:
                target[key] = source[key]
                # 投射成功＝目标事实已知（present 从"假设"成立；缺失≠零语义
                # 只在源缺失时保留）。
                target[present_key] = True

    # -- 辅助 --

    def _parse_presence(self, raw: object) -> list[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise AppError("E_INPUT_TYPE", "hk_presence_days 须为数组", field="hk_presence_days")
        limit_array(raw, "hk_presence_days")
        explicit_years = list(self._explicit_year_keys)
        allowed: set[str] = set()
        for year_key in explicit_years:
            start = int(year_key[:4])
            allowed.add(f"{start - 1}_{start % 100:02d}")
            allowed.add(year_key)
            allowed.add(f"{start + 1}_{(start + 2) % 100:02d}")
        entries: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for index, entry in enumerate(raw):
            where = f"hk_presence_days[{index}]"
            if not isinstance(entry, Mapping):
                raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
            reject_extra_keys(entry, _PRESENCE_KEYS, where)
            person_id = entry.get("person_id")
            if not isinstance(person_id, str) or person_id not in self.subjects:
                raise AppError(
                    "E_INPUT_CONTRADICTORY", f"{where}.person_id 未注册", field=where
                )
            check_id(person_id, f"{where}.person_id", ("p",))
            entry_year = entry.get("year")
            if not isinstance(entry_year, str) or entry_year not in allowed:
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"{where}.year 不在显式 Year 的 {{Y−1,Y,Y+1}} 并集内",
                    field=f"{where}.year",
                )
            key = (person_id, entry_year)
            if key in seen:
                raise AppError(
                    "E_INPUT_CONTRADICTORY", f"{where} 每人/年唯一日数记录重复", field=where
                )
            seen.add(key)
            days = entry.get("days")
            if days != "unknown":
                if isinstance(days, bool) or not isinstance(days, int):
                    raise AppError(
                        "E_INPUT_TYPE", f"{where}.days 须为整数或 \"unknown\"", field=where
                    )
                start_day, end_day = year_window(entry_year)
                max_days = (end_day - start_day).days
                if not 0 <= days <= max_days:
                    raise AppError(
                        "E_INPUT_CONTRADICTORY",
                        f"{where}.days 须在 0..{max_days}（该 YA 日数）内",
                        field=where,
                    )
            entries.append({"person_id": person_id, "year": entry_year, "days": days})
        return entries

    @property
    def _explicit_year_keys(self) -> list[str]:
        years = self.facts.get("years")
        return list(years.keys()) if isinstance(years, Mapping) else []

    def _parse_assumptions(self, raw: object, years: Mapping) -> list[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise AppError("E_INPUT_TYPE", "assumptions 须为数组", field="assumptions")
        entries: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for index, entry in enumerate(raw):
            where = f"assumptions[{index}]"
            if not isinstance(entry, Mapping):
                raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
            reject_extra_keys(entry, _ASSUMPTION_KEYS, where)
            assumption_id = entry.get("assumption_id")
            if not isinstance(assumption_id, str) or not assumption_id:
                raise AppError("E_INPUT_MISSING", f"{where} 缺少 assumption_id", field=where)
            check_id(assumption_id, f"{where}.assumption_id", ("a",))
            kind = entry.get("kind")
            if kind not in ("same_income", "same_mpf"):
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"{where}.kind 不在假设白名单（仅 same_income/same_mpf）",
                    field=f"{where}.kind",
                )
            person_id = entry.get("person_id")
            if not isinstance(person_id, str) or person_id not in self.subjects:
                raise AppError("E_INPUT_CONTRADICTORY", f"{where}.person_id 未注册", field=where)
            source_year = entry.get("source_year")
            target_year = entry.get("target_year")
            if source_year != self.year or target_year != self.following:
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"{where} 仅允许自目标年投射至紧邻下一年",
                    field=where,
                )
            if (person_id, kind) in seen:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"每人每 kind 至多一条假设（{person_id}/{kind} 重复）",
                    field=where,
                )
            seen.add((person_id, kind))
            entries.append(
                {
                    "assumption_id": assumption_id,
                    "kind": kind,
                    "person_id": person_id,
                    "source_year": source_year,
                    "target_year": target_year,
                }
            )
        return entries


def parse_year(
    subjects: Mapping[str, date | None],
    year_obj: object,
    year: str,
    bundle: Mapping,
) -> dict:
    if not isinstance(year_obj, Mapping):
        raise AppError("E_INPUT_TYPE", f"years.{year} 须为对象", field=f"years.{year}")
    window = year_window(year)
    where = f"years.{year}"
    reject_extra_keys(year_obj, _YEAR_KEYS, where)

    # participants：恰 1 self、≤1 spouse；已注册；重复 pID 矛盾
    raw_participants = year_obj.get("participants")
    if not isinstance(raw_participants, list) or not raw_participants:
        raise AppError("E_INPUT_MISSING", f"{where}.participants 必填", field=f"{where}.participants")
    participants: list[dict] = []
    seen_participants: set[str] = set()
    self_ids: list[str] = []
    spouse_ids: list[str] = []
    for index, entry in enumerate(raw_participants):
        entry_where = f"{where}.participants[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{entry_where} 须为对象", field=entry_where)
        reject_extra_keys(entry, _PARTICIPANT_KEYS, entry_where)
        person_id = entry.get("person_id")
        role = entry.get("role")
        if role not in ("self", "spouse"):
            raise AppError("E_INPUT_TYPE", f"{entry_where}.role 非法", field=f"{entry_where}.role")
        if not isinstance(person_id, str) or person_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{entry_where}.person_id 未注册",
                field=f"{entry_where}.person_id",
            )
        if person_id in seen_participants:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{where}.participants 重复 person_id：{person_id!r}",
                field=f"{where}.participants",
            )
        seen_participants.add(person_id)
        participants.append({"person_id": person_id, "role": role})
        if role == "self":
            self_ids.append(person_id)
        else:
            spouse_ids.append(person_id)
    if len(self_ids) != 1:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{where}.participants 须恰 1 名 role=self",
            field=f"{where}.participants",
        )
    if len(spouse_ids) > 1:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{where}.participants 至多 1 名 role=spouse（实际 {len(spouse_ids)}）",
            field=f"{where}.participants",
        )
    self_id = self_ids[0]
    spouse_id = spouse_ids[0] if spouse_ids else None

    # household
    household = year_obj.get("household")
    if not isinstance(household, Mapping):
        raise AppError("E_INPUT_MISSING", f"{where}.household 必填", field=f"{where}.household")
    reject_extra_keys(household, _HOUSEHOLD_KEYS, f"{where}.household")
    if "marriage" not in household or household.get("marriage") is None:
        raise AppError(
            "E_INPUT_MISSING",
            f"{where}.household.marriage 必填（显式 null 非法）",
            field=f"{where}.household.marriage",
        )
    marriage = parse_marriage_tf(household.get("marriage"), f"{where}.household.marriage")
    for _, _, value, spouse in marriage:
        if value == "married":
            if spouse not in subjects:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.household.marriage 引用未注册 subject：{spouse!r}",
                    field=f"{where}.household.marriage",
                )
            check_id(spouse, f"{where}.household.marriage.spouse_id", ("p",))
            if spouse == self_id:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.household.marriage 不得以 self 自身为配偶",
                    field=f"{where}.household.marriage",
                )
    living_apart = tf_field(
        household, "living_apart", f"{where}.household.living_apart"
    )
    nomination_raw = household.get("child_nomination")
    nomination: dict[str, Any] = {"status": "none", "person_id": None}
    if nomination_raw is not None:
        if not isinstance(nomination_raw, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.household.child_nomination 须为对象",
                field=f"{where}.household.child_nomination",
            )
        reject_extra_keys(
            nomination_raw, _NOMINATION_KEYS, f"{where}.household.child_nomination"
        )
        status = nomination_raw.get("status")
        if status not in ("none", "agreed", "disputed", "unknown"):
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.household.child_nomination.status 非法",
                field=f"{where}.household.child_nomination.status",
            )
        person = nomination_raw.get("person_id")
        if status == "agreed":
            if not isinstance(person, str) or person not in seen_participants:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.household.child_nomination agreed 须引用当年 participant",
                    field=f"{where}.household.child_nomination.person_id",
                )
            check_id(person, f"{where}.household.child_nomination.person_id", ("p",))
        else:
            if person not in (None,):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.household.child_nomination 非 agreed 须 person_id=null",
                    field=f"{where}.household.child_nomination.person_id",
                )
        nomination = {"status": status, "person_id": person}

    # subject_facts
    raw_subject_facts = year_obj.get("subject_facts")
    if raw_subject_facts is None:
        raw_subject_facts = {}
    if not isinstance(raw_subject_facts, Mapping):
        raise AppError("E_INPUT_TYPE", f"{where}.subject_facts 须为对象", field=f"{where}.subject_facts")
    subject_facts: dict[str, dict] = {}
    for subject_id, raw_facts in raw_subject_facts.items():
        fact_where = f"{where}.subject_facts.{subject_id}"
        if subject_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{fact_where} 引用未注册 subject",
                field=fact_where,
            )
        if not isinstance(raw_facts, Mapping):
            raise AppError("E_INPUT_TYPE", f"{fact_where} 须为对象", field=fact_where)
        reject_extra_keys(raw_facts, _SUBJECT_FACT_KEYS, fact_where)
        parsed: dict[str, Any] = {}
        for field_name in _SUBJECT_FACT_KEYS:
            if field_name == "death_date":
                continue
            if field_name in raw_facts:
                parsed[field_name] = tf_field(raw_facts, field_name, f"{fact_where}.{field_name}")
        if "death_date" in raw_facts:
            if raw_facts["death_date"] is None:
                raise AppError(
                    "E_INPUT_TYPE", f"{fact_where}.death_date 显式 null 非法", field=fact_where
                )
            parsed["death_date"] = parse_date(raw_facts["death_date"], f"{fact_where}.death_date")
        subject_facts[subject_id] = parsed

    # relations
    raw_relations = year_obj.get("relations")
    if raw_relations is None:
        raw_relations = []
    if not isinstance(raw_relations, list):
        raise AppError("E_INPUT_TYPE", f"{where}.relations 须为数组", field=f"{where}.relations")
    limit_array(raw_relations, f"{where}.relations")
    relations: list[dict] = []
    relation_ids: set[str] = set()
    relation_pairs: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw_relations):
        entry_where = f"{where}.relations[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{entry_where} 须为对象", field=entry_where)
        reject_extra_keys(entry, _RELATION_KEYS, entry_where)
        relation_id = entry.get("relation_id")
        actor_id = entry.get("actor_id")
        subject_id = entry.get("subject_id")
        if not isinstance(relation_id, str) or not relation_id:
            raise AppError("E_INPUT_MISSING", f"{entry_where} 缺少 relation_id", field=entry_where)
        check_id(relation_id, f"{entry_where}.relation_id", ("r",))
        if relation_id in relation_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"relation_id 重复：{relation_id!r}", field=entry_where
            )
        relation_ids.add(relation_id)
        if actor_id not in seen_participants:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{entry_where}.actor_id 须引用当年 participant",
                field=f"{entry_where}.actor_id",
            )
        check_id(actor_id, f"{entry_where}.actor_id", ("p",))
        if subject_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{entry_where}.subject_id 未注册", field=f"{entry_where}.subject_id"
            )
        check_id(subject_id, f"{entry_where}.subject_id", ("p", "d"))
        pair = (actor_id, subject_id)
        if pair in relation_pairs:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"每年 (actor_id,subject_id) 唯一（{actor_id}/{subject_id} 重复）",
                field=entry_where,
            )
        relation_pairs.add(pair)
        kinship = entry.get("kinship")
        # 005 §4.5:164 kinship 可省略；§6:468 键缺失＝未提供（≠false≠unknown）。
        # 仅显式 kind 参与解析：缺失保持 None，不得默认 "none"（显式 false 事实）。
        kind: str | None = None
        basis = "unknown"
        during: list[tuple[date, date, Any]] = []
        if isinstance(kinship, Mapping):
            reject_extra_keys(kinship, _KINSHIP_KEYS, f"{entry_where}.kinship")
            kind = kinship.get("kind")
            basis = kinship.get("basis", "unknown")
            if kind is not None and kind not in _KINSHIP_KINDS:
                raise AppError(
                    "E_INPUT_TYPE", f"{entry_where}.kinship.kind 非法", field=f"{entry_where}.kinship.kind"
                )
            if basis not in _KINSHIP_BASES:
                raise AppError(
                    "E_INPUT_TYPE", f"{entry_where}.kinship.basis 非法", field=f"{entry_where}.kinship.basis"
                )
            during = tf_field(kinship, "during", f"{entry_where}.kinship.during")
        elif kinship is not None:
            raise AppError("E_INPUT_TYPE", f"{entry_where}.kinship 须为对象", field=entry_where)
        relations.append(
            {
                "relation_id": relation_id,
                "actor_id": actor_id,
                "subject_id": subject_id,
                "kind": kind,
                "basis": basis,
                "during": during,
                "maintained": tf_field(entry, "maintained", f"{entry_where}.maintained"),
                "cash": parse_money(entry["cash_maintenance"], f"{entry_where}.cash_maintenance")
                if entry.get("cash_maintenance") is not None
                else None,
                "cohabit": tf_field(
                    entry, "cohabit_non_full_consideration", f"{entry_where}.cohabit_non_full_consideration"
                ),
                "sole_care": tf_field(
                    entry, "sole_or_predominant_care", f"{entry_where}.sole_or_predominant_care"
                ),
            }
        )
    relations.sort(key=lambda item: item["relation_id"])

    # financials
    raw_financials = year_obj.get("financials")
    if raw_financials is None:
        raw_financials = {}
    if not isinstance(raw_financials, Mapping):
        raise AppError("E_INPUT_TYPE", f"{where}.financials 须为对象", field=f"{where}.financials")
    financials: dict[str, dict] = {}
    for person_id, raw_finance in raw_financials.items():
        finance_where = f"{where}.financials.{person_id}"
        if not isinstance(raw_finance, Mapping):
            raise AppError("E_INPUT_TYPE", f"{finance_where} 须为对象", field=finance_where)
        reject_extra_keys(raw_finance, _FINANCE_KEYS, finance_where)
        if person_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{finance_where} 引用未注册 subject", field=finance_where
            )
        check_id(person_id, finance_where, ("p",))
        source_classification = raw_finance.get("source_classification")
        if source_classification is not None and not isinstance(source_classification, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{finance_where}.source_classification 须为对象",
                field=f"{finance_where}.source_classification",
            )
        if isinstance(source_classification, Mapping):
            reject_extra_keys(
                source_classification, _SOURCE_CLASS_KEYS, f"{finance_where}.source_classification"
            )
            for key, value in source_classification.items():
                if value not in _SOURCE_CLASS_VALUES:
                    raise AppError(
                        "E_INPUT_TYPE",
                        f"{finance_where}.source_classification.{key} 非法",
                        field=f"{finance_where}.source_classification.{key}",
                    )
        items_raw = raw_finance.get("deduction_items")
        if items_raw is not None and not isinstance(items_raw, list):
            raise AppError(
                "E_INPUT_TYPE",
                f"{finance_where}.deduction_items 须为数组",
                field=f"{finance_where}.deduction_items",
            )
        if isinstance(items_raw, list):
            limit_array(items_raw, f"{finance_where}.deduction_items")
        nai_present = "employment_income_nai" in raw_finance
        mpf_present = "mpf_mandatory_contributions" in raw_finance
        nai = (
            parse_money(raw_finance["employment_income_nai"], f"{finance_where}.employment_income_nai")
            if nai_present
            else Fraction(0)
        )
        mpf = (
            parse_money(
                raw_finance["mpf_mandatory_contributions"],
                f"{finance_where}.mpf_mandatory_contributions",
            )
            if mpf_present
            else Fraction(0)
        )
        ai = tri_from_raw(
            raw_finance.get("has_assessable_salary_income"),
            f"{finance_where}.has_assessable_salary_income",
        )
        if nai > 0 and ai is False:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{finance_where}：正 NAI 与 has_assessable_salary_income=false 并提"
                "（三口径分离，不得由 NAI=0 或正数互相推断）",
                field=f"{finance_where}.has_assessable_salary_income",
            )
        financials[person_id] = {
            "person_id": person_id,
            "nai": nai,
            "nai_present": nai_present,
            "mpf": mpf,
            "mpf_present": mpf_present,
            "ai": ai,
            "all_source": tri_from_raw(
                raw_finance.get("has_income_assessable_under_ordinance"),
                f"{finance_where}.has_income_assessable_under_ordinance",
            ),
            # E3：显式提交（含显式 "unknown"）与省略区分（消费前置需要）。
            "all_source_present": "has_income_assessable_under_ordinance" in raw_finance,
            "property_present": tri_from_raw(
                raw_finance.get("has_assessable_property_income"),
                f"{finance_where}.has_assessable_property_income",
            ),
            "business_present": tri_from_raw(
                raw_finance.get("has_assessable_business_income"),
                f"{finance_where}.has_assessable_business_income",
            ),
            "source_classification": dict(raw_finance.get("source_classification") or {})
            if isinstance(raw_finance.get("source_classification"), Mapping)
            else {},
            "items_raw": raw_finance.get("deduction_items") or [],
            "properties_key_present": "properties" in raw_finance,
            "businesses_key_present": "businesses" in raw_finance,
            "properties": _parse_properties(raw_finance.get("properties"), finance_where),
            "businesses": _parse_businesses(raw_finance.get("businesses"), finance_where),
        }

    # care payments
    raw_care = year_obj.get("care_payments")
    if raw_care is None:
        raw_care = []
    if not isinstance(raw_care, list):
        raise AppError("E_INPUT_TYPE", f"{where}.care_payments 须为数组", field=f"{where}.care_payments")
    limit_array(raw_care, f"{where}.care_payments")
    care_payments: list[dict] = []
    care_ids: set[str] = set()
    for index, entry in enumerate(raw_care):
        entry_where = f"{where}.care_payments[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{entry_where} 须为对象", field=entry_where)
        reject_extra_keys(entry, _CARE_KEYS, entry_where)
        payment_id = entry.get("payment_id")
        if not isinstance(payment_id, str) or not payment_id:
            raise AppError("E_INPUT_MISSING", f"{entry_where} 缺少 payment_id", field=entry_where)
        check_id(payment_id, f"{entry_where}.payment_id", ("e",))
        if payment_id in care_ids:
            raise AppError("E_INPUT_CONTRADICTORY", f"payment_id 重复：{payment_id!r}", field=entry_where)
        care_ids.add(payment_id)
        payer_id = entry.get("payer_id")
        subject_id = entry.get("subject_id")
        if payer_id not in seen_participants:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{entry_where}.payer_id 须引用当年 participant", field=entry_where
            )
        check_id(payer_id, f"{entry_where}.payer_id", ("p",))
        if subject_id not in subjects:
            raise AppError("E_INPUT_CONTRADICTORY", f"{entry_where}.subject_id 未注册", field=entry_where)
        check_id(subject_id, f"{entry_where}.subject_id", ("p", "d"))
        care_from = parse_date(entry.get("care_from"), f"{entry_where}.care_from")
        care_to = parse_date(entry.get("care_to"), f"{entry_where}.care_to")
        if care_from >= care_to:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{entry_where} 须 care_from<care_to", field=entry_where
            )
        home_basis = entry.get("home_basis")
        if home_basis not in _HOME_BASES:
            raise AppError(
                "E_INPUT_TYPE", f"{entry_where}.home_basis 非法", field=f"{entry_where}.home_basis"
            )
        home_status = tf_field(entry, "home_status_in_force", f"{entry_where}.home_status_in_force")
        for start, end, _ in home_status:
            if not (care_from <= start and end <= care_to):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{entry_where}.home_status_in_force 合法域恰为 [care_from,care_to)",
                    field=f"{entry_where}.home_status_in_force",
                )
        paid_on = parse_date(entry.get("paid_on"), f"{entry_where}.paid_on")
        if not (window[0] <= paid_on < window[1]):
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{entry_where}.paid_on 必须落在键年 {year} 内（005 §4.8）",
                field=f"{entry_where}.paid_on",
            )
        care_payments.append(
            {
                "payment_id": payment_id,
                "payer_id": payer_id,
                "subject_id": subject_id,
                "paid_on": paid_on,
                "field": entry_where,
                "amount": parse_money(entry.get("amount"), f"{entry_where}.amount"),
                "care_from": care_from,
                "care_to": care_to,
                "residential": tri_from_raw(
                    entry.get("residential_care_received"),
                    f"{entry_where}.residential_care_received",
                ),
                "paid_home": tri_from_raw(
                    entry.get("paid_to_home_or_agent"), f"{entry_where}.paid_to_home_or_agent"
                ),
                "home_basis": home_basis,
                "home_status": home_status,
            }
        )
    care_index = {payment["payment_id"]: payment for payment in care_payments}

    # claims
    raw_claims = year_obj.get("claims")
    if raw_claims is None:
        raw_claims = []
    if not isinstance(raw_claims, list):
        raise AppError("E_INPUT_TYPE", f"{where}.claims 须为数组", field=f"{where}.claims")
    limit_array(raw_claims, f"{where}.claims")
    claims: list[dict] = []
    claim_ids: set[str] = set()
    ledger_keys: set[tuple[str, str, str]] = set()
    id_fields: list[tuple[str, str, str, str]] = []
    for index, entry in enumerate(raw_claims):
        entry_where = f"{where}.claims[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{entry_where} 须为对象", field=entry_where)
        reject_extra_keys(entry, _CLAIM_KEYS, entry_where)
        claim_id = entry.get("claim_id")
        kind = entry.get("kind")
        claimant_id = entry.get("claimant_id")
        subject_id = entry.get("subject_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise AppError("E_INPUT_MISSING", f"{entry_where} 缺少 claim_id", field=entry_where)
        if claim_id in claim_ids:
            raise AppError("E_INPUT_CONTRADICTORY", f"claim_id 重复：{claim_id!r}", field=entry_where)
        claim_ids.add(claim_id)
        if kind not in _CLAIM_KINDS:
            raise AppError(
                "E_INPUT_EXTRA", f"{entry_where}.kind 不在 ClaimKind 集合内", field=f"{entry_where}.kind"
            )
        if claimant_id not in seen_participants:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{entry_where}.claimant_id 须引用当年 participant",
                field=f"{entry_where}.claimant_id",
            )
        if subject_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{entry_where}.subject_id 未注册", field=f"{entry_where}.subject_id"
            )
        ledger_key = (kind, claimant_id, subject_id)
        if ledger_key in ledger_keys:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{entry_where} 同 (kind,claimant,subject) 重复条目：{ledger_key!r}",
                field=entry_where,
            )
        ledger_keys.add(ledger_key)
        id_fields.append((entry_where, claim_id, claimant_id, subject_id))
        special_raw = entry.get("special_situation")
        if special_raw is True:
            special: Any = True
        elif special_raw == "unknown":
            special = "unknown"
        elif special_raw is False or special_raw is None:
            special = False
        else:
            raise AppError(
                "E_INPUT_TYPE",
                f'{entry_where}.special_situation 三态值须为 true/false/"unknown"',
                field=f"{entry_where}.special_situation",
            )
        claims.append(
            {
                "claim_id": claim_id,
                "kind": kind,
                "claimant_id": claimant_id,
                "subject_id": subject_id,
                "special": special,
            }
        )
    for entry_where, claim_id, claimant_id, subject_id in id_fields:
        check_id(claim_id, f"{entry_where}.claim_id", ("c",))
        check_id(claimant_id, f"{entry_where}.claimant_id", ("p",))
        check_id(subject_id, f"{entry_where}.subject_id", ("p", "d"))
    claims.sort(key=lambda item: item["claim_id"])

    # assessment
    assessment = year_obj.get("assessment")
    if not isinstance(assessment, Mapping):
        raise AppError("E_INPUT_MISSING", f"{where}.assessment 必填", field=f"{where}.assessment")
    reject_extra_keys(assessment, _ASSESSMENT_KEYS, f"{where}.assessment")
    basis = assessment.get("basis")
    if basis not in ("actual", "hypothetical"):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{where}.assessment.basis 须为 actual/hypothetical",
            field=f"{where}.assessment.basis",
        )
    candidates = assessment.get("candidates")
    if (
        not isinstance(candidates, list)
        or not candidates
        or any(not isinstance(candidate, str) for candidate in candidates)
        or len(set(candidates)) != len(candidates)
    ):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{where}.assessment.candidates 须为显式非空无重复集合",
            field=f"{where}.assessment.candidates",
        )
    selection = assessment.get("selection")
    if selection != "best" and selection not in candidates:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{where}.assessment.selection 须为 best 或显式候选",
            field=f"{where}.assessment.selection",
        )
    reference_date = None
    if assessment.get("reference_date") is not None:
        reference_date = parse_date(
            assessment["reference_date"], f"{where}.assessment.reference_date"
        )
    actual = assessment.get("actual")
    if actual is not None and not isinstance(actual, Mapping):
        raise AppError("E_INPUT_TYPE", f"{where}.assessment.actual 须为对象", field=f"{where}.assessment.actual")
    actual = actual or {}
    reject_extra_keys(actual, _ACTUAL_KEYS, f"{where}.assessment.actual")
    history = actual.get("history")
    if history is not None and history not in _HISTORY_VALUES:
        raise AppError(
            "E_INPUT_TYPE",
            f"{where}.assessment.actual.history 非法",
            field=f"{where}.assessment.actual.history",
        )
    pa_entries = _parse_pa_entries(actual.get("pa"), f"{where}.assessment.actual.pa", seen_participants)
    actual_nomination = _parse_nomination(
        actual.get("salary_joint_nomination"),
        f"{where}.assessment.actual.salary_joint_nomination",
        seen_participants,
    )
    hypothetical_nomination = _parse_nomination(
        assessment.get("hypothetical_salary_joint_nomination"),
        f"{where}.assessment.hypothetical_salary_joint_nomination",
        seen_participants,
    )
    provisional_paid = (
        parse_money(year_obj.get("provisional_paid"), f"{where}.provisional_paid")
        if year_obj.get("provisional_paid") is not None
        else None
    )

    # manual_issues（封闭条目：scope/id/code）
    raw_issues = year_obj.get("manual_issues")
    if raw_issues is None:
        raw_issues = []
    if not isinstance(raw_issues, list):
        raise AppError("E_INPUT_TYPE", f"{where}.manual_issues 须为数组", field=f"{where}.manual_issues")
    limit_array(raw_issues, f"{where}.manual_issues")
    manual_issues: list[dict] = []
    for index, issue in enumerate(raw_issues):
        issue_where = f"{where}.manual_issues[{index}]"
        if not isinstance(issue, Mapping):
            raise AppError("E_INPUT_TYPE", f"{issue_where} 须为对象", field=issue_where)
        reject_extra_keys(issue, _MANUAL_ISSUE_KEYS, issue_where)
        if issue.get("scope") not in _MANUAL_SCOPES:
            raise AppError("E_INPUT_TYPE", f"{issue_where}.scope 非法", field=f"{issue_where}.scope")
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            raise AppError("E_INPUT_MISSING", f"{issue_where} 缺少 id", field=issue_where)
        check_id(issue_id, f"{issue_where}.id", ("p", "d", "r", "h", "b", "c", "e", "a"))
        if issue.get("code") not in _MANUAL_CODES:
            raise AppError("E_INPUT_TYPE", f"{issue_where}.code 非法", field=f"{issue_where}.code")
        manual_issues.append(
            {
                "scope": issue["scope"],
                "id": issue_id,
                "code": issue["code"],
                "index": index,
            }
        )

    # TF 允许域：默认键年窗口；仅列明字段可额外覆盖参考日/选择日一天区间（§7）。
    # 域核验仅对目标年显式对象执行；下一年对象由结构与 ledger 对账兜底
    # （协议侧冻结夹具沿用本年窗口日期复制，不因域核验拒绝破坏 next 抑制语义）。
    extension_days: set[date] = set()
    if reference_date is not None:
        extension_days.add(reference_date)
    for entry in pa_entries.values():
        if entry["mode"] in ("individual", "joint") and entry["date"] is not None:
            extension_days.add(entry["date"])
    for subject_id, facts in subject_facts.items():
        for field_name, intervals in facts.items():
            if field_name == "death_date":
                continue
            _validate_tf_domain(
                intervals,
                f"{where}.subject_facts.{subject_id}.{field_name}",
                window,
                extension_days,
                extendable=field_name in ("ordinarily_resident_hk", "both_parents_dead"),
            )
    _validate_tf_domain(
        [(interval[0], interval[1], interval[2]) for interval in marriage],
        f"{where}.household.marriage",
        window,
        extension_days,
        extendable=True,
    )
    _validate_tf_domain(
        living_apart,
        f"{where}.household.living_apart",
        window,
        extension_days,
        extendable=True,
    )
    for relation in relations:
        for field_name in ("during", "maintained", "cohabit", "sole_care"):
            _validate_tf_domain(
                relation[field_name],
                f"{where}.relations.{relation['relation_id']}.{field_name}",
                window,
                extension_days,
            )

    return {
        "year": year,
        "window": window,
        "participants": participants,
        "self_id": self_id,
        "spouse_id": spouse_id,
        "household": {
            "marriage": marriage,
            "living_apart": living_apart,
            "child_nomination": nomination,
        },
        "subject_facts": subject_facts,
        "relations": relations,
        "financials": financials,
        "care_payments": care_payments,
        "care_index": care_index,
        "claims": claims,
        "manual_issues": manual_issues,
        "assessment": {
            "basis": basis,
            "candidates": list(candidates),
            "selection": selection,
            "reference_date": reference_date,
            "actual": dict(actual),
            "history": history,
            "pa_entries": pa_entries,
            "actual_nomination": actual_nomination,
            "hypothetical_nomination": hypothetical_nomination,
            "salary_joint_elected": tri_from_raw(
                actual.get("salary_joint_elected"),
                f"{where}.assessment.actual.salary_joint_elected",
            ),
            "salary_joint_assessed": tri_from_raw(
                actual.get("salary_joint_assessed"),
                f"{where}.assessment.actual.salary_joint_assessed",
            ),
        },
        "provisional_paid": provisional_paid,
    }


def _parse_properties(raw: object, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", f"{where}.properties 须为数组", field=f"{where}.properties")
    limit_array(raw, f"{where}.properties")
    properties: list[dict] = []
    seen: set[str] = set()
    for index, prop in enumerate(raw):
        prop_where = f"{where}.properties[{index}]"
        if not isinstance(prop, Mapping):
            raise AppError("E_INPUT_TYPE", f"{prop_where} 须为对象", field=prop_where)
        reject_extra_keys(prop, _PROPERTY_KEYS, prop_where)
        property_id = prop.get("property_id")
        if not isinstance(property_id, str) or not property_id:
            raise AppError("E_INPUT_MISSING", f"{prop_where} 缺少 property_id", field=prop_where)
        check_id(property_id, f"{prop_where}.property_id", ("h",))
        if property_id in seen:
            raise AppError("E_INPUT_CONTRADICTORY", f"property_id 重复：{property_id!r}", field=prop_where)
        seen.add(property_id)
        rates_raw = prop.get("rates_paid_by_owner")
        if rates_raw is None:
            rates_raw = {"amount": "0", "agreed": False, "actually_paid": False}
        if not isinstance(rates_raw, Mapping):
            raise AppError(
                "E_INPUT_TYPE", f"{prop_where}.rates_paid_by_owner 须为对象", field=prop_where
            )
        reject_extra_keys(rates_raw, _RATES_KEYS, f"{prop_where}.rates_paid_by_owner")
        amount = parse_money(rates_raw.get("amount", "0"), f"{prop_where}.rates_paid_by_owner.amount")
        agreed = tri_from_raw(rates_raw.get("agreed"), f"{prop_where}.rates_paid_by_owner.agreed")
        actually_paid = tri_from_raw(
            rates_raw.get("actually_paid"), f"{prop_where}.rates_paid_by_owner.actually_paid"
        )
        properties.append(
            {
                "property_id": property_id,
                "share": parse_ratio(prop.get("share"), f"{prop_where}.share"),
                "rent": parse_money(prop.get("rent_received"), f"{prop_where}.rent_received"),
                # E6/§4.6：保留已声明差饷原始三元组（hID 共同原始值对账用；
                # 份额/个人利息差异属合法）。
                "rates_raw": (amount, agreed, actually_paid),
                "rates": amount if (agreed is True and actually_paid is True) else Fraction(0),
                "rates_unknown": amount > 0 and (agreed is None or actually_paid is None),
                "irrecoverable": parse_money(
                    prop.get("irrecoverable_rent", "0"), f"{prop_where}.irrecoverable_rent"
                ),
                "deposits": parse_money(
                    prop.get("deposit_offsets", "0"), f"{prop_where}.deposit_offsets"
                ),
                "interest": parse_money(
                    prop.get("mortgage_interest", "0"), f"{prop_where}.mortgage_interest"
                ),
            }
        )
    return properties


def _parse_businesses(raw: object, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", f"{where}.businesses 须为数组", field=f"{where}.businesses")
    limit_array(raw, f"{where}.businesses")
    businesses: list[dict] = []
    seen: set[str] = set()
    for index, business in enumerate(raw):
        business_where = f"{where}.businesses[{index}]"
        if not isinstance(business, Mapping):
            raise AppError("E_INPUT_TYPE", f"{business_where} 须为对象", field=business_where)
        reject_extra_keys(business, _BUSINESS_KEYS, business_where)
        business_id = business.get("business_id")
        if not isinstance(business_id, str) or not business_id:
            raise AppError("E_INPUT_MISSING", f"{business_where} 缺少 business_id", field=business_where)
        check_id(business_id, f"{business_where}.business_id", ("b",))
        if business_id in seen:
            raise AppError("E_INPUT_CONTRADICTORY", f"business_id 重复：{business_id!r}", field=business_where)
        seen.add(business_id)
        kind = business.get("entity_kind")
        if kind not in ("sole_proprietorship", "partnership"):
            raise AppError(
                "E_INPUT_TYPE",
                f"{business_where}.entity_kind 必须为 sole_proprietorship／partnership",
                field=f"{business_where}.entity_kind",
            )
        businesses.append(
            {
                "business_id": business_id,
                "kind": kind,
                "profit": parse_money(business.get("assessable_profit"), f"{business_where}.assessable_profit"),
                "share": parse_ratio(business.get("share"), f"{business_where}.share"),
            }
        )
    return businesses


def _parse_pa_entries(raw: object, where: str, participants: set[str]) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{where} 须为稀疏对象映射", field=where)
    entries: dict[str, dict] = {}
    for person_id, entry in raw.items():
        entry_where = f"{where}.{person_id}"
        if person_id not in participants:
            raise AppError("E_INPUT_EXTRA", f"{entry_where} 键须为当年 participant", field=entry_where)
        check_id(person_id, entry_where, ("p",))
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{entry_where} 须为对象", field=entry_where)
        reject_extra_keys(entry, _PA_ENTRY_KEYS, entry_where)
        mode = entry.get("mode")
        if mode not in _PA_MODES:
            raise AppError("E_INPUT_TYPE", f"{entry_where}.mode 非法", field=f"{entry_where}.mode")
        date_value = None
        if "date" in entry and entry.get("date") is not None:
            if mode == "none":
                raise AppError(
                    "E_INPUT_CONTRADICTORY", f"{entry_where} mode=none 禁止 date", field=entry_where
                )
            date_value = parse_date(entry["date"], f"{entry_where}.date")
        entries[person_id] = {"mode": mode, "date": date_value}
    # joint 配对一致性：已提供的 mode/日期冲突 → 拒绝；配对缺失＝需补事实（不在此拒绝）
    for person_id, entry in entries.items():
        if entry["mode"] != "joint":
            continue
        for other_id, other in entries.items():
            if other_id == person_id:
                continue
            if other["mode"] in ("none", "individual"):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}：{person_id} 为 joint 而 {other_id} 为 {other['mode']}（配对冲突）",
                    field=where,
                )
            if (
                other["mode"] == "joint"
                and entry["date"] is not None
                and other["date"] is not None
                and entry["date"] != other["date"]
            ):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}：joint 配对日期不一致",
                    field=where,
                )
    return entries


def _parse_nomination(raw: object, where: str, participants: set[str]) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
    reject_extra_keys(raw, _NOMINATION_KEYS, where)
    status = raw.get("status")
    if status not in ("none", "named", "unknown", "disputed"):
        raise AppError("E_INPUT_TYPE", f"{where}.status 非法", field=f"{where}.status")
    person_id = raw.get("person_id")
    if status == "named":
        if not isinstance(person_id, str) or person_id not in participants:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{where} named 须引用当年两名 participant 之一",
                field=f"{where}.person_id",
            )
        check_id(person_id, f"{where}.person_id", ("p",))
    elif person_id is not None:
        raise AppError(
            "E_INPUT_CONTRADICTORY", f"{where} 非 named 须 person_id=null", field=f"{where}.person_id"
        )
    return {"status": status, "person_id": person_id}


# ---------------------------------------------------------------------------
# 婚姻窗口（S1–S7 有限形态）
# ---------------------------------------------------------------------------


def analyze_marriage(y: dict) -> dict:
    """判定 S6 普通婚姻窗口形态（本阶段自动处理边界，非宣称法例要求全年）。

    - 全域明确 unmarried → unmarried；
    - 全域同一配偶 married 且全部婚姻期间明确 not apart → ordinary；
    - 一次明确 unmarried→同一配偶 married、婚后至年末明确 not apart → ordinary
      （s.10(5) 全年合并，不按月缩减）；
    - 顺序配偶变化 → S7_SEQUENTIAL_SPOUSES；其他分居/缺口/多段形态 → S7；
    - 全 unknown／空 TF → unknown（needs_input，不默认 single）。
    """
    window = y["window"]
    intervals = y["household"]["marriage"]
    if not intervals:
        return {"state": "unknown", "reason": "MARRIAGE_UNKNOWN"}
    runs: list[list[Any]] = []
    for day in _days(window):
        value, spouse = marriage_at(intervals, day)
        if value is None:
            return {"state": "unknown", "reason": "MARRIAGE_UNKNOWN"}
        if runs and runs[-1][0] == value and runs[-1][1] == spouse:
            runs[-1][3] = day + timedelta(days=1)
        else:
            runs.append([value, spouse, day, day + timedelta(days=1)])
    married_runs = [run for run in runs if run[0] == "married"]
    if not married_runs:
        return {"state": "unmarried", "reason": None}
    spouse_ids = {run[1] for run in married_runs}
    if len(spouse_ids) > 1:
        return {"state": "sequential", "reason": "S7_SEQUENTIAL_SPOUSES"}
    apart = y["household"]["living_apart"]
    apart_unknown = False
    for run in married_runs:
        for day in _days((run[2], run[3])):
            value = tf_at(apart, day)
            if value is None:
                apart_unknown = True
            elif value is True:
                return {"state": "s7", "reason": "S7_LIVING_APART"}
    if apart_unknown:
        return {"state": "s7", "reason": "S7_MARRIAGE_WINDOW_UNKNOWN"}
    married_started = False
    for run in runs:
        if run[0] == "married":
            married_started = True
        elif married_started:
            return {"state": "s7", "reason": "S7_MARRIAGE_WINDOW"}
    return {"state": "ordinary", "reason": None, "spouse_id": next(iter(spouse_ids))}


# ---------------------------------------------------------------------------
# 长者住宿照顾（s.26D 原始事实判定；ERCE 扣除与 s.31A 替代基础共用）
# ---------------------------------------------------------------------------


def nonseparation_tri(y: dict) -> Any:
    """婚姻非分居三态（s.31(3) 整组提名与 s.26D「本人或非分居配偶」共用）。

    - True：本年内每个已婚日都已证 not living_apart（无已婚日视为不涉分居）；
    - False：存在已婚日已证 living_apart=true（已证分居）；
    - None：婚期/分居事实未知（不得默认非分居，也不判矛盾）。
    C4/R-E5-C1：婚期未知（None）不得早退——继续扫描全窗，任一已婚日已证
    living_apart=true 仍返回 False（已证分居优先于他日未决）。
    """
    marriage = y["household"]["marriage"]
    apart = y["household"]["living_apart"]
    unknown = False
    for day in _days(y["window"]):
        value, _spouse = marriage_at(marriage, day)
        if value is None:
            unknown = True
            continue
        if value != "married":
            continue
        apart_value = tf_at(apart, day)
        if apart_value is True:
            return False
        if apart_value is None:
            unknown = True
    return None if unknown else True


def partner_of(y: Mapping, person_id: str) -> str | None:
    """本人对应的配偶 participant（未注册/非夫妻 → None）。"""
    if person_id == y.get("self_id"):
        return y.get("spouse_id")
    if y.get("spouse_id") is not None and person_id == y.get("spouse_id"):
        return y.get("self_id")
    return None


def s26d_payment_facts(
    payment: Mapping,
    *,
    y: dict,
    subjects: Mapping[str, date | None],
    window: tuple[date, date],
    claimant_id: str,
    partner_id: str | None,
) -> dict:
    """单一付款的 s.26D 原始事实判定（ERCE 扣除与 s.31A 替代基础共用）。

    付款 YA＝window（付款所在 Year 自身窗口）：年龄/DAA 按付款 YA 内任一时点
    （s.26D(1)）；home_status_in_force 按 care 区间求值（005 §4.8）。关系须为
    本人或配偶的 parent/grandparent（已核 during），付款人须为本人或非分居配偶。

    返回 {"value": True/False/None, "field": 未决/否定定位, "label": 原因标签}。
    """
    year = y["year"]
    where = payment.get("field") or f"years.{year}.care_payments"
    if payment["residential"] is not True:
        return {
            "value": payment["residential"],
            "field": f"{where}.residential_care_received",
            "label": "residential_care_received 未成立/未确定",
        }
    if payment["paid_home"] is not True:
        return {
            "value": payment["paid_home"],
            "field": f"{where}.paid_to_home_or_agent",
            "label": "paid_to_home_or_agent 未成立/未确定",
        }
    home_basis = payment["home_basis"]
    if home_basis not in _QUALIFIED_HOME_BASES:
        if home_basis in (None, "other", "unknown"):
            return {
                "value": None,
                "field": f"{where}.home_basis",
                "label": "home_basis 非法定院舍类别（other/unknown 不自动合资格）",
            }
        return {
            "value": False,
            "field": f"{where}.home_basis",
            "label": "home_basis 不属已批准法定院舍类别",
        }
    care_window = (payment["care_from"], payment["care_to"])
    status = all_over(lambda day: tf_at(payment["home_status"], day), care_window)
    if status is not True:
        return {
            "value": status,
            "field": f"{where}.home_status_in_force",
            "label": "home_status_in_force 于 care 区间未成立/未确定",
        }
    subject_id = payment["subject_id"]
    birth = subjects.get(subject_id)
    age60 = (
        any(day >= add_years(birth, 60) for day in _days(window))
        if birth is not None
        else None
    )
    daa_intervals = (
        y["subject_facts"].get(subject_id, {}).get("govt_disability_allowance", [])
    )
    daa = any_over(lambda day: tf_at(daa_intervals, day), window)
    age_or_daa = tri_or(age60, daa)
    if age_or_daa is not True:
        return {
            "value": age_or_daa,
            "field": (
                f"years.{year}.subject_facts.{subject_id}.govt_disability_allowance"
            ),
            "label": "付款 YA 未达 60 且 DAA 资格未成立/未确定",
        }
    def relation_path(actor_id: str | None) -> dict:
        """单个 actor 对长者的直接关系路径（005 §4.5:170–172）。

        kinship 只表达 actor 与 subject 的直接关系；配偶亲属经当年婚姻连接
        （由调用方按 partner_of 取 actor）。返回 {value, field, label}：三态
        True/False/None 均按该 actor 自身关系事实独立求值，未提交＝None。
        """
        if actor_id is None:
            return {
                "value": None,
                "field": f"years.{year}.relations",
                "label": "本人/配偶与该长者的亲属关系事实缺失",
            }
        relation = None
        for candidate in y["relations"]:
            if (
                candidate["subject_id"] == subject_id
                and candidate["actor_id"] == actor_id
            ):
                relation = candidate
                break
        if relation is None:
            return {
                "value": None,
                "field": f"years.{year}.relations",
                "label": "本人/配偶与该长者的亲属关系事实缺失",
            }
        kind = relation["kind"]
        relation_field = f"years.{year}.relations.{relation['relation_id']}.kinship"
        if kind not in ("parent", "grandparent"):
            # 005 §6:468：kinship 键缺失解析为 None，属「未提供」而非 false；
            # 仅显式 "none"（或其他已证非父/母/祖父母类别）才是 false 事实。
            if kind is None or kind == "unknown":
                return {
                    "value": None,
                    "field": f"{relation_field}.kind",
                    "label": "亲属关系类别未确定",
                }
            return {
                "value": False,
                "field": f"{relation_field}.kind",
                "label": "亲属关系类别不属父/母/祖父母",
            }
        if kinship_unverified(kind, relation["basis"]):
            return {
                "value": None,
                "field": f"{relation_field}.basis",
                "label": "亲属关系组合未在普通已核映射内（窄 manual）",
            }
        during = any_over(lambda day: tf_at(relation["during"], day), window)
        if during is not True:
            return {
                "value": during,
                "field": f"{relation_field}.during",
                "label": "关系期间未成立/未确定",
            }
        return {"value": True, "field": where, "label": ""}

    def path_with_nonseparation(path: dict) -> dict:
        """配偶涉入路径须已证非分居（s.26D：本人或非分居配偶）。

        路径值与非分居三态 AND；关系事实本身为 false 时保持关系定位
        （不因分居改判），未决优先定位分居/关系事实。
        """
        nonsep = nonseparation_tri(y)
        if path["value"] is False:
            return path
        value = tri_and(path["value"], nonsep)
        if value is True:
            return {"value": True, "field": where, "label": ""}
        if value is False:
            return {
                "value": False,
                "field": f"years.{year}.household.living_apart",
                "label": "已证分居：配偶付款/配偶亲属不适用 s.26D",
            }
        if nonsep is None:
            return {
                "value": None,
                "field": f"years.{year}.household.living_apart",
                "label": "配偶付款/配偶亲属须已证非分居：分居事实未确定",
            }
        return path

    claimant_path = relation_path(claimant_id)
    # 配偶亲属路径：仅当存在当年配偶时按婚姻连接追加（013/005 §4.5:171）。
    spouse_path = relation_path(partner_id) if partner_id is not None else None
    payer = payment["payer_id"]
    if payer != claimant_id and (partner_id is None or payer != partner_id):
        return {
            "value": False,
            "field": f"{where}.payer_id",
            "label": "付款人非本人或配偶",
        }
    # 配偶付款时 claimant 自己的关系路径亦须非分居；配偶亲属路径本身按
    # 非分居约束。两条路径独立求值后三态 OR（005 §7:496）：任一路径 true
    # → true；全 false → false；否则 unknown；显式 none 不得否定其他已
    # 成立路径，缺失/未决不得自动授予。
    claimant_value = claimant_path
    if partner_id is not None and payer == partner_id:
        claimant_value = path_with_nonseparation(claimant_path)
    spouse_value = (
        path_with_nonseparation(spouse_path) if spouse_path is not None else None
    )
    paths = [path for path in (claimant_value, spouse_value) if path is not None]
    combined = tri_or(*[path["value"] for path in paths])
    if combined is True:
        return {"value": True, "field": where, "label": ""}
    if combined is None:
        return next(path for path in paths if path["value"] is None)
    return next(path for path in paths if path["value"] is False)


def collect_input_gates(y: dict, year: str) -> dict:
    """E3：逐人/逐组件的消费前置未决项（presence≠零、来源分类、差饷、人工边界）。

    按候选实际依赖消费（不整类禁算、不默认已确认、不从 NAI/净额零推存在性）：

    - `salary_questions`：阻断薪俸/PA 评税单位的未决（NAI/MPF 缺失、薪俸来源分类
      缺失/unknown/manual、显式全来源 unknown、person 事项、组件级 source 事项
      按候选人自身来源声明判定）；
    - `component_questions`：阻断物业/业务组件的未决（来源分类缺失/unknown/manual、
      已确认来源但组件数组省略≠[]、差饷 amount>0 而 agreed/actually_paid unknown、
      property/business 事项定位对应组件）。

    questions 元素均携带 year 与 subject_id，便于定位（005 §13）。
    """
    salary_questions: list[dict] = []
    component_questions: list[dict] = []
    issues = y.get("manual_issues") or []
    # F3：按当年 participants 遍历（非仅已提交 financials）。整人 finance
    # 缺失＝该人 NAI/MPF 活动事实缺失（缺省≠零）；不向 y["financials"] 补
    # pID/零值，保留 canonical 输入的原始缺席事实。
    for participant in y.get("participants") or []:
        person_id = participant["person_id"]
        finance = y["financials"].get(person_id) or {}
        where = f"years.{year}.financials.{person_id}"
        if not finance.get("nai_present", False):
            salary_questions.append(
                make_question(
                    f"{where}.employment_income_nai",
                    year,
                    "薪俸应评税入息缺失＝未提供（≠0）：请提交该年度 NAI 后重算。",
                    subject_id=person_id,
                )
            )
        if not finance.get("mpf_present", False):
            salary_questions.append(
                make_question(
                    f"{where}.mpf_mandatory_contributions",
                    year,
                    "MPF 强制性供款缺失＝未提供（≠0）：请提交该年度供款额"
                    "（显式 0 与缺失不同）后重算。",
                    subject_id=person_id,
                )
            )
        classification = finance.get("source_classification") or {}
        salary_class = classification.get("salary")
        # F3：来源分类缺/unknown/manual 在**活动薪俸路径**下阻断。活动性不得
        # 只看 AI=true：正 NAI 已进入薪俸计算时同样必须消费来源确认（单身
        # AI 缺席可合法，但不豁免来源）；AI=false 且 NAI=0 的非活动成员不追问。
        salary_active = finance.get("ai") is True or finance.get("nai", 0) > 0
        if salary_active and salary_class != "confirmed_in_scope":
            salary_questions.append(
                make_question(
                    f"{where}.source_classification.salary",
                    year,
                    "薪俸来源分类缺失/unknown/manual：相关评税路径未决，不得默认"
                    "已确认来源计算。",
                    subject_id=person_id,
                )
            )
        if finance.get("all_source_present") and finance.get("all_source") is None:
            salary_questions.append(
                make_question(
                    f"{where}.has_income_assessable_under_ordinance",
                    year,
                    "全来源存在性为显式 unknown：请确认该人在本年度是否有"
                    "《税务条例》下应予评税的入息（未知不得改写为 false/true）。",
                    subject_id=person_id,
                )
            )
        for issue in issues:
            if issue["scope"] == "person" and issue["id"] == person_id:
                salary_questions.append(
                    make_question(
                        where,
                        year,
                        f"人工边界事项（scope=person, id={person_id}, "
                        f"code={issue['code']}）：该人的来源/评税路径需人工确认，"
                        "不否定独立于来源路径的免税额资格。",
                        subject_id=person_id,
                    )
                )
        for kind, key, presence_field, key_present_field, id_field in (
            ("property", "properties", "property_present",
             "properties_key_present", "property_id"),
            ("business", "businesses", "business_present",
             "businesses_key_present", "business_id"),
        ):
            classification_value = classification.get(kind)
            entries = finance.get(key) or []
            presence = finance.get(presence_field)
            # F3：组件“活动”判定不要求先声明 presence——已提交非空组件、已声明
            # 存在、或来源已确认均须检查；来源确认而数组省略不得当作 []。
            active = (
                bool(entries)
                or presence is True
                or classification_value == "confirmed_in_scope"
            )
            if not active:
                continue
            if classification_value != "confirmed_in_scope":
                component_questions.append(
                    make_question(
                        f"{where}.source_classification.{kind}",
                        year,
                        f"{kind} 来源分类缺失/unknown/manual：依赖该组件的候选未决，"
                        "不得默认已确认来源计算。",
                        subject_id=person_id,
                    )
                )
            if (
                classification_value == "confirmed_in_scope"
                and not finance.get(key_present_field, False)
            ):
                component_questions.append(
                    make_question(
                        f"{where}.{key}",
                        year,
                        f"已确认 {kind} 来源存在但 {key} 数组省略（省略≠[]）："
                        "请补全组件条目或显式提交空数组。",
                        subject_id=person_id,
                    )
                )
            for issue in issues:
                if issue["scope"] != kind:
                    continue
                if any(entry.get(id_field) == issue["id"] for entry in entries):
                    component_questions.append(
                        make_question(
                            f"{where}.{key}",
                            year,
                            f"人工边界事项（scope={kind}, id={issue['id']}, "
                            f"code={issue['code']}）：依赖该组件的候选未决。",
                            subject_id=person_id,
                        )
                    )
            if kind == "property":
                for entry in entries:
                    if entry.get("rates_unknown"):
                        component_questions.append(
                            make_question(
                                f"{where}.{key}.{entry['property_id']}"
                                ".rates_paid_by_owner",
                                year,
                                "差饷 amount>0 而 agreed/actually_paid 存在 unknown："
                                "相关物业路径未决（不静默按不扣差饷继续）。",
                                subject_id=person_id,
                            )
                        )
    return {
        "salary_questions": salary_questions,
        "component_questions": component_questions,
    }


def resolve_financials(
    bundle: Mapping,
    y: dict,
    subjects: Mapping[str, date | None],
    questions: list[dict],
) -> None:
    """Part 4A 扣除解析（deduction_items 唯一入口；ERCE 桥接；逐条 decision）。"""
    year = y["year"]
    window = y["window"]
    data = bundle.get("data") if isinstance(bundle, Mapping) else None
    salaries_data = data.get("salaries") if isinstance(data, Mapping) else None
    registry = salaries_data.get("deductions") if isinstance(salaries_data, Mapping) else None
    if not isinstance(registry, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "规则数据缺少 salaries.deductions（在册扣除枚举）；拒算。",
            field="data.salaries.deductions",
        )
    care_index = y["care_index"]
    used_payment_ids: set[str] = set()
    care_cap_used: dict[tuple[str, str], Fraction] = {}
    care_elder_owner: dict[str, str] = {}
    for person_id, finance in y["financials"].items():
        partner_id = partner_of(y, person_id)
        items = finance["items_raw"]
        if not isinstance(items, list):
            raise AppError(
                "E_INPUT_TYPE",
                f"years.{year}.financials.{person_id}.deduction_items 须为数组",
                field=f"years.{year}.financials.{person_id}.deduction_items",
            )
        extra = Fraction(0)
        resolved: list[dict] = []
        decisions: list[dict] = []
        for index, item in enumerate(items):
            where = f"years.{year}.financials.{person_id}.deduction_items[{index}]"
            if not isinstance(item, Mapping):
                raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
            reject_extra_keys(item, _DEDUCTION_ITEM_KEYS, where)
            item_type = item.get("item_type")
            if not isinstance(item_type, str) or not item_type:
                raise AppError("E_INPUT_TYPE", f"{where}.item_type 须为非空字符串", field=where)
            amount_raw = item.get("amount")
            if amount_raw is None:
                raise AppError("E_INPUT_MISSING", f"{where} 缺少 amount", field=where)
            amount = parse_money(amount_raw, f"{where}.amount")
            eligibility = item.get("eligibility")
            if isinstance(eligibility, Mapping):
                reject_extra_keys(eligibility, ("value",), f"{where}.eligibility")
                value = eligibility.get("value")
            else:
                value = eligibility
            if value not in (True, False, "unknown", None):
                raise AppError(
                    "E_INPUT_TYPE",
                    f'{where}.eligibility 三态值须为 true/false/"unknown"',
                    field=where,
                )
            # E3：定位项 subject——优先 item.subject_id，否则取所引付款的受养人
            # （eligibility 缺失/unknown 的追问仍须精确 subject_id；005 §13）。
            question_subject = item.get("subject_id")
            if question_subject is None:
                raw_payment_ids = item.get("payment_ids")
                if isinstance(raw_payment_ids, list):
                    for raw_payment_id in raw_payment_ids:
                        referenced = care_index.get(raw_payment_id)
                        if referenced is not None:
                            question_subject = referenced["subject_id"]
                            break
            rule = registry.get(item_type)
            if rule is None:
                if amount > 0:
                    raise AppError(
                        "E_DEDUCTION_UNSUPPORTED",
                        f"扣除项 {item_type} 不在薪俸税可扣除附表内（逐项枚举，无 "
                        "catch-all）；正数申索须拒算受影响税项。",
                        field=where,
                    )
                if value == "unknown":
                    questions.append(
                        make_question(
                            f"{where}.eligibility",
                            year,
                            f"扣除项 {item_type} 不在可扣除附表内，而其资格状态为"
                            "「未知」：请确认是否申索该项（零金额不得吞掉三态）。",
                            subject_id=question_subject,
                        )
                    )
                decisions.append(
                    {
                        "person_id": person_id,
                        "item_type": item_type,
                        "requested": None,
                        "allowed": "0",
                        "subject_id": item.get("subject_id"),
                    }
                )
                continue
            if value is True:
                pass
            elif value is False:
                decisions.append(
                    {
                        "person_id": person_id,
                        "item_type": item_type,
                        "requested": False,
                        "allowed": "0",
                        "subject_id": item.get("subject_id"),
                    }
                )
                continue
            else:
                questions.append(
                    make_question(
                        f"{where}.eligibility",
                        year,
                        f"扣除项 {item_type}（{rule.get('label_zh') or item_type}）的资格"
                        "状态尚未确认：请确认是否符合对应附表的申索资格"
                        "（unknown 不得默认可扣，金额正数不豁免）。",
                        subject_id=question_subject,
                    )
                )
                decisions.append(
                    {
                        "person_id": person_id,
                        "item_type": item_type,
                        "requested": None,
                        "allowed": "0",
                        "subject_id": item.get("subject_id"),
                    }
                )
                continue
            payment_ids = item.get("payment_ids")
            subject_id = item.get("subject_id")
            if not isinstance(payment_ids, list) or not payment_ids:
                questions.append(
                    make_question(
                        f"{where}.payment_ids",
                        year,
                        f"扣除项 {item_type} 缺少可核对的付款引用（payment_ids）；"
                        "不静默按金额授予。",
                    )
                )
                continue
            payments: list[Mapping] = []
            missing_reference = False
            for payment_id in payment_ids:
                payment = (
                    care_index.get(payment_id)
                    if isinstance(payment_id, str)
                    else None
                )
                if payment is None or (
                    payment["payer_id"] != person_id
                    and payment["payer_id"] != partner_id
                ):
                    missing_reference = True
                    break
                payments.append(payment)
            if missing_reference:
                questions.append(
                    make_question(
                        f"{where}.payment_ids",
                        year,
                        f"扣除项 {item_type} 的付款引用不存在或非本人/配偶支付：请修正"
                        "payment_ids（不得静默按金额授予）。",
                    )
                )
                continue
            duplicate_within = sorted(
                {
                    payment_id
                    for payment_id in payment_ids
                    if isinstance(payment_id, str)
                    and payment_ids.count(payment_id) > 1
                }
            )
            if duplicate_within:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where} 同一扣除项重复引用同一付款 ID {duplicate_within!r}"
                    "（005 §6.2 对账：不得按两次计入）",
                    field=where,
                )
            if subject_id is not None and any(
                payment["subject_id"] != subject_id for payment in payments
            ):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where} 声明的受养人 subject_id 与所引付款的受养人不一致"
                    "（subject 一致性属同一对账，不得计入）",
                    field=where,
                )
            for elder in sorted({payment["subject_id"] for payment in payments}):
                other = care_elder_owner.get(elder)
                if other is not None and other != person_id:
                    raise AppError(
                        "E_INPUT_CONTRADICTORY",
                        f"同一长者 {elder} 的长者住宿照顾付款跨人重复进入同一评税"
                        "场景（005 §6.2；s.26D(4)(a)）：无争议事实必须拒绝，"
                        "不得重复计入。",
                        field=where,
                    )
                care_elder_owner[elder] = person_id
            duplicated = [
                payment["payment_id"]
                for payment in payments
                if payment["payment_id"] in used_payment_ids
            ]
            if duplicated:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where} 的付款 ID {duplicated!r} 重复进入同一评税场景扣除"
                    "（同 payment ID 不得重复扣除）",
                    field=where,
                )
            used_payment_ids.update(payment["payment_id"] for payment in payments)
            payment_total = sum((payment["amount"] for payment in payments), Fraction(0))
            if payment_total != amount:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.amount 与引用付款合计不一致（{amount} ≠ {payment_total}）",
                    field=where,
                )
            facts_list = [
                s26d_payment_facts(
                    payment,
                    y=y,
                    subjects=subjects,
                    window=window,
                    claimant_id=person_id,
                    partner_id=partner_id,
                )
                for payment in payments
            ]
            denied_facts = [fact for fact in facts_list if fact["value"] is False]
            unresolved_facts = [fact for fact in facts_list if fact["value"] is None]
            if denied_facts:
                decisions.append(
                    {
                        "person_id": person_id,
                        "item_type": item_type,
                        "requested": True,
                        "allowed": "0",
                        "subject_id": subject_id,
                        "reason_codes": ["RAW_FACTS_NOT_MET"],
                    }
                )
                continue
            if unresolved_facts:
                first = unresolved_facts[0]
                questions.append(
                    make_question(
                        first["field"],
                        year,
                        f"扣除项 {item_type} 的长者住宿照顾原始事实未确定"
                        f"（{first['label']}）：请补全后重算。",
                        subject_id=subject_id if subject_id is not None else question_subject,
                    )
                )
                decisions.append(
                    {
                        "person_id": person_id,
                        "item_type": item_type,
                        "requested": True,
                        "allowed": "0",
                        "subject_id": subject_id,
                        "reason_codes": ["RAW_FACTS_UNKNOWN"],
                    }
                )
                continue
            cap_text = (rule.get("caps") or {}).get(year)
            if not isinstance(cap_text, str):
                raise AppError(
                    "E_RULES_STATE_UNVERIFIABLE",
                    f"规则束缺少 {item_type} 在 {year} 的扣除上限（附表条目）；拒算。",
                    field=where,
                )
            try:
                cap = Fraction(int(Decimal(cap_text)))
            except (ArithmeticError, ValueError):
                raise AppError(
                    "E_RULES_STATE_UNVERIFIABLE",
                    f"规则束的 {item_type} 在 {year} 的扣除上限畸形（非 Money）；拒算。",
                    field=where,
                ) from None
            # E5/C4：ERCE cap 按 (claimant, 长者, YA) 跨 items 聚合——同一长者的
            # 多笔 item 不得各自重复用满 cap；不同长者各自独立 cap。同一 item
            # 引用多长者付款时按长者分算，不得把整笔金额只记到首笔付款长者的
            # cap（005 §6.2：cap 按同一 claimant/subject/YA 聚合）。
            elder_amounts: dict[str, Fraction] = {}
            for payment in payments:
                elder = payment["subject_id"]
                elder_amounts[elder] = (
                    elder_amounts.get(elder, Fraction(0)) + payment["amount"]
                )
            granted = Fraction(0)
            for elder in sorted(elder_amounts):
                cap_key = (person_id, elder)
                used = care_cap_used.get(cap_key, Fraction(0))
                room = cap - used
                elder_granted = (
                    min(elder_amounts[elder], room) if room > 0 else Fraction(0)
                )
                care_cap_used[cap_key] = used + elder_granted
                granted += elder_granted
            extra += granted
            resolved.append(
                {
                    "item_type": item_type,
                    "amount": granted,
                    "subject_ids": [payment["subject_id"] for payment in payments],
                    "payment_ids": list(payment_ids),
                }
            )
            decisions.append(
                {
                    "person_id": person_id,
                    "item_type": item_type,
                    "requested": True,
                    "allowed": canonical_amount(granted),
                    "subject_id": subject_id,
                }
            )
        finance["deductions"] = extra
        finance["deduction_items_resolved"] = resolved
        finance["deduction_decisions"] = decisions


def validate_ledger(
    y: Mapping,
    couple_claimants: set[str] | None,
) -> None:
    claims = y["claims"]
    child_nomination = y["household"]["child_nomination"]
    seen: set[tuple[str, str, str]] = set()
    for claim in claims:
        key = (claim["kind"], claim["claimant_id"], claim["subject_id"])
        if key in seen:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"同 (kind,claimant,subject) 重复条目：{key!r}",
                field="claims",
            )
        seen.add(key)
    groups: dict[tuple[str, str], set[str]] = {}
    for claim in claims:
        groups.setdefault((claim["kind"], claim["subject_id"]), set()).add(claim["claimant_id"])
    # s.31(3) 整组提名对账：agreed 归 nominee 一次；unknown/disputed 转 manual。
    # status=none（明确无获提名方）不构成对账场景 → 跨 claimant 重复仍拒算。
    enbloc_reconciled = (
        isinstance(child_nomination, Mapping)
        and child_nomination.get("status") in ("agreed", "unknown", "disputed")
    )
    nonseparation = nonseparation_tri(y) if couple_claimants else None
    for (kind, subject_id), claimants in groups.items():
        if len(claimants) <= 1:
            continue
        if (
            kind == "child"
            and couple_claimants
            and claimants <= couple_claimants
            and enbloc_reconciled
            and nonseparation is not False
        ):
            # s.31(3) 整组提名例外：已证分居不开放该例外（重复仍属矛盾）；
            # 非分居事实未知不在 ledger 阶段判矛盾，交计算阶段窄未决。
            continue
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"同 subject 跨 claimant 重复同类申索：{kind}/{subject_id}",
            field="claims",
        )
    kinds_by_subject: dict[str, set[str]] = {}
    for claim in claims:
        kinds_by_subject.setdefault(claim["subject_id"], set()).add(claim["kind"])
    for subject_id, kinds in kinds_by_subject.items():
        for mutex in _MUTEX_PAIRS:
            if mutex <= kinds:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"类互斥：subject {subject_id} 同时申索 {'＋'.join(sorted(mutex))}",
                    field="claims",
                )


# ---------------------------------------------------------------------------
# 日期谓词装配
# ---------------------------------------------------------------------------


def death_alive(
    death_date: date | None,
) -> Callable[[date], Any]:
    if death_date is None:
        return lambda day: True
    return lambda day: day < death_date


def child_age_path(
    birth: date,
    education: list[tuple[date, date, Any]],
    incapacity: list[tuple[date, date, Any]],
    *,
    for_sibling: bool = False,
) -> Callable[[date], Any]:
    """<18／18–25 全日制教育／18+ 伤残不能工作 三替代路径（OR）。"""

    def path(day: date) -> Any:
        if day < birth:
            return False
        if day < add_years(birth, 18):
            return True
        education_value = tf_at(education, day)
        incapacity_value = tf_at(incapacity, day)
        if day < add_years(birth, 25):
            return tri_or(education_value, incapacity_value)
        return incapacity_value

    return path


# ---------------------------------------------------------------------------
# 额度矩阵金额
# ---------------------------------------------------------------------------


def child_own_amounts(
    birth: date, matrix: Mapping, year: str, window: tuple[date, date]
) -> dict:
    base = node_amount(matrix, "child_base", year)
    birth_key = "child_birth_year_additional"
    following_key = "child_following_year_additional"
    birth_add = 0
    following_add = 0
    group = "none"
    previous_window = year_window(previous_year(year))
    if window[0] <= birth < window[1]:
        group = "birth"
        if not node_not_applicable(matrix, birth_key, year):
            birth_add = node_amount(matrix, birth_key, year)
    elif previous_window[0] <= birth < previous_window[1]:
        group = "following"
        if not node_not_applicable(matrix, following_key, year):
            following_add = node_amount(matrix, following_key, year)
    return {
        "base": base,
        "birth": birth_add,
        "following": following_add,
        "group": group,
    }


# ---------------------------------------------------------------------------
# claim 解析
# ---------------------------------------------------------------------------


def resolve_claims(
    *,
    y: dict,
    subjects: Mapping[str, date | None],
    matrix: Mapping,
    year: str,
    window: tuple[date, date],
    scenario_id: str,
    branch_a_recipient: str | None,
    spouse_dda_holder: str | None,
    child_nominee: str | None,
    child_nomination_unknown: bool,
    questions: list[dict],
    spouse_dda_both: bool = False,
) -> dict:
    """按场景解析 claim ledger → decisions＋各持有者金额＋四态。

    - entitlement 独立于 claim 意图（由原始事实派生）；
    - child 及 child DDA 归整组提名；spouse DDA 依 s.29 责任人；
    - 三层子女金额独立 cap；未知/缺失不静默计零。
    """
    spouse_id = y.get("spouse_id")
    decisions: list[dict] = []
    holder_amounts: dict[str, int] = {}
    unresolved = False
    parent_grants: list[tuple[str, str]] = []
    child_granted_claims: list[dict] = []
    child_dda_granted: list[dict] = []

    relation_index: dict[str, list[dict]] = {}
    for relation in y["relations"]:
        relation_index.setdefault(relation["subject_id"], []).append(relation)

    subject_facts = y["subject_facts"]

    def facts_tf(subject_id: str, field: str) -> list[tuple[date, date, Any]]:
        return subject_facts.get(subject_id, {}).get(field, [])

    def tf_fn(intervals: list[tuple[date, date, Any]]) -> Callable[[date], Any]:
        return lambda day: tf_at(intervals, day)

    def relation_of(subject_id: str, kinds: set[str]) -> dict | None:
        for relation in relation_index.get(subject_id, []):
            if relation["kind"] in kinds:
                return relation
        return None

    def relation_of_for(
        subject_id: str,
        actor_id: str,
        kinds: set[str],
        *,
        include_none: bool = False,
    ) -> dict | None:
        """按 claimant/actor 取直接关系（relation＝actor 与 subject 的关系，§4.5）。

        `include_none=True` 保留显式 `kind="none"`（false 事实），供 s.31A
        消费；默认仍按允许 kind 过滤，其他调用方行为不变。
        """
        for relation in relation_index.get(subject_id, []):
            if relation["actor_id"] != actor_id:
                continue
            if relation["kind"] in kinds or (
                include_none and relation["kind"] == "none"
            ):
                return relation
        return None

    def resident_fn(subject_id: str) -> Callable[[date], Any]:
        return tf_fn(facts_tf(subject_id, "ordinarily_resident_hk"))

    def daa_fn(subject_id: str) -> Callable[[date], Any]:
        return tf_fn(facts_tf(subject_id, "govt_disability_allowance"))

    def unanswered_claim(claim: dict, field: str, text: str) -> None:
        questions.append(
            make_question(
                field,
                year,
                text,
                claim_id=claim["claim_id"],
                subject_id=claim["subject_id"],
            )
        )

    def add_decision(
        claim: dict,
        *,
        entitlement: str,
        grant: str,
        amount: str | None,
        reason_codes: list[str],
        rule_refs: list[str],
        holder: str | None = None,
    ) -> dict:
        row = {
            "year": year,
            "scenario_id": scenario_id,
            "claim_id": claim["claim_id"],
            "claimant_id": holder if holder is not None else claim["claimant_id"],
            "subject_id": claim["subject_id"],
            "item_kind": claim["kind"],
            "entitlement": entitlement,
            "grant": grant,
            "amount": amount,
            "reason_codes": sorted(set(reason_codes)),
            "rule_refs": sorted(set(rule_refs)),
        }
        decisions.append(row)
        return row

    # ---- 子女（child）与 child DDA 的整组提名 ----
    participant_ids = {entry["person_id"] for entry in y["participants"]}

    def child_entitlement_for_relation(subject_id: str, relation: dict) -> Any:
        if kinship_unverified("child", relation["basis"]):
            return MANUAL
        birth = subjects.get(subject_id)
        if birth is None:
            return None
        facts = subject_facts.get(subject_id, {})
        return samepoint(
            [
                tf_fn(relation["during"]),
                death_alive(facts.get("death_date")),
                tf_fn(facts_tf(subject_id, "unmarried")),
                tf_fn(relation["maintained"]),
                child_age_path(
                    birth,
                    facts_tf(subject_id, "full_time_education"),
                    facts_tf(subject_id, "incapacitated_for_work"),
                ),
            ],
            window,
        )

    def child_entitlement(subject_id: str, claimant_id: str) -> Any:
        relation = relation_of_for(subject_id, claimant_id, {"child"})
        if relation is None:
            return None
        return child_entitlement_for_relation(subject_id, relation)

    def child_group_entitlement(subject_id: str) -> Any:
        """整组提名（agreed）：先求夫妻各自可成立的子女原始资格，再 OR 归组。"""
        relations = [
            relation
            for relation in relation_index.get(subject_id, [])
            if relation["kind"] == "child" and relation["actor_id"] in participant_ids
        ]
        if not relations:
            return None
        return tri_or(
            *[child_entitlement_for_relation(subject_id, relation) for relation in relations]
        )

    def nomination_unknown_for_child() -> bool:
        return child_nomination_unknown

    # ---- 父母/祖父母 ----
    def maintenance_of(relation: dict) -> Any:
        threshold = node_amount(matrix, "parent_cash_threshold", year)
        cash_value = (
            None if relation["cash"] is None else relation["cash"] >= threshold
        )
        cohabit_value = six_calendar_months(relation["cohabit"], window)
        return tri_or(cash_value, cohabit_value)

    def parent_tier_and_entitlement(
        subject_id: str, kinds: set[str], actor_id: str | None = None
    ) -> dict:
        birth = subjects.get(subject_id)
        if actor_id is not None:
            relation = relation_of_for(subject_id, actor_id, kinds)
        else:
            relation = relation_of(subject_id, kinds)
        result = {
            "tier": None,
            "tier_tri": None,
            "entitlement": None,
            "reason": [],
            "relation": relation,
            "during": None,
        }
        if birth is None or relation is None:
            return result
        if kinship_unverified(relation["kind"], relation["basis"]):
            result["entitlement"] = MANUAL
            result["reason"].append("UNVERIFIED_KINSHIP_BASIS")
            return result
        resident = resident_fn(subject_id)
        daa = daa_fn(subject_id)
        age60 = lambda day: day >= add_years(birth, 60)  # noqa: E731
        in_55_59 = lambda day: (  # noqa: E731
            add_years(birth, 55) <= day < add_years(birth, 60)
        )

        def high_day(day: date) -> Any:
            value = resident(day)
            if value is not True:
                return value
            if age60(day):
                return True
            return daa(day)

        high = any_over(high_day, window)
        low = tri_and(
            any_over(resident, window),
            any_over(in_55_59, window),
            all_over(lambda day: not age60(day), window),
            all_over(lambda day: tri_not(daa(day)), window),
        )
        if high is True:
            tier, tier_tri = "high", True
        elif low is True:
            tier, tier_tri = "low", True
        elif high is None or low is None:
            tier, tier_tri = None, None
        else:
            tier, tier_tri = None, False
        maintenance = maintenance_of(relation)
        # R5/E5：关系期间为父母/祖父母 entitlement 的活动消费项（during false →
        # 不成立；缺失/unknown → 未决，不得默认成立）。
        during = any_over(tf_fn(relation["during"]), window)
        entitlement = tri_and(tier_tri, maintenance, during)
        result.update(
            {
                "tier": tier,
                "tier_tri": tier_tri,
                "entitlement": entitlement,
                "during": during,
            }
        )
        if maintenance == MANUAL:
            result["reason"].append("SIX_MONTH_OPEN_BOUNDARY")
        return result

    # ---- s.26D 原始事实（替代基础；claimant/payer/关系/非分居按活动消费） ----
    def s26d_alternative(subject_id: str, claimant_id: str) -> Any:
        payments = [p for p in y["care_payments"] if p["subject_id"] == subject_id]
        if not payments:
            return False
        claimant_partner = partner_of(y, claimant_id)
        result: Any = False
        for payment in payments:
            facts = s26d_payment_facts(
                payment,
                y=y,
                subjects=subjects,
                window=window,
                claimant_id=claimant_id,
                partner_id=claimant_partner,
            )
            if facts["value"] is True:
                return True
            if facts["value"] is None:
                result = None
        return result

    # ---- 单亲 ----
    def exclusion_tri() -> Any:
        marriage = y["household"]["marriage"]
        apart = y["household"]["living_apart"]

        def exclusion_day(day: date) -> Any:
            value, _ = marriage_at(marriage, day)
            if value is None:
                return None
            if value != "married":
                return False
            return tri_not(tf_at(apart, day))

        return any_over(exclusion_day, window)

    def sole_care_fn(relation: dict) -> Callable[[date], Any]:
        return tf_fn(relation["sole_care"])

    def sibling_entitlement(subject_id: str, claimant_id: str) -> Any:
        birth = subjects.get(subject_id)
        relation = relation_of_for(subject_id, claimant_id, {"sibling"})
        if birth is None or relation is None:
            return None
        if kinship_unverified("sibling", relation["basis"]):
            return MANUAL
        facts = subject_facts.get(subject_id, {})
        base_path = samepoint(
            [
                tf_fn(relation["during"]),
                death_alive(facts.get("death_date")),
                tf_fn(facts_tf(subject_id, "unmarried")),
                tf_fn(relation["maintained"]),
                child_age_path(
                    birth,
                    facts_tf(subject_id, "full_time_education"),
                    facts_tf(subject_id, "incapacitated_for_work"),
                ),
            ],
            window,
        )
        care = any_over(sole_care_fn(relation), window)
        return tri_and(base_path, care)

    # ---- 逐 claim 决策 ----
    for claim in y["claims"]:
        kind = claim["kind"]
        subject_id = claim["subject_id"]
        claimant_id = claim["claimant_id"]
        claim_field = f"years.{year}.subject_facts.{subject_id}"
        special = claim["special"]
        if special is True or special == "unknown":
            add_decision(
                claim,
                entitlement="manual",
                grant="unresolved",
                amount=None,
                reason_codes=["SPECIAL_SITUATION"],
                rule_refs=[],
            )
            unanswered_claim(
                claim,
                f"years.{year}.claims[{claim['claim_id']}].special_situation",
                f"申索 {claim['claim_id']}（{kind}）声明特殊情形：本项转人工核实并定位，"
                "不整类禁算；请提供进一步资料或修正该项声明。",
            )
            unresolved = True
            continue

        # E3：manual_issues 按 scope/id 消费——subject/relation 事项落在本 claim
        # 依赖路径 → 该 claim 未决（其余 claim 不受影响；不整类禁算）。
        blocking_issue = None
        for issue in y.get("manual_issues") or []:
            if issue["scope"] == "subject" and issue["id"] == subject_id:
                blocking_issue = (
                    "subject",
                    issue,
                    f"years.{year}.subject_facts.{subject_id}",
                )
                break
            if issue["scope"] == "relation" and any(
                relation["relation_id"] == issue["id"]
                and relation["subject_id"] == subject_id
                and relation["actor_id"] == claimant_id
                for relation in y["relations"]
            ):
                blocking_issue = (
                    "relation",
                    issue,
                    f"years.{year}.relations.{issue['id']}",
                )
                break
        if blocking_issue is not None:
            scope, issue, block_field = blocking_issue
            add_decision(
                claim,
                entitlement="manual",
                grant="unresolved",
                amount=None,
                reason_codes=["MANUAL_ISSUE"],
                rule_refs=[],
            )
            questions.append(
                make_question(
                    block_field,
                    year,
                    f"人工边界事项（scope={scope}, id={issue['id']}, "
                    f"code={issue['code']}）定位申索 {claim['claim_id']}（{kind}）的"
                    "依赖事实：本项转人工/未决，不得授予。",
                    claim_id=claim["claim_id"],
                    subject_id=subject_id,
                )
            )
            unresolved = True
            continue

        if kind in ("child",):
            # child 整组归属：agreed 提名 → nominee；unknown/disputed → manual
            if nomination_unknown_for_child():
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["CHILD_NOMINATION_UNKNOWN"],
                    rule_refs=["s31(3)"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.household.child_nomination",
                    f"受养子女 {subject_id} 的整组提名（s.31(3)）未确定：请确认由哪一方"
                    "持有子女免税额（agreed/none），或修正争议声明。",
                )
                unresolved = True
                continue
            if child_nominee is not None:
                entitlement = child_group_entitlement(subject_id)
            else:
                entitlement = child_entitlement(subject_id, claimant_id)
            if entitlement is True:
                # 整组提名归属：agreed → nominee 持有且每 subject 只计一次；none → claimant。
                holder = child_nominee if child_nominee else claimant_id
                already = any(
                    entry["claim"]["subject_id"] == subject_id and entry["holder"] == holder
                    for entry in child_granted_claims
                )
                if already:
                    add_decision(
                        claim,
                        entitlement="eligible",
                        grant="not_requested",
                        amount=None,
                        reason_codes=["ENBLOC_NOMINATION_OTHER_HOLDER"],
                        rule_refs=["s31(3)"],
                        holder=holder,
                    )
                else:
                    amounts = child_own_amounts(subjects[subject_id], matrix, year, window)
                    child_decision = add_decision(
                        claim,
                        entitlement="eligible",
                        grant="allowed",
                        amount=str(amounts["base"] + amounts["birth"] + amounts["following"]),
                        reason_codes=["CHILD_ENTITLED"],
                        rule_refs=["s31"],
                        holder=holder,
                    )
                    child_granted_claims.append(
                        {
                            "claim": claim,
                            "amounts": amounts,
                            "holder": holder,
                            "decision": child_decision,
                        }
                    )
            elif entitlement == MANUAL:
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["UNVERIFIED_KINSHIP_BASIS"],
                    rule_refs=["s31"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.relations[{subject_id}].kinship.basis",
                    f"受养子女 {subject_id} 的亲属关系组合未在普通已核映射内：转人工核实"
                    "并定位，不整类禁算。",
                )
                unresolved = True
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["CHILD_NOT_ENTITLED"],
                    rule_refs=["s31"],
                )
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["CHILD_FACTS_UNKNOWN"],
                    rule_refs=["s31"],
                )
                unanswered_claim(
                    claim,
                    claim_field,
                    f"受养子女 {subject_id} 的资格事实未确定（未婚/受供养/年龄或教育/伤残"
                    "路径整合同点判据）：请补全对应期间事实（unknown 不得默认否）。",
                )
                unresolved = True
            continue

        if kind in ("parent", "grandparent"):
            kinds = {"parent", "grandparent"}
            death = subject_facts.get(subject_id, {}).get("death_date")
            if death is not None and window[0] <= death < window[1]:
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["DEATH_IN_YEAR"],
                    rule_refs=["s30"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.subject_facts.{subject_id}.death_date",
                    f"受养人 {subject_id} 于课税年度内死亡（{death.isoformat()}）：死亡年度"
                    "影响总额，转人工核实并定位，不整类禁算。",
                )
                unresolved = True
                continue
            tier_info = parent_tier_and_entitlement(subject_id, kinds, claimant_id)
            entitlement = tier_info["entitlement"]
            if entitlement is True:
                amount_key = (
                    "parent_high" if tier_info["tier"] == "high" else "parent_low"
                )
                amount = node_amount(matrix, amount_key, year)
                parent_grants.append((claimant_id, subject_id))
                reasons = ["PARENT_ENTITLED"]
                if tier_info["reason"]:
                    reasons += tier_info["reason"]
                add_decision(
                    claim,
                    entitlement="eligible",
                    grant="allowed",
                    amount=str(amount),
                    reason_codes=reasons,
                    rule_refs=["s30"],
                )
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["PARENT_NOT_ENTITLED"],
                    rule_refs=["s30"],
                )
            elif entitlement == MANUAL:
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=tier_info["reason"] or ["SIX_MONTH_OPEN_BOUNDARY"],
                    rule_refs=["s30(4)"],
                )
                if "UNVERIFIED_KINSHIP_BASIS" in tier_info["reason"]:
                    unanswered_claim(
                        claim,
                        f"years.{year}.relations[{subject_id}].kinship.basis",
                        f"受养人 {subject_id} 的亲属关系组合未在普通已核映射内：转人工"
                        "核实并定位，不整类禁算。",
                    )
                else:
                    unanswered_claim(
                        claim,
                        f"years.{year}.relations[{subject_id}].cohabit_non_full_consideration",
                        f"受养人 {subject_id} 的连续六个月非足值同住仅在月末截短约定下成立"
                        "（OPEN 边界）：转人工核实，不自行发明计月规则。",
                    )
                unresolved = True
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["PARENT_FACTS_UNKNOWN"],
                    rule_refs=["s30"],
                )
                if tier_info["during"] is None:
                    unanswered_claim(
                        claim,
                        f"years.{year}.relations[{subject_id}].kinship.during",
                        f"受养人 {subject_id} 的关系期间（kinship.during）缺失/未知："
                        "请补全关系有效期间（unknown 不得默认成立/否）。",
                    )
                else:
                    unanswered_claim(
                        claim,
                        claim_field,
                        f"受养人 {subject_id} 的年龄档/通常居港/政府伤残津贴资格或供养事实"
                        "未确定：请补全对应事实（unknown 不得默认否）。",
                    )
                unresolved = True
            continue

        if kind == "parent_cohabitation_additional":
            basic_intent = any(
                other["kind"] in ("parent", "grandparent")
                and other["claimant_id"] == claimant_id
                and other["subject_id"] == subject_id
                for other in y["claims"]
            )
            if not basic_intent:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["BASIC_INTENT_REQUIRED"],
                    rule_refs=["s30(3)(b)"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.claims",
                    f"受养人 {subject_id} 仅申索全年同住额外额而无基本 parent/grandparent "
                    "申索意图：请一并明确基本申索意图（s.30(3)(b) 行）。",
                )
                unresolved = True
                continue
            tier_info = parent_tier_and_entitlement(
                subject_id, {"parent", "grandparent"}, claimant_id
            )
            relation = relation_of_for(subject_id, claimant_id, {"parent", "grandparent"})
            if relation is None:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["PARENT_FACTS_UNKNOWN"],
                    rule_refs=["s30(3)"],
                )
                unanswered_claim(claim, claim_field, f"受养人 {subject_id} 的关系事实缺失：请补全。")
                unresolved = True
                continue
            cohabit_all = all_over(tf_fn(relation["cohabit"]), window)
            entitlement = tri_and(tier_info["entitlement"], cohabit_all)
            if entitlement is True:
                amount_key = (
                    "parent_high_cohabitation"
                    if tier_info["tier"] == "high"
                    else "parent_low_cohabitation"
                )
                add_decision(
                    claim,
                    entitlement="eligible",
                    grant="allowed",
                    amount=str(node_amount(matrix, amount_key, year)),
                    reason_codes=["FULL_YEAR_COHABITATION"],
                    rule_refs=["s30(3)(b)"],
                )
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["FULL_YEAR_COHABITATION_NOT_MET"],
                    rule_refs=["s30(3)(b)"],
                )
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["COHABITATION_FACTS_UNKNOWN"],
                    rule_refs=["s30(3)(b)"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.relations[{subject_id}].cohabit_non_full_consideration",
                    f"受养人 {subject_id} 的全年同住额外额事实未确定：请补全。",
                )
                unresolved = True
            continue

        if kind == "sibling":
            entitlement = sibling_entitlement(subject_id, claimant_id)
            if entitlement is True:
                add_decision(
                    claim,
                    entitlement="eligible",
                    grant="allowed",
                    amount=str(node_amount(matrix, "sibling", year)),
                    reason_codes=["SIBLING_ENTITLED"],
                    rule_refs=["s30B"],
                )
            elif entitlement == MANUAL:
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["UNVERIFIED_KINSHIP_BASIS"],
                    rule_refs=["s30B"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.relations[{subject_id}].kinship.basis",
                    f"兄弟姊妹 {subject_id} 的亲属关系组合未在普通已核映射内：转人工核实"
                    "并定位，不整类禁算。",
                )
                unresolved = True
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["SIBLING_NOT_ENTITLED"],
                    rule_refs=["s30B"],
                )
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["SIBLING_FACTS_UNKNOWN"],
                    rule_refs=["s30B"],
                )
                unanswered_claim(
                    claim,
                    claim_field,
                    f"兄弟姊妹 {subject_id} 的未婚/受供养/年龄路径或主要照顾事实未确定："
                    "请补全（汇钱不等于主要照顾）。",
                )
                unresolved = True
            continue

        if kind == "single_parent":
            exclusion = exclusion_tri()
            relation = relation_of_for(subject_id, claimant_id, {"child"})
            if exclusion is True:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["MARRIED_NOT_APART_EXCLUDED"],
                    rule_refs=["s32(2)"],
                )
                continue
            child_value = child_entitlement(subject_id, claimant_id)
            care: Any = None if relation is None else any_over(sole_care_fn(relation), window)
            entitlement = tri_and(tri_not(exclusion), child_value, care)
            if entitlement is True:
                granted = [
                    existing
                    for existing in decisions
                    if existing.get("item_kind") == "single_parent"
                    and existing.get("claimant_id") == claim["claimant_id"]
                    and existing.get("grant") == "allowed"
                ]
                if granted:
                    add_decision(
                        claim,
                        entitlement="eligible",
                        grant="not_requested",
                        amount=None,
                        reason_codes=["SINGLE_PARENT_ONCE_PER_CLAIMANT"],
                        rule_refs=["s32"],
                    )
                else:
                    add_decision(
                        claim,
                        entitlement="eligible",
                        grant="allowed",
                        amount=str(node_amount(matrix, "single_parent", year)),
                        reason_codes=["SINGLE_PARENT_ENTITLED"],
                        rule_refs=["s32"],
                    )
            elif entitlement == MANUAL:
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["UNVERIFIED_KINSHIP_BASIS"],
                    rule_refs=["s32"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.relations[{subject_id}].kinship.basis",
                    f"单亲免税额（{subject_id}）的亲属关系组合未在普通已核映射内：转人工"
                    "核实并定位，不整类禁算。",
                )
                unresolved = True
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["SINGLE_PARENT_NOT_ENTITLED"],
                    rule_refs=["s32"],
                )
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["SINGLE_PARENT_FACTS_UNKNOWN"],
                    rule_refs=["s32"],
                )
                if relation is not None and care is None:
                    # A1：缺/未知照顾事实须精确定位 relation 的
                    # sole_or_predominant_care 字段（不得指向 subject_facts）。
                    unanswered_claim(
                        claim,
                        f"years.{year}.relations[{subject_id}]"
                        ".sole_or_predominant_care",
                        f"单亲免税额（{subject_id}）的主要/独力照顾期间"
                        "（sole_or_predominant_care）缺失/未知：请补全。",
                    )
                else:
                    unanswered_claim(
                        claim,
                        claim_field,
                        f"单亲免税额（{subject_id}）的指定子女资格或独力照顾期间未确定：请补全。",
                    )
                unresolved = True
            continue

        if kind == "personal_disability":
            entitlement = any_over(daa_fn(claimant_id), window)
            if entitlement is True:
                add_decision(
                    claim,
                    entitlement="eligible",
                    grant="allowed",
                    amount=str(node_amount(matrix, "personal_disability", year)),
                    reason_codes=["PERSONAL_DISABILITY_ENTITLED"],
                    rule_refs=["s28A"],
                )
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["PERSONAL_DISABILITY_NOT_ENTITLED"],
                    rule_refs=["s28A"],
                )
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["PERSONAL_DISABILITY_UNKNOWN"],
                    rule_refs=["s28A"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.subject_facts.{claimant_id}.govt_disability_allowance",
                    f"本人伤残免税额：请确认申索人 {claimant_id} 的政府伤残津贴资格期间。",
                )
                unresolved = True
            continue

        if kind == "disabled_dependant":
            daa_value = any_over(daa_fn(subject_id), window)
            relation: dict | None = None
            # spouse 路径：subject 为配偶或本人（claimant 为另一方），两向均属 s.29(1A)
            self_id = y["self_id"]
            is_spouse_subject = (
                spouse_id is not None
                and subject_id in (self_id, spouse_id)
                and subject_id != claimant_id
            )
            if is_spouse_subject:
                if spouse_dda_both:
                    dependent_value: Any = True
                else:
                    dependent_value = claimant_id == spouse_dda_holder
            else:
                relation = relation_of_for(
                    subject_id,
                    claimant_id,
                    {"child", "parent", "grandparent", "sibling"},
                    include_none=True,
                )
                # F5/R5（005 §6.2）：s.26D 替代基础不以 claimant 直接关系为
                # 前提（配偶亲属亦在 s.26D 范围内）。先求直接基本 entitlement，
                # 再与 s.26D 替代路径做三态 OR；直接关系缺失本身保持 unknown，
                # 替代未证 true 时不得改判 false 或自动授予。
                if relation is None:
                    direct_value: Any = None
                elif relation["kind"] == "none":
                    # 补正二/R5：显式 none 是 false 事实，不是缺失；与 s.26D
                    # 替代路径 OR 后 false∨false=false（ineligible），而缺失
                    # （None）∨false 仍为 None（needs_input）。
                    direct_value = False
                elif relation["kind"] == "child":
                    direct_value = child_entitlement(subject_id, claimant_id)
                elif relation["kind"] in ("parent", "grandparent"):
                    direct_value = parent_tier_and_entitlement(
                        subject_id, {"parent", "grandparent"}, claimant_id
                    )["entitlement"]
                elif relation["kind"] == "sibling":
                    direct_value = sibling_entitlement(subject_id, claimant_id)
                else:
                    direct_value = None
                dependent_value = tri_or(
                    direct_value, s26d_alternative(subject_id, claimant_id)
                )
            entitlement = tri_and(daa_value, dependent_value)
            if subject_id != spouse_id and relation is not None and relation["kind"] == "child" and nomination_unknown_for_child():
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["CHILD_NOMINATION_UNKNOWN"],
                    rule_refs=["s31A"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.household.child_nomination",
                    f"受养人 {subject_id} 的伤残受养人额随子女整组提名归属未确定：请确认提名。",
                )
                unresolved = True
                continue
            if entitlement is True:
                holder = claimant_id
                if subject_id != spouse_id and relation is not None and relation["kind"] == "child" and child_nominee:
                    holder = child_nominee
                add_decision(
                    claim,
                    entitlement="eligible",
                    grant="allowed",
                    amount=str(node_amount(matrix, "disabled_dependant", year)),
                    reason_codes=["DISABLED_DEPENDANT_ENTITLED"],
                    rule_refs=["s31A"],
                    holder=holder,
                )
                child_dda_granted.append({"claim": claim, "holder": holder})
            elif entitlement == MANUAL:
                add_decision(
                    claim,
                    entitlement="manual",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["UNVERIFIED_KINSHIP_BASIS"],
                    rule_refs=["s31A"],
                )
                unanswered_claim(
                    claim,
                    f"years.{year}.relations[{subject_id}].kinship.basis",
                    f"伤残受养人额（{subject_id}）的亲属关系组合未在普通已核映射内："
                    "转人工核实并定位，不整类禁算。",
                )
                unresolved = True
            elif entitlement is False:
                add_decision(
                    claim,
                    entitlement="ineligible",
                    grant="ineligible",
                    amount=None,
                    reason_codes=["DISABLED_DEPENDANT_NOT_ENTITLED"],
                    rule_refs=["s31A"],
                )
            else:
                add_decision(
                    claim,
                    entitlement="unknown",
                    grant="unresolved",
                    amount=None,
                    reason_codes=["DISABLED_DEPENDANT_FACTS_UNKNOWN"],
                    rule_refs=["s31A"],
                )
                unanswered_claim(
                    claim,
                    claim_field,
                    f"伤残受养人额（{subject_id}）的伤残津贴资格或对应受养关系基础未确定："
                    "请补全（不要求基本 claim/扣除意图）。",
                )
                unresolved = True
            continue

    # ---- 三层子女金额独立 cap（min(Σbase,cap)+min(Σ(1A),cap)+min(Σ(1B),cap)）----
    group_node_keys = {
        "base": "child_base",
        "birth": "child_birth_year_additional",
        "following": "child_following_year_additional",
    }
    remaining = {
        group: node_cap(matrix, key, year) for group, key in group_node_keys.items()
    }
    for entry in sorted(child_granted_claims, key=lambda item: item["claim"]["claim_id"]):
        holder = entry["holder"]
        allocated = 0
        for group in ("base", "birth", "following"):
            amount = entry["amounts"][group]
            if amount <= 0:
                continue
            cap_left = remaining[group]
            if cap_left is not None:
                amount = min(amount, cap_left)
                remaining[group] = cap_left - amount
            allocated += amount
        # E7/R7：cap 分配结果回写该 claim 的 decision 金额（与实际有效额度对账，
        # 不得仍报 cap 前 130,000）。
        entry["decision"]["amount"] = str(allocated)
        holder_amounts[holder] = holder_amounts.get(holder, 0) + allocated
    for entry in child_dda_granted:
        holder = entry["holder"]
        holder_amounts[holder] = holder_amounts.get(holder, 0) + node_amount(
            matrix, "disabled_dependant", year
        )

    # ---- 非子女类 allowed 金额归持有者 ----
    for decision in decisions:
        if decision["scenario_id"] != scenario_id:
            continue
        if decision["grant"] != "allowed" or decision["amount"] is None:
            continue
        if decision["item_kind"] in ("child", "disabled_dependant"):
            continue
        holder = decision["claimant_id"]
        holder_amounts[holder] = holder_amounts.get(holder, 0) + int(decision["amount"])

    return {
        "decisions": decisions,
        "holder_amounts": holder_amounts,
        "unresolved": unresolved,
        "parent_grants": parent_grants,
        "child_granted": child_granted_claims,
        "child_dda_granted": child_dda_granted,
    }
