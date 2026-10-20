# -*- coding: utf-8 -*-
"""REQ-10 六金额键、组件状态与 best 语义（002 P1 Red；Annex 005）。

REQ: REQ-10（S002 §8/§9.4；PRD AC-10）。本文件为 SDD §9.4 REQ-10 引擎侧命名
节点（6）＋V006 §5.4 有符号余额节点（1），共 7 个：
  - test_002_six_keys_null_never_zero
  - test_002_pa_always_partial_balance_null_payment_out_of_scope
  - test_002_salary_balance_null_when_paid_or_partial
  - test_002_best_independent_of_paid_unknown_competitor_blocks
  - test_002_explicit_selected_known_retains_final
  - test_002_blocked_all_six_null_c3
  - test_002_negative_salary_balance_full_context_refund（V006 §5.4）

规格锚点:
  - S002 §5.5/§8 REQ-10＋ADR-10；Annex 005 §10（组件适用与状态表：salary
    partial → balance null；仅 complete 且 paid 已知时 final−paid（可负＝
    估算退税，唯一有符号例外）；PA 恒 partial、balance 恒 null、
    payment_comparison 范围外；best 仅依候选本年 final＋资格，独立于 paid/
    next；unknown/unresolved 竞争者 → best null；显式选中已知候选可保留
    final；blocked 六键全 null）、§9.3（selection=best 不能确定时保留各已知
    scenario、top-level final=null；平局择案）、§13/§14（steps tagged 形状：
    scenario/selection/next_year）、§2/§2.1。

期望值来源（禁从生产代码反推）:
  - 免税额/宽减：Annex 005 §15.2（附表 4/43 T1）；税率带：附表 1/2（T1）。
  - PAM39 Q1 final 940（T2 台账 1.13）。
  - 其余数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None) 与
    compare_personal_assessment(facts, bundle, *, availability=None)
    （005 §2.1）。
  - 六键 null≠0；blocked 六键显式 null＋blocked_reason；salary complete 时
    balance=final−paid（可为负）；partial → balance null；PA 恒 partial 且无
    payment_comparison 键；best 独立 paid、unknown 竞争者阻 best；显式选中
    已知候选保留 final；steps 含 scenario/selection/next_year tagged 形状。
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


def _resident_at_reference() -> list[dict]:
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
    **year_kw: Any,
) -> dict:
    return {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": years or {year: _base_year(year, **year_kw)},
        "hk_presence_days": [],
        "assumptions": [],
    }


def _subjects_single() -> list[dict]:
    return [{"subject_id": "p1", "birth_date": "1990-01-01"}]


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


def _run_pa(v2_input: dict) -> dict:
    from app.engines.personal_assessment import compare_personal_assessment

    bundle = _v2_bundle()
    return compare_personal_assessment(
        v2_input, bundle, availability=_availability(bundle, v2_input["year_of_assessment"])
    )


# ---------------------------------------------------------------------------
# 断言辅助（005 §13/§14）
# ---------------------------------------------------------------------------


def _assert_six_keys_all_null(result: Mapping) -> None:
    amounts = result["amounts"]
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), (
        f"amounts 必须恰含 C3.4 六键（键不省略），实际：{sorted(amounts)!r}"
    )
    assert all(v is None for v in amounts.values()), (
        f"null 不得写成 \"0\" 或省略，实际：{amounts!r}"
    )


def _steps(result: Mapping, kind: str) -> list[dict]:
    return [
        s.get("detail") or {}
        for s in (result.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == kind and isinstance(s.get("detail"), dict)
    ]


def _decisions(result: Mapping) -> list[dict]:
    return _steps(result, "decision")


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


# ---------------------------------------------------------------------------
# REQ-10 命名节点（S002 §9.4 引擎侧＋V006 §5.4）
# ---------------------------------------------------------------------------


def test_002_six_keys_null_never_zero() -> None:
    """needs_input：六金额键显式 null、键不省略、null≠"0"。

    规格锚点：C3.3/C3.4（经 S002 §8 REQ-10 引用）＋Annex 005 §10。
    构造：婚姻 TF 空（unknown）→ needs_input。
    """
    year = "2025_26"
    result = _run(_input(
        year,
        _subjects_single(),
        household={"marriage": [], "living_apart": [],
                   "child_nomination": {"person_id": None, "status": "none"}},
    ))
    assert result["status"] == "needs_input", result["status"]
    _assert_six_keys_all_null(result)
    _structured_questions(result)
    blob = json.dumps(result["amounts"], ensure_ascii=False, default=str)
    assert '"0"' not in blob, (
        f"未知组件不得以 \"0\" 呈现（null≠0），实际：{result['amounts']!r}"
    )


def test_002_pa_always_partial_balance_null_payment_out_of_scope() -> None:
    """PA 本阶段恒 partial、balance 恒 null；payment_comparison 范围外（无键）。

    规格锚点：S002 §5.4/ADR-10＋Annex 005 §10（PA 列）。
    T3：单身 240k/9k、两候选均可算（平局 940/940 → 优先 PA，§9.3）。
    """
    year = "2025_26"
    result = _run_pa(_input(
        year,
        _subjects_single(),
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    assert result["status"] == "partial", (
        f"PA 本阶段恒 partial（balance/payment_comparison 范围外），"
        f"实际 {result['status']!r}"
    )
    assert result["amounts"]["balance"] is None, result["amounts"]
    assert "payment_comparison" not in result, (
        "跨税项缴款/退款比较属阶段 3，不得出现 payment_comparison 键"
    )
    # steps 须含 tagged 形状（scenario/selection/next_year）。
    kinds = {s.get("kind") for s in (result.get("steps") or []) if isinstance(s, dict)}
    assert {"scenario", "selection"} <= kinds, (
        f"steps 必须含 scenario/selection tagged 条目（005 §14），实际：{kinds!r}"
    )


def test_002_salary_balance_null_when_paid_or_partial() -> None:
    """salary：partial（next 缺）→ balance null；complete（next 可算＋paid 已知）
    → balance=final−paid。

    规格锚点：Annex 005 §10（salary 列）。
    T3：partial 变体 final 940（PAM39 Q1）；complete 变体提供 2026_27 Year
    （240k/9k → next：NCI=86,000 → 1,000+2,160=3,160、宽减 0）→
    balance=940−500=440。
    """
    year, target = "2025_26", "2026_27"
    partial = _run(_input(year, _subjects_single(), provisional_paid="0"))
    assert partial["status"] == "partial", partial["status"]
    assert partial["amounts"]["balance"] is None, partial["amounts"]

    complete = _run(_input(
        year,
        _subjects_single(),
        years={year: _base_year(year, provisional_paid="500"),
               target: _base_year(target)},
    ))
    assert complete["status"] == "complete", (
        f"本年 final＋next＋paid 全知 → complete，实际 {complete['status']!r}"
    )
    amounts = complete["amounts"]
    assert amounts["final_after_reduction"] == "940", amounts
    assert amounts["next_provisional"] == "3160", amounts
    assert amounts["balance"] == "440", (
        f"complete 且 paid 已知 → balance=940−500=440，实际：{amounts!r}"
    )


def test_002_best_independent_of_paid_unknown_competitor_blocks() -> None:
    """best 仅依候选本年 final＋资格：缺 paid 不阻 best；unknown/unresolved
    竞争者 → best=null（且 selection=best 时 top-level final=null）。

    规格锚点：Annex 005 §10（best 行）＋§9.3。
    T3：单身 240k/9k → no_pa 与 pa_self 均 940 → 平局优先 PA → best=pa_self
    （缺 paid 不影响）；变体：hypothetical 缺 reference_date → pa_self
    needs_input → best=null。
    """
    year = "2025_26"
    # (a) 缺 provisional_paid（paid 组件缺失）→ best 仍确定（独立于 paid）。
    no_paid = _run_pa(_input(
        year,
        _subjects_single(),
        provisional_paid=None,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    assert no_paid.get("best_scenario") == "pa_self", (
        f"best 独立于 paid（缺 paid 不得令 best=null），实际："
        f"{no_paid.get('best_scenario')!r}"
    )
    assert no_paid["status"] == "partial", no_paid["status"]

    # (b) pa_self 因缺 reference_date 未决 → unknown 竞争者阻 best。
    unknown_competitor = _run_pa(_input(
        year,
        _subjects_single(),
        provisional_paid="0",
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        assessment={"basis": "hypothetical",  # 缺 reference_date → pa_self needs_input
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    ))
    assert unknown_competitor.get("best_scenario") is None, (
        f"unknown/unresolved 竞争者存在 → best=null，实际："
        f"{unknown_competitor.get('best_scenario')!r}"
    )
    assert unknown_competitor["amounts"]["final_after_reduction"] is None, (
        f"selection=best 不能确定时 top-level final=null（已知 scenario 保留在 "
        f"steps），实际：{unknown_competitor['amounts']!r}"
    )
    scenarios = [s.get("scenario_id") for s in _steps(unknown_competitor, "scenario")]
    assert "no_pa" in scenarios, (
        f"已知 no_pa scenario 必须保留（不被未知竞争者清空），实际：{scenarios!r}"
    )


def test_002_explicit_selected_known_retains_final() -> None:
    """显式选中已知合法候选 → 该候选 final 保留（amounts.final=940），
    即使另一候选未决使 best=null。

    规格锚点：Annex 005 §10（top-level amounts 行）＋§9.3。
    """
    year = "2025_26"
    result = _run_pa(_input(
        year,
        _subjects_single(),
        provisional_paid="0",
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        assessment={"basis": "hypothetical",  # 缺 reference_date → pa_self 未决
                    "candidates": ["no_pa", "pa_self"], "selection": "no_pa"},
    ))
    assert result["amounts"]["final_after_reduction"] == "940", (
        f"显式选中已知候选 no_pa → 其 final 保留（PAM39 Q1＝940），实际："
        f"{result['amounts']!r}"
    )
    assert result.get("best_scenario") is None, (
        f"未决竞争者存在 → best 仍为 null，实际：{result.get('best_scenario')!r}"
    )


def test_002_blocked_all_six_null_c3() -> None:
    """blocked：六个金额键一律显式 null、键不省略＋blocked_reason。

    规格锚点：C3.3/C3.4（S002 §8 REQ-10）＋Annex 005 §2（本年 blocked →
    不产确定税额）。
    构造：availability current.status="blocked"（guard 阻断本年规则）。
    """
    year = "2025_26"
    bundle = _v2_bundle()
    ctx = _availability(bundle, year)
    ctx["current"] = {"year": year, "status": "blocked", "reason_codes": ["GUARD_BLOCKED"]}
    result = _run(_input(year, _subjects_single()), availability=ctx)
    assert result["status"] == "blocked", (
        f"本年 guard blocked → 不产确定税额，实际 {result['status']!r}"
    )
    _assert_six_keys_all_null(result)
    assert result.get("blocked_reason"), "blocked 必须附 blocked_reason（C3.3）"


def _pa_selected_pself_two_year_input() -> dict:
    """W-D3-A/AQA-08；D4-A 共用构造器：PA 本年同税额 **best**（no_pa/pa_self
    均 940 → §9.3 平局优先 pa_self，处方:73）＋paid 2,000＋next 显式 no_pa
    双年合法事实（2025/26→2026/27；next 不混入 salary 候选）。"""
    year, target = "2025_26", "2026_27"
    return _input(
        year,
        _subjects_single(),
        years={
            year: _base_year(
                year, provisional_paid="2000",
                subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
                assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                            "candidates": ["no_pa", "pa_self"], "selection": "best"},
            ),
            target: _base_year(
                target,
                assessment={"basis": "hypothetical", "candidates": ["no_pa"],
                            "selection": "no_pa"},
            ),
        },
    )


def test_002_negative_salary_balance_full_context_refund() -> None:
    """全组件已知＋paid>final → 负 balance＝估算退税（唯一有符号例外）；
    PA balance 仍 null。负号不外溢检查（NAI 负 → 拒绝）已拆至独立节点
    `test_002_negative_nai_rejected_with_cf1_path`，防被本节点后段遮蔽。

    规格锚点：V006 §5.4（REQ-10 有符号余额）＋Annex 005 §3（Money 唯一有符号
    例外＝core amounts.balance）＋§10。
    T3：complete（final 940、next 3,160）＋paid 2,000 → balance=940−2,000
    =−1,060（负＝估算退税）。
    """
    year, target = "2025_26", "2026_27"

    result = _run(_input(
        year,
        _subjects_single(),
        years={year: _base_year(year, provisional_paid="2000"),
               target: _base_year(target)},
    ))
    assert result["status"] == "complete", result["status"]
    amounts = result["amounts"]
    assert amounts["final_after_reduction"] == "940", amounts
    assert amounts["balance"] == "-1060", (
        f"paid 2,000>final 940 → 负 balance＝估算退税（−1,060），实际：{amounts!r}"
    )

    # PA 同量事实：balance 仍恒 null（跨税项比较属阶段 3）。
    # P1 r2 定点勘误（fixture-audit #10）＋W-D3-A/AQA-08＋D4-A：本年同税额
    # best（no_pa/pa_self 均 940 → §9.3 平局优先 pa_self，处方:73）＋paid
    # 2,000＋next 显式 no_pa；六金额键＝3,940/3,000/940/2,000/3,160/null；
    # selected pa_self scenario 六键须与 top-level 一致（next 同步、不得恒
    # null）。next_year tagged step 的独立核对见紧随节点。
    pa = _run_pa(_pa_selected_pself_two_year_input())
    assert pa["status"] == "partial", pa["status"]
    selection_steps = _steps(pa, "selection")
    assert selection_steps, f"须含 selection tagged step，实际：{pa.get('steps')!r}"
    selection = selection_steps[0]
    best_observed = {
        "best_scenario": pa.get("best_scenario"),
        "selected": selection.get("selected"),
        "best": selection.get("best"),
    }
    assert best_observed == {
        "best_scenario": "pa_self",
        "selected": "pa_self",
        "best": "pa_self",
    }, (
        f"本年同税额 best 须按 005 §9.3 平局择案优先级选 pa_self（处方:73）："
        f"{best_observed!r}"
    )
    expected_six = {
        "before_reduction": "3940",
        "reduction": "3000",
        "final_after_reduction": "940",
        "provisional_paid": "2000",
        "next_provisional": "3160",
        "balance": None,
    }
    assert set(pa["amounts"].keys()) == set(_AMOUNT_KEYS), sorted(pa["amounts"])
    assert pa["amounts"] == expected_six, (
        f"selected pa_self＋paid 2,000：六金额键须为 "
        f"3,940/3,000/940/2,000/3,160/null，实际：{pa['amounts']!r}"
    )
    scenario = next(
        s for s in _steps(pa, "scenario") if s.get("scenario_id") == "pa_self"
    )
    assert scenario["amounts"] == dict(pa["amounts"]), (
        f"selected pa_self scenario 六金额键须与 top-level 一致（next 同步、"
        f"不得恒 null），实际：{scenario['amounts']!r} vs {pa['amounts']!r}"
    )


def test_002_pa_selected_next_year_step_amount_independent() -> None:
    """W-D3-A/AQA-08；D4-A（拆分-next_year 独立节点＋year 核对）：本年
    同税额 best→pa_self＋paid 2,000＋next no_pa 的 next_year tagged step
    独立核对：year=2026_27（处方:74）、status=available、selected=no_pa、
    amount=3,160（与 top-level next_provisional 一致）；不依赖 selected
    scenario 六键断言先行通过。"""
    pa = _run_pa(_pa_selected_pself_two_year_input())
    next_steps = _steps(pa, "next_year")
    assert next_steps, f"须含 next_year tagged step，实际：{pa.get('steps')!r}"
    detail = next_steps[0]
    assert detail.get("year") == "2026_27", (
        f"next_year.year 须为下一年度（2026_27，处方:74：year/selected/amount "
        f"依已提交 next），实际：{detail!r}"
    )
    assert detail.get("status") == "available", detail
    assert detail.get("selected") == "no_pa", detail
    assert detail.get("amount") == "3160", (
        f"next_year.amount 须为 3,160（与 top-level next_provisional 一致），"
        f"实际：{detail!r}"
    )


def test_002_negative_nai_rejected_with_cf1_path() -> None:
    """A 质量复核 #8（拆分为独立节点，防被 PA scenario 首失败遮蔽）：负
    balance 例外不得外溢到输入——NAI 负值按输入域错误拒绝，精确
    `E_INPUT_TYPE`＋CF1 完整 input 内容根路径（005 §3/§13；旧断言接受
    三种宽码，无 field 定位）。"""
    year, target = "2025_26", "2026_27"
    from app.core.errors import AppError  # 延迟导入：Red=缺 v2 契约

    try:
        _run(_input(
            year,
            _subjects_single(),
            years={year: _base_year(year, provisional_paid="2000",
                                    financials={"p1": _fin(nai="-100")}),
                   target: _base_year(target)},
        ))
    except AppError as exc:
        assert exc.code == "E_INPUT_TYPE", (
            f"负 NAI 属输入域错误，须精确 E_INPUT_TYPE（balance 例外不外溢），"
            f"实际 {exc.code!r}"
        )
        assert exc.field == (
            f"years.{year}.financials.p1.employment_income_nai"
        ), exc.field
    else:
        raise AssertionError(
            "NAI 不在有符号例外内（005 §3：唯一例外＝core amounts.balance），必须拒绝"
        )


# ---------------------------------------------------------------------------
# P1 r1 新 Red（复核 §六.4/§六.9）：已知 selected 保护与完整 trace
# ---------------------------------------------------------------------------


def test_002_known_selected_protected_from_unknown_competitor() -> None:
    """P1 r1 新 Red（复核 §六.4/R7.1）：居港 unknown 的竞争候选（pa_self）
    不得清空已知 selected——selection=no_pa 时 final 940 保留、best=null、
    status=partial；selection=best 时 top-level final=null 且已知 scenario
    保留在 steps。

    规格锚点：Annex 005 §9.3（unknown 竞争者→best=null；显式选中已知候选
    可保留 final）＋§10（top-level amounts 行）。复核 §四.1（观察：已知
    no_pa 被竞争 PA unknown 清为 null——问题与结果状态混同）。
    """
    year = "2025_26"
    base_year_kwargs = dict(
        subject_facts={},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "no_pa"},
    )
    explicit = _run_pa(_input(year, _subjects_single(), **base_year_kwargs))
    assert explicit["status"] == "partial", (
        f"已知 selected（no_pa）不被竞争 unknown 清为 needs_input，"
        f"实际 {explicit['status']!r}"
    )
    assert explicit["amounts"]["final_after_reduction"] == "940", (
        f"显式选中已知候选 → 其 final 保留（940），实际：{explicit['amounts']!r}"
    )
    assert explicit.get("best_scenario") is None, (
        f"未决竞争者存在 → best=null，实际 {explicit.get('best_scenario')!r}"
    )
    scenario_ids = [s.get("scenario_id") for s in _steps(explicit, "scenario")]
    assert "no_pa" in scenario_ids, (
        f"已知 no_pa scenario 必须保留，实际：{scenario_ids!r}"
    )

    best_mode = _run_pa(_input(year, _subjects_single(), **dict(
        base_year_kwargs,
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": ["no_pa", "pa_self"], "selection": "best"},
    )))
    assert best_mode.get("best_scenario") is None, best_mode.get("best_scenario")
    assert best_mode["amounts"]["final_after_reduction"] is None, (
        f"selection=best 不能确定 → top-level final=null（已知 scenario 保留），"
        f"实际：{best_mode['amounts']!r}"
    )
    scenario_ids = [s.get("scenario_id") for s in _steps(best_mode, "scenario")]
    assert "no_pa" in scenario_ids, scenario_ids


def test_002_basic_married_and_deduction_decisions_in_steps() -> None:
    """P1 r1 新 Red（复核 §六.9/R7.3）：basic/married 基础额与 deduction
    decision 须入 tagged steps（§14 decision 形状）。

    规格锚点：Annex 005 §14（decision：item_kind/entitlement/grant/amount）
    ＋§14 示例（「基本免税额132000港元」decision 条目）。复核 §四.3
    （basic/married 与 deduction decision 未入 steps）。
    """
    year = "2025_26"
    # (a) 单人：basic decision（allowed 132,000）。
    single = _run(_input(year, _subjects_single()))
    basic = [d for d in _decisions(single) if d.get("item_kind") == "basic"]
    assert basic and basic[0].get("grant") == "allowed", (
        f"steps 须含 basic 基础额 decision（005 §14 示例），实际："
        f"{_decisions(single)!r}"
    )
    assert basic[0].get("amount") == "132000", basic[0]

    # (b) 分支 A 夫妻：married decision（allowed 264,000）。
    w_from, w_to = _window(year)
    couple = _run(_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "p2", "birth_date": "1992-02-02"}],
        household={
            "marriage": [{"from": w_from, "to": w_to, "value": "married",
                          "spouse_id": "p2"}],
            "living_apart": _tf(year, False),
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=[{"person_id": "p1", "role": "self"},
                      {"person_id": "p2", "role": "spouse"}],
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p2": {"mode": "none"}}}},
    ))
    married = [d for d in _decisions(couple) if d.get("item_kind") == "married"]
    assert married and married[0].get("grant") == "allowed", (
        f"分支 A 下 steps 须含 married decision，实际：{_decisions(couple)!r}"
    )
    assert married[0].get("amount") == "264000", married[0]

    # (c) ERCE 扣除 decision（两笔付款聚合、cap 100,000 生效）。
    erce = _run(_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": "1950-05-01"}],
        subject_facts={"d1": {"ordinarily_resident_hk": _tf(year, True),
                              "govt_disability_allowance": _tf(year, True)}},
        relations=[{
            "relation_id": "r1", "actor_id": "p1", "subject_id": "d1",
            "kinship": {"kind": "parent", "basis": "birth",
                        "during": _tf(year, True)},
            "cash_maintenance": "15000",
        }],
        care_payments=[
            {"payment_id": "e1", "payer_id": "p1", "subject_id": "d1",
             "paid_on": "2025-06-15", "amount": "60000",
             "care_from": w_from, "care_to": w_to,
             "residential_care_received": True, "paid_to_home_or_agent": True,
             "home_basis": "cap459_licence",
             "home_status_in_force": [{"from": w_from, "to": w_to, "value": True}]},
            {"payment_id": "e2", "payer_id": "p1", "subject_id": "d1",
             "paid_on": "2025-09-15", "amount": "60000",
             "care_from": w_from, "care_to": w_to,
             "residential_care_received": True, "paid_to_home_or_agent": True,
             "home_basis": "cap459_licence",
             "home_status_in_force": [{"from": w_from, "to": w_to, "value": True}]},
        ],
        financials={"p1": _fin(deduction_items=[
            {"item_type": "elder_residential_care", "amount": "120000",
             "payment_ids": ["e1", "e2"], "eligibility": {"value": True},
             "subject_id": "d1"},
        ])},
    ))
    erce_decisions = [
        d for d in _decisions(erce)
        if json.dumps(d, ensure_ascii=False, default=str).find(
            "elder_residential_care") >= 0
    ]
    assert erce_decisions, (
        f"ERCE 扣除须有 decision 条目（§5.1 deduction decision），实际："
        f"{_decisions(erce)!r}"
    )
    assert any(d.get("amount") == "100000" for d in erce_decisions), (
        f"扣除 decision 金额＝聚合后 cap 100,000（非 120,000），实际："
        f"{erce_decisions!r}"
    )


def test_002_scenario_step_components_complete_when_result_complete() -> None:
    """P1 r1 新 Red（复核 §六.9/R7.3）：结果 complete 时 scenario step 不得
    恒 partial——六键组件完整（next/balance 非 null）。

    规格锚点：Annex 005 §14（scenario.amounts 六键）＋§10。复核 §四.3
    （「scenario 恒 partial、next/balance 恒 null」）。
    T3（迁移测试 #3 同型）：final 940、next 3,160、balance 440。
    """
    year, target = "2025_26", "2026_27"
    result = _run(_input(
        year,
        _subjects_single(),
        years={year: _base_year(year, provisional_paid="500"),
               target: _base_year(target)},
    ))
    assert result["status"] == "complete", result["status"]
    scenario = next(
        s for s in _steps(result, "scenario")
        if s.get("scenario_id") == "salary_separate"
    )
    amounts = scenario["amounts"]
    assert set(amounts.keys()) == set(_AMOUNT_KEYS), sorted(amounts)
    assert amounts["next_provisional"] == "3160", (
        f"complete 时 scenario.next_provisional 须为实值（3,160），实际：{amounts!r}"
    )
    assert amounts["balance"] == "440", (
        f"complete 时 scenario.balance 须为实值（440），实际：{amounts!r}"
    )


def test_002_floor_operations_record_true_fraction_inputs() -> None:
    """P1 r1 新 Red（复核 §六.9/R7.3）：floor 运算记录 floor 前真值
    （Exact 分数，非取整后回填）。

    规格锚点：Annex 005 §14（Op inputs 按「实际执行次序」的分支运算真值；
    Exact 始终约分字符串）。复核 §四.3（「no_pa CompTP 未存 floor 前输入」）。
    T3：240,008.50/9,000 → NCI=99,008.50 → 累进 raw=3,940.51＝394051/100
    → floor 输入须为 "394051/100"（非 "3940/1"）。
    """
    year = "2025_26"
    result = _run(_input(year, _subjects_single(),
                         financials={"p1": _fin(nai="240008.50", mpf="9000")}))
    ops = []
    for detail in _steps(result, "assessment"):
        if detail.get("unit_id") == "salary:p1":
            ops = [op for op in detail.get("operations") or []
                   if isinstance(op, dict) and op.get("op") == "floor"]
    assert ops, "salary 单元须有 floor 运算记录"
    assert any(op.get("inputs") == ["394051/100"] for op in ops), (
        f"floor 输入须保留 floor 前真值 394051/100，实际：{ops!r}"
    )


def test_002_steps_deterministic_order() -> None:
    """P1 r1 新 Red（复核 §六.9）：同一输入两次运行的 steps 完全一致
    （确定性排序；§14「同一输入数组规范化后同一顺序」）。"""
    year = "2025_26"
    facts = _input(year, _subjects_single())
    first = _run(facts)
    second = _run(facts)
    assert json.dumps(first.get("steps")) == json.dumps(second.get("steps")), (
        "steps 必须确定性（同输入同输出，可哈希）"
    )

# ---------------------------------------------------------------------------
# F6/R7（Wave C，2026-10-17）：薪俸候选固定顺序（separate → joint）
# ---------------------------------------------------------------------------


def _f6_subjects() -> list[dict]:
    return [
        {"subject_id": "p1", "birth_date": "1990-01-01"},
        {"subject_id": "p2", "birth_date": "1992-02-02"},
    ]


def _f6_participants() -> list[dict]:
    return [
        {"person_id": "p1", "role": "self"},
        {"person_id": "p2", "role": "spouse"},
    ]


def _f6_household(year: str) -> dict:
    w_from, w_to = _window(year)
    return {
        "marriage": [
            {"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}
        ],
        "living_apart": _tf(year, False),
        "child_nomination": {"person_id": None, "status": "none"},
    }


def _f6_t3_input(candidates: list[str], *, dda: bool = True) -> dict:
    """005 §9.1.3 T3：500k/200k、MPF 18k、p2 DAA、p1 申索 spouse DDA。"""
    year = "2025_26"
    claims = (
        [
            {
                "claim_id": "c1",
                "kind": "disabled_dependant",
                "claimant_id": "p1",
                "subject_id": "p2",
            }
        ]
        if dda
        else []
    )
    subject_facts = (
        {"p2": {"govt_disability_allowance": _tf(year, True)}} if dda else {}
    )
    return _input(
        year,
        _f6_subjects(),
        subject_facts=subject_facts,
        household=_f6_household(year),
        participants=_f6_participants(),
        financials={
            "p1": _fin(nai="500000", mpf="18000", ai=True),
            "p2": _fin(nai="200000", mpf="18000", ai=True),
        },
        claims=claims,
        assessment={
            "basis": "hypothetical",
            "candidates": list(candidates),
            "selection": "best",
            "hypothetical_salary_joint_nomination": {
                "status": "named",
                "person_id": "p1",
            },
        },
    )


def _f6_selection(result: Mapping) -> dict:
    selections = _steps(result, "selection")
    assert len(selections) == 1, f"期望恰一个 selection step，实际 {len(selections)}"
    return selections[0]


def _f6_projection(result: Mapping) -> list[dict]:
    """去除允许随请求变化的 selection.requested，其余完整 trace 保序投影。"""
    rows = []
    for step in result.get("steps") or []:
        detail = dict(step.get("detail") or {})
        detail.pop("requested", None)
        rows.append(
            {
                "step": step.get("step"),
                "kind": step.get("kind"),
                "label": step.get("label"),
                "detail": detail,
            }
        )
    return rows


def test_002_f6_salary_candidates_fixed_order_ignores_request_order() -> None:
    """F6/R7：同一合法 T3 比较，仅反转请求 candidates——评估与输出仍按固定
    顺序 salary_separate:p1 → salary_separate:p2 → salary_joint；decision/
    assessment/scenario/candidate_checks 顺序一致，运算数组保持真实执行顺序；
    除 selection.requested 外完整 trace 投影逐字段相等。

    规格锚点：Approved 005:738（薪俸候选固定顺序）；S002 §5.3/005 §9.1.3
    T3：separate 38,500；joint（计 DDA）34,250 → joint 接纳为 best。
    """
    original = _run(_f6_t3_input(["salary_separate", "salary_joint"]))
    reversed_ = _run(_f6_t3_input(["salary_joint", "salary_separate"]))
    expected_decision_order = [
        ("salary_separate", "basic", "p1", "132000"),
        ("salary_separate", "basic", "p2", "132000"),
        ("salary_separate", "disabled_dependant", "p1", None),
        ("salary_joint", "married", "p1", "264000"),
        ("salary_joint", "disabled_dependant", "p1", "75000"),
    ]
    for label, result, requested in (
        ("原始请求", original, ["salary_separate", "salary_joint"]),
        ("反转请求", reversed_, ["salary_joint", "salary_separate"]),
    ):
        assert result["status"] == "partial", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] == "34250", (
            label,
            result["amounts"],
        )
        assert result["best_scenario"] == "salary_joint", (label, result["best_scenario"])
        selection = _f6_selection(result)
        assert selection.get("best") == "salary_joint", (label, selection)
        assert selection.get("selected") == "salary_joint", (label, selection)
        assert selection.get("requested") == requested, (
            f"{label}：selection.requested 保留请求顺序（允许变化项），"
            f"实际：{selection.get('requested')!r}"
        )
        assert [
            check.get("candidate_id")
            for check in selection.get("candidate_checks") or []
        ] == ["salary_separate", "salary_joint"], (
            f"{label}：candidate_checks 须按固定候选顺序，实际："
            f"{selection.get('candidate_checks')!r}"
        )
        assert [
            detail.get("unit_id") for detail in _steps(result, "assessment")
        ] == ["salary:p1", "salary:p2", "salary:joint"], (
            f"{label}：assessment 单位顺序须 separate:p1 → separate:p2 → joint，"
            f"实际：{[d.get('unit_id') for d in _steps(result, 'assessment')]!r}"
        )
        assert [
            detail.get("scenario_id") for detail in _steps(result, "scenario")
        ] == ["salary_separate", "salary_joint"], (
            f"{label}：scenario 块顺序须 separate → joint，实际："
            f"{[d.get('scenario_id') for d in _steps(result, 'scenario')]!r}"
        )
        assert [
            (d.get("scenario_id"), d.get("item_kind"), d.get("claimant_id"),
             d.get("amount"))
            for d in _decisions(result)
        ] == expected_decision_order, (
            f"{label}：decision 内部顺序/金额须与固定候选顺序一致，实际："
            f"{[(d.get('scenario_id'), d.get('item_kind'), d.get('claimant_id'), d.get('amount')) for d in _decisions(result)]!r}"
        )
        joint = next(
            check for check in selection["candidate_checks"]
            if check.get("candidate_id") == "salary_joint"
        )
        assert joint.get("conditional_final") == "34250", (label, joint)
    assert _f6_projection(original) == _f6_projection(reversed_), (
        "去除 selection.requested 后，原始/反转请求的完整 trace 投影（含金额/"
        "bases/operations/grant/label，不排序）必须逐字段相等"
    )


def test_002_f6_reversed_request_unaccepted_joint_conditional_only_checks() -> None:
    """F6/R7 边界：反转候选请求后，未接纳 joint 的 conditional 金额仍只进
    非权威 candidate_checks，不进 scenario/top-level/decision；顺序不变。

    规格锚点：Annex 005 §9.1.2 末段＋§14（candidate_checks 行）；
    005:738（固定候选顺序）。
    T3（无 DDA）：conditional joint 47,000＞separate 38,500 → ineligible。
    """
    result = _run(_f6_t3_input(["salary_joint", "salary_separate"], dda=False))
    assert result["status"] == "partial", result["status"]
    assert result["amounts"]["final_after_reduction"] == "38500", result["amounts"]
    assert result["best_scenario"] == "salary_separate", result["best_scenario"]
    selection = _f6_selection(result)
    assert [
        check.get("candidate_id") for check in selection.get("candidate_checks") or []
    ] == ["salary_separate", "salary_joint"], selection
    joint = next(
        check for check in selection["candidate_checks"]
        if check.get("candidate_id") == "salary_joint"
    )
    assert joint.get("admission") == "ineligible", joint
    assert joint.get("conditional_final") == "47000", joint
    assert [
        detail.get("scenario_id") for detail in _steps(result, "scenario")
    ] == ["salary_separate"], (
        f"未接纳 joint 不得留下 scenario 记录，实际："
        f"{[d.get('scenario_id') for d in _steps(result, 'scenario')]!r}"
    )
    blob = json.dumps(
        [_steps(result, "scenario"), result.get("amounts"), _decisions(result)],
        ensure_ascii=False,
        default=str,
    )
    assert "47000" not in blob, (
        f"conditional 47,000 不得进入 scenario/top-level/decision，实际：{blob!r}"
    )
    assert "47000" in json.dumps(selection, ensure_ascii=False, default=str), (
        "未接纳 joint 的 conditional 金额须可在非权威 candidate_checks 中检查"
    )


# ---------------------------------------------------------------------------
# P1 final fix（2026-10-17）：完整 trace 按候选连续分块（005:738）
# ---------------------------------------------------------------------------


def _f6_block_signature(result: Mapping) -> list[tuple]:
    """年度 context 之后按实际 steps 顺序记录（kind, 候选, 单位/项目）。"""
    rows: list[tuple] = []
    for step in result.get("steps") or []:
        kind = step.get("kind")
        detail = step.get("detail") or {}
        rows.append(
            (
                kind,
                detail.get("scenario_id"),
                detail.get("unit_id") or detail.get("item_kind"),
            )
        )
    return rows


def test_002_f6_trace_candidate_blocks_contiguous() -> None:
    """P1 final fix：完整 trace 必须按「年度→固定候选→单位」连续成块——
    separate 的 decision/assessment/scenario 全部输出后，才进入 joint 的
    decision/assessment/scenario；候选内单位保持固定序（p1→p2→joint）。

    规格锚点：Approved 005:738（排序为年度→候选固定顺序→单位 ID→decision
    类别/claimID/subjectID）；不改择优/平局/operations 顺序/金额。
    T3：separate 38,500；joint（计 DDA）34,250 → joint 接纳为 best。
    """
    original = _run(_f6_t3_input(["salary_separate", "salary_joint"]))
    reversed_ = _run(_f6_t3_input(["salary_joint", "salary_separate"]))
    expected = [
        ("context", None, None),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "disabled_dependant"),
        ("assessment", "salary_separate", "salary:p1"),
        ("assessment", "salary_separate", "salary:p2"),
        ("scenario", "salary_separate", None),
        ("decision", "salary_joint", "married"),
        ("decision", "salary_joint", "disabled_dependant"),
        ("assessment", "salary_joint", "salary:joint"),
        ("scenario", "salary_joint", None),
        ("selection", None, None),
        ("next_year", None, None),
    ]
    for label, result in (("原始请求", original), ("反转请求", reversed_)):
        assert result["status"] == "partial", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] == "34250", (
            label,
            result["amounts"],
        )
        assert result["best_scenario"] == "salary_joint", (
            label,
            result["best_scenario"],
        )
        assert _f6_block_signature(result) == expected, (
            f"{label}：完整 trace 须按候选连续成块（separate 全部→joint 全部），"
            f"实际：{_f6_block_signature(result)!r}"
        )
        # 候选内/跨候选 assessment 单位固定序；operations 数组保持真实执行顺序。
        assessments = _steps(result, "assessment")
        assert [item.get("unit_id") for item in assessments] == [
            "salary:p1",
            "salary:p2",
            "salary:joint",
        ], (label, [item.get("unit_id") for item in assessments])
        assert all(item.get("operations") for item in assessments), (
            f"{label}：assessment 必须保留 operations 实际执行顺序，实际："
            f"{assessments!r}"
        )
        first_ops = [
            (op.get("op"), op.get("inputs"), op.get("output"))
            for op in assessments[0].get("operations") or []
        ]
        assert first_ops[0] == ("progressive_tax", ["350000/1"], "41500/1"), (
            f"{label}：salary:p1 首项运算须保持实际执行顺序，实际：{first_ops!r}"
        )
    assert _f6_projection(original) == _f6_projection(reversed_), (
        "去除 selection.requested 后，原始/反转请求的完整 trace 投影（含分块"
        "顺序、金额、bases/operations）须逐字段相等"
    )


# ---------------------------------------------------------------------------
# P1 F6 点修（2026-10-17）：单位序与 participants 数组序无关（005:738）
# ---------------------------------------------------------------------------


def _f6_t3_input_participants_reversed(
    candidates: list[str] | None = None,
) -> dict:
    """同一 T3 输入仅反转 participants 数组（人物/角色/金额/候选均不变）。"""
    v2 = _f6_t3_input(list(candidates or ["salary_separate", "salary_joint"]))
    year = v2["years"]["2025_26"]
    year["participants"] = list(reversed(year["participants"]))
    return v2


def test_002_f6_participants_reversed_units_and_trace_fixed() -> None:
    """P1 F6 点修：仅反转 participants 数组，薪俸单位生成与 trace 单位块仍按
    合同固定序 claimant(self)=p1 → spouse=p2 → joint；金额/择优不变。

    规格锚点：Approved 005:738（年度→固定候选→单位）；005 §9.1.3 T3。
    修复前复现：单位序 p2→p1→joint、scenario.unit_ids 同反转、完整 trace
    投影不等（金额均 34,250，非金额回归）。
    """
    original = _run(_f6_t3_input(["salary_separate", "salary_joint"]))
    reversed_participants = _run(_f6_t3_input_participants_reversed())
    expected_units = ["salary:p1", "salary:p2", "salary:joint"]
    for label, result in (
        ("原始 participants", original),
        ("反转 participants", reversed_participants),
    ):
        assert result["status"] == "partial", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] == "34250", (
            label,
            result["amounts"],
        )
        assert result["best_scenario"] == "salary_joint", (
            label,
            result["best_scenario"],
        )
        assessments = _steps(result, "assessment")
        assert [detail.get("unit_id") for detail in assessments] == expected_units, (
            f"{label}：单位生成须按固定序 p1→p2→joint，实际："
            f"{[detail.get('unit_id') for detail in assessments]!r}"
        )
        scenarios = {
            detail.get("scenario_id"): detail
            for detail in _steps(result, "scenario")
        }
        assert scenarios["salary_separate"].get("unit_ids") == [
            "salary:p1",
            "salary:p2",
        ], (label, scenarios["salary_separate"].get("unit_ids"))
        assert scenarios["salary_joint"].get("unit_ids") == [
            "salary:joint"
        ], (label, scenarios["salary_joint"].get("unit_ids"))
        assert [
            (
                detail.get("scenario_id"),
                detail.get("item_kind"),
                detail.get("claimant_id"),
                detail.get("amount"),
            )
            for detail in _decisions(result)
        ] == [
            ("salary_separate", "basic", "p1", "132000"),
            ("salary_separate", "basic", "p2", "132000"),
            ("salary_separate", "disabled_dependant", "p1", None),
            ("salary_joint", "married", "p1", "264000"),
            ("salary_joint", "disabled_dependant", "p1", "75000"),
        ], (label, _decisions(result))
    assert _f6_projection(original) == _f6_projection(reversed_participants), (
        "仅反转 participants 数组时，除 selection.requested 外完整 trace 投影"
        "（含单位序、金额、bases/operations）须逐字段相等"
    )


def test_002_f6_participant_order_control_positive_and_candidates_reversed() -> None:
    """P1 F6 点修控制：正序 participants 结果不变；仅反转 candidates 时投影
    仍与正序一致（不得以修正 participants 序为由改变择优/平局/金额）。"""
    original = _run(_f6_t3_input(["salary_separate", "salary_joint"]))
    candidates_reversed = _run(_f6_t3_input(["salary_joint", "salary_separate"]))
    assert original["status"] == "partial", original["status"]
    assert original["amounts"]["final_after_reduction"] == "34250", original["amounts"]
    assert original["best_scenario"] == "salary_joint", original["best_scenario"]
    assert [
        detail.get("unit_id") for detail in _steps(original, "assessment")
    ] == ["salary:p1", "salary:p2", "salary:joint"], _steps(original, "assessment")
    assert [
        (
            detail.get("scenario_id"),
            detail.get("item_kind"),
            detail.get("claimant_id"),
            detail.get("amount"),
        )
        for detail in _decisions(original)
    ] == [
        ("salary_separate", "basic", "p1", "132000"),
        ("salary_separate", "basic", "p2", "132000"),
        ("salary_separate", "disabled_dependant", "p1", None),
        ("salary_joint", "married", "p1", "264000"),
        ("salary_joint", "disabled_dependant", "p1", "75000"),
    ], _decisions(original)
    assert _f6_block_signature(original) == [
        ("context", None, None),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "disabled_dependant"),
        ("assessment", "salary_separate", "salary:p1"),
        ("assessment", "salary_separate", "salary:p2"),
        ("scenario", "salary_separate", None),
        ("decision", "salary_joint", "married"),
        ("decision", "salary_joint", "disabled_dependant"),
        ("assessment", "salary_joint", "salary:joint"),
        ("scenario", "salary_joint", None),
        ("selection", None, None),
        ("next_year", None, None),
    ], _f6_block_signature(original)
    assert _f6_projection(original) == _f6_projection(candidates_reversed), (
        "仅反转 candidates 时投影须仍一致（既有 F6 行为不得回退）"
    )


# ---------------------------------------------------------------------------
# P1 F6 单位 ID 序点修（2026-10-17）：005:738「单位 ID」而非角色序
# ---------------------------------------------------------------------------


def _f6_rename_person(value: Any, old: str = "p1", new: str = "p9") -> Any:
    """深拷贝式改名：仅把等于 old 的字符串值与对象键替换为 new。"""
    if isinstance(value, dict):
        return {
            (new if key == old else key): _f6_rename_person(item, old, new)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_f6_rename_person(item, old, new) for item in value]
    if value == old:
        return new
    return value


def _f6_t3_input_self_renamed_p9() -> dict:
    """同一 T3 仅把 self 的 pID 由 p1 改名 p9（p2/spouse 与全部事实不变）。"""
    return _f6_rename_person(_f6_t3_input(["salary_separate", "salary_joint"]))


def _f6_unit_structure(result: Mapping) -> dict[str, dict]:
    """单位结构投影（bases/operations/amounts/shares；p9 归一到 p1）。"""
    out: dict[str, dict] = {}
    for detail in _steps(result, "assessment"):
        person = str(detail.get("unit_id", "")).split(":", 1)[-1]
        key = "p1" if person == "p9" else person
        out[key] = {
            "branch": detail.get("branch"),
            "bases": detail.get("bases"),
            "operations": detail.get("operations"),
            "amounts": detail.get("amounts"),
            "shares": detail.get("shares"),
        }
    return out


def _f6_steps_contiguous(result: Mapping) -> bool:
    numbers = [step.get("step") for step in result.get("steps") or []]
    return numbers == list(range(1, len(numbers) + 1))


def test_002_f6_unit_order_by_unit_id_with_self_p9() -> None:
    """F6 单位 ID 序点修：仅把 self 由 p1 改名 p9（p2/spouse/事实不变），
    separate 单位与 scenario.unit_ids 须按合同单位 ID 升序 p2→p9（而非
    self→spouse 角色序 p9→p2）；金额/择优/operations 不变。

    规格锚点：Approved 005:738（排序为年度→候选固定顺序→单位 ID；单位
    ID 形如 salary:<pID>）；005:100/116/134–135 允许任意合法 pID（未规定
    self 的 ID 最小）。
    """
    original = _run(_f6_t3_input(["salary_separate", "salary_joint"]))
    renamed = _run(_f6_t3_input_self_renamed_p9())
    expected_units = ["salary:p2", "salary:p9", "salary:joint"]
    for label, result in (
        ("正序 self=p1", original),
        ("改名 self=p9", renamed),
    ):
        assert result["status"] == "partial", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] == "34250", (
            label,
            result["amounts"],
        )
        assert result["best_scenario"] == "salary_joint", (
            label,
            result["best_scenario"],
        )
    assessments = _steps(renamed, "assessment")
    assert [detail.get("unit_id") for detail in assessments] == expected_units, (
        "单位须按单位 ID（salary:<pID>）升序 p2→p9→joint，实际："
        f"{[detail.get('unit_id') for detail in assessments]!r}"
    )
    separate_units = [
        detail.get("unit_id")
        for detail in assessments
        if detail.get("scenario_id") == "salary_separate"
    ]
    assert separate_units == sorted(separate_units), (
        "候选内单位序须为单位 ID 字符串升序（005:738；joint 由候选固定序在后）"
    )
    scenarios = {
        detail.get("scenario_id"): detail for detail in _steps(renamed, "scenario")
    }
    assert scenarios["salary_separate"].get("unit_ids") == [
        "salary:p2",
        "salary:p9",
    ], scenarios["salary_separate"].get("unit_ids")
    assert scenarios["salary_joint"].get("unit_ids") == ["salary:joint"], (
        scenarios["salary_joint"].get("unit_ids")
    )
    assert [
        (
            detail.get("scenario_id"),
            detail.get("item_kind"),
            detail.get("claimant_id"),
            detail.get("amount"),
        )
        for detail in _decisions(renamed)
    ] == [
        ("salary_separate", "basic", "p2", "132000"),
        ("salary_separate", "basic", "p9", "132000"),
        ("salary_separate", "disabled_dependant", "p9", None),
        ("salary_joint", "married", "p9", "264000"),
        ("salary_joint", "disabled_dependant", "p9", "75000"),
    ], _decisions(renamed)
    # 结构投影：改名不改变任一单位（按人归一后）的 bases/operations/金额。
    assert _f6_unit_structure(renamed) == _f6_unit_structure(original), (
        "改名后单位结构投影（bases/operations/amounts/shares，按人归一）"
        "须与正序一致"
    )
    assert _f6_steps_contiguous(renamed), [
        step.get("step") for step in renamed.get("steps") or []
    ]
    assert [step.get("kind") for step in renamed.get("steps") or []] == [
        step.get("kind") for step in original.get("steps") or []
    ], "改名不得改变 step 类别序列"
    assert [
        (detail.get("scenario_id"), detail.get("amounts"))
        for detail in _steps(renamed, "scenario")
    ] == [
        (detail.get("scenario_id"), detail.get("amounts"))
        for detail in _steps(original, "scenario")
    ], "改名不得改变 scenario 六金额键"
    assert [
        step.get("detail")
        for step in renamed.get("steps") or []
        if step.get("kind") == "next_year"
    ] == [
        step.get("detail")
        for step in original.get("steps") or []
        if step.get("kind") == "next_year"
    ], "下一年度 selection 须继承同一单位 ID 序语义（金额/选择不变）"


def test_002_f6_unit_id_order_controls_positive_participants_candidates_double() -> None:
    """F6 单位 ID 序控制：正序（p1/p2）不变；仅反转 participants、仅反转
    candidates、两者双反转，单位序仍 p1→p2→joint，除 selection.requested
    外完整 trace 投影与正序逐字段相等；金额/择优不变。"""
    original = _run(_f6_t3_input(["salary_separate", "salary_joint"]))
    participants_reversed = _run(_f6_t3_input_participants_reversed())
    candidates_reversed = _run(_f6_t3_input(["salary_joint", "salary_separate"]))
    double_reversed = _run(
        _f6_t3_input_participants_reversed(["salary_joint", "salary_separate"])
    )
    for label, result in (
        ("正序", original),
        ("仅反转 participants", participants_reversed),
        ("仅反转 candidates", candidates_reversed),
        ("双反转", double_reversed),
    ):
        assert result["status"] == "partial", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] == "34250", (
            label,
            result["amounts"],
        )
        assert result["best_scenario"] == "salary_joint", (
            label,
            result["best_scenario"],
        )
        assert [detail.get("unit_id") for detail in _steps(result, "assessment")] == [
            "salary:p1",
            "salary:p2",
            "salary:joint",
        ], (label, [detail.get("unit_id") for detail in _steps(result, "assessment")])
        scenarios = {
            detail.get("scenario_id"): detail
            for detail in _steps(result, "scenario")
        }
        assert scenarios["salary_separate"].get("unit_ids") == [
            "salary:p1",
            "salary:p2",
        ], (label, scenarios["salary_separate"].get("unit_ids"))
    assert _f6_projection(original) == _f6_projection(participants_reversed), (
        "仅反转 participants 时除 requested 外投影须相等"
    )
    assert _f6_projection(original) == _f6_projection(candidates_reversed), (
        "仅反转 candidates 时除 requested 外投影须相等"
    )
    assert _f6_projection(original) == _f6_projection(double_reversed), (
        "participants＋candidates 双反转时除 requested 外投影须相等"
    )
