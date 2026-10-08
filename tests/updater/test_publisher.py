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

import sqlite3

import pytest

from app.core.errors import AppError

from _fakes import (
    FakeClock,
    FakeOutbound,
    base_documents,
    changed_bundle_content,
    hk,
    initial_content,
    make_scheduler,
    seed_published_store,
)

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


def test_update_publish_atomic_and_audit(tmp_path) -> None:
    """更新发布＝单事务＋audit（publish 类、含 job_id）；失败/未过门禁零部分状态。"""
    publish_update, _rollback = _publisher()
    db_path = tmp_path / "publish.db"
    store = seed_published_store(tmp_path, "publish.db")
    before = store.current()

    # —— 成功发布：指针切换＋publications＋publish 类 audit（含 job_id 关联）
    published = publish_update(
        store,
        job_id="job-m5-1",
        content=changed_bundle_content("3500"),
        evidence_refs=[],
        validation_report=REPORT_PUBLISH_OK,
    )
    current = store.current()
    assert current["bundle_id"] == published["bundle_id"]
    assert current["bundle_id"] != before["bundle_id"]
    publish_events = _audit_details(db_path, "publish")
    assert publish_events, "更新发布必须写 publish 类 audit 事件（C5.8）"
    assert any(
        "job-m5-1" in detail and published["bundle_id"] in detail
        for detail in publish_events
    ), f"publish audit 须关联 job_id 与 bundle_id：{publish_events!r}"

    # —— 原子性：非法 content → 整体拒绝，零部分状态
    pubs_after = len(_rows(db_path, "publications"))
    audits_after = len(_rows(db_path, "audit_log"))
    with pytest.raises((ValueError, AppError)):
        publish_update(
            store,
            job_id="job-m5-2",
            content={"rules_schema_version": "1.0.0"},  # 缺 C2.8 封闭字段
            evidence_refs=[],
            validation_report=REPORT_PUBLISH_OK,
        )
    assert len(_rows(db_path, "publications")) == pubs_after
    assert len(_rows(db_path, "audit_log")) == audits_after
    assert store.current()["bundle_id"] == current["bundle_id"]

    # —— 门禁报告门（C8.4）：空报告（零通过）与隔离报告 → 拒绝发布
    with pytest.raises((ValueError, AppError)):
        publish_update(
            store,
            job_id="job-m5-3",
            content=changed_bundle_content("3600"),
            evidence_refs=[],
            validation_report=REPORT_EMPTY_TESTS,
        )
    with pytest.raises((ValueError, AppError)):
        publish_update(
            store,
            job_id="job-m5-4",
            content=changed_bundle_content("3600"),
            evidence_refs=[],
            validation_report=REPORT_QUARANTINED,
        )
    assert store.current()["bundle_id"] == current["bundle_id"], (
        "未通过门禁的候选不得发布（保持现行版本）"
    )


def test_rollback_audited_pointer_not_time_travel(tmp_path) -> None:
    """回滚＝审计化指针回退；不把旧规则标现行——受影响期间拒算并说明。"""
    publish_update, rollback_to = _publisher()
    db_path = tmp_path / "rollback.db"
    store = seed_published_store(tmp_path, "rollback.db")
    target = store.current()  # A＝INITIAL（此前已发布）

    newer = publish_update(
        store,
        job_id="job-m5-r1",
        content=changed_bundle_content("3500"),
        evidence_refs=[],
        validation_report=REPORT_PUBLISH_OK,
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
