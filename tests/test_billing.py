"""Tests for billing: LimitStatus, ensure_daily_limit, subscription model, Stripe graceful degradation."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.billing.limits import LimitStatus, ensure_daily_limit
from api_server.billing.stripe_config import (
    get_included_units,
    get_meter_event_name,
    get_stripe_price_id,
    get_webhook_secret,
)
from db.base import Base
from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription
from tests.test_template import TestTemplate


def _make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@contextmanager
def _mock_use_db_session(session):
    """Yield the pre-built in-memory session for use_db_session patching."""
    yield session


class TestLimitStatus(TestTemplate):
    def test_limit_status_dataclass(self):
        status = LimitStatus(
            allowed=True,
            current_usage=5,
            daily_limit=100,
            remaining=95,
            tier="free_tier",
        )
        assert status.allowed is True
        assert status.remaining == 95


class TestEnsureDailyLimit(TestTemplate):
    def test_creates_subscription_if_missing(self):
        session = _make_session()
        with patch(
            "api_server.billing.limits.use_db_session",
            return_value=_mock_use_db_session(session),
        ):
            status = ensure_daily_limit("new-user")
        assert status.allowed is True
        assert status.current_usage == 1  # Incremented after check
        assert status.tier == SubscriptionTier.FREE.value

    def test_enforces_limit(self):
        session = _make_session()
        # Create a subscription at the limit
        sub = UserSubscription(
            user_id="limit-user",
            subscription_tier=SubscriptionTier.FREE.value,
            current_period_usage=10000,
            current_period_start=datetime.now(UTC),
        )
        session.add(sub)
        session.commit()

        with (
            patch(
                "api_server.billing.limits.use_db_session",
                return_value=_mock_use_db_session(session),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            ensure_daily_limit("limit-user")
        assert exc_info.value.status_code == 402

    def test_day_rollover_with_naive_reset_at(self):
        # Regression: SQLite stores daily_quota_reset_at tz-naive; the
        # day-reset update's default "evaluate" sync strategy re-ran the
        # datetime WHERE clause in Python (naive vs aware -> TypeError),
        # 500ing every authenticated call after midnight UTC.
        session = _make_session()
        yesterday = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        sub = UserSubscription(
            user_id="rollover-user",
            subscription_tier=SubscriptionTier.FREE.value,
            current_period_usage=9999,
            current_period_start=datetime.now(UTC),
            daily_quota_reset_at=yesterday,
        )
        session.add(sub)
        session.commit()

        with patch(
            "api_server.billing.limits.use_db_session",
            return_value=_mock_use_db_session(session),
        ):
            status = ensure_daily_limit("rollover-user")
        assert status.allowed is True
        assert status.current_usage == 1  # fresh day, counter reset

    def test_allows_under_limit(self):
        session = _make_session()
        sub = UserSubscription(
            user_id="ok-user",
            subscription_tier=SubscriptionTier.FREE.value,
            current_period_usage=50,
            current_period_start=datetime.now(UTC),
        )
        session.add(sub)
        session.commit()

        with patch(
            "api_server.billing.limits.use_db_session",
            return_value=_mock_use_db_session(session),
        ):
            status = ensure_daily_limit("ok-user")
        assert status.allowed is True
        assert status.current_usage == 51  # Incremented


class TestSubscriptionModel(TestTemplate):
    def test_create_subscription(self):
        session = _make_session()
        sub = UserSubscription(
            user_id="test-user",
            subscription_tier=SubscriptionTier.PLUS.value,
        )
        session.add(sub)
        session.commit()

        result = session.query(UserSubscription).filter_by(user_id="test-user").first()
        assert result is not None
        assert result.subscription_tier == SubscriptionTier.PLUS.value
        assert result.is_active is True

    def test_default_values(self):
        session = _make_session()
        sub = UserSubscription(user_id="default-user")
        session.add(sub)
        session.commit()
        session.refresh(sub)

        assert sub.subscription_tier == SubscriptionTier.FREE.value
        assert sub.current_period_usage == 0
        assert sub.payment_failure_count == 0


class TestStripeGracefulDegradation(TestTemplate):
    def test_stripe_config_functions_exist(self):
        """Stripe config module exposes the expected API."""
        # These should not raise when called (graceful degradation)
        assert isinstance(get_stripe_price_id(), str)
        assert isinstance(get_meter_event_name(), str)
        assert isinstance(get_included_units(), int)
        # Webhook secret may be None if not configured
        assert get_webhook_secret() is None or isinstance(get_webhook_secret(), str)
