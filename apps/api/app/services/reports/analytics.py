"""Organization analytics (all queries are scoped to one organization).

Period semantics (``?days`` back from now), one rule per kind of record:

* every session-based metric (counts, completion, scores, competencies,
  integrity, hints, reviews, sessions per day) places a session in the period
  by :func:`session_time` - when it started, or when it was set up if it never
  started (stable for the session's lifetime, unlike ``ended_at``/``updated_at``);
* interview metrics (invited/completed, the funnel) follow the cohort of
  interviews *invited* in the period (``Interview.created_at``);
* model calls are events: ``ModelCall.created_at``;
* KPIs about the present - live sessions, open invitations, the review queue,
  unreviewed integrity signals - are not bounded by the period.

Scores are each session's newest computed set (final when available).
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, exists, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CodingHint,
    CodingQuestion,
    CodingSession,
    IntegrityEvent,
    Interview,
    InterviewSession,
    Job,
    ModelCall,
    Report,
    ReviewDecision,
    Round,
    Score,
)
from app.domain.enums import (
    IntegrityReviewStatus,
    InterviewStatus,
    ModelCallStatus,
    ReportType,
    ScoreCategory,
    SessionStatus,
)
from app.services.reports.assembly import INTEGRITY_NOTE
from app.services.reports.queries import LIVE_STATUSES, needs_review_clause
from app.services.scoring.persistence import FLAG_ACTIONS, competency_names, latest_scores_lateral

BUCKET_LABELS = [f"{i * 10}-{i * 10 + 9}" if i < 9 else "90-100" for i in range(10)]
ERROR_STATUSES = (
    ModelCallStatus.ERROR.value,
    ModelCallStatus.TIMEOUT.value,
    ModelCallStatus.CIRCUIT_OPEN.value,
)


@dataclass(slots=True)
class Period:
    organization_id: uuid.UUID
    days: int
    since: datetime
    until: datetime
    job_id: uuid.UUID | None = None
    round_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "since": self.since,
            "until": self.until,
            "job_id": str(self.job_id) if self.job_id else None,
            "round_id": str(self.round_id) if self.round_id else None,
        }


def make_period(
    organization_id: uuid.UUID, days: int, job_id: uuid.UUID | None = None, round_id: uuid.UUID | None = None
) -> Period:
    until = datetime.now(UTC)
    return Period(organization_id, days, until - timedelta(days=days), until, job_id, round_id)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _session_filters(period: Period) -> list[Any]:
    """Organization / job / round scope of sessions (no time bound)."""
    conditions: list[Any] = [InterviewSession.organization_id == period.organization_id]
    if period.job_id is not None:
        conditions.append(InterviewSession.job_id == period.job_id)
    if period.round_id is not None:
        conditions.append(InterviewSession.round_id == period.round_id)
    return conditions


def session_time() -> Any:
    """The timestamp that places a session in a period (see the module docstring)."""
    return func.coalesce(InterviewSession.started_at, InterviewSession.created_at)


def _sessions_in_period(period: Period, *, since: datetime | None = None) -> list[Any]:
    """Scope + period for every session-based metric."""
    return [*_session_filters(period), session_time() >= (since or period.since)]


def _interview_filters(period: Period) -> list[Any]:
    """Organization / job / round scope of interviews (no time bound)."""
    conditions: list[Any] = [Interview.organization_id == period.organization_id]
    if period.job_id is not None:
        conditions.append(Interview.job_id == period.job_id)
    if period.round_id is not None:
        conditions.append(Interview.round_id == period.round_id)
    return conditions


def _interview_cohort(period: Period) -> list[Any]:
    """Interviews invited in the period (the funnel cohort)."""
    return [*_interview_filters(period), Interview.created_at >= period.since]


async def _count(db: AsyncSession, stmt: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


async def _latest_overall_rows(db: AsyncSession, period: Period) -> list[Any]:
    """(session_id, job_id, round_id, score, confidence, recommendation, elapsed)
    of the completed sessions in the period."""
    latest = latest_scores_lateral()
    conditions = [*_sessions_in_period(period), InterviewSession.status == SessionStatus.COMPLETED.value]
    stmt = (
        select(
            InterviewSession.id,
            InterviewSession.job_id,
            InterviewSession.round_id,
            Score.score,
            Score.confidence,
            Score.details["recommendation"].astext,
            InterviewSession.elapsed_seconds,
        )
        .select_from(InterviewSession)
        .join(latest, true())
        .join(
            Score,
            and_(
                Score.session_id == InterviewSession.id,
                Score.created_at == latest.c.created_at,
                Score.category == ScoreCategory.OVERALL.value,
            ),
        )
        .where(*conditions)
    )
    return list((await db.execute(stmt)).all())


# -------------------------------------------------------------------- overview


async def overview(db: AsyncSession, period: Period) -> dict[str, Any]:
    now = period.until
    interviews = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Interview.status == InterviewStatus.COMPLETED.value),
            ).where(*_interview_cohort(period))
        )
    ).one()
    invited, interviews_completed = int(interviews[0]), int(interviews[1])
    active = await _count(
        db,
        select(Interview.id).where(
            *_interview_filters(period),
            Interview.status.in_([InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value]),
            Interview.invite_expires_at > now,
        ),
    )
    base = _session_filters(period)
    week_since = now - timedelta(days=7)
    is_completed = InterviewSession.status == SessionStatus.COMPLETED.value
    counts = (
        await db.execute(
            select(
                func.count().filter(InterviewSession.started_at.is_not(None), session_time() >= period.since),
                func.count().filter(is_completed, session_time() >= period.since),
                func.count().filter(is_completed, session_time() >= week_since),
            ).where(*base, session_time() >= min(period.since, week_since))
        )
    ).one()
    started, completed, completed_week = (int(v or 0) for v in counts)
    # The present, whatever the period: live sessions, review queue, unreviewed signals.
    live = await _count(
        db, select(InterviewSession.id).where(*base, InterviewSession.status.in_(LIVE_STATUSES))
    )
    awaiting_review = await _count(db, select(InterviewSession.id).where(*base, needs_review_clause()))
    integrity_unreviewed = await _count(
        db,
        select(IntegrityEvent.id)
        .join(InterviewSession, InterviewSession.id == IntegrityEvent.session_id)
        .where(
            *base,
            IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
            IntegrityEvent.action.in_(FLAG_ACTIONS),
        ),
    )
    in_period = _sessions_in_period(period)
    integrity_events = int(
        await db.scalar(
            select(func.coalesce(func.sum(IntegrityEvent.count), 0))
            .join(InterviewSession, InterviewSession.id == IntegrityEvent.session_id)
            .where(*in_period)
        )
        or 0
    )
    rows = await _latest_overall_rows(db, period)
    # Sessions without enough evidence have no overall score: they count in the recommendation
    # distribution (insufficient_evidence) but never in score/confidence averages.
    scores = [float(r[3]) for r in rows if r[3] is not None]
    confidences = [float(r[4]) for r in rows if r[3] is not None]
    durations = [float(r[6] or 0.0) for r in rows]
    if not durations:
        durations = [
            float(v or 0.0)
            for v in (
                await db.scalars(select(InterviewSession.elapsed_seconds).where(*in_period, is_completed))
            ).all()
        ]
    recommendation = Counter(r[5] for r in rows if r[5])
    reviews = (
        await db.execute(
            select(ReviewDecision.decision, ReviewDecision.overrides_ai)
            .join(InterviewSession, InterviewSession.id == ReviewDecision.session_id)
            .where(*in_period)
        )
    ).all()
    day = cast(func.date_trunc("day", session_time()), Date)
    by_day = (await db.execute(select(day, func.count()).where(*in_period).group_by(day).order_by(day))).all()
    return {
        "period": period.as_dict(),
        "period_days": period.days,
        "interviews_invited": invited,
        "interviews_completed": interviews_completed,
        "active_interviews": active,
        "sessions_live": live,
        "sessions_started": started,
        "sessions_completed": completed,
        "completed_this_week": completed_week,
        "awaiting_review": awaiting_review,
        "average_overall_score": round(statistics.fmean(scores), 2) if scores else None,
        "average_confidence": round(statistics.fmean(confidences), 3) if confidences else None,
        "completion_rate": _rate(completed, started),
        "integrity_unreviewed": integrity_unreviewed,
        "integrity_events": integrity_events,
        "average_duration_seconds": round(statistics.fmean(durations), 1) if durations else None,
        "recommendation_distribution": dict(recommendation),
        "review_decision_distribution": dict(Counter(r[0] for r in reviews)),
        "ai_override_rate": _rate(sum(1 for r in reviews if r[1]), len(reviews)),
        "sessions_by_day": [{"date": d.isoformat(), "count": int(c)} for d, c in by_day],
    }


# ---------------------------------------------------------------------- funnel


async def funnel(db: AsyncSession, period: Period) -> dict[str, Any]:
    conditions = _interview_cohort(period)
    started = exists().where(
        InterviewSession.interview_id == Interview.id, InterviewSession.started_at.is_not(None)
    )
    completed = exists().where(
        InterviewSession.interview_id == Interview.id,
        InterviewSession.status == SessionStatus.COMPLETED.value,
    )
    reviewed = (
        select(ReviewDecision.id)
        .join(InterviewSession, InterviewSession.id == ReviewDecision.session_id)
        .where(InterviewSession.interview_id == Interview.id)
        .exists()
    )
    released = (
        select(Report.id)
        .join(InterviewSession, InterviewSession.id == Report.session_id)
        .where(
            InterviewSession.interview_id == Interview.id,
            Report.type == ReportType.CANDIDATE.value,
            Report.released_to_candidate.is_(True),
        )
        .exists()
    )
    row = (
        await db.execute(
            select(
                func.count(),
                func.count(Interview.invite_opened_at),
                func.count(Interview.verified_at),
                func.sum(case((started, 1), else_=0)),
                func.sum(
                    case((completed | (Interview.status == InterviewStatus.COMPLETED.value), 1), else_=0)
                ),
                func.sum(case((reviewed, 1), else_=0)),
                func.sum(case((released, 1), else_=0)),
            ).where(*conditions)
        )
    ).one()
    counts = [int(v or 0) for v in row]
    labels = [
        ("invited", "Invited"),
        ("opened", "Opened invite"),
        ("verified", "Verified identity"),
        ("started", "Started interview"),
        ("completed", "Completed"),
        ("reviewed", "Human decision recorded"),
        ("report_released", "Feedback released"),
    ]
    stages = []
    for i, ((key, label), count) in enumerate(zip(labels, counts, strict=True)):
        stages.append(
            {
                "stage": key,
                "label": label,
                "count": count,
                "rate_from_previous": _rate(count, counts[i - 1]) if i else None,
                "rate_from_start": _rate(count, counts[0]) if i else None,
            }
        )
    return {"period": period.as_dict(), "stages": stages}


# ---------------------------------------------------------------------- scores


def _group(key: str, label: str, values: Sequence[float]) -> dict[str, Any]:
    buckets = [0] * 10
    for v in values:
        buckets[min(9, max(0, int(float(v) // 10)))] += 1
    return {
        "key": key,
        "label": label,
        "count": len(values),
        "mean": round(statistics.fmean(values), 2) if values else None,
        "median": round(statistics.median(values), 2) if values else None,
        "min": round(min(values), 2) if values else None,
        "max": round(max(values), 2) if values else None,
        "buckets": buckets,
    }


async def _labels(
    db: AsyncSession, job_ids: Iterable[uuid.UUID], round_ids: Iterable[uuid.UUID]
) -> tuple[dict, dict]:
    jobs = (
        dict((await db.execute(select(Job.id, Job.title).where(Job.id.in_(set(job_ids))))).all())
        if job_ids
        else {}
    )
    rounds = (
        dict((await db.execute(select(Round.id, Round.name).where(Round.id.in_(set(round_ids))))).all())
        if round_ids
        else {}
    )
    return jobs, rounds


async def score_distributions(db: AsyncSession, period: Period) -> dict[str, Any]:
    rows = await _latest_overall_rows(db, period)
    by_job: dict[uuid.UUID, list[float]] = defaultdict(list)
    by_round: dict[uuid.UUID, list[float]] = defaultdict(list)
    for _, job_id, round_id, score, *_rest in rows:
        if score is None:  # not enough evidence: no score to distribute
            continue
        by_job[job_id].append(float(score))
        by_round[round_id].append(float(score))
    jobs, rounds = await _labels(db, by_job, by_round)
    return {
        "period": period.as_dict(),
        "bucket_labels": BUCKET_LABELS,
        "overall": _group("all", "All sessions", [float(r[3]) for r in rows if r[3] is not None]),
        "by_job": sorted(
            (_group(str(k), jobs.get(k, "Unknown job"), v) for k, v in by_job.items()),
            key=lambda g: -g["count"],
        ),
        "by_round": sorted(
            (_group(str(k), rounds.get(k, "Unknown round"), v) for k, v in by_round.items()),
            key=lambda g: -g["count"],
        ),
    }


# ----------------------------------------------------------------- competencies


async def competency_averages(db: AsyncSession, period: Period) -> dict[str, Any]:
    """Per competency over each session's newest score set, aggregated in SQL.

    Averages use only the sessions that produced evidence for the competency
    and are ``None`` when none did (the neutral 5.0 starting estimate is not a
    measurement); ``sessions`` counts every session scored.
    """
    latest = latest_scores_lateral()
    evidenced = (Score.evidence_count > 0) & Score.score.isnot(None)
    stmt = (
        select(
            Score.key,
            func.count().label("sessions"),
            func.avg(Score.score).filter(evidenced).label("score"),
            func.avg(Score.confidence).filter(evidenced).label("confidence"),
            func.coalesce(func.sum(Score.evidence_count), 0).label("evidence_count"),
            func.count().filter(Score.sufficient_evidence.is_(True)).label("sufficient"),
            func.max(Score.label).label("label"),
        )
        .select_from(InterviewSession)
        .join(latest, true())
        .join(
            Score,
            and_(
                Score.session_id == InterviewSession.id,
                Score.created_at == latest.c.created_at,
                Score.category == ScoreCategory.COMPETENCY.value,
            ),
        )
        .where(*_sessions_in_period(period))
        .group_by(Score.key)
    )
    rows = (await db.execute(stmt)).all()
    names = await competency_names(db, period.organization_id, [r.key for r in rows])
    items = [
        {
            "competency_key": r.key,
            "name": names.get(r.key) or r.label,
            "average_score": round(float(r.score), 2) if r.score is not None else None,
            "average_confidence": round(float(r.confidence), 3) if r.confidence is not None else None,
            "sessions": int(r.sessions),
            "evidence_count": int(r.evidence_count),
            "sufficient_rate": round(int(r.sufficient) / int(r.sessions), 4),
        }
        for r in rows
    ]
    items.sort(key=lambda i: (-i["sessions"], i["competency_key"]))
    return {"period": period.as_dict(), "items": items}


# ------------------------------------------------------------------ completion


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 1)


def _completion(key: str, label: str, rows: Sequence[Any]) -> dict[str, Any]:
    started = [r for r in rows if r.started_at is not None]
    completed = [r for r in rows if r.status == SessionStatus.COMPLETED.value]
    durations = [float(r.elapsed_seconds or 0.0) for r in completed]
    return {
        "key": key,
        "label": label,
        "started": len(started),
        "completed": len(completed),
        "completion_rate": _rate(
            len([r for r in started if r.status == SessionStatus.COMPLETED.value]), len(started)
        ),
        "average_duration_seconds": round(statistics.fmean(durations), 1) if durations else None,
        "median_duration_seconds": _percentile(durations, 0.5),
        "p90_duration_seconds": _percentile(durations, 0.9),
    }


async def completion(db: AsyncSession, period: Period) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                InterviewSession.job_id,
                InterviewSession.round_id,
                InterviewSession.status,
                InterviewSession.started_at,
                InterviewSession.elapsed_seconds,
                InterviewSession.end_reason,
            ).where(*_sessions_in_period(period))
        )
    ).all()
    by_job: dict[uuid.UUID, list[Any]] = defaultdict(list)
    by_round: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for r in rows:
        by_job[r.job_id].append(r)
        by_round[r.round_id].append(r)
    jobs, rounds = await _labels(db, by_job, by_round)
    return {
        "period": period.as_dict(),
        "overall": _completion("all", "All sessions", rows),
        "by_job": [_completion(str(k), jobs.get(k, "Unknown job"), v) for k, v in by_job.items()],
        "by_round": [_completion(str(k), rounds.get(k, "Unknown round"), v) for k, v in by_round.items()],
        "end_reasons": dict(Counter(r.end_reason for r in rows if r.end_reason)),
        "status_counts": dict(Counter(r.status for r in rows)),
    }


# ------------------------------------------------------------------- integrity


async def integrity(db: AsyncSession, period: Period) -> dict[str, Any]:
    in_period = _sessions_in_period(period)
    sessions = await _count(
        db, select(InterviewSession.id).where(*in_period, InterviewSession.started_at.is_not(None))
    )
    events = (
        await db.execute(
            select(
                IntegrityEvent.session_id,
                IntegrityEvent.type,
                IntegrityEvent.severity,
                IntegrityEvent.source,
                IntegrityEvent.review_status,
                IntegrityEvent.action,
                IntegrityEvent.count,
            )
            .join(InterviewSession, InterviewSession.id == IntegrityEvent.session_id)
            .where(*in_period)
        )
    ).all()
    by_type: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    review: Counter[str] = Counter()
    with_events: set[uuid.UUID] = set()
    with_flags: set[uuid.UUID] = set()
    total = 0
    for sid, kind, severity, source, status, action, count in events:
        weight = int(count or 1)
        total += weight
        by_type[kind] += weight
        by_severity[severity] += weight
        by_source[source] += weight
        review[status] += 1
        with_events.add(sid)
        if action in FLAG_ACTIONS:
            with_flags.add(sid)
    denominator = max(sessions, len(with_events))
    return {
        "period": period.as_dict(),
        "note": INTEGRITY_NOTE,
        "sessions": sessions,
        "sessions_with_events": len(with_events),
        "sessions_with_flags": len(with_flags),
        "flag_rate": _rate(len(with_flags), denominator),
        "events_total": total,
        "events_per_session": round(total / denominator, 3) if denominator else None,
        "by_type": dict(by_type.most_common()),
        "by_severity": dict(by_severity),
        "by_source": dict(by_source),
        "review_status": dict(review),
    }


# ----------------------------------------------------------------------- hints


async def hint_usage(db: AsyncSession, period: Period) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                CodingSession.id,
                CodingSession.hints_used,
                CodingSession.coding_question_id,
                CodingQuestion.title,
            )
            .join(InterviewSession, InterviewSession.id == CodingSession.session_id)
            .outerjoin(CodingQuestion, CodingQuestion.id == CodingSession.coding_question_id)
            .where(*_sessions_in_period(period))
        )
    ).all()
    ids = [r[0] for r in rows]
    levels = (
        dict(
            (
                await db.execute(
                    select(CodingHint.level, func.count())
                    .where(CodingHint.coding_session_id.in_(ids))
                    .group_by(CodingHint.level)
                )
            ).all()
        )
        if ids
        else {}
    )
    hints = [int(r[1] or 0) for r in rows]
    by_problem: dict[str, list[int]] = defaultdict(list)
    titles: dict[str, str] = {}
    for _, used, question_id, title in rows:
        by_problem[str(question_id)].append(int(used or 0))
        titles[str(question_id)] = title or "Coding problem"
    return {
        "period": period.as_dict(),
        "coding_sessions": len(rows),
        "sessions_with_hints": sum(1 for h in hints if h > 0),
        "hint_rate": _rate(sum(1 for h in hints if h > 0), len(hints)),
        "average_hints": round(statistics.fmean(hints), 2) if hints else None,
        "distribution": {str(k): sum(1 for h in hints if min(h, 3) == k) for k in range(4)},
        "by_level": {str(level): int(count) for level, count in sorted(levels.items())},
        "by_problem": sorted(
            (
                {
                    "coding_question_id": qid,
                    "title": titles[qid],
                    "sessions": len(values),
                    "average_hints": round(statistics.fmean(values), 2),
                    "hint_rate": _rate(sum(1 for v in values if v > 0), len(values)),
                }
                for qid, values in by_problem.items()
            ),
            key=lambda p: -p["sessions"],
        ),
    }


# ----------------------------------------------------------------- model usage


async def model_usage(db: AsyncSession, period: Period) -> dict[str, Any]:
    """Model calls made in the period (event time; most calls - resume
    processing, embeddings - belong to no session)."""
    is_error = case((ModelCall.status.in_(ERROR_STATUSES), 1), else_=0)
    stmt = (
        select(
            ModelCall.task,
            ModelCall.endpoint,
            func.count(),
            func.sum(is_error),
            func.sum(case((ModelCall.used_fallback.is_(True), 1), else_=0)),
            func.sum(case((ModelCall.repaired.is_(True), 1), else_=0)),
            func.percentile_cont(0.5).within_group(ModelCall.latency_ms),
            func.percentile_cont(0.95).within_group(ModelCall.latency_ms),
            func.avg(ModelCall.latency_ms),
            func.avg(ModelCall.ttft_ms),
            func.coalesce(func.sum(ModelCall.prompt_tokens), 0),
            func.coalesce(func.sum(ModelCall.completion_tokens), 0),
        )
        .where(ModelCall.organization_id == period.organization_id, ModelCall.created_at >= period.since)
        .group_by(ModelCall.task, ModelCall.endpoint)
        .order_by(func.count().desc())
    )
    if period.job_id is not None or period.round_id is not None:
        session_ids = select(InterviewSession.id).where(*_session_filters(period))
        stmt = stmt.where(ModelCall.session_id.in_(session_ids))
    rows = (await db.execute(stmt)).all()

    def _ms(value: Any) -> float | None:
        return round(float(value), 1) if value is not None else None

    items = [
        {
            "task": task,
            "endpoint": endpoint,
            "calls": int(calls),
            "errors": int(errors or 0),
            "error_rate": _rate(int(errors or 0), int(calls)),
            "fallbacks": int(fallbacks or 0),
            "repaired": int(repaired or 0),
            "p50_ms": _ms(p50),
            "p95_ms": _ms(p95),
            "avg_ms": _ms(avg),
            "avg_ttft_ms": _ms(ttft),
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion_tokens),
        }
        for task, endpoint, calls, errors, fallbacks, repaired, p50, p95, avg, ttft, prompt, completion_tokens in rows
    ]
    total_calls = sum(i["calls"] for i in items)
    total_errors = sum(i["errors"] for i in items)
    return {
        "period": period.as_dict(),
        "items": items,
        "totals": {
            "calls": total_calls,
            "errors": total_errors,
            "error_rate": _rate(total_errors, total_calls),
            "prompt_tokens": sum(i["prompt_tokens"] for i in items),
            "completion_tokens": sum(i["completion_tokens"] for i in items),
        },
    }
