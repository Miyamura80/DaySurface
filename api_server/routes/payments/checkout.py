"""Stripe checkout session creation and subscription cancellation."""

import time

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger as log
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import BILLING_WRITE, require_scopes
from api_server.billing.stripe_config import (
    ensure_stripe,
    get_stripe_price_id,
    reset_stripe_on_auth_error,
)
from common import global_config
from db.engine import get_db_session
from db.models.subscription_types import SubscriptionStatus, SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _delete_orphaned_customer(orphaned_id: str, user_id: str, winner_id: str) -> None:
    """Best-effort cleanup of a Stripe customer orphaned by a checkout race."""
    log.warning(
        "Stripe customer {} orphaned due to concurrent checkout "
        "race for user {}; using existing customer {}",
        orphaned_id,
        user_id,
        winner_id,
    )
    try:
        # Deliberately lazy: routes must import (and the app boot) without
        # the stripe SDK; billing endpoints 503 via ensure_stripe() instead.
        import stripe  # noqa: PLC0415

        stripe.Customer.delete(orphaned_id)
    except Exception:  # noqa: BLE001
        # Best-effort cleanup: any Stripe API / network error here is non-fatal;
        # the orphan will eventually be cleaned by Stripe's GC or ops tooling.
        log.warning("Failed to delete orphaned Stripe customer {}", orphaned_id)


_ACTIVE_PLUS_STATUSES = frozenset(
    {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.CANCELING.value,
        SubscriptionStatus.PAST_DUE.value,
        SubscriptionStatus.INCOMPLETE.value,
    }
)


def _recover_concurrent_customer(
    session: Session, user: AuthenticatedUser, orphaned_customer_id: str
) -> tuple[str, UserSubscription | None]:
    """Handle IntegrityError from concurrent customer creation.

    Returns ``(customer_id, sub)`` after reconciling with the winner's row.
    Raises HTTP 409 if the winner already has an active Plus subscription.
    """
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if (
        sub
        and sub.subscription_tier == SubscriptionTier.PLUS.value
        and sub.subscription_status in _ACTIVE_PLUS_STATUSES
    ):
        if sub.stripe_customer_id and orphaned_customer_id != sub.stripe_customer_id:
            _delete_orphaned_customer(
                orphaned_customer_id, user.user_id, sub.stripe_customer_id
            )
        raise HTTPException(
            status_code=409,
            detail="Active Plus subscription already exists",
        ) from None
    if sub and sub.stripe_customer_id:
        customer_id = sub.stripe_customer_id
        _delete_orphaned_customer(orphaned_customer_id, user.user_id, customer_id)
        return customer_id, sub
    if sub:
        sub.stripe_customer_id = orphaned_customer_id
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            log.error(
                "Failed to persist stripe_customer_id {} for user {} "
                "- customer may be orphaned in Stripe",
                orphaned_customer_id,
                user.user_id,
            )
            raise HTTPException(
                status_code=503,
                detail="Database error during checkout",
            ) from None
    return orphaned_customer_id, sub


