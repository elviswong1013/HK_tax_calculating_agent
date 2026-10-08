"""REQ-13 记录/下载/打印/重放；迟到响应与历史记录边界（M6 Red）。

REQ: REQ-13（record 全字段；JSON 下载＋打印；同快照可重现；回放兼容＝引擎
版本完全相等；回放≠当前重评；已完成记录不因输入/规则变更失效）＋
REQ-11/14 交叉（Annex A §12.2/§12.6/§12.6.1；Annex C C3.1/C3.2.4/C4.6）。
规格锚点:
  - Annex A §12.2：输入/规则变更仅废当前流程与未消费确认；完成记录保留原
    binding 可查/下载/重放；迟到响应仅在同 epoch/revision/binding 下应用。
  - Annex A §12.6/§12.6.1：内存记录/附件下载/原包重放三入口语义区分；
    download=attachment＋no-store；print=inline＋no-store；
    replay 仅引擎版本完全相等；「历史估算，非当前重新评估」。
  - Annex A §6.6：打印保留输入摘要、步骤、版本、出处、警告；不打印交互控件。
  - Annex C C3.1/C3.7：replay 引擎版本不完全相等 → 409 E_BUNDLE_INCOMPATIBLE
    （不降级、不迁移）；迟到的失配响应不作为当前结果。
期望值来源: 结构/状态断言；无金额期望（金额仅作字符串出现性核对）。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - POST /api/v1/records/replay {record_id} → 200：按确认事实＋原 binding 重算；
    响应含原 binding（非当前指针）与权威 amounts；附中文标示
    「历史估算，非当前重新评估」；不改写原记录。
  - execute 消费未消费确认前校验输入修订与规则绑定（C3.2.2 绑定元组完整生效）：
    input_revision 或 bundle 任一变化 → 409 E_CONFIRMATION_STALE，且响应
    不含 amounts/record_id（迟到/失配结果不作当前结果）。
  - record 核心字段（REQ-13 全字段）：tax_type/period/input/binding/steps/
    evidence_refs/unsupported/disclaimer 等（见 test_record_fields_complete）。
  - 引擎版本经 app.config.ENGINE_VERSION 与 app.api.routes.ENGINE_VERSION
    读取；测试以 monkeypatch 同步改两者模拟版本变化。
"""

from __future__ import annotations

import json

from _helpers import (
    AMOUNT_KEYS,
    REPLAY_MARKER,
    binding_four_keys,
    complete_calc,
    publish_alt_bundle,
    run_confirm,
    run_execute,
    run_prepare,
    salaries_input,
)

_EXEC_TARGET = "/api/v1/calc/salaries-tax"


def _error_code(resp) -> str:
    payload = resp.json()
    err = payload.get("error")
    assert isinstance(err, dict), f"错误响应须为统一形状：{payload!r}"
    return err["code"]


def _get_record(api, record_id: str):
    return api.get(f"/api/v1/records/{record_id}")


# --------------------------------------------------------------- 迟到/失配响应


def test_ui_stale_response_dropped_history_record_preserved(ui) -> None:
    """输入修订变化后，未消费确认执行 → 409 stale 且不产生当前结果；
    已完成记录仍可查/可下载（Annex A §12.2/C3.2.4 input_revision 行）。"""
    api = ui.api

    done = complete_calc(api)  # 已完成历史记录
    record_id = done["record_id"]

    # 用户开始新一轮流程但尚未执行（未消费确认能力）
    prepared = run_prepare(api, salaries_input())
    stale_confirmation = run_confirm(api, prepared)

    # —— 用户编辑输入 → input_revision 变化（新一轮 prepare）——
    edited = run_prepare(api, salaries_input(employment_income="300000"))
    assert edited["input_revision"] != prepared["input_revision"], (
        "编辑输入必须产生新 input_revision（§12.2 输入字段变更）"
    )

    # —— 迟到的执行：未消费确认已失效 → 409；不作当前结果显示 ——
    r = run_execute(api, stale_confirmation)
    assert r.status_code == 409, (
        f"输入修订变化后未消费确认必须 409 stale（C3.2.4），实际 {r.status_code}: {r.text[:300]!r}"
    )
    assert _error_code(r) == "E_CONFIRMATION_STALE"
    payload = r.json()
    assert "amounts" not in payload, "失配响应不得携带金额（不作当前结果）"
    assert "record_id" not in payload, "失配响应不得产生/指向当前记录"

    # —— 已完成记录保留：可查＋可下载 ——
    view = _get_record(api, record_id)
    assert view.status_code == 200, view.text
    dl = api.get(f"/api/v1/records/{record_id}/download")
    assert dl.status_code == 200, dl.text


# ------------------------------------------------- 输入/规则变化 vs 历史记录


