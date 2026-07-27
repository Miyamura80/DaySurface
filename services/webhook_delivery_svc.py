"""Outbound webhook delivery - drain the outbox, POST signed payloads, retry.

Called by the periodic runner (in-process loop or internal /renew endpoint).
Each tick atomically *claims* a batch of due deliveries (flipping them to
``sending`` and committing so no row locks are held during network I/O), then
processes each in its own short transaction: POST the signed payload and mark
the row ``succeeded``, or reschedule with exponential backoff (``pending``)
until ``WEBHOOK_MAX_ATTEMPTS`` is reached (``failed``).

Two invariants make this safe:

* **Per-row isolation.** Each delivery commits independently, and ``_process``
  never propagates - any failure (HTTP, an undecryptable secret after key
  rotation, a serialization error) is recorded on that row and follows the
  bounded retry/give-up path. One poison row can neither wedge the outbox nor
  cause an already-succeeded sibling to be re-sent.
* **Lock-free network I/O.** The claim uses ``FOR UPDATE SKIP LOCKED`` on
  Postgres so concurrent runners never grab the same row, but the lock is
  released at claim-commit time - the blocking POSTs run with no row locks and
  no open transaction. A runner that crashes mid-send leaves rows in
  ``sending``; the next claim reclaims any ``sending`` row older than the lease.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger as log
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.webhooks import WebhookDelivery, WebhookEvent, WebhookSubscription
from services.webhooks_svc import (
    DELIVERY_ID_HEADER,
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    decrypt_secret,
    sign_payload,
)

# Backoff schedule: delay = min(BASE * 2**(attempts-1), CAP).
_BACKOFF_BASE_S = 30
_BACKOFF_CAP_S = 3600
_HTTP_TIMEOUT_S = 10.0
_DEFAULT_BATCH = 20
# A claimed ("sending") row left behind by a crashed runner is reclaimable
# after this many seconds so deliveries never get stranded.
_CLAIM_LEASE_S = 300


def _backoff_seconds(attempts: int) -> int:
    """Delay before the next attempt after ``attempts`` failures so far."""
    exp = _BACKOFF_BASE_S * (2 ** max(0, attempts - 1))
    return min(exp, _BACKOFF_CAP_S)


def _claim_batch(session: Session, now: datetime, limit: int) -> list[str]:
    """Atomically claim up to ``limit`` due deliveries; return their ids.

    Selects rows that are ``pending`` and due, plus any ``sending`` row whose
    lease has expired (crashed-runner recovery), flips them to ``sending``, and
    commits - releasing the ``FOR UPDATE SKIP LOCKED`` locks (Postgres) before
    any HTTP I/O happens. The select+update run in one transaction so two
    replicas can never claim the same row.
    """
    lease_cutoff = now - timedelta(seconds=_CLAIM_LEASE_S)
    query = (
        session.query(WebhookDelivery.id)
        .filter(
            or_(
                and_(
                    WebhookDelivery.status == "pending",
                    WebhookDelivery.next_attempt_at <= now,
                ),
                and_(
                    WebhookDelivery.status == "sending",
                    WebhookDelivery.updated_at < lease_cutoff,
                ),
            )
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    ids = [row[0] for row in query.all()]
    if ids:
        session.query(WebhookDelivery).filter(WebhookDelivery.id.in_(ids)).update(
            {WebhookDelivery.status: "sending", WebhookDelivery.updated_at: now},
            synchronize_session=False,
        )
        session.commit()
    return ids


def _post(
    url: str, secret: str, delivery: WebhookDelivery, event: WebhookEvent
) -> None:
    """POST the signed payload; raises httpx.HTTPError on transport/HTTP failure."""
    body = json.dumps(
        {
            "id": event.id,
            "type": event.event_type,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "data": event.payload,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp = int(time.time())
    signature = sign_payload(secret, timestamp, body)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: f"sha256={signature}",
        TIMESTAMP_HEADER: str(timestamp),
        EVENT_ID_HEADER: event.id,
        EVENT_TYPE_HEADER: event.event_type,
        DELIVERY_ID_HEADER: delivery.id,
    }
    with httpx.Client(timeout=_HTTP_TIMEOUT_S) as client:
        resp = client.post(url, content=body, headers=headers)
    resp.raise_for_status()


def _record_failure(delivery: WebhookDelivery, error: str) -> str:
    """Mark a delivery failed (at max attempts) or reschedule with backoff."""
    delivery.last_error = error[:1000]
    if delivery.attempts >= global_config.WEBHOOK_MAX_ATTEMPTS:
        delivery.status = "failed"
        log.warning(
            "webhook delivery {} gave up after {} attempts: {}",
            delivery.id,
            delivery.attempts,
            delivery.last_error,
        )
        return "failed"
    # Back to pending so the next claim re-selects it once the backoff elapses.
    delivery.status = "pending"
    delivery.next_attempt_at = datetime.now(UTC) + timedelta(
        seconds=_backoff_seconds(delivery.attempts)
    )
    return "retry"


def _process(session: Session, delivery: WebhookDelivery) -> str:
    """Attempt one delivery, mutating its row. Never raises.

    Returns 'sent' | 'retry' | 'failed' | 'dropped'.
    """
    sub = session.get(WebhookSubscription, delivery.subscription_id)
    event = session.get(WebhookEvent, delivery.event_id)
    if sub is None or event is None or not sub.active:
        # Subscriber gone or deactivated after enqueue: stop trying.
        delivery.status = "failed"
        delivery.last_error = "subscription inactive or missing"
        return "dropped"

    delivery.attempts += 1
    try:
        _post(sub.url, decrypt_secret(sub.secret_enc), delivery, event)
    except httpx.HTTPError as exc:
        return _record_failure(delivery, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        # Defensive per-delivery boundary: an unexpected failure (e.g. an
        # undecryptable secret after key rotation, or a non-serializable
        # payload) must not escape and abort the runner or roll back sibling
        # deliveries. Record it and follow the same bounded backoff/give-up
        # path so this row cannot wedge the outbox.
        return _record_failure(delivery, f"{type(exc).__name__}: {exc}")

    delivery.status = "succeeded"
    delivery.last_error = None
    return "sent"


def _process_one(delivery_id: str) -> str:
    """Process a single claimed delivery in its own transaction."""
    with use_db_session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return "dropped"
        outcome = _process(session, delivery)
        session.commit()
        return outcome


def drain_due_deliveries(limit: int = _DEFAULT_BATCH) -> dict[str, int]:
    """Claim and attempt up to ``limit`` due deliveries. Returns outcome counts."""
    counts = {"sent": 0, "retry": 0, "failed": 0, "dropped": 0}
    with use_db_session() as session:
        claimed = _claim_batch(session, datetime.now(UTC), limit)
    # Locks are released; POST each claimed row in its own short transaction so
    # one failure can neither block nor roll back the others.
    for delivery_id in claimed:
        counts[_process_one(delivery_id)] += 1
    if any(counts.values()):
        log.debug("drain_due_deliveries: {}", counts)
    return counts


# ---------------------------------------------------------------------------
# Cleanup (called opportunistically by the runner)
# ---------------------------------------------------------------------------


def cleanup_delivered(older_than_days: int = 7) -> int:
    """Delete succeeded deliveries older than the cutoff. Returns rows removed."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    with use_db_session() as session:
        removed = (
            session.query(WebhookDelivery)
            .filter(
                WebhookDelivery.status == "succeeded",
                WebhookDelivery.updated_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        session.commit()
    return int(removed)
