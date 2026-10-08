"""REQ-13/14 会话隐私边界：AI 撤回 vs 本地记录；清空 vs 多标签与外部副本。

REQ: REQ-13／REQ-14（Annex A §12.5/§12.6；Annex C C3.2.4/C4.6/C5.4；
§12.9 命名测试 test_ui_revoke_keeps_local_records_discards_pending_ai／
test_ui_clear_all_tabs_no_financial_persistence）。
规格锚点:
  - Annex A §12.5：撤回仅使 outbound／pending AI 请求及迟到 AI 响应失效，
    不清除本地记录。
  - Annex C C5.4：双 epoch 分离——consent_epoch（撤回）仅废 AI；session_epoch
    （清空）删除会话内存 records；已下载/打印外部副本不在服务端、不受影响。
  - Annex A §12.6：清空经 BroadcastChannel 通知同会话其他标签页同步清空；
    无财务 localStorage。
期望值来源: 结构/状态断言（epoch、404、页面接线）；无金额期望。
"""

from __future__ import annotations

import json

from _helpers import complete_calc


def _error_code(resp) -> str:
    payload = resp.json()
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状：{payload!r}"
    return err["code"]


def test_ui_revoke_keeps_local_records_discards_pending_ai(ui) -> None:
    """AI 撤回（consent_epoch++，session_epoch 不变）：撤回后外发/挂起 AI 失效
    （chat 拒绝）；本地完成记录仍可查/下载（§12.5/C5.4）。"""
    api = ui.api

    done = complete_calc(api)  # 本地完成记录
    record_id = done["record_id"]

    consent_epoch_before = ui.registry.consent_epoch
    session_epoch_before = ui.registry.session_epoch

    # —— AI 授权：预览＋授予 ——
    preview = api.post(
        "/api/v1/ai/consent/preview",
        json={"messages": [{"role": "user", "content": "帮我检查收入资料"}]},
    )
    assert preview.status_code in (200, 201), (
        f"AI 外发前必须可取得逐字预览（§12.5），实际 {preview.status_code}: {preview.text[:300]!r}"
    )
    consent_id = preview.json().get("consent_id")
    assert consent_id, "预览响应必须含 consent_id（C4.8 prepared 态）"
    preview_text = json.dumps(preview.json(), ensure_ascii=False)
    assert "帮我检查收入资料" in preview_text, (
        "预览必须逐字展示将发送的完整消息上下文（§7/§12.5，不得只列摘要）"
    )

    grant = api.post("/api/v1/ai/consent", json={"consent_id": consent_id})
    assert grant.status_code in (200, 201), (
        f"授予必须可用（C4.8 granted 态），实际 {grant.status_code}: {grant.text[:300]!r}"
    )

    # —— 撤回：仅废 AI（C5.4 consent_epoch++；session_epoch 不变）——
    revoke = api.delete(f"/api/v1/ai/consent/{consent_id}")
    assert revoke.status_code in (200, 202, 204), (
        f"撤回必须可用（C3.1 DELETE /api/v1/ai/consent/{{id}}），"
        f"实际 {revoke.status_code}: {revoke.text[:300]!r}"
    )
    assert ui.registry.consent_epoch > consent_epoch_before, (
        "撤回必须使 consent_epoch 递增（C5.4）"
    )
    assert ui.registry.session_epoch == session_epoch_before, (
        "撤回不得影响 session_epoch——本地会话/确认/记录全部保留（C5.4 双 epoch 分离）"
    )

    # —— 撤回后：挂起/后续 AI 外发失效；迟到 AI 响应不得应用 ——
    chat = api.post(
        "/api/v1/ai/chat",
        json={"consent_id": consent_id, "messages": [{"role": "user", "content": "继续"}]},
    )
    assert chat.status_code in (403, 409), (
        f"撤回后的 AI 调用必须被拒绝（E_CONSENT_*），实际 {chat.status_code}: {chat.text[:300]!r}"
    )
    assert _error_code(chat) in ("E_CONSENT_REQUIRED", "E_CONSENT_STALE")
    assert "amounts" not in chat.json(), "AI 响应不得携带权威金额"

    # —— 本地记录保留：可查＋可下载 ——
    view = api.get(f"/api/v1/records/{record_id}")
    assert view.status_code == 200, view.text
    dl = api.get(f"/api/v1/records/{record_id}/download")
    assert dl.status_code == 200, dl.text


def test_ui_clear_all_tabs_no_financial_persistence(ui) -> None:
    """清空会话（两 epoch++）：删除内存 records 与未消费确认；无财务
    localStorage；页面接线 BroadcastChannel 同步多标签（§12.6/C5.4）。"""
    api = ui.api

    done = complete_calc(api)
    record_id = done["record_id"]

    # 清空前持有未消费确认能力（新一轮流程已确认、尚未执行）
    from _helpers import run_confirm, run_prepare, salaries_input

    pending = run_confirm(api, run_prepare(api, salaries_input()))

    session_epoch_before = ui.registry.session_epoch
    consent_epoch_before = ui.registry.consent_epoch

    # —— 清空 ——
    clear = api.delete("/api/v1/session")
    assert clear.status_code in (200, 202, 204), clear.text
    payload = clear.json() if clear.content else {}
    assert isinstance(payload.get("session_epoch"), int), (
        f"清空响应必须携带 session_epoch（C5.4）：{payload!r}"
    )
    assert isinstance(payload.get("consent_epoch"), int), (
        f"清空响应必须携带 consent_epoch（C5.4）：{payload!r}"
    )
    assert ui.registry.session_epoch > session_epoch_before, "清空必须 session_epoch++"
    assert ui.registry.consent_epoch > consent_epoch_before, "清空必须 consent_epoch++"

    # —— 内存 records 已删除 ——
    gone = api.get(f"/api/v1/records/{record_id}")
    assert gone.status_code == 404, f"清空后内存记录必须删除，实际 {gone.status_code}"
    dl_gone = api.get(f"/api/v1/records/{record_id}/download")
    assert dl_gone.status_code == 404, "清空后下载入口同样不可用（内存态）"

    # —— 未消费确认能力失效 ——
    exec_after = api.post("/api/v1/calc/salaries-tax", json={"confirmation_id": pending})
    assert exec_after.status_code == 403, (
        f"清空后确认能力必须失效（403 E_CONFIRMATION_REQUIRED），"
        f"实际 {exec_after.status_code}"
    )
    assert _error_code(exec_after) == "E_CONFIRMATION_REQUIRED"

    # —— 外部副本不受影响：服务端不持有、不追溯删除下载/打印副本 ——
    # （下载响应本就 no-store、无服务端缓存；服务端无可「撤回」外部副本的通道）
    page = api.get("/")
    assert page.status_code == 200, page.text
    html = page.text
    assert "localStorage" not in html, (
        "无财务 localStorage：页面不得使用 localStorage 保存任何会话资料（C5.5/§12.6）"
    )
    assert "BroadcastChannel" in html, (
        "清空必须经 BroadcastChannel 通知同会话其他标签页同步清空（§12.6）"
    )
    assert "副本" in html or "下载" in html, (
        "清空提示须说明已下载/打印的外部副本不会被移除（§12.6）"
    )
