"""P3 repair wave 1 / OR-1 Red tests: B5 subcase evidence must decide publish.

REQ mapping:
- OR-1 / 002 REQ-12 / 005 §15.4 / 006 §7.1-7.2:
  * ReferenceHarness and CandidateEngine must execute the *same*
    proof-derived B5 subcase boundary inputs (not the historical static
    inputs) when the independent proof changes a cap/threshold.
  * run_gate must compare every actual subcase result with the expected
    subcase result (amount/stage/identity).  Any mismatch => quarantine,
    stage_match=false, row passed=false, explicit reason.
  * The case row must carry canonical subcase proof summaries; publisher
    must reject missing/tampered summaries.
- The oracle reproduction uses profits rebate cap=3500: the stale reference
  last subcase expected reduction=3000/final=1; the fixed candidate engine
  executes proof-derived inputs and produces a different actual (today
  3500/1).  The mismatch must quarantine, not publish.  The test deliberately
  does not pin the pre-fix historical actual 3001/0/false.

Red failure reason expected on the current tree: gate ignores
``subcase_results`` and returns ``decision="publish"``; rows lack the
subcase proof fields; publisher accepts reports with missing/tampered
subcase summaries.
"""

from __future__ import annotations

import copy

import pytest

from app.core.errors import AppError

from _fakes import (
    AVD_CHANGED,
    AVD_FULLPATH,
    AVD_MANIFEST,
    FACT_AVD_110,
    avd_changed_bundle_content,
    initial_content,
    manifest_with_slot,
    seed_published_store,
)
from _repair1_helpers import (
    build_update_report,
    store_snapshot,
    subcase_proof_fields,
)

CASE_ID = "a_profits_rebate_cap"
CAP_PATH = "profits.reductions.2025_26.cap"

FACT_3500 = {
    "fact_id": "fact:test:profits_cap_3500",
    "fullpath": CAP_PATH,
    "value": "3500",
    "source_id": "test_profits_source",
    "url": "https://example.invalid/profits",
    "anchor": "heading:Profits Tax#row:2025/26#col:Cap",
    "tier": "T1",
}

PROFITS_MANIFEST = manifest_with_slot(
    CAP_PATH,
    source_id="test_profits_source",
    profile_id="profits_reduction_v1",
    constraints={"min": "0", "max": "10000", "step": "1"},
    anchor="heading:Profits Tax#row:2025/26#col:Cap",
)


def _candidate(cap: str = "3500") -> dict:
    content = initial_content()
    content["data"]["profits"]["reductions"]["2025_26"]["cap"] = cap
    return content


def _fact(value: str = "3500") -> dict:
    fact = copy.deepcopy(FACT_3500)
    fact["value"] = value
    fact["fact_id"] = f"fact:test:profits_cap_{value}"
    return fact


def _stale_oracle_expected() -> dict:
    """Oracle reproduction: reference still carries the old 3000 cap fixture."""
    from app.updater.reference import B5_CASES

    plan = B5_CASES[CASE_ID]
    return {
        "case_id": CASE_ID,
        "subcase_ids": list(plan["subcase_ids"]),
        "stages": ["3500"],
        "output": "3500",
        "parameter_evidence_ref": FACT_3500["fact_id"],
        "subcase_results": [
            {
                "subcase_id": str(subcase["subcase_id"]),
                "input": copy.deepcopy(dict(subcase["input"])),
                "expected": copy.deepcopy(dict(subcase["expected"])),
                "parameter_evidence_ref": FACT_3500["fact_id"],
            }
            for subcase in plan["subcases"]
        ],
    }


class _FixedHarness:
    def __init__(self, result: dict) -> None:
        self._result = copy.deepcopy(result)

    def run_case(self, case_id: str, **kwargs) -> dict:
        assert case_id == CASE_ID
        return copy.deepcopy(self._result)


def _gate_kwargs(candidate: dict, *, facts=(FACT_3500,), tmp_path=None) -> dict:
    from app.core.hashing import bundle_content_hash

    return {
        "candidate_content": candidate,
        "current_content": initial_content(),
        "manifests": [PROFITS_MANIFEST],
        "independent_facts": list(facts),
        "required_cases": [CASE_ID],
        "candidate_hash": bundle_content_hash(candidate),
        "changed_paths": [CAP_PATH],
        "required_case_map": {CAP_PATH: CASE_ID},
        "approved_temp_root": None if tmp_path is None else str(tmp_path),
    }


