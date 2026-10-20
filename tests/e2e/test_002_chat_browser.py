# -*- coding: utf-8 -*-
"""REQ-1/11 聊天本地支持范围浏览器 e2e（002 P2 W3 flow 29；004 §3.9；005 §17）。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；Chromium 不可用＝
BLOCKED，skip 不计通过；共享隔离/启动见 tests/e2e/conftest.py，006 §6 ①–⑤）。

【被测契约】（`test_002_chat_local_coverage_offline_no_outbound`）:
  - 模型未配置/离线下，`chat-local-coverage-toggle` 按钮可键盘展开/收起
    同卡片本地区块 `chat-local-coverage-panel`（aria-expanded/aria-controls）；
    面板标题「系统支持范围（本地说明，非模型回答）」；内容来自当前 SSR
    共享数据源；含 /coverage 链接。
  - 展开/收起焦点留在按钮；375px 下无必需横向滚动。
  - 全程 preview/grant/chat/模型外发均 0；chatHistory/messages 不增加本地说明。
  - 之后用户显式发送仍走既有冻结 preview→单次 grant→一次 send 流程
    （deny 路径：预览后拒绝，无外发），coverage 内容不进入外发负载。
"""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.e2e]

_MODEL_ENV_KEYS = ("HKTAX_MODEL_ENDPOINT", "HKTAX_MODEL_NAME", "HKTAX_MODEL_API_KEY")


@pytest.fixture
def e2e_env_overrides() -> dict:
    """未配置/离线态：三模型 env 在显式 app 构造前弹出（teardown 恢复）。"""
    return {key: None for key in _MODEL_ENV_KEYS}


@pytest.fixture
def e2e_expect_timeout() -> int:
    return 5000


def test_002_chat_local_coverage_offline_no_outbound(page, base_url, e2e_isolation) -> None:
    """离线本地查看支持范围：键盘展开/收起、焦点留在按钮、零外发、零消息注入；
    后续显式发送仍为独立 consent 流程。"""
    from playwright.sync_api import expect

    # —— 出站计数（preview/grant/chat 一律计数并放行；断言期间为零）——
    ai_calls = {"preview": 0, "grant": 0, "chat": 0}

    def _count(route):
        url = route.request.url
        if "/api/v1/ai/consent/preview" in url:
            ai_calls["preview"] += 1
        elif "/api/v1/ai/chat" in url:
            ai_calls["chat"] += 1
        elif "/api/v1/ai/consent" in url:
            ai_calls["grant"] += 1
        route.continue_()

    page.route("**/api/v1/ai/**", _count)

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")

    toggle = page.locator("#chat-local-coverage-toggle")
    panel = page.locator("#chat-local-coverage-panel")
    expect(toggle, "聊天卡标题行须有「支持范围」紧凑次要按钮（004 §3.9）").to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_have_attribute("aria-controls", "chat-local-coverage-panel")
    expect(panel).to_be_hidden()

    # —— 键盘展开（004 §3.9：键盘可操作；焦点留在按钮）——
    toggle.focus()
    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    assert page.evaluate("document.activeElement && document.activeElement.id") == (
        "chat-local-coverage-toggle"
    ), "展开后焦点必须留在按钮（004 §3.9）"

    # —— 面板内容：本地说明（非模型回答）＋当前 SSR 共享源条目＋/coverage 链接 ——
    expect(panel).to_contain_text("系统支持范围（本地说明，非模型回答）")
    expect(panel).to_contain_text("薪俸税")
    expect(panel).to_contain_text("可计算")
    expect(panel.get_by_text(re.compile("评税选择")).first).to_be_visible()
    link = panel.get_by_role("link", name=re.compile("coverage"))
    expect(link).to_have_attribute("href", "/coverage")

    # —— 375px 可读：无必需横向滚动（§4/§9）——
    page.set_viewport_size({"width": 375, "height": 812})
    expect(panel).to_be_visible()
    scroll_width = page.evaluate("document.scrollingElement.scrollWidth")
    inner = page.evaluate("window.innerWidth")
    assert scroll_width <= inner + 1, (
        f"375px 下面板不得引入横向滚动（{scroll_width} > {inner}）"
    )

    # —— 键盘收起：焦点仍在按钮 ——
    page.keyboard.press("Enter")
    expect(panel).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    assert page.evaluate("document.activeElement && document.activeElement.id") == (
        "chat-local-coverage-toggle"
    ), "收起后焦点必须仍在按钮"

    # —— 零外发/零注入：preview/grant/chat 均 0；消息区无本地说明注入 ——
    assert ai_calls == {"preview": 0, "grant": 0, "chat": 0}, (
        f"本地覆盖查看不得触发任何 AI 端点：{ai_calls!r}"
    )
    assert e2e_isolation.model.send_calls == 0, "本地覆盖查看不得产生模型外发"
    assert page.locator("#chat-messages li").count() == 0, (
        "本地说明不得注入 chatHistory/消息区"
    )

    # —— 后续显式发送仍走既有冻结流程：预览 →（拒绝）→ 无外发 ——
    page.set_viewport_size({"width": 1280, "height": 800})
    page.get_by_label("你的问题").fill("支持範圍之外可以計算嗎？")
    page.locator("#chat-send").click()
    dialog = page.locator("#consent-dialog[role=dialog]")
    expect(dialog, "显式发送仍须先经冻结预览（§7/§12.5）").to_be_visible()
    assert ai_calls["preview"] == 1, "显式发送恰一次预览"
    # 预览负载不夹带 coverage/用户 facts 注入键（payload 五键白名单由后端冻结）
    payload_text = page.locator("#consent-payload").inner_text()
    assert "coverage" not in payload_text, "外发预览不得夹带 coverage 本地说明"
    page.locator("#consent-deny").click()
    expect(dialog).to_be_hidden()
    assert ai_calls["grant"] == 0 and ai_calls["chat"] == 0, (
        "拒绝后不得授予/发送（无自动补发）"
    )
    assert e2e_isolation.model.send_calls == 0
