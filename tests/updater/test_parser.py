"""REQ-18 manifest 封闭 schema 与 locator 状态门（M5 Red；Annex C C7.1/C7.2/C7.3）。

REQ: 18（§9：候选仅限冻结 schema 内 DATA-only 字段——其数据前提＝统一 manifest
可校验 schema 与 anchor 状态门）。
规格锚点:
  - Annex C C7.1/C7.2：每个 auto slot 恰七字段（profile_ID/fullpath/type/unit/
    constraints/anchor/proof_role；无 slot_id）；manifest 记录键恰为封闭键集合
    （未知键拒绝、缺键不可用）；evidence_only_fields 正式入 schema。
  - Annex C C7.3：anchor（locator）状态门——anchor_status=confirmed 仅在对应
    DOM/版式验证证据落档后可置；未证 locator 不得标 confirmed、不得进入规则数据；
    anchor_status ∈ {proposed, unverified, confirmed}；artifact_type ∈
    {html_table, html_section, html_index, pdf_layout}；标题/条款 section＋DOM
    表格有界定位，非整页猜测。
期望值来源: 结构性契约（封闭键集合/枚举/七字段），无金额期望。

【拟名】被测契约（app/updater/parser，Green 阶段须按测试实现，不得要求测试改写）:
  - validate_manifest(manifest, *, verified_anchors=frozenset()) -> list[str]
    返回违例清单（空列表＝合规）。verified_anchors＝已落档 DOM/版式验证证据的
    path_regex 集合；manifest 中 anchor_status="confirmed" 但 path_regex 不在
    verified_anchors → 违例。
  - extract(manifest, document: bytes) -> {"values": {slot fullpath: 规范值},
    "notices": [{"anchor": path_regex, "text": 锚定节文本}]}
    值提取仅来自锚定 DOM 表格单元格（heading/row/col 有界定位）；锚定节文本
    供 legal_state_rules 匹配（C7.9 状态输入）。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.parser（解析器/manifest 校验缺失）」。
"""

from __future__ import annotations

import copy

from _fakes import (
    BUDGET_DOC_CHANGED,
    BUDGET_DOC_OK,
    BUDGET_DOC_UNCERTAIN_NOTICE,
    BUDGET_MANIFEST,
)

_SLOT_SEVEN_FIELDS = (
    "profile_ID",
    "fullpath",
    "type",
    "unit",
    "constraints",
    "anchor",
    "proof_role",
)


def _parser():
    from app.updater.parser import extract, validate_manifest

    return extract, validate_manifest


def test_auto_slot_manifest_fields_complete_and_unverified_locator_not_confirmed() -> None:
    """auto slot 七字段齐全＋封闭键集合＋anchor 枚举；未证 locator 不得 confirmed。"""
    extract, validate_manifest = _parser()
    verified = {anchor["path_regex"] for anchor in BUDGET_MANIFEST["anchors"]}

    # —— (1) 合法 manifest（七字段 slot、封闭键、confirmed 均有验证证据）→ 零违例
    assert validate_manifest(BUDGET_MANIFEST, verified_anchors=verified) == []

    # —— (2) 未证 locator 不得标 confirmed（C7.3 状态门）
    violations = validate_manifest(BUDGET_MANIFEST, verified_anchors=frozenset())
    assert violations, "无验证证据落档却声明 confirmed 的 anchor 必须判违例"

    # —— (3) anchor_status 非枚举成员 → 违例
    bad_status = copy.deepcopy(BUDGET_MANIFEST)
    bad_status["anchors"][0]["anchor_status"] = "verified_by_ai"
    assert validate_manifest(bad_status, verified_anchors=verified)

    # —— (4) slot 缺七字段之一（unit）→ 违例（恰七字段，缺一不可）
    missing_unit = copy.deepcopy(BUDGET_MANIFEST)
    del missing_unit["slots"][0]["unit"]
    assert validate_manifest(missing_unit, verified_anchors=verified)

    # —— (5) slot 多余字段（slot_id）→ 违例（封闭七字段，无 slot_id，C7.2）
    extra_slot_id = copy.deepcopy(BUDGET_MANIFEST)
    extra_slot_id["slots"][0]["slot_id"] = "slot-1"
    assert validate_manifest(extra_slot_id, verified_anchors=verified)

    # —— (6) 顶层缺键（evidence_only_fields）→ 违例；多余键（locator 对象）→ 违例
    missing_evidence = copy.deepcopy(BUDGET_MANIFEST)
    del missing_evidence["evidence_only_fields"]
    assert validate_manifest(missing_evidence, verified_anchors=verified)
    extra_locator = copy.deepcopy(BUDGET_MANIFEST)
    extra_locator["locator"] = {"css": "table > tr"}
    assert validate_manifest(extra_locator, verified_anchors=verified)

    # —— (7) artifact_type 非枚举 → 违例（有界抽取，非整页猜测）
    bad_artifact = copy.deepcopy(BUDGET_MANIFEST)
    bad_artifact["artifact_type"] = "fulltext_fuzzy"
    assert validate_manifest(bad_artifact, verified_anchors=verified)

    # —— (8) 已证 locator 的锚定抽取：产出 slot 规范值（正向路径）
    output = extract(BUDGET_MANIFEST, BUDGET_DOC_OK)
    assert output["values"]["salaries.reductions.2025_26.cap"] == "3000"
    changed = extract(BUDGET_MANIFEST, BUDGET_DOC_CHANGED)
    assert changed["values"]["salaries.reductions.2025_26.cap"] == "3500"

    # —— (9) 锚定节文本进入 notices（legal_state_rules 的状态输入）
    noticed = extract(BUDGET_MANIFEST, BUDGET_DOC_UNCERTAIN_NOTICE)
    assert any(
        "will be confirmed" in notice["text"] for notice in noticed["notices"]
    ), "锚定通知文本须进入 notices（C7.9 状态机输入）"

    # 结构自检：测试宇宙自身 slot 恰七字段（防 fixture 漂移）
    assert set(BUDGET_MANIFEST["slots"][0].keys()) == set(_SLOT_SEVEN_FIELDS)
