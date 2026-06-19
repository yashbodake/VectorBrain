"""API integration tests via httpx ASGI client (docs/07).

Embeddings + LLM mocked. Exercises the real router stack (validation, sessions,
SSE rendering) against a clean test DB.
"""

from __future__ import annotations

import io

import pytest

PDF_MAGIC = b"%PDF-1.4\nfake pdf bytes\n"


def _pdf_file(name="doc.pdf", size_extra: int = 0):
    """Build an in-memory PDF-shaped UploadFile payload (magic header + body)."""
    body = PDF_MAGIC + (b"x" * size_extra)
    return (name, body, "application/pdf")


# ---------------------------------------------------------------------------
# POST /api/documents
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_upload_pdf_returns_201_with_shape(client, tmp_path, monkeypatch):
    """Happy path: valid PDF -> 201 with the expected metadata fields."""
    from app.services import storage

    # Redirect on-disk storage to a temp dir so tests don't litter the repo.
    monkeypatch.setattr(storage.settings, "DOCUMENT_STORAGE_PATH", str(tmp_path))

    files = {"file": _pdf_file("Notes.pdf")}
    r = await client.post("/api/documents", files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "uploaded"
    assert body["filename"] == "Notes.pdf"
    assert body["page_count"] is None  # filled in during ingestion
    assert "id" in body and "uploaded_at" in body


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf_with_400(client):
    """A plain text file must be rejected with 400 (docs/04)."""
    files = {"file": ("notes.txt", b"hello world", "text/plain")}
    r = await client.post("/api/documents", files=files)
    assert r.status_code == 400
    assert "not a valid PDF" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_oversized_with_413(client, monkeypatch, tmp_path):
    """Declared size over the limit -> 413 before we even read the body."""
    from app.services import storage

    monkeypatch.setattr(storage.settings, "DOCUMENT_STORAGE_PATH", str(tmp_path))
    # Force a tiny limit for the test. max_upload_size_bytes is a derived
    # @property off MAX_UPLOAD_SIZE_MB, so only the MB field is settable.
    monkeypatch.setattr(storage.settings, "MAX_UPLOAD_SIZE_MB", 1)

    # A ~2MB body but advertise it so the pre-read size check trips.
    big_body = b"%PDF-1.4\n" + (b"0" * (2 * 1024 * 1024))
    files = {"file": ("big.pdf", big_body, "application/pdf")}
    r = await client.post("/api/documents", files=files)
    assert r.status_code == 413


# ---------------------------------------------------------------------------
# GET /api/documents + DELETE
# ---------------------------------------------------------------------------
async def _seed_one_doc(client) -> int:
    files = {"file": _pdf_file("seeded.pdf")}
    r = await client.post("/api/documents", files=files)
    return r.json()["id"]


@pytest.mark.asyncio
async def test_list_documents_returns_envelope(client):
    doc_id = await _seed_one_doc(client)
    r = await client.get("/api/documents")
    assert r.status_code == 200
    docs = r.json()["documents"]
    assert any(d["id"] == doc_id for d in docs)


@pytest.mark.asyncio
async def test_get_document_by_id(client):
    doc_id = await _seed_one_doc(client)
    r = await client.get(f"/api/documents/{doc_id}")
    assert r.status_code == 200
    assert r.json()["id"] == doc_id


@pytest.mark.asyncio
async def test_get_unknown_document_returns_404(client):
    r = await client.get("/api/documents/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_then_404_on_repeat(client):
    """Delete returns 204; deleting the same id again returns 404 (not silent)."""
    doc_id = await _seed_one_doc(client)
    r1 = await client.delete(f"/api/documents/{doc_id}")
    assert r1.status_code == 204
    r2 = await client.delete(f"/api/documents/{doc_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_cascades_chunks(client, session):
    """Deleting a document removes its chunks via FK ON DELETE CASCADE."""
    from app.db.models import Chunk, Document

    doc = Document(filename="c.pdf", file_path="/tmp/c.pdf", status="ready")
    session.add(doc)
    await session.flush()
    session.add(Chunk(document_id=doc.id, content="x", chunk_index=0, page_number=1, embedding=[0.0] * 384))
    await session.commit()
    doc_id = doc.id

    from sqlalchemy import select
    before = (await session.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars().all()
    assert len(before) == 1

    r = await client.delete(f"/api/documents/{doc_id}")
    assert r.status_code == 204

    # New read of the committed delete (the client call committed its txn).
    session.expire_all()
    after = (await session.execute(select(Chunk).where(Chunk.document_id == doc_id))).scalars().all()
    assert after == []


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_with_no_ready_documents_declines_gracefully(client, mock_llm):
    """No ready docs -> a 200 stream with a canned 'no documents ready' message
    and empty citations — NOT an error (docs/04)."""
    r = await client.post("/api/chat", json={"question": "anything"})
    assert r.status_code == 200
    text = r.text
    assert "event: token" in text
    assert "ready" in text.lower()
    assert "event: done" in text
    assert '"citations": []' in text


@pytest.mark.asyncio
async def test_chat_empty_question_rejected(client):
    """Empty/blank question -> 422 (FastAPI validation)."""
    for bad in [{"question": ""}, {"question": "   "}, {}]:
        r = await client.post("/api/chat", json=bad)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_chat_returns_streamed_answer_with_citations(client, session, mock_llm):
    """With a seeded ready doc + matching chunks, the stream contains tokens
    and a done event with at least one citation."""
    from app.db.models import Chunk, Document

    doc = Document(filename="cats.pdf", file_path="/tmp/cats.pdf", status="ready")
    session.add(doc)
    await session.flush()
    # 'cats' content -> mock embedding matches a 'cats' query.
    from tests.test_services import vec
    session.add(Chunk(document_id=doc.id, content="cats are great pets", chunk_index=0, page_number=1, embedding=vec("cats")))
    await session.commit()

    mock_llm["answer"] = "cats are great"
    r = await client.post("/api/chat", json={"question": "tell me about cats"})
    assert r.status_code == 200
    text = r.text
    assert "event: token" in text
    # Tokens stream with trailing spaces ("cats " "are " "great "), so check
    # each word appears rather than the exact joined substring.
    for word in ("cats", "are", "great"):
        assert word in text
    assert "event: done" in text
    # Citation for cats.pdf page 1 should appear.
    assert "cats.pdf" in text
    assert '"page_number": 1' in text


@pytest.mark.asyncio
async def test_chat_off_topic_question_declines_without_llm(client, session, mock_llm):
    """When nothing clears the threshold, the canned 'couldn't find' message
    streams and the LLM is never called."""
    from app.db.models import Chunk, Document

    doc = Document(filename="cats.pdf", file_path="/tmp/c.pdf", status="ready")
    session.add(doc)
    await session.flush()
    from tests.test_services import vec
    session.add(Chunk(document_id=doc.id, content="cats content", chunk_index=0, page_number=1, embedding=vec("cats")))
    await session.commit()

    # Make any LLM call prove it was used.
    mock_llm["answer"] = "SHOULD NOT APPEAR"

    r = await client.post("/api/chat", json={"question": "tell me about rockets"})
    text = r.text
    assert "couldn't find anything relevant" in text.lower()
    assert "SHOULD NOT APPEAR" not in text
