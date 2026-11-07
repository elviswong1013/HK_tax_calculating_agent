"""P3 repair-wave 1 shared Red-test fixtures (tests-only).

Contracts pinned here (Green must implement, tests must not be weakened):

1. Subcase proof payload (gate report row):
   - ``expected_subcases``: ``[{subcase_id, input, expected}]``
   - ``actual_subcases``:   ``[{subcase_id, input, expected, actual, passed, receipt}]``
   - ``expected_subcases_sha256`` = canonical JSON SHA-256 of
     ``expected_subcases``.
   - ``actual_subcases_sha256`` = canonical JSON SHA-256 of ``actual_subcases``.
   - ``subcase_results_sha256`` = canonical JSON SHA-256 of
     ``{"expected_subcases_sha256", "actual_subcases_sha256", "subcase_ids"}``.

2. Publisher proof fixture (update): the row above is embedded in a complete
   005 §15.4 report so publisher proof validation is exercised without
   re-running the gate.  Row hash consistency is recomputable from the row
   alone; subcase IDs must still match the production ``B5_CASES`` plan.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence


def canonical_sha(payload: Any) -> str:
    from app.core.hashing import canonical_json_bytes

    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _expected_payload(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "subcase_id": str(entry["subcase_id"]),
            "input": copy.deepcopy(dict(entry["input"])),
            "expected": copy.deepcopy(dict(entry["expected"])),
        }
        for entry in entries
    ]


def _actual_payload(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "subcase_id": str(entry["subcase_id"]),
            "input": copy.deepcopy(dict(entry["input"])),
            "expected": copy.deepcopy(dict(entry["expected"])),
            "actual": copy.deepcopy(dict(entry["actual"])),
            "passed": bool(entry["passed"]),
            "receipt": copy.deepcopy(dict(entry["receipt"])),
        }
        for entry in entries
    ]


def subcase_proof_fields(
    expected_entries: Sequence[Mapping[str, Any]],
    actual_entries: Sequence[Mapping[str, Any]],
    *,
    subcase_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Canonical subcase proof fields for a gate row / publisher report row."""
    expected = _expected_payload(expected_entries)
    actual = _actual_payload(actual_entries)
    ids = (
        [row["subcase_id"] for row in expected]
        if subcase_ids is None
        else [str(value) for value in subcase_ids]
    )
    expected_sha = canonical_sha(expected)
    actual_sha = canonical_sha(actual)
    combined = canonical_sha(
        {
            "expected_subcases_sha256": expected_sha,
            "actual_subcases_sha256": actual_sha,
            "subcase_ids": ids,
        }
    )
    return {
        "expected_subcases": expected,
        "actual_subcases": actual,
        "expected_subcases_sha256": expected_sha,
        "actual_subcases_sha256": actual_sha,
        "subcase_results_sha256": combined,
    }


def refresh_subcase_digests(row: dict[str, Any]) -> dict[str, Any]:
    """Recompute row digest fields from its own expected/actual payloads."""
    fields = subcase_proof_fields(
        row["expected_subcases"],
        row["actual_subcases"],
        subcase_ids=row["subcase_ids"],
    )
    row.update(fields)
    return row


def _reference_code_sha256() -> str:
    from app.updater import reference as reference_module

    return "sha256:" + hashlib.sha256(
        Path(reference_module.__file__).read_bytes()
    ).hexdigest()


def _profile_rows(content: Mapping[str, Any]) -> list[dict[str, str]]:
    profiles = (content.get("frozen_semantics") or {}).get("profiles") or []
    return [
        {
            "profile_id": str(profile["profile_id"]),
            "semantic_digest": str(
                profile.get("semantic_digest") or canonical_sha(profile)
            ),
        }
        for profile in profiles
    ]


