"""REQ-18 候选构建：DATA-only 冻结 schema＋语义变化走变更请求（M5 Red；SDD §5 管线阶段 4）。

REQ: 18（§9：候选仅限冻结 schema 内 DATA-only 字段；语义变化 → semantic_change
隔离＋变更请求；下载/候选/LLM 内容永不可执行；新语义走变更请求）。
规格锚点:
  - SDD §5 管线阶段 4（candidate build）：冻结 schema 校验；DATA-only；
    语义变化 → semantic_change 隔离＋变更请求。
  - Annex C C7.1（参数槽位二分）：auto_parameter_slots＝既有 typed profile 内
    数值/带日期条目；取整方向/单位/阶段/次序、公式、资格、band 数量/算子＝
    evidence_only_or_frozen_semantics，永不可自动写；frozen 或语义未知槽位
    一律不可写。
期望值来源: 结构性契约（候选差异仅限 data 路径；冻结面零变化），无金额期望
  （3000/3500 为测试宇宙 marker）。

【拟名】被测契约（app/updater/classifier.build_candidate，Green 阶段须按测试
实现，不得要求测试改写）:
  - build_candidate(current_content, changes, manifests) -> {
      "status": "candidate" | "quarantined",
      "content": 新 bundle content | None,     # 仅当 status="candidate"
      "quarantine_reason": str | None,          # "semantic_change" | schema 违例类
      "change_request": dict | None,            # 语义变化 → 变更请求记录
    }
    changes 元素 {"fullpath", "value", "source_id", "anchor"}；fullpath 必须命中
    某 manifest auto slot（七字段白名单）——未命中（含冻结语义路径/schema 外
    未知路径）→ quarantined（semantic_change）＋change_request，不产候选。
    候选差异仅限 data 内对应路径；rules_schema_version/effective/applicability/
    frozen_semantics 与 current 完全一致；current_content 不被就地修改。
  - change_request: {"paths": [试图写入的 fullpath…], "profiles": [受影响
    profile_ID…], "reason": 非空说明}——供人工/规格流程处置，不产生可发布物。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.classifier（候选构建缺失）」。
"""

from __future__ import annotations

from _fakes import (
    AVD_FULLPATH,
    AVD_MANIFEST,
    BUDGET_MANIFEST,
    CHANGE_CAP_3500,
    CHANGE_FROZEN_BANDS,
    SIX_STOP_AUTO_PATHS,
    initial_content,
    manifest_with_slot,
)


def _build_candidate():
    from app.updater.classifier import build_candidate

    return build_candidate


