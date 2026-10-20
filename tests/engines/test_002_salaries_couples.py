# -*- coding: utf-8 -*-
"""REQ-7 夫妻薪俸税：两遍接纳、归属矩阵与婚姻窗口（002 P1 Red；Annex 005）。

REQ: REQ-7（S002 §8/§9.4；PRD AC-7）。本文件为 SDD §9.4 REQ-7 引擎侧命名节点
（11）＋V006 §5.1（B1，4）＋§5.2（B2，2）＋第五稿 S7 两节点＋提名字段独立性
（005 §4.10）＋P1 r2 定点勘误/补测节点（fixture-audit #6–#9＋completion-plan A：
S7 branch A、actual joint×PA 模式兼容、条件式非权威 label），共 20＋N 个：
  - test_002_s29_branch_a_no_joint_required
  - test_002_both_ai_no_joint_each_basic
  - test_002_joint_legal_b_separate_exceeds_joint
  - test_002_joint_legal_a_without_b_excludes_nothing
  - test_002_joint_illegal_candidate_ineligible_reason
  - test_002_salary_separation_window_not_global_ineligible
  - test_002_clean_marriage_transition_s10_5
  - test_002_partial_child_facts_do_not_trigger_household_manual
  - test_002_marriage_missing_not_default_single
  - test_002_joint_spouse_dda_conditional_admission_no_cycle（T3 手算）
  - test_002_reverse_salary_nominee_no_wrong_grant
  - test_002_sequential_spouse_change_reaches_s7_manual（005 §5.4 fixture）
  - test_002_conflicting_spouse_identity_remains_contradictory
  - test_002_child_dda_follows_child_nominee_without_base_claim
  - test_002_spouse_dda_uses_s29_2_responsible_not_child_nominee
  - test_002_s10a_ambiguous_responsible_only_joint_unresolved
  - test_002_conditional_final_only_candidate_checks_not_scenario
  - test_002_declared_spouse_unknown_marriage_needs_input_not_contradictory
  - test_002_single_parent_anytime_exclusion_unchanged
  - test_002_salary_joint_nomination_fields_independent（005 §4.10 两字段独立）

规格锚点:
  - S002 §5.3（两遍条件式联合与归属；提名字段；T3 计划）＋§8 REQ-7；
  - Annex 005 §4.10（actual/hypothetical salary_joint_nomination 独立；
    Nomination 形状；8 条短路规则）、§5.4（S7 顺序配偶计划 fixture）、
    §7（单亲排除 ANY 语义不变）、§9.1（S1–S7 真值表＋§9.1.1 归属矩阵＋
    §9.1.2 两遍流程＋§9.1.3 T3 手算）、§13（questions 定位；
    S7 locator＝years.<YA>.household.marriage、reason 为计算标签）、
    §14（selection/candidate_checks：admission/legal_path s10a|s10b/responsible_
    person_id/separate_final/conditional_final；未接纳 joint 的 conditional_final
    只进非权威 candidate_checks）、§2.1（v2 签名 availability=）。

期望值来源（禁从生产代码反推）:
  - 免税额：Annex 005 §15.2（附表 4 T1：basic 132,000/married 264,000/DDA
    75,000 等）；税率带：附表 1/2（T1 台账 1.17）；宽减 2025/26 cap 3,000（T1）。
  - T3 手算（S002 §5.3/005 §9.1.3，未执行）：500k/200k、MPF 18k、p2 DDA、
    nominee=p1 → separate 38,500／joint 不计 DDA 47,000／conditional joint
    计 DDA 34,250，(b) 成立；反向 nominee 不得沿用 34,250。
  - 其余数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None)（005 §2.1）。
  - S2 分支 A 无需联合选择；S3 双方 AI 各基本额；S4 经 §9.1.2 两遍接纳
    （(a) 成立不得因 (b) 不成立排除；等号不满足）；S5 ineligible＋原因；
    S6 普通婚姻窗口（一次 clean transition 按 s.10(5) 全年合并不按月缩减）；
    S7 仅依赖路径 manual/unresolved＋locator＋S7_SEQUENTIAL_SPOUSES 计算标签；
    结构负例（未注册/自配/重叠/>1 年度 spouse）→ E_INPUT_CONTRADICTORY；
    婚姻缺失/unknown → needs_input 不默认 single；子女部分年度事实不触发
    夫妻窗口 manual；分居不再全局禁算（仅依赖决定 unresolved）。
  - 本文件对 app.* 延迟导入（测试体内）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


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
        },
    }


# ---------------------------------------------------------------------------
# v2 input 构造（Annex 005 §3/§4/§5.1–§5.4 示例形状）
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
    sole_care: list | None = None,
) -> dict:
    r: dict[str, Any] = {
        "relation_id": rid,
        "actor_id": actor,
        "subject_id": subject,
        "kinship": {"kind": kind, "basis": basis, "during": _tf(year, True)},
    }
    if maintained is not None:
        r["maintained"] = maintained
    if sole_care is not None:
        r["sole_or_predominant_care"] = sole_care
    return r


def _claim(cid: str, kind: str, claimant: str = "p1", subject: str = "d1") -> dict:
    return {"claim_id": cid, "kind": kind, "claimant_id": claimant, "subject_id": subject}


def _subjects_couple(*, p2_birth: str = "1992-02-02",
                     dependants: list[tuple[str, str]] | None = None) -> list[dict]:
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "p2", "birth_date": p2_birth}]
    subjects.extend({"subject_id": sid, "birth_date": birth} for sid, birth in (dependants or []))
    return subjects


def _subjects_single(dependants: list[tuple[str, str]] | None = None) -> list[dict]:
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    subjects.extend({"subject_id": sid, "birth_date": birth} for sid, birth in (dependants or []))
    return subjects


def _s6_household(year: str, *, nomination: dict | None = None) -> dict:
    """S6 形态 2：全域同一婚姻＋全部婚姻期间明确 not apart。"""
    w_from, w_to = _window(year)
    return {
        "marriage": [{"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}],
        "living_apart": _tf(year, False),
        "child_nomination": nomination or {"person_id": None, "status": "none"},
    }


_COUPLE_PARTICIPANTS = [{"person_id": "p1", "role": "self"}, {"person_id": "p2", "role": "spouse"}]


# ---------------------------------------------------------------------------
# 引擎调用（Annex 005 §2.1 v2 签名）
# ---------------------------------------------------------------------------


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


def _bundle_hash(bundle: Mapping) -> str:
    """实际完整束六字段 canonical 内容 hash（C2.8；复核 §五.4 纠错）。"""
    from app.core.hashing import bundle_content_hash  # 延迟导入

    return bundle_content_hash(bundle)


def _run(v2_input: dict) -> dict:
    from app.engines.salaries import calculate_salaries_tax  # 延迟导入：Red=缺 v2 契约

    bundle = _v2_bundle()
    return calculate_salaries_tax(
        v2_input, bundle, availability=_availability(bundle, v2_input["year_of_assessment"])
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


def _steps(result: Mapping, kind: str) -> list[dict]:
    return [
        s.get("detail") or {}
        for s in (result.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == kind and isinstance(s.get("detail"), dict)
    ]


def _decisions(result: Mapping) -> list[dict]:
    return _steps(result, "decision")


def _decision(result: Mapping, **match: Any) -> dict | None:
    for d in _decisions(result):
        if all(d.get(k) == v for k, v in match.items()):
            return d
    return None


def _unit_base(result: Mapping, unit_id: str, name: str) -> str | None:
    for detail in _steps(result, "assessment"):
        if detail.get("unit_id") == unit_id:
            for b in detail.get("bases") or []:
                if b.get("name") == name:
                    return b.get("value")
    return None


def _unit_final(result: Mapping, unit_id: str) -> str | None:
    for detail in _steps(result, "assessment"):
        if detail.get("unit_id") == unit_id:
            amounts = detail.get("amounts") or {}
            return amounts.get("final_after_reduction")
    return None


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


def _scenario_ids(result: Mapping) -> set:
    return {s.get("scenario_id") for s in _steps(result, "scenario")}


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


def _locates(questions: list, needles) -> bool:
    if isinstance(needles, str):
        needles = [needles]
    for q in questions:
        blob = q if isinstance(q, str) else json.dumps(q, ensure_ascii=False, default=str)
        if any(n in blob for n in needles):
            return True
    return False


def _question_for(
    result: Mapping, year: str, *field_needles: str, subject_id: str | None = None,
    field_exact: str | None = None,
) -> dict:
    """按 field/year（可选 subject_id）定位 questions 项（005 §13；CF1 形状）。

    不做整段 JSON 词搜索、不强制文案措辞；field 为目标事实路径。
    给出 `field_exact` 时须与目标事实路径**全等**（处方:54；不接受子串），
    其余调用沿用 `field_needles` 子串定位。
    """
    for q in _structured_questions(result):
        field = q.get("field")
        if not isinstance(field, str) or not field:
            continue
        if q.get("year") != year:
            continue
        if field_exact is not None:
            if field != field_exact:
                continue
        elif not all(needle in field for needle in field_needles):
            continue
        if subject_id is not None and q.get("subject_id") != subject_id:
            continue
        return q
    wanted = (
        f"field 全等 {field_exact!r}" if field_exact is not None
        else f"field 含 {field_needles!r}"
    )
    raise AssertionError(
        f"questions 须含 year={year}、{wanted}"
        f"（subject_id={subject_id!r}）的定位项（005 §13），"
        f"实际：{result.get('questions')!r}"
    )


def _question_found(
    result: Mapping, year: str, *field_needles: str, subject_id: str | None = None,
    field_exact: str | None = None,
) -> bool:
    """非抛出式 `_question_for`：聚合观测用。"""
    try:
        _question_for(result, year, *field_needles, subject_id=subject_id,
                      field_exact=field_exact)
        return True
    except AssertionError:
        return False


def _expect_contradictory(v2_input: dict, label: str) -> None:
    from app.core.errors import AppError  # 延迟导入：Red=缺 v2 契约

    try:
        _run(v2_input)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"{label}应以 E_INPUT_CONTRADICTORY 拒算（005 §4.2 结构优先级②），"
            f"实际 {exc.code!r}"
        )
    else:
        raise AssertionError(f"{label}属结构矛盾，必须拒算而非静默采纳")


# ---------------------------------------------------------------------------
# T3 公共构造（S002 §5.3/005 §9.1.3：500k/200k、MPF 18k、p2 DAA、spouse DDA 申索）
# ---------------------------------------------------------------------------


def _t3_input(*, nominee: str | None, year: str = "2025_26",
              extra_actual: dict | None = None) -> dict:
    """T3 案例：p1 NAI 500,000／p2 NAI 200,000、MPF 各 18,000、双方 AI、
    p2 DAA 全年、仅 p1 申索 p2 的 spouse DDA；hypothetical 比较两候选。"""
    assessment: dict[str, Any] = {
        "basis": "hypothetical",
        "candidates": ["salary_separate", "salary_joint"],
        "selection": "best",
    }
    if nominee is not None:
        assessment["hypothetical_salary_joint_nomination"] = {
            "status": "named", "person_id": nominee,
        }
    if extra_actual:
        assessment["actual"] = extra_actual
    return _salary_input(
        year,
        _subjects_couple(),
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="p2")],
        assessment=assessment,
    )


# ---------------------------------------------------------------------------
# S1–S5 真值表节点
# ---------------------------------------------------------------------------


def test_002_s29_branch_a_no_joint_required() -> None:
    """S2：married＋配偶 AI=false＋未单独选 PA → 已婚额成立，不要求已选联合。

    规格锚点：Annex 005 §9.1 S2＋§9.1.1 行 1＋§4.10 依赖表（分支 A 需配偶
    actual.pa mode）。
    T3：Part 5=264,000 → NCI=240,000−9,000−264,000<0 → final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p2": {"mode": "none"}}}},
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), result.get("questions")
    assert _unit_base(result, "salary:p1", "allowances") == "264000/1", (
        f"分支 A：married 264,000 归申索人并覆盖两人基础额，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


def test_002_both_ai_no_joint_each_basic() -> None:
    """S3：married＋双方 AI＋未选联合 → 分别评税各基本额（无已婚额）。

    规格锚点：Annex 005 §9.1 S3。
    P1 r2 定点修正（A 质量复核 #4）：actual basis 的普通 salary 模式事实
    （`salary_joint_elected=false`）须局部显式，不得靠缺省。
    T3：p1 单位 NCI=99,000 → 3,940−3,000=940。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False}},
    ))
    assert result["status"] == "partial", result["status"]
    assert _unit_base(result, "salary:p1", "allowances") == "132000/1", (
        f"S3：p1 单位仅基本额 132,000，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    assert not [d for d in _decisions(result) if d.get("amount") == "264000"], (
        "S3（s.29 分支均不成立）不得输出已婚免税额 decision"
    )
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]


def test_002_joint_legal_b_separate_exceeds_joint() -> None:
    """S4 合法 joint（(b) 路径）：separate 合计严格大于条件式 joint → 接纳。

    规格锚点：Annex 005 §9.1 S4＋§9.1.2（B:=B前提 AND separate>conditional）。
    T3（005 §9.1.3，未执行）：separate 38,500／joint 不计 DDA 47,000／
    conditional 计 DDA 34,250 → (b) 成立；nominee=p1（s10b）。
    """
    result = _run(_t3_input(nominee="p1"))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "eligible", joint
    assert joint.get("legal_path") == "s10b", joint
    assert joint.get("responsible_person_id") == "p1", joint
    assert joint.get("separate_final") == "38500", joint
    assert joint.get("conditional_final") == "34250", joint
    assert selection.get("best") == "salary_joint", selection
    assert result.get("best_scenario") == "salary_joint", (
        f"34,250<38,500 → joint 为 best，实际 {result.get('best_scenario')!r}"
    )
    # 接纳后条件式 DDA 转正式决策与正式金额（两遍无环的最终态）。
    assert result["amounts"]["final_after_reduction"] == "34250", result["amounts"]
    dda = _decision(result, claim_id="c1", scenario_id="salary_joint")
    assert dda is not None and dda.get("grant") == "allowed" and dda.get("amount") == "75000", (
        f"接纳后 spouse DDA 须转为该候选正式 allowed 决策，实际：{dda!r}"
    )


def test_002_joint_legal_a_without_b_excludes_nothing() -> None:
    """S4（(a) 路径）：A 成立（一方扣除溢出）而 B前提不成立 → joint 仍接纳。

    规格锚点：Annex 005 §9.1.2（A=true → 采用 (a) 派生责任人；B前提=双方
    分别 NCI>0；「(a) 成立不得因 (b) 不成立排除」）＋§9.1.1 行 2。
    T3：p1 480,000／p2 100,000、MPF 各 18,000：p2 NCI=−50,000<0 → A true
    （150,000>100,000）、B前提 false。separate=38,100−3,000+0=35,100；
    joint NCI=580,000−36,000−264,000=280,000 → 16,000+80,000×17%=29,600
    → −3,000=26,600 → 接纳经 (a)，责任人＝原本应课税者 p1。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="480000", mpf="18000", ai=True),
                    "p2": _fin(nai="100000", mpf="18000", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "eligible", (
        f"(a) 成立时不得因 (b) 不成立排除 joint，实际：{joint!r}"
    )
    assert joint.get("legal_path") == "s10a", joint
    assert joint.get("responsible_person_id") == "p1", (
        f"(a) 责任人＝分别路径唯一原本应课税者 p1，实际：{joint!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "26600", result["amounts"]
    assert selection.get("best") == "salary_joint", selection


def test_002_joint_illegal_candidate_ineligible_reason() -> None:
    """S5：选择联合但 (a)(b) 均不成立 → 候选 ineligible＋原因，不作 best。

    规格锚点：Annex 005 §9.1 S5。
    T3：500k/200k、无 DDA：A false（双方 NCI>0）；conditional joint 47,000
    > separate 38,500 → (b) false → ineligible；separate 保留为 best。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "ineligible", joint
    assert joint.get("reason_codes"), "ineligible 候选必须附排除原因（S5）"
    assert selection.get("best") == "salary_separate", selection
    assert "salary_joint" not in _scenario_ids(result), (
        "未接纳候选不得留下 scenario 六键记录"
    )


def test_002_salary_separation_window_not_global_ineligible() -> None:
    """分居事件不再全局禁算：joint 依赖路径 unresolved（非 ineligible），
    separate 已知组件保留。

    规格锚点：Annex 005 §9.1 S6（删除「年度 ANY 分居→joint/分支A 不成立」
    旧硬否决；living_apart 不以年度 ANY 事件全局化）＋S7（依赖路径
    needs_input/manual/unresolved）。

    A 质量复核 #6 扩展（独立已知 separate）：同一事实下显式
    selection=salary_separate → partial、top-level final 940 保留
    （同时 best 仍 null、joint 仍 unresolved；无 claims）。
    """
    year = "2025_26"
    facts = _salary_input(
        year,
        _subjects_couple(),
        household={
            "marriage": [{"from": _window(year)[0], "to": _window(year)[1],
                          "value": "married", "spouse_id": "p2"}],
            # 年内一段分居（后复合）：S6 已核窗口不成立 → S7，仅依赖路径未决。
            "living_apart": [
                {"from": "2025-08-01", "to": "2025-12-01", "value": True},
                {"from": "2025-12-01", "to": _window(year)[1], "value": False},
            ],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    )
    result = _run(facts)
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "unresolved", (
        f"分居属 S7 依赖路径未决（不是旧硬否决的 ineligible），实际：{joint!r}"
    )
    separate = _check(selection, "salary_separate")
    assert separate.get("admission") == "eligible", separate
    assert selection.get("best") is None, (
        "unresolved 竞争者存在 → best=null（005 §9.3）"
    )

    # 扩展（A 质量复核 #6）：显式选中已知 separate → 其 final 保留
    # （partial），joint 仍 unresolved、best 仍 null。
    facts["years"][year]["assessment"]["selection"] = "salary_separate"
    selected = _run(facts)
    assert selected["status"] == "partial", selected["status"]
    assert selected["amounts"]["final_after_reduction"] == "940", (
        f"显式选中已知 separate（NCI 99,000 → 3,940−3,000）须保留 940，"
        f"实际：{selected['amounts']!r}"
    )
    assert _check(_selection(selected), "salary_joint").get("admission") == "unresolved"
    assert _selection(selected).get("best") is None, (
        "即使显式选中 separate，unresolved joint 竞争者仍阻 best（§9.3）"
    )


def test_002_clean_marriage_transition_s10_5() -> None:
    """S6 形态 3：一次未婚→已婚 clean transition → s.10(5) 全年合并不按月缩减。

    规格锚点：Annex 005 §9.1 S6（一次明确 unmarried→同一配偶 married、婚后至
    年末明确 not apart → 按普通路径自动处理）。
    T3：joint（(a) 路径，p2 NCI<0）：married 全额 264,000 → final 26,600
    （同 test_002_joint_legal_a 数值）；若按月份缩减则 base≠264,000。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household={
            "marriage": [
                {"from": _window(year)[0], "to": "2025-09-01", "value": "unmarried"},
                {"from": "2025-09-01", "to": _window(year)[1],
                 "value": "married", "spouse_id": "p2"},
            ],
            "living_apart": [{"from": "2025-09-01", "to": _window(year)[1], "value": False}],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="480000", mpf="18000", ai=True),
                    "p2": _fin(nai="100000", mpf="18000", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "eligible", joint
    assert _unit_base(result, "salary:joint", "allowances") == "264000/1", (
        f"s.10(5) 全年合并：一份已婚额全额 264,000（不按 6/12 缩减），实际："
        f"{_unit_base(result, 'salary:joint', 'allowances')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "26600", result["amounts"]


def test_002_partial_child_facts_do_not_trigger_household_manual() -> None:
    """子女未婚部分年度（上半年已证 true、关系全年 true）→ 已存在同日期
    全 true → SAMEPOINT=ANY 已证 true：child 授额、不追问、不触发夫妻窗口
    manual；真正无已证同点 true（互补时点）才 unknown 追问。

    规格锚点：Annex 005 §9.1 表后注（仅子女未婚/供养/教育为部分年度不触发
    夫妻窗口 manual，照 §7 TF 量词）＋§7（SAMEPOINT=ANY(各谓词同一日期 AND)：
    任一同点全真日期即成立）。
    P1 r1 纠错（复核 §五.2）：原断言「部分覆盖→unknown 追问」与 §7 冲突
    （上半年未婚 true＋全年受供养 true → 同点已证 true）——改为已证 true
    的 eligible 断言；另补互补时点的 unknown 负例。
    T3：Part 5=132,000＋130,000=262,000 → NCI<0 → final 0。
    """
    year = "2025_26"
    subjects = _subjects_couple(dependants=[("d1", "2015-06-01")])
    common = {
        "household": _s6_household(year),
        "participants": _COUPLE_PARTICIPANTS,
        "financials": {"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        "claims": [_claim("c1", "child")],
        "assessment": {"basis": "actual", "candidates": ["salary_separate"],
                       "selection": "salary_separate",
                       "actual": {"salary_joint_elected": False}},
    }

    # (a) 上半年未婚 true＋全年受供养 true → 任一 4–10 月日期同点全真。
    proven = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {"unmarried": [
            {"from": _window(year)[0], "to": "2025-10-01", "value": True},
        ]}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        **common,
    ))
    assert proven["status"] == "partial", (
        f"存在同日期全 true → SAMEPOINT 已证 true（不得降为 unknown 追问），"
        f"实际 {proven['status']!r}"
    )
    assert not (proven.get("questions") or []), (
        f"同点已证 true 不得产生任何追问，实际：{proven.get('questions')!r}"
    )
    decision = _decision(proven, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"部分年度未婚＋全年供养 → SAMEPOINT=ANY true → eligible，实际：{decision!r}"
    )
    assert decision.get("grant") == "allowed" and decision.get("amount") == "130000", (
        decision
    )
    assert _unit_base(proven, "salary:p1", "allowances") == "262000/1"
    assert proven["amounts"]["final_after_reduction"] == "0", proven["amounts"]

    # (b) 互补时点（未婚 true 于上半年、受供养 true 于下半年）→ 无任何同点
    #     已证 true 且存在可能日期 → SAMEPOINT=unknown → 子女级追问。
    disjoint = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {"unmarried": [
            {"from": _window(year)[0], "to": "2025-10-01", "value": True},
        ]}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=[
            {"from": "2025-10-01", "to": _window(year)[1], "value": True},
        ])],
        **common,
    ))
    assert disjoint["status"] == "needs_input", (
        f"互补时点无同点 true → SAMEPOINT=unknown → 追问，实际 "
        f"{disjoint['status']!r}"
    )
    _assert_null_amounts(disjoint)
    questions = _structured_questions(disjoint)
    assert _locates(questions, ["d1", "c1"]), (
        f"追问必须定位子女事实（subject d1/claim c1），实际：{questions!r}"
    )
    assert not _locates(questions, ["household.marriage", "婚姻状况"]), (
        f"子女部分年度事实不得触发夫妻窗口 manual（S7），实际：{questions!r}"
    )


