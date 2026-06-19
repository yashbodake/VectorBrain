"""Service-layer unit tests.

Embeddings + LLM are mocked (conftest), so these are fast and deterministic.
The DB is real (Postgres+pgvector) so the `<=>` operator and FK cascade are
genuinely exercised — mocking SQL would test nothing.
"""

from __future__ import annotations

import pytest

from app.db.models import Chunk, Document


# ---------------------------------------------------------------------------
# Retrieval ranking (docs/07: seed known chunks+vectors, confirm order)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retrieval_returns_relevant_chunks_in_distance_order(session):
    """Seed three docs about different topics; querying for one topic returns
    its chunks first, ordered by cosine distance ascending."""
    from app.services.retrieval import search_chunks

    # Each content string contains a keyword the mock embedding table keys on,
    # so their embeddings are deterministic and orthogonal.
    docs = [
        Document(filename="cats.pdf", file_path="/tmp/a.pdf", status="ready"),
        Document(filename="dogs.pdf", file_path="/tmp/b.pdf", status="ready"),
        Document(filename="rockets.pdf", file_path="/tmp/c.pdf", status="ready"),
    ]
    session.add_all(docs)
    await session.flush()

    chunks = [
        Chunk(document_id=docs[0].id, content="all about cats", chunk_index=0, page_number=1, embedding=vec("cats")),
        Chunk(document_id=docs[1].id, content="all about dogs", chunk_index=0, page_number=1, embedding=vec("dogs")),
        Chunk(document_id=docs[2].id, content="all about rockets", chunk_index=0, page_number=1, embedding=vec("rockets")),
    ]
    session.add_all(chunks)
    await session.commit()

    results = await search_chunks(session, vec("cats"), top_k=3)
    # Best match is the cats chunk; distance ~0 (identical unit vector).
    assert results, "expected at least one result"
    assert results[0].filename == "cats.pdf"
    assert results[0].page_number == 1
    assert results[0].distance < 0.01  # cosine distance of identical vecs ~0
    # Distances must be non-decreasing.
    distances = [r.distance for r in results]
    assert distances == sorted(distances)


@pytest.mark.asyncio
async def test_retrieval_excludes_non_ready_documents(session):
    """Only documents with status='ready' should be searchable. A 'processing'
    doc's chunks must never come back even if perfectly similar."""
    from app.services.retrieval import search_chunks

    ready = Document(filename="ready_cats.pdf", file_path="/tmp/r.pdf", status="ready")
    proc = Document(filename="proc_cats.pdf", file_path="/tmp/p.pdf", status="processing")
    session.add_all([ready, proc])
    await session.flush()
    session.add_all([
        Chunk(document_id=ready.id, content="cats content", chunk_index=0, page_number=1, embedding=vec("cats")),
        Chunk(document_id=proc.id, content="cats content too", chunk_index=0, page_number=1, embedding=vec("cats")),
    ])
    await session.commit()

    results = await search_chunks(session, vec("cats"), top_k=10)
    filenames = {r.filename for r in results}
    assert "ready_cats.pdf" in filenames
    assert "proc_cats.pdf" not in filenames


@pytest.mark.asyncio
async def test_retrieval_applies_distance_threshold(session):
    """Chunks above the 0.5 threshold are filtered out. With the mock's
    orthogonal vectors, an unrelated query is at distance ~1.414 (orthogonal)
    — above 0.5, so no results."""
    from app.services.retrieval import search_chunks

    d = Document(filename="cats.pdf", file_path="/tmp/c.pdf", status="ready")
    session.add(d)
    await session.flush()
    session.add(Chunk(document_id=d.id, content="cats", chunk_index=0, page_number=1, embedding=vec("cats")))
    await session.commit()

    # 'dogs' is orthogonal to 'cats' — distance well above 0.5.
    results = await search_chunks(session, vec("dogs"), top_k=10)
    assert results == [], "orthogonal query should clear no threshold"


