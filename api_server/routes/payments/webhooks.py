"""Stripe webhook handler with dual-secret verification."""

import asyncio
import random
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from loguru import logger as log
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from api_server.billing.stripe_config import (
    ensure_stripe,
    get_stripe_price_id,
    get_webhook_secret,
    is_production,
)
from common import global_config
from db.engine import use_db_session
from db.models.processed_stripe_events import ProcessedStripeEvent
from db.models.subscription_types import (
    STRIPE_STATUS_MAP,
    PaymentStatus,
    SubscriptionStatus,
    SubscriptionTier,
)
from db.models.user_subscriptions import UserSubscription

WEBHOOK_PREFIX = "/api/v1/billing/webhook"

router = APIRouter(prefix=WEBHOOK_PREFIX, tags=["billing"])


async def _dispatch_event(event_type: str, data: dict, event_id: str) -> None:
    """Route a webhook event to the appropriate sync handler."""
    if event_type == "customer.subscription.created":
        await asyncio.to_thread(
            _handle_subscription_created, data, event_id, event_type
        )
    elif event_type == "customer.subscription.updated":
        await asyncio.to_thread(
            _handle_subscription_updated, data, event_id, event_type
        )
    elif event_type == "customer.subscription.deleted":
        await asyncio.to_thread(
            _handle_subscription_deleted, data, event_id, event_type
        )
    elif event_type == "invoice.payment_failed":
        await asyncio.to_thread(_handle_payment_failed, data, event_id, event_type)
    elif event_type == "invoice.payment_succeeded":
        await asyncio.to_thread(_handle_payment_succeeded, data, event_id, event_type)
    else:
        log.debug("Unhandled webhook event: {}", event_type)


def _try_construct_event(payload: bytes, sig_header: str):
    """Try to verify with primary secret, then fallback to test secret."""
    # Deliberately lazy: routes must import (and the app boot) without the
    # stripe SDK; the webhook 503s via ensure_stripe() before reaching here.
    import stripe  # noqa: PLC0415

    secret = get_webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except stripe.SignatureVerificationError:
        # Only try fallback secret in non-production environments.
        # In production, accepting a test-signed webhook would be a
        # security risk (test-mode dashboard access could inject events).
        if is_production():
            raise

        fallback = (
            global_config.STRIPE_TEST_WEBHOOK_SECRET
            if secret == global_config.STRIPE_WEBHOOK_SECRET
            else global_config.STRIPE_WEBHOOK_SECRET
        )
        if fallback and fallback != secret:
            return stripe.Webhook.construct_event(payload, sig_header, fallback)
        raise


@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Handle incoming Stripe webhook events."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = _try_construct_event(payload, sig_header)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Webhook boundary: Stripe SDK raises SignatureVerificationError plus
        # ValueError/JSONDecodeError on bad bodies; all map to a 400.
        log.warning("Webhook signature verification failed: {}", exc)
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    try:
        event_id = event["id"]
        event_type = event["type"]
        data = event["data"]["object"]
    except (KeyError, TypeError) as exc:
        log.warning("Malformed webhook event structure: {}", exc)
        raise HTTPException(status_code=400, detail="Malformed event") from exc

    # Dispatch to sync handlers via asyncio.to_thread to avoid blocking
    # the event loop during synchronous SQLAlchemy DB operations.
    # _CustomerNotFoundError is a domain exception raised by handlers;
    # convert it to an HTTP 500 here so Stripe retries the webhook.
    try:
        await _dispatch_event(event_type, data, event_id)
    except _CustomerNotFoundError:
        raise HTTPException(
            status_code=500, detail="Customer not found, will retry"
        ) from None
    except _ConfigurationError:
        raise HTTPException(
            status_code=500, detail="Billing configuration error, will retry"
        ) from None

    # Probabilistic cleanup of old processed events (1% of requests),
    # with a time-based fallback so low-traffic deployments don't let
    # the table grow indefinitely.
    if random.random() < 0.01 or _cleanup_overdue():  # noqa: S311
        task = asyncio.create_task(asyncio.to_thread(_cleanup_old_events))
        task.add_done_callback(_log_cleanup_failure)

    return {"received": True}


