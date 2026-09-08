from __future__ import annotations

import io
import zipfile

import pymupdf
from PIL import Image

from app.config import settings
from app.schemas import JobConfig
from app.utils.image_utils import _open_safe_image


def _validate_pdf_document(doc) -> int:
    """Enforce the configured page-count and page-size limits on an open document."""
    if doc.page_count > settings.MAX_PDF_PAGES:
        raise ValueError(
            f"PDF has more pages than the limit of {settings.MAX_PDF_PAGES}"
        )
    for page in doc:
        if max(page.rect.width, page.rect.height) > settings.MAX_PAGE_SIZE_POINTS:
            raise ValueError(
                f"PDF page is larger than the limit of {settings.MAX_PAGE_SIZE_POINTS} points"
            )
    return doc.page_count


def _clamp_dpi(dpi: int) -> int:
    if dpi <= 0:
        return settings.MAX_RENDER_DPI
    return min(dpi, settings.MAX_RENDER_DPI)


def get_target_pages(total_pages: int, config: JobConfig) -> list[int]:
    mode = config.page_selection.mode

    if mode == "all":
        return list(range(total_pages))
    elif mode == "first_only":
        return [0]
    elif mode == "last_only":
        return [total_pages - 1] if total_pages > 0 else []
    elif mode == "first_n":
        n = config.page_selection.first_count
        return list(range(min(n, total_pages)))
    elif mode == "last_n":
        n = config.page_selection.last_count
        start = max(0, total_pages - n)
        return list(range(start, total_pages))
    elif mode == "first_n_and_last_m":
        n = config.page_selection.first_count
        m = config.page_selection.last_count
        first = list(range(min(n, total_pages)))
        last_start = max(0, total_pages - m)
        last = list(range(last_start, total_pages))
        return sorted(set(first + last))
    elif mode == "specific":
        return [p for p in config.page_selection.specific_pages if 0 <= p < total_pages]
    else:
        return list(range(total_pages))


def _position_rect(x: float, y: float, w: float, h: float, page) -> pymupdf.Rect:
    """Build the rect for a position in the page's unrotated mediabox space.

    The frontend positions the logo in the rotation-aware (page.rect) coordinate
    space — i.e. the same space used by the preview. The logo keeps its width
    along that space's X axis and its height along its Y axis. To render upright
    and in the exact spot, we convert the rect into the page's *unrotated*
    mediabox coordinate system (the one used by insert_image) via the page's
    derotation matrix.
    """
    base_rect = pymupdf.Rect(
        x,
        y,
        x + w,
        y + h,
    )
    rotation = page.rotation % 360
    if rotation in (90, 180, 270):
        derot = page.derotation_matrix
        a, b, c, d, e, f = derot.a, derot.b, derot.c, derot.d, derot.e, derot.f
        corners = (base_rect.tl, base_rect.tr, base_rect.bl, base_rect.br)
        points = [
            pymupdf.Point(a * p.x + c * p.y + e, b * p.x + d * p.y + f)
            for p in corners
        ]
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
    return base_rect


def stamp_logo_on_pdf(
    pdf_bytes: bytes,
    logo_bytes: bytes,
    config: JobConfig,
    preserve_aspect: bool = False,
) -> bytes:
    if len(pdf_bytes) > settings.MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds the size limit of {settings.MAX_PDF_BYTES} bytes")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    _validate_pdf_document(doc)

    # Decode the logo as RGBA and re-encode it as PNG so the alpha channel is
    # preserved. Rasterizing it earlier with logo_page.get_pixmap() flattened
    # the transparency onto a white background, producing a "white box" around
    # transparent logos. Inserting the PNG stream directly lets PyMuPDF keep a
    # proper soft mask so the transparency survives into the stamped PDF.
    img = _open_safe_image(logo_bytes).convert("RGBA")
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    # When preserve_aspect is True (multiple logos stamped onto a single PDF) a
    # single position config is shared by logos of different aspect ratios, so
    # only x/y and width are taken from the config. The height is derived from
    # each logo's own aspect ratio, keeping every logo undistorted.
    logo_aspect = (img.width / img.height) if img.height else 1.0

    def effective_size(pos) -> tuple[float, float]:
        w = pos.width
        if preserve_aspect:
            return w, (w / logo_aspect)
        return w, pos.height

    target_pages = get_target_pages(doc.page_count, config)
    # first_rest only applies when stamping all pages; for any other selection
    # the logo uses the single `position` everywhere.
    is_first_rest = (
        config.position_mode == "first_rest"
        and config.page_selection.mode == "all"
        and config.position_rest is not None
    )
    xref = 0

    for page_num in target_pages:
        page = doc[page_num]
        # In "first_rest" mode the first page uses `position` and every other
        # page uses `position_rest`.
        if is_first_rest and page_num > 0:
            pos = config.position_rest
        else:
            pos = config.position
        w, h = effective_size(pos)
        rect = _position_rect(pos.x, pos.y, w, h, page)
        xref = page.insert_image(
            rect,
            stream=png_bytes,
            xref=xref,
            keep_proportion=preserve_aspect,
            overlay=True,
        )

    output = io.BytesIO()
    doc.save(output)
    doc.close()
    return output.getvalue()


