"""Canonical pypdf text-overlay machinery, shared by edit and signing.

Part of the PDF core (isolation seam): no Gmail imports.

Both ``pdf_edit``'s add_text ops and the signature stamp draw text by merging
a synthesized overlay page into the target page's content stream -
content-stream text renders identically in every viewer/rasterizer and
survives flattening, unlike annotation appearances. This module is the ONLY
place that builds those overlays, and deliberately the only PDF-core module
touching pypdf's private writer API (``_add_object``): a pypdf upgrade that
breaks it breaks exactly one file.
"""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def unencodable_pdf_text(text: str) -> str:
    """Characters in ``text`` the overlay font cannot render, deduped.

    Overlays draw with standard-14 Type1 faces under ``/WinAnsiEncoding``
    (see :func:`type1_font`), so callers MUST reject text containing these
    characters up front: letting them through would silently stamp '?' (or a
    wrong glyph) into the document - unacceptable for a signature stamp on a
    legal document, and bad even for a plain form overlay. Rejected: anything
    outside Latin-1, plus the C0/C1 control ranges (0x00-0x1F, 0x7F-0x9F)
    where WinAnsi has no printable glyphs. Returns "" when fully renderable.
    """
    seen: dict[str, None] = {}
    for char in text:
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F or code > 0xFF:
            seen.setdefault(char)
    return "".join(seen)


def escape_pdf_text(text: str) -> bytes:
    """Escape a string for a PDF literal-string operand.

    Standard-14 fonts cover Latin-1; anything outside degrades to '?' - the
    last-resort belt. Callers validate with :func:`unencodable_pdf_text`
    first so this replacement never actually fires on user-visible text.
    """
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def type1_font(writer: PdfWriter, base_font: str):
    """Register a standard-14 Type1 font on the writer; returns the ref.

    ``/WinAnsiEncoding`` is declared explicitly: without it, viewers apply
    StandardEncoding, which maps the upper Latin-1 range to different glyphs
    (e.g. 0xC0 'À' renders as '¿') - silently corrupting accented names.
    """
    return writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(base_font),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )


def build_text_overlay_page(
    width: float,
    height: float,
    fonts: dict[str, str],
    content: bytes,
):
    """Build a single page drawing ``content`` with the given font resources.

    ``fonts`` maps resource names to standard-14 base fonts, e.g.
    ``{"/F1": "/Helvetica"}``; ``content`` is a raw content stream referencing
    those names. The returned page is merge_page()-ready.
    """
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject(resource): type1_font(writer, base)
                    for resource, base in fonts.items()
                }
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
