"""Gmail attachment enhancer - makes image attachments model-viewable.

``gmail_get_attachment`` is a headless service that returns an attachment's raw
bytes as base64 in ``data_base64``. A vision-capable host cannot "see" an image
from that base64 string - it needs an MCP ``ImageContent`` block. This enhancer
detects image attachments (by magic-byte sniffing the fetched bytes) and, for
images, appends an ``ImageContent`` block so vision hosts render the actual
image into the model's context. Non-image files - and the base64 for images -
still travel in the structured result, so non-vision hosts are unaffected.

The MCP spec has no dedicated "client is vision-capable" capability (unlike
elicitation), so - like the ``config_show`` enhancer - the image block is
attached unconditionally for images; a non-vision host simply ignores the extra
content while still receiving ``data_base64`` in ``structuredContent``. The
service's size cap (``global_config.gmail.max_attachment_bytes``) is enforced
inside ``tool.call()``, so oversized images never reach this path.
"""

import base64
import binascii

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.gmail import GmailAttachmentData, GmailGetAttachmentInput

# (magic prefix, mime) for the image formats that actually show up as email
# attachments / inline images. Prefixes are unambiguous across these formats.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

# Enough base64 chars to cover every magic number below - WebP needs the most
# (12 bytes -> 16 chars); 24 leaves headroom and stays a whole base64 quantum.
_HEADER_B64_CHARS = 24


def _sniff_image_mime(data_base64: str) -> str | None:
    """Return an image mime type if the base64 bytes start with a known image
    magic number, else ``None``.

    Sniffing the fetched bytes (rather than trusting a caller-supplied type)
    keeps the decision self-contained and independent of what the model echoes
    back from the thread locator.
    """
    if not data_base64:
        return None
    # Decode only the header, not the (potentially multi-MB) whole payload. Trim
    # to a 4-char-aligned length so the slice is a valid standalone base64 chunk.
    aligned = len(data_base64) - len(data_base64) % 4
    prefix = data_base64[: min(aligned, _HEADER_B64_CHARS)]
    if not prefix:
        return None
    try:
        head = base64.b64decode(prefix)
    except (binascii.Error, ValueError):
        return None
    for magic, mime in _IMAGE_MAGIC:
        if head.startswith(magic):
            return mime
    # WebP: "RIFF" <4-byte size> "WEBP" - not a simple prefix match.
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


@enhance("gmail_get_attachment", fallback="headless")
async def gmail_get_attachment_enhanced(
    tool: EnhancedTool[GmailGetAttachmentInput, GmailAttachmentData],
) -> GmailAttachmentData:
    result = tool.call()
    mime = _sniff_image_mime(result.data_base64)
    if mime is not None:
        tool.send_image(data=result.data_base64, mime_type=mime)
    return result
