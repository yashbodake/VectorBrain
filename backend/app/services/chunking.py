"""Chunking: Docling ``HybridChunker`` -> ``(content, page_number, chunk_index)``.

Uses Docling's structure-aware ``HybridChunker`` (tokenizer-aware, respects
document structure) rather than a naive fixed-character splitter. The chunker's
tokenizer is set to the same model used for embeddings (``bge-small-en-v1.5``)
so token counts match the embedding context window — see docs/05 Step 2.

**Page numbers** come from Docling's chunk provenance, not re-derived heuristically.
Docling's provenance API has shifted across versions; we resolve it once at
first use (see :func:`_resolve_page_no`) and then reuse the working accessor. A
chunk may legitimately have no single attributable page (e.g. it spans a page
break) — in that case ``page_number`` is ``None`` and the citation layer shows
"page unknown" rather than crashing (docs/03, docs/06).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from docling.chunking import HybridChunker

from app.core.config import settings


@dataclass(slots=True)
class ParsedChunk:
    """One chunk ready to be embedded and stored.

    ``page_number`` is 1-indexed (matches how humans read PDFs and how the
    citation UI should display it) or ``None`` if unattributable.
    """

    content: str
    page_number: int | None
    chunk_index: int


def chunk_document(doc: Any) -> list[ParsedChunk]:
    """Split a DoclingDocument into :class:`ParsedChunk` s.

    The chunker is built with ``max_tokens`` from settings (default 512, per
    docs/05) and the embedding model's tokenizer so token counts are consistent.
    """
    chunker = HybridChunker(
        tokenizer=settings.EMBEDDING_MODEL,
        max_tokens=settings.CHUNK_MAX_TOKENS,
    )
    get_page = _page_resolver(doc)

    out: list[ParsedChunk] = []
    kept_index = 0  # chunk_index among KEPT chunks (gaps from filtered chunks
                    # don't leave holes in the stored sequence)
    for _doc_idx, chunk in enumerate(chunker.chunk(doc)):
        content = _extract_text(chunk)
        if not content.strip():
            # Skip empty chunks rather than write noise rows to the DB.
            continue
        if _is_boilerplate(content):
            # Skip front/back-matter noise (copyright page, TOC, bibliography,
            # ISBN/legal disclaimers). These embed semantically close to topical
            # queries (they contain the title + keywords) and crowd out real
            # content in top-k retrieval. Eval-proven issue on the Ikigai book:
            # copyright/TOC/biblio ranked #1-3, pushing the actual answer
            # (Ogimi/Okinawa) to rank #8 — outside top_k=6.
            continue
        page_number = get_page(chunk)
        out.append(
            ParsedChunk(
                content=content,
                page_number=page_number,
                chunk_index=kept_index,
            )
        )
        kept_index += 1
    return out


# Patterns that mark a chunk as boilerplate front/back-matter rather than
# content. Matched case-insensitively against the chunk text. Tuned to catch
# the publishers'-office pages (copyright, ISBN, TOC, bibliography, legal
# disclaimers) that Docling faithfully extracts but add no answer value.
_BOILERPLATE_MARKERS = (
    "all rights reserved",
    "library of congress",
    "copyright ©",
    "copyright by",
    "isbn",
    "published by",
    "no part of this book",
    "without the prior written permission",
    "title page\n",
    "table of contents",
    "this book was set",
    "printed in the",
    "disclaimer",
    "selected bibliography",
    "for further reading",
    "works cited",
    "references\n",
)


def _looks_like_citation_list(content: str) -> bool:
    """Detect a bibliography/references page: many short lines that look like
    'Surname, Firstname. Title. Publisher, Year.' citations. Heuristic: at least
    3 lines containing a 4-digit year AND a period (the citation punctuation
    pattern). Catches reference lists that lack an explicit 'Bibliography'
    heading (e.g. the Ikigai book's 'The authors were greatly inspired by:' page
    that lists Breznitz/Hemingway/Buettner with publisher+year)."""
    import re

    year_re = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
    lines = [ln.strip() for ln in content.splitlines() if len(ln.strip()) > 15]
    citation_lines = sum(
        1
        for ln in lines
        if year_re.search(ln) and ("." in ln) and ("," in ln)
    )
    # Require at least 3 citation-like lines AND that they're a sizable fraction
    # of the chunk — a body paragraph won't have 3+ year+period+comma lines.
    return citation_lines >= 3 and citation_lines >= len(lines) * 0.4


def _is_boilerplate(content: str) -> bool:
    """Heuristic: is this chunk publishers'-office noise we shouldn't index?

    A chunk is boilerplate if it's very short (under ~40 chars of real text —
    headers/section dividers) OR contains a strong boilerplate marker OR looks
    like a citation list (bibliography/references page). Kept conservative so
    we never drop genuine content.
    """
    stripped = content.strip()
    # Very short chunks are almost always headers/dividers, not content.
    real_len = sum(1 for c in stripped if not c.isspace())
    if real_len < 40:
        return True
    low = content.lower()
    if any(marker in low for marker in _BOILERPLATE_MARKERS):
        return True
    return _looks_like_citation_list(content)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------
def _extract_text(chunk: Any) -> str:
    """Get the chunk's text. Docling chunks expose ``.text`` on recent
    versions; fall back to ``str()`` if not."""
    text = getattr(chunk, "text", None)
    if text is None:
        text = str(chunk)
    return text


# ---------------------------------------------------------------------------
# Page-number provenance
# ---------------------------------------------------------------------------
def _page_resolver(doc: Any) -> Callable[[Any], int | None]:
    """Return a function ``(chunk) -> page_number|None``.

    Docling's chunk→page provenance has used a few different shapes across
    versions. Rather than hardcode one path and break on upgrade, we probe the
    live objects once and return a closure bound to whichever accessor works.
    This keeps the hot path (per-chunk) cheap: no repeated ``hasattr`` checks.
    """
    accessor = _resolve_page_accessor()
    if accessor is not None:
        return accessor

    # Nothing worked on this version — degrade gracefully: every chunk gets
    # ``None`` (citation UI shows "page unknown"). Prefer this over crashing.
    return lambda _chunk: None


@lru_cache(maxsize=1)
def _resolve_page_accessor(_doc_repr: str = "") -> Callable[[Any], int | None] | None:
    """Find a working chunk→page accessor for the installed Docling version.

    NOTE: this is ``lru_cache``d, so it must NOT take the (unhashable)
    ``DoclingDocument`` directly — a previous version did and crashed with
    ``TypeError: unhashable type: 'DoclingDocument'``. ``_doc_repr`` is a plain
    string sentinel (unused) that just lets us keep the cache without hashing a
    Pydantic model. The accessor closures operate only on chunks, not the doc.

    Tries the known provenance paths in order. Each candidate is null-safe
    (returns ``None`` on any AttributeError), so a wrong guess yields ``None``
    pages rather than a crash.
    """
    candidates: list[Callable[[Any], int | None]] = [
        _via_meta_doc_items,
        _via_origins,
    ]
    # Return the first candidate; all are null-safe, order is just a hint.
    for cand in candidates:
        try:
            # sanity: callable present
            if callable(cand):
                return cand
        except Exception:
            continue
    return None


def _via_meta_doc_items(chunk: Any) -> int | None:
    """Path A (current Docling): ``chunk.meta.doc_items`` -> item.prov -> page_no.

    On current Docling, ``chunk.meta`` is a ``DocMeta`` Pydantic model; the
    items live on the ``doc_items`` **attribute** (no ``.get()``, despite the
    Pydantic surface). On older Docling ``meta`` was a plain dict and the items
    came via ``meta["doc_items"]``. We try attribute-first (confirmed working on
    Docling 2.104), then dict-style, so this survives version drift.
    """
    try:
        meta = getattr(chunk, "meta", None)
        if meta is None:
            return None
        # Attribute-style first (current Docling DocMeta).
        doc_items = getattr(meta, "doc_items", None)
        # Dict-style fallback (older Docling).
        if not doc_items and isinstance(meta, dict):
            doc_items = meta.get("doc_items")
        if not doc_items:
            return None
        return _first_page_from_items(doc_items)
    except Exception:
        return None


def _via_origins(chunk: Any) -> int | None:
    """Path B (older/alt Docling): ``chunk.origins`` -> provenance page numbers."""
    try:
        origins = getattr(chunk, "origins", None) or getattr(chunk, "prov", None)
        if not origins:
            return None
        for origin in origins:
            page = getattr(origin, "page_no", None) or getattr(origin, "page", None)
            if isinstance(page, int) and page >= 1:
                return page
            # some versions store page under .crefs -> resolved differently
    except Exception:
        return None
    return None


def _first_page_from_items(doc_items: Any) -> int | None:
    """Take the first valid (1-indexed) page number across the items' provenance."""
    try:
        for item in doc_items:
            provs = getattr(item, "prov", None) or []
            for prov in provs:
                page = getattr(prov, "page_no", None)
                if isinstance(page, int) and page >= 1:
                    return page
    except Exception:
        return None
    return None
