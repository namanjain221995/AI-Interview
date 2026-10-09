"""Resume upload validation: type sniffing, structural checks, active content.

Nothing the client says about a file is trusted. The type is detected from
magic bytes (``filetype``) and then the structure of that type is parsed and
checked:

* **PDF** (``pypdf``): must start with ``%PDF-``; encrypted files are rejected
  unless they open with the empty user password; active content is rejected
  wherever it hides (``/JavaScript``, ``/JS``, ``/Launch``,
  ``/EmbeddedFile(s)``, ``/RichMedia``, ``/XFA``, dangerous action types and an
  ``/OpenAction`` that runs an action). The object graph is walked (catches
  names inside compressed object streams) *and* the raw bytes are scanned
  (catches ``#xx``-escaped names and objects that are not linked from the
  trailer). Data appended after the last ``%%EOF`` or a ZIP trailer means a
  polyglot and is rejected.
* **DOCX** (``zipfile`` + ``python-docx``): ZIP sanity (member count, total
  uncompressed size <= 50 MB, per-member and overall compression ratio <= 100,
  no encrypted members, no path tricks, no trailing data), macros (``vbaProject.bin``,
  macro-enabled content types), ActiveX controls and embedded OLE objects are
  rejected; external relationships are counted and non-hyperlink ones flagged.
* **TXT**: decoded as UTF-8 / UTF-16 (BOM or NUL-pattern) / Latin-1, size
  capped, control characters and markup documents rejected.
* **Images** (PNG, JPEG, WebP, TIFF; ``Pillow``): pixel-count guard before
  decoding (decompression bombs), side limits, ``verify()`` plus a full decode,
  and trailing data after the image end (polyglots) is rejected.

Every rejection raises :class:`ResumeRejected` (HTTP 422) whose message is
safe to show to a candidate; ``code`` carries the machine-readable reason.
"""

from __future__ import annotations

import codecs
import io
import re
import struct
import warnings
import zipfile
from dataclasses import dataclass, field
from typing import Any, Literal

import filetype
from PIL import Image

from app.core.errors import ValidationFailed

FileKind = Literal["pdf", "docx", "txt", "png", "jpeg", "webp", "tiff"]

CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "tiff": "image/tiff",
}
EXTENSIONS: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "txt": "txt",
    "png": "png",
    "jpeg": "jpg",
    "webp": "webp",
    "tiff": "tiff",
}
IMAGE_KINDS: frozenset[str] = frozenset({"png", "jpeg", "webp", "tiff"})
ALLOWED_DESCRIPTION = "PDF, Word (.docx), plain text (.txt), PNG, JPEG, WebP or TIFF"

# ----------------------------------------------------------------- limits
MAX_PDF_PAGES = 30
MAX_PDF_OBJECTS = 200_000
MAX_TRAILING_BYTES = 1024  # after %%EOF (whitespace, a stray newline or two)
MAX_ZIP_MEMBERS = 1000
MAX_ZIP_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100.0
MAX_XML_SCAN_BYTES = 2 * 1024 * 1024
MAX_TXT_BYTES = 1024 * 1024
MIN_TEXT_CHARS = 30
MAX_CONTROL_RATIO = 0.01
MAX_IMAGE_PIXELS = 25_000_000  # ~5000 x 5000; a 300 DPI letter page is 8.4 MP
MAX_IMAGE_SIDE = 10_000
MIN_IMAGE_SIDE = 300
MAX_IMAGE_TRAILING = 64

