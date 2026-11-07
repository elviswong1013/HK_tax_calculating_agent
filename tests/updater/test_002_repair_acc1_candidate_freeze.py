"""P3 repair wave 1 / ACC-1 Red tests: publish proof and written bytes bind one frozen candidate.

REQ mapping:
- ACC-1 / 002 REQ-12 / P3 plan §P3-W1b / 006 §7.3:
  publisher must freeze the candidate at the start of proof verification and
  use that same frozen object for every check and for the store write.  The
  store must receive the verified candidate hash and recompute it inside
  ``_write_lock``/``BEGIN IMMEDIATE``.  A candidate changed between proof
  verification and store freeze (acceptor's profits cap 3200 -> 3201
  interleaving) must be rejected with ``AppError`` and zero partial writes.
  Mutating the caller's original dict after a successful publish must not
  alter the stored bundle.

Red failure reason expected on the current tree: publisher hands the original
mutable ``content`` to the store after verification; the interleaved mutation
is written and reported as success.  No verified hash is rechecked in the
store transaction.
"""

from __future__ import annotations

import copy
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


def _profits_fact(value: str) -> dict:
    return {
        "fact_id": f"fact:test:profits_cap_{value}",
        "fullpath": CAP_PATH,
        "value": value,
        "source_id": "test_profits_source",
        "url": "https://example.invalid/profits",
        "anchor": "heading:Profits Tax#row:2025/26#col:Cap",
        "tier": "T1",
    }


def _install_prepare_bundle_mutation(monkeypatch, mutate) -> None:
    """Simulate candidate mutation between publisher verification and store freeze."""
    from app.rules.store import RuleStore

    original = RuleStore._prepare_bundle

    def _mutating_prepare_bundle(self, *args, **kwargs):
        content = args[0] if args else kwargs.get("content")
        mutate(content)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        RuleStore, "_prepare_bundle", _mutating_prepare_bundle
    )


def _install_prepare_bundle_evidence_mutation(monkeypatch, mutate) -> None:
    """Simulate evidence mutation between publisher verification and store freeze."""
    from app.rules.store import RuleStore

    original = RuleStore._prepare_bundle

    def _mutating_prepare_bundle(self, *args, **kwargs):
        content = args[0] if args else kwargs.get("content")
        evidence_refs = (
            args[1] if len(args) > 1 else kwargs.get("evidence_refs")
        )
        mutate(content, evidence_refs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        RuleStore, "_prepare_bundle", _mutating_prepare_bundle
    )


def _stored_evidence(store, bundle_id: str) -> list[dict]:
    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT evidence_json FROM rule_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"ACC-1/R1: bundle not found: {bundle_id}"
    return json.loads(row[0])


def _publish_update_args(*, store, content: dict, report: dict, fact: dict) -> dict:
    return {
        "job_id": "job-repair-acc1",
        "content": content,
        "evidence_refs": [fact],
        "validation_report": report,
        "purpose": "update",
        "baseline": store.current(),
        "manifests": [PROFITS_MANIFEST],
        "required_cases": (CASE_ID,),
    }


def test_002_repair_acc1_update_rejects_candidate_mutated_before_store_freeze(
    tmp_path, monkeypatch
) -> None:
    from app.updater.publisher import publish_update

    store = seed_published_store(tmp_path, "acc1-update.db")
    content = _profits_candidate("3200")
    fact = _profits_fact("3200")
    report = build_update_report(
        store,
        content=content,
        case_id=CASE_ID,
        changed_path=CAP_PATH,
        manifests=[PROFITS_MANIFEST],
        evidence_refs=[fact],
    )
    before = store_snapshot(store)
    mutations: list[str] = []

    def mutate(candidate) -> None:
        mutations.append(str(candidate["data"]["profits"]["reductions"]["2025_26"]["cap"]))
        candidate["data"]["profits"]["reductions"]["2025_26"]["cap"] = "3201"

    _install_prepare_bundle_mutation(monkeypatch, mutate)

    with pytest.raises(AppError) as excinfo:
        publish_update(
            store,
            **_publish_update_args(store=store, content=content, report=report, fact=fact),
        )
    assert any(
        token in str(excinfo.value).lower()
        for token in ("candidate", "hash", "verified", "freeze")
    ), f"ACC-1: mutation rejection must name candidate binding, got {excinfo.value!r}"
    assert mutations, "ACC-1: test interleaving did not reach _prepare_bundle"
    assert store_snapshot(store) == before, (
        "ACC-1: interleaved candidate change must leave pointer/bundles/"
        "publications/audit untouched (zero partial write)"
    )
    assert content["data"]["profits"]["reductions"]["2025_26"]["cap"] == "3200", (
        "ACC-1: publisher must not let store/_prepare_bundle mutate the caller's "
        "verified candidate object"
    )


def test_002_repair_acc1_seed_rejects_candidate_mutated_before_store_freeze(
    tmp_path, monkeypatch
) -> None:
    from app.rules.store import RuleStore
    from app.updater.publisher import publish_update
    from app.updater.reference import REQUIRED_CASES, release_seed_report

    store = RuleStore(tmp_path / "acc1-seed.db")
    assert store.is_true_empty()
    content = initial_content()
    before = store_snapshot(store)

    def mutate(candidate) -> None:
        candidate["data"]["stamps"]["property"]["related_conveyance"][
            "fixed_duty"
        ] = "999"

    _install_prepare_bundle_mutation(monkeypatch, mutate)

    with pytest.raises(AppError) as excinfo:
        publish_update(
            store,
            job_id="job-repair-acc1-seed",
            content=content,
            evidence_refs=[],
            validation_report=release_seed_report(),
            purpose="seed",
            baseline=None,
            manifests=(),
            required_cases=tuple(REQUIRED_CASES),
        )
    assert any(
        token in str(excinfo.value).lower()
        for token in ("candidate", "hash", "verified", "freeze")
    ), f"ACC-1: seed mutation rejection must name candidate binding, got {excinfo.value!r}"
    assert store.is_true_empty(), (
        "ACC-1: rejected seed must stay a true empty store (zero partial write)"
    )
    assert store_snapshot(store) == before
    assert content["data"]["stamps"]["property"]["related_conveyance"][
        "fixed_duty"
    ] == initial_content()["data"]["stamps"]["property"]["related_conveyance"][
        "fixed_duty"
    ], "ACC-1: frozen release content must not be mutated by the store write"


