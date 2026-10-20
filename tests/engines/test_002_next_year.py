# -*- coding: utf-8 -*-
"""REQ-9 下一年度、具名假设与可用性（002 P1 Red；Annex 005）。

REQ: REQ-9（S002 §8/§9.4；PRD AC-9）。本文件为 SDD §9.4 REQ-9 命名节点，共 8 个：
  - test_002_missing_target_rules_null_partial_no_fallback
  - test_002_known_target_reparse_birth_ages_caps
  - test_002_assumptions_only_same_income_same_mpf
  - test_002_assumption_conflict_explicit_rejected
  - test_002_no_copy_role_education_selection_claims_to_next
  - test_002_missing_next_fact_keeps_current_final
  - test_002_salary_partial_balance_null
  - test_002_availability_context_no_default_permission

规格锚点:
  - S002 §5.5＋§8 REQ-9；Annex 005 §2/§2.1（AvailabilityContext 显式参数；
    缺 context 或不匹配即 E_RULES_STATE_UNVERIFIABLE；无默认放行）、§2 键年
    规则、§10（组件适用表：next 缺 → null＋missing；partial → balance null）、
    §11（Assumption 仅 same_income/same_mpf；每人每 kind ≤1；target 必须紧邻
    且在请求 years 中；显式目标金额与假设异额 → E_INPUT_CONTRADICTORY；
    稳定复用仅 ID/birth_date；不复制 role/claims/选择/教育 TF；有规则时按
    原始事实重新解析）、§13/§14、§15.2（三年度矩阵）。

期望值来源（禁从生产代码反推）:
  - 免税额/宽减：Annex 005 §15.2（附表 4/43 T1）；税率带：附表 1/2（T1）。
  - PAM39 Q1 final 940（2025/26）与 Q2 PST 35,890（2026/27、480k/18k）：
    T2（台账 1.13）。
  - 其余数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None)（005 §2.1）。
  - 下一年缺规则/事实/guard → next_provisional=null、status=partial、
    missing_components 标注、本年 final 保留、balance=null；绝不回退本年规则；
    有规则时按下一年原始事实重新解析（年龄档/出生额外额/独立 cap/资格）；
    假设白名单仅 same_income/same_mpf；无 availability 或 context 不匹配 →
    AppError E_RULES_STATE_UNVERIFIABLE。
  - 本文件对 app.* 延迟导入（测试体内）。
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

import pytest


_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


# ---------------------------------------------------------------------------
# v2 规则束夹具（金额全部取自 Annex 005 §15.2＝附表 4/43 T1；税率带＝附表 1/2 T1）
# ---------------------------------------------------------------------------


def _node(amount: str, cap: str | None = None, status: str = "applicable") -> dict:
    return {"amount": amount, "cap": cap, "status": status}


def _matrix_years_24_25() -> dict:
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


def _matrix() -> dict:
    return {
        "2024_25": _matrix_years_24_25(),
        "2025_26": _matrix_years_24_25(),
        "2026_27": {
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
        },
        "mpf_deduction_cap": "18000",
    }


def _reductions() -> dict:
    return {
        "2024_25": {"percent_bp": 10000, "cap": "1500", "status": "applicable"},
        "2025_26": {"percent_bp": 10000, "cap": "3000", "status": "applicable"},
        "2026_27": {"percent_bp": 0, "cap": "0", "status": "not_applicable_verified"},
    }


_PROGRESSIVE = [
    {"width": "50000", "rate_bp": 200},
    {"width": "50000", "rate_bp": 600},
    {"width": "50000", "rate_bp": 1000},
    {"width": "50000", "rate_bp": 1400},
    {"rate_bp": 1700},
]
_STANDARD = [
    {"width": "5000000", "rate_bp": 1500},
    {"rate_bp": 1600},
]


def _profile_entry() -> dict:
    """Annex 005 §15.1 固定语义 profile P（逐字转录）＋按同节算法计算 digest。"""
    p = {
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
    canonical = json.dumps(p, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    entry = dict(p)
    entry["semantic_digest"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return entry


def _v2_bundle() -> dict:
    """测试注入用 v2 规则束（RuleBundleContent 六字段形状；005 §15.1/§15.2）。"""
    m = _matrix()
    v1_compat_salaries = {
        y: {
            "basic": m[y]["basic"]["amount"],
            "married": m[y]["married"]["amount"],
            "child": m[y]["child_base"]["amount"],
            "child_birth_year_additional": m[y]["child_birth_year_additional"]["amount"],
            "child_aggregate_cap": m[y]["child_base"]["cap"],
            "parent_aged_60_or_above": m[y]["parent_high"]["amount"],
            "parent_aged_55_to_59": m[y]["parent_low"]["amount"],
        }
        for y in ("2024_25", "2025_26", "2026_27")
    }
    v1_compat_pa = {
        y: {"basic": m[y]["basic"]["amount"], "married": m[y]["married"]["amount"]}
        for y in ("2024_25", "2025_26", "2026_27")
    }
    return {
        "rules_schema_version": "2.0.0",
        "applicability": {"years": ["2024_25", "2025_26", "2026_27"]},
        "effective": {"allowances": {"from_year": "2024_25"}},
        "evidence_digests": [],
        "frozen_semantics": {"profiles": [_profile_entry()]},
        "data": {
            "allowances": m,
            "reductions": _reductions(),
            "salaries": {
                "progressive_bands": _PROGRESSIVE,
                "standard_rate_bands": _STANDARD,
                "deductions": {
                    "elder_residential_care": {
                        "label_zh": "长者住宿照顾开支（附表 3C）",
                        "caps": {"2024_25": "100000", "2025_26": "100000", "2026_27": "110000"},
                    }
                },
                # 旧 1.0.0 形状残留键：仅令 Red 阶段 v1 引擎不因缺键崩溃。
                "allowances": v1_compat_salaries,
                "mpf_deduction_cap": m["mpf_deduction_cap"],
                "reductions": {
                    "2024_25": {"percent_bp": 10000, "cap": "1500"},
                    "2025_26": {"percent_bp": 10000, "cap": "3000"},
                },
            },
            # P1 r1 纠错（复核 §五.5 PA 参数不完整；与 test_002_pa_allowances
            # 固定束同形）：固定束须含 PA 段——本用例走 PA 引擎，缺段即
            # KeyError 而非断言失败。progressive/standard bands 为引擎直读
            # 键（personal_assessment.py 直取）；四项 PA 活动参数（物业/利得
            # 比较）按 005 §2.1：A6 修复删除 _APPROVED_PA_DATA 运行时回退后，
            # 活动参数缺失＝E_RULES_STATE_UNVERIFIABLE。数值＝T1（附表
            # 1/3C/两级利得；与现 v2 生产束同值；两级结构 threshold/低 7.5%/
            # 高 15%）。
            "personal_assessment": {
                "progressive_bands": _PROGRESSIVE,
                "standard_rate_bands": _STANDARD,
                "property_standard_rate": "15/100",
                "repair_allowance_ratio": "1/5",
                "profits_two_tier": {"threshold": "2000000", "low_rate_bp": 750,
                                     "high_rate_bp": 1500},
                "profits_reductions": {
                    "2024_25": {"percent_bp": 10000, "cap": "1500"},
                    "2025_26": {"percent_bp": 10000, "cap": "3000"},
                },
                # 旧 1.0.0 形状残留键：仅令 Red 阶段 v1 引擎不因缺键崩溃。
                "allowances": v1_compat_pa,
                "mpf_deduction_cap": m["mpf_deduction_cap"],
                "reductions": {
                    "2024_25": {"percent_bp": 10000, "cap": "1500"},
                    "2025_26": {"percent_bp": 10000, "cap": "3000"},
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# v2 input 构造（Annex 005 §3/§4/§5.1–§5.3 示例形状）
# ---------------------------------------------------------------------------


def _window(year: str) -> tuple[str, str]:
    start = int(year[:4])
    return f"{start}-04-01", f"{start + 1}-04-01"


def _tf(year: str, value: Any, frm: str | None = None, to: str | None = None) -> list[dict]:
    w_from, w_to = _window(year)
    return [{"from": frm or w_from, "to": to or w_to, "value": value}]


def _fin(*, nai: str = "240000", mpf: str = "9000", ai: Any = True, **over: Any) -> dict:
    f: dict[str, Any] = {
        "employment_income_nai": nai,
        "mpf_mandatory_contributions": mpf,
        "has_assessable_salary_income": ai,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
    }
    f.update(over)
    return f


def _base_year(
    year: str,
    *,
    subject_facts: dict | None = None,
    household: dict | None = None,
    relations: list | None = None,
    financials: dict | None = None,
    care_payments: list | None = None,
    claims: list | None = None,
    assessment: dict | None = None,
    participants: list | None = None,
    provisional_paid: str | None = "0",
) -> dict:
    w_from, w_to = _window(year)
    year_obj: dict[str, Any] = {
        "participants": participants or [{"person_id": "p1", "role": "self"}],
        "subject_facts": dict(subject_facts or {}),
        "household": household
        or {
            "marriage": [{"from": w_from, "to": w_to, "value": "unmarried"}],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": relations or [],
        "financials": financials or {"p1": _fin()},
        "care_payments": care_payments or [],
        "claims": claims or [],
        "assessment": assessment
        or {"basis": "actual", "candidates": ["salary_separate"], "selection": "salary_separate"},
        "manual_issues": [],
    }
    if provisional_paid is not None:
        year_obj["provisional_paid"] = provisional_paid
    return year_obj


def _input(
    year: str,
    subjects: list,
    *,
    years: dict | None = None,
    assumptions: list | None = None,
    **year_kw: Any,
) -> dict:
    """v2 薪俸输入；years 可显式提供多年（目标＋紧邻下一年）。"""
    return {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": years or {year: _base_year(year, **year_kw)},
        "hk_presence_days": [],
        "assumptions": assumptions or [],
    }


def _relation(
    year: str,
    rid: str,
    actor: str,
    subject: str,
    *,
    kind: str = "child",
    basis: str = "birth",
    maintained: list | None = None,
) -> dict:
    r: dict[str, Any] = {
        "relation_id": rid,
        "actor_id": actor,
        "subject_id": subject,
        "kinship": {"kind": kind, "basis": basis, "during": _tf(year, True)},
    }
    if maintained is not None:
        r["maintained"] = maintained
    return r


def _claim(cid: str, kind: str, claimant: str = "p1", subject: str = "d1") -> dict:
    return {"claim_id": cid, "kind": kind, "claimant_id": claimant, "subject_id": subject}


def _subjects_single(dependants: list[tuple[str, str]] | None = None) -> list[dict]:
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    subjects.extend({"subject_id": sid, "birth_date": birth} for sid, birth in (dependants or []))
    return subjects


# ---------------------------------------------------------------------------
# 引擎调用（Annex 005 §2.1 v2 签名）
# ---------------------------------------------------------------------------


def _bundle_hash(bundle: Mapping) -> str:
    """实际完整束六字段 canonical 内容 hash（C2.8；复核 §五.4 纠错）。"""
    from app.core.hashing import bundle_content_hash  # 延迟导入

    return bundle_content_hash(bundle)


def _availability(bundle: Mapping, year: str) -> dict:
    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    matrix = bundle["data"]["allowances"]
    return {
        "binding": {
            "bundle_id": "b-002-red-fixture",
            # P1 r1 纠错（复核 §五.4）：hash 占位 → 实际完整束六字段 canonical
            # 内容 hash（C2.8；束变更后随内容重算，不豁免 verifier）。
            "bundle_hash": _bundle_hash(bundle),
            "rules_schema_version": bundle["rules_schema_version"],
            "engine_version": "0.2.0",
        },
        "current": {"year": year, "status": "available", "reason_codes": []},
        "next": {
            "year": following,
            "status": "available" if following in matrix else "missing_rules",
            "reason_codes": [],
        },
    }


def _run(v2_input: dict, *, availability: dict | None = None) -> dict:
    from app.engines.salaries import calculate_salaries_tax  # 延迟导入：Red=缺 v2 契约

    bundle = _v2_bundle()
    ctx = availability if availability is not None else _availability(
        bundle, v2_input["year_of_assessment"]
    )
    return calculate_salaries_tax(v2_input, bundle, availability=ctx)


def _run_pa(v2_input: dict, *, availability: dict | None = None) -> dict:
    from app.engines.personal_assessment import compare_personal_assessment

    bundle = _v2_bundle()
    ctx = availability if availability is not None else _availability(
        bundle, v2_input["year_of_assessment"]
    )
    return compare_personal_assessment(v2_input, bundle, availability=ctx)


# ---------------------------------------------------------------------------
# 断言辅助（005 §13/§14）
# ---------------------------------------------------------------------------


def _steps(result: Mapping, kind: str) -> list[dict]:
    return [
        s.get("detail") or {}
        for s in (result.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == kind and isinstance(s.get("detail"), dict)
    ]


def _missing_blob(result: Mapping) -> str:
    return json.dumps(result.get("missing_components") or [], ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# REQ-9 命名节点（S002 §9.4）
# ---------------------------------------------------------------------------


def test_002_missing_target_rules_null_partial_no_fallback() -> None:
    """下一年（2027/28）无规则 → next_provisional=null、partial、missing 标注、
    本年 final 保留；不得回退本年规则估算。

    规格锚点：S002 §5.5＋Annex 005 §2（缺许可不默认未来计算）＋§10。
    T3：2026/27、480k/18k → NCI=480,000−18,000−145,000=317,000 →
    16,000+117,000×17%=35,890（PAM39 Q2，T2）；宽减 2026/27=0。
    若错误回退本年（2025/26）规则会得 basic 132,000/宽减 3,000 的不同值。
    """
    year = "2026_27"
    result = _run(_input(
        year,
        _subjects_single(),
        financials={"p1": _fin(nai="480000", mpf="18000")},
    ))
    assert result["status"] == "partial", result["status"]
    amounts = result["amounts"]
    assert amounts["final_after_reduction"] == "35890", amounts
    assert amounts["next_provisional"] is None, (
        f"2027/28 无规则 → next_provisional=null（不得回退本年规则），实际：{amounts!r}"
    )
    assert amounts["balance"] is None, amounts
    assert amounts["provisional_paid"] == "0", amounts
    assert "next" in _missing_blob(result).lower() or "2027_28" in _missing_blob(result), (
        f"missing_components 必须标注 next 组件缺失，实际：{_missing_blob(result)!r}"
    )


def test_002_known_target_reparse_birth_ages_caps() -> None:
    """目标年规则可用＋显式目标年事实 → 按下一年原始事实与规则重新解析
    （金额/出生额外额/独立 cap/宽减全部换用目标年）。

    规格锚点：Annex 005 §11（有规则时重新解析，不得复用本年解析对象）。
    T3：本年 2025/26（600k/18k＋child d1 born 2025-12-01 出生年）：
    Part5=132,000+130,000+130,000=392,000 → NCI=190,000 → 14,600；宽减 3,000
    → final 11,600。next 2026_27（同收入显式提交＋同 child claim 重新提交）：
    Part5=145,000+140,000+140,000(1B)=425,000 → NCI=157,000 → 9,980；
    宽减 0 → next 9,980（若沿用本年规则/对象会得 11,600 系数值）。
    """
    current, target = "2025_26", "2026_27"
    result = _run(_input(
        current,
        _subjects_single([("d1", "2025-12-01")]),
        years={
            current: _base_year(
                current,
                subject_facts={"d1": {"unmarried": [
                    {"from": "2025-12-01", "to": _window(current)[1], "value": True}
                ]}},
                relations=[_relation(current, "r1", "p1", "d1", maintained=[
                    {"from": "2025-12-01", "to": _window(current)[1], "value": True}
                ])],
                financials={"p1": _fin(nai="600000", mpf="18000")},
                claims=[_claim("c1", "child")],
            ),
            target: _base_year(
                target,
                subject_facts={"d1": {"unmarried": _tf(target, True)}},
                relations=[_relation(target, "r1", "p1", "d1", maintained=_tf(target, True))],
                financials={"p1": _fin(nai="600000", mpf="18000")},
                claims=[_claim("c1", "child")],
            ),
        },
    ))
    amounts = result["amounts"]
    assert amounts["final_after_reduction"] == "11600", amounts
    assert amounts["next_provisional"] == "9980", (
        f"next 必须按 2026/27 规则重解析（basic 145,000/child 140,000/(1B) "
        f"140,000/宽减 0 → 9,980），实际：{amounts!r}"
    )
    next_steps = _steps(result, "next_year")
    assert next_steps and next_steps[0].get("status") == "available", next_steps


def test_002_assumptions_only_same_income_same_mpf() -> None:
    """假设白名单：仅 same_income/same_mpf；其他 kind 或同人同 kind 重复 → 拒绝。

    规格锚点：Annex 005 §11（仅此两种假设；最多每人每 kind 一条）。
    """
    from app.core.errors import AppError  # 延迟导入：Red=缺 v2 契约

    current, target = "2025_26", "2026_27"
    years = {
        current: _base_year(current),
        target: _base_year(target, financials={"p1": _fin()}),
    }
    subjects = _subjects_single()

    try:
        _run(_input(
            current, subjects, years=years,
            assumptions=[{"assumption_id": "a1", "kind": "same_facts",
                          "person_id": "p1", "source_year": current, "target_year": target}],
        ))
    except AppError as exc:
        assert exc.code in ("E_INPUT_EXTRA", "E_INPUT_TYPE"), (
            f"非白名单假设 kind 必须拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "same_facts 不在假设白名单（005 §11 仅 same_income/same_mpf），必须拒绝"
        )

    try:
        _run(_input(
            current, subjects, years=years,
            assumptions=[
                {"assumption_id": "a1", "kind": "same_income",
                 "person_id": "p1", "source_year": current, "target_year": target},
                {"assumption_id": "a2", "kind": "same_income",
                 "person_id": "p1", "source_year": current, "target_year": target},
            ],
        ))
    except AppError as exc:
        assert exc.code in ("E_INPUT_EXTRA", "E_INPUT_CONTRADICTORY", "E_INPUT_TYPE"), (
            f"同人同 kind 重复假设必须拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("每人每 kind 至多一条假设（005 §11），重复必须拒绝")


@pytest.mark.parametrize("guard", ["available", "blocked"], ids=["available", "blocked"])
def test_002_assumption_conflict_explicit_rejected(guard: str) -> None:
    """显式目标金额与假设异额 → E_INPUT_CONTRADICTORY＋**完整目标 field**（CF1
    §13 完整 input 内容根路径：same_income 冲突定位
    `years.<target>.financials.<pid>.employment_income_nai`；不以内部键/当前
    实现字符串为规范）；同额 → 接受（显式事实为主）。guard available／blocked 独立
    参数化：next 被拒绝（blocked）时也不得吞掉已证假设矛盾（输入先验先于年度隔离）。

    规格锚点：Annex 005 §11＋§13（CF1 完整路径）。
    """
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    subjects = _subjects_single()
    same_income = [{"assumption_id": "a1", "kind": "same_income",
                    "person_id": "p1", "source_year": current, "target_year": target}]

    bundle = _v2_bundle()
    ctx = _availability(bundle, current)
    if guard == "blocked":
        ctx["next"] = {"year": target, "status": "blocked",
                       "reason_codes": ["GUARD_BLOCKED"]}
    try:
        _run(_input(
            current, subjects,
            years={current: _base_year(current),
                   target: _base_year(target, financials={"p1": _fin(nai="500000")})},
            assumptions=same_income,
        ), availability=ctx)
    except AppError as exc:
        expected = (
            "E_INPUT_CONTRADICTORY",
            f"years.{target}.financials.p1.employment_income_nai",
        )
        assert (exc.code, exc.field) == expected, (
            f"显式 500,000 与 same_income(240,000) 异额必须精确 {expected!r}"
            f"（CF1 完整目标 field；guard={guard}），实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"guard={guard}：显式目标金额与假设异额 → E_INPUT_CONTRADICTORY"
            "（不采用最后写入优先；输入先验先于年度隔离；005 §11）"
        )

    consistent = _run(_input(
        current, subjects,
        years={current: _base_year(current),
               target: _base_year(target, financials={"p1": _fin()})},
        assumptions=same_income,
    ))
    assert consistent["status"] in ("complete", "partial"), consistent["status"]


def test_002_no_copy_role_education_selection_claims_to_next() -> None:
    """不复制本年 education/claims/关系/role 到下一年：下一年**仍显式申索**成年
    子女（18–25 教育路径），但 education TF 缺失 → 不得沿用本年教育事实（next
    未产确定值）；下一年显式 education=true 对照 → 按下一年事实重解析（12,780）。

    规格锚点：Annex 005 §2（稳定复用仅 ID/birth_date）＋§11（有规则时按原始
    事实重新解析，不复用本年解析对象）＋§7（18–25 需 full_time_education）。
    T3（对照）：2026/27 480k/18k＋child 140,000：
    Part5=145,000+140,000=285,000 → NCI=177,000 →
    累进=1,000+3,000+5,000+27,000×14%=12,780；宽减 0 → 12,780。
    """
    current, target = "2025_26", "2026_27"
    subjects = _subjects_single([("d1", "2007-06-01")])

    def _current_year() -> dict:
        return _base_year(
            current,
            subject_facts={"d1": {
                "unmarried": [{"from": "2025-04-01", "to": _window(current)[1],
                               "value": True}],
                "full_time_education": _tf(current, True),
            }},
            relations=[_relation(current, "r1", "p1", "d1",
                                 maintained=_tf(current, True))],
            financials={"p1": _fin(nai="480000", mpf="18000")},
            claims=[_claim("c1", "child")],
        )

    def _target_year(education: Any) -> dict:
        facts = {"d1": {"unmarried": _tf(target, True)}}
        if education is not None:
            facts["d1"]["full_time_education"] = _tf(target, education)
        return _base_year(
            target,
            subject_facts=facts,
            relations=[_relation(target, "r1", "p1", "d1",
                                 maintained=_tf(target, True))],
            financials={"p1": _fin(nai="480000", mpf="18000")},
            claims=[_claim("c1", "child")],
        )

    # A) 下一年仍申索成年子女但 education 缺失：不得复制本年 education。
    missing = _run(_input(
        current, subjects,
        years={current: _current_year(), target: _target_year(None)},
    ))
    assert missing["status"] == "partial", missing["status"]
    assert missing["amounts"]["next_provisional"] is None, (
        f"下一年 education 缺失＝未提供（≠true）：不得继承本年教育事实，"
        f"实际：{missing['amounts']!r}"
    )

    # B) 对照：下一年显式 education=true → 按下一年事实重解析（12,780）。
    explicit = _run(_input(
        current, subjects,
        years={current: _current_year(), target: _target_year(True)},
    ))
    assert explicit["amounts"]["next_provisional"] == "12780", (
        f"下一年显式 education=true 须按该年事实计入子女额（12,780），"
        f"实际：{explicit['amounts']!r}"
    )


def test_002_next_year_does_not_inherit_child_claim_pst_35890() -> None:
    """§3/§11：下一年不复制本年 claims——本年 child claim＋受养事实成立，下一年
    对象保留同一受养人事实但 claims=[]（未重新申索）→ next 按该年事实重解析＝
    35,890，不继承子女额（若错误继承会得 12,780）；本年子女额照常计入 13,000。

    T3：本年 2025/26 480k/18k＋child：Part5=132,000+130,000=262,000 →
    NCI=200,000 → 累进 16,000；宽减 3,000 → final 13,000。
    next 2026/27 480k/18k（无 claim）：Part5=145,000 → NCI=317,000 → 35,890。
    """
    current, target = "2025_26", "2026_27"
    subjects = _subjects_single([("d1", "2018-05-01")])
    current_year = _base_year(
        current,
        subject_facts={"d1": {"unmarried": _tf(current, True)}},
        relations=[_relation(current, "r1", "p1", "d1",
                             maintained=_tf(current, True))],
        financials={"p1": _fin(nai="480000", mpf="18000")},
        claims=[_claim("c1", "child")],
    )
    target_year = _base_year(
        target,
        subject_facts={"d1": {"unmarried": _tf(target, True)}},
        relations=[_relation(target, "r1", "p1", "d1",
                             maintained=_tf(target, True))],
        financials={"p1": _fin(nai="480000", mpf="18000")},
        claims=[],
    )
    result = _run(_input(
        current, subjects,
        years={current: current_year, target: target_year},
    ))
    amounts = result["amounts"]
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), sorted(amounts)
    assert amounts["final_after_reduction"] == "13000", (
        f"本年 child claim＋受养事实成立 → 子女额计入（final 13,000，T3），"
        f"实际：{amounts!r}"
    )
    assert amounts["next_provisional"] == "35890", (
        f"下一年 claims=[] 未重新申索 → 不得继承本年 child claim/子女额"
        f"（next 35,890；若继承会得 12,780），实际：{amounts!r}"
    )


def test_002_missing_next_fact_keeps_current_final() -> None:
    """目标年（2026_27）规则可用但目标年 Year 键缺失 → next=null、partial、
    本年 final 保留。

    规格锚点：S002 §5.5＋Annex 005 §11（缺失即 next unavailable、本年 final
    保留）。
    T3：本年 2025/26、240k/9k → final 940（PAM39 Q1，T2）。
    """
    year = "2025_26"
    result = _run(_input(year, _subjects_single()))
    assert result["status"] == "partial", result["status"]
    amounts = result["amounts"]
    assert amounts["final_after_reduction"] == "940", amounts
    assert amounts["next_provisional"] is None, amounts
    assert "next" in _missing_blob(result).lower() or "2026_27" in _missing_blob(result), (
        f"missing_components 必须标注 next 组件缺失，实际：{_missing_blob(result)!r}"
    )


def test_002_salary_partial_balance_null() -> None:
    """salary 任一 partial（next 缺）→ balance=null，即使 provisional_paid
    已知（显式 "0"）。

    规格锚点：Annex 005 §10（任何 partial 一律 null；仅 complete 且 paid 已知
    时按 final−paid）＋§14 示例。
    """
    year = "2025_26"
    result = _run(_input(year, _subjects_single(), provisional_paid="0"))
    assert result["status"] == "partial", result["status"]
    amounts = result["amounts"]
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), sorted(amounts)
    assert amounts["provisional_paid"] == "0", amounts
    assert amounts["final_after_reduction"] == "940", amounts
    assert amounts["next_provisional"] is None, amounts
    assert amounts["balance"] is None, (
        f"partial 一律 balance=null（不得用 final−0=940 冒充结欠），实际：{amounts!r}"
    )


def test_002_availability_context_no_default_permission() -> None:
    """缺 AvailabilityContext 或 context 与目标年不匹配 →
    AppError E_RULES_STATE_UNVERIFIABLE（无默认放行）。

    规格锚点：Annex 005 §2/§2.1（None 仅保留签名兼容，不是允许默认）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    facts = _input(year, _subjects_single())
    bundle = _v2_bundle()

    from app.engines.salaries import calculate_salaries_tax

    try:
        calculate_salaries_tax(facts, bundle)  # 不传 availability：不得默认放行
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"缺 availability → E_RULES_STATE_UNVERIFIABLE，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "缺 AvailabilityContext 不得默认放行（005 §2.1：None 不是允许默认）"
        )

    stale_ctx = _availability(bundle, year)
    stale_ctx["current"] = {"year": "2024_25", "status": "available", "reason_codes": []}
    try:
        calculate_salaries_tax(facts, bundle, availability=stale_ctx)
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"context 与目标年不一致必须拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("availability context 必须与目标＋紧邻下一年一致（005 §2.1）")


