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
from typing import Any, Mapping, Sequence

from app.rules.bundle import INITIAL_BUNDLE_CONTENT

# 静态回归基准＝已批准历史束（C8.3：pin 历史 bundle＋期望）；候选变化参数不
# 在回归样例内漂移（其独立核验由 validator 前置于本门禁，SDD §5 阶段 5）。
_APPROVED_BASELINE: Mapping[str, Any] = INITIAL_BUNDLE_CONTENT

_SALARIES_Q1_FACTS: dict[str, Any] = {
    "year_of_assessment": "2025_26",
    "employment_income": "240000",
    "mpf_mandatory_contributions": "9000",
    "married_status": {"value": "single"},
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

_PA_Q32_FACTS: dict[str, Any] = {
    "year_of_assessment": "2025_26",
    "married_status": {"value": "married"},
    "persons": [
        {
            "person_id": "self",
            "employment_income": "250000",
            "mpf_mandatory_contributions": "0",
            "properties": [],
            "businesses": [],
            "dependent_parents": [],
            "dependent_children": [],
        },
        {
            "person_id": "spouse",
            "employment_income": "0",
            "mpf_mandatory_contributions": "0",
            "properties": [
                {
                    "property_id": "p-1",
                    "share": "1/1",
                    "rent_received": "240000",
                    "rates_paid_by_owner": {
                        "amount": "0",
                        "agreed": {"value": True},
                        "actually_paid": {"value": True},
                    },
                    "irrecoverable_rent": "0",
                    "deposit_offsets": "0",
                    "mortgage_interest": "0",
                }
            ],
            "businesses": [],
            "dependent_parents": [],
            "dependent_children": [],
        },
    ],
    "provisional_paid": "0",
}


def _run_salaries_q1(content: Mapping[str, Any]) -> tuple[list[Any], Any]:
    from app.engines.salaries import calculate_salaries_tax

    amounts = calculate_salaries_tax(_SALARIES_Q1_FACTS, content)["amounts"]
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

    result = compare_personal_assessment(_PA_Q32_FACTS, content)
    scenario = next(
        item
        for item in result["scenarios"]
        if item.get("method") == "personal_assessment"
    )
    return (
        [
            scenario["before_reduction"],
            scenario["reduction"],
            scenario["final_after_reduction"],
        ],
        scenario["final_after_reduction"],
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


class ReferenceHarness:
    """C8.1 独立参考 harness：期望值＝台账已核 fixture（不读候选参数对象）。

    run_case(case_id, **kwargs)（outbound 由门禁注入计数代理；本 harness 不
    进行任何出站）；未知 caseID → KeyError（门禁按缺必需 case 隔离，C8.4）。
    """

    def run_case(self, case_id: str, **kwargs: Any) -> dict[str, Any]:
        case = _CASES.get(case_id)
        if case is None:
            raise KeyError(f"未知必需 case（未固化不得编造，C8.4）：{case_id}")
        return {
            "case_id": case_id,
            "stages": list(case["expected_stages"]),
            "output": case["expected_output"],
            "parameter_evidence_ref": case["parameter_evidence_ref"],
        }


class CandidateEngine:
    """候选引擎 seam：在候选 bundle 上运行生产引擎（静态回归区以已批准基准 pin）。

    run_case(case_id, candidate_content, **kwargs)：候选仅作为传输载体；每 case
    的 domain 数据区覆盖为该区已批准历史束（C8.3），其余候选内容原样参与计算；
    未知 caseID → KeyError（由门禁按执行失败处理）。
    """

    def run_case(
        self,
        case_id: str,
        candidate_content: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        case = _CASES.get(case_id)
        if case is None:
            raise KeyError(f"未知必需 case（未固化不得编造，C8.4）：{case_id}")
        content = copy.deepcopy(dict(candidate_content))
        domain = case["domain"]
        baseline_data = _APPROVED_BASELINE.get("data") or {}
        if isinstance(content.get("data"), dict) and domain in baseline_data:
            content["data"][domain] = copy.deepcopy(baseline_data[domain])
        stages, output = case["runner"](content)
        return {"case_id": case_id, "stages": list(stages), "output": output}


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
