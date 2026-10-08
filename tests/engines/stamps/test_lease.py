"""REQ-7 印花税 — 租约引擎（M4 Red；Annex D D3 为税法语义权威）。

REQ: REQ-7（SDD §9：普通租约按文书日期选制度；档位/取整链/premium 冲突/
复本 head 4 已法定化（Annex D D3，T1-现行法例文本）；档位/每 $100 或其
部分/按金排除/期限周年日判定按 D3.1/D3.1a；关键租期事实缺失 → 追问）。
规格锚点:
  - SDD §6 租约行＋§9 REQ-7；Annex D D3.1（计税规则：GovHK＋Cap.117 head 1
    sub-head (2) T1；IRSD119 ¶5 T2 同文佐证）、D3.1a（官方租约计算器
    T1-observed，台账 1.14）、D3.2（premium 冲突法定闭合）、D3.3（复本
    HEAD 4 法定闭合）、D3.4（租约事实字段）。
  - 档位（D3.1，T1）：未界定/不确定 0.25%（年租或平均年租）；不超过 1 年
    0.25%（**租期内应付租金总额**）；>1–3 年 0.5%（年租/平均年租）；>3 年
    1%。期限按**周年日**（恰 1 年 0.25%／1 年 1 天–3 年 0.5%／恰 3 年
    0.5%／3 年+1 天 1%），非 days/365；起讫两日都算。
  - 取整链（D3.1 法定基础＋D3.1a T1-observed）：基数**每 $100 或其部分**
    （ceil100）→ 乘率 → 税额 ceil1；按金不计入。
  - premium（D3.2，T1-现行法例文本 head 1(1) Note 1，3 of 2026 s.14）：
    **2026-02-26 起**住宅含租 → 6.5%、非住宅 → 4.25%；**2024-04-01–
    2026-02-25 含租 → 4.25%**（修订前 Note 1 口径）；premium 必须显式
    （"0" 或金额；缺失 ≠ 零 → 追问）；rent＋premium 两笔独立取整后相加
    （s.10(4)＋s.18A），合计不再统一取整。
  - 复本（D3.3 HEAD 4，T1）：原本税额不足 $5 → 与原本同额；否则 $5。
  - D3.4：term_kind ∈ {indefinite, fixed}；indefinite → annual_rent 输入
    （不得强制 total_rent）；fixed ≤1 年必须 total_rent（总租金直接作基数、
    不年化）；fixed >1 年全期固定月租且无免租期方可 fixed_monthly（月租×12）。
期望值来源:
  - 档位费率/ceil100/ceil1/按金排除/复本 head 4＝T1（Cap.117 head 1 现行
    文本＋GovHK；台账 1.14）；档位周年日判定与 ≤1 年总租金/月租×12＝
    T1-observed（官方租约计算器，台账 1.14）；premium 6.5%/4.25%/4.25%
    期间口径＝T1（3 of 2026 s.14 Note 1＋GovHK L1979）；
  - 端点期望值（60,000→150；120,000.01→301；252,000→630；premium 100,000
    → 6,500/4,250 等）＝T3 独立手算（算式见各测试注释，可审计复算）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.stamps.lease.calculate_lease_stamp_duty(
    facts: Mapping, bundle: Mapping) -> dict：纯函数、注入不可变 RuleBundle；
    facts 字段名＝D3.4：instrument_date／property_class（premium>0 时必需）／
    term_kind ∈ {indefinite, fixed}／term_start／term_end／rent_input_mode ∈
    {fixed_monthly, total_rent, annual_rent}／monthly_rent／total_rent／
    annual_rent／deposit（不计税、仅记录）／premium（显式 "0" 或金额；缺失
    → needs_input）／copies（复本份数）。
  - 返回信封：status ∈ {complete, partial, needs_input, blocked}；amounts
    恰含 C3.4 六键——合计＝before_reduction＝final_after_reduction、
    reduction="0"（印花税无一次性宽减）、provisional_paid/next_provisional/
    balance 恒 None（无暂缴组件）；questions[]（needs_input 时非空中文清单）。
  - 另带 duty_breakdown{rounded_rent_base, rent_duty, premium_duty,
    duplicate_duty, total}：rounded_rent_base＝ceil100 后租金基数（≤1 年＝
    总租金、其余＝年租/平均年租）；rent_duty/premium_duty/duplicate_duty
    各自独立取整（ceil1；premium 按 D3.2 率）；total＝分项相加（不再统一
    取整）。
  - premium 缺失／关键租期事实缺失（term_kind 等）→ needs_input＋中文
    questions＋amounts 六键一律显式 None（不默认低税档）。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.engines.stamps.lease 模块」。
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
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.stamps.lease）。"""
    from app.engines.stamps.lease import calculate_lease_stamp_duty
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_lease_stamp_duty, INITIAL_BUNDLE_CONTENT


