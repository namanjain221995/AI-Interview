"""Resume text normalisation, section detection and chunking.

* :func:`normalize_text` - NFKC, control characters, ligatures, unified
  bullets, de-hyphenation of words broken across lines, whitespace, repeated
  page furniture ("Page 2 of 3").
* :func:`detect_sections` - heading lines are matched against a vocabulary
  (summary, experience, projects, skills, education, certifications; anything
  else that looks like a heading starts an ``other`` section). Text before the
  first heading is the contact/header block (``other``) unless it reads like a
  summary paragraph.
* :func:`chunk_sections` - ~800 character chunks inside each section, cut at
  blank lines / line ends / sentence ends, with ~120 characters of overlap so
  a statement split across two chunks is still retrievable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SECTION_KEYS: tuple[str, ...] = (
    "summary",
    "experience",
    "projects",
    "skills",
    "education",
    "certifications",
    "other",
)

_HEADINGS: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary",
        "professional summary",
        "career summary",
        "executive summary",
        "profile",
        "professional profile",
        "about",
        "about me",
        "objective",
        "career objective",
        "overview",
        "personal statement",
    ),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "relevant experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "professional background",
        "experience highlights",
        "positions held",
    ),
    "projects": (
        "projects",
        "personal projects",
        "selected projects",
        "key projects",
        "side projects",
        "open source",
        "open source contributions",
        "project experience",
        "portfolio",
    ),
    "skills": (
        "skills",
        "technical skills",
        "core skills",
        "key skills",
        "skills and tools",
        "skills & tools",
        "technologies",
        "tech stack",
        "technical expertise",
        "competencies",
        "core competencies",
        "tools",
        "tools and technologies",
        "areas of expertise",
        "expertise",
    ),
    "education": (
        "education",
        "academic background",
        "education and training",
        "academic qualifications",
        "academics",
    ),
    "certifications": (
        "certifications",
        "certification",
        "certificates",
        "licenses",
        "licenses and certifications",
        "licenses & certifications",
        "certifications and training",
        "courses",
        "training",
        "professional development",
    ),
    "other": (
        "awards",
        "honors",
        "honours",
        "awards and honors",
        "publications",
        "languages",
        "interests",
        "hobbies",
        "volunteering",
        "volunteer experience",
        "references",
        "achievements",
        "accomplishments",
        "patents",
        "talks",
        "conferences",
        "activities",
        "additional information",
    ),
}
_HEADING_LOOKUP: dict[str, str] = {
    phrase: section for section, phrases in _HEADINGS.items() for phrase in phrases
}
# Last word of a heading-styled line that names its section.
_HEADING_TAIL: dict[str, str] = {
    "projects": "projects",
    "experience": "experience",
    "experiences": "experience",
    "skills": "skills",
    "education": "education",
    "certifications": "certifications",
    "certificates": "certifications",
}

_BULLETS = "\u2022\u25cf\u25aa\u2023\u25e6\u2219\u25cb\u25a0\u25a1\u25ba\u25b6\u27a2\u27a4\u2713\u2714\u25c6\u25c7\u2043\u2981\u00b7*"
_BULLET_LINE = re.compile(rf"^\s*(?:[{re.escape(_BULLETS)}]|[-\u2013\u2014](?=\s))\s*")
_HYPHEN_BREAK = re.compile(r"([A-Za-z]{2,})-\n([a-z]{2,})")
_PAGE_FURNITURE = re.compile(
    r"^\s*(?:page\s+\d+(?:\s*(?:of|/)\s*\d+)?|\d+\s*/\s*\d+|-\s*\d+\s*-)\s*$", re.IGNORECASE
)
_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f\u200c-\u200f\u2028\u2029\ufeff\ufffe]")
_HEADING_CLEAN = re.compile(r"[^a-z& ]+")
# Glyphs pdfminer cannot map come out as "(cid:127)"; at a line start they are bullets.
_CID_BULLET = re.compile(r"^(\s*)\(cid:\d+\)\s*", re.MULTILINE)
_CID = re.compile(r"\(cid:\d+\)")


def normalize_text(text: str) -> str:
    """Canonical resume text (keeps line structure)."""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    value = _CONTROL.sub("", value)
    value = value.replace("\u00ad", "")  # soft hyphen
    value = _CID_BULLET.sub(r"\1- ", value)
    value = _CID.sub(" ", value)
    value = _HYPHEN_BREAK.sub(r"\1\2", value)
    lines: list[str] = []
    for raw in value.split("\n"):
        line = _SPACES.sub(" ", raw).strip()
        if _PAGE_FURNITURE.match(line):
            continue
        if line and _BULLET_LINE.match(line) and not re.match(r"^-\d", line):
            body = _BULLET_LINE.sub("", line, count=1).strip()
            line = f"- {body}" if body else ""
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def meaningful_chars(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


# =============================================================================
# Sections
# =============================================================================


@dataclass(slots=True)
class Section:
    key: str
    heading: str | None
    text: str


def heading_section(line: str) -> str | None:
    """Section key when ``line`` is a resume heading, else None."""
    stripped = line.strip().strip(":").strip()
    if not stripped or len(stripped) > 48 or stripped.startswith("- "):
        return None
    words = stripped.split()
    if len(words) > 5:
        return None
    normalized = _HEADING_CLEAN.sub(" ", stripped.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in _HEADING_LOOKUP:
        return _HEADING_LOOKUP[normalized]
    # "Academic Projects", "INDUSTRY EXPERIENCE", "Technical Skills:" ...: a short
    # heading-styled line whose last word names a section (never "Project Manager").
    if len(words) <= 4 and not re.search(r"[\d.,;!?()—–|@]", stripped):
        styled = (
            stripped.isupper()
            or line.rstrip().endswith(":")
            or all(w[:1].isupper() for w in words if w.isalpha())
        )
        last = normalized.split(" ")[-1] if normalized else ""
        if styled and last in _HEADING_TAIL:
            return _HEADING_TAIL[last]
    return None


def detect_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    current_key = "other"
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(Section(current_key, current_heading, body))

    for line in text.split("\n"):
        key = heading_section(line)
        if key is not None:
            flush()
            buffer = []
            current_key, current_heading = key, line.strip().strip(":").strip()
            continue
        buffer.append(line)
    flush()
    if sections and sections[0].heading is None:
        sections[0] = _classify_preamble(sections[0])
    return sections


def _classify_preamble(section: Section) -> Section:
    """The block before the first heading is usually name + contact details; a
    long prose paragraph there is a summary."""
    paragraphs = [p for p in section.text.split("\n\n") if p.strip()]
    if not paragraphs:
        return section
    longest = max(paragraphs, key=len)
    sentences = len(re.findall(r"[a-z][.!?](?:\s|$)", longest))
    if len(longest) >= 150 and sentences >= 2:
        return Section("summary", None, section.text)
    return section


def section_stats(sections: list[Section]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for section in sections:
        stats[section.key] = stats.get(section.key, 0) + len(section.text)
    return stats


# =============================================================================
# Chunking
# =============================================================================


@dataclass(slots=True)
class Chunk:
    index: int
    section: str
    text: str


def _split_units(text: str, target: int) -> list[str]:
    """Paragraphs, then lines, then sentences - each unit <= target chars."""
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= target:
            units.append(paragraph)
            continue
        for line in paragraph.split("\n"):
            line = line.strip()
            if not line:
                continue
            if len(line) <= target:
                units.append(line)
                continue
            sentences = re.split(r"(?<=[.!?;])\s+", line)
            piece = ""
            for sentence in sentences:
                while len(sentence) > target:  # one giant "sentence": hard cut at a space
                    cut = sentence.rfind(" ", 0, target)
                    cut = cut if cut > target // 2 else target
                    if piece:
                        units.append(piece)
                        piece = ""
                    units.append(sentence[:cut].strip())
                    sentence = sentence[cut:].strip()
                if piece and len(piece) + 1 + len(sentence) > target:
                    units.append(piece)
                    piece = sentence
                else:
                    piece = f"{piece} {sentence}".strip()
            if piece:
                units.append(piece)
    return units


def _tail(text: str, overlap: int) -> str:
    """Last ~``overlap`` characters of ``text``, starting at a clean boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    window = text[-overlap:]
    for sep in ("\n", ". ", "; ", ", ", " "):
        pos = window.find(sep)
        if 0 <= pos < len(window) - 20:
            return window[pos + len(sep) :].strip()
    return window.strip()


def chunk_sections(sections: list[Section], *, target: int = 800, overlap: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        units = _split_units(section.text, target)
        current = ""
        for unit in units:
            if current and len(current) + 1 + len(unit) > target:
                chunks.append(Chunk(len(chunks), section.key, current))
                carry = _tail(current, overlap)
                current = (
                    f"{carry}\n{unit}".strip() if carry and len(carry) + 1 + len(unit) <= target else unit
                )
            else:
                current = f"{current}\n{unit}".strip() if current else unit
        if current:
            chunks.append(Chunk(len(chunks), section.key, current))
    return chunks
