"""REQ-18 独立证据校验（M5 Red；SDD §5 管线阶段 5＋Annex C C7.5a/C8.1）。

REQ: 18（§9：独立证据校验——候选参数对照独立来源事实核验；不得以同一坏候选
在两个引擎互证；T2 须先晋升 T1）。
规格锚点:
  - SDD §5 管线阶段 5：候选参数对照独立来源事实（≠产生变更的同一来源/候选）；
    缺失 → evidence_missing 隔离。
  - Annex C C7.5a：独立证明＝IRD 法例＋官方已刊指南，非两 URL 重复。
  - Annex C C8.1：独立参考参数只来自已批准独立 source/proof，与候选参数对象
    物理分离。
期望值来源: 结构性契约（独立性判定：来源/URL/层级），无金额期望
  （3500/4000 为测试宇宙 marker）。

【拟名】被测契约（app/updater/validator.validate_change，Green 阶段须按测试
实现，不得要求测试改写）:
  - validate_change(change, independent_facts) -> {
      "status": "validated" | "evidence_missing" | "conflict",
      "reason": str | None,
    }
    change: {"fullpath", "value", "source_id", "anchor"}；
    independent_facts 元素 {"fact_id", "fullpath", "value", "source_id", "url",
    "anchor", "tier"}。
    判定规则：
    - 事实须与变更同 fullpath、数值一致，且来源独立（source_id 与 URL 均不同于
      产生变更的来源；H2 起按主机判定——同站不同页面不算独立，任一侧缺 URL
      fail closed），tier="T1"（T2 未晋升不算）→ 否则 evidence_missing；
    - 独立 T1 事实存在但数值不一致 → conflict（来源冲突隔离）。

Red 说明: app/updater/ 尚不存在 —— 每个测试失败原因＝
「ModuleNotFoundError: app.updater.validator（独立证据校验缺失）」。
"""

from __future__ import annotations

from _fakes import BUDGET_URL, CHANGE_CAP_3500, FACT_CAP_3500


def _validate_change():
    from app.updater.validator import validate_change

    return validate_change


def test_independent_source_fact_validation() -> None:
    """候选参数对照独立来源事实：同源互证/缺证/T2/冲突一律不算通过。"""
    validate_change = _validate_change()

    # —— (1) 独立来源（不同 source_id/URL、T1）且数值一致 → validated
    result = validate_change(CHANGE_CAP_3500, [FACT_CAP_3500])
    assert result["status"] == "validated", (
        f"独立 T1 事实数值一致必须通过：{result!r}"
    )

    # —— (2) 无任何独立事实 → evidence_missing（缺证隔离）
    missing = validate_change(CHANGE_CAP_3500, [])
    assert missing["status"] == "evidence_missing"

    # —— (3) 「独立」事实与变更同源（同 source_id＋同 URL）→ 不算独立
    same_source = dict(FACT_CAP_3500, source_id="ird_budget", url=BUDGET_URL)
    circular = validate_change(CHANGE_CAP_3500, [same_source])
    assert circular["status"] == "evidence_missing", (
        "不得以产生变更的同一来源自我印证（禁同坏候选互证）"
    )

    # —— (4) T2 未晋升 T1 → 不构成独立核验事实
    t2_fact = dict(FACT_CAP_3500, tier="T2")
    unpromoted = validate_change(CHANGE_CAP_3500, [t2_fact])
    assert unpromoted["status"] == "evidence_missing", (
        "T2 事实须先晋升 T1 才可作为独立证据（§5）"
    )

    # —— (5) 独立 T1 事实数值冲突 → conflict（来源冲突隔离）
    conflicting = dict(FACT_CAP_3500, value="4000")
    conflict = validate_change(CHANGE_CAP_3500, [conflicting])
    assert conflict["status"] == "conflict"

    # —— (6) H2：同站不同页面（source_id 不同、主机相同）→ 不算独立
    same_host = dict(
        FACT_CAP_3500,
        source_id="ird_avd",
        url="https://www.ird.gov.hk/eng/faq/avd.htm",
    )
    same_host_result = validate_change(CHANGE_CAP_3500, [same_host])
    assert same_host_result["status"] == "evidence_missing", (
        "同一站点（ird.gov.hk）两个页面不得互证（H2 站点级污染）"
    )

    # —— (7) H2：变更侧缺 URL → 无法证明主机独立，fail closed
    legacy_change = {
        key: value for key, value in CHANGE_CAP_3500.items() if key != "url"
    }
    legacy = validate_change(legacy_change, [FACT_CAP_3500])
    assert legacy["status"] == "evidence_missing", (
        "变更来源缺 URL 时不得仅凭 source_id 判定独立"
    )
