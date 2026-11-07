# -*- coding: utf-8 -*-
"""REQ-3 子女免税额三替代路径与出生额外额（002 P1 Red；Annex 005 事实契约）。

REQ: REQ-3（S002 §8/§9.4；PRD AC-3）。本文件为 SDD §9.4 REQ-3 命名节点，共 7 个：
  - test_002_child_under18_no_residence_condition
  - test_002_child_18_25_education_tf_window
  - test_002_child_18_disabled_incapacity_alternative_path
  - test_002_child_caps_separate_three_groups_2026_27
  - test_002_child_1b_verified_not_applicable_2024_25_2025_26
  - test_002_twins_same_birth_two_ids_legal
  - test_002_newborn_part_year_samepoint

规格锚点:
  - S002 §3.2/§5.2（child：SAMEPOINT(关系＋存活＋未婚＋claimant 维护＋年龄路径)；
    三路径替代；无一般居港条件；三层金额独立 cap）；
  - Annex 005 §7（s.31 行：三替代路径、缺无关替代路径不阻塞；s.31(1A)/(1B)
    行：独立金额＋独立 cap；not_applicable_verified 才计 0）；§15.2（child_base
    130,000/140,000、(1A) 同额、(1B) 0/140,000；cap 1,170,000／1,260,000）；
  - Annex 005 §3（不以生日识人；同生日不同 ID 合法）；§4.1/§4.3/§4.5/§4.9；
    §5.1（出生年部分年度示例）；§14（steps/Exact；assessment bases）；
    §2.1（v2 引擎签名 availability=）。

期望值来源（禁从生产代码反推）:
  - 免税额/上限：Annex 005 §15.2（转引附表 4，T1）。
  - 税率带：附表 1/2（Annex D D7；T1 台账 1.17）。
  - 无额外免税额基线 final 940：PAM39 Q1（T2 台账 1.13）。
  - 其余组合数值：T3 独立手算（算式见各测试注释）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - calculate_salaries_tax(facts, bundle, *, availability=None)（005 §2.1）：
    v2 subject/year 事实模型；child 资格＝SAMEPOINT 三替代路径，**无居港条件**
    （不得因缺 ordinarily_resident_hk 追问）；(1A)/(1B) 按 birth_date 与键年
    推导、金额独立 cap；2024/25–2025/26 的 (1B) 为 not_applicable_verified
    （计 0＋理由，非 E_RULES_STATE_UNVERIFIABLE）；steps decision/assessment
    按 §14 tagged 形状输出。
  - 本文件对 app.* 延迟导入（测试体内）。
"""

from __future__ import annotations

import copy
import hashlib
import json
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


def _subjects_with(children: list[tuple[str, str]]) -> list[dict]:
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    subjects.extend({"subject_id": sid, "birth_date": birth} for sid, birth in children)
    return subjects


# ---------------------------------------------------------------------------
# REQ-3 命名节点（S002 §9.4）
# ---------------------------------------------------------------------------


