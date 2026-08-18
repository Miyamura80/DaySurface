"""Repository for server-side PDF document sessions.

PDF bytes live in the ``pdf_documents`` table and never travel through the
model's context - every tool operates on the ``doc_id`` handle these helpers
mint. This module owns the signing state machine
(``open -> awaiting_signature -> signed``); any status change must go through
:func:`update_document`, which rejects invalid transitions.

Part of the PDF core (isolation seam): must not import Gmail modules. See
tasks/prd-pdf-forms-digital-signatures.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from human_id import generate_id

from common import global_config
from db.engine import use_db_session
from db.models.pdf_documents import PdfDocument
from models.pdf_forms import PdfDocStatus

# Short aliases: the status type itself lives in models/pdf_forms.py so every
# layer (models, services, app tools) shares one definition.
PDF_STATUS_OPEN = PdfDocStatus.OPEN

# The full state machine. ``awaiting_signature -> open`` is the user-cancel
# path; ``signed`` is terminal (no re-sign, no revocation in v1).
_VALID_TRANSITIONS: dict[PdfDocStatus, frozenset[PdfDocStatus]] = {
    PdfDocStatus.OPEN: frozenset({PdfDocStatus.AWAITING_SIGNATURE}),
    PdfDocStatus.AWAITING_SIGNATURE: frozenset(
        {PdfDocStatus.SIGNED, PdfDocStatus.OPEN}
    ),
    PdfDocStatus.SIGNED: frozenset(),
}

# Sentinel distinguishing "leave placement untouched" from "clear it".
_UNSET: Any = object()


class PdfDocumentError(Exception):
    """Base class for PDF document-session errors."""


class PdfDocumentNotFoundError(PdfDocumentError):
    """Raised when a doc_id does not exist (or belongs to another user)."""

    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        super().__init__(
            f"PDF document session {doc_id!r} not found. It may have expired - "
            "open the source PDF again with pdf_open."
        )


class PdfDocumentTooLargeError(PdfDocumentError):
    """Raised when a PDF exceeds the configured session size ceiling."""

    def __init__(self, *, size: int, max_bytes: int) -> None:
        self.size = size
        self.max_bytes = max_bytes
        super().__init__(
            f"PDF is {size} bytes, over the {max_bytes}-byte limit "
            "(pdf_forms.max_document_bytes)."
        )


class PdfInvalidTransitionError(PdfDocumentError):
    """Raised on a status change the state machine does not allow."""

    def __init__(self, *, doc_id: str, current: str, requested: str) -> None:
        self.doc_id = doc_id
        self.current = current
        self.requested = requested
        super().__init__(
            f"Document {doc_id!r} cannot move from status {current!r} to {requested!r}."
        )


def _check_size(data: bytes) -> None:
    max_bytes = global_config.pdf_forms.max_document_bytes
    if len(data) > max_bytes:
        raise PdfDocumentTooLargeError(size=len(data), max_bytes=max_bytes)


def create_document(
    *,
    user_id: str,
    filename: str,
    data: bytes,
    page_count: int,
    source_ref: dict[str, Any] | None = None,
) -> PdfDocument:
    """Mint a new document session and return the stored row.

    Does exactly that and nothing else - the TTL sweep is orchestrated by
    ``pdf_open`` (the session init hook), not hidden in here.
    """
    _check_size(data)
    doc = PdfDocument(
        doc_id=generate_id(),
        user_id=user_id,
        status=PDF_STATUS_OPEN,
        filename=filename,
        original_bytes=data,
        current_bytes=data,
        page_count=page_count,
        source_ref=source_ref,
        audit=[],
    )
    with use_db_session() as session:
        session.add(doc)
        session.commit()
    return doc


def load_document(doc_id: str, user_id: str) -> PdfDocument:
    """Load a session scoped to its owner; raises if missing or foreign."""
    with use_db_session() as session:
        doc = (
            session.query(PdfDocument)
            .filter(PdfDocument.doc_id == doc_id, PdfDocument.user_id == user_id)
            .one_or_none()
        )
    if doc is None:
        raise PdfDocumentNotFoundError(doc_id)
    return doc


def _build_update_values(
    doc: PdfDocument,
    *,
    data: bytes | None,
    page_count: int | None,
    new_status: PdfDocStatus | None,
    placement: dict[str, Any] | None,
    audit_event: dict[str, Any] | None,
) -> dict[Any, Any]:
    """Validate the requested changes against ``doc`` and build the UPDATE values.

    Keyed loosely (``dict[Any, Any]``) to match ``Query.update``'s parameter
    type, whose key union is wider than ``str``.
    """
    values: dict[Any, Any] = {"updated_at": datetime.now(UTC)}
    if new_status is not None:
        # Self-transitions are invalid too (no X -> X edges exist): a racer
        # that loads the row AFTER a winner sealed it would otherwise see
        # signed == signed, skip the transition entirely, and silently
        # overwrite the winner's bytes + audit.
        current = PdfDocStatus(doc.status)
        if new_status not in _VALID_TRANSITIONS[current]:
            raise PdfInvalidTransitionError(
                doc_id=doc.doc_id, current=doc.status, requested=new_status
            )
        values["status"] = new_status.value
    if data is not None:
        _check_size(data)
        values["current_bytes"] = data
    if page_count is not None:
        values["page_count"] = page_count
    if placement is not _UNSET:
        values["placement"] = placement
    if audit_event is not None:
        stamped = {"at": datetime.now(UTC).isoformat(), **audit_event}
        # JSON columns don't track in-place mutation; reassign a new list.
        values["audit"] = [*(doc.audit or []), stamped]
    return values


def update_document(
    doc_id: str,
    user_id: str,
    *,
    data: bytes | None = None,
    page_count: int | None = None,
    new_status: PdfDocStatus | None = None,
    placement: dict[str, Any] | None = _UNSET,
    audit_event: dict[str, Any] | None = None,
    audit_only_if_status: PdfDocStatus | None = None,
) -> PdfDocument:
    """Apply bytes / status / placement / audit changes in one transaction.

    ``new_status`` is validated against the state machine; an invalid
    transition raises and nothing is written. ``audit_event`` is appended to
    the document's append-only audit list with a UTC timestamp.

    ``audit_only_if_status`` makes a non-transition write conditional: when
    the row's status differs (e.g. a rejected ceremony submission racing a
    successful seal), nothing is written and the current row is returned -
    keeping the audit chronology of terminal documents clean.

    Concurrency: status transitions are compare-and-set - the UPDATE is
    conditioned on the status observed in this transaction, so two racing
    transitions can never both win. The SELECT additionally takes FOR UPDATE
    where the backend honors it (Postgres), making the loser block-then-fail
    instead of fail-after-work; on SQLite (no row locks - reads run in
    autocommit before the write transaction opens) the CAS predicate alone
    guarantees single-winner semantics, with the loser raising
    :class:`PdfInvalidTransitionError`.
    """
    with use_db_session() as session:
        doc = (
            session.query(PdfDocument)
            .filter(PdfDocument.doc_id == doc_id, PdfDocument.user_id == user_id)
            .with_for_update()
            .one_or_none()
        )
        if doc is None:
            raise PdfDocumentNotFoundError(doc_id)
        if audit_only_if_status is not None and doc.status != audit_only_if_status:
            return doc  # condition not met - deliberately write nothing
        observed_status = doc.status
        values = _build_update_values(
            doc,
            data=data,
            page_count=page_count,
            new_status=new_status,
            placement=placement,
            audit_event=audit_event,
        )
        query = session.query(PdfDocument).filter(
            PdfDocument.doc_id == doc_id, PdfDocument.user_id == user_id
        )
        if new_status is not None or audit_only_if_status is not None:
            # CAS: only the writer that still sees the observed status wins.
            query = query.filter(PdfDocument.status == observed_status)
        updated = query.update(values, synchronize_session=False)
        if updated == 0:
            session.rollback()
            fresh = (
                session.query(PdfDocument)
                .filter(PdfDocument.doc_id == doc_id, PdfDocument.user_id == user_id)
                .one_or_none()
            )
            if fresh is None:
                # Concurrently deleted (TTL sweep) - never hand back the
                # stale pre-race row as though the write merely no-op'd.
                raise PdfDocumentNotFoundError(doc_id)
            if audit_only_if_status is not None and "status" not in values:
                return fresh
            raise PdfInvalidTransitionError(
                doc_id=doc_id,
                current=fresh.status,
                requested=new_status.value if new_status is not None else "?",
            )
        session.commit()
        session.refresh(doc)
    return doc


def sweep_expired_documents() -> int:
    """Delete sessions idle for longer than ``pdf_forms.session_ttl_hours``.

    Returns the number of rows removed. Signed documents expire too: the
    sealed artifact of record is whatever ``pdf_export`` delivered, not the
    session row.
    """
    ttl = timedelta(hours=global_config.pdf_forms.session_ttl_hours)
    cutoff = datetime.now(UTC) - ttl
    with use_db_session() as session:
        removed = (
            session.query(PdfDocument)
            .filter(PdfDocument.updated_at < cutoff)
            .delete(synchronize_session=False)
        )
        session.commit()
    return int(removed)
