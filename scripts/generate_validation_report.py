#!/usr/bin/env python3
"""引擎 vs 官方示例 I/O 测试报告生成（真实运行；只读、无网络）。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe scripts/generate_validation_report.py

行为：
    - 导入 app.rules.bundle.INITIAL_BUNDLE_CONTENT（只读）并真实调用各引擎
      纯函数：salaries／profits／property／personal_assessment／
      stamps.lease／stamps.property（AVD）／stamps.stock；
    - 批次 A：官方示例对照——输入形状逐字段照抄 tests/engines/** 与
      tests/acceptance/** 既有用例语义（不发明 schema），每行注明对应测试名；
    - 批次 B：固定 random.seed(20261013) 的 20 条模拟数据（仅演示 I/O，
      不构成合规样例）；
    - 批次 C：边界与三态（含 API 层 tax_type 错误形状——引擎层不适用）；
    - 输出 docs/validation/001-engine-io-report.md；
    - 任一实跑对照不一致（或声明的测试名不存在）→ 退出码 1。

边界：不修改 app/、tests/、specs/ 及其他 docs 内容；不访问网络。
"""

from __future__ import annotations

import json
import platform
import random
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.api.validation import validate_prepare_body
from app.core.errors import AppError
from app.engines.personal_assessment import compare_personal_assessment
from app.engines.profits import calculate_profits_tax
from app.engines.property import calculate_property_tax
from app.engines.salaries import calculate_salaries_tax
from app.engines.stamps.lease import calculate_lease_stamp_duty
from app.engines.stamps.property import calculate_property_avd
from app.engines.stamps.stock import calculate_stock_stamp_duty
from app.rules.bundle import INITIAL_BUNDLE_CONTENT

REPORT_DATE = "2026-10-13"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "docs" / "validation" / "001-engine-io-report.md"
BUNDLE = INITIAL_BUNDLE_CONTENT

_AMOUNT_KEYS = (
    "before_reduction",
    "reduction",
    "final_after_reduction",
    "provisional_paid",
    "next_provisional",
    "balance",
)


@dataclass
class CheckItem:
    """批次 A/C 的一条对照检查（实际输出＝本次真实运行结果）。"""

    name: str
    input_summary: str
    expected: str
    actual: str
    test_name: str
    passed: bool
    covered: bool = True

    @property
    def outcome(self) -> str:
        if not self.covered:
            return "未覆盖"
        return "✅ 一致" if self.passed else "❌ 不一致"


@dataclass
class SimItem:
    """批次 B 一条模拟数据运行结果。"""

    engine: str
    input_json: str
    status: str
    key_amounts: str


# ------------------------------------------------------------------ 通用助手
def _single(year: str, income: str, mpf: str) -> dict:
    """单身受雇人士最小事实集（tests/engines/test_salaries_engine.py 同款）。"""
    return {
        "year_of_assessment": year,
        "employment_income": income,
        "mpf_mandatory_contributions": mpf,
        "married_status": {"value": "single"},
    }


def _paid_rates(amount: str) -> dict:
    """业主同意并已缴差饷（两条件均真，s.5(1A)(b)(i)）。"""
    return {
        "amount": amount,
        "agreed": {"value": True},
        "actually_paid": {"value": True},
    }


def _property(pid: str, rent: str, **extra: object) -> dict:
    prop: dict = {
        "property_id": pid,
        "rent_received": rent,
        "rates_paid_by_owner": _paid_rates("0"),
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
    }
    prop.update(extra)
    return prop


def _property_facts(year: str, *properties: dict, **extra: object) -> dict:
    facts: dict = {"year_of_assessment": year, "properties": list(properties)}
    facts.update(extra)
    return facts


def _pa_property(pid: str, rent: str, *, share: str = "1/1", interest: str = "0") -> dict:
    return {
        "property_id": pid,
        "share": share,
        "rent_received": rent,
        "rates_paid_by_owner": _paid_rates("0"),
        "irrecoverable_rent": "0",
        "deposit_offsets": "0",
        "mortgage_interest": interest,
    }


def _person(
    pid: str,
    *,
    salary: str = "0",
    mpf: str = "0",
    properties: list | None = None,
    businesses: list | None = None,
    parents: list | None = None,
    children: list | None = None,
) -> dict:
    return {
        "person_id": pid,
        "employment_income": salary,
        "mpf_mandatory_contributions": mpf,
        "properties": list(properties or []),
        "businesses": list(businesses or []),
        "dependent_parents": list(parents or []),
        "dependent_children": list(children or []),
    }


def _pa_facts(year: str, *persons: dict, married: str = "married", **extra: object) -> dict:
    facts: dict = {
        "year_of_assessment": year,
        "married_status": {"value": married},
        "persons": list(persons),
        "provisional_paid": "0",
    }
    facts.update(extra)
    return facts


def _confirmed_two_tier() -> dict:
    """已确认的两级制资格事实（无关联实体、已声明选择、无其他选择）。"""
    return {
        "connected_entities": [],
        "election_made": {"value": True},
        "no_other_election_same_year": {"value": True},
    }


def _entity(year: str, kind: str, profit: str, **extra: object) -> dict:
    facts: dict = {
        "year_of_assessment": year,
        "entity_kind": kind,
        "assessable_profit": profit,
        "two_tier": _confirmed_two_tier(),
    }
    facts.update(extra)
    return facts


def _lease(**overrides: object) -> dict:
    """租约事实构造器（tests/engines/stamps/test_lease.py 同款默认值；
    None 值＝该键不提供）。"""
    facts: dict = {
        "instrument_date": "2025-06-20",
        "term_kind": "fixed",
        "term_start": "2025-07-01",
        "term_end": "2027-06-30",
        "rent_input_mode": "fixed_monthly",
        "monthly_rent": "10000",
        "premium": "0",
    }
    for key, value in overrides.items():
        if value is None:
            facts.pop(key, None)
        else:
            facts[key] = value
    return facts


def _avd(
    base: str,
    *,
    date: str,
    property_class: str | None = "residential",
    kind: str = "agreement_for_sale",
    consideration: str | None = None,
    value: str | None = None,
) -> dict:
    facts: dict = {
        "instrument_date": date,
        "instrument_kind": kind,
        "consideration": consideration if consideration is not None else base,
        "value": value if value is not None else base,
    }
    if property_class is not None:
        facts["property_class"] = property_class
    return facts


