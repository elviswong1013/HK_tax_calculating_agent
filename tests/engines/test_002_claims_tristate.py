# -*- coding: utf-8 -*-
"""REQ-2 资格事实三态、时间谓词与扣除桥接（002 P1 Red；Annex 005 事实契约）。

REQ: REQ-2（S002 §8/§9.4；PRD AC-2）。本文件为 SDD §9.4 REQ-2 命名节点＋
006 §5.4（R3#4）同文件节点，共 11 个：
  - test_002_unknown_fact_needs_input_null_amounts
  - test_002_false_missing_zero_distinct
  - test_002_period_bounded_within_year
  - test_002_disjoint_paths_not_intersected
  - test_002_nai_positive_ai_false_contradictory
  - test_002_ai_unknown_salary_couple_needs_input
  - test_002_home_status_tf_prior_year_care_period
  - test_002_item_type_unsupported_positive_rejected_zero_accepted
  - test_002_erce_unknown_missing_still_needs
  - test_002_eligibility_true_cannot_bypass_raw_facts
  - test_002_hk_presence_days_union_of_explicit_years

规格锚点:
  - S002 §3.1/§5.1（Tri 原始三态；claim decision 四态不合并）；§8 REQ-2；
    Annex 005 §3（Tri/Date/YA/Money/TF 左闭右开）、§4.3/§4.7/§4.8/§4.9、
    §6（三口径分离；§6.1 来源一致性；§6.2 deduction_items 桥接与 ERCE）、
    §7（ANY/ALL/OR/AND/SAMEPOINT 有界谓词；TF 默认窗口＝该 YA
    [4月1日, 次年4月1日)）、§13（questions[] 结构化定位项）、§14（steps
    tagged trace；Exact "num/den"）、§15.2（金额矩阵；宽减表）、§2.1
    （v2 引擎签名 availability= 显式参数）。
  - C3.3/C3.4（needs_input 六键显式 null；partial 组件语义）。

期望值来源（禁从生产代码反推）:
  - 免税额/上限/宽减：Annex 005 §15.2 三年度全表（转引附表 4/43，T1）。
  - 税率带：附表 1/2（Annex D D7；T1 台账 1.17）。
  - PAM39 Q1 组合值 3,940/3,000/940：T2（台账 1.13）。
  - 其余组合数值：T3 独立手算（算式见各测试注释，可审计复算）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - app.engines.salaries.calculate_salaries_tax(facts, bundle, *,
    availability=None)（Annex 005 §2.1 v2 签名）：facts＝v2 input（subject
    注册表＋逐年事实；005 §3/§5 示例形状）；注入含 data.allowances 矩阵的
    v2 bundle（rules_schema_version "2.0.0"）。
  - v2 行为：needs_input＋结构化 questions（含 field/year/claim_id?/
    subject_id?/question_zh 定位）；steps[] 含 tagged decision/assessment
    条目（005 §14）；明确 false＝可判定不合资格（不追问）；缺失＝未知
    （追问）；金额缺失≠"0"；正 NAI×AI=false → AppError E_INPUT_CONTRADICTORY；
    unsupported item_type 正数 → AppError E_DEDUCTION_UNSUPPORTED；
    ERCE eligibility 声明不能绕过 care_payments 原始事实；
    hk_presence_days 年份域＝显式 Year 的 {Y−1,Y,Y+1} 并集。
  - 本文件对 app.* 延迟导入（测试体内），Red 失败原因＝缺失 v2 契约
    （§2.1 availability 签名/v2 事实模型解析），而非收集期导入失败。
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
                # v2 共享读取路径（005 §15.2）：band/宽减/ERCE cap 既有源保留
                "progressive_bands": _PROGRESSIVE,
                "standard_rate_bands": _STANDARD,
                "deductions": {
                    "elder_residential_care": {
                        "label_zh": "长者住宿照顾开支（附表 3C）",
                        "caps": {"2024_25": "100000", "2025_26": "100000", "2026_27": "110000"},
                    }
                },
                # 旧 1.0.0 形状残留键：仅令 Red 阶段 v1 引擎不因缺键崩溃，
                # 数值与 §15.2 矩阵一致，不构成期望值来源。
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


def _salary_input(
    year: str, subjects: list, *, presence: list | None = None, **year_kw: Any
) -> dict:
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
    during: Any = True,
    maintained: list | None = None,
    cash: str | None = None,
    cohabit: list | None = None,
    sole_care: list | None = None,
) -> dict:
    kinship: dict[str, Any] = {"kind": kind, "basis": basis}
    if during is not None:
        kinship["during"] = _tf(year, during)
    r: dict[str, Any] = {
        "relation_id": rid,
        "actor_id": actor,
        "subject_id": subject,
        "kinship": kinship,
    }
    if maintained is not None:
        r["maintained"] = maintained
    if cash is not None:
        r["cash_maintenance"] = cash
    if cohabit is not None:
        r["cohabit_non_full_consideration"] = cohabit
    if sole_care is not None:
        r["sole_or_predominant_care"] = sole_care
    return r


def _claim(cid: str, kind: str, claimant: str = "p1", subject: str = "d1") -> dict:
    return {"claim_id": cid, "kind": kind, "claimant_id": claimant, "subject_id": subject}


def _ordinary_couple_actual() -> dict:
    """合法普通夫妻实际选择事实（005 §4.10/§5.1 形状；active 消费者可判）。

    red-b-review 修正 #1：仅法定基线构造器显式声明已核 actual 选择/ordinary/
    PA 模式，不做全局 _base_year 默认。
    """
    return {
        "salary_joint_elected": False,
        "salary_joint_assessed": False,
        "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
        "history": "ordinary",
    }


def _care_payment(
    *,
    care_from: str,
    care_to: str,
    home_status: list[dict],
    amount: str = "80000",
    residential: Any = True,
    payer: str = "p1",
    subject: str = "d1",
    paid_on: str = "2025-06-15",
    home_basis: str = "cap459_licence",
) -> dict:
    return {
        "payment_id": "e1",
        "payer_id": payer,
        "subject_id": subject,
        "paid_on": paid_on,
        "amount": amount,
        "care_from": care_from,
        "care_to": care_to,
        "residential_care_received": residential,
        "paid_to_home_or_agent": True,
        "home_basis": home_basis,
        "home_status_in_force": home_status,
    }


# ---------------------------------------------------------------------------
# 引擎调用（Annex 005 §2.1 v2 签名；availability 显式传入）
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
# 断言辅助（005 §13 questions 形状；§14 steps/Exact）
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


# ---------------------------------------------------------------------------
# REQ-2 命名节点（S002 §9.4）
# ---------------------------------------------------------------------------


def test_002_unknown_fact_needs_input_null_amounts() -> None:
    """资格事实缺失（≠false≠"0"）→ needs_input＋结构化定位追问；六键显式 null。

    规格锚点：S002 §3.1（缺触发路径必需事实＝needs_input，不得解释为不申索）；
    Annex 005 §6（键缺失＝未提供）；§13（questions 定位项）。
    """
    year = "2025_26"
    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": "1950-05-01"}],
        subject_facts={},  # d1 通常居港事实整体缺失＝未知
        relations=[_relation(year, "r1", "p1", "d1", kind="parent",
                             maintained=_tf(year, True), cash="15000")],
        claims=[_claim("c1", "parent")],
    )
    result = _run(facts)

    assert result["status"] == "needs_input", (
        f"居港事实缺失 must → needs_input，实际 {result['status']!r}"
    )
    _assert_null_amounts(result)
    questions = _structured_questions(result)
    assert _locates(questions, ["d1"], year=year, claim_id="c1", subject_id="d1"), (
        f"追问须绑定 subject d1 的居港事实（005 §13：field+year+claim/subject），实际：{questions!r}"
    )


def test_002_false_missing_zero_distinct() -> None:
    """明确 false＝可判定不合资格（计 0、不追问）；缺失＝未知（追问）；"0"≠缺失。

    规格锚点：Annex 005 §6（四者互不相代）；§7（false 区间已证 → 谓词 false）。
    T3（PAM39 Q1 同组事实对账）：无子女额时 NCI=240,000−9,000−132,000=99,000 →
    累进 3,940、标准 34,650、宽减 3,000、final 940。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]
    relation = _relation(year, "r1", "p1", "d1", maintained=_tf(year, True))

    # (a) 未婚事实缺失 → unknown → needs_input（不得按「已排除」计）。
    missing = _run(_salary_input(
        year, subjects, relations=[relation], claims=[_claim("c1", "child")],
    ))
    assert missing["status"] == "needs_input", (
        f"未婚事实缺失 must → needs_input，实际 {missing['status']!r}"
    )
    _assert_null_amounts(missing)
    assert _locates(_structured_questions(missing), ["d1"], year=year,
                    claim_id="c1", subject_id="d1")

    # (b) 未婚明确 false（子女已婚）→ 可判定不合资格：不追问、不给额。
    explicit_false = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {"unmarried": _tf(year, False)}},
        relations=[relation],
        claims=[_claim("c1", "child")],
    ))
    assert explicit_false["status"] == "partial", (
        f"明确 false 是可判定事实，不得追问，实际 {explicit_false['status']!r}"
    )
    assert not (explicit_false.get("questions") or []), (
        f"明确 false 不产生 questions，实际：{explicit_false.get('questions')!r}"
    )
    decision = _decision(explicit_false, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        "已婚子女（未婚=false）必须落 ineligible decision（005 §14），"
        f"实际：{decision!r}"
    )
    amounts = explicit_false["amounts"]
    assert amounts["before_reduction"] == "3940", amounts
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "940", amounts
    # "0"≠缺失：显式已缴 "0" 是已知组件（provisional_paid="0"，非 null）。
    assert amounts["provisional_paid"] == "0", amounts


