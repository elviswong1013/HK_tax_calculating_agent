# -*- coding: utf-8 -*-
"""REQ-2/REQ-7/REQ-9 真正 ASGI prepare × 两纯引擎规范化对等矩阵（002 P1 Red，C 路）。

REQ: REQ-2（结构/封闭/引用/ID）、REQ-7（有效选择与年度域）、REQ-9（规则准入、
下一年与可用性）。本文件为 C 路「rules/neutral normalization/year boundaries」
新增节点，全部为 Red 作者计划；未执行前不是通过证据。

规格锚点（唯一权威：Approved S002/005/006；Annex D 法律行）:
  - 005 §3/§4 封闭字段表（逐层 extra → E_INPUT_EXTRA＋精确定位；八个必填 Year
    容器 missing≠null；manual_issues 可选，省略保持省略/显式[]合法/null→TYPE）、
    §4.1（ID：前缀＋1–31 位）、§4.3–§4.9（引用完整性、
    participant 禁止重复提交婚姻事实、跨年 self 稳定）、§4.10（actual 稀疏、
    nomination/PA-entry 封闭）、§6.1（来源事实）、§7（TF 域：默认键年
    [4/1,次年4/1)；仅 ordinarily_resident_hk／both_parents_dead／marriage／
    living_apart 可用参考日/已声明选择日的单日区间；care_payments.
    home_status_in_force 合法域＝自身 [care_from, care_to)，可完整落在键年
    之前；presence 辅助年＝显式 Year 的 {Y−1,Y,Y+1} 并集且不开放普通 TF 年外
    复制）、§11（下一年假设）、§13（错误定位形状）。
  - 引擎报告 §二 E2（唯一纯结构入口：HTTP 信封校验与两纯引擎调用同一
    规范化权威；每键年域、全部显式年度先验、非法 next 候选 guard
    available/blocked 均拒绝；other-year/undeclared day = E_INPUT_TYPE＋TF 路径）。
  - 复核 §五.6/R3④（Money 尾零/Ratio 未约简等价输入 → 同一 canonical input
    hash；2/2≡1/1 合法所有权；2/6→1/3 规范化）、§五.4（bundle hash 真重绑）。

安全（本文件为测试写 scope 的硬要求；PQA01 技术；仅本 fixture 的局部作用域）:
  - 导入 app.main 之前：设唯一临时 DB（HKTAX_DB_PATH=当前 tmp_path）＋安装真实
    边界拒绝计数（Scheduler.startup/tick/trigger_manual、HttpxModelOutbound.send、
    httpx.HTTPTransport.handle_request、AsyncHTTPTransport.handle_async_request）；
    import 后保护 `app.main._HttpxDocumentOutbound.fetch` 与 `_start_scheduler_threads`，
    并登记隐式（模块级 app）＋显式 app；cached 隐式 store 只允许本批 Temp 根内，
    显式 store 必须属当前函数 Temp；连接前对实际 `RuleStore._path` resolve 后按
    「新 store 属当前函数 Temp／cached 属同批 Temp 根」判定，真实 I/O 前拒绝越界；
    store 归属按**对象身份**登记（路径改写不使当前函数检查被「查无此路径」跳过）。
    经进程内 httpx.ASGITransport 同步桥执行，不执行 lifespan/不启服务/不联网；
    ASGITransport 回环不被阻断。
  - 自检只对 import 前安装的同步可静态调用边界动态触发（startup/tick/trigger_manual/
    send/同步 HTTPTransport）；async transport、文档出站与调度线程 seam 仅由计数非零
    与 identity 恢复核对覆盖，不冒称全部出口已动态核验（纯文件路径不在本自检范围）。
  - 显式 app 的 `app.state.update_scheduler`/`model_outbound` 接缝替换为受控合法
    guard（availability mode=ok；仅有限节点按期间覆盖为拒绝式）与拒绝式计数替身；
    每用例在目标断言前断言全零，退出时本地替身 counter 与接缝 identity 并入收口。
  - 外层 try 从首次 patch 之前包裹 setup＋yield；close 异常/monkeypatch.undo/
    本地替身收口/verify 失败均独立收集且不得跳过 restore；安装期补丁在 verify
    时按**安装替身 identity** 逐项核验（被替换/丢失即暴露）；四个故障模式
    （setup/close/undo/verify，另加本地收口）各由独立节点注入真实故障驱动本
    fixture 本身取证。全部补丁为文件本地 fixture 作用域，
    退出按栈还原（绝不永久 setattr/污染类）；不以 env 是否存在作为证据；不全局
    patch ASGI Client/事件循环/socket；纯引擎路径不构造 app。
  - 首次 import 的隐式 app/store 允许复用（同一隔离批次 Temp 内），不 reload/改写。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - 同一 v2 payload 经 POST /api/v1/calc/prepare（真实 ASGI，合法 CSRF＋
    If-Match，available guard）与对应纯引擎入口（calculate_salaries_tax／
    compare_personal_assessment，005 §2.1 显式 AvailabilityContext）必须得到
    完全相同且精确定位的接受/拒绝（code＋field）。
  - 本文件对 app.* 延迟导入；Red 失败原因＝行为差异，不是收集期失败。
"""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
import pytest

_BASE_ORIGIN = "http://127.0.0.1:8000"
_YEAR = "2025_26"
_NEXT = "2026_27"

_V2_YEAR_REQUIRED_KEYS = (
    "participants",
    "subject_facts",
    "household",
    "relations",
    "financials",
    "care_payments",
    "claims",
    "assessment",
)

_V2_YEAR_OPTIONAL_KEYS = ("manual_issues",)


# ---------------------------------------------------------------------------
# 仅测试本地接线：回环客户端＋受控合法本地 guard＋拒绝式出站计数
# ---------------------------------------------------------------------------


class _Loopback:
    """合法回环 Host/Origin ＋ CSRF 能力（Annex C C5.2/C5.3 既有契约）。"""

    def __init__(self, app: Any) -> None:
        self._client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=_BASE_ORIGIN
        )
        self._csrf: str | None = None

    def _token(self) -> str:
        if self._csrf is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf = token
        return self._csrf

    def get(self, path: str):
        return self._client.get(path)

    def post(self, path: str, *, json_body: dict | None = None, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = _BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json_body, headers=h)

    def close(self) -> None:
        self._client.close()


class _ControlledScheduler:
    """受控合法本地可用性 guard；调度服务调用一律计数＋抛错（必须为 0）。

    `modes` 仅本文件测试内使用：按期间覆盖 guard 判定（默认全 ok，合法本地
    availability 保持不变）；用于「拒绝式 guard 下非法输入仍早退」的有界观测。
    """

    def __init__(self) -> None:
        self.startup_calls = 0
        self.tick_calls = 0
        self.trigger_calls = 0
        self.periods: list[str] = []
        self.modes: dict[str, str] = {}

    def availability(self, period: str) -> dict:
        self.periods.append(period)
        return {"mode": self.modes.get(period, "ok"), "notice": None}

    def startup(self) -> None:
        self.startup_calls += 1
        raise AssertionError("调度器 startup 被调用（测试不得启动服务/首启即查）")

    def tick(self) -> None:
        self.tick_calls += 1
        raise AssertionError("调度器 tick 被调用（测试不得启动周期出站）")

    def trigger_manual(self) -> None:
        self.trigger_calls += 1
        raise AssertionError("scheduler.trigger_manual 被调用（非目标出站）")

    def status(self) -> None:
        self.trigger_calls += 1
        raise AssertionError("scheduler.status 被调用（非目标出站）")


class _RejectOutbound:
    """模型出站拒绝式替身：任何调用计数并抛错。"""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("model outbound 被调用（REQ-17：测试不得出站）")

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("model outbound 被调用（REQ-17：测试不得出站）")


_TAX_TYPE_BY_KIND = {"salary": "salaries_tax", "pa": "personal_assessment"}

_OBSERVED_STORES: list[dict[str, Any]] = []


