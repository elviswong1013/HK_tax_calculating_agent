"""manifest 封闭 schema 校验与锚定有界抽取（Annex C C7.2/C7.3；REQ-18）。

REQ: 18（§9：候选仅限冻结 schema 内 DATA-only 字段——其数据前提＝统一 manifest
可校验 schema 与 anchor 状态门）。
规格锚点:
  - Annex C C7.2：manifest 记录键恰为封闭键集合（未知键拒绝、缺键不可用）；
    artifact_type ∈ {html_table, html_section, html_index, pdf_layout}；
    anchors[] 每项恰 {path_regex, anchor_status}；
    slots[] 每 auto slot 恰七字段（无 slot_id）；
    evidence_only_fields 正式入 schema。
  - Annex C C7.3：anchor（locator）状态门——anchor_status=confirmed 仅在对应
    DOM/版式验证证据落档后可置；未证 locator 不得标 confirmed、不得进入规则
    数据；标题/条款 section＋DOM 表格有界定位，非整页猜测、非正文模糊抽取；
    行标/列标变化 → fail closed。
  - C1/C7.3 解析依赖：不安装 beautifulsoup4/pypdf——本模块仅用标准库
    （html.unescape＋有界正则），不另立方案绕过用户决定。
期望值来源: 结构性契约（封闭键集合/枚举/七字段），无金额期望。

【拟名】被测契约:
  - validate_manifest(manifest, *, verified_anchors=frozenset()) -> list[str]
    返回违例清单（空列表＝合规）；confirmed 但 path_regex 不在
    verified_anchors → 违例。
  - extract(manifest, document: bytes) ->
    {"values": {slot fullpath: 规范值}, "notices": [{"anchor", "text"}]}
    值仅来自锚定 DOM 表格单元格；锚定节文本供 legal_state_rules 匹配（C7.9）。
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

# C7.2：manifest 记录封闭键集合（未知键拒绝、缺键不可用）
MANIFEST_KEYS = frozenset(
    {
        "manifest_schema_version",
        "source_id",
        "url",
        "artifact_type",
        "parser_id",
        "parser_code_hash",
        "anchors",
        "fields",
        "slots",
        "evidence_only_fields",
        "legal_state_rules",
        "proof_authority",
        "independent_counterpart",
    }
)
ARTIFACT_TYPES = frozenset(
    {"html_table", "html_section", "html_index", "pdf_layout"}
)
ANCHOR_STATUSES = frozenset({"proposed", "unverified", "confirmed"})
ANCHOR_KEYS = frozenset({"path_regex", "anchor_status"})
# C7.1/C7.2：auto slot 恰七字段（无 slot_id）
SLOT_KEYS = frozenset(
    {
        "profile_ID",
        "fullpath",
        "type",
        "unit",
        "constraints",
        "anchor",
        "proof_role",
    }
)
# C7.9：SOURCE 状态枚举（legal_state_rules 的 state 取值域）
LEGAL_STATE_VALUES = frozenset(
    {
        "trusted_offline",
        "proposal_not_current",
        "related_change_uncertain",
        "verified_future",
        "expired",
        "unknown",
    }
)

_HEADING_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table\s*>", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t([dh])[^>]*>(.*?)</t\1\s*>", re.IGNORECASE | re.DOTALL)


def _visible_text(fragment: str) -> str:
    """剥标签＋反转义＋空白折叠（仅用于锚定文本比对/notice 文本）。"""
    plain = _html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return " ".join(plain.split())


def _parse_sections(document_text: str) -> list[tuple[str, str]]:
    """按标题切分有界 section：[(heading 文本, section 原文), …]。"""
    matches = list(_HEADING_RE.finditer(document_text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document_text)
        sections.append((_visible_text(match.group(2)), document_text[match.end():end]))
    return sections


def _tables_in(section_text: str) -> list[list[list[str]]]:
    """section 内 DOM 表格 → 行 → 单元格文本（有界抽取，非整页猜测）。"""
    tables: list[list[list[str]]] = []
    for table_match in _TABLE_RE.finditer(section_text):
        rows: list[list[str]] = []
        for row_match in _ROW_RE.finditer(table_match.group(1)):
            cells = [
                _visible_text(cell_match.group(2))
                for cell_match in _CELL_RE.finditer(row_match.group(1))
            ]
            rows.append(cells)
        tables.append(rows)
    return tables


def _split_anchor(anchor: str) -> tuple[str, str | None, str | None]:
    """C7.3 有界定位语法：heading:<标题>[#row:<行标>][#col:<列标>]。"""
    parts = anchor.split("#")
    head = parts[0]
    if not head.startswith("heading:"):
        raise LookupError(f"锚定须以 heading: 开头（有界定位，C7.3）：{anchor!r}")
    heading = head[len("heading:"):].strip()
    row: str | None = None
    col: str | None = None
    for part in parts[1:]:
        if part.startswith("row:"):
            row = part[len("row:"):].strip()
        elif part.startswith("col:"):
            col = part[len("col:"):].strip()
    return heading, row, col


def _section_body(
    sections: list[tuple[str, str]], heading: str
) -> str | None:
    for section_heading, section_body in sections:
        if section_heading == heading:
            return section_body
    return None


def _extract_slot_value(
    sections: list[tuple[str, str]], anchor: str
) -> str:
    """值抽取仅来自锚定 DOM 表格单元格；行/列未命中 → fail closed（C7.3）。"""
    heading, row, col = _split_anchor(anchor)
    if col is None:
        raise LookupError(f"值抽取须锚定到表格单元格（缺 #col，fail closed）：{anchor!r}")
    section_body = _section_body(sections, heading)
    if section_body is None:
        raise LookupError(f"锚定标题未找到（fail closed，C7.3）：{anchor!r}")
    for table in _tables_in(section_body):
        if not table:
            continue
        header = table[0]
        if col not in header:
            continue
        col_index = header.index(col)
        for data_row in table[1:]:
            if not data_row:
                continue
            if row is not None and data_row[0] != row:
                continue
            if col_index < len(data_row):
                return data_row[col_index]
    raise LookupError(f"锚定单元格未命中（行标/列标变化 → fail closed，C7.3）：{anchor!r}")


def validate_manifest(
    manifest: Any, *, verified_anchors: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """C7.2/C7.3 manifest 校验：返回违例清单（空列表＝合规）。

    verified_anchors＝已落档 DOM/版式验证证据的 path_regex 集合；声明
    confirmed 但不在其中的 anchor 判违例（状态门）。
    """
    if not isinstance(manifest, dict):
        return ["manifest 必须为映射（C7.2）"]
    violations: list[str] = []
    keys = set(manifest)
    for key in sorted(MANIFEST_KEYS - keys):
        violations.append(f"缺少封闭键（缺键不可用，C7.2）：{key}")
    for key in sorted(keys - MANIFEST_KEYS):
        violations.append(f"未知键拒绝（封闭键集合，C7.2）：{key}")
    if violations:
        return violations  # 键集合不封闭时不作深检

    for field in (
        "manifest_schema_version",
        "source_id",
        "url",
        "parser_id",
        "parser_code_hash",
        "proof_authority",
        "independent_counterpart",
    ):
        value = manifest.get(field)
        if not isinstance(value, str) or not value:
            violations.append(f"字段必须为非空字符串：{field}")
    url = manifest.get("url")
    if isinstance(url, str) and not url.startswith("https://"):
        violations.append(f"url 必须为精确 https（allowlist）：{url!r}")
    if manifest.get("artifact_type") not in ARTIFACT_TYPES:
        violations.append(
            f"artifact_type 非枚举成员（有界抽取，非整页猜测，C7.2）：{manifest.get('artifact_type')!r}"
        )

    anchor_paths: set[str] = set()
    anchors = manifest.get("anchors")
    if not isinstance(anchors, list):
        violations.append("anchors 必须为列表")
    else:
        for index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict) or set(anchor) != set(ANCHOR_KEYS):
                violations.append(
                    f"anchors[{index}] 键集合须恰为 {sorted(ANCHOR_KEYS)}（C7.2）"
                )
                continue
            path = anchor["path_regex"]
            anchor_paths.add(path)
            status = anchor["anchor_status"]
            if status not in ANCHOR_STATUSES:
                violations.append(
                    f"anchors[{index}].anchor_status 非枚举成员（C7.2/C7.3）：{status!r}"
                )
            if status == "confirmed" and path not in verified_anchors:
                violations.append(
                    f"未证 locator 不得标 confirmed（C7.3 状态门）：{path!r}"
                )

    for list_field in ("fields", "evidence_only_fields"):
        value = manifest.get(list_field)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            violations.append(f"{list_field} 必须为字符串列表（C7.2）")

    slots = manifest.get("slots")
    if not isinstance(slots, list):
        violations.append("slots 必须为列表")
    else:
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                violations.append(f"slots[{index}] 必须为映射（C7.2）")
                continue
            slot_keys = set(slot)
            for key in sorted(set(SLOT_KEYS) - slot_keys):
                violations.append(
                    f"slots[{index}] 缺 auto slot 七字段之一（C7.1/C7.2）：{key}"
                )
            for key in sorted(slot_keys - set(SLOT_KEYS)):
                violations.append(
                    f"slots[{index}] 多余字段（恰七字段、无 slot_id，C7.2）：{key}"
                )
            slot_anchor = slot.get("anchor")
            if isinstance(slot_anchor, str) and slot_anchor not in anchor_paths:
                violations.append(
                    f"slots[{index}].anchor 未指向 anchors[] path_regex 命名空间：{slot_anchor!r}"
                )

    rules = manifest.get("legal_state_rules")
    if not isinstance(rules, list):
        violations.append("legal_state_rules 必须为列表（可为空，C7.2）")
    else:
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                violations.append(f"legal_state_rules[{index}] 必须为映射")
                continue
            for key in ("anchor", "match", "state", "period"):
                if key not in rule:
                    violations.append(f"legal_state_rules[{index}] 缺键：{key}")
            if rule.get("state") not in LEGAL_STATE_VALUES:
                violations.append(
                    f"legal_state_rules[{index}].state 非 C7.9 枚举：{rule.get('state')!r}"
                )
    return violations


def extract(manifest: dict, document: bytes) -> dict[str, Any]:
    """锚定有界抽取：values（slot fullpath → 规范值）＋notices（锚定节文本）。

    notices 供 legal_state_rules 匹配（C7.9 SOURCE 状态输入）；锚定标题缺失
    → 无该锚 notice；slot 锚未命中 → 异常（fail closed，来源解析失败）。
    """
    if not isinstance(document, (bytes, bytearray)):
        raise TypeError("document 必须为字节")
    document_text = bytes(document).decode("utf-8")
    sections = _parse_sections(document_text)

    values: dict[str, str] = {}
    for slot in manifest.get("slots", []) or []:
        values[slot["fullpath"]] = _extract_slot_value(sections, slot["anchor"])

    notices: list[dict[str, str]] = []
    seen_anchors: set[str] = set()
    for rule in manifest.get("legal_state_rules", []) or []:
        anchor = rule.get("anchor") if isinstance(rule, dict) else None
        if not isinstance(anchor, str) or anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        try:
            heading, _row, _col = _split_anchor(anchor)
        except LookupError:
            continue
        text = _section_body(sections, heading)
        if text is not None:
            notices.append({"anchor": anchor, "text": _visible_text(text)})
    return {"values": values, "notices": notices}
