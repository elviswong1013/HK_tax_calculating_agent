"""C5 安全：回环绑定校验、Host/Origin 精确一致、CSRF 能力、载荷上限、响应头。

REQ-14 基础面；Annex C C5.1/C5.2/C5.3/C5.5/C5.7 与 C2.9（载荷 ≤1MB）。
- validate_bind_host(host)：精确集合 {127.0.0.1, localhost}，非回环拒绝启动。
- SecurityMiddleware（外层 ASGI 中间件）：
  ① Host 精确校验（合法集合 127.0.0.1:<port>｜localhost:<port>；不信任
     X-Forwarded-*，E_HOST 403）；
  ② 不安全方法（POST/PUT/PATCH/DELETE）：Origin 缺失/null/与 Host 不完全一致
     → E_ORIGIN 403；须回呈服务器持有的 CSRF 能力（X-CSRF-Token），否则
     E_CSRF 403；content-length 超过 1MB → E_INPUT_OVERSIZE 413（字段解析前判定）；
  ③ 安全 GET：建立不透明 HttpOnly SameSite=Strict 会话 cookie，并经
     X-CSRF-Token 响应头发放 CSRF 能力（C5.2/C5.3）；
  ④ /api/* 响应一律注入 Cache-Control: no-store（C5.5/C5.7）；records download
     ／report print 按路由注入 Content-Disposition: attachment／inline（C5.7），
     对错误响应同样成立。
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
from hashlib import sha256
from http.cookies import SimpleCookie
from typing import Any

from starlette.datastructures import Headers

from app.config import (
    CONFIRMATION_TTL_SECONDS,
    DEFAULT_PORT,
    MAX_BODY_BYTES,
    PREPARE_TTL_SECONDS,
    SESSION_COOKIE_NAME,
)
from app.core.errors import AppError

_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LoopbackBindError(ValueError):
    """非回环绑定地址（C5.1 仅允许 127.0.0.1 / localhost）。"""


def validate_bind_host(host: str) -> str:
    """受控启动入口校验：host ∈ {127.0.0.1, localhost} 精确集合，否则拒绝启动。"""
    if host not in _LOOPBACK_BIND_HOSTS:
        raise LoopbackBindError(
            f"非回环绑定地址被拒绝：{host!r}（C5.1 仅允许 127.0.0.1 / localhost）"
        )
    return host


def _hmac_hex(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), sha256).hexdigest()


class SessionRegistry:
    """内存态会话／CSRF／prepared／确认／记录（C5.3/C5.4：随进程消亡，无财务持久化）。"""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)  # C5.3：每次运行随机签名密钥
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self.session_epoch = 1
        self.consent_epoch = 1

    # —— cookie 签名（不透明会话 id）——
    def cookie_for(self, sid: str) -> str:
        return f"{sid}.{_hmac_hex(self._key, sid)}"

    def sid_from_cookie(self, cookie_value: str | None) -> str | None:
        if not cookie_value or "." not in cookie_value:
            return None
        sid, _, signature = cookie_value.rpartition(".")
        if not sid or not hmac.compare_digest(_hmac_hex(self._key, sid), signature):
            return None
        return sid

    def ensure_session(self, cookie_value: str | None) -> tuple[str, str | None]:
        """返回 (sid, set_cookie)；已持有效会话时 set_cookie 为 None。"""
        sid = self.sid_from_cookie(cookie_value)
        if sid is not None:
            with self._lock:
                if sid in self._sessions:
                    return sid, None
        new_sid = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[new_sid] = {
                "csrf": secrets.token_urlsafe(32),
                "prepared": {},
                "confirmations": {},
                "records": {},
                "input_revision": 0,
            }
        return new_sid, self.cookie_for(new_sid)

    # —— CSRF 能力（C5.3：服务器持有）——
    def csrf_token(self, sid: str | None) -> str | None:
        with self._lock:
            session = self._sessions.get(sid or "")
        return None if session is None else session["csrf"]

    def check_csrf(self, sid: str | None, presented: str | None) -> bool:
        with self._lock:
            session = self._sessions.get(sid or "")
        if session is None or not presented:
            return False
        return hmac.compare_digest(session["csrf"], presented)

    def clear_session(self, sid: str | None) -> dict[str, int]:
        """C5.4：清空 → 两 epoch 均 ++，删除会话内存 records 与挂起能力。"""
        with self._lock:
            if sid and sid in self._sessions:
                self._sessions[sid] = {
                    "csrf": self._sessions[sid]["csrf"],
                    "prepared": {},
                    "confirmations": {},
                    "records": {},
                    "input_revision": 0,
                }
            self.session_epoch += 1
            self.consent_epoch += 1
            return {
                "session_epoch": self.session_epoch,
                "consent_epoch": self.consent_epoch,
            }

    # —— prepared（C3.2.1）——
    def save_prepared(self, sid: str | None, prepared: dict[str, Any]) -> None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is not None:
                session["prepared"][prepared["prepared_id"]] = prepared

    def get_prepared(self, sid: str | None, prepared_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return None
            return session["prepared"].get(prepared_id)

    def drop_prepared(self, sid: str | None, prepared_id: str) -> None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is not None:
                session["prepared"].pop(prepared_id, None)

    def next_input_revision(self, sid: str | None) -> int:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return 1
            session["input_revision"] += 1
            return session["input_revision"]

    def current_input_revision(self, sid: str | None) -> int:
        """当前输入修订号（只读；C3.2.2 确认绑定与 C4.6 consent 绑定校验用）。"""
        with self._lock:
            session = self._sessions.get(sid or "")
            return int(session["input_revision"]) if session else 0

    def bump_consent_epoch(self) -> dict[str, int]:
        """C5.4：AI 撤回/模型配置变更 → consent_epoch++（session_epoch 不变，
        本地记录/确认能力全部保留）。"""
        with self._lock:
            self.consent_epoch += 1
            return {
                "session_epoch": self.session_epoch,
                "consent_epoch": self.consent_epoch,
            }

    # —— confirmation（C3.2.2/C3.2.3：原子消费）——
    def save_confirmation(self, sid: str | None, confirmation: dict[str, Any]) -> None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is not None:
                session["confirmations"][confirmation["confirmation_id"]] = confirmation

    def peek_confirmation(self, sid: str | None, confirmation_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return None
            return session["confirmations"].get(confirmation_id)

    def mark_consumed(self, sid: str | None, confirmation_id: str) -> bool:
        """原子消费：已消费或不存在 → False（无双重计算）。"""
        with self._lock:
            session = self._sessions.get(sid or "")
            confirmation = (
                session["confirmations"].get(confirmation_id) if session else None
            )
            if confirmation is None or confirmation.get("consumed"):
                return False
            confirmation["consumed"] = True
            return True

    # —— session records（内存态；session_epoch 变更即删除）——
    def save_record(self, sid: str | None, record: dict[str, Any]) -> None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is not None:
                session["records"][record["record_id"]] = record

    def get_record(self, sid: str | None, record_id: str | None) -> dict[str, Any] | None:
        if not record_id:
            return None
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return None
            return session["records"].get(record_id)


# C5.7：Content-Disposition 按路由注入（对错误响应同样成立）
_CONTENT_DISPOSITION_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/api/v1/records/[^/]+/download$"), "attachment"),
    (re.compile(r"^/api/v1/report/print$"), "inline"),
)


class SecurityMiddleware:
    """外层 ASGI 安全中间件：Host → Origin → CSRF → 载荷上限 → 响应头注入。"""

    def __init__(
        self,
        app: Any,
        registry: SessionRegistry,
        *,
        default_port: int = DEFAULT_PORT,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self.app = app
        self.registry = registry
        self.default_port = default_port
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host_header = headers.get("host", "")
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        is_api = path.startswith("/api/")

        if host_header not in self._allowed_hosts(scope):
            await self._send_error(
                send,
                AppError("E_HOST", "Host 不在合法回环精确集合内（C5.1）"),
                is_api,
            )
            return

        cookie_value = self._session_cookie(headers)
        state = scope.setdefault("state", {})

        if method in _UNSAFE_METHODS:
            sid = self.registry.sid_from_cookie(cookie_value)
            state["session_id"] = sid
            origin = headers.get("origin")
            if origin is None or origin == "null" or origin != f"http://{host_header}":
                await self._send_error(
                    send,
                    AppError(
                        "E_ORIGIN",
                        "Origin 缺失、为 null 或与 Host 不完全一致（C5.2）",
                    ),
                    is_api,
                )
                return
            presented = headers.get("x-csrf-token")
            if not self.registry.check_csrf(sid, presented):
                await self._send_error(
                    send,
                    AppError("E_CSRF", "缺少或无效的 CSRF 能力（C5.2/C5.3）"),
                    is_api,
                )
                return
            content_length = headers.get("content-length")
            if (
                content_length is not None
                and content_length.isdigit()
                and int(content_length) > self.max_body_bytes
            ):
                await self._send_error(
                    send,
                    AppError("E_INPUT_OVERSIZE", "请求体超过 1MB 上限（C2.9）"),
                    is_api,
                )
                return
            set_cookie: str | None = None
        else:
            sid, set_cookie = self.registry.ensure_session(cookie_value)
            state["session_id"] = sid

        state["session_epoch"] = self.registry.session_epoch
        state["consent_epoch"] = self.registry.consent_epoch

        async def send_wrapper(message: dict) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            response_headers: list[tuple[bytes, bytes]] = [
                (key, value)
                for key, value in message.get("headers", [])
                if isinstance(key, bytes)
            ]

            def _has(name: bytes) -> bool:
                return any(key.lower() == name for key, _ in response_headers)

            if is_api and not _has(b"cache-control"):
                response_headers.append((b"cache-control", b"no-store"))
            for pattern, disposition in _CONTENT_DISPOSITION_ROUTES:
                if pattern.match(path):
                    response_headers = [
                        (key, value)
                        for key, value in response_headers
                        if key.lower() != b"content-disposition"
                    ]
                    response_headers.append(
                        (b"content-disposition", disposition.encode("ascii"))
                    )
                    break
            if set_cookie is not None:
                response_headers.append(
                    (
                        b"set-cookie",
                        (
                            f"{SESSION_COOKIE_NAME}={set_cookie};"
                            " Path=/; HttpOnly; SameSite=Strict"
                        ).encode("ascii"),
                    )
                )
            if not _has(b"x-csrf-token"):
                token = self.registry.csrf_token(sid)
                if token:
                    response_headers.append((b"x-csrf-token", token.encode("ascii")))
            await send({**message, "headers": response_headers})

        await self.app(scope, receive, send_wrapper)

    # —— 内部工具 ——
    def _allowed_hosts(self, scope: dict) -> tuple[str, str]:
        server = scope.get("server")
        port = self.default_port
        if server and len(server) >= 2 and server[1]:
            port = int(server[1])
        return (f"127.0.0.1:{port}", f"localhost:{port}")

    @staticmethod
    def _session_cookie(headers: Headers) -> str | None:
        raw = headers.get("cookie", "")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None  # Cookie 头解析失败按无会话处理
        morsel = jar.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    async def _send_error(self, send: Any, err: AppError, is_api: bool) -> None:
        body = json.dumps(err.to_payload(), ensure_ascii=False).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if is_api:
            response_headers.append((b"cache-control", b"no-store"))
        await send(
            {
                "type": "http.response.start",
                "status": err.http_status,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "CONFIRMATION_TTL_SECONDS",
    "PREPARE_TTL_SECONDS",
    "LoopbackBindError",
    "SecurityMiddleware",
    "SessionRegistry",
    "validate_bind_host",
]
