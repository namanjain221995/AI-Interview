"""Deterministic quick profile, built in milliseconds right after text extraction.

The model profile (``profile.generate``) takes 10-20 s on the shared model;
the candidate should see that we read the file within a second or two. The
quick profile is built from the normalised text alone (no model call):

* name and headline from the header block, contact details as *presence
  flags only* (never stored as values);
* roles with dates from the date-range lines of the experience section
  (:func:`app.services.resume.fallback.parse_roles`), experience years from
  the merged role intervals, neutral overlap/gap findings;
* skills from the skills section (labels dropped, parenthesised lists split,
  stated proficiency kept) plus technologies of the skill vocabulary named
  elsewhere in the text;
* education entries (institution, degree, field - years are stripped: a
  graduation year is an age proxy), certifications, projects;
* claims to verify only from quantified statements (verbatim), as in the
  ``partial`` fallback profile.

It is stored as a ``candidate_profiles`` row with status ``quick``
(:data:`QUICK_STATUS`). ``ResumeService.latest_profile()`` and the interview
engine's profile gate only accept ``ready``/``partial`` rows, so a quick
profile is never mistaken for the full one. While the model sections finish
one by one, the same row is refined in place (see
:mod:`app.services.resume.profile`).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.schemas.ai import (
    Achievement,
    CertificationEntry,
    EducationEntry,
    ProjectEntry,
    ResumeExtraction,
    RoleEntry,
    SkillItem,
)
from app.schemas.ai import CandidateProfile as CandidateProfileDocument
from app.services.resume.experience import compute_experience, experience_inconsistencies
from app.services.resume.fallback import STATED_YEARS, heuristic_analysis, parse_roles
from app.services.resume.normalize import Section, detect_sections
from app.services.resume.skills import category_for, find_vocabulary_skills, group_skills, merge_skills

QUICK_STATUS = "quick"

# Candidate-facing sections of the profile and what fills them.
SECTION_LABELS: dict[str, str] = {
    "basics": "Name and headline",
    "experience": "Work experience",
    "skills": "Skills",
    "education": "Education and certifications",
    "projects": "Projects",
    "interview": "Interview topics",
}
# Shown to the candidate (``interview`` only as a state, never its content).
CANDIDATE_SECTIONS: tuple[str, ...] = ("basics", "experience", "skills", "education", "interview")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\(?\d[\d\s().-]{7,}\d")
_LINK = re.compile(
    r"https?://|www\.|linkedin|github\.com|\b[\w-]+\.(?:com|io|dev|me|net|org|app)\b", re.IGNORECASE
)
_CONTACTISH = re.compile(
    r"@|https?://|www\.|linkedin|github|\d{3}[\s.-]?\d{3,}|\b[\w-]+\.(?:com|io|dev|me|net|org)\b", re.I
)
_NAME_LINE = re.compile(r"^[^\W\d_][^\W\d_'’.\-]*(?:\s+[^\W\d_][^\W\d_'’.\-]*){1,4}$")
_NOT_NAME = frozenset(
    {"resume", "résumé", "curriculum vitae", "cv", "profile", "summary", "contact", "personal details"}
)
_LABEL_PREFIX = re.compile(r"^(?:name|full name)\s*:\s*", re.IGNORECASE)

_LEVELS = {
    "basic": "beginner",
    "basics": "beginner",
    "beginner": "beginner",
    "novice": "beginner",
    "elementary": "beginner",
    "intermediate": "intermediate",
    "advanced": "advanced",
    "expert": "expert",
}
_SPLIT_ITEMS = re.compile(r"\s*[,;|•·]\s*")
_YEARS_RANGE = re.compile(
    r"\(?\s*\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?(?:19|20)\d{2}\b"
    r"(?:\s*(?:-|–|—|to|until)\s*(?:(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?"
    r"(?:19|20)\d{2}|present|current|now|expected))?\s*\)?",
    re.IGNORECASE,
)
_DEGREE = re.compile(
    r"^(?P<degree>(?:"
    r"b\.?\s?sc\.?|b\.?\s?s\.?|b\.?\s?a\.?|b\.?\s?eng\.?|b\.?\s?tech\.?|b\.?\s?e\.?|b\.?\s?com\.?|bba|"
    r"m\.?\s?sc\.?|m\.?\s?s\.?|m\.?\s?a\.?|m\.?\s?eng\.?|m\.?\s?tech\.?|m\.?\s?e\.?|mba|m\.?\s?phil\.?|"
    r"ph\.?\s?d\.?|d\.?\s?phil\.?|a\.?\s?s\.?|a\.?\s?a\.?|"
    r"(?:bachelor|master|associate|doctor)(?:'s|s)?(?:\s+(?:of|in)\s+(?:science|arts|engineering|technology|"
    r"business administration|commerce|computer applications|philosophy|fine arts))?|"
    r"doctorate|diploma|high school diploma|ged|certificate"
    r"))(?=\s|,|$)\.?\s*(?:(?:in|of)\s+)?(?P<field>.*)$",
    re.IGNORECASE,
)
_INSTITUTION = re.compile(
    r"\b(?:university|universit[äeé]t?|college|institute|institut|school|academy|polytechnic|politecnico|"
    r"conservatory|iit|nit|bootcamp)\b",
    re.IGNORECASE,
)
_EDU_SPLIT = re.compile(r"\s+(?:—|–|-|\||@|at)\s+|,\s+", re.IGNORECASE)
_EDU_SKIP = re.compile(
    r"^(?:gpa|grade|relevant coursework|coursework|courses|honou?rs|thesis|activities|awards)\b", re.I
)
_CERT_YEAR = re.compile(r"[\s,(\-–]*\(?\b((?:19|20)\d{2})\b\)?\s*$")
_PROJECT_LABEL = re.compile(r"^(?P<name>[^:]{2,80}):\s+(?P<desc>.+)$")
_NUMBER = re.compile(r"\d")


@dataclass(slots=True)
class QuickProfile:
    """The quick profile document (``CandidateProfile`` contract fields only)
    and what we know about how it was built (stored on the resume)."""

    document: dict[str, Any]
    extraction: ResumeExtraction
    meta: dict[str, Any] = field(default_factory=dict)


def _section_text(sections: list[Section], key: str) -> str:
    return "\n".join(s.text for s in sections if s.key == key)


def _strip_bullet(line: str) -> str:
    return line[2:].strip() if line.startswith("- ") else line.strip()


def contact_presence(text: str) -> dict[str, bool]:
    """Which contact details the header shows - flags only, never the values."""
    head = "\n".join(text.split("\n")[:12])
    without_email = _EMAIL.sub(" ", head)
    digits_ok = any(len(re.sub(r"\D", "", m.group(0))) >= 9 for m in _PHONE.finditer(without_email))
    return {
        "email": bool(_EMAIL.search(head)),
        "phone": digits_ok,
        "links": bool(_LINK.search(without_email)),
    }


def identity(sections: list[Section], text: str) -> tuple[str | None, str | None]:
    """(name, headline) from the header block: the first name-like line and
    the first plain line after it."""
    header = next((s.text for s in sections if s.heading is None), "") or "\n".join(text.split("\n")[:6])
    lines = [line.strip() for line in header.split("\n") if line.strip()][:8]
    name: str | None = None
    headline: str | None = None
    for index, raw in enumerate(lines):
        line = _LABEL_PREFIX.sub("", raw).strip()
        if name is None:
            if line.lower() in _NOT_NAME or _CONTACTISH.search(line) or len(line) > 60:
                continue
            if _NAME_LINE.match(line):
                name = line.title() if line.isupper() else line
                for after in lines[index + 1 : index + 3]:
                    candidate = after.strip(" |")
                    if not candidate or _CONTACTISH.search(candidate) or len(candidate) > 120:
                        continue
                    if len(candidate.split()) > 14 or candidate.endswith("."):
                        break  # a sentence: the summary, not a headline
                    headline = candidate
                    break
                break
    return name, headline


def parse_skill_items(skills_text: str) -> list[SkillItem]:
    """Skills of the skills section: a leading ``Category:`` label is dropped,
    ``AWS (EKS, IAM)`` yields AWS, EKS and IAM, ``Rust (basic)`` keeps the
    stated level."""
    items: list[SkillItem] = []
    seen: set[str] = set()

    def add(name: str, level: str = "unspecified") -> None:
        name = name.strip(" .:-–")
        if not name or len(name) > 60 or len(name.split()) > 5 or not re.search(r"[A-Za-z]", name):
            return
        if name.lower() in _LEVELS:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        items.append(SkillItem(name=name, category=category_for(name), claimed_level=level))

    for raw in skills_text.split("\n"):
        line = _strip_bullet(raw)
        if not line:
            continue
        if ":" in line:
            label, rest = line.split(":", 1)
            if len(label) <= 40 and rest.strip():
                line = rest
        # Split at top-level separators (not inside parentheses).
        depth, current, parts = 0, "", []
        for ch in line:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            if depth == 0 and ch in ",;|•·":
                parts.append(current)
                current = ""
            else:
                current += ch
        parts.append(current)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            match = re.match(r"^(?P<name>[^(\[]+?)\s*[(\[](?P<inner>[^)\]]*)[)\]]\s*(?P<tail>.*)$", part)
            if not match:
                add(part)
                continue
            inner = [p.strip() for p in _SPLIT_ITEMS.split(match.group("inner")) if p.strip()]
            level = next((_LEVELS[p.lower()] for p in inner if p.lower() in _LEVELS), "unspecified")
            add(match.group("name"), level)
            for sub in inner:
                if sub.lower() not in _LEVELS:
                    add(sub)
            if match.group("tail"):
                add(match.group("tail"))
        if len(items) >= 120:
            break
    return items[:120]


def parse_education(education_text: str) -> list[EducationEntry]:
    """Institution, degree and field per entry; years and date ranges are
    removed (graduation years are an age proxy and are never kept)."""
    entries: list[dict[str, str | None]] = []
    for raw in education_text.split("\n"):
        line = _YEARS_RANGE.sub(" ", _strip_bullet(raw))
        line = re.sub(r"\s{2,}", " ", line).strip(" ,|-–—()")
        if not line or _EDU_SKIP.match(line):
            continue
        parts = [p.strip(" ,()") for p in _EDU_SPLIT.split(line) if p and p.strip(" ,()")]
        institution = next((p for p in parts if _INSTITUTION.search(p)), None)
        degree: str | None = None
        field_of_study: str | None = None
        for part in parts:
            if part is institution:
                continue
            match = _DEGREE.match(part)
            if match:
                degree = match.group("degree").strip()
                field_of_study = (match.group("field") or "").strip(" ,.") or None
                break
        if institution is None and degree is None:
            if entries and not entries[-1]["institution"] and len(line) <= 120:
                entries[-1]["institution"] = line
            continue
        if institution is None and degree is not None and entries and entries[-1]["degree"] is None:
            entries[-1]["degree"], entries[-1]["field"] = degree, field_of_study
            continue
        if degree is None and institution is not None and entries and not entries[-1]["institution"]:
            entries[-1]["institution"] = institution
            continue
        entries.append({"institution": institution, "degree": degree, "field": field_of_study})
    out: list[EducationEntry] = []
    for entry in entries[:10]:
        institution = (entry["institution"] or entry["degree"] or "").strip()
        if not institution:
            continue
        out.append(
            EducationEntry(
                institution=institution[:200],
                degree=(entry["degree"] or None) if entry["institution"] else None,
                field=(entry["field"] or None) if entry["institution"] else None,
            )
        )
    return out


def parse_certifications(text: str) -> list[CertificationEntry]:
    out: list[CertificationEntry] = []
    for raw in text.split("\n"):
        line = _strip_bullet(raw)
        if not line or len(line) < 3:
            continue
        year = None
        match = _CERT_YEAR.search(line)
        if match:
            year = match.group(1)
            line = line[: match.start()].strip(" ,(-–")
        if line:
            out.append(CertificationEntry(name=line[:200], year=year))
        if len(out) >= 20:
            break
    return out


def parse_projects(text: str) -> list[ProjectEntry]:
    """``- Name: description`` bullets, or a title line followed by bullets."""
    projects: list[ProjectEntry] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and current["name"]:
            description = " ".join(current["lines"])[:600]
            outcomes = [o[:300] for o in current["outcomes"]][:8]
            projects.append(
                ProjectEntry(name=current["name"][:160], description=description, outcomes=outcomes)
            )
        current = None

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        bullet = line.startswith("- ")
        body = _strip_bullet(line)
        labelled = _PROJECT_LABEL.match(body)
        if bullet and labelled and (current is None or not current["lines"] or current["from_label"]):
            flush()
            current = {
                "name": labelled.group("name").strip(),
                "lines": [labelled.group("desc").strip()],
                "outcomes": [],
                "from_label": True,
            }
            continue
        if not bullet and len(body) <= 100 and not body.endswith("."):
            flush()
            name, _, desc = body.partition(":")
            current = {
                "name": name.strip(),
                "lines": [desc.strip()] if desc.strip() else [],
                "outcomes": [],
                "from_label": False,
            }
            continue
        if current is None:
            continue
        if bullet and _NUMBER.search(body):
            current["outcomes"].append(body)
        else:
            current["lines"].append(body)
    flush()
    return projects[:25]


def quick_extraction(text: str, sections: list[Section]) -> tuple[ResumeExtraction, dict[str, Any]]:
    """Deterministic :class:`ResumeExtraction` of the whole text."""
    name, headline = identity(sections, text)
    summary = _section_text(sections, "summary").strip() or None
    stated = STATED_YEARS.search(summary or text[:3000])
    experience_text = _section_text(sections, "experience")
    roles = parse_roles(experience_text) or (parse_roles(text) if not experience_text else [])
    cleaned_roles: list[RoleEntry] = []
    for role in roles:
        company = "" if role.company.strip().lower() == "unknown" else role.company
        achievements = [a for a in role.achievements if a.text]
        cleaned_roles.append(role.model_copy(update={"company": company, "achievements": achievements[:12]}))
    if headline is None and cleaned_roles:
        headline = cleaned_roles[0].title
    skills_text = _section_text(sections, "skills")
    skills = parse_skill_items(skills_text)
    outside = "\n".join(s.text for s in sections if s.key != "skills")
    vocabulary = find_vocabulary_skills(outside)
    skills = merge_skills([*skills, *vocabulary])[:120]
    extraction = ResumeExtraction(
        candidate_name=name[:160] if name else None,
        headline=headline[:200] if headline else None,
        summary=summary[:1200] if summary else None,
        total_experience_years=float(stated.group(1)) if stated and float(stated.group(1)) <= 60 else None,
        roles=cleaned_roles[:25],
        projects=parse_projects(_section_text(sections, "projects")),
        education=parse_education(_section_text(sections, "education")),
        certifications=parse_certifications(_section_text(sections, "certifications")),
        skills=skills,
    )
    meta = {
        "sections_found": sorted({s.key for s in sections if s.heading is not None}),
        "skills_from_section": len(parse_skill_items(skills_text)) if skills_text else 0,
        "skills_from_text": len(vocabulary),
    }
    return extraction, meta


def document_from_extraction(
    extraction: ResumeExtraction,
    *,
    candidate_id: uuid.UUID | str,
    claims: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """``CandidateProfile`` document of a deterministic extraction (no job match)."""
    computation = compute_experience(extraction.roles, today=today)
    years = computation.years if computation.years is not None else extraction.total_experience_years
    companies: list[str] = []
    for role in extraction.roles:
        if role.company and role.company.lower() not in {c.lower() for c in companies}:
            companies.append(role.company)
    achievements: list[Achievement] = []
    seen: set[str] = set()
    for role in extraction.roles:
        for item in role.achievements:
            key = " ".join(item.text.split()).lower()
            if key and key not in seen:
                seen.add(key)
                achievements.append(item)
    claim_items = heuristic_analysis(extraction, has_job=False).claims_to_verify if claims else []
    document = CandidateProfileDocument(
        candidate_id=str(candidate_id),
        candidate_name=extraction.candidate_name,
        headline=extraction.headline,
        summary=extraction.summary or extraction.headline,
        experience_years=years,
        roles=extraction.roles,
        companies=companies,
        skills=group_skills(merge_skills(extraction.skills)),
        projects=extraction.projects,
        achievements=achievements,
        education=extraction.education,
        certifications=extraction.certifications,
        leadership_experience=extraction.leadership_experience,
        career_progression=extraction.career_progression,
        inconsistencies=experience_inconsistencies(computation, extraction.total_experience_years)[:20],
        claims_to_verify=claim_items,
    )
    return document.model_dump(mode="json")


def build_quick_profile(
    text: str,
    *,
    candidate_id: uuid.UUID | str,
    sections: list[Section] | None = None,
    partial: bool = False,
    pages_done: int | None = None,
    pages_total: int | None = None,
    today: date | None = None,
) -> QuickProfile:
    """The quick profile of ``text`` (normalised). ``partial`` marks a
    profile built from the first scanned pages while OCR continues."""
    started = time.perf_counter()
    sections = sections if sections is not None else detect_sections(text)
    extraction, meta = quick_extraction(text, sections)
    document = document_from_extraction(extraction, candidate_id=candidate_id, today=today)
    meta.update(
        {
            "contact": contact_presence(text),
            "partial": partial,
            "pages_done": pages_done,
            "pages_total": pages_total,
            "characters": len(text),
            "built_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    )
    return QuickProfile(document=document, extraction=extraction, meta=meta)


def initial_sections(*, has_text: bool = True, partial: bool = False) -> list[dict[str, Any]]:
    """Section states right after the quick profile: the deterministic read is
    shown (``quick``) and the model refines each section (``pending``)."""
    state = "quick" if has_text else "pending"
    out = []
    for key in SECTION_LABELS:
        out.append(
            {
                "key": key,
                "label": SECTION_LABELS[key],
                "state": "pending" if key == "interview" else state,
                "partial": partial if key != "interview" else False,
                "at": None,
            }
        )
    return out
