"""Add sent_drafts, the record of drafts this server sent.

Gmail deletes a draft on send and on discard alike, so the composer could not
tell a lost send reply from a discard. See ``db/models/sent_drafts.py``.

Revision ID: 017
Revises: 016
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sent_drafts",
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("draft_id", sa.String(255), primary_key=True),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sent_drafts_sent_at", "sent_drafts", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_sent_drafts_sent_at", table_name="sent_drafts")
    op.drop_table("sent_drafts")
