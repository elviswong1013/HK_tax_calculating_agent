"""REQ-18 publish atomicity + gate isolation evidence (P3-W1a migration).

Two nodes:
- test_acceptance_publish_audit_same_transaction:
  publish_update(purpose=update + baseline id/hash/schema + required binding);
  publish audit failure injection -> bundle/publications/pointer rollback;
  baseline replaced -> zero partial write; report self-reduced required ->
  reject.
- test_acceptance_testgate_real_isolation_evidence:
  gate_environment must carry real direct observations (temp store path,
  network/direct-HTTP/socket/DNS/session/pointer-write counters, observer
  restore), not injected-seam or hard-coded zeros; a direct-leak negative must
  be counted and quarantined.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from _acc_helpers import (
    AVD_CHANGED,
    AVD_FULLPATH,
    B5_REQUIRED_CASE,
    FACT_AVD_110,
    StrictOutbound,
    _testonly_avd_source,
    avd_changed_bundle_content,
    fake_b5_engine_result,
    fake_b5_harness_result,
    initial_content,
    update_report,
)


class _AuditFailConnection:
    def __init__(self, conn, fail_event: str) -> None:
        self._conn = conn
        self._fail_event = fail_event

    def execute(self, sql: str, params=()):
        if (
            sql.lstrip().upper().startswith("INSERT INTO AUDIT_LOG")
            and params
            and params[0] == self._fail_event
        ):
            raise sqlite3.OperationalError(
                f"injected failure: audit_log write (event_type={self._fail_event})"
            )
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _make_publish_audit_fail_store(db_path):
    from app.rules.store import RuleStore

    class _PublishAuditFailStore(RuleStore):
        def _connect(self):
            return _AuditFailConnection(super()._connect(), "publish")

    return _PublishAuditFailStore(db_path)


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _audit_events(db_path, event_type: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE event_type = ?",
            (event_type,),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def test_acceptance_publish_audit_same_transaction(tmp_path) -> None:
    from app.rules.store import RuleStore
    from app.updater.publisher import publish_update

    manifest = [dict(_testonly_avd_source()["manifest"])]

    def _publish(store, *, job_id, content, baseline, required_cases, report=None):
        return publish_update(
            store,
            job_id=job_id,
            content=content,
            evidence_refs=[FACT_AVD_110],
            validation_report=report
            or update_report(
                store,
                content=content,
                manifests=manifest,
                evidence_refs=[FACT_AVD_110],
                required_cases=required_cases,
                changed_paths=[AVD_FULLPATH],
            ),
            purpose="update",
            baseline=baseline,
            manifests=manifest,
            required_cases=required_cases,
        )

    db_path = tmp_path / "publish-audit.db"
    store = _make_publish_audit_fail_store(db_path)
    store.publish(initial_content(), evidence_refs=[])
    before_pointer = store.current()["bundle_id"]
    pubs_before = _count(db_path, "publications")
    bundles_before = _count(db_path, "rule_bundles")

    with pytest.raises(Exception) as excinfo:
        _publish(
            store,
            job_id="job-acc-audit-1",
            content=avd_changed_bundle_content(),
            baseline=store.current(),
            required_cases=(B5_REQUIRED_CASE,),
        )
    assert "injected failure" in str(excinfo.value), (
        "failure must come from the publish-audit injection (new signature "
        f"purpose/baseline/required must be accepted), actual {excinfo.value!r}"
    )

    assert store.current()["bundle_id"] == before_pointer, (
        "publish audit failure -> pointer must not switch (single transaction)"
    )
    assert _count(db_path, "publications") == pubs_before
    assert _count(db_path, "rule_bundles") == bundles_before
    assert not _audit_events(db_path, "publish")

    # baseline replaced -> CAS failure, zero partial write.
    normal_path = tmp_path / "publish-baseline.db"
    normal = RuleStore(normal_path)
    normal.publish(initial_content(), evidence_refs=[])
    stale_baseline = normal.current()
    normal.publish(avd_changed_bundle_content(), evidence_refs=[])
    current_after_replace = normal.current()
    pubs_n = _count(normal_path, "publications")
    bundles_n = _count(normal_path, "rule_bundles")
    audits_n = _count(normal_path, "audit_log")
    stale_report = update_report(
        normal,
        content=avd_changed_bundle_content("120"),
        manifests=manifest,
        evidence_refs=[FACT_AVD_110],
        required_cases=(B5_REQUIRED_CASE,),
        changed_paths=[AVD_FULLPATH],
    )
    stale_report["binding"]["baseline_bundle_id"] = stale_baseline["bundle_id"]
    stale_report["binding"]["baseline_bundle_hash"] = stale_baseline["bundle_hash"]
    with pytest.raises(Exception):
        _publish(
            normal,
            job_id="job-acc-audit-cas",
            content=avd_changed_bundle_content("120"),
            baseline=stale_baseline,
            required_cases=(B5_REQUIRED_CASE,),
            report=stale_report,
        )
    assert normal.current() == current_after_replace, (
        "publish_update must recheck baseline inside the transaction"
    )
    assert _count(normal_path, "publications") == pubs_n
    assert _count(normal_path, "rule_bundles") == bundles_n
    assert _count(normal_path, "audit_log") == audits_n

    # report self-reduces required set -> reject (server recomputes).
    reduced = update_report(
        normal,
        content=avd_changed_bundle_content("130"),
        manifests=manifest,
        evidence_refs=[FACT_AVD_110],
        required_cases=(B5_REQUIRED_CASE, "case_required_extra"),
        changed_paths=[AVD_FULLPATH],
    )
    reduced["cases"]["required_case_ids"] = [B5_REQUIRED_CASE]
    with pytest.raises(Exception):
        _publish(
            normal,
            job_id="job-acc-required-recompute",
            content=avd_changed_bundle_content("130"),
            baseline=normal.current(),
            required_cases=(B5_REQUIRED_CASE, "case_required_extra"),
            report=reduced,
        )
    assert normal.current() == current_after_replace


class _FakeHarnessLike:
    def run_case(self, case_id: str, **kwargs) -> dict:
        return fake_b5_harness_result(
            case_id,
            AVD_CHANGED,
            evidence_ref=FACT_AVD_110["fact_id"],
        )


class _FakeEngineLike:
    def run_case(self, case_id: str, candidate_content: dict, **kwargs) -> dict:
        return fake_b5_engine_result(case_id, AVD_CHANGED)


def test_acceptance_testgate_real_isolation_evidence(
    tmp_path, monkeypatch, approved_temp_root
) -> None:
    import socket

    import httpx

    from app.rules.store import RuleStore
    from app.updater.testgate import run_gate
    from app.web.security import SessionRegistry

    db_path = tmp_path / "gate.db"
    store = RuleStore(db_path)
    store.publish(initial_content(), evidence_refs=[])
    before_pointer = store.current()["bundle_id"]

    manifest = [dict(_testonly_avd_source()["manifest"])]
    outbound = StrictOutbound()
    report = run_gate(
        candidate_content=avd_changed_bundle_content(),
        current_content=initial_content(),
        manifests=manifest,
        independent_facts=[FACT_AVD_110],
        required_cases=[B5_REQUIRED_CASE],
        harness=_FakeHarnessLike(),
        engine=_FakeEngineLike(),
        outbound=outbound,
        current_store_db=str(db_path),
        approved_temp_root=str(approved_temp_root),
    )
    assert report["decision"] == "publish", (
        f"precondition: complete candidate should pass gate: {report!r}"
    )

    ge = report.get("gate_environment") or {}
    temp_store_path = ge.get("temp_store_path")
    assert isinstance(temp_store_path, str) and temp_store_path.strip(), (
        f"C8.7: gate_environment needs a real temp_store_path: {ge!r}"
    )
    assert temp_store_path != str(db_path)
    assert Path(temp_store_path).exists()

    for key in (
        "network_calls",
        "direct_http_attempts",
        "socket_attempts",
        "dns_attempts",
        "session_creations",
        "pointer_write_attempts",
    ):
        value = ge.get(key)
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"C8.7: gate_environment[{key!r}] must be an integer observation: {ge!r}"
        )
        assert value == 0, f"gate must record {key}=0, actual {value}"
    assert ge.get("observers_restored") is True, (
        f"gate must restore all observers on exit: {ge!r}"
    )
    assert ge.get("user_session") is False
    assert ge.get("current_pointer_written") is False

    assert not outbound.requests
    assert store.current()["bundle_id"] == before_pointer

    # Negative: direct egress/session/pointer write-restore must be observed.
    def _blocked_transport(self, request):
        raise AssertionError("direct HTTPX blocked")

    def _blocked_dns(*args, **kwargs):
        raise AssertionError("direct DNS blocked")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked_transport)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_dns)

    conn = store._connect()
    try:
        pointer_before_probe = conn.execute(
            "SELECT bundle_id FROM current_pointer WHERE id = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    class _DirectLeakHarness:
        def __init__(self) -> None:
            self.registry = SessionRegistry()
            self.session_count_before = len(self.registry._sessions)
            self.session_count_after = None
            self.pointer_seen_after_write = None
            self.error = None

        def run_case(self, case_id: str, **kwargs):
            try:
                httpx.get("https://blocked.invalid/gate-direct")
            except AssertionError:
                pass
            try:
                socket.getaddrinfo("blocked.invalid", 443)
            except AssertionError:
                pass
            try:
                self.registry.ensure_session(None)
            except Exception as exc:  # session must be rejected before side effect
                self.error = f"session:{type(exc).__name__}"
            self.session_count_after = len(self.registry._sessions)
            probe = store._connect()
            try:
                try:
                    probe.execute(
                        "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                        ("b-tampered",),
                    )
                    self.pointer_seen_after_write = probe.execute(
                        "SELECT bundle_id FROM current_pointer WHERE id = 1"
                    ).fetchone()[0]
                    # Cleanup only if the observer wrongly allowed the write.
                    if self.pointer_seen_after_write == "b-tampered":
                        probe.execute(
                            "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                            (pointer_before_probe,),
                        )
                except Exception as exc:
                    self.error = f"pointer:{type(exc).__name__}"
            finally:
                probe.close()
            return fake_b5_harness_result(
                case_id,
                AVD_CHANGED,
                evidence_ref=FACT_AVD_110["fact_id"],
            )

    leak_harness = _DirectLeakHarness()
    try:
        leak_report = run_gate(
            candidate_content=avd_changed_bundle_content(),
            current_content=initial_content(),
            manifests=manifest,
            independent_facts=[FACT_AVD_110],
            required_cases=[B5_REQUIRED_CASE],
            harness=leak_harness,
            engine=_FakeEngineLike(),
            outbound=StrictOutbound(),
            current_store_db=str(db_path),
            approved_temp_root=str(approved_temp_root),
        )
    except Exception as exc:
        pytest.fail(
            "direct egress/session/pointer write not caught by gate observer: "
            f"{type(exc).__name__}: {exc}"
        )
    leak_ge = leak_report.get("gate_environment") or {}
    assert leak_report.get("decision") == "quarantine", (
        f"direct HTTPX/socket/session/pointer write -> quarantine: {leak_report!r}"
    )
    assert (leak_ge.get("direct_http_attempts") or 0) >= 1
    assert (leak_ge.get("dns_attempts") or 0) >= 1
    assert (leak_ge.get("session_creations") or 0) >= 1
    assert (leak_ge.get("pointer_write_attempts") or 0) >= 1
    assert leak_harness.session_count_after == leak_harness.session_count_before, (
        "gate must reject session creation without real registry side effects"
    )
    assert leak_harness.pointer_seen_after_write in (None, pointer_before_probe), (
        "gate must reject pointer write before it becomes visible (write-restore "
        f"must not be a real write); seen={leak_harness.pointer_seen_after_write!r}"
    )
    assert store.current()["bundle_id"] == before_pointer, (
        "observer must reject/restore pointer write-restore; pointer unchanged"
    )
