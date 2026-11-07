"""C8.1/C8.4 生产回归门禁绑定：独立参考 harness＋候选引擎＋必需 case 集合。

REQ: 15/18（SDD §5 管线阶段 6：在候选 bundle 上运行受影响＋回归测试；参考
参数只来自已批准独立 source/proof；必需 case 全部执行；空报告＝隔离）。
规格锚点:
  - Annex C C8.1：独立参考 harness 不含生产税务/取整 helper；参考期望值为
    已完成独立核验的固定 fixture，不读候选参数对象；两个引擎一致本身不构成
    证据——参数独立核验由 validator（管线阶段 5）承担，本模块只承担回归对照。
  - Annex C C8.2/C8.3：fixture 结构化（typed 事实＋来源锚点＋按阶段期望）；
    静态回归 pin 已批准历史束（首启发布的 INITIAL_BUNDLE_CONTENT）对应数据区；
    候选实际引擎输出 vs 固定参考（不得以旧期望配未核新参数）。
  - Annex C C8.4：固定 caseID 集合（本批＝Annex B 台账已核回归样例）；每 case
    附参数证据引用（独立来源 fact_id）。
期望值来源（Annex B 台账；见 docs/research/001-hk-tax-rule-evidence.md）:
  - PAM39 Q1（2025/26，台账 1.13 T2 候选）：240,000／MPF 9,000 → 累进 3,940、
    宽减 3,000、final 940（与 Q2 PST 35,890 属不同假设，不得混用）。
  - pty.htm Q7（2025/26，台账 1.7 T1）：租金 120,000 → NAV 96,000 → 税 14,400。
  - pty.htm Q32（2025/26，台账 1.7 T1）：PA 12,920−3,000＝9,920（分开评税
    28,800，不得混用）。
"""

from __future__ import annotations

import copy
import hashlib
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.config import ENGINE_VERSION
from app.core.hashing import bundle_content_hash, canonical_json_bytes
from app.rules.bundle import INITIAL_BUNDLE_CONTENT

# 静态回归基准＝已批准历史束（C8.3：pin 历史 bundle＋期望）；候选变化参数不
# 在回归样例内漂移（其独立核验由 validator 前置于本门禁，SDD §5 阶段 5）。
_APPROVED_BASELINE: Mapping[str, Any] = INITIAL_BUNDLE_CONTENT


def _availability_context(
    content: Mapping[str, Any], year: str
) -> dict[str, Any]:
    """静态回归的显式 AvailabilityContext（v2 引擎不得缺省放行）。"""
    start = int(year[:4])
    following = f"{start + 1}_{(start + 2) % 100:02d}"
    matrix = (content.get("data") or {}).get("allowances") or {}
    return {
        "binding": {
            "bundle_id": "b-static-reference",
            "bundle_hash": bundle_content_hash(content),
            "rules_schema_version": content.get("rules_schema_version"),
            "engine_version": ENGINE_VERSION,
        },
        "current": {"year": year, "status": "available", "reason_codes": []},
        "next": {
            "year": following,
            "status": "available" if following in matrix else "missing_rules",
            "reason_codes": [],
        },
    }


_SALARIES_Q1_FACTS: dict[str, Any] = {
    "year_of_assessment": "2025_26",
    "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
    "years": {
        "2025_26": {
            "participants": [{"person_id": "p1", "role": "self"}],
            "subject_facts": {},
            "household": {
                "marriage": [
                    {
                        "from": "2025-04-01",
                        "to": "2026-04-01",
                        "value": "unmarried",
                    }
                ],
                "living_apart": [],
                "child_nomination": {"person_id": None, "status": "none"},
            },
            "relations": [],
            "care_payments": [],
            "claims": [],
            "financials": {
                "p1": {
                    "has_assessable_salary_income": True,
                    "source_classification": {"salary": "confirmed_in_scope"},
                    "properties": [],
                    "businesses": [],
                    "deduction_items": [],
                    "employment_income_nai": "240000",
                    "mpf_mandatory_contributions": "9000",
                }
            },
            "assessment": {
                "basis": "actual",
                "candidates": ["salary_separate"],
                "selection": "salary_separate",
            },
            "manual_issues": [],
        }
    },
    "hk_presence_days": [],
    "assumptions": [],
}

_PROPERTY_Q7_FACTS: dict[str, Any] = {
    "year_of_assessment": "2025_26",
    "properties": [
        {
            "property_id": "p-1",
            "rent_received": "120000",
            "rates_paid_by_owner": {
                "amount": "0",
                "agreed": {"value": True},
                "actually_paid": {"value": True},
            },
            "irrecoverable_rent": "0",
            "deposit_offsets": "0",
        }
    ],
}

