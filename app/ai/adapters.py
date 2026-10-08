"""模型出站适配器（REQ-14；Annex C C4.1/C4.3/C4.4/C4.6 ⑦段）。

生产默认出站：HTTPX 直接 HTTP（无 SDK、不重新序列化）：
- content=frozen_bytes 原样发送（C4.6 ⑦段字节恒等）；
- Authorization Bearer＋Content-Type application/json；timeout=30.0（C4.4）；
  follow_redirects=False（C4.3 拒绝跨主机重定向）；
- 响应以流式读取累积上限 ≤1MB（C4.4 stream-read cumulative cap）：超限即
  中止连接（close）并归类 E_MODEL_BAD_RESPONSE，不无限缓冲。

路由外发一律经 app.state.model_outbound 接缝（create_app 默认装配本适配器；
测试注入进程内假件，见 tests/ai/conftest.py）。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from app.core.errors import AppError

# C4.4：响应流式读取累积上限（与 C2.9 请求体 1MB 上限同值）
MAX_STREAM_BYTES = 1_048_576

# C4.4：请求超时 30s
REQUEST_TIMEOUT_SECONDS = 30.0


class _StreamResponseAdapter:
    """httpx 流式响应的最小适配面（.status_code／.iter_bytes()／.close()）。

    单请求 client：close() 同时关闭响应流与底层 client（中止通道，C4.4），
    保证超限中止时不泄漏连接。
    """

    def __init__(self, client: httpx.Client, response: httpx.Response) -> None:
        self._client = client
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    def iter_bytes(self):  # noqa: ANN201 —— 委托 httpx.Response.iter_bytes
        return self._response.iter_bytes()

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._client.close()


def _default_post(
    url: str,
    *,
    content: bytes,
    headers: dict[str, str],
    timeout: float,
    follow_redirects: bool,
) -> Any:
    """默认 post 原语：httpx 流式 POST（响应保持可迭代，由适配器消费/中止）。"""
    client = httpx.Client(timeout=timeout, follow_redirects=follow_redirects)
    try:
        request = client.build_request("POST", url, content=content, headers=headers)
        response = client.send(request, stream=True)
    except BaseException:
        client.close()
        raise
    return _StreamResponseAdapter(client, response)


class HttpxModelOutbound:
    """出站接缝的生产实现（send 契约同 tests/ai 假件）：

    send(upstream_endpoint, api_key, frozen) -> {"status_code": int,
    "body": bytes}；传输级失败以异常表达（httpx.TimeoutException＝超时；
    其余异常＝通信失败），由路由层分类（C3.7）。
    """

    def __init__(self, post: Callable[..., Any] | None = None) -> None:
        # post 为依赖注入接缝（同 Scheduler(outbound=…) 模式；测试注入假原语）
        self._post = post if post is not None else _default_post

    def send(self, upstream_endpoint: str, api_key: str, frozen: bytes) -> dict:
        """原样发送冻结字节；流式读取响应体（累积 ≤1MB，超限中止＋分类）。"""
        response = self._post(
            upstream_endpoint,
            content=frozen,  # C4.6 ⑦段：同一字节串原样发送，不重序列化
            headers={
                "Authorization": f"Bearer {api_key}",  # 密钥仅经头传递（C4.2）
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,  # C4.3：拒绝跨主机重定向
        )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_STREAM_BYTES:
                response.close()  # C4.4：超限即中止连接，不无限缓冲
                raise AppError(
                    "E_MODEL_BAD_RESPONSE",
                    "模型响应超过流式读取上限（1MB）；已中止连接",
                )
            chunks.append(chunk)
        return {"status_code": response.status_code, "body": b"".join(chunks)}
