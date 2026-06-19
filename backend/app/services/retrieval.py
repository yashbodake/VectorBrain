"""Retrieval: pgvector cosine similarity search over ready documents.

Implements the core retrieval query from docs/03-database-schema.md, restricted
to documents with ``status='ready'``, ordered by cosine distance, capped at
``TOP_K_RESULTS`` and filtered by ``RETRIEVAL_DISTANCE_THRESHOLD``.

Cosine distance semantics (docs/05 Part B Step 2): lower = more similar; the
threshold (0.5 default) treats anything further than that as too weak to be
useful — if the BEST result is above it, retrieval returns nothing and the chat
layer declines to answer rather than letting the LLM hallucinate.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


@dataclass(slots=True)
class RetrievedChunk:
    """One retrieved chunk with the metadata the citation layer needs.

    ``page_number`` may be ``None`` (chunk spans a page break / unattributable) —
    the citation UI shows "page unknown" in that case (docs/06).
    """

    chunk_id: int
    content: str
    page_number: int | None
    filename: str
    document_id: int
    distance: float  # cosine distance from the query embedding (lower = better)


async def search_chunks(
    session: AsyncSession, query_embedding: list[float], top_k: int | None = None
) -> list[RetrievedChunk]:
    """Run the top-k similarity search across all ``ready`` documents.

    Returns chunks in ascending distance order, already filtered by the
    relevance threshold. Callers should treat an empty list as "nothing
    relevant found" (docs/04 — decline to answer, don't hallucinate).
    """
    k = top_k if top_k is not None else settings.TOP_K_RESULTS
    threshold = settings.RETRIEVAL_DISTANCE_THRESHOLD

    # Raw SQL: pgvector's <=> operator and ORDER BY embedding <=> query aren't
    # expressible cleanly through the ORM core, so we use an explicit query
    # matching docs/03 verbatim. Parameters bound via SQLAlchemy text() to get
    # proper asyncpg escaping (avoid SQL injection on the embedding literal).
    stmt = text(
        """
        SELECT
            c.id            AS chunk_id,
            c.content       AS content,
            c.page_number   AS page_number,
            d.filename      AS filename,
            d.id            AS document_id,
            (c.embedding <=> CAST(:embedding AS vector)) AS distance
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.status = 'ready'
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
        """
    ).bindparams(
        # pgvector accepts a text-form vector like "[0.1,0.2,...]". Serialize
        # once; the CAST (... AS vector) parses it server-side.
        embedding=_vector_literal(query_embedding),
        top_k=k,
    )

    result = await session.execute(stmt)
    rows = result.mappings().all()

    out: list[RetrievedChunk] = []
    for row in rows:
        dist = float(row["distance"])
        if dist > threshold:
            # Results are sorted ascending by distance, so once we cross the
            # threshold everything after is also too weak — stop early.
            break
        out.append(
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                content=row["content"],
                page_number=row["page_number"],
                filename=row["filename"],
                document_id=row["document_id"],
                distance=dist,
            )
        )
    return out


async def count_ready_documents(session: AsyncSession) -> int:
    """Number of documents available for retrieval. Used by the chat layer to
    give a graceful 'no documents ready yet' message instead of erroring."""
    stmt = text("SELECT count(*) FROM documents WHERE status = 'ready'")
    result = await session.execute(stmt)
    return int(result.scalar_one())


def _vector_literal(vec: list[float]) -> str:
    """Format a vector as the pgvector text literal ``[a,b,c]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