def test_002_period_bounded_within_year() -> None:
    """TF 求值限于键年窗口 [4/1, 次年4/1)：ANY 按任一已证日期、ALL 需全覆盖。

    规格锚点：Annex 005 §3（TF 左闭右开、默认窗口）＋§7（ANY/ALL 定义；
    55–59 档 ALL(NOT DAA)，unknown 不默认 false；L35953–35980）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "1968-01-01"}]  # 2025/26 内 57–58 岁
    relation = _relation(year, "r1", "p1", "d1", kind="parent",
                         maintained=_tf(year, True), cash="15000")
    claim = [_claim("c1", "parent")]

    # (a) ANY(居港)＝窗口末日单日已证 true（右开边界前最后一天）→ 成立。
    last_day = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {
            "ordinarily_resident_hk": _tf(year, True, frm="2026-03-31", to="2026-04-01"),
            "govt_disability_allowance": _tf(year, False),
        }},
        relations=[relation], claims=claim,
    ))
    dec = _decision(last_day, claim_id="c1")
    assert dec is not None and dec.get("entitlement") == "eligible", (
        f"窗口内任一日期居港已证 → ANY=true（55–59 档 25,000），实际：{dec!r}"
    )
    assert dec.get("amount") == "25000", dec
    assert not (last_day.get("questions") or [])

    # (b) ALL(NOT DAA)：DAA 仅上半年已证 false、下半年未覆盖 → unknown → 追问。
    partial_all = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {
            "ordinarily_resident_hk": _tf(year, True),
            "govt_disability_allowance": _tf(year, False, to="2025-10-01"),
        }},
        relations=[relation], claims=claim,
    ))
    assert partial_all["status"] == "needs_input", (
        f"ALL 谓词部分覆盖＝unknown（不默认 false），实际 {partial_all['status']!r}"
    )
    _assert_null_amounts(partial_all)
    assert _locates(_structured_questions(partial_all), ["d1"], year=year,
                    claim_id="c1", subject_id="d1")

    # (c) DAA 全窗口 false → ALL(NOT DAA)=true → 55–59 档成立（对照）。
    full_all = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {
            "ordinarily_resident_hk": _tf(year, True),
            "govt_disability_allowance": _tf(year, False),
        }},
        relations=[relation], claims=claim,
    ))
    dec = _decision(full_all, claim_id="c1")
    assert dec is not None and dec.get("entitlement") == "eligible" and dec.get("amount") == "25000", (
        f"全窗口已证 NOT DAA → 55–59 档 25,000，实际：{dec!r}"
    )


def test_002_disjoint_paths_not_intersected() -> None:
    """SAMEPOINT：互补时点的事实不得拼资格（异时点求并＝错误）。

    规格锚点：Annex 005 §7（SAMEPOINT＝同一日期 AND；s.31 行）；S002 §2 ADR-7
    （异时点事实不得拼资格）。
    T3：无子女额 → final 940（同 PAM39 Q1 组）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]
    result = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {
            # 未婚仅上半年 true、下半年 false；受供养反之——全年无同一日期同时成立。
            "unmarried": [
                {"from": "2025-04-01", "to": "2025-07-01", "value": True},
                {"from": "2025-07-01", "to": "2026-04-01", "value": False},
            ],
        }},
        relations=[_relation(year, "r1", "p1", "d1", maintained=[
            {"from": "2025-04-01", "to": "2025-07-01", "value": False},
            {"from": "2025-07-01", "to": "2026-04-01", "value": True},
        ])],
        claims=[_claim("c1", "child")],
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        "未婚/受供养在任一日期均不同时成立 → SAMEPOINT=false（不得按 ANY 求并拼资格），"
        f"实际：{decision!r}"
    )
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]


def test_002_nai_positive_ai_false_contradictory() -> None:
    """正 NAI × AI=false 并提 → AppError E_INPUT_CONTRADICTORY（三口径分离）。

    规格锚点：Annex 005 §6（口径链）；§4.6（has_assessable_salary_income 行）；
    S002 §8 REQ-2.4。
    """
    year = "2025_26"
    facts = _salary_input(
        year, [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        financials={"p1": _fin(nai="240000", mpf="9000", ai=False)},
    )
    from app.core.errors import AppError  # 延迟导入：Red=缺 v2 契约

    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", (
            f"正 NAI×AI=false 须以 E_INPUT_CONTRADICTORY 拒算，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "正 NAI×AI=false 属矛盾资料（005 §6），必须拒算而非静默采纳"
        )


def test_002_ai_unknown_salary_couple_needs_input() -> None:
    """配偶 AI unknown → 夫妻判定 needs_input（不自动裁定来源）。

    规格锚点：Annex 005 §6（AI unknown → 夫妻判定 needs_input）；§9.1 S2/S3。
    """
    year = "2025_26"
    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"}],
        household={
            "marriage": [{"from": "2025-04-01", "to": "2026-04-01",
                          "value": "married", "spouse_id": "p2"}],
            "living_apart": _tf(year, False),
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai="unknown")},
    )
    result = _run(facts)

    assert result["status"] == "needs_input", (
        f"配偶 AI=unknown 阻断 s.29 分支判定 → needs_input，实际 {result['status']!r}"
    )
    _assert_null_amounts(result)
    questions = _structured_questions(result)
    assert _locates(questions, ["has_assessable_salary_income"], year=year,
                    subject_id="p2"), (
        f"追问须绑定配偶 p2 的薪俸 AI 存在性事实（field+year+subject），实际：{questions!r}"
    )


# ---------------------------------------------------------------------------
# R3#4 命名节点（006 §5.4；Annex 005 §4.7/§4.8/§6.2）
# ---------------------------------------------------------------------------


def _elder_care_year(**year_kw: Any) -> dict:
    """s.26D 场景公共构造：长者 d1（1950 生、DAA 全年）＋关系＋供养现金。"""
    year = "2025_26"
    year_kw.setdefault(
        "subject_facts",
        {"d1": {"govt_disability_allowance": _tf(year, True)}},
    )
    year_kw.setdefault(
        "relations",
        [_relation(year, "r1", "p1", "d1", kind="parent",
                   maintained=_tf(year, True), cash="15000")],
    )
    return _base_year(year, **year_kw)


