"""Add daily_quota_reset_at column to user_subscriptions.

Separates daily quota reset tracking from Stripe billing period dates
so the day-boundary reset doesn't corrupt current_period_start.

Revision ID: 006
Revises: 005
Create Date: 2026-03-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_subscriptions",
        sa.Column("daily_quota_reset_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_subscriptions", "daily_quota_reset_at")
