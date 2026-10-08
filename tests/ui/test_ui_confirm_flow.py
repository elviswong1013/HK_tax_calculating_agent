"""REQ-11 UI 绑定：needs_input 无确认能力；无用户确认动作不得执行。

REQ: REQ-11（Annex A §12.2 计算三段流程的 UI 绑定；§12.9 命名测试
test_ui_prepare_needs_input_no_confirmability／test_ui_explicit_user_confirm_before_execute）。
规格锚点:
  - Annex A §6：错误摘要可聚焦、链接到首个问题字段；「还有 N 项资料需要确认」。
  - Annex A §12.2：needs_input/blocked 不签发可执行确认能力；「确认资料并计算」
    为既有确认动作（§6 权威按钮文案）；prepared_id 非可执行 id。
  - Annex C C3.2.1：needs_input → 422＋questions[]，不产生任何 id；
    C3.2.2 confirm UI/CSRF/session 独占，acknowledge=true 才签发能力；
    C3.2.3 execute 凭 confirmation_id；伪造/缺失 → 403 E_CONFIRMATION_REQUIRED。
期望值来源: 结构/状态断言（错误码、id 缺席、页面结构）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 工作台页（GET /）含可聚焦错误摘要容器：id/class 含 "error-summary" 且带
    tabindex="-1"（§6「点击检查资料聚焦首个问题，顶部显示摘要」的 SSR 结构）。
  - 工作台页含 <button>「确认资料并计算」（§6 权威文案；键盘可操作原生控件）。
"""

from __future__ import annotations

import json

from _helpers import salaries_input


def test_ui_prepare_needs_input_no_confirmability(ui) -> None:
    """needs_input：prepare 422＋questions 无任何 id；伪造 prepared/confirmation
    均不可取得执行能力；工作台须有可聚焦错误摘要结构（§6/§12.2）。"""
    api = ui.api

    # —— needs_input：资格 unknown → 422＋中文问题清单，不产生 id（C3.2.1）——
    r = api.post(
        "/api/v1/calc/prepare",
        json={
            "tax_type": "salaries_tax",
            "schema_version": "1.0.0",
            "input": salaries_input(married_status={"value": "unknown"}),
        },
    )
    assert r.status_code == 422, r.text
    payload = r.json()
    assert payload["error"]["code"] == "E_ELIGIBILITY_UNKNOWN"
    assert "prepared_id" not in payload, "needs_input 不得签发 prepared_id（C3.2.1）"
    questions_text = json.dumps(payload, ensure_ascii=False)
    assert "questions" in payload and payload["questions"], (
        f"needs_input 须附结构化中文问题清单：{payload!r}"
    )
    assert any(
        "\u4e00" <= ch <= "\u9fff" for q in payload["questions"] for ch in json.dumps(q, ensure_ascii=False)
    ), "问题清单必须为中文"

    # —— 无 prepared_id → confirm 不可签发执行能力（伪造也不行）——
    r_confirm = api.post(
        "/api/v1/calc/confirm",
        json={
            "prepared_id": "p-fabricated",
            "canonical_input_hash": "sha256:" + "00" * 32,
            "expected_binding": {
                "bundle_id": "b-fabricated",
                "bundle_hash": "sha256:" + "00" * 32,
                "rules_schema_version": "1.0.0",
                "engine_version": "0.0.0",
            },
            "acknowledge": True,
        },
    )
    assert r_confirm.status_code == 403, r_confirm.text
    assert r_confirm.json()["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert "confirmation_id" not in r_confirm.json(), (
        "needs_input 路径不得出现可执行确认能力（§12.2）"
    )

    # —— 伪造 confirmation_id 执行 → 403，不得产生记录 ——
    r_exec = api.post(
        "/api/v1/calc/salaries-tax", json={"confirmation_id": "c-fabricated"}
    )
    assert r_exec.status_code == 403, r_exec.text
    assert r_exec.json()["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert "record_id" not in r_exec.json(), "无确认能力不得执行计算（§12.2）"

    # —— 工作台 SSR：可聚焦错误摘要结构（§6；键盘/读屏可达）——
    page = api.get("/")
    assert page.status_code == 200, page.text
    html = page.text
    assert "error-summary" in html, (
        "工作台必须包含错误摘要容器（id/class 含 error-summary，§6「顶部显示摘要」）"
    )
    assert 'tabindex="-1"' in html, "错误摘要容器必须可聚焦（§6/§9：错误摘要可聚焦）"


def test_ui_explicit_user_confirm_before_execute(ui) -> None:
    """prepare 成功≠可执行：prepared_id 直接执行 → 403；未 acknowledge →
    无 confirmation_id；页面确认按钮为权威文案的原生 <button>。"""
    api = ui.api

    from _helpers import run_prepare

    prepared = run_prepare(api)

    # —— prepared_id 不是可执行 id：直接拿去执行 → 403（C3.2.1/C3.2.3）——
    r_prepared_as_exec = api.post(
        "/api/v1/calc/salaries-tax",
        json={"confirmation_id": prepared["prepared_id"]},
    )
    assert r_prepared_as_exec.status_code == 403, r_prepared_as_exec.text
    assert r_prepared_as_exec.json()["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert "record_id" not in r_prepared_as_exec.json()

    # —— 用户未确认（acknowledge=false）→ 不签发能力 ——
    r_no_ack = api.post(
        "/api/v1/calc/confirm",
        json={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": False,
        },
    )
    assert r_no_ack.status_code == 403, r_no_ack.text
    assert r_no_ack.json()["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert "confirmation_id" not in r_no_ack.json(), (
        "无用户显式确认动作不得签发执行能力（§12.2）"
    )

    # —— 未确认就执行（拿不到任何 id）→ 403 ——
    r_exec = api.post("/api/v1/calc/salaries-tax", json={"confirmation_id": ""})
    assert r_exec.status_code == 403, r_exec.text

    # —— 工作台 SSR：确认动作为原生按钮＋权威文案（§6「确认资料并计算」）——
    page = api.get("/")
    assert page.status_code == 200, page.text
    html = page.text
    assert "确认资料并计算" in html, (
        "工作台必须包含确认按钮文案「确认资料并计算」（Annex A §6 权威文案）"
    )
    assert "<button" in html.lower(), "确认动作必须是原生 <button>（§9 全键盘可操作）"