def test_002_child_under18_no_residence_condition() -> None:
    """未成年子女：不提交任何居港事实 → 照常计额，不得出现居港追问。

    规格锚点：S002 §3.2（child 无一般居港条件；删除旧断言）；Annex 005 §7
    （s.31 行无居港谓词）。
    T3：130,000 计入后 NCI=240,000−9,000−132,000−130,000=−31,000 → 0；
    标准 231,000×15%=34,650 → 择低 0；宽减 0 → final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "2015-06-01")]),
        subject_facts={"d1": {"unmarried": _tf(year, True)}},  # 无 ordinarily_resident_hk
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child")],
    ))
    assert result["status"] == "partial", (
        f"未成年＋未婚＋受供养已足额（s.31 无居港条件），实际 {result['status']!r}"
    )
    assert not (result.get("questions") or []), (
        f"s.31 无一般居港条件，不得追问居港（REQ-3 删除旧断言），实际：{result.get('questions')!r}"
    )
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"未成年子女须 eligible，实际：{decision!r}"
    )
    assert decision.get("amount") == "130000", decision
    assert _unit_base(result, "salary:p1", "allowances") == "262000/1", (
        "Part 5 合计＝基本 132,000＋子女 130,000（005 §14 assessment bases），"
        f"实际：{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "0", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_child_18_25_education_tf_window() -> None:
    """18–<25 全日制教育路径按 TF 窗口与年龄交集；过 25 岁生日后教育不算数。

    规格锚点：Annex 005 §7（三替代路径；(18≤age<25 AND full_time_education)；
    教育过 25 岁生日的窗口按实际交集求值，不只用年度末年龄）。
    """
    year = "2025_26"
    subjects = _subjects_with([("d1", "2000-07-15")])  # 2025-07-15 满 25 岁
    base_relation = _relation(year, "r1", "p1", "d1", maintained=_tf(year, True))

    # (a) 教育窗口落在 <25 岁期间（部分年度即可，SAMEPOINT=ANY）→ 资格成立。
    in_window = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {
            "unmarried": _tf(year, True),
            "full_time_education": [{"from": "2025-04-01", "to": "2025-07-15", "value": True}],
        }},
        relations=[base_relation],
        claims=[_claim("c1", "child")],
    ))
    decision = _decision(in_window, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", (
        f"25 岁生日前的全日制教育窗口足以成立资格，实际：{decision!r}"
    )
    assert decision.get("amount") == "130000", decision
    assert not (in_window.get("questions") or [])

    # (b) 教育仅发生在 25 岁生日之后＋incapacitated 明确 false → 三路径均不成立。
    after_window = _run(_salary_input(
        year, subjects,
        subject_facts={"d1": {
            "unmarried": _tf(year, True),
            "full_time_education": [
                {"from": "2025-04-01", "to": "2025-08-01", "value": False},
                {"from": "2025-08-01", "to": "2026-04-01", "value": True},
            ],
            "incapacitated_for_work": _tf(year, False),
        }},
        relations=[base_relation],
        claims=[_claim("c1", "child")],
    ))
    decision = _decision(after_window, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        "满 25 岁后的教育不满足 (18≤age<25 AND 教育)；未成年路径与伤残路径亦不成立 →"
        f" ineligible，实际：{decision!r}"
    )
    assert not (after_window.get("questions") or [])
    assert after_window["amounts"]["final_after_reduction"] == "940", after_window["amounts"]


def test_002_child_18_disabled_incapacity_alternative_path() -> None:
    """18+ 因伤残不能工作（替代路径）：无需教育事实即可成立。

    规格锚点：Annex 005 §7（OR(paths)：缺无关替代路径不阻塞 true）。
    T3：NCI=−31,000 → 0；final 0。
    """
    year = "2025_26"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", "2003-02-01")]),
        subject_facts={"d1": {
            "unmarried": _tf(year, True),
            "incapacitated_for_work": _tf(year, True),
        }},
        relations=[_relation(year, "r1", "p1", "d1", maintained=_tf(year, True))],
        claims=[_claim("c1", "child")],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), (
        "伤残不能工作路径已成立，缺教育事实不阻塞（005 §7 OR 语义）"
    )
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", decision
    assert decision.get("amount") == "130000", decision
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


def test_002_child_caps_separate_three_groups_2026_27() -> None:
    """2026/27 三层金额（base/(1A)/(1B)）独立 cap：合计不得被单一上限截断。

    规格锚点：S002 §5.2（min(Σbase,cap)+min(Σ(1A),cap)+min(Σ(1B),cap)）；
    Annex 005 §15.2（2026_27 三组各 cap 1,260,000）。
    构造（≤9 名合格子女申索，C2.9 上限内）：5 名 2026/26 窗口内出生（(1A) 组）＋
    4 名上一课税年度出生（(1B) 组）。
    T3：base 组 Σ=9×140,000=1,260,000（恰触 cap）；(1A) Σ=5×140,000=700,000；
    (1B) Σ=4×140,000=560,000；Part 5 合计=145,000+1,260,000+700,000+560,000
    =2,665,000。若错误地对三组合用单一 cap 1,260,000，合计只会是 1,405,000。
    """
    year = "2026_27"
    children = [(f"d{i}", "2026-06-01", "") for i in range(1, 6)]
    children += [(f"d{i}", "2025-08-15", "") for i in range(6, 10)]
    result = _run(_salary_input(
        year,
        _subjects_with([(sid, birth) for sid, birth, _ in children]),
        **{
            "subject_facts": {
                sid: {"unmarried": _tf(year, True)} for sid, _, _ in children
            },
            "relations": [
                _relation(year, f"r-{sid}", "p1", sid, maintained=_tf(year, True))
                for sid, _, _ in children
            ],
            "claims": [_claim(f"c-{sid}", "child", subject=sid) for sid, _, _ in children],
        },
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    for sid, _, _ in children:
        decision = _decision(result, subject_id=sid)
        assert decision is not None and decision.get("entitlement") == "eligible", (
            f"子女 {sid} 须 eligible，实际：{decision!r}"
        )
    assert _unit_base(result, "salary:p1", "allowances") == "2665000/1", (
        "三组独立 cap：Part 5 合计须为 145,000+1,260,000+700,000+560,000=2,665,000，"
        f"实际：{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def test_002_child_1b_verified_not_applicable_2024_25_2025_26() -> None:
    """(1B) 出生次年额外额在 2024/25–2025/26 已核不适用：计 0＋可继续，非拒算。

    规格锚点：Annex 005 §7（s.31(1A)/(1B) 行）＋§15.2（前两年度 (1B)
    not_applicable_verified、amount 0）。
    T3：2024/25 出生年 → 130,000+130,000（(1A) 适用）；2025/26 次年 → 仅
    130,000（(1B)=0）；Part 5 合计分别为 392,000 / 262,000。
    """
    born = "2024-08-01"  # 出生课税年度＝2024/25
    # (a) 2024/25（出生年）：base+(1A)。
    y1 = "2024_25"
    r1 = _run(_salary_input(
        y1,
        _subjects_with([("d1", born)]),
        subject_facts={"d1": {"unmarried": [
            {"from": born, "to": _window(y1)[1], "value": True}
        ]}},
        relations=[_relation(y1, "r1", "p1", "d1", maintained=[
            {"from": born, "to": _window(y1)[1], "value": True}
        ])],
        claims=[_claim("c1", "child")],
    ))
    assert r1["status"] == "partial", r1["status"]
    assert not (r1.get("questions") or [])
    assert _unit_base(r1, "salary:p1", "allowances") == "392000/1", (
        f"2024/25 出生年：132,000+130,000+130,000=392,000，"
        f"实际：{_unit_base(r1, 'salary:p1', 'allowances')!r}"
    )

    # (b) 2025/26（出生次年）：仅 base（(1B) not_applicable_verified → 0）。
    y2 = "2025_26"
    r2 = _run(_salary_input(
        y2,
        _subjects_with([("d1", born)]),
        subject_facts={"d1": {"unmarried": _tf(y2, True)}},
        relations=[_relation(y2, "r1", "p1", "d1", maintained=_tf(y2, True))],
        claims=[_claim("c1", "child")],
    ))
    assert r2["status"] == "partial", (
        f"(1B) 已核不适用＝计 0＋理由，不是 E_RULES_STATE_UNVERIFIABLE，"
        f"实际 {r2['status']!r}"
    )
    assert not (r2.get("questions") or [])
    decision = _decision(r2, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", decision
    assert _unit_base(r2, "salary:p1", "allowances") == "262000/1", (
        f"2025/26 出生次年：(1B)=0 → 132,000+130,000=262,000，"
        f"实际：{_unit_base(r2, 'salary:p1', 'allowances')!r}"
    )


def test_002_twins_same_birth_two_ids_legal() -> None:
    """同生日、不同 subject_id 的双胞胎合法（不以生日识人）。

    规格锚点：Annex 005 §3（同 ID 重复才矛盾；同生日不同 ID 合法）。
    T3：2026/27 双胞胎各 140,000+140,000 → Part 5=145,000+560,000=705,000。
    """
    year = "2026_27"
    twins = [("d1", "2026-06-01"), ("d2", "2026-06-01")]
    result = _run(_salary_input(
        year,
        _subjects_with(twins),
        subject_facts={sid: {"unmarried": _tf(year, True)} for sid, _ in twins},
        relations=[_relation(year, f"r-{sid}", "p1", sid, maintained=_tf(year, True))
                   for sid, _ in twins],
        claims=[_claim(f"c-{sid}", "child", subject=sid) for sid, _ in twins],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or [])
    for sid, _ in twins:
        decision = _decision(result, subject_id=sid)
        assert decision is not None and decision.get("entitlement") == "eligible", (
            f"双胞胎 {sid} 须 eligible（同生日不同 ID 合法），实际：{decision!r}"
        )
    assert _unit_base(result, "salary:p1", "allowances") == "705000/1", (
        f"双胞胎各 base+(1A)：145,000+2×(140,000+140,000)=705,000，"
        f"实际：{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )


def test_002_newborn_part_year_samepoint() -> None:
    """年内出生新生儿：未婚/受供养自出生日起的部分年度事实足以成立同点资格。

    规格锚点：Annex 005 §5.1（出生年部分年度示例）＋§7（生日之前不要求覆盖）。
    T3：base+(1A)=260,000 → Part 5=132,000+260,000=392,000；NCI<0 → final 0。
    """
    year = "2025_26"
    birth = "2025-12-01"
    result = _run(_salary_input(
        year,
        _subjects_with([("d1", birth)]),
        subject_facts={"d1": {"unmarried": [
            {"from": birth, "to": _window(year)[1], "value": True}
        ]}},
        relations=[_relation(year, "r1", "p1", "d1", maintained=[
            {"from": birth, "to": _window(year)[1], "value": True}
        ])],
        claims=[_claim("c1", "child")],
    ))
    assert result["status"] == "partial", result["status"]
    assert not (result.get("questions") or []), (
        "出生日前的未婚/供养无需覆盖（005 §7）；不得追问"
    )
    decision = _decision(result, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "eligible", decision
    assert _unit_base(result, "salary:p1", "allowances") == "392000/1", (
        f"出生年：base 130,000＋(1A) 130,000 → 132,000+260,000=392,000，"
        f"实际：{_unit_base(result, 'salary:p1', 'allowances')!r}"
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "0", amounts
    assert amounts["final_after_reduction"] == "0", amounts


def test_002_child_unverified_kinship_basis_manual_and_during_false_ineligible() -> None:
    """P1 r1 新 Red（复核 §六.8）：未核亲属组合（basis=other）→ 窄 manual
    （可提交、可定位）；关系期间显式 false → 关系无效 → ineligible（守恒）。

    规格锚点：Annex 005 §4.5（普通已核映射外组合可提交但定位 manual，
    不整类禁算）＋§7（s.31 SAMEPOINT 含「关系有效」）。
    复核 §三 R5（未核 basis/during 未核，观察 basis=other 仍 eligible）。
    """
    year = "2025_26"
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "d1", "birth_date": "2015-06-01"}]
    unmarried = {"d1": {"unmarried": _tf(year, True)}}
    claim = [{"claim_id": "c1", "kind": "child",
              "claimant_id": "p1", "subject_id": "d1"}]

    unverified = _run(_salary_input(
        year, subjects,
        subject_facts=unmarried,
        relations=[{
            "relation_id": "r1", "actor_id": "p1", "subject_id": "d1",
            "kinship": {"kind": "child", "basis": "other",
                        "during": _tf(year, True)},
            "maintained": _tf(year, True),
        }],
        claims=claim,
    ))
    decision = _decision(unverified, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "manual", (
        f"child＋basis=other 不在普通已核映射 → 窄 manual（不整类禁算），"
        f"实际：{decision!r}"
    )

    during_false = _run(_salary_input(
        year, subjects,
        subject_facts=unmarried,
        relations=[{
            "relation_id": "r1", "actor_id": "p1", "subject_id": "d1",
            "kinship": {"kind": "child", "basis": "birth",
                        "during": _tf(year, False)},
            "maintained": _tf(year, True),
        }],
        claims=claim,
    ))
    decision = _decision(during_false, claim_id="c1")
    assert decision is not None and decision.get("entitlement") == "ineligible", (
        f"关系期间显式 false → 关系无效 → SAMEPOINT=false → ineligible，"
        f"实际：{decision!r}"
    )


# ---------------------------------------------------------------------------
# B lane 补齐（p1-r2-red-completion-plan §B）：三层 cap 分配须与 decision 对账
# ---------------------------------------------------------------------------


def test_002_child_cap_allocation_grant_amounts_reconcile() -> None:
    """E7/R7（B lane）：三层独立 cap 分配结果须回写 decision 金额并与有效额度一致。

    隔离合成矩阵控制（**非官方字节、非新法定金额**）：正常束＋Facts 解析两名
    合法子女；仅深拷贝本单元使用的矩阵并把 2025/26 `child_base.cap` 改为
    150000，经既有 `salaries._compute_separate` → `allowances.resolve_claims`
    路径实测（cap 分配接线，005 §7 s.31(5)：min(Σbase,cap)）。两名 base=130,000
    的子女 → decision 130,000／20,000、holder 合计恰为 150,000；worksheet＝
    basic 132,000＋holder 150,000=282,000。合成值不注入整束（不经 admission/
    摘要校验），也不主张其为法定金额。
    """
    from app.engines.allowances import Facts
    from app.engines.salaries import _YearSelection

    year = "2025_26"
    bundle = _v2_bundle()
    children = [("d1", "2015-06-01"), ("d2", "2015-08-20")]
    v2_input = _salary_input(
        year,
        _subjects_with(children),
        **{
            "subject_facts": {sid: {"unmarried": _tf(year, True)} for sid, _ in children},
            "relations": [
                _relation(year, f"r-{sid}", "p1", sid, maintained=_tf(year, True))
                for sid, _ in children
            ],
            "claims": [_claim("c1", "child", subject="d1"),
                       _claim("c2", "child", subject="d2")],
        },
    )
    facts_ctx = Facts(v2_input, bundle)
    selection = _YearSelection(facts_ctx.bundle, facts_ctx.y, facts_ctx.subjects)
    selection.compute()
    cloned_matrix = copy.deepcopy(selection.matrix)
    cloned_matrix["child_base"]["cap"] = "150000"
    selection.matrix = cloned_matrix
    selection._separate_cache = None  # 以克隆矩阵重解析该单元（真实 _compute_separate 路径）
    resolved = selection._compute_separate()

    assert resolved["unresolved"] is None, resolved["unresolved"]
    first = next(d for d in resolved["decisions"] if d.get("claim_id") == "c1")
    second = next(d for d in resolved["decisions"] if d.get("claim_id") == "c2")
    assert first.get("grant") == "allowed", first
    assert second.get("grant") == "allowed", second
    assert first.get("amount") == "130000", (
        f"第一序位子女占满 cap=130,000，实际：{first!r}"
    )
    assert second.get("amount") == "20000", (
        f"cap 分配后第二条须按剩余额度回写 20,000（不得仍报 130,000），实际：{second!r}"
    )
    assert resolved["holder_amounts"].get("p1") == 150000, resolved["holder_amounts"]
    basic = next(
        d for d in resolved["decisions"] if d.get("item_kind") == "basic"
    )
    assert int(basic["amount"]) + resolved["holder_amounts"]["p1"] == 282000, (
        basic, resolved["holder_amounts"],
    )
