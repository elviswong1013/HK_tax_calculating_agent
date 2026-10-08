"""REQ-4 利得税引擎（M2 Red；Annex D D4 为税法语义权威）。

REQ: REQ-4（SDD §9：法团/非法团与两级制资格显式确认；应评税利润为确认事实；
普通合伙可算并明示不等同合伙人个人税；详见 Annex D D4）。
规格锚点:
  - SDD §6 利得税行＋§9 REQ-4；Annex D D4.1（税率）/D4.2（两级制资格与关联）/
    D4.3（合伙业务层合同＋取整）/D4.4（事实字段）。
  - Annex C C3.5 利得税唯一规范模型：entity_kind ∈ {corporation, partnership,
    sole_proprietorship}；partnership 另带 partners[]{partner_id, partner_kind,
    share:RatioStr}；two_tier{connected_entities[]{entity_id, basis_period_end,
    control_basis}, no_other_election_same_year（三态）, election_made（三态）}。
  - 税率：法团 8.25%/16.5%、非法团 7.5%/15%、首个 $2,000,000（2018/19 起）＝
    T1-observed（2tr.htm；台账 1.12）。
  - 关联判定以基期结束时点；控制 >50%；无关联须声明（Q13）；任一资格事实
    unknown → 追问、不得默认＝D4.2（T1-observed 2tr.htm Q2–Q13）。
  - 合伙业务层合同（D4.3）：以前年度亏损先抵业务层再适用两级门槛；混合合伙
    门槛＝2,000,000×利润分享比例（按 partner_kind 聚合）；PA 相关税＝业务税×
    份额比例；PST 按抵亏前应评税利润计（不得复用已消耗亏损）。2trexample
    Q7（612,000）/Q8（270,000/54,000/216,000/暂缴毛额 375,000）＝T2 历史例
    （2018/19 假设、不含当年一次性宽减；整体保留作结构验证，不得当前化）。
  - 取整：非法团＝三年度官方计算器 floor 链（择档 floor(净利)≤2m → 低档对
    全部 raw 净利×7.5%；税额 raw 后 floor；PFRebate ceil 后再 floor）＝
    T1-observed（台账 1.11/D6.2a）；法团/混合＝产品估算精度约定（最终税额
    floor 至整元、择档 floor(计税利润)、宽减 ceil 后扣；未经 IRD 文字证明，
    须带「产品约定」标识）＝D4.3/D10（用户 Gate2 已批）。
  - 附表 43 宽减：2024/25 100%/$1,500；2025/26 100%/$3,000；2026/27 无条目
    （每业务一个宽减，s.100(3)）＝T1（台账 1.17）。
期望值来源: 税率/门槛数值 T1-observed（2tr.htm）＋附表 43 T1；Q7/Q8 结构数值
  ＝台账 T2 历史例整体保留；floor 边界值（2,000,000.01/2,000,001.00/2,666.67/
  200,010）＝D6.2a/D4.3 记录的计算器观察与复审反例＋T3 独立手算（算式见
  各测试注释）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.profits.calculate_profits_tax(facts: Mapping, bundle: Mapping)
    -> dict：纯函数、注入不可变 RuleBundle；facts 字段名＝C3.5/D4.4 完全
    同名同构（entity_kind/assessable_profit/partners[]/two_tier/
    loss_brought_forward/partner_assessable_allocations[]{partner_id,
    business_id, assessable_amount}/pa_election_partners[]）；
    share 为 RatioStr（num/den，gcd 规范形，Σ＝1）。
  - 返回信封同薪俸（status/amounts 六键/questions[]）；资格事实 unknown 或
    缺失 → status="needs_input"＋questions[]（不得默认符合/不符合）。
  - 结果携带 two_tier_allocation[]（混合/合伙时）：每 {partner_kind,
    threshold, low_tier_tax, high_tier_tax} 一项——门槛按 partner_kind 聚合
    （2,000,000×该类合计分享比例），不得按合伙人逐个拆档。
  - 存在 pa_election_partners 时另带 pa_related_tax／remaining_business_tax
    （税前结构拆分：业务税 × PA 份额比例 / 余额，D4.3）。
  - 法团/混合取整＝产品约定：结果 pending_verification[] 须含「产品约定」
    标识（标注未经 IRD 文字证明适用于法团评税）；纯非法团结果不得携带该
    标识（其 floor 链＝T1-observed，非产品约定）。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 失败原因＝缺失
    app.engines.profits 模块。
"""

from __future__ import annotations

import json


