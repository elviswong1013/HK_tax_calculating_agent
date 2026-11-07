# -*- coding: utf-8 -*-
"""REQ-2/7/8/11 条件 assessment：inactive 可省、present 即严格、稀疏 actual.pa
（002 P1 Red；Annex 005 §4.10）。

REQ: REQ-2/7/8/11（S002 §8/§9.4＋006 §5.8 B 合同；PRD AC-2/7/8/11）。
G0 修正后共 3 个命名节点（原 006 §5.8 两节点＋G0 新增稀疏正例）：
  - test_002_single_salary_api_ui_without_inactive_assessment_fields
  - test_002_assessment_fields_follow_active_path_without_fabricated_defaults
  - test_002_sparse_pa_only_active_key_accepted_no_fabrication
    （复核报告 §五.2/测试完整性 3：s.29 分支 A 的 none 稀疏正例——仅活动键）

规格锚点:
  - S002 §3.1（assessment 的 basis/candidates/selection **无条件必填**；
    `actual` 对象及其成员按活动消费者条件出现——省略保持省略，**不写回
    none/ordinary/日期**）＋§5.3/Annex 005 §5.3（计划合成 fixture：单一薪俸
    最简形式；计划断言＝不追问 PA 日期/history/选择；canonical input 不补
    这些键；current final 940、partial、next/balance=null；改为 hypothetical
    仍不补 none/ordinary）。
  - Annex 005 §4.10 显式依赖表（7 行）＋有限依赖与短路规则（8 条）：
    1）明确全年未婚＋仅 salary_separate/basic → 不读取 inactive 字段；
    5）active actual PA：日期取实际 election date；joint 双方一致；同时提交
    且不等 → 报矛盾，不静默择一；
    7）actual.pa 稀疏 map：none 禁止 date；individual/joint 的 date 仅在日期
    消费者活动时必填；配对 mode/日期互相冲突仍拒绝；键只允许当年
    participant；
    8）显式已知选择与请求冲突仍走既有矛盾/转换规则。
    内部 EffectiveAssessment 勘误：pa_by_person 只复制实际存在的条目、允许
    缺键、不为缺键插入 none/unknown。
  - S002 §4.2/§4.3＋Annex 005 §13（needs_input → 422＋questions[] 定位
    {field,year,subject_id?,question_zh}，无 prepared_id）。
  - Annex 005 §6.1（NAI=0 不得推断 AI；正 NAI×AI=false → 矛盾——本文件
   配偶零收入事实与 AI=false 显式共存合法）。

期望值来源（禁从生产代码反推）:
  - 3940/3000/940：PAM39 Q1（T2 台账 1.13）＋Annex 005 §5.3/§14 冻结计划锚。
  - 字段条件规则：Annex 005 §4.10 依赖表（冻结）。
  - 其余为结构/状态断言，无金额期望。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - 单一薪俸最简 fixture（assessment 仅 basis/candidates/selection）→
    prepare 201、execute 200：final 940、status partial、next/balance null；
    record 的 canonical input 中 assessment 键集恰为 {basis,candidates,
    selection}（不补 reference_date/actual/pa/history/日期）；hypothetical
    basis 同样不补默认。
  - 活动消费者缺事实 → 422＋精确追问（定位 assessment.actual.pa.<p>.mode，
    subject_id 指向该配偶），且不追问 inactive 字段（reference_date）；
    present 字段严格校验：none+date 拒、joint 配对日期冲突拒、未知键拒；
    稀疏 map（含所需键）→ 201 且不补写缺席键。
  - 本文件对 app.* 延迟导入；Red 失败原因＝缺失 v2 行为（旧校验拒绝
    2.0.0），非收集期失败。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

_BASE_ORIGIN = "http://127.0.0.1:8000"
_PREPARE = "/api/v1/calc/prepare"
_CONFIRM = "/api/v1/calc/confirm"
_SALARIES_EXECUTE = "/api/v1/calc/salaries-tax"


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


# ---------------------------------------------------------------------------
# 载荷构造（Annex 005 §5.3 最小可省形式＝权威最简 fixture）
# ---------------------------------------------------------------------------


def _single_salary_body(basis: str = "actual") -> dict:
    """单一薪俸最简形式（§5.3）：assessment 仅 basis/candidates/selection。"""
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
                            "source_classification": {
                                "salary": "confirmed_in_scope"
                            },
                            "properties": [],
                            "businesses": [],
                            "deduction_items": [],
                        }
                    },
                    "assessment": {
                        "basis": basis,
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


def _zero_income_spouse_body(actual_pa: dict | None) -> dict:
    """已婚＋配偶无薪俸 AI（显式 false、NAI 0）：s.29 分支 A 的 PA mode 消费者激活。

    actual_pa=None → 稀疏缺 p2 键（active unknown 应被精确追问）；
    否则以给定稀疏 map 提交（§4.10 规则 7）。
    """
    year: dict = {
        "participants": [
            {"person_id": "p1", "role": "self"},
            {"person_id": "p2", "role": "spouse"},
        ],
        "subject_facts": {},
        "household": {
            "marriage": [
                {
                    "from": "2025-04-01",
                    "to": "2026-04-01",
                    "value": "married",
                    "spouse_id": "p2",
                }
            ],
            "living_apart": [{"from": "2025-04-01", "to": "2026-04-01", "value": False}],
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
            },
            "p2": {
                "employment_income_nai": "0",
                "mpf_mandatory_contributions": "0",
                "has_assessable_salary_income": False,
                "properties": [],
                "businesses": [],
                "deduction_items": [],
            },
        },
        "assessment": {
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        },
        "manual_issues": [],
        "provisional_paid": "0",
    }
    if actual_pa is not None:
        year["assessment"]["actual"] = {"pa": actual_pa}
    return {
        "tax_type": "salaries_tax",
        "schema_version": "2.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "subjects": [
                {"subject_id": "p1", "birth_date": "1990-01-01"},
                {"subject_id": "p2", "birth_date": "1992-01-01"},
            ],
            "years": {"2025_26": year},
            "hk_presence_days": [],
            "assumptions": [],
        },
    }


# ---------------------------------------------------------------------------
# 客户端 fixture（本文件自备）
# ---------------------------------------------------------------------------


class _Loopback:
    def __init__(self, app: Any) -> None:
        self._client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url=_BASE_ORIGIN
        )
        self._csrf: str | None = None

    def _token(self) -> str:
        if self._csrf is None:
            r = self._client.get("/")
            r.raise_for_status()
            token = r.headers.get("X-CSRF-Token")
            assert token, "安全 GET 未发放 X-CSRF-Token（C5.2/C5.3）"
            self._csrf = token
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
    scope teardown 独立执行。"""
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


