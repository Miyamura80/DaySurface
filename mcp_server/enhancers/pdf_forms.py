"""PDF tools enhancers: page images as ``ImageContent`` + the signing handoff.

``pdf_open`` / ``pdf_edit``: optional page rasterizations move from the
structured result into ``ImageContent`` blocks (same pattern as
``gmail_attachment.py``) so vision hosts render them; headless transports
(CLI/API) keep the base64 in the structured result.

``pdf_request_signature``: attaches the pdf_signer MCP App when the host can
render iframes. On hosts without apps but with elicitation, falls back to a
host-native typed-name prompt (US-008) - the elicitation dialog is rendered
by the host outside any iframe, so the model cannot answer it; the accepted
response runs the same server-side stamp+audit+seal path with
``channel: "elicitation"`` recorded. With neither capability the request is
rolled back and the model is told signing is unavailable here.

Part of the PDF core (isolation seam): no Gmail imports.
"""

import asyncio

from mcp.server.elicitation import AcceptedElicitation
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, Field

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.pdf_forms import (
    PdfEditInput,
    PdfEditResult,
    PdfOpenInput,
    PdfOpenResult,
    PdfRequestSignatureInput,
    PdfRequestSignatureResult,
)
from services.pdf_documents_repo import load_document
from services.pdf_signing import abort_signing, perform_signing

SIGNER_APP_URI = "ui://daysurface/pdf_signer"


def _move_page_images_to_content[T: PdfOpenResult | PdfEditResult](
    tool: EnhancedTool, result: T
) -> T:
    """Attach page PNGs as ImageContent and drop them from the structured result."""
    if not result.page_images:
        return result
    for image in result.page_images:
        tool.send_image(data=image.data_base64, mime_type=image.mime_type)
    return result.model_copy(update={"page_images": []})


@enhance("pdf_open", fallback="headless")
async def pdf_open_enhanced(
    tool: EnhancedTool[PdfOpenInput, PdfOpenResult],
) -> PdfOpenResult:
    # Blocking work (pypdf parse + optional pypdfium2 render) off the loop.
    result = await asyncio.to_thread(tool.call)
    return _move_page_images_to_content(tool, result)


@enhance("pdf_edit", fallback="headless")
async def pdf_edit_enhanced(
    tool: EnhancedTool[PdfEditInput, PdfEditResult],
) -> PdfEditResult:
    # Blocking work (pypdf rewrite + optional pypdfium2 render) off the loop.
    result = await asyncio.to_thread(tool.call)
    return _move_page_images_to_content(tool, result)


class _ElicitedSignature(BaseModel):
    """Flat schema for the no-app signing fallback (host-native dialog)."""

    full_name: str = Field(
        description="Your full legal name, typed by you - this is your signature"
    )
    consent: bool = Field(
        description=("I agree that typing my name constitutes my electronic signature")
    )


@enhance("pdf_request_signature", fallback="headless", app_uri=SIGNER_APP_URI)
async def pdf_request_signature_enhanced(
    tool: EnhancedTool[PdfRequestSignatureInput, PdfRequestSignatureResult],
) -> PdfRequestSignatureResult:
    result = await asyncio.to_thread(tool.call)
    if result.status != "awaiting_user_signature":
        return result
    if tool.can_show_app:
        tool.send_app(SIGNER_APP_URI)
        return result
    doc_id = tool.input.doc_id
    user_id = tool.input.user_id
    if tool.can_elicit:
        doc = load_document(doc_id, user_id)
        try:
            elicited = await tool.elicit(
                message=(
                    f"'{doc.filename}' is ready to sign. Type your full legal "
                    "name to sign it - this applies your electronic signature "
                    "and seals the document."
                ),
                schema=_ElicitedSignature,
            )
        except McpError:
            # Declared-but-broken host (see pdf_signer.sign for the Goose
            # variant). Letting this propagate would trip the enhancer's
            # headless fallback, whose idempotent awaiting branch returns
            # "awaiting" forever - leaving the document locked with no
            # signing surface. Roll back instead.
            abort_signing(
                doc_id=doc_id,
                user_id=user_id,
                reason="elicitation_failed",
                channel="elicitation",
                back_to_open=True,
            )
            return PdfRequestSignatureResult(
                doc_id=doc_id,
                status="signing_unavailable",
                guidance=(
                    "This client advertised elicitation but failed to show "
                    "the signing prompt, so signing is unavailable here. The "
                    "document is editable again; the filled PDF can be "
                    "exported with pdf_export and signed elsewhere."
                ),
            )
        # The result union isn't parameterized by the schema, so re-validate
        # the accepted payload into the schema model at this boundary.
        signature = (
            _ElicitedSignature.model_validate(elicited.data)
            if isinstance(elicited, AcceptedElicitation)
            else None
        )
        if signature is not None and signature.consent and signature.full_name.strip():
            # Blocking work (pypdf clone + RSA seal) off the event loop.
            signed_doc, audit = await asyncio.to_thread(
                perform_signing,
                doc_id=doc_id,
                user_id=user_id,
                typed_name=signature.full_name,
                consent=signature.consent,
                channel="elicitation",
                confirmed_via_elicitation=True,
            )
            return PdfRequestSignatureResult(
                doc_id=signed_doc.doc_id,
                status="signed",
                guidance=(
                    f"Signed by {audit['typed_name']} on "
                    f"{audit['signed_at_utc']} and sealed. The document is "
                    "now immutable; use pdf_export to deliver it."
                ),
            )
        # Declined/cancelled (or consent withheld): back to editable.
        abort_signing(
            doc_id=doc_id,
            user_id=user_id,
            reason="elicitation_declined",
            channel="elicitation",
            back_to_open=True,
        )
        return PdfRequestSignatureResult(
            doc_id=doc_id,
            status="signing_declined",
            guidance=(
                "The user declined to sign. The document is editable again; "
                "ask them what they'd like to change."
            ),
        )
    # Neither apps nor elicitation: don't leave the document locked.
    abort_signing(
        doc_id=doc_id,
        user_id=user_id,
        reason="no_signing_channel_available",
        channel="none",
        back_to_open=True,
    )
    return PdfRequestSignatureResult(
        doc_id=doc_id,
        status="signing_unavailable",
        guidance=(
            "This client supports neither MCP Apps nor elicitation, so there "
            "is no way for the user to sign here. Form filling still works: "
            "the edits are saved and the filled PDF can be exported with "
            "pdf_export, then signed in a client that supports signing."
        ),
    )
