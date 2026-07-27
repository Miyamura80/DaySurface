"""Agentic payment protocol API routes."""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger as log
from pydantic import BaseModel

from api_server.auth.scopes import PAYMENTS_READ, PAYMENTS_WRITE, require_scopes
from api_server.auth.unified_auth import AuthenticatedUser
from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentStatus,
)

router = APIRouter(prefix="/api/v1/agentic-payments", tags=["agentic-payments"])


class PaymentRequirementRequest(BaseModel):
    """Request body for creating a payment requirement."""

    protocol: str = "x402"
    amount: str
    asset: str = "USDC"
    recipient: str = ""
    description: str | None = None


class PaymentVerifyRequest(BaseModel):
    """Request body for verifying a payment payload."""

    protocol: str = "x402"
    payload: dict
    header_value: str = ""
    requirement: dict


@router.get("/status")
async def payment_status(
    user: AuthenticatedUser = Depends(require_scopes(PAYMENTS_READ)),
) -> dict:
    """Return enabled payment protocols and their availability."""
    registry = PaymentRegistry.get()
    registry.initialize()

    protocols = []
    for name in registry.list_enabled():
        proto = registry.get_protocol(name)
        if proto:
            protocols.append(
                {
                    "name": name,
                    "available": proto.is_available,
                }
            )

    return {"protocols": protocols}


@router.post("/requirements")
async def create_payment_requirement(
    request: PaymentRequirementRequest,
    user: AuthenticatedUser = Depends(require_scopes(PAYMENTS_WRITE)),
) -> dict:
    """Generate a payment requirement for a given protocol.

    Returns the data needed to construct a 402 response.
    """
    registry = PaymentRegistry.get()
    registry.initialize()

    proto = registry.get_protocol(request.protocol)
    if not proto:
        raise HTTPException(
            status_code=400,
            detail=f"Protocol '{request.protocol}' is not enabled.",
        )

    if not await proto.initialize():
        raise HTTPException(
            status_code=503,
            detail=f"Protocol '{request.protocol}' is not available. "
            "Check server configuration.",
        )

    try:
        requirement = await proto.build_payment_requirement(
            amount=request.amount,
            asset=request.asset,
            recipient=request.recipient,
            description=request.description,
        )
        log.info(
            "Payment requirement created: protocol={}, amount={} {}",
            request.protocol,
            request.amount,
            request.asset,
        )
        return {
            "protocol": requirement.protocol,
            "network": requirement.network,
            "asset": requirement.asset,
            "amount": requirement.amount,
            "recipient": requirement.recipient,
            "facilitator_url": requirement.facilitator_url,
            "description": requirement.description,
            "extra": requirement.extra,
        }
    except Exception as exc:  # noqa: BLE001
        # Payment-protocol SDKs raise a wide range of errors; surface any failure
        # as a 500 rather than letting an unrelated exception type leak out.
        log.error("Failed to create payment requirement: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/verify")
async def verify_payment(
    request: PaymentVerifyRequest,
    user: AuthenticatedUser = Depends(require_scopes(PAYMENTS_WRITE)),
) -> dict:
    """Verify an incoming payment payload against a requirement."""
    registry = PaymentRegistry.get()
    registry.initialize()

    proto = registry.get_protocol(request.protocol)
    if not proto:
        raise HTTPException(
            status_code=400,
            detail=f"Protocol '{request.protocol}' is not enabled.",
        )

    if not await proto.initialize():
        raise HTTPException(
            status_code=503,
            detail=f"Protocol '{request.protocol}' is not available.",
        )

    payload = PaymentPayload(
        protocol=PaymentProtocolName(request.protocol),
        raw=request.payload,
        header_value=request.header_value,
    )

    requirement = PaymentRequirement(
        protocol=PaymentProtocolName(request.protocol),
        network=request.requirement.get("network", ""),
        asset=request.requirement.get("asset", ""),
        amount=request.requirement.get("amount", ""),
        recipient=request.requirement.get("recipient", ""),
        facilitator_url=request.requirement.get("facilitator_url"),
        description=request.requirement.get("description"),
    )

    try:
        result = await proto.verify_payment(payload, requirement)
    except Exception as exc:  # noqa: BLE001
        # Defense in depth: protocol.verify_payment() is contracted to return a
        # PaymentResult, but if it raises (SDK bug, unhandled edge case) we
        # convert to a 500 instead of leaking a stack trace.
        log.error("Payment verification failed unexpectedly: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.status == PaymentStatus.FAILED:
        # Infrastructure/SDK error - surface as non-2xx so callers
        # don't silently swallow server-side problems.
        log.error(
            "Payment verification failed: protocol={}, error={}",
            request.protocol,
            result.error,
        )
        raise HTTPException(
            status_code=502,
            detail=result.error or "Payment verification failed",
        )

    if result.status == PaymentStatus.COMPLETED:
        log.info(
            "Payment verified: protocol={}, tx={}",
            request.protocol,
            result.transaction_id,
        )
    else:
        # REJECTED is a successful check with a negative outcome - 200 is
        # correct since the verification itself worked, the payment didn't.
        log.warning(
            "Payment rejected: protocol={}, status={}, error={}",
            request.protocol,
            result.status,
            result.error,
        )

    return {
        "status": result.status,
        "transaction_id": result.transaction_id,
        "error": result.error,
    }
