"""Local engine counterpart: run app.engines on the same inputs (task 002 item 4).

Facts shapes follow tests/engines/ exactly:
  - salaries:   tests/engines/test_salaries_engine.py  (_single helper)
  - AVD:        tests/engines/stamps/test_property_avd.py  (_facts helper)
  - lease:      tests/engines/stamps/test_lease.py  (_lease helper)
  - stock:      tests/engines/stamps/test_stock.py  (_facts helper)

Read-only usage of app/ - nothing there is modified.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.engines.salaries import calculate_salaries_tax  # noqa: E402
from app.engines.stamps.lease import calculate_lease_stamp_duty  # noqa: E402
from app.engines.stamps.property import calculate_property_avd  # noqa: E402
from app.engines.stamps.stock import calculate_stock_stamp_duty  # noqa: E402
from app.rules.bundle import INITIAL_BUNDLE_CONTENT  # noqa: E402

BUNDLE = INITIAL_BUNDLE_CONTENT


@dataclass
class EngineOutcome:
    case: str
    status: str
    value: str | None  # the comparable final amount (canonical digits)
    before_reduction: str | None
    reduction: str | None
    detail: str
    next_provisional: str | None = None  # salaries only: next-year provisional estimate


def _amounts(result: dict) -> dict:
    return result.get("amounts") or {}


def run_salaries(year: str, income: str, mpf: str) -> EngineOutcome:
    facts = {
        "year_of_assessment": year,
        "employment_income": income,
        "mpf_mandatory_contributions": mpf,
        "married_status": {"value": "single"},
    }
    r = calculate_salaries_tax(facts, BUNDLE)
    a = _amounts(r)
    return EngineOutcome(
        case=f"salaries_{year}",
        status=r.get("status", "?"),
        value=a.get("final_after_reduction"),
        before_reduction=a.get("before_reduction"),
        reduction=a.get("reduction"),
        detail=f"amounts={a}",
        next_provisional=a.get("next_provisional"),
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
