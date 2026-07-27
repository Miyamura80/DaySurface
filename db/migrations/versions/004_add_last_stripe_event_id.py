"""No-op migration (last_stripe_event_id replaced by stripe_state_updated_at in 002).

Revision ID: 004
Revises: 003
Create Date: 2026-03-15
"""

from collections.abc import Sequence

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
