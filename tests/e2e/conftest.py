"""P2 W0 浏览器 e2e 共享隔离基建（006 §6 ①–⑤；本目录唯一启动点）。

REQ: 非独立 REQ；服务 tests/e2e 三个浏览器模块（REQ-11/REQ-12/REQ-14）。
规格锚点:
  - 006 §6.1 隔离顺序：① 唯一临时 HKTAX_DB_PATH＋假网络接缝（根 conftest
    会话引导已在导入 app.main 前完成）；② 只导入 app.main；③ 替换
    `app.main._start_scheduler_threads` 为可计数、可 stop 的假生命周期；
    ④ 显式构造并启动被注入的 app 对象；⑤ 禁止 import-string 另起实例，
    不得复用模块级 app。
  - 006 §6.2/§6.3：显式注入假 model 出站（调用即计数并失败）；根 guard 的
    拒绝 transport/调度器保护保持生效；断言 seam 命中、真实出站计数 0、
    唯一临时 DB、浏览器仅允许本 loopback origin。
  - 006 §6.4：Chromium 缺失＝BLOCKED（skip 不计通过，不自动安装）。

每个测试由 `e2e_isolation` 提供唯一 DB＋显式 app＋仅 127.0.0.1 的 uvicorn；
`page` 只允许该 loopback origin，并显式封禁 Service Worker 与 WebSocket
出站通道；全部拒绝记录进 `blocked_requests`。teardown 顺序：停服务→核验假
生命周期 stop→guard 收口→恢复 `_start_scheduler_threads`→恢复 DB env；
隔离记录以 JSONL 写 `HKTAX_E2E_ISOLATION_LOG`（W0 准入证据）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import pytest

DB_ENV = "HKTAX_DB_PATH"
ISOLATION_LOG_ENV = "HKTAX_E2E_ISOLATION_LOG"
LOOPBACK_HOST = "127.0.0.1"
SERVER_START_TIMEOUT_SECONDS = 15.0
SERVER_STOP_TIMEOUT_SECONDS = 10.0

# 进程内唯一性核对（每测试独立 DB；W0 准入证据，不是跨会话断言）
_SEEN_DB_PATHS: set[str] = set()


class _StopToken:
    """lifespan 返回的可 stop 对象：记录 stop 调用次数，不创建线程。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.stop_calls = 0

    def set(self) -> None:
        self.stop_calls += 1
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


class _CountableStartSeam:
    """`_start_scheduler_threads` 的可计数假生命周期。

    只记录调用并返回 stop 对象；不创建线程、不调用 startup/tick、不出站。
    """

    def __init__(self) -> None:
        self.start_count = 0
        self.entries: list[dict[str, Any]] = []

    def __call__(self, scheduler: Any) -> _StopToken:
        self.start_count += 1
        token = _StopToken()
        self.entries.append({"scheduler": scheduler, "token": token})
        return token

    @property
    def schedulers(self) -> list[Any]:
        return [entry["scheduler"] for entry in self.entries]

    @property
    def tokens(self) -> list[_StopToken]:
        return [entry["token"] for entry in self.entries]

    def stop_all(self) -> None:
        for token in self.tokens:
            if not token.is_set():
                token.set()