class _SafetyCounters:
    """真实边界拒绝计数（局部实例；不挂生产类的持久属性）。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {
            "scheduler_startup": 0,
            "scheduler_tick": 0,
            "scheduler_trigger": 0,
            "model_send": 0,
            "http_transport": 0,
            "async_http_transport": 0,
            "document_fetch": 0,
            "scheduler_threads": 0,
        }

    def bump(self, name: str) -> None:
        self.values[name] += 1


class _SafetyLayer:
    """文件本地安全层（仅本 fixture 使用；独立恢复栈；绝不永久改生产类）。

    顺序（PQA01）：import app.main 前安装真实边界拒绝计数＋包装 RuleStore
    观察实际路径 → import 后保护文档出站/线程 seam 并登记隐式+显式 app →
    退出先独立收集（close 异常/计数/实际 store/自检）再按栈还原。
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = Path(tmp_path)
        self.batch_root = self.tmp_path.parent
        self.counters = _SafetyCounters()
        self._patches: list[tuple[Any, str, Any, bool, Any]] = []
        self._store_events: list[str] = []
        self._store_owners: dict[int, tuple[Any, Path]] = {}
        self._connect_violations: list[str] = []
        self.self_check_error: str | None = None
        self.apps: dict[str, dict[str, Any]] = {}

    # —— 安装/保护 ——

    def _patch(self, owner: Any, attr: str, value: Any) -> None:
        self._patches.append(
            (owner, attr, getattr(owner, attr, None), hasattr(owner, attr), value)
        )
        setattr(owner, attr, value)

    def install(self) -> None:
        import httpx as httpx_module

        from app.ai import adapters as adapters_module
        from app.rules.store import RuleStore
        from app.updater import scheduler as scheduler_module

        counters = self.counters

        def _refuse_startup(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("scheduler_startup")
            raise AssertionError("真实 Scheduler.startup 被调用（测试不得启动调度）")

        def _refuse_tick(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("scheduler_tick")
            raise AssertionError("真实 Scheduler.tick 被调用（测试不得周期出站）")

        def _refuse_trigger(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("scheduler_trigger")
            raise AssertionError("真实 Scheduler.trigger_manual 被调用（不得出站）")

        def _refuse_send(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("model_send")
            raise AssertionError("真实 model outbound 被调用（REQ-17：不得出站）")

        def _refuse_http(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("http_transport")
            raise AssertionError("真实 httpx.HTTPTransport 被调用（不得真实网络）")

        async def _refuse_async_http(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("async_http_transport")
            raise AssertionError("真实 httpx.AsyncHTTPTransport 被调用（不得真实网络）")

        self._patch(scheduler_module.Scheduler, "startup", _refuse_startup)
        self._patch(scheduler_module.Scheduler, "tick", _refuse_tick)
        self._patch(scheduler_module.Scheduler, "trigger_manual", _refuse_trigger)
        self._patch(adapters_module.HttpxModelOutbound, "send", _refuse_send)
        self._patch(httpx_module.HTTPTransport, "handle_request", _refuse_http)
        self._patch(
            httpx_module.AsyncHTTPTransport, "handle_async_request", _refuse_async_http
        )

        layer = self
        original_init = RuleStore.__init__
        original_connect = RuleStore._connect

        def _observed_init(store_self: Any, db_path: Any) -> None:
            requested = Path(db_path).resolve()
            layer._store_events.append(f"init-start:{requested}")
            assert requested.parent == layer.tmp_path, (
                f"RuleStore 请求路径必须在当前函数 Temp 目录（import 前已设 env）："
                f"{requested}"
            )
            original_init(store_self, db_path)
            resolved = Path(store_self._path).resolve()
            # 归属按 store **对象身份**（id＋强引用）登记：构造后改写 `_path`
            # 到同批他函数目录仍命中本批创建的 store，不被「按路径查无记录」
            # 静默跳过当前函数检查（ora-17 CQA-01②）。
            layer._store_owners[id(store_self)] = (store_self, layer.tmp_path)
            _OBSERVED_STORES.append(
                {
                    "path": str(resolved),
                    "first_test": str(layer.tmp_path),
                    "store": store_self,
                }
            )
            layer._store_events.append(f"init-done:{resolved}")

        def _observed_connect(store_self: Any) -> Any:
            resolved = Path(store_self._path).resolve()
            layer._store_events.append(f"connect:{resolved}")
            if (
                id(store_self) in layer._store_owners
                and resolved.parent != layer.tmp_path
            ):
                layer._connect_violations.append(str(resolved))
                raise AssertionError(
                    "RuleStore._connect 越出当前函数 Temp（新 store 当前作用域；"
                    f"拒绝于真实 I/O 之前）：{resolved}"
                )
            if layer.batch_root not in resolved.parents:
                layer._connect_violations.append(str(resolved))
                raise AssertionError(
                    f"RuleStore._connect 越出本批 Temp 根（拒绝于真实 I/O 之前）："
                    f"{resolved}"
                )
            return original_connect(store_self)

        self._patch(RuleStore, "__init__", _observed_init)
        self._patch(RuleStore, "_connect", _observed_connect)
        self._self_check()

    def _self_check(self) -> None:
        """独立自检：对 import 前安装的**同步可静态调用**边界（Scheduler 的
        startup/tick/trigger_manual、HttpxModelOutbound.send、httpx 同步
        HTTPTransport）确认拒绝且计数增长；自检后计数清零，与真实路径的零计数
        证据分开记录。async transport、文档出站与调度线程 seam 不经事件循环/
        服务动态触发，仅由计数非零检查与 restore 的 identity 核对覆盖——本自检
        不冒称全部出口已动态核验，也不覆盖纯文件路径（N/U/S 不在此自检范围）。"""
        import httpx as httpx_module

        from app.ai.adapters import HttpxModelOutbound
        from app.updater.scheduler import Scheduler

        probes = (
            ("scheduler_startup", lambda: Scheduler.startup(object())),
            ("scheduler_tick", lambda: Scheduler.tick(object())),
            ("scheduler_trigger", lambda: Scheduler.trigger_manual(object())),
            ("model_send", lambda: HttpxModelOutbound.send(object())),
            (
                "http_transport",
                lambda: httpx_module.HTTPTransport.handle_request(object(), object()),
            ),
        )
        problems: list[str] = []
        for counter_key, probe in probes:
            try:
                probe()
            except AssertionError:
                pass
            else:
                problems.append(f"{counter_key} 未拒绝")
            if self.counters.values[counter_key] != 1:
                problems.append(
                    f"{counter_key} 自检计数未增长：{self.counters.values[counter_key]}"
                )
        for key in self.counters.values:
            self.counters.values[key] = 0
        self.self_check_error = "; ".join(problems) if problems else None

    def harden(self) -> None:
        """import app.main 之后：保护文档出站/调度线程 seam；登记隐式 app。"""
        import app.main as main_module

        counters = self.counters

        def _refuse_fetch(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("document_fetch")
            raise AssertionError("真实 _HttpxDocumentOutbound.fetch 被调用（不得出站）")

        def _refuse_threads(_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.bump("scheduler_threads")
            raise AssertionError("真实 _start_scheduler_threads 被调用（不得启动线程）")

        self._patch(main_module._HttpxDocumentOutbound, "fetch", _refuse_fetch)
        self._patch(main_module, "_start_scheduler_threads", _refuse_threads)

        implicit = getattr(main_module, "app", None)
        if implicit is not None:
            self.register_app("implicit", implicit)

    def register_app(self, label: str, app: Any) -> None:
        store = getattr(app.state, "store", None)
        assert store is not None, f"{label} app 缺 store（不得无 store 构造）"
        resolved = Path(store._path).resolve()
        self.apps[label] = {"app_id": str(id(app)), "store_path": str(resolved)}
        self._store_events.append(f"app-{label}:{resolved}")
        if label == "explicit":
            assert resolved.parent == self.tmp_path, (
                f"显式 app store 必须在当前函数 Temp 目录：{resolved}"
            )
        else:
            assert self.batch_root in resolved.parents, (
                f"隐式（cached）app store 必须在本批 Temp 根内：{resolved}"
            )

    # —— 退出：独立校验＋还原 ——

    def verify(self, close_error: BaseException | None) -> list[str]:
        problems: list[str] = []
        if close_error is not None:
            problems.append(f"client.close 异常：{close_error!r}")
        bad = {key: value for key, value in self.counters.values.items() if value}
        if bad:
            problems.append(f"真实边界调用计数非零：{bad!r}")
        # 安装 identity（ora-17 CQA-01③）：逐补丁比较当前属性与安装替身——替身被
        # 替换/丢失即显式暴露，不得只靠「还原后回到原对象」倒推安装期完整。
        for owner, attr, _original, _had, installed in self._patches:
            if getattr(owner, attr, None) is not installed:
                name = getattr(owner, "__name__", repr(owner))
                problems.append(f"安装替身已被替换/丢失：{name}.{attr}")
        if self._connect_violations:
            problems.append(
                "连接前拒绝记录非空（越界路径已在真实 I/O 前拦截）："
                f"{self._connect_violations!r}"
            )
        for record in list(_OBSERVED_STORES):
            store = record.get("store")
            # 按 store **当前** `_path` 复核（构造后改写路径不得绕过收口检查；
            # 无对象记录时回退登记路径）。
            resolved = (
                Path(store._path).resolve() if store is not None else Path(record["path"])
            )
            if Path(record["first_test"]) == self.tmp_path:
                if resolved.parent != self.tmp_path:
                    problems.append(
                        f"本次 store 不在当前 Temp：{resolved}"
                        f"（store 事件：{self._store_events!r}）"
                    )
            if self.batch_root not in resolved.parents:
                problems.append(
                    f"store 不在本批 Temp 根内：{resolved}"
                    f"（store 事件：{self._store_events!r}）"
                )
        if self.self_check_error:
            problems.append(f"安全自检失败：{self.self_check_error}")
        return problems

    def restore(self) -> list[str]:
        problems: list[str] = []
        for owner, attr, original, had, _installed in reversed(self._patches):
            try:
                if had:
                    setattr(owner, attr, original)
                else:
                    delattr(owner, attr)
            except Exception as exc:  # noqa: BLE001 - 恢复失败必须显式暴露
                name = getattr(owner, "__name__", repr(owner))
                problems.append(f"恢复 {name}.{attr} 失败：{exc!r}")
        # 还原后按原对象逐一核 identity（类方法本就在 __dict__，不能以存在性判断）。
        for owner, attr, original, had, _installed in self._patches:
            name = getattr(owner, "__name__", repr(owner))
            current = getattr(owner, attr, None)
            if had and current is not original:
                problems.append(f"还原后 {name}.{attr} 与原对象不同")
            if (not had) and current is not None:
                problems.append(f"还原后 {name}.{attr} 仍存在（原为不存在）")
        return problems


class _ParityHarness:
    def __init__(self, client: _Loopback, app: Any, scheduler: _ControlledScheduler,
                 outbound: _RejectOutbound, safety: _SafetyLayer) -> None:
        self.client = client
        self.app = app
        self.scheduler = scheduler
        self.outbound = outbound
        self.safety = safety

    def fresh_if_match(self) -> dict[str, str]:
        r = self.client.get("/api/v1/meta/version")
        assert r.status_code == 200, r.text
        payload = r.json()
        epoch = int(payload["session_epoch"])
        revision = int(payload["input_revision"])
        return {"If-Match": f'"edit-{epoch}-{revision}"'}

    def prepare(self, kind: str, body: dict):
        """真实 ASGI prepare 信封（S002 §4.1：tax_type／schema_version／input）。"""
        return self.client.post(
            "/api/v1/calc/prepare",
            json_body={
                "tax_type": _TAX_TYPE_BY_KIND[kind],
                "schema_version": "2.0.0",
                "input": body,
            },
            headers=self.fresh_if_match(),
        )

    def assert_no_service(self) -> None:
        assert self.app.state.update_scheduler is self.scheduler, (
            "受控 guard 接缝被替换（不得使用真实 scheduler）"
        )
        assert self.scheduler.startup_calls == 0, self.scheduler.startup_calls
        assert self.scheduler.tick_calls == 0, self.scheduler.tick_calls
        assert self.scheduler.trigger_calls == 0, self.scheduler.trigger_calls
        assert self.outbound.calls == 0, self.outbound.calls
        bad = {key: value for key, value in self.safety.counters.values.items() if value}
        assert not bad, f"真实边界调用计数非零：{bad!r}"
        assert self.safety.self_check_error is None, self.safety.self_check_error


@pytest.fixture
def parity(tmp_path, monkeypatch):
    """唯一临时 DB（import app.main 前）→ 真实边界安全层 → 显式 app＋受控
    guard/出站接缝 → 进程内回环客户端。外层 try 从**首次 patch 之前**（install
    之前）包裹 setup＋yield；finally 逐项独立收集（close 异常/monkeypatch.undo/
    本地替身收口/安全层 verify/自检）后无条件 restore——任一收集异常都不得跳过
    还原；四个故障模式（setup/close/undo/verify，另加本地替身收口异常）各由独立
    节点以真实故障注入驱动本 fixture 本身取证（不自写 finally）。"""
    db = tmp_path / "rules.db"
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    safety = _SafetyLayer(tmp_path)
    app: Any = None
    scheduler: _ControlledScheduler | None = None
    outbound: _RejectOutbound | None = None
    client: _Loopback | None = None
    pending: BaseException | None = None
    try:
        safety.install()
        from app.main import create_app

        app = create_app()
        safety.harden()
        safety.register_app("explicit", app)
        scheduler = _ControlledScheduler()
        outbound = _RejectOutbound()
        app.state.update_scheduler = scheduler
        app.state.model_outbound = outbound
        client = _Loopback(app)
        yield _ParityHarness(client, app, scheduler, outbound, safety)
    except BaseException as exc:
        pending = exc
        raise
    finally:
        close_error: BaseException | None = None
        if client is not None:
            try:
                client.close()
            except BaseException as exc:  # noqa: BLE001 - close 失败不得跳过校验/还原
                close_error = exc
        problems: list[str] = []
        try:
            monkeypatch.undo()  # 先恢复测试自身注入，再还原安全层
        except BaseException as exc:  # noqa: BLE001 - undo 失败也不得跳过 restore
            problems.append(f"monkeypatch.undo 异常：{exc!r}")
        try:
            problems.extend(_local_service_problems(app, scheduler, outbound))
        except BaseException as exc:  # noqa: BLE001 - 本地收口检查异常同样不跳 restore
            problems.append(f"本地替身收口异常：{exc!r}")
        try:
            problems.extend(safety.verify(close_error))
        except BaseException as exc:  # noqa: BLE001 - verify 失败必须暴露且不阻断还原
            problems.append(f"安全层 verify 异常：{exc!r}")
        try:
            problems.extend(safety.restore())
        except BaseException as exc:  # noqa: BLE001 - restore 失败必须显式暴露
            problems.append(f"安全层 restore 异常：{exc!r}")
        if problems:
            message = "Q 安全层校验失败：" + " | ".join(problems)
            if pending is not None:
                message += f"（测试本身亦失败：{type(pending).__name__}: {pending}）"
            raise AssertionError(message) from None


def _local_service_problems(
    app: Any,
    scheduler: _ControlledScheduler | None,
    outbound: _RejectOutbound | None,
) -> list[str]:
    """本地替身收口（独立于安全层计数）：受控接缝 identity 未被替换、替身
    counter 一律为零（与 `assert_no_service` 的用例内断言互为独立证据）。"""
    problems: list[str] = []
    if scheduler is not None:
        if app is None or app.state.update_scheduler is not scheduler:
            problems.append("受控 availability 接缝被替换（非受控 scheduler）")
        if scheduler.startup_calls or scheduler.tick_calls or scheduler.trigger_calls:
            problems.append(
                "本地调度替身计数非零："
                f"startup={scheduler.startup_calls} tick={scheduler.tick_calls} "
                f"trigger={scheduler.trigger_calls}"
            )
    if outbound is not None:
        if app is None or app.state.model_outbound is not outbound:
            problems.append("受控出站接缝被替换（非受控 outbound）")
        if outbound.calls:
            problems.append(f"本地出站替身计数非零：{outbound.calls}")
    return problems


def test_002_safety_layer_setup_failure_restores_boundaries(
    tmp_path, monkeypatch
) -> None:
    """故障注入（CQA-01④）：驱动**真实 parity fixture 生成器**（`__wrapped__`；
    pytest 9 fixture 对象不可直接调用），在首次 patch 后、yield 前注入 setup 失败
    （install 完成后抛错）——外层 finally 仍逐项收集并还原全部补丁；因收口无事
    件，注入的 RuntimeError 原样传播（未被收口 AssertionError 替换＝还原无问题
    的间接证据）。不写控制自己的 finally（ora-17：自写 finally 不满足 Acceptance）。"""
    import app.updater.scheduler as scheduler_module
    from app.rules.store import RuleStore

    original_startup = scheduler_module.Scheduler.startup
    original_connect = RuleStore._connect
    original_install = _SafetyLayer.install

    def _fail_after_install(self: Any) -> None:
        original_install(self)
        raise RuntimeError("injected setup failure after install patches")

    monkeypatch.setattr(_SafetyLayer, "install", _fail_after_install)
    gen = parity.__wrapped__(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="injected setup failure"):
        next(gen)
    assert scheduler_module.Scheduler.startup is original_startup
    assert RuleStore._connect is original_connect
    assert _SafetyLayer.install is original_install


def test_002_safety_layer_close_error_collected_before_restore(
    tmp_path, monkeypatch
) -> None:
    """故障注入（CQA-01④）：驱动真实 parity fixture，让 `client.close()` **真实
    抛错**（非仅向 verify 传异常对象）——finally 独立收集 close 异常（收口
    AssertionError 含注入消息），restore 仍执行并还原 identity。"""
    import app.updater.scheduler as scheduler_module
    from app.rules.store import RuleStore

    original_startup = scheduler_module.Scheduler.startup
    original_connect = RuleStore._connect
    gen = parity.__wrapped__(tmp_path, monkeypatch)
    harness = next(gen)
    real_close = harness.client.close

    def _failing_close() -> None:
        raise RuntimeError("injected client.close failure")

    harness.client.close = _failing_close
    try:
        with pytest.raises(AssertionError, match="injected client.close failure"):
            next(gen)
        assert scheduler_module.Scheduler.startup is original_startup
        assert RuleStore._connect is original_connect
    finally:
        real_close()


def test_002_safety_layer_undo_failure_still_restores_boundaries(
    tmp_path, monkeypatch
) -> None:
    """故障注入（CQA-01④）：monkeypatch.undo 首次调用抛错（替身注入）——收口独立
    记录该异常，restore 仍执行并还原 identity（undo 失败不得跳过还原）。"""
    import app.updater.scheduler as scheduler_module
    from app.rules.store import RuleStore

    original_startup = scheduler_module.Scheduler.startup
    original_connect = RuleStore._connect

    class _UndoFault:
        def __init__(self, real: Any) -> None:
            self._real = real
            self.calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def undo(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected monkeypatch.undo failure")
            self._real.undo()

    gen = parity.__wrapped__(tmp_path, _UndoFault(monkeypatch))
    next(gen)  # setup 完成；yield 后收口的 undo 由注入替身抛错
    with pytest.raises(AssertionError, match="injected monkeypatch.undo failure"):
        next(gen)
    assert scheduler_module.Scheduler.startup is original_startup
    assert RuleStore._connect is original_connect


def test_002_safety_layer_verify_failure_still_restores_boundaries(
    tmp_path, monkeypatch
) -> None:
    """故障注入（CQA-01④）：safety.verify 抛错——收口独立记录该异常，restore 仍
    执行并还原 identity（verify 失败不得跳过还原）。"""
    import app.updater.scheduler as scheduler_module
    from app.rules.store import RuleStore

    original_startup = scheduler_module.Scheduler.startup
    original_connect = RuleStore._connect
    gen = parity.__wrapped__(tmp_path, monkeypatch)
    harness = next(gen)

    def _failing_verify(close_error: Any) -> list[str]:
        raise RuntimeError("injected safety verify failure")

    harness.safety.verify = _failing_verify
    with pytest.raises(AssertionError, match="injected safety verify failure"):
        next(gen)
    assert scheduler_module.Scheduler.startup is original_startup
    assert RuleStore._connect is original_connect


def test_002_safety_layer_local_check_failure_still_restores_boundaries(
    tmp_path, monkeypatch
) -> None:
    """故障注入（CQA-01①）：本地替身收口检查抛错——外层 finally 对本地收口单独
    包裹并独立记录该异常（ora-17：`_local_service_problems` 无保护可跳过 restore），
    restore 仍执行并还原 identity。"""
    import app.updater.scheduler as scheduler_module
    from app.rules.store import RuleStore

    original_startup = scheduler_module.Scheduler.startup
    original_connect = RuleStore._connect

    def _failing_local_check(
        app: Any, scheduler: Any, outbound: Any
    ) -> list[str]:
        raise RuntimeError("injected local check failure")

    fault_patch = pytest.MonkeyPatch()
    fault_patch.setitem(globals(), "_local_service_problems", _failing_local_check)
    try:
        gen = parity.__wrapped__(tmp_path, monkeypatch)
        next(gen)
        with pytest.raises(AssertionError, match="injected local check failure"):
            next(gen)
        assert scheduler_module.Scheduler.startup is original_startup
        assert RuleStore._connect is original_connect
    finally:
        fault_patch.undo()


def test_002_safety_layer_connect_refuses_mutated_store_path(
    tmp_path, monkeypatch
) -> None:
    """狭窄控制（CQA-01②）：新 store 在本函数 Temp 创建后把 `_path` 改到**同批
    他函数目录**——归属按 store 对象身份（非路径字符串）命中当前函数检查，连接前
    拒绝且不触真实 I/O；拒绝记录入 `_connect_violations`。"""
    from app.rules.store import RuleStore

    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    mutated = tmp_path.parent / (tmp_path.name + "-mutated")
    safety = _SafetyLayer(tmp_path)
    try:
        safety.install()
        store = RuleStore(tmp_path / "rules.db")
        store._path = mutated / "rules.db"
        with pytest.raises(AssertionError, match="越出当前函数 Temp"):
            store._connect()
        assert not (mutated / "rules.db").exists(), "拒绝须发生在真实 I/O 之前"
        assert safety._connect_violations == [str((mutated / "rules.db").resolve())], (
            safety._connect_violations
        )
    finally:
        problems = safety.restore()
    assert problems == [], problems


def test_002_safety_layer_verify_detects_replaced_substitute(
    tmp_path, monkeypatch
) -> None:
    """狭窄控制（CQA-01③）：安装后的真实补丁被替换 → verify 按安装替身 identity
    检出（不得静默通过）；restore 仍按原对象还原。"""
    import app.updater.scheduler as scheduler_module

    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    original_startup = scheduler_module.Scheduler.startup
    safety = _SafetyLayer(tmp_path)
    safety.install()

    def _rogue(*args: Any, **kwargs: Any) -> None:
        return None

    setattr(scheduler_module.Scheduler, "startup", _rogue)
    try:
        problems = safety.verify(None)
    finally:
        restore_problems = safety.restore()
    assert any("安装替身" in problem for problem in problems), problems
    assert restore_problems == [], restore_problems
    assert scheduler_module.Scheduler.startup is original_startup


def _error_code_field(payload: dict) -> tuple[Any, Any]:
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状（005 §13），实际：{payload!r}"
    return err.get("code"), err.get("field")


def _http_reject(harness: _ParityHarness, kind: str, body: dict, expected: tuple, label: str):
    r = harness.prepare(kind, body)
    assert r.status_code == 422, (
        f"{label}：须结构化 422（不得 201/409/500 遮蔽），实际 {r.status_code}："
        f"{r.text[:240]!r}"
    )
    observed = _error_code_field(r.json())
    assert observed == expected, (
        f"{label}：ASGI prepare 期望 {expected!r}，实际 {observed!r}"
    )
    assert "prepared_id" not in r.json(), f"{label}：拒绝路径不得签发 prepared_id"
    return r


# ---------------------------------------------------------------------------
# 纯引擎入口（目标年 ctx 由实际束内容重算 hash；不构造 app）
# ---------------------------------------------------------------------------


def _bundle() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return INITIAL_BUNDLE_CONTENT


def _pure_availability(facts: dict, bundle: dict) -> dict:
    from app.config import ENGINE_VERSION
    from app.core.hashing import bundle_content_hash

    year = facts["year_of_assessment"]
    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    matrices = bundle["data"]["allowances"]
    return {
        "binding": {
            "bundle_id": "b-002-parity",
            "bundle_hash": bundle_content_hash(bundle),
            "rules_schema_version": bundle["rules_schema_version"],
            "engine_version": ENGINE_VERSION,
        },
        "current": {"year": year, "status": "available", "reason_codes": []},
        "next": {
            "year": following,
            "status": "available" if following in matrices else "missing_rules",
            "reason_codes": [],
        },
    }


def _run_salary(facts: dict) -> dict:
    from app.engines.salaries import calculate_salaries_tax

    bundle = _bundle()
    return calculate_salaries_tax(
        facts, bundle, availability=_pure_availability(facts, bundle)
    )


def _run_pa(facts: dict) -> dict:
    from app.engines.personal_assessment import compare_personal_assessment

    bundle = _bundle()
    return compare_personal_assessment(
        facts, bundle, availability=_pure_availability(facts, bundle)
    )


def _observe_pure(kind: str, body: dict) -> tuple[Any, Any]:
    """纯引擎观测（AppError→(code,field)；裸异常→(异常类名,None)；接受→(None,None)）。"""
    from app.core.errors import AppError

    try:
        if kind == "salary":
            _run_salary(body)
        else:
            _run_pa(body)
    except AppError as exc:
        return exc.code, exc.field
    except Exception as exc:  # noqa: BLE001 - 裸异常必须显式暴露为 Red，不得吞掉
        return type(exc).__name__, None
    return None, None


_ENTRIES = ("http", "pure")


def _assert_entry_rejection(
    harness: _ParityHarness, kind: str, body: dict, expected: tuple, label: str,
    entry: str,
) -> None:
    """按 entry 独立断言（HTTP 与纯引擎各自成独立节点，避免先失败遮蔽另一侧；
    两侧都须给出同一精确 (code, field)，但不合并为同一断言链）。"""
    if entry == "http":
        _http_reject(harness, kind, body, expected, label + "（ASGI）")
        return
    assert entry == "pure", entry
    observed = _observe_pure(kind, body)
    assert observed == expected, (
        f"{label}：纯引擎（{kind}）期望 {expected!r}，实际 {observed!r}"
    )


def _assert_control_201(
    harness: _ParityHarness, kind: str, body: dict, label: str,
    entry: str | None = None,
) -> None:
    """合法对照：同一 payload 去掉 mutation 后按 entry 分派——http 仅断言 ASGI
    201（CSRF/If-Match/guard 齐备；不得 409 或规则早拒），pure 仅断言对应纯引擎
    可计算；entry=None 两者兼验（非 entry 参数化节点）。每个 entry 节点独立断言，
    避免一侧先失败遮蔽另一侧目标。"""
    if entry in (None, "http"):
        r = harness.prepare(kind, body)
        assert r.status_code == 201, (
            f"{label}：合法对照须 201（CSRF/If-Match/guard 齐备；不得 409 或规则早拒），"
            f"实际 {r.status_code}：{r.text[:240]!r}"
        )
    if entry in (None, "pure"):
        from app.core.errors import AppError

        try:
            result = _run_salary(body) if kind == "salary" else _run_pa(body)
        except AppError as exc:
            raise AssertionError(
                f"{label}：合法对照纯引擎须可计算，实际拒绝 "
                f"{exc.code!r}/{exc.field!r}"
            ) from exc
        assert isinstance(result, dict) and result.get("status") in ("complete", "partial"), (
            f"{label}：合法对照纯引擎须可计算，实际：{result!r}"
        )


# ---------------------------------------------------------------------------
# 输入夹具（Annex 005 §5.2/§5.3 形状；仅测试本地转写）
# ---------------------------------------------------------------------------


def _tf(frm: str, to: str, value: Any) -> list[dict]:
    return [{"from": frm, "to": to, "value": value}]


def _finance_salary() -> dict:
    return {
        "employment_income_nai": "240000",
        "mpf_mandatory_contributions": "9000",
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
    }


def _salary_year() -> dict:
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {},
        "household": {
            "marriage": _tf("2025-04-01", "2026-04-01", "unmarried"),
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "financials": {"p1": _finance_salary()},
        "care_payments": [],
        "claims": [],
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
        "provisional_paid": "0",
    }


def _next_year_like(current: dict, window: tuple[str, str]) -> dict:
    year = copy.deepcopy(current)
    year["household"]["marriage"] = _tf(window[0], window[1], "unmarried")
    return year


def _salary_body(*, with_next: bool = False) -> dict:
    years = {_YEAR: _salary_year()}
    if with_next:
        years[_NEXT] = _next_year_like(_salary_year(), ("2026-04-01", "2027-04-01"))
    return {
        "year_of_assessment": _YEAR,
        "subjects": [{"subject_id": "p1"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def _pa_year(year: str = _YEAR) -> dict:
    """Annex 005 §5.2 有效完整请求（逐字转写；hypothetical 两候选；按年参数化：
    键年窗口＋该年参考日，2025_26 与原文逐字一致）。"""
    start = int(year[:4])
    w_from, w_to = f"{start}-04-01", f"{start + 1}-04-01"
    ref_from, ref_to = f"{start + 1}-06-01", f"{start + 1}-06-02"
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {
            "p1": {
                "ordinarily_resident_hk": _tf(ref_from, ref_to, True),
            }
        },
        "household": {
            "marriage": [
                {"from": w_from, "to": w_to, "value": "unmarried"},
                {"from": ref_from, "to": ref_to, "value": "unmarried"},
            ],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "financials": {
            "p1": {
                "employment_income_nai": "250000",
                "mpf_mandatory_contributions": "0",
                "has_assessable_salary_income": True,
                "has_assessable_property_income": True,
                "has_assessable_business_income": False,
                "has_income_assessable_under_ordinance": True,
                "source_classification": {
                    "salary": "confirmed_in_scope",
                    "property": "confirmed_in_scope",
                    "business": "confirmed_in_scope",
                },
                "properties": [
                    {
                        "property_id": "h1",
                        "share": "1/1",
                        "rent_received": "120000",
                        "rates_paid_by_owner": {
                            "amount": "0",
                            "agreed": False,
                            "actually_paid": False,
                        },
                        "irrecoverable_rent": "0",
                        "deposit_offsets": "0",
                        "mortgage_interest": "0",
                    }
                ],
                "businesses": [],
                "deduction_items": [],
            }
        },
        "care_payments": [],
        "claims": [],
        "assessment": {
            "reference_date": ref_from,
            "basis": "hypothetical",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "pa": {"p1": {"mode": "none"}},
                "history": "ordinary",
            },
            "candidates": ["no_pa", "pa_self"],
            "selection": "best",
        },
        "manual_issues": [],
        "provisional_paid": "0",
    }


def _pa_body(*, year: str = _YEAR, with_next: bool = False) -> dict:
    years = {year: _pa_year(year)}
    if with_next:
        nxt = f"{int(year[:4]) + 1}_{(int(year[:4]) + 2) % 100:02d}"
        years[nxt] = _pa_year(nxt)
    return {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


# ---------------------------------------------------------------------------
# 合法基线：真实 ASGI prepare（CSRF＋If-Match＋available guard）对两纯引擎
# ---------------------------------------------------------------------------


def test_002_asgi_prepare_vs_pure_legal_baseline(parity) -> None:
    """合法 baseline：真实 ASGI prepare 须 201（CSRF/If-Match/受控合法 guard
    齐备；不得 409 或规则早拒遮蔽目标），同 payload 两纯引擎均可计算；
    调度/出站 counter=0。"""
    salary_body = _salary_body()
    r_salary = parity.prepare("salary", copy.deepcopy(salary_body))
    assert r_salary.status_code == 201, (
        f"合法薪俸 baseline 须 201（不得 409/规则早拒），实际 {r_salary.status_code}："
        f"{r_salary.text[:240]!r}"
    )
    payload = r_salary.json()
    assert payload.get("status") == "prepared", payload
    assert payload.get("prepared_id"), payload
    assert payload.get("input_hash"), payload
    salary_result = _run_salary(copy.deepcopy(salary_body))
    assert salary_result["amounts"]["final_after_reduction"] == "940", (
        f"合法 baseline 纯引擎 final 940（PAM39 Q1），实际：{salary_result!r}"
    )

    pa_body = _pa_body()
    r_pa = parity.prepare("pa", copy.deepcopy(pa_body))
    assert r_pa.status_code == 201, (
        f"合法 PA baseline 须 201（不得 409/规则早拒），实际 {r_pa.status_code}："
        f"{r_pa.text[:240]!r}"
    )
    pa_result = _run_pa(copy.deepcopy(pa_body))
    assert pa_result.get("status") in ("complete", "partial"), pa_result
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# 逐层封闭键负例矩阵（one extra each；exact E_INPUT_EXTRA＋path）
# ---------------------------------------------------------------------------

_PROBE = "parity_probe"


def _setup_relation(body: dict) -> None:
    year = body["years"][_YEAR]
    body["subjects"].append({"subject_id": "d1", "birth_date": "2015-01-01"})
    year["relations"] = [
        {
            "relation_id": "r1",
            "actor_id": "p1",
            "subject_id": "d1",
            "kinship": {
                "kind": "child",
                "basis": "birth",
                "during": _tf("2025-04-01", "2026-04-01", True),
            },
            "maintained": _tf("2025-04-01", "2026-04-01", True),
        }
    ]


def _setup_child_claim(body: dict) -> None:
    year = body["years"][_YEAR]
    _setup_relation(body)
    year["subject_facts"]["d1"] = {
        "unmarried": _tf("2025-04-01", "2026-04-01", True)
    }
    year["claims"] = [
        {"claim_id": "c1", "kind": "child", "claimant_id": "p1", "subject_id": "d1"}
    ]


def _setup_property(body: dict) -> None:
    year = body["years"][_YEAR]
    year["financials"]["p1"]["properties"] = [
        {
            "property_id": "h1",
            "share": "1/1",
            "rent_received": "120000",
            "rates_paid_by_owner": {"amount": "0", "agreed": False, "actually_paid": False},
            "irrecoverable_rent": "0",
            "deposit_offsets": "0",
            "mortgage_interest": "0",
        }
    ]


def _setup_business(body: dict) -> None:
    year = body["years"][_YEAR]
    year["financials"]["p1"]["businesses"] = [
        {
            "business_id": "b1",
            "entity_kind": "sole_proprietorship",
            "assessable_profit": "100000",
            "share": "1/1",
        }
    ]
    year["financials"]["p1"]["has_assessable_business_income"] = True
    year["financials"]["p1"]["source_classification"]["business"] = "confirmed_in_scope"


def _setup_care(body: dict) -> None:
    year = body["years"][_YEAR]
    body["subjects"].append({"subject_id": "d2", "birth_date": "1950-01-01"})
    year["relations"] = [
        {
            "relation_id": "r1",
            "actor_id": "p1",
            "subject_id": "d2",
            "kinship": {
                "kind": "parent",
                "basis": "birth",
                "during": _tf("2025-04-01", "2026-04-01", True),
            },
        }
    ]
    year["care_payments"] = [
        {
            "payment_id": "e1",
            "payer_id": "p1",
            "subject_id": "d2",
            "paid_on": "2025-06-15",
            "amount": "10000",
            "care_from": "2025-04-01",
            "care_to": "2026-04-01",
            "residential_care_received": True,
            "paid_to_home_or_agent": True,
            "home_basis": "cap459_licence",
            "home_status_in_force": _tf("2025-04-01", "2026-04-01", True),
        }
    ]


def _setup_deduction(body: dict) -> None:
    year = body["years"][_YEAR]
    year["financials"]["p1"]["deduction_items"] = [
        {"item_type": "other", "amount": "0"}
    ]


def _setup_eligibility(body: dict) -> None:
    _setup_deduction(body)
    year = body["years"][_YEAR]
    year["financials"]["p1"]["deduction_items"][0]["eligibility"] = {"value": False}


def _setup_manual(body: dict) -> None:
    body["years"][_YEAR]["manual_issues"] = [
        {"scope": "election", "id": "p1", "code": "other"}
    ]


def _setup_presence(body: dict) -> None:
    body["hk_presence_days"] = [{"person_id": "p1", "year": _YEAR, "days": 1}]


def _setup_actual_salary(body: dict) -> None:
    body["years"][_YEAR]["assessment"]["actual"] = {
        "salary_joint_elected": False,
        "history": "ordinary",
    }


def _setup_nomination_salary(body: dict) -> None:
    _setup_actual_salary(body)
    body["years"][_YEAR]["assessment"]["actual"]["salary_joint_nomination"] = {
        "status": "none",
        "person_id": None,
    }


def _setup_nomination_pa(body: dict) -> None:
    body["years"][_YEAR]["assessment"]["hypothetical_salary_joint_nomination"] = {
        "status": "none",
        "person_id": None,
    }


def _setup_pa_entry(body: dict, kind: str) -> None:
    """actual.pa 条目在两种税型都合法（salary 需先补 actual 对象）。"""
    year = body["years"][_YEAR]
    actual = year["assessment"].setdefault("actual", {})
    actual.setdefault("pa", {})
    actual["pa"]["p1"] = {"mode": "none"}


def _setup_assumptions(body: dict, kind: str) -> None:
    if kind == "salary":
        body["years"][_NEXT] = _next_year_like(
            _salary_year(), ("2026-04-01", "2027-04-01")
        )
    else:
        body["years"][_NEXT] = _next_year_like(
            _pa_year(_NEXT), ("2026-04-01", "2027-04-01")
        )
    body["assumptions"] = [
        {
            "assumption_id": "a1",
            "kind": "same_income",
            "person_id": "p1",
            "source_year": _YEAR,
            "target_year": _NEXT,
        }
    ]


def _extra_at(container: dict, key: str) -> None:
    container[key] = _PROBE


# (case_id, kinds, setup(body, kind)|None, mutate, expected_code, expected_field)
_LAYER_CASES: list[tuple] = [
    ("manual", ("salary", "pa"), lambda b, k: _setup_manual(b),
     lambda b: _extra_at(b["years"][_YEAR]["manual_issues"][0], _PROBE), "E_INPUT_EXTRA",
     f"years.{_YEAR}.manual_issues[0].{_PROBE}"),
    ("presence", ("salary", "pa"), lambda b, k: _setup_presence(b),
     lambda b: _extra_at(b["hk_presence_days"][0], _PROBE), "E_INPUT_EXTRA",
     f"hk_presence_days[0].{_PROBE}"),
    ("subject_facts", ("salary", "pa"), lambda b, k: _setup_child_claim(b),
     lambda b: _extra_at(b["years"][_YEAR]["subject_facts"]["d1"], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.subject_facts.d1.{_PROBE}"),
    ("relations", ("salary", "pa"), lambda b, k: _setup_relation(b),
     lambda b: _extra_at(b["years"][_YEAR]["relations"][0], _PROBE), "E_INPUT_EXTRA",
     f"years.{_YEAR}.relations[0].{_PROBE}"),
    ("kinship", ("salary", "pa"), lambda b, k: _setup_relation(b),
     lambda b: _extra_at(b["years"][_YEAR]["relations"][0]["kinship"], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.relations[0].kinship.{_PROBE}"),
    ("property", ("salary", "pa"), lambda b, k: _setup_property(b),
     lambda b: _extra_at(b["years"][_YEAR]["financials"]["p1"]["properties"][0], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.financials.p1.properties[0].{_PROBE}"),
    ("rates", ("salary", "pa"), lambda b, k: _setup_property(b),
     lambda b: _extra_at(
         b["years"][_YEAR]["financials"]["p1"]["properties"][0]["rates_paid_by_owner"],
         _PROBE),
     "E_INPUT_EXTRA",
     f"years.{_YEAR}.financials.p1.properties[0].rates_paid_by_owner.{_PROBE}"),
    ("business", ("salary", "pa"), lambda b, k: _setup_business(b),
     lambda b: _extra_at(b["years"][_YEAR]["financials"]["p1"]["businesses"][0], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.financials.p1.businesses[0].{_PROBE}"),
    ("care", ("salary", "pa"), lambda b, k: _setup_care(b),
     lambda b: _extra_at(b["years"][_YEAR]["care_payments"][0], _PROBE), "E_INPUT_EXTRA",
     f"years.{_YEAR}.care_payments[0].{_PROBE}"),
    ("claims", ("salary", "pa"), lambda b, k: _setup_child_claim(b),
     lambda b: _extra_at(b["years"][_YEAR]["claims"][0], _PROBE), "E_INPUT_EXTRA",
     f"years.{_YEAR}.claims[0].{_PROBE}"),
    ("actual", ("salary", "pa"), lambda b, k: _setup_actual_salary(b),
     lambda b: _extra_at(b["years"][_YEAR]["assessment"]["actual"], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.assessment.actual.{_PROBE}"),
    ("nomination_actual", ("salary", "pa"), lambda b, k: _setup_nomination_salary(b),
     lambda b: _extra_at(
         b["years"][_YEAR]["assessment"]["actual"]["salary_joint_nomination"], _PROBE),
     "E_INPUT_EXTRA",
     f"years.{_YEAR}.assessment.actual.salary_joint_nomination.{_PROBE}"),
    ("deductions", ("salary", "pa"), lambda b, k: _setup_deduction(b),
     lambda b: _extra_at(b["years"][_YEAR]["financials"]["p1"]["deduction_items"][0], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.financials.p1.deduction_items[0].{_PROBE}"),
    ("eligibility", ("salary", "pa"), lambda b, k: _setup_eligibility(b),
     lambda b: _extra_at(
         b["years"][_YEAR]["financials"]["p1"]["deduction_items"][0]["eligibility"], _PROBE),
     "E_INPUT_EXTRA",
     f"years.{_YEAR}.financials.p1.deduction_items[0].eligibility.{_PROBE}"),
    ("assumptions", ("salary", "pa"), lambda b, k: _setup_assumptions(b, k),
     lambda b: _extra_at(b["assumptions"][0], _PROBE), "E_INPUT_EXTRA",
     f"assumptions[0].{_PROBE}"),
    ("pa_entry", ("salary", "pa"), _setup_pa_entry,
     lambda b: _extra_at(b["years"][_YEAR]["assessment"]["actual"]["pa"]["p1"], _PROBE),
     "E_INPUT_EXTRA", f"years.{_YEAR}.assessment.actual.pa.p1.{_PROBE}"),
    ("nomination_hypothetical", ("salary", "pa"), lambda b, k: _setup_nomination_pa(b),
     lambda b: _extra_at(
         b["years"][_YEAR]["assessment"]["hypothetical_salary_joint_nomination"], _PROBE),
     "E_INPUT_EXTRA",
     f"years.{_YEAR}.assessment.hypothetical_salary_joint_nomination.{_PROBE}"),
]


def _build_layer_case(case: tuple, kind: str) -> tuple[dict, dict]:
    case_id, kinds, setup, mutate, _, _ = case
    assert kind in kinds, (case_id, kind)
    base = _salary_body() if kind == "salary" else _pa_body()
    if setup is not None:
        setup(base, kind)
    control = copy.deepcopy(base)
    mutated = copy.deepcopy(base)
    mutate(mutated)
    return control, mutated


# 简单层在两种税型各跑一次（同一规范化权威须对两税一致）
_Both = [
    ("input", None, lambda b: _extra_at(b, _PROBE), _PROBE),
    ("subjects", None, lambda b: _extra_at(b["subjects"][0], _PROBE),
     f"subjects[0].{_PROBE}"),
    ("year", None, lambda b: _extra_at(b["years"][_YEAR], _PROBE),
     f"years.{_YEAR}.{_PROBE}"),
    ("person", None, lambda b: _extra_at(b["years"][_YEAR]["participants"][0], _PROBE),
     f"years.{_YEAR}.participants[0].{_PROBE}"),
    ("household", None, lambda b: _extra_at(b["years"][_YEAR]["household"], _PROBE),
     f"years.{_YEAR}.household.{_PROBE}"),
    ("finance", None, lambda b: _extra_at(b["years"][_YEAR]["financials"]["p1"], _PROBE),
     f"years.{_YEAR}.financials.p1.{_PROBE}"),
    ("source", None,
     lambda b: _extra_at(
         b["years"][_YEAR]["financials"]["p1"]["source_classification"], _PROBE),
     f"years.{_YEAR}.financials.p1.source_classification.{_PROBE}"),
    ("assessment", None, lambda b: _extra_at(b["years"][_YEAR]["assessment"], _PROBE),
     f"years.{_YEAR}.assessment.{_PROBE}"),
]


@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize(
    "case_id,setup,mutate,field", _Both, ids=[c[0] for c in _Both]
)
def test_002_closed_layer_extra_matrix_both_tax_types(
    parity, kind: str, case_id: str, setup, mutate, field: str, entry: str
) -> None:
    """简单层逐层 extra：tax×entry 独立——ASGI 与对应纯引擎均精确
    E_INPUT_EXTRA＋同一路径（独立字面）。"""
    base = _salary_body() if kind == "salary" else _pa_body()
    if setup is not None:
        setup(base)
    control = copy.deepcopy(base)
    mutated = copy.deepcopy(base)
    mutate(mutated)

    _assert_control_201(parity, kind, control, f"{case_id}/{kind} 对照", entry=entry)
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, kind, mutated, ("E_INPUT_EXTRA", field), f"{case_id}/{kind} extra",
        entry,
    )
    parity.assert_no_service()


_SUB_OBJECT_MATRIX: list[tuple] = [
    (case[0], kind, entry)
    for case in _LAYER_CASES
    for kind in case[1]
    for entry in _ENTRIES
]


@pytest.mark.parametrize(
    "case_id,kind,entry", _SUB_OBJECT_MATRIX,
    ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _SUB_OBJECT_MATRIX],
)
def test_002_closed_layer_extra_matrix_subobjects(
    parity, case_id: str, kind: str, entry: str
) -> None:
    """带子对象的层（relations/kinship/finance 子对象/care/claims/actual/…）：
    两相关税型×HTTP/纯独立参数化——合法元素对照 + 仅加一 extra →
    E_INPUT_EXTRA＋独立字面路径（仅路径不同不构成重复缺陷主张）。"""
    case = next(c for c in _LAYER_CASES if c[0] == case_id)
    _, _, _, _, code, field = case
    control, mutated = _build_layer_case(case, kind)
    _assert_control_201(parity, kind, control, f"{case_id}/{kind} 对照", entry=entry)
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, kind, mutated, (code, field), f"{case_id}/{kind} extra", entry
    )
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# 九个必填 Year 键：missing vs null 分明 E_INPUT_MISSING/TYPE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", _V2_YEAR_REQUIRED_KEYS)
@pytest.mark.parametrize("mode", ["missing", "null"])
@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_year_required_key_missing_vs_null(
    parity, key: str, mode: str, kind: str, entry: str
) -> None:
    """八个必填 Year 容器（manual_issues 可选、不在矩阵）：删除 → E_INPUT_MISSING；
    显式 null → E_INPUT_TYPE；两侧均用独立期望字面 `years.<YA>.<key>`（不以被测
    HTTP 行为为 oracle，且不再只比较末段路径）。"""
    body = _salary_body() if kind == "salary" else _pa_body()
    if mode == "missing":
        del body["years"][_YEAR][key]
        expected = ("E_INPUT_MISSING", f"years.{_YEAR}.{key}")
    else:
        body["years"][_YEAR][key] = None
        expected = ("E_INPUT_TYPE", f"years.{_YEAR}.{key}")

    control = _salary_body() if kind == "salary" else _pa_body()
    _assert_control_201(parity, kind, control, f"required/{key}/{mode}/{kind} 对照", entry=entry)
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, kind, body, expected, f"required/{key}/{mode}/{kind}", entry
    )
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# 可选字段：省略合法 vs 显式 null（类型错误，不是 NAI/MPF 资格追问）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_optional_birth_date_omitted_ok_null_type_error(parity, entry: str) -> None:
    """subjects[].birth_date 省略＝未提供（合法）；显式 null＝类型错误
    （HTTP/纯独立参数化）。"""
    omitted = _salary_body()
    _assert_control_201(parity, "salary", omitted, "birth_date 省略", entry=entry)
    parity.assert_no_service()

    nulled = _salary_body()
    nulled["subjects"][0]["birth_date"] = None
    _assert_entry_rejection(
        parity, "salary", nulled,
        ("E_INPUT_TYPE", "subjects[0].birth_date"), "birth_date=null", entry,
    )
    parity.assert_no_service()


@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_optional_provisional_paid_omitted_ok_null_type_error(
    parity, kind: str, entry: str
) -> None:
    """provisional_paid 省略＝未提供（partial 语义）；显式 null＝类型错误，
    不得变成 needs_input 类资格追问或 0（两税×HTTP/纯独立参数化）。"""
    omitted = _salary_body() if kind == "salary" else _pa_body()
    del omitted["years"][_YEAR]["provisional_paid"]
    _assert_control_201(parity, kind, omitted, f"{kind} provisional_paid 省略", entry=entry)
    parity.assert_no_service()

    nulled = _salary_body() if kind == "salary" else _pa_body()
    nulled["years"][_YEAR]["provisional_paid"] = None
    _assert_entry_rejection(
        parity, kind, nulled,
        ("E_INPUT_TYPE", f"years.{_YEAR}.provisional_paid"),
        f"{kind} provisional_paid=null", entry,
    )
    parity.assert_no_service()


def _with_aux(body: dict, array_key: str, mode: str) -> dict:
    """辅助数组三态：omitted（省略保持省略）／empty（显式 []）／null。"""
    if mode == "omitted":
        body.pop(array_key, None)
    elif mode == "empty":
        body[array_key] = []
    else:
        body[array_key] = None
    return body


@pytest.mark.parametrize("array_key", ("hk_presence_days", "assumptions"))
@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("mode", ["omitted", "empty", "null"])
def test_002_optional_auxiliary_arrays_omitted_empty_legal_null_type(
    parity, array_key: str, kind: str, entry: str, mode: str
) -> None:
    """根级可选辅助数组（hk_presence_days/assumptions）：省略保持省略（不得静默
    补 `[]`：与显式 [] 的 canonical hash 不同）、显式 `[]` 合法、显式 `null` →
    E_INPUT_TYPE；三态各自独立 case（互不遮蔽），两税×HTTP/纯独立参数化。"""
    base = _salary_body() if kind == "salary" else _pa_body()
    _assert_control_201(
        parity, kind, copy.deepcopy(base), f"{array_key}/{kind} 对照", entry=entry
    )
    parity.assert_no_service()

    if mode == "omitted":
        omitted = _with_aux(copy.deepcopy(base), array_key, "omitted")
        if entry == "http":
            r_om = parity.prepare(kind, copy.deepcopy(omitted))
            assert r_om.status_code == 201, (
                f"{array_key} 省略须接受（005 §4：省略保持省略），实际 "
                f"{r_om.status_code}：{r_om.text[:240]!r}"
            )
            empty_peer = _with_aux(copy.deepcopy(base), array_key, "empty")
            r_em = parity.prepare(kind, copy.deepcopy(empty_peer))
            assert r_em.status_code == 201, (
                f"{array_key} 显式 [] 合法（对照；其独立 case 亦覆盖），实际 "
                f"{r_em.status_code}：{r_em.text[:240]!r}"
            )
            assert r_om.json()["input_hash"] != r_em.json()["input_hash"], (
                f"{array_key}：省略必须保持省略，不得静默补 `[]` 后同 hash"
            )
        else:
            observed_om = _observe_pure(kind, copy.deepcopy(omitted))
            assert observed_om == (None, None), (
                f"{array_key} 省略：纯引擎（{kind}）须保持缺席并接受，实际 "
                f"{observed_om!r}"
            )
    elif mode == "empty":
        empty = _with_aux(copy.deepcopy(base), array_key, "empty")
        if entry == "http":
            r_em = parity.prepare(kind, copy.deepcopy(empty))
            assert r_em.status_code == 201, (
                f"{array_key} 显式 [] 合法，实际 {r_em.status_code}："
                f"{r_em.text[:240]!r}"
            )
        else:
            observed_em = _observe_pure(kind, copy.deepcopy(empty))
            assert observed_em == (None, None), (
                f"{array_key} 显式 []：纯引擎（{kind}）须接受，实际 {observed_em!r}"
            )
    else:
        assert mode == "null", mode
        nulled = _with_aux(copy.deepcopy(base), array_key, "null")
        _assert_entry_rejection(
            parity, kind, nulled, ("E_INPUT_TYPE", array_key),
            f"{array_key}/null", entry,
        )
    parity.assert_no_service()


@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize("year_scope", ["current", "next"])
@pytest.mark.parametrize("mode", ["omitted", "empty", "null"])
def test_002_manual_issues_optional_presence_preserved(
    parity, kind: str, entry: str, year_scope: str, mode: str
) -> None:
    """manual_issues 可选（Year 唯一可选容器）：省略保持省略（不得静默补 `[]`
    以取同 hash）／显式 `[]` 合法／显式 `null` → E_INPUT_TYPE；三态各自独立
    case（互不遮蔽），按本年＋显式 next 年、HTTP/两支纯引擎独立覆盖。"""
    body = (_salary_body(with_next=True) if kind == "salary"
            else _pa_body(with_next=True))
    year = _YEAR if year_scope == "current" else _NEXT
    assert "manual_issues" in _V2_YEAR_OPTIONAL_KEYS, _V2_YEAR_OPTIONAL_KEYS
    assert "manual_issues" not in _V2_YEAR_REQUIRED_KEYS, _V2_YEAR_REQUIRED_KEYS
    expected_field = f"years.{year}.manual_issues"

    _assert_control_201(
        parity, kind, copy.deepcopy(body), f"manual_issues/{year_scope} 对照",
        entry=entry,
    )
    parity.assert_no_service()

    if mode == "omitted":
        omitted = copy.deepcopy(body)
        omitted["years"][year].pop("manual_issues", None)
        if entry == "http":
            r_om = parity.prepare(kind, copy.deepcopy(omitted))
            assert r_om.status_code == 201, (
                f"manual_issues 省略须接受（005 §4），实际 {r_om.status_code}："
                f"{r_om.text[:240]!r}"
            )
            empty_peer = copy.deepcopy(body)
            empty_peer["years"][year]["manual_issues"] = []
            r_em = parity.prepare(kind, copy.deepcopy(empty_peer))
            assert r_em.status_code == 201, (
                f"manual_issues 显式 [] 合法（对照；其独立 case 亦覆盖），实际 "
                f"{r_em.status_code}：{r_em.text[:240]!r}"
            )
            assert r_om.json()["input_hash"] != r_em.json()["input_hash"], (
                "manual_issues 省略必须保持省略，不得静默补 `[]` 后同 hash"
            )
        else:
            observed_om = _observe_pure(kind, copy.deepcopy(omitted))
            assert observed_om == (None, None), (
                f"manual_issues 省略/{year_scope}：纯引擎（{kind}）须保持缺席并"
                f"接受，实际 {observed_om!r}"
            )
    elif mode == "empty":
        empty = copy.deepcopy(body)
        empty["years"][year]["manual_issues"] = []
        if entry == "http":
            r_em = parity.prepare(kind, copy.deepcopy(empty))
            assert r_em.status_code == 201, (
                f"manual_issues 显式 [] 合法，实际 {r_em.status_code}："
                f"{r_em.text[:240]!r}"
            )
        else:
            observed_em = _observe_pure(kind, copy.deepcopy(empty))
            assert observed_em == (None, None), (
                f"manual_issues 显式 []：纯引擎（{kind}）须接受，实际 {observed_em!r}"
            )
    else:
        assert mode == "null", mode
        nulled = copy.deepcopy(body)
        nulled["years"][year]["manual_issues"] = None
        _assert_entry_rejection(
            parity, kind, nulled, ("E_INPUT_TYPE", expected_field),
            f"manual_issues/null/{year_scope}", entry,
        )
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# TF：每键年先验（不得用显式 Year 窗口并集）+ 四种单日扩展 + 上年 care
# ---------------------------------------------------------------------------


_TF_PREDICATES = (
    "ordinarily_resident_hk",
    "both_parents_dead",
    "marriage",
    "living_apart",
)

_TF_VALUES = {
    "ordinarily_resident_hk": True,
    "both_parents_dead": False,
    "marriage": "unmarried",
    "living_apart": False,
}


def _set_tf(body: dict, year: str, predicate: str, interval: list) -> None:
    year_obj = body["years"][year]
    if predicate in ("ordinarily_resident_hk", "both_parents_dead"):
        year_obj["subject_facts"].setdefault("p1", {})
        year_obj["subject_facts"]["p1"][predicate] = interval
    else:
        year_obj["household"][predicate] = interval


def _tf_path(year: str, predicate: str) -> str:
    if predicate in ("ordinarily_resident_hk", "both_parents_dead"):
        return f"years.{year}.subject_facts.p1.{predicate}[0]"
    return f"years.{year}.household.{predicate}[0]"


def _ensure_extendable_interval(
    body: dict, year: str, predicate: str, interval: list
) -> None:
    """在保留键年年度基线事实的前提下确保扩展单日区间存在（逐项判重，同值不重复
    追加）；不得整段替换 marriage 等年度事实（正例控制）。"""
    year_obj = body["years"][year]
    if predicate in ("ordinarily_resident_hk", "both_parents_dead"):
        container = year_obj["subject_facts"].setdefault("p1", {}).setdefault(
            predicate, []
        )
    else:
        container = year_obj["household"].setdefault(predicate, [])
    for item in interval:
        if item not in container:
            container.append(item)


def _election_window(year: str) -> tuple[str, str]:
    """已声明 PA 选择日的单日窗口（与任何 reference_date 无耦合）。"""
    start = int(year[:4])
    return f"{start + 1}-06-15", f"{start + 1}-06-16"


def _pa_election_day_body(day: str) -> dict:
    """actual 基础束：reference_date **省略**，TF 扩展日仅来自已声明 PA 选择日
    （actual.pa[p1]={mode:individual,date}；005 §4.10 规则 5/7）。仅列明四谓词
    可覆盖该单日；键年年度基线（婚姻窗口/居港）保留。"""
    body = _pa_body()
    year_obj = body["years"][_YEAR]
    assessment = year_obj["assessment"]
    assessment.pop("reference_date", None)  # 不隐含代用参考日
    assessment["basis"] = "actual"
    assessment["candidates"] = ["pa_self"]  # 实际选择与候选一致（individual）
    assessment["selection"] = "pa_self"
    assessment["actual"]["pa"]["p1"] = {"mode": "individual", "date": day}
    day_to = f"{day[:8]}{int(day[8:]) + 1:02d}"
    w_from, w_to = f"{int(_YEAR[:4])}-04-01", f"{int(_YEAR[:4]) + 1}-04-01"
    year_obj["household"]["marriage"] = [
        {"from": w_from, "to": w_to, "value": "unmarried"},
        {"from": day, "to": day_to, "value": "unmarried"},
    ]
    year_obj["subject_facts"].setdefault("p1", {})["ordinarily_resident_hk"] = [
        {"from": day, "to": day_to, "value": True}
    ]
    return body


@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_prior_year_tf_in_next_year_object_rejected(
    parity, kind: str, entry: str
) -> None:
    """本年窗口的 TF 复制进 next 对象 → 每键年先验拒绝（并集域缺陷）。
    期望：E_INPUT_TYPE＋years.2026_27.household.marriage[0]（两税×HTTP/纯独立）。"""
    body = _salary_body(with_next=True) if kind == "salary" else _pa_body(with_next=True)
    _assert_control_201(parity, kind, copy.deepcopy(body), "双年合法对照", entry=entry)
    parity.assert_no_service()

    _set_tf(body, _NEXT, "marriage", _tf("2025-04-01", "2026-04-01", "unmarried"))
    _assert_entry_rejection(
        parity, kind, body,
        ("E_INPUT_TYPE", f"years.{_NEXT}.household.marriage[0]"),
        "prior-year TF in next object", entry,
    )
    parity.assert_no_service()


@pytest.mark.parametrize("kind", ["salary", "pa"])
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_next_year_tf_in_current_year_object_rejected(
    parity, kind: str, entry: str
) -> None:
    """next 窗口的 TF 复制进本年对象 → 本年域先验拒绝（并集域缺陷）。
    期望：E_INPUT_TYPE＋years.2025_26.household.marriage[0]（两税×HTTP/纯独立）。"""
    body = _salary_body(with_next=True) if kind == "salary" else _pa_body(with_next=True)
    _assert_control_201(parity, kind, copy.deepcopy(body), "双年合法对照", entry=entry)
    parity.assert_no_service()

    _set_tf(body, _YEAR, "marriage", _tf("2026-04-01", "2027-04-01", "unmarried"))
    _assert_entry_rejection(
        parity, kind, body,
        ("E_INPUT_TYPE", f"years.{_YEAR}.household.marriage[0]"),
        "next-year TF in current object", entry,
    )
    parity.assert_no_service()


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_reference_day_extension_declared_single_day_positive(
    parity, predicate: str, entry: str
) -> None:
    """四类列明谓词（ordinarily_resident_hk/both_parents_dead/marriage/
    living_apart）各自的已声明参考日单日区间合法（逐谓词×entry 独立，正例按
    entry 分派）；年度基线事实保留，仅**追加**扩展单日（marriage 不得整段替换
    键年婚姻窗口）。"""
    body = _pa_body()
    _ensure_extendable_interval(
        body, _YEAR, predicate, _tf("2026-06-01", "2026-06-02", _TF_VALUES[predicate])
    )
    _assert_control_201(parity, "pa", copy.deepcopy(body), f"{predicate} 参考日单日", entry=entry)
    parity.assert_no_service()


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_reference_day_extension_two_day_rejected(
    parity, predicate: str, entry: str
) -> None:
    """列明谓词也仅开放所声明参考日的一天区间；跨两天仍拒绝（逐谓词独立）。"""
    body = _pa_body()
    _set_tf(body, _YEAR, predicate,
            _tf("2026-06-01", "2026-06-03", _TF_VALUES[predicate]))
    _assert_control_201(parity, "pa", _pa_body(), f"{predicate} 两天对照", entry=entry)
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, "pa", body,
        ("E_INPUT_TYPE", _tf_path(_YEAR, predicate)), f"{predicate} 参考日两天", entry,
    )
    parity.assert_no_service()


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_undeclared_day_extension_rejected(
    parity, predicate: str, entry: str
) -> None:
    """任意年外一天（无参考/选择日声明）对四类列明谓词同样拒绝：不得因
    extendable 而放任未声明日期（逐谓词独立）。"""
    body = _pa_body()
    _set_tf(body, _YEAR, predicate,
            _tf("2026-06-10", "2026-06-11", _TF_VALUES[predicate]))
    _assert_control_201(parity, "pa", _pa_body(), f"{predicate} 未声明对照", entry=entry)
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, "pa", body,
        ("E_INPUT_TYPE", _tf_path(_YEAR, predicate)), f"{predicate} 未声明年外一天", entry,
    )
    parity.assert_no_service()


@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_nonlisted_predicate_reference_day_rejected(parity, entry: str) -> None:
    """未列明谓词（full_time_education）在参考日仍拒绝（不在四类扩展内）。"""
    body = _pa_body()
    body["years"][_YEAR]["subject_facts"]["p1"]["full_time_education"] = _tf(
        "2026-06-01", "2026-06-02", True
    )
    _assert_control_201(parity, "pa", _pa_body(), "非列明谓词对照", entry=entry)
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, "pa", body,
        ("E_INPUT_TYPE", f"years.{_YEAR}.subject_facts.p1.full_time_education[0]"),
        "未列明谓词参考日扩展", entry,
    )
    parity.assert_no_service()


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_election_day_extension_single_day_positive(
    parity, predicate: str, entry: str
) -> None:
    """四谓词的**已声明 PA 选择日**单日扩展合法（actual.pa[p1].date；reference_date
    省略，选择日来源独立、不被参考日隐含代用；005 §4.10 规则 5/7＋§7）；年度基线
    事实保留，仅追加扩展单日（逐谓词×entry 独立）。"""
    day, day_to = _election_window(_YEAR)
    body = _pa_election_day_body(day)
    _ensure_extendable_interval(
        body, _YEAR, predicate, _tf(day, day_to, _TF_VALUES[predicate])
    )
    _assert_control_201(parity, "pa", copy.deepcopy(body), f"{predicate} 选择日单日", entry=entry)
    parity.assert_no_service()


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_tf_election_day_extension_two_day_rejected(
    parity, predicate: str, entry: str
) -> None:
    """已声明 PA 选择日同样只开放该日一天区间：选择日外/跨两天仍拒绝
    （逐谓词×entry 独立）。"""
    day, _ = _election_window(_YEAR)
    day_plus_two = f"{day[:8]}{int(day[8:]) + 2:02d}"
    body = _pa_election_day_body(day)
    _set_tf(body, _YEAR, predicate,
            _tf(day, day_plus_two, _TF_VALUES[predicate]))
    _assert_control_201(
        parity, "pa", _pa_election_day_body(day), f"{predicate} 选择日两天对照",
        entry=entry,
    )
    parity.assert_no_service()
    _assert_entry_rejection(
        parity, "pa", body,
        ("E_INPUT_TYPE", _tf_path(_YEAR, predicate)), f"{predicate} 选择日两天", entry,
    )
    parity.assert_no_service()


# 纯引擎侧：本年/紧邻 next 对象各自的域错误先于 guard 状态（available/blocked/
# missing_rules 同）；HTTP 侧由 entry=http 节点（含拒绝式 guard）分别证明先于 guard。


def _ctx_with_statuses(bundle: dict, year: str, *, current_status: str,
                       next_status: str) -> dict:
    from app.config import ENGINE_VERSION
    from app.core.hashing import bundle_content_hash

    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    return {
        "binding": {
            "bundle_id": "b-002-parity-ctx",
            "bundle_hash": bundle_content_hash(bundle),
            "rules_schema_version": bundle["rules_schema_version"],
            "engine_version": ENGINE_VERSION,
        },
        "current": {"year": year, "status": current_status, "reason_codes": []},
        "next": {"year": following, "status": next_status, "reason_codes": []},
    }


def _observe_pure_with(kind: str, body: dict, ctx: dict) -> tuple:
    from app.core.errors import AppError
    from app.engines.personal_assessment import compare_personal_assessment
    from app.engines.salaries import calculate_salaries_tax

    bundle = _bundle()
    try:
        if kind == "salary":
            calculate_salaries_tax(body, bundle, availability=ctx)
        else:
            compare_personal_assessment(body, bundle, availability=ctx)
    except AppError as exc:
        return exc.code, exc.field
    except Exception as exc:  # noqa: BLE001 - 裸异常显式暴露为 Red，不得吞掉
        return type(exc).__name__, None
    return None, None


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("guard", ["available", "blocked", "missing_rules"])
@pytest.mark.parametrize("scope", ["current", "next"])
@pytest.mark.parametrize("kind", ["salary", "pa"])
def test_002_tf_own_year_error_precedes_guard(
    kind: str, scope: str, guard: str, predicate: str
) -> None:
    """两纯引擎：本年/紧邻 next 对象自身的 TF 域错误先于 guard 状态被拒绝
    （available/blocked/missing_rules 均同）；HTTP 侧由对应 entry=http 节点证明。"""
    bundle = _bundle()
    body = _salary_body(with_next=True) if kind == "salary" else _pa_body(with_next=True)
    year = _YEAR if scope == "current" else _NEXT
    start = int(year[:4])
    if kind == "pa":
        interval = _tf(f"{start + 1}-06-01", f"{start + 1}-06-03",
                       _TF_VALUES[predicate])
    else:
        interval = _tf(f"{start + 1}-04-01", f"{start + 1}-04-03",
                       _TF_VALUES[predicate])
    _set_tf(body, year, predicate, interval)

    if scope == "current":
        ctx = _ctx_with_statuses(bundle, _YEAR, current_status=guard,
                                 next_status="available")
    else:
        ctx = _ctx_with_statuses(bundle, _YEAR, current_status="available",
                                 next_status=guard)
    observed = _observe_pure_with(kind, body, ctx)
    expected = ("E_INPUT_TYPE", _tf_path(year, predicate))
    assert observed == expected, (
        f"{kind}/{scope}/{guard}/{predicate}：纯引擎须先于 guard 精确 "
        f"{expected!r}，实际 {observed!r}"
    )


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("scope", ["current", "next"])
def test_002_tf_http_input_error_precedes_rejecting_guard(
    parity, predicate: str, scope: str
) -> None:
    """有限 HTTP 拒绝式 guard（next=blocked）下的**顺序证据**（独立节点，避免
    与精确错误断言互相遮蔽；ora-17 CQA-03）：本年/next 对象非法 TF 须在咨询
    任何 guard **之前**被拒绝（guard 未达＝`periods == []`；engine 报告 E2：
    结构先验先于年度 guard 短路）。响应码非 UNVERIFIABLE／无 prepared_id 为
    辅助证据；本地计数断言先于顺序断言，避免 current 侧顺序失败遮蔽计数证据。

    current 侧实际会先咨询 next guard（`_availability_context`）再于引擎拒绝，
    periods 非空＝产品 Red（对照 board:43「guard 未达」），不作为测试缺陷。精确
    (code, field) 证据在同族独立节点
    `test_002_tf_http_input_error_exact_code_and_field` 单独观察。"""
    body = _pa_body(with_next=True)
    year = _YEAR if scope == "current" else _NEXT
    start = int(year[:4])
    _set_tf(
        body, year, predicate,
        _tf(f"{start + 1}-06-01", f"{start + 1}-06-03", _TF_VALUES[predicate]),
    )
    parity.scheduler.modes[_NEXT] = "blocked"  # 拒绝式 guard（期间精确阻断）
    r = parity.prepare("pa", body)
    assert r.status_code == 422, (
        f"非法 TF 先于拒绝 guard（{scope}）：须结构化 422（不得 201/409/500 "
        f"遮蔽），实际 {r.status_code}：{r.text[:240]!r}"
    )
    observed = _error_code_field(r.json())
    assert observed[0] != "E_RULES_STATE_UNVERIFIABLE", (
        f"非法 TF 不得被拒绝式 guard 先吞掉（辅助顺序证据），实际 {observed!r}"
    )
    assert "prepared_id" not in r.json(), "拒绝路径不得签发 prepared_id"
    parity.assert_no_service()
    assert parity.scheduler.periods == [], (
        f"非法 TF 须在咨询任何 guard 之前拒绝（guard 未达＝periods 为空；"
        f"E2 先验先于年度 guard 短路），实际 periods={parity.scheduler.periods!r}"
    )


@pytest.mark.parametrize("predicate", _TF_PREDICATES)
@pytest.mark.parametrize("scope", ["current", "next"])
def test_002_tf_http_input_error_exact_code_and_field(
    parity, predicate: str, scope: str
) -> None:
    """同族独立节点（ora-17 CQA-03）：拒绝式 guard 下非法 TF 的精确
    (code, field) 证据（`E_INPUT_TYPE`＋TF 路径）；与顺序证据节点分离，
    任一方失败不得遮蔽另一方。"""
    body = _pa_body(with_next=True)
    year = _YEAR if scope == "current" else _NEXT
    start = int(year[:4])
    _set_tf(
        body, year, predicate,
        _tf(f"{start + 1}-06-01", f"{start + 1}-06-03", _TF_VALUES[predicate]),
    )
    parity.scheduler.modes[_NEXT] = "blocked"  # 拒绝式 guard（期间精确阻断）
    r = parity.prepare("pa", body)
    assert r.status_code == 422, (
        f"非法 TF 先于拒绝 guard（{scope}）：须结构化 422，实际 {r.status_code}："
        f"{r.text[:240]!r}"
    )
    observed = _error_code_field(r.json())
    expected = ("E_INPUT_TYPE", _tf_path(year, predicate))
    assert observed == expected, (
        f"非法 TF 先于拒绝 guard（{scope}）：期望精确 {expected!r}，实际 {observed!r}"
    )
    assert "prepared_id" not in r.json(), "拒绝路径不得签发 prepared_id"
    parity.assert_no_service()


def _care_prior_year_body() -> dict:
    """完整落于上一 YA 的 care 正例（显式 MPF 0；付款在本 YA）。"""
    base = _salary_body()
    fin = base["years"][_YEAR]["financials"]["p1"]
    fin["mpf_mandatory_contributions"] = "0"  # 显式 0（缺失≠0；005 §4.6）
    fin["employment_income_nai"] = "151000"
    _setup_care(base)
    care = base["years"][_YEAR]["care_payments"][0]
    care["paid_on"] = "2025-06-15"
    care["care_from"] = "2024-05-01"  # 完整落于 2024/25（上一 YA，非跨年）
    care["care_to"] = "2025-03-31"
    care["home_status_in_force"] = _tf("2024-05-01", "2025-03-31", True)
    return base


@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_care_home_tf_prior_year_interval_legal_and_bounded(parity, entry: str) -> None:
    """home_status_in_force 合法域＝付款自身 [care_from,care_to)，且此处完整落于
    上一 YA；显式 MPF 0（不是缺失）→ NCI=151,000−0−132,000=19,000 → 独立手算
    380/380/0；越出 care 区间（out-of-care）负例 TYPE。"""
    base = _care_prior_year_body()
    _assert_control_201(parity, "salary", copy.deepcopy(base), "上年 care 正例对照", entry=entry)
    parity.assert_no_service()

    if entry == "http":
        r = parity.prepare("salary", copy.deepcopy(base))
        assert r.status_code == 201, (
            f"完整上一 YA 服务/本年付款的 care 正例须 HTTP 201（005 §4.8/§7），"
            f"实际 {r.status_code}：{r.text[:240]!r}"
        )
    else:
        from app.core.errors import AppError

        try:
            result = _run_salary(copy.deepcopy(base))
        except AppError as exc:
            raise AssertionError(
                f"上年 care TF 为合法域（005 §4.8/§7），纯引擎不得拒绝："
                f"实际 {exc.code!r}/{exc.field!r}"
            ) from exc
        amounts = result["amounts"]
        # 独立手算（T3；T1 附表 2/4/43）：
        #   显式 MPF 0；NCI = 151,000 − 0 − 132,000 = 19,000
        #   累进 = 19,000×2% = 380；标准 151,000×15% = 22,650 → 择低 380
        #   宽减 = min(380×100%, 3,000) = 380 → final = 0
        assert amounts["before_reduction"] == "380", amounts
        assert amounts["reduction"] == "380", amounts
        assert amounts["final_after_reduction"] == "0", amounts

    outside = _care_prior_year_body()
    outside["years"][_YEAR]["care_payments"][0]["home_status_in_force"] = _tf(
        "2024-04-01", "2025-03-31", True
    )
    _assert_entry_rejection(
        parity, "salary", outside,
        ("E_INPUT_TYPE", f"years.{_YEAR}.care_payments[0].home_status_in_force[0]"),
        "care TF 越出 care 区间（out-of-care）", entry,
    )
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# presence 辅助域：随显式输入年；不得开放普通 TF 年外复制
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", [
    "explicit_next_allows_aux_y_plus_2",
    "current_only_y_plus_2_rejected",
    "outside_union_rejected",
    "aux_domain_never_extends_ordinary_tf",
])
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_presence_auxiliary_years_follow_explicit_years(
    parity, case_id: str, entry: str
) -> None:
    """presence.year ∈ 显式 Year 的 {Y−1,Y,Y+1} 并集：显式 next 使 aux Y+2 允许；
    仅当前年（无显式 next）的 Y+2 → E_INPUT_EXTRA（真实根路径
    `hk_presence_days[i].year`）；真正域外拒绝；辅助域从不扩成普通 TF 并集。"""
    if case_id == "explicit_next_allows_aux_y_plus_2":
        body = _salary_body(with_next=True)
        body["hk_presence_days"] = [
            {"person_id": "p1", "year": "2024_25", "days": 1},
            {"person_id": "p1", "year": "2027_28", "days": "unknown"},
        ]
        _assert_control_201(parity, "salary", body, "presence 并集内（显式 next 允许 Y+2）", entry=entry)
        parity.assert_no_service()
        return

    if case_id == "current_only_y_plus_2_rejected":
        control = _salary_body()
        body = _salary_body()
        body["hk_presence_days"] = [{"person_id": "p1", "year": "2027_28", "days": 1}]
        _assert_control_201(parity, "salary", control, "仅当前年对照", entry=entry)
        parity.assert_no_service()
        _assert_entry_rejection(
            parity, "salary", body,
            ("E_INPUT_EXTRA", "hk_presence_days[0].year"), case_id, entry,
        )
        parity.assert_no_service()
        return

    if case_id == "outside_union_rejected":
        control = _salary_body(with_next=True)
        body = copy.deepcopy(control)
        body["hk_presence_days"] = [{"person_id": "p1", "year": "2028_29", "days": 1}]
        _assert_control_201(parity, "salary", control, "并集对照", entry=entry)
        parity.assert_no_service()
        _assert_entry_rejection(
            parity, "salary", body,
            ("E_INPUT_EXTRA", "hk_presence_days[0].year"), case_id, entry,
        )
        parity.assert_no_service()
        return

    # aux_domain_never_extends_ordinary_tf：presence 合法不妨放行普通 TF 年外复制。
    control = _salary_body(with_next=True)
    _assert_control_201(parity, "salary", control, "辅助域对照（无年外 TF）", entry=entry)
    parity.assert_no_service()
    tf_union = copy.deepcopy(control)
    _set_tf(tf_union, _YEAR, "ordinarily_resident_hk",
            _tf("2024-04-01", "2025-04-01", True))
    _assert_entry_rejection(
        parity, "salary", tf_union,
        ("E_INPUT_TYPE", _tf_path(_YEAR, "ordinarily_resident_hk")),
        "辅助年不开放普通 TF", entry,
    )
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# ID 边界与引用/actor 精确矛盾
# ---------------------------------------------------------------------------


def _id_renamed_body(new_id: str) -> dict:
    """把唯一 subject/person 及其全部引用（participants/financials）同步改名，
    避免长度/前缀负例被意外 dangling-ref 干扰。"""
    body = _salary_body()
    body["subjects"][0]["subject_id"] = new_id
    year = body["years"][_YEAR]
    year["participants"][0]["person_id"] = new_id
    year["financials"] = {new_id: year["financials"]["p1"]}
    return body


@pytest.mark.parametrize("case_id", ["shortest", "longest", "overlong", "bad_prefix"])
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_id_length_boundary(parity, case_id: str, entry: str) -> None:
    """ID＝前缀＋1–31 位（总长 2–32）：shortest(`p1`)/longest(前缀＋31) 合法；
    overlong(前缀＋32) 与坏前缀拒绝 E_INPUT_TYPE＋subjects[0].subject_id。

    32 合法与 33 非法为相互独立 case：负例控制用**已过短 ID `p1`**（全部引用
    同步），不依赖 32 合法先通过，避免负例目标被正例控制首失败挡住。"""
    shortest = "p1"  # 前缀＋1（总长 2，下界）
    longest = "p" + "a" * 31  # 前缀＋31（总长 32，上界）
    if case_id == "shortest":
        _assert_control_201(parity, "salary", _id_renamed_body(shortest), "shortest 合法", entry=entry)
        parity.assert_no_service()
        return
    if case_id == "longest":
        _assert_control_201(parity, "salary", _id_renamed_body(longest), "longest(32) 合法", entry=entry)
        parity.assert_no_service()
        return

    _assert_control_201(parity, "salary", _id_renamed_body(shortest), f"{case_id} 对照", entry=entry)
    parity.assert_no_service()
    body = (_id_renamed_body("p" + "a" * 32) if case_id == "overlong"
            else _id_renamed_body("x1"))
    _assert_entry_rejection(
        parity, "salary", body,
        ("E_INPUT_TYPE", "subjects[0].subject_id"), case_id, entry,
    )
    parity.assert_no_service()


def _tf_married_self() -> list[dict]:
    return [{"from": "2025-04-01", "to": "2026-04-01", "value": "married",
             "spouse_id": "p1"}]


def _mut_crossyear_self(body: dict) -> None:
    # 仅改 next 年 self（p2 已由 _reference_control 注册）；单变量＝跨年 self 变了。
    body["years"][_NEXT] = _next_year_like(_salary_year(), ("2026-04-01", "2027-04-01"))
    body["years"][_NEXT]["participants"] = [{"person_id": "p2", "role": "self"}]
    body["years"][_NEXT]["financials"] = {"p2": _finance_salary()}


def _mut_nonparticipant_finance(body: dict) -> None:
    body["years"][_YEAR]["financials"]["p2"] = _finance_salary()


def _mut_participant_unmarried(body: dict) -> None:
    body["years"][_YEAR]["subject_facts"]["p1"] = {
        "unmarried": _tf("2025-04-01", "2026-04-01", True)
    }


def _mut_spouse_self(body: dict) -> None:
    body["years"][_YEAR]["household"]["marriage"] = _tf_married_self()


def _mut_dangling_participant(body: dict) -> None:
    body["years"][_YEAR]["participants"][0] = {"person_id": "p9", "role": "self"}


_REFERENCE_CASES: list[tuple] = [
    ("nonparticipant_finance", "salary", _mut_nonparticipant_finance,
     ("E_INPUT_CONTRADICTORY", f"years.{_YEAR}.financials.p2")),
    ("participant_unmarried", "salary", _mut_participant_unmarried,
     ("E_INPUT_CONTRADICTORY", f"years.{_YEAR}.subject_facts.p1.unmarried")),
    ("spouse_self", "salary", _mut_spouse_self,
     ("E_INPUT_CONTRADICTORY", f"years.{_YEAR}.household.marriage")),
    ("crossyear_self_instability", "salary", _mut_crossyear_self,
     ("E_INPUT_CONTRADICTORY", f"years.{_NEXT}.participants")),
    ("dangling_participant", "salary", _mut_dangling_participant,
     ("E_INPUT_CONTRADICTORY", f"years.{_YEAR}.participants[0].person_id")),
]


def _reference_control(case_id: str) -> dict:
    body = _salary_body()
    if case_id == "nonparticipant_finance":
        body["subjects"].append({"subject_id": "p2"})
    if case_id == "crossyear_self_instability":
        body["subjects"].append({"subject_id": "p2"})
        body["years"][_NEXT] = _next_year_like(_salary_year(), ("2026-04-01", "2027-04-01"))
    return body


@pytest.mark.parametrize(
    "case_id,kind,mutate,expected", _REFERENCE_CASES, ids=[c[0] for c in _REFERENCE_CASES]
)
@pytest.mark.parametrize("entry", _ENTRIES)
def test_002_reference_actor_exact_contradiction(
    parity, case_id: str, kind: str, mutate, expected: tuple, entry: str
) -> None:
    """悬空/非参与/participant-unmarried/自身配偶/跨年 self：精确
    E_INPUT_CONTRADICTORY＋注册引用定位（HTTP/纯独立参数化）。"""
    control = _reference_control(case_id)
    _assert_control_201(parity, kind, control, f"{case_id} 对照", entry=entry)
    parity.assert_no_service()

    body = _reference_control(case_id)
    mutate(body)
    _assert_entry_rejection(parity, kind, body, expected, case_id, entry)
    parity.assert_no_service()


# ---------------------------------------------------------------------------
# Money/Ratio canonical 与独立 hash 工作纸
# ---------------------------------------------------------------------------

_MONEY_KEYS = (
    "employment_income_nai",
    "mpf_mandatory_contributions",
    "rent_received",
    "irrecoverable_rent",
    "deposit_offsets",
    "mortgage_interest",
    "provisional_paid",
    "amount",
    "assessable_profit",
)


def _canon_money(value: str) -> str:
    decimal = Decimal(value)
    text = format(decimal.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canon_ratio(value: str) -> str:
    fraction = Fraction(value)
    return f"{fraction.numerator}/{fraction.denominator}"


def _canonical_form(node: Any) -> Any:
    """独立工作纸：按 005 §3 词法在测试侧做 Money/Ratio 规范化（不调用生产）；
    递归全部嵌套 dict/list（此前未递归 nested 值＝坏工作表，已在本次纠正）。"""
    if isinstance(node, list):
        return [_canonical_form(item) for item in node]
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if isinstance(value, str) and key in _MONEY_KEYS:
                result[key] = _canon_money(value)
            elif isinstance(value, str) and key == "share":
                result[key] = _canon_ratio(value)
            else:
                result[key] = _canonical_form(value)
        return result
    return node


def _canonical_json(node: Any) -> str:
    return json.dumps(
        _canonical_form(node),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hand_hash(node: Any) -> str:
    payload = _canonical_json(node).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_002_canonical_worksheet_self_control_explicit_golden() -> None:
    """工作表自检（不依赖服务端）：显式 golden canonical form/json/hash——
    覆盖嵌套 Money 尾零与 Ratio 未约简，防“双方同错”式假绿。"""
    sample = {
        "financials": {"employment_income_nai": "240000.00", "amount": "1.5000"},
        "shares": [{"share": "2/6"}, {"share": "2/2"}],
    }
    expected_canonical = {
        "financials": {"employment_income_nai": "240000", "amount": "1.5"},
        "shares": [{"share": "1/3"}, {"share": "1/1"}],
    }
    assert _canonical_form(sample) == expected_canonical, _canonical_form(sample)
    expected_json = json.dumps(
        expected_canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert _canonical_json(sample) == expected_json, _canonical_json(sample)
    assert _hand_hash(sample) == "sha256:" + hashlib.sha256(
        expected_json.encode("utf-8")
    ).hexdigest()


def test_002_canonical_money_tail_zero_same_hash(parity) -> None:
    """Money 尾零（"240000.00"）：服务端 input_hash 等于独立工作纸 canonical
    hash，且与 canonical 形式同 hash（递归工作纸纠正后的独立证据）。"""
    control = _salary_body()
    r_control = parity.prepare("salary", copy.deepcopy(control))
    assert r_control.status_code == 201, r_control.text
    assert r_control.json()["input_hash"] == _hand_hash(control), (
        "canonical 输入 hash 必须等于独立工作纸重算值"
    )
    parity.assert_no_service()

    zero = _salary_body()
    zero["years"][_YEAR]["financials"]["p1"]["employment_income_nai"] = "240000.00"
    r_zero = parity.prepare("salary", copy.deepcopy(zero))
    assert r_zero.status_code == 201, (
        f"尾零金额须接受并规范（005 §3），实际 {r_zero.status_code}："
        f"{r_zero.text[:200]!r}"
    )
    assert r_zero.json()["input_hash"] == _hand_hash(zero) == _hand_hash(control), (
        "尾零等价输入须与 canonical 形式同 hash（独立工作纸）"
    )
    parity.assert_no_service()


def test_002_canonical_ratio_unreduced_2_6_equals_1_3(parity) -> None:
    """Ratio 未约简 "2/6" 规范化 "1/3" → 与显式 "1/3" 同 canonical hash，
    并等于独立工作纸重算值。"""
    pa_a = _pa_body()
    pa_a["years"][_YEAR]["financials"]["p1"]["properties"][0]["share"] = "2/6"
    pa_b = _pa_body()
    pa_b["years"][_YEAR]["financials"]["p1"]["properties"][0]["share"] = "1/3"
    r_a = parity.prepare("pa", copy.deepcopy(pa_a))
    r_b = parity.prepare("pa", copy.deepcopy(pa_b))
    assert r_a.status_code == 201 and r_b.status_code == 201, (r_a.text, r_b.text)
    assert r_a.json()["input_hash"] == _hand_hash(pa_a), "2/6 须约简后入 hash"
    assert r_a.json()["input_hash"] == r_b.json()["input_hash"] == _hand_hash(pa_b), (
        "2/6 与 1/3 须同 canonical hash"
    )
    parity.assert_no_service()


def test_002_canonical_ratio_two_two_legal_ownership_equals_1_1(parity) -> None:
    """真实所有权 "2/2" 合法（0<num≤den）且规范化为 "1/1" → 与 "1/1" 同 hash。"""
    pa_two = _pa_body()
    pa_two["years"][_YEAR]["financials"]["p1"]["properties"][0]["share"] = "2/2"
    pa_one = _pa_body()
    r_two = parity.prepare("pa", copy.deepcopy(pa_two))
    r_one = parity.prepare("pa", copy.deepcopy(pa_one))
    assert r_two.status_code == 201, (
        f"2/2（≡1/1）为合法份额（0<num≤den），实际 {r_two.status_code}："
        f"{r_two.text[:200]!r}"
    )
    assert r_two.json()["input_hash"] == r_one.json()["input_hash"] == _hand_hash(pa_one)
    parity.assert_no_service()


@pytest.mark.parametrize("kind", ["salary", "pa"])
def test_002_pure_engine_does_not_mutate_input(kind: str) -> None:
    """纯引擎不得原地修改输入（REQ-2；规范化用副本）——immutability 断言，
    不以 envelope hash 变化替代。"""
    body = _salary_body() if kind == "salary" else _pa_body()
    snapshot = copy.deepcopy(body)
    if kind == "salary":
        _run_salary(body)
    else:
        _run_pa(body)
    assert body == snapshot, f"{kind}：纯引擎不得原地修改输入（规范化用副本）"
