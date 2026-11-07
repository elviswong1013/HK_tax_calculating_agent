# -*- coding: utf-8 -*-
"""REQ-5 兄弟姊妹／单亲／本人伤残／伤残受养人（002 P1 Red；Annex 005 事实契约）。

REQ: REQ-5（S002 §8/§9.4；PRD AC-5）。本文件为 SDD §9.4 REQ-5 命名节点，共 8 个：
  - test_002_sibling_care_not_money_only
  - test_002_sibling_adult_paths_alternative
  - test_002_single_parent_once_not_per_child_entitlement_without_base_claim
  - test_002_single_parent_exclusion_married_not_apart_anytime
  - test_002_s28a_personal_disability_eligibility_not_receipt
  - test_002_s31a_entitlement_without_base_claim
  - test_002_s31a_spouse_subject_paths
  - test_002_s26d_raw_facts_alternative_basis_no_auto_deduction

规格锚点:
  - S002 §3.2/§5.2（兄弟姊妹、单亲、s.28A、s.31A；s.31A entitlement 不要求基本
    claim；s.26D 原始事实可替代基础）；§8 REQ-5；
  - Annex 005 §7（s.30B 行：ANY(未婚 AND 受供养 AND 年龄/教育/伤残路径)＋
    ANY(sole_or_predominant_care)；s.32 行：child entitlement 不要求 claim＋
    ANY(sole_or_predominant_care)；s.32(2) 行：ANY(married AND NOT living_apart)
    为 true 即排除、后来分居不能抵消；s.28A 行：ANY(DAA)、subject=claimant；
    s.31A 行：ANY(subject DAA) AND 对应 entitlement；spouse DDA 行：s.29 分支 A
    ／合法 joint salaries／joint PA 任一路径）；
  - Annex 005 §5.1（无基本 claim 的伤残受养人示例）；§6.2（s.26D entitlement
    不依赖扣除项或基本 claim；residential_care_received 等是具体事实）；
    §9.1 S2/S3（分支 A＝配偶 AI=false＋未单独选 PA；双方 AI＝分别评税各基本额）；
    §15.2（sibling 37,500；personal_disability/disabled_dependant 75,000；
    single_parent 132,000/145,000；married 264,000）；§13/§14；§2.1。

期望值来源（禁从生产代码反推）:
  - 免税额：Annex 005 §15.2（转引附表 4，T1）。
  - 税率带：附表 1/2（Annex D D7；T1 台账 1.17）。
  - 无额外免税额基线 final 940：PAM39 Q1（T2 台账 1.13）。
  - 其余组合数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None)（005 §2.1）：
    v2 subject/year 事实模型；sibling 须 sole_or_predominant_care（汇钱不足）；
    单亲额每 claimant/scenario ≤1 次（不按子女数累加）、依赖 child entitlement
    而非 child claim；s.28A＝ANY(claimant DAA)；s.31A 不要求基本 claim/扣除项；
    spouse DDA 经 s.29 分支 A 成立时 married 264,000 归申索人并覆盖两人基础额；
    care_payments 原始事实可为 s.31A 派生 s.26D entitlement，且不自动产生
    ERCE 扣除（扣除须 deduction_items 意图）。
  - 本文件对 app.* 延迟导入（测试体内）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

import pytest


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
    kind: str = "child",
    basis: str = "birth",
    maintained: list | None = None,
    cash: str | None = None,
    cohabit: list | None = None,
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
    if cash is not None:
        r["cash_maintenance"] = cash
    if cohabit is not None:
        r["cohabit_non_full_consideration"] = cohabit
    if sole_care is not None:
        r["sole_or_predominant_care"] = sole_care
    return r


def _claim(cid: str, kind: str, claimant: str = "p1", subject: str = "d1") -> dict:
    return {"claim_id": cid, "kind": kind, "claimant_id": claimant, "subject_id": subject}


def _subjects_with(dependants: list[tuple[str, str]]) -> list[dict]:
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    subjects.extend({"subject_id": sid, "birth_date": birth} for sid, birth in dependants)
    return subjects


def _married_household(year: str, *, apart: list | None = None,
                       married_to: str = "2025-10-01",
                       nomination: dict | None = None) -> dict:
    """年内一次结婚（至 married_to）其后未婚的分居/婚姻 TF（005 §5.4 形状）。"""
    w_from, w_to = _window(year)
    marriage = [
        {"from": w_from, "to": married_to, "value": "married", "spouse_id": "p2"},
        {"from": married_to, "to": w_to, "value": "unmarried"},
    ]
    return {
        "marriage": marriage,
        "living_apart": apart if apart is not None else [],
        "child_nomination": nomination or {"person_id": None, "status": "none"},
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


# ---------------------------------------------------------------------------
# 断言辅助（005 §13/§14）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# REQ-5 命名节点（S002 §9.4）
# ---------------------------------------------------------------------------


def test_002_sibling_care_not_money_only() -> None:
    """兄弟姊妹额须 sole_or_predominant_care；只汇钱（供养）不足。

    规格锚点：Annex 005 §7（s.30B 行：ANY(未婚 AND 受供养 AND 路径) 且
    ANY(sole_or_predominant_care)——两谓词分别量化）；S002 §8 REQ-5（汇钱不足）。
    """
    year = "2025_26"
    subjects = _subjects_with([("d1", "2012-03-01")])

    money_only = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="sibling", basis="half_blood",
                             maintained=_tf(year, True), sole_care=_tf(year, False))],
        claims=[_claim("c1", "sibling")],
    ))
    decision = _decision(money_only, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"受供养成立但 sole_or_predominant_care=false → s.30B 不成立，实际：{decision!r}"
    )
    assert not (money_only.get("questions") or [])
    assert money_only["amounts"]["final_after_reduction"] == "940", money_only["amounts"]

    with_care = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {"unmarried": _tf(year, True)}},
        relations=[_relation(year, "r1", "p1", "d1", kind="sibling", basis="half_blood",
                             maintained=_tf(year, True), sole_care=_tf(year, True))],
        claims=[_claim("c1", "sibling")],
    ))
    decision = _decision(with_care, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"未婚＋受供养＋主要照顾 → s.30B 成立 37,500，实际：{decision!r}"
    )
    assert decision.get("amount") == "37500", decision
    assert _unit_base(with_care, "salary:p1", "allowances") == "169500/1"


def test_002_sibling_adult_paths_alternative() -> None:
    """兄弟姊妹成年替代路径：18–<25 全日制教育与 18+ 伤残不能工作均可成立。

    规格锚点：Annex 005 §7（s.30B 行的年龄/教育/伤残路径；OR(paths)）。
    """
    year = "2025_26"
    siblings = [("d1", "2005-09-01"), ("d2", "2003-01-01")]
    result = _run(_salary_input(
        year,
        _subjects_with(siblings),
        subject_facts={
            "d1": {"unmarried": _tf(year, True),
                   "full_time_education": _tf(year, True)},
            "d2": {"unmarried": _tf(year, True),
                   "incapacitated_for_work": _tf(year, True)},
        },
        relations=[_relation(year, f"r-{sid}", "p1", sid, kind="sibling",
                             basis="half_blood", maintained=_tf(year, True),
                             sole_care=_tf(year, True))
                   for sid, _ in siblings],
        claims=[_claim(f"c-{sid}", "sibling", subject=sid) for sid, _ in siblings],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    edu = _decision(result, subject_id="d1")
    incap = _decision(result, subject_id="d2")
    assert edu is not None and edu.get("entitlement") == "eligible", edu
    assert edu.get("amount") == "37500", edu
    assert incap is not None and incap.get("entitlement") == "eligible", incap
    assert incap.get("amount") == "37500", incap


def test_002_single_parent_once_not_per_child_entitlement_without_base_claim() -> None:
    """单亲额每 claimant 一次（不按子女累加）；依赖 child entitlement 而非 claim；
    资格须 ANY(sole_or_predominant_care) 已证 true。

    规格锚点：Annex 005 §4.5/§4.9（sole_or_predominant_care＝主要/独力照顾
    期间，触发路径必填）＋§7（s.32 行：child entitlement 不要求 claim＋
    ANY(sole_or_predominant_care)；每 claimant/scenario 至多一次）＋§5.1
    （未申索的基本额不自动计入）。
    P1 r1 纠错（复核 §五.1）：正例显式提交 true 照顾 TF（缺事实不得授额）；
    补 missing/unknown/false 反例；金额期望 132,000 保留。
    T3：两名子女 entitlement 成立但仅申索 single_parent → Part 5=
    132,000+132,000=264,000（不是 132,000+2×132,000，也不含未申索子女额）。
    """
    year = "2025_26"
    children = [("d1", "2015-06-01"), ("d2", "2015-08-20")]
    child_facts = {sid: {"unmarried": _tf(year, True)} for sid, _ in children}
    claim = [_claim("c1", "single_parent", subject="d1")]
    result = _run(_salary_input(
        year,
        _subjects_with(children),
        subject_facts=child_facts,
        relations=[
            _relation(year, f"r-{sid}", "p1", sid, maintained=_tf(year, True),
                      sole_care=_tf(year, True))
            for sid, _ in children
        ],
        claims=claim,  # 仅单亲申索（照顾 TF 显式 true）
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    granted = [d for d in _decisions(result)
               if d.get("claim_id") == "c1" and d.get("grant") == "allowed"]
    assert len(granted) == 1, (
        f"单亲额每 claimant/scenario ≤1 次（不按子女数累加），实际：{granted!r}"
    )
    assert granted[0].get("amount") == "132000", granted[0]
    assert not [d for d in _decisions(result)
                if d.get("item_kind") == "child" and d.get("grant") == "allowed"], (
        "无基本 child claim 时不得自动计入子女额（005 §5.1）"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "264000/1", (
        f"Part 5＝基本 132,000＋单亲 132,000，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


@pytest.mark.parametrize("state", ["missing", "unknown", "false"])
def test_002_single_parent_care_missing_unknown_false(state) -> None:
    """P1 r2（red-b-review 矩阵：O497/559–575 参数化）：单亲照顾三态独立节点。

    - missing/unknown → needs_input、不授额；追问定位 relation 的
      `sole_or_predominant_care` 字段（含 year/claim_id/subject_id 元数据，
      不得指向 subject_facts——复核 §一 A1）；
    - false → 可判定 ineligible（ANY=false）＋ final 940，不追问。
    """
    year = "2025_26"
    children = [("d1", "2015-06-01"), ("d2", "2015-08-20")]
    child_facts = {sid: {"unmarried": _tf(year, True)} for sid, _ in children}
    claim = [_claim("c1", "single_parent", subject="d1")]
    if state == "missing":
        relations = [
            _relation(year, f"r-{sid}", "p1", sid, maintained=_tf(year, True))
            for sid, _ in children
        ]
    elif state == "unknown":
        relations = [
            _relation(year, "r-d1", "p1", "d1", maintained=_tf(year, True),
                      sole_care=_tf(year, "unknown")),
            _relation(year, "r-d2", "p1", "d2", maintained=_tf(year, True),
                      sole_care=_tf(year, True)),
        ]
    else:
        relations = [
            _relation(year, "r-d1", "p1", "d1", maintained=_tf(year, True),
                      sole_care=_tf(year, False)),
            _relation(year, "r-d2", "p1", "d2", maintained=_tf(year, True),
                      sole_care=_tf(year, False)),
        ]
    result = _run(_salary_input(
        year,
        _subjects_with(children),
        subject_facts=child_facts,
        relations=relations,
        claims=claim,
    ))
    if state == "false":
        decision = _decision(result, claim_id="c1")
        assert decision is not None and decision.get("entitlement") == "ineligible", (
            f"sole_or_predominant_care=false → s.32 ANY=false → ineligible，"
            f"实际：{decision!r}"
        )
        assert result["amounts"]["final_after_reduction"] == "940", (
            result["amounts"]
        )
        return
    assert result["status"] == "needs_input", (state, result["status"])
    assert not [d for d in _decisions(result)
                if d.get("claim_id") == "c1" and d.get("grant") == "allowed"], (
        "缺/未知照顾事实不得授予单亲额（复核 §一 A1：缺事实授额＝缺陷）"
    )
    questions = result.get("questions") or []
    care_questions = [
        q for q in questions
        if isinstance(q, Mapping)
        and "sole_or_predominant_care" in str(q.get("field") or "")
    ]
    assert care_questions, (
        "缺/未知照顾事实的追问必须定位 relation care 字段"
        f"（复核 §一 A1：不得指向 subject_facts），实际：{questions!r}"
    )
    located = [
        q for q in care_questions
        if q.get("year") == year and q.get("claim_id") == "c1" and q.get("subject_id") == "d1"
    ]
    assert located, (
        "定位项须含 year/claim_id/subject_id 元数据（005 §13），"
        f"实际：{care_questions!r}"
    )


def test_002_single_parent_exclusion_married_not_apart_anytime() -> None:
    """单亲排除 ANY(married AND NOT living_apart)：婚内未分居即排除，后来离婚不抵消；
    婚内已分居则不排除。

    规格锚点：Annex 005 §7（s.32(2) 行：L36472–36478）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "p2", "birth_date": "1992-02-02"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]
    participants = [{"person_id": "p1", "role": "self"},
                    {"person_id": "p2", "role": "spouse"}]
    child_facts = {"d1": {"unmarried": _tf(year, True)}}
    child_relations = [_relation(year, "r1", "p1", "d1", maintained=_tf(year, True),
                                 sole_care=_tf(year, True))]
    claims = [_claim("c1", "single_parent", subject="d1")]

    # (a) 婚内未分居（其后离婚）→ 排除成立。
    excluded = _run(_salary_input(
        year, subjects,
        subject_facts=child_facts,
        household=_married_household(year, apart=[
            {"from": "2025-04-01", "to": "2025-10-01", "value": False},
        ]),
        participants=participants,
        relations=child_relations,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        claims=claims,
    ))
    decision = _decision(excluded, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        "婚内任一时点未分居 → ANY(married AND NOT apart)=true 即排除"
        f"（后来离婚不抵消），实际：{decision!r}"
    )
    assert excluded.get("status") != "blocked", excluded.get("status")

    # (b) 婚内全程分居 → 排除不成立。
    apart_all = _run(_salary_input(
        year, subjects,
        subject_facts=child_facts,
        household=_married_household(year, apart=[
            {"from": "2025-04-01", "to": "2025-10-01", "value": True},
        ]),
        participants=participants,
        relations=child_relations,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        claims=claims,
    ))
    decision = _decision(apart_all, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"婚内全程分居 → 排除不成立 → 单亲额 132,000，实际：{decision!r}"
    )
    assert decision.get("amount") == "132000", decision
    assert apart_all["status"] == "partial", apart_all["status"]


