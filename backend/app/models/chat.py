"""Pydantic schemas for the chat resource."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    """``POST /api/chat`` body.

    ``question`` is required. ``document_ids`` is optional document scoping:
    when provided (non-empty), retrieval is restricted to those documents;
    when omitted/empty, all ``ready`` documents are searched (the original
    behavior). This is a spec extension over docs/04 (which defined only
    ``question``) — see PROGRESS.md "Spec Deviations".
    """

    question: str = Field(..., min_length=1, description="The user's question.")
    document_ids: list[int] | None = Field(
        default=None,
        description="Optional list of document ids to restrict retrieval to. "
        "Omit or send empty/null to search all ready documents.",
    )

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v

    @field_validator("document_ids")
    @classmethod
    def _normalize_doc_ids(cls, v: list[int] | None) -> list[int] | None:
        # Drop duplicates / empties; treat an empty list as "all docs" (None)
        # so the caller doesn't need to distinguish [] from null.
        if not v:
            return None
        # De-dup while preserving order.
        seen: set[int] = set()
        unique = [x for x in v if x not in seen and not seen.add(x)]  # type: ignore[func-returns-value]
        return unique or None


class Citation(BaseModel):
    filename: str
    page_number: int | None = None


# ---------------------------------------------------------------------------
# Chat history (session memory) — see docs/superpowers/specs/2026-06-21-
# session-memory-design.md
# ---------------------------------------------------------------------------
class ChatMessageCreate(BaseModel):
    """One message to save. Used in the batch POST."""

    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(...)
    citations: list[dict] | None = Field(default=None)


class ChatMessageBatch(BaseModel):
    """POST /api/chat/history body: a batch of messages to persist."""

    messages: list[ChatMessageCreate]
    session_id: int = Field(default=1, description="Which session to save to.")


class ChatMessageRead(BaseModel):
    """One message as returned by GET /api/chat/history."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    role: str
    content: str
    citations: list[dict] | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Chat sessions (session travel) — multiple independent threads
# ---------------------------------------------------------------------------
class ChatSessionRead(BaseModel):
    """One session as returned by GET /api/chat/sessions."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime | None = None
    message_count: int = 0


class ChatSessionCreate(BaseModel):
    """POST /api/chat/sessions body (title is optional — auto-titled later)."""

    title: str = Field(default="New session")
