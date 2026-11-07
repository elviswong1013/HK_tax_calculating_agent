"""P3-W1a REQ-12：stop-auto observer pending 状态机（006 §5.6 十二节点）。

REQ: REQ-12（005 §15.5；006:268）——六条旧 salary/PA 路径仅观察；观察失败/
变更/离线一律落 durable `observer_pending:` 状态（sticky）；普通 notice 同步、
同值、重启、回滚、后续成功不得清除；受控 Resolution 须匹配 first 原因、实际
最新事件与当前 baseline；public `source_states()`/availability 映射为既有
unknown/related_change_uncertain，不能出现未阻断读窗口。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - Scheduler 来源条目可携带 `observer_slots`（七字段旧 slot 描述，不是
    manifest 字段）；真实 manifest.slots 不含旧路径。
  - 每次观察先 durable 提交 `updater_source_states.state =
    "observer_pending:<mapped path>"`（period=YA），详情写
    `updater_meta` key `observer_pending/{source_id}/{period}/{path}`，含
    `sticky/first_observation/latest_observation`（Event 带 sequence/
    baseline 对/kind/detail/event_digest）。
  - 值确实不同 → sticky related_change_uncertain；manifest/parse/normalize/
    失败 → sticky unknown；`"18000.00" ≡ "18000"`；货币前缀/逗号不得清理。
  - `_sync_source_states` 的 SELECT/delete 集合排除 observer 行。
  - `app.updater.observer.resolve_observer_pending(store, *, source_id, path,
    period, resolution, clock=None)`：唯一清除入口；不匹配 → 拒绝且不清。

Red 失败原因（当前）：无 observer 状态/详情/Resolution API。
"""

from __future__ import annotations

import hashlib
import json
import threading

import pytest

from _fakes import (
    BUDGET_DOC_CHANGED,
    BUDGET_DOC_OK,
    BUDGET_DOC_UNCERTAIN_NOTICE,
    BUDGET_URL,
    FakeClock,
    FakeOutbound,
    SDO_INDEX_DOC,
    SDO_INDEX_URL,
    STOP_AUTO_TARGETS,
    changed_bundle_content,
    hk,
    make_scheduler,
    observer_meta,
    observer_pending_rows,
    observer_sources,
    seed_published_store,
)

CAP_OLD_PATH = "salaries.reductions.2025_26.cap"
CAP_TARGET = STOP_AUTO_TARGETS[CAP_OLD_PATH]
PERIOD = "2025_26"
BUDGET_DOC_TRAILING_ZERO = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>3000.00</td></tr>"
    b"</table>"
    b"</body></html>"
)
BUDGET_DOC_UNAPPROVED_CURRENCY = (
    b"<html><body>"
    b"<h2>Salaries Tax Reduction</h2>"
    b"<table>"
    b"<tr><th>Year of assessment</th><th>Reduction</th><th>Cap</th></tr>"
    b"<tr><td>2025/26</td><td>100%</td><td>HK$3,000</td></tr>"
    b"</table>"
    b"</body></html>"
)


def _make(tmp_path, name: str, documents: dict, *, clock=None):
    store = seed_published_store(tmp_path, name)
    sched = make_scheduler(
        store,
        clock=clock or FakeClock(hk(2026, 3, 2)),
        outbound=FakeOutbound(documents),
        sources=observer_sources(),
    )
    return store, sched


def _base_docs(budget_doc: bytes = BUDGET_DOC_CHANGED) -> dict:
    return {BUDGET_URL: budget_doc, SDO_INDEX_URL: SDO_INDEX_DOC}


def _pending_for(store, source_id: str = "ird_budget"):
    """返回 (state_row, detail)，容忍路径前缀差异。"""
    rows = [
        row
        for row in observer_pending_rows(store)
        if row[0] == source_id and PERIOD in row[1] and CAP_TARGET.split(".")[-2] in row[1]
    ]
    connect = store._connect()
    try:
        raw = connect.execute(
            "SELECT key, value_json FROM updater_meta"
            " WHERE key LIKE 'observer_pending/%' ORDER BY rowid"
        ).fetchall()
    finally:
        connect.close()
    detail = None
    for key, value in raw:
        if not str(key).startswith(f"observer_pending/{source_id}/{PERIOD}/"):
            continue
        if CAP_OLD_PATH in str(key) or CAP_TARGET in str(key):
            detail = json.loads(value)
            break
    return rows, detail


