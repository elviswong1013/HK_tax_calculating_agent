# -*- coding: utf-8 -*-
"""REQ-11 clear/异步竞态与 Operation ACK 完整性浏览器 e2e（002 P2 repair4）。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；Chromium 不可用＝
BLOCKED，skip 不计通过；共享隔离/启动见 tests/e2e/conftest.py，006 §6 ①–⑤）。

【被测契约】
  - 004 §7/§196：clear 是独立 epoch 边界。clear 后旧 epoch/nonce 的
    execute/confirm、AI preview/chat 响应整体丢弃：不 addRecord、不渲染结果、
    不进入聊天成功、不恢复 proposal/pendingPreview、不写 chatHistory/DOM。
  - 004 §194/§195：mutation ACK 必须按操作关联完整核验；Applied=1 但
    Operation-Epoch/Revision 缺失/畸形/非整数 → 冲突态，不推进版本、不
    pumpQueue。prepare 422 无有效 ACK → 冲突态（不得只显示 questions）。
    prepare 网络失败/409 携排队编辑 → 先冲突、不得发出排队 invalidate。
    413/500 带有效 operation ACK → 接纳 ACK 版本，丢弃陈旧 payload；若
    prepare 在途有编辑，用 ACK 后 revision 排队 invalidate。
  - 普通编辑（非 clear）前合法完成的旧 execute 仍可存为历史 record；
    不得成为当前结果。
"""

from __future__ import annotations

import json
import re
import time

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def e2e_expect_timeout() -> int:
    return 10000


# ------------------------------------------------------------------ helpers
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


def _fill_q1(page) -> None:
    page.get_by_label("应课税收入").fill("240000")
    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page.locator("#provisional-paid").fill("0")


def _submit_and_confirm(page) -> None:
    from playwright.sync_api import expect

    page.get_by_role("button", name="确认资料并计算").first.click()
    dialog = page.locator("#confirm-dialog[role=dialog]")
    expect(dialog, "提交后须出现确认摘要弹窗").to_be_visible()
    page.locator("#confirm-submit").click()


def _clear_and_wait(page, old_epoch: str, *, programmatic: bool = False) -> None:
    if programmatic:
        # consent 弹窗覆盖 clear 按钮时，用 DOM click 触发同一 clear handler
        # （等价于其他标签页清空；本测试只针对响应竞态，不验证指针可达性）。
        page.evaluate("document.getElementById('clear-session').click()")
    else:
        page.locator("#clear-session").click()
    _wait_for(
        lambda: _acked(page)[0] not in ("", old_epoch) and _state(page) == "idle",
        "clear 操作 ACK 后回到 idle（新 epoch）",
        page=page,
    )


class _FetchGate:
    """route.fetch()（服务器已处理）后暂缓 fulfill，由测试显式释放。"""

    def __init__(self) -> None:
        self.held: list = []
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        response = route.fetch()
        self.held.append((route, response))

    def release_all(self) -> None:
        held = self.held[:]
        self.held.clear()
        for route, response in held:
            route.fulfill(response=response)

    @property
    def count(self) -> int:
        return len(self.requests)


class _HoldGate:
    """暂缓请求，不触达服务器；由测试 fulfill/abort。"""

    def __init__(self) -> None:
        self.held: list = []
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        self.held.append(route)

    def fulfill(self, *, status: int = 200, headers: dict | None = None, body: str = "") -> None:
        held = self.held[:]
        self.held.clear()
        for route in held:
            route.fulfill(status=status, headers=headers or {}, body=body)

    def abort(self) -> None:
        held = self.held[:]
        self.held.clear()
        for route in held:
            route.abort("failed")

    @property
    def count(self) -> int:
        return len(self.requests)


