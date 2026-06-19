"""Application configuration.

Settings are loaded from a local ``.env`` file via ``pydantic-settings``.
Required variables are validated against ``.env.example`` at the repo root — if
a variable is missing or unparseable the app refuses to start, which is what we
want (fail fast on misconfiguration rather than failing later at runtime).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Repo root (backend/app/core/config.py -> ../../.. ). Used to anchor the
# storage path so it resolves the same whether uvicorn is launched from
# backend/ or the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Strongly-typed view of everything in ``.env``.

    Field names match the env var names 1:1 so ``.env.example`` stays the
    single source of truth for what's configurable.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    DATABASE_URL: str

    # --- LLM (Cerebras via OpenAI-compatible API) — used in Phase 3 ---
    CEREBRAS_API_KEY: str
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    CEREBRAS_MODEL: str = "gpt-oss-120b"

    # --- Embeddings ---
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384

    # --- Ingestion / Chunking ---
    CHUNK_MAX_TOKENS: int = 512
    TOP_K_RESULTS: int = 6
    RETRIEVAL_DISTANCE_THRESHOLD: float = 0.5

    # --- Uploads ---
    MAX_UPLOAD_SIZE_MB: int = 50
    DOCUMENT_STORAGE_PATH: str = "./storage/documents"

    # --- CORS ---
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # ------------------------------------------------------------------
    # Derived / validated values
    # ------------------------------------------------------------------

    @field_validator("EMBEDDING_DIM")
    @classmethod
    def _dim_must_be_384(cls, v: int) -> int:
        """The ``vector(384)`` column is fixed at creation time (see docs/03).
        Refuse to boot if the configured dim drifts, since the schema and the
        runtime model must stay in sync."""
        if v != 384:
            raise ValueError(
                f"EMBEDDING_DIM must be 384 (got {v}); the chunks.embedding "
                f"column is fixed at vector(384). See docs/03-database-schema.md."
            )
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def storage_path(self) -> Path:
        """Absolute path to on-disk PDF storage, created on access."""
        p = Path(self.DOCUMENT_STORAGE_PATH)
        if not p.is_absolute():
            p = _REPO_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def document_file_path(self, document_id: int) -> Path:
        """Where a given document's raw PDF lives on disk."""
        return self.storage_path / f"{document_id}.pdf"


settings = Settings()  # type: ignore[call-arg]
