"""allowlist 出站封装（SDD §5 管线阶段 1；Annex C C7.2）。

REQ-17：更新出站仅经注入的 outbound 以精确 URL 抓取；调度器只传 url、不携带
任何附加负载——不发送任何用户财务数据、不调用模型端点。快照上限 10MB
（SDD §5 更新管线行），超限→异常（来源失败）。TLS 验证、拒绝重定向、超时
30s 由真实 outbound 实现负责（测试经进程内假出站注入，Annex C C1）。
"""

from __future__ import annotations

MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024  # SDD §5：快照 ≤10MB


def fetch_document(outbound, url: str) -> bytes:
    """经注入出站抓取单份官方文档全文；非字节产物/超限 → 异常（来源失败）。"""
    body = outbound.fetch(url)
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError(f"出站产物必须为字节（实际 {type(body).__name__}）：{url}")
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise ValueError(f"快照超过 10MB 上限（SDD §5，fail closed）：{url}")
    return bytes(body)
