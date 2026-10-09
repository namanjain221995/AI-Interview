"""Fine-grained model calls of ``profile.generate``: plan, wire schemas, merge.

Measured 2026-10-08 on the shared 35B: the previous design (three extraction
parts + one analysis, all concurrent) generated ~2.5-6k tokens per resume and
took 39-98 s; a single part produced up to 2.3k tokens (skills as full
objects) and the analysis ~1.6-2k. Latency is bounded by the longest call
*and* by the total generated tokens (the server's decode throughput is
shared: per-stream speed fell from ~35 to ~20 tok/s at 12 concurrent streams
next to production traffic). So the plan:

1. derives what needs no model: the summary is the resume's own summary
   section, claim source quotes are looked up verbatim in the text
   (:func:`best_quote`), missing requirements follow from the strengths;
2. asks for compact outputs: skills as ``category -> names`` (levels and
   details only where stated), each bullet in one list only, short evidence;
3. splits the rest into small calls that run concurrently, each with its own
   token budget: ``identity``; ``roles:N`` (one call per role group, anchored
   by the role's date line so the model sees the whole experience section but
   writes only its roles) plus ``roles:rest`` (roles without a detected date
   line); ``projects``; ``skills:N`` (skills-section slices); ``background``
   (education + certifications); ``claims:scale`` / ``claims:impact`` /
   ``claims:depth`` / ``claims:people`` (at most ``CLAIMS_PER_GROUP`` each);
   ``match`` and ``reqs:N`` (groups of ``REQUIREMENTS_PER_CALL``) when a job is given;
   ``topics``; ``gaps``.

The parts merge deterministically (:func:`merge_parts`); a part that failed
falls back to the deterministic quick parse for its fields only.

vLLM prefix caching is disabled on this deployment (``/metrics``
``cache_config_info enable_prefix_caching="False"``), so prompts carry only
the excerpt a part needs instead of a shared whole-resume prefix.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from rapidfuzz import fuzz

from app.ai.structured import describe_schema
from app.domain.enums import ModelTask
from app.schemas.ai import (
    Achievement,
    CertificationEntry,
    ClaimToVerify,
    EducationEntry,
    Inconsistency,
    JobMatch,
    ProfileAnalysis,
    ProjectEntry,
    RecommendedTopic,
    RequirementMatch,
    ResumeExtraction,
    RoleEntry,
    SkillItem,
)
from app.services.resume.fallback import (
    DATE_RANGE,
    heuristic_analysis,
    is_role_date_line,
    parse_roles,
    to_iso_date,
)
from app.services.resume.llm_schemas import to_contract, wire
from app.services.resume.matching import MUST_HAVE_KINDS, RequirementInput
from app.services.resume.normalize import Section
from app.services.resume.prompts import (
    ANALYST_SYSTEM,
    ANALYST_USER,
    MAX_RESUME_CHARS,
    PARSER_SYSTEM,
    PARSER_USER,
    PART_TASKS,
    requirements_text,
)
from app.services.resume.quick import parse_projects, parse_skill_items
from app.services.resume.skills import CATEGORIES, category_for, merge_skills

# Bump when the plan, prompts or merge change: cached parts and re-upload reuse
# are only valid within one version.
PIPELINE_VERSION = "fine-2026-10-08"

ROLE_TOKENS_PER_CALL = 320  # estimated output per role group
MAX_ROLES_PER_CALL = 3
SKILL_ITEMS_PER_CALL = 45
PROJECTS_PER_CALL = 4
REQUIREMENTS_PER_CALL = 6
JOB_DESCRIPTION_CHARS = 2500
CLAIMS_PER_GROUP = 2
QUOTE_MIN_F1 = 0.3
EVIDENCE_MAX_CHARS = 80
# Evidence topics that would steer the interviewer towards a protected-trait proxy.
_PROXY_TOPIC = re.compile(
    r"\b(gaps?|continuity|continuous employment|career break|time off|employment history)\b", re.I
)

# --------------------------------------------------------------------------
# Wire schemas (every field required so guided decoding cannot skip it)
# --------------------------------------------------------------------------

_WIRE = ConfigDict(extra="ignore", populate_by_name=True)


def _model(name: str, doc: str | None = None, **fields: Any) -> type[BaseModel]:
    return create_model(name, __config__=_WIRE, __doc__=doc, **fields)  # type: ignore[call-overload,no-any-return]


class SkillLevel(BaseModel):
    model_config = _WIRE
    name: str = Field(..., max_length=80)
    level: Literal["beginner", "intermediate", "advanced", "expert"]


class SkillDetail(BaseModel):
    model_config = _WIRE
    name: str = Field(..., max_length=80)
    years: float | None = Field(..., ge=0, le=60)
    last_used_year: int | None
    evidence: str | None = Field(..., max_length=300)


SkillsByCategory = _model(
    "SkillsByCategory",
    "Skill names as written, by category",
    **{category: (list[str], Field(..., max_length=120)) for category in CATEGORIES},
)

IdentityPart = _model(
    "ResumeIdentity",
    candidate_name=(str | None, Field(..., max_length=160)),
    headline=(str | None, Field(..., max_length=200)),
    total_experience_years=(float | None, Field(..., ge=0, le=60)),
    leadership_experience=(list[str], Field(..., max_length=5)),
    career_progression=(str | None, Field(..., max_length=600)),
    inconsistencies=(list[wire(Inconsistency)], Field(..., max_length=5)),  # type: ignore[valid-type]
)
RolesPart = _model("ResumeRoles", roles=(list[wire(RoleEntry)], Field(..., max_length=25)))  # type: ignore[valid-type]


def roles_model(count: int) -> type[BaseModel]:
    """Exactly ``count`` roles: an anchored call cannot add the other roles it sees
    (measured: asked for one role, the model wrote all three, 422 tokens)."""
    return _model(
        "ResumeRoles",
        roles=(list[wire(RoleEntry)], Field(..., min_length=count, max_length=count)),  # type: ignore[valid-type]
    )


ProjectsPart = _model(
    "ResumeProjects",
    projects=(list[wire(ProjectEntry)], Field(..., max_length=25)),  # type: ignore[valid-type]
)
SkillsPart = _model(
    "ResumeSkills",
    by_category=(SkillsByCategory, ...),
    levels=(list[SkillLevel], Field(..., max_length=60)),
    details=(list[SkillDetail], Field(..., max_length=60)),
)
BackgroundPart = _model(
    "ResumeBackground",
    education=(list[wire(EducationEntry)], Field(..., max_length=10)),  # type: ignore[valid-type]
    certifications=(list[wire(CertificationEntry)], Field(..., max_length=20)),  # type: ignore[valid-type]
)

CLAIM_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "scale": (
        "the scale and design of systems the candidate built or ran (users, nodes, requests, data volume, number of "
        "services, architecture they owned)",
        ("scale", "technical"),
    ),
    "impact": (
        "measurable results the candidate claims (cost, latency, reliability, delivery speed, quality, adoption)",
        ("impact", "technical"),
    ),
    "depth": (
        "hands-on technical depth the candidate claims (designs, techniques and tools they say they built or "
        "introduced themselves)",
        ("technical",),
    ),
    "people": (
        "leadership, ownership, mentoring and stated tenure (never employment gaps or continuity)",
        ("leadership", "ownership", "tenure", "other"),
    ),
}


def _claims_model(group: str) -> type[BaseModel]:
    categories = CLAIM_GROUPS[group][1]
    claim = _model(
        "Claim",
        claim=(str, Field(..., max_length=400)),
        category=(Literal[categories], ...),  # type: ignore[valid-type]
        competency_keys=(list[str], Field(..., max_length=2)),
        evidence_topics=(list[str], Field(..., min_length=2, max_length=3)),
        priority=(int, Field(..., ge=1, le=5)),
    )
    return _model("ResumeClaims", claims=(list[claim], Field(..., max_length=CLAIMS_PER_GROUP)))  # type: ignore[valid-type]


ClaimsParts: dict[str, type[BaseModel]] = {group: _claims_model(group) for group in CLAIM_GROUPS}

MatchPart = _model(
    "JobFit",
    overall_fit=(float, Field(..., ge=0, le=100)),
    relevant_experience_years=(float | None, Field(..., ge=0, le=60)),
    strengths=(list[str], Field(..., max_length=4)),
    gaps=(list[str], Field(..., max_length=4)),
    notes=(str | None, Field(..., max_length=400)),
)


def requirements_model(count: int) -> type[BaseModel]:
    """Exactly ``count`` assessments in the listed order (guided decoding
    enforces the length). ``n`` is the requirement's number; its name is not
    echoed (we know it by position) - that halves the tokens per entry."""
    entry = _model(
        "RequirementAssessment",
        n=(int, Field(..., ge=1, le=count)),
        strength=(Literal["strong", "partial", "weak", "none"], ...),
        evidence=(str | None, Field(..., max_length=EVIDENCE_MAX_CHARS)),
    )
    return _model(
        "RequirementAssessments",
        matched_requirements=(list[entry], Field(..., min_length=count, max_length=count)),  # type: ignore[valid-type]
    )


TopicsPart = _model(
    "InterviewTopics",
    recommended_topics=(list[wire(RecommendedTopic)], Field(..., max_length=6)),  # type: ignore[valid-type]
)
GapsPart = _model(
    "ProfileGaps",
    potential_skill_gaps=(list[str], Field(..., max_length=3)),
    areas_requiring_verification=(list[str], Field(..., max_length=3)),
    job_relevant_strengths=(list[str], Field(..., max_length=3)),
    job_relevant_missing_areas=(list[str], Field(..., max_length=3)),
)

_NESTED_FOR_PROMPT: dict[str, tuple[type[BaseModel], ...]] = {
    "identity": (wire(Inconsistency),),
    "roles": (wire(RoleEntry), wire(Achievement)),
    "projects": (wire(ProjectEntry),),
    "skills": (SkillsByCategory, SkillLevel, SkillDetail),
    "background": (wire(EducationEntry), wire(CertificationEntry)),
    "topics": (wire(RecommendedTopic),),
}


def schema_text(kind: str, model: type[BaseModel]) -> str:
    parts = [describe_schema(model)]
    nested = list(_NESTED_FOR_PROMPT.get(kind, ()))
    if kind == "claims":
        nested = [model.model_fields["claims"].annotation.__args__[0]]  # type: ignore[union-attr]
    elif kind == "reqs":
        nested = [model.model_fields["matched_requirements"].annotation.__args__[0]]  # type: ignore[union-attr]
    parts.extend(f"{item.__name__}:\n{describe_schema(item)}" for item in nested)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartCall:
    """One model call of the profile."""

    key: str  # "identity", "roles:0", "roles:rest", "skills:1", "claims:tech", "reqs:2", ...
    kind: str  # identity | roles | projects | skills | background | claims | match | reqs | topics | gaps
    section: str  # UI section: basics | experience | projects | skills | education | interview
    task: ModelTask
    schema: type[BaseModel]
    messages: list[dict[str, str]]
    max_tokens: int
    temperature: float = 0.0
    anchors: tuple[str, ...] = ()
    role_indexes: tuple[int, ...] = ()  # positions in the anchored role list
    requirement_indexes: tuple[int, ...] = ()
    excerpt: str = ""  # the slice a skills/projects call reads (its deterministic fallback)


@dataclass(slots=True)
class RoleAnchor:
    index: int
    date_text: str  # the date range as written, e.g. "Jan 2023 – Present"
    bullets: int


@dataclass(slots=True)
class CallPlan:
    calls: list[PartCall] = field(default_factory=list)
    anchors: list[RoleAnchor] = field(default_factory=list)
    has_job: bool = False

    def by_section(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for call in self.calls:
            out.setdefault(call.section, []).append(call.key)
        return out


def _section_text(sections: list[Section], key: str) -> str:
    return "\n".join(s.text for s in sections if s.key == key).strip()


def role_anchors(experience: str) -> list[RoleAnchor]:
    """Date lines of the roles in the experience section (same detection as
    :func:`parse_roles`), with the bullet count of each role block."""
    lines = experience.split("\n")
    anchors: list[RoleAnchor] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not is_role_date_line(stripped):
            continue
        match = DATE_RANGE.search(stripped)
        if not match:  # pragma: no cover - is_role_date_line checked it
            continue
        anchors.append(RoleAnchor(index=index, date_text=match.group(0).strip(), bullets=0))
    for position, anchor in enumerate(anchors):
        end = anchors[position + 1].index if position + 1 < len(anchors) else len(lines)
        anchor.bullets = sum(1 for line in lines[anchor.index + 1 : end] if line.strip().startswith("- "))
    for position, anchor in enumerate(anchors):
        anchor.index = position
    return anchors[:25]


def _role_groups(anchors: list[RoleAnchor]) -> list[list[RoleAnchor]]:
    groups: list[list[RoleAnchor]] = []
    current: list[RoleAnchor] = []
    budget = 0
    for anchor in anchors:
        estimate = 60 + 45 * max(1, anchor.bullets)
        if current and (budget + estimate > ROLE_TOKENS_PER_CALL or len(current) >= MAX_ROLES_PER_CALL):
            groups.append(current)
            current, budget = [], 0
        current.append(anchor)
        budget += estimate
    if current:
        groups.append(current)
    return groups


def _skill_slices(skills_text: str) -> list[str]:
    """Whole lines of the skills section, at most ~``SKILL_ITEMS_PER_CALL`` items per slice."""
    slices: list[list[str]] = []
    current: list[str] = []
    count = 0
    for line in [line for line in skills_text.split("\n") if line.strip()]:
        items = len(re.split(r"[,;|•·(]", line))
        if current and count + items > SKILL_ITEMS_PER_CALL:
            slices.append(current)
            current, count = [], 0
        current.append(line)
        count += items
    if current:
        slices.append(current)
    return ["\n".join(lines) for lines in slices]


def _is_title_line(line: str) -> bool:
    text = line.strip()
    return (
        bool(text)
        and not text.startswith("- ")
        and len(text) <= 80
        and not text.endswith(".")
        and text[0].isupper()
    )


def project_items(projects_text: str) -> list[str]:
    """Top-level project items: a title line with its bullets, or one bullet
    per project when the section is a plain bullet list."""
    lines = [line for line in projects_text.split("\n") if line.strip()]
    titled = any(
        _is_title_line(line) and index + 1 < len(lines) and lines[index + 1].startswith("- ")
        for index, line in enumerate(lines)
    )
    items: list[list[str]] = []
    for line in lines:
        starts = _is_title_line(line) if titled else line.startswith("- ")
        if starts or not items:
            items.append([line])
        else:
            items[-1].append(line)
    return ["\n".join(item) for item in items]


def _project_slices(projects_text: str) -> list[tuple[str, int]]:
    """(excerpt, item count) with at most ``PROJECTS_PER_CALL`` items each."""
    items = project_items(projects_text)
    return [
        ("\n".join(items[i : i + PROJECTS_PER_CALL]), len(items[i : i + PROJECTS_PER_CALL]))
        for i in range(0, len(items), PROJECTS_PER_CALL)
    ]


def _bounded(text: str, limit: int = MAX_RESUME_CHARS) -> tuple[str, bool]:
    return (text[:limit], len(text) > limit)


def _parser_messages(
    kind: str, heading: str, excerpt: str, task: str, schema: type[BaseModel]
) -> list[dict[str, str]]:
    body, truncated = _bounded(excerpt)
    return [
        {"role": "system", "content": PARSER_SYSTEM.render()},
        {
            "role": "user",
            "content": PARSER_USER.render(
                heading=heading,
                excerpt=body,
                truncated=truncated,
                limit=MAX_RESUME_CHARS,
                task=task,
                schema=schema_text(kind, schema),
            ),
        },
    ]


def _analyst_messages(
    kind: str,
    *,
    resume: str,
    job: dict[str, Any] | None,
    requirements: Sequence[RequirementInput],
    competency_keys: Sequence[str],
    facts: str | None,
    task: str,
    schema: type[BaseModel],
    description: bool,
) -> list[dict[str, str]]:
    body, truncated = _bounded(resume)
    has_job = job is not None
    return [
        {"role": "system", "content": ANALYST_SYSTEM.render(has_job=has_job)},
        {
            "role": "user",
            "content": ANALYST_USER.render(
                has_job=has_job,
                job=job or {},
                description=((job or {}).get("description") or "")[:JOB_DESCRIPTION_CHARS]
                if description
                else "",
                requirements_text=requirements_text(
                    [
                        {
                            "kind": r.kind,
                            "name": r.name,
                            "min_years": f"{r.min_years:g}" if r.min_years else None,
                            "description": (r.description or "")[:200] or None,
                        }
                        for r in requirements
                    ]
                ),
                competency_keys=list(competency_keys),
                facts=facts,
                resume=body,
                truncated=truncated,
                task=task,
                schema=schema_text(kind, schema),
            ),
        },
    ]


def plan_calls(
    text: str,
    sections: list[Section],
    *,
    job: dict[str, Any] | None,
    requirements: Sequence[RequirementInput],
    competency_keys: Sequence[str],
    facts: str,
) -> CallPlan:
    """The model calls for one resume (see module docstring)."""
    plan = CallPlan(has_job=job is not None)
    calls = plan.calls
    resume_heading = "Candidate resume"

    # identity --------------------------------------------------------------
    calls.append(
        PartCall(
            key="identity",
            kind="identity",
            section="basics",
            task=ModelTask.RESUME_EXTRACTION,
            schema=IdentityPart,
            messages=_parser_messages(
                "identity", resume_heading, text, PART_TASKS["identity"].render(), IdentityPart
            ),
            max_tokens=500,
        )
    )

    # roles -----------------------------------------------------------------
    experience = _section_text(sections, "experience")
    anchors = role_anchors(experience) if experience else []
    plan.anchors = anchors
    excerpt = experience or text
    heading = "Experience section of the resume" if experience else resume_heading
    if anchors:
        for number, group in enumerate(_role_groups(anchors)):
            dates = tuple(a.date_text for a in group)
            model = roles_model(len(group))
            calls.append(
                PartCall(
                    key=f"roles:{number}",
                    kind="roles",
                    section="experience",
                    task=ModelTask.RESUME_EXTRACTION,
                    schema=model,
                    messages=_parser_messages(
                        "roles",
                        heading,
                        excerpt,
                        PART_TASKS["roles"].render(anchors=[f"«{d}»" for d in dates]),
                        model,
                    ),
                    max_tokens=min(2000, 300 + sum(120 + 90 * max(1, a.bullets) for a in group)),
                    anchors=dates,
                    role_indexes=tuple(a.index for a in group),
                )
            )
        calls.append(
            PartCall(
                key="roles:rest",
                kind="roles",
                section="experience",
                task=ModelTask.RESUME_EXTRACTION,
                schema=RolesPart,
                messages=_parser_messages(
                    "roles",
                    heading,
                    excerpt,
                    PART_TASKS["roles_rest"].render(anchors=[f"«{a.date_text}»" for a in anchors]),
                    RolesPart,
                ),
                max_tokens=900,
            )
        )
    else:
        calls.append(
            PartCall(
                key="roles:all",
                kind="roles",
                section="experience",
                task=ModelTask.RESUME_EXTRACTION,
                schema=RolesPart,
                messages=_parser_messages(
                    "roles", heading, excerpt, PART_TASKS["roles_all"].render(), RolesPart
                ),
                max_tokens=3000,
            )
        )

    # projects --------------------------------------------------------------
    projects = _section_text(sections, "projects")
    if projects:
        for number, (chunk, items) in enumerate(_project_slices(projects)):
            calls.append(
                PartCall(
                    key=f"projects:{number}",
                    kind="projects",
                    section="projects",
                    task=ModelTask.RESUME_EXTRACTION,
                    schema=ProjectsPart,
                    messages=_parser_messages(
                        "projects",
                        "Projects section of the resume",
                        chunk,
                        PART_TASKS["projects"].render(),
                        ProjectsPart,
                    ),
                    max_tokens=min(1600, 200 + 200 * items),
                    excerpt=chunk,
                )
            )

    # skills ----------------------------------------------------------------
    skills = _section_text(sections, "skills")
    if skills:
        for number, chunk in enumerate(_skill_slices(skills)):
            items = len(re.split(r"[,;|•·(\n]", chunk))
            calls.append(
                PartCall(
                    key=f"skills:{number}",
                    kind="skills",
                    section="skills",
                    task=ModelTask.RESUME_EXTRACTION,
                    schema=SkillsPart,
                    messages=_parser_messages(
                        "skills",
                        "Skills section of the resume",
                        chunk,
                        PART_TASKS["skills"].render(),
                        SkillsPart,
                    ),
                    max_tokens=min(1500, 200 + 14 * items),
                    excerpt=chunk,
                )
            )

    # education + certifications ------------------------------------------
    background = "\n\n".join(
        part
        for part in (_section_text(sections, "education"), _section_text(sections, "certifications"))
        if part
    )
    if background:
        lines = sum(1 for line in background.split("\n") if line.strip())
        calls.append(
            PartCall(
                key="background",
                kind="background",
                section="education",
                task=ModelTask.RESUME_EXTRACTION,
                schema=BackgroundPart,
                messages=_parser_messages(
                    "background",
                    "Education and certification sections of the resume",
                    background,
                    PART_TASKS["background"].render(),
                    BackgroundPart,
                ),
                max_tokens=min(900, 160 + 60 * lines),
            )
        )

    # analysis --------------------------------------------------------------
    analyst = {"resume": text, "job": job, "competency_keys": competency_keys, "facts": facts}
    has_job = job is not None
    for group, (label, categories) in CLAIM_GROUPS.items():
        calls.append(
            PartCall(
                key=f"claims:{group}",
                kind="claims",
                section="interview",
                task=ModelTask.PROFILE_ANALYSIS,
                schema=ClaimsParts[group],
                messages=_analyst_messages(
                    "claims",
                    requirements=list(requirements),
                    task=PART_TASKS["claims"].render(
                        has_job=has_job,
                        label=label,
                        categories=" | ".join(categories),
                        limit=CLAIMS_PER_GROUP,
                    ),
                    schema=ClaimsParts[group],
                    description=True,
                    **analyst,
                ),
                max_tokens=800,
                temperature=0.1,
            )
        )
    if has_job:
        calls.append(
            PartCall(
                key="match",
                kind="match",
                section="interview",
                task=ModelTask.PROFILE_ANALYSIS,
                schema=MatchPart,
                messages=_analyst_messages(
                    "match",
                    requirements=list(requirements),
                    task=PART_TASKS["match"].render(),
                    schema=MatchPart,
                    description=True,
                    **analyst,
                ),
                max_tokens=500,
                temperature=0.1,
            )
        )
        for number, start in enumerate(range(0, len(requirements), REQUIREMENTS_PER_CALL)):
            group_reqs = list(requirements[start : start + REQUIREMENTS_PER_CALL])
            model = requirements_model(len(group_reqs))
            calls.append(
                PartCall(
                    key=f"reqs:{number}",
                    kind="reqs",
                    section="interview",
                    task=ModelTask.PROFILE_ANALYSIS,
                    schema=model,
                    messages=_analyst_messages(
                        "reqs",
                        requirements=group_reqs,
                        task=PART_TASKS["reqs"].render(count=len(group_reqs)),
                        schema=model,
                        description=False,
                        **{**analyst, "facts": None, "competency_keys": []},
                    ),
                    max_tokens=120 + 60 * len(group_reqs),
                    temperature=0.1,
                    requirement_indexes=tuple(range(start, start + len(group_reqs))),
                )
            )
    calls.append(
        PartCall(
            key="topics",
            kind="topics",
            section="interview",
            task=ModelTask.PROFILE_ANALYSIS,
            schema=TopicsPart,
            messages=_analyst_messages(
                "topics",
                requirements=list(requirements),
                task=PART_TASKS["topics"].render(has_job=has_job),
                schema=TopicsPart,
                description=False,
                **analyst,
            ),
            max_tokens=650,
            temperature=0.1,
        )
    )
    calls.append(
        PartCall(
            key="gaps",
            kind="gaps",
            section="interview",
            task=ModelTask.PROFILE_ANALYSIS,
            schema=GapsPart,
            messages=_analyst_messages(
                "gaps",
                requirements=list(requirements),
                task=PART_TASKS["gaps"].render(has_job=has_job),
                schema=GapsPart,
                description=False,
                **{**analyst, "facts": None},
            ),
            max_tokens=500,
            temperature=0.1,
        )
    )
    return plan


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

_SPACES = re.compile(r"\s+")


def _norm(value: str | None) -> str:
    return _SPACES.sub(" ", re.sub(r"[^a-z0-9%+#./ ]+", " ", (value or "").lower())).strip()


_STOP = frozenset(
    "a an and or of the to in on for with by at from as is was were be been it its this that their his her our "
    "via using use used into over across per than then".split()
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9%+#.]*")


_IRREGULAR = {
    "led": "lead",
    "leading": "lead",
    "leads": "lead",
    "built": "build",
    "ran": "run",
    "grew": "grow",
    "cut": "cut",
    "managed": "manage",
    "managing": "manage",
    "designed": "design",
    "reduced": "reduce",
}


def _content_words(text: str) -> set[str]:
    words = set()
    for token in _TOKEN.findall((text or "").lower()):
        token = token.rstrip(".")
        if not token or token in _STOP:
            continue
        token = _IRREGULAR.get(token, token)
        words.add(
            token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss") else token
        )
    return words


def resume_statements(text: str) -> list[str]:
    """Bullets, lines and long-line sentences of the resume that can serve as a
    verbatim quote - not the header block, role title/date lines or contact lines."""
    lines = [raw.strip() for raw in text.split("\n")]
    skip: set[int] = set(range(min(3, len(lines))))
    for index, line in enumerate(lines):
        if line and not line.startswith("- ") and DATE_RANGE.search(line):
            skip.add(index)
            previous = next((j for j in range(index - 1, -1, -1) if lines[j]), None)
            if previous is not None and not lines[previous].startswith("- "):
                skip.add(previous)
    out: list[str] = []
    for index, raw in enumerate(lines):
        line = raw[2:].strip() if raw.startswith("- ") else raw
        if index in skip or len(line) < 20 or "@" in line:
            continue
        out.append(line)
        if len(line) > 220:
            out.extend(s.strip() for s in re.split(r"(?<=[.;!?])\s+", line) if len(s.strip()) >= 20)
    return out


def best_quote(claim: str, statements: Sequence[str]) -> str | None:
    """The resume statement that best supports ``claim`` (verbatim), or None:
    recall-weighted F-score of the content words (recall = share of the claim's
    words found in the statement)."""
    target = _content_words(claim)
    if not target or not statements:
        return None
    best: tuple[float, float, str] | None = None
    for statement in statements:
        words = _content_words(statement)
        common = len(target & words)
        if not common:
            continue
        recall = common / len(target)
        precision = common / len(words)
        f2 = 5 * recall * precision / (4 * precision + recall)  # recall-weighted
        tie = fuzz.ratio(claim.lower(), statement.lower()) / 100
        if best is None or (f2, tie) > (best[0], best[1]):
            best = (f2, tie, statement)
    if best is None or best[0] < QUOTE_MIN_F1:
        return None
    return best[2][:400]


def skills_from_part(value: BaseModel) -> list[SkillItem]:
    data = value.model_dump(mode="python")
    levels = {str(item["name"]).strip().lower(): item["level"] for item in data.get("levels") or []}
    details = {str(item["name"]).strip().lower(): item for item in data.get("details") or []}
    out: list[SkillItem] = []
    for category, names in (data.get("by_category") or {}).items():
        for raw in names or []:
            name = str(raw).strip()[:80]
            if not name:
                continue
            key = name.lower()
            detail = details.get(key) or {}
            evidence = detail.get("evidence")
            out.append(
                SkillItem(
                    name=name,
                    category=category_for(name, category),
                    claimed_level=levels.get(key, "unspecified"),
                    years=detail.get("years"),
                    last_used_year=detail.get("last_used_year"),
                    evidence=evidence[:300] if isinstance(evidence, str) and evidence.strip() else None,
                )
            )
    return out


def _short(value: Any, limit: int = EVIDENCE_MAX_CHARS) -> str | None:
    """Model evidence phrase, cut at a word boundary when the decoder hit the length cap."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    if len(text) < limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.-")
    return f"{cut}\u2026"