def test_002_s28a_personal_disability_eligibility_not_receipt() -> None:
    """本人伤残（s.28A）＝ANY(DAA 资格)；无任何「已领取」事实亦可成立。

    规格锚点：Annex 005 §7（s.28A 行：subject＝claimant）；§4.3
    （govt_disability_allowance＝资格期间，不问领取）。
    T3：Part 5=132,000+75,000=207,000；NCI=240,000−9,000−207,000=24,000 →
    24,000×2%=480；标准 34,650 → 择低 480；宽减 min(480,3,000)=480 → final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        subject_facts={"p1": {"govt_disability_allowance": _tf(year, True)}},
        claims=[_claim("c1", "personal_disability", subject="p1")],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"DAA 资格成立即 s.28A 成立（不问领取），实际：{decision!r}"
    )
    assert decision.get("amount") == "75000", decision
    assert _unit_base(result, "salary:p1", "allowances") == "207000/1"
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "480", amounts
    assert amounts["reduction"] == "480", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_s31a_entitlement_without_base_claim() -> None:
    """伤残受养人额不要求基本 claim：只申索 disabled_dependant 亦成立；
    未申索的基本子女额不自动计入。

    规格锚点：Annex 005 §5.1（同构示例）＋§7（s.31A(4) entitled 行）。
    T3：Part 5=132,000+75,000=207,000 → before 480／宽减 480／final 0（同上）。
    """
    year = "2025_26"
    birth = "2025-12-01"
    result = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": birth}],
        subject_facts={"d1": {
            "unmarried": [{"from": birth, "to": _window(year)[1], "value": True}],
            "govt_disability_allowance": [{"from": "2026-01-01", "to": _window(year)[1],
                                           "value": True}],
        }},
        relations=[_relation(year, "r1", "p1", "d1", maintained=[
            {"from": birth, "to": _window(year)[1], "value": True}
        ])],
        claims=[_claim("c1", "disabled_dependant")],  # 无基本 child claim
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"s.31A entitled 不要求先有基本 child claim（005 §5.1），实际：{decision!r}"
    )
    assert decision.get("amount") == "75000", decision
    assert not [d for d in _decisions(result)
                if d.get("item_kind") == "child" and d.get("grant") == "allowed"], (
        "未申索的基本额/出生额外额不得自动计入（005 §5.1）"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "207000/1"
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "480", amounts
    assert amounts["reduction"] == "480", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_s31a_spouse_subject_paths() -> None:
    """配偶为受养人的 DDA：s.29 分支 A（配偶 AI=false＋未单独选 PA）成立 →
    DDA 75,000＋married 264,000 归申索人；双方 AI（S3）→ DDA 不成立、各基本额。

    规格锚点：Annex 005 §7（spouse DDA 行）＋§9.1 S2/S3＋§9.1.1 行 1
    （分支 A 时 married 给对应申索人并覆盖两人基础额）＋§4.10（actual.pa
    稀疏条目：分支 A 需要配偶「未 individual PA」）。
    T3（正例）：Part 5=264,000+75,000=339,000；NCI=240,000−9,000−339,000<0 →
    final 0。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "p2", "birth_date": "1992-02-02"}]
    household = {
        "marriage": [{"from": _window(year)[0], "to": _window(year)[1],
                      "value": "married", "spouse_id": "p2"}],
        "living_apart": _tf(year, False),
        "child_nomination": {"person_id": None, "status": "none"},
    }
    participants = [{"person_id": "p1", "role": "self"},
                    {"person_id": "p2", "role": "spouse"}]
    claims = [_claim("c1", "disabled_dependant", subject="p2")]

    branch_a = _run(_salary_input(
        year, subjects,
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        household=household,
        participants=participants,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=False)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False,
                               "pa": {"p2": {"mode": "none"}}}},
        claims=claims,
    ))
    decision = _decision(branch_a, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"分支 A（配偶 AI=false＋未单独选 PA）→ spouse DDA 成立，实际：{decision!r}"
    )
    assert decision.get("amount") == "75000", decision
    assert _unit_base(branch_a, "salary:p1", "allowances") == "339000/1", (
        f"分支 A：married 264,000 覆盖两人基础额＋DDA 75,000，实际："
        f"{_unit_base(branch_a, 'salary:p1', 'allowances')!r}"
    )
    assert branch_a["amounts"]["final_after_reduction"] == "0", branch_a["amounts"]

    both_ai = _run(_salary_input(
        year, subjects,
        subject_facts={"p2": {"govt_disability_allowance": _tf(year, True)}},
        household=household,
        participants=participants,
        financials={"p1": _fin(), "p2": _fin(nai="0", mpf="0", ai=True)},
        assessment={"basis": "actual", "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                    "actual": {"salary_joint_elected": False}},
        claims=claims,
    ))
    decision = _decision(both_ai, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"双方 AI＋未选联合（S3）→ 无 s.29 entitlement → spouse DDA 不成立，"
        f"实际：{decision!r}"
    )
    assert _unit_base(both_ai, "salary:p1", "allowances") == "132000/1", (
        f"S3：分别评税各基本额（p1 单位仅 132,000），实际："
        f"{_unit_base(both_ai, 'salary:p1', 'allowances')!r}"
    )
    assert not [d for d in _decisions(both_ai) if d.get("amount") == "264000"], (
        "S3 下不得输出已婚免税额 decision"
    )


@pytest.mark.parametrize(
    "birth",
    ["1950-05-01", "1990-01-01"],
    ids=["age-60-plus", "under60-with-daa"],
)
def test_002_s26d_raw_facts_alternative_basis_no_auto_deduction(birth) -> None:
    """s.26D 原始事实可为 s.31A 派生受养人 entitlement（不经父母额）；care_payments
    本身不自动产生 ERCE 扣除。

    规格锚点：Annex 005 §6.2（s.26D entitlement 不依赖扣除项或基本 claim；
    care_payments＝具体事实，DeductionIntent 未提交不影响）＋§4.8。
    构造：长者不居港（父母额 entitlement 不成立）、DAA 资格成立（60+ 或 <60
    由 DAA 满足 s.26D(1) 任一时点路径）、完整院舍原始事实；
    无 deduction_items、无父母 claim，仅申索 disabled_dependant。
    T3：Part 4A＝仅 MPF 9,000（无自动扣除）；Part 5=132,000+75,000=207,000 →
    before 480／final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": birth}],
        subject_facts={"d1": {
            "ordinarily_resident_hk": _tf(year, False),  # 父母额肢不成立
            "govt_disability_allowance": _tf(year, True),
        }},
        relations=[_relation(year, "r1", "p1", "d1", kind="parent")],
        care_payments=[{
            "payment_id": "e1",
            "payer_id": "p1",
            "subject_id": "d1",
            "paid_on": "2025-06-15",
            "amount": "80000",
            "care_from": "2025-04-01",
            "care_to": "2026-04-01",
            "residential_care_received": True,
            "paid_to_home_or_agent": True,
            "home_basis": "cap459_licence",
            "home_status_in_force": [{"from": "2025-04-01", "to": "2026-04-01",
                                      "value": True}],
        }],
        claims=[_claim("c1", "disabled_dependant")],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        "s.26D 原始事实成立即可为 s.31A 派生受养人 entitlement"
        f"（不依赖父母额 entitlement/扣除意图），实际：{decision!r}"
    )
    assert decision.get("amount") == "75000", decision
    assert _unit_base(result, "salary:p1", "part4a") == "9000/1", (
        f"无 deduction_items 意图 → care_payments 不自动产生 ERCE 扣除，实际："
        f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "207000/1"
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "480", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_single_parent_two_claims_granted_once_with_reason() -> None:
    """P1 r1 新 Red（复核 §六.1）：同 claimant 两条单亲申索（不同 subject）→
    每 claimant/scenario 仍只授一次（264,000 非 396,000）；第二条须带
    「未额外授予」理由（reason_codes 非空或 grant≠allowed）。

    规格锚点：Annex 005 §7（s.32 行：每 claimant/scenario 至多一次）＋
    §14（decision reason_codes）。复核 §三 R5：single_parent 按 claim_id
    查重（观察两申索 396,000/1，应只 264,000）。
    T3：Part 5=132,000(基本)＋132,000(单亲一次)=264,000。
    """
    year = "2025_26"
    children = [("d1", "2015-06-01"), ("d2", "2015-08-20")]
    result = _run(_salary_input(
        year,
        _subjects_with(children),
        subject_facts={sid: {"unmarried": _tf(year, True)} for sid, _ in children},
        relations=[
            _relation(year, f"r-{sid}", "p1", sid, maintained=_tf(year, True),
                      sole_care=_tf(year, True))
            for sid, _ in children
        ],
        claims=[
            {"claim_id": "c1", "kind": "single_parent",
             "claimant_id": "p1", "subject_id": "d1"},
            {"claim_id": "c2", "kind": "single_parent",
             "claimant_id": "p1", "subject_id": "d2"},
        ],
    ))
    assert _unit_base(result, "salary:p1", "allowances") == "264000/1", (
        f"两条单亲申索同 claimant → 只授一次（264,000，非 396,000），实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    allowed = [d for d in _decisions(result)
               if d.get("item_kind") == "single_parent"
               and d.get("grant") == "allowed"]
    assert len(allowed) == 1, (
        f"单亲额每 claimant/scenario ≤1 次，实际 allowed：{allowed!r}"
    )
    assert allowed[0].get("amount") == "132000", allowed[0]
    second = _decision(result, claim_id="c2")
    assert second is not None, "第二条申索必须有 decision（不静默丢弃）"
    assert second.get("grant") != "allowed" or second.get("reason_codes"), (
        f"第二条须带未额外授予理由（grant≠allowed 或 reason_codes 非空），"
        f"实际：{second!r}"
    )


def test_002_sibling_dda_without_base_sibling_claim() -> None:
    """P1 r1 新 Red（复核 §六.8）：sibling entitlement 可作 s.31A 受养人
    entitlement 基础——仅申索 disabled_dependant（sibling subject、无基本
    sibling claim）亦成立；未申索的基本 sibling 额不自动计入。

    规格锚点：Annex 005 §7（s.31A 行：ANY(subject DAA) AND 对应
    child/parent/grandparent/sibling/spouse entitlement，不要求基本 claim）。
    复核 §三 R5：sibling/spouse DDA entitlement 复用（观察 ineligible）。
    T3：Part 5=132,000＋75,000=207,000；NCI=240,000−9,000−207,000=24,000
    → 480；宽减 480 → final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        [{"subject_id": "p1", "birth_date": "1990-01-01"},
         {"subject_id": "d1", "birth_date": "2012-03-01"}],
        subject_facts={"d1": {
            "unmarried": _tf(year, True),
            "govt_disability_allowance": _tf(year, True),
        }},
        relations=[_relation(year, "r1", "p1", "d1", kind="sibling",
                             basis="half_blood", maintained=_tf(year, True),
                             sole_care=_tf(year, True))],
        claims=[_claim("c1", "disabled_dependant")],  # 无基本 sibling claim
    ))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"sibling entitlement（未婚＋受供养＋未成年＋照顾）＋DAA → s.31A 成立"
        f"（不要求基本 sibling claim），实际：{decision!r}"
    )
    assert decision.get("grant") == "allowed" and decision.get("amount") == "75000", (
        decision
    )
    assert not [d for d in _decisions(result)
                if d.get("item_kind") == "sibling" and d.get("grant") == "allowed"], (
        "未申索的基本 sibling 额不得自动计入"
    )
    assert _unit_base(result, "salary:p1", "allowances") == "207000/1"
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "480", amounts
    assert amounts["final_after_reduction"] == "0", amounts

