"""R1 启动超时恢复的确定性测试（假 server/thread；不启动真实线程/网络）。

对应 P2 W0 补正 R1：`_start_injected_loopback` 在超时/失败时必须在移交句柄
前设置 `should_exit`、带超时 join，并原样抛出；不得遗留存活线程。
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from types import ModuleType

import pytest


def _load_e2e_conftest() -> ModuleType:
    path = Path(__file__).resolve().parent / "e2e" / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "_p2_w0_e2e_conftest_under_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeServer:
    def __init__(self) -> None:
        self.started = False
        self.should_exit = False

    def run(self) -> None:  # 由假 Thread 保存为 target；不会执行
        raise AssertionError("假 server.run 不应被调用")


class _FakeThread:
    def __init__(self, **kwargs: object) -> None:
        self.ident: object | None = None
        self.join_calls: list[float | None] = []
        self._alive = True
        self.kwargs = kwargs

    def start(self) -> None:
        self.ident = object()

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
        self._alive = False


def test_startup_timeout_cleans_up_and_raises(monkeypatch) -> None:
    conftest = _load_e2e_conftest()
    fake_server = _FakeServer()
    created_threads: list[_FakeThread] = []

    def make_thread(**kwargs: object) -> _FakeThread:
        thread = _FakeThread(**kwargs)
        created_threads.append(thread)
        return thread

    monkeypatch.setattr(conftest, "SERVER_START_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match=r"未能在 0\.01s 内启动"):
        conftest._start_injected_loopback(
            object(),
            server_factory=lambda _config: fake_server,
            thread_factory=make_thread,
        )

    assert len(created_threads) == 1
    fake_thread = created_threads[0]
    assert fake_server.should_exit is True
    assert fake_thread.join_calls == [conftest.SERVER_STOP_TIMEOUT_SECONDS]
    assert fake_thread.is_alive() is False
    assert fake_thread.kwargs["name"] == "p2-w0-e2e-uvicorn"
    assert fake_thread.kwargs["daemon"] is True
    assert not any(
        thread.name == "p2-w0-e2e-uvicorn" for thread in threading.enumerate()
    )
