"""Canonical JSON 与哈希对象封闭字段清单（Annex C C2.7/C2.8；REQ-9）。

- RuleBundleContent（bundle_hash 唯一哈希对象）恰含 6 字段。
- CalculationCore（核心计算记录 hash 唯一哈希对象）恰含 11 字段。
- 实现者不得增删字段；未列入清单的字段按构造在哈希对象之外（封闭清单）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

# C2.8：RuleBundleContent 封闭 6 字段（bundle_id/bundle_hash 自身、发布运行时
# id、抓取/发布时间戳、快照存储字段均不在对象内）
BUNDLE_CONTENT_FIELDS: tuple[str, ...] = (
    "rules_schema_version",
    "effective",
    "applicability",
    "frozen_semantics",
    "data",
    "evidence_digests",
)

# C2.8：CalculationCore 封闭 11 字段（request_id、record_id、job id、墙钟时间戳、
# warnings、questions、分页、health/网络字段等运行时信封完全在对象之外）
CALCULATION_CORE_FIELDS: tuple[str, ...] = (
    "tax_type",
    "input",
    "binding",
    "status",
    "amounts",
    "blocked_reason",
    "steps",
    "evidence_refs",
    "unsupported",
    "pending_verification",
    "missing_components",
)


def canonical_json_bytes(obj: Any) -> bytes:
    """C2.7：键排序、UTF-8、无空白分隔符。"""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_loads(data: bytes | bytearray | str) -> Any:
    """C2.7：解析期拒绝重复键（ValueError）。"""
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8")

    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"canonical JSON 拒绝重复键：{key!r}")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=_reject_duplicates)


def _closed_hash(obj: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    missing = [field for field in fields if field not in obj]
    if missing:
        raise ValueError(f"哈希对象缺少封闭字段（C2.8）：{missing}")
    payload = {field: obj[field] for field in fields}
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return "sha256:" + digest


def bundle_content_hash(content: Mapping[str, Any]) -> str:
    """C2.8：仅哈希 RuleBundleContent 封闭 6 字段，返回 "sha256:<64hex>"。"""
    return _closed_hash(content, BUNDLE_CONTENT_FIELDS)


def calculation_core_hash(core: Mapping[str, Any]) -> str:
    """C2.8：仅哈希 CalculationCore 封闭 11 字段，返回 "sha256:<64hex>"。"""
    return _closed_hash(core, CALCULATION_CORE_FIELDS)
