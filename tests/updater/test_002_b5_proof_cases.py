"""P3-W1a B5：八条不受影响路径的 proof-controlled case 登记（006 §7.1）。

REQ: REQ-12/13（005 §15.4/§15.6；006:295–309）——每行固定 case_id/
subcase_ids/input proof/required read path/source refs/worksheet/stage 期望；
历史数值只是 baseline 示例，变化参数须由独立已核参数胶囊构造子 case，跨过
新阈值/cap/取整边界；缺 legal in-scope witness → coverage_gap/quarantine，
不得伪造 fixture。

【拟名】被测契约: `app.updater.reference.B5_CASES: dict[case_id, plan]`，
plan 形状：
  {"case_id", "path", "mode":"candidate", "coverage_gap": bool,
   "subcase_ids":[str], "subcases":[{"subcase_id","input","expected"}],
   "parameter_evidence": dict|None, "required_read_paths":[str],
   "source_refs":[str]}
`AFFECTED_CASES` 的 path→case_id 与 006 八行一致。

Red 失败原因（当前）：无 B5_CASES/AFFECTED_CASES。
"""

from __future__ import annotations


def _plan(path: str) -> dict:
    from app.updater.reference import AFFECTED_CASES, B5_CASES

    case_id = AFFECTED_CASES[path]
    assert case_id in B5_CASES, f"B5_CASES 缺 {case_id!r}"
    plan = B5_CASES[case_id]
    assert plan["case_id"] == case_id
    assert plan["path"] == path
    assert plan.get("mode") == "candidate"
    assert plan.get("required_read_paths"), "每 case 须登记 required read paths"
    assert plan.get("source_refs"), "每 case 须登记独立 source refs"
    return plan


def test_002_b5_avd_related_fixed() -> None:
    plan = _plan("stamps.property.related_conveyance.fixed_duty")
    assert plan["coverage_gap"] is False
    assert plan["subcase_ids"] == ["avd_related_stamped_fixed"]
    sub = plan["subcases"][0]
    assert sub["input"]["consideration"] == "5000000"
    assert sub["input"]["prior_agreement_date"] == "2025-03-01"
    assert sub["expected"]["fixed_duty"] == "100"
    assert plan["parameter_evidence"]["fullpath"] == (
        "stamps.property.related_conveyance.fixed_duty"
    )


def test_002_b5_stock_other_fixed() -> None:
    plan = _plan("stamps.stock.other_transfer_fixed")
    assert plan["coverage_gap"] is False
    assert plan["subcase_ids"] == ["stock_two_other_transfers"]
    sub = plan["subcases"][0]
    assert sub["input"]["instrument_date"] == "2025-06-10"
    assert sub["input"]["document_count"] == 2
    assert sub["expected"]["fixed_duty_total"] == "10"


def test_002_b5_lease_ceiling_boundary() -> None:
    plan = _plan("stamps.lease.rent_ceiling_unit")
    assert plan["coverage_gap"] is False
    assert plan["subcase_ids"] == [
        "lease_ceiling_10000",
        "lease_ceiling_10000_01",
        "lease_ceiling_10099_99",
        "lease_ceiling_10100",
    ]
    by_input = {
        str(sub["input"]["annual_rent"]): sub["expected"] for sub in plan["subcases"]
    }
    assert by_input["10000"]["rent_base"] == "10000"
    assert by_input["10000.01"]["rent_base"] == "10100"
    assert by_input["10099.99"]["rent_base"] == "10100"
    assert by_input["10100"]["rent_base"] == "10100"
    assert by_input["10000"]["tax"] == "25"
    assert by_input["10000.01"]["tax"] == "26"
    assert by_input["10099.99"]["tax"] == "26"
    assert by_input["10100"]["tax"] == "26"


