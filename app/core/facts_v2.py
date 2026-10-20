# -*- coding: utf-8 -*-
"""中立 v2 事实规范化（002 W5+ Green E2；Annex 005 §3–§7/§11/§13）。

唯一结构权威：HTTP 信封校验（app.api.validation）与两纯引擎入口
（app.engines.salaries／personal_assessment）共同调用本模块。
`normalize_v2_facts(raw, *, tax_type) -> dict` 返回 canonical v2 **input**
（不是 envelope）；无运行时规则束/网络依赖（唯一例外：`_v2_deduction_item`
按需导入静态「在册扣除」枚举常量，见该函数注记）：

- input/Year/子对象逐层封闭键（未列字段 → E_INPUT_EXTRA＋完整 CF1 路径）；
- 八个必填 Year 容器 missing（E_INPUT_MISSING）与显式 null（E_INPUT_TYPE）
  分明；manual_issues／provisional_paid 可选，省略保持省略（不得静默补
  `[]`/0 后与显式值同 hash）；
- ID 前缀＋1–31 位（总长 2–32）、引用完整性、跨年 self 稳定；
- TF 左闭右开、按序不重叠；每键年先验＝该 Year 自身窗口；仅列明四谓词
  （ordinarily_resident_hk／both_parents_dead／marriage／living_apart）可
  追加该 Year 已声明的参考日/PA 选择日**单日**区间；care home TF 域＝付款
  自身 [care_from, care_to)；presence 辅助年＝显式 Year 的 {Y−1,Y,Y+1} 并集
  且不开放普通 TF 年外复制；
- Money 尾零规范化、Ratio 约简（canonical input hash 口径，R3④）；
- 全部显式 Year 的候选/selection/假设先验（含显式目标与假设异额矛盾），
  供引擎在年度 guard 短路前完成（B6/E2）；
- 缺失≠零：不注入默认值、不补资格结论（unknown 保持年度/候选局部）。
"""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Mapping

from app.config import SUPPORTED_YEARS
from app.core.errors import AppError
from app.core.money import parse_amount, parse_ratio

_YEAR_KEY_RE = re.compile(r"[0-9]{4}_[0-9]{2}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# C2.9 既有数组上限（与引擎侧 Facts 同值/同 field；此处先行核验，避免 guard
# 短路跳过结构校验——见 R3「中立入口完整闭合」）。
_MAX_ARRAY_ITEMS = 1000

_V2_INPUT_KEYS = ("year_of_assessment", "subjects", "years", "hk_presence_days", "assumptions")
_V2_SUBJECT_KEYS = ("subject_id", "birth_date")
_V2_PARTICIPANT_KEYS = ("person_id", "role")
_V2_HOUSEHOLD_KEYS = ("marriage", "living_apart", "child_nomination")
_V2_SUBJECT_FACT_TF_KEYS = (
    "unmarried",
    "govt_disability_allowance",
    "full_time_education",
    "incapacitated_for_work",
    "ordinarily_resident_hk",
    "both_parents_dead",
)
_V2_SUBJECT_FACT_KEYS = (*_V2_SUBJECT_FACT_TF_KEYS, "death_date")
# §7：仅这四类列明谓词可覆盖参考日/已声明 PA 选择日的单日区间。
_V2_EXTENDABLE_TF_KEYS = (
    "ordinarily_resident_hk",
    "both_parents_dead",
)
_V2_RELATION_KEYS = (
    "relation_id",
    "actor_id",
    "subject_id",
    "kinship",
    "maintained",
    "cash_maintenance",
    "cohabit_non_full_consideration",
    "sole_or_predominant_care",
)
_V2_KINSHIP_KINDS = ("child", "parent", "grandparent", "sibling", "none", "unknown")
_V2_KINSHIP_BASES = (
    "birth",
    "adoption",
    "step",
    "full_blood",
    "half_blood",
    "child_of_adoptive_parent",
    "other",
    "unknown",
)
_V2_FINANCE_KEYS = (
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
_V2_PROPERTY_KEYS = (
    "property_id",
    "share",
    "rent_received",
    "rates_paid_by_owner",
    "irrecoverable_rent",
    "deposit_offsets",
    "mortgage_interest",
)
_V2_BUSINESS_KEYS = ("business_id", "entity_kind", "assessable_profit", "share")
_V2_DEDUCTION_KEYS = ("item_type", "amount", "eligibility", "subject_id", "payment_ids")
_V2_CARE_PAYMENT_KEYS = (
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
_V2_HOME_BASES = (
    "cap459_licence",
    "cap459_s3_excluded",
    "cap633_scheduled_nursing_s128_exemption",
    "cap613_licence",
    "cap613_exemption",
    "other",
    "unknown",
)
_V2_CLAIM_KINDS = (
    "child",
    "parent",
    "grandparent",
    "sibling",
    "single_parent",
    "personal_disability",
    "disabled_dependant",
    "parent_cohabitation_additional",
)
_V2_CLAIM_KEYS = ("claim_id", "kind", "claimant_id", "subject_id", "special_situation")
_V2_MANUAL_SCOPES = ("person", "subject", "relation", "property", "business", "election")
_V2_MANUAL_CODES = (
    "death_in_year",
    "dispute",
    "source_uncertain",
    "loss_or_transfer",
    "other",
)
_V2_MANUAL_KEYS = ("scope", "id", "code")
# 005 §3/§4.3：八个必填 Year 容器（manual_issues 可选；provisional_paid 可选）。
_V2_YEAR_REQUIRED_KEYS = (
    "participants",
    "subject_facts",
    "household",
    "relations",
    "financials",
    "care_payments",
    "claims",
    "assessment",
)
_V2_YEAR_KEYS = (*_V2_YEAR_REQUIRED_KEYS, "manual_issues", "provisional_paid")
_V2_ASSESSMENT_KEYS = (
    "basis",
    "candidates",
    "selection",
    "reference_date",
    "actual",
    "hypothetical_salary_joint_nomination",
)
_V2_ACTUAL_KEYS = (
    "salary_joint_elected",
    "salary_joint_assessed",
    "pa",
    "history",
    "salary_joint_nomination",
)
_V2_PA_MODES = ("none", "individual", "joint", "unknown")
_V2_HISTORY_VALUES = ("ordinary", "withdrawn", "disputed", "unknown")
_V2_NOMINATION_STATUSES = ("none", "named", "unknown", "disputed")
_V2_ASSUMPTION_KINDS = ("same_income", "same_mpf")
_V2_CANDIDATES_BY_TAX: dict[str, frozenset[str]] = {
    "salaries_tax": frozenset({"salary_separate", "salary_joint"}),
    "personal_assessment": frozenset({"no_pa", "pa_self", "pa_joint"}),
}
_V2_ID_RES: dict[str, re.Pattern[str]] = {
    "p": re.compile(r"p[a-z0-9-]{1,31}"),
    "d": re.compile(r"d[a-z0-9-]{1,31}"),
    "r": re.compile(r"r[a-z0-9-]{1,31}"),
    "c": re.compile(r"c[a-z0-9-]{1,31}"),
    "e": re.compile(r"e[a-z0-9-]{1,31}"),
    "h": re.compile(r"h[a-z0-9-]{1,31}"),
    "b": re.compile(r"b[a-z0-9-]{1,31}"),
    "a": re.compile(r"a[a-z0-9-]{1,31}"),
}


def _v2_id(raw: Any, field: str, prefixes: tuple[str, ...]) -> str:
    if not isinstance(raw, str) or not any(
        _V2_ID_RES[prefix].fullmatch(raw) for prefix in prefixes
    ):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 必须为 {'/'.join(prefixes)} 前缀 ID"
            "（1–31 位小写字母/数字/连字符；005 §3 ID）",
            field=field,
        )
    return raw


def _v2_date(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or _DATE_RE.fullmatch(raw) is None:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 必须为 YYYY-MM-DD 全匹配（005 §3）", field=field
        )
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 不是有效日历日期", field=field
        ) from None
    return raw


def _v2_date_or_none(raw: Any) -> date | None:
    if not isinstance(raw, str) or _DATE_RE.fullmatch(raw) is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _v2_money(raw: Any, field: str) -> str:
    if not isinstance(raw, str):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 必须为 canonical 金额字符串（C2.1；禁止 JSON number/布尔）",
            field=field,
        )
    try:
        return parse_amount(raw)
    except AppError:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 必须为 canonical 金额字符串（C2.1；尾零规范化，"
            "禁指数/空白/前导零）",
            field=field,
        ) from None


