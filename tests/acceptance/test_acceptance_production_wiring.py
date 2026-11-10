"""REQ-17/18/C7.1 production manifest typed slots + change/quarantine/publish.

P3-W1a migration:
- A1: six old salary/PA paths are stop-auto (observer only); the real
  manifest.slots cover exactly the eight unaffected paths; the six observation
  descriptors live in app.main.STOP_AUTO_OBSERVATIONS (seven fields) and are
  attached to source entries as observer_slots; anchors/seven fields/fullpath/
  confirmed/8 sources/harness/engine/required_cases bindings are retained.
- A2: salary cap change -> observer pending only (no publish, pointer unchanged,
  pending persists); band structure -> semantic_change; unaffected path with
  one-sided change -> quarantined; unaffected path with two independent sources
  + test-only B5 required case -> publish (D1=a / D2).
"""

from __future__ import annotations

from _acc_helpers import (
    AVD_CHANGED,
    AVD_FULLPATH,
    B5_REQUIRED_CASE,
    FakeClock,
    FakeOutbound,
    _testonly_avd_source,
    b5_engine,
    b5_harness,
    hk,
    inject_scheduler_seams,
    observation_overrides,
    observer_pending_rows,
    production_round_documents,
    resolve_bundle_path,
    scheduler_binding,
    scheduler_sources,
    stop_auto_carriers,
)

SLOT_SEVEN_FIELDS = frozenset(
    {
        "profile_ID",
        "fullpath",
        "type",
        "unit",
        "constraints",
        "anchor",
        "proof_role",
    }
)

CAP_PATH = "salaries.reductions.2025_26.cap"
CAP_NEW_VALUE = "3500"
BAND_STRUCTURE_PATH = "salaries.progressive_bands.5.rate_bp"

SIX_STOP_AUTO_PATHS = frozenset(
    {
        "salaries.allowances.2025_26.basic",
        "salaries.allowances.2025_26.married",
        "salaries.allowances.2026_27.basic",
        "salaries.mpf_deduction_cap",
        "salaries.reductions.2024_25.cap",
        "salaries.reductions.2025_26.cap",
    }
)
EIGHT_UNAFFECTED_PATHS = frozenset(
    {
        "stamps.property.related_conveyance.fixed_duty",
        "stamps.stock.other_transfer_fixed",
        "stamps.lease.rent_ceiling_unit",
        "stamps.stock.effective_from",
        "property.years.2025_26.standard_rate",
        "property.repair_allowance_ratio",
        "profits.two_tier_threshold",
        "profits.reductions.2025_26.cap",
    }
)


def _cap_carrier_ids(sources) -> list[str]:
    return stop_auto_carriers(sources, CAP_PATH)


def _cap_overrides(sources, value: str, *, only_source_id: str | None = None) -> dict:
    if only_source_id is None:
        return observation_overrides(sources, CAP_PATH, value)
    return (
        {only_source_id: {CAP_PATH: value}}
        if only_source_id in _cap_carrier_ids(sources)
        else {}
    )