def test_002_marriage_missing_not_default_single() -> None:
    """婚姻缺失（marriage TF 空）→ needs_input，不默认 single。

    规格锚点：Annex 005 §9.1 S1/S7（婚姻缺失/unknown 且已婚额、联合或 PA
    分支可能成立 → needs_input，不得默认 single）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household={
            "marriage": [],  # 空 TF＝婚姻未知
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="200000", mpf="18000", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    assert result["status"] == "needs_input", (
        f"婚姻缺失不得默认 single，实际 {result['status']!r}"
    )
    _assert_null_amounts(result)
    assert _locates(_structured_questions(result),
                    ["household.marriage", "婚姻", "marriage"]), (
        "追问必须定位婚姻事实"
    )


def test_002_joint_spouse_dda_conditional_admission_no_cycle() -> None:
    """两遍条件式联合（无递归/固定点）：第二遍计入 nominee 的 spouse DDA 后
    (b) 成立才接纳；接纳后条件式决策转正式。

    规格锚点：S002 §5.3＋Annex 005 §9.1.2（第二遍建立仅内部有效的假设 joint
    上下文、解析一份 married 及依赖它的 spouse DDA）＋§9.1.3（T3 手算）。
    T3：separate 38,500（S3 下 spouse DDA 不属任何人）；joint 不计 DDA
    47,000；conditional joint 计 DDA 34,250；38,500>34,250 → (b) 成立。
    """
    result = _run(_t3_input(nominee="p1"))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "eligible", joint
    assert joint.get("separate_final") == "38500", joint
    assert joint.get("conditional_final") == "34250", joint
    # 未接纳前的条件式金额 47,000 也须作为非权威检查记录可见（两遍过程留痕）。
    assert result["amounts"]["final_after_reduction"] == "34250", result["amounts"]
    dda = _decision(result, claim_id="c1", scenario_id="salary_joint")
    assert dda is not None and dda.get("grant") == "allowed", dda
    # separate 候选中同一 claim 不得提前授予（无环：第一遍不依赖 joint 决策）。
    sep_dda = _decision(result, claim_id="c1", scenario_id="salary_separate")
    assert sep_dda is None or sep_dda.get("grant") != "allowed", (
        f"separate 候选（S3）不得授予 spouse DDA，实际：{sep_dda!r}"
    )


def test_002_reverse_salary_nominee_no_wrong_grant() -> None:
    """反向提名：DDA 申索不属 (b) 责任人 → 不得沿用 34,250 错误授予。

    规格锚点：Annex 005 §9.1.3 反向案例＋§9.1.1 行 3（spouse DDA 依 salary
    nominee 的 married entitlement）。
    T3：nominee=p2 时 DDA 不计入 conditional joint → 47,000>38,500 →
    (b) false；A false → ineligible。
    """
    result = _run(_t3_input(nominee="p2"))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("conditional_final") != "34250", (
        f"反向提名不得沿用 34,250（错误授予负例），实际：{joint!r}"
    )
    assert joint.get("conditional_final") == "47000", (
        f"DDA 不属责任人 → conditional joint 不计 DDA＝47,000，实际：{joint!r}"
    )
    assert joint.get("admission") == "ineligible", joint
    assert joint.get("responsible_person_id") == "p2", joint
    assert selection.get("best") == "salary_separate", selection
    assert result["amounts"]["final_after_reduction"] == "38500", result["amounts"]
    assert _decision(result, claim_id="c1", scenario_id="salary_joint") is None or (
        _decision(result, claim_id="c1", scenario_id="salary_joint") or {}
    ).get("grant") != "allowed", "未接纳 joint 不得留下已授予 DDA 决策"


# ---------------------------------------------------------------------------
# S7 与结构负例（005 §4.2 结构优先级＋§5.4 计划 fixture）
# ---------------------------------------------------------------------------


def test_002_sequential_spouse_change_reaches_s7_manual() -> None:
    """同一 YA 已注册、排序、非重叠的顺序配偶变化 → S7 manual（非矛盾）；
    locator＝years.<YA>.household.marriage、reason＝S7_SEQUENTIAL_SPOUSES
    （计算标签）；历史配偶不得成为第三 actor；不产伪确定税额。

    规格锚点：Annex 005 §5.4（计划 fixture 原样）＋§4.2 优先级④＋§9.1 S7
    ＋§13（S7 locator/reason 标签）。
    """
    year = "2025_26"
    v2_input = {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1"}, {"subject_id": "p2"}, {"subject_id": "p3"}],
        "years": {year: _base_year(
            year,
            participants=_COUPLE_PARTICIPANTS,  # p1 self＋p2 spouse（年度 actor）
            household={
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
            },
            financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                        "p2": _fin(nai="200000", mpf="18000", ai=True)},
            assessment={"basis": "hypothetical",
                        "actual": {"salary_joint_elected": False,
                                   "salary_joint_assessed": False,
                                   "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
                                   "history": "ordinary"},
                        "hypothetical_salary_joint_nomination": {
                            "status": "named", "person_id": "p1"},
                        "candidates": ["salary_joint"], "selection": "salary_joint"},
        )},
        "hk_presence_days": [],
        "assumptions": [],
    }
    result = _run(v2_input)

    assert result["status"] == "needs_input", (
        f"顺序配偶变化走 S7 manual/unresolved（无 prepared/伪确定税额），"
        f"实际 {result['status']!r}"
    )
    _assert_null_amounts(result)
    questions = _structured_questions(result)
    # W-D3-A/AQA-06；D4-A：salary S7 定位须**全等** field=years.<YA>.household.marriage
    # （处方:54），year/subject_id=p1 精确，不以子串包含代替；不冻结问题全文。
    _question_for(result, year, subject_id="p1",
                  field_exact=f"years.{year}.household.marriage")
    blob = json.dumps(questions, ensure_ascii=False, default=str)
    selection_steps = _steps(result, "selection")
    blob += json.dumps(selection_steps, ensure_ascii=False, default=str)
    assert "S7_SEQUENTIAL_SPOUSES" in blob, (
        f"原因标签 S7_SEQUENTIAL_SPOUSES（计算标签）必须可见，实际：{blob!r}"
    )
    # p3 已注册但非 participant：不得成为第三个评税 actor。
    unit_blobs = json.dumps(_steps(result, "assessment"), ensure_ascii=False, default=str)
    assert "salary:p3" not in unit_blobs and "\"p3\"" not in unit_blobs, (
        f"历史配偶 p3 不得自动成为评税 actor，实际：{unit_blobs!r}"
    )


def test_002_conflicting_spouse_identity_remains_contradictory() -> None:
    """结构负例（005 §4.2 优先级②）：悬空 ID、自身配偶、区间重叠、
    超一名年度 spouse → E_INPUT_CONTRADICTORY（不是 S7、不是 unknown）。

    规格锚点：Annex 005 §5.4 结构负例清单＋§4.2。
    """
    year = "2025_26"
    w_from, w_to = _window(year)
    base_household_nomination = {"person_id": None, "status": "none"}

    # (a) 婚姻引用未注册 p9。
    _expect_contradictory(_salary_input(
        year,
        _subjects_couple(),
        household={"marriage": [{"from": w_from, "to": w_to,
                                 "value": "married", "spouse_id": "p9"}],
                   "living_apart": [], "child_nomination": base_household_nomination},
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
    ), "婚姻引用未注册 subject p9")

    # (b) spouse participant＝p1 自身。
    _expect_contradictory(_salary_input(
        year,
        _subjects_couple(),
        household={"marriage": [{"from": w_from, "to": w_to,
                                 "value": "married", "spouse_id": "p1"}],
                   "living_apart": [], "child_nomination": base_household_nomination},
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p1", "role": "spouse"}],
        financials={"p1": _fin()},
    ), "自身配偶（spouse=p1）")

    # (c) 两个 married 区间重叠（同配偶）。
    _expect_contradictory(_salary_input(
        year,
        _subjects_couple(),
        household={"marriage": [
            {"from": w_from, "to": "2025-10-01", "value": "married", "spouse_id": "p2"},
            {"from": "2025-09-01", "to": w_to, "value": "married", "spouse_id": "p2"},
        ], "living_apart": [], "child_nomination": base_household_nomination},
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
    ), "婚姻区间重叠")

    # (d) 两名年度 spouse participant。
    _expect_contradictory(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "p3", "birth_date": "1993-03-03"}],
        household={"marriage": [{"from": w_from, "to": w_to,
                                 "value": "married", "spouse_id": "p2"}],
                   "living_apart": [], "child_nomination": base_household_nomination},
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"},
                      {"person_id": "p3", "role": "spouse"}],
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
    ), "超过一名年度 spouse participant")


def test_002_declared_spouse_unknown_marriage_needs_input_not_contradictory() -> None:
    """声明 spouse participant 但婚姻全 unknown → 结构有效＋needs_input（S7）。

    规格锚点：Annex 005 §9.1 S7 末句（声明 spouse 但婚姻全 unknown＝结构有效
    ＋needs_input）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household={"marriage": [], "living_apart": [],
                   "child_nomination": {"person_id": None, "status": "none"}},
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    assert result["status"] == "needs_input", (
        f"声明配偶＋婚姻 unknown＝结构有效＋needs_input，实际 {result['status']!r}"
    )
    _assert_null_amounts(result)
    assert _locates(_structured_questions(result),
                    ["household.marriage", "婚姻", "marriage"])


