"""Résumé review (``resume-review`` journey step; INTAKE_INTERVIEW.md 10.4, 15.4).

The candidate sees a candidate-safe document built from their résumé profile (quick or full: roles, projects,
skills, programming languages, tools, education without dates, certifications - never claims-to-verify,
priorities, match data or inconsistencies), corrects what was read wrongly and confirms it. The corrected
document is what the live interview uses: :func:`effective_profile` applies it to the profile document T1
selects claims from.

* Refs: extracted items are ``r1..`` (roles), ``p1..`` (projects), ``e1..`` (education), positional in the profile;
  items the candidate adds are sent with ``"new"`` and re-keyed ``n1..`` (any ``n*`` ref is treated as
  candidate-added and re-keyed on every confirmation).
* ``original`` = the document of the profile the candidate reviewed: the quick and the full profile of the current
  résumé are both candidates and the one closest to the submitted document wins, so a full profile that arrived
  while the candidate was editing never shows up as "corrections".
* A skip is allowed only after :data:`SKIP_AFTER_SECONDS` of résumé processing (decision 24).
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.core.logging import get_logger
from app.db.models import CandidateProfile, CandidateResume, IntakeInterview
from app.schemas.intake import (
    CANDIDATE_REF,
    NEW_REF,
    ResumeCorrection,
    ResumeEducation,
    ResumeProject,
    ResumeReviewDoc,
    ResumeReviewOut,
    ResumeReviewRecord,
    ResumeReviewSection,
    ResumeReviewSource,
    ResumeRole,
)
from app.services.intake.access import PortalState, review_done

log = get_logger(__name__)

SKIP_AFTER_SECONDS = 180
USABLE_STATUSES = ("ready", "partial")
QUICK_STATUS = "quick"
# Résumé progress sections a candidate may see the state of (never what the analysis found).
CANDIDATE_SECTIONS = frozenset({"basics", "experience", "skills", "education", "interview"})

LANGUAGE_CATEGORIES = frozenset({"programming_language"})
TOOL_CATEGORIES = frozenset({"tool", "devops", "cloud", "database"})
_SECTIONS: tuple[tuple[str, str], ...] = (("roles", "r"), ("projects", "p"), ("education", "e"))
_LIST_FIELDS: tuple[str, ...] = ("skills", "languages", "tools", "certifications")


# ====================================================================== document from a profile


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _texts(values: Any, *, max_items: int, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list | tuple) else []:
        if isinstance(value, Mapping):
            value = value.get("text") or value.get("name")
        text = _text(value, limit)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _skill_items(skills: Any) -> Iterable[tuple[str, str]]:
    """``(name, category)`` of the profile's skills (grouped dict or flat list)."""
    if isinstance(skills, Mapping):
        for category, items in skills.items():
            for item in items if isinstance(items, list) else []:
                if isinstance(item, Mapping):
                    yield str(item.get("name") or ""), str(item.get("category") or category or "other")
                elif isinstance(item, str):
                    yield item, str(category or "other")
    elif isinstance(skills, list):
        for item in skills:
            if isinstance(item, Mapping):
                yield str(item.get("name") or ""), str(item.get("category") or "other")


