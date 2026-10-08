"""prepare 请求结构校验（Annex C C3.5 字段名即契约；C2.9 rank 非权威输入）。

六个已批准税项全部开放 prepare（salaries／profits／property／
personal_assessment／stamp property／stock／lease）；判定顺序＝缺失 → 额外 →
类型 → 期间（C3.7 各码）。资格类字段三态（缺失≠显式值≠unknown）：
unknown 一律放行至引擎按 needs_input 追问（薪俸 married_status 除外——
C3.7 在 prepare 以 E_ELIGIBILITY_UNKNOWN＋questions 拒绝）；不支持扣除
item_type 正数申索 → E_DEDUCTION_UNSUPPORTED（REQ-10 双保险第一层）。
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.config import SUPPORTED_YEARS
from app.core.errors import AppError
from app.core.money import parse_amount, parse_ratio
from app.rules.bundles.salaries import SUPPORTED_DEDUCTION_ITEM_TYPES

_SCHEMA_VERSION = "1.0.0"
SALARIES_TAX_TYPE = "salaries_tax"

_YEAR_KEY_RE = re.compile(r"[0-9]{4}_[0-9]{2}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_SALARIES_REQUIRED = (
    "year_of_assessment",
    "employment_income",
    "mpf_mandatory_contributions",
    "married_status",
)
_SALARIES_OPTIONAL = (
    "dependent_children",
    "dependent_parents",
    "deduction_items",
    "provisional_paid",
)
_TOP_LEVEL_KEYS = ("tax_type", "schema_version", "input")

# —— 各税项字段表（C3.5；extra="forbid"；rank 等服务器派生字段按 E_INPUT_EXTRA 拒绝）——
_PROFITS_REQUIRED = ("year_of_assessment", "entity_kind", "assessable_profit")
_PROFITS_OPTIONAL = (
    "two_tier",
    "loss_brought_forward",
    "partners",
    "partner_assessable_allocations",
    "pa_election_partners",
)
_PROPERTY_REQUIRED = ("year_of_assessment", "properties")
_PROPERTY_OPTIONAL = ("preceding_year_property_facts",)
_PA_REQUIRED = ("year_of_assessment", "married_status", "persons")
_PA_OPTIONAL = ("provisional_paid",)
_STAMP_PROPERTY_REQUIRED = ("instrument_date", "instrument_kind")
_STAMP_PROPERTY_OPTIONAL = ("property_class", "consideration", "value", "prior_links")
_STAMP_STOCK_REQUIRED = ("instrument_date", "documents")
_STAMP_STOCK_OPTIONAL = ("complete",)
_STAMP_LEASE_REQUIRED = ("instrument_date",)
_STAMP_LEASE_OPTIONAL = (
    "term_kind",
    "term_start",
    "term_end",
    "rent_input_mode",
    "monthly_rent",
    "total_rent",
    "annual_rent",
    "rent_schedule",
    "rent_free_period",
    "premium",
    "property_class",
    "deposit",
    "copies",
)


def validate_prepare_body(body: Any) -> tuple[str, dict]:
    """校验顶层 {tax_type, schema_version, input}；返回 (tax_type, canonical_input)。"""
    if not isinstance(body, dict):
        raise AppError("E_INPUT_TYPE", "请求体必须是 JSON 对象", field="body")
    for key in _TOP_LEVEL_KEYS:
        if key not in body:
            raise AppError("E_INPUT_MISSING", f"缺少必填字段：{key}", field=key)
    extras = [key for key in body if key not in _TOP_LEVEL_KEYS]
    if extras:
        raise AppError("E_INPUT_EXTRA", f"请求体含未知字段：{extras[0]}", field=extras[0])
    tax_type = body["tax_type"]
    # C3.7：任何非法输入（含错误 JSON 类型）都走结构化错误——不可哈希类型
    # （列表/对象）不得触达 dict.get（否则未处理 TypeError＝500 等价）。
    if not isinstance(tax_type, str) or not tax_type:
        raise AppError(
            "E_INPUT_TYPE",
            f"tax_type 必须为非空字符串（已批准税项：salaries_tax／"
            "profits_tax／property_tax／personal_assessment／stamp_property／"
            "stamp_stock／stamp_lease）",
            field="tax_type",
        )
    validator = _TAX_VALIDATORS.get(tax_type)
    if validator is None:
        raise AppError(
            "E_INPUT_TYPE",
            f"未知税项：{tax_type!r}（已批准税项：salaries_tax／profits_tax／"
            "property_tax／personal_assessment／stamp_property／stamp_stock／"
            "stamp_lease）",
            field="tax_type",
        )
    if body["schema_version"] != _SCHEMA_VERSION:
        raise AppError(
            "E_INPUT_TYPE",
            f"schema_version 必须为 {_SCHEMA_VERSION}",
            field="schema_version",
        )
    return tax_type, validator(body["input"])


# ------------------------------------------------------------------ 共用助手
def _input_object(inp: Any) -> dict:
    if not isinstance(inp, dict):
        raise AppError("E_INPUT_TYPE", "input 必须是 JSON 对象", field="input")
    return inp


def _require_keys(inp: dict, required: tuple[str, ...]) -> None:
    for key in required:
        if key not in inp:
            raise AppError(
                "E_INPUT_MISSING", f"缺少必填事实字段：{key}", field=key
            )


def _reject_extra(inp: dict, allowed: tuple[str, ...]) -> None:
    extras = [key for key in inp if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"input 含未知字段：{extras[0]}", field=extras[0]
        )


def _canonical_year(inp: dict) -> str:
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


def _amount_value(raw: Any, field: str) -> str:
    """金额：仅规范字符串（C2.6 先严格 str 判定；禁止 JSON number/布尔）。"""
    try:
        return parse_amount(raw)
    except AppError:
        raise AppError(
            "E_INPUT_TYPE",
            f"{field} 必须为规范金额字符串（禁止 JSON number/布尔）",
            field=field,
        ) from None


def _ratio_value(raw: Any, field: str) -> str:
    """份额 RatioStr（num/den 规范形；语法非法 → E_INPUT_TYPE）。"""
    if not isinstance(raw, str):
        raise AppError(
            "E_INPUT_TYPE", f"{field} 必须为 num/den 比率字符串", field=field
        )
    try:
        parse_ratio(raw)
    except AppError:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 不是合法比率（num/den）", field=field
        ) from None
    return raw


def _date_value(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or _DATE_RE.fullmatch(raw) is None:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 必须为 YYYY-MM-DD 全匹配", field=field
        )
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 不是有效日历日期", field=field
        ) from None
    return raw


def _tri_state_field(value: Any, field: str) -> dict:
    """{value: true|false|"unknown"} 三态资格事实（C3.5）。

    缺失≠显式零≠unknown；unknown 放行至引擎按 needs_input 追问（不得默认）。
    """
    if not isinstance(value, dict):
        raise AppError("E_INPUT_TYPE", f"{field} 必须是对象", field=field)
    extras = [key for key in value if key != "value"]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
        )
    if "value" not in value:
        raise AppError(
            "E_INPUT_MISSING", f"{field}.value 缺失", field=f"{field}.value"
        )
    state = value["value"]
    if state is not True and state is not False and state != "unknown":
        raise AppError(
            "E_INPUT_TYPE",
            f'{field}.value 必须为 true／false／"unknown"',
            field=field,
        )
    return {"value": state}


def _positive(text: str) -> bool:
    return Decimal(text) > 0


# ------------------------------------------------------------------ 薪俸税
def validate_salaries_input(inp: Any) -> dict:
    """三态输入校验（缺失≠显式零≠unknown）；返回 canonical 输入映射。"""
    inp = _input_object(inp)
    _require_keys(inp, _SALARIES_REQUIRED)
    _reject_extra(inp, _SALARIES_REQUIRED + _SALARIES_OPTIONAL)

    canonical: dict[str, Any] = {"year_of_assessment": _canonical_year(inp)}
    for key in ("employment_income", "mpf_mandatory_contributions"):
        canonical[key] = _amount_value(inp[key], key)

    canonical["married_status"] = _validate_married_status(inp["married_status"])
    if "dependent_children" in inp:
        canonical["dependent_children"] = _validate_children(inp["dependent_children"])
    if "dependent_parents" in inp:
        canonical["dependent_parents"] = _validate_parents(inp["dependent_parents"])
    if "deduction_items" in inp:
        canonical["deduction_items"] = _validate_deductions(inp["deduction_items"])
    if "provisional_paid" in inp:
        # 已缴暂缴三态（REQ-8）：键缺失 ≠ 显式零；显式 "0" 是合法事实。
        canonical["provisional_paid"] = _amount_value(
            inp["provisional_paid"], "provisional_paid"
        )
    return canonical


def _validate_married_status(value: Any) -> dict:
    if not isinstance(value, dict):
        raise AppError("E_INPUT_TYPE", "married_status 必须是对象", field="married_status")
    extras = [key for key in value if key != "value"]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA",
            f"married_status 含未知字段：{extras[0]}",
            field=extras[0],
        )
    if "value" not in value:
        raise AppError(
            "E_INPUT_MISSING", "married_status.value 缺失", field="married_status.value"
        )
    status = value["value"]
    if status == "unknown":
        # 三态 unknown（仅资格类字段允许）→ 结构化中文问题清单（REQ-10）
        raise AppError(
            "E_ELIGIBILITY_UNKNOWN",
            "婚姻状况未知，无法继续计算；请确认后重新提交。",
            field="married_status.value",
            extra={
                "questions": [
                    "你的婚姻状况是「单身」还是「已婚」？确认后将按对应免税额规则继续薪俸税估算。"
                ]
            },
        )
    if status not in ("single", "married"):
        raise AppError(
            "E_INPUT_TYPE",
            "married_status.value 必须为 single／married／unknown",
            field="married_status.value",
        )
    return {"value": status}


def _validate_residence(value: Any) -> dict:
    if not isinstance(value, dict):
        raise AppError("E_INPUT_TYPE", "residence 必须是对象", field="residence")
    extras = [key for key in value if key != "value"]
    if extras:
        raise AppError("E_INPUT_EXTRA", f"residence 含未知字段：{extras[0]}", field=extras[0])
    if "value" not in value:
        raise AppError("E_INPUT_MISSING", "residence.value 缺失", field="residence.value")
    residence = value["value"]
    if residence not in (True, False, "unknown"):
        raise AppError(
            "E_INPUT_TYPE", "residence.value 必须为布尔或 unknown", field="residence.value"
        )
    return {"value": residence}


def _validated_birth_date(raw: Any) -> str:
    if not isinstance(raw, str) or _DATE_RE.fullmatch(raw) is None:
        raise AppError("E_INPUT_TYPE", "birth_date 必须为 YYYY-MM-DD 全匹配", field="birth_date")
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise AppError(
            "E_INPUT_TYPE", "birth_date 不是有效日历日期", field="birth_date"
        ) from None
    return raw


def _validate_children(entries: Any) -> list[dict]:
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE", "dependent_children 必须是数组", field="dependent_children"
        )
    if len(entries) > 9:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            "合格子女申索超过法定上限 9 名（C2.9）",
            field="dependent_children",
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AppError(
                "E_INPUT_TYPE",
                f"子女条目必须是对象：dependent_children[{index}]",
                field="dependent_children",
            )
        allowed = {"birth_date", "residence", "claim_owner"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            # C2.9：rank 由服务器派生、非权威输入 → E_INPUT_EXTRA
            raise AppError(
                "E_INPUT_EXTRA",
                f"子女条目含未知字段：{extras[0]}（rank 等由服务器派生，非权威输入）",
                field=extras[0],
            )
        for required in ("birth_date", "residence"):
            if required not in entry:
                raise AppError(
                    "E_INPUT_MISSING",
                    f"子女条目缺少 {required}",
                    field=required,
                )
        child: dict[str, Any] = {
            "birth_date": _validated_birth_date(entry["birth_date"]),
            "residence": _validate_residence(entry["residence"]),
        }
        if "claim_owner" in entry:
            if not isinstance(entry["claim_owner"], str) or not entry["claim_owner"]:
                raise AppError(
                    "E_INPUT_TYPE",
                    "claim_owner 必须为非空字符串",
                    field="claim_owner",
                )
            child["claim_owner"] = entry["claim_owner"]
        validated.append(child)
    return validated


def _validate_parents(entries: Any) -> list[dict]:
    """受养父母条目（C3.5＋s.30(4)/s.30(3)(b) 资格事实三态扩展）。

    - maintained{value}：s.30(4) 供养/维持判据事实（三态；缺失＝未提供，
      由引擎按 needs_input 追问——缺失≠unknown≠false）；
    - living_together_all_year{value}：s.30(3)(b) 全年连续同住额外额事实
      （三态；缺失＝未申索额外额）。
    """
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE", "dependent_parents 必须是数组", field="dependent_parents"
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AppError(
                "E_INPUT_TYPE",
                f"受养人条目必须是对象：dependent_parents[{index}]",
                field="dependent_parents",
            )
        allowed = {
            "relationship",
            "birth_date",
            "residence",
            "claim_owner",
            "maintained",
            "living_together_all_year",
        }
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA",
                f"受养人条目含未知字段：{extras[0]}",
                field=extras[0],
            )
        for required in ("relationship", "birth_date", "residence"):
            if required not in entry:
                raise AppError(
                    "E_INPUT_MISSING",
                    f"受养人条目缺少 {required}",
                    field=required,
                )
        parent: dict[str, Any] = {
            "relationship": entry["relationship"],
            "birth_date": _validated_birth_date(entry["birth_date"]),
            "residence": _validate_residence(entry["residence"]),
        }
        if "maintained" in entry:
            parent["maintained"] = _tri_state_field(
                entry["maintained"], f"dependent_parents[{index}].maintained"
            )
        if "living_together_all_year" in entry:
            parent["living_together_all_year"] = _tri_state_field(
                entry["living_together_all_year"],
                f"dependent_parents[{index}].living_together_all_year",
            )
        if "claim_owner" in entry:
            if not isinstance(entry["claim_owner"], str) or not entry["claim_owner"]:
                raise AppError(
                    "E_INPUT_TYPE",
                    "claim_owner 必须为非空字符串",
                    field="claim_owner",
                )
            parent["claim_owner"] = entry["claim_owner"]
        validated.append(parent)
    return validated


def _validate_deductions(entries: Any) -> list[dict]:
    """逐项枚举扣除（无 catch-all「其他扣除」，SDD §3／REQ-10）。

    - 不支持 item_type 正数申索 → E_DEDUCTION_UNSUPPORTED（422，双保险第一层；
      引擎侧同码拒算兜底）；
    - 不支持 item_type 显式 "0" → 合法、无效果（eligibility 三态仍参与校验）；
    - eligibility value="unknown"（在册与否一律）→ E_ELIGIBILITY_UNKNOWN＋
      中文 questions（422；资格未知不得默认可扣、不得被零金额吞掉）；
    - eligibility 缺失 → 放行至引擎追问（缺失≠unknown 三态保留）。
    """
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE", "deduction_items 必须是数组", field="deduction_items"
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AppError(
                "E_INPUT_TYPE",
                f"扣除条目必须是对象：deduction_items[{index}]",
                field="deduction_items",
            )
        allowed = {"item_type", "amount", "eligibility"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA",
                f"扣除条目含未知字段：{extras[0]}（逐项枚举，无 catch-all）",
                field=extras[0],
            )
        for required in ("item_type", "amount"):
            if required not in entry:
                raise AppError(
                    "E_INPUT_MISSING",
                    f"扣除条目缺少 {required}",
                    field=required,
                )
        if not isinstance(entry["item_type"], str) or not entry["item_type"]:
            raise AppError(
                "E_INPUT_TYPE", "item_type 必须为非空字符串", field="item_type"
            )
        item_type = entry["item_type"]
        amount = _amount_value(entry["amount"], "amount")
        item: dict[str, Any] = {"item_type": item_type, "amount": amount}
        if item_type not in SUPPORTED_DEDUCTION_ITEM_TYPES and _positive(amount):
            raise AppError(
                "E_DEDUCTION_UNSUPPORTED",
                f"扣除项 {item_type} 不在薪俸税可扣除附表内（逐项枚举，无 "
                "catch-all）；正数申索须拒算受影响税项（REQ-10）。",
                field="item_type",
            )
        if "eligibility" in entry:
            field = "eligibility"
            eligibility = entry["eligibility"]
            if not isinstance(eligibility, dict):
                raise AppError(
                    "E_INPUT_TYPE", f"{field} 必须是对象", field=field
                )
            extras = [key for key in eligibility if key != "value"]
            if extras:
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"{field} 含未知字段：{extras[0]}",
                    field=extras[0],
                )
            if "value" not in eligibility:
                raise AppError(
                    "E_INPUT_MISSING", f"{field}.value 缺失", field=f"{field}.value"
                )
            value = eligibility["value"]
            if value == "unknown":
                raise AppError(
                    "E_ELIGIBILITY_UNKNOWN",
                    f"扣除项 {item_type} 的资格状态未知；确认前不得计算（REQ-10；"
                    "零金额亦不例外——unknown 不得被静默吞掉）。",
                    field=f"deduction_items.{field}",
                    extra={
                        "questions": [
                            f"扣除项 {item_type} 的资格状态为「未知」：请确认是否"
                            "申索该项及是否符合对应扣除附表的申索资格（如长者住宿"
                            "照顾开支的院舍类别与实际支付事实）；确认后将按法定"
                            "上限计入。"
                        ]
                    },
                )
            if value is not True and value is not False:
                raise AppError(
                    "E_INPUT_TYPE",
                    f'{field}.value 必须为 true／false／"unknown"',
                    field=field,
                )
            item["eligibility"] = {"value": value}
        validated.append(item)
    return validated


# ------------------------------------------------------------------ 利得税
def _validate_profits_input(inp: Any) -> dict:
    inp = _input_object(inp)
    _require_keys(inp, _PROFITS_REQUIRED)
    _reject_extra(inp, _PROFITS_REQUIRED + _PROFITS_OPTIONAL)

    canonical: dict[str, Any] = {"year_of_assessment": _canonical_year(inp)}
    kind = inp["entity_kind"]
    if not isinstance(kind, str) or kind not in (
        "corporation",
        "partnership",
        "sole_proprietorship",
    ):
        raise AppError(
            "E_INPUT_TYPE",
            "entity_kind 必须为 corporation／partnership／sole_proprietorship",
            field="entity_kind",
        )
    canonical["entity_kind"] = kind
    canonical["assessable_profit"] = _amount_value(
        inp["assessable_profit"], "assessable_profit"
    )
    if "two_tier" in inp:
        canonical["two_tier"] = _validate_two_tier(inp["two_tier"])
    if "loss_brought_forward" in inp:
        canonical["loss_brought_forward"] = _amount_value(
            inp["loss_brought_forward"], "loss_brought_forward"
        )
    if "partners" in inp:
        canonical["partners"] = _validate_partners(inp["partners"])
    if "partner_assessable_allocations" in inp:
        canonical["partner_assessable_allocations"] = _validate_allocations(
            inp["partner_assessable_allocations"]
        )
    if "pa_election_partners" in inp:
        canonical["pa_election_partners"] = _validate_partner_ids(
            inp["pa_election_partners"], "pa_election_partners"
        )
    return canonical


def _validate_two_tier(value: Any) -> dict:
    """两级制资格事实（D4.2）：三态确认项 unknown 放行至引擎追问（不得默认）。"""
    if not isinstance(value, dict):
        raise AppError("E_INPUT_TYPE", "two_tier 必须是对象", field="two_tier")
    allowed = {"connected_entities", "election_made", "no_other_election_same_year"}
    extras = [key for key in value if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"two_tier 含未知字段：{extras[0]}", field=extras[0]
        )
    canonical: dict[str, Any] = {}
    if "election_made" in value:
        canonical["election_made"] = _tri_state_field(
            value["election_made"], "two_tier.election_made"
        )
    if "no_other_election_same_year" in value:
        canonical["no_other_election_same_year"] = _tri_state_field(
            value["no_other_election_same_year"], "two_tier.no_other_election_same_year"
        )
    if "connected_entities" in value:
        entities = value["connected_entities"]
        if not isinstance(entities, list):
            raise AppError(
                "E_INPUT_TYPE",
                "two_tier.connected_entities 必须是数组",
                field="two_tier.connected_entities",
            )
        validated: list[dict] = []
        for index, entity in enumerate(entities):
            field = f"two_tier.connected_entities[{index}]"
            if not isinstance(entity, dict):
                raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
            entity_allowed = {"entity_id", "basis_period_end", "control_basis"}
            entity_extras = [key for key in entity if key not in entity_allowed]
            if entity_extras:
                raise AppError(
                    "E_INPUT_EXTRA",
                    f"{field} 含未知字段：{entity_extras[0]}",
                    field=entity_extras[0],
                )
            validated.append({key: entity[key] for key in entity if key in entity_allowed})
        canonical["connected_entities"] = validated
    return canonical


def _validate_partners(entries: Any) -> list[dict]:
    if not isinstance(entries, list) or not entries:
        raise AppError(
            "E_INPUT_MISSING",
            "合伙业务须提供 partners[]（partner_id／partner_kind／share）",
            field="partners",
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        field = f"partners[{index}]"
        if not isinstance(entry, dict):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        allowed = {"partner_id", "partner_kind", "share"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
            )
        for required in ("partner_id", "partner_kind", "share"):
            if required not in entry:
                raise AppError(
                    "E_INPUT_MISSING", f"{field} 缺少 {required}", field=required
                )
        partner_id = entry["partner_id"]
        if not isinstance(partner_id, str) or not partner_id:
            raise AppError(
                "E_INPUT_TYPE", f"{field}.partner_id 须为非空字符串", field=field
            )
        kind = entry["partner_kind"]
        if kind not in ("corporation", "individual"):
            raise AppError(
                "E_INPUT_TYPE",
                f"{field}.partner_kind 必须为 corporation／individual",
                field=field,
            )
        validated.append(
            {
                "partner_id": partner_id,
                "partner_kind": kind,
                "share": _ratio_value(entry["share"], f"{field}.share"),
            }
        )
    return validated


def _validate_allocations(entries: Any) -> list[dict]:
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE",
            "partner_assessable_allocations 必须是数组",
            field="partner_assessable_allocations",
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        field = f"partner_assessable_allocations[{index}]"
        if not isinstance(entry, dict):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        allowed = {"partner_id", "business_id", "assessable_amount"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
            )
        for required in ("partner_id", "assessable_amount"):
            if required not in entry:
                raise AppError(
                    "E_INPUT_MISSING", f"{field} 缺少 {required}", field=required
                )
        partner_id = entry["partner_id"]
        if not isinstance(partner_id, str) or not partner_id:
            raise AppError(
                "E_INPUT_TYPE", f"{field}.partner_id 须为非空字符串", field=field
            )
        item: dict[str, Any] = {
            "partner_id": partner_id,
            "assessable_amount": _amount_value(
                entry["assessable_amount"], f"{field}.assessable_amount"
            ),
        }
        if "business_id" in entry:
            business_id = entry["business_id"]
            if not isinstance(business_id, str) or not business_id:
                raise AppError(
                    "E_INPUT_TYPE", f"{field}.business_id 须为非空字符串", field=field
                )
            item["business_id"] = business_id
        validated.append(item)
    return validated


def _validate_partner_ids(entries: Any, field: str) -> list[str]:
    if not isinstance(entries, list):
        raise AppError("E_INPUT_TYPE", f"{field} 必须是数组", field=field)
    validated: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, str) or not entry:
            raise AppError(
                "E_INPUT_TYPE", f"{field}[{index}] 须为非空字符串", field=field
            )
        validated.append(entry)
    return validated


# ------------------------------------------------------------------ 物业税
def _validate_property_input(inp: Any) -> dict:
    inp = _input_object(inp)
    _require_keys(inp, _PROPERTY_REQUIRED)
    _reject_extra(inp, _PROPERTY_REQUIRED + _PROPERTY_OPTIONAL)

    canonical: dict[str, Any] = {"year_of_assessment": _canonical_year(inp)}
    properties = inp["properties"]
    if not isinstance(properties, list) or not properties:
        raise AppError(
            "E_INPUT_MISSING",
            "缺少 properties[]（评税单位＝逐项物业，BIR57 Note 1(b)）",
            field="properties",
        )
    canonical["properties"] = [_validate_property_entry(p) for p in properties]
    if "preceding_year_property_facts" in inp:
        preceding = inp["preceding_year_property_facts"]
        if not isinstance(preceding, list):
            raise AppError(
                "E_INPUT_TYPE",
                "preceding_year_property_facts 必须是数组",
                field="preceding_year_property_facts",
            )
        canonical["preceding_year_property_facts"] = [
            _validate_property_entry(e, preceding=True) for e in preceding
        ]
    return canonical


def _validate_property_entry(entry: Any, *, preceding: bool = False) -> dict:
    where = "preceding_year_property_facts[]" if preceding else "properties[]"
    if not isinstance(entry, dict):
        raise AppError("E_INPUT_TYPE", f"{where} 元素须为对象", field=where)
    allowed = {
        "property_id",
        "rent_received",
        "rates_paid_by_owner",
        "irrecoverable_rent",
        "deposit_offsets",
        "recovered_previously_deducted_rent",
        "mortgage_interest",
        "deduction_items",
    }
    if preceding:
        # s.7C(3) 以前年度回扣目标：须标注课税年度（引擎只回看更早年度）
        allowed = allowed | {"year_of_assessment"}
    extras = [key for key in entry if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"{where} 含未知字段：{extras[0]}", field=extras[0]
        )
    for required in ("property_id", "rent_received"):
        if required not in entry:
            raise AppError(
                "E_INPUT_MISSING", f"{where} 缺少 {required}", field=required
            )
    pid = entry["property_id"]
    if not isinstance(pid, str) or not pid:
        raise AppError(
            "E_INPUT_TYPE", f"{where}.property_id 须为非空字符串", field="property_id"
        )
    canonical: dict[str, Any] = {
        "property_id": pid,
        "rent_received": _amount_value(entry["rent_received"], "rent_received"),
    }
    if preceding:
        if "year_of_assessment" not in entry:
            raise AppError(
                "E_INPUT_MISSING",
                f"{where} 缺少 year_of_assessment",
                field="year_of_assessment",
            )
        canonical["year_of_assessment"] = _canonical_year(entry)
    for key in (
        "irrecoverable_rent",
        "deposit_offsets",
        "recovered_previously_deducted_rent",
        "mortgage_interest",
    ):
        if key in entry:
            canonical[key] = _amount_value(entry[key], key)
    if "rates_paid_by_owner" in entry:
        canonical["rates_paid_by_owner"] = _validate_rates_paid_by_owner(
            entry["rates_paid_by_owner"]
        )
    if "deduction_items" in entry:
        canonical["deduction_items"] = _validate_deduction_items_for_property(
            entry["deduction_items"]
        )
    return canonical


def _validate_rates_paid_by_owner(value: Any) -> dict:
    """差饷两条件事实（s.5(1A)(b)(i)）：{amount?, agreed{value}?, actually_paid{value}?}。"""
    if not isinstance(value, dict):
        raise AppError(
            "E_INPUT_TYPE",
            "rates_paid_by_owner 须为对象 {amount, agreed{value}, actually_paid{value}}",
            field="rates_paid_by_owner",
        )
    allowed = {"amount", "agreed", "actually_paid"}
    extras = [key for key in value if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA",
            f"rates_paid_by_owner 含未知字段：{extras[0]}",
            field=extras[0],
        )
    canonical: dict[str, Any] = {}
    if "amount" in value:
        canonical["amount"] = _amount_value(value["amount"], "rates_paid_by_owner.amount")
    if "agreed" in value:
        canonical["agreed"] = _tri_state_field(
            value["agreed"], "rates_paid_by_owner.agreed"
        )
    if "actually_paid" in value:
        canonical["actually_paid"] = _tri_state_field(
            value["actually_paid"], "rates_paid_by_owner.actually_paid"
        )
    return canonical


def _validate_deduction_items_for_property(entries: Any) -> list[dict]:
    """物业税 deduction_items：地租/修葺/保险等均不可扣（REQ-5）——结构此处
    校验，正数拒算由引擎统一执行（显式 "0" 合法无效果）。"""
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE", "deduction_items 必须是数组", field="deduction_items"
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        field = f"deduction_items[{index}]"
        if not isinstance(entry, dict):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        allowed = {"item_type", "amount"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
            )
        for required in ("item_type", "amount"):
            if required not in entry:
                raise AppError(
                    "E_INPUT_MISSING", f"{field} 缺少 {required}", field=required
                )
        item_type = entry["item_type"]
        if not isinstance(item_type, str) or not item_type:
            raise AppError(
                "E_INPUT_TYPE", f"{field}.item_type 须为非空字符串", field=field
            )
        validated.append(
            {"item_type": item_type, "amount": _amount_value(entry["amount"], "amount")}
        )
    return validated


# ---------------------------------------------------------- 个人入息课税
def _validate_personal_assessment_input(inp: Any) -> dict:
    inp = _input_object(inp)
    _require_keys(inp, _PA_REQUIRED)
    _reject_extra(inp, _PA_REQUIRED + _PA_OPTIONAL)

    canonical: dict[str, Any] = {"year_of_assessment": _canonical_year(inp)}
    canonical["married_status"] = _validate_married_status(inp["married_status"])
    persons = inp["persons"]
    if not isinstance(persons, list) or not persons:
        raise AppError(
            "E_INPUT_MISSING", "缺少 persons[]（个人入息课税按人汇聚事实）", field="persons"
        )
    canonical["persons"] = [_validate_person(p) for p in persons]
    if "provisional_paid" in inp:
        canonical["provisional_paid"] = _amount_value(
            inp["provisional_paid"], "provisional_paid"
        )
    return canonical


def _validate_person(entry: Any) -> dict:
    if not isinstance(entry, dict):
        raise AppError("E_INPUT_TYPE", "persons[] 元素须为对象", field="persons")
    allowed = {
        "person_id",
        "employment_income",
        "mpf_mandatory_contributions",
        "properties",
        "businesses",
        "dependent_parents",
        "dependent_children",
    }
    extras = [key for key in entry if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"persons[] 含未知字段：{extras[0]}", field=extras[0]
        )
    for required in ("person_id", "employment_income", "mpf_mandatory_contributions"):
        if required not in entry:
            raise AppError(
                "E_INPUT_MISSING", f"person 缺少 {required}", field=required
            )
    pid = entry["person_id"]
    if not isinstance(pid, str) or not pid:
        raise AppError(
            "E_INPUT_TYPE", "person_id 须为非空字符串", field="person_id"
        )
    canonical: dict[str, Any] = {
        "person_id": pid,
        "employment_income": _amount_value(entry["employment_income"], "employment_income"),
        "mpf_mandatory_contributions": _amount_value(
            entry["mpf_mandatory_contributions"], "mpf_mandatory_contributions"
        ),
    }
    if "properties" in entry:
        properties = entry["properties"]
        if not isinstance(properties, list):
            raise AppError(
                "E_INPUT_TYPE", "properties 须为数组", field="properties"
            )
        canonical["properties"] = [_validate_pa_property(p) for p in properties]
    if "businesses" in entry:
        businesses = entry["businesses"]
        if not isinstance(businesses, list):
            raise AppError(
                "E_INPUT_TYPE", "businesses 须为数组", field="businesses"
            )
        canonical["businesses"] = [_validate_pa_business(b) for b in businesses]
    if "dependent_parents" in entry:
        canonical["dependent_parents"] = _validate_pa_parents(
            entry["dependent_parents"]
        )
    if "dependent_children" in entry:
        canonical["dependent_children"] = _validate_pa_children(
            entry["dependent_children"]
        )
    return canonical


def _validate_pa_property(entry: Any) -> dict:
    if not isinstance(entry, dict):
        raise AppError("E_INPUT_TYPE", "properties 元素须为对象", field="properties")
    allowed = {
        "property_id",
        "share",
        "rent_received",
        "rates_paid_by_owner",
        "irrecoverable_rent",
        "deposit_offsets",
        "mortgage_interest",
    }
    extras = [key for key in entry if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"properties 含未知字段：{extras[0]}", field=extras[0]
        )
    for required in ("property_id", "share", "rent_received"):
        if required not in entry:
            raise AppError(
                "E_INPUT_MISSING", f"property 缺少 {required}", field=required
            )
    pid = entry["property_id"]
    if not isinstance(pid, str) or not pid:
        raise AppError(
            "E_INPUT_TYPE", "property_id 须为非空字符串", field="property_id"
        )
    canonical: dict[str, Any] = {
        "property_id": pid,
        "share": _ratio_value(entry["share"], "share"),
        "rent_received": _amount_value(entry["rent_received"], "rent_received"),
    }
    for key in ("irrecoverable_rent", "deposit_offsets", "mortgage_interest"):
        if key in entry:
            canonical[key] = _amount_value(entry[key], key)
    if "rates_paid_by_owner" in entry:
        canonical["rates_paid_by_owner"] = _validate_rates_paid_by_owner(
            entry["rates_paid_by_owner"]
        )
    return canonical


def _validate_pa_business(entry: Any) -> dict:
    if not isinstance(entry, dict):
        raise AppError("E_INPUT_TYPE", "businesses 元素须为对象", field="businesses")
    allowed = {"business_id", "entity_kind", "assessable_profit", "share"}
    extras = [key for key in entry if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"businesses 含未知字段：{extras[0]}", field=extras[0]
        )
    for required in ("business_id", "entity_kind", "assessable_profit", "share"):
        if required not in entry:
            raise AppError(
                "E_INPUT_MISSING", f"business 缺少 {required}", field=required
            )
    bid = entry["business_id"]
    if not isinstance(bid, str) or not bid:
        raise AppError(
            "E_INPUT_TYPE", "business_id 须为非空字符串", field="business_id"
        )
    kind = entry["entity_kind"]
    if kind not in ("sole_proprietorship", "partnership"):
        raise AppError(
            "E_INPUT_TYPE",
            "entity_kind 必须为 sole_proprietorship／partnership",
            field="entity_kind",
        )
    return {
        "business_id": bid,
        "entity_kind": kind,
        "assessable_profit": _amount_value(entry["assessable_profit"], "assessable_profit"),
        "share": _ratio_value(entry["share"], "share"),
    }


def _validate_pa_parents(entries: Any) -> list[dict]:
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE", "dependent_parents 须为数组", field="dependent_parents"
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        field = f"dependent_parents[{index}]"
        if not isinstance(entry, dict):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        extras = [key for key in entry if key != "parent_id"]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
            )
        if "parent_id" not in entry:
            raise AppError(
                "E_INPUT_MISSING", "受养父母缺少 parent_id", field="parent_id"
            )
        parent_id = entry["parent_id"]
        if not isinstance(parent_id, str) or not parent_id:
            raise AppError(
                "E_INPUT_TYPE", "parent_id 须为非空字符串", field="parent_id"
            )
        validated.append({"parent_id": parent_id})
    return validated


def _validate_pa_children(entries: Any) -> list[dict]:
    if not isinstance(entries, list):
        raise AppError(
            "E_INPUT_TYPE", "dependent_children 须为数组", field="dependent_children"
        )
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        field = f"dependent_children[{index}]"
        if not isinstance(entry, dict):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        allowed = {"birth_date", "residence"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
            )
        child: dict[str, Any] = {}
        if "birth_date" in entry:
            child["birth_date"] = _validated_birth_date(entry["birth_date"])
        if "residence" in entry:
            child["residence"] = _validate_residence(entry["residence"])
        validated.append(child)
    return validated


# ------------------------------------------------------------------ 印花税
def _validate_stamp_property_input(inp: Any) -> dict:
    inp = _input_object(inp)
    _require_keys(inp, _STAMP_PROPERTY_REQUIRED)
    _reject_extra(inp, _STAMP_PROPERTY_REQUIRED + _STAMP_PROPERTY_OPTIONAL)

    canonical: dict[str, Any] = {
        "instrument_date": _date_value(inp["instrument_date"], "instrument_date"),
    }
    kind = inp["instrument_kind"]
    if kind not in ("agreement_for_sale", "conveyance_on_sale"):
        raise AppError(
            "E_INPUT_TYPE",
            "instrument_kind 仅支持 agreement_for_sale／conveyance_on_sale",
            field="instrument_kind",
        )
    canonical["instrument_kind"] = kind
    if "property_class" in inp:
        property_class = inp["property_class"]
        if property_class not in ("residential", "nonresidential"):
            raise AppError(
                "E_INPUT_TYPE",
                "property_class 须为 residential 或 nonresidential",
                field="property_class",
            )
        canonical["property_class"] = property_class
    for key in ("consideration", "value"):
        if key in inp:
            canonical[key] = _amount_value(inp[key], key)
    if "prior_links" in inp:
        canonical["prior_links"] = _validate_prior_links(inp["prior_links"])
    return canonical


def _validate_prior_links(entries: Any) -> list[dict]:
    if not isinstance(entries, list):
        raise AppError("E_INPUT_TYPE", "prior_links 须为数组", field="prior_links")
    validated: list[dict] = []
    for index, entry in enumerate(entries):
        field = f"prior_links[{index}]"
        if not isinstance(entry, dict):
            raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
        allowed = {"supersedes", "conform"}
        extras = [key for key in entry if key not in allowed]
        if extras:
            raise AppError(
                "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
            )
        if not any(key in entry for key in allowed):
            raise AppError(
                "E_INPUT_TYPE",
                f"{field} 须含 supersedes 或 conform 结构化谓词",
                field=field,
            )
        # 谓词内部结构（三态关系／协议日期）由引擎深度校验（D1.5/D1.7）
        validated.append({key: entry[key] for key in entry if key in allowed})
    return validated


def _validate_stamp_stock_input(inp: Any) -> dict:
    inp = _input_object(inp)
    _require_keys(inp, _STAMP_STOCK_REQUIRED)
    _reject_extra(inp, _STAMP_STOCK_REQUIRED + _STAMP_STOCK_OPTIONAL)

    canonical: dict[str, Any] = {
        "instrument_date": _date_value(inp["instrument_date"], "instrument_date"),
    }
    documents = inp["documents"]
    if not isinstance(documents, list) or not documents:
        raise AppError(
            "E_INPUT_MISSING",
            "缺少 documents[]（每份文书独立计税，D2.2）",
            field="documents",
        )
    canonical["documents"] = [_validate_stock_document(d) for d in documents]
    if "complete" in inp:
        canonical["complete"] = _tri_state_field(inp["complete"], "complete")
    return canonical


def _validate_stock_document(entry: Any) -> dict:
    if not isinstance(entry, dict):
        raise AppError("E_INPUT_TYPE", "documents[] 元素须为对象", field="documents")
    allowed = {"doc_kind", "consideration", "value"}
    extras = [key for key in entry if key not in allowed]
    if extras:
        raise AppError(
            "E_INPUT_EXTRA", f"documents 含未知字段：{extras[0]}", field=extras[0]
        )
    if "doc_kind" not in entry:
        raise AppError(
            "E_INPUT_MISSING", "documents 条目缺少 doc_kind", field="doc_kind"
        )
    kind = entry["doc_kind"]
    if kind not in (
        "contract_note_sold",
        "contract_note_bought",
        "voluntary_inter_vivos",
        "other_transfer",
    ):
        raise AppError(
            "E_INPUT_TYPE",
            f"doc_kind 非法：{kind!r}（D2.3 枚举）",
            field="doc_kind",
        )
    canonical: dict[str, Any] = {"doc_kind": kind}
    for key in ("consideration", "value"):
        if key in entry:
            canonical[key] = _amount_value(entry[key], key)
    return canonical


def _validate_stamp_lease_input(inp: Any) -> dict:
    inp = _input_object(inp)
    _require_keys(inp, _STAMP_LEASE_REQUIRED)
    _reject_extra(inp, _STAMP_LEASE_REQUIRED + _STAMP_LEASE_OPTIONAL)

    canonical: dict[str, Any] = {
        "instrument_date": _date_value(inp["instrument_date"], "instrument_date"),
    }
    if "term_kind" in inp:
        term_kind = inp["term_kind"]
        if term_kind not in ("indefinite", "fixed"):
            raise AppError(
                "E_INPUT_TYPE", "term_kind 须为 indefinite 或 fixed", field="term_kind"
            )
        canonical["term_kind"] = term_kind
    for key in ("term_start", "term_end"):
        if key in inp:
            canonical[key] = _date_value(inp[key], key)
    if "rent_input_mode" in inp:
        mode = inp["rent_input_mode"]
        if mode not in ("fixed_monthly", "total_rent", "annual_rent"):
            raise AppError(
                "E_INPUT_TYPE",
                "rent_input_mode 须为 fixed_monthly/total_rent/annual_rent",
                field="rent_input_mode",
            )
        canonical["rent_input_mode"] = mode
    for key in ("monthly_rent", "total_rent", "annual_rent", "premium", "deposit"):
        if key in inp:
            canonical[key] = _amount_value(inp[key], key)
    if "rent_schedule" in inp:
        schedule = inp["rent_schedule"]
        if not isinstance(schedule, list):
            raise AppError(
                "E_INPUT_TYPE", "rent_schedule 须为分段数组", field="rent_schedule"
            )
        validated_schedule: list[dict] = []
        for index, entry in enumerate(schedule):
            field = f"rent_schedule[{index}]"
            if not isinstance(entry, dict):
                raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
            extras = [key for key in entry if key != "amount"]
            if extras:
                raise AppError(
                    "E_INPUT_EXTRA", f"{field} 含未知字段：{extras[0]}", field=extras[0]
                )
            if "amount" not in entry:
                raise AppError(
                    "E_INPUT_MISSING", f"{field} 缺少 amount", field=field
                )
            validated_schedule.append(
                {"amount": _amount_value(entry["amount"], f"{field}.amount")}
            )
        canonical["rent_schedule"] = validated_schedule
    if "rent_free_period" in inp:
        rent_free = inp["rent_free_period"]
        if not isinstance(rent_free, (str, list)):
            raise AppError(
                "E_INPUT_TYPE",
                "rent_free_period 须为描述字符串或分段数组",
                field="rent_free_period",
            )
        canonical["rent_free_period"] = rent_free
    if "property_class" in inp:
        property_class = inp["property_class"]
        if property_class not in ("residential", "nonresidential"):
            raise AppError(
                "E_INPUT_TYPE",
                "property_class 须为 residential 或 nonresidential",
                field="property_class",
            )
        canonical["property_class"] = property_class
    if "copies" in inp:
        copies = inp["copies"]
        if isinstance(copies, bool) or not isinstance(copies, int) or copies < 0:
            raise AppError(
                "E_INPUT_TYPE", "copies 须为非负整数（复本份数）", field="copies"
            )
        canonical["copies"] = copies
    return canonical


# -------------------------------------------------------------- 税项分发
_TAX_VALIDATORS = {
    "salaries_tax": validate_salaries_input,
    "profits_tax": _validate_profits_input,
    "property_tax": _validate_property_input,
    "personal_assessment": _validate_personal_assessment_input,
    "stamp_property": _validate_stamp_property_input,
    "stamp_stock": _validate_stamp_stock_input,
    "stamp_lease": _validate_stamp_lease_input,
}
