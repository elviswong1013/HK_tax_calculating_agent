"""REQ-7 印花税 — 香港股票转让引擎（M4 Green；Annex D D2 为税法语义权威）。

REQ: REQ-7（SDD §9：香港股票普通买卖/转让印花税＝强制可算类别（D9），不得
information-only；head 2 已法定化（T1-现行法例文本）；文书清单不全 → 追问，
不给交易总额）。
规格锚点:
  - Head 2（2023-11-17 起现行，T1-现行法例文本 Cap.117 2026-10-08）＋
    D2.2（语义红线）＋D2.3（事实字段与 OPEN）。
  - Contract note（s.19(1) 每份 sold／bought note）：0.1% of the
    consideration **or** of its value（货币代价按代价额、非货币按价值，
    **不是取较高者**——不得移植物业 AVD 的 max）；**每份分别 ceil1**
    （不足 $1 向上取整至 $1）；sold／bought 不合并、不互摊。
  - Voluntary disposition inter vivos：**$5＋0.2% of the value of the
    stock**（按 value，与 contract note 措辞不同）；一份文书一次 ceil1。
  - 其他转让：**$5 每份**；不得替代一般买卖的从价税、不得在每宗买卖
    contract note 从价税上自动再加 $5（D2.2）。
  - D2.3 事实字段：doc_kind ∈ {contract_note_sold, contract_note_bought,
    voluntary_inter_vivos, other_transfer}；consideration／value（按 kind
    取用）；complete（文书清单完整性，三态）；instrument_date。
  - complete 缺失/unknown → needs_input＋中文 questions＋amounts 六键一律
    显式 None（文书清单不全不得给交易总额——D2.2/REQ-7）。
  - 2023-11-17 之前文书规则＝OPEN（D2.3）→ 不猜，E_PERIOD_NOT_SUPPORTED。
  - 规则数值经注入 bundle.data.stamps.stock（app/rules/bundles/stamp_stock.py）。
  - Annex C C3.3/C3.4：status ∈ {complete, partial, needs_input, blocked}；
    amounts 恰六键；本税无暂缴组件（provisional_paid／next_provisional／
    balance 恒 None）、无一次性税务宽减（reduction 恒 "0"）。

【契约】calculate_stock_stamp_duty(facts: Mapping, bundle: Mapping) -> dict：
纯函数、注入不可变 RuleBundle；每份文书独立计税（每 note 分别 ceil1 后
相加，不再统一取整），另带 documents[]{doc_kind, duty}；needs_input 时
documents 税额一律 None。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

from app.core.errors import AppError
from app.core.money import ceil_at_point, format_canonical, parse_amount, parse_ratio

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)

_CONTRACT_NOTE_KINDS = ("contract_note_sold", "contract_note_bought")
_DOC_KINDS = _CONTRACT_NOTE_KINDS + ("voluntary_inter_vivos", "other_transfer")


def calculate_stock_stamp_duty(
    facts: Mapping[str, Any], bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """逐份文书股票转让印花税：每 note／每文书独立 ceil1 后相加；纯函数。"""
    rules = bundle["data"]["stamps"]["stock"]

    # D2.2/D2.3：文书清单完整性三态；缺失/unknown/未完整 → 追问、不给交易总额。
    if _tri_state(facts.get("complete"), "complete") is not True:
        return _needs_input(
            [
                "请确认股票转让文书清单是否完整（每份 sold note／bought note／"
                "转让文书均已列明）；文书清单不完整前不得计算交易总额。"
            ]
        )

    _check_instrument_date(facts, rules)

    raw_documents = facts.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise AppError(
            "E_INPUT_MISSING",
            "缺少 documents[]（每份文书独立计税，D2.2）",
            field="documents",
        )

    contract_rate = parse_ratio(rules["contract_note_rate"])
    voluntary_fixed = _amount(
        rules["voluntary_inter_vivos_fixed"], "voluntary_inter_vivos_fixed"
    )
    voluntary_rate = parse_ratio(rules["voluntary_inter_vivos_rate"])
    other_fixed = _amount(rules["other_transfer_fixed"], "other_transfer_fixed")

    questions: list[str] = []
    documents: list[dict[str, Any]] = []
    total = 0

    for index, raw in enumerate(raw_documents):
        where = f"documents[{index}]"
        if not isinstance(raw, Mapping):
            raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
        kind = raw.get("doc_kind")
        if kind not in _DOC_KINDS:
            raise AppError(
                "E_INPUT_TYPE",
                f"{where}.doc_kind 非法：{kind!r}（D2.3 枚举）",
                field=f"{where}.doc_kind",
            )
        if kind in _CONTRACT_NOTE_KINDS:
            base = _consideration_or_value(raw, where)
            if base is None:  # 缺关键金额事实 → 追问（不默认、不取零）
                questions.append(
                    f"{where}（{kind}）：缺少 consideration／value——请提供该 note "
                    f"的货币代价额或股票价值（head 2：0.1%，非 max）。"
                )
                documents.append({"doc_kind": kind, "duty": None})
                continue
            duty = ceil_at_point(base * contract_rate)
        elif kind == "voluntary_inter_vivos":
            if "value" not in raw:  # 法定按 value，不得改从 consideration
                questions.append(
                    f"{where}（voluntary_inter_vivos）：缺少 value——自愿处置按"
                    f"「value of the stock」计税（$5＋0.2%），请提供股票价值。"
                )
                documents.append({"doc_kind": kind, "duty": None})
                continue
            value = _amount(raw["value"], f"{where}.value")
            duty = ceil_at_point(voluntary_fixed + value * voluntary_rate)
        else:  # other_transfer：$5 每份；有无代价事实均同（不得套从价率）
            duty = ceil_at_point(other_fixed)
        documents.append({"doc_kind": kind, "duty": format_canonical(Decimal(duty))})
        total += duty

    if questions:
        # needs_input 不输出任何金额：documents 税额一律显式 None。
        return _needs_input(
            questions,
            [{"doc_kind": entry["doc_kind"], "duty": None} for entry in documents],
        )

    total_text = format_canonical(Decimal(total))
    return {
        "status": "complete",
        "amounts": {
            "before_reduction": total_text,
            "reduction": "0",  # 印花税无一次性税务宽减
            "final_after_reduction": total_text,
            "provisional_paid": None,  # 无暂缴组件
            "next_provisional": None,
            "balance": None,
        },
        "questions": [],
        "documents": documents,
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }


def _check_instrument_date(facts: Mapping[str, Any], rules: Mapping[str, Any]) -> None:
    """文书日期校验；早于 head 2 现行文本生效日 → E_PERIOD_NOT_SUPPORTED（不猜）。"""
    raw = facts.get("instrument_date")
    if not isinstance(raw, str) or not raw:
        raise AppError("E_INPUT_MISSING", "缺少 instrument_date", field="instrument_date")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise AppError(
            "E_INPUT_TYPE", "instrument_date 须为 YYYY-MM-DD", field="instrument_date"
        ) from exc
    effective_from = date.fromisoformat(rules["effective_from"])
    if parsed < effective_from:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"文书日期 {raw} 早于 head 2 现行文本生效日 {rules['effective_from']}（D2.1）；"
            f"更早期间规则为 OPEN（D2.3），不得猜测。",
            field="instrument_date",
        )


def _tri_state(raw: object, field: str) -> bool | str:
    """C3.5 三态事实：true/false/"unknown"；缺省（None）视为 unknown。"""
    value = raw.get("value") if isinstance(raw, Mapping) else raw
    if value is True or value is False:
        return value
    if value is None or value == "unknown":
        return "unknown"
    raise AppError(
        "E_INPUT_TYPE", f"{field} 三态值须为 true/false/\"unknown\"", field=field
    )


def _consideration_or_value(raw: Mapping[str, Any], where: str) -> Fraction | None:
    """Head 2「consideration or of its value」：货币代价额或股票价值。

    取用规则（D2.2）：有 consideration 用 consideration；否则用 value。
    **不是 max／取较高者**；两者皆缺 → None（追问，不默认取零）。
    """
    if raw.get("consideration") is not None:
        return _amount(raw["consideration"], f"{where}.consideration")
    if raw.get("value") is not None:
        return _amount(raw["value"], f"{where}.value")
    return None


def _amount(value: object, field: str) -> Fraction:
    """C2.1 金额 → 精确有理（金额链路零浮点）。"""
    try:
        text = parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    return Fraction(Decimal(text))


def _needs_input(
    questions: list[str], documents: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """needs_input 信封：amounts 六键一律显式 None（不得输出任何金额）。"""
    return {
        "status": "needs_input",
        "amounts": {key: None for key in _AMOUNT_KEYS},
        "questions": questions,
        "documents": documents if documents is not None else [],
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }
