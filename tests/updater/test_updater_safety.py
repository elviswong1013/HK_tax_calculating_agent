"""REQ-18 更新程序写边界（M5 Red；SDD §5 更新管线行）。

REQ: 18（§9：下载/候选/LLM 内容永不可执行；更新程序不修改源码/测试/规格；
全部验证通过后自动发布）。
规格锚点:
  - SDD §5：更新管线……下载/候选/LLM 内容永不可执行；更新程序不修改
    源码/测试/规格；新语义走变更请求。
  - Annex C C8.5：代码/测试/受信参考语义不可变。
期望值来源: 结构性契约（仓库内容树摘要前后一致），无金额期望。

【拟名】被测契约:
  - 端到端检查（Scheduler.startup，含变更候选路径：预算页 3500＋独立法例页
    3500）完成后，publish_outcome ∈ {"published","quarantined"}；无论判定如何，
    仓库内容树（app/ tests/ specs/ docs/ scripts/ pyproject.toml README.md）
    逐文件 SHA-256 前后完全一致——更新程序的一切写入仅落在注入的临时 DB。

Red 说明: app/updater/ 尚不存在 —— 测试失败原因＝
「ModuleNotFoundError: app.updater.scheduler（更新管线缺失）」。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from _fakes import (
    BUDGET_DOC_CHANGED,
    BUDGET_URL,
    SDO_INDEX_DOC,
    SDO_INDEX_URL,
    SCH43_DOC_3500,
    SCH43_URL,
    FakeClock,
    FakeOutbound,
    hk,
    make_scheduler,
    seed_published_store,
    sources_with_law,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT_ROOTS = ("app", "tests", "specs", "docs", "scripts")
CONTENT_FILES = ("pyproject.toml", "README.md")
_SKIPPED_SUFFIXES = {".pyc", ".pyo"}


def _tree_digest(root: Path) -> dict[str, str]:
    """仓库内容树摘要（跳过隐藏目录/缓存产物——非更新程序可写面）。"""
    digests: dict[str, str] = {}
    for base in CONTENT_ROOTS:
        for path in (root / base).rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            parts = relative.parts
            if "__pycache__" in parts or path.suffix in _SKIPPED_SUFFIXES:
                continue
            if any(part.startswith(".") for part in parts):
                continue
            digests[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in CONTENT_FILES:
        path = root / name
        if path.is_file():
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digests, "内容树摘要不得为空（仓库布局异常）"
    return digests


def test_updater_never_edits_source_tests_specs(
    tmp_path, approved_temp_root, safety_guard
) -> None:
    """完整更新运行（含变更候选→门禁→发布判定）后，源码/测试/规格逐字节不变。

    W-L L4：本次运行登记的全部 store（含门禁隐藏 Temp DB）须归属批准 Temp 根；
    真实 app 边界（启动/真实 transport/模型出站）计数零增量。
    """
    before = _tree_digest(REPO_ROOT)
    stores_before = set(safety_guard.observed_store_paths())
    counters_before = safety_guard.counter_snapshot()
    gate_dirs_before = set(approved_temp_root.glob("hktax-gate-*"))

    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(
        {
            BUDGET_URL: BUDGET_DOC_CHANGED,  # 预算页：cap 3000 → 3500（变更候选）
            SCH43_URL: SCH43_DOC_3500,  # 独立法例页：同值 3500（独立对应）
            SDO_INDEX_URL: SDO_INDEX_DOC,
        }
    )
    store = seed_published_store(tmp_path)
    sched = make_scheduler(
        store, clock=clock, outbound=outbound, sources=sources_with_law()
    )

    job = sched.startup()
    assert job is not None and job["status"] == "succeeded", (
        "全源 fetch+parse+classify 应完成（候选与门禁由管线内判定）"
    )
    status = sched.status()
    assert status["global_success_at"] == clock.now()
    assert status["publish_outcome"] in {"published", "quarantined"}, (
        "变更检查必须给出明确发布判定（自动发布或隔离，无人工点击）："
        f"{status['publish_outcome']!r}"
    )
    if status["publish_outcome"] == "published":
        assert status["published_bundle_id"] == store.current()["bundle_id"]

    after = _tree_digest(REPO_ROOT)
    changed = sorted(
        name
        for name in set(before) | set(after)
        if before.get(name) != after.get(name)
    )
    assert not changed, (
        f"更新程序不得修改源码/测试/规格/文档/构建配置（SDD §5）：差异 {changed}"
    )

    # —— W-L L4：统一 Temp 约束/登记证据（含门禁隐藏 Temp DB）——
    new_paths = sorted(set(safety_guard.observed_store_paths()) - stores_before)
    assert new_paths, "更新运行必须登记其临时 store（含门禁临时 DB）至安全边界账"
    for raw in new_paths:
        path = Path(raw)
        assert path == approved_temp_root or approved_temp_root in path.parents, (
            f"更新运行 store 必须位于批准 Temp 根内：{path}"
        )
    gate_paths = [
        Path(raw)
        for raw in new_paths
        if any(part.startswith("hktax-gate-") for part in Path(raw).parts)
    ]
    for path in gate_paths:
        assert path.parent.parent == approved_temp_root, (
            f"门禁临时目录必须归属批准 Temp 根：{path}"
        )
    new_gate_dirs = set(approved_temp_root.glob("hktax-gate-*")) - gate_dirs_before
    assert {path.parent for path in gate_paths} == new_gate_dirs, (
        f"门禁临时目录须与批准根内新建 hktax-gate-* 一致：{gate_paths!r}"
    )
    # 真实边界（启动/真实 transport/模型出站）零调用：FakeOutbound 不触真实出站
    assert safety_guard.counter_deltas(counters_before) == {}, (
        "更新测试不得触发真实 app 边界调用（启动/真实 transport/模型）"
    )
