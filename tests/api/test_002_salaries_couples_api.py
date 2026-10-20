# -*- coding: utf-8 -*-
"""REQ-7/11 夫妻薪俸税 v2 字段经 prepare 的可达性与冲突（002 P1 Red）。

REQ: REQ-7（S002 §8/§9.4；PRD AC-7）＋REQ-11 信封/确认链。G0 修正后共 6 个
命名节点（原 §9.4 REQ-7 API 四节点＋G0 新增 R7 两节点）：
  - test_002_couple_fields_reach_engine
  - test_002_joint_unknown_asks_no_optimal
  - test_002_spouse_separate_pa_election_fact
  - test_002_actual_hypothetical_nomination_wire_fields
  - test_002_known_selected_retained_with_unknown_competitor（§五.7/R7）
  - test_002_all_candidates_ineligible_blocked_not_needs_input（§五.7/R7/B5）

规格锚点:
  - S002 §3.1（信封 {tax_type, schema_version:"2.0.0", input}；input 顶层封闭
    键；每年 participants 恰 1 self ≤1 spouse；assessment 另含 actual/
    hypothetical 独立 salary-joint 提名字段）＋§5.3（提名字段
    Year.assessment.actual.salary_joint_nomination?/
    hypothetical_salary_joint_nomination?＝{status, person_id}；与
    household.child_nomination 严格不同；需归属而缺失/unknown → needs_input；
    none → 要求指定；不得从 child nominee/收入较高者/self 推断）。
  - Annex 005 §4.2/§4.6（participants/financials 封闭表；has_assessable_
    salary_income 三态）、§4.10（Nomination 类型：named 必须引用当年两名
    participant 之一；其他 status 必须 person_id=null；actual.pa 稀疏 map：
    键只允许当年 participant）、§6.1（正 NAI×AI=false → 矛盾）、
    §9.1（夫妻真值表；婚姻缺失不默认 single）。
  - S002 §4.2/§4.3＋Annex 005 §13（needs_input → 422＋questions[]（field/
    year/subject_id?/question_zh）无 prepared_id；E_INPUT_CONTRADICTORY 用于
    引用/矛盾类结构拒绝）。
  - Annex 005 §5.4（同 YA 顺序配偶 fixture 的 assessment 显式完整形式示范）。

期望值来源（禁从生产代码反推）:
  - 字段形状/校验规则：Annex 005 §4 封闭字段表（冻结）。
  - 夫妻载荷事实（500000/200000＋MPF 18000）：B1 T3 手算案例同一载荷
    （S002 §5.3；仅作输入，本文件不断言该案例税额）。
  - 其余为结构/状态断言，无金额期望。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - 普通夫妻 v2 信封（双 participants、双 financials、married MarriageTF
    带 spouse_id、living_apart、child_nomination、actual/hypothetical 提名、
    salary_separate/salary_joint 候选）经 prepare＋If-Match → 201、经
    confirm→execute → 200；record 输入保留全部夫妻字段。
  - 活动路径 unknown（配偶 AI unknown＋joint 候选）→ 422＋questions 定位
    该事实＋无 prepared_id（不产伪确定最优）。
  - 配偶实际 PA 选择事实（actual.pa 稀疏 individual+date）可提交且严格
    校验、不补写缺席键。
  - 提名字段 wire 校验：named 须为当年两名 participant 之一；非 named
    status 须 person_id=null；unknown/none＋joint 请求 → needs_input
    questions；一致具名 → 201。
  - 本文件对 app.* 延迟导入；Red 失败原因＝缺失 v2 行为（旧校验拒绝
    2.0.0／无 v2 字段解析），非收集期失败。
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
# 夫妻 v2 载荷构造（Annex 005 §4 封闭表；§5.4 显式完整 assessment 形式）
# ---------------------------------------------------------------------------


def _couple_body(**overrides) -> dict:
    """普通全年在婚夫妻（双 participants/双 financials）；overrides 深覆盖。

    载荷事实＝B1 T3 手算案例同源（500000/200000、MPF 各 18000；S002 §5.3）。
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
                "employment_income_nai": "500000",
                "mpf_mandatory_contributions": "18000",
                "has_assessable_salary_income": True,
                "source_classification": {"salary": "confirmed_in_scope"},
                "properties": [],
                "businesses": [],
                "deduction_items": [],
            },
            "p2": {
                "employment_income_nai": "200000",
                "mpf_mandatory_contributions": "18000",
                "has_assessable_salary_income": True,
                "source_classification": {"salary": "confirmed_in_scope"},
                "properties": [],
                "businesses": [],
                "deduction_items": [],
            },
        },
        "assessment": {
            "basis": "hypothetical",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
                "history": "ordinary",
            },
            "hypothetical_salary_joint_nomination": {
                "status": "named",
                "person_id": "p1",
            },
            "candidates": ["salary_separate", "salary_joint"],
            "selection": "salary_joint",
        },
        "manual_issues": [],
        "provisional_paid": "0",
    }
    body: dict = {
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
    for key, value in overrides.items():
        if key == "assessment":
            body["input"]["years"]["2025_26"]["assessment"] = value
        elif key == "financials_p2":
            body["input"]["years"]["2025_26"]["financials"]["p2"].update(value)
        elif key == "extra_subject":
            body["input"]["subjects"].append(value)
        else:
            body["input"]["years"]["2025_26"][key] = value
    return body


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


def _prepare_ok(client: _Loopback, body: dict) -> dict:
    r = client.post(_PREPARE, json_body=body, headers=_fresh_if_match(client))
    assert r.status_code == 201, (
        f"prepare 须 201，实际 {r.status_code}：{r.text[:300]!r}"
    )
    return r.json()


def _confirm_and_execute(client: _Loopback, prepared: dict):
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
    confirmation_id = r_confirm.json()["confirmation_id"]
    r_exec = client.post(
        _SALARIES_EXECUTE, json_body={"confirmation_id": confirmation_id}
    )
    assert r_exec.status_code == 200, (
        f"execute 须 200，实际 {r_exec.status_code}：{r_exec.text[:300]!r}"
    )
    return r_exec.json()


_SIX_KEYS = {
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
}


# ---------------------------------------------------------------------------
# 节点 1：夫妻字段经 prepare/confirm/execute 到达引擎
# ---------------------------------------------------------------------------


def test_002_couple_fields_reach_engine(v2) -> None:
    body = _couple_body()
    prepared = _prepare_ok(v2, body)
    payload = _confirm_and_execute(v2, prepared)

    assert set(payload.get("amounts") or {}) == _SIX_KEYS, (
        f"执行结果须含六金额键（C3.3），实际：{payload.get('amounts')!r}"
    )
    assert set(payload["binding"]) == {
        "bundle_id", "bundle_hash", "rules_schema_version", "engine_version",
    }, f"binding 须为四元组，实际：{payload['binding']!r}"

    # record 输入保留全部夫妻字段（引擎确实收到这些事实）
    r_record = v2.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200, r_record.text
    record = r_record.json()["record"]
    record_year = record["input"]["years"]["2025_26"]
    assert record_year["household"]["marriage"][0]["spouse_id"] == "p2"
    assert {p["person_id"] for p in record_year["participants"]} == {"p1", "p2"}
    assert set(record_year["financials"]) == {"p1", "p2"}
    assessment = record_year["assessment"]
    assert assessment["hypothetical_salary_joint_nomination"] == {
        "status": "named",
        "person_id": "p1",
    }
    assert assessment["candidates"] == ["salary_separate", "salary_joint"]


# ---------------------------------------------------------------------------
# 节点 2：joint 活动路径 unknown → 追问、不产伪最优
# ---------------------------------------------------------------------------


def test_002_joint_unknown_asks_no_optimal(v2) -> None:
    body = _couple_body(financials_p2={"has_assessable_salary_income": "unknown"})
    r = v2.post(_PREPARE, json_body=body, headers=_fresh_if_match(v2))
    assert r.status_code == 422, (
        f"配偶 AI unknown＋joint 候选为活动路径 → 须 needs_input 422，实际 "
        f"{r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    assert "prepared_id" not in payload, "unknown 不得签发能力/伪确定最优"
    questions = payload.get("questions")
    assert isinstance(questions, list) and questions, (
        f"须含非空 questions[] 定位未知事实（Annex 005 §13），实际：{payload!r}"
    )
    assert any(
        ("has_assessable_salary_income" in str(q.get("field", "")))
        or (q.get("subject_id") == "p2")
        for q in questions
        if isinstance(q, dict)
    ), f"须定位配偶 p2 的薪俸 AI 存在性事实，实际：{questions!r}"


# ---------------------------------------------------------------------------
# 节点 3：配偶实际 PA 选择事实（稀疏 individual+date）可提交且不补写
# ---------------------------------------------------------------------------


def test_002_spouse_separate_pa_election_fact(v2) -> None:
    body = _couple_body(
        assessment={
            "basis": "actual",
            "actual": {
                "salary_joint_elected": False,
                "pa": {"p2": {"mode": "individual", "date": "2025-11-01"}},
            },
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
        }
    )
    prepared = _prepare_ok(v2, body)
    payload = _confirm_and_execute(v2, prepared)

    r_record = v2.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200, r_record.text
    actual_pa = (
        r_record.json()["record"]["input"]["years"]["2025_26"]["assessment"]
        .get("actual", {})
        .get("pa", {})
    )
    assert actual_pa == {"p2": {"mode": "individual", "date": "2025-11-01"}}, (
        f"actual.pa 须稀疏保留（不补 p1/none/日期；Annex 005 §4.10），"
        f"实际：{actual_pa!r}"
    )


# ---------------------------------------------------------------------------
# 节点 4：actual/hypothetical 提名字段 wire 校验
# ---------------------------------------------------------------------------


def test_002_actual_hypothetical_nomination_wire_fields(v2) -> None:
    # —— (a) 一致具名（两字段独立、均指向当年 participant）→ 201 ——
    body_ok = _couple_body()
    body_ok["input"]["years"]["2025_26"]["assessment"]["actual"][
        "salary_joint_nomination"
    ] = {"status": "named", "person_id": "p1"}
    assert (
        v2.post(_PREPARE, json_body=body_ok, headers=_fresh_if_match(v2)).status_code
        == 201
    ), "一致具名提名须被接受"

    # —— (b) named 引用已注册但非当年 participant 的 p3 → 结构拒绝 ——
    body_p3 = _couple_body(extra_subject={"subject_id": "p3"})
    body_p3["input"]["years"]["2025_26"]["assessment"][
        "hypothetical_salary_joint_nomination"
    ] = {"status": "named", "person_id": "p3"}
    r_p3 = v2.post(_PREPARE, json_body=body_p3, headers=_fresh_if_match(v2))
    assert r_p3.status_code == 422, (
        f"named 必须引用当年两名 participant 之一（Annex 005 §4.10），实际 "
        f"{r_p3.status_code}：{r_p3.text[:200]!r}"
    )
    err = _error_of(r_p3.json())
    assert "nomination" in (str(err.get("field", "")) + json.dumps(
        err.get("details") or [], ensure_ascii=False
    )), f"错误须定位提名字段，实际：{err!r}"
    assert "prepared_id" not in r_p3.json()

    # —— (c) 非 named status 携带 person_id → 类型拒绝 ——
    body_pid = _couple_body()
    body_pid["input"]["years"]["2025_26"]["assessment"][
        "hypothetical_salary_joint_nomination"
    ] = {"status": "unknown", "person_id": "p1"}
    r_pid = v2.post(_PREPARE, json_body=body_pid, headers=_fresh_if_match(v2))
    assert r_pid.status_code == 422, (
        f"unknown 等状态必须 person_id=null（Annex 005 §4.10），实际 "
        f"{r_pid.status_code}"
    )
    err_pid = _error_of(r_pid.json())
    assert "nomination" in str(err_pid.get("field", "")) + json.dumps(
        err_pid.get("details") or [], ensure_ascii=False
    ), f"错误须定位提名字段，实际：{err_pid!r}"

    # —— (d) unknown（person_id=null）＋ joint 请求 → needs_input 追问 ——
    body_unknown = _couple_body()
    body_unknown["input"]["years"]["2025_26"]["assessment"][
        "hypothetical_salary_joint_nomination"
    ] = {"status": "unknown", "person_id": None}
    r_unknown = v2.post(
        _PREPARE, json_body=body_unknown, headers=_fresh_if_match(v2)
    )
    assert r_unknown.status_code == 422, (
        f"需归属而 unknown → needs_input（S002 §5.3），实际 "
        f"{r_unknown.status_code}：{r_unknown.text[:200]!r}"
    )
    payload_unknown = r_unknown.json()
    assert "prepared_id" not in payload_unknown
    questions = payload_unknown.get("questions")
    assert isinstance(questions, list) and questions, "须追问提名人"
    assert any(
        "nomination" in str(q.get("field", "")) for q in questions
    ), f"须定位提名字段，实际：{questions!r}"

    # —— (e) none（显式不提名）＋ joint 请求 → 要求指定（不默认 self/高收入）——
    body_none = _couple_body()
    body_none["input"]["years"]["2025_26"]["assessment"][
        "hypothetical_salary_joint_nomination"
    ] = {"status": "none", "person_id": None}
    r_none = v2.post(_PREPARE, json_body=body_none, headers=_fresh_if_match(v2))
    assert r_none.status_code == 422, (
        f"none＋joint 请求 → 要求指定提名人（S002 §5.3：none → 要求指定），"
        f"实际 {r_none.status_code}"
    )
    assert "prepared_id" not in r_none.json()
    questions_none = r_none.json().get("questions")
    assert isinstance(questions_none, list) and questions_none, (
        "须以 questions 要求指定，不得默认 self/收入较高者"
    )


# ===========================================================================
# 新 Red：R7 状态分离（复核报告 §五.7：已知 selected 保留／全 ineligible＝blocked）
# ===========================================================================


def test_002_known_selected_retained_with_unknown_competitor(v2) -> None:
    """已知 salary_separate＋joint 竞争者 unknown → 候选问题与结果状态分离：
    prepare 201、执行后 selected 保留 final（非 null）、best=null、partial
    （复核报告 R7：任一候选追问不得清空已知 selected；005 §10/§14）。"""
    body = _couple_body()
    assessment = body["input"]["years"]["2025_26"]["assessment"]
    assessment["candidates"] = ["salary_separate", "salary_joint"]
    assessment["selection"] = "salary_separate"
    assessment["hypothetical_salary_joint_nomination"] = {
        "status": "unknown",
        "person_id": None,
    }
    prepared = _prepare_ok(v2, body)
    payload = _confirm_and_execute(v2, prepared)

    assert payload.get("status") == "partial", (
        f"best 未定＝partial（R7），实际 {payload.get('status')!r}"
    )
    amounts = payload.get("amounts") or {}
    assert isinstance(amounts.get("final_after_reduction"), str) and (
        amounts["final_after_reduction"]
    ), f"已知 selected 须保留 final（不得清空/置 null），实际 amounts：{amounts!r}"

    # 权威 trace：selection 步骤 best=null、selected=salary_separate（005 §14）
    r_record = v2.get(f"/api/v1/records/{payload['record_id']}")
    assert r_record.status_code == 200, r_record.text
    steps = r_record.json()["record"].get("steps") or []
    selection_steps = [s for s in steps if s.get("kind") == "selection"]
    assert selection_steps, f"须含 selection 步骤（005 §14），实际 steps：{steps!r}"
    detail = selection_steps[0].get("detail") or {}
    assert detail.get("best") is None, (
        f"unknown 竞争者 → best=null（005 §10/§14），实际 {detail.get('best')!r}"
    )
    assert detail.get("selected") == "salary_separate"


def test_002_all_candidates_ineligible_blocked_not_needs_input(v2) -> None:
    """显式未婚＋唯一候选 salary_joint → joint 确定不合资格 → 全 ineligible＝
    blocked（422＋blocked_reason、无 prepared_id），不得误追问提名
    （复核报告 R7/B5：含 joint 即强制提名为缺陷；005 §9.1 真值表）。"""
    body: dict = {
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
                    # 唯一候选＝joint，而婚姻已明确未婚 → 确定不合资格
                    "assessment": {
                        "basis": "actual",
                        "candidates": ["salary_joint"],
                        "selection": "salary_joint",
                    },
                    "manual_issues": [],
                    "provisional_paid": "0",
                }
            },
            "hk_presence_days": [],
            "assumptions": [],
        },
    }
    r = v2.post("/api/v1/calc/prepare", json_body=body, headers=_fresh_if_match(v2))
    assert r.status_code == 422, (
        f"全 ineligible＝blocked 422（R7），实际 {r.status_code}：{r.text[:300]!r}"
    )
    payload = r.json()
    assert "prepared_id" not in payload
    err = _error_of(payload)
    assert err.get("code") != "E_ELIGIBILITY_UNKNOWN", (
        "确定不合资格不得按 unknown 追问（更不得仅因含 joint 就追问提名；"
        f"复核报告 B5/R7），实际 code={err.get('code')!r}"
    )
    blocked_reason = payload.get("blocked_reason") or err.get("message_zh") or ""
    assert blocked_reason, (
        f"blocked 422 须携带 blocked_reason/说明（S002 §4.2），实际：{payload!r}"
    )
    questions = payload.get("questions") or []
    assert not any(
        "nomination" in str(q.get("field", "")) for q in questions
    ), f"未婚＋joint 不合资格场景不得追问提名人，实际 questions：{questions!r}"


