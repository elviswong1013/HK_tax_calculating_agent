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

import json
import secrets
import time
from typing import Any

from app.core.errors import AppError
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
    """工具层执行上下文（会话内存＋规则存储＋当前绑定；由 chat 路由注入）。"""

    def __init__(self, registry: Any, sid: str | None, store: Any, binding: dict) -> None:
        self.registry = registry
        self.sid = sid
        self.store = store
        self.binding = binding


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


def _rejected(call: dict, code: str, message_zh: str) -> dict[str, Any]:
    """拒绝该调用（无部分执行；result 恒为 None）。"""
    return _entry(
        call, outcome="rejected", error={"code": code, "message_zh": message_zh}
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
    key = "proposed_input" if name == "propose_input" else "checked_input"
    return _entry(
        call,
        outcome="executed",
        result={
            key: arguments["input"],
            "note": "只读；不产生任何确认或授权权威（C3.6）",
        },
    )


def _run_read_result(
    call: dict, arguments: dict, ctx: ToolContext
) -> dict[str, Any]:
    """read_result：本会话已完成记录的权威金额（金额仅来自服务器记录）。"""
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
            "status": core.get("status"),
            "amounts": dict(core.get("amounts") or {}),
        },
    )


def _run_calculate_confirmed(
    call: dict, arguments: dict, ctx: ToolContext
) -> dict[str, Any]:
    """calculate_confirmed：仅引用服务器已存在的确认能力（C3.2.2/C3.2.3 同构
    校验阶梯；错目标不消费；伪造/缺失确认 → E_CONFIRMATION_REQUIRED）。"""
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
    # 错目标 → 拒绝且不消费（C3.2.2/C3.2.3）
    requested_target = arguments.get("execute_target")
    if requested_target is not None and requested_target != confirmation["execute_target"]:
        return _rejected(
            call,
            "E_CONFIRMATION_TARGET_MISMATCH",
            "请从发起确认的同一税项入口执行",
        )
    if confirmation.get("consumed"):
        return _rejected(call, "E_CONFIRMATION_CONSUMED", "该确认已使用（无双重计算）")
    if (
        registry.session_epoch != confirmation.get("session_epoch")
        or time.monotonic() - confirmation["created_at"] > confirmation["ttl"]
    ):
        return _rejected(
            call, "E_CONFIRMATION_STALE", "确认能力已失效；请重新 prepare＋confirm"
        )
    if confirmation.get("input_revision") != registry.current_input_revision(ctx.sid):
        return _rejected(
            call, "E_CONFIRMATION_STALE", "输入已修订；请重新 prepare＋confirm"
        )
    if confirmation["binding"] != ctx.binding:
        return _rejected(
            call, "E_CONFIRMATION_STALE", "规则绑定已变化；请重新 prepare＋confirm"
        )
    if not registry.mark_consumed(ctx.sid, confirmation["confirmation_id"]):
        return _rejected(call, "E_CONFIRMATION_CONSUMED", "该确认已使用（无双重计算）")

    # 引擎执行（app/engines/* 纯函数；规则束按确认 binding 精确载入）
    prepared = registry.get_prepared(ctx.sid, confirmation.get("prepared_id", ""))
    facts = prepared["input"] if prepared is not None else {}
    bundle = ctx.store.get_bundle(confirmation["binding"]["bundle_id"])
    result = run_engine(confirmation["tax_type"], facts, bundle)
    core = build_core(
        confirmation["tax_type"], facts, confirmation["binding"], result, bundle
    )
    record_id = "r-" + secrets.token_hex(12)
    confirmation["record_id"] = record_id
    registry.save_record(ctx.sid, {"record_id": record_id, "core": core})
    return _entry(
        call,
        outcome="executed",
        result={
            "record_id": record_id,
            "status": core["status"],
            "amounts": dict(core.get("amounts") or {}),
        },
    )
