"""H2-B：官方 e-Legislation Verified Copy PDF 行式锚定抽取。

PDF 文本结构依据官方 Schedule 43 PDF（6 页，Last updated 22.5.2026）：
年度标题行 `For the year of assessment 2025/26` + 行 `(b) section 100(2)(b) $3,000`。
本测试不联网：行解析用文本 fixture；pypdf 集成用自造最小 PDF。
"""

from __future__ import annotations

import pytest

from app.updater.parser import extract
from app.updater.pdf_text import extract_pdf_text

_HEADING = "For the year of assessment 2025/26"
_ANCHOR = f"heading:{_HEADING}#row:section 100(2)(b)"

_PDF_TEXT = "\n".join(
    [
        "Schedule 43",
        "[s. 100]",
        "Reduction of Taxes",
        "For the year of assessment 2024/25",
        "Column 1",
        "(section)",
        "(b)  section 100(2)(b)  $1,500",
        "For the year of assessment 2025/26",
        "Column 1",
        "(section)",
        "1.  Salaries tax",
        "(b)  section 100(1)(b)  $3,000",
        "2.  Profits tax",
        "(b)  section 100(2)(b)  $3,000",
        "(Added 2 of 2026 s. 6)",
    ]
)


def _manifest(anchor: str = _ANCHOR) -> dict:
    return {
        "manifest_schema_version": "1",
        "source_id": "iro_sch43",
        "url": "https://www.elegislation.gov.hk/hk/cap112!en/sch43.pdf",
        "artifact_type": "pdf_layout",
        "parser_id": "stdlib_bounded_pdf_v1",
        "parser_code_hash": "sha256:" + "cd" * 32,
        "anchors": [
            {"path_regex": f"heading:{_HEADING}", "anchor_status": "confirmed"},
            {"path_regex": anchor, "anchor_status": "confirmed"},
        ],
        "fields": ["profits_reduction_cap_2025_26"],
        "slots": [
            {
                "profile_ID": "profits_reduction_v1",
                "fullpath": "profits.reductions.2025_26.cap",
                "type": "money_string",
                "unit": "HKD",
                "constraints": {"min": "0", "max": "1000000", "step": "1"},
                "anchor": anchor,
                "proof_role": "independent_counterpart",
            }
        ],
        "evidence_only_fields": [],
        "legal_state_rules": [],
        "proof_authority": "e-Legislation Verified Copy PDF",
        "independent_counterpart": "ird_bus_pft",
    }


def test_pdf_layout_extracts_profits_reduction_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.updater.pdf_text.extract_pdf_text", lambda document: _PDF_TEXT
    )
    output = extract(_manifest(), b"%PDF-1.4 fixture")
    assert output["values"]["profits.reductions.2025_26.cap"] == "3000"


def test_pdf_layout_does_not_cross_year_boundary(monkeypatch) -> None:
    """2025/26 锚定不得取到 2024/25 的 $1,500。"""
    monkeypatch.setattr(
        "app.updater.pdf_text.extract_pdf_text", lambda document: _PDF_TEXT
    )
    output = extract(_manifest(), b"%PDF-1.4 fixture")
    assert output["values"]["profits.reductions.2025_26.cap"] != "1500"


def test_pdf_layout_missing_heading_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.updater.pdf_text.extract_pdf_text", lambda document: _PDF_TEXT
    )
    with pytest.raises(LookupError):
        extract(_manifest("heading:For the year of assessment 2030/31#row:section 100(2)(b)"), b"%PDF-1.4")


def test_pdf_layout_split_row_and_amount(monkeypatch) -> None:
    """版式抽取把行标与金额拆行时，有界向下看 2 行仍可取到金额。"""
    text = "\n".join(
        [
            _HEADING,
            "1.  Profits tax",
            "(b)  section 100(2)(b)",
            "$3,000",
        ]
    )
    monkeypatch.setattr("app.updater.pdf_text.extract_pdf_text", lambda document: text)
    output = extract(_manifest(), b"%PDF-1.4 fixture")
    assert output["values"]["profits.reductions.2025_26.cap"] == "3000"


def test_pdf_text_uses_pypdf(monkeypatch) -> None:
    """pypdf 集成：自造最小一页 PDF，抽取到文本。"""
    document = _minimal_pdf("HELLO SCHEDULE 43")
    text = extract_pdf_text(document)
    assert "HELLO" in text


def _minimal_pdf(text: str) -> bytes:
    """构造最小合法 PDF（单页、Helvetica、一行文本），仅用于离线测试。"""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects[3] = (
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)