def _sha(payload) -> str:
    from app.core.hashing import canonical_json_bytes

    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _proof_body(
    *,
    source_id: str = "ird_budget",
    path: str = CAP_OLD_PATH,
    period: str = PERIOD,
    disposition: str = "no_effect",
) -> dict:
    return {
        "source_id": source_id,
        "path": path,
        "period": period,
        "evidence_refs": ["fact:test:observer-resolution"],
        "disposition": disposition,
        "statement": "independent observer resolution proof (test universe)",
    }


def _clear_resolution(
    store,
    detail: dict,
    *,
    latest_digest: str | None = None,
    disposition: str = "no_effect",
    target_bundle_hash: str | None = None,
    approved_bundle: dict | None = None,
    proof: dict | None = None,
    proof_sha256: str | None = None,
) -> dict:
    """Resolution fixture with independent proof body (canonical-JSON digest).

    Contract: `proof_sha256` is the canonical-JSON SHA-256 of `proof`; proof
    binds source_id/path/period/disposition, and approved_bundle carries the
    approved target bundle id/hash.
    """
    baseline = store.current()
    proof_body = (
        dict(proof)
        if proof is not None
        else _proof_body(disposition=disposition)
    )
    return {
        "disposition": disposition,
        "pending_origin_digest": detail["first_observation"]["event_digest"],
        "latest_event_digest": latest_digest
        or detail["latest_observation"]["event_digest"],
        "expected_current_bundle_id": baseline["bundle_id"],
        "expected_current_bundle_hash": baseline["bundle_hash"],
        "proof_refs": ["proof:test:observer-resolution"],
        "proof": proof_body,
        "proof_sha256": proof_sha256 or _sha(proof_body),
        "approved_bundle": approved_bundle,
        "target_bundle_hash": target_bundle_hash,
    }


def test_002_observer_pending_survives_notice_sync(tmp_path) -> None:
    store, sched = _make(tmp_path, "notice.db", _base_docs())
    assert sched.startup()["status"] == "succeeded"
    rows, detail = _pending_for(store)
    assert rows, "变更观察须落 observer_pending 状态行"
    assert isinstance(detail, dict) and detail.get("latest_observation")
    assert detail.get("sticky") is True, f"值变更须 sticky：{detail!r}"
    assert detail["latest_observation"]["kind"] == "value_changed"
    assert "related_change_uncertain" in sched.source_states()["ird_budget"], (
        "public 映射须给既有阻断状态（005 §15.5）"
    )

    # #3：sticky observer 只按普通月度 due 复核；不得引入每日复核语义。
    sched._clock.advance(days=1)
    assert sched.tick() is None, (
        "sticky observer pending must not add a daily recheck outside the monthly "
        "next_due schedule"
    )
    rows_unchanged, detail_unchanged = _pending_for(store)
    assert rows_unchanged, "non-due tick must not clear observer_pending"
    assert detail_unchanged["sticky"] is True

    # 时间注入到月度 due（startup 完成日 2026-03-02 → next_due 2026-04-02）。
    sched._clock.set(hk(2026, 4, 2))
    sched._outbound.documents[BUDGET_URL] = BUDGET_DOC_UNCERTAIN_NOTICE
    assert sched.tick()["status"] == "succeeded"
    rows_after, detail_after = _pending_for(store)
    assert rows_after, "普通 notice 同步不得删除 observer_pending 行"
    assert detail_after["sticky"] is True
    assert (
        detail_after["first_observation"]["event_digest"]
        == detail["first_observation"]["event_digest"]
    ), "first 原因不得被后续 notice 同步降级/替换"
    assert "related_change_uncertain" in sched.source_states()["ird_budget"]