def test_002_home_status_tf_prior_year_care_period() -> None:
    """home_status_in_force 合法域＝自身 [care_from,care_to)，可整段落在付款 YA 前。

    规格锚点：Annex 005 §4.8（唯一年外 TF 例外）；§6.2（ERCE 原始事实判定）。
    T3：ERCE 80,000 计入 Part 4A → 9,000+80,000=89,000；NCI=240,000−89,000−
    132,000=19,000 → 累进 380；标准 151,000×15%=22,650；宽减 min(380,3,000)=380
    → final 0。
    """
    year = "2025_26"
    facts = {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "1950-05-01"}],
        "years": {year: _elder_care_year(
            care_payments=[_care_payment(
                care_from="2024-08-01", care_to="2025-02-01",  # 上年服务、本年付款
                home_status=[{"from": "2024-08-01", "to": "2025-02-01", "value": True}],
            )],
            financials={"p1": _fin(deduction_items=[
                {"item_type": "elder_residential_care", "amount": "80000",
                 "payment_ids": ["e1"], "eligibility": {"value": True}},
            ])},
            claims=[],
        )},
        "hk_presence_days": [],
        "assumptions": [],
    }
    result = _run(facts)

    assert result["status"] == "partial", (
        f"上年服务期间的 home_status TF 合法（005 §4.8），不得拒算，实际 {result['status']!r}"
    )
    assert not (result.get("questions") or [])
    assert _unit_base(result, "salary:p1", "part4a") == "89000/1", (
        "ERCE 80,000 经原始事实判定后计入 Part 4A（MPF 9,000＋80,000），"
        f"实际：{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "380", amounts
    assert amounts["reduction"] == "380", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_item_type_unsupported_positive_rejected_zero_accepted() -> None:
    """unsupported item_type：正数 → E_DEDUCTION_UNSUPPORTED；显式零 → 接受无效果；
    显式零＋eligibility unknown → 仍追问。

    规格锚点：Annex 005 §6.2（声明行为三分支）；S002 §8 REQ-2（006 R3#4）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]
    from app.core.errors import AppError

    # (a) 未支持类型正数 → 拒算。
    try:
        _run(_salary_input(year, subjects, financials={"p1": _fin(deduction_items=[
            {"item_type": "charitable_donation", "amount": "5000"},
        ])}))
    except AppError as exc:
        assert exc.code == "E_DEDUCTION_UNSUPPORTED", (
            f"unsupported 正数申索须 E_DEDUCTION_UNSUPPORTED，实际 {exc.code!r}"
        )
    else:
        raise AssertionError("unsupported item_type 正数申索必须拒算（005 §6.2）")

    # (b) 未支持类型显式零（legacy 占位）→ 接受、无效果、不追问。
    zero = _run(_salary_input(year, subjects, financials={"p1": _fin(deduction_items=[
        {"item_type": "charitable_donation", "amount": "0"},
    ])}))
    assert zero["status"] == "partial", zero["status"]
    assert not (zero.get("questions") or []), zero.get("questions")
    assert _unit_base(zero, "salary:p1", "part4a") == "9000/1"
    assert zero["amounts"]["final_after_reduction"] == "940", zero["amounts"]

    # (c) 显式零＋eligibility={"value":"unknown"} → 零金额不得吞掉三态。
    zero_unknown = _run(_salary_input(year, subjects, financials={"p1": _fin(deduction_items=[
        {"item_type": "charitable_donation", "amount": "0",
         "eligibility": {"value": "unknown"}},
    ])}))
    assert zero_unknown["status"] == "needs_input", zero_unknown["status"]
    _assert_null_amounts(zero_unknown)
    assert _locates(
        _structured_questions(zero_unknown), ["deduction_items", "eligibility"],
        year=year,
    ), zero_unknown.get("questions")


def test_002_erce_unknown_missing_still_needs() -> None:
    """已支持 ERCE：eligibility 缺失或 unknown → 仍 needs_input（金额正数不豁免）。

    规格锚点：Annex 005 §6.2（unknown/缺失 → questions/needs_input，即使金额正数）。
    """
    year = "2025_26"
    care = [_care_payment(care_from="2025-04-01", care_to="2026-04-01",
                          home_status=[{"from": "2025-04-01", "to": "2026-04-01", "value": True}])]

    missing = _run({
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "1950-05-01"}],
        "years": {year: _elder_care_year(
            care_payments=care,
            financials={"p1": _fin(deduction_items=[
                {"item_type": "elder_residential_care", "amount": "80000",
                 "payment_ids": ["e1"]},
            ])},
        )},
        "hk_presence_days": [],
        "assumptions": [],
    })
    assert missing["status"] == "needs_input", (
        f"ERCE eligibility 缺失 → needs_input，实际 {missing['status']!r}"
    )
    _assert_null_amounts(missing)
    assert _locates(
        _structured_questions(missing), ["deduction_items", "eligibility"],
        year=year, subject_id="d1",
    ), missing.get("questions")

    unknown = _run({
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "1950-05-01"}],
        "years": {year: _elder_care_year(
            care_payments=care,
            financials={"p1": _fin(deduction_items=[
                {"item_type": "elder_residential_care", "amount": "80000",
                 "payment_ids": ["e1"], "eligibility": {"value": "unknown"}},
            ])},
        )},
        "hk_presence_days": [],
        "assumptions": [],
    })
    assert unknown["status"] == "needs_input", unknown["status"]
    _assert_null_amounts(unknown)
    assert _locates(
        _structured_questions(unknown), ["deduction_items", "eligibility"],
        year=year, subject_id="d1",
    ), unknown.get("questions")


def test_002_eligibility_true_cannot_bypass_raw_facts() -> None:
    """eligibility=true 只是旧式声明：不能绕过 care_payments 原始事实判定。

    规格锚点：Annex 005 §6.2（声明不能绕过 raw care/home 事实判定）。
    T3：扣除未获 → Part 4A＝9,000 → final 940（PAM39 Q1 同组对账）。
    """
    year = "2025_26"
    facts = {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "1950-05-01"}],
        "years": {year: _elder_care_year(
            care_payments=[_care_payment(
                care_from="2025-04-01", care_to="2026-04-01",
                home_status=[{"from": "2025-04-01", "to": "2026-04-01", "value": True}],
                residential=False,  # 原始事实否定院舍照顾
            )],
            financials={"p1": _fin(deduction_items=[
                {"item_type": "elder_residential_care", "amount": "80000",
                 "payment_ids": ["e1"], "eligibility": {"value": True}},
            ])},
        )},
        "hk_presence_days": [],
        "assumptions": [],
    }
    result = _run(facts)

    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), result.get("questions")
    assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
        "residential_care_received=false 时声明 eligibility=true 不得授予扣除"
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "3940", amounts
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "940", amounts


def test_002_hk_presence_days_union_of_explicit_years() -> None:
    """hk_presence_days 年份域＝显式 Year 的 {Y−1,Y,Y+1} 并集；域外 → E_INPUT_EXTRA。

    规格锚点：Annex 005 §4.7（允许年份＝并集；只开放辅助日数）。
    """
    year = "2025_26"  # 显式 Year 仅 2025_26 → 允许 {2024_25, 2025_26, 2026_27}
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]

    accepted = _run(_salary_input(
        year, subjects,
        presence=[{"person_id": "p1", "year": "2024_25", "days": 200}],
    ))
    assert accepted["status"] == "partial", accepted["status"]
    assert accepted["amounts"]["final_after_reduction"] == "940", accepted["amounts"]

    from app.core.errors import AppError

    try:
        _run(_salary_input(
            year, subjects,
            presence=[{"person_id": "p1", "year": "2023_24", "days": 200}],
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_EXTRA", (
            f"并集外年份须 E_INPUT_EXTRA，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "2023_24 不在显式 Year {2025_26} 的 {Y−1,Y,Y+1} 并集内，必须拒绝（005 §4.7）"
        )


# ---------------------------------------------------------------------------
# P1 r1 新 Red（复核 §一 A3＋§六.6）：TF 形状与闭合/规范化
# ---------------------------------------------------------------------------


def test_002_nested_tf_array_rejected() -> None:
    """TF 元素必须为对象：嵌套数组（如 [[{from,to,value}]]）→ E_INPUT_TYPE
    拒绝并定位元素（复核 §一 A3：005 §3；不得按「Red fixture 兼容」展开）。

    显式 null 同理不得冒充省略（null≠缺失，须拒绝而非当未知）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]

    nested = _salary_input(year, subjects)
    nested["years"][year]["subject_facts"]["d1"] = {
        "ordinarily_resident_hk": [_tf(year, True)],  # 外层多余数组包裹
    }
    try:
        _run(nested)
    except AppError as exc:
        assert exc.code == "E_INPUT_TYPE", (
            f"嵌套 TF 数组须以 E_INPUT_TYPE 拒绝，实际 {exc.code!r}"
        )
    else:
        raise AssertionError(
            "TF 值必须为 [{from,to,value}] 对象数组；嵌套数组不得被兼容展开"
            "（复核 §一 A3/005 §3）"
        )


