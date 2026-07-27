"""Subscription status endpoint (dual-source: Stripe API + DB fallback)."""

import threading
import time

from fastapi import APIRouter, Depends
from loguru import logger as log
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import BILLING_READ, require_scopes
from api_server.billing.stripe_config import (
    ensure_stripe,
    reset_stripe_on_auth_error,
)
from db.engine import get_db_session
from db.models.subscription_types import (
    STRIPE_STATUS_MAP,
    SubscriptionStatus,
    SubscriptionTier,
)
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/subscription", tags=["billing"])

# TTL cache for Stripe subscription status to avoid a live API call on
# every request.  Keyed by stripe_subscription_id.
_stripe_status_cache: dict[str, tuple[str, float]] = {}
_stripe_status_lock = threading.Lock()
_stripe_in_flight: set[str] = set()
_STRIPE_STATUS_TTL = 60  # seconds
_STRIPE_ERROR_TTL = 5  # seconds -- short TTL to avoid thundering herd on outage
_STRIPE_ERROR_SENTINEL = "__error__"
_STRIPE_CACHE_MAX_SIZE = 10_000


def _evict_stripe_cache() -> None:
    """Evict expired/oldest entries when cache is at capacity.

    Must be called while holding ``_stripe_status_lock``.
    """
    if len(_stripe_status_cache) < _STRIPE_CACHE_MAX_SIZE:
        return
    now = time.time()
    expired = [k for k, (_, exp) in list(_stripe_status_cache.items()) if exp <= now]
    for k in expired:
        _stripe_status_cache.pop(k, None)
    if len(_stripe_status_cache) >= _STRIPE_CACHE_MAX_SIZE:
        by_expiry = sorted(_stripe_status_cache.items(), key=lambda x: x[1][1])
        evict_count = max(1, len(by_expiry) // 10)
        for k, _ in by_expiry[:evict_count]:
            _stripe_status_cache.pop(k, None)


def _get_stripe_status(stripe_sub_id: str) -> str | None:
    """Fetch Stripe subscription status with a 60s TTL cache.

    Uses an in-flight set to prevent concurrent duplicate Stripe API
    calls for the same subscription ID on cache miss.
    """
    cached = _stripe_status_cache.get(stripe_sub_id)
    if cached and cached[1] > time.time():
        return None if cached[0] == _STRIPE_ERROR_SENTINEL else cached[0]

    if not ensure_stripe():
        return None

    # Acquire in-flight gate: if another thread is already fetching this
    # subscription, return the stale cache value (or None) instead of
    # piling on with another Stripe API call.
    with _stripe_status_lock:
        cached = _stripe_status_cache.get(stripe_sub_id)
        if cached and cached[1] > time.time():
            return None if cached[0] == _STRIPE_ERROR_SENTINEL else cached[0]
        if stripe_sub_id in _stripe_in_flight:
            # Deliberate stale-on-race: concurrent callers return the
            # DB-cached status rather than waiting for the in-flight
            # Stripe call.  This prevents thundering herd on Stripe's
            # API.  The webhook path keeps the DB up to date, so the
            # stale window is bounded by the webhook delivery latency.
            if cached:
                return None if cached[0] == _STRIPE_ERROR_SENTINEL else cached[0]
            return None
        _stripe_in_flight.add(stripe_sub_id)

    # Network I/O outside the lock.
    # Deliberately lazy: routes must import (and the app boot) without the
    # stripe SDK; the ensure_stripe() gate above degrades billing instead.
    import stripe  # noqa: PLC0415

    try:
        stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
        if stripe_sub.status == "active" and stripe_sub.cancel_at_period_end:
            status = SubscriptionStatus.CANCELING.value
        else:
            status = STRIPE_STATUS_MAP.get(
                stripe_sub.status, SubscriptionStatus.PAST_DUE.value
            )
        with _stripe_status_lock:
            _evict_stripe_cache()
            _stripe_status_cache[stripe_sub_id] = (
                status,
                time.time() + _STRIPE_STATUS_TTL,
            )
        return status
    except Exception as exc:  # noqa: BLE001
        # Stripe SDK raises StripeError + many subclasses, plus network errors;
        # any failure here must fall back to the DB-backed status (the function
        # returns None to signal the caller to use the cached/DB value).
        if isinstance(exc, stripe.AuthenticationError):
            reset_stripe_on_auth_error()
        log.debug(
            "Stripe subscription lookup failed for {}: {}; falling back to DB",
            stripe_sub_id,
            exc,
        )
        with _stripe_status_lock:
            _evict_stripe_cache()
            _stripe_status_cache[stripe_sub_id] = (
                _STRIPE_ERROR_SENTINEL,
                time.time() + _STRIPE_ERROR_TTL,
            )
        return None
    finally:
        with _stripe_status_lock:
            _stripe_in_flight.discard(stripe_sub_id)


@router.get("/status")
def subscription_status(
    user: AuthenticatedUser = Depends(require_scopes(BILLING_READ)),
    session: Session = Depends(get_db_session),
):
    """Return current subscription tier, status, usage, and payment info."""
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()

    if not sub:
        return {
            "tier": SubscriptionTier.FREE.value,
            "status": "active",
            "usage": 0,
            "payment_status": "current",
        }

    stripe_status = None
    if sub.stripe_subscription_id:
        stripe_status = _get_stripe_status(sub.stripe_subscription_id)

    return {
        "tier": sub.subscription_tier,
        "status": stripe_status or sub.subscription_status,
        "usage": sub.current_period_usage,
        "payment_status": sub.payment_status,
        "period_start": sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        "period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
    }
