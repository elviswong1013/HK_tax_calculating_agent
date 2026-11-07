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
    AVD_CHANGED,
    AVD_FULLPATH,
    AVD_MANIFEST,
    B5_REQUIRED_CASE,
    FACT_AVD_110,
    FakeEngine,
    FakeHarness,
    StrictOutbound,
    avd_changed_bundle_content,
    empty_b5_subcase_fields,
    initial_content,
    seed_published_store,
)


def _run_gate():
    from app.updater.testgate import run_gate

    return run_gate


def _agreeing_seams(evidence_ref: str | None):
    """engine 与 harness 输出完全一致（模拟「两个引擎一致」；AVD 路径）。"""
    harness = FakeHarness(
        {
            B5_REQUIRED_CASE: {
                "case_id": B5_REQUIRED_CASE,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": evidence_ref,
                **empty_b5_subcase_fields(),
            }
        }
    )
    engine = FakeEngine(
        {
            B5_REQUIRED_CASE: {
                "case_id": B5_REQUIRED_CASE,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                **empty_b5_subcase_fields(),
            }
        }
    )
    return harness, engine


def _gate_kwargs() -> dict:
    """不受影响路径＋B5 case＋candidate hash/required 绑定（004/005 契约）。"""
    from app.core.hashing import bundle_content_hash

    candidate = avd_changed_bundle_content()
    return {
        "candidate_content": candidate,
        "current_content": initial_content(),
        "manifests": [AVD_MANIFEST],
        "independent_facts": [FACT_AVD_110],
        "required_cases": [B5_REQUIRED_CASE],
        "candidate_hash": bundle_content_hash(candidate),
        "changed_paths": [AVD_FULLPATH],
        "required_case_map": {AVD_FULLPATH: B5_REQUIRED_CASE},
    }


def test_bad_candidate_parameters_rejected_even_if_engines_agree() -> None:
    """C8.1：双引擎一致不构成证据；candidate hash/profile/case 身份须绑定。"""
    run_gate = _run_gate()

    # —— (A) 候选坏参数（无独立 T1 证据），harness 参数取自候选（输出一致）
    harness_bad = FakeHarness(
        {
            B5_REQUIRED_CASE: {
                "case_id": B5_REQUIRED_CASE,
                "stages": ["9999"],
                "output": "9999",
                "parameter_evidence_ref": None,
                **empty_b5_subcase_fields(),
            }
        }
    )
    engine_bad = FakeEngine(
        {
            B5_REQUIRED_CASE: {
                "case_id": B5_REQUIRED_CASE,
                "stages": ["9999"],
                "output": "9999",
                **empty_b5_subcase_fields(),
            }
        }
    )
    bad_kwargs = _gate_kwargs()
    bad_kwargs["independent_facts"] = []
    report = run_gate(
        **bad_kwargs,
        harness=harness_bad,
        engine=engine_bad,
        candidate_profile="avd_related_conveyance_v1",
    )
    assert report["decision"] == "quarantine", (
        "两个引擎一致本身不构成证据：无独立参数证据的候选必须隔离（C8.1）"
    )
    assert report["quarantine_reason"], "隔离必须携带理由"

    # —— (B) 对照：独立 T1 证据齐备＋case identity/hash 绑定 → 允许发布
    harness_ok, engine_ok = _agreeing_seams(FACT_AVD_110["fact_id"])
    control = run_gate(
        **_gate_kwargs(),
        harness=harness_ok,
        engine=engine_ok,
        candidate_profile="avd_related_conveyance_v1",
        case_identity={B5_REQUIRED_CASE: "avd_related_fixed"},
    )
    assert control["decision"] == "publish", (
        f"独立证据齐备＋逐阶段一致应通过门禁：{control!r}"
    )


