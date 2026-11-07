"""P3-W1a REQ-12/13：门禁/发布/Resolution 证明负例十四（006 §7.2）。

REQ: REQ-12/13（005 §15.4；006:314–315/§7.2）——门禁不采信生产者自证：
candidate hash、report 复用、required 重算、候选数据替换、未覆盖路径、pointer
写-回滚、直接 HTTPX/socket/DNS/session、临时 DB 越界、观察器恢复/非 gate
委托；发布 seed empty-CAS/update baseline CAS；Resolution baseline 不可复用。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - `run_gate(...)` 增量可信入参：
    `candidate_hash`、`report_id`/`consumed_report_ids`、
    `changed_paths`/`required_case_map`、`current_store_db`、
    `approved_temp_root`；报告 `gate_environment` 增直接观察计数
    （direct_http_attempts/socket_attempts/dns_attempts/session_creations/
    pointer_write_attempts）与 `observers_restored`。
  - `publish_update(..., purpose="seed"|"update", baseline=..., manifests=...,
    required_cases=...)`：seed 仅 true-empty＋baseline 两项 null；update
    baseline 非 null 且事务内 CAS；任何不符零切换/零部分写。
  - `app.updater.observer.resolve_observer_pending(...)`：Resolution 的
    expected_current_bundle_id/hash 不可复用旧 baseline。

Red 失败原因（当前）：上述入参/字段/API 不存在；门禁无直接观察器。
"""

from __future__ import annotations

import copy
import hashlib
import socket
import sqlite3
import threading
from pathlib import Path

import httpx
import pytest

from app.core.errors import AppError

from _fakes import (
    AVD_CHANGED,
    AVD_FULLPATH,
    B5_REQUIRED_CASE,
    FACT_AVD_110,
    AVD_MANIFEST,
    avd_changed_bundle_content,
    empty_b5_subcase_fields,
    initial_content,
    seed_published_store,
)


def _quiet_seams():
    class _Harness:
        def run_case(self, case_id: str, **kwargs):
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    class _Engine:
        def run_case(self, case_id: str, candidate_content, **kwargs):
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                **empty_b5_subcase_fields(),
            }

    return _Harness(), _Engine()


def _run_gate(harness=None, engine=None, **extra):
    from app.updater.testgate import run_gate

    if harness is None or engine is None:
        harness, engine = _quiet_seams()
    kwargs = {
        "candidate_content": avd_changed_bundle_content(),
        "current_content": initial_content(),
        "manifests": [AVD_MANIFEST],
        "independent_facts": [FACT_AVD_110],
        "required_cases": [B5_REQUIRED_CASE],
        "harness": harness,
        "engine": engine,
    }
    kwargs.update(extra)
    return run_gate(**kwargs)


def _canonical_sha(payload) -> str:
    from app.core.hashing import canonical_json_bytes

    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _reference_code_sha256() -> str:
    """运行时读取当前 reference.py 文件字节（不得硬编码/不得 canonical label）。"""
    from app.updater import reference

    return "sha256:" + hashlib.sha256(
        Path(reference.__file__).read_bytes()
    ).hexdigest()


def _profile_bindings(content) -> list[dict]:
    return [
        {
            "profile_id": str(profile["profile_id"]),
            "semantic_digest": str(
                profile.get("semantic_digest") or _canonical_sha(profile)
            ),
        }
        for profile in content["frozen_semantics"]["profiles"]
    ]


