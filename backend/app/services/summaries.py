"""Chapter summarization service.

Groups a document's chunks into sections (by page ranges), sends each to
Cerebras for a concise summary, and returns the cached results. Summaries are
expensive (LLM calls) so they're cached in the DB — generate once, review
many times.
"""

from __future__ import annotations

import logging

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """You are a study assistant. Summarize the provided document excerpts in 2-3 sentences.
Focus on the key ideas, concepts, and takeaways. Be concise but informative.
Return ONLY the summary text (no markdown headers, no preamble)."""

_SUMMARY_USER = """Summarize these excerpts from pages {start}-{end}:

{context}"""


def summarize_section(content: str, page_start: int, page_end: int) -> str:
    """Summarize one section's content via the LLM. Blocking call — caller
    runs it in a worker thread."""
    client = OpenAI(
        api_key=settings.CEREBRAS_API_KEY,
        base_url=settings.CEREBRAS_BASE_URL,
    )
    user_msg = _SUMMARY_USER.format(
        start=page_start, end=page_end, context=content[:2000]
    )
    try:
        resp = client.chat.completions.create(
            model=settings.CEREBRAS_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            stream=False,
            temperature=0.3,  # factual, low creativity
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc


def make_section_title(content: str, page_start: int, page_end: int) -> str:
    """Generate a short title for a section. Cheaper call — just uses the
    first ~100 chars of the content as a heuristic title. No LLM call needed."""
    # Take the first meaningful line as the title, truncated.
    first_line = content.strip().split("\n")[0].strip()
    if len(first_line) > 60:
        first_line = first_line[:57] + "…"
    if not first_line:
        first_line = f"Pages {page_start}-{page_end}"
    return f"{first_line} (p. {page_start}-{page_end})"
