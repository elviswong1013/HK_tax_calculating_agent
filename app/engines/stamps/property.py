"""REQ-7 印花税 — 物业从价印花税（AVD）引擎（M4 Green；Annex D D1 为税法语义权威）。

REQ: REQ-7（SDD §9：住宅/非住宅 AVD 按文书日期选制度；普通文书仅凭日期；
s78/s79(3) 过渡保留按谓词；计税基数/税率表/取整全部由 bundle 数据驱动；
核证与转易链谓词缺事实 → 追问；初始窗 2024-04-01..2026-10-07）。
规格锚点:
  - D1.1 日期制度：Table 8（2024-04-01..2025-02-25；sd_pty_rates.pdf p3）／
    Table 9（2025-02-26..2026-02-25；Ord 12/2025 s3/s4）／2026-02-26 起
    住宅高端档（Ord 3/2026 s14）与 Scale 3 非住宅；2026 窗类别缺失 → 追问。
  - D1.2：B＝max(consideration, value) 精确金额（1999-04-01 起不向上取整至
    100）；区间下开上闭（Exceeds／Does not exceed）选行后，仅对税额
    ceil 至 1 元；禁止取相邻公式 min／平滑／插值。
  - D1.5：s78（Ord 12/2025 s3）／s79(3)（Ord 3/2026 s13）同双方＋同条款＋
    日前订立的取代协议保留修订前条例；关系谓词 unknown → 追问。
  - D1.7/Note (ii)：已 duly stamped 买卖协议后的相关转易契固定 $100
    （s.29D(2)(a)）；conform 谓词缺／unknown → 追问。
数值与档表经注入 bundle.data.stamps.property（app/rules/bundles/stamp_property.py）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

from app.core.errors import AppError
from app.core.money import ceil_at_point, format_canonical, parse_amount

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)

_INSTRUMENT_KINDS = {"agreement_for_sale", "conveyance_on_sale"}
_PROPERTY_CLASSES = {"residential", "nonresidential"}


def calculate_property_avd(
    facts: Mapping[str, Any], bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """物业 AVD：日期选制度 → B=max(考虑/价值) → 分档公式 → 税额 ceil1；纯函数。"""
    rules = bundle["data"]["stamps"]["property"]
    window = bundle["applicability"]["stamp_duty_instrument_window"]
    window_from = _parse_date(window["from"], "applicability.stamp_duty_instrument_window.from")
    window_to = _parse_date(window["to"], "applicability.stamp_duty_instrument_window.to")
    instrument_date = _required_date(facts, "instrument_date")
    if instrument_date < window_from or instrument_date > window_to:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"文书日期 {instrument_date.isoformat()} 不在印花税支持窗口 "
            f"{window_from.isoformat()}..{window_to.isoformat()}（PRD 初始窗）",
            field="instrument_date",
        )

    kind = facts.get("instrument_kind")
    if kind is None:
        raise AppError(
            "E_INPUT_MISSING", "缺少 instrument_kind", field="instrument_kind"
        )
    if kind not in _INSTRUMENT_KINDS:
        raise AppError(
            "E_INPUT_TYPE",
            "instrument_kind 仅支持 agreement_for_sale／conveyance_on_sale"
            "（交换/法院命令等复杂类别按 PRD 排除）",
            field="instrument_kind",
        )

    property_class = facts.get("property_class")
    if property_class is not None and property_class not in _PROPERTY_CLASSES:
        raise AppError(
            "E_INPUT_TYPE",
            "property_class 须为 residential 或 nonresidential",
            field="property_class",
        )
    regime = _select_regime(rules, instrument_date)
    if regime["requires_property_class"]:
        if property_class is None:
            return _needs_input(
                [
                    "2026-02-26 起住宅（Scale 1/2）与非住宅（Scale 3）分表："
                    "请说明物业类别（residential／nonresidential）——"
                    "类别不明时不得默认（D1.1/D1.3）。"
                ]
            )
        table_name = regime["tables"][property_class]
    else:
        table_name = regime["tables"]["default"]

    missing = [key for key in ("consideration", "value") if facts.get(key) is None]
    if missing:
        return _needs_input(
            [
                "缺少 " + "、".join(missing) + "：计税基数 B＝max(consideration, value) "
                "要求两者并列提供（D1.8；不得默认相等）；请补充后重算。"
            ]
        )
    base = max(
        _amount(facts["consideration"], "consideration"),
        _amount(facts["value"], "value"),
    )
    base_text = _fraction_text(base)

    questions: list[str] = []
    override_table: str | None = None
    fixed_duty: str | None = None
    raw_links = facts.get("prior_links")
    if raw_links is not None:
        if not isinstance(raw_links, list):
            raise AppError("E_INPUT_TYPE", "prior_links 须为数组", field="prior_links")
        for index, link in enumerate(raw_links):
            where = f"prior_links[{index}]"
            if not isinstance(link, Mapping):
                raise AppError("E_INPUT_TYPE", f"{where} 须为对象", field=where)
            has_supersedes = "supersedes" in link
            has_conform = "conform" in link
            if not has_supersedes and not has_conform:
                raise AppError(
                    "E_INPUT_TYPE",
                    f"{where} 须含 supersedes 或 conform 结构化谓词",
                    field=where,
                )
            if has_supersedes:
                table, unresolved = _resolve_supersedes(
                    link["supersedes"], rules, instrument_date, regime, questions, where
                )
                if not unresolved and table is not None:
                    override_table = table
            if has_conform and kind == "conveyance_on_sale":
                duty, unresolved = _resolve_conform(
                    link["conform"], rules, questions, where
                )
                if not unresolved and duty is not None:
                    fixed_duty = duty

    if questions:
        return _needs_input(questions)

    if fixed_duty is not None:
        duty_text = fixed_duty
    else:
        duty_text = _duty_from_table(base, rules["tables"], override_table or table_name)

    return {
        "status": "complete",
        "amounts": {
            "before_reduction": duty_text,
            "reduction": "0",
            "final_after_reduction": duty_text,
            "provisional_paid": None,
            "next_provisional": None,
            "balance": None,
        },
        "questions": [],
        "instrument_detail": {"base_amount": base_text, "duty": duty_text},
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }


def _select_regime(rules: Mapping[str, Any], instrument_date: date) -> Mapping[str, Any]:
    """D1.1：按文书日期选制度（含边界；to=None＝开放）。"""
    for regime in rules["regimes"]:
        start = _parse_date(regime["from"], "regimes[].from")
        end_raw = regime.get("to")
        if start <= instrument_date and (
            end_raw is None
            or instrument_date <= _parse_date(end_raw, "regimes[].to")
        ):
            return regime
    raise AppError(
        "E_PERIOD_NOT_SUPPORTED",
        f"文书日期 {instrument_date.isoformat()} 无适用 AVD 制度表",
        field="instrument_date",
    )


def _resolve_supersedes(
    raw: object,
    rules: Mapping[str, Any],
    instrument_date: date,
    regime: Mapping[str, Any],
    questions: list[str],
    where: str,
) -> tuple[str | None, bool]:
    """s78/s79(3)：同一双方＋同一条款＋日前订立的取代协议 → 保留修订前分档表。

    三谓词缺一不可；任一 unknown／缺失 → 追问（不默认保留旧法或适用新法）。
    返回（修订前表名 或 None，是否 unresolved）。
    """
    field = f"{where}.supersedes"
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    states = {
        key: _tri_state(raw.get(key), f"{field}.{key}")
        for key in ("parties_same", "terms_same", "dated_before_regime_date")
    }
    unknown = [key for key, state in states.items() if state == "unknown"]
    if unknown:
        questions.append(
            f"取代协议关系未确认（{'、'.join(unknown)} 为 unknown）：请确认是否同一双方、"
            f"同一条款、且在制度生效日前订立（s78/s79(3)）——"
            f"确认前不得默认保留旧法或适用新法。"
        )
        return None, True
    if not all(state is True for state in states.values()):
        return None, False  # 谓词明确不成立 → 不适用保留链，按当前制度计算

    raw_date = raw.get("agreement_date")
    if raw_date is None:
        questions.append(
            f"{field}：三谓词均已确认，但缺少 agreement_date（被取代协议的订立日）——"
            f"请补充以确定保留的修订前分档表（s78/s79(3)）。"
        )
        return None, True
    agreement_date = _parse_date(raw_date, f"{field}.agreement_date")
    if agreement_date >= instrument_date:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}.agreement_date={agreement_date.isoformat()} 不早于文书日期 "
            f"{instrument_date.isoformat()}，与取代关系矛盾",
            field=f"{field}.agreement_date",
        )
    transition_raw = regime.get("transition_date")
    if transition_raw is not None and agreement_date >= _parse_date(
        transition_raw, "regimes[].transition_date"
    ):
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}.agreement_date={agreement_date.isoformat()} 不早于制度生效日 "
            f"{transition_raw}，与 dated_before_regime_date=true 矛盾",
            field=f"{field}.agreement_date",
        )
    table = _pre_amendment_table(rules, agreement_date)
    if table is None:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            f"{field}.agreement_date={agreement_date.isoformat()} 不早于 2026-02-26，"
            f"无修订前分档表可保留",
            field=f"{field}.agreement_date",
        )
    return table, False


def _pre_amendment_table(
    rules: Mapping[str, Any], agreement_date: date
) -> str | None:
    """修订前条例按协议订立日保留（<2025-02-26→Table 8；<2026-02-26→Table 9）。"""
    for entry in rules["transitional"]["pre_amendment_tables"]:
        before = _parse_date(
            entry["agreement_before"], "transitional.pre_amendment_tables[].agreement_before"
        )
        if agreement_date < before:
            return entry["table"]
    return None


def _resolve_conform(
    raw: object, rules: Mapping[str, Any], questions: list[str], where: str
) -> tuple[str | None, bool]:
    """s.29D(2)(a)：符合已 duly stamped 买卖协议的相关转易契 → 固定 $100。

    conform 谓词须结构化事实（agreement_date＋agreement_duly_stamped）；缺／
    unknown → 追问；协议未盖章（s.29D(2)(b) 链）超出首版范围 → 追问。
    """
    field = f"{where}.conform"
    if not isinstance(raw, Mapping):
        raise AppError("E_INPUT_TYPE", f"{field} 须为对象", field=field)
    raw_date = raw.get("agreement_date")
    if raw_date is None:
        questions.append(
            f"{field}：缺少 agreement_date（所符合的买卖协议日期）——"
            f"请补充以核验相关转易契 $100 链（s.29D(2)(a)/Note (ii)）。"
        )
        return None, True
    _parse_date(raw_date, f"{field}.agreement_date")
    state = _tri_state(
        raw.get("agreement_duly_stamped"), f"{field}.agreement_duly_stamped"
    )
    if state is True:
        return rules["related_conveyance"]["fixed_duty"], False
    if state == "unknown":
        questions.append(
            f"{field}：买卖协议是否已加盖印花（duly stamped）未确认；"
            f"相关转易契固定 $100 仅适用于已 duly stamped 的协议（s.29D(2)(a)）——"
            f"确认前不得默认 $100 或按 AVD 表计。"
        )
        return None, True
    questions.append(
        f"{field}：协议未 duly stamped → 转易契按 head 1(1) 课税并视作协议作出之日"
        f"执行、协议本身另课 $100（s.29D(2)(b)）；该链超出本引擎首版范围，"
        f"请提供核证后的人工结论。"
    )
    return None, True


def _duty_from_table(
    base: Fraction, tables: Mapping[str, Any], table_name: str
) -> str:
    """按区间（下开上闭）选行 → 套该行公式 → 仅对税额 ceil1（D1.2）。"""
    rows = tables.get(table_name)
    if not isinstance(rows, list) or not rows:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"bundle 缺少 AVD 分档表 {table_name!r}",
            field=f"tables.{table_name}",
        )
    for row in rows:
        upto = row.get("upto")
        if upto is not None and base > _amount(upto, f"tables.{table_name}.upto"):
            continue
        fixed = _amount(row.get("fixed", "0"), f"tables.{table_name}.fixed")
        excess_over = _amount(
            row.get("excess_over", "0"), f"tables.{table_name}.excess_over"
        )
        rate = Fraction(int(row.get("rate_bp", 0)), 10000)
        raw_duty = fixed + rate * (base - excess_over)
        return format_canonical(Decimal(ceil_at_point(raw_duty)))
    raise AppError(
        "E_RULES_STATE_UNVERIFIABLE",
        f"AVD 分档表 {table_name!r} 无匹配行（须以无上限末行收尾）",
        field=f"tables.{table_name}",
    )


def _tri_state(raw: object, field: str) -> bool | str:
    """C3.5 三态事实：true/false/"unknown"；整个谓词缺失（None）视为 unknown。"""
    value = raw.get("value") if isinstance(raw, Mapping) else raw
    if value is True or value is False:
        return value
    if value is None or value == "unknown":
        return "unknown"
    raise AppError(
        "E_INPUT_TYPE", f"{field} 三态值须为 true/false/\"unknown\"", field=field
    )


def _needs_input(questions: list[str]) -> dict[str, Any]:
    """缺事实 → needs_input：金额六键一律显式 None，绝不默认（D1.5/D1.7）。"""
    return {
        "status": "needs_input",
        "amounts": {key: None for key in _AMOUNT_KEYS},
        "questions": questions,
        "instrument_detail": {"base_amount": None, "duty": None},
        "warnings": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }


def _parse_date(value: object, field: str) -> date:
    """事实/数据日期：YYYY-MM-DD 字符串。"""
    if not isinstance(value, str):
        raise AppError(
            "E_INPUT_TYPE", f"{field} 须为 YYYY-MM-DD 字符串", field=field
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(
            "E_INPUT_TYPE", f"{field} 日期格式非法（须为 YYYY-MM-DD）", field=field
        ) from exc


def _required_date(container: Mapping[str, Any], key: str) -> date:
    if key not in container or container[key] is None:
        raise AppError("E_INPUT_MISSING", f"缺少 {key}", field=key)
    return _parse_date(container[key], key)


def _amount(value: object, field: str) -> Fraction:
    """C2.1 金额 → 精确有理（金额链路零浮点）。"""
    try:
        text = parse_amount(value)
    except AppError as exc:
        raise AppError(exc.code, f"{field}：{exc.message_zh}", field=field) from exc
    return Fraction(Decimal(text))


def _fraction_text(value: Fraction) -> str:
    """canonical 字符串（金额分母均为 10 的幂，Decimal 除法精确）。"""
    return format_canonical(Decimal(value.numerator) / Decimal(value.denominator))
