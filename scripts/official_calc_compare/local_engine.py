"""Local engine counterpart: run app.engines on the same inputs (task 002 item 4).

Facts shapes follow tests/engines/ v2 envelopes:
  - salaries:   tests/engines/test_salaries_engine.py  (_single/_availability)
  - AVD:        tests/engines/stamps/test_property_avd.py  (_facts helper)
  - lease:      tests/engines/stamps/test_lease.py  (_lease helper)
  - stock:      tests/engines/stamps/test_stock.py  (_facts helper)

Every outcome carries:
  - explicit AvailabilityContext for the two v2 engines (salaries/PA);
  - a fixed-release-bundle provenance sidecar (bundle id/hash/schema/engine);
  - `simulation=True` because these are fixed-bundle checks, not live rule
    availability evidence.

Read-only usage of app/ - nothing there is modified.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import ENGINE_VERSION  # noqa: E402
from app.core.hashing import bundle_content_hash  # noqa: E402
from app.engines.salaries import calculate_salaries_tax  # noqa: E402
from app.engines.stamps.lease import calculate_lease_stamp_duty  # noqa: E402
from app.engines.stamps.property import calculate_property_avd  # noqa: E402
from app.engines.stamps.stock import calculate_stock_stamp_duty  # noqa: E402
from app.rules.bundle import INITIAL_BUNDLE_CONTENT  # noqa: E402

BUNDLE = INITIAL_BUNDLE_CONTENT
BUNDLE_ID = "release-fixed-002"
BUNDLE_HASH = bundle_content_hash(BUNDLE)
SIMULATION_NOTE = "固定束核验/模拟，不代表实时规则可用性"


@dataclass
class EngineOutcome:
    case: str
    status: str
    value: str | None  # the comparable final amount (canonical digits)
    before_reduction: str | None
    reduction: str | None
    detail: str
    next_provisional: str | None = None  # salaries only: next-year provisional estimate
    context: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)


def _amounts(result: dict) -> dict:
    return result.get("amounts") or {}


def _base_provenance(tax_type: str) -> dict:
    return {
        "kind": "fixed_bundle_simulation",
        "bundle_id": BUNDLE_ID,
        "bundle_hash": BUNDLE_HASH,
        "rules_schema_version": BUNDLE["rules_schema_version"],
        "engine_version": ENGINE_VERSION,
        "tax_type": tax_type,
        "simulation": True,
        "note": SIMULATION_NOTE,
    }


def _availability(year: str) -> dict:
    """Explicit AvailabilityContext bound to the fixed release bundle (005 §2.1)."""
    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    available_years = BUNDLE["data"]["allowances"]
    return {
        "binding": {
            "bundle_id": BUNDLE_ID,
            "bundle_hash": BUNDLE_HASH,
            "rules_schema_version": BUNDLE["rules_schema_version"],
            "engine_version": ENGINE_VERSION,
        },
        "current": {"year": year, "status": "available", "reason_codes": []},
        "next": {
            "year": following,
            "status": "available" if following in available_years else "missing_rules",
            "reason_codes": [],
        },
    }


def _salary_year(year: str, income: str, mpf: str) -> dict:
    start = int(year[:4])
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {},
        "household": {
            "marriage": [
                {"from": f"{start}-04-01", "to": f"{start + 1}-04-01", "value": "unmarried"}
            ],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "financials": {
            "p1": {
                "employment_income_nai": income,
                "mpf_mandatory_contributions": mpf,
                "has_assessable_salary_income": True,
                "source_classification": {"salary": "confirmed_in_scope"},
                "properties": [],
                "businesses": [],
                "deduction_items": [],
            }
        },
        "care_payments": [],
        "claims": [],
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
        "provisional_paid": "0",
    }


def _salary_input(year: str, income: str, mpf: str) -> dict:
    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    years = {year: _salary_year(year, income, mpf)}
    # The comparator's fixed case is "same income/MPF in the following year";
    # v2 requires the next-year facts to be explicitly supplied (005 §11).
    if following in BUNDLE["data"]["allowances"]:
        years[following] = _salary_year(following, income, mpf)
    return {
        "year_of_assessment": year,
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def run_salaries(year: str, income: str, mpf: str) -> EngineOutcome:
    facts = _salary_input(year, income, mpf)
    availability = _availability(year)
    r = calculate_salaries_tax(facts, BUNDLE, availability=availability)
    a = _amounts(r)
    return EngineOutcome(
        case=f"salaries_{year}",
        status=r.get("status", "?"),
        value=a.get("final_after_reduction"),
        before_reduction=a.get("before_reduction"),
        reduction=a.get("reduction"),
        detail=f"amounts={a}",
        next_provisional=a.get("next_provisional"),
        context={"availability": availability},
        provenance=_base_provenance("salaries_tax"),
    )


def run_avd(case: str, date: str, amount: str) -> EngineOutcome:
    facts = {
        "instrument_date": date,
        "instrument_kind": "agreement_for_sale",
        "consideration": amount,
        "value": amount,
        "property_class": "residential",
    }
    r = calculate_property_avd(facts, BUNDLE)
    a = _amounts(r)
    detail = r.get("instrument_detail") or {}
    return EngineOutcome(
        case=case,
        status=r.get("status", "?"),
        value=a.get("final_after_reduction"),
        before_reduction=a.get("before_reduction"),
        reduction=a.get("reduction"),
        detail=f"instrument_detail={detail}",
        provenance=_base_provenance("stamp_property"),
    )


def run_lease() -> EngineOutcome:
    facts = {
        "instrument_date": "2025-06-30",
        "term_kind": "fixed",
        "term_start": "2025-07-01",
        "term_end": "2025-12-31",
        "rent_input_mode": "total_rent",
        "total_rent": "60000",
        "premium": "0",
    }
    r = calculate_lease_stamp_duty(facts, BUNDLE)
    a = _amounts(r)
    return EngineOutcome(
        case="lease_6months",
        status=r.get("status", "?"),
        value=a.get("final_after_reduction"),
        before_reduction=a.get("before_reduction"),
        reduction=a.get("reduction"),
        detail=f"duty_breakdown={r.get('duty_breakdown')}",
        provenance=_base_provenance("stamp_lease"),
    )


def run_stock(case: str, documents: list[dict], instrument_date: str = "2026-10-13") -> EngineOutcome:
    """Stock transfer stamp duty; facts shape per tests/engines/stamps/test_stock.py."""
    facts = {
        "instrument_date": instrument_date,
        "complete": {"value": True},
        "documents": documents,
    }
    r = calculate_stock_stamp_duty(facts, BUNDLE)
    a = _amounts(r)
    return EngineOutcome(
        case=case,
        status=r.get("status", "?"),
        value=a.get("final_after_reduction"),
        before_reduction=a.get("before_reduction"),
        reduction=a.get("reduction"),
        detail=f"documents={r.get('documents')}",
        provenance=_base_provenance("stamp_stock"),
    )


def run_all_cases() -> dict[str, EngineOutcome]:
    out: dict[str, EngineOutcome] = {}
    for year in ("2024_25", "2025_26", "2026_27"):
        r = run_salaries(year, "240000", "9000")
        out[r.case] = r
    out["avd_case1_20260601"] = run_avd("avd_case1_20260601", "2026-06-01", "100000000")
    out["avd_case2_20250601"] = run_avd("avd_case2_20250601", "2025-06-01", "4323780")
    out["lease_6months"] = run_lease()
    out["stock_sale_notes_1000_01"] = run_stock(
        "stock_sale_notes_1000_01",
        [
            {"doc_kind": "contract_note_sold", "consideration": "1000.01"},
            {"doc_kind": "contract_note_bought", "consideration": "1000.01"},
        ],
    )
    out["stock_other_transfer_doc"] = run_stock(
        "stock_other_transfer_doc",
        [{"doc_kind": "other_transfer"}],
    )
    out["stock_voluntary_1000_01"] = run_stock(
        "stock_voluntary_1000_01",
        [{"doc_kind": "voluntary_inter_vivos", "value": "1000.01"}],
    )
    return out
