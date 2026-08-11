"""Outbound webhook services - subscribe / list / unsubscribe / rotate + enqueue.

Pure, transport-agnostic business logic. When a connected Gmail account
receives new mail, ``enqueue_event`` records a :class:`WebhookEvent` and fans
out one pending :class:`WebhookDelivery` per matching active subscription.
Draining that outbox (signing + POSTing + retry) lives in the sibling
``webhook_delivery_svc`` module.

Signing mirrors Stripe: ``X-Webhook-Signature: sha256=<hex>`` over
``{timestamp}.{body}`` with the subscription's per-endpoint secret, plus an
``X-Webhook-Timestamp`` header so subscribers can reject replays. Secrets are
Fernet-encrypted at rest with the same backend used for Google refresh tokens
and returned in cleartext only at create / rotate time.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from loguru import logger as log
from sqlalchemy.orm import Session

from common import global_config
from common.token_encryption import require_encryption
from db.engine import use_db_session
from db.models.webhooks import WebhookDelivery, WebhookEvent, WebhookSubscription
from models.webhooks import (
    WebhookListInput,
    WebhookListResult,
    WebhookRotateSecretInput,
    WebhookRotateSecretResult,
    WebhookSubscribeInput,
    WebhookSubscribeResult,
    WebhookSubscriptionView,
    WebhookUnsubscribeInput,
    WebhookUnsubscribeResult,
)
from services import service
from src.utils.ssrf import pin_url_to_validated_ip

# Header names on delivered POSTs (kept here so the delivery module imports one).
SIGNATURE_HEADER = "X-Webhook-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
EVENT_ID_HEADER = "X-Webhook-Event-Id"
EVENT_TYPE_HEADER = "X-Webhook-Event-Type"
DELIVERY_ID_HEADER = "X-Webhook-Delivery-Id"

_SECRET_PREFIX = "whsec_"  # noqa: S105 - not a secret, a public prefix marker


# ---------------------------------------------------------------------------
# ID / secret / signing helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """32-char random hex id; fits the String(64) primary keys."""
    return secrets.token_hex(16)


def _new_secret() -> str:
    return _SECRET_PREFIX + secrets.token_urlsafe(32)


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """Hex HMAC-SHA256 over ``{timestamp}.{body}`` (Stripe-style, replay-safe)."""
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    )
    return mac.hexdigest()


def _encrypt_secret(secret: str) -> tuple[bytes, str]:
    enc = require_encryption()
    return enc.encrypt(secret), enc.key_id


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a stored signing secret (used by the delivery module)."""
    return require_encryption().decrypt(ciphertext)


# ---------------------------------------------------------------------------
# Subscriber URL validation (https-only + SSRF guard)
# ---------------------------------------------------------------------------


