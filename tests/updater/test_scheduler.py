"""REQ-17 调度契约（M5 Red；SDD §5 调度行＋Annex C C6 调度时钟／C7.9 SOURCE 状态），
另含 REQ-18 的可用性/降级两测（offline notice／known-change 拒算）。

REQ: 17（§9：月历＋月末截断＋锚定/重锚显式；首启即查；逾期单次合并补查；
手动同管线＋singleflight＋有界退避；分源/全局 attempt-success 分离、部分失败
不推进全局成功；checked-ok≠published；不发财务数据、不依赖 LLM；单进程）。
REQ: 18（§5 降级行：离线已验证快照可算＋notice；已知变更/过期→拒绝受影响期间）。
规格锚点:
  - SDD §5 调度字段表：global_attempt_at／global_success_at／next_due_at／
    anchor_event／source_checks[i].{attempt_at,success_at,http_outcome,
    snapshot_digest}／publish_outcome／backoff_until。
  - Annex C C6.1–C6.6（重锚链 31Jan→28Feb→28Mar；闰 29Feb→29Mar；
    31May→30Jun→30Jul；实际完成日重锚；退避 60min×2 封顶 24h；时钟可注入）。
  - Annex C C7.9（SOURCE 状态 enter/clear；到期≠过期；仅 trusted_offline
    允许继续计算；related_change_uncertain/expired 阻断受影响期间）。
  - Annex C C5.8（audit_log 的 check 类含 scheduler/reanchor 事件）。
  - Annex C C1（假件进程内、不增第三方库；注入假时钟/假出站/临时 DB）。
期望值来源: 纯结构/时钟算术（月末截断与重锚链来自 C6.1 评审确认链），无金额期望
  （唯一数值 3000/3500 为测试宇宙结构性 marker，非规则数据）。

【拟名】被测契约（app/updater/scheduler.Scheduler，Green 阶段须按测试实现，
不得要求测试改写）:
  - Scheduler(store, *, clock, outbound, sources)
      clock: 注入时钟（clock.now() -> tz-aware datetime，Asia/Hong_Kong）；
      outbound: 一切出站经 outbound.fetch(url, **kwargs) -> bytes；
      sources: allowlist，元素 {"source_id", "url", "manifest"}；
      不得对 sources 之外的 URL 出站。
  - startup() -> job | None：首次启动（无 global_success_at）→ 立即生成并运行
    检查作业（kind="startup_initial"）；启动时 now ≥ next_due_at → 单次合并
    补查（kind="startup_catchup"，不按错过月数重复）；否则返回 None。
  - tick() -> job | None：到期触发；退避期内返回 None 且不出站（无忙循环）。
  - trigger_manual() -> job：手动触发与计划同一管线；已有进行中作业 →
    立即返回现有作业（同 job_id、in_progress=True，不重跑；API 层映射
    E_UPDATE_IN_PROGRESS/202）；退避不抑制手动触发（用户主动重验）。
  - job: {"job_id": str, "kind": "startup_initial|startup_catchup|scheduled|manual",
         "status": "running|succeeded|failed", "in_progress": bool}。
  - status() -> {
      "global_attempt_at": datetime|None,   # 本轮尝试开始（每轮唯一）
      "global_success_at": datetime|None,   # 仅全部来源 fetch+parse+classify 完成才推进
      "next_due_at": datetime|None,         # global_success_at+1 格历月（月末截断）；
                                            # 无成功 → None＝即到期
      "anchor_event": "startup_initial|startup_catchup|scheduled|manual|reanchor"|None,
      "publish_outcome": "published|quarantined|none"|None,  # 与检查结果独立
      "published_bundle_id": str|None, "quarantine_reason": str|None,
      "backoff_until": datetime|None,       # 60min×2 封顶 24h；仅进程内
      "sources": [{"source_id", "attempt_at", "success_at", "http_outcome",
                   "snapshot_digest"}],     # http_outcome: "ok"|"unreachable"|…
      "active_job": job|None}
  - source_states() -> dict[source_id, list[str]]：各源活动 SOURCE 状态
    （C7.9 枚举；空列表＝无状态/健康）。
  - availability(period) -> {"mode": "ok"|"trusted_offline"|"blocked",
      "notice": str|None, "states": [str]}：period 为课税年度键；
      trusted_offline＝允许计算＋中文 notice；blocked＝受影响期间拒算＋说明。
  - 手动/迟后完成以实际完成日重锚（anchor_event="reanchor"），reanchor 事件
    写入 audit_log（event_type="check"，detail 含 "reanchor"）。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.scheduler（调度器缺失）」。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from _fakes import (
    BUDGET_DOC_EXPIRED_NOTICE,
    BUDGET_DOC_PROPOSAL_NOTICE,
    BUDGET_DOC_UNCERTAIN_NOTICE,
    BUDGET_URL,
    FINANCIAL_KEYS,
    MODEL_ENDPOINTS,
    SDO_INDEX_DOC,
    SDO_INDEX_URL,
    FakeClock,
    FakeOutbound,
    base_documents,
    hk,
    make_scheduler,
    seed_published_store,
)


def _count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _audit_details(db_path: Path, event_type: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE event_type = ?",
            (event_type,),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def _src(status: dict) -> dict:
    return {entry["source_id"]: entry for entry in status["sources"]}


def _wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待条件超时（假出站应已收到抓取请求）")


def test_first_startup_due(tmp_path) -> None:
    """首次启动即到期：无 global_success_at → 立即 startup_initial 检查。"""
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    sched = make_scheduler(seed_published_store(tmp_path), clock=clock, outbound=outbound)

    status = sched.status()
    assert status["global_success_at"] is None
    assert status["next_due_at"] is None, "无成功检查＝即到期（§5 字段表）"

    job = sched.startup()
    assert job is not None
    assert job["kind"] == "startup_initial"
    assert job["status"] == "succeeded"

    after = sched.status()
    assert after["global_success_at"] == clock.now()
    assert after["anchor_event"] == "startup_initial"
    assert after["next_due_at"] == hk(2026, 4, 2)  # 完成日 + 1 格历月


def test_month_end_clamp_31jan_feb28_next_anchor_mar(tmp_path) -> None:
    """C6.1 重锚链：31Jan→28Feb→锚 28Mar；闰 31Jan'28→29Feb→29Mar；31May→30Jun→30Jul。"""
    # —— 链 1：锚 2026-01-31 → 到期 2026-02-28（月末截断）→ 完成后锚 2026-03-28
    clock = FakeClock(hk(2026, 1, 31))
    sched = make_scheduler(
        seed_published_store(tmp_path, "chain1.db"),
        clock=clock,
        outbound=FakeOutbound(base_documents()),
    )
    sched.startup()
    assert sched.status()["next_due_at"] == hk(2026, 2, 28)

    clock.set(hk(2026, 2, 28))
    job = sched.tick()
    assert job is not None and job["status"] == "succeeded"
    status = sched.status()
    assert status["global_success_at"] == hk(2026, 2, 28)
    assert status["next_due_at"] == hk(2026, 3, 28), (
        "完成后按实际完成日重锚（2026-03-28），不得保留 31 日（C6.1）"
    )

    # —— 链 2（闰年）：锚 2028-01-31 → 到期 2028-02-29 → 下一锚 2028-03-29
    clock2 = FakeClock(hk(2028, 1, 31))
    sched2 = make_scheduler(
        seed_published_store(tmp_path, "chain2.db"),
        clock=clock2,
        outbound=FakeOutbound(base_documents()),
    )
    sched2.startup()
    assert sched2.status()["next_due_at"] == hk(2028, 2, 29)
    clock2.set(hk(2028, 2, 29))
    job2 = sched2.tick()
    assert job2 is not None and job2["status"] == "succeeded"
    assert sched2.status()["next_due_at"] == hk(2028, 3, 29)

    # —— 链 3：锚 2026-05-31 → 到期 2026-06-30 → 下一锚 2026-07-30（C6.5）
    clock3 = FakeClock(hk(2026, 5, 31))
    sched3 = make_scheduler(
        seed_published_store(tmp_path, "chain3.db"),
        clock=clock3,
        outbound=FakeOutbound(base_documents()),
    )
    sched3.startup()
    assert sched3.status()["next_due_at"] == hk(2026, 6, 30)
    clock3.set(hk(2026, 6, 30))
    job3 = sched3.tick()
    assert job3 is not None and job3["status"] == "succeeded"
    assert sched3.status()["next_due_at"] == hk(2026, 7, 30)