def _stock(*documents: dict, date: str = "2025-06-10", complete: bool = True) -> dict:
    return {
        "instrument_date": date,
        "complete": {"value": complete},
        "documents": list(documents),
    }


def _fmt_amounts(amounts: Mapping[str, object], keys: Sequence[str] = _AMOUNT_KEYS) -> str:
    return "；".join(
        f"{key}={'null' if amounts.get(key) is None else amounts[key]}" for key in keys
    )


def _scenario(result: Mapping, method: str) -> Mapping:
    for scenario in result.get("scenarios", []):
        if scenario.get("method") == method:
            return scenario
    raise RuntimeError(f"比较结果缺少方案 {method!r}：{result.get('scenarios')!r}")


def _verdict(checks: Sequence[tuple[str, object, object]]) -> tuple[bool, list[str]]:
    failures = [
        f"{label}：期望 {expected!r}，实际 {actual!r}"
        for label, actual, expected in checks
        if actual != expected
    ]
    return not failures, failures


def _make_item(
    *,
    name: str,
    input_summary: str,
    expected: str,
    checks: Sequence[tuple[str, object, object]],
    actual: str,
    test_name: str,
) -> CheckItem:
    ok, failures = _verdict(checks)
    if failures:
        actual = actual + "；不一致：" + "；".join(failures)
    return CheckItem(
        name=name,
        input_summary=input_summary,
        expected=expected,
        actual=actual,
        test_name=test_name,
        passed=ok,
    )


def _guard(name: str, builder: Callable[[], CheckItem]) -> CheckItem:
    """运行单个案例；未处理异常＝对照失败（不夸大、不中断整份报告）。"""
    try:
        return builder()
    except Exception as exc:  # noqa: BLE001 - 报告需记录任何实跑异常
        return CheckItem(
            name=name,
            input_summary="（运行异常）",
            expected="正常返回计算结果",
            actual=f"未处理异常：{type(exc).__name__}: {exc}",
            test_name="（—）",
            passed=False,
        )


def _expect_error_code(call: Callable[[], object]) -> str:
    """执行预期失败调用：返回 AppError.code（或未处理异常名／无异常标记）。"""
    try:
        call()
    except AppError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001
        return f"未处理异常 {type(exc).__name__}"
    return "（无异常）"


