"""Add processed_stripe_events table for webhook dedup.

Revision ID: 005
Revises: 004
Create Date: 2026-03-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_stripe_events",
        sa.Column("event_id", sa.String(255), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Index for periodic cleanup (DELETE WHERE processed_at < NOW() - 7 days)
    op.create_index(
        "ix_processed_stripe_events_processed_at",
        "processed_stripe_events",
        ["processed_at"],
    )


def downgrade() -> None:
    op.drop_table("processed_stripe_events")