def _seed_report(
    required_cases=None,
    *,
    manifests=(),
    evidence_refs=(),
) -> dict:
    """有效 seed 证明：绑定冻结发行 candidate/manifest/profile/proof/engine。

    空-CAS 合法语义仍由 store 事务内 true-empty 复核；本报告只是补齐
    F2-1 要求的发行证明字段，不绕过 empty-CAS。
    """
    from app.config import ENGINE_VERSION
    from app.core.hashing import bundle_content_hash
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.updater.reference import REQUIRED_CASES

    release = copy.deepcopy(INITIAL_BUNDLE_CONTENT)
    required = list(REQUIRED_CASES if required_cases is None else required_cases)
    manifests = [dict(manifest) for manifest in manifests]
    evidence = [dict(fact) for fact in evidence_refs]
    return {
        "decision": "publish",
        "purpose": "seed",
        "binding": {
            "baseline_bundle_id": None,
            "baseline_bundle_hash": None,
            "baseline_rules_schema_version": None,
            "candidate_bundle_hash": bundle_content_hash(release),
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": str(release.get("rules_schema_version")),
            "profiles": _profile_bindings(release),
            "manifests_sha256": _canonical_sha(manifests),
            "evidence_sha256": _canonical_sha(evidence),
            "reference_code_sha256": _reference_code_sha256(),
            "required_case_ids": required,
        },
        "cases": {
            "required_case_ids": required,
            "executed": len(required),
        },
        "tests": {"passed": len(required), "failed": 0, "failed_ids": []},
        "quarantine_reason": None,
    }


def _update_report(baseline: dict, required_cases=(B5_REQUIRED_CASE,)) -> dict:
    return {
        "decision": "publish",
        "purpose": "update",
        "binding": {
            "baseline_bundle_id": baseline["bundle_id"],
            "baseline_bundle_hash": baseline["bundle_hash"],
            "baseline_rules_schema_version": baseline["rules_schema_version"],
            "required_case_ids": list(required_cases),
        },
        "cases": {
            "required_case_ids": list(required_cases),
            "executed": len(required_cases),
        },
        "tests": {"passed": 3, "failed": 0, "failed_ids": []},
        "quarantine_reason": None,
    }


def test_002_gate_rejects_wrong_candidate_hash() -> None:
    report = _run_gate(
        candidate_hash="sha256:" + "00" * 32,
        changed_paths=[AVD_FULLPATH],
        required_case_map={AVD_FULLPATH: B5_REQUIRED_CASE},
    )
    assert report["decision"] == "quarantine"
    assert "hash" in (report.get("quarantine_reason") or "").lower()


def test_002_gate_rejects_report_reuse() -> None:
    report = _run_gate(
        report_id="report-002-p3-reused",
        consumed_report_ids=("report-002-p3-reused",),
        changed_paths=[AVD_FULLPATH],
        required_case_map={AVD_FULLPATH: B5_REQUIRED_CASE},
    )
    assert report["decision"] == "quarantine"
    assert "report" in (report.get("quarantine_reason") or "").lower()


def test_002_gate_recomputes_required_set() -> None:
    from app.core.hashing import bundle_content_hash

    kwargs = {
        "candidate_content": avd_changed_bundle_content(),
        "current_content": initial_content(),
        "manifests": [AVD_MANIFEST],
        "independent_facts": [FACT_AVD_110],
        "candidate_hash": bundle_content_hash(avd_changed_bundle_content()),
        "changed_paths": [AVD_FULLPATH],
        "required_case_map": {AVD_FULLPATH: B5_REQUIRED_CASE},
        "required_cases": [],
    }
    report = _run_gate(**kwargs)
    assert report["decision"] == "quarantine", (
        "服务器须按 purpose/diff 重算 required；报告/调用方不能自减为空"
    )
    assert report.get("quarantine_reason")


def test_002_gate_detects_candidate_data_replacement() -> None:
    class _ReplacingEngine:
        def run_case(self, case_id: str, candidate_content, **kwargs):
            candidate_content["data"]["stamps"]["property"][
                "related_conveyance"
            ]["fixed_duty"] = "999"
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                **empty_b5_subcase_fields(),
            }

    harness, _engine = _quiet_seams()
    report = _run_gate(harness=harness, engine=_ReplacingEngine())
    assert report["decision"] == "quarantine", (
        "引擎就地替换 candidate.data → run 前后 hash 不符必须隔离"
    )
    assert report.get("quarantine_reason")


