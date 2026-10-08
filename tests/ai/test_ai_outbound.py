"""REQ-14 模型出站与同意管线（M7 Red；Annex C C4/C5；SDD §10 REQ-14 命名测试）。

命名测试（本文件）:
  - test_local_calc_without_model
  - test_consent_freezes_full_bytes_incl_history_system_tools
  - test_consent_one_use_consumed_before_send
  - test_context_change_new_grant
  - test_keys_server_only_never_browser_log_export
  - test_model_failure_no_leak_form_preserved
  - test_clear_session_memory_only_external_prints_kept
  - test_model_minimal_payload_no_full_export_or_unused_facts

REQ: REQ-14（本地计算独立于模型；同意冻结完整字节含 history/system/tools＋
hash＋一次性＋发送前消费＋变更即失效；默认最小化外发；密钥仅服务端；失败
分类不泄密不丢表单；会话内存态可清空）。
规格锚点:
  - Annex C C4.1（固定 wire profile：tools 必需；HTTPX content=frozen_bytes）。
  - Annex C C4.4（请求超时 30s；history ≤20 条）。
  - Annex C C4.5（预览＝确切序列化请求字节（排除 Authorization）＋SHA-256）。
  - Annex C C4.6（七段管线；一次 serialize 冻结；prepared→granted→consumed；
    发送前原子消费；重试/上下文/端点/模型/工具结果/rules 变化 → 新预览＋
    新授权；默认最小化：不发送完整 export/全部确认 facts/未用信息，白名单
    外字段一律不进入序列化）。
  - Annex C C4.8（状态转移表）。
  - Annex C C5.4（双 epoch：模型配置变更 → consent_epoch++，未消费授权失效）。
  - Annex C C3.7（E_MODEL_UNCONFIGURED 409；E_MODEL_AUTH/RATE_LIMIT/TIMEOUT/
    BAD_RESPONSE 502 保留表单与结果）。
  - Annex C C4.7（错误脱敏：不展示上游响应体/密钥）。
期望值来源: 结构/状态断言（hash、错误码、字节内容标记）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 预览负载字段白名单恰为 {endpoint, model, profile, messages, tools}；
    tools 为 C3.6 封闭注册表（4 工具）；messages 逐字含 system/history。
  - payload_sha256 ＝冻结字节（app.core.hashing.canonical_json_bytes 形态）
    的 SHA-256；payload_bytes ＝冻结字节数。
  - 路由外发经 app.state.model_outbound（见 tests/ai/conftest.py）；接缝
    异常 httpx.TimeoutException → 502 E_MODEL_TIMEOUT；返回
    {"status_code","body"}：401/403→E_MODEL_AUTH、429→E_MODEL_RATE_LIMIT、
    ≥400→E_MODEL_BAD_RESPONSE；2xx 形状不符（choices[].message 缺失）→
    E_MODEL_BAD_RESPONSE。
"""

from __future__ import annotations

import hashlib
import httpx
import json
import re

from _ai_fakes import (
    AMOUNT_KEYS,
    MODEL_API_KEY_ENV,
    MODEL_ENDPOINT_ENV,
    MODEL_NAME_ENV,
    REGISTRY_TOOL_NAMES,
    TEST_API_KEY,
    TEST_MODEL_ENDPOINT,
    TEST_MODEL_ENDPOINT_B,
    TEST_MODEL_NAME,
    ai_chat,
    ai_grant,
    ai_preview,
    ai_round,
    complete_calc,
    publish_alt_bundle,
    run_prepare,
    wire_text_body,
    wire_tool_calls_body,
)

MSG = [{"role": "user", "content": "請幫我核对薪俸税输入资料"}]
HISTORY = [
    {"role": "system", "content": "你是本地香港税务估算助手；金额一律来自工具结果。"},
    {"role": "user", "content": "請幫我核对薪俸税输入资料"},
    {"role": "assistant", "content": "已收到；請確認收入事实。"},
    {"role": "user", "content": "收入 240000，MPF 9000"},
]