def document_from_profile(profile: Mapping[str, Any] | None) -> ResumeReviewDoc:
    """The candidate-safe review document of a ``CandidateProfile`` document (quick or full)."""
    profile = profile if isinstance(profile, Mapping) else {}
    roles: list[ResumeRole] = []
    for role in profile.get("roles") or []:
        if not isinstance(role, Mapping) or len(roles) >= 12:
            continue
        title = _text(role.get("title"), 160)
        company = _text(role.get("company"), 160) or ""
        if not title and not company:
            continue
        roles.append(
            ResumeRole(
                ref=f"r{len(roles) + 1}",
                title=title or "Untitled role",
                company=company,
                start=_text(role.get("start"), 20),
                end=_text(role.get("end"), 20),
                responsibilities=_texts(role.get("responsibilities"), max_items=6, limit=400),
                technologies=_texts(role.get("technologies"), max_items=15, limit=80),
                achievements=_texts(role.get("achievements"), max_items=4, limit=400),
            )
        )
    projects: list[ResumeProject] = []
    for project in profile.get("projects") or []:
        if not isinstance(project, Mapping) or len(projects) >= 10:
            continue
        name = _text(project.get("name"), 160)
        description = _text(project.get("description"), 600) or ""
        if not name and not description:
            continue
        projects.append(
            ResumeProject(
                ref=f"p{len(projects) + 1}",
                name=name or "Untitled project",
                role=_text(project.get("role"), 160),
                description=description,
                technologies=_texts(project.get("technologies"), max_items=15, limit=80),
                outcomes=_texts(project.get("outcomes"), max_items=4, limit=400),
            )
        )
    skills: list[str] = []
    languages: list[str] = []
    tools: list[str] = []
    seen: set[str] = set()
    for name, category in _skill_items(profile.get("skills")):
        text = _text(name, 80)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        if category in LANGUAGE_CATEGORIES:
            if len(languages) < 15:
                languages.append(text[:60])
        elif category in TOOL_CATEGORIES:
            if len(tools) < 30:
                tools.append(text)
        elif len(skills) < 40:
            skills.append(text)
    education: list[ResumeEducation] = []
    for entry in profile.get("education") or []:
        if not isinstance(entry, Mapping) or len(education) >= 5:
            continue
        institution = _text(entry.get("institution"), 200)
        if not institution:
            continue
        education.append(
            ResumeEducation(
                ref=f"e{len(education) + 1}",
                institution=institution,
                degree=_text(entry.get("degree"), 160),
                field=_text(entry.get("field"), 160),
            )
        )
    years = profile.get("experience_years")
    try:
        experience_years = float(years) if years is not None else None
    except (TypeError, ValueError):
        experience_years = None
    if experience_years is not None and (
        not math.isfinite(experience_years) or not 0 <= experience_years <= 60
    ):
        experience_years = None
    return ResumeReviewDoc(
        headline=_text(profile.get("headline"), 200),
        experience_years=experience_years,
        roles=roles,
        projects=projects,
        skills=skills,
        languages=languages,
        tools=tools,
        education=education,
        certifications=_texts(profile.get("certifications"), max_items=10, limit=200),
    )


# ======================================================================= refs, re-keying, diff


def is_candidate_ref(ref: str) -> bool:
    return ref == NEW_REF or bool(CANDIDATE_REF.match(ref))


def unknown_refs(original: ResumeReviewDoc, document: ResumeReviewDoc) -> list[str]:
    """Dotted fields of extracted refs in ``document`` that ``original`` does not have."""
    problems: list[str] = []
    for section, _prefix in _SECTIONS:
        known = {item.ref for item in getattr(original, section)}
        for index, item in enumerate(getattr(document, section)):
            if not is_candidate_ref(item.ref) and item.ref not in known:
                problems.append(f"document.{section}[{index}].ref")
    return problems


def rekey(document: ResumeReviewDoc) -> ResumeReviewDoc:
    """Candidate-added items (``"new"`` or ``n*``) numbered ``n1..`` across sections."""
    counter = 0
    data = document.model_dump(mode="json")
    for section, _prefix in _SECTIONS:
        for item in data[section]:
            if is_candidate_ref(item["ref"]):
                counter += 1
                item["ref"] = f"n{counter}"
    return ResumeReviewDoc.model_validate(data)


def _fold(values: Sequence[str]) -> dict[str, str]:
    return {v.casefold(): v for v in values}


