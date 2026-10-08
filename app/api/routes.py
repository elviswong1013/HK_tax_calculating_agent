"""API 路由（Annex C C3.1 端点表；C3.2 prepare→confirm→execute 三段；C4 同意）。

M6 覆盖：calc prepare/confirm/execute（7 个执行目标端点，execute 已接
app/engines/* 纯函数；prepare/execute 均经 _consult_availability 咨询
app.state.update_scheduler.availability——blocked 受影响期间以
E_RULES_STATE_UNVERIFIABLE 拒算，trusted_offline 可算但必带中文离线 notice，
mode=ok 不加提示；调度器缺席时守卫跳过）、meta coverage/rules/version、records 读取（no-store）/
下载（attachment＋全量）/原包重放（按确认事实＋原 binding 重算，引擎版本
完全相等才兼容）、report/print（inline，§6.6 保留要素）、AI consent
preview/授予/撤回/chat（C4.5–C4.8）、update check/jobs（singleflight 202，
经 app.state.update_scheduler 接缝）、session 清空；页面 / /coverage ＋
六个税项计算页（/salaries /profits /property /personal-assessment
/stamps/{property,stock,lease}）＋信息页（/rules /updates /settings /about）
（Jinja2 SSR，Annex A 设计 token）。
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai.tools import ToolContext, execute_tool_calls, normalize_wire_message
from app.api.consent import (
    CHAT_ENDPOINT,
    CONSENT_TTL_SECONDS,
    ConsentStore,
    build_payload,
    dispatch_outbound,
    freeze_bytes,
    model_config,
    sha256_hex,
    validate_messages,
)
from app.api.validation import validate_prepare_body
from app.config import (
    CONFIRMATION_TTL_SECONDS,
    ENGINE_VERSION,
    PREPARE_TTL_SECONDS,
    RULES_SCHEMA_VERSION,
    SUPPORTED_YEARS,
)
from app.core.errors import AppError
from app.core.hashing import calculation_core_hash, canonical_json_bytes
from app.web.coverage_data import coverage_items
from app.web.records import (
    REPLAY_MARKER,
    build_core,
    render_print_html,
    recompute_for_replay,
    run_engine,
)

# C3.2.2：execute_target＝发起 prepare 的唯一可执行端点
EXECUTE_TARGETS: dict[str, str] = {
    "salaries_tax": "/api/v1/calc/salaries-tax",
    "profits_tax": "/api/v1/calc/profits-tax",
    "property_tax": "/api/v1/calc/property-tax",
    "personal_assessment": "/api/v1/personal-assessment/compare",
    "stamp_property": "/api/v1/calc/stamp-duty/property",
    "stamp_stock": "/api/v1/calc/stamp-duty/stock",
    "stamp_lease": "/api/v1/calc/stamp-duty/lease",
}


def register_routes(app: Any, templates: Any) -> None:
    # ------------------------------------------------------------------ meta
    @app.get("/api/v1/meta/version")
    async def meta_version() -> dict:
        return {
            "name": "hktax-agent",
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": RULES_SCHEMA_VERSION,
            "api_version": "v1",
        }

    @app.get("/api/v1/meta/coverage")
    async def meta_coverage() -> dict:
        return {"items": coverage_items()}

    @app.get("/api/v1/meta/rules")
    async def meta_rules(request: Request) -> dict:
        binding = _full_binding(request)
        return {
            "current": binding,
            "supported_years": list(SUPPORTED_YEARS),
            "sources": [],
            "notes": [
                "2026/27 为未完结年度：按已确认规则快照适用并提示。",
                "现行束已入数数值均挂 T1 台账锚点（见束 evidence_digests）。",
            ],
        }

    # ------------------------------------------------- prepare（C3.2.1）
    @app.post("/api/v1/calc/prepare")
    async def calc_prepare(request: Request) -> JSONResponse:
        body = await _read_json(request)
        tax_type, canonical_input = validate_prepare_body(body)
        # REQ-17/18：规则可用性守卫（blocked 受影响期间拒算且不落 prepared；
        # trusted_offline 降级 notice 并入 warnings；守卫只读状态，不写库）
        offline_notice = _consult_availability(request, canonical_input)
        registry, sid = _session(request)
        binding = _full_binding(request)
        input_hash = "sha256:" + hashlib.sha256(
            canonical_json_bytes(canonical_input)
        ).hexdigest()
        prepared_id = "p-" + secrets.token_hex(16)
        input_revision = registry.next_input_revision(sid)
        registry.save_prepared(
            sid,
            {
                "prepared_id": prepared_id,
                "tax_type": tax_type,
                "input": canonical_input,
                "input_hash": input_hash,
                "input_revision": input_revision,
                "binding": binding,
                "execute_target": EXECUTE_TARGETS[tax_type],
                "created_at": time.monotonic(),
                "ttl": PREPARE_TTL_SECONDS,
            },
        )
        payload: dict[str, Any] = {
            "prepared_id": prepared_id,
            "input_revision": input_revision,
            "input_hash": input_hash,
            "expected_binding": binding,
            "preview": {
                "tax_type": tax_type,
                "year_of_assessment": canonical_input.get("year_of_assessment"),
                "status_note": "仅事实校验；prepared_id 非可执行 id（C3.2.1）",
            },
            "expires_in": PREPARE_TTL_SECONDS,
            "status": "prepared",
        }
        # REQ-2：2026/27 未完结提示（仅直接税年度键；印花税按文书日期窗口）
        # ＋REQ-18：trusted_offline 离线 notice（mode=ok 不添加任何提示）
        warnings: list[str] = []
        if canonical_input.get("year_of_assessment") == "2026_27":
            warnings.append(
                "2026/27 课税年度尚未完结：结果按已确认规则快照计算，"
                "后续法例或官方指引可能调整。"
            )
        if offline_notice:
            warnings.append(offline_notice)
        if warnings:
            payload["warnings"] = warnings
        return JSONResponse(status_code=201, content=payload)

    # ------------------------------------------------- confirm（C3.2.2）
    @app.post("/api/v1/calc/confirm")
    async def calc_confirm(request: Request) -> dict:
        body = await _read_json(request)
        if not isinstance(body, dict):
            raise AppError("E_INPUT_TYPE", "请求体必须是 JSON 对象", field="body")
        for key in ("prepared_id", "canonical_input_hash", "expected_binding", "acknowledge"):
            if key not in body:
                raise AppError("E_INPUT_MISSING", f"缺少必填字段：{key}", field=key)
        if body["acknowledge"] is not True:
            raise AppError(
                "E_CONFIRMATION_REQUIRED",
                "必须显式确认（acknowledge=true）才能生成执行能力",
            )
        registry, sid = _session(request)
        prepared = registry.get_prepared(sid, str(body["prepared_id"]))
        if prepared is None:
            raise AppError(
                "E_CONFIRMATION_REQUIRED",
                "prepared 记录不存在；请重新 prepare",
            )
        if time.monotonic() - prepared["created_at"] > prepared["ttl"]:
            registry.drop_prepared(sid, prepared["prepared_id"])
            raise AppError("E_CONFIRMATION_STALE", "prepared 已过期；请重新 prepare")
        if (
            body["canonical_input_hash"] != prepared["input_hash"]
            or body["expected_binding"] != prepared["binding"]
        ):
            raise AppError(
                "E_CONFIRMATION_STALE",
                "输入或规则绑定已变化；请重新 prepare",
            )
        confirmation_id = "c-" + secrets.token_hex(32)  # 256-bit 可执行确认能力
        registry.save_confirmation(
            sid,
            {
                "confirmation_id": confirmation_id,
                "prepared_id": prepared["prepared_id"],
                "tax_type": prepared["tax_type"],
                "input_hash": prepared["input_hash"],
                "input_revision": prepared.get("input_revision"),
                "binding": prepared["binding"],
                "execute_target": prepared["execute_target"],
                "session_epoch": registry.session_epoch,
                "created_at": time.monotonic(),
                "ttl": CONFIRMATION_TTL_SECONDS,
                "consumed": False,
                "record_id": None,
            },
        )
        return {"confirmation_id": confirmation_id, "expires_in": CONFIRMATION_TTL_SECONDS}

    # ------------------------------------------------- execute（C3.2.3）
    def _register_execute(target: str) -> None:
        @app.post(target)
        async def calc_execute(request: Request) -> JSONResponse:
            body = await _read_json(request)
            if not isinstance(body, dict) or "confirmation_id" not in body:
                raise AppError(
                    "E_INPUT_MISSING", "缺少 confirmation_id", field="confirmation_id"
                )
            registry, sid = _session(request)
            confirmation_id = str(body["confirmation_id"])
            confirmation = registry.peek_confirmation(sid, confirmation_id)
            if confirmation is None:
                raise AppError(
                    "E_CONFIRMATION_REQUIRED",
                    "确认能力不存在；请先 prepare＋confirm",
                )
            # 错目标 → 409 且不消费（C3.2.2/C3.2.3）
            if confirmation["execute_target"] != target:
                raise AppError(
                    "E_CONFIRMATION_TARGET_MISMATCH",
                    "请从发起确认的同一税项入口执行",
                )
            if confirmation.get("consumed"):
                consumed_error = AppError(
                    "E_CONFIRMATION_CONSUMED", "该确认已使用（无双重计算）"
                )
                if confirmation.get("record_id"):
                    consumed_error.extra["record_id"] = confirmation["record_id"]
                raise consumed_error
            if (
                registry.session_epoch != confirmation["session_epoch"]
                or time.monotonic() - confirmation["created_at"] > confirmation["ttl"]
            ):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "确认能力已失效；请重新 prepare＋confirm",
                )
            # C3.2.4：input_revision 变化（用户编辑输入）→ 未消费确认失效
            if confirmation.get("input_revision") != registry.current_input_revision(sid):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "输入已修订（input_revision 变化）；请重新 prepare＋confirm",
                )
            # C3.2.4：rules 变化（bundle/engine 四元组任一字段变化）→ 失效
            if confirmation["binding"] != _full_binding(request):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "规则绑定已变化（bundle/engine）；请重新 prepare＋confirm",
                )
            # REQ-17/18：规则可用性守卫（blocked 受影响期间拒算且不消费确认
            # 能力，状态核验恢复后同一确认仍可执行；trusted_offline 降级
            # notice 并入响应 warnings；守卫只读状态，不写库）
            facts = _prepared_input(registry, sid, confirmation)
            offline_notice = _consult_availability(request, facts)
            if not registry.mark_consumed(sid, confirmation_id):
                consumed_error = AppError(
                    "E_CONFIRMATION_CONSUMED", "该确认已使用（无双重计算）"
                )
                if confirmation.get("record_id"):
                    consumed_error.extra["record_id"] = confirmation["record_id"]
                raise consumed_error

            # 引擎执行（app/engines/* 纯函数；规则束按确认 binding 精确载入）
            store = request.app.state.store
            bundle = store.get_bundle(confirmation["binding"]["bundle_id"])
            result = run_engine(confirmation["tax_type"], facts, bundle)
            core = build_core(
                confirmation["tax_type"], facts, confirmation["binding"], result, bundle
            )
            record_id = "r-" + secrets.token_hex(12)
            confirmation["record_id"] = record_id
            registry.save_record(sid, {"record_id": record_id, "core": core})
            # REQ-18：trusted_offline 降级 notice 仅并入响应 warnings（不入
            # 存档 core/core_hash；ok 时无 notice，warnings 与 core 一致）
            warnings = list(core["warnings"] or [])
            if offline_notice:
                warnings.append(offline_notice)
            return JSONResponse(
                status_code=200,
                content={
                    "record_id": record_id,
                    "status": core["status"],
                    "binding": core["binding"],
                    "amounts": core["amounts"],
                    "blocked_reason": core["blocked_reason"],
                    "core_hash": calculation_core_hash(core),
                    "steps": core["steps"],
                    "evidence_refs": core["evidence_refs"],
                    "unsupported": core["unsupported"],
                    "pending_verification": core["pending_verification"],
                    "missing_components": core["missing_components"],
                    "warnings": warnings,
                    "questions": core["questions"],
                    "disclaimer": core["disclaimer"],
                    "error": None,
                },
            )

    for _target in EXECUTE_TARGETS.values():
        _register_execute(_target)

    # ------------------------------------------------------------------ records
    @app.post("/api/v1/records/replay")
    async def records_replay(request: Request) -> JSONResponse:
        """原包重放（C3.1 replay 行）：按确认事实＋原 binding 校验后重算；
        标「历史估算，非当前重新评估」；不改写原记录。"""
        body = await _read_json(request)
        record_id = body.get("record_id") if isinstance(body, dict) else None
        registry, sid = _session(request)
        record = registry.get_record(sid, record_id)
        if record is None:
            raise StarletteHTTPException(status_code=404, detail="record not found")
        core = record["core"]
        result = recompute_for_replay(core, request.app.state.store, ENGINE_VERSION)
        return JSONResponse(
            status_code=200,
            content={
                "record_id": record_id,
                "replay": True,
                "notice": REPLAY_MARKER,
                "binding": core["binding"],
                "status": result.get("status"),
                "amounts": result.get("amounts"),
                "warnings": list(result.get("warnings") or []),
            },
        )

    @app.get("/api/v1/records/{record_id}")
    async def record_get(request: Request, record_id: str) -> dict:
        registry, sid = _session(request)
        record = registry.get_record(sid, record_id)
        if record is None:
            raise StarletteHTTPException(status_code=404, detail="record not found")
        return {"record_id": record_id, "record": record["core"]}

    @app.get("/api/v1/records/{record_id}/download")
    async def record_download(request: Request, record_id: str) -> JSONResponse:
        """全量规范确认事实下载（C3.1：非摘要；attachment＋no-store）。"""
        registry, sid = _session(request)
        record = registry.get_record(sid, record_id)
        if record is None:
            raise StarletteHTTPException(status_code=404, detail="record not found")
        core = record["core"]
        return JSONResponse(
            content={
                "record_id": record_id,
                "record": core,
                "core_hash": calculation_core_hash(core),
            },
            headers={
                "Content-Disposition": 'attachment; filename="record.json"',
                "Cache-Control": "no-store",
            },
        )

    # ------------------------------------------------------------------ report
    @app.post("/api/v1/report/print")
    async def report_print(request: Request) -> HTMLResponse:
        body = await _read_json(request)
        registry, sid = _session(request)
        record_id = body.get("record_id") if isinstance(body, dict) else None
        record = registry.get_record(sid, record_id)
        if record is None:
            raise StarletteHTTPException(status_code=404, detail="record not found")
        html = render_print_html(str(record_id), record["core"])
        return HTMLResponse(
            content=html,
            headers={"Content-Disposition": "inline", "Cache-Control": "no-store"},
        )

    # ------------------------------------------------- AI consent（C4.5–C4.8）
    consent_store = ConsentStore()

    @app.post("/api/v1/ai/consent/preview")
    async def ai_consent_preview(request: Request) -> JSONResponse:
        """外发预览：白名单负载 → 一次 serialize 冻结字节 → 完整预览＋
        字节数＋SHA-256（C4.5/C4.6 ①–④段）。"""
        body = await _read_json(request)
        if not isinstance(body, dict) or "messages" not in body:
            raise AppError("E_INPUT_MISSING", "缺少必填字段：messages", field="messages")
        messages = validate_messages(body["messages"])
        registry, sid = _session(request)
        config = model_config()
        payload = build_payload(messages, config["model"], config["upstream_endpoint"])
        frozen = freeze_bytes(payload)
        item = consent_store.create(
            sid,
            {
                "messages": messages,
                "frozen_bytes": frozen,
                "payload_sha256": sha256_hex(frozen),
                "model": config["model"],
                "upstream_endpoint": config["upstream_endpoint"],
                "endpoint": CHAT_ENDPOINT,
                "profile": payload["profile"],
                "rules_binding": _full_binding(request),
                "consent_epoch": registry.consent_epoch,
                "session_epoch": registry.session_epoch,
                "input_revision": registry.current_input_revision(sid),
                "ttl": CONSENT_TTL_SECONDS,
            },
        )
        return JSONResponse(
            status_code=201,
            content={
                "consent_id": item["consent_id"],
                "status": item["status"],
                "payload": payload,
                "payload_sha256": item["payload_sha256"],
                "payload_bytes": len(frozen),
                "model": config["model"],
                "endpoint": CHAT_ENDPOINT,
                "expires_in": CONSENT_TTL_SECONDS,
            },
        )

    @app.post("/api/v1/ai/consent")
    async def ai_consent_grant(request: Request) -> JSONResponse:
        """授予（prepared → granted；C4.8）。"""
        body = await _read_json(request)
        consent_id = body.get("consent_id") if isinstance(body, dict) else None
        if not consent_id or not isinstance(consent_id, str):
            raise AppError("E_INPUT_MISSING", "缺少必填字段：consent_id", field="consent_id")
        registry, sid = _session(request)
        item = consent_store.get(sid, consent_id)
        if item is None:
            raise AppError("E_CONSENT_REQUIRED", "同意不存在；请重新预览并授权")
        if item["status"] != "prepared":
            raise AppError(
                "E_CONSENT_STALE", "同意已失效（非 prepared 态）；请重新预览并授权"
            )
        if time.monotonic() - item["created_at"] > item["ttl"]:
            consent_store.transition(sid, consent_id, "expired")
            raise AppError("E_CONSENT_STALE", "预览已过期（TTL 5 分钟）；请重新预览并授权")
        if (
            item["consent_epoch"] != registry.consent_epoch
            or item["session_epoch"] != registry.session_epoch
        ):
            consent_store.transition(sid, consent_id, "stale")
            raise AppError("E_CONSENT_STALE", "会话或同意状态已变化；请重新预览并授权")
        if item["rules_binding"] != _full_binding(request):
            consent_store.transition(sid, consent_id, "stale")
            raise AppError("E_CONSENT_STALE", "规则绑定已变化；请重新预览并授权")
        consent_store.transition(sid, consent_id, "granted")
        return JSONResponse(
            status_code=200,
            content={
                "consent_id": consent_id,
                "status": "granted",
                "expires_in": CONSENT_TTL_SECONDS,
            },
        )

    @app.delete("/api/v1/ai/consent/{consent_id}")
    async def ai_consent_revoke(request: Request, consent_id: str) -> JSONResponse:
        """撤回（C5.4）：consent_epoch++ 仅废 outbound/pending AI；
        本地记录与确认能力不动。"""
        registry, sid = _session(request)
        item = consent_store.get(sid, consent_id)
        if item is None:
            raise StarletteHTTPException(status_code=404, detail="consent not found")
        if item["status"] in ("prepared", "granted"):
            consent_store.transition(sid, consent_id, "revoked")
        epochs = registry.bump_consent_epoch()
        return JSONResponse(
            status_code=200,
            content={
                "status": "revoked",
                "consent_id": consent_id,
                "consent_epoch": epochs["consent_epoch"],
                "session_epoch": epochs["session_epoch"],
            },
        )

    @app.post("/api/v1/ai/chat")
    async def ai_chat(request: Request) -> JSONResponse:
        """外发（C4.6 ⑤–⑦段）：有效授权 → 模型配置绑定比对 → 与冻结字节
        逐位比对 → 原子消费 → 经 app.state.model_outbound 接缝原样发送 →
        C3.6 工具层。任何变化 → 旧授权作废（E_CONSENT_STALE），必须新预览。"""
        body = await _read_json(request)
        if not isinstance(body, dict):
            raise AppError("E_INPUT_TYPE", "请求体必须是 JSON 对象", field="body")
        consent_id = body.get("consent_id")
        if not consent_id or not isinstance(consent_id, str):
            raise AppError("E_INPUT_MISSING", "缺少必填字段：consent_id", field="consent_id")
        if "messages" not in body:
            raise AppError("E_INPUT_MISSING", "缺少必填字段：messages", field="messages")
        messages = validate_messages(body["messages"])
        registry, sid = _session(request)
        item = consent_store.get(sid, consent_id)
        if item is None:
            raise AppError("E_CONSENT_REQUIRED", "同意不存在；请先预览并授权")
        if item["status"] == "prepared":
            raise AppError("E_CONSENT_REQUIRED", "尚未授权；请先确认本次允许发送")
        if item["status"] != "granted":
            raise AppError(
                "E_CONSENT_STALE",
                f"同意已失效（{item['status']}）；请重新预览并授权",
            )
        if time.monotonic() - item["created_at"] > item["ttl"]:
            consent_store.transition(sid, consent_id, "expired")
            raise AppError("E_CONSENT_STALE", "授权已过期；请重新预览并授权")
        if (
            item["consent_epoch"] != registry.consent_epoch
            or item["session_epoch"] != registry.session_epoch
        ):
            consent_store.transition(sid, consent_id, "stale")
            raise AppError("E_CONSENT_STALE", "会话或同意状态已变化；请重新预览并授权")
        if item["rules_binding"] != _full_binding(request):
            consent_store.transition(sid, consent_id, "stale")
            raise AppError("E_CONSENT_STALE", "规则绑定已变化；请重新预览并授权")
        if item["input_revision"] != registry.current_input_revision(sid):
            consent_store.transition(sid, consent_id, "stale")
            raise AppError("E_CONSENT_STALE", "输入已修订；请重新预览并授权")
        config = model_config()
        # C5.4：模型配置变更（endpoint/model）→ consent_epoch++，全部
        # 未消费授权失效（本地表单/确认/记录不动）
        if (
            item["upstream_endpoint"] != config["upstream_endpoint"]
            or item["model"] != config["model"]
        ):
            registry.bump_consent_epoch()
            consent_store.transition(sid, consent_id, "stale")
            raise AppError("E_CONSENT_STALE", "模型配置已变化；请重新预览并授权")
        # C4.6：负载与冻结字节不符（上下文/重试/任何变化）→ 旧授权作废
        incoming = freeze_bytes(
            build_payload(messages, item["model"], item["upstream_endpoint"])
        )
        if sha256_hex(incoming) != item["payload_sha256"]:
            consent_store.transition(sid, consent_id, "stale")
            raise AppError(
                "E_CONSENT_STALE", "负载与冻结字节不符；请重新预览并授权"
            )
        if not config["upstream_endpoint"] or not config["api_key"]:
            # C3.7：AI 尚未配置 → 409（本地计算可用；授权保留待配置后使用）
            raise AppError("E_MODEL_UNCONFIGURED", "AI 尚未配置；本地计算可用")
        # ⑥发送前原子消费（consumed 终态，不可回滚）→ ⑦经接缝原样发送冻结字节
        consent_store.transition(sid, consent_id, "consumed")
        raw = dispatch_outbound(
            getattr(request.app.state, "model_outbound", None),
            config["upstream_endpoint"],
            config["api_key"],
            item["frozen_bytes"],
        )
        message = normalize_wire_message(raw["body"])
        response_payload: dict[str, Any] = {
            "consent_id": consent_id,
            "sent_bytes": len(item["frozen_bytes"]),
            "status": "completed",
            "content": message["content"],
            # 模型文本非权威（REQ-11/REQ-12）：金额仅经 tool_results 承载
            "unconfirmed": True,
        }
        if message["tool_calls"]:
            # C3.6 工具层：封闭注册表＋strict 参数＋确认门控（不绕过三段确认）
            ctx = ToolContext(
                registry, sid, request.app.state.store, _full_binding(request)
            )
            response_payload["tool_results"] = execute_tool_calls(
                message["tool_calls"], ctx
            )
        return JSONResponse(status_code=200, content=response_payload)

    # ------------------------------------------------- update（C3.1/C6.6）
    def _scheduler(request: Request) -> Any:
        scheduler = getattr(request.app.state, "update_scheduler", None)
        if scheduler is None:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                "更新调度器未挂载；无法执行规则检查",
            )
        return scheduler

    @app.post("/api/v1/update/check")
    async def update_check(request: Request) -> JSONResponse:
        """触发检查；进行中 → 202＋现有 job_id（singleflight 幂等，C6.6）。"""
        job = _scheduler(request).trigger_manual()
        in_progress = bool(job.get("in_progress"))
        return JSONResponse(
            status_code=202 if in_progress else 200,
            content={
                "job_id": job.get("job_id"),
                "kind": job.get("kind"),
                "status": job.get("status"),
                "in_progress": in_progress,
            },
        )

    @app.get("/api/v1/update/jobs/{job_id}")
    async def update_job_detail(request: Request, job_id: str) -> dict:
        """作业轮询详情（§12.7：不含任何财务负载——检查不发送/不回显用户资料）。"""
        status = _scheduler(request).status()
        active = status.get("active_job") if isinstance(status, dict) else None
        if not isinstance(active, dict) or active.get("job_id") != job_id:
            raise StarletteHTTPException(status_code=404, detail="update job not found")
        return {
            "job_id": active.get("job_id"),
            "kind": active.get("kind"),
            "status": active.get("status"),
            "in_progress": bool(active.get("in_progress")),
        }

    # ------------------------------------------------------------------ session
    @app.delete("/api/v1/session")
    async def session_clear(request: Request) -> dict:
        registry, sid = _session(request)
        epochs = registry.clear_session(sid)
        return {"status": "cleared", **epochs}

    # ------------------------------------------------------------------ pages
    @app.get("/")
    async def index_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/coverage")
    async def coverage_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="coverage.html",
            context={"items": coverage_items()},
        )

    @app.get("/salaries")
    async def salaries_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="salaries.html", context={})

    @app.get("/profits")
    async def profits_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="profits.html", context={})

    @app.get("/property")
    async def property_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="property.html", context={})

    @app.get("/personal-assessment")
    async def personal_assessment_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request, name="personal_assessment.html", context={}
        )

    @app.get("/stamps/property")
    async def stamp_property_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request, name="stamp_property.html", context={}
        )

    @app.get("/stamps/stock")
    async def stamp_stock_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request, name="stamp_stock.html", context={}
        )

    @app.get("/stamps/lease")
    async def stamp_lease_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request, name="stamp_lease.html", context={}
        )

    @app.get("/rules")
    async def rules_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="rules.html",
            context={
                "binding": _full_binding(request),
                "supported_years": list(SUPPORTED_YEARS),
            },
        )

    @app.get("/updates")
    async def updates_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="updates.html", context={})

    @app.get("/settings")
    async def settings_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="settings.html", context={})

    @app.get("/about")
    async def about_page(request: Request) -> Any:
        return templates.TemplateResponse(request=request, name="about.html", context={})


# ---------------------------------------------------------------------- 工具
async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise AppError("E_INPUT_TYPE", "请求体不是合法 JSON", field="body") from None


def _session(request: Request) -> tuple[Any, str | None]:
    registry = request.app.state.registry
    sid = getattr(request.state, "session_id", None)
    return registry, sid


def _full_binding(request: Request) -> dict[str, str]:
    """C3.2.1：完整四元组 binding（请求开始 pin 单一 bundle）。"""
    pinned = request.app.state.store.pin_current()
    return {
        "bundle_id": pinned["bundle_id"],
        "bundle_hash": pinned["bundle_hash"],
        "rules_schema_version": pinned["rules_schema_version"],
        "engine_version": ENGINE_VERSION,
    }


def _prepared_input(registry: Any, sid: str | None, confirmation: dict) -> dict:
    prepared = registry.get_prepared(sid, confirmation.get("prepared_id", ""))
    return prepared["input"] if prepared is not None else {}


def _availability_guard(request: Request) -> Any | None:
    """REQ-17/18 可用性守卫接缝：返回 update_scheduler.availability；
    调度器缺席或假件无 availability 契约 → None（守卫跳过，不破坏既有流程）。"""
    scheduler = getattr(request.app.state, "update_scheduler", None)
    availability = getattr(scheduler, "availability", None)
    return availability if callable(availability) else None


def _availability_period(facts: Any) -> str | None:
    """守卫期间键：直接税取 year_of_assessment（YYYY_YY）；印花税由
    instrument_date 映射课税年度（4 月 1 日起始）；两者皆缺 → None（不守卫）。"""
    if not isinstance(facts, dict):
        return None
    year = facts.get("year_of_assessment")
    if isinstance(year, str) and year:
        return year
    instrument_date = facts.get("instrument_date")
    if isinstance(instrument_date, str) and len(instrument_date) >= 7:
        try:
            year_part = int(instrument_date[0:4])
            month_part = int(instrument_date[5:7])
        except ValueError:
            return None
        if not (1 <= month_part <= 12):
            return None
        start = year_part if month_part >= 4 else year_part - 1
        return f"{start}_{(start + 1) % 100:02d}"
    return None


def _consult_availability(request: Request, facts: Any) -> str | None:
    """咨询 update_scheduler.availability(period)（REQ-17/18；只读态不写库）：

    - blocked（已知变更/过期/回滚受影响期间）→ E_RULES_STATE_UNVERIFIABLE
      拒算（C3.7 → 422），不输出任何税额；
    - trusted_offline（离线但最近已核验快照在档）→ 返回中文降级 notice，由
      调用方并入响应 warnings（REQ-18 降级行：可算但必须告知）；
    - ok／守卫缺席／期间不可得 → None（不添加任何 notice）。
    """
    availability = _availability_guard(request)
    if availability is None:
        return None
    period = _availability_period(facts)
    if period is None:
        return None
    verdict = availability(period)
    if not isinstance(verdict, dict):
        return None
    mode = verdict.get("mode")
    notice = verdict.get("notice")
    if mode == "blocked":
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            notice
            if isinstance(notice, str) and notice
            else (
                f"课税年度 {period} 存在已知未核实的法律状态；受影响期间的"
                "现行计算已拒绝，不自动推定生效或失效，待独立核验并发布后恢复。"
            ),
        )
    if mode == "trusted_offline" and isinstance(notice, str) and notice:
        return notice
    return None
