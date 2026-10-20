# -*- coding: utf-8 -*-
"""REQ-9/REQ-12 规则束准入合同（002 P1 Red，C 路；E1）。

REQ: REQ-9（规则状态不可核验 → 无回退、无默认）、REQ-12（版本/固定 P）。
本文件为 C 路新增单元节点：直接核 `app.api.rules_admission.verify_bundle_admission`
**内容级兼容包装**公共接口（bounded-plan §3 冻结签名：成功 None；不可核验
E_RULES_STATE_UNVERIFIABLE）；该包装只做内容 schema 检查，不构成真实
context/hash 准入证据（后者属中立 `app.rules.admission`，见本文件接口节点）。

规格锚点（唯一权威：Approved S002/005/006）:
  - 005 §2.1（context 与实际 bundle hash/schema/engine/目标年一致）、§15.1
    （固定 P＋digest 双向核验，自洽非固定不得放行）、§15.2（三年度矩阵/
    宽减表缺失＝规则不可核验 fail-closed；2026/27 无利得宽减为已核合法事实，
    不得强添）、§13（错误形状）、§2「缺规则不等于已核不适用」。
  - 复核 §一/§二 E1（实际 schema=2.0.0；双方一致的旧 schema 仍拒绝；
    活动节点/畸形参数 → E_RULES_STATE_UNVERIFIABLE，不得裸异常或误报输入错）。
  - Annex D（003:335–336,380）：2026/27 无利得宽减条目属已核实法律事实。

期望值来源: 版本号/错误码/封闭字段为已批准合同（S002 §4.1/005 §15）；
数值仅断言生产束既有结构（不从生产 resolver 反推答案）。

【被测契约（Green 阶段须按测试实现，不得要求测试改写）】:
  - 旧 API 兼容包装：app.api.rules_admission.verify_bundle_admission(content, *,
    tax_type, year)——内容级检查。
  - 中立最小层（bounded-plan §3）：app.core.facts_v2.normalize_v2_facts(raw, *,
    tax_type) → canonical v2 input（不是 envelope）；app.rules.admission 集中
    固定 schema/P/digest 与活动规则核验（公共签名同 verify_bundle_admission）。
  - 本文件对 app.* 延迟导入；不构造 app/不联网/不启动服务。
"""

from __future__ import annotations

import copy
import hashlib

import pytest

_YEARS = ("2024_25", "2025_26", "2026_27")


def _verify(content, *, tax_type: str, year: str | None):
    from app.api.rules_admission import verify_bundle_admission

    return verify_bundle_admission(content, tax_type=tax_type, year=year)


def _bundle() -> dict:
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    return copy.deepcopy(INITIAL_BUNDLE_CONTENT)


def _refresh_profile_digest(bundle: dict) -> None:
    from app.core.hashing import canonical_json_bytes

    for profile in bundle["frozen_semantics"]["profiles"]:
        if profile.get("profile_id") == "part5_shared_v2":
            body = {key: value for key, value in profile.items() if key != "semantic_digest"}
            profile["semantic_digest"] = (
                "sha256:" + hashlib.sha256(canonical_json_bytes(body)).hexdigest()
            )


