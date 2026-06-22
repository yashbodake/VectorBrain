"""Pydantic schemas for the quiz resource."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class QuizQuestionRead(BaseModel):
    """One quiz question as returned by GET /api/documents/{id}/quiz.
    NOTE: correct_index and explanation are NOT included here — they're only
    revealed after the student answers (see QuizAnswerResponse)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    options: list[str]


class QuizAnswerRequest(BaseModel):
    """POST /api/quiz/{id}/answer body."""
    selected_index: int = Field(..., ge=0, le=3)


class QuizAnswerResponse(BaseModel):
    """Result of answering one quiz question. Reveals the correct answer + explanation."""
    correct: bool
    correct_index: int
    explanation: str | None = None


class QuizScore(BaseModel):
    """Aggregate score after completing a quiz."""
    total: int
    correct: int
