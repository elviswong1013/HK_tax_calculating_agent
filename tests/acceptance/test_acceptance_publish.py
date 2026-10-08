"""REQ-18 发布原子性与门禁隔离证据（终验 REJECTED 缺口 Red）。

REQ: REQ-18（发布＝单事务原子指针切换＋audit；门禁在隔离环境运行，
Annex C C8.7）。
验收发现锚点（终验 REJECTED）:
  - app/updater/publisher.publish_update：store.publish（bundle＋publication
    ＋指针切换）先以独立事务 COMMIT，publish 类 audit 随后另开事务补写——
    audit 写失败时 bundle/指针/publication 已落库，出现部分状态；
  - app/updater/testgate.run_gate：gate_environment 四断言为硬编码布尔
    （{"temp_store": True, "network": False, ...}），无临时 store 路径、
    无网络调用计数等真实隔离证据。
规格锚点:
  - SDD §5 发布行（单事务：插入 bundle＋publication＋指针切换＋audit，无新
    旧参数混用；任一步失败 → 整体拒绝、零部分状态）；Annex C C5.6/C5.8。
  - Annex C C8.6/C8.7：发布前验证报告结构含 gate_environment；门禁在临时
    store 执行——无网络、无用户 session、不写 current pointer。
期望值来源: 结构性契约（原子性/审计/隔离证据），无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - publish audit 写失败注入 seam：store._connect() 返回代理连接，凡执行
    INSERT INTO audit_log 且首参数为 "publish" 即注入 sqlite3 失败——
    publish_update 必须把 publish 类 audit 纳入发布同一事务（或等价保证
    audit 失败 → 发布整体回滚）。
  - gate_environment 证据键：temp_store_path（非空绝对路径字符串，指向
    门禁实际使用的临时 store，须存在于磁盘且 ≠ 现行 store 路径）、
    network_calls（整数出站调用计数，== 0）、user_session（False）、
    current_pointer_written（False）——不得以四个硬编码布尔充数。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from _acc_helpers import (
    BUDGET_MANIFEST,
    FACT_CAP_3500,
    GATE_CASE,
    REPORT_PUBLISH_OK,
    StrictOutbound,
    changed_bundle_content,
    initial_content,
)


# ---------------------------------------------------------------------------
# publish audit 写失败注入（代理连接：仅 publish 类 audit INSERT 失败）
# ---------------------------------------------------------------------------
class _AuditFailConnection:
    """sqlite 连接代理：INSERT INTO audit_log 且 event_type==fail_event 时注入失败。"""

    def __init__(self, conn, fail_event: str) -> None:
        self._conn = conn
        self._fail_event = fail_event

    def execute(self, sql: str, params=()):
        if (
            sql.lstrip().upper().startswith("INSERT INTO AUDIT_LOG")
            and params
            and params[0] == self._fail_event
        ):
            raise sqlite3.OperationalError(
                f"注入失败：audit_log 写入错误（event_type={self._fail_event}）"
            )
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _make_publish_audit_fail_store(db_path):
    """RuleStore 子类工厂：仅 publish 类 audit 写入失败，其余连接行为不变。

    覆写 _connect 使**一切**经 store 连接的写路径（store.publish／
    publisher._write_audit／未来把 audit 并入发布事务的实现）都流经代理——
    生产代码路径真实执行，仅 event_type=="publish" 的 audit_log INSERT 注入失败。
    """
    from app.rules.store import RuleStore

    class _PublishAuditFailStore(RuleStore):
        def _connect(self):
            return _AuditFailConnection(super()._connect(), "publish")

    return _PublishAuditFailStore(db_path)


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _audit_events(db_path, event_type: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT detail_json FROM audit_log WHERE event_type = ?",
            (event_type,),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def test_acceptance_publish_audit_same_transaction(tmp_path) -> None:
    """publish audit 写失败注入 → bundle／publications／current pointer 全部
    回滚（单事务原子，零部分状态；不得只补写 audit 而留下已发布新束）。"""
    from app.updater.publisher import publish_update

    db_path = tmp_path / "publish-audit.db"
    store = _make_publish_audit_fail_store(db_path)
    # 初始束发布走 rule 类 audit（不受注入影响），作为「此前已发布版本」
    store.publish(initial_content(), evidence_refs=[])
    before_pointer = store.current()["bundle_id"]
    pubs_before = _count(db_path, "publications")
    bundles_before = _count(db_path, "rule_bundles")

    with pytest.raises(Exception) as excinfo:
        publish_update(
            store,
            job_id="job-acc-audit-1",
            content=changed_bundle_content("3500"),
            evidence_refs=[],
            validation_report=REPORT_PUBLISH_OK,
        )
    # 注入的失败须真实发生（而非其他路径的意外异常冒充）
    assert "注入失败" in str(excinfo.value), (
        f"失败须来自 publish audit 写入注入，实际 {excinfo.value!r}"
    )

    # —— 零部分状态：bundle／publication／指针／publish audit 一致回滚 ——
    assert store.current()["bundle_id"] == before_pointer, (
        "REQ-18：publish audit 写失败 → 指针不得切换（单事务原子）；"
        "当前 audit 在发布事务之外补写，指针已指向新束（部分状态）"
    )
    assert _count(db_path, "publications") == pubs_before, (
        "REQ-18：audit 写失败 → publications 不得新增（零部分状态）"
    )
    assert _count(db_path, "rule_bundles") == bundles_before, (
        "REQ-18：audit 写失败 → 候选 bundle 不得留库（零部分状态）"
    )
    assert not _audit_events(db_path, "publish"), (
        "失败路径不得留下任何 publish 类 audit 事件"
    )


def test_acceptance_testgate_real_isolation_evidence(tmp_path) -> None:
    """门禁报告须含真实隔离证据：临时 store 路径（存在且≠现行 store）、
    网络零调用计数（整数）、未写 current pointer——非硬编码布尔。"""
    from app.updater.testgate import run_gate

    db_path = tmp_path / "gate.db"
    from app.rules.store import RuleStore

    store = RuleStore(db_path)
    store.publish(initial_content(), evidence_refs=[])
    before_pointer = store.current()["bundle_id"]

    outbound = StrictOutbound()  # 门禁内任何出站尝试即 AssertionError（C8.7）
    harness = _FakeHarnessLike()
    engine = _FakeEngineLike()
    report = run_gate(
        candidate_content=changed_bundle_content("3500"),
        current_content=initial_content(),
        manifests=[BUDGET_MANIFEST],
        independent_facts=[FACT_CAP_3500],
        required_cases=[GATE_CASE],
        harness=harness,
        engine=engine,
        outbound=outbound,
    )
    # 前置自证：独立证据齐备＋逐阶段一致 → 通过（既有绿行为）
    assert report["decision"] == "publish", (
        f"前置自证失败：齐备候选应通过门禁：{report!r}"
    )

    ge = report.get("gate_environment") or {}

    # —— 真实证据 1：临时 store 路径（非空字符串、存在于磁盘、≠现行 store）——
    temp_store_path = ge.get("temp_store_path")
    assert isinstance(temp_store_path, str) and temp_store_path.strip(), (
        "C8.7：gate_environment 须携带真实临时 store 路径 temp_store_path"
        f"（当前为硬编码布尔）：{ge!r}"
    )
    assert temp_store_path != str(db_path), (
        f"临时 store 不得指向现行 store 路径：{temp_store_path!r}"
    )
    assert Path(temp_store_path).exists(), (
        f"临时 store 路径须真实存在（可审计证据）：{temp_store_path!r}"
    )

    # —— 真实证据 2：网络零调用计数（整数计数，非布尔）——
    network_calls = ge.get("network_calls")
    assert (
        isinstance(network_calls, int) and not isinstance(network_calls, bool)
    ), (
        "C8.7：gate_environment 须携带整数网络调用计数 network_calls"
        f"（当前为硬编码布尔）：{ge!r}"
    )
    assert network_calls == 0, f"门禁内网络调用必须为 0，实际 {network_calls}"

    # —— 真实证据 3/4：无用户 session、未写 current pointer ——
    assert ge.get("user_session") is False, f"user_session 须 False：{ge!r}"
    assert ge.get("current_pointer_written") is False, (
        f"current_pointer_written 须 False：{ge!r}"
    )

    # —— 外部对账：严格出站零尝试＋现行 store 指针未动 ——
    assert not outbound.requests, "门禁运行环境不得有任何出站尝试（C8.7）"
    assert store.current()["bundle_id"] == before_pointer, (
        "门禁不得写现行 current pointer（发布只发生在门禁外）"
    )


class _FakeHarnessLike:
    """独立参考 harness：与候选一致的对照输出＋T1 独立参数证据。"""

    def run_case(self, case_id: str) -> dict:
        return {
            "case_id": case_id,
            "stages": ["3500"],
            "output": "3500",
            "parameter_evidence_ref": FACT_CAP_3500["fact_id"],
        }


class _FakeEngineLike:
    """候选引擎：与 harness 输出一致（对照通过用）。"""

    def run_case(self, case_id: str, candidate_content: dict) -> dict:
        return {"case_id": case_id, "stages": ["3500"], "output": "3500"}
