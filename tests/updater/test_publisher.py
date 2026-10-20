"""REQ-18 发布与回滚（M5 Red；SDD §5 发布/回滚行＋Annex C C5.6/C8.4）。

REQ: 18（§9：发布＝单事务原子指针切换＋audit；全验证自动发布（无点击）；
回滚仅审计化指针、不法律时间旅行——不得把已失效旧规则宣称为现行有效，
受影响期间拒算并说明）。
规格锚点:
  - SDD §5：发布＝单事务内（插入 bundle＋publication＋指针切换），无新旧参数
    混用；回滚仅审计化指针回退到此前已发布版本，写 audit_log。
  - Annex C C5.6：check/publish/rollback 三者互斥；单事务原子。
  - Annex C C5.8：audit_log 四类事件（rule/check/publish/rollback）。
  - Annex C C8.4：空报告/未通过门禁 → 不得发布。
注: M1 已有 test_atomic_pointer_no_mixed_versions（tests/unit/
  test_rules_store_pointer.py，store 层原子指针）——本文件测**更新管线**的
  发布（audit＋job 关联＋门禁报告门）与回滚语义，不重复 store 层断言。
期望值来源: 结构性契约（原子性/审计/拒绝语义），无金额期望。

【拟名】被测契约（app/updater/publisher，Green 阶段须按测试实现，不得要求
测试改写）:
  - publish_update(store, *, job_id, content, evidence_refs,
                   validation_report) -> {"bundle_id", "bundle_hash",
                   "rules_schema_version"}
    单事务插入 bundle＋publication 并切换 current_pointer，同时写 publish 类
    audit 事件（detail 含 job_id 与 bundle_id）；任一步失败 → 整体拒绝、
    零部分状态。
    validation_report 门（C8.4）：report["decision"] 必须为 "publish" 且
    report["tests"]["passed"] > 0，否则拒绝发布（抛 AppError/ValueError）。
  - rollback_to(store, *, target_bundle_id, reason) ->
    {"rolled_back_to", "from_bundle_id", "affected_periods", "notice"}
    仅允许回退到此前已发布版本（publications 内）；未知/未发布目标 →
    AppError(code="E_UPDATE_ROLLBACK_INVALID")。回滚写 rollback 类 audit 事件
    （detail 含 from/to bundle_id）；affected_periods＝两版本数据差所涉课税
    年度键（路径中 YYYY_NN 形态，如 2025_26）；受影响期间在 availability 中
    阻断（不把旧规则宣称为现行有效）。目标 bundle 内容不被改写。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.publisher（发布/回滚缺失）」。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.core.hashing import bundle_content_hash, canonical_json_bytes

from _fakes import (
    AVD_FULLPATH,
    AVD_MANIFEST,
    FACT_AVD_110,
    FakeClock,
    FakeOutbound,
    avd_changed_bundle_content,
    base_documents,
    changed_bundle_content,
    hk,
    initial_content,
    make_scheduler,
    manifest_with_slot,
    seed_published_store,
)
from _repair1_helpers import provision_plan_subcases, subcase_proof_fields

REPORT_PUBLISH_OK = {
    "decision": "publish",
    "tests": {"passed": 3, "failed": 0, "failed_ids": []},
    "quarantine_reason": None,
}
REPORT_EMPTY_TESTS = {
    "decision": "publish",
    "tests": {"passed": 0, "failed": 0, "failed_ids": []},
    "quarantine_reason": None,
}
REPORT_QUARANTINED = {
    "decision": "quarantine",
    "tests": {"passed": 2, "failed": 1, "failed_ids": ["case_x"]},
    "quarantine_reason": "test_failure",
}


def _publisher():
    from app.updater.publisher import publish_update, rollback_to

    return publish_update, rollback_to


def _rows(db_path, table: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def _audit_details(db_path, event_type: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE event_type = ?",
            (event_type,),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


B5_CASE = "a_avd_related_fixed"
PROFITS_CAP_PATH = "profits.reductions.2025_26.cap"
PROFITS_CASE = "a_profits_rebate_cap"
FACT_PROFITS_3200 = {
    "fact_id": "fact:test:profits_cap_3200",
    "fullpath": PROFITS_CAP_PATH,
    "value": "3200",
    "source_id": "test_profits_source",
    "url": "https://example.invalid/profits",
    "anchor": "heading:Profits Tax#row:2025/26#col:Cap",
    "tier": "T1",
}
PROFITS_MANIFEST = manifest_with_slot(
    PROFITS_CAP_PATH,
    source_id="test_profits_source",
    profile_id="profits_reduction_v1",
    constraints={"min": "0", "max": "10000", "step": "1"},
    anchor="heading:Profits Tax#row:2025/26#col:Cap",
)


def _profits_changed_bundle_content(value: str = "3200") -> dict:
    content = initial_content()
    content["data"]["profits"]["reductions"]["2025_26"]["cap"] = value
    return content


def _canonical_sha(payload) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _reference_code_sha256() -> str:
    """运行时读取当前 reference.py 文件字节（不得硬编码/不得 canonical label）。"""
    from app.updater import reference

    return "sha256:" + hashlib.sha256(
        Path(reference.__file__).read_bytes()
    ).hexdigest()


def _profile_bindings(content) -> list[dict]:
    """Profile binding rows; legacy profiles use canonical-JSON SHA-256 digest.

    Shared P stores its own `semantic_digest`; the five legacy fixed profiles have
    no stored digest in bundle content, so the fixture uses the same canonical
    bytes hash for them (publisher can recompute identically).
    """
    return [
        {
            "profile_id": str(profile["profile_id"]),
            "semantic_digest": str(
                profile.get("semantic_digest") or _canonical_sha(profile)
            ),
        }
        for profile in content["frozen_semantics"]["profiles"]
    ]


def _update_report(
    store,
    *,
    content,
    required_cases=(B5_CASE,),
    changed_paths=(AVD_FULLPATH,),
    baseline=None,
    manifests=(AVD_MANIFEST,),
    evidence_refs=(FACT_AVD_110,),
) -> dict:
    """Complete 005 §15.4 report fixture (binding/coverage/cases/regression/proof)."""
    from app.config import ENGINE_VERSION
    from app.updater.reference import B5_CASES, REQUIRED_CASES

    base = baseline or store.current()
    manifest_rows = [dict(manifest) for manifest in manifests]
    evidence_rows = [dict(fact) for fact in evidence_refs]
    required = [str(case_id) for case_id in required_cases]
    static_cases = list(REQUIRED_CASES)
    rows: list[dict] = []
    for case_id in required:
        plan = B5_CASES.get(case_id) or {}
        refs = [
            str(fact.get("fact_id"))
            for fact in evidence_rows
            if fact.get("fullpath") == plan.get("path")
        ]
        subcase_ids = [str(value) for value in plan.get("subcase_ids") or []]
        expected_subcases, actual_subcases = provision_plan_subcases(plan)
        row = {
            "case_id": case_id,
            "mode": "candidate",
            "input_proof_sha256": _canonical_sha(
                {"case_id": case_id, "subcase_ids": subcase_ids}
            ),
            "subcase_ids": subcase_ids,
            "worksheet_sha256": _canonical_sha(
                {"case_id": case_id, "path": plan.get("path")}
            ),
            "parameter_evidence_refs": refs,
            "stage_match": True,
            "passed": True,
        }
        row.update(
            subcase_proof_fields(
                expected_subcases,
                actual_subcases,
                subcase_ids=subcase_ids,
            )
        )
        rows.append(row)
    return {
        "decision": "publish",
        "purpose": "update",
        "binding": {
            "baseline_bundle_id": base["bundle_id"],
            "baseline_bundle_hash": base["bundle_hash"],
            "baseline_rules_schema_version": base["rules_schema_version"],
            "candidate_bundle_hash": bundle_content_hash(content),
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": base["rules_schema_version"],
            "profiles": _profile_bindings(content),
            "manifests_sha256": _canonical_sha(manifest_rows),
            "evidence_sha256": _canonical_sha(evidence_rows),
            "reference_code_sha256": _reference_code_sha256(),
            "required_case_ids": list(required),
        },
        "coverage": {
            "changed_paths": [str(path) for path in changed_paths],
            "affected_case_ids": list(required),
            "static_case_ids": static_cases,
        },
        "cases": {
            "required_case_ids": list(required),
            "executed": len(required),
            "per_stage_consistent": True,
            "rows": rows,
        },
        "regression": {
            "static_case_ids": static_cases,
            "static_baseline_bundle_hash": bundle_content_hash(initial_content()),
            "passed": len(static_cases),
            "failed": 0,
            "failed_ids": [],
        },
        "gate_environment": {
            "temp_store_path": str(store._path.parent / "gate-store.db"),
            "network_calls": 0,
            "direct_http_attempts": 0,
            "socket_attempts": 0,
            "dns_attempts": 0,
            "session_creations": 0,
            "pointer_write_attempts": 0,
            "observers_restored": True,
            "user_session": False,
            "current_pointer_written": False,
            "outside_temp_db": 0,
            "thread_starts": 0,
            "subprocess_starts": 0,
        },
        "tests": {"passed": max(1, len(required)), "failed": 0, "failed_ids": []},
        "quarantine_reason": None,
    }


def _seed_report(
    *,
    content=None,
    manifests=(),
    evidence_refs=(),
    required_cases=None,
) -> dict:
    """Valid seed proof: frozen release candidate/hash/profile/proof/engine."""
    from app.config import ENGINE_VERSION
    from app.updater.reference import REQUIRED_CASES

    release = initial_content() if content is None else content
    required = list(REQUIRED_CASES if required_cases is None else required_cases)
    manifest_rows = [dict(manifest) for manifest in manifests]
    evidence_rows = [dict(fact) for fact in evidence_refs]
    return {
        "decision": "publish",
        "purpose": "seed",
        "binding": {
            "baseline_bundle_id": None,
            "baseline_bundle_hash": None,
            "baseline_rules_schema_version": None,
            "candidate_bundle_hash": bundle_content_hash(release),
            "engine_version": ENGINE_VERSION,
            "rules_schema_version": str(release.get("rules_schema_version")),
            "profiles": _profile_bindings(release),
            "manifests_sha256": _canonical_sha(manifest_rows),
            "evidence_sha256": _canonical_sha(evidence_rows),
            "reference_code_sha256": _reference_code_sha256(),
            "required_case_ids": required,
        },
        "cases": {
            "required_case_ids": required,
            "executed": len(required),
        },
        "tests": {"passed": len(required), "failed": 0, "failed_ids": []},
        "quarantine_reason": None,
    }


def _assert_seed_rejected_without_write(tmp_path, *, report, match: str) -> None:
    from app.rules.store import RuleStore
    from app.updater.publisher import publish_update
    from app.updater.reference import REQUIRED_CASES

    store = RuleStore(tmp_path / "seed-negative.db")
    assert store.is_true_empty()
    with pytest.raises((ValueError, AppError)) as excinfo:
        publish_update(
            store,
            job_id="seed-negative",
            content=initial_content(),
            evidence_refs=[],
            validation_report=report,
            purpose="seed",
            baseline=None,
            manifests=(),
            required_cases=tuple(REQUIRED_CASES),
        )
    assert any(
        token in str(excinfo.value).lower() for token in match.lower().split("|")
    ), f"expected seed binding rejection containing one of {match!r}, actual {excinfo.value!r}"
    assert store.is_true_empty(), "rejected seed must not write any bundle/pointer"


def _publish(
    store,
    *,
    job_id,
    content,
    report=None,
    baseline=None,
    required_cases=(B5_CASE,),
    changed_paths=(AVD_FULLPATH,),
    manifests=(AVD_MANIFEST,),
    evidence_refs=(FACT_AVD_110,),
):
    publish_update, _rollback = _publisher()
    active_baseline = baseline or store.current()
    return publish_update(
        store,
        job_id=job_id,
        content=content,
        evidence_refs=list(evidence_refs),
        validation_report=report
        or _update_report(
            store,
            content=content,
            required_cases=required_cases,
            changed_paths=changed_paths,
            baseline=active_baseline,
            manifests=manifests,
            evidence_refs=evidence_refs,
        ),
        purpose="update",
        baseline=active_baseline,
        manifests=list(manifests),
        required_cases=tuple(required_cases),
    )


def _counts(db_path) -> tuple[int, int, int]:
    return (
        len(_rows(db_path, "publications")),
        len(_rows(db_path, "rule_bundles")),
        len(_rows(db_path, "audit_log")),
    )


def _audit_count(db_path, event_type: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE event_type = ?",
                (event_type,),
            ).fetchone()[0]
        )
    finally:
        conn.close()


def _assert_rejected_without_write(
    store,
    db_path,
    *,
    job_id,
    content,
    report,
    match: str,
    required_cases=(B5_CASE,),
) -> None:
    before = store.current()
    counts = _counts(db_path)
    with pytest.raises((ValueError, AppError)) as excinfo:
        _publish(
            store,
            job_id=job_id,
            content=content,
            report=report,
            required_cases=required_cases,
        )
    assert any(
        token in str(excinfo.value).lower() for token in match.lower().split("|")
    ), f"expected rejection containing one of {match!r}, actual {excinfo.value!r}"
    assert store.current() == before, "rejection must not switch pointer"
    assert _counts(db_path) == counts, "rejection must not write any rows"


def test_update_publish_atomic_and_audit(tmp_path) -> None:
    """更新发布＝purpose/baseline/required 绑定＋单事务＋audit；不匹配零部分写。"""
    publish_update, _rollback = _publisher()
    db_path = tmp_path / "publish.db"
    store = seed_published_store(tmp_path, "publish.db")
    before = store.current()

    # —— 成功发布：指针切换＋publications＋publish 类 audit（job/baseline 绑定）
    published = _publish(
        store,
        job_id="job-m5-1",
        content=avd_changed_bundle_content(),
    )
    current = store.current()
    assert current["bundle_id"] == published["bundle_id"]
    assert current["bundle_id"] != before["bundle_id"]
    publish_events = _audit_details(db_path, "publish")
    assert publish_events, "更新发布必须写 publish 类 audit 事件（C5.8）"
    assert any(
        "job-m5-1" in detail
        and published["bundle_id"] in detail
        and '"purpose": "update"' in detail
        and f'"baseline_bundle_id": "{before["bundle_id"]}"' in detail
        for detail in publish_events
    ), f"publish audit 须关联 job_id/bundle_id/purpose/baseline：{publish_events!r}"

    # —— 原子性：非法 content → 整体拒绝，零部分状态
    pubs_after = len(_rows(db_path, "publications"))
    audits_after = len(_rows(db_path, "audit_log"))
    with pytest.raises((ValueError, AppError)):
        _publish(
            store,
            job_id="job-m5-2",
            content={"rules_schema_version": "1.0.0"},  # 缺 C2.8 封闭字段
        )
    assert len(_rows(db_path, "publications")) == pubs_after
    assert len(_rows(db_path, "audit_log")) == audits_after
    assert store.current()["bundle_id"] == current["bundle_id"]

    # —— baseline 被替换 → 事务内 CAS 复核失败，零部分写 ——
    stale = current
    store.publish(changed_bundle_content("3550"), evidence_refs=[])
    current_after = store.current()
    assert current_after["bundle_id"] != stale["bundle_id"]
    pubs_cas = len(_rows(db_path, "publications"))
    bundles_cas = len(_rows(db_path, "rule_bundles"))
    audits_cas = len(_rows(db_path, "audit_log"))
    stale_report = _update_report(
        store,
        content=avd_changed_bundle_content("120"),
        required_cases=(B5_CASE,),
        changed_paths=(AVD_FULLPATH,),
        baseline=stale,
    )
    stale_report["binding"]["baseline_bundle_id"] = stale["bundle_id"]
    stale_report["binding"]["baseline_bundle_hash"] = stale["bundle_hash"]
    with pytest.raises((ValueError, AppError)):
        _publish(
            store,
            job_id="job-m5-cas",
            content=avd_changed_bundle_content("120"),
            report=stale_report,
            baseline=stale,
        )
    assert store.current() == current_after
    assert len(_rows(db_path, "publications")) == pubs_cas
    assert len(_rows(db_path, "rule_bundles")) == bundles_cas
    assert len(_rows(db_path, "audit_log")) == audits_cas

    # —— 门禁报告门（C8.4）：空报告/隔离报告 → 拒绝发布
    empty = _update_report(
        store, content=avd_changed_bundle_content("130")
    )
    empty["tests"] = {"passed": 0, "failed": 0, "failed_ids": []}
    with pytest.raises((ValueError, AppError)):
        _publish(
            store,
            job_id="job-m5-3",
            content=avd_changed_bundle_content("130"),
            report=empty,
        )
    quarantined = _update_report(
        store, content=avd_changed_bundle_content("140")
    )
    quarantined["decision"] = "quarantine"
    quarantined["quarantine_reason"] = "test_failure"
    with pytest.raises((ValueError, AppError)):
        _publish(
            store,
            job_id="job-m5-4",
            content=avd_changed_bundle_content("140"),
            report=quarantined,
        )
    assert store.current()["bundle_id"] == current_after["bundle_id"], (
        "未通过门禁的候选不得发布（保持现行版本）"
    )

    # —— 报告自减 required 集合 → 服务端重算拒绝 ——
    reduced = _update_report(
        store,
        content=avd_changed_bundle_content("150"),
        required_cases=(B5_CASE, "case_x"),
    )
    reduced["cases"]["required_case_ids"] = [B5_CASE]
    with pytest.raises((ValueError, AppError)):
        _publish(
            store,
            job_id="job-m5-required",
            content=avd_changed_bundle_content("150"),
            report=reduced,
            required_cases=(B5_CASE, "case_x"),
        )
    assert store.current()["bundle_id"] == current_after["bundle_id"]


def test_rollback_audited_pointer_not_time_travel(tmp_path) -> None:
    """回滚＝审计化指针回退；不把旧规则标现行——受影响期间拒算并说明。"""
    publish_update, rollback_to = _publisher()
    db_path = tmp_path / "rollback.db"
    store = seed_published_store(tmp_path, "rollback.db")
    target = store.current()  # A＝INITIAL（此前已发布）

    newer_content = _profits_changed_bundle_content()
    newer = publish_update(
        store,
        job_id="job-m5-r1",
        content=newer_content,
        evidence_refs=[FACT_PROFITS_3200],
        validation_report=_update_report(
            store,
            content=newer_content,
            required_cases=(PROFITS_CASE,),
            changed_paths=(PROFITS_CAP_PATH,),
            manifests=(PROFITS_MANIFEST,),
            evidence_refs=(FACT_PROFITS_3200,),
        ),
        purpose="update",
        baseline=target,
        manifests=[PROFITS_MANIFEST],
        required_cases=(PROFITS_CASE,),
    )  # B＝现行

    # —— 无效目标（从未发布）→ E_UPDATE_ROLLBACK_INVALID
    with pytest.raises(AppError) as excinfo:
        rollback_to(store, target_bundle_id="b-never-published", reason="测试")
    assert excinfo.value.code == "E_UPDATE_ROLLBACK_INVALID"

    # —— 回滚 A：仅指针移动＋rollback 类 audit（含 from/to）
    result = rollback_to(store, target_bundle_id=target["bundle_id"], reason="复核回退")
    assert result["rolled_back_to"] == target["bundle_id"]
    assert result["from_bundle_id"] == newer["bundle_id"]
    assert store.current()["bundle_id"] == target["bundle_id"]
    rollback_events = _audit_details(db_path, "rollback")
    assert rollback_events, "回滚必须写 rollback 类 audit 事件（§5）"
    assert any(
        target["bundle_id"] in detail and newer["bundle_id"] in detail
        for detail in rollback_events
    ), f"rollback audit 须含 from/to bundle_id：{rollback_events!r}"

    # —— 不做法律时间旅行：受影响期间拒算并说明（不得以旧规则冒充现行有效）
    assert "2025_26" in result["affected_periods"], (
        "回滚须给出数据差所涉受影响期间（两版本 cap 差在 2025_26）"
    )
    sched = make_scheduler(
        store,
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=FakeOutbound(base_documents()),
    )
    blocked = sched.availability("2025_26")
    assert blocked["mode"] == "blocked", "回滚后受影响期间必须拒算（§5 回滚行）"
    assert blocked["notice"], "拒算必须携带中文说明"
    assert sched.availability("2024_25")["mode"] == "ok", "未受影响期间不受牵连"

    # —— 目标 bundle 内容不被改写（不得重写适用期/生效日把旧规则标现行）
    loaded = store.get_bundle(target["bundle_id"])
    assert loaded["applicability"] == initial_content()["applicability"]


def test_publish_requires_candidate_bundle_hash(tmp_path) -> None:
    """F2：报告缺 candidate hash 不得继续（非仅“若提供则比较”）。"""
    store = seed_published_store(tmp_path, "f2-candidate.db")
    db_path = tmp_path / "f2-candidate.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    report["binding"].pop("candidate_bundle_hash", None)
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-candidate",
        content=content,
        report=report,
        match="candidate",
    )


def test_publish_requires_manifest_hash(tmp_path) -> None:
    """F2：报告缺 manifest hash 不得继续（非仅“若提供则比较”）。"""
    store = seed_published_store(tmp_path, "f2-manifest.db")
    db_path = tmp_path / "f2-manifest.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    report["binding"].pop("manifests_sha256", None)
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-manifest",
        content=content,
        report=report,
        match="manifest",
    )


def test_publish_recomputes_required_from_purpose_and_diff(tmp_path) -> None:
    """F2：required 必须按 purpose/实际 diff 重算，报告/参数不得自增自减。"""
    store = seed_published_store(tmp_path, "f2-required.db")
    db_path = tmp_path / "f2-required.db"
    content = avd_changed_bundle_content()
    required = (B5_CASE, "case_extra")
    report = _update_report(store, content=content, required_cases=required)
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-required",
        content=content,
        report=report,
        match="required",
        required_cases=required,
    )


def test_publish_requires_candidate_case_receipts(tmp_path) -> None:
    """F2：缺 candidate case 执行收据（rows）不得发布。"""
    store = seed_published_store(tmp_path, "f2-receipts.db")
    db_path = tmp_path / "f2-receipts.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    report["cases"]["rows"] = []
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-receipts",
        content=content,
        report=report,
        match="case",
    )


def test_publish_requires_static_regression_zero_failures(tmp_path) -> None:
    """F2：static 回归失败/缺完整 baseline 不得发布。"""
    store = seed_published_store(tmp_path, "f2-static.db")
    db_path = tmp_path / "f2-static.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    report["regression"]["failed"] = 1
    report["regression"]["failed_ids"] = ["case_salaries_pam39_q1_2025_26"]
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-static",
        content=content,
        report=report,
        match="static|regression",
    )


def test_publish_requires_isolation_evidence(tmp_path) -> None:
    """F2：缺/非零隔离证据（网络/session/pointer）不得发布。"""
    store = seed_published_store(tmp_path, "f2-isolation.db")
    db_path = tmp_path / "f2-isolation.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    report["gate_environment"]["network_calls"] = 1
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-isolation",
        content=content,
        report=report,
        match="isolation|gate",
    )


def test_publish_requires_matching_proof_digest(tmp_path) -> None:
    """F2：evidence/proof 摘要必须与实际独立事实一致。"""
    store = seed_published_store(tmp_path, "f2-proof.db")
    db_path = tmp_path / "f2-proof.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    report["binding"]["evidence_sha256"] = "sha256:" + "00" * 32
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-proof",
        content=content,
        report=report,
        match="evidence|proof",
    )


def test_publish_rejects_five_tax_fixed_profile_tamper(tmp_path) -> None:
    """F2：五税固定 profile 内容被改（不仅共享 P）必须拒绝。"""
    store = seed_published_store(tmp_path, "f2-profile.db")
    db_path = tmp_path / "f2-profile.db"
    content = avd_changed_bundle_content()
    for profile in content["frozen_semantics"]["profiles"]:
        if profile["profile_id"] == "property_per_unit_floor":
            profile["rule"] = "tampered fixed profile rule"
            break
    else:  # pragma: no cover - fixture contract
        raise AssertionError("property_per_unit_floor profile missing")
    report = _update_report(store, content=content)
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-profile",
        content=content,
        report=report,
        match="profile",
    )


def test_seed_rejects_non_release_candidate(tmp_path) -> None:
    """F2：seed 必须绑定发行冻结产物，不得现场发布任意自洽候选。"""
    from app.rules.store import RuleStore
    from app.updater.reference import REQUIRED_CASES

    store = RuleStore(tmp_path / "f2-seed.db")
    content = avd_changed_bundle_content()
    required = list(REQUIRED_CASES)
    report = _seed_report(
        content=content,
        manifests=[AVD_MANIFEST],
        required_cases=required,
    )
    publish_update, _rollback = _publisher()
    with pytest.raises((ValueError, AppError)) as excinfo:
        publish_update(
            store,
            job_id="f2-seed",
            content=content,
            evidence_refs=[],
            validation_report=report,
            purpose="seed",
            baseline=None,
            manifests=[AVD_MANIFEST],
            required_cases=tuple(required),
        )
    assert any(
        token in str(excinfo.value).lower()
        for token in ("seed", "release", "frozen", "candidate")
    ), f"expected seed release binding rejection, actual {excinfo.value!r}"
    assert store.is_true_empty(), "rejected seed must not write any bundle/pointer"


def test_release_seed_report_binds_release_artifacts(tmp_path) -> None:
    """F2-1：生产 release_seed_report 必须带完整发行证明并可真空 seed。"""
    from app.config import ENGINE_VERSION
    from app.rules.store import RuleStore
    from app.updater.reference import REQUIRED_CASES, release_seed_report

    report = release_seed_report()
    binding = report["binding"]
    assert report["purpose"] == "seed"
    assert binding["baseline_bundle_id"] is None
    assert binding["baseline_bundle_hash"] is None
    assert binding["baseline_rules_schema_version"] is None
    assert binding["candidate_bundle_hash"] == bundle_content_hash(initial_content())
    assert binding["engine_version"] == ENGINE_VERSION
    assert binding["rules_schema_version"] == "2.0.0"
    assert binding["profiles"] == _profile_bindings(initial_content())
    assert binding["manifests_sha256"] == _canonical_sha([])
    assert binding["evidence_sha256"] == _canonical_sha([])
    assert binding["reference_code_sha256"] == _reference_code_sha256()
    assert binding["required_case_ids"] == list(REQUIRED_CASES)

    store = RuleStore(tmp_path / "release-seed.db")
    publish_update, _rollback = _publisher()
    published = publish_update(
        store,
        job_id="release-seed",
        content=initial_content(),
        evidence_refs=[],
        validation_report=report,
        purpose="seed",
        baseline=None,
        manifests=(),
        required_cases=tuple(REQUIRED_CASES),
    )
    assert store.current()["bundle_id"] == published["bundle_id"]


def test_seed_requires_candidate_hash(tmp_path) -> None:
    """F2-1：seed 报告缺 candidate hash 不得继续。"""
    report = _seed_report()
    report["binding"].pop("candidate_bundle_hash", None)
    _assert_seed_rejected_without_write(tmp_path, report=report, match="candidate")


def test_seed_requires_manifest_hash(tmp_path) -> None:
    """F2-1：seed 报告缺 manifest hash 不得继续。"""
    report = _seed_report()
    report["binding"].pop("manifests_sha256", None)
    _assert_seed_rejected_without_write(tmp_path, report=report, match="manifest")


def test_seed_requires_profile_bindings(tmp_path) -> None:
    """F2-1：seed 报告 profile 绑定必须与冻结发行内容一致。"""
    report = _seed_report()
    report["binding"]["profiles"] = []
    _assert_seed_rejected_without_write(tmp_path, report=report, match="profile")


def test_seed_requires_evidence_proof_digest(tmp_path) -> None:
    """F2-1：seed 报告 evidence/proof 摘要必须存在且与实际事实一致。"""
    report = _seed_report()
    report["binding"]["evidence_sha256"] = "sha256:" + "00" * 32
    _assert_seed_rejected_without_write(
        tmp_path, report=report, match="evidence|proof"
    )


def test_seed_requires_matching_engine_version(tmp_path) -> None:
    """F2-1：seed 报告 engine_version 必须与当前引擎一致。"""
    report = _seed_report()
    report["binding"]["engine_version"] = "0.0.0-seed-mismatch"
    _assert_seed_rejected_without_write(tmp_path, report=report, match="engine")


def test_seed_requires_release_required_cases(tmp_path) -> None:
    """F2-1：seed required 集合必须等于发行 REQUIRED_CASES。"""
    from app.updater.reference import REQUIRED_CASES

    report = _seed_report()
    wrong = ["case_002_avd_related_fixed"]
    assert wrong != list(REQUIRED_CASES)
    report["binding"]["required_case_ids"] = list(wrong)
    report["cases"]["required_case_ids"] = list(wrong)
    report["cases"]["executed"] = len(wrong)
    _assert_seed_rejected_without_write(tmp_path, report=report, match="required")


def test_publish_rejects_reference_code_label_hash(tmp_path) -> None:
    """F2-2：reference_code_sha256 不得用 canonical label 代替文件字节 hash。"""
    store = seed_published_store(tmp_path, "f2-reference-label.db")
    db_path = tmp_path / "f2-reference-label.db"
    content = avd_changed_bundle_content()
    report = _update_report(store, content=content)
    label = _canonical_sha({"reference_code": "app/updater/reference.py"})
    assert label != _reference_code_sha256()
    report["binding"]["reference_code_sha256"] = label
    _assert_rejected_without_write(
        store,
        db_path,
        job_id="f2-reference-label",
        content=content,
        report=report,
        match="reference|proof",
    )


def test_rollback_requires_fixed_profile_admission(tmp_path) -> None:
    """F2：rollback 目标也必须通过五税固定 profile 准入。"""
    from app.rules.store import RuleStore

    store = RuleStore(tmp_path / "f2-rollback-profile.db")
    store.publish(initial_content(), evidence_refs=[])
    corrupted = initial_content()
    for profile in corrupted["frozen_semantics"]["profiles"]:
        if profile["profile_id"] == "property_per_unit_floor":
            profile["rule"] = "tampered rollback target profile"
            break
    else:  # pragma: no cover - fixture contract
        raise AssertionError("property_per_unit_floor profile missing")
    target = store.publish(corrupted, evidence_refs=[])
    store.publish(initial_content(), evidence_refs=[])
    before = store.current()
    db_path = tmp_path / "f2-rollback-profile.db"
    rollback_audits = _audit_count(db_path, "rollback")
    _publish_update, rollback_to = _publisher()
    with pytest.raises((ValueError, AppError)) as excinfo:
        rollback_to(
            store,
            target_bundle_id=target["bundle_id"],
            reason="profile 准入负例",
        )
    assert "profile" in str(excinfo.value).lower(), (
        f"rollback must reject target with tampered fixed profile: "
        f"{excinfo.value!r}"
    )
    assert store.current() == before, "rejected rollback must not switch pointer"
    assert _audit_count(db_path, "rollback") == rollback_audits, (
        "rejected rollback must not write rollback audit"
    )
