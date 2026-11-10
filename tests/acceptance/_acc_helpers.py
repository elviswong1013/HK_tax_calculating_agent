"""Acceptance shared helpers (P3-W1a Red; ASCII-safe rebuild).

Contract:
- Loopback CSRF-aware API wrapper (same shape as tests/conftest.Api).
- In-process app factory handle (app.state.store / app.state.update_scheduler).
- Minimal legal facts for the six approved tax types.
- prepare -> confirm -> execute driver.
- Updater fakes + budget manifest/document universe.
- Publish/gate local universe (report/independent fact/strict outbound/candidate).
- P3-W1a additions: stop-auto observer descriptors, unaffected AVD path,
  test-only second independent source, B5 required case, update report binding.
No amount expectations.
"""

from __future__ import annotations

import copy
import hashlib
import html as _html
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

_html_escape = _html.escape

BASE_ORIGIN = "http://127.0.0.1:8000"
HK = timezone(timedelta(hours=8))


def hk(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=HK)


class AccApi:
    """CSRF-aware loopback API wrapper."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._csrf_token: str | None = None

    def _token(self) -> str:
        if self._csrf_token is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "safe GET missing X-CSRF-Token (C5.2/C5.3)"
            self._csrf_token = token
        return self._csrf_token

    def get(self, path: str, *, headers: dict | None = None):
        return self._client.get(path, headers=headers)

    def post(self, path: str, *, json: object | None = None, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json, headers=h)


class AccHarness:
    def __init__(self, app, api: AccApi) -> None:
        self.app = app
        self.api = api

    @property
    def store(self):
        return self.app.state.store

    def close(self) -> None:
        self.api._client.close()


def build_app(setenv, db_path) -> AccHarness:
    setenv("HKTAX_DB_PATH", str(db_path))
    from app.main import create_app

    app = create_app()
    client = httpx.Client(
        transport=httpx.ASGITransport(app=app), base_url=BASE_ORIGIN
    )
    return AccHarness(app, AccApi(client))


TAX_EXECUTE_TARGETS: dict[str, str] = {
    "salaries_tax": "/api/v1/calc/salaries-tax",
    "profits_tax": "/api/v1/calc/profits-tax",
    "property_tax": "/api/v1/calc/property-tax",
    "personal_assessment": "/api/v1/personal-assessment/compare",
    "stamp_property": "/api/v1/calc/stamp-duty/property",
    "stamp_stock": "/api/v1/calc/stamp-duty/stock",
    "stamp_lease": "/api/v1/calc/stamp-duty/lease",
}


def _paid_rates_zero() -> dict:
    return {
        "amount": "0",
        "agreed": {"value": True},
        "actually_paid": {"value": True},
    }


def salaries_min_facts(*, year: str = "2025_26", with_provisional_zero: bool = False) -> dict:
    facts: dict = {
        "year_of_assessment": year,
        "employment_income": "240000",
        "mpf_mandatory_contributions": "9000",
        "married_status": {"value": "single"},
    }
    if with_provisional_zero:
        facts["provisional_paid"] = "0"
    return facts


def profits_min_facts() -> dict:
    return {
        "year_of_assessment": "2025_26",
        "entity_kind": "sole_proprietorship",
        "assessable_profit": "600000",
        "two_tier": {
            "connected_entities": [],
            "election_made": {"value": True},
            "no_other_election_same_year": {"value": True},
        },
    }


def property_min_facts() -> dict:
    return {
        "year_of_assessment": "2025_26",
        "properties": [
            {
                "property_id": "p-1",
                "rent_received": "120000",
                "rates_paid_by_owner": _paid_rates_zero(),
                "irrecoverable_rent": "0",
                "deposit_offsets": "0",
            }
        ],
    }


def personal_assessment_min_facts() -> dict:
    return {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": {
            "2025_26": {
                "participants": [{"person_id": "p1", "role": "self"}],
                "subject_facts": {
                    "p1": {
                        "ordinarily_resident_hk": [
                            {"from": "2026-06-01", "to": "2026-06-02", "value": True}
                        ]
                    }
                },
                "household": {
                    "marriage": [
                        {"from": "2025-04-01", "to": "2026-04-01", "value": "unmarried"},
                        {"from": "2026-06-01", "to": "2026-06-02", "value": "unmarried"},
                    ],
                    "living_apart": [],
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                "relations": [],
                "financials": {
                    "p1": {
                        "employment_income_nai": "250000",
                        "mpf_mandatory_contributions": "0",
                        "has_assessable_salary_income": True,
                        "has_assessable_property_income": True,
                        "has_assessable_business_income": False,
                        "has_income_assessable_under_ordinance": True,
                        "source_classification": {
                            "salary": "confirmed_in_scope",
                            "property": "confirmed_in_scope",
                            "business": "confirmed_in_scope",
                        },
                        "properties": [
                            {
                                "property_id": "h1",
                                "share": "1/1",
                                "rent_received": "120000",
                                "rates_paid_by_owner": {
                                    "amount": "0",
                                    "agreed": False,
                                    "actually_paid": False,
                                },
                                "irrecoverable_rent": "0",
                                "deposit_offsets": "0",
                                "mortgage_interest": "0",
                            }
                        ],
                        "businesses": [],
                        "deduction_items": [],
                    }
                },
                "care_payments": [],
                "claims": [],
                "assessment": {
                    "reference_date": "2026-06-01",
                    "basis": "hypothetical",
                    "actual": {
                        "salary_joint_elected": False,
                        "salary_joint_assessed": False,
                        "pa": {"p1": {"mode": "none"}},
                        "history": "ordinary",
                    },
                    "candidates": ["no_pa", "pa_self"],
                    "selection": "best",
                },
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }


def stamp_property_min_facts() -> dict:
    return {
        "instrument_date": "2025-06-20",
        "instrument_kind": "agreement_for_sale",
        "property_class": "residential",
        "consideration": "6000000",
        "value": "6000000",
    }


def stamp_stock_min_facts() -> dict:
    return {
        "instrument_date": "2025-06-10",
        "complete": {"value": True},
        "documents": [
            {
                "doc_kind": "contract_note_sold",
                "consideration": "50000",
                "value": "50000",
            }
        ],
    }


def stamp_lease_min_facts() -> dict:
    return {
        "instrument_date": "2025-06-20",
        "term_kind": "fixed",
        "term_start": "2025-07-01",
        "term_end": "2027-06-30",
        "rent_input_mode": "fixed_monthly",
        "monthly_rent": "10000",
        "premium": "0",
    }


def minimal_facts(tax_type: str) -> dict:
    builders = {
        "salaries_tax": lambda: salaries_min_facts(with_provisional_zero=True),
        "profits_tax": profits_min_facts,
        "property_tax": property_min_facts,
        "personal_assessment": personal_assessment_min_facts,
        "stamp_property": stamp_property_min_facts,
        "stamp_stock": stamp_stock_min_facts,
        "stamp_lease": stamp_lease_min_facts,
    }
    return builders[tax_type]()


PREPARE_PATH = "/api/v1/calc/prepare"
CONFIRM_PATH = "/api/v1/calc/confirm"


def fresh_if_match(api) -> dict[str, str]:
    r = api.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, (
        f"S002 4.1: meta/version missing session_epoch/input_revision: {payload!r}"
    )
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def _tf(year: str, value: object) -> list[dict]:
    start = int(year[:4])
    return [{"from": f"{start}-04-01", "to": f"{start + 1}-04-01", "value": value}]


def _v2_year_block(year: str, finance: dict, marriage_value: str = "unmarried") -> dict:
    start = int(year[:4])
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {},
        "household": {
            "marriage": [
                {
                    "from": f"{start}-04-01",
                    "to": f"{start + 1}-04-01",
                    "value": marriage_value,
                }
            ],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "care_payments": [],
        "claims": [],
        "financials": {"p1": copy.deepcopy(finance)},
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
    }


def _salary_v2_input(facts: dict) -> dict:
    year = facts["year_of_assessment"]
    start = int(year[:4])
    finance = {
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
    }
    for key in ("employment_income", "mpf_mandatory_contributions"):
        if key in facts:
            target = "employment_income_nai" if key == "employment_income" else key
            finance[target] = facts[key]
    if "deduction_items" in facts:
        finance["deduction_items"] = copy.deepcopy(facts["deduction_items"])
    status = facts["married_status"]["value"]
    marriage_value = "unmarried" if status == "single" else status
    year_block = _v2_year_block(year, finance, marriage_value)
    subjects = [{"subject_id": "p1", "birth_date": "1990-01-01"}]
    claim_seq = 0
    for index, parent in enumerate(facts.get("dependent_parents") or [], 1):
        pid = f"d{index}"
        subjects.append({"subject_id": pid, "birth_date": parent.get("birth_date")})
        subject_facts: dict = {"govt_disability_allowance": _tf(year, False)}
        residence = (parent.get("residence") or {}).get("value")
        if residence is not None:
            subject_facts["ordinarily_resident_hk"] = _tf(year, residence)
        year_block["subject_facts"][pid] = subject_facts
        relation = {
            "relation_id": f"r{index}",
            "actor_id": "p1",
            "subject_id": pid,
            "kinship": {"kind": "parent", "basis": "birth", "during": _tf(year, True)},
        }
        maintained_present = "maintained" in parent
        maintained = (parent.get("maintained") or {}).get("value")
        if maintained_present:
            relation["cash_maintenance"] = "15000" if maintained is True else "0"
        together_present = "living_together_all_year" in parent
        together = (parent.get("living_together_all_year") or {}).get("value")
        if maintained_present and together_present:
            relation["cohabit_non_full_consideration"] = _tf(year, together)
        year_block["relations"].append(relation)
        claim_seq += 1
        year_block["claims"].append(
            {
                "claim_id": f"c{claim_seq}",
                "kind": "parent",
                "claimant_id": "p1",
                "subject_id": pid,
            }
        )
        if together_present:
            claim_seq += 1
            year_block["claims"].append(
                {
                    "claim_id": f"c{claim_seq}",
                    "kind": "parent_cohabitation_additional",
                    "claimant_id": "p1",
                    "subject_id": pid,
                }
            )
    years = {year: year_block}
    if "provisional_paid" in facts:
        year_block["provisional_paid"] = facts["provisional_paid"]
        following = f"{start + 1}_{(start + 2) % 100:02d}"
        if following in ("2024_25", "2025_26", "2026_27"):
            years[following] = _v2_year_block(following, finance, marriage_value)
    return {
        "year_of_assessment": year,
        "subjects": subjects,
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


def prepare(api, tax_type: str, facts: dict):
    if tax_type == "salaries_tax" and "years" not in facts:
        input_facts = _salary_v2_input(facts)
        schema_version = "2.0.0"
    elif tax_type in ("salaries_tax", "personal_assessment"):
        input_facts = facts
        schema_version = "2.0.0"
    else:
        input_facts = facts
        schema_version = "1.0.0"
    return api.post(
        PREPARE_PATH,
        json={
            "tax_type": tax_type,
            "schema_version": schema_version,
            "input": input_facts,
        },
        headers=fresh_if_match(api),
    )


def confirm(api, prepared: dict):
    return api.post(
        CONFIRM_PATH,
        json={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )


def execute(api, confirmation_id: str, target: str):
    return api.post(target, json={"confirmation_id": confirmation_id})


def error_code(response) -> str | None:
    try:
        err = response.json().get("error")
    except Exception:
        return None
    return err.get("code") if isinstance(err, dict) else None


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(HK)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment.astimezone(HK)

    def advance(self, *, days: int = 0, minutes: int = 0) -> None:
        self._now = self._now + timedelta(days=days, minutes=minutes)


class FakeOutbound:
    def __init__(self, documents: dict | None = None) -> None:
        self.documents: dict = dict(documents or {})
        self.requests: list[dict] = []
        self.unreachable = False

    def fetch(self, url: str, **kwargs) -> bytes:
        self.requests.append({"url": url, "kwargs": kwargs})
        if self.unreachable or url not in self.documents:
            raise ConnectionError(f"unreachable (fake outbound): {url}")
        return self.documents[url]


class StrictOutbound(FakeOutbound):
    def fetch(self, url: str, **kwargs) -> bytes:
        self.requests.append({"url": url, "kwargs": kwargs})
        raise AssertionError(f"gate environment must not access network (C8.7): {url}")


class FakeHarness:
    def __init__(self, results: dict[str, dict]) -> None:
        self.results = dict(results)
        self.calls: list[str] = []

    def run_case(self, case_id: str, **kwargs) -> dict:
        self.calls.append(case_id)
        if case_id not in self.results:
            raise KeyError(f"unknown required case: {case_id}")
        return self.results[case_id]


class FakeEngine:
    def __init__(self, results: dict[str, dict]) -> None:
        self.results = dict(results)
        self.calls: list[str] = []

    def run_case(self, case_id: str, candidate_content: dict, **kwargs) -> dict:
        self.calls.append(case_id)
        if case_id not in self.results:
            raise KeyError(f"unknown required case: {case_id}")
        return self.results[case_id]


BUDGET_URL = "https://www.ird.gov.hk/eng/tax/budget.htm"

BUDGET_MANIFEST: dict = {
    "manifest_schema_version": "1",
    "source_id": "ird_budget",
    "url": BUDGET_URL,
    "artifact_type": "html_table",
    "parser_id": "ird_budget_table_v1",
    "parser_code_hash": "sha256:" + "ab" * 32,
    "anchors": [
        {"path_regex": "heading:Salaries Tax Reduction", "anchor_status": "confirmed"},
        {
            "path_regex": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
            "anchor_status": "confirmed",
        },
        {"path_regex": "heading:Legislative Update", "anchor_status": "confirmed"},
    ],
    "fields": ["reduction_cap_2025_26"],
    "slots": [
        {
            "profile_ID": "salaries_reduction_v1",
            "fullpath": "salaries.reductions.2025_26.cap",
            "type": "decimal_string",
            "unit": "HKD",
            "constraints": {"min": "0", "max": "1000000", "step": "1"},
            "anchor": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
            "proof_role": "primary_official_page",
        }
    ],
    "evidence_only_fields": ["reduction_subject_scope", "provisional_boundary"],
    "legal_state_rules": [
        {
            "anchor": "heading:Legislative Update",
            "match": "will be confirmed",
            "state": "related_change_uncertain",
            "period": "2026_27",
        },
        {
            "anchor": "heading:Legislative Update",
            "match": "proposes",
            "state": "proposal_not_current",
            "period": "2026_27",
        },
        {
            "anchor": "heading:Legislative Update",
            "match": "repealed",
            "state": "expired",
            "period": "2024_25",
        },
    ],
    "proof_authority": "ird.gov.hk official page",
    "independent_counterpart": "iro_sch43 schedule text",
}

BUDGET_DOC_OK = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3000</td></tr>"
    b"</table>"
    b"</body></html>"
)

BUDGET_DOC_EXPIRED_NOTICE = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3000</td></tr>"
    b"</table>"
    b"<h2>Legislative Update</h2>"
    b"<p>The reduction is repealed with effect from 2026-04-01.</p>"
    b"</body></html>"
)


def budget_source() -> dict:
    return {
        "source_id": "ird_budget",
        "url": BUDGET_URL,
        "manifest": copy.deepcopy(BUDGET_MANIFEST),
    }


def make_scheduler(store, *, clock, outbound, sources=None):
    from app.updater.scheduler import Scheduler

    return Scheduler(
        store,
        clock=clock,
        outbound=outbound,
        sources=[budget_source()] if sources is None else sources,
    )


REPORT_PUBLISH_OK = {
    "decision": "publish",
    "tests": {"passed": 3, "failed": 0, "failed_ids": []},
    "quarantine_reason": None,
}

GATE_CASE = "case_salaries_reduction_cap_2025_26"

FACT_CAP_3500 = {
    "fact_id": "fact_sch43_cap_3500",
    "fullpath": "salaries.reductions.2025_26.cap",
    "value": "3500",
    "source_id": "iro_sch43",
    "url": "https://www.elegislation.gov.hk/hk/cap112!schedule43",
    "anchor": "heading:Schedule 43 - Salaries Tax Reduction#row:2025/26#col:Cap",
    "tier": "T1",
}

AVD_FULLPATH = "stamps.property.related_conveyance.fixed_duty"
AVD_BASELINE = "100"
AVD_CHANGED = "110"
AVD_SECOND_URL = "https://www.elegislation.gov.hk/hk/cap117!s29D"
AVD_SECOND_SOURCE_ID = "testonly_cap117_avd"
# 生产 B5_CASES 的 AVD case id（测试仅注入 required_cases，不扩生产集合）。
B5_REQUIRED_CASE = "a_avd_related_fixed"
# 与 Scheduler._classify_round 的 fact_id 形式一致，使门禁按精确 fact_id 绑定
# 独立第二来源证据（不依赖 required-map 同值 fallback）。
B5_PROOF_FACT_ID = f"fact:{AVD_SECOND_SOURCE_ID}:{AVD_FULLPATH}"

FACT_AVD_110 = {
    "fact_id": "fact_002_cap117_avd_110",
    "fullpath": AVD_FULLPATH,
    "value": AVD_CHANGED,
    "source_id": AVD_SECOND_SOURCE_ID,
    "url": AVD_SECOND_URL,
    "anchor": "heading:Cap 117 s.29D - Related Conveyance#row:Fixed duty#col:Amount",
    "tier": "T1",
}

STOP_AUTO_TARGETS: dict[str, str] = {
    "salaries.allowances.2025_26.basic": "allowances.2025_26.basic.amount",
    "salaries.allowances.2025_26.married": "allowances.2025_26.married.amount",
    "salaries.allowances.2026_27.basic": "allowances.2026_27.basic.amount",
    "salaries.mpf_deduction_cap": "allowances.mpf_deduction_cap",
    "salaries.reductions.2024_25.cap": "reductions.2024_25.cap",
    "salaries.reductions.2025_26.cap": "reductions.2025_26.cap",
}


def _testonly_avd_source() -> dict:
    slot = {
        "profile_ID": "avd_related_conveyance_v1",
        "fullpath": AVD_FULLPATH,
        "type": "decimal_string",
        "unit": "HKD",
        "constraints": {"min": "0", "max": "10000", "step": "1"},
        "anchor": "heading:Cap 117 s.29D - Related Conveyance#row:Fixed duty#col:Amount",
        "proof_role": "independent_counterpart",
    }
    return {
        "source_id": AVD_SECOND_SOURCE_ID,
        "url": AVD_SECOND_URL,
        "manifest": {
            "manifest_schema_version": "1",
            "source_id": AVD_SECOND_SOURCE_ID,
            "url": AVD_SECOND_URL,
            "artifact_type": "html_table",
            "parser_id": "testonly_cap117_table_v1",
            "parser_code_hash": "sha256:" + "ee" * 32,
            "anchors": [
                {
                    "path_regex": "heading:Cap 117 s.29D - Related Conveyance",
                    "anchor_status": "confirmed",
                },
                {"path_regex": slot["anchor"], "anchor_status": "confirmed"},
            ],
            "fields": [AVD_FULLPATH],
            "slots": [slot],
            "evidence_only_fields": [],
            "legal_state_rules": [],
            "proof_authority": "elegislation.gov.hk Cap.117 s.29D (test-only)",
            "independent_counterpart": "ird_avd official FAQ",
        },
    }


def b5_proof_fact() -> dict:
    """独立第二来源的 B5 参数证据（value 来自 FACT_AVD_110，不硬编码 110）。"""
    fact = copy.deepcopy(FACT_AVD_110)
    fact["fact_id"] = B5_PROOF_FACT_ID
    return fact


def b5_harness():
    """生产 B5 reference harness：proof-controlled 期望（不返回常量 fake）。

    proof_facts 绑定独立第二来源事实；harness 必须走生产 B5_CASES 执行分支，
    从 proof 值构造期望，而不是把 110 写死在测试 helper。
    """
    from app.updater.reference import ReferenceHarness

    return ReferenceHarness(proof_facts=[b5_proof_fact()])


def b5_engine():
    """生产候选引擎：必须执行真实 B5_CASES 分支并读取原样 candidate data。"""
    from app.updater.reference import CandidateEngine

    return CandidateEngine()


def _canonical_sha(payload) -> str:
    from app.core.hashing import canonical_json_bytes

    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _reference_code_sha256() -> str:
    """运行时读取当前 reference.py 文件字节（不得硬编码/不得 canonical label）。"""
    from app.updater import reference

    return "sha256:" + hashlib.sha256(
        Path(reference.__file__).read_bytes()
    ).hexdigest()


def _profile_bindings(content: dict) -> list[dict]:
    """Profile rows; legacy profiles use canonical-JSON SHA-256 (shared P keeps
    its stored semantic_digest)."""
    rows: list[dict] = []
    for profile in content["frozen_semantics"]["profiles"]:
        digest = profile.get("semantic_digest") or _canonical_sha(profile)
        rows.append(
            {
                "profile_id": str(profile["profile_id"]),
                "semantic_digest": str(digest),
            }
        )
    return rows


def _fixture_plan_subcases(plan: dict) -> tuple[list[dict], list[dict]]:
    """Plan-aligned expected/actual subcase payloads for synthetic reports."""
    expected: list[dict] = []
    actual: list[dict] = []
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
                    "engine": "test:fake-b5",
                    "input_sha256": _canonical_sha(dict(subcase["input"])),
                },
            }
        )
    return expected, actual


def _subcase_proof_fields(
    expected_entries,
    actual_entries,
    subcase_ids,
) -> dict:
    """Canonical subcase proof fields (same shape as _repair1_helpers)."""
    expected = [
        {
            "subcase_id": str(entry["subcase_id"]),
            "input": copy.deepcopy(dict(entry["input"])),
            "expected": copy.deepcopy(dict(entry["expected"])),
        }
        for entry in expected_entries
    ]
    actual = [
        {
            "subcase_id": str(entry["subcase_id"]),
            "input": copy.deepcopy(dict(entry["input"])),
            "expected": copy.deepcopy(dict(entry["expected"])),
            "actual": copy.deepcopy(dict(entry["actual"])),
            "passed": bool(entry["passed"]),
            "receipt": copy.deepcopy(dict(entry["receipt"])),
        }
        for entry in actual_entries
    ]
    ids = [str(value) for value in subcase_ids]
    expected_sha = _canonical_sha(expected)
    actual_sha = _canonical_sha(actual)
    combined = _canonical_sha(
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


def _fake_b5_subcase_entries(
    plan_case_id: str,
    value,
    *,
    actual: bool,
    evidence_ref: str | None = None,
) -> list[dict]:
    from app.updater.reference import B5_CASES

    plan = B5_CASES[plan_case_id]
    rendered = str(value)
    entries: list[dict] = []
    for subcase in plan.get("subcases") or []:
        expected = copy.deepcopy(dict(subcase["expected"]))
        if "fixed_duty" in expected:
            expected["fixed_duty"] = rendered
        entry = {
            "subcase_id": str(subcase["subcase_id"]),
            "input": copy.deepcopy(dict(subcase["input"])),
            "expected": expected,
        }
        if actual:
            entry.update(
                {
                    "actual": copy.deepcopy(expected),
                    "passed": True,
                    "receipt": {
                        "subcase_id": str(subcase["subcase_id"]),
                        "engine": "test:fake-b5",
                        "input_sha256": _canonical_sha(dict(subcase["input"])),
                    },
                }
            )
        elif evidence_ref is not None:
            entry["parameter_evidence_ref"] = evidence_ref
        entries.append(entry)
    return entries


def fake_b5_harness_result(
    case_id: str,
    value=AVD_CHANGED,
    *,
    plan_case_id: str = B5_REQUIRED_CASE,
    evidence_ref: str | None = FACT_AVD_110["fact_id"],
) -> dict:
    entries = _fake_b5_subcase_entries(
        plan_case_id,
        value,
        actual=False,
        evidence_ref=evidence_ref,
    )
    result = {
        "case_id": case_id,
        "subcase_ids": [entry["subcase_id"] for entry in entries],
        "stages": [str(value)],
        "output": str(value),
        "subcase_results": entries,
    }
    if evidence_ref is not None:
        result["parameter_evidence_ref"] = evidence_ref
    return result


def fake_b5_engine_result(
    case_id: str,
    value=AVD_CHANGED,
    *,
    plan_case_id: str = B5_REQUIRED_CASE,
) -> dict:
    entries = _fake_b5_subcase_entries(
        plan_case_id,
        value,
        actual=True,
    )
    return {
        "case_id": case_id,
        "subcase_ids": [entry["subcase_id"] for entry in entries],
        "stages": [str(value)],
        "output": str(value),
        "subcase_results": entries,
    }


def update_report(
    store,
    *,
    content=None,
    manifests=(),
    evidence_refs=(),
    required_cases=(),
    decision: str = "publish",
    passed: int | None = None,
    failed: int = 0,
    failed_ids=(),
    changed_paths=None,
) -> dict:
    """Complete update/seed gate report fixture (005 §15.4 binding/coverage/proof)."""
    from app.config import ENGINE_VERSION
    from app.core.hashing import bundle_content_hash
    from app.updater.reference import B5_CASES, REQUIRED_CASES

    baseline = store.current()
    candidate = (
        content if content is not None else store.get_bundle(baseline["bundle_id"])
    )
    candidate_hash = bundle_content_hash(candidate)
    manifest_rows = [dict(manifest) for manifest in manifests]
    evidence_rows = [dict(fact) for fact in evidence_refs]
    required = [str(case_id) for case_id in required_cases]
    changed = [str(path) for path in (changed_paths or [])]
    static_cases = list(REQUIRED_CASES)
    case_rows: list[dict] = []
    for case_id in required:
        plan = B5_CASES.get(case_id) or {}
        subcase_ids = [str(value) for value in plan.get("subcase_ids") or []]
        refs = [
            str(fact.get("fact_id"))
            for fact in evidence_rows
            if fact.get("fullpath") == plan.get("path")
        ]
        expected_subcases, actual_subcases = _fixture_plan_subcases(plan)
        row = {
            "case_id": case_id,
            "mode": "candidate",
            "input_proof_sha256": _canonical_sha(
                {"case_id": case_id, "subcase_ids": subcase_ids}
            ),
            "subcase_ids": subcase_ids,
            "worksheet_sha256": _canonical_sha(
                {"case_id": case_id, "path": plan.get("path")}
            ),
            "parameter_evidence_refs": refs,
            "stage_match": True,
            "passed": True,
        }
        row.update(
            _subcase_proof_fields(
                expected_subcases,
                actual_subcases,
                subcase_ids,
            )
        )
        case_rows.append(row)
    if passed is None:
        passed = max(1, len(required))
    return {
        "decision": decision,
        "purpose": "update",
        "binding": {
            "baseline_bundle_id": baseline["bundle_id"],
            "baseline_bundle_hash": baseline["bundle_hash"],
            "baseline_rules_schema_version": baseline["rules_schema_version"],
            "candidate_bundle_hash": candidate_hash,
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": baseline["rules_schema_version"],
            "profiles": _profile_bindings(candidate),
            "manifests_sha256": _canonical_sha(manifest_rows),
            "evidence_sha256": _canonical_sha(evidence_rows),
            "reference_code_sha256": _reference_code_sha256(),
            "required_case_ids": list(required),
        },
        "coverage": {
            "changed_paths": changed,
            "affected_case_ids": list(required),
            "static_case_ids": static_cases,
        },
        "cases": {
            "required_case_ids": list(required),
            "executed": len(required),
            "per_stage_consistent": True,
            "rows": case_rows,
        },
        "regression": {
            "static_case_ids": static_cases,
            "static_baseline_bundle_hash": bundle_content_hash(initial_content()),
            "passed": len(static_cases),
            "failed": 0,
            "failed_ids": [],
        },
        "gate_environment": {
            "temp_store_path": str(store._path.parent / "gate-store.db"),
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
        "tests": {
            "passed": passed,
            "failed": failed,
            "failed_ids": list(failed_ids),
        },
        "quarantine_reason": None if decision == "publish" else "test_failure",
    }


def stop_auto_carriers(sources, fullpath: str) -> list[str]:
    carriers: list[str] = []
    for entry in sources:
        manifest = entry.get("manifest") or {}
        auto = any(
            slot.get("fullpath") == fullpath for slot in manifest.get("slots") or []
        )
        observed = any(
            slot.get("fullpath") == fullpath for slot in entry.get("observer_slots") or []
        )
        if auto or observed:
            carriers.append(str(entry.get("source_id")))
    return carriers


def observation_overrides(sources, fullpath: str, value: str) -> dict:
    return {
        source_id: {fullpath: value}
        for source_id in stop_auto_carriers(sources, fullpath)
    }


def observer_pending_rows(store) -> list[tuple[str, str, str]]:
    conn = store._connect()
    try:
        return [
            (str(row[0]), str(row[1]), str(row[2] or ""))
            for row in conn.execute(
                "SELECT source_id, state, period FROM updater_source_states"
                " WHERE state LIKE 'observer_pending:%' ORDER BY rowid"
            )
        ]
    finally:
        conn.close()


def initial_content() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return copy.deepcopy(INITIAL_BUNDLE_CONTENT)


def changed_bundle_content(value: str) -> dict:
    content = initial_content()
    content["data"]["reductions"]["2025_26"]["cap"] = value
    return content


def avd_changed_bundle_content(value: str = AVD_CHANGED) -> dict:
    content = initial_content()
    content["data"]["stamps"]["property"]["related_conveyance"]["fixed_duty"] = value
    return content


def parent_entry(
    birth_date: str,
    *,
    relationship: str = "mother",
    residence: object = True,
    maintained: object = True,
    together: object = True,
) -> dict:
    return {
        "relationship": relationship,
        "birth_date": birth_date,
        "residence": {"value": residence},
        "maintained": {"value": maintained},
        "living_together_all_year": {"value": together},
    }


def resolve_bundle_path(bundle_data, fullpath: str):
    parts = fullpath.split(".")
    if parts and parts[0] == "data":
        parts = parts[1:]
    node = bundle_data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def scheduler_sources(scheduler) -> list:
    for name in ("sources", "_sources"):
        if hasattr(scheduler, name):
            return getattr(scheduler, name)
    raise AttributeError("Scheduler does not expose sources (_sources)")


def scheduler_binding(scheduler, name: str):
    for candidate in (name, f"_{name}"):
        if hasattr(scheduler, candidate):
            return getattr(scheduler, candidate)
    return None


def inject_scheduler_seams(
    scheduler,
    *,
    clock=None,
    outbound=None,
    sources=None,
    harness=None,
    engine=None,
    required_cases=None,
) -> None:
    for name, value in (
        ("clock", clock),
        ("outbound", outbound),
        ("harness", harness),
        ("engine", engine),
        ("required_cases", required_cases),
    ):
        if value is None:
            continue
        setattr(scheduler, name if hasattr(scheduler, name) else f"_{name}", value)
    if sources is not None:
        setattr(scheduler, "sources" if hasattr(scheduler, "sources") else "_sources", sources)
        ids = [str(source.get("source_id")) for source in sources]
        if hasattr(scheduler, "_source_ids"):
            setattr(scheduler, "_source_ids", ids)


def _anchor_parts(anchor: str) -> tuple[str, str, str]:
    heading, row, col = anchor, "Item", "Value"
    for part in anchor.split("#"):
        if part.startswith("heading:"):
            heading = part[len("heading:") :]
        elif part.startswith("row:"):
            row = part[len("row:") :]
        elif part.startswith("col:"):
            col = part[len("col:") :]
    return heading, row, col


def render_source_document(
    manifest: dict,
    bundle_data: dict,
    overrides: dict | None = None,
    observer_slots: list | None = None,
) -> bytes:
    if manifest.get("artifact_type") == "pdf_layout":
        return _render_pdf_source_document(manifest, bundle_data, overrides)
    esc = _html_escape
    sections: dict[str, list[tuple[str, str, str]]] = {}
    order: list[str] = []
    observer_slots = list(observer_slots or [])
    observer_ids = {id(slot) for slot in observer_slots}
    slots = list(manifest.get("slots") or []) + observer_slots
    for slot in slots:
        heading, row, col = _anchor_parts(str(slot.get("anchor") or ""))
        fullpath = str(slot.get("fullpath") or "")
        value = (overrides or {}).get(fullpath)
        if value is None:
            baseline_path = (
                STOP_AUTO_TARGETS.get(fullpath, fullpath)
                if id(slot) in observer_ids
                else fullpath
            )
            resolved = resolve_bundle_path(bundle_data, baseline_path)
            value = resolved if resolved is not None else "__unresolved__"
        if heading not in sections:
            sections[heading] = []
            order.append(heading)
        sections[heading].append((row, col, str(value)))
    parts = ["<html><body>"]
    for heading in order:
        parts.append(f"<h2>{esc(heading)}</h2>")
        for row, col, value in sections[heading]:
            parts.append(
                f"<table><tr><th>{esc(row)}</th><th>{esc(col)}</th></tr>"
                f"<tr><td>{esc(row)}</td><td>{esc(value)}</td></tr></table>"
            )
    rule_headings: list[str] = []
    for rule in manifest.get("legal_state_rules") or []:
        anchor = rule.get("anchor") if isinstance(rule, dict) else None
        if isinstance(anchor, str) and anchor.startswith("heading:"):
            heading = _anchor_parts(anchor)[0]
            if heading not in sections and heading not in rule_headings:
                rule_headings.append(heading)
    for heading in rule_headings:
        parts.append(f"<h2>{esc(heading)}</h2><p>placeholder-no-notice</p>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def _render_pdf_source_document(manifest: dict, bundle_data: dict, overrides: dict | None) -> bytes:
    """按 pdf_layout manifest 生成最小 PDF（年度标题行＋(b) 行＋金额）。"""
    lines: list[str] = []
    for slot in manifest.get("slots") or []:
        heading, row, _col = _anchor_parts(str(slot.get("anchor") or ""))
        fullpath = str(slot.get("fullpath") or "")
        value = (overrides or {}).get(fullpath)
        if value is None:
            resolved = resolve_bundle_path(bundle_data, fullpath)
            value = resolved if resolved is not None else "0"
        if heading not in lines:
            lines.append(heading)
        lines.append(f"(b)  {row}  ${value}")
    return _minimal_pdf(lines)


def _minimal_pdf(lines: list[str]) -> bytes:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 12 Tf 72 760 Td " + "".join(
        f"({esc(line)}) Tj 0 -14 Td " for line in lines
    ) + "ET"
    payload = stream.encode("ascii", "replace")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(payload)).encode("ascii") + b" >>\nstream\n"
        + payload + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def production_round_documents(sources, bundle_data, overrides_by_source=None) -> dict:
    documents: dict = {}
    for entry in sources:
        overrides = (overrides_by_source or {}).get(str(entry.get("source_id"))) or {}
        documents[str(entry.get("url"))] = render_source_document(
            entry.get("manifest") or {},
            bundle_data,
            overrides,
            entry.get("observer_slots"),
        )
    return documents
