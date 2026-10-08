"""SQLite 规则束存储（SDD §5：单事务原子发布＋单行 current_pointer＋并发 pin）。

REQ-2/REQ-18 基础面：
- 发布＝单事务内插入 bundle＋publication 并切换指针（无新旧参数混用；
  失败不留任何部分状态）；
- 并发计算在请求开始 pin 单一完整 bundle 快照；
- 拟名表：rule_bundles／publications／update_jobs／source_checks／snapshots／
  audit_log／current_pointer（单行）；
- bundle_hash＝Annex C C2.8 封闭 6 字段（RuleBundleContent）哈希。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.core.errors import AppError
from app.core.hashing import BUNDLE_CONTENT_FIELDS, bundle_content_hash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_bundles (
    bundle_id TEXT PRIMARY KEY,
    bundle_hash TEXT NOT NULL,
    rules_schema_version TEXT NOT NULL,
    content_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id TEXT NOT NULL,
    bundle_hash TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS update_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    attempt_at TEXT,
    success_at TEXT,
    http_outcome TEXT,
    snapshot_digest TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    body BLOB
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_pointer (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    bundle_id TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuleStore:
    """SQLite 单文件规则束存储；写操作经进程内写锁＋单事务，读操作逐连接独立。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_schema()

    # —— 基础设施 ——
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30.0, isolation_level=None)

    def _init_schema(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
            finally:
                conn.close()

    # —— 发布（单事务原子：bundle＋publication＋指针切换＋可选 publish audit）——
    def publish(
        self,
        content: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
        *,
        publish_audit: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        """发布完整规则束；content 缺 C2.8 封闭字段 → 整体拒绝，不留部分状态。

        publish_audit：更新管线的 publish 类 audit 负载（如 job_id/action）；
        非 None 时与 bundle/publication/指针切换同一事务写入（bundle_id/
        bundle_hash 自动并入）——任一步失败 → 整体回滚零部分状态（SDD §5
        发布行单事务；C5.8 publish 类事件）。
        """
        missing = [field for field in BUNDLE_CONTENT_FIELDS if field not in content]
        if missing:
            raise ValueError(f"bundle content 缺少封闭字段（C2.8）：{missing}")
        content_copy = json.loads(json.dumps(dict(content), ensure_ascii=False))
        bundle_hash = bundle_content_hash(content_copy)
        bundle_id = "b-" + bundle_hash.split(":", 1)[1]
        schema_version = str(content_copy["rules_schema_version"])
        now = _now_iso()
        content_json = json.dumps(
            content_copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        evidence_json = json.dumps(
            [dict(evidence) for evidence in evidence_refs],
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO rule_bundles (bundle_id, bundle_hash,"
                    " rules_schema_version, content_json, evidence_json, published_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (bundle_id, bundle_hash, schema_version, content_json, evidence_json, now),
                )
                conn.execute(
                    "INSERT INTO publications (bundle_id, bundle_hash, published_at)"
                    " VALUES (?,?,?)",
                    (bundle_id, bundle_hash, now),
                )
                conn.execute(
                    "INSERT INTO audit_log (event_type, detail_json, created_at)"
                    " VALUES ('rule', ?, ?)",
                    (
                        json.dumps(
                            {"action": "publish", "bundle_id": bundle_id},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
                if publish_audit is not None:
                    detail = dict(publish_audit)
                    detail.setdefault("bundle_id", bundle_id)
                    detail.setdefault("bundle_hash", bundle_hash)
                    conn.execute(
                        "INSERT INTO audit_log (event_type, detail_json, created_at)"
                        " VALUES (?,?,?)",
                        (
                            "publish",
                            json.dumps(detail, ensure_ascii=False, sort_keys=True),
                            now,
                        ),
                    )
                conn.execute(
                    "INSERT INTO current_pointer (id, bundle_id) VALUES (1, ?)"
                    " ON CONFLICT(id) DO UPDATE SET bundle_id = excluded.bundle_id",
                    (bundle_id,),
                )
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()
        return {
            "bundle_id": bundle_id,
            "bundle_hash": bundle_hash,
            "rules_schema_version": schema_version,
        }

    # —— 读取（并发 pin：每次独立连接，返回单一完整绑定快照）——
    def _pointer_row(self) -> tuple[str, str, str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT rb.bundle_id, rb.bundle_hash, rb.rules_schema_version"
                " FROM current_pointer cp"
                " JOIN rule_bundles rb ON rb.bundle_id = cp.bundle_id"
                " WHERE cp.id = 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                "尚无已发布规则束；无法建立规则绑定",
            )
        return (str(row[0]), str(row[1]), str(row[2]))

    def current(self) -> dict[str, str]:
        """单行指针指向的完整绑定（三元组完全来自同一 bundle，无新旧混用）。"""
        bundle_id, bundle_hash, schema_version = self._pointer_row()
        return {
            "bundle_id": bundle_id,
            "bundle_hash": bundle_hash,
            "rules_schema_version": schema_version,
        }

    def pin_current(self) -> dict[str, str]:
        """请求开始 pin 的单一完整 bundle 绑定快照（§5）。"""
        return self.current()

    def get_bundle(self, bundle_id: str) -> dict[str, Any]:
        """按 id 载入完整 content（C2.8 封闭 6 字段映射）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT content_json FROM rule_bundles WHERE bundle_id = ?",
                (bundle_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                f"规则束不存在：{bundle_id}",
            )
        return json.loads(row[0])