def test_002_input_shape_closed_and_normalized() -> None:
    """闭合/规范化负例组（复核 §六.6；R3.1）：每层 extra、显式 null、
    包装 Tri（守恒）、紧凑日期、逆序/越域 TF、错误 ID 前缀、claim 多余键、
    share>1、布尔/越界日数、数组边界；Money 尾零与 Ratio 未约简＝接受并
    规范化（守恒）。

    规格锚点：005 §3（Date 全匹配 YYYY-MM-DD；TF 按序互不重叠、默认窗口
    ＋列明例外；ID 前缀；Money/Ratio 词法与 C2.1/C2.10；C2.9 数组 ≤1000）。
    """
    from app.core.errors import AppError

    year = "2025_26"
    w_from, w_to = _window(year)
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]

    def base_input() -> dict:
        return _salary_input(year, subjects)

    def expect_rejected(label: str, mutate) -> None:
        facts = base_input()
        mutate(facts)
        try:
            _run(facts)
        except AppError as exc:
            assert exc.code in (
                "E_INPUT_EXTRA", "E_INPUT_TYPE", "E_INPUT_MISSING",
                "E_INPUT_CONTRADICTORY", "E_INPUT_OVERSIZE",
            ), f"{label}：结构/词法违反须以 E_INPUT_* 拒绝，实际 {exc.code!r}"
        else:
            raise AssertionError(
                f"{label}属闭合契约违反（005 §3/C2），必须拒绝而非静默采纳"
            )

    # 每层 extra（信封/Year/subject_facts/claims）。
    def _top(f):
        f["extra_top"] = 1
    expect_rejected("input 顶层 extra", _top)

    def _year(f):
        f["years"][year]["extra_year"] = 1
    expect_rejected("Year extra", _year)

    def _sf(f):
        f["years"][year]["subject_facts"]["d1"] = {"extra_fact": 1}
    expect_rejected("subject_facts 条目 extra", _sf)

    def _claim(f):
        f["years"][year]["claims"] = [
            {"claim_id": "c1", "kind": "child", "claimant_id": "p1",
             "subject_id": "d1", "extra_claim": 1}
        ]
    expect_rejected("claims 条目 extra", _claim)

    # 显式 null 不得冒充省略（TF value=null）。
    def _null(f):
        f["years"][year]["subject_facts"]["d1"] = {"unmarried": None}
    expect_rejected("TF 显式 null", _null)

    # 紧凑日期（YYYYMMDD 非 YYYY-MM-DD 全匹配）。
    def _compact(f):
        f["years"][year]["household"]["marriage"] = [
            {"from": "20250601", "to": w_to, "value": "unmarried"}
        ]
    expect_rejected("紧凑日期", _compact)

    # 逆序 TF（不重叠但乱序；TF 按序合同）。
    def _reverse(f):
        f["years"][year]["subject_facts"]["d1"] = {"unmarried": [
            {"from": "2025-10-01", "to": w_to, "value": True},
            {"from": w_from, "to": "2025-10-01", "value": True},
        ]}
    expect_rejected("逆序 TF", _reverse)

    # 越域 TF（ordinarily_resident_hk 仅允许参考日一天区间例外，不得整月越域）。
    def _out_of_window(f):
        f["years"][year]["subject_facts"]["d1"] = {"ordinarily_resident_hk": [
            {"from": "2026-06-01", "to": "2026-07-01", "value": True}
        ]}
    expect_rejected("越域 TF（非一天参考日例外）", _out_of_window)

    # 错误 ID 前缀（subject 须 ^p…|^d…）。
    def _bad_id(f):
        f["subjects"] = [{"subject_id": "x1", "birth_date": "1990-01-01"}]
        f["years"][year]["participants"] = [{"person_id": "x1", "role": "self"}]
        f["years"][year]["financials"] = {"x1": {
            "employment_income_nai": "240000", "mpf_mandatory_contributions": "9000",
            "has_assessable_salary_income": True,
            "source_classification": {"salary": "confirmed_in_scope"},
            "properties": [], "businesses": [], "deduction_items": [],
        }}
    expect_rejected("错误 ID 前缀", _bad_id)

    # Ratio 域违反：share 3/2（0<num≤den）。
    def _share_gt1(f):
        f["years"][year]["financials"] = {"p1": {
            "employment_income_nai": "0", "mpf_mandatory_contributions": "0",
            "has_assessable_salary_income": False,
            "source_classification": {"salary": "confirmed_in_scope"},
            "properties": [{
                "property_id": "h1", "share": "3/2", "rent_received": "120000",
                "rates_paid_by_owner": {"amount": "0", "agreed": False,
                                        "actually_paid": False},
                "irrecoverable_rent": "0", "deposit_offsets": "0",
                "mortgage_interest": "0",
            }],
            "businesses": [], "deduction_items": [],
        }}
    expect_rejected("Ratio 域 share>1", _share_gt1)

    # 布尔日数。
    def _days_bool(f):
        f["hk_presence_days"] = [{"person_id": "p1", "year": year, "days": True}]
    expect_rejected("布尔日数", _days_bool)

    # 越界日数（2025/26 共 365 天；days 须 0..该 YA 日数）。
    def _days_over(f):
        f["hk_presence_days"] = [{"person_id": "p1", "year": year, "days": 1002}]
    expect_rejected("越界日数", _days_over)

    # 数组边界（C2.9：全局数组 ≤1000 项）。
    def _subjects_over(f):
        many = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
        many += [{"subject_id": f"d{k}"} for k in range(1, 1001)]  # 共 1001 项
        f["subjects"] = many
    expect_rejected("subjects 超 1000 项", _subjects_over)

    # ——守恒正例：Money 尾零与 Ratio 未约简＝接受并规范化（C2.1/C2.10）——
    trailing = base_input()
    trailing["years"][year]["financials"] = {"p1": {
        "employment_income_nai": "240000.00", "mpf_mandatory_contributions": "9000",
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [], "businesses": [], "deduction_items": [],
    }}
    r = _run(trailing)
    assert r["status"] == "partial", r["status"]
    assert r["amounts"]["final_after_reduction"] == "940", (
        f"尾零金额须接受并规范化（与 240000 同结果），实际：{r['amounts']!r}"
    )


def test_002_http_and_pure_engine_share_structural_rejection() -> None:
    """同一 v2 payload 经 HTTP validator 与纯引擎均以精确结构错误拒绝。"""
    from app.api.validation import validate_prepare_body
    from app.core.errors import AppError

    year = "2025_26"
    facts = _salary_input(year, [{"subject_id": "p1", "birth_date": "1990-01-01"}])
    facts["years"][year]["relations"] = None
    body = {"tax_type": "salaries_tax", "schema_version": "2.0.0", "input": facts}

    observed = []
    for label, call in (
        ("HTTP validator", lambda: validate_prepare_body(body)),
        ("pure engine", lambda: _run(facts)),
    ):
        try:
            call()
        except AppError as exc:
            observed.append((exc.code, exc.field))
        else:
            raise AssertionError(f"{label} 接受 relations=null；005 §3 要求显式 null 拒绝")

    assert observed == [
        ("E_INPUT_TYPE", f"years.{year}.relations"),
        ("E_INPUT_TYPE", f"years.{year}.relations"),
    ], f"HTTP/纯入口结构错误及字段须一致且精确，实际：{observed!r}"


