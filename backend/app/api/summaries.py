"""Chapter summaries API router.

Endpoints:
- POST /api/documents/{id}/summarize — generate + cache per-section summaries
- GET  /api/documents/{id}/summaries — fetch cached summaries
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Chunk, Document, DocumentSummary
from app.models.summary import DocumentSummaryRead
from app.services import summaries as summaries_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["summaries"])

# Group chunks into sections of this many pages (so we don't make 100 LLM
# calls for a 200-page book). Each section = one summary.
PAGES_PER_SECTION = 10


@router.post(
    "/documents/{document_id}/summarize",
    status_code=status.HTTP_201_CREATED,
    summary="Generate and cache per-section summaries",
)
async def generate_summaries(
    document_id: int,
    db: AsyncSession = Depends(get_session),
) -> list[DocumentSummaryRead]:
    """Group the document's chunks into page-range sections, summarize each
    via the LLM, cache in the DB. Replaces any existing summaries."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"No document {document_id}.")
    if doc.status != "ready":
        raise HTTPException(status_code=400, detail="Document is not ready yet.")

    # Fetch all chunks ordered by page.
    stmt = (
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.page_number.asc(), Chunk.chunk_index.asc())
    )
    result = await db.execute(stmt)
    chunks = result.scalars().all()
    if not chunks:
        raise HTTPException(status_code=400, detail="Document has no chunks.")

    # Group chunks into sections by page range (PAGES_PER_SECTION pages each).
    sections: list[dict] = []  # {pages: [min,max], content: str}
    page_to_chunks: dict[int, list[str]] = defaultdict(list)
    for c in chunks:
        if c.page_number is not None:
            page_to_chunks[c.page_number].append(c.content)

    all_pages = sorted(page_to_chunks.keys())
    if not all_pages:
        raise HTTPException(status_code=400, detail="No page numbers in chunks.")

    for section_start in range(all_pages[0], all_pages[-1] + 1, PAGES_PER_SECTION):
        section_end = min(section_start + PAGES_PER_SECTION - 1, all_pages[-1])
        # Collect content for pages in this section.
        content_parts = []
        for p in range(section_start, section_end + 1):
            content_parts.extend(page_to_chunks.get(p, []))
        if not content_parts:
            continue
        sections.append(
            {
                "page_start": section_start,
                "page_end": section_end,
                "content": "\n\n".join(content_parts),
            }
        )

    if not sections:
        raise HTTPException(status_code=400, detail="Could not build sections from chunks.")

    # Summarize each section via the LLM. Run them CONCURRENTLY (each in its own
    # worker thread) so a 123-page book (13 sections) finishes in ~15s instead
    # of ~4min (sequential). This also keeps us well under browser HTTP timeouts.
    async def _summarize_one(s):
        def _work():
            try:
                summary = summaries_service.summarize_section(
                    s["content"], s["page_start"], s["page_end"]
                )
            except RuntimeError as exc:
                logger.warning("Section summary failed (p.%s-%s): %s", s["page_start"], s["page_end"], exc)
                summary = f"(Summary unavailable for pages {s['page_start']}-{s['page_end']})"
            title = summaries_service.make_section_title(s["content"], s["page_start"], s["page_end"])
            return {**s, "summary": summary, "title": title}
        return await anyio.to_thread.run_sync(_work)

    try:
        summarized = await asyncio.gather(*[_summarize_one(s) for s in sections])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Delete old summaries (replace).
    old_stmt = select(DocumentSummary).where(DocumentSummary.document_id == document_id)
    old_result = await db.execute(old_stmt)
    for old in old_result.scalars().all():
        await db.delete(old)

    # Store new summaries.
    rows = []
    for i, s in enumerate(summarized):
        row = DocumentSummary(
            document_id=document_id,
            section_index=i,
            title=s["title"],
            page_start=s["page_start"],
            page_end=s["page_end"],
            summary=s["summary"],
        )
        db.add(row)
        rows.append(row)

    await db.commit()
    for row in rows:
        await db.refresh(row)

    return [DocumentSummaryRead.model_validate(r) for r in rows]


@router.get(
    "/documents/{document_id}/summaries",
    summary="Fetch cached summaries for a document",
)
async def get_summaries(
    document_id: int,
    db: AsyncSession = Depends(get_session),
) -> list[DocumentSummaryRead]:
    """Return cached summaries ordered by section index."""
    stmt = (
        select(DocumentSummary)
        .where(DocumentSummary.document_id == document_id)
        .order_by(DocumentSummary.section_index.asc())
    )
    result = await db.execute(stmt)
    return [DocumentSummaryRead.model_validate(r) for r in result.scalars().all()]
