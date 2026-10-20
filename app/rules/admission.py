# -*- coding: utf-8 -*-
"""中立规则束准入（002 W5+ Green E1；Annex 005 §2.1/§15.1/§15.2）。

集中固定 schema／固定 P（独立不可变 canonical bytes＋digest）与活动年度参数
核验；HTTP adapter（app.api.rules_admission）与两纯引擎（app.engines.
allowances.check_availability）共用同一权威实现：

- `rules_schema_version` 必须为 2.0.0——「双方一致的旧 schema」不代表受支持；
- `frozen_semantics.profiles` 必须含固定 `part5_shared_v2`，且保存 digest 与
  保存内容自洽、保存内容 canonical bytes 等于发行版冻结锚（自洽非固定拒绝）；
- 薪俸/PA：目标年共享矩阵（全部活动节点）／年度宽减／税率带／PA 活动参数
  （利得两级、物业标准率、修葺比例、利得宽减：2024/25–2025/26 要求存在，
  2026/27 已核无利得宽减＝合法缺键）缺失或畸形 → `E_RULES_STATE_UNVERIFIABLE`，
  不补发行数据、不裸异常；
- 成功返回 None；纯函数、无网络、无锁内调用。

2026/27 无利得宽减条目属已核法律事实（Annex D 003:335–336,380），不得强添。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from app.core.errors import AppError
from app.core.hashing import canonical_json_bytes
from app.core.money import parse_amount, parse_ratio
from app.rules.bundles.allowances import (
    FIXED_PROFILE_CANONICAL_BYTES,
    FIXED_PROFILE_DIGEST,
)

_PROFILE_ID = "part5_shared_v2"
_V2_TAX_TYPES = ("salaries_tax", "personal_assessment")
# 005:66/:895：五税各自固定 profile 身份（两税共用 part5_shared_v2）。
FIXED_PROFILE_ID_BY_TAX = {
    "salaries_tax": "part5_shared_v2",
    "personal_assessment": "part5_shared_v2",
    "profits_tax": "profits_unincorporated_floor_chain",
    "property_tax": "property_per_unit_floor",
    "stamp_property": "avd_exact_base_ceil1",
    "stamp_stock": "stock_per_note_ceil1",
    "stamp_lease": "lease_ceil100_ceil1",
}
_REQUIRED_SCHEMA = "2.0.0"
# 已核无利得宽减的年度（Annex D）：该年 profits_reductions 缺键为合法事实。
# 同时供 PA 引擎消费路径（利得业务单位）判「合法缺项 vs 活动规则不可核验」。
VERIFIED_NO_PROFITS_REDUCTION_YEARS = ("2026_27",)

# 活动共享矩阵节点（005 §15.2；两税共读同一矩阵）。
_REQUIRED_ALLOWANCE_NODES = (
    "basic",
    "married",
    "child_base",
    "child_birth_year_additional",
    "child_following_year_additional",
    "parent_high",
    "parent_high_cohabitation",
    "parent_low",
    "parent_low_cohabitation",
    "sibling",
    "personal_disability",
    "disabled_dependant",
    "single_parent",
    "parent_cash_threshold",
)

# §15.2/005:839：唯一 salary/PA 宽减表按年度冻结 status——2024/25、2025/26
# 附表 43 有宽减（applicable）；2026/27 无条目＝已核不适用
# （not_applicable_verified），与固定 P `components.rebate_applicable_years`
# = ["2024_25", "2025_26"] 及束内 effective.reductions.to_year=2025_26 一致。
# 错误年度沿用该枚举（如把 2025/26 写为 not_applicable_verified）不得改写
# 冻结年度语义。
REDUCTION_STATUS_BY_YEAR = {
    "2024_25": "applicable",
    "2025_26": "applicable",
    "2026_27": "not_applicable_verified",
}

# §15.2：status 为封闭枚举；只有 2024/25–2025/26 的 following-year 节点为
# 已核 not_applicable_verified，2026/27 起全部 applicable。
_ALLOWANCE_STATUSES = ("applicable", "not_applicable_verified")
_NOT_APPLICABLE_ONLY_NODES = {
    "2024_25": frozenset({"child_following_year_additional"}),
    "2025_26": frozenset({"child_following_year_additional"}),
    "2026_27": frozenset(),
}
# 规则整数文本（width/threshold/profits cap；消费侧 int(text)）：非负 canonical
# 十进制、无符号/指数/空白/下划线/前导零；量级域同规则金额 1e14。
_UINT_TEXT_RE = re.compile(r"(0|[1-9][0-9]*)")
_MAX_RULE_UINT = 10**14
# 共享宽减表 percent_bp 为基点整数域：0..10000（100% 上限；005:839＋附表 43）。
# 2024/25–2025/26 生产值 10000（100%）；2026/27 生产值 0（无宽减）。
MAX_REDUCTION_PERCENT_BP = 10000


def verify_bundle_admission(
    content: Any, *, tax_type: str, year: str | None
) -> None:
    """规则束准入（失败 → E_RULES_STATE_UNVERIFIABLE；成功 None）。"""
    if not isinstance(content, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "规则束内容形状损坏（非对象）；拒算（fail-closed）。",
        )
    schema = content.get("rules_schema_version")
    if schema != _REQUIRED_SCHEMA:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"当前规则束 rules_schema_version={schema!r} 与引擎要求的 "
            f"{_REQUIRED_SCHEMA} 不兼容；拒算（fail-closed，不静默迁移）。",
        )
    _verify_fixed_profile(content)
    if tax_type in FIXED_PROFILE_ID_BY_TAX:
        _verify_engine_profile_content(content, tax_type)
    if tax_type in _V2_TAX_TYPES:
        if not isinstance(year, str) or not year:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                "无法定位目标年度以核验活动规则组件；拒算（fail-closed）。",
            )
        _verify_active_rules(content, tax_type=tax_type, year=year)


def verify_fixed_profile(content: Any, *, tax_type: str | None = None) -> None:
    """005 §15.1/§15.4：固定 P/profile 独立核验（仅内容身份，不含年度活动性）。

    - 恒核验两税共享固定 P `part5_shared_v2`（digest 自洽＋canonical bytes＝
      冻结锚）；
    - 给定五税 `tax_type` 时，同一入口核验该引擎固定 profile 的**完整 canonical
      内容**与发行固定内容锚相等（名称/engines 相同但 rule 被改 → 拒绝）。
    - strict replay 的兼容性阶段用此入口，避免把年度规则完整性误归为 profile
      不兼容（后者应为 context 阶段的 422）。
    """
    if not isinstance(content, Mapping):
        raise _rules_error(
            None, "规则束内容形状损坏（非对象）；拒算（fail-closed）。"
        )
    _verify_fixed_profile(content)
    if tax_type is not None:
        _verify_engine_profile_content(content, tax_type)


_LEGACY_PROFILE_ANCHORS: dict[str, bytes] | None = None


def _fixed_legacy_profile_bytes(profile_id: str) -> bytes | None:
    """发行固定五税 profile 的独立不可变 canonical bytes（首次调用时冻结）。

    冻结后不再读取 `_FROZEN_PROFILES`/`INITIAL_BUNDLE_CONTENT` 的可变对象，
    避免共享嵌套叶被运行期修改而改变锚。
    """
    global _LEGACY_PROFILE_ANCHORS
    if _LEGACY_PROFILE_ANCHORS is None:
        # 延迟导入：app.rules.bundle 依赖各 bundles 数据模块，避免导入期环。
        from app.rules.bundle import _FROZEN_PROFILES

        _LEGACY_PROFILE_ANCHORS = {
            str(profile["profile_id"]): canonical_json_bytes(profile)
            for profile in _FROZEN_PROFILES
        }
    return _LEGACY_PROFILE_ANCHORS.get(profile_id)


def _verify_engine_profile_content(content: Mapping[str, Any], tax_type: str) -> None:
    """五税固定 profile 内容锚：完整 canonical 内容必须等于发行固定内容。"""
    expected_id = FIXED_PROFILE_ID_BY_TAX.get(tax_type)
    if expected_id is None or expected_id == _PROFILE_ID:
        return
    expected_bytes = _fixed_legacy_profile_bytes(expected_id)
    if expected_bytes is None:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            f"引擎固定 profile 锚缺失：{expected_id}（实现错误，拒算）。",
        )
    frozen = content.get("frozen_semantics")
    profiles = frozen.get("profiles") if isinstance(frozen, Mapping) else None
    if profiles is not None and not isinstance(profiles, list):
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            "frozen_semantics.profiles 须为数组（规则不可核验，拒算）。",
        )
    stored = next(
        (
            profile
            for profile in (profiles or [])
            if isinstance(profile, Mapping)
            and profile.get("profile_id") == expected_id
        ),
        None,
    )
    if stored is None:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            f"规则束缺少本引擎固定 profile {expected_id}（005:66/:895）；"
            "拒算（fail-closed，不补默认 profile）。",
        )
    try:
        stored_bytes = canonical_json_bytes(dict(stored))
    except (TypeError, ValueError) as exc:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            f"固定 profile {expected_id} 无法 canonical 化；拒算（fail-closed）。",
        ) from exc
    if stored_bytes != expected_bytes:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            f"规则束固定 profile {expected_id} 内容与引擎支持的固定 P 不等"
            "（名称/engines 相同不足以放行；005:66/:895）；拒算（fail-closed）。",
        )


def _rules_error(field: str | None, message: str) -> AppError:
    return AppError("E_RULES_STATE_UNVERIFIABLE", message, field=field)


def _verify_fixed_profile(content: Mapping[str, Any]) -> None:
    """§15.1：固定 P 双向核验（存在、digest 自洽、canonical bytes＝冻结锚）。"""
    frozen = content.get("frozen_semantics")
    profiles = frozen.get("profiles") if isinstance(frozen, Mapping) else None
    # R2：profiles 容器形状先行核验——畸形（如 JSON 数字）→ 统一规则错，
    # 不得在迭代时泄漏 TypeError。
    if profiles is not None and not isinstance(profiles, list):
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            "frozen_semantics.profiles 须为数组（规则不可核验，拒算）。",
        )
    stored = next(
        (
            profile
            for profile in (profiles or [])
            if isinstance(profile, Mapping) and profile.get("profile_id") == _PROFILE_ID
        ),
        None,
    )
    if stored is None:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            f"规则束缺少固定语义 profile {_PROFILE_ID}（§15.1）；拒算"
            "（fail-closed，不补默认 profile）。",
        )
    core_profile = {
        key: value for key, value in stored.items() if key != "semantic_digest"
    }
    try:
        stored_bytes = canonical_json_bytes(core_profile)
    except (TypeError, ValueError) as exc:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            "固定语义 profile 无法 canonical 化复算 digest；拒算（fail-closed）。",
        ) from exc
    recomputed = "sha256:" + hashlib.sha256(stored_bytes).hexdigest()
    if stored.get("semantic_digest") != recomputed:
        raise _rules_error(
            "bundle.frozen_semantics.profiles.semantic_digest",
            "固定语义 profile 的 semantic_digest 与保存内容不符（§15.1）；"
            "拒算（fail-closed）。",
        )
    if stored_bytes != FIXED_PROFILE_CANONICAL_BYTES:
        raise _rules_error(
            "bundle.frozen_semantics.profiles",
            "规则束 profile 与引擎支持的固定 P 不等（自洽 digest 不足以放行，"
            f"§15.1；预期 {FIXED_PROFILE_DIGEST}）；拒算（fail-closed，不迁移）。",
        )


def _rule_money(text: Any, field: str) -> str:
    """规则金额（消费域＝Money/`parse_amount`；allow_negative=False）。

    准入域与 005 §3 Money 一致：ASCII 全匹配、长度 ≤32、|值| ≤1e14、小数 ≤2 位；
    `"1200000.00"` 合法（不得要求整数词法）；`"-1"`/`"1e3"`/前导零/空白一律拒。
    合法小数由消费侧 `int(Decimal(...))`/`Decimal(...)` 处理。
    """
    try:
        parse_amount(text, allow_negative=False)
    except AppError as exc:
        raise _rules_error(
            field, f"{field} 畸形（规则不可核验，拒算；不裸异常）。"
        ) from exc
    return text


def _rule_uint_text(text: Any, field: str) -> str:
    """规则整数文本（消费域＝`int(text)`：税率带 width、PA threshold/profits cap）。

    不以 `int()` 可执行当充分准入：须为非负 canonical 十进制（无正负号/指数/
    空白/下划线/前导零），长度 ≤15 且 ≤1e14（与规则金额量级域一致）。
    """
    if (
        not isinstance(text, str)
        or _UINT_TEXT_RE.fullmatch(text) is None
        or len(text) > 15
    ):
        raise _rules_error(field, f"{field} 缺失或非法（规则不可核验，拒算）。")
    if int(text) > _MAX_RULE_UINT:
        raise _rules_error(field, f"{field} 超过规则量级域 1e14（拒算）。")
    return text


def _rule_canonical_money(text: Any, field: str) -> str:
    """规则金额（消费域＝`parse_amount`：年度宽减 cap 经 C2.1 词法链路）。

    非负域显式固定（allow_negative=False）：负号/指数/前导零/超过 2 位小数/
    超过 1e14 均拒；`"3000.00"` 等合法尾零由消费侧 `Decimal` 处理。
    """
    if not isinstance(text, str):
        raise _rules_error(field, f"{field} 缺失或非法（规则不可核验，拒算）。")
    try:
        parse_amount(text, allow_negative=False)
    except AppError as exc:
        raise _rules_error(
            field, f"{field} 畸形（规则不可核验，拒算；不裸异常）。"
        ) from exc
    return text


def _rule_ratio(text: Any, field: str) -> None:
    """规则比率（消费域＝`parse_ratio`＋0<num≤den；PA 标准率/修葺比例）。"""
    if not isinstance(text, str):
        raise _rules_error(field, f"{field} 缺失或非法（规则不可核验，拒算）。")
    try:
        fraction = parse_ratio(text)
        if fraction <= 0 or fraction > 1:
            raise ValueError("ratio domain")
    except (AppError, ValueError, TypeError) as exc:
        raise _rules_error(
            field, f"{field} 畸形（规则不可核验，拒算；不裸异常）。"
        ) from exc


def _rule_int(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _rules_error(field, f"{field} 缺失或非法（规则不可核验，拒算）。")


def _rule_reduction_percent_bp(value: Any, field: str) -> None:
    """共享宽减表 percent_bp：基点整数域 0..10000（005:839；附表 43 ≤100%）。

    2024/25–2025/26 为 10000（100%）、2026/27 为 0；须为非 bool 整数，负值/
    越界/浮点/字符串/None 均拒，不以 Python bool 子类或 int() 可执行放行。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _rules_error(field, f"{field} 缺失或非法（规则不可核验，拒算）。")
    if not 0 <= value <= MAX_REDUCTION_PERCENT_BP:
        raise _rules_error(
            field,
            f"{field}={value} 超出基点域 0..{MAX_REDUCTION_PERCENT_BP}"
            "（005 §15.2/005:839＋附表 43；拒算）。",
        )


