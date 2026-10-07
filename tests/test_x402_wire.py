"""The x402 challenge and the facilitator call agree, in the SDK's real types.

The other payment tests stand in for the SDK conversion. These run it, so a
requirement the SDK cannot build, or a challenge that disagrees with what the
facilitator is asked to check, fails here instead of on the first real payment.
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from x402.schemas.v1 import PaymentPayloadV1, PaymentRequirementsV1

from api_server.billing.paywall import PaymentRequiredError, enforce_payment
from common.config_models import X402ProtocolConfig
from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)
from src.payments.x402.protocol import X402Protocol, v1_accepts
from tests.test_template import TestTemplate

_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_WALLET = "0x1111111111111111111111111111111111111111"
_ENV = {"X402_WALLET_ADDRESS": _WALLET, "X402_PRIVATE_KEY": "0xkey"}


def _requirement(**overrides: str) -> PaymentRequirement:
    return replace(
        PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network="base-sepolia",
            asset="USDC",
            amount="0.001",
            recipient=_WALLET,
            facilitator_url="https://facilitator.test",
            description="paid_svc",
        ),
        **overrides,
    )


def _signed_v1_payload() -> dict:
    # Shape of what an x402 v1 exact-EVM client puts in X-PAYMENT.
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": "base-sepolia",
        "payload": {
            "signature": "0x" + "ab" * 65,
            "authorization": {
                "from": "0x2222222222222222222222222222222222222222",
                "to": _WALLET,
                "value": "1000",
                "validAfter": "0",
                "validBefore": "9999999999",
                "nonce": "0x" + "00" * 32,
            },
        },
    }


class TestV1Accepts(TestTemplate):
    def test_prices_in_the_tokens_smallest_unit_at_its_contract(self):
        accepts = v1_accepts(_requirement(), resource="paid_svc")
        assert accepts["scheme"] == "exact"
        assert accepts["network"] == "base-sepolia"
        assert accepts["maxAmountRequired"] == "1000"
        assert accepts["asset"] == _BASE_SEPOLIA_USDC
        assert accepts["payTo"] == _WALLET
        assert accepts["resource"] == "paid_svc"
        # The EIP-712 domain the client signs the transfer authorization with.
        assert accepts["extra"] == {"name": "USDC", "version": "2"}

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"network": "base-sepolia-typo"}, "no known token contract"),
            ({"amount": "0.0000001"}, "smallest unit"),
            ({"amount": "0"}, "smallest unit"),
            ({"amount": "abc"}, "not an exact number"),
            ({"amount": "0.001" + "0" * 40 + "1"}, "not an exact number"),
            ({"amount": "Infinity"}, "smallest unit"),
            ({"amount": "2e71"}, "smallest unit"),
            ({"asset": "USDT"}, "only USDC"),
        ],
    )
    def test_refuses_what_no_client_could_pay(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            v1_accepts(_requirement(**overrides), resource="paid_svc")


class TestFacilitatorGetsWhatTheClientSigned(TestTemplate):
    def _verify(self) -> tuple[MagicMock, PaymentResult]:
        proto = X402Protocol(X402ProtocolConfig())
        facilitator = MagicMock()
        verified = MagicMock(is_valid=True, payer="0xpayer")
        verified.model_dump.return_value = {"is_valid": True}
        facilitator.configure_mock(
            verify=AsyncMock(return_value=verified), aclose=AsyncMock()
        )
        payload = PaymentPayload(
            protocol=PaymentProtocolName.X402, raw=_signed_v1_payload()
        )
        with (
            patch.dict("os.environ", _ENV),
            patch("x402.http.HTTPFacilitatorClient", return_value=facilitator),
        ):
            result = asyncio.run(proto.verify_payment(payload, _requirement()))
        return facilitator, result

    def test_verify_reaches_the_facilitator_with_sdk_v1_models(self):
        facilitator, result = self._verify()
        assert result.status == PaymentStatus.COMPLETED
        sent_payload, sent_requirements = facilitator.verify.call_args.args
        assert isinstance(sent_payload, PaymentPayloadV1)
        assert isinstance(sent_requirements, PaymentRequirementsV1)

    def test_facilitator_checks_the_same_terms_the_challenge_offered(self):
        facilitator, _ = self._verify()
        sent = facilitator.verify.call_args.args[1].model_dump(
            by_alias=True, exclude_none=True
        )
        assert sent == v1_accepts(_requirement(), resource="paid_svc")


class _Proto:
    """Real requirement building, no facilitator: enough to reach the challenge."""

    def __init__(self, network: str) -> None:
        self._real = X402Protocol(X402ProtocolConfig(network=network))

    async def initialize(self) -> bool:
        with patch.dict("os.environ", _ENV):
            return await self._real.initialize()

    async def build_payment_requirement(self, **kwargs) -> PaymentRequirement:
        return await self._real.build_payment_requirement(**kwargs)


class TestPaywallChallengeIsPayable(TestTemplate):
    def _enforce(self, network: str) -> None:
        registry = MagicMock()
        registry.get_protocol.return_value = _Proto(network)
        with patch.object(PaymentRegistry, "get", return_value=registry):
            enforce_payment(
                user_id="u1",
                route="paid_svc",
                price="0.001",
                asset="USDC",
                payment_header=None,
            )

    def test_challenge_is_the_v1_accepts_entry(self):
        with pytest.raises(PaymentRequiredError) as caught:
            self._enforce("base-sepolia")
        (accept,) = caught.value.challenge["accepts"]
        # Exactly the terms the facilitator will check, plus where to settle.
        expected = v1_accepts(
            _requirement(facilitator_url="https://x402.org/facilitator"),
            resource="paid_svc",
        )
        assert accept == expected | {"facilitator": "https://x402.org/facilitator"}

    def test_unpayable_network_fails_closed(self):
        with pytest.raises(HTTPException) as caught:
            self._enforce("not-a-network")
        assert caught.value.status_code == 500
