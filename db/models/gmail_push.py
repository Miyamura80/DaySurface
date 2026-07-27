"""Processed Gmail Pub/Sub push messages for deduplication.

Pub/Sub delivers push notifications at-least-once, so the same ``messageId``
can arrive more than once. The push receiver inserts a row here inside a
savepoint and treats an ``IntegrityError`` on the primary key as a duplicate,
exactly mirroring ``processed_stripe_events``.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ProcessedPubsubMessage(Base):
    __tablename__ = "processed_pubsub_messages"

    # Pub/Sub messageId (unique per publish, stable across redeliveries).
    message_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, nullable=False
    )
    # Indexed for the periodic cleanup sweep (DELETE WHERE received_at < cutoff),
    # matching ix_processed_pubsub_messages_received_at in migration 008.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
