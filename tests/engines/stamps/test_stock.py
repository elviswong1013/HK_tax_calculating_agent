"""REQ-7 印花税 — 香港股票转让引擎（M4 Red；Annex D D2 为税法语义权威）。

REQ: REQ-7（SDD §9：香港股票普通买卖/转让印花税＝强制可算类别（D9），不得
information-only；head 2 已法定化（T1-现行法例文本）；文书清单不全 → 追问，
不给交易总额）。
规格锚点:
  - SDD §6 AVD 股票行＋§9 REQ-7；Annex D D2.1（First Schedule head 2，
    2023-11-17 起现行，T1-现行法例文本 Cap.117 2026-10-08）＋D2.2（语义
    红线）＋D2.3（事实字段与 OPEN）。
  - Contract note（s.19(1) 每份 sold／bought note）：各 0.1% of the
    consideration **or** of its value（货币代价按代价额、非货币按价值——
    **不是取较高者**，不得移植物业 AVD 的 max）；**每份分别 ceil1**
    （不足 $1 向上取整至 $1）；sold/bought 不合并。
  - Voluntary disposition inter vivos／非买卖实益转让：**$5＋0.2% of the
    value of the stock**（按 value，与 contract note 措辞不同）；一份
    文书一次 ceil1。
  - 其他转让：**$5 每份**；不得替代一般买卖的从价税、不得在每宗买卖
    contract note 从价税上自动再加 $5（D2.2）。
  - 事实字段（D2.3）：doc_kind ∈ {contract_note_sold, contract_note_bought,
    voluntary_inter_vivos, other_transfer}；consideration／value（按 kind
    取用）；role；doc_link；**complete（文书清单完整性，三态）**；
    instrument_date。缺 complete 或关键角色 → 追问（不得给交易总额）。
  - 33 of 2024 s.7 废除原 (2) 项；豁免指针 Sch 8/9/10/11A＝范围外。
期望值来源:
  - 税率/固定额全 T1（head 2 现行文本＋GovHK，台账 1.9/Annex D D2.1）；
  - 端点期望（1,000.01 → 每 note ceil1 后 2；5＋0.2%×1,000.01 → ceil → 8）
    ＝表公式＋ceil1 规则 T3 独立手算（算式见各测试注释，可审计复算）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.engines.stamps.stock.calculate_stock_stamp_duty(
    facts: Mapping, bundle: Mapping) -> dict：纯函数、注入不可变 RuleBundle；
    facts 字段名＝D2.3：instrument_date／complete{value}（三态）／
    documents[]{doc_kind, consideration, value}。
  - 返回信封：status ∈ {complete, partial, needs_input, blocked}；amounts
    恰含 C3.4 六键——合计＝before_reduction＝final_after_reduction、
    reduction="0"（印花税无一次性宽减）、provisional_paid/next_provisional/
    balance 恒 None（无暂缴组件）；questions[]（needs_input 时非空中文清单）。
  - 另带 documents[]{doc_kind, duty}：每份文书（每 note）独立 ceil1 后
    税额；合计＝Σ（每份分别取整后相加，不再统一取整）。
  - complete 缺失/unknown → status="needs_input"＋中文 questions＋amounts
    六键一律显式 None（文书清单不全不得给交易总额——D2.2/REQ-7）。
  - 本文件对 app.engines.* 延迟导入（测试体内），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.engines.stamps.stock 模块」。
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
    """延迟导入被测引擎与规则束（Red 失败原因＝缺失 app.engines.stamps.stock）。"""
    from app.engines.stamps.stock import calculate_stock_stamp_duty
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return calculate_stock_stamp_duty, INITIAL_BUNDLE_CONTENT


def _facts(*documents: dict, date: str = "2025-06-10", complete: bool = True) -> dict:
    return {
        "instrument_date": date,
        "complete": {"value": complete},
        "documents": list(documents),
    }


def _assert_total(result: dict, expected: str) -> None:
    assert result["status"] == "complete", result
    amounts = result["amounts"]
    assert amounts["before_reduction"] == expected, amounts
    assert amounts["reduction"] == "0", amounts
    assert amounts["final_after_reduction"] == expected, amounts


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
        "文书清单不全不得输出任何金额（六键一律显式 None）",
        result["amounts"],
    )


def test_stock_sold_and_bought_notes_ceil_separately() -> None:
    """Contract note：每 sold/bought note 各 0.1% 分别 ceil1（D2.1/D2.2，T1）。

    手算（T3；head 2＋ceil1）：
      代价 1,000.01 → 每 note 0.1% = 1.00001 → 不足 $1 向上取整 → 各 2
      sold 2 ＋ bought 2 = 4（若合并一次计 0.1%×2×1,000.01=2.00002 → ceil 3，
      可区分；若不取整 → 2.00002，可区分）。
    """
    calc, bundle = _calc()
    result = calc(
        _facts(
            {"doc_kind": "contract_note_sold", "consideration": "1000.01"},
            {"doc_kind": "contract_note_bought", "consideration": "1000.01"},
        ),
        bundle,
    )
    _assert_total(result, "4")
    per_doc = {d["doc_kind"]: d["duty"] for d in result["documents"]}
    assert per_doc == {
        "contract_note_sold": "2",
        "contract_note_bought": "2",
    }, result["documents"]


def test_stock_voluntary_transfer_fixed_plus_value_duty() -> None:
    """自愿在生处置：$5＋0.2% of value，一份文书一次 ceil1（D2.1，T1）。

    手算（T3）：value 1,000.01 → 5＋0.2%×1,000.01 = 5＋2.00002 = 7.00002
    → ceil → 8（floor=7／round=7，可区分取整方向）。
    """
    calc, bundle = _calc()
    result = calc(
        _facts({"doc_kind": "voluntary_inter_vivos", "value": "1000.01"}),
        bundle,
    )
    _assert_total(result, "8")
    assert result["documents"][0]["duty"] == "8", result["documents"]


def test_stock_other_transfer_is_5_per_document() -> None:
    """其他转让 $5 每份（D2.1，T1）；有代价事实也不得套从价率。

    若误按 contract note 0.1%：1,000,000×0.1% = 1,000；若误按自愿处置
    5＋0.2%：5＋2,000 = 2,005——均与 $5 可区分。
    """
    calc, bundle = _calc()
    result = calc(
        _facts({"doc_kind": "other_transfer", "consideration": "1000000"}),
        bundle,
    )
    _assert_total(result, "5")
    assert result["documents"][0]["duty"] == "5", result["documents"]


def test_stock_sale_transfer_instrument_not_added_without_document_fact() -> None:
    """买卖 contract note 税额不得自动加 $5 转让文书税（D2.2 红线，T1）。

    无 other_transfer 文书事实 → 合计恰为两 note 之和 4（自动 +5 → 9，可区分）；
    文书清单仅含用户申报的两份 note。
    """
    calc, bundle = _calc()
    result = calc(
        _facts(
            {"doc_kind": "contract_note_sold", "consideration": "1000.01"},
            {"doc_kind": "contract_note_bought", "consideration": "1000.01"},
        ),
        bundle,
    )
    _assert_total(result, "4")
    kinds = [d["doc_kind"] for d in result["documents"]]
    assert kinds == ["contract_note_sold", "contract_note_bought"], (
        "未申报转让文书事实不得自动加入文书清单",
        result["documents"],
    )


def test_stock_incomplete_document_inventory_no_transaction_total() -> None:
    """文书清单完整性（complete）缺失 → 追问，不给交易总额（D2.2/D2.3，T1）。"""
    calc, bundle = _calc()
    facts = _facts(
        {"doc_kind": "contract_note_sold", "consideration": "1000.01"},
        {"doc_kind": "contract_note_bought", "consideration": "1000.01"},
    )
    del facts["complete"]
    result = calc(facts, bundle)
    _assert_needs_input(result)

    unknown = _facts(
        {"doc_kind": "contract_note_sold", "consideration": "1000.01"},
    )
    unknown["complete"] = {"value": "unknown"}
    result_unknown = calc(unknown, bundle)
    _assert_needs_input(result_unknown)