# ---------------------------------------------------------------------------
# P1 final fix（2026-10-17）：F5 HTTP 反例＋F6 完整 trace 候选连续分块
# ---------------------------------------------------------------------------


def _f5_http_missing_spouse_kinship_body() -> dict:
    """claimant p1 显式 none＋配偶 p2 parent 路径已成立，仅删除配偶关系
    `kinship` 键：HTTP prepare 须 needs_input，不再 201→execute 200/55,500。"""
    year = "2025_26"
    w_from, w_to = "2025-04-01", "2026-04-01"
    body = _couple_body(
        subject_facts={
            "d1": {
                "ordinarily_resident_hk": [
                    {"from": w_from, "to": w_to, "value": True}
                ],
                "govt_disability_allowance": [
                    {"from": w_from, "to": w_to, "value": True}
                ],
            }
        },
        extra_subject={"subject_id": "d1", "birth_date": "1950-05-01"},
        relations=[
            {
                "relation_id": "r1",
                "actor_id": "p2",
                "subject_id": "d1",
                "cash_maintenance": "12000",
            },
            {
                "relation_id": "r-none-p1",
                "actor_id": "p1",
                "subject_id": "d1",
                "kinship": {
                    "kind": "none",
                    "basis": "other",
                    "during": [{"from": w_from, "to": w_to, "value": True}],
                },
            },
        ],
        care_payments=[
            {
                "payment_id": "e1",
                "payer_id": "p1",
                "subject_id": "d1",
                "paid_on": "2025-06-15",
                "amount": "80000",
                "care_from": w_from,
                "care_to": w_to,
                "residential_care_received": True,
                "paid_to_home_or_agent": True,
                "home_basis": "cap459_licence",
                "home_status_in_force": [
                    {"from": w_from, "to": w_to, "value": True}
                ],
            }
        ],
        claims=[
            {
                "claim_id": "c1",
                "kind": "disabled_dependant",
                "claimant_id": "p1",
                "subject_id": "d1",
            }
        ],
        assessment={
            "basis": "actual",
            "candidates": ["salary_separate"],
            "selection": "salary_separate",
            "actual": {"salary_joint_elected": False},
        },
    )
    financials = body["input"]["years"][year]["financials"]
    financials["p1"]["employment_income_nai"] = "600000"
    financials["p2"].update(
        {
            "employment_income_nai": "0",
            "mpf_mandatory_contributions": "0",
            "has_assessable_salary_income": True,
        }
    )
    return body


