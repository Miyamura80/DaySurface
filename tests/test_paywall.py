"""Tests for the x402 sell-side paywall (verify + settle + record).

The x402 SDK and facilitator are never contacted: a fake protocol is injected
into the registry so the tests exercise the paywall's own logic - challenge
emission, settle-once bookkeeping, replay binding, and failure handling -
deterministically.
"""

import asyncio
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
from common import global_config
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
from src.payments.x402.protocol import X402Protocol
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


def _detail_code(exc: HTTPException) -> str | None:
    """Extract the error ``code`` from an HTTPException whose detail is a dict."""
    detail = exc.detail
    return detail.get("code") if isinstance(detail, dict) else None


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
        finally:
            del services_pkg._registry[before:]

    @pytest.mark.parametrize(
        "bad_price", ["0", "-1", "", "abc", "1.2.3", "NaN", "Infinity", "-Infinity"]
    )
    def test_invalid_price_rejected_at_registration(self, bad_price):
        with pytest.raises(ValueError):

            @service(
                name="__test_bad_price",
                description="d",
                input_model=dict,
                output_model=dict,
                price=bad_price,
            )
            def _svc(body):
                return body


class TestPaywallDisabled(TestTemplate):
    def test_disabled_protocol_runs_free(self):
        # Pin the disabled state deterministically regardless of the ambient
        # x402 config: an initialized registry with no protocols means the
        # paywall is inactive, so enforce_payment returns False (caller applies
        # the free quota) instead of raising.
        registry = PaymentRegistry.get()
        registry._protocols.clear()
        registry._initialized = True
        try:
            charged = enforce_payment(
                user_id="u1",
                route="paid_svc",
                price="0.001",
                asset="USDC",
                payment_header=None,
            )
            assert charged is False
        finally:
            PaymentRegistry.reset()


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
        assert _detail_code(exc.value) == "payment_invalid"


class TestPaywallSettlement(_PaywallCase):
    def test_valid_payment_settles_and_records(self):
        charged = enforce_payment(
            user_id="u1",
            route="paid_svc",
            price="0.001",
            asset="USDC",
            payment_header=_header({"scheme": "exact", "nonce": "n1"}),
        )
        assert charged is True
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
        # Same single-use payment replayed on the SAME request: allowed through,
        # but not re-settled.
        charged = enforce_payment(
            user_id="u1",
            route="paid_svc",
            price="0.001",
            asset="USDC",
            payment_header=header,
        )
        assert charged is True
        assert self.fake.settle_calls == 1
        assert len(self._rows()) == 1

    def test_replay_on_different_route_rejected(self):
        header = _header({"scheme": "exact", "nonce": "n1"})
        enforce_payment(
            user_id="u1",
            route="cheap_svc",
            price="0.001",
            asset="USDC",
            payment_header=header,
        )
        # Reusing the settled payment on a different (pricier) route must not be
        # honored for free.
        with pytest.raises(HTTPException) as exc:
            enforce_payment(
                user_id="u1",
                route="pricey_svc",
                price="0.010",
                asset="USDC",
                payment_header=header,
            )
        assert exc.value.status_code == 409
        assert _detail_code(exc.value) == "payment_conflict"
        assert self.fake.settle_calls == 1

    def test_mutating_replay_rejected(self):
        header = _header({"scheme": "exact", "nonce": "n1"})
        enforce_payment(
            user_id="u1",
            route="charge_svc",
            price="0.001",
            asset="USDC",
            payment_header=header,
            mutating=True,
        )
        # A settled payment must never re-drive a mutating operation.
        with pytest.raises(HTTPException) as exc:
            enforce_payment(
                user_id="u1",
                route="charge_svc",
                price="0.001",
                asset="USDC",
                payment_header=header,
                mutating=True,
            )
        assert exc.value.status_code == 409
        assert _detail_code(exc.value) == "payment_replayed"
        assert self.fake.settle_calls == 1


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
        assert _detail_code(exc.value) == "payment_invalid"
        # Claim released so a corrected retry can re-claim the (new) payment.
        assert self._rows() == []


class TestPaywallSettlementFailure(_PaywallCase):
    settle_result = PaymentResult(
        status=PaymentStatus.FAILED,
        protocol=PaymentProtocolName.X402,
        error="facilitator timeout",
    )

    def test_settlement_failure_is_502_not_402(self):
        # A valid payment whose settlement fails on infrastructure is a server
        # error (502), not a client payment error (402).
        with pytest.raises(HTTPException) as exc:
            enforce_payment(
                user_id="u1",
                route="paid_svc",
                price="0.001",
                asset="USDC",
                payment_header=_header({"scheme": "exact", "nonce": "n1"}),
            )
        assert exc.value.status_code == 502
        assert _detail_code(exc.value) == "settlement_failed"
        assert self._rows() == []


class TestX402AssetRejection(TestTemplate):
    def test_unsupported_asset_rejected(self):
        # The SDK settles only its configured default asset, so building a
        # requirement for a different token must fail rather than advertise an
        # unpayable challenge.
        proto = X402Protocol(global_config.payments.x402)
        proto._initialized = True
        proto._wallet_address = "0xserver"
        with pytest.raises(ValueError):
            asyncio.run(
                proto.build_payment_requirement(
                    amount="0.001",
                    asset="NOTUSDC",
                    recipient="",
                )
            )


class TestPaywallModel(TestTemplate):
    def test_tablename_and_pk(self):
        assert PaymentSettlement.__tablename__ == "payment_settlements"
        pk = {c.name for c in PaymentSettlement.__table__.primary_key}
        assert pk == {"payment_hash"}