def _expect_rules_error(fn, label: str) -> None:
    from app.core.errors import AppError

    try:
        fn()
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"{label}：期望 E_RULES_STATE_UNVERIFIABLE（005 §15/§2.1），"
            f"实际 {exc.code!r}"
        )
    except Exception as exc:  # noqa: BLE001 - 裸异常必须显式暴露为 Red
        raise AssertionError(
            f"{label}：须映射 E_RULES_STATE_UNVERIFIABLE，不得裸异常 "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise AssertionError(f"{label}：不可核验规则束不得放行（fail-closed）")


def test_002_rules_admission_accepts_production_bundle_supported_years() -> None:
    """合法对照：生产束对两税全部支持年度准入 None（唯一权威入口正例）。"""
    bundle = _bundle()
    for tax_type in ("salaries_tax", "personal_assessment"):
        for year in _YEARS:
            assert _verify(bundle, tax_type=tax_type, year=year) is None, (
                tax_type, year
            )


@pytest.mark.parametrize(
    "tax_type",
    ["salaries_tax", "personal_assessment"],
    ids=["salaries_tax", "personal_assessment"],
)
def test_002_rules_admission_content_wrapper_rejects_old_schema(tax_type: str) -> None:
    """内容级兼容包装：双方一致的旧 rules schema（1.0.0）仍拒绝——仅内容相等
    不足以放行（本节点不声称真实 context/hash 准入；后者由中立层节点承载）。"""
    bundle = _bundle()
    bundle["rules_schema_version"] = "1.0.0"
    _expect_rules_error(
        lambda: _verify(bundle, tax_type=tax_type, year="2025_26"),
        f"内容包装旧 schema 1.0.0（{tax_type}）",
    )


def test_002_rules_admission_rejects_self_consistent_mutated_profile() -> None:
    """P 被改动且 digest 同步重算（自洽）仍拒绝（005 §15.1 双向核验）。"""
    bundle = _bundle()
    for profile in bundle["frozen_semantics"]["profiles"]:
        if profile.get("profile_id") == "part5_shared_v2":
            profile["contracts"] = dict(profile["contracts"], facts="mutated_v9")
    _refresh_profile_digest(bundle)
    _expect_rules_error(
        lambda: _verify(bundle, tax_type="salaries_tax", year="2025_26"),
        "P 自洽非固定",
    )


def test_002_rules_admission_rejects_missing_active_allowance_node() -> None:
    """活动矩阵节点缺失（2025/26.basic）＝规则不可核验（005 §15.2）。"""
    bundle = _bundle()
    assert _verify(bundle, tax_type="salaries_tax", year="2025_26") is None, (
        "对照：完整束须先准入"
    )
    del bundle["data"]["allowances"]["2025_26"]["basic"]
    _expect_rules_error(
        lambda: _verify(bundle, tax_type="salaries_tax", year="2025_26"),
        "缺 basic 节点",
    )


def test_002_rules_admission_rejects_malformed_active_allowance_amount() -> None:
    """活动节点畸形金额 → E_RULES_STATE_UNVERIFIABLE（不得裸 Decimal/ValueError，
    也不得误报 E_INPUT_*）。"""
    bundle = _bundle()
    assert _verify(bundle, tax_type="salaries_tax", year="2025_26") is None
    bundle["data"]["allowances"]["2025_26"]["basic"]["amount"] = "not-a-money"
    _expect_rules_error(
        lambda: _verify(bundle, tax_type="salaries_tax", year="2025_26"),
        "畸形 basic.amount",
    )


def test_002_rules_admission_rejects_missing_active_reduction() -> None:
    """活动年度宽减节点缺失 → 规则不可核验（不得按零宽减计算）。"""
    bundle = _bundle()
    assert _verify(bundle, tax_type="salaries_tax", year="2025_26") is None
    del bundle["data"]["reductions"]["2025_26"]
    _expect_rules_error(
        lambda: _verify(bundle, tax_type="salaries_tax", year="2025_26"),
        "缺 reductions.2025_26",
    )


def test_002_rules_admission_rejects_missing_active_bands() -> None:
    """活动税率带缺失/为空 → 规则不可核验（不补发行数据）。"""
    bundle = _bundle()
    assert _verify(bundle, tax_type="personal_assessment", year="2025_26") is None
    del bundle["data"]["personal_assessment"]["progressive_bands"]
    _expect_rules_error(
        lambda: _verify(bundle, tax_type="personal_assessment", year="2025_26"),
        "缺 PA progressive_bands",
    )


def test_002_rules_admission_keeps_legitimate_2026_27_without_profits_reduction() -> None:
    """2026/27 已核无利得宽减（Annex D 003:335–336,380）：合法缺键不得被强添，
    不得因此拒绝 2026/27 或其紧邻检查。"""
    bundle = _bundle()
    reductions = bundle["data"]["personal_assessment"]["profits_reductions"]
    assert "2026_27" not in reductions, (
        "生产束 2026/27 无利得宽减条目为已核事实（Annex D）：此测试不得要求补键"
    )
    assert _verify(bundle, tax_type="personal_assessment", year="2026_27") is None, (
        "合法 2026/27（无 profits_reduction）须准入"
    )
    assert _verify(bundle, tax_type="personal_assessment", year="2025_26") is None, (
        "本年检查不得因紧邻 2026/27 合法缺利得宽减而失败"
    )
    assert _verify(bundle, tax_type="salaries_tax", year="2026_27") is None


# ---------------------------------------------------------------------------
# 中立接口闭包（bounded-plan §3；缺失＝接口 I，函数内失败而非收集期 ImportError）
# ---------------------------------------------------------------------------


def test_002_neutral_normalize_v2_facts_interface_returns_input_dict() -> None:
    """中立规范化层：normalize_v2_facts(raw, *, tax_type) → canonical v2 input
    （不是 envelope；无规则/网络依赖）。当前模块缺失＝接口 I（本测试内失败）。"""
    import importlib

    module = importlib.import_module("app.core.facts_v2")
    normalize = getattr(module, "normalize_v2_facts")
    raw = {
        "year_of_assessment": "2025_26",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": {
            "2025_26": {
                "participants": [{"person_id": "p1", "role": "self"}],
                "subject_facts": {},
                "household": {
                    "marriage": [{"from": "2025-04-01", "to": "2026-04-01",
                                  "value": "unmarried"}],
                    "living_apart": [],
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                "relations": [],
                "financials": {
                    "p1": {
                        "employment_income_nai": "240000.00",
                        "mpf_mandatory_contributions": "9000",
                        "has_assessable_salary_income": True,
                        "source_classification": {"salary": "confirmed_in_scope"},
                        "properties": [],
                        "businesses": [],
                        "deduction_items": [],
                    }
                },
                "care_payments": [],
                "claims": [],
                "assessment": {"basis": "actual",
                               "candidates": ["salary_separate"],
                               "selection": "salary_separate"},
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }
    result = normalize(copy.deepcopy(raw), tax_type="salaries_tax")
    assert isinstance(result, dict), (
        f"normalize_v2_facts 须返回 canonical v2 input dict，实际 {type(result)!r}"
    )
    assert "tax_type" not in result and "schema_version" not in result, (
        "返回的是 input 内容，不是 envelope（bounded-plan §3）"
    )
    assert result.get("year_of_assessment") == "2025_26", result
    canonical = result["years"]["2025_26"]["financials"]["p1"]
    assert canonical["employment_income_nai"] == "240000", (
        f"返回 input 内的 Money 须已 canonical（尾零去除），实际：{canonical!r}"
    )


def test_002_neutral_admission_interface_signature_closure() -> None:
    """中立准入层：app.rules.admission 集中固定 schema/P/digest 与活动规则核验，
    公共签名 = verify_bundle_admission(content, *, tax_type, year)；与旧 API 内容
    包装分离（包装保持兼容，不充当未来中立层）。当前缺失＝接口 I。"""
    import importlib
    import inspect

    module = importlib.import_module("app.rules.admission")
    verify = getattr(module, "verify_bundle_admission")
    assert callable(verify), f"verify_bundle_admission 必须可调用，实际 {verify!r}"
    signature = inspect.signature(verify)
    params = list(signature.parameters)
    assert params and params[0] == "content", (
        f"首个位置参数必须为 content，实际 {params!r}"
    )
    assert signature.parameters["tax_type"].kind is inspect.Parameter.KEYWORD_ONLY, (
        "tax_type 必须为 keyword-only（bounded-plan §3 冻结签名）"
    )
    assert signature.parameters["year"].kind is inspect.Parameter.KEYWORD_ONLY, (
        "year 必须为 keyword-only（bounded-plan §3 冻结签名）"
    )


# ---------------------------------------------------------------------------
# 2026/27 活动业务：无年度宽减（Annex D 已核）＋独立算术
# ---------------------------------------------------------------------------


def _availability_2026_27(bundle: dict) -> dict:
    from app.config import ENGINE_VERSION
    from app.core.hashing import bundle_content_hash

    return {
        "binding": {
            "bundle_id": "u-002-active-business",
            "bundle_hash": bundle_content_hash(bundle),
            "rules_schema_version": bundle["rules_schema_version"],
            "engine_version": ENGINE_VERSION,
        },
        "current": {"year": "2026_27", "status": "available", "reason_codes": []},
        "next": {"year": "2027_28", "status": "missing_rules", "reason_codes": []},
    }


def _active_business_2026_27_facts() -> dict:
    """2026/27 单身、无薪俸、活动独资业务（no_pa）合法 v2 输入。"""
    return {
        "year_of_assessment": "2026_27",
        "subjects": [{"subject_id": "p1", "birth_date": "1990-01-01"}],
        "years": {
            "2026_27": {
                "participants": [{"person_id": "p1", "role": "self"}],
                "subject_facts": {},
                "household": {
                    "marriage": [{"from": "2026-04-01", "to": "2027-04-01",
                                  "value": "unmarried"}],
                    "living_apart": [],
                    "child_nomination": {"person_id": None, "status": "none"},
                },
                "relations": [],
                "financials": {
                    "p1": {
                        "employment_income_nai": "0",
                        "mpf_mandatory_contributions": "0",
                        "has_assessable_salary_income": False,
                        "has_assessable_business_income": True,
                        "has_income_assessable_under_ordinance": True,
                        "source_classification": {"salary": "confirmed_in_scope",
                                                   "business": "confirmed_in_scope"},
                        "properties": [],
                        "businesses": [
                            {"business_id": "b1",
                             "entity_kind": "sole_proprietorship",
                             "assessable_profit": "100000", "share": "1/1"}
                        ],
                        "deduction_items": [],
                    }
                },
                "care_payments": [],
                "claims": [],
                "assessment": {
                    "basis": "actual",
                    "actual": {
                        "salary_joint_elected": False,
                        "salary_joint_assessed": False,
                        "pa": {"p1": {"mode": "none"}},
                        "history": "ordinary",
                    },
                    "candidates": ["no_pa"],
                    "selection": "no_pa",
                },
                "manual_issues": [],
                "provisional_paid": "0",
            }
        },
        "hk_presence_days": [],
        "assumptions": [],
    }


def test_002_active_2026_27_business_no_rebate_independent_arithmetic() -> None:
    """2026/27 无利得宽减为已核事实（Annex D）：活动独资 no_pa 税额＝独立算术
    floor(利润份额 100,000)×低率 7.5%＝7,500，reduction=0（不得套用往年 cap）。"""
    from app.engines.personal_assessment import compare_personal_assessment

    bundle = _bundle()
    result = compare_personal_assessment(
        _active_business_2026_27_facts(), bundle, availability=_availability_2026_27(bundle)
    )
    amounts = result["amounts"]
    assert amounts["before_reduction"] == "7500", (
        f"no_pa 活动业务：100,000×7.5%＝7,500（approved_unincorporated_floor），"
        f"实际 {amounts!r}"
    )
    assert amounts["reduction"] == "0", (
        f"2026/27 无宽减（Annex D）：reduction 必须为 0，实际 {amounts!r}"
    )
    assert amounts["final_after_reduction"] == "7500", (
        f"无宽减年度 final＝before＝7,500，实际 {amounts!r}"
    )


# ---------------------------------------------------------------------------
# F1 Wave A（2026-10-17）：Money 词法/域、status 枚举/年度适用语义、cap 缺键
# ---------------------------------------------------------------------------


def _expect_rules_error_at(fn, field: str, label: str) -> None:
    from app.core.errors import AppError

    try:
        fn()
    except AppError as exc:
        assert exc.code == "E_RULES_STATE_UNVERIFIABLE", (
            f"{label}：期望 E_RULES_STATE_UNVERIFIABLE（005 §15.2），"
            f"实际 {exc.code!r}"
        )
        assert exc.field == field, (
            f"{label}：错误须定位到规则字段 {field!r}，实际 {exc.field!r}"
        )
    except Exception as exc:  # noqa: BLE001 - 裸异常必须显式暴露为 Red
        raise AssertionError(
            f"{label}：须映射 E_RULES_STATE_UNVERIFIABLE，不得裸异常 "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise AssertionError(f"{label}：不可核验规则不得放行（fail-closed）")


def test_002_rules_admission_money_domain_rejects_negative_exponent_and_bounds() -> None:
    """F1/R1（005 §15.2＋§3 Money）：矩阵 amount 以 Money 词法/长度/量级/小数位
    域准入——"-1"/"1e3" 等一律 E_RULES_STATE_UNVERIFIABLE；合法尾零
    "1200000.00" 与 1e14 边界仍放行（不得要求整数词法）。"""
    year = "2025_26"
    field = f"data.allowances.{year}.basic.amount"
    for bad in ("-1", "1e3", "", " 1", "1.234", "100000000000001", "0001200000"):
        bundle = _bundle()
        bundle["data"]["allowances"][year]["basic"]["amount"] = bad
        _expect_rules_error_at(
            lambda b=bundle: _verify(b, tax_type="salaries_tax", year=year),
            field,
            f"basic.amount={bad!r}",
        )
    for legal in ("1200000.00", "100000000000000", "0", "0.00"):
        bundle = _bundle()
        bundle["data"]["allowances"][year]["basic"]["amount"] = legal
        assert _verify(bundle, tax_type="salaries_tax", year=year) is None, (
            f"合法 Money 对照 {legal!r} 须准入（不得要求整数词法）"
        )


def test_002_rules_admission_status_enum_and_year_applicability() -> None:
    """F1/R1（005 §15.2）：status 须为封闭枚举且符合节点/年度适用语义——
    "unknown" 拒；2026/27 following-year 写 not_applicable_verified（合法枚举但
    错误年度）拒；applicable 节点写成 not_applicable_verified 亦拒。"""
    year = "2025_26"
    bundle = _bundle()
    bundle["data"]["allowances"][year]["basic"]["status"] = "unknown"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.allowances.{year}.basic.status",
        "status=unknown",
    )
    bundle = _bundle()
    bundle["data"]["allowances"]["2026_27"]["child_following_year_additional"][
        "status"
    ] = "not_applicable_verified"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="personal_assessment", year="2026_27"),
        "data.allowances.2026_27.child_following_year_additional.status",
        "合法枚举但错误年度",
    )
    bundle = _bundle()
    bundle["data"]["allowances"][year]["basic"]["status"] = "not_applicable_verified"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.allowances.{year}.basic.status",
        "applicable 节点写成 not_applicable_verified",
    )
    assert _verify(_bundle(), tax_type="salaries_tax", year="2024_25") is None, (
        "2024/25 following-year 已核不适用节点须保持合法"
    )


