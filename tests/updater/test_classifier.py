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
    BUDGET_MANIFEST,
    CHANGE_CAP_3500,
    CHANGE_FROZEN_BANDS,
    initial_content,
)


def _build_candidate():
    from app.updater.classifier import build_candidate

    return build_candidate


def test_data_only_candidate_frozen_schema() -> None:
    """候选限冻结 schema DATA-only：仅 auto slot 数值可写，冻结面零变化。"""
    build_candidate = _build_candidate()
    current = initial_content()

    # —— DATA-only 数值变更（auto slot 内）→ 候选成立，差异仅限该 data 路径
    result = build_candidate(current, [CHANGE_CAP_3500], [BUDGET_MANIFEST])
    assert result["status"] == "candidate", (
        f"auto slot 数值变更必须可建候选：{result!r}"
    )
    candidate = result["content"]
    assert candidate is not None
    assert (
        candidate["data"]["salaries"]["reductions"]["2025_26"]["cap"] == "3500"
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
    assert current["data"]["salaries"]["reductions"]["2025_26"]["cap"] == "3000"

    # —— 冻结语义路径（累进带 band 结构）→ semantic_change 隔离，不产候选
    frozen_result = build_candidate(current, [CHANGE_FROZEN_BANDS], [BUDGET_MANIFEST])
    assert frozen_result["status"] == "quarantined"
    assert frozen_result["quarantine_reason"] == "semantic_change"
    assert frozen_result["content"] is None, "语义变化不得产出任何可发布候选"

    # —— schema 外未知路径同样不可自动写（frozen 或语义未知槽位一律不可写）
    unknown = dict(CHANGE_CAP_3500, fullpath="salaries.brand_new_semantics")
    unknown_result = build_candidate(current, [unknown], [BUDGET_MANIFEST])
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
