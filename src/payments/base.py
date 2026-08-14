"""Abstract base class for agentic payment protocols."""

from abc import ABC, abstractmethod

from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentResult,
    PaymentStatus,
)


class PaymentProtocol(ABC):
    """Interface that every agentic payment protocol must implement.

    Follows the lazy-init pattern from api_server/billing/stripe_config.py:
    - initialize() caches positive results, never caches negative
    - is_available reflects whether init succeeded
    """

    @property
    @abstractmethod
    def name(self) -> PaymentProtocolName:
        """Protocol identifier."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this protocol is configured and ready."""

    @abstractmethod
    async def initialize(self) -> bool:
        """Lazy one-time setup. Returns True on success.

        Negative results are never cached so missing env vars
        can be injected without restarting.
        """

    @abstractmethod
    async def build_payment_requirement(
        self,
        *,
        amount: str,
        asset: str,
        recipient: str,
        description: str | None = None,
    ) -> PaymentRequirement:
        """Build a PaymentRequirement for a 402 response."""

    @abstractmethod
    async def verify_payment(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirement,
    ) -> PaymentResult:
        """Verify an incoming payment against a requirement (no settlement)."""

    async def settle_payment(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirement,
    ) -> PaymentResult:
        """Verify *and* capture funds for an incoming payment.

        Default implementation reports the protocol cannot settle, so the
        paywall fails closed for protocols that only verify. Override in
        protocols that support on-chain (or off-chain) capture.
        """
        return PaymentResult(
            status=PaymentStatus.FAILED,
            protocol=self.name,
            error=f"{self.name} does not support settlement",
        )

    def shutdown(self) -> None:  # noqa: B027
        """Optional cleanup. Override if the protocol holds connections."""
