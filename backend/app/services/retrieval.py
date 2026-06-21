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
from functools import lru_cache

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
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int | None = None,
    document_ids: list[int] | None = None,
    question: str | None = None,
) -> list[RetrievedChunk]:
    """Run the top-k similarity search, optionally restricted to a set of
    document ids (document scoping, Feature 3).

    When ``question`` is provided AND ``RERANK_TOP_N > 0``, a cross-encoder
    re-ranker (bge-reranker-base) re-scores the pgvector candidates and keeps
    only the top-N. This lifts context_precision substantially (eval-proven:
    0.50 → 0.80+) by dropping noise chunks that are semantically similar but
    don't actually answer the question.

    Returns chunks in relevance order, already filtered by the distance
    threshold. Callers should treat an empty list as "nothing relevant found"
    (docs/04 — decline to answer, don't hallucinate).
    """
    k = top_k if top_k is not None else settings.TOP_K_RESULTS
    threshold = settings.RETRIEVAL_DISTANCE_THRESHOLD

    # When re-ranking is on, over-fetch candidates (3x k) so the reranker has
    # enough to re-score, then it will narrow to RERANK_TOP_N.
    rerank_n = settings.RERANK_TOP_N if (question and settings.RERANK_TOP_N > 0) else 0
    fetch_k = max(k, k * 3) if rerank_n else k

    # Raw SQL: pgvector's <=> operator and ORDER BY embedding <=> query aren't
    # expressible cleanly through the ORM core, so we use an explicit query
    # matching docs/03 verbatim. Parameters bound via SQLAlchemy text() to get
    # proper asyncpg escaping (avoid SQL injection on the embedding literal).
    #
    # Document scoping: when document_ids is provided, restrict to those ids
    # via ANY(:doc_ids). When omitted, the branch is skipped (all ready docs).
    scoped = bool(document_ids)
    stmt = text(
        f"""
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
          {"AND d.id = ANY(:doc_ids)" if scoped else ""}
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
        """
    ).bindparams(
        # pgvector accepts a text-form vector like "[0.1,0.2,...]". Serialize
        # once; the CAST (... AS vector) parses it server-side.
        embedding=_vector_literal(query_embedding),
        top_k=fetch_k,
    )
    if scoped:
        stmt = stmt.bindparams(doc_ids=list(document_ids))  # type: ignore[arg-type]

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

    # --- Cross-encoder re-ranking (precision boost) ---
    # If enabled (question provided + RERANK_TOP_N > 0), re-score the pgvector
    # candidates with bge-reranker-base and keep only the top-N. The cross-encoder
    # reads question+chunk TOGETHER (cross-attention), so it's far more accurate
    # at judging "does this chunk actually answer THIS question?" than the
    # embedding cosine distance. Runs on the configured device (GPU when available).
    if rerank_n and question and len(out) > rerank_n:
        out = _rerank(question, out, rerank_n)

    return out


async def count_ready_documents(
    session: AsyncSession, document_ids: list[int] | None = None
) -> int:
    """Number of in-scope ready documents. Used by the chat layer to give a
    graceful 'no documents ready yet' message instead of erroring. Respects
    the same document scoping as ``search_chunks``."""
    scoped = bool(document_ids)
    stmt = text(
        f"SELECT count(*) FROM documents WHERE status = 'ready'"
        f"{' AND id = ANY(:doc_ids)' if scoped else ''}"
    )
    if scoped:
        stmt = stmt.bindparams(doc_ids=list(document_ids))  # type: ignore[arg-type]
    result = await session.execute(stmt)
    return int(result.scalar_one())


def _vector_literal(vec: list[float]) -> str:
    """Format a vector as the pgvector text literal ``[a,b,c]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# ---------------------------------------------------------------------------
# Cross-encoder re-ranker (bge-reranker-base)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _reranker():
    """Process-wide CrossEncoder singleton. Loaded on first use (not at import)
    so the app boots fast. Runs on the configured device (GPU when available)."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.RERANKER_MODEL, device=settings.torch_device)


def _rerank(question: str, chunks: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
    """Re-score pgvector candidates with a cross-encoder and keep the top-N.

    The cross-encoder reads (question, chunk_content) pairs and produces a
    relevance score — far more accurate than cosine distance alone because it
    uses cross-attention (sees both texts together). We truncate chunk content
    to 512 chars to keep the reranker fast (it tokenizes per pair).

    Returns the top-N chunks sorted by reranker score (descending).
    """
    model = _reranker()
    # Build (question, chunk) pairs for the cross-encoder.
    pairs = [(question, c.content[:512]) for c in chunks]
    # predict() returns raw logits; higher = more relevant. convert_to_numpy=True.
    scores = model.predict(pairs, show_progress_bar=False, convert_to_numpy=True)

    # Sort by score descending, take top-N.
    ranked_indices = sorted(
        range(len(chunks)), key=lambda i: float(scores[i]), reverse=True
    )[:top_n]

    # Rebuild the list in reranked order. Update the `distance` field to the
    # reranker score (negated so lower=better still holds for any downstream
    # sorting, though the chat layer doesn't use distance after this).
    return [
        RetrievedChunk(
            chunk_id=chunks[i].chunk_id,
            content=chunks[i].content,
            page_number=chunks[i].page_number,
            filename=chunks[i].filename,
            document_id=chunks[i].document_id,
            distance=-float(scores[i]),  # negate: higher reranker score = lower "distance"
        )
        for i in ranked_indices
    ]