def test_002_rules_admission_cap_presence_and_rule_numeric_domains() -> None:
    """F1/R1（005 §15.2）：cap:null 与缺 cap 键语义不同；amount/cap、MPF、
    ERCE cap 及 width/threshold/cap 域不因 int()/Decimal() 可执行即放行。"""
    year = "2025_26"
    bundle = _bundle()
    assert bundle["data"]["allowances"][year]["basic"]["cap"] is None
    assert _verify(bundle, tax_type="salaries_tax", year=year) is None, (
        "对照：cap:null 为合法域（不表示未知）"
    )
    del bundle["data"]["allowances"][year]["basic"]["cap"]
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.allowances.{year}.basic.cap",
        "缺 cap 键（≠null）",
    )
    bundle = _bundle()
    bundle["data"]["allowances"][year]["basic"]["cap"] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.allowances.{year}.basic.cap",
        "cap=-1",
    )
    bundle = _bundle()
    bundle["data"]["allowances"]["mpf_deduction_cap"] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        "data.allowances.mpf_deduction_cap",
        "MPF cap=-1",
    )
    bundle = _bundle()
    bundle["data"]["salaries"]["deductions"]["elder_residential_care"]["caps"][
        year
    ] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.salaries.deductions.elder_residential_care.caps.{year}",
        "ERCE cap=-1",
    )
    bundle = _bundle()
    bundle["data"]["salaries"]["progressive_bands"][0]["width"] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        "data.salaries.progressive_bands[0].width",
        "width=-1",
    )
    bundle = _bundle()
    bundle["data"]["salaries"]["progressive_bands"][0]["width"] = "1e3"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        "data.salaries.progressive_bands[0].width",
        "width=1e3",
    )
    bundle = _bundle()
    bundle["data"]["personal_assessment"]["profits_two_tier"]["threshold"] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="personal_assessment", year=year),
        "data.personal_assessment.profits_two_tier.threshold",
        "threshold=-1",
    )
    bundle = _bundle()
    bundle["data"]["personal_assessment"]["profits_reductions"][year]["cap"] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="personal_assessment", year=year),
        f"data.personal_assessment.profits_reductions.{year}.cap",
        "profits reduction cap=-1",
    )


