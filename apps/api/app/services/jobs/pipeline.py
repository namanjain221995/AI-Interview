"""A job's candidate pipeline, server-paged with exact stage counts (S14, audit F3).

The job page used to assemble its pipeline in the browser from the first 200 invitations and 100 sessions,
so larger jobs (a campus drive) silently lost rows and miscounted. Stages here are exact and match the
interview list's filters: an invitation still marked ``invited`` after its start deadline counts as closed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Candidate, Interview, InterviewSession
from app.domain.enums import InterviewStatus
from app.services.jobs.invitations import serialize_interviews
from app.services.jobs.jobs import escape_like
from app.services.reports.views import SUMMARY_COLUMNS, session_summaries

PipelineStage = Literal["invited", "in_progress", "completed", "closed"]
PIPELINE_STAGES: tuple[PipelineStage, ...] = ("invited", "in_progress", "completed", "closed")


def stage_clause(stage: PipelineStage, now: datetime) -> ColumnElement[bool]:
    if stage == "invited":
        return and_(Interview.status == InterviewStatus.INVITED.value, Interview.invite_expires_at > now)
    if stage == "in_progress":
        return Interview.status == InterviewStatus.IN_PROGRESS.value
    if stage == "completed":
        return Interview.status == InterviewStatus.COMPLETED.value
    return or_(
        Interview.status.in_((InterviewStatus.CANCELLED.value, InterviewStatus.EXPIRED.value)),
        and_(Interview.status == InterviewStatus.INVITED.value, Interview.invite_expires_at <= now),
    )


async def pipeline_counts(
    db: AsyncSession, job_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, int]:
    """Interviews of the job per stage (one grouped query), plus ``all``."""
    now = now or datetime.now(UTC)
    stage = case(*((stage_clause(s, now), s) for s in PIPELINE_STAGES[:-1]), else_="closed")
    rows = (
        await db.execute(select(stage, func.count()).where(Interview.job_id == job_id).group_by(stage))
    ).all()
    counts: dict[str, int] = {s: 0 for s in PIPELINE_STAGES}
    for name, value in rows:
        counts[str(name)] = int(value)
    counts["all"] = sum(counts[s] for s in PIPELINE_STAGES)
    return counts


async def pipeline_page(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    stage: PipelineStage | Literal["all"] = "all",
    q: str | None = None,
    limit: int,
    offset: int,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """``(rows, total, counts)``: one page of ``{interview, session}`` (newest invitation first, with its latest
    session's summary), the number of rows matching ``stage``/``q``, and the job's stage counts."""
    now = now or datetime.now(UTC)
    stmt = select(Interview).where(Interview.job_id == job_id)
    if stage != "all":
        stmt = stmt.where(stage_clause(stage, now))
    if q and q.strip():
        pattern = f"%{escape_like(q.strip())}%"
        stmt = stmt.join(Candidate, Candidate.id == Interview.candidate_id).where(
            or_(Candidate.full_name.ilike(pattern, escape="\\"), Candidate.email.ilike(pattern, escape="\\"))
        )
    total = int(
        await db.scalar(select(func.count()).select_from(stmt.with_only_columns(Interview.id).subquery()))
        or 0
    )
    interviews: Sequence[Interview] = (
        await db.scalars(
            stmt.order_by(Interview.created_at.desc(), Interview.id.desc()).limit(limit).offset(offset)
        )
    ).all()
    serialized = await serialize_interviews(db, interviews)
    latest = (
        (
            await db.scalars(
                select(InterviewSession)
                .options(load_only(*SUMMARY_COLUMNS))
                .where(InterviewSession.interview_id.in_([i.id for i in interviews]))
                .order_by(InterviewSession.interview_id, InterviewSession.created_at.desc())
                .distinct(InterviewSession.interview_id)
            )
        ).all()
        if interviews
        else []
    )
    summaries = {s["interview_id"]: s for s in await session_summaries(db, latest)}
    rows = [{"interview": item, "session": summaries.get(item.id)} for item in serialized]
    return rows, total, await pipeline_counts(db, job_id, now=now)


__all__ = ["PIPELINE_STAGES", "PipelineStage", "pipeline_counts", "pipeline_page", "stage_clause"]