# ---------------------------------------------------------------------------
# 归属矩阵（005 §9.1.1）与两遍流程边界（V006 §5.1）
# ---------------------------------------------------------------------------


def test_002_child_dda_follows_child_nominee_without_base_claim() -> None:
    """child DDA 无基本 claim 也随 child nominee 整组归属。

    规格锚点：Annex 005 §9.1.1 行 1（非分居夫妻先求子女原始资格，再由 child
    nominee 整组持有；无基本 child claim 的 DDA 使用同一归属）。
    T3：DDA 75,000 计入 nominee p2 单位 → salary:p2 base=132,000+75,000。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(dependants=[("d1", "2015-06-01")]),
        household=_s6_household(year, nomination={"person_id": "p2", "status": "agreed"}),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"d1": {"unmarried": _tf(year, True),
                              "govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="d1")],
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False}},
    ))
    assert result["status"] == "partial", result["status"]
    dda = _decision(result, claim_id="c1")
    assert dda is not None and dda.get("grant") == "allowed", dda
    assert dda.get("claimant_id") == "p2", (
        f"child DDA 随整组提名归 nominee p2 持有，实际：{dda!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") == "207000/1", (
        f"DDA 金额须计入 nominee 单位（132,000+75,000），实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "132000/1"


def test_002_spouse_dda_uses_s29_2_responsible_not_child_nominee() -> None:
    """spouse DDA 依 s.29(2) 责任人（分支 A 申索人），不随 child nominee。

    规格锚点：Annex 005 §9.1.1 行 1 末列＋§7 spouse DDA 行；salary nominee 与
    child nominee 是不同字段。
    T3：分支 A → p1 得 married 264,000＋spouse DDA 75,000（base 339,000）；
    child nomination（agreed→p2）不改变该归属。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year, nomination={"person_id": "p2", "status": "agreed"}),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="p2")],
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p2": {"mode": "none"}}}},
    ))
    dda = _decision(result, claim_id="c1")
    assert dda is not None and dda.get("grant") == "allowed", dda
    assert dda.get("claimant_id") == "p1", (
        f"spouse DDA 归 s.29(2) 责任人 p1（不随 child nominee p2），实际：{dda!r}"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "339000/1", (
        f"分支 A：married 264,000＋spouse DDA 75,000 归 p1，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def test_002_s10a_ambiguous_responsible_only_joint_unresolved() -> None:
    """s.10(2)(a) 成立但责任人不能唯一确定（双方均无税）→ 仅 joint 归属
    unresolved；独立已知 separate 不被清空、不猜 self。

    规格锚点：Annex 005 §9.1.1 表后注＋§9.3（selection=best 不能确定时保留
    各已知 scenario，top-level final=null）。
    T3：p1/p2 NAI 各 100,000、MPF 各 5,000 → 双方 NCI=100,000−5,000−132,000
    <0 → A true（137,000>100,000）但无原本应课税者 → 责任人不唯一。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="100000", mpf="5000", ai=True),
                    "p2": _fin(nai="100000", mpf="5000", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "unresolved", (
        f"(a) 责任人不唯一 → 仅该 joint 归属决定 unresolved（不猜 self），"
        f"实际：{joint!r}"
    )
    separate = _check(selection, "salary_separate")
    assert separate.get("admission") == "eligible", separate
    # 独立已知 separate 结果保留（双方各 0 税），不被清空。
    assert _unit_final(result, "salary:p1") == "0", (
        f"已知 separate 组件照常输出，实际：{_unit_final(result, 'salary:p1')!r}"
    )
    assert _unit_final(result, "salary:p2") == "0"
    assert selection.get("best") is None, selection
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]


def test_002_conditional_final_only_candidate_checks_not_scenario() -> None:
    """未接纳 joint 的 conditional_final 只进非权威 candidate_checks：
    不进 scenario 六键/selected/best，不留貌似已授予的 married/DDA decision。

    规格锚点：Annex 005 §9.1.2 末段＋§14（candidate_checks 行）。
    T3：无 DDA 500k/200k → conditional joint 47,000＞separate 38,500 →
    (b) false、A false → ineligible；47,000 只在 candidate_checks。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        assessment={"basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best"},
    ))
    selection = _selection(result)
    joint = _check(selection, "salary_joint")
    assert joint.get("admission") == "ineligible", joint
    assert joint.get("conditional_final") == "47000", joint
    assert "salary_joint" not in _scenario_ids(result), (
        "未接纳 joint 不得有 scenario 记录（条件式金额非已接纳税额）"
    )
    blob = json.dumps(
        [_steps(result, "scenario"), result.get("amounts"), _decisions(result)],
        ensure_ascii=False, default=str,
    )
    assert "47000" not in blob, (
        f"conditional_final 47,000 不得进入 scenario 六键/top-level/decision，"
        f"实际：{blob!r}"
    )
    assert not [d for d in _decisions(result)
                if d.get("scenario_id") == "salary_joint"
                and d.get("grant") == "allowed"], (
        "拒绝候选不得留下貌似已授予的 married/DDA decision"
    )
    # completion-plan A / A 质量复核 #7 / W-D3-A/AQA-07：未接纳 joint 的
    # conditional_final 记录须在**获批可见说明来源**（selection step label／
    # 既有说明载体）同时表达「条件式」与「非已接纳」语义（005 §9.1.2/§14；
    # F005584/735）。不接受机器 reason_codes 替代语义，不读未批准的
    # candidate_checks label/explanation/note；等价文案可，不整句 equals、
    # 不新增解释字段。
    selection_steps = [s for s in (result.get("steps") or [])
                       if s.get("kind") == "selection"]
    label_parts = [str(s.get("label")) for s in selection_steps if s.get("label")]
    semantics = " ".join(label_parts)
    conditional = any(w in semantics for w in ("条件式", "条件比较", "conditional"))
    not_accepted = any(
        w in semantics
        for w in ("非已接纳", "非接纳", "未接纳", "未获接纳", "not accepted",
                  "not_accepted", "not-accepted")
    )
    assert conditional and not_accepted, (
        f"未接纳 joint 的 conditional_final 记录须在获批可见 label/说明同时"
        f"表达「条件式」与「非已接纳」语义（005 §9.1.2/§14；不得用机器码/"
        f"未批准字段替代）；实际：{label_parts!r}"
    )