def test_002_f5_missing_spouse_kinship_http_prepare_needs_input(v2) -> None:
    """P1 final fix (c)：同一缺失配偶 kinship 反例经 HTTP prepare 返回
    needs_input 422（无 prepared_id/无可执行能力），不得 prepare 201→execute
    200 保存薪俸 55,500。"""
    r = v2.post(
        _PREPARE,
        json_body=_f5_http_missing_spouse_kinship_body(),
        headers=_fresh_if_match(v2),
    )
    assert r.status_code == 422, (
        f"缺失配偶 kinship 须 needs_input 422，实际 {r.status_code}："
        f"{r.text[:300]!r}"
    )
    payload = r.json()
    assert "prepared_id" not in payload, "needs_input 不得签发能力/伪确定税额"
    err = _error_of(payload)
    assert err.get("code") == "E_ELIGIBILITY_UNKNOWN", err
    assert payload.get("status") == "needs_input" or err.get("status") == "needs_input", (
        f"须显式 status=needs_input，实际：{payload!r}"
    )
    questions = [
        question
        for question in (payload.get("questions") or [])
        if isinstance(question, dict)
    ]
    assert questions, f"须含 questions[] 定位未决事实，实际：{payload!r}"
    assert any(
        "c1" in str(question.get("field", ""))
        or "kinship" in str(question.get("field", ""))
        or question.get("claim_id") == "c1"
        for question in questions
    ), f"追问须定位该申索/亲属关系事实，实际：{questions!r}"