def test_002_observer_pending_same_value_does_not_clear(tmp_path) -> None:
    store, sched = _make(tmp_path, "same.db", _base_docs())
    sched.startup()
    pointer = store.current()["bundle_id"]
    rows, detail = _pending_for(store)
    assert rows and detail.get("sticky") is True

    sched._clock.advance(days=35)
    sched._outbound.documents[BUDGET_URL] = BUDGET_DOC_OK  # 回到 baseline 3000
    job = sched.tick()
    assert job["status"] == "succeeded"
    rows_after, detail_after = _pending_for(store)
    assert rows_after, "sticky pending 不得因同值观察清除"
    assert detail_after["sticky"] is True
    assert detail_after["latest_observation"]["kind"] == "value_equal"
    assert store.current()["bundle_id"] == pointer, "observer 不得发布/移动指针"


def test_002_observer_pending_survives_failure_restart_rollback(tmp_path) -> None:
    store, sched = _make(tmp_path, "restart.db", _base_docs())
    sched.startup()
    seed_bundle = store.current()["bundle_id"]
    assert _pending_for(store)[0]

    # 重启：新建 Scheduler 仍能看到 pending/阻断
    restarted = make_scheduler(
        store,
        clock=FakeClock(hk(2026, 4, 2)),
        outbound=FakeOutbound({BUDGET_URL: BUDGET_DOC_OK, SDO_INDEX_URL: SDO_INDEX_DOC}),
        sources=observer_sources(),
    )
    assert _pending_for(store)[0], "重启不得清除 observer pending"
    assert "related_change_uncertain" in restarted.source_states()["ird_budget"]

    # 后续失败不得清除
    restarted._outbound.unreachable = True
    assert restarted.tick()["status"] == "failed"
    assert _pending_for(store)[0]

    # 回滚不得清除
    restarted._outbound.unreachable = False
    store.publish(changed_bundle_content("3500"), evidence_refs=[])
    from app.updater.publisher import rollback_to

    rollback_to(store, target_bundle_id=seed_bundle, reason="observer 持久性测试")
    rows, detail = _pending_for(store)
    assert rows and detail["sticky"] is True, "回滚不得清除已知失败 observer pending"


def test_002_observer_clear_requires_matching_resolution(tmp_path) -> None:
    from app.updater.observer import resolve_observer_pending

    store, sched = _make(tmp_path, "resolution.db", _base_docs())
    sched.startup()
    _rows, detail = _pending_for(store)
    assert detail and detail["sticky"] is True

    bad = _clear_resolution(store, detail, latest_digest="sha256:" + "00" * 32)
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=bad,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], "不匹配 latest 事件的 Resolution 不得清除"

    good = _clear_resolution(store, detail)
    resolve_observer_pending(
        store,
        source_id="ird_budget",
        path=CAP_OLD_PATH,
        period=PERIOD,
        resolution=good,
        clock=sched._clock,
    )
    assert not _pending_for(store)[0], "匹配的受控 Resolution 是唯一清除入口"


def test_002_observer_commit_has_no_unblocked_read_gap(tmp_path) -> None:
    """检查提交后到最终结束前，public 读路径不得看到“无状态”窗口。"""

    class _SnapshottingOutbound(FakeOutbound):
        def __init__(self, documents):
            super().__init__(documents)
            self.scheduler = None
            self.snapshots = []

        def fetch(self, url: str, **kwargs):
            if self.scheduler is not None:
                self.snapshots.append(
                    list(
                        self.scheduler.source_states().get("ird_budget") or []
                    )
                )
            return super().fetch(url, **kwargs)

    outbound = _SnapshottingOutbound(_base_docs())
    store = seed_published_store(tmp_path, "gap.db")
    sched = make_scheduler(
        store,
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=outbound,
        sources=observer_sources(),
    )
    outbound.scheduler = sched
    sched.startup()
    assert outbound.snapshots, "前置：观察轮须发生 fetch"
    for snapshot in outbound.snapshots:
        assert isinstance(snapshot, list) and all(
            isinstance(state, str) for state in snapshot
        ), f"source_states() must expose a plain list[str]: {snapshot!r}"
        assert any(
            state in {"unknown", "related_change_uncertain"}
            for state in snapshot
        ), (
            "fetch/解析期间 public 状态须已显示 checking/blocking，不得有无阻断"
            f"读窗口：{snapshot!r}"
        )
        assert not any(
            state.startswith("observer_pending:") for state in snapshot
        ), f"source_states() must not leak internal observer state: {snapshot!r}"