def _calc():
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.profits）。"""
    from app.engines.profits import calculate_profits_tax
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_profits_tax, INITIAL_BUNDLE_CONTENT


def _confirmed_two_tier() -> dict:
    """已确认的两级制资格事实：无关联实体、已声明选择、无其他关联实体选择。"""
    return {
        "connected_entities": [],
        "election_made": {"value": True},
        "no_other_election_same_year": {"value": True},
    }


def _entity(year: str, kind: str, profit: str, **extra) -> dict:
    facts = {
        "year_of_assessment": year,
        "entity_kind": kind,
        "assessable_profit": profit,
        "two_tier": _confirmed_two_tier(),
    }
    facts.update(extra)
    return facts


def _assert_needs_input(result: dict) -> None:
    assert result["status"] == "needs_input", (
        f"资格事实缺失/unknown 必须 needs_input（不得默认），实际 {result.get('status')!r}"
    )
    assert isinstance(result.get("questions"), list) and result["questions"], (
        "needs_input 必须携带非空中文 questions[]"
    )
    amounts = result["amounts"]
    assert all(v is None for v in amounts.values()), (
        f"needs_input 不得输出任何金额（C3.3），实际：{amounts!r}"
    )


def test_profits_corporate_vs_unincorporated() -> None:
    """两级制税率：法团 8.25/16.5 vs 非法团 7.5/15；首个 2m 门槛（T1-observed）。"""
    calculate, bundle = _calc()
    # 手算（2tr.htm 税率结构；2026/27 无宽减条目 → final＝before）：
    #   法团 2,000,000：2m×8.25% = 165,000
    #   非法团 2,000,000：2m×7.5% = 150,000
    #   法团 3,000,000：2m×8.25% + 1m×16.5% = 165,000 + 165,000 = 330,000
    #   非法团 3,000,000：2m×7.5% + 1m×15% = 150,000 + 150,000 = 300,000
    cases = [
        ("corporation", "2000000", "165000"),
        ("sole_proprietorship", "2000000", "150000"),
        ("corporation", "3000000", "330000"),
        ("sole_proprietorship", "3000000", "300000"),
    ]
    for kind, profit, expected in cases:
        result = calculate(_entity("2026_27", kind, profit), bundle)
        amounts = result["amounts"]
        assert amounts["before_reduction"] == expected, (kind, profit, amounts)
        assert amounts["reduction"] == "0", (kind, profit, amounts)
        assert amounts["final_after_reduction"] == expected, (kind, profit, amounts)


def test_profits_two_tier_confirmed_gating() -> None:
    """两级制资格门控：关联实体按基期末判定；无关联须显式确认；任一事实
    unknown → needs_input；无选择＝事实分支按全额税率（不是错误）。"""
    calculate, bundle = _calc()

    # —— two_tier 资格事实缺失（Q13：无关联也须声明）→ needs_input ——
    missing = _entity("2026_27", "corporation", "2000000")
    del missing["two_tier"]
    _assert_needs_input(calculate(missing, bundle))

    # —— election_made unknown → needs_input（不得默认已选/未选）——
    unknown_election = _entity("2026_27", "corporation", "2000000")
    unknown_election["two_tier"]["election_made"] = {"value": "unknown"}
    _assert_needs_input(calculate(unknown_election, bundle))

    # —— no_other_election_same_year unknown → needs_input ——
    unknown_other = _entity("2026_27", "corporation", "2000000")
    unknown_other["two_tier"]["no_other_election_same_year"] = {"value": "unknown"}
    _assert_needs_input(calculate(unknown_other, bundle))

    # —— 存在关联实体（基期末＋控制理由）且选择已确认 → 两级制适用于本实体 ——
    connected = _entity("2026_27", "corporation", "2000000")
    connected["two_tier"]["connected_entities"] = [
        {
            "entity_id": "conn-1",
            "basis_period_end": "2026-03-31",
            "control_basis": "直接持有 >50% 已发行股本",
        }
    ]
    amounts = calculate(connected, bundle)["amounts"]
    assert amounts["before_reduction"] == "165000", amounts  # 2m×8.25%

    # —— election_made=false（无效选择/未选择）＝事实分支 → 全额 16.5% ——
    no_election = _entity("2026_27", "corporation", "2000000")
    no_election["two_tier"]["election_made"] = {"value": False}
    result_no = calculate(no_election, bundle)
    assert result_no["amounts"]["before_reduction"] == "330000", result_no["amounts"]
    assert not result_no.get("questions"), "无选择是事实分支，不是待追问状态（D4.2）"


def test_partnership_q7_612000() -> None:
    """混合合伙门槛按利润分享比例分摊（2trexample Q7 结构验证，T2 历史例）。

    5,000,000 利润：法团 20%（门槛 400,000，8.25/16.5）＋ 两名个人合计 80%
    （门槛 1,600,000，7.5/15）→ 33,000+120,000+99,000+360,000 = 612,000。
    个人必须按 80% 聚合为一档，不得拆成两个 40%（各 800,000 门槛）。
    """
    calculate, bundle = _calc()
    facts = _entity(
        "2025_26",
        "partnership",
        "5000000",
        partners=[
            {"partner_id": "p-corp", "partner_kind": "corporation", "share": "1/5"},
            {"partner_id": "p-ind-1", "partner_kind": "individual", "share": "2/5"},
            {"partner_id": "p-ind-2", "partner_kind": "individual", "share": "2/5"},
        ],
    )
    result = calculate(facts, bundle)

    # —— 门槛分摊结构：按 partner_kind 聚合 ——
    allocation = {e["partner_kind"]: e for e in result["two_tier_allocation"]}
    assert set(allocation.keys()) == {"corporation", "individual"}, allocation
    assert allocation["corporation"]["threshold"] == "400000"  # 2m×20%
    assert allocation["corporation"]["low_tier_tax"] == "33000"  # 400,000×8.25%
    assert allocation["corporation"]["high_tier_tax"] == "99000"  # 600,000×16.5%
    # 两名个人 2/5+2/5=4/5 合并为单一非法团门槛 1,600,000（不拆两个 800,000）
    assert allocation["individual"]["threshold"] == "1600000", (
        "个人组门槛＝2m×80%（聚合），不得按合伙人逐个拆档（D4.3）",
        allocation,
    )
    assert allocation["individual"]["low_tier_tax"] == "120000"  # 1,600,000×7.5%
    assert allocation["individual"]["high_tier_tax"] == "360000"  # 2,400,000×15%

    # —— 业务层合计（Q7 锚值）：33,000+120,000+99,000+360,000 = 612,000 ——
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "612000", amounts
    # 2025/26 宽减＝每业务一个（s.100(3)）：min(612,000, 3,000) = 3,000
    assert amounts["reduction"] == "3000", amounts
    assert amounts["final_after_reduction"] == "609000", amounts


def test_partnership_q8_loss_offset_then_pa_transfer_270000_54000_216000() -> None:
    """Q8（T2 历史例）：亏损先抵业务层再适用两级门槛；PA 相关税按份额比例拆分。

    3,500,000 − 700,000 = 2,800,000 → 2m×7.5% + 800,000×15% = 270,000；
    PA 伙伴份额 560,000（1/5）→ PA 相关税 54,000、余 216,000；
    下年度暂缴按抵亏前 3,500,000 计：375,000（不得复用已消耗的 700,000）。
    """
    calculate, bundle = _calc()
    partners = [
        {"partner_id": f"p-{i}", "partner_kind": "individual", "share": "1/5"}
        for i in range(5)
    ]
    # 分配表口径＝抵亏后计税利润 B 的份额（Σ＝2,800,000；不含亏损，D4.3）
    allocations = [
        {"partner_id": f"p-{i}", "business_id": "biz-1", "assessable_amount": "560000"}
        for i in range(5)
    ]
    facts = _entity(
        "2026_27",
        "partnership",
        "3500000",
        partners=partners,
        loss_brought_forward="700000",
        partner_assessable_allocations=allocations,
        pa_election_partners=["p-0"],
    )
    result = calculate(facts, bundle)

    amounts = result["amounts"]
    assert amounts["before_reduction"] == "270000", amounts  # 2.8m 两级：150,000+120,000
    assert amounts["reduction"] == "0", amounts  # 2026/27 无宽减条目
    assert amounts["final_after_reduction"] == "270000", amounts
    assert result["pa_related_tax"] == "54000", (  # 270,000 × 560,000/2,800,000
        "PA 相关税＝业务税×PA 伙伴应评税份额比例（D4.3）",
        result,
    )
    assert result["remaining_business_tax"] == "216000", result  # 余以合伙名义征收
    # 暂缴毛额按抵亏前利润计：2m×7.5% + 1.5m×15% = 375,000
    assert amounts["next_provisional"] == "375000", amounts


def test_profits_unincorporated_final_floor_three_year_calculator_convention() -> None:
    """非法团 floor 链（三年度计算器观察）：择档 floor(净利)≤2m → 全部 raw×7.5%；
    税额 raw 计算后 floor；三年度（2024/25–2026/27）同构。"""
    calculate, bundle = _calc()
    # 手算（D6.2a 条件分支；净利 ≤2m 时低档对全部 raw 净利×7.5%）：
    #   2,000,000.01：floor=2,000,000 ≤ 2m → 低档全率 2,000,000.01×7.5%
    #     = 150,000.00075 → floor 150,000（跨界区间低档全率；min/max 线性式禁作 oracle）
    #   2,000,001.00：floor=2,000,001 > 2m → 高档 2m×7.5% + 1×15%
    #     = 150,000.15 → floor 150,000
    #   2,666.67：×7.5% = 200.00025 → floor 200（ceil=201，可区分方向）
    #   宽减：2024/25 cap 1,500 → 148,500；2025/26 cap 3,000 → 147,000；2026/27 0
    cases = [
        ("2026_27", "2000000.01", "150000", "0", "150000"),
        ("2026_27", "2000001.00", "150000", "0", "150000"),
        ("2026_27", "2666.67", "200", "0", "200"),
        ("2024_25", "2000000.01", "150000", "1500", "148500"),
        ("2025_26", "2000000.01", "150000", "3000", "147000"),
    ]
    for year, profit, before, reduction, final in cases:
        result = calculate(_entity(year, "sole_proprietorship", profit), bundle)
        amounts = result["amounts"]
        assert amounts["before_reduction"] == before, (year, profit, amounts)
        assert amounts["reduction"] == reduction, (year, profit, amounts)
        assert amounts["final_after_reduction"] == final, (year, profit, amounts)
        # 纯非法团＝T1-observed 已闭，不得携带法团/混合的「产品约定」标识（D4.3）
        assert "产品约定" not in json.dumps(result, ensure_ascii=False), (
            "非法团取整＝官方计算器观察（已闭），非产品约定；标识仅限法团/混合",
            year,
            profit,
        )


def test_profits_corporate_final_rounding_product_convention_pending_user_gate2() -> None:
    """法团/混合最终取整＝产品估算精度约定（floor 至整元）＋「产品约定」标识。

    法团侧无适用官方证据（BIR51/52「excluding cents」仅申报精度）；产品约定：
    择档 floor(计税利润)、税额 raw 后 floor、宽减 ceil 后扣；结果须标注
    「产品约定、未经 IRD 文字证明适用于法团评税」（D4.3/D10，Gate2 已批）。
    """
    calculate, bundle = _calc()

    # —— 复审反例（D4.3）：200,010×8.25% = 16,500.825 → floor 16,500
    #    （round/ceil=16,501，可区分方向；整元官方例不可区分——故须产品约定）——
    corp = calculate(_entity("2026_27", "corporation", "200010"), bundle)
    assert corp["amounts"]["before_reduction"] == "16500", corp["amounts"]
    assert corp["amounts"]["final_after_reduction"] == "16500", corp["amounts"]

    # —— 择档 floor(计税利润)：2,000,000.01 → floor 2,000,000 ≤ 2m → 低档全率
    #    2,000,000.01×8.25% = 165,000.000825 → floor 165,000 ——
    corp_bracket = calculate(
        _entity("2026_27", "corporation", "2000000.01"), bundle
    )
    assert corp_bracket["amounts"]["before_reduction"] == "165000", (
        corp_bracket["amounts"]
    )

    # —— 法团/混合结果必须携带「产品约定」标识（未经 IRD 文字证明）——
    for result in (corp, corp_bracket):
        assert "产品约定" in json.dumps(
            result.get("pending_verification", []), ensure_ascii=False
        ), "法团取整＝产品精度约定，pending_verification 须含「产品约定」标识"

    # —— 混合合伙（Q7 结构）同属产品约定范围（D4.3：混合按比例门槛分支）——
    mixed = calculate(
        _entity(
            "2026_27",
            "partnership",
            "5000000",
            partners=[
                {"partner_id": "p-corp", "partner_kind": "corporation", "share": "1/5"},
                {"partner_id": "p-ind-1", "partner_kind": "individual", "share": "2/5"},
                {"partner_id": "p-ind-2", "partner_kind": "individual", "share": "2/5"},
            ],
        ),
        bundle,
    )
    assert "产品约定" in json.dumps(
        mixed.get("pending_verification", []), ensure_ascii=False
    ), "混合合伙取整＝产品精度约定（D4.3），须含「产品约定」标识"