# ---------------------------------------------------------------------------
# F1 残余点修（2026-10-17）：共享宽减表年度 status 适用语义＋NA 数值核验
# ---------------------------------------------------------------------------


def test_002_rules_reduction_status_year_applicability_admission() -> None:
    """F1 残余（005 §15.2/005:839）：唯一 salary/PA 宽减表的 status 按年度冻结——
    2024/25、2025/26 为 applicable；2026/27 为 not_applicable_verified。
    伪造 not_applicable_verified（本年有宽减年度）或 applicable（已核无宽减年度）
    均须带规则字段拒算；三年度合同合法 status 组合逐税准入（不得误拒）。"""
    bad = (
        ("2024_25", "not_applicable_verified"),
        ("2025_26", "not_applicable_verified"),
        ("2026_27", "applicable"),
    )
    for year, status in bad:
        bundle = _bundle()
        bundle["data"]["reductions"][year]["status"] = status
        _expect_rules_error_at(
            lambda b=bundle, y=year: _verify(b, tax_type="salaries_tax", year=y),
            f"data.reductions.{year}.status",
            f"reductions.{year}.status={status}",
        )
    legal = _bundle()
    for tax_type in ("salaries_tax", "personal_assessment"):
        for year in _YEARS:
            assert _verify(legal, tax_type=tax_type, year=year) is None, (
                f"三年度合法 status 不得误拒：{tax_type} {year} "
                f"{legal['data']['reductions'][year]['status']!r}"
            )