# ---------------------------------------------------------------------------
# F5/R5（Wave C，2026-10-17）：s.26D 替代基础不以 claimant 直接关系为前提
# ---------------------------------------------------------------------------


_F5_YEAR = "2025_26"
_F5_SUBJECTS = [
    {"subject_id": "p1", "birth_date": "1990-01-01"},
    {"subject_id": "p2", "birth_date": "1992-02-02"},
    {"subject_id": "d1", "birth_date": "1950-05-01"},
]
_F5_PARTICIPANTS = [
    {"person_id": "p1", "role": "self"},
    {"person_id": "p2", "role": "spouse"},
]


def _f5_household(year: str, apart: list | None = None) -> dict:
    w_from, w_to = _window(year)
    return {
        "marriage": [
            {"from": w_from, "to": w_to, "value": "married", "spouse_id": "p2"}
        ],
        "living_apart": apart if apart is not None else _tf(year, False),
        "child_nomination": {"person_id": None, "status": "none"},
    }


def _f5_care_payment(*, residential: Any = True) -> dict:
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
        "home_status_in_force": [
            {"from": "2025-04-01", "to": "2026-04-01", "value": True}
        ],
    }


def _f5_year_kwargs(
    year: str,
    *,
    actor: str,
    kind: str = "parent",
    daa: Any = True,
    apart: list | None = None,
    residential: Any = True,
) -> dict:
    return {
        "subject_facts": {
            "d1": {
                "ordinarily_resident_hk": _tf(year, True),
                "govt_disability_allowance": _tf(year, daa),
            }
        },
        "household": _f5_household(year, apart),
        "participants": _F5_PARTICIPANTS,
        "relations": [
            {
                "relation_id": "r1",
                "actor_id": actor,
                "subject_id": "d1",
                "kinship": {
                    "kind": kind,
                    "basis": "birth",
                    "during": _tf(year, True),
                },
                "cash_maintenance": "12000",
            }
        ],
        "financials": {
            "p1": _fin(nai="600000", mpf="18000", ai=True),
            "p2": _fin(nai="0", mpf="0", ai=True),
        },
        "care_payments": [_f5_care_payment(residential=residential)],
        "claims": [_claim("c1", "disabled_dependant", claimant="p1", subject="d1")],
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
            "actual": {"salary_joint_elected": False},
        },
    }