class _FakeModelOutbound:
    """显式注入的假模型出站：计数即失败，不触达生产适配器/网络。

    `_post` 保留根 guard 的拒绝替身身份，使 guard 的 app 登记核验仍成立。
    """

    def __init__(self, guarded_post: Any) -> None:
        self._post = guarded_post
        self.send_calls = 0

    def send(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.send_calls += 1
        raise AssertionError(
            "e2e 假模型出站被调用：本测试不得发出模型请求"
            "（006 §6.2：调用即记录并失败）"
        )


class E2EIsolation:
    """一个测试的隔离把手（fixture 产出；测试只读，勿改内部状态）。"""

    def __init__(
        self,
        *,
        app: Any,
        base_url: str,
        db_path: Path,
        guard: Any,
        seam: _CountableStartSeam,
        model: _FakeModelOutbound,
        record: dict[str, Any],
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.db_path = db_path
        self.guard = guard
        self.seam = seam
        self.model = model
        self.record = record


def _stop_injected_loopback(server: Any, thread: Any) -> list[str]:
    """请求服务退出并等待线程结束；接受启动失败时的部分状态（任一可为 None）。"""
    problems: list[str] = []
    if server is not None:
        server.should_exit = True
    if thread is not None:
        # 未 start() 的 Thread.join() 会抛 RuntimeError；ident 为空即未启动。
        if getattr(thread, "ident", None) is not None:
            try:
                thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)
            except BaseException as exc:  # noqa: BLE001 - 清理异常须报出
                problems.append(f"e2e 服务器线程 join 异常：{exc!r}")
        if thread.is_alive():
            problems.append("e2e 服务器线程未在 10s 内退出")
    return problems


def _start_injected_loopback(
    app: Any,
    *,
    server_factory: Callable[[Any], Any] | None = None,
    thread_factory: Callable[..., Any] | None = None,
) -> tuple[Any, Any, str]:
    """以显式 app 对象启动单 worker 回环服务（端口由内核分配，无绑定竞态）。

    R1：线程启动后若等待 `server.started` 超时/失败，本 helper 在**移交句柄
    前**自行 `should_exit`＋join（带超时）并原样抛出；调用方 finally 只处理
    已移交或部分状态，避免超时路径遗留存活线程。

    `server_factory`/`thread_factory` 仅供确定性假件测试注入；生产调用不传。
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=LOOPBACK_HOST,
        port=0,
        log_level="warning",
        workers=1,
        lifespan="on",
    )
    if server_factory is None:
        server = uvicorn.Server(config)
    else:
        server = server_factory(config)
    thread_kwargs = {
        "target": server.run,
        "name": "p2-w0-e2e-uvicorn",
        "daemon": True,
    }
    if thread_factory is None:
        thread = threading.Thread(**thread_kwargs)
    else:
        thread = thread_factory(**thread_kwargs)
    try:
        thread.start()
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline and not server.started:
            if not thread.is_alive():
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError(
                f"e2e 服务器未能在 {SERVER_START_TIMEOUT_SECONDS:g}s 内启动"
                "（127.0.0.1 受控回环；lifespan 假 seam 未生效？）"
            )
        sockets = server.servers[0].sockets
        port = int(sockets[0].getsockname()[1])
        base_url = f"http://{LOOPBACK_HOST}:{port}"
    except BaseException as exc:
        cleanup_problems = _stop_injected_loopback(server, thread)
        if cleanup_problems:
            exc.add_note(
                "启动失败清理未完成：" + "；".join(cleanup_problems)
            )
        raise
    return server, thread, base_url


def _write_isolation_record(record: Mapping[str, Any]) -> None:
    target = os.environ.get(ISOLATION_LOG_ENV)
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        )


@pytest.fixture
def e2e_env_overrides() -> Mapping[str, str | None]:
    """在 app 构造前应用的 env 覆盖（None＝删除；模块可覆盖）。

    006 §6.1 ①：唯一 DB 与假网络 env 必须先于显式 app 构造；模块级未配置态
    （如聊天面板的三模型 env）经此声明，teardown 逐键恢复。
    """
    return {}


@pytest.fixture
def e2e_isolation(request, tmp_path, safety_guard, e2e_env_overrides):
    """006 §6 隔离顺序：唯一 DB → 假 seam → 显式 app → loopback 启动。"""
    import app.main as main_module
    from app.core.hashing import bundle_content_hash
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    guard = safety_guard
    db_path = (Path(tmp_path) / "rules.db").resolve()
    db_key = str(db_path)
    db_unique = db_key not in _SEEN_DB_PATHS
    _SEEN_DB_PATHS.add(db_key)

    env_before = os.environ.get(DB_ENV)
    env_overrides_saved = {
        key: os.environ.get(key) for key in e2e_env_overrides
    }
    seam = _CountableStartSeam()
    counters_before = guard.counter_snapshot()
    stores_before = set(guard.observed_store_paths())

    app: Any = None
    model: _FakeModelOutbound | None = None
    server: Any = None
    thread: threading.Thread | None = None
    token: _StopToken | None = None
    base_url: str | None = None
    problems: list[str] = []
    record: dict[str, Any] = {
        "nodeid": request.node.nodeid,
        "phase": "p2-w0",
        "db": {"path": db_key, "unique_in_session": db_unique},
        "seam": {},
        "app": {},
        "model": {},
        "guard": {},
        "browser": {"status": "not_started"},
        "restore": {},
        "problems": [],
    }

    try:
        # ① 每测试唯一临时 DB（根 guard 已先于 app.main 导入安装假网络接缝）
        os.environ[DB_ENV] = str(db_path)
        for key, value in e2e_env_overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        record["env_overrides"] = {
            key: ("<unset>" if value is None else value)
            for key, value in e2e_env_overrides.items()
        }
        # ③ 替换启动 seam 后，才构造/启动被注入 app（006 §6 顺序）
        guard.install_scoped_start_seam(seam)
        # ④ 显式构造（禁止复用模块级 app 或 import-string）
        app = main_module.create_app()
        if app is main_module.app:
            problems.append("e2e 复用了模块级 app（应为显式构造的新对象）")
        actual_db = Path(app.state.store._path).resolve()
        if actual_db != db_path:
            problems.append(f"e2e DB 路径不符：{actual_db} != {db_path}")
        module_db = Path(main_module.app.state.store._path).resolve()
        if actual_db == module_db:
            problems.append("e2e DB 与模块级 app DB 相同（未实现每测试隔离）")
        current = app.state.store.current()
        expected_hash = bundle_content_hash(INITIAL_BUNDLE_CONTENT)
        record["db"].update(
            {
                "actual_path": str(actual_db),
                "module_app_db": str(module_db),
                "distinct_from_module_app_db": actual_db != module_db,
                "rules_schema_version": current["rules_schema_version"],
                "bundle_hash": current["bundle_hash"],
                "expected_bundle_hash": expected_hash,
            }
        )
        if current["rules_schema_version"] != "2.0.0":
            problems.append("固定测试束非 v2（rules_schema_version≠2.0.0）")
        if current["bundle_hash"] != expected_hash:
            problems.append("固定测试束 hash 与 INITIAL_BUNDLE_CONTENT 不符")

        # 显式假 model 接缝（调用即计数失败）；_post 保持 guard 拒绝替身身份
        created_outbound = app.state.model_outbound
        model = _FakeModelOutbound(created_outbound._post)
        app.state.model_outbound = model

        # 根 guard 登记/核验：store、scheduler、lifespan 闭包、model _post
        guard.register_app("e2e", app)
        scheduler = app.state.update_scheduler
        record["app"] = {
            "explicit_object": app is not main_module.app,
            "app_id": id(app),
            "scheduler_id": id(scheduler),
            "closure_scheduler_ok": True,
        }

        # ④ 启动被注入的那个 app 对象；lifespan 命中假 seam（不建真实线程）
        server, thread, base_url = _start_injected_loopback(app)
        token = seam.tokens[0] if seam.tokens else None
        if seam.start_count != 1:
            problems.append(f"启动 seam 命中次数异常：{seam.start_count}（应=1）")
        if seam.schedulers != [scheduler]:
            problems.append("启动 seam 收到的 scheduler 非 app.state.update_scheduler")
        if token is None or token.is_set():
            problems.append("lifespan 未返回未 stop 的可停对象")
        record["seam"] = {
            "start_count": seam.start_count,
            "scheduler_identity_ok": seam.schedulers == [scheduler],
            "stop_object_present": token is not None,
            # 0 = 假 seam 实现不构造线程（fixture 事实），不是进程线程普查；
            # 真实 uvicorn 线程名单独记录，避免把常量误读为线程监测。
            "threads_created": 0,
            "threads_created_semantics": (
                "fake_seam_constructs_no_threads; not a runtime thread census"
            ),
            "server_thread_name": thread.name,
        }
        record["app"]["base_url"] = base_url
        record["guard"]["availability"] = {
            year: scheduler.availability(year)
            for year in ("2024_25", "2025_26", "2026_27")
        }
        record["guard"]["patched_boundaries"] = sorted(guard._installed)
        record["model"] = {"send_calls": 0, "post_guarded": True}

        yield E2EIsolation(
            app=app,
            base_url=base_url,
            db_path=db_path,
            guard=guard,
            seam=seam,
            model=model,
            record=record,
        )
    finally:
        # 停服务（正常 shutdown 会 set 假 seam 的 stop 对象）；启动 helper
        # 已负责失败路径清理，此处仍接受 server/thread 任一为 None 的部分状态。
        if server is not None or thread is not None:
            problems.extend(_stop_injected_loopback(server, thread))
        if token is not None:
            if not token.is_set():
                problems.append("假生命周期 stop 对象未被服务 shutdown set（显式 stop）")
                token.set()
            record["seam"]["stop_calls"] = token.stop_calls
            record["seam"]["stopped"] = token.is_set()
        seam.stop_all()

        counters_after = guard.counter_snapshot()
        deltas = {
            key: value
            for key, value in counters_after.items()
            if value != counters_before.get(key, 0)
        }
        new_stores = sorted(set(guard.observed_store_paths()) - stores_before)
        record["guard"]["counter_deltas"] = deltas
        record["guard"]["new_store_paths"] = new_stores
        if deltas:
            problems.append(f"真实边界调用计数非零（本测试）：{deltas!r}")
        if model is not None:
            record["model"]["send_calls"] = model.send_calls
            if model.send_calls:
                problems.append(f"假模型出站被调用 {model.send_calls} 次（应为 0）")

        if app is not None:
            try:
                problems.extend(
                    guard.finish_explicit_app(
                        app, counters_before, stores_before, None
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - 收口异常不得跳过恢复
                problems.append(f"guard 收口异常：{exc!r}")
            try:
                guard.unregister_scheduler(
                    getattr(app.state, "update_scheduler", None)
                )
            except BaseException as exc:  # noqa: BLE001
                problems.append(f"scheduler 注销异常：{exc!r}")

        try:
            problems.extend(guard.restore_scoped_start_seam(seam))
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"启动 seam 恢复异常：{exc!r}")
        record["restore"]["seam_restored"] = guard.scoped_start_seam is None

        try:
            if env_before is None:
                os.environ.pop(DB_ENV, None)
            else:
                os.environ[DB_ENV] = env_before
            record["restore"]["db_env_restored"] = (
                os.environ.get(DB_ENV) == env_before
            )
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"DB env 恢复异常：{exc!r}")
            record["restore"]["db_env_restored"] = False

        try:
            for key, value in env_overrides_saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            record["restore"]["overrides_restored"] = all(
                os.environ.get(key) == value
                for key, value in env_overrides_saved.items()
            )
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"env 覆盖恢复异常：{exc!r}")
            record["restore"]["overrides_restored"] = False

        record["problems"] = list(problems)
        record["status"] = "ok" if not problems else "failed"
        try:
            _write_isolation_record(record)
        except BaseException as exc:  # noqa: BLE001 - 证据失败须显式暴露
            problems.append(f"隔离记录写出失败：{exc!r}")
            record["problems"] = list(problems)
            record["status"] = "failed"
        if problems:
            raise AssertionError(
                "P2 W0 e2e 隔离收口失败：" + "；".join(problems)
            )


@pytest.fixture
def base_url(e2e_isolation) -> str:
    """本测试唯一 loopback origin（服务与 app 均为显式注入对象）。"""
    return e2e_isolation.base_url


@pytest.fixture
def e2e_expect_timeout() -> int:
    """Playwright 动作/expect 默认超时（模块可按流程长度覆盖）。"""
    return 3000


@pytest.fixture
def page(e2e_isolation, e2e_expect_timeout):
    """Chromium 页面：仅允许本 loopback origin；SW/WS 出站显式封禁。

    R2：`service_workers="block"`；HTTP 走 loopback-only 路由，WebSocket 走
    `route_web_socket`（不连接服务端＝拒绝）并记录；SW 注册尝试经 init script
    记录。teardown 断言无存活 Service Worker／未记录通道。
    """
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=e2e_expect_timeout)
    blocked: list[str] = []
    ws_attempts: list[str] = []
    sw_attempts: list[str] = []
    service_worker_events: list[str] = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 —— 缺浏览器二进制＝环境未就绪
            e2e_isolation.record["browser"] = {
                "status": "blocked",
                "error": str(exc),
            }
            pytest.skip(
                "Chromium 不可用＝BLOCKED（不得计为通过；不自动安装）："
                f"{exc}"
            )
        context = browser.new_context(service_workers="block")
        context.set_default_timeout(e2e_expect_timeout)
        allowed_origin = e2e_isolation.base_url

        context.on(
            "serviceworker",
            lambda worker: service_worker_events.append(worker.url),
        )

        def _record_sw_attempt(_source: Any, url: Any) -> None:
            text = str(url)
            sw_attempts.append(text)
            blocked.append(f"sw:{text}")

        context.expose_binding(
            "__hktax_record_service_worker_attempt", _record_sw_attempt
        )
        context.add_init_script(
            """
            (() => {
              const sw = navigator.serviceWorker;
              if (!sw || !sw.register) return;
              const original = sw.register.bind(sw);
              sw.register = (...args) => {
                try {
                  window.__hktax_record_service_worker_attempt(String(args[0]));
                } catch (_) {}
                return original(...args);
              };
            })();
            """
        )

        def _only_loopback(route: Any) -> None:
            parts = urlsplit(route.request.url)
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin == allowed_origin:
                route.continue_()
                return
            blocked.append(route.request.url)
            route.abort(error_code="blockedbyclient")

        def _reject_websocket(ws: Any) -> None:
            # 不调用 connect_to_server：Playwright 保持路由，页面消息不会出站。
            # 同步 handler 内 ws.close() 在 Playwright 1.63/Chromium 153 会死锁
            # （实测），故以「不连接＋吞消息」实现硬拒绝。
            ws_attempts.append(ws.url)
            blocked.append(f"ws:{ws.url}")
            ws.on_message(lambda _message: None)

        context.route("**/*", _only_loopback)
        context.route_web_socket("**/*", _reject_websocket)
        pg = context.new_page()
        e2e_isolation.record["browser"] = {
            "status": "running",
            "service_workers": "block",
            "web_sockets": "routed_rejected",
            "blocked_requests": list(blocked),
        }
        try:
            yield pg
        finally:
            unhandled: list[str] = []
            try:
                live_service_workers = [
                    worker.url for worker in context.service_workers
                ]
            except BaseException as exc:  # noqa: BLE001
                live_service_workers = []
                unhandled.append(f"service-worker 状态查询异常：{exc!r}")
            if live_service_workers or service_worker_events:
                unhandled.append(
                    "service-worker 未被阻止："
                    f"live={live_service_workers!r} "
                    f"events={service_worker_events!r}"
                )
            try:
                context.close()
            finally:
                browser.close()
            e2e_isolation.record["browser"] = {
                "status": (
                    "ok" if not blocked and not unhandled else "origin_violation"
                ),
                "service_workers": "block",
                "web_sockets": "routed_rejected",
                "blocked_requests": list(blocked),
                "web_socket_attempts": list(ws_attempts),
                "service_worker_attempts": list(sw_attempts),
                "service_worker_events": list(service_worker_events),
                "live_service_workers": live_service_workers,
                "unhandled_channels": list(unhandled),
            }
            failures: list[str] = []
            if blocked:
                failures.append(
                    "浏览器出站越界或触及被封禁通道（006 §6.3）："
                    f"{blocked!r}"
                )
            if unhandled:
                failures.append(
                    "浏览器存在未处理出站通道：" + "；".join(unhandled)
                )
            if failures:
                raise AssertionError("；".join(failures))
