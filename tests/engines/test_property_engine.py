"""REQ-5 物业税引擎（M2 Red；Annex D D5 为税法语义权威）。

REQ: REQ-5（SDD §9：NAV 口径（s.5(1A)：业主同意且已缴差饷可扣、20% 按
（AV−差饷）计、不可追回租金 s.7C 含超额部分以前年度回扣、按金抵销限不可追回、
不得扣地租/修葺/保险/利息）；不套用他税宽减；详见 Annex D D5）。
规格锚点:
  - SDD §6 物业税行＋§9 REQ-5；Annex D D5.0（法定基础 s.5/s.5B/s.7C＋
    BIR57 Note 3）。
  - s.5(1A)（T1-现行法例文本，台账 1.17）：NAV＝AV − 业主**同意缴付并已缴付**
    的差饷（两条件）− 扣除差饷后余额的 20% 法定修葺免税额；差饷先扣、20% 后计。
  - s.7C(1)(2)(3)（T1）：不可追回代价在成为不可追回年度扣除；已扣者追回年度
    计回收入（AV）；当年 AV 不足扣全部不可追回代价时，未扣部分自最近一个租金
    收入足够的**以前课税年度**回扣（→ 修订评税/退税；无未来结转）；目标以前
    年度 AV/差饷/已评税额事实缺失 → needs_input（不得改向未来或静默丢弃）。
  - BIR57 Note 3（T1，台账 1.15）：NAV＝租金收入−不可追回租金−业主已付差饷
    −剩余额 20%；按金抵欠租部分不视为不可追回（b)(i)；追回计收入；premium
    按租期或 3 年（较短者）月摊（(a)；s.5B(4) 法定基础）；标准税率 15%
    （2020/21–2026/27，(c)）。
  - pty.htm Q7（2025/26 官方示例，T1，台账 1.7）：租金 120,000 → NAV 96,000
    → 物业税 14,400＋下年度暂缴 14,400；Q26/Q27（T1）：仅同意并已付差饷可扣；
    不得扣地租/修葺/保险/利息；按金抵销限不可追回部分；收回租金重新计收入。
  - PAM54（T2，台账 1.15）：业主付差饷先扣差饷再计 20%；Q1 示例（9 个月
    270,000 − 差饷 12,000 → 258,000 − 20% → NAV 206,400 → 税 30,960）。
  - s.7C(3) 回扣例（GovHK irrecoverable.htm Example 2，T1 佐证，台账 1.17）：
    当年 AV 85,000 < 不可追回 120,000 → 超额 35,000 自 2024/25 回扣 → 修订税
    24,120、退税 4,200（对应 2024/25 AV 236,000：原税 28,320；35,000×0.8×15%
    = 4,200）。Example 1：20% 基数＝收入−坏账−差饷。
  - 取整/评税单位（D5.0 冻结）：直接物业税评税单位＝**逐项物业**
    （BIR57 Note 1(b)＋faq pty.htm Q4/Q7）→ 逐物业 floor(NAV)→floor(NAV×15%)；
    聚合顺序不恒等（反例：两物业 AV 各 17 → 逐物业合计 2 vs 聚合 4）；
    PA 聚合观察（floor 作用于输入聚合字段）不得外推为评税单位。
  - 物业税无一次性宽减（附表 43 每年三行＝薪俸/利得/PA，无物业税行；T1）。
期望值来源: Q7/Q26/Q27/BIR57 Note 3＝T1（pty.htm/bir57se_notes）；PAM54 Q1＝
  T2 台账值；s.7C 回扣数值＝GovHK irrecoverable.htm Example 2（T1 佐证）＋
  T3 复算（AV 236,000×0.8×15% = 28,320；(236,000−35,000)×0.8×15% = 24,120；
  退税 4,200）；其余组合数值为 T3 独立手算（算式见各测试注释）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.property.calculate_property_tax(facts: Mapping, bundle: Mapping)
    -> dict：纯函数、注入不可变 RuleBundle；字段名沿用 C3.5 物业税族
    （rent_received/rates_paid_by_owner/irrecoverable_rent/deposit_offsets/
    mortgage_interest）＋【拟名】扩展（properties[] 逐物业事实、
    recovered_previously_deducted_rent、preceding_year_property_facts[]、
    deduction_items[]{item_type,amount}）。
  - rates_paid_by_owner 形态【拟名】：{amount, agreed{value}, actually_paid
    {value}}——两条件（s.5(1A)(b)(i)）分别三态；amount>0 而 agreed/actually_paid
    未知 → needs_input。
  - 返回信封同薪俸（status/amounts 六键/questions[]）；另带
    properties[]{property_id, nav, tax}（逐物业评税单位明细）与（s.7C(3) 触发
    时）preceding_year_revision{year_of_assessment, original_tax, revised_tax,
    refund}。
  - 不支持扣除（地租/修葺/保险/利息）以 deduction_items 提交且为正 → 抛
    app.core.errors.AppError(code="E_DEDUCTION_UNSUPPORTED")（REQ-10：拒算
    受影响税项；显式 "0" 合法无效果）。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 失败原因＝缺失
    app.engines.property 模块。
"""

