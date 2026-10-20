"""REQ-15/18 gate counterfactual: network attempts during gate execution block.

P3-W1a migration (R6/U7):
- candidate/manifest/fact/case migrate to an unaffected AVD path + test-only B5
  required case (D1=a / D2);
- keep leaky/quiet assertions (injected outbound count 2 -> quarantine; 0 ->
  publish);
- add a direct-HTTPX negative (bypassing injected outbound) that the gate
  observer must count and quarantine.

Contract:
- run_gate injects its counting outbound into harness/engine execution; each
  execution-time outbound attempt increments gate_environment.network_calls and
  network_calls > 0 -> decision="quarantine" with a non-empty reason.
- gate_environment.temp_store_path is the real temporary store used by the gate
  (exists on disk; current_pointer has zero rows).
- direct httpx attempts during execution are counted in
  gate_environment.direct_http_attempts and also force quarantine.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from _acc_helpers import (
    AVD_CHANGED,
    B5_REQUIRED_CASE,
    FACT_AVD_110,
    FakeOutbound,
    _testonly_avd_source,
    avd_changed_bundle_content,
    fake_b5_engine_result,
    fake_b5_harness_result,
    initial_content,
)

REFERENCE_URL = "https://reference.invalid/independent-evidence"
AVD_MANIFEST = _testonly_avd_source()["manifest"]


class _LeakyHarness:
    def run_case(self, case_id: str, **kwargs):
        outbound = kwargs.get("outbound")
        if outbound is not None:
            outbound.fetch(REFERENCE_URL)
        return fake_b5_harness_result(
            case_id,
            AVD_CHANGED,
            evidence_ref=FACT_AVD_110["fact_id"],
        )


class _LeakyEngine:
    def run_case(self, case_id: str, candidate_content, **kwargs):
        outbound = kwargs.get("outbound")
        if outbound is not None:
            outbound.fetch(REFERENCE_URL)
        return fake_b5_engine_result(case_id, AVD_CHANGED)


class _QuietHarness:
    def run_case(self, case_id: str, **kwargs):
        return fake_b5_harness_result(
            case_id,
            AVD_CHANGED,
            evidence_ref=FACT_AVD_110["fact_id"],
        )


class _QuietEngine:
    def run_case(self, case_id: str, candidate_content, **kwargs):
        return fake_b5_engine_result(case_id, AVD_CHANGED)


class _DirectLeakyHarness:
    """Bypasses the injected outbound and calls httpx directly."""

    def run_case(self, case_id: str, **kwargs):
        import httpx

        try:
            httpx.get("https://blocked.invalid/gate-direct")
        except Exception:
            pass
        return fake_b5_harness_result(
            case_id,
            AVD_CHANGED,
            evidence_ref=FACT_AVD_110["fact_id"],
        )


def _run_gate(harness, engine, **extra):
    from app.updater.testgate import run_gate

    return run_gate(
        candidate_content=avd_changed_bundle_content(),
        current_content=initial_content(),
        manifests=[AVD_MANIFEST],
        independent_facts=[FACT_AVD_110],
        required_cases=[B5_REQUIRED_CASE],
        harness=harness,
        engine=engine,
        outbound=FakeOutbound({REFERENCE_URL: b"evidence-bytes"}),
        **extra,
    )


def test_acceptance_testgate_counterfactual_network_attempt_blocks(
    monkeypatch,
) -> None:
    # Leaky scenario: harness + engine each call outbound once.
    report = _run_gate(_LeakyHarness(), _LeakyEngine())
    gate_environment = report.get("gate_environment") or {}

    network_calls = gate_environment.get("network_calls")
    assert network_calls == 2, (
        "C8.7: gate must inject its counting outbound into harness/engine; "
        f"execution-time attempts must be counted, actual {network_calls!r}"
    )
    assert report.get("decision") == "quarantine", (
        "C8.4/C8.7: execution-time network violation must block publish; "
        f"actual decision={report.get('decision')!r}"
    )
    assert report.get("quarantine_reason"), "network violation must carry a reason"

    # Temporary store is real and has no current pointer row.
    temp_store_path = gate_environment.get("temp_store_path")
    assert isinstance(temp_store_path, str) and temp_store_path.strip(), (
        f"gate_environment must carry the real temp store path: {gate_environment!r}"
    )
    assert Path(temp_store_path).exists(), (
        f"temp store must exist on disk: {temp_store_path!r}"
    )
    conn = sqlite3.connect(temp_store_path)
    try:
        pointer_rows = int(
            conn.execute("SELECT COUNT(*) FROM current_pointer").fetchone()[0]
        )
    finally:
        conn.close()
    assert pointer_rows == 0, (
        "gate temp store must not write current pointer (C8.7); "
        f"actual rows={pointer_rows}"
    )

    # Counterfactual control: only difference is execution-time outbound.
    quiet_report = _run_gate(_QuietHarness(), _QuietEngine())
    assert quiet_report.get("gate_environment", {}).get("network_calls") == 0
    assert quiet_report.get("decision") == "publish", (
        "counterfactual: quiet harness/engine with identical inputs must publish; "
        f"actual {quiet_report.get('decision')!r}"
    )

    # Direct HTTPX attempt (bypassing injected outbound) must be observed.
    import httpx

    def _blocked_transport(self, request):
        raise AssertionError("direct httpx blocked")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked_transport)
    try:
        direct_report = _run_gate(_DirectLeakyHarness(), _QuietEngine())
    except Exception as exc:
        pytest.fail(
            "gate did not catch/convert a direct HTTPX attempt to quarantine: "
            f"{type(exc).__name__}: {exc}"
        )
    direct_ge = direct_report.get("gate_environment") or {}
    assert (direct_ge.get("direct_http_attempts") or 0) == 1, (
        f"C8.7: direct HTTPX attempts must be counted: {direct_ge!r}"
    )
    assert direct_report.get("decision") == "quarantine", (
        f"direct HTTPX attempt must quarantine: {direct_report.get('decision')!r}"
    )
    assert direct_report.get("quarantine_reason")
