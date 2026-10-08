"""REQ-15 official fixture 溯源：provenance.json 仅接受 T1 一手来源。

REQ: REQ-15（SDD §9：official fixture 本身必须 T1 溯源（官方 URL＋锚点＋
抓取日＋层级＋完整输入输出＋中间步骤））。
规格锚点:
  - SDD §9 REQ-15 行；SDD §8（tests/fixtures/official/(…＋provenance.json)）。
  - Annex C C8.2（fixture 结构化：已核事实＋官方来源锚点＋独立推导工作纸＋
    按阶段期望值）。
  - SDD §12 恒久注记：未晋升 T1/未经法例核验者一律不得进入 fixture 或规则
    数据。
期望值来源: 结构/schema 断言；样例 URL 取自 Annex C C7.5 已知 manifest
（ird_budget 等），仅作结构样例、不作数值期望。

【拟名】被测契约:
  - app.rules.provenance.validate_provenance(data: Mapping) -> list[str]：
    返回违例清单（空列表＝合规）。规则：
      * 顶层恰含 schema_version（非空 str）＋ fixtures（list）；
      * 每条 fixture 必须含 case_id／source_url／anchor／retrieved_at／
        content_hash／tier；
      * tier 必须为 "T1"（T2/T3/未知一律违例）；
      * source_url 必须 https 且主机属官方域
        （ird.gov.hk／gov.hk／elegislation.gov.hk 及 www 变体）；
      * retrieved_at 全匹配 YYYY-MM-DD；content_hash 全匹配
        sha256:[0-9a-f]{64}；anchor 非空；case_id 非空且唯一；
      * 违例信息须包含对应 case_id（可定位）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.rules.provenance import validate_provenance

PROVENANCE_FILE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "official" / "provenance.json"
)


def _good_fixture(case_id: str = "case-good-1") -> dict:
    return {
        "case_id": case_id,
        "source_url": "https://www.ird.gov.hk/eng/faq/pty.htm",
        "anchor": "Q7",
        "retrieved_at": "2026-10-07",
        "content_hash": "sha256:" + "ab" * 32,
        "tier": "T1",
    }


def _data(*fixtures: dict) -> dict:
    return {"schema_version": "1.0", "fixtures": list(fixtures)}


def test_fixture_provenance_t1_only() -> None:
    """溯源校验器：T1/官方 URL/锚点/日期/hash 全量合格才通过；任何降级违例。"""
    # —— 合法样例 → 无违例 ——
    assert validate_provenance(_data(_good_fixture())) == []

    # —— tier 降级（T2/T3/未知）→ 违例（REQ-15：fixture 仅 T1）——
    for bad_tier in ("T2", "T3", "t1", "", "human"):
        violations = validate_provenance(
            _data(_good_fixture("case-tier") | {"tier": bad_tier})
        )
        assert violations and any("case-tier" in v for v in violations), (
            f"tier={bad_tier!r} 必须被溯源校验拒绝"
        )

    # —— 非官方/非 https URL → 违例 ——
    for bad_url in (
        "http://www.ird.gov.hk/eng/faq/pty.htm",
        "https://example.com/facts",
        "https://blog.example.org/ird",
        "ftp://www.gov.hk/x",
    ):
        violations = validate_provenance(
            _data(_good_fixture("case-url") | {"source_url": bad_url})
        )
        assert violations and any("case-url" in v for v in violations), bad_url

    # —— 缺锚点/空锚点 → 违例 ——
    for bad_anchor in ("", "   "):
        violations = validate_provenance(
            _data(_good_fixture("case-anchor") | {"anchor": bad_anchor})
        )
        assert violations and any("case-anchor" in v for v in violations)

    missing_anchor = _good_fixture("case-anchor-miss")
    del missing_anchor["anchor"]
    violations = validate_provenance(_data(missing_anchor))
    assert violations and any("case-anchor-miss" in v for v in violations)

    # —— 抓取日非 YYYY-MM-DD → 违例（C2.11 全匹配风格）——
    for bad_date in ("2026/10/07", "2026-10-7", "20261007", "2026-10-07T00:00:00Z"):
        violations = validate_provenance(
            _data(_good_fixture("case-date") | {"retrieved_at": bad_date})
        )
        assert violations and any("case-date" in v for v in violations), bad_date

    # —— 内容 hash 非 sha256:64hex → 违例 ——
    for bad_hash in ("deadbeef", "sha256:xyz", "md5:" + "ab" * 16, "sha256:" + "ab" * 31):
        violations = validate_provenance(
            _data(_good_fixture("case-hash") | {"content_hash": bad_hash})
        )
        assert violations and any("case-hash" in v for v in violations), bad_hash

    # —— case_id 缺失/重复 → 违例 ——
    no_id = _good_fixture()
    del no_id["case_id"]
    violations = validate_provenance(_data(no_id, _good_fixture()))
    assert violations

    violations = validate_provenance(_data(_good_fixture("dup"), _good_fixture("dup")))
    assert violations and any("dup" in v for v in violations)

    # —— 顶层结构违例 ——
    assert validate_provenance({"fixtures": []}) != []  # 缺 schema_version
    assert validate_provenance({"schema_version": "1.0"}) != []  # 缺 fixtures

    # —— 仓库实际台账：当前（M1 无官方例）必须合规；后续条目一律 T1 ——
    assert PROVENANCE_FILE.exists(), (
        f"溯源台账缺失：{PROVENANCE_FILE}（SDD §8 tests/fixtures/official/）"
    )
    ledger = json.loads(PROVENANCE_FILE.read_text(encoding="utf-8"))
    violations = validate_provenance(ledger)
    assert violations == [], f"provenance.json 存在违例：{violations}"
    if ledger.get("fixtures"):
        # 已有条目时抽查形态（每条 tier=T1）
        for entry in ledger["fixtures"]:
            assert entry.get("tier") == "T1"
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry.get("content_hash", ""))