def _f6_http_t3_body(candidates: list[str]) -> dict:
    """HTTP T3：500k/200k、MPF 18k、p2 DAA、p1 申索 spouse DDA。"""
    w_from, w_to = "2025-04-01", "2026-04-01"
    return _couple_body(
        subject_facts={
            "p2": {
                "govt_disability_allowance": [
                    {"from": w_from, "to": w_to, "value": True}
                ]
            }
        },
        claims=[
            {
                "claim_id": "c1",
                "kind": "disabled_dependant",
                "claimant_id": "p1",
                "subject_id": "p2",
            }
        ],
        assessment={
            "basis": "hypothetical",
            "hypothetical_salary_joint_nomination": {
                "status": "named",
                "person_id": "p1",
            },
            "candidates": list(candidates),
            "selection": "best",
        },
    )


def _f6_http_block_signature(payload: dict) -> list[tuple]:
    rows: list[tuple] = []
    for step in payload.get("steps") or []:
        kind = step.get("kind")
        detail = step.get("detail") or {}
        rows.append(
            (
                kind,
                detail.get("scenario_id"),
                detail.get("unit_id") or detail.get("item_kind"),
            )
        )
    return rows


def _f6_http_projection(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for step in payload.get("steps") or []:
        detail = dict(step.get("detail") or {})
        detail.pop("requested", None)
        rows.append(
            {
                "step": step.get("step"),
                "kind": step.get("kind"),
                "label": step.get("label"),
                "detail": detail,
            }
        )
    return rows


def test_002_f6_http_trace_candidate_blocks_contiguous(v2) -> None:
    """P1 final fix (F6)：HTTP execute 返回的完整 T3 trace 须按固定候选连续
    成块（separate decision/assessment/scenario 全部→joint 全部），反转请求
    投影一致；金额 34,250/best=joint 不变。

    规格锚点：Approved 005:738；005 §9.1.3 T3。
    """
    expected = [
        ("context", None, None),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "disabled_dependant"),
        ("assessment", "salary_separate", "salary:p1"),
        ("assessment", "salary_separate", "salary:p2"),
        ("scenario", "salary_separate", None),
        ("decision", "salary_joint", "married"),
        ("decision", "salary_joint", "disabled_dependant"),
        ("assessment", "salary_joint", "salary:joint"),
        ("scenario", "salary_joint", None),
        ("selection", None, None),
        ("next_year", None, None),
    ]
    payloads = {}
    for label, candidates in (
        ("原始请求", ["salary_separate", "salary_joint"]),
        ("反转请求", ["salary_joint", "salary_separate"]),
    ):
        prepared = _prepare_ok(v2, _f6_http_t3_body(candidates))
        payload = _confirm_and_execute(v2, prepared)
        payloads[label] = payload
        assert payload["status"] == "partial", (label, payload["status"])
        assert payload["amounts"]["final_after_reduction"] == "34250", (
            label,
            payload["amounts"],
        )
        assert _f6_http_block_signature(payload) == expected, (
            f"{label}：HTTP 完整 trace 须按候选连续成块，实际："
            f"{_f6_http_block_signature(payload)!r}"
        )
        units = [
            (step.get("detail") or {}).get("unit_id")
            for step in payload.get("steps") or []
            if step.get("kind") == "assessment"
        ]
        assert units == ["salary:p1", "salary:p2", "salary:joint"], (label, units)
    assert _f6_http_projection(payloads["原始请求"]) == _f6_http_projection(
        payloads["反转请求"]
    ), "去除 selection.requested 后，HTTP 原始/反转请求完整 trace 投影须逐字段相等"


