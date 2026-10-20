# -*- coding: utf-8 -*-
"""REQ-11 repair5 窄竞态浏览器 e2e：AI 轮次归属与 clear ACK 身份。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；共享隔离/启动见
tests/e2e/conftest.py，006 §6 ①–⑤；Chromium 不可用＝BLOCKED）。

【被测契约】
  - 旧 AI 轮次 A 的迟到响应不得清掉/干扰后来建立的 preview B：A 不写
    chatHistory/DOM、不恢复 proposal；B 的 pendingPreview、弹窗、按钮
    与完成处理不受影响。
  - clear 是带操作身份的 epoch 边界：旧 clear ACK（同标签页重复 clear、
    跨标签页 clear/resync 之后）整体忽略，不得回滚 epoch/revision/
    conflict/按钮；最新 clear 的 E+2（或已确定的冲突态）保持。
"""

from __future__ import annotations

import json
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


class _HoldGate:
    """暂缓请求（不触达服务器）；按索引 fulfill/abort。"""

    def __init__(self) -> None:
        self.held: list = []
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        self.held.append(route)

    def release_one(
        self,
        index: int = 0,
        *,
        status: int = 200,
        headers: dict | None = None,
        body: str = "",
    ) -> None:
        route = self.held.pop(index)
        route.fulfill(status=status, headers=headers or {}, body=body)

    @property
    def count(self) -> int:
        return len(self.requests)


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


def _start_preview(page, question: str) -> None:
    from playwright.sync_api import expect

    page.get_by_label("你的问题").fill(question)
    page.locator("#chat-send").click()
    expect(page.locator("#consent-dialog[role=dialog]")).to_be_visible()


def _success_body(content: str, *, with_proposal: bool = False) -> str:
    payload = {
        "consent_id": "test-consent",
        "sent_bytes": 1,
        "status": "completed",
        "content": content,
        "unconfirmed": True,
        "tool_results": [],
    }
    if with_proposal:
        payload["tool_results"] = [
            {
                "id": "call_stale",
                "name": "propose_input",
                "outcome": "executed",
                "result": {
                    "proposal": {
                        "tax_type": "salaries_tax",
                        "schema_version": "2.0.0",
                        "input": {"year_of_assessment": "2025_26"},
                    }
                },
            }
        ]
    return json.dumps(payload, ensure_ascii=False)


