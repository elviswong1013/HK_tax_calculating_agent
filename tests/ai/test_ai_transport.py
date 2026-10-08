"""REQ-14 出站传输单元层（M7 Red；Annex C C4.4/C4.6 ⑦段）。

命名测试（本文件）:
  - test_stream_read_1mb_accumulation_cap
  - test_httpx_sends_frozen_bytes_no_reserialization

REQ: REQ-14（响应流式读取累积 ≤1MB；发送经 HTTPX content=frozen_bytes，
无 SDK、不重序列化）。
规格锚点:
  - Annex C C4.4：请求超时 30s；响应以流式读取累积上限 ≤1MB（超限即中止
    连接并归类 E_MODEL_BAD_RESPONSE，不无限缓冲）。
  - Annex C C4.6 ⑦段：HTTPX content=frozen_bytes 原样发送（无 SDK、不得
    重新序列化）；C4.3 拒绝跨主机重定向（follow_redirects=False）。
期望值来源: 纯传输行为断言（字节恒等、读取上限）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app/ai/adapters.py 提供 HttpxModelOutbound（生产默认出站；构造可注入
    post 原语：post(url, *, content, headers, timeout, follow_redirects)
    -> response，response 满足 .status_code + .iter_bytes()（httpx.Response
    兼容；默认 post=httpx.post 等价的流式请求））。
  - send(upstream_endpoint, api_key, frozen) -> {"status_code": int,
    "body": bytes}：
      * content 恒等 frozen（同一字节串原样发送，不重序列化）；
      * headers: Authorization Bearer＋Content-Type application/json；
        timeout=30.0；follow_redirects=False；
      * 经 iter_bytes() 累积读取响应体，累计 >1,048,576 字节 → 中止连接
        （response.close()）并抛 AppError("E_MODEL_BAD_RESPONSE")。
"""

from __future__ import annotations

import pytest

from _ai_fakes import FakeStreamingResponse, wire_text_body

ENDPOINT = "https://127.0.0.1:9/v1/chat/completions"
ONE_MB = 1_048_576  # C4.4：流式累积上限


def test_stream_read_1mb_accumulation_cap() -> None:
    """响应流式读取累积 ≤1MB：超限中止连接＋E_MODEL_BAD_RESPONSE；
    正常小响应读取不受影响。"""
    from app.ai.adapters import HttpxModelOutbound  # 延迟导入：Red＝缺失模块
    from app.core.errors import AppError

    # —— 超限：4MB 响应体（8KB 分块）→ 读至上限即中止 ——
    chunk = b"x" * 8192
    total_chunks = (4 * ONE_MB) // len(chunk)
    served = {"n": 0}

    def big_stream():
        while served["n"] < total_chunks:
            served["n"] += 1
            yield chunk

    fat = FakeStreamingResponse(200, big_stream())
    outbound = HttpxModelOutbound(post=lambda url, **kw: fat)
    with pytest.raises(AppError) as excinfo:
        outbound.send(ENDPOINT, "sk-unit", b'{"probe":true}')
    assert excinfo.value.code == "E_MODEL_BAD_RESPONSE", (
        f"超过 1MB 流式累积上限必须归类 E_MODEL_BAD_RESPONSE（C4.4），"
        f"实际 {excinfo.value.code!r}"
    )
    assert fat.consumed <= ONE_MB + len(chunk), (
        f"读取必须在上限＋单块内中止（不无限缓冲），实际累计 {fat.consumed} 字节"
    )
    assert fat.closed, "超限必须中止连接（关闭响应流）"

    # —— 正常：小响应完整读取 ——
    ok_body = wire_text_body("ok")["body"]
    ok = FakeStreamingResponse(200, [ok_body])
    outbound_ok = HttpxModelOutbound(post=lambda url, **kw: ok)
    result = outbound_ok.send(ENDPOINT, "sk-unit", b'{"probe":true}')
    assert result["status_code"] == 200
    assert result["body"] == ok_body


def test_httpx_sends_frozen_bytes_no_reserialization(ai) -> None:
    """HTTPX content=frozen_bytes 原样发送（字节恒等；无 SDK 重序列化）；
    路由层实发字节与预览 SHA-256/负载逐位一致。"""
    import hashlib
    import json as jsonlib

    from app.ai.adapters import HttpxModelOutbound  # 延迟导入：Red＝缺失模块

    # —— 传输单元：注入假 post，断言原样字节与请求参数 ——
    frozen = (
        b'{"endpoint":"https://127.0.0.1:9/v1/chat/completions",'
        b'"messages":[{"role":"user","content":"hi"}]}'
    )
    captured: dict = {}
    ok_body = wire_text_body("ok")["body"]

    def fake_post(url, *, content=None, headers=None, timeout=None,
                  follow_redirects=None, **kwargs):
        captured.update(
            url=url,
            content=content,
            headers=dict(headers or {}),
            timeout=timeout,
            follow_redirects=follow_redirects,
            kwargs=kwargs,
        )
        return FakeStreamingResponse(200, [ok_body])

    outbound = HttpxModelOutbound(post=fake_post)
    result = outbound.send(ENDPOINT, "sk-unit-key", frozen)

    assert captured["url"] == ENDPOINT
    assert isinstance(captured["content"], bytes) and captured["content"] == frozen, (
        "HTTPX 必须 content=frozen_bytes 原样发送（字节恒等，不重序列化）"
    )
    headers = captured["headers"]
    assert headers.get("Authorization") == "Bearer sk-unit-key", (
        "密钥仅经 Authorization 头传递（服务端持有；不进负载）"
    )
    assert headers.get("Content-Type") == "application/json"
    assert captured["follow_redirects"] is False, "必须拒绝跨主机重定向（C4.3）"
    assert captured["timeout"] == 30.0, "请求超时必须 30s（C4.4）"
    assert result == {"status_code": 200, "body": ok_body}

    # —— 路由层：预览冻结字节 → 假出站捕获字节逐位一致 ——
    from _ai_fakes import ai_grant, ai_chat, ai_preview

    api = ai.api
    fake = ai.outbound
    messages = [{"role": "user", "content": "hi"}]
    preview = ai_preview(api, messages)
    ai_grant(api, preview["consent_id"])
    resp = ai_chat(api, preview["consent_id"], messages)
    assert resp.status_code == 200, (
        f"路由外发必须经 app.state.model_outbound（C4.6 ⑦段），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    sent = fake.calls[0]["frozen"]
    assert isinstance(sent, bytes)
    digest = "sha256:" + hashlib.sha256(sent).hexdigest()
    assert digest == preview["payload_sha256"], (
        "实发字节必须与预览冻结字节逐位一致（SHA-256 恒等；不得重新序列化）"
    )
    assert jsonlib.loads(sent.decode("utf-8")) == preview["payload"], (
        "实发字节解析后必须与预览负载完全一致（内容恒等）"
    )
