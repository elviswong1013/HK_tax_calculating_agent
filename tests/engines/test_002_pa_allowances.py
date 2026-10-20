# -*- coding: utf-8 -*-
"""REQ-8 PA 免税额接入、候选择案与单位覆盖（002 P1 Red；Annex 005）。

REQ: REQ-8（S002 §8/§9.4；PRD AC-8）。本文件为 SDD §9.4 REQ-8 引擎侧命名节点
（6）＋V006 §5.3（B3，7）＋P1 r2 定点勘误/补测节点（fixture-audit #1–#5＋
   completion-plan A：七行依赖边界、单位前端一致性、PA trace/下一组件；见
   各节点 docstring）。原 13 个命名节点：
  - test_002_part5_lowers_progressive_not_standard_base
  - test_002_salary_std_base_unchanged
  - test_002_pa_self_spouse_no_pa_mixed_g2_proposal_tagged
  - test_002_rounding_branches_in_trace_per_annex_d
  - test_002_s41_1b_blocks_separate_pa_after_joint_salaries
  - test_002_married_not_auto_joint_pa
  - test_002_candidate_effective_salary_pa_matrix
  - test_002_actual_no_pa_joint_salary_not_silently_split
  - test_002_pa_self_effective_election_changes_spouse_s29
  - test_002_mixed_shared_unit_not_double_counted
  - test_002_all_pa_all_no_pa_ordinary_partnership_preserved
  - test_002_actual_pa_entries_per_person_not_collapsed
  - test_002_effective_pa_entry_closed_map_no_scalar_collapse

规格锚点:
  - S002 §5.4（Part 5 只降累进基数 s.42A；标准径基数不含 Part 5；候选显式；
    EffectiveAssessment 内部派生）＋§8 REQ-8；
  - Annex 005 §9.2（P1–P5）、§9.3（有限候选表＋EffectiveAssessment；
    no_pa 非隐式候选；actual no_pa＋joint salaries unsupported 不静默拆；
    pa_self 的 s.29 判断须看到 self 已 individual PA；mixed 共享单位仅该候选
    unsupported（CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED）作未决竞争者；
    平局择案顺序）、§4.10（actual.pa 稀疏复制、缺键＝缺席不补默认；8 条
    短路规则）、§10（PA 恒 partial）、§14（branch 实际执行链：
    salary_floor_min_v1／pa_unit_floor_min_v1／pa_stc_comp_tp_v1）、§2.1。

期望值来源（禁从生产代码反推）:
  - 免税额：Annex 005 §15.2（附表 4 T1）；税率带：附表 1/2（T1 台账 1.17）；
    宽减 2025/26 cap 3,000（T1）。
  - 无额外免税额基线 final 940：PAM39 Q1（T2 台账 1.13）。
  - 其余数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - compare_personal_assessment(facts, bundle, *, availability=None)（005 §2.1）
    与 calculate_salaries_tax(…, availability=None)。
  - PA 候选合法性按 P1–P5；候选显式非空不隐式添加（no_pa 仅显式列入）；
    每候选 EffectiveAssessment 逐人稀疏 pa_by_person（缺键保持缺键）；
    混合共享单位仅该候选 unsupported/unresolved 阻 best；全 PA/全 no_pa/
    普通合伙保留；Part 5 不减标准径基数；取整按真实执行分支记 trace。
  - 本文件对 app.* 延迟导入（测试体内）。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


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
                # P1 r1 纠错（复核 §五.5）：固定束须含 PA 活动参数（物业/利得
                # 比较）——A6 修复删除 _APPROVED_PA_DATA 运行时回退后，活动
                # 参数缺失＝E_RULES_STATE_UNVERIFIABLE（005 §2.1）。
                # 数值＝T1（附表 1/3C/两级利得；与现 v2 生产束同值）。
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
# v2 input 构造（Annex 005 §3/§4/§5.2 示例形状）
# ---------------------------------------------------------------------------


def _window(year: str) -> tuple[str, str]:
    start = int(year[:4])
    return f"{start}-04-01", f"{start + 1}-04-01"


def _tf(year: str, value: Any, frm: str | None = None, to: str | None = None) -> list[dict]:
    w_from, w_to = _window(year)
    return [{"from": frm or w_from, "to": to or w_to, "value": value}]


def _resident_at_reference() -> list[dict]:
    """P1 居港肢：参考日（2026-06-01）一天区间（005 §5.2 示例形状）。"""
    return [{"from": "2026-06-01", "to": "2026-06-02", "value": True}]


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


def _shared_property(share: str = "1/2", rent: str = "240000") -> dict:
    """共同持有物业 h1（原始字段同值、份额 1/2；005 §4.6 Property 形状）。"""
    return {
        "property_id": "h1",
        "share": share,
        "rent_received": rent,
        "rates_paid_by_owner": {"amount": "0", "agreed": False, "actually_paid": False},
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
        "mortgage_interest": "0",
    }


def _partnership_business(share: str = "1/2", profit: str = "300000") -> dict:
    return {
        "business_id": "b1",
        "entity_kind": "partnership",
        "assessable_profit": profit,
        "share": share,
    }


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


def _pa_input(
    year: str, subjects: list, *, presence: list | None = None, **year_kw: Any
) -> dict:
    # P1 r1 纠错（复核 §五.5/A4 日期与居港错位）：presence 路由到输入
    # 顶层 hk_presence_days（005 §3 Input 顶层封闭键＋§4.7
    # {person_id,year,days}＝s.41 temporary-resident 辅助日数；年份限
    # 显式 Year 的 {Y−1,Y,Y+1} 并集），不得误传 _base_year（Year 无此键）。
    return {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": {year: _base_year(year, **year_kw)},
        "hk_presence_days": presence if presence is not None else [],
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


def _subjects_couple() -> list[dict]:
    return [{"subject_id": "p1", "birth_date": "1990-01-01"},
            {"subject_id": "p2", "birth_date": "1992-02-02"}]


_COUPLE_PARTICIPANTS = [{"person_id": "p1", "role": "self"}, {"person_id": "p2", "role": "spouse"}]


def _s6_household(year: str, *, reference: str | None = None) -> dict:
    """S6 形态 2：全域同一婚姻＋全部婚姻期间明确 not apart。

    P1 r1 纠错（复核 §五.5）：joint PA 候选的婚姻/非分居按候选有效日
    （hypothetical＝参考日）求值（005 §9.2 P2）；正例按 §7 的参考日一天
    区间 synthetic control 显式提供婚姻/分居覆盖（参考日在 YA 窗口之外）。
    """
    w_from, w_to = _window(year)
    marriage = [{"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}]
    apart = list(_tf(year, False))
    if reference is not None:
        from datetime import date, timedelta

        ref_next = (date.fromisoformat(reference) + timedelta(days=1)).isoformat()
        marriage.append({"from": reference, "to": ref_next,
                         "value": "married", "spouse_id": "p2"})
        apart.append({"from": reference, "to": ref_next, "value": False})
    return {
        "marriage": marriage,
        "living_apart": apart,
        "child_nomination": {"person_id": None, "status": "none"},
    }


def _couple_property_facts(year: str) -> dict:
    """夫妻各持 h1 半份物业＋全来源存在（P2 肢事实）。"""
    return {
        "p1": _fin(nai="240000", mpf="9000", ai=True,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_property_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "property": "confirmed_in_scope"},
                   properties=[_shared_property()]),
        "p2": _fin(nai="0", mpf="0", ai=False,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_property_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "property": "confirmed_in_scope"},
                   properties=[_shared_property()]),
    }


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


# ---------------------------------------------------------------------------
# 断言辅助（005 §13/§14）
# ---------------------------------------------------------------------------


def _steps(result: Mapping, kind: str) -> list[dict]:
    return [
        s.get("detail") or {}
        for s in (result.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == kind and isinstance(s.get("detail"), dict)
    ]


def _decisions(result: Mapping) -> list[dict]:
    return _steps(result, "decision")


def _unit_base(result: Mapping, unit_id: str, name: str, scenario_id: str | None = None) -> list:
    values = []
    for detail in _steps(result, "assessment"):
        if detail.get("unit_id") == unit_id and (
            scenario_id is None or detail.get("scenario_id") == scenario_id
        ):
            for b in detail.get("bases") or []:
                if b.get("name") == name:
                    values.append(b.get("value"))
    return values


def _branches(result: Mapping) -> set:
    return {detail.get("branch") for detail in _steps(result, "assessment")}


def _ops(result: Mapping) -> set:
    ops = set()
    for detail in _steps(result, "assessment"):
        for op in detail.get("operations") or []:
            if isinstance(op, dict):
                ops.add(op.get("op"))
    return ops


def _selection(result: Mapping) -> dict:
    selections = _steps(result, "selection")
    assert selections, "steps 必须含 selection tagged step（005 §14）"
    return selections[0]


def _check(selection: Mapping, candidate_id: str) -> dict:
    for c in selection.get("candidate_checks") or []:
        if c.get("candidate_id") == candidate_id:
            return c
    raise AssertionError(
        f"candidate_checks 须含 {candidate_id}（005 §14），实际："
        f"{selection.get('candidate_checks')!r}"
    )


_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


def _assert_amounts_all_null(result: Mapping) -> None:
    """needs_input/blocked：六个金额键一律显式 None、键不省略（C3.3/C3.4）。"""
    amounts = result["amounts"]
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), (
        f"amounts 必须恰含六键，实际：{sorted(amounts)!r}"
    )
    assert all(value is None for value in amounts.values()), (
        f"不得输出任何金额（资格不明不填零），实际：{amounts!r}"
    )


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


def _question_for(
    result: Mapping, year: str, *field_needles: str, subject_id: str | None = None
) -> dict:
    """按 field/year（可选 subject_id）定位 questions 项。

    005 §13：questions 项含 {field, year, claim_id?, subject_id?, question_zh}；
    此处按冻结的 field/year 定位，不做整段 JSON 词搜索、不强制文案措辞。
    """
    for q in _structured_questions(result):
        field = q.get("field")
        if not isinstance(field, str) or not field:
            continue
        if q.get("year") != year:
            continue
        if not all(needle in field for needle in field_needles):
            continue
        if subject_id is not None and q.get("subject_id") != subject_id:
            continue
        return q
    raise AssertionError(
        f"questions 须含 year={year} 且 field 含 {field_needles!r}"
        f"（subject_id={subject_id!r}）的定位项（005 §13），"
        f"实际：{result.get('questions')!r}"
    )


# ---------------------------------------------------------------------------
# REQ-8 命名节点（S002 §9.4 引擎侧）
# ---------------------------------------------------------------------------


def test_002_part5_lowers_progressive_not_standard_base() -> None:
    """Part 5 只降 PA 累进基数（s.42A）；标准径基数＝NAI−Part 4A（不减 Part 5）。

    规格锚点：S002 §5.4＋Annex 005 §9.2 P1（pa_self 合法候选）。
    T3：NAI 240,000、MPF 9,000、child 130,000 → 累进基数=−61,000→0；标准径
    基数=231,000（若错误减 Part 5 会是 101,000）→ 标准税 34,650 → 择低 0。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_single([("d1", "2015-06-01")]),
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child")],
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    assert result["status"] == "partial", result["status"]
    collected = []
    for detail in _steps(result, "assessment"):
        if detail.get("scenario_id") == "pa_self":
            for b in detail.get("bases") or []:
                if b.get("name") == "standard_base":
                    collected.append(b.get("value"))
    assert collected == ["231000/1"], (
        f"PA 标准径基数不得减 Part 5（须为 NAI−MPF=231,000），实际：{collected!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


def test_002_salary_std_base_unchanged() -> None:
    """薪俸侧同律：Part 5 只降 NCI，标准径基数＝NAI−Part 4A 不减 Part 5。

    规格锚点：S002 §5.4（s.13(2)）。
    T3：child 130,000 计入 → NCI=−31,000→0 税；standard_base=231,000；
    final 0（对照无子女 940）。
    """
    year = "2025_26"
    result = _run(_pa_input(
        year,
        _subjects_single([("d1", "2015-06-01")]),
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child")],
    ))
    assert result["status"] == "partial", result["status"]
    values = []
    for detail in _steps(result, "assessment"):
        if detail.get("unit_id") == "salary:p1":
            for b in detail.get("bases") or []:
                if b.get("name") == "standard_base":
                    values.append(b.get("value"))
    assert values == ["231000/1"], (
        f"薪俸标准径基数不得减 Part 5，实际：{values!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


def test_002_pa_self_spouse_no_pa_mixed_g2_proposal_tagged() -> None:
    """混合 pa_self＋spouse no_pa 的共享单位无已批分配 → 仅该候选
    unsupported（CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED）作未决竞争者阻 best。

    规格锚点：S002 §5.4/§8 REQ-8（G2 决定项）＋Annex 005 §9.3 mixed 行。
    构造：夫妻各持 h1 半份；no_pa 候选按既有份额单位可算。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=_couple_property_facts(year),
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    selection = _selection(result)
    mixed = _check(selection, "pa_self")
    assert mixed.get("admission") in ("unresolved", "unsupported"), mixed
    reasons = mixed.get("reason_codes") or []
    assert "CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED" in reasons, (
        f"mixed 共享单位候选必须带 CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED 标记"
        f"（G2 提案 tag），实际：{mixed!r}"
    )
    assert _check(selection, "no_pa").get("admission") == "eligible"
    assert selection.get("best") is None, (
        "mixed 未决竞争者参与 best（不得当 ineligible 删除）→ best=null"
    )


def test_002_rounding_branches_in_trace_per_annex_d() -> None:
    """取整按真实执行分支记 trace（Annex 005 §14 分支表；ADR-6）。

    规格锚点：Annex 005 §14 分支表（salary_floor_min_v1／pa_unit_floor_
    min_v1／pa_stc_comp_tp_v1；PA 宽减 ceil；salary rebate floor）。
    T3：salary 940 案例（PAM39 Q1）＋PA no_pa 薪俸子分支同额 940。
    """
    year = "2025_26"
    salary = _run(_pa_input(year, _subjects_single()))
    assert "salary_floor_min_v1" in _branches(salary), _branches(salary)
    ops = _ops(salary)
    assert "choose_floor_min" in ops and "rebate_min" in ops, ops

    pa = _run_pa(_pa_input(
        year,
        _subjects_single(),
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    pa_branches = _branches(pa)
    assert "pa_unit_floor_min_v1" in pa_branches, pa_branches
    assert "pa_stc_comp_tp_v1" in pa_branches, (
        f"no_pa 候选的薪俸子分支须按 CompTP 路径记录（仅真实 no_pa 薪俸子分支），"
        f"实际：{pa_branches!r}"
    )
    assert "ceil" in _ops(pa), "PA 宽减取整须记 ceil（pa_unit_floor_min_v1）"


def test_002_s41_1b_blocks_separate_pa_after_joint_salaries() -> None:
    """P3：任一方已按 s.10(3) 联合薪俸评税 → 单独 PA 候选被禁（s.41(1B)）；
    共同选择的 pa_joint 不受影响。

    规格锚点：Annex 005 §9.2 P3。
    P1 r1 纠错（复核 §五.5）：原 actual 正例事实不足（无 actual.pa 有效
    mode/date、history 未核）——按文档拆分为 hypothetical 变体（§4.10 假设
    选择语义，实际事实不变），并按 §9.2 P2 补参考日婚姻/分居 synthetic
    control（_s6_household(reference=…)）。
    """
    year = "2025_26"
    ref = "2026-06-01"
    common = {
        "household": _s6_household(year, reference=ref),
        "participants": _COUPLE_PARTICIPANTS,
        "subject_facts": {"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                          "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        "financials": {
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
    }

    # (a) 已联合薪俸评税 → pa_self 被禁（s.41(1B)），排除原因引用条文。
    blocked = _run_pa(_pa_input(
        year, _subjects_couple(),
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "actual": {"salary_joint_assessed": True},
                    "candidates": ["pa_self"], "selection": "best"},
        **common,
    ))
    selection = _selection(blocked)
    self_check = _check(selection, "pa_self")
    assert self_check.get("admission") == "ineligible", (
        f"s.41(1B)：已有联合薪俸评税 → 单独 PA 候选被禁，实际：{self_check!r}"
    )
    blob = json.dumps(self_check, ensure_ascii=False, default=str)
    assert any(n in blob for n in ("s41", "S41", "41(1B)", "1B")), (
        f"排除原因必须引用 s.41(1B)（计算标签），实际：{self_check!r}"
    )

    # (b) 对照：无联合薪俸评税 → pa_self 合法（P1 肢成立）。
    clean = _run_pa(_pa_input(
        year, _subjects_couple(),
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "candidates": ["pa_self"], "selection": "best"},
        **common,
    ))
    assert _check(_selection(clean), "pa_self").get("admission") == "eligible", (
        "无 s.10(3) 联合评税事实 → 单独 PA 不被禁"
    )

    # (c) s.41(1B) 但书「除非双方共同选择」：pa_joint（共同选择假设）合法。
    joint_ok = _run_pa(_pa_input(
        year, _subjects_couple(),
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "actual": {"salary_joint_assessed": True},
                    "candidates": ["pa_joint"], "selection": "best"},
        **common,
    ))
    assert _check(_selection(joint_ok), "pa_joint").get("admission") == "eligible", (
        "共同选择（pa_joint）不受 s.41(1B) 单独 PA 之禁约束"
    )

    # (d) no_pa 候选不因 s.41(1B) 被禁（该条只禁「单独 PA」；005 §9.2 P3）。
    no_pa_ok = _run_pa(_pa_input(
        year, _subjects_couple(),
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "actual": {"salary_joint_assessed": True},
                    "candidates": ["no_pa"], "selection": "best"},
        **common,
    ))
    assert _check(_selection(no_pa_ok), "no_pa").get("admission") == "eligible", (
        "s.41(1B) 只禁单独 PA；no_pa 候选不作单独 PA 处理"
    )


def test_002_married_not_auto_joint_pa() -> None:
    """P2/P4：pa_joint 不是隐式候选——仅当显式列入才比较；候选集合精确。

    规格锚点：Annex 005 §9.2 P4（no_pa/pa_joint 非隐式）＋§9.3（candidates
    显式、非空、无重复、不隐式添加）。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    selection = _selection(result)
    assert selection.get("requested") == ["no_pa", "pa_self"], (
        f"requested 必须恰为显式候选（married 不得隐式添加 pa_joint），"
        f"实际：{selection.get('requested')!r}"
    )
    assert [c.get("candidate_id") for c in selection.get("candidate_checks") or []] == [
        "no_pa", "pa_self"
    ], selection.get("candidate_checks")


# ---------------------------------------------------------------------------
# B3 单位覆盖（V006 §5.3）
# ---------------------------------------------------------------------------


def test_002_candidate_effective_salary_pa_matrix() -> None:
    """每候选有效选择/actors/单位覆盖：三候选各自合法、scenario 单位不重复。

    规格锚点：Annex 005 §9.3 表（salary_mode/pa_by_person/必须覆盖的单位）。
    构造：夫妻双方薪俸（240k/200k）、双方全来源、双方 P1 肢成立 →
    no_pa／pa_self／pa_joint 三候选均 eligible。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, reference="2026-06-01"),  # P1 r1：pa_joint 按参考日求值 P2（复核 §五.5）
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self", "pa_joint"], "selection": "best"},
    ))
    selection = _selection(result)
    for cid in ("no_pa", "pa_self", "pa_joint"):
        assert _check(selection, cid).get("admission") == "eligible", (
            f"候选 {cid} 应合法（P1/P2 肢成立），实际：{_check(selection, cid)!r}"
        )
    for s in _steps(result, "scenario"):
        unit_ids = s.get("unit_ids") or []
        assert len(unit_ids) == len(set(unit_ids)), (
            f"scenario {s.get('scenario_id')} 单位不得重复计入，实际：{unit_ids!r}"
        )
    assert result.get("best_scenario") is not None, (
        "全部候选合法且可算 → best 必须确定（非 null）"
    )


def test_002_actual_no_pa_joint_salary_not_silently_split() -> None:
    """actual no_pa＋joint salaries → 该候选 unsupported/unresolved，
    不静默拆成 separate。

    规格锚点：Annex 005 §9.3 表后注第 1 条。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, reference="2026-06-01"),  # P1 r1：pa_joint 按参考日求值 P2（复核 §五.5）
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True),
        },
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
                               "salary_joint_elected": True},
                    "candidates": ["no_pa"], "selection": "no_pa"},
    ))
    selection = _selection(result)
    check = _check(selection, "no_pa")
    assert check.get("admission") in ("unresolved", "unsupported"), (
        f"actual no_pa＋joint salaries 本阶段 unsupported（不静默拆 separate），"
        f"实际：{check!r}"
    )
    assert check.get("reason_codes"), "必须附原因（不得伪称已按 separate 计算）"
    assert selection.get("best") is None and result.get("best_scenario") is None

    # 扩展（completion-plan A；005 §4.10 salary_joint_assessed 行）：已联合
    # 薪俸评税（assessed=true）与 no_pa 的 separate 有效模式不一致，同样不得
    # 静默按 separate 接纳；当前静默 eligible → 目标 Red。
    assessed = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, reference="2026-06-01"),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True),
        },
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
                               "salary_joint_assessed": True},
                    "candidates": ["no_pa"], "selection": "no_pa"},
    ))
    check = _check(_selection(assessed), "no_pa")
    assert check.get("admission") in ("unresolved", "unsupported"), (
        f"salary_joint_assessed=true 与 no_pa 的 separate 模式不一致，"
        f"不得静默接纳，实际：{check!r}"
    )
    assert check.get("reason_codes"), "必须附原因（不得伪称已按 separate 计算）"


