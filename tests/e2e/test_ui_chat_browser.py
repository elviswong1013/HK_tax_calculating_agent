"""REQ-12/14 UI AI 对话面板浏览器 e2e：未配置态引导＋真实预览＋Escape 关闭。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；Chromium 不可用＝
BLOCKED，skip 不计通过；共享隔离/启动见 tests/e2e/conftest.py，006 §6 ①–⑤）。
SSR 结构/接线断言在 tests/ui/test_ui_chat.py（httpx），本文件补充只能真实浏览器验证的行为：
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

import re

import pytest

pytestmark = [pytest.mark.e2e]

# C4.2 模型三 env：本文件全程「未配置态」——app 构造前弹出，测试后恢复
_MODEL_ENV_KEYS = ("HKTAX_MODEL_ENDPOINT", "HKTAX_MODEL_NAME", "HKTAX_MODEL_API_KEY")


@pytest.fixture
def e2e_env_overrides() -> dict[str, str | None]:
    """聊天面板未配置态：三模型 env 在显式 app 构造前弹出（teardown 恢复）。"""
    return {key: None for key in _MODEL_ENV_KEYS}


@pytest.fixture
def e2e_expect_timeout() -> int:
    """发送→预览含一次真实 API 往返（无需模型），超时 5s。"""
    return 5000


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
