"""LLM generation via Cerebras (OpenAI-compatible API).

Uses the official ``openai`` SDK pointed at ``CEREBRAS_BASE_URL``. Cerebras's
API is a drop-in for OpenAI's, so the integration is identical except for the
base URL and model string. Originally the spec (docs/05) used Groq — see
PROGRESS.md "Spec Deviations".

The client is constructed once per process (it maintains an HTTP connection
pool). Streaming is the primary path for /api/chat (tokens forwarded as SSE);
a non-streaming helper exists for tests and potential non-chat callers.

Errors are surfaced as :class:`GenerationError` so the chat layer can translate
them into a 503 (provider unreachable) distinct from "no relevant chunks".
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from openai import OpenAI
from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError

from app.core.config import settings


class GenerationError(Exception):
    """Raised when the LLM provider call fails (network/auth/rate/availability).

    The chat layer maps this to HTTP 503 — distinct from 'no relevant chunks'
    (which is a normal 200 + canned reply), because the user's remedy differs:
    retry later vs. rephrase the question (docs/04 /api/chat error semantics).
    """


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    """Process-wide OpenAI client pointed at Cerebras. Cached so the HTTP
    connection pool is reused across requests."""
    return OpenAI(
        api_key=settings.CEREBRAS_API_KEY,
        base_url=settings.CEREBRAS_BASE_URL,
    )


def stream_answer(messages: list[dict[str, str]]) -> Iterator[str]:
    """Stream the answer token-by-token. Yields text deltas as they arrive.

    Synchronous iterator — the OpenAI SDK's streaming is sync-first. The chat
    endpoint runs this whole call inside a worker thread (via anyio.to_thread
    -> a thread that pumps the iterator into an async queue) so it never
    blocks the event loop. See api/chat.py for the bridging.

    Raises :class:`GenerationError` on any provider failure so callers can emit
    an SSE ``error`` event instead of crashing mid-stream.
    """
    try:
        stream = _client().chat.completions.create(
            model=settings.CEREBRAS_MODEL,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            # A streaming chunk may carry a None delta (e.g. role-only first
            # chunk, or usage chunks at the end) — skip those.
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
    except (APIConnectionError, APITimeoutError) as exc:
        raise GenerationError(
            f"Could not reach the LLM provider ({type(exc).__name__}). "
            f"Please retry in a moment."
        ) from exc
    except RateLimitError as exc:
        raise GenerationError(
            "The LLM provider is rate-limiting requests. Please retry shortly."
        ) from exc
    except APIError as exc:
        raise GenerationError(f"LLM provider error: {exc.body or exc}") from exc


def generate_answer(messages: list[dict[str, str]]) -> str:
    """Non-streaming completion. Used by tests / a possible future non-chat
    caller. Not used by /api/chat (which streams)."""
    try:
        resp = _client().chat.completions.create(
            model=settings.CEREBRAS_MODEL,
            messages=messages,
            stream=False,
        )
        return resp.choices[0].message.content or ""
    except (APIConnectionError, APITimeoutError) as exc:
        raise GenerationError(
            f"Could not reach the LLM provider ({type(exc).__name__})."
        ) from exc
    except RateLimitError as exc:
        raise GenerationError(
            "The LLM provider is rate-limiting requests."
        ) from exc
    except APIError as exc:
        raise GenerationError(f"LLM provider error: {exc.body or exc}") from exc


def health_check() -> bool:
    """Cheap reachability probe for /health wiring or startup checks. Returns
    False (not raises) so callers can use it as a boolean without try/except."""
    try:
        _client().models.list()
        return True
    except Exception:  # noqa: BLE001 — probe, never raise
        return False


# Re-export for callers that want the raw type annotation.
__all__ = [
    "GenerationError",
    "stream_answer",
    "generate_answer",
    "health_check",
]
