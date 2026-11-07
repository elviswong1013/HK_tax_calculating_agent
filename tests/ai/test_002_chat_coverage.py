# -*- coding: utf-8 -*-
"""REQ-1/11 聊天本地覆盖指引：同一 coverage_items() 源的三渠道一致性与
确定性标签（002 P2 W3 flow 28；005 §17/005:927–930；004 §3.9）。

REQ: REQ-1（支持范围逐项区分，API/网页/聊天同源）＋REQ-11（不新增端点/工具/
外发键；聊天经本地视图消费同一数据源）。
规格锚点:
  - 005 §17 第 4 条：coverage_items() 扩展为 support_level/phase/limits_zh；
    纯内部 helper chat_coverage_guidance(items) 只读白名单键；API/覆盖页/
    首页聊天面板共用同一数据源；不维护聊天专用清单、不做 heuristic NLP。
  - 005 §17 第 7 条（D 合同）：确定性标签映射；phase2/3 不生成计算入口；
    PA 条目用更正文案；未识别/缺失→「支持范围尚未确认」＋/coverage 链接。
  - 004 §3.9：本地说明明确「非模型回答」；模型未配置/离线可用。
期望值来源: 005 §17 标签字面（005:930）；coverage_items() 数据（W2 已冻结）。

【被测契约】:
  - 改变同一 coverage item 后，/api/v1/meta/coverage、/coverage 页与首页
    `chat-local-coverage-data` 本地视图三渠道一致（同一数据源）。
  - unknown/未识别条目不变成「否/零/可计算」：标「支持范围尚未确认」。
  - phase2/3 与 unimplemented 不称已支持、不生成计算入口；PA 文案为
    「评税选择；本阶段仅 API；有限方案、部分可算，不代表完整最优」。
"""

from __future__ import annotations

import json
import re


def _index_coverage_view(api) -> dict:
    """首页 SSR 内嵌的聊天本地覆盖视图（chat-local-coverage-data JSON）。"""
    page = api.get("/")
    assert page.status_code == 200, page.text
    match = re.search(
        r'<script type="application/json" id="chat-local-coverage-data">(.*?)</script>',
        page.text,
        re.DOTALL,
    )
    assert match, "首页必须内嵌 chat-local-coverage-data 本地视图（005 §17）"
    return json.loads(match.group(1))


def _modified_items() -> list:
    """在同一数据源上做一个可辨识的改动（测试探针标记）。"""
    from app.web.coverage_data import coverage_items  # 延迟导入（C1）

    items = coverage_items()
    items[0] = dict(items[0], subtopic="PROBE-子项标记-覆盖一致性")
    return items


def test_002_chat_coverage_consumes_shared_source_and_preserves_unknown(
    ai_offline, monkeypatch
) -> None:
    """改变同一 coverage item → API／覆盖页／首页聊天本地视图三渠道一致；
    未识别条目保持「支持范围尚未确认」（unknown 不变否/零/可计算）。"""
    api = ai_offline.api
    probe_items = _modified_items()
    probe_items.append(
        {
            "tax": "探针税项",
            "subtopic": "PROBE-未识别等级",
            "support_level": "garbage_level",  # 未识别等级
            "phase": 9,
            "limits_zh": "PROBE-限制",
            "applicable_periods": [],
        }
    )
    # 同一数据源（routes 模块绑定名；覆盖页/API/首页均经此读取）
    monkeypatch.setattr("app.api.routes.coverage_items", lambda: probe_items)

    # —— 渠道一：API ——
    r_cov = api.get("/api/v1/meta/coverage")
    assert r_cov.status_code == 200
    api_items = r_cov.json()["items"]
    assert api_items[0]["subtopic"] == "PROBE-子项标记-覆盖一致性", (
        "API 必须反映同一数据源改动"
    )

    # —— 渠道二：/coverage 页 ——
    page = api.get("/coverage")
    assert page.status_code == 200
    assert "PROBE-子项标记-覆盖一致性" in page.text, (
        "覆盖页必须反映同一数据源改动（无聊天专用清单）"
    )

    # —— 渠道三：首页聊天本地视图（SSR JSON；guidance 由同一 items 生成）——
    view = _index_coverage_view(api)
    guidance = view["guidance"]
    assert view["items"][0]["subtopic"] == "PROBE-子项标记-覆盖一致性", (
        "首页本地视图 items 必须与 coverage_items() 同源"
    )
    g0 = guidance["items"][0]
    assert g0["subtopic"] == "PROBE-子项标记-覆盖一致性", (
        "guidance 必须由改动后的同一 items 生成"
    )
    assert guidance["note_zh"], "本地视图必须带「非模型回答」说明"

    # —— unknown 保持：未识别等级 → 「支持范围尚未确认」，不得变成可计算/否/零 ——
    probe_row = guidance["items"][-1]
    assert probe_row["tax"] == "探针税项"
    labels = probe_row["labels_zh"]
    assert any("支持范围尚未确认" in str(label) for label in labels), (
        f"未识别条目必须标「支持范围尚未确认」，实际：{labels!r}"
    )
    assert not any("可计算" in str(label) and "尚未确认" not in str(label) for label in labels), (
        f"未识别条目不得暗示可计算：{labels!r}"
    )
    assert not any(str(label) in ("否", "零", "0") for label in labels), (
        "unknown 不得变为否/零"
    )
    # —— 本地视图不读用户 facts/消息/模型输出：未配置态可用且为零外发 ——
    assert ai_offline.outbound.calls == [], "本地覆盖视图不得产生任何外发"


