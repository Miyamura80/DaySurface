"""Add google_tokens table for encrypted Google OAuth refresh-token storage.

Stores one row per ``user_id`` with the user's Fernet-encrypted refresh
token, the active encryption ``key_id`` (for forward-compatible rotation),
granted scopes, and grant / revocation timestamps. Access tokens are
minted on demand and never persisted.

Revision ID: 007
Revises: 006
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_tokens",
        sa.Column("user_id", sa.String(length=255), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "key_id",
            sa.String(length=32),
            nullable=False,
            server_default="v1",
        ),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("google_tokens")