def _f5_run(**kwargs: Any) -> dict:
    year = _F5_YEAR
    return _run(
        _salary_input(
            year,
            [dict(item) for item in _F5_SUBJECTS],
            **_f5_year_kwargs(year, **kwargs),
        )
    )


def _f5_assert_legal_control(result: Mapping, label: str) -> None:
    """直接/配偶关系 actor 均应得到同一合法 DDA 结果（T3 手算 final 42,750）。"""
    assert result["status"] == "partial", (label, result["status"])
    assert not (result.get("questions") or []), (label, result.get("questions"))
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"{label}：s.26D 替代基础成立时 DDA 应 eligible，实际：{decision!r}"
    )
    assert decision.get("grant") == "allowed" and decision.get("amount") == "75000", (
        label,
        decision,
    )
    assert _unit_base(result, "salary:p1", "allowances") == "207000/1", (
        f"{label}：132,000 基本额＋75,000 DDA＝207,000，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p1", "part4a") == "18000/1", (
        f"{label}：care_payments 不自动生成 ERCE 扣除（Part 4A 仅 MPF 18,000），"
        f"实际：{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "42750", (
        f"{label}：NCI=600,000−18,000−207,000=375,000；累进 16,000＋17%×"
        f"175,000=45,750；宽减 3,000 → 42,750，实际：{result['amounts']!r}"
    )


def test_002_f5_s26d_bridge_spouse_relation_actor_dda() -> None:
    """F5/R5：同一合法事实下，仅把 parent relation 的 actor 从 claimant p1
    改为其非分居配偶 p2——p1 无直接关系时仍须评估 s.26D 替代路径，DDA 成立，
    金额/Part 4A/税额与直接关系控制一致。

    规格锚点：Annex 005 §6.2（s.26D entitlement 不依赖扣除意图/基本 claim；
    配偶亲属在 s.26D 范围内）＋§4.8＋gate-p1-r3 F5（final=42,750 控制）。
    T3：Part 5=132,000＋75,000=207,000；NCI=600,000−18,000−207,000=375,000
    → 16,000＋17%×175,000=45,750；宽减 3,000 → 42,750。p2 NAI=0 → 0。
    """
    direct = _f5_run(actor="p1")
    spouse = _f5_run(actor="p2")
    _f5_assert_legal_control(direct, "直接关系控制")
    _f5_assert_legal_control(spouse, "配偶亲属 actor")
    assert (
        spouse["amounts"]["final_after_reduction"]
        == direct["amounts"]["final_after_reduction"]
    ), "仅 actor 变化不得改变税额（同一合法控制）"


def test_002_f5_s26d_bridge_spouse_grandparent_same_family() -> None:
    """F5/R5 边界：spouse 的 grandparent 同属 s.26D 父/母/祖父母族，不可因
    claimant 无直接关系而拒绝。

    规格锚点：Annex 005 §6.2＋§4.8（关系种类 parent/grandparent）。
    T3：同上一节点（Part 5=207,000；final 42,750）。
    """
    direct = _f5_run(actor="p1", kind="grandparent")
    spouse = _f5_run(actor="p2", kind="grandparent")
    _f5_assert_legal_control(direct, "直接 grandparent 控制")
    _f5_assert_legal_control(spouse, "配偶 grandparent")
    assert (
        spouse["amounts"]["final_after_reduction"]
        == direct["amounts"]["final_after_reduction"]
    )


def test_002_f5_s26d_bridge_negative_variants_no_wrong_grant() -> None:
    """F5/R5 负例：分居已真/未知、DAA false/unknown、院舍未决均不得误授
    DDA；替代未证 true 也不得把直接关系缺失改成 false（保留 unknown）。

    规格锚点：Annex 005 §4.8（s.26D 院舍/付款/年龄-DAA/非分居事实按活动消费）
    ＋§6.2；三态 OR（None∨True=True，但未知不得自动授予）。
    """
    year = _F5_YEAR
    variants = [
        ("已证分居", {"actor": "p2", "apart": _tf(year, True)}),
        ("分居未知", {"actor": "p2", "apart": []}),
        ("DAA false", {"actor": "p2", "daa": False}),
        ("DAA unknown", {"actor": "p2", "daa": "unknown"}),
        ("院舍未决", {"actor": "p2", "residential": "unknown"}),
    ]
    for label, kwargs in variants:
        result = _f5_run(**kwargs)
        granted = [
            d
            for d in _decisions(result)
            if d.get("item_kind") == "disabled_dependant"
            and d.get("grant") == "allowed"
        ]
        assert not granted, (
            f"{label}：s.26D 替代未证 true，不得误授伤残受养人额，实际：{granted!r}"
        )
        decision = _decision(result, claim_id="c1")
        assert decision is not None, f"{label}：申索必须有 decision（不得静默丢弃）"
        assert decision.get("grant") != "allowed", (label, decision)
        if label == "DAA false":
            assert decision.get("entitlement") == "ineligible", (label, decision)
            assert _unit_base(result, "salary:p1", "allowances") == "132000/1", (
                f"{label}：无 DDA，仅基本额 132,000，实际："
                f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
            )
            assert result["amounts"]["final_after_reduction"] == "55500", (
                f"{label}：NCI=450,000 → 累进 58,500；宽减 3,000 → 55,500，"
                f"实际：{result['amounts']!r}"
            )
        else:
            assert result["status"] == "needs_input", (label, result["status"])
            assert result["amounts"]["final_after_reduction"] is None, (
                label,
                result["amounts"],
            )
            assert result.get("questions"), f"{label}：未决必须给出定位追问"
            assert _unit_base(result, "salary:p1", "allowances") is None, (
                f"{label}：未决不得产出确定 assessment 单位，实际："
                f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
            )


