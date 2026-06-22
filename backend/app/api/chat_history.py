"""Chat history router — persistent session memory + session travel.

History endpoints (scoped by session):
- GET /api/chat/history?session_id=N — load messages for a session
- POST /api/chat/history — save a batch to a session
- DELETE /api/chat/history?session_id=N — clear one session's messages

Session endpoints (CRUD):
- GET /api/chat/sessions — list all sessions
- POST /api/chat/sessions — create a new session
- PATCH /api/chat/sessions/{id} — rename (e.g. auto-title from first message)
- DELETE /api/chat/sessions/{id} — delete a session (cascades to messages)

See docs/superpowers/specs/2026-06-21-session-memory-design.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import ChatMessage, ChatSession
from app.models.chat import (
    ChatMessageBatch,
    ChatMessageRead,
    ChatSessionCreate,
    ChatSessionRead,
)

# --- History router (messages, scoped by session) ---------------------------
history_router = APIRouter(prefix="/chat/history", tags=["chat-history"])


@history_router.get("", summary="Load messages for a session (oldest first)")
async def get_history(
    session_id: int = Query(default=1, description="Which session to load."),
    db: AsyncSession = Depends(get_session),
) -> dict:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(stmt)
    msgs = result.scalars().all()
    return {"messages": [ChatMessageRead.model_validate(m) for m in msgs]}


@history_router.post("", status_code=status.HTTP_201_CREATED, summary="Save a batch")
async def save_history(
    payload: ChatMessageBatch,
    db: AsyncSession = Depends(get_session),
) -> dict:
    rows = [
        ChatMessage(
            session_id=payload.session_id,
            role=m.role,
            content=m.content,
            citations=m.citations,
        )
        for m in payload.messages
    ]
    db.add_all(rows)
    # Stamp the session's updated_at.
    sess = await db.get(ChatSession, payload.session_id)
    if sess is not None:
        sess.updated_at = func.now()
    await db.commit()
    return {"saved": len(rows)}


@history_router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Clear a session")
async def clear_history(
    session_id: int = Query(default=1),
    db: AsyncSession = Depends(get_session),
) -> Response:
    stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
    result = await db.execute(stmt)
    for msg in result.scalars().all():
        await db.delete(msg)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Sessions router (CRUD) -------------------------------------------------
sessions_router = APIRouter(prefix="/chat/sessions", tags=["chat-sessions"])


@sessions_router.get("", summary="List all sessions (newest first)")
async def list_sessions(db: AsyncSession = Depends(get_session)) -> dict:
    # Left-join messages to get a count per session in one query.
    stmt = (
        select(
            ChatSession,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.created_at.desc())
    )
    result = await db.execute(stmt)
    out = []
    for sess, count in result.all():
        out.append(
            ChatSessionRead(
                id=sess.id,
                title=sess.title,
                created_at=sess.created_at,
                updated_at=sess.updated_at,
                message_count=count,
            )
        )
    return {"sessions": out}


@sessions_router.post("", status_code=status.HTTP_201_CREATED, summary="Create a session")
async def create_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_session),
) -> ChatSessionRead:
    sess = ChatSession(title=payload.title)
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return ChatSessionRead(
        id=sess.id, title=sess.title, created_at=sess.created_at, message_count=0
    )


@sessions_router.patch("/{session_id}", summary="Rename / update a session")
async def update_session(
    session_id: int,
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_session),
) -> ChatSessionRead:
    sess = await db.get(ChatSession, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}.")
    sess.title = payload.title
    await db.commit()
    await db.refresh(sess)
    return ChatSessionRead(id=sess.id, title=sess.title, created_at=sess.created_at)


@sessions_router.delete(
    "/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a session"
)
async def delete_session(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> Response:
    sess = await db.get(ChatSession, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id}.")
    await db.delete(sess)  # FK CASCADE drops messages
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
