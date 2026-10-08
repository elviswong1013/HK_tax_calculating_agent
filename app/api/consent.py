"""AI 外发同意状态机（REQ-14；Annex C C4.1/C4.2/C4.5/C4.6/C4.8）。

REQ: REQ-14（AI 外发授权绑定：负载/端点/重试/上下文任何变化 → 新预览＋
新授权；绝不自动补发；撤回仅阻断未来调用）。
规格锚点:
  - C4.5：预览＝确切序列化请求字节＋SHA-256。
  - C4.6：状态机 prepared → granted → consumed；revoked/expired/stale 终态；
    TTL 5 分钟；绑定 (consent_epoch, session_epoch, 规则四元组, profile,
    endpoint, model, input_revision, payload hash)；发送前原子消费；
    HTTPX content=frozen_bytes 原样发送（不重新序列化）。
  - C4.2：密钥仅 env HKTAX_MODEL_API_KEY（服务端）；endpoint/model 为非秘密 env。
  - C5.5/C5.8：同意状态仅存内存（随会话消亡），无持久化、无审计记录。
期望值来源: 结构/状态契约；无金额期望。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Mapping

import httpx

from app.ai.adapters import HttpxModelOutbound
from app.ai.tools import wire_tools
from app.core.errors import AppError

CONSENT_TTL_SECONDS = 300  # C4.6：TTL 5 分钟

# C4.2 配置 env（密钥服务端持有；endpoint/model 非秘密）
MODEL_ENDPOINT_ENV = "HKTAX_MODEL_ENDPOINT"
MODEL_NAME_ENV = "HKTAX_MODEL_NAME"
MODEL_API_KEY_ENV = "HKTAX_MODEL_API_KEY"

# C4.6 绑定维度：本应用外发触发端点与固定 wire profile
CHAT_ENDPOINT = "/api/v1/ai/chat"
WIRE_PROFILE = "openai-compatible-chat-completions"


def model_config() -> dict[str, Any]:
    """读取模型端点配置（无密钥/端点 → 未配置；C3.7 E_MODEL_UNCONFIGURED）。"""
    return {
        "upstream_endpoint": os.environ.get(MODEL_ENDPOINT_ENV) or None,
        "model": os.environ.get(MODEL_NAME_ENV) or None,
        "api_key": os.environ.get(MODEL_API_KEY_ENV) or None,
    }


def build_payload(
    messages: list, model: str | None, upstream_endpoint: str | None
) -> dict:
    """C4.6 第①段：字段白名单构建——恰五键（endpoint/model/profile/
    messages/tools）；白名单外字段一律不进入序列化（C4.6 默认最小化）。"""
    return {
        "endpoint": upstream_endpoint,
        "model": model,
        "profile": WIRE_PROFILE,
        "messages": messages,  # §7：确认页逐字展示的完整消息上下文
        "tools": wire_tools(),  # C4.1：tools 必需（C3.6 封闭注册表）
    }


def freeze_bytes(payload: Mapping) -> bytes:
    """C4.6 第③段：一次 serialize → 冻结字节（预览/授权/发送均以此为准）。"""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """C4.5：冻结字节 SHA-256（"sha256:<64hex>"）。"""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_messages(value: Any) -> list[dict]:
    """消息上下文结构校验（逐字负载；非数组/非对象条目 → 422）。"""
    if not isinstance(value, list) or not value:
        raise AppError(
            "E_INPUT_TYPE", "messages 必须为非空消息数组", field="messages"
        )
    for index, message in enumerate(value):
        if not isinstance(message, dict):
            raise AppError(
                "E_INPUT_TYPE",
                f"消息条目必须是对象：messages[{index}]",
                field="messages",
            )
        role = message.get("role")
        if not isinstance(role, str) or not role:
            raise AppError(
                "E_INPUT_TYPE",
                f"消息 role 必须为非空字符串：messages[{index}]",
                field="messages",
            )
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise AppError(
                "E_INPUT_TYPE",
                f"消息 content 必须为字符串：messages[{index}]",
                field="messages",
            )
    return [dict(message) for message in value]


class ConsentStore:
    """进程内同意状态（内存态、按会话隔离；过期条目机会性清剪，C5.5/C5.8）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    def create(self, sid: str | None, record: Mapping[str, Any]) -> dict:
        """preview 构造 → prepared（C4.8：冻结字节＋hash＋完整绑定元组）。"""
        consent_id = "consent-" + secrets.token_hex(16)
        item = dict(record)
        item["consent_id"] = consent_id
        item["status"] = "prepared"
        item["created_at"] = time.monotonic()
        with self._lock:
            self._prune_expired_locked()
            self._items[(sid or "", consent_id)] = item
        return dict(item)

    def get(self, sid: str | None, consent_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get((sid or "", consent_id))
            return dict(item) if item is not None else None

    def transition(
        self, sid: str | None, consent_id: str, status: str
    ) -> dict[str, Any] | None:
        """C4.8 状态转移（granted/revoked/stale/expired/consumed）。"""
        with self._lock:
            item = self._items.get((sid or "", consent_id))
            if item is None:
                return None
            item["status"] = status
            return dict(item)

    def _prune_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, item in self._items.items()
            if now - item["created_at"] > CONSENT_TTL_SECONDS
        ]
        for key in expired:
            self._items.pop(key, None)


def dispatch_outbound(
    outbound: Any, upstream_endpoint: str, api_key: str, frozen: bytes
) -> dict[str, Any]:
    """C4.6 第⑦段：经出站接缝（app.state.model_outbound）原样发送冻结字节。

    接缝契约：send(upstream_endpoint, api_key, frozen) -> {"status_code",
    "body"}；传输级失败以异常表达。失败分类（C3.7）：
    httpx.TimeoutException → E_MODEL_TIMEOUT；其余异常 → E_MODEL_BAD_RESPONSE；
    返回 401/403 → E_MODEL_AUTH；429 → E_MODEL_RATE_LIMIT；其他 ≥400 或
    接缝返回形状不符 → E_MODEL_BAD_RESPONSE。outbound 为 None（接缝未装配）
    时回退生产默认 HttpxModelOutbound。

    返回 {"status_code", "body"}（2xx；wire 形状校验由内部规范层继续，
    app.ai.tools.normalize_wire_message）。
    """
    if outbound is None:
        outbound = HttpxModelOutbound()
    try:
        raw = outbound.send(upstream_endpoint, api_key, frozen)
    except httpx.TimeoutException:
        raise AppError("E_MODEL_TIMEOUT", "模型端点超时；可重试或继续本地计算") from None
    except AppError:
        raise
    except Exception:
        raise AppError("E_MODEL_BAD_RESPONSE", "模型端点通信失败") from None
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("status_code"), int)
        or not isinstance(raw.get("body"), (bytes, bytearray))
    ):
        raise AppError("E_MODEL_BAD_RESPONSE", "出站接缝返回形状不符")
    status_code = raw["status_code"]
    if status_code in (401, 403):
        raise AppError("E_MODEL_AUTH", "模型端点认证失败；请检查服务端密钥配置")
    if status_code == 429:
        raise AppError("E_MODEL_RATE_LIMIT", "模型端点限流；请稍后重试")
    if status_code >= 400:
        raise AppError("E_MODEL_BAD_RESPONSE", "模型端点返回非 2xx 响应")
    return {"status_code": status_code, "body": bytes(raw["body"])}