def test_overdue_single_coalesced_catchup(tmp_path) -> None:
    """逾期多个月：启动只做单次合并补查（每源恰抓取一次），不按月数重复。"""
    db_path = tmp_path / "coalesce.db"
    clock = FakeClock(hk(2026, 1, 31))
    outbound = FakeOutbound(base_documents())
    store = seed_published_store(tmp_path, "coalesce.db")
    sched = make_scheduler(store, clock=clock, outbound=outbound)
    sched.startup()  # 成功锚 2026-01-31 → 到期 2026-02-28

    clock.set(hk(2026, 6, 15))  # 逾期四个半月：模拟重启后的新调度器实例
    restarted = make_scheduler(store, clock=clock, outbound=outbound)
    outbound.requests.clear()

    job = restarted.startup()
    assert job is not None
    assert job["kind"] == "startup_catchup", "逾期启动＝单次合并补查作业"
    assert job["status"] == "succeeded"

    per_url = Counter(request["url"] for request in outbound.requests)
    assert per_url[BUDGET_URL] == 1, (
        f"逾期 N 月只单次合并补查（不按月数重复抓取）：{dict(per_url)}"
    )
    assert per_url[SDO_INDEX_URL] == 1

    # update_jobs 恰两行（initial＋catchup）——逾期未产生逐月作业
    assert _count_rows(db_path, "update_jobs") == 2
    # 补查按实际完成日重锚
    assert restarted.status()["next_due_at"] == hk(2026, 7, 15)


