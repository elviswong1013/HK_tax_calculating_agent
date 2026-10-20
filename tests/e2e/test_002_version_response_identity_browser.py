# -*- coding: utf-8 -*-
"""REQ-11 repair6 窄竞态浏览器 e2e：版本 GET 响应的操作身份。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；共享隔离/启动见
tests/e2e/conftest.py，006 §6 ①–⑤；Chromium 不可用＝BLOCKED）。

【被测契约】
  - 004 §7/§194–196：resync/init 的 meta/version GET 必须绑定请求身份。
    任一 await 后 nonce 或请求 token 变化 → 整体忽略：不写 epoch/revision/
    queue/conflict/initialized，不更新按钮/状态。
  - init 响应暂缓→clear 启动（ACK 未到）→释放 init：旧版本不得写入，
    initialized 不得提前 true，操作不得提前启用。
  - resync 响应暂缓→两次 clear 到 E+2→再制造冲突→释放旧 resync：
    保持 E+2/conflict，不回滚/不 re-enable。
"""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def e2e_expect_timeout() -> int:
    return 10000


def _state(page) -> str:
    return page.evaluate("document.getElementById('calc-form').dataset.editorState")


def _acked(page) -> tuple[str, str]:
    return (
        page.evaluate("document.getElementById('calc-form').dataset.ackedEpoch"),
        page.evaluate("document.getElementById('calc-form').dataset.ackedRevision"),
    )


def _wait_for(predicate, message: str, timeout: float = 10.0, page=None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if page is not None:
            page.wait_for_timeout(50)
        else:
            time.sleep(0.05)
    raise AssertionError(f"同步点超时：{message}")


def _wait_idle(page) -> None:
    _wait_for(lambda: _state(page) == "idle", "编辑器队列回到 idle", page=page)


def _page_errors(page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


class _FetchGate:
    """route.fetch()（服务器已处理）后暂缓 fulfill；按索引释放。"""

    def __init__(self) -> None:
        self.held: list = []
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        response = route.fetch()
        self.held.append((route, response))

    def release_one(self, index: int = 0) -> None:
        route, response = self.held.pop(index)
        route.fulfill(response=response)

    @property
    def count(self) -> int:
        return len(self.requests)


def _malformed_invalidate(route) -> None:
    route.fulfill(
        status=200,
        headers={
            "X-Edit-Applied": "1",
            "X-Edit-Operation-Epoch": "not-an-int",
            "X-Edit-Operation-Revision": "1",
        },
        body="{}",
    )


def test_002_init_stale_at_clear_boundary_no_rollback(page, base_url) -> None:
    """init meta/version 暂缓→clear 启动→释放 init：旧版本不得写入/提前启用。"""
    from playwright.sync_api import expect

    meta_gate = _FetchGate()
    session_gate = _FetchGate()
    page.route("**/api/v1/meta/version", meta_gate.handler)
    page.route("**/api/v1/session", session_gate.handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")

    _wait_for(
        lambda: meta_gate.count == 1 and len(meta_gate.held) == 1,
        "init meta/version 响应进入闸门",
        page=page,
    )
    # 初始化尚未完成：blocked、按钮禁用、无已确认版本。
    assert _state(page) == "blocked"
    assert _acked(page) == ("", "")
    expect(page.locator("#confirm-and-calc")).to_be_disabled()

    # clear 启动：DELETE 也被暂缓（ACK 未到）；init 响应此时仍被持有。
    page.locator("#clear-session").click()
    _wait_for(
        lambda: session_gate.count == 1 and len(session_gate.held) == 1,
        "clear 响应进入闸门",
        page=page,
    )
    clear_epoch = session_gate.held[0][1].headers.get("x-edit-operation-epoch")
    clear_revision = session_gate.held[0][1].headers.get("x-edit-operation-revision")
    assert clear_epoch is not None and clear_revision == "0"

    # 释放旧 init：必须整体忽略，blocked 状态与空版本保持。
    meta_gate.release_one(0)
    page.wait_for_timeout(400)
    assert _state(page) == "blocked", "旧 init 不得使 initialized 提前 true"
    assert _acked(page) == ("", ""), "旧 init 不得写入旧 epoch/revision"
    expect(page.locator("#confirm-and-calc")).to_be_disabled()
    expect(page.locator("#resync-session")).to_be_hidden()

    # 释放 clear ACK：才接纳本操作分配的版本并启用。
    session_gate.release_one(0)
    _wait_for(
        lambda: _state(page) == "idle" and _acked(page) == (clear_epoch, clear_revision),
        "clear ACK 后接纳本操作版本",
        page=page,
    )
    expect(page.locator("#confirm-and-calc")).to_be_enabled()
    assert not errors, f"init/clear 竞态不得产生页面 JS 异常：{errors!r}"


def test_002_resync_stale_after_clear_keeps_latest_epoch(page, base_url) -> None:
    """resync 暂缓→clear 两次到 E+2→制造冲突→释放旧 resync：保持 E+2/conflict。"""
    from playwright.sync_api import expect

    page.route("**/api/v1/calc/invalidate", _malformed_invalidate)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    # 先进入冲突，让 resync 按钮可用。
    page.get_by_label("应课税收入").fill("240000")
    _wait_for(lambda: _state(page) == "conflict", "首个冲突态", page=page)

    # 注册 resync 版本 GET 闸门（此时 init 已完成），点击 resync 暂缓响应。
    meta_gate = _FetchGate()
    page.route("**/api/v1/meta/version", meta_gate.handler)
    page.locator("#resync-session").click()
    _wait_for(
        lambda: meta_gate.count == 1 and len(meta_gate.held) == 1,
        "resync meta/version 响应进入闸门",
        page=page,
    )

    # 两次 clear 到 E+2；旧 resync 响应仍被持有。
    page.locator("#clear-session").click()
    _wait_for(
        lambda: _acked(page) == (str(int(epoch0) + 1), "0") and _state(page) == "idle",
        "第一次 clear ACK 到 E+1",
        page=page,
    )
    page.locator("#clear-session").click()
    _wait_for(
        lambda: _acked(page) == (str(int(epoch0) + 2), "0") and _state(page) == "idle",
        "第二次 clear ACK 到 E+2",
        page=page,
    )
    latest = (str(int(epoch0) + 2), "0")

    # 再制造冲突（malformed invalidate），保持 resync 按钮可见。
    page.get_by_label("应课税收入").fill("250000")
    _wait_for(lambda: _state(page) == "conflict", "E+2 后冲突态", page=page)
    assert _acked(page) == latest

    # 释放旧 resync 响应：整体忽略，不得回滚 E+2、不得清 conflict/re-enable。
    meta_gate.release_one(0)
    page.wait_for_timeout(400)
    assert _acked(page) == latest, (
        f"旧 resync 不得回滚 epoch/revision，实际 {_acked(page)!r}"
    )
    assert _state(page) == "conflict", "旧 resync 不得清除冲突态"
    expect(page.locator("#resync-session")).to_be_visible()
    expect(page.locator("#confirm-and-calc")).to_be_disabled()
    assert not errors, f"resync/clear 竞态不得产生页面 JS 异常：{errors!r}"
