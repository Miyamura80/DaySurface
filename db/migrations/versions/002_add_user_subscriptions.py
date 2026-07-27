"""Add user_subscriptions table.

Revision ID: 002
Revises: 001
Create Date: 2026-03-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column(
            "subscription_tier",
            sa.String(50),
            nullable=False,
            server_default="free_tier",
        ),
        sa.Column(
            "subscription_status",
            sa.String(50),
            nullable=False,
            server_default="active",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("trial_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stripe_customer_id", sa.String(255), nullable=True, unique=True, index=True
        ),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("stripe_meter_event_name", sa.String(255), nullable=True),
        sa.Column("stripe_state_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payment_status", sa.String(50), nullable=False, server_default="current"
        ),
        sa.Column(
            "payment_failure_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("last_payment_error", sa.Text, nullable=True),
        sa.Column(
            "current_period_usage", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
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


def downgrade() -> None:
    op.drop_table("user_subscriptions")