def diff_documents(original: ResumeReviewDoc, document: ResumeReviewDoc) -> list[ResumeCorrection]:
    """What the candidate changed, added or removed (paths like ``roles[r1].title`` or ``skills``)."""
    corrections: list[ResumeCorrection] = []
    for name in ("headline", "experience_years"):
        before, after = getattr(original, name), getattr(document, name)
        if before != after:
            corrections.append(ResumeCorrection(path=name, op="changed", before=before, after=after))
    for section, _prefix in _SECTIONS:
        before_items = {item.ref: item for item in getattr(original, section)}
        after_refs = set()
        for item in getattr(document, section):
            after_refs.add(item.ref)
            previous = before_items.get(item.ref)
            if previous is None:
                corrections.append(
                    ResumeCorrection(
                        path=f"{section}[{item.ref}]", op="added", after=item.model_dump(mode="json")
                    )
                )
                continue
            old, new = previous.model_dump(mode="json"), item.model_dump(mode="json")
            for key in new:
                if key != "ref" and old.get(key) != new.get(key):
                    corrections.append(
                        ResumeCorrection(
                            path=f"{section}[{item.ref}].{key}",
                            op="changed",
                            before=old.get(key),
                            after=new.get(key),
                        )
                    )
        for ref, previous in before_items.items():
            if ref not in after_refs:
                corrections.append(
                    ResumeCorrection(
                        path=f"{section}[{ref}]", op="removed", before=previous.model_dump(mode="json")
                    )
                )
    for name in _LIST_FIELDS:
        before_values, after_values = _fold(getattr(original, name)), _fold(getattr(document, name))
        for key, value in after_values.items():
            if key not in before_values:
                corrections.append(ResumeCorrection(path=name, op="added", after=value))
        for key, value in before_values.items():
            if key not in after_values:
                corrections.append(ResumeCorrection(path=name, op="removed", before=value))
    return corrections


# ============================================================================ effective profile

_SOURCE_RESUME = "resume"
_SOURCE_CANDIDATE = "candidate_correction"


def _key(*values: Any) -> str:
    return "|".join(" ".join(str(v or "").split()).casefold() for v in values)


def _record(record: ResumeReviewRecord | Mapping[str, Any] | None) -> ResumeReviewRecord | None:
    if record is None or isinstance(record, ResumeReviewRecord):
        return record
    if isinstance(record, BaseModel):
        record = record.model_dump(mode="json")
    try:
        return ResumeReviewRecord.model_validate(record)
    except ValidationError:
        log.warning("intake.resume_review_record_invalid")
        return None


def _term_pattern(term: str) -> re.Pattern[str] | None:
    term = " ".join(term.split())
    if len(term) < 2:
        return None
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def _removed_terms(record: ResumeReviewRecord) -> list[re.Pattern[str]]:
    """Names of items the candidate removed (companies of removed roles, removed projects, skills, languages,
    tools and technologies) that no longer appear anywhere in the corrected document."""
    document = record.document
    if document is None:
        return []
    remaining = set()
    for role in document.roles:
        remaining |= {role.company.casefold(), *(t.casefold() for t in role.technologies)}
    for project in document.projects:
        remaining |= {project.name.casefold(), *(t.casefold() for t in project.technologies)}
    for name in ("skills", "languages", "tools"):
        remaining |= {v.casefold() for v in getattr(document, name)}
    terms: list[str] = []
    for correction in record.corrections:
        before = correction.before
        if correction.op == "removed":
            if correction.path.startswith("roles[") and isinstance(before, Mapping):
                terms.append(str(before.get("company") or ""))
                terms += [str(t) for t in before.get("technologies") or []]
            elif correction.path.startswith("projects[") and isinstance(before, Mapping):
                terms.append(str(before.get("name") or ""))
                terms += [str(t) for t in before.get("technologies") or []]
            elif correction.path in ("skills", "languages", "tools") and isinstance(before, str):
                terms.append(before)
        elif correction.op == "changed" and correction.path.endswith(".technologies"):
            after = {str(t).casefold() for t in correction.after or []}
            terms += [str(t) for t in correction.before or [] if str(t).casefold() not in after]
    patterns = []
    for term in dict.fromkeys(t.strip() for t in terms if t and t.strip()):
        if term.casefold() in remaining:
            continue
        pattern = _term_pattern(term)
        if pattern is not None:
            patterns.append(pattern)
    return patterns


def _mentions(claim: Mapping[str, Any], patterns: Sequence[re.Pattern[str]]) -> bool:
    parts = [str(claim.get("claim") or ""), str(claim.get("source_quote") or "")]
    parts += [str(t) for t in claim.get("evidence_topics") or [] if isinstance(t, str)]
    text = "\n".join(parts)
    return any(p.search(text) for p in patterns)


