"""P2 W2 repair — AI consent_epoch 撤回竞态与工具信封语义预检负例。

Audit (ora-56) 项 1/3：
  - 005:698：出站期间撤回已 consumed 的授权（只 bump consent_epoch）→ 迟到
    响应整轮丢弃（不执行工具、不写记录、不消费确认）。
  - 005:911–912：propose_input／validate_input 的 input 是完整 HTTP 业务信封；
    必须与 HTTP 同一校验器＋语义预检，非法 tax_type/schema/input 一律 rejected，
    不得创建 prepare/确认能力；裸 facts 的既有只读回显不变。
"""

from __future__ import annotations

import threading

from _ai_fakes import (
    ai_grant,
    ai_preview,
    ai_round,
    complete_calc,
    prepare_body,
    run_confirm,
    run_prepare,
    salaries_input,
    tool_results_of,
    wire_tool_calls_body,
)

BASE_ORIGIN = "http://127.0.0.1:8000"
CHAT_PATH = "/api/v1/ai/chat"
MSG = [{"role": "user", "content": "請幫我估算薪俸税"}]


def _record_ids(registry, sid: str) -> frozenset[str]:
    with registry._lock:
        return frozenset(registry._sessions[sid]["records"].keys())


def _consumed(registry, sid: str, confirmation_id: str) -> bool:
    consumption = registry.get_consumption(sid, confirmation_id)
    return bool(consumption and consumption.get("consumed"))


def _clone_chat_client(ai):
    import httpx
    from app.config import SESSION_COOKIE_NAME

    ai.api._token()
    raw = ai.api.client.cookies.get(SESSION_COOKIE_NAME)
    assert raw, "未能取得测试会话 cookie"
    client = httpx.Client(
        transport=httpx.ASGITransport(app=ai.app), base_url=BASE_ORIGIN
    )
    client.cookies.set(SESSION_COOKIE_NAME, raw)
    return client


