"""REQ-7 印花税 — 物业从价印花税（AVD）引擎（M4 Red；Annex D D1 为税法语义权威）。

REQ: REQ-7（SDD §9：住宅/非住宅 AVD 按文书日期选制度；普通文书仅日期即可；
过渡保留按 s78/s79 谓词；计税基数/税率表/取整全部由 bundle 数据驱动；
核证与转易链谓词缺事实 → 追问；初始窗 2024-04-01..2026-10-07）。
规格锚点:
  - SDD §6 AVD 物业行＋§9 REQ-7；Annex D D1.1（日期制度）、D1.2（计税基数
    与取整）、D1.3（完整分档表）、D1.4（端点边界：下开上闭）、D1.5（s78/s79
    过渡谓词，Ord 12/2025 与 Ord 3/2026 原文 PDF，T1）、D1.6（s.29/s.29G
    核证事实模型）、D1.7（已盖章协议后的相关转易契 $100；s.29D 核心）、
    D1.8（事实字段）。
  - Table 8（2024-04-01..2025-02-25，T1 台账 1.9/sd_pty_rates.pdf p3）：
    ≤3,000,000→100；(3m,3,528,240]→100＋10%×超额；(3,528,240,4.5m]→1.5%；
    (4.5m 以上公共高档与 Table 9 相同)。
  - Table 9（2025-02-26..2026-02-25，T1）：≤4,000,000→100；
    (4m,4,323,780]→100＋20%×超额；(4,323,780,4.5m]→1.5%；4.5m 以上同 Table 8。
  - 2026-02-26 起住宅（Ord 3/2026 s14，T1）：Table 9 至 21,739,120；
    (21,739,120,100m]→4.25%；(100m,109,574,470]→4,250,000＋30%；
    >109,574,470→6.5%。非住宅 Scale 3＝Table 9（>21,739,120 一律 4.25%，
    无高端档）。
  - D1.2（T1 GovHK）：基数 B＝max(consideration, value) 精确金额，**不向上
    取整至 100**（1999-04-01 起）；先按 B 选行（区间下开上闭），再对税额
    ceil 至 1 元；禁止取两公式 min/平滑/插值。
  - D1.5（T1）：s78(Ord 12/2025 s3)/s79(3)(Ord 3/2026 s13)：修订前条例保留
    适用于 (a) 生效日前执行的文书、(b) 同一双方＋同一条款＋日前订立的取代
    协议、(c) 符合日前订立协议的转易契；关系谓词 unknown → 追问（不默认）。
  - D1.7/Note (ii)（T1）：买卖协议**已加盖印花**后的相关转易契 → 固定 $100
    （s.29D(2)(a) 母法）；conform 谓词须结构化事实，缺 → 追问。
  - 2026-02-26 起住宅/非住宅分表（Scale 1/2 vs Scale 3）→ property_class
    缺失 → needs_input（2026 窗口）；2025 窗口两表同档、无须类别。
期望值来源:
  - Table 8/9/2026 表全部档值＝T1（台账 1.9；sd_pty_rates.pdf＋GovHK）；
  - 端点期望值（3,000,000.01→101；4,323,780→64,856；4,323,780.01→64,857；
    109,574,470→7,122,341 等）＝表公式＋ceil 规则 T3 独立手算（Annex D
    D1.4 同款算例，可审计复算）；
  - s78/s79 保留链与相关转易契 $100＝T1（Ord 12/2025/Ord 3/2026 原文＋
    sd_pty_rates.pdf Note (ii)）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.stamps.property.calculate_property_avd(
    facts: Mapping, bundle: Mapping) -> dict：纯函数、注入不可变 RuleBundle；
    facts 字段名＝D1.8：instrument_date／instrument_kind ∈
    {agreement_for_sale, conveyance_on_sale}／property_class ∈
    {residential, nonresidential}（2026 窗口必需）／consideration／value
    （B=max 两者）／prior_links[]{supersedes{agreement_date, parties_same
    {value}, terms_same{value}, dated_before_regime_date{value}} 或 conform
    {agreement_date, agreement_duly_stamped{value}}}。
  - 返回信封：status ∈ {complete, partial, needs_input, blocked}；amounts
    恰含 C3.4 六键——duty＝before_reduction＝final_after_reduction
    （canonical 字符串）、reduction="0"（印花税无一次性宽减）、
    provisional_paid/next_provisional/balance 恒 None（无暂缴组件）；
    questions[]（needs_input 时非空中文清单）。
  - 另带 instrument_detail{base_amount, duty}：base_amount＝精确基数 B 的
    canonical 字符串（未取整至 100）；duty＝ceil1 后税额。
  - 谓词 unknown/缺失（property_class 于 2026 窗口、supersedes 关系、
    conform/duly stamped 链）→ status="needs_input"＋中文 questions＋
    amounts 六键一律显式 None（不默认、不拒算整类）。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.engines.stamps.property 模块」。
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
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.stamps.property）。"""
    from app.engines.stamps.property import calculate_property_avd
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_property_avd, INITIAL_BUNDLE_CONTENT


