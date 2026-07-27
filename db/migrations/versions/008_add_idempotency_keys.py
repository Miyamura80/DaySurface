"""Add idempotency_keys table for safe retries of mutating API requests.

Stores one row per ``(user_id, route, idempotency_key)``. The row is claimed
before the handler executes and updated with the cached response afterwards so
retries replay the stored result instead of re-running the side effect.

Revision ID: 008
Revises: 007
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.String(255), primary_key=True, nullable=False),
        sa.Column("route", sa.String(255), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(255), primary_key=True, nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Index for periodic TTL cleanup (DELETE WHERE created_at < NOW() - 1 day).
    op.create_index(
        "ix_idempotency_keys_created_at",
        "idempotency_keys",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
