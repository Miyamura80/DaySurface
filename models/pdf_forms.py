"""Pydantic contracts for the PDF form-filling + user-gated signing tools.

Part of the PDF core (isolation seam): no imports from ``models.gmail``.
Sources and destinations are discriminated unions on ``type`` - v1 ships the
Gmail variants only, but new variants (upload, URL, filesystem) slot in
without touching the tools' signatures. Note for future extraction: the
Gmail-shaped variants (``GmailAttachmentSource``, ``GmailDraftDestination``)
live here as pure locator contracts, so pulling the PDF domain out as an
add-on means excising those two models as well as swapping the bridge.

Coordinates are PDF user space: origin at the page's bottom-left corner,
units are points (1/72 inch). All tools share this convention.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class PdfDocStatus(StrEnum):
    """The document-session state machine's states (single source of truth).

    Transitions are owned by ``services/pdf_documents_repo.py``:
    open -> awaiting_signature -> signed (terminal), with
    awaiting_signature -> open as the user-cancel path.
    """

    OPEN = "open"
    AWAITING_SIGNATURE = "awaiting_signature"
    SIGNED = "signed"


# AcroForm field kinds as surfaced to the LLM.
PdfFieldType = Literal["text", "checkbox", "radio", "choice", "signature", "unknown"]

# ---------------------------------------------------------------------------
# Sources (pdf_open) and destinations (pdf_export)
# ---------------------------------------------------------------------------


class GmailAttachmentSource(BaseModel):
    """Locator for a PDF arriving as a Gmail attachment.

    Resolved server-side by the Gmail bridge - the bytes never travel
    through the model's context.
    """

    type: Literal["gmail_attachment"] = "gmail_attachment"
    message_id: str = Field(
        description="Id of the message the attachment lives on (from gmail_get_thread)",
        min_length=1,
    )
    attachment_id: str = Field(
        description="Stable attachment id from a gmail_get_thread attachment entry",
        min_length=1,
    )


# v1 has a single variant; becomes a discriminated union (Field
# discriminator="type") as soon as a second source type lands.
PdfSource = GmailAttachmentSource


class GmailDraftDestination(BaseModel):
    """Attach the exported PDF to an existing Gmail draft, server-side."""

    type: Literal["gmail_draft"] = "gmail_draft"
    draft_id: str = Field(min_length=1)
    filename: str | None = Field(
        default=None,
        description=(
            "Attachment filename. Defaults to '{original-stem}-signed.pdf' for "
            "signed documents, '{original-stem}-filled.pdf' otherwise."
        ),
    )


# v1 has a single variant; becomes a discriminated union alongside PdfSource.
PdfDestination = GmailDraftDestination


# ---------------------------------------------------------------------------
# Shared document shapes
# ---------------------------------------------------------------------------


class PdfFormField(BaseModel):
    """One AcroForm field, flattened for the LLM to reason about."""

    name: str = Field(description="Fully-qualified field name (use in set_field)")
    field_type: PdfFieldType
    value: str | None = Field(default=None, description="Current value, if any")
    page: int | None = Field(
        default=None, description="1-based page the field's widget sits on"
    )
    rect: list[float] | None = Field(
        default=None,
        description="Widget rectangle [x0, y0, x1, y1] in PDF user space",
    )
    options: list[str] | None = Field(
        default=None,
        description=(
            "Allowed values: export options for choice fields, appearance "
            "states (e.g. ['/Off', '/Yes']) for checkboxes and radios"
        ),
    )
    required: bool = False
    read_only: bool = False


class PdfTextLine(BaseModel):
    """A line of existing text with its anchor position, for flat-PDF overlays."""

    page: int = Field(description="1-based page number")
    x: float = Field(description="Left edge of the line, PDF user space")
    y: float = Field(description="Baseline of the line, PDF user space")
    text: str


class PdfPageSize(BaseModel):
    page: int = Field(description="1-based page number")
    width: float
    height: float


class PdfPageImage(BaseModel):
    """A rasterized page (PNG) for visual verification of edits."""

    page: int = Field(description="1-based page number")
    mime_type: str = "image/png"
    data_base64: str


# ---------------------------------------------------------------------------
# pdf_open
# ---------------------------------------------------------------------------


class PdfOpenInput(BaseModel):
    user_id: str = ""
    source: PdfSource = Field(description="Where to fetch the PDF from")
    render_pages: list[int] = Field(
        default_factory=list,
        description=(
            "1-based page numbers to rasterize into the response as PNG images "
            "for visual inspection (capped by pdf_forms.render_max_pages)"
        ),
    )


class PdfOpenResult(BaseModel):
    doc_id: str = Field(description="Session handle for all subsequent pdf_* calls")
    filename: str
    status: PdfDocStatus
    page_count: int
    page_sizes: list[PdfPageSize] = Field(default_factory=list)
    has_acroform: bool = Field(
        description="True if the PDF has fillable form fields (use set_field); "
        "false means a flat PDF (use add_text overlays anchored on text_layout)"
    )
    fields: list[PdfFormField] = Field(default_factory=list)
    text_layout: list[PdfTextLine] = Field(
        default_factory=list,
        description="Existing text lines with coordinates (flat PDFs only), "
        "for anchoring add_text overlays",
    )
    text_layout_truncated: bool = Field(
        default=False,
        description="True if text_layout was cut at pdf_forms.text_layout_max_lines",
    )
    page_images: list[PdfPageImage] = Field(default_factory=list)
    existing_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Signature fields that already hold a digital signature. Editing "
            "such a document cryptographically invalidates them - pdf_edit "
            "requires acknowledge_signature_invalidation=true, and the user "
            "must be told before proceeding."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal caveats about this document (existing digital "
            "signatures, XFA forms). Relay these to the user before planning "
            "edits."
        ),
    )


# ---------------------------------------------------------------------------
# pdf_edit
# ---------------------------------------------------------------------------


class SetFieldOp(BaseModel):
    """Set an AcroForm field's value (text, checkbox, radio, choice)."""

    op: Literal["set_field"] = "set_field"
    name: str = Field(description="Field name from pdf_open's fields[]")
    value: str = Field(
        description=(
            "New value. Text fields: any string. Checkboxes/radios: one of the "
            "field's options (e.g. '/Yes'; '/Off' clears). Choice fields: one "
            "of the listed options."
        )
    )


