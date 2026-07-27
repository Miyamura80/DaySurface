"""Tests for the gmail_get_attachment enhancer (image-attachment viewability).

The enhancer sniffs the fetched bytes and, for images, appends an
``ImageContent`` block so vision-capable hosts render the actual image while
non-image files (and non-vision hosts) still get ``data_base64`` in the
structured result. The pure service is faked here - the fetch/size-cap logic
is covered in ``tests/test_gmail_services.py``.
"""

import asyncio
import base64
from unittest.mock import MagicMock

from mcp_server.enhancers.base import EnhancedTool
from mcp_server.enhancers.gmail_attachment import (
    _sniff_image_mime,
    gmail_get_attachment_enhanced,
)
from models.gmail import GmailAttachmentData, GmailGetAttachmentInput
from tests.test_template import TestTemplate

# Real magic-number-prefixed byte samples for each format the sniffer knows.
_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
_GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00"
_BMP = b"BM\x8a\x00\x00\x00\x00\x00\x00\x00"
_TIFF_LE = b"II*\x00\x08\x00\x00\x00"
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestSniffImageMime(TestTemplate):
    """_sniff_image_mime maps attachment bytes to an image mime type by magic number."""

    def test_detects_png(self):
        assert _sniff_image_mime(_b64(_PNG)) == "image/png"

    def test_detects_jpeg(self):
        assert _sniff_image_mime(_b64(_JPEG)) == "image/jpeg"

    def test_detects_gif(self):
        assert _sniff_image_mime(_b64(_GIF)) == "image/gif"

    def test_detects_bmp(self):
        assert _sniff_image_mime(_b64(_BMP)) == "image/bmp"

    def test_detects_tiff(self):
        assert _sniff_image_mime(_b64(_TIFF_LE)) == "image/tiff"

    def test_detects_webp(self):
        assert _sniff_image_mime(_b64(_WEBP)) == "image/webp"

    def test_non_image_returns_none(self):
        # A PDF header - reachable/decodable, but not an image.
        assert _sniff_image_mime(_b64(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3")) is None

    def test_empty_returns_none(self):
        assert _sniff_image_mime("") is None

    def test_invalid_base64_returns_none(self):
        # Not decodable as base64 - must degrade to None, not raise.
        assert _sniff_image_mime("!!!!not base64!!!!") is None


class TestGmailAttachmentEnhancer(TestTemplate):
    """The gmail_get_attachment enhancer attaches an ImageContent block for images only."""

    def _run(self, raw: bytes) -> tuple[EnhancedTool, GmailAttachmentData]:
        def fake_service(input: GmailGetAttachmentInput) -> GmailAttachmentData:
            return GmailAttachmentData(
                message_id=input.message_id,
                attachment_id=input.attachment_id,
                size=len(raw),
                data_base64=_b64(raw),
            )

        tool: EnhancedTool[GmailGetAttachmentInput, GmailAttachmentData] = EnhancedTool(
            ctx=MagicMock(),
            input=GmailGetAttachmentInput(message_id="m-1", attachment_id="att-1"),
            service_fn=fake_service,
        )
        result = asyncio.run(gmail_get_attachment_enhanced(tool))
        return tool, result

    def test_image_attachment_gets_image_content_block(self):
        tool, _ = self._run(_PNG)
        assert len(tool.extra_content) == 1
        block = tool.extra_content[0]
        assert block.type == "image"
        assert block.mimeType == "image/png"
        # The image block carries the same base64 bytes as the structured result.
        assert block.data == _b64(_PNG)

    def test_non_image_attachment_has_no_extra_content(self):
        tool, _ = self._run(b"%PDF-1.7\nnot an image at all")
        assert tool.extra_content == []

    def test_enhancer_returns_service_result_unchanged(self):
        _, result = self._run(_JPEG)
        # Structured result is the untouched service output (bytes reach non-vision
        # hosts via structuredContent regardless of the image block).
        assert result.message_id == "m-1"
        assert result.data_base64 == _b64(_JPEG)
