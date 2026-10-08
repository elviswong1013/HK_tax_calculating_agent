"""REQ-17/18/C7.1 生产装配 manifest typed slots 与变更识别/隔离/发布（第二轮 REJECTED 缺口 Red）。

REQ: REQ-17（更新调度与来源状态——生产 allowlist 须为可核验配置）／
REQ-18（数值变更独立验证后发布；语义变化隔离；独立证据缺失/冲突隔离）。
验收发现锚点（终验第二轮 REJECTED）:
  - app/main.py._known_source_entries：8 源 manifest 的 anchors/slots/
    fields 全部空占位（「锚点待 DOM/版式验证证据落档后晋升」未做）——
    C7.1 typed slot 形状在生产配置中不存在，任何数值变化在生产管线中
    不可见（检查成功但 publish_outcome 恒 none/不可达）；
  - app/main.py.create_app：Scheduler 未绑定 harness/engine/
    required_cases（默认未绑定 fail closed）——生产门禁永远不可能
    publish，「对象存在」但发布能力不存在。
规格锚点:
  - Annex C C7.1/C7.2/C7.3：auto slot 恰七字段（profile_ID/fullpath/
    type/unit/constraints/anchor/proof_role）；anchors[] 每项
    {path_regex, anchor_status}；值承载锚须 confirmed（未证 locator
    不得进入规则数据）；slot.anchor 须指向 anchors[] 命名空间；fullpath
    指向规则束内真实 data 路径。
  - Annex C C8.4＋SDD §5 管线：数值变化 → 独立来源核验（C7.5a 非同源
    非 URL 重复）→ 门禁 → 单事务发布（指针切换）；语义变化（band 结构
    等冻结面）→ semantic_change 隔离＋变更请求；独立证据不一致/缺失 →
    隔离，指针不动。
期望值来源: 结构性契约（七字段/锚定/路径解析/绑定非空）＋管线结局
  （published/quarantined/指针切换与不动）；无金额期望（3500 为场景 marker）。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 生产 8 源 manifest 逐源补齐非空 anchors 与 typed slots（值＝现行束
    已入数数值，可被 tests/acceptance/_acc_helpers.render_source_document
    按 manifest 自描述渲染的快照命中）；scheduler 绑定非空 harness/
    engine/required_cases（生产发布能力真实存在）。
  - 生产配置可完成「识别→独立核验→门禁→发布」全链路：预算案调 cap
    （salaries.reductions.2025_26.cap）→ publish＋指针切换；band 结构
    改动（新增第六段）→ semantic_change 隔离；独立来源值不一致 →
    quarantined、指针不动。
"""

from __future__ import annotations

from _acc_helpers import (
    FakeClock,
    FakeOutbound,
    hk,
    inject_scheduler_seams,
    production_round_documents,
    resolve_bundle_path,
    scheduler_binding,
    scheduler_sources,
)

# C7.1/C7.2：auto slot 恰七字段（无 slot_id）
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

# 场景 marker：预算案调整 2025/26 薪俸税宽减 cap 3000 → 3500
CAP_PATH = "salaries.reductions.2025_26.cap"
CAP_NEW_VALUE = "3500"
# 语义变化：累进带新增第六段（band 数量/结构＝冻结语义，任何 auto slot
# 均不得指向不存在的 band 路径——C7.1 evidence_only_or_frozen_semantics）
BAND_STRUCTURE_PATH = "salaries.progressive_bands.5.rate_bp"


def _cap_carrier_ids(sources) -> list[str]:
    """生产配置中在该 fullpath 有 auto slot 的来源（C7.5a 独立对应面）。"""
    carriers: list[str] = []
    for entry in sources:
        manifest = entry.get("manifest") or {}
        if any(
            slot.get("fullpath") == CAP_PATH for slot in manifest.get("slots") or []
        ):
            carriers.append(str(entry.get("source_id")))
    return carriers