def test_002_missing_salary_presence_is_not_zero() -> None:
    """E3：活动 NAI 缺失不得按零算。"""
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]

    missing_nai = _salary_input(year, subjects)
    del missing_nai["years"][year]["financials"]["p1"]["employment_income_nai"]
    unknown = _run(missing_nai)
    assert unknown["status"] == "needs_input", unknown
    _assert_null_amounts(unknown)
    assert _locates(_structured_questions(unknown), ["employment_income_nai"],
                    year=year, subject_id="p1"), unknown.get("questions")

def test_002_explicit_zero_salary_presence_is_known() -> None:
    """E3: 明确提交的零与缺失不同，是已知值。"""
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    explicit_zero = _run(_salary_input(
        year, subjects,
        financials={"p1": _fin(nai="0", mpf="0", ai=False)},
    ))
    assert explicit_zero["status"] == "partial", explicit_zero["status"]
    assert explicit_zero["amounts"]["final_after_reduction"] == "0", explicit_zero["amounts"]

def test_002_manual_salary_source_does_not_produce_amount() -> None:
    """E3：来源分类 manual 影响相关候选，不得当作已确认来源计算。

    P1 r2 纠错（勘误 #8）：core 状态只按 Approved 005 §10（633–635）的
    needs_input/blocked；manual_handoff 不是 core status。金额 null、best 不得
    确定，且来源须可定位（questions 或 blocked_reason）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    manual_source = _salary_input(
        year, subjects,
        financials={"p1": _fin(source_classification={"salary": "manual"})},
    )
    unresolved = _run(manual_source)
    assert unresolved["status"] in ("needs_input", "blocked"), (
        f"source manual 的 core 状态只能是 needs_input/blocked（005 §10：blocked "
        f"合法；manual_handoff 不是 core status），实际 {unresolved['status']!r}"
    )
    assert unresolved["amounts"]["final_after_reduction"] is None, unresolved["amounts"]
    assert unresolved.get("best_scenario") is None, (
        f"相关候选未决 → 不得确定 best，实际：{unresolved.get('best_scenario')!r}"
    )
    questions = unresolved.get("questions") or []
    assert _locator_ok(unresolved, ["source_classification"], year=year,
                       subject_id="p1"), (
        "来源须绑定 p1 的分类事实（field+year+subject）或既有非空 blocked_reason，"
        f"实际：{questions!r} {unresolved.get('blocked_reason')!r}"
    )


def test_002_erce_cap_aggregates_items_per_elder_and_keeps_other_elder_cap() -> None:
    """E5/E6：ERCE cap 按 claimant+elder+YA 跨 items 聚合，不串用其他 elder cap。"""
    year = "2025_26"
    window = _window(year)
    subjects = [
        {"subject_id": "p1", "birth_date": "1990-01-01"},
        {"subject_id": "d1", "birth_date": "1950-05-01"},
        {"subject_id": "d2", "birth_date": "1952-05-01"},
    ]

    def payment(pid: str, elder: str) -> dict:
        return {
            "payment_id": pid, "payer_id": "p1", "subject_id": elder,
            "paid_on": "2025-06-15", "amount": "60000",
            "care_from": window[0], "care_to": window[1],
            "residential_care_received": True, "paid_to_home_or_agent": True,
            "home_basis": "cap459_licence",
            "home_status_in_force": [{"from": window[0], "to": window[1], "value": True}],
        }

    financial = _fin(nai="600000", mpf="18000", deduction_items=[
        {"item_type": "elder_residential_care", "amount": "60000",
         "payment_ids": ["e1"], "eligibility": {"value": True}, "subject_id": "d1"},
        {"item_type": "elder_residential_care", "amount": "60000",
         "payment_ids": ["e2"], "eligibility": {"value": True}, "subject_id": "d1"},
        {"item_type": "elder_residential_care", "amount": "60000",
         "payment_ids": ["e3"], "eligibility": {"value": True}, "subject_id": "d2"},
    ])
    result = _run(_salary_input(
        year, subjects,
        subject_facts={
            elder: {"ordinarily_resident_hk": _tf(year, True),
                    "govt_disability_allowance": _tf(year, True)}
            for elder in ("d1", "d2")
        },
        relations=[_relation(year, "r1", "p1", "d1", kind="parent"),
                   _relation(year, "r2", "p1", "d2", kind="parent")],
        care_payments=[payment("e1", "d1"), payment("e2", "d1"), payment("e3", "d2")],
        financials={"p1": financial},
    ))
    assert _unit_base(result, "salary:p1", "part4a") == "178000/1", (
        "MPF 18,000＋d1 两 item 聚合封顶 100,000＋d2 独立 cap 60,000"
        "＝part4a 178,000；不得按 item 重复 d1 cap 或跨 elder 共用 cap"
    )


# ---------------------------------------------------------------------------
# B lane 补齐（p1-r2-red-completion-plan §B）：presence/来源/人工边界与 s.26D
# ---------------------------------------------------------------------------


def test_002_missing_mpf_presence_is_not_zero() -> None:
    """B：活动 MPF 缺失不得按零算（missing≠"0"）→ needs_input＋精确定位。"""
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    missing_mpf = _salary_input(year, subjects)
    del missing_mpf["years"][year]["financials"]["p1"]["mpf_mandatory_contributions"]
    unknown = _run(missing_mpf)
    assert unknown["status"] == "needs_input", unknown
    _assert_null_amounts(unknown)
    assert _locates(_structured_questions(unknown), ["mpf_mandatory_contributions"],
                    year=year, subject_id="p1"), unknown.get("questions")


def test_002_explicit_zero_mpf_positive_nai_yields_1800() -> None:
    """B 对照：显式 MPF="0" 是已知值（≠缺失）。

    T3：NAI=240,000、MPF=0 → NCI=108,000 → 累进 4,800；标准 36,000；择低
    4,800；宽减 min(4,800,3,000)=3,000 → final 1,800。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    result = _run(_salary_input(year, subjects, financials={"p1": _fin(mpf="0")}))
    assert result["status"] == "partial", result["status"]
    assert _unit_base(result, "salary:p1", "part4a") == "0/1", (
        f"显式 MPF=0 是已知事实（≠缺失），实际：{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "4800", amounts
    assert amounts["final_after_reduction"] == "1800", amounts


@pytest.mark.parametrize(
    "classification",
    [{}, {"salary": "unknown"}],
    ids=["missing", "unknown"],
)
def test_002_salary_source_classification_missing_or_unknown_needs_input(classification) -> None:
    """B：活动薪俸来源分类缺失/unknown＝需要判定的分支未确认 → needs_input
    （不默认 confirmed）＋精确定位。"""
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    result = _run(_salary_input(
        year, subjects,
        financials={"p1": _fin(source_classification=classification)},
    ))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    assert _locates(_structured_questions(result), ["source_classification"],
                    year=year, subject_id="p1"), result.get("questions")


def _manual_issue_scenario(year: str, *, manual_issues: list | None = None) -> dict:
    return _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child")],
        manual_issues=manual_issues,
    )


def test_002_manual_issue_scope_locality_control() -> None:
    """B 对照（仅无事项基线）：无 manual_issues 时同一场景合法（child
    allowed 130,000）；本节点不充当任何 scope 族的无关对照（复审 #4）。"""
    year = "2025_26"
    result = _run(_manual_issue_scenario(year))
    assert result["status"] == "partial", result["status"]
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("grant") == "allowed", decision
    assert decision.get("amount") == "130000", decision


