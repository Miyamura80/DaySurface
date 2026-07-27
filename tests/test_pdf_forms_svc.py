"""Tests for the LLM-visible pdf_* services (US-002 onward).

The Gmail bridge is stubbed at the port layer (``resolve_source``) and the
document store runs on in-memory SQLite, so these tests exercise the real
pypdf/pypdfium2 paths end-to-end without Gmail or Postgres.
"""

import base64
from unittest.mock import patch

import pytest

from common import global_config
from models.pdf_forms import (
    AddTextOp,
    PdfEditInput,
    SetFieldOp,
)
from services import pdf_documents_repo as repo
from services import pdf_forms_svc
from services.pdf_edit_engine import PdfEditBatchError
from services.pdf_forms_svc import PdfDocumentLockedError, PdfEditSignedSourceError
from services.pdf_inspect import PdfEncryptedError, PdfNotAPdfError, inspect_pdf
from services.pdf_render import PdfRenderRequestError
from tests.pdf_fixtures import make_acroform_pdf, make_encrypted_pdf, make_flat_pdf
from tests.pdf_harness import PdfServiceTestBase


class TestPdfOpen(PdfServiceTestBase):
    def test_acroform_inventory(self):
        result = self._open(make_acroform_pdf())
        assert result.doc_id
        assert result.status == "open"
        assert result.filename == "nda.pdf"
        assert result.page_count == 1
        assert result.has_acroform is True
        by_name = {f.name: f for f in result.fields}
        assert by_name["full_name"].field_type == "text"
        assert by_name["full_name"].required is True
        assert by_name["full_name"].rect == [100.0, 600.0, 300.0, 620.0]
        assert by_name["full_name"].page == 1
        assert by_name["agree_terms"].field_type == "checkbox"
        assert set(by_name["agree_terms"].options) == {"/Yes", "/Off"}
        assert by_name["agree_terms"].value == "/Off"
        assert by_name["state"].field_type == "choice"
        assert by_name["state"].options == ["CA", "NY"]
        assert by_name["signature"].field_type == "signature"
        # AcroForm docs are filled by field name; no layout needed.
        assert result.text_layout == []

    def test_flat_pdf_text_layout(self):
        result = self._open(make_flat_pdf())
        assert result.has_acroform is False
        assert result.fields == []
        assert result.page_sizes[0].width == 612.0
        assert result.page_sizes[0].height == 792.0
        lines = {line.text: line for line in result.text_layout}
        assert "NON-DISCLOSURE AGREEMENT" in lines
        title = lines["NON-DISCLOSURE AGREEMENT"]
        assert title.page == 1
        assert title.x == pytest.approx(72.0)
        assert title.y == pytest.approx(700.0)
        assert "Signature:" in lines

    def test_text_layout_cap_sets_truncated(self):
        with patch.object(global_config.pdf_forms, "text_layout_max_lines", 2):
            result = self._open(make_flat_pdf())
        assert len(result.text_layout) == 2
        assert result.text_layout_truncated is True

    def test_open_persists_session(self):
        result = self._open(make_flat_pdf())
        doc = repo.load_document(result.doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_OPEN
        assert doc.current_bytes.startswith(b"%PDF-")
        assert (doc.source_ref or {}).get("type") == "gmail_attachment"
        assert doc.page_count == 1

    def test_non_pdf_names_detected_type(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        with pytest.raises(PdfNotAPdfError) as exc:
            self._open(png, filename="photo.png")
        assert "image/png" in str(exc.value)

    def test_render_pages_returns_png(self):
        result = self._open(make_flat_pdf(), render_pages=[1])
        assert len(result.page_images) == 1
        image = result.page_images[0]
        assert image.page == 1
        assert image.mime_type == "image/png"
        decoded = base64.b64decode(image.data_base64)
        assert decoded.startswith(b"\x89PNG\r\n\x1a\n")

    def test_render_pages_out_of_range(self):
        with pytest.raises(PdfRenderRequestError):
            self._open(make_flat_pdf(), render_pages=[5])

    def test_render_pages_over_cap(self):
        with (
            patch.object(global_config.pdf_forms, "render_max_pages", 2),
            pytest.raises(PdfRenderRequestError),
        ):
            self._open(make_flat_pdf(page_count=4), render_pages=[1, 2, 3])

    def test_clean_document_has_no_warnings(self):
        result = self._open(make_acroform_pdf())
        assert result.warnings == []
        assert result.existing_signatures == []

    def test_encrypted_pdf_rejected_cleanly(self):
        with pytest.raises(PdfEncryptedError) as exc:
            self._open(make_encrypted_pdf(), filename="locked.pdf")
        assert "password-protected" in str(exc.value)

    def test_xfa_form_warns(self):
        result = self._open(make_acroform_pdf(xfa=True))
        assert any("XFA" in w for w in result.warnings)

    def test_existing_signature_surfaced_and_warned(self):
        result = self._open(make_acroform_pdf(signed_sig_field=True))
        assert result.existing_signatures == ["signature"]
        assert any("invalidate" in w for w in result.warnings)
        # The inventory shows the signer's name, not the stringified sig dict.
        by_name = {f.name: f for f in result.fields}
        assert by_name["signature"].value == "Alice Example"


class TestPdfEdit(PdfServiceTestBase):
    def _edit(self, doc_id: str, ops, **kwargs):
        return pdf_forms_svc.pdf_edit(
            PdfEditInput(user_id="u1", doc_id=doc_id, ops=ops, **kwargs)
        )

    def test_fill_acroform_fields(self):
        opened = self._open(make_acroform_pdf())
        result = self._edit(
            opened.doc_id,
            [
                SetFieldOp(name="full_name", value="Eito Miyamura"),
                SetFieldOp(name="agree_terms", value="/Yes"),
                SetFieldOp(name="state", value="CA"),
            ],
        )
        assert result.applied_ops == 3
        assert result.status == "open"
        by_name = {f.name: f for f in result.fields}
        assert by_name["full_name"].value == "Eito Miyamura"
        assert by_name["agree_terms"].value == "/Yes"
        assert by_name["state"].value == "CA"
        # Values persisted in the stored bytes, not just the response.
        doc = repo.load_document(opened.doc_id, "u1")
        stored = {f.name: f for f in inspect_pdf(doc.current_bytes).fields}
        assert stored["full_name"].value == "Eito Miyamura"

    def test_checkbox_value_without_slash_is_normalized(self):
        opened = self._open(make_acroform_pdf())
        result = self._edit(
            opened.doc_id, [SetFieldOp(name="agree_terms", value="Yes")]
        )
        by_name = {f.name: f for f in result.fields}
        assert by_name["agree_terms"].value == "/Yes"

    def test_overlay_flat_pdf(self):
        opened = self._open(make_flat_pdf())
        result = self._edit(
            opened.doc_id,
            [AddTextOp(page=1, x=110.0, y=650.0, text="Eito Miyamura")],
        )
        assert result.applied_ops == 1
        doc = repo.load_document(opened.doc_id, "u1")
        layout = inspect_pdf(doc.current_bytes, include_text_layout=True)
        # Flat PDF stays flat; the overlay text is in the content stream.
        assert layout.has_acroform is False

    def test_mixed_batch(self):
        opened = self._open(make_acroform_pdf())
        result = self._edit(
            opened.doc_id,
            [
                SetFieldOp(name="full_name", value="A. Person"),
                AddTextOp(page=1, x=100.0, y=400.0, text="Initials: AP"),
            ],
        )
        assert result.applied_ops == 2

    def test_unknown_field_rejects_whole_batch(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(PdfEditBatchError) as exc:
            self._edit(
                opened.doc_id,
                [
                    SetFieldOp(name="full_name", value="Kept? No."),
                    SetFieldOp(name="does_not_exist", value="x"),
                ],
            )
        assert exc.value.errors[0]["index"] == 1
        assert "does_not_exist" in exc.value.errors[0]["error"]
        # Atomicity: the valid op must not have been applied.
        doc = repo.load_document(opened.doc_id, "u1")
        stored = {f.name: f for f in inspect_pdf(doc.current_bytes).fields}
        assert stored["full_name"].value is None

    def test_invalid_checkbox_state_rejected(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(PdfEditBatchError) as exc:
            self._edit(opened.doc_id, [SetFieldOp(name="agree_terms", value="Maybe")])
        assert "appearance states" in exc.value.errors[0]["error"]

    def test_invalid_choice_option_rejected(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(PdfEditBatchError):
            self._edit(opened.doc_id, [SetFieldOp(name="state", value="TX")])

    def test_page_out_of_range_rejected(self):
        opened = self._open(make_flat_pdf())
        with pytest.raises(PdfEditBatchError) as exc:
            self._edit(opened.doc_id, [AddTextOp(page=9, x=10.0, y=10.0, text="ghost")])
        assert "out of range" in exc.value.errors[0]["error"]

    def test_signature_field_cannot_be_set(self):
        opened = self._open(make_acroform_pdf())
        with pytest.raises(PdfEditBatchError) as exc:
            self._edit(opened.doc_id, [SetFieldOp(name="signature", value="Me")])
        assert "signature" in exc.value.errors[0]["error"]

    def test_edit_rejected_when_awaiting_signature(self):
        opened = self._open(make_acroform_pdf())
        repo.update_document(
            opened.doc_id, "u1", new_status=repo.PDF_STATUS_AWAITING_SIGNATURE
        )
        with pytest.raises(PdfDocumentLockedError):
            self._edit(opened.doc_id, [SetFieldOp(name="full_name", value="x")])

    def test_edit_rejected_when_signed(self):
        opened = self._open(make_acroform_pdf())
        repo.update_document(
            opened.doc_id, "u1", new_status=repo.PDF_STATUS_AWAITING_SIGNATURE
        )
        repo.update_document(opened.doc_id, "u1", new_status=repo.PDF_STATUS_SIGNED)
        with pytest.raises(PdfDocumentLockedError) as exc:
            self._edit(opened.doc_id, [SetFieldOp(name="full_name", value="x")])
        assert "immutable" in str(exc.value)

    def test_add_text_non_latin1_rejected(self):
        opened = self._open(make_flat_pdf())
        with pytest.raises(PdfEditBatchError) as exc:
            self._edit(
                opened.doc_id, [AddTextOp(page=1, x=110.0, y=650.0, text="宮村英人")]
            )
        assert "cannot render" in exc.value.errors[0]["error"]
        # Atomicity: nothing stamped.
        doc = repo.load_document(opened.doc_id, "u1")
        assert not any(
            "?" in line.text for line in inspect_pdf(doc.current_bytes).text_layout
        )

    def test_latin1_accents_still_accepted(self):
        opened = self._open(make_flat_pdf())
        result = self._edit(
            opened.doc_id, [AddTextOp(page=1, x=110.0, y=650.0, text="Renée Müller")]
        )
        assert result.applied_ops == 1

    def test_edit_presigned_requires_acknowledgement(self):
        opened = self._open(make_acroform_pdf(signed_sig_field=True))
        with pytest.raises(PdfEditSignedSourceError) as exc:
            self._edit(opened.doc_id, [SetFieldOp(name="full_name", value="Eito")])
        assert "invalidate" in str(exc.value)
        # Explicit acknowledgement unlocks the edit.
        result = self._edit(
            opened.doc_id,
            [SetFieldOp(name="full_name", value="Eito")],
            acknowledge_signature_invalidation=True,
        )
        assert result.applied_ops == 1

    def test_edit_returns_render(self):
        opened = self._open(make_flat_pdf())
        result = self._edit(
            opened.doc_id,
            [AddTextOp(page=1, x=110.0, y=650.0, text="Eito")],
            render_pages=[1],
        )
        assert len(result.page_images) == 1
        assert base64.b64decode(result.page_images[0].data_base64).startswith(
            b"\x89PNG"
        )
