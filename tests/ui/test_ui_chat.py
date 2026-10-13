"""REQ-12/14（交叉 REQ-11）UI AI 对话面板：把静态「AI 引导（可选）」卡片
升级为可用对话面板的结构／流程／渲染契约（httpx 层 Red）。

REQ: REQ-12（Annex A §9 可访问性与响应行为；§12.9 命名测试
test_ui_chat_panel_structure_a11y）＋REQ-14（§7/§12.5 AI 外发授权：
预览→授权→单次发送；拒绝/错误后本地照常）＋REQ-11 交叉（§6 工具结果与
「待你确认」；C3.6 unconfirmed 自由文本非权威）。
规格锚点:
  - Annex A §7：初始态「本地计算可用；AI 未连接／未授权」；未配置文案
    「AI 尚未配置。你仍可填写表格并在本机计算。」；确认页逐字完整负载＋
    接收方（可识别端点/域名）＋字节数＋SHA-256；「返回修改」「本次允许发送」
    「不发送，继续本地使用」；拒绝后表单与确定性计算照常；超时文案
    「模型暂时没有回应；你的资料和计算结果未受影响。可重试，或继续使用表格。」。
  - Annex A §9：状态更新对屏幕阅读器可感知（aria-live）、程序关联 label、
    全程键盘可操作、375px 无必需横向滚动。
  - Annex A §12.5：预览＝将发送的确切完整 JSON＋字节数＋SHA-256；单次授权；
    上下文/工具结果/端点/模型变化或任何重试 → 新预览＋新授权、绝不自动补发；
    模型错误保留输入与结果、不渲染模型返回的 HTML。
  - Annex A §12.8：POST ai/consent/preview → 授权预览对话框（焦点进入）；
    POST ai/chat 403/409 consent 失效 → 「重新预览并授权」＋aria-live 通告；
    502 模型错误 → 保留表单与结果。
  - Annex C C3.7（E_MODEL_UNCONFIGURED 409／E_MODEL_TIMEOUT 502）、C4.2
    （配置仅服务端 env 三变量）、C4.5/C4.6/C4.8（冻结字节→授予→单次消费）、
    C5.5（无财务 localStorage；§12.3 金额一律字符串——无 parseFloat）。
期望值来源: 结构/状态断言（控件、接线、错误码、hash）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 面板（工作台 AI 卡片）含消息区 id="chat-messages"（aria-live，§9：
    助手回复/错误/拒绝通告对读屏可感知）＋文本输入 id="chat-input"
    （<label for="chat-input">「你的问题」——程序关联 label，可被
    get_by_label 定位）＋发送按钮 id="chat-send"（原生 <button>，文案含
    「发送」）。
  - 点发送 → POST /api/v1/ai/consent/preview（messages=[{role:"user",
    content:输入}]），以**真实响应**填充既有授权对话框字段
    （#consent-endpoint/#consent-bytes/#consent-hash/#consent-payload；
    payload_bytes/payload_sha256/consent_id 逐字来自响应）；预览后不自动发送。
  - 「本次允许发送」→ POST /api/v1/ai/consent {consent_id} → POST
    /api/v1/ai/chat {consent_id, messages}（单次授权单次发送）；助手自由
    文本渲染进消息区并标「未确认」（C3.6 unconfirmed；自由文本不带权威
    金额样式，§6）。
  - 「不发送，继续本地使用」→ 关闭对话框；输入保留；消息区通告
    「已选择不发送…」（本地功能照常，§7）。
  - 第二条消息/任何变化 → 不得沿用旧授权；chat 403/409 → 消息区通告含
    「重新预览并授权」（§12.8 该行 UI 状态）。
  - tool_results 渲染：工具建议/结果进消息区并标「待你确认」（对话不能
    取代输入确认，§6）；模型内容一律 textContent 渲染（页面不得出现
    innerHTML——§12.5 不渲染模型返回的 HTML）。
  - 模型错误（E_MODEL_TIMEOUT 等）→ §7 权威文案「模型暂时没有回应…」
    「…未受影响…」；输入与本地结果保留。
  - 未配置态（三 env 全缺）→ 面板显示「AI 未配置／AI 尚未配置」引导＋
    两个非密钥 env 名（HKTAX_MODEL_ENDPOINT/HKTAX_MODEL_NAME）＋密钥指引
    文案（服务端密钥环境变量名称见设置页/README）；首页**不得**出现
    HKTAX_MODEL_API_KEY 字面量（与 tests/ai/test_ai_outbound.py
    test_keys_server_only_never_browser_log_export 同姿态：密钥变量名仅
    /settings 与 README 的配置说明）；本地计算不受影响。
  - 页面脚本以字面路径引用三个 /api/v1/ai/* 端点（接线证据——httpx 层
    无法执行 JS；真实浏览器行为由 tests/e2e/test_ui_chat_browser.py 断言）。
  - 页面不得出现 parseFloat（金额一律字符串，§12.3）与 localStorage（C5.5）。
  - 假出站＝app.state.model_outbound 接缝注入（同 tests/ai/_ai_fakes.
    FakeModelOutbound 的接缝契约之进程内同构实现；tests/ui 车道独立维护，
    不跨目录导入）。
"""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace

import httpx
import pytest

from _helpers import (
    AMOUNT_KEYS,
    audit_workbench,
    complete_calc,
    fetch_asset,
    fixed_width_violations,
    salaries_input,
)

# —— 模型配置 env（C4.2：密钥仅服务端 env；endpoint/model 非秘密）——
_MODEL_ENV = ("HKTAX_MODEL_ENDPOINT", "HKTAX_MODEL_NAME", "HKTAX_MODEL_API_KEY")
# 首页可见的非密钥 env 名（密钥变量名 HKTAX_MODEL_API_KEY 仅 /settings 与
# README 的配置说明——同 tests/ai/test_ai_outbound.py 密钥安全姿态）
_MODEL_ENV_PUBLIC = ("HKTAX_MODEL_ENDPOINT", "HKTAX_MODEL_NAME")
# 测试端点：回环 9 端口（真实路径若被误用 → 连接立即拒绝，测试不悬挂）
_TEST_ENDPOINT = "https://127.0.0.1:9/v1/chat/completions"
_TEST_MODEL = "hktax-ui-test-model"
_TEST_API_KEY = "sk-UI-TEST-ONLY-2610"

_QUESTION_A = "請幫我估算薪俸税"
_QUESTION_B = "再幫我比較個人入息課稅"
_MSG_A = [{"role": "user", "content": _QUESTION_A}]
# 第二条消息＝完整上下文（首轮问答回传＋新问题；§12.5 上下文变化 → 新授权）
_MSG_B = [
    {"role": "user", "content": _QUESTION_A},
    {"role": "assistant", "content": "第一轮回复（假出站）"},
    {"role": "user", "content": _QUESTION_B},
]
_ASSISTANT_TEXT = "這是假出站回覆，僅供參考。"


# ---------------------------------------------------------------------------
# 假出站与 wire 响应构造（同 tests/ai/_ai_fakes 接缝契约；UI 车道独立维护）
# ---------------------------------------------------------------------------
def _wire_text_body(text: str) -> dict:
    """纯文本 assistant 响应（content 非空，无 tool_calls）。"""
    return {
        "status_code": 200,
        "body": json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": text}}]},
            ensure_ascii=False,
        ).encode("utf-8"),
    }


def _wire_tool_calls_body(calls: list[dict], content=None) -> dict:
    """assistant tool_calls 响应；arguments 为 JSON 字符串（wire 层契约）。"""
    wire_calls = []
    for call in calls:
        args = call["arguments"]
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        wire_calls.append(
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": args},
            }
        )
    return {
        "status_code": 200,
        "body": json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": wire_calls,
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8"),
    }