def test_candidate_gate_isolated_from_current_store_and_network(
    tmp_path, approved_temp_root, safety_guard
) -> None:
    """C8.7：门禁＝临时 store／无网络／无 session／不写 current pointer。

    W-L L4：门禁临时目录/DB 归属批准 Temp 根并登记（真实 transport guard＝会话
    边界计数；报告 network_calls 仅为注入 seam 计数，不得替代）。
    """
    run_gate = _run_gate()
    db_path = tmp_path / "gate.db"
    store = seed_published_store(tmp_path, "gate.db")
    before_pointer = store.current()["bundle_id"]
    counters_before = safety_guard.counter_snapshot()
    gate_dirs_before = set(approved_temp_root.glob("hktax-gate-*"))

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
    harness, engine = _agreeing_seams(FACT_AVD_110["fact_id"])
    report = run_gate(
        **_gate_kwargs(),
        harness=harness,
        engine=engine,
        outbound=outbound,
        current_store_db=str(db_path),
        approved_temp_root=str(approved_temp_root),
    )

    assert report["decision"] == "publish"
    ge = report["gate_environment"]
    assert ge["temp_store_path"] and Path(ge["temp_store_path"]).exists(), (
        f"门禁隔离证据须含真实临时 store 路径（C8.7）：{ge!r}"
    )
    assert ge["network_calls"] == 0, f"门禁隔离证据须含真实网络调用计数＝0（C8.7）：{ge!r}"
    for key in (
        "direct_http_attempts",
        "socket_attempts",
        "dns_attempts",
        "session_creations",
        "pointer_write_attempts",
    ):
        assert ge.get(key) == 0, f"直接观察计数 {key} 须为 0：{ge!r}"
    assert ge.get("observers_restored") is True, (
        f"门禁结束须复原观察器（C8.7）：{ge!r}"
    )
    assert ge["user_session"] is False
    assert ge["current_pointer_written"] is False, (
        f"门禁隔离证据须证明未写 current pointer（C8.7）：{ge!r}"
    )

    # —— W-L L4：门禁临时目录/DB 归属批准 Temp 根，并入安全边界登记账 ——
    gate_store_path = Path(ge["temp_store_path"]).resolve()
    assert gate_store_path.parent.parent == approved_temp_root, (
        f"门禁临时目录必须归属批准 Temp 根（{approved_temp_root}）：{gate_store_path}"
    )
    new_gate_dirs = set(approved_temp_root.glob("hktax-gate-*")) - gate_dirs_before
    assert gate_store_path.parent in new_gate_dirs, (
        f"门禁运行必须新建批准根内 hktax-gate-* 目录：{gate_store_path.parent}"
    )
    assert str(gate_store_path) in set(safety_guard.observed_store_paths()), (
        f"门禁临时 DB 必须登记至安全边界记录：{gate_store_path}"
    )
    # 真实 transport guard＝会话边界计数零增量（network_calls 仅为注入 seam 计数）
    assert safety_guard.counter_deltas(counters_before) == {}, (
        "真实 app 边界（启动/真实 transport/模型出站）在本测试内必须零调用"
    )

    # —— 现行 store 完全未被触碰（发布只发生在门禁外）
    assert not outbound.requests, "门禁运行环境不得有任何出站尝试（C8.7 无网络）"
    assert store.current()["bundle_id"] == before_pointer
    assert _count("publications") == pubs_before
    assert _count("rule_bundles") == bundles_before
    assert _count("audit_log") == audits_before


def test_missing_required_case_quarantines() -> None:
    """C8.4：必需 case 缺失/未全执行 → 隔离；required 集合由服务器重算。"""
    run_gate = _run_gate()
    missing_case = "case_002_profits_threshold_boundary"

    # —— (1) 绑定两个必需 case（purpose/baseline/diff），只能执行其一 → 隔离
    harness = FakeHarness(
        {
            B5_REQUIRED_CASE: {
                "case_id": B5_REQUIRED_CASE,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                "parameter_evidence_ref": FACT_AVD_110["fact_id"],
                **empty_b5_subcase_fields(),
            }
        }
    )
    engine = FakeEngine(
        {
            B5_REQUIRED_CASE: {
                "case_id": B5_REQUIRED_CASE,
                "stages": [AVD_CHANGED],
                "output": AVD_CHANGED,
                **empty_b5_subcase_fields(),
            }
        }
    )
    kwargs = _gate_kwargs()
    kwargs["required_cases"] = [B5_REQUIRED_CASE, missing_case]
    kwargs["purpose"] = "update"
    kwargs["baseline"] = {
        "bundle_id": "b-test-baseline",
        "bundle_hash": "sha256:" + "55" * 32,
        "rules_schema_version": "2.0.0",
    }
    kwargs["changed_paths"] = [AVD_FULLPATH, "profits.two_tier_threshold"]
    kwargs["required_case_map"] = {
        AVD_FULLPATH: B5_REQUIRED_CASE,
        "profits.two_tier_threshold": missing_case,
    }
    report = run_gate(**kwargs, harness=harness, engine=engine)
    assert report["decision"] == "quarantine", (
        "必需 case 未全部执行必须隔离（C8.4）"
    )
    assert report["quarantine_reason"]
    executed = report["cases"]["executed"]
    assert isinstance(executed, int) and executed < 2, (
        f"报告须如实记录已执行 case 数：{report['cases']!r}"
    )

    # —— (2) 空 required 入参：服务器按 purpose/diff 重算，不得自减为空 ——
    harness_ok, engine_ok = _agreeing_seams(FACT_AVD_110["fact_id"])
    empty = run_gate(
        **{**kwargs, "required_cases": [], "changed_paths": [AVD_FULLPATH],
           "required_case_map": {AVD_FULLPATH: B5_REQUIRED_CASE}},
        harness=harness_ok,
        engine=engine_ok,
    )
    assert empty["decision"] == "quarantine", (
        "空报告＝零通过，不得发布（C8.4）；required 集合须由服务器按 diff 重算"
    )
