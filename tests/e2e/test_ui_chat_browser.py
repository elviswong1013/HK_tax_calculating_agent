"""REQ-12/14 UI AI 对话面板浏览器 e2e：未配置态引导＋真实预览＋Escape 关闭。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；浏览器缺失时全部
SKIP，同 tests/e2e/test_ui_browser.py 惯例）。SSR 结构/接线断言在
tests/ui/test_ui_chat.py（httpx），本文件补充只能真实浏览器验证的行为：
  - 未配置态面板引导可见（§7 文案＋非密钥 env 名 HKTAX_MODEL_ENDPOINT/
    HKTAX_MODEL_NAME；密钥变量名 HKTAX_MODEL_API_KEY 不上面板——仅
    /settings 与 README 的配置说明，同 tests/ai/test_ai_outbound.py 密钥
    安全姿态）；
  - 输入问题点发送 → 授权预览对话框打开且显示**真实**负载（字节数>0、
    sha256 64hex、逐字含用户消息——经真实 POST /ai/consent/preview 往返，
    未配置态亦可预览，全程不需要模型/网络）；
  - Escape 关闭非破坏性弹窗（§9）。

REQ: REQ-12（Annex A §9）＋REQ-14（Annex A §7/§12.5；specs/001 §9）。
期望值来源: 结构/行为断言（文案、hash 形状）；无金额期望。

【拟名】契约（同 tests/ui/test_ui_chat.py；Green 阶段须按测试实现）:
  - 面板输入 <label for="chat-input">「你的问题」＋发送按钮 #chat-send；
    点发送先开 #consent-dialog（role=dialog 由脚本补上），字段
    #consent-bytes/#consent-hash/#consent-payload/#consent-endpoint 由
    POST /api/v1/ai/consent/preview 真实响应填充（CSRF 经安全 GET 探测，
    同既有「清空本次资料」接线模式）。
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time

import pytest

pytestmark = [pytest.mark.e2e]

# C4.2 模型三 env：本文件全程「未配置态」——弹出并待服务停止后恢复
_MODEL_ENV_KEYS = ("HKTAX_MODEL_ENDPOINT", "HKTAX_MODEL_NAME", "HKTAX_MODEL_API_KEY")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    """受控回环启动被测应用（真实 uvicorn；单 worker；临时 DB；模型三 env
    全清＝未配置态——本文件断言全程不需要模型/网络）。

    同 tests/e2e/test_ui_browser.py 的服务夹具（本目录无 conftest，文件自含）。
    """
    import uvicorn

    db_dir = tmp_path_factory.mktemp("e2e-chat")
    saved_model_env = {key: os.environ.get(key) for key in _MODEL_ENV_KEYS}
    old = os.environ.get("HKTAX_DB_PATH")
    os.environ["HKTAX_DB_PATH"] = str(db_dir / "rules.db")
    for key in _MODEL_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        from app.main import create_app  # 延迟导入：失败原因=缺失 app 行为

        app = create_app()
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
    finally:
        if old is None:
            os.environ.pop("HKTAX_DB_PATH", None)
        else:
            os.environ["HKTAX_DB_PATH"] = old
        for key, value in saved_model_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def page(base_url):
    """Chromium 页面；浏览器缺失时整文件 SKIP（安装留给 Green 阶段）。

    动作/expect 超时 5s：发送→预览含一次真实 API 往返（无需模型）。
    """
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=5000)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 —— 缺浏览器二进制＝环境未就绪
            pytest.skip(f"需要 Chromium（Green 阶段运行 playwright install chromium）：{exc}")
        context = browser.new_context()
        context.set_default_timeout(5000)
        pg = context.new_page()
        yield pg
        context.close()
        browser.close()


def test_ui_chat_unconfigured_preview_escape_e2e(page, base_url) -> None:
    """未配置态：面板显示未配置引导（含非密钥 env 名；密钥变量名不上面板）；输入问题点发送 → 授权
    预览对话框显示真实负载（字节数>0／sha256 64hex／逐字消息）；Escape
    关闭（§7/§9/§12.5；全程不需要模型）。"""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")

    # —— 未配置引导：§7 文案＋非密钥 env 名（C4.2；密钥名仅设置页/README）——
    expect(
        page.get_by_text(re.compile("AI 尚未配置|AI 未配置")).first,
        "未配置态必须显示「AI 尚未配置／AI 未配置」引导（§7）",
    ).to_be_visible()
    expect(
        page.get_by_text("HKTAX_MODEL_ENDPOINT").first,
        "未配置引导必须列出环境变量名（C4.2）",
    ).to_be_visible()

    # —— 输入问题（label 程序关联 → get_by_label 可定位）→ 点发送 ——
    question = "請幫我估算薪俸税"
    box = page.get_by_label("你的问题")
    box.wait_for(state="visible")
    box.fill(question)
    page.locator("#chat-send").click()

    # —— 先预览后发送：对话框打开（§7 不得先发送再补告知）——
    dialog = page.locator("#consent-dialog[role=dialog]")
    expect(
        dialog, "点发送必须先打开授权预览对话框（§7 先显示确认页）"
    ).to_be_visible()

    # —— 真实负载：字节数>0、SHA-256 64hex、逐字含用户消息、接收方如实 ——
    expect(
        page.locator("#consent-bytes"),
        "预览字节数必须 >0（真实冻结负载，非占位 0）",
    ).to_have_text(re.compile(r"[1-9]\d*"))
    expect(
        page.locator("#consent-hash"),
        "预览必须显示负载 SHA-256（sha256:<64hex>，C4.5）",
    ).to_have_text(re.compile(r"sha256:[0-9a-f]{64}"))
    expect(
        page.locator("#consent-payload"),
        "预览必须逐字含将发送的完整消息上下文（§7）",
    ).to_contain_text(question)
    expect(
        page.locator("#consent-endpoint"),
        "未配置态接收方必须如实显示「未配置」（§7 不虚构接收方）",
    ).to_contain_text("未配置")

    # —— Escape 关闭非破坏性弹窗（§9）；关闭后不发送（无需模型）——
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
