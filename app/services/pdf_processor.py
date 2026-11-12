from __future__ import annotations

import io
import zipfile

import pymupdf
from PIL import Image

from app.config import settings
from app.schemas import JobConfig, LogoPlacement, Position
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


def _axis_offset(w: float, h: float, axis: str) -> tuple[float, float]:
    """Vector from the top-left corner to the chosen anchor point."""
    if axis == "center":
        return w / 2, h / 2
    if axis == "top_right":
        return w, 0.0
    if axis == "bottom_left":
        return 0.0, h
    if axis == "bottom_right":
        return w, h
    # Default: top_left.
    return 0.0, 0.0


def _axis_adjusted_top_left(
    ref_x: float,
    ref_y: float,
    ref_w: float,
    ref_h: float,
    current_w: float,
    current_h: float,
    axis: str,
) -> tuple[float, float]:
    """Shift the current logo so its anchor aligns with the reference logo's anchor.

    The reference logo stays at (ref_x, ref_y) as top-left. The current logo is
    moved so that both share the same anchor point (center, corner, etc.).
    """
    if axis == "top_left" or not axis:
        return ref_x, ref_y
    ref_off_x, ref_off_y = _axis_offset(ref_w, ref_h, axis)
    cur_off_x, cur_off_y = _axis_offset(current_w, current_h, axis)
    return ref_x + ref_off_x - cur_off_x, ref_y + ref_off_y - cur_off_y


def _position_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    page,
) -> pymupdf.Rect:
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


def _logo_aspect(logo_bytes: bytes) -> float:
    img = _open_safe_image(logo_bytes).convert("RGBA")
    return (img.width / img.height) if img.height else 1.0


def _resolve_page_position(
    config: JobConfig,
    file_index: int,
    page_num: int,
) -> tuple[Position, str]:
    """Return the effective position and axis for a page of a specific file.

    Priority:
    1. Per-page override in file_page_positions[file_index][page_num].
    2. first_rest fallback (page 0 uses position, rest uses position_rest).
    3. Global position.
    """
    file_map = config.file_page_positions.get(str(file_index))
    if file_map and str(page_num) in file_map.page_positions:
        return file_map.page_positions[str(page_num)], config.logo_axis or "top_left"

    is_first_rest = (
        config.position_mode == "first_rest"
        and config.page_selection.mode == "all"
        and config.position_rest is not None
    )
    if is_first_rest and page_num > 0:
        axis = config.logo_axis_rest or config.logo_axis or "top_left"
        return config.position_rest, axis

    return config.position, config.logo_axis or "top_left"


def _resolve_page_placements(
    config: JobConfig,
    file_index: int,
    page_num: int,
) -> list[tuple[Position, str]]:
    """Resolve all duplicated logo placements for one output page."""
    placements = list(
        config.logo_placements_rest
        if config.position_mode == "first_rest"
        and page_num > 0
        and config.logo_placements_rest
        else config.logo_placements
    )
    if not placements:
        position, axis = _resolve_page_position(config, file_index, page_num)
        return [(position, axis)]

    file_map = config.file_page_positions.get(str(file_index))
    custom = file_map.page_positions.get(str(page_num)) if file_map else None
    is_rest = (
        config.position_mode == "first_rest"
        and page_num > 0
    )
    resolved: list[tuple[Position, str]] = []
    for placement in placements:
        position = placement.position_rest if is_rest and placement.position_rest else placement.position
        axis = placement.logo_axis_rest if is_rest else placement.logo_axis
        if custom and len(resolved) == 0:
            position = custom
            axis = config.logo_axis_rest if is_rest else config.logo_axis
        resolved.append((position, axis))
    return resolved


def stamp_logo_on_pdf(
    pdf_bytes: bytes,
    logo_bytes: bytes,
    config: JobConfig,
    preserve_aspect: bool = False,
    reference_logo_bytes: bytes | None = None,
    file_index: int = 0,
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
    xref = 0

    # Reference logo defines the anchor point shared by all logos in
    # multiple_logos mode. When absent, the current logo is used as its own
    # reference (no shifting).
    ref_aspect = (
        _logo_aspect(reference_logo_bytes)
        if reference_logo_bytes
        else logo_aspect
    )

    for page_num in target_pages:
        page = doc[page_num]
        for pos, axis in _resolve_page_placements(config, file_index, page_num):
            w, h = effective_size(pos)
            ref_h = w / ref_aspect
            x, y = _axis_adjusted_top_left(pos.x, pos.y, w, ref_h, w, h, axis)
            rect = _position_rect(x, y, w, h, page)
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
    reference_logo_bytes: bytes | None = None,
    file_index: int = 0,
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

    ref_aspect = (
        _logo_aspect(reference_logo_bytes)
        if reference_logo_bytes
        else logo_aspect
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
            for pos, axis in _resolve_page_placements(config, file_index, page_num):
                w, h = effective_size(pos)
                ref_h = w / ref_aspect if ref_aspect else h
                x, y = _axis_adjusted_top_left(pos.x, pos.y, w, ref_h, w, h, axis)
                rect = _position_rect(x, y, w, h, page)
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