# ---------------------------------------------------------------------------
# P1 F6 点修（2026-10-17）：HTTP 单位序与 participants 数组序无关（005:738）
# ---------------------------------------------------------------------------


def _f6_http_reverse_participants(body: dict) -> dict:
    """同一 HTTP T3 载荷仅反转 participants 数组（其余字段不变）。"""
    year = body["input"]["years"]["2025_26"]
    year["participants"] = list(reversed(year["participants"]))
    return body


def test_002_f6_http_participants_reversed_units_and_trace_fixed(v2) -> None:
    """P1 F6 点修（HTTP）：仅反转 participants 数组，prepare 201 → confirm 200
    → execute 200；trace 单位序仍为 salary:p1→salary:p2→salary:joint，
    scenario.unit_ids 同序，金额 34,250，完整投影（去 requested）一致。

    规格锚点：Approved 005:738；005 §9.1.3 T3。
    """
    expected = [
        ("context", None, None),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "basic"),
        ("decision", "salary_separate", "disabled_dependant"),
        ("assessment", "salary_separate", "salary:p1"),
        ("assessment", "salary_separate", "salary:p2"),
        ("scenario", "salary_separate", None),
        ("decision", "salary_joint", "married"),
        ("decision", "salary_joint", "disabled_dependant"),
        ("assessment", "salary_joint", "salary:joint"),
        ("scenario", "salary_joint", None),
        ("selection", None, None),
        ("next_year", None, None),
    ]
    original = _confirm_and_execute(
        v2,
        _prepare_ok(v2, _f6_http_t3_body(["salary_separate", "salary_joint"])),
    )
    reversed_participants = _confirm_and_execute(
        v2,
        _prepare_ok(
            v2,
            _f6_http_reverse_participants(
                _f6_http_t3_body(["salary_separate", "salary_joint"])
            ),
        ),
    )
    for label, payload in (
        ("原始 participants", original),
        ("反转 participants", reversed_participants),
    ):
        assert payload["status"] == "partial", (label, payload["status"])
        assert payload["amounts"]["final_after_reduction"] == "34250", (
            label,
            payload["amounts"],
        )
        assert _f6_http_block_signature(payload) == expected, (
            f"{label}：HTTP 完整 trace 须按候选连续成块，实际："
            f"{_f6_http_block_signature(payload)!r}"
        )
        units = [
            (step.get("detail") or {}).get("unit_id")
            for step in payload.get("steps") or []
            if step.get("kind") == "assessment"
        ]
        assert units == ["salary:p1", "salary:p2", "salary:joint"], (label, units)
        scenarios = {
            (step.get("detail") or {}).get("scenario_id"): (
                step.get("detail") or {}
            ).get("unit_ids")
            for step in payload.get("steps") or []
            if step.get("kind") == "scenario"
        }
        assert scenarios["salary_separate"] == ["salary:p1", "salary:p2"], (
            label,
            scenarios,
        )
        assert scenarios["salary_joint"] == ["salary:joint"], (label, scenarios)
        assert [
            (
                (step.get("detail") or {}).get("scenario_id"),
                (step.get("detail") or {}).get("item_kind"),
                (step.get("detail") or {}).get("claimant_id"),
                (step.get("detail") or {}).get("amount"),
            )
            for step in payload.get("steps") or []
            if step.get("kind") == "decision"
        ] == [
            ("salary_separate", "basic", "p1", "132000"),
            ("salary_separate", "basic", "p2", "132000"),
            ("salary_separate", "disabled_dependant", "p1", None),
            ("salary_joint", "married", "p1", "264000"),
            ("salary_joint", "disabled_dependant", "p1", "75000"),
        ], (label, payload.get("steps"))
    assert _f6_http_projection(original) == _f6_http_projection(
        reversed_participants
    ), (
        "仅反转 participants 数组时，HTTP 除 selection.requested 外完整 trace "
        "投影（含单位序、金额、bases/operations）须逐字段相等"
    )