def render_page_preview(pdf_bytes: bytes, page_number: int, dpi: int = 96) -> tuple[bytes, float, float]:
    if len(pdf_bytes) > settings.MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds the size limit of {settings.MAX_PDF_BYTES} bytes")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    _validate_pdf_document(doc)
    page = doc[page_number]
    pix = page.get_pixmap(dpi=_clamp_dpi(dpi), alpha=False)
    png_bytes = pix.tobytes("png")
    width = page.rect.width
    height = page.rect.height
    doc.close()
    return png_bytes, width, height


def _encode_logo(logo_bytes: bytes) -> tuple[bytes, float]:
    """Re-encode a logo as PNG (RGBA) preserving transparency. Returns (png, aspect)."""
    img = _open_safe_image(logo_bytes).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    aspect = (img.width / img.height) if img.height else 1.0
    return buf.getvalue(), aspect


def render_stamped_pages_preview(
    pdf_bytes: bytes,
    logo_bytes: bytes | None,
    config: JobConfig,
    requested_pages: list[int],
    preserve_aspect: bool = False,
    dpi: int = 96,
) -> tuple[list[tuple[int, bytes, bool]], float, float, int]:
    """Render specific pages of a PDF with the logo applied where the config
    targets them, opening the PDF once and encoding the logo once for speed.

    Returns a list of (page_number, png_bytes, stamped) tuples plus the page
    dimensions and the total number of pages in the source PDF.
    """
    if len(pdf_bytes) > settings.MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds the size limit of {settings.MAX_PDF_BYTES} bytes")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    _validate_pdf_document(doc)
    dpi = _clamp_dpi(dpi)
    target_pages = set(get_target_pages(doc.page_count, config))

    if logo_bytes is not None:
        png_bytes, logo_aspect = _encode_logo(logo_bytes)
    else:
        png_bytes, logo_aspect = None, 1.0

    is_first_rest = (
        config.position_mode == "first_rest"
        and config.page_selection.mode == "all"
        and config.position_rest is not None
    )

    def effective_size(pos) -> tuple[float, float]:
        w = pos.width
        if preserve_aspect:
            return w, (w / logo_aspect)
        return w, pos.height

    results: list[tuple[int, bytes, bool]] = []
    xref = 0
    for page_num in requested_pages:
        if page_num < 0 or page_num >= doc.page_count:
            continue
        page = doc[page_num]
        stamped = page_num in target_pages and png_bytes is not None
        if stamped:
            if is_first_rest and page_num > 0:
                pos = config.position_rest
            else:
                pos = config.position
            w, h = effective_size(pos)
            rect = _position_rect(pos.x, pos.y, w, h, page)
            xref = page.insert_image(
                rect,
                stream=png_bytes,
                xref=xref,
                keep_proportion=preserve_aspect,
                overlay=True,
            )
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        results.append((page_num, pix.tobytes("png"), stamped))

    first_page = doc[0] if doc.page_count else None
    width = first_page.rect.width if first_page else 0
    height = first_page.rect.height if first_page else 0
    page_count = doc.page_count
    doc.close()
    return results, width, height, page_count


def create_zip_from_pdf_bytes(pdf_list: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in pdf_list:
            zf.writestr(name, data)
    return buf.getvalue()


def get_pdf_page_count(pdf_bytes: bytes) -> int:
    if len(pdf_bytes) > settings.MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds the size limit of {settings.MAX_PDF_BYTES} bytes")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    count = _validate_pdf_document(doc)
    doc.close()
    return count
