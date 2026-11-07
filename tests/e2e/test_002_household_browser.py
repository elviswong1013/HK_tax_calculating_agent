# -*- coding: utf-8 -*-
"""REQ-2/7/11 首页家庭表单浏览器 e2e（002 P2 W3 flows 25/27；004 §3/§3.7/§3.8）。

本文件为需要 Chromium 的独立 e2e 标记文件（pytest.mark.e2e；共享隔离/启动见
tests/e2e/conftest.py 的 `e2e_isolation`，006 §6 ①–⑤；Chromium 缺失＝BLOCKED）。

【被测契约】:
  - flow 25 `test_002_single_salary_no_inactive_assessment_controls`：
    合成 Q1（2025_26、NAI 240000、MPF 9000、全年未婚、仅 salary_separate）
    在 actual/hypothetical 两种 basis 下：页面/wire 不出现也不组装 actual
    object、PA mode/date/history、reference_date；完整 prepare→人工确认→
    execute 得 final 940、partial、next/balance=null（页面显示「未能估算」）。
  - flow 27 `test_002_same_ya_sequential_spouse_history_manual`：
    bounded-closure §3.1 同 YA 顺序配偶 fixture 经真实页面录入；
    p3 为已注册历史对象但不是第三 actor；joint 走 S7 manual → prepare 422、
    错误摘要定位 `years.2025_26.household.marriage`（year=2025_26、
    subject_id=p1）、不产生貌似完整的家庭税额（无结果区/无 prepared）。
期望值来源:
  - 940/partial/null：PAM39 Q1（T2 台账 1.13）＋Annex 005 §5.3/§14 冻结锚。
  - S7 locator/reason：Annex 005 §5.4/§13（计算标签 S7_SEQUENTIAL_SPOUSES）。
"""

from __future__ import annotations

import json
import re

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def e2e_expect_timeout() -> int:
    """完整链含多次真实 API 往返，超时 10s。"""
    return 10000


# ------------------------------------------------------------------ 页面驱动
def _editor_state(page) -> str:
    return page.evaluate("document.getElementById('calc-form').dataset.editorState")


def _wait_editor_idle(page) -> None:
    """显式同步点：轮询编辑器队列回 idle（轮询可观测状态，不用固定 sleep 猜测，
    006 §6.6）；每次迭代经 wait_for_timeout 泵事件循环。"""
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _editor_state(page) == "idle":
            return
        page.wait_for_timeout(50)
    raise AssertionError(
        f"编辑器 mutation 队列未回到 idle（invalidate ACK 未完成）：{_editor_state(page)!r}"
    )


def _capture_prepare(page) -> list:
    """拦截并记录 prepare 请求体（放行；wire 断言用）。"""
    captured: list = []

    def _route(route):
        try:
            captured.append(json.loads(route.request.post_data or "{}"))
        except json.JSONDecodeError:
            captured.append({"__raw__": route.request.post_data})
        route.continue_()

    page.route("**/api/v1/calc/prepare", _route)
    return captured


def _submit_and_confirm(page) -> None:
    """人工确认链：表单主按钮 → 摘要弹窗 → 弹窗内确认（两段均人工点击）。"""
    from playwright.sync_api import expect

    page.get_by_role("button", name="确认资料并计算").first.click()
    dialog = page.locator("#confirm-dialog[role=dialog]")
    expect(dialog, "提交后须出现确认摘要弹窗（§6/§12.2）").to_be_visible()
    page.locator("#confirm-submit").click()


def _fill_q1(page, *, basis: str) -> None:
    page.get_by_label("应课税收入").fill("240000")
    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page.locator("#provisional-paid").fill("0")
    page.locator("#assessment-basis").select_option(basis)


