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

import copy
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from hashlib import sha256
from http.cookies import SimpleCookie
from typing import Any

from starlette.datastructures import Headers

from app.config import (
    CONFIRMATION_TTL_SECONDS,
    DEFAULT_PORT,
    ENGINE_VERSION,
    MAX_BODY_BYTES,
    PREPARE_TTL_SECONDS,
    SESSION_ABSOLUTE_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TTL_SECONDS,
    SESSION_REGISTRY_MAX_SESSIONS,
)
from app.core.errors import AppError
from app.core.hashing import (
    BUNDLE_CONTENT_FIELDS,
    bundle_content_hash,
    calculation_core_hash,
    canonical_json_bytes,
    canonical_loads,
)
from app.rules import runtime as _runtime

_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PREPARE_PATH = "/api/v1/calc/prepare"
# S002 §4.1 版本表 wire 字面量：`If-Match:"edit-<epoch>-<revision>"`（带引号；
# RFC 9110 If-Match entity-tag 语法要求 DQUOTE）。单一语法，不做双兼容。
_IF_MATCH_RE = re.compile(r'"edit-([0-9]+)-([0-9]+)"')


def _parse_if_match(value: str | None) -> tuple[int, int] | None:
    """If-Match `"edit-<epoch>-<revision>"` 严格形态（S002 §4.1 版本表）。"""
    if not isinstance(value, str):
        return None
    match = _IF_MATCH_RE.fullmatch(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _applied_headers(reservation: dict[str, int]) -> dict[str, str]:
    """本操作 Operation ACK（Applied=1；不换成 registry 最新值）。"""
    return {
        "X-Edit-Applied": "1",
        "X-Edit-Operation-Epoch": str(reservation["session_epoch"]),
        "X-Edit-Operation-Revision": str(reservation["input_revision"]),
    }


_V2_TAX_TYPES = ("salaries_tax", "personal_assessment")


def _expected_input_schema(tax_type: Any) -> str:
    """S002 §4.2 封闭输入 schema 契约：两税 2.0.0；其余五税 1.0.0。"""
    return "2.0.0" if tax_type in _V2_TAX_TYPES else "1.0.0"


def _context_value(context: Any, name: str) -> Any:
    """context 载体读写（plan §3：类型表示不绑定 dict/dataclass）。"""
    if isinstance(context, Mapping):
        return context.get(name)
    return getattr(context, name, None)


def _build_execution_context(
    *,
    execution_id: str,
    session_id: str | None,
    session_epoch: int,
    input_revision: int,
    confirmation_id: str,
    tax_type: str,
    schema_version: Any,
    execute_target: str,
    input_hash: Any,
    facts: Any,
    binding: Any,
    bundle: Any,
    availability_context: Any,
) -> dict[str, Any]:
    """005 §12.3：完整冻结 ExecutionContext（C11 已批准 13 材料构造职责）。

    canonical_input_bytes＝canonical 输入 bytes（hash＝input_hash，构造前与
    传入 hash 互核；不一致即拒，不冻结）；verified_bundle_bytes＝C2.8 封闭
    六字段 canonical bytes（hash＝bundle_hash）；engine/core 只从该快照独立
    解码；返回/工作副本修改不影响 registry 权威。
    """
    closed_bundle = {field: bundle[field] for field in BUNDLE_CONTENT_FIELDS}
    canonical_input_bytes = bytes(canonical_json_bytes(facts))
    if "sha256:" + sha256(canonical_input_bytes).hexdigest() != input_hash:
        # ④/§3.1：传入 hash 与重序列化 bytes 互核（不得只比缓存 hash 字段）。
        raise AppError(
            "E_CONFIRMATION_STALE",
            "输入快照与确认 canonical input hash 不符；拒绝冻结（不消费）。",
        )
    return {
        "execution_id": execution_id,
        "session_id": session_id,
        "session_epoch": session_epoch,
        "input_revision": input_revision,
        "confirmation_id": confirmation_id,
        "tax_type": tax_type,
        "schema_version": schema_version,
        "execute_target": execute_target,
        "input_hash": input_hash,
        "canonical_input_bytes": canonical_input_bytes,
        "binding": copy.deepcopy(dict(binding)),
        "verified_bundle_bytes": bytes(canonical_json_bytes(closed_bundle)),
        "availability_context": copy.deepcopy(dict(availability_context)),
    }


def _guard_comparison_key(guard_read: Mapping[str, Any]) -> tuple:
    """guard-only 比较 key：有序 ((year, status, sorted-unique reasons), …)。"""
    key = []
    for period in guard_read.get("periods") or []:
        reasons = period.get("reason_codes") or []
        key.append(
            (
                period.get("year"),
                period.get("status"),
                tuple(sorted(set(reasons))),
            )
        )
    return tuple(key)


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
        # 安全批次B（M3）：会话生命周期时钟（测试可注入假时钟）
        self._clock = time.monotonic
        # S002 §4.1/§12＋复核报告 B2：input_revision 属**每个 sid 的会话状态**
        # （clear＝epoch 边界且该 sid rev=0）；不入 binding/core 哈希。
    # —— cookie 签名（不透明会话 id）——
    def cookie_for(self, sid: str) -> str:
        return f"{sid}.{_hmac_hex(self._key, sid)}"

    def sid_from_cookie(self, cookie_value: str | None) -> str | None:
        if not cookie_value or "." not in cookie_value:
            return None
        sid, _, signature = cookie_value.rpartition(".")
        if not sid or not hmac.compare_digest(_hmac_hex(self._key, sid), signature):
            return None
        # 注：闲置 TTL 的刷新只在 ensure_session 命中有效会话时进行
        # （M3）；此处不做 touch，保证过期判定使用刷新前的 last_used。
        return sid

    # —— 安全批次B（M3）：会话 TTL 与注册表容量 ————————————————
    def _expired_locked(self, session: dict[str, Any], now: float) -> bool:
        return (
            now - session["created_at"] > SESSION_ABSOLUTE_TTL_SECONDS
            or now - session["last_used"] > SESSION_IDLE_TTL_SECONDS
        )

    def _evict_locked(self, now: float) -> None:
        for sid in [
            sid
            for sid, session in self._sessions.items()
            if self._expired_locked(session, now)
        ]:
            self._sessions.pop(sid, None)
        # 容量淘汰：调用方即将再写入 1 个新会话，需先腾出位置
        # （len - MAX + 1 个最久未用），确保写入后总数 ≤ MAX。
        overflow = len(self._sessions) - SESSION_REGISTRY_MAX_SESSIONS + 1
        if overflow > 0:
            ordered = sorted(
                self._sessions.items(), key=lambda item: item[1]["last_used"]
            )
            for sid, _ in ordered[:overflow]:
                self._sessions.pop(sid, None)

    def ensure_session(self, cookie_value: str | None) -> tuple[str, str | None]:
        """返回 (sid, set_cookie)；已持有效会话时 set_cookie 为 None。

        安全批次B（M3）：会话闲置 30 分钟或绝对 2 小时过期后按新会话处理；
        注册表超容量时按 last_used 淘汰最久未用会话（内存 DoS 防护）。
        """
        sid = self.sid_from_cookie(cookie_value)
        now = self._clock()
        if sid is not None:
            with self._lock:
                session = self._sessions.get(sid)
                if session is not None and not self._expired_locked(session, now):
                    session["last_used"] = now
                    self._evict_locked(now)
                    return sid, None
        new_sid = secrets.token_urlsafe(32)
        with self._lock:
            self._evict_locked(now)
            self._sessions[new_sid] = {
                "csrf": secrets.token_urlsafe(32),
                "prepared": {},
                "confirmations": {},
                "executions": {},
                "records": {},
                "input_revision": 0,
                "created_at": now,
                "last_used": now,
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

    def clear_session(
        self, sid: str | None, *, consent_store: Any | None = None
    ) -> dict[str, int]:
        """C5.4＋Annex 005 §12.3：清空＝epoch+1、该 sid input_revision=0 的 epoch 边界。

        - 删除挂起 prepared 与内存 records；未消费/已消费确认一律转 stale
          墓碑并清除 record 链接（迟到执行 → 409 E_CONFIRMATION_STALE，
          不返回旧 core/税额、不写 record）；
        - 同操作**真删除**该 sid 的 AI consent（全部状态条目与冻结负载；
          复核报告 R2/Bu2：clear 未清 ConsentStore 为缺陷）；
        - 两 epoch 均 ++；保留会话主体以便同一 sid 继续使用。
        """
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is not None:
                session["prepared"] = {}
                session["executions"] = {}
                for confirmation in session["confirmations"].values():
                    confirmation["stale"] = True
                    confirmation["record_id"] = None
                    confirmation["core_hash"] = None
                session["records"] = {}
                session["input_revision"] = 0
            self.session_epoch += 1
            self.consent_epoch += 1
            if consent_store is not None:
                consent_store.delete_session(sid)
            return {
                "session_epoch": self.session_epoch,
                "consent_epoch": self.consent_epoch,
                "input_revision": 0,
            }

    # —— prepared（C3.2.1）——
    def save_prepared_if_current(
        self,
        sid: str | None,
        prepared: dict[str, Any],
        expected_epoch: int,
        expected_revision: int,
    ) -> bool:
        """§12.1 末行＋R2：核验 reservation 仍当前并保存 prepared——单临界区。

        返回 False＝另一编辑/clear 已推进（调用方 409 stale，不落 prepared）。
        """
        with self._lock:
            session = self._sessions.get(sid or "")
            if (
                session is None
                or expected_epoch != self.session_epoch
                or expected_revision != int(session.get("input_revision", 0))
            ):
                return False
            session["prepared"][prepared["prepared_id"]] = copy.deepcopy(prepared)
            return True

    def get_prepared(self, sid: str | None, prepared_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return None
            prepared = session["prepared"].get(prepared_id)
            # §12.3：getter 只返回独立副本（调用方不得经引用改写冻结快照）
            return copy.deepcopy(prepared) if prepared is not None else None

    def current_prepared(self, sid: str | None) -> dict[str, Any] | None:
        """只读 precheck：本会话当前唯一 prepared 快照副本（§12.1 prepare 清空
        旧 prepared；无则 None）。仅供 ToolContext 预检，不构成执行权威。"""
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None or not session["prepared"]:
                return None
            prepared = next(iter(session["prepared"].values()))
            return copy.deepcopy(prepared)

    def current_input_revision(self, sid: str | None) -> int:
        """该 sid 当前输入修订号（只读；§12 运行时版本，不入 binding/core）。"""
        with self._lock:
            session = self._sessions.get(sid or "")
            return int(session.get("input_revision", 0)) if session is not None else 0

    def version_snapshot(self, sid: str | None) -> dict[str, int]:
        """运行时版本只读快照（§12.2 Current 头：409 冲突信息，不自动接纳）。"""
        with self._lock:
            session = self._sessions.get(sid or "")
            return {
                "session_epoch": self.session_epoch,
                "input_revision": (
                    int(session.get("input_revision", 0)) if session is not None else 0
                ),
            }

    # —— prepare reservation（唯一入口；Annex 005 §12.1）——
    def begin_prepare(
        self,
        sid: str | None,
        expected_epoch: int,
        expected_revision: int,
        *,
        consent_store: Any | None = None,
    ) -> dict[str, Any]:
        """If-Match CAS → 原子 revision++/失效（旧 prepared／未消费 confirm／
        未消费 consent）；失败返回当前只读快照（不推进）。

        只此一处推进 prepare revision：route 不得再次递增（§12.1）。
        """
        with self._lock:
            session = self._sessions.get(sid or "")
            if (
                session is None
                or expected_epoch != self.session_epoch
                or expected_revision != int(session.get("input_revision", 0))
            ):
                return {
                    "ok": False,
                    "session_epoch": self.session_epoch,
                    "input_revision": (
                        int(session.get("input_revision", 0))
                        if session is not None
                        else 0
                    ),
                }
            session["input_revision"] = int(session.get("input_revision", 0)) + 1
            revision = session["input_revision"]
            session["prepared"] = {}
            for confirmation in session["confirmations"].values():
                if not confirmation.get("consumed"):
                    confirmation["stale"] = True
            if consent_store is not None:
                consent_store.invalidate_session(sid)
            return {
                "ok": True,
                "session_epoch": self.session_epoch,
                "input_revision": revision,
            }

    def invalidate(
        self,
        sid: str | None,
        expected_epoch: int,
        expected_revision: int,
        *,
        consent_store: Any | None = None,
    ) -> dict[str, int] | None:
        """invalidate CAS（§12.3）：成功 → revision++、删除旧 prepared、未消费
        confirm 失效、records 保留；失败 → None（409 附当前快照，不推进）。"""
        with self._lock:
            session = self._sessions.get(sid or "")
            if (
                session is None
                or expected_epoch != self.session_epoch
                or expected_revision != int(session.get("input_revision", 0))
            ):
                return None
            session["input_revision"] = int(session.get("input_revision", 0)) + 1
            revision = session["input_revision"]
            session["prepared"] = {}
            for confirmation in session["confirmations"].values():
                if not confirmation.get("consumed"):
                    confirmation["stale"] = True
            if consent_store is not None:
                consent_store.invalidate_session(sid)
            return {
                "session_epoch": self.session_epoch,
                "input_revision": revision,
            }

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
                session["confirmations"][confirmation["confirmation_id"]] = (
                    copy.deepcopy(confirmation)
                )

    def issue_confirmation(
        self,
        sid: str | None,
        prepared_id: str,
        input_hash: str,
        expected_binding: dict[str, Any],
        *,
        store: Any,
    ) -> dict[str, Any]:
        """§12.3＋R2/A2：registry 锁内原子核对 prepared 身份（epoch/revision/
        schema/TTL/hash）并在**同一临界区**经 store 重读当前 binding——
        锁前 route 缓存值不得作为签发权威（指针窗口内切换 → 不签发）。

        A2/§3.2：最终读取→比较→签发提交整段由同一 app-owned store 写锁
        覆盖；发布不得穿「读取→签发」窗口。
        """
        with self._lock:
            session = self._sessions.get(sid or "")
            prepared = (
                session["prepared"].get(prepared_id) if session is not None else None
            )
            if prepared is None:
                raise AppError(
                    "E_CONFIRMATION_REQUIRED",
                    "prepared 记录不存在（已被编辑/清空失效）；请重新 prepare",
                )
            if time.monotonic() - prepared["created_at"] > prepared["ttl"]:
                session["prepared"].pop(prepared_id, None)
                raise AppError("E_CONFIRMATION_STALE", "prepared 已过期；请重新 prepare")
            if (
                prepared.get("session_epoch") != self.session_epoch
                or prepared.get("input_revision")
                != int(session.get("input_revision", 0))
            ):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "prepared 属旧 epoch/revision；请重新 prepare＋confirm",
                )
            if prepared.get("schema_version") != _expected_input_schema(
                prepared.get("tax_type")
            ):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "prepared 输入 schema 身份不符（不迁移）；请重新 prepare＋confirm",
                )
            if (
                input_hash != prepared.get("input_hash")
                or expected_binding != prepared.get("binding")
            ):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "输入或规则绑定已变化；请重新 prepare",
                )
            # A2/§3.2：registry→store 锁序内完成最终读取→比较→签发提交；
            # writer 的 publish 不得穿过「读取→签发」窗口。
            with store._write_lock:
                pinned = store.pin_current()
                current_binding = {
                    "bundle_id": pinned["bundle_id"],
                    "bundle_hash": pinned["bundle_hash"],
                    "rules_schema_version": pinned["rules_schema_version"],
                    "engine_version": ENGINE_VERSION,
                }
                if prepared["binding"] != current_binding:
                    raise AppError(
                        "E_CONFIRMATION_STALE",
                        "当前规则绑定已变化（bundle/engine）；旧 prepared 不签发能力，"
                        "请重新 prepare＋confirm",
                    )
                confirmation_id = "c-" + secrets.token_hex(32)  # 256-bit 可执行确认能力
                confirmation = {
                    "confirmation_id": confirmation_id,
                    "prepared_id": prepared["prepared_id"],
                    "tax_type": prepared["tax_type"],
                    "schema_version": prepared.get("schema_version"),
                    "input_hash": prepared["input_hash"],
                    "input_revision": prepared.get("input_revision"),
                    "binding": copy.deepcopy(dict(prepared["binding"])),
                    "execute_target": prepared["execute_target"],
                    "session_epoch": self.session_epoch,
                    "created_at": time.monotonic(),
                    "ttl": CONFIRMATION_TTL_SECONDS,
                    "consumed": False,
                    "stale": False,
                    "record_id": None,
                }
                session["confirmations"][confirmation_id] = confirmation
                return copy.deepcopy(confirmation)

    def peek_confirmation(self, sid: str | None, confirmation_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return None
            confirmation = session["confirmations"].get(confirmation_id)
            return copy.deepcopy(confirmation) if confirmation is not None else None

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

    # —— 原子消费/执行快照/完成（Annex 005 §12.3）——
    def consume_for_execution(
        self,
        sid: str | None,
        confirmation_id: str,
        *,
        target: str,
        store: Any,
        guard: Any,
    ) -> dict[str, Any]:
        """§12.3/B5/B6/C9：registry→store 同一临界区重取指针/束并重查
        current＋next guard，完整冻结 ExecutionContext 成功后才标 consumed。

        - 任何失败均不消费（错目标/epoch/revision/TTL/binding/guard/身份）；
        - 锁内禁运行引擎/网络；消费后调用方禁止再 get_prepared/读 current
          bundle；引擎/core 只从快照独立工作副本执行；clear/epoch → discarded。
        """
        with self._lock:
            session = self._sessions.get(sid or "")
            confirmation = (
                session["confirmations"].get(confirmation_id) if session else None
            )
            if confirmation is None:
                raise AppError(
                    "E_CONFIRMATION_REQUIRED", "确认能力不存在；请先 prepare＋confirm"
                )
            if confirmation.get("stale"):
                raise AppError(
                    "E_CONFIRMATION_STALE", "确认能力已失效；请重新 prepare＋confirm"
                )
            if confirmation["execute_target"] != target:
                raise AppError(
                    "E_CONFIRMATION_TARGET_MISMATCH", "请从发起确认的同一税项入口执行"
                )
            if confirmation.get("session_epoch") != self.session_epoch:
                raise AppError(
                    "E_CONFIRMATION_STALE", "确认能力已失效（epoch 变化）；"
                    "请重新 prepare＋confirm"
                )
            if confirmation.get("consumed"):
                # ②/005 §12.3＋plan §3：epoch/关联核验之后才按已消费作答；链接
                # 仅取当前 epoch 且 execution-record 关联有效的权威判定。
                consumed_error = AppError(
                    "E_CONFIRMATION_CONSUMED", "该确认已使用（无双重计算）"
                )
                linked = self._linked_record_id(session, confirmation, confirmation_id)
                if linked:
                    consumed_error.extra["record_id"] = linked
                raise consumed_error
            if time.monotonic() - confirmation["created_at"] > confirmation["ttl"]:
                confirmation["stale"] = True
                raise AppError(
                    "E_CONFIRMATION_STALE", "确认能力已过期；请重新 prepare＋confirm"
                )
            if confirmation.get("input_revision") != int(
                session.get("input_revision", 0)
            ):
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "输入已修订（input_revision 变化）；请重新 prepare＋confirm",
                )
            prepared = session["prepared"].get(confirmation.get("prepared_id", ""))
            if prepared is None:
                confirmation["stale"] = True
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "prepared 快照不存在（已被编辑失效）；请重新 prepare＋confirm",
                )
            if prepared.get("schema_version") != _expected_input_schema(
                prepared.get("tax_type")
            ):
                confirmation["stale"] = True
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "prepared 输入 schema 身份不符（不迁移）；请重新 prepare＋confirm",
                )
            if (
                confirmation.get("tax_type") != prepared.get("tax_type")
                or confirmation.get("input_hash") != prepared.get("input_hash")
                or confirmation.get("execute_target")
                != prepared.get("execute_target")
                or confirmation.get("binding") != prepared.get("binding")
            ):
                confirmation["stale"] = True
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "确认身份与 prepared 快照不符；请重新 prepare＋confirm",
                )
            # ④/§3.1：对输入内容重新形成 canonical bytes/hash 与确认 hash 互核
            #（不得只比较两个缓存 hash 字段）；不一致即拒、不消费。
            try:
                recomputed_input_bytes = bytes(canonical_json_bytes(prepared["input"]))
            except (TypeError, ValueError) as exc:
                confirmation["stale"] = True
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "prepared 输入无法重序列化（canonical bytes/hash 复核失败）；"
                    "请重新 prepare＋confirm",
                ) from exc
            if (
                "sha256:" + sha256(recomputed_input_bytes).hexdigest()
                != confirmation.get("input_hash")
            ):
                confirmation["stale"] = True
                raise AppError(
                    "E_CONFIRMATION_STALE",
                    "prepared 输入与确认 canonical input hash 不符；"
                    "请重新 prepare＋confirm",
                )
            tax_type = prepared["tax_type"]
            # B7/§3.2：读→提交窗口由同一 app-owned store 写锁覆盖；锁内重取
            # 指针/束并重查 current＋next（不接受 route 缓存的锁外判定）。
            with store._write_lock:
                pinned = store.pin_current()
                current_binding = {
                    "bundle_id": pinned["bundle_id"],
                    "bundle_hash": pinned["bundle_hash"],
                    "rules_schema_version": pinned["rules_schema_version"],
                    "engine_version": ENGINE_VERSION,
                }
                if current_binding != confirmation.get("binding"):
                    raise AppError(
                        "E_CONFIRMATION_STALE",
                        "规则绑定已变化（bundle/engine）；请重新 prepare＋confirm",
                    )
                content = store.get_bundle(current_binding["bundle_id"])
                try:
                    recomputed = bundle_content_hash(content)
                except (TypeError, ValueError) as exc:
                    raise AppError(
                        "E_RULES_STATE_UNVERIFIABLE",
                        "规则束内容形状损坏（无法复核哈希）；拒算。",
                    ) from exc
                if recomputed != current_binding["bundle_hash"]:
                    raise AppError(
                        "E_RULES_STATE_UNVERIFIABLE",
                        "规则束内容与确认 binding 哈希不符（载入损坏）；拒算。",
                    )
                facts = prepared["input"]
                guard_read = _runtime.read_guard(
                    guard, tax_type=tax_type, facts=facts
                )
                admission = _runtime.admit_runtime(
                    current_binding,
                    content,
                    tax_type=tax_type,
                    facts=facts,
                    guard_read=guard_read,
                )
                execution_id = "x-" + secrets.token_hex(16)
                # §12.3：完整冻结成功后才提交 consumed/running（构造失败不半消费）。
                context = _build_execution_context(
                    execution_id=execution_id,
                    session_id=sid,
                    session_epoch=self.session_epoch,
                    input_revision=int(session.get("input_revision", 0)),
                    confirmation_id=confirmation_id,
                    tax_type=tax_type,
                    schema_version=prepared.get("schema_version"),
                    execute_target=target,
                    input_hash=prepared.get("input_hash"),
                    facts=copy.deepcopy(facts),
                    binding=copy.deepcopy(current_binding),
                    bundle=copy.deepcopy(dict(content)),
                    availability_context=admission["availability_context"],
                )
                # C11：独立返回副本＋完整 execution entry 全部在提交前构造；
                # 此后只有登记写入，提交后直接返回预先构造副本（不再复制）。
                return_context = copy.deepcopy(context)
                execution_entry = {
                    "context": context,
                    "record_id": None,
                    "core_hash": None,
                    "failed": False,
                    "warnings": list(admission.get("warnings") or []),
                    "guard_key": _guard_comparison_key(guard_read),
                    # ③/005:692：签发时 deadline（created_at＋ttl，同一 monotonic
                    # 时钟）由权威 entry 独立保留；不扩展 context/core/binding。
                    "expires_at": confirmation["created_at"] + confirmation["ttl"],
                }
                session["executions"][execution_id] = execution_entry
                confirmation["consumed"] = True
                confirmation["execution_id"] = execution_id
                return return_context

    def fail_execution(self, context: Any, safe_error_code: str) -> None:
        """P4/005 §12.3：仅 running→failed——已存 record 的成功终态不被迟到
        fail 覆盖、已 failed 不被重写、跨 epoch 不可改。

        解析 context 所属 session/execution；仅当权威 entry 属当前 epoch、
        尚无 record 链接且未 failed 时才标 failed（不恢复能力、不写税额）。
        """
        sid = _context_value(context, "session_id")
        execution_id = _context_value(context, "execution_id")
        with self._lock:
            session = self._sessions.get(sid or "")
            entry = (
                session["executions"].get(execution_id)
                if session is not None and execution_id
                else None
            )
            if (
                entry is not None
                and entry.get("record_id") is None
                and not entry.get("failed")
                and _context_value(entry.get("context"), "session_epoch")
                == self.session_epoch
            ):
                entry["failed"] = True
                entry["error"] = safe_error_code

    def complete_execution(
        self, context: Any, core: dict[str, Any], *, store: Any, guard: Any
    ) -> dict[str, Any]:
        """原子完成：一次保存 record＋consumed 链接并返回完整 Receipt（§12.3）。

        - 以 registry 权威 entry（非调用方 context 副本）核对身份；
        - clear/epoch 变化/首次未保存且越确认 deadline → discarded（无链接）；
        - failed 后不得补写成功 record（409 E_CONFIRMATION_CONSUMED）；
        - 同 execution 同 core hash → already_stored＋原 record_id＋原执行
          epoch/revision；异 hash → 冲突（原记录字节不变）；
        - editor_result=current 仅当 revision/binding/相关 guard 同时匹配
          冻结快照（guard-only key）；guard 不可核验只能 historical，不丢弃
          合法历史结果。
        """
        sid = _context_value(context, "session_id")
        execution_id = _context_value(context, "execution_id")
        with self._lock:
            session = self._sessions.get(sid or "")
            entry = (
                session["executions"].get(execution_id)
                if session is not None and execution_id
                else None
            )
            if entry is None:
                return self._receipt(
                    "discarded",
                    None,
                    None,
                    "discarded",
                    _context_value(context, "session_epoch"),
                    _context_value(context, "input_revision"),
                )
            authority = entry["context"]
            receipt_epoch = authority.get("session_epoch")
            receipt_revision = authority.get("input_revision")
            if (
                authority.get("session_epoch") != self.session_epoch
                or authority.get("session_id") not in (None, sid)
            ):
                return self._receipt(
                    "discarded", None, None, "discarded", receipt_epoch, receipt_revision
                )
            if entry.get("failed"):
                raise AppError(
                    "E_CONFIRMATION_CONSUMED",
                    "该执行已失败（不恢复能力、不补写成功结果）",
                )
            if str(core.get("tax_type")) != authority["tax_type"]:
                raise AppError(
                    "E_CONFIRMATION_CONSUMED",
                    "完成 core 的税项与执行身份不符；拒绝保存",
                )
            if dict(core.get("binding") or {}) != authority["binding"]:
                raise AppError(
                    "E_CONFIRMATION_CONSUMED",
                    "完成 core 的规则绑定与执行快照不符；拒绝保存",
                )
            core_input = core.get("input")
            try:
                input_hash = (
                    "sha256:"
                    + sha256(canonical_json_bytes(core_input)).hexdigest()
                    if isinstance(core_input, dict)
                    else None
                )
            except (TypeError, ValueError):
                input_hash = None
            if input_hash is None or input_hash != authority.get("input_hash"):
                raise AppError(
                    "E_CONFIRMATION_CONSUMED",
                    "完成 core 的输入与执行快照不符（canonical input hash）；"
                    "拒绝保存",
                )
            core_hash = calculation_core_hash(core)
            if entry["record_id"] is not None:
                if entry["core_hash"] == core_hash:
                    editor_result = self._completion_freshness(
                        session, authority, entry, store, guard
                    )
                    return self._receipt(
                        "already_stored",
                        entry["record_id"],
                        core_hash,
                        editor_result,
                        receipt_epoch,
                        receipt_revision,
                    )
                raise AppError(
                    "E_CONFIRMATION_CONSUMED",
                    "同一确认已保存不同结果（冲突；原 record 不变）",
                )
            confirmation = session["confirmations"].get(
                authority.get("confirmation_id", "")
            )
            # ③/005:692：首次完成的 freshness 读取＋deadline 复核＋record/link
            # 提交全部置于同一 store 写锁临界区（writer 发布不得穿「读取→
            # 提交」窗口）；deadline 取签发时独立冻结于权威 entry 的值。
            with store._write_lock:
                editor_result = self._completion_freshness_locked(
                    session, authority, entry, store, guard
                )
                expires_at = entry.get("expires_at")
                if expires_at is None and confirmation is not None:
                    expires_at = confirmation["created_at"] + confirmation["ttl"]
                if expires_at is not None and time.monotonic() > expires_at:
                    # CR-P：最终提交前重核时钟；等号仍有效（stored 后同 hash
                    # 幂等不因随后到期撤销）。
                    return self._receipt(
                        "discarded", None, None, "discarded", receipt_epoch, receipt_revision
                    )
                record_id = "r-" + secrets.token_hex(12)
                # §12.3：保存冻结字节/独立深副本；getter 返回副本（调用方改写
                # 不得影响存储记录）。
                session["records"][record_id] = {
                    "record_id": record_id,
                    "core": copy.deepcopy(core),
                }
                entry["record_id"] = record_id
                entry["core_hash"] = core_hash
                if confirmation is not None:
                    confirmation["record_id"] = record_id
                    confirmation["core_hash"] = core_hash
                return self._receipt(
                    "stored",
                    record_id,
                    core_hash,
                    editor_result,
                    receipt_epoch,
                    receipt_revision,
                )

    @staticmethod
    def _receipt(
        outcome: str,
        record_id: str | None,
        core_hash: str | None,
        editor_result: str,
        execution_epoch: Any,
        execution_revision: Any,
    ) -> dict[str, Any]:
        """CompletionReceipt（005 §12.3；所有 outcome 均携带原执行身份）。"""
        return {
            "outcome": outcome,
            "record_id": record_id,
            "core_hash": core_hash,
            "editor_result": editor_result,
            "execution_epoch": execution_epoch,
            "execution_revision": execution_revision,
        }

    def _completion_freshness(
        self,
        session: Mapping[str, Any],
        authority: Mapping[str, Any],
        entry: Mapping[str, Any],
        store: Any,
        guard: Any,
    ) -> str:
        """完成阶段 freshness（guard-contract §4）：revision/binding/相关
        guard 同时匹配才 current；guard 不可核验或 key 变化 → historical。

        以同一 store 写锁覆盖 current/guard 的最终读取（幂等 already_stored
        路径独立调用；首次提交路径由 complete_execution 持锁直调 _locked）。
        """
        with store._write_lock:
            return self._completion_freshness_locked(
                session, authority, entry, store, guard
            )

    def _completion_freshness_locked(
        self,
        session: Mapping[str, Any],
        authority: Mapping[str, Any],
        entry: Mapping[str, Any],
        store: Any,
        guard: Any,
    ) -> str:
        """持锁 freshness 核（调用方已持 store 写锁；完成提交临界区内直调）。"""
        if int(session.get("input_revision", 0)) != int(
            authority.get("input_revision", -1)
        ):
            return "historical"
        try:
            facts = canonical_loads(authority.get("canonical_input_bytes") or b"{}")
        except (TypeError, ValueError):
            return "historical"
        try:
            pinned = store.pin_current()
        except AppError:
            return "historical"
        current_binding = {
            "bundle_id": pinned["bundle_id"],
            "bundle_hash": pinned["bundle_hash"],
            "rules_schema_version": pinned["rules_schema_version"],
            "engine_version": ENGINE_VERSION,
        }
        if current_binding != authority.get("binding"):
            return "historical"
        guard_read = _runtime.read_guard(
            guard, tax_type=authority.get("tax_type"), facts=facts
        )
        if not guard_read.get("verifiable"):
            return "historical"
        if _guard_comparison_key(guard_read) != entry.get("guard_key"):
            return "historical"
        return "current"

    def _linked_record_id(
        self,
        session: Mapping[str, Any] | None,
        confirmation: Mapping[str, Any],
        confirmation_id: str,
    ) -> str | None:
        """已消费确认的完成关联核验（005 §12.3/plan §3）：仅当确认与 execution
        entry 均属当前 epoch、entry 确属本确认且 record/core hash 一致时返回
        链接；旧 epoch 或关联断裂一律 None（不返旧链接、不得锁外 peek 作答）。"""
        if session is None or not confirmation.get("consumed"):
            return None
        if confirmation.get("session_epoch") != self.session_epoch:
            return None
        execution_id = confirmation.get("execution_id")
        entry = (
            session["executions"].get(execution_id) if execution_id else None
        )
        if entry is None:
            return None
        if (
            _context_value(entry.get("context"), "confirmation_id")
            != confirmation_id
            or _context_value(entry.get("context"), "session_epoch")
            != self.session_epoch
        ):
            return None
        record_id = entry.get("record_id")
        if (
            record_id is None
            or record_id != confirmation.get("record_id")
            or entry.get("core_hash") != confirmation.get("core_hash")
        ):
            return None
        return str(record_id)

    def get_consumption(
        self, sid: str | None, confirmation_id: str
    ) -> dict[str, Any] | None:
        """§12.3：已消费确认的只读副本（重复消费响应/record 链接＋消费时冻结
        的运行时 warnings；G12：完成阶段不得改写消费时提示）。

        ②/005 §12.3：仅**当前 epoch 且 execution-record 关联有效**时返回
        record/core 链接；旧 epoch 或关联断裂一律视 stale／无链接返回。
        """
        with self._lock:
            session = self._sessions.get(sid or "")
            confirmation = (
                session["confirmations"].get(confirmation_id) if session else None
            )
            if confirmation is None:
                return None
            stale = bool(confirmation.get("stale")) or (
                confirmation.get("session_epoch") != self.session_epoch
            )
            warnings: list[str] = []
            execution_id = confirmation.get("execution_id")
            if session is not None and execution_id:
                entry = session["executions"].get(execution_id)
                if entry is not None:
                    warnings = list(entry.get("warnings") or [])
            record_id = self._linked_record_id(session, confirmation, confirmation_id)
            return {
                "confirmation_id": confirmation_id,
                "consumed": bool(confirmation.get("consumed")),
                "stale": stale,
                "record_id": record_id,
                "core_hash": (
                    confirmation.get("core_hash") if record_id is not None else None
                ),
                "warnings": warnings,
            }

    # —— session records（内存态；session_epoch 变更即删除）——
    def save_record(self, sid: str | None, record: dict[str, Any]) -> None:
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is not None:
                session["records"][record["record_id"]] = copy.deepcopy(record)

    def get_record(self, sid: str | None, record_id: str | None) -> dict[str, Any] | None:
        if not record_id:
            return None
        with self._lock:
            session = self._sessions.get(sid or "")
            if session is None:
                return None
            record = session["records"].get(record_id)
            # Bu1③：getter 只读副本——修改返回值不得影响存储记录。
            return copy.deepcopy(record) if record is not None else None


