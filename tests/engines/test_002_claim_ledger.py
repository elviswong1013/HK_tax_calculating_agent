# -*- coding: utf-8 -*-
"""REQ-6 claim ledger：键重复、类互斥、合法叠加与跨方案复用（002 P1 Red）。

REQ: REQ-6（S002 §8/§9.4；PRD AC-6）。本文件为 SDD §9.4 REQ-6 命名节点，共 9 个：
  - test_002_duplicate_key_rejected
  - test_002_parent_grandparent_mutex
  - test_002_child_sibling_mutex
  - test_002_child_plus_disabled_stack_legal
  - test_002_parent_plus_cohab_extra_stack_legal
  - test_002_26d_granted_vs_parent_allowance_mutex
  - test_002_enbloc_nomination_reconciles
  - test_002_pa_no_pa_reuse_not_duplicate
  - test_002_special_situation_manual_handoff_locator

规格锚点:
  - S002 §2 ADR-2（每场景独立 claim ledger；键 (kind,claimant,subject)＋类互斥）；
    §3.2/§5.2；§8 REQ-6；
  - Annex 005 §8（§8.1 重复与互斥表；§8.2 决策顺序）；§4.4（child_nomination；
    special_situation）；§4.9（claims）；§6.2（父母额×s.26D 互斥：两者都可获计入
    时要求用户修正互斥意图、服务器不替用户选；后者不合资格不剥夺前者）；
    §9.1 S3／§9.3（跨方案复用不算重复；no_pa 不是隐式候选）；§10（PA 恒 partial）；
    §14（decision grant 含 "mutex"、entitlement 含 "manual"）；§13；§2.1。

期望值来源（禁从生产代码反推）:
  - 免税额：Annex 005 §15.2（转引附表 4，T1）。
  - 税率带：附表 1/2（Annex D D7；T1 台账 1.17）。
  - 无额外免税额基线 final 940：PAM39 Q1（T2 台账 1.13）。
  - 其余组合数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None) 与
    compare_personal_assessment(facts, bundle, *, availability=None)（005 §2.1）。
  - ledger 语义：同 (kind,claimant,subject) 重复、同 subject 跨 claimant 同类
    （s.31(3) 整组提名例外）、parent+grandparent、child+sibling → AppError
    E_INPUT_CONTRADICTORY；child+disabled_dependant、
    parent/grandparent+cohabitation_additional 合法叠加；父母额×s.26D 同 scenario
    互斥且两者皆可获计入时 needs_input（不替用户选）；PA/no-PA 各自 ledger
    复用不算重复；special_situation=true/unknown → decision manual＋定位。
  - 本文件对 app.* 延迟导入（测试体内）。
"""

from __future__ import annotations

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
            "personal_assessment": {
                "progressive_bands": _PROGRESSIVE,
                "standard_rate_bands": _STANDARD,
                # B lane：活动 PA 参数（物业/利得比较；与已批准 T1 数值一致）。
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
    f = {
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
    manual_issues: list | None = None,
    provisional_paid: str = "0",
) -> dict:
    w_from, w_to = _window(year)
    return {
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
        "manual_issues": list(manual_issues or []),
        "provisional_paid": provisional_paid,
    }


def _salary_input(year: str, subjects: list, **year_kw: Any) -> dict:
    return {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": {year: _base_year(year, **year_kw)},
        "hk_presence_days": [],
        "assumptions": [],
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
    cash: str | None = None,
    cohabit: list | None = None,
) -> dict:
    r: dict[str, Any] = {
        "relation_id": rid,
        "actor_id": actor,
        "subject_id": subject,
        "kinship": {"kind": kind, "basis": basis, "during": _tf(year, True)},
    }
    if maintained is not None:
        r["maintained"] = maintained
    if cash is not None:
        r["cash_maintenance"] = cash
    if cohabit is not None:
        r["cohabit_non_full_consideration"] = cohabit
    return r


def _claim(cid: str, kind: str, claimant: str = "p1", subject: str = "d1",
           special: Any = None) -> dict:
    c = {"claim_id": cid, "kind": kind, "claimant_id": claimant, "subject_id": subject}
    if special is not None:
        c["special_situation"] = special
    return c


def _subjects_with(dependants: list[tuple[str, str]]) -> list[dict]:
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    subjects.extend({"subject_id": sid, "birth_date": birth} for sid, birth in dependants)
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


def _run(v2_input: dict) -> dict:
    from app.engines.salaries import calculate_salaries_tax  # 延迟导入：Red=缺 v2 契约

    bundle = _v2_bundle()
    return calculate_salaries_tax(
        v2_input, bundle, availability=_availability(bundle, v2_input["year_of_assessment"])
    )


def _run_pa(v2_input: dict) -> dict:
    from app.engines.personal_assessment import compare_personal_assessment

    bundle = _v2_bundle()
    return compare_personal_assessment(
        v2_input, bundle, availability=_availability(bundle, v2_input["year_of_assessment"])
    )


def _expect_contradictory(v2_input: dict, label: str) -> None:
    """同 (kind,claimant,subject)／类互斥冲突 → AppError E_INPUT_CONTRADICTORY。"""
    from app.core.errors import AppError  # 延迟导入：Red=缺 v2 契约

    try:
        _run(v2_input)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"{label}应以 E_INPUT_CONTRADICTORY 拒算（005 §8.1），实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            f"{label}属 ledger 冲突（005 §8.1），必须拒算而非静默采纳"
        )


# ---------------------------------------------------------------------------
# 断言辅助（005 §13/§14）
# ---------------------------------------------------------------------------


def _assert_null_amounts(result: Mapping) -> None:
    amounts = result["amounts"]
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), (
        f"amounts 必须恰含六键（C3.4），实际：{sorted(amounts)!r}"
    )
    assert all(v is None for v in amounts.values()), (
        f"不得输出任何金额（未知资格不填零），实际：{amounts!r}"
    )