def test_002_repair_or1_gate_quarantines_oracle_subcase_mismatch(tmp_path) -> None:
    """OR-1 oracle: stale expected 3000/1 vs proof-derived actual must quarantine."""
    from app.updater.reference import CandidateEngine
    from app.updater.testgate import run_gate

    candidate = _candidate("3500")
    expected = _stale_oracle_expected()
    engine = CandidateEngine()
    actual = engine.run_case(CASE_ID, candidate)

    last_expected = expected["subcase_results"][-1]
    last_actual = actual["subcase_results"][-1]
    assert last_expected["expected"] == {"reduction": "3000", "final": "1"}
    actual_amounts = last_actual["actual"]
    assert any(
        actual_amounts.get(key) != value
        for key, value in last_expected["expected"].items()
    ), (
        "OR-1: the stale 3000-cap expectation must differ from the fixed "
        f"engine's proof-derived actual: expected={last_expected!r} "
        f"actual={last_actual!r}"
    )

    report = run_gate(
        harness=_FixedHarness(expected),
        engine=engine,
        **_gate_kwargs(candidate, tmp_path=tmp_path),
    )
    assert report["decision"] == "quarantine", (
        "OR-1: a B5 subcase actual/expected mismatch must never publish: "
        f"{report!r}"
    )
    assert report["cases"]["per_stage_consistent"] is False, (
        "OR-1: per_stage_consistent must include executed subcase results: "
        f"{report['cases']!r}"
    )
    row = report["cases"]["rows"][0]
    assert row["stage_match"] is False
    assert row["passed"] is False
    reason = str(report.get("quarantine_reason") or "")
    assert (
        "subcase" in reason.lower()
        or "stage_inconsistent" in reason
        or "stage_mismatch" in reason
    ), f"OR-1: explicit subcase/stage mismatch reason required, got {reason!r}"
    fail_reasons = " ".join(str(item) for item in report["tests"]["fail_reasons"])
    assert (
        "subcase" in fail_reasons.lower()
        or "stage_inconsistent" in fail_reasons
        or "stage_mismatch" in fail_reasons
    ), f"OR-1: tests.fail_reasons must name the subcase mismatch: {fail_reasons!r}"
    assert int(report["tests"]["failed"]) >= 1

    fields = subcase_proof_fields(
        expected["subcase_results"],
        actual["subcase_results"],
        subcase_ids=expected["subcase_ids"],
    )
    for key, value in fields.items():
        assert row.get(key) == value, (
            f"OR-1: case row must bind {key} to the executed subcase proof: "
            f"expected={value!r} actual={row.get(key)!r}"
        )


AVD_CASE_ID = "a_avd_related_fixed"


def test_002_repair_or1_avd_static_expected_pass_false_does_not_quarantine(
    tmp_path,
) -> None:
    """Regression: gate compares harness expected 110 with engine actual 110.

    CandidateEngine still carries the static B5 plan expectation 100 for the
    AVD path, so its own subcase ``passed`` is False for candidate 110.  The
    gate must not turn that local flag into an unconditional quarantine; it
    must compare the independent harness expected (110) with the engine
    actual (110).
    """
    from app.core.hashing import bundle_content_hash
    from app.updater.reference import CandidateEngine, ReferenceHarness
    from app.updater.testgate import run_gate

    candidate = avd_changed_bundle_content(AVD_CHANGED)
    harness = ReferenceHarness(proof_facts=[FACT_AVD_110])
    engine = CandidateEngine()

    expected = harness.run_case(AVD_CASE_ID)
    actual = engine.run_case(AVD_CASE_ID, candidate)
    harness_subcase = expected["subcase_results"][0]
    engine_subcase = actual["subcase_results"][0]
    assert harness_subcase["expected"] == {"fixed_duty": AVD_CHANGED}
    # Regression precondition: the engine still has the static plan
    # expectation 100, so its local passed flag is False for candidate 110.
    assert engine_subcase["expected"] == {"fixed_duty": "100"}
    assert engine_subcase["actual"] == {"fixed_duty": AVD_CHANGED}
    assert engine_subcase["passed"] is False

    report = run_gate(
        candidate_content=candidate,
        current_content=initial_content(),
        manifests=[AVD_MANIFEST],
        independent_facts=[FACT_AVD_110],
        required_cases=[AVD_CASE_ID],
        harness=harness,
        engine=engine,
        candidate_hash=bundle_content_hash(candidate),
        changed_paths=[AVD_FULLPATH],
        required_case_map={AVD_FULLPATH: AVD_CASE_ID},
        approved_temp_root=str(tmp_path),
    )
    assert report["decision"] == "publish", (
        "OR-1 regression: gate compares harness expected 110 with engine "
        "actual 110; the engine's static-100 local passed=False must not "
        f"quarantine a proof-consistent AVD case: {report!r}"
    )
    row = report["cases"]["rows"][0]
    assert row["passed"] is True
    assert row["stage_match"] is True


