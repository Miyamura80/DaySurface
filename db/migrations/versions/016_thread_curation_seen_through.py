"""Add thread_curation.seen_through, the freshness watermark.

The newest message a banked verdict accounts for. A verdict goes stale only
when a message from someone else arrives after it. Nullable: rows written
before this column fall back to ``curated_at``. See
``services/curation_status.py``.

Revision ID: 016
Revises: 015
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "thread_curation",
        sa.Column("seen_through", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thread_curation", "seen_through")
