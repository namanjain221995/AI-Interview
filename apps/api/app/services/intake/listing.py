"""Intake list, buckets and stats (INTAKE_INTERVIEW.md 10.3).

One query over ``intake_interviews i`` LEFT JOIN ``interviews iv``, LATERAL latest ``interview_sessions s`` (by
``created_at``), LATERAL newest ready ``intake_evaluations e``, LATERAL newest ``intake_decisions d``. Buckets are
views and may overlap; each row's single badge (``IntakeSummaryOut.bucket``) uses the precedence
cancelled > expired > rejected > accepted > borderline (decided) > awaiting_review > in_progress > scheduled >
active > drafts. :func:`bucket_predicates` (SQL) and :func:`badge` (Python) implement the same table.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import NotFound
from app.db.models import (
    Candidate,
    IntakeDecision,
    IntakeEvaluation,
    IntakeInterview,
    IntakeStageResult,
    IntegrityEvent,
    Interview,
    InterviewSession,
    Job,
    Round,
    User,
)
from app.domain.enums import IntegrityReviewStatus, SessionEndReason
from app.schemas.api.intake_admin import (
    IntakeCandidateRef,
    IntakeDecisionRef,
    IntakeEvaluationRef,
    IntakeInterviewRef,
    IntakeJobRef,
    IntakeRoundRef,
    IntakeSessionRef,
    IntakeStatsBuckets,
    IntakeStatsOut,
    IntakeStatsTotals,
    IntakeSummaryOut,
    UserRef,
)
from app.services.integrity.rules import REVIEWABLE_ACTIONS
from app.services.jobs.jobs import escape_like

ACCEPT_OUTCOMES = ("accept", "accept_with_notes")
BORDERLINE_OUTCOMES = ("second_interview", "request_evidence")
TRAINING_BANDS = ("strong_accept", "accept_with_notes")
LIVE_SESSION = ("live", "paused")
ENDED_SESSION = ("completing", "completed")
WAITING_SESSION = ("created", "preparing", "ready")
INCOMPLETE_END_REASONS = (
    SessionEndReason.CANDIDATE_ENDED.value,
    SessionEndReason.DISCONNECT_TIMEOUT.value,
    SessionEndReason.TECHNICAL_FAILURE.value,
)
STATS_BUCKETS: tuple[str, ...] = (
    "drafts", "active", "scheduled", "in_progress", "completed", "awaiting_review", "accepted", "borderline",
    "rejected", "training", "expired", "cancelled",
)  # fmt: skip
SORTS: tuple[str, ...] = ("-created_at", "created_at", "-completed_at", "-score", "score", "candidate")
_FOUR_PLACES = Decimal("0.0001")


@dataclass(slots=True)
class IntakeFilters:
    q: str | None = None
    job_id: uuid.UUID | None = None
    target_role: str | None = None
    band: str | None = None
    outcome: str | None = None
    reviewer_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    #: Exact lookup of the intake that owns this interview session (any session of its interview).
    session_id: uuid.UUID | None = None


# ================================================================================== the query

_i = IntakeInterview
_iv = Interview
_assigned = aliased(User, name="assigned_user")
_decider = aliased(User, name="decision_user")


def _laterals() -> tuple[Any, Any, Any, Any]:
    s = (
        select(
            InterviewSession.id.label("id"),
            InterviewSession.status.label("status"),
            InterviewSession.started_at.label("started_at"),
            InterviewSession.ended_at.label("ended_at"),
            InterviewSession.elapsed_seconds.label("elapsed_seconds"),
            InterviewSession.end_reason.label("end_reason"),
        )
        .where(InterviewSession.interview_id == _i.interview_id)
        .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
        .limit(1)
        .lateral("s")
    )
    has_plan = func.coalesce(func.jsonb_typeof(IntakeEvaluation.training_plan), "null") != "null"
    e = (
        select(
            IntakeEvaluation.id.label("id"),
            IntakeEvaluation.status.label("status"),
            IntakeEvaluation.weighted_total.label("weighted_total"),
            IntakeEvaluation.band.label("band"),
            IntakeEvaluation.evidence_sufficiency.label("evidence_sufficiency"),
            has_plan.label("has_training_plan"),
            IntakeEvaluation.review_items.label("review_items"),
        )
        .where(IntakeEvaluation.intake_id == _i.id, IntakeEvaluation.status == "ready")
        .order_by(IntakeEvaluation.created_at.desc(), IntakeEvaluation.version.desc())
        .limit(1)
        .lateral("e")
    )
    ea = (
        select(
            IntakeEvaluation.id.label("id"),
            IntakeEvaluation.status.label("status"),
            IntakeEvaluation.weighted_total.label("weighted_total"),
            IntakeEvaluation.band.label("band"),
            IntakeEvaluation.evidence_sufficiency.label("evidence_sufficiency"),
        )
        .where(IntakeEvaluation.intake_id == _i.id)
        .order_by(IntakeEvaluation.created_at.desc(), IntakeEvaluation.version.desc())
        .limit(1)
        .lateral("ea")
    )
    d = (
        select(
            IntakeDecision.id.label("id"),
            IntakeDecision.outcome.label("outcome"),
            IntakeDecision.created_at.label("created_at"),
            IntakeDecision.reviewer_id.label("reviewer_id"),
            IntakeDecision.review_items_resolution.label("review_items_resolution"),
        )
        .where(IntakeDecision.intake_id == _i.id)
        .order_by(IntakeDecision.created_at.desc(), IntakeDecision.id.desc())
        .limit(1)
        .lateral("d")
    )
    return s, e, ea, d


@dataclass(slots=True)
class _Q:
    s: Any
    e: Any
    ea: Any
    d: Any


def _from(q: _Q, organization_id: uuid.UUID) -> Select[Any]:
    return (
        select()
        .select_from(_i)
        .join(Candidate, Candidate.id == _i.candidate_id)
        .join(Job, Job.id == _i.job_id)
        .outerjoin(Round, Round.id == _i.round_id)
        .outerjoin(_iv, _iv.id == _i.interview_id)
        .outerjoin(q.s, true())
        .outerjoin(q.e, true())
        .outerjoin(q.ea, true())
        .outerjoin(q.d, true())
        .where(_i.organization_id == organization_id)
    )


def bucket_predicates(q: _Q) -> dict[str, ColumnElement[bool]]:
    s, e, d = q.s, q.e, q.d
    now = func.now()
    invited_open = and_(_i.status == "active", _iv.status == "invited", _iv.invite_expires_at > now)
    no_decision = d.c.id.is_(None)
    return {
        "drafts": _i.status == "draft",
        "active": and_(
            invited_open,
            or_(_iv.scheduled_for.is_(None), _iv.scheduled_for <= now),
            or_(s.c.id.is_(None), s.c.status.in_(WAITING_SESSION)),
        ),
        "scheduled": and_(invited_open, _iv.scheduled_for > now),
        "in_progress": s.c.status.in_(LIVE_SESSION),
        "completed": s.c.status.in_(ENDED_SESSION),
        "awaiting_review": and_(s.c.status.in_(ENDED_SESSION), no_decision),
        "accepted": d.c.outcome.in_(ACCEPT_OUTCOMES),
        "borderline": or_(and_(no_decision, e.c.band == "borderline"), d.c.outcome.in_(BORDERLINE_OUTCOMES)),
        "rejected": d.c.outcome == "reject",
        "training": and_(
            e.c.has_training_plan.is_(True),
            or_(d.c.outcome.in_(ACCEPT_OUTCOMES), and_(no_decision, e.c.band.in_(TRAINING_BANDS))),
        ),
        "expired": or_(_iv.status == "expired", and_(_iv.status == "invited", _iv.invite_expires_at <= now)),
        "cancelled": or_(_i.status == "cancelled", _iv.status == "cancelled"),
    }


def _filters(stmt: Select[Any], q: _Q, filters: IntakeFilters) -> Select[Any]:
    if filters.q:
        pattern = f"%{escape_like(filters.q.strip())}%"
        stmt = stmt.where(
            or_(Candidate.full_name.ilike(pattern, escape="\\"), Candidate.email.ilike(pattern, escape="\\"))
        )
    if filters.job_id is not None:
        stmt = stmt.where(_i.job_id == filters.job_id)
    if filters.target_role:
        stmt = stmt.where(_i.target_role == filters.target_role)
    if filters.band:
        stmt = stmt.where(q.e.c.band == filters.band)
    if filters.outcome:
        stmt = stmt.where(q.d.c.outcome == filters.outcome)
    if filters.reviewer_id is not None:
        stmt = stmt.where(
            or_(_i.assigned_reviewer_id == filters.reviewer_id, q.d.c.reviewer_id == filters.reviewer_id)
        )
    if filters.batch_id is not None:
        stmt = stmt.where(_i.batch_id == filters.batch_id)
    if filters.session_id is not None:
        stmt = stmt.where(
            exists().where(
                InterviewSession.id == filters.session_id,
                InterviewSession.interview_id == _i.interview_id,
            )
        )
    if filters.created_from is not None:
        stmt = stmt.where(_i.created_at >= filters.created_from)
    if filters.created_to is not None:
        stmt = stmt.where(_i.created_at <= filters.created_to)
    return stmt


def _columns(q: _Q) -> list[Any]:
    s, e, ea, d = q.s, q.e, q.ea, q.d
    return [
        _i.id.label("id"),
        _i.status.label("status"),
        _i.title.label("title"),
        _i.batch_id.label("batch_id"),
        _i.target_role.label("target_role"),
        _i.onboarding_submitted_at.label("onboarding_submitted_at"),
        _i.resume_confirmed_at.label("resume_confirmed_at"),
        _i.created_at.label("created_at"),
        _i.updated_at.label("updated_at"),
        _i.assigned_reviewer_id.label("assigned_reviewer_id"),
        Candidate.id.label("candidate_id"),
        Candidate.full_name.label("candidate_name"),
        Candidate.email.label("candidate_email"),
        Job.id.label("job_id"),
        Job.title.label("job_title"),
        Round.id.label("round_id"),
        Round.name.label("round_name"),
        _iv.id.label("iv_id"),
        _iv.status.label("iv_status"),
        _iv.invite_expires_at.label("iv_expires_at"),
        _iv.scheduled_for.label("iv_scheduled_for"),
        _iv.verified_at.label("iv_verified_at"),
        s.c.id.label("s_id"),
        s.c.status.label("s_status"),
        s.c.started_at.label("s_started_at"),
        s.c.ended_at.label("s_ended_at"),
        s.c.elapsed_seconds.label("s_elapsed_seconds"),
        e.c.id.label("e_id"),
        e.c.status.label("e_status"),
        e.c.weighted_total.label("e_weighted_total"),
        e.c.band.label("e_band"),
        e.c.evidence_sufficiency.label("e_evidence_sufficiency"),
        e.c.has_training_plan.label("e_has_training_plan"),
        e.c.review_items.label("e_review_items"),
        ea.c.id.label("ea_id"),
        ea.c.status.label("ea_status"),
        ea.c.weighted_total.label("ea_weighted_total"),
        ea.c.band.label("ea_band"),
        ea.c.evidence_sufficiency.label("ea_evidence_sufficiency"),
        d.c.id.label("d_id"),
        d.c.outcome.label("d_outcome"),
        d.c.created_at.label("d_created_at"),
        d.c.reviewer_id.label("d_reviewer_id"),
        d.c.review_items_resolution.label("d_resolution"),
        _assigned.full_name.label("assigned_name"),
        _decider.full_name.label("decider_name"),
    ]


def _sorted(stmt: Select[Any], q: _Q, sort: str) -> Select[Any]:
    if sort == "created_at":
        return stmt.order_by(_i.created_at.asc(), _i.id)
    if sort == "-completed_at":
        return stmt.order_by(q.s.c.ended_at.desc().nulls_last(), _i.created_at.desc(), _i.id)
    if sort == "-score":
        return stmt.order_by(q.e.c.weighted_total.desc().nulls_last(), _i.created_at.desc(), _i.id)
    if sort == "score":
        return stmt.order_by(q.e.c.weighted_total.asc().nulls_last(), _i.created_at.desc(), _i.id)
    if sort == "candidate":
        return stmt.order_by(func.lower(Candidate.full_name).asc(), _i.created_at.desc(), _i.id)
    return stmt.order_by(_i.created_at.desc(), _i.id)


def _rows_query(organization_id: uuid.UUID, filters: IntakeFilters) -> tuple[Select[Any], _Q]:
    q = _Q(*_laterals())
    stmt = (
        _from(q, organization_id)
        .add_columns(*_columns(q))
        .outerjoin(_assigned, _assigned.id == _i.assigned_reviewer_id)
        .outerjoin(_decider, _decider.id == q.d.c.reviewer_id)
    )
    return _filters(stmt, q, filters), q


# ================================================================================ serialisation


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def badge(row: Mapping[str, Any], now: datetime | None = None) -> str:
    """The single bucket badge of one row (same table as :func:`bucket_predicates`, with precedence)."""
    now = now or datetime.now(UTC)
    status = row.get("status")
    iv_status = row.get("iv_status")
    expires = _aware(row.get("iv_expires_at"))
    scheduled = _aware(row.get("iv_scheduled_for"))
    s_status = row.get("s_status")
    outcome = row.get("d_outcome")
    if status == "cancelled" or iv_status == "cancelled":
        return "cancelled"
    if iv_status == "expired" or (iv_status == "invited" and expires is not None and expires <= now):
        return "expired"
    if outcome == "reject":
        return "rejected"
    if outcome in ACCEPT_OUTCOMES:
        return "accepted"
    if outcome in BORDERLINE_OUTCOMES:
        return "borderline"
    if s_status in ENDED_SESSION and outcome is None:
        return "awaiting_review"
    if s_status in LIVE_SESSION:
        return "in_progress"
    invited_open = status == "active" and iv_status == "invited" and expires is not None and expires > now
    if invited_open and scheduled is not None and scheduled > now:
        return "scheduled"
    if invited_open and (row.get("s_id") is None or s_status in WAITING_SESSION):
        return "active"
    if status == "draft":
        return "drafts"
    if s_status in ENDED_SESSION:
        return "completed"
    return "active"


def _open_review_items(row: Mapping[str, Any]) -> int:
    raw_items, raw_resolution = row.get("e_review_items"), row.get("d_resolution")
    items: list[Any] = raw_items if isinstance(raw_items, list) else []
    resolution: list[Any] = raw_resolution if isinstance(raw_resolution, list) else []
    resolved = {str(r.get("item_id")) for r in resolution if isinstance(r, Mapping) and r.get("item_id")}
    return sum(1 for item in items if isinstance(item, Mapping) and str(item.get("id")) not in resolved)


def _summary(row: Mapping[str, Any], flags: Mapping[uuid.UUID, int], now: datetime) -> IntakeSummaryOut:
    evaluation = None
    prefix = "e" if row.get("e_id") is not None else ("ea" if row.get("ea_id") is not None else None)
    if prefix is not None:
        evaluation = IntakeEvaluationRef(
            id=row[f"{prefix}_id"],
            status=row[f"{prefix}_status"],
            weighted_total=row[f"{prefix}_weighted_total"],
            band=row[f"{prefix}_band"],
            evidence_sufficiency=row[f"{prefix}_evidence_sufficiency"],
        )
    decision = None
    if row.get("d_id") is not None:
        decision = IntakeDecisionRef(
            outcome=row["d_outcome"],
            created_at=row["d_created_at"],
            reviewer=UserRef(id=row["d_reviewer_id"], full_name=row.get("decider_name") or ""),
        )
    session_id = row.get("s_id")
    return IntakeSummaryOut(
        id=row["id"],
        status=row["status"],
        bucket=badge(row, now),  # type: ignore[arg-type]
        title=row["title"],
        batch_id=row.get("batch_id"),
        candidate=IntakeCandidateRef(
            id=row["candidate_id"], full_name=row["candidate_name"], email=row["candidate_email"]
        ),
        job=IntakeJobRef(id=row["job_id"], title=row["job_title"]),
        round=IntakeRoundRef(id=row["round_id"], name=row["round_name"]) if row.get("round_id") else None,
        target_role=row.get("target_role"),
        interview=IntakeInterviewRef(
            id=row["iv_id"],
            status=row["iv_status"],
            invite_expires_at=row["iv_expires_at"],
            scheduled_for=row.get("iv_scheduled_for"),
            verified_at=row.get("iv_verified_at"),
        )
        if row.get("iv_id")
        else None,
        session=IntakeSessionRef(
            id=session_id,
            status=row["s_status"],
            started_at=row.get("s_started_at"),
            ended_at=row.get("s_ended_at"),
            elapsed_seconds=float(row.get("s_elapsed_seconds") or 0.0),
        )
        if session_id
        else None,
        onboarding_submitted=row.get("onboarding_submitted_at") is not None,
        resume_confirmed=row.get("resume_confirmed_at") is not None,
        evaluation=evaluation,
        decision=decision,
        assigned_reviewer=UserRef(id=row["assigned_reviewer_id"], full_name=row.get("assigned_name") or "")
        if row.get("assigned_reviewer_id")
        else None,
        has_training_plan=bool(row.get("e_has_training_plan")),
        review_items_open=_open_review_items(row),
        integrity_flags=int(flags.get(session_id, 0)) if session_id else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _integrity_flags(db: AsyncSession, session_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not session_ids:
        return {}
    rows = await db.execute(
        select(IntegrityEvent.session_id, func.count())
        .where(
            IntegrityEvent.session_id.in_(session_ids),
            IntegrityEvent.action.in_([a.value for a in REVIEWABLE_ACTIONS]),
            IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
        )
        .group_by(IntegrityEvent.session_id)
    )
    return {session_id: int(count) for session_id, count in rows.all()}


async def _serialize(db: AsyncSession, rows: Sequence[Mapping[str, Any]]) -> list[IntakeSummaryOut]:
    flags = await _integrity_flags(db, [r["s_id"] for r in rows if r.get("s_id")])
    now = datetime.now(UTC)
    return [_summary(row, flags, now) for row in rows]


# ======================================================================================== API


async def list_intakes(
    db: AsyncSession,
    organization_id: uuid.UUID,
    filters: IntakeFilters,
    *,
    bucket: str = "all",
    sort: str = "-created_at",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[IntakeSummaryOut], int]:
    stmt, q = _rows_query(organization_id, filters)
    if bucket and bucket != "all":
        stmt = stmt.where(bucket_predicates(q)[bucket])
    total = int(await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    rows = (await db.execute(_sorted(stmt, q, sort).limit(limit).offset(offset))).mappings().all()
    return await _serialize(db, rows), total


async def summaries_for(
    db: AsyncSession, organization_id: uuid.UUID, intake_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, IntakeSummaryOut]:
    """Summaries of the given intake rows of the organisation (others are silently absent)."""
    if not intake_ids:
        return {}
    stmt, _q = _rows_query(organization_id, IntakeFilters())
    rows = (await db.execute(stmt.where(_i.id.in_(list(intake_ids))))).mappings().all()
    return {item.id: item for item in await _serialize(db, rows)}


async def get_summary(db: AsyncSession, organization_id: uuid.UUID, intake_id: uuid.UUID) -> IntakeSummaryOut:
    """One summary (404 ``intake_not_found`` for unknown or other-organisation ids). T2's detail view extends it."""
    found = await summaries_for(db, organization_id, [intake_id])
    if intake_id not in found:
        raise NotFound("Intake interview not found.", code="intake_not_found")
    return found[intake_id]