def _v2_ratio(raw: Any, field: str) -> str:
    if not isinstance(raw, str):
        raise AppError(
            "E_INPUT_TYPE", f"{field} 必须为 num/den 比率字符串（C2.3）", field=field
        )
    try:
        fraction = parse_ratio(raw)
    except AppError:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 不是合法比率（num/den，C2.3）", field=field
        ) from None
    if fraction <= 0 or fraction > 1:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 必须满足 0<num≤den（C2.3 份额域）",
            field=field,
        )
    return f"{fraction.numerator}/{fraction.denominator}"


def _v2_tri(raw: Any, field: str) -> Any:
    """原始 Tri（005 §3：true/false/"unknown"；扣除项 legacy 包装除外）。"""
    if raw is True or raw is False or raw == "unknown":
        return raw
    raise AppError(
        "E_INPUT_TYPE",
        f'{field} 必须为原始三态 true／false／"unknown"（005 §3；扣除项除外）',
        field=field,
    )


def _reject_v2_extra(value: Mapping, allowed: tuple[str, ...], field: str) -> None:
    extras = [key for key in value if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=f"{field}.{extras[0]}"
        )


def _canonical_year_bounds(year_key: str) -> tuple[date, date]:
    start_year = int(year_key[:4])
    return date(start_year, 4, 1), date(start_year + 1, 4, 1)


def _limit_array(raw: list, field: str) -> None:
    if len(raw) > _MAX_ARRAY_ITEMS:
        raise AppError(
            "E_INPUT_OVERSIZE",
            f"{field} 超过既有上限 {_MAX_ARRAY_ITEMS} 项（C2.9）",
            field=field,
        )


def _following_year(year_key: str) -> str:
    start = int(year_key[:4])
    return f"{start + 1}_{(start + 2) % 100:02d}"


def _canonical_year(inp: Mapping) -> str:
    """年度键（C2.11 YYYY_YY；REQ-2 超范围不回退当前年度）。"""
    year = inp["year_of_assessment"]
    if not isinstance(year, str) or _YEAR_KEY_RE.fullmatch(year) is None:
        raise AppError(
            "E_INPUT_TYPE", "年度键必须为 YYYY_YY 形式", field="year_of_assessment"
        )
    if year not in SUPPORTED_YEARS:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"课税年度 {year} 超出已核验支持范围（2024/25 至 2026/27）；"
            "请改选支持期间或停止计算。",
            field="year_of_assessment",
        )
    return year


def _require_v2_key(raw: Mapping, key: str, field: str) -> None:
    """必填容器：missing → E_INPUT_MISSING；显式 null → E_INPUT_TYPE（同一完整路径）。"""
    if key not in raw:
        raise AppError("E_INPUT_MISSING", f"{field}.{key} 必填", field=f"{field}.{key}")
    if raw[key] is None:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.{key} 显式 null 非法（null≠省略；005 §3）",
            field=f"{field}.{key}",
        )


def _v2_assessment_extra_dates(assessment: Any) -> list[date]:
    """§7：仅列明谓词可额外覆盖参考日/已声明 PA 选择日的一天区间。"""
    if not isinstance(assessment, Mapping):
        return []
    extras: list[date] = []
    reference = _v2_date_or_none(assessment.get("reference_date"))
    if reference is not None:
        extras.append(reference)
    actual = assessment.get("actual")
    if isinstance(actual, Mapping):
        pa = actual.get("pa")
        if isinstance(pa, Mapping):
            for entry in pa.values():
                if isinstance(entry, Mapping):
                    election = _v2_date_or_none(entry.get("date"))
                    if election is not None:
                        extras.append(election)
    return extras


def _v2_tf(
    raw: Any,
    field: str,
    *,
    domain: list[tuple[date, date]],
    kind: str = "tri",
    single_day_extension: bool = False,
) -> list[dict]:
    """TF 严格校验（005 §3/§7）：Date 全匹配、from<to、按 wire 顺序且互不重叠、
    每段必须完整落在允许窗口之一（该 Year 窗口＋列明参考/选择日一天区间）。

    `single_day_extension`：该 Year 未声明任何参考/选择日时，列明谓词保留单日
    区间兼容（其余情况仅开放所声明日期的一天区间）。
    """
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为时间事实数组", field=field)
    canonical: list[dict] = []
    previous_to: date | None = None
    previous_value: Any = None
    for index, segment in enumerate(raw):
        where = f"{field}[{index}]"
        if not isinstance(segment, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
        extras = [key for key in segment if key not in ("from", "to", "value", "spouse_id")]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{where} 含未知字段：{extras[0]}", field=f"{where}.{extras[0]}"
            )
        for required in ("from", "to", "value"):
            if required not in segment:
                raise AppError(
                    "E_INPUT_MISSING", f"{where} 缺少 {required}", field=f"{where}.{required}"
                )
        from_date = date.fromisoformat(_v2_date(segment["from"], f"{where}.from"))
        to_date = date.fromisoformat(_v2_date(segment["to"], f"{where}.to"))
        if from_date >= to_date:
            raise AppError(
                "E_INPUT_TYPE", f"{where} 须满足 from<to（左闭右开）", field=where
            )
        if previous_to is not None and from_date < previous_to:
            if (
                kind == "marriage"
                and previous_value == "married"
                and segment.get("value") == "married"
            ):
                # 双 married 重叠＝配偶身份已证矛盾（005 §4.2 结构负例；
                # 纯引擎冻结断言：CONTRADICTORY）。
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where} 婚姻区间重叠（配偶身份矛盾；005 §4.2）",
                    field=where,
                )
            raise AppError(
                "E_INPUT_TYPE",
                f"{where} 时间事实须按序且互不重叠（按 wire 原始顺序核验）",
                field=where,
            )
        previous_to = to_date
        previous_value = segment.get("value")
        if not any(begin <= from_date and to_date <= end for begin, end in domain):
            if single_day_extension and to_date - from_date == timedelta(days=1):
                pass  # 未声明参考/选择日的列明谓词：单日区间兼容
            else:
                raise AppError(
                    "E_INPUT_TYPE",
                    f"{where} 越出允许时间窗口（该 Year [4/1, 次年 4/1)；仅列明"
                    "谓词可加参考日/选择日一天区间，005 §7）",
                    field=where,
                )
        result: dict[str, Any] = {
            "from": segment["from"],
            "to": segment["to"],
        }
        if kind == "marriage":
            value = segment["value"]
            if value not in ("unmarried", "married", "unknown"):
                raise AppError(
                    "E_INPUT_TYPE",
                    f'{where}.value 必须为 "unmarried"/"married"/"unknown"（MarriageTF）',
                    field=f"{where}.value",
                )
            if value == "married":
                if "spouse_id" not in segment:
                    raise AppError(
                        "E_INPUT_MISSING",
                        f"{where}.spouse_id 缺失（married 区间必带配偶 ID）",
                        field=f"{where}.spouse_id",
                    )
                result["spouse_id"] = _v2_id(
                    segment["spouse_id"], f"{where}.spouse_id", ("p",)
                )
            elif "spouse_id" in segment:
                raise AppError(
                    "E_INPUT_TYPE",
                    f"{where}.spouse_id 仅 married 区间允许（非 married 区间禁止该键）",
                    field=f"{where}.spouse_id",
                )
            result["value"] = value
        else:
            result["value"] = _v2_tri(segment["value"], f"{where}.value")
        canonical.append(result)
    return canonical


