"""P3 repair wave 1 / store evidence-conflict Red tests (narrow ``_publish_in_txn`` fix).

REQ mapping:
- 002 REQ-12 / 005 §15.4 / C8.4 publish proof binding: every publication must be
  bound to the evidence that the publisher verified for *that* publication.  The
  stored ``rule_bundles.evidence_json`` for a bundle id must therefore equal the
  evidence supplied for the publication that made it current.
- Final-2 acceptor regression: ``_publish_in_txn`` uses
  ``INSERT ... ON CONFLICT(bundle_id) DO NOTHING``.  Republishing a previously
  stored bundle with different evidence keeps the old evidence, yet still
  inserts a publication, switches the current pointer and returns success
  (real sequence 3200 -> 3300 -> 3200: stored fact stays at the first 3200
  evidence and ``matches_report`` is false).

Authorized narrow fix contract pinned here:
- inside the store transaction, an existing ``rule_bundles`` row whose
  ``evidence_json`` differs from the evidence prepared for this publication must
  raise ``AppError`` naming the evidence/bundle conflict; the transaction rolls
  back with zero partial writes (pointer/bundles/publications/audit unchanged,
  historical evidence not overwritten);
- republishing with byte-identical evidence stays allowed (idempotent), with no
  inconsistent evidence stored.

Red failure reason expected on the current tree: the third publish succeeds
(``DID NOT RAISE AppError``), the pointer moves back to the old bundle while its
stored evidence remains the first evidence, and publications/audit grow.

Deterministic and offline: a temporary ``RuleStore`` plus the existing
publisher/gate fixture helper; the real publisher validation path is used, but
no updater, network, or clock is involved.
"""

from __future__ import annotations

import json

import pytest

from app.core.errors import AppError

from _fakes import initial_content, manifest_with_slot, seed_published_store
from _repair1_helpers import build_update_report, canonical_sha, store_snapshot

CASE_ID = "a_profits_rebate_cap"
CAP_PATH = "profits.reductions.2025_26.cap"

PROFITS_MANIFEST = manifest_with_slot(
    CAP_PATH,
    source_id="test_profits_source",
    profile_id="profits_reduction_v1",
    constraints={"min": "0", "max": "10000", "step": "1"},
    anchor="heading:Profits Tax#row:2025/26#col:Cap",
)


def _profits_candidate(cap: str) -> dict:
    content = initial_content()
    content["data"]["profits"]["reductions"]["2025_26"]["cap"] = cap
    return content


def _profits_fact(value: str, tag: str) -> dict:
    """Independent T1 fact; ``tag`` keeps the evidence distinct across rounds."""
    return {
        "fact_id": f"fact:test:profits_cap_{value}:{tag}",
        "fullpath": CAP_PATH,
        "value": value,
        "source_id": "test_profits_source",
        "url": "https://example.invalid/profits",
        "anchor": "heading:Profits Tax#row:2025/26#col:Cap",
        "tier": "T1",
    }


def _stored_evidence(store, bundle_id: str) -> list[dict]:
    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT evidence_json FROM rule_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"store evidence conflict: bundle not found: {bundle_id}"
    return json.loads(row[0])


def _build_report(store, content: dict, fact: dict) -> dict:
    return build_update_report(
        store,
        content=content,
        case_id=CASE_ID,
        changed_path=CAP_PATH,
        manifests=[PROFITS_MANIFEST],
        evidence_refs=[fact],
    )


def _publish_via_publisher(
    store,
    content: dict,
    fact: dict,
    *,
    job_id: str,
    report: dict | None = None,
) -> tuple[dict, dict]:
    """Publish through the real publisher gate; returns (result, report)."""
    from app.updater.publisher import publish_update

    if report is None:
        report = _build_report(store, content, fact)
    result = publish_update(
        store,
        job_id=job_id,
        content=content,
        evidence_refs=[fact],
        validation_report=report,
        purpose="update",
        baseline=store.current(),
        manifests=[PROFITS_MANIFEST],
        required_cases=(CASE_ID,),
    )
    return result, report


def _assert_evidence_conflict_error(excinfo: pytest.ExceptionInfo[AppError]) -> None:
    message = str(excinfo.value)
    lowered = message.lower()
    assert any(
        token in lowered
        for token in ("evidence", "bundle", "conflict", "证据", "冲突", "既有")
    ), (
        "repeated bundle with different evidence must be rejected naming the "
        f"evidence/bundle conflict, got {message!r}"
    )


