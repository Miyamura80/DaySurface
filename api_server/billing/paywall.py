"""x402 sell-side paywall: gate a priced service behind a settled payment.

A service declares a price with ``@service(price="0.001")``. On the HTTP and
MCP transports, :func:`enforce_payment` runs before the service and:

1. If x402 is **not enabled** in config, returns immediately - the service runs
   free (quota-limited elsewhere) so the template works with no wallet.
2. If x402 is enabled but the operator wallet is unconfigured, fails closed
   (503) - a priced service must not run for free on a payments deployment.
3. Otherwise builds an x402 *payment requirement* and, when the request carries
   no ``X-PAYMENT`` header, raises :class:`PaymentRequiredError` (HTTP 402) with
   the ``accepts`` challenge the client needs to pay.
4. When a payment is present, verifies and settles it through the facilitator
   and records the settlement. Settlement is idempotent on the payment hash, so
   a replayed ``X-PAYMENT`` header is honored without double-charging.

The public entry point is synchronous so both the sync REST route and the sync
MCP tool wrapper can call it; the async facilitator I/O runs on a private event
loop via :func:`_run_async`.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import threading
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger as log
from sqlalchemy.exc import IntegrityError

from db.engine import use_db_session
from db.models.payment_settlements import PaymentSettlement
from src.payments.registry import PaymentRegistry
from src.payments.types import (
    PaymentPayload,
    PaymentProtocolName,
    PaymentRequirement,
    PaymentStatus,
)

_PROTOCOL = PaymentProtocolName.X402
# x402 payload envelope version advertised in the 402 challenge.
_X402_VERSION = 1


class PaymentRequiredError(HTTPException):
    """HTTP 402 carrying an x402 ``accepts`` challenge.

    Subclasses ``HTTPException`` (status < 500) on purpose: the idempotency
    engine only releases a claimed key for client errors, so a paid mutating
    service that 402s stays retryable instead of wedging the key.
    """

    def __init__(self, challenge: dict) -> None:
        self.challenge = challenge
        super().__init__(status_code=402, detail=challenge)


def _run_async(make_coro):
    """Run an async coroutine to completion on a private event loop.

    Used to drive the async facilitator client from the synchronous REST/MCP
    call paths. A fresh loop in a dedicated thread is safe whether or not the
    caller already has a running loop, and never touches the caller's loop.
    """
    box: dict = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(make_coro())
        except BaseException as exc:  # noqa: BLE001
            # Captured and re-raised in the calling thread so the caller sees
            # the real error instead of a silently swallowed worker failure.
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def _canonical_hash(payload: dict) -> str:
    """Stable SHA-256 of a payment payload, independent of key ordering."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _build_challenge(requirement: PaymentRequirement, resource: str) -> dict:
    """Build the x402 ``402`` response body describing how to pay."""
    return {
        "x402Version": _X402_VERSION,
        "error": "payment required",
        "accepts": [
            {
                "scheme": "exact",
                "network": requirement.network,
                "maxAmountRequired": requirement.amount,
                "resource": resource,
                "description": requirement.description or "",
                "payTo": requirement.recipient,
                "asset": requirement.asset,
                "facilitator": requirement.facilitator_url,
            }
        ],
    }


def _decode_payment_header(header: str) -> dict:
    """Decode a base64-encoded JSON ``X-PAYMENT`` header into a raw dict."""
    try:
        decoded = base64.b64decode(header, validate=True)
        raw = json.loads(decoded)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_invalid",
                "message": "X-PAYMENT header is not valid base64-encoded JSON",
            },
        ) from exc
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_invalid",
                "message": "X-PAYMENT payload must be a JSON object",
            },
        )
    return raw