# C5.7：Content-Disposition 按路由注入（对错误响应同样成立）
_CONTENT_DISPOSITION_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/api/v1/records/[^/]+/download$"), "attachment"),
    (re.compile(r"^/api/v1/report/print$"), "inline"),
)

# F-09：统一安全响应头——防点击劫持（frame-ancestors/XFO）、MIME 嗅探、
# Referer 泄漏。CSP 仅声明 frame-ancestors，不限制脚本/样式源，避免破坏
# 页面既有内联脚本；各头若已由内层显式设置则不覆盖。
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
)


def _with_security_headers(
    headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    present = {key.lower() for key, _ in headers}
    return headers + [
        (name, value) for name, value in _SECURITY_HEADERS if name not in present
    ]


class _CountingReceive:
    """安全批次B（M4）：按实际接收字节累计限流。

    C2.9 原实现只信任 Content-Length 头；无 CL 的 chunked 请求可在拒绝前
    无限缓冲。本包装在 ASGI receive 层累计 http.request 字节，超限即中止
    读取并抛 E_INPUT_OVERSIZE（413），不依赖请求头声明。
    """

    def __init__(self, receive: Any, max_body_bytes: int) -> None:
        self._receive = receive
        self._max_body_bytes = max_body_bytes
        self._seen = 0

    async def __call__(self) -> dict:
        message = await self._receive()
        if message.get("type") == "http.request":
            self._seen += len(message.get("body") or b"")
            if self._seen > self._max_body_bytes:
                raise AppError(
                    "E_INPUT_OVERSIZE",
                    "请求体超过 1MB 上限（C2.9，按实际接收字节累计）",
                )
        return message


class SecurityMiddleware:
    """外层 ASGI 安全中间件：Host → Origin → CSRF →（prepare reservation）→
    载荷上限 → 响应头注入（C5；Annex 005 §12.1/§12.2）。

    prepare 唯一 reservation 入口：认证与 CSRF 之后、任何 body 读取之前完成
    If-Match CAS→原子 rev++/失效→声明大小上限；route 不得再次递增 revision。
    """

    def __init__(
        self,
        app: Any,
        registry: SessionRegistry,
        *,
        default_port: int = DEFAULT_PORT,
        max_body_bytes: int = MAX_BODY_BYTES,
        consent_store: Any | None = None,
    ) -> None:
        self.app = app
        self.registry = registry
        self.default_port = default_port
        self.max_body_bytes = max_body_bytes
        self.consent_store = consent_store

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host_header = headers.get("host", "")
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        is_api = path.startswith("/api/")
        is_prepare = method == "POST" and path == _PREPARE_PATH

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
            if is_prepare:
                # §12.1：If-Match CAS（缺失/非法/陈旧 → 409 不读 body 不推进）
                expected = _parse_if_match(headers.get("if-match"))
                reservation = None
                if expected is not None:
                    reservation = self.registry.begin_prepare(
                        sid,
                        expected[0],
                        expected[1],
                        consent_store=self.consent_store,
                    )
                if reservation is None or not reservation.get("ok"):
                    snapshot = self.registry.version_snapshot(sid)
                    await self._send_error(
                        send,
                        AppError(
                            "E_CONFIRMATION_STALE",
                            "If-Match 缺失/非法/陈旧；本次编辑未生效，"
                            "请以 X-Edit-Current-* 只读快照显式重同步后重新 prepare",
                        ),
                        is_api,
                        extra_headers={
                            "X-Edit-Current-Epoch": str(snapshot["session_epoch"]),
                            "X-Edit-Current-Revision": str(
                                snapshot["input_revision"]
                            ),
                        },
                    )
                    return
                state["edit_reservation"] = {
                    "session_epoch": reservation["session_epoch"],
                    "input_revision": reservation["input_revision"],
                }
                # §12.1：匹配且声明超限 → 先推进（ACK）再 413，不读 body
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
                        extra_headers=_applied_headers(state["edit_reservation"]),
                    )
                    return
            else:
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
            # C5.3：安全 GET 持续回呈本会话签名 cookie（同值刷新；测试/多标签页
            # 读取 cookie 语义稳定，不新建会话、不改变 sid）
            if set_cookie is None:
                set_cookie = self.registry.cookie_for(sid)

        state["session_epoch"] = self.registry.session_epoch
        state["consent_epoch"] = self.registry.consent_epoch

        async def send_wrapper(message: dict) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            state["response_started"] = True
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
            # §12.2：本操作 ACK（Applied/Operation-*）取自本请求 reservation，
            # 响应结束时不得换成 registry 最新值；409 附 Current 只读快照。
            ack = state.get("edit_ack") or state.get("edit_reservation")
            if ack is not None:
                for name, value in _applied_headers(ack).items():
                    header = name.lower().encode("ascii")
                    if not _has(header):
                        response_headers.append((header, value.encode("ascii")))
            status_code = int(message.get("status") or 0)
            if status_code == 409:
                snapshot = self.registry.version_snapshot(sid)
                if not _has(b"x-edit-current-epoch"):
                    response_headers.append(
                        (
                            b"x-edit-current-epoch",
                            str(snapshot["session_epoch"]).encode("ascii"),
                        )
                    )
                if not _has(b"x-edit-current-revision"):
                    response_headers.append(
                        (
                            b"x-edit-current-revision",
                            str(snapshot["input_revision"]).encode("ascii"),
                        )
                    )
            if not _has(b"x-csrf-token"):
                token = self.registry.csrf_token(sid)
                if token:
                    response_headers.append((b"x-csrf-token", token.encode("ascii")))
            response_headers = _with_security_headers(response_headers)
            await send({**message, "headers": response_headers})

        try:
            await self.app(
                scope, _CountingReceive(receive, self.max_body_bytes), send_wrapper
            )
        except AppError as exc:
            # 安全批次B（M4）：读体阶段抛出的业务错误（如按累计字节 413）——
            # 响应未开始则按统一错误形状回应该错误（prepare 分支保留 ACK 头）；
            # 已开始只能中止。路由层 AppError 已由内层异常处理器消化，不至此。
            if state.get("response_started"):
                raise
            extra = (
                _applied_headers(state["edit_reservation"])
                if state.get("edit_reservation") is not None
                else None
            )
            await self._send_error(send, exc, is_api, extra_headers=extra)
        except Exception:
            # R2：reservation 后意外异常 → 安全 500（保留本操作 ACK＋no-store，
            # 不泄露异常原文）；响应已开始则不再注入（避免损坏响应流）。
            if state.get("response_started"):
                raise
            extra = (
                _applied_headers(state["edit_reservation"])
                if state.get("edit_reservation") is not None
                else None
            )
            await self._send_safe_internal_error(send, is_api, extra_headers=extra)

    # —— 内部工具 ——
    async def _send_safe_internal_error(
        self,
        send: Any,
        is_api: bool,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """安全异常兜底（非业务错误码表成员；不泄露异常原文，§12.1 末行）。"""
        body = json.dumps(
            {
                "error": {
                    "code": "E_INTERNAL",
                    "message_zh": "服务器内部错误；本次编辑已记录（见 ACK 头），请重试。",
                    "field": None,
                    "details": [],
                    "binding": None,
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if is_api:
            response_headers.append((b"cache-control", b"no-store"))
        for name, value in (extra_headers or {}).items():
            response_headers.append(
                (name.lower().encode("ascii"), value.encode("ascii"))
            )
        response_headers = _with_security_headers(response_headers)
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
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

    async def _send_error(
        self,
        send: Any,
        err: AppError,
        is_api: bool,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(err.to_payload(), ensure_ascii=False).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if is_api:
            response_headers.append((b"cache-control", b"no-store"))
        for name, value in (extra_headers or {}).items():
            response_headers.append(
                (name.lower().encode("ascii"), value.encode("ascii"))
            )
        response_headers = _with_security_headers(response_headers)
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
