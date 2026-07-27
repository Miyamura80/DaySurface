"""Allow google_tokens.refresh_token_enc to be NULL.

``gmail_disconnect`` now erases the refresh-token ciphertext instead of only
stamping ``revoked_at``, so a revoked row holds no Google credential at rest.
The column stays non-NULL for every live connection; only revoked rows are
NULL. Existing revoked rows are cleared in the same upgrade.

Revision ID: 012
Revises: 011
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("google_tokens") as batch_op:
        batch_op.alter_column(
            "refresh_token_enc",
            existing_type=sa.LargeBinary(),
            nullable=True,
        )
    # Retroactively erase tokens for connections already revoked.
    op.execute(
        sa.text(
            "UPDATE google_tokens SET refresh_token_enc = NULL "
            "WHERE revoked_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    # NULL is not representable in the old schema; a revoked row keeps no
    # token to restore, so back-fill an empty blob before tightening. Bind it
    # as a LargeBinary parameter rather than a bare '' literal: the column is
    # binary, and a text literal is dialect-dependent (SQLite's type affinity
    # would store a str where the ORM expects bytes).
    google_tokens = sa.table(
        "google_tokens", sa.column("refresh_token_enc", sa.LargeBinary())
    )
    op.execute(
        google_tokens.update()
        .where(google_tokens.c.refresh_token_enc.is_(None))
        .values(refresh_token_enc=b"")
    )
    with op.batch_alter_table("google_tokens") as batch_op:
        batch_op.alter_column(
            "refresh_token_enc",
            existing_type=sa.LargeBinary(),
            nullable=False,
        )
