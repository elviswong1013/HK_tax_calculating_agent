"""REQ-18 快照 pin 与修剪（M5 Red；SDD §5 获取行＋Annex C C7.8）。

REQ: 18（§9：每源保留最近 24 份全文有界快照；被发布/回滚版本引用的快照
pin 至受控归档为止；首版被引用快照持续 pin；hash 去重）。
规格锚点:
  - SDD §5：allowlist 抓取产物＝snapshots（全文有界快照：URL＋抓取日＋hash）；
    每源保留最近 24 份。
  - Annex C C7.8：被发布/回滚版本引用的全文快照 pin 至受控归档为止；
    首版（受控归档建立前）被引用快照持续 pin，同样不适用修剪；24 份保留
    策略仅适用未引用快照；hash 去重。
期望值来源: 结构性契约（保留上限/pin 存活），无金额期望。

【拟名】被测契约（app/updater/archive，Green 阶段须按测试实现，不得要求
测试改写）:
  - record_snapshot(store, *, url, retrieved_at: str, content_hash, body)
    -> snapshot_id（int）；同 (url, content_hash) 重复记录 → 去重（返回既有
    id、不新增行，C7.8）。
  - mark_referenced(store, snapshot_id, reference) -> None
    把快照标记为被某发布/回滚版本引用（pin）；reference 形如
    {"kind": "publication"|"rollback", "bundle_id": …}。
  - prune_snapshots(store, *, keep_per_url=24) -> int（移除数）
    仅修剪未引用快照至每 URL 最多 keep_per_url 份（保留最近）；被引用
    （pinned）快照一律不剪。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.archive（快照归档缺失）」。
"""

from __future__ import annotations

import sqlite3

from _fakes import BUDGET_URL, seed_published_store


def _archive():
    from app.updater.archive import mark_referenced, prune_snapshots, record_snapshot

    return record_snapshot, mark_referenced, prune_snapshots


def _snapshot_exists(db_path, snapshot_id) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    finally:
        conn.close()
    return bool(row and row[0])


def _count_snapshots(db_path, url: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM snapshots WHERE url = ?", (url,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


def test_snapshot_pin_first_version_never_pruned(tmp_path) -> None:
    """首版被引用快照持续 pin 不剪；未引用快照按每 URL 24 份上限修剪。"""
    record_snapshot, mark_referenced, prune_snapshots = _archive()
    db_path = tmp_path / "archive.db"
    store = seed_published_store(tmp_path, "archive.db")

    # —— 首版快照被发布版本引用（首版＝受控归档建立前）→ 持续 pin
    first = record_snapshot(
        store,
        url=BUDGET_URL,
        retrieved_at="2026-01-31T09:00:00+08:00",
        content_hash="sha256:" + "11" * 32,
        body=b"budget-v1",
    )
    mark_referenced(
        store, first, reference={"kind": "publication", "bundle_id": "b-seed"}
    )

    # —— 未引用快照写入 30 份 → 修剪至 24 份（仅未引用适用上限）
    for index in range(30):
        record_snapshot(
            store,
            url=BUDGET_URL,
            retrieved_at=f"2026-02-{(index % 28) + 1:02d}T09:00:00+08:00",
            content_hash=f"sha256:{index + 1:064x}",
            body=f"budget-unreferenced-{index}".encode(),
        )
    removed = prune_snapshots(store, keep_per_url=24)
    assert removed >= 6, f"30 份未引用快照应至少剪除 6 份（实际 {removed}）"
    assert _snapshot_exists(db_path, first), "被引用（pinned）首版快照不得被修剪"
    assert _count_snapshots(db_path, BUDGET_URL) == 25, (
        "修剪后＝24 份未引用上限＋1 份被引用 pin（C7.8：24 份策略仅适用未引用）"
    )

    # —— 多轮修剪：被引用首版持续存活，未引用维持 24 份
    for index in range(30, 60):
        record_snapshot(
            store,
            url=BUDGET_URL,
            retrieved_at=f"2026-03-{(index % 28) + 1:02d}T09:00:00+08:00",
            content_hash=f"sha256:{index + 1:064x}",
            body=f"budget-unreferenced-{index}".encode(),
        )
    prune_snapshots(store, keep_per_url=24)
    assert _snapshot_exists(db_path, first), "首版被引用快照在任何修剪轮次中持续 pin"
    assert _count_snapshots(db_path, BUDGET_URL) == 25

    # —— hash 去重（C7.8）：同 (url, content_hash) 重复记录不新增行
    before = _count_snapshots(db_path, BUDGET_URL)
    again = record_snapshot(
        store,
        url=BUDGET_URL,
        retrieved_at="2026-04-01T09:00:00+08:00",
        content_hash="sha256:" + "11" * 32,  # 与首版同 hash
        body=b"budget-v1",
    )
    assert again == first, "同 (url, content_hash) 须返回既有快照 id"
    assert _count_snapshots(db_path, BUDGET_URL) == before
