"""P3 repair wave 1 / OR-2 Red tests: every allowed gate SQLite connection is observed.

REQ mapping:
- OR-2 / 005 §15.4 / 006 §7.2 / F3-2:
  ``ATTACH DATABASE`` and ``VACUUM INTO`` are SQLite file-I/O exits.  Inside
  the gate observation scope, *all allowed connections* (``:memory:``, empty
  database name, ``file::memory:``, gate temp-dir paths, and
  ``sqlite3.dbapi2.connect``) must be wrapped so the attempt is rejected
  before real I/O, counted in ``gate_environment.outside_temp_db``, and no
  outside file is created.
- Negative probes below create the outside target only if the bug exists;
  each test cleans its own uniquely-named temp files in ``finally``.

Red failure reason expected on the current tree: those allowed connections
are returned raw (``sqlite3.connect`` branch), so ATTACH/VACUUM succeeds,
``outside_temp_db`` stays 0 and the gate publishes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _fakes import (
    AVD_FULLPATH,
    AVD_MANIFEST,
    avd_changed_bundle_content,
    initial_content,
)

CASE_ID = "a_avd_related_fixed"

FACT_100 = {
    "fact_id": "fact:test:avd_100",
    "fullpath": AVD_FULLPATH,
    "value": "100",
    "source_id": "test_avd_source",
    "url": "https://example.invalid/avd",
    "anchor": "heading:AVD - Related Conveyance#row:Fixed duty#col:Amount",
    "tier": "T1",
}


def _cleanup_sqlite(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except FileNotFoundError:
            continue


def _sql_literal(path: Path) -> str:
    return str(path).replace("'", "''")


class _ProbeHarness:
    def __init__(self, probe) -> None:
        self._probe = probe

    def run_case(self, case_id: str, **kwargs):
        from app.updater.reference import ReferenceHarness

        self._probe()
        return ReferenceHarness(proof_facts=[FACT_100]).run_case(case_id, **kwargs)


def _run_probe_gate(*, probe, approved_root: Path):
    from app.core.hashing import bundle_content_hash
    from app.updater.reference import CandidateEngine
    from app.updater.testgate import run_gate

    approved_root.mkdir(parents=True, exist_ok=True)
    candidate = avd_changed_bundle_content("100")
    report = run_gate(
        candidate_content=candidate,
        current_content=initial_content(),
        manifests=[AVD_MANIFEST],
        independent_facts=[FACT_100],
        required_cases=[CASE_ID],
        harness=_ProbeHarness(probe),
        engine=CandidateEngine(),
        candidate_hash=bundle_content_hash(candidate),
        changed_paths=[AVD_FULLPATH],
        required_case_map={AVD_FULLPATH: CASE_ID},
        approved_temp_root=str(approved_root),
    )
    return report


def _assert_blocked(report: dict, observed: dict, outside: Path) -> None:
    assert report["decision"] == "quarantine", (
        "OR-2: SQLite file I/O through an allowed gate connection must never "
        f"publish: {report!r}"
    )
    assert int(report["gate_environment"].get("outside_temp_db") or 0) >= 1, (
        "OR-2: the rejected ATTACH/VACUUM attempt must be counted in "
        f"gate_environment.outside_temp_db: {report['gate_environment']!r}"
    )
    assert observed.get("executed") is not True, (
        "OR-2: gate must reject the file-I/O statement before it executes; "
        f"observed={observed!r}"
    )
    assert not outside.exists(), (
        f"OR-2: rejected SQLite file I/O must not create an outside file: {outside}"
    )


def test_002_repair_or2_memory_attach_outside(tmp_path) -> None:
    """sqlite3.connect(':memory:') + ATTACH outside must be observed/rejected."""
    approved_root = tmp_path / "approved-root"
    outside = tmp_path / "outside-memory-attach.db"
    _cleanup_sqlite(outside)
    observed: dict = {}

    def probe() -> None:
        conn = None
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute(
                f"ATTACH DATABASE '{_sql_literal(outside)}' AS leak"
            )
            conn.execute("CREATE TABLE leak.leak (v TEXT)")
            conn.execute("INSERT INTO leak.leak VALUES ('outside-write')")
            conn.commit()
            observed["executed"] = True
        except Exception as exc:  # noqa: BLE001 - violation is asserted via report
            observed["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                conn.close()

    try:
        report = _run_probe_gate(probe=probe, approved_root=approved_root)
        _assert_blocked(report, observed, outside)
    finally:
        _cleanup_sqlite(outside)


def test_002_repair_or2_dbapi2_memory_vacuum_into_outside(tmp_path) -> None:
    """sqlite3.dbapi2.connect(':memory:') + VACUUM INTO outside must be observed."""
    approved_root = tmp_path / "approved-root"
    outside = tmp_path / "outside-memory-vacuum.db"
    _cleanup_sqlite(outside)
    observed: dict = {}

    def probe() -> None:
        conn = None
        try:
            conn = sqlite3.dbapi2.connect(":memory:")
            conn.execute(f"VACUUM INTO '{_sql_literal(outside)}'")
            observed["executed"] = True
        except Exception as exc:  # noqa: BLE001
            observed["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                conn.close()

    try:
        report = _run_probe_gate(probe=probe, approved_root=approved_root)
        _assert_blocked(report, observed, outside)
    finally:
        _cleanup_sqlite(outside)


def test_002_repair_or2_empty_database_name_attach_outside(tmp_path) -> None:
    """sqlite3.connect('') + ATTACH outside must be observed/rejected."""
    approved_root = tmp_path / "approved-root"
    outside = tmp_path / "outside-empty-name-attach.db"
    _cleanup_sqlite(outside)
    observed: dict = {}

    def probe() -> None:
        conn = None
        try:
            conn = sqlite3.connect("")
            conn.execute(
                f"ATTACH DATABASE '{_sql_literal(outside)}' AS leak"
            )
            conn.execute("CREATE TABLE leak.leak (v TEXT)")
            conn.execute("INSERT INTO leak.leak VALUES ('outside-write')")
            conn.commit()
            observed["executed"] = True
        except Exception as exc:  # noqa: BLE001
            observed["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                conn.close()

    try:
        report = _run_probe_gate(probe=probe, approved_root=approved_root)
        _assert_blocked(report, observed, outside)
    finally:
        _cleanup_sqlite(outside)


def test_002_repair_or2_file_memory_uri_attach_outside(tmp_path) -> None:
    """sqlite3.connect('file::memory:', uri=True) + ATTACH outside must be observed."""
    approved_root = tmp_path / "approved-root"
    outside = tmp_path / "outside-file-memory-attach.db"
    _cleanup_sqlite(outside)
    observed: dict = {}

    def probe() -> None:
        conn = None
        try:
            conn = sqlite3.connect("file::memory:", uri=True)
            conn.execute(
                f"ATTACH DATABASE '{_sql_literal(outside)}' AS leak"
            )
            conn.execute("CREATE TABLE leak.leak (v TEXT)")
            conn.execute("INSERT INTO leak.leak VALUES ('outside-write')")
            conn.commit()
            observed["executed"] = True
        except Exception as exc:  # noqa: BLE001
            observed["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                conn.close()

    try:
        report = _run_probe_gate(probe=probe, approved_root=approved_root)
        _assert_blocked(report, observed, outside)
    finally:
        _cleanup_sqlite(outside)


def test_002_repair_or2_gate_temp_dir_connection_attach_outside(tmp_path) -> None:
    """Connection to a gate temp-dir DB + ATTACH outside must be observed."""
    approved_root = tmp_path / "approved-root"
    outside = tmp_path / "outside-gate-temp-attach.db"
    _cleanup_sqlite(outside)
    observed: dict = {}

    def probe() -> None:
        gate_dirs = sorted(Path(approved_root).glob("hktax-gate-*"))
        assert len(gate_dirs) == 1, (
            f"expected exactly one gate temp dir under {approved_root}, got {gate_dirs!r}"
        )
        conn = None
        try:
            conn = sqlite3.connect(gate_dirs[0] / "probe.db")
            conn.execute(
                f"ATTACH DATABASE '{_sql_literal(outside)}' AS leak"
            )
            conn.execute("CREATE TABLE leak.leak (v TEXT)")
            conn.execute("INSERT INTO leak.leak VALUES ('outside-write')")
            conn.commit()
            observed["executed"] = True
        except Exception as exc:  # noqa: BLE001
            observed["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                conn.close()

    try:
        report = _run_probe_gate(probe=probe, approved_root=approved_root)
        _assert_blocked(report, observed, outside)
    finally:
        _cleanup_sqlite(outside)