def _facts(
    base: str,
    *,
    date: str,
    property_class: str | None = "residential",
    kind: str = "agreement_for_sale",
    consideration: str | None = None,
    value: str | None = None,
    **extra,
) -> dict:
    facts: dict = {
        "instrument_date": date,
        "instrument_kind": kind,
        "consideration": consideration if consideration is not None else base,
        "value": value if value is not None else base,
    }
    if property_class is not None:
        facts["property_class"] = property_class
    facts.update(extra)
    return facts


def _assert_duty(result: dict, expected: str) -> None:
    assert result["status"] == "complete", result
    amounts = result["amounts"]
    assert amounts["before_reduction"] == expected, amounts
    assert amounts["reduction"] == "0", amounts
    assert amounts["final_after_reduction"] == expected, amounts
    assert result["instrument_detail"]["duty"] == expected, result["instrument_detail"]


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


def test_avd_table8_historical_window_2024() -> None:
    """Table 8（2024 窗口，T1）：首档 100／10% bridge／1.5% 档端点。

    手算（T3；Table 8 公式＋ceil1）：
      B=3,000,000 → 首档（≤3m 上闭）→ 100
      B=3,000,000.01 → (3m,3,528,240] → 100＋10%×0.01=100.001 → ceil → 101
      B=3,528,240 → 同档上端 → 100＋10%×528,240 = 52,924
      B=4,500,000 → (3,528,240,4.5m] → 1.5%×4,500,000 = 67,500
    """
    calc, bundle = _calc()
    cases = [
        ("3000000", "100"),
        ("3000000.01", "101"),
        ("3528240", "52924"),
        ("4500000", "67500"),
    ]
    for base, duty in cases:
        result = calc(_facts(base, date="2024-06-10"), bundle)
        _assert_duty(result, duty)


def test_avd_table9_2025_window() -> None:
    """Table 9（2025-02-26..2026-02-25，T1）：≤4m→100；20% bridge 端点换档。

    手算（T3；Table 9 公式＋ceil1；下开上闭换档）：
      B=4,000,000 → 首档（≤4m）→ 100
      B=4,323,780 → (4m,4,323,780] → 100＋20%×323,780 = 64,856
      B=4,323,780.01 → (4,323,780,4.5m] → 1.5%×B = 64,856.70015 → ceil → 64,857
        （两值不同属区间定义，非矛盾；不取两公式 min／不平滑——D1.4）
    """
    calc, bundle = _calc()
    cases = [
        ("4000000", "100"),
        ("4323780", "64856"),
        ("4323780.01", "64857"),
    ]
    for base, duty in cases:
        result = calc(_facts(base, date="2025-06-10"), bundle)
        _assert_duty(result, duty)


def test_avd_2026_residential_high_bands() -> None:
    """2026-02-26 起住宅高端档（Ord 3/2026 s14，T1）：4.25%／4,250,000+30%／6.5%。

    手算（T3；2026 住宅表＋ceil1）：
      B=100,000,000 → (21,739,120,100m] 上端 → 4.25%×1e8 = 4,250,000
      B=100,000,000.01 → (100m,109,574,470] → 4,250,000＋30%×0.01=4,250,000.003
        → ceil → 4,250,001
      B=109,574,470 → 同档上端 → 4,250,000＋30%×9,574,470 = 7,122,341
      B=110,000,000 → >109,574,470 → 6.5%×1.1e8 = 7,150,000
    """
    calc, bundle = _calc()
    cases = [
        ("100000000", "4250000"),
        ("100000000.01", "4250001"),
        ("109574470", "7122341"),
        ("110000000", "7150000"),
    ]
    for base, duty in cases:
        result = calc(_facts(base, date="2026-06-10", property_class="residential"), bundle)
        _assert_duty(result, duty)


def test_avd_2026_nonresidential_no_high_bands() -> None:
    """2026 非住宅 Scale 3（T1）：>21,739,120 一律 4.25%，无住宅高端档。

    手算（T3）：B=110,000,000 → 4.25%×1.1e8 = 4,675,000（住宅路径 7,150,000，
    可区分）；B=109,574,470 → 4.25%×109,574,470 = 4,656,914.975 → ceil → 4,656,915
    （若误套 (100m,109,574,470] 档 4,250,000＋30%×9,574,470 = 7,122,341，可区分）。
    """
    calc, bundle = _calc()
    cases = [
        ("109574470", "4656915"),
        ("110000000", "4675000"),
    ]
    for base, duty in cases:
        result = calc(
            _facts(base, date="2026-06-10", property_class="nonresidential"), bundle
        )
        _assert_duty(result, duty)


