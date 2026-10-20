# -*- coding: utf-8 -*-
"""REQ-4 父母／祖父母免税额资格（002 P1 Red；Annex 005 事实契约）。

REQ: REQ-4（S002 §8/§9.4；PRD AC-4）。本文件为 SDD §9.4 REQ-4 命名节点，共 9 个：
  - test_002_parent_60plus_basic_extra_separate
  - test_002_parent_under60_daa_eligible_high_tier
  - test_002_parent_55_59_daa_ineligible_all_not_anytime_required_unknown_not_false
  - test_002_parent_six_calendar_month_cohabitation_non_full_consideration
  - test_002_parent_cash_threshold_12000_annual_total
  - test_002_parent_maintenance_independent_of_age_window
  - test_002_parent_daa_eligibility_not_receipt
  - test_002_parent_extra_full_year_only_claim
  - test_002_parent_month_end_case_manual_not_invented

规格锚点:
  - S002 §3.2/§5.2（parent/grandparent 谓词；供养独立；全年同住额外额独立 claim）；
  - Annex 005 §7（s.30/30A 高档＝ANY(居港 AND (60+ OR <60 且 DAA))，DAA 只问资格；
    s.30(1A) 55–59 档＝ANY(居港)+ANY(55≤age<60)+未达 60+ALL(NOT DAA)，unknown
    不默认 false；s.30(4)(a) 供养＝年度现金 ≥12,000 OR 连续六个日历月非足值同住；
    六个月日历月＝包含 [d, 相隔6个月同日)，相邻 true 区间先合并；月末边界
    OPEN→窄 manual；s.30(3)(b) 全年同住额外＝基本 entitlement＋ALL(cohabit,YA)）；
  - Annex 005 §15.2（parent_high/low 及 cohabitation 各年度金额；现金门槛 12,000）；
    §13/§14（questions 定位；steps decision entitlement 含 "manual"）；§2.1。

期望值来源（禁从生产代码反推）:
  - 免税额/门槛：Annex 005 §15.2（转引附表 4，T1）。
  - 税率带：附表 1/2（Annex D D7；T1 台账 1.17）。
  - 无额外免税额基线 final 940：PAM39 Q1（T2 台账 1.13）。
  - 其余组合数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None)（005 §2.1）：
    v2 subject/year 事实模型；父母资格按 §7 逐条款量词（年龄按年内窗口求值，
    不用年度末年龄替代）；供养双路径独立于年龄档；额外额独立 claim；月末
    依赖截短约定的个案 → decision entitlement "manual"（可定位、不整类禁算）。
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
        "manual_issues": [],
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
    kind: str = "parent",
    basis: str = "birth",
    during: Any = True,
    maintained: list | None = None,
    cash: str | None = None,
    cohabit: list | None = None,
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
    return r


def _claim(cid: str, kind: str, claimant: str = "p1", subject: str = "d1") -> dict:
    return {"claim_id": cid, "kind": kind, "claimant_id": claimant, "subject_id": subject}


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
    保留免 `[0]`/h1/b1/item_type 字面量原则。
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


def _resident_parent_facts(year: str, birth: str, daa: Any = None) -> dict:
    """60+ 居港父母公共事实（DAA 可选覆盖）。"""
    facts: dict[str, Any] = {"ordinarily_resident_hk": _tf(year, True)}
    if daa is not None:
        facts["govt_disability_allowance"] = _tf(year, daa)
    return facts


# ---------------------------------------------------------------------------
# REQ-4 命名节点（S002 §9.4）
# ---------------------------------------------------------------------------


def test_002_parent_60plus_basic_extra_separate() -> None:
    """60+ 父母基本额与全年同住额外额为两个独立 claim、独立 decision。

    规格锚点：Annex 005 §3.2（全年同住额外额独立 claim）；§7（s.30(3)(b) 行）；
    §15.2（2025_26 各 50,000）。
    T3：Part 5=132,000+50,000+50,000=232,000；NCI=240,000−9,000−232,000=−1,000→0；
    final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000",
                             cohabit=_tf(year, True))],
        claims=[_claim("c1", "parent"), _claim("c2", "parent_cohabitation_additional")],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    basic = _decision(result, claim_id="c1")
    extra = _decision(result, claim_id="c2")
    assert basic is not None and basic.get("entitlement") == "eligible", basic
    assert basic.get("amount") == "50000", basic
    assert extra is not None and extra.get("entitlement") == "eligible", extra
    assert extra.get("amount") == "50000", extra
    assert _unit_base(result, "salary:p1", "allowances") == "232000/1", (
        f"基本 50,000＋额外 50,000 独立合计，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "0", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_parent_under60_daa_eligible_high_tier() -> None:
    """未满 60 而 DAA 资格成立 → 高档（50,000），非 55–59 低档。

    规格锚点：Annex 005 §7（s.30/30A 高档＝ANY(居港 AND (60+ OR (<60 AND DAA)))）。
    T3：Part 5=132,000+50,000=182,000；NCI=240,000−9,000−182,000=49,000 →
    49,000×2%=980；标准 231,000×15%=34,650 → 择低 980；宽减 min(980,3,000)=980
    → final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1975-06-01")]),  # 2025/26 内 49–50 岁
        subject_facts={"d1": _resident_parent_facts(year, "1975-06-01", daa=True)},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000")],
        claims=[_claim("c1", "parent")],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", decision
    assert decision.get("amount") == "50000", (
        f"<60 且 DAA 资格 → 高档 50,000（不落 55–59 档），实际：{decision!r}"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "182000/1"
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "980", amounts
    assert amounts["reduction"] == "980", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_parent_55_59_daa_ineligible_all_not_anytime_required_unknown_not_false() -> None:
    """55–59 档 ALL(NOT DAA)：DAA unknown → needs_input（不默认 false）；false → 低档。

    规格锚点：Annex 005 §7（s.30(1A) 行：unknown 不得默认 false；L35953–35980）。
    """
    year = "2025_26"
    subjects = _subjects_with([("d1", "1968-01-01")])  # 2025/26 内 57–58 岁
    relation = _relation(year, "r1", "p1", "d1", cash="15000")
    claim = [_claim("c1", "parent")]

    unknown = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {"ordinarily_resident_hk": _tf(year, True)}},  # DAA 缺失
        relations=[relation], claims=claim,
    ))
    assert unknown["status"] == "needs_input", (
        f"ALL(NOT DAA) 遇 DAA 缺失＝unknown → needs_input，实际 {unknown['status']!r}"
    )
    _assert_null_amounts(unknown)
    assert _locates(_structured_questions(unknown), ["d1"], year=year,
                    claim_id="c1", subject_id="d1")

    false_case = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": _resident_parent_facts(year, "1968-01-01", daa=False)},
        relations=[relation], claims=claim,
    ))
    decision = _decision(false_case, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", decision
    assert decision.get("amount") == "25000", (
        f"55–59 档低档 25,000，实际：{decision!r}"
    )
    assert not (false_case.get("questions") or [])


def test_002_parent_six_calendar_month_cohabitation_non_full_consideration() -> None:
    """供养替代路径：非足值对价同住满六个日历月即成立；差一日不成立。

    规格锚点：Annex 005 §7（s.30(4)(a)(ii) 行：区间包含 [d, 相隔6个月同日)）。
    """
    year = "2025_26"
    subjects = _subjects_with([("d1", "1950-05-01")])
    base_facts = {"d1": _resident_parent_facts(year, "1950-05-01")}

    # (a) [2025-06-15, 2025-12-15) 恰含 [06-15, 12-15) 六个日历月 → 供养成立。
    six_months = _run(_salary_input(
        year, subjects,
        subject_facts=base_facts,
        relations=[_relation(year, "r1", "p1", "d1",
                             cohabit=[{"from": "2025-06-15", "to": "2025-12-15", "value": True}])],
        claims=[_claim("c1", "parent")],
    ))
    decision = _decision(six_months, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"六日历月非足值同住足以成立供养（现金为 0），实际：{decision!r}"
    )
    assert decision.get("amount") == "50000", decision
    assert not (six_months.get("questions") or [])

    # (b) [2025-06-15, 2025-12-14) 差一日＋现金显式 "0"＋其余显式 false → 可判定不供养。
    one_day_short = _run(_salary_input(
        year, subjects,
        subject_facts=base_facts,
        relations=[_relation(year, "r1", "p1", "d1", cash="0", cohabit=[
            {"from": "2025-04-01", "to": "2025-06-15", "value": False},
            {"from": "2025-06-15", "to": "2025-12-14", "value": True},
            {"from": "2025-12-14", "to": "2026-04-01", "value": False},
        ])],
        claims=[_claim("c1", "parent")],
    ))
    decision = _decision(one_day_short, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"差一日不满六日历月且现金 0 → 不构成 s.30(4) 供养，实际：{decision!r}"
    )
    assert not (one_day_short.get("questions") or [])
    assert one_day_short["amounts"]["final_after_reduction"] == "940", (
        one_day_short["amounts"]
    )


def test_002_parent_cash_threshold_12000_annual_total() -> None:
    """年度现金供养 ≥12,000 成立；11,999 且同住显式 false → 不成立（可判定）。

    规格锚点：Annex 005 §7（s.30(4)(a)(i) 行；年度合计门槛 12,000＝§15.2）。
    """
    year = "2025_26"
    subjects = _subjects_with([("d1", "1950-05-01")])
    base_facts = {"d1": _resident_parent_facts(year, "1950-05-01")}
    claims = [_claim("c1", "parent")]

    at_threshold = _run(_salary_input(
        year, subjects, subject_facts=base_facts,
        relations=[_relation(year, "r1", "p1", "d1", cash="12000")],
        claims=claims,
    ))
    decision = _decision(at_threshold, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"年度现金恰 12,000 → 供养成立，实际：{decision!r}"
    )
    assert decision.get("amount") == "50000", decision

    below = _run(_salary_input(
        year, subjects, subject_facts=base_facts,
        relations=[_relation(year, "r1", "p1", "d1", cash="11999",
                             cohabit=_tf(year, False))],
        claims=claims,
    ))
    decision = _decision(below, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"11,999＜12,000 且同住 false → 双路径均不成立，实际：{decision!r}"
    )
    assert not (below.get("questions") or [])


def test_002_parent_maintenance_independent_of_age_window() -> None:
    """供养窗口与年龄资格窗口无需相交（供养独立于年龄档）。

    规格锚点：Annex 005 §7（s.30(4) 行：独立于年龄档；不要求年龄资格覆盖供养窗口）。
    构造：父母 2025-11-15 满 60；非足值同住 [2025-04-15, 2025-10-15)（六日历月，
    全部在 60 岁生日前、且在键年窗口内）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1965-11-15")]),
        subject_facts={"d1": _resident_parent_facts(year, "1965-11-15")},
        relations=[_relation(year, "r1", "p1", "d1",
                             cohabit=[{"from": "2025-04-15", "to": "2025-10-15", "value": True}])],
        claims=[_claim("c1", "parent")],
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        "同住供养窗口早于 60 岁生日不妨碍高档资格（ANY(age≥60) 年内成立即可），"
        f"实际：{decision!r}"
    )
    assert decision.get("amount") == "50000", decision
    assert not (result.get("questions") or [])


