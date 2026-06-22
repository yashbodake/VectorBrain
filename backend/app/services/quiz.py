"""Quiz generation service — generates multiple-choice questions from a document.

Uses Cerebras (via the configured OpenAI-compatible client) to create N
multiple-choice questions from a document's chunks. The LLM is prompted to
return strict JSON so we can parse and store each question with its options,
correct answer, and explanation.

Generation is a blocking LLM call — callers run it in a worker thread (the API
handler uses anyio.to_thread.run_sync), never on the event loop.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# The system prompt asks for strict JSON so parsing is deterministic.
_QUIZ_SYSTEM = """You are a study tutor. Create multiple-choice questions from the provided document excerpts.
Each question must have exactly 4 options and one correct answer.
Return ONLY a JSON array (no markdown, no explanation). Each element:
{"question": "...", "options": ["A", "B", "C", "D"], "correct_index": 0, "explanation": "why"}
The correct_index is 0-based (0 = first option).
Make questions test understanding, not just memorization."""

_QUIZ_USER = """Create {n} multiple-choice questions from these excerpts:

{context}

Return ONLY the JSON array."""


@dataclass(slots=True)
class GeneratedQuestion:
    question: str
    options: list[str]
    correct_index: int
    explanation: str | None


def generate_quiz(content_chunks: list[str], n_questions: int = 5) -> list[GeneratedQuestion]:
    """Generate N multiple-choice questions from the given chunks.

    Returns a list of GeneratedQuestion. Raises on LLM failure or unparseable
    output (the API handler catches and returns a 503).
    """
    client = OpenAI(
        api_key=settings.CEREBRAS_API_KEY,
        base_url=settings.CEREBRAS_BASE_URL,
    )

    # Truncate each chunk to keep the prompt manageable (4 chunks * ~400 chars).
    chunks = "\n\n".join(c[:500] for c in content_chunks[:6])
    user_msg = _QUIZ_USER.format(n=n_questions, context=chunks)

    try:
        resp = client.chat.completions.create(
            model=settings.CEREBRAS_MODEL,
            messages=[
                {"role": "system", "content": _QUIZ_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            stream=False,
            temperature=0.7,  # some variety in questions
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc

    # Parse: the model may wrap in ```json ... ``` despite instructions. Strip it.
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned unparseable JSON: {raw[:200]}") from exc

    questions: list[GeneratedQuestion] = []
    for item in items:
        opts = item.get("options", [])
        if len(opts) != 4:
            logger.warning("Skipping question with %d options (expected 4)", len(opts))
            continue
        idx = item.get("correct_index", 0)
        if not isinstance(idx, int) or idx < 0 or idx > 3:
            idx = 0
        questions.append(
            GeneratedQuestion(
                question=item.get("question", ""),
                options=opts,
                correct_index=idx,
                explanation=item.get("explanation"),
            )
        )

    if not questions:
        raise RuntimeError("LLM produced no valid questions")
    return questions
