"""REQ-3 薪俸税引擎（M2 Red；Annex D D7 为税法语义权威）。

REQ: REQ-3（SDD §9：免税额/MPF 强制供款扣除/两径择低/年度宽减/资格未知追问；
详见 Annex D D7/D7.0）。
规格锚点:
  - SDD §6 薪俸税行＋§9 REQ-3；Annex D D7.0（法定基础）与 D7 末段取整矩阵①。
  - 附表 2 累进带（首四段各 $50,000 → 2/6/10/14%、余额 17%；2018/19 起）＝
    T1-现行法例文本（台账 1.17）。
  - 附表 1 两级标准（2024/25 起首 $5,000,000 15%、余额 16%）＝T1（台账 1.17）。
  - s.12B(2) 联合评税＝两人 NAI 合计−两人 Part 4A 扣除−Part 5 免税额（单一
    NCI）；s.13(2) 标准径基数＝NAI−Part 4A＝T1（台账 1.17）。
  - 附表 4 免税额（2023/24–2025/26：基本 132,000／已婚 264,000；2026/27+：
    基本 145,000／已婚 290,000）＝T1（台账 1.17）。
  - 附表 3B MPF 扣除上限 $18,000（2015/16+）＝T1（台账 1.17）；PAM38 供款
    参数（5%、月相关入息上限 30,000/供 1,500）＝T2（台账 1.13）。
  - 附表 43 宽减（2024/25 100%/$1,500；2025/26 100%/$3,000；2026/27 无条目；
    不适用暂缴）＝T1（台账 1.17）。
  - 取整＝薪俸税估算器三年度观察（两径先分别 floor 再比较、floor 后相等取
    累进、外层 floor；rebate cap 1500/3000/0）＝T1-observed（台账 1.10）。
  - PAM39 Q1/Q2 候选示例（Q1 2025/26：240k/MPF 9k→累进 3,940、标准 34,650、
    宽减 3,000、final 940；Q2 2026/27：480k/MPF 18k→PST 35,890——两组事实
    不得混用）＝T2（台账 1.13，与 SDD §3 示例一致；不得作 fixture 来源）。
期望值来源: 税率/免税额/MPF 上限/宽减上限全部 T1（附表 1/2/3B/4/43）；
  PAM39 Q1 终值（3,940/3,000/940）与 Q2 PST 35,890 为台账 T2 候选值；
  其余组合数值为 T3 独立手算（算式见各测试注释，可审计复算）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.salaries.calculate_salaries_tax(facts: Mapping, bundle: Mapping)
    -> dict：纯函数、注入不可变 RuleBundle（SDD §3）；facts 字段名沿用
    Annex C C3.5 薪俸税族（year_of_assessment／employment_income／
    mpf_mandatory_contributions／married_status{value}／dependent_children[]）。
  - 返回信封：status ∈ {complete, partial, needs_input, blocked}（C3.3 权威）；
    amounts 恰含 C3.4 六键（canonical 字符串或 None）；questions[]（needs_input
    时非空中文清单）。
  - 资格 unknown → status="needs_input"，amounts 六键一律显式 None（不填零、
    不部分计算；C3.3）。
  - 联名（合并评税）事实字段【拟名】：married_status="married"＋
    spouse_employment_income／spouse_mpf_mandatory_contributions＋
    joint_assessment_elected{value}；NCI 按 s.12B(2) 单一合并口径
    （两人 NAI 合计 − 两人 Part 4A 扣除 − 一份已婚免税额）。
  - MPF 扣除额＝min(确认供款事实, 18,000)（附表 3B 每人上限）。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 阶段每个测试的失败
    原因对应「缺失的 app.engines.salaries 模块」，而非收集期整体失败。
"""

from __future__ import annotations

import json
import re

_AMOUNT_KEYS = {
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
}