def test_002_parent_daa_eligibility_not_receipt() -> None:
    """DAA 只问资格不问领取：55–59 岁父母 DAA 资格成立 → 高档（非低档）。

    规格锚点：Annex 005 §4.3（govt_disability_allowance＝资格期间，不问领取）；
    §7（高档 (<60 AND DAA)）；输入模型中不存在任何「已领取」事实。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1968-04-01")]),  # 2025/26 内 57–58 岁
        subject_facts={"d1": _resident_parent_facts(year, "1968-04-01", daa=True)},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000")],
        claims=[_claim("c1", "parent")],
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", decision
    assert decision.get("amount") == "50000", (
        f"DAA 资格成立 → 高档 50,000（不落 55–59 低档 25,000），实际：{decision!r}"
    )
    assert not (result.get("questions") or [])


def test_002_parent_extra_full_year_only_claim() -> None:
    """全年同住额外额要求 ALL(cohabit, YA)：11 个月 → 额外 ineligible、基本不受影响。

    规格锚点：Annex 005 §7（s.30(3)(b)/(3A)(b) 行）；§3.2（独立 claim）。
    T3（负例）：基本仍在 → Part 5=132,000+50,000=182,000。
    """
    year = "2025_26"
    claims = [_claim("c1", "parent"), _claim("c2", "parent_cohabitation_additional")]

    full_year = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000",
                             cohabit=_tf(year, True))],
        claims=claims,
    ))
    extra = _decision(full_year, claim_id="c2")
    assert extra is not None and extra.get("entitlement") == "eligible", extra

    eleven_months = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000", cohabit=[
            {"from": "2025-04-01", "to": "2026-03-01", "value": True},
            {"from": "2026-03-01", "to": "2026-04-01", "value": False},
        ])],
        claims=claims,
    ))
    basic = _decision(eleven_months, claim_id="c1")
    extra = _decision(eleven_months, claim_id="c2")
    assert basic is not None and basic.get("entitlement") == "eligible", basic
    assert basic.get("amount") == "50000", basic
    assert extra is not None and extra.get("entitlement") == "ineligible", (
        f"同住未满全年 → 额外额 ineligible（ALL 不满足），实际：{extra!r}"
    )
    assert not (eleven_months.get("questions") or [])
    assert _unit_base(eleven_months, "salary:p1", "allowances") == "182000/1"


def test_002_parent_month_end_case_manual_not_invented() -> None:
    """月末起始的六日历月（对日本不存在）＝OPEN 边界 → 窄 manual，不发明规则。

    规格锚点：Annex 005 §7（六个月日历月行：月末不存在同日的精确边界仍标 OPEN；
    仅依赖月末截短约定才能成立的个案转 manual，不整类禁算）；§14（decision
    entitlement "manual"）。
    构造：d2 同住 [2025-08-31, 2026-03-01)（08-31 起、六个月后同日 02-31 不存在）、
    现金显式 "0"；d1 为干净对照（现金 15,000）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01"), ("d2", "1949-03-03")]),
        subject_facts={
            "d1": _resident_parent_facts(year, "1950-05-01"),
            "d2": _resident_parent_facts(year, "1949-03-03"),
        },
        relations=[
            _relation(year, "r1", "p1", "d1", cash="15000"),
            _relation(year, "r2", "p1", "d2", cash="0",
                      cohabit=[{"from": "2025-08-31", "to": "2026-03-01", "value": True}]),
        ],
        claims=[_claim("c1", "parent"), _claim("c2", "parent", subject="d2")],
    ))
    assert result.get("status") != "blocked", (
        "月末边界是窄 manual，不得整类禁算（005 §7）"
    )
    manual = _decision(result, subject_id="d2")
    assert manual is not None and manual.get("entitlement") == "manual", (
        "月末截短个案 → decision entitlement=manual（可提交、可定位），"
        f"实际：{manual!r}"
    )
    clean = _decision(result, subject_id="d1")
    assert clean is not None and clean.get("entitlement") == "eligible", (
        f"对照父母（现金供养）不受月末边界影响，实际：{clean!r}"
    )
    assert clean.get("amount") == "50000", clean