def test_002_repair_acc1_post_publish_mutation_does_not_change_stored_bundle(
    tmp_path,
) -> None:
    from app.core.hashing import bundle_content_hash
    from app.updater.publisher import publish_update

    store = seed_published_store(tmp_path, "acc1-post-mutation.db")
    content = _profits_candidate("3200")
    fact = _profits_fact("3200")
    report = build_update_report(
        store,
        content=content,
        case_id=CASE_ID,
        changed_path=CAP_PATH,
        manifests=[PROFITS_MANIFEST],
        evidence_refs=[fact],
    )

    published = publish_update(
        store,
        **_publish_update_args(store=store, content=content, report=report, fact=fact),
    )
    stored_before = copy.deepcopy(store.get_bundle(published["bundle_id"]))
    assert stored_before["data"]["profits"]["reductions"]["2025_26"]["cap"] == "3200"

    content["data"]["profits"]["reductions"]["2025_26"]["cap"] = "9999"
    content["data"]["profits"]["reductions"]["2025_26"]["percent_bp"] = 1
    stored_after = store.get_bundle(published["bundle_id"])
    assert stored_after == stored_before, (
        "ACC-1: mutating the caller's original dict after publish must not "
        "change the stored bundle"
    )
    assert bundle_content_hash(stored_after) == published["bundle_hash"]
    assert store.current()["bundle_id"] == published["bundle_id"]


def test_002_repair_acc1_update_rejects_evidence_mutated_before_store_freeze(
    tmp_path, monkeypatch
) -> None:
    """R1: evidence refs must be frozen like the candidate before store write."""
    from app.updater.publisher import publish_update

    store = seed_published_store(tmp_path, "acc1-evidence-update.db")
    content = _profits_candidate("3200")
    fact = _profits_fact("3200")
    report = build_update_report(
        store,
        content=content,
        case_id=CASE_ID,
        changed_path=CAP_PATH,
        manifests=[PROFITS_MANIFEST],
        evidence_refs=[fact],
    )
    caller_evidence = copy.deepcopy(fact)
    before = store_snapshot(store)
    mutations: list[list[dict]] = []

    def mutate(candidate, evidence_refs) -> None:
        mutations.append([dict(row) for row in evidence_refs])
        evidence_refs[0]["value"] = "3201"
        evidence_refs[0]["source_id"] = "mutated-source"

    _install_prepare_bundle_evidence_mutation(monkeypatch, mutate)

    args = _publish_update_args(store=store, content=content, report=report, fact=fact)
    evidence_arg = args["evidence_refs"]

    with pytest.raises(AppError) as excinfo:
        publish_update(store, **args)
    assert any(
        token in str(excinfo.value).lower()
        for token in ("evidence", "hash", "freeze")
    ), (
        "ACC-1/R1: evidence mutation rejection must name the evidence binding/"
        f"hash freeze, got {excinfo.value!r}"
    )
    assert mutations, "ACC-1/R1: test interleaving did not reach _prepare_bundle"
    assert fact == caller_evidence, (
        "ACC-1/R1: publisher must pass a frozen evidence snapshot to the store; "
        "store/_prepare_bundle mutations must not rewrite the caller's fact dict"
    )
    assert evidence_arg == [caller_evidence], (
        "ACC-1/R1: caller's evidence_refs list must not be rewritten"
    )
    assert store_snapshot(store) == before, (
        "ACC-1/R1: evidence mutation must leave pointer/bundles/publications/"
        "audit untouched (zero partial write)"
    )


def test_002_repair_acc1_post_publish_evidence_mutation_keeps_stored_evidence(
    tmp_path,
) -> None:
    """R1: stored evidence canonical SHA must equal the report binding."""
    from app.updater.publisher import publish_update

    store = seed_published_store(tmp_path, "acc1-evidence-post.db")
    content = _profits_candidate("3200")
    fact = _profits_fact("3200")
    report = build_update_report(
        store,
        content=content,
        case_id=CASE_ID,
        changed_path=CAP_PATH,
        manifests=[PROFITS_MANIFEST],
        evidence_refs=[fact],
    )

    published = publish_update(
        store,
        **_publish_update_args(store=store, content=content, report=report, fact=fact),
    )
    stored_before = _stored_evidence(store, published["bundle_id"])
    expected_sha = report["binding"]["evidence_sha256"]
    assert canonical_sha(stored_before) == expected_sha, (
        "ACC-1/R1: stored evidence canonical SHA must equal "
        "binding.evidence_sha256 immediately after publish"
    )

    fact["value"] = "9999"
    fact["source_id"] = "mutated-source"

    stored_after = _stored_evidence(store, published["bundle_id"])
    assert stored_after == stored_before, (
        "ACC-1/R1: mutating the caller's original evidence dict after publish "
        "must not change the stored evidence_json"
    )
    assert canonical_sha(stored_after) == expected_sha, (
        "ACC-1/R1: stored evidence canonical SHA must remain equal to "
        "binding.evidence_sha256 after caller-side mutation"
    )
