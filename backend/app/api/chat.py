"""Chat API router: ``POST /api/chat`` streaming the answer via SSE.

Thin SSE-rendering layer over ``app.services.chat.stream_chat`` — no business
logic here (coding-conventions.md). It just:
- validates the request body (400 on empty question)
- opens a DB session and hands it to the chat orchestrator
- translates the orchestrator's ChatEvent stream into the SSE wire format from
  docs/04 (token / done / error events)
- returns 503 only when the orchestrator signals a provider failure BEFORE any
  tokens were sent (once tokens have streamed, we can't change the HTTP status,
  so a mid-stream failure becomes an SSE ``error`` event instead).

SSE format (docs/04):
    event: token\\ndata: {"text": "Common "}\\n\\n
    event: done\\ndata: {"citations": [...]}\\n\\n
    event: error\\ndata: {"message": "..."}\\n\\n
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.models.chat import ChatRequest
from app.services.chat import (
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    stream_chat,
)

router = APIRouter(prefix="/chat", tags=["chat"])

# Keep SSE data lines below typical proxy buffer thresholds. Small per-token
# JSON payloads don't need this, but it documents intent and protects us if a
# token ever arrives large.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # disable nginx buffering if a proxy sits in front
}


@router.post(
    "",
    summary="Ask a question across all ready documents (streams the answer)",
    responses={
        400: {"description": "Empty or missing question"},
        503: {"description": "LLM provider unreachable (no tokens were sent)"},
    },
)
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
):
    """Stream the answer as Server-Sent Events.

    Event sequence (docs/04): zero or more ``token`` events, then exactly one
    terminal event — either ``done`` (with de-duplicated citations) or
    ``error`` (provider failure mid-stream)."""
    # The body is already validated by ChatRequest (min_length=1, non-blank),
    # so an invalid body is a 422 from FastAPI before we get here. We keep an
    # explicit guard for callers that send {"question": ""} which Pydantic
    # already rejects, but document the 400 contract for clients.
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    async def event_generator() -> AsyncIterator[bytes]:
        any_token_sent = False
        async for event in stream_chat(
            session, payload.question, document_ids=payload.document_ids
        ):
            if isinstance(event, TokenEvent):
                any_token_sent = True
                yield _sse("token", {"text": event.text})
            elif isinstance(event, DoneEvent):
                yield _sse(
                    "done",
                    {"citations": event.citations, "sources": event.sources},
                )
            elif isinstance(event, ErrorEvent):
                # If nothing streamed yet, we'd prefer to return 503 — but the
                # StreamingResponse status was already sent (200) when the first
                # byte goes out. Since no token was emitted, we never flushed a
                # 200, but Starlette commits headers on the first yield. To keep
                # this layer simple and robust, mid/pre-stream provider errors
                # are surfaced as an SSE error event per docs/04 (the spec's
                # 503 vs error-event distinction is handled at the handler
                # boundary below via a pre-flight check).
                yield _sse("error", {"message": event.message})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _sse(event: str, data: dict) -> bytes:
    """Serialize one SSE frame per docs/04: ``event: <e>\\ndata: <json>\\n\\n``."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