def test_002_gate_rejects_uncovered_changed_path() -> None:
    report = _run_gate(
        changed_paths=["property.repair_allowance_ratio"],
        required_case_map={AVD_FULLPATH: B5_REQUIRED_CASE},
        required_cases=[B5_REQUIRED_CASE],
    )
    assert report is not None
    assert report["decision"] == "quarantine", (
        "变更路径无 affected case 覆盖 → coverage_gap/隔离，不得发布"
    )
    assert report.get("quarantine_reason")


def test_002_gate_detects_pointer_write_then_restore(tmp_path) -> None:
    db_path = tmp_path / "gate-pointer.db"
    store = seed_published_store(tmp_path, "gate-pointer.db")
    before = store.current()["bundle_id"]
    observed: dict = {}

    class _PointerWriteHarness:
        def run_case(self, case_id: str, **kwargs):
            probe = store._connect()
            try:
                try:
                    probe.execute(
                        "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                        ("b-tampered",),
                    )
                    observed["after_write"] = probe.execute(
                        "SELECT bundle_id FROM current_pointer WHERE id = 1"
                    ).fetchone()[0]
                    if observed["after_write"] == "b-tampered":
                        probe.execute(
                            "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                            (before,),
                        )
                except Exception as exc:
                    observed["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                probe.close()
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_PointerWriteHarness(),
        engine=engine,
        current_store_db=str(db_path),
    )
    assert report["decision"] == "quarantine", (
        "pointer 写后恢复也必须被写尝试日志识别（C8.7）"
    )
    assert (
        report["gate_environment"].get("pointer_write_attempts") or 0
    ) >= 1
    assert observed.get("after_write") in (None, before), (
        "gate must reject the pointer write before it becomes visible; "
        f"observed={observed!r}"
    )
    assert store.current()["bundle_id"] == before


def _raw_pointer(db_path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return str(
            conn.execute(
                "SELECT bundle_id FROM current_pointer WHERE id = 1"
            ).fetchone()[0]
        )
    finally:
        conn.close()


def test_002_gate_detects_cte_pointer_write(tmp_path) -> None:
    """F3：等价 SQL（WITH ... UPDATE）写 pointer 也必须被观察/拒绝。"""
    db_path = tmp_path / "gate-cte.db"
    store = seed_published_store(tmp_path, "gate-cte.db")
    before = _raw_pointer(db_path)

    class _CteHarness:
        def run_case(self, case_id: str, **kwargs):
            conn = store._connect()
            try:
                try:
                    conn.execute(
                        "WITH one AS (SELECT 1)"
                        " UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                        ("b-tampered",),
                    )
                except Exception:
                    pass
            finally:
                conn.close()
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_CteHarness(),
        engine=engine,
        current_store_db=str(db_path),
    )
    assert report["decision"] == "quarantine", (
        "CTE UPDATE current_pointer must not bypass SQL write observation"
    )
    assert (
        report["gate_environment"].get("pointer_write_attempts") or 0
    ) >= 1
    assert _raw_pointer(db_path) == before, (
        "gate must reject/restore CTE pointer write; pointer unchanged"
    )


def test_002_gate_detects_direct_sqlite_pointer_write(tmp_path) -> None:
    """F3：绕过 RuleStore 的直接 sqlite3 写 pointer 也必须被观察/拒绝。"""
    db_path = tmp_path / "gate-direct-sqlite.db"
    seed_published_store(tmp_path, "gate-direct-sqlite.db")
    before = _raw_pointer(db_path)

    class _DirectSqliteHarness:
        def run_case(self, case_id: str, **kwargs):
            conn = sqlite3.connect(db_path)
            try:
                try:
                    conn.execute(
                        "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                        ("b-tampered",),
                    )
                    conn.commit()
                except Exception:
                    pass
            finally:
                conn.close()
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_DirectSqliteHarness(),
        engine=engine,
        current_store_db=str(db_path),
    )
    assert report["decision"] == "quarantine", (
        "direct sqlite3 current_pointer write must not bypass gate observation"
    )
    assert _raw_pointer(db_path) == before, (
        "gate must reject/restore direct sqlite pointer write; pointer unchanged"
    )