def test_002_pa_self_effective_election_changes_spouse_s29() -> None:
    """pa_self 的 s.29 判断须看到 self 已 individual PA：spouse 分支 A 不得
    继续以 actual.pa[self]=none 作依据。

    规格锚点：Annex 005 §9.3 表后注（pa_self 行）＋§9.1.1 行 4。
    T3：p1（self）NAI 0＋AI=false＋actual.pa p1=none；p2 NAI 400,000：
    no_pa 候选 → p2 经分支 A 得 married 264,000（base 264,000）；
    pa_self 候选 → p1 effective individual → p2 分支 A 失效 → base 132,000。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, reference="2026-06-01"),  # P1 r1：pa_joint 按参考日求值 P2（复核 §五.5）
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="0", mpf="0", ai=False),
            "p2": _fin(nai="400000", mpf="18000", ai=True),
        },
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "actual": {"pa": {"p1": {"mode": "none"}}},
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    no_pa_base = _unit_base(result, "salary:p2", "allowances", scenario_id="no_pa")
    pa_self_base = _unit_base(result, "salary:p2", "allowances", scenario_id="pa_self")
    assert no_pa_base == ["264000/1"], (
        f"no_pa：p1 未选 PA → p2 分支 A 成立 → married 264,000，实际：{no_pa_base!r}"
    )
    assert pa_self_base == ["132000/1"], (
        f"pa_self：p1 effective individual → p2 分支 A 失效 → 仅基本额，"
        f"实际：{pa_self_base!r}"
    )


def test_002_mixed_shared_unit_not_double_counted() -> None:
    """mixed 共享单位不重复计税：mixed 候选 unresolved 阻 best；全 PA 与
    全 no_pa 候选保留可算。

    规格锚点：Annex 005 §9.3（单位覆盖纪律＋mixed 行）。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, reference="2026-06-01"),  # P1 r1：pa_joint 按参考日求值 P2（复核 §五.5；编排者补漏 2026-10-15）
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=_couple_property_facts(year),
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self", "pa_joint"], "selection": "best"},
    ))
    selection = _selection(result)
    assert _check(selection, "pa_self").get("admission") in ("unresolved", "unsupported")
    assert _check(selection, "no_pa").get("admission") == "eligible"
    assert _check(selection, "pa_joint").get("admission") == "eligible", (
        "不因混合限制禁用 pa_joint（全 PA 候选保留）"
    )
    assert selection.get("best") is None, "mixed 未决竞争者 → best=null"
    for s in _steps(result, "scenario"):
        unit_ids = s.get("unit_ids") or []
        assert len(unit_ids) == len(set(unit_ids)), (
            f"同一物业/业务单位不得重复计入（{s.get('scenario_id')}）：{unit_ids!r}"
        )


def test_002_all_pa_all_no_pa_ordinary_partnership_preserved() -> None:
    """全 no_pa／全 PA／既有普通合伙份额契约不被 mixed 限制禁用。

    规格锚点：Annex 005 §9.3（不因混合限制禁用 pa_joint、完整 no_pa 或既有
    普通合伙份额契约）。
    构造：夫妻各持合伙 b1 半份（300,000×1/2）；两候选均可算。
    """
    year = "2025_26"
    financials = {
        "p1": _fin(nai="120000", mpf="6000", ai=True,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_business_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "business": "confirmed_in_scope"},
                   businesses=[_partnership_business()]),
        "p2": _fin(nai="120000", mpf="6000", ai=True,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_business_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "business": "confirmed_in_scope"},
                   businesses=[_partnership_business()]),
    }
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, reference="2026-06-01"),  # P1 r1：pa_joint 按参考日求值 P2（复核 §五.5；编排者补漏 2026-10-15）
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=financials,
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_joint"], "selection": "best"},
    ))
    selection = _selection(result)
    assert _check(selection, "no_pa").get("admission") == "eligible", (
        "完整 no_pa 候选保留（普通合伙按份额单位）"
    )
    assert _check(selection, "pa_joint").get("admission") == "eligible", (
        "全 PA 候选保留"
    )
    assert result.get("best_scenario") is not None


def test_002_actual_pa_entries_per_person_not_collapsed() -> None:
    """actual.pa 逐人条目不被压成单一状态：与候选一致 → eligible；
    与候选冲突（实际 joint vs 候选 pa_self）→ 该候选 unresolved。

    规格锚点：Annex 005 §9.2 P5＋§4.10（稀疏条目/规则 5/8）。
    P1 r1 纠错（复核 §五.5）：actual 候选的 P1 按实际 election date 求值
    （R4/A4）——居港 TF 须覆盖 2025-06-01（原 TF 在 2026-06-01＝错位）。
    """
    year = "2025_26"
    resident_on_election = [{"from": "2025-06-01", "to": "2025-06-02", "value": True}]
    common = {
        "household": _s6_household(year),
        "participants": _COUPLE_PARTICIPANTS,
        "subject_facts": {"p1": {"ordinarily_resident_hk": resident_on_election},
                          "p2": {"ordinarily_resident_hk": resident_on_election}},
        "financials": {
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
    }
    consistent = _run_pa(_pa_input(
        year, _subjects_couple(),
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual", "date": "2025-06-01"},
                                      "p2": {"mode": "none"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
        **common,
    ))
    assert _check(_selection(consistent), "pa_self").get("admission") == "eligible", (
        "actual.pa p1=individual（election date 2025-06-01 居港已证）与 pa_self "
        "一致且活动选择历史/评税事实显式 → 合法候选"
    )

    conflict = _run_pa(_pa_input(
        year, _subjects_couple(),
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "joint", "date": "2025-06-01"},
                                      "p2": {"mode": "joint", "date": "2025-06-01"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
        **common,
    ))
    check = _check(_selection(conflict), "pa_self")
    assert check.get("admission") == "unresolved", (
        f"实际 joint 选择 vs 候选 pa_self → 该候选 unresolved（P5，不回退默认），"
        f"实际：{check!r}"
    )


def test_002_effective_pa_entry_closed_map_no_scalar_collapse() -> None:
    """actual.pa 稀疏映射不塌缩：缺键＝缺席（不补 none/unknown）；活动消费者
    需要该键 → needs_input，补齐后照常判定。

    规格锚点：Annex 005 §4.10（稀疏 map＋规则 7）＋§9.3 salary_separate 行
    （只复制实际已提交条目、缺键保持缺键）。
    """
    year = "2025_26"
    # (a) 分支 A 需要配偶 p2 的 PA mode，而 actual.pa 只含 p1 条目 → 追问。
    missing = _run(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p1": {"mode": "none"}}}},  # p2 缺键
    ))
    assert missing["status"] == "needs_input", (
        f"分支 A 活动而 p2 PA 条目缺键 → needs_input（缺键≠none），"
        f"实际 {missing['status']!r}"
    )
    questions = _structured_questions(missing)
    blob = json.dumps(questions, ensure_ascii=False, default=str)
    assert any(n in blob for n in ("p2", "pa", "评税选择")), (
        f"追问必须定位配偶 PA 选择事实，实际：{questions!r}"
    )

    # (b) 补齐 p2 条目 → 分支 A 判定照常（married 264,000 归 p1）。
    filled = _run(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p1": {"mode": "none"},
                                      "p2": {"mode": "none"}}}},
    ))
    assert _unit_base(filled, "salary:p1", "allowances") == ["264000/1"], (
        f"补齐 p2=none 后分支 A 成立 → married 264,000，实际："
        f"{_unit_base(filled, 'salary:p1', 'allowances')!r}"
    )


# ---------------------------------------------------------------------------
# P1 r1 新 Red（复核 §六）：PA 单位、稀疏 map、P1/P2、候选集合
# ---------------------------------------------------------------------------


def _partnership_fin(*, profit: str = "300000") -> dict:
    """夫妻各持合伙 b1 半份＋全来源存在（no_pa 按批准评税单位）。"""
    return {
        "p1": _fin(nai="120000", mpf="6000", ai=True,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_business_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "business": "confirmed_in_scope"},
                   businesses=[_partnership_business(profit=profit)]),
        "p2": _fin(nai="120000", mpf="6000", ai=True,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_business_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "business": "confirmed_in_scope"},
                   businesses=[_partnership_business(profit=profit)]),
    }


def test_002_partnership_unit_one_assessment_one_rebate() -> None:
    """P1 r1 新 Red（复核 §六.2/R6）：no_pa 按 bID 原评税单位计税＋一次宽减；
    跨两档门槛仍按单位整体分段（不按人切片）。

    规格锚点：Annex 005 §9.3（先按原始 bID 识别单位再分配；no_pa 按批准
    评税单位）＋§4.6（两级利得，T1）＋附表 43 利得宽减行（T1）。
    T3（300,000 各 1/2）：单位整体 300,000≤2,000,000 → 7.5% → 22,500；
    一次宽减 min(22,500, 3,000)=3,000 → final 19,500（按人切片错误路径：
    各 150,000×7.5%＝11,250×2＝22,500 但两次宽减 6,000 → 16,500）。
    跨门槛（2,100,000 各 1/2）：2,000,000×7.5%＋100,000×15%＝165,000；
    宽减 3,000 → 162,000（按人错误路径：1,050,000×7.5%×2＝157,500）。
    """
    year = "2025_26"
    ref = "2026-06-01"

    result = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=ref),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=_partnership_fin(),
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "candidates": ["no_pa"], "selection": "no_pa"},
    ))
    scenario = next(
        s for s in _steps(result, "scenario") if s.get("scenario_id") == "no_pa"
    )
    assert scenario["amounts"]["before_reduction"] == "22500", scenario
    assert scenario["amounts"]["reduction"] == "3000", (
        f"同一 b1 单位只享受一次利得宽减（3,000，非按人×2＝6,000），"
        f"实际：{scenario['amounts']!r}"
    )
    assert scenario["amounts"]["final_after_reduction"] == "19500", scenario

    cross = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=ref),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=_partnership_fin(profit="2100000"),
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "candidates": ["no_pa"], "selection": "no_pa"},
    ))
    scenario = next(
        s for s in _steps(cross, "scenario") if s.get("scenario_id") == "no_pa"
    )
    assert scenario["amounts"]["before_reduction"] == "165000", (
        f"跨门槛按单位整体分段（2,000,000×7.5%＋100,000×15%），实际："
        f"{scenario['amounts']!r}"
    )
    assert scenario["amounts"]["final_after_reduction"] == "162000", scenario