def test_002_observer_anchor_parse_scalar_failures_persist_guard(tmp_path) -> None:
    store, sched = _make(
        tmp_path,
        "anchor.db",
        {BUDGET_URL: b"<html><body><h2>Other</h2></body></html>", SDO_INDEX_URL: SDO_INDEX_DOC},
    )
    job = sched.startup()
    assert job is not None
    rows, detail = _pending_for(store)
    assert rows, "锚定/解析失败须持久 observer pending"
    assert detail["sticky"] is True
    assert detail["latest_observation"]["kind"] == "observation_failed"
    assert detail["latest_observation"]["detail"]["phase"] in {"manifest", "parse"}
    assert sched.availability(PERIOD)["mode"] == "blocked"


def test_002_observer_failure_before_final_commit_leaves_checking_block(tmp_path) -> None:
    sources = observer_sources()
    sources[0]["manifest"].pop("parser_code_hash", None)  # 结构校验失败
    store = seed_published_store(tmp_path, "premanifest.db")
    sched = make_scheduler(
        store,
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=FakeOutbound(_base_docs()),
        sources=sources,
    )
    job = sched.startup()
    assert job["status"] == "failed"
    rows, detail = _pending_for(store)
    assert rows, "manifest 校验前失败也不得跳过 durable checking/unknown 落盘"
    assert detail["sticky"] is True
    assert sched.availability(PERIOD)["mode"] == "blocked"


def test_002_observer_other_source_failure_cannot_skip_guard_commit(tmp_path) -> None:
    store = seed_published_store(tmp_path, "otherfail.db")
    outbound = FakeOutbound(_base_docs())
    # 发现源失败不得跳过 budget 来源的观察 guard 落盘
    outbound.documents.pop(SDO_INDEX_URL)
    sched = make_scheduler(
        store,
        clock=FakeClock(hk(2026, 3, 2)),
        outbound=outbound,
        sources=observer_sources(),
    )
    job = sched.startup()
    assert job["status"] == "failed"
    rows, detail = _pending_for(store)
    assert rows, "另一来源失败不得跳过本来源 observer guard 提交"
    assert detail["sticky"] is True


def test_002_observer_trailing_zero_format_is_noop(tmp_path) -> None:
    store, sched = _make(
        tmp_path, "trailing.db", _base_docs(BUDGET_DOC_TRAILING_ZERO)
    )
    assert sched.startup()["status"] == "succeeded"
    rows, detail = _pending_for(store)
    assert detail is not None, "同值观察仍须留可审计 latest 事件"
    assert detail["sticky"] is False, (
        f"3000.00 经 AmountStr 规范化后≡3000，不得 sticky：{detail!r}"
    )
    assert detail["latest_observation"]["kind"] == "value_equal"
    assert sched.availability(PERIOD)["mode"] != "blocked"


def test_002_observer_unapproved_currency_cleanup_rejected(tmp_path) -> None:
    store, sched = _make(
        tmp_path, "currency.db", _base_docs(BUDGET_DOC_UNAPPROVED_CURRENCY)
    )
    job = sched.startup()
    assert job is not None
    rows, detail = _pending_for(store)
    assert rows, "未批准货币前缀/逗号不得清理，须持久失败"
    assert detail["sticky"] is True
    assert detail["latest_observation"]["kind"] == "observation_failed"
    assert detail["latest_observation"]["detail"]["code"] in {
        "invalid_scalar",
        "parse_error",
    }
    assert sched.availability(PERIOD)["mode"] == "blocked"