@pytest.mark.parametrize("mutate_mode", ["missing", "extra"])
def test_002_repair_or1_gate_rejects_missing_or_extra_subcase(
    tmp_path, mutate_mode: str
) -> None:
    """OR-1: a missing or extra B5 subcase must quarantine even if top-level matches."""
    from app.updater.reference import CandidateEngine
    from app.updater.testgate import run_gate

    candidate = _candidate("3500")
    expected = _stale_oracle_expected()
    if mutate_mode == "missing":
        expected["subcase_results"].pop()
    else:
        extra = copy.deepcopy(expected["subcase_results"][-1])
        extra["subcase_id"] = "profits_rebate_extra"
        expected["subcase_results"].append(extra)

    report = run_gate(
        harness=_FixedHarness(expected),
        engine=CandidateEngine(),
        **_gate_kwargs(candidate, tmp_path=tmp_path),
    )
    assert report["decision"] == "quarantine", (
        f"OR-1: {mutate_mode} subcase evidence must not publish: {report!r}"
    )
    assert report["cases"]["per_stage_consistent"] is False
    row = report["cases"]["rows"][0]
    assert row["stage_match"] is False
    assert row["passed"] is False
    reason = str(report.get("quarantine_reason") or "").lower()
    assert any(
        token in reason
        for token in ("subcase", "missing", "extra", "stage_inconsistent")
    ), f"OR-1: {mutate_mode} subcase rejection needs an explicit reason: {reason!r}"


def test_002_repair_or1_reference_derives_new_cap_boundary(tmp_path) -> None:
    """OR-1: cap=3500 must derive fresh below/at/above-cap subcases for both seams."""
    from app.updater.reference import B5_CASES, CandidateEngine, ReferenceHarness
    from app.updater.testgate import run_gate

    candidate = _candidate("3500")
    plan = B5_CASES[CASE_ID]
    historical_inputs = [
        copy.deepcopy(dict(subcase["input"])) for subcase in plan["subcases"]
    ]
    harness = ReferenceHarness(proof_facts=[FACT_3500])
    expected = harness.run_case(CASE_ID)
    expected_subcases = expected.get("subcase_results")
    assert isinstance(expected_subcases, list) and expected_subcases, (
        "OR-1: ReferenceHarness must expose proof-derived subcase expectations: "
        f"{expected!r}"
    )
    assert len(expected_subcases) == len(plan["subcase_ids"]), (
        "OR-1: derived subcase set must stay aligned with the fixed case plan: "
        f"{expected_subcases!r}"
    )

    derived_inputs = [
        copy.deepcopy(dict(entry["input"])) for entry in expected_subcases
    ]
    assert derived_inputs != historical_inputs, (
        "OR-1: changing the cap cannot keep the historical fixed subcase inputs: "
        f"{derived_inputs!r}"
    )

    engine = CandidateEngine()
    actual = engine.run_case(CASE_ID, candidate)
    actual_subcases = actual.get("subcase_results")
    assert isinstance(actual_subcases, list)
    assert [entry["input"] for entry in actual_subcases] == [
        entry["input"] for entry in expected_subcases
    ], (
        "OR-1: ReferenceHarness and CandidateEngine must execute the same "
        f"derived inputs; expected={expected_subcases!r} actual={actual_subcases!r}"
    )

    for expected_entry, actual_entry in zip(expected_subcases, actual_subcases):
        assert actual_entry.get("passed") is True, (
            "OR-1: proof-derived expected and candidate actual must agree: "
            f"expected={expected_entry!r} actual={actual_entry!r}"
        )
        expected_amounts = expected_entry["expected"]
        for key, value in expected_amounts.items():
            assert actual_entry["actual"].get(key) == value, (
                f"OR-1: subcase {expected_entry['subcase_id']!r} {key} mismatch: "
                f"expected={value!r} actual={actual_entry['actual'].get(key)!r}"
            )

    reductions = [int(entry["expected"]["reduction"]) for entry in expected_subcases]
    assert max(reductions) == 3500, (
        "OR-1: derived cap=3500 boundary must actually apply the new cap: "
        f"{reductions!r}"
    )
    assert min(reductions) < 3500, (
        "OR-1: derived boundary must retain a below-cap branch: " f"{reductions!r}"
    )
    assert any(
        int(entry["actual"]["before"]) > 3500 for entry in actual_subcases
    ), (
        "OR-1: at least one derived subcase must cross above the new cap "
        f"boundary: {actual_subcases!r}"
    )

    report = run_gate(
        harness=harness,
        engine=engine,
        **_gate_kwargs(candidate, tmp_path=tmp_path),
    )
    assert report["decision"] == "publish", (
        "OR-1: proof-derived, fully consistent cap=3500 subcases should pass: "
        f"{report.get('quarantine_reason')!r}"
    )
    row = report["cases"]["rows"][0]
    assert row["stage_match"] is True
    assert row["passed"] is True
    fields = subcase_proof_fields(
        expected_subcases,
        actual_subcases,
        subcase_ids=expected["subcase_ids"],
    )
    for key, value in fields.items():
        assert row.get(key) == value, (
            f"OR-1: consistent case row must bind {key}: "
            f"expected={value!r} actual={row.get(key)!r}"
        )


