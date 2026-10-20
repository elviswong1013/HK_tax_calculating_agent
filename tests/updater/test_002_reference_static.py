"""P3-W1b Red2 F5-2：官方 PTY Q32 personal assessment witness（9,920）。

原始 Q32 facts 来自第一次返修 pre-snapshot：
`pre/app/updater/reference.py:58-96,179-193`（只读基线）——已婚夫妻：
本人薪金 250,000、配偶薪金 0 且持有物业租金 240,000（差饷 0）、
无暂缴；PA 宽减前 12,920、宽减 3,000、终额 9,920（分开评税 28,800
不得混用）。本测试要求生产 static case 以完整 v2 facts 保留该官方
witness，case ID/anchor 仍为 Q32。

Red（当前生产）：Q32 case 被改成单人 250,000＋物业 120,000，期望
18,380/3,000/15,380；本测试按 brief 期望 12,920/3,000/9,920。
"""

from __future__ import annotations

import copy


def test_002_reference_pa_q32_static_case_keeps_official_9920(monkeypatch) -> None:
    import app.engines.personal_assessment as pa_engine

    from app.rules.bundle import INITIAL_BUNDLE_CONTENT
    from app.updater.reference import CandidateEngine, ReferenceHarness, _CASES

    case_id = "case_pa_pty_q32_2025_26"
    case = _CASES[case_id]
    assert case["domain"] == "personal_assessment"
    assert case["expected_stages"] == ["12920", "3000", "9920"]
    assert case["expected_output"] == "9920"
    assert case["evidence_fact"]["anchor"] == "heading:Personal Assessment - Q32"

    captured: dict = {}
    original = pa_engine.compare_personal_assessment

    def _spy(facts, bundle, **kwargs):
        captured["facts"] = copy.deepcopy(facts)
        return original(facts, bundle, **kwargs)

    monkeypatch.setattr(pa_engine, "compare_personal_assessment", _spy)

    engine = CandidateEngine()
    actual = engine.run_case(case_id, copy.deepcopy(INITIAL_BUNDLE_CONTENT))
    assert actual["stages"] == ["12920", "3000", "9920"], (
        "Q32 official static witness must be 12,920/3,000/9,920: " f"{actual!r}"
    )
    assert actual["output"] == "9920"

    facts = captured["facts"]
    assert facts["year_of_assessment"] == "2025_26"
    assert "persons" not in facts, "Q32 facts must use v2 years/subjects, not v1 persons"
    assert {subject["subject_id"] for subject in facts["subjects"]} == {"p1", "p2"}
    year = facts["years"]["2025_26"]
    assert {p["person_id"]: p["role"] for p in year["participants"]} == {
        "p1": "self",
        "p2": "spouse",
    }
    marriage = year["household"]["marriage"]
    assert any(
        entry.get("value") == "married" and entry.get("spouse_id") == "p2"
        for entry in marriage
    ), f"Q32 is a married couple case: {marriage!r}"

    p1 = year["financials"]["p1"]
    p2 = year["financials"]["p2"]
    assert p1["employment_income_nai"] == "250000"
    assert p1["mpf_mandatory_contributions"] == "0"
    assert p1["properties"] == []
    assert p2["employment_income_nai"] == "0"
    assert p2["has_assessable_salary_income"] is False
    assert p2["has_assessable_property_income"] is True
    assert len(p2["properties"]) == 1
    prop = p2["properties"][0]
    assert prop["share"] == "1/1"
    assert prop["rent_received"] == "240000"
    assert prop["rates_paid_by_owner"]["amount"] == "0"
    assert prop["irrecoverable_rent"] == "0"
    assert prop["deposit_offsets"] == "0"
    assert year["provisional_paid"] == "0"

    expected = ReferenceHarness().run_case(case_id)
    assert expected["stages"] == ["12920", "3000", "9920"]
    assert expected["output"] == "9920"
    assert expected["parameter_evidence_ref"] == "fact:reference:pty_q32"
