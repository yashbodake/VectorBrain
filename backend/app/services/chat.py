"""Chat pipeline orchestrator: the decision logic for ``POST /api/chat``.

Encodes the three branches from docs/04 (the /api/chat behavior spec) so the
SSE layer (api/chat.py) is just rendering, and this module stays testable
without any HTTP:

1. No ``ready`` documents  -> stream a canned "nothing ready yet" message,
   empty citations, NO LLM call.
2. Ready docs exist, but nothing clears the relevance threshold  -> stream a
   "couldn't find anything relevant" message, empty citations, NO LLM call.
3. Relevant chunks found  -> embed question, retrieve, build prompt, stream the
   LLM answer token-by-token, then emit the de-duplicated citations.

This module yields :class:`ChatEvent` s; the API layer converts them to SSE.
Citations come from WHICH CHUNKS WERE RETRIEVED, not from parsing the model's
prose — citation correctness never depends on the LLM self-reporting sources
(docs/05 Part B Step 3, Step 5).
"""

from __future__ import annotations

import anyio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import embeddings, generation, prompting, retrieval


@dataclass(slots=True)
class TokenEvent:
    text: str


@dataclass(slots=True)
class DoneEvent:
    citations: list[dict[str, object]]  # [{"filename":..., "page_number":...}]
    # True when the answer was a canned decline (no LLM was called). Lets the
    # frontend distinguish "model said it couldn't find X" from "retrieval said
    # so" — not strictly required by the spec but cheap to expose.
    declined: bool


@dataclass(slots=True)
class ErrorEvent:
    message: str


ChatEvent = TokenEvent | DoneEvent | ErrorEvent


# Canned decline messages (docs/04). Returned as tokens so the frontend renders
# them identically to a real streamed answer.
_NO_DOCS_MSG = (
    "No documents are ready yet. Please upload some PDFs and wait for them to "
    "finish processing before asking questions."
)
_NO_MATCH_MSG = (
    "I couldn't find anything relevant in your uploaded documents for that. "
    "Try rephrasing, or upload documents that cover the topic."
)


async def stream_chat(
    session: AsyncSession, question: str, document_ids: list[int] | None = None
) -> AsyncIterator[ChatEvent]:
    """Run the full chat pipeline and yield events for the API layer to render.

    ``document_ids`` optionally scopes retrieval to a subset of documents
    (Feature 3). ``None``/empty means all ready documents.

    Never raises for expected conditions (no docs / no match) — those are
    normal ``DoneEvent``s with canned token messages. Provider failures come
    back as ``ErrorEvent``s so the SSE layer can emit an ``error`` SSE event.
    """
    ready_count = await retrieval.count_ready_documents(session, document_ids)
    if ready_count == 0:
        # Branch 1: nothing to search. Decline without an LLM call.
        async for ev in _stream_decline(_NO_DOCS_MSG):
            yield ev
        return

    # Embed the question (BGE query prefix applied inside encode_query).
    query_embedding = await anyio.to_thread.run_sync(
        embeddings.encode_query, question
    )
    chunks = await retrieval.search_chunks(
        session, query_embedding.tolist(), document_ids=document_ids
    )
    if not chunks:
        # Branch 2: nothing relevant above the threshold. Decline, skip the LLM
        # entirely (docs/04 — don't let it hallucinate from empty context).
        async for ev in _stream_decline(_NO_MATCH_MSG):
            yield ev
        return

    # Branch 3: build the prompt and stream the real answer.
    messages = prompting.build_messages(question, chunks)
    citations = _dedupe_citations(chunks)

    try:
        # stream_answer is a SYNC iterator (OpenAI SDK). Pump it through a
        # thread so the event loop stays free. anyio.to_thread.run_sync handles
        # one call at a time; we bridge a sync iterator to async via a queue.
        async for token in _stream_from_sync(messages):
            yield TokenEvent(text=token)
    except generation.GenerationError as exc:
        yield ErrorEvent(message=str(exc))
        return

    yield DoneEvent(citations=citations, declined=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _stream_decline(message: str) -> AsyncIterator[ChatEvent]:
    """Emit a canned message as tokens + a DoneEvent with no citations.

    We emit the whole message as a single token rather than char-by-char: it's
    not a real LLM stream, and one token keeps the SSE traffic minimal. The
    frontend renders it the same way."""
    yield TokenEvent(text=message)
    yield DoneEvent(citations=[], declined=True)


async def _stream_from_sync(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """Bridge the SDK's synchronous streaming iterator to an async iterator.

    The OpenAI SDK's ``stream_answer`` is a sync iterator that blocks while
    waiting on the network. We run it in a worker thread and ferry each token
    back to the event loop via anyio's ``from_thread`` portal, so the loop
    stays free to flush SSE frames to the client as tokens arrive (streaming
    latency matters — see docs/02 "stream the response so it feels fast").

    Errors raised inside the worker (e.g. ``GenerationError``) are wrapped in a
    :class:`_Sentinel` and pushed through the same channel, then re-raised in
    the consumer so the caller's try/except can turn them into an ``ErrorEvent``.
    """
    send, recv = anyio.create_memory_object_stream(max_buffer_size=64)

    async def _producer() -> None:
        def _work() -> None:
            try:
                for tok in generation.stream_answer(messages):
                    anyio.from_thread.run(send.send, tok)
            except BaseException as exc:  # forward to the consumer
                anyio.from_thread.run(send.send, _Sentinel(exc))
            finally:
                anyio.from_thread.run(send.aclose)

        await anyio.to_thread.run_sync(_work)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_producer)
        async for item in recv:
            if isinstance(item, _Sentinel):
                raise item.exc
            yield item


@dataclass(slots=True)
class _Sentinel:
    """Wrapper for an exception pushed through the channel from the worker
    thread, so we can distinguish an exception from a normal string token."""

    exc: BaseException


def _dedupe_citations(
    chunks: list[retrieval.RetrievedChunk],
) -> list[dict[str, object]]:
    """De-duplicate (filename, page_number) across retrieved chunks, in
    retrieval order (most relevant first). docs/04 /api/chat done event.

    Each citation carries the highest-ranked chunk's ``excerpt`` so the frontend
    can show a NotebookLM-style hover popup with the actual source passage — not
    just "filename, page". Retrieval returns chunks in ascending-distance order,
    so the first time we see a (filename, page) is its most-relevant chunk."""
    seen: set[tuple[str, int | None]] = set()
    out: list[dict[str, object]] = []
    for c in chunks:
        key = (c.filename, c.page_number)
        if key in seen:
            continue
        seen.add(key)
        # Truncate the excerpt so the done-event JSON stays small but still
        # gives the user a meaningful passage on hover.
        excerpt = c.content.strip()
        if len(excerpt) > 320:
            excerpt = excerpt[:320].rstrip() + "…"
        out.append(
            {
                "filename": c.filename,
                "page_number": c.page_number,
                "excerpt": excerpt,
            }
        )
    return out


__all__ = [
    "TokenEvent",
    "DoneEvent",
    "ErrorEvent",
    "ChatEvent",
    "stream_chat",
]