# ---------------------------------------------------------------------------
# P1 F6 单位 ID 序点修（2026-10-17，HTTP）：self pID 改名 p9 仍按单位 ID 序
# ---------------------------------------------------------------------------


def _f6_http_rename_person(value: Any, old: str = "p1", new: str = "p9") -> Any:
    """深拷贝式改名：仅把等于 old 的字符串值与对象键替换为 new。"""
    if isinstance(value, dict):
        return {
            (new if key == old else key): _f6_http_rename_person(item, old, new)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_f6_http_rename_person(item, old, new) for item in value]
    if value == old:
        return new
    return value


def _f6_http_t3_body_self_renamed_p9() -> dict:
    """同一 HTTP T3 载荷仅把 self 的 pID 由 p1 改名 p9。"""
    return _f6_http_rename_person(
        _f6_http_t3_body(["salary_separate", "salary_joint"])
    )


def _f6_http_unit_structure(payload: dict) -> dict[str, dict]:
    """单位结构投影（bases/operations/amounts/shares；p9 归一到 p1）。"""
    out: dict[str, dict] = {}
    for step in payload.get("steps") or []:
        detail = step.get("detail") or {}
        if step.get("kind") != "assessment":
            continue
        person = str(detail.get("unit_id", "")).split(":", 1)[-1]
        key = "p1" if person == "p9" else person
        out[key] = {
            "branch": detail.get("branch"),
            "bases": detail.get("bases"),
            "operations": detail.get("operations"),
            "amounts": detail.get("amounts"),
            "shares": detail.get("shares"),
        }
    return out


