# -*- coding: utf-8 -*-
"""REQ-11 prepare 唯一 reservation／If-Match CAS／invalidate／ACK／clear（002 P1 Red）。

REQ: REQ-11（S002 §8/§9.4；PRD AC-11）。G0 修正后共 21 个命名节点：
  §9.4 六节点＋006 §5.5 八节点（原 14）＋G0 新增七节点：
  - test_002_prepare_clear_during_body_read_stale_no_prepared（§五.4）
  - test_002_confirm_after_edit_or_clear_rejected_then_recovered（§五.4）
  - test_002_pointer_change_between_confirm_and_execute_not_consumed（§五.4）
  - test_002_prepare_unexpected_error_keeps_ack_and_no_store（§五.7/R2）
  - test_002_clear_session_deletes_consent_same_operation（§五.7/R2/Bu2）
  - test_002_invalidated_consent_terminal_not_regrantable（§五.7/Bu2）
  - test_002_invalidate_body_closed_keys（§五.6/R3⑧）
  原节点清单（S002 §9.4 REQ-11 confirmations 6＋006 §5.5 协议/消费/清空 8）：
  - test_002_prepare_if_match_required_and_rotates（§9.4）
  - test_002_prepare_invalid_body_still_invalidates_no_capability（§9.4）
  - test_002_confirm_atomic_revision_binding（§9.4）
  - test_002_wrong_target_409_not_consumed（§9.4）
  - test_002_invalidate_cas_409_current（§9.4）
  - test_002_invalidate_revokes_pending_keeps_records（§9.4）
  - test_002_prepare_stale_or_missing_if_match_oversize_409_no_advance（006 §5.5）
  - test_002_prepare_matching_oversize_advances_and_acks_413（006 §5.5）
  - test_002_prepare_security_403_no_body_read_no_revision（006 §5.5）
  - test_002_prepare_body_read_after_auth_and_reservation_only（006 §5.5）
  - test_002_prepare_stream_oversize_bounded_and_invalidated（006 §5.5）
  - test_002_operation_ack_not_replaced_by_other_editor_revision（006 §5.5）
  - test_002_edit_during_prepare_uses_acked_revision（006 §5.5）
  - test_002_clear_session_epoch_resets_revision_and_drops_late_replies（006 §5.5）

规格锚点:
  - S002 §4.2（prepare 唯一 reservation 入口＝SecurityMiddleware begin_prepare
    一次；固定顺序 Host→Origin→session/CSRF→If-Match CAS→原子 rev++/失效→
    大小上限→有界读取→JSON/业务校验→保存前再 CAS；403 不读 body/不推进/
    不带版本头；缺失/陈旧 If-Match 409 不推进；匹配且声明超限**先推进后
    413**；运行时头 X-Edit-Applied/X-Edit-Operation-Epoch/X-Edit-Operation-
    Revision（仅 Applied=1，不换成最新值）/X-Edit-Current-Epoch/
    X-Edit-Current-Revision（409 只读冲突信息）；invalidate 端点 CAS；
    clear＝epoch+1/rev=0 的 epoch 边界）＋§4.3（409 码表）。
  - Annex 005 §12.1（reservation 固定顺序与失败表：认证与 CAS 前不调用
    ASGI receive、不解析 body；422/413 也确认本次失效已发生）、§12.2
    （Operation ACK≠当前版本；Current 头只读）、§12.3（原子消费/完成；
    invalidate 删除旧 prepared、失效未消费 confirm、records 保留）、
    §13（409 形状）。
  - 006 §2（413/403 必须断言 ASGI receive 调用次数与 Operation ACK，
    不只断言 HTTP 码；确定性屏障，不靠 sleep）。
  - Annex 005 §5.3（v2 薪俸最小可省信封＝合法载荷）。

期望值来源（禁从生产代码反推）:
  - 协议顺序/状态码/ACK 头名与语义：S002 §4.2＋Annex 005 §12–§13 冻结表。
  - If-Match 形如 "edit-<epoch>-<revision>"（S002 §4.1 版本表）。
  - v2 载荷：Annex 005 §5.3 逐字转写。其余全部为结构/状态断言，无金额期望。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - prepare 缺/陈旧 If-Match → 409 E_CONFIRMATION_STALE＋X-Edit-Current-*
    （当前只读快照），不读 body（ASGI receive 计数 0）、不推进 revision；
  - 匹配＋声明 >1MB → 先推进（X-Edit-Applied:1＋Operation-Revision=旧+1）
    再 413 E_INPUT_OVERSIZE，不读 body；
  - 匹配＋非法 body/needs_input → 已推进（Applied:1）＋422、无 prepared_id；
  - 分块累计超限 → 已推进＋413（有界读取：body 有被读，receive 计数 ≥1）；
  - 403（CSRF 失败）即使声明超大也不读 body、不带任何 X-Edit-* 头；
  - Operation ACK 反映**本请求** reservation 结果，响应结束不换成 registry
    最新值（确定性 ASGI 屏障：另一 editor 在 body 读取中段 invalidate）；
    reservation 后另一编辑再保存 prepared → 409 stale、不落 prepared，
    客户端以 Current 头显式重同步后可用；
  - DELETE /api/v1/session → session_epoch+1、input_revision=0（epoch 边界
    非 reservation），ACK Applied:1/Operation-Epoch=新/Operation-Revision=0；
    旧 If-Match/旧确认随之失效；
  - POST /api/v1/calc/invalidate：CAS 失败 409 附当前值；成功 200
    {status:"invalidated", session_epoch, input_revision}（rev++），旧
    prepared/未消费 confirm 失效、records 保留；
  - confirm→execute 原子核对 revision/binding；错目标 409 不消费；单次消费
    409 仅附已保存 record 链接。
  - 本文件对 app.* 延迟导入；Red 失败原因＝缺失 v2 协议行为（旧校验拒绝
    2.0.0、缺 CAS/ACK/原子消费），非收集期失败。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest

_BASE_ORIGIN = "http://127.0.0.1:8000"
_PREPARE = "/api/v1/calc/prepare"
_CONFIRM = "/api/v1/calc/confirm"
_INVALIDATE = "/api/v1/calc/invalidate"
_SALARIES_EXECUTE = "/api/v1/calc/salaries-tax"
_PROFITS_EXECUTE = "/api/v1/calc/profits-tax"
_OVERSIZE = b'{"tax_type":"salaries_tax","schema_version":"2.0.0","input":{"pad":"' + (
    b"x" * (1024 * 1024 + 2048)
) + b'"}}'


# ---------------------------------------------------------------------------
# 固定 v2 测试 seed（Annex 005 §15.1/§15.2 全表；P1 注入件；同源转写）
# ---------------------------------------------------------------------------


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _part5_profile() -> dict:
    return {
        "profile_id": "part5_shared_v2",
        "profile_version": 1,
        "engines": ["personal_assessment", "salaries_tax"],
        "input_schema_version": "2.0.0",
        "contracts": {
            "facts": "subject_relation_year_v2",
            "temporal": "coverage_three_value_clause_quantifiers_v2_salary_window",
            "entitlement": "independent_of_claim_intent_v2_ownership_twopass",
            "ledger": "per_scenario_typed_mutex_enbloc_v2_ownership",
            "candidates": "stage1_explicit_actors_units_effective_v2",
            "future": "next_year_reparse_no_fallback_v1",
            "assumptions": ["same_income", "same_mpf"],
            "selection": "unknown_competitor_blocks_best_v1",
            "money": "annex_c_c2_exact_v1",
            "trace": "stage1_tagged_steps_v2_candidate_checks",
        },
        "allowance_groups": {
            "child": ["base", "birth_year", "following_year"],
            "child_cap_mode": "independent_per_group",
            "grandparent_amount_source": "parent",
            "following_year_applicable": ["2026_27"],
        },
        "rounding": {
            "salary": "salary_floor_min_v1",
            "pa_individual": "pa_unit_floor_min_v1",
            "pa_joint": "pa_unit_floor_min_v1",
            "pa_no_pa_salary": "pa_stc_comp_tp_v1",
            "pa_no_pa_property": "property_per_assessment_floor_v1",
            "pa_no_pa_business": "approved_unincorporated_floor_branch_v1",
        },
        "components": {
            "salary_partial_balance": "null",
            "pa_status_ceiling": "partial",
            "pa_balance": "null",
            "payment_comparison": "out_of_scope",
            "rebate_applicable_years": ["2024_25", "2025_26"],
        },
    }


def _node(amount: str, cap: str | None = None, status: str = "applicable") -> dict:
    return {"amount": amount, "cap": cap, "status": status}


def _progressive_bands() -> list:
    """附表 2（T1，台账 1.17）：累进带；末段无 width＝余额段（bp＝万分比）。"""
    return [
        {"width": "50000", "rate_bp": 200},
        {"width": "50000", "rate_bp": 600},
        {"width": "50000", "rate_bp": 1000},
        {"width": "50000", "rate_bp": 1400},
        {"rate_bp": 1700},
    ]


def _standard_rate_bands() -> list:
    """附表 1（T1，台账 1.17）：两级标准税率（2024/25 起）；末段＝余额段。"""
    return [
        {"width": "5000000", "rate_bp": 1500},
        {"rate_bp": 1600},
    ]


# 定稿 seed canonical hash（六文件同值；冻结于 tests/unit/test_002_store_compat.py
# 的 _FIXED_V2_SEED_HASH）：sha256:a08937e4a8b9923392c02da8c66451e0a526e919046d3315ec3e30361ad2f1b5


def _fixed_v2_seed() -> dict:
    profile = _part5_profile()
    profile["semantic_digest"] = "sha256:" + hashlib.sha256(
        _canonical_bytes(_part5_profile())
    ).hexdigest()
    years_24_25 = {
        "basic": _node("132000"),
        "married": _node("264000"),
        "child_base": _node("130000", "1170000"),
        "child_birth_year_additional": _node("130000", "1170000"),
        "child_following_year_additional": _node("0", "0", "not_applicable_verified"),
        "parent_high": _node("50000"),
        "parent_high_cohabitation": _node("50000"),
        "parent_low": _node("25000"),
        "parent_low_cohabitation": _node("25000"),
        "sibling": _node("37500"),
        "personal_disability": _node("75000"),
        "disabled_dependant": _node("75000"),
        "single_parent": _node("132000"),
        "parent_cash_threshold": _node("12000"),
    }
    return {
        "rules_schema_version": "2.0.0",
        "effective": {
            "allowances": {"from_year": "2024_25", "to_year": "2026_27"},
            "reductions": {"from_year": "2024_25", "to_year": "2025_26"},
        },
        "applicability": {"years": ["2024_25", "2025_26", "2026_27"]},
        "frozen_semantics": {"profiles": [profile]},
        "data": {
            "allowances": {
                "2024_25": years_24_25,
                "2025_26": years_24_25,
                "2026_27": {
                    "basic": _node("145000"),
                    "married": _node("290000"),
                    "child_base": _node("140000", "1260000"),
                    "child_birth_year_additional": _node("140000", "1260000"),
                    "child_following_year_additional": _node("140000", "1260000"),
                    "parent_high": _node("55000"),
                    "parent_high_cohabitation": _node("55000"),
                    "parent_low": _node("27500"),
                    "parent_low_cohabitation": _node("27500"),
                    "sibling": _node("37500"),
                    "personal_disability": _node("75000"),
                    "disabled_dependant": _node("75000"),
                    "single_parent": _node("145000"),
                    "parent_cash_threshold": _node("12000"),
                },
                "mpf_deduction_cap": "18000",
            },
            "reductions": {
                "2024_25": {"percent_bp": 10000, "cap": "1500", "status": "applicable"},
                "2025_26": {"percent_bp": 10000, "cap": "3000", "status": "applicable"},
                "2026_27": {
                    "percent_bp": 0, "cap": "0", "status": "not_applicable_verified"
                },
            },
            "salaries": {
                # 附表 2/1（T1，台账 1.17）：薪俸累进带＋两级标准径
                "progressive_bands": _progressive_bands(),
                "standard_rate_bands": _standard_rate_bands(),
                # §15.2：ERCE cap 仍留 salaries.deductions（附表 3C，T1）
                "deductions": {
                    "elder_residential_care": {
                        "caps": {
                            "2024_25": "100000",
                            "2025_26": "100000",
                            "2026_27": "110000",
                        }
                    }
                },
            },
            "personal_assessment": {
                # 附表 2/1（T1，台账 1.17）：PA 累进带＋标准径（s.43(1A)）
                "progressive_bands": _progressive_bands(),
                "standard_rate_bands": _standard_rate_bands(),
                # 附表 43 利得行（T1；s.100(2)/(3)）
                "profits_reductions": {
                    "2024_25": {"percent_bp": 10000, "cap": "1500"},
                    "2025_26": {"percent_bp": 10000, "cap": "3000"},
                },
                # D6.2a（T1-observed）：no_pa 比较组件非法团两级分支
                "profits_two_tier": {
                    "threshold": "2000000",
                    "low_rate_bp": 750,
                    "high_rate_bp": 1500,
                },
                "property_standard_rate": "15/100",
                "repair_allowance_ratio": "1/5",
            },
            "profits": {
                "two_tier_threshold": "2000000",
                "reductions": {"2025_26": {"cap": "3000"}},
            },
            "property": {
                "years": {
                    "2024_25": {"standard_rate": "15/100", "reduction": "0"},
                    "2025_26": {"standard_rate": "15/100", "reduction": "0"},
                    "2026_27": {"standard_rate": "15/100", "reduction": "0"},
                },
                "repair_allowance_ratio": "1/5",
            },
            "stamps": {
                "property": {"related_conveyance": {"fixed_duty": "100"}},
                "stock": {"other_transfer_fixed": "5", "effective_from": "2023-11-17"},
                "lease": {"rent_ceiling_unit": "100"},
            },
        },
        "evidence_digests": [],
    }


def _v2_salary_body() -> dict:
    """Annex 005 §5.3 最小可省 v2 薪俸信封（逐字转写）。"""
    return {
        "tax_type": "salaries_tax",
        "schema_version": "2.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "subjects": [{"subject_id": "p1"}],
            "years": {
                "2025_26": {
                    "participants": [{"person_id": "p1", "role": "self"}],
                    "subject_facts": {},
                    "household": {
                        "marriage": [
                            {"from": "2025-04-01", "to": "2026-04-01", "value": "unmarried"}
                        ],
                        "living_apart": [],
                        "child_nomination": {"person_id": None, "status": "none"},
                    },
                    "relations": [],
                    "care_payments": [],
                    "claims": [],
                    "financials": {
                        "p1": {
                            "employment_income_nai": "240000",
                            "mpf_mandatory_contributions": "9000",
                            "has_assessable_salary_income": True,
                            "source_classification": {"salary": "confirmed_in_scope"},
                            "properties": [],
                            "businesses": [],
                            "deduction_items": [],
                        }
                    },
                    "assessment": {
                        "basis": "actual",
                        "candidates": ["salary_separate"],
                        "selection": "salary_separate",
                    },
                    "manual_issues": [],
                    "provisional_paid": "0",
                }
            },
            "hk_presence_days": [],
            "assumptions": [],
        },
    }


# ---------------------------------------------------------------------------
# 客户端 fixture（会话/CSRF＋固定 v2 种子；本文件自备）
# ---------------------------------------------------------------------------


class _ReceiveCountingApp:
    """ASGI 包装：统计应用侧 receive() 调用次数（006 §2：断言 body 读取行为）。"""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.receive_calls = 0

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        async def counting_receive() -> dict:
            self.receive_calls += 1
            return await receive()

        await self.inner(scope, counting_receive, send)


class _Loopback:
    def __init__(self, app: Any, cookie_value: str | None = None) -> None:
        """cookie_value 提供时沿用既有会话（同 sid 第二标签页语义）。"""
        self._app = app
        self._client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=_BASE_ORIGIN
        )
        self._csrf: str | None = None
        self.cookie_value = cookie_value
        if cookie_value:
            from app.config import SESSION_COOKIE_NAME

            self._client.cookies.set(SESSION_COOKIE_NAME, cookie_value)

    def _token(self) -> str:
        if self._csrf is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf = token
            from app.config import SESSION_COOKIE_NAME

            self.cookie_value = self.cookie_value or r.cookies.get(SESSION_COOKIE_NAME)
        return self._csrf

    def get(self, path: str):
        return self._client.get(path)

    def post(
        self,
        path: str,
        *,
        json_body: dict | None = None,
        content: bytes | Any | None = None,
        headers: dict | None = None,
        csrf: bool = True,
    ):
        h = dict(headers or {})
        h["Origin"] = _BASE_ORIGIN
        if csrf:
            h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json_body, content=content, headers=h)

    def delete(self, path: str, *, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = _BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.delete(path, headers=h)

    def clone(self) -> "_Loopback":
        """同一会话的第二客户端（跨标签页/另一 editor 语义；P1 r2 屏障场景）。"""
        self._token()
        return _Loopback(self._app, cookie_value=self.cookie_value)

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# P1 r2 PQA-01 函数级安全生命周期（文件本地 scope；自有 undo 栈）：
# - collection 只定义：不 setattr 真实类、不创建 app、无共享类计数器；
# - autouse safety fixture（依赖 tmp_path/monkeypatch）在 main 导入前安装真实
#   边界守卫＋真实 RuleStore 观察器；测试侧 client/屏障/线程结束后：先
#   monkeypatch.undo() 撤销测试自身注入，再独立收集 counter/DB/安装检查，
#   随后无条件复原自有属性/env，最后 identity 断言；任一检查失败不阻断其它；
# - app fixture 显式依赖本 fixture；inline 构造经模块级当前 scope 覆盖。
# ---------------------------------------------------------------------------


_MISSING = object()
_RED_SCOPE: Any = None
_RED_SELFTEST_DONE = False


class _RedSafetyScope:
    """单测试函数的真实边界守卫与真实 store 观察（计数/观察局部，不挂生产类）。"""

    def __init__(self, tmp_path: Any) -> None:
        self.tmp_path = Path(tmp_path).resolve()
        self.batch_root = self.tmp_path.parent
        self.counts = {
            "scheduler_class": 0,
            "model_send_class": 0,
            "document_fetch_class": 0,
            "start_scheduler_threads": 0,
            "http_transport": 0,
            "async_http_transport": 0,
        }
        self.store_events: list = []
        self.store_instances: dict = {}
        self.registered_stores: dict = {}
        self.store_violations = 0
        self.apps: list = []
        self._undo: list = []
        self._env: list = []
        self._undo_snapshot: list = []
        self._env_snapshot: list = []
        self._post_main_done = False

    # —— 自有恢复栈（独立于 pytest monkeypatch）——
    def _set(self, owner: Any, name: str, value: Any) -> None:
        original = getattr(owner, name, _MISSING)
        present = original is not _MISSING
        self._undo.append((owner, name, present, original))
        setattr(owner, name, value)

    def _setenv(self, name: str, value: str) -> None:
        self._env.append((name, os.environ.get(name, _MISSING)))
        os.environ[name] = value

    def _restore_own(self) -> None:
        # D4-PQA-01：快照追加式累计（非覆盖）——install() 内部失败先自带复原并
        # 清栈（快照＝已安装集合）；fixture finally 再次调用本方法时栈已空，覆盖
        # 会让 snapshot 置空、final identity 循环退化为空检查；追加式保证该路径
        # 快照/恢复语义不可退化（后一次调用只补充新登记，绝不抹除既有记录）。
        self._undo_snapshot.extend(self._undo)
        self._env_snapshot.extend(self._env)
        errors: list = []
        for owner, name, present, original in reversed(self._undo):
            try:
                if present:
                    setattr(owner, name, original)
                else:
                    try:
                        delattr(owner, name)
                    except (AttributeError, KeyError):
                        pass
            except Exception as exc:  # noqa: BLE001 - 收集后统一报告
                errors.append(f"{name}: {exc!r}")
        self._undo.clear()
        for name, original in reversed(self._env):
            if original is _MISSING:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
        self._env.clear()
        if errors:
            raise AssertionError("安全层复原异常：" + " | ".join(errors))

    # —— 真实边界守卫（拒绝＋计数；计数局部）——
    def _reject(self, counter: str):
        def _guard(*_args: Any, **_kwargs: Any):
            self.counts[counter] += 1
            raise AssertionError(f"真实网络/调度边界被调用（安全证据）：{counter}")

        _guard._red_safety_guard = True  # type: ignore[attr-defined]
        return _guard

    def _reject_async(self, counter: str):
        async def _guard(*_args: Any, **_kwargs: Any):
            self.counts[counter] += 1
            raise AssertionError(f"真实网络/调度边界被调用（安全证据）：{counter}")

        _guard._red_safety_guard = True  # type: ignore[attr-defined]
        return _guard

    def install(self) -> None:
        """main 导入前：真实边界守卫＋真实 RuleStore 观察器＋默认 TempDB env。"""
        try:
            self._install_boundaries()
            self._install_store_observers()
            self._setenv("HKTAX_DB_PATH", str(self.tmp_path / "rules.db"))
            import sys as _sys

            if _sys.modules.get("app.main") is not None:
                # main 已缓存（同进程前序测试导入）：本函数窗口同样保护
                # 真实文档出站/线程启动并登记隐式 app（不触发新导入）。
                self._post_main_done = True
                self._install_post_main_guards()
        except BaseException:
            self._restore_own()
            raise

    def _install_boundaries(self) -> None:
        import httpx

        from app.ai.adapters import HttpxModelOutbound
        from app.updater.scheduler import Scheduler

        for name in ("startup", "tick", "trigger_manual"):
            self._set(Scheduler, name, self._reject("scheduler_class"))
        self._set(HttpxModelOutbound, "send", self._reject("model_send_class"))
        self._set(httpx.HTTPTransport, "handle_request", self._reject("http_transport"))
        self._set(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            self._reject_async("async_http_transport"),
        )

    def _install_store_observers(self) -> None:
        from app.rules.store import RuleStore

        real_init = RuleStore.__init__
        real_connect = RuleStore._connect

        def _resolve_for_guard(path: Path) -> Path | None:
            try:
                return path.resolve()
            except OSError:
                return None

        def _observed_init(store_self: Any, db_path: Any) -> None:
            requested = Path(str(db_path))
            resolved = _resolve_for_guard(requested)
            allowed = resolved is not None and resolved.is_relative_to(self.tmp_path)
            self.store_events.append(
                {
                    "kind": "init-request",
                    "path": str(requested),
                    "resolved": None if resolved is None else str(resolved),
                    "allowed": allowed,
                }
            )
            if not allowed:
                self.store_violations += 1
                raise AssertionError(
                    "RuleStore 请求路径经解析规范化后不在当前函数 Temp 目录"
                    f"（先于任何真实 I/O 拒绝）：{requested!s}"
                )
            identity = id(store_self)
            # 构造中的 _init_schema 会在本包装返回前调用 _connect：先按规范化
            # 路径登记身份，连接观察据此绑定本函数新构造 store；init 失败撤销。
            self.store_instances[identity] = str(resolved)
            completed = False
            try:
                real_init(store_self, db_path)
                completed = True
            finally:
                resolved_done = str(Path(store_self._path).resolve())
                self.store_events.append(
                    {
                        "kind": "init-done",
                        "completed": completed,
                        "identity": identity,
                        "resolved": resolved_done,
                    }
                )
                if completed:
                    self.store_instances[identity] = resolved_done
                else:
                    self.store_instances.pop(identity, None)

        def _observed_connect(store_self: Any) -> Any:
            identity = id(store_self)
            path = Path(str(store_self._path))
            resolved = _resolve_for_guard(path)
            if identity in self.store_instances:
                scope = "new"
                registered = self.store_instances[identity]
            elif identity in self.registered_stores:
                scope = "cached-implicit"
                registered = self.registered_stores[identity]
            else:
                scope = "unknown"
                registered = None
            allowed = (
                resolved is not None
                and registered is not None
                and str(resolved) == registered
            )
            self.store_events.append(
                {
                    "kind": "connect",
                    "path": str(path),
                    "resolved": None if resolved is None else str(resolved),
                    "allowed": allowed,
                    "identity": identity,
                    "scope": scope,
                    "registered": registered,
                }
            )
            if not allowed:
                self.store_violations += 1
                raise AssertionError(
                    "RuleStore 连接未绑定本函数新构造 store 或已核 cached "
                    f"implicit（先于真实连接拒绝）：identity={identity} "
                    f"path={path!s} scope={scope}"
                )
            return real_connect(store_self)

        self._set(RuleStore, "__init__", _observed_init)
        self._set(RuleStore, "_connect", _observed_connect)

    # —— main 导入后：文档出站/线程启动守卫＋隐式 app 硬化 ——
    def _install_post_main_guards(self) -> None:
        import app.main as main_module

        document_cls = getattr(main_module, "_HttpxDocumentOutbound", None)
        if document_cls is not None and not getattr(
            getattr(document_cls, "fetch", None), "_red_safety_guard", False
        ):
            self._set(document_cls, "fetch", self._reject("document_fetch_class"))
        starter = getattr(main_module, "_start_scheduler_threads", None)
        if starter is not None and not getattr(starter, "_red_safety_guard", False):
            self._set(
                main_module,
                "_start_scheduler_threads",
                self._reject("start_scheduler_threads"),
            )
        implicit = getattr(main_module, "app", None)
        if implicit is not None:
            self.harden_app(implicit, source="implicit")

    def harden_app(self, app: Any, source: str | None = None) -> Any:
        if getattr(app.state, "_red_safety", None) is not None:
            return app
        if not self._post_main_done:
            self._post_main_done = True
            self._install_post_main_guards()
        import app.main as main_module

        if source is None:
            source = (
                "implicit" if app is getattr(main_module, "app", None) else "explicit"
            )
        store = getattr(app.state, "store", None)
        if store is None:
            self.store_violations += 1
            raise AssertionError("硬化 app 缺 app.state.store（无法核验真实库身份）")
        identity = id(store)
        resolved = str(Path(store._path).resolve())
        # 新 store 的路径规范化前置防护在 _observed_init（先于任何真实 I/O）；
        # 此处按实例身份复核：新构造者须与本函数 init 记录完全一致；既存
        # cached implicit 单独登记（仅限本批次隔离 Temp，不要求当前函数
        # tmp_path），未知/非临时立即拒绝，绝不改 path/reseed。
        observed_new = identity in self.store_instances
        if observed_new:
            allowed = resolved == self.store_instances[identity]
            registered_cached = False
        elif source == "implicit" and Path(resolved).is_relative_to(self.batch_root):
            allowed = True
            registered_cached = True
        else:
            allowed = False
            registered_cached = False
        self.store_events.append(
            {
                "kind": "harden",
                "source": source,
                "identity": identity,
                "resolved": resolved,
                "allowed": allowed,
                "observed_new_init": observed_new,
                "registered_cached": registered_cached,
            }
        )
        if not allowed:
            self.store_violations += 1
            raise AssertionError(
                f"{source} app 真实 store 未经本函数新构造/已核 cached "
                f"implicit（不可核验；真实 I/O 前置防护见 init 观察）：{resolved}"
            )
        if registered_cached:
            self.registered_stores[identity] = resolved
        safety = {
            "scheduler": _RedSafetyScheduler(app.state.update_scheduler),
            "outbound": _RedSafetyOutbound(app.state.model_outbound),
        }
        self._set(app.state, "update_scheduler", safety["scheduler"])
        self._set(app.state, "model_outbound", safety["outbound"])
        self._set(app.state, "_red_safety", safety)
        self.apps.append({"app": app, "source": source, "safety": safety})
        return app

    # —— 独立检查（互不阻断）——
    @staticmethod
    def _collect(failures: list, name: str, check) -> None:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - 收集后统一报告
            failures.append(f"{name}: {exc!r}")

    def _check_boundary_counters(self) -> None:
        nonzero = {key: value for key, value in self.counts.items() if value != 0}
        assert not nonzero, f"真实边界计数须为 0，实际 {nonzero!r}"

    def _check_app_wrappers(self) -> None:
        for entry in self.apps:
            app = entry["app"]
            safety = entry["safety"]
            assert app.state.update_scheduler is safety["scheduler"], (
                "app.state.update_scheduler 已被替换（自定义 guard 不得拆除安全层）"
            )
            assert app.state.model_outbound is safety["outbound"], (
                "app.state.model_outbound 已被替换（真实出站安全层不得被拆除）"
            )
            scheduler = safety["scheduler"]
            assert (
                scheduler.startup_calls == 0
                and scheduler.tick_calls == 0
                and scheduler.trigger_calls == 0
            ), "app 包装器 scheduler 计数须为 0"
            assert safety["outbound"].calls == 0, "app 包装器真实出站计数须为 0"

    def _check_store_observations(self) -> None:
        assert self.store_violations == 0, f"store 观察违规 {self.store_violations} 次"
        if not self.store_events and not self.apps:
            return
        assert self.store_events, "有 app/无 store 观察事件（不可核验实际库）"
        for event in self.store_events:
            kind = event["kind"]
            if kind == "init-request":
                assert event["allowed"], event
                assert event["resolved"] is not None, event
                assert Path(event["resolved"]).is_relative_to(self.tmp_path), event
            elif kind == "init-done":
                assert event["completed"], event
                identity = event["identity"]
                assert identity in self.store_instances, event
                assert self.store_instances[identity] == event["resolved"], event
                assert Path(event["resolved"]).is_relative_to(self.tmp_path), event
            elif kind == "connect":
                assert event["allowed"], event
                identity = event["identity"]
                if event["scope"] == "new":
                    assert identity in self.store_instances, event
                    expected = self.store_instances[identity]
                    root = self.tmp_path
                else:
                    assert event["scope"] == "cached-implicit", event
                    assert identity in self.registered_stores, event
                    expected = self.registered_stores[identity]
                    root = self.batch_root
                assert event["resolved"] == expected, event
                assert Path(expected).is_relative_to(root), event
            elif kind == "harden":
                assert event["allowed"], event
                identity = event["identity"]
                if event["observed_new_init"]:
                    assert identity in self.store_instances, event
                    assert event["resolved"] == self.store_instances[identity], event
                else:
                    assert event["registered_cached"], event
                    assert identity in self.registered_stores, event
                    assert event["resolved"] == self.registered_stores[identity], event
                    assert Path(event["resolved"]).is_relative_to(
                        self.batch_root
                    ), event
        for entry in self.apps:
            if entry["source"] == "explicit":
                store = entry["app"].state.store
                assert id(store) in self.store_instances, (
                    "显式 app 真实 store 未经本函数构造观察"
                )

    def _check_install_state(self) -> None:
        import sys

        import httpx

        from app.ai.adapters import HttpxModelOutbound
        from app.updater.scheduler import Scheduler

        assert getattr(Scheduler.startup, "_red_safety_guard", False), (
            "Scheduler.startup 守卫未安装（缺 safety 必须 fail）"
        )
        assert getattr(Scheduler.tick, "_red_safety_guard", False)
        assert getattr(Scheduler.trigger_manual, "_red_safety_guard", False)
        assert getattr(HttpxModelOutbound.send, "_red_safety_guard", False), (
            "HttpxModelOutbound.send 守卫未安装"
        )
        assert getattr(httpx.HTTPTransport.handle_request, "_red_safety_guard", False)
        assert getattr(
            httpx.AsyncHTTPTransport.handle_async_request, "_red_safety_guard", False
        )
        main_module = sys.modules.get("app.main")
        if main_module is not None:
            document_cls = getattr(main_module, "_HttpxDocumentOutbound", None)
            if document_cls is not None:
                assert getattr(document_cls.fetch, "_red_safety_guard", False), (
                    "_HttpxDocumentOutbound.fetch 守卫未安装"
                )
            starter = getattr(main_module, "_start_scheduler_threads", None)
            if starter is not None:
                assert getattr(starter, "_red_safety_guard", False), (
                    "_start_scheduler_threads 守卫未安装"
                )
        for entry in self.apps:
            assert getattr(entry["app"].state, "_red_safety", None) is not None, (
                "app 安全层缺失（缺 safety 必须 fail）"
            )

    def _check_final_identity(self) -> None:
        for owner, name, present, original in self._undo_snapshot:
            if present:
                assert getattr(owner, name) is original, (
                    f"复原 identity 不符：{name}（{type(owner).__name__}）"
                )
            else:
                assert getattr(owner, name, _MISSING) is _MISSING, (
                    f"新增属性未删除：{name}（{type(owner).__name__}）"
                )
        for name, original in self._env_snapshot:
            current = os.environ.get(name, _MISSING)
            if original is _MISSING:
                assert current is _MISSING, f"env 未复原：{name}={current!r}"
            else:
                assert current == original, f"env 未复原：{name}={current!r}"
        for entry in self.apps:
            assert getattr(entry["app"].state, "_red_safety", None) is None, (
                "app 安全标记未清除"
            )


class _RedSafetyScheduler:
    """app.state 包装器：startup/tick/trigger_manual 拒绝＋计数；availability
    委托真实本地判定（受控合法，不以 reject-all 遮蔽目标行为）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.startup_calls = 0
        self.tick_calls = 0
        self.trigger_calls = 0

    def availability(self, period: str) -> dict:
        return self._inner.availability(period)

    def startup(self) -> Any:
        self.startup_calls += 1
        raise AssertionError("scheduler.startup 被调用（安全证据：禁止启动检查）")

    def tick(self) -> Any:
        self.tick_calls += 1
        raise AssertionError("scheduler.tick 被调用（安全证据：禁止周期检查）")

    def trigger_manual(self) -> Any:
        self.trigger_calls += 1
        raise AssertionError("scheduler.trigger_manual 被调用（安全证据：禁止出站管线）")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RedSafetyOutbound:
    """app.state 包装器：真实模型出站入口拒绝＋计数（E21 假件计数独立）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def send(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("真实模型出站被调用（安全证据：禁止网络）")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _harden_app(app: Any) -> Any:
    """经当前文件本地安全 scope 硬化 app（隐式/显式；scope 未激活即 fail）。"""
    scope = _RED_SCOPE
    assert scope is not None, (
        "PQA-01：安全 scope 未激活（app 构造必须发生在 safety fixture 生命周期内）"
    )
    return scope.harden_app(app)


def _run_guard_selftest() -> None:
    """独立 guard 自检（一次性 scope）：被保护出口拒绝＋计数增长后完整复原；
    与真实路径零计数分开观察。进程内每文件只跑一次。"""
    import asyncio

    import httpx

    from app.ai.adapters import HttpxModelOutbound
    from app.updater.scheduler import Scheduler

    selftest = _RedSafetyScope.__new__(_RedSafetyScope)
    selftest.counts = {
        key: 0
        for key in (
            "scheduler_class",
            "model_send_class",
            "document_fetch_class",
            "start_scheduler_threads",
            "http_transport",
            "async_http_transport",
        )
    }
    selftest.store_events = []
    selftest.store_instances = {}
    selftest.registered_stores = {}
    selftest.store_violations = 0
    selftest.apps = []
    selftest._undo = []
    selftest._env = []
    selftest._undo_snapshot = []
    selftest._env_snapshot = []
    selftest._post_main_done = True
    selftest._set(Scheduler, "startup", selftest._reject("scheduler_class"))
    selftest._set(Scheduler, "tick", selftest._reject("scheduler_class"))
    selftest._set(Scheduler, "trigger_manual", selftest._reject("scheduler_class"))
    selftest._set(HttpxModelOutbound, "send", selftest._reject("model_send_class"))
    selftest._set(
        httpx.HTTPTransport, "handle_request", selftest._reject("http_transport")
    )
    selftest._set(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        selftest._reject_async("async_http_transport"),
    )
    try:
        scheduler = object.__new__(Scheduler)
        with pytest.raises(AssertionError):
            Scheduler.startup(scheduler)
        with pytest.raises(AssertionError):
            Scheduler.tick(scheduler)
        with pytest.raises(AssertionError):
            Scheduler.trigger_manual(scheduler)
        with pytest.raises(AssertionError):
            HttpxModelOutbound.send(object.__new__(HttpxModelOutbound), "e", "k", b"x")
        with pytest.raises(AssertionError):
            httpx.HTTPTransport.handle_request(object.__new__(httpx.HTTPTransport), None)
        with pytest.raises(AssertionError):
            asyncio.run(
                httpx.AsyncHTTPTransport.handle_async_request(
                    object.__new__(httpx.AsyncHTTPTransport), None
                )
            )
        assert selftest.counts == {
            "scheduler_class": 3,
            "model_send_class": 1,
            "document_fetch_class": 0,
            "start_scheduler_threads": 0,
            "http_transport": 1,
            "async_http_transport": 1,
        }, f"guard 自检计数不符：{selftest.counts!r}"
    finally:
        selftest._restore_own()


@pytest.fixture(autouse=True)
def _red_safety_scope(tmp_path, monkeypatch):
    """文件本地 PQA-01 安全生命周期：安装→测试→undo 测试注入→独立检查→复原。

    正常/目标失败/setup 失败路径均执行 counter 与 DB 两类独立检查（互不阻断）；
    setup 失败时不 yield 测试；最后无条件复原原方法/env 并复核 identity。"""
    global _RED_SCOPE, _RED_SELFTEST_DONE

    if not _RED_SELFTEST_DONE:
        _RED_SELFTEST_DONE = True
        _run_guard_selftest()
    scope = _RedSafetyScope(tmp_path)
    _RED_SCOPE = scope
    setup_error: BaseException | None = None
    try:
        try:
            scope.install()
        except BaseException as exc:  # noqa: BLE001 - setup 失败仍须独立检查
            setup_error = exc
        if setup_error is None:
            yield scope
    finally:
        _RED_SCOPE = None
        failures: list = []
        try:
            monkeypatch.undo()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"monkeypatch.undo: {exc!r}")
        scope._collect(failures, "boundary counters", scope._check_boundary_counters)
        scope._collect(failures, "app wrappers", scope._check_app_wrappers)
        scope._collect(failures, "store observations", scope._check_store_observations)
        scope._collect(failures, "guard install", scope._check_install_state)
        try:
            scope._restore_own()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"restore: {exc!r}")
        scope._collect(failures, "final identity", scope._check_final_identity)
        if setup_error is not None:
            failures.append(f"install: {setup_error!r}")
        if failures:
            raise AssertionError("PQA-01 安全作用域失败：" + " | ".join(failures))



@pytest.fixture
def v2(tmp_path, monkeypatch, _red_safety_scope):
    """固定 v2 测试 seed 注入后的进程内 app＋回环客户端（P1 依赖契约）。

    显式依赖文件本地 safety scope（PQA-01）：安装先于 main 导入，检查/复原在
    scope teardown 独立执行（client.close 先于检查）。"""
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    client = _Loopback(app)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def v2_counting(tmp_path, monkeypatch, _red_safety_scope):
    """同 v2，但经 receive 计数包装（403/413 body 读取断言用）。"""
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    hardened = _harden_app(create_app())
    _harden_app(implicit_main_app)
    wrapped = _ReceiveCountingApp(hardened)
    client = _Loopback(wrapped)
    try:
        yield client, wrapped
    finally:
        client.close()


@pytest.fixture
def v2app(tmp_path, monkeypatch, _red_safety_scope):
    """P1 r2（protocol 咨询 §5 A/E 组）：固定 v2 seed 注入后的 (app, client)。

    直驱 registry/consent_store 接缝需要 app.state 句柄；DB 隔离/延迟导入
    纪律与 v2 相同（导入 app.main 之前已设唯一临时 HKTAX_DB_PATH）。"""
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    client = _Loopback(app)
    try:
        yield app, client
    finally:
        client.close()


def _field(obj: Any, name: str, *default: Any) -> Any:
    """表示无关字段读取（plan §3：ConfirmationSnapshot 等类型表示由实施者
    选择，测试不绑定 dict/dataclass——Mapping 与属性两种载体均可读）。"""
    if isinstance(obj, Mapping):
        if name in obj:
            return obj[name]
    else:
        try:
            return getattr(obj, name)
        except AttributeError:
            pass
    if default:
        return default[0]
    raise AssertionError(
        f"对象缺少冻结字段 {name!r}（plan §3/005 §12.3）：{obj!r}"
    )


_MISSING = object()


def _red_identity_sets(registry: Any, sid: str) -> tuple:
    """registry 锁内权威身份集合（PQA-03）：先确认观察面真实存在可读
    （sessions 容器、该 sid 真实存在、executions/records 键集合在场），再取
    frozenset；真实空 Mapping 合法；不可观察 → held（不填零）；本应存续 sid
    消失是状态前置失败，不伪装接口缺失。不新增 getter/不冻 entry repr。"""
    with registry._lock:
        sessions = getattr(registry, "_sessions", _MISSING)
        if not isinstance(sessions, MutableMapping):
            raise AssertionError(
                "INTERFACE_HELD：registry._sessions 权威容器不可观察（PQA-03）"
            )
        if sid not in sessions:
            raise AssertionError(
                f"状态前置失败：本应存续的 sid 不在 registry._sessions（{sid!r}）"
            )
        session = sessions[sid]
        if not isinstance(session, Mapping):
            raise AssertionError("INTERFACE_HELD：session 表示不可读（PQA-03）")
        executions = session.get("executions", _MISSING)
        records = session.get("records", _MISSING)
        if executions is _MISSING or records is _MISSING:
            raise AssertionError(
                "INTERFACE_HELD：session.executions/records 键集合不可观察（PQA-03）"
            )
        if not isinstance(executions, Mapping) or not isinstance(records, Mapping):
            raise AssertionError(
                "INTERFACE_HELD：executions/records 非键集合表示（PQA-03）"
            )
        return frozenset(executions.keys()), frozenset(records.keys())


def _runtime_versions(client: _Loopback) -> tuple[int, int]:
    r = client.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, (
        "S002 §4.1/Annex 005 §12：meta/version 须附运行时 session_epoch/"
        f"input_revision，实际：{payload!r}"
    )
    return int(payload["session_epoch"]), int(payload["input_revision"])


def _if_match(epoch: int, revision: int) -> dict[str, str]:
    """S002 §4.1 版本表 wire 字面量：`If-Match:"edit-<epoch>-<revision>"`（带引号；
    RFC 9110 If-Match entity-tag 语法亦要求 DQUOTE）。单一语法，不做双兼容。"""
    return {"If-Match": f'"edit-{epoch}-{revision}"'}


def _fresh_if_match(client: _Loopback) -> dict[str, str]:
    epoch, revision = _runtime_versions(client)
    return _if_match(epoch, revision)


def _error_code(payload: dict) -> str | None:
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状 §4.3，实际：{payload!r}"
    return err.get("code")


def _prepare_ok(client: _Loopback) -> dict:
    r = client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_fresh_if_match(client)
    )
    assert r.status_code == 201, f"prepare 须 201，实际 {r.status_code}：{r.text[:300]!r}"
    return r.json()


def _confirm_ok(client: _Loopback, prepared: dict) -> str:
    r = client.post(
        _CONFIRM,
        json_body={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r.status_code == 200, f"confirm 须 200，实际 {r.status_code}：{r.text[:300]!r}"
    return r.json()["confirmation_id"]


def _invalidate(client: _Loopback, epoch: int, revision: int):
    return client.post(
        _INVALIDATE,
        json_body={
            "expected_session_epoch": epoch,
            "expected_input_revision": revision,
        },
    )


# ---------------------------------------------------------------------------
# 确定性 ASGI 屏障驱动（006 §2：事件屏障，不靠 sleep；不经 httpx 缓冲）
# ---------------------------------------------------------------------------


async def _drive_asgi(
    app: Any,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body_items: list,
):
    """直接驱动 ASGI app；body_items 为 bytes 或返回 bytes 的协程函数。

    协程函数项＝确定性屏障：应用请求下一段 body 时先等待事件再继续。
    返回 (status, response_headers_dict, body_bytes)。
    """
    raw_headers = [
        (key.lower().encode("utf-8"), value.encode("utf-8"))
        for key, value in headers.items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 45678),
        "server": ("127.0.0.1", 8000),
    }
    items = list(body_items)
    state = {"done": False}
    response = {"status": None, "headers": [], "body": b""}

    async def receive() -> dict:
        if state["done"]:
            return {"type": "http.disconnect"}
        if not items:
            state["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        item = items.pop(0)
        if callable(item):
            chunk = await item()
        else:
            chunk = item
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": True,
        }

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = [
                (k.decode("latin-1"), v.decode("latin-1"))
                for k, v in message.get("headers", [])
            ]
        elif message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return (
        response["status"],
        {name.lower(): value for name, value in response["headers"]},
        response["body"],
    )


async def _mid_body_edit_scenario(app: Any, mutation: str = "invalidate"):
    """prepare body 读取中段另一 editor 推进的确定性屏障场景（同 sid）。

    - 屏障 helper 强断言（复核报告 §五.3）：A 的 reservation（rev0→rev0+1）
      发生在首块 body 读取之前（§12.1 顺序）→ B（同 cookie 第二标签页）
      **只提交一次** rev0+1 的 invalidate → 必须 200 且 Operation-Revision
      ＝rev0+2（ACK＝本操作结果）。
    - mutation="clear"：B 改为 DELETE /api/v1/session（epoch 边界，不推进
      revision）。
    - 返回 (A status, A headers, A body, epoch, rev0, cookie_value)：调用方
      须以 cookie_value 沿用**同一 sid** 做后续断言（复核报告 §四.2/§四.3：
      新建 _Loopback 会另立 sid，不得用于旧 sid 的 revision 断言）。
    """
    assert mutation in ("invalidate", "clear")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=_BASE_ORIGIN
    ) as setup:
        r = await setup.get("/")
        r.raise_for_status()
        cookie_name = "hktax_session"
        cookie_value = r.cookies.get(cookie_name)
        csrf = r.headers.get("X-CSRF-Token")
        assert cookie_value and csrf, "会话/CSRF 建立失败"
        r_meta = await setup.get("/api/v1/meta/version")
        meta = r_meta.json()
        assert "session_epoch" in meta and "input_revision" in meta, (
            "S002 §4.1/Annex 005 §12：meta/version 须附运行时 session_epoch/"
            f"input_revision，实际：{meta!r}"
        )
        epoch = int(meta["session_epoch"])
        rev0 = int(meta["input_revision"])

    body = _canonical_bytes(_v2_salary_body())
    half = len(body) // 2
    first_chunk_seen = asyncio.Event()
    proceed = asyncio.Event()

    async def middle_barrier() -> bytes:
        first_chunk_seen.set()
        await asyncio.wait_for(proceed.wait(), timeout=10)
        return b""

    headers = {
        "host": "127.0.0.1:8000",
        "origin": _BASE_ORIGIN,
        "x-csrf-token": csrf,
        # S002 §4.1 wire 字面量（带引号；A.5 对齐，单一语法）
        "if-match": f'"edit-{epoch}-{rev0}"',
        "content-type": "application/json",
        "cookie": f"{cookie_name}={cookie_value}",
    }
    task_a = asyncio.create_task(
        _drive_asgi(
            app,
            method="POST",
            path=_PREPARE,
            headers=headers,
            body_items=[body[:half], middle_barrier, body[half:]],
        )
    )
    engaged = True
    try:
        await asyncio.wait_for(first_chunk_seen.wait(), timeout=10)
    except asyncio.TimeoutError:
        engaged = False
    assert engaged, (
        "prepare 未进入 body 读取阶段——If-Match CAS 已在认证后拒绝请求"
        "（如带引号 wire 字面量未获支持，A.5），屏障场景无法建立；"
        "生产须先支持 §4.1 字面量再谈保存前再 CAS 语义"
    )

    # 另一「editor」＝同一 sid（同 cookie）的第二标签页：单次提交、强断言
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_BASE_ORIGIN,
        cookies={cookie_name: cookie_value},
    ) as editor_b:
        if mutation == "invalidate":
            r_mut = await editor_b.post(
                _INVALIDATE,
                json={
                    "expected_session_epoch": epoch,
                    "expected_input_revision": rev0 + 1,
                },
                headers={"Origin": _BASE_ORIGIN, "X-CSRF-Token": csrf},
            )
            assert r_mut.status_code == 200, (
                f"B 以 A reservation 后的 rev0+1 单次提交 invalidate 须 200"
                f"（§12.1：reservation 先于 body 读取），实际 {r_mut.status_code}："
                f"{r_mut.text[:200]!r}"
            )
            assert r_mut.headers.get("X-Edit-Operation-Revision") == str(rev0 + 2), (
                "B 的 Operation ACK 须为本操作结果 rev0+2（§12.2），实际："
                f"{r_mut.headers.get('X-Edit-Operation-Revision')!r}"
            )
        else:  # clear（epoch 边界：不推进 revision）
            r_mut = await editor_b.delete(
                "/api/v1/session",
                headers={"Origin": _BASE_ORIGIN, "X-CSRF-Token": csrf},
            )
            assert r_mut.status_code == 200, (
                f"计算期间 clear 须 200（epoch 边界），实际 {r_mut.status_code}"
            )
    proceed.set()
    status, resp_headers, resp_body = await asyncio.wait_for(task_a, timeout=10)
    return status, resp_headers, resp_body, epoch, rev0, cookie_value


# ---------------------------------------------------------------------------
# 节点 1：If-Match 必需＋轮换
# ---------------------------------------------------------------------------


def test_002_prepare_if_match_required_and_rotates(v2) -> None:
    # —— 缺 If-Match → 409 不推进；附 Current 只读快照 ——
    r_missing = v2.post(_PREPARE, json_body=_v2_salary_body())
    assert r_missing.status_code == 409, (
        f"缺 If-Match 须 409 E_CONFIRMATION_STALE（Annex 005 §12.1），实际 "
        f"{r_missing.status_code}：{r_missing.text[:200]!r}"
    )
    assert _error_code(r_missing.json()) == "E_CONFIRMATION_STALE"
    assert "prepared_id" not in r_missing.json()
    epoch, rev0 = _runtime_versions(v2)
    assert r_missing.headers.get("X-Edit-Current-Epoch") == str(epoch)
    assert r_missing.headers.get("X-Edit-Current-Revision") == str(rev0)
    assert r_missing.headers.get("X-Edit-Applied") in (None, "0"), (
        "409 未推进不得携带 Applied=1（Annex 005 §12.2）"
    )

    # —— 匹配 If-Match → 201＋Operation ACK（本操作 rev=rev0+1）——
    r_ok = v2.post(_PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0))
    assert r_ok.status_code == 201, (
        f"匹配 If-Match 须 201，实际 {r_ok.status_code}：{r_ok.text[:300]!r}"
    )
    assert r_ok.headers.get("X-Edit-Applied") == "1", "成功修改须 X-Edit-Applied: 1"
    assert r_ok.headers.get("X-Edit-Operation-Epoch") == str(epoch)
    assert r_ok.headers.get("X-Edit-Operation-Revision") == str(rev0 + 1), (
        "Operation-Revision 须为本请求 reservation 结果（旧值+1，§12.2）"
    )
    assert r_ok.json()["prepared_id"]

    # —— 旧 If-Match 已轮换 → 409 ——
    r_rotated = v2.post(_PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0))
    assert r_rotated.status_code == 409, (
        f"reservation 后旧 If-Match 须 409（revision 已轮换），实际 "
        f"{r_rotated.status_code}"
    )
    assert _error_code(r_rotated.json()) == "E_CONFIRMATION_STALE"

    # —— 新值继续可用 ——
    r_next = v2.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0 + 1)
    )
    assert r_next.status_code == 201, r_next.text
    assert r_next.headers.get("X-Edit-Operation-Revision") == str(rev0 + 2)


# ---------------------------------------------------------------------------
# 节点 2：缺/陈旧 If-Match 且声明超大 → 409（不是 413）、不读 body、不推进
# ---------------------------------------------------------------------------


def test_002_prepare_stale_or_missing_if_match_oversize_409_no_advance(
    v2_counting,
) -> None:
    client, counting = v2_counting

    counting.receive_calls = 0
    r_missing = client.post(_PREPARE, content=_OVERSIZE, headers={})
    assert r_missing.status_code == 409, (
        f"缺 If-Match 即使声明超大也须 409（CAS 先于大小检查，Annex 005 "
        f"§12.1），实际 {r_missing.status_code}"
    )
    assert _error_code(r_missing.json()) == "E_CONFIRMATION_STALE"
    assert counting.receive_calls == 0, "409 不得读 body（§12.1：不调用 receive）"
    epoch, rev0 = _runtime_versions(client)
    assert r_missing.headers.get("X-Edit-Current-Revision") == str(rev0)

    counting.receive_calls = 0
    r_stale = client.post(
        _PREPARE, content=_OVERSIZE, headers=_if_match(epoch, rev0 + 9)
    )
    assert r_stale.status_code == 409, (
        f"陈旧 If-Match 即使声明超大也须 409，实际 {r_stale.status_code}"
    )
    assert _error_code(r_stale.json()) == "E_CONFIRMATION_STALE"
    assert counting.receive_calls == 0
    assert r_stale.headers.get("X-Edit-Current-Revision") == str(rev0)

    # —— 409 未推进：旧值仍可用 ——
    r_ok = client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0)
    )
    assert r_ok.status_code == 201, (
        f"409 不推进 revision，旧 If-Match 仍须可用，实际 {r_ok.status_code}："
        f"{r_ok.text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 节点 3：匹配＋声明超大 → 先推进后 413（Applied=1＋Operation ACK）
# ---------------------------------------------------------------------------


def test_002_prepare_matching_oversize_advances_and_acks_413(v2_counting) -> None:
    client, counting = v2_counting
    epoch, rev0 = _runtime_versions(client)

    counting.receive_calls = 0
    r = client.post(_PREPARE, content=_OVERSIZE, headers=_if_match(epoch, rev0))
    assert r.status_code == 413, (
        f"匹配 If-Match＋声明 >1MB 须 413 E_INPUT_OVERSIZE（Annex 005 §12.1），"
        f"实际 {r.status_code}"
    )
    assert _error_code(r.json()) == "E_INPUT_OVERSIZE"
    assert r.headers.get("X-Edit-Applied") == "1", (
        "匹配后超限须**先推进并失效**再 413（Applied=1，S002 §4.2）"
    )
    assert r.headers.get("X-Edit-Operation-Epoch") == str(epoch)
    assert r.headers.get("X-Edit-Operation-Revision") == str(rev0 + 1), (
        "413 须携带本操作 reservation ACK（rev0+1）"
    )
    assert counting.receive_calls == 0, "声明超限 413 不得读 body（§12.1）"

    # —— 已推进：旧 If-Match 失效，新值可用 ——
    r_old = client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0)
    )
    assert r_old.status_code == 409, "413 已推进，旧 If-Match 须 409"
    r_new = client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0 + 1)
    )
    assert r_new.status_code == 201, r_new.text


# ---------------------------------------------------------------------------
# 节点 4：403（CSRF 失败）不读 body、不推进、不带版本头
# ---------------------------------------------------------------------------


def test_002_prepare_security_403_no_body_read_no_revision(v2_counting) -> None:
    client, counting = v2_counting
    client._token()  # 建立 cookie，但本次提交伪造 token

    counting.receive_calls = 0
    r = client.post(
        _PREPARE,
        content=_OVERSIZE,
        headers={"X-CSRF-Token": "forged-token", "If-Match": "edit-1-0"},
        csrf=False,
    )
    assert r.status_code == 403, f"CSRF 失败须 403，实际 {r.status_code}"
    assert _error_code(r.json()) == "E_CSRF"
    assert counting.receive_calls == 0, "403 不得读 body（§12.1）"
    for name in (
        "X-Edit-Applied",
        "X-Edit-Operation-Epoch",
        "X-Edit-Operation-Revision",
        "X-Edit-Current-Epoch",
        "X-Edit-Current-Revision",
    ):
        assert r.headers.get(name) is None, f"403 不得携带版本头 {name}（§12.2）"
    # 未推进：合法 If-Match 旧值仍可用
    epoch, rev0 = _runtime_versions(client)
    r_ok = client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0)
    )
    assert r_ok.status_code == 201, f"403 不得推进 revision，实际 {r_ok.status_code}"


# ---------------------------------------------------------------------------
# 节点 5：body 只在认证＋reservation 之后读取（happy path）
# ---------------------------------------------------------------------------


def test_002_prepare_body_read_after_auth_and_reservation_only(v2_counting) -> None:
    client, counting = v2_counting
    epoch, rev0 = _runtime_versions(client)

    counting.receive_calls = 0
    r = client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0)
    )
    assert r.status_code == 201, (
        f"合法 prepare 须 201，实际 {r.status_code}：{r.text[:300]!r}"
    )
    assert counting.receive_calls >= 1, "合法请求 body 须被读取（≥1 次 receive）"
    assert r.headers.get("X-Edit-Applied") == "1"
    assert r.headers.get("X-Edit-Operation-Revision") == str(rev0 + 1)


# ---------------------------------------------------------------------------
# 节点 6：分块累计超限 → 已推进＋413（有界读取）
# ---------------------------------------------------------------------------


def test_002_prepare_stream_oversize_bounded_and_invalidated(v2_counting) -> None:
    client, counting = v2_counting
    epoch, rev0 = _runtime_versions(client)

    def _stream():
        yield _OVERSIZE[:65536]
        yield _OVERSIZE[65536:]

    counting.receive_calls = 0
    r = client.post(
        _PREPARE, content=_stream(), headers=_if_match(epoch, rev0)
    )
    assert r.status_code == 413, (
        f"分块累计 >1MB 须 413（有界读取，Annex 005 §12.1），实际 "
        f"{r.status_code}：{r.text[:200]!r}"
    )
    assert _error_code(r.json()) == "E_INPUT_OVERSIZE"
    assert r.headers.get("X-Edit-Applied") == "1", "413 前须已推进（§12.1）"
    assert r.headers.get("X-Edit-Operation-Revision") == str(rev0 + 1)
    assert counting.receive_calls >= 1, "分块路径 body 有被读取"


# ---------------------------------------------------------------------------
# 节点 7：Operation ACK 不被另一 editor 的最新 revision 替换（确定性屏障）
# ---------------------------------------------------------------------------


def test_002_operation_ack_not_replaced_by_other_editor_revision(tmp_path, monkeypatch):
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    status, resp_headers, _body, epoch, rev0, cookie_value = asyncio.run(
        _mid_body_edit_scenario(app)
    )
    assert status == 409, (
        f"body 读取中段另一编辑推进后，保存 prepared 前再 CAS 须 409 stale"
        f"（Annex 005 §12.1 末行），实际 {status}"
    )
    assert resp_headers.get("x-edit-applied") == "1", (
        "本请求 reservation 已发生（Applied=1），即使最终 409（§12.2）"
    )
    assert resp_headers.get("x-edit-operation-revision") == str(rev0 + 1), (
        "Operation ACK 须为本请求自己的 reservation 结果（rev0+1），"
        "不得换成 registry 最新值（另一 editor 已推进到 rev0+2）"
    )
    current = resp_headers.get("x-edit-current-revision")
    if current is not None:
        assert current == str(rev0 + 2), (
            f"Current 头若存在须为另一 editor 推进后的当前值 rev0+2，"
            f"实际 {current!r}"
        )
    # 服务器当前值确已推进两次（A 的 reservation＋B 的 invalidate）——
    # 复核报告 §四.2：须沿用**同一 sid**（原 cookie）断言，不得另立会话
    same_sid = _Loopback(app, cookie_value=cookie_value)
    fresh_sid = _Loopback(app)
    try:
        _epoch_now, rev_now = _runtime_versions(same_sid)
        assert rev_now == rev0 + 2, (
            f"同 sid：A（rev0+1）＋B invalidate（rev0+2）后当前 revision 应为 "
            f"{rev0 + 2}，实际 {rev_now}"
        )
        # B2（复核报告）：input_revision 属每 sid 会话状态——真正的新会话
        # 必须从 revision 0 开始，不受其他 sid 的编辑干涉
        _fresh_epoch, fresh_rev = _runtime_versions(fresh_sid)
        assert fresh_rev == 0, (
            f"新 sid 的 input_revision 须为 0（revision 存于 sid 状态，"
            f"进程级计数会跨会话干涉；复核报告 B2），实际 {fresh_rev}"
        )
    finally:
        same_sid.close()
        fresh_sid.close()


# ---------------------------------------------------------------------------
# 节点 8：prepare 在途编辑 → 409 不落 prepared；以 Current 显式重同步后可用
# ---------------------------------------------------------------------------


def test_002_edit_during_prepare_uses_acked_revision(tmp_path, monkeypatch):
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    status, resp_headers, body_bytes, epoch, rev0, cookie_value = asyncio.run(
        _mid_body_edit_scenario(app)
    )
    assert status == 409, (
        f"reservation 后另一编辑再保存 prepared 须 409 stale（不落 prepared），"
        f"实际 {status}"
    )
    payload = json.loads(body_bytes)
    assert _error_code(payload) == "E_CONFIRMATION_STALE"
    assert "prepared_id" not in payload, "409 stale 不得签发 prepared_id"

    # 客户端以 Current 头显式重同步（不自动接纳）——复核报告 §四.3：须由
    # **原 sid**（同 cookie）用 rev0+2 重新 prepare，不得另立会话消费旧值
    same_sid = _Loopback(app, cookie_value=cookie_value)
    try:
        r_retry = same_sid.post(
            _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0 + 2)
        )
        assert r_retry.status_code == 201, (
            f"原 sid 以 Current（rev0+2）显式重同步后 prepare 须 201，实际 "
            f"{r_retry.status_code}：{r_retry.text[:300]!r}"
        )
    finally:
        same_sid.close()


# ---------------------------------------------------------------------------
# 节点 8b：prepare 在途 clear（epoch 边界）→ 409 不落 prepared（§五.4）
# ---------------------------------------------------------------------------


def test_002_prepare_clear_during_body_read_stale_no_prepared(tmp_path, monkeypatch):
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    status, resp_headers, body_bytes, epoch, rev0, cookie_value = asyncio.run(
        _mid_body_edit_scenario(app, mutation="clear")
    )
    assert status == 409, (
        f"reservation 后 clear（epoch 边界）再保存 prepared 须 409 stale，"
        f"实际 {status}"
    )
    payload = json.loads(body_bytes)
    assert _error_code(payload) == "E_CONFIRMATION_STALE"
    assert "prepared_id" not in payload, "409 stale 不得签发 prepared_id"
    # clear 是 epoch 边界：原 sid 须以新 epoch、revision 0 重新开始
    same_sid = _Loopback(app, cookie_value=cookie_value)
    try:
        r_retry = same_sid.post(
            _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch + 1, 0)
        )
        assert r_retry.status_code == 201, (
            f"clear 后原 sid 须以新 epoch/rev0 重新 prepare，实际 "
            f"{r_retry.status_code}：{r_retry.text[:300]!r}"
        )
    finally:
        same_sid.close()


# ---------------------------------------------------------------------------
# 节点 9：clear＝epoch+1/rev=0 的 epoch 边界（非 reservation）
# ---------------------------------------------------------------------------


def test_002_clear_session_epoch_resets_revision_and_drops_late_replies(v2) -> None:
    epoch0, rev0 = _runtime_versions(v2)
    prepared = _prepare_ok(v2)  # rev0 → rev0+1
    confirmation_id = _confirm_ok(v2, prepared)
    assert _runtime_versions(v2)[1] == rev0 + 1

    r_clear = v2.delete("/api/v1/session")
    assert r_clear.status_code == 200, r_clear.text
    payload = r_clear.json()
    assert payload.get("session_epoch") == epoch0 + 1, (
        f"clear 须 session_epoch+1（Annex 005 §12.3），实际 {payload!r}"
    )
    assert payload.get("input_revision") == 0, (
        f"clear 须 input_revision=0（epoch 边界，非 reservation），实际 {payload!r}"
    )
    assert r_clear.headers.get("X-Edit-Applied") == "1"
    assert r_clear.headers.get("X-Edit-Operation-Epoch") == str(epoch0 + 1)
    assert r_clear.headers.get("X-Edit-Operation-Revision") == "0"

    # 旧 If-Match（epoch0 内的 revision）→ 409；新 epoch 从 rev0 重新开始
    r_old_match = v2.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch0, rev0 + 1)
    )
    assert r_old_match.status_code == 409, "epoch 边界后旧 If-Match 须 409"
    r_new = v2.post(
        _PREPARE,
        json_body=_v2_salary_body(),
        headers=_if_match(epoch0 + 1, 0),
    )
    assert r_new.status_code == 201, (
        f"clear 后新 epoch 从 revision 0 开始，实际 {r_new.status_code}："
        f"{r_new.text[:200]!r}"
    )

    # clear 前签发的确认能力随之失效（迟到回复丢弃，不返回旧税额）
    r_late = v2.post(_SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id})
    assert r_late.status_code == 409, (
        f"clear 后旧确认须 409 E_CONFIRMATION_STALE，实际 {r_late.status_code}"
    )
    assert _error_code(r_late.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r_late.json(), "迟到回复不得携带旧 core/税额"


# ---------------------------------------------------------------------------
# 节点 10：invalidate CAS 失败 409 附当前值、不推进
# ---------------------------------------------------------------------------


def test_002_invalidate_cas_409_current(v2) -> None:
    epoch, rev0 = _runtime_versions(v2)

    r_bad = _invalidate(v2, epoch, rev0 + 7)
    assert r_bad.status_code == 409, (
        f"CAS 失败须 409 E_CONFIRMATION_STALE（S002 §4.2），实际 "
        f"{r_bad.status_code}：{r_bad.text[:200]!r}"
    )
    assert _error_code(r_bad.json()) == "E_CONFIRMATION_STALE"
    assert r_bad.headers.get("X-Edit-Current-Epoch") == str(epoch)
    assert r_bad.headers.get("X-Edit-Current-Revision") == str(rev0), (
        "409 须附当前只读快照（Annex 005 §12.2）"
    )
    # 未推进：原值仍可 CAS 成功
    _, rev_now = _runtime_versions(v2)
    assert rev_now == rev0, "CAS 失败不得推进 revision"

    r_ok = _invalidate(v2, epoch, rev0)
    assert r_ok.status_code == 200, (
        f"正确 CAS 须 200，实际 {r_ok.status_code}：{r_ok.text[:200]!r}"
    )
    payload = r_ok.json()
    assert payload.get("status") == "invalidated"
    assert payload.get("session_epoch") == epoch
    assert payload.get("input_revision") == rev0 + 1


# ---------------------------------------------------------------------------
# 节点 11：invalidate 失效未消费确认、保留 records
# ---------------------------------------------------------------------------


def test_002_invalidate_revokes_pending_keeps_records(v2) -> None:
    prepared = _prepare_ok(v2)
    confirmation_id = _confirm_ok(v2, prepared)
    r_exec = v2.post(_SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id})
    assert r_exec.status_code == 200, (
        f"execute 须 200，实际 {r_exec.status_code}：{r_exec.text[:300]!r}"
    )
    record_id = r_exec.json()["record_id"]

    epoch, rev = _runtime_versions(v2)
    r_inv = _invalidate(v2, epoch, rev)
    assert r_inv.status_code == 200, (
        f"invalidate 须 200，实际 {r_inv.status_code}：{r_inv.text[:200]!r}"
    )

    # 旧 prepared 已删除：以其再 confirm 不得签发新能力
    r_reconfirm = v2.post(
        _CONFIRM,
        json_body={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r_reconfirm.status_code in (403, 409), (
        f"旧 prepared 已被 invalidate 删除，不得再签发确认，实际 "
        f"{r_reconfirm.status_code}"
    )
    assert r_reconfirm.json().get("confirmation_id") != confirmation_id

    # 未消费/已消费确认不得继续产出税额；records 保留
    r_reexec = v2.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_reexec.status_code == 409, (
        f"invalidate 后旧确认执行须 409，实际 {r_reexec.status_code}"
    )
    assert "amounts" not in r_reexec.json()
    r_record = v2.get(f"/api/v1/records/{record_id}")
    assert r_record.status_code == 200, (
        f"invalidate 须保留 records（Annex 005 §12.3），实际 {r_record.status_code}"
    )


# ---------------------------------------------------------------------------
# 节点 12：匹配＋非法 body → 已失效（Applied=1）＋422、无 prepared_id
# ---------------------------------------------------------------------------


def test_002_prepare_invalid_body_still_invalidates_no_capability(v2) -> None:
    epoch, rev0 = _runtime_versions(v2)

    r = v2.post(
        _PREPARE, content=b'{"tax_type": "sala', headers=_if_match(epoch, rev0)
    )
    assert r.status_code == 422, f"非法 JSON 须 422，实际 {r.status_code}"
    assert _error_code(r.json()) == "E_INPUT_TYPE"
    assert "prepared_id" not in r.json()
    assert r.headers.get("X-Edit-Applied") == "1", (
        "匹配 If-Match 后 body 校验失败也须确认本次失效已发生（Applied=1，"
        "Annex 005 §12.1/§12.2）"
    )
    assert r.headers.get("X-Edit-Operation-Revision") == str(rev0 + 1)

    # 旧能力不恢复：旧 If-Match 已失效，需以新值重新 prepare
    r_old = v2.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0)
    )
    assert r_old.status_code == 409, "422 后旧 If-Match 不得复活"
    r_new = v2.post(
        _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0 + 1)
    )
    assert r_new.status_code == 201, r_new.text


# ---------------------------------------------------------------------------
# 节点 13：confirm 原子核对 revision/binding；消费一次
# ---------------------------------------------------------------------------


def test_002_confirm_atomic_revision_binding(v2) -> None:
    # —— 成功链：prepare→confirm→execute 200；再 execute → 409 CONSUMED＋链接 ——
    prepared = _prepare_ok(v2)
    confirmation_id = _confirm_ok(v2, prepared)
    r_exec = v2.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, (
        f"execute 须 200，实际 {r_exec.status_code}：{r_exec.text[:300]!r}"
    )
    record_id = r_exec.json()["record_id"]
    r_again = v2.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_again.status_code == 409, "单次消费：再执行须 409"
    assert _error_code(r_again.json()) == "E_CONFIRMATION_CONSUMED"
    assert r_again.json().get("record_id") == record_id, (
        "已消费重放须附已保存 record 链接（Annex 005 §12.3）"
    )
    assert "amounts" not in r_again.json(), "重放不得重算/返回税额"

    # —— 失效链：prepare→confirm→invalidate → execute 409 STALE（未消费）——
    prepared2 = _prepare_ok(v2)
    confirmation2 = _confirm_ok(v2, prepared2)
    epoch, rev = _runtime_versions(v2)
    assert _invalidate(v2, epoch, rev).status_code == 200
    r_stale = v2.post(_SALARIES_EXECUTE, json_body={"confirmation_id": confirmation2})
    assert r_stale.status_code == 409, (
        f"修订后未消费确认须 409 E_CONFIRMATION_STALE，实际 {r_stale.status_code}"
    )
    assert _error_code(r_stale.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r_stale.json()
    # 重新 prepare＋confirm 后可执行
    prepared3 = _prepare_ok(v2)
    confirmation3 = _confirm_ok(v2, prepared3)
    r_re = v2.post(_SALARIES_EXECUTE, json_body={"confirmation_id": confirmation3})
    assert r_re.status_code == 200, (
        f"重新确认后须可执行，实际 {r_re.status_code}：{r_re.text[:300]!r}"
    )


# ---------------------------------------------------------------------------
# 节点 14：错目标 409 不消费
# ---------------------------------------------------------------------------


def test_002_wrong_target_409_not_consumed(v2) -> None:
    prepared = _prepare_ok(v2)
    confirmation_id = _confirm_ok(v2, prepared)

    r_wrong = v2.post(_PROFITS_EXECUTE, json_body={"confirmation_id": confirmation_id})
    assert r_wrong.status_code == 409, (
        f"错目标须 409 E_CONFIRMATION_TARGET_MISMATCH（不消费），实际 "
        f"{r_wrong.status_code}"
    )
    assert _error_code(r_wrong.json()) == "E_CONFIRMATION_TARGET_MISMATCH"

    r_right = v2.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_right.status_code == 200, (
        f"错目标拒绝不得消费确认：正确目标仍须 200，实际 "
        f"{r_right.status_code}：{r_right.text[:300]!r}"
    )


# ===========================================================================
# 新 Red：原子窗口／准入失败面／安全失败（复核报告 §五.4/§五.7＋R2/R3⑧）
# ===========================================================================


def test_002_confirm_after_edit_or_clear_rejected_then_recovered(v2) -> None:
    """confirm 前发生编辑/clear → 不签发可执行能力；恢复链可用（§五.4：
    无 stale prepared/能力、失败不消费、恢复后有效确认可执行）。"""
    # —— (a) prepare → invalidate → confirm 不得签发能力 ——
    prepared = _prepare_ok(v2)
    epoch, rev = _runtime_versions(v2)
    assert _invalidate(v2, epoch, rev).status_code == 200
    r_confirm_stale = v2.post(
        _CONFIRM,
        json_body={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r_confirm_stale.status_code in (403, 409), (
        f"编辑后旧 prepared 已删除，confirm 不得签发能力，实际 "
        f"{r_confirm_stale.status_code}：{r_confirm_stale.text[:200]!r}"
    )
    assert "confirmation_id" not in r_confirm_stale.json()

    # —— (b) prepare → clear → confirm 不得签发能力（epoch 边界）——
    prepared2 = _prepare_ok(v2)
    assert v2.delete("/api/v1/session").status_code == 200
    r_confirm_cleared = v2.post(
        _CONFIRM,
        json_body={
            "prepared_id": prepared2["prepared_id"],
            "canonical_input_hash": prepared2["input_hash"],
            "expected_binding": prepared2["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r_confirm_cleared.status_code in (403, 409), (
        f"clear 后旧 prepared 已清除，confirm 不得签发能力，实际 "
        f"{r_confirm_cleared.status_code}"
    )
    assert "confirmation_id" not in r_confirm_cleared.json()

    # —— (c) 恢复链：重新 prepare→confirm→execute 200（失败未污染会话）——
    prepared3 = _prepare_ok(v2)
    confirmation3 = _confirm_ok(v2, prepared3)
    r_exec = v2.post(_SALARIES_EXECUTE, json_body={"confirmation_id": confirmation3})
    assert r_exec.status_code == 200, (
        f"恢复后有效确认须可执行，实际 {r_exec.status_code}："
        f"{r_exec.text[:300]!r}"
    )


def test_002_pointer_change_between_confirm_and_execute_not_consumed(
    tmp_path, monkeypatch
) -> None:
    """confirm 与 execute 之间指针切换（发布另一 v2 束）→ execute 409 不消费；
    指针复原后同一确认仍可执行（§五.4：guard/pointer 改变后消费）。"""
    import copy as _copy

    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    seed = _fixed_v2_seed()
    RuleStore(db).publish(seed, evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    client = _Loopback(app)
    try:
        prepared = _prepare_ok(client)
        confirmation_id = _confirm_ok(client, prepared)

        alt = _copy.deepcopy(seed)
        alt["data"]["allowances"]["2025_26"]["basic"]["amount"] = "130000"
        # P1 r2 接线适配（protocol 咨询 §3.2：另建 RuleStore 不能证明同一锁域；
        # 指针切换走真实 app.state.store 生产写路径）
        app.state.store.publish(alt, evidence_refs=[])

        r_moved = client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_moved.status_code == 409, (
            f"指针切换后 binding 不再匹配 → 409 E_CONFIRMATION_STALE 且不消费，"
            f"实际 {r_moved.status_code}：{r_moved.text[:200]!r}"
        )
        assert _error_code(r_moved.json()) == "E_CONFIRMATION_STALE"
        assert "amounts" not in r_moved.json()

        # 指针复原（同内容 republish → 同 hash 派生同 bundle_id）→ 同一确认可执行
        app.state.store.publish(seed, evidence_refs=[])
        r_restored = client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_restored.status_code == 200, (
            f"失败未消费：指针复原后同一确认须可执行，实际 "
            f"{r_restored.status_code}：{r_restored.text[:300]!r}"
        )
    finally:
        client.close()


def test_002_prepare_unexpected_error_keeps_ack_and_no_store(
    tmp_path, monkeypatch
) -> None:
    """reservation 后意外异常（500）→ 仍携带本操作 ACK＋no-store，不泄露原文
    （复核报告 R2：安全异常路径保留 ACK 且不泄露原文；C5.5 /api/* no-store）。"""
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    client = _Loopback(app)
    try:
        epoch, rev0 = _runtime_versions(client)

        import app.api.routes as routes_module

        def _boom(*_args: Any, **_kwargs: Any):
            raise RuntimeError("内部秘密细节-不应泄露")

        monkeypatch.setattr(routes_module, "validate_prepare_body", _boom)
        r = client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=_if_match(epoch, rev0)
        )
        assert r.status_code == 500, (
            f"注入的意外异常应产生 500（非 4xx 伪装），实际 {r.status_code}："
            f"{r.text[:200]!r}"
        )
        assert r.headers.get("X-Edit-Applied") == "1", (
            "500 须保留本操作 ACK（reservation 已发生；复核报告 R2）"
        )
        assert r.headers.get("X-Edit-Operation-Revision") == str(rev0 + 1), (
            "500 的 Operation ACK 须为本请求 reservation 结果（rev0+1）"
        )
        cache_control = (r.headers.get("Cache-Control") or "").lower()
        assert "no-store" in cache_control, (
            f"/api/* 500 响应须 no-store（C5.5），实际 {cache_control!r}"
        )
        assert "内部秘密细节" not in r.text, (
            "500 不得泄露异常原文（R2：安全异常路径不泄露原文）"
        )
    finally:
        client.close()


def test_002_clear_session_deletes_consent_same_operation(tmp_path, monkeypatch) -> None:
    """clear 须在同一操作内失效该 sid 的 AI consent（Annex 005 §12.3「删除
    旧会话状态/未消费能力/该 sid consent」；复核报告 R2/Bu2：clear 未清
    ConsentStore 为缺陷）。"""
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    client = _Loopback(app)
    try:
        r_preview = client.post(
            "/api/v1/ai/consent/preview",
            json_body={"messages": [{"role": "user", "content": "测试消息"}]},
        )
        assert r_preview.status_code == 201, r_preview.text
        consent_id = r_preview.json()["consent_id"]
        r_grant = client.post(
            "/api/v1/ai/consent", json_body={"consent_id": consent_id}
        )
        assert r_grant.status_code == 200, r_grant.text

        assert client.delete("/api/v1/session").status_code == 200

        registry = app.state.registry
        sid = registry.sid_from_cookie(client.cookie_value)
        assert sid, "会话 cookie 未能解析出 sid"
        item = app.state.consent_store.get(sid, consent_id)
        status = (item or {}).get("status")
        assert status not in ("prepared", "granted"), (
            f"clear 须同操作失效该 sid consent（不得残留可用 granted/prepared），"
            f"实际 status={status!r}"
        )
    finally:
        client.close()


def test_002_invalidated_consent_terminal_not_regrantable(v2) -> None:
    """invalidate 后 consent 进入失效终态：不得再次授予/外发（Bu2 P1 边界；
    005 §12.3 invalidate 失效该 sid prepared/granted consent）。"""
    r_preview = v2.post(
        "/api/v1/ai/consent/preview",
        json_body={"messages": [{"role": "user", "content": "测试消息"}]},
    )
    assert r_preview.status_code == 201, r_preview.text
    consent_id = r_preview.json()["consent_id"]
    assert (
        v2.post("/api/v1/ai/consent", json_body={"consent_id": consent_id}).status_code
        == 200
    )

    epoch, rev = _runtime_versions(v2)
    assert _invalidate(v2, epoch, rev).status_code == 200

    r_regrant = v2.post("/api/v1/ai/consent", json_body={"consent_id": consent_id})
    assert r_regrant.status_code == 409, (
        f"失效终态不可恢复 granted（Bu2），实际 {r_regrant.status_code}"
    )
    assert _error_code(r_regrant.json()) == "E_CONSENT_STALE"

    r_chat = v2.post(
        "/api/v1/ai/chat",
        json_body={
            "consent_id": consent_id,
            "messages": [{"role": "user", "content": "测试消息"}],
        },
    )
    assert r_chat.status_code == 409, (
        f"失效 consent 不得外发，实际 {r_chat.status_code}"
    )
    assert _error_code(r_chat.json()) == "E_CONSENT_STALE"


def test_002_invalidate_body_closed_keys(v2) -> None:
    """invalidate 请求闭合两键（Annex 005 §12.3「闭合请求」；R3⑧：当前接受
    extra，探针 200）。"""
    epoch, rev = _runtime_versions(v2)
    r = v2.post(
        _INVALIDATE,
        json_body={
            "expected_session_epoch": epoch,
            "expected_input_revision": rev,
            "unexpected_key": 1,
        },
    )
    assert r.status_code == 422, (
        f"invalidate 请求体须闭合两键（Annex 005 §12.3；R3⑧），实际 "
        f"{r.status_code}：{r.text[:200]!r}"
    )
    code = _error_code(r.json())
    assert code in ("E_INPUT_EXTRA", "E_INPUT_TYPE"), (
        f"extra 键须结构化拒绝，实际 {code!r}"
    )
    # 未推进：原版本值仍可 CAS 成功
    r_ok = _invalidate(v2, epoch, rev)
    assert r_ok.status_code == 200, (
        f"闭合拒绝不得推进 revision；原值 CAS 须仍成功，实际 {r_ok.status_code}"
    )


# ===========================================================================
# P1 r2 新 Red（protocol-consultation.md §5 A1–A4/E18–E22；
# 计划 p1-r2-bounded-plan.md §2 RED-P／§3 冻结签名）
# ===========================================================================


def _sid_of(app: Any, client: _Loopback) -> str:
    registry = app.state.registry
    sid = registry.sid_from_cookie(client.cookie_value)
    assert sid, "会话 cookie 未能解析出 sid"
    return sid


def _confirm_body(prepared: dict) -> dict:
    return {
        "prepared_id": prepared["prepared_id"],
        "canonical_input_hash": prepared["input_hash"],
        "expected_binding": prepared["expected_binding"],
        "acknowledge": True,
    }


def _alt_seed() -> dict:
    """与现行指针不同内容的合法 v2 束（A2：confirm 窗口内发布）。"""
    import copy as _copy

    alt = _copy.deepcopy(_fixed_v2_seed())
    alt["data"]["allowances"]["2025_26"]["basic"]["amount"] = "130000"
    return alt


def test_002_r2_a1_prepared_epoch_identity_no_restamp(v2app) -> None:
    """§5 A1/A4：confirm 核 prepared epoch——clear 后旧 prepared 不得被重盖
    新 epoch 签发；新 prepare 后恢复成功；ConfirmationSnapshot 贯穿身份字段
    （含输入 schema_version）。

    【接口型 Red】issue_confirmation 缺 plan §3 冻结签名（*, store）→
    TypeError 为失败原因，不冒充行为 Red。"""
    from app.core.errors import AppError

    app, client = v2app
    registry = app.state.registry

    # —— 正向：冻结签名签发 → Snapshot 身份字段（epoch/schema/target/hash）——
    prepared = _prepare_ok(client)
    sid = _sid_of(app, client)
    snapshot = registry.issue_confirmation(
        sid,
        prepared["prepared_id"],
        prepared["input_hash"],
        prepared["expected_binding"],
        store=app.state.store,
    )
    for name in (
        "confirmation_id", "session_epoch", "input_revision", "tax_type",
        "schema_version", "execute_target", "input_hash", "binding",
    ):
        _field(snapshot, name)
    assert _field(snapshot, "session_epoch") == registry.session_epoch, (
        "Snapshot epoch 须为签发时 prepared 所属 epoch（不得重盖）"
    )
    assert _field(snapshot, "schema_version") == "2.0.0", (
        "A4：输入信封 schema_version 须贯穿 confirmation（非 bundle "
        "rules_schema_version）"
    )
    assert _field(snapshot, "execute_target") == _SALARIES_EXECUTE

    # —— 旧 prepared epoch：prepare → clear → 旧 prepared 不得再签发 ——
    prepared_old = _prepare_ok(client)
    assert client.delete("/api/v1/session").status_code == 200
    with pytest.raises(AppError):
        registry.issue_confirmation(
            sid,
            prepared_old["prepared_id"],
            prepared_old["input_hash"],
            prepared_old["expected_binding"],
            store=app.state.store,
        )

    # —— 新 B prepare 后恢复成功（失败不污染会话）——
    prepared_new = _prepare_ok(client)
    snapshot_new = registry.issue_confirmation(
        sid,
        prepared_new["prepared_id"],
        prepared_new["input_hash"],
        prepared_new["expected_binding"],
        store=app.state.store,
    )
    assert _field(snapshot_new, "session_epoch") == registry.session_epoch
    assert _field(snapshot_new, "confirmation_id") != _field(
        snapshot, "confirmation_id"
    )


def test_002_r2_a1b_cross_sid_global_epoch_blocks_old_prepared(v2app) -> None:
    """§5 A1（review 10）：跨 sid 全局 epoch——B sid prepare；A sid clear
    （全局 session_epoch+1）；B 旧 prepared 仍在但属旧 epoch → confirm 409
    E_CONFIRMATION_STALE、不签发能力；B 以**新** prepare 恢复成功。"""
    app, client_b = v2app
    registry = app.state.registry
    client_a = _Loopback(app)
    try:
        prepared_b = _prepare_ok(client_b)
        sid_b = _sid_of(app, client_b)
        epoch_before = registry.session_epoch
        assert client_a.delete("/api/v1/session").status_code == 200, (
            "A sid clear 须 200（epoch 边界）"
        )
        assert registry.session_epoch == epoch_before + 1, (
            f"clear 须推进全局 session_epoch，实际 {registry.session_epoch}"
        )
        assert registry.get_prepared(sid_b, prepared_b["prepared_id"]) is not None, (
            "前置校验：B 旧 prepared 仍在（clear 只清 A sid）"
        )
        r_confirm = client_b.post(_CONFIRM, json_body=_confirm_body(prepared_b))
        assert r_confirm.status_code == 409, (
            f"旧 epoch 的 prepared 不得再签发能力（全局 epoch 语义），实际 "
            f"{r_confirm.status_code}：{r_confirm.text[:250]!r}"
        )
        assert _error_code(r_confirm.json()) == "E_CONFIRMATION_STALE"
        assert "confirmation_id" not in r_confirm.json(), "不得签发能力"
        prepared_new = _prepare_ok(client_b)
        confirmation_id = _confirm_ok(client_b, prepared_new)
        r_exec = client_b.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_exec.status_code == 200, (
            f"新 prepare 后恢复成功，实际 {r_exec.status_code}：{r_exec.text[:250]!r}"
        )
    finally:
        client_a.close()


def _real_input_schema_carrier(prepared: Any) -> tuple:
    """PQA-02：发现真实生产负载的输入 schema 承载（仅顶层 schema_version 或
    input_schema_version；不递归 binding/profile/bundle；不注入 key）。
    返回 (字段名, 值)；无承载 → (None, None)；两个承载并存 → ("__ambiguous__",
    None)（职责不清时 held，不猜优先）。"""
    found: list = []
    for name in ("schema_version", "input_schema_version"):
        if isinstance(prepared, Mapping):
            if name in prepared:
                found.append((name, prepared[name]))
        else:
            value = getattr(prepared, name, _MISSING)
            if value is not _MISSING:
                found.append((name, value))
    if not found:
        return None, None
    if len(found) > 1:
        return "__ambiguous__", None
    return found[0]


def _authoritative_material_same(raw: Any, stored: Any) -> bool | None:
    """PQA-02：按实际记录形态核对透明 spy 材料与真实 get_prepared 读回：
    Mapping→全部键值相等；同类型实际 dataclass→声明数据字段全等；同类型
    真实 namedtuple（tuple 且标准 `_fields` 元组声明）→元组全等；任意同型
    普通 tuple 或其它表示无法核实 → None（held，不逼 dict、不发明比较协议）。"""
    import dataclasses

    if isinstance(raw, Mapping) and isinstance(stored, Mapping):
        return dict(stored) == dict(raw)
    if (
        dataclasses.is_dataclass(raw)
        and not isinstance(raw, type)
        and dataclasses.is_dataclass(stored)
        and not isinstance(stored, type)
    ):
        if type(raw) is not type(stored):
            return False
        return all(
            getattr(stored, field.name) == getattr(raw, field.name)
            for field in dataclasses.fields(raw)
        )
    raw_fields = getattr(raw, "_fields", None)
    stored_fields = getattr(stored, "_fields", None)
    if isinstance(raw, tuple) and isinstance(stored, tuple):
        # D4-PQA-02：仅两侧均为标准 `_fields`（tuple 且全 str）声明的真实
        # namedtuple 才可核实；任意同型普通 tuple（如 ("x",)）不是可核实的
        # 权威表示 → None（held），按值相等直判 True 属过度放宽。
        standard = (
            isinstance(raw_fields, tuple)
            and all(isinstance(name, str) for name in raw_fields)
            and isinstance(stored_fields, tuple)
            and all(isinstance(name, str) for name in stored_fields)
        )
        if not standard:
            return None
        return type(raw) is type(stored) and tuple(stored) == tuple(raw)
    return None


def _rebuild_with_field(obj: Any, field_name: str, value: Any) -> Any:
    """PQA-02：仅对真实 producer/权威存储既有表示中**已存在**的字段做单字段
    重建（不得插入缺失字段）；只支持 Mapping／实际 dataclass／真实 namedtuple
    （tuple 且标准 `_fields` 声明），其它表示 → None（held）：
    不逼 dict、不把任意 callable `_replace` 当标准记录。"""
    import dataclasses

    if isinstance(obj, Mapping):
        if field_name not in obj:
            return None
        rebuilt = dict(obj)
        rebuilt[field_name] = value
        return rebuilt
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        if field_name not in {field.name for field in dataclasses.fields(obj)}:
            return None
        try:
            return dataclasses.replace(obj, **{field_name: value})
        except (TypeError, ValueError):
            return None
    declared = getattr(obj, "_fields", None)
    if (
        isinstance(obj, tuple)
        and isinstance(declared, tuple)
        and all(isinstance(name, str) for name in declared)
    ):
        if field_name not in declared:
            return None
        try:
            return obj._replace(**{field_name: value})
        except (TypeError, ValueError):
            return None
    return None


def test_002_r2_a4a_confirm_rejects_corrupted_prepared_schema(v2app, monkeypatch) -> None:
    """§5 A4/PQA-02（stage/code 已批准；旧“注入 2.0.1 后 confirm 200”行为标签
    撤回）：真实 save_prepared_if_current 边界透明 spy 记录**未经修改**的
    producer 材料并原样委托，与真实 get_prepared 对照；只修改**真实存在且等于
    已验证信封版本**的输入 schema 承载（schema_version 或 input_schema_version；
    两者并存/职责不清 → HELD；缺失 → INTERFACE_ONLY，不注入影子 key）→
    confirm 409 E_CONFIRMATION_STALE、不签发能力。"""
    app, client = v2app
    registry = app.state.registry
    real_save = registry.save_prepared_if_current
    captured: list = []

    def _spy(sid: Any, prepared: Any, expected_epoch: int, expected_revision: int):
        captured.append(prepared)
        return real_save(sid, prepared, expected_epoch, expected_revision)

    monkeypatch.setattr(registry, "save_prepared_if_current", _spy)
    prepared = _prepare_ok(client)  # 合法 201
    assert prepared.get("status") == "prepared"
    assert captured, "前置校验：真实 producer 未被调用（透明 spy 未命中）"
    raw = captured[-1]
    sid = _sid_of(app, client)
    stored = registry.get_prepared(sid, prepared["prepared_id"])
    assert stored is not None, "前置校验：prepared 已保存"
    same_material = _authoritative_material_same(raw, stored)
    assert same_material is not None, (
        "HELD：producer 材料与 get_prepared 读回无法按实际记录形态核实"
        f"（不逼 dict）：{type(raw)!r} / {type(stored)!r}"
    )
    assert same_material, (
        "透明 spy 记录的材料须与真实 get_prepared 一致（未被测试修改）"
    )
    carrier_name, carrier_value = _real_input_schema_carrier(raw)
    if carrier_name == "__ambiguous__":
        raise AssertionError(
            "HELD：schema_version 与 input_schema_version 并存，职责不清（不猜优先）"
        )
    assert carrier_name is not None, (
        "INTERFACE_ONLY：真实 save_prepared_if_current 负载无输入 schema 承载"
        "（schema_version/input_schema_version 均不存在；不注入影子 key，"
        "旧行为标签已撤回）"
    )
    assert carrier_value == "2.0.0", (
        f"前置校验：真实输入 schema 承载须等于已验证信封版本 2.0.0，实际 "
        f"{carrier_name}={carrier_value!r}"
    )
    stored_carrier_name, stored_carrier_value = _real_input_schema_carrier(stored)
    assert (
        stored_carrier_name == carrier_name
        and stored_carrier_value == carrier_value
    ), (
        "前置校验：权威存储须已有同一输入 schema 承载与值（只改已有字段、"
        f"不插入缺失字段），实际 {stored_carrier_name}={stored_carrier_value!r}"
    )
    corrupted = _rebuild_with_field(stored, carrier_name, "2.0.1")
    assert corrupted is not None, (
        "INTERFACE_ONLY：无法安全单字段重建权威 prepared 表示（held）"
    )
    snapshot = registry.version_snapshot(sid)
    saved = registry.save_prepared_if_current(
        sid, corrupted, snapshot["session_epoch"], snapshot["input_revision"]
    )
    assert saved is True, "前置校验：真实 producer 覆写权威 prepared 须成功（CAS 当前）"
    readback_name, readback_value = _real_input_schema_carrier(
        registry.get_prepared(sid, prepared["prepared_id"])
    )
    assert readback_name == carrier_name and readback_value == "2.0.1", (
        "前置校验：权威 prepared 的输入 schema 身份确被单变量破坏"
    )
    r_confirm = client.post(_CONFIRM, json_body=_confirm_body(prepared))
    assert r_confirm.status_code == 409, (
        f"schema 身份被破坏的 prepared 不得签发能力，实际 {r_confirm.status_code}："
        f"{r_confirm.text[:250]!r}"
    )
    assert _error_code(r_confirm.json()) == "E_CONFIRMATION_STALE"
    assert "confirmation_id" not in r_confirm.json()
    # 恢复：真实 producer 下以 NEW prepare＋confirm 重新签发（不复活被破坏
    # 身份的确认能力）
    prepared_new = _prepare_ok(client)
    confirmation_new = _confirm_ok(client, prepared_new)
    assert confirmation_new, "NEW 合法 prepare＋confirm 须重新签发能力"


def test_002_r2_a4b_consume_rejects_corrupted_prepared_schema(v2app, monkeypatch) -> None:
    """§5 A4/PQA-02：合法 prepare＋confirm 后，经真实 producer 透明 spy 核实
    真实输入 schema 承载（同 A4a；缺失 → INTERFACE_ONLY，不注入影子 key）；
    仅破坏该真实字段（其余 identity 一致）→ consume 409 E_CONFIRMATION_STALE、
    不消费、无新 execution/record 身份键、引擎阶段增量 0；恢复用 NEW 合法
    prepare＋confirm（不复活 stale 身份）。内部 error.field 未冻结，不新增断言。"""
    import app.api.routes as routes_module

    app, client = v2app
    registry = app.state.registry
    real_save = registry.save_prepared_if_current
    captured: list = []

    def _spy(sid: Any, prepared: Any, expected_epoch: int, expected_revision: int):
        captured.append(prepared)
        return real_save(sid, prepared, expected_epoch, expected_revision)

    monkeypatch.setattr(registry, "save_prepared_if_current", _spy)
    prepared = _prepare_ok(client)
    confirmation_id = _confirm_ok(client, prepared)
    sid = _sid_of(app, client)
    stored = registry.get_prepared(sid, prepared["prepared_id"])
    assert stored is not None
    assert captured, "前置校验：真实 producer 未被调用（透明 spy 未命中）"
    raw = captured[-1]
    same_material = _authoritative_material_same(raw, stored)
    assert same_material is not None, (
        "HELD：producer 材料与 get_prepared 读回无法按实际记录形态核实"
        f"（不逼 dict）：{type(raw)!r} / {type(stored)!r}"
    )
    assert same_material, (
        "透明 spy 记录的材料须与真实 get_prepared 一致（未被测试修改）"
    )
    carrier_name, carrier_value = _real_input_schema_carrier(raw)
    if carrier_name == "__ambiguous__":
        raise AssertionError(
            "HELD：schema_version 与 input_schema_version 并存，职责不清（不猜优先）"
        )
    assert carrier_name is not None, (
        "INTERFACE_ONLY：真实 save_prepared_if_current 负载无输入 schema 承载"
        "（不注入影子 key）"
    )
    assert carrier_value == "2.0.0", (
        f"前置校验：真实输入 schema 承载须等于已验证信封版本 2.0.0，实际 "
        f"{carrier_name}={carrier_value!r}"
    )
    stored_carrier_name, stored_carrier_value = _real_input_schema_carrier(stored)
    assert (
        stored_carrier_name == carrier_name
        and stored_carrier_value == carrier_value
    ), (
        "前置校验：权威存储须已有同一输入 schema 承载与值（只改已有字段、"
        f"不插入缺失字段），实际 {stored_carrier_name}={stored_carrier_value!r}"
    )
    corrupted = _rebuild_with_field(stored, carrier_name, "2.0.1")
    assert corrupted is not None, (
        "INTERFACE_ONLY：无法安全单字段重建权威 prepared 表示（held）"
    )
    snapshot = registry.version_snapshot(sid)
    saved = registry.save_prepared_if_current(
        sid, corrupted, snapshot["session_epoch"], snapshot["input_revision"]
    )
    assert saved is True, "前置校验：真实 producer 覆写权威 prepared 须成功（CAS 当前）"
    readback_name, readback_value = _real_input_schema_carrier(
        registry.get_prepared(sid, prepared["prepared_id"])
    )
    assert readback_name == carrier_name and readback_value == "2.0.1", (
        "前置校验：权威 prepared 的输入 schema 身份确被单变量破坏"
    )
    before_ids = _red_identity_sets(registry, sid)
    engine = {"count": 0}
    real_engine = routes_module.run_engine

    def _counting_engine(*args: Any, **kwargs: Any):
        engine["count"] += 1
        return real_engine(*args, **kwargs)

    monkeypatch.setattr(routes_module, "run_engine", _counting_engine)
    r = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 409, (
        f"已签发能力在 consume 发现 schema 身份被破坏 → 409 stale、不消费，"
        f"实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert _error_code(r.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r.json() and "record_id" not in r.json()
    assert engine["count"] == 0, "身份破坏须在引擎阶段前拒绝（引擎阶段增量 0）"
    consumption = registry.get_consumption(sid, confirmation_id)
    assert consumption is not None and consumption["consumed"] is False, (
        f"不得消费（consumed=false），实际 {consumption!r}"
    )
    assert consumption["record_id"] is None and consumption["core_hash"] is None
    assert _red_identity_sets(registry, sid) == before_ids, (
        "不得产生新 execution/record 身份键"
    )
    # 恢复：NEW 合法 prepare＋confirm（不复活 stale 身份）
    prepared_new = _prepare_ok(client)
    confirmation_new = _confirm_ok(client, prepared_new)
    r_ok = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_new}
    )
    assert r_ok.status_code == 200, (
        f"NEW 合法 prepare＋confirm 须恢复执行，实际 {r_ok.status_code}："
        f"{r_ok.text[:250]!r}"
    )
    assert r_ok.json().get("record_id")


def test_002_r2_a2_confirm_binding_read_then_publish_rejected(v2app, monkeypatch) -> None:
    """§5 A2：route 已在锁外读取 current binding 后、issue_confirmation 签发
    前发布新束 → 不得签发旧 binding 能力（须在 registry→store 临界区重读
    current，不得消费锁外缓存参数）。"""
    app, client = v2app
    prepared = _prepare_ok(client)
    registry = app.state.registry

    entered = threading.Event()
    release = threading.Event()
    original_issue = registry.issue_confirmation

    def _gated(*args: Any, **kwargs: Any):
        entered.set()
        assert release.wait(timeout=10), "A2 屏障未在 10s 内放行（死锁看门狗）"
        return original_issue(*args, **kwargs)

    monkeypatch.setattr(registry, "issue_confirmation", _gated)
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = client.post(
            _CONFIRM, json_body=_confirm_body(prepared)
        )

    thread = threading.Thread(target=_worker, name="hktax-test-a2")
    thread.start()
    try:
        assert entered.wait(timeout=10), "confirm 未进入 issue_confirmation"
        app.state.store.publish(_alt_seed(), evidence_refs=[])
    finally:
        release.set()
        thread.join(timeout=15)
    assert "response" in outcome
    r = outcome["response"]
    assert r.status_code == 409, (
        f"签发窗口内指针已切换 → 不得签发旧 binding 能力（临界区重读 current），"
        f"实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert _error_code(r.json()) == "E_CONFIRMATION_STALE"
    assert "confirmation_id" not in r.json(), "不得签发能力"


def test_002_r2_a3_confirm_edit_clear_linear_orders(v2app, monkeypatch) -> None:
    """§5 A3：confirm 与 edit/clear 的两种合法线性次序——(i) edit 先提交 →
    不得签发；(ii) 签发先提交、之后 clear → 能力随 clear 失效（不撤回已在途
    的 HTTP 成功响应）。边界锚点（现实现已闭合，防止 Green 回归）。"""
    app, client = v2app

    # —— (i) issue_confirmation 入口屏障：invalidate 先提交 → confirm 拒绝 ——
    prepared = _prepare_ok(client)
    registry = app.state.registry
    entered = threading.Event()
    release = threading.Event()
    original_issue = registry.issue_confirmation

    def _gated(*args: Any, **kwargs: Any):
        entered.set()
        assert release.wait(timeout=10), "A3 屏障未在 10s 内放行（死锁看门狗）"
        return original_issue(*args, **kwargs)

    monkeypatch.setattr(registry, "issue_confirmation", _gated)
    editor = client.clone()
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = client.post(
            _CONFIRM, json_body=_confirm_body(prepared)
        )

    thread = threading.Thread(target=_worker, name="hktax-test-a3")
    thread.start()
    try:
        assert entered.wait(timeout=10), "confirm 未进入 issue_confirmation"
        epoch, rev = _runtime_versions(editor)
        r_inv = _invalidate(editor, epoch, rev)
        assert r_inv.status_code == 200, (
            f"edit 先提交：invalidate 须 200，实际 {r_inv.status_code}"
        )
    finally:
        release.set()
        thread.join(timeout=15)
        editor.close()
    r_edit_first = outcome["response"]
    assert r_edit_first.status_code in (403, 409), (
        f"edit 先提交 → confirm 不得签发能力（§3.4：无条目 E_CONFIRMATION_"
        f"REQUIRED／失配 E_CONFIRMATION_STALE），实际 {r_edit_first.status_code}"
    )
    assert "confirmation_id" not in r_edit_first.json()

    # —— (ii) 签发先提交、之后 clear → 旧确认执行须 409（能力随 clear 失效）——
    prepared2 = _prepare_ok(client)
    confirmation2 = _confirm_ok(client, prepared2)
    assert client.delete("/api/v1/session").status_code == 200
    r_late = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation2}
    )
    assert r_late.status_code == 409, (
        f"clear 后旧确认须 409（能力失效），实际 {r_late.status_code}"
    )
    assert _error_code(r_late.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r_late.json()


_E18_MARKER = "E18-唯一冻结消息标记-不得残留"


def test_002_r2_e18_clear_deletes_all_consent_states_frozen_payload(v2app) -> None:
    """§5 E18：clear 真删除该 sid 全部 consent 状态（prepared/granted/
    consumed/revoked/expired/stale）与冻结消息；另一 sid 不被误删；registry
    旧负载不可见。不检查已发外部副本或测试局部变量被「清零」。"""
    app, client = v2app
    consent_store = app.state.consent_store

    # registry 旧负载对照（prepared＋record）
    prepared_calc = _prepare_ok(client)
    sid = _sid_of(app, client)  # prepare 已建立会话 cookie（clear 保留同一 sid）
    confirmation_calc = _confirm_ok(client, prepared_calc)
    r_exec = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_calc}
    )
    assert r_exec.status_code == 200, r_exec.text[:250]
    record_id = r_exec.json()["record_id"]

    def _preview() -> str:
        r = client.post(
            "/api/v1/ai/consent/preview",
            json_body={"messages": [{"role": "user", "content": _E18_MARKER}]},
        )
        assert r.status_code == 201, r.text[:250]
        return r.json()["consent_id"]

    # 六状态条目（含唯一消息 marker 与 frozen bytes）；consumed 走合法链
    # prepared→granted→consumed（review 8：不得 prepared 直接 consumed）
    cid_prepared = _preview()
    cid_granted = _preview()
    r_grant = client.post("/api/v1/ai/consent", json_body={"consent_id": cid_granted})
    assert r_grant.status_code == 200, r_grant.text[:250]
    cid_consumed = _preview()
    assert consent_store.transition(sid, cid_consumed, "granted") is not None, (
        "合法转换 prepared→granted 须成功"
    )
    assert consent_store.transition(sid, cid_consumed, "consumed") is not None, (
        "合法转换 granted→consumed 须成功"
    )
    cid_revoked = _preview()
    assert consent_store.transition(sid, cid_revoked, "revoked") is not None
    cid_expired = _preview()
    assert consent_store.transition(sid, cid_expired, "expired") is not None
    cid_stale = _preview()
    assert consent_store.transition(sid, cid_stale, "stale") is not None
    old_ids = {
        cid_prepared, cid_granted, cid_consumed, cid_revoked, cid_expired,
        cid_stale,
    }
    # review 8：clear 前逐条核对六种状态均已在场（避免后续断言见证不足）
    for cid, expected in (
        (cid_prepared, "prepared"),
        (cid_granted, "granted"),
        (cid_consumed, "consumed"),
        (cid_revoked, "revoked"),
        (cid_expired, "expired"),
        (cid_stale, "stale"),
    ):
        item = consent_store.get(sid, cid)
        assert item is not None and item["status"] == expected, (
            f"前置校验：clear 前 {cid} 须为合法 {expected}，实际 {item!r}"
        )

    # 另一 sid 对照：其 prepared 条目不得被 clear 误删
    other = _Loopback(app)
    try:
        r_other = other.post(
            "/api/v1/ai/consent/preview",
            json_body={"messages": [{"role": "user", "content": "E18-other-sid"}]},
        )
        assert r_other.status_code == 201, r_other.text[:250]
        other_cid = r_other.json()["consent_id"]
        other_cookie = other.cookie_value

        assert client.delete("/api/v1/session").status_code == 200

        for cid in old_ids:
            assert consent_store.get(sid, cid) is None, (
                f"clear 须删除该 sid 全部 consent 条目（{cid} 仍可取回）"
            )
        items = consent_store._items  # §5 E18：逐条证明该 sid 无残留（含未列举条目）
        assert all(key[0] != sid for key in items), (
            f"clear 后该 sid 在 ConsentStore 不得残留任何条目，实际 "
            f"{[key for key in items if key[0] == sid]!r}"
        )
        for item in items.values():
            payload = json.dumps(item, ensure_ascii=False, default=str)
            assert _E18_MARKER not in payload, (
                "冻结消息/负载不得由 store 持有或可取回（messages/frozen_bytes）"
            )
        other_sid = app.state.registry.sid_from_cookie(other_cookie)
        assert other_sid and consent_store.get(other_sid, other_cid) is not None, (
            "另一 sid 的 consent 不得被 clear 误删"
        )
    finally:
        other.close()

    # registry 旧负载不可见（prepared/record）
    assert app.state.registry.get_prepared(sid, prepared_calc["prepared_id"]) is None
    assert app.state.registry.get_record(sid, record_id) is None
    assert client.get(f"/api/v1/records/{record_id}").status_code == 404


def test_002_r2_e19_invalidate_stales_pending_keeps_history(v2app) -> None:
    """§5 E19（review 8）：invalidate ≠ delete——**record 完成之后**才创建
    prepared/granted（避免 calc prepare 先行失效造成假阳性）→ invalidate 使
    pending/granted 转 stale（不可 grant/consume）、历史终态条目与 calc
    records 保留（不得把 clear 语义扩大到普通编辑）。"""
    app, client = v2app
    consent_store = app.state.consent_store

    prepared = _prepare_ok(client)
    confirmation_id = _confirm_ok(client, prepared)
    r_exec = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, r_exec.text[:250]
    record_id = r_exec.json()["record_id"]
    sid = _sid_of(app, client)  # record 已建立会话 cookie

    def _preview() -> str:
        r = client.post(
            "/api/v1/ai/consent/preview",
            json_body={"messages": [{"role": "user", "content": "E19-marker"}]},
        )
        assert r.status_code == 201, r.text[:250]
        return r.json()["consent_id"]

    cid_pending = _preview()
    cid_granted = _preview()
    r_grant = client.post("/api/v1/ai/consent", json_body={"consent_id": cid_granted})
    assert r_grant.status_code == 200, r_grant.text[:250]
    cid_revoked = _preview()
    assert consent_store.transition(sid, cid_revoked, "revoked") is not None
    cid_expired = _preview()
    assert consent_store.transition(sid, cid_expired, "expired") is not None
    for cid, expected in (
        (cid_pending, "prepared"),
        (cid_granted, "granted"),
        (cid_revoked, "revoked"),
        (cid_expired, "expired"),
    ):
        item = consent_store.get(sid, cid)
        assert item is not None and item["status"] == expected, (
            f"前置校验：invalidate 前 {cid} 须为 {expected}，实际 {item!r}"
        )

    epoch, rev = _runtime_versions(client)
    assert _invalidate(client, epoch, rev).status_code == 200

    for cid in (cid_pending, cid_granted):
        item = consent_store.get(sid, cid)
        assert item is not None and item["status"] == "stale", (
            f"invalidate 只失效 pending/granted（→stale，不删除），实际 {item!r}"
        )
    for cid, expected in ((cid_revoked, "revoked"), (cid_expired, "expired")):
        item = consent_store.get(sid, cid)
        assert item is not None and item["status"] == expected, (
            f"invalidate 不是 delete：历史终态条目须保留（{cid}），实际 {item!r}"
        )
    # stale token 不可 grant
    r_grant2 = client.post("/api/v1/ai/consent", json_body={"consent_id": cid_pending})
    assert r_grant2.status_code == 409 and _error_code(r_grant2.json()) == (
        "E_CONSENT_STALE"
    ), f"stale 不可 grant，实际 {r_grant2.status_code}：{r_grant2.text[:200]!r}"
    # calc records 保留
    assert client.get(f"/api/v1/records/{record_id}").status_code == 200


def _gate_consent_transition(
    monkeypatch, consent_store: Any, target_status: str,
    entered: threading.Event, release: threading.Event,
) -> None:
    """在 ConsentStore.transition 入口、原转换之前暂停（仅目标 status 首次）。"""
    original = consent_store.transition

    def _gated(sid: Any, consent_id: str, status: str):
        if status == target_status and not entered.is_set():
            entered.set()
            assert release.wait(timeout=10), "transition 屏障未放行（死锁看门狗）"
        return original(sid, consent_id, status)

    monkeypatch.setattr(consent_store, "transition", _gated)


@pytest.mark.parametrize("mutation", ["invalidate", "clear"])
def test_002_r2_e20_cached_grant_vs_invalidate_clear_no_resurrect(
    v2app, monkeypatch, mutation
) -> None:
    """§5 E20：grant 在 transition 入口（原转换前）暂停；invalidate/clear 先
    提交后放行 → grant 不得回成功、条目不得复活（终态/删除后转换保护）。"""
    app, client = v2app
    consent_store = app.state.consent_store

    r_preview = client.post(
        "/api/v1/ai/consent/preview",
        json_body={"messages": [{"role": "user", "content": "E20-marker"}]},
    )
    assert r_preview.status_code == 201, r_preview.text[:250]
    consent_id = r_preview.json()["consent_id"]

    entered = threading.Event()
    release = threading.Event()
    _gate_consent_transition(monkeypatch, consent_store, "granted", entered, release)
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = client.post(
            "/api/v1/ai/consent", json_body={"consent_id": consent_id}
        )

    thread = threading.Thread(target=_worker, name="hktax-test-e20")
    thread.start()
    editor = client.clone()
    try:
        assert entered.wait(timeout=10), "grant 未进入 transition（前置未满足）"
        if mutation == "invalidate":
            epoch, rev = _runtime_versions(editor)
            r_mut = _invalidate(editor, epoch, rev)
        else:
            r_mut = editor.delete("/api/v1/session")
        assert r_mut.status_code == 200, (
            f"{mutation} 先提交须 200，实际 {r_mut.status_code}"
        )
    finally:
        release.set()
        thread.join(timeout=15)
        editor.close()
    assert "response" in outcome
    r = outcome["response"]
    # review 8（bounded plan:90/Annex C:166 冻结映射）：invalidate 保留条目 →
    # 非法终态转换 409 E_CONSENT_STALE；clear 删除条目 → 无条目 403
    # E_CONSENT_REQUIRED。不做 403/409 任意并集。
    if mutation == "invalidate":
        assert r.status_code == 409, (
            f"invalidate 后 grant 非法终态转换须 409，实际 {r.status_code}："
            f"{r.text[:200]!r}"
        )
        assert _error_code(r.json()) == "E_CONSENT_STALE", (
            f"保留条目的非法终态转换须 E_CONSENT_STALE，实际 "
            f"{_error_code(r.json())!r}"
        )
    else:
        assert r.status_code == 403, (
            f"clear 后 grant 无条目须 403，实际 {r.status_code}：{r.text[:200]!r}"
        )
        assert _error_code(r.json()) == "E_CONSENT_REQUIRED", (
            f"删除后的缺失条目须 E_CONSENT_REQUIRED，实际 {_error_code(r.json())!r}"
        )
    item = consent_store.get(_sid_of(app, client), consent_id)
    assert item is None or item["status"] != "granted", (
        f"条目不得复活为 granted，实际 {item!r}"
    )


class _CountingOutbound:
    """拒绝式出站假件：计数并返回最小合法 wire 响应（被调用即测试失败面）。"""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, endpoint: str, api_key: str, frozen: bytes) -> dict:
        self.calls += 1
        return {
            "status_code": 200,
            "body": b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}',
        }


def test_002_r2_e21_cached_chat_vs_clear_no_dispatch(v2app, monkeypatch) -> None:
    """§5 E21：chat 在 consumed transition 入口暂停；clear 先提交后放行 →
    拒绝转换、拒绝式 fake outbound 调用数为 0（不启动真实模型）。"""
    app, client = v2app
    consent_store = app.state.consent_store
    monkeypatch.setenv("HKTAX_MODEL_ENDPOINT", "https://model.invalid.test/ep")
    monkeypatch.setenv("HKTAX_MODEL_API_KEY", "test-key-not-real")
    counting = _CountingOutbound()
    real_outbound = app.state.model_outbound
    app.state.model_outbound = counting
    try:
        messages = [{"role": "user", "content": "E21-marker"}]
        r_preview = client.post(
            "/api/v1/ai/consent/preview",
            json_body={"messages": messages},
        )
        assert r_preview.status_code == 201, r_preview.text[:250]
        consent_id = r_preview.json()["consent_id"]
        r_grant = client.post(
            "/api/v1/ai/consent", json_body={"consent_id": consent_id}
        )
        assert r_grant.status_code == 200, r_grant.text[:250]

        entered = threading.Event()
        release = threading.Event()
        _gate_consent_transition(
            monkeypatch, consent_store, "consumed", entered, release
        )
        outcome: dict[str, Any] = {}

        def _worker() -> None:
            outcome["response"] = client.post(
                "/api/v1/ai/chat",
                json_body={"consent_id": consent_id, "messages": messages},
            )

        thread = threading.Thread(target=_worker, name="hktax-test-e21")
        thread.start()
        editor = client.clone()
        try:
            assert entered.wait(timeout=10), "chat 未进入 consumed transition"
            r_clear = editor.delete("/api/v1/session")
            assert r_clear.status_code == 200, (
                f"chat 在途 clear 须 200，实际 {r_clear.status_code}"
            )
        finally:
            release.set()
            thread.join(timeout=15)
            editor.close()
        assert "response" in outcome
        r = outcome["response"]
        assert counting.calls == 0, (
            f"clear 已删除同意后不得外发（拒绝式 outbound 调用数须 0），"
            f"实际 {counting.calls}"
        )
        # review 8：clear 删除条目 → chat 无条目 403 E_CONSENT_REQUIRED
        assert r.status_code == 403, (
            f"clear 后 chat 无条目须 403，实际 {r.status_code}：{r.text[:200]!r}"
        )
        assert _error_code(r.json()) == "E_CONSENT_REQUIRED"
    finally:
        app.state.model_outbound = real_outbound


def test_002_r2_e22_clear_deletes_within_same_operation(v2app, monkeypatch) -> None:
    """§5 E22：clear 的删除在同一操作内完成——包装 delete_session 暂停期间
    clear 尚未返回 ACK；删除完成后 clear 才返回；返回后 record/consent/冻结
    消息均不可取回，旧 transition 无法再创造该 ID。

    【接口型 Red】ConsentStore 缺 delete_session（plan §3 冻结接口）→
    首个断言即失败，不冒充行为 Red。"""
    app, client = v2app
    consent_store = app.state.consent_store

    prepared = _prepare_ok(client)
    confirmation_id = _confirm_ok(client, prepared)
    r_exec = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, r_exec.text[:250]
    record_id = r_exec.json()["record_id"]
    r_preview = client.post(
        "/api/v1/ai/consent/preview",
        json_body={"messages": [{"role": "user", "content": "E22-marker"}]},
    )
    assert r_preview.status_code == 201, r_preview.text[:250]
    consent_id = r_preview.json()["consent_id"]
    sid = _sid_of(app, client)

    delete_session = getattr(consent_store, "delete_session", None)
    assert delete_session is not None, (
        "INTERFACE_ONLY：缺 ConsentStore.delete_session(sid)（plan §3 冻结接口；"
        "clear 真删除；接口缺失型 Red，不冒充行为 Red）"
    )

    entered = threading.Event()
    release = threading.Event()
    original_delete = consent_store.delete_session

    def _gated(*args: Any, **kwargs: Any):
        entered.set()
        assert release.wait(timeout=10), "E22 屏障未放行（死锁看门狗）"
        return original_delete(*args, **kwargs)

    monkeypatch.setattr(consent_store, "delete_session", _gated)
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = client.delete("/api/v1/session")

    thread = threading.Thread(target=_worker, name="hktax-test-e22")
    thread.start()
    try:
        assert entered.wait(timeout=10), "clear 未进入 delete_session"
        assert "response" not in outcome, (
            "delete_session 完成前 clear 不得返回 ACK（同操作完成点）"
        )
    finally:
        release.set()
        thread.join(timeout=15)
    assert "response" in outcome and outcome["response"].status_code == 200, (
        "删除完成后 clear 须返回 200 ACK"
    )

    assert consent_store.get(sid, consent_id) is None, "冻结消息不可取回"
    assert app.state.registry.get_record(sid, record_id) is None, "record 不可取回"
    assert client.get(f"/api/v1/records/{record_id}").status_code == 404
    # review 8：缺失条目上的 transition 以合法 AppError 拒绝（E_CONSENT_REQUIRED
    # 403）或返回 None 均可，但不得复活为 granted。
    from app.core.errors import AppError

    try:
        revived = consent_store.transition(sid, consent_id, "granted")
    except AppError as exc:
        assert exc.code == "E_CONSENT_REQUIRED", (
            f"缺失条目的合法拒绝须 E_CONSENT_REQUIRED，实际 {exc.code!r}"
        )
        revived = None
    assert revived is None or revived.get("status") != "granted", (
        f"旧 transition 无法再创造该 ID（不得复活），实际 {revived!r}"
    )
