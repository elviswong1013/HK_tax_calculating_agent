# -*- coding: utf-8 -*-
"""REQ-2/6/7/8/10 个人入息课税比较引擎（v2 迁移版；Annex D D6 权威）。

本文件为旧 v1 引擎测试按已批准 dispositions 的 v2 迁移（006 §4.2 逐行；
S002 §9.5）。v1 数值/不变量全部保留；事实模型迁 v2（005 §3 subject/year
＋§2.1 availability 显式信封）；方案输出迁 tagged steps（§14 scenario/
assessment/selection；无顶层 scenarios 列表）。

disposition 映射（006 §4.2 / S002 §9.5）：
  - test_pa_q32_example_9920_vs_28800   适配；complete→partial（PA 恒 partial）；
    配偶无薪俸 AI 边界须显式事实 → REQ-8/10
  - test_pa_interest_cap_per_property_nav_share  保留（数值/状态迁 v2）→ REQ-8
  - test_pa_standard_cap_pa_reduced_income_not_salary_reuse  保留 → REQ-8
  - test_pa_compare_after_rebate_not_before  宽减后比较保留；「缺 paid 无
    best」替换为 best 独立于 paid → REQ-10
  - test_pa_duplicate_claim_blocked  重复拒保留；扩展 ledger 矩阵 → REQ-6
  - test_pa_unknown_qualification_no_optimal_claim  适配 Tri/TF → REQ-2
  - test_pa_joint_output_total_authoritative_no_per_person_whole  保留；
    零收入改合法来源存在事实（zero-reduced）→ REQ-7/8
  - test_missing_pst_partial_no_total_balance  适配 REQ-9/10 → REQ-9/10
  - test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions
    保留（数值＋内部精确比例）→ REQ-8

P1 r1 追加纠错（复核 §五.4/§五.5，2026-10-14 授权）：
  - availability.bundle_hash：占位 "test" → 实际完整束六字段 canonical
    内容 hash（app.core.hashing.bundle_content_hash；C2.8）。
  - 旧迁移 ID：物业 p-1/p-a/p-b → h-1/h-a/h-b（005 §3 hID 前缀；单元
    locator 同步 property:h-*:*）。

规格锚点:
  - 005 §2/§2.1（AvailabilityContext）、§3/§4/§5（v2 input）、§8（ledger
    矩阵）、§9.2/§9.3（P1–P5＋候选）、§10（PA 恒 partial、balance null）、
    §14（tagged steps：scenario/assessment（bases/shares Exact）/selection）。
  - Annex D D6.0–D6.3（T1/T2）；附表 1/2/43（T1，台账 1.17）；Q32/Q33
    （T1，台账 1.7）；PAM37 例 4（T2，台账 1.13）。

期望值来源（禁从生产代码反推）: 数值断言与 v1 版一致（T1/T2/T3 手算，
算式见各测试注释）；v2 状态/形状断言来自 005 §10/§14。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from app.core.errors import AppError

_METHOD_PA = "pa_joint"   # v2：已婚共同选择 PA 的候选 ID（005 §9.3）
_METHOD_NO_PA = "no_pa"

_AMOUNT_KEYS = {
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
}


def _calc():
    """延迟导入被测引擎与生产 v2 规则束。"""
    from app.engines.personal_assessment import compare_personal_assessment
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return compare_personal_assessment, INITIAL_BUNDLE_CONTENT


def _bundle_hash(bundle: Mapping) -> str:
    """实际完整束六字段 canonical 内容 hash（C2.8；复核 §五.4 纠错）。"""
    from app.core.hashing import bundle_content_hash  # 延迟导入

    return bundle_content_hash(bundle)


def _availability(bundle: Mapping, year: str) -> dict:
    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    years = bundle["data"]["allowances"]
    return {
        "binding": {
            "bundle_id": "test",
            # P1 r1 纠错（复核 §五.4）：hash 占位 → 实际完整束六字段 canonical
            # 内容 hash（C2.8；束变更后随内容重算，不豁免 verifier）。
            "bundle_hash": _bundle_hash(bundle),
            "rules_schema_version": bundle["rules_schema_version"],
            "engine_version": "0.2.0",
        },
        "current": {"year": year, "status": "available", "reason_codes": []},
        "next": {
            "year": following,
            "status": "available" if following in years else "missing_rules",
            "reason_codes": [],
        },
    }


def _run(v2_input: dict):
    compare, bundle = _calc()
    return compare(
        v2_input, bundle, availability=_availability(bundle, v2_input["year_of_assessment"])
    )


def _property(pid: str, rent: str, *, share: str = "1/1", interest: str = "0") -> dict:
    """单一物业事实（差饷两条件显式为零；份额默认全权拥有；v2 原始 Tri）。"""
    return {
        "property_id": pid,
        "share": share,
        "rent_received": rent,
        "rates_paid_by_owner": {"amount": "0", "agreed": False, "actually_paid": False},
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
        "mortgage_interest": interest,
    }


def _fin(*, nai: str = "0", mpf: str = "0", ai: Any = False, **over: Any) -> dict:
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


def _resident_at_reference() -> list[dict]:
    """P1 居港肢：参考日一天区间（005 §5.2 示例形状）。"""
    return [{"from": "2026-06-01", "to": "2026-06-02", "value": True}]


def _household(year: str, *, married: bool, nomination: dict | None = None,
               reference: str | None = None) -> dict:
    start = int(year[:4])
    w = (f"{start}-04-01", f"{start + 1}-04-01")
    if not married:
        return {
            "marriage": [{"from": w[0], "to": w[1], "value": "unmarried"}],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        }
    marriage = [{"from": w[0], "to": w[1], "value": "married", "spouse_id": "p2"}]
    apart = [{"from": w[0], "to": w[1], "value": False}]
    # P1 r1 定点纠错（编排者执行，2026-10-15）：joint PA 候选的婚姻/非分居按
    # 候选有效日（hypothetical＝参考日）求值（005 §9.2 P2）；参考日在 YA 窗口
    # 之外，按 §7 一天区间 synthetic control 显式补覆盖（同 _s6_household 形态）。
    if reference is not None:
        from datetime import date, timedelta

        ref_next = (date.fromisoformat(reference) + timedelta(days=1)).isoformat()
        marriage.append({"from": reference, "to": ref_next,
                         "value": "married", "spouse_id": "p2"})
        apart.append({"from": reference, "to": ref_next, "value": False})
    return {
        "marriage": marriage,
        "living_apart": apart,
        "child_nomination": nomination or {"person_id": None, "status": "none"},
    }


def _year_obj(
    year: str,
    *,
    participants: list,
    subject_facts: dict,
    financials: dict,
    assessment: dict,
    household: dict,
    relations: list | None = None,
    claims: list | None = None,
    provisional_paid: str | None = "0",
) -> dict:
    y: dict[str, Any] = {
        "participants": participants,
        "subject_facts": subject_facts,
        "household": household,
        "relations": relations or [],
        "financials": financials,
        "care_payments": [],
        "claims": claims or [],
        "assessment": assessment,
        "manual_issues": [],
    }
    if provisional_paid is not None:
        y["provisional_paid"] = provisional_paid
    return y


def _input(year: str, subjects: list, y: dict) -> dict:
    return {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": {year: y},
        "hk_presence_days": [],
        "assumptions": [],
    }


_SINGLE_SUB = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
_COUPLE_SUB = [{"subject_id": "p1", "birth_date": "1990-01-01"},
               {"subject_id": "p2", "birth_date": "1992-02-02"}]
_SELF = [{"person_id": "p1", "role": "self"}]
_COUPLE = [{"person_id": "p1", "role": "self"}, {"person_id": "p2", "role": "spouse"}]


def _assert_amounts_all_null(result: dict) -> None:
    """needs_input/blocked：六个金额键一律显式 None（C3.3）。"""
    assert set(result["amounts"].keys()) == _AMOUNT_KEYS, (
        f"amounts 必须恰含 C3.4 六键，实际：{sorted(result['amounts'])!r}"
    )
    assert all(v is None for v in result["amounts"].values()), (
        f"不得输出任何金额（资格不明不填零），实际：{result['amounts']!r}"
    )


def _assert_chinese_questions(result: dict) -> None:
    questions = result.get("questions")
    assert isinstance(questions, list) and questions, (
        "needs_input 必须携带非空 questions[]"
    )
    for q in questions:
        assert isinstance(q, Mapping), (
            f"questions[] 项必须为结构化定位项（005 §13），实际：{q!r}"
        )
        text = q.get("question_zh")
        assert isinstance(text, str) and text, f"question_zh 必须为非空串，实际：{q!r}"
        assert re.search(r"[\u4e00-\u9fff]", text), (
            f"问题清单必须为中文，实际：{text!r}"
        )


def _steps(result: dict, kind: str) -> list[dict]:
    return [
        s.get("detail") or {}
        for s in (result.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == kind and isinstance(s.get("detail"), dict)
    ]


def _scenario(result: dict, scenario_id: str) -> dict:
    """按 scenario_id 取 tagged scenario step；缺失即失败（候选枚举是契约）。"""
    for detail in _steps(result, "scenario"):
        if detail.get("scenario_id") == scenario_id:
            return detail
    raise AssertionError(
        f"比较结果缺少方案 scenario {scenario_id!r}，实际："
        f"{[d.get('scenario_id') for d in _steps(result, 'scenario')]!r}"
    )


def _assessment(result: dict, unit_id: str, *, scenario_id: str | None = None) -> dict:
    for detail in _steps(result, "assessment"):
        if detail.get("unit_id") == unit_id and (
            scenario_id is None or detail.get("scenario_id") == scenario_id
        ):
            return detail
    raise AssertionError(
        f"steps 缺少评税单元 {unit_id!r}（scenario={scenario_id!r}），实际单元："
        f"{[d.get('unit_id') for d in _steps(result, 'assessment')]!r}"
    )


def _shares_by_person(assessment_detail: dict) -> dict:
    return {
        s.get("person_id"): s.get("ratio")
        for s in (assessment_detail.get("shares") or [])
        if isinstance(s, dict)
    }


def test_pa_q32_example_9920_vs_28800() -> None:
    """Q32（2025/26 官方完整示例，T1）：PA 9,920 vs 分开评税 28,800＋0。

    夫妻事实整体保留（不得拆成独立单项 fixture）：本人薪金 250,000、配偶
    持有物业租金 240,000（差饷 0）→ NAV=192,000；v2 下配偶无薪俸 AI 边界
    为显式事实（has_assessable_salary_income=false＋全来源存在）。
    v2 迁移（006 §4.2 行 6）：数值/best 保留；complete→partial（PA 恒
    partial，005 §10）；方案输出迁 tagged scenario steps。
    """
    y = "2025_26"
    result = _run(_input(y, _COUPLE_SUB, _year_obj(
        y,
        participants=_COUPLE,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="250000", mpf="0", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(ai=False,
                       has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-1", "240000")]),
        },
        household=_household(y, married=True, reference="2026-06-01"),  # P1 r1 定点纠错（编排者执行，2026-10-15）：补参考日婚姻覆盖（005 §9.2 P2）
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, _METHOD_PA], "selection": "best"},
    )))

    assert result["status"] == "partial", (
        f"PA 本阶段恒 partial（005 §10；v1 断言 complete 已按处置改写），"
        f"实际 {result['status']!r}"
    )
    # 手算（Q32＋附表 2/43，T1；v1 版同式保留）：
    #   PA（共同选择，s.42A 合并；一份已婚免税额覆盖两人）：
    #     合并总入息 R = 250,000＋192,000 = 442,000；NCI = 442,000−264,000
    #     = 178,000 → 累进 12,920；标准 66,300 → 12,920；宽减 3,000 → 9,920
    #   分开评税（no_pa）：本人薪俸税 0（NCI=max(0,250,000−264,000)=0）；
    #     配偶物业税 = floor(192,000×15%) = 28,800（无宽减）→ 合计 28,800
    pa = _scenario(result, _METHOD_PA)
    assert pa["amounts"]["before_reduction"] == "12920", pa
    assert pa["amounts"]["reduction"] == "3000", pa
    assert pa["amounts"]["final_after_reduction"] == "9920", pa

    no_pa = _scenario(result, _METHOD_NO_PA)
    assert no_pa["amounts"]["final_after_reduction"] == "28800", no_pa
    # v1 components{tax_type: final} 不变量迁 v2 单元断言：薪俸 0＋物业 28,800。
    assert _assessment(result, "salary:p1", scenario_id=_METHOD_NO_PA)["amounts"][
        "final_after_reduction"
    ] == "0"
    assert _assessment(result, "property:h-1:p2", scenario_id=_METHOD_NO_PA)["amounts"][
        "final_after_reduction"
    ] == "28800"

    assert result["best_scenario"] == _METHOD_PA, result
    assert result["amounts"]["final_after_reduction"] == "9920", result["amounts"]


def test_pa_interest_cap_per_property_nav_share() -> None:
    """Q33（T1）：利息逐物业 min(利息, 该人该物业 NAV 份额)；非全局池。

    v2 迁移：v1 的 persons[].properties[].interest_deducted 在 005 §14
    bases 白名单（nav/nav_share/…）中无对应字段——不变量迁经
    （1）no_pa 物业单元 bases nav_share；（2）pa:p1 的 standard_base=R。
    手算（T3；s.42(1) 但书＋Q33；v1 版同式保留）：
      物业 A：租金 200,000 → NAV 160,000；利息 200,000 > NAV 份额 → 扣 160,000
      物业 B：租金 100,000 → NAV 80,000；利息 10,000 ≤ NAV 份额 → 扣 10,000
      R = 0(薪金) ＋ (160,000−160,000) ＋ (80,000−10,000) = 70,000
      （错误全局池：min(210,000, 240,000)=210,000 → R=30,000，可区分。）
    """
    y = "2025_26"
    result = _run(_input(y, _SINGLE_SUB, _year_obj(
        y,
        participants=_SELF,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(ai=False,
                       has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-a", "200000", interest="200000"),
                                   _property("h-b", "100000", interest="10000")]),
        },
        household=_household(y, married=False),
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, "pa_self"], "selection": "best"},
    )))

    # 逐物业 NAV 份额（Q33）：A 160,000；B 80,000。
    for pid, nav_share in (("h-a", "160000/1"), ("h-b", "80000/1")):
        bases = {
            b.get("name"): b.get("value")
            for b in _assessment(result, f"property:{pid}:p1",
                                 scenario_id=_METHOD_NO_PA)["bases"]
        }
        assert bases.get("nav_share") == nav_share, (pid, bases)
    # R（减少后总入息）＝240,000−170,000＝70,000（全局池错误路径＝30,000）。
    bases = {
        b.get("name"): b.get("value")
        for b in _assessment(result, "pa:p1", scenario_id="pa_self")["bases"]
    }
    assert bases.get("standard_base") == "70000/1", bases
    # 免税额 132,000 > R → PA 税 0（标准径 70,000×15%=10,500 → 取 0）。
    assert _scenario(result, "pa_self")["amounts"]["final_after_reduction"] == "0"


def test_pa_standard_cap_pa_reduced_income_not_salary_reuse() -> None:
    """s.43(1A)：标准上限基数＝PA 减少后总入息 R（含物业/业务成分），非薪俸净收入。

    手算（T3；附表 1 两级标准＋附表 43，T1；v1 版同式保留）：
      R = 200,000(薪金) ＋ 11,760,000×80%(=9,408,000) = 9,608,000
      累进 = (9,608,000−132,000=9,476,000) → 16,000＋9,276,000×17% = 1,592,920
      标准（对 R 两级）= 5,000,000×15% ＋ 4,608,000×16% = 1,487,280 → 上限绑定
      宽减 3,000 → final = 1,484,280
      （错误复用薪俸口径：200,000×15%=30,000 当上限 → final 27,000，可区分。）
    """
    y = "2025_26"
    result = _run(_input(y, _SINGLE_SUB, _year_obj(
        y,
        participants=_SELF,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="200000", mpf="0", ai=True,
                       has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-1", "11760000")]),
        },
        household=_household(y, married=False),
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, "pa_self"], "selection": "best"},
    )))

    pa = _scenario(result, "pa_self")
    assert pa["amounts"]["before_reduction"] == "1487280", pa
    assert pa["amounts"]["reduction"] == "3000", pa
    assert pa["amounts"]["final_after_reduction"] == "1484280", pa


def test_pa_compare_after_rebate_not_before() -> None:
    """s.100(4)：各方案在每项宽减之后比较；best 独立于 paid（缺 paid 仍有 best）。

    v2 迁移（006 §4.2 行 7/S002 §9.5）：宽减后比较保留；v1「缺 paid 无
    best」段替换为 best 独立于 paid（005 §10：best 仅依候选本年 final＋资格）。
    手算（T3；附表 43：PA/薪俸各一个 cap、物业税无宽减；v1 版同式保留）：
      PA：R = 150,000＋1,000,000×80%(=800,000) = 950,000
        累进 = (950,000−132,000=818,000) → 16,000＋618,000×17% = 121,060
        标准 = 950,000×15% = 142,500 → 121,060；宽减 3,000 → **118,060**
      分开评税（宽减前合计 120,360 **<** PA 宽减前 121,060）：
        物业税 = 800,000×15% = 120,000（无宽减）
        薪俸税 = NCI 18,000 → 360；宽减 360 → 0
        → 宽减后合计 **120,000**
      宽减后：PA 118,060 < 120,000 → 最优＝PA（若按宽减前比较会错选分开评税）。
    """
    y = "2025_26"
    facts = _input(y, _SINGLE_SUB, _year_obj(
        y,
        participants=_SELF,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="150000", mpf="0", ai=True,
                       has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-1", "1000000")]),
        },
        household=_household(y, married=False),
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, "pa_self"], "selection": "best"},
    ))
    result = _run(facts)

    assert result["best_scenario"] == "pa_self", (
        "比较必须在各方案宽减之后进行（118,060 < 120,000 → PA）",
        result.get("best_scenario"),
    )
    assert _scenario(result, "pa_self")["amounts"]["final_after_reduction"] == "118060"
    assert _scenario(result, _METHOD_NO_PA)["amounts"]["final_after_reduction"] == "120000"
    assert result["amounts"]["final_after_reduction"] == "118060", result["amounts"]

    # —— best 独立于 paid：缺 PST 组件（provisional_paid）→ best 仍确定 ——
    partial_facts = {
        "year_of_assessment": facts["year_of_assessment"],
        "subjects": facts["subjects"],
        "years": {
            y: {
                k: v for k, v in facts["years"][y].items() if k != "provisional_paid"
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }
    partial = _run(partial_facts)
    assert partial["status"] == "partial", partial
    assert partial.get("best_scenario") == "pa_self", (
        "best 只依候选本年 final＋资格（缺 paid 不阻 best；005 §10/REQ-10）",
        partial.get("best_scenario"),
    )


def test_pa_duplicate_claim_blocked() -> None:
    """同一受养人跨人重复申索（D6.3 免税额互斥；s.31(3) 整笔提名）→ 拒算；
    扩展 ledger 矩阵（child+sibling 类互斥）。

    v2 迁移（006 §4.2 行 8/S002 §9.5）：「重复拒」状态保留——父母跨人
    重复（无例外）与子女未按 s.31(3) 整笔提名而双方申索（child_nomination
    =none：明确无获提名方）均须拒算；另按 §8.1 行 3 扩展 child+sibling
    类互斥子案例。
    """
    y = "2025_26"
    resident = _resident_at_reference()
    base_fin = {
        "p1": _fin(nai="300000", mpf="0", ai=True),
        "p2": _fin(nai="200000", mpf="0", ai=True),
    }
    assessment = {"basis": "hypothetical", "reference_date": "2026-06-01",
                  "candidates": [_METHOD_NO_PA, _METHOD_PA], "selection": "best"}

    def relation(rid, actor, subject, kind="child"):
        return {
            "relation_id": rid, "actor_id": actor, "subject_id": subject,
            "kinship": {"kind": kind, "basis": "birth",
                        "during": [{"from": "2025-04-01", "to": "2026-04-01",
                                    "value": True}]},
            "maintained": [{"from": "2025-04-01", "to": "2026-04-01", "value": True}],
        }

    dep_subjects = _COUPLE_SUB + [{"subject_id": "d1", "birth_date": "1955-03-03"}]
    dep_facts = {"d1": {"ordinarily_resident_hk": [
        {"from": "2025-04-01", "to": "2026-04-01", "value": True}
    ]}}
    dep_relation = [
        {"relation_id": "r1", "actor_id": "p1", "subject_id": "d1",
         "kinship": {"kind": "parent", "basis": "birth",
                     "during": [{"from": "2025-04-01", "to": "2026-04-01",
                                 "value": True}]},
         "cash_maintenance": "15000"},
        {"relation_id": "r2", "actor_id": "p2", "subject_id": "d1",
         "kinship": {"kind": "parent", "basis": "birth",
                     "during": [{"from": "2025-04-01", "to": "2026-04-01",
                                 "value": True}]},
         "cash_maintenance": "15000"},
    ]

    # —— 同一受养父母被夫妻两人各自申索（跨人重复，无提名例外）——
    both_parent = _input(y, dep_subjects, _year_obj(
        y, participants=_COUPLE,
        subject_facts={**dep_facts, "p1": {"ordinarily_resident_hk": resident}},
        financials=base_fin, assessment=assessment,
        household=_household(y, married=True),
        relations=dep_relation,
        claims=[
            {"claim_id": "c1", "kind": "parent", "claimant_id": "p1", "subject_id": "d1"},
            {"claim_id": "c2", "kind": "parent", "claimant_id": "p2", "subject_id": "d1"},
        ],
    ))
    # —— 同一子女被夫妻两人分别申索而 child_nomination=none（明确无获提名方；
    #    v1「未按 s.31(3) 整笔提名而双方申索」的 v2 等价输入）——
    child_subjects = _COUPLE_SUB + [{"subject_id": "d1", "birth_date": "2015-06-01"}]
    both_child = _input(y, child_subjects, _year_obj(
        y, participants=_COUPLE,
        subject_facts={"d1": {"unmarried": [
            {"from": "2025-04-01", "to": "2026-04-01", "value": True}
        ]}, "p1": {"ordinarily_resident_hk": resident}},
        financials=base_fin, assessment=assessment,
        household=_household(y, married=True,
                             nomination={"person_id": None, "status": "none"}),
        relations=[relation("r1", "p1", "d1")],
        claims=[
            {"claim_id": "c1", "kind": "child", "claimant_id": "p1", "subject_id": "d1"},
            {"claim_id": "c2", "kind": "child", "claimant_id": "p2", "subject_id": "d1"},
        ],
    ))
    # —— 扩展（§8.1 行 3）：child＋sibling 同 subject 类互斥 ——
    child_sibling = _input(y, child_subjects, _year_obj(
        y, participants=_COUPLE,
        subject_facts={"d1": {"unmarried": [
            {"from": "2025-04-01", "to": "2026-04-01", "value": True}
        ]}, "p1": {"ordinarily_resident_hk": resident}},
        financials=base_fin, assessment=assessment,
        household=_household(y, married=True),
        relations=[relation("r1", "p1", "d1")],
        claims=[
            {"claim_id": "c1", "kind": "child", "claimant_id": "p1", "subject_id": "d1"},
            {"claim_id": "c2", "kind": "sibling", "claimant_id": "p1", "subject_id": "d1"},
        ],
    ))
    for label, facts in (
        ("受养父母重复申索", both_parent),
        ("子女未按 s.31(3) 整笔提名而双方申索", both_child),
        ("child＋sibling 同 subject 类互斥", child_sibling),
    ):
        try:
            _run(facts)
        except AppError as exc:
            assert exc.code == "E_INPUT_CONTRADICTORY", (
                f"{label}应以 E_INPUT_CONTRADICTORY 拒算（005 §8.1），"
                f"实际 {exc.code!r}"
            )
        else:
            raise AssertionError(
                f"{label}属 ledger 冲突（005 §8.1），必须拒算而非静默采纳"
            )


def test_pa_unknown_qualification_no_optimal_claim() -> None:
    """资格 unknown（婚姻状况，v2 空 TF）→ needs_input＋结构化中文追问；
    不输出任何「最优」断言（v2 迁移：Tri/TF 适配，006 §4.2 行 9）。"""
    y = "2025_26"
    result = _run(_input(y, _COUPLE_SUB, _year_obj(
        y,
        participants=_COUPLE,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="250000", mpf="0", ai=True),
            "p2": _fin(ai=False,
                       has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-1", "240000")]),
        },
        household={"marriage": [], "living_apart": [],
                   "child_nomination": {"person_id": None, "status": "none"}},
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, _METHOD_PA], "selection": "best"},
    )))

    assert result["status"] == "needs_input", result
    _assert_chinese_questions(result)
    _assert_amounts_all_null(result)
    assert result.get("best_scenario") is None, (
        "资格不明不得输出最优方案断言",
        result.get("best_scenario"),
    )


def test_pa_joint_output_total_authoritative_no_per_person_whole() -> None:
    """joint 首版输出：权威＝宽减后 joint 总额；份额仅以精确分数（§14
    assessment.shares）暴露、无个人整元分摊税额。

    v2 迁移（006 §4.2 行 10/S002 §9.5）：v1 的 ΣR=0「双方零入息」案例
    不得虚构 s.41(1A) 资格——改为有合法来源存在事实且 zero-reduced 的
    边界（NAV 份额被利息抵尽的物业入息）。
    手算（T3；附表 2/43，T1；v1 版同式保留）：
      R_self=300,000、R_spouse=200,000 → 合并 500,000 − 264,000 = 236,000
      累进 = 16,000＋36,000×17% = 22,120；标准 = 500,000×15% = 75,000 → 累进
      宽减 3,000 → joint 总额 19,120（权威金额）
      份额 Ri/ΣR（§14 Exact）：300,000/500,000=3/5、200,000/500,000=2/5
    ΣR=0（zero-reduced 合法来源）：无税、不分摊、不执行除法（D6.3）。
    """
    y = "2025_26"
    resident = _resident_at_reference()
    assessment = {"basis": "hypothetical", "reference_date": "2026-06-01",
                  "candidates": [_METHOD_NO_PA, _METHOD_PA], "selection": "best"}

    result = _run(_input(y, _COUPLE_SUB, _year_obj(
        y, participants=_COUPLE,
        subject_facts={"p1": {"ordinarily_resident_hk": resident},
                       "p2": {"ordinarily_resident_hk": resident}},
        financials={
            "p1": _fin(nai="300000", mpf="0", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(nai="200000", mpf="0", ai=True,
                       has_income_assessable_under_ordinance=True),
        },
        household=_household(y, married=True, reference="2026-06-01"), assessment=assessment,
    )))
    pa = _assessment(result, "pa:joint", scenario_id=_METHOD_PA)
    assert pa["amounts"]["final_after_reduction"] == "19120", pa
    # 份额仅以精确分数（§14 shares[{person_id,ratio:Exact}]）暴露。
    shares = _shares_by_person(pa)
    assert shares == {"p1": "3/5", "p2": "2/5"}, (
        f"joint PA 份额须以精确分数记入 assessment.shares（005 §14），实际：{shares!r}"
    )
    # 不得输出个人整元分摊税额（scenario amounts 恰六键；无 per-person 整元键）。
    scenario = _scenario(result, _METHOD_PA)
    assert set(scenario["amounts"].keys()) == _AMOUNT_KEYS, sorted(scenario["amounts"])
    blob = json.dumps([scenario, pa], ensure_ascii=False, default=str)
    for forbidden in ("per_person_tax", "per_person_taxes", "apportioned_tax",
                      "individual_allocation"):
        assert forbidden not in blob, f"首版不得输出个人整元分摊税额（发现 {forbidden!r}）"

    # —— ΣR=0：合法来源存在（物业入息）且 zero-reduced（利息抵尽 NAV 份额）
    #    → 无税、不分摊（不执行除法，不产生 NaN/异常）——
    zero = _run(_input(y, _COUPLE_SUB, _year_obj(
        y, participants=_COUPLE,
        subject_facts={"p1": {"ordinarily_resident_hk": resident},
                       "p2": {"ordinarily_resident_hk": resident}},
        financials={
            "p1": _fin(ai=False, has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h1", "100000", interest="80000")]),
            "p2": _fin(ai=False, has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h2", "100000", interest="80000")]),
        },
        household=_household(y, married=True, reference="2026-06-01"), assessment=assessment,
    )))
    pa_zero = _assessment(zero, "pa:joint", scenario_id=_METHOD_PA)
    assert pa_zero["amounts"]["final_after_reduction"] == "0", pa_zero
    assert not (pa_zero.get("shares") or []), (
        "ΣR=0 无税不分摊（D6.3）",
        pa_zero.get("shares"),
    )


def test_missing_pst_partial_no_total_balance() -> None:
    """REQ-9/10：缺 PST 组件（provisional_paid）→ partial、balance null、
    无总额结欠（v2 迁移：适配 REQ-9/10，006 §4.2 行 11）。"""
    y = "2025_26"
    facts = _input(y, _COUPLE_SUB, _year_obj(
        y,
        participants=_COUPLE,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(nai="250000", mpf="0", ai=True,
                       has_income_assessable_under_ordinance=True),
            "p2": _fin(ai=False,
                       has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-1", "240000")]),
        },
        household=_household(y, married=True, reference="2026-06-01"),
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, _METHOD_PA], "selection": "best"},
        provisional_paid=None,
    ))
    result = _run(facts)

    assert result["status"] == "partial", result
    # 已知最终部分仍可输出（Q32 链 → PA final 9,920；best 依候选 final 确定）
    assert result["amounts"]["final_after_reduction"] == "9920", result["amounts"]
    # 缺组件 → 不得输出结欠/退款总额（SDD §3/REQ-9/10）
    assert result["amounts"]["balance"] is None, result["amounts"]
    assert result["amounts"]["provisional_paid"] is None, result["amounts"]
    assert "provisional_paid" in result.get("missing_components", []), result


def test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions() -> None:
    """PAM37 例 4（T2）：39,120−3,000=36,120；内部精确比例 4/5 与 1/5。

    v2 迁移（006 §4.2 行 12/S002 §9.5）：数值保留；内部精确比例经 §14
    assessment.shares（Exact num/den）断言；个人整元不作产品输出。
    手算（PAM37 例 4＋附表 2/43，T1；v1 版同式保留）：
      本人独资利润 480,000；配偶物业租金 150,000 → NAV 120,000
      R_self=480,000、R_spouse=120,000 → 合并 600,000（s.42(10) 各自先算）
      NCI = 600,000 − 264,000 = 336,000
      累进 = 16,000＋136,000×17% = 39,120；标准 = 600,000×15% = 90,000 → 累进
      宽减 3,000 → joint 总额 36,120（权威金额）
      内部精确分数份额：36,120×4/5=28,896、36,120×1/5=7,224——**仅验内部
      比例与 joint 总额**（D6.3 首版输出契约），个人整元不作产品输出。
    """
    y = "2025_26"
    result = _run(_input(y, _COUPLE_SUB, _year_obj(
        y,
        participants=_COUPLE,
        subject_facts={"p1": {"ordinarily_resident_hk": _resident_at_reference()},
                       "p2": {"ordinarily_resident_hk": _resident_at_reference()}},
        financials={
            "p1": _fin(ai=False, has_income_assessable_under_ordinance=True,
                       has_assessable_business_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "business": "confirmed_in_scope"},
                       businesses=[{
                           "business_id": "b-1",
                           "entity_kind": "sole_proprietorship",
                           "assessable_profit": "480000",
                           "share": "1/1",
                       }]),
            "p2": _fin(ai=False, has_income_assessable_under_ordinance=True,
                       has_assessable_property_income=True,
                       source_classification={"salary": "confirmed_in_scope",
                                              "property": "confirmed_in_scope"},
                       properties=[_property("h-1", "150000")]),
        },
        household=_household(y, married=True, reference="2026-06-01"),  # P1 r1 定点纠错（编排者执行，2026-10-15）：补参考日婚姻覆盖（005 §9.2 P2）
        assessment={"basis": "hypothetical", "reference_date": "2026-06-01",
                    "candidates": [_METHOD_NO_PA, _METHOD_PA], "selection": "best"},
    )))

    pa = _assessment(result, "pa:joint", scenario_id=_METHOD_PA)
    assert pa["amounts"]["before_reduction"] == "39120", pa
    assert pa["amounts"]["reduction"] == "3000", pa
    assert pa["amounts"]["final_after_reduction"] == "36120", pa
    # s.43(2B)：按各自减少后总入息比例（宽减后分摊；§14 精确分数 Exact）。
    shares = _shares_by_person(pa)
    assert shares == {"p1": "4/5", "p2": "1/5"}, (
        f"内部精确比例须记入 assessment.shares（005 §14），实际：{shares!r}"
    )
    # 28,896/7,224 为 PAM37 官方例内部精确分数证据——不得作为产品输出断言
    blob = json.dumps([pa, _scenario(result, _METHOD_PA)],
                      ensure_ascii=False, default=str)
    for forbidden in ("per_person_tax", "per_person_taxes", "apportioned_tax"):
        assert forbidden not in blob, forbidden
    assert result["amounts"]["final_after_reduction"] == "36120", result["amounts"]