def enforce_payment(
    *,
    user_id: str,
    route: str,
    price: str,
    asset: str,
    payment_header: str | None,
) -> None:
    """Gate a priced service call behind a settled x402 payment.

    Returns ``None`` when the call may proceed (payment settled, replayed, or
    x402 disabled). Raises :class:`PaymentRequiredError` (402) when a payment is
    required but absent, or ``HTTPException`` on an invalid/failed payment.
    """
    registry = PaymentRegistry.get()
    registry.initialize()
    proto = registry.get_protocol(_PROTOCOL)

    # x402 not enabled at all: the paywall feature is off, so a priced service
    # runs free (quota still applies on the free path). Keeps the template
    # usable with no wallet configured.
    if proto is None:
        log.debug("Paywall inactive (x402 disabled); {} runs free", route)
        return

    # Enabled but not configured (missing operator wallet): a priced service
    # must not run for free on a payments deployment - fail closed.
    if not _run_async(proto.initialize):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "payments_unavailable",
                "message": "x402 is enabled but not configured on this server",
            },
        )

    requirement = _run_async(
        lambda: proto.build_payment_requirement(
            amount=price,
            asset=asset,
            recipient="",
            description=route,
        )
    )

    if not payment_header:
        raise PaymentRequiredError(_build_challenge(requirement, route))

    raw = _decode_payment_header(payment_header)
    payment_hash = _canonical_hash(raw)
    payload = PaymentPayload(protocol=_PROTOCOL, raw=raw, header_value=payment_header)

    _settle_once(
        payment_hash=payment_hash,
        user_id=user_id,
        route=route,
        price=price,
        asset=asset,
        requirement=requirement,
        payload=payload,
        proto=proto,
    )


def _settle_once(
    *,
    payment_hash: str,
    user_id: str,
    route: str,
    price: str,
    asset: str,
    requirement: PaymentRequirement,
    payload: PaymentPayload,
    proto,
) -> None:
    """Claim, settle, and record a payment exactly once per payment hash."""
    with use_db_session() as session:
        # Claim the payment by inserting a pending row. A duplicate means this
        # single-use payment was already claimed (settled or in-flight).
        try:
            session.add(
                PaymentSettlement(
                    payment_hash=payment_hash,
                    user_id=user_id,
                    route=route,
                    protocol=_PROTOCOL,
                    amount=price,
                    asset=asset,
                    network=requirement.network,
                    status="pending",
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            _resolve_existing(session, payment_hash)
            return

        # We hold the claim: settle exactly once through the facilitator.
        result = _run_async(lambda: proto.settle_payment(payload, requirement))

        if result.status == PaymentStatus.COMPLETED:
            session.query(PaymentSettlement).filter_by(
                payment_hash=payment_hash
            ).update(
                {
                    "status": "settled",
                    "transaction_id": result.transaction_id,
                    "payer": raw_get(result.raw_response, "payer"),
                    "raw_response": result.raw_response,
                    "settled_at": datetime.now(UTC),
                }
            )
            session.commit()
            log.info(
                "x402 payment settled: route={} tx={} amount={} {}",
                route,
                result.transaction_id,
                price,
                asset,
            )
            return

        # Rejected or failed: release the claim so the caller can retry, then
        # surface the failure. Rejected = the payment itself is bad (402);
        # failed = an infrastructure/facilitator error (502).
        session.query(PaymentSettlement).filter_by(payment_hash=payment_hash).delete()
        session.commit()

    if result.status == PaymentStatus.REJECTED:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "payment_invalid",
                "message": result.error or "payment was rejected",
            },
        )
    raise HTTPException(
        status_code=502,
        detail={
            "code": "settlement_failed",
            "message": result.error or "payment settlement failed",
        },
    )


def _resolve_existing(session, payment_hash: str) -> None:
    """Handle a duplicate payment claim: replay if settled, else conflict."""
    existing = session.get(PaymentSettlement, payment_hash)
    if existing is not None and existing.status == "settled":
        # Same single-use payment already paid for this call: allow the replay
        # through without re-charging (mirrors idempotent retries).
        log.debug("x402 payment replay for already-settled hash {}", payment_hash)
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "settlement_in_progress",
            "message": "a settlement for this payment is already in progress",
        },
    )


def raw_get(raw: dict | None, key: str) -> str | None:
    """Best-effort read of a string field from a facilitator response dict."""
    if not raw:
        return None
    value = raw.get(key)
    return value if isinstance(value, str) else None
