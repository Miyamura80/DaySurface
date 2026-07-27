"""Daily usage limit enforcement."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from loguru import logger as log
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription


@dataclass
class LimitStatus:
    allowed: bool
    current_usage: int
    daily_limit: int
    remaining: int
    tier: str


def _get_or_create_subscription(session: Session, user_id: str) -> UserSubscription:
    """Return the user's subscription, creating a free-tier row if needed."""
    sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
    if sub is not None:
        return sub

    try:
        now = datetime.now(UTC)
        sub = UserSubscription(
            user_id=user_id,
            subscription_tier=SubscriptionTier.FREE.value,
            daily_quota_reset_at=now,
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)
        return sub
    except IntegrityError:
        session.rollback()
        sub = session.query(UserSubscription).filter_by(user_id=user_id).first()

    if sub is None:
        raise RuntimeError(
            f"Failed to create or retrieve subscription for user {user_id}"
        )
    return sub


def ensure_daily_limit(user_id: str) -> LimitStatus:
    """Check and enforce the daily request limit for a user.

    Creates a free-tier subscription row if none exists.
    Raises HTTP 402 if the daily quota is exceeded.

    Always uses its own short-lived session to avoid committing or rolling
    back pending work on a caller-provided session.

    Uses an atomic UPDATE...WHERE to prevent race conditions under concurrent
    load.  Uses ``daily_quota_reset_at`` (not ``current_period_start``) for
    day-boundary tracking so the Stripe billing period is never corrupted.
    """
    cfg = global_config.subscription_config

    with use_db_session() as session:
        sub = _get_or_create_subscription(session, user_id)

        tier_key = sub.subscription_tier
        tier_cfg = cfg.tier_limits.get(tier_key)
        if tier_cfg is None:
            log.error(
                "Unknown subscription tier {!r} for user {}; "
                "blocking request until tier is configured",
                tier_key,
                user_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "misconfiguration",
                    "message": "Subscription tier not configured",
                },
            )
        daily_limit = tier_cfg.daily_requests

        # Initialise daily_quota_reset_at if missing (e.g., pre-migration rows)
        reset_at = sub.daily_quota_reset_at
        if reset_at is None:
            now = datetime.now(UTC)
            session.execute(
                update(UserSubscription)
                .where(
                    UserSubscription.user_id == user_id,
                    UserSubscription.daily_quota_reset_at.is_(None),
                )
                .values(
                    daily_quota_reset_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            # Refresh picks up the value we wrote, or the value another
            # request wrote if our WHERE-IS-NULL was a no-op.  Either way
            # reset_at ends up as a valid timestamp and we proceed normally.
            session.refresh(sub)
            reset_at = sub.daily_quota_reset_at

        # Atomic day-boundary reset: merge reset + first increment into one
        # statement so concurrent requests can't clobber each other's counts.
        # Only touches daily_quota_reset_at, never current_period_start.
        # reset_at is guaranteed non-None here (initialised above if missing).
        if reset_at is None:
            raise RuntimeError(
                f"daily_quota_reset_at unexpectedly None for user {user_id}"
            )
        now = datetime.now(UTC)
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=UTC)
        if now.date() > reset_at.date():
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            # synchronize_session=False on both day-reset updates: the default
            # "evaluate" strategy re-runs the datetime WHERE clause in Python
            # against the in-session `sub`, whose SQLite-loaded reset_at is
            # tz-naive - comparing it to the aware `day_start` raises
            # TypeError and 500s EVERY authenticated call after midnight UTC.
            # `sub` is refreshed after each commit, so no sync is needed.
            # Zero-quota tiers: reset the clock without claiming a slot
            if daily_limit == 0:
                session.execute(
                    update(UserSubscription)
                    .where(
                        UserSubscription.user_id == user_id,
                        UserSubscription.daily_quota_reset_at < day_start,
                    )
                    .values(
                        current_period_usage=0,
                        daily_quota_reset_at=day_start,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                session.commit()
                next_reset = day_start + timedelta(days=1)
                retry_after = max(1, int((next_reset - now).total_seconds()))
                raise HTTPException(
                    status_code=402,
                    detail={
                        "code": "quota_exceeded",
                        "message": f"No daily request quota for {tier_key} tier.",
                        "current_usage": 0,
                        "daily_limit": 0,
                        "tier": tier_key,
                        "quota_reset_at": next_reset.isoformat(),
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            result = session.execute(
                update(UserSubscription)
                .where(
                    UserSubscription.user_id == user_id,
                    UserSubscription.daily_quota_reset_at < day_start,
                )
                .values(
                    current_period_usage=1,
                    daily_quota_reset_at=day_start,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
            if result.rowcount > 0:  # ty: ignore[unresolved-attribute]
                return LimitStatus(
                    allowed=True,
                    current_usage=1,
                    daily_limit=daily_limit,
                    remaining=daily_limit - 1,
                    tier=tier_key,
                )
            # Another concurrent request already reset the counter.
            # Refresh sub so it reflects the winner's reset values
            # (current_period_usage, daily_quota_reset_at) before the
            # atomic increment below reads it for the 402 response.
            # Note: the local `reset_at` variable is stale after this
            # point but is not used again -- only `sub` attributes are
            # read from here on.
            session.refresh(sub)
            log.debug(
                "Day-reset race for user {}: another request won; falling through",
                user_id,
            )

        # Atomic increment: only succeeds if usage is still under the limit.
        # Both the happy path (day-reset winner) and the fall-through
        # (day-reset loser) converge here with a fresh `sub` object.
        result = session.execute(
            update(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.current_period_usage < daily_limit,
            )
            .values(
                current_period_usage=UserSubscription.current_period_usage + 1,
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

        if result.rowcount == 0:  # ty: ignore[unresolved-attribute]
            session.refresh(sub)
            reset_ts = sub.daily_quota_reset_at or datetime.now(UTC)
            if reset_ts.tzinfo is None:
                reset_ts = reset_ts.replace(tzinfo=UTC)
            next_reset = reset_ts.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            retry_after = max(1, int((next_reset - datetime.now(UTC)).total_seconds()))
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "quota_exceeded",
                    "message": f"Daily request limit ({daily_limit}) exceeded for {tier_key} tier.",
                    "current_usage": sub.current_period_usage,
                    "daily_limit": daily_limit,
                    "tier": tier_key,
                    "quota_reset_at": next_reset.isoformat(),
                },
                headers={"Retry-After": str(retry_after)},
            )

        session.refresh(sub)
        new_remaining = max(0, daily_limit - sub.current_period_usage)
        return LimitStatus(
            allowed=True,
            current_usage=sub.current_period_usage,
            daily_limit=daily_limit,
            remaining=new_remaining,
            tier=tier_key,
        )
