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

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.core.errors import AppError
from app.core.hashing import (
    BUNDLE_CONTENT_FIELDS,
    bundle_content_hash,
    canonical_json_bytes,
    canonical_loads,
)

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


def _canonical_sha(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


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
    def _prepare_bundle(
        self,
        content: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """在写库前整体校验 C2.8 封闭字段并准备单事务写入载荷。"""
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
        return {
            "bundle_id": bundle_id,
            "bundle_hash": bundle_hash,
            "rules_schema_version": schema_version,
            "content_json": content_json,
            "evidence_json": evidence_json,
            "now": now,
        }

    def _publish_in_txn(
        self,
        conn: sqlite3.Connection,
        prepared: Mapping[str, Any],
        publish_audit: Mapping[str, Any] | None,
    ) -> None:
        """在调用方已开启的单事务/单连接内写入 bundle/publication/audit/指针。

        本方法不取锁、不开事务、不关连接——锁与事务边界唯一归 store 的调用方
        （publish/publish_update/rollback 同一连接完成重核＋全部写入）。
        """
        bundle_id = str(prepared["bundle_id"])
        bundle_hash = str(prepared["bundle_hash"])
        schema_version = str(prepared["rules_schema_version"])
        now = str(prepared["now"])
        # storefix：同一 bundle_id 的既有行必须与本次发布证明/内容一致。
        # 先查后写（publication/audit/pointer 之前）；不同 evidence 不得借
        # INSERT ... ON CONFLICT DO NOTHING 静默保留旧证据。
        existing = conn.execute(
            "SELECT bundle_hash, content_json, evidence_json"
            " FROM rule_bundles WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        if existing is not None:
            existing_hash, existing_content_json, existing_evidence_json = existing
            if str(existing_evidence_json) != str(prepared["evidence_json"]):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    (
                        "bundle evidence conflict: existing rule_bundles row for "
                        f"{bundle_id} carries different evidence_json than the "
                        "prepared publication (evidence/bundle conflict)"
                    ),
                )
            if (
                str(existing_hash) != bundle_hash
                or str(existing_content_json) != str(prepared["content_json"])
            ):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    (
                        "bundle conflict: existing rule_bundles row for "
                        f"{bundle_id} has different bundle_hash/content_json "
                        "than the prepared publication"
                    ),
                )
        conn.execute(
            "INSERT INTO rule_bundles (bundle_id, bundle_hash,"
            " rules_schema_version, content_json, evidence_json, published_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(bundle_id) DO NOTHING",
            (
                bundle_id,
                bundle_hash,
                schema_version,
                prepared["content_json"],
                prepared["evidence_json"],
                now,
            ),
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
        prepared = self._prepare_bundle(content, evidence_refs)
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._publish_in_txn(conn, prepared, publish_audit)
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
            "bundle_id": str(prepared["bundle_id"]),
            "bundle_hash": str(prepared["bundle_hash"]),
            "rules_schema_version": str(prepared["rules_schema_version"]),
        }

    def publish_update(
        self,
        *,
        purpose: str,
        baseline: Mapping[str, Any] | None,
        content: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
        required_cases: Sequence[str] = (),
        publish_audit: Mapping[str, Any] | None = None,
        verified_candidate_hash: str | None = None,
        verified_evidence_sha256: str | None = None,
    ) -> dict[str, str]:
        """seed/update 单事务发布：store 唯一持锁＋事务连接，事务内重核 CAS。

        - seed（仅发行固定 v2）：baseline 必须两项 null；事务内复核
          current_pointer 无行且 rule_bundles 零行（真空库唯一态）。
        - update：baseline 两项非 null；事务内复核当前指针仍等于预期
          baseline（id/hash/schema）且当前束内容 hash 未损坏。
        - verified_candidate_hash（publisher 已核验候选）：在锁内
          BEGIN IMMEDIATE 内对冻结候选重算 hash 并比对；不一致即拒绝，
          保证核验与写入绑定同一字节（ACC-1）。
        - verified_evidence_sha256（publisher 已核验 evidence）：在锁内对
          传入的冻结 evidence 及已序列化 evidence_json 重算 canonical SHA
          并比对；不一致即拒绝，保证 stored evidence 与 report binding 一致
          （R1）。None 时保持旧调用兼容。
        任何不符 → 整体拒绝、零切换/零部分写；不做跨 purpose 混用。
        """
        if purpose not in {"seed", "update"}:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                f"发布 purpose 非法（须为 seed|update）：{purpose!r}",
            )
        if purpose == "seed":
            if baseline is not None:
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "seed 发布禁止非空 baseline（仅真空白＋两项 null）",
                )
            baseline_id = baseline_hash = baseline_schema = None
        else:
            if not isinstance(baseline, Mapping):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "update 发布必须提供非空 baseline（id/hash/schema）",
                )
            baseline_id = baseline.get("bundle_id")
            baseline_hash = baseline.get("bundle_hash")
            baseline_schema = baseline.get("rules_schema_version")
            if not all(
                isinstance(value, str) and value
                for value in (baseline_id, baseline_hash, baseline_schema)
            ):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "update baseline 的 id/hash/schema 必须全部为非空字符串",
                )
        prepared = self._prepare_bundle(content, evidence_refs)
        audit_detail = dict(publish_audit or {})
        audit_detail.update(
            {
                "purpose": purpose,
                "baseline_bundle_id": baseline_id,
                "baseline_bundle_hash": baseline_hash,
                "baseline_rules_schema_version": baseline_schema,
                "required_case_ids": list(required_cases),
                "candidate_bundle_hash": str(prepared["bundle_hash"]),
            }
        )
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if verified_candidate_hash is not None:
                    try:
                        actual_candidate_hash = bundle_content_hash(content)
                    except (TypeError, ValueError) as exc:
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            "verified candidate hash cannot be recomputed "
                            f"(candidate freeze): {exc}",
                        ) from exc
                    if (
                        actual_candidate_hash != verified_candidate_hash
                        or str(prepared["bundle_hash"]) != verified_candidate_hash
                    ):
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            (
                                "verified candidate hash mismatch inside "
                                "BEGIN IMMEDIATE (candidate freeze): "
                                f"verified={verified_candidate_hash!r} "
                                f"actual={actual_candidate_hash!r}"
                            ),
                        )
                if verified_evidence_sha256 is not None:
                    try:
                        actual_evidence = [
                            dict(fact) for fact in evidence_refs
                        ]
                        actual_evidence_sha = _canonical_sha(actual_evidence)
                        prepared_evidence_sha = _canonical_sha(
                            canonical_loads(prepared["evidence_json"])
                        )
                    except (TypeError, ValueError) as exc:
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            "verified evidence hash cannot be recomputed "
                            f"(evidence freeze): {exc}",
                        ) from exc
                    if (
                        actual_evidence_sha != verified_evidence_sha256
                        or prepared_evidence_sha != verified_evidence_sha256
                    ):
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            (
                                "verified evidence hash mismatch inside "
                                "BEGIN IMMEDIATE (evidence freeze): "
                                f"verified={verified_evidence_sha256!r} "
                                f"actual={actual_evidence_sha!r} "
                                f"stored={prepared_evidence_sha!r}"
                            ),
                        )
                if purpose == "seed":
                    pointer_count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM current_pointer"
                        ).fetchone()[0]
                    )
                    bundle_count = int(
                        conn.execute("SELECT COUNT(*) FROM rule_bundles").fetchone()[0]
                    )
                    if pointer_count != 0 or bundle_count != 0:
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            "仅真空白库允许 seed（指针无行且束零行）；拒绝 reseed",
                        )
                else:
                    row = conn.execute(
                        "SELECT rb.bundle_id, rb.bundle_hash, rb.rules_schema_version,"
                        " rb.content_json"
                        " FROM current_pointer cp"
                        " JOIN rule_bundles rb ON rb.bundle_id = cp.bundle_id"
                        " WHERE cp.id = 1"
                    ).fetchone()
                    if row is None:
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            "update 发布无法核对当前指针：无已发布束",
                        )
                    current_id, current_hash, current_schema, current_json = row
                    if (
                        str(current_id) != str(baseline_id)
                        or str(current_hash) != str(baseline_hash)
                        or str(current_schema) != str(baseline_schema)
                    ):
                        raise AppError(
                            "E_UPDATE_QUARANTINED",
                            "update baseline 与实际 current 指针不符（CAS 拒绝，零写入）",
                        )
                    try:
                        current_content = canonical_loads(current_json)
                        recomputed = bundle_content_hash(current_content)
                    except (TypeError, ValueError) as exc:
                        raise AppError(
                            "E_RULES_STATE_UNVERIFIABLE",
                            "当前束内容损坏（非法 JSON/封闭字段缺失）；拒绝发布",
                        ) from exc
                    if recomputed != str(current_hash):
                        raise AppError(
                            "E_RULES_STATE_UNVERIFIABLE",
                            "当前束内容 hash 与已发布 hash 不符；拒绝发布",
                        )
                self._publish_in_txn(conn, prepared, audit_detail)
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
            "bundle_id": str(prepared["bundle_id"]),
            "bundle_hash": str(prepared["bundle_hash"]),
            "rules_schema_version": str(prepared["rules_schema_version"]),
        }

    def rollback_atomic(
        self,
        *,
        target_bundle_id: str,
        reason: str,
        affected_periods_fn,
    ) -> dict[str, Any]:
        """审计化指针回退（store 唯一持锁＋单事务连接；publisher 不持锁）。

        兼容/差集/指针切换/rollback audit 全部在同一事务连接完成；未知/未发布
        目标 → E_UPDATE_ROLLBACK_INVALID。目标 bundle 内容不改写。
        """
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                current_row = conn.execute(
                    "SELECT rb.bundle_id, rb.content_json, rb.rules_schema_version"
                    " FROM current_pointer cp"
                    " JOIN rule_bundles rb ON rb.bundle_id = cp.bundle_id"
                    " WHERE cp.id = 1"
                ).fetchone()
                if current_row is None:
                    raise AppError(
                        "E_UPDATE_ROLLBACK_INVALID",
                        "尚无已发布规则束；无法回滚",
                    )
                from_bundle_id = str(current_row[0])
                from_schema = str(current_row[2])
                published = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM publications WHERE bundle_id = ?",
                        (target_bundle_id,),
                    ).fetchone()[0]
                )
                if not published:
                    raise AppError(
                        "E_UPDATE_ROLLBACK_INVALID",
                        "回滚目标不是此前已发布版本（仅允许回退到 publications"
                        f" 在档版本）：{target_bundle_id}",
                    )
                target_row = conn.execute(
                    "SELECT content_json, bundle_hash, rules_schema_version"
                    " FROM rule_bundles WHERE bundle_id = ?",
                    (target_bundle_id,),
                ).fetchone()
                if target_row is None:
                    raise AppError(
                        "E_UPDATE_ROLLBACK_INVALID",
                        f"回滚目标规则束不存在：{target_bundle_id}",
                    )
                if str(target_row[2]) != from_schema:
                    raise AppError(
                        "E_UPDATE_ROLLBACK_INVALID",
                        "回滚目标 rules_schema_version 与现行束不兼容（拒绝回滚）",
                    )
                try:
                    from_content = canonical_loads(current_row[1])
                    target_content = canonical_loads(target_row[0])
                    target_hash = bundle_content_hash(target_content)
                except (TypeError, ValueError) as exc:
                    raise AppError(
                        "E_RULES_STATE_UNVERIFIABLE",
                        "回滚读取规则束内容损坏；拒绝回滚",
                    ) from exc
                if target_hash != str(target_row[1]):
                    raise AppError(
                        "E_RULES_STATE_UNVERIFIABLE",
                        "回滚目标内容 hash 与已发布 hash 不符；拒绝回滚",
                    )
                # F2：回滚目标同样执行五税固定 profile 准入（锁内同一事务）。
                from app.rules.admission import (
                    FIXED_PROFILE_ID_BY_TAX,
                    verify_fixed_profile,
                )

                try:
                    for tax_type in FIXED_PROFILE_ID_BY_TAX:
                        verify_fixed_profile(target_content, tax_type=tax_type)
                except AppError as exc:
                    raise AppError(
                        "E_UPDATE_ROLLBACK_INVALID",
                        f"回滚目标 fixed profile 准入失败；拒绝回滚：{exc}",
                    ) from exc
                affected = list(affected_periods_fn(from_content, target_content))
                detail = {
                    "action": "rollback",
                    "from_bundle_id": from_bundle_id,
                    "to_bundle_id": target_bundle_id,
                    "reason": reason,
                    "affected_periods": affected,
                }
                conn.execute(
                    "UPDATE current_pointer SET bundle_id = ? WHERE id = 1",
                    (target_bundle_id,),
                )
                conn.execute(
                    "INSERT INTO audit_log (event_type, detail_json, created_at)"
                    " VALUES ('rollback',?,?)",
                    (
                        json.dumps(detail, ensure_ascii=False, sort_keys=True),
                        _now_iso(),
                    ),
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
            "rolled_back_to": target_bundle_id,
            "from_bundle_id": from_bundle_id,
            "affected_periods": affected,
        }

    # —— 读取（并发 pin：每次独立连接，返回单一完整绑定快照）——
    def is_true_empty(self) -> bool:
        """真空库判定（Annex 005 §15.4 第一行）：current_pointer 无行 **且**
        rule_bundles 零行——唯一允许发行版固定 v2 seed 的状态；孤儿束/悬空
        指针/损坏/不兼容一律 fail-closed，不得当空库 reseed。"""
        conn = self._connect()
        try:
            pointer_count = int(
                conn.execute("SELECT COUNT(*) FROM current_pointer").fetchone()[0]
            )
            bundle_count = int(
                conn.execute("SELECT COUNT(*) FROM rule_bundles").fetchone()[0]
            )
        finally:
            conn.close()
        return pointer_count == 0 and bundle_count == 0

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
        """按 id 载入完整 content（C2.8 封闭 6 字段映射）。

        R1⑥：载入内容非法 JSON/非对象/重复键 → E_RULES_STATE_UNVERIFIABLE
        （fail-closed；不得裸抛 500 或以损坏映射参与计算）。
        """
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
        try:
            content = canonical_loads(row[0])
        except (TypeError, ValueError) as exc:
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                f"规则束内容损坏（非法 JSON/重复键）：{bundle_id}；拒算"
                "（fail-closed）。",
            ) from exc
        if not isinstance(content, dict):
            raise AppError(
                "E_RULES_STATE_UNVERIFIABLE",
                f"规则束内容形状损坏（非对象）：{bundle_id}；拒算（fail-closed）。",
            )
        return content