def _decisions(result: Mapping) -> list[dict]:
    steps = result.get("steps") or []
    return [
        s["detail"]
        for s in steps
        if isinstance(s, dict) and s.get("kind") == "decision" and isinstance(s.get("detail"), dict)
    ]


def _decision(result: Mapping, **match: Any) -> dict | None:
    for d in _decisions(result):
        if all(d.get(k) == v for k, v in match.items()):
            return d
    return None


def _unit_base(result: Mapping, unit_id: str, name: str) -> str | None:
    for s in result.get("steps") or []:
        if isinstance(s, dict) and s.get("kind") == "assessment":
            detail = s.get("detail") or {}
            if detail.get("unit_id") == unit_id:
                for b in detail.get("bases") or []:
                    if b.get("name") == name:
                        return b.get("value")
    return None


def _property_unit_base(result: Mapping, property_id: str, name: str) -> str | None:
    """按物业对象取唯一相关 assessment 的 base——不绑定 unit_id 具体形状
    （red-b-review 修正 #7：property:h1:p1 未冻结；E6 原 ID 形状可接受）。"""
    matches: list[dict] = []
    for s in result.get("steps") or []:
        if not isinstance(s, dict) or s.get("kind") != "assessment":
            continue
        detail = s.get("detail") or {}
        if property_id in str(detail.get("unit_id") or ""):
            matches.append(detail)
    assert len(matches) == 1, (
        f"须恰有一个与 {property_id!r} 相关的物业 assessment，实际：{matches!r}"
    )
    for b in matches[0].get("bases") or []:
        if b.get("name") == name:
            return b.get("value")
    return None


def _structured_questions(result: Mapping) -> list:
    questions = result.get("questions") or []
    assert isinstance(questions, list) and questions, (
        "needs_input 必须携带非空 questions[]（C3.3）"
    )
    for q in questions:
        assert isinstance(q, Mapping), (
            "questions[] 项必须为结构化定位项 "
            "{field,year,claim_id?,subject_id?,question_zh}"
            f"（Annex 005 §13），实际：{q!r}"
        )
        assert isinstance(q.get("question_zh"), str) and q["question_zh"], (
            f"question_zh 必须为非空中文串（005 §13），实际：{q!r}"
        )
    return questions


def _locates(questions: list, needles, *, year: str,
             claim_id: str | None = None, subject_id: str | None = None) -> bool:
    """同一问题项上的分层结构化核验（w3b 复审 #6 返修）：

    - 全部 needle 须位于该问题项的 field（子串；不比对 question_zh/其它键）；
    - year 须存在且精确等于期望键年（不做缺省/错配放行）；
    - 如给出 claim_id/subject_id，须在同一问题项上绑定相等（适用元数据）。
    保留免 `[0]`/h1/b1/item_type 字面量原则；字段族择一用 `_locates_any`。
    """
    if isinstance(needles, str):
        needles = [needles]
    for q in questions:
        if not isinstance(q, Mapping):
            continue
        field = str(q.get("field") or "")
        if not all(n in field for n in needles):
            continue
        if q.get("year") != year:
            continue
        if claim_id is not None and q.get("claim_id") != claim_id:
            continue
        if subject_id is not None and q.get("subject_id") != subject_id:
            continue
        return True
    return False


def _locates_any(questions: list, alternatives, *, year: str,
                 claim_id: str | None = None, subject_id: str | None = None) -> bool:
    """字段族择一（如 living_apart/marriage），仍须满足同一问题项的
    year 精确匹配与适用 claim/subject 元数据绑定。"""
    for needles in alternatives:
        if _locates(questions, needles, year=year,
                    claim_id=claim_id, subject_id=subject_id):
            return True
    return False


def _locator_ok(result: Mapping, needles, *, year: str,
                claim_id: str | None = None, subject_id: str | None = None) -> bool:
    """needs_input/blocked 的定位证据：结构化问题项（field+year+元数据绑定），
    或既有非空 blocked_reason（005 §10：blocked 可无 questions）。"""
    if _locates(result.get("questions") or [], needles, year=year,
                claim_id=claim_id, subject_id=subject_id):
        return True
    return bool(result.get("status") == "blocked" and result.get("blocked_reason"))


def _full_care_payment(residential: Any = True) -> dict:
    return {
        "payment_id": "e1",
        "payer_id": "p1",
        "subject_id": "d1",
        "paid_on": "2025-06-15",
        "amount": "80000",
        "care_from": "2025-04-01",
        "care_to": "2026-04-01",
        "residential_care_received": residential,
        "paid_to_home_or_agent": True,
        "home_basis": "cap459_licence",
        "home_status_in_force": [{"from": "2025-04-01", "to": "2026-04-01", "value": True}],
    }


# ---------------------------------------------------------------------------
# REQ-6 命名节点（S002 §9.4）
# ---------------------------------------------------------------------------