def test_002_manual_issue_unrelated_scope_control() -> None:
    """B 对照（五族无关控制·subject；consult#3 ACCEPT）：合法但与依赖路径
    无关的 scope（subject p1——本场景无任何申索消费 p1 的 subject_facts）不得
    阻塞已证子女路径；manual_issues 按对象局部消费，不是整类禁算。"""
    year = "2025_26"
    result = _run(_manual_issue_scenario(
        year,
        manual_issues=[{"scope": "subject", "id": "p1", "code": "other"}],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), result.get("questions")
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("grant") == "allowed", decision
    assert decision.get("amount") == "130000", decision


def test_002_manual_issue_person_scope_keeps_child_entitlement() -> None:
    """五族无关控制（person；复审 #4 返修）：person 来源事项
    （source_uncertain）只使该人的来源/candidate 未决、无确定税额；独立子女
    申索仍**获授**——decision 必须存在且 allowed 130,000，不得以 `child is
    None` 充当“仍获授”的证明。"""
    year = "2025_26"
    result = _run(_manual_issue_scenario(
        year,
        manual_issues=[{"scope": "person", "id": "p1", "code": "source_uncertain"}],
    ))
    assert result["status"] in ("needs_input", "blocked"), result["status"]
    _assert_null_amounts(result)
    child = _decision(result, claim_id="c1")
    assert child is not None and child.get("grant") == "allowed", (
        "person 来源事项不得否定独立子女资格（s.31 独立于来源路径）", child
    )
    assert child.get("amount") == "130000", child
    assert result.get("best_scenario") is None, result.get("best_scenario")
    assert _locator_ok(result, ["p1"], year=year, subject_id="p1"), (
        "person 事项须绑定 p1 的 field/year/subject 元数据", result.get("questions")
    )


def test_002_manual_issue_relation_scope_unrelated_control() -> None:
    """五族无关控制（relation；复审 #4 返修）：合法但不在 c1 依赖路径上的另一
    关系 r2（配偶 p2→d1；已证分居时 s.31(3) 不适用、单一申索 c1 仅经 r1）
    受 issue 定位时，c1 的子女路径仍获授——复用既有关系场景构造、不发明第三
    actor。"""
    year = "2025_26"
    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "2015-06-01"}],
        household=_couple_household(year, apart=True),
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True)),
                   _relation(year, "r2", "p2", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child", claimant="p1", subject="d1")],
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        manual_issues=[{"scope": "relation", "id": "r2", "code": "other"}],
    )
    result = _run(facts)
    assert result["status"] == "partial", result["status"]
    child = _decision(result, claim_id="c1")
    assert child is not None and child.get("grant") == "allowed", (
        "与 c1 无关的关系 r2 受 issue 定位不得阻塞 c1（局部性）", child
    )
    assert child.get("amount") == "130000", child


@pytest.mark.parametrize(
    "scope,issue_id,issue_code",
    [("subject", "d1", "dispute"),
     ("relation", "r1", "dispute")],
    ids=["subject", "relation"],
)
def test_002_manual_issue_scope_blocks_dependent_path(
    scope, issue_id, issue_code
) -> None:
    """B：manual_issues 按 scope/id 消费——受阻的依赖路径不得授予子女额、无
    确定税额，且定位项须绑定正确年度与适用 claim/subject 元数据。

    person 族（来源事项不否定独立子女资格）见
    `test_002_manual_issue_person_scope_keeps_child_entitlement`。
    """
    year = "2025_26"
    result = _run(_manual_issue_scenario(
        year,
        manual_issues=[{"scope": scope, "id": issue_id, "code": issue_code}],
    ))
    assert result["status"] in ("needs_input", "blocked"), (scope, result["status"])
    _assert_null_amounts(result)
    assert not [
        d for d in _decisions(result)
        if d.get("claim_id") == "c1" and d.get("grant") == "allowed"
    ], (scope, _decisions(result))
    # 定位受影响对象：field 含 issue 目标＋正确 year＋适用 claim/subject 绑定
    # （blocked 允许既有非空理由）。
    assert _locator_ok(result, [issue_id], year=year, claim_id="c1",
                       subject_id="d1"), (scope, result.get("questions"))


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


def _positive_business() -> dict:
    return {"business_id": "b1", "entity_kind": "sole_proprietorship",
            "assessable_profit": "300000", "share": "1/1"}


@pytest.mark.parametrize(
    "finance",
    [
        # red-b-review 修正 #3：先声明 property confirmed_in_scope，再断言
        # 正物业组件 × present=false 矛盾（单一变量；baseline 合法）。
        _fin(
            has_assessable_property_income=False,
            properties=[_positive_property()],
            source_classification={
                "salary": "confirmed_in_scope",
                "property": "confirmed_in_scope",
            },
        ),
        # 业务对应反例（同一 §6.1 规则）。
        _fin(
            has_assessable_business_income=False,
            businesses=[_positive_business()],
            source_classification={
                "salary": "confirmed_in_scope",
                "business": "confirmed_in_scope",
            },
        ),
        # 已确认薪俸来源 present=true × 全来源=false。
        _fin(has_income_assessable_under_ordinance=False),
        # 全来源=true × 三来源均 false（转输入修正/来源人工边界）。
        _fin(
            nai="0",
            mpf="0",
            ai=False,
            has_assessable_property_income=False,
            has_assessable_business_income=False,
            has_income_assessable_under_ordinance=True,
        ),
    ],
    ids=[
        "property-present-present-false",
        "business-present-present-false",
        "salary-present-all-source-false",
        "all-source-true-three-sources-false",
    ],
)
def test_002_presence_cross_consistency_rejected(finance) -> None:
    """B：005 §6.1 来源存在性交叉约束——已证来源与 present=false 矛盾并提 →
    E_INPUT_CONTRADICTORY＋定位当年财务对象（每例＝合法基线＋唯一变更）。"""
    from app.core.errors import AppError

    year = "2025_26"
    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        financials={"p1": dict(finance)},
    )
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
        assert str(exc.field or "").startswith(f"years.{year}.financials.p1"), exc.field
    else:
        raise AssertionError("005 §6.1 来源矛盾必须拒绝（不得静默按已确认来源计算）")


def _s26d_erce_input(year: str, *, relation: dict, amount: str = "80000") -> dict:
    return _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": "1950-05-01"}],
        subject_facts={"d1": {"govt_disability_allowance": _tf(year, True)}},
        relations=[relation],
        care_payments=[_care_payment(
            care_from=_window(year)[0], care_to=_window(year)[1], home_status=_tf(year, True))],
        financials={"p1": _fin(deduction_items=[{
            "item_type": "elder_residential_care", "amount": amount,
            "payment_ids": ["e1"], "eligibility": {"value": True}}])},
    )


def test_002_s26d_relation_during_false_not_deductible() -> None:
    """B（R5/E5）：关系期间显式 false → s.26D 原始事实（关系）不成立 → 扣除不获，
    其余金额照常（T3：Part 4A=9,000、NCI=99,000 → 3,940−3,000=940）。"""
    year = "2025_26"
    result = _run(_s26d_erce_input(
        year, relation=_relation(year, "r1", "p1", "d1", kind="parent",
                                 cash="15000", during=False)))
    assert result["status"] == "partial", result["status"]
    assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
        f"关系期间 false 时不得计入 ERCE，实际：{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]
    assert not [
        d for d in _decisions(result)
        if d.get("item_kind") == "elder_residential_care" and d.get("grant") == "allowed"
    ], _decisions(result)


def test_002_s26d_relation_during_missing_needs_input() -> None:
    """B：关系期间缺失＝未知（≠false）→ 追问、不得授扣；定位 relation 的 during
    事实（不只 subject d1）。"""
    year = "2025_26"
    result = _run(_s26d_erce_input(
        year, relation=_relation(year, "r1", "p1", "d1", kind="parent",
                                 cash="15000", during=None)))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    questions = _structured_questions(result)
    assert _locates(questions, ["during"], year=year, subject_id="d1"), questions


def test_002_s26d_unverified_kinship_basis_manual_locator() -> None:
    """B：未核亲属组合（parent/other）→ 窄 manual＋定位缺失的 kinship.basis。"""
    year = "2025_26"
    result = _run(_s26d_erce_input(
        year, relation=_relation(year, "r1", "p1", "d1", kind="parent",
                                 basis="other", cash="15000")))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    assert _locates(_structured_questions(result), ["kinship.basis"],
                    year=year, subject_id="d1"), result.get("questions")


def test_002_s26d_relation_kind_none_ineligible() -> None:
    """B 对照（red-b-review 矩阵）：kinship.kind=none → 关系无效 → s.26D 原始
    事实不成立 → 不获扣除（T3：Part 4A=9,000、final 940），且不追问。"""
    year = "2025_26"
    result = _run(_s26d_erce_input(
        year, relation=_relation(year, "r1", "p1", "d1", kind="none", cash="15000")))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
        f"kind=none 不得计入 ERCE，实际：{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]
    assert not [
        d for d in _decisions(result)
        if d.get("item_kind") == "elder_residential_care" and d.get("grant") == "allowed"
    ], _decisions(result)