def test_002_salary_joint_nomination_fields_independent() -> None:
    """actual.salary_joint_nomination 与 hypothetical_salary_joint_nomination
    独立：不得互抄、不得默认（缺所需字段 → 该归属 needs_input/unresolved）。

    规格锚点：Annex 005 §4.10（两 Nomination 字段条件出现；「需归属而缺字段/
    unknown → needs_input」）＋§9.1 S4。
    """
    # (a) hypothetical (b) 路径缺 hypothetical 提名：不得抄用 actual 提名。
    # fixture-audit #9：局部补 ordinary/所需 PA 模式/评税状态，令唯一缺口
    # ＝hypothetical 提名本身（actual 提名仅作不得互抄的干扰项）。
    only_actual = _run(_t3_input(
        nominee=None,
        extra_actual={"salary_joint_nomination": {"status": "named", "person_id": "p1"},
                      "salary_joint_elected": True,
                      "salary_joint_assessed": False,
                      "history": "ordinary",
                      "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}}},
    ))
    joint = _check(_selection(only_actual), "salary_joint")
    assert joint.get("admission") == "unresolved", (
        f"(b) 归属缺 hypothetical 提名 → unresolved（不得默认/互抄 actual），"
        f"实际：{joint!r}"
    )
    assert _selection(only_actual).get("best") is None

    # (b) actual (b) 路径缺 actual 提名：不得抄用 hypothetical 提名。
    year = "2025_26"
    only_hypo = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="p2")],
        assessment={"basis": "actual",
                    # fixture-audit #9：局部补齐 ordinary/PA 模式/评税状态，
                    # 令唯一缺口＝actual 提名缺失（hypothetical 提名仅干扰项）。
                    "actual": {"salary_joint_elected": True,
                               "salary_joint_assessed": False,
                               "history": "ordinary",
                               "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}}},
                    "hypothetical_salary_joint_nomination": {
                        "status": "named", "person_id": "p1"},
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "salary_separate"},
    ))
    joint = _check(_selection(only_hypo), "salary_joint")
    assert joint.get("admission") == "unresolved", (
        f"actual (b) 归属缺 actual 提名 → unresolved（不得抄 hypothetical），"
        f"实际：{joint!r}"
    )


def _s10b_hypo_input(state: str | None) -> dict:
    """s.10(2)(b) hypothetical 提名单变量输入；`None`＝键缺失（missing）。"""
    year = "2025_26"
    facts = _t3_input(nominee=None)
    if state is not None:
        facts["years"][year]["assessment"][
            "hypothetical_salary_joint_nomination"
        ] = {"status": state, "person_id": None}
    return facts


def _s10b_actual_input(state: str | None) -> dict:
    """s.10(2)(b) actual 提名单变量输入（仅请求/选 joint）；`named`＝合法控制
    （p1）、`None`＝键缺失（missing）、其余＝显式状态。"""
    year = "2025_26"
    facts = _salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="p2")],
        assessment={"basis": "actual",
                    "actual": {"salary_joint_elected": True,
                               "salary_joint_assessed": False,
                               "history": "ordinary",
                               "pa": {"p1": {"mode": "none"},
                                      "p2": {"mode": "none"}}},
                    "candidates": ["salary_joint"],
                    "selection": "salary_joint"},
    )
    if state == "named":
        facts["years"][year]["assessment"]["actual"][
            "salary_joint_nomination"
        ] = {"status": "named", "person_id": "p1"}
    elif state is not None:
        facts["years"][year]["assessment"]["actual"][
            "salary_joint_nomination"
        ] = {"status": state, "person_id": None}
    return facts


def _assert_s10b_hypo_control() -> None:
    """合法控制：hypothetical named=p1 → joint eligible（此后仅改目标字段）。"""
    control = _check(_selection(_run(_t3_input(nominee="p1"))), "salary_joint")
    assert control.get("admission") == "eligible", (
        f"合法控制（hypothetical named=p1）须 eligible，实际：{control!r}"
    )


def _assert_s10b_actual_control() -> None:
    """合法控制：actual 仅 joint＋named=p1 → joint eligible。"""
    control = _check(_selection(_run(_s10b_actual_input("named"))), "salary_joint")
    assert control.get("admission") == "eligible", (
        f"合法控制（actual 仅 joint＋named=p1）须 eligible，实际：{control!r}"
    )


def _s10b_hypo_observed(result: Mapping, year: str, *, with_question: bool) -> dict:
    """hypothetical joint 未接纳逐项观测＋已知 separate 38,500 保留（处方:40）。"""
    selection = _selection(result)
    separate_scenario = next(
        (s.get("amounts") or {} for s in _steps(result, "scenario")
         if s.get("scenario_id") == "salary_separate"),
        None,
    )
    observed = {
        "admission": _check(selection, "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "best": selection.get("best"),
        "final": result["amounts"]["final_after_reduction"],
        "separate_final": _check(selection, "salary_separate").get("separate_final"),
        "separate_scenario_final": (separate_scenario or {}).get(
            "final_after_reduction"
        ),
    }
    if with_question:
        observed["question"] = _question_found(
            result, year, "hypothetical", "salary_joint_nomination"
        )
    return observed


def _s10b_actual_observed(result: Mapping, year: str, *, with_question: bool) -> dict:
    """actual（仅请求/选 joint）提名单变量逐项观测；提名字段限定 actual basis。"""
    selection = _selection(result)
    observed = {
        "admission": _check(selection, "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
    }
    if with_question:
        observed["question"] = _question_found(
            result, year, "actual", "salary_joint_nomination"
        )
    return observed


def test_002_s10b_hypothetical_nomination_missing_partial_keeps_separate() -> None:
    """s.10(2)(b) hypothetical 提名 missing 单变量（A 质量复核 #5；
    W-D3-A/AQA-05；D4-A 逐状态独立节点）：先 named=p1 合法控制；仅删目标
    字段 → joint unresolved、无 joint scenario；**已知 separate 38,500 必须
    保留**、best=null、top final=null、status=partial；missing 属需补事实 →
    按 hypothetical basis 提名字段定位（不接受整词 "nomination" 模糊匹配）。"""
    year = "2025_26"
    _assert_s10b_hypo_control()
    result = _run(_s10b_hypo_input(None))
    observed = _s10b_hypo_observed(result, year, with_question=True)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "partial",
        "best": None,
        "final": None,
        "separate_final": "38500",
        "separate_scenario_final": "38500",
        "question": True,
    }, (
        f"hypothetical 提名 missing：joint 未接纳＋已知 separate 38,500 保留＋"
        f"best=null/top final=null/partial＋提名字段定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_s10b_hypothetical_nomination_none_partial_keeps_separate() -> None:
    """s.10(2)(b) hypothetical 提名 none 单变量（A 质量复核 #5；W-D3-A/AQA-05；
    D4-A 逐状态独立节点）：先 named=p1 合法控制；仅改目标字段 → joint
    unresolved、无 joint scenario；已知 separate 38,500 保留、best=null、
    top final=null、status=partial；none 需指定提名人 → 按 hypothetical basis
    提名字段定位。"""
    year = "2025_26"
    _assert_s10b_hypo_control()
    result = _run(_s10b_hypo_input("none"))
    observed = _s10b_hypo_observed(result, year, with_question=True)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "partial",
        "best": None,
        "final": None,
        "separate_final": "38500",
        "separate_scenario_final": "38500",
        "question": True,
    }, (
        f"hypothetical 提名 none：joint 未接纳＋已知 separate 38,500 保留＋"
        f"best=null/top final=null/partial＋提名字段定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_s10b_hypothetical_nomination_unknown_partial_keeps_separate() -> None:
    """s.10(2)(b) hypothetical 提名 unknown 单变量（A 质量复核 #5；
    W-D3-A/AQA-05；D4-A 逐状态独立节点）：先 named=p1 合法控制；仅改目标
    字段 → joint unresolved、无 joint scenario；已知 separate 38,500 保留、
    best=null、top final=null、status=partial；unknown 属需补事实 → 按
    hypothetical basis 提名字段定位（不读 actual 字段）。"""
    year = "2025_26"
    _assert_s10b_hypo_control()
    result = _run(_s10b_hypo_input("unknown"))
    observed = _s10b_hypo_observed(result, year, with_question=True)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "partial",
        "best": None,
        "final": None,
        "separate_final": "38500",
        "separate_scenario_final": "38500",
        "question": True,
    }, (
        f"hypothetical 提名 unknown：joint 未接纳＋已知 separate 38,500 保留＋"
        f"best=null/top final=null/partial＋提名字段定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_s10b_hypothetical_nomination_disputed_partial_keeps_separate() -> None:
    """s.10(2)(b) hypothetical 提名 disputed 单变量（A 质量复核 #5；
    W-D3-A/AQA-05；D4-A 逐状态独立节点）：先 named=p1 合法控制；仅改目标
    字段 → joint unresolved（争议提名不默认）、无 joint scenario；已知
    separate 38,500 保留、best=null、top final=null、status=partial；
    disputed 属 manual，不强制定位。"""
    year = "2025_26"
    _assert_s10b_hypo_control()
    result = _run(_s10b_hypo_input("disputed"))
    observed = _s10b_hypo_observed(result, year, with_question=False)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "partial",
        "best": None,
        "final": None,
        "separate_final": "38500",
        "separate_scenario_final": "38500",
    }, (
        f"hypothetical 提名 disputed：joint 未接纳＋已知 separate 38,500 保留＋"
        f"best=null/top final=null/partial（逐项观测）：{observed!r}"
    )


def test_002_s10b_actual_nomination_missing_needs_input() -> None:
    """s.10(2)(b) actual 提名 missing 单变量（A 质量复核 #5；W-D3-A/AQA-05；
    D4-A 逐状态独立节点）：actual 仅请求/选 joint；先 named=p1 合法控制；
    仅删目标字段 → joint unresolved、无 joint scenario、needs_input；missing
    属需补事实 → 按 actual basis 提名字段定位（不接受整词 "nomination"）。"""
    year = "2025_26"
    _assert_s10b_actual_control()
    result = _run(_s10b_actual_input(None))
    observed = _s10b_actual_observed(result, year, with_question=True)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"actual 提名 missing：joint 未接纳＋needs_input＋提名字段定位"
        f"（逐项观测）：{observed!r}"
    )