def _ensure_stripe_customer(
    user: AuthenticatedUser, session: Session, sub: UserSubscription | None
) -> tuple[str, UserSubscription | None]:
    """Find or create a Stripe customer, persisting the ID to prevent duplicates.

    Returns ``(customer_id, sub)`` where *sub* may be newly created.
    """
    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    customer_id = sub.stripe_customer_id if sub else None
    if customer_id:
        return customer_id, sub

    if not user.email:
        raise HTTPException(
            status_code=422,
            detail="An email address is required to create a billing account.",
        )

    # Create the Stripe customer first (outside any DB lock) so a slow
    # Stripe round-trip doesn't hold a row lock and block other requests.
    # The idempotency key ensures concurrent calls return the same customer.
    customer = stripe.Customer.create(
        metadata={"user_id": user.user_id},
        email=user.email,
        idempotency_key=f"create-customer-{user.user_id}",
    )
    customer_id = customer.id

    # Now lock the row briefly to serialize the DB write.  Re-check after
    # acquiring the lock in case another thread already set the customer ID.
    # Nested rather than `and`-combined: the inner re-query may return None if
    # the row was deleted between reads, and that case must fall through to the
    # `else` branch below to create a fresh row.
    if sub:
        sub = (
            session.query(UserSubscription)
            .filter_by(user_id=user.user_id)
            .with_for_update()
            .first()
        )
    if sub:
        if sub.stripe_customer_id:
            # Another thread won the race -- clean up our orphan
            if customer_id != sub.stripe_customer_id:
                _delete_orphaned_customer(
                    customer_id, user.user_id, sub.stripe_customer_id
                )
            return sub.stripe_customer_id, sub
        if sub is None:
            # Row vanished between the unlocked check and with_for_update --
            # extremely rare. Log the orphan for manual cleanup and surface
            # a transient error so the client can retry.
            log.error(
                "Subscription row for user {} disappeared during checkout; "
                "Stripe customer {} is orphaned",
                user.user_id,
                customer_id,
            )
            raise HTTPException(
                status_code=503,
                detail="Database error during checkout",
            )
        sub.stripe_customer_id = customer_id
    else:
        # Either no prior row, or it was deleted between the initial read and
        # the locking re-query.  Create a fresh row tied to our new customer.
        sub = UserSubscription(
            user_id=user.user_id,
            stripe_customer_id=customer_id,
        )
        session.add(sub)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        customer_id, sub = _recover_concurrent_customer(session, user, customer_id)
    except SQLAlchemyError:
        session.rollback()
        log.error(
            "DB error persisting Stripe customer {} for user {} - customer may be orphaned in Stripe",
            customer_id,
            user.user_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Database error during checkout",
        ) from None
    return customer_id, sub


@router.post("/checkout/create")
def create_checkout(
    user: AuthenticatedUser = Depends(require_scopes(BILLING_WRITE)),
    session: Session = Depends(get_db_session),
):
    """Create a Stripe Checkout Session for the Plus tier."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    # Check for existing active subscription
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if (
        sub
        and sub.subscription_tier == SubscriptionTier.PLUS.value
        and sub.subscription_status in _ACTIVE_PLUS_STATUSES
    ):
        raise HTTPException(
            status_code=409, detail="Active Plus subscription already exists"
        )

    customer_id, sub = _ensure_stripe_customer(user, session, sub)

    price_id = get_stripe_price_id()
    if not price_id:
        raise HTTPException(status_code=503, detail="Stripe price ID not configured")

    frontend_url = global_config.FRONTEND_URL.rstrip("/")

    sub_cfg = global_config.subscription_config
    trial_days = sub_cfg.trial_period_days

    checkout_session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        subscription_data=({"trial_period_days": trial_days} if trial_days else {}),
        success_url=f"{frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend_url}/billing/cancel",
        metadata={"user_id": user.user_id},
        # Include a 5-minute bucket so rapid double-clicks are deduped
        # but abandoned sessions don't block re-checkout for 24 hours.
        idempotency_key=f"checkout-{user.user_id}-{price_id}-{int(time.time()) // 300}",
    )

    return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}


@router.post("/cancel")
def cancel_subscription(
    user: AuthenticatedUser = Depends(require_scopes(BILLING_WRITE)),
    session: Session = Depends(get_db_session),
):
    """Cancel the user's Stripe subscription."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(status_code=404, detail="No active subscription found")

    if sub.subscription_status == SubscriptionStatus.CANCELING.value:
        return {"status": "cancel_scheduled"}

    if sub.subscription_status == SubscriptionStatus.CANCELED.value:
        raise HTTPException(status_code=409, detail="Subscription already canceled")

    try:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=True,
        )
    except stripe.AuthenticationError:
        reset_stripe_on_auth_error()
        raise HTTPException(
            status_code=503, detail="Stripe authentication failed; please retry"
        ) from None
    except stripe.InvalidRequestError as exc:
        raise HTTPException(
            status_code=400, detail=exc.user_message or str(exc)
        ) from exc
    except stripe.StripeError:
        raise HTTPException(
            status_code=502, detail="Failed to cancel subscription; please retry"
        ) from None

    # Mark as CANCELING - subscription stays active until period end.
    # The customer.subscription.deleted webhook sets CANCELED when it expires.
    sub.subscription_status = SubscriptionStatus.CANCELING.value
    session.commit()

    return {"status": "cancel_scheduled"}
