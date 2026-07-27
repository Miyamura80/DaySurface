"""pypdfium2 page rasterization for visual verification of PDF edits.

Part of the PDF core (isolation seam): no Gmail imports. Renders requested
pages to PNG so a vision-capable host can check field/overlay placement
without the PDF bytes themselves entering the model's context.
"""

from __future__ import annotations

import base64
import io

import pypdfium2 as pdfium

from common import global_config
from models.pdf_forms import PdfPageImage


class PdfRenderRequestError(Exception):
    """Raised for out-of-range pages or too many pages in one render request."""


# A hostile PDF can declare an arbitrarily large MediaBox; even within the
# page-count and DPI limits that would balloon the bitmap allocation. 20M
# pixels (~80MB RGBA) comfortably covers A0 at the default 110 DPI while
# bounding worst-case memory per page.
_MAX_RENDER_PIXELS_PER_PAGE = 20_000_000


def render_pages(data: bytes, pages: list[int], page_count: int) -> list[PdfPageImage]:
    """Rasterize the given 1-based pages to PNG at the configured DPI."""
    if not pages:
        return []
    requested = sorted(set(pages))
    bad = [p for p in requested if p < 1 or p > page_count]
    if bad:
        raise PdfRenderRequestError(
            f"render_pages out of range: {bad} (document has {page_count} pages)."
        )
    max_pages = global_config.pdf_forms.render_max_pages
    if len(requested) > max_pages:
        raise PdfRenderRequestError(
            f"render_pages asked for {len(requested)} pages; the limit is "
            f"{max_pages} per call (pdf_forms.render_max_pages). Request fewer "
            "pages, over several calls if needed."
        )
    scale = global_config.pdf_forms.render_dpi / 72.0
    pdf = pdfium.PdfDocument(data)
    try:
        images: list[PdfPageImage] = []
        for page_no in requested:
            width, height = pdf[page_no - 1].get_size()
            pixels = int(width * scale) * int(height * scale)
            if pixels > _MAX_RENDER_PIXELS_PER_PAGE:
                raise PdfRenderRequestError(
                    f"page {page_no} would rasterize to {pixels} pixels, over "
                    f"the {_MAX_RENDER_PIXELS_PER_PAGE} per-page limit - the "
                    "page's MediaBox is unusually large. Skip render_pages "
                    "for this document."
                )
            bitmap = pdf[page_no - 1].render(scale=scale)
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="PNG")
            images.append(
                PdfPageImage(
                    page=page_no,
                    data_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
                )
            )
        return images
    finally:
        pdf.close()
