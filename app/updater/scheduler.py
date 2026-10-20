"""调度时钟与更新管线编排（REQ-17/18；SDD §5 调度/更新管线行）。

REQ: 17（§9：格历一月调度、月末截断、完成日重锚；首启即查；逾期单次合并补查；
手动与计划同一管线＋进程内 singleflight；分源/全局 attempt-success 分离、部分
失败不推进全局成功；checked-ok≠published；不发送任何用户财务数据、不依赖
模型 API；失败有界退避 60min×2 封顶 24h、仅进程内）。
REQ: 18（§5 降级行：离线时已验证快照可计算＋中文 notice；已知变更/过期 →
拒绝受影响期间；SOURCE enter/clear 状态机，到期≠过期）。
规格锚点:
  - Annex C C6.1–C6.6（重锚链 31Jan→28Feb→28Mar；闰 29Feb→29Mar；
    31May→30Jun→30Jul；早/迟完成一律按实际完成时间戳重锚并写 reanchor
    审计；时钟可注入；退避无忙循环；启动合并补查单次）。
  - Annex C C7.8（快照 hash 去重）／C7.9（SOURCE 状态 enter/clear）。
  - Annex C C5.6（known-change/guard 状态在 classify 后、publish 判定前
    落盘持久，重启/回滚不清）／C5.8（audit 四类：rule/check/publish/rollback）。
  - SDD §5 管线七阶段：allowlist fetch → parse → classify → candidate →
    independent validation → testgate → publish。
期望值来源: 结构与时钟算术（无金额期望；3000/3500 为测试宇宙 marker）。

实现注记:
  - 调度/状态持久化复用既有 RuleStore 连接与写锁原语（store._connect／
    store._write_lock；同 app 包内复用，不改 store 层）；辅助表
    updater_meta／updater_source_states 以 CREATE IF NOT EXISTS 附加于同一
    DB（store._SCHEMA 不动）。
  - 门禁必需 case 集合由税法 annex 固化；未固化前默认空集，空报告＝隔离
    （Annex C C8.4 fail closed）——自动管线对 DATA-only 候选因此判定
    quarantined 而不发布，检查成功仍推进 global_success_at（C6.3）。
  - allowlist 之外不出站；出站仅 fetch(url)，不携带任何负载（REQ-17）。
"""

from __future__ import annotations

import calendar
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from app.core.hashing import bundle_content_hash
from app.updater import observer
from app.updater.archive import record_snapshot
from app.updater.classifier import build_candidate, is_stop_auto_path
from app.updater.fetcher import fetch_document
from app.updater.parser import extract, validate_manifest
from app.updater.publisher import publish_update
from app.updater.reference import AFFECTED_CASES, REQUIRED_CASES, reference_evidence_facts
from app.updater.testgate import run_gate
from app.updater.validator import validate_change

_BACKOFF_BASE_MINUTES = 60  # Annex C C6.4：退避基数 60min
_BACKOFF_CAP_MINUTES = 24 * 60  # 封顶 24h
# C7.9：阻断受影响期间的状态（trusted_offline 仅降级告警；提案不作现行、不阻断）
_BLOCKING_STATES = frozenset({"related_change_uncertain", "expired", "unknown"})
# legal_state_rules 分类所得、随重验同步 enter/clear 的规则状态
_RULE_STATES = frozenset(
    {"proposal_not_current", "related_change_uncertain", "verified_future", "expired"}
)

_AUX_SCHEMA = """
CREATE TABLE IF NOT EXISTS updater_meta (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS updater_source_states (
    source_id TEXT NOT NULL,
    state TEXT NOT NULL,
    period TEXT NOT NULL DEFAULT '',
    entered_at TEXT NOT NULL,
    PRIMARY KEY (source_id, state, period)
);
"""


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _add_calendar_month(moment: datetime) -> datetime:
    """完成日 + 1 格历月，月末截断（Annex C C6.1：31Jan→28Feb；闰 29Feb）。"""
    year, month = moment.year, moment.month + 1
    if month > 12:
        year, month = year + 1, 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _get_path(node: Any, fullpath: str) -> Any:
    """按束内规范点路径取 data 值（缺失 → None）。"""
    current = node
    for part in fullpath.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _confirmed_anchors(manifest: Mapping[str, Any]) -> set[str]:
    anchors = manifest.get("anchors") or []
    return {
        anchor.get("path_regex")
        for anchor in anchors
        if isinstance(anchor, dict) and anchor.get("anchor_status") == "confirmed"
    }