def test_002_f6_http_unit_order_by_unit_id_with_self_p9(v2) -> None:
    """F6 单位 ID 序点修（HTTP）：仅把 self 由 p1 改名 p9，prepare 201 →
    confirm 200 → execute 200；trace 单位序与 scenario.unit_ids 须按单位
    ID 升序 salary:p2→salary:p9（而非角色序 p9→p2），金额 34,250，单位
    结构（bases/operations/amounts）与正序逐字段一致。

    规格锚点：Approved 005:738；005:100/116/134–135 允许任意合法 pID。
    """
    original = _confirm_and_execute(
        v2, _prepare_ok(v2, _f6_http_t3_body(["salary_separate", "salary_joint"]))
    )
    renamed = _confirm_and_execute(
        v2, _prepare_ok(v2, _f6_http_t3_body_self_renamed_p9())
    )
    for label, payload in (
        ("正序 self=p1", original),
        ("改名 self=p9", renamed),
    ):
        assert payload["status"] == "partial", (label, payload["status"])
        assert payload["amounts"]["final_after_reduction"] == "34250", (
            label,
            payload["amounts"],
        )
    units = [
        (step.get("detail") or {}).get("unit_id")
        for step in renamed.get("steps") or []
        if step.get("kind") == "assessment"
    ]
    assert units == ["salary:p2", "salary:p9", "salary:joint"], (
        "改名 self=p9：HTTP 单位须按单位 ID 升序 p2→p9→joint，"
        f"实际：{units!r}"
    )
    scenarios = {
        (step.get("detail") or {}).get("scenario_id"): (
            step.get("detail") or {}
        ).get("unit_ids")
        for step in renamed.get("steps") or []
        if step.get("kind") == "scenario"
    }
    assert scenarios["salary_separate"] == ["salary:p2", "salary:p9"], scenarios
    assert scenarios["salary_joint"] == ["salary:joint"], scenarios
    assert [
        (
            (step.get("detail") or {}).get("scenario_id"),
            (step.get("detail") or {}).get("item_kind"),
            (step.get("detail") or {}).get("claimant_id"),
            (step.get("detail") or {}).get("amount"),
        )
        for step in renamed.get("steps") or []
        if step.get("kind") == "decision"
    ] == [
        ("salary_separate", "basic", "p2", "132000"),
        ("salary_separate", "basic", "p9", "132000"),
        ("salary_separate", "disabled_dependant", "p9", None),
        ("salary_joint", "married", "p9", "264000"),
        ("salary_joint", "disabled_dependant", "p9", "75000"),
    ], renamed.get("steps")
    assert _f6_http_unit_structure(renamed) == _f6_http_unit_structure(original), (
        "改名后 HTTP 单位结构投影（bases/operations/amounts/shares，按人归一）"
        "须与正序一致"
    )
    assert [
        step.get("step") for step in renamed.get("steps") or []
    ] == list(range(1, len(renamed.get("steps") or []) + 1)), (
        "HTTP step 编号须自 1 连续"
    )
    assert [
        (step.get("kind"), (step.get("detail") or {}).get("amounts"))
        for step in renamed.get("steps") or []
        if step.get("kind") == "scenario"
    ] == [
        (step.get("kind"), (step.get("detail") or {}).get("amounts"))
        for step in original.get("steps") or []
        if step.get("kind") == "scenario"
    ], "改名不得改变 HTTP scenario 六金额键"