# ---------------------------------------------------------------------------
# 审计项 1：撤回已 consumed 授权 → consent_epoch 变化 → 整轮丢弃
# ---------------------------------------------------------------------------
def test_ai_chat_revoked_consumed_consent_discards_late_round(ai, monkeypatch) -> None:
    """004/C5.4＋005:698：出站阻塞期间撤回 consent（状态仍 consumed，仅
    consent_epoch++）→ 释放后不得执行任何工具/写记录/消费确认。"""
    import app.api.routes as routes_module

    api = ai.api
    sid = ai.session_id()
    prepared = run_prepare(api)
    confirmation_id = run_confirm(api, prepared)
    records_before = _record_ids(ai.registry, sid)

    ai.outbound.script = [
        wire_tool_calls_body(
            [
                {
                    "id": "call_calc",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        )
    ]
    preview = ai_preview(api, MSG)
    ai_grant(api, preview["consent_id"])
    token = api._token()

    dispatched: list[int] = []
    original_execute = routes_module.execute_tool_calls

    def execute_spy(calls, ctx):
        dispatched.append(1)
        return original_execute(calls, ctx)

    monkeypatch.setattr(routes_module, "execute_tool_calls", execute_spy)

    original_send = ai.outbound.send
    entered = threading.Event()
    release = threading.Event()

    def blocking_send(upstream_endpoint, api_key, frozen, **kwargs):
        entered.set()
        assert release.wait(10), "外发释放事件超时"
        return original_send(upstream_endpoint, api_key, frozen, **kwargs)

    monkeypatch.setattr(ai.outbound, "send", blocking_send)
    client = _clone_chat_client(ai)
    result: dict = {}

    def run_chat() -> None:
        try:
            resp = client.post(
                CHAT_PATH,
                json={"consent_id": preview["consent_id"], "messages": MSG},
                headers={"Origin": BASE_ORIGIN, "X-CSRF-Token": token},
            )
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            result.update({"status": resp.status_code, "payload": payload})
        except BaseException as exc:  # noqa: BLE001 - 线程异常显式回传
            result["error"] = exc
        finally:
            client.close()

    thread = threading.Thread(target=run_chat, name="revoked-consent-round", daemon=True)
    thread.start()
    assert entered.wait(10), "出站接缝未被调用"
    try:
        revoke = api.delete(f"/api/v1/ai/consent/{preview['consent_id']}")
        assert revoke.status_code == 200, (
            f"撤回须 200，实际 {revoke.status_code}: {revoke.text[:300]!r}"
        )
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive(), "迟到轮线程未结束"
    assert "error" not in result, f"迟到轮请求异常：{result.get('error')!r}"

    assert result["status"] == 200, result
    payload = result["payload"]
    assert payload.get("status") == "discarded", (
        "consent_epoch 已变化必须整轮丢弃（005:698），"
        f"实际 {payload!r}"
    )
    assert dispatched == [], (
        f"丢弃轮不得调用工具层（无 executed 条目），实际 {dispatched!r}"
    )
    assert "tool_results" not in payload and "amounts" not in payload
    assert _record_ids(ai.registry, sid) == records_before, "丢弃轮不得写记录"
    assert not _consumed(ai.registry, sid, confirmation_id), (
        "丢弃轮不得消费确认能力（005:698）"
    )


# ---------------------------------------------------------------------------
# 审计项 3：propose/validate 完整 HTTP 业务信封校验（非法三种 → rejected）
# ---------------------------------------------------------------------------
def _invalid_envelopes() -> list[tuple[str, dict, str]]:
    return [
        (
            "unknown_tax_type",
            {"tax_type": "not-a-tax", "schema_version": "9", "input": {}},
            "E_INPUT_TYPE",
        ),
        (
            "wrong_schema_version",
            {"tax_type": "salaries_tax", "schema_version": "9", "input": {}},
            "E_INPUT_TYPE",
        ),
        (
            "invalid_input",
            {"tax_type": "profits_tax", "schema_version": "1.0.0", "input": {}},
            "E_INPUT_MISSING",
        ),
    ]


def _capability_counts(ai) -> tuple[int, int]:
    sid = ai.session_id()
    with ai.registry._lock:
        session = ai.registry._sessions[sid]
        return len(session["prepared"]), len(session["confirmations"])


def test_ai_read_only_tools_reject_invalid_http_envelope(ai) -> None:
    """005:911–912：两只读工具对非法 tax_type/schema/input 均 rejected，且
    不创建 prepare/确认能力。"""
    api = ai.api
    before = _capability_counts(ai)
    problems: list[str] = []
    for tool_name in ("propose_input", "validate_input"):
        for label, envelope, expected_code in _invalid_envelopes():
            resp, _ = ai_round(
                api,
                ai.outbound,
                MSG,
                wire_tool_calls_body(
                    [
                        {
                            "id": f"call_{tool_name}_{label}",
                            "name": tool_name,
                            "arguments": {"input": envelope},
                        }
                    ]
                ),
            )
            if resp.status_code != 200:
                problems.append(
                    f"{tool_name}/{label}: 工具轮须 200，实际 {resp.status_code}: "
                    f"{resp.text[:200]!r}"
                )
                continue
            entry = tool_results_of(resp.json())[0]
            if entry["outcome"] != "rejected":
                problems.append(
                    f"{tool_name}/{label}: 必须 rejected（005:912/916），"
                    f"实际 {entry!r}"
                )
                continue
            code = (entry.get("error") or {}).get("code")
            if code != expected_code:
                problems.append(
                    f"{tool_name}/{label}: 错误码须 {expected_code}，实际 {code!r}"
                )
            if entry.get("result") is not None:
                problems.append(f"{tool_name}/{label}: rejected 不得有 result")
    assert not problems, "只读工具信封预检缺口（005:911–912）：\n  - " + "\n  - ".join(
        problems
    )
    assert _capability_counts(ai) == before, (
        "只读工具不得创建 prepare/确认能力（005:912）"
    )


def test_ai_read_only_tools_keep_valid_read_only_behavior(ai) -> None:
    """完整信封通过同一校验后 executed＋005:915 形状；裸 facts 一律 rejected；
    均不创建能力。"""
    api = ai.api
    before = _capability_counts(ai)
    envelope = {
        "tax_type": "profits_tax",
        "schema_version": "1.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "entity_kind": "sole_proprietorship",
            "assessable_profit": "600000",
            "two_tier": {
                "connected_entities": [],
                "election_made": {"value": True},
                "no_other_election_same_year": {"value": True},
            },
        },
    }
    resp, _ = ai_round(
        api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_valid_env",
                    "name": "propose_input",
                    "arguments": {"input": envelope},
                },
                {
                    "id": "call_valid_validate",
                    "name": "validate_input",
                    "arguments": {"input": envelope},
                },
                {
                    "id": "call_legacy",
                    "name": "validate_input",
                    "arguments": {"input": salaries_input()},
                },
            ]
        ),
    )
    assert resp.status_code == 200, resp.text[:300]
    entries = {entry["id"]: entry for entry in tool_results_of(resp.json())}
    for valid_id in ("call_valid_env", "call_valid_validate"):
        env_entry = entries[valid_id]
        assert env_entry["outcome"] == "executed", env_entry
        assert env_entry["note"] == "read_only_checked"
        assert env_entry["needs_confirmation"] is False
        assert env_entry["error"] is None
        assert set(env_entry["result"]) == {
            "proposal",
            "canonical_input_hash",
            "expected_binding",
            "status",
            "questions",
            "missing_components",
        }
        assert env_entry["result"]["proposal"]["tax_type"] == "profits_tax"
        assert env_entry["result"]["proposal"]["schema_version"] == "1.0.0"
        assert env_entry["result"]["status"] == "checked"
        assert env_entry["result"]["questions"] == []
        assert env_entry["result"]["missing_components"] == []
        assert env_entry["result"]["canonical_input_hash"].startswith("sha256:")
        assert len(env_entry["result"]["canonical_input_hash"]) == 71
        assert set(env_entry["result"]["expected_binding"]) == {
            "bundle_id",
            "bundle_hash",
            "rules_schema_version",
            "engine_version",
        }
    legacy_entry = entries["call_legacy"]
    assert legacy_entry["outcome"] == "rejected", legacy_entry
    assert legacy_entry["error"]["code"] == "E_INPUT_MISSING"
    assert legacy_entry["result"] is None
    assert set(legacy_entry["error"]) == {
        "code",
        "message_zh",
        "field",
        "details",
        "binding",
        "status",
        "questions",
        "blocked_reason",
    }
    assert _capability_counts(ai) == before, (
        "只读工具不得创建 prepare/确认能力（005:912）"
    )