def _publisher_report(*, store, content: dict, fact: dict) -> dict:
    return build_update_report(
        store,
        content=content,
        case_id=CASE_ID,
        changed_path=CAP_PATH,
        manifests=[PROFITS_MANIFEST],
        evidence_refs=[fact],
    )


def _publish_kwargs(*, store, content: dict, report: dict, fact: dict) -> dict:
    return {
        "job_id": "job-repair-or1-proof",
        "content": content,
        "evidence_refs": [fact],
        "validation_report": report,
        "purpose": "update",
        "baseline": store.current(),
        "manifests": [PROFITS_MANIFEST],
        "required_cases": (CASE_ID,),
    }


def test_002_repair_or1_publisher_rejects_missing_subcase_digests(tmp_path) -> None:
    from app.updater.publisher import publish_update

    store = seed_published_store(tmp_path, "or1-missing-digest.db")
    content = _candidate("3200")
    fact = _fact("3200")
    report = _publisher_report(store=store, content=content, fact=fact)
    row = report["cases"]["rows"][0]
    for key in (
        "expected_subcases_sha256",
        "actual_subcases_sha256",
        "subcase_results_sha256",
    ):
        row.pop(key, None)
    before = store_snapshot(store)

    with pytest.raises(AppError) as excinfo:
        publish_update(store, **_publish_kwargs(store=store, content=content, report=report, fact=fact))
    assert any(
        token in str(excinfo.value).lower() for token in ("subcase", "proof", "sha")
    ), f"OR-1: missing subcase digests must be named, got {excinfo.value!r}"
    assert store_snapshot(store) == before, (
        "OR-1: rejecting missing subcase proof must write nothing"
    )


@pytest.mark.parametrize("tamper_mode", ["digest", "payload"])
def test_002_repair_or1_publisher_rejects_tampered_subcase_digest(
    tmp_path, tamper_mode: str
) -> None:
    from app.updater.publisher import publish_update

    store = seed_published_store(tmp_path, f"or1-tampered-{tamper_mode}.db")
    content = _candidate("3200")
    fact = _fact("3200")
    report = _publisher_report(store=store, content=content, fact=fact)
    row = report["cases"]["rows"][0]
    if tamper_mode == "digest":
        row["expected_subcases_sha256"] = "sha256:" + "00" * 32
    else:
        # Keep the declared digest fields but change the bound evidence: a
        # summary that no longer hashes the payload must be rejected.
        row["expected_subcases"][-1]["expected"]["reduction"] = "3001"
    before = store_snapshot(store)

    with pytest.raises(AppError) as excinfo:
        publish_update(store, **_publish_kwargs(store=store, content=content, report=report, fact=fact))
    assert any(
        token in str(excinfo.value).lower() for token in ("subcase", "proof", "sha")
    ), f"OR-1: tampered subcase proof must be named, got {excinfo.value!r}"
    assert store_snapshot(store) == before, (
        "OR-1: rejecting tampered subcase proof must write nothing"
    )