def skill_labels(excerpt: str) -> set[str]:
    """Line labels of a skills excerpt ("Cloud & IaC: AWS, ...") - category
    names, not skills."""
    labels: set[str] = set()
    for line in excerpt.split("\n"):
        body = line.strip()[2:] if line.strip().startswith("- ") else line.strip()
        if ":" in body:
            label = body.split(":", 1)[0].strip()
            if 0 < len(label) <= 40:
                labels.add(_norm(label))
    return labels


def _role_key(role: RoleEntry) -> str:
    return f"{_norm(role.title)}|{_norm(role.company)}"


def _richness(role: RoleEntry) -> int:
    return len(role.responsibilities) + len(role.achievements) + len(role.technologies)


def _add_role(roles: list[RoleEntry], role: RoleEntry) -> None:
    """Append ``role`` unless the same role (title + company + dates) is there;
    a duplicate replaces the kept one when it carries more detail."""
    key = (_role_key(role), role_range(role))
    for index, existing in enumerate(roles):
        if (_role_key(existing), role_range(existing)) == key and key[0] != "|":
            if _richness(role) > _richness(existing):
                roles[index] = role
            return
    roles.append(role)


def _iso(value: str | None) -> str | None:
    return None if value is None else to_iso_date(value)


def anchor_range(date_text: str) -> tuple[str | None, str | None]:
    """(start, end) of an anchor date line in the role date format."""
    match = DATE_RANGE.search(date_text)
    if not match:
        return (None, None)
    return (_iso(match.group("start")), _iso(match.group("end")))