def test_002_s26d_relation_kind_unknown_needs_input() -> None:
    """B 对照（red-b-review 矩阵）：kinship.kind=unknown → 关系未决（≠false）→
    追问并定位 relation 的 kinship 事实，不得授扣。"""
    year = "2025_26"
    result = _run(_s26d_erce_input(
        year, relation=_relation(year, "r1", "p1", "d1", kind="unknown", cash="15000")))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    questions = _structured_questions(result)
    assert _locates(questions, ["r1", "kinship"], year=year, subject_id="d1"), questions


def _couple_household(year: str, apart: Any = False) -> dict:
    w_from, w_to = _window(year)
    return {
        "marriage": [{"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}],
        "living_apart": _tf(year, apart) if apart is not None else [],
        "child_nomination": {"person_id": None, "status": "none"},
    }


def _s26d_spouse_payer_input(year: str, *, relation: dict, payer: str, apart: Any) -> dict:
    return _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "1950-05-01"}],
        household=_couple_household(year, apart=apart),
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        subject_facts={"d1": {"govt_disability_allowance": _tf(year, True)}},
        relations=[relation],
        care_payments=[_care_payment(
            care_from=_window(year)[0], care_to=_window(year)[1],
            home_status=_tf(year, True), payer=payer)],
        # red-b-review 修正 #1：夫妻法定基线显式声明已核 actual 选择（不依赖
        # 全局 _base_year 默认）——使失败只指向 s.26D 付款人/分居判定。
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": _ordinary_couple_actual()},
        financials={
            "p1": _fin(deduction_items=[{
                "item_type": "elder_residential_care", "amount": "80000",
                "payment_ids": ["e1"], "eligibility": {"value": True}}]),
            "p2": _fin(nai="0", mpf="0", ai=True),
        },
    )


@pytest.mark.parametrize(
    "relation_kind,relation_actor,payer,apart,expect",
    [
        ("parent", "p1", "p1", False, "allowed"),
        ("grandparent", "p1", "p1", False, "allowed"),
        ("parent", "p1", "p2", False, "allowed"),
        ("grandparent", "p2", "p2", False, "allowed"),
        ("parent", "p2", "p1", False, "allowed"),
        ("parent", "p2", "p2", False, "allowed"),
        ("grandparent", "p1", "p2", False, "allowed"),
        ("grandparent", "p2", "p1", False, "allowed"),
        ("parent", "p1", "p2", True, "denied"),
        ("parent", "p1", "p2", None, "needs_input"),
    ],
    ids=["direct-parent-self-control", "direct-grandparent-self",
         "direct-parent-spouse-payer", "spouse-grandparent-spouse-payer",
         "spouse-parent-self-payer", "spouse-parent-spouse-payer",
         "own-grandparent-spouse-payer", "spouse-grandparent-self-payer",
         "separated-spouse-payer", "unknown-apart-spouse-payer"],
)
def test_002_s26d_spouse_payer_nonseparation_matrix(
    relation_kind, relation_actor, payer, apart, expect
) -> None:
    """B（s.26D(1)/(5) 逐字）：本人或非分居配偶付款、父母/祖父母（含配偶方）；
    已证分居 → 不成立；分居未知 → 不当已证非分居。」

    - allowed：Part 4A＝9,000＋80,000=89,000、final 0；
    - denied：分居 ⇒ 配偶付款不成立 ⇒ Part 4A=9,000、final 940；
    - needs_input：分居未知 ⇒ 追问（不得默认非分居）。
    """
    year = "2025_26"
    result = _run(_s26d_spouse_payer_input(
        year,
        relation=_relation(year, "r1", relation_actor, "d1", kind=relation_kind,
                           cash="15000"),
        payer=payer,
        apart=apart,
    ))
    if expect == "allowed":
        assert result["status"] == "partial", result["status"]
        assert _unit_base(result, "salary:p1", "part4a") == "89000/1", (
            f"配偶（非分居）付款/配偶方父母祖父母应可扣，实际："
            f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
        )
        assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]
    elif expect == "denied":
        assert result["status"] == "partial", result["status"]
        assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
            f"已证分居 ⇒ 配偶付款不成立，实际：{_unit_base(result, 'salary:p1', 'part4a')!r}"
        )
        assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]
    else:
        assert result["status"] == "needs_input", result["status"]
        _assert_null_amounts(result)
        assert _locates_any(_structured_questions(result), [
            ["living_apart"],
            ["marriage"],
        ], year=year), result.get("questions")


@pytest.mark.parametrize(
    "birth,daa,expect",
    [("1965-09-01", False, "deductible"),
     ("1970-01-01", True, "deductible"),
     ("1990-01-01", False, "ineligible"),
     ("1990-01-01", None, "unknown"),
     ("1990-01-01", "unknown", "unknown")],
    ids=["payment-year-age60", "payment-year-daa-under60",
         "under60-no-daa-ineligible", "under60-daa-missing", "under60-daa-unknown"],
)
def test_002_erce_prior_year_care_payment_year_age_or_daa(birth, daa, expect) -> None:
    """B（s.26D(1) 逐字：付款 YA 内任一时点 60+，或 <60 而具 DAA 资格）：care 区间
    整段落在付款 YA 之前、付款在本年，仍按付款 YA 判定年龄/DAA；home 状态按 care
    区间求值（005 §4.8）。红处方补充：under60 且 DAA=false → 可判定不获；DAA
    缺失/unknown → 未决＋定位 DAA 事实。

    T3：Part 4A=9,000+80,000=89,000 → NCI=19,000 → 380−380=0；不获时 9,000 → 940。
    """
    year = "2025_26"
    d1_facts: dict = {}
    if daa is not None:
        d1_facts["govt_disability_allowance"] = _tf(year, daa)
    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": birth}],
        subject_facts={"d1": d1_facts},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent", cash="15000")],
        care_payments=[_care_payment(
            care_from="2024-08-01", care_to="2025-02-01",
            home_status=[{"from": "2024-08-01", "to": "2025-02-01", "value": True}])],
        financials={"p1": _fin(deduction_items=[{
            "item_type": "elder_residential_care", "amount": "80000",
            "payment_ids": ["e1"], "eligibility": {"value": True}}])},
    )
    result = _run(facts)
    if expect == "deductible":
        assert result["status"] == "partial", result["status"]
        assert _unit_base(result, "salary:p1", "part4a") == "89000/1", (
            f"付款 YA 年龄/DAA 成立时上年 care 服务可扣，实际："
            f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
        )
        assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]
    elif expect == "ineligible":
        assert result["status"] == "partial", result["status"]
        assert not (result.get("questions") or []), result.get("questions")
        assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
            f"under60 且 DAA=false 不得计入 ERCE，实际："
            f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
        )
        assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]
    else:
        assert result["status"] == "needs_input", result["status"]
        _assert_null_amounts(result)
        assert _locates(
            _structured_questions(result), ["govt_disability_allowance"],
            year=year, subject_id="d1",
        ), result.get("questions")