# ---------------------------------------------------------------------------
# P1 r1 新 Red（复核 §六.5/§六.9）：规则束准入与年度隔离
# ---------------------------------------------------------------------------


def _bundle_variant(mutate) -> tuple:
    """深拷贝固定束并应用变异；返回 (variant, ctx)（ctx 与变体各维度一致，
    除被测维度外）。"""
    import copy

    bundle = copy.deepcopy(_v2_bundle())
    mutate(bundle)
    ctx = _availability(bundle, "2025_26")
    return bundle, ctx


def test_002_rules_bundle_admission_negative_matrix() -> None:
    """P1 r1 新 Red（复核 §六.5/A5）：束身份/规则完整性准入负例——
    错 hash、内容变未更新 hash、schema 不一致、缺 profile、P 自洽非固定、
    digest 错、活动节点/宽减缺失 → E_RULES_STATE_UNVERIFIABLE；
    current blocked 拒绝、next blocked 仅独立抑制（current 保留）。

    规格锚点：Annex 005 §2.1（context 与实际传入 bundle 的 hash/schema、
    执行 engine 版本一致）＋§15.1（固定 P＋digest 双向核验；不得只验自洽）
    ＋§15.2（三年度节点缺失＝规则不可核验，fail-closed）。
    """
    from app.core.errors import AppError
    from app.engines.salaries import calculate_salaries_tax

    def expect_unverifiable(label, bundle, ctx):
        facts = _input("2025_26", _subjects_single())
        try:
            calculate_salaries_tax(facts, bundle, availability=ctx)
        except AppError as exc:
            assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
                f"{label}：束身份/完整性不可核验须 E_RULES_STATE_UNVERIFIABLE，"
                f"实际 {exc.code!r}"
            )
        else:
            raise AssertionError(
                f"{label}：不得以不可核验的规则束计算（005 §2.1/§15.1/§15.2）"
            )

    # (1) binding.bundle_hash 与实际六字段内容 hash 不符。
    bundle = _v2_bundle()
    ctx = _availability(bundle, "2025_26")
    ctx["binding"]["bundle_hash"] = "sha256:" + "0" * 64
    expect_unverifiable("binding hash 错误", bundle, ctx)

    # (2) 束内容变更而 binding hash 未随内容重算（旧 hash 残留）。
    old_hash = _bundle_hash(_v2_bundle())
    bundle = _v2_bundle()
    bundle["data"]["allowances"]["2025_26"]["basic"]["amount"] = "999000"
    ctx = _availability(bundle, "2025_26")
    ctx["binding"]["bundle_hash"] = old_hash
    expect_unverifiable("内容变未更新 hash", bundle, ctx)

    # (3) binding.rules_schema_version 与束不一致（旧 schema）。
    bundle = _v2_bundle()
    ctx = _availability(bundle, "2025_26")
    ctx["binding"]["rules_schema_version"] = "1.0.0"
    expect_unverifiable("schema 不一致", bundle, ctx)

    # (4) frozen_semantics 缺 part5_shared_v2 profile。
    def _drop_profile(b):
        b["frozen_semantics"]["profiles"] = [
            p for p in b["frozen_semantics"]["profiles"]
            if p.get("profile_id") != "part5_shared_v2"
        ]
    bundle, ctx = _bundle_variant(_drop_profile)
    expect_unverifiable("缺固定 profile", bundle, ctx)

    # (5) P 被改动且 digest 随之重算（自洽但非引擎固定 P）。
    def _mutate_p_selfconsistent(b):
        import hashlib

        for p in b["frozen_semantics"]["profiles"]:
            if p.get("profile_id") == "part5_shared_v2":
                p["contracts"] = dict(p["contracts"], facts="mutated_v9")
                body = {k: v for k, v in p.items() if k != "semantic_digest"}
                p["semantic_digest"] = "sha256:" + hashlib.sha256(
                    json.dumps(body, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
                ).hexdigest()
    bundle, ctx = _bundle_variant(_mutate_p_selfconsistent)
    expect_unverifiable("P 自洽非固定", bundle, ctx)

    # (6) digest 与 P 不符。
    def _wrong_digest(b):
        for p in b["frozen_semantics"]["profiles"]:
            if p.get("profile_id") == "part5_shared_v2":
                p["semantic_digest"] = "sha256:" + "1" * 64
    bundle, ctx = _bundle_variant(_wrong_digest)
    expect_unverifiable("digest 错", bundle, ctx)

    # (7) 活动矩阵节点缺失（2025_26.basic）。
    def _drop_node(b):
        del b["data"]["allowances"]["2025_26"]["basic"]
    bundle, ctx = _bundle_variant(_drop_node)
    expect_unverifiable("矩阵节点缺失", bundle, ctx)

    # (8) 活动年度宽减节点缺失（reductions.2025_26）。
    def _drop_reduction(b):
        del b["data"]["reductions"]["2025_26"]
    bundle, ctx = _bundle_variant(_drop_reduction)
    expect_unverifiable("宽减节点缺失", bundle, ctx)

    # (9) current blocked → 拒绝本年（不产确定税额）；next blocked → 仅
    #     抑制 next（current final 保留、partial）。
    bundle = _v2_bundle()
    ctx = _availability(bundle, "2025_26")
    ctx["current"] = {"year": "2025_26", "status": "blocked",
                      "reason_codes": ["GUARD_BLOCKED"]}
    blocked = _run(_input("2025_26", _subjects_single()), availability=ctx)
    assert blocked["status"] == "blocked", blocked["status"]

    ctx = _availability(bundle, "2025_26")
    ctx["next"] = {"year": "2026_27", "status": "blocked",
                   "reason_codes": ["GUARD_BLOCKED"]}
    next_blocked = _run(_input("2025_26", _subjects_single()), availability=ctx)
    assert next_blocked["status"] == "partial", next_blocked["status"]
    assert next_blocked["amounts"]["final_after_reduction"] == "940", (
        f"next 抑制只影响 next（current 940 保留），实际："
        f"{next_blocked['amounts']!r}"
    )
    assert next_blocked["amounts"]["next_provisional"] is None


def test_002_next_year_provisional_no_annual_rebate() -> None:
    """P1 r1 新 Red（复核 §四.4/§六.9）：暂缴不套年度宽减——
    2024/25→2025/26 同收入 240k/9k → next=3,940（宽减前），非 940。

    规格锚点：Annex D D6.3/SDD ADR-6（宽减仅作用最终税，不适用暂缴；
    下一年按该年规则计暂缴）。复核 §四.4（观察 next=940，应 3,940）。
    T3：本年 2024/25 final=3,940−1,500=2,440；next 2025/26：
    NCI=99,000 → 3,940（不计 2025/26 宽减 3,000）。
    """
    current, target = "2024_25", "2025_26"
    result = _run(_input(
        current,
        _subjects_single(),
        years={
            current: _base_year(current),
            target: _base_year(target),
        },
    ))
    amounts = result["amounts"]
    assert amounts["final_after_reduction"] == "2440", amounts
    assert amounts["next_provisional"] == "3940", (
        f"暂缴不套 2025/26 年度宽减（3,940，非 940），实际：{amounts!r}"
    )


def test_002_pa_next_year_contradiction_not_swallowed() -> None:
    """P1 r1 新 Red（复核 §四.2/§六.9）：PA 下一年已证矛盾（重复 claim）
    → E_INPUT_CONTRADICTORY 重抛，不被 next 计算吞掉。

    规格锚点：Annex 005 §8.1（同 (kind,claimant,subject) 重复条目）＋
    复核 §四.2（「非法类型/extra/已证矛盾仍拒绝；避免 catch-all」）。
    """
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    subjects = _subjects_single() + [{"subject_id": "d1",
                                      "birth_date": "2015-06-01"}]
    duplicate_claims = [
        {"claim_id": "c1", "kind": "child", "claimant_id": "p1",
         "subject_id": "d1"},
        {"claim_id": "c2", "kind": "child", "claimant_id": "p1",
         "subject_id": "d1"},
    ]
    try:
        _run_pa({
            "year_of_assessment": current,
            "subjects": subjects,
            "years": {
                current: _base_year(
                    current,
                    subject_facts={"p1": {"ordinarily_resident_hk": [
                        {"from": "2026-06-01", "to": "2026-06-02", "value": True}
                    ]}},
                    assessment={"basis": "hypothetical",
                                "reference_date": "2026-06-01",
                                "candidates": ["no_pa", "pa_self"],
                                "selection": "best"},
                ),
                target: _base_year(
                    target,
                    claims=duplicate_claims,
                    assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                                "selection": "no_pa"},
                ),
            },
            "hk_presence_days": [],
            "assumptions": [],
        })
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"下一年重复 claim 属已证矛盾，必须重抛，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "显式提交的下一年 ledger 矛盾不得被 next 抑制吞掉（复核 §四.2）"
        )


