"""Add Gmail watch state, Pub/Sub dedup, and outbound webhook tables.

Extends ``google_tokens`` with Gmail ``users.watch`` state (forward-only
historyId baseline, expiration, topic) and indexes ``email`` for the push
receiver's user lookup. Adds ``processed_pubsub_messages`` (at-least-once
dedup), plus the outbound webhook triplet: ``webhook_subscriptions``,
``webhook_events``, and the ``webhook_deliveries`` outbox.

Revision ID: 009
Revises: 008
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- google_tokens: Gmail watch state + email index ---
    op.add_column(
        "google_tokens",
        sa.Column("watch_history_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "google_tokens",
        sa.Column("watch_expiration", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "google_tokens",
        sa.Column("watch_topic", sa.String(length=512), nullable=True),
    )
    op.create_index(
        "ix_google_tokens_email",
        "google_tokens",
        ["email"],
    )

    # --- processed_pubsub_messages: at-least-once push dedup ---
    op.create_table(
        "processed_pubsub_messages",
        sa.Column("message_id", sa.String(255), primary_key=True, nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Index for periodic cleanup (DELETE WHERE received_at < NOW() - 7 days).
    op.create_index(
        "ix_processed_pubsub_messages_received_at",
        "processed_pubsub_messages",
        ["received_at"],
    )

    # --- webhook_subscriptions ---
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("event_types", sa.JSON(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_webhook_subscriptions_user_id",
        "webhook_subscriptions",
        ["user_id"],
    )

    # --- webhook_events ---
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_webhook_events_user_id",
        "webhook_events",
        ["user_id"],
    )

    # --- webhook_deliveries (outbox) ---
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("subscription_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_event_id",
        "webhook_deliveries",
        ["event_id"],
    )
    # Drain query: pending rows whose next_attempt_at is due, ordered by it.
    op.create_index(
        "ix_webhook_deliveries_status_next_attempt_at",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_events")
    op.drop_table("webhook_subscriptions")
    op.drop_index(
        "ix_processed_pubsub_messages_received_at",
        table_name="processed_pubsub_messages",
    )
    op.drop_table("processed_pubsub_messages")
    op.drop_index("ix_google_tokens_email", table_name="google_tokens")
    op.drop_column("google_tokens", "watch_topic")
    op.drop_column("google_tokens", "watch_expiration")
    op.drop_column("google_tokens", "watch_history_id")