def _fresh_if_match(client: _Loopback) -> dict[str, str]:
    r = client.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, (
        "S002 §4.1：meta/version 须附运行时 session_epoch/input_revision，"
        f"实际：{payload!r}"
    )
    # S002 §4.1 版本表 wire 字面量：`If-Match:"edit-<epoch>-<revision>"`（带引号；
    # RFC 9110 If-Match entity-tag 语法亦要求 DQUOTE）。单一语法，不做双兼容。
    return {
        "If-Match": (
            f'"edit-{int(payload["session_epoch"])}-{int(payload["input_revision"])}"'
        )
    }


def _error_of(payload: dict) -> dict:
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状 §4.3，实际：{payload!r}"
    return err


def _prepare(client: _Loopback, body: dict):
    return client.post(_PREPARE, json_body=body, headers=_fresh_if_match(client))


def _prepare_ok(client: _Loopback, body: dict) -> dict:
    r = _prepare(client, body)
    assert r.status_code == 201, (
        f"prepare 须 201，实际 {r.status_code}：{r.text[:300]!r}"
    )
    return r.json()


def _confirm_and_execute(client: _Loopback, prepared: dict) -> dict:
    r_confirm = client.post(
        _CONFIRM,
        json_body={
            "prepared_id": prepared["prepared_id"],
            "canonical_input_hash": prepared["input_hash"],
            "expected_binding": prepared["expected_binding"],
            "acknowledge": True,
        },
    )
    assert r_confirm.status_code == 200, (
        f"confirm 须 200，实际 {r_confirm.status_code}：{r_confirm.text[:300]!r}"
    )
    r_exec = client.post(
        _SALARIES_EXECUTE,
        json_body={"confirmation_id": r_confirm.json()["confirmation_id"]},
    )
    assert r_exec.status_code == 200, (
        f"execute 须 200，实际 {r_exec.status_code}：{r_exec.text[:300]!r}"
    )
    return r_exec.json()