# ------------------------------------------------------------------ A. 轮次归属
def test_002_late_old_chat_round_does_not_clear_new_preview(
    page, base_url, e2e_isolation
) -> None:
    """A chat 暂缓→clear→B preview/allow→释放 A：B 弹窗/按钮/完成不受影响。"""
    from playwright.sync_api import expect

    chat_gate = _HoldGate()
    page.route("**/api/v1/ai/chat", chat_gate.handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    # 轮次 A：真实 preview/grant，chat 响应被暂缓。
    _start_preview(page, "A問題")
    page.locator("#consent-allow").click()
    _wait_for(lambda: chat_gate.count == 1, "A chat 请求进入闸门", page=page)

    # clear：consent 弹窗遮住 clear 按钮，用 DOM click 触发同一 handler
    # （等价跨标签页清空；本测试针对轮次竞态）。
    page.evaluate("document.getElementById('clear-session').click()")
    _wait_for(
        lambda: _acked(page)[0] not in ("", epoch0) and _state(page) == "idle",
        "clear ACK 后 idle（新 epoch）",
        page=page,
    )

    # 轮次 B：新 preview + 新授权；chat B 也被暂缓。
    _start_preview(page, "B問題")
    page.locator("#consent-allow").click()
    _wait_for(lambda: chat_gate.count == 2, "B chat 请求进入闸门", page=page)

    # B 在途：按钮由 B 拥有（disabled）。释放旧 A 成功响应。
    expect(page.locator("#consent-allow")).to_be_disabled()
    chat_gate.release_one(0, status=200, headers={"Content-Type": "application/json"},
                          body=_success_body("A遲到回覆", with_proposal=True))
    page.wait_for_timeout(400)
    expect(page.locator("#consent-dialog[role=dialog]")).to_be_visible()
    expect(
        page.locator("#consent-allow"), "A 迟到 finally 不得恢复 B 的按钮"
    ).to_be_disabled()
    payload_text = page.locator("#consent-payload").text_content()
    assert "B問題" in payload_text and "A問題" not in payload_text
    assert page.locator(".proposal-diff").count() == 0, "A 迟到 proposal 不得恢复"
    assert page.locator("#chat-messages li").count() == 0, "A 迟到回复不得写聊天"

    # 释放 B：正常完成，B 内容进入聊天；A 内容不出现。
    chat_gate.release_one(0, status=200, headers={"Content-Type": "application/json"},
                          body=_success_body("B回覆"))
    _wait_for(
        lambda: page.locator("#chat-messages li").count() == 2,
        "B 成功消息写入聊天",
        page=page,
    )
    expect(page.locator("#consent-dialog")).to_be_hidden()
    chat_text = page.locator("#chat-messages").inner_text()
    assert "B回覆" in chat_text and "A遲到回覆" not in chat_text
    assert page.locator(".proposal-diff").count() == 0
    assert not errors, f"轮次归属竞态不得产生页面 JS 异常：{errors!r}"
    assert e2e_isolation.model.send_calls == 0


# ------------------------------------------------------------------ B. clear ACK
def test_002_clear_ack_reorder_keeps_latest_epoch(page, base_url) -> None:
    """两次 clear ACK 重排：第二次 E+2 先接纳，第一次迟到不得回滚。"""
    from playwright.sync_api import expect

    clear_gate = _FetchGate()
    page.route("**/api/v1/session", clear_gate.handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    page.locator("#clear-session").click()
    _wait_for(
        lambda: clear_gate.count == 1 and len(clear_gate.held) == 1,
        "第一次 clear 响应进入闸门",
        page=page,
    )
    page.locator("#clear-session").click()
    _wait_for(
        lambda: clear_gate.count == 2 and len(clear_gate.held) == 2,
        "第二次 clear 响应进入闸门",
        page=page,
    )

    # 先释放第二次：其 ACK 分配 E+2，当前 clear 操作身份接纳。
    clear_gate.release_one(1)
    _wait_for(
        lambda: _acked(page) == (str(int(epoch0) + 2), "0") and _state(page) == "idle",
        "第二次 clear ACK 须为 epoch+2/rev0 并 idle",
        page=page,
    )

    # 再释放第一次（旧 ACK E+1）：整体忽略。
    clear_gate.release_one(0)
    page.wait_for_timeout(400)
    assert _acked(page) == (str(int(epoch0) + 2), "0"), (
        f"旧 clear ACK 不得回滚版本，实际 {_acked(page)!r}"
    )
    assert _state(page) == "idle"
    expect(page.locator("#resync-session")).to_be_hidden()
    assert not errors, f"clear ACK 重排不得产生页面 JS 异常：{errors!r}"


def test_002_cross_tab_clear_stale_ack_keeps_conflict(page, base_url) -> None:
    """跨标签页 clear：旧本地 clear ACK 迟到不得清掉跨标签页冲突/回滚 epoch。"""
    from playwright.sync_api import expect

    clear_gate = _FetchGate()
    page.route("**/api/v1/session", clear_gate.handler)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    # A 标签页 clear：服务器已处理（E+1），ACK 被闸持有。
    page.locator("#clear-session").click()
    _wait_for(
        lambda: clear_gate.count == 1 and len(clear_gate.held) == 1,
        "A clear 响应进入闸门",
        page=page,
    )

    # B 标签页（同 context/同 sid）再次 clear：epoch E+2；A 收到
    # BroadcastChannel 清空并进入冲突态（须显式 resync）。
    page_b = page.context.new_page()
    page_b.goto(base_url + "/")
    _wait_idle(page_b)
    page_b.locator("#clear-session").click()
    _wait_for(
        lambda: _state(page_b) == "idle" and _acked(page_b)[0] not in ("", epoch0),
        "B clear ACK 完成",
        page=page_b,
    )
    _wait_for(lambda: _state(page) == "conflict", "A 收到跨标签页 clear 冲突", page=page)
    expect(page.locator("#resync-session")).to_be_visible()

    # 释放 A 的旧 clear ACK（E+1）：不得清 conflict、不得写 E+1、不得 re-enable。
    clear_gate.release_one(0)
    page.wait_for_timeout(400)
    assert _state(page) == "conflict", "旧 clear ACK 不得清除跨标签页冲突"
    assert _acked(page) == ("", ""), (
        f"旧 clear ACK 不得回滚/写入 epoch，实际 {_acked(page)!r}"
    )
    expect(page.locator("#resync-session")).to_be_visible()
    page_b.close()
