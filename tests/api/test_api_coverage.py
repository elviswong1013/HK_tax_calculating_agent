"""REQ-1 覆盖页数据驱动盘点＋PA 评税选择标注＋未实现项无计算入口。

REQ: REQ-1（SDD §9：覆盖页由数据驱动呈现 PRD §3.3 盘点＋§3.1 支持矩阵
（字段：税项/子项/支持级别/主管机关/官方来源/核验日/适用期间/缺口）；
PA 标为评税选择；未实现项无计算入口）。
规格锚点:
  - SDD §4 页面 /coverage（GET）与 /api/v1/meta/coverage（GET 覆盖清单）。
  - SDD §9 REQ-1 行；PRD AC-1。
期望值来源: 结构/关键词断言（REQ-1 规定的字段与分区语义）；无金额数值期望。

【拟名】契约说明:
  - /api/v1/meta/coverage 返回 {"items":[…]} 或直接列表；每项恰含 REQ-1 八字段
    的规范键：tax／subtopic／support_level／authority／official_source／
    verified_date／applicable_periods／gaps（值可为空串/空数组，键必须在）。
  - 支持级别词汇（小写比较）："unimplemented"/"not_implemented"/"unsupported"
    之一＝未实现；未实现项不得携带 truthy 的 calc_entry（计算入口）。
  - 计算页路由（§4）：/salaries /profits /property /personal-assessment
    /stamps*。
"""

from __future__ import annotations

import json
import re

FAMILIES = ["薪俸税", "利得税", "物业税", "个人入息课税", "印花税"]
REQUIRED_ITEM_KEYS = [
    "tax",
    "subtopic",
    "support_level",
    "authority",
    "official_source",
    "verified_date",
    "applicable_periods",
    "gaps",
]
NOT_COMPUTABLE_LEVELS = {"unimplemented", "not_implemented", "unsupported"}
CALC_ROUTES = (
    "/salaries",
    "/profits",
    "/property",
    "/personal-assessment",
    "/stamps",
)


def _coverage_items(api) -> list[dict]:
    r = api.get("/api/v1/meta/coverage")
    assert r.status_code == 200, r.text
    payload = r.json()
    items = payload["items"] if isinstance(payload, dict) else payload
    assert isinstance(items, list) and items, "覆盖清单必须非空（REQ-1 盘点）"
    return items


def test_coverage_page_lists_inventory_with_status(api) -> None:
    """覆盖页 HTML＋覆盖清单 JSON 均按 REQ-1 字段呈现全税项盘点与状态。"""
    r = api.get("/coverage")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    for family in FAMILIES:
        assert family in html, f"覆盖页必须列出 {family}（PRD §3.3 盘点）"
    for column in ["支持级别", "主管机关", "官方来源", "核验", "适用期间", "缺口"]:
        assert column in html, f"覆盖页必须呈现字段「{column}」（REQ-1 字段清单）"

    items = _coverage_items(api)
    for item in items:
        for key in REQUIRED_ITEM_KEYS:
            assert key in item, f"覆盖项缺字段 {key!r}（REQ-1）：{item!r}"

    inventory_text = json.dumps({"items": items}, ensure_ascii=False)
    for family in FAMILIES:
        assert family in inventory_text, f"覆盖清单必须覆盖 {family}"


def test_personal_assessment_labelled_election(api) -> None:
    """个人入息课税必须标注为「评税选择」，而非第四个独立税种。"""
    items = _coverage_items(api)
    pa_items = [
        item
        for item in items
        if "个人入息课税" in json.dumps(item, ensure_ascii=False)
        or "personal_assessment" in json.dumps(item)
    ]
    assert pa_items, "覆盖清单必须含个人入息课税条目"
    for item in pa_items:
        item_text = json.dumps(item, ensure_ascii=False)
        assert "评税选择" in item_text, (
            f"PA 条目必须标注为评税选择（REQ-1）：{item_text!r}"
        )


def test_unimplemented_no_calc_entry(api) -> None:
    """未实现项不得有计算入口：无 calc_entry，且（当无任何可计算项时）
    覆盖页不得渲染指向计算页的链接。"""
    items = _coverage_items(api)

    computable = []
    for item in items:
        assert "support_level" in item, f"覆盖项必须有 support_level：{item!r}"
        level = str(item.get("support_level", "")).lower()
        if level in NOT_COMPUTABLE_LEVELS:
            assert not item.get("calc_entry"), (
                f"未实现项不得携带计算入口（REQ-1）：{item!r}"
            )
        if item.get("calc_entry"):
            computable.append(item)

    if not computable:
        r = api.get("/coverage")
        assert r.status_code == 200, r.text
        hrefs = re.findall(r'href="([^"]+)"', r.text)
        leaked = [
            href for href in hrefs
            if any(href.startswith(route) for route in CALC_ROUTES)
        ]
        assert not leaked, (
            f"全部条目未实现时覆盖页不得出现计算入口链接（REQ-1）：{leaked!r}"
        )
