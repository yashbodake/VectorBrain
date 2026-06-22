"""Add quiz_questions + quiz_attempts tables (quiz generation feature).

Revision ID: 0004_quiz_tables
Revises: 0003_chat_sessions
Create Date: 2026-06-22

Quiz generation: generate multiple-choice questions from a document's chunks,
store them, let the student take the quiz, track attempts/scores. Active recall
is the #1 evidence-based study technique.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_quiz_tables"
down_revision: Union[str, None] = "0003_chat_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quiz_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        # JSONB: ["option A", "option B", "option C", "option D"]
        sa.Column("options", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("correct_index", sa.Integer(), nullable=False),  # 0-based
        sa.Column("explanation", sa.Text(), nullable=True),  # why the answer is correct
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("quiz_questions_document_id_idx", "quiz_questions", ["document_id"])

    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "quiz_question_id",
            sa.Integer(),
            sa.ForeignKey("quiz_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selected_index", sa.Integer(), nullable=False),  # user's choice
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "quiz_attempts_quiz_question_id_idx", "quiz_attempts", ["quiz_question_id"]
    )


def downgrade() -> None:
    op.drop_index("quiz_attempts_quiz_question_id_idx", table_name="quiz_attempts")
    op.drop_table("quiz_attempts")
    op.drop_index("quiz_questions_document_id_idx", table_name="quiz_questions")
    op.drop_table("quiz_questions")
