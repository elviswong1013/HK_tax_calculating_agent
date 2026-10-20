# -*- coding: utf-8 -*-
"""REQ-12 存储五态判定（002 P1 Red；Annex 005 §15.4 冻结表；P1 固定测试 seed 注入）。

REQ: REQ-12（S002 §8/§9.4；PRD AC-12）。G0 修正后共 8 个命名节点
（原 §9.4 REQ-12 五节点＋G0 新增准入三节点）：
  - test_002_true_empty_only_seed（G0：补完整固定束 canonical hash 固定）
  - test_002_orphan_bundles_fail_closed_no_reseed
  - test_002_dangling_pointer_fail_closed
  - test_002_hash_corruption_fail_closed
  - test_002_incompatible_bundle_blocks_calc_replays_kept
  - test_002_missing_profile_blocks_calc（§五.5/R1）
  - test_002_tampered_profile_self_consistent_digest_blocks（§五.5/R1②）
  - test_002_shared_compat_guard_blocks_all_seven_targets_on_incompatible_store
    （§五.5/Bu4：七目标共同兼容检查）

规格锚点:
  - S002 §2 ADR-8（发布路径最小化：空库判定＝五态状态表；仅在「真空库」seed；
    兼容守卫覆盖五入口；损坏指针不当空库）＋§3.3（存储不变：SQLite 规则束
    单指针原子发布；新 v2 seed 由发行版固定内容组成）＋§10 依赖契约
    （P1 产出隔离固定 v2 seed 输入＝测试前置，非生产 bootstrap；create_app
    不得由任意 AppError 触发 reseed；有效 app 在获批 seed 前可保持
    new-calc blocked，不是错误态）。
  - Annex 005 §15.4 存储五态冻结表：
      真空库＝current_pointer 无行 **且** rule_bundles 零行 → 允许发布固定
      v2 seed（仅此一况）；
      孤儿束（无指针行但有束行）→ fail-closed，不得当空库 reseed；
      悬空指针（指针行引用的 bundle 不存在）→ fail-closed；
      内容/哈希损坏（载入内容与 bundle_hash 不符）→ fail-closed；
      不兼容旧库（rules_schema_version != 2.0.0 / profile 预期 P/digest
      不符 / 所需 allowance 节点缺失）→ fail-closed 阻新计算，
      旧记录查看/导出不受影响。
  - Annex 005 §15.1（固定语义 profile part5_shared_v2＝P 逐字段；
    semantic_digest＝"sha256:"+SHA256(canonical_json_bytes(P))；不对含自身
    digest 的对象再哈希）＋§15.2（共享金额矩阵全表；缺键＝运行期数据缺失 →
    E_RULES_STATE_UNVERIFIABLE fail-closed）。
  - S002 §4.2（prepare 端点为五入口守卫之一：pin/prepare 含兼容与可用性）。

期望值来源（禁从生产代码反推）:
  - 五态判定与处置：Annex 005 §15.4 冻结表（逐行转写）。
  - 金额矩阵三年度全表：Annex 005 §15.2（转引附表 4/43，T1）。
  - profile P 与 digest 算法：Annex 005 §15.1（canonical JSON 按 C2.7）。
  - 其余为结构/状态断言，无金额期望。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - create_app()（既有装配点，HKTAX_DB_PATH 注入临时库）对五种库态的构造
    行为：非真空态一律不得 reseed（不新增 bundle 行、不改写/新增指针行、
    不静默迁移 schema），且新计算 fail-closed（prepare → 422
    E_RULES_STATE_UNVERIFIABLE，无 prepared_id）；app 本身保持有效
    （meta/version 200，不是错误态）。
  - 真空库：若构造期发布 seed，则只能发布 v2 seed（rules_schema_version
    "2.0.0"＋data.allowances §15.2 矩阵＋frozen_semantics 内 part5_shared_v2
    且 semantic_digest 与本文件独立复算一致）；亦允许保持 new-calc blocked。
    本文件在真空且未 seed 时显式注入固定 v2 测试 seed（RuleStore.publish，
    既有 seam）后新计算可用（P1 依赖契约，不依赖 P3 生产 bootstrap）。
  - 旧记录查看不受五态影响：不兼容旧库下注入的既有内存 record 经
    GET /api/v1/records/{id} 仍 200 原样返回。
  - 本文件对 app.* 延迟导入（fixture/测试体内），Red 失败原因＝缺失 v2
    行为（仍 seed v1 / 无 fail-closed 守卫），而非收集期导入失败。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

_BASE_ORIGIN = "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# 固定 v2 测试 seed（P1 注入件；Annex 005 §15.1/§15.2 全表独立转写）
# ---------------------------------------------------------------------------


def _canonical_bytes(obj: Any) -> bytes:
    """C2.7 canonical JSON：键排序、UTF-8、无空白分隔符（独立复算，不引生产）。"""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _part5_profile() -> dict:
    """Annex 005 §15.1 固定语义 profile P（逐字段转写；不含 semantic_digest）。"""
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


def _semantic_digest() -> str:
    """§15.1：digest 只覆盖 P 本身（不对含 digest 的对象再哈希）。"""
    return "sha256:" + hashlib.sha256(_canonical_bytes(_part5_profile())).hexdigest()


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


def _matrix_years_24_25() -> dict:
    return {
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


def _allowance_matrix() -> dict:
    """Annex 005 §15.2 共享金额矩阵（三年度全表＋MPF 唯一源）。"""
    return {
        "2024_25": _matrix_years_24_25(),
        "2025_26": _matrix_years_24_25(),
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
    }


def _shared_reductions() -> dict:
    """§15.2：唯一 salary/PA 宽减表 reductions.<YA>（附表 43，T1）。"""
    return {
        "2024_25": {"percent_bp": 10000, "cap": "1500", "status": "applicable"},
        "2025_26": {"percent_bp": 10000, "cap": "3000", "status": "applicable"},
        "2026_27": {"percent_bp": 0, "cap": "0", "status": "not_applicable_verified"},
    }


# 定稿 seed 的 canonical hash（C2.8 六字段；跨 6 个协议测试文件同值，
# 见各文件 `_fixed_v2_seed`——复核报告 §四.1 要求完整固定束可检篡改）
_FIXED_V2_SEED_HASH = "sha256:a08937e4a8b9923392c02da8c66451e0a526e919046d3315ec3e30361ad2f1b5"


def _fixed_v2_seed() -> dict:
    """固定 v2 测试 seed（C2.8 封闭六字段；P1 测试注入件，非生产 bootstrap）。

    五条不受影响路径的最小泳道数值取自现行 001 束（T1 台账），仅为使
    seed 完整可服务七税 prepare；两税语义数据全按 Annex 005 §15.1/§15.2。

    来源与 canonical hash（复核报告 §四.1：完整、有来源的固定束）：
      - 矩阵/宽减/MPF/ERCE：Annex 005 §15.2（转引附表 4/3B/3C/43，T1）；
      - 税率带与 PA 比较参数：附表 1/2（T1，台账 1.17）＋D6.2a（T1-observed）；
      - profile P＋digest：Annex 005 §15.1（canonical JSON 按 C2.7 复算）；
      - 本 seed 的 bundle_content_hash（六字段 canonical）冻结为
        `_FIXED_V2_SEED_HASH`（见下），篡改可检。
    """
    profile = _part5_profile()
    profile_with_digest = {**profile, "semantic_digest": _semantic_digest()}
    return {
        "rules_schema_version": "2.0.0",
        "effective": {
            "allowances": {"from_year": "2024_25", "to_year": "2026_27"},
            "reductions": {"from_year": "2024_25", "to_year": "2025_26"},
        },
        "applicability": {"years": ["2024_25", "2025_26", "2026_27"]},
        "frozen_semantics": {"profiles": [profile_with_digest]},
        "data": {
            "allowances": _allowance_matrix(),
            "reductions": _shared_reductions(),
            # §15.2：ERCE cap 仍留 salaries.deductions（PA 读取同一现有源）
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
            # §15.3 八条不受影响路径的现行值（仅保 seed 完整性）
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


def _v1_bundle_content() -> dict:
    """最小 v1 旧库束（schema 1.0.0；六字段齐全；内容不代表任何期望值）。

    刻意与生产 INITIAL_BUNDLE_CONTENT 解耦：Green 后生产初始束升 v2 时，
    本构造仍稳定表达「不兼容旧库」态。
    """
    return {
        "rules_schema_version": "1.0.0",
        "effective": {"salaries": {"from_year": "2024_25"}},
        "applicability": {"years": ["2024_25", "2025_26", "2026_27"]},
        "frozen_semantics": {"profiles": []},
        "data": {
            "salaries": {
                "allowances": {
                    "2025_26": {"basic": "132000", "married": "264000"}
                }
            }
        },
        "evidence_digests": [],
    }


# ---------------------------------------------------------------------------
# v2 薪俸信封（Annex 005 §5.3 计划合成 fixture 逐字转写；PLANNED 契约件）
# ---------------------------------------------------------------------------


def _v2_salary_body() -> dict:
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
                            {
                                "from": "2025-04-01",
                                "to": "2026-04-01",
                                "value": "unmarried",
                            }
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
# 回环客户端（会话/CSRF 薄封装；C5.2/C5.3）
# ---------------------------------------------------------------------------


class _Loopback:
    """合法回环 Host/Origin 下带 CSRF 能力访问 API 的薄封装（本文件内自备）。"""

    def __init__(self, app: Any) -> None:
        self._client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=_BASE_ORIGIN
        )
        self._csrf: str | None = None
        self.cookie_value: str | None = None

    def _token(self) -> str:
        if self._csrf is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf = token
            from app.config import SESSION_COOKIE_NAME

            self.cookie_value = r.cookies.get(SESSION_COOKIE_NAME)
        return self._csrf

    def get(self, path: str):
        return self._client.get(path)

    def post(self, path: str, *, json_body: dict | None = None, headers: dict | None = None):
        h = dict(headers or {})
        h["Origin"] = _BASE_ORIGIN
        h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json_body, headers=h)

    def close(self) -> None:
        self._client.close()


def _runtime_if_match(client: _Loopback) -> dict[str, str]:
    """§4.1/§12：运行时 epoch/revision 只从 meta/version 读取，构造 If-Match。

    S002 §4.1 版本表 wire 字面量：`If-Match:"edit-<epoch>-<revision>"`（带引号；
    RFC 9110 If-Match entity-tag 语法亦要求 DQUOTE）。单一语法，不做双兼容。
    """
    r = client.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload, (
        "S002 §4.1：meta/version 须附运行时 session_epoch（Annex 005 §12），"
        f"实际：{payload!r}"
    )
    assert "input_revision" in payload, (
        "S002 §4.1：meta/version 须附运行时 input_revision（Annex 005 §12），"
        f"实际：{payload!r}"
    )
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


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



def _make_app(tmp_path, monkeypatch) -> Any:
    """在 HKTAX_DB_PATH 指向既有临时库的前提下构造 app（Red：延迟导入）。

    导入后同时保护隐式 app.main.app 与本次显式实例（幂等）。"""
    monkeypatch.setenv("HKTAX_DB_PATH", str(tmp_path / "rules.db"))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    return app


# ---------------------------------------------------------------------------
# SQLite 直读/直写工具（表名＝现行冻结存储 §3.3「存储不变」）
# ---------------------------------------------------------------------------


def _bundle_rows(db_path) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            (str(r[0]), str(r[1]), str(r[2]))
            for r in conn.execute(
                "SELECT bundle_id, bundle_hash, content_json FROM rule_bundles"
            ).fetchall()
        ]
    finally:
        conn.close()


def _pointer_rows(db_path) -> list[tuple[str]]:
    conn = sqlite3.connect(db_path)
    try:
        return [(str(r[0]),) for r in conn.execute(
            "SELECT bundle_id FROM current_pointer"
        ).fetchall()]
    finally:
        conn.close()


def _execute(db_path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _publish_into(db_path, content: dict) -> None:
    """经既有 RuleStore.publish seam 注入束（单事务原子发布；§3.3）。"""
    from app.rules.store import RuleStore

    RuleStore(db_path).publish(content, evidence_refs=[])


def _assert_fail_closed_prepare(client: _Loopback) -> None:
    """五态中非真空态：新计算 fail-closed（422 E_RULES_STATE_UNVERIFIABLE）。"""
    r = client.post(
        "/api/v1/calc/prepare",
        json_body=_v2_salary_body(),
        headers=_runtime_if_match(client),
    )
    assert r.status_code == 422, (
        f"非真空库态新计算须 fail-closed 422，实际 {r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    err = payload.get("error") or {}
    assert err.get("code") == "E_RULES_STATE_UNVERIFIABLE", (
        f"须为 E_RULES_STATE_UNVERIFIABLE（Annex 005 §15.4 fail-closed），"
        f"实际 {err.get('code')!r}"
    )
    assert "prepared_id" not in payload, "fail-closed 不得签发任何 prepared_id"


# ---------------------------------------------------------------------------
# 五态节点（S002 §9.4 REQ-12 命名映射）
# ---------------------------------------------------------------------------


def test_002_true_empty_only_seed(tmp_path, monkeypatch) -> None:
    """真空库（无指针行且零束行）是唯一允许 seed 的状态；seed 只能是 v2。

    Annex 005 §15.4 第一行＋S002 §10：Green 后 create_app 可选择发布 v2 seed
    或保持 new-calc blocked；但绝不允许再发布 v1 初始束（001 的
    「任意 AppError 即 seed v1」行为必须消失）。本测试同时验证 P1 注入路径：
    真空且未 seed 时注入固定 v2 测试 seed 后新计算可用。
    """
    db = tmp_path / "rules.db"
    app = _make_app(tmp_path, monkeypatch)
    client = _Loopback(app)
    try:
        # app 构造成功且不是错误态（§10：new-calc blocked ≠ 错误态）
        r_meta = client.get("/api/v1/meta/version")
        assert r_meta.status_code == 200, r_meta.text

        rows = _bundle_rows(db)
        for _bundle_id, _bundle_hash, content_json in rows:
            content = json.loads(content_json)
            assert content.get("rules_schema_version") == "2.0.0", (
                "真空库 seed 只能是 v2（rules_schema_version 2.0.0；"
                f"Annex 005 §15.4/S002 ADR-8），实际 "
                f"{content.get('rules_schema_version')!r}"
            )
            allowances = content.get("data", {}).get("allowances", {})
            assert allowances.get("2025_26", {}).get("basic") == {
                "amount": "132000",
                "cap": None,
                "status": "applicable",
            }, f"seed 须含 §15.2 矩阵节点 allowances.2025_26.basic：{allowances!r}"
            assert allowances.get("mpf_deduction_cap") == "18000", (
                "§15.2：唯一 MPF 数值 allowances.mpf_deduction_cap=18000"
            )
            profiles = content.get("frozen_semantics", {}).get("profiles", [])
            p5 = [p for p in profiles if p.get("profile_id") == "part5_shared_v2"]
            assert p5, "seed 须携带 part5_shared_v2 冻结 profile（§15.1）"
            assert p5[0].get("semantic_digest") == _semantic_digest(), (
                "semantic_digest 须等于本文件按 §15.1 算法独立复算值"
            )

        # P1 注入路径：构造期未 seed（保持 blocked）时，显式注入固定 v2
        # 测试 seed（既有 publish seam）→ 真空库上 seed 被接受且新计算可用
        if not rows:
            _publish_into(db, _fixed_v2_seed())
            from app.core.hashing import bundle_content_hash

            assert bundle_content_hash(_fixed_v2_seed()) == _FIXED_V2_SEED_HASH, (
                "固定 v2 测试 seed 内容漂移（canonical hash 与冻结值不符）——"
                "须同步全部协议测试文件副本并更新冻结值"
            )
        r = client.post(
            "/api/v1/calc/prepare",
            json_body=_v2_salary_body(),
            headers=_runtime_if_match(client),
        )
        assert r.status_code == 201, (
            f"真空库＋固定 v2 测试 seed 后 prepare 须 201（§10 P1 注入契约），"
            f"实际 {r.status_code}：{r.text[:300]!r}"
        )
        binding = r.json()["expected_binding"]
        assert binding["rules_schema_version"] == "2.0.0"
    finally:
        client.close()


def test_002_orphan_bundles_fail_closed_no_reseed(tmp_path, monkeypatch) -> None:
    """孤儿束（有束行、无指针行）→ fail-closed；不得当空库 reseed。"""
    db = tmp_path / "rules.db"
    _publish_into(db, _v1_bundle_content())
    _execute(db, "DELETE FROM current_pointer")
    assert _bundle_rows(db) and not _pointer_rows(db), "前置：孤儿束态构造失败"

    app = _make_app(tmp_path, monkeypatch)
    client = _Loopback(app)
    try:
        rows_after = _bundle_rows(db)
        assert len(rows_after) == 1, (
            f"孤儿束不得触发 reseed（Annex 005 §15.4 第二行；不得当空库），"
            f"束行数 {len(rows_after)}（构造前 1）"
        )
        assert not _pointer_rows(db), "孤儿束态不得写入指针行"
        _assert_fail_closed_prepare(client)
    finally:
        client.close()


def test_002_dangling_pointer_fail_closed(tmp_path, monkeypatch) -> None:
    """悬空指针（指针行引用不存在的 bundle）→ fail-closed，不 reseed。"""
    db = tmp_path / "rules.db"
    _publish_into(db, _v1_bundle_content())
    _execute(db, "UPDATE current_pointer SET bundle_id = 'b-does-not-exist'")
    assert _pointer_rows(db) == [("b-does-not-exist",)], "前置：悬空指针构造失败"

    app = _make_app(tmp_path, monkeypatch)
    client = _Loopback(app)
    try:
        assert len(_bundle_rows(db)) == 1, (
            "悬空指针不得触发 reseed（Annex 005 §15.4 第三行），束行数"
            f" {len(_bundle_rows(db))}"
        )
        assert _pointer_rows(db) == [("b-does-not-exist",)], (
            "悬空指针不得被改写/修复（fail-closed，非静默迁移）"
        )
        _assert_fail_closed_prepare(client)
    finally:
        client.close()


def test_002_hash_corruption_fail_closed(tmp_path, monkeypatch) -> None:
    """内容/哈希损坏（载入内容与 bundle_hash 不符）→ fail-closed，不 reseed。"""
    db = tmp_path / "rules.db"
    _publish_into(db, _fixed_v2_seed())
    (bundle_id, _hash, content_json), = _bundle_rows(db)
    corrupted = json.loads(content_json)
    corrupted["data"]["allowances"]["2025_26"]["basic"]["amount"] = "999000"
    _execute(
        db,
        "UPDATE rule_bundles SET content_json = ? WHERE bundle_id = ?",
        (json.dumps(corrupted, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")), bundle_id),
    )

    app = _make_app(tmp_path, monkeypatch)
    client = _Loopback(app)
    try:
        rows_after = _bundle_rows(db)
        assert len(rows_after) == 1, (
            f"哈希损坏不得触发 reseed（Annex 005 §15.4 第四行），束行数"
            f" {len(rows_after)}"
        )
        assert _pointer_rows(db) == [(bundle_id,)], "指针不得被改写"
        _assert_fail_closed_prepare(client)
    finally:
        client.close()


def test_002_incompatible_bundle_blocks_calc_replays_kept(
    tmp_path, monkeypatch
) -> None:
    """不兼容旧库（schema 1.0.0 current）→ fail-closed 阻新计算、不静默迁移；
    旧记录查看不受影响（§15.4 第五行）。"""
    db = tmp_path / "rules.db"
    _publish_into(db, _v1_bundle_content())
    (v1_bundle_id, _h, _c), = _bundle_rows(db)

    app = _make_app(tmp_path, monkeypatch)
    client = _Loopback(app)
    try:
        # 不静默迁移：束行/指针/schema 原样
        rows_after = _bundle_rows(db)
        assert len(rows_after) == 1, (
            f"不兼容旧库不得 reseed/迁移出新束，束行数 {len(rows_after)}"
        )
        assert _pointer_rows(db) == [(v1_bundle_id,)], "指针不得被迁移改写"
        assert json.loads(rows_after[0][2])["rules_schema_version"] == "1.0.0"

        # fail-closed 阻新计算（而不是由旧校验以 E_INPUT_TYPE 拒 2.0.0）
        _assert_fail_closed_prepare(client)

        # app 不是错误态：meta 可用
        assert client.get("/api/v1/meta/version").status_code == 200

        # 旧记录查看不受影响：注入内存 record 后仍原样可读
        legacy_core = {
            "tax_type": "salaries_tax",
            "period": "2025_26",
            "input": {"year_of_assessment": "2025_26"},
            "binding": {
                "bundle_id": v1_bundle_id,
                "bundle_hash": _h,
                "rules_schema_version": "1.0.0",
                "engine_version": "0.1.0",
            },
            "status": "complete",
            "amounts": {"final_after_reduction": "940"},
        }
        client._token()  # 建立会话
        registry = app.state.registry
        sid = registry.sid_from_cookie(client.cookie_value)
        assert sid, "会话 cookie 未能解析出 sid"
        registry.save_record(sid, {"record_id": "r-legacy-1", "core": legacy_core})
        r_record = client.get("/api/v1/records/r-legacy-1")
        assert r_record.status_code == 200, r_record.text
        assert r_record.json()["record"]["amounts"] == {"final_after_reduction": "940"}, (
            "不兼容旧库下旧记录查看必须原样返回（§15.4 第五行）"
        )
    finally:
        client.close()


# ===========================================================================
# 新 Red：准入双向核验（复核报告 §五.5：R1/Bu4——缺 profile／篡改 P 自洽
# digest／七目标共同兼容检查）
# ===========================================================================


def _seeded_app(tmp_path, monkeypatch, seed: dict) -> Any:
    db = tmp_path / "rules.db"
    _publish_into(db, seed)
    monkeypatch.setenv("HKTAX_DB_PATH", str(db))
    from app.main import app as implicit_main_app, create_app

    app = _harden_app(create_app())
    _harden_app(implicit_main_app)
    return app


def _assert_rules_unverifiable_prepare(client: _Loopback, label: str) -> None:
    r = client.post(
        "/api/v1/calc/prepare",
        json_body=_v2_salary_body(),
        headers=_runtime_if_match(client),
    )
    assert r.status_code == 422, (
        f"{label}：须 fail-closed 422 E_RULES_STATE_UNVERIFIABLE，实际 "
        f"{r.status_code}：{r.text[:250]!r}"
    )
    payload = r.json()
    assert (payload.get("error") or {}).get("code") == "E_RULES_STATE_UNVERIFIABLE", (
        f"{label}：实际 code {(payload.get('error') or {}).get('code')!r}"
    )
    assert "prepared_id" not in payload


def test_002_missing_profile_blocks_calc(tmp_path, monkeypatch) -> None:
    """束内缺 part5_shared_v2 profile → fail-closed 拒算（005 §15.4「profile
    预期 P/digest 不符」＋§2.1 双向核验；R1 探针：删 profile 仍计算）。"""
    import copy as _copy

    seed = _copy.deepcopy(_fixed_v2_seed())
    seed["frozen_semantics"]["profiles"] = []
    app = _seeded_app(tmp_path, monkeypatch, seed)
    client = _Loopback(app)
    try:
        _assert_rules_unverifiable_prepare(client, "缺 part5 profile")
    finally:
        client.close()


def test_002_tampered_profile_self_consistent_digest_blocks(tmp_path, monkeypatch) -> None:
    """篡改 P 后重算自洽 digest（digest 匹配被改 P，但 P≠引擎固定 P）→ 仍须
    fail-closed（005 §15.1：兼容检查须同时验证「保存 digest 匹配 P」与
    「P 等于本引擎支持的固定 P」，不得只验证自洽；R1②）。"""
    import copy as _copy

    seed = _copy.deepcopy(_fixed_v2_seed())
    tampered_p = _part5_profile()
    tampered_p["rounding"]["salary"] = "bogus_floor_v9"
    tampered_digest = "sha256:" + hashlib.sha256(
        _canonical_bytes(tampered_p)
    ).hexdigest()
    tampered_p["semantic_digest"] = tampered_digest
    seed["frozen_semantics"]["profiles"] = [tampered_p]
    app = _seeded_app(tmp_path, monkeypatch, seed)
    client = _Loopback(app)
    try:
        _assert_rules_unverifiable_prepare(client, "篡改 P 自洽 digest")
    finally:
        client.close()


_FIVE_TAX_V1_INPUTS: dict[str, dict] = {
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
    "stamp_property": {
        "instrument_date": "2025-06-20",
        "instrument_kind": "agreement_for_sale",
    },
    "stamp_stock": {
        "instrument_date": "2025-06-10",
        "complete": {"value": True},
        "documents": [
            {"doc_kind": "contract_note_sold", "consideration": "50000", "value": "50000"}
        ],
    },
    "stamp_lease": {"instrument_date": "2025-06-20"},
}


def test_002_shared_compat_guard_blocks_all_seven_targets_on_incompatible_store(
    tmp_path, monkeypatch
) -> None:
    """不兼容旧库（schema 1.0.0 current）→ 七目标共同兼容检查：其余五税
    prepare 亦须 fail-closed（005 §2.2「engine/hash/schema/profile 兼容检查
    为七目标共同」；复核报告 Bu4/R1：五税现无 schema/profile 检查）。"""
    app = _seeded_app(tmp_path, monkeypatch, _v1_bundle_content())
    client = _Loopback(app)
    try:
        for tax_type, facts in _FIVE_TAX_V1_INPUTS.items():
            r = client.post(
                "/api/v1/calc/prepare",
                json_body={
                    "tax_type": tax_type,
                    "schema_version": "1.0.0",
                    "input": facts,
                },
                headers=_runtime_if_match(client),
            )
            assert r.status_code == 422, (
                f"{tax_type}：不兼容旧库上五税 prepare 亦须 fail-closed 422"
                f"（七目标共同兼容检查，Annex 005 §2.2/Bu4），实际 "
                f"{r.status_code}：{r.text[:200]!r}"
            )
            code = (r.json().get("error") or {}).get("code")
            assert code == "E_RULES_STATE_UNVERIFIABLE", (
                f"{tax_type}：实际 code {code!r}"
            )
            assert "prepared_id" not in r.json()
    finally:
        client.close()
