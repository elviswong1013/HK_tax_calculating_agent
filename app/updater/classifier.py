"""候选构建：DATA-only 冻结 schema（SDD §5 管线阶段 4；Annex C C7.1；REQ-18）。

REQ: 18（§9：候选仅限冻结 schema 内 DATA-only 字段；语义变化 → semantic_change
隔离＋变更请求；下载/候选/LLM 内容永不可执行；新语义走变更请求）。
规格锚点:
  - Annex C C7.1 参数槽位二分：auto_parameter_slots＝既有 typed profile 内的
    数值/带日期条目；取整方向/单位/阶段/次序、公式、资格、band 数量/算子＝
    evidence_only_or_frozen_semantics，永不可自动写；frozen 或语义未知槽位
    一律不可写。
期望值来源: 结构性契约（候选差异仅限 data 路径；冻结面零变化），无金额期望
（3000/3500 为测试宇宙 marker）。

【拟名】被测契约:
  - build_candidate(current_content, changes, manifests) ->
    {"status": "candidate"|"quarantined", "content", "quarantine_reason",
     "change_request"}
    changes 元素 {"fullpath", "value", "source_id", "anchor"}；fullpath 必须命中
    某 manifest auto slot（七字段白名单）——未命中 → quarantined
    （semantic_change）＋change_request，不产候选。候选差异仅限 data 内对应
    路径；冻结面与 current 完全一致；current_content 不被就地修改。
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.core.errors import AppError
from app.core.money import parse_amount

_STATUS_CANDIDATE = "candidate"
_STATUS_QUARANTINED = "quarantined"


def _top_segment(fullpath: str) -> str:
    return fullpath.split(".", 1)[0]


def _profiles_for_paths(paths: Sequence[str], manifests: Sequence[Mapping[str, Any]]) -> list[str]:
    """受影响 profile_ID：同首段既有 auto slot 的 profile（无则空）。"""
    profiles: set[str] = set()
    for manifest in manifests:
        for slot in manifest.get("slots", []) or []:
            if any(_top_segment(slot["fullpath"]) == _top_segment(path) for path in paths):
                profiles.add(str(slot["profile_ID"]))
    return sorted(profiles)


def _write_value(content: dict, fullpath: str, value: Any) -> None:
    """按束内规范路径写入 data 分区（fullpath 相对 data 根；允许 data. 前缀）。"""
    parts = fullpath.split(".")
    if parts and parts[0] == "data":
        parts = parts[1:]
        node = content
    else:
        node = content["data"]
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _slot_value_violation(slot: Mapping[str, Any], value: Any) -> str | None:
    """槽位类型/约束校验（C7.1：值域/步长/类型；fail closed）。"""
    slot_type = slot.get("type")
    if slot_type != "decimal_string":
        return f"未支持的槽位类型（fail closed，C7.1）：{slot_type!r}"
    if not isinstance(value, str):
        return "decimal_string 槽位值必须为规范字符串（C2.1）"
    try:
        amount = Decimal(parse_amount(value))
    except (AppError, ValueError, TypeError):
        return f"值不符合 C2.1 金额语法：{value!r}"
    constraints = slot.get("constraints") or {}
    minimum = constraints.get("min")
    maximum = constraints.get("max")
    step = constraints.get("step")
    if minimum is not None and amount < Decimal(str(minimum)):
        return f"值低于约束下限 {minimum}：{value!r}"
    if maximum is not None and amount > Decimal(str(maximum)):
        return f"值超过约束上限 {maximum}：{value!r}"
    if step is not None:
        step_decimal = Decimal(str(step))
        base = Decimal(str(minimum)) if minimum is not None else Decimal("0")
        if step_decimal != 0 and (amount - base) % step_decimal != 0:
            return f"值不满足步长 {step}：{value!r}"
    return None


def build_candidate(
    current_content: Mapping[str, Any],
    changes: Sequence[Mapping[str, Any]],
    manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """构建 DATA-only 候选；语义/schema 外路径 → 隔离＋变更请求，不产候选。"""
    slot_by_path: dict[str, Mapping[str, Any]] = {}
    for manifest in manifests:
        for slot in manifest.get("slots", []) or []:
            slot_by_path[slot["fullpath"]] = slot

    rejected = [change for change in changes if change.get("fullpath") not in slot_by_path]
    if rejected:
        paths = [str(change.get("fullpath")) for change in rejected]
        profiles = _profiles_for_paths(paths, manifests)
        return {
            "status": _STATUS_QUARANTINED,
            "content": None,
            "quarantine_reason": "semantic_change",
            "change_request": {
                "paths": paths,
                "profiles": profiles,
                "reason": (
                    "以下变更未命中任何 manifest auto slot（frozen 或语义未知槽位"
                    "一律不可自动写，Annex C C7.1）：语义变化须走变更请求（CR），"
                    "不得自动发布。"
                ),
            },
        }

    content = copy.deepcopy(dict(current_content))
    for change in changes:
        slot = slot_by_path[change["fullpath"]]
        violation = _slot_value_violation(slot, change.get("value"))
        if violation is not None:
            return {
                "status": _STATUS_QUARANTINED,
                "content": None,
                "quarantine_reason": "schema_violation",
                "change_request": {
                    "paths": [str(change["fullpath"])],
                    "profiles": _profiles_for_paths([str(change["fullpath"])], manifests),
                    "reason": f"候选值违反 slot 类型/约束（fail closed，C7.1）：{violation}",
                },
            }
        _write_value(content, str(change["fullpath"]), change["value"])

    return {
        "status": _STATUS_CANDIDATE,
        "content": content,
        "quarantine_reason": None,
        "change_request": None,
    }
