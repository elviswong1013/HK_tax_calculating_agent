"""REQ-15/18 候选门禁：独立参考、必需 case、隔离运行环境（M5 Red；Annex C C8）。

REQ: 15/18（§9：在候选 bundle 上运行受影响＋回归测试；参考参数只来自已批准
独立 source/proof；门禁在隔离环境运行；空报告/缺必需 case 禁止发布）。
规格锚点:
  - Annex C C8.1：独立参考 harness 不与候选共享参数对象；两个引擎一致本身
    不构成证据（test_bad_candidate_parameters_rejected_even_if_engines_agree）。
  - Annex C C8.4：必需 case 全部执行＋每 case 参数证据＋逐阶段一致＋回归
    failed=0；缺任何必需 case、或报告为空/零通过 → 隔离，不得发布。
  - Annex C C8.6：发布前运行时验证报告结构（cases/regression/gate_environment/
    decision 等）。
  - Annex C C8.7：门禁在临时 store 执行——无网络、无用户 session、不写
    current pointer；发布只发生在门禁外、全验证通过后的单事务。
期望值来源: 结构性契约（门禁判定/隔离断言），无金额期望。

【拟名】被测契约（app/updater/testgate.run_gate，Green 阶段须按测试实现，
不得要求测试改写）:
  - run_gate(*, candidate_content, current_content, manifests,
             independent_facts, required_cases, harness, engine,
             outbound=None) -> C8.6 形状报告 dict：
      {"decision": "publish" | "quarantine",
       "quarantine_reason": str | None,
       "cases": {"required_case_ids": [...], "executed": int,
                 "per_stage_consistent": bool, "parameter_evidence_refs": [...]},
       "regression": {...}, "tests": {...},
       "gate_environment": {"temp_store": bool, "network": bool,
                            "user_session": bool, "current_pointer_written": bool}}
  - harness（独立参考 seam）: run_case(case_id) -> {"case_id", "stages",
    "output", "parameter_evidence_ref"}；parameter_evidence_ref＝该 case 参数
    证据引用的独立事实 fact_id（None＝无独立参数证据）。
  - engine（候选引擎 seam）: run_case(case_id, candidate_content) ->
    {"case_id", "stages", "output"}；逐阶段一致＝harness 与 engine 各阶段
    中间值及 output 一致。
  - 判定：decision="publish" 须同时满足——required_cases 全部执行、每 case
    parameter_evidence_ref 解析到 independent_facts 中的独立事实、逐阶段一致、
    回归 failed=0、非空报告；任一不满足 → "quarantine"。
  - 隔离（C8.7）：门禁不得访问网络（若传入 outbound，任何出站尝试都必须被
    视为违例）、不使用用户 session、不写现行 store/current pointer——
    run_gate 不接受现行 store 句柄，验证在临时 store 中完成，并把
    gate_environment 四断言写入报告。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.testgate（候选门禁缺失）」。
"""

from __future__ import annotations

from pathlib import Path

from _fakes import (
    BUDGET_MANIFEST,
    FACT_CAP_3500,
    GATE_CASE,
    FakeEngine,
    FakeHarness,
    StrictOutbound,
    changed_bundle_content,
    initial_content,
    seed_published_store,
)


def _run_gate():
    from app.updater.testgate import run_gate

    return run_gate


def _agreeing_seams(evidence_ref: str | None):
    """engine 与 harness 输出完全一致（模拟「两个引擎一致」）。"""
    harness = FakeHarness(
        {
            GATE_CASE: {
                "case_id": GATE_CASE,
                "stages": ["3500"],
                "output": "3500",
                "parameter_evidence_ref": evidence_ref,
            }
        }
    )
    engine = FakeEngine(
        {GATE_CASE: {"case_id": GATE_CASE, "stages": ["3500"], "output": "3500"}}
    )
    return harness, engine


