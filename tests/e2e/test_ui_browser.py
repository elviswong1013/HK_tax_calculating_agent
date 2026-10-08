"""M6 浏览器 e2e（REQ-12；Annex A §9/§12.9 浏览器层断言）。

本文件为需要 Chromium 的独立 e2e 标记文件（pytest.mark.e2e）：
浏览器二进制未安装时全部 SKIP（Green 阶段执行 `playwright install chromium`）。
SSR 结构断言在 tests/ui/（httpx），本文件补充只能真实浏览器验证的行为：
  - 弹窗焦点进入/关闭回触发控件、Escape 关闭（Annex A §9）；
  - aria-live 通告存在、主动作原生键盘可达（§9 全键盘）;
  - 375px 与 1280px 关键流程无必需横向滚动（§4/§9；命名测试
    test_core_flows_375_and_1280 —— SDD §10 REQ-12 权威名）。

REQ: REQ-12（Annex A §3/§4/§6/§9；specs/001 §9 REQ-12 行）。
期望值来源: 结构/布局行为断言；无金额期望。
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

pytestmark = [pytest.mark.e2e]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    """受控回环启动被测应用（真实 uvicorn；单 worker；临时 DB）。"""
    import uvicorn

    db_dir = tmp_path_factory.mktemp("e2e-ui")
    old = os.environ.get("HKTAX_DB_PATH")
    os.environ["HKTAX_DB_PATH"] = str(db_dir / "rules.db")
    try:
        from app.main import create_app  # 延迟导入：失败原因=缺失 app 行为

        app = create_app()
    finally:
        if old is None:
            os.environ.pop("HKTAX_DB_PATH", None)
        else:
            os.environ["HKTAX_DB_PATH"] = old

    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", workers=1
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "e2e 服务器未能在 15s 内启动（127.0.0.1 受控回环）"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def page(base_url):
    """Chromium 页面；浏览器缺失时整文件 SKIP（安装留给 Green 阶段）。"""
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=3000)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 —— 缺浏览器二进制＝环境未就绪
            pytest.skip(f"需要 Chromium（Green 阶段运行 playwright install chromium）：{exc}")
        context = browser.new_context()
        pg = context.new_page()
        yield pg
        context.close()
        browser.close()


def test_core_flows_375_and_1280(page, base_url) -> None:
    """375px 与 1280px 关键流程：表单可见可填、主动作可见、无必需横向滚动。"""
    for width, height in ((375, 812), (1280, 800)):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(base_url + "/")

        # 关键流程：应课税收入金额输入可见且可填（§4 表单输入宽度 100%）
        income = page.get_by_label("应课税收入")
        income.wait_for(state="visible")
        income.fill("240000")

        # 主动作可见（§4 主要操作触控高度/不遮挡）
        cta = page.get_by_role("button", name="确认资料并计算")
        cta.wait_for(state="visible")

        # 无必需横向滚动：内容不宽于视口（§4 禁止核心流程依赖横向滚动）
        scroll_width = page.evaluate("document.scrollingElement.scrollWidth")
        inner = page.evaluate("window.innerWidth")
        assert scroll_width <= inner + 1, (
            f"{width}px 视口出现必需横向滚动（scrollWidth={scroll_width} > innerWidth={inner}）"
        )


def test_ui_dialog_focus_and_escape_e2e(page, base_url) -> None:
    """确认摘要弹窗：打开后焦点进入弹窗；Escape 关闭；焦点回到触发控件（§9）。"""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")

    trigger = page.get_by_role("button", name="确认资料并计算")
    trigger.wait_for(state="visible")
    trigger.click()

    dialog = page.locator("[role=dialog]")
    expect(dialog).to_be_visible()

    # 焦点已进入弹窗
    focus_in_dialog = dialog.evaluate(
        "el => el.contains(document.activeElement)"
    )
    assert focus_in_dialog, "弹窗打开后焦点必须进入弹窗（§9）"

    # Escape 关闭非破坏性弹窗
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    # 焦点回到触发控件
    back_on_trigger = trigger.evaluate(
        "el => el === document.activeElement || el.contains(document.activeElement)"
    )
    assert back_on_trigger, "弹窗关闭后焦点必须回到触发控件（§9）"


def test_ui_aria_live_and_keyboard_e2e(page, base_url) -> None:
    """aria-live 状态通告存在；主动作/金额输入为原生可聚焦控件（全键盘，§9）。"""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")

    live = page.locator("[aria-live], [role=status], [role=alert]")
    expect(live.first).to_be_attached()

    cta = page.get_by_role("button", name="确认资料并计算")
    cta.focus()
    active = cta.evaluate("el => document.activeElement === el")
    assert active, "确认按钮必须是原生可聚焦控件（§9 全程键盘可操作）"

    income = page.get_by_label("应课税收入")
    income.focus()
    income_focused = income.evaluate("el => document.activeElement === el")
    assert income_focused, "金额输入必须是原生可聚焦控件（§9）"