def test_002_resolution_must_match_latest_failed_event(tmp_path) -> None:
    from app.updater.observer import resolve_observer_pending

    store, sched = _make(
        tmp_path,
        "latest.db",
        {BUDGET_URL: b"<html><body><h2>Other</h2></body></html>", SDO_INDEX_URL: SDO_INDEX_DOC},
    )
    sched.startup()
    _rows, first = _pending_for(store)
    assert first["sticky"] is True

    # 第二次失败更新 latest（不同事件 digest）
    sched._clock.advance(days=36)
    sched._outbound.documents[BUDGET_URL] = BUDGET_DOC_UNAPPROVED_CURRENCY
    assert sched.tick()["status"] == "failed"
    rows, latest = _pending_for(store)
    assert rows and latest["latest_observation"]["event_digest"] != (
        first["latest_observation"]["event_digest"]
    )

    stale = _clear_resolution(store, latest)
    stale["latest_event_digest"] = first["latest_observation"]["event_digest"]
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=stale,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], "匹配旧失败事件的 Resolution 不得清除"

    good = _clear_resolution(store, latest)
    resolve_observer_pending(
        store,
        source_id="ird_budget",
        path=CAP_OLD_PATH,
        period=PERIOD,
        resolution=good,
        clock=sched._clock,
    )
    assert not _pending_for(store)[0]


def test_002_resolution_baseline_change_cannot_reuse(tmp_path) -> None:
    from app.updater.observer import resolve_observer_pending

    store, sched = _make(tmp_path, "baseline.db", _base_docs())
    sched.startup()
    _rows, detail = _pending_for(store)
    stale_baseline = store.current()

    store.publish(changed_bundle_content("3500"), evidence_refs=[])
    new_baseline = store.current()
    assert new_baseline["bundle_id"] != stale_baseline["bundle_id"]

    stale = _clear_resolution(store, detail)
    stale["expected_current_bundle_id"] = stale_baseline["bundle_id"]
    stale["expected_current_bundle_hash"] = stale_baseline["bundle_hash"]
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=stale,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], "baseline 已变化时不得复用旧 Resolution"

    good = _clear_resolution(store, detail)
    good["expected_current_bundle_id"] = new_baseline["bundle_id"]
    good["expected_current_bundle_hash"] = new_baseline["bundle_hash"]
    resolve_observer_pending(
        store,
        source_id="ird_budget",
        path=CAP_OLD_PATH,
        period=PERIOD,
        resolution=good,
        clock=sched._clock,
    )
    assert not _pending_for(store)[0]


