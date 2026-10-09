"""Jobs and job requirements (organization-scoped)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.logging import get_logger
from app.db.models import Interview, InterviewSession, Job, JobRequirement, Round
from app.domain.enums import JobStatus
from app.schemas.api.jobs import JobCreate, JobUpdate, RequirementCreate, RequirementUpdate
from app.services.identity import get_organization_settings
from app.services.jobs.policy import validate_job_policy_overrides
from app.services.question_bank.embeddings import (
    embed_rows_best_effort,
    enqueue_embedding,
    job_texts,
    requirement_text,
)

log = get_logger(__name__)

_JOB_EMBED_FIELDS = frozenset({"title", "description", "seniority", "department"})
_REQUIREMENT_EMBED_FIELDS = frozenset({"name", "description"})


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_job(db: AsyncSession, job_id: uuid.UUID, organization_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.organization_id != organization_id:
        raise NotFound("Job not found.")
    return job


def job_list_query(organization_id: uuid.UUID, *, status: JobStatus | None, q: str | None) -> Select[Any]:
    stmt = select(Job).where(Job.organization_id == organization_id)
    if status is not None:
        stmt = stmt.where(Job.status == status.value)
    else:
        stmt = stmt.where(Job.status != JobStatus.ARCHIVED.value)
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Job.department.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
            )
        )
    return stmt


async def job_counts(db: AsyncSession, job_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    ids = list(job_ids)
    counts: dict[uuid.UUID, dict[str, int]] = {
        i: {"rounds_count": 0, "requirements_count": 0, "candidates_count": 0, "sessions_count": 0}
        for i in ids
    }
    if not ids:
        return counts
    queries = {
        "rounds_count": select(Round.job_id, func.count())
        .where(Round.job_id.in_(ids), Round.is_active.is_(True))
        .group_by(Round.job_id),
        "requirements_count": select(JobRequirement.job_id, func.count())
        .where(JobRequirement.job_id.in_(ids))
        .group_by(JobRequirement.job_id),
        "candidates_count": select(Interview.job_id, func.count(distinct(Interview.candidate_id)))
        .where(Interview.job_id.in_(ids))
        .group_by(Interview.job_id),
        "sessions_count": select(InterviewSession.job_id, func.count())
        .where(InterviewSession.job_id.in_(ids))
        .group_by(InterviewSession.job_id),
    }
    for name, stmt in queries.items():
        for job_id, value in (await db.execute(stmt)).all():
            counts[job_id][name] = int(value)
    return counts


def _apply_job_fields(job: Job, body: JobCreate | JobUpdate, fields: set[str]) -> set[str]:
    changed: set[str] = set()
    for name in fields:
        if name == "requirements":
            continue
        value = getattr(body, name)
        if name == "coding_requirements":
            value = value.model_dump(mode="json") if value is not None else {}
        elif name in ("integrity_settings", "recording_settings"):
            value = dict(value or {})
        elif name in ("status", "candidate_report_mode"):
            if value is None:
                continue
            value = value.value
        elif name == "allow_candidate_round_selection" and value is None:
            continue
        elif name in ("title", "description") and value is None:
            continue
        if getattr(job, name) != value:
            setattr(job, name, value)
            changed.add(name)
    return changed


async def create_job(
    db: AsyncSession, *, organization_id: uuid.UUID, created_by_id: uuid.UUID | None, body: JobCreate
) -> tuple[Job, list[JobRequirement]]:
    validate_job_policy_overrides(body.integrity_settings, body.recording_settings)
    job = Job(
        organization_id=organization_id,
        created_by_id=created_by_id,
        title=body.title,
        description=body.description,
    )
    _apply_job_fields(job, body, set(body.model_fields_set) | {"status"})
    if body.candidate_report_mode is None:
        job.candidate_report_mode = (
            await get_organization_settings(db, organization_id)
        ).candidate_report_mode.value
    if body.allow_candidate_round_selection is None:
        job.allow_candidate_round_selection = False
    db.add(job)
    await db.flush()
    requirements = [_new_requirement(job.id, req, index) for index, req in enumerate(body.requirements)]
    db.add_all(requirements)
    await db.flush()
    await refresh_embeddings(db, job=job, requirements=requirements)
    return job, requirements


async def update_job(db: AsyncSession, job: Job, body: JobUpdate) -> set[str]:
    fields = set(body.model_fields_set)
    if fields & {"integrity_settings", "recording_settings"}:
        validate_job_policy_overrides(
            body.integrity_settings if "integrity_settings" in fields else job.integrity_settings,
            body.recording_settings if "recording_settings" in fields else job.recording_settings,
        )
    changed = _apply_job_fields(job, body, fields)
    if changed & _JOB_EMBED_FIELDS:
        job.embedding = None
    await db.flush()
    if job.embedding is None:
        await refresh_embeddings(db, job=job)
    return changed


async def archive_job(db: AsyncSession, job: Job) -> bool:
    if job.status == JobStatus.ARCHIVED.value:
        return False
    job.status = JobStatus.ARCHIVED.value
    await db.flush()
    return True


def _new_requirement(job_id: uuid.UUID, body: RequirementCreate, index: int) -> JobRequirement:
    return JobRequirement(
        job_id=job_id,
        kind=body.kind.value,
        name=body.name.strip(),
        competency_key=body.competency_key,
        description=body.description,
        weight=body.weight,
        min_years=body.min_years,
        sort_order=body.sort_order if body.sort_order is not None else index,
    )


async def list_requirements(db: AsyncSession, job_id: uuid.UUID) -> list[JobRequirement]:
    return list(
        (
            await db.scalars(
                select(JobRequirement)
                .where(JobRequirement.job_id == job_id)
                .order_by(JobRequirement.sort_order, JobRequirement.created_at)
            )
        ).all()
    )


async def add_requirement(db: AsyncSession, job: Job, body: RequirementCreate) -> JobRequirement:
    if body.sort_order is None:
        current = await db.scalar(
            select(func.max(JobRequirement.sort_order)).where(JobRequirement.job_id == job.id)
        )
        index = int(current) + 1 if current is not None else 0
    else:
        index = body.sort_order
    requirement = _new_requirement(job.id, body, index)
    db.add(requirement)
    job.embedding = None  # requirement names are part of the job text
    await db.flush()
    await refresh_embeddings(db, job=job, requirements=[requirement])
    return requirement


async def get_requirement(
    db: AsyncSession, requirement_id: uuid.UUID, organization_id: uuid.UUID
) -> tuple[JobRequirement, Job]:
    requirement = await db.get(JobRequirement, requirement_id)
    if requirement is None:
        raise NotFound("Requirement not found.")
    job = await db.get(Job, requirement.job_id)
    if job is None or job.organization_id != organization_id:
        raise NotFound("Requirement not found.")
    return requirement, job


async def update_requirement(
    db: AsyncSession, requirement: JobRequirement, job: Job, body: RequirementUpdate
) -> set[str]:
    changed: set[str] = set()
    for name in body.model_fields_set:
        value = getattr(body, name)
        if name == "kind":
            if value is None:
                continue
            value = value.value
        elif name in ("name", "weight", "sort_order") and value is None:
            continue
        if getattr(requirement, name) != value:
            setattr(requirement, name, value)
            changed.add(name)
    if changed & _REQUIREMENT_EMBED_FIELDS:
        requirement.embedding = None
        job.embedding = None
    await db.flush()
    if requirement.embedding is None or job.embedding is None:
        await refresh_embeddings(db, job=job, requirements=[requirement])
    return changed


async def delete_requirement(db: AsyncSession, requirement: JobRequirement, job: Job) -> None:
    await db.delete(requirement)
    job.embedding = None
    await db.flush()
    await refresh_embeddings(db, job=job)


async def refresh_embeddings(
    db: AsyncSession, *, job: Job | None = None, requirements: Sequence[JobRequirement] = ()
) -> None:
    """Embed inline (best effort); on failure enqueue ``question_bank.embed``."""
    ok = True
    if requirements:
        ok = await embed_rows_best_effort(db, requirements, requirement_text)
    if job is not None and job.embedding is None:
        texts = await job_texts(db, [job])
        ok = await embed_rows_best_effort(db, [job], lambda row: texts.get(row.id, "")) and ok
    if not ok:
        await enqueue_embedding(
            db,
            requirement_ids=[r.id for r in requirements if r.embedding is None],
            job_ids=[job.id] if job is not None and job.embedding is None else [],
        )