def test_002_s10b_actual_nomination_none_needs_input() -> None:
    """s.10(2)(b) actual 提名 none 单变量（A 质量复核 #5；W-D3-A/AQA-05；
    D4-A 逐状态独立节点）：仅改目标字段 → joint unresolved、无 joint
    scenario、needs_input；none 需指定提名人 → 按 actual basis 提名字段定位。"""
    year = "2025_26"
    _assert_s10b_actual_control()
    result = _run(_s10b_actual_input("none"))
    observed = _s10b_actual_observed(result, year, with_question=True)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"actual 提名 none：joint 未接纳＋needs_input＋提名字段定位"
        f"（逐项观测）：{observed!r}"
    )


def test_002_s10b_actual_nomination_unknown_needs_input() -> None:
    """s.10(2)(b) actual 提名 unknown 单变量（A 质量复核 #5；W-D3-A/AQA-05；
    D4-A 逐状态独立节点）：仅改目标字段 → joint unresolved、无 joint
    scenario、needs_input；unknown 属需补事实 → 按 actual basis 提名字段
    定位（不读 hypothetical 字段）。"""
    year = "2025_26"
    _assert_s10b_actual_control()
    result = _run(_s10b_actual_input("unknown"))
    observed = _s10b_actual_observed(result, year, with_question=True)
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"actual 提名 unknown：joint 未接纳＋needs_input＋提名字段定位"
        f"（逐项观测）：{observed!r}"
    )


def test_002_s10b_actual_nomination_disputed_manual() -> None:
    """s.10(2)(b) actual 提名 disputed 单变量（A 质量复核 #5；W-D3-A/AQA-05；
    D4-A 逐状态独立节点）：仅改目标字段 → joint unresolved（争议提名不
    默认）、无 joint scenario；disputed 属 manual，状态限 needs_input/partial，
    不强制定位。"""
    year = "2025_26"
    _assert_s10b_actual_control()
    result = _run(_s10b_actual_input("disputed"))
    observed = _s10b_actual_observed(result, year, with_question=False)
    assert observed["admission"] == "unresolved", observed
    assert observed["scenario"] is False, observed
    assert observed["status"] in ("needs_input", "partial"), (
        f"actual 提名 disputed 属 manual（状态限 needs_input/partial）：{observed!r}"
    )


# ---------------------------------------------------------------------------
# 单亲排除语义不变（V006 §5.2）
# ---------------------------------------------------------------------------


def test_002_single_parent_anytime_exclusion_unchanged() -> None:
    """s.32(2) 单亲排除 ANY(married AND NOT living_apart) 不随 S6/S7 表改动。

    规格锚点：Annex 005 §9.1 表后注（单亲排除保持原样）＋§7 s.32(2) 行。
    """
    year = "2025_26"
    common = {
        "subject_facts": {"d1": {"unmarried": _tf(year, True)}},
        "relations": [_relation(year, "r1", "p1", "d1", maintained=_tf(year, True),
                                sole_care=_tf(year, True))],
        "claims": [_claim("c1", "single_parent", subject="d1")],
    }
    # (a) 年内一段已婚且未分居（后离婚）→ 排除成立（后来分居/离婚不抵消）。
    excluded = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        household={
            "marriage": [
                {"from": _window(year)[0], "to": "2025-10-01",
                 "value": "married", "spouse_id": "p2"},
                {"from": "2025-10-01", "to": _window(year)[1], "value": "unmarried"},
            ],
            "living_apart": [{"from": _window(year)[0], "to": "2025-10-01", "value": False}],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        **common,
    ))
    decision = _decision(excluded, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"ANY(married AND NOT apart)=true 即排除（不随 S6/S7 改动），实际：{decision!r}"
    )

    # (b) 婚内全程分居 → 排除不成立（unmarried 全年对照亦成立）。
    apart = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        household={
            "marriage": [
                {"from": _window(year)[0], "to": "2025-10-01",
                 "value": "married", "spouse_id": "p2"},
                {"from": "2025-10-01", "to": _window(year)[1], "value": "unmarried"},
            ],
            "living_apart": [{"from": _window(year)[0], "to": "2025-10-01", "value": True}],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        **common,
    ))
    decision = _decision(apart, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"婚内全程分居 → 排除不成立 → 单亲额 132,000，实际：{decision!r}"
    )
    assert decision.get("amount") == "132000", decision


# ---------------------------------------------------------------------------
# P1 r1 新 Red（复核 §六.7/§六.8）：活动选择与归属
# ---------------------------------------------------------------------------


def _joint_actual_couple(year: str, *, actual: dict) -> dict:
    """(a) 成立的 joint 事实组（480k/100k、MPF 各 18k；SDD §9.1.2）。

    W-D3-A/AQA-05：actual 局部仅请求/选 joint（不并同请求 separate，
    避免以 separate 已知结果掩盖 joint 依赖观测）。"""
    return _salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="480000", mpf="18000", ai=True),
                    "p2": _fin(nai="100000", mpf="18000", ai=True)},
        assessment={"basis": "actual", "actual": actual,
                    "candidates": ["salary_joint"],
                    "selection": "salary_joint"},
    )


def _joint_actual_mutation(*, omit: str | None = None, **mutation: Any) -> dict:
    """(a) joint 完整基线的单变量 mutation（omit＝删除该键；其余键替换）。

    W-D3-A/AQA-05：actual 局部仅请求/选 joint（不并同请求 separate，避免以
    separate 已知结果掩盖 joint 依赖观测）；本构造器只产生单一变异。
    """
    actual: dict[str, Any] = {
        "salary_joint_elected": True,
        "salary_joint_assessed": False,
        "history": "ordinary",
        "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
    }
    if omit is not None:
        actual.pop(omit, None)
    for key, value in mutation.items():
        actual[key] = value
    return actual


def _assert_joint_actual_control(year: str) -> None:
    """合法控制：完整基线（仅 joint 请求/选中）→ eligible＋确定 joint scenario。"""
    control_result = _run(_joint_actual_couple(year, actual=_joint_actual_mutation()))
    control = _check(_selection(control_result), "salary_joint")
    assert control.get("admission") == "eligible", (
        f"合法控制（完整 actual 依赖）须 eligible，实际：{control!r}"
    )
    assert "salary_joint" in _scenario_ids(control_result), (
        f"合法控制须有确定 joint scenario，实际："
        f"{[s.get('scenario_id') for s in _steps(control_result, 'scenario')]!r}"
    )


def test_002_joint_actual_elected_missing_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：`salary_joint_elected` 缺失（其余齐全）→ joint unresolved、
    无确定 joint scenario、needs_input＋field/year 定位（005 §4.10 依赖表：
    elected 缺失→相关候选未决）。先完整基线控制、再仅删目标字段。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(omit="salary_joint_elected")))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "question": _question_found(result, year, "actual", "salary_joint_elected"),
    }
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"elected 缺失须置 joint unresolved＋needs_input＋定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_joint_actual_elected_unknown_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：`salary_joint_elected=unknown` → joint unresolved、无确定
    joint scenario、needs_input＋定位（005 §4.10 依赖表）。先完整基线控制、
    再仅改目标字段。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(salary_joint_elected="unknown")))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "question": _question_found(result, year, "actual", "salary_joint_elected"),
    }
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"elected unknown 须置 joint unresolved＋needs_input＋定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_joint_actual_pa_mode_missing_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：active PA 模式 p2 键缺（只阻相关候选）→ joint unresolved、
    无确定 joint scenario、needs_input＋定位（005 §4.10 依赖表）。先完整
    基线控制、再仅删 p2 条目。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(pa={"p1": {"mode": "none"}})))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "question": _question_found(result, year, "actual.pa", "p2"),
    }
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"PA 模式 p2 缺失须置 joint unresolved＋needs_input＋定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_joint_actual_pa_mode_unknown_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：active PA 模式 p2=unknown（只阻相关候选）→ joint
    unresolved、无确定 joint scenario、needs_input＋定位（005 §4.10 依赖
    表）。先完整基线控制、再仅改目标字段。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(
            pa={"p1": {"mode": "none"}, "p2": {"mode": "unknown"}})))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "question": _question_found(result, year, "actual.pa", "p2"),
    }
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"PA 模式 p2 unknown 须置 joint unresolved＋needs_input＋定位"
        f"（逐项观测）：{observed!r}"
    )