_EVENT_RETENTION = timedelta(days=7)
_last_cleanup = time.monotonic()
_CLEANUP_INTERVAL = 3600  # force cleanup at least once per hour
_cleanup_lock = threading.Lock()


def _cleanup_overdue() -> bool:
    """Return True if enough time has elapsed to force a cleanup."""
    return (time.monotonic() - _last_cleanup) >= _CLEANUP_INTERVAL


def _log_cleanup_failure(task: asyncio.Task) -> None:
    """Log unexpected cleanup task failures instead of silently dropping them."""
    if not task.cancelled() and task.exception() is not None:
        log.warning("Cleanup task failed: {}", task.exception())


def _cleanup_old_events() -> None:
    """Delete processed_stripe_events older than 7 days.

    Both webhook dedup records and ``meter:`` metering idempotency keys
    share the same 7-day retention window.  This is sufficient for
    metering callers since Stripe itself expires idempotency keys after
    24 hours.
    """
    global _last_cleanup  # noqa: PLW0603
    with _cleanup_lock:
        if not _cleanup_overdue():
            return  # another thread already ran cleanup
        prev_cleanup = _last_cleanup
        new_cleanup = time.monotonic()
        _last_cleanup = new_cleanup
    try:
        cutoff = datetime.now(UTC) - _EVENT_RETENTION
        with use_db_session() as session:
            result = session.execute(
                delete(ProcessedStripeEvent).where(
                    ProcessedStripeEvent.processed_at < cutoff,
                )
            )
            session.commit()
            if result.rowcount:  # ty: ignore[unresolved-attribute]
                log.info(
                    "Cleaned up {} old processed stripe events",
                    result.rowcount,  # ty: ignore[unresolved-attribute]
                )
    except Exception:  # noqa: BLE001
        # Background cleanup: any DB error must not crash the webhook handler;
        # restore the previous cleanup timestamp so the next probe retries.
        with _cleanup_lock:
            if _last_cleanup == new_cleanup:
                _last_cleanup = prev_cleanup
        log.warning("Failed to clean up old processed stripe events")


def _find_subscription_by_customer(
    session, customer_id: str
) -> UserSubscription | None:
    return (
        session.query(UserSubscription)
        .filter_by(stripe_customer_id=customer_id)
        .first()
    )


def _mark_event_processed(session, event_id: str, event_type: str) -> bool:
    """Try to insert event into processed_stripe_events table.

    Returns True if the event was newly inserted (not a duplicate).
    Returns False if the event was already processed (IntegrityError on PK).
    Uses a savepoint so only the INSERT is rolled back on conflict,
    preserving any other pending session state.
    """
    try:
        with session.begin_nested():
            session.add(ProcessedStripeEvent(event_id=event_id, event_type=event_type))
        return True
    except IntegrityError:
        return False


def _map_stripe_status(data: dict) -> tuple[str, bool]:
    """Map Stripe subscription status to local enum and is_active flag.

    ``paused`` subscriptions are mapped to ``PAST_DUE`` and ``is_active=False``
    because Stripe may pause collection during dunning flows, not only on
    voluntary customer requests.  Paid-tier access is revoked until the
    subscription returns to ``active`` or ``trialing``.
    """
    stripe_status = data.get("status", "active")
    local_status = STRIPE_STATUS_MAP.get(
        stripe_status, SubscriptionStatus.PAST_DUE.value
    )
    is_active = stripe_status in ("trialing", "active")
    return local_status, is_active


def _resolve_tier(data: dict) -> str:
    """Map a Stripe subscription's price ID to a local tier.

    Logs an error if the price ID is unrecognised so that adding a new
    Stripe tier without updating this mapping is caught immediately
    rather than silently assigning every subscriber to PLUS.
    """
    items = data.get("items", {}).get("data", [])
    price_id = items[0].get("price", {}).get("id") if items else None
    expected_plus_id = get_stripe_price_id()

    if not expected_plus_id:
        log.error(
            "Stripe PLUS price ID is not configured; cannot resolve tier for price {}. "
            "Set subscription_config.stripe.price_ids correctly.",
            price_id,
        )
        raise _ConfigurationError("price_id_not_configured")

    if price_id is not None and price_id == expected_plus_id:
        return SubscriptionTier.PLUS.value

    log.error(
        "Unknown Stripe price ID {}; expected PLUS price {}. "
        "Defaulting to FREE tier -- update _resolve_tier for new tiers",
        price_id,
        expected_plus_id,
    )
    return SubscriptionTier.FREE.value


