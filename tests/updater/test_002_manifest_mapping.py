"""P3-W1a REQ-12：生产 manifest stop-auto 映射与 Gate 2 授权边界（Red）。

REQ: REQ-12（005 §15.3/§15.5；006 §5.6/§7.2）——六条旧 salary/PA 路径本期
全部 stop-auto（仅观察）；八条不受影响路径保留原 manifest 配置；六旧路径
描述移入 `app.main.STOP_AUTO_OBSERVATIONS`（七字段，不是 manifest 新字段、
不是新写入白名单）；classifier 以固定停止路径集合拒绝注入的旧路径/新共享
路径；Gate 2 只授权 Red cases，不放行新 auto slot。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - `app.main.STOP_AUTO_OBSERVATIONS: dict[source_id, list[slot]]`，slot 恰
    C7.1 七字段；`_known_source_entries()` 返回的真实 manifest.slots 不含
    六旧路径，来源条目另有 `observer_slots`（与 STOP_AUTO_OBSERVATIONS 同
    内容），观察副本不得进入 classifier。
  - `app.updater.classifier.STOP_AUTO_PATHS: frozenset[str]`＝六旧路径；
    `build_candidate` 即使收到含旧 slot 的 manifest 也隔离（reason 非空，
    content None），旧路径/新共享路径永不自动写。
  - `app.updater.reference.AFFECTED_CASES: dict[path, case_id]`＝006 §7.1 八
    行；`B5_CASES` 逐 case 登记 subcase/证据（缺失 witness → coverage_gap）。
  - 生产 `REQUIRED_CASES` 保持既有静态回归 3 例，不因 Gate 2 扩面。

Red 失败原因（当前）：无 STOP_AUTO_OBSERVATIONS/STOP_AUTO_PATHS/
AFFECTED_CASES；生产 manifest 仍含六旧路径 auto slot。
"""

from __future__ import annotations

import copy

from _fakes import (
    AVD_FULLPATH,
    AVD_MANIFEST,
    CHANGE_CAP_3500,
    CHANGE_FROZEN_BANDS,
    EIGHT_UNAFFECTED_PATHS,
    SIX_STOP_AUTO_PATHS,
    initial_content,
    manifest_with_slot,
)

SLOT_SEVEN_FIELDS = frozenset(
    {
        "profile_ID",
        "fullpath",
        "type",
        "unit",
        "constraints",
        "anchor",
        "proof_role",
    }
)

EXPECTED_AFFECTED_CASES = {
    "stamps.property.related_conveyance.fixed_duty": "a_avd_related_fixed",
    "stamps.stock.other_transfer_fixed": "a_stock_other_fixed",
    "stamps.lease.rent_ceiling_unit": "a_lease_ceiling_boundary",
    "stamps.stock.effective_from": "a_stock_effective_boundary",
    "property.years.2025_26.standard_rate": "a_property_rate",
    "property.repair_allowance_ratio": "a_property_repair_ratio",
    "profits.two_tier_threshold": "a_profits_threshold_boundary",
    "profits.reductions.2025_26.cap": "a_profits_rebate_cap",
}


def _flatten(observations: dict) -> list[dict]:
    slots: list[dict] = []
    for described in observations.values():
        slots.extend(described or [])
    return slots


