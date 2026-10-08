"""REQ-2/9/13 初始束 evidence_digests 溯源完备（第二轮 REJECTED 缺口 Red）。

REQ: REQ-2（规则束结构含官方出处摘要）／REQ-9（入数口径可溯源）／
REQ-13（record 官方出处直读束证据）。
验收发现锚点（终验第二轮 REJECTED）:
  - app/rules/bundle._EVIDENCE_DIGESTS：条目仅
    {tier, ledger_anchor, source_id, covers}（9 条中仅 2 条带 url）——
    无 fetched_at、无 content_hash：出处摘要不可核验（「声称来自官方源」
    而无抓取日与内容指纹）。
规格锚点:
  - SDD §9 REQ-15 行（official fixture/证据须 T1 溯源：官方 URL＋锚点＋
    抓取日＋层级）；Annex C C8.2 结构化出处。
  - 台账 §8（docs/research/001-hk-tax-rule-evidence.md）：T1 引文均锚定
    2026-10-08 抓取的页面快照（.playwright-mcp/page-2026-10-08T*.yml，
    如 cap112 → page-2026-10-08T04-16-44-396Z.yml、cap117 →
    page-2026-10-08T04-10-43-739Z.yml）——本地保存件可实算 sha256。
期望值来源: 结构/指纹断言（键形状、日期格式、sha256 前缀、与本地保存件
  hash 一致）；无金额数值期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - evidence_digests 每项补齐溯源五件：url（https 官方源）＋fetched_at
    （YYYY-MM-DD）＋content_hash（sha256:<64hex>）＋tier（"T1"）＋
    ledger_anchor（台账 §1 条目号）；其中至少 5 项的 content_hash 可从
    本地保存的官方快照（.playwright-mcp/page-*.yml 等台账 §8 引用件）
    实算复核，且 fetched_at 与快照抓取日（2026-10-08）一致。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")

# 台账 §8 引用的本地保存官方快照（页面取证件）＋官方 fixture 目录
LOCAL_SNAPSHOT_GLOBS = (
    ".playwright-mcp/*.yml",
    "tests/fixtures/official/*",
)

# 本地快照抓取日（文件名内嵌 2026-10-08；台账 §8 同日直读）
SNAPSHOT_FETCH_DAY = "2026-10-08"


def _local_snapshot_hashes() -> set[str]:
    hashes: set[str] = set()
    for pattern in LOCAL_SNAPSHOT_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if path.is_file():
                hashes.add(
                    "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                )
    return hashes


def test_acceptance_bundle_evidence_digests_complete(make_acc) -> None:
    """实际发布的束：evidence_digests 每项含 url＋fetched_at＋content_hash
    （sha256 前缀）＋tier＋ledger_anchor；≥5 项 content_hash 与本地保存的
    T1 官方快照实算 hash 一致（fetched_at＝快照抓取日）。"""
    acc = make_acc("digests3")
    bundle = acc.store.get_bundle(acc.store.current()["bundle_id"])
    digests = bundle.get("evidence_digests") or []
    assert digests, "REQ-2/13：evidence_digests 不得为空"

    local_hashes = _local_snapshot_hashes()
    problems: list[str] = []
    snapshot_backed = 0

    for index, entry in enumerate(digests):
        label = f"evidence_digests[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{label}: 须为结构化映射，实际 {entry!r}")
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            problems.append(f"{label}: url 须为 https 官方源，实际 {url!r}")
        fetched_at = entry.get("fetched_at")
        if not isinstance(fetched_at, str) or DATE_RE.fullmatch(fetched_at) is None:
            problems.append(
                f"{label}: fetched_at 须为 YYYY-MM-DD 抓取日，实际 {fetched_at!r}"
            )
        content_hash = entry.get("content_hash")
        if not isinstance(content_hash, str) or HASH_RE.fullmatch(content_hash) is None:
            problems.append(
                f"{label}: content_hash 须为 sha256:<64hex>，实际 {content_hash!r}"
            )
        if entry.get("tier") != "T1":
            problems.append(f"{label}: tier 须为 T1，实际 {entry.get('tier')!r}")
        anchor = entry.get("ledger_anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            problems.append(
                f"{label}: ledger_anchor（台账 §1 条目号）不得缺失，实际 {anchor!r}"
            )
        if isinstance(content_hash, str) and content_hash in local_hashes:
            snapshot_backed += 1
            if fetched_at != SNAPSHOT_FETCH_DAY:
                problems.append(
                    f"{label}: content_hash 由本地快照实算复核，fetched_at 须＝"
                    f"快照抓取日 {SNAPSHOT_FETCH_DAY}，实际 {fetched_at!r}"
                )

    assert snapshot_backed >= 5, (
        f"REQ-2/15：至少 5 项 evidence 的 content_hash 须可从本地保存的 T1 "
        f"官方快照实算复核（本地候选件 {len(local_hashes)} 个），实际可复核 "
        f"{snapshot_backed} 项——出处摘要不可核验（无抓取日/内容指纹）"
    )
    assert not problems, (
        "REQ-2/9/13 缺口：evidence_digests 溯源不完备：\n  - "
        + "\n  - ".join(problems)
    )