def test_002_partnership_inconsistent_original_values_rejected() -> None:
    """P1 r1 新 Red（复核 §六.2/R6）：同 bID 共同原始金额不一致 → 拒绝。

    规格锚点：Annex 005 §9.3（按 bID 识别同一业务、检查共享原始值一致）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    financials = _partnership_fin()
    financials["p2"]["businesses"][0]["assessable_profit"] = "310000"  # 不一致
    try:
        _run_pa(_pa_input(
            year, _subjects_couple(),
            household=_s6_household(year),
            participants=_COUPLE_PARTICIPANTS,
            subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
            financials=financials,
            assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                        "candidates": ["no_pa"], "selection": "no_pa"},
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"同 b1 原始利润不一致须矛盾拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "同一 bID 的共同原始金额必须一致（005 §9.3），不一致不得计算"
        )


def _assert_bid_mutation_rejected(candidate_id: str) -> None:
    """bID 原值一致性单候选节点（W-D3-A/AQA-03）：先同输入合法控制
    （no_pa/pa_joint eligible；pa_self 的 mixed 共享 b1 单位 unresolved
    合法），再仅把 p2 的 b1 利润改为 310000 → E_INPUT_CONTRADICTORY。"""
    from app.core.errors import AppError

    year, reference = "2025_26", "2026-06-01"

    def run(financials: dict) -> dict:
        return _run_pa(_pa_input(
            year, _subjects_couple(),
            household=_s6_household(year, reference=reference),
            participants=_COUPLE_PARTICIPANTS,
            subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                           "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
            financials=financials,
            assessment={"basis": "hypothetical", "reference_date": reference,
                        "candidates": [candidate_id], "selection": candidate_id},
        ))

    control = _check(_selection(run(_partnership_fin())), candidate_id)
    if candidate_id == "pa_self":
        assert control.get("admission") == "unresolved" and (
            "CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED" in (control.get("reason_codes") or [])
        ), (
            f"合法控制：pa_self 的 mixed 共享 b1 单位须 unresolved/"
            f"CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED（005 §9.3），实际：{control!r}"
        )
    else:
        assert control.get("admission") == "eligible", (
            f"合法控制（无变异）：{candidate_id} 应可算，实际：{control!r}"
        )

    financials = _partnership_fin()
    financials["p2"]["businesses"][0]["assessable_profit"] = "310000"  # 仅 p2 变异
    try:
        run(financials)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"仅 p2 b1 原值变异：{candidate_id} 亦须在任何候选分配前"
            f"E_INPUT_CONTRADICTORY（005 §9.3），实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            f"同一 bID 的共同原始金额必须一致：{candidate_id} 下仅 p2 变异"
            "（310000）不得计算（005 §9.3）"
        )


def test_002_partnership_inconsistent_bid_rejected_no_pa() -> None:
    """bID 3 候选独立参数节点（W-D3-A/AQA-03）：no_pa 单候选。"""
    _assert_bid_mutation_rejected("no_pa")


def test_002_partnership_inconsistent_bid_rejected_pa_self() -> None:
    """bID 3 候选独立参数节点（W-D3-A/AQA-03）：pa_self 单候选
    （mixed 共享 b1 单位的合法控制＝unresolved，非 ineligible）。"""
    _assert_bid_mutation_rejected("pa_self")


def test_002_partnership_inconsistent_bid_rejected_pa_joint() -> None:
    """bID 3 候选独立参数节点（W-D3-A/AQA-03）：pa_joint 单候选。"""
    _assert_bid_mutation_rejected("pa_joint")


def test_002_effective_pa_map_sparse_active_key_only() -> None:
    """P1 r1 新 Red（复核 §六.3）：稀疏 map 正例——仅活动消费者（配偶 p2）
    的键存在、p1 键缺省不追问；p2 条目 mode=none 无 date（不补默认日期）。

    规格锚点：Annex 005 §4.10（稀疏 map：缺键＝缺席；none 禁止 date；
    规则 2 短路——不活动的 p1 键不产生追问）。
    T3：分支 A 成立 → married 264,000 归 p1（NCI<0 → final 0）。
    """
    year = "2025_26"
    result = _run(_pa_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p2": {"mode": "none"}}}},  # 仅活动键
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), (
        "仅活动消费者（p2）键存在 → 不追问 p1 的非活动 PA 条目"
    )
    assert _unit_base(result, "salary:p1", "allowances") == ["264000/1"], (
        f"分支 A 成立 → married 264,000，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def test_002_p1_age_and_temporary_residence_limbs() -> None:
    """P1 r1 新 Red（复核 §六.7/A4）：P1 居港肢 temporary 路径（严格大于
    180/合计 300）；当日通常居港缺省时按 presence 求值，不得回退任意区间。

    规格锚点：Annex 005 §9.2 P1（参考日 (age≥18 OR 双亲已亡) AND (当日通常
    居港 OR temporary：本 YA>180 天，或本 YA 与相邻任一 YA 合计>300 天；
    严格大于）。
    """
    year = "2025_26"
    assessment = {"basis": "hypothetical", "reference_date": "2026-06-01",
                  "candidates": ["no_pa", "pa_self"], "selection": "best"}

    def pa_with_presence(days_pairs):
        return _run_pa(_pa_input(
            year, _subjects_single(),
            subject_facts={},  # 无 ordinarily_resident_hk → 走 temporary 肢
            assessment=assessment,
            presence=[{"person_id": "p1", "year": yr, "days": days}
                      for yr, days in days_pairs],
        ))

    over = pa_with_presence([(year, 181)])
    check = _check(_selection(over), "pa_self")
    assert check.get("admission") == "eligible", (
        f"本 YA 在港 181>180 天 → temporary 肢成立 → pa_self 合法，实际：{check!r}"
    )

    cross = pa_with_presence([(year, 170), ("2024_25", 150)])
    check = _check(_selection(cross), "pa_self")
    assert check.get("admission") == "eligible", (
        f"170＋150＝320>300（相邻 YA 合计）→ temporary 肢成立，实际：{check!r}"
    )

    not_over = pa_with_presence([(year, 180)])
    check = _check(_selection(not_over), "pa_self")
    assert check.get("admission") == "unresolved", (
        f"180 不>180 → temporary false＋居港缺省 → P1 unknown → 未决，"
        f"实际：{check!r}"
    )

    not_cross = pa_with_presence([(year, 150), ("2024_25", 150)])
    check = _check(_selection(not_cross), "pa_self")
    assert check.get("admission") == "unresolved", (
        f"150＋150＝300 不>300 → temporary false → P1 unknown → 未决，"
        f"实际：{check!r}"
    )

    # P1 r2 定点修正（A 质量复核 #1）：居港显式 false＋本年 100＋上年 100，
    # 但**下一相邻 YA 日数缺失** → temporary OR 肢 unknown（false OR
    # unknown=unknown，005 §7 三态 OR）→ P1 未决＝正确控制（旧期望
    # ineligible 属 fixture 缺陷）。ineligible 反例见紧随的三年 100 节点。
    hk_false = _run_pa(_pa_input(
        year, _subjects_single(),
        subject_facts={"p1": {"ordinarily_resident_hk": [
            {"from": "2026-06-01", "to": "2026-06-02", "value": False}]}},
        assessment=assessment,
        presence=[{"person_id": "p1", "year": year, "days": 100},
                  {"person_id": "p1", "year": "2024_25", "days": 100}],
    ))
    check = _check(_selection(hk_false), "pa_self")
    assert check.get("admission") == "unresolved", (
        f"居港 false＋100/100 不达阈值但下一相邻 YA 未知 → temporary OR 肢 "
        f"unknown（false OR unknown=unknown）→ P1 未决，实际：{check!r}"
    )


def test_002_p1_minor_temporary_cannot_bypass_age() -> None:
    """A 质量复核 #1（拆分）：temporary 不能绕过年龄肢——未成年
    （both_dead 显式 false）＋在港 181>180 天 → 年龄肢 false → ineligible。

    独立节点：旧实现中该断言位于同函数首个失败之后＝未达（不可观测），
    拆分后本章可见其真实行为。规格锚点：Annex 005 §9.2 P1（两组括号并列，
    temporary 路径不能绕过年龄肢）。
    """
    year = "2025_26"
    minor_temp = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "2007-06-15"}],
        subject_facts={"p1": {"both_parents_dead": [
            {"from": "2025-06-01", "to": "2025-06-02", "value": False}]}},
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual",
                                              "date": "2025-06-01"}}},
                    "candidates": ["pa_self"], "selection": "pa_self"},
        presence=[{"person_id": "p1", "year": year, "days": 181}],
    ))
    check = _check(_selection(minor_temp), "pa_self")
    assert check.get("admission") == "ineligible", (
        f"181>180 天 temporary 不得绕过年龄肢（17 岁＋both_dead=false）→ "
        f"ineligible，实际：{check!r}"
    )


def test_002_p1_temporary_all_three_years_below_threshold_ineligible() -> None:
    """A 质量复核 #1：三个相邻 YA 日数齐备且均不达阈值（100/100/100：
    单年不>180、任一相邻对 200 不>300）＋参考日居港显式 false → 居港肢
    两路皆 false → P1 false → ineligible。

    对照上一节点（缺 next 年 → unknown）：三态 OR 不得用缺数据判 false。
    """
    year = "2025_26"
    all_low = _run_pa(_pa_input(
        year, _subjects_single(),
        subject_facts={"p1": {"ordinarily_resident_hk": [
            {"from": "2026-06-01", "to": "2026-06-02", "value": False}]}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
        presence=[{"person_id": "p1", "year": "2024_25", "days": 100},
                  {"person_id": "p1", "year": year, "days": 100},
                  {"person_id": "p1", "year": "2026_27", "days": 100}],
    ))
    check = _check(_selection(all_low), "pa_self")
    assert check.get("admission") == "ineligible", (
        f"三年均 100（各对 200≤300、单年 100≤180）且居港 false → 居港肢 "
        f"false 确定 → P1 ineligible，实际：{check!r}"
    )


def test_002_p1_age_boundary_and_both_dead_substitute() -> None:
    """P1 r1 新 Red（复核 §六.7/A4）：P1 年龄肢按候选有效日求值——
    actual 用 election date（年末年龄回退＝缺陷）。

    规格锚点：Annex 005 §9.2 P1＋§4.10 规则 5（active actual PA：日期取
    实际 election date）。
    fixture-audit #5：本节点只保留 (a) 满 18 正例与 (b) 未成年＋显式 false
    负例；missing/unknown/替代/缺生日拆为独立节点，防前失败遮蔽后断言。
    """
    year = "2025_26"
    election_day = [{"from": "2025-06-01", "to": "2025-06-02", "value": True}]
    # P1 r2 定点纠错（§三）：显式 false 单日区间（与 election 日对齐）。
    election_day_false = [{"from": "2025-06-01", "to": "2025-06-02", "value": False}]

    # (a) election 日恰满 18（birth 2007-06-01）→ 年龄肢成立。
    exact = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "2007-06-01"}],
        subject_facts={"p1": {"ordinarily_resident_hk": election_day}},
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual",
                                              "date": "2025-06-01"}}},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    assert _check(_selection(exact), "pa_self").get("admission") == "eligible", (
        "election 日（2025-06-01）恰满 18 岁 → P1 年龄肢成立"
    )

    # (b) election 日未满 18（birth 2007-06-15，当日 17 岁）且双亲已亡显式
    # false → 年龄肢不成立（P1 r2 定点纠错：单变量控制——本负例显式提交
    # both_parents_dead=false，使「17 岁→ineligible」只由年龄事实决定）。
    under = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "2007-06-15"}],
        subject_facts={"p1": {"ordinarily_resident_hk": election_day,
                              "both_parents_dead": election_day_false}},
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual",
                                              "date": "2025-06-01"}}},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    check = _check(_selection(under), "pa_self")
    assert check.get("admission") == "ineligible", (
        f"actual 候选按 election date（当日 17 岁）求值 → P1 年龄肢不成立"
        f"（不得回退年末年龄），实际：{check!r}"
    )


def test_002_p1_minor_missing_both_dead_is_unresolved() -> None:
    """P1 r2 新 Red（复核 §三/E4；fixture-audit #5 拆参数）：未满 18 且
    both_parents_dead 事实缺失 → 年龄肢 OR(age≥18, both_dead) unknown →
    P1 未决（缺失≠false，不得当 false 处理成 ineligible）。"""
    year = "2025_26"
    election_day = [{"from": "2025-06-01", "to": "2025-06-02", "value": True}]
    under_missing = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "2007-06-15"}],
        subject_facts={"p1": {"ordinarily_resident_hk": election_day}},
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual",
                                              "date": "2025-06-01"}}},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    check = _check(_selection(under_missing), "pa_self")
    assert check.get("admission") == "unresolved", (
        f"17 岁＋both_parents_dead 缺失 → OR 肢 unknown → P1 未决"
        f"（005 §6 缺失≠false），实际：{check!r}"
    )


def test_002_p1_minor_unknown_both_dead_is_unresolved() -> None:
    """未满 18 且 both_parents_dead 显式 unknown → 年龄肢 OR unknown →
    P1 未决（unknown 不得当 false）。"""
    year = "2025_26"
    election_day = [{"from": "2025-06-01", "to": "2025-06-02", "value": True}]
    under_unknown = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "2007-06-15"}],
        subject_facts={"p1": {
            "ordinarily_resident_hk": election_day,
            "both_parents_dead": [{"from": "2025-06-01", "to": "2025-06-02",
                                   "value": "unknown"}],
        }},
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual",
                                              "date": "2025-06-01"}}},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    check = _check(_selection(under_unknown), "pa_self")
    assert check.get("admission") == "unresolved", (
        f"17 岁＋both_parents_dead=unknown → OR 肢 unknown → P1 未决，"
        f"实际：{check!r}"
    )


def test_002_p1_missing_birth_both_dead_true_is_eligible() -> None:
    """P1 r2 新 Red（复核 §三/E4；fixture-audit #5 拆参数）：生日缺失＋
    both_parents_dead=true＋居港肢 true → 已证 both_dead 替代成立 →
    年龄肢 OR 成立 → eligible（缺生日不得阻止已证替代肢）。"""
    year = "2025_26"
    no_birth = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1"}],
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference(),
                              "both_parents_dead": _resident_at_reference()}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    check = _check(_selection(no_birth), "pa_self")
    assert check.get("admission") == "eligible", (
        f"生日缺失但 both_parents_dead=true（参考日）→ OR 年龄肢由已证"
        f"替代肢成立 → pa_self 合法（005 §9.2 P1），实际：{check!r}"
    )


def test_002_p1_minor_both_dead_true_substitute_is_eligible() -> None:
    """未满 18＋双亲已亡（参考日）→ 年龄肢替代成立（s.41 OR 肢）。"""
    year = "2025_26"
    orphan = _run_pa(_pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "2008-06-02"}],
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference(),
                              "both_parents_dead": _resident_at_reference()}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    check = _check(_selection(orphan), "pa_self")
    assert check.get("admission") == "eligible", (
        f"参考日未满 18 但双亲已亡 → 年龄肢替代成立（s.41 年龄肢 OR 肢），"
        f"实际：{check!r}"
    )


def test_002_p2_reference_date_marriage_coverage() -> None:
    """P1 r1 新 Red（复核 §六.7）：P2 婚姻/非分居按候选参考日求值——
    参考日无婚姻覆盖 → P2 unknown → pa_joint unresolved、best=null（已知
    no_pa scenario 保留）；对照补参考日一天区间后 eligible。

    规格锚点：Annex 005 §9.2 P2（以已核适用事实在该候选参考/许可选择日
    求值）＋§9.3（unknown 竞争者 → best=null）＋§7（参考日一天区间）。
    """
    year = "2025_26"
    ref = "2026-06-01"
    common = {
        "participants": _COUPLE_PARTICIPANTS,
        "subject_facts": {"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                          "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        "financials": {
            "p1": _fin(nai="240000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
        "assessment": {"basis": "hypothetical", "reference_date": ref,
                       "candidates": ["no_pa", "pa_joint"], "selection": "best"},
    }

    missing = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year),  # 仅 YA 窗口覆盖；参考日在窗外
        **{k: v for k, v in common.items() if k != "assessment"},
        assessment=common["assessment"],
    ))
    check = _check(_selection(missing), "pa_joint")
    assert check.get("admission") == "unresolved", (
        f"参考日（2026-06-01）无婚姻覆盖 → P2 unknown → pa_joint 未决，"
        f"实际：{check!r}"
    )
    assert missing.get("best_scenario") is None, (
        f"unknown 竞争者 → best=null，实际 {missing.get('best_scenario')!r}"
    )
    scenario_ids = [s.get("scenario_id") for s in _steps(missing, "scenario")]
    assert "no_pa" in scenario_ids, (
        f"已知 no_pa scenario 必须保留（不被未知竞争者清空），实际：{scenario_ids!r}"
    )

    covered = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=ref),
        **{k: v for k, v in common.items() if k != "assessment"},
        assessment=common["assessment"],
    ))
    assert _check(_selection(covered), "pa_joint").get("admission") == "eligible", (
        "补参考日一天区间（married＋not apart）后 P2 成立"
    )


def test_002_actual_pa_date_reference_conflict_rejected() -> None:
    """active actual PA 日期与 reference_date 同时提交且不同 → 报矛盾
    （不静默择一）；hypothetical 异日不触发规则 5（不发生非法转换）。

    规格锚点：Annex 005 §4.10 规则 5（仅 active actual PA 才执行 reference
    冲突检查）＋规则 6（hypothetical 取 reference_date，实际 PA 日期只用于
    检查已有选择/转换，不覆盖假设日期）。
    P1 r2 定点纠错（复核 §三）：负例 basis 由 hypothetical 改 actual（冲突
    检查的活动消费者）；另增 hypothetical 异日正例（E4）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    # (a) actual basis：active actual individual PA（date 2025-06-01）与
    # reference_date（2026-06-01）不同且同时提交 → E_INPUT_CONTRADICTORY。
    try:
        _run_pa(_pa_input(
            year, _subjects_single(),
            subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
            assessment={"basis": "actual",
                        "reference_date": "2026-06-01",
                        "actual": {"pa": {"p1": {"mode": "individual",
                                                  "date": "2025-06-01"}}},
                        "candidates": ["pa_self"], "selection": "pa_self"},
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"active actual election date≠reference_date 同时提交须矛盾拒绝，"
            f"实际 {exc.code!r}"
        )
        # P1 r2 定点勘误（fixture-audit #4）：加精确 reference_date 定位。
        assert exc.field == f"years.{year}.assessment.reference_date", exc.field
    else:
        raise AssertionError(
            "active actual PA 日期（2025-06-01）与 reference_date（2026-06-01）"
            "不同且同时提交：必须报矛盾（005 §4.10 规则 5），不得静默择一"
        )

    # (b) P1 r2 新 Red（复核 §三/E4；fixture-audit #4 定点勘误）：hypothetical
    # ＋已有实际 individual 选举（date 2025-06-01）与 reference_date
    # （2026-06-01）异日、模式无转换冲突 → 不报矛盾；hypothetical 按参考日
    # 求值、pa_self 合法可算。局部仅请求 pa_self（不并同请求 no_pa——已有
    # individual 实际选择下的 no_pa 属撤回转换，应另行未决而非本案目标）；
    # 局部补 history=ordinary / salary_joint_assessed=false 消除 s.41(1B)
    # 与选择历史噪声，只留「hypothetical 异日不冲突」目标。
    hypo = _run_pa(_pa_input(
        year, _subjects_single(),
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "actual": {"pa": {"p1": {"mode": "individual",
                                              "date": "2025-06-01"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    check = _check(_selection(hypo), "pa_self")
    assert check.get("admission") == "eligible", (
        f"hypothetical 异日（实际选举日≠参考日）不触发规则 5 冲突，"
        f"pa_self 按 reference_date 求值应合法（005 §4.10 规则 6），"
        f"实际：{check!r}"
    )


def test_002_joint_pa_bidirectional_spouse_dda() -> None:
    """P1 r1 新 Red（复核 §六.8）：joint PA 下双向 spouse DDA——夫妻各申索
    对方的伤残受养人额，均按 s.29(1A) 双方 married entitlement 成立。

    规格锚点：Annex 005 §9.1.1 行 5（pa_joint：spouse DDA 按 s.29(1A)
    对应双方 entitlement，不虚构 salary nominee）。
    T3：allowances=264,000＋2×75,000=414,000；NCI=500,000−36,000−414,000
    =50,000 → 累进 1,000 → 宽减 1,000 → final 0。
    """
    year = "2025_26"
    ref = "2026-06-01"
    result = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=ref),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={
            "p1": {"ordinarily_resident_hk": _resident_at_reference(),
                   "govt_disability_allowance": _tf(year, True)},
            "p2": {"ordinarily_resident_hk": _resident_at_reference(),
                   "govt_disability_allowance": _tf(year, True)},
        },
        financials={"p1": _fin(nai="300000", mpf="18000", ai=True,
                               has_income_assessable_under_ordinance=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True,
                               has_income_assessable_under_ordinance=True)},
        claims=[{"claim_id": "c1", "kind": "disabled_dependant",
                 "claimant_id": "p1", "subject_id": "p2"},
                {"claim_id": "c2", "kind": "disabled_dependant",
                 "claimant_id": "p2", "subject_id": "p1"}],
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "candidates": ["no_pa", "pa_joint"], "selection": "best"},
    ))
    for cid in ("c1", "c2"):
        decision = next(
            (d for d in _decisions(result)
             if d.get("claim_id") == cid and d.get("scenario_id") == "pa_joint"),
            None,
        )
        assert decision is not None and decision.get("grant") == "allowed", (
            f"joint PA 下双向 spouse DDA 均须成立（{cid}），实际：{decision!r}"
        )
        assert decision.get("amount") == "75000", decision
    assert _unit_base(result, "pa:joint", "allowances",
                      scenario_id="pa_joint") == ["414000/1"], (
        f"一份 married＋两份 DDA＝414,000，实际："
        f"{_unit_base(result, 'pa:joint', 'allowances', scenario_id='pa_joint')!r}"
    )


def test_002_endpoint_candidate_set_closed() -> None:
    """P1 r1 新 Red（复核 §一 A7）：候选集合按端点封闭——PA 请求只允许
    no_pa/pa_self/pa_joint，salary 请求只允许 salary_separate/salary_joint；
    非法候选名 → E_INPUT_EXTRA（不是 ineligible「优雅降级」）。

    规格锚点：Annex 005 §9.3（「salary 请求仅前两种、PA 请求仅后三种」）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    try:
        _run_pa(_pa_input(
            year, _subjects_single(),
            subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
            assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                        "candidates": ["no_pa", "salary_separate"],
                        "selection": "best"},
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_EXTRA", (
            f"PA 端点出现 salary 候选名须 E_INPUT_EXTRA，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "salary_separate 不属 PA 端点候选集合（005 §9.3），必须拒绝"
        )

    try:
        _run(_pa_input(
            year, _subjects_single(),
            assessment={"basis": "actual", "candidates": ["salary_separate",
                                                          "pa_self"],
                        "selection": "salary_separate"},
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_EXTRA", (
            f"salary 端点出现 PA 候选名须 E_INPUT_EXTRA，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "pa_self 不属 salary 端点候选集合（005 §9.3），必须拒绝"
        )


def test_002_pa_no_pa_comp_tp_trace_keeps_pre_floor_fraction() -> None:
    """E7：PA no_pa 的真实薪俸 CompTP floor trace 保存 floor 前 Exact。

    P1 r2 定点勘误（fixture-audit #3）：actual no_pa 候选须有实际 PA 模式
    （p1=none）与所需选择控制，令本例失败只落在 floor trace 目标上。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year, _subjects_single(),
        assessment={"basis": "actual", "candidates": ["no_pa"], "selection": "no_pa",
                    "actual": {"pa": {"p1": {"mode": "none"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False}},
        financials={"p1": _fin(nai="240008.50", mpf="9000")},
    ))
    assert "pa_stc_comp_tp_v1" in _branches(result), _branches(result)
    operations = [
        op
        for detail in _steps(result, "assessment")
        if detail.get("unit_id") == "salary:p1"
        for op in detail.get("operations") or []
        if isinstance(op, dict) and op.get("op") == "floor"
    ]
    assert any(op.get("inputs") == ["394051/100"] for op in operations), (
        f"真实 no_pa CompTP floor 应记录 raw 394051/100，而非 floor 后值：{operations!r}"
    )
    assert any(
        op.get("inputs") == ["394051/100"] and op.get("output") == "3940/1"
        for op in operations
    ), (
        f"raw 394051/100 的 floor 输出须为 3940/1（外层单次 floor）：{operations!r}"
    )


def _pself_married_allowance_input() -> dict:
    """§9.1.1 pa_self＋spouse no_pa 同合法构造器：p1 500,000/18,000、
    p2 0/false、参考日婚姻覆盖＋活动 PA 前置（金额/basis/decision 独立节点共用）。"""
    year, reference = "2025_26", "2026-06-01"
    return _pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=reference),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="500000", mpf="18000", ai=True),
            "p2": _fin(nai="0", mpf="0", ai=False),
        },
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "actual": {"pa": {"p1": {"mode": "none"},
                                      "p2": {"mode": "none"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    )


def test_002_pa_self_uses_effective_married_allowance_amount() -> None:
    """E6（W-D3-A/AQA-02 拆分-金额节点）：PA pa_self 有效 married 基础额，
    独立手算 before/reduction/final＝19,060/3,000/16,060。

    P1 r2 定点勘误（fixture-audit #1）：局部补活动 PA 前置（双方 actual.pa
    none、history=ordinary、salary_joint_assessed=false），排除既有转换噪声；
    非全局默认。本节点只读 amounts（不读 trace/decision）：basis/去重见
    `test_002_pa_self_married_basis_single_unit_no_duplicate`，married
    decision 见 `test_002_pa_self_married_decision_grant_attribution_unit`。

    D4-A/AQA-02：删除原先在金额断言前读取 selection/trace 的 admission 先决
    （ora-16：『金额节点只读 amounts』表述不准确）——admission 合法控制由
    basis 节点承担，本节点真正只读 amounts。
    """
    result = _run_pa(_pself_married_allowance_input())
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "19060", (
        f"有效 married 264,000 基础：before_reduction=19,060（逐项观测），"
        f"实际：{amounts!r}"
    )
    assert amounts["reduction"] == "3000", (
        f"2025/26 宽减 3,000（逐项观测），实际：{amounts!r}"
    )
    assert amounts["final_after_reduction"] == "16060", (
        f"19,060−3,000=16,060（逐项观测），实际：{amounts!r}"
    )


def test_002_pa_self_married_basis_single_unit_no_duplicate() -> None:
    """W-D3-A/AQA-02 拆分-basis/去重节点：pa:p1 恰一份 264,000；salary:p2
    不重复基础额。只读 assessment trace，不以前置金额断言阻断。"""
    result = _run_pa(_pself_married_allowance_input())
    check = _check(_selection(result), "pa_self")
    assert check.get("admission") == "eligible", check
    pa_bases = _unit_base(result, "pa:p1", "allowances", scenario_id="pa_self")
    assert pa_bases == ["264000/1"], (
        f"pa_self 的 pa:p1 单位须记一份 264,000 基础额，实际：{pa_bases!r}"
    )
    married_264 = [
        (detail.get("unit_id"), b.get("value"))
        for detail in _steps(result, "assessment")
        for b in detail.get("bases") or []
        if b.get("name") == "allowances" and b.get("value") == "264000/1"
    ]
    assert len(married_264) == 1, (
        f"一份 264,000 只出现一次（跨人去重），实际：{married_264!r}"
    )
    p2_bases = _unit_base(result, "salary:p2", "allowances", scenario_id="pa_self")
    assert p2_bases == ["0/1"], (
        f"salary:p2 不得重复基础额，实际：{p2_bases!r}"
    )


def test_002_pa_self_married_decision_grant_attribution_unit() -> None:
    """W-D3-A/AQA-02 拆分-decision 节点：PA married decision 须
    grant=allowed、amount=264000、归属 p1、scenario=pa_self（salary decision
    不作 PA 证明）；先证明匹配集合非空，再与 pa:p1 实际单位基础额对账。"""
    result = _run_pa(_pself_married_allowance_input())
    married_decisions = [
        d for d in _decisions(result)
        if d.get("item_kind") == "married" and d.get("scenario_id") == "pa_self"
    ]
    assert married_decisions, (
        f"pa_self 候选须有 married decision（005 §14 decision 形状），实际："
        f"{_decisions(result)!r}"
    )
    decision = married_decisions[0]
    assert decision.get("grant") == "allowed", decision
    assert decision.get("amount") == "264000", decision
    assert decision.get("claimant_id") == "p1", (
        f"married 264,000 归属 p1，实际：{decision!r}"
    )
    assert _unit_base(result, "pa:p1", "allowances",
                      scenario_id="pa_self") == ["264000/1"], (
        f"decision 金额须与 pa:p1 实际单位基础额对账，实际："
        f"{_unit_base(result, 'pa:p1', 'allowances', scenario_id='pa_self')!r}"
    )


def _erce_pself_input(*, eligibility: bool = True, residential_received: bool = True) -> dict:
    """ERCE 合法构造器（活动 PA 前置＋payment/deduction 同步 requested
    120,000，cap 100,000）：eligibility/source 事实单变量供独立节点复用。"""
    year, reference = "2025_26", "2026-06-01"
    start, end = _window(year)
    return _pa_input(
        year, _subjects_single([("d1", "1950-05-01")]),
        subject_facts={
            "p1": {"ordinarily_resident_hk": _resident_at_reference()},
            "d1": {"ordinarily_resident_hk": _tf(year, False),
                   "govt_disability_allowance": _tf(year, True)},
        },
        relations=[_relation(year, "r1", "p1", "d1", kind="parent")],
        care_payments=[{
            "payment_id": "e1", "payer_id": "p1", "subject_id": "d1",
            "paid_on": "2025-06-15", "amount": "120000",
            "care_from": start, "care_to": end,
            "residential_care_received": residential_received,
            "paid_to_home_or_agent": True,
            "home_basis": "cap459_licence",
            "home_status_in_force": [{"from": start, "to": end, "value": True}],
        }],
        financials={"p1": _fin(nai="600000", mpf="18000", deduction_items=[{
            "item_type": "elder_residential_care", "amount": "120000",
            "payment_ids": ["e1"], "eligibility": {"value": eligibility},
            "subject_id": "d1",
        }])},
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "actual": {"pa": {"p1": {"mode": "none"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    )


def test_002_pa_reduced_income_uses_only_allowed_erce() -> None:
    """E6（W-D3-A/AQA-02 拆分-金额节点）：PA reduced income 金额不重用
    requested——allowed ERCE 100,000（cap 后）→ 41,500−3,000=38,500。

    P1 r2 定点勘误（fixture-audit #2）：付款与 deduction item 同步
    requested=120,000，官方 cap 100,000 不变 → allowed=100,000（非 requested）；
    本节点只读 amounts（reduced basis 见紧随节点；trace 见 deduction decision
    节点），不以前置 trace 断言阻断。若错误地用 requested 120,000：
    reduced=462,000、final=35,100（本用例可区分）。
    """
    result = _run_pa(_erce_pself_input())
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "41500", (
        f"600,000−18,000−allowed ERCE 100,000＝reduced 482,000 → "
        f"482,000−132,000=350,000 → 41,500（逐项观测），实际：{amounts!r}"
    )
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "38500", (
        f"41,500−3,000=38,500（逐项观测），实际：{amounts!r}"
    )


def test_002_pa_reduced_income_basis_allowed_erce_482000() -> None:
    """W-D3-A/AQA-02 拆分-reduced basis 节点（独立于金额）：allowed ERCE
    100,000（cap 后）须记入 trace 的 reduced_income=482,000；只读 bases。"""
    result = _run_pa(_erce_pself_input())
    reduced_bases = [
        b.get("value")
        for step in _steps(result, "assessment")
        for b in step.get("bases") or []
        if b.get("name") == "reduced_income"
    ]
    assert "482000/1" in reduced_bases, (
        f"600,000−18,000−allowed ERCE 100,000＝reduced 482,000 须记入 trace，"
        f"实际：{reduced_bases!r}"
    )


def test_002_pa_erce_deduction_decision_records_allowed_amount() -> None:
    """E7 trace：ERCE 扣除 decision 记录 allowed=100,000（cap 后），保留于 steps。

    P1 r2 定点勘误（fixture-audit #2）：金额与 trace 拆开独立断言；本例单测
    扣除 decision 的 allowed 金额（requested 120,000 / cap 100,000）。
    """
    year, reference = "2025_26", "2026-06-01"
    start, end = _window(year)
    result = _run_pa(_pa_input(
        year, _subjects_single([("d1", "1950-05-01")]),
        subject_facts={
            "p1": {"ordinarily_resident_hk": _resident_at_reference()},
            "d1": {"ordinarily_resident_hk": _tf(year, False),
                   "govt_disability_allowance": _tf(year, True)},
        },
        relations=[_relation(year, "r1", "p1", "d1", kind="parent")],
        care_payments=[{
            "payment_id": "e1", "payer_id": "p1", "subject_id": "d1",
            "paid_on": "2025-06-15", "amount": "120000",
            "care_from": start, "care_to": end,
            "residential_care_received": True, "paid_to_home_or_agent": True,
            "home_basis": "cap459_licence",
            "home_status_in_force": [{"from": start, "to": end, "value": True}],
        }],
        financials={"p1": _fin(nai="600000", mpf="18000", deduction_items=[{
            "item_type": "elder_residential_care", "amount": "120000",
            "payment_ids": ["e1"], "eligibility": {"value": True}, "subject_id": "d1",
        }])},
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "actual": {"pa": {"p1": {"mode": "none"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    decisions = [d for d in _decisions(result)
                 if d.get("subject_id") == "d1" and "elder_residential_care" in
                 json.dumps(d, ensure_ascii=False, default=str)]
    assert decisions, (
        f"ERCE 扣除须有 decision 条目（005 §5.1/§14），实际：{_decisions(result)!r}"
    )
    assert any(d.get("grant") == "allowed" and d.get("amount") == "100000"
               for d in decisions), (
        f"扣除 decision 金额＝cap 后 allowed 100,000（非 requested 120,000），"
        f"实际：{decisions!r}"
    )


def test_002_pa_erce_excluded_eligibility_false_amounts() -> None:
    """E6 对照（W-D3-A/AQA-02；D4-A 拆分-金额节点）：ERCE eligibility=false
    → excluded 不请求/不求解 → before 58,500、宽减 3,000、final 55,500
    （正确值＝控制，不记金额缺陷）；只读 amounts（不读 trace/decision）。

    与 allowed 路径同事实单变量 mutation（eligibility true→false），区分
    「扣 allowed」与「根本不扣」两条路径（005 §6.2 requested≠allowed）。
    """
    result = _run_pa(_erce_pself_input(eligibility=False))
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "58500", (
        f"excluded 不扣：before_reduction=58,500（逐项观测），实际：{amounts!r}"
    )
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "55500", (
        f"58,500−3,000=55,500（正确 55500 为控制），实际：{amounts!r}"
    )


def test_002_pa_erce_excluded_reduced_income_basis_582000() -> None:
    """W-D3-A/AQA-02；D4-A 拆分-reduced basis 节点（独立于金额/decision）：
    excluded 不扣 → reduced_income=582,000 须记入 trace bases；只读 bases，
    不以前置金额断言阻断。"""
    result = _run_pa(_erce_pself_input(eligibility=False))
    reduced_bases = [
        b.get("value")
        for step in _steps(result, "assessment")
        for b in step.get("bases") or []
        if b.get("name") == "reduced_income"
    ]
    assert "582000/1" in reduced_bases, (
        f"excluded 的 reduced＝582,000 须记入 trace，实际：{reduced_bases!r}"
    )


def test_002_pa_erce_excluded_decision_not_requested() -> None:
    """W-D3-A/AQA-02；D4-A 拆分-decision 节点（独立于金额/basis）：excluded
    可无 decision（不请求/不求解），空集合法；存在时逐条核 not_requested
    （非 ineligible），不用可空真的 `all()` 冒充「已存在某类 decision」的
    证据（005 §5.1/§14）。"""
    result = _run_pa(_erce_pself_input(eligibility=False))
    erce_decisions = [
        d for d in _decisions(result)
        if "elder_residential_care" in json.dumps(d, ensure_ascii=False, default=str)
    ]
    for decision in erce_decisions:
        assert decision.get("grant") == "not_requested", (
            f"excluded 的 ERCE decision 若存在须记 not_requested（非 ineligible），"
            f"实际：{decision!r}"
        )


def test_002_pa_erce_raw_ineligible_amounts() -> None:
    """E6 对照（A 质量复核 #2；W-D3-A/AQA-02；D4-A 拆分-金额节点）：
    eligibility=true 仅确定 requested；原始院舍事实不成立
    （residential_care_received=false）→ raw 资格不成立 → allowed 0、
    before 58,500、final 55,500；只读 amounts（005 §5.1）。"""
    result = _run_pa(_erce_pself_input(residential_received=False))
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "58500", (
        f"raw-ineligible 不扣：before_reduction=58,500（逐项观测），实际："
        f"{amounts!r}"
    )
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "55500", (
        f"58,500−3,000=55,500（正确 55500 为控制），实际：{amounts!r}"
    )


def test_002_pa_erce_raw_ineligible_reduced_income_basis_582000() -> None:
    """W-D3-A/AQA-02；D4-A 拆分-reduced basis 节点（独立于金额/decision）：
    raw-ineligible 的 reduced＝582,000 须记入 trace bases；只读 bases。"""
    result = _run_pa(_erce_pself_input(residential_received=False))
    reduced_bases = [
        b.get("value")
        for step in _steps(result, "assessment")
        for b in step.get("bases") or []
        if b.get("name") == "reduced_income"
    ]
    assert "582000/1" in reduced_bases, (
        f"raw-ineligible 的 reduced＝582,000 须记入 trace，实际：{reduced_bases!r}"
    )


def test_002_pa_erce_raw_ineligible_decision_ineligible() -> None:
    """W-D3-A/AQA-02；D4-A 拆分-decision 节点（独立于金额/basis）：
    requested（原始资格不成立）须留下 decision 记录并记 ineligible（与
    excluded/not_requested 区分，005 §5.1 eligibility=true 不能绕过 raw
    事实）；先证明匹配集合非空再核语义。"""
    result = _run_pa(_erce_pself_input(residential_received=False))
    erce_decisions = [
        d for d in _decisions(result)
        if "elder_residential_care" in json.dumps(d, ensure_ascii=False, default=str)
    ]
    assert erce_decisions, (
        f"requested（原始资格不成立）须留下 decision 记录（005 §5.1/§14），"
        f"实际：{_decisions(result)!r}"
    )
    assert any(d.get("grant") == "ineligible" for d in erce_decisions), (
        f"requested＋raw 资格不成立须记 ineligible（区分 excluded 的 "
        f"not_requested），实际：{erce_decisions!r}"
    )


# ---------------------------------------------------------------------------
# P1 r2 定点补测（completion-plan A）：七行依赖边界／单位前端一致性／
# 结果-下一年组件与 trace 封闭（仍为 Red 或既有行为控制；非 Green 授权）
# ---------------------------------------------------------------------------


def test_002_pa_no_pa_only_does_not_require_p1_or_reference() -> None:
    """§4.10 规则 3：PA 只请求 no_pa 时不评估 P1、不要求 reference_date——
    无居港/参考日事实仍照常计算，且不得产生追问（对照控制）。"""
    year = "2025_26"
    result = _run_pa(_pa_input(
        year, _subjects_single(),
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), (
        f"no_pa-only 不活动 P1/reference：不得追问，实际：{result.get('questions')!r}"
    )
    assert _check(_selection(result), "no_pa").get("admission") == "eligible"
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]


def test_002_pa_actual_none_mode_cannot_carry_date() -> None:
    """§4.10 规则 7：`none` 禁止 date；显式违反 → E_INPUT_CONTRADICTORY
    并精确落在该 actual.pa 条目（对照控制）。"""
    from app.core.errors import AppError

    year = "2025_26"
    try:
        _run_pa(_pa_input(
            year, _subjects_single(),
            assessment={"basis": "actual", "candidates": ["no_pa"],
                        "selection": "no_pa",
                        "actual": {"pa": {"p1": {"mode": "none",
                                                  "date": "2025-06-01"}}}},
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
        assert exc.field == f"years.{year}.assessment.actual.pa.p1", exc.field
    else:
        raise AssertionError("none 模式禁止 date（005 §4.10 规则 7），必须拒绝")


def test_002_pa_actual_no_pa_unknown_mode_not_none() -> None:
    """§4.10 规则 3/7：actual no_pa 的 PA 模式 unknown 只阻相关候选，
    不得当 none 接纳；单候选 unknown → 候选 unresolved、结果 needs_input
    （当前静默 eligible → 目标 Red）。"""
    year = "2025_26"
    result = _run_pa(_pa_input(
        year, _subjects_single(),
        assessment={"basis": "actual", "candidates": ["no_pa"], "selection": "no_pa",
                    "actual": {"pa": {"p1": {"mode": "unknown"}}}},
    ))
    check = _check(_selection(result), "no_pa")
    assert check.get("admission") == "unresolved", (
        f"actual PA 模式 unknown 与 no_pa 一致性别未决，不得当 none 接纳，"
        f"实际：{check!r}"
    )
    assert result["status"] == "needs_input", result["status"]


def _election_day() -> list[dict]:
    """actual joint 选择日一天区间（2025-06-01 居港 true）。"""
    return [{"from": "2025-06-01", "to": "2025-06-02", "value": True}]


def _joint_actual_pa_input(
    year: str,
    *,
    household: dict,
    actual_pa: dict,
    resident: list[dict] | None = None,
) -> dict:
    """CF4/依赖矩阵公用合法基线：仅 household 与 actual.pa 由调用方给出。

    夫妻双方薪俸＋双方 P1 肢（election 日居港 TF）、actual joint 配对、
    history=ordinary 与 salary_joint_assessed=false 显式（隔离活动依赖），
    候选仅 pa_joint、selection=pa_joint（不隐式加 no_pa）。
    """
    subject_facts = {}
    if resident is not None:
        subject_facts = {"p1": {"ordinarily_resident_hk": resident},
                         "p2": {"ordinarily_resident_hk": resident}}
    return _pa_input(
        year, _subjects_couple(),
        household=household,
        participants=_COUPLE_PARTICIPANTS,
        subject_facts=subject_facts,
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
        assessment={"basis": "actual",
                    "actual": {"pa": actual_pa,
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_joint"], "selection": "pa_joint"},
    )


def _question_located(
    result: Mapping, year: str, *field_needles: str, subject_id: str | None = None
) -> bool:
    """非抛出式 `_question_for`：聚合观测用。"""
    try:
        _question_for(result, year, *field_needles, subject_id=subject_id)
        return True
    except AssertionError:
        return False


def test_002_pa_joint_actual_control_ordinary_eligible() -> None:
    """CF4 控制（A 质量复核 #7）：合法 actual joint ordinary——选择日
    married＋not apart、双方 P1 肢、history/assessed 显式 → pa_joint
    eligible。后续反证参数仅在选择日婚姻/分居事实上变化（005 §9.2 P5 段）。"""
    year = "2025_26"
    result = _run_pa(_joint_actual_pa_input(
        year,
        household=_s6_household(year),
        actual_pa={"p1": {"mode": "joint", "date": "2025-06-01"},
                   "p2": {"mode": "joint", "date": "2025-06-01"}},
        resident=_election_day(),
    ))
    check = _check(_selection(result), "pa_joint")
    assert check.get("admission") == "eligible", (
        f"控制：选择日 married＋not apart 且活动依赖显式 → pa_joint 合法，"
        f"实际：{check!r}"
    )


def test_002_pa_joint_actual_uses_election_date_not_ya_ordinary_marriage() -> None:
    """CF4（P5 窄澄清，人类已批准）：actual joint 选择日已知未婚（P2 已知
    反证）→ **仅相依的 joint 候选** unresolved/manual＋原因定位，
    best=null、无确定 joint 税（无 pa_joint scenario）；不整份
    E_INPUT_CONTRADICTORY、不宣称政府选择无效、不回退默认（005 §9.2 P5
    段；F005259/596/599）。控制＝合法 ordinary（见上一节点），本例仅把
    选择日婚姻事实改为已知未婚。"""
    year = "2025_26"
    w_from, w_to = _window(year)
    household = {
        "marriage": [
            {"from": w_from, "to": "2025-06-02", "value": "unmarried"},
            {"from": "2025-06-02", "to": w_to, "value": "married", "spouse_id": "p2"},
        ],
        "living_apart": [{"from": w_from, "to": w_to, "value": False}],
        "child_nomination": {"person_id": None, "status": "none"},
    }
    result = _run_pa(_joint_actual_pa_input(
        year,
        household=household,
        actual_pa={"p1": {"mode": "joint", "date": "2025-06-01"},
                   "p2": {"mode": "joint", "date": "2025-06-01"}},
        resident=_election_day(),
    ))
    selection = _selection(result)
    check = _check(selection, "pa_joint")
    scenario_ids = [s.get("scenario_id") for s in _steps(result, "scenario")]
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "best": selection.get("best"),
        "pa_joint_scenario": "pa_joint" in scenario_ids,
        "question": _question_located(result, year, "household.marriage"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "best": None,
        "pa_joint_scenario": False,
        "question": True,
    }, (
        f"CF4 未婚反证：仅相依 joint 候选 unresolved/manual＋原因定位、"
        f"best=null、无确定 joint 税（逐项观测）：{observed!r}"
    )
    # W-D3-A/AQA-07：六金额键齐全且全 null；无任何确定 scenario（不只无
    # pa_joint）；requested/candidate_checks 不得隐式添加 no_pa 或其它候选。
    _assert_amounts_all_null(result)
    assert scenario_ids == [], (
        f"无确定 joint 税：不得留下任何确定 scenario，实际：{scenario_ids!r}"
    )
    assert selection.get("requested") == ["pa_joint"], (
        f"requested 必须恰为显式候选（不得隐式加 no_pa），实际："
        f"{selection.get('requested')!r}"
    )
    assert [c.get("candidate_id") for c in selection.get("candidate_checks") or []] == [
        "pa_joint"
    ], selection.get("candidate_checks")


def test_002_pa_joint_actual_election_day_apart_unresolved() -> None:
    """CF4（P5 窄澄清）第二参数：actual joint 选择日 known living_apart=true
    （married 但已知分居，P2 已知反证）→ 仅相依 joint 候选
    unresolved/manual＋living_apart 原因定位、best=null、无 pa_joint
    scenario；不整份 E_INPUT_CONTRADICTORY（005 §9.2 P5 段）。"""
    year = "2025_26"
    w_from, w_to = _window(year)
    household = {
        "marriage": [{"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}],
        "living_apart": [
            {"from": w_from, "to": "2025-06-01", "value": False},
            {"from": "2025-06-01", "to": "2025-06-02", "value": True},
            {"from": "2025-06-02", "to": w_to, "value": False},
        ],
        "child_nomination": {"person_id": None, "status": "none"},
    }
    result = _run_pa(_joint_actual_pa_input(
        year,
        household=household,
        actual_pa={"p1": {"mode": "joint", "date": "2025-06-01"},
                   "p2": {"mode": "joint", "date": "2025-06-01"}},
        resident=_election_day(),
    ))
    selection = _selection(result)
    check = _check(selection, "pa_joint")
    scenario_ids = [s.get("scenario_id") for s in _steps(result, "scenario")]
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "best": selection.get("best"),
        "pa_joint_scenario": "pa_joint" in scenario_ids,
        "question": _question_located(result, year, "household.living_apart"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "best": None,
        "pa_joint_scenario": False,
        "question": True,
    }, (
        f"CF4 分居反证：仅相依 joint 候选 unresolved/manual＋分居事实定位、"
        f"best=null、无确定 joint 税（逐项观测）：{observed!r}"
    )
    # W-D3-A/AQA-07：六金额键齐全且全 null；无任何确定 scenario（不只无
    # pa_joint）；requested/candidate_checks 不得隐式添加 no_pa 或其它候选。
    _assert_amounts_all_null(result)
    assert scenario_ids == [], (
        f"无确定 joint 税：不得留下任何确定 scenario，实际：{scenario_ids!r}"
    )
    assert selection.get("requested") == ["pa_joint"], (
        f"requested 必须恰为显式候选（不得隐式加 no_pa），实际："
        f"{selection.get('requested')!r}"
    )
    assert [c.get("candidate_id") for c in selection.get("candidate_checks") or []] == [
        "pa_joint"
    ], selection.get("candidate_checks")


def test_002_pa_actual_joint_p2_entry_missing_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5）：actual joint 配对信息缺失（p1 joint 已提
    日期、p2 条目缺失）→ 需补事实非矛盾（005 §4.10 规则 7）→ pa_joint
    unresolved＋needs_input＋定位，无 pa_joint scenario。"""
    year = "2025_26"
    result = _run_pa(_joint_actual_pa_input(
        year,
        household=_s6_household(year),
        actual_pa={"p1": {"mode": "joint", "date": "2025-06-01"}},
        resident=_election_day(),
    ))
    selection = _selection(result)
    check = _check(selection, "pa_joint")
    scenario_ids = [s.get("scenario_id") for s in _steps(result, "scenario")]
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "best": selection.get("best"),
        "pa_joint_scenario": "pa_joint" in scenario_ids,
        "question": _question_located(result, year, "actual.pa", "p2"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "best": None,
        "pa_joint_scenario": False,
        "question": True,
    }, (
        f"p2 配对条目缺失＝需补事实（非矛盾）→ 候选未决＋定位，逐项观测："
        f"{observed!r}"
    )


def test_002_pa_actual_joint_p2_date_missing_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5）：actual joint 配对日期缺失（p2 mode=joint
    无 date，日期消费者活动）→ 需补事实非矛盾 → pa_joint unresolved＋
    needs_input＋定位（005 §4.10 规则 7）。"""
    year = "2025_26"
    result = _run_pa(_joint_actual_pa_input(
        year,
        household=_s6_household(year),
        actual_pa={"p1": {"mode": "joint", "date": "2025-06-01"},
                   "p2": {"mode": "joint"}},
        resident=_election_day(),
    ))
    selection = _selection(result)
    check = _check(selection, "pa_joint")
    scenario_ids = [s.get("scenario_id") for s in _steps(result, "scenario")]
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "best": selection.get("best"),
        "pa_joint_scenario": "pa_joint" in scenario_ids,
        "question": _question_located(result, year, "actual.pa", "p2"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "best": None,
        "pa_joint_scenario": False,
        "question": True,
    }, (
        f"joint 配对日期缺失（日期消费者活动）＝需补事实 → 候选未决＋定位，"
        f"逐项观测：{observed!r}"
    )


def test_002_pa_actual_self_missing_election_date_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5）：active actual pa_self 自身 election date
    缺失（日期消费者活动：选择日/居港/P1 求值）→ pa_self unresolved＋
    needs_input＋定位（005 §4.10 规则 7）。"""
    year = "2025_26"
    result = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _election_day()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="0", mpf="0", ai=False),
        },
        assessment={"basis": "actual",
                    "actual": {"pa": {"p1": {"mode": "individual"},
                                      "p2": {"mode": "none"}},
                               "history": "ordinary",
                               "salary_joint_assessed": False},
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    selection = _selection(result)
    check = _check(selection, "pa_self")
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "best": selection.get("best"),
        "question": _question_located(result, year, "actual.pa", "p1"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "best": None,
        "question": True,
    }, (
        f"自身 election date 缺失（日期消费者活动）＝需补事实 → 候选未决＋"
        f"定位，逐项观测：{observed!r}"
    )


def test_002_pa_actual_no_pa_mode_missing_not_none() -> None:
    """依赖矩阵（A 质量复核 #5；§4.10 规则 3/7）：actual no_pa 候选的 PA
    模式**缺失**（稀疏 map 无 p1 键）≠ none → 候选 unresolved、结果
    needs_input＋定位。与既有 unknown 变体各自独立（不复合 actual={}）。"""
    year = "2025_26"
    result = _run_pa(_pa_input(
        year, _subjects_single(),
        assessment={"basis": "actual", "candidates": ["no_pa"], "selection": "no_pa",
                    "actual": {"pa": {}}},
    ))
    check = _check(_selection(result), "no_pa")
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "question": _question_located(result, year, "actual.pa", "p1"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "question": True,
    }, (
        f"actual.pa 稀疏 map 缺 p1 键（缺键≠none）→ no_pa 一致性别未决＋"
        f"定位，逐项观测：{observed!r}"
    )


def _active_history_input(history: str | None) -> dict:
    """active actual PA（p1 individual＋有效 date、p2 none）单变量 `history`
    输入（005 §4.10 依赖表）；`None`＝`actual.history` 键缺失。"""
    year = "2025_26"
    actual: dict[str, Any] = {
        "pa": {"p1": {"mode": "individual", "date": "2025-06-01"},
               "p2": {"mode": "none"}},
        "salary_joint_assessed": False,
    }
    if history is not None:
        actual["history"] = history
    return _pa_input(
        year, _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _election_day()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="0", mpf="0", ai=False),
        },
        assessment={"basis": "actual", "candidates": ["pa_self"],
                    "selection": "pa_self", "actual": actual},
    )


def _assert_active_history_control() -> None:
    """同输入 history=ordinary 合法控制（各自节点内先跑，再仅删/改目标字段）。"""
    control = _check(_selection(_run_pa(_active_history_input("ordinary"))), "pa_self")
    assert control.get("admission") == "eligible", (
        f"合法控制（history=ordinary）须 eligible，实际：{control!r}"
    )


def test_002_pa_active_history_missing_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    `actual.history` **缺失**（仅 pa_self 被请求且无独立已知 scenario）→
    候选 unresolved、结果 needs_input（不容忍 partial）、missing 属需补
    事实 → 以 field/year 定位追问（005 §4.10 依赖表）。

    每态先 ordinary 合法控制、仅删目标字段；admission/状态/定位同一观测
    字典一次呈现（不以前一断言遮蔽后项）。"""
    year = "2025_26"
    _assert_active_history_control()
    result = _run_pa(_active_history_input(None))
    check = _check(_selection(result), "pa_self")
    observed = {
        "admission": check.get("admission"),
        "status": result["status"],
        "question": _question_located(result, year, "actual", "history"),
    }
    assert observed == {
        "admission": "unresolved",
        "status": "needs_input",
        "question": True,
    }, (
        f"history 缺失须置候选 unresolved＋needs_input＋field/year 定位"
        f"（逐项观测）：{observed!r}"
    )


def test_002_pa_active_history_unknown_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    `actual.history` **unknown** → 候选 unresolved、needs_input、unknown 属
    需补事实 → field/year 定位（005 §4.10 依赖表）。先 ordinary 控制。"""
    year = "2025_26"
    _assert_active_history_control()
    result = _run_pa(_active_history_input("unknown"))
    check = _check(_selection(result), "pa_self")
    observed = {
        "admission": check.get("admission"),
        "status": result["status"],
        "question": _question_located(result, year, "actual", "history"),
    }
    assert observed == {
        "admission": "unresolved",
        "status": "needs_input",
        "question": True,
    }, (
        f"history unknown 须置候选 unresolved＋needs_input＋field/year 定位"
        f"（逐项观测）：{observed!r}"
    )


def test_002_pa_active_history_withdrawn_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    `actual.history` **withdrawn**（已知非 ordinary）→ 候选 unresolved、
    结果 needs_input；不强制定位（005 §4.10 依赖表）。先 ordinary 控制。"""
    _assert_active_history_control()
    result = _run_pa(_active_history_input("withdrawn"))
    check = _check(_selection(result), "pa_self")
    observed = {
        "admission": check.get("admission"),
        "status": result["status"],
    }
    assert observed == {
        "admission": "unresolved",
        "status": "needs_input",
    }, (
        f"history withdrawn 须置候选 unresolved＋needs_input（逐项观测）："
        f"{observed!r}"
    )


def test_002_pa_active_history_disputed_unresolved() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    `actual.history` **disputed**（已知争议历史）→ 候选 unresolved、
    结果 needs_input；不强制定位（005 §4.10 依赖表）。先 ordinary 控制。"""
    _assert_active_history_control()
    result = _run_pa(_active_history_input("disputed"))
    check = _check(_selection(result), "pa_self")
    observed = {
        "admission": check.get("admission"),
        "status": result["status"],
    }
    assert observed == {
        "admission": "unresolved",
        "status": "needs_input",
    }, (
        f"history disputed 须置候选 unresolved＋needs_input（逐项观测）："
        f"{observed!r}"
    )


def _assessed_state_input(candidate_id: str, state: str | None) -> dict:
    """active PA 候选（pa_self：p1 individual/date；no_pa：双方 none）的
    `salary_joint_assessed` 单变量输入；`None`＝完整控制（assessed=false），
    `missing`＝删键，`unknown`＝显式 unknown（不用 actual={} 多变异）。"""
    year = "2025_26"
    if candidate_id == "pa_self":
        actual: dict[str, Any] = {
            "pa": {"p1": {"mode": "individual", "date": "2025-06-01"},
                   "p2": {"mode": "none"}},
            "history": "ordinary",
            "salary_joint_assessed": False,
        }
    else:
        actual = {
            "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
            "history": "ordinary",
            "salary_joint_elected": False,
            "salary_joint_assessed": False,
        }
    if state == "missing":
        actual.pop("salary_joint_assessed", None)
    elif state == "unknown":
        actual["salary_joint_assessed"] = "unknown"
    return _pa_input(
        year, _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _election_day()}},
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="0", mpf="0", ai=False),
        },
        assessment={"basis": "actual", "candidates": [candidate_id],
                    "selection": candidate_id, "actual": actual},
    )


def _assert_assessed_control(candidate_id: str) -> None:
    """同候选 assessed=false 完整控制（各自节点内先跑，再仅删/改目标字段）。"""
    control = _check(
        _selection(_run_pa(_assessed_state_input(candidate_id, None))), candidate_id
    )
    assert control.get("admission") == "eligible", (
        f"合法控制（salary_joint_assessed=false）：{candidate_id} 应可算，"
        f"实际：{control!r}"
    )


def _assert_assessed_state(candidate_id: str, state: str) -> None:
    """单状态观测：unresolved＋needs_input＋field/year 定位（一次字典呈现）。"""
    year = "2025_26"
    result = _run_pa(_assessed_state_input(candidate_id, state))
    check = _check(_selection(result), candidate_id)
    observed = {
        "admission": check.get("admission"),
        "status": result["status"],
        "question": _question_located(
            result, year, "actual", "salary_joint_assessed"
        ),
    }
    assert observed == {
        "admission": "unresolved",
        "status": "needs_input",
        "question": True,
    }, (
        f"salary_joint_assessed {state}（{candidate_id}）须置候选 unresolved＋"
        f"needs_input＋field/year 定位（逐项观测）：{observed!r}"
    )


def test_002_pa_assessed_missing_unresolved_pa_self() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    pa_self 的 `salary_joint_assessed` 缺失（s.41(1B) 依赖核对未决）→
    unresolved＋needs_input＋定位（005 §4.10 依赖表）。先 assessed=false
    完整控制、再仅删目标字段。"""
    _assert_assessed_control("pa_self")
    _assert_assessed_state("pa_self", "missing")


def test_002_pa_assessed_unknown_unresolved_pa_self() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    pa_self 的 `salary_joint_assessed` 显式 unknown → unresolved＋
    needs_input＋定位；先完整控制、再仅改目标字段（005 §4.10 依赖表）。"""
    _assert_assessed_control("pa_self")
    _assert_assessed_state("pa_self", "unknown")


def test_002_pa_assessed_missing_unresolved_no_pa() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    no_pa 的 `salary_joint_assessed`（actual no_pa 的 salary 模式核对）缺失
    → unresolved＋needs_input＋定位；先完整控制、再仅删目标字段。"""
    _assert_assessed_control("no_pa")
    _assert_assessed_state("no_pa", "missing")


def test_002_pa_assessed_unknown_unresolved_no_pa() -> None:
    """依赖矩阵（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐状态独立节点）：
    no_pa 的 `salary_joint_assessed` 显式 unknown → unresolved＋needs_input＋
    定位；先完整控制、再仅改目标字段（005 §4.10 依赖表）。"""
    _assert_assessed_control("no_pa")
    _assert_assessed_state("no_pa", "unknown")


def _s7_sequential_household(year: str) -> dict:
    """005 §5.4 S7 计划 fixture 形态：同一 YA 顺序配偶 p3→p2（非重叠）。"""
    return {
        "marriage": [
            {"from": "2025-04-01", "to": "2025-08-01",
             "value": "married", "spouse_id": "p3"},
            {"from": "2025-08-01", "to": "2025-09-01", "value": "unmarried"},
            {"from": "2025-09-01", "to": "2026-04-01",
             "value": "married", "spouse_id": "p2"},
        ],
        "living_apart": [
            {"from": "2025-04-01", "to": "2025-08-01", "value": False},
            {"from": "2025-09-01", "to": "2026-04-01", "value": False},
        ],
        "child_nomination": {"person_id": None, "status": "none"},
    }


def _s7_subjects() -> list[dict]:
    return [{"subject_id": "p1", "birth_date": "1990-01-01"},
            {"subject_id": "p2", "birth_date": "1992-02-02"},
            {"subject_id": "p3", "birth_date": "1993-03-03"}]


def test_002_pa_s7_no_pa_sequential_spouse_unresolved() -> None:
    """S7（completion-plan A；A 质量复核 #6／W-D3-A/AQA-06 PA 侧）：同一 YA
    顺序配偶变化下，hypothetical no_pa 的 s.29/婚姻相关归属决定不得默认
    分支 A——候选 unresolved、needs_input、无 married 264,000 授予、无确定
    税额；locator＝household.marriage、subject_id=p1（005 §9.1 S7/F005462）。

    W-D3-A 合法前置修正：删除未声明 reference/选举的年外 TF（no_pa 不评估
    P1、不要求 reference_date；005 §4.10 规则 3/§7 域），不加无用 reference；
    同 subjects（历史 p3 注册但非 actor）先跑普通婚姻窗口控制，仅改窗口。
    不以薪俸 lane 的 Red 代替 PA 路径。"""
    year = "2025_26"
    control = _run_pa(_pa_input(
        year,
        _s7_subjects(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="240000", mpf="9000", ai=True),
                    "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
    ))
    assert _check(_selection(control), "no_pa").get("admission") == "eligible", (
        f"合法控制：同 subjects 仅把窗口改为普通婚姻 → no_pa 应可算，实际："
        f"{_check(_selection(control), 'no_pa')!r}"
    )

    result = _run_pa(_pa_input(
        year,
        _s7_subjects(),
        household=_s7_sequential_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="240000", mpf="9000", ai=True),
                    "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
    ))
    selection = _selection(result)
    check = _check(selection, "no_pa")
    married_bases = [
        b.get("value")
        for detail in _steps(result, "assessment")
        for b in detail.get("bases") or []
        if b.get("name") == "allowances" and b.get("value") == "264000/1"
    ]
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "final": result["amounts"]["final_after_reduction"],
        "married_bases": married_bases,
        "question": _question_located(result, year, "household.marriage",
                                      subject_id="p1"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "final": None,
        "married_bases": [],
        "question": True,
    }, (
        f"S7 顺序配偶下 no_pa 不得默认分支 A（逐项观测）：{observed!r}"
    )


def test_002_pa_s7_pa_self_sequential_spouse_unresolved() -> None:
    """S7（completion-plan A；A 质量复核 #6／W-D3-A/AQA-06 PA 侧）：
    hypothetical pa_self 在顺序配偶变化下，s.29 归属依赖决定未决 →
    pa_self unresolved、needs_input、无 married 264,000 授予、无确定税额；
    locator＝household.marriage、subject_id=p1（005 §9.1 S7）。P1 按参考日
    独立可评（本节点不含婚姻依赖的默认授予）。

    W-D3-A 合法前置：保留 pa_self 实际活动 reference/P1（不共享删除）；
    同 subjects 先跑普通婚姻窗口控制（_s6_household(reference=…)），仅改窗口。"""
    year, reference = "2025_26", "2026-06-01"
    control = _run_pa(_pa_input(
        year,
        _s7_subjects(),
        household=_s6_household(year, reference=reference),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={"p1": _fin(nai="240000", mpf="9000", ai=True),
                    "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    assert _check(_selection(control), "pa_self").get("admission") == "eligible", (
        f"合法控制：同 subjects 仅把窗口改为普通婚姻（参考日覆盖）→ pa_self "
        f"应可算，实际：{_check(_selection(control), 'pa_self')!r}"
    )

    result = _run_pa(_pa_input(
        year,
        _s7_subjects(),
        household=_s7_sequential_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={"p1": _fin(nai="240000", mpf="9000", ai=True),
                    "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "candidates": ["pa_self"], "selection": "pa_self"},
    ))
    selection = _selection(result)
    check = _check(selection, "pa_self")
    married_bases = [
        b.get("value")
        for detail in _steps(result, "assessment")
        for b in detail.get("bases") or []
        if b.get("name") == "allowances" and b.get("value") == "264000/1"
    ]
    observed = {
        "status": result["status"],
        "admission": check.get("admission"),
        "final": result["amounts"]["final_after_reduction"],
        "married_bases": married_bases,
        "question": _question_located(result, year, "household.marriage",
                                      subject_id="p1"),
    }
    assert observed == {
        "status": "needs_input",
        "admission": "unresolved",
        "final": None,
        "married_bases": [],
        "question": True,
    }, (
        f"S7 顺序配偶下 pa_self 不得默认授予（逐项观测）：{observed!r}"
    )


def _hid_mutate_p2_property(financials: dict, field: str) -> None:
    """hID 原值变异（仅 p2 原始字段；合法基线其余同值）。"""
    prop = financials["p2"]["properties"][0]
    if field == "rent_received":
        prop["rent_received"] = "250000"
    elif field == "rates_paid_by_owner.amount":
        prop["rates_paid_by_owner"] = {
            "amount": "1000", "agreed": False, "actually_paid": False,
        }
    elif field == "irrecoverable_rent":
        prop["irrecoverable_rent"] = "1000"
    else:  # deposit_offsets
        prop["deposit_offsets"] = "1000"


def _assert_hid_mutation_rejected(field: str, candidate_id: str) -> None:
    """hID 原值一致性单（字段×候选）节点（W-D3-A/AQA-03）：先同输入合法
    控制（no_pa/pa_joint eligible；pa_self 的 mixed 共享 h1 单位 unresolved
    合法），再仅改 p2 的该原始字段 → E_INPUT_CONTRADICTORY（005 §9.3）。"""
    from app.core.errors import AppError

    year, reference = "2025_26", "2026-06-01"

    def run(financials: dict) -> dict:
        return _run_pa(_pa_input(
            year, _subjects_couple(),
            household=_s6_household(year, reference=reference),
            participants=_COUPLE_PARTICIPANTS,
            subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                           "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
            financials=financials,
            assessment={"basis": "hypothetical", "reference_date": reference,
                        "candidates": [candidate_id], "selection": candidate_id},
        ))

    control = _check(_selection(run(_couple_property_facts(year))), candidate_id)
    if candidate_id == "pa_self":
        assert control.get("admission") == "unresolved" and (
            "CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED" in (control.get("reason_codes") or [])
        ), (
            f"合法控制：pa_self 的 mixed 共享 h1 单位须 unresolved/"
            f"CROSS_MODE_UNIT_ALLOCATION_UNSUPPORTED（005 §9.3），实际：{control!r}"
        )
    else:
        assert control.get("admission") == "eligible", (
            f"合法控制（无变异）：{candidate_id} 应可算，实际：{control!r}"
        )

    financials = _couple_property_facts(year)
    _hid_mutate_p2_property(financials, field)
    try:
        run(financials)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"仅 p2 {field} 变异：{candidate_id} 亦须在候选分配前"
            f"E_INPUT_CONTRADICTORY（005 §9.3），实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            f"同一 hID 的共同原始字段在 {candidate_id} 分配前均须一致："
            f"仅 p2 {field} 变异不得计算（005 §9.3）"
        )


def test_002_pa_hid_raw_consistency_rent_no_pa() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：rent_received×no_pa。"""
    _assert_hid_mutation_rejected("rent_received", "no_pa")


def test_002_pa_hid_raw_consistency_rent_pa_self() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：rent_received×pa_self。"""
    _assert_hid_mutation_rejected("rent_received", "pa_self")


def test_002_pa_hid_raw_consistency_rent_pa_joint() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：rent_received×pa_joint。"""
    _assert_hid_mutation_rejected("rent_received", "pa_joint")


def test_002_pa_hid_raw_consistency_rates_no_pa() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：rates×no_pa。"""
    _assert_hid_mutation_rejected("rates_paid_by_owner.amount", "no_pa")


def test_002_pa_hid_raw_consistency_rates_pa_self() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：rates×pa_self。"""
    _assert_hid_mutation_rejected("rates_paid_by_owner.amount", "pa_self")


def test_002_pa_hid_raw_consistency_rates_pa_joint() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：rates×pa_joint。"""
    _assert_hid_mutation_rejected("rates_paid_by_owner.amount", "pa_joint")


def test_002_pa_hid_raw_consistency_bad_debt_no_pa() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：irrecoverable_rent×no_pa。"""
    _assert_hid_mutation_rejected("irrecoverable_rent", "no_pa")


def test_002_pa_hid_raw_consistency_bad_debt_pa_self() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：irrecoverable_rent×pa_self。"""
    _assert_hid_mutation_rejected("irrecoverable_rent", "pa_self")


def test_002_pa_hid_raw_consistency_bad_debt_pa_joint() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：irrecoverable_rent×pa_joint。"""
    _assert_hid_mutation_rejected("irrecoverable_rent", "pa_joint")


def test_002_pa_hid_raw_consistency_offset_no_pa() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：deposit_offsets×no_pa。"""
    _assert_hid_mutation_rejected("deposit_offsets", "no_pa")


def test_002_pa_hid_raw_consistency_offset_pa_self() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：deposit_offsets×pa_self。"""
    _assert_hid_mutation_rejected("deposit_offsets", "pa_self")


def test_002_pa_hid_raw_consistency_offset_pa_joint() -> None:
    """hID 12 组合独立参数节点（W-D3-A/AQA-03）：deposit_offsets×pa_joint。"""
    _assert_hid_mutation_rejected("deposit_offsets", "pa_joint")


def test_002_pa_hid_share_and_interest_differences_are_legal() -> None:
    """005 §4.6：同 hID 共享原始值一致时，份额与个人利息可不同——
    单变量控制（份额 1/3/2/3＋利息 1,000/2,000）不得被原值一致性拒绝。"""
    year = "2025_26"
    reference = "2026-06-01"
    financials = _couple_property_facts(year)
    financials["p1"]["properties"][0]["share"] = "1/3"
    financials["p2"]["properties"][0]["share"] = "2/3"
    financials["p1"]["properties"][0]["mortgage_interest"] = "1000"
    financials["p2"]["properties"][0]["mortgage_interest"] = "2000"
    result = _run_pa(_pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=reference),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=financials,
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "candidates": ["no_pa"], "selection": "no_pa"},
    ))
    assert _check(_selection(result), "no_pa").get("admission") == "eligible", (
        "份额/个人利息差异属合法（005 §4.6），不得按原值不一致拒绝"
    )


def _shared_h1_low_rent_input(year: str) -> dict:
    """共享 h1 租金 25、双方各 1/2、其他扣减 0（NAV=25×4/5=20）合法基线。"""
    reference = "2026-06-01"
    prop = _shared_property(rent="25")
    financials = {
        "p1": _fin(nai="0", mpf="0", ai=False,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_property_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "property": "confirmed_in_scope"},
                   properties=[dict(prop)]),
        "p2": _fin(nai="0", mpf="0", ai=False,
                   has_income_assessable_under_ordinance=True,
                   has_assessable_property_income=True,
                   source_classification={"salary": "confirmed_in_scope",
                                          "property": "confirmed_in_scope"},
                   properties=[dict(prop)]),
    }
    return _pa_input(
        year, _subjects_couple(),
        household=_s6_household(year, reference=reference),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials=financials,
        assessment={"basis": "hypothetical", "reference_date": reference,
                    "candidates": ["no_pa"], "selection": "no_pa"},
    )


def test_002_pa_shared_property_original_unit_tax_amount_is_three() -> None:
    """E6/R6（completion-plan A；A 质量复核 #3 拆分-金额节点先行）：
    共享 h1 租金 25、双方各 1/2、其他扣减 0：NAV=25×(1−1/5)=20，
    单位物业税=floor(20×15%)=3（按人切片错误路径：floor(10×15%)×2=2）。

    本节点只断言金额（独立于单计数器）；单单位结构断言见紧随节点。
    """
    year = "2025_26"
    result = _run_pa(_shared_h1_low_rent_input(year))
    scenario = next(
        s for s in _steps(result, "scenario") if s.get("scenario_id") == "no_pa"
    )
    assert scenario["amounts"]["final_after_reduction"] == "3", (
        f"NAV=25×4/5=20 → 原始单位物业税 floor(20×15%)=3（非按人 1+1=2），"
        f"实际：{scenario['amounts']!r}"
    )


def test_002_pa_shared_property_single_original_unit() -> None:
    """E6/R6（completion-plan A；A 质量复核 #3 拆分-单单位结构断言）：
    同一 h1 共同持有须按**一个原始评税单位**计税（禁止先按人切片）——
    独立于金额断言的单位计数节点。"""
    year = "2025_26"
    result = _run_pa(_shared_h1_low_rent_input(year))
    h1_units = [d for d in _steps(result, "assessment")
                if str(d.get("unit_id", "")).startswith("property:h1")]
    assert len(h1_units) == 1, (
        f"h1 共同持有须按一个原始评税单位计税（禁止先按人切片），实际："
        f"{[d.get('unit_id') for d in h1_units]!r}"
    )


def test_002_pa_basic_decision_in_steps_reconciles_unit() -> None:
    """E7/§14：PA 引擎须输出基础额 decision（basic 132,000）并与实际单位
    对账；当前 PA steps 无任何 decision → 目标 Red。

    A 质量复核 #4：active actual no_pa 不得缺 mode（不引入 inactive P1/参考
    日）——局部补 `actual.pa.p1=none` 的合法活动事实；不因该合法事实要求
    reference_date。"""
    year = "2025_26"
    result = _run_pa(_pa_input(
        year, _subjects_single(),
        assessment={"basis": "actual", "candidates": ["no_pa"], "selection": "no_pa",
                    "actual": {"pa": {"p1": {"mode": "none"}}}},
    ))
    basics = [d for d in _decisions(result) if d.get("item_kind") == "basic"]
    assert basics, (
        f"PA steps 须含 basic 基础额 decision（005 §14 示例），实际："
        f"{_decisions(result)!r}"
    )
    assert basics[0].get("grant") == "allowed" and basics[0].get("amount") == "132000", (
        basics[0]
    )
    assert _unit_base(result, "salary:p1", "allowances") == ["132000/1"], (
        f"decision 金额须与 salary:p1 实际单位基础额对账，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def test_002_pa_2024_25_next_3940_not_940_and_scenario_components_sync() -> None:
    """E7/R7.3：PA 2024/25→2025/26 的 next 暂缴取 2025/26 宽减前 3,940
    （不得套 2025/26 宽减错得 940）；scenario 组件须同步真实下一年组件。

    当前 top-level next=3,940 但 scenario.next_provisional 恒 null → 目标 Red。
    """
    y0, y1 = "2024_25", "2025_26"
    base0 = _base_year(
        y0,
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
        financials={"p1": _fin(nai="240000", mpf="9000")},
    )
    base1 = _base_year(
        y1,
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
        financials={"p1": _fin(nai="240000", mpf="9000")},
    )
    facts = {
        "year_of_assessment": y0,
        "subjects": _subjects_single(),
        "years": {y0: base0, y1: base1},
        "hk_presence_days": [],
        "assumptions": [],
    }
    result = _run_pa(facts)
    amounts = result["amounts"]
    assert result["status"] == "partial", result["status"]
    assert amounts["before_reduction"] == "3940", amounts
    assert amounts["reduction"] == "1500", amounts
    assert amounts["final_after_reduction"] == "2440", amounts
    assert amounts["next_provisional"] == "3940", (
        f"next 暂缴＝2025/26 宽减前 3,940（不得套宽减 940），实际：{amounts!r}"
    )
    assert amounts["next_provisional"] != "940", amounts
    assert amounts["balance"] is None, amounts
    scenario = next(
        s for s in _steps(result, "scenario") if s.get("scenario_id") == "no_pa"
    )
    assert scenario["amounts"]["next_provisional"] == "3940", (
        f"scenario 组件须同步真实下一年组件（非恒 null），实际："
        f"{scenario['amounts']!r}"
    )


def _pa_no_pa_two_year_facts(y0: str, y1: str) -> dict:
    """no_pa-only 双年合法事实（NAI 240,000/MPF 9,000；selected=no_pa）共用构造器。"""
    base0 = _base_year(
        y0,
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
        financials={"p1": _fin(nai="240000", mpf="9000")},
    )
    base1 = _base_year(
        y1,
        assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                    "selection": "no_pa"},
        financials={"p1": _fin(nai="240000", mpf="9000")},
    )
    return {
        "year_of_assessment": y0,
        "subjects": _subjects_single(),
        "years": {y0: base0, y1: base1},
        "hk_presence_days": [],
        "assumptions": [],
    }


def test_002_pa_2025_26_no_pa_only_scenario_and_next_sync() -> None:
    """E7（completion-plan A；A 质量复核 #8；W-D3-A/AQA-08 拆分-1/2）：
    2025/26→2026/27 no_pa-only 当前 940、next 3,160（2026/27 basic 145,000：
    NCI=240,000−9,000−145,000=86,000 → 1,000+2,160=3,160、宽减 0）；PA 恒
    partial、balance null；selected scenario 六金额键＝top-level（不得
    scenario 恒 partial/null）。next_year tagged step 独立见紧随节点。"""
    y0, y1 = "2025_26", "2026_27"
    result = _run_pa(_pa_no_pa_two_year_facts(y0, y1))
    assert result["status"] == "partial", result["status"]
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "3940", amounts
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "940", amounts
    assert amounts["next_provisional"] == "3160", (
        f"next 暂缴＝2026/27 计算值 3,160，实际：{amounts!r}"
    )
    assert amounts["balance"] is None, amounts
    scenario = next(
        s for s in _steps(result, "scenario") if s.get("scenario_id") == "no_pa"
    )
    assert scenario["amounts"] == dict(amounts), (
        f"selected scenario 六金额键须与 top-level 一致，实际："
        f"{scenario['amounts']!r} vs {amounts!r}"
    )


def test_002_pa_2025_26_no_pa_only_next_year_step_amount() -> None:
    """E7（W-D3-A/AQA-08 拆分-2/2，next_year 独立节点）：
    2025/26→2026/27 no_pa-only 的 next_year tagged step 独立核对：
    status=available、selected=no_pa、amount=3,160（与 top-level
    next_provisional 一致）；不依赖 selected scenario 六键断言先行通过。"""
    y0, y1 = "2025_26", "2026_27"
    result = _run_pa(_pa_no_pa_two_year_facts(y0, y1))
    next_steps = _steps(result, "next_year")
    assert next_steps, f"须含 next_year tagged step，实际：{result.get('steps')!r}"
    detail = next_steps[0]
    assert detail.get("year") == y1, (
        f"next_year.year 须为下一年度 {y1}（处方:74：year/selected/amount 依"
        f"已提交 next），实际：{detail!r}"
    )
    assert detail.get("status") == "available", detail
    assert detail.get("selected") == "no_pa", detail
    assert detail.get("amount") == "3160", (
        f"next_year.amount 须与 next_provisional 一致（3,160），实际：{detail!r}"
    )


def test_002_pa_steps_closed_details_exact_and_sorted_reasons() -> None:
    """§14：PA steps 封闭 detail 键（**required 与 allowed 同时断言**）／
    bases/op/shares、assessment 三金额、scenario 六金额、candidate_checks
    嵌套形状覆盖／Exact 必须为真字符串且约分（零仅 0/1；拒 -0/1、00/1、
    非 ASCII 数字与非串）／reason_codes+rule_refs 排序去重／同输入决定序稳定。

    W-D3-A/AQA-08：改用含**真实非空 decision 与 joint shares** 的合法输入
    （夫妻＋子女申索＋pa_joint），先证容器非空再核嵌套（不得以单人无 claim
    的空遍历宣称覆盖）；零字面严格 0/1，配少量固定正反控制（不用生产
    helper 作 oracle）。
    """
    year, ref = "2025_26", "2026-06-01"
    facts = _pa_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        household=_s6_household(year, reference=ref),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={
            "p1": {"ordinarily_resident_hk": _resident_at_reference()},
            "p2": {"ordinarily_resident_hk": _resident_at_reference()},
            "d1": {"unmarried": _tf(year, True)},
        },
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child")],
        financials={
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
        assessment={"basis": "hypothetical", "reference_date": ref,
                    "candidates": ["no_pa", "pa_joint"], "selection": "best"},
    )
    result = _run_pa(facts)
    closed_detail_keys = {
        "context": {"year", "tax_type", "profile_id", "assumptions"},
        "decision": {"year", "scenario_id", "claim_id", "claimant_id", "subject_id",
                     "item_kind", "entitlement", "grant", "amount",
                     "reason_codes", "rule_refs"},
        "assessment": {"year", "scenario_id", "unit_id", "branch", "actors",
                       "bases", "operations", "amounts", "shares"},
        "scenario": {"year", "scenario_id", "status", "unit_ids", "amounts"},
        "selection": {"year", "requested", "selected", "best", "reason_codes",
                      "candidate_checks"},
        "next_year": {"year", "status", "selected", "amount", "reason_codes",
                      "assumptions"},
    }
    for step in result.get("steps") or []:
        kind = step.get("kind")
        assert kind in closed_detail_keys, kind
        detail = step.get("detail") or {}
        extra = set(detail) - closed_detail_keys[kind]
        assert not extra, f"{kind} detail 超出封闭键：{sorted(extra)!r}"
        missing = closed_detail_keys[kind] - set(detail)
        assert not missing, (
            f"{kind} detail 缺 required 封闭键（§14）：{sorted(missing)!r}；"
            f"实际键：{sorted(detail)!r}"
        )

    # 先证容器非空：真实 decision（claim c1）与 joint shares（pa:joint）存在。
    assert _decisions(result), (
        f"本节点须有真实非空 decision（不得空遍历宣称覆盖），实际："
        f"{result.get('steps')!r}"
    )
    joint_units = [d for d in _steps(result, "assessment") if d.get("unit_id") == "pa:joint"]
    assert joint_units, "本节点须有 pa:joint 评税单位"
    assert joint_units[0].get("shares"), (
        f"pa:joint 须记真实非空 shares，实际：{joint_units[0]!r}"
    )
    assert _steps(result, "scenario") and _steps(result, "selection"), (
        "须含 scenario 与 selection tagged 条目"
    )

    def _exact_ok(value: object) -> bool:
        if not isinstance(value, str) or not value.isascii():
            return False
        num, sep, den = value.partition("/")
        if not sep or not num or not den:
            return False
        body = num[1:] if num.startswith("-") else num
        if not body.isdigit() or not den.isdigit():
            return False
        if len(body) > 1 and body.startswith("0"):
            return False  # 拒 00/1 等前导零
        if num.startswith("-") and body == "0":
            return False  # 拒 -0/1
        n, d = int(num), int(den)
        if d <= 0 or math.gcd(abs(n), d) != 1:
            return False
        return n != 0 or value == "0/1"  # 零仅允许 "0/1"

    # 固定正反控制（少量）：约分、正分母、零字面、非 ASCII、非串。
    assert _exact_ok("3940/1") and _exact_ok("0/1") and _exact_ok("-1/1")
    assert not _exact_ok("-0/1") and not _exact_ok("00/1") and not _exact_ok("0/0")
    assert not _exact_ok("2/4") and not _exact_ok("1/0") and not _exact_ok("3940")
    assert not _exact_ok("٣/1")

    assessment_keys = closed_detail_keys["assessment"]
    assessment_amount_keys = {"before_reduction", "reduction", "final_after_reduction"}
    base_names = {"nai", "part4a", "allowances", "nci", "reduced_income",
                  "nav", "nav_share", "profit_share", "standard_base"}
    for detail in _steps(result, "assessment"):
        assert set(detail) == assessment_keys, detail
        assert set(detail.get("amounts") or {}) == assessment_amount_keys, detail
        for base in detail.get("bases") or []:
            assert isinstance(base, Mapping) and set(base) == {"name", "value"}, base
            assert base.get("name") in base_names, base
            assert _exact_ok(base.get("value")), (
                f"bases 值须为约分 Exact 字符串（非 Fraction/float），实际：{base!r}"
            )
        for op in detail.get("operations") or []:
            assert isinstance(op, Mapping) and set(op) == {"op", "inputs", "output"}, op
            assert isinstance(op.get("inputs"), list)
            assert all(_exact_ok(v) for v in op.get("inputs") or []), op
            assert _exact_ok(op.get("output")), op
        for share in detail.get("shares") or []:
            assert isinstance(share, Mapping) and set(share) == {"person_id", "ratio"}, share
            assert _exact_ok(share.get("ratio")), (
                f"shares.ratio 须为约分 Exact 字符串（§14），实际：{share!r}"
            )
    for detail in _steps(result, "scenario"):
        assert set(detail.get("amounts") or {}) == set(_AMOUNT_KEYS), (
            f"scenario.amounts 须为六金额键，实际：{detail!r}"
        )
    for detail in _steps(result, "selection"):
        for check in detail.get("candidate_checks") or []:
            assert isinstance(check, Mapping) and set(check) == {
                "candidate_id", "admission", "legal_path",
                "responsible_person_id", "separate_final", "conditional_final",
                "reason_codes",
            }, check
    for detail in _decisions(result):
        for key in ("reason_codes", "rule_refs"):
            values = detail.get(key) or []
            assert values == sorted(set(values)), (key, values)

    again = _run_pa(facts)
    assert json.dumps(result.get("steps"), ensure_ascii=False, sort_keys=True) == (
        json.dumps(again.get("steps"), ensure_ascii=False, sort_keys=True)
    ), "PA steps 必须确定性（同输入同序）"


def _assert_assessment_block_order(
    blocks: list[tuple[str, list]], expected: tuple[str, ...]
) -> None:
    """D4-A-A2/AQA-08（ora-22 缺口；Approved005:738）：assessment 分块规定顺序。

    §14 排序＝年度→候选固定顺序→单位 ID；以原始返回为对象：
    ①同一 scenario 不得分段再现（块唯一——防 A:u2→B:v1→A:u1 三单元素块
    以「每块各自升序」漏检）；②块次序须为规定候选顺序的子序（scenario 缺席
    时保持相对顺序即可）；③按 scenario 收集**全部**原始单位序列后须按单位
    ID 升序（不因分块截断放弃整段升序不变量）。
    """
    block_ids = [scenario_id for scenario_id, _ in blocks]
    assert len(block_ids) == len(set(block_ids)), (
        f"同一 scenario 的 assessment 不得分段再现（§14 候选固定顺序；"
        f"Approved005:738）：{blocks!r}"
    )
    assert block_ids == [sid for sid in expected if sid in block_ids], (
        f"assessment 块须按规定候选顺序 {list(expected)!r} 排列（§14）："
        f"{blocks!r}"
    )
    merged: dict[str, list] = {}
    for scenario_id, unit_ids in blocks:
        merged.setdefault(scenario_id, []).extend(unit_ids)
    for scenario_id, unit_ids in merged.items():
        assert unit_ids == sorted(unit_ids), (
            f"同一 scenario {scenario_id!r} 的全部原始单位序列须按单位 ID 升序"
            f"（§14；Approved005:738）：{unit_ids!r}"
        )


def test_002_pa_reordered_arrays_trace_projection_deterministic() -> None:
    """E7（A 质量复核 #8；W-D3-A/AQA-08；D4-A 还原内容比较）：重排合法
    candidates／物业单位／claims 输入数组 → 规定 trace 顺序（候选固定顺序、
    单位 ID 升序、decision 按 claimID/subjectID）与连续 step 号保持一致；
    **不比整 core/hash**，也不要求 requested 按固定顺序（`requested` 可保留
    输入顺序）。

    W-D3-A 修正：正数物业须有活动 property 来源分类（005 §477：缺需判定
    的来源分支＝needs_input，不得默认 confirmed）——补齐合法来源后再观察
    排序；直接核**原始返回**的 unitID/同类 claimID 实际顺序及连续 step，
    不先排序结果再证明排序；重复稳定不替代规定顺序。

    D4-A（ora-16 缺口）：恢复跨输入投影内容比较（kind/label＋去 requested
    后的完整 detail——含金额/bases/operations/grant），不得以仅比 ID/顺序
    替代（同 ID 异内容须被检出）；assessment 原始顺序按 scenario 分块核
    unit ID 规定顺序，不以「跨输入相等」代替。

    D4-A-A2（A2=1 再授权；ora-22 缺口）：assessment 分块增加
    `_assert_assessment_block_order`——同一 scenario 不得分段再现、块须按
    规定候选顺序子序排列、且按 scenario 合并全部原始单位后须升序；helper 级
    正/反双向控制见 `test_002_pa_assessment_block_order_validator_controls`。

    A：candidates=[no_pa,pa_self]、properties=[h1,h2]、claims=[c1,c2]；
    B：三者倒序（同一事实集，仅数组次序变化）。"""
    year = "2025_26"

    def property_item(pid: str, rent: str) -> dict:
        return {
            "property_id": pid,
            "share": "1/1",
            "rent_received": rent,
            "rates_paid_by_owner": {"amount": "0", "agreed": False,
                                    "actually_paid": False},
            "irrecoverable_rent": "0",
            "deposit_offsets": "0",
            "mortgage_interest": "0",
        }

    def facts_for(reversed_order: bool) -> dict:
        candidates = ["no_pa", "pa_self"]
        properties = [property_item("h1", "100000"), property_item("h2", "50000")]
        claims = [_claim("c1", "child", subject="d1"),
                  _claim("c2", "child", subject="d2")]
        relations = [
            _relation(year, "r1", "p1", "d1", maintained=_tf(year, True)),
            _relation(year, "r2", "p1", "d2", maintained=_tf(year, True)),
        ]
        if reversed_order:
            candidates.reverse()
            properties.reverse()
            claims.reverse()
            relations.reverse()
        finance = _fin(nai="240000", mpf="9000", properties=properties)
        finance["has_assessable_property_income"] = True
        finance["source_classification"] = {
            "salary": "confirmed_in_scope",
            "property": "confirmed_in_scope",
        }
        return _pa_input(
            year,
            _subjects_single([("d1", "2015-06-01"), ("d2", "2017-07-01")]),
            subject_facts={
                "p1": {"ordinarily_resident_hk": _resident_at_reference()},
                "d1": {"unmarried": _tf(year, True)},
                "d2": {"unmarried": _tf(year, True)},
            },
            relations=relations,
            claims=claims,
            financials={"p1": finance},
            assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                        "candidates": candidates, "selection": "best"},
        )

    def raw_orders(result: Mapping) -> dict:
        steps = result.get("steps") or []
        assert [s.get("step") for s in steps] == list(range(1, len(steps) + 1)), (
            f"step 号必须自 1 连续（§14），实际：{[s.get('step') for s in steps]!r}"
        )
        selection = _selection(result)
        assessment_blocks: list[tuple[str, list]] = []
        for detail in _steps(result, "assessment"):
            scenario_id = detail.get("scenario_id")
            if not assessment_blocks or assessment_blocks[-1][0] != scenario_id:
                assessment_blocks.append((scenario_id, []))
            assessment_blocks[-1][1].append(detail.get("unit_id"))
        return {
            "checks_order": [
                c.get("candidate_id")
                for c in selection.get("candidate_checks") or []
            ],
            "scenario_units": [
                (d.get("scenario_id"), list(d.get("unit_ids") or []))
                for d in _steps(result, "scenario")
            ],
            "assessments": [d.get("unit_id") for d in _steps(result, "assessment")],
            "assessment_blocks": assessment_blocks,
            "decisions": [
                (d.get("scenario_id"), d.get("claim_id"), d.get("subject_id"),
                 d.get("item_kind"))
                for d in _decisions(result)
            ],
        }

    def projection(result: Mapping) -> list:
        """规定 trace 投影（§14）：kind/label＋去 `requested` 后的完整 detail。

        D4-A：保留内容一致性（金额/bases/operations/grant）——跨输入仅数组
        次序变化时，同 ID 异内容必须被检出；不预排序结果再比对。
        """
        out = []
        for step in result.get("steps") or []:
            detail = dict(step.get("detail") or {})
            if step.get("kind") == "selection":
                detail.pop("requested", None)
            out.append((
                step.get("kind"),
                step.get("label"),
                json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str),
            ))
        return out

    result_a = _run_pa(facts_for(False))
    result_b = _run_pa(facts_for(True))
    first = raw_orders(result_a)
    second = raw_orders(result_b)

    # 候选固定顺序（不随请求输入次序）：原始返回顺序而非事后排序。
    assert first["checks_order"] == ["no_pa", "pa_self"], first["checks_order"]
    assert second["checks_order"] == ["no_pa", "pa_self"], second["checks_order"]
    # 同一 scenario 内单位 ID 升序（规定顺序）。
    for orders in (first, second):
        for scenario_id, unit_ids in orders["scenario_units"]:
            assert unit_ids == sorted(unit_ids), (scenario_id, unit_ids)
    # assessment 原始顺序：按 scenario 分块后各块 unit ID 亦须升序（§14：
    # 候选固定顺序→单位 ID），先排序结果再证明排序不成立。
    for orders in (first, second):
        for scenario_id, unit_ids in orders["assessment_blocks"]:
            assert unit_ids == sorted(unit_ids), (scenario_id, unit_ids)
    # D4-A-A2（ora-22）：分块唯一＋块候选顺序＋按 scenario 合并后升序——
    # 防 A:u2→B:v1→A:u1 三单元素块以「每块各自升序」漏检（Approved005:738）。
    for orders in (first, second):
        _assert_assessment_block_order(
            orders["assessment_blocks"], ("no_pa", "pa_self")
        )
    # 重排输入后规定顺序不变（原始顺序直接比对）。
    assert first["scenario_units"] == second["scenario_units"], (
        first["scenario_units"], second["scenario_units"]
    )
    assert first["assessments"] == second["assessments"], (
        first["assessments"], second["assessments"]
    )
    assert first["decisions"] == second["decisions"], (
        first["decisions"], second["decisions"]
    )
    # 投影内容一致性（kind/label＋去 requested 后完整 detail，含金额/bases/
    # operations/grant）：仅比 ID/顺序不足以检出同 ID 异内容。
    assert projection(result_a) == projection(result_b), (
        "重排合法 candidates/units/claims 数组后 prescribed trace 投影（kind/"
        "label＋去 requested 后完整 detail）须一致——不得以仅比 ID/顺序替代"
    )
    # 同类 claim 按 claimID/subjectID 实际顺序。
    for orders in (first, second):
        for scenario_id in ("no_pa", "pa_self"):
            claim_ids = [d[1] for d in orders["decisions"] if d[0] == scenario_id]
            assert claim_ids == sorted(claim_ids), (scenario_id, claim_ids)

    # requested 可保留输入顺序（不比较其顺序，仅核对集合）。
    assert sorted(_selection(result_a).get("requested") or []) == [
        "no_pa", "pa_self"
    ]
    assert sorted(_selection(result_b).get("requested") or []) == [
        "no_pa", "pa_self"
    ]


def test_002_pa_assessment_block_order_validator_controls() -> None:
    """D4-A-A2/AQA-08（A2=1 再授权；ora-22 方向②）：新校验
    `_assert_assessment_block_order` 的 helper 级**正/反双向直接控制**——
    证据不依赖整链到达产品输出（当前红态下 reorder 节点先败于候选顺序）。

    规格锚点：Approved005:738（年度→候选固定顺序→单位 ID）；反例基准＝
    ora-22 静态反例 A:u2→B:v1→A:u1（三单元素块，旧「连续片段升序」漏检）。
    """
    expected = ("A", "B")
    # 正例：块唯一、按候选固定顺序、各 scenario 合并后单位 ID 升序 → 通过。
    _assert_assessment_block_order(
        [("A", ["u1", "u2"]), ("B", ["v1"])], expected)
    # 反例 1（ora-22 静态反例）：A:u2→B:v1→A:u1——同一 scenario 分段再现／
    # 合并后倒序，必须失败（块唯一性先行）。
    try:
        _assert_assessment_block_order(
            [("A", ["u2"]), ("B", ["v1"]), ("A", ["u1"])], expected)
    except AssertionError as exc:
        assert ("分段再现" in str(exc)) or ("升序" in str(exc)), str(exc)
    else:
        raise AssertionError(
            "A:u2→B:v1→A:u1 必须被新校验拒绝（ora-22 反例；Approved005:738）"
        )
    # 反例 2：块顺序颠倒（B→A）→ 候选固定顺序子序校验必须失败。
    try:
        _assert_assessment_block_order(
            [("B", ["v1"]), ("A", ["u1", "u2"])], expected)
    except AssertionError as exc:
        assert "候选顺序" in str(exc), str(exc)
    else:
        raise AssertionError("assessment 块顺序颠倒必须被拒绝（§14 候选固定顺序）")
    # 反例 3：再现但合并后仍升序（A:u1→B:v1→A:u2）→ 块唯一性校验必须失败
    # （证明「保护块顺序」有独立判别力，非仅靠合并升序）。
    try:
        _assert_assessment_block_order(
            [("A", ["u1"]), ("B", ["v1"]), ("A", ["u2"])], expected)
    except AssertionError as exc:
        assert "分段再现" in str(exc), str(exc)
    else:
        raise AssertionError(
            "同一 scenario 分段再现（合并后仍升序）也必须被拒绝（§14）"
        )
    # 反例 4：单块内单位倒序（A:u2,u1）→ 按 scenario 合并升序校验必须失败。
    try:
        _assert_assessment_block_order(
            [("A", ["u2", "u1"]), ("B", ["v1"])], expected)
    except AssertionError as exc:
        assert "升序" in str(exc), str(exc)
    else:
        raise AssertionError("scenario 内单位 ID 倒序必须被拒绝（§14 单位 ID 升序）")


# ---------------------------------------------------------------------------
# Wave B / F3（R3）：缺失≠零、来源消费者与 PA 组件完整性（最小新增 Red）
# ---------------------------------------------------------------------------


def _f3_question(
    result: Mapping, year: str, field_exact: str, *, subject_id: str = "p1"
) -> dict:
    """F3 定位断言：field 必须**全等**，year/subject_id 必须精确。"""
    for q in result.get("questions") or []:
        if (
            q.get("field") == field_exact
            and q.get("year") == year
            and q.get("subject_id") == subject_id
        ):
            return q
    raise AssertionError(
        f"questions 须含 field 全等 {field_exact!r}、year={year!r}、"
        f"subject_id={subject_id!r} 的定位项（005 §13），实际："
        f"{result.get('questions')!r}"
    )


def _f3_no_scenario(result: Mapping, scenario_id: str) -> None:
    steps = [
        s for s in (result.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == "scenario"
    ]
    found = [
        s for s in steps
        if (s.get("detail") or {}).get("scenario_id") == scenario_id
    ]
    assert not found, f"未决候选不得留下 {scenario_id!r} 的确定 scenario，实际：{found!r}"


def test_002_f3_missing_participant_finance_not_zero_both_engines() -> None:
    """F3(a)：整人 finance 缺失（financials={}）≠ `{p1:{}}` ≠ 显式零。

    规格锚点：005 §4.6/§6.1（缺省不等于零；financials 键可缺席，canonical
    输入不补 pID/零值）。修复前：整人 finance 缺失被 `_finance().get(...,0)`
    当作零，薪俸/PA 均给出确定税额且无追问；缺失条目必须产生 NAI/MPF 活动
    缺失项，令相关候选未决、best=null、无确定 scenario、金额全 null。
    """
    year = "2025_26"
    ref = "2026-06-01"

    def salary_with(mode: str) -> dict:
        v2 = _pa_input(year, _subjects_single())
        if mode == "missing":
            v2["years"][year]["financials"] = {}
        elif mode == "empty_entry":
            v2["years"][year]["financials"] = {"p1": {}}
        else:
            v2["years"][year]["financials"] = {
                "p1": _fin(nai="0", mpf="0", ai=False)
            }
        return _run(v2)

    for mode in ("missing", "empty_entry"):
        result = salary_with(mode)
        assert result["status"] == "needs_input", (mode, result["status"])
        _assert_amounts_all_null(result)
        _f3_question(
            result, year, f"years.{year}.financials.p1.employment_income_nai"
        )
        _f3_question(
            result, year,
            f"years.{year}.financials.p1.mpf_mandatory_contributions",
        )
        selection = _selection(result)
        assert _check(selection, "salary_separate")["admission"] == "unresolved", mode
        assert selection.get("best") is None, mode
        _f3_no_scenario(result, "salary_separate")

    zero_control = salary_with("zero")
    assert zero_control["status"] == "partial", zero_control["status"]
    assert zero_control["amounts"]["final_after_reduction"] == "0"
    assert not (zero_control.get("questions") or []), zero_control.get("questions")

    def pa_with(mode: str) -> dict:
        v2 = _pa_input(
            year,
            _subjects_single(),
            subject_facts={
                "p1": {"ordinarily_resident_hk": _resident_at_reference()}
            },
            assessment={
                "basis": "hypothetical",
                "reference_date": ref,
                "candidates": ["no_pa", "pa_self"],
                "selection": "best",
            },
        )
        if mode == "missing":
            v2["years"][year]["financials"] = {}
        elif mode == "empty_entry":
            v2["years"][year]["financials"] = {"p1": {}}
        else:
            v2["years"][year]["financials"] = {
                "p1": _fin(nai="0", mpf="0", ai=False)
            }
        return _run_pa(v2)

    for mode in ("missing", "empty_entry"):
        result = pa_with(mode)
        assert result["status"] == "needs_input", (mode, result["status"])
        _assert_amounts_all_null(result)
        _f3_question(
            result, year, f"years.{year}.financials.p1.employment_income_nai"
        )
        _f3_question(
            result, year,
            f"years.{year}.financials.p1.mpf_mandatory_contributions",
        )
        selection = _selection(result)
        assert _check(selection, "no_pa")["admission"] == "unresolved", mode
        assert _check(selection, "pa_self")["admission"] == "unresolved", mode
        assert selection.get("best") is None, mode
        _f3_no_scenario(result, "no_pa")
        _f3_no_scenario(result, "pa_self")

    pa_zero = pa_with("zero")
    assert pa_zero["status"] == "partial", pa_zero["status"]
    assert pa_zero["amounts"]["final_after_reduction"] == "0"
    assert not (pa_zero.get("questions") or []), pa_zero.get("questions")


def test_002_f3_positive_nai_consumes_salary_source_classification() -> None:
    """F3(b)：正 NAI 已进入薪俸计算 → salary 来源分类缺失/unknown/manual 必须
    被消费；§4.6 存在性（AI）条件只绑定夫妻法定判定，不豁免单身。

    规格锚点：005 §4.6/§6.1（来源缺失/未确认≠已确认；AI 缺席不得反推来源）。
    修复前：活动性只看 `ai is True`，单身 AI 缺席时正 NAI 仍按确定额 940；
    修复后非 confirmed 分类 → 未决、定位 source_classification.salary。
    """
    year = "2025_26"

    def run_with(classification: str | None) -> dict:
        finance = _fin()
        finance.pop("has_assessable_salary_income")
        if classification is None:
            finance.pop("source_classification")
        else:
            finance["source_classification"] = {"salary": classification}
        v2 = _pa_input(year, _subjects_single())
        v2["years"][year]["financials"] = {"p1": finance}
        return _run(v2)

    control = run_with("confirmed_in_scope")
    assert control["status"] == "partial", control["status"]
    assert control["amounts"]["final_after_reduction"] == "940", control["amounts"]
    assert not (control.get("questions") or []), control.get("questions")

    for classification in (None, "unknown", "manual"):
        result = run_with(classification)
        assert result["status"] == "needs_input", (classification, result["status"])
        _assert_amounts_all_null(result)
        _f3_question(
            result, year,
            f"years.{year}.financials.p1.source_classification.salary",
        )
        selection = _selection(result)
        assert _check(selection, "salary_separate")["admission"] == "unresolved", (
            classification
        )
        assert selection.get("best") is None, classification
        _f3_no_scenario(result, "salary_separate")


def test_002_f3_pa_component_arrays_omitted_not_empty() -> None:
    """F3(c)：PA 组件完整性不以 presence 先声明才启用——来源已确认但组件数组
    省略 ≠ 显式 `[]`；省略不得当作空数组继续算确定税额。

    规格锚点：005 §4.6（用户提交的物业/业务组件；省略≠[]）＋§6.1。
    修复前：`active` 只看 presence/entries，来源已确认而数组省略时静默按 []
    计算；修复后必须定位 `financials.p1.<kind>s` 并令 PA 候选未决。
    """
    year = "2025_26"
    ref = "2026-06-01"

    def run_property(omit: bool) -> dict:
        finance = _fin()
        finance["source_classification"] = {
            "salary": "confirmed_in_scope",
            "property": "confirmed_in_scope",
        }
        if omit:
            finance.pop("properties")
        else:
            finance["properties"] = []
        v2 = _pa_input(
            year,
            _subjects_single(),
            subject_facts={
                "p1": {"ordinarily_resident_hk": _resident_at_reference()}
            },
            assessment={
                "basis": "hypothetical",
                "reference_date": ref,
                "candidates": ["no_pa", "pa_self"],
                "selection": "best",
            },
        )
        v2["years"][year]["financials"] = {"p1": finance}
        return _run_pa(v2)

    omitted = run_property(True)
    assert omitted["status"] == "needs_input", omitted["status"]
    _assert_amounts_all_null(omitted)
    _f3_question(omitted, year, f"years.{year}.financials.p1.properties")
    selection = _selection(omitted)
    assert _check(selection, "no_pa")["admission"] == "unresolved"
    assert _check(selection, "pa_self")["admission"] == "unresolved"
    assert selection.get("best") is None
    _f3_no_scenario(omitted, "no_pa")
    _f3_no_scenario(omitted, "pa_self")

    explicit = run_property(False)
    assert explicit["status"] == "partial", explicit["status"]
    assert not (explicit.get("questions") or []), explicit.get("questions")

    def run_business(omit: bool) -> dict:
        finance = _fin()
        finance["source_classification"] = {
            "salary": "confirmed_in_scope",
            "business": "confirmed_in_scope",
        }
        if omit:
            finance.pop("businesses")
        else:
            finance["businesses"] = []
        v2 = _pa_input(
            year,
            _subjects_single(),
            subject_facts={
                "p1": {"ordinarily_resident_hk": _resident_at_reference()}
            },
            assessment={
                "basis": "hypothetical",
                "reference_date": ref,
                "candidates": ["no_pa", "pa_self"],
                "selection": "best",
            },
        )
        v2["years"][year]["financials"] = {"p1": finance}
        return _run_pa(v2)

    omitted_business = run_business(True)
    assert omitted_business["status"] == "needs_input", omitted_business["status"]
    _assert_amounts_all_null(omitted_business)
    _f3_question(
        omitted_business, year, f"years.{year}.financials.p1.businesses"
    )
    explicit_business = run_business(False)
    assert explicit_business["status"] == "partial", explicit_business["status"]
    assert not (explicit_business.get("questions") or []), (
        explicit_business.get("questions")
    )


def test_002_f3_pa_nonempty_components_require_source_confirmation() -> None:
    """F3(d)：已提交非空物业/业务组件必须触发来源确认与后续检查，即便没有
    预先声明 presence/classification；单独改分类只消除对应组件的追问。

    规格锚点：005 §4.6（组件来源分类为消费前置）＋§6.1（不默认已确认）。
    修复前：非空 entries 在 presence 与 classification 均缺席时被视为不活动，
    直接算确定税额且无追问。
    """
    year = "2025_26"
    ref = "2026-06-01"

    def run_with(property_class: str | None, business_class: str | None) -> dict:
        finance = _fin()
        finance["properties"] = [_shared_property(share="1/1", rent="120000")]
        finance["businesses"] = [
            _partnership_business(share="1/1", profit="100000")
        ]
        classification = {"salary": "confirmed_in_scope"}
        if property_class is not None:
            classification["property"] = property_class
        if business_class is not None:
            classification["business"] = business_class
        finance["source_classification"] = classification
        v2 = _pa_input(
            year,
            _subjects_single(),
            subject_facts={
                "p1": {"ordinarily_resident_hk": _resident_at_reference()}
            },
            assessment={
                "basis": "hypothetical",
                "reference_date": ref,
                "candidates": ["no_pa"],
                "selection": "best",
            },
        )
        v2["years"][year]["financials"] = {"p1": finance}
        return _run_pa(v2)

    missing_both = run_with(None, None)
    assert missing_both["status"] == "needs_input", missing_both["status"]
    _assert_amounts_all_null(missing_both)
    _f3_question(
        missing_both, year,
        f"years.{year}.financials.p1.source_classification.property",
    )
    _f3_question(
        missing_both, year,
        f"years.{year}.financials.p1.source_classification.business",
    )
    selection = _selection(missing_both)
    assert _check(selection, "no_pa")["admission"] == "unresolved"
    assert selection.get("best") is None
    _f3_no_scenario(missing_both, "no_pa")

    property_only = run_with("confirmed_in_scope", None)
    assert property_only["status"] == "needs_input", property_only["status"]
    fields = [q.get("field") for q in property_only.get("questions") or []]
    assert (
        f"years.{year}.financials.p1.source_classification.property" not in fields
    ), fields
    _f3_question(
        property_only, year,
        f"years.{year}.financials.p1.source_classification.business",
    )

    both_confirmed = run_with("confirmed_in_scope", "confirmed_in_scope")
    assert both_confirmed["status"] == "partial", both_confirmed["status"]
    assert not (both_confirmed.get("questions") or []), (
        both_confirmed.get("questions")
    )


def test_002_f3_pa_rates_unknown_keeps_source_confirmation() -> None:
    """F3(e)：正差饷 unknown 不得被静默按「不扣差饷」继续；来源未确认时，
    来源确认追问与差饷追问同时存在（不得互相遮蔽）。

    规格锚点：005 §4.6（差饷 agreed/actually_paid 三态）＋§6.1。
    修复前：非空 entries 且来源/存在性都未声明时整个组件被跳过，两个追问
    均消失并输出确定税额。
    """
    year = "2025_26"
    ref = "2026-06-01"

    def run_with(source: str | None, rates: dict) -> dict:
        finance = _fin()
        prop = _shared_property(share="1/1", rent="120000")
        prop["rates_paid_by_owner"] = rates
        finance["properties"] = [prop]
        classification = {"salary": "confirmed_in_scope"}
        if source is not None:
            classification["property"] = source
        finance["source_classification"] = classification
        v2 = _pa_input(
            year,
            _subjects_single(),
            subject_facts={
                "p1": {"ordinarily_resident_hk": _resident_at_reference()}
            },
            assessment={
                "basis": "hypothetical",
                "reference_date": ref,
                "candidates": ["no_pa"],
                "selection": "best",
            },
        )
        v2["years"][year]["financials"] = {"p1": finance}
        return _run_pa(v2)

    unknown_rates = {"amount": "5000", "agreed": "unknown", "actually_paid": False}
    no_source = run_with(None, unknown_rates)
    assert no_source["status"] == "needs_input", no_source["status"]
    _assert_amounts_all_null(no_source)
    _f3_question(
        no_source, year,
        f"years.{year}.financials.p1.source_classification.property",
    )
    _f3_question(
        no_source, year,
        f"years.{year}.financials.p1.properties.h1.rates_paid_by_owner",
    )
    selection = _selection(no_source)
    assert _check(selection, "no_pa")["admission"] == "unresolved"
    assert selection.get("best") is None
    _f3_no_scenario(no_source, "no_pa")

    source_confirmed = run_with("confirmed_in_scope", unknown_rates)
    assert source_confirmed["status"] == "needs_input", source_confirmed["status"]
    confirmed_fields = [
        q.get("field") for q in source_confirmed.get("questions") or []
    ]
    assert (
        f"years.{year}.financials.p1.source_classification.property"
        not in confirmed_fields
    ), confirmed_fields
    _f3_question(
        source_confirmed, year,
        f"years.{year}.financials.p1.properties.h1.rates_paid_by_owner",
    )

    known_rates = {"amount": "0", "agreed": False, "actually_paid": False}
    rates_control = run_with("confirmed_in_scope", known_rates)
    assert rates_control["status"] == "partial", rates_control["status"]
    assert not (rates_control.get("questions") or []), rates_control.get("questions")


def test_002_f3_single_salary_ignores_unrelated_components_and_next_keeps_current() -> None:
    """F3(f)：单身薪俸不因无关组件完全缺省而阻断；下一年缺资料只抑 next，
    current 已知税额保留（不得把缺失下一年当作零）。

    规格锚点：005 §4.6（仅依赖路径消费）＋§11（next 缺事实＝未估算）。
    第二段修复前：下一年整人 finance 缺失 → next 被当作 0 输出，而 current
    正确额保留；修复后 next_provisional=None。
    """
    year = "2025_26"
    finance = _fin()
    finance.pop("properties")
    finance.pop("businesses")
    finance["source_classification"] = {"salary": "confirmed_in_scope"}
    v2 = _pa_input(year, _subjects_single())
    v2["years"][year]["financials"] = {"p1": finance}
    salary = _run(v2)
    assert salary["status"] == "partial", salary["status"]
    assert salary["amounts"]["final_after_reduction"] == "940", salary["amounts"]
    assert not (salary.get("questions") or []), salary.get("questions")

    current = "2024_25"
    following = "2025_26"
    v2_next = {
        "year_of_assessment": current,
        "subjects": _subjects_single(),
        "years": {
            current: _base_year(current, financials={"p1": _fin()}),
            following: _base_year(following, financials={"p1": _fin()}),
        },
        "hk_presence_days": [],
        "assumptions": [],
    }
    v2_next["years"][following]["financials"] = {}
    result = _run(v2_next)
    assert result["status"] == "partial", result["status"]
    assert result["amounts"]["final_after_reduction"] == "2440", result["amounts"]
    assert result["amounts"]["next_provisional"] is None, result["amounts"]
    assert not (result.get("questions") or []), result.get("questions")

# ---------------------------------------------------------------------------
# F4/R4 新增 Red（Wave B2）：actual PA 有效模式完整期望
# ---------------------------------------------------------------------------


def _f4_pa_self_common(year: str) -> dict:
    resident_on_election = [
        {"from": "2025-06-01", "to": "2025-06-02", "value": True}
    ]
    return {
        "household": _s6_household(year),
        "participants": _COUPLE_PARTICIPANTS,
        "subject_facts": {
            "p1": {"ordinarily_resident_hk": resident_on_election},
            "p2": {"ordinarily_resident_hk": resident_on_election},
        },
        "financials": {
            "p1": _fin(nai="240000", mpf="9000", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
    }


def test_002_f4_actual_pa_self_requires_spouse_none_mode() -> None:
    """F4/R4：actual pa_self 的完整期望＝self individual＋spouse none。

    005 §4.10 规则 7＋§9.3 pa_self 行：配偶条目缺键/unknown 不得由下游
    派生 none 掩盖；未决须定位配偶 PA 条目（带 year/subject）。
    """
    year = "2025_26"
    common = _f4_pa_self_common(year)

    def facts(pa_entries: dict) -> dict:
        return _pa_input(
            year,
            _subjects_couple(),
            assessment={
                "basis": "actual",
                "actual": {
                    "salary_joint_elected": False,
                    "salary_joint_assessed": False,
                    "history": "ordinary",
                    "pa": pa_entries,
                },
                "candidates": ["pa_self"],
                "selection": "pa_self",
            },
            **common,
        )

    self_entry = {"mode": "individual", "date": "2025-06-01"}
    control = _run_pa(facts({"p1": self_entry, "p2": {"mode": "none"}}))
    assert control["status"] == "partial", control["status"]
    assert _check(_selection(control), "pa_self").get("admission") == "eligible"

    for label, entry, reason in (
        ("missing", None, "ACTUAL_PA_MODE_MISSING"),
        ("unknown", {"mode": "unknown"}, "ACTUAL_PA_MODE_UNKNOWN"),
    ):
        entries = {"p1": self_entry}
        if entry is not None:
            entries["p2"] = entry
        result = _run_pa(facts(entries))
        assert result["status"] == "needs_input", (label, result["status"])
        _assert_amounts_all_null(result)
        check = _check(_selection(result), "pa_self")
        assert check.get("admission") == "unresolved", (label, check)
        assert reason in (check.get("reason_codes") or []), (label, check)
        assert _selection(result).get("best") is None, label
        assert not [
            detail for detail in _steps(result, "scenario")
            if detail.get("scenario_id") == "pa_self"
        ], (label, "未决 pa_self 不得留下确定 scenario")
        question = _question_for(
            result, year, "assessment", "actual", "pa", "p2", subject_id="p2"
        )
        assert question.get("year") == year, (label, question)
        assert not [
            detail for detail in _steps(result, "assessment")
            if detail.get("unit_id") == "pa:p1"
        ], (label, "未决 pa_self 不得留下 PA 单位税额")


def test_002_f4_actual_pa_self_spouse_individual_not_overridden() -> None:
    """F4/R4 已知反证：spouse=individual＋合法日期不得被派生 none 覆盖。

    005 §9.3 pa_self 行＋P5：实际配偶 individual 与候选期望 spouse none
    冲突 → 仅该候选 unresolved，不得静默按 none 计算。
    """
    year = "2025_26"
    result = _run_pa(_pa_input(
        year,
        _subjects_couple(),
        assessment={
            "basis": "actual",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "history": "ordinary",
                "pa": {
                    "p1": {"mode": "individual", "date": "2025-06-01"},
                    "p2": {"mode": "individual", "date": "2025-06-01"},
                },
            },
            "candidates": ["pa_self"],
            "selection": "pa_self",
        },
        **_f4_pa_self_common(year),
    ))
    assert result["status"] == "needs_input", result["status"]
    _assert_amounts_all_null(result)
    check = _check(_selection(result), "pa_self")
    assert check.get("admission") == "unresolved", check
    assert "ACTUAL_PA_MODE_CONFLICT" in (check.get("reason_codes") or []), check
    assert _selection(result).get("best") is None, _selection(result)
    assert not [
        detail for detail in _steps(result, "scenario")
        if detail.get("scenario_id") == "pa_self"
    ], "冲突 pa_self 不得留下确定 scenario"
    assert not [
        detail for detail in _steps(result, "assessment")
        if detail.get("unit_id") == "pa:p1"
    ], "不得把实际 spouse=individual 覆盖为派生 none 后计算 PA 单位"


def test_002_f4_actual_no_pa_requires_salary_joint_elected() -> None:
    """F4/R4：actual no_pa 模式核对须看到 salary_joint_elected。

    005 §4.10 依赖表第 5 行＋§9.3 no_pa 行：PA modes=none 不能派生
    separate 成功；缺失/unknown 须 unresolved，而非静默按 separate 计算。
    """
    year = "2025_26"
    common = {
        "household": _s6_household(year),
        "participants": _COUPLE_PARTICIPANTS,
        "financials": {
            "p1": _fin(nai="240000", mpf="9000", ai=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True),
        },
    }

    def facts(actual: dict) -> dict:
        return _pa_input(
            year,
            _subjects_couple(),
            assessment={
                "basis": "actual",
                "actual": actual,
                "candidates": ["no_pa"],
                "selection": "no_pa",
            },
            **common,
        )

    pa_none = {"p1": {"mode": "none"}, "p2": {"mode": "none"}}
    control = _run_pa(facts({
        "salary_joint_elected": False,
        "salary_joint_assessed": False,
        "history": "ordinary",
        "pa": pa_none,
    }))
    assert control["status"] == "partial", control["status"]
    assert _check(_selection(control), "no_pa").get("admission") == "eligible"

    for label, elected in (("missing", None), ("unknown", "unknown")):
        actual = {
            "salary_joint_assessed": False,
            "history": "ordinary",
            "pa": pa_none,
        }
        if elected is not None:
            actual["salary_joint_elected"] = elected
        result = _run_pa(facts(actual))
        assert result["status"] == "needs_input", (label, result["status"])
        _assert_amounts_all_null(result)
        check = _check(_selection(result), "no_pa")
        assert check.get("admission") == "unresolved", (label, check)
        assert "ACTUAL_SALARY_JOINT_ELECTED_REQUIRED" in (
            check.get("reason_codes") or []
        ), (label, check)
        assert _selection(result).get("best") is None, label
        assert not [
            detail for detail in _steps(result, "scenario")
            if detail.get("scenario_id") == "no_pa"
        ], (label, "未决 no_pa 不得留下确定 scenario")
        _question_for(result, year, "salary_joint_elected")


def test_002_f4_actual_pa_self_retains_assessed_and_s41_controls() -> None:
    """F4/R4：保留 salary_joint_assessed 缺失/unknown 与 s.41(1B) 控制。

    assessed 缺失/unknown → actual 选择状态未决（定位 salary_joint_assessed）；
    assessed=true → 单独 PA 仍按 s.41(1B) ineligible（不得混为 unknown）。
    """
    year = "2025_26"
    common = _f4_pa_self_common(year)

    def facts(assessed) -> dict:
        actual = {
            "salary_joint_elected": False,
            "history": "ordinary",
            "pa": {
                "p1": {"mode": "individual", "date": "2025-06-01"},
                "p2": {"mode": "none"},
            },
        }
        if assessed is not None:
            actual["salary_joint_assessed"] = assessed
        return _pa_input(
            year,
            _subjects_couple(),
            assessment={
                "basis": "actual",
                "actual": actual,
                "candidates": ["pa_self"],
                "selection": "pa_self",
            },
            **common,
        )

    for label, assessed in (("missing", None), ("unknown", "unknown")):
        result = _run_pa(facts(assessed))
        assert result["status"] == "needs_input", (label, result["status"])
        _assert_amounts_all_null(result)
        check = _check(_selection(result), "pa_self")
        assert check.get("admission") == "unresolved", (label, check)
        assert "ACTUAL_SELECTION_STATE_UNRESOLVED" in (
            check.get("reason_codes") or []
        ), (label, check)
        question = _question_for(
            result, year, "salary_joint_assessed", subject_id="p1"
        )
        assert question.get("year") == year, (label, question)

    blocked = _run_pa(facts(True))
    blocked_check = _check(_selection(blocked), "pa_self")
    assert blocked_check.get("admission") == "ineligible", blocked_check
    assert "S41_1B_JOINT_SALARY_BLOCKS_SINGLE_PA" in (
        blocked_check.get("reason_codes") or []
    ), blocked_check
    assert blocked[ "status"] == "blocked", blocked["status"]
    _assert_amounts_all_null(blocked)