# 官方 Q32：已婚夫妻；本人薪俸 250,000，配偶持物业租金 240,000（差饷 0）。
# PA 合并入息 442,000 − 已婚免税额 264,000 = 178,000 → 12,920 − 3,000 = 9,920。
_PA_Q32_YEAR: dict[str, Any] = {
    "participants": [
        {"person_id": "p1", "role": "self"},
        {"person_id": "p2", "role": "spouse"},
    ],
    "subject_facts": {
        "p1": {
            "ordinarily_resident_hk": [
                {"from": "2025-04-01", "to": "2026-04-01", "value": True},
                {"from": "2026-06-01", "to": "2026-06-02", "value": True},
            ]
        },
        "p2": {
            "ordinarily_resident_hk": [
                {"from": "2025-04-01", "to": "2026-04-01", "value": True},
                {"from": "2026-06-01", "to": "2026-06-02", "value": True},
            ]
        },
    },
    "household": {
        "marriage": [
            {
                "from": "2025-04-01",
                "to": "2026-04-01",
                "value": "married",
                "spouse_id": "p2",
            },
            {
                "from": "2026-06-01",
                "to": "2026-06-02",
                "value": "married",
                "spouse_id": "p2",
            },
        ],
        "living_apart": [
            {"from": "2025-04-01", "to": "2026-04-01", "value": False},
            {"from": "2026-06-01", "to": "2026-06-02", "value": False},
        ],
        "child_nomination": {"person_id": None, "status": "none"},
    },
    "relations": [],
    "care_payments": [],
    "claims": [],
    "financials": {
        "p1": {
            "has_assessable_salary_income": True,
            "has_income_assessable_under_ordinance": True,
            "source_classification": {"salary": "confirmed_in_scope"},
            "properties": [],
            "businesses": [],
            "deduction_items": [],
            "employment_income_nai": "250000",
            "mpf_mandatory_contributions": "0",
        },
        "p2": {
            "has_assessable_salary_income": False,
            "has_assessable_property_income": True,
            "has_income_assessable_under_ordinance": True,
            "source_classification": {
                "salary": "confirmed_in_scope",
                "property": "confirmed_in_scope",
            },
            "properties": [
                {
                    "property_id": "h-1",
                    "share": "1/1",
                    "rent_received": "240000",
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
            "employment_income_nai": "0",
            "mpf_mandatory_contributions": "0",
        },
    },
    "assessment": {
        "reference_date": "2026-06-01",
        "basis": "hypothetical",
        "candidates": ["no_pa", "pa_joint"],
        "selection": "best",
    },
    "manual_issues": [],
    "provisional_paid": "0",
}

_PA_Q32_FACTS: dict[str, Any] = {
    "year_of_assessment": "2025_26",
    "subjects": [
        {"subject_id": "p1", "birth_date": "1990-01-01"},
        {"subject_id": "p2", "birth_date": "1992-02-02"},
    ],
    "years": {"2025_26": _PA_Q32_YEAR},
    "hk_presence_days": [],
    "assumptions": [],
}


def _run_salaries_q1(content: Mapping[str, Any]) -> tuple[list[Any], Any]:
    from app.engines.salaries import calculate_salaries_tax

    amounts = calculate_salaries_tax(
        _SALARIES_Q1_FACTS,
        content,
        availability=_availability_context(content, "2025_26"),
    )["amounts"]
    return (
        [
            amounts["before_reduction"],
            amounts["reduction"],
            amounts["final_after_reduction"],
        ],
        amounts["final_after_reduction"],
    )


def _run_property_q7(content: Mapping[str, Any]) -> tuple[list[Any], Any]:
    from app.engines.property import calculate_property_tax

    result = calculate_property_tax(_PROPERTY_Q7_FACTS, content)
    amounts = result["amounts"]
    nav = result["properties"][0]["nav"]
    return ([nav, amounts["final_after_reduction"]], amounts["final_after_reduction"])


def _run_pa_q32(content: Mapping[str, Any]) -> tuple[list[Any], Any]:
    from app.engines.personal_assessment import compare_personal_assessment

    result = compare_personal_assessment(
        _PA_Q32_FACTS,
        content,
        availability=_availability_context(content, "2025_26"),
    )
    scenario_steps = [
        step.get("detail") or {}
        for step in (result.get("steps") or [])
        if isinstance(step, Mapping)
        and step.get("kind") == "scenario"
        and (step.get("detail") or {}).get("scenario_id") in {"pa_self", "pa_joint"}
    ]
    if not scenario_steps:
        raise KeyError("PA static regression produced no personal-assessment scenario")
    scenario = scenario_steps[0]
    amounts = scenario.get("amounts") or {}
    return (
        [
            amounts.get("before_reduction"),
            amounts.get("reduction"),
            amounts.get("final_after_reduction"),
        ],
        amounts.get("final_after_reduction"),
    )


# 固定 caseID 集合（C8.4）。每 case：
#   domain＝静态回归 pin 的数据区（对候选内容覆盖为该区已批准基准）；
#   runner(content) -> (stages, output)：在（pin 后的）候选 bundle 上运行生产引擎；
#   expected_stages/expected_output＝固定参考期望（C8.2 fixture，不读候选）；
#   parameter_evidence_ref/evidence_fact＝该 case 的独立参数证据（C8.4）。
_CASES: dict[str, dict[str, Any]] = {
    "case_salaries_pam39_q1_2025_26": {
        "domain": "salaries",
        "runner": _run_salaries_q1,
        "expected_stages": ["3940", "3000", "940"],
        "expected_output": "940",
        "parameter_evidence_ref": "fact:reference:pam39_q1",
        "evidence_fact": {
            "fact_id": "fact:reference:pam39_q1",
            "fullpath": "salaries.reductions.2025_26.cap",
            "value": "3000",
            "source_id": "ird_pam39",
            "url": "https://www.ird.gov.hk/eng/pdf/pam39e.pdf",
            "anchor": "heading:PAM39 - Salaries Tax Q1",
            "tier": "T2",
        },
    },
    "case_property_pty_q7_2025_26": {
        "domain": "property",
        "runner": _run_property_q7,
        "expected_stages": ["96000", "14400"],
        "expected_output": "14400",
        "parameter_evidence_ref": "fact:reference:pty_q7",
        "evidence_fact": {
            "fact_id": "fact:reference:pty_q7",
            "fullpath": "property.years.2025_26.standard_rate",
            "value": "15/100",
            "source_id": "ird_faq_pty",
            "url": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "anchor": "heading:Property Tax - Q7",
            "tier": "T1",
        },
    },
    "case_pa_pty_q32_2025_26": {
        "domain": "personal_assessment",
        "runner": _run_pa_q32,
        "expected_stages": ["12920", "3000", "9920"],
        "expected_output": "9920",
        "parameter_evidence_ref": "fact:reference:pty_q32",
        "evidence_fact": {
            "fact_id": "fact:reference:pty_q32",
            "fullpath": "personal_assessment.reductions.2025_26.cap",
            "value": "3000",
            "source_id": "ird_faq_pty",
            "url": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "anchor": "heading:Personal Assessment - Q32",
            "tier": "T1",
        },
    },
}

REQUIRED_CASES: tuple[str, ...] = tuple(_CASES)

# 005 §15.3/§15.6、006 §7.1：八条不受影响路径的固定 path→case 计划表。
# 当前为计划 Red 证据，不进入生产 REQUIRED_CASES（静态回归三例保持不变）。
AFFECTED_CASES: dict[str, str] = {
    "stamps.property.related_conveyance.fixed_duty": "a_avd_related_fixed",
    "stamps.stock.other_transfer_fixed": "a_stock_other_fixed",
    "stamps.lease.rent_ceiling_unit": "a_lease_ceiling_boundary",
    "stamps.stock.effective_from": "a_stock_effective_boundary",
    "property.years.2025_26.standard_rate": "a_property_rate",
    "property.repair_allowance_ratio": "a_property_repair_ratio",
    "profits.two_tier_threshold": "a_profits_threshold_boundary",
    "profits.reductions.2025_26.cap": "a_profits_rebate_cap",
}


def _b5_plan(
    case_id: str,
    path: str,
    *,
    subcase_ids: Sequence[str],
    subcases: Sequence[Mapping[str, Any]],
    coverage_gap: bool = False,
    parameter_evidence: Mapping[str, Any] | None = None,
    required_read_paths: Sequence[str] = (),
    source_refs: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "path": path,
        "mode": "candidate",
        "coverage_gap": bool(coverage_gap),
        "subcase_ids": list(subcase_ids),
        "subcases": [dict(subcase) for subcase in subcases],
        "parameter_evidence": (
            None if parameter_evidence is None else dict(parameter_evidence)
        ),
        "required_read_paths": list(required_read_paths),
        "source_refs": list(source_refs),
    }


B5_CASES: dict[str, dict[str, Any]] = {
    "a_avd_related_fixed": _b5_plan(
        "a_avd_related_fixed",
        "stamps.property.related_conveyance.fixed_duty",
        subcase_ids=["avd_related_stamped_fixed"],
        subcases=[
            {
                "subcase_id": "avd_related_stamped_fixed",
                "input": {
                    "instrument_date": "2025-06-10",
                    "instrument_kind": "conveyance_on_sale",
                    "consideration": "5000000",
                    "value": "5000000",
                    "prior_agreement_date": "2025-03-01",
                    "prior_agreement_duly_stamped": True,
                },
                "expected": {"fixed_duty": "100"},
            }
        ],
        parameter_evidence={
            "fullpath": "stamps.property.related_conveyance.fixed_duty",
            "value": "100",
            "source_id": "elegislation_cap117",
            "url": "https://www.elegislation.gov.hk/hk/cap117!en",
            "anchor": "heading:Cap 117 s.29D - Related Conveyance",
            "tier": "T1",
        },
        required_read_paths=[
            "app/engines/stamps/property.py:134-145",
            "app/engines/stamps/property.py:265-288",
            "app/api/validation.py:1097",
        ],
        source_refs=["D1.7", "Cap117 s.29D(2)(a)", "sd_pty_rates Note(ii)"],
    ),
    "a_stock_other_fixed": _b5_plan(
        "a_stock_other_fixed",
        "stamps.stock.other_transfer_fixed",
        subcase_ids=["stock_two_other_transfers"],
        subcases=[
            {
                "subcase_id": "stock_two_other_transfers",
                "input": {
                    "instrument_date": "2025-06-10",
                    "complete": True,
                    "document_count": 2,
                    "documents": [
                        {"doc_kind": "other_transfer", "value": "0"},
                        {"doc_kind": "other_transfer", "value": "0"},
                    ],
                },
                "expected": {"fixed_duty_total": "10"},
            }
        ],
        parameter_evidence={
            "fullpath": "stamps.stock.other_transfer_fixed",
            "value": "5",
            "source_id": "elegislation_cap117",
            "url": "https://www.elegislation.gov.hk/hk/cap117!en",
            "anchor": "heading:Stamp Duty Rates#row:Stock transfer - other transfers",
            "tier": "T1",
        },
        required_read_paths=[
            "app/engines/stamps/stock.py:88",
            "app/engines/stamps/stock.py:125-128",
        ],
        source_refs=["D2.1", "Cap117 head 2", "GovHK stamp duty rates"],
    ),
    "a_lease_ceiling_boundary": _b5_plan(
        "a_lease_ceiling_boundary",
        "stamps.lease.rent_ceiling_unit",
        subcase_ids=[
            "lease_ceiling_10000",
            "lease_ceiling_10000_01",
            "lease_ceiling_10099_99",
            "lease_ceiling_10100",
        ],
        subcases=[
            {
                "subcase_id": "lease_ceiling_10000",
                "input": {
                    "instrument_date": "2025-06-10",
                    "term_kind": "indefinite",
                    "annual_rent": "10000",
                    "premium": "0",
                    "copies": 0,
                },
                "expected": {"rent_base": "10000", "tax": "25"},
            },
            {
                "subcase_id": "lease_ceiling_10000_01",
                "input": {
                    "instrument_date": "2025-06-10",
                    "term_kind": "indefinite",
                    "annual_rent": "10000.01",
                    "premium": "0",
                    "copies": 0,
                },
                "expected": {"rent_base": "10100", "tax": "26"},
            },
            {
                "subcase_id": "lease_ceiling_10099_99",
                "input": {
                    "instrument_date": "2025-06-10",
                    "term_kind": "indefinite",
                    "annual_rent": "10099.99",
                    "premium": "0",
                    "copies": 0,
                },
                "expected": {"rent_base": "10100", "tax": "26"},
            },
            {
                "subcase_id": "lease_ceiling_10100",
                "input": {
                    "instrument_date": "2025-06-10",
                    "term_kind": "indefinite",
                    "annual_rent": "10100",
                    "premium": "0",
                    "copies": 0,
                },
                "expected": {"rent_base": "10100", "tax": "26"},
            },
        ],
        parameter_evidence={
            "fullpath": "stamps.lease.rent_ceiling_unit",
            "value": "100",
            "source_id": "elegislation_cap117",
            "url": "https://www.elegislation.gov.hk/hk/cap117!en",
            "anchor": "heading:Lease - rent ceiling unit",
            "tier": "T1",
        },
        required_read_paths=[
            "app/engines/stamps/lease.py:123-125",
            "app/engines/stamps/lease.py:356-359",
        ],
        source_refs=["D3.1", "D3.1a", "Cap117 head 1(2)"],
    ),
    "a_stock_effective_boundary": _b5_plan(
        "a_stock_effective_boundary",
        "stamps.stock.effective_from",
        subcase_ids=[],
        subcases=[],
        coverage_gap=True,
        parameter_evidence=None,
        required_read_paths=["app/engines/stamps/stock.py:168-173"],
        source_refs=["Cap117 head 2 effective 2023-11-17"],
    ),
    "a_property_rate": _b5_plan(
        "a_property_rate",
        "property.years.2025_26.standard_rate",
        subcase_ids=["property_rate_2025_26"],
        subcases=[
            {
                "subcase_id": "property_rate_2025_26",
                "input": {
                    "year_of_assessment": "2025_26",
                    "rent_received": "120000",
                    "rates_paid": "0",
                    "irrecoverable_rent": "0",
                    "deposit_offsets": "0",
                },
                "expected": {"nav": "96000", "tax": "14400"},
            }
        ],
        parameter_evidence={
            "fullpath": "property.years.2025_26.standard_rate",
            "value": "15/100",
            "source_id": "ird_faq_pty",
            "url": "https://www.ird.gov.hk/eng/faq/pty.htm",
            "anchor": "heading:Property Tax - Q7",
            "tier": "T1",
        },
        required_read_paths=["app/engines/property.py:61", "app/engines/property.py:107"],
        source_refs=["D5", "pty.htm Q7", "Cap112 s.5"],
    ),
    "a_property_repair_ratio": _b5_plan(
        "a_property_repair_ratio",
        "property.repair_allowance_ratio",
        subcase_ids=["repair_ratio_120000", "repair_ratio_120000_99"],
        subcases=[
            {
                "subcase_id": "repair_ratio_120000",
                "input": {"rent_received": "120000"},
                "expected": {"tax": "14400"},
            },
            {
                "subcase_id": "repair_ratio_120000_99",
                "input": {"rent_received": "120000.99"},
                "expected": {"tax": "14400"},
            },
        ],
        parameter_evidence={
            "fullpath": "property.repair_allowance_ratio",
            "value": "1/5",
            "source_id": "elegislation_cap112",
            "url": "https://www.elegislation.gov.hk/hk/cap112!en",
            "anchor": "heading:Property Tax - repair allowance",
            "tier": "T1",
        },
        required_read_paths=["app/engines/property.py:62", "app/engines/property.py:107"],
        source_refs=["D5", "BIR57 Note3", "Cap112 s.5(1A)"],
    ),
    "a_profits_threshold_boundary": _b5_plan(
        "a_profits_threshold_boundary",
        "profits.two_tier_threshold",
        subcase_ids=[
            "profits_threshold_1999999_99",
            "profits_threshold_2000000",
            "profits_threshold_2000000_01",
            "profits_threshold_2000001",
        ],
        subcases=[
            {
                "subcase_id": "profits_threshold_1999999_99",
                "input": {"assessable_profit": "1999999.99"},
                "expected": {"before": "149999", "final": "146999"},
            },
            {
                "subcase_id": "profits_threshold_2000000",
                "input": {"assessable_profit": "2000000"},
                "expected": {"before": "150000", "final": "147000"},
            },
            {
                "subcase_id": "profits_threshold_2000000_01",
                "input": {"assessable_profit": "2000000.01"},
                "expected": {"before": "150000", "final": "147000"},
            },
            {
                "subcase_id": "profits_threshold_2000001",
                "input": {"assessable_profit": "2000001"},
                "expected": {"before": "150000", "final": "147000"},
            },
        ],
        parameter_evidence={
            "fullpath": "profits.two_tier_threshold",
            "value": "2000000",
            "source_id": "ird_bus_pft",
            "url": "https://www.ird.gov.hk/eng/tax/bus_pft.htm",
            "anchor": "heading:Two-tiered Profits Tax Rates#row:Threshold",
            "tier": "T1",
        },
        required_read_paths=[
            "app/engines/profits.py:103-104",
            "app/engines/profits.py:173-189",
            "app/engines/profits.py:432-434",
        ],
        source_refs=["D4.1-D4.3", "D6.2a"],
    ),
    "a_profits_rebate_cap": _b5_plan(
        "a_profits_rebate_cap",
        "profits.reductions.2025_26.cap",
        subcase_ids=[
            "profits_rebate_39999_99",
            "profits_rebate_40000",
            "profits_rebate_40020",
        ],
        subcases=[
            {
                "subcase_id": "profits_rebate_39999_99",
                "input": {"assessable_profit": "39999.99"},
                "expected": {"reduction": "2999", "final": "0"},
            },
            {
                "subcase_id": "profits_rebate_40000",
                "input": {"assessable_profit": "40000"},
                "expected": {"reduction": "3000", "final": "0"},
            },
            {
                "subcase_id": "profits_rebate_40020",
                "input": {"assessable_profit": "40020"},
                "expected": {"reduction": "3000", "final": "1"},
            },
        ],
        parameter_evidence={
            "fullpath": "profits.reductions.2025_26.cap",
            "value": "3000",
            "source_id": "elegislation_cap112",
            "url": "https://www.elegislation.gov.hk/hk/cap112!en",
            "anchor": "heading:Two-tiered Profits Tax Rates#row:2025/26 reduction cap",
            "tier": "T1",
        },
        required_read_paths=[
            "app/engines/profits.py:274-284",
            "app/engines/profits.py:439-440",
        ],
        source_refs=["Schedule 43", "D6.3"],
    ),
}


def _data_value(content: Mapping[str, Any], fullpath: str) -> Any:
    node: Any = content.get("data")
    for part in str(fullpath).split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _deep_overlay(base: Any, overlay: Any) -> Any:
    """Recursively overlay ``overlay`` onto ``base`` without mutating inputs."""
    if isinstance(base, Mapping) and isinstance(overlay, Mapping):
        result = copy.deepcopy(dict(base))
        for key, value in overlay.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = _deep_overlay(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    return copy.deepcopy(overlay)


def _canonical_sha(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


# ---------------------------------------------------------------------------
# OR-1: proof-derived B5 子 case 边界
#
# 受影响参数（cap/threshold/ceiling/rate/unit）变化时，参考期望与候选实际必须
# 都围绕 *proof 值* 重新生成边界输入与阶段期望；baseline 值（与 B5_CASES 的
# parameter_evidence 相同）一律原样返回固定 fixture，保证历史基线不漂移。
# 参考侧只使用独立已核验常量，不读取候选参数对象。
# ---------------------------------------------------------------------------
_B5_PROFITS_LOW_RATE = Fraction(750, 10000)       # 非法团首 200 万 7.5%
_B5_PROFITS_HIGH_RATE = Fraction(1500, 10000)     # 非法团全额 15%
_B5_REBATE_PERCENT = Fraction(10000, 10000)       # 2025/26 100% 宽减
_B5_REBATE_CAP_BASELINE = Fraction(3000)          # 2025/26 法定宽减上限
_B5_LEASE_INDEFINITE_RATE = Fraction(25, 10000)   # 不定期租约 0.25%
_B5_PROPERTY_RATE_BASELINE = Fraction(15, 100)    # 2025/26 标准税率
_B5_PROPERTY_REPAIR_RATIO_BASELINE = Fraction(1, 5)


def _fraction_value(value: Any) -> Fraction:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"B5 参数值不是十进制数：{value!r}")
    return Fraction(str(value))


def _floor_value(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil_value(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _decimal_text(value: Fraction) -> str:
    """Fraction -> 规范十进制字符串（拒绝非终止小数，金额不使用 float）。"""
    if value.denominator == 1:
        return str(value.numerator)
    numerator = value.numerator
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError(f"B5 派生边界不是终止小数：{value!r}")
    places = max(twos, fives)
    scaled = numerator * (10 ** places) // value.denominator
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled)).rjust(places + 1, "0")
    return f"{sign}{digits[:-places]}.{digits[-places:]}"


def _static_subcases(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "subcase_id": str(subcase["subcase_id"]),
            "input": copy.deepcopy(dict(subcase["input"])),
            "expected": copy.deepcopy(dict(subcase["expected"])),
        }
        for subcase in plan.get("subcases") or []
    ]


def _derived_entry(
    subcase: Mapping[str, Any],
    *,
    input_value: str,
    input_key: str,
    expected_values: Mapping[str, Any],
) -> dict[str, Any]:
    expected_template = subcase["expected"]
    return {
        "subcase_id": str(subcase["subcase_id"]),
        "input": {**copy.deepcopy(dict(subcase["input"])), input_key: input_value},
        "expected": {
            key: expected_values[key] for key in expected_template
        },
    }


def _derive_profits_rebate_subcases(
    plan: Mapping[str, Any], value: str
) -> list[dict[str, Any]]:
    cap = _fraction_value(value)
    boundary = cap / _B5_PROFITS_LOW_RATE
    at = _ceil_value(boundary)
    below = Fraction(_floor_value(boundary) * 100 - 1, 100)
    above = Fraction(at + 20)
    profits = [_decimal_text(below), str(at), str(at + 20)]
    cap_floor = _floor_value(cap)
    entries: list[dict[str, Any]] = []
    for subcase, profit_text in zip(plan.get("subcases") or [], profits):
        before = _floor_value(Fraction(profit_text) * _B5_PROFITS_LOW_RATE)
        reduction = min(
            _ceil_value(_B5_REBATE_PERCENT * before), cap_floor, before
        )
        entries.append(
            _derived_entry(
                subcase,
                input_value=profit_text,
                input_key="assessable_profit",
                expected_values={
                    "reduction": str(reduction),
                    "final": str(before - reduction),
                },
            )
        )
    return entries


def _derive_profits_threshold_subcases(
    plan: Mapping[str, Any], value: str
) -> list[dict[str, Any]]:
    threshold = _fraction_value(value)
    profits = [
        threshold - Fraction(1, 100),
        threshold,
        threshold + Fraction(1, 100),
        threshold + 1,
    ]
    cap_floor = _floor_value(_B5_REBATE_CAP_BASELINE)
    entries: list[dict[str, Any]] = []
    for subcase, profit in zip(plan.get("subcases") or [], profits):
        if _floor_value(profit) <= threshold:
            before = _floor_value(profit * _B5_PROFITS_LOW_RATE)
        else:
            before = _floor_value(threshold * _B5_PROFITS_LOW_RATE) + _floor_value(
                (profit - threshold) * _B5_PROFITS_HIGH_RATE
            )
        reduction = min(
            _ceil_value(_B5_REBATE_PERCENT * before), cap_floor, before
        )
        entries.append(
            _derived_entry(
                subcase,
                input_value=_decimal_text(profit),
                input_key="assessable_profit",
                expected_values={
                    "before": str(before),
                    "final": str(before - reduction),
                },
            )
        )
    return entries


def _derive_lease_ceiling_subcases(
    plan: Mapping[str, Any], value: str
) -> list[dict[str, Any]]:
    unit = _fraction_value(value)
    unit_int = _floor_value(unit)
    if unit_int <= 0:
        raise ValueError("B5 租约 ceiling unit 必须为正整数")
    rents = [
        Fraction(unit_int * 100),
        Fraction(unit_int * 100) + Fraction(1, 100),
        Fraction(unit_int * 101) - Fraction(1, 100),
        Fraction(unit_int * 101),
    ]
    entries: list[dict[str, Any]] = []
    for subcase, rent in zip(plan.get("subcases") or [], rents):
        rent_base = _ceil_value(rent / unit_int) * unit_int
        tax = _ceil_value(Fraction(rent_base) * _B5_LEASE_INDEFINITE_RATE)
        entries.append(
            _derived_entry(
                subcase,
                input_value=_decimal_text(rent),
                input_key="annual_rent",
                expected_values={"rent_base": str(rent_base), "tax": str(tax)},
            )
        )
    return entries


def _property_chargeable(subcase: Mapping[str, Any]) -> Fraction:
    facts = subcase.get("input") or {}
    rent = _fraction_value(facts.get("rent_received", "0"))
    rates = _fraction_value(facts.get("rates_paid", "0"))
    irrecoverable = _fraction_value(facts.get("irrecoverable_rent", "0"))
    deposits = _fraction_value(facts.get("deposit_offsets", "0"))
    net_irrecoverable = max(Fraction(0), irrecoverable - deposits)
    return max(Fraction(0), rent - net_irrecoverable - rates)


def _derive_property_rate_subcases(
    plan: Mapping[str, Any], value: str
) -> list[dict[str, Any]]:
    rate = _fraction_value(value)
    entries: list[dict[str, Any]] = []
    for subcase in plan.get("subcases") or []:
        chargeable = _property_chargeable(subcase)
        nav = _floor_value(
            chargeable * (1 - _B5_PROPERTY_REPAIR_RATIO_BASELINE)
        )
        tax = _floor_value(Fraction(nav) * rate)
        entries.append(
            {
                "subcase_id": str(subcase["subcase_id"]),
                "input": copy.deepcopy(dict(subcase["input"])),
                "expected": {"nav": str(nav), "tax": str(tax)},
            }
        )
    return entries


def _derive_property_repair_subcases(
    plan: Mapping[str, Any], value: str
) -> list[dict[str, Any]]:
    ratio = _fraction_value(value)
    entries: list[dict[str, Any]] = []
    for subcase in plan.get("subcases") or []:
        chargeable = _property_chargeable(subcase)
        nav = _floor_value(chargeable * (1 - ratio))
        tax = _floor_value(Fraction(nav) * _B5_PROPERTY_RATE_BASELINE)
        entries.append(
            {
                "subcase_id": str(subcase["subcase_id"]),
                "input": copy.deepcopy(dict(subcase["input"])),
                "expected": {"tax": str(tax)},
            }
        )
    return entries


def _derive_stock_other_subcases(
    plan: Mapping[str, Any], value: str
) -> list[dict[str, Any]]:
    unit = _fraction_value(value)
    entries: list[dict[str, Any]] = []
    for subcase in plan.get("subcases") or []:
        facts = subcase.get("input") or {}
        count = facts.get("document_count")
        if not isinstance(count, int):
            count = len(facts.get("documents") or [])
        total = unit * count
        entries.append(
            {
                "subcase_id": str(subcase["subcase_id"]),
                "input": copy.deepcopy(dict(subcase["input"])),
                "expected": {"fixed_duty_total": str(_floor_value(total))},
            }
        )
    return entries


_B5_DERIVERS: dict[str, Any] = {
    "a_profits_rebate_cap": _derive_profits_rebate_subcases,
    "a_profits_threshold_boundary": _derive_profits_threshold_subcases,
    "a_lease_ceiling_boundary": _derive_lease_ceiling_subcases,
    "a_property_rate": _derive_property_rate_subcases,
    "a_property_repair_ratio": _derive_property_repair_subcases,
    "a_stock_other_fixed": _derive_stock_other_subcases,
}


def _b5_subcases_for_value(
    case_id: str, plan: Mapping[str, Any], value: Any
) -> list[dict[str, Any]]:
    """proof/candidate 参数值 -> 子 case；baseline 值保持 B5_CASES 原样。"""
    static = _static_subcases(plan)
    evidence = plan.get("parameter_evidence") or {}
    baseline_value = evidence.get("value")
    if baseline_value is not None and str(value) == str(baseline_value):
        return static
    deriver = _B5_DERIVERS.get(str(case_id))
    if deriver is None or not static:
        return static
    derived = deriver(plan, str(value))
    if len(derived) != len(static):
        raise ValueError(
            f"B5 派生子 case 数与固定计划不一致：{case_id!r}"
        )
    return derived


def _b5_avd_actual(
    subcase: Mapping[str, Any], content: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    from app.engines.stamps.property import calculate_property_avd

    facts = dict(subcase["input"])
    agreement_date = facts.pop("prior_agreement_date")
    agreement_duly_stamped = facts.pop("prior_agreement_duly_stamped")
    facts["prior_links"] = [
        {
            "conform": {
                "agreement_date": agreement_date,
                "agreement_duly_stamped": agreement_duly_stamped,
            }
        }
    ]
    result = calculate_property_avd(facts, content)
    return (
        {"fixed_duty": result["amounts"]["final_after_reduction"]},
        "app.engines.stamps.property.calculate_property_avd",
    )


def _b5_stock_actual(
    subcase: Mapping[str, Any], content: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    from app.engines.stamps.stock import calculate_stock_stamp_duty

    facts = dict(subcase["input"])
    facts.pop("document_count", None)
    result = calculate_stock_stamp_duty(facts, content)
    return (
        {"fixed_duty_total": result["amounts"]["final_after_reduction"]},
        "app.engines.stamps.stock.calculate_stock_stamp_duty",
    )


def _b5_lease_actual(
    subcase: Mapping[str, Any], content: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    from app.engines.stamps.lease import calculate_lease_stamp_duty

    facts = dict(subcase["input"])
    facts["rent_input_mode"] = "annual_rent"
    result = calculate_lease_stamp_duty(facts, content)
    return (
        {
            "rent_base": result["duty_breakdown"]["rounded_rent_base"],
            "tax": result["amounts"]["final_after_reduction"],
        },
        "app.engines.stamps.lease.calculate_lease_stamp_duty",
    )


def _b5_property_actual(
    subcase: Mapping[str, Any], content: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    from app.engines.property import calculate_property_tax

    facts = {
        "year_of_assessment": subcase["input"].get(
            "year_of_assessment", "2025_26"
        ),
        "properties": [
            {
                "property_id": "p-1",
                "share": "1/1",
                "rent_received": subcase["input"]["rent_received"],
                "rates_paid_by_owner": {
                    "amount": subcase["input"].get("rates_paid", "0"),
                    "agreed": True,
                    "actually_paid": True,
                },
                "irrecoverable_rent": subcase["input"].get(
                    "irrecoverable_rent", "0"
                ),
                "deposit_offsets": subcase["input"].get(
                    "deposit_offsets", "0"
                ),
                "mortgage_interest": "0",
            }
        ],
    }
    result = calculate_property_tax(facts, content)
    detail = result["properties"][0]
    return (
        {"nav": detail["nav"], "tax": detail["tax"]},
        "app.engines.property.calculate_property_tax",
    )


def _b5_profits_actual(
    subcase: Mapping[str, Any], content: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    from app.engines.profits import calculate_profits_tax

    facts = {
        "year_of_assessment": "2025_26",
        "entity_kind": "sole_proprietorship",
        "assessable_profit": subcase["input"]["assessable_profit"],
        "two_tier": {
            "connected_entities": [],
            "election_made": {"value": True},
            "no_other_election_same_year": {"value": True},
        },
    }
    result = calculate_profits_tax(facts, content)
    amounts = result["amounts"]
    return (
        {
            "before": amounts["before_reduction"],
            "reduction": amounts["reduction"],
            "final": amounts["final_after_reduction"],
        },
        "app.engines.profits.calculate_profits_tax",
    )


_B5_EXECUTORS: dict[str, Any] = {
    "a_avd_related_fixed": _b5_avd_actual,
    "a_stock_other_fixed": _b5_stock_actual,
    "a_lease_ceiling_boundary": _b5_lease_actual,
    "a_property_rate": _b5_property_actual,
    "a_property_repair_ratio": _b5_property_actual,
    "a_profits_threshold_boundary": _b5_profits_actual,
    "a_profits_rebate_cap": _b5_profits_actual,
}


def _execute_b5_subcase(
    case_id: str, subcase: Mapping[str, Any], content: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    executor = _B5_EXECUTORS.get(str(case_id))
    if executor is None:
        raise KeyError(f"B5 case 无可执行生产税引擎：{case_id!r}")
    return executor(subcase, content)


class ReferenceHarness:
    """C8.1 独立参考 harness：static fixture＋proof-controlled B5 执行分支。

    run_case(case_id, **kwargs)（outbound 由门禁注入计数代理；本 harness 不
    进行任何出站）；B5 case 的期望输出只来自注入的独立 proof fact，未知/
    无 proof → KeyError（门禁按缺必需 case/证据隔离，C8.4）。
    """

    def __init__(self, proof_facts: Sequence[Mapping[str, Any]] | None = None) -> None:
        self._proof_facts = [dict(fact) for fact in (proof_facts or ())]

    def run_case(self, case_id: str, **kwargs: Any) -> dict[str, Any]:
        case = _CASES.get(case_id)
        if case is not None:
            return {
                "case_id": case_id,
                "stages": list(case["expected_stages"]),
                "output": case["expected_output"],
                "parameter_evidence_ref": case["parameter_evidence_ref"],
            }
        plan = B5_CASES.get(case_id)
        if plan is None:
            raise KeyError(f"未知必需 case（未固化不得编造，C8.4）：{case_id}")
        fact = next(
            (
                item
                for item in self._proof_facts
                if item.get("fullpath") == plan.get("path")
                and item.get("tier") == "T1"
            ),
            None,
        )
        if fact is None or not fact.get("fact_id"):
            raise KeyError(
                f"B5 case 缺独立 proof fact（不得常量编造，C8.4）：{case_id}"
            )
        output = str(fact.get("value"))
        evidence_ref = str(fact["fact_id"])
        subcase_results: list[dict[str, Any]] = []
        path_leaf = str(plan.get("path") or "").rsplit(".", 1)[-1]
        for subcase in _b5_subcases_for_value(case_id, plan, fact.get("value")):
            expected = copy.deepcopy(dict(subcase["expected"]))
            if path_leaf in expected:
                expected[path_leaf] = output
            subcase_results.append(
                {
                    "subcase_id": str(subcase["subcase_id"]),
                    "input": copy.deepcopy(dict(subcase["input"])),
                    "expected": expected,
                    "parameter_evidence_ref": evidence_ref,
                }
            )
        return {
            "case_id": case_id,
            "subcase_ids": list(plan.get("subcase_ids") or []),
            "stages": [output],
            "output": output,
            "parameter_evidence_ref": evidence_ref,
            "subcase_results": subcase_results,
        }


class CandidateEngine:
    """候选引擎 seam：static 使用完整发行 baseline；B5 affected 分支读原样候选。

    run_case(case_id, candidate_content, **kwargs)：static case 以完整发行
    baseline 运行；B5 case 必须读取原样 candidate.data 的对应路径（不替换
    data 区），从而实际执行 proof-controlled 候选分支。
    """

    def run_case(
        self,
        case_id: str,
        candidate_content: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        case = _CASES.get(case_id)
        if case is not None:
            content = copy.deepcopy(dict(candidate_content))
            # 静态回归＝完整发行 baseline 副本（不是仅替换候选涉及的一个税区）。
            if isinstance(content.get("data"), dict):
                content["data"] = copy.deepcopy(_APPROVED_BASELINE.get("data") or {})
            stages, output = case["runner"](content)
            return {
                "case_id": case_id,
                "stages": list(stages),
                "output": output,
            }
        plan = B5_CASES.get(case_id)
        if plan is None:
            raise KeyError(f"未知必需 case（未固化不得编造，C8.4）：{case_id}")
        value = _data_value(candidate_content, str(plan["path"]))
        if value is None:
            raise KeyError(f"B5 case 候选缺失目标路径：{plan['path']!r}")
        # Affected B5 execution reads the original candidate data.  A caller may
        # supply only the candidate data region (e.g. a focused probe); fill in
        # missing bundle metadata from the approved release without replacing
        # the candidate data itself.
        content: Mapping[str, Any] = candidate_content
        if not isinstance(candidate_content.get("applicability"), Mapping):
            content = copy.deepcopy(dict(_APPROVED_BASELINE))
            content["data"] = _deep_overlay(
                _APPROVED_BASELINE.get("data") or {},
                candidate_content.get("data") or {},
            )
        output = str(value)
        subcase_results: list[dict[str, Any]] = []
        for subcase in _b5_subcases_for_value(case_id, plan, value):
            actual, engine_name = _execute_b5_subcase(
                case_id, subcase, content
            )
            expected = copy.deepcopy(dict(subcase["expected"]))
            passed = all(
                actual.get(key) == expected_value
                for key, expected_value in expected.items()
            )
            subcase_results.append(
                {
                    "subcase_id": str(subcase["subcase_id"]),
                    "input": copy.deepcopy(dict(subcase["input"])),
                    "expected": expected,
                    "actual": actual,
                    "passed": passed,
                    "receipt": {
                        "subcase_id": str(subcase["subcase_id"]),
                        "engine": engine_name,
                        "input_sha256": _canonical_sha(dict(subcase["input"])),
                    },
                }
            )
        return {
            "case_id": case_id,
            "subcase_ids": list(plan.get("subcase_ids") or []),
            "stages": [output],
            "output": output,
            "subcase_results": subcase_results,
        }


def reference_evidence_facts(case_ids: Sequence[str]) -> list[dict[str, Any]]:
    """必需 case 的独立参数证据事实（去重；未知 caseID 不编造、跳过）。"""
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case_id in case_ids or ():
        key = str(case_id)
        case = _CASES.get(key)
        if case is None or key in seen:
            continue
        seen.add(key)
        facts.append(copy.deepcopy(case["evidence_fact"]))
    return facts


def _reference_code_sha256() -> str:
    """当前 reference.py 文件字节 SHA256（不是 canonical label）。"""
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _profile_bindings(content: Mapping[str, Any]) -> list[dict[str, str]]:
    frozen = content.get("frozen_semantics")
    profiles = frozen.get("profiles") if isinstance(frozen, Mapping) else []
    rows: list[dict[str, str]] = []
    for profile in profiles or ():
        if not isinstance(profile, Mapping):
            raise ValueError("candidate frozen profile shape invalid")
        digest = profile.get("semantic_digest") or _canonical_sha(dict(profile))
        rows.append(
            {
                "profile_id": str(profile.get("profile_id")),
                "semantic_digest": str(digest),
            }
        )
    return rows


def release_seed_report() -> dict[str, Any]:
    """随发行固定 v2 seed 的完整绑定验证产物（purpose=seed，baseline 两项 null）。

    绑定冻结发行 candidate/manifest/profiles/evidence/engine/reference code
    与 ``REQUIRED_CASES``；publisher 对缺字段/不符一律零写入拒绝。
    """
    required = list(REQUIRED_CASES)
    return {
        "decision": "publish",
        "purpose": "seed",
        "binding": {
            "baseline_bundle_id": None,
            "baseline_bundle_hash": None,
            "baseline_rules_schema_version": None,
            "candidate_bundle_hash": bundle_content_hash(INITIAL_BUNDLE_CONTENT),
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": str(
                INITIAL_BUNDLE_CONTENT.get("rules_schema_version")
            ),
            "profiles": _profile_bindings(INITIAL_BUNDLE_CONTENT),
            "manifests_sha256": _canonical_sha([]),
            "evidence_sha256": _canonical_sha([]),
            "reference_code_sha256": _reference_code_sha256(),
            "required_case_ids": required,
        },
        "cases": {
            "required_case_ids": required,
            "executed": len(required),
        },
        "tests": {"passed": len(required), "failed": 0, "failed_ids": []},
        "quarantine_reason": None,
    }
