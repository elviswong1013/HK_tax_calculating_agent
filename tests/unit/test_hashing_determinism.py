"""REQ-9 canonical JSON 与哈希对象封闭字段清单（core 确定性）。

REQ: REQ-9（确定性：同（输入, bundle, 引擎核心）→ 字节一致；请求/响应绑定版本
哈希；SDD §3/§9）。
规格锚点:
  - Annex C C2.7 Canonical JSON：键排序、UTF-8、无空白分隔符；解析期拒绝重复键。
  - Annex C C2.8 哈希对象冻结（封闭字段清单）：实现者不得增删字段；
    `RuleBundleContent`（bundle_hash 的唯一哈希对象）恰含 6 字段：
    rules_schema_version／effective／applicability／frozen_semantics／data／
    evidence_digests；bundle_id、bundle_hash 自身、发布运行时 id、抓取/发布
    时间戳、快照存储字段均不在该对象内。
    `CalculationCore`（核心计算记录 hash 的唯一哈希对象）恰含 11 字段：
    tax_type／input／binding／status／amounts／blocked_reason／steps／
    evidence_refs／unsupported／pending_verification／missing_components；
    运行时信封（request_id、record_id、job id、墙钟时间戳、warnings、
    questions、分页、health/网络字段）完全在哈希对象之外。
    core 确定性＝同（输入, bundle, 引擎核心）→ 同 CalculationCore 序列化字节。
期望值来源: 纯结构性质（键排序/字节序/封闭字段集合），无金额数值期望。

【拟名】被测契约（app.core.hashing）:
  - canonical_json_bytes(obj) -> bytes：键排序、UTF-8、紧凑分隔符。
  - canonical_loads(data) -> obj：解析期拒绝重复键（ValueError）。
  - bundle_content_hash(content: Mapping) -> "sha256:<64hex>"：仅哈希 6 封闭字段。
  - calculation_core_hash(core: Mapping) -> "sha256:<64hex>"：仅哈希 11 封闭字段。
"""

from __future__ import annotations

import pytest

from app.core.hashing import (
    bundle_content_hash,
    calculation_core_hash,
    canonical_json_bytes,
    canonical_loads,
)

HEX64 = "0123456789abcdef" * 4


def _bundle_content(marker: str) -> dict:
    """按 C2.8 封闭 6 字段构造 RuleBundleContent 样例（结构性，无规则数值）。"""
    return {
        "rules_schema_version": "1.0.0",
        "effective": "2024-04-01",
        "applicability": {"years": ["2024_25", "2025_26", "2026_27"]},
        "frozen_semantics": {
            "profiles": [
                {"profile_ID": f"salaries_v1_{marker}", "semantic_digest": "sha256:" + HEX64}
            ]
        },
        "data": {"marker": marker},
        "evidence_digests": ["sha256:" + "ab" * 32],
    }


def _calculation_core() -> dict:
    """按 C2.8 封闭 11 字段构造 CalculationCore 样例。"""
    return {
        "tax_type": "salaries_tax",
        "input": {
            "year_of_assessment": "2025_26",
            "employment_income": "240000",
        },
        "binding": {
            "bundle_id": "b-example",
            "bundle_hash": "sha256:" + "11" * 32,
            "rules_schema_version": "1.0.0",
            "engine_version": "0.1.0",
        },
        "status": "blocked",
        "amounts": {
            "before_reduction": None,
            "reduction": None,
            "final_after_reduction": None,
            "provisional_paid": None,
            "next_provisional": None,
            "balance": None,
        },
        "blocked_reason": "rounding_profile_unfrozen",
        "steps": [{"step": "mpf_floor", "value_exact": "9000/1"}],
        "evidence_refs": ["ird_budget#rebate-cap"],
        "unsupported": [],
        "pending_verification": [],
        "missing_components": [],
    }


def test_core_determinism_byte_identical() -> None:
    """C2.7/C2.8：canonical 字节确定 + 哈希仅覆盖封闭字段、信封字段恒在外。"""
    # —— C2.7：canonical JSON 字节（键排序、紧凑、UTF-8）——
    assert canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    assert canonical_json_bytes({"a": 2, "b": 1}) == b'{"a":2,"b":1}'  # 与键序无关
    assert canonical_json_bytes({"x": {"d": 1, "c": 2}, "l": [3, 2, 1]}) == (
        b'{"l":[3,2,1],"x":{"c":2,"d":1}}'  # 数组保序、对象键排序
    )
    assert canonical_json_bytes({"k": "值"}) == '{"k":"值"}'.encode("utf-8")

    # —— C2.7：解析期拒绝重复键 ——
    with pytest.raises(ValueError):
        canonical_loads(b'{"a":1,"a":2}')

    # —— C2.8：bundle_content_hash 仅覆盖封闭 6 字段，运行时/发布字段在外 ——
    base = _bundle_content("seed-a")
    reordered = {
        "evidence_digests": base["evidence_digests"],
        "data": base["data"],
        "frozen_semantics": base["frozen_semantics"],
        "applicability": base["applicability"],
        "effective": base["effective"],
        "rules_schema_version": base["rules_schema_version"],
    }
    with_runtime_extras = dict(base)
    with_runtime_extras.update(
        {
            "bundle_id": "b-runtime-999",  # 运行时字段（C2.8 明列在外）
            "published_at": "2026-10-08T00:00:00+08:00",
            "publication_run_id": "run-7",
            "snapshot_ref": "snap-1",
        }
    )
    h1 = bundle_content_hash(base)
    assert h1 == bundle_content_hash(reordered)
    assert h1 == bundle_content_hash(with_runtime_extras)
    assert h1.startswith("sha256:") and len(h1) == len("sha256:") + 64
    # 封闭字段内容变化 → hash 必须变化
    assert bundle_content_hash(_bundle_content("seed-b")) != h1

    # —— C2.8：calculation_core_hash 仅覆盖封闭 11 字段，运行时信封在外 ——
    core = _calculation_core()
    envelope = dict(core)
    envelope.update(
        {
            "request_id": "req-1",
            "record_id": "r-1",
            "job_id": "job-1",
            "created_at": "2026-10-08T12:00:00Z",
            "warnings": ["w-1"],
            "questions": ["q-1"],
            "pagination": {"page": 1},
            "health": {"upstream": "ok"},
        }
    )
    ch1 = calculation_core_hash(core)
    assert ch1 == calculation_core_hash(envelope)  # 信封字段不参与 core hash
    assert ch1 == calculation_core_hash(dict(reversed(list(core.items()))))
    assert ch1.startswith("sha256:") and len(ch1) == len("sha256:") + 64
    changed = dict(core)
    changed["status"] = "complete"
    assert calculation_core_hash(changed) != ch1

    # —— 确定性：重复调用字节一致 ——
    assert canonical_json_bytes(core) == canonical_json_bytes(core)
    assert bundle_content_hash(base) == h1
    assert calculation_core_hash(core) == ch1