def test_002_rules_reduction_not_applicable_still_validates_numeric_domains() -> None:
    """F1 残余（005 §15.2/005:839）：2026/27 的 not_applicable_verified 是「已核
    不适用」而非免于结构核验——唯一宽减表仍声明 percent_bp/cap，缺失或畸形须
    E_RULES_STATE_UNVERIFIABLE（不得以 status 跳过数值域）。"""
    year = "2026_27"
    bundle = _bundle()
    del bundle["data"]["reductions"][year]["percent_bp"]
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.reductions.{year}.percent_bp",
        "not_applicable_verified 缺 percent_bp",
    )
    bundle = _bundle()
    bundle["data"]["reductions"][year]["cap"] = "-1"
    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type="salaries_tax", year=year),
        f"data.reductions.{year}.cap",
        "not_applicable_verified cap=-1",
    )


@pytest.mark.parametrize("kind", ["salary", "pa"], ids=["salary", "pa"])
def test_002_rules_reduction_wrong_status_rejected_by_pure_entry_and_runtime(
    kind: str,
) -> None:
    """F1 残余（005 §15.2/005:839）：仅改 reductions.2025_26.status=
    not_applicable_verified（金额/事实/profile 不变，按六字段内容重算真实 bundle
    hash）→ 两税纯入口与 runtime.admit_runtime 均在规则字段拒算；合同合法束
    对照 final=940（不得把 940 放行为 3,940）。"""
    from app.core.errors import AppError
    from app.rules import runtime
    import tests.engines.test_002_next_year as fx

    current, target = "2025_26", "2026_27"
    tax_type = "salaries_tax" if kind == "salary" else "personal_assessment"
    facts = fx._dual_year_input(current, target, pa=(kind == "pa"))
    runner = fx._run_salary_with if kind == "salary" else fx._run_pa_with

    control_bundle = fx._v2_bundle()
    control_ctx = fx._availability(control_bundle, current)
    control = runner(facts, control_bundle, control_ctx)
    assert control["amounts"]["final_after_reduction"] == "940", (
        f"{kind}：合同合法 status 对照组须 940，实际：{control['amounts']!r}"
    )

    bundle = copy.deepcopy(fx._v2_bundle())
    bundle["data"]["reductions"][current]["status"] = "not_applicable_verified"
    ctx = fx._availability(bundle, current)
    assert ctx["binding"]["bundle_hash"] != control_ctx["binding"]["bundle_hash"], (
        "规则内容变更后须按真实六字段内容重算 bundle hash（C2.8）"
    )
    field = f"data.reductions.{current}.status"

    try:
        runner(facts, bundle, ctx)
    except AppError as exc:
        assert (exc.code, exc.field) == ("E_RULES_STATE_UNVERIFIABLE", field), (
            f"{kind} 纯入口：须以规则字段拒算，实际 {(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"{kind} 纯入口不得放行错误年度 not_applicable_verified（940→3,940）"
        )

    guard_read = {
        "periods": [
            {"year": current, "status": "available", "reason_codes": []},
            {"year": target, "status": "available", "reason_codes": []},
        ],
        "warnings": [],
    }
    try:
        runtime.admit_runtime(
            ctx["binding"],
            bundle,
            tax_type=tax_type,
            facts=facts,
            guard_read=guard_read,
        )
    except AppError as exc:
        assert (exc.code, exc.field) == ("E_RULES_STATE_UNVERIFIABLE", field), (
            f"{kind} runtime：须与纯入口同码同字段，实际 "
            f"{(exc.code, exc.field)!r}"
        )
    else:
        raise AssertionError(
            f"{kind} runtime 不得放行错误年度 not_applicable_verified"
        )


