"""REQ-11 模型工具层与确认门控（M7 Red；Annex C C3.6；SDD §10 REQ-11 命名测试）。

命名测试（本文件）:
  - test_structured_confirm_before_calc
  - test_explanation_amounts_from_tool_result_only
  - test_input_mutation_invalidates_results
  - test_unsourced_rate_refused
  - test_new_tool_recommendation_needs_confirmation
  - test_confirmation_wrong_target_not_consumed
  - test_unknown_tool_no_action_strict_params_rejected

REQ: REQ-11（意图限于受支持任务；计算前结构化确认；解释金额/年度/引用必须
来自工具结果；输入变更→待确认结果失效（已完成记录按原 binding 保留，
C3.2.4）；无来源税率拒绝；新工具建议须确认）。
规格锚点:
  - Annex C C3.6：工具注册表封闭 4 工具（propose_input/validate_input/
    calculate_confirmed/read_result）；无 settings/update/rollback 工具；
    calculate_confirmed 仅能引用服务器已存在确认能力（无伪造用户标志）；
    未知 tool → 无动作（注记 unknown_tool_ignored）；arguments strict
    （未知/多余/类型不符 → 拒绝该调用，无部分执行）；单条消息 tool_calls
    ≤4（违规整体 E_MODEL_BAD_RESPONSE）；arguments 解析失败/重复 id →
    E_MODEL_BAD_RESPONSE。
  - Annex C C3.2.2/C3.2.3：确认能力绑 execute_target；错目标 409
    E_CONFIRMATION_TARGET_MISMATCH 且不消费。
  - Annex C C3.2.4：输入修订 → 挂起能力失效；已完成记录按原 binding 保留。
  - SDD §7/§9：模型路径同样必须经结构化确认能力，不得绕过。
期望值来源: 结构/状态断言（outcome/错误码/金额仅来自工具结果）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - /api/v1/ai/chat 对含 tool_calls 的 wire 响应执行 C3.6 工具层并在响应
    回带 "tool_results": [{"id","name","outcome","note","needs_confirmation",
    "error","result"}]：
      * outcome ∈ {executed, rejected, ignored}；
      * 未知工具 → outcome="ignored"＋note="unknown_tool_ignored"＋
        needs_confirmation=True（新工具建议须用户确认，不自动执行）；
      * strict 参数违规（多余/类型不符/缺必填）→ outcome="rejected"＋
        error.code="E_MODEL_BAD_RESPONSE"＋无 result（无部分执行）；
      * calculate_confirmed 伪造/缺失确认能力 → rejected＋
        error.code="E_CONFIRMATION_REQUIRED"；错 execute_target → rejected＋
        error.code="E_CONFIRMATION_TARGET_MISMATCH"（确认不被消费）；
      * read_result 执行成功 → result={"record_id","amounts"(六键，来自
        服务器记录)…}；
      * 批次 >4／arguments 非法 JSON／重复 tool_call id → 整条消息 502
        E_MODEL_BAD_RESPONSE。
  - 纯文本轮响应标记 unconfirmed=True；模型自由文本金额不进入任何权威
    金额字段（amounts 六键仅来自工具结果/本地引擎）。
"""

from __future__ import annotations

import json

from _ai_fakes import (
    AMOUNT_KEYS,
    REGISTRY_TOOL_NAMES,
    ai_round,
    complete_calc,
    run_confirm,
    run_prepare,
    salaries_input,
    tool_results_of,
    wire_text_body,
    wire_tool_calls_body,
)

MSG = [{"role": "user", "content": "請幫我估算薪俸税"}]


def _error_code(resp) -> str:
    payload = resp.json()
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状：{payload!r}"
    return err["code"]


