"""Pydantic schemas for the chat resource."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    """``POST /api/chat`` body. Just the question — no document scoping in Lite
    scope (search runs across all ready documents, see docs/04)."""

    question: str = Field(..., min_length=1, description="The user's question.")

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v


class Citation(BaseModel):
    filename: str
    page_number: int | None = None