def _calc():
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.salaries）。"""
    from app.engines.salaries import calculate_salaries_tax
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_salaries_tax, INITIAL_BUNDLE_CONTENT


def _single(year: str, income: str, mpf: str) -> dict:
    """单身受雇人士最小事实集（无子女/父母/其他扣除申索）。"""
    return {
        "year_of_assessment": year,
        "employment_income": income,
        "mpf_mandatory_contributions": mpf,
        "married_status": {"value": "single"},
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
    questions = result.get("questions")
    assert isinstance(questions, list) and questions, (
        "needs_input 必须携带非空 questions[]（REQ-10 中文问题清单）"
    )
    assert any(
        re.search(r"[\u4e00-\u9fff]", q if isinstance(q, str) else json.dumps(q, ensure_ascii=False))
        for q in questions
    ), "问题清单必须为中文"


def test_salaries_pam39_q1_final_940() -> None:
    """PAM39 Q1（2025/26，T2 候选）：40k×6=240k、MPF 9k → 累进 3,940、宽减 3,000、final 940。"""
    calculate, bundle = _calc()
    result = calculate(_single("2025_26", "240000", "9000"), bundle)

    amounts = result["amounts"]
    # 手算（T1 附表 2/4/43＋T2 PAM39 Q1 对账）：
    #   NCI = 240,000 − 9,000(MPF) − 132,000(基本) = 99,000
    #   累进 = 50,000×2% + 49,000×6% = 1,000 + 2,940 = 3,940
    #   标准 = (240,000 − 9,000)×15% = 34,650 → 择低取累进 3,940
    #   宽减 = min(3,940×100%, 3,000) = 3,000 → final = 940
    assert amounts["before_reduction"] == "3940", amounts
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "940", amounts


def test_salaries_pam39_pst_2026_27_35890() -> None:
    """PAM39 Q2（2026/27，T2 候选）：年化 40k×12=480k、MPF 18k → PST 35,890。

    与 Q1 属不同事实组（不得混用）：本组 2026/27 无宽减条目 → reduction="0"，
    final≠940，Q1 的 3,000 宽减不得泄漏到本组结果。
    """
    calculate, bundle = _calc()
    result = calculate(_single("2026_27", "480000", "18000"), bundle)

    amounts = result["amounts"]
    # 手算（T1 附表 2/4/43＋T2 PAM39 Q2 对账）：
    #   NCI = 480,000 − 18,000 − 145,000(2026/27 基本免税额) = 317,000
    #   累进 = 16,000(首 200k) + 117,000×17% = 16,000 + 19,890 = 35,890
    #   标准 = 462,000×15% = 69,300 → 择低取累进
    #   2026/27 无宽减条目（附表 43）→ reduction = 0；final = 35,890
    assert amounts["next_provisional"] == "35890", (
        f"2026/27 暂缴薪俸税应为 35,890（PAM39 Q2），实际：{amounts!r}"
    )
    assert amounts["reduction"] == "0", "2026/27 无宽减条目（附表 43），不得套用 2025/26 的 3,000"
    assert amounts["before_reduction"] == "35890", amounts
    assert amounts["final_after_reduction"] == "35890", amounts


def test_salaries_floor_convention_three_years_official() -> None:
    """三年度（2024/25–2026/27）两径先分别 floor 再择低；宽减上限 1500/3000/0。

    取整锚点＝薪俸税估算器三年度观察（台账 1.10：两径分别 floor 后比较、
    外层 floor；rebate cap 1500/3000/0 与附表 43 一致）。本组事实的小数尾
    使每一年都能区分 floor 与 ceil/round。
    """
    calculate, bundle = _calc()
    facts_by_year = {
        year: _single(year, "240008.50", "9000")
        for year in ("2024_25", "2025_26", "2026_27")
    }
    # 手算（T1 附表 2/4/43）：
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
        amounts = calculate(facts_by_year[year], bundle)["amounts"]
        assert amounts["before_reduction"] == before, (year, amounts)
        assert amounts["reduction"] == reduction, (year, amounts)
        assert amounts["final_after_reduction"] == final, (year, amounts)


def test_salaries_mpf_mandatory_deduction() -> None:
    """MPF 强制供款扣除：每人上限 18,000（附表 3B）；合并评税下两人聚合、各自封顶。"""
    calculate, bundle = _calc()

    # —— 单人：确认供款事实 24,000 > 附表 3B 上限 → 扣除额＝18,000（不是 24,000）——
    #   手算（2025/26，T1 附表 2/4/43；MPF 上限 T1 附表 3B）：
    #   NCI = 480,000 − 18,000 − 132,000 = 330,000
    #   累进 = 16,000 + 130,000×17% = 38,100；标准 = 462,000×15% = 69,300
    #   宽减 3,000 → final = 35,100
    single = calculate(_single("2025_26", "480000", "24000"), bundle)
    assert single["amounts"]["before_reduction"] == "38100", single["amounts"]
    assert single["amounts"]["final_after_reduction"] == "35100", single["amounts"]

    # —— 联名（合并评税，s.12B(2) 单一 NCI）：本人封顶 18,000 ＋ 配偶实际 12,000
    #    聚合扣除 30,000；一份已婚免税额 264,000 ——
    #   手算：合并 NAI = 720,000；Part 4A = 18,000 + 12,000 = 30,000
    #   NCI = 720,000 − 30,000 − 264,000 = 426,000
    #   累进 = 16,000 + 226,000×17% = 54,420；标准 = 690,000×15% = 103,500
    #   宽减 3,000 → final = 51,420
    joint = calculate(
        {
            "year_of_assessment": "2025_26",
            "employment_income": "480000",
            "mpf_mandatory_contributions": "24000",
            "spouse_employment_income": "240000",
            "spouse_mpf_mandatory_contributions": "12000",
            "married_status": {"value": "married"},
            "joint_assessment_elected": {"value": True},
        },
        bundle,
    )
    assert joint["amounts"]["before_reduction"] == "54420", (
        "合并评税应聚合两人 MPF（各自 18,000 封顶）后按 s.12B(2) 计单一 NCI",
        joint["amounts"],
    )
    assert joint["amounts"]["reduction"] == "3000", joint["amounts"]
    assert joint["amounts"]["final_after_reduction"] == "51420", joint["amounts"]


def test_salaries_unknown_eligibility_questions() -> None:
    """资格类字段 unknown → needs_input＋中文问题清单；不得填零或部分计算。"""
    calculate, bundle = _calc()

    # —— 婚姻状况 unknown（s.29 已婚免税额三分支无法判定）——
    married_unknown = _single("2025_26", "240000", "9000")
    married_unknown["married_status"] = {"value": "unknown"}
    result = calculate(married_unknown, bundle)
    assert result["status"] == "needs_input", result
    _assert_chinese_questions(result)
    _assert_amounts_all_null(result)

    # —— 受养子女居住资格 unknown：子女免税额资格不明 → 追问，不得静默按无子女计 ——
    child_unknown = _single("2026_27", "480000", "18000")
    child_unknown["dependent_children"] = [
        {"birth_date": "2026-05-01", "residence": {"value": "unknown"}}
    ]
    result_child = calculate(child_unknown, bundle)
    assert result_child["status"] == "needs_input", result_child
    _assert_chinese_questions(result_child)
    _assert_amounts_all_null(result_child)