def test_002_observer_read_path_does_not_reenter_store_write_lock(tmp_path) -> None:
    """F1：持 store 非重入写锁时读取 observer 公开状态不得重入死锁（有界）。"""
    store, sched = _make(tmp_path, "lock-read.db", _base_docs())
    assert sched.startup()["status"] == "succeeded"
    assert observer_pending_rows(store), "前置：须存在 observer_pending 行"

    assert store._write_lock.acquire(timeout=1.0), "前置：测试须持有 store 写锁"
    result: dict = {}

    def _read_public_state() -> None:
        try:
            result["availability"] = sched.availability(PERIOD)
            result["states"] = sched.source_states()
        except BaseException as exc:  # noqa: BLE001 - 证据化任何读取异常
            result["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=_read_public_state, daemon=True)
    try:
        worker.start()
        worker.join(timeout=2.0)
        stuck = worker.is_alive()
    finally:
        store._write_lock.release()
        worker.join(timeout=5.0)

    assert not stuck, (
        "observer public read path re-entered the non-reentrant store._write_lock "
        "while the write lock was already held (F1 deadlock); availability/"
        "source_states must not acquire it"
    )
    assert "error" not in result, result.get("error")
    assert result["availability"]["mode"] == "blocked"
    assert "related_change_uncertain" in result["states"]["ird_budget"]


def test_002_source_states_is_plain_list_of_strings(tmp_path) -> None:
    """#2：source_states() 返回真正的 list[str]，不得用迭代改写的 list 子类。"""
    store, sched = _make(tmp_path, "states-shape.db", _base_docs())
    sched.startup()
    states = sched.source_states()
    values = states["ird_budget"]
    assert type(values) is list, (
        "source_states() must return a plain list, not an iterator-rewriting "
        f"list subclass: {type(values)!r}"
    )
    assert all(isinstance(state, str) for state in values), (
        f"source_states() items must be strings: {values!r}"
    )
    assert "related_change_uncertain" in values
    assert not any(state.startswith("observer_pending:") for state in values)


def test_002_observer_internal_suffix_is_data_relative(tmp_path) -> None:
    """F4：内部 state/detail suffix 使用映射后的 data-relative 路径（005:899）。"""
    mapped = STOP_AUTO_TARGETS[CAP_OLD_PATH]
    store, sched = _make(tmp_path, "suffix.db", _base_docs())
    sched.startup()

    rows = observer_pending_rows(store)
    assert any(
        row_source == "ird_budget"
        and row_state == "observer_pending:" + mapped
        and row_period == PERIOD
        for row_source, row_state, row_period in rows
    ), f"observer state suffix must be data-relative {mapped!r}: {rows!r}"
    assert not any("salaries." in row_state for _source, row_state, _period in rows), (
        f"old salary-private path must not enter observer state suffix: {rows!r}"
    )

    detail = observer_meta(store, "ird_budget", PERIOD, mapped)
    assert isinstance(detail, dict), (
        f"observer detail must be stored under data-relative path {mapped!r}"
    )
    assert detail["path"] == mapped
    assert detail["period"] == PERIOD


def test_002_observer_corrupt_detail_is_unknown_and_blocking(tmp_path) -> None:
    """F4：详情缺失/损坏不得当作无状态，须保持 unknown/阻断且不得清除 pending。"""
    store, sched = _make(tmp_path, "corrupt.db", _base_docs())
    sched.startup()
    rows_before = observer_pending_rows(store)
    assert rows_before

    entries = _all_details(store)
    assert entries, "前置：observer detail 须存在"
    key, detail = entries[0]
    corrupted = dict(detail)
    corrupted.pop("latest_observation", None)
    corrupted["first_observation"] = None
    corrupted["sticky"] = False  # 与现有 observer_pending 行不一致的损坏详情
    _write_detail_payload(store, key, corrupted)

    states = sched.source_states()["ird_budget"]
    assert "unknown" in states, (
        "corrupt/missing observer detail must map to unknown blocking state, not "
        f"disappear: {states!r}"
    )
    availability = sched.availability(PERIOD)
    assert availability["mode"] == "blocked"
    assert observer_pending_rows(store), "corrupt detail must not delete pending state"

    from app.updater.observer import resolve_observer_pending

    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=_clear_resolution(store, detail),
            clock=sched._clock,
        )
    assert observer_pending_rows(store), "Resolution must not clear corrupt detail"


def test_002_resolution_requires_independent_proof_content(tmp_path) -> None:
    """F4：Resolution 必须匹配独立 proof 内容，不得仅非空/digest 字符串。"""
    from app.updater.observer import resolve_observer_pending

    store, sched = _make(tmp_path, "proof.db", _base_docs())
    sched.startup()
    _rows, detail = _pending_for(store)
    assert detail and detail["sticky"] is True

    bad_digest = _clear_resolution(
        store, detail, proof_sha256="sha256:" + "00" * 32
    )
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=bad_digest,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], "proof_sha256 不符不得清除"

    wrong_identity = _clear_resolution(
        store, detail, proof=_proof_body(source_id="other_source")
    )
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=wrong_identity,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], "proof 身份不符不得清除"

    missing_proof = _clear_resolution(store, detail)
    missing_proof["proof"] = None
    missing_proof["proof_sha256"] = "sha256:" + "11" * 32
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=missing_proof,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], "缺独立 proof 内容不得清除"

    good = _clear_resolution(store, detail)
    resolve_observer_pending(
        store,
        source_id="ird_budget",
        path=CAP_OLD_PATH,
        period=PERIOD,
        resolution=good,
        clock=sched._clock,
    )
    assert not _pending_for(store)[0]