def test_002_joint_actual_history_missing_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：`history` 缺失（同一 elected=true、assessed=false、pa none
    基线）→ joint unresolved、无确定 joint scenario、needs_input＋定位
    （005 §4.10 依赖表）。先完整基线控制、再仅删目标字段。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(omit="history")))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "question": _question_found(result, year, "actual", "history"),
    }
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"history 缺失须置 joint unresolved＋needs_input＋定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_joint_actual_history_unknown_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：`history=unknown` → joint unresolved、无确定 joint
    scenario、needs_input＋定位（005 §4.10 依赖表）。先完整基线控制、再仅
    改目标字段。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(history="unknown")))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
        "question": _question_found(result, year, "actual", "history"),
    }
    assert observed == {
        "admission": "unresolved",
        "scenario": False,
        "status": "needs_input",
        "question": True,
    }, (
        f"history unknown 须置 joint unresolved＋needs_input＋定位（逐项观测）："
        f"{observed!r}"
    )


def test_002_joint_actual_history_withdrawn_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：`history=withdrawn`（同一 elected=true、assessed=false、
    pa none 基线）→ joint unresolved、无确定 joint scenario；已知非普通
    历史状态限 needs_input/partial，不强制定位（005 §4.10 依赖表）。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(history="withdrawn")))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
    }
    assert observed["admission"] == "unresolved", observed
    assert observed["scenario"] is False, observed
    assert observed["status"] in ("needs_input", "partial"), (
        f"已知非普通 history（withdrawn）限 needs_input/partial：{observed!r}"
    )


def test_002_joint_actual_history_disputed_unresolved() -> None:
    """actual joint 活动选择依赖（A 质量复核 #5；W-D3-A/AQA-05；D4-A 逐例
    独立节点）：`history=disputed`（同一 elected=true、assessed=false、
    pa none 基线）→ joint unresolved、无确定 joint scenario；已知非普通
    历史状态限 needs_input/partial，不强制定位（005 §4.10 依赖表）。"""
    year = "2025_26"
    _assert_joint_actual_control(year)
    result = _run(_joint_actual_couple(
        year, actual=_joint_actual_mutation(history="disputed")))
    observed = {
        "admission": _check(_selection(result), "salary_joint").get("admission"),
        "scenario": "salary_joint" in _scenario_ids(result),
        "status": result["status"],
    }
    assert observed["admission"] == "unresolved", observed
    assert observed["scenario"] is False, observed
    assert observed["status"] in ("needs_input", "partial"), (
        f"已知非普通 history（disputed）限 needs_input/partial：{observed!r}"
    )


def test_002_s10a_conflicting_nomination_rejected() -> None:
    """(a) 路径：提名字段与派生责任人冲突 → E_INPUT_CONTRADICTORY。

    规格锚点：S002 §5.3（「提名字段与派生责任人冲突 → E_INPUT_CONTRADICTORY；
    (a) 责任人服务器派生」）。复核 §三 R4（(a) 冲突提名未核）。
    构造：(a) 成立组（480k/100k）＋actual basis＋elected=true＋
    actual.salary_joint_nomination named p2（≠派生责任人 p1）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    # fixture-audit #9：局部补 ordinary/PA 模式/评税状态，令唯一缺陷＝
    # (a) 派生责任人 p1 与提名 p2 冲突（目标断言）。
    facts = _joint_actual_couple(year, actual={
        "salary_joint_elected": True,
        "salary_joint_assessed": False,
        "history": "ordinary",
        "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
        "salary_joint_nomination": {"status": "named", "person_id": "p2"},
    })
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"(a) 提名与派生责任人冲突须矛盾拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "(a) 派生责任人 p1 与提名 p2 冲突：必须 E_INPUT_CONTRADICTORY"
            "（S002 §5.3），不得静默按任何一方接纳"
        )


def test_002_single_unmarried_separate_does_not_require_inactive_facts() -> None:
    """§4.10 规则 1：明确全年未婚＋仅 salary_separate/basic 不读取 inactive
    actual 字段（actual 对象整体省略仍合法）；不得产生“未选 PA”追问。"""
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_single(),
        household={
            "marriage": [{"from": _window(year)[0], "to": _window(year)[1],
                          "value": "unmarried"}],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate"},
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), result.get("questions")
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]


def _s7_branch_a_baseline_input(year: str) -> dict:
    """S7 顺序配偶＋p2 AI=false 的分支 A 干扰基线（p1 240k/9k、p2 0/false；
    actual 选择/历史/PA 模式齐全，令唯一差异＝S7 婚姻窗口）。"""
    return {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "p2", "birth_date": "1992-02-02"},
                     {"subject_id": "p3", "birth_date": "1993-03-03"}],
        "years": {year: _base_year(
            year,
            participants=_COUPLE_PARTICIPANTS,
            household={
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
            },
            financials={"p1": _fin(nai="240000", mpf="9000", ai=True),
                        "p2": _fin(nai="0", mpf="0", ai=False)},
            assessment={"basis": "actual",
                        "actual": {"salary_joint_elected": False,
                                   "salary_joint_assessed": False,
                                   "pa": {"p1": {"mode": "none"},
                                          "p2": {"mode": "none"}},
                                   "history": "ordinary"},
                        "candidates": ["salary_separate"],
                        "selection": "salary_separate"},
        )},
        "hk_presence_days": [],
        "assumptions": [],
    }


def _s7_branch_a_control_input(year: str) -> dict:
    """同一 subjects/financials，仅把窗口改为普通婚姻（_s6_household）：
    分支 A 基线合法控制——证明后续差异仅来自 S7 顺序配偶窗口，而非来源/
    关系/申报事实（W-D3-A/AQA-06；p3 保留注册但不成为第三 actor）。"""
    facts = _s7_branch_a_baseline_input(year)
    facts["years"][year]["household"] = _s6_household(year)
    return facts


def test_002_s7_sequential_spouse_branch_a_status_needs_input() -> None:
    """S7（A 质量复核 #6 拆分-状态节点；W-D3-A/AQA-06 合法控制）：顺序配偶
    变化下 s.29/婚姻决定不得默认分支 A——状态 needs_input、六金额键 null。

    先同 subjects 跑普通婚姻窗口控制（partial、separate eligible），仅把
    窗口改为顺序配偶；旧实现该函数首失败为 `partial`，金额断言未达；
    拆分后状态与金额各自独立观测。规格锚点：Annex 005 §9.1 S7。"""
    year = "2025_26"
    control = _run(_s7_branch_a_control_input(year))
    assert control["status"] == "partial", (
        f"合法控制（普通婚姻窗口）须 partial 可算，实际 {control['status']!r}"
    )
    assert _check(_selection(control), "salary_separate").get("admission") == "eligible", (
        f"合法控制：salary_separate 须 eligible，实际："
        f"{_check(_selection(control), 'salary_separate')!r}"
    )

    result = _run(_s7_branch_a_baseline_input(year))
    assert result["status"] == "needs_input", (
        f"S7 顺序配偶变化下 s.29/婚姻决定不得默认分支 A，实际 {result['status']!r}"
    )
    _assert_null_amounts(result)


def test_002_s7_sequential_spouse_branch_a_no_married_grant() -> None:
    """S7（A 质量复核 #6 拆分-金额节点；W-D3-A/AQA-06 合法控制）：同一
    基线下不得授予 branch A married 264,000（独立于状态断言的金额事实；
    旧断言被首失败遮蔽）。控制先证普通窗口下 264,000 确会授予。"""
    year = "2025_26"
    control_married = [
        (detail.get("unit_id"), b.get("value"))
        for detail in _steps(_run(_s7_branch_a_control_input(year)), "assessment")
        for b in detail.get("bases") or []
        if b.get("name") == "allowances" and b.get("value") == "264000/1"
    ]
    assert control_married == [("salary:p1", "264000/1")], (
        f"合法控制（普通婚姻窗口）：branch A married 264,000 应授予 salary:p1，"
        f"实际：{control_married!r}"
    )

    result = _run(_s7_branch_a_baseline_input(year))
    married_bases = [
        (detail.get("unit_id"), b.get("value"))
        for detail in _steps(result, "assessment")
        for b in detail.get("bases") or []
        if b.get("name") == "allowances" and b.get("value") == "264000/1"
    ]
    assert not married_bases, (
        f"S7 下不得授予 branch A married 264,000，实际：{married_bases!r}"
    )


def test_002_actual_joint_conflicts_with_individual_pa() -> None:
    """§4.10 模式兼容（salary_joint 实际选择）＋s.41(1B)：已实际 individual
    PA 与联合薪俸评税不能并存 → E_INPUT_CONTRADICTORY，不得静默按 s10a 接纳。

    当前静默 eligible（s10a）→ 目标 Red；不宣称任一选择无效、不回退默认。
    """
    from app.core.errors import AppError

    year = "2025_26"
    facts = _joint_actual_couple(year, actual={
        "salary_joint_elected": True,
        "salary_joint_assessed": False,
        "history": "ordinary",
        "pa": {"p1": {"mode": "individual"}, "p2": {"mode": "none"}},
    })
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"actual individual PA 与联合薪俸选择冲突须矛盾拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "s.41(1B)：已单独选 PA 者不得与 s.10(3) 联合薪俸评税并存"
            "（005 §4.10 模式兼容），不得静默按 s10a 接纳"
        )


def test_002_child_claim_of_claimant_without_own_relation_not_granted() -> None:
    """claimant 自身无关系事实时，不得借用其他 participant 的关系授予
    （relation 须按 claimant 取；复核 §三 R5「relation 取第一条未按 claimant」）。

    规格锚点：Annex 005 §4.5（relation＝actor 与 subject 的直接关系）＋§7
    （s.31 SAMEPOINT 含「claimant 维护」——谓词按申索人评估）。
    构造：S3（双方 AI）夫妻；child 关系仅在 p2 名下；claim 由 p1 提交。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(dependants=[("d1", "2015-06-01")]),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p2", "d1", maintained=_tf(year, True))],
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        claims=[_claim("c1", "child")],
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate"},
    ))
    decision = _decision(result, claim_id="c1")
    assert not (decision is not None and decision.get("grant") == "allowed"), (
        f"p1 对 d1 无关系事实 → 不得借用 p2 的关系授予（复核 R5），实际：{decision!r}"
    )
    # 未证关系＝未知 → 定位到该申索的追问（而非静默按无子女计）。
    assert result["status"] == "needs_input" or (
        decision is not None and decision.get("entitlement") != "eligible"
    ), f"关系事实缺失不得既不追问又授额：{result['status']!r}/{decision!r}"