def _cap_overrides(sources, value: str, *, only_source_id: str | None = None) -> dict:
    by_source: dict = {}
    for source_id in _cap_carrier_ids(sources):
        if only_source_id is None or source_id == only_source_id:
            by_source[source_id] = {CAP_PATH: value}
    return by_source


def test_acceptance_production_manifests_typed_slots_and_gate_binding(make_acc) -> None:
    """生产装配（create_app 真实 Scheduler）：8 源 manifest 各含非空
    anchors＋typed slots（每 slot 恰七字段、fullpath 指向束内真实 data
    路径、unit/constraints 非空、anchor 指向已 confirmed 锚）；scheduler
    绑定 harness/engine/required_cases 非空。"""
    acc = make_acc("prodwiring")
    scheduler = acc.app.state.update_scheduler
    sources = scheduler_sources(scheduler)
    assert len(sources) == 8, (
        f"生产 allowlist 须 8 源（C7.5），实际 {len(sources)}"
    )
    bundle = acc.store.get_bundle(acc.store.current()["bundle_id"])
    bundle_data = bundle.get("data") or {}
    problems: list[str] = []

    for entry in sources:
        source_id = str(entry.get("source_id") or "")
        manifest = entry.get("manifest") or {}
        label = f"源 {source_id!r}"

        anchors = manifest.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            problems.append(f"{label}: anchors 不得为空（C7.2/C7.3）")
            anchors = []
        anchor_confirmed: dict[str, str] = {}
        for index, anchor in enumerate(anchors):
            if not isinstance(anchor, dict) or set(anchor) != {
                "path_regex",
                "anchor_status",
            }:
                problems.append(
                    f"{label}: anchors[{index}] 键集合须恰为 "
                    "{path_regex, anchor_status}（C7.2）"
                )
                continue
            path_regex = anchor.get("path_regex")
            if not isinstance(path_regex, str) or not path_regex:
                problems.append(f"{label}: anchors[{index}].path_regex 须非空")
                continue
            anchor_confirmed[path_regex] = str(anchor.get("anchor_status") or "")

        slots = manifest.get("slots")
        if not isinstance(slots, list) or not slots:
            problems.append(
                f"{label}: slots 不得为空（C7.1 typed slot——生产配置须可承载"
                "数值变化，非空占位）"
            )
            slots = []
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                problems.append(f"{label}: slots[{index}] 须为映射")
                continue
            keys = set(slot)
            if keys != SLOT_SEVEN_FIELDS:
                problems.append(
                    f"{label}: slots[{index}] 须恰七字段 {sorted(SLOT_SEVEN_FIELDS)}"
                    f"（C7.1/C7.2），实际 {sorted(keys)}"
                )
                continue
            fullpath = slot.get("fullpath")
            if not isinstance(fullpath, str) or not fullpath:
                problems.append(f"{label}: slots[{index}].fullpath 须非空")
                continue
            resolved = resolve_bundle_path(bundle_data, fullpath)
            if resolved is None:
                problems.append(
                    f"{label}: slots[{index}].fullpath {fullpath!r} 须指向束内"
                    "真实 data 路径（C7.1）"
                )
            elif not isinstance(resolved, str) or not resolved:
                problems.append(
                    f"{label}: slots[{index}].fullpath {fullpath!r} 解析目标须为"
                    "canonical 字符串数值（C2 全链路规范字符串）"
                )
            if not isinstance(slot.get("unit"), str) or not slot.get("unit"):
                problems.append(f"{label}: slots[{index}].unit 须非空")
            constraints = slot.get("constraints")
            if not isinstance(constraints, dict) or not constraints:
                problems.append(f"{label}: slots[{index}].constraints 须非空")
            slot_anchor = slot.get("anchor")
            if not isinstance(slot_anchor, str) or not slot_anchor:
                problems.append(f"{label}: slots[{index}].anchor 须非空")
            elif slot_anchor not in anchor_confirmed:
                problems.append(
                    f"{label}: slots[{index}].anchor 须指向 anchors[] 内的 "
                    f"path_regex：{slot_anchor!r}"
                )
            elif anchor_confirmed.get(slot_anchor) != "confirmed":
                problems.append(
                    f"{label}: slots[{index}] 的值承载锚须 confirmed（未证 "
                    f"locator 不得进入规则数据，C7.3）：{slot_anchor!r}"
                )

    # —— 生产门禁绑定：harness/engine/required_cases 非空（发布能力存在）——
    harness = scheduler_binding(scheduler, "harness")
    if harness is None:
        problems.append("生产 Scheduler 未绑定独立参考 harness（C8.1）")
    engine = scheduler_binding(scheduler, "engine")
    if engine is None:
        problems.append("生产 Scheduler 未绑定候选引擎 seam（C8.4）")
    required_cases = scheduler_binding(scheduler, "required_cases")
    if not required_cases:
        problems.append(
            "生产 Scheduler 未绑定非空 required_cases（C8.4）——门禁空报告"
            "恒隔离，生产发布不可达"
        )

    assert not problems, (
        "REQ-17/18/C7.1 缺口：生产装配 manifest/绑定不完整：\n  - "
        + "\n  - ".join(problems)
    )


