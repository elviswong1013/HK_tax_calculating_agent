"""独立证据校验（SDD §5 管线阶段 5；Annex C C7.5a/C8.1；REQ-18）。

REQ: 18（§9：候选参数对照独立来源事实核验；不得以同一坏候选在两个引擎互证；
T2 须先晋升 T1）。
规格锚点:
  - SDD §5 管线阶段 5：候选参数对照独立来源事实（≠产生变更的同一来源/候选）；
    缺失 → evidence_missing 隔离。
  - Annex C C7.5a：独立证明＝IRD 法例＋官方已刊指南，非两 URL 重复。
  - Annex C C8.1：独立参考参数只来自已批准独立 source/proof，与候选参数对象
    物理分离；两个引擎一致本身不构成证据。
期望值来源: 结构性契约（独立性判定：来源/URL/层级），无金额期望
（3500/4000 为测试宇宙 marker）。

【拟名】被测契约:
  - validate_change(change, independent_facts) ->
    {"status": "validated"|"evidence_missing"|"conflict", "reason": str|None}
    判定：事实须与变更同 fullpath、数值一致，且来源独立（source_id 与 URL 均
    不同于产生变更的来源）、tier="T1"（T2 未晋升不算）→ validated；无此类
    事实 → evidence_missing；独立 T1 事实存在但数值不一致 → conflict。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


def _host(url: Any) -> str | None:
    """URL 主机名（小写、去 `www.`）；缺失/不可解析 → None。"""
    if not isinstance(url, str) or not url:
        return None
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _is_independent(fact: Mapping[str, Any], change: Mapping[str, Any]) -> bool:
    """来源独立：source_id 不同 **且** 主机不同（C7.5a 非 URL 重复）。

    H2：仅比较 source_id/URL 会把同一站点（如 www.ird.gov.hk 的两个页面）
    当作独立来源，站点级污染即可自我印证。独立性按主机判定；任一侧 URL
    缺失时 fail closed（不构成独立证据）。
    """
    if fact.get("source_id") == change.get("source_id"):
        return False
    fact_host = _host(fact.get("url"))
    change_host = _host(change.get("url"))
    if fact_host is None or change_host is None:
        return False
    return fact_host != change_host


def validate_change(
    change: Mapping[str, Any],
    independent_facts: Sequence[Mapping[str, Any]],
) -> dict[str, str | None]:
    """候选单条变更的独立事实核验（validated/evidence_missing/conflict）。"""
    fullpath = change.get("fullpath")
    relevant = [
        fact
        for fact in independent_facts or []
        if fact.get("fullpath") == fullpath
    ]
    independent_t1 = [
        fact
        for fact in relevant
        if _is_independent(fact, change) and fact.get("tier") == "T1"
    ]
    if not independent_t1:
        return {
            "status": "evidence_missing",
            "reason": (
                f"无独立来源 T1 事实核验 fullpath={fullpath!r}"
                "（同源/未晋升 T2 不算数，Annex C C7.5a/C8.1 禁同坏候选互证）"
            ),
        }
    conflicting = [fact for fact in independent_t1 if fact.get("value") != change.get("value")]
    if conflicting:
        fact_ids = sorted(str(fact.get("fact_id")) for fact in conflicting)
        return {
            "status": "conflict",
            "reason": (
                f"独立 T1 事实与候选值冲突（来源冲突隔离）：{fact_ids}"
            ),
        }
    return {"status": "validated", "reason": None}
