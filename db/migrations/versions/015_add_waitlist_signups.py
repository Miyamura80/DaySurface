"""Add waitlist_signups for the public /waitlist page.

One row per email that joins the waitlist. ``email`` is unique so the public
signup route can treat a repeat as an idempotent success. See
``db/models/waitlist_signups.py``.

Revision ID: 015
Revises: 014
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "waitlist_signups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_waitlist_signups_email"),
    )


def downgrade() -> None:
    op.drop_table("waitlist_signups")