def test_002_duplicate_key_rejected() -> None:
    """同 (kind, claimant, subject) 重复条目 → E_INPUT_CONTRADICTORY。

    规格锚点：Annex 005 §8.1 行 1。
    """
    year = "2025_26"
    _expect_contradictory(
        _salary_input(
            year,
            _subjects_with([("d1", "2015-06-01")]),
            **{
                "subject_facts": {"d1": {"unmarried": _tf(year, True)}},
                "relations": [_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
                "claims": [_claim("c1", "child"), _claim("c2", "child")],
            },
        ),
        "同 (child,p1,d1) 重复条目",
    )


def test_002_parent_grandparent_mutex() -> None:
    """parent＋grandparent 同 subject → 类互斥 E_INPUT_CONTRADICTORY。

    规格锚点：Annex 005 §8.1 行 3。
    """
    year = "2025_26"
    _expect_contradictory(
        _salary_input(
            year,
            _subjects_with([("d1", "1950-05-01")]),
            **{
                "subject_facts": {"d1": {"ordinarily_resident_hk": _tf(year, True)}},
                "relations": [_relation(year, "r1", "p1", "d1", kind="parent", cash="15000")],
                "claims": [_claim("c1", "parent"), _claim("c2", "grandparent")],
            },
        ),
        "parent＋grandparent 同 subject",
    )


def test_002_child_sibling_mutex() -> None:
    """child＋sibling 同 subject → 类互斥 E_INPUT_CONTRADICTORY。

    规格锚点：Annex 005 §8.1 行 3。
    """
    year = "2025_26"
    _expect_contradictory(
        _salary_input(
            year,
            _subjects_with([("d1", "2015-06-01")]),
            **{
                "subject_facts": {"d1": {"unmarried": _tf(year, True)}},
                "relations": [_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
                "claims": [_claim("c1", "child"), _claim("c2", "sibling")],
            },
        ),
        "child＋sibling 同 subject",
    )


def test_002_child_plus_disabled_stack_legal() -> None:
    """child＋disabled_dependant 同 subject 合法叠加（不误判重复）。

    规格锚点：Annex 005 §8.1 行 4。
    T3：Part 5=132,000+130,000+75,000=337,000；NCI<0 → final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "2015-06-01")]),
        claims=[_claim("c1", "child"), _claim("c2", "disabled_dependant")],
        subject_facts={"d1": {"unmarried": _tf(year, True),
                              "govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    child = _decision(result, claim_id="c1")
    dda = _decision(result, claim_id="c2")
    assert child is not None and child.get("entitlement") == "eligible", child
    assert child.get("amount") == "130000", child
    assert dda is not None and dda.get("entitlement") == "eligible", dda
    assert dda.get("amount") == "75000", dda
    assert _unit_base(result, "salary:p1", "allowances") == "337000/1", (
        f"child＋disabled 叠加合计，实际：{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def test_002_parent_plus_cohab_extra_stack_legal() -> None:
    """parent＋parent_cohabitation_additional 同 subject 合法叠加（不同 kind）。

    规格锚点：Annex 005 §8.1 行 4。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": {"ordinarily_resident_hk": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent", cash="15000",
                             cohabit=_tf(year, True))],
        claims=[_claim("c1", "parent"), _claim("c2", "parent_cohabitation_additional")],
    ))
    assert result["status"] == "partial", result["status"]
    basic = _decision(result, claim_id="c1")
    extra = _decision(result, claim_id="c2")
    assert basic is not None and basic.get("entitlement") == "eligible", basic
    assert basic.get("amount") == "50000", basic
    assert extra is not None and extra.get("entitlement") == "eligible", extra
    assert extra.get("amount") == "50000", extra


def test_002_26d_granted_vs_parent_allowance_mutex() -> None:
    """同人父母额×s.26D 获扣除同 scenario 互斥：两者皆可获计入 → 不替用户选
    （needs_input）；后者不合资格 → 不剥夺父母额。

    规格锚点：Annex 005 §8.1 行 5＋§6.2 末段（s.30(5)）。
    """
    year = "2025_26"
    subjects = _subjects_with([("d1", "1950-05-01")])
    erce_item = {"item_type": "elder_residential_care", "amount": "80000",
                 "payment_ids": ["e1"], "eligibility": {"value": True}}
    claims = [_claim("c1", "parent")]

    both_grantable = _run(_salary_input(
        year, subjects,
        care_payments=[_full_care_payment()],
        claims=claims,
        subject_facts={"d1": {"ordinarily_resident_hk": _tf(year, True),
                              "govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent", cash="15000")],
        financials={"p1": _fin(deduction_items=[erce_item])},
    ))
    assert both_grantable["status"] == "needs_input", (
        "父母额与 s.26D 扣除两者都可获计入时，服务器不替用户选（要求修正互斥意图）"
        f"，实际 {both_grantable['status']!r}"
    )
    _assert_null_amounts(both_grantable)
    assert _locates(
        _structured_questions(both_grantable), ["deduction_items"],
        year=year, subject_id="d1",
    ), both_grantable.get("questions")

    care_fails = _run(_salary_input(
        year, subjects,
        care_payments=[_full_care_payment(residential=False)],
        claims=claims,
        subject_facts={"d1": {"ordinarily_resident_hk": _tf(year, True),
                              "govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent", cash="15000")],
        financials={"p1": _fin(deduction_items=[erce_item])},
    ))
    decision = _decision(care_fails, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"s.26D 不合资格时不自动剥夺父母额（005 §6.2），实际：{decision!r}"
    )
    assert decision.get("amount") == "50000", decision
    assert not (care_fails.get("questions") or [])


