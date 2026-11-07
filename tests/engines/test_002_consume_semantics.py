# -*- coding: utf-8 -*-
"""REQ-11 原子消费/执行快照/完成语义（002 P1 Red；Annex 005 §12.3）。

REQ: REQ-11（S002 §8/§9.4；PRD AC-11）。G0 修正后共 14 个命名节点
（006 §5.5 原六节点＋G0 新增八节点）：
  - test_002_consume_snapshot_survives_edit_before_compute
  - test_002_execute_never_refetches_prepared_after_consume
  - test_002_consume_clear_complete_discards_old_record_and_link
  - test_002_completion_atomic_idempotent_and_conflict_safe
  - test_002_wrong_target_and_guard_failure_do_not_consume
  - test_002_consumed_reuse_returns_only_saved_record_link
  - test_002_missing_active_bands_rejected_before_capability（§四.1/B1 缺带负例）
  - test_002_guard_missing_malformed_unknown_current_blocks（§五.5/R1③）
  - test_002_next_only_unverifiable_suppresses_next_only（§五.5/R1③＋五税无 next）
  - test_002_completion_backend_identity_frozen_and_terminal（§五.1/Bu1）
  - test_002_execution_snapshot_frozen_against_mutation（§五.1/Bu1③）
  - test_002_rules_change_during_compute_stores_historical（§五.5/§12.3 交错表）
  - test_002_completion_backend_concurrent_single_registration（§五.1 缺口 1
    「连续/并发」之并发半边：Barrier 确定性并发完成）
  - test_002_guard_change_during_compute_stores_historical（§五.5 缺口 5
    「消费后规则/guard 变化仅 historical」之 guard 半边）

规格锚点:
  - S002 §4.2「原子消费/完成（Annex 005 §12.3）」：consume_for_execution 在
    registry→store 锁序内冻结 ExecutionContext（canonical bytes/binding/
    bundle/AvailabilityContext）后才标 consumed；消费后禁止再 get_prepared/
    读 current bundle；引擎用独立工作副本；complete_execution 原子保存
    record＋consumed 链接；编辑后可存历史 record（editor_result=current 仅当
    当前 revision/binding/guard 仍匹配 context，否则 historical）；
    clear/epoch 变化 → discarded（HTTP 409 E_CONFIRMATION_STALE、不返回旧
    core/税额、无 record/链接写入、不得 ensure_session）；同 execution 幂等/
    冲突 409 E_CONFIRMATION_CONSUMED；错目标 409 E_CONFIRMATION_TARGET_
    MISMATCH 不消费；规则阻断 E_RULES_STATE_UNVERIFIABLE 不消费。
  - Annex 005 §12.3 交错表＋§12.1（invalidate 删除旧 prepared）＋§13。
  - 006 §2（确定性屏障：事件/BARRIER，不靠 sleep 概率）。屏障实现＝临时
    替换 app.api.routes.run_engine（HTTP execute 的实际引擎派发入口，
    Annex 005 §2.1 表列 `routes.py:257–275`→`records.run_engine`），在引擎
    被调用（＝已过消费临界区）时先通知再等待放行。
  - Annex 005 §5.3（v2 最小信封＋计划断言：current final 940、partial、
    next/balance=null；canonical input 不补 inactive 键）。

期望值来源（禁从生产代码反推）:
  - 3940/3000/940：PAM39 Q1（T2 台账 1.13）＋Annex 005 §14 规范示例
    （合成 Q1 未执行，数值同为冻结计划锚）。
  - 其余为结构/状态/协议断言，无金额期望。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - execute 以 §12.3 快照执行：消费后在计算期间发生 invalidate（revision++、
    删除旧 prepared）→ 仍按快照存 record（HTTP 200＋record 可读＋金额来自
    快照事实，禁止 facts={} 回退或重读 prepared）；record input 与确认的
    canonical v2 输入一致。
  - 计算期间 clear（epoch 边界）→ 409 E_CONFIRMATION_STALE、无 amounts/
    record_id、不写 record。
  - 完成：一次保存 record＋链接；同 confirmation 重放 → 409
    E_CONFIRMATION_CONSUMED＋仅附已保存 record 链接（record 原样不变）；
    clear 后旧 token → 409 且不附旧链接。
  - 错目标/守卫阻断（update_scheduler.availability blocked 接缝）不消费：
    同一确认随后在正确目标/守卫恢复后仍可 200。
  - 本文件对 app.* 延迟导入；Red 失败原因＝缺失 v2 行为（旧校验拒绝
    2.0.0、缺原子消费/快照语义），非收集期失败。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import threading
import time
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


# 注：本文件 _fixed_v2_seed 已补全发行五税固定 profiles（admission 内容锚）；
# 定稿 seed canonical hash 见 tests/unit/test_002_store_compat.py::_FIXED_V2_SEED_HASH。


def _fixed_v2_seed() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    profile = _part5_profile()
    profile["semantic_digest"] = "sha256:" + hashlib.sha256(
        _canonical_bytes(_part5_profile())
    ).hexdigest()
    legacy_profiles = [
        copy.deepcopy(item)
        for item in INITIAL_BUNDLE_CONTENT["frozen_semantics"]["profiles"]
        if item.get("profile_id") != "part5_shared_v2"
    ]
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
        "frozen_semantics": {"profiles": [*legacy_profiles, profile]},
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
                # 附表 2/1（T1，台账 1.17）：薪俸累进带＋两级标准径（完整固定束）
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
# 回环客户端（可克隆同会话第二客户端；确定性屏障用）
# ---------------------------------------------------------------------------


class _Loopback:
    def __init__(self, app: Any, cookie_value: str | None = None) -> None:
        from app.config import SESSION_COOKIE_NAME

        self._cookie_name = SESSION_COOKIE_NAME
        self._app = app
        self._client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=_BASE_ORIGIN
        )
        self._csrf: str | None = None
        self.cookie_value = cookie_value
        if cookie_value:
            self._client.cookies.set(SESSION_COOKIE_NAME, cookie_value)

    def _token(self) -> str:
        if self._csrf is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf = token
            if self.cookie_value is None:
                self.cookie_value = r.cookies.get(self._cookie_name)
        return self._csrf

    def clone(self) -> "_Loopback":
        """同一会话的第二客户端（跨标签页/另一 editor 语义）。"""
        self._token()
        return _Loopback(self._app, cookie_value=self.cookie_value)

    def get(self, path: str):
        return self._client.get(path)

    def post(self, path: str, *, json_body: dict | None = None, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = _BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json_body, headers=h)

    def delete(self, path: str):
        return self._client.delete(
            path, headers={"Origin": _BASE_ORIGIN, "X-CSRF-Token": self._token()}
        )

    def close(self) -> None:
        self._client.close()


class _Harness:
    def __init__(self, app: Any, client: _Loopback) -> None:
        self.app = app
        self.client = client

    def runtime_versions(self) -> tuple[int, int]:
        r = self.client.get("/api/v1/meta/version")
        assert r.status_code == 200, r.text
        payload = r.json()
        assert "session_epoch" in payload and "input_revision" in payload, (
            "S002 §4.1：meta/version 须附运行时 session_epoch/input_revision，"
            f"实际：{payload!r}"
        )
        return int(payload["session_epoch"]), int(payload["input_revision"])

    def fresh_if_match(self) -> dict[str, str]:
        epoch, revision = self.runtime_versions()
        # S002 §4.1 版本表 wire 字面量：`If-Match:"edit-<epoch>-<revision>"`
        # （带引号；RFC 9110 entity-tag 语法）。单一语法，不做双兼容。
        return {"If-Match": f'"edit-{epoch}-{revision}"'}

    def prepare_ok(self) -> dict:
        r = self.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=self.fresh_if_match()
        )
        assert r.status_code == 201, (
            f"prepare 须 201，实际 {r.status_code}：{r.text[:300]!r}"
        )
        return r.json()

    def confirm_ok(self, prepared: dict) -> str:
        r = self.client.post(
            _CONFIRM,
            json_body={
                "prepared_id": prepared["prepared_id"],
                "canonical_input_hash": prepared["input_hash"],
                "expected_binding": prepared["expected_binding"],
                "acknowledge": True,
            },
        )
        assert r.status_code == 200, (
            f"confirm 须 200，实际 {r.status_code}：{r.text[:300]!r}"
        )
        return r.json()["confirmation_id"]

    def invalidate_now(self, editor: _Loopback):
        epoch, revision = self.runtime_versions()
        return editor.post(
            _INVALIDATE,
            json_body={
                "expected_session_epoch": epoch,
                "expected_input_revision": revision,
            },
        )


# ===========================================================================
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
def v2harness(tmp_path, monkeypatch, _red_safety_scope):
    """固定 v2 测试 seed 注入后的 app＋同会话可克隆客户端（P1 依赖契约）。

    显式依赖文件本地 safety scope（PQA-01）：安装先于 main 导入，检查/复原在
    scope teardown 独立执行（client.close 先于检查）。"""
    db = tmp_path / "rules.db"
    from app.rules.store import RuleStore

    RuleStore(db).publish(_fixed_v2_seed(), evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    harness = _Harness(app, _Loopback(app))
    try:
        yield harness
    finally:
        harness.client.close()


def _patch_engine_barrier(monkeypatch, started: threading.Event, released: threading.Event) -> None:
    """确定性屏障：替换 HTTP execute 的引擎派发入口（Annex 005 §2.1 表）。

    引擎被调用＝已过消费临界区（Annex 005 §12.3：释放锁后计算）。
    """
    import app.api.routes as routes_module

    original = getattr(routes_module, "run_engine", None)
    assert original is not None, (
        "被测 seam 缺失：app.api.routes.run_engine（HTTP execute 引擎派发入口，"
        "Annex 005 §2.1 表）"
    )

    def _barrier(*args: Any, **kwargs: Any):
        started.set()
        assert released.wait(timeout=10), "引擎屏障未在 10s 内放行（测试屏障死锁）"
        return original(*args, **kwargs)

    monkeypatch.setattr(routes_module, "run_engine", _barrier)


def _execute_with_barrier(
    monkeypatch, harness: _Harness, confirmation_id: str, mutate_during_compute
):
    """在引擎计算期间执行 mutate_during_compute（确定性事件屏障）。"""
    started = threading.Event()
    released = threading.Event()
    _patch_engine_barrier(monkeypatch, started, released)
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )

    worker = threading.Thread(target=_worker, name="hktax-test-execute")
    worker.start()
    try:
        assert started.wait(timeout=10), "引擎未被调用（消费/执行未到达计算阶段）"
        mutate_during_compute()
    finally:
        released.set()
        worker.join(timeout=15)
    assert "response" in outcome, "execute 工作线程未返回响应"
    return outcome["response"]


def _error_code(payload: dict) -> str | None:
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状 §4.3，实际：{payload!r}"
    return err.get("code")


# ---------------------------------------------------------------------------
# 节点 1：消费快照在计算前编辑（revision++）后仍按快照存 record
# ---------------------------------------------------------------------------


def test_002_consume_snapshot_survives_edit_before_compute(v2harness, monkeypatch) -> None:
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    editor = v2harness.client.clone()

    def _edit_during_compute():
        r_inv = v2harness.invalidate_now(editor)
        assert r_inv.status_code == 200, (
            f"计算期间 invalidate 须 200（编辑→失效→ACK），实际 "
            f"{r_inv.status_code}：{r_inv.text[:200]!r}"
        )

    r = _execute_with_barrier(monkeypatch, v2harness, confirmation_id, _edit_during_compute)
    assert r.status_code == 200, (
        f"消费后编辑 revision++ → 仍须按快照存历史 record（HTTP 200），实际 "
        f"{r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    record_id = payload.get("record_id")
    assert record_id, "200 须携带 record_id（历史 record 保存）"
    amounts = payload.get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", (
        f"金额须来自快照事实（§5.3：final 940），实际 amounts：{amounts!r}"
    )
    r_record = v2harness.client.get(f"/api/v1/records/{record_id}")
    assert r_record.status_code == 200, "历史 record 须可读（编辑不撤回已存记录）"


# ---------------------------------------------------------------------------
# 节点 2：消费后不得再读 prepared/current（invalidate 删除 prepared 后仍正确）
# ---------------------------------------------------------------------------


def test_002_execute_never_refetches_prepared_after_consume(v2harness, monkeypatch) -> None:
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    editor = v2harness.client.clone()

    def _drop_prepared_during_compute():
        r_inv = v2harness.invalidate_now(editor)
        assert r_inv.status_code == 200, (
            f"计算期间 invalidate（删除旧 prepared）须 200，实际 {r_inv.status_code}"
        )

    r = _execute_with_barrier(
        monkeypatch, v2harness, confirmation_id, _drop_prepared_during_compute
    )
    assert r.status_code == 200, (
        f"消费后禁止重读 prepared：删除 prepared 后仍须按快照完成，实际 "
        f"{r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    amounts = payload.get("amounts") or {}
    assert amounts.get("before_reduction") == "3940", amounts
    assert amounts.get("reduction") == "3000", amounts
    assert amounts.get("final_after_reduction") == "940", (
        f"禁止 facts={{}} 回退：金额须与确认事实一致（§5.3），实际 {amounts!r}"
    )
    # record 输入取快照，不取引擎修改后对象，也不得回退为空
    r_record = v2harness.client.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200, r_record.text
    record_input = r_record.json()["record"]["input"]
    assert record_input == _v2_salary_body()["input"], (
        "record 输入须为确认时的 canonical v2 输入（Annex 005 §12.3：record "
        "输入/binding 取快照）"
    )


# ---------------------------------------------------------------------------
# 节点 3：计算期间 clear → discarded（409、无税额、无 record/链接）
# ---------------------------------------------------------------------------


def test_002_consume_clear_complete_discards_old_record_and_link(
    v2harness, monkeypatch
) -> None:
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    editor = v2harness.client.clone()

    def _clear_during_compute():
        r_clear = editor.delete("/api/v1/session")
        assert r_clear.status_code == 200, (
            f"计算期间 clear 须 200（epoch 边界），实际 {r_clear.status_code}"
        )

    r = _execute_with_barrier(
        monkeypatch, v2harness, confirmation_id, _clear_during_compute
    )
    assert r.status_code == 409, (
        f"clear/epoch 变化 → Receipt=discarded → HTTP 409 E_CONFIRMATION_STALE"
        f"（Annex 005 §12.3），实际 {r.status_code}"
    )
    payload = r.json()
    assert _error_code(payload) == "E_CONFIRMATION_STALE"
    assert "amounts" not in payload, "discarded 不得返回旧 core/税额"
    assert "record_id" not in payload, "discarded 不得写 record/链接"


# ---------------------------------------------------------------------------
# 节点 4：完成原子/幂等/冲突安全（record 只写一次、原样不变）
# ---------------------------------------------------------------------------


def test_002_completion_atomic_idempotent_and_conflict_safe(v2harness) -> None:
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)

    r_first = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_first.status_code == 200, (
        f"execute 须 200，实际 {r_first.status_code}：{r_first.text[:300]!r}"
    )
    record_id = r_first.json()["record_id"]
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200, r_download.text
    core_hash_before = r_download.json()["core_hash"]

    r_again = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_again.status_code == 409, "同 confirmation 重放须 409"
    assert _error_code(r_again.json()) == "E_CONFIRMATION_CONSUMED"
    assert r_again.json().get("record_id") == record_id, (
        "重放只附已保存 record 链接（一次登记，不重复写）"
    )
    assert "amounts" not in r_again.json()

    r_download_after = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download_after.status_code == 200
    assert r_download_after.json()["core_hash"] == core_hash_before, (
        "冲突/重放不得改动原 record（CompletionReceipt 幂等）"
    )


# ---------------------------------------------------------------------------
# 节点 5：错目标/守卫阻断不消费
# ---------------------------------------------------------------------------


class _BlockedScheduler:
    """守卫接缝假件：availability 一律 blocked（既有 app.state 接缝）。"""

    def availability(self, period: str) -> dict:
        return {"mode": "blocked", "notice": "测试注入：该期间已知未核实变更"}


def test_002_wrong_target_and_guard_failure_do_not_consume(v2harness, monkeypatch) -> None:
    # —— (a) 错目标 → 409 且不消费；正确目标随后 200 ——
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r_wrong = v2harness.client.post(
        _PROFITS_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_wrong.status_code == 409, (
        f"错目标须 409 E_CONFIRMATION_TARGET_MISMATCH（不消费），实际 "
        f"{r_wrong.status_code}"
    )
    assert _error_code(r_wrong.json()) == "E_CONFIRMATION_TARGET_MISMATCH"
    r_right = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_right.status_code == 200, (
        f"错目标不得消费：正确目标随后须 200，实际 {r_right.status_code}："
        f"{r_right.text[:300]!r}"
    )

    # —— (b) 守卫阻断 → 422 E_RULES_STATE_UNVERIFIABLE 不消费；恢复后同一确认可执行 ——
    prepared2 = v2harness.prepare_ok()
    confirmation2 = v2harness.confirm_ok(prepared2)
    real_scheduler = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _BlockedScheduler()
    try:
        r_blocked = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation2}
        )
        assert r_blocked.status_code == 422, (
            f"守卫阻断须 422 E_RULES_STATE_UNVERIFIABLE（不消费），实际 "
            f"{r_blocked.status_code}：{r_blocked.text[:200]!r}"
        )
        assert _error_code(r_blocked.json()) == "E_RULES_STATE_UNVERIFIABLE"
    finally:
        v2harness.app.state.update_scheduler = real_scheduler
    r_unblocked = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation2}
    )
    assert r_unblocked.status_code == 200, (
        f"守卫恢复后同一确认须仍可执行（未被消费），实际 "
        f"{r_unblocked.status_code}：{r_unblocked.text[:300]!r}"
    )


# ---------------------------------------------------------------------------
# 节点 6：已消费重放只返回已保存链接；clear 后旧 token 不返回旧链接
# ---------------------------------------------------------------------------


def test_002_consumed_reuse_returns_only_saved_record_link(v2harness) -> None:
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r_exec = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, (
        f"execute 须 200，实际 {r_exec.status_code}：{r_exec.text[:300]!r}"
    )
    record_id = r_exec.json()["record_id"]

    r_reuse = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_reuse.status_code == 409, "单次消费"
    assert _error_code(r_reuse.json()) == "E_CONFIRMATION_CONSUMED"
    assert r_reuse.json().get("record_id") == record_id, "仅同会话已完成关联附链接"
    assert "amounts" not in r_reuse.json(), "重放不重算、不返回税额"

    # clear 后旧 token → 409 STALE 且不返回旧链接
    r_clear = v2harness.client.delete("/api/v1/session")
    assert r_clear.status_code == 200, r_clear.text
    r_after_clear = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_after_clear.status_code == 409, (
        f"clear 后旧 token 须 409，实际 {r_after_clear.status_code}"
    )
    assert _error_code(r_after_clear.json()) == "E_CONFIRMATION_STALE"
    assert "record_id" not in r_after_clear.json(), (
        "clear 后旧 token 不返回旧链接（Annex 005 §12.3）"
    )
    assert "amounts" not in r_after_clear.json()


# ===========================================================================
# 新 Red（复核报告 §四.1/§五.1/§五.4/§五.5：B1 缺带负例／R1 准入／
# completion 后端／冻结快照／规则变化 historical）
# ===========================================================================


def _seeded_harness(tmp_path, monkeypatch, seed: dict) -> _Harness:
    """以给定 seed 构造 harness（缺带／篡改 seed 负例用）。"""
    from app.rules.store import RuleStore

    db = tmp_path / "rules.db"
    RuleStore(db).publish(seed, evidence_refs=[])
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    return _Harness(app, _Loopback(app))


def test_002_missing_active_bands_rejected_before_capability(tmp_path, monkeypatch) -> None:
    """缺带负例（复核报告 §四.1/B1）：seed 缺活动税率带 → 拒算且不签发能力，
    不得由发行模块补数后照算（records 派发回填＝B1 缺陷；探针：删带仍算 940）。
    期望值 3940/3000/940 保留在完整 seed 各节点（附表 1/2，T1）。"""
    import copy as _copy

    seed = _copy.deepcopy(_fixed_v2_seed())
    del seed["data"]["salaries"]["progressive_bands"]
    del seed["data"]["salaries"]["standard_rate_bands"]
    harness = _seeded_harness(tmp_path, monkeypatch, seed)
    try:
        r = harness.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=harness.fresh_if_match()
        )
        assert r.status_code == 422, (
            f"缺活动税率带须 E_RULES_STATE_UNVERIFIABLE 拒算（B1/R1④：不补"
            f"发行数据），实际 {r.status_code}：{r.text[:250]!r}"
        )
        assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE"
        assert "prepared_id" not in r.json(), "缺活动规则不得签发能力（消费前拒绝）"
    finally:
        harness.client.close()


class _NoVerdictScheduler:
    """guard 缺席：availability 返回 None（R1 探针：缺席默认放行）。"""

    def availability(self, period: str):
        return None


class _JunkVerdictScheduler:
    """guard 畸形：availability 返回非对象（R1 探针：非对象默认放行）。"""

    def availability(self, period: str):
        return "junk"


class _WeirdModeScheduler:
    """guard 未知 mode（R1③：公共 availability 只接受可信 ok/trusted_offline）。"""

    def availability(self, period: str):
        return {"mode": "celestial", "notice": "测试注入：未知 mode"}


def test_002_guard_missing_malformed_unknown_current_blocks(v2harness) -> None:
    """本年 guard 缺席/畸形/未知 mode → 拒绝（缺 context 不等于允许；005 §2
    「任何缺失一律不回退本年度规则、不补默认」；R1③：current 无条件
    available 为缺陷）。"""
    for fake in (_NoVerdictScheduler(), _JunkVerdictScheduler(), _WeirdModeScheduler()):
        real = v2harness.app.state.update_scheduler
        v2harness.app.state.update_scheduler = fake
        try:
            r = v2harness.client.post(
                _PREPARE,
                json_body=_v2_salary_body(),
                headers=v2harness.fresh_if_match(),
            )
            assert r.status_code == 422, (
                f"{type(fake).__name__}：本年 guard 不可核验须 422 "
                f"E_RULES_STATE_UNVERIFIABLE（无默认放行），实际 "
                f"{r.status_code}：{r.text[:200]!r}"
            )
            assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE"
            assert "prepared_id" not in r.json()
        finally:
            v2harness.app.state.update_scheduler = real


class _NextOnlyWeirdScheduler:
    """仅下一年（2026_27）guard 不可核验；本年 ok。"""

    def availability(self, period: str):
        if period == "2026_27":
            return {"mode": "celestial"}
        return {"mode": "ok"}


def test_002_next_only_unverifiable_suppresses_next_only(v2harness) -> None:
    """仅 next 不可核验 → 只抑制 next（本年 final 保留、next=null、partial；
    005 §2「next blocked/missing_rules → 仅 next，本年 final 原样保留」）；
    五税不受未来年 guard 影响（§2.2：五税无 next 概念）。"""
    import copy as _copy

    body = _v2_salary_body()
    next_year = _copy.deepcopy(body["input"]["years"]["2025_26"])
    # P1 r2 定点纠错（engine 报告 §三，交协议 Red owner）：next 婚姻日期局部
    # 换 2026_27 目标年 [2026-04-01, 2027-04-01)——原 2025-04-01..2026-04-01
    # 越出目标年窗口，next 抑制可能因 TF 域错误而非 guard 不可核验触发
    # （误译遮蔽）。保留 guard 抑制与当前年 940 断言；不改共享 _v2_salary_body。
    next_year["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    # 显式目标事实与假设同额（005 §11：以显式事实为主，假设冗余合法）
    body["input"]["years"]["2026_27"] = next_year
    body["input"]["assumptions"] = [
        {
            "assumption_id": "a1",
            "kind": "same_income",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        },
        {
            "assumption_id": "a2",
            "kind": "same_mpf",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        },
    ]

    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _NextOnlyWeirdScheduler()
    try:
        r = v2harness.client.post(
            _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 201, (
            f"仅 next 不可核验不得阻断本年（005 §2），实际 {r.status_code}："
            f"{r.text[:250]!r}"
        )
        confirmation_id = v2harness.confirm_ok(r.json())
        r_exec = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_exec.status_code == 200, (
            f"仅 next 不可核验本年仍须可执行，实际 {r_exec.status_code}："
            f"{r_exec.text[:250]!r}"
        )
        payload = r_exec.json()
        amounts = payload.get("amounts") or {}
        assert amounts.get("final_after_reduction") == "940", (
            f"本年 final 须原样保留（940），实际 {amounts!r}"
        )
        assert amounts.get("next_provisional") is None, (
            f"next 不可核验 → next_provisional=null（不得默认放行计算），"
            f"实际 {amounts.get('next_provisional')!r}"
        )
        assert payload.get("status") == "partial"

        # 五税对照：profits 2025_26 prepare 不受 2026_27 guard 影响（无 next 概念）
        r_profits = v2harness.client.post(
            _PREPARE,
            json_body={
                "tax_type": "profits_tax",
                "schema_version": "1.0.0",
                "input": {
                    "year_of_assessment": "2025_26",
                    "entity_kind": "sole_proprietorship",
                    "assessable_profit": "600000",
                },
            },
            headers=v2harness.fresh_if_match(),
        )
        assert r_profits.status_code == 201, (
            f"五税 current-period 计算不受未来年 guard 影响（§2.2），实际 "
            f"{r_profits.status_code}：{r_profits.text[:200]!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real


# —— completion 后端（复核报告 §五.1/Bu1：005 §12.3 冻结名 consume_for_execution/
# complete_execution/fail_execution；经 app.state.registry 直驱，HTTP 不可
# 观察同 execution 幂等/冲突窗口）——


def _core_like(tax_type: str, facts: dict, binding: dict) -> dict:
    """最小 11 字段 core（C2.8；信封外字段不入哈希对象）。"""
    return {
        "tax_type": tax_type,
        "input": facts,
        "binding": dict(binding),
        "status": "partial",
        "amounts": {
            "before_reduction": "3940",
            "reduction": "3000",
            "final_after_reduction": "940",
            "provisional_paid": "0",
            "next_provisional": None,
            "balance": None,
        },
        "blocked_reason": None,
        "steps": [],
        "evidence_refs": [],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }


def _field(obj: Any, name: str, *default: Any) -> Any:
    """表示无关字段读取（plan §3：context/receipt 类型表示由实施者选择，
    测试不绑定 dict/dataclass——Mapping 与属性两种载体均可读）。"""
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


def _kv(obj: Any, key: str) -> Any:
    """表示无关嵌套键读取（binding/availability_context 等嵌套载体）。"""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return _field(obj, key, None)


def _logical_keys(obj: Any) -> tuple | None:
    """PQA-05：仅对已支持/明确声明的表示枚举逻辑字段全集：
    - Mapping → 全部 keys；
    - 实际 dataclass → dataclasses.fields(instance) 声明的数据字段
      （方法/ClassVar/InitVar 不计）；
    - 真实 namedtuple（tuple 且以标准 `_fields` 元组声明）→ 其字段；
      普通类即使带字符串 `_fields` 属性也不是 namedtuple（拒绝鸭子类型投影）。
    其它（含混有内部缓存/opaque 表示、无明确逻辑投影）→ None（held：不猜
    下划线过滤、不发明 keys/to_dict/schema_fields 协议、不要求 dict）。"""
    import dataclasses

    if isinstance(obj, Mapping):
        return tuple(obj.keys())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return tuple(field.name for field in dataclasses.fields(obj))
    declared = getattr(obj, "_fields", None)
    if (
        isinstance(obj, tuple)
        and isinstance(declared, tuple)
        and all(isinstance(name, str) for name in declared)
    ):
        return declared
    return None


def _assert_closed_keys(obj: Any, required: tuple) -> None:
    """逻辑封闭键断言（PQA-05）：在可枚举表示上精确比较全部逻辑字段
    （任意额外逻辑字段即使值为 None 也拒绝）；opaque/混合表示显式 HELD，
    不以 required/forbidden 兜底、不冻结 entry 表示。"""
    logical = _logical_keys(obj)
    if logical is None:
        raise AssertionError(
            "INTERFACE_HELD：无法枚举该表示的逻辑字段全集（PQA-05；不要求 "
            f"dict、不猜内部缓存/方法为字段）：{type(obj)!r}"
        )
    keys = set(logical)
    assert keys == set(required), (
        f"封闭逻辑字段须精确为 {sorted(required)!r}，实际 {sorted(keys)!r}"
    )


def _consume_direct(harness: _Harness, prepared: dict) -> tuple:
    """HTTP prepare/confirm 后直驱 registry.consume_for_execution。

    P1 r2 迁移（plan §3 冻结签名，protocol 咨询 §4 调用方表）：显式传入
    target/store/guard——store=app-owned 同实例 RuleStore、guard=当前真实
    scheduler（本地只读 availability），不以 availability=None 绕过公共准入，
    不保留旧签名容错。返回 (sid, context, facts, binding)；facts/binding 取
    自 prepared（消费前合法读取），供 core 构造复用。
    """
    confirmation_id = harness.confirm_ok(prepared)
    registry = harness.app.state.registry
    sid = registry.sid_from_cookie(harness.client.cookie_value)
    assert sid, "会话 cookie 未能解析出 sid"
    stored_prepared = registry.get_prepared(sid, prepared["prepared_id"])
    assert stored_prepared is not None
    context = registry.consume_for_execution(
        sid,
        confirmation_id,
        target=_SALARIES_EXECUTE,
        store=harness.app.state.store,
        guard=harness.app.state.update_scheduler,
    )
    return sid, context, stored_prepared["input"], prepared["expected_binding"]


def _complete_direct(harness: _Harness, context: Any, core: dict) -> dict:
    """直驱 registry.complete_execution（plan §3 冻结签名：context＋显式
    store/guard 执行环境依赖；不进入 context 冻结字段）。"""
    return harness.app.state.registry.complete_execution(
        context,
        core,
        store=harness.app.state.store,
        guard=harness.app.state.update_scheduler,
    )


def _complete_tolerant(harness: _Harness, context: Any, core: dict) -> dict:
    """完成调用：外层码未冻结（复核报告 需裁定 3）→ 容错捕获 AppError，
    返回 outcome（rejected＝以异常拒绝）。语义断言与原版一致（迁移签名）。"""
    from app.core.errors import AppError

    try:
        return _complete_direct(harness, context, core)
    except AppError:
        return {"outcome": "rejected"}


def test_002_completion_backend_identity_frozen_and_terminal(v2harness) -> None:
    """completion 后端（Bu1/005 §12.3；P1 r2 已迁移 plan §3 冻结签名）：
    - 错 tax/binding/input core → 不保存（正确 core 随后仍可首存）；
    - 同 execution 同 core → already_stored＋同 record_id（一次登记）；
    - 异 core → 409 E_CONFIRMATION_CONSUMED 且原记录字节不变；
    - failed 后完成不得补写；clear 后完成 → discarded（无 record/链接）。"""
    from app.core.errors import AppError

    import copy as _copy

    registry = v2harness.app.state.registry

    # —— E1：错 tax core 不保存；正确 core 首存 ——
    prepared = v2harness.prepare_ok()
    sid, context, facts, binding = _consume_direct(v2harness, prepared)
    wrong_tax = _core_like("profits_tax", facts, binding)
    outcome_wrong = _complete_tolerant(v2harness, context, wrong_tax)
    assert _field(outcome_wrong, "outcome") != "stored", (
        f"错 tax core 不得保存（Bu1②），实际 {outcome_wrong!r}"
    )
    correct_core = _core_like("salaries_tax", facts, binding)
    try:
        first = _complete_direct(v2harness, context, correct_core)
    except AppError as exc:
        pytest.fail(
            f"错误 tax core 不应先占位导致正确完成被拒（Bu1②）：{exc.code}"
        )
    assert _field(first, "outcome") == "stored", f"实际 {first!r}"

    # —— 同 core 幂等：already_stored＋同 record_id（一次登记）——
    again = _complete_direct(v2harness, context, correct_core)
    assert _field(again, "outcome") == "already_stored", f"实际 {again!r}"
    assert _field(again, "record_id") == _field(first, "record_id"), (
        "一次登记：record_id 不变"
    )

    record_id = _field(first, "record_id")
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200, r_download.text
    hash_before = r_download.json()["core_hash"]

    # —— 异 core → 409 E_CONFIRMATION_CONSUMED（005 §12.3 冻结）＋原记录不变 ——
    conflict_core = _core_like("salaries_tax", facts, binding)
    conflict_core["amounts"]["final_after_reduction"] = "9400"
    with pytest.raises(AppError) as excinfo:
        _complete_direct(v2harness, context, conflict_core)
    assert excinfo.value.code == "E_CONFIRMATION_CONSUMED", (
        f"异 core 冲突外层码须 E_CONFIRMATION_CONSUMED（005 §12.3），"
        f"实际 {excinfo.value.code!r}"
    )
    r_after = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_after.json()["core_hash"] == hash_before, "原记录字节不得改变"

    # —— E2：错 binding core 不保存 ——
    prepared2 = v2harness.prepare_ok()
    sid2, context2, facts2, binding2 = _consume_direct(v2harness, prepared2)
    wrong_binding = dict(binding2)
    wrong_binding["bundle_id"] = "b-forged"
    outcome_wb = _complete_tolerant(
        v2harness, context2, _core_like("salaries_tax", facts2, wrong_binding)
    )
    assert _field(outcome_wb, "outcome") != "stored", (
        f"错 binding core 不得保存（Bu1②），实际 {outcome_wb!r}"
    )
    assert (
        _field(
            _complete_direct(
                v2harness, context2, _core_like("salaries_tax", facts2, binding2)
            ),
            "outcome",
        )
        == "stored"
    ), "错 binding 未占位：正确 core 仍可首存"

    # —— E3：错 input（canonical hash 不符）core 不保存 ——
    prepared3 = v2harness.prepare_ok()
    sid3, context3, facts3, binding3 = _consume_direct(v2harness, prepared3)
    mutated_facts = _copy.deepcopy(facts3)
    mutated_facts["years"]["2025_26"]["financials"]["p1"][
        "employment_income_nai"
    ] = "9999999"
    outcome_wi = _complete_tolerant(
        v2harness, context3, _core_like("salaries_tax", mutated_facts, binding3)
    )
    assert _field(outcome_wi, "outcome") != "stored", (
        f"错 input hash core 不得保存（Bu1②：核对 canonical input hash），"
        f"实际 {outcome_wi!r}"
    )
    assert (
        _field(
            _complete_direct(
                v2harness, context3, _core_like("salaries_tax", facts3, binding3)
            ),
            "outcome",
        )
        == "stored"
    ), "错 input 未占位：正确 core 仍可首存"

    # —— E4：fail_execution 后完成不得补写成功 record（Bu1⑤；
    #     plan §3 冻结签名 fail_execution(context, safe_error_code)）——
    prepared4 = v2harness.prepare_ok()
    sid4, context4, facts4, binding4 = _consume_direct(v2harness, prepared4)
    registry.fail_execution(context4, "E_RULES_STATE_UNVERIFIABLE")
    outcome_failed = _complete_tolerant(
        v2harness, context4, _core_like("salaries_tax", facts4, binding4)
    )
    assert _field(outcome_failed, "outcome") != "stored", (
        f"failed 后完成不得补写成功 record（Bu1⑤），实际 {outcome_failed!r}"
    )

    # —— E5：clear 后完成 → discarded（无 record/链接写入；005 §12.3 冻结）——
    prepared5 = v2harness.prepare_ok()
    sid5, context5, facts5, binding5 = _consume_direct(v2harness, prepared5)
    assert v2harness.client.delete("/api/v1/session").status_code == 200
    discarded = _complete_tolerant(
        v2harness, context5, _core_like("salaries_tax", facts5, binding5)
    )
    assert _field(discarded, "outcome") == "discarded", (
        f"clear 后完成须 discarded（无 record/链接写入），实际 {discarded!r}"
    )
    assert _field(discarded, "record_id") is None


def test_002_execution_snapshot_frozen_against_mutation(v2harness, monkeypatch) -> None:
    """冻结快照保护（Bu1③/005 §12.3）：
    - 引擎违反「独立工作副本」就地改 facts → record 输入仍为冻结快照；
    - getter 返回副本：修改 getter 结果不影响存储记录。"""
    import app.api.routes as routes_module

    original = routes_module.run_engine

    def _leaky_engine(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        # 模拟引擎改写共享 facts（消费后快照必须不受影响）
        try:
            args[1]["years"]["2025_26"]["financials"]["p1"][
                "employment_income_nai"
            ] = "9999999"
        except (KeyError, TypeError):
            pass
        return result

    monkeypatch.setattr(routes_module, "run_engine", _leaky_engine)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r_exec = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, (
        f"execute 须 200，实际 {r_exec.status_code}：{r_exec.text[:300]!r}"
    )
    record_id = r_exec.json()["record_id"]
    amounts = r_exec.json()["amounts"]
    assert amounts.get("final_after_reduction") == "940", amounts

    r_record = v2harness.client.get(f"/api/v1/records/{record_id}")
    assert r_record.status_code == 200, r_record.text
    record_input = r_record.json()["record"]["input"]
    assert record_input == _v2_salary_body()["input"], (
        "record 输入须取冻结快照（不取引擎修改后的 facts；005 §12.3）"
    )

    # getter 副本保护：修改 get_record 返回对象不得影响存储
    registry = v2harness.app.state.registry
    sid = registry.sid_from_cookie(v2harness.client.cookie_value)
    assert sid
    leaked = registry.get_record(sid, record_id)
    assert leaked is not None
    leaked["core"]["amounts"]["final_after_reduction"] = "999000"
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200
    assert (
        r_download.json()["record"]["amounts"]["final_after_reduction"] == "940"
    ), "getter 须返回副本／存储须冻结（Bu1③：修改 getter 结果不影响 record）"


def test_002_rules_change_during_compute_stores_historical(v2harness, monkeypatch) -> None:
    """消费后规则（指针/bundle）变化 → 仍按快照存历史 record：响应与 record
    的 binding 均为快照原值（005 §12.3 交错表「规则/guard 变化同理 →
    historical」；记录不得取新指针）。"""
    import copy as _copy

    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    original_binding = dict(prepared["expected_binding"])
    store = v2harness.app.state.store
    original_bundle_id = store.current()["bundle_id"]

    alt = _copy.deepcopy(_fixed_v2_seed())
    alt["data"]["allowances"]["2025_26"]["basic"]["amount"] = "130000"

    def _publish_alt_during_compute():
        store.publish(alt, evidence_refs=[])

    r = _execute_with_barrier(
        monkeypatch, v2harness, confirmation_id, _publish_alt_during_compute
    )
    assert r.status_code == 200, (
        f"消费后规则变化 → 仍按快照存历史 record（HTTP 200），实际 "
        f"{r.status_code}：{r.text[:300]!r}"
    )
    assert r.json()["binding"] == original_binding, (
        "响应 binding 须为快照原值（不得取新指针）"
    )
    record_id = r.json()["record_id"]
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200
    assert r_download.json()["record"]["binding"] == original_binding, (
        "record binding 须为快照原值（历史 record 不随指针漂移）"
    )
    assert store.current()["bundle_id"] != original_bundle_id, (
        "前置校验：指针确已切换（否则本测试未触发目标窗口）"
    )


def test_002_completion_backend_concurrent_single_registration(v2harness) -> None:
    """completion 并发窗口（复核报告 §五.1 缺口 1「连续/**并发**（同 record_id、
    一次登记）」之并发半边；Bu1/R2：核对与保存须临界区内原子完成）。

    以 threading.Barrier 确定性并发驱动（不靠 sleep 概率）：
    - 并发同 core：结果恰为 {stored, already_stored} 且 record_id 相同
      （一次登记，无双写）；
    - 并发异 core：{stored, 409 E_CONFIRMATION_CONSUMED}，原记录字节不变。
    """
    from app.core.errors import AppError

    registry = v2harness.app.state.registry

    # —— E6：并发同 core → 一次登记 ——
    prepared = v2harness.prepare_ok()
    sid, context, facts, binding = _consume_direct(v2harness, prepared)
    core_a = _core_like("salaries_tax", facts, binding)
    core_b = _core_like("salaries_tax", facts, binding)  # 等价深副本（canonical 同）
    barrier = threading.Barrier(2)
    outcomes: list = []

    def _worker(core: dict) -> None:
        barrier.wait(timeout=10)
        outcomes.append(_complete_tolerant(v2harness, context, core))

    workers = [
        threading.Thread(target=_worker, args=(core,), name="hktax-test-complete")
        for core in (core_a, core_b)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=15)

    assert len(outcomes) == 2, f"并发完成须各自返回，实际 {outcomes!r}"
    by_outcome: dict = {}
    for outcome in outcomes:
        by_outcome.setdefault(_field(outcome, "outcome"), []).append(outcome)
    assert by_outcome.get("stored") and len(by_outcome["stored"]) == 1, (
        f"并发同 core：恰一个 stored（一次登记，不得双写），实际 {outcomes!r}"
    )
    assert by_outcome.get("already_stored") and len(by_outcome["already_stored"]) == 1, (
        f"并发同 core：另一完成须 already_stored（幂等），实际 {outcomes!r}"
    )
    record_id = _field(by_outcome["stored"][0], "record_id")
    assert _field(by_outcome["already_stored"][0], "record_id") == record_id, (
        "并发同 core：already_stored 须指向同一 record_id（一次登记）"
    )

    # —— E7：并发异 core → 一存一 409 E_CONFIRMATION_CONSUMED，原记录不变 ——
    # P1 r2 定点纠错（protocol 咨询 §5.17）：末尾断言改到**本次** stored2 的
    # record_id（原实现检查的是上一执行的 record，未证本次冲突行为）。
    prepared2 = v2harness.prepare_ok()
    sid2, context2, facts2, binding2 = _consume_direct(v2harness, prepared2)
    core_right = _core_like("salaries_tax", facts2, binding2)
    core_wrong = _core_like("salaries_tax", facts2, binding2)
    core_wrong["amounts"]["final_after_reduction"] = "9400"
    outcomes2: list = []
    barrier2 = threading.Barrier(2)

    def _worker2(core: dict, tag: str) -> None:
        barrier2.wait(timeout=10)
        try:
            outcome = _complete_direct(v2harness, context2, core)
            outcomes2.append(
                {"outcome": _field(outcome, "outcome"), "receipt": outcome, "tag": tag}
            )
        except AppError as exc:
            outcomes2.append({"outcome": "error", "code": exc.code, "tag": tag})

    workers2 = [
        threading.Thread(target=_worker2, args=(core, tag), name="hktax-test-complete-c")
        for core, tag in ((core_right, "right"), (core_wrong, "wrong"))
    ]
    for w in workers2:
        w.start()
    for w in workers2:
        w.join(timeout=15)

    assert len(outcomes2) == 2, f"并发完成须各自返回，实际 {outcomes2!r}"
    stored2 = [o for o in outcomes2 if o.get("outcome") == "stored"]
    conflicts = [o for o in outcomes2 if o.get("outcome") == "error"]
    assert len(stored2) == 1, (
        f"并发异 core：恰一个 stored（首写胜出），实际 {outcomes2!r}"
    )
    assert len(conflicts) == 1 and conflicts[0]["code"] == "E_CONFIRMATION_CONSUMED", (
        f"并发异 core：另一完成须 409 E_CONFIRMATION_CONSUMED（005 §12.3），"
        f"实际 {outcomes2!r}"
    )
    # 本次胜出 core 的 record：金额须等于获胜输入（right→940 / wrong→9400），
    # 且冲突未改写它（下载哈希与该 Receipt 的 core_hash 一致）。
    winner = stored2[0]
    winner_record_id = _field(winner["receipt"], "record_id")
    assert winner_record_id, f"stored Receipt 须携带 record_id，实际 {winner!r}"
    expected_final = "940" if winner["tag"] == "right" else "9400"
    r_winner = v2harness.client.get(f"/api/v1/records/{winner_record_id}/download")
    assert r_winner.status_code == 200, r_winner.text
    winner_record = r_winner.json()["record"]
    assert winner_record["amounts"]["final_after_reduction"] == expected_final, (
        f"本次 stored record 须保存获胜输入的 core（tag={winner['tag']}→"
        f"{expected_final}），实际 {winner_record['amounts']!r}"
    )
    assert r_winner.json()["core_hash"] == _field(winner["receipt"], "core_hash"), (
        "并发冲突不得改写本次已保存 record（下载哈希须与 stored Receipt 一致）"
    )
    r_winner_again = v2harness.client.get(
        f"/api/v1/records/{winner_record_id}/download"
    )
    assert r_winner_again.json()["core_hash"] == r_winner.json()["core_hash"], (
        "并发冲突不得改动已保存 record（原记录字节不变）"
    )


def test_002_guard_change_during_compute_stores_historical(v2harness, monkeypatch) -> None:
    """消费后 guard（availability）变化 → 同按快照存历史 record（复核报告
    §五.5 缺口 5「消费后规则/**guard** 变化仅 historical」之 guard 半边；
    005 §12.3 交错表「规则/guard 变化同理 → historical」）。

    消费临界区已过（引擎屏障触发＝已快照）后把 guard 换成 blocked：
    不得拒算/丢弃已消费执行，仍 200＋快照金额＋record 可读。
    """

    real_scheduler = v2harness.app.state.update_scheduler

    def _block_guard_during_compute() -> None:
        v2harness.app.state.update_scheduler = _BlockedScheduler()

    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    try:
        r = _execute_with_barrier(
            monkeypatch, v2harness, confirmation_id, _block_guard_during_compute
        )
    finally:
        # 安全层不得被拆除（review 1）：替换仅限本计算窗口，随后恢复原包装器。
        v2harness.app.state.update_scheduler = real_scheduler
    assert r.status_code == 200, (
        f"消费后 guard 变化 → 仍须按快照存历史 record（HTTP 200），实际 "
        f"{r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    amounts = payload.get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", (
        f"金额须来自消费时快照（guard 变化不撤回/不改写），实际 {amounts!r}"
    )
    r_record = v2harness.client.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200, (
        "guard 变化后的历史 record 仍须可读（仅 historical，不回撤）"
    )


# ===========================================================================
# P1 r2 新 Red（protocol-consultation.md §5 B5–B8/C9–C12/D13–D16；
# 计划 p1-r2-bounded-plan.md §2 RED-P／§3 冻结签名）
# ===========================================================================


def _alt_seed() -> dict:
    """与现行指针不同内容的合法 v2 束（basic 金额改动 → 不同 bundle hash）。"""
    import copy as _copy

    alt = _copy.deepcopy(_fixed_v2_seed())
    alt["data"]["allowances"]["2025_26"]["basic"]["amount"] = "130000"
    return alt


class _OtherPeriodBlockedScheduler:
    """仅无关期间（2024_25）blocked；目标期 2025_26/2026_27 均 ok
    （D13：无关期间 guard 变化不得误伤 current 判定）。"""

    def availability(self, period: str) -> dict:
        if period == "2024_25":
            return {"mode": "blocked", "notice": "测试注入：无关期间 blocked"}
        return {"mode": "ok"}


def _sid_of(harness: _Harness) -> str:
    registry = harness.app.state.registry
    sid = registry.sid_from_cookie(harness.client.cookie_value)
    assert sid, "会话 cookie 未能解析出 sid"
    return sid


def _request_in_worker(harness: _Harness, confirmation_id: str) -> dict:
    """工作线程发起 execute（Event/Barrier 确定性交错；不靠 sleep）。"""
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )

    return outcome, _worker


def _gate_registry_method(
    monkeypatch, registry: Any, name: str, entered: threading.Event,
    release: threading.Event,
) -> None:
    """在 registry 既有方法入口设确定性屏障（进入原方法前等待放行）。"""
    original = getattr(registry, name)

    def _gated(*args: Any, **kwargs: Any):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=10), (
                f"{name} 屏障未在 10s 内放行（测试屏障死锁看门狗）"
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(registry, name, _gated)


def _gate_registry_method_after_result(
    monkeypatch, registry: Any, name: str, entered: threading.Event,
    release: threading.Event,
) -> None:
    """先执行原方法、**捕获其结果后**暂停——模拟「route 已取得权威判定副本、
    返回前」窗口（§5 B8：peek 副本在 clear 前取得、在 clear 后被用作答案）。"""
    original = getattr(registry, name)

    def _gated(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=10), (
                f"{name} 屏障未在 10s 内放行（测试屏障死锁看门狗）"
            )
        return result

    monkeypatch.setattr(registry, name, _gated)


def test_002_r2_b5_pointer_change_after_precheck_before_consume_not_consumed(
    v2harness, monkeypatch
) -> None:
    """§5 B5：route 预检完成后、consume 提交前指针切换 → 须 stale 且不消费；
    指针复原后同 token 可执行（失败未消费）。写路径＝真实 app.state.store
    publish（不另建 RuleStore 冒充锁域）。"""
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    store = v2harness.app.state.store
    original_bundle_id = store.current()["bundle_id"]

    entered = threading.Event()
    release = threading.Event()
    _gate_registry_method(
        monkeypatch, v2harness.app.state.registry, "consume_for_execution",
        entered, release,
    )
    outcome, worker = _request_in_worker(v2harness, confirmation_id)
    thread = threading.Thread(target=worker, name="hktax-test-b5")
    thread.start()
    try:
        assert entered.wait(timeout=10), "consume 未被调用（未达消费临界区）"
        store.publish(_alt_seed(), evidence_refs=[])
    finally:
        release.set()
        thread.join(timeout=15)
    assert "response" in outcome, "execute 工作线程未返回响应"
    r = outcome["response"]
    assert r.status_code == 409, (
        f"预检后指针变化 → consume 须在临界区内重读并 409 stale（不消费），"
        f"实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert _error_code(r.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r.json(), "stale 拒绝不得输出税额"

    # 失败未消费：指针复原（同内容 republish → 同 hash 派生同 bundle_id）后
    # 同一确认可执行
    assert store.current()["bundle_id"] != original_bundle_id, (
        "前置校验：指针确已切换（否则本测试未触发目标窗口）"
    )
    store.publish(_fixed_v2_seed(), evidence_refs=[])
    r_restored = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_restored.status_code == 200, (
        f"指针复原后同一确认须仍可执行（失败未消费），实际 "
        f"{r_restored.status_code}：{r_restored.text[:250]!r}"
    )


def test_002_r2_b6_current_guard_change_after_precheck_before_consume(
    v2harness, monkeypatch, tmp_path
) -> None:
    """§5 B6（current 半边，review 4）：预检后、consume 前本期 guard 经真实
    app-owned store scheduler 状态写入（rollback audit 行）变 blocked——同一
    guard 对象的判定被真实状态改变（不替换 app.state 对象）；规则错误拒绝且
    不消费；删除状态行恢复后同 token 可用。"""
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    db = tmp_path / "rules.db"

    entered = threading.Event()
    release = threading.Event()
    _gate_registry_method(
        monkeypatch, v2harness.app.state.registry, "consume_for_execution",
        entered, release,
    )
    outcome, worker = _request_in_worker(v2harness, confirmation_id)
    thread = threading.Thread(target=worker, name="hktax-test-b6a")
    thread.start()
    rollback_row: dict[str, Any] = {}
    try:
        assert entered.wait(timeout=10), "consume 未被调用（未达消费临界区）"
        conn = sqlite3.connect(db)
        try:
            cursor = conn.execute(
                "INSERT INTO audit_log (event_type, detail_json, created_at)"
                " VALUES ('rollback', ?, ?)",
                (
                    json.dumps({"affected_periods": ["2025_26"]}, ensure_ascii=False),
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            rollback_row["id"] = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()
        verdict = v2harness.app.state.update_scheduler.availability("2025_26")
        assert verdict.get("mode") == "blocked", (
            f"前置校验：真实 rollback 状态行使同一 guard 期间判定变 blocked，实际 "
            f"{verdict!r}"
        )
    finally:
        release.set()
        thread.join(timeout=15)
    assert "response" in outcome
    r = outcome["response"]
    assert r.status_code == 422, (
        f"预检后本期 guard 变 blocked → consume 锁内重查须拒绝（不消费），"
        f"实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE"
    assert "amounts" not in r.json()
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM audit_log WHERE id = ?", (rollback_row["id"],))
        conn.commit()
    finally:
        conn.close()
    r_recovered = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_recovered.status_code == 200, (
        f"guard 恢复后同一确认须仍可执行（失败未消费），实际 "
        f"{r_recovered.status_code}：{r_recovered.text[:250]!r}"
    )


def test_002_r2_b6_next_only_guard_change_freezes_next_keeps_current(
    v2harness, monkeypatch
) -> None:
    """§5 B6（next-only 独立例，review 4）：预检后、consume 前仅 next guard 经
    同一 guard 对象（不替换 app.state）变不可核验 → 消费成功、冻结 next
    不可用（next_provisional=null）、本年 final 940 保留。"""
    import copy as _copy

    body = _v2_salary_body()
    next_year = _copy.deepcopy(body["input"]["years"]["2025_26"])
    next_year["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    body["input"]["years"]["2026_27"] = next_year
    body["input"]["assumptions"] = [
        {
            "assumption_id": "a1",
            "kind": "same_income",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        },
        {
            "assumption_id": "a2",
            "kind": "same_mpf",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        },
    ]
    r_prepare = v2harness.client.post(
        _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
    )
    assert r_prepare.status_code == 201, (
        f"显式双年＋假设同额载荷须可 prepare，实际 {r_prepare.status_code}："
        f"{r_prepare.text[:250]!r}"
    )
    confirmation_id = v2harness.confirm_ok(r_prepare.json())
    guard = v2harness.app.state.update_scheduler
    real_availability = guard.availability

    def _next_weird(period: str):
        if period == "2026_27":
            return {"mode": "celestial", "notice": "B6b-同对象判定注入"}
        return real_availability(period)

    entered = threading.Event()
    release = threading.Event()
    _gate_registry_method(
        monkeypatch, v2harness.app.state.registry, "consume_for_execution",
        entered, release,
    )
    outcome, worker = _request_in_worker(v2harness, confirmation_id)
    thread = threading.Thread(target=worker, name="hktax-test-b6b")
    thread.start()
    armed = False
    try:
        assert entered.wait(timeout=10), "consume 未被调用（未达消费临界区）"
        setattr(guard, "availability", _next_weird)
        armed = True
    finally:
        release.set()
        thread.join(timeout=15)
        if armed and "availability" in guard.__dict__:
            delattr(guard, "availability")
    assert "response" in outcome
    r = outcome["response"]
    assert r.status_code == 200, (
        f"仅 next guard 不可核验不得阻断本年（消费成功），实际 "
        f"{r.status_code}：{r.text[:250]!r}"
    )
    amounts = r.json().get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", (
        f"本年 final 须保留（940），实际 {amounts!r}"
    )
    assert amounts.get("next_provisional") is None, (
        f"next 在消费临界区内重查为不可核验 → 须冻结为 null，实际 "
        f"{amounts.get('next_provisional')!r}"
    )


def test_002_r2_b7_store_lock_covers_read_to_commit_window(
    v2harness, monkeypatch
) -> None:
    """§5 B7：consume 的读→提交窗口须被同一 store 写锁覆盖——guard 在
    registry→store 临界区内被重查时，writer 非阻塞 acquire 不可获取、真实
    publish 只能在 reader 释放后落地。不等待被锁住的 writer 完成才释放 reader。

    锁探针＝RuleStore._write_lock（实例级写锁，protocol 咨询 §3.2 明示）。
    当前缺陷：consume 不在锁内重查 guard → 屏障永不触发（看门狗显式失败）。
    """
    store = v2harness.app.state.store
    guard = v2harness.app.state.update_scheduler
    real_availability = guard.availability

    entered = threading.Event()
    release = threading.Event()
    window: dict[str, Any] = {"engaged": False}

    def _probing_availability(period: str):
        verdict = real_availability(period)
        if not window["engaged"]:
            acquired = store._write_lock.acquire(blocking=False)
            try:
                if not acquired:
                    # 该次 guard 查询发生在 store 写锁被持有期间＝消费临界区内
                    window["engaged"] = True
                    entered.set()
                    assert release.wait(timeout=10), (
                        "B7 屏障未在 10s 内放行（测试屏障死锁看门狗）"
                    )
            finally:
                if acquired:
                    store._write_lock.release()
        return verdict

    original_bundle_id = store.current()["bundle_id"]
    alt = _alt_seed()
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    # review 5：探针须在 prepare/confirm 之后才 arm（否则正确实现的 prepare
    # 单快照锁会在 execute 线程存在前先行命中窗口；数据准备阶段不得被探测）。
    monkeypatch.setattr(guard, "availability", _probing_availability)
    outcome, worker = _request_in_worker(v2harness, confirmation_id)
    thread = threading.Thread(target=worker, name="hktax-test-b7-reader")
    thread.start()
    try:
        assert entered.wait(timeout=10), (
            "consume 未在 registry→store 临界区内重查 guard（读→提交窗口"
            "未被 store 写锁覆盖；plan §3「消费锁内重查 current+next」）"
        )
    except AssertionError:
        release.set()
        thread.join(timeout=15)
        raise

    attempted = threading.Event()
    writer_done = threading.Event()
    probe: dict[str, Any] = {}

    def _writer() -> None:
        acquired = store._write_lock.acquire(blocking=False)
        probe["acquired"] = acquired
        if acquired:
            store._write_lock.release()
        attempted.set()
        # 真实写路径（app.state.store.publish）；若被锁住则阻塞至 reader 释放
        store.publish(alt, evidence_refs=[])
        writer_done.set()

    writer_thread = threading.Thread(target=_writer, name="hktax-test-b7-writer")
    writer_thread.start()
    try:
        assert attempted.wait(timeout=10), "writer 未完成非阻塞探测（死锁看门狗）"
        assert probe["acquired"] is False, (
            "writer 在消费临界区（读→提交窗口）内获取了同一 store 写锁——"
            "快照提交窗口未被锁覆盖（§5 B7/§3.2 锁序 registry→store）"
        )
    finally:
        # 先释放 reader，再等待双方结束（不得等被锁住的 writer 先完成）
        release.set()
        thread.join(timeout=15)
        writer_thread.join(timeout=15)
    assert writer_done.is_set(), "writer 真实写须在 reader 释放后完成"
    assert "response" in outcome and outcome["response"].status_code == 200, (
        "reader 快照执行须成功（锁内重查不应误拒正常执行）"
    )
    assert outcome["response"].json()["binding"]["bundle_id"] == original_bundle_id, (
        "reader 须按窗口前的指针快照执行（不受窗口后 writer 影响）"
    )
    assert store.current()["bundle_id"] != original_bundle_id, (
        "writer 写入须在 reader 结束后生效（不能穿过提交窗口）"
    )


def test_002_r2_b8_repeat_token_after_clear_no_stale_record_link(
    v2harness, monkeypatch
) -> None:
    """§5 B8：已消费确认的重复 execute 在取得权威判定后、返回前发生 clear →
    不得返回旧 record 链接（不能以锁外 peek 副本作答）；正常历史重复执行
    返回链接的原断言保留于 test_002_consumed_reuse_returns_only_saved_record_link。"""
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r_exec = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, r_exec.text[:300]
    record_id = r_exec.json()["record_id"]

    # 第二次（重复）execute：route 取得 registry 判定**副本后**、返回前暂停
    # （peek 副本在 clear 前取得——正是锁外 peek 分支的缺陷窗口）；若 Green
    # 移除 peek 分支，则首个权威读取（get_consumption/consume）在入口屏障
    # 处等待 clear 提交后再读——两种实现形态都不得返回旧链接。
    entered = threading.Event()
    release = threading.Event()
    registry = v2harness.app.state.registry
    _gate_registry_method_after_result(
        monkeypatch, registry, "peek_confirmation", entered, release
    )
    for name in ("get_consumption", "consume_for_execution"):
        _gate_registry_method(monkeypatch, registry, name, entered, release)
    outcome, worker = _request_in_worker(v2harness, confirmation_id)
    thread = threading.Thread(target=worker, name="hktax-test-b8")
    thread.start()
    editor = v2harness.client.clone()
    try:
        assert entered.wait(timeout=10), "重复 execute 未触发任何 registry 判定读取"
        r_clear = editor.delete("/api/v1/session")
        assert r_clear.status_code == 200, (
            f"重复 execute 在途 clear 须 200（epoch 边界），实际 {r_clear.status_code}"
        )
    finally:
        release.set()
        thread.join(timeout=15)
        editor.close()
    assert "response" in outcome
    r = outcome["response"]
    assert r.status_code == 409, f"clear 后重复 execute 须 409，实际 {r.status_code}"
    assert _error_code(r.json()) == "E_CONFIRMATION_STALE", (
        f"clear 后旧 token 须 E_CONFIRMATION_STALE（不得以 CONSUMED＋旧链接作答），"
        f"实际 {_error_code(r.json())!r}"
    )
    assert "record_id" not in r.json(), (
        f"clear 后不得返回旧 record 链接（锁外 peek 副本不得作答），"
        f"实际 payload：{r.json()!r}"
    )
    assert "amounts" not in r.json()


def test_002_r2_c9_execution_context_complete_frozen_snapshot(v2harness) -> None:
    """§5 C9/A4：冻结 ExecutionContext 全字段与身份（plan §3/005 §12.3 冻结
    集合；类型表示不绑定 dict/dataclass）。两税 current/next；五税仅当前期；
    canonical bytes 哈希与确认值一致；返回副本修改不改变 registry 权威判定。

    【接口型 Red】当前 consume_for_execution 缺 store/guard 冻结签名 →
    TypeError/缺字段即失败原因，不冒充行为 Red（计划 §4）。"""
    import hashlib as _hashlib

    from app.core.hashing import calculation_core_hash

    registry = v2harness.app.state.registry
    prepared = v2harness.prepare_ok()
    sid = _sid_of(v2harness)  # prepare 已建立会话 cookie
    confirmation_id = v2harness.confirm_ok(prepared)
    context = registry.consume_for_execution(
        sid,
        confirmation_id,
        target=_SALARIES_EXECUTE,
        store=v2harness.app.state.store,
        guard=v2harness.app.state.update_scheduler,
    )

    # —— 全字段在场（缺冻结字段 → _field 以字段名失败）——
    for name in (
        "execution_id", "session_id", "session_epoch", "input_revision",
        "confirmation_id", "tax_type", "schema_version", "execute_target",
        "input_hash", "canonical_input_bytes", "binding",
        "verified_bundle_bytes", "availability_context",
    ):
        _field(context, name)
    assert _field(context, "tax_type") == "salaries_tax"
    assert _field(context, "schema_version") == "2.0.0", (
        "输入信封 schema_version 须贯穿 prepared/confirmation/context（A4："
        "不得以 bundle rules_schema_version 替代）"
    )
    assert _field(context, "execute_target") == _SALARIES_EXECUTE
    assert _field(context, "session_id") == sid

    # —— bytes 哈希与确认值一致（review 11：严格 bytes，不接受 bytearray）——
    canonical = _field(context, "canonical_input_bytes")
    assert isinstance(canonical, bytes), (
        f"canonical_input_bytes 须为冻结 bytes（非 bytearray），实际 {type(canonical)!r}"
    )
    assert (
        "sha256:" + _hashlib.sha256(canonical).hexdigest()
        == _field(context, "input_hash") == prepared["input_hash"]
    ), "canonical bytes 哈希须等于确认的 input_hash（C9②）"
    verified = _field(context, "verified_bundle_bytes")
    assert isinstance(verified, bytes), (
        f"verified_bundle_bytes 须为冻结 bytes（非 bytearray），实际 {type(verified)!r}"
    )
    binding = _field(context, "binding")
    assert (
        "sha256:" + _hashlib.sha256(verified).hexdigest()
        == _kv(binding, "bundle_hash")
    ), "完整已核束 bytes 的哈希须等于该消费 binding 的 bundle_hash（C9②）"

    # —— 两税 current/next；五税仅当前期 ——
    availability_context = _field(context, "availability_context")
    assert _kv(_kv(availability_context, "current"), "year") == "2025_26"
    assert _kv(_kv(availability_context, "next"), "year") == "2026_27"

    r_profits = v2harness.client.post(
        _PREPARE,
        json_body={
            "tax_type": "profits_tax",
            "schema_version": "1.0.0",
            "input": {
                "year_of_assessment": "2025_26",
                "entity_kind": "sole_proprietorship",
                "assessable_profit": "600000",
            },
        },
        headers=v2harness.fresh_if_match(),
    )
    assert r_profits.status_code == 201, r_profits.text[:250]
    profits_confirmation = v2harness.confirm_ok(r_profits.json())
    profits_context = registry.consume_for_execution(
        sid,
        profits_confirmation,
        target=_PROFITS_EXECUTE,
        store=v2harness.app.state.store,
        guard=v2harness.app.state.update_scheduler,
    )
    profits_availability = _field(profits_context, "availability_context")
    assert _kv(profits_availability, "current") is not None
    assert _kv(profits_availability, "next") is None, (
        "五税仅当前期 guard 上下文（§3.3：其余五税不扩年度 next）"
    )
    assert _field(profits_context, "schema_version") == "1.0.0", (
        "五税 context schema_version 须为输入信封 1.0.0（review 15：不得用 "
        "bundle rules_schema_version 2.0.0 替代）"
    )
    assert _field(profits_context, "tax_type") == "profits_tax"
    assert _field(profits_context, "execute_target") == _PROFITS_EXECUTE

    # —— 返回副本修改不改变 registry 权威（C9③）——
    facts = _v2_salary_body()["input"]  # canonical 输入（与 input_hash 一致）
    original_binding = dict(prepared["expected_binding"])
    binding_copy = _field(context, "binding")
    if isinstance(binding_copy, MutableMapping):
        binding_copy["bundle_id"] = "b-forged-by-caller"
    core = _core_like("salaries_tax", facts, original_binding)
    receipt = registry.complete_execution(
        context,
        core,
        store=v2harness.app.state.store,
        guard=v2harness.app.state.update_scheduler,
    )
    assert _field(receipt, "outcome") == "stored", (
        f"完成按 registry 权威判定（不信任调用方改写的 context 副本），"
        f"实际 {_field(receipt, 'outcome')!r}"
    )
    r_download = v2harness.client.get(
        f"/api/v1/records/{_field(receipt, 'record_id')}/download"
    )
    assert r_download.status_code == 200
    assert r_download.json()["record"]["binding"] == original_binding, (
        "record binding 须取消费快照权威值（返回副本污染不得入库；C9③）"
    )
    assert calculation_core_hash(r_download.json()["record"]) == r_download.json()[
        "core_hash"
    ], "record 哈希一致性（下载 core_hash＝封闭 11 字段哈希）"


def test_002_r2_c10_bundle_working_copy_pollution_not_persisted(
    v2harness, monkeypatch
) -> None:
    """§5 C10：引擎（工作副本）改写束的嵌套内容 → record evidence/binding
    仍须取消费快照值；现有 facts-only 快照测试不足以覆盖此例。"""
    import app.api.routes as routes_module

    original = routes_module.run_engine
    forged = {"source_id": "forged-by-engine", "digest": "sha256:" + "0" * 64}

    def _polluting_engine(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        bundle = args[2] if len(args) > 2 else kwargs.get("bundle")
        try:
            bundle["evidence_digests"].append(forged)
        except (KeyError, TypeError, AttributeError):
            pass
        return result

    monkeypatch.setattr(routes_module, "run_engine", _polluting_engine)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 200, (
        f"污染引擎副本不得影响执行结果，实际 {r.status_code}：{r.text[:250]!r}"
    )
    original_binding = dict(prepared["expected_binding"])
    record_id = r.json()["record_id"]
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200
    record = r_download.json()["record"]
    assert record["evidence_refs"] == [], (
        f"record evidence 须取消费快照的已核束（seed evidence_digests=[]），"
        f"不得带引擎污染后的 {record['evidence_refs']!r}（C10/§12.3）"
    )
    assert record["binding"] == original_binding, (
        "原束 hash/binding 须保持消费快照值（C10）"
    )


# —— C11 替代（c11-construction-proposal/review APPROVED FOR C11 RED；
# 旧 deepcopy 注入实现细节耦合职责已归档为历史诊断，不再作为冻结算法）——


def _red_registry_identity_sets(registry: Any, sid: str) -> tuple:
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


def _execution_context_builder_or_fail() -> Any:
    """C11：真实模块私有构造职责必须在场（缺失＝接口型失败，非行为证据）。"""
    import app.web.security as security_module

    builder = getattr(security_module, "_build_execution_context", None)
    assert callable(builder), (
        "INTERFACE_ONLY：缺 app/web/security.py::_build_execution_context"
        "（C11 已批准 13-keyword 真实构造职责；缺失只记接口型 Red，"
        "不得解读为快照构造行为通过/失败）"
    )
    return builder


_C11_BUILDER_KEYWORDS = (
    "execution_id", "session_id", "session_epoch", "input_revision",
    "confirmation_id", "tax_type", "schema_version", "execute_target",
    "input_hash", "facts", "binding", "bundle", "availability_context",
)


def test_002_r2_c11_execution_context_builder_interface() -> None:
    """C11（接口门，review 11）：真实模块私有职责存在且**恰好** 13 个材料、
    全部为 KEYWORD-ONLY（不允许参数子集或位置参数容错）。当前 security.py
    的 consume 先 consumed 后构造、该 helper 不存在 → 接口型 Red。"""
    import inspect

    builder = _execution_context_builder_or_fail()
    parameters = inspect.signature(builder).parameters
    assert set(parameters) == set(_C11_BUILDER_KEYWORDS), (
        "INTERFACE_ONLY：_build_execution_context 须恰为 13 个已批材料"
        f"（无多余/缺失/子集），实际参数 {list(parameters)!r}"
    )
    kinds = {param.kind for param in parameters.values()}
    assert kinds == {inspect.Parameter.KEYWORD_ONLY}, (
        f"13 材料须全部为 KEYWORD-ONLY（拒绝位置传参容错），实际 kind={kinds!r}"
    )


def test_002_r2_c11_builder_spy_real_context_forms_snapshot(
    v2harness, monkeypatch
) -> None:
    """C11（正常 spy 行为面）：真实 helper 参与消费构造，且返回内容确实成为
    权威快照/记录来源（c11-review 要求1/7；对象身份不绑定，公开独立副本即可）。"""
    import inspect

    import app.web.security as security_module

    builder = _execution_context_builder_or_fail()
    signature = inspect.signature(builder)
    calls: list[dict[str, Any]] = []

    def _spy(*args: Any, **kwargs: Any):
        context = builder(*args, **kwargs)
        try:
            materials = dict(signature.bind(*args, **kwargs).arguments)
        except TypeError:
            materials = dict(kwargs)
        calls.append({"materials": materials, "context": context})
        return context

    monkeypatch.setattr(security_module, "_build_execution_context", _spy)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 200, (
        f"正常路径透明 spy 不得改变行为，实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert len(calls) >= 1, (
        "消费构造未调用真实 _build_execution_context（空 hook/未参与＝实现缺陷）"
    )
    materials = calls[-1]["materials"]
    assert set(materials) == set(_C11_BUILDER_KEYWORDS), (
        f"构造须以 13 个已批材料调用，实际 {sorted(materials)!r}"
    )
    context = calls[-1]["context"]
    assert _field(context, "input_hash") == prepared["input_hash"]
    canonical = _field(context, "canonical_input_bytes")
    assert isinstance(canonical, bytes), (
        f"canonical_input_bytes 须为冻结 bytes（非 bytearray），实际 {type(canonical)!r}"
    )
    assert "sha256:" + hashlib.sha256(canonical).hexdigest() == (
        prepared["input_hash"]
    ), "返回内容的 canonical bytes 哈希须与确认 input_hash 一致"
    binding = _field(context, "binding")
    verified = _field(context, "verified_bundle_bytes")
    assert isinstance(verified, bytes), (
        f"verified_bundle_bytes 须为冻结 bytes（非 bytearray），实际 {type(verified)!r}"
    )
    assert "sha256:" + hashlib.sha256(verified).hexdigest() == (
        _kv(binding, "bundle_hash")
    ), "完整已核束 bytes 哈希须与该消费 binding 一致"
    assert _kv(binding, "bundle_hash") == prepared["expected_binding"]["bundle_hash"]
    # 返回内容确实成为 record 来源（独立公开副本比较，不要求对象身份）
    record_id = r.json()["record_id"]
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200
    record = r_download.json()["record"]
    assert record["binding"] == prepared["expected_binding"]
    assert record["input"] == _v2_salary_body()["input"]
    assert (record.get("amounts") or {}).get("final_after_reduction") == "940"


def test_002_r2_c11_builder_fault_no_half_consume_same_token_retry(
    v2harness, monkeypatch
) -> None:
    """C11（故障语义面；c11-review 要求2/3/4/5/6，review 6/11）：仅在真实
    构造边界注入一次普通 RuntimeError（恰命中、无内部重试）→ 安全 500 无
    core/税额/marker、原 token 未消费、无新 execution/record 身份键（registry
    锁内只读观察）、未执行引擎（计数自 prepare/confirm 后 stage 增量）；
    恢复真实 helper 后**同 token 无 prepare/confirm** 成功，事实/绑定/金额与
    合法 control 一致。"""
    import app.api.routes as routes_module
    import app.web.security as security_module

    real_builder = _execution_context_builder_or_fail()
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    registry = v2harness.app.state.registry
    sid = _sid_of(v2harness)
    fired = {"count": 0}

    # review 6：引擎计数在 prepare/confirm 之后安装——prepare 语义预检自身
    # 已调用引擎一次，此前安装会把该阶段增量误计入本案例。
    engine_calls = {"count": 0}
    real_run_engine = routes_module.run_engine

    def _counting_engine(*args: Any, **kwargs: Any):
        engine_calls["count"] += 1
        return real_run_engine(*args, **kwargs)

    monkeypatch.setattr(routes_module, "run_engine", _counting_engine)
    before_ids = _red_registry_identity_sets(registry, sid)

    def _boom(*_args: Any, **_kwargs: Any):
        fired["count"] += 1
        raise RuntimeError("C11-builder-marker-不得泄露")

    monkeypatch.setattr(security_module, "_build_execution_context", _boom)
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert fired["count"] == 1, (
        "真实构造边界注入须恰命中一次（precommit 构造阶段、无内部重试），"
        f"实际 {fired['count']}；response={r.status_code}"
    )
    assert engine_calls["count"] == 0, (
        "快照构造失败发生在消费提交前 → 不得执行引擎（commit 后失败为独立 P4 合同）"
    )
    assert r.status_code == 500, (
        f"构造普通异常 → 安全 500（不伪装 4xx），实际 {r.status_code}"
    )
    assert "C11-builder-marker" not in r.text, "500 不得泄露异常原文/marker"
    payload = r.json()
    assert "amounts" not in payload and "record_id" not in payload, (
        "安全 500 不得携带 core/税额/record 链接"
    )
    assert "no-store" in (r.headers.get("Cache-Control") or "").lower()
    consumption = registry.get_consumption(sid, confirmation_id)
    assert consumption is not None, "get_consumption 既有只读合同"
    assert consumption["consumed"] is False, (
        f"构造失败不得半消费（consumed 须 False），实际 {consumption!r}"
    )
    assert consumption["record_id"] is None, "不得有 record 链接"
    assert consumption["core_hash"] is None, "不得有 core hash"
    after_ids = _red_registry_identity_sets(registry, sid)
    assert after_ids == before_ids, (
        f"构造失败不得产生新 execution/record 身份键（registry 锁内只读比较），"
        f"before={before_ids!r} after={after_ids!r}"
    )

    # 恢复真实 helper；同一 confirmation_id（无重新 prepare/confirm）恢复成功
    monkeypatch.setattr(security_module, "_build_execution_context", real_builder)
    r_retry = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_retry.status_code == 200, (
        f"恢复真实 helper 后同 token 须成功（失败未消费），实际 "
        f"{r_retry.status_code}：{r_retry.text[:250]!r}"
    )
    assert engine_calls["count"] == 1, "恢复后引擎恰执行一次"
    body = r_retry.json()
    assert body["binding"] == prepared["expected_binding"]
    assert (body.get("amounts") or {}).get("final_after_reduction") == "940", (
        f"恢复结果金额须与合法 control 一致，实际 {body.get('amounts')!r}"
    )
    r_download = v2harness.client.get(f"/api/v1/records/{body['record_id']}/download")
    assert r_download.status_code == 200
    record = r_download.json()["record"]
    assert record["input"] == _v2_salary_body()["input"]
    assert record["binding"] == prepared["expected_binding"]
    r_replay = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_replay.status_code == 409 and _error_code(
        r_replay.json()
    ) == "E_CONFIRMATION_CONSUMED", "恰一次登记（恢复后重复执行＝已消费）"
    assert r_replay.json().get("record_id") == body["record_id"]


def test_002_r2_c12_no_source_refetch_during_compute_phase(
    v2harness, monkeypatch
) -> None:
    """§5 C12：消费后引擎计算阶段禁止 get_prepared/get_bundle 数值重取；
    completion 允许只读 current binding/guard；build_core 从快照取内容。"""
    registry = v2harness.app.state.registry
    store = v2harness.app.state.store
    phase = {"consume_done": False, "complete_entered": False}
    counts = {"get_prepared": 0, "get_bundle": 0}
    real_get_prepared = registry.get_prepared
    real_get_bundle = store.get_bundle
    real_consume = registry.consume_for_execution
    real_complete = registry.complete_execution

    def _counting_get_prepared(*args: Any, **kwargs: Any):
        if phase["consume_done"] and not phase["complete_entered"]:
            counts["get_prepared"] += 1
        return real_get_prepared(*args, **kwargs)

    def _counting_get_bundle(*args: Any, **kwargs: Any):
        if phase["consume_done"] and not phase["complete_entered"]:
            counts["get_bundle"] += 1
        return real_get_bundle(*args, **kwargs)

    def _marking_consume(*args: Any, **kwargs: Any):
        context = real_consume(*args, **kwargs)
        phase["consume_done"] = True
        return context

    def _marking_complete(*args: Any, **kwargs: Any):
        phase["complete_entered"] = True
        return real_complete(*args, **kwargs)

    monkeypatch.setattr(registry, "get_prepared", _counting_get_prepared)
    monkeypatch.setattr(store, "get_bundle", _counting_get_bundle)
    monkeypatch.setattr(registry, "consume_for_execution", _marking_consume)
    monkeypatch.setattr(registry, "complete_execution", _marking_complete)

    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 200, (
        f"正常执行须成功（计数装置不改变行为），实际 {r.status_code}："
        f"{r.text[:250]!r}"
    )
    assert phase["consume_done"] and phase["complete_entered"], (
        "前置校验：窗口标记未触发（consume/complete 未被经过）"
    )
    assert counts["get_prepared"] == 0, (
        f"消费后计算阶段禁止 get_prepared 重取，实际 {counts['get_prepared']} 次"
    )
    assert counts["get_bundle"] == 0, (
        f"消费后计算阶段禁止 get_bundle 重取，实际 {counts['get_bundle']} 次"
    )


# —— D13/D14：内部 Receipt 捕获（包装 complete_execution；签名无关透传）——


def _capture_contexts_and_receipts(monkeypatch, harness: _Harness) -> tuple:
    """passthrough 包装 consume/complete，捕获 context 与内部 Receipt
    （§5 统一纪律：内部 Receipt 经包装捕获，不强制可选 HTTP 字段）。"""
    registry = harness.app.state.registry
    boxes: dict[str, list] = {"contexts": [], "receipts": []}
    real_consume = registry.consume_for_execution
    real_complete = registry.complete_execution

    def _capturing_consume(*args: Any, **kwargs: Any):
        context = real_consume(*args, **kwargs)
        boxes["contexts"].append(context)
        return context

    def _capturing_complete(*args: Any, **kwargs: Any):
        receipt = real_complete(*args, **kwargs)
        boxes["receipts"].append(receipt)
        return receipt

    monkeypatch.setattr(registry, "consume_for_execution", _capturing_consume)
    monkeypatch.setattr(registry, "complete_execution", _capturing_complete)
    return boxes


_D13_CASES = [
    ("no_change", "current"),
    ("revision_changed", "historical"),
    ("binding_changed", "historical"),
    ("relevant_guard_blocked", "historical"),
    ("irrelevant_guard_blocked", "current"),
    ("relevant_guard_unverifiable", "historical"),
]


@pytest.mark.parametrize("case,expected_editor_result", _D13_CASES)
def test_002_r2_d13_receipt_outcome_and_historical_single_variable(
    v2harness, monkeypatch, case, expected_editor_result
) -> None:
    """§5 D13：Receipt 四组单变量——current 须 revision/binding/相关 guard
    同时匹配；每组 Receipt 的 execution epoch/revision 须为**原执行值**；
    无关期间 guard 不误伤；guard 不可核验不得 current 也不得丢弃合法历史。"""
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    editor = v2harness.client.clone()
    real_scheduler = v2harness.app.state.update_scheduler

    def _mutate() -> None:
        if case == "no_change":
            return
        if case == "revision_changed":
            r_inv = v2harness.invalidate_now(editor)
            assert r_inv.status_code == 200, (
                f"计算期间 invalidate 须 200，实际 {r_inv.status_code}"
            )
        elif case == "binding_changed":
            v2harness.app.state.store.publish(_alt_seed(), evidence_refs=[])
        elif case == "relevant_guard_blocked":
            v2harness.app.state.update_scheduler = _BlockedScheduler()
        elif case == "irrelevant_guard_blocked":
            v2harness.app.state.update_scheduler = _OtherPeriodBlockedScheduler()
        elif case == "relevant_guard_unverifiable":
            v2harness.app.state.update_scheduler = _NoVerdictScheduler()

    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    try:
        r = _execute_with_barrier(monkeypatch, v2harness, confirmation_id, _mutate)
    finally:
        editor.close()
        v2harness.app.state.update_scheduler = real_scheduler
    assert r.status_code == 200, (
        f"{case}：完成阶段 freshness 只影响 current/historical（不拒算、不撤回），"
        f"实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert boxes["contexts"] and boxes["receipts"], (
        f"{case}：前置校验失败（context/Receipt 未被捕获）"
    )
    context = boxes["contexts"][-1]
    receipt = boxes["receipts"][-1]

    assert _field(receipt, "outcome") == "stored", (
        f"{case}：合法快照计算须保存 record，实际 {_field(receipt, 'outcome')!r}"
    )
    assert _field(receipt, "editor_result") == expected_editor_result, (
        f"{case}：editor_result 须为 {expected_editor_result}（005 §12.3：current "
        f"须 revision/binding/相关 guard 同时匹配），实际 "
        f"{_field(receipt, 'editor_result')!r}"
    )
    assert _field(receipt, "execution_epoch") == _field(context, "session_epoch"), (
        f"{case}：Receipt 须携带原执行 epoch（不是完成时最新值），实际 "
        f"{_field(receipt, 'execution_epoch')!r}"
    )
    assert _field(receipt, "execution_revision") == _field(
        context, "input_revision"
    ), (
        f"{case}：Receipt 须携带原执行 revision，实际 "
        f"{_field(receipt, 'execution_revision')!r}"
    )
    amounts = r.json().get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", (
        f"{case}：金额须来自消费快照，实际 {amounts!r}"
    )


def test_002_r2_d14_clear_during_completion_discard_receipt_complete(
    v2harness, monkeypatch
) -> None:
    """§5 D14：complete 前提交 clear → Receipt discarded 且字段完整；无
    record/链接/旧税额、不重建会话；已保存旧 record 经 getter 与 HTTP 均不可见。"""
    registry = v2harness.app.state.registry

    # 先保存一条历史 record（供 clear 后不可见断言）
    prepared0 = v2harness.prepare_ok()
    confirmation0 = v2harness.confirm_ok(prepared0)
    sid = _sid_of(v2harness)  # prepare 已建立会话 cookie（clear 保留同一 sid）
    r0 = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation0}
    )
    assert r0.status_code == 200, r0.text[:250]
    old_record_id = r0.json()["record_id"]

    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    editor = v2harness.client.clone()

    def _clear_during_compute() -> None:
        r_clear = editor.delete("/api/v1/session")
        assert r_clear.status_code == 200, (
            f"完成前 clear 须 200（epoch 边界），实际 {r_clear.status_code}"
        )

    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    try:
        r = _execute_with_barrier(
            monkeypatch, v2harness, confirmation_id, _clear_during_compute
        )
    finally:
        editor.close()
    assert r.status_code == 409, (
        f"clear 后完成 → discarded → 409 E_CONFIRMATION_STALE，实际 {r.status_code}"
    )
    assert _error_code(r.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r.json() and "record_id" not in r.json()

    assert boxes["receipts"], "前置校验：discarded Receipt 未被捕获"
    receipt = boxes["receipts"][-1]
    context = boxes["contexts"][-1]
    assert _field(receipt, "outcome") == "discarded"
    assert _field(receipt, "record_id") is None, "discarded 不得携带 record 链接"
    assert _field(receipt, "execution_epoch") == _field(context, "session_epoch"), (
        f"discarded Receipt 亦须携带原执行 epoch（字段完整），实际 "
        f"{_field(receipt, 'execution_epoch')!r}"
    )
    assert _field(receipt, "execution_revision") == _field(
        context, "input_revision"
    ), "discarded Receipt 亦须携带原执行 revision（字段完整）"

    # 已保存旧 record：registry getter 与 HTTP 读取均不可见（不重建会话）
    assert registry.get_record(sid, old_record_id) is None, (
        "clear 后旧 record 经 registry getter 不可见"
    )
    assert (
        v2harness.client.get(f"/api/v1/records/{old_record_id}").status_code == 404
    ), "clear 后旧 record 经 HTTP 不可见"


def test_002_r2_d15_late_fail_cannot_overwrite_stored_terminal(v2harness) -> None:
    """§5 D15：成功终态不被迟到 fail 覆盖——stored 后 fail_execution 为无效
    操作；同 core 再完成仍 already_stored＋同 record_id＋原字节不变。"""
    registry = v2harness.app.state.registry
    prepared = v2harness.prepare_ok()
    sid, context, facts, binding = _consume_direct(v2harness, prepared)
    first = _complete_direct(
        v2harness, context, _core_like("salaries_tax", facts, binding)
    )
    assert _field(first, "outcome") == "stored"
    record_id = _field(first, "record_id")
    r_before = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    hash_before = r_before.json()["core_hash"]

    registry.fail_execution(context, "E_RULES_STATE_UNVERIFIABLE")  # 迟到 fail

    again = _complete_direct(
        v2harness, context, _core_like("salaries_tax", facts, binding)
    )
    assert _field(again, "outcome") == "already_stored", (
        f"迟到 fail 不得把 stored 改写为 failed（running-only fail），实际 "
        f"{_field(again, 'outcome')!r}"
    )
    assert _field(again, "record_id") == record_id, "一次登记：record_id 不变"
    r_after = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_after.json()["core_hash"] == hash_before, "原记录字节不得改变"
    assert registry.get_record(sid, record_id) is not None


_D16_CASES = [
    ("engine_apperror", "engine", "apperror"),
    ("engine_runtime", "engine", "runtime"),
    ("core_apperror", "core", "apperror"),
    ("core_runtime", "core", "runtime"),
]


@pytest.mark.parametrize("case,stage,kind", _D16_CASES)
def test_002_r2_d16_exception_matrix_consumed_no_record_no_rerun(
    v2harness, monkeypatch, case, stage, kind
) -> None:
    """§5 D16（HTTP 行为面）：execute 的 engine/build_core 阶段 AppError 或
    普通异常 → consumed=true、无 record/链接、不自动重跑；RuntimeError 对外
    安全 500 不泄露 marker。"""
    import app.api.routes as routes_module
    from app.core.errors import AppError

    registry = v2harness.app.state.registry
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    sid = _sid_of(v2harness)

    def _raise_engine(*args: Any, **kwargs: Any):
        if kind == "apperror":
            raise AppError("E_RULES_STATE_UNVERIFIABLE", "测试注入：引擎业务错误")
        raise RuntimeError("秘密细节-D16-不应泄露")

    def _raise_core(*args: Any, **kwargs: Any):
        if kind == "apperror":
            raise AppError("E_RULES_STATE_UNVERIFIABLE", "测试注入：core 阶段业务错误")
        raise RuntimeError("秘密细节-D16-core-不应泄露")

    if stage == "engine":
        monkeypatch.setattr(routes_module, "run_engine", _raise_engine)
    else:
        monkeypatch.setattr(routes_module, "build_core", _raise_core)

    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    expected_status = 422 if kind == "apperror" else 500
    assert r.status_code == expected_status, (
        f"{case}：预期 {expected_status}，实际 {r.status_code}：{r.text[:200]!r}"
    )
    if kind == "runtime":
        assert "秘密细节" not in r.text, f"{case}：500 不得泄露异常原文"
        assert "no-store" in (r.headers.get("Cache-Control") or "").lower()
    assert "record_id" not in r.json(), f"{case}：失败不得写 record/链接"
    assert "amounts" not in r.json(), f"{case}：失败不得输出税额"

    consumption = registry.get_consumption(sid, confirmation_id)
    assert consumption is not None and consumption["consumed"] is True, (
        f"{case}：计算抛错仍 consumed（不恢复能力；005 §12.3），实际 {consumption!r}"
    )
    r_again = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_again.status_code == 409, f"{case}：不得自动重跑，实际 {r_again.status_code}"
    assert _error_code(r_again.json()) == "E_CONFIRMATION_CONSUMED"
    assert "amounts" not in r_again.json()


@pytest.mark.parametrize("case,stage,kind", _D16_CASES)
def test_002_r2_d16_failed_terminal_rejects_backfill(
    v2harness, monkeypatch, case, stage, kind
) -> None:
    """§5 D16（终态面；接口型 Red）：异常进入 failed 终态后，同 execution
    完成不得补写成功 record。经 plan §3 冻结签名 complete_execution(context,
    core, *, store, guard) 直驱——当前缺该签名 → TypeError 为失败原因，
    不冒充行为 Red。"""
    import app.api.routes as routes_module
    from app.core.errors import AppError

    registry = v2harness.app.state.registry
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)

    def _raise_engine(*args: Any, **kwargs: Any):
        if kind == "apperror":
            raise AppError("E_RULES_STATE_UNVERIFIABLE", "测试注入：引擎业务错误")
        raise RuntimeError("秘密细节-D16b")

    def _raise_core(*args: Any, **kwargs: Any):
        if kind == "apperror":
            raise AppError("E_RULES_STATE_UNVERIFIABLE", "测试注入：core 阶段业务错误")
        raise RuntimeError("秘密细节-D16b-core")

    if stage == "engine":
        monkeypatch.setattr(routes_module, "run_engine", _raise_engine)
    else:
        monkeypatch.setattr(routes_module, "build_core", _raise_core)
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code in (422, 500), f"{case}：注入异常须失败，实际 {r.status_code}"
    assert boxes["contexts"], f"{case}：前置校验失败（context 未捕获）"
    context = boxes["contexts"][-1]

    facts = _v2_salary_body()["input"]
    binding = dict(prepared["expected_binding"])
    with pytest.raises(AppError) as excinfo:
        registry.complete_execution(
            context,
            _core_like("salaries_tax", facts, binding),
            store=v2harness.app.state.store,
            guard=v2harness.app.state.update_scheduler,
        )
    assert excinfo.value.code == "E_CONFIRMATION_CONSUMED", (
        f"{case}：failed 终态后完成须拒绝补写，实际 {excinfo.value.code!r}"
    )


# ===========================================================================
# P1 r2 addendum 新 Red（p1-r2-red-queued-amendments.md §四 CR-P 四节点；
# p1-r2-guard-contract.md §5 十二组（G9 由首批 B5/B6a/B6b 承载，见映射）；
# p1-r2-guard-plan-review.md 两执行约束；D16 行为锚补强）
# ===========================================================================


class _ControlledClock:
    """CR-P 可控 monotonic 时钟（006 §5.5：无 sleep；请求间前进、请求内冻结）。"""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, delta: float) -> float:
        self.now += delta
        return self.now


def _install_clock(monkeypatch, clock: _ControlledClock) -> None:
    """全局替换 time.monotonic（security/routes 的 TTL/created_at 均经它）。

    仅影响 Python 层读取；threading.Event/join 的 C 级超时用独立时钟，不受
    本替换影响（屏障看门狗仍为真实超时）。"""
    monkeypatch.setattr(time, "monotonic", clock.monotonic)


class _RecordingGuard:
    """记录逐期间查询的受控本地 guard（不 reject-all； verdicts: period→结果
    或 Exception；'*' 为缺省）。"""

    def __init__(self, verdicts: dict) -> None:
        self.queries: list[str] = []
        self._verdicts = verdicts

    def availability(self, period: str):
        self.queries.append(period)
        verdict = self._verdicts.get(period, self._verdicts.get("*"))
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


_OFFLINE_TEMPLATE = (
    "课税年度 {ya}：官方来源暂不可达；依据最近已核验的本地规则快照计算，"
    "不代表最新规则。"
)


def _dual_year_body() -> dict:
    """双年＋同额假设载荷（next 可计算：使 next guard/规则差异可观察）。"""
    import copy as _copy

    body = _v2_salary_body()
    next_year = _copy.deepcopy(body["input"]["years"]["2025_26"])
    next_year["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    body["input"]["years"]["2026_27"] = next_year
    body["input"]["assumptions"] = [
        {
            "assumption_id": "a1",
            "kind": "same_income",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        },
        {
            "assumption_id": "a2",
            "kind": "same_mpf",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        },
    ]
    return body


# ---------------------------------------------------------------------------
# CR-P 四节点（006 §5.5 冻结命名；005 §12.3 同步条款）
# ---------------------------------------------------------------------------


def test_002_completion_uses_confirmation_not_prepared_deadline(
    v2harness, monkeypatch
) -> None:
    """CR-P1：完成授权到期点＝confirmation 签发 created_at+ttl（prepare=0/
    confirm=100/ttl=300 → 400），不取 prepared（=300）。complete=350 越过
    prepared deadline 仍须授权并存 record。"""
    clock = _ControlledClock(0.0)
    _install_clock(monkeypatch, clock)

    prepared = v2harness.prepare_ok()  # t=0：prepared created_at=0（deadline 300）
    clock.advance(100.0)
    confirmation_id = v2harness.confirm_ok(prepared)  # t=100：deadline 100+300=400
    clock.advance(250.0)  # t=350：> prepared deadline 300、< confirmation 400
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code == 200, (
        f"complete=350 仍在 confirmation deadline(400) 内 → 须授权（不得用 "
        f"prepared deadline=300 拒绝），实际 {r.status_code}：{r.text[:250]!r}"
    )
    payload = r.json()
    assert payload.get("record_id"), "授权完成须保存 record"
    assert (payload.get("amounts") or {}).get("final_after_reduction") == "940"
    r_record = v2harness.client.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200


def test_002_consume_does_not_extend_confirmation_deadline(
    v2harness, monkeypatch
) -> None:
    """CR-P2：consume 不刷新到期点——t=350 消费合法、计算期间推进到 401
    （>400）→ 首次未保存完成须在原子提交前核验过期：discarded 409、无
    core/link/record（若 consume 刷新 created_at→deadline 650 则会错误存储）。"""
    clock = _ControlledClock(0.0)
    _install_clock(monkeypatch, clock)

    prepared = v2harness.prepare_ok()  # t=0
    clock.advance(100.0)
    confirmation_id = v2harness.confirm_ok(prepared)  # t=100 → deadline 400
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)

    clock.advance(250.0)  # t=350：消费时点（350-100=250 ≤ 300 合法）

    def _expire_during_compute() -> None:
        clock.advance(51.0)  # t=401 > 400：完成前过期

    r = _execute_with_barrier(
        monkeypatch, v2harness, confirmation_id, _expire_during_compute
    )
    assert r.status_code == 409, (
        f"完成时已过 confirmation deadline（401>400）→ 首次未保存须 discarded"
        f" 409（consume 不刷新到期点），实际 {r.status_code}：{r.text[:250]!r}"
    )
    assert _error_code(r.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r.json() and "record_id" not in r.json()
    assert boxes["receipts"], "前置校验：Receipt 未被捕获"
    assert _field(boxes["receipts"][-1], "outcome") == "discarded", (
        f"过期首次完成 Receipt 须 discarded，实际 {boxes['receipts'][-1]!r}"
    )


def test_002_complete_at_and_after_confirmation_deadline(
    v2harness, monkeypatch
) -> None:
    """CR-P3：严格 now> ——恰等 deadline（t=400＝100+300）仍授权；越过
    （t=410.5>110+300=410，经计算期间推进、消费时点 399.5 仍合法）→ 首次
    未保存 discarded 409。两条确认各自命中自己的 deadline。"""
    clock = _ControlledClock(0.0)
    _install_clock(monkeypatch, clock)

    # —— 等号（==400）：授权 ——
    prepared = v2harness.prepare_ok()  # t=0
    clock.advance(100.0)
    confirmation_eq = v2harness.confirm_ok(prepared)  # created 100 → deadline 400
    clock.advance(300.0)  # t=400 恰等（等号子例须先执行——后续 prepare 会推进
    # revision 使本确认失效，属预期协议行为，非本节点目标条件）
    r_eq = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_eq}
    )
    assert r_eq.status_code == 200, (
        f"complete=400（等号）尚未过期 → 须授权，实际 {r_eq.status_code}："
        f"{r_eq.text[:250]!r}"
    )
    assert r_eq.json().get("record_id")

    # —— 越过（>710）：t=400 第二对 prepare/confirm（deadline 410+300=710）；
    #     t=709.5 消费仍合法（709.5-410=299.5），计算期间跨过 710 ——
    prepared2 = v2harness.prepare_ok()  # t=400
    clock.advance(10.0)
    confirmation_after = v2harness.confirm_ok(prepared2)  # t=410 → deadline 710
    clock.advance(299.5)  # t=709.5

    def _cross_deadline_during_compute() -> None:
        clock.advance(1.0)  # t=710.5 > 710

    r_after = _execute_with_barrier(
        monkeypatch, v2harness, confirmation_after, _cross_deadline_during_compute
    )
    assert r_after.status_code == 409, (
        f"complete 越过 deadline（710.5>710）→ 首次未保存须 discarded 409，"
        f"实际 {r_after.status_code}：{r_after.text[:250]!r}"
    )
    assert _error_code(r_after.json()) == "E_CONFIRMATION_STALE"
    assert "amounts" not in r_after.json() and "record_id" not in r_after.json()


def test_002_stored_completion_idempotency_survives_confirmation_expiry(
    v2harness, monkeypatch
) -> None:
    """CR-P4（review 7/14）：已成功保存的完成不因确认期限随后到期撤销——
    t=350 存储；t=500 过期后：HTTP 重复执行仍 CONSUMED＋原 record 链接；直驱
    **恰好存储的真实 core**（从 record 读回）→ already_stored＋同 record_id＋
    完整 Receipt（原 execution epoch/revision）；仅改一个 amount 的异 core →
    409 E_CONFIRMATION_CONSUMED 且原记录字节不变；clear 后同 context/core
    直驱 → 完整 discarded Receipt、无链接、旧 record 不可读。"""
    import copy as _copy

    from app.core.errors import AppError

    clock = _ControlledClock(0.0)
    _install_clock(monkeypatch, clock)

    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    prepared = v2harness.prepare_ok()  # t=0
    clock.advance(100.0)
    confirmation_id = v2harness.confirm_ok(prepared)  # deadline 400
    clock.advance(250.0)  # t=350
    r_exec = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, r_exec.text[:250]
    record_id = r_exec.json()["record_id"]
    assert boxes["contexts"] and boxes["receipts"], "前置校验：context/Receipt 未捕获"
    context = boxes["contexts"][-1]
    first_receipt = boxes["receipts"][-1]
    assert _field(first_receipt, "outcome") == "stored"
    stored_hash = _field(first_receipt, "core_hash")
    sid = _sid_of(v2harness)
    registry = v2harness.app.state.registry
    stored_record = registry.get_record(sid, record_id)
    assert stored_record is not None, "前置校验：已存 record 可读（EXACT core 来源）"
    exact_core = _copy.deepcopy(stored_record["core"])
    r_download0 = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download0.status_code == 200
    assert r_download0.json()["core_hash"] == stored_hash, (
        "前置校验：下载 core_hash 与首次完成 Receipt 一致"
    )

    clock.advance(150.0)  # t=500 > 400：确认已过期，record 不得被撤销
    r_replay = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_replay.status_code == 409, (
        f"过期后重复执行仍按已消费合同（409），实际 {r_replay.status_code}"
    )
    assert _error_code(r_replay.json()) == "E_CONFIRMATION_CONSUMED"
    assert r_replay.json().get("record_id") == record_id, (
        "过期不撤销已存完成的链接（005 §12.3：幂等合同不因到期撤销）"
    )
    r_record = v2harness.client.get(f"/api/v1/records/{record_id}")
    assert r_record.status_code == 200

    # —— 直驱 EXACT 存储 core（plan §3 冻结签名）——
    again = registry.complete_execution(
        context,
        _copy.deepcopy(exact_core),
        store=v2harness.app.state.store,
        guard=v2harness.app.state.update_scheduler,
    )
    assert _field(again, "outcome") == "already_stored", (
        f"过期后同 EXACT core 完成 → already_stored＋原 record，实际 {again!r}"
    )
    assert _field(again, "record_id") == record_id
    assert _field(again, "core_hash") == stored_hash, "幂等 Receipt 须带原 core_hash"
    assert _field(again, "execution_epoch") == _field(context, "session_epoch"), (
        f"already_stored Receipt 须带原执行 epoch（review 14），实际 "
        f"{_field(again, 'execution_epoch')!r}"
    )
    assert _field(again, "execution_revision") == _field(
        context, "input_revision"
    ), "already_stored Receipt 须带原执行 revision（review 14）"
    assert _field(again, "editor_result") == "current", (
        "无其它变化的同 core 重交 → editor_result=current（PQA-04；只有时间推进，"
        "stored 不因确认到期撤销，005 §12.3:692,695）"
    )

    conflict_core = _copy.deepcopy(exact_core)
    conflict_amounts = _kv(conflict_core, "amounts")
    assert isinstance(conflict_amounts, MutableMapping), (
        f"前置校验：存储 core.amounts 须为可变映射，实际 {type(conflict_amounts)!r}"
    )
    conflict_amounts["final_after_reduction"] = "9400"  # 仅改一个 amount 字段
    with pytest.raises(AppError) as excinfo:
        registry.complete_execution(
            context,
            conflict_core,
            store=v2harness.app.state.store,
            guard=v2harness.app.state.update_scheduler,
        )
    assert excinfo.value.code == "E_CONFIRMATION_CONSUMED", (
        f"过期后仅改一个 amount 的异 core → 冲突（不撤销原记录），实际 "
        f"{excinfo.value!r}"
    )
    r_download1 = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download1.status_code == 200
    assert r_download1.json()["core_hash"] == stored_hash, "冲突不得改写原记录字节"
    assert r_download1.json()["record"]["amounts"]["final_after_reduction"] == "940"

    # —— clear 优先：同 context/core 直驱 → 完整 discarded、无链接、旧 record 不可读 ——
    assert v2harness.client.delete("/api/v1/session").status_code == 200
    cleared = registry.complete_execution(
        context,
        _copy.deepcopy(exact_core),
        store=v2harness.app.state.store,
        guard=v2harness.app.state.update_scheduler,
    )
    assert _field(cleared, "outcome") == "discarded", (
        f"clear 后同 context/core 完成 → discarded（epoch 优先），实际 {cleared!r}"
    )
    assert _field(cleared, "record_id") is None, "discarded 不得携带 record 链接"
    assert _field(cleared, "core_hash") is None
    assert _field(cleared, "editor_result") == "discarded"
    assert _field(cleared, "execution_epoch") == _field(context, "session_epoch"), (
        "discarded Receipt 亦须携带原执行 epoch（字段完整）"
    )
    assert _field(cleared, "execution_revision") == _field(
        context, "input_revision"
    ), "discarded Receipt 亦须携带原执行 revision（字段完整）"
    assert registry.get_record(sid, record_id) is None, "clear 后旧 record 不可读"
    assert v2harness.client.get(f"/api/v1/records/{record_id}").status_code == 404


# ---------------------------------------------------------------------------
# guard-contract §5 十二组（G9 由首批 B5/B6a/B6b 承载：consume 入口屏障＋
# 锁内重读）。全部经现有 HTTP/registry 行为边界；runtime 内部值（GuardRead/
# reason_codes 排序/封闭 context 键）不在此冒充行为，列接口 held。
# ---------------------------------------------------------------------------

_V1_PREPARE_BODIES: dict[str, dict] = {
    # 五税 v1 最小合法载荷（schema_versions 同源事实；仅作 prepare 期间计数）
    "profits_tax": {
        "year_of_assessment": "2025_26",
        "entity_kind": "sole_proprietorship",
        "assessable_profit": "600000",
    },
    "property_tax": {
        "year_of_assessment": "2025_26",
        "properties": [
            {
                "property_id": "p-1",
                "rent_received": "120000",
                "rates_paid_by_owner": {
                    "amount": "0",
                    "agreed": {"value": False},
                    "actually_paid": {"value": False},
                },
                "irrecoverable_rent": "0",
                "deposit_offsets": "0",
            }
        ],
    },
    "stamp_stock": {
        "instrument_date": "2025-06-10",
        "complete": {"value": True},
        "documents": [
            {
                "doc_kind": "contract_note_sold",
                "consideration": "50000",
                "value": "50000",
            }
        ],
    },
    "stamp_lease": {
        "instrument_date": "2025-06-20",
        "term_kind": "fixed",
        "term_start": "2025-07-01",
        "term_end": "2027-06-30",
        "rent_input_mode": "fixed_monthly",
        "monthly_rent": "10000",
        "premium": "0",
    },
}


def _v2_pa_body_local() -> dict:
    """最小合法 PA v2 信封（schema_versions §5.2 同源；供期间计数）。"""
    return {
        "tax_type": "personal_assessment",
        "schema_version": "2.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
            "years": {
                "2025_26": {
                    "participants": [{"person_id": "p1", "role": "self"}],
                    "subject_facts": {
                        "p1": {
                            "ordinarily_resident_hk": [
                                {
                                    "from": "2026-06-01",
                                    "to": "2026-06-02",
                                    "value": True,
                                }
                            ]
                        }
                    },
                    "household": {
                        "marriage": [
                            {"from": "2025-04-01", "to": "2026-04-01", "value": "unmarried"},
                            {"from": "2026-06-01", "to": "2026-06-02", "value": "unmarried"},
                        ],
                        "living_apart": [],
                        "child_nomination": {"person_id": None, "status": "none"},
                    },
                    "relations": [],
                    "financials": {
                        "p1": {
                            "employment_income_nai": "250000",
                            "mpf_mandatory_contributions": "0",
                            "has_assessable_salary_income": True,
                            "has_assessable_property_income": True,
                            "has_assessable_business_income": False,
                            "has_income_assessable_under_ordinance": True,
                            "source_classification": {
                                "salary": "confirmed_in_scope",
                                "property": "confirmed_in_scope",
                                "business": "confirmed_in_scope",
                            },
                            "properties": [
                                {
                                    "property_id": "h1",
                                    "share": "1/1",
                                    "rent_received": "120000",
                                    "rates_paid_by_owner": {
                                        "amount": "0",
                                        "agreed": False,
                                        "actually_paid": False,
                                    },
                                    "irrecoverable_rent": "0",
                                    "deposit_offsets": "0",
                                    "mortgage_interest": "0",
                                }
                            ],
                            "businesses": [],
                            "deduction_items": [],
                        }
                    },
                    "care_payments": [],
                    "claims": [],
                    "assessment": {
                        "reference_date": "2026-06-01",
                        "basis": "hypothetical",
                        "actual": {
                            "salary_joint_elected": False,
                            "salary_joint_assessed": False,
                            "pa": {"p1": {"mode": "none"}},
                            "history": "ordinary",
                        },
                        "candidates": ["no_pa", "pa_self"],
                        "selection": "best",
                    },
                    "manual_issues": [],
                    "provisional_paid": "0",
                }
            },
            "hk_presence_days": [],
            "assumptions": [],
        },
    }


def test_002_r2_g1_period_table_and_stamp_april_boundary(v2harness) -> None:
    """G1＋G12(a)（prepare 阶段计数；guard-plan-review 约束：阶段分清——
    consume 阶段计数由首批 B5/B6a/B6b/B7 承载）：两税恰查 current+next 两期、
    五税恰一期；印花 3-31/4-1 边界各归其 YA。"""
    real = v2harness.app.state.update_scheduler
    expectations: list[tuple[str, dict, set[str]]] = [
        ("salaries_tax", _v2_salary_body(), {"2025_26", "2026_27"}),
        ("personal_assessment", _v2_pa_body_local(), {"2025_26", "2026_27"}),
        ("profits_tax", _V1_PREPARE_BODIES["profits_tax"], {"2025_26"}),
        ("property_tax", _V1_PREPARE_BODIES["property_tax"], {"2025_26"}),
        ("stamp_stock", _V1_PREPARE_BODIES["stamp_stock"], {"2025_26"}),
        ("stamp_lease", _V1_PREPARE_BODIES["stamp_lease"], {"2025_26"}),
    ]
    for tax_type, facts, expected_periods in expectations:
        guard = _RecordingGuard({"*": {"mode": "ok", "notice": None, "states": []}})
        v2harness.app.state.update_scheduler = guard
        try:
            schema = "2.0.0" if tax_type in ("salaries_tax", "personal_assessment") else "1.0.0"
            request_body = (
                facts
                if schema == "2.0.0"
                else {"tax_type": tax_type, "schema_version": schema, "input": facts}
            )
            r = v2harness.client.post(
                _PREPARE,
                json_body=request_body,
                headers=v2harness.fresh_if_match(),
            )
            assert r.status_code == 201, (
                f"{tax_type}：ok guard 下 prepare 须 201，实际 {r.status_code}："
                f"{r.text[:200]!r}"
            )
            assert set(guard.queries) == expected_periods, (
                f"{tax_type}：prepare 阶段查询期间须恰为 {sorted(expected_periods)}，"
                f"实际 {guard.queries!r}（G1：两税恰 2 期、五税恰 1 期）"
            )
        finally:
            v2harness.app.state.update_scheduler = real

    # —— 印花 4 月边界（stamp_property：3-31 → 2024_25；4-01 → 2025_26）——
    for date, expected in (("2025-03-31", {"2024_25"}), ("2025-04-01", {"2025_26"})):
        guard = _RecordingGuard({"*": {"mode": "ok", "notice": None, "states": []}})
        v2harness.app.state.update_scheduler = guard
        try:
            body = {
                "instrument_date": date,
                "instrument_kind": "agreement_for_sale",
                "property_class": "residential",
                "consideration": "6000000",
                "value": "6000000",
            }
            r = v2harness.client.post(
                _PREPARE,
                json_body={
                    "tax_type": "stamp_property",
                    "schema_version": "1.0.0",
                    "input": body,
                },
                headers=v2harness.fresh_if_match(),
            )
            assert r.status_code == 201, (
                f"stamp_property {date}：prepare 须 201，实际 {r.status_code}："
                f"{r.text[:200]!r}"
            )
            assert set(guard.queries) == expected, (
                f"stamp_property {date}：4 月边界须归 {expected}，实际 "
                f"{guard.queries!r}"
            )
        finally:
            v2harness.app.state.update_scheduler = real


def test_002_r2_g2_offline_fixed_template_and_blocked_reject(v2harness) -> None:
    """G2：offline 判定可用但提示必须为冻结固定模板（不依赖 provider notice
    排版；缺 notice 亦然）；blocked 拒绝。"""
    real = v2harness.app.state.update_scheduler
    template = _OFFLINE_TEMPLATE.format(ya="2025_26")
    variants = [
        ("provider notice", {"2025_26": {"mode": "trusted_offline", "notice": "排版A-来源特定提示"}, "2026_27": {"mode": "ok", "notice": None, "states": []}}),
        ("缺 notice", {"2025_26": {"mode": "trusted_offline"}, "2026_27": {"mode": "ok", "notice": None, "states": []}}),
    ]
    try:
        for label, verdicts in variants:
            v2harness.app.state.update_scheduler = _RecordingGuard(verdicts)
            r = v2harness.client.post(
                _PREPARE,
                json_body=_v2_salary_body(),
                headers=v2harness.fresh_if_match(),
            )
            assert r.status_code == 201, (
                f"offline（{label}）可算不拒：须 201，实际 {r.status_code}："
                f"{r.text[:200]!r}"
            )
            warnings = r.json().get("warnings") or []
            assert template in warnings, (
                f"offline（{label}）提示须为冻结固定模板（guard-contract §3），"
                f"实际 warnings={warnings!r}"
            )
        v2harness.app.state.update_scheduler = _BlockedScheduler()
        r_blocked = v2harness.client.post(
            _PREPARE,
            json_body=_v2_salary_body(),
            headers=v2harness.fresh_if_match(),
        )
        assert r_blocked.status_code == 422 and _error_code(
            r_blocked.json()
        ) == "E_RULES_STATE_UNVERIFIABLE"
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g3_real_rollback_empty_states_blocks_current(
    v2harness, tmp_path
) -> None:
    """G3：真实 rollback 路径（隔离本地库 rollback audit → states=[]/blocked）
    仍拒 current；不以伪造 states 掩盖、不因空 states 改 ok。"""
    db = tmp_path / "rules.db"  # 与 v2harness fixture 同一临时库
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO audit_log (event_type, detail_json, created_at)"
            " VALUES ('rollback', ?, ?)",
            (
                json.dumps({"affected_periods": ["2025_26"]}, ensure_ascii=False),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    scheduler = v2harness.app.state.update_scheduler  # 安全件（availability 委托真实）
    verdict = scheduler.availability("2025_26")
    assert verdict.get("mode") == "blocked" and verdict.get("states") == [], (
        f"前置校验：真实 rollback 须产生 mode=blocked/states=[]（scheduler "
        f":416–424 实际路径），实际 {verdict!r}"
    )
    r = v2harness.client.post(
        _PREPARE, json_body=_v2_salary_body(), headers=v2harness.fresh_if_match()
    )
    assert r.status_code == 422, (
        f"真实 rollback（states=[]）须拒 current 计算，实际 {r.status_code}"
    )
    assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE"
    assert "prepared_id" not in r.json()


@pytest.mark.parametrize(
    "cell",
    ["missing_guard", "unknown_mode", "malformed_states", "query_exception"],
)
def test_002_r2_g4_current_guard_errors_reject_no_capability(v2harness, cell) -> None:
    """G4：current 期 guard 缺席/未知 mode/states 畸形/查询普通异常（参数化）
    → 拒绝且不签发能力（missing/unknown 首批已覆盖，此处按 §5.4 成组参数化）。"""
    real = v2harness.app.state.update_scheduler
    try:
        if cell == "missing_guard":
            v2harness.app.state.update_scheduler = object()
        elif cell == "unknown_mode":
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {"*": {"mode": "celestial"}}
            )
        elif cell == "malformed_states":
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {"*": {"mode": "ok", "states": "not-a-list"}}
            )
        else:  # query_exception
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {"*": RuntimeError("guard 查询注入异常-G4")}
            )
        r = v2harness.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 422, (
            f"{cell}：current guard 不可核验须 422 E_RULES_STATE_UNVERIFIABLE"
            f"（不泄露异常/不放行），实际 {r.status_code}：{r.text[:200]!r}"
        )
        assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE"
        assert "prepared_id" not in r.json()
    finally:
        v2harness.app.state.update_scheduler = real


@pytest.mark.parametrize("cell", ["unknown_mode", "malformed_states", "query_exception"])
def test_002_r2_g5_next_only_guard_errors_localized(v2harness, cell) -> None:
    """G5：同一组错误仅发生在 next → 本年可 prepare/消费、next 被局部抑制
    （next blocked/null）；不得把整体不可核验当本年拒绝。"""
    real = v2harness.app.state.update_scheduler
    ok = {"mode": "ok", "notice": None, "states": []}
    next_verdicts = {
        "unknown_mode": {"mode": "celestial"},
        "malformed_states": {"mode": "ok", "states": "not-a-list"},
        "query_exception": RuntimeError("guard 查询注入异常-G5"),
    }
    verdicts = {"2025_26": ok, "2026_27": next_verdicts[cell]}
    v2harness.app.state.update_scheduler = _RecordingGuard(verdicts)
    try:
        body = _dual_year_body()  # next 可计算：使 next 抑制可观察
        r = v2harness.client.post(
            _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 201, (
            f"{cell}：仅 next 不可核验不得拒本年（G5），实际 {r.status_code}："
            f"{r.text[:200]!r}"
        )
        confirmation_id = v2harness.confirm_ok(r.json())
        r_exec = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_exec.status_code == 200, (
            f"{cell}：本年仍须可执行，实际 {r_exec.status_code}"
        )
        amounts = r_exec.json().get("amounts") or {}
        assert amounts.get("final_after_reduction") == "940", (
            f"{cell}：本年 final 须保留，实际 {amounts!r}"
        )
        assert amounts.get("next_provisional") is None, (
            f"{cell}：next 须局部抑制（null），实际 "
            f"{amounts.get('next_provisional')!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g6_last_supported_current_year_next_missing_rules(v2harness) -> None:
    """G6＋guard-plan-review 约束2：current=2026_27（最末支持年）仍查
    next=2027_28；不得因 next 无规则拒本年或扩支持年；next 仅局部不可用。

    独立手算（Annex 005 §15.2 矩阵）：NAI 240000−MPF 9000−basic 145000
    ＝86000；累进 50000×2%＋36000×6%＝3160；标准 86000×15%＝12900 →
    择低 3160；2026/27 宽减 not_applicable → final=3160。"""
    import copy as _copy

    body = _v2_salary_body()
    body["input"]["year_of_assessment"] = "2026_27"
    year26 = _copy.deepcopy(body["input"]["years"]["2025_26"])
    year26["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    body["input"]["years"] = {"2026_27": year26}
    guard = _RecordingGuard({"*": {"mode": "ok", "notice": None, "states": []}})
    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = guard
    try:
        r = v2harness.client.post(
            _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 201, (
            f"current=2026_27 不得因 next(2027_28) 无规则而拒本年，实际 "
            f"{r.status_code}：{r.text[:250]!r}"
        )
        assert "2027_28" in guard.queries, (
            f"两税须查询紧邻 next（2027_28），实际 {guard.queries!r}"
        )
        confirmation_id = v2harness.confirm_ok(r.json())
        r_exec = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_exec.status_code == 200, r_exec.text[:250]
        amounts = r_exec.json().get("amounts") or {}
        assert amounts.get("before_reduction") == "3160", amounts
        assert amounts.get("reduction") == "0", amounts
        assert amounts.get("final_after_reduction") == "3160", (
            f"2026/27 current 金额须保留（手算 3160），实际 {amounts!r}"
        )
        assert amounts.get("next_provisional") is None, (
            f"next=2027_28 缺规则仅局部不可用（missing_rules），实际 "
            f"{amounts.get('next_provisional')!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g7_guard_key_equivalence_vs_mode_change(v2harness, monkeypatch) -> None:
    """G7：等价——notice 排版/states 重排重复不改变判定 key（current 保持）；
    mode 变化（offline↔ok）→ key 变化 → historical。"""
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    real = v2harness.app.state.update_scheduler
    ok = {"mode": "ok", "notice": None, "states": []}
    offline_a = {
        "mode": "trusted_offline",
        "notice": "排版A-等价性对照",
        "states": ["trusted_offline"],
    }
    offline_b = {
        "mode": "trusted_offline",
        "notice": "排版B-仅文案不同",
        "states": ["trusted_offline", "trusted_offline"],
    }

    # —— 等价（offline A → 消费；offline B → 完成：同 mode＋同去重 state 集）——
    v2harness.app.state.update_scheduler = _RecordingGuard(
        {"2025_26": offline_a, "2026_27": ok, "*": ok}
    )
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)

    def _swap_to_equivalent() -> None:
        v2harness.app.state.update_scheduler = _RecordingGuard(
            {"2025_26": offline_b, "2026_27": ok, "*": ok}
        )

    try:
        r = _execute_with_barrier(
            monkeypatch, v2harness, confirmation_id, _swap_to_equivalent
        )
        assert r.status_code == 200, r.text[:250]
        assert boxes["receipts"], "前置校验：Receipt 未捕获"
        assert _field(boxes["receipts"][-1], "editor_result") == "current", (
            "notice 排版/states 重排重复 → guard key 不变 → 仍 current（G7），"
            f"实际 {_field(boxes['receipts'][-1], 'editor_result')!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real

    # —— 变化（offline → ok：mode 变化）——
    boxes["receipts"].clear()
    v2harness.app.state.update_scheduler = _RecordingGuard(
        {"2025_26": offline_a, "2026_27": ok, "*": ok}
    )
    try:
        prepared2 = v2harness.prepare_ok()
        confirmation2 = v2harness.confirm_ok(prepared2)

        def _swap_mode_changed() -> None:
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {"*": ok}
            )

        r2 = _execute_with_barrier(
            monkeypatch, v2harness, confirmation2, _swap_mode_changed
        )
        assert r2.status_code == 200, r2.text[:250]
        assert _field(boxes["receipts"][-1], "editor_result") == "historical", (
            "mode 变化（offline→ok）→ guard key 变化 → historical（G7），"
            f"实际 {_field(boxes['receipts'][-1], 'editor_result')!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g8_irrelevant_period_current_vs_real_global_offline_historical(
    v2harness, monkeypatch, tmp_path
) -> None:
    """G8：无关期间变更且 verdict 不变 → current（首批 D13[irrelevant] 锚）；
    全局 offline 真改变所查期间 verdict（真实 source_states 行）→ historical。"""
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)

    def _make_really_offline() -> None:
        # 真实全局降级路径：插入 trusted_offline 状态行（scheduler :405 全局判定）
        conn = sqlite3.connect(tmp_path / "rules.db")
        try:
            conn.execute(
                "INSERT INTO updater_source_states"
                " (source_id, state, period, entered_at) VALUES (?,?,?,?)",
                ("src-redp-g8", "trusted_offline", "", "2026-01-01T00:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()
        verdict = v2harness.app.state.update_scheduler.availability("2025_26")
        assert verdict.get("mode") == "trusted_offline", (
            f"前置校验：真实状态行使所查期间实际变 offline，实际 {verdict!r}"
        )

    r = _execute_with_barrier(
        monkeypatch, v2harness, confirmation_id, _make_really_offline
    )
    assert r.status_code == 200, (
        f"guard 真变化不拒算/不撤回（仅 historical），实际 {r.status_code}"
    )
    assert boxes["receipts"], "前置校验：Receipt 未捕获"
    assert _field(boxes["receipts"][-1], "editor_result") == "historical", (
        f"全局 offline 真改变所查期间 verdict → historical（G8/§4 注），实际 "
        f"{_field(boxes['receipts'][-1], 'editor_result')!r}"
    )
    amounts = r.json().get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", (
        f"金额仍须来自原快照，实际 {amounts!r}"
    )


def test_002_r2_g10_next_guard_change_during_compute_historical(
    v2harness, monkeypatch
) -> None:
    """G10：计算期间仅 next guard 变 blocked → Receipt 必 historical；
    record/input/binding/金额仍来自原快照（current final 940）。"""
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    ok = {"mode": "ok", "notice": None, "states": []}
    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _RecordingGuard({"*": ok})
    try:
        body = _dual_year_body()
        r = v2harness.client.post(
            _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 201, r.text[:250]
        confirmation_id = v2harness.confirm_ok(r.json())
        original_binding = dict(r.json()["expected_binding"])

        def _block_next_only() -> None:
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {
                    "2025_26": ok,
                    "2026_27": {"mode": "blocked", "notice": "next 变化-G10", "states": ["expired"]},
                }
            )

        r_exec = _execute_with_barrier(
            monkeypatch, v2harness, confirmation_id, _block_next_only
        )
        assert r_exec.status_code == 200, r_exec.text[:250]
        assert boxes["receipts"], "前置校验：Receipt 未捕获"
        assert _field(boxes["receipts"][-1], "editor_result") == "historical", (
            f"相关 next guard 变化 → historical（G10），实际 "
            f"{_field(boxes['receipts'][-1], 'editor_result')!r}"
        )
        amounts = r_exec.json().get("amounts") or {}
        assert amounts.get("final_after_reduction") == "940", (
            f"current 金额仍取原快照，实际 {amounts!r}"
        )
        assert r_exec.json()["binding"] == original_binding
        r_record = v2harness.client.get(
            f"/api/v1/records/{r_exec.json()['record_id']}/download"
        )
        assert r_record.status_code == 200
        assert r_record.json()["record"]["binding"] == original_binding
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g11_completion_guard_exception_historical_still_stored(
    v2harness, monkeypatch
) -> None:
    """G11：completion 阶段 guard 查询抛普通异常 → 仅 historical、仍成功保存
    （不得 current、不得 500/丢弃合法历史；最新不可核验也不得 current）。"""
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    ok = {"mode": "ok", "notice": None, "states": []}
    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _RecordingGuard({"*": ok})
    try:
        prepared = v2harness.prepare_ok()
        confirmation_id = v2harness.confirm_ok(prepared)

        def _guard_starts_raising() -> None:
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {"*": RuntimeError("completion 查询异常-G11")}
            )

        r = _execute_with_barrier(
            monkeypatch, v2harness, confirmation_id, _guard_starts_raising
        )
        assert r.status_code == 200, (
            f"completion guard 不可核验只禁 current（不 500/不丢弃），实际 "
            f"{r.status_code}：{r.text[:250]!r}"
        )
        assert boxes["receipts"], "前置校验：Receipt 未捕获"
        receipt = boxes["receipts"][-1]
        assert _field(receipt, "outcome") == "stored"
        assert _field(receipt, "editor_result") == "historical", (
            f"最新 guard 不可核验 → 不得 current（G11），实际 "
            f"{_field(receipt, 'editor_result')!r}"
        )
        amounts = r.json().get("amounts") or {}
        assert amounts.get("final_after_reduction") == "940", amounts
        r_record = v2harness.client.get(
            f"/api/v1/records/{_field(receipt, 'record_id')}"
        )
        assert r_record.status_code == 200
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g12_five_tax_current_only_and_notice_not_rewritten(
    v2harness, monkeypatch
) -> None:
    """G12：五税仅查当前期（prepare/execute 各恰一次）；offline 固定模板提示
    进入响应且不被 completion 期 notice 改写；两税 context 封闭键/reason 排序
    属 runtime 内部值（接口 held，见 addendum）。"""
    profits_input = _V1_PREPARE_BODIES["profits_tax"]
    template = _OFFLINE_TEMPLATE.format(ya="2025_26")
    offline_a = {"mode": "trusted_offline", "notice": "五税排版A-G12"}
    ok = {"mode": "ok", "notice": None, "states": []}

    # —— 计数（ok，review 13）：prepare 恰 1；consume 恰 1；completion 恰 1
    #    （分 phase 计数，不是把 consume＋completion 当整 execute 一次）——
    real = v2harness.app.state.update_scheduler
    guard = _RecordingGuard({"*": ok})
    v2harness.app.state.update_scheduler = guard
    registry = v2harness.app.state.registry
    marks: dict[str, int] = {}
    real_consume = registry.consume_for_execution
    real_complete = registry.complete_execution

    def _wrapped_consume(*args: Any, **kwargs: Any):
        marks["consume_start"] = len(guard.queries)
        context = real_consume(*args, **kwargs)
        marks["consume_end"] = len(guard.queries)
        return context

    def _wrapped_complete(*args: Any, **kwargs: Any):
        marks["complete_start"] = len(guard.queries)
        receipt = real_complete(*args, **kwargs)
        marks["complete_end"] = len(guard.queries)
        return receipt

    monkeypatch.setattr(registry, "consume_for_execution", _wrapped_consume)
    monkeypatch.setattr(registry, "complete_execution", _wrapped_complete)
    try:
        r = v2harness.client.post(
            _PREPARE,
            json_body={"tax_type": "profits_tax", "schema_version": "1.0.0", "input": profits_input},
            headers=v2harness.fresh_if_match(),
        )
        assert r.status_code == 201, r.text[:250]
        prepare_queries = list(guard.queries)
        confirmation_id = v2harness.confirm_ok(r.json())
        r_exec = v2harness.client.post(
            _PROFITS_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_exec.status_code == 200, r_exec.text[:250]
    finally:
        v2harness.app.state.update_scheduler = real
    assert "consume_start" in marks and "complete_start" in marks, (
        f"前置校验：consume/complete 未被经过，实际 marks={marks!r}"
    )
    consume_queries = guard.queries[marks["consume_start"]:marks["consume_end"]]
    completion_queries = guard.queries[marks["complete_start"]:marks["complete_end"]]
    assert prepare_queries == ["2025_26"], (
        f"五税 prepare 恰查一期（无 next），实际 {prepare_queries!r}"
    )
    assert consume_queries == ["2025_26"], (
        f"五税 consume 阶段恰查当前期一次（无 next；G12），实际 {consume_queries!r}"
    )
    assert completion_queries == ["2025_26"], (
        f"五税 completion 阶段恰查当前期一次（guard-only 比较；G12/§4），"
        f"实际 {completion_queries!r}"
    )

    # —— offline 模板＋不因 completion 期 notice 改写 ——
    v2harness.app.state.update_scheduler = _RecordingGuard({"*": offline_a})
    try:
        r2 = v2harness.client.post(
            _PREPARE,
            json_body={"tax_type": "profits_tax", "schema_version": "1.0.0", "input": profits_input},
            headers=v2harness.fresh_if_match(),
        )
        assert r2.status_code == 201, r2.text[:250]
        confirmation2 = v2harness.confirm_ok(r2.json())

        def _notice_changes_late() -> None:
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {"*": {"mode": "trusted_offline", "notice": "completion 期排版B-不得改写"}}
            )

        import app.api.routes as routes_module

        real_engine = routes_module.run_engine

        def _change_notice_before_compute(*args: Any, **kwargs: Any):
            _notice_changes_late()
            return real_engine(*args, **kwargs)

        monkeypatch.setattr(routes_module, "run_engine", _change_notice_before_compute)
        r2_exec = v2harness.client.post(
            _PROFITS_EXECUTE, json_body={"confirmation_id": confirmation2}
        )
        assert r2_exec.status_code == 200, r2_exec.text[:250]
        warnings = r2_exec.json().get("warnings") or []
        assert template in warnings, (
            f"五税 offline 提示须为冻结固定模板（G12），实际 {warnings!r}"
        )
        assert not any("不得改写" in str(w) for w in warnings), (
            f"消费时冻结提示不得被 completion 期 notice 改写，实际 {warnings!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real


# ---------------------------------------------------------------------------
# D16 行为锚补强（P4 还欠的行为证据：fail_execution 终态观察；
# 响应发送期异常不改 failed）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case,stage,kind", _D16_CASES)
def test_002_r2_d16b_fail_execution_terminal_observed(
    v2harness, monkeypatch, case, stage, kind
) -> None:
    """D16b（行为锚，不依赖新签名）：engine/core 阶段 AppError/普通异常后，
    fail_execution 须被调用（终态 failed 标记）且权威状态＝已 consumed、无
    record 链接（get_consumption 既有合同键），不得后补 record。"""
    import app.api.routes as routes_module
    from app.core.errors import AppError

    registry = v2harness.app.state.registry
    fail_calls: list = []
    real_fail = registry.fail_execution

    def _spy(*args: Any, **kwargs: Any):
        fail_calls.append((args, kwargs))
        return real_fail(*args, **kwargs)

    monkeypatch.setattr(registry, "fail_execution", _spy)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    sid = _sid_of(v2harness)

    def _raise_engine(*args: Any, **kwargs: Any):
        if kind == "apperror":
            raise AppError("E_RULES_STATE_UNVERIFIABLE", "测试注入：引擎业务错误")
        raise RuntimeError("秘密细节-D16b2")

    def _raise_core(*args: Any, **kwargs: Any):
        if kind == "apperror":
            raise AppError("E_RULES_STATE_UNVERIFIABLE", "测试注入：core 阶段业务错误")
        raise RuntimeError("秘密细节-D16b2-core")

    if stage == "engine":
        monkeypatch.setattr(routes_module, "run_engine", _raise_engine)
    else:
        monkeypatch.setattr(routes_module, "build_core", _raise_core)
    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r.status_code in (422, 500), f"{case}：注入异常须失败，实际 {r.status_code}"
    assert len(fail_calls) >= 1, (
        f"{case}：fail_execution 须被调用（异常进入 failed 终态；P4 行为证据），"
        f"实际调用 {len(fail_calls)} 次"
    )
    consumption = registry.get_consumption(sid, confirmation_id)
    assert consumption is not None, "get_consumption 须返回既有只读副本"
    assert consumption["consumed"] is True, (
        f"{case}：仍 consumed（不恢复能力），实际 {consumption!r}"
    )
    assert consumption["record_id"] is None, (
        f"{case}：failed 终态不得有 record 链接（不能后补），实际 {consumption!r}"
    )


def _drive_execute_with_send_failure(
    app: Any, cookie_value: str, csrf_token: str, confirmation_id: str
) -> dict:
    """直接驱动 ASGI（本文件 local）：响应 body 首块发送时抛受控普通异常
    （record 已在路由内存储完成）；异常在驱动层捕获，不改生产响应代码。"""
    from app.config import SESSION_COOKIE_NAME

    async def _run() -> dict:
        body_bytes = json.dumps(
            {"confirmation_id": confirmation_id}, ensure_ascii=False
        ).encode("utf-8")
        raw_headers = [
            (b"host", b"127.0.0.1:8000"),
            (b"origin", b"http://127.0.0.1:8000"),
            (b"x-csrf-token", csrf_token.encode("ascii")),
            (b"content-type", b"application/json"),
            (b"cookie", f"{SESSION_COOKIE_NAME}={cookie_value}".encode("ascii")),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": _SALARIES_EXECUTE,
            "raw_path": _SALARIES_EXECUTE.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": ("127.0.0.1", 45678),
            "server": ("127.0.0.1", 8000),
        }
        outcome: dict[str, Any] = {"started": None, "send_error": None}

        async def receive() -> dict:
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                outcome["started"] = message["status"]
                return
            if message["type"] == "http.response.body":
                raise RuntimeError("响应发送期注入异常-D16c")

        try:
            await app(scope, receive, send)
        except Exception as exc:  # 应用层重抛的发送期异常（受控）
            outcome["send_error"] = f"{type(exc).__name__}: {exc}"
        return outcome

    return asyncio.run(_run())


def test_002_r2_d16c_send_failure_after_store_keeps_terminal(v2harness, monkeypatch) -> None:
    """D16c（Green/Red 锚）：record 已存储后响应发送期普通异常 → 存储与链接
    保留、幂等可答、fail_execution 不被调用（成功终态不改 failed）。
    seam＝现有 ASGI send 调用层（无新增生产 hook）。"""
    registry = v2harness.app.state.registry
    fail_calls: list = []
    real_fail = registry.fail_execution

    def _spy(*args: Any, **kwargs: Any):
        fail_calls.append((args, kwargs))
        return real_fail(*args, **kwargs)

    monkeypatch.setattr(registry, "fail_execution", _spy)
    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    sid = _sid_of(v2harness)
    csrf_token = v2harness.client._token()

    outcome = _drive_execute_with_send_failure(
        v2harness.app, v2harness.client.cookie_value, csrf_token, confirmation_id
    )
    assert outcome["started"] == 200, (
        f"前置校验：record 存储完成、响应开始（200）后才注入，实际 "
        f"{outcome!r}"
    )
    assert outcome["send_error"] is not None, "前置校验：发送期异常未命中"

    consumption = registry.get_consumption(sid, confirmation_id)
    assert consumption is not None and consumption["consumed"] is True
    record_id = consumption["record_id"]
    assert record_id, "发送期异常不得丢失已存 record 链接"
    assert not fail_calls, (
        f"已成功存储后的响应发送异常不得改为 failed（调用 {len(fail_calls)} 次）"
    )
    r_replay = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_replay.status_code == 409 and _error_code(
        r_replay.json()
    ) == "E_CONFIRMATION_CONSUMED"
    assert r_replay.json().get("record_id") == record_id, "幂等链接保留"
    r_download = v2harness.client.get(f"/api/v1/records/{record_id}/download")
    assert r_download.status_code == 200 and r_download.json()["record"][
        "amounts"
    ]["final_after_reduction"] == "940"


# ===========================================================================
# P1 r2 red-protocol-completion 追加（completion-plan 有限事项 2–7）：
# fine guard（reason 集合/状态矛盾/notice 模板/current-vs-next/封闭 context＋
# warnings 独立）；prepare 单快照锁探针与阶段计数；completion guard-only
# freshness（missing_rules 掩蔽/冻结即不可核验）；runtime 声明接口（缺失＝
# 接口型 Red）。P4/CR-P 既有节点保留（D14/D16/D16b/D16c/CR-P1–4 不重造）。
# ===========================================================================


_G13_OK = {"mode": "ok", "notice": None, "states": []}


class _NonCallableAvailabilityGuard:
    """availability 属性存在但不可调用（guard-contract §2：与缺席同等不可
    核验，不因属性存在而放行）。"""

    availability = "not-callable"


_G13_CASES: list[tuple[str, Any, int]] = [
    ("unknown_state", {"mode": "ok", "states": ["mystery_state_zz"]}, 422),
    (
        "ok_with_blocking_state",
        {"mode": "ok", "states": ["related_change_uncertain"]},
        422,
    ),
    (
        "ok_with_trusted_offline_state",
        {"mode": "ok", "states": ["trusted_offline"]},
        422,
    ),
    (
        "offline_with_blocking_state",
        {"mode": "trusted_offline", "states": ["expired"]},
        422,
    ),
    ("blocked_empty_states", {"mode": "blocked", "states": []}, 422),
    ("blocked_with_state", {"mode": "blocked", "states": ["unknown"]}, 422),
    ("states_omitted_ok", {"mode": "ok"}, 201),
    ("states_explicit_empty_ok", {"mode": "ok", "states": []}, 201),
    (
        "offline_notice_blank",
        {"mode": "trusted_offline", "notice": "   ", "states": ["trusted_offline"]},
        201,
    ),
    (
        "offline_notice_nonstring",
        {"mode": "trusted_offline", "notice": 123, "states": ["trusted_offline"]},
        201,
    ),
    ("noncallable_availability", "__noncallable__", 422),
]


@pytest.mark.parametrize(
    "case,verdict,expected_status",
    _G13_CASES,
    ids=[case for case, _verdict, _status in _G13_CASES],
)
def test_002_r2_g13_guard_fine_verdict_matrix(
    v2harness, case, verdict, expected_status
) -> None:
    """G13（behavior；guard-contract §2）：有限 mode/state 映射与状态矛盾——
    未知 state、ok/offline 携带 blocking/offline state 均判 malformed 拒算；
    blocked＋[] 合法仍拒 current；states 缺省/显式 [] 合法；notice 缺失/
    空白/非字符串不改变已明确 mode/states 的判定（offline 固定期间模板）。"""
    real = v2harness.app.state.update_scheduler
    if verdict == "__noncallable__":
        v2harness.app.state.update_scheduler = _NonCallableAvailabilityGuard()
    else:
        v2harness.app.state.update_scheduler = _RecordingGuard(
            {"2025_26": verdict, "2026_27": _G13_OK, "*": _G13_OK}
        )
    try:
        r = v2harness.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=v2harness.fresh_if_match()
        )
        assert r.status_code == expected_status, (
            f"{case}：预期 {expected_status}（fine guard 有限映射），实际 "
            f"{r.status_code}：{r.text[:250]!r}"
        )
        if expected_status == 422:
            assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE", (
                f"{case}：不可核验须 E_RULES_STATE_UNVERIFIABLE，实际 "
                f"{r.text[:200]!r}"
            )
            assert "prepared_id" not in r.json(), f"{case}：不得签发能力"
        elif isinstance(verdict, dict) and verdict.get("mode") == "trusted_offline":
            warnings = r.json().get("warnings") or []
            assert _OFFLINE_TEMPLATE.format(ya="2025_26") in warnings, (
                f"{case}：offline 固定期间模板须保留（notice 缺失/空白/非字符"
                f"串不覆盖判定），实际 warnings={warnings!r}"
            )
    finally:
        v2harness.app.state.update_scheduler = real


_G14_CASES: list[tuple[str, Any, Any, str, tuple, str, tuple]] = [
    (
        "ok_ok",
        _G13_OK, _G13_OK,
        "available", ("GUARD_OK",), "available", ("GUARD_OK",),
    ),
    (
        "offline_dupes",
        {
            "mode": "trusted_offline",
            "notice": "G14-文案不改判定",
            "states": ["trusted_offline", "trusted_offline"],
        },
        _G13_OK,
        "available",
        ("GUARD_STATE_TRUSTED_OFFLINE", "GUARD_TRUSTED_OFFLINE"),
        "available", ("GUARD_OK",),
    ),
    (
        "next_blocked_empty",
        _G13_OK, {"mode": "blocked", "states": []},
        "available", ("GUARD_OK",), "blocked", ("GUARD_BLOCKED",),
    ),
    (
        "next_blocked_sorted_dedup",
        _G13_OK, {"mode": "blocked", "states": ["unknown", "expired", "unknown"]},
        "available", ("GUARD_OK",), "blocked",
        ("GUARD_BLOCKED", "GUARD_STATE_EXPIRED", "GUARD_STATE_UNKNOWN"),
    ),
    (
        "next_unknown_state",
        _G13_OK, {"mode": "ok", "states": ["mystery_state_zz"]},
        "available", ("GUARD_OK",), "blocked",
        ("GUARD_MALFORMED", "GUARD_UNVERIFIABLE"),
    ),
    (
        "next_offline_contradiction",
        _G13_OK, {"mode": "trusted_offline", "states": ["expired"]},
        "available", ("GUARD_OK",), "blocked",
        ("GUARD_MALFORMED", "GUARD_UNVERIFIABLE"),
    ),
]


@pytest.mark.parametrize(
    "case,current_verdict,next_verdict,cur_status,cur_reasons,next_status,next_reasons",
    _G14_CASES,
    ids=[case for case, _c, _n, _cs, _cr, _ns, _nr in _G14_CASES],
)
def test_002_r2_g14_internal_guard_reason_sets_and_closed_context(
    v2harness, monkeypatch, case, current_verdict, next_verdict, cur_status,
    cur_reasons, next_status, next_reasons,
) -> None:
    """G14（冻结 context 内部值；runtime/消费侧冻结签名未交付前为接口型
    Red）：两税 context 恰为 binding＋current/next 且期间键仅 year/status/
    reason_codes；reason 集合为固定 GUARD_* 且 sorted＋dedup；无 raw mode/
    states/warnings/verifiable 透传；blocked＋[] 合法。"""
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _RecordingGuard(
        {"2025_26": current_verdict, "2026_27": next_verdict, "*": _G13_OK}
    )
    try:
        prepared = v2harness.prepare_ok()
        confirmation_id = v2harness.confirm_ok(prepared)
        r = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r.status_code == 200, (
            f"{case}：current 合法时须可执行（next 抑制只局部），实际 "
            f"{r.status_code}：{r.text[:250]!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real
    assert boxes["contexts"], f"{case}：前置校验失败（context 未捕获）"
    context = boxes["contexts"][-1]
    avail = _field(context, "availability_context")
    # PQA-05：逻辑封闭字段精确集合（Mapping 全部 keys；dataclass/namedtuple
    # 声明字段；opaque → HELD）。任意额外逻辑字段即使值为 None 亦拒。
    _assert_closed_keys(avail, required=("binding", "current", "next"))
    binding_ctx = _kv(avail, "binding")
    _assert_closed_keys(
        binding_ctx,
        required=(
            "bundle_id", "bundle_hash", "rules_schema_version", "engine_version",
        ),
    )
    for key in ("bundle_id", "bundle_hash", "rules_schema_version", "engine_version"):
        assert _kv(binding_ctx, key) == prepared["expected_binding"][key], (
            f"{case}：availability_context.binding.{key} 须与实际消费 binding 一致"
        )
    for which, status, reasons in (
        ("current", cur_status, cur_reasons),
        ("next", next_status, next_reasons),
    ):
        period = _kv(avail, which)
        _assert_closed_keys(
            period, required=("year", "status", "reason_codes")
        )
        expected_year = "2025_26" if which == "current" else "2026_27"
        assert _kv(period, "year") == expected_year, (
            f"{case}：{which}.year 须精确为 {expected_year}"
        )
        assert _kv(period, "status") == status, (
            f"{case}：{which}.status 须为 {status}，实际 {_kv(period, 'status')!r}"
        )
        actual_reasons = tuple(_kv(period, "reason_codes") or [])
        assert actual_reasons == tuple(sorted(reasons)), (
            f"{case}：{which}.reason_codes 须为固定 sorted＋dedup "
            f"{sorted(reasons)!r}，实际 {actual_reasons!r}"
        )
    amounts = r.json().get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", (
        f"{case}：本年 final 须保留 940，实际 {amounts!r}"
    )


def test_002_r2_g14b_warnings_outside_core_hash(v2harness) -> None:
    """G14b（behavior 控制）：offline 响应 warnings 独立于 core——同输入
    offline 与 ok 两次执行的 record core_hash 相等（降级提示不进入封闭 11
    字段哈希对象）；warnings 与 context 的分离由 G14 封闭键断言承载。"""
    real = v2harness.app.state.update_scheduler
    offline = {"mode": "trusted_offline", "notice": "G14b-排版", "states": ["trusted_offline"]}
    try:
        v2harness.app.state.update_scheduler = _RecordingGuard({"*": offline})
        prepared_a = v2harness.prepare_ok()
        cid_a = v2harness.confirm_ok(prepared_a)
        r_a = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": cid_a}
        )
        assert r_a.status_code == 200, r_a.text[:250]
        warnings_a = r_a.json().get("warnings") or []
        assert warnings_a, "offline 执行须携带降级提示"
        rec_a = v2harness.client.get(
            f"/api/v1/records/{r_a.json()['record_id']}/download"
        ).json()
        v2harness.app.state.update_scheduler = _RecordingGuard({"*": _G13_OK})
        prepared_b = v2harness.prepare_ok()
        cid_b = v2harness.confirm_ok(prepared_b)
        r_b = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": cid_b}
        )
        assert r_b.status_code == 200, r_b.text[:250]
        rec_b = v2harness.client.get(
            f"/api/v1/records/{r_b.json()['record_id']}/download"
        ).json()
    finally:
        v2harness.app.state.update_scheduler = real
    assert rec_a["core_hash"] == rec_b["core_hash"], (
        "warnings 不得进入 core 哈希对象（offline 与 ok 同输入 core_hash 须相等）"
    )


def test_002_r2_g15_guard_query_failure_no_retry_no_marker(v2harness) -> None:
    """G15（behavior）：current 期查询抛普通异常 → 固定 reasons 拒绝（422
    E_RULES_STATE_UNVERIFIABLE）、当期查询恰一次（不重试）、响应不泄露异常原文/marker。"""
    real = v2harness.app.state.update_scheduler
    guard = _RecordingGuard(
        {"2025_26": RuntimeError("guard-marker-G15-不得泄露"), "2026_27": _G13_OK}
    )
    v2harness.app.state.update_scheduler = guard
    try:
        r = v2harness.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=v2harness.fresh_if_match()
        )
        current_calls = guard.queries.count("2025_26")
        assert current_calls == 1, (
            f"普通查询异常不得内部重试（当期查询恰一次），实际 {current_calls} 次："
            f"{guard.queries!r}"
        )
        assert r.status_code == 422, (
            f"普通查询异常须 fail-closed 422（不伪装 500），实际 {r.status_code}："
            f"{r.text[:250]!r}"
        )
        assert _error_code(r.json()) == "E_RULES_STATE_UNVERIFIABLE"
        assert "guard-marker-G15" not in r.text, "拒绝响应不得泄露异常原文/marker"
        assert "prepared_id" not in r.json()
    finally:
        v2harness.app.state.update_scheduler = real


class _GuardBaseException(BaseException):
    """模拟 KeyboardInterrupt 类不可被 except Exception 吞掉的基类异常。"""


class _BaseExceptionGuard:
    """availability 抛 BaseException 类异常的受控 guard（基类异常须原样传播）。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def availability(self, period: str) -> Any:
        raise self._exc


def test_002_r2_g15b_guard_base_exception_propagates(v2harness) -> None:
    """G15b（behavior 控制）：查询抛 BaseException 类异常须原样传播（不捕获
    BaseException、不转成 E_RULES_STATE_UNVERIFIABLE/安全 500）。"""
    real = v2harness.app.state.update_scheduler
    marker = "guard-base-marker-G15b"
    v2harness.app.state.update_scheduler = _BaseExceptionGuard(
        _GuardBaseException(marker)
    )
    try:
        with pytest.raises(_GuardBaseException) as excinfo:
            v2harness.client.post(
                _PREPARE,
                json_body=_v2_salary_body(),
                headers=v2harness.fresh_if_match(),
            )
        assert marker in str(excinfo.value), "原始 BaseException 须原样传播"
    finally:
        v2harness.app.state.update_scheduler = real


def test_002_r2_g16_prepare_snapshot_under_store_lock(
    v2harness, monkeypatch
) -> None:
    """G16（behavior；guard-plan-review 高风险约束的确定性锁探针）：prepare 的
    pin→bundle→guard→admission 须在同一 app-owned store 写锁窗口内完成
    （precheck 只复用捕获材料）；窗口内 writer 非阻塞 acquire 必失败、真实
    publish 只能在释放后落地。探针＝RuleStore._write_lock（与 B7 同一边界）。"""
    store = v2harness.app.state.store
    guard = v2harness.app.state.update_scheduler
    real_availability = guard.availability

    entered = threading.Event()
    release = threading.Event()
    window: dict[str, Any] = {"engaged": False}

    def _probing_availability(period: str):
        verdict = real_availability(period)
        if not window["engaged"]:
            acquired = store._write_lock.acquire(blocking=False)
            try:
                if not acquired:
                    # 该次 guard 查询发生在 store 写锁持有期间＝prepare 快照窗口
                    window["engaged"] = True
                    entered.set()
                    assert release.wait(timeout=10), (
                        "G16 屏障未在 10s 内放行（测试屏障死锁看门狗）"
                    )
            finally:
                if acquired:
                    store._write_lock.release()
        return verdict

    monkeypatch.setattr(guard, "availability", _probing_availability)
    original_bundle_id = store.current()["bundle_id"]
    alt = _alt_seed()
    headers = v2harness.fresh_if_match()
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        outcome["response"] = v2harness.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=headers
        )

    thread = threading.Thread(target=_worker, name="hktax-test-g16-prepare")
    thread.start()
    try:
        assert entered.wait(timeout=10), (
            "prepare 未在 store 写锁窗口内查询 guard（pin/bundle/guard/"
            "admission 未形成同一锁内快照；plan §4 prepare 单快照要求未覆盖）"
        )
    except AssertionError:
        release.set()
        thread.join(timeout=15)
        raise

    attempted = threading.Event()
    writer_done = threading.Event()
    probe: dict[str, Any] = {}

    def _writer() -> None:
        acquired = store._write_lock.acquire(blocking=False)
        probe["acquired"] = acquired
        if acquired:
            store._write_lock.release()
        attempted.set()
        # 真实写路径（app.state.store.publish）；被锁住则阻塞至 prepare 释放
        store.publish(alt, evidence_refs=[])
        writer_done.set()

    writer_thread = threading.Thread(target=_writer, name="hktax-test-g16-writer")
    writer_thread.start()
    try:
        assert attempted.wait(timeout=10), "writer 未完成非阻塞探测（死锁看门狗）"
        assert probe["acquired"] is False, (
            "writer 在 prepare 快照窗口内获取了同一 store 写锁——pin/bundle/"
            "guard/admission 未被同一锁覆盖"
        )
    finally:
        release.set()
        thread.join(timeout=15)
        writer_thread.join(timeout=15)
    assert writer_done.is_set(), "writer 真实写须在 prepare 释放后完成"
    assert "response" in outcome and outcome["response"].status_code == 201, (
        f"锁内单快照 prepare 须成功 201，实际 {outcome.get('response')!r}"
    )
    assert outcome["response"].json()["expected_binding"]["bundle_id"] == (
        original_bundle_id
    ), "prepared binding 须取窗口前的同一快照（不混入窗口后发布）"
    assert store.current()["bundle_id"] != original_bundle_id, (
        "发布须在 prepare 释放后生效（不能穿过快照窗口）"
    )


def test_002_r2_g17_prepare_single_producer_phase_counts(
    v2harness, monkeypatch
) -> None:
    """G17（behavior 控制＋计数）：prepare 阶段恰一次 pin/取束/guard（current
    ＋next）且语义预检复用同一组捕获材料（不二次取束/造 context、无第二
    producer）；引擎调用不在 store 锁内。"""
    import app.api.routes as routes_module

    store = v2harness.app.state.store
    counts: dict[str, Any] = {
        "pin": 0, "bundle": 0, "engine": 0, "engine_lock_free": None,
    }
    real_pin = store.pin_current
    real_bundle = store.get_bundle
    real_engine = routes_module.run_engine

    def _counting_pin(*args: Any, **kwargs: Any):
        counts["pin"] += 1
        return real_pin(*args, **kwargs)

    def _counting_bundle(*args: Any, **kwargs: Any):
        counts["bundle"] += 1
        return real_bundle(*args, **kwargs)

    def _counting_engine(*args: Any, **kwargs: Any):
        counts["engine"] += 1
        acquired = store._write_lock.acquire(blocking=False)
        try:
            counts["engine_lock_free"] = acquired
        finally:
            if acquired:
                store._write_lock.release()
        return real_engine(*args, **kwargs)

    monkeypatch.setattr(store, "pin_current", _counting_pin)
    monkeypatch.setattr(store, "get_bundle", _counting_bundle)
    monkeypatch.setattr(routes_module, "run_engine", _counting_engine)

    real_guard = v2harness.app.state.update_scheduler
    guard = _RecordingGuard({"*": _G13_OK})
    v2harness.app.state.update_scheduler = guard
    try:
        r = v2harness.client.post(
            _PREPARE, json_body=_v2_salary_body(), headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 201, (
            f"prepare 须 201，实际 {r.status_code}：{r.text[:250]!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real_guard
    assert counts["pin"] == 1, (
        f"prepare pin 须恰一次（单快照；不得二次 pin），实际 {counts['pin']}"
    )
    assert counts["bundle"] == 1, (
        f"prepare 取束须恰一次（precheck 复用捕获束），实际 {counts['bundle']}"
    )
    assert counts["engine"] == 1, (
        f"prepare 语义预检引擎调用须恰一次，实际 {counts['engine']}"
    )
    assert guard.queries == ["2025_26", "2026_27"], (
        f"prepare guard 查询须恰为 current＋next 各一次，实际 {guard.queries!r}"
    )
    assert counts["engine_lock_free"] is True, (
        "引擎运行不得在 store 写锁内（锁内无引擎运行）"
    )


def test_002_r2_g18_two_tax_phase_counts_isolated(v2harness, monkeypatch) -> None:
    """G18（behavior；阶段计数隔离）：两税 prepare 恰 current＋next 各一次；
    confirm 不新查；execute 期（预检/消费）恰 current＋next 各一次；completion
    只做 guard-only 比较（同样恰 current＋next 各一次、不重算引擎）。防止把
    消费＋完成两阶段误算成一次计数。"""
    import app.api.routes as routes_module

    registry = v2harness.app.state.registry
    real_guard = v2harness.app.state.update_scheduler
    guard = _RecordingGuard({"*": _G13_OK})
    v2harness.app.state.update_scheduler = guard
    marks: dict[str, int] = {}
    engine = {"count": 0}
    real_consume = registry.consume_for_execution
    real_complete = registry.complete_execution
    real_engine = routes_module.run_engine

    def _wrapped_consume(*args: Any, **kwargs: Any):
        marks["consume_start"] = len(guard.queries)
        context = real_consume(*args, **kwargs)
        marks["consume_end"] = len(guard.queries)
        marks["engine_at_consume"] = engine["count"]
        return context

    def _wrapped_complete(*args: Any, **kwargs: Any):
        marks["complete_start"] = len(guard.queries)
        marks["engine_at_complete_start"] = engine["count"]
        receipt = real_complete(*args, **kwargs)
        marks["complete_end"] = len(guard.queries)
        return receipt

    def _counting_engine(*args: Any, **kwargs: Any):
        engine["count"] += 1
        return real_engine(*args, **kwargs)

    monkeypatch.setattr(registry, "consume_for_execution", _wrapped_consume)
    monkeypatch.setattr(registry, "complete_execution", _wrapped_complete)
    monkeypatch.setattr(routes_module, "run_engine", _counting_engine)
    try:
        prepared = v2harness.prepare_ok()
        prepare_queries = list(guard.queries)
        engine_after_prepare = engine["count"]
        confirmation_id = v2harness.confirm_ok(prepared)
        confirm_queries = guard.queries[len(prepare_queries):]
        r = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r.status_code == 200, (
            f"两税 execute 须成功（计数装置不改变行为），实际 {r.status_code}："
            f"{r.text[:250]!r}"
        )
    finally:
        v2harness.app.state.update_scheduler = real_guard
    assert prepare_queries == ["2025_26", "2026_27"], (
        f"prepare 阶段须恰查 current＋next 各一次，实际 {prepare_queries!r}"
    )
    assert engine_after_prepare == 1, (
        f"prepare 语义预检引擎调用须恰一次，实际 {engine_after_prepare}"
    )
    assert confirm_queries == [], (
        f"confirm 不得新查 guard（阶段隔离），实际 {confirm_queries!r}"
    )
    # review 13：断言 consume 入口内的 query slice（防止 route 锁外预检混过）；
    # completion 两期各 1 为此前已批准冻结项。
    consume_queries = guard.queries[marks["consume_start"]:marks["consume_end"]]
    assert consume_queries == ["2025_26", "2026_27"], (
        f"consume 入口内须恰重查 current＋next 各一次（锁内快照），实际 "
        f"{consume_queries!r}（query slice [{marks['consume_start']}:"
        f"{marks['consume_end']}]）"
    )
    execute_queries = guard.queries[len(prepare_queries) + len(confirm_queries):marks["complete_start"]]
    assert execute_queries == ["2025_26", "2026_27"], (
        f"execute 期（预检/消费）须恰查 current＋next 各一次，实际 "
        f"{execute_queries!r}"
    )
    assert marks["engine_at_consume"] == engine_after_prepare, (
        "消费临界区不得运行引擎（引擎在释放锁后计算）"
    )
    completion_queries = guard.queries[marks["complete_start"]:marks["complete_end"]]
    assert completion_queries == ["2025_26", "2026_27"], (
        f"completion 只做 guard-only 比较（current＋next 各一次），实际 "
        f"{completion_queries!r}"
    )
    assert engine["count"] == marks["engine_at_complete_start"], (
        "completion 不得重算引擎（guard-only 比较）"
    )


def test_002_r2_g19_next_guard_change_masked_by_missing_rules_historical(
    v2harness, monkeypatch
) -> None:
    """G19（behavior；completion guard-only 比较）：快照 next 为 missing_rules
    ＋RULES_UNVERIFIABLE（规则缺），计算期间 next guard ok→blocked——比较面须为
    guard-only ordered(year,status,sortedunique reasons)，不得因 RULES_UNVERIFIABLE
    混入而漏判：仍须 historical；金额/binding/record 取原快照（无 500/重算/丢弃）。"""
    import copy as _copy

    body = _v2_salary_body()
    body["input"]["year_of_assessment"] = "2026_27"
    year26 = _copy.deepcopy(body["input"]["years"]["2025_26"])
    year26["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    body["input"]["years"] = {"2026_27": year26}
    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _RecordingGuard({"*": _G13_OK})
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    try:
        r_prepare = v2harness.client.post(
            _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
        )
        assert r_prepare.status_code == 201, (
            f"current=2026_27 不得因 next 无规则拒本年，实际 {r_prepare.status_code}："
            f"{r_prepare.text[:250]!r}"
        )
        original_binding = dict(r_prepare.json()["expected_binding"])
        confirmation_id = v2harness.confirm_ok(r_prepare.json())

        def _block_next_only() -> None:
            v2harness.app.state.update_scheduler = _RecordingGuard(
                {
                    "2026_27": _G13_OK,
                    "2027_28": {
                        "mode": "blocked", "notice": "G19", "states": ["expired"]
                    },
                }
            )

        r = _execute_with_barrier(
            monkeypatch, v2harness, confirmation_id, _block_next_only
        )
    finally:
        v2harness.app.state.update_scheduler = real
    assert r.status_code == 200, (
        f"guard 变化不得拒算/丢弃（仅 historical），实际 {r.status_code}："
        f"{r.text[:250]!r}"
    )
    assert boxes["receipts"], "前置校验：Receipt 未捕获"
    receipt = boxes["receipts"][-1]
    assert _field(receipt, "outcome") == "stored"
    assert _field(receipt, "editor_result") == "historical", (
        f"next guard-only key 变化（ok→blocked）不得因 missing_rules/"
        f"RULES_UNVERIFIABLE 混入而判 current，实际 "
        f"{_field(receipt, 'editor_result')!r}"
    )
    amounts = r.json().get("amounts") or {}
    assert amounts.get("before_reduction") == "3160" and amounts.get(
        "final_after_reduction"
    ) == "3160", f"current 金额须取原快照（手算 3160），实际 {amounts!r}"
    assert r.json()["binding"] == original_binding
    r_record = v2harness.client.get(
        f"/api/v1/records/{r.json()['record_id']}/download"
    )
    assert r_record.status_code == 200
    assert r_record.json()["record"]["binding"] == original_binding


def test_002_r2_g20_incomplete_latest_historical_even_if_frozen_unverifiable(
    v2harness, monkeypatch
) -> None:
    """G20（behavior）：快照本身即含不可核验期间（next malformed→verifiable
    false）；completion 最新仍不可核验 → 只能 historical（不得因两者「相同」
    判 current）；合法历史仍保存、金额取原快照（不 500/不丢弃）。"""
    real = v2harness.app.state.update_scheduler
    v2harness.app.state.update_scheduler = _RecordingGuard(
        {"2025_26": _G13_OK, "2026_27": {"mode": "ok", "states": "not-a-list"}}
    )
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    try:
        prepared = v2harness.prepare_ok()
        confirmation_id = v2harness.confirm_ok(prepared)
        r = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
    finally:
        v2harness.app.state.update_scheduler = real
    assert r.status_code == 200, (
        f"next 不可核验只抑制 next、本年仍存历史（不得 500/丢弃），实际 "
        f"{r.status_code}：{r.text[:250]!r}"
    )
    assert boxes["receipts"], "前置校验：Receipt 未捕获"
    receipt = boxes["receipts"][-1]
    assert _field(receipt, "outcome") == "stored"
    assert _field(receipt, "editor_result") == "historical", (
        f"最新 GuardRead.verifiable=false → 不得 current（即使冻结快照亦不可"
        f"核验），实际 {_field(receipt, 'editor_result')!r}"
    )
    amounts = r.json().get("amounts") or {}
    assert amounts.get("final_after_reduction") == "940", amounts


_G21_CASES = ["next_blocked", "next_missing_rules"]


@pytest.mark.parametrize("case", _G21_CASES)
def test_002_r2_g21_next_blocked_vs_missing_rules_context_status(
    v2harness, monkeypatch, case
) -> None:
    """G21（冻结 context 内部值；接口型 Red）：next guard blocked 与 next
    rule-only missing_rules 必须区分——blocked 携带 guard reasons（无
    RULES_UNVERIFIABLE），missing_rules 增加 RULES_UNVERIFIABLE；本年金额
    均保留（940/3160）。"""
    import copy as _copy

    real = v2harness.app.state.update_scheduler
    boxes = _capture_contexts_and_receipts(monkeypatch, v2harness)
    if case == "next_blocked":
        v2harness.app.state.update_scheduler = _RecordingGuard(
            {"2025_26": _G13_OK, "2026_27": {"mode": "blocked", "states": []}}
        )
        body = _v2_salary_body()
        expected_final = "940"
    else:
        v2harness.app.state.update_scheduler = _RecordingGuard({"*": _G13_OK})
        body = _v2_salary_body()
        body["input"]["year_of_assessment"] = "2026_27"
        year26 = _copy.deepcopy(body["input"]["years"]["2025_26"])
        year26["household"]["marriage"] = [
            {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
        ]
        body["input"]["years"] = {"2026_27": year26}
        expected_final = "3160"
    try:
        r = v2harness.client.post(
            _PREPARE, json_body=body, headers=v2harness.fresh_if_match()
        )
        assert r.status_code == 201, r.text[:250]
        confirmation_id = v2harness.confirm_ok(r.json())
        r_exec = v2harness.client.post(
            _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
        )
        assert r_exec.status_code == 200, r_exec.text[:250]
    finally:
        v2harness.app.state.update_scheduler = real
    amounts = r_exec.json().get("amounts") or {}
    assert amounts.get("final_after_reduction") == expected_final, amounts
    assert boxes["contexts"], "前置校验：context 未捕获"
    avail = _field(boxes["contexts"][-1], "availability_context")
    next_period = _kv(avail, "next")
    reasons = list(_kv(next_period, "reason_codes") or [])
    if case == "next_blocked":
        assert _kv(next_period, "status") == "blocked", (
            f"next guard 已拒绝须为 blocked，实际 {_kv(next_period, 'status')!r}"
        )
        assert reasons == ["GUARD_BLOCKED"], (
            f"guard 已拒绝不得混入 RULES_UNVERIFIABLE，实际 {reasons!r}"
        )
    else:
        assert _kv(next_period, "status") == "missing_rules", (
            f"next 规则缺须为 missing_rules（与 blocked 区分），实际 "
            f"{_kv(next_period, 'status')!r}"
        )
        assert reasons == ["GUARD_OK", "RULES_UNVERIFIABLE"], (
            f"missing_rules 须在 guard reasons 上增加 RULES_UNVERIFIABLE，"
            f"实际 {reasons!r}"
        )
        assert "GUARD_BLOCKED" not in reasons


def _runtime_module_or_fail() -> Any:
    """guard-contract §1 声明的 runtime 入口模块（真实缺失＝接口型失败；
    review 12：只认 ModuleNotFoundError 且名字确为该模块，其它 ImportError
    不得被吞成「模块缺失」）。"""
    import importlib

    try:
        return importlib.import_module("app.rules.runtime")
    except ModuleNotFoundError as exc:
        if exc.name not in ("app.rules.runtime", "app.rules"):
            raise
        raise AssertionError(
            "INTERFACE_ONLY：缺 app/rules/runtime.py（guard-contract §1 冻结入口 "
            "periods_for/read_guard/admit_runtime；缺失只记接口型 Red，"
            "不冒充行为证据）"
        ) from exc


def test_002_r2_g22_runtime_periods_for_mapping() -> None:
    """G22-runtime（接口）：periods_for 两税＝目标年＋紧邻下一年、五税＝目标年。"""
    runtime = _runtime_module_or_fail()
    salaries = tuple(
        runtime.periods_for("salaries_tax", {"year_of_assessment": "2025_26"})
    )
    assert salaries == ("2025_26", "2026_27"), f"实际 {salaries!r}"
    pa = tuple(
        runtime.periods_for("personal_assessment", {"year_of_assessment": "2025_26"})
    )
    assert pa == ("2025_26", "2026_27"), f"实际 {pa!r}"
    profits = tuple(
        runtime.periods_for("profits_tax", {"year_of_assessment": "2025_26"})
    )
    assert profits == ("2025_26",), f"实际 {profits!r}"


_G22_READ_CASES: list[tuple[str, Any, str, tuple, str, tuple, bool]] = [
    (
        "guard_none",
        None,
        "blocked", ("GUARD_MISSING", "GUARD_UNVERIFIABLE"),
        "blocked", ("GUARD_MISSING", "GUARD_UNVERIFIABLE"),
        False,
    ),
    (
        "ok",
        _RecordingGuard({"*": _G13_OK}),
        "available", ("GUARD_OK",),
        "available", ("GUARD_OK",),
        True,
    ),
    (
        # review 2：合法 blocked/states=[] 判定是**可核验**的（verifiable=True）
        "blocked_empty_valid",
        _RecordingGuard({"*": {"mode": "blocked", "states": []}}),
        "blocked", ("GUARD_BLOCKED",),
        "blocked", ("GUARD_BLOCKED",),
        True,
    ),
    (
        "next_unknown_mode",
        _RecordingGuard({"2025_26": _G13_OK, "2026_27": {"mode": "celestial"}}),
        "available", ("GUARD_OK",),
        "blocked", ("GUARD_MALFORMED", "GUARD_UNVERIFIABLE"),
        False,
    ),
    (
        "current_query_failure",
        _RecordingGuard(
            {"2025_26": RuntimeError("G22-query-marker"), "2026_27": _G13_OK}
        ),
        "blocked", ("GUARD_QUERY_FAILED", "GUARD_UNVERIFIABLE"),
        "available", ("GUARD_OK",),
        False,
    ),
]


@pytest.mark.parametrize(
    "case,guard,cur_status,cur_reasons,next_status,next_reasons,verifiable",
    _G22_READ_CASES,
    ids=[case for case, *_rest in _G22_READ_CASES],
)
def test_002_r2_g22_runtime_read_guard_mapping(
    case, guard, cur_status, cur_reasons, next_status, next_reasons, verifiable
) -> None:
    """G22-runtime（接口）：read_guard 对 absent/ok/next 畸形/当期查询故障的
    有限规范化；周期顺序恰 current＋next；reason_codes 固定 sorted＋dedup；
    verifiable 为全部期间可核验的合取；查询异常不泄露原文。"""
    runtime = _runtime_module_or_fail()
    facts = {"year_of_assessment": "2025_26"}
    read = runtime.read_guard(guard, tax_type="salaries_tax", facts=facts)
    periods = list(_field(read, "periods"))
    assert [_kv(period, "year") for period in periods] == ["2025_26", "2026_27"], (
        f"GuardRead.periods 须按查询顺序恰 current＋next，实际 "
        f"{[_kv(period, 'year') for period in periods]!r}"
    )
    for index, (status, reasons) in enumerate(
        ((cur_status, cur_reasons), (next_status, next_reasons))
    ):
        assert _kv(periods[index], "status") == status, (
            f"{case}：期 {index} status 须为 {status}，实际 "
            f"{_kv(periods[index], 'status')!r}"
        )
        actual = tuple(_kv(periods[index], "reason_codes") or [])
        assert actual == tuple(sorted(reasons)), (
            f"{case}：期 {index} reason_codes 须为固定 sorted＋dedup "
            f"{sorted(reasons)!r}，实际 {actual!r}"
        )
        assert "G22-query-marker" not in str(actual), "不得泄露查询异常原文"
    assert bool(_field(read, "verifiable")) is verifiable, (
        f"{case}：verifiable 须为 {verifiable}（全部期间可核验的合取），实际 "
        f"{_field(read, 'verifiable')!r}"
    )
    assert isinstance(_field(read, "warnings"), list), "warnings 须为字符串数组"


def _v2_binding_and_bundle(harness: _Harness) -> tuple:
    from app.config import ENGINE_VERSION

    store = harness.app.state.store
    pinned = store.pin_current()
    binding = {
        "bundle_id": pinned["bundle_id"],
        "bundle_hash": pinned["bundle_hash"],
        "rules_schema_version": pinned["rules_schema_version"],
        "engine_version": ENGINE_VERSION,
    }
    return binding, store.get_bundle(binding["bundle_id"])


def test_002_r2_g22_runtime_admit_next_blocked_keeps_valid_current(
    v2harness,
) -> None:
    """G22-runtime（接口；§3，review 2）：next guard blocked 是**合法可核验**
    判定（aggregate verifiable=true）且不得拒绝合法 current；next 为 blocked
    （不得混入 RULES_UNVERIFIABLE）；availability_context 对实际 binding/
    current/next 精确绑定。"""
    runtime = _runtime_module_or_fail()
    facts = {"year_of_assessment": "2025_26"}
    guard_read = runtime.read_guard(
        _RecordingGuard(
            {"2025_26": _G13_OK, "2026_27": {"mode": "blocked", "states": []}}
        ),
        tax_type="salaries_tax",
        facts=facts,
    )
    assert bool(_field(guard_read, "verifiable")) is True, (
        "合法 blocked/states=[] 判定可核验（verifiable=True）；只有 absent/"
        "malformed/query-failed 才 false"
    )
    binding, bundle = _v2_binding_and_bundle(v2harness)
    admission = runtime.admit_runtime(
        binding, bundle, tax_type="salaries_tax", facts=facts, guard_read=guard_read
    )
    avail = _field(admission, "availability_context")
    current_period = _kv(avail, "current")
    assert _kv(current_period, "status") == "available", (
        "next blocked 不得拒绝/降级合法 current"
    )
    assert list(_kv(current_period, "reason_codes") or []) == ["GUARD_OK"]
    next_period = _kv(avail, "next")
    assert _kv(next_period, "status") == "blocked", (
        f"next guard 已拒绝须为 blocked（不得覆盖成 available/missing_rules），"
        f"实际 {_kv(next_period, 'status')!r}"
    )
    assert _kv(next_period, "year") == "2026_27"
    assert "RULES_UNVERIFIABLE" not in (
        _kv(next_period, "reason_codes") or []
    ), "guard 已拒绝无需继续证明其规则（不得混入 RULES_UNVERIFIABLE）"
    assert isinstance(_field(admission, "warnings"), list), "warnings 须为字符串数组"


def test_002_r2_g22_runtime_admit_current_blocked_rejects(v2harness) -> None:
    """G22-runtime（接口；§3）：current guard blocked → E_RULES_STATE_
    UNVERIFIABLE（HTTP 层的无能力/不消费由 G4 承载，不在此重造）。"""
    from app.core.errors import AppError

    runtime = _runtime_module_or_fail()
    facts = {"year_of_assessment": "2025_26"}
    guard_read = runtime.read_guard(
        _RecordingGuard(
            {"2025_26": {"mode": "blocked", "states": []}, "2026_27": _G13_OK}
        ),
        tax_type="salaries_tax",
        facts=facts,
    )
    binding, bundle = _v2_binding_and_bundle(v2harness)
    with pytest.raises(AppError) as excinfo:
        runtime.admit_runtime(
            binding, bundle, tax_type="salaries_tax", facts=facts,
            guard_read=guard_read,
        )
    assert excinfo.value.code == "E_RULES_STATE_UNVERIFIABLE"


def _patch_e_rule_verification(runtime: Any, monkeypatch, make_hook) -> None:
    """在 E 的中立核验入口注入受控错误（review 12）：
    - 修补**定义模块** app.rules.admission 的属性（module-attr 直调用路径）；
    - 并按**对象 identity** 扫描 runtime globals 中对同一函数的别名绑定并一并
      修补（不锁 from-import 风格/变量名）；
    - 真实缺失只认 ModuleNotFoundError（名字须确为该模块）；其它 ImportError
      原样传播，不吞成「模块缺失」。"""
    import importlib

    try:
        admission_module = importlib.import_module("app.rules.admission")
    except ModuleNotFoundError as exc:
        if exc.name not in ("app.rules.admission", "app.rules"):
            raise
        raise AssertionError(
            "INTERFACE_ONLY：缺 app/rules/admission.py::verify_bundle_admission"
            "（guard-contract §3 E 中立核验接口；缺失只记接口型 Red）"
        ) from exc
    real = getattr(admission_module, "verify_bundle_admission", None)
    assert callable(real), (
        "INTERFACE_ONLY：E 中立核验入口 verify_bundle_admission 未在场"
        "（缺失只记接口型 Red，不冒充行为证据）"
    )
    hook = make_hook(real)
    monkeypatch.setattr(admission_module, "verify_bundle_admission", hook)
    for name, value in list(vars(runtime).items()):
        if value is real:
            monkeypatch.setattr(runtime, name, hook)


_G22_PROPAGATION_CASES = ["e_input_apperror", "e_program_runtimeerror"]


@pytest.mark.parametrize("case", _G22_PROPAGATION_CASES)
def test_002_r2_g22_runtime_next_e_error_must_propagate(
    v2harness, monkeypatch, case
) -> None:
    """G22-runtime（接口；§3，review 12）：仅 E 明确的规则不可核验错误可局部
    化 next（missing_rules＋RULES_UNVERIFIABLE）；E 的 input 类 AppError 或
    普通程序异常必须原样传播（同一 error 对象 identity），不得成为
    next-null/missing_rules；先以 next 核验命中计数证明注入真实到达。"""
    from app.core.errors import AppError

    runtime = _runtime_module_or_fail()
    facts = {"year_of_assessment": "2025_26"}
    guard_read = runtime.read_guard(
        _RecordingGuard({"2025_26": _G13_OK, "2026_27": _G13_OK}),
        tax_type="salaries_tax",
        facts=facts,
    )
    if case == "e_input_apperror":
        raised_error: BaseException = AppError(
            "E_INPUT_TYPE", "G22-E-input-marker", field="input.g22"
        )
    else:
        raised_error = RuntimeError("G22-E-program-marker")
    hits = {"next": 0}

    def _make_hook(real):
        def _hook(content: Any, *, tax_type: str, year: Any = None):
            if year == "2026_27":
                hits["next"] += 1
                raise raised_error
            return real(content, tax_type=tax_type, year=year)

        return _hook

    _patch_e_rule_verification(runtime, monkeypatch, _make_hook)
    binding, bundle = _v2_binding_and_bundle(v2harness)
    if case == "e_input_apperror":
        with pytest.raises(AppError) as excinfo:
            runtime.admit_runtime(
                binding, bundle, tax_type="salaries_tax", facts=facts,
                guard_read=guard_read,
            )
        assert hits["next"] >= 1, (
            "next 规则核验入口须实际命中（命中计数后才断言同原 error）"
        )
        assert excinfo.value is raised_error, (
            "E input 类错误须原样传播同一 AppError 对象（不得吞成 next-null）"
        )
        assert excinfo.value.code == "E_INPUT_TYPE"
        assert excinfo.value.field == "input.g22"
    else:
        with pytest.raises(RuntimeError) as excinfo:
            runtime.admit_runtime(
                binding, bundle, tax_type="salaries_tax", facts=facts,
                guard_read=guard_read,
            )
        assert hits["next"] >= 1, (
            "next 规则核验入口须实际命中（命中计数后才断言同原 error）"
        )
        assert excinfo.value is raised_error, "程序异常须同一对象原样传播"


# ---------------------------------------------------------------------------
# F2/R2 Wave A（2026-10-17）：C11 独立返回副本必须早于消费提交
# ---------------------------------------------------------------------------


class _ReturnCopyProxy:
    """在 security.py 的 copy.deepcopy 上定向注入：仅当被复制对象是完整
    ExecutionContext（execution_id＋verified_bundle_bytes）时命中一次。"""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.fired = 0

    def deepcopy(self, value: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            isinstance(value, Mapping)
            and "execution_id" in value
            and "verified_bundle_bytes" in value
        ):
            self.fired += 1
            raise RuntimeError("C11-return-copy-marker-不得泄露")
        return self._real.deepcopy(value, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_002_r2_c11_return_copy_fault_before_commit_same_token_retry(
    v2harness, monkeypatch
) -> None:
    """F2/R2（c11-review:18）：在实际独立返回副本构造边界注入普通 RuntimeError
    → 未消费、无新 execution/record/关联、引擎未执行；恢复后同 token 不重
    prepare/confirm 成功，再次执行拒绝；HTTP 安全 500 无 marker/core/税额。"""
    import copy as _copy

    import app.api.routes as routes_module
    import app.web.security as security_module

    prepared = v2harness.prepare_ok()
    confirmation_id = v2harness.confirm_ok(prepared)
    registry = v2harness.app.state.registry
    sid = _sid_of(v2harness)

    engine_calls = {"count": 0}
    real_run_engine = routes_module.run_engine

    def _counting_engine(*args: Any, **kwargs: Any):
        engine_calls["count"] += 1
        return real_run_engine(*args, **kwargs)

    monkeypatch.setattr(routes_module, "run_engine", _counting_engine)
    before_ids = _red_registry_identity_sets(registry, sid)
    proxy = _ReturnCopyProxy(_copy)
    monkeypatch.setattr(security_module, "copy", proxy)

    r = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert proxy.fired == 1, (
        "实际独立返回副本边界须恰命中一次（无内部重试），"
        f"实际 {proxy.fired}；response={r.status_code}"
    )
    assert engine_calls["count"] == 0, (
        "返回副本构造失败发生在消费提交前 → 不得执行引擎"
    )
    assert r.status_code == 500, (
        f"普通构造异常 → 安全 500（不伪装 4xx），实际 {r.status_code}"
    )
    assert "C11-return-copy-marker" not in r.text, "500 不得泄露异常原文/marker"
    payload = r.json()
    assert "amounts" not in payload and "record_id" not in payload, (
        "安全 500 不得携带 core/税额/record 链接"
    )
    assert "no-store" in (r.headers.get("Cache-Control") or "").lower()
    consumption = registry.get_consumption(sid, confirmation_id)
    assert consumption is not None, "get_consumption 既有只读合同"
    assert consumption["consumed"] is False, (
        f"返回副本构造失败不得半消费（consumed 须 False），实际 {consumption!r}"
    )
    assert consumption["record_id"] is None, "不得有 record 链接"
    assert consumption["core_hash"] is None, "不得有 core hash"
    assert _red_registry_identity_sets(registry, sid) == before_ids, (
        "失败不得产生新 execution/record 身份键；"
        f"before={before_ids!r} after={_red_registry_identity_sets(registry, sid)!r}"
    )

    # 恢复真实 copy；同一 confirmation_id（不重 prepare/confirm）恢复成功
    monkeypatch.setattr(security_module, "copy", _copy)
    r_retry = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_retry.status_code == 200, (
        f"恢复后同 token 须成功（失败未消费），实际 "
        f"{r_retry.status_code}：{r_retry.text[:250]!r}"
    )
    assert engine_calls["count"] == 1, "恢复后引擎恰执行一次"
    body = r_retry.json()
    assert body["binding"] == prepared["expected_binding"]
    assert (body.get("amounts") or {}).get("final_after_reduction") == "940", (
        f"恢复结果金额须与合法 control 一致，实际 {body.get('amounts')!r}"
    )
    r_download = v2harness.client.get(
        f"/api/v1/records/{body['record_id']}/download"
    )
    assert r_download.status_code == 200
    assert r_download.json()["record"]["binding"] == prepared["expected_binding"]
    r_replay = v2harness.client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_replay.status_code == 409 and _error_code(
        r_replay.json()
    ) == "E_CONFIRMATION_CONSUMED", "恰一次登记（恢复后重复执行＝已消费）"
    assert r_replay.json().get("record_id") == body["record_id"]


def test_002_r2_c11_returned_context_independent_from_authority(v2harness) -> None:
    """F2/R2：成功消费返回的 context 是独立副本——binding/availability_context
    及其嵌套可变字段不与 registry 权威 entry 共用引用；完整 entry 元数据仍在。"""
    prepared = v2harness.prepare_ok()
    sid, context, facts, binding = _consume_direct(v2harness, prepared)
    registry = v2harness.app.state.registry
    execution_id = _field(context, "execution_id")
    assert isinstance(execution_id, str) and execution_id, (
        f"返回 context 缺 execution_id，实际 {execution_id!r}"
    )
    with registry._lock:
        session = registry._sessions[sid]
        entry = session["executions"][execution_id]
        authority = entry["context"]
        assert context is not authority, "返回对象不得是权威 context 本身"
        assert _field(context, "binding") is not _field(authority, "binding"), (
            "返回 binding 不得与权威 context 共用引用"
        )
        assert _field(context, "availability_context") is not _field(
            authority, "availability_context"
        ), "返回 availability_context 不得与权威 context 共用引用"
        returned_av = _field(context, "availability_context")
        authority_av = _field(authority, "availability_context")
        assert returned_av["current"] is not authority_av["current"], (
            "current 嵌套 dict 须独立"
        )
        assert returned_av["next"] is not authority_av["next"], (
            "next 嵌套 dict 须独立"
        )
        # 返回副本可安全交给调用方改写：权威快照不得随之变化
        _field(context, "binding")["bundle_id"] = "tampered"
        returned_av["current"]["status"] = "blocked"
        returned_av["next"]["reason_codes"].append("TAMPERED")
        assert _field(authority, "binding")["bundle_id"] != "tampered", (
            "调用方改写返回副本不得污染权威 binding"
        )
        assert authority_av["current"]["status"] == "available", (
            "调用方改写返回副本不得污染权威 current"
        )
        assert "TAMPERED" not in authority_av["next"].get("reason_codes", []), (
            "调用方改写返回副本不得污染权威 next.reason_codes"
        )
        assert isinstance(entry.get("warnings"), list), (
            f"execution entry 须保留 warnings 列表，实际 {entry.get('warnings')!r}"
        )
        assert "guard_key" in entry and "expires_at" in entry, (
            f"execution entry 须含 guard_key/expires_at，实际键 {sorted(entry)!r}"
        )
        assert isinstance(entry.get("expires_at"), (int, float)), (
            f"expires_at 须为签发时冻结的数值，实际 {entry.get('expires_at')!r}"
        )