# ------------------------------------------------------------ PDF content
SUSPICIOUS_PDF_KEYS: frozenset[str] = frozenset(
    {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/EmbeddedFiles", "/RichMedia", "/XFA"}
)
DANGEROUS_ACTIONS: frozenset[str] = frozenset(
    {
        "/JavaScript",
        "/Launch",
        "/ImportData",
        "/SubmitForm",
        "/GoToE",
        "/GoToR",
        "/Rendition",
        "/RichMediaExecute",
    }
)
_RAW_PDF_KEYS = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA)(?=[\s/<>\[\]()%{}]|$)")
_ESCAPED_PDF_NAME = re.compile(rb"/((?:[A-Za-z0-9]|#[0-9A-Fa-f]{2}){1,40})")
_ZIP_EOCD = b"PK\x05\x06"
_EXECUTABLE_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",  # Windows PE
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"#!",
)
_MARKUP_PREFIXES = ("<!doctype", "<html", "<svg", "<?xml", "<?php", "<script", "<head", "<body", "<iframe")
_IMAGE_EMBEDDED_SIGNATURES: tuple[bytes, ...] = (b"%PDF-", b"<script", b"<?php", b"<html", _ZIP_EOCD)

# DOCX
_RELATIONSHIP = re.compile(rb"<Relationship\b([^>]*)/?>", re.IGNORECASE)
_ATTRIBUTE = re.compile(rb'([A-Za-z:]+)\s*=\s*"([^"]*)"')
_MACRO_CONTENT_TYPE = re.compile(rb"macroEnabled|vbaProject|vnd\.ms-office\.activeX", re.IGNORECASE)


