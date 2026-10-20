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
from typing import Any

from app.config import SUPPORTED_YEARS
from app.core.errors import AppError
from app.core.facts_v2 import normalize_v2_facts
from app.core.money import parse_amount, parse_ratio

_SCHEMA_VERSION = "1.0.0"
_V2_SCHEMA_VERSION = "2.0.0"

# S002 ADR-3 版本表：薪俸/PA input 升 2.0.0；其余五税维持 1.0.0
_V2_TAX_TYPES = ("salaries_tax", "personal_assessment")

_YEAR_KEY_RE = re.compile(r"[0-9]{4}_[0-9]{2}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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
    if tax_type in _V2_TAX_TYPES:
        # S002 §4.2：薪俸/PA 仅接受 2.0.0；旧 1.0.0 → 422 E_INPUT_TYPE
        # field=schema_version，附重新 prepare＋confirm 指引（不迁移）
        if body["schema_version"] != _V2_SCHEMA_VERSION:
            raise AppError(
                "E_INPUT_TYPE",
                "薪俸税／个人入息课税仅接受 schema_version 2.0.0；旧确认已失效，"
                "请重新 prepare＋confirm（不迁移旧 1.0.0 输入）。",
                field="schema_version",
            )
        return tax_type, _validate_v2_input(body["input"], tax_type)
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

# ------------------------------------------------- 薪俸/PA v2 输入（schema 2.0.0）
# Annex 005 §3–§7/§11：唯一结构权威＝app.core.facts_v2.normalize_v2_facts
# （HTTP 信封校验与两纯引擎入口共用；本模块不保留第二套 v2 解析实现）。


def _validate_v2_input(inp: Any, tax_type: str) -> dict:
    """v2 输入 → canonical v2 input（005 §3–§7；E2 中立入口）。"""
    return normalize_v2_facts(inp, tax_type=tax_type)


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
    """差饷两条件事实（s.5(1A)(b)(i)）：{amount?, agreed, actually_paid?}。

    v1 契约（复核报告 B4）：资格事实走 legacy `{value: true|false|"unknown"}`
    包装（C3.5 三态）；原始 Tri 属 v2 形状，v1 须拒绝（v2 由 `_v2_property`
    按原始 Tri 严格处理）。
    """
    if not isinstance(value, dict):
        raise AppError(
            "E_INPUT_TYPE",
            "rates_paid_by_owner 须为对象 {amount, agreed, actually_paid}",
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
    "profits_tax": _validate_profits_input,
    "property_tax": _validate_property_input,
    "stamp_property": _validate_stamp_property_input,
    "stamp_stock": _validate_stamp_stock_input,
    "stamp_lease": _validate_stamp_lease_input,
}
