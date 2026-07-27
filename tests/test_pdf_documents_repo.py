"""Tests for the pdf_documents session repository (US-001).

Runs against an in-memory SQLite engine by patching the repo module's
``use_db_session``, mirroring tests/test_idempotency.py.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from common import global_config
from db.models.pdf_documents import PdfDocument
from services import pdf_documents_repo as repo
from tests.pdf_harness import SqlitePdfRepoTestBase
from tests.test_template import TestTemplate

_PDF = b"%PDF-1.7 test-bytes"


class TestPdfDocumentModel(TestTemplate):
    def test_tablename_and_pk(self):
        assert PdfDocument.__tablename__ == "pdf_documents"
        pk = {c.name for c in PdfDocument.__table__.primary_key}
        assert pk == {"doc_id"}

    def test_columns(self):
        cols = {c.name for c in PdfDocument.__table__.columns}
        expected = {
            "doc_id",
            "user_id",
            "status",
            "filename",
            "original_bytes",
            "current_bytes",
            "page_count",
            "source_ref",
            "placement",
            "audit",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(cols)

    def test_no_foreign_keys(self):
        # Isolation seam: the PDF table must not reference Gmail (or any) tables.
        assert not list(PdfDocument.__table__.foreign_keys)


class TestPdfDocumentsRepo(SqlitePdfRepoTestBase):
    def _create(self, user_id="u1", **kwargs):
        defaults = {
            "user_id": user_id,
            "filename": "nda.pdf",
            "data": _PDF,
            "page_count": 2,
            "source_ref": {"type": "gmail_attachment", "message_id": "m1"},
        }
        defaults.update(kwargs)
        return repo.create_document(**defaults)

    def test_create_and_load_roundtrip(self):
        doc = self._create()
        assert doc.doc_id
        loaded = repo.load_document(doc.doc_id, "u1")
        assert loaded.status == repo.PDF_STATUS_OPEN
        assert loaded.filename == "nda.pdf"
        assert loaded.current_bytes == _PDF
        assert loaded.original_bytes == _PDF
        assert loaded.page_count == 2
        assert loaded.source_ref == {"type": "gmail_attachment", "message_id": "m1"}
        assert loaded.audit == []

    def test_load_missing_raises(self):
        with pytest.raises(repo.PdfDocumentNotFoundError):
            repo.load_document("nope", "u1")

    def test_load_is_user_scoped(self):
        doc = self._create(user_id="u1")
        with pytest.raises(repo.PdfDocumentNotFoundError):
            repo.load_document(doc.doc_id, "someone-else")

    def test_size_cap_on_create(self):
        with (
            patch.object(global_config.pdf_forms, "max_document_bytes", 8),
            pytest.raises(repo.PdfDocumentTooLargeError) as exc,
        ):
            self._create()
        assert exc.value.max_bytes == 8

    def test_size_cap_on_update(self):
        doc = self._create()
        with (
            patch.object(global_config.pdf_forms, "max_document_bytes", 8),
            pytest.raises(repo.PdfDocumentTooLargeError),
        ):
            repo.update_document(doc.doc_id, "u1", data=b"123456789")

    def test_update_bytes(self):
        doc = self._create()
        updated = repo.update_document(doc.doc_id, "u1", data=b"%PDF-1.7 edited")
        assert updated.current_bytes == b"%PDF-1.7 edited"
        # Original bytes are preserved for audit/diffing.
        assert updated.original_bytes == _PDF

    def test_full_signing_lifecycle(self):
        doc = self._create()
        doc = repo.update_document(
            doc.doc_id, "u1", new_status=repo.PDF_STATUS_AWAITING_SIGNATURE
        )
        assert doc.status == repo.PDF_STATUS_AWAITING_SIGNATURE
        doc = repo.update_document(doc.doc_id, "u1", new_status=repo.PDF_STATUS_SIGNED)
        assert doc.status == repo.PDF_STATUS_SIGNED

    def test_user_cancel_returns_to_open(self):
        doc = self._create()
        repo.update_document(
            doc.doc_id, "u1", new_status=repo.PDF_STATUS_AWAITING_SIGNATURE
        )
        doc = repo.update_document(doc.doc_id, "u1", new_status=repo.PDF_STATUS_OPEN)
        assert doc.status == repo.PDF_STATUS_OPEN

    def test_open_to_signed_is_invalid(self):
        doc = self._create()
        with pytest.raises(repo.PdfInvalidTransitionError):
            repo.update_document(doc.doc_id, "u1", new_status=repo.PDF_STATUS_SIGNED)

    def test_signed_is_terminal(self):
        doc = self._create()
        repo.update_document(
            doc.doc_id, "u1", new_status=repo.PDF_STATUS_AWAITING_SIGNATURE
        )
        repo.update_document(doc.doc_id, "u1", new_status=repo.PDF_STATUS_SIGNED)
        for target in (repo.PDF_STATUS_OPEN, repo.PDF_STATUS_AWAITING_SIGNATURE):
            with pytest.raises(repo.PdfInvalidTransitionError):
                repo.update_document(doc.doc_id, "u1", new_status=target)

    def test_invalid_transition_writes_nothing(self):
        doc = self._create()
        with pytest.raises(repo.PdfInvalidTransitionError):
            repo.update_document(
                doc.doc_id,
                "u1",
                data=b"%PDF-1.7 should-not-persist",
                new_status=repo.PDF_STATUS_SIGNED,
            )
        loaded = repo.load_document(doc.doc_id, "u1")
        assert loaded.current_bytes == _PDF

    def test_same_status_transition_rejected(self):
        # Self-transitions are invalid (no X -> X edges): treating them as a
        # no-op let a racer that loaded the row after a winner sealed it
        # slip past the CAS and overwrite the winner's bytes + audit (see
        # tests/test_pdf_concurrency.py). No caller requests X -> X on
        # purpose, so observing one always means a lost race.
        doc = self._create()
        with pytest.raises(repo.PdfInvalidTransitionError):
            repo.update_document(doc.doc_id, "u1", new_status=repo.PDF_STATUS_OPEN)
        assert repo.load_document(doc.doc_id, "u1").status == repo.PDF_STATUS_OPEN

    def test_placement_set_and_clear(self):
        doc = self._create()
        doc = repo.update_document(
            doc.doc_id, "u1", placement={"page": 1, "x": 72.0, "y": 100.0}
        )
        assert doc.placement == {"page": 1, "x": 72.0, "y": 100.0}
        doc = repo.update_document(doc.doc_id, "u1", placement=None)
        assert doc.placement is None

    def test_audit_appends_with_timestamp(self):
        doc = self._create()
        repo.update_document(doc.doc_id, "u1", audit_event={"event": "sign_requested"})
        doc = repo.update_document(
            doc.doc_id,
            "u1",
            audit_event={"event": "sign_aborted", "reason": "declined"},
        )
        audit = doc.audit or []
        assert [e["event"] for e in audit] == ["sign_requested", "sign_aborted"]
        assert all("at" in e for e in audit)

    def test_sweep_removes_expired_sessions(self):
        doc = self._create()
        fresh = self._create()
        stale = datetime.now(UTC) - timedelta(
            hours=global_config.pdf_forms.session_ttl_hours + 1
        )
        with self.SessionLocal() as session:
            row = session.get(PdfDocument, doc.doc_id)
            assert row is not None
            row.updated_at = stale
            session.commit()
        removed = repo.sweep_expired_documents()
        assert removed == 1
        with pytest.raises(repo.PdfDocumentNotFoundError):
            repo.load_document(doc.doc_id, "u1")
        assert repo.load_document(fresh.doc_id, "u1").doc_id == fresh.doc_id