# ---------------------------------------------------------------------------
# F1-fix2（2026-10-17）：共享宽减表数值域（percent_bp 基点域＋cap Money）
# ---------------------------------------------------------------------------


def _following_ya(year: str) -> str:
    start = int(year[:4])
    return f"{start + 1}_{(start + 2) % 100:02d}"


def _reduction_guard_read(year: str) -> dict:
    return {
        "periods": [
            {"year": year, "status": "available", "reason_codes": []},
            {
                "year": _following_ya(year),
                "status": "missing_rules",
                "reason_codes": [],
            },
        ],
        "warnings": [],
    }


@pytest.mark.parametrize("kind", ["salary", "pa"], ids=["salary", "pa"])
@pytest.mark.parametrize("year", ["2025_26", "2026_27"])
def test_002_rules_reduction_percent_bp_negative_rejected_by_pure_entry_and_runtime(
    kind: str, year: str
) -> None:
    """F1-fix2（005 §15.2/005:839＋附表 43）：共享宽减表 percent_bp 为基点整数域
    0..10000；伪造 -1 并重算真实 bundle hash 后，两税直接准入、纯入口与
    runtime.admit_runtime 均须在 data.reductions.<YA>.percent_bp 拒算。
    对照仍按合同值计算（2025/26 final=940；2026/27 无宽减 final=3160）。"""
    from app.core.hashing import bundle_content_hash
    from app.rules import runtime
    import tests.engines.test_002_next_year as fx

    tax_type = "salaries_tax" if kind == "salary" else "personal_assessment"
    facts = fx._dual_year_input(year, _following_ya(year), pa=(kind == "pa"))
    runner = fx._run_salary_with if kind == "salary" else fx._run_pa_with

    control_bundle = fx._v2_bundle()
    control_ctx = fx._availability(control_bundle, year)
    control = runner(facts, control_bundle, control_ctx)
    expected_control = "940" if year == "2025_26" else "3160"
    assert control["amounts"]["final_after_reduction"] == expected_control, (
        f"{kind} {year} 对照须 final={expected_control}，"
        f"实际 {control['amounts']!r}"
    )

    bundle = copy.deepcopy(fx._v2_bundle())
    bundle["data"]["reductions"][year]["percent_bp"] = -1
    ctx = fx._availability(bundle, year)
    assert ctx["binding"]["bundle_hash"] != control_ctx["binding"]["bundle_hash"], (
        "宽减数值变更后须按真实六字段内容重算 bundle hash（C2.8）"
    )
    assert bundle_content_hash(bundle) == ctx["binding"]["bundle_hash"], (
        "availability.binding.bundle_hash 必须是真实重算 hash"
    )
    field = f"data.reductions.{year}.percent_bp"

    _expect_rules_error_at(
        lambda: _verify(bundle, tax_type=tax_type, year=year),
        field,
        f"{kind} {year} 直接准入 percent_bp=-1",
    )
    _expect_rules_error_at(
        lambda: runner(facts, bundle, ctx),
        field,
        f"{kind} {year} 纯入口 percent_bp=-1",
    )
    _expect_rules_error_at(
        lambda: runtime.admit_runtime(
            ctx["binding"],
            bundle,
            tax_type=tax_type,
            facts=facts,
            guard_read=_reduction_guard_read(year),
        ),
        field,
        f"{kind} {year} runtime percent_bp=-1",
    )


