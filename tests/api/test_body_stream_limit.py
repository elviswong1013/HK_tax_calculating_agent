"""安全批次B（M4a）回归：无 Content-Length 的请求体按实际接收字节累计限流。

来源：2026-11-05 安全审查 F-06/ARCH-06：原实现只检查 Content-Length 头，
chunked（无 CL）请求可在拒绝前无限缓冲。契约:
  - 无 CL、实际接收 > 1MB → 413 E_INPUT_OVERSIZE（不依赖请求头声明）；
  - 无 CL、实际接收 ≤ 1MB → 不因体积被拒（后续按业务校验返回相应状态码）。
"""

from __future__ import annotations

BASE = "http://127.0.0.1:8000"


def _bootstrap(raw_client):
    boot = raw_client.get("/")
    token = boot.headers.get("X-CSRF-Token")
    assert token, "安全 GET 必须发放 X-CSRF-Token（conftest 既有契约）"
    return {"Origin": BASE, "X-CSRF-Token": token}


def test_chunked_oversize_body_rejected_413(raw_client) -> None:
    headers = _bootstrap(raw_client)

    def oversize():
        for _ in range(1025):  # 1025 KiB > 1 MiB，且不带 Content-Length
            yield b"x" * 1024

    r = raw_client.post("/api/v1/calc/confirm", content=oversize(), headers=headers)
    assert r.status_code == 413, (
        f"chunked 超限必须按实际接收字节 413，实际 {r.status_code}: {r.text[:200]!r}"
    )
    assert r.json()["error"]["code"] == "E_INPUT_OVERSIZE"


def test_chunked_small_body_not_rejected_for_size(raw_client) -> None:
    headers = _bootstrap(raw_client)
    r = raw_client.post(
        "/api/v1/calc/confirm", content=iter([b"{}"]), headers=headers
    )
    assert r.status_code != 413, (
        f"≤1MB 的 chunked 请求不得因体积被拒，实际 {r.status_code}: {r.text[:200]!r}"
    )