def _achievements(match: Mapping[str, Any] | None, texts: Sequence[str]) -> list[dict[str, Any]]:
    known = {}
    for item in (match or {}).get("achievements") or []:
        if isinstance(item, Mapping) and item.get("text"):
            known[_key(item.get("text"))] = dict(item)
    return [
        known.get(_key(text)) or {"text": text, "metric": None, "value": None, "measurable": False}
        for text in texts
    ]


def _tag(items: Any, source: str = _SOURCE_RESUME) -> list[dict[str, Any]]:
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping):
            tagged = dict(item)
            tagged.setdefault("source", source)
            out.append(tagged)
    return out


def effective_profile(
    profile_doc: Mapping[str, Any] | None, record: ResumeReviewRecord | Mapping[str, Any] | None
) -> dict[str, Any]:
    """The profile document the live interview uses (T1 ``selection.select_claims``): ``profile_doc`` (latest
    usable profile, else the quick profile) with the candidate's confirmed corrections applied.

    Returns a plain dict with the ``CandidateProfile`` keys plus ``source`` (``resume`` | ``candidate_correction``)
    on every role, project and claim, and ``intake_review`` (``{action, corrections, removed_claim_ids}``).
    Claims about items the candidate removed are dropped. Do not validate the result with the strict
    ``CandidateProfile`` model (it carries the extra keys)."""
    base: dict[str, Any] = copy.deepcopy(dict(profile_doc or {}))
    parsed = _record(record)
    if parsed is None or parsed.action != "confirmed" or parsed.document is None:
        base["roles"] = _tag(base.get("roles"))
        base["projects"] = _tag(base.get("projects"))
        base["claims_to_verify"] = _tag(base.get("claims_to_verify"))
        base["intake_review"] = {
            "action": parsed.action if parsed else None,
            "corrections": 0,
            "removed_claim_ids": [],
        }
        return base
    document = parsed.document
    profile_roles = [r for r in base.get("roles") or [] if isinstance(r, Mapping)]
    roles_by_key = {_key(r.get("title"), r.get("company")): r for r in profile_roles}
    roles: list[dict[str, Any]] = []
    for item in document.roles:
        match = None if is_candidate_ref(item.ref) else roles_by_key.get(_key(item.title, item.company))
        role = dict(match) if match else {}
        role.update(
            {
                "title": item.title,
                "company": item.company,
                "start": item.start,
                "end": item.end,
                "responsibilities": list(item.responsibilities),
                "technologies": list(item.technologies),
                "achievements": _achievements(match, item.achievements),
                "source": _SOURCE_CANDIDATE if is_candidate_ref(item.ref) else _SOURCE_RESUME,
            }
        )
        roles.append(role)
    profile_projects = [p for p in base.get("projects") or [] if isinstance(p, Mapping)]
    projects_by_key = {_key(p.get("name")): p for p in profile_projects}
    projects: list[dict[str, Any]] = []
    for project_item in document.projects:
        match = None if is_candidate_ref(project_item.ref) else projects_by_key.get(_key(project_item.name))
        project = dict(match) if match else {}
        project.update(
            {
                "name": project_item.name,
                "role": project_item.role,
                "description": project_item.description,
                "technologies": list(project_item.technologies),
                "outcomes": list(project_item.outcomes),
                "source": _SOURCE_CANDIDATE if is_candidate_ref(project_item.ref) else _SOURCE_RESUME,
            }
        )
        projects.append(project)
    known_skills = {
        _key(name): (name, category) for name, category in _skill_items(base.get("skills")) if name
    }
    old_items: dict[str, dict[str, Any]] = {}
    grouped_source = base.get("skills")
    if isinstance(grouped_source, Mapping):
        for items in grouped_source.values():
            for entry in items if isinstance(items, list) else []:
                if isinstance(entry, Mapping) and entry.get("name"):
                    old_items[_key(entry.get("name"))] = dict(entry)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for section, fallback in (("languages", "programming_language"), ("tools", "tool"), ("skills", "other")):
        for name in getattr(document, section):
            known = known_skills.get(_key(name))
            category = fallback
            if known is not None:
                old_category = known[1]
                in_section = (
                    old_category in LANGUAGE_CATEGORIES
                    if section == "languages"
                    else old_category in TOOL_CATEGORIES
                    if section == "tools"
                    else old_category not in LANGUAGE_CATEGORIES | TOOL_CATEGORIES
                )
                category = old_category if in_section else fallback
            entry = dict(old_items.get(_key(name)) or {})
            entry.update({"name": name, "category": category})
            entry.setdefault("claimed_level", "unspecified")
            grouped.setdefault(category, []).append(entry)
    certifications_by_name = {
        _key(c.get("name")): dict(c) for c in base.get("certifications") or [] if isinstance(c, Mapping)
    }
    base.update(
        {
            "headline": document.headline,
            "experience_years": document.experience_years,
            "roles": roles,
            "companies": list(dict.fromkeys(r["company"] for r in roles if r.get("company"))),
            "projects": projects,
            "skills": grouped,
            "education": [
                {"institution": e.institution, "degree": e.degree, "field": e.field}
                for e in document.education
            ],
            "certifications": [
                certifications_by_name.get(_key(name)) or {"name": name} for name in document.certifications
            ],
        }
    )
    patterns = _removed_terms(parsed)
    claims: list[dict[str, Any]] = []
    removed: list[str] = []
    for claim in base.get("claims_to_verify") or []:
        if not isinstance(claim, Mapping):
            continue
        if patterns and _mentions(claim, patterns):
            removed.append(str(claim.get("id") or ""))
            continue
        tagged = dict(claim)
        tagged["source"] = _SOURCE_RESUME
        claims.append(tagged)
    base["claims_to_verify"] = claims
    base["intake_review"] = {
        "action": "confirmed",
        "corrections": len(parsed.corrections),
        "removed_claim_ids": removed,
    }
    return base


