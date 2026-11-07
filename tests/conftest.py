"""共享测试基建（M1 Red 阶段；tests/ 按 SDD §8 布局）。

REQ: 无独立 REQ（纯基建；服务各 API 层测试文件，见各文件头部）。
规格锚点:
  - Annex C C1（测试接线：API/单元测试经 httpx.ASGITransport 进程内执行；
    假件为进程内实现、不新增第三方库）。
  - SDD §8（DB 运行时默认 %LOCALAPPDATA%\\hktax-agent\\rules.db，HKTAX_DB_PATH 可覆盖；
    仓库内无 DB —— 测试一律注入临时路径）。
  - Annex C C5.1/C5.2/C5.3（合法 Host/Origin 精确集合、不安全方法须 Origin+CSRF、
    CSRF token 由服务器持有）。
  - W-L 安全准入（2026-10-16；共享 harness L1–L4）:
      L1 会话级真实边界 reject/counter guard，于「首次真实 app 构造前」安装：
         调度真实启动（Scheduler.startup/tick/trigger_manual、lifespan 闭包
         `_start_scheduler_threads`）、同步/异步真实 httpx transport
         （HTTPTransport/AsyncHTTPTransport）、模型适配器 `_post`、文档出站
         （`_HttpxDocumentOutbound.fetch`＝httpx.Client 真实传输）。不封禁
         httpx.Client/线程：ASGI 回环（ASGITransport）与 anyio portal 保留。
      L2 collection 前引导设置唯一 Temp DB env（HKTAX_DB_PATH），先于一切
         app.main 导入；未配置/不合规（越出批准 Temp 根、监控模块已先行导入）
         即拒绝（configure 失败，整会话不放行）。
      L3 implicit app（首次导入 module-level `app.main.app`）＋ explicit app
         均登记/核验/保护；捕获对象（lifespan 闭包 scheduler、适配器实例
         `_post`、文档出站类方法）逐项身份核验——不仅替换 app.state 接缝。
      L4 Temp 目录/DB 统一约束并登记至批准 Temp 根（`tempfile.tempdir` 约束
         使 testgate `mkdtemp()` 归属批准根；RuleStore 路径准入先于真实 I/O）。
期望值来源: 结构性契约，无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - app.main.create_app() -> ASGI app；DB 路径取 env HKTAX_DB_PATH（测试注入 tmp）。
  - 任意同源安全 GET（如 GET /）建立会话 cookie 的同时，经响应头 X-CSRF-Token
    发放 CSRF 能力；不安全方法（POST/PUT/PATCH/DELETE）须以同名请求头回呈该
    token，且 Origin 与 Host 完全一致（C5.2）。
  - 本模块对 app.* 一律延迟导入（fixture 内 import），使 Red 阶段每个测试的
    失败原因对应「缺失的 app.* 模块」，而非收集期整体失败。W-L 例外：collection
    前的安全引导不导入 app.main；首次导入由会话 fixture 在 guard 安装后、
    有效临时 DB env 下执行，使 implicit app 纳入仪表（L2/L3）。

环境（由启动器提供；conftest 自足兜底）:
  - HKTAX_TEST_TEMP_ROOT：批准 Temp 根（缺省＝tempfile.gettempdir()）。
  - HKTAX_DB_PATH：唯一 Temp DB env（缺省由引导创建；不合规即拒绝）。
  - HKTAX_SAFETY_EVIDENCE：可选，写 W-L 安全证据 JSON（可执行探针/登记账）。
  - 运行要求：pytest basetemp 必须位于批准 Temp 根内（--basetemp=<root>/...）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx
import pytest

REPO_ROOT_DIR = None  # 占位：由各测试文件按 __file__ 自行解析，避免本文件被误扫
BASE_ORIGIN = "http://127.0.0.1:8000"

TEMP_ROOT_ENV = "HKTAX_TEST_TEMP_ROOT"
DB_ENV = "HKTAX_DB_PATH"
EVIDENCE_ENV = "HKTAX_SAFETY_EVIDENCE"

# 先于安全引导即不得导入的受监控 app 模块（保证 DB env/guard 先于首次构造）
_MONITORED_APP_MODULES = (
    "app.main",
    "app.ai.adapters",
    "app.rules.store",
    "app.updater.scheduler",
    "app.updater.testgate",
    "app.api.routes",
    "app.web.security",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_under(root: Path, path: Path) -> bool:
    """严格包含：path 必须位于 root 之内；path == root **不接受**（R1 收紧）。

    - basetemp 必须为批准根内独立可丢弃子目录且 ≠ 根（pytest getbasetemp() 对
      既有给定目录先 rm_rf 再 mkdir：等于根即整根清除）；
    - RuleStore/DB env 等路径准入同样要求严格子路径（路径不得恰为根）。
    """
    return root in path.parents


def _owner_name(owner: Any) -> str:
    return getattr(owner, "__name__", repr(owner))


# R2：快照哨兵——区分「快照未取得」与「原值为空（None）」
_UNSPECIFIED = object()
_SNAPSHOT_TAKEN = False
_ORIGINAL_TEMP_DIR: Any = _UNSPECIFIED
_ORIGINAL_DB_ENV: Any = _UNSPECIFIED
# R1：basetemp 配置源准入记录（configure 期，getbasetemp 之前）
_BASETEMP_ADMISSION: dict[str, Any] = {}
# R2：unconfigure 收口记录（未取得 guard 时亦留存）
_SHUTDOWN_RECORD: dict[str, Any] = {}


def _env_describe(value: Any) -> Any:
    """证据用描述：<no-snapshot>（快照未取得）／<unset/None>（原值为空）／字符串值。"""
    if value is _UNSPECIFIED:
        return "<no-snapshot>"
    if value is None:
        return "<unset/None>"
    return str(value)


def _write_evidence_file(payload: dict[str, Any]) -> str | None:
    """按 EVIDENCE_ENV 写出证据 JSON（configure/unconfigure 全路径可用）。"""
    target = os.environ.get(EVIDENCE_ENV)
    if not target:
        return None
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(path)


class _SessionGuard:
    """W-L 会话级真实边界 guard：拒绝＋计数＋登记＋独立收口/还原。

    - 仅拒绝「真实 app 调度器」（按登记身份或生产文档出站类型识别）；
      updater/acceptance 的批准 Fake/Strict 本地调度不受影响；
    - RuleStore 路径准入先于真实 I/O，并登记全部 store 路径（含门禁临时 DB）；
    - 不触碰 httpx.Client 定义、threading、ASGITransport 与 anyio portal。
    """

    BOUNDARY_KEYS = (
        "scheduler_startup",
        "scheduler_tick",
        "scheduler_trigger",
        "scheduler_threads",
        "model_default_post",
        "document_fetch",
        "http_transport",
        "async_http_transport",
    )

    def __init__(self, approved_root: Path) -> None:
        self.approved_root = approved_root
        self.counters: dict[str, int] = {key: 0 for key in self.BOUNDARY_KEYS}
        self._patches: list[tuple[Any, str, Any, bool, Any]] = []
        self._installed: dict[str, Any] = {}
        self._owners: dict[str, tuple[Any, str]] = {}
        self._post_guards: list[Any] = []
        self.real_schedulers: dict[int, Any] = {}
        self.scheduler_labels: dict[int, str] = {}
        self.app_records: list[dict[str, Any]] = []
        self.store_paths: dict[str, str] = {}
        self.store_violations: list[str] = []
        self._probe_depth = 0
        self.probe_violations: list[str] = []
        self.self_check_record: dict[str, Any] = {}
        self.self_check_problems: list[str] = []
        self.drill_record: dict[str, Any] = {}
        self.drill_problems: list[str] = []
        self.r3_control: dict[str, Any] = {}
        self.r3_control_problems: list[str] = []
        self.test_events: list[dict[str, Any]] = []
        self._test_start: dict[str, Any] | None = None
        self.basetemp: Path | None = None
        self.effective_db: Path | None = None
        self.db_env_launcher: str | None = None
        self.imported_before_session: tuple[str, ...] = ()
        self.pre_bootstrap_imports: tuple[str, ...] = ()
        self.app_main_preloaded: bool | None = None
        self.patched_boundaries_snapshot: list[str] = []
        self.bootstrap_at: str | None = None
        # W0：仅显式注入 app 的 e2e 可临时换入可计数假生命周期；默认拒绝不变
        self.scoped_start_seam: Any = None

    # ------------------------------------------------------------------
    # 计数/快照（供测试文件读取证据；不改 guard 状态）
    # ------------------------------------------------------------------
    def _bump(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1

    def counter_snapshot(self) -> dict[str, int]:
        return dict(self.counters)

    def counter_deltas(self, snapshot: Mapping[str, int]) -> dict[str, int]:
        return {
            key: value
            for key, value in self.counters.items()
            if value != snapshot.get(key, 0)
        }

    def observed_store_paths(self) -> list[str]:
        return sorted(self.store_paths)

    def test_start(self, nodeid: str) -> None:
        self._test_start = {
            "nodeid": nodeid,
            "counters": dict(self.counters),
            "stores": set(self.store_paths),
        }

    def test_end(self, nodeid: str) -> None:
        baseline = self._test_start or {"counters": {}, "stores": set()}
        self.test_events.append(
            {
                "nodeid": nodeid,
                "counter_deltas": {
                    key: value
                    for key, value in self.counters.items()
                    if value != baseline["counters"].get(key, 0)
                },
                "new_store_paths": sorted(
                    set(self.store_paths) - set(baseline["stores"])
                ),
            }
        )
        self._test_start = None

    # ------------------------------------------------------------------
    # 安装/硬化
    # ------------------------------------------------------------------
    def _install_patch(self, owner: Any, attr: str, value: Any, *, name: str) -> None:
        if name in self._installed:
            raise RuntimeError(f"W-L guard 重复安装：{name}（须先 restore）")
        original = getattr(owner, attr)
        self._patches.append((owner, attr, original, True, value))
        self._installed[name] = value
        self._owners[name] = (owner, attr)
        setattr(owner, attr, value)

    def install(self) -> None:
        """import app.main 前安装：模型 `_post`、调度真实启动、同步/异步真实
        transport、RuleStore 路径准入。"""

        from app.ai import adapters as adapters_module
        from app.rules.store import RuleStore
        from app.updater import scheduler as scheduler_module

        guard = self

        def _guarded_default_post(*args: Any, **kwargs: Any) -> Any:
            guard._bump("model_default_post")
            raise AssertionError(
                "真实模型出站 _post 被调用（W-L 安全准入：测试不得发出模型网络请求）"
            )

        self._post_guards.append(_guarded_default_post)
        self._install_patch(
            adapters_module, "_default_post", _guarded_default_post,
            name="model_default_post",
        )

        def _is_protected_scheduler(instance: Any) -> bool:
            if id(instance) in guard.real_schedulers:
                return True
            outbound = getattr(instance, "_outbound", None)
            main_module = sys.modules.get("app.main")
            document_cls = (
                getattr(main_module, "_HttpxDocumentOutbound", None)
                if main_module is not None
                else None
            )
            return document_cls is not None and isinstance(outbound, document_cls)

        def _make_scheduler_guard(key: str, method: str) -> Callable[..., Any]:
            original = getattr(scheduler_module.Scheduler, method)

            def _guarded(instance: Any, *args: Any, **kwargs: Any) -> Any:
                if _is_protected_scheduler(instance):
                    guard._bump(key)
                    raise AssertionError(
                        f"真实 Scheduler.{method} 被调用"
                        "（W-L 安全准入：测试不得启动真实调度/出站）"
                    )
                return original(instance, *args, **kwargs)

            return _guarded

        for key, method in (
            ("scheduler_startup", "startup"),
            ("scheduler_tick", "tick"),
            ("scheduler_trigger", "trigger_manual"),
        ):
            self._install_patch(
                scheduler_module.Scheduler, method,
                _make_scheduler_guard(key, method), name=key,
            )

        def _guarded_http(instance: Any, *args: Any, **kwargs: Any) -> Any:
            guard._bump("http_transport")
            raise AssertionError(
                "真实 httpx.HTTPTransport 被调用（W-L 安全准入：测试不得真实网络）"
            )

        async def _guarded_async_http(instance: Any, *args: Any, **kwargs: Any) -> Any:
            guard._bump("async_http_transport")
            raise AssertionError(
                "真实 httpx.AsyncHTTPTransport 被调用"
                "（W-L 安全准入：测试不得真实网络）"
            )

        self._install_patch(
            httpx.HTTPTransport, "handle_request", _guarded_http,
            name="http_transport",
        )
        self._install_patch(
            httpx.AsyncHTTPTransport, "handle_async_request", _guarded_async_http,
            name="async_http_transport",
        )

        original_init = RuleStore.__init__
        original_connect = RuleStore._connect

        def _observed_init(store_self: Any, db_path: Any) -> None:
            guard.authorize_store_path(db_path, context="RuleStore.__init__")
            original_init(store_self, db_path)
            guard.authorize_store_path(
                store_self._path, context="RuleStore.__init__/done"
            )

        def _observed_connect(store_self: Any) -> Any:
            guard.authorize_store_path(store_self._path, context="RuleStore._connect")
            return original_connect(store_self)

        self._install_patch(RuleStore, "__init__", _observed_init, name="store_init")
        self._install_patch(
            RuleStore, "_connect", _observed_connect, name="store_connect"
        )

    def harden(self) -> None:
        """import app.main 之后安装：文档出站（httpx.Client 真实传输）与
        lifespan 调度线程启动 seam。

        默认替身保持「调用即拒绝＋计数」；显式注入 app 的 e2e 经
        `install_scoped_start_seam` 临时换入可计数假生命周期，且必须恢复。
        """
        import app.main as main_module

        guard = self

        def _guarded_fetch(instance: Any, *args: Any, **kwargs: Any) -> Any:
            guard._bump("document_fetch")
            raise AssertionError(
                "真实 _HttpxDocumentOutbound.fetch 被调用"
                "（W-L 安全准入：测试不得真实文档出站）"
            )

        def _guarded_threads(*args: Any, **kwargs: Any) -> Any:
            guard._bump("scheduler_threads")
            raise AssertionError(
                "真实 _start_scheduler_threads 被调用"
                "（W-L 安全准入：测试不得启动真实调度线程）"
            )

        self._install_patch(
            main_module._HttpxDocumentOutbound, "fetch", _guarded_fetch,
            name="document_fetch",
        )
        self._install_patch(
            main_module, "_start_scheduler_threads", _guarded_threads,
            name="scheduler_threads",
        )

    # ------------------------------------------------------------------
    # W0：显式注入 app 的作用域启动 seam（006 §6 ③；默认拒绝不放开）
    # ------------------------------------------------------------------
    def install_scoped_start_seam(self, seam: Any) -> None:
        """把 `_start_scheduler_threads` 临时换成显式注入 app 的假生命周期。

        仅当 guard 自身的拒绝替身仍原样安装时允许；调用方必须在 finally
        经 `restore_scoped_start_seam` 恢复。guard 的 `scheduler_threads`
        计数语义不变（真实启动仍计 0；假 seam 自身计数由调用方取证）。
        """
        import app.main as main_module

        installed = self._installed.get("scheduler_threads")
        if installed is None:
            raise RuntimeError("W-L: scheduler_threads 守卫未安装（harden 未运行）")
        if self.scoped_start_seam is not None:
            raise RuntimeError("W-L: scoped start seam 已存在（上次未恢复）")
        current = getattr(main_module, "_start_scheduler_threads", None)
        if current is not installed:
            raise RuntimeError(
                "W-L: 当前 _start_scheduler_threads 并非 guard 拒绝替身；"
                f"拒绝覆盖（{current!r}）"
            )
        self.scoped_start_seam = seam
        setattr(main_module, "_start_scheduler_threads", seam)

    def restore_scoped_start_seam(self, seam: Any) -> list[str]:
        """恢复 guard 拒绝替身并核验身份（问题以列表返回，不吞异常）。"""
        import app.main as main_module

        problems: list[str] = []
        installed = self._installed.get("scheduler_threads")
        if self.scoped_start_seam is not seam:
            problems.append("scoped start seam 登记身份不符")
        current = getattr(main_module, "_start_scheduler_threads", None)
        if current is not seam:
            problems.append(f"恢复前 scoped start seam 已被替换/丢失：{current!r}")
        if installed is None:
            problems.append("scheduler_threads 守卫未安装，无法恢复")
        else:
            setattr(main_module, "_start_scheduler_threads", installed)
            if getattr(main_module, "_start_scheduler_threads", None) is not installed:
                problems.append("恢复 guard scheduler_threads 拒绝替身失败")
        self.scoped_start_seam = None
        return problems

    # ------------------------------------------------------------------
    # 登记/核验（implicit＋explicit app 与捕获对象）
    # ------------------------------------------------------------------
    def authorize_store_path(self, path: Any, *, context: str) -> Path:
        """RuleStore 路径准入（先于真实 I/O）＋登记至批准 Temp 根。"""
        try:
            resolved = Path(str(path)).resolve()
        except OSError as exc:
            resolved = None
            message = f"{context}: RuleStore 路径不可解析（{exc!r}）：{path!s}"
            if self._probe_depth:
                self.probe_violations.append(message)
            else:
                self.store_violations.append(message)
            raise AssertionError(message) from exc
        if not _is_under(self.approved_root, resolved):
            message = (
                f"{context}: RuleStore 路径越出批准 Temp 根（先于真实 I/O 拒绝）："
                f"{resolved}"
            )
            if self._probe_depth:
                self.probe_violations.append(message)
            else:
                self.store_violations.append(message)
            raise AssertionError(message)
        kind = (
            "gate"
            if any(part.startswith("hktax-gate-") for part in resolved.parts)
            else "store"
        )
        self.store_paths.setdefault(str(resolved), kind)
        return resolved

    def register_app(self, label: str, app: Any) -> dict[str, Any]:
        """登记/核验 app 与其捕获对象（lifespan 闭包 scheduler、`_post`）。"""
        import app.main as main_module
        from app.updater import scheduler as scheduler_module

        store = getattr(app.state, "store", None)
        if store is None:
            raise RuntimeError(f"W-L: {label} app 缺 app.state.store（不得无 store 构造）")
        store_path = self.authorize_store_path(
            store._path, context=f"register_app({label})"
        )
        scheduler = getattr(app.state, "update_scheduler", None)
        if scheduler is None:
            raise RuntimeError(f"W-L: {label} app 缺 app.state.update_scheduler")
        self.real_schedulers[id(scheduler)] = scheduler
        self.scheduler_labels[id(scheduler)] = label

        closure_scheduler = _lifespan_closure_scheduler(app, scheduler_module.Scheduler)
        document_fetch_guard = self._installed.get("document_fetch")
        fetch_is_guarded = (
            getattr(main_module._HttpxDocumentOutbound, "fetch", None)
            is document_fetch_guard
        )
        model_outbound = getattr(app.state, "model_outbound", None)
        post = getattr(model_outbound, "_post", None)
        post_is_guarded = any(post is candidate for candidate in self._post_guards)

        problems: list[str] = []
        if closure_scheduler is not scheduler:
            problems.append(f"{label} lifespan 闭包 scheduler identity 不符（捕获对象未保护）")
        if not fetch_is_guarded:
            problems.append(f"{label} _HttpxDocumentOutbound.fetch 守卫 identity 不符")
        if not post_is_guarded:
            problems.append(f"{label} 模型适配器 _post 未捕获 guard 替身（构造先于 guard？）")
        record = {
            "label": label,
            "app_id": id(app),
            "store_path": str(store_path),
            "scheduler_id": id(scheduler),
            "lifespan_closure_scheduler_ok": closure_scheduler is scheduler,
            "document_fetch_guarded": fetch_is_guarded,
            "model_post_guarded": post_is_guarded,
            "problems": problems,
        }
        self.app_records.append(record)
        if problems:
            raise RuntimeError("W-L app 登记核验失败：" + "；".join(problems))
        return record

    def unregister_scheduler(self, scheduler: Any) -> None:
        if scheduler is None:
            return
        self.real_schedulers.pop(id(scheduler), None)
        self.scheduler_labels.pop(id(scheduler), None)

    def finish_explicit_app(
        self,
        app: Any,
        counters_before: Mapping[str, int],
        stores_before: set[str],
        close_error: BaseException | None,
    ) -> list[str]:
        """共享 fixture 收口（独立于测试断言）：计数增量、登记、捕获身份。"""
        problems: list[str] = []
        if close_error is not None:
            problems.append(f"client.close 异常：{close_error!r}")
        deltas = self.counter_deltas(counters_before)
        if deltas:
            problems.append(f"真实边界调用计数非零（本测试）：{deltas!r}")
        new_stores = [
            path for path in self.observed_store_paths() if path not in stores_before
        ]
        for path in new_stores:
            if not _is_under(self.approved_root, Path(path)):
                problems.append(f"新 store 越出批准 Temp 根：{path}")
        scheduler = getattr(app.state, "update_scheduler", None)
        if id(scheduler) not in self.real_schedulers:
            problems.append("显式 app scheduler 未登记（受控接缝被替换？）")
        model_outbound = getattr(app.state, "model_outbound", None)
        post = getattr(model_outbound, "_post", None)
        if not any(post is candidate for candidate in self._post_guards):
            problems.append("显式 app 适配器 _post 非 guard 替身")
        return problems

    # ------------------------------------------------------------------
    # 可执行自检（正/反双向）与失败路径演练
    # ------------------------------------------------------------------
    def _run_probe_cycle(self) -> dict[str, Any]:
        """执行一轮真实边界拒绝探针＋批准路径恢复探针，返回记录（不改记录字段）。

        R3：探针增量只累计到调用方保存/恢复的账上；本方法不整字典归零，
        自检前的真实阶段累计由 `self_check` 保存并恢复（不被洗掉）。
        """
        import app.main as main_module
        from app.ai.adapters import HttpxModelOutbound
        from app.rules.store import RuleStore

        record: dict[str, Any] = {
            "at": _utc_now(), "probes": [], "allowed": [], "problems": [],
            "boundaries": list(self.BOUNDARY_KEYS),
        }
        implicit_app = getattr(main_module, "app", None)
        if implicit_app is None:
            record["problems"].append("implicit app 缺失（app.main 未构造 module-level app）")
            return record
        scheduler = implicit_app.state.update_scheduler
        model_outbound = implicit_app.state.model_outbound

        def probe(name: str, counter: str | None, fn: Callable[[], Any]) -> None:
            before = self.counters.get(counter, 0) if counter else 0
            refused = False
            error: str | None = None
            try:
                fn()
            except AssertionError:
                refused = True
            except BaseException as exc:  # noqa: BLE001 - 非拒绝异常须显式暴露
                error = repr(exc)
            delta = None if counter is None else self.counters[counter] - before
            record["probes"].append(
                {
                    "name": name, "refused": refused, "counter": counter,
                    "counter_delta": delta, "error": error,
                }
            )
            if not refused:
                record["problems"].append(f"{name} 未被拒绝（error={error!r}）")
            elif counter is not None and delta != 1:
                record["problems"].append(f"{name} 拒绝计数异常：{delta}")

        probe("scheduler.startup", "scheduler_startup", scheduler.startup)
        probe("scheduler.tick", "scheduler_tick", scheduler.tick)
        probe("scheduler.trigger_manual", "scheduler_trigger", scheduler.trigger_manual)
        probe(
            "document_outbound.fetch", "document_fetch",
            lambda: scheduler._outbound.fetch("https://www.ird.gov.hk/eng/tax/budget.htm"),
        )
        probe(
            "model_outbound.send->_post", "model_default_post",
            lambda: model_outbound.send(
                "https://model.invalid/v1/chat/completions", "sk-probe", b"{}"
            ),
        )
        probe(
            "_start_scheduler_threads", "scheduler_threads",
            lambda: main_module._start_scheduler_threads(scheduler),
        )
        probe(
            "HTTPTransport.handle_request", "http_transport",
            lambda: httpx.HTTPTransport.handle_request(object(), object()),
        )
        probe(
            "AsyncHTTPTransport.handle_async_request", "async_http_transport",
            lambda: asyncio.run(
                httpx.AsyncHTTPTransport.handle_async_request(object(), object())
            ),
        )

        # —— RuleStore 路径拒绝探针（先于真实 I/O；探针记录与真实违规分开）——
        self._probe_depth += 1
        try:
            outside = self.approved_root.parent / f"hktax-forbidden-probe-{os.getpid()}.db"
            probe("store_outside_root_refusal", None, lambda: RuleStore(outside))
            if outside.exists():
                record["problems"].append("越界 store 探针在拒绝后仍创建了文件")
            saved_env = os.environ.pop(DB_ENV, None)
            try:
                from app.config import db_path_from_env

                fallback = db_path_from_env()
            finally:
                if saved_env is not None:
                    os.environ[DB_ENV] = saved_env
            record["env_missing_fallback"] = str(fallback)
            probe(
                "store_unconfigured_env_refusal", None, lambda: RuleStore(fallback)
            )
        finally:
            self._probe_depth -= 1

        # —— 批准路径恢复探针（拒绝后必须仍可用）——
        probe_dir = self.approved_root / f"hktax-guard-probe-{os.getpid()}"
        try:
            probe_store = RuleStore(probe_dir / "probe.db")
            connection = probe_store._connect()
            try:
                value = connection.execute("SELECT 1").fetchone()
            finally:
                connection.close()
            ok = value is not None
            record["allowed"].append(
                {"name": "approved_root_store", "ok": ok, "path": str(probe_dir / "probe.db")}
            )
            if not ok:
                record["problems"].append("批准根内 store 探针返回空")
        except BaseException as exc:  # noqa: BLE001
            record["allowed"].append(
                {"name": "approved_root_store", "ok": False, "error": repr(exc)}
            )
            record["problems"].append(f"批准根内 store 被误拒：{exc!r}")
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)

        class _ProbeResponse:
            status_code = 200

            def iter_bytes(self):
                yield b"ok"

            def close(self) -> None:
                pass

        try:
            injected = HttpxModelOutbound(post=lambda url, **kw: _ProbeResponse())
            injected_result = injected.send(
                "https://model.invalid/v1/chat/completions", "sk", b"{}"
            )
            ok = injected_result == {"status_code": 200, "body": b"ok"}
            record["allowed"].append({"name": "injected_post_adapter", "ok": ok})
            if not ok:
                record["problems"].append(f"注入 post 适配器行为异常：{injected_result!r}")
        except BaseException as exc:  # noqa: BLE001
            record["allowed"].append(
                {"name": "injected_post_adapter", "ok": False, "error": repr(exc)}
            )
            record["problems"].append(f"注入 post 适配器被误拒：{exc!r}")

        counters_before = dict(self.counters)
        try:
            loopback_client = httpx.Client(
                transport=httpx.ASGITransport(app=implicit_app), base_url=BASE_ORIGIN
            )
        except BaseException as exc:  # noqa: BLE001
            record["problems"].append(f"ASGI 回环客户端创建失败：{exc!r}")
        else:
            try:
                status: Any = loopback_client.get("/").status_code
            except BaseException as exc:  # noqa: BLE001 - app 行为失败不阻断 guard 准入
                status = f"exception:{exc!r}"
            finally:
                loopback_client.close()
            loopback_delta = {
                key: self.counters[key] - counters_before[key]
                for key in self.counters
                if self.counters[key] != counters_before[key]
            }
            record["allowed"].append(
                {
                    "name": "asgi_loopback_get_root", "status": status,
                    "boundary_counter_delta": loopback_delta,
                }
            )
            if loopback_delta:
                record["problems"].append(f"ASGI 回环触发真实边界计数：{loopback_delta!r}")

        return record

    def self_check(self) -> None:
        """独立自检（R3）：保存自检前真实累计账 → 一轮探针 → 恢复该基数。

        探针增量只进入本轮 record（逐探针 delta＋pre/post 账快照），**不**以整
        字典归零洗掉自检前的真实计数——B3 终态零不能外推为 configure 起零调用。
        """
        base = dict(self.counters)
        record = self._run_probe_cycle()
        record["pre_cycle_counters"] = dict(base)
        self.counters.clear()
        self.counters.update(base)
        record["post_cycle_counters"] = dict(self.counters)
        self.self_check_record = record
        self.self_check_problems = list(record["problems"])

    def control_selfcheck_preserves_prior_counts(self) -> None:
        """R3 控制（可执行）：「自检前非零不被洗掉」——无需真实网络。

        合成一个自检前非零计数 → 执行完整 self_check（保存/恢复基数）→ 断言
        该非零保留、且其余键无探针残留；最后移除合成脚手架、恢复控制前真实账。
        合成标记的存在与移除均记录于证据（不是对真实计数的清洗）。
        """
        record: dict[str, Any] = {"at": _utc_now(), "problems": []}
        base = dict(self.counters)
        marker = "scheduler_tick"
        injected = base.get(marker, 0) + 7  # 合成「自检前真实非零」
        primary_record = self.self_check_record
        primary_problems = list(self.self_check_problems)
        cycle_record: dict[str, Any] = {}
        control_problems: list[str] = []
        self.counters[marker] = injected
        try:
            self.self_check()
            cycle_record = self.self_check_record
            control_problems = list(self.self_check_problems)
        finally:
            self.self_check_record = primary_record
            self.self_check_problems = primary_problems
            # 移除合成脚手架：恢复控制前真实账（非清洗真实计数）
            self.counters.clear()
            self.counters.update(base)
        probe_summary = [
            {
                "name": entry.get("name"), "refused": entry.get("refused"),
                "counter_delta": entry.get("counter_delta"),
            }
            for entry in cycle_record.get("probes", [])
        ]
        post = dict(cycle_record.get("post_cycle_counters", {}))
        record["injected_marker"] = {marker: injected}
        record["pre_control_counters"] = dict(base)
        record["post_cycle_counters"] = post
        record["post_control_counters"] = dict(self.counters)
        record["control_cycle_problems"] = control_problems
        record["control_cycle_probe_summary"] = probe_summary
        preserved = post.get(marker) == injected
        other_residue = {
            key: value
            for key, value in post.items()
            if key != marker and value != base.get(key, 0)
        }
        record["marker_preserved"] = preserved
        record["other_key_residue"] = other_residue
        if not preserved:
            record["problems"].append(
                f"自检洗掉了自检前非零计数：{marker}={injected} → {post.get(marker)}"
            )
        if other_residue:
            record["problems"].append(f"探针计数残留/其它键变动：{other_residue!r}")
        if control_problems:
            record["problems"].append(f"控制周期自检问题：{control_problems!r}")
        self.r3_control = record
        self.r3_control_problems = list(record["problems"])

    def drill_restore_reinstall(self) -> None:
        """失败路径独立核验（可执行）：还原→身份核验→重装→再次拒绝。"""
        import app.main as main_module

        record: dict[str, Any] = {"at": _utc_now(), "problems": []}
        installed_before = dict(self._installed)
        owners_before = dict(self._owners)
        restore_problems = self.restore()
        record["restore_problems"] = list(restore_problems)
        record["problems"].extend(restore_problems)
        identity: dict[str, bool] = {}
        for name, (owner, attr) in owners_before.items():
            current = getattr(owner, attr, None)
            identity[name] = current is not installed_before.get(name)
        record["post_restore_identity"] = identity
        for name, restored in identity.items():
            if not restored:
                record["problems"].append(f"还原后 {name} 仍为安装替身")
        try:
            self.install()
            self.harden()
        except BaseException as exc:  # noqa: BLE001 - 重装失败必须显式暴露
            record["problems"].append(f"演练重装异常：{exc!r}")
            self.drill_record = record
            self.drill_problems = list(record["problems"])
            raise
        scheduler = main_module.app.state.update_scheduler
        probe_base = dict(self.counters)
        before = self.counters["scheduler_startup"]
        refused = False
        try:
            scheduler.startup()
        except AssertionError:
            refused = True
        except BaseException as exc:  # noqa: BLE001
            record["problems"].append(f"重装后 startup 探针异常：{exc!r}")
        delta = self.counters["scheduler_startup"] - before
        record["reinstall_probe"] = {"refused": refused, "counter_delta": delta}
        record["counters_before_probe"] = dict(probe_base)
        self.counters.clear()
        self.counters.update(probe_base)  # R3：恢复演练前基数（不整字典归零）
        record["counters_after_probe_restore"] = dict(self.counters)
        if not refused or delta != 1:
            record["problems"].append("重装后 startup 未被拒绝/计数异常")
        self.drill_record = record
        self.drill_problems = list(record["problems"])

    # ------------------------------------------------------------------
    # 收口/还原
    # ------------------------------------------------------------------
    def verify_session(self) -> list[str]:
        problems: list[str] = []
        if self.scoped_start_seam is not None:
            problems.append("scoped start seam 未恢复（e2e 隔离泄漏）")
        bad = {key: value for key, value in self.counters.items() if value}
        if bad:
            problems.append(f"真实边界累计计数非零：{bad!r}")
        if self.store_violations:
            problems.append(f"store 路径拒绝记录非空：{self.store_violations!r}")
        for owner, attr, original, had, installed in self._patches:
            if getattr(owner, attr, None) is not installed:
                problems.append(f"安装替身已被替换/丢失：{_owner_name(owner)}.{attr}")
        for record in self.app_records:
            if record["problems"]:
                problems.append(f"app 登记问题：{record['problems']!r}")
        return problems

    def restore(self) -> list[str]:
        problems: list[str] = []
        for owner, attr, original, had, installed in reversed(self._patches):
            try:
                if had:
                    setattr(owner, attr, original)
                else:
                    delattr(owner, attr)
            except BaseException as exc:  # noqa: BLE001 - 恢复失败必须显式暴露
                problems.append(f"恢复 {_owner_name(owner)}.{attr} 失败：{exc!r}")
        for owner, attr, original, had, installed in self._patches:
            current = getattr(owner, attr, None)
            if had and current is not original:
                problems.append(f"还原后 {_owner_name(owner)}.{attr} 与原对象不同")
            if (not had) and current is not None:
                problems.append(f"还原后 {_owner_name(owner)}.{attr} 仍存在（原为不存在）")
        self._patches = []
        self._installed = {}
        self._owners = {}
        return problems

    # ------------------------------------------------------------------
    # 证据
    # ------------------------------------------------------------------
    def evidence_payload(self, *, stage: str) -> dict[str, Any]:
        return {
            "stage": stage,
            "generated_at": _utc_now(),
            "bootstrap_at": self.bootstrap_at,
            "approved_root": str(self.approved_root),
            "temp_root_env": os.environ.get(TEMP_ROOT_ENV),
            "basetemp": None if self.basetemp is None else str(self.basetemp),
            "db_env_launcher": self.db_env_launcher,
            "db_env_effective": (
                None if self.effective_db is None else str(self.effective_db)
            ),
            "app_main_preloaded_before_session_fixture": self.app_main_preloaded,
            "monitored_modules_pre_bootstrap": list(self.pre_bootstrap_imports),
            "monitored_modules_at_session_fixture": list(self.imported_before_session),
            "self_check": self.self_check_record,
            "self_check_problems": self.self_check_problems,
            "r3_control": self.r3_control,
            "r3_control_problems": self.r3_control_problems,
            "restore_reinstall_drill": self.drill_record,
            "drill_problems": self.drill_problems,
            "basetemp_admission": dict(_BASETEMP_ADMISSION),
            "app_records": self.app_records,
            "observed_store_paths": {
                path: self.store_paths[path] for path in self.observed_store_paths()
            },
            "store_violations": self.store_violations,
            "probe_violations": self.probe_violations,
            "counters": dict(self.counters),
            "test_events": self.test_events,
            "patched_boundaries": (
                list(self.patched_boundaries_snapshot) or list(self._installed)
            ),
        }

    def write_evidence(self, *, stage: str) -> str | None:
        target = os.environ.get(EVIDENCE_ENV)
        if not target:
            return None
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.evidence_payload(stage=stage), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)


def _lifespan_closure_scheduler(app: Any, scheduler_cls: type) -> Any | None:
    """从 FastAPI lifespan 闭包提取 scheduler（独立核验捕获对象 identity）。"""
    lifespan = getattr(getattr(app, "router", None), "lifespan_context", None)
    func = getattr(lifespan, "__wrapped__", lifespan)
    found: list[Any] = []
    for cell in getattr(func, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if isinstance(value, scheduler_cls):
            found.append(value)
    return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------
# 会话引导（L1/L2/L4 安装；pytest_configure 先于 collection）
# ---------------------------------------------------------------------------
_GUARD: _SessionGuard | None = None
_APPROVED_ROOT: Path | None = None


def _require_guard() -> _SessionGuard:
    if _GUARD is None:
        raise RuntimeError("W-L 安全引导未运行（pytest_configure 未完成）")
    return _GUARD


def _record_basetemp_verdict(record: dict[str, Any]) -> None:
    _BASETEMP_ADMISSION.clear()
    _BASETEMP_ADMISSION.update(record)


def _validate_basetemp_admission(config: pytest.Config, root: Path) -> None:
    """R1：basetemp 配置源准入（**先于任何 getbasetemp()/目录清理**）。

    pytest `TempPathFactory.getbasetemp()`（_pytest/tmpdir.py）对给定 basetemp
    会先 `rm_rf(既有目录)` 再 mkdir——任何校验必须在 getbasetemp() 之前由配置源
    直读完成（本函数绝不调用 getbasetemp()）：
    - 给定 `--basetemp`：解析后必须是批准根的**严格子目录（≠ 根）**，且不得为
      当前工作目录或其祖先（独立、可丢弃）；拒绝时被拒路径内容原样保留。
    - 未给定：默认编号目录由 pytest 在（已被引导覆盖的）tempfile.gettempdir()
      下新建；若设 PYTEST_DEBUG_TEMPROOT，同样要求解析后为批准根严格子路径。
    """
    record: dict[str, Any] = {"validated_before_getbasetemp": True, "at": _utc_now()}
    raw = getattr(config.option, "basetemp", None)
    record["given_basetemp"] = None if raw is None else str(raw)
    cwd = Path.cwd().resolve()

    def _refuse(verdict: str, message: str) -> None:
        record["verdict"] = verdict
        _record_basetemp_verdict(record)
        raise RuntimeError(message)

    if raw is not None:
        try:
            candidate = Path(str(raw)).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            _refuse(
                "refused-unresolvable",
                "W-L 安全准入拒绝（configure 期，getbasetemp 之前）：basetemp "
                f"不可解析：{raw!r}（{exc!r}）",
            )
        record["resolved_basetemp"] = str(candidate)
        if not _is_under(root, candidate):
            _refuse(
                "refused-not-strict-subdir",
                "W-L 安全准入拒绝（configure 期，getbasetemp 之前）：--basetemp 必须为"
                f"批准 Temp 根内独立可丢弃子目录且 ≠ 根：[basetemp]{candidate} 不是"
                f"[{root}] 的严格子路径",
            )
        if candidate == cwd or candidate in cwd.parents:
            _refuse(
                "refused-cwd-ancestor",
                "W-L 安全准入拒绝（configure 期，getbasetemp 之前）：--basetemp 不得为"
                f"当前工作目录或其祖先（须独立可丢弃）：{candidate} vs cwd={cwd}",
            )
        record["verdict"] = "accepted-strict-subdir"
    else:
        debug_temproot = os.environ.get("PYTEST_DEBUG_TEMPROOT")
        record["pytest_debug_temproot"] = debug_temproot
        if debug_temproot:
            try:
                temproot = Path(debug_temproot).expanduser().resolve()
            except (OSError, RuntimeError) as exc:
                _refuse(
                    "refused-debug-temproot-unresolvable",
                    "W-L 安全准入拒绝（configure 期，getbasetemp 之前）："
                    f"PYTEST_DEBUG_TEMPROOT 不可解析：{debug_temproot!r}（{exc!r}）",
                )
            if not _is_under(root, temproot):
                _refuse(
                    "refused-debug-temproot-outside",
                    "W-L 安全准入拒绝（configure 期，getbasetemp 之前）："
                    f"PYTEST_DEBUG_TEMPROOT 必须为批准 Temp 根严格子路径：{temproot} "
                    f"不是 [{root}] 的严格子路径",
                )
            record["verdict"] = "accepted-debug-temproot"
        else:
            record["verdict"] = "accepted-default-numbered-dir"
    _record_basetemp_verdict(record)


def pytest_configure(config: pytest.Config) -> None:
    global _GUARD, _APPROVED_ROOT, _SNAPSHOT_TAKEN
    global _ORIGINAL_TEMP_DIR, _ORIGINAL_DB_ENV

    # R2：首变更（tempfile.tempdir=…/os.environ）之前的完整快照（含 None）；
    #     哨兵区分「快照未取得」与「原值为空」。
    _ORIGINAL_TEMP_DIR = tempfile.tempdir
    _ORIGINAL_DB_ENV = os.environ.get(DB_ENV)
    _SNAPSHOT_TAKEN = True

    root_env = os.environ.get(TEMP_ROOT_ENV)
    root = Path(root_env).resolve() if root_env else Path(tempfile.gettempdir()).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"W-L 安全引导拒绝：批准 Temp 根不可用 {root}（{exc!r}）") from exc
    if not root.is_dir():
        raise RuntimeError(f"W-L 安全引导拒绝：批准 Temp 根不是目录：{root}")
    _APPROVED_ROOT = root

    # R1：basetemp 配置源准入——先于任何 getbasetemp()/目录清理
    _validate_basetemp_admission(config, root)

    # L4：统一约束进程内临时目录（testgate mkdtemp 等归属批准根）
    tempfile.tempdir = str(root)

    # L2 顺序核验：受监控 app 模块不得先于引导导入
    imported = tuple(name for name in _MONITORED_APP_MODULES if name in sys.modules)
    if imported:
        raise RuntimeError(
            "W-L 安全引导拒绝：受监控 app 模块先于引导导入（DB env/guard 未先行）："
            f"{imported!r}"
        )

    # L2：唯一 Temp DB env（先于 collection 与一切 app.main 导入；R2：取值自快照）
    launcher_db = _ORIGINAL_DB_ENV
    if launcher_db:
        db_path = Path(launcher_db).resolve()
        if not _is_under(root, db_path):
            raise RuntimeError(
                "W-L 安全引导拒绝：HKTAX_DB_PATH 越出批准 Temp 根（拒绝真实用户路径）："
                f"{db_path} 不在 {root} 内"
            )
    else:
        db_dir = Path(tempfile.mkdtemp(prefix="hktax-collection-db-", dir=str(root)))
        db_path = db_dir / "rules.db"
    os.environ[DB_ENV] = str(db_path)

    guard = _SessionGuard(root)
    guard.db_env_launcher = launcher_db
    guard.pre_bootstrap_imports = imported
    try:
        guard.install()
    except BaseException as exc:
        # R2：安装失败路径的还原问题不得丢弃（留存＋并入异常显式报告）
        install_restore_problems = guard.restore()
        if install_restore_problems:
            raise RuntimeError(
                "W-L 引导安装失败且还原有问题：" + "；".join(install_restore_problems)
            ) from exc
        raise
    _GUARD = guard
    guard.bootstrap_at = _utc_now()


def pytest_unconfigure(config: pytest.Config) -> None:
    global _GUARD, _SHUTDOWN_RECORD

    guard = _GUARD
    restore_problems: list[str] = []
    if guard is not None:
        if guard._patches:
            try:
                restore_problems.extend(guard.restore())
            except BaseException as exc:  # noqa: BLE001 - 恢复异常必须留存
                restore_problems.append(f"unconfigure restore 异常：{exc!r}")
        payload: dict[str, Any] = guard.evidence_payload(stage="unconfigure")
    else:
        payload = {"stage": "unconfigure", "guard": None}

    # R2：快照已取得则**含 None 无条件恢复**（原值 None ⇒ 恢复为未设置语义）
    if _SNAPSHOT_TAKEN:
        try:
            tempfile.tempdir = _ORIGINAL_TEMP_DIR
        except BaseException as exc:  # noqa: BLE001
            restore_problems.append(f"tempfile.tempdir 恢复异常：{exc!r}")
        try:
            if _ORIGINAL_DB_ENV is None:
                os.environ.pop(DB_ENV, None)
            else:
                os.environ[DB_ENV] = _ORIGINAL_DB_ENV
        except BaseException as exc:  # noqa: BLE001
            restore_problems.append(f"{DB_ENV} 恢复异常：{exc!r}")

    shutdown: dict[str, Any] = {
        "snapshot_taken": _SNAPSHOT_TAKEN,
        "original_tempdir": _env_describe(_ORIGINAL_TEMP_DIR),
        "original_db_env": _env_describe(_ORIGINAL_DB_ENV),
        "post_restore_tempdir": _env_describe(tempfile.tempdir),
        "post_restore_db_env": _env_describe(os.environ.get(DB_ENV)),
        "basetemp_admission": dict(_BASETEMP_ADMISSION),
        "restore_problems": list(restore_problems),
    }
    payload["shutdown"] = shutdown
    _SHUTDOWN_RECORD = shutdown
    try:
        _write_evidence_file(payload)
    except BaseException as exc:  # noqa: BLE001
        print(f"[W-L] 证据写出失败：{exc!r}", file=sys.stderr)
    _GUARD = None
    if restore_problems:
        # R2：恢复异常显式报告（留存于证据 + stderr + INTERNALERROR 可见）
        print(
            f"[W-L] unconfigure 恢复问题（已留存证据）：{restore_problems!r}",
            file=sys.stderr,
        )
        raise RuntimeError("W-L unconfigure 恢复问题：" + "；".join(restore_problems))


@pytest.fixture(scope="session", autouse=True)
def _wl_session_safety(tmp_path_factory: pytest.TempPathFactory):
    """首次 app.main 导入（implicit app 纳入仪表）＋basetemp 对齐＋自检/演练。

    先于任何 function fixture 执行；退出时独立收口（计数/登记/身份）后无条件
    还原全部边界。问题一律显式暴露（session teardown error）。
    """
    guard = _require_guard()
    main_module: Any = None

    try:
        basetemp = tmp_path_factory.getbasetemp().resolve()
        if not _is_under(guard.approved_root, basetemp):
            raise RuntimeError(
                "W-L 安全准入拒绝：pytest basetemp 必须位于批准 Temp 根内且 ≠ 根"
                f"（独立可丢弃子目录；--basetemp=<root>/...）：{basetemp} 不是"
                f"[{guard.approved_root}] 的严格子路径"
            )
        guard.basetemp = basetemp
        launcher_effective = os.environ.get(DB_ENV)
        if launcher_effective and _is_under(basetemp, Path(launcher_effective).resolve()):
            effective_db = Path(launcher_effective).resolve()
        else:
            boot_dir = basetemp / "hktax-bootstrap"
            boot_dir.mkdir(parents=True, exist_ok=True)
            effective_db = boot_dir / "rules.db"
        os.environ[DB_ENV] = str(effective_db)
        guard.effective_db = effective_db

        guard.app_main_preloaded = "app.main" in sys.modules
        guard.imported_before_session = tuple(
            name for name in _MONITORED_APP_MODULES if name in sys.modules
        )
        import app.main as main_module  # 首次导入：有效 Temp DB env 已生效

        guard.harden()
        guard.register_app("implicit", main_module.app)
        guard.self_check()
        if guard.self_check_problems:
            raise RuntimeError(
                "W-L 安全自检失败：" + "；".join(guard.self_check_problems)
            )
        guard.drill_restore_reinstall()
        if guard.drill_problems:
            raise RuntimeError(
                "W-L 还原/重装演练失败：" + "；".join(guard.drill_problems)
            )
        # R3 控制：自检前非零计数不被洗掉（可执行；无需真实网络）
        guard.control_selfcheck_preserves_prior_counts()
        if guard.r3_control_problems:
            raise RuntimeError(
                "W-L R3 控制失败：" + "；".join(guard.r3_control_problems)
            )
        guard.write_evidence(stage="bootstrap")
        yield guard
    finally:
        problems: list[str] = []
        guard.patched_boundaries_snapshot = list(guard._installed)
        try:
            problems.extend(guard.verify_session())
        except BaseException as exc:  # noqa: BLE001 - 收口异常不得跳过还原
            problems.append(f"会话收口异常：{exc!r}")
        try:
            problems.extend(guard.restore())
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"会话还原异常：{exc!r}")
        try:
            guard.write_evidence(stage="session-end")
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"证据写出异常：{exc!r}")
        if problems:
            raise AssertionError("W-L 会话安全收口失败：" + "；".join(problems))


@pytest.fixture(scope="session")
def approved_temp_root() -> Path:
    """W-L 批准 Temp 根（HKTAX_TEST_TEMP_ROOT 或系统 Temp；唯一）。"""
    if _APPROVED_ROOT is None:
        raise RuntimeError("W-L 安全引导未运行")
    return _APPROVED_ROOT


@pytest.fixture
def safety_guard() -> _SessionGuard:
    """读取 W-L 会话安全 guard（计数/登记证据；勿改其内部状态）。"""
    return _require_guard()


def pytest_runtest_setup(item: pytest.Item) -> None:
    if _GUARD is not None:
        _GUARD.test_start(item.nodeid)


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    if _GUARD is not None:
        _GUARD.test_end(item.nodeid)


class Api:
    """在合法回环 Host/Origin 下带 CSRF 能力访问 API 的薄封装。"""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._csrf_token: str | None = None

    def _token(self) -> str:
        if self._csrf_token is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, (
                "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3 拟名契约："
                "会话建立时经响应头发放 CSRF 能力）"
            )
            self._csrf_token = token
        return self._csrf_token

    def get(self, path: str, *, headers: dict | None = None):
        return self._client.get(path, headers=headers)

    def post(
        self,
        path: str,
        *,
        json: object | None = None,
        origin: str | None = BASE_ORIGIN,
        csrf: bool = True,
        headers: dict | None = None,
    ):
        h = dict(headers or {})
        if origin is not None:
            h["Origin"] = origin
        if csrf:
            h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json, headers=h)

    def delete(
        self,
        path: str,
        *,
        origin: str | None = BASE_ORIGIN,
        csrf: bool = True,
        headers: dict | None = None,
    ):
        h = dict(headers or {})
        if origin is not None:
            h["Origin"] = origin
        if csrf:
            h["X-CSRF-Token"] = self._token()
        return self._client.delete(path, headers=h)


def _build_guarded_explicit_app(tmp_path, monkeypatch):
    """共享 fixture 共用装配：唯一临时 DB → create_app → 登记核验 → 回环客户端。"""
    monkeypatch.setenv(DB_ENV, str(tmp_path / "rules.db"))
    from app.main import create_app  # 延迟导入：Red 失败原因=缺失 app 模块

    app = create_app()
    guard = _require_guard()
    try:
        guard.register_app("explicit", app)
        client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
        )
    except BaseException:
        guard.unregister_scheduler(getattr(app.state, "update_scheduler", None))
        raise
    return guard, app, client


def _close_guarded_fixture(
    guard: _SessionGuard,
    app: Any,
    client: httpx.Client,
    monkeypatch,
    counters_before: Mapping[str, int],
    stores_before: set[str],
) -> None:
    """共享 fixture 收口：close 异常/undo/计数/登记分开收集，最后无条件注销。"""
    problems: list[str] = []
    close_error: BaseException | None = None
    try:
        client.close()
    except BaseException as exc:  # noqa: BLE001 - close 失败不得跳过校验
        close_error = exc
    try:
        monkeypatch.undo()
    except BaseException as exc:  # noqa: BLE001 - undo 失败也不得跳过收口
        problems.append(f"monkeypatch.undo 异常：{exc!r}")
    try:
        problems.extend(
            guard.finish_explicit_app(app, counters_before, stores_before, close_error)
        )
    except BaseException as exc:  # noqa: BLE001 - 收口异常不得跳过注销
        problems.append(f"安全收口异常：{exc!r}")
    guard.unregister_scheduler(getattr(app.state, "update_scheduler", None))
    if problems:
        raise AssertionError("W-L 共享 fixture 安全收口失败：" + "；".join(problems))


@pytest.fixture
def api(tmp_path, monkeypatch):
    """进程内 ASGI 应用 + 合法回环客户端（每测试独立临时 DB；W-L 登记/保护）。"""
    guard, app, client = _build_guarded_explicit_app(tmp_path, monkeypatch)
    counters_before = guard.counter_snapshot()
    stores_before = set(guard.observed_store_paths())
    try:
        yield Api(client)
    finally:
        _close_guarded_fixture(
            guard, app, client, monkeypatch, counters_before, stores_before
        )


@pytest.fixture
def raw_client(tmp_path, monkeypatch):
    """不经封装的回环客户端（安全测试需要完全控制请求头；W-L 登记/保护）。"""
    guard, app, client = _build_guarded_explicit_app(tmp_path, monkeypatch)
    counters_before = guard.counter_snapshot()
    stores_before = set(guard.observed_store_paths())
    try:
        yield client
    finally:
        _close_guarded_fixture(
            guard, app, client, monkeypatch, counters_before, stores_before
        )