def _f5_pa_bundle() -> dict:
    """在共享束上补 PA 活动参数（物业/利得比较口径；本节点只用 pa_self）。"""
    bundle = _v2_bundle()
    m = bundle["data"]["allowances"]
    v1_compat_pa = {
        y: {
            "basic": m[y]["basic"]["amount"],
            "married": m[y]["married"]["amount"],
        }
        for y in ("2024_25", "2025_26", "2026_27")
    }
    bundle["data"]["personal_assessment"] = {
        "progressive_bands": _PROGRESSIVE,
        "standard_rate_bands": _STANDARD,
        "property_standard_rate": "15/100",
        "repair_allowance_ratio": "1/5",
        "profits_two_tier": {
            "threshold": "2000000",
            "low_rate_bp": 750,
            "high_rate_bp": 1500,
        },
        "profits_reductions": {
            "2024_25": {"percent_bp": 10000, "cap": "1500"},
            "2025_26": {"percent_bp": 10000, "cap": "3000"},
        },
        "allowances": v1_compat_pa,
        "mpf_deduction_cap": m["mpf_deduction_cap"],
        "reductions": {
            "2024_25": {"percent_bp": 10000, "cap": "1500"},
            "2025_26": {"percent_bp": 10000, "cap": "3000"},
        },
    }
    return bundle


def _f5_run_pa(v2_input: dict) -> dict:
    from app.engines.personal_assessment import compare_personal_assessment

    bundle = _f5_pa_bundle()
    return compare_personal_assessment(
        v2_input,
        bundle,
        availability=_availability(bundle, v2_input["year_of_assessment"]),
    )


