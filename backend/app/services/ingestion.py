"""Ingestion pipeline orchestrator.

Single public entry point: :func:`process_document`. It parses an uploaded PDF
with Docling, chunks it, embeds the chunks, persists them, and drives the
document status machine ``uploaded → processing → ready`` (or ``failed``).

Design notes (docs/05 "Concurrency Note"):
- This is one self-contained callable that takes a ``document_id`` and does all
  the work — deliberately not tangled into the request handler, so swapping in a
  real task queue (Celery/RQ) later is a drop-in change.
- It opens its **own** DB session (the request's session is already closed by
  the time a BackgroundTask runs).
- The CPU/GPU-bound work (Docling parse, ``HybridChunker``, embedding encode)
  is blocking and runs entirely inside a worker thread via
  :func:`anyio.to_thread.run_sync` — never on the event loop (coding-conventions).
- On any failure it sets ``status='failed'`` with a populated ``error_message``
  so no document is ever left stuck in ``'processing'`` (docs/05 Step 4).
"""

from __future__ import annotations

import logging
from pathlib import Path

import anyio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import Chunk, Document
from app.services import chunking, embeddings, parsing

logger = logging.getLogger(__name__)


async def process_document(document_id: int) -> None:
    """Run the full ingestion pipeline for one document.

    Safe to call from a FastAPI ``BackgroundTasks`` job. All exceptions are
    caught and recorded on the document row as ``status='failed'`` — this
    function never raises to its caller (a background task has nowhere
    meaningful to propagate to).
    """
    # Phase 1: mark processing. Do this in its own short session so the status
    # flip is visible to the frontend immediately, before the slow parse starts.
    await _set_status(document_id, status="processing", error_message=None)

    try:
        file_path = await _get_file_path(document_id)

        # All blocking work (parse + chunk + embed) in one worker thread.
        result = await anyio.to_thread.run_sync(_parse_chunk_embed, file_path)
        doc_obj, page_count, parsed_chunks = result

        # Embedding is also blocking; run it in a thread too. Done separately
        # from parsing so a large batch embeds can use the model's batching.
        texts = [c.content for c in parsed_chunks]
        vectors = await anyio.to_thread.run_sync(embeddings.encode_chunks, texts)

        # Persist: write chunks + flip to ready, in one transaction.
        await _persist(
            document_id=document_id,
            page_count=page_count,
            parsed_chunks=parsed_chunks,
            vectors=vectors,
        )
        logger.info("Ingested document %d: %d chunks", document_id, len(parsed_chunks))
    except Exception as exc:  # noqa: BLE001 — record any failure on the row
        logger.exception("Ingestion failed for document %d", document_id)
        await _set_status(
            document_id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# DB helpers (each opens its own short-lived session)
# ---------------------------------------------------------------------------
async def _set_status(
    document_id: int, *, status: str, error_message: str | None
) -> None:
    """Flip a document's status (and optional error message / processed_at).

    The status flip is the critical operation — a document must NEVER be left
    stuck in ``processing``. So we commit the status + error_message first
    (terminal states), and only then attempt to stamp ``processed_at`` in a
    separate step. If the timestamp write fails for any reason, we log it but
    the status has already been persisted. This matters most on the failure
    path: an exception while recording a failure must not mask the failure.
    """
    async with SessionLocal() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            # Document was deleted between upload and processing — nothing to do.
            logger.warning("Document %d vanished before status update", document_id)
            return
        doc.status = status
        if status == "failed":
            doc.error_message = error_message
        elif status == "ready":
            doc.error_message = None
        # Commit the status/error first — this is what unblocks the frontend.
        await session.commit()

    # Now stamp processed_at for terminal states. Isolated so a failure here
    # can't roll back the status change above.
    if status in ("failed", "ready"):
        try:
            async with SessionLocal() as session:
                doc = await session.get(Document, document_id)
                if doc is not None:
                    doc.processed_at = await _now()
                    await session.commit()
        except Exception:
            logger.exception(
                "Could not stamp processed_at for document %d (status=%s already persisted)",
                document_id,
                status,
            )


async def _get_file_path(document_id: int) -> Path:
    async with SessionLocal() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            raise FileNotFoundError(f"Document {document_id} not found")
        if not doc.file_path:
            raise FileNotFoundError(f"Document {document_id} has no file_path")
        path = Path(doc.file_path)
    if not path.exists():
        raise FileNotFoundError(f"Stored PDF missing on disk: {path}")
    return path


async def _persist(
    *,
    document_id: int,
    page_count: int,
    parsed_chunks: list[chunking.ParsedChunk],
    vectors,
) -> None:
    """Write chunks + embeddings, then flip the document to ready.

    Idempotent: any pre-existing chunks for this document are deleted first, so
    re-processing the same doc (e.g. after a failed run) doesn't double-insert.
    """
    async with SessionLocal() as session:
        # Clear any leftover chunks from a prior partial/failed run.
        await session.execute(
            delete(Chunk).where(Chunk.document_id == document_id)
        )

        # Bulk-insert new chunks. Vector values are bound as pgvector literals
        # via the ORM Vector type; list[float] round-trips correctly.
        rows = [
            Chunk(
                document_id=document_id,
                content=c.content,
                page_number=c.page_number,
                chunk_index=c.chunk_index,
                embedding=v.tolist(),
            )
            for c, v in zip(parsed_chunks, vectors, strict=True)
        ]
        if rows:
            session.add_all(rows)

        # Flip the document to ready and stamp processed_at.
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = "ready"
            doc.page_count = page_count
            doc.processed_at = await _now()
            doc.error_message = None

        await session.commit()


async def _now():
    """Timezone-aware UTC now, usable as an ORM attribute value."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Blocking worker (runs in a thread, NOT on the event loop)
# ---------------------------------------------------------------------------
def _parse_chunk_embed(
    file_path: Path,
) -> tuple[object, int, list[chunking.ParsedChunk]]:
    """Pure blocking step: Docling parse + HybridChunker. Returns the parsed
    document object (unused downstream, kept for debugging), the page count,
    and the list of chunks with page numbers + indices."""
    doc_obj, page_count = parsing.parse_pdf(file_path)
    parsed_chunks = chunking.chunk_document(doc_obj)
    return doc_obj, page_count, parsed_chunks
