"""Waitlist signups captured from the public /waitlist page.

Deliberately its own table, not a row in ``profiles``: a waitlist entry is an
unauthenticated email address with no account, Google token, or subscription
behind it, so it must never mingle with real user records. ``email`` is unique
so a repeat submit is idempotent (the public route treats a conflict as success
rather than an error - see ``services/waitlist_svc.py``).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class WaitlistSignup(Base):
    __tablename__ = "waitlist_signups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 320 = RFC 5321 max (64 local + @ + 255 domain). Unique so dedupe is a DB
    # invariant, not just application logic.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # Where the signup came from (e.g. "waitlist-page"), for later attribution.
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
