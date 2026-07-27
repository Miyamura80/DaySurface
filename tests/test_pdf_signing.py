"""Tests for the signing gate (US-005) and the signing engine (US-007).

Covers the pdf_request_signature state machine, the stamp + audit + PAdES
seal pipeline, seal validation against the dev certificate, and tamper
detection - the PRD's success metrics for the ceremony.
"""

import io

import pytest
from pydantic import ValidationError

from models.pdf_forms import SignaturePlacement
from services import pdf_documents_repo as repo
from services import pdf_signing
from services.pdf_forms_svc import PdfSignatureRequestError
from tests.pdf_fixtures import make_acroform_pdf, make_flat_pdf
from tests.pdf_harness import PdfSigningTestBase


def _validate_seal(data: bytes):
    """Validate the newest embedded signature against the dev trust root."""
    from pyhanko.keys import load_cert_from_pemder  # noqa: PLC0415 - test-local
    from pyhanko.pdf_utils.reader import PdfFileReader  # noqa: PLC0415
    from pyhanko.sign.validation import validate_pdf_signature  # noqa: PLC0415
    from pyhanko_certvalidator import ValidationContext  # noqa: PLC0415

    cert_path, _ = pdf_signing.sealing_cert_paths()
    vc = ValidationContext(
        trust_roots=[load_cert_from_pemder(str(cert_path))], allow_fetching=False
    )
    reader = PdfFileReader(io.BytesIO(data))
    assert reader.embedded_signatures, "no embedded signature found"
    return validate_pdf_signature(reader.embedded_signatures[0], vc)


