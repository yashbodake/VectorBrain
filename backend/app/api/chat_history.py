"""Chat history router — persistent session memory.

Three CRUD endpoints for the chat_messages table. The streaming chat
(POST /api/chat) stays untouched; saving happens in the frontend store AFTER
the stream completes, via POST /api/chat/history.

See docs/superpowers/specs/2026-06-21-session-memory-design.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import ChatMessage
from app.models.chat import ChatMessageBatch, ChatMessageRead

router = APIRouter(prefix="/chat/history", tags=["chat-history"])


@router.get(
    "",
    summary="Load all chat messages (oldest first)",
)
async def get_history(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return every saved message ordered oldest-first. The frontend store
    populates from this on mount, restoring the full conversation including
    citations (for hover popups + chips)."""
    stmt = select(ChatMessage).order_by(ChatMessage.created_at.asc())
    result = await session.execute(stmt)
    msgs = result.scalars().all()
    return {"messages": [ChatMessageRead.model_validate(m) for m in msgs]}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Save a batch of messages (after a Q&A turn)",
)
async def save_history(
    payload: ChatMessageBatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Persist a batch of messages (typically: one user + one assistant with
    citations) after the streaming chat completes. Fire-and-forget from the
    frontend's perspective — the answer was already shown."""
    rows = [
        ChatMessage(
            role=m.role,
            content=m.content,
            citations=m.citations,
        )
        for m in payload.messages
    ]
    session.add_all(rows)
    await session.commit()
    return {"saved": len(rows)}


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear all chat history",
)
async def clear_history(
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Wipe all messages. Used by the 'Clear chat' button."""
    stmt = select(ChatMessage)
    result = await session.execute(stmt)
    for msg in result.scalars().all():
        await session.delete(msg)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
