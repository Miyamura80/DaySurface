"""Tests for the Gmail<->PDF bridge adapters (isolation seam)."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from models.gmail import (
    GmailAttachmentData,
    GmailDraftAttachment,
    GmailDraftAttachmentsResult,
)
from models.pdf_forms import GmailAttachmentSource, GmailDraftDestination
from services import pdf_gmail_bridge as bridge
from services.pdf_ports import deliver_to_destination, resolve_source
from tests.test_template import TestTemplate

_PDF = b"%PDF-1.7 bridge-test"


def _fake_gmail_client(payload: dict):
    svc = MagicMock()
    svc.users().messages().get(userId="me", id="m1").execute.return_value = {
        "payload": payload
    }
    return svc


def _nested_payload(attachment_id: str, filename: str) -> dict:
    return {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {}},
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "filename": filename,
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": attachment_id},
                    }
                ],
            },
        ],
    }


class TestGmailAttachmentResolver(TestTemplate):
    def test_resolves_filename_and_bytes(self):
        source = GmailAttachmentSource(message_id="m1", attachment_id="a1")
        att = GmailAttachmentData(
            message_id="m1",
            attachment_id="a1",
            size=len(_PDF),
            data_base64=base64.b64encode(_PDF).decode("ascii"),
        )
        with (
            patch.object(
                bridge,
                "_get_gmail_client",
                return_value=_fake_gmail_client(_nested_payload("a1", "nda.pdf")),
            ),
            patch.object(bridge, "gmail_get_attachment", return_value=att),
        ):
            # Dispatch through the port registry to prove registration works.
            resolved = resolve_source("u1", source)
        assert resolved.filename == "nda.pdf"
        assert resolved.data == _PDF

    def test_unknown_attachment_raises(self):
        source = GmailAttachmentSource(message_id="m1", attachment_id="missing")
        with (
            patch.object(
                bridge,
                "_get_gmail_client",
                return_value=_fake_gmail_client(_nested_payload("a1", "nda.pdf")),
            ),
            pytest.raises(bridge.GmailAttachmentNotFoundError),
        ):
            resolve_source("u1", source)


class TestGmailDraftDestination(TestTemplate):
    def test_delivers_via_gmail_add_attachment(self):
        added = GmailDraftAttachmentsResult(
            draft_id="d1",
            attachments=[
                GmailDraftAttachment(
                    filename="nda-signed.pdf",
                    mime_type="application/pdf",
                    size=len(_PDF),
                    attachment_id="att-9",
                    message_id="m-9",
                )
            ],
        )
        with patch.object(
            bridge, "gmail_add_attachment", return_value=added
        ) as add_mock:
            result = deliver_to_destination(
                "u1", GmailDraftDestination(draft_id="d1"), "nda-signed.pdf", _PDF
            )
        sent = add_mock.call_args.args[0]
        assert sent.draft_id == "d1"
        assert sent.attachment.filename == "nda-signed.pdf"
        assert sent.attachment.mime_type == "application/pdf"
        assert base64.b64decode(sent.attachment.data_base64) == _PDF
        assert result.ref_id == "d1"
        # message_id (an internal Gmail detail) is not forwarded; no bytes either.
        assert len(result.attachments) == 1
        attachment = result.attachments[0]
        assert attachment.filename == "nda-signed.pdf"
        assert attachment.mime_type == "application/pdf"
        assert attachment.size == len(_PDF)
        assert attachment.attachment_id == "att-9"
