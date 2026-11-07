# -*- coding: utf-8 -*-
"""REQ-2/7/8/11 首页家庭表单（004 §3/§3.7；006 §5.8 flow 26）UI 契约（httpx 层）。

REQ: REQ-2（unknown≠false/零）＋REQ-7（夫妻路径、条件 assessment、提名分离）
＋REQ-8（条件式比较边界）＋REQ-11（失效协议 UI 接线）。
规格锚点:
  - 004 §3.6/§3.7：assessment actual/PA mode/date/history 按活动消费者显示；
    actual/hypothetical 联合薪俸提名为两个独立控件（s.10(a) 派生、s.10(b) 显式）；
    child_nomination 与 spouse DDA 分离；reference_date 无首页控件（PA API-only）。
  - 005 §4.10：inactive 字段省略保持省略、不写回 none/ordinary/日期；
    活动 unknown 精确追问（field/subject_id）；未知竞争者 → best=null、
    独立已知 separate 保留。
  - 005 §13：422 questions 定位形状。
期望值来源:
  - 422 追问定位：Annex 005 §4.10 依赖表（s.29 分支 A 消费配偶 PA mode）；
    实测冻结后端响应（W2 合同；本文件记录逐字段实测值）。
  - best=null/known-separate 保留：005 §9.3/§10（unknown_competitor_blocks_best_v1）。
  - 其余为结构断言；无金额期望（金额仅作 null/字符串出现性核对）。

【被测契约】:
  - 首页 SSR 含条件 assessment 控件（已婚＋联合候选活动时显示），默认
    （单身）不显示；无 reference_date 控件、无 PA 工作台入口。
  - 配偶 AI=false 且 actual PA mode 缺失/unknown → prepare 422 精确追问
    years.<YA>.assessment.actual.pa.p2（subject_id=p2），不追问 inactive
    reference_date。
  - 两候选中 joint 未决（s.10(a) 责任人不唯一）→ 结果 partial、best=null、
    独立已知 separate 保留；record 的 canonical assessment 不补写 inactive 键。
"""

from __future__ import annotations

import json

from _helpers import fresh_if_match

_EXECUTE = "/api/v1/calc/salaries-tax"


def _couple_body(*, assessment: dict, fin_p2: dict | None = None) -> dict:
    """已婚夫妇最小 v2 请求（全年已婚未分居；p1 NAI 240000/MPF 9000）。"""
    p2 = {
        "employment_income_nai": "0",
        "mpf_mandatory_contributions": "0",
        "has_assessable_salary_income": False,
        "properties": [],
        "businesses": [],
        "deduction_items": [],
    }
    if fin_p2 is not None:
        p2 = fin_p2
    return {
        "tax_type": "salaries_tax",
        "schema_version": "2.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "subjects": [{"subject_id": "p1"}, {"subject_id": "p2"}],
            "years": {
                "2025_26": {
                    "participants": [
                        {"person_id": "p1", "role": "self"},
                        {"person_id": "p2", "role": "spouse"},
                    ],
                    "subject_facts": {},
                    "household": {
                        "marriage": [
                            {
                                "from": "2025-04-01",
                                "to": "2026-04-01",
                                "value": "married",
                                "spouse_id": "p2",
                            }
                        ],
                        "living_apart": [
                            {"from": "2025-04-01", "to": "2026-04-01", "value": False}
                        ],
                        "child_nomination": {"person_id": None, "status": "none"},
                    },
                    "relations": [],
                    "care_payments": [],
                    "claims": [],
                    "financials": {
                        "p1": {
                            "employment_income_nai": "240000",
                            "mpf_mandatory_contributions": "9000",
                            "has_assessable_salary_income": True,
                            "source_classification": {"salary": "confirmed_in_scope"},
                            "properties": [],
                            "businesses": [],
                            "deduction_items": [],
                        },
                        "p2": p2,
                    },
                    "assessment": assessment,
                    "manual_issues": [],
                    "provisional_paid": "0",
                }
            },
            "hk_presence_days": [],
            "assumptions": [],
        },
    }


def _prepare(api, body: dict):
    return api.post("/api/v1/calc/prepare", json=body, headers=fresh_if_match(api))


