"""Deterministic resume parsing used when the language model is unavailable.

Only used after the model failed on the last attempt (or returned output that
could not be repaired), so the interview can still run on a basic profile
instead of none. The result is clearly marked (profile status ``partial``).
It reads the detected sections: the name/headline from the header block,
roles from date-range lines in the experience section (title and company
from the same or the preceding line), bullets as responsibilities, skills
split from the skills section, certifications and education lines.
"""

from __future__ import annotations

import re

from app.schemas.ai import (
    Achievement,
    CertificationEntry,
    ClaimToVerify,
    EducationEntry,
    JobMatch,
    ProfileAnalysis,
    ResumeExtraction,
    RoleEntry,
    SkillItem,
)
from app.services.resume.normalize import Section
from app.services.resume.skills import category_for

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = (
    rf"(?:{_MONTH}\s+\d{{4}}|{_MONTH}\s+'\d{{2}}|\d{{1,2}}[/.]\d{{4}}|\d{{4}}[-./]\d{{1,2}}(?!\d)|\d{{4}})"
)
_OPEN_END = r"present|current|currently|now|ongoing|today|to date|till date|till now|date"
_RANGE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:-|\u2013|\u2014|to|until)\s*(?P<end>{_DATE}|{_OPEN_END})",
    re.IGNORECASE,
)
DATE_RANGE = _RANGE  # role date lines (also the anchors of the per-role model calls)
_TITLE_SPLIT = re.compile(r"\s+(?:\u2014|\u2013|-|\||@|at)\s+|,\s+", re.IGNORECASE)
_NUMBER_CLAIM = re.compile(
    r"\d[\d,.]*\s*(?:%|percent|x\b|k\b|m\b|users|nodes|services|engineers|requests|ms\b)", re.IGNORECASE
)
_STATED_YEARS = re.compile(
    r"(\d{1,2}(?:\.\d)?)\+?\s+years?\s+(?:of\s+)?(?:professional\s+)?experience", re.IGNORECASE
)
STATED_YEARS = _STATED_YEARS  # also used by the quick profile
_CONTACT = re.compile(r"@|\+?\d[\d\s().-]{6,}|https?://|www\.|linkedin|github", re.IGNORECASE)


def _to_iso(value: str) -> str:
    text = value.strip().lower().rstrip(".")
    if text in (
        "present",
        "current",
        "currently",
        "now",
        "ongoing",
        "today",
        "to date",
        "till date",
        "till now",
        "date",
    ):
        return "present"
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    match = re.match(rf"({_MONTH})\s+(\d{{4}})", text)
    if match:
        month = months.index(match.group(1)[:3]) + 1
        return f"{match.group(2)}-{month:02d}"
    match = re.match(rf"({_MONTH})\s+'(\d{{2}})$", text)
    if match:
        month = months.index(match.group(1)[:3]) + 1
        year = int(match.group(2))
        return f"{2000 + year if year <= 50 else 1900 + year}-{month:02d}"
    match = re.match(r"(\d{1,2})[/.](\d{4})$", text)
    if match and 1 <= int(match.group(1)) <= 12:
        return f"{match.group(2)}-{int(match.group(1)):02d}"
    match = re.match(r"(\d{4})[./-](\d{1,2})$", text)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    return text


to_iso_date = _to_iso  # role dates as "YYYY-MM" / "YYYY" / "present" (used to match role anchors)


def _section(sections: list[Section], key: str) -> str:
    return "\n".join(s.text for s in sections if s.key == key)


def _split_title(line: str) -> tuple[str, str]:
    parts = [p.strip() for p in _TITLE_SPLIT.split(line) if p and p.strip()]
    if len(parts) >= 2:
        return parts[0][:160], parts[1][:160]
    return line.strip()[:160] or "Role", "Unknown"


def is_role_date_line(line: str) -> bool:
    """A role header or date line: holds a date range and is not a bullet or a
    sentence of prose that happens to mention years."""
    text = line.strip()
    if not text or text.startswith("- ") or not _RANGE.search(text):
        return False
    return len(text) <= 140 and not (text.endswith(".") and len(text.split()) > 8)


def parse_roles(experience: str) -> list[RoleEntry]:
    lines = [line.strip() for line in experience.split("\n")]
    roles: list[RoleEntry] = []
    for index, line in enumerate(lines):
        if not is_role_date_line(line):
            continue
        match = _RANGE.search(line)
        if match is None:  # pragma: no cover - is_role_date_line checked it
            continue
        header = line[: match.start()].strip(" |,\u2014\u2013-")
        if not header:
            previous = next(
                (lines[j] for j in range(index - 1, -1, -1) if lines[j] and not lines[j].startswith("- ")), ""
            )
            header = previous
        title, company = _split_title(header)
        bullets: list[str] = []
        for following in lines[index + 1 :]:
            if is_role_date_line(following):
                break
            if following.startswith("- "):
                bullets.append(following[2:].strip()[:300])
        achievements = [
            Achievement(text=b[:400], measurable=True) for b in bullets if _NUMBER_CLAIM.search(b)
        ][:12]
        roles.append(
            RoleEntry(
                title=title,
                company=company,
                start=_to_iso(match.group("start")),
                end=_to_iso(match.group("end")),
                responsibilities=bullets[:15],
                achievements=achievements,
            )
        )
        if len(roles) >= 25:
            break
    # Header lines directly preceding a date line were consumed as role headers;
    # drop roles whose "title" is just another role's bullet text.
    return roles


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _similar(a: str, b: str) -> bool:
    return bool(a and b and (a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a))))