@pytest.mark.parametrize(
    "home_basis",
    ["cap459_licence", "cap459_s3_excluded", "cap633_scheduled_nursing_s128_exemption",
     "cap613_licence", "cap613_exemption"],
)
def test_002_s26d_home_basis_approved_types_deductible(home_basis) -> None:
    """B（协调员对保存 Cap.112 快照 32881–32981 逐项归并）：五项法定院舍类型均可
    计入（005 §4.8 已批准 home_basis 枚举）；不另增 wire 类型。"""
    year = "2025_26"
    facts = _s26d_erce_input(
        year,
        relation=_relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
    )
    facts["years"][year]["care_payments"][0]["home_basis"] = home_basis
    result = _run(facts)
    assert result["status"] == "partial", (home_basis, result["status"])
    assert _unit_base(result, "salary:p1", "part4a") == "89000/1", (
        f"home_basis={home_basis} 属已批准法定类型，应可扣，实际："
        f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


@pytest.mark.parametrize("home_basis", ["other", "unknown"])
def test_002_s26d_home_basis_unverified_not_auto_eligible(home_basis) -> None:
    """B 对照：other/unknown 不自动认定院舍合资格（未确定 → 不产确定扣除）。"""
    year = "2025_26"
    facts = _s26d_erce_input(
        year,
        relation=_relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
    )
    facts["years"][year]["care_payments"][0]["home_basis"] = home_basis
    result = _run(facts)
    assert result["status"] == "needs_input", (home_basis, result["status"])
    _assert_null_amounts(result)
    assert not [
        d for d in _decisions(result)
        if d.get("item_kind") == "elder_residential_care" and d.get("grant") == "allowed"
    ], (home_basis, _decisions(result))


def test_002_s26d_home_status_false_not_deductible() -> None:
    """B（red-b-review：category cannot replace inforce TF）：home_basis 为已批准
    法定类型但 home_status_in_force 显式 false → 原始在院事实不成立 → 不获扣除
    （T3：Part 4A=9,000、final 940），可判定、不追问。"""
    year = "2025_26"
    facts = _s26d_erce_input(
        year,
        relation=_relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
    )
    facts["years"][year]["care_payments"][0]["home_status_in_force"] = [
        {"from": "2025-04-01", "to": "2026-04-01", "value": False}]
    result = _run(facts)
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), result.get("questions")
    assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
        f"home_status=false 不得计入 ERCE，实际："
        f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]
    assert not [
        d for d in _decisions(result)
        if d.get("item_kind") == "elder_residential_care" and d.get("grant") == "allowed"
    ], _decisions(result)


@pytest.mark.parametrize(
    "home_status_tf",
    [
        [{"from": "2025-04-01", "to": "2026-04-01", "value": "unknown"}],
        [{"from": "2025-04-01", "to": "2025-10-01", "value": True}],
    ],
    ids=["explicit-unknown", "uncovered-window"],
)
def test_002_s26d_home_status_unknown_or_uncovered_needs_input(home_status_tf) -> None:
    """B（red-b-review：homequestion）：home_status_in_force 显式 unknown 或未
    覆盖 care 区间 → 未决＋定位该 TF 事实（不得由 home_basis 法定类别替代
    原始在院事实）。"""
    year = "2025_26"
    facts = _s26d_erce_input(
        year,
        relation=_relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
    )
    facts["years"][year]["care_payments"][0]["home_status_in_force"] = home_status_tf
    result = _run(facts)
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    assert _locates(
        _structured_questions(result), ["home_status_in_force"],
        year=year, subject_id="d1",
    ), result.get("questions")
    assert not [
        d for d in _decisions(result)
        if d.get("item_kind") == "elder_residential_care" and d.get("grant") == "allowed"
    ], _decisions(result)


def _legal_erce_year(year: str) -> dict:
    window = _window(year)
    return _base_year(
        year,
        subject_facts={"d1": {"govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent", cash="15000")],
        care_payments=[_care_payment(
            care_from=window[0], care_to=window[1],
            paid_on=window[0],  # red-b-review 修正 #2：合法基线 paid_on 须落在键年
            home_status=_tf(year, True))],
        financials={"p1": _fin(deduction_items=[{
            "item_type": "elder_residential_care", "amount": "80000",
            "payment_ids": ["e1"], "eligibility": {"value": True}}])},
    )


def test_002_erce_paid_on_outside_current_year_rejected() -> None:
    """B：paid_on 必须落在其所在 Year 内（005 §4.8）→ 精确拒绝（唯一变量）。

    先以合法基线证明控制可通过，再只改 paid_on 为键年外日期。
    """
    from app.core.errors import AppError

    year = "2025_26"
    facts = {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "1950-05-01"}],
        "years": {year: _legal_erce_year(year)},
        "hk_presence_days": [],
        "assumptions": [],
    }
    control = _run(facts)
    assert control["status"] == "partial", (
        f"合法基线（paid_on 在键年内）须可算，实际 {control['status']!r}"
    )
    facts["years"][year]["care_payments"][0]["paid_on"] = "2024-06-15"
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
        field = str(exc.field or "")
        assert field.startswith(f"years.{year}.care_payments") and field.endswith(".paid_on"), field
    else:
        raise AssertionError("paid_on 落在键年之外必须拒绝（005 §4.8），不得静默采纳")


def test_002_erce_paid_on_outside_explicit_next_year_rejected() -> None:
    """B：显式下一年对象内的 paid_on 同样受其自身 Year 域约束（全部显式年度先验），
    不得因 next 抑制而吞掉输入错误。

    先以两年合法基线证明控制可通过（_legal_erce_year 按各自键年产出 paid_on），
    再将 2026_27 的 paid_on 改为键年 2025_26 内、但落在 2026_27 之外的日期。
    """
    from app.core.errors import AppError

    year = "2025_26"
    next_year = "2026_27"
    facts = {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "d1", "birth_date": "1950-05-01"}],
        "years": {year: _legal_erce_year(year), next_year: _legal_erce_year(next_year)},
        "hk_presence_days": [],
        "assumptions": [],
    }
    control = _run(facts)
    assert control["status"] in ("partial", "complete"), (
        f"两年合法基线（各自 paid_on 在各自键年内）须可算，实际 {control['status']!r}"
    )
    facts["years"][next_year]["care_payments"][0]["paid_on"] = "2025-06-15"
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
        field = str(exc.field or "")
        assert field.startswith(f"years.{next_year}.care_payments") and field.endswith(".paid_on"), field
    else:
        raise AssertionError("显式下一年对象内 paid_on 越域必须拒绝（全部年度先验）")


def test_002_erce_same_item_duplicate_payment_rejected() -> None:
    """B：同一 item 重复引用同一付款 ID（payment_ids=["e1","e1"] 且金额＝两次合计）
    → E_INPUT_CONTRADICTORY（不得按两次计入）。"""
    from app.core.errors import AppError

    year = "2025_26"
    facts = _s26d_erce_input(
        year,
        relation=_relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
        amount="160000",
    )
    facts["years"][year]["financials"]["p1"]["deduction_items"][0]["payment_ids"] = ["e1", "e1"]
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
        assert str(exc.field or "").startswith(
            f"years.{year}.financials.p1.deduction_items"
        ), exc.field
    else:
        raise AssertionError("同一 item 重复引用同一付款必须拒绝（005 §6.2 对账）")


def test_002_erce_same_elder_across_persons_different_payments_rejected() -> None:
    """B：同一长者跨人（不同付款 ID）重复进入一个 scenario → 拒绝
    （s.26D(4)(a)「不得多于一人同一父母/祖父母」；无争议事实取拒绝分支）。"""
    from app.core.errors import AppError

    year = "2025_26"
    window = _window(year)

    def care(pid: str, payer: str) -> dict:
        payment = _care_payment(care_from=window[0], care_to=window[1],
                                home_status=_tf(year, True), amount="60000", payer=payer)
        payment["payment_id"] = pid
        return payment

    def item(eid: str) -> dict:
        return {"item_type": "elder_residential_care", "amount": "60000",
                "payment_ids": [eid], "eligibility": {"value": True}, "subject_id": "d1"}

    facts = _salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"},
         {"subject_id": "d1", "birth_date": "1950-05-01"}],
        household=_couple_household(year),
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        # red-b-review 修正 #1：夫妻基线显式声明已核 actual 选择（ordinary）。
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": _ordinary_couple_actual()},
        subject_facts={"d1": {"govt_disability_allowance": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent", cash="15000"),
                   _relation(year, "r2", "p2", "d1", kind="parent", cash="15000")],
        care_payments=[care("e1", "p1"), care("e2", "p2")],
        financials={"p1": _fin(deduction_items=[item("e1")]),
                    "p2": _fin(nai="200000", mpf="18000", deduction_items=[item("e2")])},
    )
    try:
        _run(facts)
    except AppError as exc:
        assert exc.code == "E_INPUT_CONTRADICTORY", exc.code
    else:
        raise AssertionError("同一长者跨人重复进入同一 scenario 必须拒绝（005 §6.2）")
