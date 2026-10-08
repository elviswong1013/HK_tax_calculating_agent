"""REQ-14 受控启动入口（M7 Red；Annex C C5.1；SDD §8 启动命令）。

命名测试（本文件）:
  - test_startup_single_worker_no_proxy_headers_loopback

REQ: REQ-14（启动强制 --workers 1 --no-proxy-headers；非回环绑定拒绝启动）。
规格锚点:
  - Annex C C5.1：仅绑定 127.0.0.1:<port>（默认 8000）。启动命令强制
    `--workers 1 --no-proxy-headers`，并由受控启动入口校验绑定地址 ∈
    {127.0.0.1, localhost}，非回环绑定拒绝启动（不依赖用户记忆）。
  - SDD §8 启动命令：uvicorn app.main:app --host 127.0.0.1 --port 8000
    --workers 1 --no-proxy-headers（启动器校验单 worker＋回环）。
期望值来源: 纯启动校验行为断言；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.main 提供受控启动入口 run(host="127.0.0.1", port=8000, *,
    workers=1, proxy_headers=False, runner=None)：
      * 非回环 host → app.web.security.LoopbackBindError（复用 M1 校验）；
      * workers != 1 → 拒绝启动（ValueError/SystemExit，信息含 "worker"）；
      * proxy_headers=True → 拒绝启动（ValueError/SystemExit，信息含 "proxy"）；
      * 校验通过后经 runner（默认 uvicorn.run）启动，传参保留
        host/port/workers=1/proxy_headers=False（runner 为依赖注入接缝，
        同 Scheduler(outbound=…) 模式；测试注入假 runner，不真正监听端口）。
"""

from __future__ import annotations

import pytest


def test_startup_single_worker_no_proxy_headers_loopback() -> None:
    """受控启动入口：非回环绑定拒绝；workers≠1／proxy_headers 拒绝；
    合法配置以 workers=1、proxy_headers=False 启动。"""
    from app.web.security import LoopbackBindError  # 既有 M1 校验（C5.1）

    import app.main as main_mod  # 延迟导入：Red＝缺失受控启动入口

    run = getattr(main_mod, "run", None)
    assert callable(run), (
        "app.main 必须提供受控启动入口 run()（SDD §8 启动命令强制 "
        "--workers 1 --no-proxy-headers；启动器校验单 worker＋回环绑定）"
    )

    # —— 非回环绑定 → 拒绝启动（C5.1 精确集合 {127.0.0.1, localhost}）——
    for bad_host in ("0.0.0.0", "192.168.1.5", "::", "::1", "example.com"):
        with pytest.raises(LoopbackBindError):
            run(host=bad_host, port=8000)

    # —— 多 worker → 拒绝（强制单 worker；进程内会话/同意状态依赖）——
    with pytest.raises((ValueError, SystemExit)) as excinfo_workers:
        run(host="127.0.0.1", port=8000, workers=2)
    assert "worker" in str(excinfo_workers.value).lower(), (
        f"多 worker 拒绝信息须说明原因，实际 {excinfo_workers.value!r}"
    )

    # —— 信任代理头 → 拒绝（不信任 X-Forwarded-*，C5.1/C5.2）——
    with pytest.raises((ValueError, SystemExit)) as excinfo_proxy:
        run(host="127.0.0.1", port=8000, proxy_headers=True)
    assert "proxy" in str(excinfo_proxy.value).lower(), (
        f"代理头拒绝信息须说明原因，实际 {excinfo_proxy.value!r}"
    )

    # —— 合法配置：经注入 runner 启动，保留单 worker＋no-proxy-headers ——
    captured: list[dict] = []

    def fake_runner(**kwargs):
        captured.append(kwargs)

    run(host="127.0.0.1", port=8000, runner=fake_runner)
    run(host="localhost", port=8000, runner=fake_runner)
    assert len(captured) == 2, "回环合法配置必须放行至 runner（localhost 同样合法）"
    for kwargs in captured:
        assert kwargs.get("workers") == 1, f"启动必须单 worker，实际 {kwargs!r}"
        assert kwargs.get("proxy_headers") is False, (
            f"启动必须 no-proxy-headers，实际 {kwargs!r}"
        )
        assert kwargs.get("host") in ("127.0.0.1", "localhost")
        assert kwargs.get("port") == 8000