class _UnboundReferenceHarness:
    """默认独立参考 harness（C8.1 seam）：caseID 绑定未固化 → fail closed。"""

    def run_case(self, case_id: str) -> dict[str, Any]:
        raise KeyError(
            f"caseID 未绑定独立参考（税法 annex 未固化，不编造，C8.4）：{case_id}"
        )


class _UnboundCandidateEngine:
    """默认候选引擎 seam：与 harness 同为未绑定 fail closed。"""

    def run_case(self, case_id: str, candidate_content: dict) -> dict[str, Any]:
        raise KeyError(
            f"候选引擎 caseID 未绑定（税法 annex 未固化，不编造，C8.4）：{case_id}"
        )


class Scheduler:
    """单进程调度器：格历月调度＋更新管线编排＋SOURCE 状态（C6/C7.9）。"""

    def __init__(
        self,
        store,
        *,
        clock,
        outbound,
        sources: Sequence[Mapping[str, Any]],
        harness: Any | None = None,
        engine: Any | None = None,
        required_cases: Sequence[str] = (),
    ) -> None:
        self._store = store
        self._clock = clock
        self._outbound = outbound
        self._sources: list[dict[str, Any]] = [dict(source) for source in sources]
        self._source_ids: list[str] = [str(source["source_id"]) for source in self._sources]
        self._harness = harness
        self._engine = engine
        self._required_cases = tuple(required_cases)
        self._lock = threading.Lock()
        self._active_job: dict[str, Any] | None = None
        self._backoff_until: datetime | None = None  # 退避仅进程内，不持久（§5）
        self._backoff_failures = 0
        self._init_schema()

    # ------------------------------------------------------------------
    # 基础设施（复用 store 连接/写锁；辅助表 IF NOT EXISTS 附加）
    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.executescript(_AUX_SCHEMA)
            finally:
                conn.close()

    def _load_meta(self) -> dict[str, Any]:
        conn = self._store._connect()
        try:
            row = conn.execute(
                "SELECT value_json FROM updater_meta WHERE key = 'scheduler'"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        try:
            meta = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _save_meta(self, meta: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(meta), ensure_ascii=False, sort_keys=True)
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO updater_meta (key, value_json) VALUES ('scheduler', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                    (payload,),
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

    def _write_audit(self, event_type: str, detail: Mapping[str, Any]) -> None:
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO audit_log (event_type, detail_json, created_at)"
                    " VALUES (?,?,?)",
                    (
                        event_type,
                        json.dumps(dict(detail), ensure_ascii=False, sort_keys=True),
                        _iso(self._clock.now()),
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

    # ------------------------------------------------------------------
    # 状态读取（§5 字段表）
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        meta = self._load_meta()
        with self._lock:
            active = dict(self._active_job) if self._active_job is not None else None
            backoff_until = self._backoff_until
        return {
            "global_attempt_at": _parse_dt(meta.get("global_attempt_at")),
            "global_success_at": _parse_dt(meta.get("global_success_at")),
            "next_due_at": _parse_dt(meta.get("next_due_at")),
            "anchor_event": meta.get("anchor_event"),
            "publish_outcome": meta.get("publish_outcome"),
            "published_bundle_id": meta.get("published_bundle_id"),
            "quarantine_reason": meta.get("quarantine_reason"),
            "backoff_until": backoff_until,
            "sources": self._latest_source_checks(),
            "active_job": active,
        }

    def _latest_source_checks(self) -> list[dict[str, Any]]:
        conn = self._store._connect()
        try:
            rows: dict[str, tuple] = {}
            for row in conn.execute(
                "SELECT source_id, attempt_at, success_at, http_outcome,"
                " snapshot_digest FROM source_checks ORDER BY id"
            ):
                rows[row[0]] = row[1:]
        finally:
            conn.close()
        entries: list[dict[str, Any]] = []
        for source_id in self._source_ids:
            attempt_at, success_at, outcome, digest = rows.get(
                source_id, (None, None, None, None)
            )
            entries.append(
                {
                    "source_id": source_id,
                    "attempt_at": _parse_dt(attempt_at),
                    "success_at": _parse_dt(success_at),
                    "http_outcome": outcome,
                    "snapshot_digest": digest,
                }
            )
        return entries

    def _last_verified_digest(self, source_id: str) -> str | None:
        conn = self._store._connect()
        try:
            row = conn.execute(
                "SELECT snapshot_digest FROM source_checks"
                " WHERE source_id = ? AND success_at IS NOT NULL"
                " ORDER BY id DESC LIMIT 1",
                (source_id,),
            ).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row and row[0] else None

    # ------------------------------------------------------------------
    # SOURCE 状态（Annex C C7.9：enter/clear 显式；到期≠过期）
    # ------------------------------------------------------------------
    def _read_states(self) -> list[dict[str, str]]:
        conn = self._store._connect()
        try:
            rows = conn.execute(
                "SELECT source_id, state, period FROM updater_source_states ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()
        return [
            {"source_id": row[0], "state": row[1], "period": row[2] or ""}
            for row in rows
        ]

    def _public_state_rows(self) -> list[dict[str, str]]:
        """把 observer 内部行映射为既有公开状态（不泄漏 `observer_pending:`）。"""
        mapped: list[dict[str, str]] = []
        for row in self._read_states():
            public = observer.public_state_for(
                self._store, row["source_id"], row["state"], row["period"]
            )
            if public is None:
                continue
            mapped.append(
                {
                    "source_id": row["source_id"],
                    "state": public,
                    "period": row["period"],
                }
            )
        return mapped

    def source_states(self) -> dict[str, list[str]]:
        rows = self._public_state_rows()
        return {
            source_id: [
                row["state"] for row in rows if row["source_id"] == source_id
            ]
            for source_id in self._source_ids
        }

    def _sync_source_states_in_txn(
        self, conn, source_id: str, matched: set[tuple[str, str]]
    ) -> None:
        """在调用方事务内同步普通 C7 规则状态；SELECT/delete 排除 observer 行。"""
        desired = {(state, period or "") for state, period in matched}
        rows = conn.execute(
            "SELECT state, period FROM updater_source_states"
            " WHERE source_id = ? AND state NOT LIKE 'observer_pending:%'",
            (source_id,),
        ).fetchall()
        existing = {(row[0], row[1] or "") for row in rows}
        for state, period in sorted(existing - desired):
            conn.execute(
                "DELETE FROM updater_source_states"
                " WHERE source_id = ? AND state = ? AND period = ?"
                " AND state NOT LIKE 'observer_pending:%'",
                (source_id, state, period),
            )
        if desired - existing:
            entered_at = _iso(self._clock.now())
            for state, period in sorted(desired - existing):
                conn.execute(
                    "INSERT OR REPLACE INTO updater_source_states"
                    " (source_id, state, period, entered_at) VALUES (?,?,?,?)",
                    (source_id, state, period, entered_at),
                )

    def _sync_source_states(
        self, source_id: str, matched: set[tuple[str, str]]
    ) -> None:
        """来源重验成功后同步规则状态（enter 新状态、clear 未再确认状态；
        trusted_offline/unknown 随重验一致 clear，C7.9）。"""
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._sync_source_states_in_txn(conn, source_id, matched)
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def _matched_states(
        self, manifest: Mapping[str, Any], parsed: Mapping[str, Any]
    ) -> set[tuple[str, str]]:
        matched: set[tuple[str, str]] = set()
        for rule in manifest.get("legal_state_rules", []) or []:
            anchor = rule.get("anchor")
            match = rule.get("match")
            for notice in parsed.get("notices", []):
                if (
                    notice.get("anchor") == anchor
                    and isinstance(match, str)
                    and match in notice.get("text", "")
                ):
                    matched.add((str(rule["state"]), str(rule.get("period") or "")))
                    break
        return matched

    def _enter_state(self, source_id: str, state: str, period: str) -> None:
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT OR REPLACE INTO updater_source_states"
                    " (source_id, state, period, entered_at) VALUES (?,?,?,?)",
                    (source_id, state, period or "", _iso(self._clock.now())),
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

    def _rollback_periods(self) -> set[str]:
        """回滚守卫：rollback 类 audit 中的 affected_periods（持久，C5.6）。"""
        conn = self._store._connect()
        try:
            rows = conn.execute(
                "SELECT detail_json FROM audit_log WHERE event_type = 'rollback'"
            ).fetchall()
        finally:
            conn.close()
        periods: set[str] = set()
        for (detail_json,) in rows:
            try:
                detail = json.loads(detail_json)
            except (TypeError, ValueError):
                continue
            for period in (detail or {}).get("affected_periods") or []:
                if isinstance(period, str):
                    periods.add(period)
        return periods

    def availability(self, period: str) -> dict[str, Any]:
        """REQ-18 降级判定：blocked > trusted_offline > ok（受影响期间精确阻断）。"""
        rows = self._public_state_rows()
        states_for_period = sorted(
            {row["state"] for row in rows if row["period"] == period}
        )
        blocking = [state for state in states_for_period if state in _BLOCKING_STATES]
        offline = any(row["state"] == "trusted_offline" for row in rows)
        if blocking:
            return {
                "mode": "blocked",
                "notice": (
                    f"课税年度 {period} 存在已知未核实的法律状态"
                    f"（{'、'.join(blocking)}），受影响期间的现行计算已拒绝；"
                    "不自动推定生效或失效，待独立核验并发布后恢复。"
                ),
                "states": states_for_period,
            }
        if period in self._rollback_periods():
            return {
                "mode": "blocked",
                "notice": (
                    f"课税年度 {period} 的规则版本已回滚至此前已发布版本，"
                    "受影响期间的现行计算已拒绝（不做法律时间旅行）；"
                    "待重新核验并发布后恢复。"
                ),
                "states": states_for_period,
            }
        if offline:
            return {
                "mode": "trusted_offline",
                "notice": (
                    "官方来源暂不可达；正在依据最近已核验的本地规则快照计算，"
                    "结果仍可复核；恢复联网后将自动重新核验。"
                ),
                "states": states_for_period + ["trusted_offline"],
            }
        return {"mode": "ok", "notice": None, "states": states_for_period}

    # ------------------------------------------------------------------
    # 调度入口（Annex C C6.6 事件状态表）
    # ------------------------------------------------------------------
    def startup(self) -> dict[str, Any] | None:
        """首次启动（无 global_success_at）→ 立即 startup_initial；启动发现
        逾期 → 单次合并补查（startup_catchup，不按错过月数重复）；否则 None。"""
        meta = self._load_meta()
        if meta.get("global_success_at") is None:
            return self._run("startup_initial")
        next_due = _parse_dt(meta.get("next_due_at"))
        if next_due is None or self._clock.now() >= next_due:
            return self._run("startup_catchup")
        return None

    def tick(self) -> dict[str, Any] | None:
        """到期触发；退避期内返回 None 且不出站（无忙循环，C6.4）。"""
        with self._lock:
            if self._active_job is not None:
                return None
            backoff_until = self._backoff_until
        now = self._clock.now()
        if backoff_until is not None and now < backoff_until:
            return None
        meta = self._load_meta()
        next_due = _parse_dt(meta.get("next_due_at"))
        if next_due is None or now >= next_due:
            return self._run("scheduled")
        return None

    def trigger_manual(self) -> dict[str, Any]:
        """手动触发与计划同一管线；已有进行中作业 → 立即返回现有作业
        （同 job_id、in_progress=True；API 层映射 E_UPDATE_IN_PROGRESS/202）；
        退避不抑制手动触发（用户主动重验）。"""
        with self._lock:
            if self._active_job is not None:
                return dict(self._active_job)  # singleflight：不重跑、不阻塞
            job = self._create_job("manual")
            self._active_job = job
        self._run_job_guarded(job)
        return dict(job)

    def _run(self, kind: str) -> dict[str, Any] | None:
        with self._lock:
            if self._active_job is not None:
                return None
            job = self._create_job(kind)
            self._active_job = job
        self._run_job_guarded(job)
        return dict(job)

    def _create_job(self, kind: str) -> dict[str, Any]:
        job_id = "job-" + uuid.uuid4().hex
        now_iso = _iso(self._clock.now())
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO update_jobs (job_id, kind, status, created_at,"
                    " updated_at) VALUES (?,?,?,?,?)",
                    (job_id, kind, "running", now_iso, now_iso),
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
        return {
            "job_id": job_id,
            "kind": kind,
            "status": "running",
            "in_progress": True,
        }

    def _finish_job_row(self, job: Mapping[str, Any]) -> None:
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE update_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                    (job["status"], _iso(self._clock.now()), job["job_id"]),
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

    def _run_job_guarded(self, job: dict[str, Any]) -> None:
        try:
            self._execute(job)
        except Exception as exc:  # 防御：管线意外异常不悬挂 active job
            job["status"] = "failed"
            job["in_progress"] = False
            self._finish_job_row(job)
            self._write_audit(
                "check",
                {
                    "action": "check_failed",
                    "job_id": job["job_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        finally:
            with self._lock:
                self._active_job = None

    # ------------------------------------------------------------------
    # 更新管线（SDD §5 七阶段；C6.3：部分失败不推进全局成功）
    # ------------------------------------------------------------------
    def _execute(self, job: dict[str, Any]) -> None:
        store = self._store
        started = self._clock.now()
        meta = self._load_meta()
        prev_success = _parse_dt(meta.get("global_success_at"))
        prev_due = _parse_dt(meta.get("next_due_at"))
        # 轮开始：attempt 前进；发布判定清空（检查未完成 → 无发布判定，§5）
        meta.update(
            {
                "global_attempt_at": _iso(started),
                "publish_outcome": None,
                "published_bundle_id": None,
                "quarantine_reason": None,
            }
        )
        self._save_meta(meta)

        outcomes: dict[str, dict[str, Any]] = {}
        parses: dict[str, dict[str, Any]] = {}
        any_failed = False
        try:
            pointer = store.current()
            current_content: dict[str, Any] | None = store.get_bundle(
                pointer["bundle_id"]
            )
        except AppError:
            current_content = None
        for source in self._sources:
            source_id = str(source["source_id"])
            url = str(source["url"])
            manifest = source.get("manifest") or {}
            observer_slots = [
                slot
                for slot in (source.get("observer_slots") or [])
                if isinstance(slot, dict)
            ]
            attempt = self._clock.now()
            # 防漏 guard：manifest 校验/fetch 前先 durable 提交该源观察 checking
            # （每源独立落盘，另一来源失败不得跳过本来源 guard，005 §15.5）。
            for slot in observer_slots:
                path = str(slot.get("fullpath") or "")
                for period in observer.periods_for(path):
                    observer.record_event(
                        store,
                        source_id=source_id,
                        path=path,
                        period=period,
                        kind="checking",
                        event_detail={"phase": "manifest", "snapshot_hash": None},
                        sticky=None,
                        clock=self._clock,
                    )
            outcome: str
            digest: str | None = None
            parsed: dict[str, Any] | None = None
            body: bytes | None = None
            violations = validate_manifest(
                manifest, verified_anchors=_confirmed_anchors(manifest)
            )
            if violations:
                outcome = "invalid_manifest"
            else:
                try:
                    body = fetch_document(self._outbound, url)
                except ConnectionError:
                    outcome = "unreachable"
                except Exception:
                    outcome = "fetch_error"
                else:
                    digest = "sha256:" + hashlib.sha256(body).hexdigest()
                    for slot in observer_slots:
                        path = str(slot.get("fullpath") or "")
                        for period in observer.periods_for(path):
                            observer.record_event(
                                store,
                                source_id=source_id,
                                path=path,
                                period=period,
                                kind="checking",
                                event_detail={
                                    "phase": "parse",
                                    "snapshot_hash": digest,
                                },
                                sticky=None,
                                clock=self._clock,
                            )
                    try:
                        parsed = extract(manifest, body)
                        outcome = "ok"
                    except Exception:
                        outcome = "parse_error"
            success_at = attempt if outcome == "ok" else None
            outcomes[source_id] = {
                "attempt_at": attempt,
                "success_at": success_at,
                "http_outcome": outcome,
                "snapshot_digest": digest if outcome == "ok" else None,
            }
            self._record_source_check(
                source_id, attempt, success_at, outcome, digest if outcome == "ok" else None
            )
            if outcome == "ok" and digest is not None and parsed is not None:
                # 阶段 1 产物：全文有界快照（hash 去重，C7.8）
                record_snapshot(
                    store,
                    url=url,
                    retrieved_at=_iso(attempt),
                    content_hash=digest,
                    body=body if body is not None else b"",
                )
                parses[source_id] = parsed
                observer_failed = self._commit_source_round(
                    source_id=source_id,
                    manifest=manifest,
                    parsed=parsed,
                    observer_slots=observer_slots,
                    current_content=current_content,
                    body=body,
                    snapshot_hash=digest,
                )
                if observer_failed:
                    any_failed = True
            else:
                any_failed = True
                if outcome in {"unreachable", "fetch_error"} and (
                    self._last_verified_digest(source_id) is not None
                ):
                    # C7.9 enter：网络不可达，但最近已核验快照仍现行
                    self._enter_state(source_id, "trusted_offline", "")
                self._commit_observer_failure(
                    source_id=source_id,
                    observer_slots=observer_slots,
                    outcome=outcome,
                )

        if any_failed:
            job["status"] = "failed"
            job["in_progress"] = False
            self._finish_job_row(job)
            self._write_audit(
                "check",
                {
                    "action": "check_failed",
                    "job_id": job["job_id"],
                    "outcomes": {
                        source_id: entry["http_outcome"]
                        for source_id, entry in outcomes.items()
                    },
                },
            )
            # 有界退避：60min×2 封顶 24h；仅进程内（§5/C6.4）
            self._backoff_failures += 1
            minutes = min(
                _BACKOFF_BASE_MINUTES * (2 ** (self._backoff_failures - 1)),
                _BACKOFF_CAP_MINUTES,
            )
            with self._lock:
                self._backoff_until = started + timedelta(minutes=minutes)
            self._save_meta(meta)
            return

        # —— 全部来源 fetch+parse+classify 完成：全局成功推进（C6.3/C6.6）
        completed = self._clock.now()
        current_content, changes, facts, conflict = self._classify_round(parses)
        publish_outcome, published_bundle_id, quarantine_reason = self._decide_publish(
            job, current_content, changes, facts, conflict
        )
        anchor_event = self._anchor_event(job["kind"], prev_success, prev_due, completed)
        meta.update(
            {
                "global_success_at": _iso(completed),
                "next_due_at": _iso(_add_calendar_month(completed)),
                "anchor_event": anchor_event,
                "publish_outcome": publish_outcome,
                "published_bundle_id": published_bundle_id,
                "quarantine_reason": quarantine_reason,
            }
        )
        self._save_meta(meta)
        detail: dict[str, Any] = {
            "action": "check_complete",
            "job_id": job["job_id"],
            "anchor_event": anchor_event,
            "publish_outcome": publish_outcome,
        }
        if anchor_event == "reanchor":
            detail["reanchor"] = True  # C6.2：reanchor 事件显式入 audit_log
        self._write_audit("check", detail)
        with self._lock:
            self._backoff_failures = 0
            self._backoff_until = None
        job["status"] = "succeeded"
        job["in_progress"] = False
        self._finish_job_row(job)

    def _observer_success_events(
        self,
        *,
        manifest: Mapping[str, Any],
        observer_slots: Sequence[Mapping[str, Any]],
        current_content: Mapping[str, Any] | None,
        body: bytes | None,
        snapshot_hash: str | None,
    ) -> list[tuple[str, str, str, dict[str, Any], bool | None]]:
        """复用 extract 读取观察副本；返回待与 notice 同步同事务提交的事件。"""
        if not observer_slots:
            return []
        observer_manifest = dict(manifest)
        observer_manifest["slots"] = [dict(slot) for slot in observer_slots]
        try:
            observed = extract(observer_manifest, body if body is not None else b"")
        except Exception as exc:  # noqa: BLE001 - 观察失败必须持久 unknown
            code = "anchor_missing" if isinstance(exc, LookupError) else "parse_error"
            return [
                (
                    str(slot.get("fullpath") or ""),
                    period,
                    "observation_failed",
                    {
                        "phase": "parse",
                        "code": code,
                        "snapshot_hash": snapshot_hash,
                    },
                    True,
                )
                for slot in observer_slots
                for period in observer.periods_for(str(slot.get("fullpath") or ""))
            ]
        values = observed.get("values") if isinstance(observed, Mapping) else {}
        events: list[tuple[str, str, str, dict[str, Any], bool | None]] = []
        for slot in observer_slots:
            path = str(slot.get("fullpath") or "")
            for period in observer.periods_for(path):
                try:
                    kind, event_detail = observer.evaluate_value(
                        slot, str((values or {}).get(path, "")), current_content or {}
                    )
                except Exception:  # noqa: BLE001 - 标量/定位失败＝sticky unknown
                    events.append(
                        (
                            path,
                            period,
                            "observation_failed",
                            {
                                "phase": "normalize",
                                "code": "invalid_scalar",
                                "snapshot_hash": snapshot_hash,
                            },
                            True,
                        )
                    )
                    continue
                event_detail["snapshot_hash"] = snapshot_hash
                events.append(
                    (
                        path,
                        period,
                        kind,
                        event_detail,
                        True if kind == "value_changed" else None,
                    )
                )
        return events

    def _commit_source_round(
        self,
        *,
        source_id: str,
        manifest: Mapping[str, Any],
        parsed: Mapping[str, Any],
        observer_slots: Sequence[Mapping[str, Any]],
        current_content: Mapping[str, Any] | None,
        body: bytes | None,
        snapshot_hash: str | None,
    ) -> bool:
        """每源正常结束：普通 notice 同步与 observer 差异在同一 store 事务落盘。

        返回是否存在 observation_failed（观察失败＝检查失败，但仍保留 sticky
        pending；值变更/value_equal 不是检查失败）。
        """
        events = self._observer_success_events(
            manifest=manifest,
            observer_slots=observer_slots,
            current_content=current_content,
            body=body,
            snapshot_hash=snapshot_hash,
        )
        matched = self._matched_states(manifest, parsed)
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for path, period, kind, event_detail, sticky in events:
                    observer.apply_event(
                        conn,
                        store=self._store,
                        source_id=source_id,
                        path=path,
                        period=period,
                        kind=kind,
                        event_detail=event_detail,
                        sticky=sticky,
                        clock=self._clock,
                    )
                self._sync_source_states_in_txn(conn, source_id, matched)
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()
        return any(event[2] == "observation_failed" for event in events)

    def _commit_observer_failure(
        self,
        *,
        source_id: str,
        observer_slots: Sequence[Mapping[str, Any]],
        outcome: str,
    ) -> None:
        """来源失败时先完成本来源观察 guard 落盘（另一来源失败不得跳过）。"""
        if not observer_slots:
            return
        last_verified = self._last_verified_digest(source_id)
        if outcome in {"unreachable", "fetch_error"}:
            kind = "transport_unavailable"
            detail = {
                "code": outcome,
                "last_verified_snapshot_hash": last_verified,
            }
            sticky: bool | None = None if last_verified is not None else True
        else:
            kind = "observation_failed"
            detail = {
                "phase": "manifest" if outcome == "invalid_manifest" else "parse",
                "code": (
                    "invalid_manifest"
                    if outcome == "invalid_manifest"
                    else "parse_error"
                ),
                "snapshot_hash": None,
            }
            sticky = True
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for slot in observer_slots:
                    path = str(slot.get("fullpath") or "")
                    for period in observer.periods_for(path):
                        observer.apply_event(
                            conn,
                            store=self._store,
                            source_id=source_id,
                            path=path,
                            period=period,
                            kind=kind,
                            event_detail=detail,
                            sticky=sticky,
                            clock=self._clock,
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

    def _record_source_check(
        self,
        source_id: str,
        attempt_at: datetime,
        success_at: datetime | None,
        http_outcome: str,
        snapshot_digest: str | None,
    ) -> None:
        with self._store._write_lock:
            conn = self._store._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO source_checks (source_id, attempt_at, success_at,"
                    " http_outcome, snapshot_digest) VALUES (?,?,?,?,?)",
                    (
                        source_id,
                        _iso(attempt_at),
                        _iso(success_at) if success_at is not None else None,
                        http_outcome,
                        snapshot_digest,
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

    def _anchor_event(
        self,
        kind: str,
        prev_success: datetime | None,
        prev_due: datetime | None,
        completed: datetime,
    ) -> str:
        """C6.1/C6.2：早/迟完成按实际完成时间戳重锚（manual 与迟后 scheduled）。"""
        if kind == "startup_initial":
            return "startup_initial"
        if kind == "startup_catchup":
            return "startup_catchup"
        if kind == "manual":
            if prev_success is None or prev_success == completed:
                return "manual"  # 无前锚（首查）或同一时刻 → 非重锚
            return "reanchor"
        return "reanchor" if (prev_due is not None and completed > prev_due) else "scheduled"

    def _classify_round(
        self, parses: Mapping[str, Mapping[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], bool]:
        """阶段 3/对照：数值变化检测＋独立事实构建＋SOURCE 状态同步（C7.9）。"""
        store = self._store
        pointer = store.current()
        current_content = store.get_bundle(pointer["bundle_id"])
        changes_by_path: dict[str, dict[str, Any]] = {}
        facts: list[dict[str, Any]] = []
        conflict = False
        for source in self._sources:
            source_id = str(source["source_id"])
            parsed = parses.get(source_id)
            if parsed is None:
                continue
            manifest = source.get("manifest") or {}
            slot_by_path = {
                slot["fullpath"]: slot for slot in manifest.get("slots", []) or []
            }
            for fullpath, value in parsed.get("values", {}).items():
                baseline_path = (
                    observer.data_target(fullpath)
                    if is_stop_auto_path(fullpath)
                    else fullpath
                )
                if _get_path(current_content.get("data"), baseline_path) == value:
                    continue  # 与现行已发布内容一致 → 无变更（旧路径按映射基线比较）
                slot = slot_by_path.get(fullpath)
                entry = changes_by_path.setdefault(
                    fullpath,
                    {"fullpath": fullpath, "sources": [], "anchors": [], "values": []},
                )
                if source_id not in entry["sources"]:
                    entry["sources"].append(source_id)
                entry["anchors"].append(slot["anchor"] if slot else "")
                if value not in entry["values"]:
                    entry["values"].append(value)
                # 独立事实（阶段 5 输入；与候选参数对象分离，C8.1）
                facts.append(
                    {
                        "fact_id": f"fact:{source_id}:{fullpath}",
                        "fullpath": fullpath,
                        "value": value,
                        "source_id": source_id,
                        "url": source.get("url"),
                        "anchor": slot["anchor"] if slot else "",
                        "tier": "T1",
                    }
                )
        changes: list[dict[str, Any]] = []
        for entry in changes_by_path.values():
            if len(entry["values"]) > 1:
                conflict = True  # 同一路径各来源值不一致 → 来源冲突隔离
            changes.append(
                {
                    "fullpath": entry["fullpath"],
                    "value": entry["values"][0],
                    "source_id": entry["sources"][0],
                    "anchor": entry["anchors"][0],
                }
            )
        return current_content, changes, facts, conflict

    def _required_case_map(self, changed_paths: Sequence[str]) -> dict[str, str]:
        """服务器固定 path→case 表；测试侧 B5 注入仅在双独立承载源时可替代。

        生产 REQUIRED_CASES 保持静态回归三例；受影响变更若其计划 affected case
        不在服务端 required 集合，门禁按 required 不符隔离（缺 affected witness
        → coverage_gap），不采信报告自减。D1=a 的 test-only 第二独立源＋B5
        required case 仅由测试注入 required_cases 生效，不扩生产 allowlist。
        """
        carriers: dict[str, int] = {}
        for source in self._sources:
            for slot in (source.get("manifest") or {}).get("slots") or []:
                fullpath = str(slot.get("fullpath") or "")
                carriers[fullpath] = carriers.get(fullpath, 0) + 1
        configured = [str(case_id) for case_id in self._required_cases]
        test_only = bool(configured) and not set(configured).issubset(
            set(REQUIRED_CASES)
        )
        mapping: dict[str, str] = {}
        for path in changed_paths:
            planned = AFFECTED_CASES.get(path)
            if planned is not None and planned in configured:
                mapping[path] = planned
            elif (
                test_only
                and configured
                and carriers.get(path, 0) >= 2
            ):
                mapping[path] = configured[0]
            elif planned is not None:
                mapping[path] = planned
            elif configured:
                mapping[path] = configured[0]
        return mapping

    def _decide_publish(
        self,
        job: Mapping[str, Any],
        current_content: Mapping[str, Any],
        changes: Sequence[Mapping[str, Any]],
        facts: Sequence[Mapping[str, Any]],
        conflict: bool,
    ) -> tuple[str, str | None, str | None]:
        """checked-ok ≠ published：发布判定与检查结果独立记录（§5/C6.3）。"""
        if not changes:
            return ("none", None, None)
        if conflict:
            return ("quarantined", None, "source_conflict")
        manifests = [source.get("manifest") or {} for source in self._sources]
        built = build_candidate(current_content, changes, manifests)
        if built["status"] != "candidate":
            return ("quarantined", None, built.get("quarantine_reason") or "candidate_rejected")
        for change in changes:
            verdict = validate_change(change, facts)
            if verdict["status"] != "validated":
                return ("quarantined", None, str(verdict["status"]))
        # 门禁事实＝本轮分类独立事实＋必需 case 的已批准参数证据（固定参考
        # fixture，C8.2/C8.4；不参与候选参数核验——后者已由 validator 完成）。
        gate_facts = list(facts) + reference_evidence_facts(self._required_cases)
        candidate = built["content"]
        candidate_hash = bundle_content_hash(candidate)
        changed_paths = [str(change["fullpath"]) for change in changes]
        baseline = self._store.current()
        report = run_gate(
            candidate_content=candidate,
            current_content=current_content,
            manifests=manifests,
            independent_facts=gate_facts,
            required_cases=self._required_cases,
            harness=self._harness if self._harness is not None else _UnboundReferenceHarness(),
            engine=self._engine if self._engine is not None else _UnboundCandidateEngine(),
            outbound=None,
            candidate_hash=candidate_hash,
            changed_paths=changed_paths,
            required_case_map=self._required_case_map(changed_paths),
            purpose="update",
            baseline=baseline,
            report_id=f"report:{job['job_id']}:{candidate_hash}",
            consumed_report_ids=(),
            current_store_db=str(self._store._path),
        )
        if report["decision"] != "publish":
            return (
                "quarantined",
                None,
                report.get("quarantine_reason") or "gate_quarantined",
            )
        published = publish_update(
            self._store,
            job_id=str(job["job_id"]),
            content=candidate,
            evidence_refs=list(gate_facts),
            validation_report=report,
            purpose="update",
            baseline=baseline,
            manifests=manifests,
            required_cases=self._required_cases,
        )
        return ("published", published["bundle_id"], None)