class _Counter:
    def __init__(self) -> None:
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        route.continue_()

    @property
    def count(self) -> int:
        return len(self.requests)

    def bodies(self) -> list:
        out = []
        for request in self.requests:
            try:
                out.append(json.loads(request.post_data or "{}"))
            except json.JSONDecodeError:
                out.append({"__raw__": request.post_data})
        return out


def _page_errors(page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


# ------------------------------------------------------------------ A. clear
def test_002_clear_drops_late_execute_success(page, base_url) -> None:
    """延迟 execute 成功→clear→释放：不恢复记录/结果。"""
    from playwright.sync_api import expect

    execute_gate = _FetchGate()
    page.route("**/api/v1/calc/salaries-tax", execute_gate.handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    _fill_q1(page)
    _wait_idle(page)
    _submit_and_confirm(page)
    _wait_for(lambda: execute_gate.count == 1, "execute 请求进入闸门", page=page)
    assert len(execute_gate.held) == 1

    _clear_and_wait(page, epoch0)
    execute_gate.release_all()
    page.wait_for_timeout(400)

    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.locator("#records-list .record-card")).to_have_count(0)
    assert "计算完成" not in page.locator("#status-region").inner_text()
    assert not errors, f"clear/execute 竞态不得产生页面 JS 异常：{errors!r}"


def test_002_ordinary_edit_keeps_completed_execute_as_history(page, base_url) -> None:
    """普通编辑（非 clear）前完成的旧 execute → 历史 record，且不回写当前结果。"""
    from playwright.sync_api import expect

    execute_gate = _FetchGate()
    page.route("**/api/v1/calc/salaries-tax", execute_gate.handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    _fill_q1(page)
    _wait_idle(page)
    _submit_and_confirm(page)
    _wait_for(lambda: execute_gate.count == 1, "execute 请求进入闸门", page=page)

    # 普通编辑：generation 前进、invalidate 完成；不是 clear。
    page.get_by_label("应课税收入").fill("250000")
    _wait_idle(page)
    execute_gate.release_all()
    _wait_for(
        lambda: page.locator("#records-list .record-card").count() == 1,
        "旧 execute 存为单条历史记录",
        page=page,
    )
    card_text = page.locator("#records-list .record-card").inner_text()
    assert "历史记录" in card_text, f"应为历史 record，实际：{card_text!r}"
    expect(page.locator("#result-section")).to_be_hidden()
    assert "历史记录" in page.locator("#status-region").inner_text()
    assert not errors, f"普通编辑/历史记录不得产生页面 JS 异常：{errors!r}"


def test_002_clear_drops_late_preview_and_chat_history(page, base_url) -> None:
    """延迟 preview→clear→释放：无 pendingPreview/聊天历史恢复；后续预览只含新问题。"""
    from playwright.sync_api import expect

    preview_gate = _HoldGate()
    state = {"open": False}

    def _preview_handler(route) -> None:
        if state["open"]:
            route.continue_()
            return
        preview_gate.handler(route)

    page.route("**/api/v1/ai/consent/preview", _preview_handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    old_question = "舊問題：清空前的內容"
    page.get_by_label("你的问题").fill(old_question)
    page.locator("#chat-send").click()
    _wait_for(lambda: preview_gate.count == 1, "preview 请求进入闸门", page=page)

    _clear_and_wait(page, epoch0)
    state["open"] = True
    assert page.locator("#chat-input").input_value() == "", "clear 须清空聊天输入"
    preview_gate.fulfill(
        status=201,
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "consent_id": "stale-preview",
                "status": "prepared",
                "payload": {},
                "payload_sha256": "0" * 64,
                "payload_bytes": 0,
                "model": "",
                "endpoint": "/api/v1/ai/chat",
                "expires_in": 300,
            }
        ),
    )
    page.wait_for_timeout(400)
    expect(page.locator("#consent-dialog")).to_be_hidden()
    assert page.locator("#chat-messages li").count() == 0, "迟到 preview 不得打开/写聊天"

    # 后续显式发送仍可预览；外发消息不得拼接清空前的旧历史。
    page.get_by_label("你的问题").fill("新問題")
    page.locator("#chat-send").click()
    dialog = page.locator("#consent-dialog[role=dialog]")
    expect(dialog).to_be_visible()
    # 负载在折叠的 <details> 内：inner_text() 为“未渲染”，须读 textContent。
    payload_text = page.locator("#consent-payload").text_content()
    assert "新問題" in payload_text
    assert old_question not in payload_text, "clear 后不得恢复旧 chatHistory"
    page.locator("#consent-deny").click()
    assert not errors, f"clear/preview 竞态不得产生页面 JS 异常：{errors!r}"


def test_002_clear_drops_late_chat_success_and_proposal(page, base_url) -> None:
    """延迟 AI chat 成功→clear→释放：不进入聊天/提案，不恢复 pendingPreview。"""
    from playwright.sync_api import expect

    chat_gate = _HoldGate()
    page.route("**/api/v1/ai/chat", chat_gate.handler)
    errors = _page_errors(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)

    page.get_by_label("你的问题").fill("清空前發送")
    page.locator("#chat-send").click()
    expect(page.locator("#consent-dialog[role=dialog]")).to_be_visible()
    page.locator("#consent-allow").click()
    _wait_for(lambda: chat_gate.count == 1, "chat 请求进入闸门", page=page)

    _clear_and_wait(page, epoch0, programmatic=True)
    chat_gate.fulfill(
        status=200,
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "consent_id": "stale-consent",
                "sent_bytes": 1,
                "status": "completed",
                "content": "遲到回覆",
                "unconfirmed": True,
                "tool_results": [
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
                ],
            },
            ensure_ascii=False,
        ),
    )
    page.wait_for_timeout(400)
    expect(page.locator("#consent-dialog")).to_be_hidden()
    assert page.locator("#chat-messages li").count() == 0, "迟到 chat 不得写入聊天"
    assert page.locator(".proposal-diff").count() == 0, "迟到 proposal 不得恢复"
    assert not errors, f"clear/chat 竞态不得产生页面 JS 异常：{errors!r}"