# ---------------------------------------------------------------------------
# 节点 1：最简单一薪俸——inactive assessment 字段可省且不写回
# ---------------------------------------------------------------------------


def test_002_single_salary_api_ui_without_inactive_assessment_fields(v2) -> None:
    for basis in ("actual", "hypothetical"):
        prepared = _prepare_ok(v2, _single_salary_body(basis))
        payload = _confirm_and_execute(v2, prepared)

        # §5.3 计划断言：current final 940、partial、next/balance null
        amounts = payload.get("amounts") or {}
        assert amounts.get("before_reduction") == "3940", amounts
        assert amounts.get("reduction") == "3000", amounts
        assert amounts.get("final_after_reduction") == "940", (
            f"basis={basis}：final 须与已核口径一致（940），实际 {amounts!r}"
        )
        assert payload.get("status") == "partial", (
            f"basis={basis}：缺未来事实 → partial，实际 {payload.get('status')!r}"
        )
        assert amounts.get("next_provisional") is None
        assert amounts.get("balance") is None

        # canonical input 不补 inactive 键（不写回 none/ordinary/日期）
        r_record = v2.get(f"/api/v1/records/{payload['record_id']}")
        assert r_record.status_code == 200, r_record.text
        record_year = r_record.json()["record"]["input"]["years"]["2025_26"]
        assessment = record_year["assessment"]
        assert set(assessment.keys()) == {"basis", "candidates", "selection"}, (
            f"basis={basis}：assessment 须保持最简（不补 reference_date/actual/"
            f"pa/history），实际键集：{sorted(assessment.keys())}"
        )
        assert record_year["subject_facts"] == {}, (
            f"basis={basis}：不得补写 subject_facts 默认资格"
        )
        assert set(record_year["financials"]["p1"].keys()) == {
            "employment_income_nai",
            "mpf_mandatory_contributions",
            "has_assessable_salary_income",
            "source_classification",
            "properties",
            "businesses",
            "deduction_items",
        }, (
            f"basis={basis}：financials 不得补写默认键，实际："
            f"{sorted(record_year['financials']['p1'].keys())}"
        )


# ---------------------------------------------------------------------------
# 节点 2：活动路径精确追问＋present 严格校验＋稀疏 actual.pa
# ---------------------------------------------------------------------------


