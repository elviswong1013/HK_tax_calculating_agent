"""REQ-14 UI 授权绑定：负载/端点/重试任一变化 → 新预览＋新授权。

REQ: REQ-14（Annex A §12.5 AI 外发授权绑定；§12.9 命名测试
test_ui_consent_new_payload_new_grant）。
规格锚点:
  - Annex A §7：第一次可能外发时先显示确认页；确认页逐字展示完整消息上下文
    及工具结果负载；「本次允许发送」单次使用。
  - Annex A §12.5：预览＝将发送的确切完整 JSON＋字节数与 SHA-256；上下文、
    工具结果、端点、模型变化或任何重试 → 新预览＋新授权；绝不自动补发。
  - Annex C C4.5/C4.6/C4.8：consent 绑定 (…, endpoint, model, input_revision,
    payload hash)；重发/重试/任何变化 → stale → 必须新 preview＋新授权。
期望值来源: 结构/状态断言（hash、id、错误码）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - POST /api/v1/ai/consent/preview {messages} → 200/201：
    {consent_id, payload(逐字), payload_sha256(sha256:…)}；不同负载 →
    不同 consent_id 与不同 hash。
  - POST /api/v1/ai/consent {consent_id} → granted。
  - POST /api/v1/ai/chat 以旧 consent 发送与冻结字节不符的新负载 →
    403/409 E_CONSENT_*（不得沿用旧授权执行）。
"""

from __future__ import annotations

import json
import re

_MSG_A = [{"role": "user", "content": "帮我核对薪俸税输入资料"}]
_MSG_B = [{"role": "user", "content": "帮我核对薪俸税输入资料（已补充支出）"}]
_MSG_CHANGED = [{"role": "user", "content": "再帮我比较个人入息课税"}]


def _sha256_in(text: str) -> bool:
    return re.search(r"sha256:[0-9a-f]{64}", text) is not None


def test_ui_consent_new_payload_new_grant(ui) -> None:
    """负载变化 → 新预览（不同 hash/consent_id）；旧授权不得用于新负载或重试
    （403/409 E_CONSENT_*；绝不自动补发）。"""
    api = ui.api

    # —— 预览 A：逐字负载＋SHA-256 ——
    preview_a = api.post("/api/v1/ai/consent/preview", json={"messages": _MSG_A})
    assert preview_a.status_code in (200, 201), (
        f"预览入口必须可用（§12.5），实际 {preview_a.status_code}: {preview_a.text[:300]!r}"
    )
    pa = preview_a.json()
    a_text = json.dumps(pa, ensure_ascii=False)
    assert pa.get("consent_id"), "预览响应必须含 consent_id（C4.8 prepared）"
    assert _MSG_A[0]["content"] in a_text, "预览必须逐字含将发送的消息（§7）"
    assert _sha256_in(a_text), "预览必须附负载 SHA-256（C4.5）"

    # —— 预览 B：负载变化 → 新预览（不同 hash＋不同 consent_id）——
    preview_b = api.post("/api/v1/ai/consent/preview", json={"messages": _MSG_B})
    assert preview_b.status_code in (200, 201), preview_b.text
    pb = preview_b.json()
    b_text = json.dumps(pb, ensure_ascii=False)
    assert pb.get("consent_id") != pa["consent_id"], (
        "负载变化必须产生新的授权（新 consent_id；旧授权不可沿用）"
    )
    hash_a = re.search(r"sha256:[0-9a-f]{64}", a_text)
    hash_b = re.search(r"sha256:[0-9a-f]{64}", b_text)
    assert hash_a and hash_b and hash_a.group(0) != hash_b.group(0), (
        "负载变化必须反映为不同的负载 hash（C4.6 payload hash 绑定）"
    )

    # —— 授予 A（单次使用授权）——
    grant_a = api.post("/api/v1/ai/consent", json={"consent_id": pa["consent_id"]})
    assert grant_a.status_code in (200, 201), (
        f"授予必须可用（C4.8 granted），实际 {grant_a.status_code}: {grant_a.text[:300]!r}"
    )

    # —— 以 A 的授权发送与冻结字节不符的新负载 → 拒绝（须新预览＋新授权）——
    chat_changed = api.post(
        "/api/v1/ai/chat",
        json={"consent_id": pa["consent_id"], "messages": _MSG_CHANGED},
    )
    assert chat_changed.status_code in (403, 409), (
        f"上下文/负载变化后旧授权必须失效（E_CONSENT_*），"
        f"实际 {chat_changed.status_code}: {chat_changed.text[:300]!r}"
    )
    err = chat_changed.json().get("error", {})
    assert err.get("code") in ("E_CONSENT_REQUIRED", "E_CONSENT_STALE"), (
        f"变化负载必须要求新授权：{chat_changed.json()!r}"
    )

    # —— 重试同一授权（未获新授权）→ 不得静默沿用/自动补发 ——
    chat_retry = api.post(
        "/api/v1/ai/chat",
        json={"consent_id": pa["consent_id"], "messages": _MSG_A},
    )
    assert chat_retry.status_code in (403, 409), (
        f"重试同样需要新预览＋新授权（C4.6 绝不自动补发），"
        f"实际 {chat_retry.status_code}: {chat_retry.text[:300]!r}"
    )
    err_retry = chat_retry.json().get("error", {})
    assert err_retry.get("code") in ("E_CONSENT_REQUIRED", "E_CONSENT_STALE")