def test_manual_same_pipeline_singleflight(tmp_path) -> None:
    """手动与计划同管线；进行中再触发 → 立即返回同一 job_id（202 幂等语义）。"""
    db_path = tmp_path / "singleflight.db"
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    sched = make_scheduler(
        seed_published_store(tmp_path, "singleflight.db"),
        clock=clock,
        outbound=outbound,
    )

    # 假出站阻塞首个手动触发，模拟长跑检查
    outbound.hold = threading.Event()
    release = threading.Timer(2.0, outbound.hold.set)
    release.start()
    results: dict[str, dict] = {}
    first_thread = threading.Thread(
        target=lambda: results.setdefault("first", sched.trigger_manual())
    )
    first_thread.start()
    _wait_for(lambda: bool(outbound.requests))

    active = sched.status()["active_job"]
    assert active is not None and active["status"] == "running"

    # 进行中再触发：不得重跑、不得阻塞等待首个作业完成
    second_thread = threading.Thread(
        target=lambda: results.setdefault("second", sched.trigger_manual())
    )
    second_thread.start()
    second_thread.join(timeout=15)
    assert not second_thread.is_alive(), (
        "singleflight：进行中再触发必须立即返回现有作业，不得排队重跑或阻塞"
    )
    second = results["second"]
    assert second["job_id"] == active["job_id"], "再触发返回同一 job_id"
    assert second["in_progress"] is True, "再触发时作业仍在进行（API 层映射 202）"

    outbound.hold.wait()
    first_thread.join(timeout=10)
    assert not first_thread.is_alive()
    first = results["first"]
    assert first["job_id"] == active["job_id"]
    assert first["status"] == "succeeded"

    # 全程只产生一条 update_jobs 行（singleflight，无重复作业）
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT kind, status FROM update_jobs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"进行中再触发不得新建作业行（实际 {rows!r}）"
    assert rows[0][0] == "manual"

    # 手动与计划同管线：手动完成的检查同样推进全局成功并重锚
    status = sched.status()
    assert status["global_success_at"] == clock.now()
    assert status["anchor_event"] == "manual"
    assert status["next_due_at"] == hk(2026, 4, 2)