def test_002_child_claim_granted_to_nominee_without_spouse_claim() -> None:
    """整组提名归属：agreed→p2 且仅 p1 提交 child claim → 金额归 nominee p2
    （claimant_id=p2、计入 salary:p2 单位；复核 §三 R5「child 提名回退」）。

    规格锚点：Annex 005 §9.1.1 行 1（先求各自原始资格，再由 child nominee
    整组持有）＋§8.1 行 2（child_nomination 对账）。
    T3：p1 500k/18k、p2 200k/18k、child 130,000 归 p2 单位
    （salary:p2 allowances=132,000+130,000=262,000）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(dependants=[("d1", "2015-06-01")]),
        household=_s6_household(year, nomination={"person_id": "p2",
                                                  "status": "agreed"}),
        participants=_COUPLE_PARTICIPANTS,
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        claims=[_claim("c1", "child")],  # 仅 p1 提交；归 nominee p2 持有
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False}},
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("grant") == "allowed", decision
    assert decision.get("claimant_id") == "p2", (
        f"整组提名后子女额归 nominee p2 持有（decision.claimant_id），"
        f"实际：{decision!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") == "262000/1", (
        f"子女额须计入 nominee 单位（132,000＋130,000），实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "132000/1"


def test_002_election_issue_id_requires_year_participant() -> None:
    """CR-E: election issue 仅接受该 Year 的 participant pID，且不增 election ID。

    P1 r2 定点勘误（fixture-audit #6）：合法同年 baseline 完全可判（不请求
    joint，缺提名不得成为未决来源）；跨年负例局部把 next spouse 改 p3，
    并逐年补所需配偶 PA none，令唯一缺陷＝本年 manual_issues id 引用 p3。
    """
    from app.core.errors import AppError

    year, target = "2025_26", "2026_27"
    subjects = _subjects_couple() + [{"subject_id": "p3", "birth_date": "1993-03-03"}]

    # 同年 participant p2 为合法 locator：完全可判 baseline（仅
    # salary_separate、双方 AI＋普通 salary 模式/PA none 显式 → S3 各基本额）。
    valid = _salary_input(
        year, subjects,
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                    "p2": _fin(nai="200000", mpf="18000", ai=True)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p1": {"mode": "none"},
                                      "p2": {"mode": "none"}}}},
    )
    valid["years"][year]["manual_issues"] = [
        {"scope": "election", "id": "p2", "code": "dispute"}
    ]
    result = _run(valid)
    assert result["status"] == "partial", (
        f"同年 participant locator 不得结构拒绝或整体未决，实际 {result['status']!r}"
    )
    assert _check(_selection(result), "salary_separate").get("admission") == "eligible", (
        "完全可判 baseline：salary_separate 不依赖被争议选举事实，须保留 eligible"
    )

    # A 质量复核 #4：负例须在**同一 cross-year 输入**上先验证合法（issue
    # id=p2 本年 participant），然后仅把 id 改为 p3（已注册、且为紧邻下一年
    # spouse participant，但不是本年 participant）才矛盾；不得另立不同基线。
    target_household = _s6_household(target)
    target_household["marriage"][0]["spouse_id"] = "p3"  # next actor=p3
    cross_year = {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": {
            year: _base_year(
                year,
                participants=_COUPLE_PARTICIPANTS,
                household=_s6_household(year),
                financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                            "p2": _fin(nai="200000", mpf="18000", ai=True)},
                assessment={"basis": "actual",
                            "candidates": ["salary_separate"],
                            "selection": "salary_separate",
                            "actual": {"salary_joint_elected": False,
                                       "pa": {"p1": {"mode": "none"},
                                              "p2": {"mode": "none"}}}},
            ),
            target: _base_year(
                target,
                participants=[{"person_id": "p1", "role": "self"},
                              {"person_id": "p3", "role": "spouse"}],
                household=target_household,
                financials={"p1": _fin(nai="500000", mpf="18000", ai=True),
                            "p3": _fin(nai="200000", mpf="18000", ai=True)},
                assessment={"basis": "actual",
                            "candidates": ["salary_separate"],
                            "selection": "salary_separate",
                            "actual": {"salary_joint_elected": False,
                                       "pa": {"p1": {"mode": "none"},
                                              "p3": {"mode": "none"}}}},
            ),
        },
        "hk_presence_days": [],
        "assumptions": [],
    }
    cross_year["years"][year]["manual_issues"] = [
        {"scope": "election", "id": "p2", "code": "dispute"}
    ]
    validated = _run(cross_year)
    assert validated["status"] in ("partial", "complete"), (
        f"同一 cross-year 输入（issue id=p2 本年 participant）须可算，"
        f"实际 {validated['status']!r}"
    )
    assert _check(_selection(validated), "salary_separate").get("admission") == "eligible", (
        "cross-year 基线须完全可判（salary_separate independent eligible）"
    )
    cross_year["years"][year]["manual_issues"] = [
        {"scope": "election", "id": "p3", "code": "dispute"}
    ]
    try:
        _run(cross_year)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"跨年 participant p3 不是本年 election locator，实际 {exc.code!r}"
        )
        assert exc.field == f"years.{year}.manual_issues[0].id", exc.field
    else:
        raise AssertionError("election.id 必须引用同一 Year participants 的 pID")


def test_002_election_issue_only_blocks_dependent_candidates() -> None:
    """CR-E: election manual 只阻依赖候选；best 未定但显式 selected/金额保留。

    P1 r2 定点勘误（fixture-audit #7）：先以无 issue 的合法提名/history/
    选择 baseline 证明 joint eligible，再仅加 issue（单一 mutation）；
    admission 只用 unresolved（§14 枚举无 manual）。
    """
    year = "2025_26"

    # baseline 控制（无 issue）：合法提名/history/选择齐备 → joint eligible。
    baseline = _t3_input(nominee="p1")
    baseline["years"][year]["assessment"]["selection"] = "salary_separate"
    baseline_selection = _selection(_run(baseline))
    assert _check(baseline_selection, "salary_joint").get("admission") == "eligible", (
        f"无 issue 的合法 baseline 下 joint 必须 eligible（后一断言才有区分力），"
        f"实际：{_check(baseline_selection, 'salary_joint')!r}"
    )

    facts = _t3_input(nominee="p1")
    facts["years"][year]["assessment"]["selection"] = "salary_separate"
    facts["years"][year]["manual_issues"] = [
        {"scope": "election", "id": "p1", "code": "dispute"}
    ]
    result = _run(facts)
    selection = _selection(result)
    assert _check(selection, "salary_separate").get("admission") == "eligible", selection
    assert _check(selection, "salary_joint").get("admission") == "unresolved", (
        f"依赖该选举选择的 joint 候选须 unresolved（§14 admission 枚举无 manual），"
        f"实际：{_check(selection, 'salary_joint')!r}"
    )
    assert selection.get("best") is None, selection
    assert selection.get("selected") == "salary_separate", selection
    assert result["amounts"]["final_after_reduction"] == "38500", result["amounts"]

# ---------------------------------------------------------------------------
# F4/R4 新增 Red（Wave B2）：actual 有效 salary/PA 模式核对
# ---------------------------------------------------------------------------


def test_002_f4_actual_separate_requires_salary_joint_elected() -> None:
    """F4/R4：ordinary 夫妻 actual 分别评税须核对 salary_joint_elected。

    005 §4.10 依赖表第 5 行：缺失/unknown → 相关候选未决，不得从候选集合或
    PA map 反推“未选联合”。本节点覆盖 S3（双方 AI）控制与两态负例。
    """
    year = "2025_26"
    common = {
        "household": _s6_household(year),
        "participants": _COUPLE_PARTICIPANTS,
        "financials": {"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
    }

    def facts(actual: dict) -> dict:
        return _salary_input(
            year,
            _subjects_couple(),
            assessment={
                "basis": "actual",
                "candidates": ["salary_separate"],
                "selection": "salary_separate",
                "actual": actual,
            },
            **common,
        )

    control = _run(facts({"salary_joint_elected": False}))
    assert control["status"] == "partial", control["status"]
    assert _check(_selection(control), "salary_separate").get("admission") == "eligible"

    field = f"years.{year}.assessment.actual.salary_joint_elected"
    for label, actual in (
        ("missing", {}),
        ("unknown", {"salary_joint_elected": "unknown"}),
    ):
        result = _run(facts(actual))
        assert result["status"] == "needs_input", (label, result["status"])
        _assert_null_amounts(result)
        check = _check(_selection(result), "salary_separate")
        assert check.get("admission") == "unresolved", (label, check)
        assert "ACTUAL_SALARY_JOINT_ELECTED_REQUIRED" in (
            check.get("reason_codes") or []
        ), (label, check)
        assert check.get("separate_final") is None, (label, check)
        assert _selection(result).get("best") is None, label
        assert result.get("best_scenario") is None, label
        assert _scenario_ids(result) == set(), (label, _scenario_ids(result))
        _question_for(result, year, field_exact=field)
        assert not _steps(result, "assessment"), (
            label,
            "未决候选不得留下确定 salary 单位",
        )


def test_002_f4_actual_separate_elected_true_conflict_not_determined() -> None:
    """F4/R4 已知反证：elected=true 与 separate 请求冲突 → 不接纳、不产确定额。

    005 §4.10 规则 8：显式已知选择与请求冲突仍走既有冲突/转换规则；不得静默
    把 separate 当成功，也不得自动改判 joint。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_couple(),
        household=_s6_household(year),
        participants=_COUPLE_PARTICIPANTS,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": True}},
    ))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    check = _check(_selection(result), "salary_separate")
    assert check.get("admission") == "unresolved", check
    assert "ACTUAL_SALARY_MODE_CONFLICT" in (check.get("reason_codes") or []), check
    assert check.get("separate_final") is None, check
    assert _selection(result).get("best") is None, _selection(result)
    assert result.get("best_scenario") is None, result.get("best_scenario")
    assert _scenario_ids(result) == set(), _scenario_ids(result)
    assert not _steps(result, "assessment"), "冲突候选不得留下确定 salary 单位"


def test_002_f4_actual_separate_sparse_and_dda_contexts_require_elected() -> None:
    """F4/R4：稀疏 PA map 与子女 DDA 两上下文，elected 缺失/unknown 均未决。

    005 §4.10 依赖表第 5 行＋§9.1.1 行 1：branch A/DDA 依赖配偶 PA mode，
    但不能以“已提交 p2=none”替代实际 salary 模式；未决时不得留下正式
    DDA allowed 授予或确定 separate 税额。本节点的 DDA 主体是子女 d1
    （不是配偶）；真实配偶 DDA（subject=p2）见下一节点
    `test_002_f4_actual_spouse_dda_requires_salary_joint_elected`。
    """
    year = "2025_26"
    field = f"years.{year}.assessment.actual.salary_joint_elected"
    pa_map = {"p2": {"mode": "none"}}

    def sparse_facts(actual: dict) -> dict:
        return _salary_input(
            year,
            _subjects_couple(),
            household=_s6_household(year),
            participants=_COUPLE_PARTICIPANTS,
            financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
            assessment={"basis": "actual", "candidates": ["salary_separate"],
                        "selection": "salary_separate", "actual": actual},
        )

    def dda_facts(actual: dict) -> dict:
        return _salary_input(
            year,
            _subjects_couple(dependants=[("d1", "2015-06-01")]),
            household=_s6_household(
                year, nomination={"person_id": "p2", "status": "agreed"}
            ),
            participants=_COUPLE_PARTICIPANTS,
            subject_facts={
                "d1": {"unmarried": _tf(year, True),
                       "govt_disability_allowance": _tf(year, True)},
            },
            relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
            financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
            claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="d1")],
            assessment={"basis": "actual", "candidates": ["salary_separate"],
                        "selection": "salary_separate", "actual": actual},
        )

    for label, actual in (
        ("missing", {"pa": pa_map}),
        ("unknown", {"salary_joint_elected": "unknown", "pa": pa_map}),
    ):
        sparse = _run(sparse_facts(actual))
        assert sparse["status"] == "needs_input", (label, sparse["status"])
        _assert_null_amounts(sparse)
        sparse_check = _check(_selection(sparse), "salary_separate")
        assert sparse_check.get("admission") == "unresolved", (label, sparse_check)
        assert _selection(sparse).get("best") is None, label
        assert _scenario_ids(sparse) == set(), (label, _scenario_ids(sparse))
        _question_for(sparse, year, field_exact=field)

        dda = _run(dda_facts(actual))
        assert dda["status"] == "needs_input", (label, dda["status"])
        _assert_null_amounts(dda)
        dda_check = _check(_selection(dda), "salary_separate")
        assert dda_check.get("admission") == "unresolved", (label, dda_check)
        assert _selection(dda).get("best") is None, label
        assert _scenario_ids(dda) == set(), (label, _scenario_ids(dda))
        assert _decision(dda, claim_id="c1") is None, (
            label,
            "未决事实不得留下正式 DDA allowed/其他确定 decision",
        )
        assert _unit_base(dda, "salary:p1", "allowances") is None, (
            label,
            "未决事实不得计算 married/DDA 免税额",
        )
        _question_for(dda, year, field_exact=field)