def test_002_six_old_paths_stop_auto_observer_only() -> None:
    """六旧路径移出真实 manifest.slots；STOP_AUTO_OBSERVATIONS 七字段可观察。"""
    from app.main import _known_source_entries
    import app.main as app_main

    sources = _known_source_entries()
    assert len(sources) == 8, "生产 allowlist 须 8 源（C7.5）"

    slot_paths: set[str] = set()
    for entry in sources:
        for slot in (entry.get("manifest") or {}).get("slots") or []:
            slot_paths.add(str(slot.get("fullpath")))

    leaked = sorted(slot_paths & set(SIX_STOP_AUTO_PATHS))
    assert not leaked, f"六旧路径不得进入真实 manifest.slots（stop-auto）：{leaked}"
    assert slot_paths == set(EIGHT_UNAFFECTED_PATHS), (
        "真实 manifest.slots 须恰覆盖 8 条不受影响路径（005:856）；"
        f"缺失 {sorted(set(EIGHT_UNAFFECTED_PATHS) - slot_paths)}，"
        f"多余 {sorted(slot_paths - set(EIGHT_UNAFFECTED_PATHS))}"
    )

    observations = getattr(app_main, "STOP_AUTO_OBSERVATIONS", None)
    assert isinstance(observations, dict), (
        "生产须新增固定 STOP_AUTO_OBSERVATIONS（source_id → 观察描述；"
        "不是 manifest 字段/新写入白名单）"
    )
    observed = _flatten(observations)
    assert observed, "STOP_AUTO_OBSERVATIONS 不得为空（六旧路径仅观察）"
    for index, slot in enumerate(observed):
        assert isinstance(slot, dict), f"观察描述[{index}] 须为映射"
        assert set(slot) == SLOT_SEVEN_FIELDS, (
            f"观察描述[{index}] 须恰七字段（C7.1），实际 {sorted(slot)}"
        )
    assert {str(slot["fullpath"]) for slot in observed} == set(SIX_STOP_AUTO_PATHS), (
        "STOP_AUTO_OBSERVATIONS 须恰覆盖六条旧 salary/PA 路径（005 §15.3）"
    )
    assert len(observed) >= len(SIX_STOP_AUTO_PATHS), (
        "每个旧来源实例均须保留观察描述（至少 6 个）"
    )

    source_ids = {str(entry.get("source_id")) for entry in sources}
    assert set(observations) <= source_ids, (
        "观察描述只能归属既有 8 生产来源（不得新增生产源）"
    )
    for entry in sources:
        source_id = str(entry.get("source_id"))
        assert (entry.get("observer_slots") or []) == (
            observations.get(source_id) or []
        ), (
            f"源 {source_id!r} 须在来源条目携带与 STOP_AUTO_OBSERVATIONS "
            "同内容的 observer_slots（观察副本不写入 manifest.slots）"
        )


def test_002_stop_auto_change_quarantines_known_period() -> None:
    """旧路径/共享路径即使被注入 slot 描述，classifier 仍固定拒绝。"""
    from app.updater.classifier import STOP_AUTO_PATHS, build_candidate

    assert STOP_AUTO_PATHS == frozenset(SIX_STOP_AUTO_PATHS), (
        "classifier 固定停止路径集合须恰为六旧路径（005:855）"
    )

    current = initial_content()
    for index, fullpath in enumerate(SIX_STOP_AUTO_PATHS):
        manifest = manifest_with_slot(
            fullpath,
            source_id=f"injected_{index}",
            profile_id="salaries_observer_v1",
        )
        change = {
            "fullpath": fullpath,
            "value": "999999",
            "source_id": f"injected_{index}",
            "anchor": manifest["slots"][0]["anchor"],
        }
        result = build_candidate(current, [change], [manifest])
        assert result["status"] == "quarantined", (
            f"注入的旧路径 {fullpath!r} 不得产候选（stop-auto 固定拒绝）"
        )
        assert result["content"] is None
        assert result.get("quarantine_reason"), "拒绝须携带非空原因"

    # 生产真实 manifest（六旧路径已移除）同样拒绝旧路径变更
    from app.main import _known_source_entries

    manifests = [entry["manifest"] for entry in _known_source_entries()]
    result = build_candidate(current, [CHANGE_CAP_3500], manifests)
    assert result["status"] == "quarantined", (
        "生产真实 manifest 下 salary cap 旧路径须隔离（stop-auto）"
    )
    assert result["content"] is None


def test_002_observer_extract_cannot_enter_classifier() -> None:
    """观察副本（STOP_AUTO_OBSERVATIONS）不得进入 classifier 自动写路径。"""
    import app.main as app_main
    from app.updater.classifier import build_candidate

    observations = getattr(app_main, "STOP_AUTO_OBSERVATIONS", None)
    assert isinstance(observations, dict) and observations, (
        "缺少生产 STOP_AUTO_OBSERVATIONS（观察描述）"
    )
    observer_slots = _flatten(observations)
    observer_manifest = {
        "manifest_schema_version": "1",
        "source_id": "observer_copy",
        "url": "https://example.invalid/observer-copy",
        "artifact_type": "html_table",
        "parser_id": "observer_copy_v1",
        "parser_code_hash": "sha256:" + "aa" * 32,
        "anchors": [
            {"path_regex": str(slot["anchor"]), "anchor_status": "confirmed"}
            for slot in observer_slots
        ],
        "fields": [str(slot["fullpath"]) for slot in observer_slots],
        "slots": copy.deepcopy(observer_slots),
        "evidence_only_fields": [],
        "legal_state_rules": [],
        "proof_authority": "观察副本（不得自动写）",
        "independent_counterpart": "—",
    }
    change = {
        "fullpath": "salaries.reductions.2025_26.cap",
        "value": "3500",
        "source_id": "observer_copy",
        "anchor": "heading:Salaries Tax Reduction#row:2025/26#col:Cap",
    }
    result = build_candidate(initial_content(), [change], [observer_manifest])
    assert result["status"] == "quarantined", (
        "观察副本传入 classifier 不得产候选（005:853/855）"
    )
    assert result["content"] is None
    assert result.get("quarantine_reason"), "拒绝须携带非空原因"