def test_attempt_vs_success_per_source_global(tmp_path) -> None:
    """分源 attempt/success 分离；任一来源失败 → 全局成功时间不推进。"""
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    sched = make_scheduler(
        seed_published_store(tmp_path),
        clock=clock,
        outbound=outbound,
    )
    sched.startup()  # 成功锚 2026-03-02 → 到期 2026-04-02

    clock.set(hk(2026, 4, 2))
    outbound.documents[SDO_INDEX_URL] = ConnectionError("网络不可达（脚本化单源失败）")
    job = sched.tick()
    assert job is not None and job["status"] == "failed"

    status = sched.status()
    sources = _src(status)
    assert sources["ird_budget"]["attempt_at"] == hk(2026, 4, 2)
    assert sources["ird_budget"]["success_at"] == hk(2026, 4, 2)
    assert sources["ird_budget"]["http_outcome"] == "ok"
    index = sources["ird_sdo_index"]
    assert index["attempt_at"] == hk(2026, 4, 2), "失败源仍有本轮 attempt"
    assert index["success_at"] is None, "失败源 success 不落（分源分离）"
    assert index["http_outcome"] == "unreachable"

    # 全局：本轮 attempt 前进；部分失败 → global_success_at 不推进
    assert status["global_attempt_at"] == hk(2026, 4, 2)
    assert status["global_success_at"] == hk(2026, 3, 2), (
        "任一来源缺失/失败 → 全局成功时间不推进（§5）"
    )
    assert status["publish_outcome"] is None, "检查未完成 → 无发布判定"


def test_checked_ok_distinct_from_published(tmp_path) -> None:
    """checked-ok ≠ published：检查成功独立记录；无变更 → 不发布、指针不动。"""
    db_path = tmp_path / "checked_ok.db"
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    store = seed_published_store(tmp_path, "checked_ok.db")
    before_pointer = store.current()
    pubs_before = _count_rows(db_path, "publications")

    sched = make_scheduler(store, clock=clock, outbound=outbound)
    job = sched.startup()
    assert job is not None and job["status"] == "succeeded"

    status = sched.status()
    assert status["global_success_at"] == clock.now(), "全部来源完成 → 检查成功"
    assert status["publish_outcome"] == "none", "无变更 → publish_outcome=none（不发布）"
    assert status["published_bundle_id"] is None

    assert store.current()["bundle_id"] == before_pointer["bundle_id"], (
        "检查成功不得移动 current pointer（发布是独立事件）"
    )
    assert _count_rows(db_path, "publications") == pubs_before, (
        "无变更检查不得新增 publication 行"
    )


def test_manual_reanchor_on_actual_completion(tmp_path) -> None:
    """手动早完成/迟后完成一律按实际完成时间戳重锚，并写 reanchor 审计事件。"""
    db_path = tmp_path / "reanchor.db"
    clock = FakeClock(hk(2026, 1, 31))
    sched = make_scheduler(
        seed_published_store(tmp_path, "reanchor.db"),
        clock=clock,
        outbound=FakeOutbound(base_documents()),
    )
    sched.startup()  # 锚 2026-01-31 → 到期 2026-02-28

    # —— 早于到期的手动成功：按实际完成日 2026-02-10 重锚（C6.2）
    clock.set(hk(2026, 2, 10))
    job = sched.trigger_manual()
    assert job["status"] == "succeeded"
    status = sched.status()
    assert status["global_success_at"] == hk(2026, 2, 10)
    assert status["next_due_at"] == hk(2026, 3, 10), (
        "手动早完成 → 下一锚＝实际完成日 + 1 月（2026-03-10），非原到期日 + 1 月"
    )
    assert status["anchor_event"] == "reanchor"
    check_events = _audit_details(db_path, "check")
    assert check_events and any("reanchor" in detail for detail in check_events), (
        "reanchor 事件须显式入 audit_log（check 类，§5/Annex C C5.8）"
    )

    # —— 迟后完成（到期 2026-03-10 已过）：同样按实际完成时间戳重锚
    clock.set(hk(2026, 4, 20))
    late_job = sched.tick()
    assert late_job is not None and late_job["status"] == "succeeded"
    assert sched.status()["next_due_at"] == hk(2026, 5, 20)


