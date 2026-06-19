"""Shared pytest fixtures.

Design: tests run against the REAL Postgres+pgvector (so SQL and the `<=>`
operator are genuinely exercised), but every external call is mocked — the
embedding model (slow, needs torch) and the LLM (network/cost/flaky) are both
patched. That keeps the suite deterministic and fast, per docs/07.

Event-loop note: asyncpg connections are bound to the event loop that created
them. pytest-asyncio's default gives a fresh loop per test, so a module-level
engine (created at import on some other loop) corrupts the connection pool
("got result for unknown protocol state 3"). To avoid that we build a
**function-scoped** engine + session factory here and patch them into
``app.db.base`` for the duration of each test, so production code still reads
``from app.db.base import SessionLocal`` but gets a loop-local pool.

The DB must be running on localhost:5433 (Docker Compose `db` service).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import base as db_base
from app.db.models import Base


# ---------------------------------------------------------------------------
# Per-suite engine + session factory (session-scoped loop — see pyproject.toml
# asyncio_default_fixture_loop_scope). asyncpg binds connections to the loop
# that made them, so we keep ONE loop for the whole suite and ONE engine pool.
# Per-test isolation comes from clean_db (row wipe), not from new loops.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def engine():
    """A single async engine for the whole session. Disposed at session end."""
    eng = create_async_engine(db_base.settings.DATABASE_URL, pool_pre_ping=True)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def session(session_factory, clean_db) -> AsyncIterator[Any]:
    """A real AsyncSession against the test DB, closed after the test."""
    # Patch the module-level SessionLocal so production code (which imports it
    # from app.db.base) uses this session-scoped factory too.
    db_base.SessionLocal = session_factory
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def clean_db(session_factory, engine) -> AsyncIterator[None]:
    """Per-test: wipe all rows so tests are independent. Order matters —
    chunks first (FK), then documents. Also patches SessionLocal to the
    session-scoped factory so ingestion/status code uses the same pool."""
    db_base.SessionLocal = session_factory
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("DELETE FROM chunks"))
        await conn.execute(text("DELETE FROM documents"))
        await conn.commit()
    yield


# ---------------------------------------------------------------------------
# Background-task isolation: uploads schedule `process_document` as a FastAPI
# BackgroundTask, which would spawn real ingestion work (anyio threadpool +
# its own sessions) inside the test's loop. That work can outlive the test and
# raise RuntimeError: Task ... / Event loop is closed during teardown. Since
# the upload integration tests don't need real ingestion (the dedicated tests
# in test_services.py call process_document directly), we make it a no-op here.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _noop_background_ingestion(monkeypatch):
    async def _noop(document_id: int) -> None:
        return None

    # The documents router imported process_document BY NAME, so patch it on
    # the router module too, not just the ingestion module.
    import app.api.documents as docs_router
    import app.services.ingestion as ingestion

    # Stash the real implementation so tests that WANT to drive ingestion
    # directly can (see real_process_document fixture below).
    import app.services.ingestion as _ing
    monkeypatch.setattr(_ing, "process_document", _noop)
    monkeypatch.setattr(docs_router, "process_document", _noop)


@pytest.fixture
def real_process_document(monkeypatch):
    """Restore the real process_document for tests that drive ingestion
    directly (the status-transition tests). Undoes the autouse noop."""
    import app.services.ingestion as ingestion

    # Re-import a fresh reference to the original by reloading the function
    # attribute. Since the noop was set via monkeypatch (which restores on
    # teardown), we just need to point back to the real one for THIS test's
    # duration. The cleanest way: capture it before the noop ran — but the
    # autouse fixture already ran. Instead, grab the function object from the
    # module's original dict isn't possible post-patch. So we re-derive it:
    # the real function is defined in app.services.ingestion; monkeypatch
    # restored it after the previous test. We read the CURRENT (real) value
    # at fixture-setup time of THIS test — but autouse _noop runs first.
    #
    # Simplest robust approach: temporarily set it back to the function
    # object captured at conftest import time (module load).
    monkeypatch.setattr(ingestion, "process_document", _ORIGINAL_PROCESS_DOCUMENT)
    return _ORIGINAL_PROCESS_DOCUMENT


# Capture the real process_document ONCE at conftest import, before any test
# patches it. Module import triggers function definition, so this is the real one.
import app.services.ingestion as _ing_mod  # noqa: E402

_ORIGINAL_PROCESS_DOCUMENT = _ing_mod.process_document


# ---------------------------------------------------------------------------
# Mocks: embedding model + LLM, so tests never load torch or hit Cerebras.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch):
    """Replace the SentenceTransformer-backed functions with deterministic
    ones. We map known strings to fixed 384-dim vectors so retrieval ranking
    tests are exact and fast (docs/07: "simple, clearly-separable test vectors
    rather than real embeddings")."""
    import numpy as np

    from app.services import embeddings

    def _unit(vec: list[float]) -> "np.ndarray":
        v = np.asarray(vec, dtype=np.float32)
        n = float((v @ v) ** 0.5) or 1.0
        return (v / n).astype(np.float32)

    # A fixed table of "embeddings" for deterministic tests. Each is a unit
    # vector along a distinct axis pair so cosine distances are exact.
    DIM = 384

    table = {
        "cats": _unit([1.0] + [0.0] * (DIM - 1)),
        "dogs": _unit([0.0, 1.0] + [0.0] * (DIM - 2)),
        "rockets": _unit([0.0, 0.0, 1.0] + [0.0] * (DIM - 3)),
    }

    def _lookup(text: str):
        t = text.lower()
        for key, vec in table.items():
            if key in t:
                return vec
        # Unknown text -> a far-off vector so it never matches anything close.
        return _unit([0.0] * (DIM - 1) + [1.0])

    def fake_encode_chunks(texts):
        if not texts:
            return np.empty((0, DIM), dtype=np.float32)
        return np.stack([_lookup(t) for t in texts])

    def fake_encode_query(question):
        return _lookup(question)

    monkeypatch.setattr(embeddings, "encode_chunks", fake_encode_chunks)
    monkeypatch.setattr(embeddings, "encode_query", fake_encode_query)
    yield


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch the generation layer so tests can drive the chat pipeline without
    a network call. Returns a small helper to configure what the 'model' says."""
    from app.services import generation

    state = {"answer": "mocked answer", "error": None}

    def fake_stream(messages):
        if state["error"]:
            raise generation.GenerationError(state["error"])
        words = state["answer"].split()
        for w in words:
            yield w + " "

    def fake_generate(messages):
        if state["error"]:
            raise generation.GenerationError(state["error"])
        return state["answer"]

    monkeypatch.setattr(generation, "stream_answer", fake_stream)
    monkeypatch.setattr(generation, "generate_answer", fake_generate)
    return state


# ---------------------------------------------------------------------------
# HTTP client for integration tests
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client(session_factory, clean_db) -> AsyncIterator[AsyncClient]:
    """ASGI client bound straight to the FastAPI app — no real port needed.
    Production code reads SessionLocal from app.db.base, so patching it here
    makes request handlers use this loop-local factory too."""
    db_base.SessionLocal = session_factory
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

