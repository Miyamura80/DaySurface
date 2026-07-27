"""Gmail Pub/Sub push receiver + internal runner endpoint.

The push receiver authenticates the Pub/Sub OIDC token (verifying the signed
JWT's audience and the push service account's email), decodes the notification
envelope, and hands off to ``process_notification`` which dedups on the Pub/Sub
``messageId`` and syncs Gmail history into the webhook outbox. History sync runs
in a worker thread so the event loop is never blocked by synchronous Gmail/DB
I/O, mirroring the Stripe webhook handler.

The internal ``/renew`` endpoint lets an external scheduler (cron, Cloud
Scheduler) drive watch renewal + outbox draining when
``WEBHOOK_RUNNER_MODE="endpoint"`` instead of the in-process loop. It is gated
by a shared bearer (``WEBHOOK_RUNNER_TOKEN``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger as log

from common import global_config
from services.gmail_watch_svc import process_notification, renew_due_watches
from services.webhook_delivery_svc import drain_due_deliveries

router = APIRouter(prefix="/api/v1/google", tags=["google-webhooks"])


def _is_dev() -> bool:
    return (getattr(global_config, "DEV_ENV", "") or "").lower() in {"local", "dev"}


def _verify_oidc(authorization: str | None) -> None:
    """Verify the Pub/Sub OIDC bearer token or raise HTTPException.

    Checks the JWT signature + expiry, that ``aud`` matches
    ``GMAIL_PUSH_AUDIENCE``, and - when configured - that the token's verified
    ``email`` equals the push subscription's service account
    (``GMAIL_PUSH_SA_EMAIL``). ``verify_oauth2_token`` also enforces that the
    issuer is Google.
    """
    audience = global_config.GMAIL_PUSH_AUDIENCE
    if not audience:
        raise HTTPException(status_code=503, detail="Gmail push not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]

    # Lazy import: keep google-auth off the app-boot / test import path.
    from google.auth.transport import requests as google_requests  # noqa: PLC0415
    from google.oauth2 import id_token  # noqa: PLC0415

    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=audience
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid OIDC token") from exc

    expected_sa = global_config.GMAIL_PUSH_SA_EMAIL
    if not expected_sa:
        # Fail closed outside dev: without the SA-email check, ANY Google-signed
        # OIDC token with the right audience would be accepted, letting a third
        # party spoof push notifications for arbitrary mailboxes.
        if not _is_dev():
            raise HTTPException(
                status_code=503,
                detail="Gmail push identity (GMAIL_PUSH_SA_EMAIL) not configured",
            )
    elif claims.get("email") != expected_sa or not claims.get("email_verified"):
        raise HTTPException(status_code=403, detail="Untrusted push identity")


def _decode_envelope(body: dict) -> tuple[str, str, str]:
    """Extract (email, historyId, messageId) from a Pub/Sub push envelope."""
    message = body.get("message") or {}
    message_id = message.get("messageId") or message.get("message_id")
    data_b64 = message.get("data")
    if not message_id or not data_b64:
        raise HTTPException(status_code=400, detail="Malformed Pub/Sub envelope")
    try:
        decoded = json.loads(base64.b64decode(data_b64))
    except (ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Undecodable message data") from exc
    email = decoded.get("emailAddress")
    history_id = decoded.get("historyId")
    if not email or history_id is None:
        raise HTTPException(status_code=400, detail="Missing emailAddress/historyId")
    return email, str(history_id), str(message_id)


@router.post("/webhook/gmail")
async def gmail_push(request: Request):
    """Receive an authenticated Gmail Pub/Sub push notification."""
    _verify_oidc(request.headers.get("authorization"))

    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    email, history_id, message_id = _decode_envelope(body)

    # Run the synchronous Gmail/DB work off the event loop. A failure here
    # returns 500 so Pub/Sub redelivers (the dedup marker is rolled back too).
    try:
        result = await asyncio.to_thread(
            process_notification, email, history_id, message_id
        )
    except Exception as exc:  # noqa: BLE001
        # Push boundary: any Gmail/DB error must surface as a 5xx so Pub/Sub
        # retries rather than dropping the notification.
        log.warning("Gmail push processing failed for {}: {}", email, exc)
        raise HTTPException(status_code=500, detail="Processing error") from exc

    return {"received": True, **result}


@router.post("/internal/renew")
async def internal_renew(request: Request):
    """Drive watch renewal + outbox draining (external scheduler entrypoint)."""
    token = global_config.WEBHOOK_RUNNER_TOKEN
    if not token:
        raise HTTPException(status_code=503, detail="Runner endpoint not enabled")
    provided = request.headers.get("x-runner-token") or ""
    if not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid runner token")
    renewed = await asyncio.to_thread(renew_due_watches)
    drained = await asyncio.to_thread(drain_due_deliveries)
    return {"renewed": renewed, "drained": drained}