def _v2_nomination(raw: Any, field: str, participant_ids: set[str]) -> dict:
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为对象", field=field)
    _reject_v2_extra(raw, ("status", "person_id"), field)
    if "status" not in raw or "person_id" not in raw:
        raise AppError(
            "E_INPUT_MISSING", f"{field} 缺少 status/person_id", field=field
        )
    status = raw["status"]
    if status not in _V2_NOMINATION_STATUSES:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.status 必须为 none/named/unknown/disputed",
            field=f"{field}.status",
        )
    person_id = raw["person_id"]
    if status == "named":
        nominee = _v2_id(person_id, f"{field}.person_id", ("p",))
        if nominee not in participant_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.person_id 必须引用当年两名 participant 之一"
                "（005 §4.10 Nomination）",
                field=f"{field}.person_id",
            )
        return {"status": "named", "person_id": nominee}
    if person_id is not None:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.person_id 必须为 null（非 named status；005 §4.10）",
            field=f"{field}.person_id",
        )
    return {"status": status, "person_id": None}


def _v2_assessment(
    raw: Any,
    field: str,
    *,
    tax_type: str,
    participants: set[str],
) -> dict:
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为对象", field=field)
    _reject_v2_extra(raw, _V2_ASSESSMENT_KEYS, field)
    if "basis" not in raw or "candidates" not in raw or "selection" not in raw:
        raise AppError(
            "E_INPUT_MISSING",
            f"{field} 须无条件含 basis/candidates/selection（005 §4.10）",
            field=field,
        )
    basis = raw["basis"]
    if basis not in ("actual", "hypothetical"):
        raise AppError(
            "E_INPUT_TYPE", f"{field}.basis 必须为 actual／hypothetical", field=f"{field}.basis"
        )
    candidates_raw = raw["candidates"]
    if not isinstance(candidates_raw, list) or not candidates_raw:
        raise AppError(
            "E_INPUT_MISSING", f"{field}.candidates 必须为非空数组", field=f"{field}.candidates"
        )
    allowed_candidates = _V2_CANDIDATES_BY_TAX[tax_type]
    candidates: list[str] = []
    for candidate in candidates_raw:
        if not isinstance(candidate, str) or candidate not in allowed_candidates:
            raise AppError(
                "E_INPUT_EXTRA",
                f"候选 {candidate!r} 不属于本端点允许集合（§9.3："
                "salary_separate/salary_joint｜no_pa/pa_self/pa_joint）",
                field=f"{field}.candidates",
            )
        if candidate in candidates:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.candidates 不得重复：{candidate!r}",
                field=f"{field}.candidates",
            )
        candidates.append(candidate)
    selection = raw["selection"]
    if not isinstance(selection, str) or (
        selection != "best" and selection not in candidates
    ):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.selection 必须为 best 或显式候选之一（无条件必填）",
            field=f"{field}.selection",
        )
    canonical: dict[str, Any] = {
        "basis": basis,
        "candidates": candidates,
        "selection": selection,
    }
    if "reference_date" in raw:
        canonical["reference_date"] = _v2_date(
            raw["reference_date"], f"{field}.reference_date"
        )
    if "hypothetical_salary_joint_nomination" in raw:
        canonical["hypothetical_salary_joint_nomination"] = _v2_nomination(
            raw["hypothetical_salary_joint_nomination"],
            f"{field}.hypothetical_salary_joint_nomination",
            participants,
        )
    if "actual" in raw:
        canonical["actual"] = _v2_actual(
            raw["actual"], f"{field}.actual", participants=participants
        )
    return canonical


def _v2_actual(raw: Any, field: str, *, participants: set[str]) -> dict:
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为对象", field=field)
    _reject_v2_extra(raw, _V2_ACTUAL_KEYS, field)
    canonical: dict[str, Any] = {}
    for key in ("salary_joint_elected", "salary_joint_assessed"):
        if key in raw:
            canonical[key] = _v2_tri(raw[key], f"{field}.{key}")
    if "history" in raw:
        history = raw["history"]
        if history not in _V2_HISTORY_VALUES:
            raise AppError(
                "E_INPUT_TYPE",
                f"{field}.history 必须为 ordinary/withdrawn/disputed/unknown",
                field=f"{field}.history",
            )
        canonical["history"] = history
    if "salary_joint_nomination" in raw:
        canonical["salary_joint_nomination"] = _v2_nomination(
            raw["salary_joint_nomination"],
            f"{field}.salary_joint_nomination",
            participants,
        )
    if "pa" in raw:
        canonical["pa"] = _v2_actual_pa(raw["pa"], f"{field}.pa", participants=participants)
    return canonical


def _v2_actual_pa(raw: Any, field: str, *, participants: set[str]) -> dict:
    """§4.10 规则 7：actual.pa 稀疏 map（键只允许当年 participant；none 禁止
    date；individual/joint 的 date 提供即严格校验；joint 配对冲突拒绝）。"""
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为对象映射", field=field)
    entries: dict[str, dict] = {}
    for person_id, entry in raw.items():
        where = f"{field}.{person_id}"
        if person_id not in participants:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{where} 键只允许当年 participant（005 §4.10 规则 7）",
                field=where,
            )
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 必须为对象", field=where)
        _reject_v2_extra(entry, ("mode", "date"), where)
        if "mode" not in entry:
            raise AppError("E_INPUT_MISSING", f"{where}.mode 缺失", field=f"{where}.mode")
        mode = entry["mode"]
        if mode not in _V2_PA_MODES:
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.mode 必须为 none/individual/joint/unknown",
                field=f"{where}.mode",
            )
        canonical_entry: dict[str, Any] = {"mode": mode}
        if "date" in entry:
            if mode == "none":
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.date 禁止与 mode=none 同时出现（005 §4.10 规则 7）",
                    field=where,
                )
            canonical_entry["date"] = _v2_date(entry["date"], f"{where}.date")
        entries[person_id] = canonical_entry
    joint_dates = [
        entry["date"]
        for entry in entries.values()
        if entry["mode"] == "joint" and "date" in entry
    ]
    if len(set(joint_dates)) > 1:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：joint 双方日期不一致（不静默择一；005 §4.10 规则 5/7）",
            field=field,
        )
    conflicting = [
        person_id
        for person_id, entry in entries.items()
        if entry["mode"] in ("individual", "none")
    ]
    if any(entry["mode"] == "joint" for entry in entries.values()) and conflicting:
        # joint 与非 joint 显式并存 → 配对 mode 冲突（已提供者严格拒绝）
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：joint 选举须配对一致（已提供的 mode 冲突 → 拒绝；"
            "005 §4.10 规则 7）",
            field=field,
        )
    return entries


def _v2_subjects(raw: Any) -> tuple[list[dict], set[str]]:
    if not isinstance(raw, list) or not raw:
        raise AppError("E_INPUT_MISSING", "缺少 subjects[]（注册 subject）", field="subjects")
    _limit_array(raw, "subjects")
    subjects: list[dict] = []
    seen: set[str] = set()
    for index, subject in enumerate(raw):
        field = f"subjects[{index}]"
        if not isinstance(subject, Mapping):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        _reject_v2_extra(subject, _V2_SUBJECT_KEYS, field)
        if "subject_id" not in subject:
            raise AppError("E_INPUT_MISSING", f"{field}.subject_id 缺失", field=f"{field}.subject_id")
        subject_id = _v2_id(subject["subject_id"], f"{field}.subject_id", ("p", "d"))
        if subject_id in seen:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"subject ID 重复：{subject_id!r}（005 §3 ID 去重）",
                field=f"{field}.subject_id",
            )
        seen.add(subject_id)
        item: dict[str, Any] = {"subject_id": subject_id}
        if "birth_date" in subject:
            item["birth_date"] = _v2_date(subject["birth_date"], f"{field}.birth_date")
        subjects.append(item)
    return subjects, seen