def test_002_ai_discarded_status_is_not_success(page, base_url, e2e_isolation) -> None:
    """服务器 status='discarded'：走丢弃分支，不写聊天/提案。"""
    from playwright.sync_api import expect

    chat_gate = _HoldGate()
    page.route("**/api/v1/ai/chat", chat_gate.handler)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    question = "discarded 分支問題"
    page.get_by_label("你的问题").fill(question)
    page.locator("#chat-send").click()
    expect(page.locator("#consent-dialog[role=dialog]")).to_be_visible()
    page.locator("#consent-allow").click()
    _wait_for(lambda: chat_gate.count == 1, "chat 请求进入闸门", page=page)

    chat_gate.fulfill(
        status=200,
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "consent_id": "discarded-consent",
                "sent_bytes": 1,
                "status": "discarded",
                "content": None,
                "unconfirmed": True,
            },
            ensure_ascii=False,
        ),
    )
    page.wait_for_timeout(400)
    expect(page.locator("#consent-dialog")).to_be_hidden()
    assert page.locator("#chat-messages li").count() == 0, "discarded 不得进入聊天成功"
    assert page.locator(".proposal-diff").count() == 0
    assert page.get_by_label("你的问题").input_value() == question, (
        "discarded 不应清除用户尚未发送的问题"
    )
    assert e2e_isolation.model.send_calls == 0