def test_002_next_guard_blocked_still_rejects_contradiction() -> None:
    """P1 r1 新 Red（复核 §四.2/§六.6）：next guard blocked 时，显式下一年
    已证矛盾仍拒绝（guard 短路不得越过输入对账）。

    规格锚点：Annex 005 §2（「任何缺失一律不回退」不豁免已证矛盾）＋
    复核 §四.2（「guard 短路前未检查全部显式下一年输入」）。
    """
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    bundle = _v2_bundle()
    ctx = _availability(bundle, current)
    ctx["next"] = {"year": target, "status": "blocked",
                   "reason_codes": ["GUARD_BLOCKED"]}
    facts = _input(
        current,
        _subjects_single() + [{"subject_id": "d1", "birth_date": "2015-06-01"}],
        years={
            current: _base_year(current),
            target: _base_year(target, claims=[
                {"claim_id": "c1", "kind": "child", "claimant_id": "p1",
                 "subject_id": "d1"},
                {"claim_id": "c2", "kind": "child", "claimant_id": "p1",
                 "subject_id": "d1"},
            ]),
        },
    )
    from app.engines.salaries import calculate_salaries_tax

    try:
        calculate_salaries_tax(facts, bundle, availability=ctx)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"guard blocked 不得吞掉已证矛盾，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "next guard 抑制前仍须核验全部显式下一年输入（复核 §四.2）"
        )