def test_acceptance_production_manifests_typed_slots_and_gate_binding(make_acc) -> None:
    acc = make_acc("prodwiring")
    scheduler = acc.app.state.update_scheduler
    sources = scheduler_sources(scheduler)
    assert len(sources) == 9, (
        "production allowlist must have 9 sources (8 HTML + 1 law PDF, H2-B), "
        f"actual {len(sources)}"
    )
    bundle = acc.store.get_bundle(acc.store.current()["bundle_id"])
    bundle_data = bundle.get("data") or {}
    problems: list[str] = []
    slot_paths: set[str] = set()

    for entry in sources:
        source_id = str(entry.get("source_id") or "")
        manifest = entry.get("manifest") or {}
        label = f"source {source_id!r}"

        anchors = manifest.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            problems.append(f"{label}: anchors must be non-empty (C7.2/C7.3)")
            anchors = []
        anchor_confirmed: dict[str, str] = {}
        for index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict) or set(anchor) != {
                "path_regex",
                "anchor_status",
            }:
                problems.append(f"{label}: anchors[{index}] keys must be exact")
                continue
            path_regex = anchor.get("path_regex")
            if not isinstance(path_regex, str) or not path_regex:
                problems.append(f"{label}: anchors[{index}].path_regex must be non-empty")
                continue
            anchor_confirmed[path_regex] = str(anchor.get("anchor_status") or "")

        slots = manifest.get("slots")
        observer_slots = entry.get("observer_slots") or []
        if not isinstance(slots, list):
            problems.append(f"{label}: slots must be a list (C7.1)")
            slots = []
        if not slots and not observer_slots:
            problems.append(
                f"{label}: empty slots only allowed for all-stop-auto sources (005 15.3)"
            )
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                problems.append(f"{label}: slots[{index}] must be a mapping")
                continue
            keys = set(slot)
            if keys != SLOT_SEVEN_FIELDS:
                problems.append(
                    f"{label}: slots[{index}] must have exactly seven fields "
                    f"{sorted(SLOT_SEVEN_FIELDS)}, actual {sorted(keys)}"
                )
                continue
            fullpath = slot.get("fullpath")
            if not isinstance(fullpath, str) or not fullpath:
                problems.append(f"{label}: slots[{index}].fullpath must be non-empty")
                continue
            slot_paths.add(fullpath)
            resolved = resolve_bundle_path(bundle_data, fullpath)
            if resolved is None:
                problems.append(
                    f"{label}: slots[{index}].fullpath {fullpath!r} must resolve "
                    "inside the published bundle (C7.1)"
                )
            elif not isinstance(resolved, str) or not resolved:
                problems.append(
                    f"{label}: slots[{index}].fullpath {fullpath!r} must resolve "
                    "to a canonical string value (C2)"
                )
            if not isinstance(slot.get("unit"), str) or not slot.get("unit"):
                problems.append(f"{label}: slots[{index}].unit must be non-empty")
            constraints = slot.get("constraints")
            if not isinstance(constraints, dict) or not constraints:
                problems.append(f"{label}: slots[{index}].constraints must be non-empty")
            slot_anchor = slot.get("anchor")
            if not isinstance(slot_anchor, str) or not slot_anchor:
                problems.append(f"{label}: slots[{index}].anchor must be non-empty")
            elif slot_anchor not in anchor_confirmed:
                problems.append(
                    f"{label}: slots[{index}].anchor must reference anchors[]: "
                    f"{slot_anchor!r}"
                )
            elif anchor_confirmed.get(slot_anchor) != "confirmed":
                problems.append(
                    f"{label}: value-bearing anchor must be confirmed (C7.3): "
                    f"{slot_anchor!r}"
                )

    # D3: real slots exactly cover the eight unaffected paths; no old paths.
    if slot_paths != set(EIGHT_UNAFFECTED_PATHS):
        problems.append(
            "real manifest.slots must cover exactly the eight unaffected paths "
            f"(005:856); missing {sorted(set(EIGHT_UNAFFECTED_PATHS) - slot_paths)}, "
            f"extra {sorted(slot_paths - set(EIGHT_UNAFFECTED_PATHS))}"
        )
    leaked = sorted(slot_paths & set(SIX_STOP_AUTO_PATHS))
    if leaked:
        problems.append(f"six old paths must not enter real manifest.slots: {leaked}")

    import app.main as app_main

    observations = getattr(app_main, "STOP_AUTO_OBSERVATIONS", None)
    if not isinstance(observations, dict):
        problems.append(
            "production must define STOP_AUTO_OBSERVATIONS (source_id -> seven-field "
            "observation descriptors; not a manifest field)"
        )
        observations = {}
    observed_paths: set[str] = set()
    observed_count = 0
    for source_id, described in observations.items():
        if not isinstance(described, list):
            problems.append(f"STOP_AUTO_OBSERVATIONS[{source_id!r}] must be a list")
            continue
        for index, slot in enumerate(described):
            observed_count += 1
            if not isinstance(slot, dict) or set(slot) != SLOT_SEVEN_FIELDS:
                problems.append(
                    f"STOP_AUTO_OBSERVATIONS[{source_id!r}][{index}] must have "
                    "exactly seven fields (C7.1)"
                )
                continue
            observed_paths.add(str(slot.get("fullpath")))
    if observed_paths != set(SIX_STOP_AUTO_PATHS):
        problems.append(
            "STOP_AUTO_OBSERVATIONS must cover exactly the six old salary/PA paths "
            f"(005 15.3); missing {sorted(set(SIX_STOP_AUTO_PATHS) - observed_paths)}, "
            f"extra {sorted(observed_paths - set(SIX_STOP_AUTO_PATHS))}"
        )
    if observed_count < len(SIX_STOP_AUTO_PATHS):
        problems.append(
            "STOP_AUTO_OBSERVATIONS must retain all old source instances "
            f"(>=6), actual {observed_count}"
        )
    for entry in sources:
        source_id = str(entry.get("source_id") or "")
        attached = entry.get("observer_slots") or []
        expected = observations.get(source_id) or []
        if attached != expected:
            problems.append(
                f"source {source_id!r}: source entry must carry observer_slots "
                "equal to STOP_AUTO_OBSERVATIONS; observation copy must not enter "
                "manifest.slots"
            )

    harness = scheduler_binding(scheduler, "harness")
    if harness is None:
        problems.append("production Scheduler is missing the independent harness (C8.1)")
    engine = scheduler_binding(scheduler, "engine")
    if engine is None:
        problems.append("production Scheduler is missing the candidate engine seam (C8.4)")
    required_cases = scheduler_binding(scheduler, "required_cases")
    if not required_cases:
        problems.append("production Scheduler is missing non-empty required_cases (C8.4)")

    assert not problems, "REQ-17/18/C7.1 production wiring gaps:\n  - " + "\n  - ".join(
        problems
    )


