# -*- coding: utf-8 -*-
"""REQ-7/8/9/13 薪俸税引擎（v2 迁移版；Annex D D7 为税法语义权威）。

本文件为旧 v1 引擎测试按已批准 dispositions 的 v2 迁移（006 §4.2 逐行：
M1 附属任务；002 P1）。原 v1 断言的数值/不变量全部保留，事实模型与调用
契约迁 v2（005 §3 subject/year 事实＋§2.1 availability 显式信封）。

disposition 映射（006 §4.2 / S002 §9.5）：
  - test_salaries_pam39_q1_final_940      适配 v2；保留独立算术（T2+手算）→ REQ-8/13
  - test_salaries_pam39_pst_2026_27_35890 final 保留；next 改 2027/28 缺规则
    null/partial → REQ-9
  - test_salaries_floor_convention_three_years_official 适配；保留 → REQ-8/13
  - test_salaries_mpf_mandatory_deduction 单人保留；joint 段（v1 51,420 为
    非法联合候选）替换为 (a) 成立合法案例 → REQ-7
  - test_salaries_unknown_eligibility_questions 婚姻 unknown 保留；子女居港
    断言删除（s.31 无一般居港，REQ-3；迁为无居港事实→子女额照常计入的
    正向断言，不弱化为「不提问也算」）→ REQ-2/3

规格锚点:
  - 005 §2/§2.1（AvailabilityContext 显式参数；缺 context 拒算）、§3/§4/§5
    （v2 input 形状）、§9.1（S1–S7＋两遍）、§10（partial→balance null；
    next 缺→null＋missing）、§14（steps tagged；bases/Exact）。
  - 附表 1/2/3B/4/43（T1，台账 1.17）；PAM39 Q1/Q2（T2，台账 1.13）；
    取整＝Annex D D7 末段取整矩阵①（T1-observed 台账 1.10）。

期望值来源（禁从生产代码反推）: 数值断言与 v1 版一致（T1/T2/T3 手算，
算式见各测试注释）；新增的 v2 状态断言（partial/next null）来自 005 §10
与 REQ-9，非运行观察。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

_AMOUNT_KEYS = {
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
}


def _calc():
    """延迟导入被测引擎与生产 v2 规则束（005 §15.2 矩阵；注入用）。"""
    from app.engines.salaries import calculate_salaries_tax
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_salaries_tax, INITIAL_BUNDLE_CONTENT


def _bundle_hash(bundle: Mapping) -> str:
    """实际完整束六字段 canonical 内容 hash（C2.8；复核 §五.4 纠错）。"""
    from app.core.hashing import bundle_content_hash  # 延迟导入

    return bundle_content_hash(bundle)


def _availability(bundle: Mapping, year: str) -> dict:
    """v2 显式 AvailabilityContext（005 §2 形状；next 缺规则如实标注）。"""
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
    calculate, bundle = _calc()
    return calculate(
        v2_input, bundle, availability=_availability(bundle, v2_input["year_of_assessment"])
    )


def _single(year: str, income: str, mpf: str) -> dict:
    """单身受雇人士最小 v2 事实集（005 §5.3 形状；无子女/父母/扣除申索）。"""
    start = int(year[:4])
    return {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": {
            year: {
                "participants": [{"person_id": "p1", "role": "self"}],
                "subject_facts": {},
                "household": {
                    "marriage": [{"from": f"{start}-04-01",
                                  "to": f"{start + 1}-04-01",
                                  "value": "unmarried"}],
                    "living_apart": [],
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                "relations": [],
                "financials": {
                    "p1": {
                        "employment_income_nai": income,
                        "mpf_mandatory_contributions": mpf,
                        "has_assessable_salary_income": True,
                        "source_classification": {"salary": "confirmed_in_scope"},
                        "properties": [],
                        "businesses": [],
                        "deduction_items": [],
                    }
                },
                "care_payments": [],
                "claims": [],
                "assessment": {
                    "basis": "actual",
                    "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                },
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }


def _assert_amounts_all_null(result: dict) -> None:
    """needs_input/blocked：六个金额键一律显式 None（C3.3；不填零、不缺省）。"""
    assert set(result["amounts"].keys()) == _AMOUNT_KEYS, (
        f"amounts 必须恰含 C3.4 六键，实际：{sorted(result['amounts'])!r}"
    )
    assert all(v is None for v in result["amounts"].values()), (
        f"不得输出任何金额（未知资格不填零），实际：{result['amounts']!r}"
    )


def _assert_chinese_questions(result: dict) -> None:
    """needs_input 携带结构化中文追问（005 §13 定位项；C3.3「结构化」）。"""
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


def _assessment_bases(result: dict, unit_id: str) -> dict:
    """从 tagged steps 取指定评税单元的 bases（005 §14）。"""
    for step in result.get("steps") or []:
        if isinstance(step, dict) and step.get("kind") == "assessment":
            detail = step.get("detail") or {}
            if detail.get("unit_id") == unit_id:
                return {b.get("name"): b.get("value") for b in detail.get("bases") or []}
    return {}


def test_salaries_pam39_q1_final_940() -> None:
    """PAM39 Q1（2025/26，T2 候选）：40k×6=240k、MPF 9k → 累进 3,940、宽减 3,000、final 940。

    v2 迁移（006 §4.2 行 1）：数值保留；状态按 REQ-9/§10（缺下一年事实 →
    next null＋partial；best=salary_separate）。
    """
    result = _run(_single("2025_26", "240000", "9000"))

    amounts = result["amounts"]
    # 手算（T1 附表 2/4/43＋T2 PAM39 Q1 对账；v1 版同式保留）：
    #   NCI = 240,000 − 9,000(MPF) − 132,000(基本) = 99,000
    #   累进 = 50,000×2% + 49,000×6% = 1,000 + 2,940 = 3,940
    #   标准 = (240,000 − 9,000)×15% = 34,650 → 择低取累进 3,940
    #   宽减 = min(3,940×100%, 3,000) = 3,000 → final = 940
    assert amounts["before_reduction"] == "3940", amounts
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "940", amounts
    assert result["status"] == "partial", (
        f"缺下一年事实 → partial（REQ-9/005 §10），实际 {result['status']!r}"
    )
    assert amounts["next_provisional"] is None, amounts
    assert result.get("best_scenario") == "salary_separate", result.get("best_scenario")


def test_salaries_pam39_pst_2026_27_35890() -> None:
    """PAM39 Q2（2026/27，T2 候选）：年化 40k×12=480k、MPF 18k → 35,890；
    2027/28 无规则 → next null＋partial（006 §4.2 行 2：PST 段处置）。

    与 Q1 属不同事实组（不得混用）：本组 2026/27 无宽减条目 → reduction="0"，
    final≠940，Q1 的 3,000 宽减不得泄漏到本组结果；v1 的「下一年回退本年
    规则估算 next」行为按 REQ-9 废除（next=null，不回退）。
    """
    result = _run(_single("2026_27", "480000", "18000"))

    amounts = result["amounts"]
    # 手算（T1 附表 2/4/43＋T2 PAM39 Q2 对账；v1 版同式保留）：
    #   NCI = 480,000 − 18,000 − 145,000(2026/27 基本免税额) = 317,000
    #   累进 = 16,000(首 200k) + 117,000×17% = 35,890；标准 69,300 → 择低
    #   2026/27 无宽减条目（附表 43）→ reduction = 0；final = 35,890
    assert amounts["before_reduction"] == "35890", amounts
    assert amounts["reduction"] == "0", (
        "2026/27 无宽减条目（附表 43），不得套用 2025/26 的 3,000"
    )
    assert amounts["final_after_reduction"] == "35890", amounts
    assert amounts["next_provisional"] is None, (
        f"2027/28 缺规则 → next_provisional=null（不回退本年规则），实际：{amounts!r}"
    )
    assert result["status"] == "partial", result["status"]
    missing = json.dumps(result.get("missing_components") or [], default=str)
    assert "2027_28" in missing or "next" in missing, missing


def test_salaries_floor_convention_three_years_official() -> None:
    """三年度（2024/25–2026/27）两径先分别 floor 再择低；宽减上限 1500/3000/0。

    v2 迁移（006 §4.2 行 3）：三组元组保留；状态迁 v2（partial＋next null）。
    取整锚点＝薪俸税估算器三年度观察（台账 1.10）。本组事实的小数尾
    使每一年都能区分 floor 与 ceil/round。
    """
    facts_by_year = {
        year: _single(year, "240008.50", "9000")
        for year in ("2024_25", "2025_26", "2026_27")
    }
    # 手算（T1 附表 2/4/43；v1 版同式保留）：
    #   2024/25 与 2025/26（基本 132,000）：
    #     NCI = 240,008.50 − 9,000 − 132,000 = 99,008.50
    #     累进 raw = 1,000 + 49,008.50×6% = 3,940.51 → floor 3,940
    #     标准 raw = 231,008.50×15% = 34,651.275 → floor 34,651 → 择低＝累进 3,940
    #     2024/25 宽减 min(3,940, 1,500)=1,500 → final 2,440
    #     2025/26 宽减 min(3,940, 3,000)=3,000 → final 940
    #   2026/27（基本 145,000）：
    #     NCI = 240,008.50 − 9,000 − 145,000 = 86,008.50
    #     累进 raw = 1,000 + 36,008.50×6% = 3,160.51 → floor 3,160（ceil/round=3,161）
    #     标准 floor 34,651 → 择低＝累进 3,160；无宽减条目 → final 3,160
    expected = [
        ("2024_25", "3940", "1500", "2440"),
        ("2025_26", "3940", "3000", "940"),
        ("2026_27", "3160", "0", "3160"),
    ]
    for year, before, reduction, final in expected:
        result = _run(facts_by_year[year])
        amounts = result["amounts"]
        assert amounts["before_reduction"] == before, (year, amounts)
        assert amounts["reduction"] == reduction, (year, amounts)
        assert amounts["final_after_reduction"] == final, (year, amounts)
        assert amounts["next_provisional"] is None, (year, amounts)
        assert result["status"] == "partial", (year, result["status"])


def test_salaries_mpf_mandatory_deduction() -> None:
    """MPF 强制供款扣除：单人每人上限 18,000（附表 3B）；joint 段替换为
    (a) 成立合法案例（006 §4.2 行 4/S002 §9.5）。

    v1 的 joint 段（480k/240k 双方正 NCI → 分别合计 35,860＜联合 51,420，
    s.10(2)(a)(b) 均不成立）属非法联合候选，按处置「必须修而非换信封」
    替换为 (a) 成立（配偶扣除溢出）的合法 10-case；MPF 聚合各自封顶的
    不变量保留（bases part4a＝18,000＋12,000＝30,000）。
    """
    # —— 单人：确认供款事实 24,000 > 附表 3B 上限 → 扣除额＝18,000（不是 24,000）——
    #   手算（2025/26，T1 附表 2/4/43；v1 版同式保留）：
    #   NCI = 480,000 − 18,000 − 132,000 = 330,000
    #   累进 = 16,000 + 130,000×17% = 38,100；标准 = 462,000×15% = 69,300
    #   宽减 3,000 → final = 35,100
    single = _run(_single("2025_26", "480000", "24000"))
    assert single["amounts"]["before_reduction"] == "38100", single["amounts"]
    assert single["amounts"]["final_after_reduction"] == "35100", single["amounts"]

    # —— 合法 joint（s.10(2)(a)：配偶 NCI<0 扣除溢出；MPF 24k→18k＋12k 聚合）——
    #   手算（T3；T1 附表 2/3B/4/43）：
    #   配偶 p2：NAI 100,000、MPF 12,000 → NCI=100,000−12,000−132,000=−44,000<0
    #     → A 成立（p2 分别路径 Part4A＋Part5=144,000>100,000）
    #   joint（s.12B(2) 单一 NCI）：合并 NAI=580,000；Part4A=18,000+12,000=30,000
    #   NCI = 580,000 − 30,000 − 264,000 = 286,000
    #   累进 = 16,000 + 86,000×17% = 30,620；标准 = 550,000×15% = 82,500 → 择低
    #   宽减 3,000 → final = 27,620；separate 合计 = 35,100 + 0 = 35,100
    joint_input = {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"},
                     {"subject_id": "p2", "birth_date": "1992-02-02"}],
        "years": {
            "2025_26": {
                "participants": [{"person_id": "p1", "role": "self"},
                                 {"person_id": "p2", "role": "spouse"}],
                "subject_facts": {},
                "household": {
                    "marriage": [{"from": "2025-04-01", "to": "2026-04-01",
                                  "value": "married", "spouse_id": "p2"}],
                    "living_apart": [{"from": "2025-04-01", "to": "2026-04-01",
                                      "value": False}],
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                "relations": [],
                "financials": {
                    "p1": {
                        "employment_income_nai": "480000",
                        "mpf_mandatory_contributions": "24000",
                        "has_assessable_salary_income": True,
                        "source_classification": {"salary": "confirmed_in_scope"},
                        "properties": [], "businesses": [], "deduction_items": [],
                    },
                    "p2": {
                        "employment_income_nai": "100000",
                        "mpf_mandatory_contributions": "12000",
                        "has_assessable_salary_income": True,
                        "source_classification": {"salary": "confirmed_in_scope"},
                        "properties": [], "businesses": [], "deduction_items": [],
                    },
                },
                "care_payments": [],
                "claims": [],
                "assessment": {
                    "basis": "hypothetical",
                    "candidates": ["salary_separate", "salary_joint"],
                    "selection": "best",
                },
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }
    joint = _run(joint_input)
    assert joint["amounts"]["before_reduction"] == "30620", (
        "合法 joint 应聚合两人 MPF（24,000→18,000 封顶＋12,000）后按 s.12B(2) "
        "计单一 NCI", joint["amounts"],
    )
    assert joint["amounts"]["reduction"] == "3000", joint["amounts"]
    assert joint["amounts"]["final_after_reduction"] == "27620", joint["amounts"]
    assert joint.get("best_scenario") == "salary_joint", joint.get("best_scenario")
    bases = _assessment_bases(joint, "salary:joint")
    assert bases.get("part4a") == "30000/1", (
        f"MPF 聚合各自封顶：18,000＋12,000＝30,000（附表 3B），实际：{bases!r}"
    )


def test_salaries_unknown_eligibility_questions() -> None:
    """资格类事实 unknown → needs_input＋结构化中文追问；不得填零或部分计算。

    v2 迁移（006 §4.2 行 5）：婚姻 unknown 断言保留（household.marriage 空
    TF＝未知）；v1 的「子女居港 unknown→追问」断言删除（s.31 无一般居港，
    REQ-3）——迁为正向断言：不提交任何居港事实时子女免税额照常成立
    （不弱化为「不提问也算」，而是验证计入金额）。
    """
    # —— 婚姻状况 unknown（空 TF；s.29 已婚免税额分支无法判定）——
    married_unknown = _single("2025_26", "240000", "9000")
    married_unknown["years"]["2025_26"]["household"]["marriage"] = []
    result = _run(married_unknown)
    assert result["status"] == "needs_input", result
    _assert_chinese_questions(result)
    _assert_amounts_all_null(result)

    # —— 受养子女（2026/27，未婚/受供养已证）不提交任何居港事实：
    #    s.31 无一般居港条件 → 子女额照常计入（REQ-3 正向断言）——
    #   手算（T1 附表 2/4；T3）：
    #   Part 5 = 145,000(基本) + 140,000(子女) = 285,000
    #   NCI = 480,000 − 18,000 − 285,000 = 177,000
    #   累进 = 1,000+3,000+5,000+27,000×14% = 12,780；标准 69,300 → final 12,780
    child_no_residence = _single("2026_27", "480000", "18000")
    year_obj = child_no_residence["years"]["2026_27"]
    child_no_residence["subjects"].append(
        {"subject_id": "d1", "birth_date": "2018-05-01"}
    )
    year_obj["subject_facts"]["d1"] = {
        "unmarried": [{"from": "2026-04-01", "to": "2027-04-01", "value": True}]
    }
    year_obj["relations"] = [{
        "relation_id": "r1", "actor_id": "p1", "subject_id": "d1",
        "kinship": {"kind": "child", "basis": "birth",
                    "during": [{"from": "2026-04-01", "to": "2027-04-01",
                                "value": True}]},
        "maintained": [{"from": "2026-04-01", "to": "2027-04-01", "value": True}],
    }]
    year_obj["claims"] = [
        {"claim_id": "c1", "kind": "child", "claimant_id": "p1", "subject_id": "d1"}
    ]
    result_child = _run(child_no_residence)
    assert result_child["status"] == "partial", (
        f"s.31 无一般居港条件：不提交居港事实不构成追问，实际 {result_child['status']!r}"
    )
    assert not (result_child.get("questions") or []), result_child.get("questions")
    assert result_child["amounts"]["final_after_reduction"] == "12780", (
        result_child["amounts"]
    )
    bases = _assessment_bases(result_child, "salary:p1")
    assert bases.get("allowances") == "285000/1", (
        f"子女额 140,000 须照常计入（无居港条件），实际：{bases!r}"
    )


# ---------------------------------------------------------------------------
# C 路新增：E1 生产束 schema 准入与活动规则 fail-closed（真 hash 重绑；
# 唯一破坏目标，其余规则齐全；合法对照先行）
# ---------------------------------------------------------------------------


def _mutated_production_bundle(mutate) -> tuple[dict, dict]:
    import copy

    _, bundle = _calc()
    mutated = copy.deepcopy(bundle)
    mutate(mutated)
    return mutated, _availability(mutated, "2025_26")


def _expect_rules_error(call, label: str) -> None:
    from app.core.errors import AppError

    try:
        call()
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"{label}：期望 E_RULES_STATE_UNVERIFIABLE（005 §2.1/§15），"
            f"实际 {exc.code!r}/{exc.field!r}"
        )
    except Exception as exc:  # noqa: BLE001 - 裸异常必须显式暴露为 Red
        raise AssertionError(
            f"{label}：须映射规则错误，不得裸异常 {type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise AssertionError(f"{label}：活动规则不可核验不得计算（fail-closed）")


def test_salaries_self_consistent_v1_production_bundle_rejected() -> None:
    """E1：生产束双方一致的旧 rules schema（1.0.0）仍不受支持——
    合法对照 940；唯一改 schema＋真 hash 重绑 → E_RULES_STATE_UNVERIFIABLE。"""
    calculate, _ = _calc()
    control = _run(_single("2025_26", "240000", "9000"))
    assert control["amounts"]["final_after_reduction"] == "940", control["amounts"]

    mutated, ctx = _mutated_production_bundle(
        lambda b: b.__setitem__("rules_schema_version", "1.0.0")
    )
    assert ctx["binding"]["rules_schema_version"] == "1.0.0"
    facts = _single("2025_26", "240000", "9000")
    _expect_rules_error(
        lambda: calculate(facts, mutated, availability=ctx),
        "自洽旧 schema 1.0.0",
    )


def test_salaries_active_allowance_amount_malformed_maps_rules_error() -> None:
    """E1：活动矩阵节点畸形金额（真 hash 重绑、其余规则齐全）须映射
    E_RULES_STATE_UNVERIFIABLE，不得裸 Decimal/ValueError 或误报 E_INPUT_*。"""
    control = _run(_single("2025_26", "240000", "9000"))
    assert control["amounts"]["final_after_reduction"] == "940", control["amounts"]

    def _mutate(bundle: dict) -> None:
        bundle["data"]["allowances"]["2025_26"]["basic"]["amount"] = "not-a-money"

    mutated, ctx = _mutated_production_bundle(_mutate)
    facts = _single("2025_26", "240000", "9000")
    _expect_rules_error(
        lambda: _run_on(facts, mutated, ctx),
        "畸形 allowances.2025_26.basic.amount",
    )


def test_salaries_active_allowance_node_missing_maps_rules_error() -> None:
    """E1：活动矩阵节点缺键（真 hash 重绑、其余规则齐全）须映射
    E_RULES_STATE_UNVERIFIABLE，不得按零/回退计算。"""
    control = _run(_single("2025_26", "240000", "9000"))
    assert control["amounts"]["final_after_reduction"] == "940", control["amounts"]

    def _mutate(bundle: dict) -> None:
        del bundle["data"]["allowances"]["2025_26"]["basic"]

    mutated, ctx = _mutated_production_bundle(_mutate)
    facts = _single("2025_26", "240000", "9000")
    _expect_rules_error(
        lambda: _run_on(facts, mutated, ctx),
        "缺 allowances.2025_26.basic",
    )


def _run_on(facts: dict, bundle: dict, ctx: dict) -> dict:
    from app.engines.salaries import calculate_salaries_tax

    return calculate_salaries_tax(facts, bundle, availability=ctx)