def _lease(
    *,
    date: str = "2025-06-20",
    term_kind: str = "fixed",
    term_start: str = "2025-07-01",
    term_end: str | None = "2027-06-30",
    rent_input_mode: str = "fixed_monthly",
    monthly_rent: str | None = "10000",
    total_rent: str | None = None,
    annual_rent: str | None = None,
    premium: str | None = "0",
    deposit: str | None = None,
    copies: int | None = None,
    property_class: str | None = None,
    **extra,
) -> dict:
    """租约事实构造器：premium 默认显式 "0"（D3.1：缺失 ≠ 零 → 追问）。"""
    facts: dict = {
        "instrument_date": date,
        "term_kind": term_kind,
        "rent_input_mode": rent_input_mode,
    }
    if term_start is not None:
        facts["term_start"] = term_start
    if term_end is not None:
        facts["term_end"] = term_end
    if monthly_rent is not None:
        facts["monthly_rent"] = monthly_rent
    if total_rent is not None:
        facts["total_rent"] = total_rent
    if annual_rent is not None:
        facts["annual_rent"] = annual_rent
    if premium is not None:
        facts["premium"] = premium
    if deposit is not None:
        facts["deposit"] = deposit
    if copies is not None:
        facts["copies"] = copies
    if property_class is not None:
        facts["property_class"] = property_class
    facts.update(extra)
    return facts


def _assert_total(result: dict, expected: str) -> None:
    assert result["status"] == "complete", result
    amounts = result["amounts"]
    assert amounts["before_reduction"] == expected, amounts
    assert amounts["reduction"] == "0", amounts
    assert amounts["final_after_reduction"] == expected, amounts
    assert result["duty_breakdown"]["total"] == expected, result["duty_breakdown"]


def _assert_needs_input(result: dict) -> None:
    assert result["status"] == "needs_input", result
    questions = result.get("questions")
    assert isinstance(questions, list) and questions, "needs_input 须携带非空 questions[]"
    assert any(
        re.search(r"[\u4e00-\u9fff]", q if isinstance(q, str) else json.dumps(q, ensure_ascii=False))
        for q in questions
    ), "问题清单必须为中文"
    assert set(result["amounts"].keys()) == _AMOUNT_KEYS, result["amounts"]
    assert all(v is None for v in result["amounts"].values()), (
        "needs_input 不得输出任何金额（六键一律显式 None）",
        result["amounts"],
    )


def test_lease_six_months_uses_total_not_annual_rent() -> None:
    """≤1 年档（0.25%）以租期内总租金为基数，不得年化（D3.1/D3.1a，T1）。

    手算（T3）：月租 10,000×6 个月 → 总租金 60,000 → ceil100 无变化 →
    0.25% → 150（若错误年化 60,000→120,000/年 → 300，可区分）。
    租期 2025-07-01..2025-12-31 恰 6 个月（≤1 年 → 必须总租金输入模式）。
    """
    calc, bundle = _calc()
    result = calc(
        _lease(
            term_start="2025-07-01",
            term_end="2025-12-31",
            rent_input_mode="total_rent",
            total_rent="60000",
            monthly_rent=None,
        ),
        bundle,
    )
    _assert_total(result, "150")
    assert result["duty_breakdown"]["rounded_rent_base"] == "60000", result["duty_breakdown"]
    assert result["duty_breakdown"]["rent_duty"] == "150", result["duty_breakdown"]