def backfill_role_dates(
    extraction: ResumeExtraction, sections: list[Section]
) -> tuple[ResumeExtraction, int]:
    """Fill role dates the model left empty from date-range lines of the text.

    A parsed role is matched by company (and title when several share the
    company); only missing values are filled, nothing the model stated is
    overwritten. Returns the (possibly) updated extraction and the number of
    roles that received dates.
    """
    missing = [
        i
        for i, r in enumerate(extraction.roles)
        if r.start is None or (r.end is None and r.duration_months is None)
    ]
    if not missing:
        return extraction, 0
    parsed = parse_roles(_section(sections, "experience")) or parse_roles("\n".join(s.text for s in sections))
    if not parsed:
        return extraction, 0
    roles = list(extraction.roles)
    used: set[int] = set()
    filled = 0
    for index in missing:
        role = roles[index]
        company, title = _norm(role.company), _norm(role.title)
        best: tuple[int, int] | None = None
        for candidate_index, candidate in enumerate(parsed):
            if candidate_index in used:
                continue
            score = 2 * _similar(company, _norm(candidate.company)) + _similar(title, _norm(candidate.title))
            if score >= 2 and (best is None or score > best[0]):
                best = (score, candidate_index)
        if best is None:
            title_matches = [
                j for j, c in enumerate(parsed) if j not in used and _similar(title, _norm(c.title))
            ]
            if len(title_matches) != 1:
                continue
            best = (1, title_matches[0])
        source = parsed[best[1]]
        used.add(best[1])
        update = {"start": role.start or source.start, "end": role.end or source.end}
        if update != {"start": role.start, "end": role.end}:
            roles[index] = role.model_copy(update=update)
            filled += 1
    if not filled:
        return extraction, 0
    return extraction.model_copy(update={"roles": roles}), filled


def parse_skills(skills_text: str) -> list[SkillItem]:
    items: list[SkillItem] = []
    seen: set[str] = set()
    for line in skills_text.split("\n"):
        body = line.split(":", 1)[1] if ":" in line else line
        body = body.lstrip("- ").replace("(", ",").replace(")", ",")
        for token in re.split(r"[,;|\u2022]", body):
            name = token.strip(" .")
            if not name or len(name) > 60 or len(name.split()) > 5:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(SkillItem(name=name, category=category_for(name)))
            if len(items) >= 120:
                return items
    return items


def heuristic_extraction(text: str, sections: list[Section]) -> ResumeExtraction:
    header = next((s.text for s in sections if s.heading is None), "")
    header_lines = [line.strip() for line in header.split("\n") if line.strip()]
    name = None
    headline = None
    if header_lines:
        first = header_lines[0]
        if 1 < len(first.split()) <= 5 and not _CONTACT.search(first) and len(first) <= 60:
            name = first.title() if first.isupper() else first
        if len(header_lines) > 1 and not _CONTACT.search(header_lines[1]) and len(header_lines[1]) <= 120:
            headline = header_lines[1]
    summary = _section(sections, "summary").strip() or None
    stated = _STATED_YEARS.search(summary or text[:3000])
    certifications = [
        CertificationEntry(name=line.lstrip("- ").strip()[:200])
        for line in _section(sections, "certifications").split("\n")
        if line.strip()
    ][:20]
    education = [
        EducationEntry(institution=line.lstrip("- ").strip()[:200])
        for line in _section(sections, "education").split("\n")
        if line.strip()
    ][:10]
    return ResumeExtraction(
        candidate_name=name,
        headline=headline,
        summary=summary[:1200] if summary else None,
        total_experience_years=float(stated.group(1)) if stated else None,
        roles=parse_roles(_section(sections, "experience")),
        skills=parse_skills(_section(sections, "skills")),
        certifications=certifications,
        education=education,
    )


def heuristic_analysis(extraction: ResumeExtraction, *, has_job: bool) -> ProfileAnalysis:
    """Claims from quantified statements (priority 3), nothing inferred."""
    claims: list[ClaimToVerify] = []
    for role in extraction.roles:
        for statement in [a.text for a in role.achievements] or role.responsibilities:
            if not _NUMBER_CLAIM.search(statement):
                continue
            claims.append(
                ClaimToVerify(
                    id=f"c{len(claims) + 1}",
                    claim=statement[:400],
                    source_quote=statement[:400],
                    category="impact",
                    evidence_topics=[
                        "baseline and measurement",
                        "personal contribution",
                        "approach and trade-offs",
                    ],
                    priority=3,
                )
            )
            if len(claims) >= 8:
                break
        if len(claims) >= 8:
            break
    return ProfileAnalysis(
        claims_to_verify=claims,
        job_match=JobMatch(overall_fit=0.0, notes="Automatic analysis was unavailable." if has_job else None),
        areas_requiring_verification=["Automatic analysis was unavailable; review the resume manually."],
    )