def _avg_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value).quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


async def intake_stats(
    db: AsyncSession, organization_id: uuid.UUID, filters: IntakeFilters
) -> IntakeStatsOut:
    """Bucket counts and totals under the same filters as the list (no bucket, sort or paging)."""
    q = _Q(*_laterals())
    preds = bucket_predicates(q)
    s, e, d = q.s, q.e, q.d
    incomplete = and_(
        s.c.ended_at.isnot(None),
        or_(
            s.c.end_reason.in_(INCOMPLETE_END_REASONS),
            exists().where(IntakeStageResult.session_id == s.c.id, IntakeStageResult.status == "incomplete"),
        ),
    )
    with_band = and_(e.c.band.isnot(None), e.c.band != "insufficient_evidence")
    columns = [func.count().filter(pred).label(name) for name, pred in preds.items()]
    columns += [
        func.count().filter(_i.status != "draft").label("t_invitations"),
        func.count().filter(s.c.started_at.isnot(None)).label("t_started"),
        func.count().filter(preds["completed"]).label("t_completed"),
        func.count().filter(incomplete).label("t_incomplete"),
        func.count().filter(preds["awaiting_review"]).label("t_awaiting_review"),
        func.count().filter(d.c.id.isnot(None)).label("t_decided"),
        func.avg(e.c.weighted_total).filter(with_band).label("t_avg_total"),
        func.avg(s.c.elapsed_seconds).filter(s.c.status.in_(ENDED_SESSION)).label("t_avg_duration"),
    ]
    stmt = _filters(_from(q, organization_id).add_columns(*columns), q, filters)
    row = (await db.execute(stmt)).mappings().one()
    buckets = IntakeStatsBuckets(**{name: int(row[name] or 0) for name in STATS_BUCKETS})
    avg_duration = row["t_avg_duration"]
    totals = IntakeStatsTotals(
        invitations=int(row["t_invitations"] or 0),
        started=int(row["t_started"] or 0),
        completed=int(row["t_completed"] or 0),
        incomplete=int(row["t_incomplete"] or 0),
        awaiting_review=int(row["t_awaiting_review"] or 0),
        decided=int(row["t_decided"] or 0),
        average_weighted_total=_avg_decimal(row["t_avg_total"]),
        average_duration_s=round(float(avg_duration), 1) if avg_duration is not None else None,
    )
    return IntakeStatsOut(buckets=buckets, totals=totals)


__all__ = [
    "SORTS",
    "STATS_BUCKETS",
    "IntakeFilters",
    "badge",
    "bucket_predicates",
    "get_summary",
    "intake_stats",
    "list_intakes",
    "summaries_for",
]