def test_002_gate_detects_cursor_executescript_pointer_write_restore(tmp_path) -> None:
    """F3-1：cursor.executescript 写/恢复 current_pointer 不得绕过 gate。"""
    db_path = tmp_path / "gate-cursor-script.db"
    seed_published_store(tmp_path, "gate-cursor-script.db")
    before = _raw_pointer(db_path)
    observed: dict = {}

    class _CursorScriptHarness:
        def run_case(self, case_id: str, **kwargs):
            conn = sqlite3.connect(db_path)
            try:
                try:
                    conn.cursor().executescript(
                        "UPDATE current_pointer SET bundle_id = 'b-tampered'"
                        " WHERE id = 1;"
                        f"UPDATE current_pointer SET bundle_id = '{before}'"
                        " WHERE id = 1;"
                    )
                    observed["executed"] = True
                except Exception as exc:
                    observed["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    conn.commit()
                except Exception:
                    pass
                conn.close()
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_CursorScriptHarness(),
        engine=engine,
        current_store_db=str(db_path),
        approved_temp_root=str(tmp_path),
    )
    assert report["decision"] == "quarantine", (
        "cursor.executescript must not bypass current_pointer write observation"
    )
    assert (
        report["gate_environment"].get("pointer_write_attempts") or 0
    ) >= 1
    assert observed.get("executed") is not True, (
        "gate must reject cursor.executescript before the script is executed; "
        f"observed={observed!r}"
    )
    assert _raw_pointer(db_path) == before


def test_002_gate_detects_cursor_connection_pointer_write_restore(tmp_path) -> None:
    """F3-1：cursor.connection 写/恢复 current_pointer 不得绕过 gate。"""
    db_path = tmp_path / "gate-cursor-connection.db"
    seed_published_store(tmp_path, "gate-cursor-connection.db")
    before = _raw_pointer(db_path)
    observed: dict = {}

    class _CursorConnectionHarness:
        def run_case(self, case_id: str, **kwargs):
            conn = sqlite3.connect(db_path)
            try:
                try:
                    raw = conn.cursor().connection
                    raw.execute(
                        "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                        ("b-tampered",),
                    )
                    observed["after_write"] = raw.execute(
                        "SELECT bundle_id FROM current_pointer WHERE id = 1"
                    ).fetchone()[0]
                    if observed["after_write"] == "b-tampered":
                        raw.execute(
                            "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                            (before,),
                        )
                except Exception as exc:
                    observed["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    conn.commit()
                except Exception:
                    pass
                conn.close()
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_CursorConnectionHarness(),
        engine=engine,
        current_store_db=str(db_path),
        approved_temp_root=str(tmp_path),
    )
    assert report["decision"] == "quarantine", (
        "cursor.connection must not bypass current_pointer write observation"
    )
    assert (
        report["gate_environment"].get("pointer_write_attempts") or 0
    ) >= 1
    assert observed.get("after_write") in (None, before), (
        "gate must reject cursor.connection pointer write before it is visible; "
        f"observed={observed!r}"
    )
    assert _raw_pointer(db_path) == before


def test_002_gate_observation_scope_is_context_local() -> None:
    """F3：gate 期间其他已有线程的正常调用不得被拦截/计入（上下文局部）。"""
    from app.web.security import SessionRegistry

    started = threading.Event()
    release = threading.Event()
    done = threading.Event()
    observed: dict = {}

    def _background() -> None:
        started.set()
        assert release.wait(timeout=5.0)
        registry = SessionRegistry()
        registry.ensure_session(None)
        observed["sessions"] = len(registry._sessions)
        done.set()

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()
    assert started.wait(timeout=5.0), "background thread did not start"

    class _Harness:
        def run_case(self, case_id: str, **kwargs):
            release.set()
            assert done.wait(timeout=5.0), "background call did not complete"
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    try:
        report = _run_gate(harness=_Harness(), engine=engine)
    finally:
        thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert observed.get("sessions") == 1, (
        "background thread must retain its normal SessionRegistry behavior"
    )
    assert report["decision"] == "publish", (
        "non-gate thread calls must not be counted as gate violations: "
        f"{report.get('quarantine_reason')!r}"
    )
    assert report["gate_environment"].get("session_creations") == 0


