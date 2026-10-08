"""P0-2 第二轮 Red：非薪俸税项页面浏览器端实际计算 e2e（输入→确认→计算→结果）。

验收缺口（@acceptor 第二轮 P0-2）：/profits、/property、/stamps/lease 仍只是
说明页；须实现各税项输入、补问、确认、计算与结果展示，并补充浏览器端实际
计算测试。本文件为该缺口的 Red 测试（pytest.mark.e2e；Chromium；与
tests/e2e/test_ui_browser.py 同启动/服务夹具风格）——当前各页无表单控件、
无「确认资料并计算」按钮/确认对话框，全部测试应因「控件/流程不存在」失败
（Red 失败原因＝缺口本身，不得以环境/导入错误失败）。

REQ: REQ-12（Annex A §4/§6/§9/§12.2：表单控件、确认对话框、结果状态徽标、
375px 无必需横向滚动）＋REQ-4（利得税）＋REQ-5（物业税）＋REQ-7（租约印花税）。
与 tests/ui/（httpx SSR 结构）及 tests/e2e/test_ui_browser.py（无金额的行为
断言）互补：本文件验证真实浏览器内「填表→确认→计算→结果区数值」完整链路。

期望值来源:
  - 利得：独资（非法团）2,000,000×两级低档 7.5%＝150,000（Annex D D4.1；
    非两级全额 15%→300,000、法团 16.5%→330,000 可区分）。宽减前税额与
    下年度暂缴在 2024/25–2026/27 三年度恒为 150,000（宽减只影响
    final_after_reduction）——断言 150,000 与表单年度默认值无关，不因年度
    默认选择弱化金额断言。
  - 物业：pty.htm Q7（2025/26 官方示例，T1，台账 1.7）：租金 120,000、无
    扣除 → NAV＝(120,000−0)×80%＝96,000 → 税 floor(96,000×15%)＝14,400
    ＋下年度暂缴 14,400。
  - 租约：月租 10,000×6 个月＝租期内总租金 60,000 → ≤1 年档 0.25% →
    ceil 链不变 → 150（若误年化 60,000→120,000/年 → 300 可区分；
    Annex D D3.1/D3.1a，同 tests/engines/stamps/test_lease.py 端点值）。

【拟名】被测契约（Green 阶段须按测试实现，不得要求测试改写）:
  - /profits、/property、/stamps/lease 页含计算表单（<label for> 关联原生
    控件；金额以字符串输入，无千位逗号）；每页主动作按钮文案为
    「确认资料并计算」（Annex A §6 权威文案；原生 <button>）。
  - 点击主动作 → 出现确认摘要对话框（role=dialog，Annex A §6/§12.2 确认
    段的 UI 绑定）；对话框内主按钮文案亦为「确认资料并计算」，经其确认后
    于本机走三段计算（prepare→confirm→execute，C3.2）并渲染结果区。
  - 结果区：完成态显示状态徽标「估算完成」（§12.1 四状态徽标之一）与
    canonical 金额（千位逗号分组允许；断言按数字边界匹配）。
  - /profits 表单字段（labels 含以下子串；选择类为 <select>，三态选项不
    默认）：「主体类别」（选项文字含「独资」）、「应评税利润」、「是否
    选择两级制」（真值选项文字含「是／已选择」）、「无其他关联实体」
    （真值选项文字含「是／无」）、「关联实体声明」（空列表声明选项文字
    含「无」，D4.2 两级制资格三项须显式确认）；课税年度用表单默认值。
  - /property 表单字段：「租金收入」（单物业最小事实；差饷/不可追回租金/
    按金未申报＝无扣除，Q7 口径；引擎侧缺省即零）。
  - /stamps/lease 表单字段：「文书日期」（YYYY-MM-DD）、「月租」、
    「租期」以月数表达（标签形如「租期（月）／租期月数」）、「非租金
    代价」（premium 须显式填 "0"，D3.1 缺失≠零）。
  - 375px 视口下上述流程全程无必需横向滚动（§4；同
    test_core_flows_375_and_1280 判定）。
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time

import pytest

pytestmark = [pytest.mark.e2e]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    """受控回环启动被测应用（真实 uvicorn；单 worker；临时 DB）。

    同 tests/e2e/test_ui_browser.py 的服务夹具（本目录无 conftest，文件自含）。
    """
    import uvicorn

    db_dir = tmp_path_factory.mktemp("e2e-forms")
    old = os.environ.get("HKTAX_DB_PATH")
    os.environ["HKTAX_DB_PATH"] = str(db_dir / "rules.db")
    try:
        from app.main import create_app  # 延迟导入：失败原因=缺失 app 行为

        app = create_app()
    finally:
        if old is None:
            os.environ.pop("HKTAX_DB_PATH", None)
        else:
            os.environ["HKTAX_DB_PATH"] = old

    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", workers=1
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "e2e 服务器未能在 15s 内启动（127.0.0.1 受控回环）"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def page(base_url):
    """Chromium 页面；浏览器缺失时整文件 SKIP（安装留给 Green 阶段）。

    动作/expect 超时 8s：本文件流程含真实 API 往返（prepare→confirm→
    execute），略宽于 test_ui_browser.py 的 3s；Red 阶段仍快速失败。
    """
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=8000)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 —— 缺浏览器二进制＝环境未就绪
            pytest.skip(f"需要 Chromium（Green 阶段运行 playwright install chromium）：{exc}")
        context = browser.new_context()
        context.set_default_timeout(8000)
        pg = context.new_page()
        yield pg
        context.close()
        browser.close()


# ------------------------------------------------------------------ 表单填写
def _select_by_option_text(select_locator, pattern) -> None:
    """按选项文字选 <select>（Playwright select_option 不支持正则 label；
    取匹配 option 的 value 选项；选项文字允许前后缀，如「独资（非法团）」）。"""
    option = select_locator.locator("option", has_text=pattern).first
    option.wait_for(state="attached")
    value = option.get_attribute("value")
    if value is None or value == "":
        value = (option.text_content() or "").strip()
    select_locator.select_option(value=value)


def _fill_profits_facts(page) -> None:
    """利得税最小合法事实（tests/acceptance/_acc_helpers.profits_min_facts
    同形、利润 2,000,000 版本）：独资＋两级制资格三项显式确认。"""
    _select_by_option_text(page.get_by_label("主体类别"), re.compile("独资"))
    page.get_by_label("应评税利润").fill("2000000")
    # D4.2 两级制资格三项（三态、不得默认；无关联实体＝空列表显式声明）
    _select_by_option_text(
        page.get_by_label("是否选择两级制"), re.compile("是|已选择")
    )
    _select_by_option_text(
        page.get_by_label("无其他关联实体"), re.compile("是|无")
    )
    _select_by_option_text(page.get_by_label("关联实体声明"), re.compile("无"))


def _fill_property_q7_facts(page) -> None:
    """物业税 Q7 最小事实：单一物业租金 120,000、无扣除（差饷/不可追回/
    按金未申报——缺省即零，Q7 口径）。"""
    page.get_by_label("租金收入").fill("120000")


def _fill_lease_facts(page) -> None:
    """租约最小事实：文书日期 2025-06-20、月租 10,000×租期 6 个月、
    premium 显式 "0"（D3.1：缺失≠零，不得静默按零计税）。"""
    page.get_by_label("文书日期").fill("2025-06-20")
    page.get_by_label("月租").fill("10000")
    page.get_by_label(re.compile("租期[（(]?月")).fill("6")
    page.get_by_label("非租金代价").fill("0")


# ------------------------------------------------------------------ 流程驱动
def _confirm_and_calculate(page) -> None:
    """三段确认的 UI 绑定：点「确认资料并计算」→ 确认摘要对话框 →
    对话框内同名主按钮确认执行（Annex A §6/§12.2；C3.2）。"""
    from playwright.sync_api import expect

    primary = page.get_by_role("button", name="确认资料并计算")
    primary.wait_for(state="visible")
    primary.click()

    dialog = page.locator("[role=dialog]")
    expect(
        dialog, "点击「确认资料并计算」后必须出现确认摘要对话框（§6/§12.2 确认段）"
    ).to_be_visible()
    dialog.get_by_role("button", name="确认资料并计算").click()


def _expect_complete_result(page, amounts: tuple[str, ...]) -> str:
    """结果区断言：「估算完成」徽标可见＋canonical 金额按数字边界出现。

    千位逗号分组允许（先归一化再匹配 (?<!\\d)N(?!\\d)）；返回结果区原始
    文本供调用方附加断言（如「暂缴」分项）。
    """
    from playwright.sync_api import expect

    main = page.locator("main")
    badge = main.get_by_text("估算完成").first
    expect(
        badge, "计算完成后结果区必须显示「估算完成」状态徽标（§12.1 四状态）"
    ).to_be_visible()

    text = main.inner_text()
    flat = text.replace(",", "").replace("，", "")
    for amount in amounts:
        assert re.search(rf"(?<!\d){amount}(?!\d)", flat), (
            f"结果区未显示 canonical 金额 {amount}（结果区文本：{text!r}）"
        )
    return text


def _assert_no_horizontal_scroll(page, stage: str) -> None:
    """无必需横向滚动判定（§4；同 test_core_flows_375_and_1280 口径）。"""
    scroll_width = page.evaluate("document.scrollingElement.scrollWidth")
    inner = page.evaluate("window.innerWidth")
    assert scroll_width <= inner + 1, (
        f"375px 视口在{stage}出现必需横向滚动"
        f"（scrollWidth={scroll_width} > innerWidth={inner}）"
    )


# ------------------------------------------------------------------ 测试
def test_ui_profits_form_end_to_end_e2e(page, base_url) -> None:
    """利得税完整链路：/profits 填独资 2,000,000 最小事实→确认→结果区
    显示两级低档 150,000 与「估算完成」徽标。"""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/profits")

    _fill_profits_facts(page)
    _confirm_and_calculate(page)
    _expect_complete_result(page, ("150000",))


def test_ui_property_form_end_to_end_e2e(page, base_url) -> None:
    """/property 填 Q7 事实（租金 120,000、无扣除）→ 三段 UI 流程 → 结果区
    显示 NAV 96,000／税 14,400／暂缴 14,400（T1 pty.htm Q7，台账 1.7）。"""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/property")

    _fill_property_q7_facts(page)
    _confirm_and_calculate(page)
    text = _expect_complete_result(page, ("96000", "14400"))
    assert "暂缴" in text, (
        f"结果区必须分项显示下年度暂缴物业税（Q7：税 14,400＋暂缴 14,400）；"
        f"结果区文本：{text!r}"
    )


def test_ui_stamp_lease_form_end_to_end_e2e(page, base_url) -> None:
    """/stamps/lease 填月租 10,000×6 个月（premium 显式 0）→ 确认 → 结果区
    显示租约印花税 150（总租金 60,000×0.25%，≤1 年档不年化）。"""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url + "/stamps/lease")

    _fill_lease_facts(page)
    _confirm_and_calculate(page)
    _expect_complete_result(page, ("150",))


def test_ui_form_mobile_375_e2e(page, base_url) -> None:
    """375px 视口：利得税表单流程全程无必需横向滚动且完成计算
    （§4 移动优先；取字段最多的利得税表单作为最不利情形）。"""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base_url + "/profits")

    _fill_profits_facts(page)
    _assert_no_horizontal_scroll(page, "填写表单后")
    _confirm_and_calculate(page)
    _expect_complete_result(page, ("150000",))
    _assert_no_horizontal_scroll(page, "结果显示后")