def test_002_supported_current_year_context_next_missing_rules_no_expansion() -> None:
    """当前最后支持 YA：测试侧构造的显式 context 标注紧邻 next missing_rules
    （纯引擎接收 context，不自行查询 guard）；缺 next 规则不拒 current，也不因
    显式事实扩展现行规则/计税支持。"""
    year = "2026_27"
    bundle = _v2_bundle()
    ctx = _availability(bundle, year)
    assert ctx["current"] == {"year": year, "status": "available", "reason_codes": []}
    assert ctx["next"] == {
        "year": "2027_28", "status": "missing_rules", "reason_codes": []
    }, ctx

    result = _run(_input(
        year, _subjects_single(),
        financials={"p1": _fin(nai="480000", mpf="18000")},
    ), availability=ctx)
    assert result["status"] == "partial", result["status"]
    assert result["amounts"]["final_after_reduction"] == "35890", result["amounts"]
    assert result["amounts"]["next_provisional"] is None, result["amounts"]
    assert result["amounts"]["balance"] is None, result["amounts"]
    assert "2027_28" in _missing_blob(result), _missing_blob(result)


def test_002_self_consistent_unsupported_bundle_schema_rejected() -> None:
    """双方一致的旧 rules schema 仍不受支持（005 §2.1，不仅要求相等）。"""
    from app.core.errors import AppError
    from app.engines.salaries import calculate_salaries_tax

    year = "2025_26"
    bundle = _v2_bundle()
    bundle["rules_schema_version"] = "1.0.0"
    ctx = _availability(bundle, year)
    assert ctx["binding"]["rules_schema_version"] == "1.0.0"
    try:
        calculate_salaries_tax(_input(year, _subjects_single()), bundle, availability=ctx)
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"双方一致但不支持的 schema 须拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("双方一致的 1.0.0 rules schema 不得冒充获支持的 2.0.0")


