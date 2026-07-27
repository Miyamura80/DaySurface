"""Thread-curation ledger ORM model.

One row per ``(user_id, thread_id)`` holding the host-LLM's banked judgment
for a Gmail thread: which bucket it falls in, an importance score, an encrypted
summary / reasoning, a suggested action, and freshness metadata.

The ledger is a materialized view of host-LLM curation so repeat "what's
important?" reads cost almost no tokens. Freshness is tracked against Gmail's
``historyId`` (``curated_history_id``): a thread is stale once its current
history id advances past the value stamped at curation time, which also
self-corrects for out-of-band changes (a manual archive in Gmail).

``summary`` and ``reasoning`` are derived from email content, so they are
stored encrypted (``*_enc: bytes``) with a ``key_id`` mirroring
``db/models/google_tokens.py`` so keys can rotate. Plaintext is never
persisted.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ThreadCuration(Base):
    __tablename__ = "thread_curation"

    # Composite primary key gives the (user_id, thread_id) uniqueness the ledger
    # is keyed on, plus a covering index for per-user lookups.
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # Host-LLM judgment. bucket: needs_reply / waiting_on / fyi / noise.
    bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    importance: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Derived email content, encrypted at rest (see module docstring).
    summary_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    reasoning_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")

    # suggested_action: archive / reply / mark_done / none.
    suggested_action: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none"
    )
    draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # state: pending / curated / acted / dismissed.
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="curated")

    # Gmail historyId at curation time (freshness watermark) + curator tag so a
    # future prompt/model bump can be reasoned about (or bulk-invalidated).
    curated_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    curator_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    curated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_thread_curation_user_state", "user_id", "state"),
        Index("ix_thread_curation_user_bucket", "user_id", "bucket"),
    )
