"""Index webhook_deliveries.subscription_id.

The disconnect purge deletes a user's deliveries by matching the
subscriptions they own (``subscription_id IN (SELECT ...)``) rather than by a
snapshot of their event ids. Without an index that delete scans the whole
outbox - every tenant's rows - so a per-user purge would get slower as
unrelated traffic grows.

Revision ID: 013
Revises: 012
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_webhook_deliveries_subscription_id",
        "webhook_deliveries",
        ["subscription_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_deliveries_subscription_id",
        table_name="webhook_deliveries",
    )
