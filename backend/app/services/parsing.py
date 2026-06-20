"""Docling PDF parsing.

Wraps :class:`docling.document_converter.DocumentConverter` so the rest of the
codebase depends on a stable function (``parse_pdf``) rather than Docling's
internals. The converter is constructed once per process — building it reloads
the layout/OCR models each time, which is expensive.

The compute device (CPU/CUDA) comes from ``settings.torch_device``, so the same
code runs on a GPU box (fast OCR + layout) or a CPU box with no edits — see
docs/08-gpu-and-ocr-plan.md Part A.

Parsing is CPU/GPU-bound and blocking; callers run it in a worker thread, never
on the event loop (coding-conventions.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.core.config import settings


def _build_converter() -> DocumentConverter:
    """Construct a DocumentConverter wired to the configured device.

    Docling's accelerator options take an ``AcceleratorDevice`` enum; we map
    our resolved torch device ('cuda'/'cpu') onto it. ``num_threads`` only
    matters on CPU, but it's harmless to pass either way.
    """
    device = (
        AcceleratorDevice.CUDA
        if settings.torch_device == "cuda"
        else AcceleratorDevice.CPU
    )
    pipeline_options = PdfPipelineOptions(
        accelerator_options=AcceleratorOptions(
            device=device,
            num_threads=4,
            # flash attention 2 needs a compatible GPU + wheel; leave off for
            # broad compatibility (the 2050 doesn't support it anyway).
            cuda_use_flash_attention2=False,
        ),
    )
    return DocumentConverter(
        # format_options is a DICT keyed by InputFormat (not a set —
        # PdfFormatOption is unhashable). Keying PDF only configures our
        # accelerator/pipeline for PDF parsing; other formats fall back to
        # Docling defaults (we only ingest PDFs anyway, per docs/01 scope).
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    """Process-wide converter. Cached so model weights load only once."""
    return _build_converter()


def parse_pdf(file_path: Path) -> tuple[object, int]:
    """Parse a PDF into a DoclingDocument and return ``(doc, page_count)``.

    ``page_count`` is taken from the parsed document's pages (1-indexed
    conceptually, but we just count them). Returns ``0`` if Docling couldn't
    determine page structure — that's a data-quality signal, not an error.

    The returned ``doc`` is Docling's :class:`DoclingDocument`; downstream
    chunking consumes it directly. Typed as ``object`` here to avoid leaking
    the Docling type into this module's public signature (callers in
    ``chunking.py`` import the real type).

    NOTE: a ``force_ocr`` parameter is reserved here for Part B of
    docs/08-gpu-and-ocr-plan.md (OCR fallback for font-subsetted PDFs that
    extract as glyph garbage). Not wired yet — added now so the signature is
    stable when Part B lands.
    """
    converter = _converter()
    conv_res = converter.convert(str(file_path))
    doc = conv_res.document

    pages = getattr(doc, "pages", None) or {}
    page_count = len(pages) if pages else 0
    return doc, page_count