def test_avd_property_base_not_rounded_to_100() -> None:
    """基数 B 精确、不向上取整至 100（D1.2；1999-04-01 起 GovHK 原文）。

    手算（T3）：B=max(4,000,000.01, 4,000,000)=4,000,000.01 → 精确选档
    (4m,4,323,780] → 100＋20%×0.01=100.002 → ceil → 101。
    若错误先向上取整至 100：B'=4,000,100 → 100＋20%×100 = 120（可区分）。
    """
    calc, bundle = _calc()
    result = calc(
        _facts(
            "4000000.01",
            date="2025-06-10",
            consideration="4000000.01",
            value="4000000",
        ),
        bundle,
    )
    _assert_duty(result, "101")
    assert result["instrument_detail"]["base_amount"] == "4000000.01", (
        "基数必须按精确金额选档（不向上取整至 100）",
        result["instrument_detail"],
    )


def test_avd_residential_vs_nonresidential_class_required() -> None:
    """2026 窗口住宅/非住宅分表 → property_class 缺失须追问；2025 窗口无须类别。

    2026-02-26 起 Scale 1/2（住宅）与 Scale 3（非住宅）在 >21,739,120 分岔
    （4.25%+高端档 vs 一律 4.25%）——类别不明时不得默认（B=110,000,000 时
    两表差 2,475,000）。
    """
    calc, bundle = _calc()

    # —— 2026 窗口缺 property_class → needs_input（不默认住宅/非住宅）——
    missing = calc(_facts("110000000", date="2026-06-10", property_class=None), bundle)
    _assert_needs_input(missing)

    # —— 2025 窗口（Table 9）两类别同档 → 无类别亦可算（普通文书仅日期）——
    classless = calc(_facts("4000000", date="2025-06-10", property_class=None), bundle)
    _assert_duty(classless, "100")


def test_avd_s78_s79_transition_old_chain() -> None:
    """s78/s79(3) 过渡：同方同条款旧协议链保留旧法；关系 unknown → 追问（D1.5）。

    手算（T3；表值 T1）：
      s78（Ord 12/2025）：2025-06-10 取代协议（B=4,200,000）取代 2024-06-01
        同双方同条款协议 → 修订前 Table 8 (3,528,240,4.5m] → 1.5%×4,200,000
        = 63,000（若按新 Table 9 → 100＋20%×200,000 = 40,100，可区分）。
      s79(3)（Ord 3/2026）：2026-03-10 住宅协议（B=110,000,000）取代
        2026-01-15 同双方同条款协议 → 修订前 Table 9 → 4.25%×1.1e8
        = 4,675,000（2026 新住宅表 → 7,150,000，可区分）。
    """
    calc, bundle = _calc()
    supersedes_s78 = {
        "prior_links": [
            {
                "supersedes": {
                    "agreement_date": "2024-06-01",
                    "parties_same": {"value": True},
                    "terms_same": {"value": True},
                    "dated_before_regime_date": {"value": True},
                }
            }
        ]
    }
    result = calc(_facts("4200000", date="2025-06-10", **supersedes_s78), bundle)
    _assert_duty(result, "63000")

    supersedes_s79 = {
        "prior_links": [
            {
                "supersedes": {
                    "agreement_date": "2026-01-15",
                    "parties_same": {"value": True},
                    "terms_same": {"value": True},
                    "dated_before_regime_date": {"value": True},
                }
            }
        ]
    }
    result_s79 = calc(
        _facts("110000000", date="2026-03-10", property_class="residential", **supersedes_s79),
        bundle,
    )
    _assert_duty(result_s79, "4675000")

    # —— 同双方谓词 unknown → needs_input（不得默认保留旧法或适用新法）——
    unknown_link = {
        "prior_links": [
            {
                "supersedes": {
                    "agreement_date": "2024-06-01",
                    "parties_same": {"value": "unknown"},
                    "terms_same": {"value": True},
                    "dated_before_regime_date": {"value": True},
                }
            }
        ]
    }
    result_unknown = calc(_facts("4200000", date="2025-06-10", **unknown_link), bundle)
    _assert_needs_input(result_unknown)


def test_related_conveyance_duly_stamped_chain_100() -> None:
    """已加盖印花买卖协议后的相关转易契 → 固定 $100（Note (ii)/s.29D(2)(a)，T1）。

    B=5,000,000 的转易契若按 AVD 表应为 0.0225×5,000,000=112,500——相关
    转易契按 conform 链（协议已 duly stamped）课固定 100，与基数无关。
    """
    calc, bundle = _calc()
    facts = _facts(
        "5000000",
        date="2025-06-10",
        kind="conveyance_on_sale",
        prior_links=[
            {
                "conform": {
                    "agreement_date": "2025-03-01",
                    "agreement_duly_stamped": {"value": True},
                }
            }
        ],
    )
    result = calc(facts, bundle)
    _assert_duty(result, "100")
