"""Add chat_sessions table + session_id on chat_messages (session travel).

Revision ID: 0003_chat_sessions
Revises: 0002_chat_messages
Create Date: 2026-06-22

Lets the user have multiple independent study sessions (like ChatGPT threads).
Each chat_messages row belongs to exactly one session via a FK. Deleting a
session cascades to its messages. The first session is auto-created for
backward compatibility with the existing single-thread history.

See docs/superpowers/specs/2026-06-21-session-memory-design.md (upgrade path).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_chat_sessions"
down_revision: Union[str, None] = "0002_chat_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the sessions table.
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False, server_default="New session"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # 2. Insert a default session so existing chat_messages (if any) have a home.
    op.execute("INSERT INTO chat_sessions (title) VALUES ('New session')")

    # 3. Add session_id FK to chat_messages, defaulting to the first session.
    op.add_column(
        "chat_messages",
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_index("chat_messages_session_id_idx", "chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("chat_messages_session_id_idx", table_name="chat_messages")
    op.drop_column("chat_messages", "session_id")
    op.drop_table("chat_sessions")