def _truncate(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------------ 批次 A：官方示例
def _a_pam39_q1() -> CheckItem:
    result = calculate_salaries_tax(_single("2025_26", "240000", "9000"), BUNDLE)
    amounts = result["amounts"]
    return _make_item(
        name="PAM39 Q1（2025/26）",
        input_summary="薪俸：240,000／MPF 9,000／single（无暂缴事实）",
        expected=(
            "累进 3,940、宽减 3,000、final 940（T2 PAM39 Q1，台账 1.13；"
            "T1 附表 2/4/43 手算见测试注释）"
        ),
        checks=[
            ("before_reduction", amounts["before_reduction"], "3940"),
            ("reduction", amounts["reduction"], "3000"),
            ("final_after_reduction", amounts["final_after_reduction"], "940"),
        ],
        actual=(
            f'status={result["status"]}；'
            + _fmt_amounts(amounts, ("before_reduction", "reduction", "final_after_reduction"))
        ),
        test_name="test_salaries_pam39_q1_final_940",
    )


def _a_pam39_q2() -> CheckItem:
    result = calculate_salaries_tax(_single("2026_27", "480000", "18000"), BUNDLE)
    amounts = result["amounts"]
    return _make_item(
        name="PAM39 Q2 PST（2026/27）",
        input_summary="薪俸：480,000／MPF 18,000／single（无暂缴事实）",
        expected=(
            "PST 35,890；2026/27 无宽减条目 → reduction 0、final 35,890"
            "（T2 PAM39 Q2，台账 1.13）"
        ),
        checks=[
            ("before_reduction", amounts["before_reduction"], "35890"),
            ("reduction", amounts["reduction"], "0"),
            ("final_after_reduction", amounts["final_after_reduction"], "35890"),
            ("next_provisional", amounts["next_provisional"], "35890"),
        ],
        actual=(
            f'status={result["status"]}；'
            + _fmt_amounts(
                amounts,
                ("before_reduction", "reduction", "final_after_reduction", "next_provisional"),
            )
        ),
        test_name="test_salaries_pam39_pst_2026_27_35890",
    )


def _a_property_q7() -> CheckItem:
    result = calculate_property_tax(
        _property_facts("2025_26", _property("prop-1", "120000")), BUNDLE
    )
    detail = result["properties"][0]
    amounts = result["amounts"]
    return _make_item(
        name="物业 Q7（2025/26）",
        input_summary="单一物业：租金 120,000、差饷 0、无不可追回/按金",
        expected=(
            "NAV 96,000 → 税 14,400＋下年度暂缴 14,400（pty.htm Q7，T1 台账 1.7；"
            "无物业税宽减）"
        ),
        checks=[
            ("nav", detail["nav"], "96000"),
            ("tax", detail["tax"], "14400"),
            ("before_reduction", amounts["before_reduction"], "14400"),
            ("reduction", amounts["reduction"], "0"),
            ("final_after_reduction", amounts["final_after_reduction"], "14400"),
            ("next_provisional", amounts["next_provisional"], "14400"),
        ],
        actual=(
            f'status={result["status"]}；nav={detail["nav"]}；tax={detail["tax"]}；'
            + _fmt_amounts(amounts, ("before_reduction", "reduction", "final_after_reduction", "next_provisional"))
        ),
        test_name="test_property_q7_120000_96000_14400",
    )


def _a_pa_q32() -> CheckItem:
    facts = _pa_facts(
        "2025_26",
        _person("self", salary="250000"),
        _person("spouse", properties=[_pa_property("p-1", "240000")]),
    )
    result = compare_personal_assessment(facts, BUNDLE)
    pa = _scenario(result, "personal_assessment")
    no_pa = _scenario(result, "no_pa")
    components = {c["tax_type"]: c["final_after_reduction"] for c in no_pa["components"]}
    return _make_item(
        name="PA Q32（2025/26）",
        input_summary="已婚两人：本人薪金 250,000；配偶物业租金 240,000（NAV 192,000）",
        expected="PA 9,920 vs 分开评税 28,800＋0（Q32，T1 台账 1.7）",
        checks=[
            ("PA before_reduction", pa["before_reduction"], "12920"),
            ("PA reduction", pa["reduction"], "3000"),
            ("PA final_after_reduction", pa["final_after_reduction"], "9920"),
            ("no_pa final_after_reduction", no_pa["final_after_reduction"], "28800"),
            ("no_pa salaries 分项", components.get("salaries"), "0"),
            ("no_pa property 分项", components.get("property"), "28800"),
            ("best_scenario", result["best_scenario"], "personal_assessment"),
            ("amounts.final_after_reduction", result["amounts"]["final_after_reduction"], "9920"),
        ],
        actual=(
            f'PA：before={pa["before_reduction"]}、reduction={pa["reduction"]}、'
            f'final={pa["final_after_reduction"]}；no_pa：final={no_pa["final_after_reduction"]}'
            f'（salaries={components.get("salaries")}、property={components.get("property")}）；'
            f'best={result["best_scenario"]}'
        ),
        test_name="test_pa_q32_example_9920_vs_28800",
    )


def _a_pam37_ex4() -> CheckItem:
    facts = _pa_facts(
        "2025_26",
        _person(
            "self",
            businesses=[
                {
                    "business_id": "b-1",
                    "entity_kind": "sole_proprietorship",
                    "assessable_profit": "480000",
                    "share": "1/1",
                }
            ],
        ),
        _person("spouse", properties=[_pa_property("p-1", "150000")]),
    )
    result = compare_personal_assessment(facts, BUNDLE)
    pa = _scenario(result, "personal_assessment")
    return _make_item(
        name="PAM37 例 4",
        input_summary="已婚两人：本人独资 480,000；配偶租金 150,000（NAV 120,000）",
        expected=(
            "R 600,000；累进 39,120；宽减 3,000；final 36,120；内部比例 4/5、1/5"
            "（PAM37 例 4，T2 台账 1.13；个人整元不作产品输出）"
        ),
        checks=[
            ("before_reduction", pa["before_reduction"], "39120"),
            ("reduction", pa["reduction"], "3000"),
            ("final_after_reduction", pa["final_after_reduction"], "36120"),
            ("apportionment_ratios", pa["apportionment_ratios"], {"self": "4/5", "spouse": "1/5"}),
            ("amounts.final_after_reduction", result["amounts"]["final_after_reduction"], "36120"),
        ],
        actual=(
            f'PA：before={pa["before_reduction"]}、reduction={pa["reduction"]}、'
            f'final={pa["final_after_reduction"]}；'
            f'ratios={json.dumps(pa["apportionment_ratios"], ensure_ascii=False, sort_keys=True)}'
        ),
        test_name="test_pa_pam37_example4_joint_rebate_then_ratio_apportion_internal_fractions",
    )


def _a_pam37_not_covered(label: str) -> CheckItem:
    return CheckItem(
        name=f"PAM37 {label}",
        input_summary="（未提供）",
        expected="（测试库未覆盖 PAM37 例 1/2/3，不虚构输入与期望值）",
        actual="（未运行）",
        test_name="（未覆盖）",
        passed=False,
        covered=False,
    )


def _a_partnership_q7() -> CheckItem:
    facts = _entity(
        "2025_26",
        "partnership",
        "5000000",
        partners=[
            {"partner_id": "p-corp", "partner_kind": "corporation", "share": "1/5"},
            {"partner_id": "p-ind-1", "partner_kind": "individual", "share": "2/5"},
            {"partner_id": "p-ind-2", "partner_kind": "individual", "share": "2/5"},
        ],
    )
    result = calculate_profits_tax(facts, BUNDLE)
    amounts = result["amounts"]
    allocation = {entry["partner_kind"]: entry for entry in result["two_tier_allocation"]}
    return _make_item(
        name="合伙 Q7（2025/26）",
        input_summary="混合合伙：利润 5,000,000；法团 1/5＋个人 2/5＋2/5",
        expected=(
            "业务层税额 612,000（33,000＋120,000＋99,000＋360,000）；宽减 3,000 → "
            "609,000；门槛按 partner_kind 聚合（T2 2trexample Q7 结构例）"
        ),
        checks=[
            ("before_reduction", amounts["before_reduction"], "612000"),
            ("reduction", amounts["reduction"], "3000"),
            ("final_after_reduction", amounts["final_after_reduction"], "609000"),
            ("corp threshold", allocation["corporation"]["threshold"], "400000"),
            ("corp low_tier_tax", allocation["corporation"]["low_tier_tax"], "33000"),
            ("corp high_tier_tax", allocation["corporation"]["high_tier_tax"], "99000"),
            ("individual threshold", allocation["individual"]["threshold"], "1600000"),
            ("individual low_tier_tax", allocation["individual"]["low_tier_tax"], "120000"),
            ("individual high_tier_tax", allocation["individual"]["high_tier_tax"], "360000"),
        ],
        actual=(
            f'before={amounts["before_reduction"]}、reduction={amounts["reduction"]}、'
            f'final={amounts["final_after_reduction"]}；'
            f'corp：threshold={allocation["corporation"]["threshold"]}、'
            f'low={allocation["corporation"]["low_tier_tax"]}、high={allocation["corporation"]["high_tier_tax"]}；'
            f'individual：threshold={allocation["individual"]["threshold"]}、'
            f'low={allocation["individual"]["low_tier_tax"]}、high={allocation["individual"]["high_tier_tax"]}'
        ),
        test_name="test_partnership_q7_612000",
    )


def _a_partnership_q8() -> CheckItem:
    partners = [
        {"partner_id": f"p-{i}", "partner_kind": "individual", "share": "1/5"}
        for i in range(5)
    ]
    allocations = [
        {"partner_id": f"p-{i}", "business_id": "biz-1", "assessable_amount": "560000"}
        for i in range(5)
    ]
    facts = _entity(
        "2026_27",
        "partnership",
        "3500000",
        partners=partners,
        loss_brought_forward="700000",
        partner_assessable_allocations=allocations,
        pa_election_partners=["p-0"],
    )
    result = calculate_profits_tax(facts, BUNDLE)
    amounts = result["amounts"]
    return _make_item(
        name="合伙 Q8（2026/27）",
        input_summary="合伙：利润 3,500,000、承前亏损 700,000、五名个人各 1/5、PA 选择 p-0",
        expected=(
            "抵亏后 2,800,000 → 业务税 270,000；PA 相关税 54,000；余额 216,000；"
            "暂缴按抵亏前 375,000（T2 2trexample Q8 结构例）"
        ),
        checks=[
            ("before_reduction", amounts["before_reduction"], "270000"),
            ("reduction", amounts["reduction"], "0"),
            ("final_after_reduction", amounts["final_after_reduction"], "270000"),
            ("pa_related_tax", result["pa_related_tax"], "54000"),
            ("remaining_business_tax", result["remaining_business_tax"], "216000"),
            ("next_provisional", amounts["next_provisional"], "375000"),
        ],
        actual=(
            f'before={amounts["before_reduction"]}、reduction={amounts["reduction"]}、'
            f'final={amounts["final_after_reduction"]}；PA 相关税={result["pa_related_tax"]}、'
            f'余额={result["remaining_business_tax"]}；next_provisional={amounts["next_provisional"]}'
        ),
        test_name="test_partnership_q8_loss_offset_then_pa_transfer_270000_54000_216000",
    )


def _a_lease_150() -> CheckItem:
    facts = _lease(
        term_start="2025-07-01",
        term_end="2025-12-31",
        rent_input_mode="total_rent",
        total_rent="60000",
        monthly_rent=None,
    )
    result = calculate_lease_stamp_duty(facts, BUNDLE)
    breakdown = result["duty_breakdown"]
    return _make_item(
        name="租约 ≤1 年：60,000→150",
        input_summary="fixed 2025-07-01..2025-12-31、total_rent 60,000、premium 0",
        expected=(
            "≤1 年档 0.25%×总租金 60,000＝150（T1 档位＋ceil100/ceil1，"
            "T1-observed 周年日；T3 手算）"
        ),
        checks=[
            ("total", breakdown["total"], "150"),
            ("rounded_rent_base", breakdown["rounded_rent_base"], "60000"),
            ("rent_duty", breakdown["rent_duty"], "150"),
        ],
        actual=(
            f'total={breakdown["total"]}；rounded_rent_base={breakdown["rounded_rent_base"]}；'
            f'rent_duty={breakdown["rent_duty"]}；premium_duty={breakdown["premium_duty"]}'
        ),
        test_name="test_lease_six_months_uses_total_not_annual_rent",
    )


def _a_lease_630() -> CheckItem:
    facts = _lease(
        term_start="2025-07-01",
        term_end="2027-06-30",
        rent_input_mode="total_rent",
        total_rent="252000",
        monthly_rent=None,
    )
    result = calculate_lease_stamp_duty(facts, BUNDLE)
    breakdown = result["duty_breakdown"]
    return _make_item(
        name="租约 >1 年年化：252,000→630",
        input_summary="fixed 2025-07-01..2027-06-30（恰 2 年）、total_rent 252,000",
        expected=(
            "平均年租 126,000（round4）→ >1–3 年档 0.5% → 630"
            "（T1-observed 年化；T3 手算）"
        ),
        checks=[
            ("total", breakdown["total"], "630"),
            ("rounded_rent_base", breakdown["rounded_rent_base"], "126000"),
        ],
        actual=(
            f'total={breakdown["total"]}；rounded_rent_base={breakdown["rounded_rent_base"]}；'
            f'rent_duty={breakdown["rent_duty"]}'
        ),
        test_name="test_lease_two_year_variable_rent_uses_average",
    )


def _a_lease_300() -> CheckItem:
    facts = _lease(
        term_kind="indefinite",
        term_end=None,
        rent_input_mode="annual_rent",
        monthly_rent=None,
        annual_rent="120000",
    )
    result = calculate_lease_stamp_duty(facts, BUNDLE)
    breakdown = result["duty_breakdown"]
    return _make_item(
        name="租约 indefinite：120,000→300",
        input_summary="indefinite、annual_rent 120,000、无 term_end",
        expected="未界定期限档 0.25%×年租 120,000＝300（T1 档位＝有效档非缺失；T3 手算）",
        checks=[
            ("total", breakdown["total"], "300"),
            ("rounded_rent_base", breakdown["rounded_rent_base"], "120000"),
        ],
        actual=(
            f'total={breakdown["total"]}；rounded_rent_base={breakdown["rounded_rent_base"]}；'
            f'rent_duty={breakdown["rent_duty"]}'
        ),
        test_name="test_lease_indefinite_is_valid_not_missing_term",
    )


def _a_lease_1200() -> CheckItem:
    facts = _lease(term_start="2025-07-01", term_end="2028-07-01")
    result = calculate_lease_stamp_duty(facts, BUNDLE)
    breakdown = result["duty_breakdown"]
    return _make_item(
        name="租约 3 年+1 天：120,000→1,200",
        input_summary="fixed 2025-07-01..2028-07-01（3 年 1 天，起讫两日都算）、月租 10,000",
        expected="3 年+1 天＝超过 3 年档 1%×年租 120,000＝1,200（T1 档位；T3 手算）",
        checks=[
            ("total", breakdown["total"], "1200"),
            ("rounded_rent_base", breakdown["rounded_rent_base"], "120000"),
        ],
        actual=(
            f'total={breakdown["total"]}；rounded_rent_base={breakdown["rounded_rent_base"]}；'
            f'rent_duty={breakdown["rent_duty"]}'
        ),
        test_name="test_lease_exact_one_and_three_year_boundaries",
    )


def _avd_item(
    name: str,
    facts: dict,
    expected_duty: str,
    *,
    input_summary: str,
    expected: str,
    test_name: str,
    expected_base: str | None = None,
) -> CheckItem:
    result = calculate_property_avd(facts, BUNDLE)
    detail = result["instrument_detail"]
    amounts = result["amounts"]
    checks: list[tuple[str, object, object]] = [
        ("duty", detail["duty"], expected_duty),
        ("before_reduction", amounts["before_reduction"], expected_duty),
        ("final_after_reduction", amounts["final_after_reduction"], expected_duty),
    ]
    if expected_base is not None:
        checks.append(("base_amount", detail["base_amount"], expected_base))
    return _make_item(
        name=name,
        input_summary=input_summary,
        expected=expected,
        checks=checks,
        actual=(
            f'base_amount={detail["base_amount"]}；duty={detail["duty"]}；'
            f'status={result["status"]}'
        ),
        test_name=test_name,
    )


def _a_avd_101_table8() -> CheckItem:
    return _avd_item(
        "AVD 3,000,000.01→101",
        _avd("3000000.01", date="2024-06-10"),
        "101",
        input_summary="B=3,000,000.01、2024-06-10（Table 8）",
        expected="(3m,3,528,240] 档 100＋10%×0.01=100.001 → 税额 ceil1=101（T3 手算；表值 T1）",
        test_name="test_avd_table8_historical_window_2024",
    )


def _a_avd_52924() -> CheckItem:
    return _avd_item(
        "AVD 3,528,240→52,924",
        _avd("3528240", date="2024-06-10"),
        "52924",
        input_summary="B=3,528,240、2024-06-10（Table 8 桥档上端）",
        expected="100＋10%×(3,528,240−3,000,000)=52,924（T3 手算；表值 T1）",
        test_name="test_avd_table8_historical_window_2024",
    )


def _a_avd_64856() -> CheckItem:
    return _avd_item(
        "AVD 4,323,780→64,856",
        _avd("4323780", date="2025-06-10"),
        "64856",
        input_summary="B=4,323,780、2025-06-10（Table 9 桥档上端）",
        expected="100＋20%×(4,323,780−4,000,000)=64,856（T3 手算；表值 T1）",
        test_name="test_avd_table9_2025_window",
    )


def _a_avd_64857() -> CheckItem:
    return _avd_item(
        "AVD 4,323,780.01→64,857",
        _avd("4323780.01", date="2025-06-10"),
        "64857",
        input_summary="B=4,323,780.01、2025-06-10（Table 9 换档点，下开上闭）",
        expected="1.5%×4,323,780.01=64,856.70015 → ceil1=64,857（区间定义，非矛盾；T3 手算）",
        test_name="test_avd_table9_2025_window",
    )


def _a_avd_4250000() -> CheckItem:
    return _avd_item(
        "AVD 100,000,000→4,250,000",
        _avd("100000000", date="2026-06-10", property_class="residential"),
        "4250000",
        input_summary="B=100,000,000、2026-06-10、residential（2026 住宅高端档上端）",
        expected="(21,739,120,100m] 档 4.25%×1e8=4,250,000（T3 手算；Ord 3/2026 表值 T1）",
        test_name="test_avd_2026_residential_high_bands",
    )


def _a_avd_7122341() -> CheckItem:
    return _avd_item(
        "AVD 109,574,470→7,122,341",
        _avd("109574470", date="2026-06-10", property_class="residential"),
        "7122341",
        input_summary="B=109,574,470、2026-06-10、residential（30% 档上端）",
        expected="4,250,000＋30%×9,574,470=7,122,341（T3 手算；Ord 3/2026 表值 T1）",
        test_name="test_avd_2026_residential_high_bands",
    )


def _a_avd_base_not_rounded() -> CheckItem:
    return _avd_item(
        "AVD 基数不取整至 100（4,000,000.01→101）",
        _avd(
            "4000000.01",
            date="2025-06-10",
            consideration="4000000.01",
            value="4000000",
        ),
        "101",
        input_summary="B=max(4,000,000.01, 4,000,000)=4,000,000.01、2025-06-10",
        expected="100＋20%×0.01=100.002 → ceil1=101；基数不进位至 100（D1.2；T3 手算）",
        test_name="test_avd_property_base_not_rounded_to_100",
        expected_base="4000000.01",
    )


def _a_stock_4() -> CheckItem:
    result = calculate_stock_stamp_duty(
        _stock(
            {"doc_kind": "contract_note_sold", "consideration": "1000.01"},
            {"doc_kind": "contract_note_bought", "consideration": "1000.01"},
        ),
        BUNDLE,
    )
    per_doc = {entry["doc_kind"]: entry["duty"] for entry in result["documents"]}
    total = result["amounts"]["before_reduction"]
    return _make_item(
        name="股票 每 note 分别 ceil1→4",
        input_summary="sold note 1,000.01＋bought note 1,000.01、complete=true",
        expected=(
            "每份 0.1%=1.00001 → 各 ceil1=2；sold 2＋bought 2=4"
            "（不合并、不互摊；T1 head 2＋T3 手算）"
        ),
        checks=[
            ("total", total, "4"),
            ("sold duty", per_doc.get("contract_note_sold"), "2"),
            ("bought duty", per_doc.get("contract_note_bought"), "2"),
        ],
        actual=f'total={total}；documents={json.dumps(result["documents"], ensure_ascii=False)}',
        test_name="test_stock_sold_and_bought_notes_ceil_separately",
    )


def _a_stock_8() -> CheckItem:
    result = calculate_stock_stamp_duty(
        _stock({"doc_kind": "voluntary_inter_vivos", "value": "1000.01"}),
        BUNDLE,
    )
    total = result["amounts"]["before_reduction"]
    return _make_item(
        name="股票 自愿处置→8",
        input_summary="voluntary_inter_vivos、value 1,000.01",
        expected="$5＋0.2%×1,000.01=7.00002 → 一份文书一次 ceil1=8（T1 head 2；T3 手算）",
        checks=[("total", total, "8"), ("doc duty", result["documents"][0]["duty"], "8")],
        actual=f'total={total}；documents={json.dumps(result["documents"], ensure_ascii=False)}',
        test_name="test_stock_voluntary_transfer_fixed_plus_value_duty",
    )


def _a_stock_5() -> CheckItem:
    result = calculate_stock_stamp_duty(
        _stock({"doc_kind": "other_transfer", "consideration": "1000000"}),
        BUNDLE,
    )
    total = result["amounts"]["before_reduction"]
    return _make_item(
        name="股票 其他转让→5",
        input_summary="other_transfer、consideration 1,000,000（有代价也不套从价率）",
        expected="$5 每份（不得替代一般买卖从价税；T1 head 2；T3 手算）",
        checks=[("total", total, "5"), ("doc duty", result["documents"][0]["duty"], "5")],
        actual=f'total={total}；documents={json.dumps(result["documents"], ensure_ascii=False)}',
        test_name="test_stock_other_transfer_is_5_per_document",
    )


def collect_batch_a() -> list[CheckItem]:
    return [
        _guard("PAM39 Q1（2025/26）", _a_pam39_q1),
        _guard("PAM39 Q2 PST（2026/27）", _a_pam39_q2),
        _guard("物业 Q7（2025/26）", _a_property_q7),
        _guard("PA Q32（2025/26）", _a_pa_q32),
        _guard("PAM37 例 4", _a_pam37_ex4),
        _a_pam37_not_covered("例 1"),
        _a_pam37_not_covered("例 2"),
        _a_pam37_not_covered("例 3"),
        _guard("合伙 Q7（2025/26）", _a_partnership_q7),
        _guard("合伙 Q8（2026/27）", _a_partnership_q8),
        _guard("租约 ≤1 年：60,000→150", _a_lease_150),
        _guard("租约 >1 年年化：252,000→630", _a_lease_630),
        _guard("租约 indefinite：120,000→300", _a_lease_300),
        _guard("租约 3 年+1 天：120,000→1,200", _a_lease_1200),
        _guard("AVD 3,000,000.01→101", _a_avd_101_table8),
        _guard("AVD 3,528,240→52,924", _a_avd_52924),
        _guard("AVD 4,323,780→64,856", _a_avd_64856),
        _guard("AVD 4,323,780.01→64,857", _a_avd_64857),
        _guard("AVD 100,000,000→4,250,000", _a_avd_4250000),
        _guard("AVD 109,574,470→7,122,341", _a_avd_7122341),
        _guard("AVD 基数不取整（4,000,000.01→101）", _a_avd_base_not_rounded),
        _guard("股票 每 note 分别 ceil1→4", _a_stock_4),
        _guard("股票 自愿处置→8", _a_stock_8),
        _guard("股票 其他转让→5", _a_stock_5),
    ]


# ------------------------------------------------------------------ 批次 B：模拟数据
def _sim_item(engine: str, facts: Mapping, result: Mapping) -> SimItem:
    payload = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return SimItem(
        engine=engine,
        input_json=_truncate(payload),
        status=str(result.get("status")),
        key_amounts=_fmt_amounts(result.get("amounts") or {}),
    )


def collect_batch_b() -> list[SimItem]:
    """固定 seed 模拟数据（仅演示 I/O；不构成合规样例）。"""
    random.seed(20261013)
    years = ("2024_25", "2025_26", "2026_27")
    items: list[SimItem] = []

    # 薪俸税 8 条（部分含显式已缴暂缴 → complete；其余 → partial）
    for _ in range(8):
        facts = _single(
            random.choice(years),
            str(random.randrange(120_000, 2_500_001, 1000)),
            str(random.randrange(0, 21_001, 100)),
        )
        if random.random() < 0.5:
            facts["provisional_paid"] = str(random.randrange(0, 60_001, 500))
        result = calculate_salaries_tax(facts, BUNDLE)
        items.append(_sim_item("薪俸税（salaries_tax）", facts, result))

    # 利得税 6 条（2/3 独资、1/3 法团；两级制资格已确认）
    for index in range(6):
        kind = "corporation" if index % 3 == 2 else "sole_proprietorship"
        facts = _entity(
            random.choice(years),
            kind,
            str(random.randrange(100_000, 5_000_001, 1000)),
        )
        result = calculate_profits_tax(facts, BUNDLE)
        items.append(_sim_item("利得税（profits_tax）", facts, result))

    # 物业税 6 条（每例 1–3 项物业，差饷 0）
    for _ in range(6):
        count = random.randint(1, 3)
        props = [
            _property(f"sim-{index}", str(random.randrange(60_000, 720_001, 1000)))
            for index in range(count)
        ]
        facts = _property_facts(random.choice(years), *props)
        result = calculate_property_tax(facts, BUNDLE)
        items.append(_sim_item("物业税（property_tax）", facts, result))

    return items


# ------------------------------------------------------------------ 批次 C：边界与三态
def _c_unincorporated_2000000_01() -> CheckItem:
    result = calculate_profits_tax(
        _entity("2026_27", "sole_proprietorship", "2000000.01"), BUNDLE
    )
    amounts = result["amounts"]
    pending_text = json.dumps(result.get("pending_verification", []), ensure_ascii=False)
    return _make_item(
        name="C1 独资净利 2,000,000.01（低档全率）",
        input_summary="profits：sole_proprietorship、assessable_profit 2,000,000.01、2026/27",
        expected=(
            "择档 floor(2,000,000.01)=2,000,000 ≤ 2m → 低档全率 150,000；"
            "final 150,000；非法团非产品约定（D6.2a 计算器观察）"
        ),
        checks=[
            ("before_reduction", amounts["before_reduction"], "150000"),
            ("reduction", amounts["reduction"], "0"),
            ("final_after_reduction", amounts["final_after_reduction"], "150000"),
            ("无产品约定标识", "产品约定" not in pending_text, True),
        ],
        actual=(
            f'before={amounts["before_reduction"]}、reduction={amounts["reduction"]}、'
            f'final={amounts["final_after_reduction"]}；pending_verification 无「产品约定」标识'
        ),
        test_name="test_profits_unincorporated_final_floor_three_year_calculator_convention",
    )


def _c_property_two_17s() -> CheckItem:
    result = calculate_property_tax(
        _property_facts("2025_26", _property("p-a", "17"), _property("p-b", "17")),
        BUNDLE,
    )
    taxes = [entry["tax"] for entry in result["properties"]]
    before = result["amounts"]["before_reduction"]
    return _make_item(
        name="C2 物业两笔 AV 各 17（逐物业 vs 聚合）",
        input_summary="property：p-a 与 p-b 各 17（NAV raw 13.6）",
        expected=(
            "逐物业 floor(13.6)=13 → 税 floor(1.95)=1；合计 2；"
            "聚合一次取整反例 4 不得采用（D5.0）"
        ),
        checks=[
            ("per-property taxes", taxes, ["1", "1"]),
            ("before_reduction", before, "2"),
            ("≠ 聚合反例", before != "4", True),
        ],
        actual=f'properties.tax={json.dumps(taxes)}；before={before}（聚合反例 4 未采用）',
        test_name="test_property_aggregate_vs_per_property_rounding_not_conflated",
    )


def _c_missing_provisional() -> CheckItem:
    facts = _pa_facts(
        "2025_26",
        _person("self", salary="250000"),
        _person("spouse", properties=[_pa_property("p-1", "240000")]),
    )
    del facts["provisional_paid"]
    result = compare_personal_assessment(facts, BUNDLE)
    amounts = result["amounts"]
    return _make_item(
        name="C3 缺 provisional_paid（partial / balance null）",
        input_summary="PA：Q32 同款事实但删除 provisional_paid 键（缺失 ≠ 显式零）",
        expected="status=partial；已知 final 9,920；provisional_paid/balance=null；missing_components 含 provisional_paid（REQ-8）",
        checks=[
            ("status", result["status"], "partial"),
            ("final_after_reduction", amounts["final_after_reduction"], "9920"),
            ("provisional_paid", amounts["provisional_paid"], None),
            ("balance", amounts["balance"], None),
            ("missing provisional_paid", "provisional_paid" in result.get("missing_components", []), True),
        ],
        actual=(
            f'status={result["status"]}；'
            + _fmt_amounts(
                amounts,
                ("final_after_reduction", "provisional_paid", "balance"),
            )
            + f'；missing_components={json.dumps(result.get("missing_components"), ensure_ascii=False)}'
        ),
        test_name="test_missing_pst_partial_no_total_balance",
    )


def _c_unsupported_deduction() -> CheckItem:
    property_code = _expect_error_code(
        lambda: calculate_property_tax(
            _property_facts(
                "2025_26",
                _property(
                    "prop-1",
                    "120000",
                    deduction_items=[{"item_type": "ground_rent", "amount": "5000"}],
                ),
            ),
            BUNDLE,
        )
    )
    salaries_facts = _single("2025_26", "240000", "9000")
    salaries_facts["deduction_items"] = [
        {"item_type": "voluntary_mpf_contribution", "amount": "5000"}
    ]
    salaries_code = _expect_error_code(
        lambda: calculate_salaries_tax(salaries_facts, BUNDLE)
    )
    return _make_item(
        name="C4 不支持扣除正数（E_DEDUCTION_UNSUPPORTED）",
        input_summary="物业 ground_rent 5,000；薪俸 voluntary_mpf_contribution 5,000",
        expected="两引擎均抛 AppError(code=E_DEDUCTION_UNSUPPORTED)，不得静默忽略（REQ-10）",
        checks=[
            ("物业引擎", property_code, "E_DEDUCTION_UNSUPPORTED"),
            ("薪俸引擎", salaries_code, "E_DEDUCTION_UNSUPPORTED"),
        ],
        actual=f"物业引擎：{property_code}；薪俸引擎：{salaries_code}",
        test_name=(
            "test_property_rejects_ground_rent_repairs_insurance_interest, "
            "test_acceptance_unsupported_deduction_positive_rejected"
        ),
    )


def _c_unknown_eligibility() -> CheckItem:
    facts = _single("2025_26", "240000", "9000")
    facts["married_status"] = {"value": "unknown"}
    result = calculate_salaries_tax(facts, BUNDLE)
    amounts = result["amounts"]
    all_null = all(value is None for value in amounts.values())
    return _make_item(
        name="C5 资格 unknown（needs_input）",
        input_summary="薪俸：married_status.value=unknown（240,000／MPF 9,000）",
        expected="status=needs_input；中文 questions 非空；amounts 六键一律显式 None（不填零、不部分计算）",
        checks=[
            ("status", result["status"], "needs_input"),
            ("questions 非空", bool(result.get("questions")), True),
            ("六键全 None", all_null, True),
        ],
        actual=(
            f'status={result["status"]}；questions={len(result.get("questions", []))} 条；'
            f'amounts={"全 None" if all_null else result["amounts"]}'
        ),
        test_name="test_salaries_unknown_eligibility_questions",
    )


def _c_stamp_window() -> CheckItem:
    code = _expect_error_code(
        lambda: calculate_property_avd(
            _avd("5000000", date="2026-10-08"), BUNDLE
        )
    )
    return _make_item(
        name="C6 印花税文书日期超窗（E_PERIOD_NOT_SUPPORTED）",
        input_summary="AVD：instrument_date 2026-10-08（窗口至 2026-10-07）",
        expected=(
            "AppError(code=E_PERIOD_NOT_SUPPORTED)（bundle.applicability."
            "stamp_duty_instrument_window；无直接引擎测试，本脚本实跑核验）"
        ),
        checks=[("error code", code, "E_PERIOD_NOT_SUPPORTED")],
        actual=f"calculate_property_avd → {code}",
        test_name="（无直接引擎测试）",
    )


def _c_tax_type_wrong_shape() -> CheckItem:
    code = _expect_error_code(
        lambda: validate_prepare_body(
            {"tax_type": [], "schema_version": "1.0.0", "input": {}}
        )
    )
    return _make_item(
        name="C7 tax_type=[]（API 层结构化拒绝）",
        input_summary='prepare 请求体 tax_type=[]（非法 JSON 类型；引擎层不适用——引擎契约输入为 facts Mapping，无 tax_type 字段）',
        expected=(
            "API 层结构化 422 E_INPUT_TYPE（field=tax_type），不得未处理 TypeError；"
            "由 tests/acceptance 保障"
        ),
        checks=[("error code", code, "E_INPUT_TYPE")],
        actual=f"validate_prepare_body → {code}（引擎层不适用：由 API 层保障）",
        test_name="test_acceptance_tax_type_wrong_shape_structured_rejection",
    )


def collect_batch_c() -> list[CheckItem]:
    return [
        _guard("C1 独资净利 2,000,000.01（低档全率）", _c_unincorporated_2000000_01),
        _guard("C2 物业两笔 AV 各 17（逐物业 vs 聚合）", _c_property_two_17s),
        _guard("C3 缺 provisional_paid（partial / balance null）", _c_missing_provisional),
        _guard("C4 不支持扣除正数（E_DEDUCTION_UNSUPPORTED）", _c_unsupported_deduction),
        _guard("C5 资格 unknown（needs_input）", _c_unknown_eligibility),
        _guard("C6 印花税文书日期超窗（E_PERIOD_NOT_SUPPORTED）", _c_stamp_window),
        _guard("C7 tax_type=[]（API 层结构化拒绝）", _c_tax_type_wrong_shape),
    ]


# ------------------------------------------------------------------ 测试名核验
def _collect_test_names() -> set[str]:
    names: set[str] = set()
    tests_dir = ROOT / "tests"
    if not tests_dir.is_dir():
        return names
    for path in tests_dir.rglob("*.py"):
        if not path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"^def (test_[0-9A-Za-z_]+)", text, re.MULTILINE))
    return names