def test_002_f5_s26d_bridge_shared_resolver_pa_path() -> None:
    """F5/R5 贯通：PA 候选经同一 `resolve_claims` 消费 s.26D 替代基础——
    配偶 parent relation＋claimant p1 付款在 pa_self 下亦须授 DDA 75,000。

    规格锚点：Annex 005 §6.2＋§9.2（PA 候选共享 Part 5 解析器）。
    T3：pa:p1＝basic 132,000＋DDA 75,000=207,000；salary:p2 基本额 132,000。
    """
    year = _F5_YEAR
    w_from, w_to = _window(year)
    reference = "2026-06-01"
    result = _f5_run_pa(
        _salary_input(
            year,
            [dict(item) for item in _F5_SUBJECTS],
            subject_facts={
                "p1": {
                    "ordinarily_resident_hk": [
                        {"from": reference, "to": "2026-06-02", "value": True}
                    ]
                },
                "d1": {
                    "ordinarily_resident_hk": _tf(year, True),
                    "govt_disability_allowance": _tf(year, True),
                },
            },
            household={
                "marriage": [
                    {
                        "from": w_from,
                        "to": w_to,
                        "value": "married",
                        "spouse_id": "p2",
                    },
                    {
                        "from": reference,
                        "to": "2026-06-02",
                        "value": "married",
                        "spouse_id": "p2",
                    },
                ],
                "living_apart": [
                    {"from": w_from, "to": w_to, "value": False},
                    {"from": reference, "to": "2026-06-02", "value": False},
                ],
                "child_nomination": {"person_id": None, "status": "none"},
            },
            participants=_F5_PARTICIPANTS,
            relations=[
                {
                    "relation_id": "r1",
                    "actor_id": "p2",
                    "subject_id": "d1",
                    "kinship": {
                        "kind": "parent",
                        "basis": "birth",
                        "during": _tf(year, True),
                    },
                    "cash_maintenance": "12000",
                }
            ],
            financials={
                "p1": _fin(
                    nai="240000",
                    mpf="9000",
                    ai=True,
                    has_income_assessable_under_ordinance=True,
                ),
                "p2": _fin(
                    nai="0",
                    mpf="0",
                    ai=True,
                    has_income_assessable_under_ordinance=True,
                ),
            },
            care_payments=[_f5_care_payment()],
            claims=[
                _claim("c1", "disabled_dependant", claimant="p1", subject="d1")
            ],
            assessment={
                "basis": "hypothetical",
                "reference_date": reference,
                "candidates": ["pa_self"],
                "selection": "pa_self",
            },
        )
    )
    assert result["status"] == "partial", result["status"]
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("grant") == "allowed", (
        f"PA 共享 resolver：配偶 parent 的 s.26D 替代基础应授 DDA，实际：{decision!r}"
    )
    assert decision.get("amount") == "75000", decision
    assert _unit_base(result, "pa:p1", "allowances") == "207000/1", (
        f"pa_self：basic 132,000＋DDA 75,000=207,000，实际："
        f"{_unit_base(result, 'pa:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") == "132000/1", (
        f"pa_self：配偶薪俸单位基础额 132,000，实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )


# ---------------------------------------------------------------------------
# Wave C 一次补正（2026-10-17）：显式 claimant none 不得否定配偶已成立路径
# ---------------------------------------------------------------------------


def _f5_none_relation(year: str) -> dict:
    """claimant p1 对 d1 的显式直接关系 none（005 §4.5:170 直接关系事实）。"""
    return {
        "relation_id": "r-none",
        "actor_id": "p1",
        "subject_id": "d1",
        "kinship": {
            "kind": "none",
            "basis": "other",
            "during": _tf(year, True),
        },
    }


def _f5_spouse_parent_relations(year: str, *, spouse_path: bool) -> list[dict]:
    relations = []
    if spouse_path:
        relations.append(
            {
                "relation_id": "r1",
                "actor_id": "p2",
                "subject_id": "d1",
                "kinship": {
                    "kind": "parent",
                    "basis": "birth",
                    "during": _tf(year, True),
                },
                "cash_maintenance": "12000",
            }
        )
    relations.append(_f5_none_relation(year))
    return relations


def _f5_run_with_explicit_none(*, spouse_path: bool) -> dict:
    year = _F5_YEAR
    kwargs = _f5_year_kwargs(year, actor="p2")
    kwargs["relations"] = _f5_spouse_parent_relations(year, spouse_path=spouse_path)
    return _run(
        _salary_input(
            year,
            [dict(item) for item in _F5_SUBJECTS],
            **kwargs,
        )
    )


def test_002_f5_s26d_bridge_explicit_none_claimant_keeps_spouse_path() -> None:
    """补正回归 (a)：配偶 parent 路径已成立时，claimant 的显式直接关系
    `none` 不得否定该路径（005 §4.5:170–172：kinship 仅表达直接关系，配偶
    亲属经当年婚姻连接；§6.2:487；§7:496 OR：任一路径 true→true）。

    期望值与无 none 事实的配偶 actor 控制完全相同：DDA 75,000；
    Part 5=132,000＋75,000=207,000；Part 4A 仅 MPF 18,000；final 42,750。
    """
    baseline = _f5_run(actor="p2")
    with_none = _f5_run_with_explicit_none(spouse_path=True)
    _f5_assert_legal_control(baseline, "配偶 parent 基线")
    _f5_assert_legal_control(with_none, "配偶 parent＋claimant 显式 none")
    assert with_none["amounts"] == baseline["amounts"], (
        "显式 claimant none 不得改变已成立配偶路径的金额，实际："
        f"{with_none['amounts']!r} vs {baseline['amounts']!r}"
    )
    assert with_none["amounts"]["final_after_reduction"] == "42750"


def test_002_f5_s26d_bridge_explicit_none_claimant_without_spouse_path_needs_input() -> None:
    """补正回归 (b) 负例：claimant 显式 none 且配偶路径不存在（无 p2 关系事实）
    → unknown/needs_input，不得误授 DDA（005 §7:496：None∨False=None）。"""
    result = _f5_run_with_explicit_none(spouse_path=False)
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "未决必须给出定位追问"
    decision = _decision(result, claim_id="c1")
    assert decision is not None, "申索必须有 decision（不得静默丢弃）"
    assert decision.get("grant") != "allowed", decision
    granted = [
        d
        for d in _decisions(result)
        if d.get("item_kind") == "disabled_dependant"
        and d.get("grant") == "allowed"
    ]
    assert not granted, f"配偶路径不存在时不得误授 DDA，实际：{granted!r}"
    assert _unit_base(result, "salary:p1", "allowances") is None, (
        f"未决不得产出确定 assessment 单位，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def _f5_pa_input_with_explicit_none() -> dict:
    year = _F5_YEAR
    w_from, w_to = _window(year)
    reference = "2026-06-01"
    return _salary_input(
        year,
        [dict(item) for item in _F5_SUBJECTS],
        subject_facts={
            "p1": {
                "ordinarily_resident_hk": [
                    {"from": reference, "to": "2026-06-02", "value": True}
                ]
            },
            "d1": {
                "ordinarily_resident_hk": _tf(year, True),
                "govt_disability_allowance": _tf(year, True),
            },
        },
        household={
            "marriage": [
                {
                    "from": w_from,
                    "to": w_to,
                    "value": "married",
                    "spouse_id": "p2",
                },
                {
                    "from": reference,
                    "to": "2026-06-02",
                    "value": "married",
                    "spouse_id": "p2",
                },
            ],
            "living_apart": [
                {"from": w_from, "to": w_to, "value": False},
                {"from": reference, "to": "2026-06-02", "value": False},
            ],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        participants=_F5_PARTICIPANTS,
        relations=_f5_spouse_parent_relations(year, spouse_path=True),
        financials={
            "p1": _fin(
                nai="240000",
                mpf="9000",
                ai=True,
                has_income_assessable_under_ordinance=True,
            ),
            "p2": _fin(
                nai="0",
                mpf="0",
                ai=True,
                has_income_assessable_under_ordinance=True,
            ),
        },
        care_payments=[_f5_care_payment()],
        claims=[
            _claim("c1", "disabled_dependant", claimant="p1", subject="d1")
        ],
        assessment={
            "basis": "hypothetical",
            "reference_date": reference,
            "candidates": ["pa_self"],
            "selection": "pa_self",
        },
    )


def test_002_f5_s26d_bridge_explicit_none_claimant_shared_resolver_pa_path() -> None:
    """补正回归 (c)：PA `pa_self` 经同一 `resolve_claims`；配偶 parent＋claimant
    显式 none 仍授 DDA 75,000，`pa:p1` allowances=207,000、final=0。

    规格锚点：005 §4.5:170–172＋§6.2:487＋§7:496；§9.2（PA 候选共享 Part 5）。
    """
    result = _f5_run_pa(_f5_pa_input_with_explicit_none())
    assert result["status"] == "partial", result["status"]
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("grant") == "allowed", (
        f"PA 共享 resolver：显式 claimant none 不得否定配偶 parent 路径，实际："
        f"{decision!r}"
    )
    assert decision.get("amount") == "75000", decision
    assert _unit_base(result, "pa:p1", "allowances") == "207000/1", (
        f"pa_self：basic 132,000＋DDA 75,000=207,000，实际："
        f"{_unit_base(result, 'pa:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") == "132000/1", (
        f"pa_self：配偶薪俸单位基础额 132,000，实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


# ---------------------------------------------------------------------------
# Wave C 补正二（2026-10-17）：显式 none 须解析为 false（不得当作缺失）
# ---------------------------------------------------------------------------


def _f5_fix2_none_relation(year: str, actor: str) -> dict:
    """actor 对 d1 的显式直接关系 none（005 §4.5:170 直接关系事实）。"""
    return {
        "relation_id": f"r-none-{actor}",
        "actor_id": actor,
        "subject_id": "d1",
        "kinship": {
            "kind": "none",
            "basis": "other",
            "during": _tf(year, True),
        },
    }


def _f5_fix2_spouse_relation(year: str, *, during: bool) -> dict:
    return {
        "relation_id": "r1",
        "actor_id": "p2",
        "subject_id": "d1",
        "kinship": {
            "kind": "parent",
            "basis": "birth",
            "during": _tf(year, during),
        },
        "cash_maintenance": "12000",
    }


def _f5_fix2_relations(
    year: str,
    *,
    spouse_during: bool | None,
    both_none: bool = False,
    include_spouse: bool = True,
) -> list[dict]:
    """构造 p1 显式 none＋配偶路径（parent during 可控／双方 none／无配偶事实）。"""
    relations: list[dict] = []
    if include_spouse:
        if both_none:
            relations.append(_f5_fix2_none_relation(year, "p2"))
        else:
            relations.append(_f5_fix2_spouse_relation(year, during=bool(spouse_during)))
    relations.append(_f5_fix2_none_relation(year, "p1"))
    return relations


def _f5_fix2_run(relations: list[dict], *, care_payments: list | None = None) -> dict:
    year = _F5_YEAR
    kwargs = _f5_year_kwargs(year, actor="p2")
    kwargs["relations"] = relations
    if care_payments is not None:
        kwargs["care_payments"] = care_payments
    return _run(
        _salary_input(
            year,
            [dict(item) for item in _F5_SUBJECTS],
            **kwargs,
        )
    )


def _f5_fix2_pa_run(relations: list[dict]) -> dict:
    v2_input = _f5_pa_input_with_explicit_none()
    v2_input["years"][_F5_YEAR]["relations"] = relations
    return _f5_run_pa(v2_input)


def _f5_fix2_assert_dda_ineligible(result: Mapping, label: str) -> None:
    """显式 false 的确定结果：DDA ineligible，不进入 unresolved/unknown。"""
    assert result["status"] == "partial", (label, result["status"])
    assert not (result.get("questions") or []), (label, result.get("questions"))
    decision = _decision(result, claim_id="c1")
    assert decision is not None, f"{label}：申索必须有 decision（不得静默丢弃）"
    assert decision.get("entitlement") == "ineligible", (label, decision)
    assert decision.get("grant") == "ineligible", (label, decision)
    assert decision.get("amount") is None, (label, decision)
    assert decision.get("reason_codes") == ["DISABLED_DEPENDANT_NOT_ENTITLED"], (
        label,
        decision,
    )
    allowed = [
        d
        for d in _decisions(result)
        if d.get("item_kind") == "disabled_dependant"
        and d.get("grant") == "allowed"
    ]
    assert not allowed, f"{label}：显式 false 不得授予 DDA，实际：{allowed!r}"


def test_002_f5_s26d_bridge_explicit_none_spouse_parent_false_ineligible() -> None:
    """补正二 (a)：claimant 显式 none＋配偶 parent during=false → 直接关系 false，
    s.26D 替代亦 false；false∨false=false → DDA ineligible（不是 unknown），
    薪俸按 DDA 不合资格给出确定结果（既有 DAA false 口径：Part 5=132,000；
    NCI=600,000−18,000−132,000=450,000；累进 58,500；宽减 3,000 → final 55,500）。
    """
    result = _f5_fix2_run(_f5_fix2_relations(_F5_YEAR, spouse_during=False))
    _f5_fix2_assert_dda_ineligible(result, "薪俸：显式 none＋配偶 parent false")
    assert _unit_base(result, "salary:p1", "allowances") == "132000/1", (
        "DDA 不合资格时仅基本免税额 132,000，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p1", "part4a") == "18000/1", (
        f"care_payments 不自动生成 ERCE 扣除，实际："
        f"{_unit_base(result, 'salary:p1', 'part4a')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "55500", result["amounts"]


def test_002_f5_s26d_bridge_explicit_none_spouse_parent_false_pa_ineligible() -> None:
    """补正二 (b)：同构 PA 路径（pa_self 共用 resolve_claims）——显式 none＋
    配偶 parent during=false 亦须 DDA ineligible；PA 按不合资格确定计算：
    pa:p1 allowances=132,000；NCI=240,000−9,000−132,000=99,000；
    累进 1,000＋2,940=3,940；宽减 3,000 → final 940。
    """
    result = _f5_fix2_pa_run(_f5_fix2_relations(_F5_YEAR, spouse_during=False))
    _f5_fix2_assert_dda_ineligible(result, "PA：显式 none＋配偶 parent false")
    assert _unit_base(result, "pa:p1", "allowances") == "132000/1", (
        f"PA DDA 不合资格时仅基本免税额 132,000，实际："
        f"{_unit_base(result, 'pa:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") == "132000/1", (
        f"配偶薪俸单位基础额 132,000，实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "940", result["amounts"]


def test_002_f5_s26d_bridge_both_actors_none_ineligible() -> None:
    """补正二 (c)：claimant 与配偶双方均显式 none → 两条直接关系事实均 false；
    false∨false=false → DDA ineligible，薪俸确定结果与 (a) 同额。"""
    result = _f5_fix2_run(
        _f5_fix2_relations(_F5_YEAR, spouse_during=False, both_none=True)
    )
    _f5_fix2_assert_dda_ineligible(result, "薪俸：双方 actor none")
    assert _unit_base(result, "salary:p1", "allowances") == "132000/1", (
        f"双方 none 时 DDA 不合资格，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    assert result["amounts"]["final_after_reduction"] == "55500", result["amounts"]


def test_002_f5_s26d_bridge_explicit_none_spouse_parent_true_allowed_control() -> None:
    """补正二 (d) 控制（pre-P）：claimant 显式 none＋配偶 parent during=true
    → 直接 false∨替代 true=true，DDA 仍 allowed 75,000；final 42,750 不变。"""
    result = _f5_fix2_run(_f5_fix2_relations(_F5_YEAR, spouse_during=True))
    _f5_assert_legal_control(result, "补正二控制：显式 none＋配偶 parent true")


def test_002_f5_s26d_bridge_no_spouse_relation_needs_input_control() -> None:
    """补正二 (e) 控制（pre-P）：配偶关系事实缺失（None）不得因 claimant 显式
    none=false 被降为 ineligible——None∨False=None，保持 unknown/needs_input。
    第二个子例覆盖「直接关系缺失（None）＋s.26D false（无付款）」同口径。"""
    missing_spouse = _f5_fix2_run(
        _f5_fix2_relations(
            _F5_YEAR, spouse_during=False, include_spouse=False
        )
    )
    for label, result in (("配偶关系缺失", missing_spouse),):
        assert result["status"] == "needs_input", (label, result["status"])
        assert result["amounts"]["final_after_reduction"] is None, (
            label,
            result["amounts"],
        )
        assert result.get("questions"), f"{label}：未决必须给出定位追问"
        decision = _decision(result, claim_id="c1")
        assert decision is not None, f"{label}：申索必须有 decision"
        assert decision.get("entitlement") == "unknown", (label, decision)
        assert decision.get("grant") == "unresolved", (label, decision)
        assert _unit_base(result, "salary:p1", "allowances") is None, (
            label,
            _unit_base(result, "salary:p1", "allowances"),
        )

    no_relations_no_payments = _f5_fix2_run([], care_payments=[])
    assert no_relations_no_payments["status"] == "needs_input", (
        "直接关系缺失＋s.26D false：None∨False=None",
        no_relations_no_payments["status"],
    )
    assert (
        no_relations_no_payments["amounts"]["final_after_reduction"] is None
    ), no_relations_no_payments["amounts"]


# ---------------------------------------------------------------------------
# Wave C 补正三（2026-10-17）：缺失 kinship 保持缺失（不得当作显式 none）
# ---------------------------------------------------------------------------


def _f5_fix3_missing_kinship_relation(year: str, actor: str) -> dict:
    """显式 none 关系仅删除 `kinship` 键：缺失 ≠ false（005 §4.5:164、§6:468）。"""
    relation = _f5_fix2_none_relation(year, actor)
    relation.pop("kinship")
    return relation


def _f5_fix3_unknown_relation(year: str, actor: str) -> dict:
    """显式 unknown 关系（005 §4.5:170）：三态未定，不得误判 false/true。"""
    relation = _f5_fix2_none_relation(year, actor)
    relation["kinship"]["kind"] = "unknown"
    return relation


def _f5_fix3_missing_relations(
    year: str, *, spouse_during: bool, include_spouse: bool = True
) -> list[dict]:
    """显式 none＋配偶 false 控制基础上，仅删除 claimant 关系记录的 kinship 键。"""
    relations: list[dict] = []
    if include_spouse:
        relations.append(_f5_fix2_spouse_relation(year, during=spouse_during))
    relations.append(_f5_fix3_missing_kinship_relation(year, "p1"))
    return relations


def _f5_fix3_no_allowed_dda(result: Mapping, label: str) -> None:
    allowed = [
        d
        for d in _decisions(result)
        if d.get("item_kind") == "disabled_dependant"
        and d.get("grant") == "allowed"
    ]
    assert not allowed, f"{label}：缺失/未定不得误授 DDA，实际：{allowed!r}"


def test_002_f5_s26d_bridge_missing_claimant_kinship_spouse_parent_false_needs_input() -> None:
    """补正三 (a)：显式 none＋配偶 parent false 控制基础上，仅删除 claimant
    关系记录的 `kinship` 键 → 缺失保持 None（005 §4.5:164、§6:468：键缺失
    ≠ false ≠ unknown）；直接关系 None∨s.26D false=None → needs_input/null，
    不得 partial/55,500。"""
    result = _f5_fix2_run(
        _f5_fix3_missing_relations(_F5_YEAR, spouse_during=False)
    )
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "缺失 kinship 必须给出定位追问"
    decision = _decision(result, claim_id="c1")
    assert decision is not None, "申索必须有 decision（不得静默丢弃）"
    assert decision.get("entitlement") == "unknown", decision
    assert decision.get("grant") == "unresolved", decision
    assert _unit_base(result, "salary:p1", "allowances") is None, (
        "缺失 kinship 不得产出确定 assessment 单位，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    _f5_fix3_no_allowed_dda(result, "薪俸：缺失 claimant kinship＋配偶 false")


def test_002_f5_s26d_bridge_missing_claimant_kinship_spouse_parent_false_pa_needs_input() -> None:
    """补正三 (b)：同构 PA 路径（pa_self 共用 resolve_claims）——缺失 claimant
    `kinship` 亦须 needs_input/null/追问，不得 partial/940。"""
    result = _f5_fix2_pa_run(
        _f5_fix3_missing_relations(_F5_YEAR, spouse_during=False)
    )
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "缺失 kinship 必须给出定位追问"
    assert _unit_base(result, "pa:p1", "allowances") is None, (
        "PA 缺失 kinship 不得产出确定 allowances 单位，实际："
        f"{_unit_base(result, 'pa:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") is None, (
        f"PA 未决不得产出配偶薪俸单位，实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )
    _f5_fix3_no_allowed_dda(result, "PA：缺失 claimant kinship＋配偶 false")


def test_002_f5_s26d_bridge_missing_claimant_kinship_no_payment_needs_input() -> None:
    """补正三 (c)：缺失 claimant `kinship`＋无付款（s.26D 替代 false）——
    None∨false=None，仍须 needs_input/null/追问，不得降为 ineligible。"""
    result = _f5_fix2_run(
        _f5_fix3_missing_relations(_F5_YEAR, spouse_during=False),
        care_payments=[],
    )
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "缺失 kinship 必须给出定位追问"
    decision = _decision(result, claim_id="c1")
    assert decision is not None, "申索必须有 decision（不得静默丢弃）"
    assert decision.get("entitlement") == "unknown", decision
    assert decision.get("grant") == "unresolved", decision
    assert _unit_base(result, "salary:p1", "allowances") is None, (
        f"未决不得产出确定 assessment 单位，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    _f5_fix3_no_allowed_dda(result, "薪俸：缺失 kinship＋无付款")


def test_002_f5_s26d_bridge_fix3_controls_retained() -> None:
    """补正三 (d) 控制（pre-P）：既有显式事实与缺失/unknown 口径全部保留。

    - 显式 none＋配偶 parent false：薪俸 ineligible/55,500；PA ineligible/940；
    - 显式 none＋配偶 parent true：allowed 75,000；final 42,750；
    - 无配偶关系（claimant 显式 none）：needs_input/null/追问；
    - 显式 unknown＋配偶 parent false：needs_input/null/追问。
    """
    salary_false = _f5_fix2_run(
        _f5_fix2_relations(_F5_YEAR, spouse_during=False)
    )
    _f5_fix2_assert_dda_ineligible(salary_false, "控制：薪俸显式 none＋配偶 false")
    assert _unit_base(salary_false, "salary:p1", "allowances") == "132000/1", (
        f"控制：DDA 不合资格仅基本免税额 132,000，实际："
        f"{_unit_base(salary_false, 'salary:p1', 'allowances')!r}"
    )
    assert salary_false["amounts"]["final_after_reduction"] == "55500", (
        salary_false["amounts"]
    )

    pa_false = _f5_fix2_pa_run(
        _f5_fix2_relations(_F5_YEAR, spouse_during=False)
    )
    _f5_fix2_assert_dda_ineligible(pa_false, "控制：PA 显式 none＋配偶 false")
    assert _unit_base(pa_false, "pa:p1", "allowances") == "132000/1", (
        f"控制：PA DDA 不合资格仅基本免税额 132,000，实际："
        f"{_unit_base(pa_false, 'pa:p1', 'allowances')!r}"
    )
    assert _unit_base(pa_false, "salary:p2", "allowances") == "132000/1", (
        f"控制：配偶薪俸单位基础额 132,000，实际："
        f"{_unit_base(pa_false, 'salary:p2', 'allowances')!r}"
    )
    assert pa_false["amounts"]["final_after_reduction"] == "940", (
        pa_false["amounts"]
    )

    allowed = _f5_fix2_run(_f5_fix2_relations(_F5_YEAR, spouse_during=True))
    _f5_assert_legal_control(allowed, "控制：显式 none＋配偶 parent true")

    no_spouse = _f5_fix2_run(
        _f5_fix2_relations(
            _F5_YEAR, spouse_during=False, include_spouse=False
        )
    )
    assert no_spouse["status"] == "needs_input", no_spouse["status"]
    assert no_spouse["amounts"]["final_after_reduction"] is None, (
        no_spouse["amounts"]
    )
    assert no_spouse.get("questions"), "无配偶关系：未决必须给出定位追问"

    unknown = _f5_fix2_run(
        [
            _f5_fix2_spouse_relation(_F5_YEAR, during=False),
            _f5_fix3_unknown_relation(_F5_YEAR, "p1"),
        ]
    )
    assert unknown["status"] == "needs_input", unknown["status"]
    assert unknown["amounts"]["final_after_reduction"] is None, unknown["amounts"]
    assert unknown.get("questions"), "显式 unknown：未决必须给出定位追问"
    unknown_decision = _decision(unknown, claim_id="c1")
    assert unknown_decision is not None, "显式 unknown 申索必须有 decision"
    assert unknown_decision.get("entitlement") == "unknown", unknown_decision
    assert unknown_decision.get("grant") == "unresolved", unknown_decision
    assert _unit_base(unknown, "salary:p1", "allowances") is None, (
        f"显式 unknown 不得产出确定 assessment 单位，实际："
        f"{_unit_base(unknown, 'salary:p1', 'allowances')!r}"
    )
    _f5_fix3_no_allowed_dda(unknown, "控制：显式 unknown＋配偶 false")


# ---------------------------------------------------------------------------
# P1 final fix（2026-10-17）：配偶替代路径/ERCE 消费者缺失 kinship 不得落 False
# ---------------------------------------------------------------------------


def _f5_fix4_spouse_missing_kinship(year: str) -> dict:
    """配偶 parent during=true 控制仅删除 `kinship` 键：缺失≠false（005:468）。"""
    relation = _f5_fix2_spouse_relation(year, during=True)
    relation.pop("kinship")
    return relation


def _f5_fix4_relations(year: str) -> list[dict]:
    """claimant 显式 none＋配偶 parent 已成立但 kinship 键缺失。"""
    return [
        _f5_fix4_spouse_missing_kinship(year),
        _f5_fix2_none_relation(year, "p1"),
    ]


def _f5_fix4_erce_run() -> dict:
    """ERCE 消费者同根反例：无 DDA claim，仅 ERCE 扣除意图；claimant p1 的
    `kinship` 键缺失、配偶 parent during=false。"""
    year = _F5_YEAR
    kwargs = _f5_year_kwargs(year, actor="p2")
    kwargs["relations"] = _f5_fix3_missing_relations(year, spouse_during=False)
    kwargs["claims"] = []
    kwargs["financials"]["p1"] = _fin(
        nai="600000",
        mpf="18000",
        ai=True,
        deduction_items=[
            {
                "item_type": "elder_residential_care",
                "amount": "80000",
                "payment_ids": ["e1"],
                "eligibility": {"value": True},
                "subject_id": "d1",
            }
        ],
    )
    return _run(
        _salary_input(
            year,
            [dict(item) for item in _F5_SUBJECTS],
            **kwargs,
        )
    )


def test_002_f5_s26d_bridge_missing_spouse_kinship_salary_needs_input() -> None:
    """P1 final fix (a)：claimant 显式 none＋配偶 parent 路径已成立，仅删除
    配偶关系记录的 `kinship` 键——解析后 kind=None 须视为未决（None），不得
    落入非父母分支 False；false∨None=None → needs_input/null/追问，不得
    partial/55,500。

    规格锚点：005 §4.5:164/§6:468（键缺失≠false≠unknown）＋§7:496
    （全 false 才 false，否则 unknown）。
    """
    control = _f5_fix2_run(_f5_fix2_relations(_F5_YEAR, spouse_during=True))
    _f5_assert_legal_control(control, "控制：显式 none＋配偶 parent true（kinship 完整）")
    assert control["amounts"]["final_after_reduction"] == "42750"

    result = _f5_fix2_run(_f5_fix4_relations(_F5_YEAR))
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "配偶 kinship 缺失必须给出定位追问"
    decision = _decision(result, claim_id="c1")
    assert decision is not None, "申索必须有 decision（不得静默丢弃）"
    assert decision.get("entitlement") == "unknown", decision
    assert decision.get("grant") == "unresolved", decision
    assert _unit_base(result, "salary:p1", "allowances") is None, (
        "配偶 kinship 缺失不得产出确定 assessment 单位，实际："
        f"{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    _f5_fix3_no_allowed_dda(result, "薪俸：缺失配偶 kinship")


def test_002_f5_s26d_bridge_missing_spouse_kinship_pa_needs_input() -> None:
    """P1 final fix (a/PA)：同一反例经 PA `pa_self` 共享 resolver 亦须
    needs_input/null/追问，不得 partial/940。"""
    result = _f5_fix2_pa_run(_f5_fix4_relations(_F5_YEAR))
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "PA 配偶 kinship 缺失必须给出定位追问"
    assert _unit_base(result, "pa:p1", "allowances") is None, (
        "PA 配偶 kinship 缺失不得产出确定 allowances 单位，实际："
        f"{_unit_base(result, 'pa:p1', 'allowances')!r}"
    )
    assert _unit_base(result, "salary:p2", "allowances") is None, (
        f"PA 未决不得产出配偶薪俸单位，实际："
        f"{_unit_base(result, 'salary:p2', 'allowances')!r}"
    )
    _f5_fix3_no_allowed_dda(result, "PA：缺失配偶 kinship")


def test_002_f5_erce_missing_claimant_kinship_needs_input() -> None:
    """P1 final fix (b)：ERCE 消费者（:2849–2895）共用 `s26d_payment_facts`
    → `relation_path`；claimant p1 的 `kinship` 键缺失不得被确定拒扣
    （RAW_FACTS_NOT_MET/ineligible），须未决（RAW_FACTS_UNKNOWN/unresolved）
    → needs_input/null/追问，不保存确定税额 55,500。

    规格锚点：005 §6.2:485–487（ERCE 须通过原始事实资格解析；缺失/unknown
    → needs_input）＋§6:468＋§7:496。
    """
    result = _f5_fix4_erce_run()
    assert result["status"] == "needs_input", result["status"]
    assert result["amounts"]["final_after_reduction"] is None, result["amounts"]
    assert result.get("questions"), "ERCE 缺失 kinship 必须给出定位追问"
    erce = [
        decision
        for decision in _decisions(result)
        if decision.get("item_kind") == "elder_residential_care"
    ]
    assert erce, "ERCE 扣除必须有 decision（不得静默丢弃）"
    assert erce[0].get("entitlement") == "unknown", erce
    assert erce[0].get("grant") == "unresolved", erce
    assert erce[0].get("reason_codes") == ["RAW_FACTS_UNKNOWN"], erce
    assert not any(
        decision.get("grant") == "allowed" for decision in erce
    ), f"缺失 kinship 不得确定授予 ERCE，实际：{erce!r}"
