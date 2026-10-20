# -*- coding: utf-8 -*-
"""REQ-11 单飞队列七场景浏览器 e2e（002 P2 W3；004 §7/§8.18–24；005 §12；006 §6.7）。

本文件为需要 Chromium 的独立 e2e 标记文件（pytest.mark.e2e；共享隔离/启动见
tests/e2e/conftest.py 的 `e2e_isolation`，006 §6 ①–⑤；Chromium 缺失＝BLOCKED）。

【被测契约】（全部显式同步点：路由闸持有真实响应、轮询可观测状态；不用 sleep
推测时序）:
  1. test_ui_singleflight_rapid_edits_delayed_invalidate_ack：
     延迟 invalidate ACK 期间快速编辑——仅一个 mutation 在途、generation 持续
     递增、草稿合并保留；ACK（即使 generation 已变）推进内部已确认版本、不回写
     草稿、不自动 prepare。
  2. test_ui_singleflight_edit_during_prepare_error_ack_reordering：
     prepare 在途编辑→等 ACK（含版本的 422）后才排队 invalidate；陈旧 payload
     丢弃；403 无版本头不推进、进入冲突态。
  3. test_ui_singleflight_cross_tab_409_explicit_resync：
     真实双标签页 409→保留草稿、禁用能力；Current 头不自动接纳；仅用户点
     「重新同步」读取 runtime version，之后仍须手动 prepare/confirm。
  4. test_ui_singleflight_clear_session_epoch_drops_late_replies：
     clear=epoch 边界（ACK 带 epoch+1/rev0）；旧 epoch 迟到响应整体丢弃；
     ACK 缺失须显式重同步；不恢复旧草稿。
  5. test_ui_salary_nomination_actual_hypothetical_and_conditional_copy：
     actual/hypothetical 提名独立控件独立 wire 字段、互不复制；未接纳 joint 的
     条件式金额只带「条件式比较，非已接纳税额」，不进入当前税额/best。
  6. test_ui_child_dda_nomination_not_spouse_dda：
     无基本 child claim 的 child-DDA 仍校验整组提名；spouse DDA 不显示/不校验
     子女提名。
  7. test_ui_singleflight_focus_consent_no_auto_outbound：
     键盘编辑/显式重同步焦点可见且草稿不丢；dirty/conflict 下
     confirm/calculate/send 禁用；零自动 consent 消费/AI 外发。
期望值来源: 004 §7（ACK/队列语义）＋005 §12.2/§12.3（头契约/清空边界）＋
T3 手算（38500/47000；005 §9.1.3）。
"""

from __future__ import annotations

import json
import time

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def e2e_expect_timeout() -> int:
    return 10000


