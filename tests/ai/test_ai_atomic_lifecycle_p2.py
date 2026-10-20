"""P2 W2 Red — AI `calculate_confirmed` 原子生命周期（005 §12.3/§16）。

REQ: REQ-9/10/11/12（002 §8）；AI 计算与 HTTP 共用 consume/complete/fail。

规格锚点（Approved，逐行）:
  - 005:42–55（可信来源表）：AI calculate「与 HTTP 共用 consume 快照，
    availability 传同一 dispatch；废除消费后重取 facts」（005:51）。
  - 005:690：`consume_for_execution(sid, confirmation_id, target, store, guard)`
    在 registry→store 锁序内验证并冻结 ExecutionContext；**此前任何失败均不
    消费**；**消费之后禁止再次 get_prepared 或重读 current bundle**；引擎只从
    快照解码独立工作副本。
  - 005:698：invalidate/in-flight 边界——「迟到 AI 响应在处理 tool_calls 前
    重新核验该轮 epoch/revision/consent，过期整轮丢弃」。
  - 005:913：`calculate_confirmed` 与 HTTP 共用 §12.3 的 consume/complete/
    fail 语义与 ExecutionContext 快照；响应可附运行时 `execution` 元数据，
    但不写入 core/binding。
  - 005:912：ToolContext 增加只读 precheck context（pinned bundle/四元组、
    current/next AvailabilityContext、调用开始 epoch/revision）。
  - 002 REQ-9（next 缺失→partial/null）、REQ-10（状态/null 一致）、
    REQ-11（确认/消费协议）、REQ-12（版本/绑定证据）。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - `ToolContext` 携带 `guard` 与本轮 `availability_context` 快照；工具把
    **同一** guard 对象传给 `consume_for_execution`；`availability_context`
    的 binding 为本轮 prepared 的四元组、current/next 为目标年与紧邻下一年。
  - 成功：`consume_for_execution` 恰一次（sid/confirmation_id/target/store/
    guard 身份正确）＋`complete_execution` 恰一次＋`fail_execution` 0 次；
    tool_results 一项 `outcome="executed"`；产生恰一条记录；同 confirmation
    再次执行 → `E_CONFIRMATION_CONSUMED`，记录数不增。
  - 消费返回后不得再调用 `registry.get_prepared` 或 `store.get_bundle`；
    金额/状态来自消费时快照（与 HTTP 基线逐键一致）。
  - 引擎失败：`fail_execution` 一次、complete 0、无记录；再次尝试不产生
    第二次执行/第二条记录。
  - guard/availability 失败（missing/unknown/blocked/不可核验）→ tool
    rejected 同码 `E_RULES_STATE_UNVERIFIABLE`，不消费，complete/fail 0；
    guard 恢复后同一 confirmation 仍可成功。
  - 错目标 → rejected `E_CONFIRMATION_TARGET_MISMATCH`，不消费；正确目标
    随后仍成功。
  - 迟到整轮丢弃（rev++ / clear）：释放外发后无任何已执行 tool 条目、无
    记录、无消费；HTTP 形状未钉死处只断言无副作用（不臆造状态码）。

本文件对 app.* 延迟导入（Red 失败原因＝缺失行为，而非收集期错误）；线程
同步仅用 `threading.Event`，无 sleep。
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping, MutableMapping

import httpx

from _ai_fakes import (
    ai_grant,
    ai_preview,
    ai_round,
    tool_results_of,
    wire_tool_calls_body,
)

BASE_ORIGIN = "http://127.0.0.1:8000"
PREPARE_PATH = "/api/v1/calc/prepare"
CONFIRM_PATH = "/api/v1/calc/confirm"
CHAT_PATH = "/api/v1/ai/chat"
SALARIES_TARGET = "/api/v1/calc/salaries-tax"
PROFITS_TARGET = "/api/v1/calc/profits-tax"
MSG = [{"role": "user", "content": "請幫我估算薪俸税"}]


# ---------------------------------------------------------------------------
# v2 薪俸事实（Annex 005 §3/§4；与 UI/验收 helper 同源的最小 Years 容器）
# ---------------------------------------------------------------------------
def _v2_year_block(start: int, finance: dict) -> dict:
    return {
        "participants": [{"person_id": "p1", "role": "self"}],
        "subject_facts": {},
        "household": {
            "marriage": [
                {
                    "from": f"{start}-04-01",
                    "to": f"{start + 1}-04-01",
                    "value": "unmarried",
                }
            ],
            "living_apart": [],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        "relations": [],
        "care_payments": [],
        "claims": [],
        "financials": {"p1": copy.deepcopy(finance)},
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
    }


def _salary_facts(*, with_next: bool) -> dict:
    finance = {
        "has_assessable_salary_income": True,
        "source_classification": {"salary": "confirmed_in_scope"},
        "properties": [],
        "businesses": [],
        "deduction_items": [],
        "employment_income_nai": "240000",
        "mpf_mandatory_contributions": "9000",
    }
    target = _v2_year_block(2025, finance)
    years = {"2025_26": target}
    if with_next:
        target["provisional_paid"] = "0"
        years["2026_27"] = _v2_year_block(2026, finance)
    return {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": years,
        "hk_presence_days": [],
        "assumptions": [],
    }


# ---------------------------------------------------------------------------
# 本地三段 helper（tests/ai/_ai_fakes.run_prepare 仍为 v1 载荷，P2 不复用）
# ---------------------------------------------------------------------------
def _fresh_if_match(api) -> dict[str, str]:
    r = api.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, (
        "meta/version 须附运行时 epoch/revision（S002 §4.1），"
        f"实际：{payload!r}"
    )
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def _prepare(api, *, with_next: bool = True) -> dict:
    r = api.post(
        PREPARE_PATH,
        json={
            "tax_type": "salaries_tax",
            "schema_version": "2.0.0",
            "input": _salary_facts(with_next=with_next),
        },
        headers=_fresh_if_match(api),
    )
    assert r.status_code == 201, (
        f"v2 薪俸 prepare 须 201（005 §4.6），实际 {r.status_code}: {r.text[:300]!r}"
    )
    return r.json()


def _confirm(api, prepared: dict) -> str:
    r = api.post(
        CONFIRM_PATH,
        json={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r.status_code == 200, f"confirm 须 200，实际 {r.status_code}: {r.text[:300]!r}"
    return r.json()["confirmation_id"]


def _http_baseline(api) -> dict:
    """HTTP 同事实三段基线（005:913：AI 与 HTTP 共用同一执行语义）。"""
    prepared = _prepare(api)
    cid = _confirm(api, prepared)
    r = api.post(SALARIES_TARGET, json={"confirmation_id": cid})
    assert r.status_code == 200, (
        f"HTTP 基线 execute 须 200，实际 {r.status_code}: {r.text[:300]!r}"
    )
    payload = r.json()
    assert payload.get("record_id"), payload
    return payload


# ---------------------------------------------------------------------------
# 观察 helper（仅读 registry 权威容器；不可观察 → 明确 held，不填零）
# ---------------------------------------------------------------------------
def _record_ids(registry, sid: str) -> frozenset[str]:
    with registry._lock:
        sessions = getattr(registry, "_sessions", None)
        assert isinstance(sessions, MutableMapping), (
            "INTERFACE_HELD：registry._sessions 权威容器不可观察（PQA-03）"
        )
        assert sid in sessions, f"状态前置失败：sid 不在 registry._sessions（{sid!r}）"
        records = sessions[sid].get("records")
        assert isinstance(records, Mapping), (
            "INTERFACE_HELD：session.records 非键集合表示（PQA-03）"
        )
        return frozenset(records.keys())


def _consumed(registry, sid: str, confirmation_id: str) -> bool:
    consumption = registry.get_consumption(sid, confirmation_id)
    return bool(consumption and consumption.get("consumed"))


def _executed_entries(payload: dict) -> list[dict]:
    return [
        entry
        for entry in payload.get("tool_results") or []
        if entry.get("outcome") == "executed"
    ]


def _single_executed(payload: dict) -> dict:
    entries = tool_results_of(payload)
    executed = _executed_entries(payload)
    assert len(executed) == 1, (
        f"本工具轮必须恰有一项 executed（C3.6/005:916），实际 {entries!r}"
    )
    return executed[0]


def _install_lifecycle_spies(monkeypatch, registry) -> dict[str, list[dict]]:
    """spy consume/complete/fail（保留原语义；记录入参与 core 副本）。"""
    calls: dict[str, list[dict]] = {"consume": [], "complete": [], "fail": []}

    original_consume = registry.consume_for_execution
    original_complete = registry.complete_execution
    original_fail = registry.fail_execution

    def consume(sid, confirmation_id, *, target, store, guard):
        calls["consume"].append(
            {
                "sid": sid,
                "confirmation_id": confirmation_id,
                "target": target,
                "store": store,
                "guard": guard,
            }
        )
        return original_consume(
            sid, confirmation_id, target=target, store=store, guard=guard
        )

    def complete(context, core, *, store, guard):
        calls["complete"].append(
            {"context": context, "core": copy.deepcopy(core)}
        )
        return original_complete(context, core, store=store, guard=guard)

    def fail(context, safe_error_code):
        calls["fail"].append(
            {"context": context, "safe_error_code": safe_error_code}
        )
        return original_fail(context, safe_error_code)

    monkeypatch.setattr(registry, "consume_for_execution", consume)
    monkeypatch.setattr(registry, "complete_execution", complete)
    monkeypatch.setattr(registry, "fail_execution", fail)
    return calls


class _BlockedGuard:
    """guard 接缝假件：availability 一律 blocked（既有 app.state 模式）。"""

    def availability(self, period: str) -> dict:
        return {"mode": "blocked", "states": []}


class _UnknownGuard:
    """guard 接缝假件：state=unknown（不可核验，不是允许）。"""

    def availability(self, period: str) -> dict:
        return {"mode": "ok", "states": ["unknown"]}


class _RaisingGuard:
    """guard 接缝假件：查询失败 → 不可核验（不泄露原文）。"""

    def availability(self, period: str) -> dict:
        raise RuntimeError("guard-probe-marker")


# ---------------------------------------------------------------------------
# 节点 1：成功路径＝一次 consume＋一次 complete＋executed 记录
# ---------------------------------------------------------------------------
def test_ai_calculate_success_consumes_once_completes_once(ai, monkeypatch) -> None:
    """005:690/:913（002 REQ-9..12）：成功恰一次 consume/complete、一次记录；
    同 confirmation 重复 → `E_CONFIRMATION_CONSUMED` 且记录数不增。"""
    baseline = _http_baseline(ai.api)
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)
    sid = ai.session_id()
    guard_before = ai.app.state.update_scheduler
    records_before = _record_ids(ai.registry, sid)

    calls = _install_lifecycle_spies(monkeypatch, ai.registry)

    resp, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_calc",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        ),
    )
    assert len(calls["consume"]) == 1, (
        "calculate_confirmed 必须与 HTTP 共用且恰调用一次 consume_for_execution"
        f"（005:690/:913），实际 {calls['consume']!r}"
    )
    assert resp.status_code == 200, (
        "AI 轮必须 200 并由工具层回带 tool_results（005:913/:916），"
        f"实际 {resp.status_code}: {resp.text[:400]!r}"
    )
    entry = _single_executed(resp.json())
    assert entry["name"] == "calculate_confirmed"

    consume_call = calls["consume"][0]
    assert consume_call["sid"] == sid
    assert consume_call["confirmation_id"] == confirmation_id
    assert consume_call["target"] == SALARIES_TARGET, (
        "consume 的 target 必须为确认绑定的执行目标（005:690）"
    )
    assert consume_call["store"] is ai.app.state.store
    assert consume_call["guard"] is guard_before, (
        "consume 必须接收同一 guard 快照对象（005:690/:912）"
    )

    assert len(calls["complete"]) == 1, (
        f"成功终态恰一次 complete_execution（005:690），实际 {calls['complete']!r}"
    )
    assert calls["fail"] == [], f"成功路径不得 fail：{calls['fail']!r}"

    result = entry["result"]
    record_id = result["record_id"]
    assert record_id in _record_ids(ai.registry, sid)
    assert _record_ids(ai.registry, sid) == records_before | {record_id}, (
        "AI 计算必须恰产生一条记录（005:913）"
    )
    record = ai.registry.get_record(sid, record_id)
    assert record is not None
    core = record["core"]
    assert core["status"] == baseline["status"], (
        "AI 记录状态必须与 HTTP 同事实基线一致（005:913）"
    )
    assert core["amounts"] == baseline["amounts"], (
        "AI 记录金额必须逐键等于 HTTP 基线（005:690/:913）"
    )

    # —— 同 confirmation 再次执行 → 已消费；记录数不增 ——
    resp2, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_again",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        ),
    )
    assert resp2.status_code == 200, resp2.text[:300]
    again = tool_results_of(resp2.json())[0]
    assert again["outcome"] == "rejected", again
    assert again.get("error", {}).get("code") == "E_CONFIRMATION_CONSUMED", (
        f"重复执行同一 confirmation 必须 E_CONFIRMATION_CONSUMED，实际 {again!r}"
    )
    assert len(calls["complete"]) == 1, "重复执行不得再次 complete（无双重记录）"
    assert _record_ids(ai.registry, sid) == records_before | {record_id}, (
        "重复执行不得新增记录（005:690）"
    )


# ---------------------------------------------------------------------------
# 节点 2：消费后不重取 prepared/current bundle
# ---------------------------------------------------------------------------
def test_ai_calculate_never_refetches_after_consume(ai, monkeypatch) -> None:
    """005:690：consume 返回后 `get_prepared`/`get_bundle` 调用数必须为 0；
    工具仍按 ExecutionContext 快照执行，金额等于基线。"""
    baseline = _http_baseline(ai.api)
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)
    sid = ai.session_id()

    state = {"consumed": False}
    original_consume = ai.registry.consume_for_execution
    get_prepared_calls = {"after": 0}
    get_bundle_calls = {"after": 0}

    def consume(sid_arg, confirmation_id_arg, *, target, store, guard):
        try:
            return original_consume(
                sid_arg,
                confirmation_id_arg,
                target=target,
                store=store,
                guard=guard,
            )
        finally:
            # finally 保证「返回」语义（异常路径不构成本节点成功前提）
            state["consumed"] = True

    original_get_prepared = ai.registry.get_prepared

    def get_prepared(sid_arg, prepared_id):
        if state["consumed"]:
            get_prepared_calls["after"] += 1
        return original_get_prepared(sid_arg, prepared_id)

    original_get_bundle = ai.app.state.store.get_bundle

    def get_bundle(bundle_id):
        if state["consumed"]:
            get_bundle_calls["after"] += 1
        return original_get_bundle(bundle_id)

    monkeypatch.setattr(ai.registry, "consume_for_execution", consume)
    monkeypatch.setattr(ai.registry, "get_prepared", get_prepared)
    monkeypatch.setattr(ai.app.state.store, "get_bundle", get_bundle)

    resp, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_calc",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        ),
    )
    assert state["consumed"] is True, (
        "calculate_confirmed 必须经 consume_for_execution 快照（005:690/:913）"
    )
    assert resp.status_code == 200, resp.text[:400]
    entry = _single_executed(resp.json())
    assert get_prepared_calls["after"] == 0, (
        "消费之后禁止再次 get_prepared（005:690）；"
        f"实际 {get_prepared_calls['after']} 次"
    )
    assert get_bundle_calls["after"] == 0, (
        "消费之后禁止重读 current bundle（005:690）；"
        f"实际 {get_bundle_calls['after']} 次"
    )

    record = ai.registry.get_record(sid, entry["result"]["record_id"])
    assert record is not None
    assert record["core"]["amounts"] == baseline["amounts"], (
        "工具必须从 ExecutionContext 快照执行并给出基金额（005:690/:913）"
    )


# ---------------------------------------------------------------------------
# 节点 3：引擎失败 → fail 一次、complete 0、无记录、无第二次执行
# ---------------------------------------------------------------------------
def test_ai_calculate_engine_failure_terminal(ai, monkeypatch) -> None:
    """005:690/:694/:913（002 REQ-11）：引擎抛错 → fail_execution 一次、
    complete 0、无记录；再次尝试不产生第二次执行/第二条记录。"""
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)
    sid = ai.session_id()
    records_before = _record_ids(ai.registry, sid)
    calls = _install_lifecycle_spies(monkeypatch, ai.registry)

    engine_calls = {"n": 0}

    def boom(tax_type, facts, bundle, *, availability=None):
        engine_calls["n"] += 1
        from app.core.errors import AppError

        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE", "测试注入：引擎执行失败（fail-closed）"
        )

    # 两种可能的派发绑定都替换（工具模块导入名 / records 模块名）
    import app.ai.tools as tools_module

    monkeypatch.setattr(tools_module, "run_engine", boom)
    import app.web.records as records_module

    monkeypatch.setattr(records_module, "run_engine", boom)

    def _send() -> tuple[int, dict]:
        resp, _ = ai_round(
            ai.api,
            ai.outbound,
            MSG,
            wire_tool_calls_body(
                [
                    {
                        "id": "call_calc",
                        "name": "calculate_confirmed",
                        "arguments": {"confirmation_id": confirmation_id},
                    }
                ]
            ),
        )
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        return resp.status_code, payload

    status1, payload1 = _send()
    assert status1 in (200, 422), (
        f"引擎 AppError 必须形成受控响应（005:913/:916），实际 {status1}"
    )
    if status1 == 200:
        for entry in payload1.get("tool_results") or []:
            assert entry.get("outcome") != "executed", (
                f"失败终态不得出现 executed 条目：{entry!r}"
            )
    else:
        assert payload1.get("error", {}).get("code") == "E_RULES_STATE_UNVERIFIABLE"

    assert engine_calls["n"] == 1, (
        f"失败轮必须恰执行一次引擎（005:690），实际 {engine_calls['n']}"
    )
    assert len(calls["fail"]) == 1, (
        "引擎失败必须 fail_execution 恰一次（005:690/:694），"
        f"实际 {calls['fail']!r}"
    )
    assert calls["complete"] == [], "失败终态不得 complete（005:690）"
    assert _record_ids(ai.registry, sid) == records_before, (
        "失败终态不得写记录（005:690/:694）"
    )

    # —— 再次尝试：无第二次执行/第二条记录 ——
    status2, payload2 = _send()
    assert status2 in (200, 409, 422), status2
    if status2 == 200:
        for entry in payload2.get("tool_results") or []:
            assert entry.get("outcome") != "executed", (
                f"已失败确认不得再次执行：{entry!r}"
            )
    assert engine_calls["n"] == 1, (
        f"失败后再次尝试不得重新运行引擎（005:694），实际 {engine_calls['n']}"
    )
    assert len(calls["fail"]) == 1, "不得产生第二次 fail（终态幂等）"
    assert calls["complete"] == []
    assert _record_ids(ai.registry, sid) == records_before


# ---------------------------------------------------------------------------
# 节点 4：guard/availability 失败不消费；恢复后同一 confirmation 可成功
# ---------------------------------------------------------------------------
def test_ai_calculate_guard_failure_not_consumed_then_recovers(ai, monkeypatch) -> None:
    """005:690（current 阻断或不核验→拒绝；此前任何失败均不消费）＋
    005:913（AI 与 HTTP 同 precheck）；002 REQ-11/12。"""
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)
    sid = ai.session_id()
    records_before = _record_ids(ai.registry, sid)
    real_guard = ai.app.state.update_scheduler
    calls = _install_lifecycle_spies(monkeypatch, ai.registry)

    fakes = [
        ("missing", None),
        ("blocked", _BlockedGuard()),
        ("unknown", _UnknownGuard()),
        ("query-failed", _RaisingGuard()),
    ]
    for label, fake in fakes:
        ai.app.state.update_scheduler = fake
        try:
            resp, _ = ai_round(
                ai.api,
                ai.outbound,
                MSG,
                wire_tool_calls_body(
                    [
                        {
                            "id": f"call_{label}",
                            "name": "calculate_confirmed",
                            "arguments": {"confirmation_id": confirmation_id},
                        }
                    ]
                ),
            )
        finally:
            ai.app.state.update_scheduler = real_guard

        assert not _consumed(ai.registry, sid, confirmation_id), (
            f"{label}: 任何失败均不得消费确认（005:690）"
        )
        assert resp.status_code == 200, (
            f"{label}: guard 失败必须形成 rejected 工具条目（005:916），"
            f"实际 {resp.status_code}: {resp.text[:300]!r}"
        )
        entry = tool_results_of(resp.json())[0]
        assert entry["outcome"] == "rejected", (
            f"{label}: guard 不可核验必须拒绝该调用（005:690），实际 {entry!r}"
        )
        assert entry.get("error", {}).get("code") == "E_RULES_STATE_UNVERIFIABLE", (
            f"{label}: 拒绝须同码 E_RULES_STATE_UNVERIFIABLE，实际 {entry!r}"
        )
        assert calls["complete"] == [], f"{label}: 失败不得 complete"
        assert calls["fail"] == [], f"{label}: 失败不得 fail"
        assert _record_ids(ai.registry, sid) == records_before, (
            f"{label}: 失败不得写记录"
        )

    # —— guard 恢复后同一 confirmation 仍可成功（未被消费）——
    resp_ok, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_ok",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        ),
    )
    assert resp_ok.status_code == 200, resp_ok.text[:400]
    entry_ok = _single_executed(resp_ok.json())
    assert len(calls["complete"]) == 1, (
        f"guard 恢复后成功须恰一次 complete（005:690），实际 {calls['complete']!r}"
    )
    assert calls["fail"] == []
    assert len(_record_ids(ai.registry, sid)) == len(records_before) + 1
    assert entry_ok["result"]["record_id"] in _record_ids(ai.registry, sid)


# ---------------------------------------------------------------------------
# 节点 5：错目标不消费；正确目标随后成功
# ---------------------------------------------------------------------------
def test_ai_calculate_wrong_target_not_consumed(ai, monkeypatch) -> None:
    """005:690/§13：execute_target 不符 → E_CONFIRMATION_TARGET_MISMATCH，
    未消费；随后正确目标经同一原子路径成功。"""
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)
    sid = ai.session_id()
    records_before = _record_ids(ai.registry, sid)
    calls = _install_lifecycle_spies(monkeypatch, ai.registry)

    resp_wrong, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_wrong",
                    "name": "calculate_confirmed",
                    "arguments": {
                        "confirmation_id": confirmation_id,
                        "execute_target": PROFITS_TARGET,
                    },
                }
            ]
        ),
    )
    assert resp_wrong.status_code == 200, resp_wrong.text[:300]
    wrong = tool_results_of(resp_wrong.json())[0]
    assert wrong["outcome"] == "rejected", wrong
    assert wrong.get("error", {}).get("code") == "E_CONFIRMATION_TARGET_MISMATCH"
    assert wrong.get("result") is None
    assert calls["complete"] == [] and calls["fail"] == []
    assert _record_ids(ai.registry, sid) == records_before
    assert not _consumed(ai.registry, sid, confirmation_id), (
        "错目标不得消费确认（005:690/§13）"
    )

    # —— 同一 confirmation 在正确目标仍可成功（证明未消费）——
    consume_before = len(calls["consume"])
    resp_ok, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_ok",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        ),
    )
    assert len(calls["consume"]) == consume_before + 1, (
        "正确目标必须经 consume_for_execution 原子消费（005:690/:913），"
        f"实际 {calls['consume']!r}"
    )
    assert resp_ok.status_code == 200, resp_ok.text[:400]
    entry_ok = _single_executed(resp_ok.json())
    assert len(calls["complete"]) == 1
    assert calls["fail"] == []
    assert len(_record_ids(ai.registry, sid)) == len(records_before) + 1
    assert entry_ok["result"]["record_id"] in _record_ids(ai.registry, sid)


# ---------------------------------------------------------------------------
# 节点 6a/6b：迟到整轮丢弃（rev++ / clear）
# ---------------------------------------------------------------------------
def _clone_chat_client(ai) -> httpx.Client:
    from app.config import SESSION_COOKIE_NAME

    ai.api._token()
    raw = ai.api.client.cookies.get(SESSION_COOKIE_NAME)
    assert raw, "未能取得测试会话 cookie（C5.3）"
    client = httpx.Client(
        transport=httpx.ASGITransport(app=ai.app), base_url=BASE_ORIGIN
    )
    client.cookies.set(SESSION_COOKIE_NAME, raw)
    return client


def _blocking_outbound(ai, entered: threading.Event, release: threading.Event):
    original_send = ai.outbound.send

    def send(upstream_endpoint, api_key, frozen, **kwargs):
        entered.set()
        assert release.wait(10), "外发释放事件超时（确定性失败，不 sleep 重试）"
        return original_send(upstream_endpoint, api_key, frozen, **kwargs)

    return send


def _late_round(
    ai, monkeypatch, *, confirmation_id: str, mutate, thread_result: dict
) -> dict:
    """外发阻塞 → mutate()（rev++/clear）→ 释放；返回主线程观察。"""
    sid = ai.session_id()
    records_before = _record_ids(ai.registry, sid)
    ai.outbound.script = [
        wire_tool_calls_body(
            [
                {
                    "id": "call_prop",
                    "name": "propose_input",
                    "arguments": {
                        "input": {
                            "tax_type": "salaries_tax",
                            "schema_version": "2.0.0",
                            "input": {},
                        }
                    },
                },
                {
                    "id": "call_calc",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                },
            ]
        )
    ]
    preview = ai_preview(ai.api, MSG)
    ai_grant(ai.api, preview["consent_id"])
    token = ai.api._token()

    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(ai.outbound, "send", _blocking_outbound(ai, entered, release))

    chat_client = _clone_chat_client(ai)

    def run() -> None:
        try:
            resp = chat_client.post(
                CHAT_PATH,
                json={"consent_id": preview["consent_id"], "messages": MSG},
                headers={"Origin": BASE_ORIGIN, "X-CSRF-Token": token},
            )
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            thread_result.update({"status": resp.status_code, "payload": payload})
        except BaseException as exc:  # noqa: BLE001 - 线程异常必须显式回传
            thread_result["error"] = exc
        finally:
            chat_client.close()

    thread = threading.Thread(target=run, name="late-ai-round", daemon=True)
    thread.start()
    assert entered.wait(10), "外发接缝未被调用（C4.6 ⑦段）"
    try:
        mutate()
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive(), "迟到轮线程未结束（确定性失败）"
    assert "error" not in thread_result, (
        f"迟到轮请求异常：{thread_result.get('error')!r}"
    )
    return {
        "sid": sid,
        "records_before": records_before,
        "confirmation_id": confirmation_id,
        "result": thread_result,
    }


def _assert_late_round_dropped(observed: dict) -> None:
    payload = observed["result"].get("payload") or {}
    executed = _executed_entries(payload)
    assert not executed, (
        "过期整轮必须在处理 tool_calls 前丢弃（005:698）："
        f"不得有已执行工具结果，实际 {executed!r}"
    )
    assert (
        _record_ids(observed["registry"], observed["sid"])
        == observed["records_before"]
    ), "迟到轮不得写记录（005:698）"
    consumption = observed["registry"].get_consumption(
        observed["sid"], observed["confirmation_id"]
    )
    assert not (consumption and consumption.get("consumed")), (
        f"迟到轮不得消费确认（005:698），实际 {consumption!r}"
    )


def test_ai_late_round_dropped_after_input_revision(ai, monkeypatch) -> None:
    """005:698：外发阻塞期间 input revision++（invalidate）→ 释放后整轮丢弃；
    无已执行 tool 结果、无记录、无消费。"""
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)

    def mutate() -> None:
        version = ai.api.get("/api/v1/meta/version").json()
        r = ai.api.post(
            "/api/v1/calc/invalidate",
            json={
                "expected_session_epoch": int(version["session_epoch"]),
                "expected_input_revision": int(version["input_revision"]),
            },
        )
        assert r.status_code == 200, f"invalidate 须 200，实际 {r.status_code}: {r.text}"

    thread_result: dict = {}
    observed = _late_round(
        ai,
        monkeypatch,
        confirmation_id=confirmation_id,
        mutate=mutate,
        thread_result=thread_result,
    )
    observed["registry"] = ai.registry
    _assert_late_round_dropped(observed)


def test_ai_late_round_dropped_after_session_clear(ai, monkeypatch) -> None:
    """005:698/§12.3：外发阻塞期间 clear（epoch 边界）→ 释放后整轮丢弃；
    无已执行 tool 结果、无记录、无消费。"""
    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)

    def mutate() -> None:
        r = ai.api.delete("/api/v1/session")
        assert r.status_code == 200, f"clear 须 200，实际 {r.status_code}: {r.text}"

    thread_result: dict = {}
    observed = _late_round(
        ai,
        monkeypatch,
        confirmation_id=confirmation_id,
        mutate=mutate,
        thread_result=thread_result,
    )
    observed["registry"] = ai.registry
    _assert_late_round_dropped(observed)


# ---------------------------------------------------------------------------
# 节点 7：ToolContext guard/availability precheck（同一快照进 consume）
# ---------------------------------------------------------------------------
def test_ai_tool_context_carries_guard_and_availability_snapshot(
    ai, monkeypatch
) -> None:
    """005:912/:913：ToolContext 只读 precheck（pinned 四元组、current/next
    AvailabilityContext）；同一 guard 快照传入 consume_for_execution。"""
    import app.api.routes as routes_module

    prepared = _prepare(ai.api)
    confirmation_id = _confirm(ai.api, prepared)
    guard_before = ai.app.state.update_scheduler
    captured: dict = {}

    original_execute = routes_module.execute_tool_calls

    def execute(calls, ctx):
        captured["ctx"] = ctx
        return original_execute(calls, ctx)

    monkeypatch.setattr(routes_module, "execute_tool_calls", execute)
    calls = _install_lifecycle_spies(monkeypatch, ai.registry)

    resp, _ = ai_round(
        ai.api,
        ai.outbound,
        MSG,
        wire_tool_calls_body(
            [
                {
                    "id": "call_calc",
                    "name": "calculate_confirmed",
                    "arguments": {"confirmation_id": confirmation_id},
                }
            ]
        ),
    )
    assert "ctx" in captured, "聊天路由必须执行 C3.6 工具层（005:913）"
    ctx = captured["ctx"]
    assert getattr(ctx, "guard", None) is guard_before, (
        "ToolContext 必须携带本轮 guard 快照（005:912）"
    )
    availability = getattr(ctx, "availability_context", None)
    assert isinstance(availability, Mapping), (
        "ToolContext 必须携带本轮 AvailabilityContext 快照（005:912）"
    )
    assert availability.get("binding") == prepared["expected_binding"], (
        "precheck context 的 binding 必须为本轮 prepared 四元组（005:912/:913）"
    )
    assert availability.get("current", {}).get("year") == "2025_26", availability
    assert availability.get("next", {}).get("year") == "2026_27", availability

    assert resp.status_code == 200, resp.text[:400]
    assert len(calls["consume"]) == 1, (
        "成功路径必须经 consume_for_execution（005:690/:913）"
    )
    assert calls["consume"][0]["guard"] is getattr(ctx, "guard"), (
        "同一 guard 快照必须传入 consume_for_execution（005:912/:913）"
    )
