"""Add thread_curation ledger table for banked host-LLM inbox judgments.

Stores one row per ``(user_id, thread_id)`` holding the host-LLM's curation
verdict (bucket, importance, encrypted summary / reasoning, suggested action,
state) plus a Gmail ``historyId`` freshness watermark. Summaries and reasoning
are encrypted at rest with a ``key_id`` mirroring ``google_tokens`` so keys can
rotate. See ``db/models/thread_curation.py``.

Revision ID: 010
Revises: 009
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "thread_curation",
        sa.Column("user_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("thread_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=True),
        sa.Column("importance", sa.Float(), nullable=True),
        sa.Column("summary_enc", sa.LargeBinary(), nullable=True),
        sa.Column("reasoning_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "key_id",
            sa.String(length=32),
            nullable=False,
            server_default="v1",
        ),
        sa.Column(
            "suggested_action",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
        sa.Column("draft_id", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="curated",
        ),
        sa.Column("curated_history_id", sa.String(length=64), nullable=True),
        sa.Column("curator_version", sa.String(length=64), nullable=True),
        sa.Column(
            "curated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_thread_curation_user_state",
        "thread_curation",
        ["user_id", "state"],
    )
    op.create_index(
        "ix_thread_curation_user_bucket",
        "thread_curation",
        ["user_id", "bucket"],
    )


def downgrade() -> None:
    op.drop_index("ix_thread_curation_user_bucket", table_name="thread_curation")
    op.drop_index("ix_thread_curation_user_state", table_name="thread_curation")
    op.drop_table("thread_curation")
