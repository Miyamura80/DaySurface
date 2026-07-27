"""LLM-visible PDF tools: open, edit, request-signature, export.

Part of the PDF core (isolation seam): no Gmail imports. Sources and
destinations resolve through the ports in ``services/pdf_ports.py``; the
Gmail adapters live in ``services/pdf_gmail_bridge.py``.

Security invariant: no service in this module (or anywhere LLM-visible)
produces a signature. Signing happens only in the app-only
``pdf_signer.sign`` tool or the host-elicitation fallback, both of which are
human-gated (see the PRD's three-layer model).

Coordinates are PDF user space: origin bottom-left, points (1/72 inch).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from models.pdf_forms import (
    PdfDocStatus,
    PdfEditInput,
    PdfEditResult,
    PdfExportInput,
    PdfExportResult,
    PdfOpenInput,
    PdfOpenResult,
    PdfRequestSignatureInput,
    PdfRequestSignatureResult,
    SignaturePlacement,
)
from services import service
from services.pdf_documents_repo import (
    PdfInvalidTransitionError,
    create_document,
    load_document,
    sweep_expired_documents,
    update_document,
)
from services.pdf_edit_engine import apply_ops
from services.pdf_inspect import inspect_pdf
from services.pdf_ports import deliver_to_destination, resolve_source
from services.pdf_render import render_pages


class PdfEditSignedSourceError(Exception):
    """Editing a document that carries third-party signatures needs an ack."""

    def __init__(self, signatures: list[str]) -> None:
        names = ", ".join(signatures)
        super().__init__(
            f"This document already carries digital signature(s) ({names}); "
            "editing will cryptographically invalidate them. If the user "
            "explicitly confirms they accept that, retry with "
            "acknowledge_signature_invalidation=true."
        )


class PdfDocumentLockedError(Exception):
    """Raised when an edit targets a document that is no longer editable."""

    def __init__(self, *, doc_id: str, status: str) -> None:
        self.doc_id = doc_id
        self.status = status
        if status == PdfDocStatus.AWAITING_SIGNATURE:
            detail = (
                "a signature has been requested; the user must sign (or cancel "
                "in the signing UI) before further edits"
            )
        else:
            detail = "signed documents are immutable"
        super().__init__(f"Document {doc_id!r} is {status!r}: {detail}.")


def _document_warnings(inspection) -> list[str]:
    """Non-fatal caveats surfaced by pdf_open (and disclosed downstream)."""
    warnings: list[str] = []
    if inspection.existing_signatures:
        names = ", ".join(inspection.existing_signatures)
        warnings.append(
            f"This document already carries digital signature(s) ({names}). "
            "Any edit - and the signing ceremony itself - will "
            "cryptographically invalidate them. pdf_edit requires "
            "acknowledge_signature_invalidation=true; tell the user before "
            "proceeding."
        )
    if inspection.has_xfa:
        warnings.append(
            "This is an XFA (LiveCycle) form. The field inventory shown here "
            "is the AcroForm view, which may be incomplete, and edits may "
            "not appear in XFA-only viewers. Verify results with "
            "render_pages before exporting."
        )
    return warnings


@service(
    name="pdf_open",
    description=(
        "Open a PDF into a server-side editing session and return everything "
        "needed to plan edits: a doc_id handle, the fillable form-field "
        "inventory (name, type, current value, page, rect), and - for flat "
        "PDFs without form fields - the existing text layout with coordinates "
        "for anchoring text overlays. The PDF bytes stay server-side; every "
        "later pdf_* call takes the doc_id. Optionally pass render_pages "
        "(1-based page numbers) to also get PNG renders of pages for visual "
        "inspection. Coordinates are PDF user space: origin at the page's "
        "bottom-left corner, in points (1/72 inch)."
    ),
    input_model=PdfOpenInput,
    output_model=PdfOpenResult,
    mutating=True,
)
def pdf_open(input: PdfOpenInput) -> PdfOpenResult:
    # Session init doubles as the retention hook (long-running pattern:
    # init -> continue(id) -> cleanup): sweep expired sessions up front.
    sweep_expired_documents()
    resolved = resolve_source(input.user_id, input.source)
    inspection = inspect_pdf(resolved.data)
    # Render BEFORE persisting: a rejected render_pages request must not
    # leave an orphaned session row behind the error.
    page_images = render_pages(resolved.data, input.render_pages, inspection.page_count)
    doc = create_document(
        user_id=input.user_id,
        filename=resolved.filename,
        data=resolved.data,
        page_count=inspection.page_count,
        source_ref=input.source.model_dump(),
    )
    return PdfOpenResult(
        doc_id=doc.doc_id,
        filename=doc.filename,
        status=PdfDocStatus.OPEN,
        page_count=inspection.page_count,
        page_sizes=inspection.page_sizes,
        has_acroform=inspection.has_acroform,
        fields=inspection.fields,
        text_layout=inspection.text_layout,
        text_layout_truncated=inspection.text_layout_truncated,
        page_images=page_images,
        existing_signatures=inspection.existing_signatures,
        warnings=_document_warnings(inspection),
    )


@service(
    name="pdf_edit",
    description=(
        "Apply a batch of edits to an open PDF document session. Ops: "
        "{op:'set_field', name, value} fills an AcroForm field (checkbox/"
        "radio/choice values must match the field's options); {op:'add_text', "
        "page, x, y, text, font_size?} stamps a text overlay onto a flat PDF "
        "at PDF user-space coordinates (origin bottom-left, points). The "
        "batch is atomic: any invalid op rejects the whole batch and the "
        "document is unchanged. Pass render_pages (1-based) to get PNG "
        "renders back for verifying placement. Rejected once a signature has "
        "been requested or the document is signed."
    ),
    input_model=PdfEditInput,
    output_model=PdfEditResult,
    mutating=True,
)
def pdf_edit(input: PdfEditInput) -> PdfEditResult:
    doc = load_document(input.doc_id, input.user_id)
    if doc.status != PdfDocStatus.OPEN:
        raise PdfDocumentLockedError(doc_id=input.doc_id, status=doc.status)
    # Docs carrying third-party signatures: a pypdf rewrite invalidates them,
    # so require an explicit, user-confirmed acknowledgement first.
    pre = inspect_pdf(doc.current_bytes, include_text_layout=False)
    if pre.existing_signatures and not input.acknowledge_signature_invalidation:
        raise PdfEditSignedSourceError(pre.existing_signatures)
    new_bytes = apply_ops(doc.current_bytes, input.ops)
    inspection = inspect_pdf(new_bytes, include_text_layout=False)
    # Render BEFORE persisting: a rejected render_pages request must fail the
    # whole call with the document unchanged, or a retry would re-apply the
    # overlay ops on top of the committed first attempt.
    page_images = render_pages(new_bytes, input.render_pages, inspection.page_count)
    update_document(
        input.doc_id,
        input.user_id,
        data=new_bytes,
        page_count=inspection.page_count,
    )
    return PdfEditResult(
        doc_id=input.doc_id,
        status=PdfDocStatus.OPEN,
        applied_ops=len(input.ops),
        fields=inspection.fields,
        page_images=page_images,
    )


class PdfSignatureRequestError(Exception):
    """Raised when a signature request is invalid for the document."""


_AWAITING_GUIDANCE = (
    "The document is now locked for signing. Only the user can sign: they "
    "must review the document and type their full legal name in the signing "
    "UI (or the confirmation dialog their client shows). You cannot sign on "
    "their behalf or complete this step - tell the user the document is "
    "ready for their signature."
)


def _validate_placement(placement: SignaturePlacement, data: bytes):
    """Document-dependent placement checks; returns the inspection.

    The shape half (field_name XOR complete page/x/y) is enforced by
    ``SignaturePlacement``'s own model validator; only the checks that need
    the actual document live here.
    """
    inspection = inspect_pdf(data, include_text_layout=False)
    if placement.is_field_based():
        field = next(
            (f for f in inspection.fields if f.name == placement.field_name), None
        )
        if field is None:
            raise PdfSignatureRequestError(
                f"No field named {placement.field_name!r} in this document."
            )
        if field.field_type != "signature":
            raise PdfSignatureRequestError(
                f"Field {placement.field_name!r} is a {field.field_type} field, "
                "not a signature field. Use {page, x, y} placement instead."
            )
    elif placement.page is not None and placement.page > inspection.page_count:
        raise PdfSignatureRequestError(
            f"placement page {placement.page} out of range "
            f"(document has {inspection.page_count} pages)."
        )
    elif placement.x is not None and placement.y is not None:
        # Anchor must be on the page: an off-page stamp would lock the
        # ceremony onto a signature the user can never see.
        size = inspection.page_sizes[(placement.page or 1) - 1]
        if not (0 <= placement.x <= size.width and 0 <= placement.y <= size.height):
            raise PdfSignatureRequestError(
                f"placement ({placement.x}, {placement.y}) is outside page "
                f"{placement.page}'s bounds ({size.width} x {size.height} pt)."
            )
    return inspection


@service(
    name="pdf_request_signature",
    description=(
        "Hand a filled PDF document over to the user for signing. Locks the "
        "document (no further edits) and opens the signing ceremony: the user "
        "reviews the document and types their full legal name themselves - "
        "signing can never be performed by the assistant. placement says "
        "where the signature stamp goes: a signature field's name from "
        "pdf_open's fields[], or {page, x, y} in PDF user-space coordinates "
        "(origin bottom-left, points) for flat PDFs. Call pdf_export "
        "afterwards to deliver the signed file."
    ),
    input_model=PdfRequestSignatureInput,
    output_model=PdfRequestSignatureResult,
    mutating=True,
)
def pdf_request_signature(
    input: PdfRequestSignatureInput,
) -> PdfRequestSignatureResult:
    doc = load_document(input.doc_id, input.user_id)
    if doc.status == PdfDocStatus.SIGNED:
        raise PdfSignatureRequestError(
            f"Document {input.doc_id!r} is already signed; re-signing is not supported."
        )
    if doc.status == PdfDocStatus.AWAITING_SIGNATURE:
        # Idempotent re-request: keep the recorded placement, re-emit guidance
        # (and, via the enhancer, re-open the signing app).
        return PdfRequestSignatureResult(
            doc_id=input.doc_id,
            status="awaiting_user_signature",
            guidance=_AWAITING_GUIDANCE,
        )
    inspection = _validate_placement(input.placement, doc.current_bytes)
    guidance = _AWAITING_GUIDANCE
    if inspection.existing_signatures:
        # A pypdf rewrite at signing time invalidates third-party signatures;
        # the user must hear that BEFORE they sign, not discover it after.
        names = ", ".join(inspection.existing_signatures)
        guidance += (
            f" IMPORTANT: this document already carries digital signature(s) "
            f"({names}) which the new signature will cryptographically "
            "invalidate - make sure the user knows before they sign."
        )
    try:
        update_document(
            input.doc_id,
            input.user_id,
            new_status=PdfDocStatus.AWAITING_SIGNATURE,
            placement=input.placement.model_dump(),
            audit_event={
                "event": "signature_requested",
                "placement": input.placement.model_dump(),
                "invalidates_existing_signatures": inspection.existing_signatures,
            },
        )
    except PdfInvalidTransitionError:
        # Concurrent request won the open -> awaiting race (the repo's row
        # lock makes the transition atomic). Its placement stands; behave
        # exactly like the idempotent re-request branch above.
        return PdfRequestSignatureResult(
            doc_id=input.doc_id,
            status="awaiting_user_signature",
            guidance=_AWAITING_GUIDANCE,
        )
    return PdfRequestSignatureResult(
        doc_id=input.doc_id,
        status="awaiting_user_signature",
        guidance=guidance,
    )


class PdfExportStateError(Exception):
    """Raised when exporting a document that is mid-signing."""

    def __init__(self, doc_id: str) -> None:
        super().__init__(
            f"Document {doc_id!r} is awaiting the user's signature. Wait for "
            "them to sign (or cancel in the signing UI) before exporting."
        )


def _default_export_filename(original: str, signed: bool) -> str:
    stem = PurePosixPath(original).stem or "document"
    suffix = "signed" if signed else "filled"
    return f"{stem}-{suffix}.pdf"


@service(
    name="pdf_export",
    description=(
        "Deliver a PDF document session to its destination - v1: attach it "
        "to an existing Gmail draft, server-side (the PDF bytes never enter "
        "this conversation). Default filename is '{original-stem}-signed.pdf' "
        "for signed documents, '{original-stem}-filled.pdf' otherwise; "
        "override with destination.filename. Returns the draft's resulting "
        "attachment list. Rejected while a signature request is pending."
    ),
    input_model=PdfExportInput,
    output_model=PdfExportResult,
    mutating=True,
)
def pdf_export(input: PdfExportInput) -> PdfExportResult:
    doc = load_document(input.doc_id, input.user_id)
    if doc.status == PdfDocStatus.AWAITING_SIGNATURE:
        raise PdfExportStateError(input.doc_id)
    signed = doc.status == PdfDocStatus.SIGNED
    filename = input.destination.filename or _default_export_filename(
        doc.filename, signed
    )
    delivery = deliver_to_destination(
        input.user_id, input.destination, filename, doc.current_bytes
    )
    update_document(
        input.doc_id,
        input.user_id,
        audit_event={
            "event": "exported",
            "destination_type": input.destination.type,
            "filename": filename,
        },
    )
    return PdfExportResult(
        doc_id=input.doc_id,
        status="signed" if signed else "open",
        filename=filename,
        destination_type=input.destination.type,
        draft_id=delivery.ref_id,
        attachments=delivery.attachments,
    )