def test_002_self_consistent_unsupported_bundle_schema_rejected_pa() -> None:
    """PA 对照（独立）：合法 PA 事实＋完整显式年＋真 hash 重绑——双方一致的
    旧 rules schema 仍不受支持。"""
    from app.core.errors import AppError
    from app.engines.personal_assessment import compare_personal_assessment

    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target, pa=True)
    bundle = _v2_bundle()
    control = compare_personal_assessment(
        copy.deepcopy(facts), bundle, availability=_availability(bundle, current)
    )
    assert control["status"] in ("complete", "partial"), control

    bundle["rules_schema_version"] = "1.0.0"
    ctx = _availability(bundle, current)
    assert ctx["binding"]["rules_schema_version"] == "1.0.0"
    try:
        compare_personal_assessment(copy.deepcopy(facts), bundle, availability=ctx)
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"PA：双方一致但不支持的 schema 须拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("PA：双方一致的 1.0.0 rules schema 不得冒充获支持的 2.0.0")


def test_002_profile_entry_nested_mutation_does_not_mutate_fixed_anchor() -> None:
    """profile_entry 的深副本隔离嵌套固定 P：返回值的原地嵌套 mutation 不得改变
    发行束同一公开 profile 的内容，也不得改变新调用 profile_entry 的结果；
    finally 只原位恢复真正被改的 `contracts.facts` 叶与 `engines` 列表，并分别
    按原对象（或无则删除）还原模块绑定，不清空/整体替换未改的 assumptions 列表；
    不约束私有锚的具体表示（允许 immutable/bytes；不得以私有 dict 存在性为前提）。"""
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.rules.bundles import allowances as allowance_rules

    released = [
        p
        for p in INITIAL_BUNDLE_CONTENT["frozen_semantics"]["profiles"]
        if p.get("profile_id") == "part5_shared_v2"
    ]
    assert len(released) == 1, released
    public_profile = released[0]
    original_content = copy.deepcopy(public_profile)

    _sentinel = object()
    original_bindings: dict[str, Any] = {
        attr: getattr(allowance_rules, attr, _sentinel)
        for attr in ("_PROFILE_P", "FIXED_PROFILE_P")
    }

    entry = allowance_rules.profile_entry()
    return_contracts = entry["contracts"]
    return_engines = entry["engines"]
    original_facts_leaf = return_contracts["facts"]
    original_engines_items = list(return_engines)

    def _assert_public_content_intact(label: str) -> None:
        assert public_profile == original_content, (
            f"{label}：发行束同一公开 profile 内容被污染（别名未隔离）"
        )
        fresh = allowance_rules.profile_entry()
        assert fresh == original_content, (
            f"{label}：新调用 profile_entry 结果不等于原内容（共享叶污染残留）"
        )

    try:
        # 单一目标 mutation：嵌套叶＋列表（同一对象上原地改）。
        entry["contracts"]["facts"] = "mutated_red_probe"
        entry["engines"].append("mutated_probe")
        _assert_public_content_intact("目标断言")
    finally:
        # 只恢复被改的叶与列表（不做 clear/update 整体替换未改内容）。
        return_contracts["facts"] = original_facts_leaf
        return_engines[:] = original_engines_items
        # 模块绑定按原对象分别还原（原来不存在则删除）。
        for attr, original in original_bindings.items():
            if original is _sentinel:
                if hasattr(allowance_rules, attr):
                    delattr(allowance_rules, attr)
            else:
                setattr(allowance_rules, attr, original)
        _assert_public_content_intact("fail/finally 后")
        for attr, original in original_bindings.items():
            if original is not _sentinel:
                assert getattr(allowance_rules, attr) is original, (
                    f"fail/finally 后：{attr} 未按原对象还原"
                )


def _active_business_pa_facts(year: str = "2025_26") -> dict:
    """活动 PA 业务基线：合法完整 actual no_pa＋独资业务（可判定基线）。"""
    return _input(
        year, _subjects_single(),
        financials={"p1": _fin(
            nai="100000", mpf="0", has_assessable_business_income=True,
            has_income_assessable_under_ordinance=True,
            source_classification={"salary": "confirmed_in_scope",
                                   "business": "confirmed_in_scope"},
            businesses=[{"business_id": "b1", "entity_kind": "sole_proprietorship",
                         "assessable_profit": "100000", "share": "1/1"}],
        )},
        assessment=_pa_no_pa_assessment(),
    )


def _assert_active_business_baseline_computable(facts: dict, bundle: Mapping,
                                                year: str, label: str) -> None:
    """先证合法基线在原束可判定（活动业务未被 inactive 默认遮蔽）。"""
    from app.engines.personal_assessment import compare_personal_assessment

    baseline = compare_personal_assessment(
        copy.deepcopy(facts), bundle, availability=_availability(bundle, year)
    )
    assert baseline["status"] in ("complete", "partial"), (
        f"{label}：合法活动业务基线须可计算，实际 {baseline!r}"
    )
    assert baseline["amounts"]["final_after_reduction"] is not None, (
        f"{label}：基线不得为确定金额缺失（active business 可判定），实际 {baseline!r}"
    )


def test_002_missing_active_pa_profit_reduction_fails_closed() -> None:
    """E1：活动 PA 利得宽减节点缺失须精确 fail-closed；先证活动业务基线可判定，
    再仅缺该规则节点＋真 hash 重绑。"""
    from app.core.errors import AppError
    from app.engines.personal_assessment import compare_personal_assessment

    year = "2025_26"
    facts = _active_business_pa_facts(year)
    bundle = _v2_bundle()
    _assert_active_business_baseline_computable(facts, bundle, year, "missing reduction")
    bundle["data"]["personal_assessment"]["profits_reductions"].pop(year)
    ctx = _availability(bundle, year)
    try:
        compare_personal_assessment(copy.deepcopy(facts), bundle, availability=ctx)
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"活动利得宽减缺节点须映射专用码，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("活动 profits_reductions 缺节点不得静默计算（005 §2.1）")


def test_002_malformed_active_pa_profit_parameter_fails_closed() -> None:
    """E1：畸形活动 PA 利得参数须映射规则错误，而非裸异常/输入错；先证活动业务
    基线可判定，再仅改该参数＋真 hash 重绑。"""
    from app.core.errors import AppError
    from app.engines.personal_assessment import compare_personal_assessment

    year = "2025_26"
    facts = _active_business_pa_facts(year)
    bundle = _v2_bundle()
    _assert_active_business_baseline_computable(facts, bundle, year, "malformed parameter")
    bundle["data"]["personal_assessment"]["profits_two_tier"]["threshold"] = "not-a-money"
    ctx = _availability(bundle, year)
    try:
        compare_personal_assessment(copy.deepcopy(facts), bundle, availability=ctx)
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"畸形活动参数须映射规则错误，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("畸形活动 profits_two_tier 参数不得静默计算（005 §2.1）")


# ---------------------------------------------------------------------------
# C 路新增：下一年活动规则缺/畸 → 只抑 next、保留 current（两引擎）；
# 显式 next 输入错误（候选/扣除/假设）guard available/blocked 均拒。
# ---------------------------------------------------------------------------


def _pa_no_pa_assessment() -> dict:
    """合法完整 actual no_pa 评估（显式模式/历史，不靠缺失默认）。"""
    return {
        "basis": "actual",
        "actual": {
            "salary_joint_elected": False,
            "salary_joint_assessed": False,
            "pa": {"p1": {"mode": "none"}},
            "history": "ordinary",
        },
        "candidates": ["no_pa"],
        "selection": "no_pa",
    }


def _dual_year_input(current: str, target: str, *, pa: bool = False) -> dict:
    """本年＋紧邻下一年显式事实（两引擎共用；每键年窗口各自合法）。

    PA 时两年 assessment 必须**各自独立深拷贝**：整体 deepcopy 不拆分内部共享
    引用，两年共享同一对象时的单变量变异会同时改中两年、遮蔽 next 单变量证据
    （ora-17 CQA-04）。"""
    def _assessment() -> dict | None:
        return copy.deepcopy(_pa_no_pa_assessment()) if pa else None

    current_year = _base_year(current, assessment=_assessment())
    target_year = _base_year(target, assessment=_assessment())
    if pa:
        assert (
            current_year["assessment"] is not target_year["assessment"]
        ), "PA 两年 assessment 不得共享同一对象（next 单变量变异证据）"
    return _input(
        current, _subjects_single(), years={current: current_year, target: target_year}
    )