def _v2_participants(
    raw: Any, field: str, subjects: set[str]
) -> tuple[list[dict], set[str]]:
    if not isinstance(raw, list) or not raw:
        raise AppError("E_INPUT_TYPE", f"{field} 必须为非空数组", field=field)
    participants: list[dict] = []
    person_ids: set[str] = set()
    self_ids: list[str] = []
    spouse_count = 0
    for index, participant in enumerate(raw):
        where = f"{field}[{index}]"
        if not isinstance(participant, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
        _reject_v2_extra(participant, _V2_PARTICIPANT_KEYS, where)
        if "person_id" not in participant or "role" not in participant:
            raise AppError(
                "E_INPUT_MISSING", f"{where} 缺少 person_id/role", field=where
            )
        person_id = _v2_id(participant["person_id"], f"{where}.person_id", ("p",))
        if person_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{where}.person_id 未在 subjects 注册：{person_id!r}",
                field=f"{where}.person_id",
            )
        if person_id in person_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"participant 重复：{person_id!r}",
                field=f"{where}.person_id",
            )
        person_ids.add(person_id)
        role = participant["role"]
        if role not in ("self", "spouse"):
            raise AppError(
                "E_INPUT_TYPE", f"{where}.role 必须为 self／spouse", field=f"{where}.role"
            )
        if role == "self":
            self_ids.append(person_id)
        else:
            spouse_count += 1
        participants.append({"person_id": person_id, "role": role})
    if len(self_ids) != 1:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field} 须恰 1 名 role=self（实际 {len(self_ids)}；005 §3）",
            field=field,
        )
    if spouse_count > 1:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field} 最多 1 名 role=spouse（实际 {spouse_count}；005 §3）",
            field=field,
        )
    return participants, person_ids


def _v2_subject_facts(
    raw: Any,
    field: str,
    *,
    subjects: set[str],
    participant_ids: set[str],
    year_domain: list[tuple[date, date]],
    reference_domain: list[tuple[date, date]],
    single_day_reference: bool,
) -> dict:
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为对象映射", field=field)
    canonical: dict[str, Any] = {}
    for subject_id, facts in raw.items():
        where = f"{field}.{subject_id}"
        if subject_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{where} 引用未注册 subject（005 §3 引用完整性）",
                field=where,
            )
        if not isinstance(facts, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 必须为对象", field=where)
        _reject_v2_extra(facts, _V2_SUBJECT_FACT_KEYS, where)
        item: dict[str, Any] = {}
        for key in _V2_SUBJECT_FACT_TF_KEYS:
            if key not in facts:
                continue
            if key == "unmarried" and subject_id in participant_ids:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{where}.unmarried 禁止对 participant 提交（婚姻事实由 "
                    "household 派生；005 §4.3）",
                    field=f"{where}.unmarried",
                )
            item[key] = _v2_tf(
                facts[key],
                f"{where}.{key}",
                domain=(
                    reference_domain
                    if key in _V2_EXTENDABLE_TF_KEYS
                    else year_domain
                ),
                single_day_extension=(
                    single_day_reference and key in _V2_EXTENDABLE_TF_KEYS
                ),
            )
        if "death_date" in facts:
            item["death_date"] = _v2_date(facts["death_date"], f"{where}.death_date")
        canonical[subject_id] = item
    return canonical


def _v2_finance(
    raw: Any,
    field: str,
    *,
    year_domain: list[tuple[date, date]],
    subjects: set[str],
) -> dict:
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 必须为对象", field=field)
    _reject_v2_extra(raw, _V2_FINANCE_KEYS, field)
    canonical: dict[str, Any] = {}
    for key in ("employment_income_nai", "mpf_mandatory_contributions"):
        if key in raw:
            canonical[key] = _v2_money(raw[key], f"{field}.{key}")
    for key in (
        "has_assessable_salary_income",
        "has_assessable_property_income",
        "has_assessable_business_income",
        "has_income_assessable_under_ordinance",
    ):
        if key in raw:
            canonical[key] = _v2_tri(raw[key], f"{field}.{key}")
    if "source_classification" in raw:
        source = raw["source_classification"]
        if not isinstance(source, Mapping):
            raise AppError(
                "E_INPUT_TYPE",
                f"{field}.source_classification 必须为对象",
                field=f"{field}.source_classification",
            )
        _reject_v2_extra(source, ("salary", "property", "business"), f"{field}.source_classification")
        canonical_source: dict[str, str] = {}
        for source_key, value in source.items():
            if value not in ("confirmed_in_scope", "unknown", "manual"):
                raise AppError(
                    "E_INPUT_TYPE",
                    f"{field}.source_classification.{source_key} 必须为 "
                    "confirmed_in_scope／unknown／manual",
                    field=f"{field}.source_classification.{source_key}",
                )
            canonical_source[source_key] = value
        canonical["source_classification"] = canonical_source
    if "properties" in raw:
        properties = raw["properties"]
        if not isinstance(properties, list):
            raise AppError("E_INPUT_TYPE", f"{field}.properties 必须为数组", field=f"{field}.properties")
        canonical["properties"] = [
            _v2_property(entry, f"{field}.properties[{index}]")
            for index, entry in enumerate(properties)
        ]
    if "businesses" in raw:
        businesses = raw["businesses"]
        if not isinstance(businesses, list):
            raise AppError("E_INPUT_TYPE", f"{field}.businesses 必须为数组", field=f"{field}.businesses")
        canonical["businesses"] = [
            _v2_business(entry, f"{field}.businesses[{index}]")
            for index, entry in enumerate(businesses)
        ]
    if "deduction_items" in raw:
        items = raw["deduction_items"]
        if not isinstance(items, list):
            raise AppError(
                "E_INPUT_TYPE", f"{field}.deduction_items 必须为数组", field=f"{field}.deduction_items"
            )
        canonical["deduction_items"] = [
            _v2_deduction_item(entry, f"{field}.deduction_items[{index}]", subjects=subjects)
            for index, entry in enumerate(items)
        ]
    # E3：005 §6.1 来源存在性交叉约束（单变量反例矩阵；与引擎 Facts 同 code，
    # 但先行于 guard 短路）。仅显式值参与判定（缺失≠false）。
    nai_text = canonical.get("employment_income_nai")
    ai = canonical.get("has_assessable_salary_income")
    property_present = canonical.get("has_assessable_property_income")
    business_present = canonical.get("has_assessable_business_income")
    all_source = canonical.get("has_income_assessable_under_ordinance")
    if ai is False and nai_text is not None and Decimal(nai_text) > 0:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：正 NAI 与 has_assessable_salary_income=false 并提"
            "（三口径分离，不得由 NAI=0 或正数互相推断）",
            field=f"{field}.has_assessable_salary_income",
        )
    if (
        property_present is False
        and isinstance(canonical.get("properties"), list)
        and canonical["properties"]
    ):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：已提交物业组件与 has_assessable_property_income=false 并提"
            "（005 §6.1 来源存在性矛盾）",
            field=f"{field}.has_assessable_property_income",
        )
    if (
        business_present is False
        and isinstance(canonical.get("businesses"), list)
        and canonical["businesses"]
    ):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：已提交业务组件与 has_assessable_business_income=false 并提"
            "（005 §6.1 来源存在性矛盾）",
            field=f"{field}.has_assessable_business_income",
        )
    if all_source is False and (
        ai is True or property_present is True or business_present is True
    ):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：已确认来源存在与 has_income_assessable_under_ordinance=false "
            "并提（005 §6.1 来源存在性矛盾）",
            field=f"{field}.has_income_assessable_under_ordinance",
        )
    if (
        all_source is True
        and ai is False
        and property_present is False
        and business_present is False
    ):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}：全来源存在=true 与三来源均=false 并提"
            "（005 §6.1 来源存在性矛盾）",
            field=f"{field}.has_income_assessable_under_ordinance",
        )
    return canonical


