"""stop-auto 观察状态机（REQ-12；005 §15.3/§15.5；006 §5.6）。

六条旧 salary/PA 路径本期仅观察、不自动写：观察副本复用现有 extract，差异与
失败一律落 durable `observer_pending:<mapped data-relative path>` 状态行＋
`updater_meta` 详情（sticky）；普通 notice 同步/同值/重启/回滚/后续成功不得
清除；唯一清除入口是受控 `resolve_observer_pending`（匹配 first/latest 事件、
当前 baseline、独立 proof 内容与 approved target）。

公开读取路径（source_states/availability）不得取 store 写锁；schema 由
Scheduler 初始化或写入口显式初始化。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.core.errors import AppError
from app.core.hashing import canonical_json_bytes
from app.core.money import parse_amount

OBSERVER_STATE_PREFIX = "observer_pending:"
META_PREFIX = "observer_pending"

# 005 §15.3 固定观察值映射（旧逻辑路径 → data 内目标路径）。
STOP_AUTO_TARGETS: dict[str, str] = {
    "salaries.allowances.2025_26.basic": "allowances.2025_26.basic.amount",
    "salaries.allowances.2025_26.married": "allowances.2025_26.married.amount",
    "salaries.allowances.2026_27.basic": "allowances.2026_27.basic.amount",
    "salaries.mpf_deduction_cap": "allowances.mpf_deduction_cap",
    "salaries.reductions.2024_25.cap": "reductions.2024_25.cap",
    "salaries.reductions.2025_26.cap": "reductions.2025_26.cap",
}

# MPF 上限影响全部三个已支持年度（005 §15.3/15.5：三年度分别 pending）。
SUPPORTED_YEARS: tuple[str, ...] = ("2024_25", "2025_26", "2026_27")

_PERIOD_RE = re.compile(r"(?<!\d)(\d{4}_\d{2})(?!\d)")

_EVENT_KINDS = frozenset(
    {
        "checking",
        "manifest_ok",
        "value_equal",
        "value_changed",
        "observation_failed",
        "transport_unavailable",
    }
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


def _iso(moment: datetime | None) -> str:
    return (moment or datetime.now()).isoformat()


def ensure_schema(store) -> None:
    """幂等附加 observer 所需既有辅助表（仅写入口/构造期调用）。"""
    with store._write_lock:
        conn = store._connect()
        try:
            conn.executescript(_AUX_SCHEMA)
        finally:
            conn.close()


def _baseline_pair(store) -> tuple[str | None, str | None]:
    try:
        current = store.current()
    except AppError:
        return (None, None)
    return (str(current["bundle_id"]), str(current["bundle_hash"]))


def data_target(path: str) -> str:
    """旧逻辑路径 → 映射后的 data-relative 路径（已是目标路径则原样）。"""
    return STOP_AUTO_TARGETS.get(path, path)


def _detail_key(source_id: str, period: str, path: str) -> str:
    return f"{META_PREFIX}/{source_id}/{period}/{data_target(path)}"


def _state_value(path: str) -> str:
    return OBSERVER_STATE_PREFIX + data_target(path)


def periods_for(path: str) -> tuple[str, ...]:
    """观察路径影响的课税年度键；MPF 上限分别覆盖三个支持年度。"""
    if "mpf_deduction_cap" in path:
        return SUPPORTED_YEARS
    match = _PERIOD_RE.search(path)
    return (match.group(1),) if match else ()


def _get_path(node: Any, fullpath: str) -> Any:
    current = node
    for part in fullpath.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _event_digest(event: Mapping[str, Any]) -> str:
    payload = {
        "sequence": event["sequence"],
        "baseline_bundle_id": event["baseline_bundle_id"],
        "baseline_bundle_hash": event["baseline_bundle_hash"],
        "kind": event["kind"],
        "detail": event["detail"],
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _make_event(
    *,
    sequence: int,
    baseline_id: str | None,
    baseline_hash: str | None,
    kind: str,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    event = {
        "sequence": int(sequence),
        "baseline_bundle_id": baseline_id,
        "baseline_bundle_hash": baseline_hash,
        "kind": kind,
        "detail": dict(detail),
    }
    event["event_digest"] = _event_digest(event)
    return event


def _valid_baseline_pair(value_id: Any, value_hash: Any) -> bool:
    if value_id is None and value_hash is None:
        return True
    return (
        isinstance(value_id, str)
        and bool(value_id)
        and isinstance(value_hash, str)
        and bool(value_hash)
    )


def _valid_event(event: Any) -> bool:
    if not isinstance(event, Mapping):
        return False
    try:
        sequence = int(event.get("sequence"))
    except (TypeError, ValueError):
        return False
    if sequence < 1:
        return False
    if event.get("kind") not in _EVENT_KINDS:
        return False
    if not isinstance(event.get("detail"), Mapping):
        return False
    if not _valid_baseline_pair(
        event.get("baseline_bundle_id"), event.get("baseline_bundle_hash")
    ):
        return False
    digest = event.get("event_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return False
    try:
        return _event_digest(event) == digest
    except (TypeError, ValueError):
        return False


def _valid_detail(detail: Any, *, source_id: str, path: str, period: str) -> bool:
    if not isinstance(detail, Mapping):
        return False
    state_path = data_target(path)
    if (
        detail.get("source_id") != source_id
        or detail.get("path") != state_path
        or detail.get("period") != period
        or not isinstance(detail.get("sticky"), bool)
    ):
        return False
    latest = detail.get("latest_observation")
    first = detail.get("first_observation")
    if not _valid_event(latest):
        return False
    if first is not None and not _valid_event(first):
        return False
    if detail.get("sticky"):
        if first is None:
            return False
        try:
            if int(latest["sequence"]) < int(first["sequence"]):
                return False
        except (TypeError, ValueError, KeyError):
            return False
    resolution = detail.get("resolution")
    if resolution is not None and not isinstance(resolution, Mapping):
        return False
    return True


def _load_detail(conn, source_id: str, period: str, path: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT value_json FROM updater_meta WHERE key = ?",
        (_detail_key(source_id, period, path),),
    ).fetchone()
    if row is None:
        return None
    try:
        detail = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return detail if isinstance(detail, dict) else None


def _save_detail(
    conn, source_id: str, period: str, path: str, detail: Mapping[str, Any]
) -> None:
    payload = json.dumps(dict(detail), ensure_ascii=False, sort_keys=True)
    conn.execute(
        "INSERT INTO updater_meta (key, value_json) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
        (_detail_key(source_id, period, path), payload),
    )


def _upsert_state(
    conn, source_id: str, path: str, period: str, entered_at: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO updater_source_states"
        " (source_id, state, period, entered_at) VALUES (?,?,?,?)",
        (source_id, _state_value(path), period or "", entered_at),
    )


def _delete_state(conn, source_id: str, path: str, period: str) -> None:
    conn.execute(
        "DELETE FROM updater_source_states"
        " WHERE source_id = ? AND state = ? AND period = ?",
        (source_id, _state_value(path), period or ""),
    )


def _state_exists(conn, source_id: str, path: str, period: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM updater_source_states"
        " WHERE source_id = ? AND state = ? AND period = ?",
        (source_id, _state_value(path), period or ""),
    ).fetchone()
    return row is not None


def _corrupt_event(
    *, sequence: int, store, clock: Any
) -> dict[str, Any]:
    baseline_id, baseline_hash = _baseline_pair(store)
    return _make_event(
        sequence=max(1, sequence),
        baseline_id=baseline_id,
        baseline_hash=baseline_hash,
        kind="observation_failed",
        detail={
            "phase": "parse",
            "code": "interrupted_check",
            "snapshot_hash": None,
        },
    )


def apply_event(
    conn,
    *,
    store,
    source_id: str,
    path: str,
    period: str,
    kind: str,
    event_detail: Mapping[str, Any],
    sticky: bool | None,
    clock: Any = None,
) -> dict[str, Any]:
    """在调用方事务连接内追加一条观察事件（不做锁/事务边界）。"""
    state_path = data_target(path)
    prior = _load_detail(conn, source_id, period, state_path)
    prior_valid = _valid_detail(
        prior, source_id=source_id, path=state_path, period=period
    )
    state_present = _state_exists(conn, source_id, state_path, period)
    corrupt_prior = (prior is not None and not prior_valid) or (
        prior is None and state_present
    )
    prior_sticky = bool((prior or {}).get("sticky")) if prior_valid else corrupt_prior
    if sticky is None:
        sticky = prior_sticky

    prior_latest = (prior or {}).get("latest_observation") if prior_valid else None
    sequence = 1
    if isinstance(prior_latest, Mapping):
        try:
            sequence = int(prior_latest.get("sequence") or 0) + 1
        except (TypeError, ValueError):
            sequence = 1
    baseline_id, baseline_hash = _baseline_pair(store)
    event = _make_event(
        sequence=sequence,
        baseline_id=baseline_id,
        baseline_hash=baseline_hash,
        kind=kind,
        detail=event_detail,
    )
    first = (prior or {}).get("first_observation") if prior_valid else None
    if corrupt_prior and not isinstance(first, Mapping):
        first = _corrupt_event(sequence=sequence, store=store, clock=clock)
        sticky = True
    if sticky:
        if not isinstance(first, Mapping):
            first = event
    else:
        first = None
    detail = {
        "source_id": source_id,
        "path": state_path,
        "period": period,
        "sticky": bool(sticky),
        "first_observation": first,
        "latest_observation": event,
        "resolution": (prior or {}).get("resolution") if prior_valid else None,
    }
    _save_detail(conn, source_id, period, state_path, detail)
    if sticky or kind == "checking" or corrupt_prior:
        # checking 虽非 sticky，也必须是 durable 阻断行（不得有无阻断读窗口）。
        _upsert_state(
            conn,
            source_id,
            state_path,
            period,
            _iso(clock.now() if clock else None),
        )
    else:
        _delete_state(conn, source_id, state_path, period)
    return detail


def record_event(
    store,
    *,
    source_id: str,
    path: str,
    period: str,
    kind: str,
    event_detail: Mapping[str, Any],
    sticky: bool | None,
    clock: Any = None,
) -> dict[str, Any]:
    """独立事务记录观察事件（非组合 notice 同步场景）。"""
    ensure_schema(store)
    with store._write_lock:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            detail = apply_event(
                conn,
                store=store,
                source_id=source_id,
                path=path,
                period=period,
                kind=kind,
                event_detail=event_detail,
                sticky=sticky,
                clock=clock,
            )
            conn.execute("COMMIT")
            return detail
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def normalize_observed(text: str, slot: Mapping[str, Any]) -> str:
    """观察文本按既有 AmountStr 校验/canonical 化，再检查原 slot 值域/步长。"""
    try:
        canonical = parse_amount(text)
        amount = Decimal(canonical)
    except (AppError, ValueError, TypeError) as exc:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"观察值不符合 C2.1 金额语法：{text!r}",
        ) from exc
    constraints = slot.get("constraints") or {}
    minimum = constraints.get("min")
    maximum = constraints.get("max")
    step = constraints.get("step")
    if minimum is not None and amount < Decimal(str(minimum)):
        raise AppError("E_RULES_STATE_UNVERIFIABLE", f"观察值低于下限：{text!r}")
    if maximum is not None and amount > Decimal(str(maximum)):
        raise AppError("E_RULES_STATE_UNVERIFIABLE", f"观察值超过上限：{text!r}")
    if step is not None:
        step_decimal = Decimal(str(step))
        base = Decimal(str(minimum)) if minimum is not None else Decimal("0")
        if step_decimal != 0 and (amount - base) % step_decimal != 0:
            raise AppError("E_RULES_STATE_UNVERIFIABLE", f"观察值不满足步长：{text!r}")
    return canonical


def evaluate_value(
    slot: Mapping[str, Any], observed_text: str, current_content: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """比较观察值与当前已发布 baseline；返回 (kind, detail)。"""
    path = str(slot.get("fullpath") or "")
    target = data_target(path)
    data = current_content.get("data") if isinstance(current_content, Mapping) else None
    baseline_raw = _get_path(data, target)
    if baseline_raw is None:
        raise AppError(
            "E_RULES_STATE_UNVERIFIABLE",
            f"观察目标 baseline 缺失（不得补零）：{target!r}",
        )
    baseline = normalize_observed(str(baseline_raw), slot)
    observed = normalize_observed(observed_text, slot)
    if observed == baseline:
        return ("value_equal", {"value": observed})
    return (
        "value_changed",
        {"baseline_value": baseline, "observed_value": observed},
    )


def public_state_for(store, source_id: str, state: str, period: str) -> str | None:
    """内部 observer 行 → 既有公开状态（不新增对外枚举；None＝不导出）。

    只读路径：不取 store._write_lock，不做 schema 初始化（F1）。
    """
    if not state.startswith(OBSERVER_STATE_PREFIX):
        return state
    path = state[len(OBSERVER_STATE_PREFIX):]
    conn = store._connect()
    try:
        detail = _load_detail(conn, source_id, period, path)
    finally:
        conn.close()
    if not _valid_detail(detail, source_id=source_id, path=path, period=period):
        return "unknown"
    assert isinstance(detail, Mapping)
    if not detail.get("sticky"):
        return "unknown"
    latest = detail.get("latest_observation")
    first = detail.get("first_observation")
    latest_kind = latest.get("kind") if isinstance(latest, Mapping) else None
    first_kind = first.get("kind") if isinstance(first, Mapping) else None
    if latest_kind == "value_changed":
        return "related_change_uncertain"
    if latest_kind in {"observation_failed", "checking"}:
        return "unknown"
    if latest_kind == "transport_unavailable":
        return "unknown"
    return (
        "related_change_uncertain" if first_kind == "value_changed" else "unknown"
    )


def _proof_digest(proof: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(dict(proof))).hexdigest()


def resolve_observer_pending(
    store,
    *,
    source_id: str,
    path: str,
    period: str,
    resolution: Mapping[str, Any],
    clock: Any = None,
) -> dict[str, Any]:
    """受控 Resolution 是唯一清除入口；独立 proof/approved target 必须匹配。"""
    if not isinstance(resolution, Mapping):
        raise AppError("E_UPDATE_QUARANTINED", "Resolution 必须为映射")
    disposition = resolution.get("disposition")
    if disposition not in {"no_effect", "approved_bundle"}:
        raise AppError("E_UPDATE_QUARANTINED", "Resolution disposition 非法")
    proof_refs = resolution.get("proof_refs")
    if (
        not isinstance(proof_refs, Sequence)
        or isinstance(proof_refs, (str, bytes))
        or not proof_refs
    ):
        raise AppError("E_UPDATE_QUARANTINED", "Resolution 缺独立 proof_refs")
    proof = resolution.get("proof")
    proof_sha = resolution.get("proof_sha256")
    if not isinstance(proof, Mapping) or not isinstance(proof_sha, str):
        raise AppError("E_UPDATE_QUARANTINED", "Resolution 缺独立 proof 内容")
    try:
        expected_proof_sha = _proof_digest(proof)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "E_UPDATE_QUARANTINED", "Resolution proof 不可 canonical 化"
        ) from exc
    if proof_sha != expected_proof_sha:
        raise AppError("E_UPDATE_QUARANTINED", "Resolution proof_sha256 不符")
    if (
        proof.get("source_id") != source_id
        or proof.get("path") != path
        or proof.get("period") != period
        or proof.get("disposition") != disposition
    ):
        raise AppError("E_UPDATE_QUARANTINED", "Resolution proof 身份不符")
    target_hash = resolution.get("target_bundle_hash")
    approved_bundle = resolution.get("approved_bundle")
    if disposition == "approved_bundle":
        if not isinstance(approved_bundle, Mapping):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "approved_bundle Resolution 缺 approved_bundle 目标",
            )
        approved_id = approved_bundle.get("target_bundle_id")
        approved_hash = approved_bundle.get("target_bundle_hash")
        if (
            not isinstance(approved_id, str)
            or not approved_id
            or not isinstance(approved_hash, str)
            or not approved_hash
            or not isinstance(target_hash, str)
            or target_hash != approved_hash
        ):
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "approved_bundle.target_bundle_hash 与 target_bundle_hash 不符",
            )
    else:
        if approved_bundle is not None or target_hash is not None:
            raise AppError(
                "E_UPDATE_QUARANTINED",
                "no_effect Resolution 不得携带 approved target",
            )
    ensure_schema(store)
    with store._write_lock:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            detail = _load_detail(conn, source_id, period, path)
            if not _valid_detail(
                detail, source_id=source_id, path=path, period=period
            ) or not detail.get("sticky"):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "无有效 sticky observer pending（详情缺失/损坏不得清除）",
                )
            assert isinstance(detail, Mapping)
            first = detail.get("first_observation")
            latest = detail.get("latest_observation")
            assert isinstance(first, Mapping) and isinstance(latest, Mapping)
            if resolution.get("pending_origin_digest") != first.get("event_digest"):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "Resolution 未匹配 first sticky 原因",
                )
            if resolution.get("latest_event_digest") != latest.get("event_digest"):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "Resolution 未匹配实际最新事件",
                )
            current_row = conn.execute(
                "SELECT rb.bundle_id, rb.bundle_hash FROM current_pointer cp"
                " JOIN rule_bundles rb ON rb.bundle_id = cp.bundle_id"
                " WHERE cp.id = 1"
            ).fetchone()
            current_id = str(current_row[0]) if current_row else None
            current_hash = str(current_row[1]) if current_row else None
            if (
                resolution.get("expected_current_bundle_id") != current_id
                or resolution.get("expected_current_bundle_hash") != current_hash
            ):
                raise AppError(
                    "E_UPDATE_QUARANTINED",
                    "Resolution 复用了旧 baseline（当前指针已变化）",
                )
            if disposition == "approved_bundle":
                assert isinstance(approved_bundle, Mapping)
                approved_row = conn.execute(
                    "SELECT rb.bundle_hash FROM rule_bundles rb"
                    " JOIN publications p ON p.bundle_id = rb.bundle_id"
                    " WHERE rb.bundle_id = ?",
                    (str(approved_bundle["target_bundle_id"]),),
                ).fetchone()
                if approved_row is None or str(approved_row[0]) != str(
                    approved_bundle["target_bundle_hash"]
                ):
                    raise AppError(
                        "E_UPDATE_QUARANTINED",
                        "approved_bundle 目标不是已发布的批准目标",
                    )
            cleared = dict(detail)
            cleared["sticky"] = False
            cleared["first_observation"] = first
            cleared["latest_observation"] = latest
            cleared["resolution"] = {
                "disposition": disposition,
                "pending_origin_digest": first.get("event_digest"),
                "latest_event_digest": latest.get("event_digest"),
                "expected_current_bundle_id": current_id,
                "expected_current_bundle_hash": current_hash,
                "proof_refs": list(proof_refs),
                "proof": dict(proof),
                "proof_sha256": proof_sha,
                "approved_bundle": (
                    dict(approved_bundle)
                    if isinstance(approved_bundle, Mapping)
                    else None
                ),
                "target_bundle_hash": target_hash,
                "resolved_at": _iso(clock.now() if clock else None),
            }
            _save_detail(conn, source_id, period, path, cleared)
            _delete_state(conn, source_id, path, period)
            conn.execute("COMMIT")
            return cleared
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
