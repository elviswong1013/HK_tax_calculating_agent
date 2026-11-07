# -*- coding: utf-8 -*-
"""REQ-12/REQ-2 输入 schema 版本分派（002 P1 Red；S002 §4.1 版本表）。

REQ: REQ-12（S002 §8/§9.4；PRD AC-12）＋REQ-2 节点
test_002_unknown_422_questions_no_prepared_id。G0 修正后共 9 个命名节点：
  - test_002_v2_accepted_v1_rejected_reconfirm（REQ-12）
  - test_002_five_other_inputs_keep_v1_but_prepare_if_match_shared（REQ-12/11；
    G0：v1 property 恢复 {value:…} 包装＋raw Tri 拒绝负例，复核报告 §四.4/B4）
  - test_002_unknown_422_questions_no_prepared_id（REQ-2；G0：扩展 422 全形状
    status＋error.binding 四元组，复核报告 §五.7/R7）
  - test_002_v2_layered_extra_null_type_and_id_rejected（§五.6/R3）
  - test_002_v2_tf_order_window_and_presence_strict（§五.6/R3③⑧）
  - test_002_v2_all_explicit_year_candidates_validated_upfront（§五.6/B6）
  - test_002_v2_assumption_conflict_rejected（§五.6/005 §11）
  - test_002_duplicate_json_keys_rejected（§五.6/R3⑦）
  - test_002_money_ratio_equivalent_inputs_same_hash（§五.6/R3④）

规格锚点:
  - S002 §2 ADR-3（版本分派：engine 0.2.0 全局；薪俸/PA input 2.0.0；
    其余五税 1.0.0 不变；bundle rules_schema_version 2.0.0；/api/v1 URL 不变；
    七税 prepare 共同升级 If-Match 协议）＋§4.1 版本表＋§4.2 prepare 语义
    （薪俸/PA 仅 2.0.0；旧 1.0.0 → 422 E_INPUT_TYPE field=schema_version，
    附「旧确认已失效，请重新 prepare＋confirm」指引，不迁移；无 If-Match →
    409 E_CONFIRMATION_STALE 不推进）。
  - Annex 005 §12.1（prepare reservation 固定顺序：认证/CSRF → If-Match CAS
    → 原子 rev++/失效 → 大小 → 读取 → 校验）＋§12.2（运行时 ACK 头；
    409 携带 X-Edit-Current-* 只读冲突信息）。
  - Annex 005 §5.2/§5.3（有效完整 PA/薪俸 v2 示例＝封闭契约示范）。
  - S002 §4.3/Annex 005 §13（422 形状 questions[] 含
    {field,year,claim_id?,subject_id?,question_zh}；needs_input/blocked 均
    无 prepared_id；未知资格保持 E_ELIGIBILITY_UNKNOWN＋questions）。

期望值来源（禁从生产代码反推）:
  - 版本号/协议/错误码：S002 §4.1–§4.3、Annex 005 §12–§13（冻结表）。
  - v2 载荷：Annex 005 §5.2/§5.3 官方示例逐字转写。
  - 其余五税 v1 最小事实：现行 001 契约形状（tests/acceptance/_acc_helpers
    同源事实；仅作输入载荷，不作税额期望）。
  - 全部为结构/状态断言，无金额期望。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - 固定 v2 测试 seed 经 RuleStore.publish 注入临时库（P1 依赖契约，
    非生产 bootstrap；Annex 005 §15.1/§15.2 全表），create_app 在该库上
    构造后新计算可用。
  - GET /api/v1/meta/version：engine_version "0.2.0"、rules_schema_version
    "2.0.0"、附运行时 session_epoch/input_revision（§4.1 版本表）。
  - prepare：薪俸/PA 信封 schema_version=="2.0.0"＋If-Match
    "edit-<epoch>-<revision>" → 201；旧 "1.0.0" → 422 E_INPUT_TYPE
    field=schema_version＋message_zh 含重新 prepare/confirm 指引，不迁移。
  - 其余五税（profits/property/stamp×3）：schema 1.0.0＋If-Match → 201；
    2.0.0 → 422 E_INPUT_TYPE field=schema_version；缺 If-Match/陈旧 →
    409 E_CONFIRMATION_STALE（七税共享协议）。
  - v2 婚姻 unknown（MarriageTF value "unknown"）→ 422 E_ELIGIBILITY_UNKNOWN
    ＋结构化 questions[]（field/year/question_zh）且无 prepared_id。
  - 本文件对 app.* 延迟导入；Red 失败原因＝缺失 v2 契约（meta 缺运行时
    epoch/revision、旧校验拒绝 2.0.0、缺 If-Match CAS），非收集期失败。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

_BASE_ORIGIN = "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# 固定 v2 测试 seed（与 tests/unit/test_002_store_compat.py 同源转写；
# Annex 005 §15.1/§15.2，P1 注入件）
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


def _semantic_digest() -> str:
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


# 注：本文件 _fixed_v2_seed 已补全发行五税固定 profiles（admission 内容锚）；
# 定稿 seed canonical hash 见 tests/unit/test_002_store_compat.py::_FIXED_V2_SEED_HASH。


def _fixed_v2_seed() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    profile = _part5_profile()
    profile["semantic_digest"] = _semantic_digest()
    legacy_profiles = [
        copy.deepcopy(item)
        for item in INITIAL_BUNDLE_CONTENT["frozen_semantics"]["profiles"]
        if item.get("profile_id") != "part5_shared_v2"
    ]
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
                # §15.2：ERCE cap 仍留 salaries.deductions（PA 读取同一现有源；
                # 附表 3C，T1）
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
                # 附表 2/1（T1，台账 1.17）：PA 累进带＋标准径（s.43(1A) 上限基数）
                "progressive_bands": _progressive_bands(),
                "standard_rate_bands": _standard_rate_bands(),
                # 附表 43 利得行（T1；s.100(2)/(3)，每业务一个）
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
                # 附表 1「其他税项」（T1）＋s.5(1A)(b)(ii)（T1）
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
# v2 信封（Annex 005 §5.2/§5.3 官方示例逐字转写；§5.3 为最小可省形式）
# ---------------------------------------------------------------------------


def _v2_salary_body(marriage_unknown: bool = False) -> dict:
    marriage_value = "unknown" if marriage_unknown else "unmarried"
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
                                "value": marriage_value,
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


def _v2_pa_body() -> dict:
    """Annex 005 §5.2：单人普通薪俸＋物业，hypothetical 两候选（逐字转写）。"""
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
                                {"from": "2026-06-01", "to": "2026-06-02", "value": True}
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


def _v1_salary_body() -> dict:
    """旧 1.0.0 薪俸信封（现行 001 形状；仅作被拒载荷）。"""
    return {
        "tax_type": "salaries_tax",
        "schema_version": "1.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "employment_income": "240000",
            "mpf_mandatory_contributions": "9000",
            "married_status": {"value": "single"},
        },
    }


def _v1_pa_body() -> dict:
    """旧 1.0.0 PA 信封（现行 001 形状；仅作被拒载荷）。"""
    return {
        "tax_type": "personal_assessment",
        "schema_version": "1.0.0",
        "input": {
            "year_of_assessment": "2025_26",
            "married_status": {"value": "single"},
            "persons": [
                {
                    "person_id": "self",
                    "employment_income": "250000",
                    "mpf_mandatory_contributions": "0",
                    "properties": [],
                    "businesses": [],
                }
            ],
        },
    }


_FIVE_TAX_V1_INPUTS: dict[str, dict] = {
    "profits_tax": {
        "year_of_assessment": "2025_26",
        "entity_kind": "sole_proprietorship",
        "assessable_profit": "600000",
        "two_tier": {
            "connected_entities": [],
            "election_made": {"value": True},
            "no_other_election_same_year": {"value": True},
        },
    },
    "property_tax": {
        "year_of_assessment": "2025_26",
        "properties": [
            {
                "property_id": "p-1",
                "rent_received": "120000",
                # 001 v1 契约：资格事实走 {value:…} 包装（C3.5 三态）；
                # raw Tri 属 v2 形状，v1 须拒绝（复核报告 B4）
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
        "property_class": "residential",
        "consideration": "6000000",
        "value": "6000000",
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


# ---------------------------------------------------------------------------
# 回环客户端与 v2 种子 fixture（本文件自备）
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

    def post(
        self,
        path: str,
        *,
        json_body: dict | None = None,
        content: bytes | None = None,
        headers: dict | None = None,
        csrf: bool = True,
    ):
        h = dict(headers or {})
        h["Origin"] = _BASE_ORIGIN
        if csrf:
            h["X-CSRF-Token"] = self._token()
        return self._client.post(path, json=json_body, content=content, headers=h)

    def close(self) -> None:
        self._client.close()


def _runtime_versions(client: _Loopback) -> tuple[int, int]:
    r = client.get("/api/v1/meta/version")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "session_epoch" in payload and "input_revision" in payload, (
        "S002 §4.1/Annex 005 §12：meta/version 须附运行时 session_epoch/"
        f"input_revision，实际：{payload!r}"
    )
    return int(payload["session_epoch"]), int(payload["input_revision"])


def _fresh_if_match(client: _Loopback) -> dict[str, str]:
    epoch, revision = _runtime_versions(client)
    return _if_match(epoch, revision)


def _if_match(epoch: int, revision: int) -> dict[str, str]:
    """S002 §4.1 版本表 wire 字面量：`If-Match:"edit-<epoch>-<revision>"`（带引号；
    RFC 9110 If-Match entity-tag 语法亦要求 DQUOTE）。单一语法，不做双兼容。"""
    return {"If-Match": f'"edit-{epoch}-{revision}"'}


def _stale_if_match(client: _Loopback, bump: int = 9) -> dict[str, str]:
    epoch, revision = _runtime_versions(client)
    return {"If-Match": f'"edit-{epoch}-{revision + bump}"'}


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


def _error_of(payload: dict) -> dict:
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状 §4.3，实际：{payload!r}"
    return err


# ---------------------------------------------------------------------------
# 命名节点
# ---------------------------------------------------------------------------


def test_002_v2_accepted_v1_rejected_reconfirm(v2) -> None:
    """薪俸/PA 仅接受 2.0.0；旧 1.0.0 → 422 E_INPUT_TYPE（field=
    schema_version、附重新确认指引、不迁移）；引擎/束版本升至 0.2.0/2.0.0。"""
    # —— 版本表（S002 §4.1）：engine 0.2.0 / bundle 2.0.0 / 运行时 epoch ——
    r_meta = v2.get("/api/v1/meta/version")
    assert r_meta.status_code == 200, r_meta.text
    meta = r_meta.json()
    assert meta["engine_version"] == "0.2.0", (
        f"engine_version 须为 0.2.0（S002 §4.1 版本表），实际 {meta.get('engine_version')!r}"
    )
    assert meta["rules_schema_version"] == "2.0.0", (
        f"rules_schema_version 须为 2.0.0（S002 §4.1），实际 "
        f"{meta.get('rules_schema_version')!r}"
    )

    # —— 薪俸 v2 → 201（Annex 005 §5.3 最小可省形式＋If-Match）——
    r = v2.post(
        "/api/v1/calc/prepare",
        json_body=_v2_salary_body(),
        headers=_fresh_if_match(v2),
    )
    assert r.status_code == 201, (
        f"薪俸 2.0.0 信封须被接受（Annex 005 §5.3），实际 {r.status_code}："
        f"{r.text[:300]!r}"
    )
    binding = r.json()["expected_binding"]
    assert binding["rules_schema_version"] == "2.0.0"
    assert binding["engine_version"] == "0.2.0"

    # —— 薪俸旧 1.0.0 → 422 E_INPUT_TYPE＋重新确认指引，不迁移 ——
    r_old = v2.post(
        "/api/v1/calc/prepare",
        json_body=_v1_salary_body(),
        headers=_fresh_if_match(v2),
    )
    assert r_old.status_code == 422, (
        f"旧 1.0.0 薪俸信封须被拒绝（S002 §4.2），实际 {r_old.status_code}"
    )
    err = _error_of(r_old.json())
    assert err["code"] == "E_INPUT_TYPE", f"实际 code：{err.get('code')!r}"
    assert err["field"] == "schema_version", f"实际 field：{err.get('field')!r}"
    message = err.get("message_zh") or ""
    assert "重新" in message and "prepare" in message and "confirm" in message, (
        "须附「旧确认已失效，请重新 prepare＋confirm」类指引（S002 §4.2），"
        f"实际 message_zh：{message!r}"
    )
    assert "prepared_id" not in r_old.json(), "拒绝路径不得签发 prepared_id"

    # —— PA v2 → 201；PA 旧 1.0.0 → 422 E_INPUT_TYPE field=schema_version ——
    r_pa = v2.post(
        "/api/v1/calc/prepare",
        json_body=_v2_pa_body(),
        headers=_fresh_if_match(v2),
    )
    assert r_pa.status_code == 201, (
        f"PA 2.0.0 信封须被接受（Annex 005 §5.2），实际 {r_pa.status_code}："
        f"{r_pa.text[:300]!r}"
    )
    assert r_pa.json()["expected_binding"]["rules_schema_version"] == "2.0.0"

    r_pa_old = v2.post(
        "/api/v1/calc/prepare",
        json_body=_v1_pa_body(),
        headers=_fresh_if_match(v2),
    )
    assert r_pa_old.status_code == 422, r_pa_old.text
    err_pa = _error_of(r_pa_old.json())
    assert err_pa["code"] == "E_INPUT_TYPE"
    assert err_pa["field"] == "schema_version"
    assert "prepared_id" not in r_pa_old.json()


def test_002_five_other_inputs_keep_v1_but_prepare_if_match_shared(v2) -> None:
    """其余五税维持 1.0.0（2.0.0 拒绝）；七税共享 If-Match：缺失/陈旧 →
    409 E_CONFIRMATION_STALE 不推进（S002 ADR-3 协议 delta）。"""
    for tax_type, facts in _FIVE_TAX_V1_INPUTS.items():
        # —— 1.0.0＋新鲜 If-Match → 201（五税输入契约不变）——
        r = v2.post(
            "/api/v1/calc/prepare",
            json_body={"tax_type": tax_type, "schema_version": "1.0.0", "input": facts},
            headers=_fresh_if_match(v2),
        )
        assert r.status_code == 201, (
            f"{tax_type} 1.0.0＋If-Match 须 201（S002 §4.1 其余五税不变），"
            f"实际 {r.status_code}：{r.text[:300]!r}"
        )
        # —— 2.0.0 → 422 E_INPUT_TYPE field=schema_version（不得升版）——
        r_v2 = v2.post(
            "/api/v1/calc/prepare",
            json_body={"tax_type": tax_type, "schema_version": "2.0.0", "input": facts},
            headers=_fresh_if_match(v2),
        )
        assert r_v2.status_code == 422, (
            f"{tax_type} 不得接受 2.0.0（其余五税 schema 不变），"
            f"实际 {r_v2.status_code}"
        )
        err = _error_of(r_v2.json())
        assert err["code"] == "E_INPUT_TYPE"
        assert err["field"] == "schema_version"

    # —— v1 property raw Tri（无 {value:…} 包装）→ 422（001 v1 契约不变；
    #    复核报告 B4：raw Tri 仅属 v2 词法，v1 保持 _tri_state_field 包装）——
    import copy as _copy

    raw_property = _copy.deepcopy(_FIVE_TAX_V1_INPUTS["property_tax"])
    raw_property["properties"][0]["rates_paid_by_owner"]["agreed"] = False
    r_raw = v2.post(
        "/api/v1/calc/prepare",
        json_body={
            "tax_type": "property_tax",
            "schema_version": "1.0.0",
            "input": raw_property,
        },
        headers=_fresh_if_match(v2),
    )
    assert r_raw.status_code == 422, (
        f"v1 property raw 布尔（无 value 包装）须 422（001 契约；复核报告 B4），"
        f"实际 {r_raw.status_code}：{r_raw.text[:200]!r}"
    )
    err_raw = _error_of(r_raw.json())
    assert err_raw["code"] == "E_INPUT_TYPE", f"实际 code：{err_raw.get('code')!r}"
    assert "agreed" in str(err_raw.get("field", "")) + str(
        err_raw.get("details") or []
    ), f"错误须定位 agreed 字段，实际：{err_raw!r}"
    assert "prepared_id" not in r_raw.json()

    # —— 缺 If-Match → 409 E_CONFIRMATION_STALE（七税共享，Annex 005 §12.1）——
    r_missing = v2.post(
        "/api/v1/calc/prepare",
        json_body={
            "tax_type": "profits_tax",
            "schema_version": "1.0.0",
            "input": _FIVE_TAX_V1_INPUTS["profits_tax"],
        },
    )
    assert r_missing.status_code == 409, (
        f"缺 If-Match 须 409 不推进（Annex 005 §12.1），实际 "
        f"{r_missing.status_code}：{r_missing.text[:200]!r}"
    )
    assert _error_of(r_missing.json())["code"] == "E_CONFIRMATION_STALE"
    assert "prepared_id" not in r_missing.json()
    # 已认证 409 须携带 Current 只读冲突信息（Annex 005 §12.2）
    epoch, revision = _runtime_versions(v2)
    assert r_missing.headers.get("X-Edit-Current-Epoch") == str(epoch), (
        "409 须附 X-Edit-Current-Epoch 当前只读快照（Annex 005 §12.2）"
    )
    assert r_missing.headers.get("X-Edit-Current-Revision") == str(revision), (
        "409 须附 X-Edit-Current-Revision 当前只读快照（Annex 005 §12.2）"
    )

    # —— 陈旧 If-Match → 409（CAS；即使其余部分合法也不推进）——
    r_stale = v2.post(
        "/api/v1/calc/prepare",
        json_body={
            "tax_type": "profits_tax",
            "schema_version": "1.0.0",
            "input": _FIVE_TAX_V1_INPUTS["profits_tax"],
        },
        headers=_stale_if_match(v2),
    )
    assert r_stale.status_code == 409, (
        f"陈旧 If-Match 须 409（Annex 005 §12.1 CAS），实际 {r_stale.status_code}"
    )
    assert _error_of(r_stale.json())["code"] == "E_CONFIRMATION_STALE"


def test_002_unknown_422_questions_no_prepared_id(v2) -> None:
    """v2 婚姻 unknown → 422 E_ELIGIBILITY_UNKNOWN＋结构化 questions[]（field/
    year/question_zh），无 prepared_id（S002 §9.4 REQ-2 节点）。"""
    r = v2.post(
        "/api/v1/calc/prepare",
        json_body=_v2_salary_body(marriage_unknown=True),
        headers=_fresh_if_match(v2),
    )
    assert r.status_code == 422, (
        f"婚姻 unknown 须 needs_input 422（S002 §4.3），实际 {r.status_code}："
        f"{r.text[:300]!r}"
    )
    payload = r.json()
    err = _error_of(payload)
    assert err["code"] == "E_ELIGIBILITY_UNKNOWN", (
        f"未知资格保持 E_ELIGIBILITY_UNKNOWN（S002 §4.3/Annex 005 §13），"
        f"实际 {err.get('code')!r}"
    )
    assert "prepared_id" not in payload, "needs_input 不得签发 prepared_id"

    questions = payload.get("questions")
    assert isinstance(questions, list) and questions, (
        f"须含非空 questions[]（Annex 005 §13），实际：{payload!r}"
    )
    for question in questions:
        assert isinstance(question, dict), f"question 须为对象：{question!r}"
        assert isinstance(question.get("field"), str) and question["field"], (
            f"question 须含 field 定位（Annex 005 §13）：{question!r}"
        )
        assert question.get("year") == "2025_26", (
            f"question 须含 year 定位：{question!r}"
        )
        assert isinstance(question.get("question_zh"), str) and question["question_zh"], (
            f"question 须含中文 question_zh：{question!r}"
        )
    assert any(
        "marriage" in q["field"] for q in questions
    ), f"须定位婚姻事实字段（household.marriage），实际：{questions!r}"

    # —— R7/S002 §4.3：422 全形状——status＋error.binding（四元组）＋无 prepared_id
    # （复核报告 R7：routes 422 缺 status、binding 未设；冻结形状见 S002 §4.3
    #  `{"error":{code,message_zh,field,details,binding}, questions[], status}`）——
    assert payload.get("status") == "needs_input", (
        f"needs_input 422 须携带 status=\"needs_input\"（S002 §4.3 422 形状），"
        f"实际：{payload.get('status')!r}"
    )
    error_binding = err.get("binding")
    assert isinstance(error_binding, dict) and set(error_binding) == {
        "bundle_id", "bundle_hash", "rules_schema_version", "engine_version",
    }, f"422 error.binding 须为完整四元组（S002 §4.3/R7），实际：{error_binding!r}"


# ===========================================================================
# 结构与 canonical 新 Red（复核报告 §五.6：R3 封闭结构＋缺失≠零＋canonical 哈希）
# ===========================================================================


def _prepare(v2, body: dict):
    return v2.post("/api/v1/calc/prepare", json_body=body, headers=_fresh_if_match(v2))


_UNCOVERED_FIELD = object()


def _expect_rejected(
    v2, body: dict, code: str, label: str, expected_field: Any = _UNCOVERED_FIELD
) -> None:
    """PQA-06（CF1）：可选独立 literal `expected_field`（完整 input 根路径）；
    未指定＝该例 field 未覆盖（不猜首错/不以 HTTP 响应当 oracle）。"""
    r = _prepare(v2, body)
    assert r.status_code == 422, (
        f"{label}：须结构化 422（不得 201/500；R3 结构封闭），实际 "
        f"{r.status_code}：{r.text[:200]!r}"
    )
    err = _error_of(r.json())
    assert err.get("code") == code, f"{label}：实际 code {err.get('code')!r}"
    assert "prepared_id" not in r.json()
    if expected_field is not _UNCOVERED_FIELD:
        assert err.get("field") == expected_field, (
            f"{label}：error.field 须为完整 CF1 路径 {expected_field!r}，实际 "
            f"{err.get('field')!r}"
        )


def test_002_v2_layered_extra_null_type_and_id_rejected(v2) -> None:
    """逐层封闭（005 §3/§4 封闭字段表）：Year/financials 层 extra、null 标量、
    紧凑日期、错误 ID 前缀、悬空引用 → 结构化 422，不得放行或 500（R3①②）。"""
    import copy as _copy

    # —— Year 层 extra（现行已查；保留为回归锚点；PQA-06/CF1 精确路径）——
    body = _v2_salary_body()
    body["input"]["years"]["2025_26"]["surprise"] = 1
    _expect_rejected(
        v2, body, "E_INPUT_EXTRA", "Year 层未知字段",
        expected_field="years.2025_26.surprise",
    )

    # —— financials 层 extra（R3①：多层 extra 现被忽略；CF1 精确路径）——
    body = _v2_salary_body()
    body["input"]["years"]["2025_26"]["financials"]["p1"]["bonus"] = "1"
    _expect_rejected(
        v2, body, "E_INPUT_EXTRA", "financials 层未知字段",
        expected_field="years.2025_26.financials.p1.bonus",
    )

    # —— null 标量（year_of_assessment=None → E_INPUT_TYPE，不得 500）——
    body = _v2_salary_body()
    body["input"]["year_of_assessment"] = None
    _expect_rejected(v2, body, "E_INPUT_TYPE", "year_of_assessment=null")

    # —— 紧凑日期（005 §3 Date＝YYYY-MM-DD 全匹配，C2.11；R3 parse_date 未全匹配）——
    body = _v2_salary_body()
    body["input"]["subjects"] = [{"subject_id": "p1", "birth_date": "19900101"}]
    _expect_rejected(
        v2, body, "E_INPUT_TYPE", "birth_date 紧凑日期",
        expected_field="subjects[0].birth_date",
    )

    # —— 错误 ID 前缀（005 §3 ID：人=p、dependant=d；R3② ID 前缀未闭合）——
    # 该例同时改 subject_id 前缀与 participant person_id（多变量、无已冻结
    # 优先级）：只保留 code 断言；field 登记为未覆盖（不猜首错字段）。
    body = _v2_salary_body()
    body["input"]["subjects"] = [{"subject_id": "x1"}]
    body["input"]["years"]["2025_26"]["participants"] = [
        {"person_id": "x1", "role": "self"}
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "subject_id 错误前缀")

    # —— 悬空引用（005 §3：未注册 pID 引用 → E_INPUT_CONTRADICTORY；CF1 路径）——
    body = _v2_salary_body()
    body["input"]["years"]["2025_26"]["participants"] = [
        {"person_id": "p9", "role": "self"}
    ]
    _expect_rejected(
        v2, body, "E_INPUT_CONTRADICTORY", "participants 悬空 pID",
        expected_field="years.2025_26.participants[0].person_id",
    )

    # —— claimant 前缀错误（claimant_id 类型＝^p…；注册 d1 亦不得作 claimant）——
    body = _v2_salary_body()
    body["input"]["subjects"] = [
        {"subject_id": "p1"},
        {"subject_id": "d1", "birth_date": "2015-01-01"},
    ]
    body["input"]["years"]["2025_26"]["claims"] = [
        {"claim_id": "c1", "kind": "disabled_dependant", "claimant_id": "d1",
         "subject_id": "d1"}
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "claimant_id 非 p 前缀")


def test_002_v2_tf_order_window_and_presence_strict(v2) -> None:
    """TF 原始顺序/非重叠/合法窗口＋presence bool/范围（005 §3 TF、§4.7；
    R3③⑧：排序副本读原序、presence 缺 bool 排除/日数范围未闭合）。"""
    year = "2025_26"

    # —— 乱序 TF（按 wire 原始顺序核验；排序副本合法化＝R3 缺陷）——
    body = _v2_salary_body()
    body["input"]["years"][year]["household"]["marriage"] = [
        {"from": "2025-10-01", "to": "2026-04-01", "value": "unmarried"},
        {"from": "2025-04-01", "to": "2025-10-01", "value": "unmarried"},
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "TF 乱序")

    # —— 重叠 TF（005 §3：按序且互不重叠）——
    body = _v2_salary_body()
    body["input"]["years"][year]["household"]["marriage"] = [
        {"from": "2025-04-01", "to": "2026-01-01", "value": "unmarried"},
        {"from": "2025-12-01", "to": "2026-04-01", "value": "unmarried"},
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "TF 重叠")

    # —— 域越界 TF（默认窗口＝该 YA [4/1, 次年 4/1)；仅 §7 列明谓词可加参考日
    #     一天区间——此处为普通全年婚姻区间，越界须拒）——
    body = _v2_salary_body()
    body["input"]["years"][year]["household"]["marriage"] = [
        {"from": "2024-06-01", "to": "2025-06-01", "value": "unmarried"}
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "TF 越出 YA 窗口")

    # —— presence：布尔（bool 是 int 子类，须显式排除）——
    body = _v2_salary_body()
    body["input"]["hk_presence_days"] = [
        {"person_id": "p1", "year": year, "days": True}
    ]
    _expect_rejected(
        v2, body, "E_INPUT_TYPE", "presence days=布尔",
        expected_field="hk_presence_days[0].days",
    )

    # —— presence：负数（范围 0..该 YA 日数）——
    body = _v2_salary_body()
    body["input"]["hk_presence_days"] = [
        {"person_id": "p1", "year": year, "days": -5}
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "presence days=负数")

    # —— presence：字符串数字（须整数或 "unknown"）——
    body = _v2_salary_body()
    body["input"]["hk_presence_days"] = [
        {"person_id": "p1", "year": year, "days": "366"}
    ]
    _expect_rejected(v2, body, "E_INPUT_TYPE", "presence days=字符串")


def test_002_v2_all_explicit_year_candidates_validated_upfront(v2) -> None:
    """全部显式 Year 的候选/形状 upfront 校验（B6/005 §3：next guard 不得隐藏
    非法输入；R3⑥ 所有显式 Year 先验）。"""
    import copy as _copy

    body = _v2_salary_body()
    next_year = _copy.deepcopy(body["input"]["years"]["2025_26"])
    # P1 r2 定点纠错（engine 报告 §三）：next clone 婚姻日期改 2026_27 目标年
    # [2026-04-01, 2027-04-01)——原 2025-04-01..2026-04-01 越出目标年窗口，
    # 会先触发 TF 域错误而非候选错误（误译遮蔽）；非法候选与 selection 用
    # 同一非法名称，单变量只命中候选错误。不改共享 _v2_salary_body。
    next_year["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    next_year["assessment"] = {
        "basis": "actual",
        "candidates": ["salary_telepathy"],  # 非法候选藏在下一年
        "selection": "salary_telepathy",
    }
    body["input"]["years"]["2026_27"] = next_year
    _expect_rejected(v2, body, "E_INPUT_EXTRA", "下一年非法候选")


def test_002_v2_assumption_conflict_rejected(v2) -> None:
    """显式目标事实与 same_income 假设异额 → E_INPUT_CONTRADICTORY
    （005 §11：不采用「最后写入者优先」；R3⑤ source/assumption 未成消费者）。"""
    import copy as _copy

    body = _v2_salary_body()
    next_year = _copy.deepcopy(body["input"]["years"]["2025_26"])
    # P1 r2 定点纠错（engine 报告 §三）：同上——next 婚姻日期改目标年，避免
    # TF 域错误先触发而冒称假设冲突覆盖；保留 300000 与冲突断言。
    next_year["household"]["marriage"] = [
        {"from": "2026-04-01", "to": "2027-04-01", "value": "unmarried"}
    ]
    next_year["financials"]["p1"]["employment_income_nai"] = "300000"
    body["input"]["years"]["2026_27"] = next_year
    body["input"]["assumptions"] = [
        {
            "assumption_id": "a1",
            "kind": "same_income",
            "person_id": "p1",
            "source_year": "2025_26",
            "target_year": "2026_27",
        }
    ]
    _expect_rejected(v2, body, "E_INPUT_CONTRADICTORY", "假设与显式事实异额")


def test_002_duplicate_json_keys_rejected(v2) -> None:
    """重复 JSON 键 → 422（C2.7 canonical 拒绝重复键；R3⑦ routes 须复用
    canonical_loads——当前 json.loads 接受重复键，探针 201）。"""
    body = _v2_salary_body()
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    duplicated = raw.replace(
        b'"input": {"', b'"input": {"year_of_assessment": "2025_26", ', 1
    )
    assert duplicated != raw, "重复键构造失败（replace 未命中）"
    r = v2.post(
        "/api/v1/calc/prepare",
        content=duplicated,
        headers={**_fresh_if_match(v2), "Content-Type": "application/json"},
    )
    assert r.status_code == 422, (
        f"重复 JSON 键须 422（C2.7/R3⑦），实际 {r.status_code}：{r.text[:200]!r}"
    )
    assert _error_of(r.json())["code"] in ("E_INPUT_TYPE", "E_INPUT_EXTRA")
    assert "prepared_id" not in r.json()


def test_002_money_ratio_equivalent_inputs_same_hash(v2) -> None:
    """Money 尾零/Ratio 未约简等价输入 → 规范化后同一 canonical input hash
    （005 §3：`"1200000.00"` 接受并规范；`"2/6"` 约简为 `"1/3"`；R3④）。"""
    # —— Money：尾零等价 ——
    body_a = _v2_salary_body()
    body_a["input"]["years"]["2025_26"]["financials"]["p1"][
        "employment_income_nai"
    ] = "240000.00"
    r_a = _prepare(v2, body_a)
    assert r_a.status_code == 201, (
        f"尾零金额须接受并规范化（005 §3），实际 {r_a.status_code}："
        f"{r_a.text[:200]!r}"
    )
    r_b = _prepare(v2, _v2_salary_body())
    assert r_b.status_code == 201, r_b.text
    assert r_a.json()["input_hash"] == r_b.json()["input_hash"], (
        "等价金额（240000.00 vs 240000）规范化后须同 canonical input hash（R3④）"
    )

    # —— Ratio：未约简等价（PA 物业份额 2/6 ≡ 1/3）——
    pa_a = _v2_pa_body()
    pa_a["input"]["years"]["2025_26"]["financials"]["p1"]["properties"][0][
        "share"
    ] = "2/6"
    r_pa_a = _prepare(v2, pa_a)
    assert r_pa_a.status_code == 201, (
        f"未约简比率须接受并规范化（005 §3），实际 {r_pa_a.status_code}："
        f"{r_pa_a.text[:200]!r}"
    )
    pa_b = _v2_pa_body()
    pa_b["input"]["years"]["2025_26"]["financials"]["p1"]["properties"][0][
        "share"
    ] = "1/3"  # P1 r1 定点纠错：断言自述 2/6≡1/3，fixture 原 "1/1" 为误译（用户授权编排者直接执行，2026-10-15）
    r_pa_b = _prepare(v2, pa_b)
    assert r_pa_b.status_code == 201, r_pa_b.text
    assert r_pa_a.json()["input_hash"] == r_pa_b.json()["input_hash"], (
        "等价比率（2/6 vs 1/3）规范化后须同 canonical input hash（R3④）"
    )