def role_range(role: RoleEntry) -> tuple[str | None, str | None]:
    return (_iso(role.start), _iso(role.end))


def _year(value: str | None) -> str | None:
    found = re.search(r"(19|20)\d{2}", value or "")
    return found.group(0) if found else None


def _anchored_roles(
    items: list[RoleEntry],
    call: PartCall,
    anchor_dates: list[tuple[str | None, str | None]],
    quick_roles: list[RoleEntry],
    fallbacks: dict[str, str],
) -> list[RoleEntry]:
    """The roles of an anchored call in anchor order: the model role whose dates
    match each anchor, else the role at the same position when its start year
    agrees (the model may write "2023" for "Jan 2023"), else the deterministic
    role for that anchor (recorded as a fallback)."""
    out: list[RoleEntry] = []
    unused = list(items)
    for position, index in enumerate(call.role_indexes):
        wanted = anchor_dates[index] if index < len(anchor_dates) else (None, None)
        match = next((r for r in unused if wanted != (None, None) and role_range(r) == wanted), None)
        if match is None and position < len(items) and items[position] in unused:
            candidate = items[position]
            if _year(candidate.start) == _year(wanted[0]):
                match = candidate
        if match is not None:
            unused.remove(match)
            out.append(match)
        elif index < len(quick_roles):
            fallbacks.setdefault(f"{call.key}#{index}", "role_mismatch")
            out.append(quick_roles[index])
    return out


