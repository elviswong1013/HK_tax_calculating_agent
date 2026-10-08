"""快照归档：hash 去重、引用 pin、每 URL 上限修剪（SDD §5 获取行；Annex C C7.8）。

REQ: 18（§9：每源保留最近 24 份全文有界快照；被发布/回滚版本引用的快照 pin
至受控归档为止；首版（受控归档建立前）被引用快照持续 pin；hash 去重）。
规格锚点:
  - SDD §5：`snapshots` 表（URL＋抓取日＋hash＋body）由 app.rules.store 建表；
    本模块复用既有表与连接/写锁原语，不重写 store 层。
  - Annex C C7.8：24 份保留策略仅适用未引用快照；引用记录落
    snapshot_references（CREATE IF NOT EXISTS 附加表，不动 store._SCHEMA）。
期望值来源: 结构性契约，无金额期望。

【拟名】被测契约:
  - record_snapshot(store, *, url, retrieved_at: str, content_hash, body) -> int
    同 (url, content_hash) 重复记录 → 去重（返回既有 id、不新增行）。
  - mark_referenced(store, snapshot_id, reference) -> None
    reference＝{"kind": "publication"|"rollback", "bundle_id": …}。
  - prune_snapshots(store, *, keep_per_url=24) -> int（移除数）
    仅修剪未引用快照至每 URL 最多 keep_per_url 份（保留最近）；
    被引用（pinned）快照一律不剪。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

_REFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_references (
    snapshot_id INTEGER NOT NULL,
    ref_kind TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ref_kind, bundle_id)
);
"""


def _ensure_references_schema(store) -> None:
    """附加引用表（幂等）；复用既有 store 连接/写锁原语，不改 store 层。"""
    with store._write_lock:
        conn = store._connect()
        try:
            conn.executescript(_REFERENCES_SCHEMA)
        finally:
            conn.close()


def record_snapshot(
    store,
    *,
    url: str,
    retrieved_at: str,
    content_hash: str,
    body: bytes,
) -> int:
    """记录全文快照；同 (url, content_hash) 去重（C7.8）→ 返回既有 id。"""
    _ensure_references_schema(store)
    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT id FROM snapshots WHERE url = ? AND content_hash = ?",
            (url, content_hash),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        return int(row[0])
    with store._write_lock:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "INSERT INTO snapshots (url, retrieved_at, content_hash, body)"
                " VALUES (?,?,?,?)",
                (url, retrieved_at, content_hash, bytes(body)),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    return int(cursor.lastrowid)


def mark_referenced(store, snapshot_id: int, reference: Mapping[str, Any]) -> None:
    """把快照标记为被某发布/回滚版本引用（pin；首版被引用持续 pin，C7.8）。"""
    _ensure_references_schema(store)
    kind = str(reference.get("kind") or "")
    bundle_id = str(reference.get("bundle_id") or "")
    with store._write_lock:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO snapshot_references"
                " (snapshot_id, ref_kind, bundle_id, created_at) VALUES (?,?,?,?)",
                (
                    int(snapshot_id),
                    kind,
                    bundle_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def prune_snapshots(store, *, keep_per_url: int = 24) -> int:
    """仅修剪未引用快照至每 URL 上限（保留最近）；被引用一律不剪。返回移除数。"""
    _ensure_references_schema(store)
    removed = 0
    with store._write_lock:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            urls = [row[0] for row in conn.execute("SELECT DISTINCT url FROM snapshots")]
            for url in urls:
                rows = conn.execute(
                    "SELECT id FROM snapshots WHERE url = ? AND id NOT IN"
                    " (SELECT snapshot_id FROM snapshot_references)"
                    " ORDER BY retrieved_at DESC, id DESC",
                    (url,),
                ).fetchall()
                stale = [int(row[0]) for row in rows[keep_per_url:]]
                for snapshot_id in stale:
                    conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
                removed += len(stale)
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    return removed