def test_ai_read_only_tool_rejects_when_pinned_bundle_missing(ai) -> None:
    """pinned bundle 缺失时不得退回裸回显（005:912）；用伪造 ToolContext 直接
    验证工具层 fail-closed。"""
    import app.ai.tools as tools_module

    ctx = tools_module.ToolContext(
        ai.registry,
        ai.session_id(),
        ai.store,
        {"bundle_id": "b-missing", "bundle_hash": "sha256:" + "0" * 64},
        pinned_bundle=None,
    )
    entry = tools_module.execute_tool_calls(
        [
            {
                "id": "call_no_bundle",
                "name": "propose_input",
                "arguments": {
                    "input": {
                        "tax_type": "profits_tax",
                        "schema_version": "1.0.0",
                        "input": {
                            "year_of_assessment": "2025_26",
                            "entity_kind": "sole_proprietorship",
                            "assessable_profit": "600000",
                        },
                    }
                },
            }
        ],
        ctx,
    )[0]
    assert entry["outcome"] == "rejected", entry
    assert entry["error"]["code"] == "E_RULES_STATE_UNVERIFIABLE"


def test_ai_read_only_tools_reject_bare_facts_and_empty_input(ai) -> None:
    """005:911–912：`{}`／裸 facts／缺信封键一律 rejected（不得绕过 HTTP
    校验与 pinned bundle/epoch/revision/guard）；不创建能力。"""
    api = ai.api
    before = _capability_counts(ai)
    cases = [
        ("empty", {}),
        ("bare_facts", salaries_input()),
        ("missing_schema", {"tax_type": "salaries_tax"}),
    ]
    problems: list[str] = []
    for tool_name in ("propose_input", "validate_input"):
        for label, bad_input in cases:
            resp, _ = ai_round(
                api,
                ai.outbound,
                MSG,
                wire_tool_calls_body(
                    [
                        {
                            "id": f"call_{tool_name}_{label}",
                            "name": tool_name,
                            "arguments": {"input": bad_input},
                        }
                    ]
                ),
            )
            if resp.status_code != 200:
                problems.append(f"{tool_name}/{label}: 工具轮须 200，实际 {resp.status_code}")
                continue
            entry = tool_results_of(resp.json())[0]
            if entry["outcome"] != "rejected":
                problems.append(
                    f"{tool_name}/{label}: 裸 facts/空输入必须 rejected，实际 {entry!r}"
                )
                continue
            error = entry.get("error") or {}
            if error.get("code") != "E_INPUT_MISSING":
                problems.append(
                    f"{tool_name}/{label}: 须 E_INPUT_MISSING，实际 {error.get('code')!r}"
                )
            if set(error) != {
                "code",
                "message_zh",
                "field",
                "details",
                "binding",
                "status",
                "questions",
                "blocked_reason",
            }:
                problems.append(
                    f"{tool_name}/{label}: 拒绝须 005:916 恰八键，实际 {sorted(error)!r}"
                )
            if entry.get("result") is not None:
                problems.append(f"{tool_name}/{label}: rejected 不得有 result")
    assert not problems, "裸 facts 信封拒绝缺口（005:911–912）：\n  - " + "\n  - ".join(
        problems
    )
    assert _capability_counts(ai) == before, "只读工具不得创建 prepare/确认能力"


