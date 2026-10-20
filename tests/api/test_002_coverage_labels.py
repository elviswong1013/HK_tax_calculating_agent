"""P2 W2 Red — 覆盖矩阵标签、聊天本地指引与同源一致性（005:927–930）。

REQ: REQ-1（AC-1 支持范围三档＋phase＋limits_zh；阶段 2/3 不得称已支持；
PA 为评税选择；页面/API/聊天同源）。

规格锚点（Approved，逐行）:
  - 005:927：`coverage_items()` 逐普通类别/阶段更新为
    `support_level ∈ {computable, needs_input, manual_handoff}`＋`phase`＋
    `limits_zh`；阶段 2/3 不得称已支持或“完整最优”；新增纯内部 helper
    `chat_coverage_guidance(items)`（同文件；只按既有 support_level/phase/
    limits_zh/tax/subtopic/applicable_periods 有限映射，不读用户 facts/
    消息/模型输出）；`index_page` 调 `coverage_items()` **一次**并把本地视图
    传入聊天面板；API/覆盖页/聊天共用同一数据源（不新增端点/工具/外发键）。
  - 005:930：聊天本地覆盖指引的确定性标签（phase1＋computable→可计算；
    needs_input→缺资料待确认；manual_handoff→需人工核实/本阶段范围外＋
    limits；phase2/3→后续阶段，当前不支持计算；PA→评税选择/仅 API/有限
    方案/部分可算/不代表完整最优）。
  - 002 §1.3（AC-1；spec L42–44）：三档由 `unimplemented` 迁移；阶段 2/3
    一律 `manual_handoff`＋“另行需求与规格审批”；PA 标“评税选择（本阶段仅
    纠正既有 PA/no-PA 方案免税额；页面仍 API-only）”；五条现有条目全部更新。
  - 004 §覆盖（L138）：首页路由传 `coverage_items()` 一次，同一数据入纯
    `chat_coverage_guidance(items)`；只映射支持等级/phase/limits/tax/
    subtopic 等有限字段。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 每条 coverage item 含 `phase`（1/2/3）与 `limits_zh`（非空中文边界）；
    阶段 2/3 为 `manual_handoff` 且显式写明“另行/不支持/范围外”；
    `computable` 项 `calc_entry` 指向既有计算路由；非 `computable` 无入口。
  - `chat_coverage_guidance(items)` 为纯函数：只读取白名单键，注入额外
    facts/messages/model_text 不改变输出；无 I/O。
  - 首页只调用 `coverage_items()` 一次；同一本地视图出现在聊天面板；
    `/coverage`、`/api/v1/meta/coverage`、首页同源。
  - 不新增端点（除既有 `/coverage` 与 `/api/v1/meta/coverage`）、不新增
    工具、不新增 consent/preview 外发键。

本文件对 app.* 延迟导入；既有 tests/api/test_api_coverage.py 不改并须继续
通过（兼容性说明见交付报告）。
"""

from __future__ import annotations

import copy
import json

FAMILIES = {"薪俸税", "利得税", "物业税", "个人入息课税", "印花税"}
ALLOWED_LEVELS = {"computable", "needs_input", "manual_handoff"}
CALC_ROUTES = (
    "/salaries",
    "/profits",
    "/property",
    "/personal-assessment",
    "/stamps",
)
OUTBOUND_KEYS = {"endpoint", "model", "profile", "messages", "tools"}
TOOL_NAMES = {
    "propose_input",
    "validate_input",
    "calculate_confirmed",
    "read_result",
}

CANARY = "CANARY-USER-FACTS-7f3a91"
PA_CANARY = "CANARY-MODEL-TEXT-b0d2c4"


def _coverage_items() -> list[dict]:
    from app.web.coverage_data import coverage_items

    items = coverage_items()
    assert isinstance(items, list) and items, "coverage_items() 必须返回非空清单"
    return items


def _text(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False)