def _validate_test_names(*batches: Sequence[CheckItem]) -> None:
    """声明的测试名必须存在于 tests/——不存在的声明＝对照失败（不夸大）。"""
    known = _collect_test_names()
    for batch in batches:
        for item in batch:
            for name in re.split(r"[,，]", item.test_name):
                name = name.strip()
                if name.startswith("test_") and name not in known:
                    item.passed = False
                    item.actual += f"；声明的测试名不存在：{name}"


# ------------------------------------------------------------------ 报告装配
def _md_cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join([" --- "] * len(headers)) + "|",
    ]
    lines.extend(
        "| " + " | ".join(_md_cell(cell) for cell in row) + " |" for row in rows
    )
    return lines


def _generation_command() -> str:
    try:
        exe = Path(sys.executable).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        exe = sys.executable
    try:
        script = Path(__file__).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        script = str(Path(__file__).name)
    parts = [exe, script, *sys.argv[1:]]
    return " ".join(part for part in parts if part)


def build_report(
    batch_a: Sequence[CheckItem],
    batch_b: Sequence[SimItem],
    batch_c: Sequence[CheckItem],
) -> str:
    lines: list[str] = []
    lines.append("# 引擎 vs 官方示例 I/O 测试报告（001）")
    lines.append("")
    lines.append(f"- 报告日期：{REPORT_DATE}")
    lines.append(f"- 生成命令：`{_generation_command()}`")
    try:
        exe_display = Path(sys.executable).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        exe_display = sys.executable
    lines.append(
        f"- 运行时环境：Python {platform.python_version()}（`{exe_display}`；"
        f"{platform.system()} {platform.release()}）"
    )
    lines.append(
        "- 规则束：`app.rules.bundle.INITIAL_BUNDLE_CONTENT`（只读；"
        f"rules_schema_version={BUNDLE['rules_schema_version']}；"
        f"适用年度={'、'.join(BUNDLE['applicability']['years'])}；"
        "印花税文书窗口="
        f"{BUNDLE['applicability']['stamp_duty_instrument_window']['from']}.."
        f"{BUNDLE['applicability']['stamp_duty_instrument_window']['to']}）"
    )
    lines.append(
        "- 运行性质：本地只读、不联网；表中「引擎实际输出」均为本脚本本次真实运行结果；"
        "输入形状逐字段照抄 tests/engines/** 与 tests/acceptance/** 既有用例语义。"
    )
    lines.append("")

    lines.append("## 批次 A：官方示例对照")
    lines.append("")
    lines.append(
        "口径说明：官方期望值列附出处锚（台账条目／官方文件／测试注释手算）；"
        "「未覆盖」＝测试库无对应用例锚，不虚构输入与期望值。"
    )
    lines.append("")
    lines.extend(
        _md_table(
            ["案例", "输入摘要", "官方期望值（出处锚）", "引擎实际输出（步骤/金额）", "一致？", "对应测试名"],
            [
                [
                    item.name,
                    item.input_summary,
                    item.expected,
                    item.actual,
                    item.outcome,
                    item.test_name,
                ]
                for item in batch_a
            ],
        )
    )
    lines.append("")

    lines.append("## 批次 B：模拟数据（固定 seed）")
    lines.append("")
    lines.append(
        "> ⚠️ **模拟数据**：以下 20 条由 `random.seed(20261013)` 生成，仅演示引擎 "
        "I/O 与状态/金额字段形态，**不构成任何合规或官方示例样例**；不得作为计算正确性证据引用。"
    )
    lines.append("")
    lines.extend(
        _md_table(
            ["引擎", "输入 JSON（截断）", "状态", "关键金额"],
            [
                [item.engine, f"`{item.input_json}`", item.status, item.key_amounts]
                for item in batch_b
            ],
        )
    )
    lines.append("")

    lines.append("## 批次 C：边界与三态")
    lines.append("")
    lines.extend(
        _md_table(
            ["检查项", "输入摘要", "期望行为（依据）", "引擎实际输出", "一致？", "对应测试名"],
            [
                [
                    item.name,
                    item.input_summary,
                    item.expected,
                    item.actual,
                    item.outcome,
                    item.test_name,
                ]
                for item in batch_c
            ],
        )
    )
    lines.append("")

    all_items = list(batch_a) + list(batch_c)
    covered = [item for item in all_items if item.covered]
    consistent = [item for item in covered if item.passed]
    failures = [item for item in covered if not item.passed]
    not_covered = [item for item in all_items if not item.covered]
    exit_code = 1 if failures else 0

    lines.append("## 批次 D：汇总")
    lines.append("")
    lines.append(
        f"- 对照项合计：{len(all_items)}（批次 A {len(batch_a)}＋批次 C {len(batch_c)}；"
        f"批次 B {len(batch_b)} 条模拟数据不计入对照）"
    )
    lines.append(f"- 一致：{len(consistent)}")
    lines.append(f"- 不一致：{len(failures)}")
    lines.append(f"- 未覆盖：{len(not_covered)}")
    lines.append(
        f"- 脚本退出码：{exit_code}（无不一致 → 0；任一不一致 → 1）"
    )
    if not_covered:
        lines.append("")
        lines.append("未覆盖清单（如实标注，不得虚构）：")
        for item in not_covered:
            lines.append(f"- {item.name}：{item.expected}")
    if failures:
        lines.append("")
        lines.append("不一致清单：")
        for item in failures:
            lines.append(f"- {item.name}：{item.actual}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    batch_a = collect_batch_a()
    batch_b = collect_batch_b()
    batch_c = collect_batch_c()
    _validate_test_names(batch_a, batch_c)

    report = build_report(batch_a, batch_b, batch_c)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(report, encoding="utf-8", newline="\n")

    all_items = list(batch_a) + list(batch_c)
    covered = [item for item in all_items if item.covered]
    failures = [item for item in covered if not item.passed]
    not_covered = [item for item in all_items if not item.covered]
    print(
        f"报告已写入 {OUTPUT_PATH.relative_to(ROOT).as_posix()}："
        f"一致 {len(covered) - len(failures)}／不一致 {len(failures)}／"
        f"未覆盖 {len(not_covered)}"
    )
    if failures:
        print("FAIL：存在不一致对照项：", file=sys.stderr)
        for item in failures:
            print(f"  - {item.name}：{item.actual}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
