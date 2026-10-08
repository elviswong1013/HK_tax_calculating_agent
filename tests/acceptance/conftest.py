"""tests/acceptance 共享 fixture（终验 REJECTED 后 Red 阶段）。

REQ: 无独立 REQ（纯基建；服务本目录各验收缺口测试文件，见各文件头部）。
【拟名】契约:
  - make_acc(name)：按唯一 DB 路径构建进程内应用把手（app.state.store／
    app.state.update_scheduler 服务端接缝，同 tests/ui 注入模式）；
    同一测试可构建多个互不共享 DB 的应用实例。
  - 本目录对 app.* 一律延迟导入（fixture 内 import），Red 失败原因对应
    「缺失的 app 行为」而非收集期失败。
"""

from __future__ import annotations

import pytest

from _acc_helpers import AccHarness, build_app


@pytest.fixture
def make_acc(tmp_path, monkeypatch):
    """应用工厂：每次调用产出独立临时 DB 的进程内应用把手。"""
    made: list[AccHarness] = []

    def _make(name: str) -> AccHarness:
        harness = build_app(monkeypatch.setenv, tmp_path / f"{name}.db")
        made.append(harness)
        return harness

    yield _make
    for harness in made:
        harness.close()
