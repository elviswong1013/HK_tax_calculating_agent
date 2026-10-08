"""official fixture 溯源校验（REQ-15；SDD §9/§12：仅 T1 一手来源进入 fixture）。

返回违例清单（空列表＝合规）；每条违例包含对应 case_id（可定位）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from urllib.parse import urlsplit

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")

# 官方域（含 www 变体）：ird.gov.hk／gov.hk／elegislation.gov.hk
_OFFICIAL_HOSTS = frozenset(
    {
        "ird.gov.hk",
        "www.ird.gov.hk",
        "gov.hk",
        "www.gov.hk",
        "elegislation.gov.hk",
        "www.elegislation.gov.hk",
    }
)

_REQUIRED_FIELDS = ("case_id", "source_url", "anchor", "retrieved_at", "content_hash", "tier")


def validate_provenance(data: Mapping) -> list[str]:
    """校验 provenance 台账：顶层恰含 schema_version＋fixtures；每条 fixture 仅 T1。"""
    violations: list[str] = []
    if not isinstance(data, Mapping):
        return ["顶层必须是映射（schema_version＋fixtures）"]
    extras = sorted(set(data.keys()) - {"schema_version", "fixtures"})
    if extras:
        violations.append(f"顶层含未知字段：{extras}")
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        violations.append("顶层 schema_version 缺失或为空")
    fixtures = data.get("fixtures")
    if not isinstance(fixtures, list):
        violations.append("顶层 fixtures 缺失或不是列表")
        return violations

    seen_ids: set[str] = set()
    for index, fixture in enumerate(fixtures):
        case_id = fixture.get("case_id") if isinstance(fixture, Mapping) else None
        if isinstance(case_id, str) and case_id.strip():
            label = case_id
        else:
            label = f"<fixture#{index}（缺 case_id）>"
            violations.append(f"{label}: case_id 缺失或为空")

        def _violation(message: str, *, _label: str = label) -> None:
            violations.append(f"{_label}: {message}")

        if not isinstance(fixture, Mapping):
            _violation("条目必须是映射")
            continue
        for field in _REQUIRED_FIELDS:
            if field not in fixture:
                _violation(f"缺少字段 {field}")
        stripped_id = case_id if isinstance(case_id, str) else None
        if stripped_id and stripped_id.strip():
            if stripped_id in seen_ids:
                _violation(f"case_id 重复：{stripped_id}")
            seen_ids.add(stripped_id)

        source_url = fixture.get("source_url")
        if not isinstance(source_url, str) or not source_url:
            _violation("source_url 缺失或为空")
        else:
            parts = urlsplit(source_url)
            if parts.scheme != "https":
                _violation(f"source_url 必须 https：{source_url}")
            if parts.hostname not in _OFFICIAL_HOSTS:
                _violation(f"source_url 非官方域：{source_url}")

        anchor = fixture.get("anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            _violation("anchor 缺失或为空")

        retrieved_at = fixture.get("retrieved_at")
        if not isinstance(retrieved_at, str) or _DATE_RE.fullmatch(retrieved_at) is None:
            _violation(f"retrieved_at 非 YYYY-MM-DD 全匹配：{retrieved_at!r}")
        else:
            try:
                date.fromisoformat(retrieved_at)
            except ValueError:
                _violation(f"retrieved_at 不是有效日历日期：{retrieved_at!r}")

        content_hash = fixture.get("content_hash")
        if not isinstance(content_hash, str) or _HASH_RE.fullmatch(content_hash) is None:
            _violation(f"content_hash 非 sha256:<64hex>：{content_hash!r}")

        if fixture.get("tier") != "T1":
            _violation(f"tier 必须为 T1（T2/T3/未知一律违例）：{fixture.get('tier')!r}")
    return violations