def test_ui_household_form_ssr_structure(ui) -> None:
    """首页 SSR：条件 assessment/提名/婚姻期间控件按 004 §3.6 locator 存在且
    默认（单身）不显示；无 reference_date 控件、无 PA 工作台；文案基线保留。"""
    page = ui.api.get("/")
    assert page.status_code == 200, page.text
    html = page.text

    # —— 004 §3.6 年度限定 locator（data-locator；默认年度 2025_26）——
    for locator in (
        "y-2025_26-marriage",
        "y-2025_26-living-apart",
        "y-2025_26-child-nomination",
        "y-2025_26-assessment-basis",
        "y-2025_26-salary-candidates",
        "y-2025_26-salary-selection",
        "y-2025_26-assessment-actual-salary-joint-elected",
        "y-2025_26-assessment-actual-salary-joint-assessed",
        "y-2025_26-assessment-actual-history",
        "y-2025_26-assessment-actual-salary-joint-nomination-status",
        "y-2025_26-assessment-actual-salary-joint-nomination-person",
        "y-2025_26-assessment-hypothetical-salary-joint-nomination-status",
        "y-2025_26-assessment-hypothetical-salary-joint-nomination-person",
        "y-2025_26-assessment-actual-pa-p1-mode",
        "y-2025_26-assessment-actual-pa-p2-mode",
        "y-2025_26-assessment-actual-pa-p2-date",
        "y-2025_26-person-p2-nai",
        "y-2025_26-person-p2-salary-ai",
        "y-2025_26-provisional-paid",
        "y-2025_26-claim-spouse-dda",
    ):
        assert f'data-locator="{locator}"' in html, (
            f"首页缺少 004 §3.6 定位控件 {locator!r}"
        )

    # —— 默认（单身）条件区不显示（SSR hidden；004 §3.7 inactive 不出现）——
    for marker in ('id="spouse-details" hidden', 'id="assessment-extras" hidden'):
        assert marker.replace('" hidden', '"') in html and "hidden" in html, (
            f"SSR 标记缺失：{marker!r}"
        )
    import re

    for block_id in ("spouse-details", "assessment-extras", "nom-actual-group",
                     "nom-hypo-group", "child-nomination-group", "pa-mode-block"):
        m = re.search(rf'<[^>]+id="{block_id}"[^>]*>', html)
        assert m, f"缺少条件块 {block_id}"
        assert "hidden" in m.group(0), (
            f"{block_id} 默认（单身/未申索）必须 hidden（inactive 不显示）"
        )

    # —— PA API-only：首页无 reference_date 控件、无 PA 计算入口（004 §3.6）——
    assert "reference_date" not in html, "首页不得出现 reference_date 控件（PA 仅 API）"
    assert "reference-date" not in html
    assert "/api/v1/calc/personal-assessment" not in html, (
        "首页不得携带 PA 执行入口（本阶段 PA API-only）"
    )

    # —— s.10(a)/(b) 与 child nomination 文案基线（004 §3.1/§3.3/§6）——
    assert "s.10(a)" in html and "s.10(b)" in html, "提名控件须说明 s.10(a) 派生/s.10(b) 显式"
    assert "整组" in html, "子女申索须为整组提名文案"
    # 结果期文案由运行时 steps 驱动：静态结构（非脚本/注释）不得占位该文案。
    static_html = re.sub(r"<script\b.*?</script>", "", html, flags=re.DOTALL)
    static_html = re.sub(r"<!--.*?-->", "", static_html, flags=re.DOTALL)
    assert "条件式比较" not in static_html, (
        "「条件式比较」须仅由运行时 steps 渲染，不得在静态结果结构占位"
    )
    # —— null≠0 结果口径提示（004 §5）——
    assert "未能估算" in html, "结果区须携带「未能估算」缺组件口径"
    # —— 模型文本非权威（C3.6 文案保留）——
    assert "未确认" in html


