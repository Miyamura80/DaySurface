"""x402 payment protocol implementation wrapping the Coinbase x402 SDK."""

import os
import threading
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from typing import Any

from loguru import logger as log

from common.config_models import X402ProtocolConfig
from src.payments.base import PaymentProtocol
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)

# How long a signed authorization stays valid; the client sets validBefore from it.
_MAX_TIMEOUT_SECONDS = 300

# USDC per x402 v1 network name: contract, EIP-712 domain, decimals. These are
# the only networks (and USDC the only asset) this deployment can charge on;
# any other config fails closed with a 500 before a challenge goes out. Values
# from the SDK's V1_DEFAULT_ASSETS, which we can't import without the
# ``x402[evm]`` extra and its Ethereum signing stack, used only by clients.
_USDC: dict[str, dict[str, Any]] = {
    "base": {
        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "name": "USD Coin",
        "version": "2",
        "decimals": 6,
    },
    "base-sepolia": {
        "address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "name": "USDC",
        "version": "2",
        "decimals": 6,
    },
}


def v1_accepts(req: PaymentRequirement, *, resource: str) -> dict[str, Any]:
    """The x402 v1 ``accepts`` entry for ``req``, in wire (camelCase) form.

    One source for both the 402 challenge the client signs against and the
    requirements the facilitator checks the signature with, so the two cannot
    disagree. Our requirement carries the human price ("0.001") and token
    symbol; the exact-EVM scheme wants the token's contract address, the amount
    in its smallest unit, and the EIP-712 domain the client signs with.

    Raises ValueError for an asset other than USDC, a network with no known
    USDC contract, or an amount USDC cannot represent exactly.
    """
    if req.asset != "USDC":
        raise ValueError(
            f"x402: unsupported asset {req.asset!r}; only USDC is supported"
        )
    token = _USDC.get(req.network)
    if token is None:
        raise ValueError(f"x402: no known token contract for network {req.network!r}")
    try:
        # Inexact trapped: a price with more digits than the context keeps
        # must be refused, never rounded into a different charge.
        with localcontext() as ctx:
            ctx.traps[Inexact] = True
            atomic = Decimal(req.amount).scaleb(token["decimals"])
    except (InvalidOperation, Inexact) as exc:
        raise ValueError(f"x402: amount {req.amount!r} is not an exact number") from exc
    if (
        not atomic.is_finite()
        or atomic <= 0
        or atomic != atomic.to_integral_value()
        # The transfer authorization's value is a uint256.
        or atomic > (1 << 256) - 1
    ):
        raise ValueError(
            f"x402: amount {req.amount!r} is not payable: it must be a positive "
            f"whole number of the token's smallest unit ({token['decimals']} "
            "decimals) that fits in a uint256"
        )
    return {
        "scheme": "exact",
        "network": req.network,
        "maxAmountRequired": str(int(atomic)),
        "resource": resource,
        "description": req.description or "",
        "payTo": req.recipient,
        "maxTimeoutSeconds": _MAX_TIMEOUT_SECONDS,
        "asset": token["address"],
        "extra": {"name": token["name"], "version": token["version"]},
    }