def test_002_b5_stock_effective_boundary() -> None:
    plan = _plan("stamps.stock.effective_from")
    assert plan["coverage_gap"] is True, (
        "stock effective_from 无窗口内前后成功 witness → coverage_gap，"
        "不得移日期/伪造 fixture（006:302）"
    )
    assert plan["subcase_ids"] == []
    assert plan["subcases"] == []
    assert plan["parameter_evidence"] is None


def test_002_b5_property_rate() -> None:
    plan = _plan("property.years.2025_26.standard_rate")
    assert plan["coverage_gap"] is False
    sub = plan["subcases"][0]
    assert sub["input"]["rent_received"] == "120000"
    assert sub["expected"]["nav"] == "96000"
    assert sub["expected"]["tax"] == "14400"
    assert plan["parameter_evidence"]["value"] == "15/100"


def test_002_b5_property_repair_ratio() -> None:
    plan = _plan("property.repair_allowance_ratio")
    assert plan["coverage_gap"] is False
    rents = [str(sub["input"]["rent_received"]) for sub in plan["subcases"]]
    assert "120000" in rents and "120000.99" in rents
    for sub in plan["subcases"]:
        assert sub["expected"]["tax"] != "", "每子 case 须有独立阶段期望"


def test_002_b5_profits_threshold_boundary() -> None:
    plan = _plan("profits.two_tier_threshold")
    assert plan["coverage_gap"] is False
    profits = [str(sub["input"]["assessable_profit"]) for sub in plan["subcases"]]
    assert profits == ["1999999.99", "2000000", "2000000.01", "2000001"]
    for sub in plan["subcases"]:
        assert sub["expected"]["before"] != "", (
            f"profits threshold subcase {sub['subcase_id']!r} 缺 before 阶段期望"
        )
        assert sub["expected"]["final"] != "", (
            f"profits threshold subcase {sub['subcase_id']!r} 缺 final 阶段期望"
        )


def test_002_b5_profits_rebate_cap() -> None:
    plan = _plan("profits.reductions.2025_26.cap")
    assert plan["coverage_gap"] is False
    profits = [str(sub["input"]["assessable_profit"]) for sub in plan["subcases"]]
    assert profits == ["39999.99", "40000", "40020"]
    for sub in plan["subcases"]:
        assert sub["expected"]["reduction"] != ""
        assert sub["expected"]["final"] != ""


def _assert_subcase_shape(result: dict, plan: dict, *, expect_passed: bool) -> None:
    """B5 结果必须携带子 case input/expected/actual/执行收据（不得只回显）。"""
    entries = result.get("subcase_results")
    assert isinstance(entries, list), (
        f"B5 result must expose executed subcase receipts: {result!r}"
    )
    assert [entry.get("subcase_id") for entry in entries] == plan["subcase_ids"], (
        f"subcase ids must match B5_CASES: {entries!r}"
    )
    for entry, subcase in zip(entries, plan["subcases"]):
        assert entry.get("subcase_id") == subcase["subcase_id"]
        assert entry.get("input") == subcase["input"], (
            "B5 subcase input from B5_CASES must be the executed input: "
            f"{entry!r}"
        )
        assert entry.get("expected") == subcase["expected"], (
            "B5 subcase expected from B5_CASES must participate: " f"{entry!r}"
        )
        actual = entry.get("actual")
        assert isinstance(actual, dict), f"subcase actual must be a mapping: {entry!r}"
        if expect_passed:
            for key, expected_value in subcase["expected"].items():
                assert actual.get(key) == expected_value, (
                    f"compliant subcase {subcase['subcase_id']!r} actual[{key!r}] "
                    f"must equal expected {expected_value!r}: {entry!r}"
                )
        else:
            assert actual != entry.get("expected"), (
                "non-compliant candidate actual must be the engine execution "
                f"value, not disguised as expected: {entry!r}"
            )
            for key, expected_value in subcase["expected"].items():
                assert actual.get(key) != expected_value, (
                    f"non-compliant subcase {subcase['subcase_id']!r} "
                    f"actual[{key!r}] must differ from expected "
                    f"{expected_value!r}: {entry!r}"
                )
        assert entry.get("passed") is expect_passed, (
            f"subcase {subcase['subcase_id']!r} passed must be {expect_passed}: "
            f"{entry!r}"
        )
        receipt = entry.get("receipt")
        assert isinstance(receipt, dict) and receipt, (
            f"subcase {subcase['subcase_id']!r} must carry an execution receipt"
        )
        assert receipt.get("subcase_id") == subcase["subcase_id"]
        assert isinstance(receipt.get("engine"), str) and receipt["engine"], (
            "execution receipt must name the production tax engine: "
            f"{receipt!r}"
        )
        assert isinstance(receipt.get("input_sha256"), str) and receipt["input_sha256"], (
            "execution receipt must bind the executed subcase input hash: "
            f"{receipt!r}"
        )


