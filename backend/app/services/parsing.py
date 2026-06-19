"""Docling PDF parsing.

Wraps :class:`docling.document_converter.DocumentConverter` so the rest of the
codebase depends on a stable function (``parse_pdf``) rather than Docling's
internals. The converter is constructed once per process — building it reloads
the layout/OCR models each time, which is expensive.

Parsing is CPU/GPU-bound and blocking; callers run it in a worker thread, never
on the event loop (coding-conventions.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from docling.document_converter import DocumentConverter


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    """Process-wide converter. Cached so model weights load only once."""
    return DocumentConverter()


def parse_pdf(file_path: Path) -> tuple[object, int]:
    """Parse a PDF into a DoclingDocument and return ``(doc, page_count)``.

    ``page_count`` is taken from the parsed document's pages (1-indexed
    conceptually, but we just count them). Returns ``0`` if Docling couldn't
    determine page structure — that's a data-quality signal, not an error.

    The returned ``doc`` is Docling's :class:`DoclingDocument`; downstream
    chunking consumes it directly. Typed as ``object`` here to avoid leaking
    the Docling type into this module's public signature (callers in
    ``chunking.py`` import the real type).
    """
    converter = _converter()
    conv_res = converter.convert(str(file_path))
    doc = conv_res.document

    pages = getattr(doc, "pages", None) or {}
    page_count = len(pages) if pages else 0
    return doc, page_count