class AddTextOp(BaseModel):
    """Stamp a text overlay onto a page (for flat PDFs without form fields)."""

    op: Literal["add_text"] = "add_text"
    page: int = Field(ge=1, description="1-based page number")
    # allow_inf_nan=False throughout: a NaN/inf coordinate would serialize
    # into the content stream as 'nan'/'inf', producing a broken PDF.
    x: float = Field(
        allow_inf_nan=False,
        description="Left edge of the text, PDF user space (points)",
    )
    y: float = Field(
        allow_inf_nan=False,
        description="Baseline of the text, PDF user space (origin bottom-left)",
    )
    text: str = Field(min_length=1)
    font_size: float = Field(default=10.0, gt=0, le=72, allow_inf_nan=False)


PdfEditOp = Annotated[SetFieldOp | AddTextOp, Field(discriminator="op")]


class PdfEditInput(BaseModel):
    user_id: str = ""
    doc_id: str = Field(min_length=1)
    ops: list[PdfEditOp] = Field(
        min_length=1,
        description="Batch of edits, applied atomically: if any op is invalid "
        "the whole batch is rejected and the document is unchanged",
    )
    render_pages: list[int] = Field(
        default_factory=list,
        description=(
            "1-based page numbers to rasterize into the response as PNG images "
            "to verify placement (capped by pdf_forms.render_max_pages)"
        ),
    )
    acknowledge_signature_invalidation: bool = Field(
        default=False,
        description=(
            "Must be true to edit a document that already carries digital "
            "signatures (pdf_open's existing_signatures) - editing "
            "cryptographically invalidates them. Set only after the user has "
            "explicitly confirmed they accept that."
        ),
    )