class _FakeModelOutbound:
    """进程内假出站：记录每次外发的确切冻结字节与密钥通道（零依赖）。

    script 逐次弹出（dict=wire 响应；BaseException=传输级失败）；空则回落
    200 纯文本。与生产 HttpxModelOutbound 同一接缝签名。
    """

    def __init__(self, script: list | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict] = []

    def send(self, upstream_endpoint, api_key, frozen, **kwargs):
        self.calls.append(
            {
                "upstream_endpoint": upstream_endpoint,
                "api_key": api_key,
                "frozen": frozen,
                "kwargs": dict(kwargs),
            }
        )
        item = self.script.pop(0) if self.script else _wire_text_body(
            "好的（UI 假出站默认回复）"
        )
        if isinstance(item, BaseException):
            raise item
        return dict(item)


def _mirror_frozen(payload: dict) -> bytes:
    """以与 app 相同的规范化重序列化预览负载（C4.5：预览显示的 JSON 与
    字节数/SHA-256 必须对应同一冻结字节——「真实负载」一致性复核）。"""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# 面板 SSR 结构审计（stdlib HTMLParser；同 _helpers.WorkbenchAudit 思路）
# ---------------------------------------------------------------------------
class _PanelAudit(HTMLParser):
    """按 id 收集元素属性、按钮文案与 label for 关联（不新增解析依赖，C1）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.by_id: dict[str, dict] = {}
        self.label_for: set[str] = set()
        self.button_text: dict[str, str] = {}
        self._btn_id: str | None = None
        self._btn_buf: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.by_id[a["id"]] = {"tag": tag, **a}
        if tag == "label" and a.get("for"):
            self.label_for.add(a["for"])
        if tag == "button":
            self._btn_id = a.get("id") or ""
            self._btn_buf = []

    def handle_endtag(self, tag) -> None:
        if tag == "button" and self._btn_id is not None:
            self.button_text[self._btn_id] = "".join(self._btn_buf).strip()
            self._btn_id = None

    def handle_data(self, data) -> None:
        if self._btn_id is not None:
            self._btn_buf.append(data)


def _audit_panel(html: str) -> _PanelAudit:
    parser = _PanelAudit()
    parser.feed(html)
    parser.close()
    return parser


def _all_css(api, html: str) -> str:
    """内联样式＋同站链接样式表（375px 固定宽度审计用；同既有结构测试口径）。"""
    audit = audit_workbench(html)
    parts = list(audit.inline_styles)
    for href in audit.css_links:
        asset = fetch_asset(api, href)
        assert asset is not None and asset.status_code == 200, (
            f"工作台引用的样式资产必须可取（{href!r}）"
        )
        parts.append(asset.text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 夹具：在 ui 夹具之上装配模型配置态＋假出站（app.state.model_outbound 接缝）
# ---------------------------------------------------------------------------
@pytest.fixture
def chat(ui, monkeypatch):
    """模型已配置（三 env）＋假出站注入（C4.6 ⑦段接缝；不依赖真实网络）。"""
    for var in _MODEL_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HKTAX_MODEL_ENDPOINT", _TEST_ENDPOINT)
    monkeypatch.setenv("HKTAX_MODEL_NAME", _TEST_MODEL)
    monkeypatch.setenv("HKTAX_MODEL_API_KEY", _TEST_API_KEY)
    fake = _FakeModelOutbound()
    ui.app.state.model_outbound = fake
    return SimpleNamespace(app=ui.app, api=ui.api, outbound=fake)


@pytest.fixture
def chat_offline(ui, monkeypatch):
    """模型未配置（三 env 全清；C3.7 E_MODEL_UNCONFIGURED 态）＋假出站哨兵
    （任何意外外发都会被零调用断言捕获）。"""
    for var in _MODEL_ENV:
        monkeypatch.delenv(var, raising=False)
    fake = _FakeModelOutbound()
    ui.app.state.model_outbound = fake
    return SimpleNamespace(app=ui.app, api=ui.api, outbound=fake)


# ---------------------------------------------------------------------------
# 面板同一 /ai 轮次助手（面板每轮外发＝新预览＋新授予＋单次 chat，C4.6/C4.8）
# ---------------------------------------------------------------------------
def _page_html(api) -> str:
    page = api.get("/")
    assert page.status_code == 200, page.text[:300]
    assert "text/html" in page.headers.get("content-type", "")
    return page.text


def _preview(api, messages: list) -> dict:
    r = api.post("/api/v1/ai/consent/preview", json={"messages": messages})
    assert r.status_code in (200, 201), (
        f"AI 外发预览必须可用（§12.5），实际 {r.status_code}: {r.text[:300]!r}"
    )
    return r.json()


def _grant(api, consent_id: str) -> None:
    r = api.post("/api/v1/ai/consent", json={"consent_id": consent_id})
    assert r.status_code in (200, 201), (
        f"授予必须可用（C4.8 granted），实际 {r.status_code}: {r.text[:300]!r}"
    )


def _chat(api, consent_id: str, messages: list):
    return api.post(
        "/api/v1/ai/chat", json={"consent_id": consent_id, "messages": messages}
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_ui_chat_panel_structure_a11y(chat) -> None:
    """面板结构：消息区（aria-live）、程序关联 label 的文本输入、发送按钮；
    375px 无固定宽度违规；无 parseFloat/localStorage（§9/§4/§12.3/C5.5）。"""
    html = _page_html(chat.api)
    audit = _audit_panel(html)

    # —— 消息区：aria-live（§9 助手回复/错误/通告对读屏可感知）——
    region = audit.by_id.get("chat-messages")
    assert region, (
        "对话面板必须含消息区 #chat-messages（§7/§9：AI 回复与状态须可感知）"
    )
    assert region["tag"] in ("div", "section", "ul", "ol"), (
        f"消息区应为语义容器元素，实际 {region['tag']!r}"
    )
    assert region.get("aria-live") or region.get("role") in ("status", "alert"), (
        "消息区必须带 aria-live（或 role=status/alert）——加载/完成/失败可读（§9）"
    )

    # —— 文本输入：<label for> 程序关联（get_by_label 可定位；§9）——
    box = audit.by_id.get("chat-input")
    assert box and box["tag"] in ("textarea", "input"), (
        "面板必须含文本输入 #chat-input（textarea/input），供用户输入问题"
    )
    assert "chat-input" in audit.label_for, (
        "#chat-input 必须有程序关联 <label for=\"chat-input\">（§9；"
        "浏览器层 get_by_label 定位的前提）"
    )

    # —— 发送按钮：原生 <button>＋「发送」文案（§9 全键盘可操作）——
    send_text = audit.button_text.get("chat-send")
    assert send_text is not None and "发送" in send_text, (
        f"面板必须有原生发送按钮 #chat-send（文案含「发送」），实际 {send_text!r}"
    )

    # —— 375px：@media 之外无 >375px 固定宽度（§4 沿用既有口径）——
    css_text = _all_css(chat.api, html)
    violations = fixed_width_violations(css_text, limit_px=375)
    assert not violations, (
        f"@media 之外不得出现 >375px 固定 width/min-width（§4 禁止必需横向滚动）：{violations}"
    )

    # —— 金额一律字符串（无 parseFloat）；无财务 localStorage（§12.3/C5.5）——
    assert "parseFloat" not in html, (
        "页面不得使用 parseFloat（§12.3 金额一律以字符串读取与提交）"
    )
    assert "localStorage" not in html, (
        "页面不得使用 localStorage（C5.5 无财务 localStorage）"
    )


def test_ui_chat_unconfigured_state(chat_offline) -> None:
    """模型未配置 → 面板显示「AI 未配置」中文引导＋两个非密钥 env 名
    （HKTAX_MODEL_ENDPOINT/HKTAX_MODEL_NAME；密钥变量名不上面板——同
    tests/ai 密钥安全姿态）；本地计算不受影响（prepare→confirm→execute
    照常；无任何外发）。"""
    html = _page_html(chat_offline.api)

    # —— 未配置引导（§7 权威文案口径）——
    assert re.search(r"AI 尚未配置|AI 未配置", html), (
        "未配置态必须显示「AI 尚未配置／AI 未配置」引导（§7）"
    )
    for var in _MODEL_ENV_PUBLIC:
        assert var in html, (
            f"未配置引导必须列出非密钥环境变量名 {var}（C4.2：用户可据此自行配置）"
        )
    # 密钥变量名不得出现在页面（密钥仅服务端 env，名称指引放设置页/README；
    # 同 tests/ai/test_ai_outbound.py::test_keys_server_only_never_browser_log_export）
    assert "HKTAX_MODEL_API_KEY" not in html, (
        "首页不得出现密钥环境变量名 HKTAX_MODEL_API_KEY（C4.2 密钥不出现在"
        "浏览器页面；其名称仅 /settings 与 README 的配置说明）"
    )

    # —— 本地计算不受影响（§7：你仍可填写表格并在本机计算）——
    done = complete_calc(chat_offline.api)
    assert done["record_id"], "未配置态本地三段计算必须照常产出记录"

    # —— 无任何外发（未配置态不存在模型调用路径）——
    assert chat_offline.outbound.calls == [], "未配置态不得产生任何模型外发"


def test_ui_chat_send_opens_real_preview(chat) -> None:
    """输入问题点发送 → 先开授权预览（不发送）：对话框显示真实负载——
    字节数>0、SHA-256 64hex、接收方；与 POST /ai/consent/preview 返回逐字
    一致；fake 出站零调用（§7 不得先发送再补告知）。"""
    api = chat.api
    html = _page_html(api)

    # —— 接线：发送动作走预览端点，对话框字段取自真实响应（Red 驱动）——
    assert "/api/v1/ai/consent/preview" in html, (
        "发送按钮必须先经 POST /api/v1/ai/consent/preview 取得真实预览（§7/§12.5）"
    )
    for token in ("payload_sha256", "payload_bytes", "consent_id"):
        assert token in html, (
            f"脚本必须以预览响应字段填充对话框（接线证据：{token}，§12.5）"
        )
    audit = _audit_panel(html)
    for field in ("consent-endpoint", "consent-bytes", "consent-hash",
                  "consent-payload"):
        assert field in audit.by_id, (
            f"授权预览对话框必须含 {field} 字段（§7 接收方/字节数/SHA-256/逐字负载）"
        )

    # —— 真实负载一致性（面板同一调用）：字节数/hash 对应显示的 payload ——
    preview = _preview(api, _MSG_A)
    assert preview["payload_bytes"] > 0, "预览字节数必须 >0（真实冻结负载）"
    frozen = _mirror_frozen(preview["payload"])
    assert len(frozen) == preview["payload_bytes"], (
        "payload_bytes 必须对应预览显示 JSON 的确切字节数（C4.5 冻结字节）"
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", preview["payload_sha256"]), (
        "预览必须附负载 SHA-256（sha256:<64hex>，C4.5）"
    )
    assert preview["payload_sha256"] == "sha256:" + hashlib.sha256(frozen).hexdigest(), (
        "SHA-256 必须对显示的逐字负载计算（预览/授权/发送同一冻结字节）"
    )
    messages_text = json.dumps(preview["payload"]["messages"], ensure_ascii=False)
    assert _QUESTION_A in messages_text, "预览必须逐字含将发送的消息（§7）"
    assert preview["payload"].get("endpoint") == _TEST_ENDPOINT, (
        "预览负载必须携带真实接收方端点（§7 接收方可识别名称及域名）"
    )

    # —— 不自动发送：未授予即无外发；绕过授予直接 chat → 403（C4.8）——
    assert chat.outbound.calls == [], "仅预览不得外发（不得先发送再补告知）"
    refused = _chat(api, preview["consent_id"], _MSG_A)
    assert refused.status_code in (403, 409), (
        f"未授予的 consent 不得发送，实际 {refused.status_code}: {refused.text[:200]!r}"
    )
    assert refused.json()["error"]["code"] == "E_CONSENT_REQUIRED"
    assert chat.outbound.calls == [], "预览阶段 fake 出站必须零调用"


def test_ui_chat_deny_keeps_local_only(chat) -> None:
    """拒绝（不发送，继续本地使用）→ 无外发、输入保留、本地操作照常；
    面板须通告已选择不发送（§7 拒绝后表单与确定性计算照常工作）。"""
    api = chat.api
    html = _page_html(api)

    # —— 拒绝态通告文案（Red 驱动：拒绝后面板须告知本地照常）——
    assert "已选择不发送" in html, (
        "拒绝后面板必须通告「已选择不发送…」（§7：本地功能照常可用的明确反馈）"
    )
    assert "不发送，继续本地使用" in html, (
        "拒绝按钮必须保留 §7 权威文案「不发送，继续本地使用」"
    )

    # —— 拒绝＝不授予：无外发；未授予发送必被拒（面板无法绕过）——
    preview = _preview(api, _MSG_A)  # 用户看过预览后选择拒绝：不调用授予
    refused = _chat(api, preview["consent_id"], _MSG_A)
    assert refused.status_code in (403, 409) and (
        refused.json()["error"]["code"] == "E_CONSENT_REQUIRED"
    ), f"拒绝路径不得产生任何发送：{refused.text[:200]!r}"
    assert chat.outbound.calls == [], "拒绝后 fake 出站必须零调用"

    # —— 输入保留＝控件仍在；本地流程照常（§7 用户拒绝后一切本地可用）——
    audit = _audit_panel(html)
    assert "chat-input" in audit.by_id, "拒绝后输入控件必须仍在（输入保留）"
    done = complete_calc(api)
    assert done["record_id"], "拒绝后本地三段计算必须照常完成"


def test_ui_chat_allow_grants_and_sends_once(chat) -> None:
    """允许 → 单次授权 → /ai/chat 经 fake 出站恰发送一次；助手回复渲染进
    消息区并标「未确认」（自由文本非权威）；同授权重发 → 409，绝不补发。"""
    api = chat.api
    html = _page_html(api)

    # —— 接线：授予＋发送端点（Red 驱动；静态壳无任何 /ai/* 调用）——
    assert re.search(r"/api/v1/ai/consent(?![\w/])", html), (
        "「本次允许发送」必须经 POST /api/v1/ai/consent 授予（§12.5 单次授权）"
    )
    assert "/api/v1/ai/chat" in html, (
        "授予后必须经 POST /api/v1/ai/chat 发送（§12.5 发送前原子消费）"
    )
    assert "未确认" in html, (
        "助手自由文本回复必须标「未确认」（C3.6 unconfirmed；§6 自由文本"
        "建议不带权威结果样式）"
    )

    # —— 面板同一轮次：预览 → 授予 → 单次发送（经 fake 出站）——
    chat.outbound.script = [_wire_text_body(_ASSISTANT_TEXT)]
    preview = _preview(api, _MSG_A)
    _grant(api, preview["consent_id"])
    resp = _chat(api, preview["consent_id"], _MSG_A)
    assert resp.status_code == 200, (
        f"授权轮外发必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    payload = resp.json()
    assert payload.get("content") == _ASSISTANT_TEXT, "助手回复原样返回（渲染来源）"
    assert payload.get("unconfirmed") is True, "纯文本轮必须标记 unconfirmed（C3.6）"
    for key in AMOUNT_KEYS:
        assert key not in payload, f"纯文本轮不得携带权威金额字段 {key!r}"

    # —— 恰一次：冻结字节/密钥通道；同授权重发 → 409（单次消费）——
    assert len(chat.outbound.calls) == 1, (
        f"单次授权必须恰发送一次，实际 {len(chat.outbound.calls)} 次"
    )
    sent = chat.outbound.calls[0]
    assert sent["upstream_endpoint"] == _TEST_ENDPOINT, "外发必须到达配置端点（C4.2）"
    assert sent["api_key"] == _TEST_API_KEY, "密钥仅经服务端通道（C4.2 密钥不出服务端）"
    assert _QUESTION_A in sent["frozen"].decode("utf-8"), (
        "外发冻结字节必须逐字含预览消息（C4.6 不重序列化）"
    )
    again = _chat(api, preview["consent_id"], _MSG_A)
    assert again.status_code == 409 and (
        again.json()["error"]["code"] == "E_CONSENT_STALE"
    ), f"已消费授权不得重发（绝不自动补发）：{again.text[:200]!r}"
    assert len(chat.outbound.calls) == 1, "重试被拒后不得产生第二次外发"


def test_ui_chat_new_message_requires_new_preview(chat) -> None:
    """第二条消息 → 必须再次预览＋新授权：旧授权对新负载 409 E_CONSENT_STALE、
    无自动补发；新预览（不同 hash/consent_id）＋新授权后第二轮可用（§12.5）。"""
    api = chat.api
    html = _page_html(api)

    # —— 变化须重新授权的 UI 通告（Red 驱动；§12.8 chat 403/409 行）——
    assert "重新预览并授权" in html, (
        "chat 403/409（授权失效/负载变化）后面板必须通告「重新预览并授权」"
        "（§12.8；含 aria-live 通告）"
    )

    # —— 第一轮（完整授权后发送）——
    chat.outbound.script = [
        _wire_text_body("第一轮回复（假出站）"),
        _wire_text_body("第二轮回复（假出站）"),
    ]
    first = _preview(api, _MSG_A)
    _grant(api, first["consent_id"])
    r1 = _chat(api, first["consent_id"], _MSG_A)
    assert r1.status_code == 200, f"第一轮必须可用：{r1.text[:200]!r}"
    assert len(chat.outbound.calls) == 1

    # —— 第二条消息（上下文变化）：旧授权不复用、绝不自动补发 ——
    stale = _chat(api, first["consent_id"], _MSG_B)
    assert stale.status_code == 409, (
        f"上下文变化后旧授权必须失效（E_CONSENT_STALE），实际 {stale.text[:200]!r}"
    )
    assert stale.json()["error"]["code"] == "E_CONSENT_STALE"
    assert len(chat.outbound.calls) == 1, "授权失效不得触发自动补发（§12.5）"

    # —— 新预览＋新授权 → 第二轮可用 ——
    second = _preview(api, _MSG_B)
    assert second["consent_id"] != first["consent_id"], (
        "新负载必须产生新授权（不同 consent_id）"
    )
    assert second["payload_sha256"] != first["payload_sha256"], (
        "负载变化必须反映为不同 hash（C4.6 payload hash 绑定）"
    )
    _grant(api, second["consent_id"])
    r2 = _chat(api, second["consent_id"], _MSG_B)
    assert r2.status_code == 200, (
        f"重新授权后第二轮必须可用：{r2.status_code}: {r2.text[:200]!r}"
    )
    assert len(chat.outbound.calls) == 2, "第二轮恰新增一次外发"


def test_ui_chat_tool_result_rendered(chat) -> None:
    """模型回 tool_calls（propose_input 例子）→ 消息区渲染工具结果/建议并标
    「待你确认」；金额类文本仅来自工具结果（自由文本金额不作权威显示）；
    模型内容一律纯文本渲染（不 innerHTML，§12.5 不渲染模型 HTML）。"""
    api = chat.api
    html = _page_html(api)

    # —— 工具结果渲染接线＋确认标记（Red 驱动）——
    assert "tool_results" in html, (
        "脚本必须消费 chat 响应的 tool_results 并渲染进消息区（C3.6 工具层）"
    )
    assert "待你确认" in html, (
        "工具建议/结果必须标「待你确认」——对话不能取代输入确认（§6/REQ-11）"
    )
    assert "innerHTML" not in html, (
        "模型返回内容不得以 HTML 渲染（§12.5；一律 textContent）"
    )

    # —— 面板同一轮次：propose_input（只读建议）＋含金额的自由文本 ——
    free_text_with_amount = (
        "根據我的計算，你最終應繳稅款 1 元。建議按以下輸入重新確認。"
    )
    chat.outbound.script = [
        _wire_tool_calls_body(
            [{"id": "call_p1", "name": "propose_input",
              "arguments": {"input": salaries_input()}}],
            content=free_text_with_amount,
        )
    ]
    preview = _preview(api, _MSG_A)
    _grant(api, preview["consent_id"])
    resp = _chat(api, preview["consent_id"], _MSG_A)
    assert resp.status_code == 200, (
        f"工具轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    payload = resp.json()
    results = payload.get("tool_results")
    assert isinstance(results, list) and len(results) == 1, (
        f"propose_input 轮必须回带逐 call 的 tool_results：{payload!r}"
    )
    entry = results[0]
    assert entry["name"] == "propose_input" and entry["outcome"] == "executed", (
        f"propose_input 引用合法参数必须执行（C3.6 只读建议），实际 {entry!r}"
    )
    assert entry["result"]["proposed_input"] == salaries_input(), (
        "拟议输入必须原样回带（渲染「待你确认」建议的数据来源）"
    )
    assert payload.get("content") == free_text_with_amount, (
        "模型自由文本原样保留（不采信其中金额）"
    )
    assert payload.get("unconfirmed") is True, "工具轮文本同样非权威（unconfirmed）"
    for key in AMOUNT_KEYS:
        assert key not in payload, (
            f"金额字段只能来自工具结果（read_result/calculate_confirmed），"
            f"propose_input 轮不得出现 {key!r}"
        )
    assert len(chat.outbound.calls) == 1


def test_ui_chat_model_error_preserved(chat) -> None:
    """fake 出站超时 → 中文错误（E_MODEL_TIMEOUT 映射 §7 权威文案）；
    输入与本地结果保留（API 失败不清空表单、不阻止已有结果查看）；
    失败后不自动补发（重发须新预览＋新授权）。"""
    api = chat.api
    html = _page_html(api)

    # —— §7 超时权威文案（Red 驱动；静态壳无任何错误文案映射）——
    assert "模型暂时没有回应" in html, (
        "面板必须携带 §7 超时权威文案「模型暂时没有回应…」（E_MODEL_TIMEOUT "
        "的用户可见映射）"
    )
    assert "未受影响" in html, (
        "错误文案必须明确「你的资料和计算结果未受影响」（§7）"
    )

    # —— 本地结果先在（错误不得使其不可查）——
    done = complete_calc(api)
    record_id = done["record_id"]

    # —— 超时轮：授权后外发失败 → 502 E_MODEL_TIMEOUT（中文信息）——
    chat.outbound.script = [httpx.TimeoutException("upstream timed out")]
    preview = _preview(api, _MSG_A)
    _grant(api, preview["consent_id"])
    resp = _chat(api, preview["consent_id"], _MSG_A)
    assert resp.status_code == 502, (
        f"出站超时必须映射 502 E_MODEL_TIMEOUT（C3.7），实际 {resp.text[:200]!r}"
    )
    err = resp.json()["error"]
    assert err["code"] == "E_MODEL_TIMEOUT", f"错误码必须为 E_MODEL_TIMEOUT：{err!r}"
    assert "超时" in err["message_zh"], "错误信息必须为中文（§7 恢复提示）"
    assert len(chat.outbound.calls) == 1, "超时轮恰尝试一次外发"

    # —— 失败后不自动补发：同授权重发 → 409（须新预览＋新授权）——
    retry = _chat(api, preview["consent_id"], _MSG_A)
    assert retry.status_code == 409 and (
        retry.json()["error"]["code"] == "E_CONSENT_STALE"
    ), f"失败后不得沿用旧授权自动补发：{retry.text[:200]!r}"
    assert len(chat.outbound.calls) == 1, "错误后不得自动重试外发"

    # —— 本地结果保留（§7/§12.5：不阻止已有结果查看）——
    kept = api.get(f"/api/v1/records/{record_id}")
    assert kept.status_code == 200, "模型错误后本地记录必须仍可查阅"
