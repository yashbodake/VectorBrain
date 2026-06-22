"""Quiz API router — generate, fetch, and answer quiz questions.

Endpoints:
- POST   /api/documents/{id}/quiz  — generate N questions from a doc's chunks
- GET    /api/documents/{id}/quiz  — fetch existing questions for a doc
- POST   /api/quiz/{id}/answer     — answer one question (graded instantly)
- GET    /api/quiz/score?document_id=N — aggregate score for a doc's attempts

Generation is a blocking LLM call — run in a worker thread via anyio.
"""

from __future__ import annotations

import logging

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Chunk, Document, QuizAttempt, QuizQuestion
from app.models.quiz import (
    QuizAnswerRequest,
    QuizAnswerResponse,
    QuizQuestionRead,
    QuizScore,
)
from app.services import quiz as quiz_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quiz"])


@router.post(
    "/documents/{document_id}/quiz",
    status_code=status.HTTP_201_CREATED,
    summary="Generate quiz questions from a document",
)
async def generate_quiz(
    document_id: int,
    n: int = Query(default=5, ge=1, le=10),
    db: AsyncSession = Depends(get_session),
) -> list[QuizQuestionRead]:
    """Generate N multiple-choice questions from the document's chunks via the
    LLM, store them, and return them (without answers — answers are revealed
    on submission). Replaces any existing questions for this document."""
    # 1. Check the doc exists + is ready.
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"No document {document_id}.")
    if doc.status != "ready":
        raise HTTPException(status_code=400, detail="Document is not ready yet.")

    # 2. Fetch some chunks for context (top 6 by chunk_index for diversity).
    chunk_stmt = (
        select(Chunk.content)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index.asc())
        .limit(6)
    )
    chunk_result = await db.execute(chunk_stmt)
    contents = [r[0] for r in chunk_result.all()]
    if not contents:
        raise HTTPException(status_code=400, detail="Document has no chunks.")

    # 3. Generate via LLM (blocking — worker thread).
    try:
        questions = await anyio.to_thread.run_sync(
            lambda: quiz_service.generate_quiz(contents, n_questions=n)
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 4. Delete old questions for this doc (replace, not append).
    old_stmt = select(QuizQuestion).where(QuizQuestion.document_id == document_id)
    old_result = await db.execute(old_stmt)
    for old in old_result.scalars().all():
        await db.delete(old)

    # 5. Store the new questions.
    rows = []
    for q in questions:
        row = QuizQuestion(
            document_id=document_id,
            question=q.question,
            options=q.options,
            correct_index=q.correct_index,
            explanation=q.explanation,
        )
        db.add(row)
        rows.append(row)

    await db.commit()
    for row in rows:
        await db.refresh(row)

    # Return WITHOUT correct_index/explanation (student hasn't answered yet).
    return [QuizQuestionRead(id=r.id, question=r.question, options=r.options) for r in rows]


@router.get(
    "/documents/{document_id}/quiz",
    summary="Fetch existing quiz questions for a document",
)
async def get_quiz(
    document_id: int,
    db: AsyncSession = Depends(get_session),
) -> list[QuizQuestionRead]:
    """Return all stored questions for a document (without answers)."""
    stmt = (
        select(QuizQuestion)
        .where(QuizQuestion.document_id == document_id)
        .order_by(QuizQuestion.id.asc())
    )
    result = await db.execute(stmt)
    return [
        QuizQuestionRead(id=q.id, question=q.question, options=q.options)
        for q in result.scalars().all()
    ]


@router.post(
    "/quiz/{question_id}/answer",
    summary="Answer one quiz question (graded instantly)",
)
async def answer_question(
    question_id: int,
    payload: QuizAnswerRequest,
    db: AsyncSession = Depends(get_session),
) -> QuizAnswerResponse:
    """Grade the student's answer and reveal the correct answer + explanation.
    Also records the attempt for score tracking."""
    q = await db.get(QuizQuestion, question_id)
    if q is None:
        raise HTTPException(status_code=404, detail=f"No quiz question {question_id}.")

    is_correct = payload.selected_index == q.correct_index

    # Record the attempt.
    db.add(
        QuizAttempt(
            quiz_question_id=question_id,
            selected_index=payload.selected_index,
            correct=is_correct,
        )
    )
    await db.commit()

    return QuizAnswerResponse(
        correct=is_correct,
        correct_index=q.correct_index,
        explanation=q.explanation,
    )


@router.get(
    "/quiz/score",
    summary="Aggregate quiz score for a document",
)
async def get_score(
    document_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
) -> QuizScore:
    """Total questions answered vs correct for a document."""
    # COUNT(*) for total, COUNT(*) FILTER (WHERE correct) for correct count.
    # Can't SUM(booleans) directly in Postgres — use FILTER instead.
    stmt = (
        select(
            func.count(QuizAttempt.id).label("total"),
            func.count(QuizAttempt.id).filter(QuizAttempt.correct).label("correct"),
        )
        .join(QuizQuestion, QuizQuestion.id == QuizAttempt.quiz_question_id)
        .where(QuizQuestion.document_id == document_id)
    )
    result = await db.execute(stmt)
    total, correct_sum = result.one()
    return QuizScore(total=total or 0, correct=int(correct_sum or 0))