def test_002_enbloc_nomination_reconciles() -> None:
    """同 subject 跨 claimant 同类申索：agreed 整组提名归 nominee（只计一次）；
    unknown → manual（可定位），均不误判矛盾。

    规格锚点：Annex 005 §8.1 行 2（s.31(3) 整组提名例外）＋§4.4
    （child_nomination：agreed/unknown/disputed）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "p2", "birth_date": "1992-02-02"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]
    participants = [{"person_id": "p1", "role": "self"},
                    {"person_id": "p2", "role": "spouse"}]
    household_base = {
        "marriage": [{"from": _window(year)[0], "to": _window(year)[1],
                      "value": "married", "spouse_id": "p2"}],
        "living_apart": _tf(year, False),
    }
    claims = [_claim("c1", "child", claimant="p1"), _claim("c2", "child", claimant="p2")]
    common = {
        "subject_facts": {"d1": {"unmarried": _tf(year, True)}},
        "participants": participants,
        "relations": [_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        "financials": {"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        "claims": claims,
        "assessment": {"basis": "actual", "candidates": ["salary_separate"],
                       "selection": "salary_separate",
                       "actual": {"salary_joint_elected": False}},
    }

    agreed = _run(_salary_input(
        year, subjects,
        household={**household_base,
                   "child_nomination": {"person_id": "p2", "status": "agreed"}},
        **common,
    ))
    allowed = [d for d in _decisions(agreed)
               if d.get("item_kind") == "child" and d.get("grant") == "allowed"]
    assert len(allowed) == 1, (
        f"整组提名后子女额只归属 nominee 一次，实际 allowed decisions：{allowed!r}"
    )
    assert allowed[0].get("claimant_id") == "p2", allowed
    assert allowed[0].get("amount") == "130000", allowed[0]
    assert agreed["status"] == "partial", agreed["status"]

    unknown = _run(_salary_input(
        year, subjects,
        household={**household_base,
                   "child_nomination": {"person_id": None, "status": "unknown"}},
        **common,
    ))
    assert not [d for d in _decisions(unknown)
                if d.get("item_kind") == "child" and d.get("grant") == "allowed"], (
        "提名 unknown → 不得确定归属任何一方（005 §4.4 manual_handoff）"
    )
    manual = _decision(unknown, subject_id="d1")
    assert manual is not None and manual.get("entitlement") == "manual", (
        f"提名 unknown → decision entitlement=manual（可定位），实际：{manual!r}"
    )


def test_002_pa_no_pa_reuse_not_duplicate() -> None:
    """同一合法事实在 PA 与 no-PA 方案各自 ledger 复用不算重复。

    规格锚点：Annex 005 §8.1 行 6＋§9.3（no_pa 仅显式列入才比较）＋§10（PA 恒
    partial）。
    构造：p1 单身、NAI 240,000/MPF 9,000、子女 entitlement＋child claim；
    candidates=["no_pa","pa_self"]（hypothetical、reference_date 2026-06-01、
    参考日通常居港一天区间）。
    T3：两方案子女额均 130,000；两方案 final 均 0（NCI<0）→ 平局优先 PA
    （§9.3 择案顺序）→ best=pa_self。
    """
    year = "2025_26"
    v2_input = {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "2015-06-01"}],
        "years": {year: _base_year(
            year,
            subject_facts={
                "p1": {"ordinarily_resident_hk": [
                    {"from": "2026-06-01", "to": "2026-06-02", "value": True}
                ]},
                "d1": {"unmarried": _tf(year, True)},
            },
            relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
            claims=[_claim("c1", "child")],
            assessment={"basis": "hypothetical",
                        "reference_date": "2026-06-01",
                        "candidates": ["no_pa", "pa_self"],
                        "selection": "best"},
        )},
        "hk_presence_days": [],
        "assumptions": [],
    }
    result = _run_pa(v2_input)

    assert result["status"] == "partial", (
        f"PA 本阶段恒 partial（005 §10），实际 {result['status']!r}"
    )
    for scenario_id in ("no_pa", "pa_self"):
        granted = [d for d in _decisions(result)
                   if d.get("scenario_id") == scenario_id
                   and d.get("claim_id") == "c1" and d.get("grant") == "allowed"]
        assert granted, (
            f"方案 {scenario_id} 的 ledger 须各自计入子女额一次（跨方案复用非重复）"
        )
    assert result.get("best_scenario") == "pa_self", (
        f"两方案 final 均 0 → 平局优先 PA（005 §9.3），实际 {result.get('best_scenario')!r}"
    )


def test_002_special_situation_manual_handoff_locator() -> None:
    """special_situation=true → 该 claim manual_handoff＋可定位；不整类禁算。

    规格锚点：Annex 005 §4.9（special_situation 行）＋§14（entitlement "manual"）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        subject_facts={"d1": {"unmarried": _tf(year, True)},
                       "p1": {"govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[
            _claim("c1", "child", special=True),
            {"claim_id": "c2", "kind": "personal_disability",
             "claimant_id": "p1", "subject_id": "p1"},
        ],
    ))
    assert result.get("status") != "blocked", (
        "special_situation 是窄 manual（可提交、可定位），不整类禁算（005 §4.9）"
    )
    manual = _decision(result, claim_id="c1")
    assert manual is not None and manual.get("entitlement") == "manual", (
        f"special_situation=true → decision entitlement=manual＋claim_id 定位，"
        f"实际：{manual!r}"
    )
    clean = _decision(result, claim_id="c2")
    assert clean is not None and clean.get("entitlement") == "eligible", (
        f"同一请求内无特殊情形的 claim 照常判定，实际：{clean!r}"
    )
    assert clean.get("amount") == "75000", clean


# ---------------------------------------------------------------------------
# P1 r1 新 Red（复核 §六.8）：ERCE 台账
# ---------------------------------------------------------------------------


def _elder_year(**year_kw: Any) -> dict:
    """长者 ERCE 公共事实：d1（1950 生、居港、DAA 全年）。"""
    year = "2025_26"
    year_kw.setdefault(
        "subject_facts",
        {"d1": {"ordinarily_resident_hk": _tf(year, True),
                "govt_disability_allowance": _tf(year, True)}},
    )
    year_kw.setdefault(
        "relations",
        [_relation(year, "r1", "p1", "d1", kind="parent", cash="15000")],
    )
    return _base_year(year, **year_kw)


def _erce_care(pid: str = "e1", *, payer: str = "p1", subject: str = "d1",
               amount: str = "60000") -> dict:
    return {
        "payment_id": pid, "payer_id": payer, "subject_id": subject,
        "paid_on": "2025-06-15", "amount": amount,
        "care_from": "2025-04-01", "care_to": "2026-04-01",
        "residential_care_received": True, "paid_to_home_or_agent": True,
        "home_basis": "cap459_licence",
        "home_status_in_force": [{"from": "2025-04-01", "to": "2026-04-01",
                                  "value": True}],
    }


