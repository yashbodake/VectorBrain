"""Add document_summaries table (chapter summaries feature).

Revision ID: 0005_summaries
Revises: 0004_quiz_tables
Create Date: 2026-06-22

Caches per-chapter summaries for a document. Summaries are expensive to
generate (LLM calls) so we cache them — generate once, review many times.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_summaries"
down_revision: Union[str, None] = "0004_quiz_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_index", sa.Integer(), nullable=False),  # ordering
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "document_summaries_document_id_idx", "document_summaries", ["document_id"]
    )


def downgrade() -> None:
    op.drop_index("document_summaries_document_id_idx", table_name="document_summaries")
    op.drop_table("document_summaries")