def test_002_gate_counts_blocked_direct_httpx_attempt(monkeypatch) -> None:
    def _blocked(*args, **kwargs):
        raise AssertionError("direct httpx blocked")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)

    class _DirectHarness:
        def run_case(self, case_id: str, **kwargs):
            try:
                httpx.get("https://blocked.invalid/negatives")
            except Exception:
                pass
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    try:
        report = _run_gate(harness=_DirectHarness(), engine=engine)
    except Exception as exc:
        pytest.fail(f"直接 HTTPX 尝试未被门禁观察器捕获：{type(exc).__name__}: {exc}")
    assert report["decision"] == "quarantine"
    assert (report["gate_environment"].get("direct_http_attempts") or 0) == 1


def test_002_gate_detects_session_creation() -> None:
    observed: dict = {}

    class _SessionHarness:
        def run_case(self, case_id: str, **kwargs):
            from app.web.security import SessionRegistry

            registry = SessionRegistry()
            observed["before"] = len(registry._sessions)
            try:
                registry.ensure_session(None)
            except Exception as exc:
                observed["error"] = f"{type(exc).__name__}: {exc}"
            observed["after"] = len(registry._sessions)
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(harness=_SessionHarness(), engine=engine)
    assert report["decision"] == "quarantine"
    assert (
        report["gate_environment"].get("session_creations") or 0
    ) >= 1
    assert observed["after"] == observed["before"], (
        "gate must reject session creation before any real registry side effect: "
        f"{observed!r}"
    )


def test_002_gate_rejects_outside_temp_db(tmp_path, approved_temp_root) -> None:
    outside = (
        approved_temp_root
        / f"outside-gate-002-{tmp_path.parent.name}-{tmp_path.name}"
        / "store.db"
    )
    assert not outside.exists()
    assert not outside.parent.exists()

    class _OutsideDbHarness:
        def run_case(self, case_id: str, **kwargs):
            from app.rules.store import RuleStore

            try:
                RuleStore(outside)
            except Exception:
                pass
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_OutsideDbHarness(),
        engine=engine,
        approved_temp_root=str(approved_temp_root),
    )
    assert report["decision"] == "quarantine", (
        "门禁期间新开批准根内、但非本次 gate 临时目录的 DB → 隔离"
    )
    assert report.get("quarantine_reason")
    assert not outside.parent.exists(), (
        "outside-gate RuleStore must be rejected before any real filesystem I/O "
        f"(no directory/DB created): {outside}"
    )
    assert not outside.exists()