def test_no_financial_data_no_llm(tmp_path) -> None:
    """更新路径不携带任何财务数据、不调用模型端点；仅 allowlist 官方 URL 出站。"""
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    sched = make_scheduler(
        seed_published_store(tmp_path),
        clock=clock,
        outbound=outbound,
    )
    sched.startup()
    clock.set(hk(2026, 4, 2))
    sched.tick()

    assert outbound.requests, "应已发生 allowlist 抓取"
    allowlist = {BUDGET_URL, SDO_INDEX_URL}
    for request in outbound.requests:
        assert request["url"] in allowlist, (
            f"仅 allowlist 精确 https URL 可出站（REQ-17）：{request!r}"
        )
        assert not any(
            request["url"].startswith(endpoint) for endpoint in MODEL_ENDPOINTS
        ), f"更新路径不得调用模型端点：{request!r}"
        blob = json.dumps(request.get("kwargs", {}), ensure_ascii=False, default=str)
        for key in FINANCIAL_KEYS:
            assert key not in blob, (
                f"更新出站不得携带财务数据字段 {key!r}（REQ-17）：{request!r}"
            )


def test_backoff_bounded_in_process(tmp_path) -> None:
    """失败退避 60min×2 封顶 24h、退避期无忙循环；退避状态仅进程内不持久。"""
    db_path = tmp_path / "backoff.db"
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    store = seed_published_store(tmp_path, "backoff.db")
    sched = make_scheduler(store, clock=clock, outbound=outbound)
    sched.startup()

    clock.set(hk(2026, 4, 2))
    outbound.unreachable = True  # 之后每次检查都失败

    def _drive_failure() -> tuple[float, object]:
        started = clock.now()
        job = sched.tick()
        assert job is not None and job["status"] == "failed"
        backoff_until = sched.status()["backoff_until"]
        assert backoff_until is not None, "失败后必须进入退避"
        return (backoff_until - started).total_seconds() / 60.0, backoff_until

    deltas: list[float] = []
    minutes, backoff_until = _drive_failure()
    deltas.append(minutes)

    # 退避期内：tick 不产作业、不出站（无忙循环）
    outbound.requests.clear()
    clock.advance(minutes=30)
    assert sched.tick() is None
    assert not outbound.requests, "退避期内不得重复抓取（无忙循环）"

    for _ in range(10):
        clock.set(backoff_until)  # type: ignore[arg-type]
        clock.advance(minutes=1)
        minutes, backoff_until = _drive_failure()
        deltas.append(minutes)

    assert deltas[0] == 60, f"首次退避基数为 60min（实际 {deltas[0]}）"
    assert deltas[1] == 120, f"第二次退避 ×2＝120min（实际 {deltas[1]}）"
    assert all(minutes <= 1440 for minutes in deltas), f"退避封顶 24h：{deltas}"
    assert deltas[-1] == 1440, f"连续失败后退避收敛于 24h 封顶（实际 {deltas[-1]}）"

    # 退避仅进程内：同库新实例（模拟重启）退避清零
    restarted = make_scheduler(store, clock=clock, outbound=FakeOutbound(base_documents()))
    assert restarted.status()["backoff_until"] is None, (
        "退避状态不得持久化到 DB（进程内不持久，§5）"
    )


def test_source_states_enter_clear_and_due_is_not_expired(tmp_path) -> None:
    """SOURCE enter/clear：不可达→trusted_offline；恢复重验一致→clear；到期≠过期。"""
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    sched = make_scheduler(
        seed_published_store(tmp_path),
        clock=clock,
        outbound=outbound,
    )
    sched.startup()  # 建立已核验快照基线
    assert sched.source_states() == {"ird_budget": [], "ird_sdo_index": []}

    # —— enter：网络不可达，但最近已核验快照仍现行 → trusted_offline（C7.9）
    clock.set(hk(2026, 4, 2))
    outbound.unreachable = True
    failed_job = sched.tick()
    assert failed_job is not None and failed_job["status"] == "failed"
    states = sched.source_states()
    assert "trusted_offline" in states["ird_budget"], (
        f"网络不可达＋已核验快照在档 → enter trusted_offline：{states!r}"
    )
    assert "trusted_offline" in states["ird_sdo_index"]
    assert all("expired" not in entries for entries in states.values()), (
        "离线不是过期（expired 仅由来源证据判定）"
    )

    # —— clear：网络恢复且重验一致（手动触发不受退避抑制）
    outbound.unreachable = False
    recovered = sched.trigger_manual()
    assert recovered["status"] == "succeeded"
    assert sched.source_states() == {"ird_budget": [], "ird_sdo_index": []}

    # —— 到期 ≠ 过期：next_due_at 到达只是调度事件，不进入任何阻断状态
    due_at = sched.status()["next_due_at"]
    assert due_at is not None
    clock.set(due_at)
    at_due = sched.source_states()
    assert all(entries == [] for entries in at_due.values()), (
        f"到期未检查不得进入 expired/任何阻断状态（C7.9）：{at_due!r}"
    )