def test_lease_exact_one_and_three_year_boundaries() -> None:
    """期限按周年日：恰 1 年 0.25%；1 年+1 天 0.5%；恰 3 年 0.5%；3 年+1 天 1%。

    手算（T3；档位 T1；ceil100 后基数均为 120,000）：
      2025-07-01..2026-06-30 恰 1 年 → 0.25% ×总租金 120,000 = 300
      2025-07-01..2026-07-01（1 年 1 天，起讫两日都算）→ 0.5% ×年租 120,000 = 600
      2025-07-01..2028-06-30 恰 3 年 → 0.5% ×年租 120,000 = 600
      2025-07-01..2028-07-01（3 年 1 天）→ 1% ×年租 120,000 = 1,200
    """
    calc, bundle = _calc()
    # —— 恰 1 年：≤1 年档 → 必须总租金输入（月租模式对 ≤1 年不适用）——
    exact_1y = calc(
        _lease(
            term_start="2025-07-01",
            term_end="2026-06-30",
            rent_input_mode="total_rent",
            total_rent="120000",
            monthly_rent=None,
        ),
        bundle,
    )
    _assert_total(exact_1y, "300")

    one_y_one_d = calc(
        _lease(term_start="2025-07-01", term_end="2026-07-01"), bundle
    )
    _assert_total(one_y_one_d, "600")

    exact_3y = calc(
        _lease(term_start="2025-07-01", term_end="2028-06-30"), bundle
    )
    _assert_total(exact_3y, "600")

    three_y_one_d = calc(
        _lease(term_start="2025-07-01", term_end="2028-07-01"), bundle
    )
    _assert_total(three_y_one_d, "1200")


def test_lease_two_year_variable_rent_uses_average() -> None:
    """>1 年总租金分支按实际年化平均年租（D3.1a，T1-observed）。

    手算（T3）：两年租金 120,000＋132,000 → 总租金 252,000；恰 2 年
    （2025-07-01..2027-06-30，无尾部天数、无 2/29）→ 平均年租
    = 252,000×365/(2×365+0) = 126,000 → round4 不变 → ceil100 不变 →
    0.5% → 630（若误用首年 120,000 → 600／末年 132,000 → 660，可区分）。
    """
    calc, bundle = _calc()
    result = calc(
        _lease(
            term_start="2025-07-01",
            term_end="2027-06-30",
            rent_input_mode="total_rent",
            total_rent="252000",
            monthly_rent=None,
        ),
        bundle,
    )
    _assert_total(result, "630")
    assert result["duty_breakdown"]["rounded_rent_base"] == "126000", result["duty_breakdown"]


def test_lease_indefinite_is_valid_not_missing_term() -> None:
    """不确定期限＝有效档（0.25%×年租），不是缺失租期事实（D3.1/D3.4，T1）。

    手算（T3）：indefinite → annual_rent 输入模式 → 120,000 → ceil100 不变
    → 0.25% → 300。不得因 term_end 缺席而 needs_input。
    """
    calc, bundle = _calc()
    result = calc(
        _lease(
            term_kind="indefinite",
            term_end=None,
            rent_input_mode="annual_rent",
            monthly_rent=None,
            annual_rent="120000",
        ),
        bundle,
    )
    _assert_total(result, "300")


def test_lease_rent_ceil100_then_duty_ceil1() -> None:
    """取整链：基数 ceil100 → 乘率 → 税额 ceil1（sub-head (2)(b)＋D3.1a，T1）。

    手算（T3）：年租 120,000.01 → 每 $100 或其部分 → 基数 120,100 →
    0.25% = 300.25 → ceil → 301（基数必须先取整至 120,100——经
    rounded_rent_base 审计；floor 路径 300，可区分）。
    """
    calc, bundle = _calc()
    result = calc(
        _lease(
            term_kind="indefinite",
            term_end=None,
            rent_input_mode="annual_rent",
            monthly_rent=None,
            annual_rent="120000.01",
        ),
        bundle,
    )
    _assert_total(result, "301")
    assert result["duty_breakdown"]["rounded_rent_base"] == "120100", (
        "租金基数须先按每 $100 或其部分向上取整（ceil100）",
        result["duty_breakdown"],
    )
    assert result["duty_breakdown"]["rent_duty"] == "301", result["duty_breakdown"]


def test_lease_deposit_excluded_without_silently_excluding_premium() -> None:
    """按金不计税；premium 必须显式（缺失 ≠ 零 → 追问）（D3.1，T1）。

    手算（T3）：两年固定月租 10,000 → 年租 120,000 → 0.5% → 600；
    按金 20,000 不计入（若误计：年租 120,000＋按金摊入 → 不同值，可区分）。
    premium 缺失 → needs_input（不得静默按 0 处理）。
    """
    calc, bundle = _calc()

    # —— premium 缺失 → 追问（premium 必须显式："0" 或金额；缺失 ≠ 零）——
    missing_premium = _lease(deposit="20000", premium=None)
    result_missing = calc(missing_premium, bundle)
    _assert_needs_input(result_missing)

    # —— premium 显式 "0"＋按金 20,000 → 仅租金税 600（按金排除）——
    result = calc(_lease(deposit="20000", premium="0"), bundle)
    _assert_total(result, "600")
    assert result["duty_breakdown"]["rent_duty"] == "600", result["duty_breakdown"]
    assert result["duty_breakdown"]["premium_duty"] == "0", result["duty_breakdown"]


