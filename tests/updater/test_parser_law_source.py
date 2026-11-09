"""H2/B：HKLII 法例来源解析（Cap.112 Schedule 43）。

官方 elegislation.gov.hk 无稳定可抓取 HTML，更新器采用 HKLII（HKU Law and
Technology Centre）镜像作为法例独立来源。HKLII 使用法例标记
`<heading>/<crossheading>` 与 `colspan` 表头；本测试用真实结构的裁剪样本
验证有界抽取与 fail-closed 行为（不联网）。
"""

from __future__ import annotations

import pytest

from app.updater.parser import extract

SCHEDULE_43_HTML = b"""<html><body><schedule id="ID_1" name="sch43">
<num value="43"> Schedule 43 </num>
<note> [ s. 100 ] </note>
<heading> Reduction of Taxes </heading>
<crossheading><inline class="not_bold"> For the <a href="/s2">year of assessment</a> 2025/26 </inline></crossheading>
<text>
<table class="repeat_header">
<tbody>
<tr><td> </td><td colspan="2"><center> Column 1 <br> (section) </center></td>
<td><center> Column 2 <br> (prescribed percentage or prescribed amount) </center></td></tr>
<tr><td> 1. </td><td colspan="2"> Salaries tax </td><td> </td></tr>
<tr><td> </td><td> (a) </td><td> section 100(1)(a) </td><td> 100% </td></tr>
<tr><td> </td><td> (b) </td><td> section 100(1)(b) </td><td> $3,000 </td></tr>
<tr><td> 2. </td><td colspan="2"> Profits tax </td><td> </td></tr>
<tr><td> </td><td> (a) </td><td> section 100(2)(a) </td><td> 100% </td></tr>
<tr><td> </td><td> (b) </td><td> section 100(2)(b) </td><td> $3,000 </td></tr>
</tbody></table>
</text>
</schedule></body></html>
"""

_HEADING = "For the year of assessment 2025/26"
_COL = "Column 2 (prescribed percentage or prescribed amount)"


def _manifest(row: str, fullpath: str = "profits.reductions.2025_26.cap") -> dict:
    anchor = f"heading:{_HEADING}#row:{row}#col:{_COL}"
    return {
        "manifest_schema_version": "1",
        "source_id": "iro_sch43",
        "url": "https://www.hklii.hk/en/legis/ord/112/sch43",
        "artifact_type": "html_table",
        "parser_id": "stdlib_bounded_html_v1",
        "parser_code_hash": "sha256:" + "ab" * 32,
        "anchors": [
            {"path_regex": f"heading:{_HEADING}", "anchor_status": "confirmed"},
            {"path_regex": anchor, "anchor_status": "confirmed"},
        ],
        "fields": ["profits_reduction_cap_2025_26"],
        "slots": [
            {
                "profile_ID": "profits_reduction_v1",
                "fullpath": fullpath,
                "type": "money_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": anchor,
                "proof_role": "independent_counterpart",
            }
        ],
        "evidence_only_fields": [],
        "legal_state_rules": [],
        "proof_authority": "HKLII Cap.112 Schedule 43",
        "independent_counterpart": "ird_bus_pft",
    }


def test_hklii_schedule_43_extracts_profits_reduction_cap() -> None:
    """colspan 表头展开 + 任意列行标 + "$3,000" 归一化 → 3000。"""
    output = extract(_manifest("section 100(2)(b)"), SCHEDULE_43_HTML)
    assert output["values"]["profits.reductions.2025_26.cap"] == "3000"


def test_hklii_schedule_43_salary_row_also_parses() -> None:
    output = extract(
        _manifest("section 100(1)(b)", "salaries.reductions.2025_26.cap"),
        SCHEDULE_43_HTML,
    )
    assert output["values"]["salaries.reductions.2025_26.cap"] == "3000"


def test_hklii_unknown_row_fails_closed() -> None:
    """行标变化（法例重编号）→ LookupError，不得猜测数值。"""
    with pytest.raises(LookupError):
        extract(_manifest("section 100(2)(c)"), SCHEDULE_43_HTML)


def test_money_string_strips_currency_and_grouping() -> None:
    document = SCHEDULE_43_HTML.replace(b"$3,000", b"$1,500")
    output = extract(_manifest("section 100(2)(b)"), document)
    assert output["values"]["profits.reductions.2025_26.cap"] == "1500"