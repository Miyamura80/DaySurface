"""Add oauth_codes and oauth_tokens for DaySurface's own OAuth server.

MCP clients get their tokens from DaySurface instead of directly from WorkOS
AuthKit, so DaySurface can revoke them (logout). See ``db/models/oauth.py``.

Revision ID: 018
Revises: 017
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(2048), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("workos_session_id", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "oauth_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(2048), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("workos_session_id", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_tokens_family_id", "oauth_tokens", ["family_id"])
    op.create_index("ix_oauth_tokens_user_id", "oauth_tokens", ["user_id"])
    op.create_index("ix_oauth_tokens_expires_at", "oauth_tokens", ["expires_at"])
    op.create_table(
        "oauth_sign_outs",
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("signed_out_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_sign_outs")
    op.drop_index("ix_oauth_tokens_expires_at", table_name="oauth_tokens")
    op.drop_index("ix_oauth_tokens_user_id", table_name="oauth_tokens")
    op.drop_index("ix_oauth_tokens_family_id", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_codes")