def _phase(item: dict) -> int:
    assert "phase" in item, f"覆盖项必须有 phase（005:927/002 §1.3）：{item!r}"
    value = item["phase"]
    if isinstance(value, bool):
        raise AssertionError(f"phase 不得为布尔：{value!r}")
    try:
        phase = int(value)
    except (TypeError, ValueError):
        raise AssertionError(f"phase 必须为 1/2/3：{value!r}") from None
    assert phase in (1, 2, 3), f"phase 必须为 1/2/3：{value!r}"
    return phase


def _app(api):
    transport = getattr(api._client, "_transport", None)
    app = getattr(transport, "app", None)
    assert app is not None, "无法从 ASGITransport 取得 app 把手（测试接缝）"
    return app


# ---------------------------------------------------------------------------
# 节点 1：五条标签完整、阶段边界、PA 评税选择
# ---------------------------------------------------------------------------
def test_002_coverage_labels_support_phase_limits() -> None:
    """005:927/002 §1.3：五条均含三档 support_level＋phase＋limits_zh；
    阶段 2/3 为 manual_handoff 且显式边界；PA 仍标注「评税选择」。

    Approved 未给逐 family→phase/level 的固定映射（005:927 只给字段与边界；
    002 §1.3 只给“阶段 2/3 一律 manual_handoff”与 PA 文案），故本节点断言
    允许集合＋完整性＋阶段边界，不臆造逐 family 等级；该边界在交付报告登记。
    """
    items = _coverage_items()
    assert len(items) == 5, (
        f"现五条普通类别须全部更新（002 §1.3），实际 {len(items)} 条"
    )
    assert {item.get("tax") for item in items} == FAMILIES, (
        "覆盖清单必须恰为五类普通税项（002 §1.3）："
        f"{[item.get('tax') for item in items]!r}"
    )

    problems: list[str] = []
    for item in items:
        tax = item.get("tax")
        level = item.get("support_level")
        if level not in ALLOWED_LEVELS:
            problems.append(
                f"{tax}: support_level 必须 ∈ {sorted(ALLOWED_LEVELS)}"
                f"（005:927/002 §1.3），实际 {level!r}"
            )
        try:
            phase = _phase(item)
        except AssertionError as exc:
            problems.append(str(exc))
            continue
        limits = item.get("limits_zh")
        if not isinstance(limits, str) or not limits.strip():
            problems.append(f"{tax}: limits_zh 必须为非空中文边界（005:927）")
            continue
        if not any("\u4e00" <= char <= "\u9fff" for char in limits):
            problems.append(f"{tax}: limits_zh 必须含中文边界说明（005:927）")
        text = _text(item)
        for forbidden in ("完整最优", "完整支持", "已支持全部", "fully supported"):
            if forbidden in text:
                problems.append(
                    f"{tax}: 不得声称「{forbidden}」（005:927；阶段 2/3 不得称已支持）"
                )
        if phase >= 2:
            if level != "manual_handoff":
                problems.append(
                    f"{tax}: 阶段 {phase} 必须 manual_handoff（002 §1.3），"
                    f"实际 {level!r}"
                )
            if not any(
                token in limits
                for token in ("另行", "审批", "不支持", "范围外", "后续阶段")
            ):
                problems.append(
                    f"{tax}: 阶段 {phase} limits_zh 必须显式边界"
                    f"（另行/不支持/范围外；005:927/:930），实际 {limits!r}"
                )

    pa_items = [item for item in items if item.get("tax") == "个人入息课税"]
    assert len(pa_items) == 1, "覆盖清单必须恰含一条个人入息课税（PA 为评税选择）"
    pa_text = _text(pa_items[0])
    assert "评税选择" in pa_text, (
        f"PA 必须标注为「评税选择」（005:927/002 §1.3），实际 {pa_text!r}"
    )
    pa_limits = pa_items[0].get("limits_zh") or ""
    if not any(token in pa_limits for token in ("API", "接口")):
        problems.append(
            "个人入息课税: limits_zh 必须写明本阶段仅 API（005:927/:930），"
            f"实际 {pa_limits!r}"
        )

    assert not problems, "覆盖标签缺口（005:927）：\n  - " + "\n  - ".join(problems)