def _v2_property(entry: Any, field: str) -> dict:
    if not isinstance(entry, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(entry, _V2_PROPERTY_KEYS, field)
    for required in ("property_id", "share", "rent_received"):
        if required not in entry:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
    canonical: dict[str, Any] = {
        "property_id": _v2_id(entry["property_id"], f"{field}.property_id", ("h",)),
        "share": _v2_ratio(entry["share"], f"{field}.share"),
        "rent_received": _v2_money(entry["rent_received"], f"{field}.rent_received"),
    }
    for key in ("irrecoverable_rent", "deposit_offsets", "mortgage_interest"):
        if key in entry:
            canonical[key] = _v2_money(entry[key], f"{field}.{key}")
    if "rates_paid_by_owner" in entry:
        rates = entry["rates_paid_by_owner"]
        where = f"{field}.rates_paid_by_owner"
        if not isinstance(rates, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 必须为对象", field=where)
        _reject_v2_extra(rates, ("amount", "agreed", "actually_paid"), where)
        canonical_rates: dict[str, Any] = {}
        if "amount" in rates:
            canonical_rates["amount"] = _v2_money(rates["amount"], f"{where}.amount")
        if "agreed" in rates:
            canonical_rates["agreed"] = _v2_tri(rates["agreed"], f"{where}.agreed")
        if "actually_paid" in rates:
            canonical_rates["actually_paid"] = _v2_tri(
                rates["actually_paid"], f"{where}.actually_paid"
            )
        canonical["rates_paid_by_owner"] = canonical_rates
    return canonical


def _v2_business(entry: Any, field: str) -> dict:
    if not isinstance(entry, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(entry, _V2_BUSINESS_KEYS, field)
    for required in ("business_id", "entity_kind", "assessable_profit", "share"):
        if required not in entry:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
    kind = entry["entity_kind"]
    if kind not in ("sole_proprietorship", "partnership"):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.entity_kind 必须为 sole_proprietorship／partnership",
            field=f"{field}.entity_kind",
        )
    return {
        "business_id": _v2_id(entry["business_id"], f"{field}.business_id", ("b",)),
        "entity_kind": kind,
        "assessable_profit": _v2_money(
            entry["assessable_profit"], f"{field}.assessable_profit"
        ),
        "share": _v2_ratio(entry["share"], f"{field}.share"),
    }


def _v2_deduction_item(entry: Any, field: str, *, subjects: set[str]) -> dict:
    if not isinstance(entry, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(entry, _V2_DEDUCTION_KEYS, field)
    for required in ("item_type", "amount"):
        if required not in entry:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
    item_type = entry["item_type"]
    if not isinstance(item_type, str) or not item_type:
        raise AppError(
            "E_INPUT_TYPE", f"{field}.item_type 必须为非空字符串", field=f"{field}.item_type"
        )
    amount = _v2_money(entry["amount"], f"{field}.amount")
    # 单源常量：在册扣除枚举由 app/rules/bundles/salaries.py 维护（其文档声明
    # 「唯一来源；引擎与 prepare 校验共用」）。此处按需（惰性）导入：避免核心
    # 模块级规则依赖，同时不复制常量造成漂移；该模块仅含静态数值表，无运行时
    # 取束/网络（依赖方向注记：core → rules 静态常量，仅供结构枚举判定）。
    from app.rules.bundles.salaries import SUPPORTED_DEDUCTION_ITEM_TYPES

    if item_type not in SUPPORTED_DEDUCTION_ITEM_TYPES and Decimal(amount) > 0:
        raise AppError(
            "E_DEDUCTION_UNSUPPORTED",
            f"扣除项 {item_type} 不在薪俸税可扣除附表内（逐项枚举，无 catch-all）；"
            "正数申索须拒算受影响税项（REQ-10）。",
            field=field,
        )
    canonical: dict[str, Any] = {"item_type": item_type, "amount": amount}
    if "subject_id" in entry:
        canonical["subject_id"] = _v2_id(
            entry["subject_id"], f"{field}.subject_id", ("p", "d")
        )
        if canonical["subject_id"] not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.subject_id 未注册（005 §3 引用完整性）",
                field=f"{field}.subject_id",
            )
    if "payment_ids" in entry:
        payment_ids = entry["payment_ids"]
        if not isinstance(payment_ids, list):
            raise AppError(
                "E_INPUT_TYPE", f"{field}.payment_ids 必须为数组", field=f"{field}.payment_ids"
            )
        canonical["payment_ids"] = [
            _v2_id(pid, f"{field}.payment_ids[{index}]", ("e",))
            for index, pid in enumerate(payment_ids)
        ]
    if "eligibility" in entry:
        # 005 §6.2：legacy {value:Tri} 包装仅扣除项允许；unknown 保持三态交引擎
        # 年度/候选局部处理（缺失≠false；不得在结构层吞掉或全局清空）。
        eligibility = entry["eligibility"]
        where = f"{field}.eligibility"
        if not isinstance(eligibility, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 必须是对象", field=where)
        _reject_v2_extra(eligibility, ("value",), where)
        if "value" not in eligibility:
            raise AppError("E_INPUT_MISSING", f"{where}.value 缺失", field=f"{where}.value")
        value = eligibility["value"]
        if value is not True and value is not False and value != "unknown":
            raise AppError(
                "E_INPUT_TYPE",
                f'{where}.value 必须为 true／false／"unknown"',
                field=where,
            )
        canonical["eligibility"] = {"value": value}
    return canonical


def _v2_care_payment(entry: Any, field: str, *, subjects: set[str]) -> dict:
    if not isinstance(entry, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(entry, _V2_CARE_PAYMENT_KEYS, field)
    for required in _V2_CARE_PAYMENT_KEYS:
        if required not in entry:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
    payer = _v2_id(entry["payer_id"], f"{field}.payer_id", ("p",))
    subject = _v2_id(entry["subject_id"], f"{field}.subject_id", ("p", "d"))
    if payer not in subjects or subject not in subjects:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field} 的 payer_id/subject_id 须已注册（005 §3）",
            field=field,
        )
    care_from = date.fromisoformat(_v2_date(entry["care_from"], f"{field}.care_from"))
    care_to = date.fromisoformat(_v2_date(entry["care_to"], f"{field}.care_to"))
    if care_from >= care_to:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 须满足 care_from<care_to", field=field
        )
    home_basis = entry["home_basis"]
    if home_basis not in _V2_HOME_BASES:
        raise AppError(
            "E_INPUT_TYPE", f"{field}.home_basis 非法（005 §4.8 枚举）", field=f"{field}.home_basis"
        )
    canonical: dict[str, Any] = {
        "payment_id": _v2_id(entry["payment_id"], f"{field}.payment_id", ("e",)),
        "payer_id": payer,
        "subject_id": subject,
        "paid_on": _v2_date(entry["paid_on"], f"{field}.paid_on"),
        "amount": _v2_money(entry["amount"], f"{field}.amount"),
        "care_from": entry["care_from"],
        "care_to": entry["care_to"],
        "residential_care_received": _v2_tri(
            entry["residential_care_received"], f"{field}.residential_care_received"
        ),
        "paid_to_home_or_agent": _v2_tri(
            entry["paid_to_home_or_agent"], f"{field}.paid_to_home_or_agent"
        ),
        "home_basis": home_basis,
        # §7 例外：合法域＝该付款自身 [care_from, care_to)（可完整落在键年之前）
        "home_status_in_force": _v2_tf(
            entry["home_status_in_force"],
            f"{field}.home_status_in_force",
            domain=[(care_from, care_to)],
        ),
    }
    return canonical


def _v2_claim(entry: Any, field: str, *, subjects: set[str], participants: set[str]) -> dict:
    if not isinstance(entry, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(entry, _V2_CLAIM_KEYS, field)
    for required in ("claim_id", "kind", "claimant_id", "subject_id"):
        if required not in entry:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
    kind = entry["kind"]
    if kind not in _V2_CLAIM_KINDS:
        raise AppError("E_INPUT_TYPE", f"{field}.kind 非法（005 §4.9）", field=f"{field}.kind")
    claimant = _v2_id(entry["claimant_id"], f"{field}.claimant_id", ("p",))
    if claimant not in participants:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}.claimant_id 必须引用当年 participant（005 §4.9）",
            field=f"{field}.claimant_id",
        )
    subject = _v2_id(entry["subject_id"], f"{field}.subject_id", ("p", "d"))
    if subject not in subjects:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}.subject_id 未注册（005 §3）",
            field=f"{field}.subject_id",
        )
    canonical: dict[str, Any] = {
        "claim_id": _v2_id(entry["claim_id"], f"{field}.claim_id", ("c",)),
        "kind": kind,
        "claimant_id": claimant,
        "subject_id": subject,
    }
    if "special_situation" in entry:
        canonical["special_situation"] = _v2_tri(
            entry["special_situation"], f"{field}.special_situation"
        )
    return canonical