class _CustomerNotFoundError(Exception):
    """Raised when a webhook references an unknown customer (triggers retry)."""


class _ConfigurationError(Exception):
    """Raised when a required billing config value is absent (triggers retry)."""


def _event_timestamp(data: dict) -> datetime:
    """Return the Stripe event's own ``created`` timestamp (or server time as fallback)."""
    event_ts = data.get("created")
    return datetime.fromtimestamp(event_ts, tz=UTC) if event_ts else datetime.now(UTC)


def _is_stale_event(data: dict, sub: UserSubscription) -> bool:
    """Return True if this event predates the last webhook-driven state change.

    Stripe does not guarantee event delivery order. Comparing the event's
    ``created`` timestamp against ``stripe_state_updated_at`` (set only by
    webhook handlers, not by usage tracking or daily quota resets) prevents
    an older event from overwriting a newer state.
    """
    event_created = data.get("created")
    if event_created and sub.stripe_state_updated_at:
        updated_at = sub.stripe_state_updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return datetime.fromtimestamp(event_created, tz=UTC) < updated_at
    return False


def _handle_subscription_created(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.error(
                "Received subscription.created for unknown customer {}; will retry",
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        if _is_stale_event(data, sub):
            log.debug(
                "Skipping stale subscription.created event {} for customer {}",
                event_id,
                customer_id,
            )
            session.commit()
            return

        local_status, is_active = _map_stripe_status(data)
        # Preserve CANCELING state on creation (rare but valid via API).
        if local_status == SubscriptionStatus.ACTIVE.value and data.get(
            "cancel_at_period_end"
        ):
            local_status = SubscriptionStatus.CANCELING.value
        sub.stripe_subscription_id = data.get("id")
        sub.subscription_tier = _resolve_tier(data)
        sub.subscription_status = local_status
        sub.is_active = is_active
        sub.stripe_state_updated_at = _event_timestamp(data)

        current_period = data.get("current_period_start")
        if current_period:
            sub.current_period_start = datetime.fromtimestamp(current_period, tz=UTC)
        period_end = data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

        session.commit()
        log.info("Subscription created for customer {}", customer_id)


def _handle_subscription_updated(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.error(
                "Received subscription.updated for unknown customer {}; will retry",
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        if _is_stale_event(data, sub):
            log.debug(
                "Skipping stale subscription.updated event {} for customer {}",
                event_id,
                customer_id,
            )
            session.commit()
            return

        local_status, is_active = _map_stripe_status(data)

        # Preserve local CANCELING state: Stripe keeps status "active"
        # when cancel_at_period_end=True, but we track it separately.
        if local_status == SubscriptionStatus.ACTIVE.value and data.get(
            "cancel_at_period_end"
        ):
            local_status = SubscriptionStatus.CANCELING.value

        sub.subscription_status = local_status
        sub.is_active = is_active
        # Resolve tier from price ID (same as subscription.created) so
        # unrecognised prices are caught rather than silently granting PLUS.
        # PAST_DUE intentionally keeps its current tier as a grace period --
        # downgrade occurs only on customer.subscription.deleted.
        if is_active:
            sub.subscription_tier = _resolve_tier(data)
            sub.payment_failure_count = 0
            sub.last_payment_error = None
        elif local_status == SubscriptionStatus.CANCELED.value:
            sub.subscription_tier = SubscriptionTier.FREE.value
            # Clear to prevent stale Stripe polling from the status endpoint
            sub.stripe_subscription_id = None
            # Reset usage so the user isn't immediately quota-blocked on the
            # free tier if subscription.deleted arrives late or is lost.
            sub.current_period_usage = 0
            sub.daily_quota_reset_at = datetime.now(UTC)

        sub.stripe_state_updated_at = _event_timestamp(data)

        current_period = data.get("current_period_start")
        if current_period:
            sub.current_period_start = datetime.fromtimestamp(current_period, tz=UTC)
        period_end = data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

        session.commit()
        log.info("Subscription updated for customer {}", customer_id)


def _handle_subscription_deleted(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.error(
                "Received {} for unknown customer {}; will retry",
                event_type,
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        if _is_stale_event(data, sub):
            log.debug(
                "Skipping stale {} event {} for customer {}",
                event_type,
                event_id,
                customer_id,
            )
            session.commit()
            return

        sub.subscription_tier = SubscriptionTier.FREE.value
        sub.subscription_status = SubscriptionStatus.CANCELED.value
        sub.stripe_subscription_id = None
        sub.is_active = False
        sub.stripe_state_updated_at = _event_timestamp(data)
        # Reset usage so the user is not immediately quota-blocked
        # on the lower free tier daily limit.
        sub.current_period_usage = 0
        sub.daily_quota_reset_at = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        session.commit()
        log.info("Subscription canceled for customer {}", customer_id)


def _resolve_payment_error(data: dict) -> str | None:
    """Extract last_payment_error message from the PaymentIntent.

    Called after a fast-path dedup check (separate session) to skip
    already-processed events.  Runs outside the main DB session to
    avoid holding a pooled connection during the network call.
    """
    pi = data.get("payment_intent")
    if isinstance(pi, dict):
        raw_err = pi.get("last_payment_error")
        return raw_err.get("message") if isinstance(raw_err, dict) else None
    if isinstance(pi, str) and ensure_stripe():
        try:
            # Deliberately lazy: stripe SDK optional (see _try_construct_event);
            # guarded by the ensure_stripe() check above.
            import stripe  # noqa: PLC0415

            pi_obj = stripe.PaymentIntent.retrieve(pi)
            raw_err = getattr(pi_obj, "last_payment_error", None)
            if raw_err:
                return getattr(raw_err, "message", None)
        except Exception:  # noqa: BLE001
            # Best-effort enrichment: Stripe API errors are non-fatal here;
            # webhook processing continues with no error-message detail.
            log.debug("Failed to retrieve PaymentIntent {} for error details", pi)
    return None


def _handle_payment_failed(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    # Single session for dedup + update so a rollback on customer-not-found
    # also undoes the dedup record, allowing Stripe to retry the event.
    # _resolve_payment_error is called after dedup succeeds so that
    # concurrent duplicate events don't make redundant Stripe API calls.
    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.warning(
                "Received {} for unknown customer {}; will retry",
                event_type,
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        if _is_stale_event(data, sub):
            log.debug(
                "Skipping stale payment_failed event {} for customer {}",
                event_id,
                customer_id,
            )
            session.commit()
            return

        # Resolve error details only after confirming the customer exists
        # and the event is not stale, to avoid wasted Stripe API calls.
        error_msg = _resolve_payment_error(data)

        sub.payment_status = PaymentStatus.FAILED.value
        sub.payment_failure_count = (sub.payment_failure_count or 0) + 1
        sub.last_payment_error = error_msg
        sub.updated_at = datetime.now(UTC)
        sub.stripe_state_updated_at = _event_timestamp(data)
        session.commit()
        log.warning("Payment failed for customer {}", customer_id)


def _handle_payment_succeeded(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.warning(
                "Received {} for unknown customer {}; will retry",
                event_type,
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        if _is_stale_event(data, sub):
            log.debug(
                "Skipping stale payment_succeeded event {} for customer {}",
                event_id,
                customer_id,
            )
            session.commit()
            return

        sub.payment_status = PaymentStatus.CURRENT.value
        sub.payment_failure_count = 0
        sub.last_payment_error = None
        # Reset usage and advance period boundaries for the new billing cycle.
        # Also reset daily_quota_reset_at so ensure_daily_limit re-triggers
        # the day-boundary reset on the next request (prevents quota bypass).
        sub.current_period_usage = 0
        sub.daily_quota_reset_at = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Prefer subscription line-item period over invoice top-level
        # period_start/period_end, which may not match the subscription
        # billing cycle when invoices have multiple line items.
        lines = data.get("lines", {}).get("data", [])
        first_line = lines[0] if lines else {}
        line_period = first_line.get("period", {})
        period_start = line_period.get("start") or data.get("period_start")
        if period_start:
            sub.current_period_start = datetime.fromtimestamp(period_start, tz=UTC)
        period_end = line_period.get("end") or data.get("period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)
        sub.stripe_state_updated_at = _event_timestamp(data)
        session.commit()
        log.info("Payment succeeded for customer {}", customer_id)