def test_structured_confirm_before_calc(ai) -> None:
    """模型路径不得绕过结构化确认：伪造 confirmation_id / 缺失确认的直算 /
    未注册直算工具 → 一律无计算发生；真实确认能力不受伪造尝试影响。"""
    api = ai.api
    prepared = run_prepare(api)
    real_confirmation = run_confirm(api, prepared)

    # —— 轮 1：伪造确认能力 → 拒绝（E_CONFIRMATION_REQUIRED；无计算）——
    forged = wire_tool_calls_body(
        [{"id": "call_1", "name": "calculate_confirmed",
          "arguments": {"confirmation_id": "c-forged-" + "0" * 16}}]
    )
    resp, _ = ai_round(api, ai.outbound, MSG, forged)
    assert resp.status_code == 200, (
        f"假出站轮必须 200（C4.6 ⑦段经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    results = tool_results_of(resp.json())
    entry = results[0]
    assert entry["name"] == "calculate_confirmed"
    assert entry["outcome"] == "rejected", (
        f"伪造确认能力必须拒绝该调用（C3.6 仅能引用服务器已存在确认），"
        f"实际 {entry!r}"
    )
    assert entry.get("error", {}).get("code") == "E_CONFIRMATION_REQUIRED"
    assert entry.get("result") is None, "拒绝的调用不得产生任何结果（无部分执行）"
    assert "amounts" not in resp.json(), "拒绝的模型调用不得携带权威金额"

    # —— 轮 2：缺 confirmation_id 直算（绕过确认的尝试）→ strict 拒绝 ——
    no_confirm = wire_tool_calls_body(
        [{"id": "call_2", "name": "calculate_confirmed",
          "arguments": {"input": salaries_input()}}]
    )
    resp2, _ = ai_round(api, ai.outbound, MSG, no_confirm)
    assert resp2.status_code == 200, resp2.text[:300]
    entry2 = tool_results_of(resp2.json())[0]
    assert entry2["outcome"] == "rejected", (
        f"缺确认能力直算必须拒绝（结构化确认不可绕过），实际 {entry2!r}"
    )

    # —— 轮 3：未注册的直算工具 → 无动作（unknown_tool_ignored）——
    unknown = wire_tool_calls_body(
        [{"id": "call_3", "name": "calculate_tax_directly",
          "arguments": {"employment_income": "240000"}}]
    )
    resp3, _ = ai_round(api, ai.outbound, MSG, unknown)
    assert resp3.status_code == 200, resp3.text[:300]
    entry3 = tool_results_of(resp3.json())[0]
    assert entry3["outcome"] == "ignored" and entry3.get("note") == "unknown_tool_ignored"
    assert entry3.get("result") is None and "amounts" not in resp3.json()

    # —— 真实确认能力未被任何伪造尝试消费：直接执行仍成功 ——
    direct = api.post(
        "/api/v1/calc/salaries-tax", json={"confirmation_id": real_confirmation}
    )
    assert direct.status_code == 200, (
        f"真实确认能力不受模型伪造尝试影响（C3.2.3），"
        f"实际 {direct.status_code}: {direct.text[:300]!r}"
    )


def test_explanation_amounts_from_tool_result_only(ai) -> None:
    """解释中的金额/年度只来自工具结果：read_result 的金额＝服务器记录金额；
    模型自由文本声称的金额不进入任何权威金额字段。"""
    api = ai.api
    done = complete_calc(api)
    record_id = done["record_id"]

    # 服务器记录的权威金额（本地引擎产出）
    record = api.get(f"/api/v1/records/{record_id}").json()["record"]
    authoritative_amounts = record["amounts"]
    assert set(authoritative_amounts.keys()) == set(AMOUNT_KEYS)

    body = wire_tool_calls_body(
        [{"id": "call_r1", "name": "read_result",
          "arguments": {"record_id": record_id}}],
        content="根據我的計算，你最終應繳稅款 1 元（2024/25 年度）。",
    )
    resp, _ = ai_round(api, ai.outbound, MSG, body)
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    payload = resp.json()
    entry = tool_results_of(payload)[0]
    assert entry["name"] == "read_result" and entry["outcome"] == "executed", (
        f"read_result 引用真实记录必须执行，实际 {entry!r}"
    )
    result = entry["result"]
    assert result["record_id"] == record_id
    assert result["amounts"] == authoritative_amounts, (
        "解释金额必须逐键来自工具结果（服务器记录），不得改写"
    )
    # 模型自由文本不作权威显示：顶层不得出现金额字段；文本原样保留为字符串
    for key in AMOUNT_KEYS:
        assert key not in payload, f"模型轮响应不得携带权威金额字段 {key!r}"
    assert payload.get("content") == "根據我的計算，你最終應繳稅款 1 元（2024/25 年度）。"


def test_input_mutation_invalidates_results(ai) -> None:
    """输入变更 → 旧授权/待用结果失效（E_CONSENT_STALE）＋须新预览新授权；
    已完成记录按原 binding 保留可查（C3.2.4）。"""
    api = ai.api
    done = complete_calc(api)
    record_id = done["record_id"]

    preview_resp = api.post("/api/v1/ai/consent/preview", json={"messages": MSG})
    assert preview_resp.status_code in (200, 201), preview_resp.text[:300]
    consent_id = preview_resp.json()["consent_id"]
    grant = api.post("/api/v1/ai/consent", json={"consent_id": consent_id})
    assert grant.status_code in (200, 201), grant.text[:300]

    # —— 输入修订（新一轮 prepare → input_revision++）——
    run_prepare(api, salaries_input(employment_income="300000"))

    stale = api.post("/api/v1/ai/chat", json={"consent_id": consent_id, "messages": MSG})
    assert stale.status_code == 409 and _error_code(stale) == "E_CONSENT_STALE", (
        f"输入修订必须使旧授权失效（C3.2.4/C4.6 input_revision 绑定），"
        f"实际 {stale.status_code}: {stale.text[:300]!r}"
    )
    assert ai.outbound.calls == [], "失效授权不得外发（绝不自动补发）"

    # —— 已完成记录按原 binding 保留 ——
    kept = api.get(f"/api/v1/records/{record_id}")
    assert kept.status_code == 200, "输入变更不得废除已完成记录（C3.2.4）"

    # —— 新预览＋新授权后 AI 轮可用（经假出站）——
    resp, _ = ai_round(api, ai.outbound, MSG, wire_text_body("已按修订后资料处理"))
    assert resp.status_code == 200, (
        f"重新授权后外发必须可用（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )


def test_unsourced_rate_refused(ai) -> None:
    """无来源税率：模型文本声称的税率/税额不作权威显示（unconfirmed 标记）；
    工具注册表封闭（无任何设率/取率/网络工具），税率仅经已验证规则束。"""
    api = ai.api
    unsourced = (
        "2025/26 標準稅率為 99%，應繳稅款 237600 元"
        "（我自己的估計，無官方來源）。"
    )
    resp, _ = ai_round(api, ai.outbound, MSG, wire_text_body(unsourced))
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    payload = resp.json()
    assert payload.get("content") == unsourced, "模型文本须原样返回（不采信）"
    assert payload.get("unconfirmed") is True, (
        "无工具结果支撑的模型文本必须标记为未确认（不据以显示税率/税额）"
    )
    for key in AMOUNT_KEYS:
        assert key not in payload, f"无来源税率轮不得产生权威金额字段 {key!r}"

    # 外发负载的工具注册表封闭：无设率/取率/网络类工具
    sent = json.loads(ai.outbound.calls[0]["frozen"].decode("utf-8"))
    tools = sent.get("tools") or []
    names = {
        (t.get("function") or {}).get("name") or t.get("name") for t in tools
    }
    assert names and names <= REGISTRY_TOOL_NAMES, (
        f"外发工具注册表必须封闭为 C3.6 四工具（不得新增设率/取率/网络工具），"
        f"实际 {names!r}"
    )


def test_new_tool_recommendation_needs_confirmation(ai) -> None:
    """新工具建议须确认：模型提议的未注册工具 → 无动作＋注记＋
    needs_confirmation（交用户决定，不自动执行）。"""
    api = ai.api
    proposal = wire_tool_calls_body(
        [{"id": "call_prop_1", "name": "fetch_web_page",
          "arguments": {"url": "https://www.ird.gov.hk"}}]
    )
    resp, _ = ai_round(api, ai.outbound, MSG, proposal)
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    entry = tool_results_of(resp.json())[0]
    assert entry["name"] == "fetch_web_page"
    assert entry["outcome"] == "ignored", (
        f"未注册工具必须无动作（不猜测映射、不报 5xx），实际 {entry!r}"
    )
    assert entry.get("note") == "unknown_tool_ignored"
    assert entry.get("needs_confirmation") is True, (
        "新工具建议必须显式交用户确认（REQ-11），不得静默忽略或自动执行"
    )
    assert entry.get("result") is None and "amounts" not in resp.json()


def test_confirmation_wrong_target_not_consumed(ai) -> None:
    """确认能力绑 execute_target：模型以错目标端点引用确认 → 409 语义拒绝且
    不消费；随后正确入口执行仍成功。"""
    api = ai.api
    prepared = run_prepare(api)
    confirmation_id = run_confirm(api, prepared)

    wrong_target = wire_tool_calls_body(
        [{"id": "call_wt", "name": "calculate_confirmed",
          "arguments": {"confirmation_id": confirmation_id,
                        "execute_target": "/api/v1/calc/profits-tax"}}]
    )
    resp, _ = ai_round(api, ai.outbound, MSG, wrong_target)
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    entry = tool_results_of(resp.json())[0]
    assert entry["outcome"] == "rejected", f"错目标必须拒绝，实际 {entry!r}"
    assert entry.get("error", {}).get("code") == "E_CONFIRMATION_TARGET_MISMATCH"
    assert entry.get("result") is None, "错目标不得产生计算结果"

    # —— 未消费证明：正确目标执行仍成功（C3.2.2/C3.2.3）——
    direct = api.post(
        "/api/v1/calc/salaries-tax", json={"confirmation_id": confirmation_id}
    )
    assert direct.status_code == 200, (
        f"错目标尝试不得消费确认能力（409 不消费），"
        f"实际 {direct.status_code}: {direct.text[:300]!r}"
    )


def test_unknown_tool_no_action_strict_params_rejected(ai) -> None:
    """未知 tool 无动作＋注记；strict 参数（多余/类型不符）拒绝该调用；
    批次 >4／arguments 非法 JSON／重复 tool_call id → 整体 502。"""
    api = ai.api

    # —— 轮 1：三条调用（≤4）：未知 / 多余参数 / 类型不符 ——
    mixed = wire_tool_calls_body(
        [
            {"id": "call_u", "name": "web_search", "arguments": {"query": "税率"}},
            {"id": "call_e", "name": "read_result",
             "arguments": {"record_id": "r-1", "extra_flag": True}},
            {"id": "call_t", "name": "read_result", "arguments": {"record_id": 123}},
        ]
    )
    resp, _ = ai_round(api, ai.outbound, MSG, mixed)
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    results = tool_results_of(resp.json())
    by_id = {r["id"]: r for r in results}
    assert set(by_id) == {"call_u", "call_e", "call_t"}, (
        f"tool_results 必须逐 call 一一对应，实际 {results!r}"
    )
    assert by_id["call_u"]["outcome"] == "ignored", (
        f"未知 tool 必须无动作＋注记，实际 {by_id['call_u']!r}"
    )
    assert by_id["call_u"].get("note") == "unknown_tool_ignored"
    for cid in ("call_e", "call_t"):
        entry = by_id[cid]
        assert entry["outcome"] == "rejected", (
            f"strict 参数违规（多余/类型不符）必须拒绝该调用 {cid}，实际 {entry!r}"
        )
        assert entry.get("error", {}).get("code") == "E_MODEL_BAD_RESPONSE"
        assert entry.get("result") is None, "拒绝的调用不得有部分执行"

    # —— 轮 2：单条消息 tool_calls >4 → 整体 E_MODEL_BAD_RESPONSE（502）——
    five = wire_tool_calls_body(
        [
            {"id": f"call_b{i}", "name": "read_result", "arguments": {"record_id": "r-1"}}
            for i in range(5)
        ]
    )
    resp2, _ = ai_round(api, ai.outbound, MSG, five)
    assert resp2.status_code == 502 and _error_code(resp2) == "E_MODEL_BAD_RESPONSE", (
        f"单条消息 tool_calls >4 必须整体 502（C3.6 批次限额），"
        f"实际 {resp2.status_code}: {resp2.text[:300]!r}"
    )

    # —— 轮 3：arguments 非法 JSON 字符串 → 502 ——
    bad_json = wire_tool_calls_body(
        [{"id": "call_j", "name": "read_result",
          "arguments": "{not-json"}]
    )
    resp3, _ = ai_round(api, ai.outbound, MSG, bad_json)
    assert resp3.status_code == 502 and _error_code(resp3) == "E_MODEL_BAD_RESPONSE", (
        f"arguments 解析失败必须 502 E_MODEL_BAD_RESPONSE，"
        f"实际 {resp3.status_code}: {resp3.text[:300]!r}"
    )

    # —— 轮 4：重复 tool_call id → 502 ——
    dup = wire_tool_calls_body(
        [
            {"id": "call_d", "name": "read_result", "arguments": {"record_id": "r-1"}},
            {"id": "call_d", "name": "read_result", "arguments": {"record_id": "r-2"}},
        ]
    )
    resp4, _ = ai_round(api, ai.outbound, MSG, dup)
    assert resp4.status_code == 502 and _error_code(resp4) == "E_MODEL_BAD_RESPONSE", (
        f"重复 tool_call id 必须 502 E_MODEL_BAD_RESPONSE，"
        f"实际 {resp4.status_code}: {resp4.text[:300]!r}"
    )
