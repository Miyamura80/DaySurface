"""Index oauth_codes.user_id, which sign-out filters on.

Revision ID: 019
Revises: 018
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_oauth_codes_user_id", "oauth_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_codes_user_id", table_name="oauth_codes")
