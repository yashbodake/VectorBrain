"""Add chat_messages table for persistent chat history.

Revision ID: 0002_chat_messages
Revises: 0001_initial
Create Date: 2026-06-21

Stores every chat message (user + assistant) so the conversation survives
browser refresh, clearing browser data, and backend restarts. See
docs/superpowers/specs/2026-06-21-session-memory-design.md.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_chat_messages"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # JSONB for citations: [{filename, page_number, excerpt}, ...]. NULL for
        # user messages. JSONB (not JSON) so we can query/index it later if needed.
        sa.Column("citations", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("chat_messages_created_at_idx", "chat_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("chat_messages_created_at_idx", table_name="chat_messages")
    op.drop_table("chat_messages")
