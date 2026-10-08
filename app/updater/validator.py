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


def _is_independent(fact: Mapping[str, Any], change: Mapping[str, Any]) -> bool:
    """来源独立：source_id 与 URL 均不同于产生变更的来源（C7.5a 非 URL 重复）。"""
    if fact.get("source_id") == change.get("source_id"):
        return False
    change_url = change.get("url")
    if change_url is not None and fact.get("url") == change_url:
        return False
    return True


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
