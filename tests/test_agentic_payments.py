"""Tests for agentic payment protocol core abstractions."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from common import global_config
from common.config_models import AgenticPaymentsConfig, X402ProtocolConfig
from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)
from src.payments.x402.protocol import X402Protocol
from tests.test_template import TestTemplate

_ENV_VARS = {
    "X402_WALLET_ADDRESS": "0x1234567890abcdef",
    "X402_PRIVATE_KEY": "0xdeadbeef",
}


def _make_initialized_protocol() -> X402Protocol:
    """Create an X402Protocol that is initialized (with mocked env vars)."""
    proto = X402Protocol(X402ProtocolConfig())
    with patch.dict("os.environ", _ENV_VARS):
        asyncio.run(proto.initialize())
    return proto


class TestPaymentTypes(TestTemplate):
    def test_protocol_name_values(self):
        assert PaymentProtocolName.X402 == "x402"
        assert PaymentProtocolName.MPP == "mpp"
        assert PaymentProtocolName.ACP == "acp"

    def test_payment_status_values(self):
        assert PaymentStatus.COMPLETED == "completed"
        assert PaymentStatus.FAILED == "failed"
        assert PaymentStatus.REJECTED == "rejected"
        assert PaymentStatus.PENDING == "pending"

    def test_payment_requirement_frozen(self):
        req = PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network="base-sepolia",
            asset="USDC",
            amount="0.001",
            recipient="0xabc",
        )
        assert req.protocol == "x402"
        assert req.network == "base-sepolia"
        assert req.facilitator_url is None
        assert req.extra == {}

    def test_payment_result_defaults(self):
        result = PaymentResult(status=PaymentStatus.COMPLETED)
        assert result.transaction_id is None
        assert result.error is None
        assert result.raw_response == {}

    def test_payment_payload_defaults(self):
        payload = PaymentPayload(protocol=PaymentProtocolName.X402)
        assert payload.raw == {}
        assert payload.header_value == ""


class TestPaymentRegistry(TestTemplate):
    def setup_method(self):
        PaymentRegistry.reset()

    def test_singleton(self):
        r1 = PaymentRegistry.get()
        r2 = PaymentRegistry.get()
        assert r1 is r2

    def test_no_protocols_when_all_disabled(self):
        registry = PaymentRegistry.get()
        registry.initialize()
        assert registry.list_enabled() == []
        assert registry.is_any_enabled() is False

    def test_reset_clears_singleton(self):
        r1 = PaymentRegistry.get()
        PaymentRegistry.reset()
        r2 = PaymentRegistry.get()
        assert r1 is not r2

    def test_get_protocol_returns_none_for_unknown(self):
        registry = PaymentRegistry.get()
        assert registry.get_protocol("nonexistent") is None

    def test_registers_x402_when_enabled(self):
        """Tier A: Registry with x402 enabled via config override."""
        PaymentRegistry.reset()
        registry = PaymentRegistry.get()

        mock_cfg = AgenticPaymentsConfig(
            x402=X402ProtocolConfig(enabled=True),
        )

        original = global_config.payments
        try:
            object.__setattr__(global_config, "payments", mock_cfg)
            registry.initialize()
        finally:
            object.__setattr__(global_config, "payments", original)

        assert registry.is_any_enabled() is True
        assert "x402" in registry.list_enabled()
        proto = registry.get_protocol("x402")
        assert proto is not None
        assert proto.name == PaymentProtocolName.X402

    def test_initialize_is_idempotent(self):
        """Double-init should not duplicate protocols."""
        PaymentRegistry.reset()
        registry = PaymentRegistry.get()

        mock_cfg = AgenticPaymentsConfig(
            x402=X402ProtocolConfig(enabled=True),
        )

        original = global_config.payments
        try:
            object.__setattr__(global_config, "payments", mock_cfg)
            registry.initialize()
            registry.initialize()
        finally:
            object.__setattr__(global_config, "payments", original)

        assert len(registry.list_enabled()) == 1


class TestX402Protocol(TestTemplate):
    def test_name(self):
        proto = X402Protocol(X402ProtocolConfig())
        assert proto.name == PaymentProtocolName.X402

    def test_not_available_before_init(self):
        proto = X402Protocol(X402ProtocolConfig())
        assert proto.is_available is False

    def test_init_fails_without_env_vars(self):
        proto = X402Protocol(X402ProtocolConfig())
        result = asyncio.run(proto.initialize())
        assert result is False
        assert proto.is_available is False

    def test_init_succeeds_with_env_vars(self):
        proto = X402Protocol(X402ProtocolConfig())
        with patch.dict("os.environ", _ENV_VARS):
            result = asyncio.run(proto.initialize())
            assert result is True
            assert proto.is_available is True

    def test_build_requirement(self):
        proto = X402Protocol(X402ProtocolConfig())
        with patch.dict("os.environ", _ENV_VARS):

            async def _run():
                await proto.initialize()
                return await proto.build_payment_requirement(
                    amount="0.01",
                    asset="USDC",
                    recipient="0xrecipient",
                    description="test payment",
                )

            req = asyncio.run(_run())
            assert req.protocol == PaymentProtocolName.X402
            assert req.amount == "0.01"
            assert req.asset == "USDC"
            assert req.recipient == "0xrecipient"
            assert req.description == "test payment"

    def test_shutdown_resets_state(self):
        proto = X402Protocol(X402ProtocolConfig())
        with patch.dict("os.environ", _ENV_VARS):
            asyncio.run(proto.initialize())
            assert proto.is_available is True
            proto.shutdown()
            assert proto.is_available is False


class TestX402VerifyPayment(TestTemplate):
    """Tier S: Test verify_payment branching with mocked facilitator responses.

    We mock at the HTTPFacilitatorClient.verify() level. This tests OUR
    branching logic (is_valid -> COMPLETED, !is_valid -> REJECTED with
    correct error, exception -> FAILED) without re-testing the SDK.
    """

    def _make_verify_response(
        self, *, is_valid=True, payer=None, invalid_reason=None, invalid_message=None
    ):
        resp = MagicMock()
        resp.is_valid = is_valid
        resp.payer = payer
        resp.invalid_reason = invalid_reason
        resp.invalid_message = invalid_message
        resp.model_dump.return_value = {
            "is_valid": is_valid,
            "payer": payer,
            "invalid_reason": invalid_reason,
            "invalid_message": invalid_message,
        }
        return resp

    def _requirement(self):
        return PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network="base-sepolia",
            asset="USDC",
            amount="0.001",
            recipient="0xrecipient",
            facilitator_url="https://x402.org/facilitator",
        )

    def _payload(self):
        return PaymentPayload(
            protocol=PaymentProtocolName.X402,
            raw={"test": "data"},
            header_value="base64encodedpayment",
        )

    def _run_verify(self, proto, mock_verify_return=None, mock_verify_side_effect=None):
        """Run verify_payment with mocked facilitator."""

        async def _run():
            with (
                patch("x402.http.HTTPFacilitatorClient") as mock_fc_cls,
                patch.object(proto, "_to_sdk_payload", return_value=MagicMock()),
                patch.object(proto, "_to_sdk_requirements", return_value=MagicMock()),
            ):
                instance = MagicMock()
                if mock_verify_side_effect:
                    instance.verify = AsyncMock(side_effect=mock_verify_side_effect)
                else:
                    instance.verify = AsyncMock(return_value=mock_verify_return)
                mock_fc_cls.return_value = instance
                return await proto.verify_payment(self._payload(), self._requirement())

        return asyncio.run(_run())

    def test_valid_payment_returns_completed(self):
        """Facilitator says valid -> COMPLETED with payer as transaction_id."""
        proto = _make_initialized_protocol()
        mock_resp = self._make_verify_response(is_valid=True, payer="0xpayer123")

        result = self._run_verify(proto, mock_verify_return=mock_resp)
        assert result.status == PaymentStatus.COMPLETED
        assert result.transaction_id == "0xpayer123"
        assert result.protocol == PaymentProtocolName.X402
        assert result.raw_response["is_valid"] is True

    def test_insufficient_balance_returns_rejected(self):
        """Facilitator says insufficient balance -> REJECTED with error."""
        proto = _make_initialized_protocol()
        mock_resp = self._make_verify_response(
            is_valid=False,
            invalid_reason="ERR_INSUFFICIENT_BALANCE",
            invalid_message="Sender balance too low",
        )

        result = self._run_verify(proto, mock_verify_return=mock_resp)
        assert result.status == PaymentStatus.REJECTED
        assert result.error == "ERR_INSUFFICIENT_BALANCE"
        assert result.protocol == PaymentProtocolName.X402

    def test_nonce_replay_returns_rejected(self):
        """Facilitator detects nonce reuse -> REJECTED."""
        proto = _make_initialized_protocol()
        mock_resp = self._make_verify_response(
            is_valid=False, invalid_reason="ERR_NONCE_ALREADY_USED"
        )

        result = self._run_verify(proto, mock_verify_return=mock_resp)
        assert result.status == PaymentStatus.REJECTED
        assert result.error == "ERR_NONCE_ALREADY_USED"

    def test_invalid_with_message_only_uses_message(self):
        """When invalid_reason is None, falls back to invalid_message."""
        proto = _make_initialized_protocol()
        mock_resp = self._make_verify_response(
            is_valid=False,
            invalid_reason=None,
            invalid_message="Signature verification failed",
        )

        result = self._run_verify(proto, mock_verify_return=mock_resp)
        assert result.status == PaymentStatus.REJECTED
        assert result.error == "Signature verification failed"

    def test_facilitator_exception_returns_failed(self):
        """SDK raises exception -> FAILED with error string, no crash."""
        proto = _make_initialized_protocol()

        result = self._run_verify(
            proto,
            mock_verify_side_effect=ConnectionError("Facilitator unreachable"),
        )
        assert result.status == PaymentStatus.FAILED
        assert "Facilitator unreachable" in result.error
        assert result.protocol == PaymentProtocolName.X402

    def test_verify_when_not_initialized_returns_failed(self):
        """Calling verify_payment on uninitialized protocol -> FAILED."""
        proto = X402Protocol(X402ProtocolConfig())
        result = asyncio.run(proto.verify_payment(self._payload(), self._requirement()))
        assert result.status == PaymentStatus.FAILED
        assert result.error is not None
        assert "not initialized" in result.error