def test_002_b5_reference_and_candidate_engine_execute_production_plan() -> None:
    """F5：B5 必须走生产 ReferenceHarness/CandidateEngine 执行分支。

    - harness 期望来自独立 proof fact（不返回常量 110）；
    - candidate engine 读取原样 candidate data（不替换 data 区）并执行
      B5_CASES 子 case（input/expected/actual/执行收据）；
    - static case 仍以完整发行 baseline 运行（不复用候选数据）。
    """
    from _fakes import FACT_AVD_110, avd_changed_bundle_content

    from app.updater.reference import B5_CASES, CandidateEngine, ReferenceHarness

    case_id = "a_avd_related_fixed"
    plan = B5_CASES[case_id]
    assert plan["subcase_ids"] == ["avd_related_stamped_fixed"]

    harness = ReferenceHarness(proof_facts=[FACT_AVD_110])
    expected = harness.run_case(case_id)
    assert expected["case_id"] == case_id
    assert expected["subcase_ids"] == plan["subcase_ids"]
    assert expected["parameter_evidence_ref"] == FACT_AVD_110["fact_id"]
    assert expected["output"] == FACT_AVD_110["value"], (
        "B5 expected output must be proof-controlled from the independent fact, "
        f"not a constant: {expected!r}"
    )
    assert expected["output"] != "100", "baseline value must not be reused as witness"

    expected_subcases = expected.get("subcase_results")
    assert isinstance(expected_subcases, list) and len(expected_subcases) == 1, (
        f"ReferenceHarness must expose proof-controlled subcase expectations: {expected!r}"
    )
    hsub = expected_subcases[0]
    assert hsub["subcase_id"] == plan["subcase_ids"][0]
    assert hsub["expected"] == {"fixed_duty": FACT_AVD_110["value"]}, (
        "harness subcase expected must derive from the independent proof fact: "
        f"{hsub!r}"
    )
    assert hsub.get("parameter_evidence_ref") == FACT_AVD_110["fact_id"]

    engine = CandidateEngine()
    actual = engine.run_case(case_id, avd_changed_bundle_content())
    assert actual["case_id"] == case_id
    assert actual["subcase_ids"] == plan["subcase_ids"]
    assert actual["stages"] == expected["stages"]
    assert actual["output"] == expected["output"] == FACT_AVD_110["value"]
    # Top-level witness is proof-controlled 110; the B5 plan's static subcase
    # expectation remains 100, so this candidate receipt is non-compliant.
    _assert_subcase_shape(actual, plan, expect_passed=False)

    compliant_candidate = avd_changed_bundle_content("100")
    compliant = engine.run_case(case_id, compliant_candidate)
    assert compliant["output"] == "100", (
        "compliant candidate 100 must execute to the declared expected 100: "
        f"{compliant!r}"
    )
    _assert_subcase_shape(compliant, plan, expect_passed=True)

    other_candidate = avd_changed_bundle_content("120")
    other_actual = engine.run_case(case_id, other_candidate)
    assert other_actual["output"] == "120", (
        "affected B5 engine must receive the original candidate data (not the "
        f"static baseline): {other_actual!r}"
    )
    other_subcases = other_actual.get("subcase_results")
    assert isinstance(other_subcases, list) and len(other_subcases) == 1
    other = other_subcases[0]
    assert other["expected"] == plan["subcases"][0]["expected"] == {"fixed_duty": "100"}
    assert other["actual"].get("fixed_duty") == "120", (
        f"candidate 120 must be executed by the production engine: {other!r}"
    )
    assert other["passed"] is False, (
        "audit probe: candidate 120 must be compared against the declared "
        f"subcase expected 100, not echoed as a pass: {other!r}"
    )
    assert other["receipt"]["subcase_id"] == plan["subcase_ids"][0]
    assert other["receipt"]["engine"]

    static = engine.run_case(
        "case_salaries_pam39_q1_2025_26", avd_changed_bundle_content("120")
    )
    assert static["stages"] == ["3940", "3000", "940"]
    assert static["output"] == "940", (
        "static regression must run against the full approved release baseline, "
        f"not candidate data: {static!r}"
    )


