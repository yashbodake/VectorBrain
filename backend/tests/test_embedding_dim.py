"""Tests for the embedding dimension guard + prompt assembly — no DB needed.

The 384-dim invariant is enforced by ``embeddings._assert_dim`` (docs/05 Step
3): a wrong-dim model must fail loudly rather than corrupt the vector(384)
table. We test the guard directly (not through the model) so no torch loads.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services import embeddings, prompting
from app.services.retrieval import RetrievedChunk


def test_assert_dim_accepts_384():
    """A 384-dim array passes the guard silently."""
    good = np.zeros(384, dtype=np.float32)
    embeddings._assert_dim(good)  # must not raise


def test_assert_dim_rejects_wrong_dimension():
    """A non-384 array raises ValueError with a clear message — never a silent
    corruption of the vector(384) column."""
    bad = np.zeros(768, dtype=np.float32)
    with pytest.raises(ValueError, match="dimension mismatch"):
        embeddings._assert_dim(bad)


def test_encode_chunks_empty_returns_empty_array():
    """Empty input -> shape (0, 384), not an error."""
    out = embeddings.encode_chunks([])
    assert out.shape == (0, 384)


def test_encode_query_returns_384():
    """A single query -> a 384-dim vector (mocked, fast)."""
    v = embeddings.encode_query("anything")
    assert v.shape == (384,)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------
def test_build_messages_structure():
    chunks = [
        RetrievedChunk(chunk_id=1, content="cats are great", page_number=3, filename="a.pdf", document_id=1, distance=0.1),
        RetrievedChunk(chunk_id=2, content="dogs too", page_number=None, filename="b.pdf", document_id=2, distance=0.2),
    ]
    msgs = prompting.build_messages("which is best?", chunks)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    # Numbered labels present
    assert "[1] (a.pdf, p. 3)" in user
    # page None -> "p. unknown", not a crash
    assert "[2] (b.pdf, p. unknown)" in user
    assert "which is best?" in user


def test_build_messages_with_no_chunks_still_includes_question():
    """Defensive: even empty context must return a valid messages list."""
    msgs = prompting.build_messages("hello", [])
    assert msgs[-1]["content"].endswith("hello")


def test_citation_deduplication():
    """The chat layer dedupes (filename, page) — two chunks from the same page
    collapse to one citation. Each citation now also carries an excerpt of the
    most-relevant chunk for that page (Feature 2: hoverable citations)."""
    from app.services.chat import _dedupe_citations

    chunks = [
        RetrievedChunk(chunk_id=1, content="a cats passage", page_number=1, filename="x.pdf", document_id=1, distance=0.1),
        RetrievedChunk(chunk_id=2, content="b more cats", page_number=1, filename="x.pdf", document_id=1, distance=0.2),
        RetrievedChunk(chunk_id=3, content="c cats page two", page_number=2, filename="x.pdf", document_id=1, distance=0.3),
        RetrievedChunk(chunk_id=4, content="d other doc", page_number=1, filename="y.pdf", document_id=2, distance=0.4),
    ]
    deduped = _dedupe_citations(chunks)
    # One citation per (filename, page), in retrieval order.
    assert [(c["filename"], c["page_number"]) for c in deduped] == [
        ("x.pdf", 1),
        ("x.pdf", 2),
        ("y.pdf", 1),
    ]
    # The excerpt is the highest-ranked chunk for that page (first occurrence).
    assert deduped[0]["excerpt"] == "a cats passage"
    assert deduped[1]["excerpt"] == "c cats page two"
    assert deduped[2]["excerpt"] == "d other doc"