class ResumeRejected(ValidationFailed):
    """The upload was refused. ``message`` is candidate-safe; ``code`` is the reason."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=code, details={"reason": code, **(details or {})})


def _too_large(max_bytes: int) -> ResumeRejected:
    mb = max_bytes / (1024 * 1024)
    limit = f"{mb:.0f} MB" if mb >= 1 else f"{max_bytes // 1024} KB"
    return ResumeRejected(
        "file_too_large",
        f"The file is larger than {limit}. Please upload a smaller file.",
        details={"max_bytes": max_bytes},
    )


def unsupported(detected: str | None = None) -> ResumeRejected:
    return ResumeRejected(
        "unsupported_file_type",
        f"This file type is not supported. Please upload a {ALLOWED_DESCRIPTION} file.",
        details={"detected": detected} if detected else None,
    )


def damaged(detail: str | None = None) -> ResumeRejected:
    return ResumeRejected(
        "file_damaged",
        "We could not read this file. It may be damaged; please export it again and re-upload.",
        details={"detail": detail[:120]} if detail else None,
    )


def invalid_structure(detail: str) -> ResumeRejected:
    return ResumeRejected(
        "invalid_file_structure",
        "This file's internal structure is not valid for its type. Please export the document again "
        "(for example with 'Save as PDF') and re-upload.",
        details={"detail": detail[:120]},
    )


def active_content(found: list[str]) -> ResumeRejected:
    return ResumeRejected(
        "active_content",
        "This file contains active content (such as scripts, macros or embedded files) and cannot be "
        "accepted. Please save it as a plain PDF or DOCX and try again.",
        details={"found": sorted(set(found))[:10]},
    )


def encrypted() -> ResumeRejected:
    return ResumeRejected(
        "encrypted_file",
        "This file is password-protected. Please upload a version without a password.",
    )


@dataclass(slots=True)
class ValidatedFile:
    """Outcome of a successful validation."""

    kind: FileKind
    content_type: str
    extension: str
    size_bytes: int
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "page_count": self.page_count,
            "warnings": list(self.warnings),
            **self.details,
        }


# =============================================================================
# Entry point
# =============================================================================


def validate_resume_bytes(
    data: bytes,
    *,
    filename: str | None = None,
    declared_content_type: str | None = None,
    max_bytes: int,
) -> ValidatedFile:
    """Validate an upload (CPU-bound: call through ``asyncio.to_thread``)."""
    size = len(data)
    if size == 0:
        raise ResumeRejected("file_empty", "The file appears to be empty.")
    if size > max_bytes:
        raise _too_large(max_bytes)
    head = data[:16]
    if any(head.startswith(sig) for sig in _EXECUTABLE_SIGNATURES):
        raise unsupported("executable")

    kind = _sniff(data)
    if kind == "pdf":
        result = _validate_pdf(data)
    elif kind == "docx":
        result = _validate_docx(data)
    elif kind in IMAGE_KINDS:
        result = _validate_image(data, kind)
    elif kind == "txt":
        result = _validate_txt(data, max_bytes)
    else:  # pragma: no cover - _sniff raises for everything else
        raise unsupported(kind)

    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if (
        declared
        and declared not in {result.content_type, "application/octet-stream"}
        and not (result.kind == "jpeg" and declared == "image/jpg")
    ):
        result.warnings.append("declared_type_mismatch")
        result.details["declared_content_type"] = declared[:120]
    ext = _extension_of(filename)
    if ext and ext not in _accepted_extensions(result.kind):
        result.warnings.append("extension_mismatch")
        result.details["original_extension"] = ext[:16]
    return result


def _extension_of(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].strip().lower()


def _accepted_extensions(kind: str) -> set[str]:
    return {
        "pdf": {"pdf"},
        "docx": {"docx"},
        "txt": {"txt", "text", "md"},
        "png": {"png"},
        "jpeg": {"jpg", "jpeg", "jpe"},
        "webp": {"webp"},
        "tiff": {"tif", "tiff"},
    }.get(kind, set())


def _sniff(data: bytes) -> str:
    guess = filetype.guess(data[:8192])
    mime = guess.mime if guess is not None else None
    if data.startswith(b"%PDF-"):
        return "pdf"
    if mime == "application/pdf":
        # filetype accepts "%PDF" without the dash; the header must be exact.
        raise invalid_structure("pdf header")
    if data.startswith(b"PK\x03\x04"):
        return "docx"  # any ZIP: _validate_docx decides whether it really is a Word document
    if mime in ("image/png", "image/apng"):
        return "png"
    if mime == "image/jpeg":
        return "jpeg"
    if mime == "image/webp":
        return "webp"
    if mime == "image/tiff":
        return "tiff"
    if mime is not None:
        raise unsupported(guess.extension if guess is not None else mime)
    if data.startswith(b"\xd0\xcf\x11\xe0"):  # OLE compound file (legacy .doc/.xls)
        raise unsupported("ole")
    return "txt"


# =============================================================================
# PDF
# =============================================================================


def _validate_pdf(data: bytes) -> ValidatedFile:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError, FileNotDecryptedError, PdfReadError

    _check_pdf_tail(data)
    raw_findings = _raw_pdf_findings(data)
    warnings_out: list[str] = []
    details: dict[str, Any] = {"encrypted": False}
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                outcome = reader.decrypt("")
            except (DependencyError, NotImplementedError) as exc:
                raise encrypted() from exc
            if not outcome:
                raise encrypted()
            warnings_out.append("encrypted_with_empty_password")
            details["encrypted"] = True
        page_count = len(reader.pages)
    except ResumeRejected:
        raise
    except FileNotDecryptedError as exc:
        raise encrypted() from exc
    except (
        PdfReadError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        RecursionError,
        OSError,
    ) as exc:
        raise damaged(type(exc).__name__) from exc
    except Exception as exc:  # noqa: BLE001 - any parser failure means we cannot trust the file
        raise damaged(type(exc).__name__) from exc
    if page_count <= 0:
        raise damaged("no pages")
    if page_count > MAX_PDF_PAGES:
        raise ResumeRejected(
            "too_many_pages",
            f"This document has too many pages for a resume (maximum {MAX_PDF_PAGES}).",
            details={"page_count": page_count},
        )
    graph_findings = _walk_pdf(reader)
    findings = sorted(set(raw_findings) | set(graph_findings))
    if findings:
        raise active_content(findings)
    return ValidatedFile(
        kind="pdf",
        content_type=CONTENT_TYPES["pdf"],
        extension=EXTENSIONS["pdf"],
        size_bytes=len(data),
        page_count=page_count,
        warnings=warnings_out,
        details=details,
    )


def _check_pdf_tail(data: bytes) -> None:
    eof = data.rfind(b"%%EOF")
    if eof < 0:
        raise damaged("missing %%EOF")
    trailing = data[eof + 5 :]
    if len(trailing.strip(b"\r\n\t \x00")) > MAX_TRAILING_BYTES:
        raise invalid_structure("data after %%EOF")
    tail = data[-(65_536 + 22) :]
    if _ZIP_EOCD in tail:
        raise invalid_structure("zip trailer inside pdf")


def _decode_pdf_name(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i : i + 1] == b"#" and i + 3 <= len(raw):
            try:
                out.append(int(raw[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
    return "/" + out.decode("latin-1")


def _raw_pdf_findings(data: bytes) -> set[str]:
    found = {"/" + match.group(1).decode("latin-1") for match in _RAW_PDF_KEYS.finditer(data)}
    for match in _ESCAPED_PDF_NAME.finditer(data):
        raw = match.group(1)
        if b"#" not in raw:
            continue
        name = _decode_pdf_name(raw)
        if name in SUSPICIOUS_PDF_KEYS or name in DANGEROUS_ACTIONS:
            found.add(name + " (obfuscated)")
    return found


def _walk_pdf(reader: Any) -> set[str]:
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

    findings: set[str] = set()
    stack: list[Any] = [reader.trailer]
    seen: set[tuple[int, int]] = set()
    visited = 0

    def resolve(value: Any) -> Any:
        try:
            return value.get_object() if isinstance(value, IndirectObject) else value
        except Exception:  # noqa: BLE001 - broken references cannot carry actions we would run
            return None

    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in seen:
                continue
            seen.add(key)
            obj = resolve(obj)
            if obj is None:
                continue
        visited += 1
        if visited > MAX_PDF_OBJECTS:
            raise invalid_structure("too many pdf objects")
        if isinstance(obj, DictionaryObject):
            for key_name, value in obj.items():
                name = str(key_name)
                if name in SUSPICIOUS_PDF_KEYS:
                    findings.add(name)
                elif name == "/S":
                    action = resolve(value)
                    if isinstance(action, NameObject) and str(action) in DANGEROUS_ACTIONS:
                        findings.add(f"action {action}")
                elif name == "/OpenAction":
                    target = resolve(value)
                    if isinstance(target, DictionaryObject) and "/S" in target:
                        action = str(resolve(target.get("/S")))
                        if action != "/GoTo":
                            findings.add(f"/OpenAction {action}")
                elif name == "/AcroForm":
                    form = resolve(value)
                    if isinstance(form, DictionaryObject) and "/XFA" in form:
                        findings.add("/XFA")
                if isinstance(value, IndirectObject | DictionaryObject | ArrayObject):
                    stack.append(value)
        elif isinstance(obj, ArrayObject):
            stack.extend(
                item for item in obj if isinstance(item, IndirectObject | DictionaryObject | ArrayObject)
            )
    return findings


# =============================================================================
# DOCX
# =============================================================================


def _validate_docx(data: bytes) -> ValidatedFile:
    _check_zip_tail(data)
    warnings_out: list[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as exc:
        raise damaged("zip") from exc
    with archive:
        infos = archive.infolist()
        if not infos:
            raise damaged("empty zip")
        if len(infos) > MAX_ZIP_MEMBERS:
            raise invalid_structure("too many zip members")
        total_uncompressed = 0
        total_compressed = 0
        names: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/") or "\x00" in name:
                raise invalid_structure("unsafe zip member name")
            if info.flag_bits & 0x1:
                raise encrypted()
            if info.file_size > 0 and info.compress_size == 0:
                raise invalid_structure("zip member ratio")
            if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ZIP_RATIO:
                raise invalid_structure("zip member ratio")
            total_uncompressed += info.file_size
            total_compressed += info.compress_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED:
                raise invalid_structure("zip uncompressed size")
            names[name.lower()] = info
        if total_compressed > 0 and total_uncompressed / total_compressed > MAX_ZIP_RATIO:
            raise invalid_structure("zip overall ratio")
        if "[content_types].xml" not in names or "word/document.xml" not in names:
            raise unsupported("zip")

        found: list[str] = []
        for lower in names:
            base = lower.rsplit("/", 1)[-1]
            if base in ("vbaproject.bin", "vbadata.xml") or base.startswith("vbaproject"):
                found.append("macro")
            if lower.startswith("word/activex/"):
                found.append("activex")
            if lower.startswith("word/embeddings/"):
                if base.endswith(".bin") or base.startswith("oleobject"):
                    found.append("embedded_ole_object")
                else:
                    warnings_out.append("embedded_document")
        content_types = _read_member(archive, names["[content_types].xml"])
        if _MACRO_CONTENT_TYPE.search(content_types):
            found.append("macro_enabled_content_type")
        if found:
            raise active_content(found)

        hyperlinks = 0
        external_refs: list[str] = []
        for lower, info in names.items():
            if not lower.endswith(".rels"):
                continue
            for attrs in _relationships(_read_member(archive, info)):
                if attrs.get("targetmode", "").lower() != "external":
                    continue
                rel_type = attrs.get("type", "").rsplit("/", 1)[-1]
                if rel_type == "hyperlink":
                    hyperlinks += 1
                else:
                    external_refs.append(rel_type or "unknown")
        if external_refs:
            warnings_out.append("external_references")

    try:
        import docx

        document = docx.Document(io.BytesIO(data))
        paragraph_count = len(document.paragraphs)
    except Exception as exc:  # noqa: BLE001 - python-docx raises many types for malformed parts
        raise damaged(type(exc).__name__) from exc
    return ValidatedFile(
        kind="docx",
        content_type=CONTENT_TYPES["docx"],
        extension=EXTENSIONS["docx"],
        size_bytes=len(data),
        warnings=warnings_out,
        details={
            "zip_members": len(infos),
            "uncompressed_bytes": total_uncompressed,
            "external_hyperlinks": hyperlinks,
            "external_references": sorted(set(external_refs))[:10],
            "paragraphs": paragraph_count,
        },
    )


def _check_zip_tail(data: bytes) -> None:
    eocd = data.rfind(_ZIP_EOCD)
    if eocd < 0 or len(data) - eocd < 22:
        raise damaged("zip end record")
    (comment_length,) = struct.unpack("<H", data[eocd + 20 : eocd + 22])
    if eocd + 22 + comment_length != len(data):
        raise invalid_structure("data after zip end record")


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_XML_SCAN_BYTES:
        raise invalid_structure("oversized package part")
    try:
        with archive.open(info) as handle:
            return handle.read(MAX_XML_SCAN_BYTES + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError) as exc:
        raise damaged("zip member") from exc


def _relationships(xml: bytes) -> list[dict[str, str]]:
    out = []
    for match in _RELATIONSHIP.finditer(xml):
        attrs = {
            key.decode("latin-1").lower(): value.decode("utf-8", "replace")
            for key, value in _ATTRIBUTE.findall(match.group(1))
        }
        out.append(attrs)
    return out


# =============================================================================
# Images
# =============================================================================

_PIL_FORMATS = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP", "tiff": "TIFF"}


def _validate_image(data: bytes, kind: str) -> ValidatedFile:
    _check_image_trailing(data, kind)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as probe:
                image_format = probe.format
                width, height = probe.size
                frames = int(getattr(probe, "n_frames", 1) or 1)
                if width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_IMAGE_SIDE:
                    raise _image_too_large(width, height)
                if min(width, height) < MIN_IMAGE_SIDE:
                    raise ResumeRejected(
                        "image_too_small",
                        "The image is too small to read. Please upload a higher-resolution image or a PDF.",
                        details={"width": width, "height": height},
                    )
                probe.verify()
            with Image.open(io.BytesIO(data)) as full:
                full.load()  # verify() does not decode pixel data; a truncated file fails here
        except ResumeRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise _image_too_large(None, None) from exc
        except Exception as exc:  # noqa: BLE001 - Pillow raises OSError/SyntaxError/ValueError/struct.error...
            raise damaged(type(exc).__name__) from exc
    if image_format != _PIL_FORMATS[kind]:
        raise invalid_structure(f"image format {image_format}")
    warnings_out = ["multi_frame_image"] if frames > 1 else []
    return ValidatedFile(
        kind=kind,  # type: ignore[arg-type]
        content_type=CONTENT_TYPES[kind],
        extension=EXTENSIONS[kind],
        size_bytes=len(data),
        page_count=frames,
        warnings=warnings_out,
        details={"width": width, "height": height, "frames": frames},
    )


def _image_too_large(width: int | None, height: int | None) -> ResumeRejected:
    return ResumeRejected(
        "image_too_large",
        f"The image dimensions are too large. Please upload an image under {MAX_IMAGE_PIXELS // 1_000_000} "
        f"megapixels and {MAX_IMAGE_SIDE:,} pixels per side.",
        details={"width": width, "height": height} if width else None,
    )


def _check_image_trailing(data: bytes, kind: str) -> None:
    for signature in _IMAGE_EMBEDDED_SIGNATURES:
        if signature in data:
            raise invalid_structure("embedded document signature in image")
    end: int | None = None
    if kind == "png":
        end = _png_end(data)
    elif kind == "jpeg":
        marker = data.rfind(b"\xff\xd9")
        end = marker + 2 if marker >= 0 else None
        if end is None:
            raise damaged("jpeg end marker")
    elif kind == "webp" and len(data) >= 12:
        (riff_size,) = struct.unpack("<I", data[4:8])
        end = riff_size + 8 + (riff_size & 1)
    if end is not None and len(data) - end > MAX_IMAGE_TRAILING:
        raise invalid_structure("data after image end")


def _png_end(data: bytes) -> int:
    pos = 8
    while pos + 12 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        chunk_type = data[pos + 4 : pos + 8]
        pos += 12 + length
        if chunk_type == b"IEND":
            return pos
        if length > len(data):
            break
    raise damaged("png chunks")


# =============================================================================
# Plain text
# =============================================================================


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode resume text: BOMs, UTF-8, BOM-less UTF-16 (NUL pattern), Latin-1."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="strict"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="strict"), "utf-16"
    sample = data[:4096]
    if len(sample) >= 4 and sample.count(b"\x00") >= len(sample) // 4:
        even_nuls = sample[0::2].count(b"\x00")
        odd_nuls = sample[1::2].count(b"\x00")
        encoding = "utf-16-be" if even_nuls > odd_nuls else "utf-16-le"
        return data.decode(encoding, errors="strict"), encoding
    try:
        return data.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def _validate_txt(data: bytes, max_bytes: int) -> ValidatedFile:
    limit = min(MAX_TXT_BYTES, max_bytes)
    if len(data) > limit:
        raise _too_large(limit)
    try:
        text, encoding = decode_text(data)
    except UnicodeDecodeError as exc:
        raise unsupported("binary") from exc
    if "\x00" in text:
        raise unsupported("binary")
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r\f")
    if text and controls / len(text) > MAX_CONTROL_RATIO:
        raise unsupported("binary")
    unprintable = sum(1 for ch in text if not ch.isprintable() and ch not in "\t\n\r\f")
    if text and unprintable / len(text) > 0.03:
        raise unsupported("binary")
    lowered = text.lstrip().lower()
    if lowered.startswith(_MARKUP_PREFIXES):
        raise unsupported("markup")
    if sum(ch.isalnum() for ch in text) < MIN_TEXT_CHARS:
        raise ResumeRejected("file_empty", "The file does not contain enough readable text.")
    return ValidatedFile(
        kind="txt",
        content_type=CONTENT_TYPES["txt"],
        extension=EXTENSIONS["txt"],
        size_bytes=len(data),
        details={"encoding": encoding, "characters": len(text)},
    )