# ------------------------------------------------------------------ B. ACK
def test_002_invalidate_missing_ack_enters_conflict(page, base_url) -> None:
    """invalidate 200 无 Operation ACK → 冲突态，不接纳版本。"""
    from playwright.sync_api import expect

    page.route(
        "**/api/v1/calc/invalidate",
        lambda route: route.fulfill(status=200, headers={}, body="{}"),
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, revision0 = _acked(page)

    page.get_by_label("应课税收入").fill("240000")
    _wait_for(lambda: _state(page) == "conflict", "缺 ACK 须进入冲突态", page=page)
    expect(page.locator("#resync-session")).to_be_visible()
    assert _acked(page) == (epoch0, revision0), "缺 ACK 不得推进版本"


def test_002_invalidate_malformed_ack_enters_conflict(page, base_url) -> None:
    """invalidate 200 Applied=1 但版本 header 畸形 → 冲突态，不接纳版本。"""
    from playwright.sync_api import expect

    page.route(
        "**/api/v1/calc/invalidate",
        lambda route: route.fulfill(
            status=200,
            headers={
                "X-Edit-Applied": "1",
                "X-Edit-Operation-Epoch": "not-an-int",
                "X-Edit-Operation-Revision": "1",
            },
            body="{}",
        ),
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, revision0 = _acked(page)

    page.get_by_label("应课税收入").fill("240000")
    _wait_for(lambda: _state(page) == "conflict", "畸形 ACK 须进入冲突态", page=page)
    expect(page.locator("#resync-session")).to_be_visible()
    assert _acked(page) == (epoch0, revision0), "畸形 ACK 不得推进版本"


def test_002_prepare_422_missing_ack_enters_conflict_no_questions(page, base_url) -> None:
    """prepare 422 无有效 ACK：冲突态，不得仅显示 questions 后结束。"""
    from playwright.sync_api import expect

    prepare_gate = _HoldGate()
    page.route("**/api/v1/calc/prepare", prepare_gate.handler)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)
    _fill_q1(page)
    _wait_idle(page)
    _submit_and_confirm(page)
    _wait_for(lambda: prepare_gate.count == 1, "prepare 请求进入闸门", page=page)

    prepare_gate.fulfill(
        status=422,
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "error": {
                    "code": "E_ELIGIBILITY_UNKNOWN",
                    "message_zh": "存在未确定事实",
                },
                "questions": [
                    {
                        "field": "years.2025_26.household.marriage",
                        "question_zh": "请确认婚姻期间",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    _wait_for(lambda: _state(page) == "conflict", "422 无 ACK 须进入冲突态", page=page)
    expect(page.locator("#resync-session")).to_be_visible()
    status_text = page.locator("#status-region").inner_text()
    assert "需要补充资料" not in status_text, (
        f"无有效 ACK 的 422 不得仅显示 questions，实际状态：{status_text!r}"
    )
    expect(page.locator("#error-summary")).to_be_hidden()


def _prepare_with_queued_edit(page, base_url, prepare_gate, invalidate_counter):
    epoch0, revision0 = _acked(page)
    _fill_q1(page)
    _wait_idle(page)
    epoch0, revision0 = _acked(page)
    _submit_and_confirm(page)
    _wait_for(lambda: prepare_gate.count == 1, "prepare 请求进入闸门", page=page)
    invalidate_baseline = invalidate_counter.count
    page.get_by_label("应课税收入").fill("250000")
    page.wait_for_timeout(150)
    assert invalidate_counter.count == invalidate_baseline, (
        "prepare 在途编辑只排队 invalidate，不得并行发送"
    )
    return epoch0, revision0, invalidate_baseline


def test_002_prepare_network_failure_with_queued_edit_no_pump(page, base_url) -> None:
    """prepare 网络失败 + 排队编辑 → 冲突态；不得 pump 出排队 invalidate。"""
    from playwright.sync_api import expect

    prepare_gate = _HoldGate()
    invalidate_counter = _Counter()
    page.route("**/api/v1/calc/prepare", prepare_gate.handler)
    page.route("**/api/v1/calc/invalidate", invalidate_counter.handler)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    _, _, baseline = _prepare_with_queued_edit(
        page, base_url, prepare_gate, invalidate_counter
    )
    prepare_gate.abort()
    _wait_for(lambda: _state(page) == "conflict", "网络失败须进入冲突态", page=page)
    expect(page.locator("#resync-session")).to_be_visible()
    assert invalidate_counter.count == baseline, "冲突后不得 pump 排队 invalidate"


def test_002_prepare_409_with_queued_edit_no_pump(page, base_url) -> None:
    """prepare 409 + 排队编辑 → 冲突态；不得 pump 出排队 invalidate。"""
    from playwright.sync_api import expect

    prepare_gate = _HoldGate()
    invalidate_counter = _Counter()
    page.route("**/api/v1/calc/prepare", prepare_gate.handler)
    page.route("**/api/v1/calc/invalidate", invalidate_counter.handler)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    _, _, baseline = _prepare_with_queued_edit(
        page, base_url, prepare_gate, invalidate_counter
    )
    prepare_gate.fulfill(
        status=409,
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {"error": {"code": "E_CONFIRMATION_STALE", "message_zh": "版本冲突"}},
            ensure_ascii=False,
        ),
    )
    _wait_for(lambda: _state(page) == "conflict", "409 须进入冲突态", page=page)
    expect(page.locator("#resync-session")).to_be_visible()
    assert invalidate_counter.count == baseline, "冲突后不得 pump 排队 invalidate"


@pytest.mark.parametrize(
    ("status", "code"),
    [(413, "E_INPUT_TOO_LARGE"), (500, "E_INTERNAL")],
)
def test_002_prepare_413_500_with_ack_accepts_version(
    page, base_url, status, code
) -> None:
    """prepare 413/500 携有效 Operation ACK：接纳版本并用 ACK revision 发排队 invalidate。"""
    from playwright.sync_api import expect

    prepare_gate = _HoldGate()
    invalidate_counter = _Counter()
    invalidate_bodies: list = []

    def _invalidate_handler(route) -> None:
        body = json.loads(route.request.post_data or "{}")
        invalidate_bodies.append(body)
        epoch = body["expected_session_epoch"]
        revision = body["expected_input_revision"] + 1
        route.fulfill(
            status=200,
            headers={
                "X-Edit-Applied": "1",
                "X-Edit-Operation-Epoch": str(epoch),
                "X-Edit-Operation-Revision": str(revision),
            },
            body="{}",
        )

    page.route("**/api/v1/calc/prepare", prepare_gate.handler)
    page.route("**/api/v1/calc/invalidate", _invalidate_handler)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    epoch0, revision0, baseline = _prepare_with_queued_edit(
        page, base_url, prepare_gate, invalidate_counter
    )
    invalidate_bodies.clear()  # 只观察 prepare ACK 后释放的排队 invalidate
    prepare_ack_revision = int(revision0) + 1
    prepare_gate.fulfill(
        status=status,
        headers={
            "Content-Type": "application/json",
            "X-Edit-Applied": "1",
            "X-Edit-Operation-Epoch": epoch0,
            "X-Edit-Operation-Revision": str(prepare_ack_revision),
        },
        body=json.dumps(
            {"error": {"code": code, "message_zh": "服务端拒绝"}},
            ensure_ascii=False,
        ),
    )
    _wait_for(
        lambda: len(invalidate_bodies) == 1 and _state(page) == "idle",
        "413/500 有效 ACK 后按新 revision 发送排队 invalidate",
        page=page,
    )
    assert invalidate_bodies[0]["expected_session_epoch"] == int(epoch0)
    assert invalidate_bodies[0]["expected_input_revision"] == prepare_ack_revision, (
        f"排队 invalidate 须使用 prepare ACK revision，实际 {invalidate_bodies[0]!r}"
    )
    assert _acked(page) == (epoch0, str(prepare_ack_revision + 1))
    assert page.locator("#resync-session").is_hidden(), "有效 ACK 不得误入冲突态"
    expect(page.locator("#error-summary")).to_be_visible()