class PdfEditResult(BaseModel):
    doc_id: str
    status: PdfDocStatus
    applied_ops: int
    fields: list[PdfFormField] = Field(
        default_factory=list, description="Field inventory after the edit"
    )
    page_images: list[PdfPageImage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# pdf_request_signature
# ---------------------------------------------------------------------------


class SignaturePlacement(BaseModel):
    """Where the visible signature stamp goes.

    Either ``field_name`` (an AcroForm signature field from pdf_open's
    fields[]) or an explicit ``page``/``x``/``y`` anchor for flat PDFs -
    exactly one of the two forms.
    """

    field_name: str | None = Field(
        default=None,
        description="Name of a signature-type AcroForm field to sign into",
    )
    page: int | None = Field(
        default=None, ge=1, description="1-based page for flat-PDF placement"
    )
    x: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="Left edge of the stamp, PDF user space",
    )
    y: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="Baseline of the stamp, PDF user space (origin bottom-left)",
    )

    def is_field_based(self) -> bool:
        return self.field_name is not None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> SignaturePlacement:
        coords = (self.page, self.x, self.y)
        if self.field_name is not None:
            if any(c is not None for c in coords):
                raise ValueError(
                    "placement takes either field_name or {page, x, y}, not both"
                )
        elif not all(c is not None for c in coords):
            raise ValueError(
                "placement needs either field_name (an AcroForm signature "
                "field) or all of page, x, y (flat-PDF anchor, PDF user space)"
            )
        return self


class PdfRequestSignatureInput(BaseModel):
    user_id: str = ""
    doc_id: str = Field(min_length=1)
    placement: SignaturePlacement = Field(
        description=(
            "Where the signature stamp goes: a signature field's name, or "
            "{page, x, y} in PDF user space for flat PDFs"
        )
    )


class PdfRequestSignatureResult(BaseModel):
    doc_id: str
    status: Literal[
        "awaiting_user_signature",
        "signed",
        "signing_declined",
        "signing_unavailable",
    ]
    guidance: str = Field(
        description="What happens next; relay this to the user verbatim"
    )


# ---------------------------------------------------------------------------
# App-only tool payloads (pdf_signer.* - iframe only, never LLM-visible)
# ---------------------------------------------------------------------------


class PdfSignerDocument(BaseModel):
    """Full document payload for the signing iframe's pdf.js viewer.

    Contains the raw PDF bytes (base64) - returned ONLY by the app-only
    ``pdf_signer.get_document`` tool, never by an LLM-visible one (FR-10).
    """

    doc_id: str
    filename: str
    status: PdfDocStatus
    page_count: int
    # Placement resolved server-side to the exact rectangle the stamp will
    # occupy, so the iframe just scales it - stamp geometry has one owner
    # (services/pdf_signing.py).
    stamp_page: int | None = None
    stamp_rect: list[float] | None = Field(
        default=None,
        description="Stamp footprint [x0, y0, x1, y1] in PDF user space",
    )
    data_base64: str


class PdfSignResult(BaseModel):
    """Outcome of the signing ceremony, rendered by the signing app."""

    doc_id: str
    status: Literal["signed", "declined"]
    signed_by: str | None = None
    signed_at_utc: str | None = None
    message: str


class PdfSignerCancelResult(BaseModel):
    """User cancelled from the signing UI; the document is editable again."""

    doc_id: str
    status: Literal["open"]


# ---------------------------------------------------------------------------
# pdf_export
# ---------------------------------------------------------------------------


class PdfExportInput(BaseModel):
    user_id: str = ""
    doc_id: str = Field(min_length=1)
    destination: PdfDestination = Field(
        description="Where to deliver the PDF (attached server-side; the "
        "bytes never enter this conversation)"
    )


class PdfExportedAttachment(BaseModel):
    """Metadata of one attachment on the destination after export (no bytes)."""

    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    attachment_id: str | None = None


class PdfDelivery(BaseModel):
    """What a destination adapter reports back after delivering the PDF.

    ``ref_id`` is the destination-native handle (the Gmail draft id for
    ``gmail_draft`` destinations); destination-neutral by design so the port
    contract stays Gmail-free.
    """

    ref_id: str | None = None
    attachments: list[PdfExportedAttachment] = Field(default_factory=list)


class PdfExportResult(BaseModel):
    doc_id: str
    status: Literal["open", "signed"]
    filename: str = Field(description="Filename the PDF was delivered under")
    destination_type: str
    draft_id: str | None = Field(
        default=None, description="Gmail draft id (gmail_draft destinations)"
    )
    attachments: list[PdfExportedAttachment] = Field(
        default_factory=list,
        description="The destination draft's resulting attachment list",
    )