# ------------------------------------------------------------------ 同步助手
def _wait_for(predicate, message: str, timeout: float = 10.0, page=None) -> None:
    """显式同步点：轮询可观测状态（每迭代经 wait_for_timeout 泵事件循环）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if page is not None:
            page.wait_for_timeout(50)
        else:
            time.sleep(0.05)
    raise AssertionError(f"同步点超时：{message}")


def _state(page) -> str:
    return page.evaluate("document.getElementById('calc-form').dataset.editorState")


def _acked(page) -> tuple[str, str]:
    return (
        page.evaluate("document.getElementById('calc-form').dataset.ackedEpoch"),
        page.evaluate("document.getElementById('calc-form').dataset.ackedRevision"),
    )


def _generation(page) -> int:
    return int(page.evaluate("document.getElementById('calc-form').dataset.generation"))


def _wait_idle(page) -> None:
    _wait_for(lambda: _state(page) == "idle", "编辑器队列回到 idle", page=page)


class _Gate:
    """路由闸：立即转发服务器（route.fetch）但暂缓 fulfill，由测试显式放行。

    服务器在 fetch 时即处理请求（真实 ACK 头保留在响应里）；页面仅在放行时
    收到响应——即「迟到/延迟 ACK」的确定性构造（006 §6.6 显式同步点）。
    """

    def __init__(self) -> None:
        self.held: list = []
        self.requests: list = []

    def handler(self, route) -> None:
        self.requests.append(route.request)
        response = route.fetch()
        self.held.append((route, response))

    def release_all(self) -> None:
        held = self.held[:]
        self.held.clear()
        for route, response in held:
            route.fulfill(response=response)

    @property
    def count(self) -> int:
        return len(self.requests)


class _Counter:
    """放行并计数的路由。"""

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
    from playwright.sync_api import expect

    page.get_by_role("button", name="确认资料并计算").first.click()
    dialog = page.locator("#confirm-dialog[role=dialog]")
    expect(dialog, "提交后须出现确认摘要弹窗").to_be_visible()
    page.locator("#confirm-submit").click()


def _fill_q1(page) -> None:
    page.get_by_label("应课税收入").fill("240000")
    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page.locator("#provisional-paid").fill("0")


# ------------------------------------------------------------------ 场景 18
def test_ui_singleflight_rapid_edits_delayed_invalidate_ack(page, base_url) -> None:
    """延迟 ACK＋快速编辑：单飞、generation 递增、草稿合并、ACK 推进版本但
    不回写草稿、ACK 后不自动 prepare。"""
    from playwright.sync_api import expect

    invalidate_gate = _Gate()
    prepare_counter = _Counter()
    page.route("**/api/v1/calc/invalidate", invalidate_gate.handler)
    page.route("**/api/v1/calc/prepare", prepare_counter.handler)

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, revision0 = _acked(page)
    assert invalidate_gate.count == 0, "未编辑不得发送 invalidate"

    # —— 首次编辑：invalidate 在途（被闸持有）——
    page.get_by_label("应课税收入").fill("240000")
    _wait_for(lambda: invalidate_gate.count == 1, "首个 invalidate 已发出", page=page)
    assert _state(page) == "in-flight", "invalidate 在途须为 in-flight（单飞）"

    # —— 在途期间快速编辑多个字段：合并草稿、generation 递增、不并行发请求 ——
    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page.locator("#provisional-paid").fill("0")
    gen_while_held = _generation(page)
    assert gen_while_held >= 3, f"在途编辑须持续递增 generation，实际 {gen_while_held}"
    page.wait_for_timeout(300)  # 泵若干事件循环后仍不得有第二个 mutation
    assert invalidate_gate.count == 1, (
        f"在途期间不得发送第二个 invalidate（单飞），实际 {invalidate_gate.count}"
    )
    assert prepare_counter.count == 0, "在途编辑不得触发 prepare"

    # —— dirty/在途：confirm/calculate/send 禁用（004 §8.24 前置）——
    expect(page.locator("#confirm-and-calc")).to_be_disabled()
    expect(page.locator("#chat-send")).to_be_disabled()

    # —— 放行 ACK：Applied=1 的本操作 ACK 推进内部已确认版本（即使 gen 已变）——
    held_route, held_response = invalidate_gate.held[0]
    assert held_response.headers.get("x-edit-applied") == "1", (
        "invalidate 成功响应须带 X-Edit-Applied: 1"
    )
    op_revision = held_response.headers.get("x-edit-operation-revision")
    assert op_revision is not None, "须带本操作分配的 X-Edit-Operation-Revision"
    invalidate_gate.release_all()
    _wait_idle(page)
    assert _acked(page) == (epoch0, str(int(revision0) + 1)), (
        f"ACK 须推进内部已确认版本（{epoch0}/{revision0}→rev+1），实际 {_acked(page)!r}"
    )
    assert _generation(page) == gen_while_held, (
        "ACK 不得回写/回滚本地草稿 generation"
    )
    # 草稿保留（合并后的最新值）
    assert page.get_by_label("应课税收入").input_value() == "240000"
    assert page.get_by_label("强积金（MPF）强制性供款").input_value() == "9000"
    # ACK 后不自动 prepare／confirm／send
    page.wait_for_timeout(300)
    assert prepare_counter.count == 0, "invalidate ACK 后不得自动 prepare"
    expect(page.locator("#confirm-and-calc")).to_be_enabled()
    expect(page.locator("#chat-send")).to_be_enabled()


# ------------------------------------------------------------------ 场景 19
def test_ui_singleflight_edit_during_prepare_error_ack_reordering(page, base_url) -> None:
    """prepare 在途编辑：等含版本的 422 ACK 后才排队 invalidate；陈旧 payload
    丢弃；403 无版本头 → 不推进、冲突态保留草稿。"""
    from playwright.sync_api import expect

    prepare_gate = _Gate()
    invalidate_counter = _Counter()
    page.route("**/api/v1/calc/prepare", prepare_gate.handler)
    page.route("**/api/v1/calc/invalidate", invalidate_counter.handler)

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, _ = _acked(page)
    _fill_q1(page)
    page.locator("#married-status").select_option("unknown")  # 触发 422 needs_input
    _wait_idle(page)
    revision_before = int(_acked(page)[1])
    invalidate_baseline = invalidate_counter.count

    # —— 提交：prepare 在途（被闸持有）——
    _submit_and_confirm(page)
    _wait_for(lambda: prepare_gate.count == 1, "prepare 已发出且在途", page=page)
    assert _state(page) == "in-flight"

    # —— prepare 在途编辑：只合并草稿，不并行发 invalidate ——
    page.get_by_label("应课税收入").fill("250000")
    page.wait_for_timeout(300)
    assert invalidate_counter.count == invalidate_baseline, (
        "prepare 在途时编辑不得并行发送 invalidate（单飞队列）"
    )

    # —— 放行 422（带本操作 ACK）：版本推进；陈旧 questions 作废；随后才 invalidate ——
    _, prepare_response = prepare_gate.held[0]
    assert prepare_response.status == 422, (
        f"婚姻 unknown 须 422（needs_input），实际 {prepare_response.status}"
    )
    assert prepare_response.headers.get("x-edit-applied") == "1", (
        "422 也须确认本次 reservation 的 ACK（005 §12.2）"
    )
    op_revision = int(prepare_response.headers["x-edit-operation-revision"])
    assert op_revision == revision_before + 1, (
        f"422 ACK 须携带本操作分配版本（{revision_before}+1），实际 {op_revision}"
    )
    prepare_gate.release_all()
    _wait_for(lambda: invalidate_counter.count == invalidate_baseline + 1,
              "422 ACK 后排队 invalidate 才发出", page=page)
    invalidate_body = invalidate_counter.bodies()[-1]
    assert invalidate_body == {
        "expected_session_epoch": int(epoch0),
        "expected_input_revision": op_revision,
    }, (
        f"prepare 在途编辑后的 invalidate 须用 ACK 版本（闭合两键 body），"
        f"实际 {invalidate_body!r}"
    )
    _wait_idle(page)
    # 陈旧 422 的问题清单不得覆盖新草稿（payload 作废，不弹错误摘要）
    expect(page.locator("#error-summary")).to_be_hidden()
    assert page.get_by_label("应课税收入").input_value() == "250000", (
        "ACK/失效流程不得回写草稿"
    )
    assert prepare_gate.count == 1, "不得自动重新 prepare"

    # —— 403：无版本头、不推进版本、不读业务 body → 冲突态保留草稿 ——
    page.unroute("**/api/v1/calc/invalidate", invalidate_counter.handler)

    def _forbid(route) -> None:
        route.fulfill(
            status=403,
            content_type="application/json",
            body=json.dumps(
                {"error": {"code": "E_CSRF", "message_zh": "探针：安全校验拒绝"}},
                ensure_ascii=False,
            ),
        )

    page.route("**/api/v1/calc/invalidate", _forbid)
    revision_at_403 = _acked(page)
    page.get_by_label("应课税收入").fill("260000")
    _wait_for(lambda: _state(page) == "conflict", "403 后进入冲突态", page=page)
    assert _acked(page) == revision_at_403, (
        f"403 不带版本头，不得推进内部版本：{_acked(page)!r} vs {revision_at_403!r}"
    )
    expect(page.locator("#resync-session")).to_be_visible()
    expect(page.locator("#confirm-and-calc")).to_be_disabled()
    assert page.get_by_label("应课税收入").input_value() == "260000", (
        "403/冲突不得丢弃草稿"
    )


# ------------------------------------------------------------------ 场景 20
def test_ui_singleflight_cross_tab_409_explicit_resync(page, base_url) -> None:
    """真实双标签页 CAS 409：保留草稿、禁用能力、Current 头不自动接纳；
    仅显式「重新同步」读 runtime version，之后手动 prepare/confirm。"""
    from playwright.sync_api import expect

    context = page.context
    page_b = page  # 先加载的标签页 B（初始 epoch/revision）
    page.set_viewport_size({"width": 1280, "height": 800})
    page_b.goto(base_url + "/")
    _wait_idle(page_b)
    b_epoch0, b_rev0 = _acked(page_b)

    page_a = context.new_page()
    page_a.goto(base_url + "/")
    _wait_idle(page_a)

    # —— 标签页 A 完成一次完整链（推进服务器 revision）——
    _fill_q1(page_a)
    _wait_idle(page_a)
    _submit_and_confirm(page_a)
    expect(page_a.locator("#result-section")).to_be_visible()

    # —— 标签页 B 编辑：invalidate 以旧版本 CAS → 真实 409 ——
    invalidate_counter = _Counter()
    page_b.route("**/api/v1/calc/invalidate", invalidate_counter.handler)
    page_b.get_by_label("应课税收入").fill("300000")
    page_b.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page_b.locator("#provisional-paid").fill("0")
    _wait_for(lambda: _state(page_b) == "conflict", "B 收到 409 进入冲突态",
              page=page_b)
    assert invalidate_counter.count == 1, "冲突后不得自动重试/重发"
    assert _acked(page_b) == (b_epoch0, b_rev0), (
        f"409 Current 头只读、不得自动接纳：{_acked(page_b)!r}（应仍为 {b_epoch0}/{b_rev0}）"
    )
    # 草稿保留、能力禁用
    assert page_b.get_by_label("应课税收入").input_value() == "300000"
    expect(page_b.locator("#confirm-and-calc")).to_be_disabled()
    expect(page_b.locator("#chat-send")).to_be_disabled()
    expect(page_b.locator("#resync-session")).to_be_visible()

    # —— 显式重同步：只读取 runtime version；草稿不丢；不自动 prepare ——
    prepare_counter = _Counter()
    page_b.route("**/api/v1/calc/prepare", prepare_counter.handler)
    page_b.locator("#resync-session").click()
    _wait_idle(page_b)
    assert _acked(page_b) != (b_epoch0, b_rev0), "重同步须读取服务器当前版本"
    assert page_b.get_by_label("应课税收入").input_value() == "300000", (
        "重同步不得丢弃本地草稿"
    )
    page_b.wait_for_timeout(300)
    assert prepare_counter.count == 0, "重同步后不得自动 prepare（须人工重新发起）"

    # —— 人工重新走完整链：成功 ——
    _submit_and_confirm(page_b)
    expect(page_b.locator("#result-section")).to_be_visible()
    expect(page_b.locator("#result-badge")).to_have_text("部分结果")


# ------------------------------------------------------------------ 场景 21
def test_ui_singleflight_clear_session_epoch_drops_late_replies(page, base_url) -> None:
    """clear＝epoch 边界：ACK 带 epoch+1/rev0；旧 epoch 迟到 prepare/invalidate
    响应整体丢弃（不建结果/记录、不回滚版本）；ACK 缺失须显式重同步。"""
    from playwright.sync_api import expect

    prepare_gate = _Gate()
    page.route("**/api/v1/calc/prepare", prepare_gate.handler)

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")
    _wait_idle(page)
    epoch0, revision0 = _acked(page)
    _fill_q1(page)
    _wait_idle(page)
    assert int(_acked(page)[1]) > int(revision0), (
        f"编辑 ACK 后版本须推进，实际 {_acked(page)!r}（初值 {epoch0}/{revision0}）"
    )

    # —— 提交：prepare 在途（旧 epoch 响应被闸持有）——
    _submit_and_confirm(page)
    _wait_for(lambda: prepare_gate.count == 1, "prepare 已发出且在途", page=page)

    # —— 清空会话：本地立即清空；服务端 epoch+1/rev0 经 ACK ——
    page.locator("#clear-session").click()
    _wait_for(lambda: _acked(page) == (str(int(epoch0) + 1), "0"),
              "clear ACK 须带本操作分配的 epoch+1/rev0", page=page)
    assert page.get_by_label("应课税收入").input_value() == "", "清空须重置表单"
    assert page.locator("#records-list .record-card").count() == 0

    # —— 放出旧 epoch 的迟到 prepare 响应：整体丢弃 ——
    _, old_response = prepare_gate.held[0]
    assert old_response.status == 201, "prepare 在 fetch 时已由服务器处理（旧 epoch 201）"
    assert old_response.headers.get("x-edit-operation-epoch") == epoch0
    prepare_gate.release_all()
    page.wait_for_timeout(400)  # 泵事件循环让迟到响应有机会被（错误）应用
    assert _acked(page) == (str(int(epoch0) + 1), "0"), (
        f"旧 epoch 迟到 ACK 不得回滚版本：{_acked(page)!r}"
    )
    expect(page.locator("#result-section")).to_be_hidden()
    assert page.locator("#records-list .record-card").count() == 0, (
        "旧 epoch 迟到响应不得建立结果/记录"
    )

    # —— clear ACK 缺失（无版本头）→ 显式重同步 ——
    def _drop_ack(route) -> None:
        response = route.fetch()
        headers = {
            key: value
            for key, value in response.headers.items()
            if not key.lower().startswith("x-edit-")
        }
        route.fulfill(response=response, headers=headers)

    _fill_q1(page)
    _wait_idle(page)
    page.route("**/api/v1/session", _drop_ack)
    page.locator("#clear-session").click()
    _wait_for(lambda: _state(page) == "conflict",
              "clear ACK 缺失须进入待重同步态", page=page)
    expect(page.locator("#resync-session")).to_be_visible()
    page.locator("#resync-session").click()
    _wait_idle(page)
    epoch_after, _revision = _acked(page)
    assert epoch_after == str(int(epoch0) + 2), (
        f"两次 clear 后 epoch 须为 {int(epoch0) + 2}，实际 {_acked(page)!r}"
    )


# ------------------------------------------------------------------ 场景 22
def _add_full_year_not_apart(page) -> None:
    """显式录入「全年未分居」：空值表示未提供（unknown），不得由测试或 UI
    默认成「未分居」（004 §4「未填也不等于否」；005 §5.4 S6 形态）。

    004 §3.6/flow 27 的 T3/S6 形态度量与引擎单测 `_s6_household` 均要求
    living_apart 覆盖全窗且为 false；缺失会走 S7 未决，不是本场景前提。
    """
    page.locator("#add-living-apart-period").click()
    row = page.locator("#living-apart-periods .living-apart-row").last
    row.get_by_label("分居说明期间开始（含）").fill("2025-04-01")
    row.get_by_label("分居说明期间结束（不含）").fill("2026-04-01")
    row.get_by_label("该期间是否分居").select_option("false")


def _fill_couple(page) -> None:
    """已婚夫妇基础事实（T3：p1 500000/18000、p2 200000/18000、全年未分居）。"""
    page.get_by_label("应课税收入").fill("500000")
    page.get_by_label("强积金（MPF）强制性供款").fill("18000")
    page.locator("#provisional-paid").fill("0")
    page.locator("#married-status").select_option("married")
    page.locator("#spouse-salary-ai").select_option("true")
    page.get_by_label("配偶薪俸净入息（NAI）").fill("200000")
    page.get_by_label("配偶 MPF 强制性供款").fill("18000")
    _add_full_year_not_apart(page)


def test_ui_salary_nomination_actual_hypothetical_and_conditional_copy(
    page, base_url
) -> None:
    """actual/hypothetical 提名独立（互不复制、不默认本人）；未接纳 joint 的
    条件式金额只作「条件式比较，非已接纳税额」展示，不进入当前税额/best。"""
    from playwright.sync_api import expect

    prepare_counter = _Counter()
    page.route("**/api/v1/calc/prepare", prepare_counter.handler)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")
    _wait_idle(page)
    _fill_couple(page)

    # —— actual basis：提名控件按 basis 独立显示 ——
    page.locator("#candidate-joint").check()
    expect(page.locator("#nom-actual-group")).to_be_visible()
    expect(page.locator("#nom-hypo-group")).to_be_hidden()
    page.locator("#actual-joint-elected").select_option("false")
    page.locator("#nom-actual-status").select_option("named")
    expect(page.locator("#nom-actual-person-field")).to_be_visible()
    page.locator("#nom-actual-person").select_option("p2")
    _wait_idle(page)
    _submit_and_confirm(page)
    _wait_for(lambda: prepare_counter.count == 1, "actual 提名 prepare 已发出",
              page=page)
    assessment = prepare_counter.bodies()[-1]["input"]["years"]["2025_26"]["assessment"]
    assert assessment["actual"]["salary_joint_nomination"] == {
        "status": "named", "person_id": "p2",
    }, f"actual 提名须原样进 wire：{assessment!r}"
    assert "hypothetical_salary_joint_nomination" not in assessment, (
        "actual basis 不得携带 hypothetical 提名（两字段独立）"
    )
    # 关闭可能残留的错误/结果提示，继续状态循环
    page.wait_for_timeout(200)

    # —— 各状态 wire：unknown/disputed 不带人；none 默认不指本人 ——
    for status in ("unknown", "disputed", "none"):
        page.locator("#nom-actual-status").select_option(status)
        _wait_idle(page)
        _submit_and_confirm(page)
        _wait_for(lambda: len(prepare_counter.bodies()) >= 1, "prepare 已发出",
                  page=page)
        assessment = prepare_counter.bodies()[-1]["input"]["years"]["2025_26"]["assessment"]
        nomination = assessment["actual"]["salary_joint_nomination"]
        assert nomination == {"status": status, "person_id": None}, (
            f"status={status} 时 person_id 必须为 null（不默认本人/高收入者），"
            f"实际 {nomination!r}"
        )

    # —— 切换 hypothetical：不复制 actual 提名 ——
    page.locator("#assessment-basis").select_option("hypothetical")
    expect(page.locator("#nom-hypo-group")).to_be_visible()
    expect(page.locator("#nom-actual-group")).to_be_hidden()
    assert page.locator("#nom-hypo-status").input_value() == "none", (
        "actual 提名不得复制到 hypothetical"
    )
    page.locator("#nom-hypo-status").select_option("named")
    page.locator("#nom-hypo-person").select_option("p1")
    _wait_idle(page)
    _submit_and_confirm(page)
    _wait_for(lambda: True, "noop", timeout=0.2, page=page)
    assessment = prepare_counter.bodies()[-1]["input"]["years"]["2025_26"]["assessment"]
    assert assessment["hypothetical_salary_joint_nomination"] == {
        "status": "named", "person_id": "p1",
    }
    assert "salary_joint_nomination" not in assessment.get("actual", {}), (
        "hypothetical 提名不得写回 actual（不互相复制）"
    )

    # —— 条件式比较（T3 反向 nominee p2：joint ineligible、conditional 47000、
    #    separate 38500 为 best）——
    page.goto(base_url + "/")  # 重新载入＝干净编辑器（同会话，版本续读）
    _wait_idle(page)
    _fill_couple(page)
    page.locator("#spouse-dda-details").evaluate("el => el.open = true")
    page.locator("#spouse-daa").select_option("true")
    page.locator("#spouse-dda-claim").check()
    page.locator("#candidate-joint").check()
    page.locator("#assessment-basis").select_option("hypothetical")
    page.locator("#nom-hypo-status").select_option("named")
    page.locator("#nom-hypo-person").select_option("p2")
    _wait_idle(page)
    _submit_and_confirm(page)
    expect(page.locator("#result-section")).to_be_visible()
    expect(page.locator("#result-badge")).to_have_text("部分结果")
    amounts = page.locator("#result-amounts").inner_text()
    assert "38500" in amounts, f"已接纳 separate 38500 须为当前结果，实际：{amounts!r}"
    assert "47000" not in amounts, (
        "未接纳 joint 的条件式金额不得进入当前税额分项"
    )
    steps = page.locator("#result-steps").inner_text()
    assert "条件式比较，非已接纳税额" in steps, (
        f"未接纳 joint 的条件式金额须带权威标注，实际：{steps!r}"
    )
    assert "47000" in steps, "条件式金额仅在非权威检查记录中可见"


# ------------------------------------------------------------------ 场景 23
def test_ui_child_dda_nomination_not_spouse_dda(page, base_url) -> None:
    """child-DDA（无基本 child claim）随整组提名；spouse DDA 不显示/不校验
    子女提名控件。"""
    from playwright.sync_api import expect

    prepare_counter = _Counter()
    page.route("**/api/v1/calc/prepare", prepare_counter.handler)
    page.set_viewport_size({"width": 1280, "height": 800})

    # —— A：child DDA 无基本 claim + 整组提名 agreed p2 ——
    page.goto(base_url + "/")
    _wait_idle(page)
    _fill_couple(page)
    page.locator("#actual-joint-elected").select_option("false")  # 已婚 actual 活动消费者
    page.locator("#children-details").evaluate("el => el.open = true")
    page.locator("#add-child").click()
    entry = page.locator("#dep-d1")
    expect(entry).to_be_visible()
    entry.get_by_label("出生日期（YYYY-MM-DD；资格路径需要时填写）").fill("2015-06-01")
    entry.get_by_label("关系依据").select_option("birth")
    entry.get_by_label("该年度是否未婚（子女 1）").select_option("true")
    entry.get_by_label("是否符合相关政府伤残津贴资格（子女 1）").select_option("true")
    entry.get_by_label("是否供养（期间）（子女 1）").select_option("true")
    entry.get_by_label("申索伤残受养人免税额（此子女）").check()
    # child-DDA（无基本 child claim）也触发整组提名控件（004 §3.3）
    expect(page.locator("#child-nomination-group")).to_be_visible()
    page.locator("#child-nomination-status").select_option("agreed")
    page.locator("#child-nomination-person").select_option("p2")
    _wait_idle(page)
    _submit_and_confirm(page)
    expect(page.locator("#result-section")).to_be_visible()
    expect(page.locator("#result-badge")).to_have_text("部分结果")
    wire = prepare_counter.bodies()[-1]["input"]["years"]["2025_26"]
    claims = wire["claims"]
    assert claims == [
        {"claim_id": "c1", "kind": "disabled_dependant",
         "claimant_id": "p1", "subject_id": "d1"}
    ], f"child-DDA 无基本 child claim：claims 恰为 DDA 一项，实际 {claims!r}"
    assert wire["household"]["child_nomination"] == {
        "status": "agreed", "person_id": "p2",
    }, "child-DDA 仍校验/携带整组提名"
    steps_text = page.locator("#result-steps").inner_text()
    assert "75000" in steps_text, "DDA 75,000 须在结果步骤中呈现"

    # —— B：spouse DDA 不显示子女提名（不同 locator、不同事实）——
    page.goto(base_url + "/")
    _wait_idle(page)
    page.get_by_label("应课税收入").fill("240000")
    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    page.locator("#provisional-paid").fill("0")
    page.locator("#married-status").select_option("married")
    page.locator("#spouse-salary-ai").select_option("false")
    # 005 §4.6：participant 的 NAI/MPF 是 presence 事实（缺失≠零）；无收入须显式 0。
    # 金额控件在 AI=false 时仍须可填；inactive 的薪俸来源范围不显示。
    expect(page.locator("#spouse-income-block")).to_be_visible()
    expect(page.locator("#spouse-source-field")).to_be_hidden()
    page.get_by_label("配偶薪俸净入息（NAI）").fill("0")
    page.get_by_label("配偶 MPF 强制性供款").fill("0")
    page.locator("#actual-joint-elected").select_option("false")
    page.locator("#spouse-pa-mode").select_option("none")
    page.locator("#spouse-dda-details").evaluate("el => el.open = true")
    page.locator("#spouse-daa").select_option("true")
    page.locator("#spouse-dda-claim").check()
    _add_full_year_not_apart(page)  # S6 前提：已婚期间须已证非分居（否则 S7 未决）
    expect(page.locator("#child-nomination-group")).to_be_hidden(), (
        "spouse DDA 路径不得显示子女整组提名控件"
    )
    _wait_idle(page)
    _submit_and_confirm(page)
    expect(page.locator("#result-section")).to_be_visible()
    wire = prepare_counter.bodies()[-1]["input"]["years"]["2025_26"]
    assert wire["household"]["child_nomination"] == {
        "status": "none", "person_id": None,
    }, "spouse DDA 不得携带子女提名"
    assert wire["claims"][-1]["subject_id"] == "p2", "spouse DDA 申索对象为配偶"


# ------------------------------------------------------------------ 场景 24
def test_ui_singleflight_focus_consent_no_auto_outbound(
    page, base_url, e2e_isolation
) -> None:
    """键盘编辑与显式重同步：焦点可见、草稿不丢；dirty/conflict 禁用
    confirm/calculate/send；零自动 consent 消费与 AI 外发。"""
    from playwright.sync_api import expect

    ai_counter = _Counter()
    page.route("**/api/v1/ai/**", ai_counter.handler)
    prepare_counter = _Counter()
    page.route("**/api/v1/calc/prepare", prepare_counter.handler)
    # 首个 invalidate 的 ACK 由闸门暂缓，确定性地观察 dirty 禁用；释放后恢复直通。
    invalidate_gate = _Gate()
    invalidate_state = {"hold": True}

    def _invalidate_handler(route) -> None:
        if invalidate_state["hold"]:
            invalidate_gate.handler(route)
        else:
            route.continue_()

    page.route("**/api/v1/calc/invalidate", _invalidate_handler)

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/")
    _wait_idle(page)

    # —— 键盘编辑：焦点可见且 dirty 即时禁用能力 ——
    income = page.get_by_label("应课税收入")
    income.focus()
    page.keyboard.type("240000")
    assert page.evaluate("document.activeElement && document.activeElement.id") == (
        "employment-income"
    ), "键盘输入期间焦点须保持在编辑控件"
    expect(page.locator("#confirm-and-calc")).to_be_disabled()
    expect(page.locator("#chat-send")).to_be_disabled()
    _wait_for(
        lambda: invalidate_gate.count == 1 and len(invalidate_gate.held) == 1,
        "首个 invalidate 响应进入闸门",
        page=page,
    )
    invalidate_state["hold"] = False
    invalidate_gate.release_all()
    _wait_idle(page)
    expect(page.locator("#confirm-and-calc")).to_be_enabled()

    # —— 构造跨标签页冲突（真实 409）——
    page_b = page.context.new_page()
    page_b.goto(base_url + "/")
    _wait_idle(page_b)
    page_b.get_by_label("应课税收入").fill("100000")
    _wait_idle(page_b)
    page_b.close()

    page.get_by_label("强积金（MPF）强制性供款").fill("9000")
    _wait_for(lambda: _state(page) == "conflict", "409 冲突态", page=page)
    assert income.input_value() == "240000", "冲突不得丢弃草稿"
    expect(page.locator("#confirm-and-calc")).to_be_disabled()
    expect(page.locator("#chat-send")).to_be_disabled()

    # —— 键盘显式重同步：焦点落在「重新同步」并保持可见；草稿不丢 ——
    page.locator("#resync-session").focus()
    page.keyboard.press("Enter")
    _wait_idle(page)
    assert page.evaluate("document.activeElement && document.activeElement.id") == (
        "confirm-and-calc"
    ), "重同步后焦点须移至可见的下一步人工动作（004 §8.24）"
    assert income.input_value() == "240000", "重同步不得丢弃草稿"
    page.wait_for_timeout(300)
    assert prepare_counter.count == 0, "重同步后不得自动 prepare"

    # —— 零自动 consent/外发：全程无 AI 端点调用、无模型外发 ——
    assert ai_counter.count == 0, f"不得有任何自动 AI 端点调用：{ai_counter.count}"
    assert e2e_isolation.model.send_calls == 0, "不得有任何模型外发"
    assert page.locator("#chat-messages li").count() == 0
