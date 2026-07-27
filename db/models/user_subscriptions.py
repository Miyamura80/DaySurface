"""User subscription ORM model."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models.subscription_types import (
    PaymentStatus,
    SubscriptionStatus,
    SubscriptionTier,
)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )

    # Subscription details
    subscription_tier: Mapped[str] = mapped_column(
        String(50), nullable=False, default=SubscriptionTier.FREE.value
    )
    subscription_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=SubscriptionStatus.ACTIVE.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Trial
    trial_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Stripe IDs
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    stripe_meter_event_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # Webhook event ordering (guards against out-of-order Stripe delivery)
    stripe_state_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Payment
    payment_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=PaymentStatus.CURRENT.value
    )
    payment_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_payment_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Usage tracking (local cache)
    current_period_usage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    daily_quota_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
