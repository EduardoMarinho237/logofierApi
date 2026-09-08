from base64 import b64decode
from io import BytesIO
from urllib.parse import unquote_to_bytes

from PIL import Image

from app.config import settings

Image.MAX_IMAGE_PIXELS = settings.MAX_IMAGE_PIXELS


def _open_safe_image(image_bytes: bytes) -> Image.Image:
    """Open an image applying the configured pixel/dimension limits.

    Pillow raises DecompressionBombError/DecompressionBombWarning beyond
    MAX_IMAGE_PIXELS (a decompression DoS vector); we also reject images
    whose canvas is wider/taller than the configured maximums.
    """
    img = Image.open(BytesIO(image_bytes))
    width, height = img.size
    if width > settings.MAX_IMAGE_WIDTH or height > settings.MAX_IMAGE_HEIGHT:
        raise ValueError(
            f"Image is too large ({width}x{height}); maximum is "
            f"{settings.MAX_IMAGE_WIDTH}x{settings.MAX_IMAGE_HEIGHT}"
        )
    return img


def blocking_svg_url_fetcher(url: str, timeout: float = 5.0) -> bytes:
    """SVG resource loader that allows only embedded data: URLs.

    Blocks http/https/file/ftp references that cairosvg would otherwise
    fetch (SSRF / local file read), keeping SVG rendering safe.
    """
    if not url.startswith("data:"):
        raise ValueError("External resources are not allowed inside SVG")
    header, _, payload = url.partition(",")
    if ";base64" in header.lower():
        return b64decode(payload)
    return unquote_to_bytes(payload)


def convert_to_png(image_bytes: bytes, filename: str) -> bytes:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "png":
        return image_bytes

    if ext == "svg":
        try:
            import cairosvg
        except ImportError:
            raise ValueError("SVG support requires cairosvg")
        return cairosvg.svg2png(
            bytestring=image_bytes,
            url_fetcher=blocking_svg_url_fetcher,
        )

    img = _open_safe_image(image_bytes)

    if img.mode == "RGBA" and ext in ("jpg", "jpeg"):
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background.convert("RGB")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    img = _open_safe_image(image_bytes)
    return img.size


def create_logo_thumbnail(image_bytes: bytes, size: int = 128, background: tuple = (255, 255, 255)) -> bytes:
    """Create a square thumbnail preserving aspect ratio on a solid background."""
    img = _open_safe_image(image_bytes)

    if img.mode in ("RGBA", "P"):
        canvas = Image.new("RGBA", img.size, (*background, 255))
        canvas.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = canvas.convert("RGB")
    else:
        img = img.convert("RGB")

    img.thumbnail((size, size), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (size, size), background)
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y))

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def create_avatar_thumbnail(image_bytes: bytes, size: int = 200, quality: int = 85) -> bytes:
    """Crop central quadrado + redimensiona + comprime para avatar."""
    img = _open_safe_image(image_bytes)

    # Converter para RGB descartando transparância
    if img.mode in ("RGBA", "P"):
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = background.convert("RGB")
    else:
        img = img.convert("RGB")

    width, height = img.size
    min_side = min(width, height)
    left = (width - min_side) // 2
    top = (height - min_side) // 2
    right = left + min_side
    bottom = top + min_side
    img = img.crop((left, top, right, bottom))
    img = img.resize((size, size), Image.Resampling.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
