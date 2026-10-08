"""REQ-9 金额语法（AmountStr 严格 ASCII 全匹配）＋规范化。

REQ: REQ-9（类型严格面由 tests/api/test_api_input_three_states.py 承接，C9：C2→9/10）。
规格锚点:
  - Annex C C2.1：ASCII 全匹配 `^-?(0|[1-9][0-9]{0,29})(\\.[0-9]{1,2})?$`；
    整数部分 1–30 位；无 `+`；禁止 JSON number、boolean、指数、空白、千位符、
    前导零；长度 ≤32 字符；绝对值 ≤1e14；小数 ≤2 位。
  - Annex C C2.2：接受尾随小数零并规范化为无尾零形式；规范形不含正号；
    `-0`（负零）一切形态拒绝。
  - Annex C C2 判定示例表（逐行覆盖）：`"240000"`、`"240000.5"`、`"1200000.00"`
    合法并 canonical 化；`"0240000"`、`"2.4e5"`、`" 240000"`、`240000`（JSON
    number）、`true` 拒绝；`"-0"`、`"-0.00"` 拒绝；`"-940"` 仅限显式退款/亏损
    字段合法。
  - Annex C C2.6：先严格 str 判定，再正则全匹配，再 `Decimal(value)` 构造；
    任何 int/float/bool → Decimal 的隐式强制视为实现缺陷。
  - SDD §3（AmountStr【拟名】）、§4 错误分类法（E_INPUT_TYPE）。
期望值来源: 纯语法/规范化判定（C2 示例表逐行）；无金额数值期望。

【拟名】被测契约:
  - app.core.money.parse_amount(value, *, allow_negative: bool = False) -> str
    返回 canonical 字符串；非法输入抛 app.core.errors.AppError 且
    AppError.code == "E_INPUT_TYPE"（C3.7 分类）。
  - 负号仅在 allow_negative=True（显式亏损/退款字段，C2.1）时接受；
    负零在任何形态下拒绝（C2.2）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.core.money import parse_amount


def _assert_rejected(value: object, **kwargs) -> None:
    with pytest.raises(AppError) as excinfo:
        parse_amount(value, **kwargs)
    assert excinfo.value.code == "E_INPUT_TYPE", (
        f"{value!r} 应以 E_INPUT_TYPE 拒绝（C2.1/C2.6），实际 code="
        f"{excinfo.value.code!r}"
    )


def test_decimal_canonical_string_only() -> None:
    """C2.1/C2.2 示例表全量：仅严格 ASCII 规范字符串被接受并 canonical 化。"""
    # —— 合法 → canonical（C2 判定示例表前三行）——
    accepted = [
        ("240000", "240000"),
        ("240000.5", "240000.5"),
        ("1200000.00", "1200000"),  # 尾随小数零规范化（C2.2/C2.10）
        ("0", "0"),
        ("0.00", "0"),  # 零的尾零同样归一；非负零，合法
        ("0.5", "0.5"),
        ("940.60", "940.6"),
    ]
    for raw, canonical in accepted:
        assert parse_amount(raw) == canonical, f"{raw!r} 应回 canonical {canonical!r}"

    # —— 量级边界：|值| ≤ 1e14（C2.1）——
    assert parse_amount("100000000000000") == "100000000000000"  # 恰 1e14
    assert parse_amount("99999999999999") == "99999999999999"
    _assert_rejected("100000000000001")  # 1e14 + 1 → 拒绝
    _assert_rejected("-100000000000001")

    # —— 语法拒绝（C2 判定示例表）——
    for bad in [
        "0240000",  # 前导零
        "2.4e5",  # 指数
        "2.4E5",
        " 240000",  # 前导空白
        "240000 ",  # 尾随空白
        "240,000",  # 千位符
        "+940",  # 正号（C2.2）
        "-0",  # 负零一切形态（C2.2）
        "-0.00",
        "940.",  # 小数点后无数字
        ".5",  # 无整数部分
        "240000.123",  # 3 位小数
        "",  # 空串
        "240000.5.5",
    ]:
        _assert_rejected(bad)

    # —— 非字符串类型一律拒绝（C2.6：先严格 str 判定）——
    for bad_value in [240000, 240000.5, True, False, None, Decimal("240000")]:
        _assert_rejected(bad_value)

    # —— 负号仅显式亏损/退款字段（C2.1）——
    with pytest.raises(AppError) as excinfo_default:
        parse_amount("-940")
    assert excinfo_default.value.code == "E_INPUT_TYPE"
    assert parse_amount("-940", allow_negative=True) == "-940"
    with pytest.raises(AppError) as excinfo_negzero:
        parse_amount("-0", allow_negative=True)
    assert excinfo_negzero.value.code == "E_INPUT_TYPE"  # 负零在亏损字段同样拒绝