def test_002_rules_reduction_percent_bp_legal_domain_keeps_940_control() -> None:
    """F1-fix2（005 §15.2/005:839＋003:336 附表 43 100%）：合法域对照不得误拒——
    0（2026/27 生产值＝下界）、10000（100%＝上界）与生产正常值均准入；本年
    2025/26 对照 final=940；仅当被消费时 10000 才产生 3,000 宽减。"""
    import tests.engines.test_002_next_year as fx

    facts = fx._dual_year_input("2025_26", "2026_27")

    normal = fx._v2_bundle()
    assert normal["data"]["reductions"]["2025_26"]["percent_bp"] == 10000
    assert normal["data"]["reductions"]["2026_27"]["percent_bp"] == 0
    assert _verify(normal, tax_type="salaries_tax", year="2025_26") is None
    ctx = fx._availability(normal, "2025_26")
    assert fx._run_salary_with(facts, normal, ctx)["amounts"][
        "final_after_reduction"
    ] == "940", "生产正常值（10000）对照须 940"

    upper = copy.deepcopy(fx._v2_bundle())
    upper["data"]["reductions"]["2025_26"]["percent_bp"] = 10000
    assert _verify(upper, tax_type="salaries_tax", year="2025_26") is None
    ctx = fx._availability(upper, "2025_26")
    assert fx._run_salary_with(facts, upper, ctx)["amounts"][
        "final_after_reduction"
    ] == "940", "上界 10000（100%）须准入且仍为 940"

    lower = copy.deepcopy(fx._v2_bundle())
    lower["data"]["reductions"]["2026_27"]["percent_bp"] = 0
    for tax_type in ("salaries_tax", "personal_assessment"):
        for year in _YEARS:
            assert _verify(lower, tax_type=tax_type, year=year) is None, (
                f"下界 0 不得误拒：{tax_type} {year}"
            )
    ctx = fx._availability(lower, "2025_26")
    assert fx._run_salary_with(facts, lower, ctx)["amounts"][
        "final_after_reduction"
    ] == "940", "下一年 0（生产值）不得改变本年 940"

    zero_applicable = copy.deepcopy(fx._v2_bundle())
    zero_applicable["data"]["reductions"]["2025_26"]["percent_bp"] = 0
    assert _verify(zero_applicable, tax_type="personal_assessment", year="2025_26") is None, (
        "下界 0 在 applicable 年度亦为合法域（仅消费时宽减为 0）"
    )