# ------------------------------------------------------------------ flow 25
def test_002_single_salary_no_inactive_assessment_controls(page, base_url) -> None:
    """最简单一薪俸（两 basis）：页面/wire 均无 inactive assessment 字段；
    final 940、partial、next/balance null。"""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 800})
    captured = _capture_prepare(page)
    page.goto(base_url + "/")
    _wait_editor_idle(page)

    # —— 页面层：单身默认下条件 assessment 控件不显示（004 §3.7）——
    expect(page.locator("#assessment-extras")).to_be_hidden()
    expect(page.locator("#spouse-details")).to_be_hidden()
    expect(page.locator("#nom-actual-group")).to_be_hidden()
    expect(page.locator("#nom-hypo-group")).to_be_hidden()
    expect(page.locator("#child-nomination-group")).to_be_hidden()

    for basis in ("actual", "hypothetical"):
        _fill_q1(page, basis=basis)
        _wait_editor_idle(page)
        _submit_and_confirm(page)

        result = page.locator("#result-section")
        expect(result, f"basis={basis}：结果区须出现").to_be_visible()
        expect(page.locator("#result-badge")).to_have_text("部分结果")
        amounts = page.locator("#result-amounts").inner_text()
        assert re.search(r"最终税款\s*\n?940", amounts) or "940" in amounts, (
            f"basis={basis}：最终税款 940 须直读展示，实际：{amounts!r}"
        )
        assert "3940" in amounts and "3000" in amounts, (
            f"basis={basis}：宽减前 3940／宽减 3000 须展示，实际：{amounts!r}"
        )
        # null≠0：下年度暂缴税与结欠显示「未能估算」，不显示 $0
        assert amounts.count("未能估算") >= 2, (
            f"basis={basis}：next/balance 为 null 须显示「未能估算」（≥2 处），"
            f"实际：{amounts!r}"
        )
        assert not re.search(r"下年度暂缴税\s*\n?0\s*港元", amounts), (
            "next_provisional=null 不得显示为 0（null≠0）"
        )

    # —— wire 层：两次 prepare 的 assessment 恰为 {basis,candidates,selection} ——
    assert len(captured) == 2, f"应恰好两次 prepare（两 basis），实际 {len(captured)}"
    for body, basis in zip(captured, ("actual", "hypothetical")):
        assert body["schema_version"] == "2.0.0"
        assert body["tax_type"] == "salaries_tax"
        year_block = body["input"]["years"]["2025_26"]
        assessment = year_block["assessment"]
        assert set(assessment.keys()) == {"basis", "candidates", "selection"}, (
            f"basis={basis}：wire 不得组装 actual/PA mode/date/history/"
            f"reference_date，实际键集 {sorted(assessment.keys())}"
        )
        assert assessment["basis"] == basis
        assert assessment["candidates"] == ["salary_separate"]
        blob = json.dumps(body, ensure_ascii=False)
        assert "reference_date" not in blob
        assert "salary_joint_nomination" not in blob
        finance = year_block["financials"]["p1"]
        assert finance["employment_income_nai"] == "240000"
        assert finance["mpf_mandatory_contributions"] == "9000"
        assert year_block["provisional_paid"] == "0"
        assert body["input"]["subjects"] == [{"subject_id": "p1"}], (
            "单身路径不得虚构配偶 subject"
        )


# ------------------------------------------------------------------ flow 27
def _set_marriage_row(page, index: int, *, from_: str, to: str, value: str,
                      spouse: str | None = None) -> None:
    row = page.locator("#marriage-periods .marriage-row").nth(index)
    row.get_by_label("婚姻期间开始（含）").fill(from_)
    row.get_by_label("婚姻期间结束（不含）").fill(to)
    row.get_by_label("该期间婚姻状态").select_option(value)
    if spouse is not None:
        row.get_by_label("该期间配偶（会话内已登记人物）").select_option(spouse)


def _add_living_apart_row(page, *, from_: str, to: str, value: str) -> None:
    page.locator("#add-living-apart-period").click()
    row = page.locator("#living-apart-periods .living-apart-row").last
    row.get_by_label("分居说明期间开始（含）").fill(from_)
    row.get_by_label("分居说明期间结束（不含）").fill(to)
    row.get_by_label("该期间是否分居").select_option(value)