def test_002_erce_duplicate_payment_reference_rejected() -> None:
    """P1 r1 新 Red（复核 §六.8/R5）：同一付款 ID 被多条 ERCE 意图重复引用
    → 拒绝（不得重复扣除；复核观察：同 e1 重复扣 120,000）。

    规格锚点：Annex 005 §6.2（同 payment ID 重复进入一个 scenario 时拒绝）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    item = {"item_type": "elder_residential_care", "amount": "60000",
            "payment_ids": ["e1"], "eligibility": {"value": True},
            "subject_id": "d1"}
    try:
        _run({
            "year_of_assessment": year,
            "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                         {"subject_id": "d1", "birth_date": "1950-05-01"}],
            "years": {year: _elder_year(
                care_payments=[_erce_care()],
                financials={"p1": _fin(deduction_items=[item, dict(item)])},
            )},
            "hk_presence_days": [],
            "assumptions": [],
        })
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"同 e1 被两条扣除意图重复引用须矛盾拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "同一付款 ID 不得重复进入同一 scenario 扣除（005 §6.2）"
        )


def test_002_erce_cross_person_mutex_with_parent_claim() -> None:
    """P1 r1 新 Red（复核 §六.8）：同长者跨人重复进入一个 scenario（p1 申索
    父母额＋p2 申索 ERCE）→ 拒绝（无争议情形）。

    规格锚点：Annex 005 §6.2（「同长者跨人重复进入一个 scenario 时拒绝/
    争议转人工」——无争议事实取拒绝分支）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    try:
        _run({
            "year_of_assessment": year,
            "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                         {"subject_id": "p2", "birth_date": "1992-02-02"},
                         {"subject_id": "d1", "birth_date": "1950-05-01"}],
            "years": {year: _elder_year(
                care_payments=[_erce_care(payer="p2")],
                relations=[
                    _relation(year, "r1", "p1", "d1", kind="parent",
                              cash="15000"),
                    _relation(year, "r2", "p2", "d1", kind="parent",
                              cash="15000"),
                ],
                participants=[{"person_id": "p1", "role": "self"},
                              {"person_id": "p2", "role": "spouse"}],
                household={
                    "marriage": [{"from": "2025-04-01", "to": "2026-04-01",
                                  "value": "married", "spouse_id": "p2"}],
                    "living_apart": _tf(year, False),
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                financials={"p1": _fin(),
                            "p2": _fin(nai="200000", mpf="18000",
                                       deduction_items=[
                                           {"item_type": "elder_residential_care",
                                            "amount": "60000",
                                            "payment_ids": ["e1"],
                                            "eligibility": {"value": True},
                                            "subject_id": "d1"}])},
                claims=[_claim("c1", "parent")],
            )},
            "hk_presence_days": [],
            "assumptions": [],
        })
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"同长者跨人（parent＋ERCE）重复进入一个 scenario 须拒绝，"
            f"实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "同长者跨人重复进入同一 scenario（005 §6.2）：无争议事实必须拒绝"
        )