def test_002_resolution_approved_bundle_target_must_match(tmp_path) -> None:
    """F4：approved_bundle 的 target hash 必须与实际批准目标匹配。"""
    from app.updater.observer import resolve_observer_pending

    store, sched = _make(tmp_path, "approved.db", _base_docs())
    sched.startup()
    _rows, detail = _pending_for(store)
    assert detail and detail["sticky"] is True

    store.publish(changed_bundle_content("3200"), evidence_refs=[])
    approved = store.current()
    store.publish(changed_bundle_content("3300"), evidence_refs=[])
    other = store.current()
    assert approved["bundle_hash"] != other["bundle_hash"]

    mismatch = _clear_resolution(
        store,
        detail,
        disposition="approved_bundle",
        target_bundle_hash=other["bundle_hash"],
        approved_bundle={
            "target_bundle_id": approved["bundle_id"],
            "target_bundle_hash": approved["bundle_hash"],
        },
    )
    with pytest.raises(Exception):
        resolve_observer_pending(
            store,
            source_id="ird_budget",
            path=CAP_OLD_PATH,
            period=PERIOD,
            resolution=mismatch,
            clock=sched._clock,
        )
    assert _pending_for(store)[0], (
        "target_bundle_hash 与 approved_bundle.target_bundle_hash 不符不得清除"
    )

    good = _clear_resolution(
        store,
        detail,
        disposition="approved_bundle",
        target_bundle_hash=approved["bundle_hash"],
        approved_bundle={
            "target_bundle_id": approved["bundle_id"],
            "target_bundle_hash": approved["bundle_hash"],
        },
    )
    resolve_observer_pending(
        store,
        source_id="ird_budget",
        path=CAP_OLD_PATH,
        period=PERIOD,
        resolution=good,
        clock=sched._clock,
    )
    assert not _pending_for(store)[0]


def test_002_observer_resolution_closes_sticky_state(tmp_path) -> None:
    """F4：resolve 后详情不得继续 sticky 并在下一轮继承。"""
    from app.updater.observer import resolve_observer_pending

    store, sched = _make(tmp_path, "closed.db", _base_docs())
    sched.startup()
    _rows, detail = _pending_for(store)
    assert detail and detail["sticky"] is True

    good = _clear_resolution(store, detail)
    resolve_observer_pending(
        store,
        source_id="ird_budget",
        path=CAP_OLD_PATH,
        period=PERIOD,
        resolution=good,
        clock=sched._clock,
    )
    assert not observer_pending_rows(store)

    cleared = _all_details(store)[0][1]
    assert cleared["sticky"] is False, (
        f"resolved detail must close sticky state: {cleared!r}"
    )
    assert cleared.get("resolution", {}).get("disposition") == "no_effect"
    assert sched.availability(PERIOD)["mode"] == "ok"

    # 下一轮同值观察不得从已闭合详情继承 sticky pending。
    sched._outbound.documents[BUDGET_URL] = BUDGET_DOC_OK
    sched._clock.set(hk(2026, 4, 2))
    assert sched.tick()["status"] == "succeeded"
    assert not observer_pending_rows(store), (
        "resolved sticky detail must not be inherited by the next round"
    )


def test_002_salary_bundle_private_copies_retired() -> None:
    """A8：salary 税区 allowances/reductions 私有副本必须退役（读顶层共享矩阵）。"""
    from app.rules.bundle import INITIAL_BUNDLE_CONTENT

    salaries = INITIAL_BUNDLE_CONTENT["data"]["salaries"]
    assert "allowances" not in salaries, (
        "A8: data.salaries.allowances private copy must be retired; production "
        "and helpers read top-level data.allowances"
    )
    assert "reductions" not in salaries, (
        "A8: data.salaries.reductions private copy must be retired; production "
        "and helpers read top-level data.reductions"
    )


def _all_details(store) -> list[tuple[str, dict]]:
    conn = store._connect()
    try:
        rows = conn.execute(
            "SELECT key, value_json FROM updater_meta"
            " WHERE key LIKE 'observer_pending/%' ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    return [(str(key), json.loads(value)) for key, value in rows]


def _write_detail_payload(store, key: str, detail: dict) -> None:
    payload = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    conn = store._connect()
    try:
        conn.execute(
            "UPDATE updater_meta SET value_json = ? WHERE key = ?", (payload, key)
        )
    finally:
        conn.close()