# ---------------------------------------------------------------------------
# 合成 items（仅白名单键；不依赖生产清单的当前阶段）
# ---------------------------------------------------------------------------
def _synthetic_items() -> list[dict]:
    common = {
        "authority": "香港税务局（IRD）",
        "official_source": "https://www.ird.gov.hk/",
        "verified_date": "",
        "applicable_periods": ["2025/26"],
        "gaps": "",
        "calc_entry": None,
    }
    return [
        {
            **common,
            "tax": "薪俸税",
            "subtopic": "受雇入息与 MPF",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": "阶段 2 扣除另行需求与规格审批",
        },
        {
            **common,
            "tax": "利得税",
            "subtopic": "两级制",
            "support_level": "needs_input",
            "phase": 1,
            "limits_zh": "资格事实须显式确认，缺失不默认",
        },
        {
            **common,
            "tax": "物业税",
            "subtopic": "NAV",
            "support_level": "manual_handoff",
            "phase": 1,
            "limits_zh": "边界-人工核实-本阶段范围外",
        },
        {
            **common,
            "tax": "印花税",
            "subtopic": "AVD",
            "support_level": "manual_handoff",
            "phase": 2,
            "limits_zh": "边界-后续阶段-另行审批",
        },
        {
            **common,
            "tax": "个人入息课税",
            "subtopic": "评税选择：各评税方式并列比较",
            "support_level": "computable",
            "phase": 1,
            "limits_zh": (
                "本阶段仅有 API，网页不提供计算工作台；有限方案、部分可算，"
                "不代表完整最优或缴款比较"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# 节点 2：chat_coverage_guidance 纯函数（白名单派生、零泄漏、无 I/O）
# ---------------------------------------------------------------------------
def test_002_chat_coverage_guidance_pure_whitelist() -> None:
    """005:927/:930：guidance 只由白名单键派生；额外 facts/messages/模型
    文本不改变输出、不泄漏；无 I/O；确定性标签齐全。"""
    from app.web import coverage_data as coverage_module

    guidance = getattr(coverage_module, "chat_coverage_guidance", None)
    assert guidance is not None, (
        "INTERFACE_MISSING：`app/web/coverage_data.py::chat_coverage_guidance`"
        " 缺失（Approved 005:927）"
    )

    items = _synthetic_items()
    out = guidance(copy.deepcopy(items))
    assert guidance(copy.deepcopy(items)) == out, "guidance 必须确定性"
    text = json.dumps(out, ensure_ascii=False)

    # —— 确定性标签（005:930）——
    assert "可计算" in text, "phase1＋computable 须给「可计算」指引（005:930）"
    assert "缺资料" in text, "needs_input 须给缺资料指引（005:930）"
    assert "未知" in text, "needs_input 须说明未知不是否/零（005:930）"
    assert "人工" in text, "manual_handoff 须给人工核实指引（005:930）"
    assert "边界-人工核实" in text, "manual_handoff 须显示 limits_zh（005:930）"
    assert "后续阶段" in text and "不支持" in text, (
        "phase2/3 须标「后续阶段，当前不支持计算」（005:930）"
    )
    assert "评税选择" in text, "PA 指引须标评税选择（005:930）"
    assert "部分" in text and "最优" in text, (
        "PA 指引须说明有限方案/部分可算/不代表完整最优（005:930）"
    )

    # —— 注入用户 facts/消息/模型文本：输出不得改变、不得泄漏 ——
    dirty = [
        {
            **copy.deepcopy(item),
            "user_facts": {"income": CANARY, "mpf": CANARY},
            "messages": [{"role": "user", "content": CANARY}],
            "model_text": PA_CANARY,
            "notes": CANARY,
        }
        for item in items
    ]
    out_dirty = guidance(dirty)
    assert out_dirty == out, (
        "guidance 不得读取白名单之外的键（用户 facts/消息/模型文本；005:927）"
    )
    dirty_text = json.dumps(out_dirty, ensure_ascii=False)
    assert CANARY not in dirty_text and PA_CANARY not in dirty_text, (
        f"guidance 输出泄漏非白名单内容：{dirty_text!r}"
    )

    # —— 无 I/O：不重读 coverage_items，不打开文件/套接字 ——
    def boom(*args, **kwargs):
        raise AssertionError("guidance 不得执行 I/O（005:927 纯内部 helper）")

    monkeypatch_items = getattr(coverage_module, "coverage_items")
    coverage_module.coverage_items = boom  # type: ignore[assignment]
    try:
        import builtins
        import io
        import socket

        original_open = builtins.open
        original_io_open = io.open
        original_socket = socket.socket
        original_create = socket.create_connection
        builtins.open = boom  # type: ignore[assignment]
        io.open = boom  # type: ignore[assignment]
        socket.socket = boom  # type: ignore[assignment]
        socket.create_connection = boom  # type: ignore[assignment]
        try:
            again = guidance(copy.deepcopy(items))
        finally:
            builtins.open = original_open  # type: ignore[assignment]
            io.open = original_io_open  # type: ignore[assignment]
            socket.socket = original_socket  # type: ignore[assignment]
            socket.create_connection = original_create  # type: ignore[assignment]
    finally:
        coverage_module.coverage_items = monkeypatch_items  # type: ignore[assignment]
    assert again == out, "无 I/O 条件下输出必须不变（005:927）"


# ---------------------------------------------------------------------------
# 节点 3：index_page 只调一次 coverage_items 并把本地视图传入聊天面板
# ---------------------------------------------------------------------------
def test_002_index_page_single_coverage_call_and_chat_view(api, monkeypatch) -> None:
    """005:927/004:138：首页只调用 `coverage_items()` 一次；同一本地视图进入
    聊天面板（含 phase2/3「后续阶段/不支持」与 limits_zh）。"""
    import app.api.routes as routes_module

    sentinel = [
        {
            "tax": "哨兵税项A",
            "subtopic": "哨兵子项A",
            "support_level": "manual_handoff",
            "phase": 1,
            "limits_zh": "哨兵边界A-人工核实-本阶段范围外",
            "authority": "哨兵机关",
            "official_source": "https://example.invalid/a",
            "verified_date": "",
            "applicable_periods": ["2025/26"],
            "gaps": "哨兵缺口A",
            "calc_entry": None,
        },
        {
            "tax": "哨兵税项B",
            "subtopic": "哨兵子项B",
            "support_level": "manual_handoff",
            "phase": 2,
            "limits_zh": "哨兵边界B-另行需求与规格审批",
            "authority": "哨兵机关",
            "official_source": "https://example.invalid/b",
            "verified_date": "",
            "applicable_periods": ["2025/26"],
            "gaps": "哨兵缺口B",
            "calc_entry": None,
        },
    ]
    calls = {"n": 0}
    original = routes_module.coverage_items

    def counting():
        calls["n"] += 1
        return copy.deepcopy(sentinel)

    monkeypatch.setattr(routes_module, "coverage_items", counting)
    resp = api.get("/")
    assert resp.status_code == 200, resp.text[:300]
    html = resp.text

    assert calls["n"] == 1, (
        "index_page 必须只调用 coverage_items() 一次（005:927/004:138），"
        f"实际 {calls['n']} 次"
    )
    assert "哨兵税项A" in html and "哨兵税项B" in html, (
        "首页必须把 coverage_items() 的本地视图传入聊天面板（005:927）"
    )
    assert "哨兵边界A-人工核实" in html and "哨兵边界B-另行需求与规格审批" in html, (
        "聊天本地视图必须包含 limits_zh（005:930）"
    )
    assert "后续阶段" in html and "当前不支持计算" in html, (
        "阶段 2/3 的本地指引必须标「后续阶段，当前不支持计算」（005:930）"
    )

    # 恢复真实数据源，便于同一测试内做同源核对
    monkeypatch.setattr(routes_module, "coverage_items", original)


# ---------------------------------------------------------------------------
# 节点 4：三处同源、calc_entry 合法、无新增端点/工具/外发键
# ---------------------------------------------------------------------------
def test_002_coverage_three_sources_and_no_new_surface(api, monkeypatch) -> None:
    """005:927：`/coverage`、`/api/v1/meta/coverage`、首页同源；computable
    项 calc_entry 指向合法计算路由、非 computable 无入口；不新增端点/工具/
    consent-preview 外发键。"""
    import app.api.routes as routes_module
    import app.ai.tools as tools_module

    # —— 1) 无新增端点（仅既有两个覆盖入口）——
    paths = {
        getattr(route, "path", None)
        for route in getattr(_app(api), "routes", [])
    }
    coverage_paths = {
        path for path in paths if isinstance(path, str) and "coverage" in path
    }
    assert coverage_paths == {"/coverage", "/api/v1/meta/coverage"}, (
        "不得新增覆盖端点（005:927）："
        f"{sorted(coverage_paths)!r}"
    )

    # —— 2) 无新增工具；consent/preview 外发键封闭（005:927）——
    assert set(tools_module.TOOL_NAMES) == TOOL_NAMES, (
        f"工具注册表必须封闭为四工具（005:927），实际 {tools_module.TOOL_NAMES!r}"
    )
    wire = tools_module.wire_tools()
    wire_names = {
        (tool.get("function") or {}).get("name") for tool in wire
    }
    assert wire_names == TOOL_NAMES, f"外发 tools 必须封闭四工具，实际 {wire_names!r}"

    monkeypatch.setenv("HKTAX_MODEL_ENDPOINT", "https://127.0.0.1:9/v1/chat/completions")
    monkeypatch.setenv("HKTAX_MODEL_NAME", "hktax-coverage-test")
    monkeypatch.setenv("HKTAX_MODEL_API_KEY", "sk-COVERAGE-TEST")
    preview = api.post(
        "/api/v1/ai/consent/preview",
        json={"messages": [{"role": "user", "content": "你好"}]},
    )
    assert preview.status_code in (200, 201), preview.text[:300]
    body = preview.json()
    payload = body["payload"]
    assert set(payload) == OUTBOUND_KEYS, (
        "consent/preview 外发负载必须恰为既有五键（C4.6 ①段），"
        f"实际 {sorted(payload)!r}"
    )
    forbidden_keys = {
        key
        for key in list(body) + list(payload)
        if any(token in str(key).lower() for token in ("coverage", "support", "limits"))
    }
    assert not forbidden_keys, (
        f"外发/预览负载不得新增 coverage 键（005:927）：{sorted(forbidden_keys)!r}"
    )
    payload_text = json.dumps(payload, ensure_ascii=False).lower()
    for token in ("limits_zh", "support_level", "chat_coverage_guidance"):
        assert token not in payload_text, (
            f"外发负载不得携带 coverage 字段 {token!r}（005:927）"
        )

    # —— 3) 三处同源（API / 覆盖页 / 首页）——
    api_items = api.get("/api/v1/meta/coverage").json()["items"]
    direct_items = _coverage_items()
    assert api_items == direct_items, (
        "API 覆盖清单必须直接来自 coverage_items()（005:927）"
    )

    coverage_html = api.get("/coverage").text
    home_html = api.get("/").text
    problems: list[str] = []
    for item in api_items:
        tax = item.get("tax")
        level = str(item.get("support_level"))
        if tax not in coverage_html:
            problems.append(f"{tax}: /coverage 缺税项")
        if tax not in home_html:
            problems.append(f"{tax}: 首页缺同源税项")
        if level not in coverage_html:
            problems.append(f"{tax}: /coverage 缺 support_level={level!r}")
        if level not in home_html:
            problems.append(
                f"{tax}: 首页聊天面板缺同源 support_level={level!r}"
            )
        limits = item.get("limits_zh")
        if not isinstance(limits, str) or not limits.strip():
            problems.append(f"{tax}: 缺 limits_zh（005:927）")
        if item.get("support_level") == "computable":
            entry = item.get("calc_entry")
            if not isinstance(entry, str) or not entry.startswith(CALC_ROUTES):
                problems.append(
                    f"{tax}: computable 项 calc_entry 必须指向合法计算路由"
                    f"（005:927），实际 {entry!r}"
                )
        elif item.get("calc_entry"):
            problems.append(
                f"{tax}: 非 computable 项不得有 calc_entry（005:927），"
                f"实际 {item.get('calc_entry')!r}"
            )
    assert not problems, "覆盖三源同源/入口缺口（005:927）：\n  - " + "\n  - ".join(
        problems
    )
