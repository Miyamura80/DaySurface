"""Shared test harness for the pdf_* suites.

Test modules must not import each other (hidden collection-order coupling),
so the SQLite-backed repo patch and the layered base classes live here, next
to ``tests/pdf_fixtures.py``:

- :class:`SqlitePdfRepoTestBase` - in-memory SQLite behind the document repo.
- :class:`PdfServiceTestBase` - adds the stubbed source port + ``_open``.
- :class:`PdfSigningTestBase` - adds the dev-cert dir + ceremony helpers.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import global_config
from db.base import Base
from models.pdf_forms import (
    GmailAttachmentSource,
    PdfOpenInput,
    PdfRequestSignatureInput,
    SignaturePlacement,
)
from services import pdf_documents_repo as repo
from services import pdf_forms_svc, pdf_signing
from services.pdf_ports import ResolvedPdfSource
from tests.pdf_fixtures import make_acroform_pdf
from tests.test_template import TestTemplate

_SOURCE = GmailAttachmentSource(message_id="m1", attachment_id="a1")

# One dev cert per test run: generated on first use, reused from disk after.
_CERT_DIR = tempfile.mkdtemp(prefix="pdf-signing-certs-")


def make_sqlite_session_factory() -> sessionmaker:
    """Fresh in-memory SQLite with the full schema created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class SqlitePdfRepoTestBase(TestTemplate):
    """Runs the document repo against in-memory SQLite."""

    def setup_method(self):
        self.SessionLocal = make_sqlite_session_factory()

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        self._patchers: list = [patch.object(repo, "use_db_session", _ctx)]
        for p in self._patchers:
            p.start()

    def teardown_method(self):
        for p in self._patchers:
            p.stop()


class PdfServiceTestBase(SqlitePdfRepoTestBase):
    """Adds the source port stubbed to fixture bytes + an ``_open`` helper."""

    def _stub_source(self, data: bytes, filename: str = "nda.pdf"):
        return patch.object(
            pdf_forms_svc,
            "resolve_source",
            lambda user_id, source: ResolvedPdfSource(filename=filename, data=data),
        )

    def _open(self, data: bytes, filename: str = "nda.pdf", **kwargs):
        with self._stub_source(data, filename):
            return pdf_forms_svc.pdf_open(
                PdfOpenInput(user_id="u1", source=_SOURCE, **kwargs)
            )


class PdfSigningTestBase(PdfServiceTestBase):
    """Adds the cached dev sealing cert + ceremony drive helpers."""

    def setup_method(self):
        super().setup_method()
        cert_patch = patch.object(
            global_config.pdf_forms.signing, "dev_cert_dir", _CERT_DIR
        )
        cert_patch.start()
        self._patchers.append(cert_patch)

    def _request(self, doc_id: str, placement: SignaturePlacement):
        return pdf_forms_svc.pdf_request_signature(
            PdfRequestSignatureInput(user_id="u1", doc_id=doc_id, placement=placement)
        )

    def _open_awaiting(self, data: bytes | None = None) -> str:
        """Open a doc and drive it to awaiting_signature on the sig field."""
        opened = self._open(data if data is not None else make_acroform_pdf())
        self._request(opened.doc_id, SignaturePlacement(field_name="signature"))
        return opened.doc_id

    def _sign(self, doc_id: str, **overrides):
        kwargs = {
            "doc_id": doc_id,
            "user_id": "u1",
            "typed_name": "Eito Miyamura",
            "consent": True,
            "channel": "app",
            "confirmed_via_elicitation": True,
        }
        kwargs.update(overrides)
        return pdf_signing.perform_signing(**kwargs)
