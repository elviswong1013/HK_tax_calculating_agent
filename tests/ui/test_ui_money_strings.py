"""REQ-10（交叉 13）UI 金额输入绑定：AmountStr 字符串；禁 parseFloat／number。

REQ: REQ-10／REQ-13（Annex A §12.3 金额与输入绑定；§12.9 命名测试
test_ui_money_as_string_no_parsefloat）。
规格锚点:
  - Annex A §12.3：金额一律以字符串提交（AmountStr），禁止 parseFloat 或
    JSON number；规范形不含指数、空白、千位符与前导零；输入控件仅作纯文本
    港元输入与中文提示。
  - Annex A §3 线框：应课税收入 [____] 港元（金额输入为文本控件＋单位提示）。
  - Annex C C2.6：金额非字符串（int/float/bool/None）→ E_INPUT_TYPE；
    C3.7 → 422 字段级错误。
期望值来源: 结构断言（错误码、控件形态）；无金额期望。
"""

from __future__ import annotations

from _helpers import audit_workbench, fetch_asset, salaries_input


def test_ui_money_as_string_no_parsefloat(ui) -> None:
    """JSON number 金额 → 422 E_INPUT_TYPE；字符串合法；前端模板/脚本不含
    parseFloat；应课税收入为带标签的文本控件（非 type=number）＋港元单位。"""
    api = ui.api

    # —— API 侧：金额作为 JSON number 提交 → 422（C2.6/§12.3）——
    bad = salaries_input()
    bad["employment_income"] = 240000  # int，非 AmountStr
    r_num = api.post(
        "/api/v1/calc/prepare",
        json={"tax_type": "salaries_tax", "schema_version": "1.0.0", "input": bad},
    )
    assert r_num.status_code == 422, r_num.text
    assert r_num.json()["error"]["code"] == "E_INPUT_TYPE", (
        f"JSON number 金额必须 422 E_INPUT_TYPE（§12.3）：{r_num.text!r}"
    )
    assert r_num.json()["error"]["field"] == "employment_income"

    # —— 字符串规范形合法（对照；不得因字符串而拒绝）——
    ok = salaries_input()  # "240000" 字符串
    r_str = api.post(
        "/api/v1/calc/prepare",
        json={"tax_type": "salaries_tax", "schema_version": "1.0.0", "input": ok},
    )
    assert r_str.status_code == 201, r_str.text

    # —— 页面侧：工作台表单结构 ——
    page = api.get("/")
    assert page.status_code == 200, page.text
    html = page.text

    assert "应课税收入" in html, "工作台必须包含「应课税收入」金额输入（Annex A §3）"
    assert "港元" in html, "金额输入旁必须有港元单位提示（§6 单位说明）"

    audit = audit_workbench(html)
    assert audit.controls, "工作台必须包含结构化表单控件（§6 任务相关结构化表单）"

    # parseFloat 禁令：SSR HTML＋内联脚本＋同站脚本资产一律不得出现
    script_texts = list(audit.inline_scripts)
    for src in audit.script_srcs:
        asset = fetch_asset(api, src)
        if asset is not None and asset.status_code == 200:
            script_texts.append(asset.text)
    for text in [html] + script_texts:
        assert "parseFloat" not in text, (
            "前端不得使用 parseFloat 解析金额（§12.3 禁令；金额提交一律字符串）"
        )

    # 金额控件形态：非 number/range（浮点语义控件），须有程序关联 label
    for control in audit.controls:
        assert control["type"] not in ("number", "range"), (
            f"金额/事实输入不得使用 type={control['type']}（§12.3 纯文本输入）：{control!r}"
        )
        assert control["id"], f"表单控件必须有 id 供 label 关联（§9）：{control!r}"
        assert control["id"] in audit.label_for or control["aria_label"], (
            f"控件 {control['id']!r} 必须有程序关联 label（label for / aria）：{control!r}"
        )
