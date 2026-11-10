"""有界 PDF 文本抽取（H2-B：官方 e-Legislation Verified Copy PDF）。

用户 2026-11-11 批准引入 PDF 解析依赖（pypdf）；仅用于法例 PDF 的锚定抽取。
PDF 炸弹/超大文档 fail closed：页数与字符数均有上限。
"""

from __future__ import annotations

import io

MAX_PDF_PAGES = 20
MAX_PDF_CHARS = 2_000_000


def extract_pdf_text(document: bytes) -> str:
    """按页抽取文本；页数/字符超限 → ValueError（调用方视为解析失败）。"""
    if not isinstance(document, (bytes, bytearray)):
        raise TypeError("document 必须为字节")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(bytes(document)))
    pages = reader.pages
    if len(pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF 页数超限（{len(pages)} > {MAX_PDF_PAGES}）")
    chunks: list[str] = []
    total = 0
    for page in pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:  # 兼容不支持 layout 模式的 pypdf
            text = page.extract_text() or ""
        total += len(text)
        if total > MAX_PDF_CHARS:
            raise ValueError(f"PDF 文本超限（>{MAX_PDF_CHARS} 字符）")
        chunks.append(text)
    return "\n".join(chunks)