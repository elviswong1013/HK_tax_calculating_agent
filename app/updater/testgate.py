"""候选门禁：独立参考、必需 case、隔离运行环境（Annex C C8；REQ-15/18）。

REQ: 15/18（§9：在候选 bundle 上运行受影响＋回归测试；参考参数只来自已批准
独立 source/proof；门禁在隔离环境运行；空报告/缺必需 case 禁止发布）。
规格锚点:
  - Annex C C8.1/C8.4/C8.6：独立参考、必需 case、报告形状。
  - Annex C C8.7 / 006 §7.2：上下文局部可信同步观测作用域——直接观察
    HTTPX/socket/DNS/session/pointer 写（含 CTE/cursor/直接 sqlite/
    executescript/dbapi2.connect/ATTACH/VACUUM INTO 等价路径）、越界 DB
    真实 I/O 前拒绝、新线程/子进程；
    其他已有线程的正常调用委托原实现；异常恢复；counter 来自观察器。
  - 005 §15.4：报告须带 binding/coverage/cases.rows/regression/gate_environment
    供 publisher 证明门核验。
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import os
import re
import socket
import sqlite3
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.core.hashing import bundle_content_hash, canonical_json_bytes
from app.rules.store import RuleStore

_GATE_ACTIVE: ContextVar[bool] = ContextVar("hktax_gate_observation_active", default=False)

_POINTER_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE | re.DOTALL
)

# SQLite 外部文件 I/O 出口（F3-2 等价出口）：gate 内一律拒绝，先于真实执行。
_SQLITE_FILE_IO_RES = (
    re.compile(r"\bATTACH\b", re.IGNORECASE),
    re.compile(r"\bVACUUM\b[\s\S]*?\bINTO\b", re.IGNORECASE),
)


class _CountingOutbound:
    """出站计数代理：门禁期间任何 fetch 尝试都被实际计数（C8.7 须为 0）。"""

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner
        self.calls = 0

    def fetch(self, url: str, **kwargs: Any) -> bytes:
        self.calls += 1
        if self._inner is None:
            raise ConnectionError(f"门禁运行环境不得访问网络（C8.7）：{url}")
        return self._inner.fetch(url, **kwargs)


def _accepts_outbound(func: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    if "outbound" in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _call_with_outbound(func: Callable[..., Any], *args: Any, outbound: Any) -> Any:
    if _accepts_outbound(func):
        return func(*args, outbound=outbound)
    return func(*args)


def _canonical_sha(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _subcase_expected_payload(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise TypeError("expected_subcases 必须为列表")
    payload: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("expected_subcases 元素必须为映射")
        subcase_id = entry.get("subcase_id")
        input_data = entry.get("input")
        expected_data = entry.get("expected")
        if not isinstance(subcase_id, str) or not subcase_id:
            raise KeyError("expected_subcases.subcase_id 缺失")
        if not isinstance(input_data, Mapping) or not isinstance(
            expected_data, Mapping
        ):
            raise TypeError("expected_subcases input/expected 必须为映射")
        payload.append(
            {
                "subcase_id": subcase_id,
                "input": copy.deepcopy(dict(input_data)),
                "expected": copy.deepcopy(dict(expected_data)),
            }
        )
    return payload


def _subcase_actual_payload(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise TypeError("actual_subcases 必须为列表")
    payload: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("actual_subcases 元素必须为映射")
        subcase_id = entry.get("subcase_id")
        input_data = entry.get("input")
        expected_data = entry.get("expected")
        actual_data = entry.get("actual")
        receipt = entry.get("receipt")
        passed = entry.get("passed")
        if not isinstance(subcase_id, str) or not subcase_id:
            raise KeyError("actual_subcases.subcase_id 缺失")
        if not isinstance(input_data, Mapping) or not isinstance(
            expected_data, Mapping
        ):
            raise TypeError("actual_subcases input/expected 必须为映射")
        if not isinstance(actual_data, Mapping) or not isinstance(receipt, Mapping):
            raise TypeError("actual_subcases actual/receipt 必须为映射")
        if not isinstance(passed, bool):
            raise TypeError("actual_subcases.passed 必须为布尔值")
        payload.append(
            {
                "subcase_id": subcase_id,
                "input": copy.deepcopy(dict(input_data)),
                "expected": copy.deepcopy(dict(expected_data)),
                "actual": copy.deepcopy(dict(actual_data)),
                "passed": passed,
                "receipt": copy.deepcopy(dict(receipt)),
            }
        )
    return payload


def _subcase_proof_fields(
    expected_payload: Sequence[Mapping[str, Any]],
    actual_payload: Sequence[Mapping[str, Any]],
    subcase_ids: Sequence[str],
) -> dict[str, Any]:
    expected_sha = _canonical_sha([dict(entry) for entry in expected_payload])
    actual_sha = _canonical_sha([dict(entry) for entry in actual_payload])
    ids = [str(value) for value in subcase_ids]
    combined_sha = _canonical_sha(
        {
            "expected_subcases_sha256": expected_sha,
            "actual_subcases_sha256": actual_sha,
            "subcase_ids": ids,
        }
    )
    return {
        "expected_subcases": [dict(entry) for entry in expected_payload],
        "actual_subcases": [dict(entry) for entry in actual_payload],
        "expected_subcases_sha256": expected_sha,
        "actual_subcases_sha256": actual_sha,
        "subcase_results_sha256": combined_sha,
    }


def _empty_subcase_proof_fields(subcase_ids: Sequence[str]) -> dict[str, Any]:
    return _subcase_proof_fields([], [], subcase_ids)


def _b5_subcases_match(
    expected_payload: Sequence[Mapping[str, Any]],
    actual_payload: Sequence[Mapping[str, Any]],
    plan_subcase_ids: Sequence[str],
) -> bool:
    """harness expected vs engine actual（金额/阶段/身份）；local passed 仅作收据。

    engine 对静态 B5_CASES 期望的 local ``passed=false`` 不单独构成隔离条件：
    AVD 等 proof 改变的是输出值而非边界输入时，engine 仍以静态 100 比较 110。
    gate 以独立 harness expected 与 engine actual 为准；仅当 engine 自身期望
    与 harness 期望一致且仍声明 passed=false 时，才视为该收据不可信。
    """
    plan_ids = [str(value) for value in plan_subcase_ids]
    expected_ids = [entry["subcase_id"] for entry in expected_payload]
    actual_ids = [entry["subcase_id"] for entry in actual_payload]
    if expected_ids != plan_ids or actual_ids != plan_ids:
        return False
    for expected_entry, actual_entry in zip(expected_payload, actual_payload):
        if expected_entry["subcase_id"] != actual_entry["subcase_id"]:
            return False
        if expected_entry["input"] != actual_entry["input"]:
            return False
        for key, value in expected_entry["expected"].items():
            if actual_entry["actual"].get(key) != value:
                return False
        receipt = actual_entry["receipt"]
        if receipt.get("subcase_id") != actual_entry["subcase_id"]:
            return False
        if not isinstance(receipt.get("engine"), str) or not receipt["engine"]:
            return False
        if receipt.get("input_sha256") != _canonical_sha(
            dict(actual_entry["input"])
        ):
            return False
        local_match = all(
            actual_entry["actual"].get(key) == value
            for key, value in actual_entry["expected"].items()
        )
        if actual_entry["passed"] != local_match:
            return False
        if (
            actual_entry["expected"] == expected_entry["expected"]
            and actual_entry["passed"] is not True
        ):
            return False
    return True


def _reference_code_sha256() -> str:
    """当前 reference.py 文件字节 SHA256（不是 canonical label）。"""
    from app.updater import reference as reference_module

    return "sha256:" + hashlib.sha256(
        Path(reference_module.__file__).read_bytes()
    ).hexdigest()


def _isolated_gate_store(
    approved_temp_root: str | os.PathLike[str] | None,
) -> tuple[str, int]:
    parent = Path(approved_temp_root) if approved_temp_root else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="hktax-gate-", dir=str(parent)))
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="hktax-gate-"))
    temp_store_path = str(temp_dir / "gate-store.db")
    gate_store = RuleStore(temp_store_path)
    conn = gate_store._connect()
    try:
        pointer_rows = int(
            conn.execute("SELECT COUNT(*) FROM current_pointer").fetchone()[0]
        )
    finally:
        conn.close()
    return temp_store_path, pointer_rows


def _is_pointer_write(sql: Any) -> bool:
    if not isinstance(sql, str):
        return False
    text = sql.lower()
    return "current_pointer" in text and bool(_POINTER_WRITE_RE.search(text))


def _is_sqlite_file_io(sql: Any) -> bool:
    """ATTACH / VACUUM INTO 是已允许连接上的越界文件 I/O 等价出口。"""
    if isinstance(sql, bytes):
        sql = sql.decode("utf-8", "surrogateescape")
    if not isinstance(sql, str):
        return False
    return any(pattern.search(sql) for pattern in _SQLITE_FILE_IO_RES)


class _GateCursor:
    """SQLite cursor proxy：所有 SQL 出口在真实执行前守卫 pointer 写/恢复。"""

    def __init__(
        self,
        cursor: Any,
        tracker: dict[str, Any],
        connection: "_GateConnection | None" = None,
    ) -> None:
        object.__setattr__(self, "_GateCursor__cursor", cursor)
        object.__setattr__(self, "_GateCursor__tracker", tracker)
        object.__setattr__(self, "_GateCursor__connection", connection)

    @property
    def connection(self) -> "_GateConnection | None":
        return self.__connection

    def _guard(self, sql: Any) -> None:
        if _is_pointer_write(sql):
            self.__tracker["pointer_write_attempts"] += 1
            raise sqlite3.OperationalError(
                "gate rejected current_pointer write before execution"
            )
        if _is_sqlite_file_io(sql):
            self.__tracker["outside_temp_db"] += 1
            raise sqlite3.OperationalError(
                "gate rejected SQLite file I/O (ATTACH/VACUUM INTO) before execution"
            )

    def _wrap(self, result: Any) -> Any:
        if isinstance(result, sqlite3.Cursor):
            return _GateCursor(result, self.__tracker, self.__connection)
        return result

    def execute(self, sql, params=()):
        self._guard(sql)
        return self._wrap(self.__cursor.execute(sql, params))

    def executemany(self, sql, seq_of_params):
        self._guard(sql)
        return self._wrap(self.__cursor.executemany(sql, seq_of_params))

    def executescript(self, script):
        self._guard(script)
        return self._wrap(self.__cursor.executescript(script))

    def __getattr__(self, name):
        if name in {"connection", "cursor"}:
            raise AttributeError(
                f"gate cursor does not expose raw {name!r}"
            )
        return getattr(self.__cursor, name)

    def __iter__(self):
        return iter(self.__cursor)


class _GateConnection:
    """SQLite connection proxy：拒绝 current_pointer 写（不产生可见副作用）。"""

    def __init__(self, conn: Any, tracker: dict[str, Any]) -> None:
        object.__setattr__(self, "_GateConnection__conn", conn)
        object.__setattr__(self, "_GateConnection__tracker", tracker)

    def _guard(self, sql: Any) -> None:
        if _is_pointer_write(sql):
            self.__tracker["pointer_write_attempts"] += 1
            raise sqlite3.OperationalError(
                "gate rejected current_pointer write before execution"
            )
        if _is_sqlite_file_io(sql):
            self.__tracker["outside_temp_db"] += 1
            raise sqlite3.OperationalError(
                "gate rejected SQLite file I/O (ATTACH/VACUUM INTO) before execution"
            )

    def execute(self, sql, params=()):
        self._guard(sql)
        return _GateCursor(
            self.__conn.execute(sql, params), self.__tracker, self
        )

    def executemany(self, sql, seq_of_params):
        self._guard(sql)
        return _GateCursor(
            self.__conn.executemany(sql, seq_of_params), self.__tracker, self
        )

    def executescript(self, script):
        self._guard(script)
        return _GateCursor(
            self.__conn.executescript(script), self.__tracker, self
        )

    def cursor(self, *args, **kwargs):
        return _GateCursor(
            self.__conn.cursor(*args, **kwargs), self.__tracker, self
        )

    def __getattr__(self, name):
        if name in {"connection", "cursor"}:
            raise AttributeError(
                f"gate connection does not expose raw {name!r}"
            )
        return getattr(self.__conn, name)

    def __enter__(self):
        self.__conn.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self.__conn.__exit__(*exc_info)


@contextmanager
def gate_observation_scope(
    *,
    store: Any = None,
    approved_temp_root: Any = None,
    temp_store_path: str | None = None,
):
    """上下文局部可信同步观测作用域；退出/异常一律复原原实现。"""
    tracker: dict[str, Any] = {
        "direct_http_attempts": 0,
        "socket_attempts": 0,
        "dns_attempts": 0,
        "session_creations": 0,
        "pointer_write_attempts": 0,
        "outside_temp_db": 0,
        "thread_starts": 0,
        "subprocess_starts": 0,
        "restore_problems": [],
    }
    gate_temp_dir = (
        Path(temp_store_path).resolve().parent if temp_store_path else None
    )
    current_store_path: Path | None = None
    if store is not None:
        raw = getattr(store, "_path", store)
        try:
            current_store_path = Path(str(raw)).resolve()
        except (OSError, TypeError):
            current_store_path = None

    patches: list[tuple[Any, str, Any, Any]] = []

    def _patch(owner: Any, attr: str, replacement: Any) -> None:
        original = getattr(owner, attr)
        patches.append((owner, attr, original, replacement))
        setattr(owner, attr, replacement)

    def _active() -> bool:
        return bool(_GATE_ACTIVE.get())

    try:
        import httpx

        original_http = httpx.HTTPTransport.handle_request

        def _observed_http(transport_self, request):
            if not _active():
                return original_http(transport_self, request)
            tracker["direct_http_attempts"] += 1
            raise AssertionError(
                "门禁运行环境不得直接 HTTPX 出站（C8.7；绕过 outbound 亦拒绝）"
            )

        _patch(httpx.HTTPTransport, "handle_request", _observed_http)

        original_async_http = httpx.AsyncHTTPTransport.handle_async_request

        async def _observed_async_http(transport_self, request):
            if not _active():
                return await original_async_http(transport_self, request)
            tracker["direct_http_attempts"] += 1
            raise AssertionError(
                "门禁运行环境不得直接 HTTPX 出站（C8.7；async 亦拒绝）"
            )

        _patch(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            _observed_async_http,
        )

        original_socket = socket.socket

        class _ObservedSocket:
            def __init__(self, *args, **kwargs):
                if not _active():
                    self._inner = original_socket(*args, **kwargs)
                    return
                tracker["socket_attempts"] += 1
                raise AssertionError("门禁运行环境不得创建 socket（C8.7）")

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, *exc_info):
                return self._inner.__exit__(*exc_info)

        _patch(socket, "socket", _ObservedSocket)

        original_dns = socket.getaddrinfo

        def _observed_dns(*args, **kwargs):
            if not _active():
                return original_dns(*args, **kwargs)
            tracker["dns_attempts"] += 1
            raise AssertionError("门禁运行环境不得进行 DNS 解析（C8.7）")

        _patch(socket, "getaddrinfo", _observed_dns)

        from app.web.security import SessionRegistry

        original_ensure = SessionRegistry.ensure_session

        def _observed_session(registry_self, *args, **kwargs):
            if not _active():
                return original_ensure(registry_self, *args, **kwargs)
            tracker["session_creations"] += 1
            raise AssertionError(
                "门禁运行环境不得创建用户 session（C8.7；真实 registry 无副作用）"
            )

        _patch(SessionRegistry, "ensure_session", _observed_session)

        original_thread_start = threading.Thread.start

        def _observed_thread_start(thread_self):
            if not _active():
                return original_thread_start(thread_self)
            tracker["thread_starts"] += 1
            raise AssertionError("门禁运行环境不得创建新线程（C8.7）")

        _patch(threading.Thread, "start", _observed_thread_start)

        original_popen = subprocess.Popen

        def _observed_popen(*args, **kwargs):
            if not _active():
                return original_popen(*args, **kwargs)
            tracker["subprocess_starts"] += 1
            raise AssertionError("门禁运行环境不得创建子进程（C8.7）")

        _patch(subprocess, "Popen", _observed_popen)

        original_init = RuleStore.__init__

        def _observed_init(store_self, db_path):
            if not _active():
                return original_init(store_self, db_path)
            try:
                resolved = Path(str(db_path)).resolve()
            except (OSError, TypeError):
                resolved = None
            if gate_temp_dir is not None and (
                resolved is None
                or not (
                    resolved == gate_temp_dir or gate_temp_dir in resolved.parents
                )
            ):
                tracker["outside_temp_db"] += 1
                raise PermissionError(
                    "gate rejected RuleStore outside the gate temporary directory "
                    "before any real I/O"
                )
            return original_init(store_self, db_path)

        _patch(RuleStore, "__init__", _observed_init)

        original_connect = sqlite3.connect

        def _observed_sqlite_connect(database, *args, **kwargs):
            if not _active():
                return original_connect(database, *args, **kwargs)
            database_text = (
                database.decode("utf-8", "surrogateescape")
                if isinstance(database, bytes)
                else str(database)
            )
            if database_text in {":memory:", ""} or database_text.startswith(
                "file::memory:"
            ):
                # 允许连接但必须统一观察：ATTACH/VACUUM INTO 等文件 I/O 出口
                # 在真实执行前拒绝并计数（OR-2）。
                return _GateConnection(
                    original_connect(database, *args, **kwargs), tracker
                )
            try:
                resolved = Path(database_text).resolve()
            except (OSError, TypeError):
                resolved = None
            if current_store_path is not None and resolved == current_store_path:
                return _GateConnection(
                    original_connect(database, *args, **kwargs), tracker
                )
            if gate_temp_dir is None:
                return original_connect(database, *args, **kwargs)
            if resolved is not None and (
                resolved == gate_temp_dir or gate_temp_dir in resolved.parents
            ):
                return _GateConnection(
                    original_connect(database, *args, **kwargs), tracker
                )
            tracker["outside_temp_db"] += 1
            raise PermissionError(
                "gate rejected sqlite connection outside the gate temporary "
                "directory before any real I/O"
            )

        # F3-2 等价出口：dbapi2.connect 与 sqlite3.connect 是不同模块属性，
        # 必须分别包装；否则 gate 内可绕过越界 DB 拒绝。
        _patch(sqlite3, "connect", _observed_sqlite_connect)
        _patch(sqlite3.dbapi2, "connect", _observed_sqlite_connect)

        token = _GATE_ACTIVE.set(True)
        try:
            yield tracker
        finally:
            _GATE_ACTIVE.reset(token)
    finally:
        for owner, attr, original, installed in reversed(patches):
            try:
                if getattr(owner, attr, None) is not installed:
                    tracker["restore_problems"].append(
                        f"{getattr(owner, '__name__', owner)!s}.{attr} 已被替换"
                    )
                setattr(owner, attr, original)
            except BaseException as exc:  # noqa: BLE001
                tracker["restore_problems"].append(f"{attr} 恢复异常：{exc!r}")


def _pointer_bundle_id(db_path: str | None) -> str | None:
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT bundle_id FROM current_pointer WHERE id = 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return str(row[0]) if row else None


def _required_from_map(
    *,
    required_cases: Sequence[str],
    changed_paths: Sequence[str] | None,
    required_case_map: Mapping[str, str] | None,
) -> tuple[list[str], bool, bool]:
    supplied = [str(case_id) for case_id in required_cases]
    if required_case_map is None:
        return supplied, False, False
    recomputed: list[str] = []
    coverage_gap = False
    for path in changed_paths or ():
        case_id = required_case_map.get(str(path))
        if not case_id:
            coverage_gap = True
            continue
        recomputed.append(str(case_id))
    unique = sorted(set(recomputed))
    reduced = sorted(set(supplied)) != unique
    return unique, coverage_gap, reduced


def _profile_rows(content: Mapping[str, Any]) -> list[dict[str, str]]:
    frozen = content.get("frozen_semantics")
    profiles = frozen.get("profiles") if isinstance(frozen, Mapping) else []
    rows: list[dict[str, str]] = []
    for profile in profiles or ():
        if not isinstance(profile, Mapping):
            raise ValueError("candidate frozen profile shape invalid")
        digest = profile.get("semantic_digest") or _canonical_sha(dict(profile))
        rows.append(
            {
                "profile_id": str(profile.get("profile_id")),
                "semantic_digest": str(digest),
            }
        )
    return rows


def run_gate(
    *,
    candidate_content: Mapping[str, Any],
    current_content: Mapping[str, Any],
    manifests: Sequence[Mapping[str, Any]],
    independent_facts: Sequence[Mapping[str, Any]],
    required_cases: Sequence[str],
    harness: Any,
    engine: Any,
    outbound: Any = None,
    candidate_hash: str | None = None,
    candidate_profile: str | None = None,
    case_identity: Mapping[str, str] | None = None,
    report_id: str | None = None,
    consumed_report_ids: Sequence[str] = (),
    changed_paths: Sequence[str] | None = None,
    required_case_map: Mapping[str, str] | None = None,
    purpose: str | None = None,
    baseline: Mapping[str, Any] | None = None,
    current_store_db: str | None = None,
    approved_temp_root: str | None = None,
) -> dict[str, Any]:
    """隔离门禁：产出 C8.6 形状报告；任何不满足 → quarantine（fail closed）。"""
    from app.config import ENGINE_VERSION
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.updater.reference import B5_CASES, REQUIRED_CASES

    counter = _CountingOutbound(outbound)
    temp_store_path, temp_pointer_rows = _isolated_gate_store(approved_temp_root)
    pointer_before = _pointer_bundle_id(current_store_db)
    required, coverage_gap, reduced = _required_from_map(
        required_cases=required_cases,
        changed_paths=changed_paths,
        required_case_map=required_case_map,
    )
    fact_ids = {
        fact.get("fact_id")
        for fact in (independent_facts or [])
        if isinstance(fact, Mapping)
    }
    try:
        before_hash = bundle_content_hash(candidate_content)
    except (TypeError, ValueError):
        before_hash = None

    executed = 0
    case_ok = 0
    stage_ok_all = True
    evidence_ok_all = True
    evidence_refs: list[Any] = []
    stage_failed_ids: list[str] = []
    subcase_failed_ids: list[str] = []
    missing_cases: list[str] = []
    missing_evidence_cases: list[str] = []
    execution_failures: list[str] = []
    case_rows: list[dict[str, Any]] = []
    per_case: dict[str, dict[str, Any]] = {}
    static_failed: list[str] = []
    static_passed = 0

    tracker: dict[str, Any] = {}
    try:
        with gate_observation_scope(
            store=current_store_db,
            approved_temp_root=approved_temp_root,
            temp_store_path=temp_store_path,
        ) as scope:
            tracker = scope
            for case_id in required:
                is_b5_case = str(case_id) in B5_CASES
                plan = B5_CASES.get(str(case_id)) or {}
                subcase_ids = list(plan.get("subcase_ids") or [])
                row: dict[str, Any] = {
                    "case_id": str(case_id),
                    "mode": "candidate",
                    "input_proof_sha256": _canonical_sha(
                        {"case_id": str(case_id), "subcase_ids": subcase_ids}
                    ),
                    "subcase_ids": subcase_ids,
                    "worksheet_sha256": _canonical_sha(
                        {"case_id": str(case_id), "path": plan.get("path")}
                    ),
                    "parameter_evidence_refs": [],
                    "stage_match": False,
                    "passed": False,
                }
                if is_b5_case:
                    row.update(_empty_subcase_proof_fields(subcase_ids))
                try:
                    expected = _call_with_outbound(
                        harness.run_case, case_id, outbound=counter
                    )
                except KeyError:
                    missing_cases.append(str(case_id))
                    case_rows.append(row)
                    continue
                except Exception as exc:  # noqa: BLE001
                    execution_failures.append(f"{case_id}:{type(exc).__name__}")
                    case_rows.append(row)
                    continue
                executed += 1
                reference = (
                    expected.get("parameter_evidence_ref")
                    if isinstance(expected, Mapping)
                    else None
                )
                if reference is not None:
                    evidence_refs.append(reference)
                evidence_ok = reference is not None and reference in fact_ids
                if not evidence_ok:
                    evidence_ok_all = False
                    missing_evidence_cases.append(str(case_id))
                else:
                    row["parameter_evidence_refs"] = [str(reference)]
                try:
                    actual = _call_with_outbound(
                        engine.run_case,
                        case_id,
                        candidate_content,
                        outbound=counter,
                    )
                except Exception as exc:  # noqa: BLE001
                    execution_failures.append(
                        f"engine:{case_id}:{type(exc).__name__}"
                    )
                    actual = None
                stage_ok = (
                    isinstance(expected, Mapping)
                    and isinstance(actual, Mapping)
                    and expected.get("stages") == actual.get("stages")
                    and expected.get("output") == actual.get("output")
                )
                subcase_ok = True
                if is_b5_case:
                    expected_entries = (
                        expected.get("subcase_results")
                        if isinstance(expected, Mapping)
                        else None
                    )
                    actual_entries = (
                        actual.get("subcase_results")
                        if isinstance(actual, Mapping)
                        else None
                    )
                    try:
                        expected_payload = _subcase_expected_payload(
                            expected_entries
                        )
                        actual_payload = _subcase_actual_payload(actual_entries)
                    except (KeyError, TypeError, ValueError):
                        subcase_ok = False
                    else:
                        row.update(
                            _subcase_proof_fields(
                                expected_payload,
                                actual_payload,
                                subcase_ids,
                            )
                        )
                        subcase_ok = _b5_subcases_match(
                            expected_payload, actual_payload, subcase_ids
                        )
                    if not subcase_ok:
                        subcase_failed_ids.append(str(case_id))
                combined_ok = bool(stage_ok and subcase_ok)
                if not combined_ok:
                    stage_ok_all = False
                    if str(case_id) not in stage_failed_ids:
                        stage_failed_ids.append(str(case_id))
                row["stage_match"] = combined_ok
                row["passed"] = bool(combined_ok and evidence_ok)
                if combined_ok and evidence_ok:
                    case_ok += 1
                case_rows.append(row)
                per_case[str(case_id)] = {"expected": expected, "actual": actual}

            # Static regression runs independent of injected candidate seams:
            # production ReferenceHarness/CandidateEngine against the full release
            # baseline; never counted as affected and never receives test fakes.
            from app.updater.reference import (
                CandidateEngine as _StaticCandidateEngine,
                ReferenceHarness as _StaticReferenceHarness,
            )

            static_harness = _StaticReferenceHarness()
            static_engine = _StaticCandidateEngine()
            static_failed = []
            static_passed = 0
            for case_id in REQUIRED_CASES:
                try:
                    expected_static = static_harness.run_case(case_id)
                    actual_static = static_engine.run_case(
                        case_id, candidate_content
                    )
                    ok = (
                        isinstance(expected_static, Mapping)
                        and isinstance(actual_static, Mapping)
                        and expected_static.get("stages") == actual_static.get("stages")
                        and expected_static.get("output") == actual_static.get("output")
                    )
                except Exception:  # noqa: BLE001
                    ok = False
                if ok:
                    static_passed += 1
                else:
                    static_failed.append(str(case_id))
    except Exception as exc:  # noqa: BLE001
        execution_failures.append(f"gate:{type(exc).__name__}")

    try:
        after_hash = bundle_content_hash(candidate_content)
    except (TypeError, ValueError):
        after_hash = None
    candidate_replaced = (
        before_hash is not None
        and after_hash is not None
        and before_hash != after_hash
    )
    expected_hash = candidate_hash or before_hash
    hash_mismatch = (
        candidate_hash is not None
        and before_hash is not None
        and candidate_hash != before_hash
    )
    profile_ok = True
    if candidate_profile is not None:
        profiles = {
            str(slot.get("profile_ID"))
            for manifest in manifests or ()
            for slot in (manifest.get("slots") or [])
            if isinstance(slot, Mapping)
        }
        frozen = (candidate_content.get("frozen_semantics") or {}).get("profiles") or []
        profiles.update(
            str(profile.get("profile_id"))
            for profile in frozen
            if isinstance(profile, Mapping)
        )
        profile_ok = candidate_profile in profiles

    reasons: list[str] = []
    if candidate_replaced or hash_mismatch:
        reasons.append("candidate_hash_mismatch")
    if not profile_ok:
        reasons.append("candidate_profile_mismatch")
    if report_id is not None and report_id in set(consumed_report_ids or ()):
        reasons.append("report_reuse")
    if coverage_gap:
        reasons.append("coverage_gap")
    if reduced:
        reasons.append("required_case_set_mismatch")
    if not required:
        reasons.append("empty_report")
    if missing_cases:
        reasons.append("missing_required_case")
    if not stage_ok_all:
        reasons.append("stage_inconsistent")
    if subcase_failed_ids:
        reasons.append("subcase_mismatch")
    if not evidence_ok_all:
        reasons.append("missing_parameter_evidence")
    if stage_failed_ids or static_failed:
        reasons.append("regression_failure")
    if execution_failures:
        reasons.append("gate_exception")
    if required and case_ok == 0 and "empty_report" not in reasons:
        reasons.append("zero_passed")
    if counter.calls > 0:
        reasons.append("network_violation")
    observed = tracker or {}
    if observed.get("direct_http_attempts", 0) > 0:
        reasons.append("direct_http_violation")
    if observed.get("socket_attempts", 0) > 0:
        reasons.append("socket_violation")
    if observed.get("dns_attempts", 0) > 0:
        reasons.append("dns_violation")
    if observed.get("session_creations", 0) > 0:
        reasons.append("session_violation")
    if observed.get("pointer_write_attempts", 0) > 0:
        reasons.append("pointer_write_violation")
    if observed.get("outside_temp_db", 0) > 0:
        reasons.append("outside_temp_db")
    if observed.get("thread_starts", 0) > 0:
        reasons.append("thread_creation_violation")
    if observed.get("subprocess_starts", 0) > 0:
        reasons.append("subprocess_creation_violation")
    restored = tracker is not None and not observed.get("restore_problems")
    if not restored:
        reasons.append("observers_not_restored")

    pointer_after = _pointer_bundle_id(current_store_db)
    current_pointer_written = temp_pointer_rows > 0 or (
        current_store_db is not None and pointer_after != pointer_before
    )
    if current_pointer_written:
        reasons.append("pointer_write_violation")

    decision = "publish" if not reasons else "quarantine"
    candidate_binding_hash = expected_hash
    manifest_rows = [dict(manifest) for manifest in manifests or ()]
    evidence_rows = [dict(fact) for fact in (independent_facts or ())]
    binding = {
        "baseline_bundle_id": (
            baseline.get("bundle_id") if isinstance(baseline, Mapping) else None
        ),
        "baseline_bundle_hash": (
            baseline.get("bundle_hash") if isinstance(baseline, Mapping) else None
        ),
        "baseline_rules_schema_version": (
            baseline.get("rules_schema_version")
            if isinstance(baseline, Mapping)
            else None
        ),
        "candidate_bundle_hash": candidate_binding_hash,
        "engine_version": ENGINE_VERSION,
        "rules_schema_version": str(candidate_content.get("rules_schema_version")),
        "profiles": _profile_rows(candidate_content),
        "manifests_sha256": _canonical_sha(manifest_rows),
        "evidence_sha256": _canonical_sha(evidence_rows),
        "reference_code_sha256": _reference_code_sha256(),
        "required_case_ids": list(required),
    }
    gate_environment = {
        "temp_store_path": temp_store_path,
        "network_calls": counter.calls,
        "direct_http_attempts": int(observed.get("direct_http_attempts", 0)),
        "socket_attempts": int(observed.get("socket_attempts", 0)),
        "dns_attempts": int(observed.get("dns_attempts", 0)),
        "session_creations": int(observed.get("session_creations", 0)),
        "pointer_write_attempts": int(observed.get("pointer_write_attempts", 0)),
        "observers_restored": bool(restored),
        "user_session": bool(observed.get("session_creations", 0) > 0),
        "current_pointer_written": bool(current_pointer_written),
        "outside_temp_db": int(observed.get("outside_temp_db", 0)),
        "thread_starts": int(observed.get("thread_starts", 0)),
        "subprocess_starts": int(observed.get("subprocess_starts", 0)),
    }
    return {
        "decision": decision,
        "quarantine_reason": ";".join(reasons) if reasons else None,
        "purpose": purpose,
        "binding": binding,
        "coverage": {
            "changed_paths": sorted(str(path) for path in (changed_paths or ())),
            "affected_case_ids": sorted(
                str(case_id) for case_id in set((required_case_map or {}).values())
            ),
            "static_case_ids": list(REQUIRED_CASES),
        },
        "cases": {
            "required_case_ids": list(required),
            "executed": executed,
            "per_stage_consistent": bool(required) and stage_ok_all,
            "parameter_evidence_refs": evidence_refs,
            "rows": case_rows,
            "case_identity": dict(case_identity or {}),
        },
        "regression": {
            "static_case_ids": list(REQUIRED_CASES),
            "static_baseline_bundle_hash": bundle_content_hash(INITIAL_BUNDLE_CONTENT),
            "passed": static_passed,
            "failed": len(static_failed),
            "failed_ids": sorted(static_failed),
        },
        "tests": {
            "passed": case_ok,
            "failed": len(required) - case_ok,
            "fail_reasons": reasons,
            "fail_ids": sorted(
                set(
                    missing_cases
                    + missing_evidence_cases
                    + stage_failed_ids
                    + subcase_failed_ids
                    + execution_failures
                )
            ),
            "gate": (
                "passed>0 且必需 case 全执行＋参数证据＋逐阶段一致＋回归 failed=0；"
                "空报告=隔离（Annex C C8.4）"
            ),
        },
        "gate_environment": gate_environment,
    }