# ================================================================================ portal flow


@dataclass(slots=True, frozen=True)
class ProfileChoice:
    row: CandidateProfile
    stage: str  # quick | full
    document: ResumeReviewDoc


async def profile_choices(db: AsyncSession, resume: CandidateResume) -> list[ProfileChoice]:
    """The current résumé's newest usable (``full``) and newest quick profile, newest first."""
    rows = (
        await db.scalars(
            select(CandidateProfile)
            .where(
                CandidateProfile.resume_id == resume.id,
                CandidateProfile.status.in_((*USABLE_STATUSES, QUICK_STATUS)),
            )
            .order_by(CandidateProfile.version.desc(), CandidateProfile.created_at.desc())
        )
    ).all()
    choices: list[ProfileChoice] = []
    full = next((r for r in rows if r.status in USABLE_STATUSES), None)
    quick = next((r for r in rows if r.status == QUICK_STATUS), None)
    for row, stage in ((full, "full"), (quick, "quick")):
        if row is None:
            continue
        try:
            choices.append(ProfileChoice(row=row, stage=stage, document=document_from_profile(row.profile)))
        except ValidationError:
            log.warning("intake.resume_review_document_failed", profile_id=str(row.id))
    return choices


def skip_remaining_seconds(resume: CandidateResume, now: datetime) -> int:
    created = resume.created_at
    elapsed = (now - created).total_seconds() if created else SKIP_AFTER_SECONDS
    return max(0, math.ceil(SKIP_AFTER_SECONDS - elapsed))


def _sections(resume: CandidateResume) -> list[ResumeReviewSection]:
    progress = resume.progress if isinstance(resume.progress, dict) else {}
    out = []
    for section in progress.get("sections") or []:
        if isinstance(section, Mapping) and section.get("key") in CANDIDATE_SECTIONS:
            out.append(
                ResumeReviewSection(
                    key=str(section.get("key")),
                    label=section.get("label") if isinstance(section.get("label"), str) else None,
                    state=section.get("state") if isinstance(section.get("state"), str) else None,
                )
            )
    return out