def test_bad_candidate_parameters_rejected_even_if_engines_agree() -> None:
    """C8.1：候选坏参数即使双引擎输出一致也不得发布——一致不构成证据。"""
    run_gate = _run_gate()

    # —— (A) 候选携带坏参数（cap=9999），harness 参数取自候选（输出一致），
    #        但无任何独立参数证据 → 隔离
    harness_bad = FakeHarness(
        {
            GATE_CASE: {
                "case_id": GATE_CASE,
                "stages": ["9999"],
                "output": "9999",
                "parameter_evidence_ref": None,
            }
        }
    )
    engine_bad = FakeEngine(
        {GATE_CASE: {"case_id": GATE_CASE, "stages": ["9999"], "output": "9999"}}
    )
    report = run_gate(
        candidate_content=changed_bundle_content("9999"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[],
        required_cases=[GATE_CASE],
        harness=harness_bad,
        engine=engine_bad,
    )
    assert report["decision"] == "quarantine", (
        "两个引擎一致本身不构成证据：无独立参数证据的候选必须隔离（C8.1）"
    )
    assert report["quarantine_reason"], "隔离必须携带理由"

    # —— (B) 对照：同一致输出＋独立 T1 参数证据在档 → 允许发布
    harness_ok, engine_ok = _agreeing_seams(FACT_CAP_3500["fact_id"])
    control = run_gate(
        candidate_content=changed_bundle_content("3500"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[FACT_CAP_3500],
        required_cases=[GATE_CASE],
        harness=harness_ok,
        engine=engine_ok,
    )
    assert control["decision"] == "publish", (
        f"独立证据齐备＋逐阶段一致应通过门禁：{control!r}"
    )


def test_candidate_gate_isolated_from_current_store_and_network(tmp_path) -> None:
    """C8.7：门禁＝临时 store／无网络／无 session／不写 current pointer。"""
    run_gate = _run_gate()
    db_path = tmp_path / "gate.db"
    store = seed_published_store(tmp_path, "gate.db")
    before_pointer = store.current()["bundle_id"]

    import sqlite3

    def _count(table: str) -> int:
        conn = sqlite3.connect(db_path)
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

    pubs_before = _count("publications")
    bundles_before = _count("rule_bundles")
    audits_before = _count("audit_log")

    # 严格出站假件：门禁内任何网络访问都会以 AssertionError 中断测试
    outbound = StrictOutbound()
    harness, engine = _agreeing_seams(FACT_CAP_3500["fact_id"])
    report = run_gate(
        candidate_content=changed_bundle_content("3500"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[FACT_CAP_3500],
        required_cases=[GATE_CASE],
        harness=harness,
        engine=engine,
        outbound=outbound,
    )

    assert report["decision"] == "publish"
    ge = report["gate_environment"]
    assert ge["temp_store_path"] and Path(ge["temp_store_path"]).exists(), (
        f"门禁隔离证据须含真实临时 store 路径（C8.7）：{ge!r}"
    )
    assert ge["network_calls"] == 0, f"门禁隔离证据须含真实网络调用计数＝0（C8.7）：{ge!r}"
    assert ge["user_session"] is False
    assert ge["current_pointer_written"] is False, (
        f"门禁隔离证据须证明未写 current pointer（C8.7）：{ge!r}"
    )

    # —— 现行 store 完全未被触碰（发布只发生在门禁外）
    assert not outbound.requests, "门禁运行环境不得有任何出站尝试（C8.7 无网络）"
    assert store.current()["bundle_id"] == before_pointer
    assert _count("publications") == pubs_before
    assert _count("rule_bundles") == bundles_before
    assert _count("audit_log") == audits_before


def test_missing_required_case_quarantines() -> None:
    """C8.4：必需 case 缺失/未全执行 → 隔离；空报告（零必需 case）禁止发布。"""
    run_gate = _run_gate()
    missing_case = "case_salaries_cap_2026_27"

    # —— (1) 绑定两个必需 case，harness 只能执行其一 → 隔离
    harness = FakeHarness(
        {
            GATE_CASE: {
                "case_id": GATE_CASE,
                "stages": ["3500"],
                "output": "3500",
                "parameter_evidence_ref": FACT_CAP_3500["fact_id"],
            }
        }
    )
    engine = FakeEngine(
        {GATE_CASE: {"case_id": GATE_CASE, "stages": ["3500"], "output": "3500"}}
    )
    report = run_gate(
        candidate_content=changed_bundle_content("3500"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[FACT_CAP_3500],
        required_cases=[GATE_CASE, missing_case],
        harness=harness,
        engine=engine,
    )
    assert report["decision"] == "quarantine", (
        "必需 case 未全部执行必须隔离（C8.4）"
    )
    assert report["quarantine_reason"]
    executed = report["cases"]["executed"]
    assert isinstance(executed, int) and executed < 2, (
        f"报告须如实记录已执行 case 数：{report['cases']!r}"
    )

    # —— (2) 空报告（零必需 case 绑定）→ 禁止发布
    harness_ok, engine_ok = _agreeing_seams(FACT_CAP_3500["fact_id"])
    empty = run_gate(
        candidate_content=changed_bundle_content("3500"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[FACT_CAP_3500],
        required_cases=[],
        harness=harness_ok,
        engine=engine_ok,
    )
    assert empty["decision"] == "quarantine", (
        "空报告＝零通过，不得发布（C8.4）；必需 case 集合由税法事实固化，不得为空"
    )
