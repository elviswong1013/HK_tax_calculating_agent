"""REQ-12（交叉 11/14）SSR 页面结构：可达性、焦点、响应式（结构断言）。

REQ: REQ-12（Annex A §9 可访问性与响应行为；§12.9 命名测试
test_ui_focus_aria_responsive_accessibility）。
规格锚点:
  - Annex A §9：语义化标题/表单分组；所有输入程序关联 label；状态更新对
    屏幕阅读器可感知（aria-live）；错误摘要可聚焦；全程键盘可操作；
    200% 放大不遮挡；375px 与 1280px 关键流程无必需横向滚动，中间宽度
    平滑改为单栏（存在断点）。
  - Annex A §4：375px 单栏流式、左右内边距 16px、禁止核心流程横向滚动。
  - Annex A §6/§7：确认摘要对话框与 AI 授权确认页为弹窗（role=dialog、
    打开后焦点进入、Escape 关闭——浏览器行为在 tests/e2e/ 断言）。
  - Annex C C5.5：无财务 localStorage；CSP connect-src 'self'。
期望值来源: 结构断言（HTML/CSS 结构契约）；无金额期望。

【拟名】契约（Green 阶段须按测试实现，不得要求测试改写）:
  - 工作台页为响应式 SSR：viewport meta width=device-width（不允许
    user-scalable=no / maximum-scale="1"——200% 缩放必须可用）。
  - 弹窗标记 role="dialog"；状态通告区 aria-live（或 role=status/alert）。
  - 表单控件全部程序关联 label（label for 或 aria-label/labelledby）。
  - 样式含 @media 断点；@media 之外不得出现 >375px 的固定 width/min-width。
"""

from __future__ import annotations

from _helpers import (
    audit_workbench,
    fetch_asset,
    fixed_width_violations,
)


def test_ui_focus_aria_responsive_accessibility(ui) -> None:
    """工作台 SSR 结构：label 关联、aria-live、dialog 标记、可聚焦错误摘要、
    viewport（允许缩放）、@media 断点、无 >375px 固定宽度、无 localStorage。"""
    api = ui.api
    page = api.get("/")
    assert page.status_code == 200, page.text
    html = page.text
    assert "text/html" in page.headers.get("content-type", "")

    # —— 语义根：zh 语言声明（§9 语义化）——
    import re

    assert re.search(r"<html[^>]*\blang=\"zh", html), "<html> 必须声明中文语言"

    # —— viewport：响应式＋允许 200% 缩放（§9）——
    viewport = re.search(r"<meta[^>]+name=\"viewport\"[^>]*>", html)
    assert viewport, "必须有 viewport meta（§4 移动布局）"
    v = viewport.group(0)
    assert "width=device-width" in v, "viewport 必须含 width=device-width（§4）"
    assert "user-scalable=no" not in v, "不得禁用用户缩放（§9 200% 放大）"
    assert 'maximum-scale="1"' not in v and "maximum-scale=1" not in v, (
        "不得锁定缩放倍率（§9 200% 放大不遮挡主要操作）"
    )

    # —— 表单结构：控件存在且全部程序关联 label（§9）——
    audit = audit_workbench(html)
    assert audit.controls, "工作台必须包含结构化表单控件（§6 按主题分组表单）"
    for control in audit.controls:
        assert control["id"], f"控件必须有 id 供 label 关联：{control!r}"
        assert control["id"] in audit.label_for or control["aria_label"], (
            f"控件 {control['id']!r} 缺程序关联 label（§9 label for / aria）：{control!r}"
        )

    # —— 状态通告：aria-live（§9 状态更新对屏幕阅读器可感知）——
    assert (
        "aria-live" in html or 'role="status"' in html or 'role="alert"' in html
    ), "必须有 aria-live 状态通告区（§9；加载/完成/失败可读）"

    # —— 弹窗标记：确认/授权对话框（§6/§7；焦点进出与 Escape 由 e2e 断言）——
    assert 'role="dialog"' in html, "确认/授权弹窗必须标记 role=dialog（§6/§7）"

    # —— 键盘可达：原生按钮＋可聚焦错误摘要（§9 全程键盘可操作）——
    assert audit.button_count >= 1, "主动作必须是原生 <button>（键盘可操作）"
    assert "error-summary" in html and 'tabindex="-1"' in html, (
        "错误摘要容器（error-summary＋tabindex=-1）必须存在且可聚焦（§6/§9）"
    )

    # —— 无财务 localStorage（C5.5；记录仅会话内存＋用户下载副本）——
    assert "localStorage" not in html, "页面不得使用 localStorage（C5.5 无财务 localStorage）"

    # —— 响应式样式：内联＋链接资产；断点存在；无 >375px 固定宽度 ——
    css_parts = list(audit.inline_styles)
    for href in audit.css_links:
        asset = fetch_asset(api, href)
        assert asset is not None and asset.status_code == 200, (
            f"工作台引用的样式资产必须可取（{href!r}）"
        )
        css_parts.append(asset.text)
    css_text = "\n".join(css_parts)
    assert css_text.strip(), "工作台必须携带样式（内联 <style> 或链接样式表，§5 视觉系统）"
    assert "@media" in css_text, "样式必须包含断点（§9 中间宽度平滑改单栏）"
    violations = fixed_width_violations(css_text, limit_px=375)
    assert not violations, (
        f"@media 之外不得出现 >375px 固定 width/min-width（§4 禁止必需横向滚动）：{violations}"
    )
