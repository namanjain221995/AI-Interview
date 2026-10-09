"""Frame validation: magic bytes, declared type, size, dimensions, decoding,
downsizing and metadata stripping."""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from app.core.errors import BadRequest, PayloadTooLarge, UnsupportedMediaType, ValidationFailed
from app.services.integrity.frames import (
    MAX_FRAME_EDGE,
    prepare_frame,
    sniff_image_type,
    validate_frame_bytes,
)

MAX = 2 * 1024 * 1024


def encode(fmt: str, size: tuple[int, int] = (1280, 720), mode: str = "RGB", **save_kwargs) -> bytes:
    image = Image.new(mode, size, (40, 90, 160) if mode == "RGB" else None)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("fmt", "content_type"), [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_valid_frames_are_downsized_to_640_jpeg(fmt: str, content_type: str) -> None:
    prepared = prepare_frame(encode(fmt), content_type, max_bytes=MAX)
    assert prepared.content_type == "image/jpeg"
    assert sniff_image_type(prepared.data) == "jpeg"
    assert max(prepared.width, prepared.height) == MAX_FRAME_EDGE
    assert (prepared.width, prepared.height) == (640, 360)
    assert (prepared.source_width, prepared.source_height) == (1280, 720)
    decoded = Image.open(io.BytesIO(prepared.data))
    assert decoded.size == (640, 360)


def test_small_frames_are_not_upscaled() -> None:
    prepared = prepare_frame(encode("JPEG", (320, 240)), "image/jpeg", max_bytes=MAX)
    assert (prepared.width, prepared.height) == (320, 240)


def test_exif_metadata_is_stripped_and_orientation_applied() -> None:
    image = Image.new("RGB", (400, 200), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees
    exif[0x010F] = "SecretCam"  # Make
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    prepared = prepare_frame(buffer.getvalue(), "image/jpeg", max_bytes=MAX)
    out = Image.open(io.BytesIO(prepared.data))
    assert out.size == (200, 400)  # orientation applied
    assert not out.getexif()  # metadata stripped
    assert b"SecretCam" not in prepared.data


def test_png_with_alpha_and_palette_are_flattened() -> None:
    rgba = prepare_frame(encode("PNG", (100, 100), mode="RGBA"), "image/png", max_bytes=MAX)
    palette = prepare_frame(encode("PNG", (100, 100), mode="P"), "image/png", max_bytes=MAX)
    gray = prepare_frame(encode("PNG", (100, 100), mode="L"), "image/png", max_bytes=MAX)
    for prepared in (rgba, palette, gray):
        assert Image.open(io.BytesIO(prepared.data)).mode == "RGB"


def test_empty_frame_rejected() -> None:
    with pytest.raises(BadRequest):
        validate_frame_bytes(b"", "image/jpeg", max_bytes=MAX)


def test_oversized_frame_rejected() -> None:
    data = b"\xff\xd8\xff" + b"0" * 200
    with pytest.raises(PayloadTooLarge) as info:
        validate_frame_bytes(data, "image/jpeg", max_bytes=100)
    assert info.value.status_code == 413


@pytest.mark.parametrize(
    "data",
    [b"GIF89a" + b"0" * 100, b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"%PDF-1.7 ...", b"\x00" * 64],
)
def test_unknown_magic_bytes_rejected(data: bytes) -> None:
    with pytest.raises(UnsupportedMediaType):
        validate_frame_bytes(data, "image/jpeg", max_bytes=MAX)


def test_declared_type_must_match_content() -> None:
    with pytest.raises(UnsupportedMediaType) as info:
        validate_frame_bytes(encode("PNG", (64, 64)), "image/jpeg", max_bytes=MAX)
    assert info.value.code == "frame_type_mismatch"
    with pytest.raises(UnsupportedMediaType):
        validate_frame_bytes(encode("PNG", (64, 64)), "image/svg+xml", max_bytes=MAX)
    # missing / generic declared type is fine: the sniffed type decides
    assert validate_frame_bytes(encode("PNG", (64, 64)), None, max_bytes=MAX) == "png"
    assert validate_frame_bytes(encode("JPEG", (64, 64)), "application/octet-stream", max_bytes=MAX) == "jpeg"
    assert (
        validate_frame_bytes(encode("JPEG", (64, 64)), "image/jpeg; charset=binary", max_bytes=MAX) == "jpeg"
    )


def test_too_small_dimensions_rejected() -> None:
    with pytest.raises(ValidationFailed) as info:
        prepare_frame(encode("PNG", (16, 16)), "image/png", max_bytes=MAX)
    assert info.value.code == "frame_too_small"


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG whose IHDR claims huge dimensions (decompression-bomb probe)."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 10)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_huge_dimensions_rejected_before_decoding() -> None:
    with pytest.raises(ValidationFailed) as info:
        prepare_frame(_png_header_only(60000, 60000), "image/png", max_bytes=MAX)
    assert info.value.code == "frame_dimensions_too_large"


def test_corrupt_image_rejected() -> None:
    truncated = encode("JPEG", (800, 600))[:200]
    with pytest.raises(ValidationFailed) as info:
        prepare_frame(truncated, "image/jpeg", max_bytes=MAX)
    assert info.value.code == "frame_corrupt"
    with pytest.raises(ValidationFailed):
        prepare_frame(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20, "image/png", max_bytes=MAX)