def test_002_erce_subject_mismatch_rejected() -> None:
    """P1 r1 新 Red（复核 §六.8）：ERCE item 的 subject_id 与所引付款的
    subject 不一致 → 拒绝（不得按 item 声明的长者扣除他人付款）。

    规格锚点：Annex 005 §6.2（ERCE：subject_id 关联长者；amount 必须等于
    所引付款合计——subject 一致性属同一对账）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    try:
        _run({
            "year_of_assessment": year,
            "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                         {"subject_id": "d1", "birth_date": "1950-05-01"},
                         {"subject_id": "d2", "birth_date": "1948-01-01"}],
            "years": {year: _elder_year(
                subject_facts={
                    "d1": {"ordinarily_resident_hk": _tf(year, True),
                           "govt_disability_allowance": _tf(year, True)},
                    "d2": {"ordinarily_resident_hk": _tf(year, True)},
                },
                # red-b-review：d2 一并具备合法亲属关系，使唯一冲突＝item(d2)
                # 与所引付款(d1) 的 subject 不一致。
                relations=[
                    _relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
                    _relation(year, "r2", "p1", "d2", kind="parent", cash="15000"),
                ],
                care_payments=[_erce_care(subject="d1")],
                financials={"p1": _fin(deduction_items=[
                    {"item_type": "elder_residential_care", "amount": "60000",
                     "payment_ids": ["e1"], "eligibility": {"value": True},
                     "subject_id": "d2"}])},
            )},
            "hk_presence_days": [],
            "assumptions": [],
        })
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"ERCE subject（d2）与付款 subject（d1）不一致须以 E_INPUT_CONTRADICTORY "
            f"拒绝（不得按 item 声明的长者扣除他人付款），实际 {exc.code!r}"
        )
        assert str(exc.field or "").startswith(
            "years.2025_26.financials.p1.deduction_items"
        ), f"须精确定位该 deduction item，实际 field={exc.field!r}"
    else:
        raise AssertionError(
            "ERCE 声明的长者须与所引付款的受养人一致（005 §6.2），不得计入"
        )


# ---------------------------------------------------------------------------
# B lane 补齐（p1-r2-red-completion-plan §B）：活动 PA 数组/来源/差饷/人工边界、
# s.31(3) 非分居前提（L 拥有 PA 运行 helper，不改 A 文件）
# ---------------------------------------------------------------------------


def _pa_no_pa_input(year: str, finance: dict) -> dict:
    return _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        financials={"p1": finance},
        assessment={"basis": "hypothetical", "candidates": ["no_pa"], "selection": "no_pa"},
    )


def _positive_property() -> dict:
    return {
        "property_id": "h1",
        "share": "1/1",
        "rent_received": "120000",
        "rates_paid_by_owner": {"amount": "0", "agreed": False, "actually_paid": False},
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
        "mortgage_interest": "0",
    }


def _sole_business(profit: str = "300000", share: str = "1/1") -> dict:
    return {"business_id": "b1", "entity_kind": "sole_proprietorship",
            "assessable_profit": profit, "share": share}


def test_002_pa_no_component_source_empty_arrays_control() -> None:
    """B 对照：无物业/业务来源且显式 [] → 照常按薪俸单位计算（940）。"""
    year = "2025_26"
    result = _run_pa(_pa_no_pa_input(year, _fin()))
    assert result["status"] == "partial", result["status"]
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]
    assert result.get("best_scenario") == "no_pa", result.get("best_scenario")


@pytest.mark.parametrize("missing_key", ["properties", "businesses"])
def test_002_pa_missing_component_array_not_empty(missing_key) -> None:
    """B：活动 PA 候选下已确认存在的组件数组省略＝未知（≠[]）→ needs_input＋定位。

    red-b-review：基线声明“活动组件存在”（confirmed_in_scope＋组件条目＋
    present=true），唯一变更为删除该数组；「无来源＋显式 []」另有独立对照。
    """
    year = "2025_26"
    if missing_key == "properties":
        baseline = _fin(
            properties=[_positive_property()],
            has_assessable_property_income=True,
            source_classification={"salary": "confirmed_in_scope",
                                   "property": "confirmed_in_scope"},
        )
    else:
        baseline = _fin(
            businesses=[_sole_business()],
            has_assessable_business_income=True,
            source_classification={"salary": "confirmed_in_scope",
                                   "business": "confirmed_in_scope"},
        )
    facts = _pa_no_pa_input(year, baseline)
    del facts["years"][year]["financials"]["p1"][missing_key]
    result = _run_pa(facts)
    assert result["status"] == "needs_input", (missing_key, result["status"])
    assert all(value is None for value in result["amounts"].values()), result["amounts"]
    assert _locates(result.get("questions") or [], [missing_key],
                    year=year, subject_id="p1"), (
        missing_key, result.get("questions")
    )


def _pa_property_input(year: str, rates: dict) -> dict:
    prop = _positive_property()
    prop["rates_paid_by_owner"] = rates
    return _pa_no_pa_input(year, _fin(
        nai="0", mpf="0", ai=False,
        properties=[prop],
        # red-b-review 修正 #1：物业基线显式声明来源存在性。
        has_assessable_property_income=True,
        source_classification={"salary": "confirmed_in_scope",
                               "property": "confirmed_in_scope"},
    ))


@pytest.mark.parametrize(
    "agreed,actually_paid",
    [("unknown", True), (True, "unknown"), ("unknown", "unknown")],
    ids=["agreed-unknown", "actually-paid-unknown", "both-unknown"],
)
def test_002_pa_rates_unknown_blocks_property_path(agreed, actually_paid) -> None:
    """B：差饷 amount>0 而 agreed/actually_paid 任一 unknown → 相关物业路径未决
    （不静默按不扣差饷继续），精确定位 rates_paid_by_owner（单一变量矩阵）。"""
    year = "2025_26"
    result = _run_pa(_pa_property_input(
        year, {"amount": "3000", "agreed": agreed, "actually_paid": actually_paid}))
    assert result["status"] == "needs_input", (agreed, actually_paid, result["status"])
    assert all(value is None for value in result["amounts"].values()), result["amounts"]
    assert _locates(result.get("questions") or [], ["rates_paid_by_owner"],
                    year=year, subject_id="p1"), (
        agreed, actually_paid, result.get("questions")
    )


@pytest.mark.parametrize(
    "agreed,actually_paid",
    [(False, False), (False, True), (True, False)],
    ids=["both-false", "agreed-false-actually-true", "agreed-true-actually-false"],
)
def test_002_pa_rates_known_false_control_computes(agreed, actually_paid) -> None:
    """B 对照：差饷未获扣除的可判定形态（任一 false）→ NAV=96,000、税 14,400。

    只有 agreed 与 actually_paid 同为 true 才扣除；其余组合按不扣差饷继续。
    """
    year = "2025_26"
    result = _run_pa(_pa_property_input(
        year, {"amount": "3000", "agreed": agreed, "actually_paid": actually_paid}))
    assert result["status"] == "partial", (agreed, actually_paid, result["status"])
    assert _property_unit_base(result, "h1", "nav") == "96000/1", (
        f"NAV=(120,000−0)×(1−1/5)=96,000，实际："
        f"{_property_unit_base(result, 'h1', 'nav')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "14400", result["amounts"]


def test_002_pa_rates_both_true_deducts() -> None:
    """B 对照：agreed=true 且 actually_paid=true → 差饷 3,000 扣除 →
    NAV=(120,000−3,000)×(1−1/5)=93,600、税 14,040。"""
    year = "2025_26"
    result = _run_pa(_pa_property_input(
        year, {"amount": "3000", "agreed": True, "actually_paid": True}))
    assert result["status"] == "partial", result["status"]
    assert _property_unit_base(result, "h1", "nav") == "93600/1", (
        f"NAV=(120,000−3,000)×(1−1/5)=93,600，实际："
        f"{_property_unit_base(result, 'h1', 'nav')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "14040", result["amounts"]


def _pa_component_input(year: str, kind: str, *, classification: dict) -> dict:
    finance = _fin(nai="0", mpf="0", ai=False, source_classification=classification)
    if kind == "property":
        finance["properties"] = [_positive_property()]
        # red-b-review 修正 #1：物业/业务基线显式声明来源存在性。
        finance["has_assessable_property_income"] = True
    else:
        finance["businesses"] = [_sole_business()]
        finance["has_assessable_business_income"] = True
    return _pa_no_pa_input(year, finance)


def test_002_pa_component_source_confirmed_controls() -> None:
    """B 对照：分类 confirmed 时同一组件照常计算（物业 14,400；业务 19,500）。"""
    year = "2025_26"
    prop = _run_pa(_pa_component_input(
        year, "property",
        classification={"salary": "confirmed_in_scope", "property": "confirmed_in_scope"}))
    assert prop["status"] == "partial", prop["status"]
    assert prop["amounts"]["final_after_reduction"] == "14400", prop["amounts"]
    biz = _run_pa(_pa_component_input(
        year, "business",
        classification={"salary": "confirmed_in_scope", "business": "confirmed_in_scope"}))
    assert biz["status"] == "partial", biz["status"]
    assert biz["amounts"]["final_after_reduction"] == "19500", biz["amounts"]


@pytest.mark.parametrize(
    "kind,value",
    [("property", "unknown"), ("property", "manual"),
     ("business", "unknown"), ("business", "manual")],
)
def test_002_pa_component_source_classification_blocks(kind, value) -> None:
    """B：活动来源分类 unknown/manual → 相关候选未决、无确定税额、精确定位。"""
    year = "2025_26"
    result = _run_pa(_pa_component_input(
        year, kind,
        classification={"salary": "confirmed_in_scope", kind: value}))
    assert result["status"] in ("needs_input", "blocked"), (kind, value, result["status"])
    assert all(v is None for v in result["amounts"].values()), (kind, value, result["amounts"])
    assert _locator_ok(result, [f"source_classification.{kind}"],
                       year=year, subject_id="p1"), (
        kind, value, result.get("questions")
    )


@pytest.mark.parametrize("kind", ["property", "business"])
def test_002_pa_component_source_classification_missing_needs_input(kind) -> None:
    """B：需要判定的来源分支缺 source_classification＝needs_input（不默认 confirmed）。

    red-b-review：只删除目标 kind 键，保留其余已确认来源（不同于整个
    source_classification 缺失）。
    """
    year = "2025_26"
    result = _run_pa(_pa_component_input(
        year, kind, classification={"salary": "confirmed_in_scope"}))
    assert result["status"] == "needs_input", (kind, result["status"])
    assert all(v is None for v in result["amounts"].values()), (kind, result["amounts"])
    assert _locates(result.get("questions") or [], [f"source_classification.{kind}"],
                    year=year, subject_id="p1"), (
        kind, result.get("questions")
    )


def test_002_pa_manual_property_scope_blocks_related_candidate() -> None:
    """B：manual_issues scope=property 定位 h1 → 依赖 h1 的候选未决（≠删除/静默计入）。

    定位核验：受影响对象为物业组件（结构化 field 指向 properties 家族）；
    不要求 English/manual_issues 字面量或未冻结数组下标。
    """
    year = "2025_26"
    facts = _pa_property_input(
        year, {"amount": "0", "agreed": False, "actually_paid": False})
    facts["years"][year]["manual_issues"] = [
        {"scope": "property", "id": "h1", "code": "loss_or_transfer"}]
    result = _run_pa(facts)
    assert result["status"] in ("needs_input", "blocked"), result["status"]
    assert all(v is None for v in result["amounts"].values()), result["amounts"]
    assert _locator_ok(result, ["properties"], year=year,
                       subject_id="p1"), result.get("questions")


def test_002_pa_manual_business_scope_blocks_related_candidate() -> None:
    """B：manual_issues scope=business 定位 b1 → 依赖 b1 的候选未决。"""
    year = "2025_26"
    facts = _pa_component_input(
        year, "business",
        classification={"salary": "confirmed_in_scope", "business": "confirmed_in_scope"})
    facts["years"][year]["manual_issues"] = [
        {"scope": "business", "id": "b1", "code": "loss_or_transfer"}]
    result = _run_pa(facts)
    assert result["status"] in ("needs_input", "blocked"), result["status"]
    assert all(v is None for v in result["amounts"].values()), result["amounts"]
    assert _locator_ok(result, ["businesses"], year=year,
                       subject_id="p1"), result.get("questions")


@pytest.mark.parametrize(
    "kind,issue_id",
    [("property", "h1"), ("business", "b1")],
    ids=["property-issue", "business-issue"],
)
def test_002_salary_candidate_ignores_property_business_manual_issue(
    kind, issue_id
) -> None:
    """B 对照（red-b-review：salarycandidatecanignoreproperty/businessissue）：
    同一财务对象上物业/业务的 manual scope 事项只使依赖该组件的候选未决；
    薪俸候选不依赖组件 → 已知薪俸路径照常（组件级局部性＝受影响对象才未决）。
    PA 组件候选受阻的对照见上方 `test_002_pa_manual_*` 节点。
    """
    year = "2025_26"
    finance = _fin(
        has_assessable_property_income=True,
        has_assessable_business_income=True,
        properties=[_positive_property()],
        businesses=[_sole_business()],
        source_classification={"salary": "confirmed_in_scope",
                               "property": "confirmed_in_scope",
                               "business": "confirmed_in_scope"},
    )
    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        financials={"p1": finance},
        manual_issues=[{"scope": kind, "id": issue_id, "code": "loss_or_transfer"}],
    )
    result = _run(facts)
    assert result["status"] == "partial", (kind, result["status"])
    assert not (result.get("questions") or []), (kind, result.get("questions"))
    assert result["amounts"]["final_after_reduction"] == "940", (
        kind, result["amounts"]
    )
    assert _unit_base(result, "salary:p1", "allowances") == "132000/1", (
        kind, _unit_base(result, "salary:p1", "allowances")
    )


def _candidate_checks(result: Mapping) -> list:
    for step in result.get("steps") or []:
        if isinstance(step, dict) and step.get("kind") == "selection":
            return list((step.get("detail") or {}).get("candidate_checks") or [])
    return []


def _candidate_admission(result: Mapping, candidate_id: str) -> str | None:
    for check in _candidate_checks(result):
        if check.get("candidate_id") == candidate_id:
            return check.get("admission")
    return None


def _s6_household(year: str, *, reference: str) -> dict:
    from datetime import date, timedelta

    w_from, w_to = _window(year)
    ref_next = (date.fromisoformat(reference) + timedelta(days=1)).isoformat()
    marriage = [{"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"},
                {"from": reference, "to": ref_next, "value": "married", "spouse_id": "p2"}]
    apart = list(_tf(year, False))
    apart.append({"from": reference, "to": ref_next, "value": False})
    return {
        "marriage": marriage,
        "living_apart": apart,
        "child_nomination": {"person_id": None, "status": "none"},
    }


def _resident_at_reference(reference: str) -> list[dict]:
    from datetime import date, timedelta

    ref_next = (date.fromisoformat(reference) + timedelta(days=1)).isoformat()
    return [{"from": reference, "to": ref_next, "value": True}]


def _ordinary_joint_actual() -> dict:
    """合法普通夫妻的实际选择事实（005 §5.1/§5.2 形状：已核 actual 选择＋
    ordinary 活动历史＋PA mode=none）；red-b-review 修正 #1。"""
    return {
        "salary_joint_elected": False,
        "salary_joint_assessed": False,
        "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
        "history": "ordinary",
    }


