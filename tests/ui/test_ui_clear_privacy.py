"""REQ-12/14（Annex C C5.4）：清空会话必须同时删除该会话的同意记录。

规格锚点:
  - Annex C C5.4：清空＝session_epoch＋consent_epoch 双递增，且会话内
    records 与挂起能力删除；同意记录携带冻结的消息内容，仅靠 epoch
    失效会让旧对话内容留在内存，并可能随后续授权再次外发。
  - SDD §4/C3.1：DELETE /api/v1/session 返回清除结果。

【拟名】契约（本文件为安全修复回归，Green 不得要求改写）:
  - DELETE /api/v1/session 成功后，该 sid 在 ConsentStore 中的全部记录
    （含 granted/prepared/consumed 任意状态）被物理删除；
  - 响应携带 consents_purged（本次删除条数，>= 已创建数）；
  - 其他 sid 的同意记录不受影响。
"""

from __future__ import annotations


def test_clear_session_purges_consent_records_for_sid(ui) -> None:
    consent_store = ui.app.state.consent_store

    sid = ui.session_id()
    item = consent_store.create(
        sid,
        {
            "payload": {
                "messages": [{"role": "user", "content": "secret-income-question"}]
            }
        },
    )
    consent_id = item["consent_id"]
    assert consent_store.get(sid, consent_id) is not None, "前置：同意记录已创建"

    other = consent_store.create(
        "other-sid",
        {"payload": {"messages": [{"role": "user", "content": "other"}]}},
    )

    resp = ui.api.delete("/api/v1/session")
    assert resp.status_code in (200, 202, 204), resp.text

    body = resp.json() if resp.content else {}
    assert body.get("status") == "cleared"
    assert int(body.get("consents_purged", 0)) >= 1, (
        f"响应必须报告已删除的同意记录数，实际：{body!r}"
    )
    assert consent_store.get(sid, consent_id) is None, (
        "清空后该会话的同意记录（含冻结消息内容）必须被物理删除，"
        "不得只靠 consent_epoch 失效"
    )
    assert consent_store.get("other-sid", other["consent_id"]) is not None, (
        "清空只作用于当前 sid，不得影响其他会话的同意记录"
    )