def test_ui_records_survive_editor_and_rules_changes(ui) -> None:
    """rules 变化：未消费确认失效（409）；完成记录固定原 binding 可查/下载；
    按原 snapshot 重放（引擎版本相等）成功且绑定原 bundle（§12.2/C3.2.4）。"""
    api = ui.api

    done = complete_calc(api)
    record_id, binding0 = done["record_id"], done["binding"]

    prepared = run_prepare(api, salaries_input())
    pending_confirmation = run_confirm(api, prepared)

    # —— rules 变化：发布新 bundle，指针切换 ——
    publish_alt_bundle(ui.store)
    after = run_prepare(api, salaries_input())
    binding1 = after["expected_binding"]
    assert binding1["bundle_hash"] != binding0["bundle_hash"], (
        "发布新 bundle 后 prepare 必须绑定新规则（rules 变化已生效）"
    )

    # —— 未消费确认因 rules 变化失效 → 409 ——
    r = run_execute(api, pending_confirmation)
    assert r.status_code == 409, (
        f"rules 变化后未消费确认必须 409 stale（C3.2.4 rules 行），"
        f"实际 {r.status_code}: {r.text[:300]!r}"
    )
    assert _error_code(r) == "E_CONFIRMATION_STALE"

    # —— 完成记录不因 rules 变化失效：可查＋原 binding 固定 ——
    view = _get_record(api, record_id)
    assert view.status_code == 200, view.text
    record_core = view.json()["record"]
    assert record_core["binding"] == binding0, (
        "已完成记录必须保留原 bundle/engine binding（REQ-13 不因规则变更失效）"
    )

    # —— 可下载（attachment 语义见 headers 测试；此处验证可下载性）——
    dl = api.get(f"/api/v1/records/{record_id}/download")
    assert dl.status_code == 200, dl.text

    # —— 原包重放：引擎版本相等 → 允许；绑定原 bundle 而非当前指针 ——
    replay = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay.status_code == 200, (
        f"引擎版本相等时必须允许按原 snapshot 重放（§12.6），"
        f"实际 {replay.status_code}: {replay.text[:300]!r}"
    )
    replay_payload = replay.json()
    assert replay_payload["binding"] == binding0, (
        "重放必须按记录原 binding（原 bundle snapshot），不得改用当前规则"
    )
    assert REPLAY_MARKER in json.dumps(replay_payload, ensure_ascii=False), (
        f"重放结果必须标示「{REPLAY_MARKER}」（§12.2）"
    )


# ------------------------------------------------------- 三入口语义区分


def test_ui_records_memory_vs_export_replay_distinct(ui) -> None:
    """内存查阅（no-store）/ 附件下载（attachment＋全量）/ 原包重放（重算＋
    历史标示）三入口语义区分；工作台界面提供对应标签（§12.6）。"""
    api = ui.api

    done = complete_calc(api)
    record_id = done["record_id"]

    # —— 入口一：内存查阅（界面查看；不作为附件下载）——
    view = _get_record(api, record_id)
    assert view.status_code == 200, view.text
    assert "no-store" in view.headers.get("cache-control", ""), "查阅响应必须 no-store"
    assert not view.headers.get("content-disposition", "").startswith(
        "attachment"
    ), "内存查阅不是下载入口（不得带 attachment）"

    # —— 入口二：附件下载＝全量规范确认事实（非摘要）——
    dl = api.get(f"/api/v1/records/{record_id}/download")
    assert dl.status_code == 200, dl.text
    assert dl.headers.get("content-disposition", "").startswith("attachment"), (
        "下载必须 Content-Disposition: attachment（§12.6.1）"
    )
    assert "no-store" in dl.headers.get("cache-control", "")
    dl_text = json.dumps(dl.json(), ensure_ascii=False)
    view_core = view.json()["record"]
    for key in view_core:
        assert f'"{key}"' in dl_text, (
            f"附件下载必须为全量记录（含 {key!r}），而非摘要（§12.6）"
        )
    assert "240000" in dl_text, "下载必须含输入摘要事实（全量确认事实）"
    assert '"binding"' in dl_text, "下载必须含原 bundle/engine binding"

    # —— 入口三：原包重放（重算入口，与当前重算/查阅分开）——
    replay = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay.status_code == 200, (
        f"重放入口必须可用（引擎版本相等），实际 {replay.status_code}: {replay.text[:300]!r}"
    )
    assert REPLAY_MARKER in json.dumps(replay.json(), ensure_ascii=False), (
        "重放入口必须与当前评估入口区分并标示历史语义（§12.6）"
    )

    # —— 工作台界面：记录区提供查/下载/打印/重放相关标签 ——
    page = api.get("/")
    assert page.status_code == 200, page.text
    html = page.text
    for label in ("计算记录", "下载", "打印"):
        assert label in html, f"工作台必须提供「{label}」记录操作入口（§2/§6）"
    assert REPLAY_MARKER in html or "重放" in html, (
        "工作台必须区分「按原包重放」入口并标示其历史语义（§12.6）"
    )


