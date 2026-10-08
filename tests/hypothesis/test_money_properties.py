"""REQ-9 金额/份额/取整性质测试（Hypothesis 优先；C2.3/C2.4/C2.5/C2.10）。

REQ: REQ-9（精确有理、税点整数商余取整、canonical 定点格式化器）。
规格锚点:
  - Annex C C2.3 RatioStr/Shares：`num/den` 两侧 ASCII 正整数 ∈[1,1e9]，以 gcd
    约分为规范形；`1/3` 合法、`2/6` 规范化为 `1/3`；全程禁浮点；Shares 列表
    Σ share == 1（精确有理），否则 E_INPUT_CONTRADICTORY。
  - Annex C C2.4 精度：份额/分配等非十进制终止中间值一律以整数有理（Fraction）
    计算，禁用二进制浮点；仅在规格指定的税务取整点以整数商/余数精确完成取整，
    再转 Decimal 输出。
  - Annex C C2.5：步骤记录在非终止时携带精确有理表示（num/den）。
  - Annex C C2.10：规范字符串由 Decimal(sign/digits/exponent) 经 ASCII 普通
    定点格式化器生成；禁止以 str(Decimal) 充当规范形；保存/落盘值一律定点、
    禁指数记法（e/E）；尾零归零、无前导零；序列化输出前再校验一次无 e/E。
  - C2 判定示例表：share "1/3"×3 合法（精确和=1）；"0.33333333"×3 拒绝。
期望值来源: 纯性质断言（Hypothesis 生成 + 独立整数算术推导）：
  - 取整 oracle＝测试内以 Python 整数地板除/负数商余独立手算（divmod 层面，
    与生产代码不共享实现）；
  - 格式化 oracle＝测试内纯字符串操作独立推导（去尾零/去前导零）。
  不依赖任何具体税额数值（SDD §9/§11 Red 期望政策：性质期望无需 T1 fixture）。

【拟名】被测契约（app.core.money）:
  - parse_ratio(value: str) -> Fraction：非法比率抛 AppError(code="E_INPUT_TYPE")。
  - validate_shares(ratios: Sequence[str]) -> Fraction：返回精确和；
    Σ≠1 抛 AppError(code="E_INPUT_CONTRADICTORY")。
  - floor_at_point(value: Fraction) -> int / ceil_at_point(value: Fraction) -> int：
    仅整数商/余精确取整，无银行家舍入（C2.4）。
  - format_canonical(dec: Decimal) -> str：C2.10 canonical 定点格式化器。
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.errors import AppError
from app.core.money import (
    ceil_at_point,
    floor_at_point,
    format_canonical,
    parse_ratio,
    validate_shares,
)

CANONICAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$")


# ---------------------------------------------------------------------------
# C2.3 精确份额
# ---------------------------------------------------------------------------
def test_fraction_exact_shares_sum() -> None:
    """RatioStr 精确有理解析；份额列表精确和恒等；非精确/越界一律拒绝。"""
    # C2 示例表：三份 1/3 精确和 = 1
    assert parse_ratio("1/3") == Fraction(1, 3)
    assert parse_ratio("2/6") == Fraction(1, 3)  # gcd 约分为规范形（C2.3）
    assert validate_shares(["1/3", "1/3", "1/3"]) == Fraction(1, 1)

    with pytest.raises(AppError) as excinfo_sum:
        validate_shares(["1/2", "1/3"])  # 5/6 ≠ 1
    assert excinfo_sum.value.code == "E_INPUT_CONTRADICTORY"

    # share 必须为 RatioStr：小数串不接受（C2 示例表："0.33333333"×3 拒绝）
    with pytest.raises(AppError) as excinfo_dec:
        validate_shares(["0.33333333", "0.33333333", "0.33333333"])
    assert excinfo_dec.value.code == "E_INPUT_TYPE"

    # 语法/域拒绝：零分子、零分母、负数、小数侧、空白、越界（>1e9）、非比率
    for bad in [
        "0/3",
        "3/0",
        "-1/3",
        "1.5/3",
        "1 / 3",
        "1/1000000001",
        "1000000001/1",
        "",
        "1",
        "1/2/3",
    ]:
        with pytest.raises(AppError) as excinfo_bad:
            parse_ratio(bad)
        assert excinfo_bad.value.code == "E_INPUT_TYPE", bad

    # 域边界恰含：1e9 两侧均合法
    assert parse_ratio("1000000000/1") == Fraction(1000000000, 1)
    assert parse_ratio("1/1000000000") == Fraction(1, 1000000000)

    @given(
        pairs=st.lists(
            st.tuples(st.integers(min_value=1, max_value=10**6),
                      st.integers(min_value=1, max_value=10**6)),
            min_size=1,
            max_size=4,
        )
    )
    @settings(deadline=None, max_examples=200)
    def _parse_roundtrip(pairs) -> None:
        for a, b in pairs:
            f = parse_ratio(f"{a}/{b}")
            assert f == Fraction(a, b)
            assert f.numerator >= 1 and f.denominator >= 1
            # canonical 形态再解析恒等
            assert parse_ratio(f"{f.numerator}/{f.denominator}") == f

    _parse_roundtrip()

    # 构造精确和为 1 的份额（小分子分母，保证规范形两侧 ≤1e9）：
    # r_i = p_i / Σp —— 必须被 validate_shares 以精确有理接受。
    @given(
        raw=st.lists(
            st.tuples(st.integers(min_value=1, max_value=30),
                      st.integers(min_value=1, max_value=30)),
            min_size=2,
            max_size=3,
        )
    )
    @settings(deadline=None, max_examples=200)
    def _exact_sum_one(raw) -> None:
        fracs = [Fraction(a, b) for a, b in raw]
        total = sum(fracs, Fraction(0))
        shares = [f / total for f in fracs]
        rendered = [f"{s.numerator}/{s.denominator}" for s in shares]
        assert all(s.numerator <= 10**9 and s.denominator <= 10**9 for s in shares)
        assert validate_shares(rendered) == Fraction(1, 1)

    _exact_sum_one()


# ---------------------------------------------------------------------------
# C2.4 取整＝整数商/余精确；无银行家舍入
# ---------------------------------------------------------------------------
def test_no_bankers_at_tax_points() -> None:
    """半点值按取整方向走，绝不受奇偶影响（银行家舍入偏差即违例）。"""
    # 反银行家判定点：3.5 floor=3（银行家给 4）；2.5 ceil=3（银行家给 2）
    assert floor_at_point(Fraction(7, 2)) == 3
    assert ceil_at_point(Fraction(5, 2)) == 3
    assert floor_at_point(Fraction(5, 2)) == 2
    assert ceil_at_point(Fraction(7, 2)) == 4

    @given(n=st.integers(min_value=-(10**6), max_value=10**6))
    @settings(deadline=None, max_examples=300)
    def _half_values(n) -> None:
        half = Fraction(2 * n + 1, 2)
        assert floor_at_point(half) == n  # 与 n 奇偶无关
        assert ceil_at_point(half) == n + 1

    _half_values()


def test_integer_division_rounding_exact() -> None:
    """取整以整数商/余精确完成：oracle＝测试内独立地板除推导；超浮点精度仍精确。"""
    # 超 float53 位精度的精确性：任何浮点中间值都会失真（C2.4 禁浮点）
    assert floor_at_point(Fraction(2**53 + 1, 1)) == 2**53 + 1
    assert ceil_at_point(Fraction(2**53 + 1, 1)) == 2**53 + 1
    big_num, big_den = 10**25 + 1, 10**9 + 7
    assert floor_at_point(Fraction(big_num, big_den)) == big_num // big_den
    assert ceil_at_point(Fraction(big_num, big_den)) == -((-big_num) // big_den)

    @given(
        a=st.integers(min_value=-(10**12), max_value=10**12),
        b=st.integers(min_value=1, max_value=10**9),
    )
    @settings(deadline=None, max_examples=400)
    def _matches_integer_division(a, b) -> None:
        f = Fraction(a, b)
        assert floor_at_point(f) == a // b  # Python 地板除＝精确整数商（独立 oracle）
        assert ceil_at_point(f) == -((-a) // b)
        # 整数输入取整不变
        assert floor_at_point(Fraction(a, 1)) == a
        assert ceil_at_point(Fraction(a, 1)) == a

    _matches_integer_division()


# ---------------------------------------------------------------------------
# C2.10 canonical 定点格式化器
# ---------------------------------------------------------------------------
def test_fixed_point_formatter_no_exponent() -> None:
    """格式化器输出恒为无 e/E 的 canonical 定点串；oracle＝字符串独立推导。"""
    fixed_cases = [
        (Decimal("1200000.00"), "1200000"),
        (Decimal("2.4E+5"), "240000"),  # str(Decimal) 含指数 —— 规范形禁止
        (Decimal("2.4E5"), "240000"),
        (Decimal("1E+14"), "100000000000000"),
        (Decimal("2.5E-1"), "0.25"),
        (Decimal("0E+2"), "0"),
        (Decimal("-0.00"), "0"),  # 负零一切形态归一为 "0"（C2.2）
        (Decimal("-940.50"), "-940.5"),
        (Decimal("0.10"), "0.1"),
        (Decimal("240000.5"), "240000.5"),
    ]
    for dec, expected in fixed_cases:
        out = format_canonical(dec)
        assert out == expected, f"format_canonical({dec}) = {out!r}, 期望 {expected!r}"
        assert "e" not in out.lower()  # 禁指数记法（C2.10）
        assert CANONICAL_RE.fullmatch(out)
        assert Decimal(out) == dec  # 数值往返恒等

    def _expected_canonical(i: int, frac: int, negative: bool) -> str:
        frac_s = f"{frac:02d}".rstrip("0")
        body = str(i) + (("." + frac_s) if frac_s else "")
        if i == 0 and not frac_s:
            return "0"  # 含负零在内的一切零 → "0"
        return ("-" if negative else "") + body

    @given(
        i=st.integers(min_value=0, max_value=10**14),
        frac=st.integers(min_value=0, max_value=99),
        negative=st.booleans(),
    )
    @settings(deadline=None, max_examples=400)
    def _formatter_property(i, frac, negative) -> None:
        if negative and i == 0 and frac == 0:
            return  # "-0.00" 已由固定用例覆盖；这里跳过同型负零
        raw = f"{'-' if negative else ''}{i}.{frac:02d}"
        dec = Decimal(raw)
        out = format_canonical(dec)
        assert out == _expected_canonical(i, frac, negative)
        assert "e" not in out.lower()
        assert CANONICAL_RE.fullmatch(out)
        assert Decimal(out) == dec

    _formatter_property()
