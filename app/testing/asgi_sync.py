"""pytest 插件：同步 httpx.Client × httpx.ASGITransport 进程内桥（Annex C C1 测试接线）。

背景：C1 规定 API/单元测试「经 httpx.ASGITransport 进程内执行」；tests/conftest.py
以同步 ``httpx.Client(transport=httpx.ASGITransport(app=app))`` 落地该契约。但
httpx ≥0.27 的 ``ASGITransport`` 为 async-only（仅 ``handle_async_request``），
同步 Client 调用的 ``handle_request`` 不存在。

本插件按 C1「假件为进程内实现、不新增第三方库」补齐同步桥：
为 ``httpx.ASGITransport`` 追加 ``handle_request``——经 anyio blocking portal
运行既有 ``handle_async_request``，缓冲响应体并把流替换为同步兼容的
``httpx.ByteStream``。不改动任何测试断言/夹具；语义与 async 路径一致。

（若测试基座日后改为 httpx.AsyncClient，本插件即无作用，可随 pyproject
addopts 一并移除——已在任务报告中向编排者标注。）
"""

from __future__ import annotations

from typing import Any

import anyio
from httpx import ASGITransport, ByteStream


async def _run_and_buffer(transport: ASGITransport, request: Any) -> Any:
    """在事件循环内执行既有 async 处理并完整缓冲响应体。"""
    response = await transport.handle_async_request(request)
    await response.aread()
    response.stream = ByteStream(response.content)  # 同步/异步双协议流
    return response


def _handle_request_sync(self: ASGITransport, request: Any) -> Any:
    """同步入口：每请求独立 blocking portal（顺序语义，与单测用法一致）。"""
    with anyio.from_thread.start_blocking_portal(backend="asyncio") as portal:
        return portal.call(_run_and_buffer, self, request)


def ensure_sync_bridge() -> None:
    """幂等补齐 ASGITransport 的同步面（已存在则不动）。

    - handle_request：同步 Client 的请求入口 → portal 运行 handle_async_request。
    - close：同步 Client.close() 的释放入口（每次请求独立 portal，无需释放资源）。
    """
    if not hasattr(ASGITransport, "handle_request"):
        ASGITransport.handle_request = _handle_request_sync  # type: ignore[attr-defined]
    if not hasattr(ASGITransport, "close"):
        ASGITransport.close = lambda self: None  # type: ignore[attr-defined]


def pytest_configure(config: Any) -> None:
    ensure_sync_bridge()