@dataclass(slots=True)
class MergeResult:
    extraction: ResumeExtraction
    analysis: ProfileAnalysis
    model_match: JobMatch | None
    degraded: list[str] = field(default_factory=list)
    fallbacks: dict[str, str] = field(default_factory=dict)  # call key -> reason


def merge_parts(
    plan: CallPlan,
    results: dict[str, Any],
    *,
    quick: ResumeExtraction,
    quick_roles_by_anchor: list[RoleEntry],
    text: str,
    requirements: Sequence[RequirementInput],
    known_competency_keys: set[str],
    extra_skills: Sequence[SkillItem] = (),
) -> MergeResult:
    """Deterministic merge of the part results (``results[key]`` is the part's
    wire value, or an exception / None when the call failed)."""
    fallbacks: dict[str, str] = {}

    def ok(key: str) -> BaseModel | None:
        value = results.get(key)
        if isinstance(value, BaseModel):
            return value
        fallbacks[key] = type(value).__name__ if value is not None else "missing"
        return None

    def calls_of(kind: str) -> list[PartCall]:
        return [c for c in plan.calls if c.kind == kind]

    # identity
    identity = ok("identity")
    if identity is not None:
        data = identity.model_dump(mode="python")
        name = data.get("candidate_name") or quick.candidate_name
        headline = data.get("headline") or quick.headline
        total = data.get("total_experience_years")
        leadership = [s for s in data.get("leadership_experience") or [] if s][:10]
        career = data.get("career_progression")
        inconsistencies = [to_contract(i, Inconsistency) for i in identity.inconsistencies]  # type: ignore[attr-defined]
    else:
        name, headline, total = quick.candidate_name, quick.headline, quick.total_experience_years
        leadership, career, inconsistencies = [], None, []

    # roles
    roles: list[RoleEntry] = []
    anchor_dates = [anchor_range(a.date_text) for a in plan.anchors]
    role_calls = sorted(calls_of("roles"), key=lambda c: c.key == "roles:rest")  # anchored roles first
    for call in role_calls:
        part = ok(call.key)
        if part is not None:
            items = [to_contract(r, RoleEntry) for r in part.roles]  # type: ignore[attr-defined]
            if call.role_indexes:
                items = _anchored_roles(items, call, anchor_dates, quick_roles_by_anchor, fallbacks)
            elif call.key == "roles:rest":
                # The rest call must not repeat an anchored role (dates written differently included).
                known = {_role_key(r) for r in roles}
                items = [r for r in items if role_range(r) not in anchor_dates and _role_key(r) not in known]
        elif call.role_indexes:
            items = [quick_roles_by_anchor[i] for i in call.role_indexes if i < len(quick_roles_by_anchor)]
        elif call.key == "roles:all":
            items = list(quick.roles)
        else:
            items = []
        for role in items:
            _add_role(roles, role)

    # projects
    projects: list[ProjectEntry] = []
    for call in calls_of("projects"):
        part = ok(call.key)
        if part is not None:
            projects.extend(to_contract(p, ProjectEntry) for p in part.projects)  # type: ignore[attr-defined]
        else:
            projects.extend(parse_projects(call.excerpt))

    # skills
    skills: list[SkillItem] = []
    for call in calls_of("skills"):
        part = ok(call.key)
        if part is None:
            skills.extend(parse_skill_items(call.excerpt))
            continue
        labels = skill_labels(call.excerpt)
        skills.extend(s for s in skills_from_part(part) if _norm(s.name) not in labels)
    skills = merge_skills([*skills, *extra_skills])

    # education + certifications
    education, certifications = list(quick.education), list(quick.certifications)
    if calls_of("background"):
        part = ok("background")
        if part is not None:
            education = [to_contract(e, EducationEntry) for e in part.education]  # type: ignore[attr-defined]
            certifications = [to_contract(c, CertificationEntry) for c in part.certifications]  # type: ignore[attr-defined]

    extraction = ResumeExtraction(
        candidate_name=name,
        headline=headline,
        summary=quick.summary,
        total_experience_years=total,
        roles=roles[:25],
        projects=projects[:25],
        education=education[:10],
        certifications=certifications[:20],
        skills=skills[:120],
        leadership_experience=leadership[:10],
        career_progression=career,
        inconsistencies=inconsistencies[:10],
    )

    # claims
    statements = resume_statements(text)
    claims: list[ClaimToVerify] = []
    claim_failed = False
    for call in calls_of("claims"):
        part = ok(call.key)
        if part is None:
            claim_failed = True
            continue
        for item in part.claims:  # type: ignore[attr-defined]
            data = item.model_dump(mode="python")
            claims.append(
                ClaimToVerify(
                    id=f"c{len(claims) + 1}",
                    claim=str(data["claim"])[:400],
                    source_quote=best_quote(str(data["claim"]), statements),
                    category=str(data["category"]),
                    competency_keys=[str(k) for k in data.get("competency_keys") or []][:5],
                    evidence_topics=[
                        str(t)[:120]
                        for t in data.get("evidence_topics") or []
                        if not _PROXY_TOPIC.search(str(t))
                    ][:8],
                    priority=int(data.get("priority") or 3),
                )
            )
    if claim_failed and not claims:
        claims = list(heuristic_analysis(extraction, has_job=plan.has_job).claims_to_verify)
    claims = dedupe_claims(claims)

    # job fit (model view)
    model_match: JobMatch | None = None
    if plan.has_job:
        overall = ok("match")
        assessed: list[RequirementMatch] = []
        for call in calls_of("reqs"):
            part = ok(call.key)
            if part is None:
                continue
            entries = list(part.matched_requirements)  # type: ignore[attr-defined]
            numbers = [int(e.n) for e in entries]
            if sorted(numbers) == list(
                range(1, len(entries) + 1)
            ):  # numbered consistently: trust the numbers
                entries = sorted(entries, key=lambda e: int(e.n))
            for position, entry in zip(call.requirement_indexes, entries, strict=False):
                data = entry.model_dump(mode="python")
                if position >= len(requirements):
                    continue
                requirement = requirements[position].name
                assessed.append(
                    RequirementMatch(
                        requirement=requirement[:200],
                        evidence=_short(data.get("evidence")),
                        strength=str(data["strength"]),
                    )
                )
        if overall is not None:
            data = overall.model_dump(mode="python")
            strength_by_name = {m.requirement: m.strength for m in assessed}
            # The model found no evidence at all: must-haves first, then the others.
            missing = [
                r.name[:200]
                for r in sorted(requirements, key=lambda r: r.kind not in MUST_HAVE_KINDS)
                if strength_by_name.get(r.name[:200]) == "none"
            ]
            model_match = JobMatch(
                overall_fit=float(data["overall_fit"]),
                relevant_experience_years=data.get("relevant_experience_years"),
                matched_requirements=assessed[:40],
                missing_requirements=missing[:20],
                strengths=[s for s in data.get("strengths") or [] if s][:10],
                gaps=[s for s in data.get("gaps") or [] if s][:10],
                notes=data.get("notes"),
            )
        elif assessed:
            # Fit unavailable: keep the per-requirement view; the blend treats the
            # missing model score as absent (weights redistributed).
            model_match = None

    topics_part = ok("topics")
    gaps_part = ok("gaps")
    gaps = gaps_part.model_dump(mode="python") if gaps_part is not None else {}
    analysis = ProfileAnalysis(
        claims_to_verify=claims,
        job_match=model_match or JobMatch(overall_fit=0.0),
        recommended_topics=[to_contract(t, RecommendedTopic) for t in topics_part.recommended_topics]  # type: ignore[attr-defined]
        if topics_part is not None
        else [],
        potential_skill_gaps=list(gaps.get("potential_skill_gaps") or [])[:15],
        areas_requiring_verification=list(gaps.get("areas_requiring_verification") or [])[:10],
        job_relevant_strengths=list(gaps.get("job_relevant_strengths") or [])[:10],
        job_relevant_missing_areas=list(gaps.get("job_relevant_missing_areas") or [])[:10],
    )
    failed_calls = {key.split("#", 1)[0] for key in fallbacks}
    degraded = sorted({call.section for call in plan.calls if call.key in failed_calls})
    return MergeResult(
        extraction=extraction,
        analysis=analysis,
        model_match=model_match,
        degraded=degraded,
        fallbacks=fallbacks,
    )