def test_002_repair_store_rejects_republishing_existing_bundle_with_new_evidence(
    tmp_path,
) -> None:
    """3200(first evidence) -> 3300(second) -> 3200(third, different evidence)."""
    store = seed_published_store(tmp_path, "storefix-republish.db")

    content_a = _profits_candidate("3200")
    fact_first = _profits_fact("3200", "first")
    first, first_report = _publish_via_publisher(
        store, content_a, fact_first, job_id="job-storefix-first"
    )
    stored_first = _stored_evidence(store, first["bundle_id"])
    assert canonical_sha(stored_first) == first_report["binding"]["evidence_sha256"], (
        "precondition: the first publication stores the first evidence"
    )

    content_b = _profits_candidate("3300")
    fact_second = _profits_fact("3300", "second")
    second, _second_report = _publish_via_publisher(
        store, content_b, fact_second, job_id="job-storefix-second"
    )
    assert store.current()["bundle_id"] == second["bundle_id"], (
        "precondition: the second publication is current"
    )

    # The third publication reuses the first bundle id but carries new evidence.
    fact_third = _profits_fact("3200", "third")
    third_report = _build_report(store, _profits_candidate("3200"), fact_third)
    third_evidence_sha = third_report["binding"]["evidence_sha256"]
    assert third_evidence_sha == canonical_sha([fact_third])
    assert third_evidence_sha != first_report["binding"]["evidence_sha256"], (
        "precondition: the third evidence differs from the stored evidence"
    )

    before = store_snapshot(store)
    before_current = store.current()

    with pytest.raises(AppError) as excinfo:
        _publish_via_publisher(
            store,
            _profits_candidate("3200"),
            fact_third,
            job_id="job-storefix-third",
            report=third_report,
        )
    _assert_evidence_conflict_error(excinfo)

    assert store_snapshot(store) == before, (
        "store must reject inside the transaction with zero partial writes: "
        "pointer/bundles/publications/audit must not change"
    )
    assert store.current() == before_current, (
        "rejected third publication must leave the pointer on the second bundle"
    )
    assert store.current()["bundle_id"] == second["bundle_id"]
    assert _stored_evidence(store, first["bundle_id"]) == stored_first, (
        "historical evidence must not be overwritten by the rejected publication"
    )
    assert canonical_sha(_stored_evidence(store, first["bundle_id"])) != (
        third_evidence_sha
    ), "stored evidence must not silently become the report-bound third evidence"


def test_002_repair_store_allows_idempotent_republish_with_identical_evidence(
    tmp_path,
) -> None:
    """Repeating a stored bundle with byte-identical evidence stays allowed."""
    store = seed_published_store(tmp_path, "storefix-idempotent.db")

    content_a = _profits_candidate("3200")
    fact_first = _profits_fact("3200", "first")
    first, first_report = _publish_via_publisher(
        store, content_a, fact_first, job_id="job-storefix-idem-first"
    )
    stored_first = _stored_evidence(store, first["bundle_id"])

    content_b = _profits_candidate("3300")
    _publish_via_publisher(
        store,
        content_b,
        _profits_fact("3300", "second"),
        job_id="job-storefix-idem-second",
    )

    republished, republish_report = _publish_via_publisher(
        store,
        _profits_candidate("3200"),
        fact_first,
        job_id="job-storefix-idem-repeat",
    )

    assert republished["bundle_id"] == first["bundle_id"], (
        "repeated content must resolve to the same stored bundle id"
    )
    assert store.current()["bundle_id"] == first["bundle_id"]
    assert republish_report["binding"]["evidence_sha256"] == (
        first_report["binding"]["evidence_sha256"]
    )
    assert _stored_evidence(store, first["bundle_id"]) == stored_first, (
        "idempotent republish must not add inconsistent evidence"
    )
    assert canonical_sha(_stored_evidence(store, first["bundle_id"])) == (
        republish_report["binding"]["evidence_sha256"]
    ), "stored evidence must match the republished report binding"


def test_002_repair_store_publish_rejects_repeated_bundle_with_different_evidence(
    tmp_path,
) -> None:
    """The guard belongs to the store transaction, not only to the publisher."""
    from app.rules.store import RuleStore

    store = RuleStore(tmp_path / "storefix-direct.db")

    content_a = _profits_candidate("3200")
    fact_first = _profits_fact("3200", "first")
    first = store.publish(content_a, [fact_first])
    stored_first = _stored_evidence(store, first["bundle_id"])

    second = store.publish(
        _profits_candidate("3300"), [_profits_fact("3300", "second")]
    )
    assert store.current()["bundle_id"] == second["bundle_id"]

    fact_third = _profits_fact("3200", "third")
    assert canonical_sha([fact_third]) != canonical_sha(stored_first), (
        "precondition: direct third evidence differs from stored evidence"
    )

    before = store_snapshot(store)
    before_current = store.current()

    with pytest.raises(AppError) as excinfo:
        store.publish(_profits_candidate("3200"), [fact_third])
    _assert_evidence_conflict_error(excinfo)

    assert store_snapshot(store) == before, (
        "RuleStore.publish must reject the repeated bundle inside its "
        "transaction with zero partial writes"
    )
    assert store.current() == before_current
    assert store.current()["bundle_id"] == second["bundle_id"]
    assert _stored_evidence(store, first["bundle_id"]) == stored_first, (
        "RuleStore.publish must not overwrite historical evidence"
    )