def test_ai_read_result_contract_005_919(ai) -> None:
    """005:919：read_result 本地最小保存结果恰十键；steps 仅
    scenario/selection/next_year；不返回 raw input/decision。"""
    api = ai.api
    done = complete_calc(api)
    record_id = done["record_id"]
    record = api.get(f"/api/v1/records/{record_id}").json()["record"]

    resp, _ = ai_round(
        api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_read",
                    "name": "read_result",
                    "arguments": {"record_id": record_id},
                }
            ]
        ),
    )
    assert resp.status_code == 200, resp.text[:300]
    entry = tool_results_of(resp.json())[0]
    assert entry["outcome"] == "executed", entry
    result = entry["result"]
    assert set(result) == {
        "record_id",
        "tax_type",
        "binding",
        "status",
        "amounts",
        "blocked_reason",
        "missing_components",
        "unsupported",
        "pending_verification",
        "steps",
    }, sorted(result)
    assert result["record_id"] == record_id
    assert result["tax_type"] == record["tax_type"]
    assert result["binding"] == record["binding"]
    assert result["status"] == record["status"]
    assert result["amounts"] == record["amounts"]
    assert result["blocked_reason"] == record["blocked_reason"]
    assert result["missing_components"] == record["missing_components"]
    assert result["unsupported"] == record["unsupported"]
    assert result["pending_verification"] == record["pending_verification"]
    allowed_kinds = {"scenario", "selection", "next_year"}
    assert all(
        isinstance(step, dict) and step.get("kind") in allowed_kinds
        for step in result["steps"]
    ), result["steps"]
    assert "input" not in result and "questions" not in result


def _propose_envelope(api, ai, envelope: dict) -> dict:
    resp, _ = ai_round(
        api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_propose_missing",
                    "name": "propose_input",
                    "arguments": {"input": envelope},
                }
            ]
        ),
    )
    assert resp.status_code == 200, resp.text[:300]
    entry = tool_results_of(resp.json())[0]
    assert entry["outcome"] == "executed", entry
    assert entry["note"] == "read_only_checked"
    return entry["result"]


def test_ai_proposal_preserves_missing_next_component(ai) -> None:
    """005:915：缺下一年事实 → checked 但 missing_components 保留
    `next_provisional:2026_27`，不阻断本年已确认提案。"""
    api = ai.api
    before = _capability_counts(ai)
    result = _propose_envelope(api, ai, prepare_body(salaries_input()))
    assert result["status"] == "checked"
    assert "next_provisional:2026_27" in result["missing_components"], result
    assert result["questions"] == []
    assert _capability_counts(ai) == before


def test_ai_proposal_preserves_missing_next_when_next_guard_blocked(ai) -> None:
    """005:70/915：current ok、next guard blocked → 本年提案仍 checked，
    missing_components 保留 next 缺项。"""
    class _NextBlockedGuard:
        def availability(self, period: str) -> dict:
            if period == "2026_27":
                return {"mode": "blocked", "states": []}
            return {"mode": "ok", "states": []}

    api = ai.api
    before = _capability_counts(ai)
    real_guard = ai.app.state.update_scheduler
    ai.app.state.update_scheduler = _NextBlockedGuard()
    try:
        result = _propose_envelope(api, ai, prepare_body(salaries_input()))
    finally:
        ai.app.state.update_scheduler = real_guard
    assert result["status"] == "checked"
    assert "next_provisional:2026_27" in result["missing_components"], result
    assert _capability_counts(ai) == before