def test_002_same_ya_sequential_spouse_history_manual(page, base_url) -> None:
    """同 YA 顺序配偶变化（p3 历史对象）：S7 manual——错误定位婚姻期间编辑器、
    不产生家庭总税/确认能力；p3 不成为第三 actor（wire 断言）。"""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 800})
    captured = _capture_prepare(page)
    page.goto(base_url + "/")
    _wait_editor_idle(page)

    # —— 本人收入（fixture：500000/18000/已缴 0）——
    page.get_by_label("应课税收入").fill("500000")
    page.get_by_label("强积金（MPF）强制性供款").fill("18000")
    page.locator("#provisional-paid").fill("0")

    # —— 已婚 → 配偶区展开；配偶 AI＝有＋200000/18000 ——
    page.locator("#married-status").select_option("married")
    expect(page.locator("#spouse-details")).to_be_visible()
    page.locator("#spouse-salary-ai").select_option("true")
    page.get_by_label("配偶薪俸净入息（NAI）").fill("200000")
    page.get_by_label("配偶 MPF 强制性供款").fill("18000")

    # —— 历史关系对象 p3（已注册、非本年度参与人）——
    page.locator("#add-historical-subject").click()
    expect(page.locator("#historical-subjects input")).to_be_visible()

    # —— 婚姻期间：p3（4/1–8/1）→ 未婚（8/1–9/1）→ p2（9/1–4/1）——
    _set_marriage_row(page, 0, from_="2025-04-01", to="2025-08-01",
                      value="married", spouse="p3")
    page.locator("#add-marriage-period").click()
    _set_marriage_row(page, 1, from_="2025-08-01", to="2025-09-01",
                      value="unmarried")
    page.locator("#add-marriage-period").click()
    _set_marriage_row(page, 2, from_="2025-09-01", to="2026-04-01",
                      value="married", spouse="p2")

    # —— 分居说明：两段已婚窗口均明确未分居 ——
    _add_living_apart_row(page, from_="2025-04-01", to="2025-08-01", value="false")
    _add_living_apart_row(page, from_="2025-09-01", to="2026-04-01", value="false")

    # —— 评税方式：hypothetical；仅联合候选；提名 p1（s.10(b) 显式）——
    page.locator("#assessment-basis").select_option("hypothetical")
    page.locator("#candidate-separate").uncheck()
    page.locator("#candidate-joint").check()
    page.locator("#salary-selection").select_option("salary_joint")
    extras = page.locator("#assessment-extras")
    expect(extras, "联合候选活动时实际评税事实区须显示").to_be_visible()
    page.locator("#actual-joint-elected").select_option("false")
    page.locator("#actual-joint-assessed").select_option("false")
    page.locator("#actual-history").select_option("ordinary")
    page.locator("#self-pa-mode").select_option("none")
    page.locator("#spouse-pa-mode").select_option("none")
    page.locator("#nom-hypo-status").select_option("named")
    page.locator("#nom-hypo-person").select_option("p1")

    _wait_editor_idle(page)
    _submit_and_confirm(page)

    # —— S7 manual：422 needs_input；错误摘要定位婚姻期间编辑器 ——
    summary = page.locator("#error-summary")
    expect(summary, "S7 须产生可定位的缺资料/人工核实摘要").to_be_visible()
    summary_text = summary.inner_text()
    assert "S7_SEQUENTIAL_SPOUSES" in summary_text, (
        f"错误摘要须含 S7 原因标签（计算标签），实际：{summary_text!r}"
    )
    # 不产貌似完整的家庭税额：无结果区、无记录
    expect(page.locator("#result-section")).to_be_hidden()
    assert page.locator("#records-list .record-card").count() == 0, (
        "S7 manual 不得产生计算记录"
    )

    # —— 摘要链接：展开折叠组并聚焦婚姻期间编辑器内控件（004 §4/§7）——
    summary.locator("a").first.click()
    active_inside = page.evaluate(
        "document.getElementById('marriage-periods').contains(document.activeElement)"
    )
    assert active_inside, "定位链接须聚焦婚姻期间编辑器内控件（years.2025_26.household.marriage）"

    # —— wire：三段婚姻期间原样提交；p3 已注册但非 participant（不虚构第三 actor）——
    assert captured, "须有一次 prepare"
    body = captured[-1]
    year_block = body["input"]["years"]["2025_26"]
    assert year_block["household"]["marriage"] == [
        {"from": "2025-04-01", "to": "2025-08-01", "value": "married", "spouse_id": "p3"},
        {"from": "2025-08-01", "to": "2025-09-01", "value": "unmarried"},
        {"from": "2025-09-01", "to": "2026-04-01", "value": "married", "spouse_id": "p2"},
    ], f"婚姻期间须按编辑原样提交，实际：{year_block['household']['marriage']!r}"
    subjects = body["input"]["subjects"]
    assert {"subject_id": "p3"} in subjects, "p3 须已注册为 subject"
    assert year_block["participants"] == [
        {"person_id": "p1", "role": "self"},
        {"person_id": "p2", "role": "spouse"},
    ], "p3 不得成为本年度 participant（不虚构第三 actor）"
    assert year_block["financials"].get("p3") is None, (
        "p3 不得被当作零收入 participant 填入 financials"
    )
    assert year_block["assessment"]["hypothetical_salary_joint_nomination"] == {
        "status": "named", "person_id": "p1",
    }