# ---------------------------------------------------------------------------
# P1 S7 主体定位点修（2026-10-17，ora-50）：HTTP 422 questions subject 按
# role=self 定位（005:462/551），不按排序后 participants[0]
# ---------------------------------------------------------------------------


def _s7_http_subject_body(self_renamed: bool) -> dict:
    """S7 顺序配偶 HTTP 载荷；self_renamed=True 时仅把 self p1 改名 p9。"""
    body = _couple_body(
        household={
            "marriage": [
                {"from": "2025-04-01", "to": "2025-08-01",
                 "value": "married", "spouse_id": "p3"},
                {"from": "2025-08-01", "to": "2025-09-01", "value": "unmarried"},
                {"from": "2025-09-01", "to": "2026-04-01",
                 "value": "married", "spouse_id": "p2"},
            ],
            "living_apart": [
                {"from": "2025-04-01", "to": "2025-08-01", "value": False},
                {"from": "2025-09-01", "to": "2026-04-01", "value": False},
            ],
            "child_nomination": {"person_id": None, "status": "none"},
        },
        assessment={
            "basis": "hypothetical",
            "actual": {
                "salary_joint_elected": False,
                "salary_joint_assessed": False,
                "pa": {"p1": {"mode": "none"}, "p2": {"mode": "none"}},
                "history": "ordinary",
            },
            "hypothetical_salary_joint_nomination": {
                "status": "named", "person_id": "p1",
            },
            "candidates": ["salary_joint"],
            "selection": "salary_joint",
        },
    )
    body["input"]["subjects"].append(
        {"subject_id": "p3", "birth_date": "1993-03-03"}
    )
    if self_renamed:
        body = _f6_http_rename_person(body)
    return body


def test_002_s7_http_marriage_question_subject_is_self_after_self_renamed_p9(v2) -> None:
    """S7 主体定位（HTTP Red）：同一顺序配偶载荷仅把 self p1 改名 p9 后，
    prepare 仍须 422＋needs_input＋无 prepared_id，且 marriage 问题的
    subject_id 指向 self=p9（不是排序首项 spouse=p2）；正序 p1 控制主体不变。
    """
    for label, renamed, expected_subject in (
        ("正序 self=p1", False, "p1"),
        ("改名 self=p9", True, "p9"),
    ):
        r = v2.post(
            _PREPARE,
            json_body=_s7_http_subject_body(renamed),
            headers=_fresh_if_match(v2),
        )
        assert r.status_code == 422, (
            f"{label}：S7 顺序配偶须 needs_input 422，实际 "
            f"{r.status_code}：{r.text[:300]!r}"
        )
        payload = r.json()
        assert "prepared_id" not in payload, (label, payload)
        assert payload.get("status") == "needs_input" or _error_of(payload).get(
            "status"
        ) == "needs_input", (label, payload)
        questions = [
            question
            for question in (payload.get("questions") or [])
            if isinstance(question, dict)
        ]
        assert any(
            question.get("field") == "years.2025_26.household.marriage"
            and question.get("subject_id") == expected_subject
            for question in questions
        ), f"{label}：marriage 问题须定位 self={expected_subject}，实际：{questions!r}"
        assert not any(
            question.get("field") == "years.2025_26.household.marriage"
            and question.get("subject_id") == "p2"
            for question in questions
        ), f"{label}：marriage 问题主体不得落在 spouse p2，实际：{questions!r}"