def test_lease_premium_with_rent_425_before_2026_02_26() -> None:
    """历史窗（2024-04-01..2026-02-25）含租 premium＝4.25%（D3.2，T1）。

    手算（T3；s.10(4)＋s.18A 两笔独立取整后相加）：
      租金：两年月租 10,000 → 年租 120,000 → ceil100 不变 → 0.5% → 600
      premium：100,000×4.25% = 4,250（ceil1 不变）
      合计 600＋4,250 = 4,850（不再统一取整）。
    """
    calc, bundle = _calc()
    result = calc(
        _lease(date="2025-06-20", premium="100000", property_class="residential"),
        bundle,
    )
    _assert_total(result, "4850")
    assert result["duty_breakdown"]["rent_duty"] == "600", result["duty_breakdown"]
    assert result["duty_breakdown"]["premium_duty"] == "4250", result["duty_breakdown"]


def test_lease_premium_with_rent_residential_6_5_nonresidential_4_25_statutory() -> None:
    """2026-02-26 起含租 premium：住宅 6.5%／非住宅 4.25%（3 of 2026 s.14 Note 1，T1）。

    按现行法例文本（D3.2，法定已闭）：GovHK 概括「4.25%」为过时/简化表述，
    住宅含租 premium 自 2026-02-26 起＝6.5%（非住宅 4.25%）。
    手算（T3；两笔独立取整后相加）：
      租金：两年月租 10,000 → 年租 120,000 → 0.5% → 600
      住宅：premium 100,000×6.5% = 6,500 → 合计 7,100
      非住宅：premium 100,000×4.25% = 4,250 → 合计 4,850
    """
    calc, bundle = _calc()
    residential = calc(
        _lease(
            date="2026-03-01",
            term_start="2026-04-01",
            term_end="2028-03-31",
            premium="100000",
            property_class="residential",
        ),
        bundle,
    )
    _assert_total(residential, "7100")
    assert residential["duty_breakdown"]["premium_duty"] == "6500", residential["duty_breakdown"]

    nonresidential = calc(
        _lease(
            date="2026-03-01",
            term_start="2026-04-01",
            term_end="2028-03-31",
            premium="100000",
            property_class="nonresidential",
        ),
        bundle,
    )
    _assert_total(nonresidential, "4850")
    assert nonresidential["duty_breakdown"]["premium_duty"] == "4250", (
        nonresidential["duty_breakdown"]
    )


def test_lease_duplicate_5_each_with_original_link() -> None:
    """复本（HEAD 4）：原本 ≥$5 → 每份 $5；原本 <$5 → 与原本同额（D3.3，T1）。

    手算（T3）：
      原本：两年月租 10,000 → 0.5% → 600；复本 2 份 × 5 = 10 → 合计 610
      低额例外：年租 40（indefinite）→ ceil100 基数 100 → 0.25% = 0.25
      → ceil1 → 原本 1（<5）→ 复本与原本同额 1 → 合计 2（非 5，可区分）。
    """
    calc, bundle = _calc()

    # —— 原本 600（≥5）：复本每份 $5 ——
    two_copies = calc(_lease(copies=2, premium="0"), bundle)
    _assert_total(two_copies, "610")
    assert two_copies["duty_breakdown"]["rent_duty"] == "600", two_copies["duty_breakdown"]
    assert two_copies["duty_breakdown"]["duplicate_duty"] == "10", (
        "复本 2 份 × $5（原本 600 ≥ 5）",
        two_copies["duty_breakdown"],
    )

    # —— 原本 1（<5）：复本与原本同额（HEAD 4 低额例外）——
    low_original = calc(
        _lease(
            term_kind="indefinite",
            term_end=None,
            rent_input_mode="annual_rent",
            monthly_rent=None,
            annual_rent="40",
            copies=1,
        ),
        bundle,
    )
    _assert_total(low_original, "2")
    assert low_original["duty_breakdown"]["rent_duty"] == "1", low_original["duty_breakdown"]
    assert low_original["duty_breakdown"]["duplicate_duty"] == "1", (
        "原本税额不足 $5 → 复本与原本同额（HEAD 4 低额例外）",
        low_original["duty_breakdown"],
    )
