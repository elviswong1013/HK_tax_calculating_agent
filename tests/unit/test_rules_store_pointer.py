"""REQ-2（M1 基础面：年度 pin 单一完整 bundle）＋ REQ-18（M1 基础面：发布原子指针）。

REQ: REQ-2（计算按年度键 pin 已验证 bundle）、REQ-18（§5：原子指针＋并发 pin）。
规格锚点:
  - SDD §5：SQLite 拟名表 `rule_bundles`、`publications`、`update_jobs`、
    `source_checks`、`snapshots`、`audit_log`、`current_pointer`（单行）；
    发布＝单事务内（插入 bundle＋publication＋指针切换），无新旧参数混用；
    并发计算在请求开始 pin 单一完整 bundle。
  - SDD §5 RuleBundle：官方证据引用（URL＋锚点＋抓取日＋内容 hash＋T1/T2 层级）；
    canonical SHA-256。
  - Annex C C2.8：bundle_hash 唯一哈希对象＝RuleBundleContent 封闭 6 字段。
期望值来源: 纯结构性质（指针/版本一致性、事务原子性），无金额数值期望。

【拟名】被测契约（app.rules.store.RuleStore）:
  - RuleStore(db_path)：构造即建 schema（幂等）。
  - publish(content: Mapping, evidence_refs: Sequence[Mapping]) -> Mapping：
    单事务插入 bundle＋publication 并切换 current_pointer；返回含
    bundle_id／bundle_hash／rules_schema_version；content 缺封闭字段 → 拒绝
    （抛 ValueError 或 AppError），且不留任何部分状态。
  - current() -> Mapping：单行指针指向的完整绑定（bundle_id/bundle_hash/
    rules_schema_version 一致来自同一 bundle，无新旧混用）。
  - pin_current() -> Mapping：请求开始 pin 的单一完整 bundle 快照。
  - get_bundle(bundle_id) -> Mapping：按 id 载入完整 content。
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.core.errors import AppError
from app.core.hashing import bundle_content_hash
from app.rules.store import RuleStore

EVIDENCE = [
    {
        "url": "https://www.ird.gov.hk/eng/tax/budget.htm",
        "anchor": "salaries/rebate-cap",
        "retrieved_at": "2026-10-07",
        "content_hash": "sha256:" + "ab" * 32,
        "tier": "T1",
    }
]


def _content(marker: str) -> dict:
    """C2.8 封闭 6 字段的 bundle content 样例（结构性 marker，无规则数值）。"""
    return {
        "rules_schema_version": "1.0.0",
        "effective": "2024-04-01",
        "applicability": {"years": ["2024_25", "2025_26", "2026_27"]},
        "frozen_semantics": {
            "profiles": [
                {
                    "profile_ID": f"salaries_v1_{marker}",
                    "semantic_digest": "sha256:" + "cd" * 32,
                }
            ]
        },
        "data": {"marker": marker},
        "evidence_digests": ["sha256:" + "ef" * 32],
    }


def test_atomic_pointer_no_mixed_versions(tmp_path) -> None:
    """§5：发布＝单事务（bundle＋publication＋指针切换），失败不留部分状态。"""
    db = tmp_path / "rules.db"
    store = RuleStore(db)

    pub_a = store.publish(_content("seed-a"), EVIDENCE)
    cur = store.current()
    assert cur["bundle_id"] == pub_a["bundle_id"]
    assert cur["bundle_hash"] == pub_a["bundle_hash"]
    assert cur["rules_schema_version"] == "1.0.0"  # §5 冻结 schema 版本

    pub_b = store.publish(_content("seed-b"), EVIDENCE)
    cur_b = store.current()
    # 三元组完全来自同一 bundle —— 无新旧参数混用
    assert cur_b["bundle_id"] == pub_b["bundle_id"]
    assert cur_b["bundle_hash"] == pub_b["bundle_hash"]
    assert cur_b["bundle_hash"] != pub_a["bundle_hash"]

    # 载入的完整 bundle 与指针发布值一致（content 哈希可复算）
    loaded = store.get_bundle(pub_b["bundle_id"])
    assert bundle_content_hash(loaded) == pub_b["bundle_hash"]

    # —— 失败发布（content 缺封闭字段）→ 整体拒绝，不留部分状态 ——
    with pytest.raises((ValueError, AppError)):
        store.publish({"rules_schema_version": "1.0.0"}, EVIDENCE)

    assert store.current()["bundle_id"] == pub_b["bundle_id"]  # 指针未动

    # —— §5 拟名表结构：publications 恰 2 行（无孤儿行）、current_pointer 单行指向 b ——
    conn = sqlite3.connect(db)
    try:
        pubs = conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0]
        assert pubs == 2, f"失败发布不得留下部分 publication 行（实际 {pubs}）"
        pointers = conn.execute(
            "SELECT bundle_id FROM current_pointer"
        ).fetchall()
        assert len(pointers) == 1, "current_pointer 必须单行（§5）"
        assert pointers[0][0] == pub_b["bundle_id"]
    finally:
        conn.close()


def test_concurrent_calc_pins_single_bundle(tmp_path) -> None:
    """§5：并发计算在请求开始 pin 单一完整 bundle —— 永不观测混用版本。"""
    db = tmp_path / "rules.db"
    store = RuleStore(db)

    published: dict[str, str] = {}  # bundle_id -> bundle_hash
    for marker in ("seed-a", "seed-b"):
        pub = store.publish(_content(marker), EVIDENCE)
        published[pub["bundle_id"]] = pub["bundle_hash"]

    # 先取的 pin 在后续发布后仍按原 bundle 完整可解析
    early_pin = store.pin_current()
    assert early_pin["bundle_id"] in published
    pub_c = store.publish(_content("seed-c"), EVIDENCE)
    published[pub_c["bundle_id"]] = pub_c["bundle_hash"]
    assert bundle_content_hash(store.get_bundle(early_pin["bundle_id"])) == (
        early_pin["bundle_hash"]
    )

    # 并发 pin 风暴：每个 pin 的 (bundle_id, bundle_hash) 必须恰为某一次已发布对
    observed: list[tuple[str, str]] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def pin_loop() -> None:
        try:
            for _ in range(200):
                pin = store.pin_current()
                with lock:
                    observed.append((pin["bundle_id"], pin["bundle_hash"]))
        except BaseException as exc:  # pragma: no cover - 仅记录意外异常
            errors.append(exc)

    threads = [threading.Thread(target=pin_loop) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"并发 pin 不得抛异常：{errors!r}"
    assert observed, "并发 pin 应产生观测样本"

    for bundle_id, bundle_hash in observed:
        assert bundle_id in published, f"观测到未发布 bundle：{bundle_id!r}"
        assert published[bundle_id] == bundle_hash, (
            f"混用版本：bundle_id={bundle_id!r} 搭配了不属于它的 bundle_hash"
        )

    # 最终指针收敛到最后一次发布
    final_pin = store.pin_current()
    assert final_pin["bundle_id"] == pub_c["bundle_id"]
    assert final_pin["bundle_hash"] == published[pub_c["bundle_id"]]
