"""SQLAlchemy ORM models mirroring ``docs/03-database-schema.md``.

The two tables here are the source of truth for Alembic's autogenerate. Keep
them in sync with the schema doc — if you change one, regenerate the migration
(see docs/03 "Migrations").
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models and Alembic's env.py."""


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # one of: uploaded | processing | ready | failed (Phase 1 only writes 'uploaded')
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DateTime(timezone=True): the underlying columns are TIMESTAMPTZ (see
    # docs/03 + the migration), so the ORM MUST declare them timezone-aware —
    # otherwise asyncpg sends tz-aware Python datetimes through a naive column
    # type and raises "can't subtract offset-naive and offset-aware datetimes".
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",  # mirrors the SQL ON DELETE CASCADE
        passive_deletes=True,
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.EMBEDDING_DIM),  # vector(384), fixed per schema
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


# ---------------------------------------------------------------------------
# Indexes — match docs/03 verbatim. HNSW needs pgvector >= 0.5.0; the pgvector
# image we use ships a recent version, so HNSW is the right choice (no separate
# ANALYZE step, better recall/speed at this dataset size).
# ---------------------------------------------------------------------------
Index("chunks_document_id_idx", Chunk.document_id)
Index("documents_status_idx", Document.status)
Index(
    "chunks_embedding_idx",
    Chunk.embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"},
)


# ---------------------------------------------------------------------------
# Chat history (session memory) — see docs/superpowers/specs/2026-06-21-
# session-memory-design.md. Stores every message so the conversation survives
# refresh / browser clear / backend restart.
# ---------------------------------------------------------------------------
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # JSONB: [{filename, page_number, excerpt}, ...] for assistant messages; NULL for user.
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
