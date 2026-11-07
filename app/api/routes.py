"""API 路由（Annex C C3.1 端点表；C3.2 prepare→confirm→execute 三段；C4 同意）。

M6 覆盖：calc prepare/confirm/execute（7 个执行目标端点，execute 已接
app/engines/* 纯函数；prepare 单快照（pin→束→guard→admission 同一 store
写锁窗口）与 execute 消费（registry→store 临界区重查 current＋next）均经
guard-contract §3–§4；meta coverage/rules/version、records 读取（no-store）/
下载（attachment＋全量）/原包重放（按确认事实＋原 binding 重算，引擎版本
完全相等才兼容）、report/print（inline，§6.6 保留要素）、AI consent
preview/授予/撤回/chat（C4.5–C4.8）、update check/jobs（singleflight 202，
经 app.state.update_scheduler 接缝）、session 清空；页面 / /coverage ＋
六个税项计算页（/salaries /profits /property /personal-assessment
/stamps/{property,stock,lease}）＋信息页（/rules /updates /settings /about）
（Jinja2 SSR，Annex A 设计 token）。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai.tools import (
    ToolContext,
    build_precheck_context,
    execute_tool_calls,
    normalize_wire_message,
)
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
    MAX_BODY_BYTES,
    MODEL_OUTBOUND_TOTAL_TIMEOUT_SECONDS,
    PREPARE_TTL_SECONDS,
    RULES_SCHEMA_VERSION,
    SUPPORTED_YEARS,
)
from app.core.errors import AppError
from app.core.hashing import (
    bundle_content_hash,
    calculation_core_hash,
    canonical_json_bytes,
    canonical_loads,
)
from app.rules import runtime as rules_runtime
from app.web.coverage_data import chat_coverage_guidance, coverage_items
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

# S002 ADR-3：v2 输入（schema 2.0.0＋显式 AvailabilityContext）仅此两税
_V2_TAX_TYPES = ("salaries_tax", "personal_assessment")


def register_routes(app: Any, templates: Any) -> None:
    # 共享同意存储（Annex 005 §12.1：middleware reservation 与 routes 共用同一
    # ConsentStore 实例；create_app 装配时写入 app.state.consent_store）
    consent_store = getattr(app.state, "consent_store", None) or ConsentStore()

    # ------------------------------------------------------------------ meta
    @app.get("/api/v1/meta/version")
    async def meta_version(request: Request) -> dict:
        """版本表＋运行时 epoch/revision（S002 §4.1；不入 binding/core 哈希）。"""
        registry, sid = _session(request)
        return {
            "name": "hktax-agent",
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": RULES_SCHEMA_VERSION,
            "api_version": "v1",
            "session_epoch": registry.session_epoch,
            "input_revision": registry.current_input_revision(sid),
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
        """prepare：middleware 已完成唯一 reservation（If-Match CAS→rev++/
        失效→声明大小检查）；本路由在有界读取/校验后，于同一 app-owned
        store 写锁窗口内完成 pin→取束→guard→admission 单快照（G16/G17），
        锁外运行恰一次语义预检引擎，保存前再 CAS→落 prepared（不再递增
        revision）。"""
        registry, sid = _session(request)
        reservation = getattr(request.state, "edit_reservation", None)
        body = await _read_bounded_json(request)
        tax_type, canonical_input = validate_prepare_body(body)
        if reservation is None:
            raise AppError(
                "E_CONFIRMATION_STALE",
                "编辑在途：本次 reservation 已非当前版本；请以 X-Edit-Current-* "
                "只读快照显式重同步后重新 prepare",
            )
        store = request.app.state.store
        guard = getattr(request.app.state, "update_scheduler", None)
        # §4 prepare 单快照：pin→bundle→guard→admission 同一 store 写锁窗口内
        #（锁内不运行引擎/网络；writer publish 不得穿过快照窗口）。
        with store._write_lock:
            binding = _full_binding(request)
            content = store.get_bundle(binding["bundle_id"])
            try:
                recomputed = bundle_content_hash(content)
            except (TypeError, ValueError) as exc:
                raise AppError(
                    "E_RULES_STATE_UNVERIFIABLE",
                    "规则束内容形状损坏（无法复核哈希）；拒算（fail-closed）。",
                ) from exc
            if recomputed != binding["bundle_hash"]:
                raise AppError(
                    "E_RULES_STATE_UNVERIFIABLE",
                    "规则束内容与已发布 bundle_hash 不符（载入损坏）；"
                    "拒算（fail-closed）。",
                )
            guard_read = rules_runtime.read_guard(
                guard, tax_type=tax_type, facts=canonical_input
            )
            admission = rules_runtime.admit_runtime(
                binding,
                content,
                tax_type=tax_type,
                facts=canonical_input,
                guard_read=guard_read,
            )
        # 锁外：v2 两税语义预检（引擎恰一次；needs_input/blocked 在 prepare
        # 即拒绝、无能力；结构/引用错误由校验/引擎抛出）。
        if tax_type in _V2_TAX_TYPES:
            result = run_engine(
                tax_type,
                copy.deepcopy(dict(canonical_input)),
                copy.deepcopy(dict(content)),
                availability=admission["availability_context"],
            )
            status = result.get("status")
            if status == "needs_input":
                questions = [
                    question
                    for question in (result.get("questions") or [])
                    if isinstance(question, dict)
                ]
                field = questions[0].get("field") if questions else None
                raise AppError(
                    "E_ELIGIBILITY_UNKNOWN",
                    "存在尚未确定的资格事实；请按定位补全后重新 prepare＋confirm。",
                    field=field,
                    binding=dict(binding),
                    extra={"questions": questions, "status": "needs_input"},
                )
            if status == "blocked":
                blocked_reason = str(
                    result.get("blocked_reason") or "规则状态不可核验；拒算。"
                )
                raise AppError(
                    "E_RULES_STATE_UNVERIFIABLE",
                    blocked_reason,
                    binding=dict(binding),
                    extra={
                        "status": "blocked",
                        "blocked_reason": blocked_reason,
                        "questions": [
                            question
                            for question in (result.get("questions") or [])
                            if isinstance(question, dict)
                        ],
                    },
                )
        input_hash = "sha256:" + hashlib.sha256(
            canonical_json_bytes(canonical_input)
        ).hexdigest()
        prepared_id = "p-" + secrets.token_hex(16)
        input_revision = int(reservation["input_revision"])
        # §12.1 末行＋R2：保存前再 CAS 与保存同一临界区（body 读取期间另一
        # 编辑推进 → 409 stale、不落 prepared）。
        if not registry.save_prepared_if_current(
            sid,
            {
                "prepared_id": prepared_id,
                "tax_type": tax_type,
                "schema_version": str(body["schema_version"]),
                "input": copy.deepcopy(canonical_input),
                "input_hash": input_hash,
                "input_revision": input_revision,
                "binding": copy.deepcopy(binding),
                "session_epoch": reservation["session_epoch"],
                "execute_target": EXECUTE_TARGETS[tax_type],
                "created_at": time.monotonic(),
                "ttl": PREPARE_TTL_SECONDS,
            },
            reservation["session_epoch"],
            reservation["input_revision"],
        ):
            raise AppError(
                "E_CONFIRMATION_STALE",
                "编辑在途：本次 reservation 已非当前版本；请以 X-Edit-Current-* "
                "只读快照显式重同步后重新 prepare",
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
        # ＋REQ-18：guard admission 的离线固定模板提示（ok 不添加任何提示）
        warnings: list[str] = []
        if canonical_input.get("year_of_assessment") == "2026_27":
            warnings.append(
                "2026/27 课税年度尚未完结：结果按已确认规则快照计算，"
                "后续法例或官方指引可能调整。"
            )
        warnings.extend(admission.get("warnings") or [])
        if warnings:
            payload["warnings"] = warnings
        return JSONResponse(status_code=201, content=payload)

    # ------------------------------------------------- invalidate（§12.3）
    @app.post("/api/v1/calc/invalidate")
    async def calc_invalidate(request: Request) -> JSONResponse:
        """显式失效（CAS）：rev++、删除旧 prepared、未消费 confirm 失效、
        records 保留；CAS 失败 409（附 Current 只读快照）。"""
        body = await _read_json(request)
        if not isinstance(body, dict):
            raise AppError("E_INPUT_TYPE", "请求体必须是 JSON 对象", field="body")
        # §12.3/R3⑧：invalidate 请求体闭合两键（extra → 结构化拒绝、不推进）
        _INVALIDATE_KEYS = ("expected_session_epoch", "expected_input_revision")
        _invalidate_extras = [key for key in body if key not in _INVALIDATE_KEYS]
        if _invalidate_extras:
            raise AppError(
                "E_INPUT_EXTRA",
                f"invalidate 请求含未知字段：{_invalidate_extras[0]}（仅受理"
                " expected_session_epoch／expected_input_revision）",
                field=_invalidate_extras[0],
            )
        expected_epoch = body.get("expected_session_epoch")
        expected_revision = body.get("expected_input_revision")
        for name, value in (
            ("expected_session_epoch", expected_epoch),
            ("expected_input_revision", expected_revision),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise AppError(
                    "E_INPUT_TYPE", f"{name} 必须为整数", field=name
                )
        registry, sid = _session(request)
        outcome = registry.invalidate(
            sid,
            expected_epoch,
            expected_revision,
            consent_store=consent_store,
        )
        if outcome is None:
            raise AppError(
                "E_CONFIRMATION_STALE",
                "会话版本不符（CAS 失败）；请以 X-Edit-Current-* 只读快照重同步",
            )
        request.state.edit_ack = {
            "session_epoch": outcome["session_epoch"],
            "input_revision": outcome["input_revision"],
        }
        return JSONResponse(
            status_code=200,
            content={
                "status": "invalidated",
                "session_epoch": outcome["session_epoch"],
                "input_revision": outcome["input_revision"],
            },
        )

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
        # §12.3＋R2/A2：registry→store 临界区内原子核对 prepared 身份并在同一
        # 临界区重读当前 binding 后签发能力（指针窗口内切换 → 不发能力）。
        confirmation = registry.issue_confirmation(
            sid,
            str(body["prepared_id"]),
            body["canonical_input_hash"],
            body["expected_binding"],
            store=request.app.state.store,
        )
        return {
            "confirmation_id": confirmation["confirmation_id"],
            "expires_in": CONFIRMATION_TTL_SECONDS,
        }

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
            # stale 墓碑（invalidate/clear 后）优先于已消费链接返回（§12.3：
            # clear 后旧 token 不返回旧链接）
            if confirmation.get("stale"):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "确认能力已失效；请重新 prepare＋confirm",
                )
            # 错目标 → 409 且不消费（C3.2.2/C3.2.3）
            if confirmation["execute_target"] != target:
                raise AppError(
                    "E_CONFIRMATION_TARGET_MISMATCH",
                    "请从发起确认的同一税项入口执行",
                )
            # 重复请求仍以 registry 权威作答（B8：锁外 peek 副本不得返回旧
            # 链接；clear/失效后 → stale，无旧 record 链接）
            if confirmation.get("consumed"):
                consumption = registry.get_consumption(sid, confirmation_id)
                if consumption is None or consumption.get("stale"):
                    raise AppError(
                        "E_CONFIRMATION_STALE",
                        "确认能力已失效；请重新 prepare＋confirm",
                    )
                consumed_error = AppError(
                    "E_CONFIRMATION_CONSUMED", "该确认已使用（无双重计算）"
                )
                if consumption.get("record_id"):
                    consumed_error.extra["record_id"] = consumption["record_id"]
                raise consumed_error
            # §12.3：registry→store 同一临界区重取指针/束、重查 current＋next
            # guard 并完整冻结后才消费；锁内不运行引擎/网络。
            context = registry.consume_for_execution(
                sid,
                confirmation_id,
                target=target,
                store=request.app.state.store,
                guard=getattr(request.app.state, "update_scheduler", None),
            )
            consumption = registry.get_consumption(sid, confirmation_id)
            frozen_warnings = list((consumption or {}).get("warnings") or [])
            tax_type = context["tax_type"]
            # 引擎/core 只从冻结快照独立解码（C10/C12：工作副本污染不影响
            # 记录证据；消费后禁止 get_prepared/get_bundle 数值重取）。
            facts = canonical_loads(context["canonical_input_bytes"])
            bundle = canonical_loads(context["verified_bundle_bytes"])
            try:
                result = run_engine(
                    tax_type,
                    copy.deepcopy(facts),
                    copy.deepcopy(bundle),
                    availability=context["availability_context"],
                )
                core = build_core(
                    tax_type,
                    facts,
                    context["binding"],
                    result,
                    bundle,
                )
            except AppError as exc:
                registry.fail_execution(context, exc.code)
                raise
            except Exception:
                # 普通程序异常同样进入 failed 终态（P4）；对外安全 500 由
                # SecurityMiddleware 兜底（脱敏/ACK/no-store）。
                registry.fail_execution(context, "E_INTERNAL")
                raise
            # §12.3：原子完成；clear/epoch/deadline → discarded（无 record/链接）
            receipt = registry.complete_execution(
                context,
                core,
                store=request.app.state.store,
                guard=getattr(request.app.state, "update_scheduler", None),
            )
            if receipt["outcome"] == "discarded":
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "会话已清空或授权已到期；本次执行已丢弃（不写记录、"
                    "不返回旧税额）",
                )
            # REQ-18：消费时冻结的离线模板提示并入响应 warnings（不得被
            # completion 期 notice 改写；不入 core/core_hash）
            warnings = list(core["warnings"] or []) + frozen_warnings
            return JSONResponse(
                status_code=200,
                content={
                    "record_id": receipt["record_id"],
                    "status": core["status"],
                    "binding": core["binding"],
                    "amounts": core["amounts"],
                    "blocked_reason": core["blocked_reason"],
                    "core_hash": receipt["core_hash"],
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
        """原包重放（C3.1 replay 行；005 §2.2）：先完成 engine/hash/schema/
        固定 profile 兼容检查，再经可信 provider 取「原 binding/原 bundle＋
        当前 guard」的两税 AvailabilityContext；不改写原记录。"""
        body = await _read_json(request)
        record_id = body.get("record_id") if isinstance(body, dict) else None
        if record_id is not None and not isinstance(record_id, str):
            raise AppError(
                "E_INPUT_TYPE",
                "record_id 必须为字符串（C3.1 replay）",
                field="record_id",
            )
        registry, sid = _session(request)
        record = registry.get_record(sid, record_id)
        if record is None:
            raise StarletteHTTPException(status_code=404, detail="record not found")
        core = record["core"]

        def availability_provider(
            tax_type: str, facts: Any, binding: Any, bundle: Any
        ) -> dict:
            """可信来源：当前 app-owned guard 对原 bundle 目标年度准入。

            005:62/:66：binding 恒为 record 原四元组（不 pin_current）；guard
            缺失/畸形/unknown/blocked → admit_runtime 抛 422 语义错误。
            005:71：trusted_offline 等 admission warnings 原样转交 replay 结果。
            """
            guard = getattr(request.app.state, "update_scheduler", None)
            guard_read = rules_runtime.read_guard(
                guard, tax_type=tax_type, facts=facts
            )
            admission = rules_runtime.admit_runtime(
                binding,
                bundle,
                tax_type=tax_type,
                facts=facts,
                guard_read=guard_read,
            )
            return {
                "availability_context": admission["availability_context"],
                "warnings": list(admission.get("warnings") or []),
            }

        result = recompute_for_replay(
            core,
            request.app.state.store,
            ENGINE_VERSION,
            availability_provider=availability_provider,
            guard=getattr(request.app.state, "update_scheduler", None),
        )
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
        if record_id is not None and not isinstance(record_id, str):
            raise AppError(
                "E_INPUT_TYPE",
                "record_id 必须为字符串（C3.1 打印）",
                field="record_id",
            )
        record = registry.get_record(sid, record_id)
        if record is None:
            raise StarletteHTTPException(status_code=404, detail="record not found")
        html = render_print_html(str(record_id), record["core"])
        return HTMLResponse(
            content=html,
            headers={"Content-Disposition": "inline", "Cache-Control": "no-store"},
        )

    # ------------------------------------------------- AI consent（C4.5–C4.8）
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
        # 005:698：外发前的本轮版本快照（出站返回后、处理 tool_calls 前重核）
        round_epoch = registry.session_epoch
        round_revision = registry.current_input_revision(sid)
        # C5.4：撤回已 consumed 授权不回滚状态、只 bump consent_epoch——故
        # 本轮必须同时冻结 consent_epoch，出站后比较；否则撤回后的迟到响应
        # 仍会执行工具（审计负例）。
        round_consent_epoch = registry.consent_epoch
        # ⑥发送前原子消费（consumed 终态，不可回滚）→ ⑦经接缝原样发送冻结字节
        consent_store.transition(sid, consent_id, "consumed")
        # 安全批次B（M4）：同步 HTTPX 外发移出事件循环（to_thread），并以
        # 端到端总期限兜底（45s > 传输 30s 超时）；超时按 E_MODEL_TIMEOUT
        # 分类，迟到线程结果按 §005:698 整轮丢弃语义不消费。
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    dispatch_outbound,
                    getattr(request.app.state, "model_outbound", None),
                    config["upstream_endpoint"],
                    config["api_key"],
                    item["frozen_bytes"],
                ),
                timeout=MODEL_OUTBOUND_TOTAL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise AppError(
                "E_MODEL_TIMEOUT",
                "模型外发超过端到端总期限（45s）；请稍后重试",
            ) from exc
        # 005:698：迟到响应整轮丢弃——epoch/revision/consent/consent_epoch 任一
        # 已变即不执行任何工具（无 executed 条目、无记录、不消费任何确认）。
        current_consent = consent_store.get(sid, consent_id)
        if (
            registry.session_epoch != round_epoch
            or registry.current_input_revision(sid) != round_revision
            or registry.consent_epoch != round_consent_epoch
            or current_consent is None
            or current_consent.get("status") != "consumed"
        ):
            return JSONResponse(
                status_code=200,
                content={
                    "consent_id": consent_id,
                    "sent_bytes": len(item["frozen_bytes"]),
                    "status": "discarded",
                    "content": None,
                    "unconfirmed": True,
                },
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
            # C3.6 工具层：封闭注册表＋strict 参数＋确认门控（不绕过三段确认）；
            # 005:912：同一 guard 快照＋本轮 prepared 四元组/current-next 预检。
            guard = getattr(request.app.state, "update_scheduler", None)
            binding = _full_binding(request)
            ctx = ToolContext(
                registry,
                sid,
                request.app.state.store,
                binding,
                guard=guard,
                availability_context=build_precheck_context(
                    registry, sid, guard, binding
                ),
                pinned_bundle=request.app.state.store.get_bundle(
                    binding["bundle_id"]
                ),
                session_epoch=registry.session_epoch,
                input_revision=registry.current_input_revision(sid),
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
        """触发检查；进行中 → 202＋现有 job_id（singleflight 幂等，C6.6）。

        安全批次B（M4）：同步更新流水线移出事件循环（to_thread），避免
        手动检查期间阻塞全部 API/页面请求；不设外层终止（避免打断
        singleflight 作业状态机，作业自身有超时与隔离）。
        """
        scheduler = _scheduler(request)
        job = await asyncio.to_thread(scheduler.trigger_manual)
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
        """clear＝epoch+1、input_revision=0 的 epoch 边界（非 reservation）；
        ACK 由本操作结果直出（不靠事后 current 查询）；同操作真删除该 sid 的
        全部 AI consent 状态与冻结负载（R2/Bu2：删除非失效）。"""
        registry, sid = _session(request)
        epochs = registry.clear_session(sid, consent_store=consent_store)
        request.state.edit_ack = {
            "session_epoch": epochs["session_epoch"],
            "input_revision": 0,
        }
        return {"status": "cleared", **epochs}

    # ------------------------------------------------------------------ pages
    @app.get("/")
    async def index_page(request: Request) -> Any:
        # 005:927/004:138：coverage_items() 恰调用一次；同一本地视图传入聊天
        # 面板（确定性本地指引，非模型文本；视觉完整链留 W3）。
        items = coverage_items()
        guidance = chat_coverage_guidance(items)
        view_json = json.dumps(
            {"items": items, "guidance": guidance},
            ensure_ascii=False,
            sort_keys=True,
        )
        view_json = (
            view_json.replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "coverage_items": items,
                "coverage_guidance": guidance,
                "coverage_view_json": view_json,
            },
        )

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
    except AppError:
        # 安全批次B（M4）：读体阶段的安全限流（如按累计字节 413）必须穿透，
        # 不得被归类为 JSON 解析错误
        raise
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


async def _read_bounded_json(request: Request) -> Any:
    """§12.1 有界读取：累计 >1MB 即 413（超限 chunk 不入聚合 buffer）。

    C2.7/R3⑦：JSON 解析复用 canonical_loads（解析期拒绝重复键）。
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise AppError(
                "E_INPUT_OVERSIZE", "请求体超过 1MB 上限（C2.9）", field="body"
            )
        chunks.append(bytes(chunk))
    raw = b"".join(chunks)
    try:
        return canonical_loads(raw.decode("utf-8"))
    except Exception:
        raise AppError(
            "E_INPUT_TYPE",
            "请求体不是合法 JSON（或含重复键；C2.7 canonical 拒绝重复键）",
            field="body",
        ) from None
