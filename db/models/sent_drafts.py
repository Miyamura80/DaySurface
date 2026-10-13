"""Ledger of drafts this server sent, keyed by ``(user_id, draft_id)``.

Gmail deletes a draft both when it is sent and when it is discarded, so "the
draft is gone" cannot say which happened. ``gmail_send`` writes a row here once
Gmail confirms a send, so a caller whose reply was lost (or who retries) can
get the first send's result back instead of guessing. Holds Gmail ids and a
timestamp only, no message content.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class SentDraft(Base):
    __tablename__ = "sent_drafts"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # The sent message, as Gmail's drafts.send returned it.
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Indexed for a future retention sweep (DELETE WHERE sent_at < cutoff).
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
