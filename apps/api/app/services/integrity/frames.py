"""Frame validation and normalisation (camera / screen still images).

Untrusted uploads are checked before any decoding: size limit, magic bytes
(JPEG, PNG, WebP), declared content type must match the sniffed type, and the
header dimensions are bounded before pixels are decoded (decompression-bomb
guard). The image is then re-encoded as a baseline JPEG no larger than
``MAX_FRAME_EDGE`` pixels on its longest side, which also strips EXIF and
other metadata (location, device identifiers).
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import BadRequest, PayloadTooLarge, UnsupportedMediaType, ValidationFailed

MAX_FRAME_EDGE = 640
MIN_FRAME_EDGE = 32
MAX_SOURCE_EDGE = 8192
MAX_SOURCE_PIXELS = 24_000_000
JPEG_QUALITY = 80
OUTPUT_CONTENT_TYPE = "image/jpeg"

ImageType = Literal["jpeg", "png", "webp"]
_CONTENT_TYPES: dict[str, ImageType] = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}
_PIL_FORMATS: dict[ImageType, str] = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


@dataclass(frozen=True, slots=True)
class PreparedFrame:
    data: bytes  # JPEG
    width: int
    height: int
    source_type: ImageType
    source_width: int
    source_height: int
    source_bytes: int
    content_type: str = OUTPUT_CONTENT_TYPE


def sniff_image_type(data: bytes) -> ImageType | None:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def validate_frame_bytes(data: bytes, content_type: str | None, *, max_bytes: int) -> ImageType:
    """Cheap checks (no decoding): size, magic bytes, declared type."""
    if not data:
        raise BadRequest("The frame is empty.", code="empty_frame")
    if len(data) > max_bytes:
        raise PayloadTooLarge(
            "The frame is too large.",
            code="frame_too_large",
            details={"max_bytes": max_bytes, "size_bytes": len(data)},
        )
    sniffed = sniff_image_type(data)
    if sniffed is None:
        raise UnsupportedMediaType("Frames must be JPEG, PNG or WebP images.", code="unsupported_frame_type")
    declared = normalize_content_type(content_type)
    if declared and declared not in ("application/octet-stream",):
        expected = _CONTENT_TYPES.get(declared)
        if expected is None:
            raise UnsupportedMediaType(
                "Frames must be JPEG, PNG or WebP images.",
                code="unsupported_frame_type",
                details={"content_type": declared},
            )
        if expected != sniffed:
            raise UnsupportedMediaType(
                "The frame content does not match its declared type.",
                code="frame_type_mismatch",
                details={"declared": declared, "detected": f"image/{sniffed}"},
            )
    return sniffed


def prepare_frame(
    data: bytes, content_type: str | None, *, max_bytes: int, max_edge: int = MAX_FRAME_EDGE
) -> PreparedFrame:
    """Validate, decode, bound and re-encode one frame (CPU-bound: run in a thread)."""
    return prepare_frame_image(data, content_type, max_bytes=max_bytes, max_edge=max_edge)[0]


def prepare_frame_image(
    data: bytes, content_type: str | None, *, max_bytes: int, max_edge: int = MAX_FRAME_EDGE
) -> tuple[PreparedFrame, Image.Image]:
    """:func:`prepare_frame` that also returns the decoded, bounded RGB image
    (for the face detector, so the frame is not decoded twice)."""
    sniffed = validate_frame_bytes(data, content_type, max_bytes=max_bytes)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image: Image.Image = Image.open(io.BytesIO(data))
            if image.format != _PIL_FORMATS[sniffed]:
                raise UnsupportedMediaType(
                    "The frame content does not match its type.", code="frame_type_mismatch"
                )
            width, height = image.size
            if width < MIN_FRAME_EDGE or height < MIN_FRAME_EDGE:
                raise ValidationFailed(
                    "The frame is too small.",
                    code="frame_too_small",
                    details={"width": width, "height": height},
                )
            if width > MAX_SOURCE_EDGE or height > MAX_SOURCE_EDGE or width * height > MAX_SOURCE_PIXELS:
                raise ValidationFailed(
                    "The frame dimensions are too large.",
                    code="frame_dimensions_too_large",
                    details={"width": width, "height": height},
                )
            image.draft("RGB", (max_edge, max_edge))  # JPEG: decode at reduced scale (fast)
            image.load()
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                background = Image.new("RGB", image.size, (0, 0, 0))
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            elif image.mode == "L":
                image = image.convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except (UnsupportedMediaType, ValidationFailed):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationFailed(
            "The frame dimensions are too large.", code="frame_dimensions_too_large"
        ) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationFailed("The frame could not be decoded.", code="frame_corrupt") from None
    prepared = PreparedFrame(
        data=out.getvalue(),
        width=image.width,
        height=image.height,
        source_type=sniffed,
        source_width=width,
        source_height=height,
        source_bytes=len(data),
    )
    return prepared, image
