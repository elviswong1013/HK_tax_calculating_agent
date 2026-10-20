"""C3.6 模型工具注册表与响应双层契约（REQ-11；chat 工具层）。

封闭 4 工具（无 settings/update/rollback 工具）：
  - propose_input / validate_input：只读；不产生任何确认/授权权威；
  - calculate_confirmed：仅能引用服务器已存在的确认能力（无伪造用户标志）；
  - read_result：读取本会话已完成记录的权威金额（C3.4 六键）。

双层契约（C3.6）：
  - 上游 wire profile（不可信传输层，仅结构校验）：choices[].message；
    message.content 可为 null；function.arguments 为 JSON 字符串（wire 层
    不解析、不做语义校验）；结构校验失败 → E_MODEL_BAD_RESPONSE。
  - 内部规范消息：{content, tool_calls: [{id, name, arguments}]}——id 保留
    wire 调用 id；arguments 解析失败/重复 tool_call id/类型不符 →
    E_MODEL_BAD_RESPONSE；批次限额：单条消息 tool_calls ≤4，违规整体
    E_MODEL_BAD_RESPONSE。
  - name ∉ 注册表 → 无动作（note=unknown_tool_ignored；不猜测映射、不报
    5xx；needs_confirmation=True——新工具建议交用户确认）。
  - arguments strict：未知/多余/类型不符/缺必填 → 拒绝该调用（无部分执行）。

外发 wire tools 描述保持最小（名称＋中文用途说明）：strict 参数 schema 在
服务端随实现冻结（C3.6），不把内部字段名批量外发（C4.6 ①段默认最小化）。
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.api.validation import validate_prepare_body
from app.core.errors import AppError
from app.core.hashing import (
    bundle_content_hash,
    canonical_json_bytes,
    canonical_loads,
)
from app.rules import runtime as rules_runtime
from app.rules.admission import verify_fixed_profile
from app.web.records import build_core, run_engine

# C3.6 封闭工具注册表
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "propose_input",
        "validate_input",
        "calculate_confirmed",
        "read_result",
    }
)

# C3.6 批次限额：单条助手消息 tool_calls ≤4
MAX_TOOL_CALLS_PER_MESSAGE = 4

# 外发 wire tools（C4.1：tools 必需；最小描述，名称＋用途）
_WIRE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "propose_input",
            "description": "提交拟议计算输入供用户审阅；只读，不产生任何确认或授权。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_input",
            "description": "对拟议输入做只读结构检查；结果不构成任何确认或授权。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_confirmed",
            "description": "引用服务器已存在的确认能力执行计算；须提供有效确认凭据。",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_result",
            "description": "读取本会话已完成计算的权威结果；金额逐键来自服务器记录。",
        },
    },
]

# strict 参数 schema（随实现冻结；服务端强制，非外发内容）
_TOOL_PARAMS_REQUIRED: dict[str, tuple[tuple[str, type], ...]] = {
    "propose_input": (("input", dict),),
    "validate_input": (("input", dict),),
    "calculate_confirmed": (("confirmation_id", str),),
    "read_result": (("record_id", str),),
}
_TOOL_PARAMS_OPTIONAL: dict[str, tuple[tuple[str, type], ...]] = {
    "calculate_confirmed": (("execute_target", str),),
}


def wire_tools() -> list[dict[str, Any]]:
    """C4.1：外发负载 tools 注册表（封闭 4 工具；每次返回新列表防共享变更）。"""
    return [dict(tool) for tool in _WIRE_TOOLS]


# ------------------------------------------------------------------ wire 规范化
def normalize_wire_message(body: bytes) -> dict[str, Any]:
    """wire → 内部规范消息（C3.6 双层）：{content, tool_calls}。

    形状校验失败／批次 >4／arguments 非法 JSON 或非对象／重复 tool_call id →
    AppError("E_MODEL_BAD_RESPONSE")（整条消息按 502 处理）。
    """
    try:
        data = json.loads(body.decode("utf-8"))
        message = data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise ValueError("message 不是对象")
    except Exception:
        raise AppError(
            "E_MODEL_BAD_RESPONSE",
            "模型响应形状不符合 wire profile（C3.6）",
        ) from None

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise AppError(
            "E_MODEL_BAD_RESPONSE", "模型响应 content 类型不符（C3.6）"
        )

    wire_calls = message.get("tool_calls")
    if wire_calls is None:
        wire_calls = []
    if not isinstance(wire_calls, list):
        raise AppError(
            "E_MODEL_BAD_RESPONSE", "模型响应 tool_calls 形状不符（C3.6）"
        )
    if len(wire_calls) > MAX_TOOL_CALLS_PER_MESSAGE:
        raise AppError(
            "E_MODEL_BAD_RESPONSE",
            f"单条消息 tool_calls 超过批次限额 {MAX_TOOL_CALLS_PER_MESSAGE}（C3.6）",
        )

    calls: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for wire_call in wire_calls:
        try:
            call_id = wire_call["id"]
            function = wire_call["function"]
            name = function["name"]
            arguments_raw = function["arguments"]
            if wire_call.get("type") != "function":
                raise ValueError("tool_call type 非 function")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("tool_call id 缺失或类型不符")
            if not isinstance(name, str) or not name:
                raise ValueError("function.name 缺失或类型不符")
            if not isinstance(arguments_raw, str):
                raise ValueError("function.arguments 必须为 JSON 字符串")
        except Exception:
            raise AppError(
                "E_MODEL_BAD_RESPONSE",
                "模型响应 tool_call 形状不符 wire profile（C3.6）",
            ) from None
        if call_id in seen_ids:
            raise AppError(
                "E_MODEL_BAD_RESPONSE", f"重复 tool_call id（C3.6）：{call_id}"
            )
        seen_ids.add(call_id)
        try:
            arguments = json.loads(arguments_raw)
        except Exception:
            raise AppError(
                "E_MODEL_BAD_RESPONSE",
                "tool_call arguments 不是合法 JSON（C3.6）",
            ) from None
        if not isinstance(arguments, dict):
            raise AppError(
                "E_MODEL_BAD_RESPONSE",
                "tool_call arguments 必须为 JSON 对象（C3.6）",
            )
        calls.append({"id": call_id, "name": name, "arguments": arguments})
    return {"content": content, "tool_calls": calls}


# ------------------------------------------------------------------ 工具执行
class ToolContext:
    """工具层执行上下文（会话内存＋规则存储＋pinned 绑定＋只读 precheck）。

    005:912：`guard` 为本轮 `app.state.update_scheduler` 快照（与 HTTP 共用
    同一对象）；`availability_context` 为本轮 prepared 四元组 binding＋
    current/next AvailabilityContext 的预检快照（只读；不构成确认/授权）。
    """

    def __init__(
        self,
        registry: Any,
        sid: str | None,
        store: Any,
        binding: dict,
        *,
        guard: Any = None,
        availability_context: Any = None,
        pinned_bundle: Any = None,
        session_epoch: int | None = None,
        input_revision: int | None = None,
    ) -> None:
        self.registry = registry
        self.sid = sid
        self.store = store
        self.binding = binding
        self.guard = guard
        self.availability_context = (
            copy.deepcopy(availability_context)
            if availability_context is not None
            else {}
        )
        # 005:912：pinned bundle 与调用开始 epoch/revision 同属只读 precheck
        # context；不构成 prepare/确认能力。
        self.pinned_bundle = (
            copy.deepcopy(pinned_bundle)
            if pinned_bundle is not None
            else None
        )
        self.session_epoch = session_epoch
        self.input_revision = input_revision


def build_precheck_context(
    registry: Any, sid: str | None, guard: Any, binding: dict
) -> dict[str, Any]:
    """005:912：由当前 prepared 快照＋可信 guard 构造只读 precheck context。

    - binding 取本轮 prepared 的四元组（无 prepared 时退回当前 pinned 绑定）；
    - current/next 为 guard 按期间表的规范化判定；guard 缺失/畸形/不可核验
      时仅保留 binding（预检不构成放行，实际执行仍经 consume_for_execution）。
    """
    prepared = None
    reader = getattr(registry, "current_prepared", None)
    if callable(reader):
        prepared = reader(sid)
    resolved_binding = (
        prepared.get("binding") if isinstance(prepared, dict) else None
    )
    context: dict[str, Any] = {
        "binding": copy.deepcopy(dict(resolved_binding or binding)),
        "current": None,
        "next": None,
    }
    if not isinstance(prepared, dict):
        return context
    tax_type = prepared.get("tax_type")
    facts = prepared.get("input")
    if not isinstance(tax_type, str) or not isinstance(facts, dict):
        return context
    try:
        guard_read = rules_runtime.read_guard(guard, tax_type=tax_type, facts=facts)
    except AppError:
        return context
    periods = list(guard_read.get("periods") or [])
    if periods:
        context["current"] = periods[0]
    if len(periods) > 1:
        context["next"] = periods[1]
    return context


def _entry(
    call: dict,
    *,
    outcome: str,
    note: str | None = None,
    needs_confirmation: bool = False,
    error: dict | None = None,
    result: Any = None,
) -> dict[str, Any]:
    """tool_results 条目（C3.6 契约七键：id/name/outcome/note/
    needs_confirmation/error/result）。"""
    return {
        "id": call["id"],
        "name": call["name"],
        "outcome": outcome,
        "note": note,
        "needs_confirmation": needs_confirmation,
        "error": error,
        "result": result,
    }


def _rejected(
    call: dict,
    code: str,
    message_zh: str,
    *,
    field: str | None = None,
    details: list | None = None,
    binding: dict | None = None,
    status: str | None = None,
    questions: list | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """拒绝该调用（005:916 恰八键 error；无部分执行，result 恒 None）。"""
    return _entry(
        call,
        outcome="rejected",
        error={
            "code": code,
            "message_zh": message_zh,
            "field": field,
            "details": list(details or []),
            "binding": binding,
            "status": status,
            "questions": list(questions or []),
            "blocked_reason": blocked_reason,
        },
    )


def _rejected_from_app_error(call: dict, exc: AppError) -> dict[str, Any]:
    """AppError → 005:916（保留精确字段；extra 中的状态/问题/阻断原因）。"""
    extra = exc.extra or {}
    return _rejected(
        call,
        exc.code,
        exc.message_zh,
        field=exc.field,
        details=exc.details,
        binding=exc.binding,
        status=extra.get("status"),
        questions=extra.get("questions") or [],
        blocked_reason=extra.get("blocked_reason"),
    )


def _strict_schema_error(name: str, arguments: dict) -> str | None:
    """strict 参数校验（C3.6：未知/多余/类型不符/缺必填 → 拒绝该调用）。"""
    required = dict(_TOOL_PARAMS_REQUIRED.get(name, ()))
    optional = dict(_TOOL_PARAMS_OPTIONAL.get(name, ()))
    for key, expected in required.items():
        value = arguments.get(key)
        if value is None or not isinstance(value, expected):
            return f"参数缺失或类型不符：{key}"
    allowed = set(required) | set(optional)
    extra = sorted(key for key in arguments if key not in allowed)
    if extra:
        return f"未知/多余参数：{'、'.join(extra)}"
    for key, expected in optional.items():
        if key in arguments and not isinstance(arguments[key], expected):
            return f"参数类型不符：{key}"
    return None


def execute_tool_calls(calls: list[dict], ctx: ToolContext) -> list[dict[str, Any]]:
    """执行内部规范 tool_calls（strict；单调用拒绝不阻断其余调用）。"""
    return [_execute_single(call, ctx) for call in calls]


def _precheck_read_only(arguments: dict, ctx: ToolContext) -> dict[str, Any]:
    """005:911–912：propose_input／validate_input 的 HTTP 同源只读语义预检。

    - `input` **只接受完整 HTTP 业务信封**（`tax_type/schema_version/input`）；
      裸 facts、`{}` 及任意缺信封键的输入一律 rejected（与 HTTP 同一校验器）。
    - 完整信封使用 `validate_prepare_body`（税项/schema/事实结构）＋ pinned
      bundle/hash ＋ guard admission ＋两税引擎预检；五税固定 profile 内容锚
      同源核验。
    - 不调用 prepare/invalidate、不创建任何确认/授权能力、不补默认资格。
    """
    envelope = arguments.get("input")
    if not isinstance(envelope, Mapping):
        raise AppError("E_INPUT_TYPE", "input 必须是 JSON 对象", field="input")
    if (
        ctx.session_epoch is not None
        and ctx.registry.session_epoch != ctx.session_epoch
    ) or (
        ctx.input_revision is not None
        and ctx.registry.current_input_revision(ctx.sid) != ctx.input_revision
    ):
        raise AppError(
            "E_CONFIRMATION_STALE",
            "调用的会话/输入版本已变化；提案已过期，请重新开始本轮",
        )
    tax_type, canonical_input = validate_prepare_body(dict(envelope))
    binding = ctx.binding
    bundle = ctx.pinned_bundle
    if not isinstance(bundle, Mapping):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "预检缺少 pinned bundle；拒算（fail-closed）。",
            field="bundle",
        )
    try:
        actual_hash = bundle_content_hash(bundle)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "pinned bundle 内容形状损坏（无法复核哈希）；拒算。",
        ) from exc
    if actual_hash != binding.get("bundle_hash"):
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            "pinned bundle 与四元组 binding 不符；拒算（fail-closed）。",
            field="bundle_hash",
        )
    verify_fixed_profile(bundle, tax_type=tax_type)
    guard_read = rules_runtime.read_guard(
        ctx.guard, tax_type=tax_type, facts=canonical_input
    )
    admission = rules_runtime.admit_runtime(
        binding,
        bundle,
        tax_type=tax_type,
        facts=canonical_input,
        guard_read=guard_read,
    )
    missing_components: list = []
    if tax_type in ("salaries_tax", "personal_assessment"):
        result = run_engine(
            tax_type,
            copy.deepcopy(dict(canonical_input)),
            copy.deepcopy(dict(bundle)),
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
                "存在尚未确定的资格事实；提案不可视为已检查。",
                field=field,
                extra={"questions": questions, "status": "needs_input"},
            )
        if status == "blocked":
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                str(result.get("blocked_reason") or "规则状态不可核验；拒算。"),
                field="availability",
                extra={
                    "status": "blocked",
                    "blocked_reason": result.get("blocked_reason"),
                    "questions": [
                        question
                        for question in (result.get("questions") or [])
                        if isinstance(question, dict)
                    ],
                },
            )
        # 005:915：未来缺项只进 missing_components，不阻断已知本年提案。
        missing_components = [
            str(item) for item in (result.get("missing_components") or [])
        ]
    input_bytes = canonical_json_bytes(canonical_input)
    return {
        "tax_type": tax_type,
        "schema_version": str(envelope["schema_version"]),
        "input": canonical_input,
        "canonical_input_hash": (
            "sha256:" + hashlib.sha256(input_bytes).hexdigest()
        ),
        "expected_binding": copy.deepcopy(dict(binding)),
        "missing_components": missing_components,
    }


def _execute_single(call: dict, ctx: ToolContext) -> dict[str, Any]:
    name = call["name"]
    if name not in TOOL_NAMES:
        # C3.6：未知工具 → 无动作（不猜测映射、不报 5xx）；新工具建议交用户确认
        return _entry(
            call,
            outcome="ignored",
            note="unknown_tool_ignored",
            needs_confirmation=True,
        )
    arguments = call["arguments"]
    schema_error = _strict_schema_error(name, arguments)
    if schema_error is not None:
        return _rejected(call, "E_MODEL_BAD_RESPONSE", schema_error)
    if name == "read_result":
        return _run_read_result(call, arguments, ctx)
    if name == "calculate_confirmed":
        return _run_calculate_confirmed(call, arguments, ctx)
    # propose_input / validate_input：只读（不产生任何确认/授权权威，C3.6）
    try:
        precheck = _precheck_read_only(arguments, ctx)
    except AppError as exc:
        return _rejected_from_app_error(call, exc)
    return _entry(
        call,
        outcome="executed",
        note="read_only_checked",
        result={
            "proposal": {
                "tax_type": precheck["tax_type"],
                "schema_version": precheck["schema_version"],
                "input": precheck["input"],
            },
            "canonical_input_hash": precheck["canonical_input_hash"],
            "expected_binding": precheck["expected_binding"],
            "status": "checked",
            "questions": [],
            "missing_components": list(precheck["missing_components"]),
        },
    )


def _read_result_steps(steps: Any) -> list[dict]:
    """005:919：AI read_result 仅选 scenario/selection/next_year 三类原条目。

    旧无 tag 记录（无 kind）→ []；不重建/不读取 raw input/decision。
    """
    selected: list[dict] = []
    for step in steps or []:
        if isinstance(step, Mapping) and step.get("kind") in (
            "scenario",
            "selection",
            "next_year",
        ):
            selected.append(copy.deepcopy(dict(step)))
    return selected


def _run_read_result(
    call: dict, arguments: dict, ctx: ToolContext
) -> dict[str, Any]:
    """read_result：005:919 本地最小保存结果（金额仅来自服务器记录）。"""
    record = ctx.registry.get_record(ctx.sid, arguments["record_id"])
    if record is None:
        return _rejected(
            call, "E_INPUT_MISSING", "记录不存在；仅可读取本会话已完成计算"
        )
    core = record["core"]
    return _entry(
        call,
        outcome="executed",
        result={
            "record_id": record["record_id"],
            "tax_type": core.get("tax_type"),
            "binding": dict(core.get("binding") or {}),
            "status": core.get("status"),
            "amounts": dict(core.get("amounts") or {}),
            "blocked_reason": core.get("blocked_reason"),
            "missing_components": list(core.get("missing_components") or []),
            "unsupported": list(core.get("unsupported") or []),
            "pending_verification": list(core.get("pending_verification") or []),
            "steps": _read_result_steps(core.get("steps")),
        },
    )


def _run_calculate_confirmed(
    call: dict, arguments: dict, ctx: ToolContext
) -> dict[str, Any]:
    """calculate_confirmed：与 HTTP 共用 §12.3 consume/complete/fail 原子语义
    （005:690/:913）。消费前任何失败均不消费；消费后只从 ExecutionContext
    快照解码执行，不再 get_prepared/重读 current bundle。"""
    registry = ctx.registry
    confirmation = registry.peek_confirmation(
        ctx.sid, arguments["confirmation_id"]
    )
    if confirmation is None:
        return _rejected(
            call,
            "E_CONFIRMATION_REQUIRED",
            "确认能力不存在；请先本地 prepare＋confirm",
        )
    requested_target = arguments.get("execute_target")
    target = (
        requested_target
        if requested_target is not None
        else confirmation["execute_target"]
    )
    try:
        context = registry.consume_for_execution(
            ctx.sid,
            arguments["confirmation_id"],
            target=target,
            store=ctx.store,
            guard=ctx.guard,
        )
    except AppError as exc:
        # 错目标/已消费/过期/guard 不可核验：均在此前返回且不消费（005:690）
        return _rejected_from_app_error(call, exc)

    # 005:690：引擎/core 只从冻结快照独立解码；消费后不再数值重取。
    facts = canonical_loads(context["canonical_input_bytes"])
    bundle = canonical_loads(context["verified_bundle_bytes"])
    tax_type = str(context["tax_type"])
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
        receipt = registry.complete_execution(
            context, core, store=ctx.store, guard=ctx.guard
        )
    except AppError as exc:
        registry.fail_execution(context, exc.code)
        return _rejected_from_app_error(call, exc)
    except Exception:
        registry.fail_execution(context, "E_INTERNAL")
        raise
    if receipt["outcome"] == "discarded":
        return _rejected(
            call,
            "E_CONFIRMATION_STALE",
            "会话已清空或授权已到期；本次执行已丢弃（不写记录）",
        )
    return _entry(
        call,
        outcome="executed",
        result={
            "record_id": receipt["record_id"],
            "status": core["status"],
            "amounts": dict(core.get("amounts") or {}),
        },
    )
