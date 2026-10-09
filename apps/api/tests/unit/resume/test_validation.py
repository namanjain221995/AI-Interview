"""Upload validation: accepted formats and the attack corpus."""

from __future__ import annotations

import codecs

import pytest

from app.services.resume.validation import (
    MAX_TXT_BYTES,
    ResumeRejected,
    decode_text,
    validate_resume_bytes,
)
from tests.unit.resume import corpus

MAX = 10 * 1024 * 1024


def validate(data: bytes, filename: str = "resume.pdf", declared: str | None = None, max_bytes: int = MAX):
    return validate_resume_bytes(data, filename=filename, declared_content_type=declared, max_bytes=max_bytes)


def rejected(data: bytes, filename: str = "resume.pdf", **kwargs) -> ResumeRejected:
    with pytest.raises(ResumeRejected) as info:
        validate(data, filename, **kwargs)
    error = info.value
    assert error.status_code == 422
    assert error.message and "Traceback" not in error.message
    return error


# ------------------------------------------------------------------ accepted


@pytest.mark.parametrize(
    ("name", "kind", "pages"),
    [
        ("jane_doe_devops.pdf", "pdf", 1),
        ("jane_doe_devops_scanned.pdf", "pdf", 2),
        ("marcus_okafor_backend.pdf", "pdf", 1),
        ("jane_doe_devops.docx", "docx", None),
        ("sofia_martinez_frontend.docx", "docx", None),
        ("jane_doe_devops.txt", "txt", None),
        ("priya_raman_ml.txt", "txt", None),
        ("jane_doe_devops.png", "png", 1),
        ("jane_doe_devops.jpg", "jpeg", 1),
    ],
)
def test_sample_resumes_are_accepted(name: str, kind: str, pages: int | None) -> None:
    result = validate(corpus.sample(name), name)
    assert result.kind == kind
    assert result.page_count == pages
    assert result.warnings == []
    assert result.content_type.startswith(("application/", "text/", "image/"))


def test_text_pdf_and_empty_password_pdf() -> None:
    assert validate(corpus.pdf_bytes(text=corpus.RESUME_TEXT)).kind == "pdf"
    result = validate(corpus.pdf_bytes(encrypt_with=""))
    assert result.kind == "pdf" and "encrypted_with_empty_password" in result.warnings


def test_webp_and_tiff_are_accepted() -> None:
    import io

    from PIL import Image

    for fmt, kind in (("WEBP", "webp"), ("TIFF", "tiff")):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 900), "white").save(buffer, format=fmt)
        assert validate(buffer.getvalue(), f"cv.{kind}").kind == kind


def test_wrong_extension_is_detected_by_content() -> None:
    result = validate(corpus.sample("jane_doe_devops.png"), "resume.pdf", declared="application/pdf")
    assert result.kind == "png"
    assert result.extension == "png"
    assert {"extension_mismatch", "declared_type_mismatch"} <= set(result.warnings)


def test_docx_external_template_is_flagged_not_rejected() -> None:
    result = validate(corpus.docx_with_external_template(), "cv.docx")
    assert result.kind == "docx"
    assert "external_references" in result.warnings
    assert result.details["external_references"] == ["attachedTemplate"]
    assert result.details["external_hyperlinks"] == 1


# --------------------------------------------------------------------- PDFs


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (corpus.pdf_with_javascript, "/JavaScript"),
        (corpus.pdf_with_open_action_launch, "/OpenAction /Launch"),
        (corpus.pdf_with_embedded_file, "/EmbeddedFile"),
        (corpus.pdf_with_xfa, "/XFA"),
        (corpus.pdf_with_rich_media, "/RichMedia"),
        (corpus.pdf_with_obfuscated_javascript, "/JavaScript (obfuscated)"),
    ],
)
def test_pdf_active_content_is_rejected(builder, expected: str) -> None:
    error = rejected(builder())
    assert error.code == "active_content"
    assert expected in error.details["found"]


def test_password_protected_pdf_is_rejected() -> None:
    assert rejected(corpus.pdf_bytes(encrypt_with="hunter2")).code == "encrypted_file"


@pytest.mark.parametrize("builder", [corpus.pdf_with_trailing_zip, corpus.pdf_with_trailing_html])
def test_pdf_polyglots_are_rejected(builder) -> None:
    assert rejected(builder()).code == "invalid_file_structure"


def test_corrupt_pdf_is_rejected() -> None:
    data = corpus.pdf_bytes()
    assert rejected(data[:200] + b"%%EOF").code == "file_damaged"