def test_data_only_candidate_frozen_schema() -> None:
    """候选限冻结 schema DATA-only（不受影响路径正例；旧路径固定拒绝）。"""
    build_candidate = _build_candidate()
    current = initial_content()

    # —— DATA-only 数值变更（不受影响 AVD 路径 auto slot）→ 候选成立 ——
    change = {
        "fullpath": AVD_FULLPATH,
        "value": "110",
        "source_id": "ird_avd",
        "anchor": "heading:AVD - Related Conveyance#row:Fixed duty#col:Amount",
    }
    result = build_candidate(current, [change], [AVD_MANIFEST])
    assert result["status"] == "candidate", (
        f"不受影响路径 auto slot 数值变更必须可建候选：{result!r}"
    )
    candidate = result["content"]
    assert candidate is not None
    assert (
        candidate["data"]["stamps"]["property"]["related_conveyance"]["fixed_duty"]
        == "110"
    )
    for frozen_field in (
        "rules_schema_version",
        "effective",
        "applicability",
        "frozen_semantics",
    ):
        assert candidate[frozen_field] == current[frozen_field], (
            f"候选不得改动冻结面 {frozen_field}（C7.1 永不可自动写）"
        )
    # 输入 current 不被就地修改
    assert (
        current["data"]["stamps"]["property"]["related_conveyance"]["fixed_duty"]
        == "100"
    )

    # —— 六条 stop-auto/共享路径：即使 manifest 注入 slot 描述也隔离 ——
    for index, fullpath in enumerate(SIX_STOP_AUTO_PATHS):
        injected = manifest_with_slot(
            fullpath,
            source_id=f"legacy_{index}",
            profile_id="salaries_legacy_v1",
        )
        legacy_change = {
            "fullpath": fullpath,
            "value": "999999",
            "source_id": f"legacy_{index}",
            "anchor": injected["slots"][0]["anchor"],
        }
        legacy = build_candidate(current, [legacy_change], [injected])
        assert legacy["status"] == "quarantined", (
            f"stop-auto 旧路径 {fullpath!r} 不得产候选"
        )
        assert legacy["content"] is None

    # —— 冻结语义路径（累进带 band 结构）→ semantic_change 隔离，不产候选 ——
    frozen_result = build_candidate(current, [CHANGE_FROZEN_BANDS], [BUDGET_MANIFEST])
    assert frozen_result["status"] == "quarantined"
    assert frozen_result["quarantine_reason"] == "semantic_change"
    assert frozen_result["content"] is None, "语义变化不得产出任何可发布候选"

    # —— schema 外未知路径同样不可自动写（frozen 或语义未知槽位一律不可写）
    unknown = dict(change, fullpath="salaries.brand_new_semantics")
    unknown_result = build_candidate(current, [unknown], [AVD_MANIFEST])
    assert unknown_result["status"] == "quarantined"
    assert unknown_result["content"] is None


def test_semantic_change_to_change_request() -> None:
    """语义变化 → 隔离并生成变更请求记录（不产生可发布物）。"""
    build_candidate = _build_candidate()
    current = initial_content()

    result = build_candidate(current, [CHANGE_FROZEN_BANDS], [BUDGET_MANIFEST])
    assert result["status"] == "quarantined"
    assert result["quarantine_reason"] == "semantic_change"
    assert result["content"] is None

    change_request = result["change_request"]
    assert isinstance(change_request, dict), (
        f"语义变化必须走变更请求（§5 阶段 4）：{result!r}"
    )
    for key in ("paths", "profiles", "reason"):
        assert key in change_request, f"变更请求缺字段 {key}：{change_request!r}"
    assert CHANGE_FROZEN_BANDS["fullpath"] in change_request["paths"], (
        "变更请求必须列出试图写入的冻结路径"
    )
    assert change_request["reason"], "变更请求必须携带非空理由"


def test_new_shared_data_paths_are_fixed_rejected() -> None:
    """F6：新共享路径及等价前缀入口即使注入 slot 也必须固定拒绝。

    005:855：classifier 以固定停止路径集合拒绝旧路径或新共享路径；不能仅靠
    UI 隐藏。合法 decimal slot 不得成为写入共享规则的后门。
    """
    build_candidate = _build_candidate()
    current = initial_content()
    shared_paths = (
        "allowances.2025_26.basic.amount",
        "allowances.2025_26.basic",
        "allowances.2025_26.married.amount",
        "allowances.mpf_deduction_cap",
        "reductions.2025_26.cap",
        "reductions.2024_25.cap",
    )
    for index, fullpath in enumerate(shared_paths):
        injected = manifest_with_slot(
            fullpath,
            source_id=f"shared_{index}",
            profile_id="test_shared_v1",
        )
        change = {
            "fullpath": fullpath,
            "value": "3500",
            "source_id": f"shared_{index}",
            "anchor": injected["slots"][0]["anchor"],
        }
        result = build_candidate(current, [change], [injected])
        assert result["status"] == "quarantined", (
            f"new shared path {fullpath!r} must be fixed-rejected, not written "
            f"through a legal decimal slot: {result!r}"
        )
        assert result["content"] is None
        assert result["quarantine_reason"] == "stop_auto_observer_only", (
            f"new shared path {fullpath!r} must use stop-auto rejection: {result!r}"
        )
        assert fullpath in (result["change_request"] or {}).get("paths", [])