def test_002_cohab_extra_without_basic_intent_needs_input() -> None:
    """P1 r1 新 Red（复核 §六.8）：仅申索全年同住额外额而无基本 parent
    申索意图 → 须追问基本意图（不得直接授 50,000）。

    规格锚点：Annex 005 §7（s.30(3)(b) 行：「仅额外意图时要求一并明确
    基本申索意图」）。复核 §三 R5（观察：仅 extra 仍给 50000）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000",
                             cohabit=_tf(year, True))],
        claims=[_claim("c2", "parent_cohabitation_additional")],  # 无基本 claim
    ))
    decision = _decision(result, claim_id="c2")
    assert not (decision is not None and decision.get("grant") == "allowed"), (
        f"仅额外意图而无基本申索意图 → 不得直接授予额外额，实际：{decision!r}"
    )
    assert result["status"] == "needs_input", (
        f"须追问基本申索意图（s.30(3)(b) 行），实际 {result['status']!r}"
    )


def test_002_parent_death_in_year_manual_boundary() -> None:
    """P1 r1 新 Red（复核 §六.8/§三 R5 新增）：受养人年内死亡 → 死亡年度
    影响总额 → 该 claim 转窄 manual（可定位），不给确定金额、不整类禁算。

    规格锚点：Annex 005 §7 表后段（「死亡/争议事实影响总额时…死亡年度…
    可提交、可定位转人工，不整类禁算」；S002 §12.3）。
    复核 §三 R5（死亡年度未转人工，新增缺口）。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": {
            "ordinarily_resident_hk": _tf(year, True),
            "govt_disability_allowance": _tf(year, True),
            "death_date": "2025-09-01",  # 年内死亡
        }},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000")],
        claims=[_claim("c1", "parent")],
    ))
    assert result.get("status") != "blocked", (
        "死亡年度是窄 manual 边界，不得整类禁算"
    )
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "manual", (
        f"受养人年内死亡（2025-09-01）→ 该 claim 转 manual（不产确定年额），"
        f"实际：{decision!r}"
    )