def test_002_gate_rejects_direct_sqlite_outside_temp_db(
    tmp_path, approved_temp_root
) -> None:
    """F3-2：gate 内直接 sqlite3.connect 越界 DB 必须在真实 I/O 前拒绝。"""
    db_path = tmp_path / "gate-direct-outside-current.db"
    seed_published_store(tmp_path, "gate-direct-outside-current.db")
    outside = approved_temp_root / f"outside-direct-sqlite-{tmp_path.name}.db"
    if outside.exists():  # 防御：仅清理本测试唯一命名文件，不触其他证据
        outside.unlink()
    assert not outside.exists()
    observed: dict = {}

    class _DirectSqliteOutsideHarness:
        def run_case(self, case_id: str, **kwargs):
            try:
                conn = sqlite3.connect(outside)
                observed["created"] = True
                try:
                    conn.execute("CREATE TABLE IF NOT EXISTS leak (v TEXT)")
                    conn.execute("INSERT INTO leak VALUES ('outside-write')")
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                observed["error"] = f"{type(exc).__name__}: {exc}"
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    report = _run_gate(
        harness=_DirectSqliteOutsideHarness(),
        engine=engine,
        current_store_db=str(db_path),
        approved_temp_root=str(approved_temp_root),
    )
    assert report["decision"] == "quarantine", (
        "direct sqlite3.connect to an outside DB must be rejected before real I/O"
    )
    assert (report["gate_environment"].get("outside_temp_db") or 0) >= 1
    assert observed.get("created") is not True, (
        "gate must reject direct sqlite3.connect before the DB is created; "
        f"observed={observed!r}"
    )
    assert not outside.exists(), (
        f"outside direct-sqlite DB must not be created: {outside}"
    )


def _cleanup_outside_sqlite(path: Path) -> None:
    """清理本测试唯一命名的 outside SQLite 文件及附属文件（不触其他证据）。"""
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            path.with_name(path.name + suffix).unlink()
        except FileNotFoundError:
            continue


def test_002_gate_rejects_dbapi2_connect_outside_temp_db(
    tmp_path, approved_temp_root
) -> None:
    """F3-2 等价出口：gate 内 sqlite3.dbapi2.connect 越界 DB 须在真实 I/O 前拒绝。"""
    db_path = tmp_path / "gate-dbapi2-outside-current.db"
    seed_published_store(tmp_path, "gate-dbapi2-outside-current.db")
    outside = tmp_path / "outside-dbapi2-connect.db"
    assert not outside.exists()
    observed: dict = {}

    class _Dbapi2OutsideHarness:
        def run_case(self, case_id: str, **kwargs):
            try:
                conn = sqlite3.dbapi2.connect(outside)
                observed["created"] = True
                try:
                    conn.execute("CREATE TABLE IF NOT EXISTS leak (v TEXT)")
                    conn.execute("INSERT INTO leak VALUES ('dbapi2-outside-write')")
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                observed["error"] = f"{type(exc).__name__}: {exc}"
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    try:
        report = _run_gate(
            harness=_Dbapi2OutsideHarness(),
            engine=engine,
            current_store_db=str(db_path),
            approved_temp_root=str(approved_temp_root),
        )
        assert report["decision"] != "publish", (
            "sqlite3.dbapi2.connect to an outside DB must not publish"
        )
        assert report.get("quarantine_reason"), (
            "dbapi2 outside-DB violation must be quarantined with a reason"
        )
        assert observed.get("created") is not True, (
            "gate must reject sqlite3.dbapi2.connect before the DB is opened; "
            f"observed={observed!r}"
        )
        assert not outside.exists(), (
            f"outside dbapi2 DB must not be created or written: {outside}"
        )
    finally:
        _cleanup_outside_sqlite(outside)