def _pa_joint_input(year: str, *, all_source: Any) -> dict:
    reference = "2026-06-01"
    finance = {
        "p1": _fin(has_income_assessable_under_ordinance=all_source),
        "p2": _fin(nai="0", mpf="0", ai=True, has_income_assessable_under_ordinance=True),
    }
    return _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"}],
        household=_s6_household(year, reference=reference),
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        subject_facts={
            "p1": {"ordinarily_resident_hk": _resident_at_reference(reference)},
            "p2": {"ordinarily_resident_hk": _resident_at_reference(reference)},
        },
        financials=finance,
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "candidates": ["pa_joint"], "selection": "pa_joint",
                    "actual": _ordinary_joint_actual()},
    )


def test_002_pa_joint_all_source_unknown_unresolved_with_locator() -> None:
    """B：显式全来源 unknown 保留并追问（不静默改写 false/true）。

    对照：双方 true 时 pa_joint eligible 且 best 确定；改 p1 unknown（单变量）
    → 该候选 unresolved、best=null、六键 null，追问定位
    has_income_assessable_under_ordinance。
    """
    year = "2025_26"
    baseline = _run_pa(_pa_joint_input(year, all_source=True))
    assert baseline["status"] == "partial", baseline["status"]
    assert _candidate_admission(baseline, "pa_joint") == "eligible", _candidate_checks(baseline)
    assert baseline.get("best_scenario") == "pa_joint", baseline.get("best_scenario")

    unknown = _run_pa(_pa_joint_input(year, all_source="unknown"))
    assert unknown["status"] == "needs_input", unknown["status"]
    assert all(v is None for v in unknown["amounts"].values()), unknown["amounts"]
    assert unknown.get("best_scenario") is None, unknown.get("best_scenario")
    assert _candidate_admission(unknown, "pa_joint") == "unresolved", (
        "显式全来源 unknown 保留未决（不得改写为 false/ineligible）",
        _candidate_checks(unknown),
    )
    assert _locates(unknown.get("questions") or [],
                    ["has_income_assessable_under_ordinance"],
                    year=year, subject_id="p1"), unknown.get("questions")