def test_002_b5_candidate_engine_executes_each_subcase_plan() -> None:
    """F5：每个 B5 子 case 必须实际执行税引擎并达到 B5_CASES 基线期望。"""
    from _fakes import initial_content

    from app.updater.reference import B5_CASES, CandidateEngine

    engine = CandidateEngine()
    for case_id, plan in B5_CASES.items():
        result = engine.run_case(case_id, initial_content())
        assert result.get("case_id") == case_id
        assert result.get("subcase_ids") == plan["subcase_ids"], (
            f"B5 {case_id!r} subcase ids must match plan: {result!r}"
        )
        if not plan["subcases"]:
            assert result.get("subcase_results") == [], (
                f"coverage-gap case {case_id!r} must not fabricate subcase results: "
                f"{result!r}"
            )
            continue
        _assert_subcase_shape(result, plan, expect_passed=True)


def test_002_b5_required_map_value_fallback_cannot_replace_proof(tmp_path) -> None:
    """#5：required-map 同值 fallback 只是辅助，不能替代 case 独立 proof/收据。"""
    from _fakes import (
        AVD_FULLPATH,
        AVD_MANIFEST,
        FACT_AVD_110,
        avd_changed_bundle_content,
        b5_engine_result,
        b5_harness_result,
        initial_content,
    )

    from app.updater.testgate import run_gate

    class _NoProofHarness:
        def run_case(self, case_id: str, **kwargs):
            # 非独立 fact_id：只是 case 身份标签，不能作为 proof 引用。
            return b5_harness_result(
                case_id,
                "110",
                evidence_ref="case_identity:a_avd_related_fixed",
            )

    class _Engine:
        def run_case(self, case_id: str, candidate_content, **kwargs):
            return b5_engine_result(case_id, "110")

    report = run_gate(
        candidate_content=avd_changed_bundle_content(),
        current_content=initial_content(),
        manifests=[AVD_MANIFEST],
        independent_facts=[FACT_AVD_110],
        required_cases=["a_avd_related_fixed"],
        harness=_NoProofHarness(),
        engine=_Engine(),
        changed_paths=[AVD_FULLPATH],
        required_case_map={AVD_FULLPATH: "a_avd_related_fixed"},
        approved_temp_root=str(tmp_path),
    )
    reason = str(report.get("quarantine_reason") or "")
    assert report["decision"] == "quarantine", (
        "required-map same-value fallback must not replace the candidate case's "
        f"independent parameter proof: {report!r}"
    )
    assert "evidence" in reason or "proof" in reason, (
        f"expected missing-proof rejection, actual reason={reason!r}"
    )
    assert "missing_parameter_evidence" in report["tests"]["fail_reasons"]