def test_002_gate_rejects_attach_database_outside_temp_db(
    tmp_path, approved_temp_root
) -> None:
    """F3-2 等价出口：gate 内 ATTACH DATABASE 越界文件须在真实 I/O 前拒绝。"""
    db_path = tmp_path / "gate-attach-outside-current.db"
    seed_published_store(tmp_path, "gate-attach-outside-current.db")
    outside = tmp_path / "outside-attach-database.db"
    assert not outside.exists()
    sql_outside = str(outside).replace("'", "''")
    observed: dict = {}

    class _AttachOutsideHarness:
        def run_case(self, case_id: str, **kwargs):
            try:
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        f"ATTACH DATABASE '{sql_outside}' AS leak"
                    )
                    observed["attached"] = True
                    conn.execute("CREATE TABLE leak.leak (v TEXT)")
                    conn.execute(
                        "INSERT INTO leak.leak VALUES ('attach-outside-write')"
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                observed["error"] = f"{type(exc).__name__}: {exc}"
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    try:
        report = _run_gate(
            harness=_AttachOutsideHarness(),
            engine=engine,
            current_store_db=str(db_path),
            approved_temp_root=str(approved_temp_root),
        )
        assert report["decision"] != "publish", (
            "ATTACH DATABASE to an outside file must not publish"
        )
        assert observed.get("attached") is not True, (
            "gate must reject ATTACH DATABASE before the outside file is created; "
            f"observed={observed!r}"
        )
        assert not outside.exists(), (
            f"ATTACH DATABASE outside file must not be created: {outside}"
        )
    finally:
        _cleanup_outside_sqlite(outside)


def test_002_gate_rejects_vacuum_into_outside_temp_db(
    tmp_path, approved_temp_root
) -> None:
    """F3-2 等价出口：gate 内 VACUUM INTO 越界文件须在真实 I/O 前拒绝。"""
    db_path = tmp_path / "gate-vacuum-outside-current.db"
    seed_published_store(tmp_path, "gate-vacuum-outside-current.db")
    outside = tmp_path / "outside-vacuum-into.db"
    assert not outside.exists()
    sql_outside = str(outside).replace("'", "''")
    observed: dict = {}

    class _VacuumOutsideHarness:
        def run_case(self, case_id: str, **kwargs):
            try:
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(f"VACUUM INTO '{sql_outside}'")
                    observed["vacuumed"] = True
                finally:
                    conn.close()
            except Exception as exc:
                observed["error"] = f"{type(exc).__name__}: {exc}"
            return {
                "case_id": case_id,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }

    _harness, engine = _quiet_seams()
    try:
        report = _run_gate(
            harness=_VacuumOutsideHarness(),
            engine=engine,
            current_store_db=str(db_path),
            approved_temp_root=str(approved_temp_root),
        )
        assert report["decision"] != "publish", (
            "VACUUM INTO an outside file must not publish"
        )
        assert observed.get("vacuumed") is not True, (
            "gate must reject VACUUM INTO before the outside file is created; "
            f"observed={observed!r}"
        )
        assert not outside.exists(), (
            f"VACUUM INTO outside file must not be created: {outside}"
        )
    finally:
        _cleanup_outside_sqlite(outside)


def test_002_gate_binds_reference_code_to_file_bytes_hash() -> None:
    """F2-2：gate binding 的 reference_code_sha256 必须是文件字节 hash。"""
    report = _run_gate()
    actual = _reference_code_sha256()
    label = _canonical_sha({"reference_code": "app/updater/reference.py"})
    assert actual != label, "runtime reference.py bytes hash must differ from label"
    assert report["binding"]["reference_code_sha256"] == actual, (
        "gate report must bind the actual reference.py file bytes SHA256, "
        f"not a canonical label: {report['binding']['reference_code_sha256']!r}"
    )


def test_002_gate_observers_restore_on_exception() -> None:
    class _ExplodingHarness:
        def run_case(self, case_id: str, **kwargs):
            raise RuntimeError("脚本化失败（观察器恢复测试）")

    _harness, engine = _quiet_seams()
    try:
        report = _run_gate(harness=_ExplodingHarness(), engine=engine)
    except Exception as exc:
        pytest.fail(f"门禁异常未恢复观察器/未转为隔离：{type(exc).__name__}: {exc}")
    assert report["decision"] == "quarantine"
    assert report["gate_environment"].get("observers_restored") is True


def test_002_gate_observers_do_not_block_non_gate_context() -> None:
    from app.updater.testgate import gate_observation_scope

    original_dns = socket.getaddrinfo
    original_socket = socket.socket
    with gate_observation_scope(store=None, approved_temp_root=None):
        pass
    # 离开 gate 上下文后委托原实现：不残留 DNS/socket 拒绝包装
    assert socket.getaddrinfo is original_dns, "非 gate 上下文不得残留 DNS 观察器"
    assert socket.socket is original_socket, "非 gate 上下文不得残留 socket 观察器"


def test_002_seed_null_baseline_empty_cas(tmp_path) -> None:
    from app.rules.store import RuleStore
    from app.updater.publisher import publish_update
    from app.updater.reference import REQUIRED_CASES

    db_path = tmp_path / "seed.db"
    store = RuleStore(db_path)
    manifest_rows = [AVD_MANIFEST]
    report = _seed_report(
        required_cases=REQUIRED_CASES,
        manifests=manifest_rows,
    )
    published = publish_update(
        store,
        job_id="job-002-seed",
        content=initial_content(),
        evidence_refs=[],
        validation_report=report,
        purpose="seed",
        baseline=None,
        manifests=manifest_rows,
        required_cases=tuple(REQUIRED_CASES),
    )
    assert store.current()["bundle_id"] == published["bundle_id"]

    # 非真空库再 seed → 拒绝且零部分写
    before_pointer = store.current()
    before_count = len(store._connect().execute("SELECT * FROM publications").fetchall())
    with pytest.raises(Exception):
        publish_update(
            store,
            job_id="job-002-seed-again",
            content=initial_content(),
            evidence_refs=[],
            validation_report=report,
            purpose="seed",
            baseline=None,
            manifests=manifest_rows,
            required_cases=tuple(REQUIRED_CASES),
        )
    assert store.current() == before_pointer
    assert (
        len(store._connect().execute("SELECT * FROM publications").fetchall())
        == before_count
    )


def test_002_publish_baseline_changed_no_partial_write(tmp_path) -> None:
    from app.rules.store import RuleStore
    from app.updater.publisher import publish_update

    db_path = tmp_path / "baseline.db"
    store = RuleStore(db_path)
    store.publish(initial_content(), evidence_refs=[])
    stale = store.current()
    store.publish(avd_changed_bundle_content(), evidence_refs=[])
    current = store.current()

    pubs_before = len(store._connect().execute("SELECT * FROM publications").fetchall())
    bundles_before = len(store._connect().execute("SELECT * FROM rule_bundles").fetchall())
    audits_before = len(store._connect().execute("SELECT * FROM audit_log").fetchall())

    with pytest.raises((AppError, ValueError)) as excinfo:
        publish_update(
            store,
            job_id="job-002-stale",
            content=initial_content(),
            evidence_refs=[],
            validation_report=_update_report(stale),
            purpose="update",
            baseline=stale,
            manifests=[AVD_MANIFEST],
            required_cases=(B5_REQUIRED_CASE,),
        )
    assert not isinstance(excinfo.value, TypeError), (
        "失败须来自 baseline CAS 复核，而非 publish_update 缺少新签名入参"
    )
    assert store.current() == current
    assert len(store._connect().execute("SELECT * FROM publications").fetchall()) == pubs_before
    assert len(store._connect().execute("SELECT * FROM rule_bundles").fetchall()) == bundles_before
    assert len(store._connect().execute("SELECT * FROM audit_log").fetchall()) == audits_before


def test_002_resolution_baseline_change_cannot_reuse(tmp_path) -> None:
    from app.rules.store import RuleStore
    from app.updater.observer import resolve_observer_pending

    store = RuleStore(tmp_path / "resolution.db")
    store.publish(initial_content(), evidence_refs=[])
    stale = store.current()
    store.publish(copy.deepcopy(initial_content()), evidence_refs=[])
    resolution = {
        "disposition": "no_effect",
        "pending_origin_digest": "sha256:" + "22" * 32,
        "latest_event_digest": "sha256:" + "33" * 32,
        "expected_current_bundle_id": stale["bundle_id"],
        "expected_current_bundle_hash": stale["bundle_hash"],
        "proof_refs": ["proof:test:stale"],
        "proof_sha256": "sha256:" + "44" * 32,
        "target_bundle_hash": None,
    }
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path="salaries.reductions.2025_26.cap",
            period="2025_26",
            resolution=resolution,
        )