def provision_plan_subcases(plan: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    """Fixture subcase evidence for a plan using its declared static expectations.

    Publisher only verifies bound hashes/IDs, not tax arithmetic; the gate
    tests use the real harness/engine seam instead.
    """
    expected: list[dict[str, Any]] = []
    actual: list[dict[str, Any]] = []
    for subcase in plan.get("subcases") or []:
        entry = {
            "subcase_id": str(subcase["subcase_id"]),
            "input": copy.deepcopy(dict(subcase["input"])),
            "expected": copy.deepcopy(dict(subcase["expected"])),
        }
        expected.append(entry)
        actual.append(
            {
                **copy.deepcopy(entry),
                "actual": copy.deepcopy(dict(subcase["expected"])),
                "passed": True,
                "receipt": {
                    "subcase_id": str(subcase["subcase_id"]),
                    "engine": "test:provisioned-proof",
                    "input_sha256": canonical_sha(dict(subcase["input"])),
                },
            }
        )
    return expected, actual


def build_update_report(
    store: Any,
    *,
    content: Mapping[str, Any],
    case_id: str,
    changed_path: str,
    manifests: Sequence[Mapping[str, Any]] = (),
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    expected_subcases: Sequence[Mapping[str, Any]] | None = None,
    actual_subcases: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Complete valid update report fixture (005 §15.4 / repair OR-1/ACC-1)."""
    from app.config import ENGINE_VERSION
    from app.core.hashing import bundle_content_hash
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.updater.reference import B5_CASES, REQUIRED_CASES

    baseline = store.current()
    plan = B5_CASES[case_id]
    subcase_ids = [str(value) for value in plan.get("subcase_ids") or []]
    if expected_subcases is None or actual_subcases is None:
        expected_subcases, actual_subcases = provision_plan_subcases(plan)
    manifest_rows = [dict(manifest) for manifest in manifests]
    evidence_rows = [dict(fact) for fact in evidence_refs]
    required = [case_id]
    static_cases = list(REQUIRED_CASES)
    row = {
        "case_id": case_id,
        "mode": "candidate",
        "input_proof_sha256": canonical_sha(
            {"case_id": case_id, "subcase_ids": subcase_ids}
        ),
        "subcase_ids": subcase_ids,
        "worksheet_sha256": canonical_sha(
            {"case_id": case_id, "path": plan.get("path")}
        ),
        "parameter_evidence_refs": [
            str(fact.get("fact_id"))
            for fact in evidence_rows
            if fact.get("fullpath") == plan.get("path")
        ],
        "stage_match": True,
        "passed": True,
        **subcase_proof_fields(
            expected_subcases,
            actual_subcases,
            subcase_ids=subcase_ids,
        ),
    }
    return {
        "decision": "publish",
        "purpose": "update",
        "binding": {
            "baseline_bundle_id": baseline["bundle_id"],
            "baseline_bundle_hash": baseline["bundle_hash"],
            "baseline_rules_schema_version": baseline["rules_schema_version"],
            "candidate_bundle_hash": bundle_content_hash(content),
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": str(content.get("rules_schema_version")),
            "profiles": _profile_rows(content),
            "manifests_sha256": canonical_sha(manifest_rows),
            "evidence_sha256": canonical_sha(evidence_rows),
            "reference_code_sha256": _reference_code_sha256(),
            "required_case_ids": required,
        },
        "coverage": {
            "changed_paths": [changed_path],
            "affected_case_ids": required,
            "static_case_ids": static_cases,
        },
        "cases": {
            "required_case_ids": required,
            "executed": 1,
            "per_stage_consistent": True,
            "rows": [row],
        },
        "regression": {
            "static_case_ids": static_cases,
            "static_baseline_bundle_hash": bundle_content_hash(INITIAL_BUNDLE_CONTENT),
            "passed": len(static_cases),
            "failed": 0,
            "failed_ids": [],
        },
        "gate_environment": {
            "temp_store_path": "gate-store.db",
            "network_calls": 0,
            "direct_http_attempts": 0,
            "socket_attempts": 0,
            "dns_attempts": 0,
            "session_creations": 0,
            "pointer_write_attempts": 0,
            "observers_restored": True,
            "user_session": False,
            "current_pointer_written": False,
            "outside_temp_db": 0,
            "thread_starts": 0,
            "subprocess_starts": 0,
        },
        "tests": {"passed": max(1, len(required)), "failed": 0, "failed_ids": []},
        "quarantine_reason": None,
    }


def store_snapshot(store: Any) -> dict[str, Any]:
    """Pointer/bundles/publications/audit state snapshot for zero-write checks."""
    conn = store._connect()
    try:
        pointer = conn.execute(
            "SELECT bundle_id FROM current_pointer WHERE id = 1"
        ).fetchone()
        return {
            "pointer_bundle_id": None if pointer is None else str(pointer[0]),
            "rule_bundles": int(
                conn.execute("SELECT COUNT(*) FROM rule_bundles").fetchone()[0]
            ),
            "publications": int(
                conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0]
            ),
            "audit_log": int(
                conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            ),
        }
    finally:
        conn.close()