def _v2_manual_issue(entry: Any, field: str) -> dict:
    if not isinstance(entry, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(entry, _V2_MANUAL_KEYS, field)
    for required in _V2_MANUAL_KEYS:
        if required not in entry:
            raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
    scope = entry["scope"]
    if scope not in _V2_MANUAL_SCOPES:
        raise AppError("E_INPUT_TYPE", f"{field}.scope 非法", field=f"{field}.scope")
    code = entry["code"]
    if code not in _V2_MANUAL_CODES:
        raise AppError("E_INPUT_TYPE", f"{field}.code 非法", field=f"{field}.code")
    issue_id = entry["id"]
    if not isinstance(issue_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", issue_id):
        raise AppError("E_INPUT_TYPE", f"{field}.id 必须为 ID 形式", field=f"{field}.id")
    return {"scope": scope, "id": issue_id, "code": code}


def _v2_year(
    raw: Any,
    field: str,
    *,
    year_key: str,
    tax_type: str,
    subjects: set[str],
) -> dict:
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    _reject_v2_extra(raw, _V2_YEAR_KEYS, field)
    for required in _V2_YEAR_REQUIRED_KEYS:
        _require_v2_key(raw, required, field)

    # §7：允许窗口＝本 Year 自身窗口＋本 Year 已声明的参考日/选择日一天区间。
    year_domain = [_canonical_year_bounds(year_key)]
    extra_dates = _v2_assessment_extra_dates(raw.get("assessment"))
    reference_domain = year_domain + [
        (extra, extra + timedelta(days=1)) for extra in extra_dates
    ]
    # 未声明任何参考/选择日：列明谓词保留单日区间兼容（既有冻结夹具形状；
    # 一经声明则仅开放所声明日期的一天区间）。
    single_day_reference = not extra_dates

    participants, participant_ids = _v2_participants(
        raw["participants"], f"{field}.participants", subjects
    )

    # household（§4.4）
    household_raw = raw["household"]
    if not isinstance(household_raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field}.household 必须为对象", field=f"{field}.household")
    _reject_v2_extra(household_raw, _V2_HOUSEHOLD_KEYS, f"{field}.household")
    for required in _V2_HOUSEHOLD_KEYS:
        if required not in household_raw:
            raise AppError(
                "E_INPUT_MISSING",
                f"{field}.household 缺少 {required}",
                field=f"{field}.household.{required}",
            )
    marriage = _v2_tf(
        household_raw["marriage"],
        f"{field}.household.marriage",
        domain=reference_domain,
        kind="marriage",
        single_day_extension=single_day_reference,
    )
    self_id = next(
        (entry["person_id"] for entry in participants if entry["role"] == "self"), None
    )
    for segment in marriage:
        spouse_id = segment.get("spouse_id")
        if spouse_id is not None:
            if spouse_id not in subjects:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{field}.household.marriage.spouse_id 未注册：{spouse_id!r}",
                    field=f"{field}.household.marriage",
                )
            if spouse_id == self_id and spouse_id in participant_ids:
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{field}.household.marriage.spouse_id 不得为本人",
                    field=f"{field}.household.marriage",
                )
    living_apart = _v2_tf(
        household_raw["living_apart"],
        f"{field}.household.living_apart",
        domain=reference_domain,
        single_day_extension=single_day_reference,
    )
    child_nomination_raw = household_raw["child_nomination"]
    if not isinstance(child_nomination_raw, Mapping):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.household.child_nomination 必须为对象",
            field=f"{field}.household.child_nomination",
        )
    _reject_v2_extra(
        child_nomination_raw,
        ("person_id", "status"),
        f"{field}.household.child_nomination",
    )
    if "status" not in child_nomination_raw or "person_id" not in child_nomination_raw:
        raise AppError(
            "E_INPUT_MISSING",
            f"{field}.household.child_nomination 缺少 status/person_id",
            field=f"{field}.household.child_nomination",
        )
    nomination_status = child_nomination_raw["status"]
    if nomination_status not in ("none", "agreed", "disputed", "unknown"):
        raise AppError(
            "E_INPUT_TYPE",
            f"{field}.household.child_nomination.status 非法（005 §4.4）",
            field=f"{field}.household.child_nomination.status",
        )
    nomination_person = child_nomination_raw["person_id"]
    if nomination_status == "agreed":
        nominee = _v2_id(
            nomination_person,
            f"{field}.household.child_nomination.person_id",
            ("p",),
        )
        if nominee not in participant_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.household.child_nomination.person_id 须为当年 participant",
                field=f"{field}.household.child_nomination.person_id",
            )
        canonical_nomination: dict[str, Any] = {
            "person_id": nominee,
            "status": "agreed",
        }
    else:
        if nomination_person is not None:
            raise AppError(
                "E_INPUT_TYPE",
                f"{field}.household.child_nomination.person_id 必须为 null"
                "（非 agreed status）",
                field=f"{field}.household.child_nomination.person_id",
            )
        canonical_nomination = {"person_id": None, "status": nomination_status}

    # subject_facts（§4.3）
    subject_facts = _v2_subject_facts(
        raw["subject_facts"],
        f"{field}.subject_facts",
        subjects=subjects,
        participant_ids=participant_ids,
        year_domain=year_domain,
        reference_domain=reference_domain,
        single_day_reference=single_day_reference,
    )

    # relations（§4.5）
    relations_raw = raw["relations"]
    if not isinstance(relations_raw, list):
        raise AppError("E_INPUT_TYPE", f"{field}.relations 必须为数组", field=f"{field}.relations")
    _limit_array(relations_raw, f"{field}.relations")
    relations: list[dict] = []
    relation_ids: set[str] = set()
    actor_subject: set[tuple[str, str]] = set()
    for index, entry in enumerate(relations_raw):
        where = f"{field}.relations[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
        _reject_v2_extra(entry, _V2_RELATION_KEYS, where)
        for required in ("relation_id", "actor_id", "subject_id"):
            if required not in entry:
                raise AppError("E_INPUT_MISSING", f"{where} 缺少 {required}", field=f"{where}.{required}")
        relation_id = _v2_id(entry["relation_id"], f"{where}.relation_id", ("r",))
        if relation_id in relation_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"relation_id 重复：{relation_id!r}", field=where
            )
        relation_ids.add(relation_id)
        actor = _v2_id(entry["actor_id"], f"{where}.actor_id", ("p",))
        subject = _v2_id(entry["subject_id"], f"{where}.subject_id", ("p", "d"))
        if actor not in subjects or subject not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{where} 引用未注册 subject", field=where
            )
        if (actor, subject) in actor_subject:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"每年 (actor_id, subject_id) 唯一：({actor!r}, {subject!r})",
                field=where,
            )
        actor_subject.add((actor, subject))
        canonical_relation: dict[str, Any] = {
            "relation_id": relation_id,
            "actor_id": actor,
            "subject_id": subject,
        }
        if "kinship" in entry:
            kinship = entry["kinship"]
            where_k = f"{where}.kinship"
            if not isinstance(kinship, Mapping):
                raise AppError("E_INPUT_TYPE", f"{where_k} 必须为对象", field=where_k)
            _reject_v2_extra(kinship, ("kind", "basis", "during"), where_k)
            if "kind" not in kinship or "basis" not in kinship:
                raise AppError(
                    "E_INPUT_MISSING", f"{where_k} 缺少 kind/basis", field=where_k
                )
            if kinship["kind"] not in _V2_KINSHIP_KINDS:
                raise AppError(
                    "E_INPUT_TYPE", f"{where_k}.kind 非法（005 §4.5）", field=f"{where_k}.kind"
                )
            if kinship["basis"] not in _V2_KINSHIP_BASES:
                raise AppError(
                    "E_INPUT_TYPE", f"{where_k}.basis 非法（005 §4.5）", field=f"{where_k}.basis"
                )
            canonical_kinship: dict[str, Any] = {
                "kind": kinship["kind"],
                "basis": kinship["basis"],
            }
            if "during" in kinship:
                canonical_kinship["during"] = _v2_tf(
                    kinship["during"], f"{where_k}.during", domain=year_domain
                )
            canonical_relation["kinship"] = canonical_kinship
        for key in ("maintained", "cohabit_non_full_consideration", "sole_or_predominant_care"):
            if key in entry:
                canonical_relation[key] = _v2_tf(
                    entry[key], f"{where}.{key}", domain=year_domain
                )
        if "cash_maintenance" in entry:
            canonical_relation["cash_maintenance"] = _v2_money(
                entry["cash_maintenance"], f"{where}.cash_maintenance"
            )
        relations.append(canonical_relation)

    # financials（§4.6：映射键仅当年 participant）
    financials_raw = raw["financials"]
    if not isinstance(financials_raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field}.financials 必须为对象映射", field=f"{field}.financials")
    financials: dict[str, dict] = {}
    for person_id, finance in financials_raw.items():
        if person_id not in participant_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.financials.{person_id} 键只允许当年 participant（005 §4.6）",
                field=f"{field}.financials.{person_id}",
            )
        financials[person_id] = _v2_finance(
            finance,
            f"{field}.financials.{person_id}",
            year_domain=year_domain,
            subjects=subjects,
        )

    # care_payments（§4.8）
    care_raw = raw["care_payments"]
    if not isinstance(care_raw, list):
        raise AppError("E_INPUT_TYPE", f"{field}.care_payments 必须为数组", field=f"{field}.care_payments")
    _limit_array(care_raw, f"{field}.care_payments")
    care_payments: list[dict] = []
    payment_ids: set[str] = set()
    for index, entry in enumerate(care_raw):
        canonical_care = _v2_care_payment(
            entry, f"{field}.care_payments[{index}]", subjects=subjects
        )
        if canonical_care["payment_id"] in payment_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"payment_id 重复：{canonical_care['payment_id']!r}",
                field=f"{field}.care_payments[{index}].payment_id",
            )
        payment_ids.add(canonical_care["payment_id"])
        care_payments.append(canonical_care)

    # claims（§4.9）
    claims_raw = raw["claims"]
    if not isinstance(claims_raw, list):
        raise AppError("E_INPUT_TYPE", f"{field}.claims 必须为数组", field=f"{field}.claims")
    _limit_array(claims_raw, f"{field}.claims")
    claims: list[dict] = []
    claim_ids: set[str] = set()
    # C4/R-E57：规则无关的 ledger 重复键（同 (kind,claimant,subject)）属于
    # 结构对账，必须在共享 normalize 先验（覆盖全部显式 Year），不得等到
    # 引擎运行期 parse_year —— 否则 current guard 早退会遮蔽该矛盾。
    ledger_keys: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(claims_raw):
        canonical_claim = _v2_claim(
            entry,
            f"{field}.claims[{index}]",
            subjects=subjects,
            participants=participant_ids,
        )
        if canonical_claim["claim_id"] in claim_ids:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"claim_id 重复：{canonical_claim['claim_id']!r}",
                field=f"{field}.claims[{index}].claim_id",
            )
        claim_ids.add(canonical_claim["claim_id"])
        ledger_key = (
            canonical_claim["kind"],
            canonical_claim["claimant_id"],
            canonical_claim["subject_id"],
        )
        if ledger_key in ledger_keys:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.claims[{index}] 同 (kind,claimant,subject) 重复条目：{ledger_key!r}",
                field=f"{field}.claims[{index}]",
            )
        ledger_keys.add(ledger_key)
        claims.append(canonical_claim)

    # assessment（§4.10）
    assessment = _v2_assessment(
        raw["assessment"],
        f"{field}.assessment",
        tax_type=tax_type,
        participants=participant_ids,
    )

    canonical_year: dict[str, Any] = {
        "participants": participants,
        "subject_facts": subject_facts,
        "household": {
            "marriage": marriage,
            "living_apart": living_apart,
            "child_nomination": canonical_nomination,
        },
        "relations": relations,
        "financials": financials,
        "care_payments": care_payments,
        "claims": claims,
        "assessment": assessment,
    }
    # manual_issues：可选容器；省略保持省略（不得静默补 [] 后同 hash）。
    if "manual_issues" in raw:
        manual_raw = raw["manual_issues"]
        if not isinstance(manual_raw, list):
            raise AppError(
                "E_INPUT_TYPE",
                f"{field}.manual_issues 必须为数组",
                field=f"{field}.manual_issues",
            )
        _limit_array(manual_raw, f"{field}.manual_issues")
        parsed_issues: list[dict] = []
        for index, entry in enumerate(manual_raw):
            issue = _v2_manual_issue(entry, f"{field}.manual_issues[{index}]")
            # E4/CR-E：election 事项仅接受**同一 Year participant** 的 pID
            # （不新增 election ID 类型；跨年引用＝已证矛盾）。
            if (
                issue["scope"] == "election"
                and issue["id"] not in participant_ids
            ):
                raise AppError(
                    "E_INPUT_CONTRADICTORY",
                    f"{field}.manual_issues[{index}].id 必须引用同一 Year participants "
                    "的 pID（CR-E：election 事项定位本年度选举参与者）",
                    field=f"{field}.manual_issues[{index}].id",
                )
            parsed_issues.append(issue)
        canonical_year["manual_issues"] = parsed_issues
    if "provisional_paid" in raw:
        canonical_year["provisional_paid"] = _v2_money(
            raw["provisional_paid"], f"{field}.provisional_paid"
        )
    return canonical_year