# ------------------------------------------------------- 打印/下载响应头


def test_ui_print_html_inline_no_store_json_download_attachment(ui) -> None:
    """打印报告：HTML＋inline＋no-store＋保留输入摘要/步骤/版本/出处/警告、
    无交互控件；JSON 下载：attachment＋no-store（§12.6.1/§6.6）。"""
    api = ui.api

    done = complete_calc(api, salaries_input(provisional_paid="0"))
    record_id = done["record_id"]

    # —— JSON 下载响应头 ——
    dl = api.get(f"/api/v1/records/{record_id}/download")
    assert dl.status_code == 200, dl.text
    assert dl.headers.get("content-disposition", "").startswith("attachment"), (
        f"下载须 attachment：{dl.headers.get('content-disposition')!r}"
    )
    assert "no-store" in dl.headers.get("cache-control", ""), "下载须 no-store（§12.6.1）"
    assert "json" in dl.headers.get("content-type", "")

    # —— 打印报告响应头与内容 ——
    pr = api.post("/api/v1/report/print", json={"record_id": record_id})
    assert pr.status_code == 200, pr.text
    assert "text/html" in pr.headers.get("content-type", "")
    assert pr.headers.get("content-disposition", "").startswith("inline"), (
        f"打印须 inline：{pr.headers.get('content-disposition')!r}"
    )
    assert "no-store" in pr.headers.get("cache-control", ""), "打印须 no-store（§12.6.1）"

    html = pr.text
    # §6.6：打印版本保留输入摘要、步骤、版本、出处、警告
    assert "输入摘要" in html, "打印必须保留输入摘要（§6.6）"
    assert "步骤" in html, "打印必须保留计算步骤区（§6.6）"
    assert "版本" in html, "打印必须保留规则/引擎版本（§6.6）"
    assert "来源" in html, "打印必须保留出处区（§6.6）"
    assert "警告" in html or "警告与未覆盖" in html, "打印必须保留警告区（§6.6）"
    assert "2025/26" in html or "2025_26" in html, "打印必须显示期间"
    # 不打印交互控件（§6.6）
    lowered = html.lower()
    assert "<button" not in lowered and "<form" not in lowered, (
        "打印版本不得包含交互控件（§6.6）"
    )


# ------------------------------------------------------- 记录全字段


def test_record_fields_complete(ui) -> None:
    """完成记录含 REQ-13 全字段：税项/期间/输入摘要/binding/步骤（税率、扣除、
    暂缴、宽减）/官方出处/未覆盖/免责；金额六键 canonical 字符串或 null。"""
    api = ui.api

    done = complete_calc(api, salaries_input(provisional_paid="0"))
    assert done["response"]["status"] == "complete", (
        "薪俸税可计算事实经确认后必须产生 complete 记录（M6：execute 接入引擎），"
        f"实际 {done['response'].get('status')!r} / {done['response'].get('blocked_reason')!r}"
    )
    record_id = done["record_id"]

    view = _get_record(api, record_id)
    assert view.status_code == 200, view.text
    record = view.json()["record"]

    # —— 税项/期间/输入摘要/binding ——
    assert record["tax_type"] == "salaries_tax"
    record_text = json.dumps(record, ensure_ascii=False)
    assert "2025_26" in record_text or "2025/26" in record_text, "记录必须含期间"
    assert record["input"]["employment_income"] == "240000", "记录必须含输入摘要（原事实）"
    assert binding_four_keys(record["binding"]), "记录必须含 bundle＋engine 四元组 binding"

    # —— 步骤与税率/扣除/暂缴/宽减要素 ——
    steps = record.get("steps")
    assert isinstance(steps, list) and steps, (
        "complete 记录必须含非空计算步骤（REQ-13 步骤；§6.4 每个计税基数、"
        "适用税率、扣除/免税额、取整及中间结果）"
    )
    steps_text = json.dumps(steps, ensure_ascii=False)
    assert ("rate" in steps_text.lower()) or ("税率" in steps_text), (
        "计算步骤必须含适用税率（REQ-13 税率）"
    )
    assert any(
        marker in steps_text for marker in ("deduction", "扣除", "免税", "allowance")
    ), "计算步骤必须含扣除/免税额要素（REQ-13 扣除）"
    assert ("provisional" in record_text.lower()) or ("暂缴" in record_text), (
        "记录必须含暂缴税假设/组成（REQ-13 暂缴假设）"
    )
    assert ("reduction" in record_text.lower()) or ("宽减" in record_text), (
        "记录必须含宽减要素（REQ-13 宽减）"
    )

    # —— 官方出处/未覆盖/免责 ——
    assert isinstance(record.get("evidence_refs"), list), (
        "记录必须含官方出处字段 evidence_refs（REQ-13）"
    )
    assert isinstance(record.get("unsupported"), list), (
        "记录必须含未覆盖事项字段 unsupported（REQ-13）"
    )
    disclaimer = record.get("disclaimer")
    assert isinstance(disclaimer, str) and disclaimer.strip(), (
        "记录必须含免责声明字段 disclaimer（REQ-13 免责；默认非空中文）"
    )

    # —— 金额六键（canonical 字符串或 null；不填零、不缺省）——
    amounts = record.get("amounts")
    assert isinstance(amounts, dict) and set(amounts.keys()) == set(AMOUNT_KEYS), (
        f"记录金额必须恰含 C3.4 六键：{amounts!r}"
    )
    for key, value in amounts.items():
        assert value is None or isinstance(value, str), (
            f"金额 {key} 必须为 canonical 字符串或 null（禁 JSON number）：{value!r}"
        )