class TestPdfRequestSignature(PdfSigningTestBase):
    def test_field_placement_locks_document(self):
        opened = self._open(make_acroform_pdf())
        result = self._request(
            opened.doc_id, SignaturePlacement(field_name="signature")
        )
        assert result.status == "awaiting_user_signature"
        assert "cannot sign" in result.guidance.lower() or "user" in result.guidance
        doc = repo.load_document(opened.doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_AWAITING_SIGNATURE
        assert (doc.placement or {}).get("field_name") == "signature"
        assert (doc.audit or [])[-1]["event"] == "signature_requested"

    def test_coordinate_placement_for_flat_pdf(self):
        opened = self._open(make_flat_pdf())
        result = self._request(
            opened.doc_id, SignaturePlacement(page=1, x=140.0, y=545.0)
        )
        assert result.status == "awaiting_user_signature"

    def test_non_signature_field_rejected(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(PdfSignatureRequestError) as exc:
            self._request(opened.doc_id, SignaturePlacement(field_name="full_name"))
        assert "not a signature field" in str(exc.value)

    def test_unknown_field_rejected(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(PdfSignatureRequestError):
            self._request(opened.doc_id, SignaturePlacement(field_name="ghost"))

    def test_both_placement_forms_rejected_by_model(self):
        # The shape rule (XOR) lives on the model itself, so an invalid
        # placement never even reaches the service.
        with pytest.raises(ValidationError, match="not both"):
            SignaturePlacement(field_name="signature", page=1, x=1.0, y=1.0)

    def test_incomplete_coordinates_rejected_by_model(self):
        with pytest.raises(ValidationError, match="all of page, x, y"):
            SignaturePlacement(page=1, x=10.0)

    def test_page_out_of_range_rejected(self):
        opened = self._open(make_flat_pdf())
        with pytest.raises(PdfSignatureRequestError):
            self._request(opened.doc_id, SignaturePlacement(page=7, x=1.0, y=1.0))

    def test_second_request_is_idempotent(self):
        doc_id = self._open_awaiting()
        result = self._request(doc_id, SignaturePlacement(field_name="signature"))
        assert result.status == "awaiting_user_signature"

    def test_presigned_document_disclosed_in_guidance(self):
        opened = self._open(make_acroform_pdf(signed_sig_field=True))
        result = self._request(
            opened.doc_id, SignaturePlacement(page=1, x=140.0, y=545.0)
        )
        assert "invalidate" in result.guidance
        doc = repo.load_document(opened.doc_id, "u1")
        last = (doc.audit or [])[-1]
        assert last["invalidates_existing_signatures"] == ["signature"]

    def test_already_signed_rejected(self):
        doc_id = self._open_awaiting()
        self._sign(doc_id)
        with pytest.raises(PdfSignatureRequestError) as exc:
            self._request(doc_id, SignaturePlacement(field_name="signature"))
        assert "already signed" in str(exc.value)


class TestPerformSigning(PdfSigningTestBase):
    def test_full_ceremony_produces_valid_seal(self):
        doc_id = self._open_awaiting()
        doc, audit = self._sign(doc_id)
        assert doc.status == repo.PDF_STATUS_SIGNED
        # Visible stamp + printed metadata are in the (uncompressed) bytes.
        assert b"Eito Miyamura" in doc.current_bytes
        assert b"Signed by Eito Miyamura" in doc.current_bytes
        # Audit trail embedded in the PDF and recorded on the session.
        assert b"MyMCPSignatureAudit" in doc.current_bytes
        last = (doc.audit or [])[-1]
        assert last["event"] == "signed"
        assert last["typed_name"] == "Eito Miyamura"
        assert last["consent"] is True
        assert last["channel"] == "app"
        assert last["confirmed_via_elicitation"] is True
        assert len(last["document_sha256"]) == 64
        # The PAdES seal verifies against the server certificate.
        status = _validate_seal(doc.current_bytes)
        assert status.intact
        assert status.valid
        assert status.trusted

    def test_coordinate_placement_stamp(self):
        opened = self._open(make_flat_pdf())
        self._request(opened.doc_id, SignaturePlacement(page=1, x=140.0, y=545.0))
        doc, _ = self._sign(opened.doc_id)
        assert b"Eito Miyamura" in doc.current_bytes
        assert _validate_seal(doc.current_bytes).intact

    def test_tampering_is_detected(self):
        doc_id = self._open_awaiting()
        doc, _ = self._sign(doc_id)
        tampered = doc.current_bytes.replace(b"Signed by", b"S1gned by", 1)
        assert tampered != doc.current_bytes
        status = _validate_seal(tampered)
        assert status.intact is False

    def test_wrong_state_rejected(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(pdf_signing.PdfSigningStateError):
            self._sign(opened.doc_id)

    def test_double_sign_rejected(self):
        doc_id = self._open_awaiting()
        self._sign(doc_id)
        with pytest.raises(pdf_signing.PdfSigningStateError):
            self._sign(doc_id)

    def test_empty_name_rejected(self):
        doc_id = self._open_awaiting()
        with pytest.raises(pdf_signing.PdfSigningInputError):
            self._sign(doc_id, typed_name="   ")
        # Failed attempt must not consume the awaiting state.
        assert (
            repo.load_document(doc_id, "u1").status
            == repo.PDF_STATUS_AWAITING_SIGNATURE
        )

    def test_missing_consent_rejected(self):
        doc_id = self._open_awaiting()
        with pytest.raises(pdf_signing.PdfSigningInputError):
            self._sign(doc_id, consent=False)

    def test_stale_rejection_never_audits_after_signed(self):
        # Race: a rejected submission carrying a stale 'awaiting' row must
        # not append sign_rejected to a document that has since been sealed.
        doc_id = self._open_awaiting()
        stale = repo.load_document(doc_id, "u1")
        self._sign(doc_id)
        with pytest.raises(pdf_signing.PdfSigningInputError):
            pdf_signing.validate_ceremony(stale, "   ", True)
        events = [e["event"] for e in repo.load_document(doc_id, "u1").audit or []]
        assert "sign_rejected" not in events
        assert events[-1] == "signed"

    def test_rejected_submission_is_audited_while_awaiting(self):
        doc_id = self._open_awaiting()
        doc = repo.load_document(doc_id, "u1")
        with pytest.raises(pdf_signing.PdfSigningInputError):
            pdf_signing.validate_ceremony(doc, "", True)
        events = [e["event"] for e in repo.load_document(doc_id, "u1").audit or []]
        assert events[-1] == "sign_rejected"

    def test_non_latin1_name_rejected(self):
        # The stamp face is standard-14 Helvetica (Latin-1): a CJK name would
        # silently render as '?' on a legal document, so it must be refused.
        doc_id = self._open_awaiting()
        with pytest.raises(pdf_signing.PdfSigningInputError) as exc:
            self._sign(doc_id, typed_name="宮村英人")
        assert "cannot render" in str(exc.value)
        assert (
            repo.load_document(doc_id, "u1").status
            == repo.PDF_STATUS_AWAITING_SIGNATURE
        )

    def test_signed_output_reports_seal_on_reinspection(self):
        # Round-trip: our own sealed output, re-opened, must surface as a
        # document carrying an existing signature (gap-2 detection works on
        # real pyHanko signatures, not just synthetic /V dicts). Field-based
        # placement signs the USER'S chosen field, not a separate seal field.
        from services.pdf_inspect import inspect_pdf  # noqa: PLC0415 - test-local

        doc_id = self._open_awaiting()
        doc, _ = self._sign(doc_id)
        inspection = inspect_pdf(doc.current_bytes, include_text_layout=False)
        assert inspection.existing_signatures == ["signature"]

    def test_coordinate_placement_seals_platform_field(self):
        # Flat PDFs have no signature field to sign into; the seal creates
        # the platform field.
        from services.pdf_inspect import inspect_pdf  # noqa: PLC0415 - test-local

        opened = self._open(make_flat_pdf())
        self._request(opened.doc_id, SignaturePlacement(page=1, x=140.0, y=545.0))
        doc, _ = self._sign(opened.doc_id)
        inspection = inspect_pdf(doc.current_bytes, include_text_layout=False)
        assert inspection.existing_signatures == ["MyMCP-Seal"]

    def test_abort_back_to_open(self):
        doc_id = self._open_awaiting()
        doc = pdf_signing.abort_signing(
            doc_id=doc_id,
            user_id="u1",
            reason="user_cancelled_in_app",
            channel="app",
            back_to_open=True,
        )
        assert doc.status == repo.PDF_STATUS_OPEN
        assert (doc.audit or [])[-1]["event"] == "sign_aborted"

    def test_abort_stays_awaiting(self):
        doc_id = self._open_awaiting()
        doc = pdf_signing.abort_signing(
            doc_id=doc_id,
            user_id="u1",
            reason="host_confirmation_declined",
            channel="app",
            back_to_open=False,
        )
        assert doc.status == repo.PDF_STATUS_AWAITING_SIGNATURE
        assert (doc.audit or [])[-1]["reason"] == "host_confirmation_declined"

    def test_dev_cert_is_cached(self):
        first = pdf_signing.sealing_cert_paths()
        stat_before = first[0].stat().st_mtime_ns
        second = pdf_signing.sealing_cert_paths()
        assert first == second
        assert second[0].stat().st_mtime_ns == stat_before
