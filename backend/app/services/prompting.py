"""Prompt assembly for the RAG answer step.

Builds the system + user messages exactly as specified in docs/05 Part B
Step 3: numbered excerpts tagged with (filename, page), plus the user question.

The system prompt is fixed by the spec (study assistant, answer ONLY from
excerpts, ground in citations, don't guess). Keep it stable — prompt churn
mid-build makes retrieval-quality tuning impossible to reason about.
"""

from __future__ import annotations

from app.services.retrieval import RetrievedChunk

SYSTEM_PROMPT = """You are a study assistant. Answer the user's question using ONLY the document excerpts provided below.
If the excerpts don't contain enough information to answer, say so directly — do not guess or use outside knowledge.

GROUNDING RULES (critical for faithfulness):
- Support EVERY factual claim with a [n] citation to the excerpt it comes from.
- If you cannot cite a source for a claim, DO NOT make that claim.
- Never combine information from two excerpts into a new claim that neither
  excerpt explicitly states. Synthesis is not allowed — only report what an
  excerpt directly says.
- When unsure whether a fact is supported, omit it rather than risk an
  unsupported claim.

CITATION FORMAT: each excerpt is numbered [1], [2], .... Cite inline using a
plain bracketed number such as [1] or [1][3]. Use ONLY a plain number in
brackets — never add line ranges, dagger symbols, or other decoration. The
frontend turns [n] into a hoverable citation, so the format must be exactly [n].

Answer naturally and accurately. You may use markdown (bold, italics, bullet
lists) for readability."""


def build_excerpt_label(idx: int, chunk: RetrievedChunk) -> str:
    """Format one excerpt's source tag, e.g. ``(Physics.pdf, p. 45)``.

    Page number may be None (unattributable chunk) — show 'p. unknown' so the
    model and the user both know that chunk has no page anchor.
    """
    page = f"p. {chunk.page_number}" if chunk.page_number is not None else "p. unknown"
    return f"[{idx}] ({chunk.filename}, {page})"


def build_user_message(question: str, chunks: list[RetrievedChunk]) -> str:
    """Assemble the user message: numbered excerpts + the question.

    Numbering lets the model implicitly anchor its answer to specific sources,
    but the ACTUAL citations sent to the frontend come from which chunks were
    retrieved (see chat.py), NOT from parsing the model's prose — citation
    correctness never depends on the LLM self-reporting what it used.
    """
    if not chunks:
        # Defensive: callers should decline-to-answer before reaching here, but
        # if they don't, give the model nothing to fabricate from.
        return f"USER QUESTION:\n{question}"

    lines = ["DOCUMENT EXCERPTS:"]
    for i, chunk in enumerate(chunks, start=1):
        label = build_excerpt_label(i, chunk)
        # Quote the excerpt; collapse internal whitespace so each excerpt is one
        # scannable line in the prompt.
        excerpt = " ".join(chunk.content.split())
        lines.append(f'{label}: "{excerpt}"')
    lines.append("")
    lines.append("USER QUESTION:")
    lines.append(question)
    return "\n".join(lines)


def build_messages(
    question: str, chunks: list[RetrievedChunk]
) -> list[dict[str, str]]:
    """Return the OpenAI-style messages list for the chat completion call."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(question, chunks)},
    ]