# ------------------------------------------------------- 重放契约


def test_replay_requires_pinned_bundle_and_engine_version_exact_equality(
    ui, monkeypatch
) -> None:
    """重放钉住原 bundle（指针已变仍用原包）；不信任保存税额（重算）；
    引擎版本不完全相等 → 409 E_BUNDLE_INCOMPATIBLE（不降级、不迁移）。"""
    api = ui.api

    done = complete_calc(api)
    record_id, binding0 = done["record_id"], done["binding"]

    # —— 规则指针变化后仍可按原包重放（引擎版本相等）——
    publish_alt_bundle(ui.store)
    replay = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay.status_code == 200, (
        f"引擎版本相等时重放必须钉住原 bundle 成功（C3.1 replay 行），"
        f"实际 {replay.status_code}: {replay.text[:300]!r}"
    )
    assert replay.json()["binding"] == binding0, (
        "重放绑定必须是记录原 binding（原 snapshot），而非当前指针"
    )

    # —— 不信任导出税额：篡改保存金额后重放必须重算出新值 ——
    sid = ui.session_id()
    stored = ui.registry.get_record(sid, record_id)
    assert stored is not None
    stored["core"]["amounts"]["final_after_reduction"] = "999999"
    replay2 = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay2.status_code == 200, replay2.text
    final = replay2.json()["amounts"]["final_after_reduction"]
    assert final != "999999", (
        "重放必须按确认事实＋原 binding 重新计算，不得直接回显记录保存的税额（C3.1）"
    )

    # —— 引擎版本变化（不完全相等）→ 409 E_BUNDLE_INCOMPATIBLE ——
    monkeypatch.setattr("app.config.ENGINE_VERSION", "9.9.9")
    monkeypatch.setattr("app.api.routes.ENGINE_VERSION", "9.9.9")
    replay3 = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay3.status_code == 409, (
        f"引擎版本不完全相等必须拒绝重算（409，不降级、不迁移），"
        f"实际 {replay3.status_code}: {replay3.text[:300]!r}"
    )
    assert _error_code(replay3) == "E_BUNDLE_INCOMPATIBLE"


def test_replay_not_current_reassessment(ui) -> None:
    """重放≠当前重评：重放响应带历史标示与原 binding；不改写原记录；
    原记录保持原 binding 与原金额。"""
    api = ui.api

    done = complete_calc(api)
    record_id, binding0 = done["record_id"], done["binding"]
    original = _get_record(api, record_id).json()["record"]
    original_amounts = original["amounts"]

    publish_alt_bundle(ui.store)  # 当前规则已变：重放仍按原包

    replay = api.post("/api/v1/records/replay", json={"record_id": record_id})
    assert replay.status_code == 200, (
        f"引擎版本相等时重放必须可用，实际 {replay.status_code}: {replay.text[:300]!r}"
    )
    payload = replay.json()
    payload_text = json.dumps(payload, ensure_ascii=False)

    # —— 历史标示（§12.2：用正常中文标示，不冒充当前重新评估）——
    assert REPLAY_MARKER in payload_text, (
        f"重放响应必须包含「{REPLAY_MARKER}」（REQ-13 回放≠当前重评）"
    )
    # —— 原 binding（非当前指针）——
    assert payload["binding"] == binding0
    current = ui.store.current()
    assert binding0["bundle_hash"] != current["bundle_hash"], (
        "前置校验：当前规则指针必须已不同于原 binding"
    )

    # —— 重放不改写原记录 ——
    after = _get_record(api, record_id).json()["record"]
    assert after["binding"] == binding0, "重放不得改写原记录 binding"
    assert after["amounts"] == original_amounts, (
        "重放不得改写原记录保存的金额（原记录事实固定，REQ-13）"
    )