def test_002_chat_never_claims_stage2_stage3_or_pa_web_support(ai_offline) -> None:
    """权威本地标签：phase2/3 与 unimplemented 不称已支持且不生成计算入口；
    PA 条目为更正文案；labels 只由 support_level/phase/limits_zh 等有限映射。"""
    api = ai_offline.api
    view = _index_coverage_view(api)
    guidance = view["guidance"]
    items = guidance["items"]
    assert items, "本地视图必须含覆盖条目"

    blob = json.dumps(guidance, ensure_ascii=False)
    # —— PA 条目：评税选择；仅 API；有限方案、部分可算、不代表完整最优/缴款比较 ——
    pa_rows = [item for item in items if item.get("tax") == "个人入息课税"]
    assert pa_rows, "覆盖条目须含 PA 行"
    pa_blob = json.dumps(pa_rows[0], ensure_ascii=False)
    assert "评税选择" in pa_blob, "PA 条目须标明「评税选择」（005 §17 D 合同）"
    assert "本阶段仅 API" in pa_blob or "仅有 API" in pa_blob, (
        "PA 条目须标明网页不提供计算工作台（本阶段仅 API）"
    )
    assert "不代表完整最优" in pa_blob, "PA 条目不得称完整最优"

    # —— phase2/3／unimplemented／manual：不称已支持、不生成计算入口 ——
    from app.web.coverage_data import chat_coverage_guidance  # 延迟导入（C1）

    probe = chat_coverage_guidance(
        [
            {"tax": "T2", "subtopic": "阶段2", "support_level": "computable",
             "phase": 2, "limits_zh": "阶段二边界", "applicable_periods": []},
            {"tax": "T3", "subtopic": "阶段3", "support_level": "computable",
             "phase": 3, "limits_zh": "阶段三边界", "applicable_periods": []},
            {"tax": "TU", "subtopic": "未实现", "support_level": "unimplemented",
             "phase": 1, "limits_zh": "未实现边界", "applicable_periods": []},
            {"tax": "TM", "subtopic": "人工", "support_level": "manual_handoff",
             "phase": 1, "limits_zh": "PROBE-人工边界", "applicable_periods": []},
            {"tax": "TN", "subtopic": "缺资料", "support_level": "needs_input",
             "phase": 1, "limits_zh": "缺资料边界", "applicable_periods": []},
        ]
    )
    rows = {row["tax"]: row for row in probe["items"]}
    assert any("后续阶段，当前不支持计算" in x for x in rows["T2"]["labels_zh"]), (
        "phase2 不得称已支持（须标后续阶段不支持计算）"
    )
    assert any("后续阶段，当前不支持计算" in x for x in rows["T3"]["labels_zh"])
    assert any("当前不支持计算" in x for x in rows["TU"]["labels_zh"]), (
        "unimplemented 须明说当前不支持计算"
    )
    assert any("需人工核实" in x for x in rows["TM"]["labels_zh"]), (
        "manual_handoff 须标人工核实／范围外"
    )
    assert "PROBE-人工边界" in json.dumps(rows["TM"], ensure_ascii=False), (
        "manual_handoff 须照录 limits_zh 范围限制"
    )
    assert any("未知不是否或零" in x for x in rows["TN"]["labels_zh"]), (
        "needs_input 须明说未知≠否/零"
    )
    # 确定性：labels 只含字符串列表，不携带计算入口/能力字段
    for row in probe["items"]:
        assert set(row.keys()) == {
            "tax", "subtopic", "support_level", "phase", "limits_zh",
            "applicable_periods", "labels_zh",
        }, f"本地视图行不得携带计算入口/外发键：{sorted(row.keys())!r}"
    # 未识别/缺失等级的 /coverage 链接由首页面板渲染层提供，不是 guidance
    # 数据字段；数据侧不得夹带链接，渲染侧必须提供（e2e flow 29 点击验证）。
    assert "/coverage" not in blob, (
        "本地视图 guidance JSON 不得携带链接字段（链接由首页渲染层提供）"
    )
    home = api.get("/")
    assert home.status_code == 200, home.text
    assert "覆盖详情：/coverage" in home.text, (
        "首页面板渲染层须提供 /coverage 详情链接（005 §17 D 合同）"
    )

    # —— 首页聊天面板无 PA 计算入口（本阶段 PA API-only；不造入口）——
    page = api.get("/")
    assert 'value="personal_assessment" disabled' in page.text, (
        "首页税项选择的 PA 项必须保持 disabled（不提供计算工作台）"
    )
