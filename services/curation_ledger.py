"""Curation-ledger persistence layer (pure DB, no Gmail / no transport).

This module is the single place that reads and writes ``thread_curation`` rows
and handles encryption of the derived ``summary`` / ``reasoning`` text. It
imports only the DB engine, the ORM model, the curation contracts, and the
token-encryption util - deliberately no Gmail-client imports - so both the
ledger services (``services.inbox_curation_svc``) and the action services
(``services.gmail_messages_svc`` / ``services.gmail_drafts_svc``) can depend on
it without an import cycle.

Encryption reuses the project's ``common.token_encryption`` Fernet backend and
stamps each row with the active ``key_id`` (mirroring ``google_tokens``) so keys
can rotate. Plaintext is never persisted.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import UTC, datetime

from loguru import logger as log
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from db.engine import use_db_session
from db.models.thread_curation import ThreadCuration
from models.curation import (
    CurationBucket,
    CurationRecord,
    CurationState,
    LedgerStatus,
    SuggestedAction,
    ThreadJudgment,
)


@contextmanager
def _session() -> Generator[Session, None, None]:
    with use_db_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Encryption (reuse the Google-token Fernet backend)
# ---------------------------------------------------------------------------


def _encrypt(plaintext: str | None) -> tuple[bytes | None, str]:
    """Return ``(ciphertext_or_None, key_id)`` for optional text.

    Call-time import so tests that patch ``common.token_encryption.
    require_encryption`` take effect (binding at module import would bypass it).
    """
    from common.token_encryption import require_encryption  # noqa: PLC0415

    enc = require_encryption()
    if plaintext is None:
        return None, enc.key_id
    return enc.encrypt(plaintext), enc.key_id


def _decrypt(ciphertext: bytes | None) -> str | None:
    if ciphertext is None:
        return None
    from common.token_encryption import require_encryption  # noqa: PLC0415

    return require_encryption().decrypt(ciphertext)


# ---------------------------------------------------------------------------
# Row -> model
# ---------------------------------------------------------------------------


def row_to_record(row: ThreadCuration) -> CurationRecord:
    """Decrypt a ledger row into a host-facing ``CurationRecord``.

    ``ledger_status`` defaults to ``curated`` here; callers overlay staleness
    from a freshness check against the thread's current Gmail historyId.
    """
    return CurationRecord(
        thread_id=row.thread_id,
        bucket=CurationBucket(row.bucket) if row.bucket else None,
        importance=row.importance,
        summary=_decrypt(row.summary_enc),
        reasoning=_decrypt(row.reasoning_enc),
        suggested_action=SuggestedAction(row.suggested_action),
        draft_id=row.draft_id,
        confidence=row.confidence,
        state=CurationState(row.state),
        curator_version=row.curator_version,
        curated_history_id=row.curated_history_id,
        curated_at=row.curated_at,
        updated_at=row.updated_at,
        ledger_status=LedgerStatus.curated,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_records(
    user_id: str,
    *,
    bucket: str | None = None,
    state: str | None = None,
    limit: int = 50,
) -> list[CurationRecord]:
    """Return decrypted curation records for a user, optionally filtered."""
    with _session() as session:
        query = session.query(ThreadCuration).filter(ThreadCuration.user_id == user_id)
        if bucket is not None:
            query = query.filter(ThreadCuration.bucket == bucket)
        if state is not None:
            query = query.filter(ThreadCuration.state == state)
        rows = (
            query.order_by(ThreadCuration.importance.desc().nullslast())
            .limit(limit)
            .all()
        )
        return [row_to_record(r) for r in rows]


def load_status_map(user_id: str, thread_ids: Iterable[str]) -> dict[str, dict]:
    """Return ``{thread_id: {state, curated_history_id}}`` for the given threads.

    Cheap lookup (no decryption) used to annotate search results with their
    ledger status without materializing full records.
    """
    ids = list(thread_ids)
    if not ids:
        return {}
    with _session() as session:
        rows = (
            session.query(
                ThreadCuration.thread_id,
                ThreadCuration.state,
                ThreadCuration.curated_history_id,
            )
            .filter(
                ThreadCuration.user_id == user_id,
                ThreadCuration.thread_id.in_(ids),
            )
            .all()
        )
    return {
        tid: {"state": state, "curated_history_id": hist} for tid, state, hist in rows
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def upsert_judgments(
    user_id: str,
    judgments: list[ThreadJudgment],
    *,
    history_ids: dict[str, str | None] | None = None,
    curator_version: str | None = None,
) -> list[str]:
    """Insert or update ledger rows from host judgments. Returns saved thread ids.

    ``history_ids`` maps ``thread_id -> current Gmail historyId`` so each written
    row's freshness watermark advances to the moment of curation. A re-curated
    thread advances its ``curated_history_id`` (freshening it).
    """
    history_ids = history_ids or {}
    saved: list[str] = []
    with _session() as session:
        for j in judgments:
            summary_enc, key_id = _encrypt(j.summary)
            reasoning_enc, _ = _encrypt(j.reasoning)
            row = (
                session.query(ThreadCuration)
                .filter(
                    ThreadCuration.user_id == user_id,
                    ThreadCuration.thread_id == j.thread_id,
                )
                .one_or_none()
            )
            if row is None:
                row = ThreadCuration(user_id=user_id, thread_id=j.thread_id)
                session.add(row)
            row.bucket = j.bucket.value
            row.importance = j.importance
            row.summary_enc = summary_enc
            row.reasoning_enc = reasoning_enc
            row.key_id = key_id
            row.suggested_action = j.suggested_action.value
            row.draft_id = j.draft_id
            row.confidence = j.confidence
            row.state = CurationState.curated.value
            # Only advance the freshness watermark when we actually fetched a
            # current historyId for this thread. If the lookup missed it (e.g.
            # deleted between search and save), keep the prior watermark rather
            # than clearing it to None, which would silently disable staleness
            # detection for an already-curated thread.
            new_history_id = history_ids.get(j.thread_id)
            if new_history_id is not None:
                row.curated_history_id = new_history_id
            row.curator_version = curator_version
            row.curated_at = datetime.now(UTC)
            saved.append(j.thread_id)
        session.commit()
    return saved


def mark_state(
    user_id: str,
    thread_id: str,
    state: CurationState,
    *,
    draft_id: str | None = None,
) -> bool:
    """Update a ledger row's ``state`` (and optionally ``draft_id``) if it exists.

    Returns ``True`` if a row was updated. No-op (returns ``False``) when the
    thread was never curated - an action on an uncurated thread does not
    fabricate a ledger row.
    """
    with _session() as session:
        row = (
            session.query(ThreadCuration)
            .filter(
                ThreadCuration.user_id == user_id,
                ThreadCuration.thread_id == thread_id,
            )
            .one_or_none()
        )
        if row is None:
            return False
        row.state = state.value
        if draft_id is not None:
            row.draft_id = draft_id
        session.commit()
        return True


def mark_state_best_effort(
    user_id: str,
    thread_id: str,
    state: CurationState,
    *,
    draft_id: str | None = None,
) -> None:
    """Best-effort ``mark_state`` for action tools.

    A ledger update must never fail a Gmail action that already succeeded, so DB
    errors are swallowed and logged rather than propagated.
    """
    try:
        mark_state(user_id, thread_id, state, draft_id=draft_id)
    except SQLAlchemyError as exc:
        log.warning(
            "Ledger state update failed for thread {} (action still succeeded): {}",
            thread_id,
            exc,
        )


def purge_user(user_id: str) -> int:
    """Delete every ledger row for a user. Returns the number deleted."""
    with _session() as session:
        deleted = (
            session.query(ThreadCuration)
            .filter(ThreadCuration.user_id == user_id)
            .delete(synchronize_session=False)
        )
        session.commit()
        return int(deleted)
