"""Subscription enums and constants."""

import enum


class SubscriptionTier(enum.StrEnum):
    FREE = "free_tier"
    PLUS = "plus_tier"


class SubscriptionStatus(enum.StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELING = "canceling"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


class PaymentStatus(enum.StrEnum):
    CURRENT = "current"
    FAILED = "failed"
    PENDING = "pending"


# Mapping from Stripe's raw subscription status strings to local enum values.
# Shared by webhook handlers (write path) and subscription status endpoint
# (read path) so the API always returns a consistent vocabulary.
STRIPE_STATUS_MAP: dict[str, str] = {
    "trialing": SubscriptionStatus.TRIALING.value,
    "active": SubscriptionStatus.ACTIVE.value,
    "incomplete": SubscriptionStatus.INCOMPLETE.value,
    "incomplete_expired": SubscriptionStatus.CANCELED.value,
    "past_due": SubscriptionStatus.PAST_DUE.value,
    "canceled": SubscriptionStatus.CANCELED.value,
    "unpaid": SubscriptionStatus.PAST_DUE.value,
    "paused": SubscriptionStatus.PAST_DUE.value,
}