def _run_salary_with(facts: dict, bundle: Mapping, ctx: Mapping) -> dict:
    from app.engines.salaries import calculate_salaries_tax

    return calculate_salaries_tax(facts, bundle, availability=ctx)


def _run_pa_with(facts: dict, bundle: Mapping, ctx: Mapping) -> dict:
    from app.engines.personal_assessment import compare_personal_assessment

    return compare_personal_assessment(facts, bundle, availability=ctx)


def _next_rule_mutant(mutate) -> tuple:
    import copy

    bundle = copy.deepcopy(_v2_bundle())
    mutate(bundle)
    ctx = _availability(bundle, "2025_26")
    return bundle, ctx


def _assert_next_suppressed_current_kept(result: Mapping, target: str, label: str) -> None:
    amounts = result["amounts"]
    assert result["status"] == "partial", (
        f"{label}：next 抑制后整体状态必须 partial，实际 {result['status']!r}"
    )
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), sorted(amounts)
    assert amounts["final_after_reduction"] == "940", (
        f"{label}：next 规则缺/畸不得污染本年 final（940 保留），实际：{amounts!r}"
    )
    assert amounts["next_provisional"] is None, (
        f"{label}：next 规则不可核验须仅抑制 next（next=null），实际：{amounts!r}"
    )
    assert amounts["balance"] is None, (
        f"{label}：partial 一律 balance=null，实际：{amounts!r}"
    )
    assert "2026_27" in _missing_blob(result) or "next" in _missing_blob(result).lower(), (
        f"{label}：missing_components 须标注 next 缺失，实际：{_missing_blob(result)!r}"
    )


def _assert_next_rule_isolated(kind: str, mutate, label: str) -> None:
    """对照（去 mutation）→ next 规则缺/畸 → 只抑 next、本年 940 保留。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target, pa=(kind == "pa"))
    runner = _run_salary_with if kind == "salary" else _run_pa_with

    bundle = _v2_bundle()
    ctx = _availability(bundle, current)
    control = runner(facts, bundle, ctx)
    assert control["amounts"]["final_after_reduction"] == "940", (
        f"{label}：对照当前年 final 须 940（PAM39 Q1），实际：{control['amounts']!r}"
    )
    assert control["amounts"]["next_provisional"] == "3160", (
        f"{label}：对照 2026/27 按该年规则 3,160（T3；宽减 0），"
        f"实际：{control['amounts']!r}"
    )

    bundle_bad, ctx_bad = _next_rule_mutant(mutate)
    try:
        result = runner(facts, bundle_bad, ctx_bad)
    except AppError as exc:
        raise AssertionError(
            f"{label}：next 规则缺/畸须映射为 next 抑制（current 保留），"
            f"实际抛出 {exc.code!r}/{exc.field!r}（E7 年度隔离）"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 裸异常显式暴露为 Red
        raise AssertionError(
            f"{label}：next 规则缺/畸不得裸异常 {type(exc).__name__}: {exc}"
            "（须 E_RULES_STATE_UNVERIFIABLE 年度隔离，005 §2.1）"
        ) from exc
    _assert_next_suppressed_current_kept(result, target, label)


def test_002_next_active_rule_missing_preserves_current_final_salary() -> None:
    """salary：next 活动规则缺键（2026_27.basic）＋显式完整 next 事实＋真 hash
    重绑 → 只抑 next（null）、本年 940 保留。"""
    _assert_next_rule_isolated(
        "salary",
        lambda b: b["data"]["allowances"]["2026_27"].pop("basic"),
        "salary/missing",
    )


def test_002_next_active_rule_missing_preserves_current_final_pa() -> None:
    """PA：next 活动规则缺键（2026_27.basic）＋显式完整 next 事实＋真 hash
    重绑 → 只抑 next（null）、本年 940 保留。"""
    _assert_next_rule_isolated(
        "pa",
        lambda b: b["data"]["allowances"]["2026_27"].pop("basic"),
        "PA/missing",
    )


def test_002_next_active_rule_malformed_preserves_current_final_salary() -> None:
    """salary：next 活动规则畸形（2026_27.basic.amount 非 Money）＋真 hash 重绑
    → 只抑 next，本年 940 保留，不得裸 Decimal 异常。"""
    _assert_next_rule_isolated(
        "salary",
        lambda b: b["data"]["allowances"]["2026_27"]["basic"].__setitem__(
            "amount", "not-a-money"
        ),
        "salary/malformed",
    )


def test_002_next_active_rule_malformed_preserves_current_final_pa() -> None:
    """PA：next 活动规则畸形（2026_27.basic.amount 非 Money）＋真 hash 重绑
    → 只抑 next，本年 940 保留，不得裸 Decimal 异常。"""
    _assert_next_rule_isolated(
        "pa",
        lambda b: b["data"]["allowances"]["2026_27"]["basic"].__setitem__(
            "amount", "not-a-money"
        ),
        "PA/malformed",
    )


@pytest.mark.parametrize("kind", ["salary", "pa"], ids=["salary", "pa"])
@pytest.mark.parametrize("guard", ["available", "blocked"], ids=["available", "blocked"])
def test_002_next_unsupported_deduction_rejected_regardless_of_guard(
    kind: str, guard: str
) -> None:
    """显式 next 不支持正数扣除：两税×guard available／blocked 均须
    E_DEDUCTION_UNSUPPORTED 拒绝（先验全部显式输入，不被 next 抑制吞掉）。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    target_fin = _fin()
    target_fin["deduction_items"] = [{"item_type": "other", "amount": "1"}]
    legal_assessment = _pa_no_pa_assessment() if kind == "pa" else None
    facts = _input(
        current,
        _subjects_single(),
        years={
            current: _base_year(current, assessment=legal_assessment),
            target: _base_year(target, financials={"p1": target_fin},
                               assessment=legal_assessment),
        },
    )
    bundle = _v2_bundle()
    ctx = _availability(bundle, current)
    if guard == "blocked":
        ctx["next"] = {"year": target, "status": "blocked",
                       "reason_codes": ["GUARD_BLOCKED"]}
    expected_field = f"years.{target}.financials.p1.deduction_items[0]"
    runner = _run_salary_with if kind == "salary" else _run_pa_with
    try:
        runner(facts, bundle, ctx)
    except AppError as exc:
        assert (exc.code, exc.field) == ("E_DEDUCTION_UNSUPPORTED", expected_field), (
            f"{kind}/{guard}：不支持正数扣除须精确 E_DEDUCTION_UNSUPPORTED/"
            f"{expected_field}，实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"{kind}/{guard}：显式 next 不支持正数扣除不得被 guard/next 抑制吞掉"
            "（E7：输入先验先于年度隔离）"
        )


@pytest.mark.parametrize("kind", ["salary", "pa"], ids=["salary", "pa"])
@pytest.mark.parametrize("guard", ["available", "blocked"], ids=["available", "blocked"])
def test_002_next_invalid_candidate_rejected_regardless_of_guard(
    kind: str, guard: str
) -> None:
    """显式 next 非法候选 id（两税×guard available／blocked）：从**已证完整合法
    next 控制**复制（保留 actual/模式/历史），先证控制可判（next=3,160），再唯一
    把 candidates 改为不合法 CandidateID（selection 保持合法 token `best` 以分离
    缺陷）→ E_INPUT_EXTRA＋candidates 路径；guard 抑制不得吞掉。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target, pa=(kind == "pa"))
    facts["years"][target]["assessment"]["selection"] = "best"
    runner = _run_salary_with if kind == "salary" else _run_pa_with
    bundle = _v2_bundle()

    control = runner(copy.deepcopy(facts), bundle, _availability(bundle, current))
    assert control["amounts"]["next_provisional"] == "3160", (
        f"{kind}：完整合法 next 控制须可判（T3：3,160），实际 {control['amounts']!r}"
    )

    facts["years"][target]["assessment"]["candidates"] = ["salary_telepathy"]
    ctx = _availability(bundle, current)
    if guard == "blocked":
        ctx["next"] = {"year": target, "status": "blocked",
                       "reason_codes": ["GUARD_BLOCKED"]}
    expected = ("E_INPUT_EXTRA", f"years.{target}.assessment.candidates")
    try:
        result = runner(facts, bundle, ctx)
    except AppError as exc:
        assert (exc.code, exc.field) == expected, (
            f"{kind}/{guard}：非法 next 候选须精确 {expected!r}，"
            f"实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"{kind}/{guard}：非法 next 候选不得被 guard/next 抑制吞掉"
            f"（B6：全部显式 Year 候选先验），实际未拒绝：{result!r}"
        )


@pytest.mark.parametrize("kind", ["salary", "pa"], ids=["salary", "pa"])
@pytest.mark.parametrize("guard", ["available", "blocked"], ids=["available", "blocked"])
def test_002_next_invalid_selection_rejected_regardless_of_guard(
    kind: str, guard: str
) -> None:
    """显式 next 合法候选＋非法 selection（两税×guard available／blocked）：从
    **已证完整合法 next 控制**复制（保留 actual/模式/历史），先证控制可判
    （next=3,160），再唯一把 selection 改为不合法 CandidateID `salary_telepathy`；
    期望 E_INPUT_TYPE＋selection 路径——依据 Approved005 §4.10:233 枚举域
    `"best"|CandidateID`（不以当前 API 返回值为规范；不泛化「合法 CandidateID
    未列于 candidates」的关系案例）。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target, pa=(kind == "pa"))
    runner = _run_salary_with if kind == "salary" else _run_pa_with
    bundle = _v2_bundle()

    control = runner(copy.deepcopy(facts), bundle, _availability(bundle, current))
    assert control["amounts"]["next_provisional"] == "3160", (
        f"{kind}：完整合法 next 控制须可判（T3：3,160），实际 {control['amounts']!r}"
    )

    facts["years"][target]["assessment"]["selection"] = "salary_telepathy"
    ctx = _availability(bundle, current)
    if guard == "blocked":
        ctx["next"] = {"year": target, "status": "blocked",
                       "reason_codes": ["GUARD_BLOCKED"]}
    expected = ("E_INPUT_TYPE", f"years.{target}.assessment.selection")
    try:
        result = runner(facts, bundle, ctx)
    except AppError as exc:
        assert (exc.code, exc.field) == expected, (
            f"{kind}/{guard}：非法 next selection 须精确 {expected!r}，"
            f"实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"{kind}/{guard}：非法 next selection 不得被 guard/next 抑制吞掉"
            f"（B6：全部显式 Year 候选先验），实际未拒绝：{result!r}"
        )