def _v2_presence_days(
    raw: Any,
    allowed_years: dict[str, tuple[date, date]],
    subjects: set[str],
) -> list[dict]:
    """presence 辅助年：显式 Year 的 {Y−1,Y,Y+1} 并集；显式 null → TYPE。

    person_id 须已注册（005 §3 引用完整性；与引擎侧 Facts 同口径，中立层先行
    核验以避免 guard 短路跳过结构校验）。
    """
    if raw is None:
        raise AppError(
            "E_INPUT_TYPE",
            "hk_presence_days 显式 null 非法（null≠省略；005 §3/§4.7）",
            field="hk_presence_days",
        )
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", "hk_presence_days 必须为数组", field="hk_presence_days")
    _limit_array(raw, "hk_presence_days")
    canonical: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw):
        field = f"hk_presence_days[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        _reject_v2_extra(entry, ("person_id", "year", "days"), field)
        for required in ("person_id", "year", "days"):
            if required not in entry:
                raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
        person_id = _v2_id(entry["person_id"], f"{field}.person_id", ("p",))
        if person_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}.person_id 未注册（005 §3 引用完整性）",
                field=f"{field}.person_id",
            )
        year_key = entry["year"]
        if not isinstance(year_key, str) or year_key not in allowed_years:
            raise AppError(
                "E_INPUT_EXTRA",
                f"{field}.year 允许年份＝显式 Year 的 {{Y−1,Y,Y+1}} 并集（005 §4.7）",
                field=f"{field}.year",
            )
        if (person_id, year_key) in seen:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"每人/年唯一日数记录重复：({person_id!r}, {year_key!r})",
                field=field,
            )
        seen.add((person_id, year_key))
        days = entry["days"]
        if days == "unknown":
            canonical_days: Any = "unknown"
        else:
            if isinstance(days, bool) or not isinstance(days, int):
                raise AppError(
                    "E_INPUT_TYPE",
                    f'{field}.days 必须为整数 0..该 YA 日数或 "unknown"（bool 明确排除）',
                    field=f"{field}.days",
                )
            start, end = allowed_years[year_key]
            max_days = (end - start).days
            if days < 0 or days > max_days:
                raise AppError(
                    "E_INPUT_TYPE",
                    f"{field}.days 必须在 0..{max_days} 范围内",
                    field=f"{field}.days",
                )
            canonical_days = days
        canonical.append(
            {"person_id": person_id, "year": year_key, "days": canonical_days}
        )
    return canonical


