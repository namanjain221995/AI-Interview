"""Recruiter-facing interview sessions (CONTRACTS.md section 4).

Every endpoint is staff-only and organization-scoped (SUPER_ADMIN may read
any organization; a session of another organization looks like a 404).
Transcript and question views are audited. Integrity (``/integrity``) and
coding (``/coding``) sub-resources live in their own modules.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import load_only

from app.core.audit import audit
from app.core.deps import DbSession, PaginationParams, require_permission, resolve_org_id
from app.core.errors import ValidationFailed
from app.core.rbac import Permission, Principal
from app.db.models import Candidate, CompetencyEvidence, InterviewSession, Transcript
from app.domain.enums import EvidenceSignal, EvidenceSource, Recommendation, SessionStatus
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.sessions import (
    EvidenceOut,
    SessionDetailOut,
    SessionQuestionOut,
    SessionScoresOut,
    SessionSummaryOut,
    TimelineOut,
    TranscriptOut,
    TranscriptSegmentOut,
)
from app.services.reports.live import live_events
from app.services.reports.queries import (
    LIVE_STATUSES,
    TERMINAL_STATUSES,
    answer_audio_media,
    current_recommendation_expr,
    get_session_for_principal,
    needs_review_clause,
    not_needs_review_clause,
)
from app.services.reports.views import (
    SUMMARY_COLUMNS,
    questions_view,
    scores_view,
    session_detail,
    session_summaries,
    timeline_view,
)
from app.services.scoring.persistence import recompute_session_scores

router = APIRouter(prefix="/sessions", tags=["sessions"], responses=ERROR_RESPONSES)

SessionReader = Annotated[Principal, Depends(require_permission(Permission.SESSIONS_READ))]
TranscriptReader = Annotated[
    Principal, Depends(require_permission(Permission.SESSIONS_READ, Permission.TRANSCRIPTS_READ))
]
ScoreManager = Annotated[
    Principal, Depends(require_permission(Permission.SESSIONS_READ, Permission.REVIEWS_WRITE))
]

MAX_TRANSCRIPT_PAGE = 5000


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _statuses(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(SessionStatus(part).value)
            except ValueError as exc:
                raise ValidationFailed(
                    f"Unknown session status {part!r}.", details={"field": "status"}
                ) from exc
    return out


@router.get("", response_model=Page[SessionSummaryOut], summary="List interview sessions")
async def list_sessions(
    principal: SessionReader,
    db: DbSession,
    page: PaginationParams,
    organization_id: Annotated[
        uuid.UUID | None, Query(description="SUPER_ADMIN: filter by organization")
    ] = None,
    status: Annotated[list[str] | None, Query(description="Repeat or comma-separate statuses")] = None,
    job_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    round_id: uuid.UUID | None = None,
    interview_id: uuid.UUID | None = None,
    recommendation: Recommendation | None = None,
    live: bool | None = None,
    needs_review: Annotated[
        bool | None,
        Query(description="true: completed sessions without a human review decision yet (the review queue)"),
    ] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search candidate name or email")] = None,
) -> Page[SessionSummaryOut]:
    stmt = select(InterviewSession).options(load_only(*SUMMARY_COLUMNS))
    if principal.is_super_admin:
        if organization_id is not None:
            stmt = stmt.where(InterviewSession.organization_id == organization_id)
    else:
        stmt = stmt.where(InterviewSession.organization_id == resolve_org_id(principal, organization_id))
    statuses = _statuses(status)
    if statuses:
        stmt = stmt.where(InterviewSession.status.in_(statuses))
    if live is True:
        stmt = stmt.where(InterviewSession.status.in_(LIVE_STATUSES))
    elif live is False:
        stmt = stmt.where(InterviewSession.status.not_in(LIVE_STATUSES))
    if needs_review is True:
        stmt = stmt.where(needs_review_clause())
    elif needs_review is False:
        stmt = stmt.where(not_needs_review_clause())
    if job_id is not None:
        stmt = stmt.where(InterviewSession.job_id == job_id)
    if candidate_id is not None:
        stmt = stmt.where(InterviewSession.candidate_id == candidate_id)
    if round_id is not None:
        stmt = stmt.where(InterviewSession.round_id == round_id)
    if interview_id is not None:
        stmt = stmt.where(InterviewSession.interview_id == interview_id)
    if recommendation is not None:
        # The value the list shows (newest recruiter report, else newest overall score).
        stmt = stmt.where(current_recommendation_expr() == recommendation.value)
    if created_from is not None:
        stmt = stmt.where(InterviewSession.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(InterviewSession.created_at < created_to)
    if q:
        pattern = f"%{_escape_like(q.strip())}%"
        stmt = stmt.join(Candidate, Candidate.id == InterviewSession.candidate_id).where(
            or_(Candidate.full_name.ilike(pattern, escape="\\"), Candidate.email.ilike(pattern, escape="\\"))
        )
    total = int(
        await db.scalar(
            select(func.count()).select_from(stmt.with_only_columns(InterviewSession.id).subquery())
        )
        or 0
    )
    rows = (
        await db.scalars(
            page.apply(
                stmt.order_by(
                    InterviewSession.started_at.desc().nulls_last(),
                    InterviewSession.created_at.desc(),
                    InterviewSession.id,
                )
            )
        )
    ).all()
    items = [SessionSummaryOut.model_validate(s) for s in await session_summaries(db, rows)]
    return Page[SessionSummaryOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{session_id}", response_model=SessionDetailOut, summary="Session summary, plan, state digest and scores"
)
async def get_session(session_id: uuid.UUID, principal: SessionReader, db: DbSession) -> SessionDetailOut:
    session = await get_session_for_principal(db, principal, session_id)
    return SessionDetailOut.model_validate(await session_detail(db, session))


@router.get(
    "/{session_id}/transcript", response_model=TranscriptOut, summary="Transcript of both speakers (audited)"
)
async def get_transcript(
    session_id: uuid.UUID,
    principal: TranscriptReader,
    db: DbSession,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_TRANSCRIPT_PAGE)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
    speaker: Annotated[str | None, Query(pattern="^(candidate|interviewer|system)$")] = None,
) -> TranscriptOut:
    session = await get_session_for_principal(db, principal, session_id)
    stmt = select(Transcript).where(Transcript.session_id == session.id, Transcript.kind == "final")
    if speaker:
        stmt = stmt.where(Transcript.speaker == speaker)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    interruptions = int(
        await db.scalar(
            select(func.count())
            .select_from(Transcript)
            .where(Transcript.session_id == session.id, Transcript.interrupted.is_(True))
        )
        or 0
    )
    rows = (await db.scalars(stmt.order_by(Transcript.seq).limit(limit).offset(offset))).all()
    audio = await answer_audio_media(db, session.id)
    items = []
    for row in rows:
        item = TranscriptSegmentOut.model_validate(row)
        item.audio_media_id = audio.get(row.answer_id) if row.answer_id else None
        items.append(item)
    await audit(
        db,
        principal,
        "transcript.view",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={"segments": len(items), "offset": offset, "speaker": speaker},
    )
    return TranscriptOut(
        session_id=session.id,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        interruptions=interruptions,
        answer_audio={str(k): v for k, v in audio.items()},
    )


@router.get(
    "/{session_id}/questions",
    response_model=Page[SessionQuestionOut],
    summary="Questions with answers, evaluations, evidence and follow-up chains (audited)",
)
async def get_questions(
    session_id: uuid.UUID, principal: TranscriptReader, db: DbSession, request: Request
) -> Page[SessionQuestionOut]:
    session = await get_session_for_principal(db, principal, session_id)
    items = [SessionQuestionOut.model_validate(q) for q in await questions_view(db, session)]
    await audit(
        db,
        principal,
        "transcript.view",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={"view": "questions", "questions": len(items)},
    )
    return Page[SessionQuestionOut](items=items, total=len(items), limit=len(items), offset=0)


@router.get("/{session_id}/evidence", response_model=Page[EvidenceOut], summary="Competency evidence items")
async def get_evidence(
    session_id: uuid.UUID,
    principal: SessionReader,
    db: DbSession,
    page: PaginationParams,
    competency: Annotated[str | None, Query(max_length=80, description="Competency key")] = None,
    signal: EvidenceSignal | None = None,
    source: EvidenceSource | None = None,
    question_id: uuid.UUID | None = None,
    answer_id: uuid.UUID | None = None,
) -> Page[EvidenceOut]:
    session = await get_session_for_principal(db, principal, session_id)
    stmt = select(CompetencyEvidence).where(CompetencyEvidence.session_id == session.id)
    if competency:
        stmt = stmt.where(CompetencyEvidence.competency_key == competency)
    if signal is not None:
        stmt = stmt.where(CompetencyEvidence.signal == signal.value)
    if source is not None:
        stmt = stmt.where(CompetencyEvidence.source == source.value)
    if question_id is not None:
        stmt = stmt.where(CompetencyEvidence.question_id == question_id)
    if answer_id is not None:
        stmt = stmt.where(CompetencyEvidence.answer_id == answer_id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(
            page.apply(
                stmt.order_by(
                    CompetencyEvidence.elapsed_seconds.nulls_last(),
                    CompetencyEvidence.created_at,
                    CompetencyEvidence.id,
                )
            )
        )
    ).all()
    return Page[EvidenceOut](
        items=[EvidenceOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get(
    "/{session_id}/scores", response_model=SessionScoresOut, summary="Scores and the why behind each one"
)
async def get_scores(session_id: uuid.UUID, principal: SessionReader, db: DbSession) -> SessionScoresOut:
    session = await get_session_for_principal(db, principal, session_id)
    return SessionScoresOut.model_validate(await scores_view(db, session))


@router.get("/{session_id}/timeline", response_model=TimelineOut, summary="Competency estimates over time")
async def get_timeline(
    session_id: uuid.UUID,
    principal: SessionReader,
    db: DbSession,
    window_minutes: Annotated[float, Query(gt=0, le=120, description="Window width in minutes")] = 10,
) -> TimelineOut:
    session = await get_session_for_principal(db, principal, session_id)
    return TimelineOut.model_validate(await timeline_view(db, session, window_minutes=window_minutes))


@router.get(
    "/{session_id}/live",
    summary="Live session monitor (Server-Sent Events, staff only)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def live_session(
    session_id: uuid.UUID,
    principal: SessionReader,
    db: DbSession,
    request: Request,
    interval: Annotated[float, Query(ge=1, le=60, description="Seconds between state digests")] = 5,
    max_seconds: Annotated[float, Query(ge=5, le=4 * 3600)] = 4 * 3600,
) -> StreamingResponse:
    session = await get_session_for_principal(db, principal, session_id)
    await audit(
        db,
        principal,
        "session.live.view",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
    )
    stream = live_events(
        session.id, is_disconnected=request.is_disconnected, interval=interval, max_seconds=max_seconds
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post(
    "/{session_id}/recompute-scores",
    response_model=SessionScoresOut,
    summary="Recompute scores from the stored evidence",
)
async def recompute_scores(
    session_id: uuid.UUID,
    principal: ScoreManager,
    db: DbSession,
    request: Request,
    final: Annotated[bool | None, Query(description="Default: final once the session has ended")] = None,
) -> SessionScoresOut:
    session = await get_session_for_principal(db, principal, session_id)
    is_final = final if final is not None else session.status in TERMINAL_STATUSES
    result = await recompute_session_scores(db, session.id, final=is_final, trigger="manual")
    await audit(
        db,
        principal,
        "scores.recompute",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={
            "final": is_final,
            "overall_score": result.rubric.overall_score,
            "recommendation": result.recommendation.recommendation.value,
        },
    )
    return SessionScoresOut.model_validate(await scores_view(db, session))