from __future__ import annotations

from app.core.errors import AppError


def _calc():
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.property）。"""
    from app.engines.property import calculate_property_tax
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_property_tax, INITIAL_BUNDLE_CONTENT


def _paid_rates(amount: str) -> dict:
    """业主同意并已缴差饷（两条件均真，s.5(1A)(b)(i)）。"""
    return {
        "amount": amount,
        "agreed": {"value": True},
        "actually_paid": {"value": True},
    }


def _property(pid: str, rent: str, **extra) -> dict:
    prop = {
        "property_id": pid,
        "rent_received": rent,
        "rates_paid_by_owner": _paid_rates("0"),
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
    }
    prop.update(extra)
    return prop


def _facts(year: str, *properties: dict, **extra) -> dict:
    facts = {"year_of_assessment": year, "properties": list(properties)}
    facts.update(extra)
    return facts


def test_property_q7_120000_96000_14400() -> None:
    """pty.htm Q7（2025/26 官方示例，T1）：120,000 → NAV 96,000 → 税 14,400＋暂缴 14,400。"""
    calculate, bundle = _calc()
    result = calculate(_facts("2025_26", _property("prop-1", "120000")), bundle)

    detail = result["properties"][0]
    # 手算（Q7/BIR57 Note 3）：NAV = (120,000 − 0 差饷) × 80% = 96,000
    #   → 税 = floor(floor(96,000)×15%) = 14,400
    assert detail["nav"] == "96000", detail
    assert detail["tax"] == "14400", detail

    amounts = result["amounts"]
    assert amounts["before_reduction"] == "14400", amounts
    assert amounts["reduction"] == "0", "物业税不适用一次性宽减（附表 43 无物业税行）"
    assert amounts["final_after_reduction"] == "14400", amounts
    assert amounts["next_provisional"] == "14400", "下年度暂缴物业税 14,400（Q7；分列）"


def test_property_rates_agreed_and_actually_paid() -> None:
    """差饷扣除两条件（s.5(1A)：同意＋已缴）；PAM54 Q1：先扣差饷再计 20%。"""
    calculate, bundle = _calc()

    # —— 两条件均真：PAM54 Q1（T2 台账值）270,000 − 12,000 → 258,000
    #    → 20% 后 NAV 206,400 → 税 30,960 ——
    paid = calculate(
        _facts("2025_26", _property("prop-1", "270000", rates_paid_by_owner=_paid_rates("12000"))),
        bundle,
    )
    assert paid["properties"][0]["nav"] == "206400", paid["properties"]
    assert paid["properties"][0]["tax"] == "30960", paid["properties"]

    # —— 已同意但未实际缴付 → 不得扣差饷：NAV = 270,000×80% = 216,000 → 32,400 ——
    unpaid = _property(
        "prop-1",
        "270000",
        rates_paid_by_owner={
            "amount": "12000",
            "agreed": {"value": True},
            "actually_paid": {"value": False},
        },
    )
    result_unpaid = calculate(_facts("2025_26", unpaid), bundle)
    assert result_unpaid["properties"][0]["nav"] == "216000", result_unpaid["properties"]
    assert result_unpaid["properties"][0]["tax"] == "32400", result_unpaid["properties"]

    # —— 差饷金额为正但同意事实 unknown → needs_input（不得默认可扣/不可扣）——
    unknown_agreed = _property(
        "prop-1",
        "270000",
        rates_paid_by_owner={
            "amount": "12000",
            "agreed": {"value": "unknown"},
            "actually_paid": {"value": True},
        },
    )
    result_unknown = calculate(_facts("2025_26", unknown_agreed), bundle)
    assert result_unknown["status"] == "needs_input", result_unknown
    assert result_unknown.get("questions"), "差饷两条件不明须追问（中文问题清单）"
    assert all(v is None for v in result_unknown["amounts"].values()), (
        result_unknown["amounts"]
    )


def test_property_rejects_ground_rent_repairs_insurance_interest() -> None:
    """地租/修葺/保险/按揭利息不得在物业税扣除：正数申索 → E_DEDUCTION_UNSUPPORTED。"""
    calculate, bundle = _calc()
    for item_type in ("ground_rent", "repairs", "insurance", "mortgage_interest"):
        facts = _facts(
            "2025_26",
            _property(
                "prop-1",
                "120000",
                deduction_items=[{"item_type": item_type, "amount": "5000"}],
            ),
        )
        try:
            calculate(facts, bundle)
        except AppError as exc:
            assert exc.code == "E_DEDUCTION_UNSUPPORTED", (
                f"{item_type} 正数申索应以 E_DEDUCTION_UNSUPPORTED 拒算，"
                f"实际 {exc.code!r}"
            )
        else:
            raise AssertionError(
                f"{item_type} 不可在物业税下扣除（Q26/PAM54），正数申索须拒算受影响税项"
            )

    # —— 显式零合法（无效果）：仍按 Q7 链计算 14,400（三态：零≠正数）——
    zero = calculate(
        _facts(
            "2025_26",
            _property(
                "prop-1",
                "120000",
                deduction_items=[{"item_type": "ground_rent", "amount": "0"}],
            ),
        ),
        bundle,
    )
    assert zero["properties"][0]["tax"] == "14400", zero["properties"]


def test_property_twenty_percent_after_rates_and_bad_debt() -> None:
    """20% 基数＝收入−不可追回租金−差饷（s.5(1A) 括号顺序；BIR57 Note 3(d)）。"""
    calculate, bundle = _calc()
    # 手算（T3；Example 1 口径：20% 基数＝收入−坏账−差饷）：
    #   250,000 − 50,000(不可追回) − 20,000(同意并已缴差饷) = 180,000
    #   → 20% 免税额 36,000 → NAV = 180,000 − 36,000 = 144,000 → 税 21,600
    #   （若误按全额租金先计 20%：NAV=130,000→19,500；可区分顺序）
    facts = _facts(
        "2025_26",
        _property(
            "prop-1",
            "250000",
            rates_paid_by_owner=_paid_rates("20000"),
            irrecoverable_rent="50000",
        ),
    )
    result = calculate(facts, bundle)
    assert result["properties"][0]["nav"] == "144000", result["properties"]
    assert result["properties"][0]["tax"] == "21600", result["properties"]


def test_s7c_excess_bad_debt_setoff_against_preceding_year() -> None:
    """s.7C(3)：超额坏账自以前课税年度回扣（修订评税/退税）；缺以前年度事实 → needs_input。"""
    calculate, bundle = _calc()
    # 当年（2025/26）AV 85,000 < 不可追回 120,000 → 当年扣 85,000、超额 35,000
    # 自最近租金足够的以前年度（2024/25，AV 236,000）回扣（irrecoverable.htm
    # Example 2，T1 佐证；T3 复算见下）。
    current = _property("prop-1", "85000", irrecoverable_rent="120000")
    preceding = [
        {
            "year_of_assessment": "2024_25",
            "property_id": "prop-1",
            "rent_received": "236000",
            "rates_paid_by_owner": _paid_rates("0"),
            "irrecoverable_rent": "0",
        }
    ]

    # —— 缺以前年度事实 → needs_input（不得改向未来年度或静默丢弃）——
    missing = calculate(_facts("2025_26", current), bundle)
    assert missing["status"] == "needs_input", missing
    assert missing.get("questions"), "超额回扣须追问目标以前年度 AV/差饷/已评税额事实"
    assert all(v is None for v in missing["amounts"].values()), missing["amounts"]

    # —— 补齐以前年度事实 → 修订评税/退税（T3 复算）：
    #   2024/25 原税 = 236,000×0.8×15% = 28,320
    #   修订 = (236,000−35,000)×0.8×15% = 160,800×15% = 24,120 → 退税 4,200 ——
    result = calculate(
        _facts("2025_26", current, preceding_year_property_facts=preceding), bundle
    )
    revision = result["preceding_year_revision"]
    assert revision["year_of_assessment"] == "2024_25", revision
    assert revision["original_tax"] == "28320", revision
    assert revision["revised_tax"] == "24120", revision
    assert revision["refund"] == "4200", revision
    # 当年 AV 全额被不可追回租金吸收 → 当年税 0
    assert result["amounts"]["final_after_reduction"] == "0", result["amounts"]


def test_property_per_property_assessment_unit_floor_chain() -> None:
    """评税单位＝逐项物业（BIR57 Note 1(b)/pty.htm Q4）：逐物业 floor(NAV)→floor(NAV×15%)。"""
    calculate, bundle = _calc()

    # —— 台账反例数值（D5.0/D6.2a）：两物业 AV 各 17 → 各自 NAV raw 13.6
    #    → floor 13 → 税 floor(13×15%) = floor(1.95) = 1；合计 2 ——
    pair = calculate(
        _facts("2025_26", _property("p-a", "17"), _property("p-b", "17")), bundle
    )
    assert [p["tax"] for p in pair["properties"]] == ["1", "1"], pair["properties"]
    assert pair["amounts"]["before_reduction"] == "2", pair["amounts"]

    # —— 单物业小数链（区分 floor/round）：AV 125,012.50 → NAV raw 100,010
    #    → floor 100,010 → 税 floor(15,001.50) = 15,001（round=15,002）——
    single = calculate(_facts("2025_26", _property("p-c", "125012.50")), bundle)
    assert single["properties"][0]["nav"] == "100010", single["properties"]
    assert single["properties"][0]["tax"] == "15001", single["properties"]


def test_property_aggregate_vs_per_property_rounding_not_conflated() -> None:
    """聚合顺序不恒等：逐物业合计 2 ≠ 聚合一次取整 4；不得把 PA 聚合观察
    （floor 作用于输入聚合字段）外推为直接物业税评税单位（D5.0）。"""
    calculate, bundle = _calc()
    result = calculate(
        _facts("2025_26", _property("p-a", "17"), _property("p-b", "17")), bundle
    )
    # 逐物业：floor(13.6)=13 → floor(1.95)=1；两物业合计 2
    per_property_taxes = [p["tax"] for p in result["properties"]]
    assert per_property_taxes == ["1", "1"], result["properties"]
    assert result["amounts"]["before_reduction"] == "2", result["amounts"]
    # 聚合一次（错误路径，仅作反例记录）：floor(13.6+13.6)=27 → floor(4.05)=4
    assert result["amounts"]["before_reduction"] != "4", (
        "总额必须等于逐物业评税之和（2），不得按输入聚合字段一次取整（4）"
    )
    # 明细必须逐物业可审计（评税单位暴露）
    assert [p["property_id"] for p in result["properties"]] == ["p-a", "p-b"]


def test_property_deposit_offsets_same_debt_once() -> None:
    """按金抵销仅限不可追回部分（Q27/BIR57 Note 3(b)(i)）：同一债务只扣一次。

    按金抵欠租的 10,000 不视为不可追回（不重复扣除）；不可追回扣除额＝
    40,000 − 10,000 = 30,000。
    """
    calculate, bundle = _calc()
    # 手算（T3）：AV 300,000；扣除 30,000 → 270,000 → 20% 免税额 54,000
    #   → NAV = 216,000 → 税 32,400
    #   （若把按金抵销的 10,000 也按不可追回再扣一遍：NAV=208,000→31,200，可区分）
    facts = _facts(
        "2025_26",
        _property(
            "prop-1",
            "300000",
            irrecoverable_rent="40000",
            deposit_offsets="10000",
        ),
    )
    result = calculate(facts, bundle)
    assert result["properties"][0]["nav"] == "216000", result["properties"]
    assert result["properties"][0]["tax"] == "32400", result["properties"]


def test_property_recovery_in_recovery_year() -> None:
    """s.7C(2)/Q27：先前已扣不可追回租金于追回年度计回收入。"""
    calculate, bundle = _calc()
    # 手算（T3）：追回年度 AV = 200,000 + 60,000 = 260,000 → NAV = 208,000
    #   → 税 31,200（若漏计追回：24,000，可区分）
    facts = _facts(
        "2025_26",
        _property(
            "prop-1",
            "200000",
            recovered_previously_deducted_rent="60000",
        ),
    )
    result = calculate(facts, bundle)
    assert result["properties"][0]["nav"] == "208000", result["properties"]
    assert result["properties"][0]["tax"] == "31200", result["properties"]
    assert result["amounts"]["final_after_reduction"] == "31200", result["amounts"]
    # 追回事实已显式 → 无须追问（questions 为空）
    assert not result.get("questions"), result.get("questions")