def test_002_f4_actual_spouse_dda_requires_salary_joint_elected() -> None:
    """F4/R4：真实配偶 DDA（claim subject=p2）下 elected 缺失/unknown/true 均未决。

    005 §4.10 依赖表第 5 行＋§9.1.1 行 1：spouse DDA/分支 A 依赖配偶 PA
    mode，但不得以“已提交 p2=none”替代 actual salary 模式事实。控制：
    elected=false、p2 PA=none 时分支 A 合法，p1 得 married 264,000＋
    spouse DDA 75,000（allowances 339,000）。三个变异均不得留下正式
    DDA decision、确定 salary 单位或 scenario；缺失/unknown 定位
    salary_joint_elected，已知 true 走既有冲突规则（assessment 级定位）。
    """
    year = "2025_26"
    field = f"years.{year}.assessment.actual.salary_joint_elected"

    def spouse_dda_facts(actual: dict) -> dict:
        return _salary_input(
            year,
            _subjects_couple(),
            household=_s6_household(year),
            participants=_COUPLE_PARTICIPANTS,
            subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
            financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
            claims=[_claim("c1", "disabled_dependant", claimant="p1", subject="p2")],
            assessment={"basis": "actual", "candidates": ["salary_separate"],
                        "selection": "salary_separate", "actual": actual},
        )

    pa_map = {"p2": {"mode": "none"}}
    control = _run(spouse_dda_facts({
        "salary_joint_elected": False,
        "pa": pa_map,
    }))
    assert control["status"] == "partial", control["status"]
    assert not (control.get("questions") or []), control.get("questions")
    assert _check(_selection(control), "salary_separate").get("admission") == "eligible"
    dda = _decision(control, claim_id="c1")
    assert dda is not None and dda.get("grant") == "allowed", dda
    assert dda.get("claimant_id") == "p1", (
        f"spouse DDA 归 s.29(2) 责任人 p1，实际：{dda!r}"
    )
    assert dda.get("subject_id") == "p2", dda
    assert _unit_base(control, "salary:p1", "allowances") == "339000/1", (
        f"分支 A：married 264,000＋spouse DDA 75,000 归 p1，实际："
        f"{_unit_base(control, 'salary:p1', 'allowances')!r}"
    )

    for label, actual, reason, question_field in (
        ("missing", {"pa": pa_map}, "ACTUAL_SALARY_JOINT_ELECTED_REQUIRED", field),
        ("unknown", {"salary_joint_elected": "unknown", "pa": pa_map},
         "ACTUAL_SALARY_JOINT_ELECTED_REQUIRED", field),
        ("true", {"salary_joint_elected": True, "pa": pa_map},
         "ACTUAL_SALARY_MODE_CONFLICT", f"years.{year}.assessment"),
    ):
        result = _run(spouse_dda_facts(actual))
        assert result["status"] == "needs_input", (label, result["status"])
        _assert_null_amounts(result)
        check = _check(_selection(result), "salary_separate")
        assert check.get("admission") == "unresolved", (label, check)
        assert reason in (check.get("reason_codes") or []), (label, check)
        assert check.get("separate_final") is None, (label, check)
        assert _selection(result).get("best") is None, label
        assert result.get("best_scenario") is None, label
        assert _scenario_ids(result) == set(), (label, _scenario_ids(result))
        assert _decision(result, claim_id="c1") is None, (
            label,
            "未决事实不得留下正式 DDA allowed/其他确定 decision",
        )
        assert _unit_base(result, "salary:p1", "allowances") is None, (
            label,
            "未决事实不得计算 married/spouse DDA 免税额",
        )
        assert not _steps(result, "assessment"), (
            label,
            "未决事实不得留下确定 salary 单位",
        )
        _question_for(result, year, field_exact=question_field)
# ---------------------------------------------------------------------------
# P1 S7 主体定位点修（2026-10-17，ora-50）：问题 subject 按 role=self 定位，
# 不按排序后 participants[0]（005:462/551＋单位 ID 序 005:738）
# ---------------------------------------------------------------------------


def _s7_subject_input(
    self_id: str = "p1", *, participants_reversed: bool = False
) -> dict:
    """S7 顺序配偶 fixture：self=<self_id>、spouse=p2、历史配偶=p3（非 actor）。

    仅把既有 S7 fixture 的 self pID 参数化（p1→p9 时保留 p2/spouse、p3/
    历史配偶与全部事实）；participants 反转只改变数组序，不改变角色/事实。
    """
    year = "2025_26"
    participants = [
        {"person_id": self_id, "role": "self"},
        {"person_id": "p2", "role": "spouse"},
    ]
    if participants_reversed:
        participants.reverse()
    return _salary_input(
        year,
        [
            {"subject_id": self_id, "birth_date": "1990-01-01"},
            {"subject_id": "p2", "birth_date": "1992-02-02"},
            {"subject_id": "p3", "birth_date": "1993-03-03"},
        ],
        participants=participants,
        household={
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
        },
        financials={
            self_id: _fin(nai="500000", mpf="18000", ai=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True),
        },
        assessment={
            "basis": "hypothetical",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "pa": {self_id: {"mode": "none"}, "p2": {"mode": "none"}},
                "history": "ordinary",
            },
            "hypothetical_salary_joint_nomination": {
                "status": "named", "person_id": self_id,
            },
            "candidates": ["salary_joint"],
            "selection": "salary_joint",
        },
    )


def _s7_branch_a_subject_input(self_id: str = "p1") -> dict:
    """S7＋p2 AI=false 的 separate 分支 A 输入（`_separate_allocation` S7 分支）。

    该路径在 `_eval_separate` -> `_compute_separate` -> `_separate_allocation`
    内产生 marriage 问题（salaries.py 修前行 :435–443）；self pID 参数化后，
    可观测问题 subject 是否按角色而非排序位置（p2→p9 排序后首项为 spouse）。
    """
    year = "2025_26"
    return _salary_input(
        year,
        [
            {"subject_id": self_id, "birth_date": "1990-01-01"},
            {"subject_id": "p2", "birth_date": "1992-02-02"},
            {"subject_id": "p3", "birth_date": "1993-03-03"},
        ],
        participants=[
            {"person_id": self_id, "role": "self"},
            {"person_id": "p2", "role": "spouse"},
        ],
        household={
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
        },
        financials={
            self_id: _fin(nai="240000", mpf="9000", ai=True),
            "p2": _fin(nai="0", mpf="0", ai=False),
        },
        assessment={
            "basis": "actual",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "pa": {self_id: {"mode": "none"}, "p2": {"mode": "none"}},
                "history": "ordinary",
            },
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
    )


def _self_p9_computable_input(*, participants_reversed: bool = False) -> dict:
    """同一 T3 事实仅把 self p1 改名 p9：用于单位 ID 序控制（p2→p9→joint）。"""
    year = "2025_26"
    participants = [
        {"person_id": "p9", "role": "self"},
        {"person_id": "p2", "role": "spouse"},
    ]
    if participants_reversed:
        participants.reverse()
    return _salary_input(
        year,
        [
            {"subject_id": "p9", "birth_date": "1990-01-01"},
            {"subject_id": "p2", "birth_date": "1992-02-02"},
        ],
        participants=participants,
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        household=_s6_household(year),
        financials={
            "p9": _fin(nai="500000", mpf="18000", ai=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True),
        },
        claims=[_claim("c1", "disabled_dependant", claimant="p9", subject="p2")],
        assessment={
            "basis": "hypothetical",
            "hypothetical_salary_joint_nomination": {
                "status": "named", "person_id": "p9",
            },
            "candidates": ["salary_separate", "salary_joint"],
            "selection": "best",
        },
    )


def test_002_s7_marriage_question_subject_is_self_after_self_renamed_p9() -> None:
    """S7 主体定位（纯入口 Red）：既有顺序配偶 fixture 仅把 self 的 pID 由
    p1 改名 p9（保留 p2/spouse、p3/历史配偶与全部事实）后，筛选后
    participants 首项为 p2（单位 ID 序 p2→p9）；婚姻问题 subject_id 仍须
    指向 self=p9，而不是 spouse=p2。

    规格锚点：Annex 005:462/551（locator year/subject_id/field）、005:738
    （单位 ID 序）；既有断言仍保留于 test_002_sequential_spouse_change_...
    """
    year = "2025_26"
    result = _run(_s7_subject_input("p9"))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    question = _question_for(
        result,
        year,
        subject_id="p9",
        field_exact=f"years.{year}.household.marriage",
    )
    assert question.get("subject_id") == "p9", question
    assert not _question_found(
        result,
        year,
        subject_id="p2",
        field_exact=f"years.{year}.household.marriage",
    ), f"婚姻问题主体不得落在 spouse p2：{result.get('questions')!r}"


def test_002_s7_branch_a_marriage_question_subject_is_self_after_self_renamed_p9() -> None:
    """S7 separate 分支 A（`:442` 对应路径）主体定位 Red：p2 AI=false 时
    `_separate_allocation` 的 S7 分支追加婚姻问题；self 改名 p9 后排序首项为
    p2，问题 subject_id 仍须为 self=p9，且 candidate unresolved/needs_input。
    """
    year = "2025_26"
    result = _run(_s7_branch_a_subject_input("p9"))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    check = _check(_selection(result), "salary_separate")
    assert check.get("admission") == "unresolved", check
    assert "S7_SEQUENTIAL_SPOUSES" in (check.get("reason_codes") or []), check
    _question_for(
        result,
        year,
        subject_id="p9",
        field_exact=f"years.{year}.household.marriage",
    )
    assert not _question_found(
        result,
        year,
        subject_id="p2",
        field_exact=f"years.{year}.household.marriage",
    ), f"S7 分支 A 问题主体不得落在 spouse p2：{result.get('questions')!r}"


def test_002_s7_subject_controls_positive_reversed_and_unit_id_order() -> None:
    """S7 主体定位控制：正序 self=p1 主体仍为 p1；participants 反转后
    self=p9 主体仍为 p9（角色识别与数组序无关）；可算 p9 控制的单位 ID 序
    仍为 salary:p2→salary:p9→salary:joint，金额 34,250 不变。
    """
    year = "2025_26"
    positive = _run(_s7_subject_input("p1"))
    assert positive["status"] == "needs_input", positive["status"]
    _assert_null_amounts(positive)
    _question_for(
        positive,
        year,
        subject_id="p1",
        field_exact=f"years.{year}.household.marriage",
    )

    reversed_ = _run(_s7_subject_input("p9", participants_reversed=True))
    assert reversed_["status"] == "needs_input", reversed_["status"]
    _assert_null_amounts(reversed_)
    _question_for(
        reversed_,
        year,
        subject_id="p9",
        field_exact=f"years.{year}.household.marriage",
    )

    for label, facts in (
        ("正序 participants", _self_p9_computable_input()),
        ("反转 participants", _self_p9_computable_input(participants_reversed=True)),
    ):
        result = _run(facts)
        assert result["status"] == "partial", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] == "34250", (
            label,
            result["amounts"],
        )
        assert [
            detail.get("unit_id") for detail in _steps(result, "assessment")
        ] == ["salary:p2", "salary:p9", "salary:joint"], (
            label,
            _steps(result, "assessment"),
        )
