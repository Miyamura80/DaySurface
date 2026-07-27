"""pypdf edit engine: apply a validated batch of ops to PDF bytes.

Part of the PDF core (isolation seam): no Gmail imports, no session
awareness - pure ``(bytes, ops) -> bytes``.

Atomicity: every op is validated against the document before anything is
applied; any invalid op fails the whole batch with a per-op error report
(:class:`PdfEditBatchError`) and the input bytes are never touched.

``add_text`` stamps the text into the page's content stream (merged overlay
page) rather than as a FreeText annotation: content-stream text renders
identically in every viewer/rasterizer and survives flattening, whereas
annotation appearances are viewer-dependent.
"""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter

from models.pdf_forms import AddTextOp, PdfEditOp, PdfFormField, SetFieldOp
from services.pdf_inspect import _qualified_name, inspect_pdf
from services.pdf_overlay import (
    build_text_overlay_page,
    escape_pdf_text,
    unencodable_pdf_text,
)


class PdfEditBatchError(Exception):
    """The batch was rejected; ``errors`` lists every invalid op."""

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        lines = "; ".join(f"ops[{e['index']}]: {e['error']}" for e in errors)
        super().__init__(
            f"pdf_edit batch rejected, document unchanged. Invalid ops: {lines}"
        )


def _normalize_state(value: str) -> str:
    return value if value.startswith("/") else f"/{value}"


def _validate_set_field(
    op: SetFieldOp, fields_by_name: dict[str, PdfFormField]
) -> str | None:
    field = fields_by_name.get(op.name)
    if field is None:
        known = ", ".join(sorted(fields_by_name)) or "none"
        return f"unknown field {op.name!r} (document fields: {known})"
    if field.read_only:
        return f"field {op.name!r} is read-only"
    if field.field_type == "signature":
        return (
            f"field {op.name!r} is a signature field; signatures are applied "
            "only by the user via pdf_request_signature, never by set_field"
        )
    if field.field_type not in ("text", "checkbox", "radio", "choice"):
        # Pushbuttons and unrecognized widget types hold no fillable value.
        return (
            f"field {op.name!r} is a {field.field_type} widget and cannot "
            "hold a value; only text, checkbox, radio and choice fields are "
            "editable"
        )
    if field.field_type in ("checkbox", "radio", "choice"):
        options = field.options or []
        if field.field_type == "choice":
            if op.value not in options:
                return f"invalid value {op.value!r} for {op.name!r}; options: {options}"
        elif _normalize_state(op.value) not in options:
            return (
                f"invalid state {op.value!r} for {op.name!r}; "
                f"appearance states: {options}"
            )
    return None


def _validate_ops(
    ops: list[PdfEditOp],
    fields_by_name: dict[str, PdfFormField],
    page_count: int,
) -> list[dict]:
    errors: list[dict] = []
    for index, op in enumerate(ops):
        if isinstance(op, SetFieldOp):
            problem = _validate_set_field(op, fields_by_name)
        elif op.page > page_count:
            problem = f"page {op.page} out of range (document has {page_count} pages)"
        elif bad := unencodable_pdf_text(op.text):
            # Overlays draw with standard-14 Helvetica (Latin-1 only);
            # letting these through would silently render '?' on the page.
            problem = (
                f"text contains characters the overlay font cannot render: "
                f"{bad!r}. add_text uses a Latin-1 (standard-14) font; "
                "transliterate the text or restrict it to Latin script. "
                "(AcroForm set_field values are not affected.)"
            )
        else:
            problem = None
        if problem is not None:
            errors.append({"index": index, "op": op.op, "error": problem})
    return errors


def _overlay_page(width: float, height: float, items: list[AddTextOp]):
    """Build a single page whose content stream draws the given text items."""
    chunks = [
        b"BT /PdfEditF1 %.2f Tf %.2f %.2f Td (%s) Tj ET\n"
        % (item.font_size, item.x, item.y, escape_pdf_text(item.text))
        for item in items
    ]
    return build_text_overlay_page(
        width, height, {"/PdfEditF1": "/Helvetica"}, b"".join(chunks)
    )


def _apply_field_values(
    writer: PdfWriter,
    ops: list[PdfEditOp],
    fields_by_name: dict[str, PdfFormField],
) -> None:
    """Write set_field values onto EVERY page carrying a widget for the field.

    A field can repeat its widget across pages (e.g. "sign each page"
    initials), and with appearance regeneration off, pages the update never
    visits would keep their stale appearance.
    """
    values_by_name: dict[str, str] = {}
    for op in ops:
        if not isinstance(op, SetFieldOp):
            continue
        field = fields_by_name[op.name]
        value = op.value
        if field.field_type in ("checkbox", "radio"):
            value = _normalize_state(value)
        values_by_name[op.name] = value
    if not values_by_name:
        return
    for page in writer.pages:
        on_page = {
            name
            for ref in page.get("/Annots") or []
            if (name := _qualified_name(ref.get_object())) in values_by_name
        }
        if on_page:
            writer.update_page_form_field_values(
                page,
                {n: values_by_name[n] for n in on_page},
                auto_regenerate=False,
            )


def apply_ops(data: bytes, ops: list[PdfEditOp]) -> bytes:
    """Validate the whole batch, then apply it; returns the new bytes."""
    inspection = inspect_pdf(data, include_text_layout=False)
    fields_by_name = {f.name: f for f in inspection.fields}
    errors = _validate_ops(ops, fields_by_name, inspection.page_count)
    if errors:
        raise PdfEditBatchError(errors)

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)

    _apply_field_values(writer, ops, fields_by_name)

    # Text overlays, grouped per page into one merged content stream each.
    overlays: dict[int, list[AddTextOp]] = {}
    for op in ops:
        if isinstance(op, AddTextOp):
            overlays.setdefault(op.page, []).append(op)
    for page_no, items in overlays.items():
        target = writer.pages[page_no - 1]
        overlay = _overlay_page(
            float(target.mediabox.width), float(target.mediabox.height), items
        )
        target.merge_page(overlay)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
