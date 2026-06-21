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
    "about the author",
    "acknowledgments",
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


def _looks_like_footnotes(content: str) -> bool:
    """Detect a footnotes/endnotes chunk.

    Docling merges endnote pages into single chunks where the text starts with
    '- 1\\t\\t\\tDan Buettner...' (dash, number, TAB, author name). The TAB after
    the number is the unambiguous signature — bullet-list items like '- 1 apple'
    use spaces, not tabs. So we check if the chunk STARTS with this pattern.

    This catches footnote/endnote chunks regardless of how Docling splits them
    into lines (it often merges multiple notes into one tab-separated paragraph).
    """
    import re

    # ^-\s*\d+\t : dash, optional spaces, number, then a TAB. The tab is key —
    # it distinguishes Docling's endnote format ('- 1\\t\\t\\tAuthor') from real
    # bullet lists ('- 1 apple'). Compiled once per call (cheap).
    return bool(re.match(r"^-\s*\d+\t", content.lstrip()))


def _is_boilerplate(content: str) -> bool:
    """Heuristic: is this chunk publishers'-office noise we shouldn't index?

    A chunk is boilerplate if it's very short (under ~40 chars of real text —
    headers/section dividers) OR contains a strong boilerplate marker OR looks
    like a citation list (bibliography/references page) OR looks like a table of
    contents (a stack of short title-like lines). Kept conservative so we never
    drop genuine content.
    """
    stripped = content.strip()
    # Very short chunks are almost always headers/dividers, not content.
    real_len = sum(1 for c in stripped if not c.isspace())
    if real_len < 40:
        return True
    # Whitespace-normalize for marker matching: Docling often emits "Title   Page"
    # with runs of spaces, so collapse whitespace before substring checks.
    low_flat = " ".join(content.lower().split())
    flat_markers = tuple(" ".join(m.split()) for m in _BOILERPLATE_MARKERS)
    if any(marker in low_flat for marker in flat_markers):
        return True
    return (
        _looks_like_citation_list(content)
        or _looks_like_toc(content)
        or _looks_like_footnotes(content)
    )


def _looks_like_toc(content: str) -> bool:
    """Detect a table-of-contents page: a stack of short title-like lines.

    TOC chunks are distinctive: many short lines (section/chapter titles) with
    little prose between them. Docling's whitespace is irregular ("Title   Page"
    with multiple spaces), so plain substring markers miss them — this catches
    them structurally. Heuristic: at least 6 non-empty lines, AND most (>=70%)
    are short (<40 chars), AND the chunk has a low ratio of prose. Tuned to
    catch the Ikigai TOC chunk (9/10 lines <40 chars) without firing on a real
    bulleted list (which has longer item text)."""
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if len(lines) < 6:
        return False
    short_lines = sum(1 for ln in lines if len(ln) < 40)
    return short_lines >= len(lines) * 0.7


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