def _parse_webhook_url(url: str) -> tuple[Any, str, bool]:
    """Parse and scheme-check a subscriber URL. Returns ``(parsed, host, dev)``.

    Rejects non-http(s) schemes and cleartext http except to a dev loopback name.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Webhook url must be an http(s) URL")
    host = parsed.hostname
    if not host:
        raise ValueError("Webhook url must include a host")
    dev = global_config.is_dev
    is_loopback_name = host.lower() in {"localhost", "ip6-localhost"}
    if parsed.scheme == "http" and not (dev and is_loopback_name):
        raise ValueError("Webhook url must use https")
    return parsed, host, dev


def _reject_if_ssrf(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, dev: bool
) -> None:
    """Raise if *ip* is an SSRF-prone target (metadata / internal / loopback).

    Loopback is permitted only in dev for local testing.
    """
    if ip.is_loopback:
        if not dev:
            raise ValueError("Webhook url must not target a loopback address")
    elif (
        ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError("Webhook url must not target a private/reserved address")


def _validate_webhook_url(url: str) -> None:
    """Reject non-https and SSRF-prone subscriber URLs at subscribe time.

    Best-effort by design: it rejects a URL that *currently* resolves to an
    internal/reserved address, but a host that cannot be resolved right now is
    allowed through (a webhook endpoint may be briefly down at registration).
    This is safe because :func:`resolve_and_pin_webhook` re-checks and pins at
    delivery time and fails closed there - that, not this, is the authoritative
    SSRF guard.
    """
    parsed, host, dev = _parse_webhook_url(url)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return  # unresolvable now; the delivery-time guard is authoritative
    for info in infos:
        _reject_if_ssrf(ipaddress.ip_address(str(info[4][0]).split("%")[0]), dev)


def resolve_and_pin_webhook(
    url: str,
) -> tuple[str, dict[str, str], dict[str, str], tuple[str, str] | None]:
    """Validate + pin a subscriber URL for delivery. Returns ``(url, headers, ext, auth)``.

    Applies the webhook scheme policy (https except a dev loopback) and then the
    shared SSRF resolve-and-pin guard with the webhook address policy (loopback
    allowed only in dev). Called at delivery time so a DNS record flipped to an
    internal address after subscription is caught when the payload is actually
    sent; fails closed on an unresolvable host.
    """
    _parsed, _host, dev = _parse_webhook_url(url)
    return pin_url_to_validated_ip(
        url,
        validate_ip=lambda ip: _reject_if_ssrf(ip, dev),
        error_cls=ValueError,
    )


# ---------------------------------------------------------------------------
# Services (CRUD over subscriptions)
# ---------------------------------------------------------------------------


@service(
    name="webhook_subscribe",
    description="Register an HTTPS endpoint to receive signed webhook events",
    input_model=WebhookSubscribeInput,
    output_model=WebhookSubscribeResult,
    mutating=True,
)
def webhook_subscribe(input: WebhookSubscribeInput) -> WebhookSubscribeResult:
    """Create a subscription and return its one-time signing secret."""
    _validate_webhook_url(input.url)

    secret = _new_secret()
    secret_enc, key_id = _encrypt_secret(secret)
    sub_id = _new_id()
    event_types = input.event_types or None

    with use_db_session() as session:
        session.add(
            WebhookSubscription(
                id=sub_id,
                user_id=input.user_id,
                url=input.url,
                secret_enc=secret_enc,
                key_id=key_id,
                event_types=event_types,
                active=True,
            )
        )
        session.commit()

    return WebhookSubscribeResult(
        id=sub_id,
        url=input.url,
        event_types=event_types,
        active=True,
        secret=secret,
    )


@service(
    name="webhook_list",
    description="List the caller's webhook subscriptions (secrets are never returned)",
    input_model=WebhookListInput,
    output_model=WebhookListResult,
)
def webhook_list(input: WebhookListInput) -> WebhookListResult:
    with use_db_session() as session:
        rows = (
            session.query(WebhookSubscription)
            .filter(WebhookSubscription.user_id == input.user_id)
            .order_by(WebhookSubscription.created_at.desc())
            .all()
        )
        views = [
            WebhookSubscriptionView(
                id=r.id,
                url=r.url,
                event_types=r.event_types,
                active=r.active,
                created_at=r.created_at,
            )
            for r in rows
        ]
    return WebhookListResult(subscriptions=views)


@service(
    name="webhook_unsubscribe",
    description="Deactivate a webhook subscription so it stops receiving events",
    input_model=WebhookUnsubscribeInput,
    output_model=WebhookUnsubscribeResult,
    mutating=True,
)
def webhook_unsubscribe(
    input: WebhookUnsubscribeInput,
) -> WebhookUnsubscribeResult:
    with use_db_session() as session:
        row = (
            session.query(WebhookSubscription)
            .filter(
                WebhookSubscription.id == input.subscription_id,
                WebhookSubscription.user_id == input.user_id,
            )
            .one_or_none()
        )
        if row is None or not row.active:
            return WebhookUnsubscribeResult(unsubscribed=False)
        row.active = False
        session.commit()
    return WebhookUnsubscribeResult(unsubscribed=True)


@service(
    name="webhook_rotate_secret",
    description="Issue a new signing secret for a subscription (invalidates the old one)",
    input_model=WebhookRotateSecretInput,
    output_model=WebhookRotateSecretResult,
    mutating=True,
)
def webhook_rotate_secret(
    input: WebhookRotateSecretInput,
) -> WebhookRotateSecretResult:
    secret = _new_secret()
    secret_enc, key_id = _encrypt_secret(secret)
    with use_db_session() as session:
        row = (
            session.query(WebhookSubscription)
            .filter(
                WebhookSubscription.id == input.subscription_id,
                WebhookSubscription.user_id == input.user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ValueError("Subscription not found")
        row.secret_enc = secret_enc
        row.key_id = key_id
        session.commit()
    return WebhookRotateSecretResult(id=input.subscription_id, secret=secret)


# ---------------------------------------------------------------------------
# Enqueue (fan-out) - called by the Gmail push receiver within its own session
# ---------------------------------------------------------------------------


def enqueue_event(
    session: Session,
    *,
    user_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> str | None:
    """Record an event and fan out one pending delivery per matching sub.

    A subscription matches when it is active and either declares no
    ``event_types`` filter or lists ``event_type`` explicitly. Returns the
    new event id, or ``None`` when the user has no matching subscription (no
    event row is written in that case). Flushes but does not commit - the
    caller owns the surrounding transaction.
    """
    subs = (
        session.query(WebhookSubscription)
        .filter(
            WebhookSubscription.user_id == user_id,
            WebhookSubscription.active.is_(True),
        )
        .all()
    )
    matching = [s for s in subs if not s.event_types or event_type in s.event_types]
    if not matching:
        return None

    now = datetime.now(UTC)
    event_id = _new_id()
    session.add(
        WebhookEvent(
            id=event_id,
            user_id=user_id,
            event_type=event_type,
            payload=payload,
        )
    )
    for sub in matching:
        session.add(
            WebhookDelivery(
                id=_new_id(),
                event_id=event_id,
                subscription_id=sub.id,
                status="pending",
                attempts=0,
                next_attempt_at=now,
            )
        )
    session.flush()
    log.debug(
        "enqueued webhook event {} ({}) -> {} deliveries",
        event_id,
        event_type,
        len(matching),
    )
    return event_id


# ---------------------------------------------------------------------------
# Purge - called on Gmail disconnect
# ---------------------------------------------------------------------------


def purge_user_events(user_id: str) -> tuple[int, int]:
    """Delete every webhook event (and its deliveries) for a user.

    Event payloads bank Gmail-derived content - subject, sender, snippet - as
    plaintext JSON, so disconnecting must leave none of them behind. Returns
    ``(events_deleted, deliveries_deleted)``.

    Deliveries are removed in the same pass because they are keyed on
    ``event_id``: leaving them would strand pending rows the runner can only
    ever mark "dropped". Subscriptions themselves are *not* touched - they hold
    no email content and survive a disconnect/reconnect cycle.

    Both deletes match on a *subquery*, never a materialized id list: a busy
    mailbox banks one event per message, and an ``IN (...)`` list that long
    would blow the driver's bind-parameter limit. That failure would be
    swallowed by the best-effort caller and quietly leave payloads behind, so
    the purge must not scale with the user's event count.

    Deliveries are matched by *subscription*, not by the user's event ids:
    enqueue only ever fans an event out to its own user's subscriptions, so
    the two predicates select the same rows - but the subscription one does
    not depend on a snapshot of the event set, so it also catches deliveries
    for an event enqueued concurrently with this purge. Events are deleted
    last on purpose: if a push lands mid-purge the residue is a contentless
    outbox row (which the runner drops as event-missing), never a payload.
    """
    with use_db_session() as session:
        user_subs = (
            session.query(WebhookSubscription.id)
            .filter(WebhookSubscription.user_id == user_id)
            .scalar_subquery()
        )
        deliveries = (
            session.query(WebhookDelivery)
            .filter(WebhookDelivery.subscription_id.in_(user_subs))
            .delete(synchronize_session=False)
        )
        events = (
            session.query(WebhookEvent)
            .filter(WebhookEvent.user_id == user_id)
            .delete(synchronize_session=False)
        )
        session.commit()
        return int(events), int(deliveries)