# ---------------------------------------------------------------------------
# Document status transitions (docs/07: uploaded -> processing -> ready/failed)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_status_transitions_to_ready_on_success(session, mock_llm, real_process_document):
    """A successful ingestion-style run flips the doc to ready with page_count."""
    process_document = real_process_document

    doc = Document(filename="x.pdf", file_path="/tmp/x.pdf", status="uploaded")
    session.add(doc)
    await session.commit()
    doc_id = doc.id

    # process_document opens its OWN session, so we don't pass `session`.
    # But it needs a real file to parse — patch parsing + chunking to avoid
    # touching Docling. We patch at the service boundary.
    import app.services.ingestion as ing
    import app.services.chunking as chunking

    class FakeChunk:
        def __init__(self, c, p, i):
            self.content, self.page_number, self.chunk_index = c, p, i

    async def fake_get_file_path(did):
        from pathlib import Path
        return Path("/tmp/x.pdf")

    def fake_parse_chunk_embed(path):
        return None, 5, [FakeChunk("cats here", 1, 0), FakeChunk("more cats", 2, 1)]

    monkey_targets = pytest.MonkeyPatch()
    monkey_targets.setattr(ing, "_get_file_path", fake_get_file_path)
    monkey_targets.setattr(ing, "_parse_chunk_embed", fake_parse_chunk_embed)

    await process_document(doc_id)
    monkey_targets.undo()

    # Re-read in a fresh session context (process_document used its own).
    # expunge so the get() below doesn't hit the already-loaded instance.
    session.expire_all()
    refreshed = await session.get(Document, doc_id)
    assert refreshed.status == "ready"
    assert refreshed.page_count == 5
    assert refreshed.processed_at is not None

    # Chunks persisted with embeddings (mocked).
    from sqlalchemy import select
    rows = (await session.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars().all()
    assert len(rows) == 2
    assert all(r.page_number in (1, 2) for r in rows)


@pytest.mark.asyncio
async def test_status_transitions_to_failed_on_exception(session, mock_llm, real_process_document):
    """A forced exception during ingestion must land the doc in 'failed' with
    a populated error_message — never stuck in 'processing'."""
    process_document = real_process_document

    doc = Document(filename="bad.pdf", file_path="/tmp/bad.pdf", status="uploaded")
    session.add(doc)
    await session.commit()
    doc_id = doc.id

    import app.services.ingestion as ing

    async def boom(did):
        raise RuntimeError("disk exploded")

    mp = pytest.MonkeyPatch()
    mp.setattr(ing, "_get_file_path", boom)
    await process_document(doc_id)
    mp.undo()

    session.expire_all()
    refreshed = await session.get(Document, doc_id)
    assert refreshed.status == "failed"
    assert refreshed.error_message is not None
    assert "disk exploded" in refreshed.error_message


# ---------------------------------------------------------------------------
# Document scoping (Feature 3): document_ids restricts which docs are searched
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retrieval_respects_document_scope(session):
    """With document_ids set, only chunks from those docs come back — even if a
    better-matching chunk exists in an out-of-scope doc."""
    from app.services.retrieval import search_chunks

    docs = [
        Document(filename="in_scope.pdf", file_path="/tmp/a.pdf", status="ready"),
        Document(filename="out_scope.pdf", file_path="/tmp/b.pdf", status="ready"),
    ]
    session.add_all(docs)
    await session.flush()
    # Both docs have a 'cats' chunk; the out-of-scope one is *closer* (same
    # vector, lower chunk_id tiebreak aside). Scope must exclude it anyway.
    session.add_all([
        Chunk(document_id=docs[0].id, content="cats in scope", chunk_index=0, page_number=1, embedding=vec("cats")),
        Chunk(document_id=docs[1].id, content="cats out of scope", chunk_index=0, page_number=1, embedding=vec("cats")),
    ])
    await session.commit()

    # Scope to only the first doc.
    results = await search_chunks(session, vec("cats"), top_k=10, document_ids=[docs[0].id])
    assert results, "expected results from the in-scope doc"
    assert all(r.document_id == docs[0].id for r in results), "out-of-scope doc leaked in"
    assert {r.filename for r in results} == {"in_scope.pdf"}

    # No scope -> both docs searchable (backward compatible).
    all_results = await search_chunks(session, vec("cats"), top_k=10)
    assert {r.filename for r in all_results} == {"in_scope.pdf", "out_scope.pdf"}


@pytest.mark.asyncio
async def test_count_ready_respects_scope(session):
    """count_ready_documents honors document_ids — the chat layer uses it to
    decide whether to decline with 'no documents ready'."""
    from app.services.retrieval import count_ready_documents

    docs = [
        Document(filename="a.pdf", file_path="/tmp/a.pdf", status="ready"),
        Document(filename="b.pdf", file_path="/tmp/b.pdf", status="ready"),
    ]
    session.add_all(docs)
    await session.commit()

    assert await count_ready_documents(session) == 2  # all ready
    assert await count_ready_documents(session, [docs[0].id]) == 1  # scoped
    assert await count_ready_documents(session, [999999]) == 0  # none match scope


# ---------------------------------------------------------------------------
# Helper: build a mock embedding vector (384-dim, unit length)
# ---------------------------------------------------------------------------
def vec(keyword: str):
    """Return a 384-dim list matching the mock embedding table in conftest."""
    import numpy as np
    from app.services.embeddings import encode_chunks  # patched version
    arr = encode_chunks([keyword])
    return arr[0].tolist()