def test_002_current_2026_27_with_explicit_2027_28_facts_stays_unsupported() -> None:
    """current 2026/27 仍查询紧邻 next 2027/28：即使显式提交完整 2027/28 事实，
    也不得扩展现行规则/计税支持（next=null＋missing，本年 35,890 保留）。"""
    current, target = "2026_27", "2027_28"
    result = _run(_input(
        current,
        _subjects_single(),
        years={
            current: _base_year(current, financials={"p1": _fin(nai="480000", mpf="18000")}),
            target: _base_year(target, financials={"p1": _fin(nai="480000", mpf="18000")}),
        },
    ))
    amounts = result["amounts"]
    assert result["status"] == "partial", result["status"]
    assert amounts["final_after_reduction"] == "35890", (
        f"2026/27 PAM39 Q2 保留，实际：{amounts!r}"
    )
    assert amounts["next_provisional"] is None, (
        f"2027/28 未支持：显式事实不得扩展计税支持（next=null），实际：{amounts!r}"
    )
    assert "2027_28" in _missing_blob(result), _missing_blob(result)


def test_002_projection_same_income_target_missing() -> None:
    """§11 投射（same_income，目标缺失）：目标年缺 NAI＋源已知 → next＝3,160，
    六金额键齐备且源不得按 0 制造。"""
    current, target = "2025_26", "2026_27"
    same_income = [{"assumption_id": "a1", "kind": "same_income",
                    "person_id": "p1", "source_year": current, "target_year": target}]
    target_missing = _fin()
    del target_missing["employment_income_nai"]
    result = _run(_input(
        current, _subjects_single(),
        years={current: _base_year(current),
               target: _base_year(target, financials={"p1": target_missing})},
        assumptions=same_income,
    ))
    assert set(result["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(result["amounts"])
    assert result["amounts"]["next_provisional"] == "3160", (
        f"源 NAI 已知（240k）→ 投射后 next=3,160（T3 手算），"
        f"实际：{result['amounts']!r}"
    )


def test_002_projection_same_income_redundant_equal() -> None:
    """§11 投射（same_income，冗余等额）：显式目标金额＝假设同额 → 接受且
    next 仍 3,160（显式事实为主；不重复计算）。"""
    current, target = "2025_26", "2026_27"
    same_income = [{"assumption_id": "a1", "kind": "same_income",
                    "person_id": "p1", "source_year": current, "target_year": target}]
    result = _run(_input(
        current, _subjects_single(),
        years={current: _base_year(current), target: _base_year(target)},
        assumptions=same_income,
    ))
    assert set(result["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(result["amounts"])
    assert result["amounts"]["next_provisional"] == "3160", (
        f"显式等额＋假设＝冗余接受（005 §11），next 仍 3,160，"
        f"实际：{result['amounts']!r}"
    )


def _same_mpf(current: str, target: str) -> list[dict]:
    return [{"assumption_id": "a1", "kind": "same_mpf",
             "person_id": "p1", "source_year": current, "target_year": target}]


def test_002_projection_same_mpf_target_missing() -> None:
    """§11 投射（same_mpf，目标缺失）：目标缺 MPF＋源已知 9,000 →
    NCI=240,000−9,000−145,000=86,000 → next=3,160。"""
    current, target = "2025_26", "2026_27"
    target_missing = _fin()
    del target_missing["mpf_mandatory_contributions"]
    result = _run(_input(
        current, _subjects_single(),
        years={current: _base_year(current),
               target: _base_year(target, financials={"p1": target_missing})},
        assumptions=_same_mpf(current, target),
    ))
    assert set(result["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(result["amounts"])
    assert result["amounts"]["next_provisional"] == "3160", (
        f"same_mpf 目标缺失：源 MPF 9,000 投射 → next=3,160，"
        f"实际：{result['amounts']!r}"
    )


def test_002_projection_same_mpf_redundant_equal() -> None:
    """§11 投射（same_mpf，冗余等额）：目标显式 MPF 9,000＝源 → 接受且
    next 仍 3,160。"""
    current, target = "2025_26", "2026_27"
    result = _run(_input(
        current, _subjects_single(),
        years={current: _base_year(current), target: _base_year(target)},
        assumptions=_same_mpf(current, target),
    ))
    assert set(result["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(result["amounts"])
    assert result["amounts"]["next_provisional"] == "3160", (
        f"same_mpf 冗余等额接受且 next 3,160，实际：{result['amounts']!r}"
    )


@pytest.mark.parametrize("guard", ["available", "blocked"], ids=["available", "blocked"])
def test_002_projection_same_mpf_conflict_rejected(guard: str) -> None:
    """§11 投射（same_mpf，冲突）：目标显式 MPF 5,000 ≠ 源 9,000 →
    E_INPUT_CONTRADICTORY＋**完整目标 field**（CF1 §13：
    `years.<target>.financials.<pid>.mpf_mandatory_contributions`；不以内部键/
    当前实现字符串为规范）；guard available／blocked 独立参数化——guard 抑制
    不得吞掉已证投影矛盾。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    bundle = _v2_bundle()
    ctx = _availability(bundle, current)
    if guard == "blocked":
        ctx["next"] = {"year": target, "status": "blocked",
                       "reason_codes": ["GUARD_BLOCKED"]}
    facts = _input(
        current, _subjects_single(),
        years={current: _base_year(current),
               target: _base_year(target, financials={"p1": _fin(mpf="5000")})},
        assumptions=_same_mpf(current, target),
    )
    try:
        _run(facts, availability=ctx)
    except AppError as exc:
        expected = (
            "E_INPUT_CONTRADICTORY",
            f"years.{target}.financials.p1.mpf_mandatory_contributions",
        )
        assert (exc.code, exc.field) == expected, (
            f"same_mpf 异额必须精确 {expected!r}（CF1 完整目标 field；"
            f"guard={guard}），实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"guard={guard}：显式目标 MPF 与 same_mpf 假设异额 → "
            "E_INPUT_CONTRADICTORY（005 §11；输入先验先于年度隔离）"
        )


_MISSING_PROJECTION_KEY = {
    "same_income": "employment_income_nai",
    "same_mpf": "mpf_mandatory_contributions",
}


@pytest.mark.parametrize(
    "assumption_kind", ["same_income", "same_mpf"], ids=["same_income", "same_mpf"]
)
def test_002_projection_source_and_target_missing_stays_unresolved(
    assumption_kind: str,
) -> None:
    """§11/§6：源与目标金额均缺失（same_income/same_mpf 独立参数化）——假设无
    可投射值，不得按 0 制造；缺失未决 → needs_input、六金额键一律 null。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    key = _MISSING_PROJECTION_KEY[assumption_kind]
    source = _base_year(current)
    del source["financials"]["p1"][key]
    target_year = _base_year(target)
    del target_year["financials"]["p1"][key]
    facts = _input(
        current, _subjects_single(),
        years={current: source, target: target_year},
        assumptions=[{"assumption_id": "a1", "kind": assumption_kind,
                      "person_id": "p1", "source_year": current,
                      "target_year": target}],
    )
    try:
        result = _run(facts)
    except AppError as exc:
        raise AssertionError(
            f"{assumption_kind}：源/目标均缺失＝未提供（≠0）：不得制造虚假矛盾，"
            f"实际抛出 {exc.code!r}/{exc.field!r}"
        ) from exc
    assert result["status"] == "needs_input", (
        f"{assumption_kind}：源与目标均缺失 → needs_input（不得按 0 继续），"
        f"实际：{result['status']!r}"
    )
    assert set(result["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(result["amounts"])
    assert all(value is None for value in result["amounts"].values()), (
        f"{assumption_kind}：缺失未决时六金额键一律 null（不得用假设制造 0），"
        f"实际：{result['amounts']!r}"
    )


def test_002_projection_source_missing_not_projected_as_zero() -> None:
    """§11/§6：源年金额缺失≠0——假设不得把缺失源制造为零、再与显式目标
    虚假矛盾；缺失未决保持 needs_input、六金额键 null。"""
    from app.core.errors import AppError

    current, target = "2025_26", "2026_27"
    source = _base_year(current)
    del source["financials"]["p1"]["employment_income_nai"]
    facts = _input(
        current, _subjects_single(),
        years={
            current: source,
            target: _base_year(target, financials={"p1": _fin()}),
        },
        assumptions=[{"assumption_id": "a1", "kind": "same_income",
                      "person_id": "p1", "source_year": current,
                      "target_year": target}],
    )
    try:
        result = _run(facts)
    except AppError as exc:
        raise AssertionError(
            f"源 NAI 缺失＝未提供（≠0）：假设不得把缺失源按零制造与显式目标的"
            f"虚假矛盾，实际抛出 {exc.code!r}/{exc.field!r}"
        ) from exc
    assert result["status"] == "needs_input", (
        f"源 NAI 缺失＝未提供（≠0）→ needs_input，不得按零继续，"
        f"实际：{result['status']!r}，questions={result.get('questions')!r}"
    )
    assert set(result["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(result["amounts"])
    assert all(value is None for value in result["amounts"].values()), (
        f"缺失未决时六金额键一律 null（不得用假设制造 0），"
        f"实际：{result['amounts']!r}"
    )


def test_002_next_does_not_inherit_spouse_exit() -> None:
    """§3/§11：spouse 逐年退出不继承——本年 married＋配偶 AI=false（S2 分支 A，
    final 0）；下一年无该 participant／婚姻事实：按单身基本额重解析（next 3,160，
    若错误继承婚姻/role 会得已婚路径的不同值）。"""
    current, target = "2025_26", "2026_27"
    current_year = _base_year(
        current,
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        household={
            "marriage": [{"from": "2025-04-01", "to": "2026-04-01",
                          "value": "married", "spouse_id": "p2"}],
            "living_apart": [{"from": "2025-04-01", "to": "2026-04-01",
                              "value": False}],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        financials={
            "p1": _fin(nai="240000", mpf="9000"),
            "p2": _fin(nai="0", mpf="0", ai=False),
        },
        assessment={
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "history": "ordinary",
                "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
            },
        },
    )
    result = _run(_input(
        current,
        _subjects_single() + [{"subject_id": "p2", "birth_date": "1992-02-02"}],
        years={current: current_year, target: _base_year(target)},
    ))
    amounts = result["amounts"]
    # 本年：S2 分支 A：married 264,000 覆盖两人基础额；NCI<0 → 税 0，宽减 0。
    assert amounts["final_after_reduction"] == "0", (
        f"本年 branch A（配偶 AI=false）→ final 0（T3 手算），"
        f"实际：{amounts!r}，status={result['status']!r}，"
        f"questions={result.get('questions')!r}"
    )
    # 下一年：配偶不继承；单身 basic 145,000 → NCI 86,000 → 3,160（宽减 0）。
    assert amounts["next_provisional"] == "3160", (
        f"spouse 退出不得被继承（下一年按单身事实重解析 next=3,160），"
        f"实际：{amounts!r}，status={result['status']!r}"
    )


# ---------------------------------------------------------------------------
# F1 Wave A（2026-10-17）：next 完整矩阵准入＋纯入口/runtime 配对
# ---------------------------------------------------------------------------


def _next_matrix_mutant(mutate) -> tuple[dict, dict]:
    """真实双年束 mutation＋按变更后内容重算真 bundle hash 的 availability。"""
    bundle = copy.deepcopy(_v2_bundle())
    mutate(bundle)
    ctx = _availability(bundle, "2025_26")
    # 2026/27 仍在矩阵中 → _availability 声明 next=available；纯入口必须自行
    # 核验该声明年度的完整活动规则（不得等引擎消费到缺失节点才失败）。
    assert ctx["next"]["status"] == "available"
    return bundle, ctx


@pytest.mark.parametrize("kind", ["salary", "pa"], ids=["salary", "pa"])
def test_002_next_full_matrix_parent_high_removed_suppresses_next(kind: str) -> None:
    """F1/R1（005 §15.2）：完整双年仅删除 2026/27 parent_high（next 事实齐全、
    真 hash 重绑）→ 两引擎 current=940 保留、next=null；完整对照 next=3,160。
    该节点未被本年/下一年单一个人消费，仍须由完整矩阵准入拦下。"""
    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target, pa=(kind == "pa"))
    runner = _run_salary_with if kind == "salary" else _run_pa_with

    control_bundle = _v2_bundle()
    control_ctx = _availability(control_bundle, current)
    control = runner(facts, control_bundle, control_ctx)
    assert control["amounts"]["final_after_reduction"] == "940", (
        f"{kind}：对照当前年 final 须 940，实际：{control['amounts']!r}"
    )
    assert control["amounts"]["next_provisional"] == "3160", (
        f"{kind}：对照 2026/27 next 须 3,160，实际：{control['amounts']!r}"
    )

    bundle, ctx = _next_matrix_mutant(
        lambda b: b["data"]["allowances"][target].pop("parent_high")
    )
    assert ctx["binding"]["bundle_hash"] != control_ctx["binding"]["bundle_hash"], (
        "规则内容变更后须重算真实 bundle hash（C2.8 六字段 canonical）"
    )
    result = runner(facts, bundle, ctx)
    amounts = result["amounts"]
    assert result["status"] == "partial", (
        f"{kind}：next 规则不可核验须仅抑制 next（整体 partial），"
        f"实际：{result['status']!r}"
    )
    assert amounts["final_after_reduction"] == "940", (
        f"{kind}：next 矩阵缺 key 不得污染本年 final（940），实际：{amounts!r}"
    )
    assert amounts["next_provisional"] is None, (
        f"{kind}：2026/27 完整矩阵缺 parent_high → next=null，实际：{amounts!r}"
    )
    assert amounts["balance"] is None, (
        f"{kind}：partial 一律 balance=null，实际：{amounts!r}"
    )


def test_002_next_rule_pure_entry_matches_runtime_salary() -> None:
    """F1/R1：同一 next 矩阵缺失束，纯入口 check_availability 与 runtime.
    admit_runtime 均把 next 局部化 missing_rules＋RULES_UNVERIFIABLE（保留
    current）；两种 context 交引擎均 current=940、next=null。"""
    from app.engines.allowances import check_availability
    from app.rules import runtime

    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target)
    bundle, ctx = _next_matrix_mutant(
        lambda b: b["data"]["allowances"][target].pop("parent_high")
    )

    pure_ctx = check_availability(ctx, bundle, current, tax_type="salaries_tax")
    assert pure_ctx["current"]["status"] == "available", (
        f"current 不得被 next 规则缺口污染，实际：{pure_ctx!r}"
    )
    assert pure_ctx["next"]["status"] == "missing_rules", (
        "纯入口须核验 next 声明 available 的 2026/27 完整矩阵；"
        f"实际 next={pure_ctx['next']!r}"
    )
    assert "RULES_UNVERIFIABLE" in pure_ctx["next"]["reason_codes"], (
        f"next 局部化须带 RULES_UNVERIFIABLE，实际：{pure_ctx['next']!r}"
    )

    guard_read = {
        "periods": [
            {"year": current, "status": "available", "reason_codes": []},
            {"year": target, "status": "available", "reason_codes": []},
        ],
        "warnings": [],
    }
    admission = runtime.admit_runtime(
        ctx["binding"],
        bundle,
        tax_type="salaries_tax",
        facts=facts,
        guard_read=guard_read,
    )
    runtime_ctx = admission["availability_context"]
    assert runtime_ctx["next"] == pure_ctx["next"], (
        "纯入口与 runtime 的 next 局部化语义须一致（runtime.py:235–254）；"
        f"pure={pure_ctx['next']!r} runtime={runtime_ctx['next']!r}"
    )

    pure_result = _run_salary_with(facts, bundle, pure_ctx)
    runtime_result = _run_salary_with(facts, bundle, runtime_ctx)
    for label, result in (("pure", pure_result), ("runtime", runtime_result)):
        amounts = result["amounts"]
        assert result["status"] == "partial", (
            f"{label}：next 抑制须 partial，实际：{result['status']!r}"
        )
        assert amounts["final_after_reduction"] == "940", (
            f"{label}：current=940 须保留，实际：{amounts!r}"
        )
        assert amounts["next_provisional"] is None, (
            f"{label}：next 须 null，实际：{amounts!r}"
        )


def test_002_current_money_domain_rejected_by_pure_entry_and_runtime() -> None:
    """F1/R1：当前年矩阵 amount="-1" 在纯引擎入口与 runtime admit_runtime
    均以同一 E_RULES_STATE_UNVERIFIABLE＋规则字段拒算（不得静默算出税额）。"""
    from app.core.errors import AppError
    from app.rules import runtime

    current, target = "2025_26", "2026_27"
    facts = _dual_year_input(current, target)
    bundle = copy.deepcopy(_v2_bundle())
    bundle["data"]["allowances"][current]["basic"]["amount"] = "-1"
    ctx = _availability(bundle, current)
    field = f"data.allowances.{current}.basic.amount"

    try:
        _run_salary_with(facts, bundle, ctx)
    except AppError as exc:
        assert (exc.code, exc.field) == ("E_RULES_STATE_UNVERIFIABLE", field), (
            f"纯入口须以规则字段拒算，实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError("纯入口不得接受负数规则金额并算出税额")

    guard_read = {
        "periods": [
            {"year": current, "status": "available", "reason_codes": []},
            {"year": target, "status": "available", "reason_codes": []},
        ],
        "warnings": [],
    }
    try:
        runtime.admit_runtime(
            ctx["binding"],
            bundle,
            tax_type="salaries_tax",
            facts=facts,
            guard_read=guard_read,
        )
    except AppError as exc:
        assert (exc.code, exc.field) == ("E_RULES_STATE_UNVERIFIABLE", field), (
            f"runtime 须与纯入口同码同字段，实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError("runtime 不得接受负数规则金额（须 fail-closed）")