def test_002_rules_reduction_percent_bp_type_and_upper_bound_rejected() -> None:
    """F1-fix2（005 §15.2/005:839）：percent_bp 须为非 bool 整数且 ≤10000——
    10001、负值、bool、字符串数字、float 与 None 均不得以类型可执行放行。"""
    field = "data.reductions.2025_26.percent_bp"
    for bad in (-1, 10001, 10**18, True, "10000", 10000.0, None):
        bundle = _bundle()
        bundle["data"]["reductions"]["2025_26"]["percent_bp"] = bad
        _expect_rules_error_at(
            lambda b=bundle: _verify(b, tax_type="salaries_tax", year="2025_26"),
            field,
            f"percent_bp={bad!r}",
        )


def test_002_rules_reduction_cap_money_domain_boundaries() -> None:
    """F1-fix2（005 §15.2/005:839＋§3 Money）：宽减 cap 与同表准入同域——
    非负 canonical Money 字符串，长度/量级/小数位受 C2.1 约束；"-1"/"1e3"/
    "1.234"/超 1e14/JSON number/空串/空白/前导零均拒；合法尾零/0/1e14 放行。"""
    field = "data.reductions.2025_26.cap"
    for bad in ("-1", "1e3", "1.234", "100000000000001", 3000, "", " 3000", "0003000"):
        bundle = _bundle()
        bundle["data"]["reductions"]["2025_26"]["cap"] = bad
        _expect_rules_error_at(
            lambda b=bundle: _verify(b, tax_type="salaries_tax", year="2025_26"),
            field,
            f"reductions.cap={bad!r}",
        )
    for legal in ("3000", "3000.00", "0", "100000000000000"):
        bundle = _bundle()
        bundle["data"]["reductions"]["2025_26"]["cap"] = legal
        assert _verify(bundle, tax_type="salaries_tax", year="2025_26") is None, (
            f"合法 Money cap {legal!r} 不得误拒"
        )


def test_002_rules_reduction_numeric_domain_enforced_at_consumption_boundary() -> None:
    """F1-fix2（allowances.py:929,1031–1049）：准入之外，消费助手也不得消费非法
    数值——reduction_entry 对 applicable/NA 均核 percent_bp/cap；直接调用
    salary_unit_tax（纯内部路径）同样拒算；合法 NA 仍返回 None（合同语义保留）。"""
    from fractions import Fraction

    from app.engines.allowances import reduction_entry, salary_unit_tax
    import tests.engines.test_002_next_year as fx

    bundle = copy.deepcopy(fx._v2_bundle())
    bundle["data"]["reductions"]["2025_26"]["percent_bp"] = -1
    _expect_rules_error_at(
        lambda: reduction_entry(bundle, "2025_26"),
        "data.reductions.2025_26.percent_bp",
        "reduction_entry applicable percent_bp=-1",
    )

    bundle = copy.deepcopy(fx._v2_bundle())
    bundle["data"]["reductions"]["2025_26"]["cap"] = "-1"
    _expect_rules_error_at(
        lambda: reduction_entry(bundle, "2025_26"),
        "data.reductions.2025_26.cap",
        "reduction_entry applicable cap=-1",
    )

    bundle = copy.deepcopy(fx._v2_bundle())
    bundle["data"]["reductions"]["2026_27"]["percent_bp"] = -1
    _expect_rules_error_at(
        lambda: reduction_entry(bundle, "2026_27"),
        "data.reductions.2026_27.percent_bp",
        "reduction_entry NA percent_bp=-1",
    )

    salaries = fx._v2_bundle()["data"]["salaries"]
    _expect_rules_error_at(
        lambda: salary_unit_tax(
            branch="salary_floor_min_v1",
            nai=Fraction(240000),
            part4a=Fraction(9000),
            allowances=132000,
            bands=salaries["progressive_bands"],
            standard_bands=salaries["standard_rate_bands"],
            reduction={"percent_bp": -1, "cap": "3000"},
        ),
        "data.reductions.percent_bp",
        "salary_unit_tax direct percent_bp=-1",
    )

    assert reduction_entry(fx._v2_bundle(), "2026_27") is None, (
        "合法 2026/27 not_applicable_verified 仍须返回 None（合同语义保留）"
    )
