"""Initial schema: documents, chunks, pgvector extension, indexes.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-19

Hand-written (rather than autogenerate) so the migration reproduces the schema
from docs/03-database-schema.md exactly, including the ``CREATE EXTENSION vector``
step that autogenerate can't infer. Keep this file as the source of truth; if
the ORM models in app/db/models.py change, add a *new* migration rather than
editing this one.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector must be installed in the database before we can use the
    # vector type. The pgvector/pgvector image ships it; this just enables it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- Indexes (match docs/03 verbatim) ---
    # HNSW for the vector column (better recall/speed than IVFFlat at this
    # scale; pgvector >= 0.5.0 — our image satisfies that).
    op.create_index(
        "chunks_embedding_idx",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("chunks_document_id_idx", "chunks", ["document_id"])
    op.create_index("documents_status_idx", "documents", ["status"])


def downgrade() -> None:
    op.drop_index("documents_status_idx", table_name="documents")
    op.drop_index("chunks_document_id_idx", table_name="chunks")
    op.drop_index("chunks_embedding_idx", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("documents")
    # Leave the vector extension in place on downgrade — other things in the
    # database may depend on it, and DROP EXTENSION is not safely reversible.
