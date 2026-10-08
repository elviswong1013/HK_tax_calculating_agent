"""金额与标量原语（Annex C C2.1/C2.3/C2.4/C2.10；REQ-9）。

- parse_amount：C2.1 ASCII 全匹配严格语法 → canonical 字符串（C2.2/C2.10 规范化）。
- parse_ratio / validate_shares：C2.3 精确有理比率与份额（全程禁浮点）。
- floor_at_point / ceil_at_point：C2.4 仅整数商/余精确取整（无银行家舍入）。
- format_canonical：C2.10 canonical 定点格式化器（禁 str(Decimal)、禁指数记法）。
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Sequence

from app.core.errors import AppError

# C2.1：ASCII 全匹配；整数部分 1–30 位；小数 ≤2 位；无 + 前缀
_AMOUNT_RE = re.compile(r"-?(0|[1-9][0-9]{0,29})(\.[0-9]{1,2})?")
# C2.3：RatioStr 两侧 ASCII 正整数（前导零拒绝由 [1-9] 首位保证）
_RATIO_RE = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")

_MAX_MAGNITUDE = Decimal("100000000000000")  # |值| ≤ 1e14（C2.1）
_MAX_AMOUNT_CHARS = 32  # 长度 ≤32 字符（C2.1）
_RATIO_LIMIT = 10**9  # 两侧 ∈ [1, 1e9]（C2.3）


def parse_amount(value: object, *, allow_negative: bool = False) -> str:
    """C2.1/C2.2/C2.6：仅接受严格 ASCII 规范字符串，返回 canonical 形式。

    负号仅在 allow_negative=True（显式亏损/退款字段）时接受；负零一切形态拒绝；
    非字符串类型（含 int/float/bool/None/Decimal）一律 E_INPUT_TYPE（C2.6）。
    """
    if not isinstance(value, str):
        raise AppError("E_INPUT_TYPE", "金额必须为字符串（禁止 JSON number/布尔/其他类型）")
    text = value
    if len(text) > _MAX_AMOUNT_CHARS:
        raise AppError("E_INPUT_TYPE", "金额长度超过 32 字符上限")
    negative = text.startswith("-")
    if negative and not allow_negative:
        raise AppError("E_INPUT_TYPE", "该字段不接受负数（负号仅限显式亏损/退款字段）")
    if _AMOUNT_RE.fullmatch(text) is None:
        raise AppError("E_INPUT_TYPE", "金额语法非法（C2.1 ASCII 全匹配）")
    dec = Decimal(text)
    if dec == 0 and negative:
        raise AppError("E_INPUT_TYPE", "负零一切形态拒绝（C2.2）")
    if abs(dec) > _MAX_MAGNITUDE:
        raise AppError("E_INPUT_TYPE", "金额量级超过 1e14 上限")
    return format_canonical(dec)


def parse_ratio(value: object) -> Fraction:
    """C2.3：RatioStr `num/den` → 精确有理（gcd 约分为规范形，全程禁浮点）。"""
    if not isinstance(value, str):
        raise AppError("E_INPUT_TYPE", "比率必须为 num/den 字符串")
    match = _RATIO_RE.fullmatch(value)
    if match is None:
        raise AppError("E_INPUT_TYPE", "比率语法非法（须为 ASCII 正整数 num/den）")
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if numerator > _RATIO_LIMIT or denominator > _RATIO_LIMIT:
        raise AppError("E_INPUT_TYPE", "比率分子/分母超过 1e9 上限")
    return Fraction(numerator, denominator)


def validate_shares(ratios: Sequence[object]) -> Fraction:
    """C2.3：份额列表精确和必须 == 1，返回精确和；否则 E_INPUT_CONTRADICTORY。"""
    total = Fraction(0)
    for ratio in ratios:
        total += parse_ratio(ratio)
    if total != 1:
        raise AppError(
            "E_INPUT_CONTRADICTORY",
            "份额精确和必须为 1（精确有理判定，C2.3）",
        )
    return total


def floor_at_point(value: Fraction) -> int:
    """C2.4：税法取整点＝整数商/余精确 floor（与奇偶无关，无银行家舍入）。"""
    frac = value if isinstance(value, Fraction) else Fraction(value)
    return frac.numerator // frac.denominator


def ceil_at_point(value: Fraction) -> int:
    """C2.4：税法取整点＝整数商/余精确 ceil（与奇偶无关，无银行家舍入）。"""
    frac = value if isinstance(value, Fraction) else Fraction(value)
    return -((-frac.numerator) // frac.denominator)


def format_canonical(dec: Decimal) -> str:
    """C2.10 canonical 定点格式化器：由 Decimal(sign/digits/exponent) 生成。

    禁指数记法（e/E）；尾零归零；无前导零；无正号；一切零形态（含 -0）→ "0"。
    """
    if not isinstance(dec, Decimal):
        raise TypeError("format_canonical 仅接受 Decimal（float 不进入金额链路）")
    sign, digits, exponent = dec.as_tuple()
    number = int("".join(str(d) for d in digits)) if digits else 0
    if number == 0:
        return "0"  # 一切零形态归一（含 -0、0E+2、-0.00）
    if exponent >= 0:
        int_part = str(number) + "0" * exponent
        frac_part = ""
    else:
        denominator = 10 ** (-exponent)
        int_part = str(number // denominator)
        frac_part = str(number % denominator).rjust(-exponent, "0").rstrip("0")
    body = int_part + (("." + frac_part) if frac_part else "")
    return ("-" if sign else "") + body