async def review_view(db: AsyncSession, state: PortalState, *, now: datetime) -> ResumeReviewOut:
    """``ResumeReviewOut`` for the candidate's current résumé."""
    resume = state.resume
    if resume is None or not state.resume_usable:
        return ResumeReviewOut(status="waiting")
    confirmed, skipped = review_done(state.record, resume)
    choices = await profile_choices(db, resume)
    stage = choices[0].stage if choices else "none"
    if confirmed and state.record is not None and state.record.document is not None:
        document: ResumeReviewDoc | None = state.record.document
    else:
        document = choices[0].document if choices else None
    status = "ready" if document is not None else "waiting"
    remaining = skip_remaining_seconds(resume, now)
    pending = not (confirmed or skipped)
    return ResumeReviewOut(
        status=status,
        profile_stage=stage,  # type: ignore[arg-type]
        sections=_sections(resume),
        document=document,
        confirmed=confirmed,
        skipped=skipped,
        confirmed_at=state.record.confirmed_at if (confirmed or skipped) and state.record else None,
        can_skip=pending and status == "waiting" and remaining == 0,
        skip_available_in_s=remaining if pending and status == "waiting" else None,
    )


def _store(intake: IntakeInterview, record: ResumeReviewRecord) -> None:
    intake.resume_review = record.model_dump(mode="json")
    intake.resume_confirmed_at = record.confirmed_at


async def confirm_review(
    db: AsyncSession, state: PortalState, document: ResumeReviewDoc, *, now: datetime
) -> ResumeReviewRecord:
    """Store the candidate's confirmed (possibly corrected) document. 409 ``resume_not_ready`` without a profile;
    422 ``unknown_ref`` for extracted refs the reviewed profile does not have."""
    resume = state.resume
    if resume is None or not state.resume_usable:
        raise Conflict("Your résumé is not ready to review yet.", code="resume_not_ready")
    choices = await profile_choices(db, resume)
    if not choices:
        raise Conflict("Your résumé is still being read. Please wait a moment.", code="resume_not_ready")
    rekeyed = rekey(document)
    best: tuple[int, ProfileChoice, list[ResumeCorrection]] | None = None
    for choice in choices:
        if unknown_refs(choice.document, document):
            continue
        corrections = diff_documents(choice.document, rekeyed)
        if best is None or len(corrections) < best[0]:
            best = (len(corrections), choice, corrections)
    if best is None:
        fields = unknown_refs(choices[0].document, document)
        raise ValidationFailed(
            "Some items do not match your résumé any more. Please reload the page and try again.",
            code="unknown_ref",
            details={"field": fields[0] if fields else "document", "fields": fields},
        )
    _count, choice, corrections = best
    record = ResumeReviewRecord(
        action="confirmed",
        source=ResumeReviewSource(
            resume_id=resume.id, profile_id=choice.row.id, profile_status=choice.row.status
        ),
        original=choice.document,
        document=rekeyed,
        corrections=corrections,
        confirmed_at=now,
    )
    _store(state.intake, record)
    return record


async def skip_review(db: AsyncSession, state: PortalState, *, now: datetime) -> ResumeReviewRecord:
    """Record a skip (only after :data:`SKIP_AFTER_SECONDS` of processing; 409 ``skip_not_allowed``). A review the
    candidate already confirmed for this résumé is kept (idempotent)."""
    resume = state.resume
    if resume is None or not state.resume_usable:
        raise Conflict("There is no résumé to review.", code="skip_not_allowed")
    confirmed, skipped = review_done(state.record, resume)
    if (confirmed or skipped) and state.record is not None:
        return state.record
    remaining = skip_remaining_seconds(resume, now)
    if remaining > 0:
        raise Conflict(
            "You can continue without reviewing once your résumé has been processing for 3 minutes.",
            code="skip_not_allowed",
            details={"skip_available_in_s": remaining},
        )
    choices = await profile_choices(db, resume)
    choice = choices[0] if choices else None
    record = ResumeReviewRecord(
        action="skipped",
        source=ResumeReviewSource(
            resume_id=resume.id,
            profile_id=choice.row.id if choice else None,
            profile_status=choice.row.status if choice else None,
        ),
        original=choice.document if choice else None,
        document=choice.document if choice else None,
        corrections=[],
        confirmed_at=now,
    )
    _store(state.intake, record)
    return record


__all__ = [
    "SKIP_AFTER_SECONDS",
    "ProfileChoice",
    "confirm_review",
    "diff_documents",
    "document_from_profile",
    "effective_profile",
    "is_candidate_ref",
    "profile_choices",
    "rekey",
    "review_view",
    "skip_remaining_seconds",
    "skip_review",
    "unknown_refs",
]
