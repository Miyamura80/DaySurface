"""Idempotency records for safe retries of mutating API requests.

One row per ``(user_id, route, idempotency_key)``. The row is inserted to
*claim* the key before the handler runs (``completed_at`` NULL = in-flight);
once the handler succeeds the cached response body and status code are written
and ``completed_at`` is set so subsequent retries replay the stored response
instead of re-executing the side effect.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"

    # Composite PK namespaces the client-supplied key by user and route so two
    # users (or two endpoints) can reuse the same Idempotency-Key value safely.
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    route: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), primary_key=True, nullable=False
    )
    # SHA-256 of the canonical request payload; reusing a key with a different
    # payload is a client error (422) rather than a silent replay.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL until the handler completes; then the cached response is replayed.
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