def test_offline_verified_snapshot_with_notice(tmp_path) -> None:
    """REQ-18：离线时已验证快照可计算＋notice；恢复重验一致后复原。"""
    clock = FakeClock(hk(2026, 3, 2))
    outbound = FakeOutbound(base_documents())
    store = seed_published_store(tmp_path)
    sched = make_scheduler(store, clock=clock, outbound=outbound)
    sched.startup()  # 已核验快照在档

    clock.set(hk(2026, 4, 2))
    outbound.unreachable = True
    sched.tick()  # 检查失败 → trusted_offline

    availability = sched.availability("2025_26")
    assert availability["mode"] == "trusted_offline", (
        "离线＋已验证快照现行 → 允许计算并告警（非阻断）："
        f"{availability!r}"
    )
    assert availability["mode"] != "blocked"
    notice = availability.get("notice")
    assert isinstance(notice, str) and notice, "必须携带中文 notice"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in notice)

    # 已验证快照仍支撑现行计算：指针完整可解析
    assert store.current()["bundle_id"]

    # 恢复重验一致 → 恢复 ok、notice 消除
    outbound.unreachable = False
    assert sched.trigger_manual()["status"] == "succeeded"
    recovered = sched.availability("2025_26")
    assert recovered["mode"] == "ok"
    assert not recovered["notice"]


def test_known_change_or_expired_refuses_affected(tmp_path) -> None:
    """REQ-18：已知未核变更/过期 → 拒绝受影响期间（非泛化警告）；提案不阻断。"""
    # —— (1) related_change_uncertain：已知相关变更且生效状态未核实 → 阻断受影响期间
    uncertain = FakeOutbound(
        {BUDGET_URL: BUDGET_DOC_UNCERTAIN_NOTICE, SDO_INDEX_URL: SDO_INDEX_DOC}
    )
    sched1 = make_scheduler(
        seed_published_store(tmp_path, "uncertain.db"),
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=uncertain,
    )
    job1 = sched1.startup()
    assert job1 is not None and job1["status"] == "succeeded", (
        "未核实状态是分类结果，不是来源失败（检查可完成）"
    )
    assert "related_change_uncertain" in sched1.source_states()["ird_budget"]
    blocked = sched1.availability("2026_27")
    assert blocked["mode"] == "blocked", "受影响期间必须拒算（REQ-18）"
    assert blocked["notice"], "拒算必须携带中文说明"
    assert any("\u4e00" <= ch <= "\u9fff" for ch in blocked["notice"])
    assert sched1.availability("2025_26")["mode"] == "ok", (
        "未受影响期间不得泛化阻断"
    )

    # —— (2) expired：官方证据标注失效且适用期已过 → 阻断（不得宣称现行）
    expired = FakeOutbound(
        {BUDGET_URL: BUDGET_DOC_EXPIRED_NOTICE, SDO_INDEX_URL: SDO_INDEX_DOC}
    )
    sched2 = make_scheduler(
        seed_published_store(tmp_path, "expired.db"),
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=expired,
    )
    job2 = sched2.startup()
    assert job2 is not None and job2["status"] == "succeeded"
    assert "expired" in sched2.source_states()["ird_budget"]
    assert sched2.availability("2024_25")["mode"] == "blocked"

    # —— (3) 对照：预算提案（proposal_not_current）→ 现行计算继续，不阻断
    proposal = FakeOutbound(
        {BUDGET_URL: BUDGET_DOC_PROPOSAL_NOTICE, SDO_INDEX_URL: SDO_INDEX_DOC}
    )
    sched3 = make_scheduler(
        seed_published_store(tmp_path, "proposal.db"),
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=proposal,
    )
    job3 = sched3.startup()
    assert job3 is not None and job3["status"] == "succeeded"
    availability = sched3.availability("2026_27")
    assert availability["mode"] != "blocked", (
        "提案不作现行、也不阻断现行计算（C7.9 proposal_not_current）"
    )
    assert "proposal_not_current" in availability["states"]
