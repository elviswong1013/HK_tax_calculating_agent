# -*- coding: utf-8 -*-
"""REQ-11/13 records 客户端动作与 proposal 采用浏览器 e2e（002 P2 W4）。

本文件为需要 Chromium 的 e2e 标记文件（pytest.mark.e2e；Chromium 不可用＝
BLOCKED，skip 不计通过；共享隔离/启动见 tests/e2e/conftest.py，006 §6 ①–⑤）。

【被测契约】
  1. `test_002_records_view_download_print_replay`（REQ-13；C3.1/C3.3/C3.4/
     Annex A §6.6/§12.1–§12.2）：
     完成一次真实首页计算后，实际点击记录卡的「查看／下载／打印／原包重放」：
     - 查看：内联展开金额分项（最终税款 940）与计算步骤；
     - 下载：attachment＋no-store（C5.7 安全层按路由注入），内容为全量记录
       （core_hash 存在）；
     - 打印：新窗口打开打印版（含记录号、金额分项、免责声明，无交互控件）；
     - 重放：展示「历史估算，非当前重新评估」与原记录金额，不新增记录。
  2. `test_002_proposal_diff_adopt_invalidate_manual_prepare`（REQ-11/005 §16）：
     在真实聊天 consent preview＋grant 后，`/api/v1/ai/chat` 由浏览器路由以
     冻结的 tool_results 响应提供（不触达服务器模型接缝；本测试验证客户端
     JS）：结构化提议渲染本地差异 → 点击采用 → 填表 → 立即失效
     （invalidate 闭合两键 body）→ 不自动 prepare → 用户手动确认后才 prepare
     ＋confirm＋execute → final 940。

【证据边界】
  - records 四个动作均为真实按钮/链接点击，服务端端点与响应为真实 loopback。
  - proposal 的服务端 AI 工具层由 tests/ai 与 tests/ui 覆盖；本测试替身仅提供
    已冻结的 wire `tool_results`，不改变客户端渲染/采用/失效路径；e2e 假模型
    接缝 `send_calls` 必须为 0（浏览器未向服务器发送 chat）。
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


def _wait_idle(page) -> None:
    """显式同步点：轮询编辑器队列回 idle（轮询可观测状态，不用固定 sleep）。"""
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _editor_state(page) == "idle":
            return
        page.wait_for_timeout(50)
    raise AssertionError(
        f"编辑器 mutation 队列未回到 idle：{_editor_state(page)!r}"
    )


class _Counter:
    """放行并计数/捕获请求体的路由。"""

    def __init__(self) -> None:
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        route.continue_()

    @property
    def count(self) -> int:
        return len(self.requests)

    def bodies(self) -> list:
        out = []
        for request in self.requests:
            try:
                out.append(json.loads(request.post_data or "{}"))
            except json.JSONDecodeError:
                out.append({"__raw__": request.post_data})
        return out


def _submit_and_confirm(page) -> None:
    """人工确认链：表单主按钮 → 摘要弹窗 → 弹窗内确认（两段均人工点击）。"""
    from playwright.sync_api import expect

    page.get_by_role("button", name="确认资料并计算").first.click()
    dialog = page.locator("#confirm-dialog[role=dialog]")
    expect(dialog, "提交后须出现确认摘要弹窗（§6/§12.2）").to_be_visible()
    page.locator("#confirm-submit").click()


def _fill_q1(page) -> None:
    page.get_by_label("应课税收入").fill("240000")
    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page.locator("#provisional-paid").fill("0")


def _record_id_from_card(page) -> str:
    text = page.locator("#records-list .record-card").first.inner_text()
    match = re.search(r"r-[0-9a-f]{8,}", text)
    assert match, f"记录卡须显示服务器 record_id，实际：{text!r}"
    return match.group(0)


# ------------------------------------------------------------------ records
def test_002_records_view_download_print_replay(page, base_url) -> None:
    """计算后实际点击查看/下载/打印/重放：客户端 JS 全链可执行。"""
    from playwright.sync_api import expect

    page_errors: list[str] = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    download_headers: list[dict] = []

    def _download_route(route) -> None:
        response = route.fetch()
        download_headers.append(dict(response.headers))
        route.fulfill(response=response)

    page.route("**/api/v1/records/*/download", _download_route)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    _fill_q1(page)
    _wait_idle(page)
    _submit_and_confirm(page)

    expect(page.locator("#result-section"), "计算须得结果").to_be_visible()
    expect(page.locator("#result-badge")).to_have_text("部分结果")
    card = page.locator("#records-list .record-card")
    expect(card, "execute 成功须新增本次结果记录卡（§12.6）").to_have_count(1)
    expect(card).to_contain_text("本次结果")
    record_id = _record_id_from_card(page)

    # —— 查看：内联展开金额分项＋步骤（不由前端重算）——
    page.get_by_role("button", name="查看 " + record_id).click()
    detail = card.locator(".record-detail")
    expect(detail).to_be_visible()
    detail_text = detail.inner_text()
    assert "最终税款" in detail_text and "940" in detail_text, (
        f"查看须直读展示最终税款 940，实际：{detail_text!r}"
    )
    assert "计算步骤" in detail_text, "查看须展示服务器 steps"

    # —— 下载：真实链接点击 → attachment（C5.7）＋no-store → 全量记录内容 ——
    with page.expect_download() as download_info:
        card.get_by_role("link", name="下载 " + record_id).click()
    download = download_info.value
    assert download.suggested_filename.endswith(".json"), (
        f"下载须为 JSON 文件，实际 {download.suggested_filename!r}"
    )
    assert len(download_headers) == 1, "下载链接须恰好触发一次记录端点请求"
    headers = download_headers[0]
    assert headers.get("content-disposition", "").startswith("attachment"), (
        f"下载须为 attachment（C5.7 安全层按路由注入），实际 "
        f"{headers.get('content-disposition')!r}"
    )
    assert headers.get("cache-control") == "no-store", (
        f"下载须 no-store，实际 {headers.get('cache-control')!r}"
    )
    with open(download.path(), "rb") as handle:
        downloaded = json.loads(handle.read().decode("utf-8"))
    assert downloaded["record_id"] == record_id
    assert downloaded["record"]["amounts"]["final_after_reduction"] == "940"
    assert str(downloaded.get("core_hash", "")).startswith("sha256:"), (
        "下载内容须含 core_hash"
    )

    # —— 打印：POST /report/print → 新窗口写入打印版（无交互控件）——
    with page.expect_popup() as popup_info:
        card.get_by_role("button", name="打印 " + record_id).click()
    popup = popup_info.value
    popup.wait_for_selector("h1", timeout=10000)
    assert record_id in popup.locator("h1").inner_text(), (
        "打印版标题须含记录号（Annex A §6.6）"
    )
    print_text = popup.locator("body").inner_text()
    assert "最终税款（final_after_reduction）：940 港元" in print_text, (
        f"打印版须直读金额分项，实际：{print_text[:400]!r}"
    )
    assert "免责声明" in print_text
    assert popup.locator("button, input, select, textarea").count() == 0, (
        "打印版不得含交互控件（Annex A §6.6）"
    )
    popup.close()

    # —— 重放：原包重放 → 历史标示＋原记录金额；不新增记录 ——
    page.get_by_role("button", name="原包重放 " + record_id).click()
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("历史估算，非当前重新评估")
    replay_text = detail.inner_text()
    assert "940" in replay_text, f"重放须展示原记录金额 940，实际：{replay_text!r}"
    expect(card).to_have_count(1)
    assert not page_errors, f"记录区动作不得产生页面 JS 异常：{page_errors!r}"


# ------------------------------------------------------------------ proposal
def _proposal_chat_route(page, *, proposal_input: dict):
    """以冻结 tool_results 响应替身承接浏览器 chat 请求（不触达服务器模型）。"""
    captured: list = []

    def _handler(route) -> None:
        captured.append(json.loads(route.request.post_data or "{}"))
        body = {
            "consent_id": "proxy-consent",
            "sent_bytes": 1234,
            "status": "completed",
            "content": "已按你的要求整理薪俸资料，请核对差异。",
            "unconfirmed": True,
            "tool_results": [
                {
                    "id": "call_proposal_1",
                    "name": "propose_input",
                    "outcome": "executed",
                    "note": "read_only_checked",
                    "needs_confirmation": False,
                    "error": None,
                    "result": {
                        "proposal": {
                            "tax_type": "salaries_tax",
                            "schema_version": "2.0.0",
                            "input": proposal_input,
                        },
                        "canonical_input_hash": "sha256:" + "0" * 64,
                        "expected_binding": {
                            "bundle_id": "test",
                            "bundle_hash": "sha256:" + "0" * 64,
                            "rules_schema_version": "2.0.0",
                            "engine_version": "1.0.0",
                        },
                        "status": "checked",
                        "questions": [],
                        "missing_components": [],
                    },
                }
            ],
        }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(body, ensure_ascii=False),
        )

    page.route("**/api/v1/ai/chat", _handler)
    return captured


def _proposal_input() -> dict:
    """v2 单薪俸提议（与当前表单不同：240000/9000；结构经真实校验器核验）。"""
    return {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1"}],
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
                    "child_nomination": {"status": "none", "person_id": None},
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
                    }
                },
                "assessment": {
                    "basis": "actual",
                    "candidates": ["salary_separate"],
                    "selection": "salary_separate",
                },
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }


def test_002_proposal_diff_adopt_invalidate_manual_prepare(
    page, base_url, e2e_isolation
) -> None:
    """proposal 差异→采用→填表→invalidate→不自动 prepare→手动 prepare。"""
    from playwright.sync_api import expect

    invalidate = _Counter()
    prepare = _Counter()
    page.route("**/api/v1/calc/invalidate", invalidate.handler)
    page.route("**/api/v1/calc/prepare", prepare.handler)
    chat_requests = _proposal_chat_route(page, proposal_input=_proposal_input())

    page_errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base_url + "/")
    _wait_idle(page)

    # 当前表单与提议不同（用于差异展示；adopt 后应被覆盖）。
    page.get_by_label("应课税收入").fill("100000")
    page.get_by_label("强积金（MPF）强制性供款").fill("5000")
    page.locator("#provisional-paid").fill("0")
    _wait_idle(page)
    invalidate_baseline = invalidate.count
    assert prepare.count == 0, "编辑不得自动 prepare"

    # —— 发送 → 预览 → 单次授权 → chat 替身返回结构化提议 ——
    page.get_by_label("你的问题").fill("請把薪俸資料改為 240000／9000")
    page.locator("#chat-send").click()
    dialog = page.locator("#consent-dialog[role=dialog]")
    expect(dialog, "发送须先经冻结预览（§7/§12.5）").to_be_visible()
    page.locator("#consent-allow").click()

    tool_entry = page.locator("#chat-messages li.chat-entry-tool")
    expect(tool_entry).to_have_count(1)
    expect(tool_entry).to_contain_text("propose_input")
    diff = tool_entry.locator("ul.proposal-diff")
    assert not page_errors, f"proposal 渲染产生页面 JS 异常：{page_errors!r}"
    assert not console_errors, f"proposal 渲染 console.error：{console_errors!r}"
    expect(diff).to_be_visible()
    diff_text = diff.inner_text()
    assert "薪俸净入息（NAI）" in diff_text and "240000" in diff_text, (
        f"差异须标出 NAI 变更，实际：{diff_text!r}"
    )
    assert "MPF 强制性供款" in diff_text and "9000" in diff_text, (
        f"差异须标出 MPF 变更，实际：{diff_text!r}"
    )
    assert "→" in diff_text, "差异须呈现 before → after"

    # AI 端点：preview/grant 真实到服务器；chat 仅浏览器替身，服务器模型零调用。
    assert len(chat_requests) == 1, "浏览器恰发出一次 chat 请求"
    assert chat_requests[0].get("consent_id"), "chat 请求须带有效 consent_id"
    assert isinstance(chat_requests[0].get("messages"), list)
    assert e2e_isolation.model.send_calls == 0, (
        "本测试不得触达服务器模型接缝（浏览器已以冻结响应承接 chat）"
    )

    # —— 采用：填表＋立即失效（invalidate）＋不自动 prepare ——
    tool_entry.get_by_role(
        "button", name="采用提议（填入表单，不自动提交）"
    ).click()
    _wait_idle(page)
    assert invalidate.count == invalidate_baseline + 1, (
        f"采用须恰好触发一次 invalidate，实际 {invalidate.count - invalidate_baseline}"
    )
    adopt_body = invalidate.bodies()[-1]
    assert set(adopt_body) == {
        "expected_session_epoch",
        "expected_input_revision",
    }, f"invalidate 须为闭合两键 body，实际键集 {sorted(adopt_body)!r}"
    assert prepare.count == 0, "采用/失效后不得自动 prepare"

    assert page.get_by_label("应课税收入").input_value() == "240000"
    assert page.get_by_label("强积金（MPF）强制性供款").input_value() == "9000"
    assert page.locator("#provisional-paid").input_value() == "0"
    assert page.locator("#married-status").input_value() == "single"
    assert page.locator("#assessment-basis").input_value() == "actual"
    expect(page.locator("#records-list .record-card")).to_have_count(0)

    # —— 手动 prepare：用户显式确认后才提交；If-Match 仅 prepare 携带 ——
    _submit_and_confirm(page)
    expect(page.locator("#result-section"), "手动确认后须得到结果").to_be_visible()
    expect(page.locator("#result-badge")).to_have_text("部分结果")
    amounts = page.locator("#result-amounts").inner_text()
    assert "940" in amounts, f"采用后手动 prepare 须得 final 940，实际：{amounts!r}"
    assert prepare.count == 1, f"仅手动一次 prepare，实际 {prepare.count}"
    prepare_body = prepare.bodies()[0]
    finance = prepare_body["input"]["years"]["2025_26"]["financials"]["p1"]
    assert finance["employment_income_nai"] == "240000"
    assert finance["mpf_mandatory_contributions"] == "9000"
    assert prepare_body["schema_version"] == "2.0.0"
    if_match = prepare.requests[0].headers.get("if-match")
    assert if_match and if_match.startswith('"edit-'), (
        f"prepare 须显式携带 If-Match（实际 {if_match!r}）"
    )
    assert invalidate.requests[0].headers.get("if-match") is None, (
        "invalidate 不得携带 If-Match（闭合两键 body 已够）"
    )
    expect(page.locator("#records-list .record-card")).to_have_count(1)
    assert not page_errors, f"proposal 采用/手动重走流程不得产生 JS 异常：{page_errors!r}"
