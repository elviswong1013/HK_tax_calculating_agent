# -*- coding: utf-8 -*-
"""运行时 guard 适配与准入组合（p1-r2-guard-contract.md §1–§4；GREEN-P 独占）。

依赖方向：routes/security → rules.runtime → rules.admission；runtime **不取
任何锁**（锁所有权归调用方）、不导 scheduler 实现、不联网、不重试、不解析
notice 文本、不捕获 BaseException。

内部值（非 wire/core/binding 字段）：
  GuardPeriod = {year, status:"available"|"blocked", reason_codes:[str]}
  GuardRead   = {periods:[GuardPeriod], verifiable:bool, warnings:[str]}
  RuntimeAdmission = {availability_context, warnings:[str]}

有限规范化（§2）：ok/trusted_offline→available、blocked→blocked（含
states=[] 合法）；缺席／非对象／未知 mode／states 畸形或矛盾／查询普通异常
→ blocked+GUARD_UNVERIFIABLE（verifiable=false）；reason_codes 固定
sorted＋dedup；仅离线期间产出冻结中文模板提示。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.core.errors import AppError
from app.rules.admission import verify_bundle_admission

_V2_TAX_TYPES = ("salaries_tax", "personal_assessment")
_YEAR_RE = re.compile(r"^(\d{4})_(\d{2})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# 仅接受当前来源已有集合（guard-contract §2）；未知 state 判 malformed。
_KNOWN_GUARD_STATES = frozenset(
    {
        "proposal_not_current",
        "verified_future",
        "related_change_uncertain",
        "expired",
        "unknown",
        "trusted_offline",
    }
)
_BLOCKING_GUARD_STATES = frozenset(
    {"related_change_uncertain", "expired", "unknown"}
)

_OFFLINE_TEMPLATE = (
    "课税年度 {ya}：官方来源暂不可达；依据最近已核验的本地规则快照计算，"
    "不代表最新规则。"
)

_MISSING = object()


def _following_year(year: str) -> str:
    match = _YEAR_RE.fullmatch(year)
    if match is None:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"课税年度键格式非法（YYYY_YY）：{year!r}",
            field="year_of_assessment",
        )
    start = int(match.group(1))
    return f"{start + 1}_{(start + 2) % 100:02d}"


def _stamp_year(instrument_date: Any) -> str:
    match = _DATE_RE.fullmatch(instrument_date) if isinstance(instrument_date, str) else None
    if match is None:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"文书日期非法（YYYY-MM-DD）：{instrument_date!r}",
            field="instrument_date",
        )
    year_part = int(match.group(1))
    month_part = int(match.group(2))
    if not 1 <= month_part <= 12:
        raise AppError(
            "E_PERIOD_NOT_SUPPORTED",
            f"文书日期月份非法：{instrument_date!r}",
            field="instrument_date",
        )
    start = year_part if month_part >= 4 else year_part - 1
    return f"{start}_{(start + 1) % 100:02d}"


def periods_for(tax_type: str, facts: Mapping) -> tuple[str, ...]:
    """目标期间表：两税＝目标年＋紧邻下一年；五税＝目标年（印花按 4/1 边界）。"""
    if not isinstance(facts, Mapping):
        raise AppError("E_INPUT_TYPE", "事实必须为对象以定位期间", field="facts")
    if tax_type in _V2_TAX_TYPES:
        year = facts.get("year_of_assessment")
        if not isinstance(year, str) or not year:
            raise AppError(
                "E_PERIOD_NOT_SUPPORTED",
                f"{tax_type}：缺少 year_of_assessment，无法定位期间",
                field="year_of_assessment",
            )
        return (year, _following_year(year))
    if tax_type in ("profits_tax", "property_tax"):
        year = facts.get("year_of_assessment")
        if not isinstance(year, str) or not year:
            raise AppError(
                "E_PERIOD_NOT_SUPPORTED",
                f"{tax_type}：缺少 year_of_assessment，无法定位期间",
                field="year_of_assessment",
            )
        return (year,)
    if tax_type in ("stamp_property", "stamp_stock", "stamp_lease"):
        return (_stamp_year(facts.get("instrument_date")),)
    raise AppError(
        "E_PERIOD_NOT_SUPPORTED",
        f"未知税项：{tax_type!r}（不在已批准期间表内）",
        field="tax_type",
    )


def _normalize_verdict(verdict: Any) -> tuple[str, list[str], bool]:
    """单期判定规范化 → (status, sorted_reason_codes, period_verifiable)。"""
    if not isinstance(verdict, Mapping):
        return "blocked", ["GUARD_MALFORMED", "GUARD_UNVERIFIABLE"], False
    mode = verdict.get("mode")
    states_raw = verdict.get("states", _MISSING)
    if states_raw is _MISSING:
        states_raw = []
    if not isinstance(states_raw, list):
        return "blocked", ["GUARD_MALFORMED", "GUARD_UNVERIFIABLE"], False
    states: list[str] = []
    for item in states_raw:
        if not isinstance(item, str) or item not in _KNOWN_GUARD_STATES:
            return "blocked", ["GUARD_MALFORMED", "GUARD_UNVERIFIABLE"], False
        states.append(item)
    if mode == "ok":
        if any(
            state in _BLOCKING_GUARD_STATES or state == "trusted_offline"
            for state in states
        ):
            return "blocked", ["GUARD_MALFORMED", "GUARD_UNVERIFIABLE"], False
        reasons = ["GUARD_OK"] + [f"GUARD_STATE_{state.upper()}" for state in states]
        return "available", sorted(set(reasons)), True
    if mode == "trusted_offline":
        if any(state in _BLOCKING_GUARD_STATES for state in states):
            return "blocked", ["GUARD_MALFORMED", "GUARD_UNVERIFIABLE"], False
        reasons = ["GUARD_TRUSTED_OFFLINE"] + [
            f"GUARD_STATE_{state.upper()}" for state in states
        ]
        return "available", sorted(set(reasons)), True
    if mode == "blocked":
        reasons = ["GUARD_BLOCKED"] + [
            f"GUARD_STATE_{state.upper()}" for state in states
        ]
        return "blocked", sorted(set(reasons)), True
    return "blocked", ["GUARD_MALFORMED", "GUARD_UNVERIFIABLE"], False


def read_guard(guard: Any, *, tax_type: str, facts: Mapping) -> dict[str, Any]:
    """按期间表查询 guard.availability 并有限规范化（每期恰查询一次，不重试）。

    - guard 缺席／availability 不可调用 → blocked+GUARD_MISSING+UNVERIFIABLE；
    - 查询普通 Exception → blocked+GUARD_QUERY_FAILED+UNVERIFIABLE（不泄露原文）；
    - BaseException 不捕获、原样传播。
    """
    periods = periods_for(tax_type, facts)
    availability = None
    if guard is not None:
        availability = getattr(guard, "availability", None)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    verifiable = True
    for period in periods:
        if not callable(availability):
            status, reasons, period_ok = (
                "blocked",
                ["GUARD_MISSING", "GUARD_UNVERIFIABLE"],
                False,
            )
        else:
            try:
                verdict = availability(period)
            except Exception:
                status, reasons, period_ok = (
                    "blocked",
                    ["GUARD_QUERY_FAILED", "GUARD_UNVERIFIABLE"],
                    False,
                )
            else:
                status, reasons, period_ok = _normalize_verdict(verdict)
        if status == "available" and "GUARD_TRUSTED_OFFLINE" in reasons:
            template = _OFFLINE_TEMPLATE.format(ya=period)
            if template not in warnings:
                warnings.append(template)
        records.append(
            {"year": period, "status": status, "reason_codes": list(reasons)}
        )
        verifiable = verifiable and period_ok
    return {"periods": records, "verifiable": verifiable, "warnings": warnings}


def admit_runtime(
    binding: Mapping,
    bundle: Mapping,
    *,
    tax_type: str,
    facts: Mapping,
    guard_read: Mapping,
) -> dict[str, Any]:
    """组合 current/next 判定与 E 的中立规则核验（不调用引擎）。

    - current 非 available → E_RULES_STATE_UNVERIFIABLE（无能力/不消费）；
    - 两税 next guard 允许但目标年规则不可核验 → missing_rules＋
      RULES_UNVERIFIABLE；next guard 已拒绝/不可核验 → 保留 blocked 判定；
    - 仅 E 明确的规则不可核验错误可局部化 next；其它 AppError 与程序异常
      原样传播（同一对象）。
    """
    periods = list(guard_read.get("periods") or [])
    if not periods:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "规则可用性上下文缺失；拒算（fail-closed）。",
            field="availability",
        )
    current = dict(periods[0])
    if current.get("status") != "available":
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"课税年度 {current.get('year')}：规则可用性不可核验或已阻断；"
            "现行计算已拒绝，不自动推定生效或失效。",
            field="availability.current",
        )
    warnings = list(guard_read.get("warnings") or [])
    if tax_type in _V2_TAX_TYPES:
        # 两税：current 所给年度规则核验（E 明确不可核验错误在此整体拒绝）。
        verify_bundle_admission(
            bundle, tax_type=tax_type, year=current.get("year")
        )
        upcoming = dict(periods[1]) if len(periods) > 1 else None
        if upcoming is not None and upcoming.get("status") == "available":
            try:
                verify_bundle_admission(
                    bundle, tax_type=tax_type, year=upcoming.get("year")
                )
            except AppError as exc:
                if exc.code != "E_RULES_STATE_UNVERIFIABLE":
                    raise
                reasons = sorted(
                    set(
                        list(upcoming.get("reason_codes") or [])
                        + ["RULES_UNVERIFIABLE"]
                    )
                )
                upcoming = {
                    "year": upcoming.get("year"),
                    "status": "missing_rules",
                    "reason_codes": reasons,
                }
        availability_context = {
            "binding": {
                "bundle_id": binding.get("bundle_id"),
                "bundle_hash": binding.get("bundle_hash"),
                "rules_schema_version": binding.get("rules_schema_version"),
                "engine_version": binding.get("engine_version"),
            },
            "current": current,
            "next": upcoming,
        }
    else:
        verify_bundle_admission(bundle, tax_type=tax_type, year=None)
        availability_context = {"current": current}
    return {"availability_context": availability_context, "warnings": warnings}
