"""Shared types for agentic payment protocols."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PaymentProtocolName(StrEnum):
    """Supported agentic payment protocol identifiers."""

    X402 = "x402"
    MPP = "mpp"
    ACP = "acp"


class PaymentStatus(StrEnum):
    """Outcome status for a payment verification."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PaymentRequirement:
    """Describes what payment a server requires (returned in 402 responses)."""

    protocol: PaymentProtocolName
    network: str
    asset: str
    amount: str
    recipient: str
    facilitator_url: str | None = None
    description: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentResult:
    """Outcome of a payment verification or settlement."""

    status: PaymentStatus
    transaction_id: str | None = None
    protocol: PaymentProtocolName | None = None
    error: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentPayload:
    """A payment payload attached to a request by a client."""

    protocol: PaymentProtocolName
    raw: dict[str, Any] = field(default_factory=dict)
    header_value: str = ""