def test_002_eight_unchanged_paths_affected_cases_planned() -> None:
    """8 条不受影响路径逐条被 slot 覆盖，且各自登记计划 affected case。"""
    from app.main import _known_source_entries
    from app.updater.reference import AFFECTED_CASES

    manifests = [entry["manifest"] for entry in _known_source_entries()]
    slot_paths = {
        str(slot["fullpath"])
        for manifest in manifests
        for slot in manifest.get("slots") or []
    }
    assert slot_paths == set(EIGHT_UNAFFECTED_PATHS), (
        "8 条不受影响路径须逐条被真实 manifest slot 覆盖（005:856）"
    )
    assert set(AFFECTED_CASES) == set(EIGHT_UNAFFECTED_PATHS), (
        "AFFECTED_CASES 须恰覆盖 8 条不受影响路径"
    )
    assert dict(AFFECTED_CASES) == EXPECTED_AFFECTED_CASES, (
        "affected case ID 须与 006 §7.1 八行一致（固定 path→case 表）"
    )
    assert len(set(AFFECTED_CASES.values())) == 8, "affected case ID 须唯一"

    for fullpath in EIGHT_UNAFFECTED_PATHS:
        carriers = [
            manifest
            for manifest in manifests
            if any(
                slot.get("fullpath") == fullpath
                for slot in manifest.get("slots") or []
            )
        ]
        assert len(carriers) == 1, (
            f"{fullpath!r} 须恰由一个生产来源承载（保留原配置）"
        )


def test_002_semantic_digest_profile_frozen_dual_check() -> None:
    """固定 profile/digest 只覆盖冻结语义：候选不改 profile，语义面隔离。"""
    from app.rules.admission import verify_fixed_profile
    from app.updater.classifier import STOP_AUTO_PATHS, build_candidate

    current = initial_content()
    verify_fixed_profile(current)
    profiles_before = copy.deepcopy(current["frozen_semantics"]["profiles"])

    change = {
        "fullpath": AVD_FULLPATH,
        "value": "110",
        "source_id": "ird_avd",
        "anchor": "heading:AVD - Related Conveyance#row:Fixed duty#col:Amount",
    }
    result = build_candidate(current, [change], [AVD_MANIFEST])
    assert result["status"] == "candidate", (
        f"不受影响路径 DATA-only 候选须成立：{result!r}"
    )
    candidate = result["content"]
    assert candidate["frozen_semantics"]["profiles"] == profiles_before, (
        "候选不得改动固定 profile/semantic_digest（只覆盖冻结语义）"
    )
    verify_fixed_profile(candidate)

    frozen = build_candidate(current, [CHANGE_FROZEN_BANDS], [AVD_MANIFEST])
    assert frozen["status"] == "quarantined"
    assert frozen["content"] is None

    assert not (set(STOP_AUTO_PATHS) & set(EIGHT_UNAFFECTED_PATHS)), (
        "六 stop-auto 路径与八不受影响路径必须不相交"
    )


def test_002_gate2_authorizes_red_cases_not_auto_slots() -> None:
    """Gate 2 只授权 Red cases；不扩生产 required case/新 auto slot。"""
    from app.main import _known_source_entries
    from app.updater.reference import AFFECTED_CASES, B5_CASES, REQUIRED_CASES

    affected_ids = set(AFFECTED_CASES.values())
    assert affected_ids == set(EXPECTED_AFFECTED_CASES.values())
    assert len(REQUIRED_CASES) == 3, (
        "生产 required case 集合保持既有静态回归 3 例（不因 Gate 2 扩面）"
    )
    assert not (set(REQUIRED_CASES) & affected_ids), (
        "计划 affected case 不得进入生产 REQUIRED_CASES（测试侧 B5 only）"
    )
    assert set(B5_CASES) == affected_ids, (
        "B5_CASES 须逐条登记 8 个 affected case（缺失 witness → coverage_gap）"
    )

    manifests = [entry["manifest"] for entry in _known_source_entries()]
    slot_paths = {
        str(slot["fullpath"])
        for manifest in manifests
        for slot in manifest.get("slots") or []
    }
    assert slot_paths == set(EIGHT_UNAFFECTED_PATHS), (
        "Gate 2 不得为 affected case 新增 auto slot（只保留 8 条不受影响路径）"
    )
    assert not (slot_paths & set(SIX_STOP_AUTO_PATHS))
