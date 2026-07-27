"""Add scopes column to api_keys table.

Revision ID: 003
Revises: 002
Create Date: 2026-03-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("scopes", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "scopes")