def _separated_household(year: str, *, apart: Any = True) -> dict:
    w_from, w_to = _window(year)
    living_apart = _tf(year, apart) if apart is not None else []
    return {
        "marriage": [{"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}],
        "living_apart": living_apart,
        "child_nomination": {"person_id": "p2", "status": "agreed"},
    }


def _separated_enbloc_input(year: str, *, claims: list, p2_maintained: bool = True,
                            apart: Any = True) -> dict:
    return _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        household=_separated_household(year, apart=apart),
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True)),
                   _relation(year, "r2", "p2", "d1",
                             maintained=_tf(year, p2_maintained))],
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        claims=claims,
    )


def test_002_enbloc_nomination_requires_nonseparation_duplicate_rejected() -> None:
    """B（C1）：已证分居夫妻不适用 s.31(3) 整组提名例外 → 跨 claimant 重复子女
    申索仍属 E_INPUT_CONTRADICTORY。"""
    from app.core.errors import AppError

    year = "2025_26"
    facts = _separated_enbloc_input(
        year,
        claims=[_claim("c1", "child", claimant="p1"),
                _claim("c2", "child", claimant="p2")],
    )
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
    else:
        raise AssertionError(
            "已证分居下重复子女申索必须拒绝（s.31(3) 例外仅限非分居夫妻）"
        )


def test_002_enbloc_single_claim_not_silently_transferred() -> None:
    """B（C1）：已证分居时 s.31(3) 不适用——单一子女申索不得静默转移给提名人，
    仍归实际申索人（130,000）。

    red-b-review 修正 #5：单一申索场景把 p2 的 maintained 置 false，使 p1 的
    子女资格成为唯一已证权利（证明“归实际申索人”而非双权竞合）。
    """
    year = "2025_26"
    result = _run(_separated_enbloc_input(
        year, claims=[_claim("c1", "child", claimant="p1")], p2_maintained=False))
    allowed = [
        d for d in _decisions(result)
        if d.get("item_kind") == "child" and d.get("grant") == "allowed"
    ]
    assert len(allowed) == 1, allowed
    assert allowed[0].get("claimant_id") == "p1", (
        f"已证分居 ⇒ 单 claim 须归实际申索人（不得静默转移给提名人），实际：{allowed[0]!r}"
    )
    assert allowed[0].get("amount") == "130000", allowed[0]


def test_002_enbloc_nomination_requires_nonseparation_unknown_not_auto_assigned() -> None:
    """B（C1）：提名 agreed 而非分居事实未知（living_apart 未覆盖）→ 不自动按
    提名人归属、不得判整组矛盾；追问婚姻/分居事实再定归属。

    red-b-review 矩阵：新增案例＝agreed nomination＋nonseparation unknown
    （非旧 nomination=unknown 案例）；双申索保持双方原始权利不变。
    """
    year = "2025_26"
    result = _run(_separated_enbloc_input(
        year,
        claims=[_claim("c1", "child", claimant="p1"),
                _claim("c2", "child", claimant="p2")],
        apart=None,
    ))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    assert not [
        d for d in _decisions(result)
        if d.get("item_kind") == "child" and d.get("grant") == "allowed"
        and d.get("claimant_id") == "p2"
    ], (
        "非分居事实未知 ⇒ 不得按提名人自动归属（005 §8.1 行 2 例外不成立）",
        _decisions(result),
    )
    assert _locates_any(result.get("questions") or [], [
        ["living_apart"],
        ["marriage"],
    ], year=year), result.get("questions")
