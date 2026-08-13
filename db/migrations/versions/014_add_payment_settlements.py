"""Add payment_settlements for x402 sell-side payments.

Records one row per settled x402 payment, keyed by the SHA-256 of the
``X-PAYMENT`` payload so a single-use authorization can be claimed once and
replayed safely (settle-once). See ``db/models/payment_settlements.py``.

Revision ID: 014
Revises: 013
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_settlements",
        sa.Column("payment_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.String(length=64), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("transaction_id", sa.String(length=255), nullable=True),
        sa.Column("payer", sa.String(length=255), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("payment_hash"),
    )
    op.create_index(
        "ix_payment_settlements_user_id",
        "payment_settlements",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_settlements_user_id",
        table_name="payment_settlements",
    )
    op.drop_table("payment_settlements")
