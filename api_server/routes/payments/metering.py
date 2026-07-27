"""Metered usage reporting via Stripe Billing Meter API."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger as log
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import BILLING_READ, BILLING_WRITE, require_scopes
from api_server.billing.stripe_config import (
    ensure_stripe,
    get_included_units,
    get_meter_event_name,
    reset_stripe_on_auth_error,
)
from common import global_config
from db.engine import get_db_session, use_db_session
from db.models.processed_stripe_events import ProcessedStripeEvent
from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/usage", tags=["billing"])

# Stripe's documented limit for MeterEvent identifier field.
_STRIPE_IDENTIFIER_MAX = 100


def _report_to_stripe(
    sub: UserSubscription, user_id: str, idempotency_key: str
) -> bool:
    """Send a meter event to Stripe. Returns True on success or skip."""
    if not ensure_stripe():
        if sub.stripe_customer_id:
            log.error(
                "Stripe SDK not initialised but user {} is on a paid tier; "
                "refusing to increment local counter without Stripe event",
                user_id,
            )
            return False
        return True
    if not sub.stripe_customer_id:
        log.error(
            "Paid user {} has no stripe_customer_id; cannot report meter event",
            user_id,
        )
        return False
    # Deliberately lazy: routes must import (and the app boot) without the
    # stripe SDK; the ensure_stripe() gate above degrades billing instead.
    import stripe  # noqa: PLC0415

    try:
        stripe.billing.MeterEvent.create(
            event_name=get_meter_event_name(),
            payload={"stripe_customer_id": sub.stripe_customer_id, "value": "1"},
            identifier=idempotency_key,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # Stripe SDK + network errors all need the same response: log, reset
        # auth on AuthError, and return False so the caller rolls back the
        # dedup record and can retry.
        if isinstance(exc, stripe.AuthenticationError):
            reset_stripe_on_auth_error()
        log.warning(
            "Failed to report meter event for customer {}; "
            "rolling back dedup record so caller can retry: {}",
            sub.stripe_customer_id,
            exc,
        )
        return False


@router.post("/report")
def report_usage(
    request: Request,
    user: AuthenticatedUser = Depends(require_scopes(BILLING_WRITE)),
    session: Session = Depends(get_db_session),
):
    """Report a single usage event via Stripe Billing Meter.

    This endpoint is for explicit metering (e.g. batch/external usage) and
    is separate from the daily quota enforced by ``ensure_daily_limit`` on
    service routes. Do not call this for actions that already pass through
    the services route, as it would double-count usage.

    Requires an ``Idempotency-Key`` header so that retries are safe: the
    key deduplicates both the Stripe MeterEvent and the local DB counter
    increment.
    """
    idempotency_key = (request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key header is required for metering requests",
        )
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    if not sub.is_active:
        raise HTTPException(
            status_code=402, detail="No active subscription for metering"
        )
    if sub.subscription_tier == SubscriptionTier.FREE.value:
        raise HTTPException(
            status_code=402,
            detail="Metering reporting requires an active paid subscription",
        )

    # Compute the tightest key-length limit across both DB dedup key
    # (String(255)) and Stripe identifier (100 chars).
    db_prefix_len = len(f"meter:{user.user_id}:")
    db_max_key_len = 255 - db_prefix_len
    stripe_cid = sub.stripe_customer_id or ""
    stripe_max_key_len = _STRIPE_IDENTIFIER_MAX - len(stripe_cid) - 1  # for ":"
    effective_max = min(db_max_key_len, stripe_max_key_len)
    if effective_max <= 0:
        raise HTTPException(
            status_code=500,
            detail="User/customer ID too long for metering dedup key",
        )
    if len(idempotency_key) > effective_max:
        raise HTTPException(
            status_code=422,
            detail=f"Idempotency-Key must not exceed {effective_max} characters",
        )

    # Namespace dedup keys by user so two different users with the same
    # Idempotency-Key header value don't collide.
    db_dedup_key = f"meter:{user.user_id}:{idempotency_key}"
    stripe_identifier = f"{stripe_cid}:{idempotency_key}"

    # Use a dedicated session for dedup + Stripe + counter increment so
    # all three are committed atomically.  A failure at any stage rolls
    # back the entire transaction, keeping the dedup record and counter
    # in sync.  The Stripe call runs between flush (dedup) and commit
    # so that on Stripe failure the dedup record is also rolled back,
    # allowing the caller to retry with the same idempotency key.
    with use_db_session() as dedup_session:
        try:
            dedup_session.add(
                ProcessedStripeEvent(
                    event_id=db_dedup_key,
                    event_type="metering.report_usage",
                )
            )
            dedup_session.flush()
        except IntegrityError:
            dedup_session.rollback()
            session.refresh(sub)
            log.debug("Duplicate metering request with key {}", idempotency_key)
            return {"usage": sub.current_period_usage}

        # Report to Stripe (identifier deduplicates on Stripe's side).
        # If Stripe fails, the dedup_session rollback (on context exit)
        # undoes only the dedup INSERT, leaving the caller free to retry.
        if not _report_to_stripe(sub, user.user_id, stripe_identifier):
            raise HTTPException(
                status_code=502,
                detail="Stripe meter event failed; retry with the same Idempotency-Key",
            )

        # Atomic increment in the same session/transaction as the dedup
        # record so both commit together.  If this commit fails, neither
        # the dedup record nor the counter increment persists.
        dedup_session.execute(
            update(UserSubscription)
            .where(UserSubscription.user_id == user.user_id)
            .values(
                current_period_usage=UserSubscription.current_period_usage + 1,
                updated_at=datetime.now(UTC),
            )
        )
        dedup_session.commit()

    session.refresh(sub)
    return {"usage": sub.current_period_usage}


@router.get("/current")
def get_current_usage(
    user: AuthenticatedUser = Depends(require_scopes(BILLING_READ)),
    session: Session = Depends(get_db_session),
):
    """Return current period usage, daily quota limit, and billing overage info."""
    sub_cfg = global_config.subscription_config
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()

    tier_key = sub.subscription_tier if sub else SubscriptionTier.FREE.value
    tier_cfg = sub_cfg.tier_limits.get(tier_key)
    daily_limit = tier_cfg.daily_requests if tier_cfg else 100

    if not sub:
        return {
            "usage": 0,
            "daily_limit": daily_limit,
            "billing_included_units": get_included_units(),
            "daily_overage": 0,
        }

    included = get_included_units()
    # current_period_usage resets daily (via ensure_daily_limit), so this
    # reflects daily overage against included units, not billing-period
    # overage.  Stripe's Meter API is the billing source of truth.
    daily_overage = max(0, sub.current_period_usage - included)

    return {
        "usage": sub.current_period_usage,
        "daily_limit": daily_limit,
        "billing_included_units": included,
        "daily_overage": daily_overage,
        "period_start": sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        "period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
    }
