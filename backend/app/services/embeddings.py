"""Embedding model: ``BAAI/bge-small-en-v1.5`` via ``sentence-transformers``.

The model is heavy (~130 MB) and slow to cold-start (~2-4 s to load weights into
memory), so we instantiate it exactly **once** per process (module-level
singleton) and reuse it for both ingestion (embedding chunks) and query time
(embedding the user's question). This symmetry is required for cosine similarity
to be meaningful — see docs/05-rag-pipeline-spec.md.

All inference runs in a worker thread (``anyio.to_thread`` / ``run_in_executor``)
from the callers; ``SentenceTransformer.encode`` is a blocking, CPU-bound call
and must never run inline on the asyncio event loop (coding-conventions.md).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import settings

if TYPE_CHECKING:
    from numpy.typing import NDArray


# BGE asymmetric retrieval: the *query* side takes this instruction prefix;
# the *document/chunk* side takes NO prefix. See docs/05 Part B Step 1.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Return the process-wide SentenceTransformer singleton.

    ``lru_cache`` keeps it to one instance even if called from many places.
    Loaded on first use (not at import) so importing the module is cheap and
    the app boots fast — the heavy load only happens when ingestion/query
    actually needs it.

    Device comes from ``settings.torch_device`` ('cuda' when available +
    configured, else 'cpu'). Moving the model to GPU makes batch embedding
    materially faster on large ingestions — see docs/08-gpu-and-ocr-plan.md.
    """
    return SentenceTransformer(settings.EMBEDDING_MODEL, device=settings.torch_device)


def encode_chunks(texts: list[str]) -> "NDArray[np.float32]":
    """Embed a batch of chunk texts. NO prefix (document side).

    Returns a ``(len(texts), 384)`` float32 array of L2-normalized vectors.
    Raises ``ValueError`` if the output dimension drifts from 384 — the
    ``vector(384)`` DB column is fixed at creation time and a mismatch would
    silently corrupt the table, so we fail loudly instead (docs/05 Step 3).
    """
    if not texts:
        return np.empty((0, settings.EMBEDDING_DIM), dtype=np.float32)

    model = get_model()
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,  # required: <=> cosine assumes this (docs/05)
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    _assert_dim(embeddings)
    return embeddings.astype(np.float32)


def encode_query(question: str) -> "NDArray[np.float32]":
    """Embed a single user question WITH the BGE query prefix.

    This is the one place the embedding call differs from the chunk side
    (asymmetric BGE retrieval). Returns a ``(384,)`` float32 normalized vector.
    """
    model = get_model()
    embedding = model.encode(
        QUERY_PREFIX + question,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    _assert_dim(embedding)
    return embedding.astype(np.float32)


def _assert_dim(arr: "NDArray[np.float32]") -> None:
    """Guard the 384-dim invariant. See docs/05 Step 3 + docs/03 schema."""
    actual = arr.shape[-1]
    if actual != settings.EMBEDDING_DIM:
        raise ValueError(
            f"Embedding dimension mismatch: model produced {actual}-dim vectors "
            f"but the schema's chunks.embedding column is fixed at "
            f"vector({settings.EMBEDDING_DIM}). Either the wrong embedding model "
            f"is loaded or EMBEDDING_DIM is misconfigured. Refusing to write "
            f"to avoid corrupting the table."
        )