def test_too_many_pages() -> None:
    def mutate(writer) -> None:
        for _ in range(40):
            writer.add_blank_page(612, 792)

    assert rejected(corpus.pdf_bytes(mutate)).code == "too_many_pages"


# --------------------------------------------------------------------- DOCX


@pytest.mark.parametrize(
    ("builder", "code"),
    [
        (corpus.docm_with_macro, "active_content"),
        (corpus.docx_with_activex, "active_content"),
        (corpus.docx_with_ole_object, "active_content"),
        (corpus.zip_bomb_docx, "invalid_file_structure"),
        (corpus.docx_with_zip_slip, "invalid_file_structure"),
        (corpus.plain_zip, "unsupported_file_type"),
    ],
)
def test_docx_attacks_are_rejected(builder, code: str) -> None:
    assert rejected(builder(), "cv.docx").code == code


def test_docm_reports_macro_findings() -> None:
    error = rejected(corpus.docm_with_macro(), "cv.docx")
    assert {"macro", "macro_enabled_content_type"} <= set(error.details["found"])


def test_docx_with_trailing_data_is_rejected() -> None:
    assert rejected(corpus.docx_bytes() + b"MZ" + b"\x00" * 100, "cv.docx").code == "invalid_file_structure"


# ------------------------------------------------------------------- images


def test_decompression_bomb_header_is_rejected_before_decoding() -> None:
    assert rejected(corpus.png_declaring_huge_dimensions(), "cv.png").code == "image_too_large"


def test_tiny_image_is_rejected() -> None:
    assert rejected(corpus.png_bytes(64, 64), "cv.png").code == "image_too_small"


@pytest.mark.parametrize("builder", [corpus.jpeg_with_appended_zip, corpus.png_with_trailing_script])
def test_image_polyglots_are_rejected(builder) -> None:
    assert rejected(builder(), "cv.jpg").code == "invalid_file_structure"


def test_truncated_image_is_rejected() -> None:
    assert rejected(corpus.truncated_png(), "cv.png").code in {"file_damaged", "invalid_file_structure"}


# ------------------------------------------------------- executables & types


@pytest.mark.parametrize(
    ("builder", "filename"),
    [
        (corpus.windows_exe, "resume.pdf"),
        (corpus.elf_binary, "resume.docx"),
        (corpus.gif_bytes, "resume.png"),
        (corpus.legacy_doc, "resume.doc"),
    ],
)
def test_renamed_executables_and_unsupported_types(builder, filename: str) -> None:
    error = rejected(builder(), filename)
    assert error.code == "unsupported_file_type"
    assert "PDF" in error.message


def test_shebang_script_is_rejected() -> None:
    assert rejected(b"#!/bin/sh\nrm -rf /\n" * 10, "resume.txt").code == "unsupported_file_type"


# --------------------------------------------------------------------- text


def test_text_encodings() -> None:
    body = "Résumé of Zoë — Kubernetes engineer with ten years of experience in platform work."
    assert decode_text(body.encode("utf-8")) == (body, "utf-8")
    assert decode_text(codecs.BOM_UTF8 + body.encode("utf-8"))[0] == body
    assert decode_text(body.encode("utf-16"))[0] == body
    assert decode_text(body.encode("utf-16-le"))[0] == body
    latin = "Résumé of José, Kubernetes engineer with ten years of experience".encode("latin-1")
    assert decode_text(latin) == (
        "Résumé of José, Kubernetes engineer with ten years of experience",
        "latin-1",
    )
    assert validate(body.encode("utf-16"), "cv.txt").details["encoding"] == "utf-16"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"<!DOCTYPE html><html><body><script>alert(1)</script>" + b"x" * 100, "unsupported_file_type"),
        (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'>" + b"y" * 100, "unsupported_file_type"),
        (bytes(range(1, 32)) * 40, "unsupported_file_type"),
        (b"   \n\n  ...  \n", "file_empty"),
    ],
)
def test_bad_text_files(data: bytes, code: str) -> None:
    assert rejected(data, "cv.txt").code == code


def test_size_limits() -> None:
    assert rejected(b"%PDF-1.7\n" + b"0" * 2048, max_bytes=1024).code == "file_too_large"
    assert rejected(b"", "cv.pdf").code == "file_empty"
    big_text = ("Kubernetes engineer. " * (MAX_TXT_BYTES // 10)).encode()
    assert rejected(big_text, "cv.txt").code == "file_too_large"