def test_acceptance_production_change_detection_quarantine_publish(make_acc) -> None:
    """生产配置＋假出站三场景（快照假件按 manifest 自描述渲染）：
    ① 预算案调 cap（数值变化）→ 独立验证过 → publish＋指针切换；
    ② band 结构改动（语义变化）→ semantic_change 隔离、指针不动；
    ③ 独立来源值不一致 → quarantined、指针不动。"""
    # ================================================================
    # 场景 ②：语义变化（band 结构）→ classifier 以生产 manifests 判
    # semantic_change（值抽取只产 auto slot 路径，语义/冻结面边界在
    # 候选构建阶段——C7.1）
    # ================================================================
    acc_semantic = make_acc("prodsemantic")
    scheduler_semantic = acc_semantic.app.state.update_scheduler
    sources = scheduler_sources(scheduler_semantic)
    current_bundle = acc_semantic.store.get_bundle(
        acc_semantic.store.current()["bundle_id"]
    )
    pointer_before_semantic = acc_semantic.store.current()["bundle_id"]

    from app.updater.classifier import build_candidate  # 延迟导入（同目录约定）

    manifests = [entry.get("manifest") or {} for entry in sources]
    band_change = {
        "fullpath": BAND_STRUCTURE_PATH,
        "value": "1800",
        "source_id": sources[0].get("source_id"),
        "anchor": "",
    }
    built = build_candidate(current_bundle, [band_change], manifests)
    assert built.get("status") == "quarantined", (
        "REQ-18：band 结构改动是冻结语义（C7.1），生产配置须判 semantic_change"
        f" 隔离，实际 {built.get('status')!r}"
    )
    assert built.get("quarantine_reason") == "semantic_change", (
        f"隔离原因须 semantic_change，实际 {built.get('quarantine_reason')!r}"
    )
    assert acc_semantic.store.current()["bundle_id"] == pointer_before_semantic, (
        "语义变化隔离不得移动 current pointer"
    )

    # ================================================================
    # 场景 ①：数值变化（预算案 cap 3000 → 3500）→ 独立验证 → publish
    # ================================================================
    acc_publish = make_acc("prodpublish")
    scheduler_publish = acc_publish.app.state.update_scheduler
    sources_publish = scheduler_sources(scheduler_publish)
    bundle_data = acc_publish.store.get_bundle(
        acc_publish.store.current()["bundle_id"]
    )["data"]
    pointer_before = acc_publish.store.current()["bundle_id"]

    inject_scheduler_seams(  # 仅替换 clock/outbound；生产 manifests＋门禁绑定不变
        scheduler_publish,
        clock=FakeClock(hk(2026, 10, 9)),
        outbound=FakeOutbound(
            production_round_documents(
                sources_publish, bundle_data, _cap_overrides(sources_publish, CAP_NEW_VALUE)
            )
        ),
    )
    job = scheduler_publish.startup()
    if job is None or job.get("status") != "succeeded":
        outcomes = {
            entry.get("source_id"): entry.get("http_outcome")
            for entry in scheduler_publish.status()["sources"]
        }
        raise AssertionError(
            f"场景①前置失败：生产检查轮未完成（job={job!r}，逐源 outcome="
            f"{outcomes!r}）——生产 manifest 须可被锚定抽取（C7.3）"
        )
    status_publish = scheduler_publish.status()
    assert status_publish.get("publish_outcome") == "published", (
        "REQ-18：数值变化（预算案调 cap）经独立来源核验后须 publish（生产"
        "配置具备发布能力，非仅对象存在）；实际 publish_outcome="
        f"{status_publish.get('publish_outcome')!r} quarantine="
        f"{status_publish.get('quarantine_reason')!r}"
    )
    published_bundle_id = status_publish.get("published_bundle_id")
    assert published_bundle_id, "published 须记录 published_bundle_id"
    pointer_after = acc_publish.store.current()["bundle_id"]
    assert pointer_after == published_bundle_id and pointer_after != pointer_before, (
        "发布＝单事务原子指针切换（SDD §5）：指针须切到新束，实际 "
        f"{pointer_before!r} → {pointer_after!r}"
    )
    published_bundle = acc_publish.store.get_bundle(published_bundle_id)
    assert (
        resolve_bundle_path(published_bundle["data"], CAP_PATH) == CAP_NEW_VALUE
    ), (
        "发布后的现行束须携带变更值 "
        f"{CAP_PATH}={CAP_NEW_VALUE!r}，实际 "
        f"{resolve_bundle_path(published_bundle['data'], CAP_PATH)!r}"
    )

    # ================================================================
    # 场景 ③：独立来源值不一致（仅首个承载源改值）→ quarantined、指针不动
    # ================================================================
    acc_quarantine = make_acc("prodquarantine")
    scheduler_quarantine = acc_quarantine.app.state.update_scheduler
    sources_quarantine = scheduler_sources(scheduler_quarantine)
    bundle_data_q = acc_quarantine.store.get_bundle(
        acc_quarantine.store.current()["bundle_id"]
    )["data"]
    pointer_before_q = acc_quarantine.store.current()["bundle_id"]

    carriers = _cap_carrier_ids(sources_quarantine)
    assert carriers, (
        "前置自证失败：生产配置须有来源在 " + CAP_PATH + " 上承载 auto slot"
    )
    inject_scheduler_seams(
        scheduler_quarantine,
        clock=FakeClock(hk(2026, 10, 9)),
        outbound=FakeOutbound(
            production_round_documents(
                sources_quarantine,
                bundle_data_q,
                _cap_overrides(
                    sources_quarantine, CAP_NEW_VALUE, only_source_id=carriers[0]
                ),
            )
        ),
    )
    job_q = scheduler_quarantine.startup()
    if job_q is None or job_q.get("status") != "succeeded":
        outcomes_q = {
            entry.get("source_id"): entry.get("http_outcome")
            for entry in scheduler_quarantine.status()["sources"]
        }
        raise AssertionError(
            f"场景③前置失败：生产检查轮未完成（job={job_q!r}，逐源 outcome="
            f"{outcomes_q!r}）"
        )
    status_q = scheduler_quarantine.status()
    assert status_q.get("publish_outcome") == "quarantined", (
        "REQ-18：独立证据不能核验候选值（不一致/缺失 → source_conflict/"
        "evidence_missing/conflict 隔离）时须 quarantined，实际 "
        f"publish_outcome={status_q.get('publish_outcome')!r} "
        f"quarantine_reason={status_q.get('quarantine_reason')!r}"
    )
    assert acc_quarantine.store.current()["bundle_id"] == pointer_before_q, (
        "隔离场景不得移动 current pointer（发布只发生在门禁外全验证通过后）"
    )
