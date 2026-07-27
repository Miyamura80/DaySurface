"""Generic ``Idempotency-Key`` support for mutating API routes.

Implements the REST idempotency convention (Stripe / IETF
draft-ietf-httpapi-idempotency-key-header) on top of the ``idempotency_keys``
table:

1. Client sends a unique ``Idempotency-Key`` header with a mutating request.
2. The server *claims* the key by inserting an in-flight row (unique on
   ``(user_id, route, key)``). A losing concurrent insert means the key is
   already in use.
3. On the first request the handler runs, and its response is cached on the
   row. Retries with the same key replay the cached response instead of
   re-executing the side effect. Reusing a key with a *different* payload is
   rejected (422); a still-running request returns 409.

Only the API transport uses this; CLI and MCP are unaffected.
"""

import hashlib
import json
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger as log
from pydantic import BaseModel
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from db.engine import use_db_session
from db.models.idempotency_keys import IdempotencyRecord

# Mirror the DB column width and (loosely) Stripe's own key-length guidance.
_KEY_MAX_LEN = 255
# Match Stripe's 24h idempotency window; retries beyond this re-execute.
_RETENTION = timedelta(days=1)
# Probability of running an opportunistic TTL sweep after a successful claim.
_CLEANUP_PROBABILITY = 0.01


def _canonical_hash(payload: dict) -> str:
    """Stable SHA-256 of a request payload, independent of key ordering."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _require_key(request: Request) -> str:
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key header is required for this request",
        )
    if len(key) > _KEY_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Idempotency-Key must not exceed {_KEY_MAX_LEN} characters",
        )
    return key


def _handle_existing(
    session, user_id: str, route: str, key: str, request_hash: str
) -> Response:
    """Resolve a duplicate claim into a replay, conflict, or in-flight error."""
    existing = session.get(IdempotencyRecord, (user_id, route, key))
    if existing is None:
        # The competing transaction rolled back between our failed INSERT and
        # this read. Treat as a transient conflict; the caller can retry.
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key conflict; please retry the request",
        )
    if existing.request_hash != request_hash:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key was already used with a different request payload",
        )
    if existing.completed_at is None:
        raise HTTPException(
            status_code=409,
            detail="A request with this Idempotency-Key is still in progress",
        )
    return JSONResponse(
        content=existing.response_body,
        status_code=existing.status_code or 200,
    )


def _release(session, user_id: str, route: str, key: str) -> None:
    """Delete a claimed-but-failed row so the caller can retry the same key."""
    session.rollback()
    session.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.route == route,
            IdempotencyRecord.idempotency_key == key,
        )
    )
    session.commit()


def _maybe_cleanup() -> None:
    """Best-effort opportunistic TTL sweep, gated to a fraction of requests.

    Interim mechanism: the template ships no scheduler, so retention is enforced
    by piggy-backing a rare sweep onto request traffic. Prefer calling
    ``cleanup_expired_idempotency_keys`` from a scheduled job (cron / background
    task) when one exists and drop this request-path gate.
    """
    if random.random() >= _CLEANUP_PROBABILITY:
        return
    try:
        cleanup_expired_idempotency_keys()
    except Exception as exc:  # noqa: BLE001
        # Opportunistic maintenance must never fail a request that already
        # succeeded; a transient DB error here is non-fatal.
        log.warning("Idempotency key cleanup failed: {}", exc)


def cleanup_expired_idempotency_keys() -> int:
    """Delete idempotency rows older than the retention window.

    Returns the number of rows removed. Safe to schedule periodically.
    """
    cutoff = datetime.now(UTC) - _RETENTION
    with use_db_session() as session:
        result = session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.created_at < cutoff)
        )
        session.commit()
        return result.rowcount or 0  # ty: ignore[unresolved-attribute]


def execute_idempotent(
    *,
    request: Request,
    user_id: str,
    route: str,
    request_payload: dict,
    compute: Callable[[], BaseModel],
) -> Response | BaseModel:
    """Run ``compute`` at most once per ``Idempotency-Key``, caching its result.

    On the first request the computed model is returned and cached. Retries
    with the same key replay the cached response; the same key with a different
    payload returns 422; an in-flight key returns 409. If ``compute`` raises a
    client error (HTTP 4xx, e.g. quota/validation) the claim is released so the
    request can be retried; ambiguous failures (5xx, network errors that may
    have already committed an irreversible side effect) keep the claim so a
    retry returns 409 rather than re-executing.
    """
    key = _require_key(request)
    request_hash = _canonical_hash(request_payload)

    with use_db_session() as session:
        # Claim the key by inserting an in-flight row. A duplicate means the
        # key is already taken (completed, in-flight, or payload mismatch).
        try:
            session.add(
                IdempotencyRecord(
                    user_id=user_id,
                    route=route,
                    idempotency_key=key,
                    request_hash=request_hash,
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            return _handle_existing(session, user_id, route, key, request_hash)

        # We hold the claim: execute the side effect exactly once.
        #
        # Crash window: ``compute`` performs the real (often irreversible) side
        # effect, and it runs between the claim commit above and the result
        # commit below with no enclosing transaction - the two commits cannot be
        # made atomic when the side effect is a remote call. If the process dies
        # after the side effect but before the result is cached, the row is left
        # in-flight and retries get 409 until the TTL sweep removes it (see
        # ``_RETENTION``), after which the same key re-executes. This is inherent
        # to idempotency over external effects; we bound it rather than prevent
        # it.
        try:
            result = compute()
        except HTTPException as exc:
            # Client errors (4xx: quota 402, validation 422, bad input 400) are
            # raised before the side effect commits, so release the claim for a
            # clean same-key retry. A 5xx HTTPException is ambiguous and falls
            # through to the no-release path below. Errors are never cached.
            if exc.status_code < 500:
                _release(session, user_id, route, key)
            raise
        # Any other failure - a 5xx HTTPException, a network drop, or an
        # unexpected exception - is *ambiguous*: the side effect may already
        # have committed remotely (e.g. Gmail accepted the send before the
        # connection dropped). We deliberately do NOT release the claim, so a
        # same-key retry returns 409 instead of re-executing an irreversible
        # operation; the wedged row clears via the TTL sweep. The exception
        # propagates out of the ``use_db_session`` context with the claim intact.

        # Match FastAPI's response_model serialization (by_alias=True by default)
        # so a replayed JSONResponse is byte-identical to the first response.
        body = result.model_dump(mode="json", by_alias=True)
        session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.route == route,
                IdempotencyRecord.idempotency_key == key,
            )
            .values(
                status_code=200,
                response_body=body,
                completed_at=datetime.now(UTC),
            )
        )
        session.commit()

    _maybe_cleanup()
    return result