def test_002_assessment_fields_follow_active_path_without_fabricated_defaults(v2) -> None:
    # 最小纠错（F4）：本节点全部调用均属 ordinary 夫妻 actual separate 上下文；
    # 补实际 salary 模式事实（elected=false），保留各自原断言/缺陷隔离。
    def _body(actual_pa):
        body = _zero_income_spouse_body(actual_pa)
        body["input"]["years"]["2025_26"]["assessment"].setdefault(
            "actual", {}
        )["salary_joint_elected"] = False
        return body

    # —— (i) 活动消费者缺键 → 精确追问 assessment.actual.pa.p2.mode ——
    r_missing = _prepare(v2, _body(actual_pa=None))
    assert r_missing.status_code == 422, (
        f"s.29 分支 A 激活而配偶 PA mode 缺键 → 须 needs_input 422，实际 "
        f"{r_missing.status_code}：{r_missing.text[:300]!r}"
    )
    payload = r_missing.json()
    assert "prepared_id" not in payload
    questions = payload.get("questions")
    assert isinstance(questions, list) and questions, (
        f"须含非空 questions[]（Annex 005 §13），实际：{payload!r}"
    )
    assert any(
        isinstance(q, dict)
        and "pa" in str(q.get("field", ""))
        and (q.get("subject_id") == "p2" or "p2" in str(q.get("field", "")))
        for q in questions
    ), f"须精确追问 assessment.actual.pa.p2.mode（定位 p2），实际：{questions!r}"
    assert not any(
        isinstance(q, dict) and "reference_date" in str(q.get("field", ""))
        for q in questions
    ), "普通 salary 请求不得追问 reference_date（§4.10 依赖表第 1 行：inactive）"

    # —— (ii) present 字段严格校验 ——
    # none 禁止 date（§4.10 规则 7）
    r_none_date = _prepare(
        v2,
        _body(
            actual_pa={"p1": {"mode": "none", "date": "2025-11-01"}}
        ),
    )
    assert r_none_date.status_code == 422, (
        f"actual.pa mode=none 携带 date 须拒绝（§4.10），实际 "
        f"{r_none_date.status_code}"
    )
    err = _error_of(r_none_date.json())
    assert "pa" in str(err.get("field", "")) + json.dumps(
        err.get("details") or [], ensure_ascii=False
    ), f"错误须定位 actual.pa，实际：{err!r}"

    # joint 配对日期冲突 → 拒绝（不静默择一；§4.10 规则 5/7）
    r_conflict = _prepare(
        v2,
        _body(
            actual_pa={
                "p1": {"mode": "joint", "date": "2025-11-01"},
                "p2": {"mode": "joint", "date": "2025-12-01"},
            }
        ),
    )
    assert r_conflict.status_code == 422, (
        f"joint 双方日期不一致须报矛盾（不静默择一），实际 "
        f"{r_conflict.status_code}"
    )
    err_conflict = _error_of(r_conflict.json())
    assert "pa" in str(err_conflict.get("field", "")) + json.dumps(
        err_conflict.get("details") or [], ensure_ascii=False
    ), f"错误须定位 actual.pa，实际：{err_conflict!r}"

    # 未知键（非当年 participant）→ 拒绝（§4.10：键只允许当年 participant）
    r_unknown_key = _prepare(
        v2, _body(actual_pa={"p9": {"mode": "none"}})
    )
    assert r_unknown_key.status_code == 422, (
        f"actual.pa 键只允许当年 participant，实际 {r_unknown_key.status_code}"
    )

    # —— (iii) 稀疏 map 含所需键 → 201 且不补写缺席键 ——
    prepared = _prepare_ok(
        v2,
        _body(
            actual_pa={"p1": {"mode": "none"}, "p2": {"mode": "none"}}
        ),
    )
    payload_ok = _confirm_and_execute(v2, prepared)
    r_record = v2.get(f"/api/v1/records/{payload_ok['record_id']}")
    assert r_record.status_code == 200, r_record.text
    actual_pa = (
        r_record.json()["record"]["input"]["years"]["2025_26"]["assessment"]
        .get("actual", {})
        .get("pa", {})
    )
    assert actual_pa == {
        "p1": {"mode": "none"},
        "p2": {"mode": "none"},
    }, f"actual.pa 须原样稀疏保留（不为缺键插入对象），实际：{actual_pa!r}"


# ===========================================================================
# 新 Red：s.29 分支 A 的 none 稀疏正例（复核报告 §五.2/测试完整性 3）
# ===========================================================================


def test_002_sparse_pa_only_active_key_accepted_no_fabrication(v2) -> None:
    """仅提交**活动键** p2:{mode:none}（p1 缺席）→ 201；record 的 actual.pa
    键集恰为 {p2}（不补 p1/none/日期；005 §4.10 规则 7＋EffectiveAssessment
    勘误：pa_by_person 只复制实际存在的条目、允许缺键）。"""
    body = _zero_income_spouse_body(actual_pa={"p2": {"mode": "none"}})
    body["input"]["years"]["2025_26"]["assessment"]["actual"][
        "salary_joint_elected"
    ] = False
    prepared = _prepare_ok(v2, body)
    payload = _confirm_and_execute(v2, prepared)

    r_record = v2.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200, r_record.text
    actual_pa = (
        r_record.json()["record"]["input"]["years"]["2025_26"]["assessment"]
        .get("actual", {})
        .get("pa", {})
    )
    assert set(actual_pa.keys()) == {"p2"}, (
        f"稀疏 map 须仅保留已提交的活动键 p2（不得补写 p1 或任何缺键对象），"
        f"实际键集：{sorted(actual_pa.keys())}"
    )
    assert actual_pa["p2"] == {"mode": "none"}
