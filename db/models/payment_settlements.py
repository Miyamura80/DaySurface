"""Settlement records for x402 (sell-side) payments.

One row per settled payment, keyed by ``payment_hash`` - the SHA-256 of the
canonical ``X-PAYMENT`` payload. Because an x402 authorization carries a
single-use nonce, that hash is globally unique per payment, so the primary key
gives us settle-once semantics: the row is inserted to *claim* the payment
before contacting the facilitator (``status`` "pending"); once the facilitator
confirms settlement the row is marked "settled" with the transaction id. A
replayed ``X-PAYMENT`` header finds the settled row and is allowed through
without re-charging, mirroring the idempotency-key pattern used for mutating
API routes.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class PaymentSettlement(Base):
    __tablename__ = "payment_settlements"

    # SHA-256 of the canonical X-PAYMENT payload. Unique per single-use x402
    # authorization, so it doubles as the settle-once claim key.
    payment_hash: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False
    )
    # Principal that presented the payment and the service it paid for.
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(255), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    # Priced amount + asset + network captured at settlement time.
    amount: Mapped[str] = mapped_column(String(64), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    network: Mapped[str] = mapped_column(String(64), nullable=False)
    # "pending" while the facilitator call is in flight; "settled" once funds
    # are captured. A pending row that never settles is released (deleted).
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # On-chain (or facilitator) transaction id, set once settled.
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Full facilitator response, retained for reconciliation/receipts.
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
