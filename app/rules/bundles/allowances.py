# -*- coding: utf-8 -*-
"""共享 Part 5 免税额矩阵与冻结语义 profile（002 P1；Annex 005 §15.1/§15.2）。

- `data.allowances.<YA>.<K>`：唯一三年度矩阵（键缺失＝规则不可核验 →
  E_RULES_STATE_UNVERIFIABLE；`not_applicable_verified`＝已核不适用，计 0）。
- `data.allowances.mpf_deduction_cap`：唯一 MPF 强制性供款扣除上限数值。
- `data.reductions.<YA>`：唯一 salary/PA 年度宽减表（percent_bp/cap/status）。
- `PROFILE_PART5_SHARED_V2`：固定语义 profile P＋semantic_digest
  （sha256(canonical_json_bytes(P))，不纳入金额/哈希/排版）。

数值来源（T1；Annex 005 §15.2 转引附表 4/43）：
- 2024/25–2025/26：基本 132,000／已婚 264,000／子女 130,000＋出生年额外
  130,000（组上限 1,170,000；(1B) 已核不适用）／父母 60+ 50,000、55–59
  25,000（全年同住额外同额）／兄弟姊妹 37,500／伤残类各 75,000／单亲
  132,000／供养现金门槛 12,000。
- 2026/27：基本 145,000／已婚 290,000／子女 140,000＋出生年额外 140,000＋
  出生次年额外 140,000（组上限 1,260,000）／父母 55,000／27,500／单亲
  145,000。
- 附表 43 宽减：2024/25 100%/$1,500；2025/26 100%/$3,000；2026/27 无条目。
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

MPF_DEDUCTION_CAP = "18000"

_YEARS_EARLY: tuple[str, ...] = ("2024_25", "2025_26")


def _node(amount: str, cap: str | None = None, status: str = "applicable") -> dict:
    return {"amount": amount, "cap": cap, "status": status}


def _early_year() -> dict:
    return {
        "basic": _node("132000"),
        "married": _node("264000"),
        "child_base": _node("130000", "1170000"),
        "child_birth_year_additional": _node("130000", "1170000"),
        "child_following_year_additional": _node("0", "0", "not_applicable_verified"),
        "parent_high": _node("50000"),
        "parent_high_cohabitation": _node("50000"),
        "parent_low": _node("25000"),
        "parent_low_cohabitation": _node("25000"),
        "sibling": _node("37500"),
        "personal_disability": _node("75000"),
        "disabled_dependant": _node("75000"),
        "single_parent": _node("132000"),
        "parent_cash_threshold": _node("12000"),
    }


def _late_year() -> dict:
    return {
        "basic": _node("145000"),
        "married": _node("290000"),
        "child_base": _node("140000", "1260000"),
        "child_birth_year_additional": _node("140000", "1260000"),
        "child_following_year_additional": _node("140000", "1260000"),
        "parent_high": _node("55000"),
        "parent_high_cohabitation": _node("55000"),
        "parent_low": _node("27500"),
        "parent_low_cohabitation": _node("27500"),
        "sibling": _node("37500"),
        "personal_disability": _node("75000"),
        "disabled_dependant": _node("75000"),
        "single_parent": _node("145000"),
        "parent_cash_threshold": _node("12000"),
    }


DATA: dict = {
    "2024_25": _early_year(),
    "2025_26": _early_year(),
    "2026_27": _late_year(),
    "mpf_deduction_cap": MPF_DEDUCTION_CAP,
}

REDUCTIONS: dict = {
    "2024_25": {"percent_bp": 10000, "cap": "1500", "status": "applicable"},
    "2025_26": {"percent_bp": 10000, "cap": "3000", "status": "applicable"},
    "2026_27": {"percent_bp": 0, "cap": "0", "status": "not_applicable_verified"},
}

# Annex 005 §15.1 固定语义 profile P（逐字冻结；改变谓词/顺序/候选语义必须
# 同步修订 P 与 digest，不得保持同标识而换实现）。
_PROFILE_P: dict = {
    "profile_id": "part5_shared_v2",
    "profile_version": 1,
    "engines": ["personal_assessment", "salaries_tax"],
    "input_schema_version": "2.0.0",
    "contracts": {
        "facts": "subject_relation_year_v2",
        "temporal": "coverage_three_value_clause_quantifiers_v2_salary_window",
        "entitlement": "independent_of_claim_intent_v2_ownership_twopass",
        "ledger": "per_scenario_typed_mutex_enbloc_v2_ownership",
        "candidates": "stage1_explicit_actors_units_effective_v2",
        "future": "next_year_reparse_no_fallback_v1",
        "assumptions": ["same_income", "same_mpf"],
        "selection": "unknown_competitor_blocks_best_v1",
        "money": "annex_c_c2_exact_v1",
        "trace": "stage1_tagged_steps_v2_candidate_checks",
    },
    "allowance_groups": {
        "child": ["base", "birth_year", "following_year"],
        "child_cap_mode": "independent_per_group",
        "grandparent_amount_source": "parent",
        "following_year_applicable": ["2026_27"],
    },
    "rounding": {
        "salary": "salary_floor_min_v1",
        "pa_individual": "pa_unit_floor_min_v1",
        "pa_joint": "pa_unit_floor_min_v1",
        "pa_no_pa_salary": "pa_stc_comp_tp_v1",
        "pa_no_pa_property": "property_per_assessment_floor_v1",
        "pa_no_pa_business": "approved_unincorporated_floor_branch_v1",
    },
    "components": {
        "salary_partial_balance": "null",
        "pa_status_ceiling": "partial",
        "pa_balance": "null",
        "payment_comparison": "out_of_scope",
        "rebate_applicable_years": ["2024_25", "2025_26"],
    },
}


def _canonical_profile_bytes(profile: dict) -> bytes:
    """C2.7 canonical JSON：键排序、UTF-8、无空白分隔符、拒绝重复键。"""
    return json.dumps(
        profile, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


# 固定 P 的独立不可变锚（E1）：canonical bytes＋digest 于导入期一次性冻结；
# 任何共享嵌套对象在运行期被原地污染都不得改变预期锚。
FIXED_PROFILE_CANONICAL_BYTES: bytes = _canonical_profile_bytes(_PROFILE_P)
FIXED_PROFILE_DIGEST: str = (
    "sha256:" + hashlib.sha256(FIXED_PROFILE_CANONICAL_BYTES).hexdigest()
)


def profile_entry(profile: dict | None = None) -> dict:
    """返回 P 加 semantic_digest 的**独立深副本**（不对含自身 digest 的对象再哈希）。

    深副本隔离嵌套固定 P：调用方原地 mutation（含嵌套 dict/list）不得改变发行束
    同一公开 profile，亦不得改变后续 profile_entry() 结果（E1/A5）。
    """
    source: Any = _PROFILE_P if profile is None else profile
    entry = copy.deepcopy({key: source[key] for key in source})
    digest = "sha256:" + hashlib.sha256(_canonical_profile_bytes(source)).hexdigest()
    entry["semantic_digest"] = digest
    return entry


PROFILE_PART5_SHARED_V2: dict = profile_entry()
