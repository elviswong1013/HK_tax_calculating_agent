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


def test_updater_never_edits_source_tests_specs(tmp_path) -> None:
    """完整更新运行（含变更候选→门禁→发布判定）后，源码/测试/规格逐字节不变。"""
    before = _tree_digest(REPO_ROOT)

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
