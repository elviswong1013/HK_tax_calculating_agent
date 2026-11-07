"""F-07 回归：病态标记输入不得造成更新器解析器 CPU 放大。

原实现 `_parse_sections` 对每个未闭合 `<hN>` 反向扫描到文末（O(n^2)），
`_visible_text` 的标签剥离对每个 `<` 同样回溯到文末。修复后先收集开启/
闭合标签位置再线性配对，标签剥离字符类排除 `<`。本测试用病态输入绑定
线性行为（远低于旧实现的秒级放大），并验证 section 数量上限。
"""

from __future__ import annotations

import time

from app.updater.parser import _parse_sections, _visible_text


def test_hostile_unclosed_markup_is_parsed_in_bounded_time() -> None:
    # 约 2MB：10 万个未闭合 <h2> + 正文
    hostile = ("<h2>" * 100_000) + ("a" * 1_900_000)

    started = time.perf_counter()
    assert _parse_sections(hostile) == []
    assert _visible_text(hostile)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"hostile parse took {elapsed:.2f}s（应远低于旧实现）"


def test_heading_sections_are_capped() -> None:
    # 官方页面标题数远低于上限；超限时截断，保证最坏情况工作量有界。
    document = "".join(f"<h2>H{i}</h2><p>{i}</p>" for i in range(2500))
    assert len(_parse_sections(document)) == 2000