class X402Protocol(PaymentProtocol):
    """x402 stablecoin payment protocol via Coinbase SDK.

    Follows the lazy-init pattern from api_server/billing/stripe_config.py:
    positive init results are cached, negative results are not (allows
    env var injection without restart).
    """

    def __init__(self, config: X402ProtocolConfig) -> None:
        self._config = config
        self._wallet_address: str = ""
        self._private_key: str = ""
        self._initialized = False
        self._lock = threading.Lock()

    @property
    def name(self) -> PaymentProtocolName:
        return PaymentProtocolName.X402

    @property
    def is_available(self) -> bool:
        return self._initialized

    async def initialize(self) -> bool:
        """Check the x402 SDK is importable and the operator wallet is configured.

        Returns True on success. Negative results are never cached
        so missing env vars can be provided later.
        """
        if self._initialized:
            return True

        with self._lock:
            if self._initialized:
                return True

            try:
                # Lazy by design (see class docstring): the x402 SDK stays off
                # the module import path so a missing/broken SDK is caught here
                # and retried, instead of failing at import time.
                import x402.http  # noqa: F401, PLC0415

                # Pre-flight: wallet address is used as the payment
                # recipient in build_payment_requirement(). The private
                # key is validated here to ensure the operator has
                # configured credentials, but is not passed to the SDK:
                # the facilitator handles on-chain settlement independently.
                wallet = os.getenv(self._config.wallet_address_env)
                private_key = os.getenv(self._config.private_key_env)

                if not wallet:
                    log.debug(
                        "x402: missing env var {}",
                        self._config.wallet_address_env,
                    )
                    return False

                if not private_key:
                    log.debug(
                        "x402: missing env var {}",
                        self._config.private_key_env,
                    )
                    return False

                self._wallet_address = wallet
                self._private_key = private_key
                self._initialized = True
                log.info(
                    "x402 protocol initialized (network={}, testnet={})",
                    self._config.network,
                    self._config.testnet,
                )
                return True

            except Exception as exc:  # noqa: BLE001
                # x402 SDK init can fail in many ways (env, key derivation, network
                # discovery); a failed init must not crash the host, so we retry next call.
                log.warning("x402 init failed; will retry next call: {}", exc)
                return False

    async def build_payment_requirement(
        self,
        *,
        amount: str,
        asset: str,
        recipient: str,
        description: str | None = None,
    ) -> PaymentRequirement:
        """Build a PaymentRequirement for a 402 response.

        Rejects any ``asset`` this deployment cannot actually settle. The SDK
        conversion (:meth:`_to_sdk_requirements`) only carries scheme/pay_to/
        price/network, so settlement always uses the configured default asset;
        advertising a different token in the 402 challenge would hand the client
        a payment it can never fulfil, so we fail fast instead.
        """
        if not await self.initialize():
            raise RuntimeError("x402 protocol not initialized")

        resolved_asset = asset or self._config.default_asset
        if resolved_asset != self._config.default_asset:
            raise ValueError(
                f"unsupported asset {resolved_asset!r}: this x402 deployment "
                f"settles {self._config.default_asset!r} on {self._config.network}"
            )

        return PaymentRequirement(
            protocol=PaymentProtocolName.X402,
            network=self._config.network,
            asset=resolved_asset,
            amount=amount or self._config.default_amount,
            recipient=recipient or self._wallet_address,
            facilitator_url=self._config.facilitator_url,
            description=description,
            extra={
                "testnet": self._config.testnet,
            },
        )

    async def verify_payment(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirement,
    ) -> PaymentResult:
        """Verify an incoming payment using the x402 facilitator (no settlement)."""
        return await self._run_facilitator(payload, requirement, settle=False)

    async def settle_payment(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirement,
    ) -> PaymentResult:
        """Verify then settle an incoming payment via the x402 facilitator.

        The resource-server contract is verify-then-settle: we first ask the
        facilitator whether the signed authorization is valid, and only then
        ask it to broadcast/settle it on-chain. A rejected verification never
        reaches settlement.
        """
        return await self._run_facilitator(payload, requirement, settle=True)

    async def _run_facilitator(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirement,
        *,
        settle: bool,
    ) -> PaymentResult:
        """Verify (and optionally settle) a payment through the facilitator.

        Shared by :meth:`verify_payment` and :meth:`settle_payment` so the two
        never drift. Owns the facilitator client's lifecycle (closed in a
        ``finally``) and classifies outcomes consistently:

        - verification says the payment is invalid -> ``REJECTED`` (client's
          fault; the paywall maps this to HTTP 402);
        - verification passes but settlement fails (transaction/simulation/
          facilitator error) -> ``FAILED`` (infrastructure; maps to HTTP 502);
        - any SDK/transport exception -> ``FAILED``.
        """
        if not await self.initialize():
            return PaymentResult(
                status=PaymentStatus.FAILED,
                protocol=PaymentProtocolName.X402,
                error="x402 protocol not initialized",
            )

        try:
            # Lazy by design: keep the x402 SDK off the module import path
            # (see class docstring); failures surface as PaymentResult below.
            from x402.http import HTTPFacilitatorClient  # noqa: PLC0415

            facilitator_url = (
                requirement.facilitator_url or self._config.facilitator_url
            )
            facilitator = HTTPFacilitatorClient({"url": facilitator_url})
            try:
                x402_payload = self._to_sdk_payload(payload)
                x402_requirements = self._to_sdk_requirements(requirement)

                verify = await facilitator.verify(x402_payload, x402_requirements)
                if not verify.is_valid:
                    return PaymentResult(
                        status=PaymentStatus.REJECTED,
                        protocol=PaymentProtocolName.X402,
                        error=verify.invalid_reason or verify.invalid_message,
                        raw_response=verify.model_dump(),
                    )

                if not settle:
                    return PaymentResult(
                        status=PaymentStatus.COMPLETED,
                        protocol=PaymentProtocolName.X402,
                        transaction_id=verify.payer,
                        raw_response=verify.model_dump(),
                    )

                settled = await facilitator.settle(x402_payload, x402_requirements)
                if settled.success:
                    return PaymentResult(
                        status=PaymentStatus.COMPLETED,
                        protocol=PaymentProtocolName.X402,
                        transaction_id=settled.transaction or settled.payer,
                        raw_response=settled.model_dump(),
                    )
                # Verification already passed, so a settlement failure is an
                # execution/facilitator error, not an invalid payment: FAILED.
                return PaymentResult(
                    status=PaymentStatus.FAILED,
                    protocol=PaymentProtocolName.X402,
                    error=settled.error_reason or settled.error_message,
                    raw_response=settled.model_dump(),
                )
            finally:
                # The facilitator owns an async HTTP client; close it so we
                # don't leak connection-pool resources under load. Closing must
                # never override the payment result, so a close failure is
                # logged and swallowed rather than masking a settled payment.
                try:
                    await facilitator.aclose()
                except Exception as close_exc:  # noqa: BLE001
                    # Defensive: aclose failure is non-fatal to the payment.
                    log.warning("x402 facilitator close failed: {}", close_exc)

        except Exception as exc:  # noqa: BLE001
            # External SDK boundary: any failure (HTTP, signing, broadcast) must
            # surface as a structured PaymentResult, not propagate to the caller.
            log.error(
                "x402 payment {} failed: {}",
                "settlement" if settle else "verification",
                exc,
            )
            return PaymentResult(
                status=PaymentStatus.FAILED,
                protocol=PaymentProtocolName.X402,
                error=str(exc),
            )

    def _to_sdk_payload(self, payload: PaymentPayload) -> Any:
        """Convert our PaymentPayload to x402 SDK's PaymentPayload."""
        # Lazy by design: keep the x402 SDK off the module import path
        # (see class docstring).
        from x402 import parse_payment_payload  # noqa: PLC0415

        return parse_payment_payload(payload.raw)

    def _to_sdk_requirements(self, req: PaymentRequirement) -> Any:
        """Convert our PaymentRequirement to the SDK's v1 PaymentRequirements.

        v1 because that is the version the paywall's 402 challenge advertises,
        so it is the version clients sign. The paywall sets the description to
        the route, which is also the challenge's resource.
        """
        # Lazy by design: keep the x402 SDK off the module import path
        # (see class docstring).
        from x402.schemas.v1 import PaymentRequirementsV1  # noqa: PLC0415

        return PaymentRequirementsV1.model_validate(
            v1_accepts(req, resource=req.description or "")
        )

    def shutdown(self) -> None:
        """Reset protocol state and clear key material."""
        with self._lock:
            self._initialized = False
            self._wallet_address = ""
            self._private_key = ""
