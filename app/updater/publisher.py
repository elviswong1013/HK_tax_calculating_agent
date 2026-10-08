"""发布与回滚（SDD §5 发布/回滚行；Annex C C5.6/C5.8/C8.4；REQ-18）。

REQ: 18（§9：发布＝单事务原子指针切换＋audit；全验证自动发布（无点击）；
回滚仅审计化指针、不法律时间旅行——不得把已失效旧规则宣称为现行有效，
受影响期间拒算并说明）。
规格锚点:
  - SDD §5：发布＝单事务内（插入 bundle＋publication＋指针切换），无新旧参数
    混用；回滚仅审计化指针回退到此前已发布版本，写 audit_log。
  - Annex C C5.6：check/publish/rollback 三者互斥；单事务原子。
  - Annex C C5.8：audit_log 四类事件（rule/check/publish/rollback）。
  - Annex C C8.4：空报告/未通过门禁 → 不得发布。
期望值来源: 结构性契约（原子性/审计/拒绝语义），无金额期望。

实现注记: 发布复用既有 RuleStore.publish 单事务原子契约（bundle＋publication
＋指针切换＋rule 类 audit；缺 C2.8 封闭字段在写库前整体拒绝、零部分状态），
本模块仅前置门禁报告门，并把 publish 类 audit 负载传入同一事务（audit 写失败
→ 发布整体回滚，SDD §5 单事务原子；C5.8）；回滚＝单事务内指针更新＋rollback
类 audit（affected_periods 由两版本数据差计算，随审计持久——调度器
availability 据此阻断受影响期间，重启/回滚不清，C5.6）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.core.errors import AppError

_PERIOD_RE = re.compile(r"(?<!\d)(\d{4}_\d{2})(?!\d)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def publish_update(
    store,
    *,
    job_id: str,
    content: Mapping[str, Any],
    evidence_refs: Sequence[Mapping[str, Any]],
    validation_report: Mapping[str, Any] | None,
) -> dict[str, str]:
    """门禁报告门（C8.4）＋既有 store 单事务原子发布＋publish 类 audit。

    validation_report["decision"] 必须为 "publish" 且
    report["tests"]["passed"] > 0，否则拒绝发布（AppError）。content 缺
    C2.8 封闭字段 → store.publish 写库前整体拒绝（零部分状态）。
    """
    report = dict(validation_report or {})
    tests = report.get("tests")
    passed = tests.get("passed", 0) if isinstance(tests, Mapping) else 0
    if report.get("decision") != "publish" or not isinstance(passed, int) or passed <= 0:
        raise AppError(
            "E_UPDATE_QUARANTINED",
            (
                "发布被拒绝（C8.4 门禁报告门）："
                f"decision={report.get('decision')!r}，tests.passed={passed!r}"
            ),
        )
    # 复用既有原子契约：bundle＋publication＋指针切换＋rule/publish audit 单事务。
    # publish 类 audit 负载随发布同事务写入：audit 写失败 → 整体回滚（零部分状态）。
    return store.publish(
        content,
        evidence_refs,
        publish_audit={"action": "update_publish", "job_id": job_id},
    )


def _affected_periods(
    content_a: Mapping[str, Any], content_b: Mapping[str, Any]
) -> list[str]:
    """两版本 data 差所涉课税年度键（路径中 YYYY_NN 形态，如 2025_26）。"""
    differing: list[list[str]] = []

    def walk(node_a: Any, node_b: Any, prefix: list[str]) -> None:
        if isinstance(node_a, dict) and isinstance(node_b, dict):
            for key in sorted(set(node_a) | set(node_b)):
                walk(node_a.get(key), node_b.get(key), prefix + [str(key)])
        elif isinstance(node_a, list) and isinstance(node_b, list):
            if len(node_a) != len(node_b):
                differing.append(prefix)
            else:
                for index, (item_a, item_b) in enumerate(zip(node_a, node_b)):
                    walk(item_a, item_b, prefix + [str(index)])
        elif node_a != node_b:
            differing.append(prefix)

    walk(content_a.get("data"), content_b.get("data"), [])
    periods: set[str] = set()
    for path in differing:
        periods.update(_PERIOD_RE.findall(".".join(path)))
    return sorted(periods)


def rollback_to(store, *, target_bundle_id: str, reason: str) -> dict[str, Any]:
    """回滚＝审计化指针回退到此前已发布版本（单事务）；不法律时间旅行。

    未知/未发布目标 → AppError(code="E_UPDATE_ROLLBACK_INVALID")。
    返回 {"rolled_back_to", "from_bundle_id", "affected_periods", "notice"}；
    affected_periods 随 rollback 类 audit 持久（availability 据此阻断受影响
    期间；重启/回滚不清，Annex C C5.6）。目标 bundle 内容不被改写。
    """
    from_bundle_id = store.current()["bundle_id"]
    conn = store._connect()
    try:
        published = conn.execute(
            "SELECT COUNT(*) FROM publications WHERE bundle_id = ?",
            (target_bundle_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    if not published:
        raise AppError(
            "E_UPDATE_ROLLBACK_INVALID",
            f"回滚目标不是此前已发布版本（仅允许回退到 publications 在档版本）：{target_bundle_id}",
        )
    affected = _affected_periods(
        store.get_bundle(from_bundle_id), store.get_bundle(target_bundle_id)
    )
    detail = {
        "action": "rollback",
        "from_bundle_id": from_bundle_id,
        "to_bundle_id": target_bundle_id,
        "reason": reason,
        "affected_periods": affected,
    }
    with store._write_lock:
        conn = store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
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
            except Exception:
                pass
            raise
        finally:
            conn.close()
    return {
        "rolled_back_to": target_bundle_id,
        "from_bundle_id": from_bundle_id,
        "affected_periods": affected,
        "notice": (
            f"已回滚至此前已发布版本 {target_bundle_id}；"
            f"受影响期间（{'、'.join(affected) if affected else '无'}）的现行计算"
            "已拒绝，不把旧规则宣称为现行有效；待重新核验并发布后恢复。"
        ),
    }