def _verify_active_rules(
    content: Mapping[str, Any], *, tax_type: str, year: str
) -> None:
    """§15.2＋R1④：活动矩阵/宽减/税率带/PA 活动参数缺键或畸形 → 拒算。"""
    data = content.get("data")
    if not isinstance(data, Mapping):
        raise _rules_error(
            "bundle.data", "规则束缺少 data 分区（§15.2）；拒算（fail-closed）。"
        )
    allowances = data.get("allowances")
    year_node = allowances.get(year) if isinstance(allowances, Mapping) else None
    if not isinstance(year_node, Mapping):
        raise _rules_error(
            f"data.allowances.{year}",
            f"共享免税额矩阵缺少 {year} 年度节点（§15.2）；拒算"
            "（fail-closed，不补发行数据）。",
        )
    for key in _REQUIRED_ALLOWANCE_NODES:
        entry = year_node.get(key)
        if not isinstance(entry, Mapping):
            raise _rules_error(
                f"data.allowances.{year}.{key}",
                f"规则矩阵缺少 allowances.{year}.{key}（缺键＝不可核验，拒算）。",
            )
        _rule_money(entry.get("amount"), f"data.allowances.{year}.{key}.amount")
        if "cap" not in entry:
            raise _rules_error(
                f"data.allowances.{year}.{key}.cap",
                f"allowances.{year}.{key} 缺少 cap 键（null 与缺键语义不同；"
                "规则不可核验，拒算）。",
            )
        cap = entry.get("cap")
        if cap is not None:
            _rule_money(cap, f"data.allowances.{year}.{key}.cap")
        status = entry.get("status")
        if status not in _ALLOWANCE_STATUSES:
            raise _rules_error(
                f"data.allowances.{year}.{key}.status",
                f"allowances.{year}.{key}.status 缺失或非法（须为封闭枚举；"
                "规则不可核验，拒算）。",
            )
        not_applicable_nodes = _NOT_APPLICABLE_ONLY_NODES.get(year)
        if not_applicable_nodes is not None:
            expected_status = (
                "not_applicable_verified"
                if key in not_applicable_nodes
                else "applicable"
            )
            if status != expected_status:
                raise _rules_error(
                    f"data.allowances.{year}.{key}.status",
                    f"allowances.{year}.{key}.status={status!r} 与 {year} 该节点"
                    f"适用语义不符（须为 {expected_status!r}，§15.2）；拒算。",
                )
    _rule_money(
        allowances.get("mpf_deduction_cap")
        if isinstance(allowances, Mapping)
        else None,
        "data.allowances.mpf_deduction_cap",
    )

    # 在册扣除枚举（ERCE 上限经 resolve_financials 消费：int(Decimal(caps[year]))）
    # ——存在即须可消费（当前年度；缺年由消费路径给既定规则错，语义归 E7 年度隔离）。
    salaries_region = data.get("salaries")
    deductions = (
        salaries_region.get("deductions")
        if isinstance(salaries_region, Mapping)
        else None
    )
    if deductions is not None:
        if not isinstance(deductions, Mapping):
            raise _rules_error(
                "data.salaries.deductions",
                "在册扣除枚举形状非法（不可核验，拒算）。",
            )
        for item_type, rule in deductions.items():
            item_field = f"data.salaries.deductions.{item_type}"
            if not isinstance(rule, Mapping):
                raise _rules_error(
                    item_field, f"{item_field} 形状非法（不可核验，拒算）。"
                )
            # 形状先验完整化（C2）：消费域＝`(rule.get("caps") or {}).get(year)`
            # ——None 允许（与消费一致）；非 Mapping 一律规则错（truthy 非 Mapping
            # 会在消费侧 AttributeError、falsy 非 Mapping 会静默视为无 cap）。
            caps = rule.get("caps")
            if caps is not None:
                if not isinstance(caps, Mapping):
                    raise _rules_error(
                        f"{item_field}.caps",
                        f"{item_field}.caps 形状非法（不可核验，拒算）。",
                    )
                if year in caps:
                    _rule_money(caps.get(year), f"{item_field}.caps.{year}")

    reductions = data.get("reductions")
    year_reduction = reductions.get(year) if isinstance(reductions, Mapping) else None
    if not isinstance(year_reduction, Mapping):
        raise _rules_error(
            f"data.reductions.{year}",
            f"年度宽减表缺少 {year} 条目（§15.2）；拒算（fail-closed）。",
        )
    status = year_reduction.get("status")
    expected_status = REDUCTION_STATUS_BY_YEAR.get(year)
    if expected_status is None:
        raise _rules_error(
            f"data.reductions.{year}.status",
            f"reductions.{year} 不在三年度冻结宽减表内（§15.2）；拒算"
            "（fail-closed）。",
        )
    if status != expected_status:
        raise _rules_error(
            f"data.reductions.{year}.status",
            f"reductions.{year}.status={status!r} 与 {year} 年度适用语义不符"
            f"（须为 {expected_status!r}，§15.2/005:839）；拒算。",
        )
    # 数值字段不论 status 均须可消费：not_applicable_verified 是「已核不适用」
    # 而非免于唯一宽减表 {percent_bp,cap} 的结构/域核验。
    _rule_reduction_percent_bp(
        year_reduction.get("percent_bp"), f"data.reductions.{year}.percent_bp"
    )
    _rule_canonical_money(year_reduction.get("cap"), f"data.reductions.{year}.cap")

    region_key = "salaries" if tax_type == "salaries_tax" else "personal_assessment"
    region = data.get(region_key)
    if not isinstance(region, Mapping):
        raise _rules_error(
            f"data.{region_key}",
            f"规则束缺少 {region_key} 数据区（§15.2）；拒算（fail-closed）。",
        )
    for key in ("progressive_bands", "standard_rate_bands"):
        bands = region.get(key)
        if not isinstance(bands, list) or not bands:
            raise _rules_error(
                f"data.{region_key}.{key}",
                f"活动税率带 {region_key}.{key} 缺失或为空（§15.2；R1/B1：不补"
                "发行数据）；拒算（fail-closed）。",
            )
        for index, band in enumerate(bands):
            field = f"data.{region_key}.{key}[{index}]"
            if not isinstance(band, Mapping):
                raise _rules_error(field, f"{field} 须为对象（规则不可核验，拒算）。")
            _rule_int(band.get("rate_bp"), f"{field}.rate_bp")
            if band.get("width") is not None:
                _rule_uint_text(band.get("width"), f"{field}.width")

    if tax_type != "personal_assessment":
        return
    # PA 活动参数：存在即须可核验（畸形 → 拒算）；缺失不在此处强求——
    # 消费路径（如利得业务单位）按活动性 fail-closed（E1：规则缺项不得
    # 静默计算，见 app.engines.personal_assessment）。
    tier = region.get("profits_two_tier")
    if tier is not None:
        if not isinstance(tier, Mapping):
            raise _rules_error(
                "data.personal_assessment.profits_two_tier",
                "活动参数 profits_two_tier 形状非法（不可核验，拒算）。",
            )
        _rule_uint_text(
            tier.get("threshold"), "data.personal_assessment.profits_two_tier.threshold"
        )
        _rule_int(
            tier.get("low_rate_bp"), "data.personal_assessment.profits_two_tier.low_rate_bp"
        )
        _rule_int(
            tier.get("high_rate_bp"), "data.personal_assessment.profits_two_tier.high_rate_bp"
        )
    for ratio_key in ("property_standard_rate", "repair_allowance_ratio"):
        value = region.get(ratio_key)
        if value is not None:
            _rule_ratio(value, f"data.personal_assessment.{ratio_key}")
    profits_reductions = region.get("profits_reductions")
    if profits_reductions is not None:
        if not isinstance(profits_reductions, Mapping):
            raise _rules_error(
                "data.personal_assessment.profits_reductions",
                "活动参数 profits_reductions 形状非法（不可核验，拒算）。",
            )
        year_entry = profits_reductions.get(year)
        if year_entry is not None:
            if not isinstance(year_entry, Mapping):
                raise _rules_error(
                    f"data.personal_assessment.profits_reductions.{year}",
                    "活动利得宽减条目形状非法（不可核验，拒算）。",
                )
            _rule_int(
                year_entry.get("percent_bp"),
                f"data.personal_assessment.profits_reductions.{year}.percent_bp",
            )
            _rule_uint_text(
                year_entry.get("cap"),
                f"data.personal_assessment.profits_reductions.{year}.cap",
            )


__all__ = [
    "verify_bundle_admission",
    "verify_fixed_profile",
    "FIXED_PROFILE_ID_BY_TAX",
    "VERIFIED_NO_PROFITS_REDUCTION_YEARS",
    "REDUCTION_STATUS_BY_YEAR",
    "MAX_REDUCTION_PERCENT_BP",
]