# ---------------------------------------------------------------------------
# B lane 补齐（p1-r2-red-completion-plan §B）：父母关系期间/未核 basis 活动资格
# ---------------------------------------------------------------------------


def test_002_parent_relation_during_false_ineligible() -> None:
    """B（R5.1）：父母 entitlement 须消费关系期间——during 显式 false → 关系无效
    → ineligible（可判定不追问）；供养/年龄/同住事实各自独立量化、不强制同点相交。

    T3：无父母额 → NCI=99,000 → 3,940−3,000=940。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000", during=False)],
        claims=[_claim("c1", "parent")],
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"关系期间显式 false → 父母额不成立，实际：{decision!r}"
    )
    assert decision.get("amount") is None, decision
    assert not (result.get("questions") or []), result.get("questions")
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]


def test_002_parent_relation_during_missing_needs_input() -> None:
    """B：关系期间缺失＝未知（≠false）→ 追问、不得默认成立/否；定位 relation
    的 during 事实（不只 subject d1）。"""
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", cash="15000", during=None)],
        claims=[_claim("c1", "parent")],
    ))
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    assert _locates(_structured_questions(result), ["during"], year=year,
                    claim_id="c1", subject_id="d1"), result.get("questions")
    assert not [
        d for d in _decisions(result)
        if d.get("claim_id") == "c1" and d.get("grant") == "allowed"
    ], _decisions(result)


def test_002_parent_unverified_kinship_basis_manual_locator() -> None:
    """B：未核亲属组合（parent/other）→ 窄 manual（可提交、可定位 kinship.basis），
    不是月底 OPEN 边界问题也不整类禁算。"""
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "1950-05-01")]),
        subject_facts={"d1": _resident_parent_facts(year, "1950-05-01")},
        relations=[_relation(year, "r1", "p1", "d1", basis="other", cash="15000")],
        claims=[_claim("c1", "parent")],
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "manual", (
        f"parent/other 未在普通已核映射内 → 转人工核实，实际：{decision!r}"
    )
    assert result["status"] == "needs_input", result["status"]
    _assert_null_amounts(result)
    assert _locates(_structured_questions(result), ["kinship.basis"],
                    year=year, claim_id="c1", subject_id="d1"), (
        f"追问须绑定 r1 的 kinship.basis（field+year+claim/subject），实际：{result.get('questions')!r}"
    )
