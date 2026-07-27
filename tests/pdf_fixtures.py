"""In-memory PDF fixtures for the pdf_* tool tests.

Built with pypdf's generic object layer so no binary files are committed:
``make_acroform_pdf`` produces a one-page form with a text field, checkbox,
choice field, and signature field; ``make_flat_pdf`` produces a field-less
page with positioned text lines for overlay-anchoring tests.
"""

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from services.pdf_overlay import type1_font

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

_FF_REQUIRED = 1 << 1
_FF_COMBO = 1 << 17


def _helvetica(writer: PdfWriter):
    return type1_font(writer, "/Helvetica")


def _empty_form_xobject(writer: PdfWriter, width: float, height: float):
    xobj = DecodedStreamObject()
    xobj.set_data(b"")
    xobj[NameObject("/Type")] = NameObject("/XObject")
    xobj[NameObject("/Subtype")] = NameObject("/Form")
    xobj[NameObject("/BBox")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]
    )
    return writer._add_object(xobj)


def _widget(
    writer: PdfWriter,
    page,
    *,
    field_type: str,
    name: str,
    rect: tuple[float, float, float, float],
    flags: int = 0,
    extra: dict | None = None,
):
    annot = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject(field_type),
            NameObject("/T"): TextStringObject(name),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
            NameObject("/P"): page.indirect_reference,
            NameObject("/Ff"): NumberObject(flags),
        }
    )
    for key, value in (extra or {}).items():
        annot[NameObject(key)] = value
    return writer._add_object(annot)


def make_acroform_pdf(*, xfa: bool = False, signed_sig_field: bool = False) -> bytes:
    """One page, four fields: text (required), checkbox, combo choice, signature.

    ``xfa=True`` adds an ``/XFA`` packet to the AcroForm (LiveCycle hybrid);
    ``signed_sig_field=True`` populates the signature field's ``/V`` with a
    minimal signature dictionary (signer name only - detection tests don't
    need real crypto).
    """
    writer = PdfWriter()
    page = writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    checkbox_states = DictionaryObject(
        {
            NameObject("/Yes"): _empty_form_xobject(writer, 12, 12),
            NameObject("/Off"): _empty_form_xobject(writer, 12, 12),
        }
    )
    field_refs = [
        _widget(
            writer,
            page,
            field_type="/Tx",
            name="full_name",
            rect=(100, 600, 300, 620),
            flags=_FF_REQUIRED,
        ),
        _widget(
            writer,
            page,
            field_type="/Btn",
            name="agree_terms",
            rect=(100, 560, 112, 572),
            extra={
                "/V": NameObject("/Off"),
                "/AS": NameObject("/Off"),
                "/AP": DictionaryObject({NameObject("/N"): checkbox_states}),
            },
        ),
        _widget(
            writer,
            page,
            field_type="/Ch",
            name="state",
            rect=(100, 520, 250, 540),
            flags=_FF_COMBO,
            extra={
                "/Opt": ArrayObject([TextStringObject("CA"), TextStringObject("NY")])
            },
        ),
        _widget(
            writer,
            page,
            field_type="/Sig",
            name="signature",
            rect=(100, 120, 300, 160),
            extra=(
                {
                    "/V": DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Sig"),
                            NameObject("/Name"): TextStringObject("Alice Example"),
                        }
                    )
                }
                if signed_sig_field
                else None
            ),
        ),
    ]
    page[NameObject("/Annots")] = ArrayObject(field_refs)

    acroform = DictionaryObject(
        {
            NameObject("/Fields"): ArrayObject(field_refs),
            NameObject("/NeedAppearances"): BooleanObject(True),
            NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
            NameObject("/DR"): DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/Helv"): _helvetica(writer)}
                    )
                }
            ),
        }
    )
    if xfa:
        packet = DecodedStreamObject()
        packet.set_data(b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'/>")
        acroform[NameObject("/XFA")] = writer._add_object(packet)
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(acroform)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_encrypted_pdf(user_password: str = "secret") -> bytes:
    """A password-protected flat page (user password required to open)."""
    writer = PdfWriter()
    writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    writer.encrypt(user_password=user_password, algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_flat_pdf(page_count: int = 1) -> bytes:
    """Field-less page(s) with positioned text lines (a minimal 'flat' NDA)."""
    writer = PdfWriter()
    for _ in range(page_count):
        page = writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        font_ref = _helvetica(writer)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        content = DecodedStreamObject()
        content.set_data(
            b"BT /F1 14 Tf 72 700 Td (NON-DISCLOSURE AGREEMENT) Tj ET\n"
            b"BT /F1 10 Tf 72 650 Td (Name:) Tj ET\n"
            b"BT /F1 10 Tf 72 600 Td (Date:) Tj ET\n"
            b"BT /F1 10 Tf 72 550 Td (Signature:) Tj ET\n"
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
