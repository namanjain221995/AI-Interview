"""``profile.generate``: resume text -> versioned ``CandidateProfile``.

1. The deterministic quick profile (:mod:`app.services.resume.quick`) is the
   base; it is already visible (``candidate_profiles`` row, status ``quick``).
2. Small model calls run CONCURRENTLY (:mod:`app.services.resume.calls`:
   identity, one call per role group, projects, skills slices, education and
   certifications, four claim groups, the job fit, requirement groups, topics,
   gaps). As each call finishes, its result is cached on the resume
   (``scan_result.ai_parts``, so a job retry or an identical re-upload only runs
   what is missing) and, when it completes a section, the quick row is refined
   in place and ``progress.sections`` says which sections the model confirmed.
   Meanwhile the resume chunks are embedded and the semantic requirement
   evidence (pgvector + reranker) is computed.
3. Deterministic post-processing: experience years from merged role
   intervals (cross-checked with the stated total; overlaps/gaps become
   neutral inconsistencies), skills de-duplicated and grouped by category,
   companies, flattened achievements, unique claim ids, verbatim claim quotes.
4. Job match: requirement evidence (semantic + lexical) blended with the
   model's job fit (formula in :mod:`app.services.resume.matching`).
5. Persist ``candidate_profiles`` (version = previous max + 1 for the
   candidate, status ``ready``; ``partial`` when any part fell back to the
   deterministic parse on the job's last attempt) and mark the resume ``ready``.

A retryable model failure before the job's last attempt lets the other calls
finish (their results are cached) and then re-raises, so the queue retries the
job and only the missing parts run again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, or_, select, text

from app.ai.base import AIError, AIServices, CallContext, StructuredResult
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.db.models import CandidateProfile, CandidateResume, Competency, Job, JobRequirement, ResumeChunk
from app.db.session import session_scope
from app.domain.enums import ModelTask, ResumeStatus
from app.schemas.ai import (
    Achievement,
    ClaimToVerify,
    Inconsistency,
    JobMatch,
    ProfileAnalysis,
    ResumeExtraction,
)
from app.schemas.ai import (
    CandidateProfile as CandidateProfileDocument,
)
from app.services.resume import progress as progress_mod
from app.services.resume.calls import (
    PIPELINE_VERSION,
    CallPlan,
    PartCall,
    merge_parts,
    plan_calls,
    quick_roles_for_anchors,
)
from app.services.resume.experience import compute_experience, experience_inconsistencies
from app.services.resume.matching import (
    RequirementInput,
    SemanticHit,
    blend_job_match,
    combine_evidence,
    semantic_evidence,
)
from app.services.resume.normalize import chunk_sections, detect_sections
from app.services.resume.prompts import PART_PROMPT_VERSIONS
from app.services.resume.quick import QUICK_STATUS, SECTION_LABELS, build_quick_profile, initial_sections
from app.services.resume.skills import (
    find_vocabulary_skills,
    group_skills,
    merge_skills,
    skills_from_projects,
    skills_from_roles,
)
from app.services.retrieval import embed_documents

log = get_logger(__name__)

PART_TIMEOUT_SECONDS = 90.0  # per call (the calls run concurrently; 2 attempts each)
PROFILE_STATUS_READY = "ready"
PROFILE_STATUS_PARTIAL = "partial"
MAX_CLAIMS = 15
MAX_CHUNKS = 200
# Kinds whose cached results stay valid for another job (they read only the resume).
RESUME_ONLY_KINDS = frozenset({"identity", "roles", "projects", "skills", "background"})


class ProfileInputError(Exception):
    """The inputs of the job are unusable (permanent)."""

    retryable = False


@dataclass(slots=True)
class ProfileOutcome:
    profile_id: uuid.UUID
    version: int
    status: str
    job_match_score: float | None
    experience_years: float | None
    claims: int
    timings_ms: dict[str, float] = field(default_factory=dict)
    breakdown: dict[str, Any] | None = None
    degraded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": str(self.profile_id),
            "version": self.version,
            "status": self.status,
            "job_match_score": self.job_match_score,
            "experience_years": self.experience_years,
            "claims": self.claims,
            "timings_ms": {k: round(v, 1) for k, v in self.timings_ms.items()},
            "match": self.breakdown,
            "degraded": self.degraded,
        }


@dataclass(slots=True)
class _Inputs:
    candidate_id: uuid.UUID
    resume_id: uuid.UUID
    organization_id: uuid.UUID
    text: str
    job: dict[str, Any] | None
    job_id: uuid.UUID | None
    requirements: list[RequirementInput]
    competency_keys: list[str]
    scan_result: dict[str, Any] = field(default_factory=dict)


async def _load_inputs(candidate_id: uuid.UUID, resume_id: uuid.UUID, job_id: uuid.UUID | None) -> _Inputs:
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id)
        if resume is None or resume.candidate_id != candidate_id:
            raise ProfileInputError("resume not found for candidate")
        text = resume.normalized_text or resume.extracted_text or ""
        if not text.strip():
            raise ProfileInputError("resume has no extracted text")
        job_doc: dict[str, Any] | None = None
        requirements: list[RequirementInput] = []
        if job_id is not None:
            job = await db.get(Job, job_id)
            if job is None or job.organization_id != resume.organization_id:
                log.warning("profile.job_ignored", job_id=str(job_id), reason="not found in organization")
                job_id = None
            else:
                experience = None
                if job.experience_min_years or job.experience_max_years:
                    lo = f"{job.experience_min_years:g}" if job.experience_min_years else "0"
                    hi = f"-{job.experience_max_years:g}" if job.experience_max_years else "+"
                    experience = f"{lo}{hi} years"
                job_doc = {
                    "title": job.title,
                    "seniority": job.seniority,
                    "experience": experience,
                    "description": (job.description or "")[:6000],
                    "experience_min_years": job.experience_min_years,
                }
                rows = (
                    await db.scalars(
                        select(JobRequirement)
                        .where(JobRequirement.job_id == job.id)
                        .order_by(JobRequirement.sort_order, JobRequirement.created_at)
                    )
                ).all()
                requirements = [
                    RequirementInput(
                        id=str(r.id),
                        name=r.name,
                        kind=r.kind,
                        description=r.description,
                        weight=r.weight,
                        min_years=r.min_years,
                        competency_key=r.competency_key,
                    )
                    for r in rows
                ]
        keys = (
            await db.scalars(
                select(Competency.key)
                .where(
                    Competency.is_active.is_(True),
                    or_(
                        Competency.organization_id.is_(None),
                        Competency.organization_id == resume.organization_id,
                    ),
                )
                .order_by(Competency.key)
            )
        ).all()
        return _Inputs(
            candidate_id=candidate_id,
            resume_id=resume_id,
            organization_id=resume.organization_id,
            text=text,
            job=job_doc,
            job_id=job_id,
            requirements=requirements,
            competency_keys=sorted(set(keys)),
            scan_result=dict(resume.scan_result or {}),
        )


def _clean_claims(claims: list[ClaimToVerify], known_keys: set[str]) -> list[ClaimToVerify]:
    """Unique ids c1..cN, known competency keys only, priority order kept."""
    seen_text: set[str] = set()
    out: list[ClaimToVerify] = []
    for claim in sorted(claims, key=lambda c: -c.priority):
        text = " ".join(claim.claim.split()).lower()
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        keys = [k.strip().lower() for k in claim.competency_keys if k and k.strip()]
        if known_keys:
            keys = [k for k in keys if k in known_keys]
        out.append(claim.model_copy(update={"id": f"c{len(out) + 1}", "competency_keys": keys[:5]}))
        if len(out) >= MAX_CLAIMS:
            break
    return out


def _flatten_achievements(extraction: ResumeExtraction) -> list[Achievement]:
    seen: set[str] = set()
    out: list[Achievement] = []
    for role in extraction.roles:
        for achievement in role.achievements:
            key = " ".join(achievement.text.split()).lower()
            if key and key not in seen:
                seen.add(key)
                out.append(achievement)
    for project in extraction.projects:
        for outcome in project.outcomes:
            key = " ".join(outcome.split()).lower()
            if key and key not in seen:
                seen.add(key)
                out.append(Achievement(text=outcome[:400], measurable=any(ch.isdigit() for ch in outcome)))
    return out


def _companies(extraction: ResumeExtraction) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for role in extraction.roles:
        name = role.company.strip()
        if name and name.lower() not in seen and name.lower() != "unknown":
            seen.add(name.lower())
            out.append(name)
    return out


_DATE_ARITHMETIC = re.compile(
    r"\b(gaps?|overlap\w*|spans?|spanning|present|employment dates|date range|timeline|tenure)\b",
    re.IGNORECASE,
)


def _date_arithmetic(item: Inconsistency) -> bool:
    """Gaps, overlaps and stated totals versus the dates are computed from the
    role dates by :func:`experience_inconsistencies` (with neutral wording);
    the model's own date arithmetic is unreliable - its "present" is its
    training cutoff (live 2026-10-07: "5.8 years ... but dates span Jan 2021 to
    present (approx 4+ years)") - so such model findings are dropped."""
    return bool(_DATE_ARITHMETIC.search(item.description))


def _dedupe_inconsistencies(items: list[Inconsistency]) -> list[Inconsistency]:
    seen: set[str] = set()
    out: list[Inconsistency] = []
    for item in items:
        key = item.description.strip().lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_profile_document(
    *,
    candidate_id: uuid.UUID,
    extraction: ResumeExtraction,
    analysis: ProfileAnalysis,
    job_match: JobMatch | None,
    known_competency_keys: set[str],
    today: Any = None,
) -> tuple[CandidateProfileDocument, dict[str, Any]]:
    """Deterministic assembly of the stored profile (pure; unit-tested)."""
    computation = compute_experience(extraction.roles, today=today)
    computed_years = computation.years
    experience_years = computed_years if computed_years is not None else extraction.total_experience_years
    model_inconsistencies = [i for i in extraction.inconsistencies if not _date_arithmetic(i)]
    inconsistencies = _dedupe_inconsistencies(
        experience_inconsistencies(computation, extraction.total_experience_years) + model_inconsistencies
    )[:20]
    skills = group_skills(
        merge_skills(
            [
                *extraction.skills,
                *skills_from_roles(extraction.roles),
                *skills_from_projects(extraction.projects),
            ]
        )
    )
    relevant = job_match.relevant_experience_years if job_match else None
    if relevant is not None and experience_years is not None:
        relevant = min(relevant, experience_years)
    summary = extraction.summary or extraction.headline
    document = CandidateProfileDocument(
        candidate_id=str(candidate_id),
        candidate_name=extraction.candidate_name,
        headline=extraction.headline,
        summary=summary,
        experience_years=experience_years,
        relevant_experience_years=relevant,
        roles=extraction.roles,
        companies=_companies(extraction),
        skills=skills,
        projects=extraction.projects,
        achievements=_flatten_achievements(extraction),
        education=extraction.education,
        certifications=extraction.certifications,
        leadership_experience=extraction.leadership_experience,
        career_progression=extraction.career_progression,
        inconsistencies=inconsistencies,
        claims_to_verify=_clean_claims(list(analysis.claims_to_verify), known_competency_keys),
        job_match=job_match,
        recommended_topics=analysis.recommended_topics,
        potential_skill_gaps=analysis.potential_skill_gaps,
        areas_requiring_verification=analysis.areas_requiring_verification,
        job_relevant_strengths=analysis.job_relevant_strengths,
        job_relevant_missing_areas=analysis.job_relevant_missing_areas,
    )
    facts = {
        "experience": computation.as_dict(),
        "stated_total_years": extraction.total_experience_years,
        "experience_source": "dated_roles" if computed_years is not None else "stated_total",
    }
    return document, facts


def _facts_text(extraction: ResumeExtraction) -> str:
    computation = compute_experience(extraction.roles)
    parts = []
    if computation.years is not None:
        parts.append(f"{computation.years:g} years across dated roles after merging overlaps")
    if extraction.total_experience_years is not None:
        parts.append(f"stated total {extraction.total_experience_years:g} years")
    if computation.overlaps:
        parts.append(f"{len(computation.overlaps)} overlapping role period(s)")
    return "; ".join(parts) or "no dated roles"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def analysis_key(inputs: _Inputs) -> str:
    """Everything the job-aware parts read besides the resume text."""
    payload = {
        "job_id": str(inputs.job_id) if inputs.job_id else None,
        "job": inputs.job,
        "requirements": [
            [r.name, r.kind, r.description, r.min_years, r.weight, r.competency_key]
            for r in inputs.requirements
        ],
        "competency_keys": inputs.competency_keys,
    }
    return _sha(json.dumps(payload, sort_keys=True, default=str))


def cached_parts(
    scan_result: dict[str, Any], plan: CallPlan, *, text_sha: str, analysis_sha: str
) -> dict[str, BaseModel]:
    """Valid cached part results (same pipeline version and text; job-aware parts
    also need the same job inputs) parsed with the part's schema."""
    cache = scan_result.get("ai_parts")
    if (
        not isinstance(cache, dict)
        or cache.get("version") != PIPELINE_VERSION
        or cache.get("text_sha") != text_sha
    ):
        return {}
    entries = cache.get("parts") if isinstance(cache.get("parts"), dict) else {}
    out: dict[str, BaseModel] = {}
    for call in plan.calls:
        entry = entries.get(call.key)
        if not isinstance(entry, dict):
            continue
        if call.kind not in RESUME_ONLY_KINDS and entry.get("analysis_sha") != analysis_sha:
            continue
        try:
            out[call.key] = call.schema.model_validate(entry.get("data"))
        except ValidationError:
            continue
    return out


@dataclass(slots=True)
class _Run:
    """State of one profile generation."""

    inputs: _Inputs
    plan: CallPlan
    quick: Any  # QuickProfile
    quick_roles: list[Any]
    vocabulary: list[Any]
    text_sha: str
    analysis_sha: str
    results: dict[str, Any] = field(default_factory=dict)
    final_failures: set[str] = field(default_factory=set)
    model_call_ids: list[str] = field(default_factory=list)
    durations_ms: dict[str, float] = field(default_factory=dict)
    cached: set[str] = field(default_factory=set)

    def merged(self) -> Any:
        return merge_parts(
            self.plan,
            self.results,
            quick=self.quick.extraction,
            quick_roles_by_anchor=self.quick_roles,
            text=self.inputs.text,
            requirements=self.inputs.requirements,
            known_competency_keys=set(self.inputs.competency_keys),
            extra_skills=self.vocabulary,
        )

    def section_state(self, section: str) -> str:
        keys = [c.key for c in self.plan.calls if c.section == section]
        if not keys:
            return "done"
        if any(k in self.final_failures for k in keys):
            return "fallback" if all(k in self.results for k in keys) else "active"
        if all(isinstance(self.results.get(k), BaseModel) for k in keys):
            return "done"
        return "active"

    def percent(self) -> int:
        total = len(self.plan.calls) or 1
        finished = sum(1 for c in self.plan.calls if c.key in self.results)
        return 60 + int(38 * finished / total)


def _progressive_document(run: _Run) -> dict[str, Any]:
    """The quick row refined with every model section that has finished."""
    merged = run.merged()
    document, _ = build_profile_document(
        candidate_id=run.inputs.candidate_id,
        extraction=merged.extraction,
        analysis=merged.analysis,
        job_match=None,
        known_competency_keys=set(run.inputs.competency_keys),
    )
    return document.model_dump(mode="json")


async def _save_progress(run: _Run, *, call: PartCall | None, value: BaseModel | None) -> None:
    """Cache a finished part and, when it completes a section, refine the quick
    row and the section state (one short transaction)."""
    section_done = call is not None and run.section_state(call.section) in ("done", "fallback")
    document = _progressive_document(run) if section_done else None
    async with session_scope() as db:
        resume = await db.get(CandidateResume, run.inputs.resume_id, with_for_update=True)
        if resume is None or resume.status not in (ResumeStatus.ANALYZING.value, ResumeStatus.READY.value):
            return
        scan = dict(resume.scan_result or {})
        cache = scan.get("ai_parts") if isinstance(scan.get("ai_parts"), dict) else {}
        if cache.get("version") != PIPELINE_VERSION or cache.get("text_sha") != run.text_sha:
            cache = {"version": PIPELINE_VERSION, "text_sha": run.text_sha, "parts": {}}
        if call is not None and value is not None:
            parts = dict(cache.get("parts") or {})
            parts[call.key] = {"data": value.model_dump(mode="json"), "analysis_sha": run.analysis_sha}
            cache = {**cache, "parts": parts}
        scan["ai_parts"] = cache
        resume.scan_result = scan
        progress = progress_mod.set_percent(resume.progress, run.percent())
        if call is not None and section_done:
            progress = progress_mod.set_section_state(progress, call.section, run.section_state(call.section))
        resume.progress = progress
        if document is not None:
            row = await db.scalar(
                select(CandidateProfile)
                .where(
                    CandidateProfile.resume_id == run.inputs.resume_id,
                    CandidateProfile.status == QUICK_STATUS,
                )
                .order_by(CandidateProfile.version.desc())
                .limit(1)
                .with_for_update()
            )
            if row is not None:
                row.profile = document
                row.summary = document.get("summary") or document.get("headline")
                row.experience_years = document.get("experience_years")
                row.claims_count = len(document.get("claims_to_verify") or [])


async def _begin_sections(run: _Run) -> None:
    async with session_scope() as db:
        resume = await db.get(CandidateResume, run.inputs.resume_id, with_for_update=True)
        if resume is None:
            return
        progress = dict(resume.progress or {})
        if not progress.get("sections"):
            progress = progress_mod.with_sections(progress, initial_sections())
        for key in SECTION_LABELS:
            progress = progress_mod.set_section_state(progress, key, run.section_state(key))
        resume.progress = progress_mod.set_percent(progress, run.percent())


async def _index_chunks(run: _Run, sections: list[Any], services: AIServices) -> bool:
    """Embed the resume chunks (skipped when the stored chunks belong to this
    text). Returns False when the embeddings are unavailable."""
    if run.inputs.scan_result.get("chunks_text_sha") == run.text_sha:
        async with session_scope() as db:
            existing = await db.scalar(
                select(func.count())
                .select_from(ResumeChunk)
                .where(ResumeChunk.resume_id == run.inputs.resume_id)
            )
        if existing:
            return True
    chunks = chunk_sections(sections)[:MAX_CHUNKS]
    vectors = await embed_documents(
        services.embeddings,
        [chunk.text for chunk in chunks],
        context=CallContext(task=ModelTask.EMBEDDING, organization_id=str(run.inputs.organization_id)),
    )
    async with session_scope() as db:
        resume = await db.get(CandidateResume, run.inputs.resume_id, with_for_update=True)
        if resume is None:
            return False
        await db.execute(delete(ResumeChunk).where(ResumeChunk.resume_id == run.inputs.resume_id))
        db.add_all(
            ResumeChunk(
                resume_id=run.inputs.resume_id,
                candidate_id=run.inputs.candidate_id,
                chunk_index=chunk.index,
                section=chunk.section,
                text=chunk.text,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        resume.scan_result = {
            **(resume.scan_result or {}),
            "chunks": len(chunks),
            "chunks_text_sha": run.text_sha,
        }
    return True


async def _evidence_task(
    run: _Run, sections: list[Any], services: AIServices, *, last_attempt: bool
) -> list[SemanticHit] | None:
    """Chunks + semantic requirement evidence (runs beside the model calls)."""
    try:
        indexed = await _index_chunks(run, sections, services)
    except AIError as exc:
        if exc.retryable and not last_attempt:
            raise
        log.warning(
            "profile.chunks_unavailable", resume_id=str(run.inputs.resume_id), error=type(exc).__name__
        )
        return None
    if not indexed or not run.inputs.requirements:
        return None
    async with session_scope() as db:
        return await semantic_evidence(
            db,
            resume_id=run.inputs.resume_id,
            requirements=run.inputs.requirements,
            ai=services,
            organization_id=str(run.inputs.organization_id),
        )


async def _run_parts(run: _Run, services: AIServices, *, last_attempt: bool) -> None:
    """Run the uncached part calls concurrently; cache and publish each result
    as it lands. A retryable failure before the last attempt is re-raised
    after the other calls finished (their results stay cached)."""
    org = str(run.inputs.organization_id)
    pending = [call for call in run.plan.calls if call.key not in run.results]

    async def one(call: PartCall) -> tuple[PartCall, StructuredResult[Any] | AIError]:
        started = time.perf_counter()
        try:
            result = await services.llm.structured(
                call.schema,
                call.messages,
                context=CallContext(
                    task=call.task,
                    organization_id=org,
                    prompt_version=PART_PROMPT_VERSIONS.get(f"task.{call.kind}", PIPELINE_VERSION)[:40],
                ),
                temperature=call.temperature,
                max_tokens=call.max_tokens,
                timeout=PART_TIMEOUT_SECONDS,
                max_attempts=2,
            )
            return call, result
        except AIError as exc:
            return call, exc
        finally:
            run.durations_ms[call.key] = round((time.perf_counter() - started) * 1000, 1)

    tasks = [asyncio.ensure_future(one(call)) for call in pending]
    retry: AIError | None = None
    try:
        for finished in asyncio.as_completed(tasks):
            call, outcome = await finished
            if isinstance(outcome, AIError):
                run.results[call.key] = outcome
                final = last_attempt or not outcome.retryable
                log.warning(
                    "profile.part_failed",
                    resume_id=str(run.inputs.resume_id),
                    part=call.key,
                    error=type(outcome).__name__,
                    final=final,
                )
                if final:
                    run.final_failures.add(call.key)
                    await _save_progress(run, call=call, value=None)
                elif retry is None:
                    retry = outcome
                continue
            run.results[call.key] = outcome.value
            if outcome.model_call_id:
                run.model_call_ids.append(outcome.model_call_id)
            await _save_progress(run, call=call, value=outcome.value)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if retry is not None:
        raise retry


async def generate_profile(
    candidate_id: uuid.UUID,
    resume_id: uuid.UUID,
    job_id: uuid.UUID | None = None,
    *,
    last_attempt: bool = True,
    ai: AIServices | None = None,
) -> ProfileOutcome:
    """Run the whole profile generation (see module docstring)."""
    services = ai or get_ai()
    timings: dict[str, float] = {}
    started = time.perf_counter()
    inputs = await _load_inputs(candidate_id, resume_id, job_id)
    sections = detect_sections(inputs.text)
    quick = build_quick_profile(inputs.text, candidate_id=candidate_id, sections=sections)
    plan = plan_calls(
        inputs.text,
        sections,
        job=inputs.job,
        requirements=inputs.requirements,
        competency_keys=inputs.competency_keys,
        facts=_facts_text(quick.extraction),
    )
    experience_text = "\n".join(s.text for s in sections if s.key == "experience")
    outside_skills = "\n".join(s.text for s in sections if s.key != "skills")
    run = _Run(
        inputs=inputs,
        plan=plan,
        quick=quick,
        quick_roles=quick_roles_for_anchors(experience_text, plan.anchors),
        vocabulary=find_vocabulary_skills(outside_skills),
        text_sha=_sha(inputs.text),
        analysis_sha=analysis_key(inputs),
    )
    run.results.update(
        cached_parts(inputs.scan_result, plan, text_sha=run.text_sha, analysis_sha=run.analysis_sha)
    )
    run.cached = set(run.results)
    timings["plan"] = (time.perf_counter() - started) * 1000
    await _begin_sections(run)

    # 1.+2. model parts and (beside them) chunks + semantic evidence ----------
    evidence_task = asyncio.ensure_future(_evidence_task(run, sections, services, last_attempt=last_attempt))
    t0 = time.perf_counter()
    try:
        await _run_parts(run, services, last_attempt=last_attempt)
    except BaseException:
        if not evidence_task.done():
            evidence_task.cancel()
        await asyncio.gather(evidence_task, return_exceptions=True)
        raise
    timings["model_calls"] = (time.perf_counter() - t0) * 1000
    semantic = await evidence_task
    timings["evidence_wait"] = (time.perf_counter() - t0) * 1000 - timings["model_calls"]

    # 3. merge -------------------------------------------------------------
    merged = run.merged()
    degraded = list(merged.degraded)
    has_job = inputs.job is not None
    if has_job and inputs.requirements and semantic is None:
        degraded.append("matching")
    extraction = merged.extraction
    t0 = time.perf_counter()
    provisional, _ = build_profile_document(
        candidate_id=candidate_id,
        extraction=extraction,
        analysis=merged.analysis,
        job_match=None,
        known_competency_keys=set(inputs.competency_keys),
    )
    job_match: JobMatch | None = None
    breakdown: dict[str, Any] | None = None
    if has_job:
        skill_names = [s.name for group in provisional.skills.values() for s in group]
        evidence = combine_evidence(
            inputs.requirements, semantic, resume_text=inputs.text, skill_names=skill_names
        )
        job_match, match_breakdown = blend_job_match(
            evidence,
            merged.model_match,
            experience_years=provisional.experience_years,
            experience_min_years=(inputs.job or {}).get("experience_min_years"),
        )
        breakdown = match_breakdown.as_dict()
    document, facts = build_profile_document(
        candidate_id=candidate_id,
        extraction=extraction,
        analysis=merged.analysis,
        job_match=job_match,
        known_competency_keys=set(inputs.competency_keys),
    )
    timings["matching"] = (time.perf_counter() - t0) * 1000
    status = PROFILE_STATUS_PARTIAL if degraded else PROFILE_STATUS_READY

    # 4. persist ------------------------------------------------------------
    async with session_scope() as db:
        # Lock order everywhere: the resume row, then the per-candidate advisory
        # lock that serialises version numbering (two resumes may finish together).
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        if resume is None:
            raise ProfileInputError("resume disappeared")
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"candidate_profile:{candidate_id}"}
        )
        previous = await db.scalar(
            select(func.max(CandidateProfile.version)).where(CandidateProfile.candidate_id == candidate_id)
        )
        row = CandidateProfile(
            candidate_id=candidate_id,
            resume_id=resume_id,
            job_id=inputs.job_id,
            version=int(previous or 0) + 1,
            status=status,
            profile=document.model_dump(mode="json"),
            summary=(document.summary or document.headline or None),
            experience_years=document.experience_years,
            relevant_experience_years=document.relevant_experience_years,
            job_match_score=job_match.overall_fit if job_match is not None else None,
            claims_count=len(document.claims_to_verify),
            model_call_ids=run.model_call_ids,
        )
        db.add(row)
        resume.status = ResumeStatus.READY.value
        resume.error = None
        resume.status_detail = "analysis degraded" if degraded else None
        resume.processing_finished_at = datetime.now(UTC)
        progress = resume.progress
        for key in SECTION_LABELS:
            state = run.section_state(key)
            progress = progress_mod.set_section_state(
                progress, key, "fallback" if state == "fallback" else "done"
            )
        resume.progress = progress_mod.complete(progress)
        timings["total"] = (time.perf_counter() - started) * 1000
        resume.scan_result = {
            **(resume.scan_result or {}),
            "profile": {
                "facts": facts,
                "degraded": degraded,
                "fallbacks": merged.fallbacks,
                "match": breakdown,
                "pipeline": PIPELINE_VERSION,
                "prompt_versions": PART_PROMPT_VERSIONS,
                "calls": {
                    call.key: {
                        "ms": run.durations_ms.get(call.key),
                        "cached": call.key in run.cached,
                        "ok": isinstance(run.results.get(call.key), BaseModel),
                    }
                    for call in plan.calls
                },
                "timings_ms": {k: round(v, 1) for k, v in timings.items()},
            },
        }
        await db.flush()
        profile_id, version = row.id, row.version
    log.info(
        "profile.generated",
        resume_id=str(resume_id),
        profile_id=str(profile_id),
        version=version,
        status=status,
        calls=len(plan.calls),
        cached=len(run.cached),
        job_match=document.job_match.overall_fit if document.job_match else None,
        claims=len(document.claims_to_verify),
        duration_ms=round(timings["total"]),
    )
    return ProfileOutcome(
        profile_id=profile_id,
        version=version,
        status=status,
        job_match_score=job_match.overall_fit if job_match else None,
        experience_years=document.experience_years,
        claims=len(document.claims_to_verify),
        timings_ms=timings,
        breakdown=breakdown,
        degraded=degraded,
    )