def test_acceptance_production_change_detection_quarantine_publish(make_acc) -> None:
    # Scenario 2: band structure change -> semantic_change, pointer unchanged.
    acc_semantic = make_acc("prodsemantic")
    scheduler_semantic = acc_semantic.app.state.update_scheduler
    sources = scheduler_sources(scheduler_semantic)
    current_bundle = acc_semantic.store.get_bundle(
        acc_semantic.store.current()["bundle_id"]
    )
    pointer_before_semantic = acc_semantic.store.current()["bundle_id"]

    from app.updater.classifier import build_candidate

    manifests = [entry.get("manifest") or {} for entry in sources]
    band_change = {
        "fullpath": BAND_STRUCTURE_PATH,
        "value": "1800",
        "source_id": sources[0].get("source_id"),
        "anchor": "",
    }
    built = build_candidate(current_bundle, [band_change], manifests)
    assert built.get("status") == "quarantined", (
        f"REQ-18: band structure change must quarantine, actual {built.get('status')!r}"
    )
    assert built.get("quarantine_reason") == "semantic_change", (
        f"quarantine reason must be semantic_change, actual "
        f"{built.get('quarantine_reason')!r}"
    )
    assert acc_semantic.store.current()["bundle_id"] == pointer_before_semantic

    # Scenario 1: salary cap old path (stop-auto) -> observer pending only.
    acc_observer = make_acc("prodobserver")
    scheduler_observer = acc_observer.app.state.update_scheduler
    sources_observer = scheduler_sources(scheduler_observer)
    bundle_data_o = acc_observer.store.get_bundle(
        acc_observer.store.current()["bundle_id"]
    )["data"]
    pointer_before_o = acc_observer.store.current()["bundle_id"]
    carriers_o = _cap_carrier_ids(sources_observer)
    assert carriers_o, f"production config must register stop-auto observation for {CAP_PATH}"
    inject_scheduler_seams(
        scheduler_observer,
        clock=FakeClock(hk(2026, 10, 9)),
        outbound=FakeOutbound(
            production_round_documents(
                sources_observer,
                bundle_data_o,
                _cap_overrides(sources_observer, CAP_NEW_VALUE),
            )
        ),
    )
    job_o = scheduler_observer.startup()
    if job_o is None or job_o.get("status") != "succeeded":
        outcomes_o = {
            entry.get("source_id"): entry.get("http_outcome")
            for entry in scheduler_observer.status()["sources"]
        }
        raise AssertionError(
            f"scenario 1 precondition failed: round not complete (job={job_o!r}, "
            f"outcomes={outcomes_o!r})"
        )
    status_o = scheduler_observer.status()
    assert status_o.get("publish_outcome") != "published", (
        "REQ-12: six old paths are stop-auto this phase; must not publish, actual "
        f"{status_o.get('publish_outcome')!r}"
    )
    assert acc_observer.store.current()["bundle_id"] == pointer_before_o, (
        "stop-auto observation must not move current pointer"
    )
    pending = observer_pending_rows(acc_observer.store)
    assert pending, (
        "REQ-12: stop-auto change must persist durable observer_pending rows "
        f"(005 15.5), actual {pending!r}"
    )
    for source_id in carriers_o:
        assert any(
            row_source == source_id
            and "reductions.2025_26.cap" in row_state
            and row_period == "2025_26"
            for row_source, row_state, row_period in pending
        ), f"source {source_id!r} must persist {CAP_PATH} pending for 2025_26: {pending!r}"
    availability_o = scheduler_observer.availability("2025_26")
    assert availability_o["mode"] == "blocked", (
        f"observer pending must map to an existing blocking state: {availability_o!r}"
    )
    scheduler_observer.trigger_manual()
    assert observer_pending_rows(acc_observer.store), (
        "observer pending must not be cleared by an ordinary successful round"
    )

    # Scenario 3: unaffected path, only one carrier changes -> evidence missing.
    acc_quarantine = make_acc("prodquarantine")
    scheduler_quarantine = acc_quarantine.app.state.update_scheduler
    production_sources_q = scheduler_sources(scheduler_quarantine)
    assert len(production_sources_q) == 9, (
        "production allowlist must stay at 9 sources (H2-B law PDF)"
    )
    sources_quarantine = production_sources_q + [_testonly_avd_source()]
    bundle_data_q = acc_quarantine.store.get_bundle(
        acc_quarantine.store.current()["bundle_id"]
    )["data"]
    pointer_before_q = acc_quarantine.store.current()["bundle_id"]
    inject_scheduler_seams(
        scheduler_quarantine,
        clock=FakeClock(hk(2026, 10, 9)),
        outbound=FakeOutbound(
            production_round_documents(
                sources_quarantine,
                bundle_data_q,
                {"ird_avd": {AVD_FULLPATH: AVD_CHANGED}},
            )
        ),
        sources=sources_quarantine,
    )
    job_q = scheduler_quarantine.startup()
    if job_q is None or job_q.get("status") != "succeeded":
        outcomes_q = {
            entry.get("source_id"): entry.get("http_outcome")
            for entry in scheduler_quarantine.status()["sources"]
        }
        raise AssertionError(
            f"scenario 3 precondition failed: round not complete (job={job_q!r}, "
            f"outcomes={outcomes_q!r})"
        )
    status_q = scheduler_quarantine.status()
    assert status_q.get("publish_outcome") == "quarantined", (
        "REQ-18: independent evidence cannot verify the candidate value -> "
        f"quarantined, actual {status_q.get('publish_outcome')!r} "
        f"reason={status_q.get('quarantine_reason')!r}"
    )
    assert acc_quarantine.store.current()["bundle_id"] == pointer_before_q

    # Scenario 4: unaffected path, two independent sources agree + B5 case -> publish.
    acc_publish = make_acc("prodpublish")
    scheduler_publish = acc_publish.app.state.update_scheduler
    production_sources = scheduler_sources(scheduler_publish)
    assert len(production_sources) == 9, (
        "production allowlist must stay at 9 sources (H2-B law PDF)"
    )
    sources_publish = production_sources + [_testonly_avd_source()]
    bundle_data = acc_publish.store.get_bundle(
        acc_publish.store.current()["bundle_id"]
    )["data"]
    pointer_before = acc_publish.store.current()["bundle_id"]
    inject_scheduler_seams(
        scheduler_publish,
        clock=FakeClock(hk(2026, 10, 9)),
        outbound=FakeOutbound(
            production_round_documents(
                sources_publish,
                bundle_data,
                {
                    "ird_avd": {AVD_FULLPATH: AVD_CHANGED},
                    "testonly_cap117_avd": {AVD_FULLPATH: AVD_CHANGED},
                },
            )
        ),
        sources=sources_publish,
        harness=b5_harness(),
        engine=b5_engine(),
        required_cases=(B5_REQUIRED_CASE,),
    )
    job = scheduler_publish.startup()
    if job is None or job.get("status") != "succeeded":
        outcomes = {
            entry.get("source_id"): entry.get("http_outcome")
            for entry in scheduler_publish.status()["sources"]
        }
        raise AssertionError(
            f"scenario 4 precondition failed: round not complete (job={job!r}, "
            f"outcomes={outcomes!r})"
        )
    status_publish = scheduler_publish.status()
    assert status_publish.get("publish_outcome") == "published", (
        "REQ-18: unaffected path with second independent T1 source + B5 required "
        f"case must publish (D1=a); actual {status_publish.get('publish_outcome')!r} "
        f"quarantine={status_publish.get('quarantine_reason')!r}"
    )
    published_bundle_id = status_publish.get("published_bundle_id")
    assert published_bundle_id
    pointer_after = acc_publish.store.current()["bundle_id"]
    assert pointer_after == published_bundle_id and pointer_after != pointer_before, (
        f"publish must atomically switch pointer: {pointer_before!r} -> {pointer_after!r}"
    )
    published_bundle = acc_publish.store.get_bundle(published_bundle_id)
    assert resolve_bundle_path(published_bundle["data"], AVD_FULLPATH) == AVD_CHANGED
    conn = acc_publish.store._connect()
    try:
        publish_details = [
            str(row[0])
            for row in conn.execute(
                "SELECT detail_json FROM audit_log WHERE event_type = 'publish'"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert any(
        '"purpose": "update"' in detail
        and f'"baseline_bundle_id": "{pointer_before}"' in detail
        for detail in publish_details
    ), f"publish audit must bind purpose=update + baseline bundle id: {publish_details!r}"