def _error_code(resp) -> str:
    payload = resp.json()
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状：{payload!r}"
    return err["code"]


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_local_calc_without_model(ai_offline, monkeypatch) -> None:
    """模型未配置：本地计算全端点可用；AI 出站 409 E_MODEL_UNCONFIGURED
    （不尝试发送）；配置变化后旧授权失效（C5.4）须新预览新授权。"""
    api = ai_offline.api
    fake = ai_offline.outbound

    # —— 本地全面可用（模型缺失不影响任何本地路径）——
    assert api.get("/api/v1/meta/version").status_code == 200
    assert api.get("/api/v1/meta/coverage").status_code == 200
    assert api.get("/coverage").status_code == 200
    done = complete_calc(api)
    record_id = done["record_id"]
    assert record_id, "本地三段流程必须产出记录（模型未配置不阻断计算）"
    assert api.get(f"/api/v1/records/{record_id}").status_code == 200
    assert api.get(f"/api/v1/records/{record_id}/download").status_code == 200
    print_ok = api.post("/api/v1/report/print", json={"record_id": record_id})
    assert print_ok.status_code == 200, "打印不依赖模型（本地可用）"
    replay_ok = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay_ok.status_code == 200, "重放不依赖模型（本地可用）"
    update_ok = api.post("/api/v1/update/check")
    assert update_ok.status_code in (200, 202), "规则检查不依赖模型（REQ-17）"

    # —— AI 出站：未配置 → 409 E_MODEL_UNCONFIGURED；不发送 ——
    preview = ai_preview(api, MSG)
    ai_grant(api, preview["consent_id"])
    unconfigured = ai_chat(api, preview["consent_id"], MSG)
    assert unconfigured.status_code == 409 and _error_code(unconfigured) == "E_MODEL_UNCONFIGURED", (
        f"模型未配置必须 409 E_MODEL_UNCONFIGURED（本地计算可用），"
        f"实际 {unconfigured.status_code}: {unconfigured.text[:300]!r}"
    )
    assert fake.calls == [], "未配置不得尝试外发"

    # —— E_MODEL_UNCONFIGURED 不影响计算路径：本地计算依旧可用 ——
    again = complete_calc(api, {"year_of_assessment": "2025_26",
                                "employment_income": "300000",
                                "mpf_mandatory_contributions": "15000",
                                "married_status": {"value": "single"}})
    assert again["record_id"], "409 后本地计算必须照常完成"

    # —— 模型配置变化（C5.4）→ 未消费/已授予授权一律失效 ——
    monkeypatch.setenv(MODEL_ENDPOINT_ENV, TEST_MODEL_ENDPOINT)
    monkeypatch.setenv(MODEL_NAME_ENV, TEST_MODEL_NAME)
    monkeypatch.setenv(MODEL_API_KEY_ENV, TEST_API_KEY)
    stale = ai_chat(api, preview["consent_id"], MSG)
    assert stale.status_code == 409 and _error_code(stale) == "E_CONSENT_STALE", (
        f"模型配置变更必须使已授予授权失效（C5.4 consent_epoch++），"
        f"实际 {stale.status_code}: {stale.text[:300]!r}"
    )
    # —— 新预览＋新授权后可用（经假出站；本地能力全程不受影响）——
    resp, _ = ai_round(api, fake, MSG, wire_text_body("已连线"))
    assert resp.status_code == 200, (
        f"重新授权后外发必须可用（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )


def test_consent_freezes_full_bytes_incl_history_system_tools(ai) -> None:
    """冻结负载含 system/history 全量消息＋tools 注册表＋SHA-256/字节数
    （C4.1 tools 必需；C4.5 确切字节）。"""
    api = ai.api

    preview = ai_preview(api, HISTORY)
    payload = preview["payload"]
    assert payload["messages"] == HISTORY, (
        "冻结负载必须逐字含完整消息上下文（system＋多轮 history；§7/C4.6 ①段）"
    )

    # —— tools 注册表随负载冻结（C4.1：tools 必需；C3.6 封闭 4 工具）——
    tools = payload.get("tools")
    assert isinstance(tools, list) and tools, (
        "外发负载必须包含 tools 注册表（C4.1 wire profile：tools 必需）"
    )
    names = {(t.get("function") or {}).get("name") or t.get("name") for t in tools}
    assert names == REGISTRY_TOOL_NAMES, (
        f"tools 必须恰为 C3.6 注册表四工具（无 settings/update/rollback 工具），"
        f"实际 {names!r}"
    )

    # —— 字段白名单（C4.6 ①段：白名单外字段一律不进入序列化）——
    assert set(payload.keys()) == {"endpoint", "model", "profile", "messages", "tools"}, (
        f"冻结负载字段必须恰为白名单五键，实际 {sorted(payload.keys())!r}"
    )

    # —— 确切字节＋SHA-256（C4.5）——
    from app.core.hashing import canonical_json_bytes

    frozen = canonical_json_bytes(payload)
    assert preview["payload_sha256"] == _sha256(frozen), (
        "payload_sha256 必须等于冻结字节的 SHA-256（预览＝确切序列化字节）"
    )
    assert preview["payload_bytes"] == len(frozen), (
        "payload_bytes 必须等于冻结字节数（C4.5 完整预览）"
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", preview["payload_sha256"])


def test_consent_one_use_consumed_before_send(ai) -> None:
    """一次性＋发送前原子消费：发送失败（超时）后授权已 consumed；
    重试不得自动补发（须新预览＋新授权）。"""
    api = ai.api
    fake = ai.outbound

    preview = ai_preview(api, MSG)
    consent_id = preview["consent_id"]
    ai_grant(api, consent_id)

    # —— 发送失败（传输超时）：先消费、后发送 → 授权已终态 ——
    fake.script = [httpx.TimeoutException("假出站：上游超时")]
    failed = ai_chat(api, consent_id, MSG)
    assert failed.status_code == 502 and _error_code(failed) == "E_MODEL_TIMEOUT", (
        f"传输超时必须 502 E_MODEL_TIMEOUT（C3.7），"
        f"实际 {failed.status_code}: {failed.text[:300]!r}"
    )
    assert len(fake.calls) == 1, "该授权恰发送一次（原子消费在发送前）"

    # —— 同一授权重试 → E_CONSENT_*（consumed；绝不自动补发）——
    retry = ai_chat(api, consent_id, MSG)
    assert retry.status_code in (403, 409), (
        f"一次性授权重试必须拒绝（C4.6 consumed 终态），实际 {retry.status_code}"
    )
    assert _error_code(retry) in ("E_CONSENT_REQUIRED", "E_CONSENT_STALE")
    assert len(fake.calls) == 1, "重试不得再次外发（绝不自动补发）"


def test_context_change_new_grant(ai, monkeypatch) -> None:
    """上下文/端点/工具结果/rules 变化 → 新预览＋新授权；旧授权作废且
    不外发；新预览反映新端点。"""
    api = ai.api
    fake = ai.outbound

    # —— 端点变化（C4.6 绑定 endpoint）——
    preview_a = ai_preview(api, MSG)
    consent_a = preview_a["consent_id"]
    ai_grant(api, consent_a)
    monkeypatch.setenv(MODEL_ENDPOINT_ENV, TEST_MODEL_ENDPOINT_B)

    stale_endpoint = ai_chat(api, consent_a, MSG)
    assert stale_endpoint.status_code == 409 and _error_code(stale_endpoint) == "E_CONSENT_STALE", (
        f"端点变化必须使旧授权失效（须新预览＋新授权），"
        f"实际 {stale_endpoint.status_code}: {stale_endpoint.text[:300]!r}"
    )
    assert fake.calls == [], "失效授权不得外发"

    # —— 工具结果/上下文变化（消息上下文新增 tool 结果）——
    preview_b = ai_preview(api, MSG)
    consent_b = preview_b["consent_id"]
    ai_grant(api, consent_b)
    grown = MSG + [{"role": "tool", "content": "{\"ok\":true}"}]
    stale_context = ai_chat(api, consent_b, grown)
    assert stale_context.status_code in (403, 409) and _error_code(stale_context) in (
        "E_CONSENT_REQUIRED",
        "E_CONSENT_STALE",
    ), f"上下文/工具结果变化必须要求新授权，实际 {stale_context.status_code}"

    # —— rules 变化（bundle 发布 → 绑定四元组变化）——
    preview_c = ai_preview(api, MSG)
    consent_c = preview_c["consent_id"]
    ai_grant(api, consent_c)
    publish_alt_bundle(ai.store)
    stale_rules = ai_chat(api, consent_c, MSG)
    assert stale_rules.status_code == 409 and _error_code(stale_rules) == "E_CONSENT_STALE", (
        f"rules 变化必须使未消费授权 stale（C4.6 绑定四元组），"
        f"实际 {stale_rules.status_code}: {stale_rules.text[:300]!r}"
    )

    # —— 新预览反映新端点；新授权后经假出站可用 ——
    preview_new = ai_preview(api, MSG)
    assert preview_new["payload"].get("endpoint") == TEST_MODEL_ENDPOINT_B, (
        "新预览必须反映当前端点（完整预览）"
    )
    resp, _ = ai_round(api, fake, MSG, wire_text_body("新授权已发送"))
    assert resp.status_code == 200, (
        f"新授权外发必须可用（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )


def test_keys_server_only_never_browser_log_export(ai, tmp_path) -> None:
    """密钥仅服务端 env：不出现在页面/预览负载/冻结外发字节/响应/导出/
    磁盘；仅经出站通道独立传递。"""
    api = ai.api
    fake = ai.outbound

    # —— 页面无密钥（C4.2：无浏览器端密钥表单）——
    for path in ("/", "/coverage"):
        page = api.get(path)
        assert page.status_code == 200, page.text[:200]
        assert TEST_API_KEY not in page.text, f"页面 {path} 不得出现服务端密钥"
        assert MODEL_API_KEY_ENV not in page.text

    # —— 预览负载无密钥（C4.5：预览排除 Authorization 秘密）——
    preview = ai_preview(api, MSG)
    preview_text = json.dumps(preview, ensure_ascii=False)
    assert TEST_API_KEY not in preview_text, "预览不得包含密钥（C4.5 排除秘密）"

    # —— 外发：密钥仅经出站通道；冻结字节不含密钥 ——
    resp, _ = ai_round(api, fake, MSG, wire_text_body("ok"))
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    call = fake.calls[0]
    assert call["api_key"] == TEST_API_KEY, "密钥须经出站参数独立传递（服务端持有）"
    frozen_text = call["frozen"].decode("utf-8")
    assert TEST_API_KEY not in frozen_text, "冻结字节不得包含密钥"
    assert "Bearer" not in frozen_text, "Authorization 秘密不得进入外发负载体"
    assert TEST_API_KEY not in resp.text, "响应不得回显密钥（C4.7）"

    # —— 导出面（下载/打印）无密钥 ——
    done = complete_calc(api)
    dl = api.get(f"/api/v1/records/{done['record_id']}/download")
    assert dl.status_code == 200 and TEST_API_KEY not in dl.text
    pr = api.post("/api/v1/report/print", json={"record_id": done["record_id"]})
    assert pr.status_code == 200 and TEST_API_KEY not in pr.text

    # —— 无持久化泄漏：临时 DB 目录内任何文件不含密钥 ——
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TEST_API_KEY.encode("utf-8") not in path.read_bytes(), (
                f"密钥不得写入任何持久文件（C5.5/C5.8 无持久日志）：{path.name}"
            )


def test_model_failure_no_leak_form_preserved(ai) -> None:
    """超时/鉴权/限流/格式错误 → 分类 502；错误不泄漏上游响应体；
    表单与本地结果保留（失败后本地三段照常）；不渲染模型 HTML。"""
    api = ai.api
    fake = ai.outbound
    sentinel = "UPSTREAM-INTERNAL-DETAIL-XYZ"

    done = complete_calc(api)
    record_id = done["record_id"]

    # —— 分类矩阵（每轮新授权：C4.6 每次外发独立授权）——
    cases = [
        (httpx.TimeoutException("假出站超时"), "E_MODEL_TIMEOUT"),
        ({"status_code": 401, "body": json.dumps({"error": sentinel}).encode()}, "E_MODEL_AUTH"),
        ({"status_code": 403, "body": b'{"error":"forbidden"}'}, "E_MODEL_AUTH"),
        ({"status_code": 429, "body": b'{"error":"rate"}'}, "E_MODEL_RATE_LIMIT"),
        ({"status_code": 500, "body": sentinel.encode()}, "E_MODEL_BAD_RESPONSE"),
        ({"status_code": 200, "body": b'{"unexpected": true}'}, "E_MODEL_BAD_RESPONSE"),
    ]
    for i, (result, expected_code) in enumerate(cases):
        resp, _ = ai_round(api, fake, MSG, result)
        assert resp.status_code == 502, (
            f"失败案例 {expected_code} 必须 502（C3.7），"
            f"实际 {resp.status_code}: {resp.text[:300]!r}"
        )
        assert _error_code(resp) == expected_code, (
            f"失败分类必须为 {expected_code}，实际 {_error_code(resp)}：{resp.text[:300]!r}"
        )
        assert sentinel not in resp.text, "错误响应不得回显上游响应体（C4.7 脱敏）"

    # —— 模型 HTML 不渲染：成功轮 content 含脚本标记 → 仅作 JSON 字符串值 ——
    htmlish = "<script>alert(1)</script> 模型返回的标记"
    resp_ok, _ = ai_round(api, fake, MSG, wire_text_body(htmlish))
    assert resp_ok.status_code == 200, resp_ok.text[:300]
    assert "application/json" in resp_ok.headers.get("content-type", ""), (
        "模型文本必须以 JSON 字符串承载（不得渲染为 HTML）"
    )
    payload_ok = resp_ok.json()
    assert payload_ok.get("content") == htmlish, "模型文本须原样保留为字符串值"

    # —— 表单与本地结果保留：既有记录可查＋新计算照常 ——
    kept = api.get(f"/api/v1/records/{record_id}")
    assert kept.status_code == 200, "模型失败不得影响既有本地结果"
    fresh = complete_calc(api)
    assert fresh["record_id"], "模型失败后本地三段必须照常完成（不丢表单）"
    page = api.get("/")
    assert sentinel not in page.text and "alert(1)" not in page.text, (
        "页面不得渲染模型返回的 HTML/标记"
    )


def test_clear_session_memory_only_external_prints_kept(ai) -> None:
    """清空删会话内存（records＋AI 轮/授权）；外部下载/打印副本不受影响
    （客户端持有的下载内容仍完整）；清空后可开新轮。"""
    api = ai.api
    fake = ai.outbound

    done = complete_calc(api)
    record_id = done["record_id"]

    # —— 外部副本：清空前下载的内容（客户端持有；服务端无从撤回）——
    dl = api.get(f"/api/v1/records/{record_id}/download")
    assert dl.status_code == 200, dl.text[:200]
    external_copy = dl.text
    assert "amounts" in external_copy, "下载为全量规范记录（C3.1）"
    print_copy = api.post("/api/v1/report/print", json={"record_id": record_id})
    assert print_copy.status_code == 200 and record_id in print_copy.text, (
        "打印副本为完整渲染报告（inline；客户端持有）"
    )

    # —— AI 轮（会话内存中的授权/轮次状态）——
    resp, _ = ai_round(api, fake, MSG, wire_text_body("清空前的一轮"))
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )

    preview_pending = ai_preview(api, MSG)  # 清空前遗留的未授予预览
    pending_id = preview_pending["consent_id"]

    # —— 清空（两 epoch++；删会话内存）——
    clear = api.delete("/api/v1/session")
    assert clear.status_code in (200, 202, 204), clear.text[:200]

    # —— 会话内存已删：records 404；遗留授权/预览不可用 ——
    assert api.get(f"/api/v1/records/{record_id}").status_code == 404
    gone = ai_chat(api, pending_id, MSG)
    assert gone.status_code in (403, 409) and _error_code(gone) in (
        "E_CONSENT_REQUIRED",
        "E_CONSENT_STALE",
    ), f"清空后遗留 AI 授权必须失效，实际 {gone.status_code}"

    # —— 外部副本不受影响：客户端持有的下载/打印内容仍完整 ——
    assert "amounts" in external_copy, (
        "外部副本由客户端持有，服务端清空不得影响其完整性（C5.4）"
    )

    # —— 清空后可开新轮（内存态服务随会话重建）——
    resp2, _ = ai_round(api, fake, MSG, wire_text_body("清空后的新一轮"))
    assert resp2.status_code == 200, (
        f"清空后新授权外发必须可用（经 app.state.model_outbound），"
        f"实际 {resp2.status_code}: {resp2.text[:300]!r}"
    )


def test_model_minimal_payload_no_full_export_or_unused_facts(ai) -> None:
    """默认最小化：白名单恰五键；不发送完整 export/全部确认 facts/未用信息；
    用户消息上下文逐字保留。"""
    api = ai.api
    fake = ai.outbound

    # —— 会话中存在已完成记录＋已确认但未用的 facts（最小化不得带上）——
    done = complete_calc(api)
    run_prepare(api)  # 未执行的新一轮 prepared facts

    preview = ai_preview(api, MSG)
    payload = preview["payload"]
    assert set(payload.keys()) == {"endpoint", "model", "profile", "messages", "tools"}, (
        f"外发负载必须恰为白名单五键（C4.6 ①段；tools 必需），"
        f"实际 {sorted(payload.keys())!r}"
    )
    assert payload["messages"] == MSG, "用户消息上下文逐字保留（§7 逐字展示）"

    # —— 完整 export/确认 facts/未用信息不得进入外发 ——
    forbidden_markers = list(AMOUNT_KEYS) + [
        "record_id",
        "core_hash",
        "employment_income",
        "mpf_mandatory_contributions",
        "married_status",
        "steps",
        "evidence_refs",
        "schema_version",
        "tax_type",
    ]
    preview_text = json.dumps(payload, ensure_ascii=False)
    for marker in forbidden_markers:
        assert f'"{marker}"' not in preview_text, (
            f"默认最小化外发不得包含 {marker!r}（完整 export/确认 facts/未用信息）"
        )

    # —— 实发字节同样最小化（假出站捕获）——
    resp, _ = ai_round(api, fake, MSG, wire_text_body("ok"))
    assert resp.status_code == 200, (
        f"假出站轮必须 200（经 app.state.model_outbound），"
        f"实际 {resp.status_code}: {resp.text[:300]!r}"
    )
    sent_text = fake.calls[0]["frozen"].decode("utf-8")
    for marker in forbidden_markers:
        assert f'"{marker}"' not in sent_text, (
            f"实发冻结字节不得包含 {marker!r}（白名单外字段不进序列化）"
        )
    parsed = json.loads(sent_text)
    assert set(parsed.keys()) == {"endpoint", "model", "profile", "messages", "tools"}