def _v2_assumptions(
    raw: Any,
    *,
    years: Mapping[str, Any],
    subjects: set[str],
) -> list[dict]:
    if raw is None:
        raise AppError(
            "E_INPUT_TYPE",
            "assumptions 显式 null 非法（null≠省略；005 §3/§11）",
            field="assumptions",
        )
    if not isinstance(raw, list):
        raise AppError("E_INPUT_TYPE", "assumptions 必须为数组", field="assumptions")
    canonical: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw):
        field = f"assumptions[{index}]"
        if not isinstance(entry, Mapping):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        _reject_v2_extra(entry, ("assumption_id", "kind", "person_id", "source_year", "target_year"), field)
        for required in ("assumption_id", "kind", "person_id", "source_year", "target_year"):
            if required not in entry:
                raise AppError("E_INPUT_MISSING", f"{field} 缺少 {required}", field=f"{field}.{required}")
        assumption_id = _v2_id(entry["assumption_id"], f"{field}.assumption_id", ("a",))
        kind = entry["kind"]
        if kind not in _V2_ASSUMPTION_KINDS:
            raise AppError(
                "E_INPUT_TYPE", f"{field}.kind 必须为 same_income／same_mpf（005 §11）", field=f"{field}.kind"
            )
        person_id = _v2_id(entry["person_id"], f"{field}.person_id", ("p",))
        if person_id not in subjects:
            raise AppError(
                "E_INPUT_CONTRADICTORY", f"{field}.person_id 未注册", field=f"{field}.person_id"
            )
        source_year = entry["source_year"]
        target_year = entry["target_year"]
        # R4：先做完整年度键词法，再做「显式 years 内紧邻年度对」关系校验；
        # 任何畸形源/目标年度须结构化拒绝，不得泄漏 int(year[:4]) ValueError。
        for year_value, year_field in (
            (source_year, f"{field}.source_year"),
            (target_year, f"{field}.target_year"),
        ):
            if not isinstance(year_value, str) or _YEAR_KEY_RE.fullmatch(year_value) is None:
                raise AppError(
                    "E_INPUT_TYPE",
                    f"{year_field} 必须为 YYYY_YY 年度键（005 §11）",
                    field=year_field,
                )
        if (
            source_year not in years
            or target_year not in years
            or _following_year(source_year) != target_year
        ):
            raise AppError(
                "E_INPUT_TYPE",
                f"{field}：source_year 与 target_year 必须为请求显式 years 中的紧邻年度对"
                "（005 §11）",
                field=field,
            )
        key = (person_id, kind)
        if key in seen:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"每人每 kind 至多一条假设：{key!r}（005 §11）",
                field=field,
            )
        seen.add(key)
        # §11：显式目标金额与假设异额 → E_INPUT_CONTRADICTORY（完整目标 field；
        # 不采用最后写入者；输入先验先于年度 guard/next 抑制）。
        source_finance = None
        if isinstance(years.get(source_year), Mapping):
            source_finance = (years[source_year].get("financials") or {}).get(person_id)
        target_finance = None
        if isinstance(years.get(target_year), Mapping):
            target_finance = (years[target_year].get("financials") or {}).get(person_id)
        value_key = (
            "employment_income_nai" if kind == "same_income" else "mpf_mandatory_contributions"
        )
        if (
            isinstance(source_finance, Mapping)
            and isinstance(target_finance, Mapping)
            and value_key in source_finance
            and value_key in target_finance
            and source_finance[value_key] != target_finance[value_key]
        ):
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"{field}：显式目标金额与 same_income/same_mpf 假设异额"
                f"（{value_key}；005 §11，不采用最后写入者优先）",
                field=f"years.{target_year}.financials.{person_id}.{value_key}",
            )
        canonical.append(
            {
                "assumption_id": assumption_id,
                "kind": kind,
                "person_id": person_id,
                "source_year": source_year,
                "target_year": target_year,
            }
        )
    return canonical


def normalize_v2_facts(raw: Any, *, tax_type: str) -> dict:
    """唯一 v2 结构权威：raw（input，非 envelope）→ canonical v2 input dict。

    无规则/网络依赖；不注入默认值；缺失≠零；资格 unknown 保持年度/候选局部。
    HTTP 信封校验与两纯引擎入口共用本函数（E2；005 §3–§7）。
    """
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", "input 必须是 JSON 对象", field="input")
    extras = [key for key in raw if key not in _V2_INPUT_KEYS]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"input 含未知字段：{extras[0]}", field=extras[0]
        )
    if "year_of_assessment" not in raw:
        raise AppError(
            "E_INPUT_MISSING", "缺少 year_of_assessment", field="year_of_assessment"
        )
    year = _canonical_year(raw)
    years = raw.get("years")
    if not isinstance(years, Mapping):
        raise AppError("E_INPUT_MISSING", "缺少 years 对象", field="years")
    if year not in years:
        raise AppError(
            "E_INPUT_MISSING", f"缺少目标年 years.{year}", field=f"years.{year}"
        )
    following = _following_year(year)
    for key, value in years.items():
        if key not in (year, following):
            raise AppError(
                "E_INPUT_EXTRA",
                f"years 键 {key!r} 不受理（仅目标年＋紧邻下一年键）",
                field=f"years.{key}",
            )
        if not isinstance(value, Mapping):
            raise AppError("E_INPUT_TYPE", f"years.{key} 须为对象", field=f"years.{key}")

    subjects, subject_ids = _v2_subjects(raw.get("subjects"))

    canonical_years: dict[str, Any] = {}
    for key in years:
        canonical_years[key] = _v2_year(
            years[key],
            f"years.{key}",
            year_key=key,
            tax_type=tax_type,
            subjects=subject_ids,
        )
    # 跨年 self 稳定（005 §3）：同一 self 的 pID 不得逐年变化。
    seen_self: str | None = None
    for key in years:
        self_id = next(
            (
                entry["person_id"]
                for entry in canonical_years[key]["participants"]
                if entry["role"] == "self"
            ),
            None,
        )
        if self_id is None:
            continue
        if seen_self is not None and seen_self != self_id:
            raise AppError(
                "E_INPUT_CONTRADICTORY",
                f"years.{key}：self 的 pID 须跨年稳定（{seen_self!r} ≠ {self_id!r}；005 §3）",
                field=f"years.{key}.participants",
            )
        seen_self = self_id

    # R3：presence 辅助域＝**实际显式 years** 的 {Y−1,Y,Y+1} 并集（非固定
    # (year, following)）；仅当前年为显式时不得接受 Y+2。
    presence_span: dict[str, tuple[date, date]] = {}
    for key in years:
        start = int(key[:4])
        for delta in (-1, 0, 1):
            span_key = f"{start + delta}_{(start + delta + 1) % 100:02d}"
            if span_key not in presence_span:
                presence_span[span_key] = _canonical_year_bounds(span_key)

    canonical: dict[str, Any] = {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": canonical_years,
    }
    # 根级可选辅助数组：省略保持省略（不得静默补 [] 后与显式 [] 同 hash）；
    # 显式 null → E_INPUT_TYPE（函数内拒绝）。
    if "hk_presence_days" in raw:
        canonical["hk_presence_days"] = _v2_presence_days(
            raw["hk_presence_days"], presence_span, subject_ids
        )
    if "assumptions" in raw:
        canonical["assumptions"] = _v2_assumptions(
            raw["assumptions"],
            years=canonical_years,
            subjects=subject_ids,
        )
    return copy.deepcopy(canonical)