def dedupe_claims(claims: list[ClaimToVerify]) -> list[ClaimToVerify]:
    """Drop near-duplicate claims (the two claim groups may pick the same
    statement): same normalised text, same source quote, or very similar text.
    Higher priority wins; order is priority, then original order."""
    ordered = sorted(enumerate(claims), key=lambda pair: (-pair[1].priority, pair[0]))
    kept: list[ClaimToVerify] = []
    for _, claim in ordered:
        text = _norm(claim.claim)
        duplicate = any(
            text == _norm(other.claim)
            or (
                claim.source_quote
                and claim.source_quote == other.source_quote
                and fuzz.token_set_ratio(text, _norm(other.claim)) >= 70
            )
            or fuzz.token_set_ratio(text, _norm(other.claim)) >= 90
            for other in kept
        )
        if not duplicate:
            kept.append(claim)
    return kept


def quick_roles_for_anchors(experience: str, anchors: list[RoleAnchor]) -> list[RoleEntry]:
    """Deterministic roles in anchor order (fallback of a failed role call)."""
    roles = parse_roles(experience)
    return roles if len(roles) == len(anchors) else roles[: len(anchors)]


def cache_value(value: BaseModel) -> dict[str, Any]:
    return copy.deepcopy(value.model_dump(mode="json"))
