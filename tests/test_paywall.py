"""Tests for the x402 sell-side paywall (verify + settle + record).

The x402 SDK and facilitator are never contacted: a fake protocol is injected
into the registry so the tests exercise the paywall's own logic - challenge
emission, settle-once bookkeeping, and failure handling - deterministically.
"""

import base64
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services as services_pkg
from api_server.billing.paywall import PaymentRequiredError, enforce_payment
from db.base import Base
from db.models.payment_settlements import PaymentSettlement
from services import ServiceEntry, service
from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)
from tests.test_template import TestTemplate


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _header(payload: dict) -> str:
    """Encode a payment payload the way an x402 client sends X-PAYMENT."""
    return base64.b64encode(json.dumps(payload).encode()).decode()


class _FakeX402:
    """A stand-in x402 protocol whose settle outcome is scripted per test."""

    def __init__(self, settle_result: PaymentResult) -> None:
        self._settle_result = settle_result
        self.settle_calls = 0

    async def initialize(self) -> bool:
        return True

    async def build_payment_requirement(
        self, *, amount, asset, recipient, description=None
    ) -> PaymentRequirement:
        return PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network="base-sepolia",
            asset=asset,
            amount=amount,
            recipient=recipient or "0xserver",
            facilitator_url="https://facilitator.test",
            description=description,
        )

    async def settle_payment(self, payload, requirement) -> PaymentResult:
        self.settle_calls += 1
        return self._settle_result

    def shutdown(self) -> None:
        pass


class _PaywallCase(TestTemplate):
    """Shared setup: in-memory DB bound to the paywall + injected protocol."""

    settle_result = PaymentResult(
        status=PaymentStatus.COMPLETED,
        protocol=PaymentProtocolName.X402,
        transaction_id="0xtxhash",
        raw_response={"payer": "0xpayer", "transaction": "0xtxhash"},
    )

    def setup_method(self) -> None:
        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        self._patcher = patch("api_server.billing.paywall.use_db_session", _ctx)
        self._patcher.start()

        self.fake = _FakeX402(self.settle_result)
        registry = PaymentRegistry.get()
        # _FakeX402 is a structural test double, not a PaymentProtocol subclass.
        registry._protocols[PaymentProtocolName.X402] = self.fake  # ty: ignore[invalid-assignment]
        registry._initialized = True

    def teardown_method(self) -> None:
        self._patcher.stop()
        PaymentRegistry.reset()

    def _rows(self) -> list[PaymentSettlement]:
        session = self.SessionLocal()
        try:
            return session.query(PaymentSettlement).all()
        finally:
            session.close()


class TestPaywallDecorator(TestTemplate):
    def test_price_defaults_to_free(self):
        entry = ServiceEntry(
            name="x",
            description="d",
            input_model=dict,
            output_model=dict,
            func=lambda b: b,
        )
        assert entry.price is None
        assert entry.is_paid is False
        assert entry.asset == "USDC"

    def test_decorator_sets_price(self):
        before = len(services_pkg._registry)
        try:

            @service(
                name="__test_paid_svc",
                description="d",
                input_model=dict,
                output_model=dict,
                price="0.01",
                asset="USDC",
            )
            def _svc(body):
                return body

            entry = next(
                e for e in services_pkg.get_registry() if e.name == "__test_paid_svc"
            )
            assert entry.price == "0.01"
            assert entry.is_paid is True
        finally:
            del services_pkg._registry[before:]


class TestPaywallDisabled(TestTemplate):
    def test_disabled_protocol_runs_free(self):
        # No x402 protocol registered -> paywall inactive -> no raise.
        PaymentRegistry.reset()
        enforce_payment(
            user_id="u1",
            route="paid_svc",
            price="0.001",
            asset="USDC",
            payment_header=None,
        )


class TestPaywallChallenge(_PaywallCase):
    def test_missing_header_raises_challenge(self):
        with pytest.raises(PaymentRequiredError) as exc:
            enforce_payment(
                user_id="u1",
                route="paid_svc",
                price="0.001",
                asset="USDC",
                payment_header=None,
            )
        challenge = exc.value.challenge
        assert challenge["x402Version"] == 1
        accept = challenge["accepts"][0]
        assert accept["maxAmountRequired"] == "0.001"
        assert accept["asset"] == "USDC"
        assert accept["resource"] == "paid_svc"
        # Nothing was settled or recorded.
        assert self.fake.settle_calls == 0
        assert self._rows() == []

    def test_malformed_header_rejected(self):
        with pytest.raises(HTTPException) as exc:
            enforce_payment(
                user_id="u1",
                route="paid_svc",
                price="0.001",
                asset="USDC",
                payment_header="not-base64!!!",
            )
        assert exc.value.status_code == 402
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == "payment_invalid"


class TestPaywallSettlement(_PaywallCase):
    def test_valid_payment_settles_and_records(self):
        enforce_payment(
            user_id="u1",
            route="paid_svc",
            price="0.001",
            asset="USDC",
            payment_header=_header({"scheme": "exact", "nonce": "n1"}),
        )
        assert self.fake.settle_calls == 1
        rows = self._rows()
        assert len(rows) == 1
        assert rows[0].status == "settled"
        assert rows[0].transaction_id == "0xtxhash"
        assert rows[0].payer == "0xpayer"
        assert rows[0].amount == "0.001"

    def test_replay_does_not_double_settle(self):
        header = _header({"scheme": "exact", "nonce": "n1"})
        enforce_payment(
            user_id="u1",
            route="paid_svc",
            price="0.001",
            asset="USDC",
            payment_header=header,
        )
        # Same single-use payment replayed: allowed through, but not re-settled.
        enforce_payment(
            user_id="u1",
            route="paid_svc",
            price="0.001",
            asset="USDC",
            payment_header=header,
        )
        assert self.fake.settle_calls == 1
        assert len(self._rows()) == 1


class TestPaywallRejection(_PaywallCase):
    settle_result = PaymentResult(
        status=PaymentStatus.REJECTED,
        protocol=PaymentProtocolName.X402,
        error="insufficient funds",
    )

    def test_rejected_payment_402_and_releases_claim(self):
        with pytest.raises(HTTPException) as exc:
            enforce_payment(
                user_id="u1",
                route="paid_svc",
                price="0.001",
                asset="USDC",
                payment_header=_header({"scheme": "exact", "nonce": "bad"}),
            )
        assert exc.value.status_code == 402
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == "payment_invalid"
        # Claim released so a corrected retry can re-claim the (new) payment.
        assert self._rows() == []


class TestPaywallModel(TestTemplate):
    def test_tablename_and_pk(self):
        assert PaymentSettlement.__tablename__ == "payment_settlements"
        pk = {c.name for c in PaymentSettlement.__table__.primary_key}
        assert pk == {"payment_hash"}