def test_ui_conditional_assessment_unknown_keeps_known_separate(ui) -> None:
    """flow 26：活动消费者缺/unknown 精确追问；未知竞争者不宣告不合法、
    已知 separate 保留、best=null；inactive 字段不写回默认值。"""
    api = ui.api

    # —— (a) 配偶 AI=false、s.29 分支 A 消费者激活而 actual.pa.p2 缺键 → 422 精确追问 ——
    body_missing = _couple_body(assessment={
        "basis": "actual",
        "candidates": ["salary_separate"],
        "selection": "salary_separate",
        "actual": {"salary_joint_elected": False},
    })
    r = _prepare(api, body_missing)
    assert r.status_code == 422, (
        f"活动 PA mode 缺键须 422 needs_input，实际 {r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    assert payload["error"]["code"] == "E_ELIGIBILITY_UNKNOWN"
    assert "prepared_id" not in payload
    questions = payload.get("questions")
    assert isinstance(questions, list) and questions
    assert any(
        q.get("field") == "years.2025_26.assessment.actual.pa.p2"
        and q.get("subject_id") == "p2"
        and q.get("year") == "2025_26"
        for q in questions
    ), f"须精确追问 actual.pa.p2（subject_id=p2），实际：{questions!r}"
    assert not any("reference_date" in str(q.get("field", "")) for q in questions), (
        "inactive reference_date 不得被追问（005 §4.10；PA 主页无该消费者）"
    )

    # —— (a2) 显式 mode unknown 同样追问（unknown≠none，不写回默认）——
    body_unknown = _couple_body(assessment={
        "basis": "actual",
        "candidates": ["salary_separate"],
        "selection": "salary_separate",
        "actual": {"salary_joint_elected": False, "pa": {"p2": {"mode": "unknown"}}},
    })
    r2 = _prepare(api, body_unknown)
    assert r2.status_code == 422, (
        f"mode=unknown 须仍追问（unknown 不是已知无选择），实际 {r2.status_code}"
    )
    assert any(
        q.get("field") == "years.2025_26.assessment.actual.pa.p2"
        for q in r2.json().get("questions", [])
    )

    # —— (b) 两候选：joint 未决（s.10(a) 责任人不唯一）→ 不宣告不合法；
    #    独立已知 separate 保留；best=null ——
    both_ai_fin = {
        "employment_income_nai": "100000",
        "mpf_mandatory_contributions": "5000",
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
    }
    body_ambiguous = _couple_body(
        assessment={
            "basis": "hypothetical",
            "candidates": ["salary_separate", "salary_joint"],
            "selection": "best",
        },
        fin_p2=both_ai_fin,
    )
    body_ambiguous["input"]["years"]["2025_26"]["financials"]["p1"] = dict(both_ai_fin)
    r3 = _prepare(api, body_ambiguous)
    assert r3.status_code == 201, (
        f"joint 未决但 separate 独立已知：prepare 须 201（不整单 422），"
        f"实际 {r3.status_code}：{r3.text[:300]!r}"
    )
    prepared = r3.json()
    r_confirm = api.post("/api/v1/calc/confirm", json={
        "prepared_id": prepared["prepared_id"],
        "canonical_input_hash": prepared["input_hash"],
        "expected_binding": prepared["expected_binding"],
        "acknowledge": True,
    })
    assert r_confirm.status_code == 200, r_confirm.text
    r_exec = api.post(_EXECUTE, json={"confirmation_id": r_confirm.json()["confirmation_id"]})
    assert r_exec.status_code == 200, r_exec.text
    result = r_exec.json()
    assert result["status"] == "partial", (
        f"未知竞争者→非 complete；实际 {result['status']!r}"
    )
    amounts = result["amounts"]
    assert amounts["final_after_reduction"] is None, (
        f"best 未决时顶层 final 为 null（不虚构确定税额），实际 {amounts!r}"
    )
    selection_steps = [
        s for s in result.get("steps", [])
        if isinstance(s, dict) and s.get("kind") == "selection"
    ]
    assert selection_steps, "结果须含 selection 步骤（候选检查记录）"
    detail = selection_steps[-1]["detail"]
    assert detail["best"] is None, f"未知竞争者存在 → best=null，实际 {detail!r}"
    checks = {c["candidate_id"]: c for c in detail["candidate_checks"]}
    assert checks["salary_joint"]["admission"] == "unresolved", (
        f"未知 joint 须 unresolved（不宣告不合法），实际 {checks!r}"
    )
    assert checks["salary_separate"]["admission"] == "eligible", (
        f"独立已知 separate 保留（eligible），实际 {checks!r}"
    )
    assert checks["salary_separate"]["separate_final"] == "0", (
        "separate 已知组件照常输出（本例双方各 0 税）"
    )

    # —— (b2) record 不写回 inactive 键（actual/nomination/reference_date 缺席保持缺席）——
    record = api.get(f"/api/v1/records/{result['record_id']}").json()["record"]
    assessment = record["input"]["years"]["2025_26"]["assessment"]
    assert set(assessment.keys()) == {"basis", "candidates", "selection"}, (
        f"record assessment 不得补写 inactive 键，实际：{sorted(assessment.keys())}"
    )
