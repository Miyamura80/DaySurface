"""Gmail watch (users.watch) + push-notification history sync.

Bridges Gmail push notifications (hop 1) into the outbound webhook outbox
(hop 2). Three responsibilities:

* **Watch lifecycle** - ``gmail_watch_start`` / ``gmail_watch_stop`` register
  or cancel a mailbox's Pub/Sub watch and persist the returned forward-only
  ``historyId`` baseline + expiration on the ``google_tokens`` row.
* **History sync** - ``process_notification`` is the entrypoint the Pub/Sub
  receiver calls: it dedups on the Pub/Sub ``messageId``, walks
  ``users.history.list`` from the stored baseline for ``messageAdded`` events,
  and enqueues one ``gmail.message.new`` webhook event per new message. An
  expired baseline (HTTP 404) falls back to a bounded ``messages.list`` full
  resync and resets the baseline.
* **Renewal** - ``renew_due_watches`` re-issues watches nearing their ~7-day
  expiry (the periodic runner calls this daily).

All functions are synchronous (the transport layer bridges to async via
``asyncio.to_thread``). Gmail SDK imports stay lazy - see ``gmail_svc``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger as log
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common import global_config
from db.models.gmail_push import ProcessedPubsubMessage
from db.models.google_tokens import GoogleToken
from models.gmail_watch import (
    GmailWatchStartInput,
    GmailWatchStartResult,
    GmailWatchStopInput,
    GmailWatchStopResult,
)
from services import service
from services.gmail_svc import (
    GmailNotConnectedError,
    _get_db_session,
    _get_gmail_client,
    _load_token_row,
)
from services.webhooks_svc import enqueue_event

WATCH_EVENT_TYPE = "gmail.message.new"
# Renew watches expiring within this window (Gmail expiry is ~7 days; renewing
# daily with a generous buffer means a missed run never drops coverage).
_RENEW_BUFFER = timedelta(days=2)


# ---------------------------------------------------------------------------
# Watch lifecycle
# ---------------------------------------------------------------------------


def _call_watch(client: Any) -> dict[str, Any]:
    """Issue users().watch against the configured topic; return the response."""
    return (
        client.users()
        .watch(
            userId="me",
            body={"topicName": global_config.GMAIL_PUBSUB_TOPIC, "labelIds": ["INBOX"]},
        )
        .execute()
    )


def _apply_watch_response(row: GoogleToken, resp: dict[str, Any]) -> None:
    """Persist the historyId baseline + expiration from a watch response."""
    history_id = resp.get("historyId")
    if history_id:
        row.watch_history_id = str(history_id)
    expiration = resp.get("expiration")
    row.watch_expiration = (
        datetime.fromtimestamp(int(expiration) / 1000, tz=UTC) if expiration else None
    )
    row.watch_topic = global_config.GMAIL_PUBSUB_TOPIC


@service(
    name="gmail_watch_start",
    description="Subscribe the user's Gmail inbox to push notifications",
    input_model=GmailWatchStartInput,
    output_model=GmailWatchStartResult,
    mutating=True,
)
def gmail_watch_start(input: GmailWatchStartInput) -> GmailWatchStartResult:
    if not global_config.GMAIL_PUBSUB_TOPIC:
        raise RuntimeError("GMAIL_PUBSUB_TOPIC is not configured")
    client = _get_gmail_client(input.user_id)
    resp = _call_watch(client)
    with _get_db_session() as session:
        row = _load_token_row(session, input.user_id)
        if row is None:
            raise GmailNotConnectedError(input.user_id)
        _apply_watch_response(row, resp)
        session.commit()
        return GmailWatchStartResult(
            watching=True,
            history_id=row.watch_history_id,
            expiration=row.watch_expiration,
        )


@service(
    name="gmail_watch_stop",
    description="Cancel the user's Gmail push-notification watch",
    input_model=GmailWatchStopInput,
    output_model=GmailWatchStopResult,
    mutating=True,
)
def gmail_watch_stop(input: GmailWatchStopInput) -> GmailWatchStopResult:
    client = _get_gmail_client(input.user_id)
    client.users().stop(userId="me").execute()
    with _get_db_session() as session:
        row = _load_token_row(session, input.user_id)
        if row is not None:
            row.watch_history_id = None
            row.watch_expiration = None
            row.watch_topic = None
            session.commit()
    return GmailWatchStopResult(stopped=True)


def _renew_one(user_id: str) -> None:
    client = _get_gmail_client(user_id)
    resp = _call_watch(client)
    with _get_db_session() as session:
        row = _load_token_row(session, user_id)
        if row is not None:
            _apply_watch_response(row, resp)
            session.commit()


def renew_due_watches() -> int:
    """Re-issue watches at/near expiry. Returns the number renewed."""
    if not global_config.GMAIL_PUBSUB_TOPIC:
        return 0
    cutoff = datetime.now(UTC) + _RENEW_BUFFER
    with _get_db_session() as session:
        rows = (
            session.query(GoogleToken.user_id)
            .filter(
                GoogleToken.revoked_at.is_(None),
                GoogleToken.watch_topic.isnot(None),
                or_(
                    GoogleToken.watch_expiration.is_(None),
                    GoogleToken.watch_expiration < cutoff,
                ),
            )
            .all()
        )
        user_ids = [r[0] for r in rows]

    renewed = 0
    for user_id in user_ids:
        try:
            _renew_one(user_id)
            renewed += 1
        except Exception as exc:  # noqa: BLE001
            # Per-user boundary: one mailbox's renewal failure (revoked grant,
            # transient Gmail error) must not abort renewal of the others.
            log.warning("Gmail watch renewal failed for {}: {}", user_id, exc)
    if renewed:
        log.info("Renewed {} Gmail watch(es)", renewed)
    return renewed


# ---------------------------------------------------------------------------
# Push-notification history sync
# ---------------------------------------------------------------------------


def _load_token_row_by_email(session: Session, email: str) -> GoogleToken | None:
    """Resolve a mailbox for an inbound push. Mirrors ``_load_token_row``'s
    definition of active: un-revoked *and* still holding a credential, so a
    disconnected mailbox stops resolving (and stops enqueueing) immediately."""
    return (
        session.query(GoogleToken)
        .filter(
            GoogleToken.email == email,
            GoogleToken.revoked_at.is_(None),
            GoogleToken.refresh_token_enc.isnot(None),
        )
        .first()
    )


def _mark_pubsub_processed(session: Session, message_id: str) -> bool:
    """Insert the Pub/Sub messageId; False if already seen (at-least-once dedup)."""
    try:
        with session.begin_nested():
            session.add(ProcessedPubsubMessage(message_id=message_id))
        return True
    except IntegrityError:
        return False


def _message_payload(client: Any, message_id: str) -> dict[str, Any]:
    """Fetch lightweight metadata + snippet for a message (never the full body)."""
    msg = (
        client.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        )
        .execute()
    )
    headers = {
        h.get("name", "").lower(): h.get("value")
        for h in msg.get("payload", {}).get("headers", [])
    }
    return {
        "message_id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "label_ids": msg.get("labelIds", []),
        "snippet": msg.get("snippet"),
        "from": headers.get("from"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
    }


def _added_message_ids(client: Any, start_history_id: str) -> list[str]:
    """Page users.history.list for INBOX messageAdded ids since ``start_history_id``.

    ``labelId="INBOX"`` scopes the incremental walk identically to the watch
    (which registers ``labelIds=["INBOX"]``) so non-inbox mail never fans out.
    """
    seen: list[str] = []
    page_token: str | None = None
    while True:
        resp = (
            client.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
                pageToken=page_token,
            )
            .execute()
        )
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                mid = added.get("message", {}).get("id")
                if mid and mid not in seen:
                    seen.append(mid)
        page_token = resp.get("nextPageToken")
        if not page_token:
            return seen


def _fetch_message_payload(client: Any, message_id: str) -> dict[str, Any] | None:
    """Metadata + snippet, or None if the message vanished (404/410) before fetch.

    Gmail's history log is append-only, so a message that was added and then
    deleted still appears in ``messageAdded`` records but ``messages.get`` 404s.
    Skipping it stops one vanished message from poisoning the whole notification.
    """
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    try:
        return _message_payload(client, message_id)
    except HttpError as exc:
        if exc.resp.status in (404, 410):
            log.debug("Gmail message {} vanished before fetch; skipping", message_id)
            return None
        raise


def _forward(current: str | None, candidate: str) -> str:
    """Monotonic historyId advance - never let the baseline regress."""
    try:
        cur = int(current) if current else 0
        cand = int(candidate)
    except (TypeError, ValueError):
        return str(candidate)
    return str(max(cur, cand))


def _fetch_new_messages(
    client: Any, baseline: str | None, notified_history_id: str
) -> tuple[list[dict[str, Any]], str]:
    """Collect payloads for messages added since ``baseline``. Holds no DB session.

    Returns ``(payloads, new_baseline)``. Payloads are empty when there is no
    baseline yet, or when the baseline has expired (HTTP 404): we reset the
    baseline forward without replaying the backlog, because replaying recent
    INBOX messages as brand-new events would spam subscribers with duplicates.
    """
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    if not baseline:
        return [], str(notified_history_id)
    try:
        message_ids = _added_message_ids(client, baseline)
    except HttpError as exc:
        if exc.resp.status != 404:
            raise
        log.warning(
            "Gmail history baseline {} expired; resetting without backlog replay",
            baseline,
        )
        return [], _forward(baseline, notified_history_id)

    payloads: list[dict[str, Any]] = []
    for mid in message_ids:
        payload = _fetch_message_payload(client, mid)
        if payload is not None:
            payloads.append(payload)
    return payloads, _forward(baseline, notified_history_id)


def process_notification(
    email: str, history_id: str, message_id: str
) -> dict[str, Any]:
    """Entrypoint for the Pub/Sub receiver: resolve mailbox, sync history, dedup.

    Gmail network I/O runs with no DB connection held. The dedup insert, the
    event fan-out, and the monotonic baseline advance share one short
    transaction, so a failure before commit rolls back the dedup marker and
    Pub/Sub re-delivers (at-least-once).
    """
    # 1. Resolve the target mailbox in a short read (no connection held for I/O).
    with _get_db_session() as session:
        row = _load_token_row_by_email(session, email)
        if row is None:
            _mark_pubsub_processed(session, message_id)
            session.commit()  # dedup the unknown-user notification anyway
            log.debug("Gmail push for unknown/disconnected address {}", email)
            return {"status": "unknown_user"}
        user_id = row.user_id
        baseline = row.watch_history_id

    # 2. Gmail network I/O - no DB session open.
    client = _get_gmail_client(user_id)
    payloads, new_baseline = _fetch_new_messages(client, baseline, history_id)

    # 3. Short transaction: dedup + enqueue + monotonic baseline advance.
    with _get_db_session() as session:
        if not _mark_pubsub_processed(session, message_id):
            return {"status": "duplicate"}
        row = _load_token_row(session, user_id)
        if row is None:
            session.commit()
            return {"status": "unknown_user"}
        for payload in payloads:
            enqueue_event(
                session,
                user_id=user_id,
                event_type=WATCH_EVENT_TYPE,
                payload=payload,
            )
        row.watch_history_id = _forward(row.watch_history_id, new_baseline)
        session.commit()
    return {"status": "ok", "enqueued": len(payloads)}
