"""Read helpers shared by the report pipeline and the recruiter-facing APIs
(sessions, reports, reviews, analytics)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import ColumnElement, and_, exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.logging import get_logger
from app.core.rbac import Principal
from app.db.models import (
    Interview,
    InterviewSession,
    Job,
    MediaAsset,
    Organization,
    Report,
    ReviewDecision,
    Score,
)
from app.domain.enums import (
    CandidateReportMode,
    MediaKind,
    Recommendation,
    ReportType,
    ScoreCategory,
    SessionStatus,
)
from app.schemas.policies import OrganizationSettings

log = get_logger(__name__)

TERMINAL_STATUSES = frozenset(
    {SessionStatus.COMPLETED.value, SessionStatus.ABORTED.value, SessionStatus.FAILED.value}
)
# Sessions a report can be (re)generated for: every ended session. Must equal
# ``app.workers.reports.REPORTABLE`` (the worker accepts FAILED sessions too:
# a technical failure still leaves evidence a reviewer may need summarised).
REPORTABLE_STATUSES = frozenset(
    {
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
        SessionStatus.ABORTED.value,
        SessionStatus.FAILED.value,
    }
)
REVIEWABLE_STATUSES = frozenset(
    {
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
        SessionStatus.ABORTED.value,
        SessionStatus.FAILED.value,
    }
)
LIVE_STATUSES = frozenset({SessionStatus.LIVE.value, SessionStatus.PAUSED.value})
DEFAULT_REPORT_RETENTION_DAYS = 730


def as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def get_session_for_principal(
    db: AsyncSession, principal: Principal, session_id: uuid.UUID
) -> InterviewSession:
    """The session when the staff principal may see it (cross-org looks like 404)."""
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise NotFound("Session not found.")
    if not principal.is_super_admin and session.organization_id != principal.organization_id:
        raise NotFound("Session not found.")
    return session


# ------------------------------------------------------------ list filters


def needs_review_clause() -> ColumnElement[bool]:
    """Completed session without any human review decision yet (the review queue).

    Same definition as the dashboard KPI ``awaiting_review``.
    """
    return and_(
        InterviewSession.status == SessionStatus.COMPLETED.value,
        ~exists().where(ReviewDecision.session_id == InterviewSession.id),
    )


def not_needs_review_clause() -> ColumnElement[bool]:
    return not_(needs_review_clause())


def current_recommendation_expr() -> ColumnElement[Any]:
    """The assistive recommendation a session list shows (correlated to
    ``InterviewSession``): the newest recruiter report's when it has one, else
    the newest overall score's. Mirrors ``views.session_summaries``."""
    from_report = (
        select(Report.recommendation)
        .where(Report.session_id == InterviewSession.id, Report.type == ReportType.RECRUITER.value)
        .order_by(Report.version.desc())
        .limit(1)
        .correlate(InterviewSession)
        .scalar_subquery()
    )
    from_score = (
        select(Score.details["recommendation"].astext)
        .where(Score.session_id == InterviewSession.id, Score.category == ScoreCategory.OVERALL.value)
        .order_by(Score.created_at.desc())
        .limit(1)
        .correlate(InterviewSession)
        .scalar_subquery()
    )
    return func.coalesce(from_report, from_score)


# ------------------------------------------------------------------ scores


async def latest_overall(db: AsyncSession, session_id: uuid.UUID) -> Score | None:
    return await db.scalar(
        select(Score)
        .where(Score.session_id == session_id, Score.category == ScoreCategory.OVERALL.value)
        .order_by(Score.created_at.desc())
        .limit(1)
    )


def recommendation_of(score: Score | None) -> Recommendation | None:
    if score is None:
        return None
    value = (score.details or {}).get("recommendation")
    try:
        return Recommendation(value) if value else None
    except ValueError:
        return None


async def current_recommendation(db: AsyncSession, session_id: uuid.UUID) -> Recommendation | None:
    """Latest assistive recommendation: newest recruiter report, else newest overall score."""
    report = await db.scalar(
        select(Report)
        .where(
            Report.session_id == session_id,
            Report.type == ReportType.RECRUITER.value,
            Report.status == "ready",
        )
        .order_by(Report.version.desc())
        .limit(1)
    )
    if report is not None and report.recommendation:
        try:
            return Recommendation(report.recommendation)
        except ValueError:
            pass
    return recommendation_of(await latest_overall(db, session_id))


# ----------------------------------------------------------------- reports


async def latest_report(
    db: AsyncSession, session_id: uuid.UUID, report_type: ReportType | str, *, version: int | None = None
) -> Report | None:
    stmt = select(Report).where(Report.session_id == session_id, Report.type == ReportType(report_type).value)
    if version is not None:
        stmt = stmt.where(Report.version == version)
    return await db.scalar(stmt.order_by(Report.version.desc()).limit(1))


async def latest_ready_report(
    db: AsyncSession, session_id: uuid.UUID, report_type: ReportType | str
) -> Report | None:
    return await db.scalar(
        select(Report)
        .where(
            Report.session_id == session_id,
            Report.type == ReportType(report_type).value,
            Report.status == "ready",
        )
        .order_by(Report.version.desc())
        .limit(1)
    )


REPORT_VERSION_COLUMNS = (
    Report.id,
    Report.session_id,
    Report.type,
    Report.version,
    Report.status,
    Report.generated_at,
    Report.released_to_candidate,
    Report.released_at,
    Report.created_at,
)


async def report_versions(
    db: AsyncSession, session_id: uuid.UUID, report_type: ReportType | str | None = None
) -> list[Any]:
    """Version metadata rows (``REPORT_VERSION_COLUMNS``; no report content)."""
    stmt = select(*REPORT_VERSION_COLUMNS).where(Report.session_id == session_id)
    if report_type is not None:
        stmt = stmt.where(Report.type == ReportType(report_type).value)
    return list((await db.execute(stmt.order_by(Report.type, Report.version.desc()))).all())


async def get_released_candidate_report(db: AsyncSession, session_id: uuid.UUID | str) -> Report | None:
    """Newest candidate report released to the candidate (portal use)."""
    return await db.scalar(
        select(Report)
        .where(
            Report.session_id == as_uuid(session_id),
            Report.type == ReportType.CANDIDATE.value,
            Report.status == "ready",
            Report.released_to_candidate.is_(True),
        )
        .order_by(Report.version.desc())
        .limit(1)
    )


async def media_ids_for_keys(db: AsyncSession, keys: Iterable[str | None]) -> dict[str, uuid.UUID]:
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(MediaAsset.storage_key, MediaAsset.id).where(
                MediaAsset.storage_key.in_(wanted), MediaAsset.deleted_at.is_(None)
            )
        )
    ).all()
    return {key: media_id for key, media_id in rows}


async def answer_audio_media(db: AsyncSession, session_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """answer id -> media id of its stored audio (live rows only)."""
    from app.db.models import Answer

    answers = (
        await db.execute(
            select(Answer.id, Answer.audio_storage_key).where(
                Answer.session_id == session_id, Answer.audio_storage_key.is_not(None)
            )
        )
    ).all()
    by_key = await media_ids_for_keys(db, [key for _, key in answers])
    out = {answer_id: by_key[key] for answer_id, key in answers if key in by_key}
    if len(out) < len(answers):
        # Audio registered without an exact key on the answer row: match on details.answer_id.
        rows = (
            await db.execute(
                select(MediaAsset.id, MediaAsset.details).where(
                    MediaAsset.session_id == session_id,
                    MediaAsset.kind == MediaKind.ANSWER_AUDIO.value,
                    MediaAsset.deleted_at.is_(None),
                )
            )
        ).all()
        for media_id, details in rows:
            raw = (details or {}).get("answer_id")
            try:
                answer_id = uuid.UUID(str(raw)) if raw else None
            except ValueError:
                answer_id = None
            if answer_id is not None:
                out.setdefault(answer_id, media_id)
    return out


# ------------------------------------------------------------------ policy


async def resolve_candidate_report_mode(db: AsyncSession, session: InterviewSession) -> CandidateReportMode:
    """Policy frozen at invite time, then the job, then the organization. Intake interviews never produce a
    candidate report, so they never promise one (S21: no endless "preparing feedback" spinner)."""
    if (session.round_config or {}).get("round_type") == "intake":
        return CandidateReportMode.NEVER
    interview = await db.get(Interview, session.interview_id)
    snapshot = (interview.policy_snapshot if interview is not None else None) or {}
    raw = snapshot.get("candidate_report_mode")
    if raw:
        try:
            return CandidateReportMode(raw)
        except ValueError:
            pass
    job = await db.get(Job, session.job_id)
    if job is not None and job.candidate_report_mode:
        try:
            return CandidateReportMode(job.candidate_report_mode)
        except ValueError:
            pass
    org = await db.get(Organization, session.organization_id)
    try:
        return OrganizationSettings.model_validate(
            (org.settings if org else None) or {}
        ).candidate_report_mode
    except ValidationError:
        return CandidateReportMode.NEVER  # the platform default: never promise feedback


async def report_retention_days(db: AsyncSession, session: InterviewSession) -> int:
    interview = await db.get(Interview, session.interview_id)
    snapshot = (interview.policy_snapshot if interview is not None else None) or {}
    days = (snapshot.get("retention") or {}).get("report_days")
    if isinstance(days, int) and days > 0:
        return days
    org = await db.get(Organization, session.organization_id)
    try:
        return OrganizationSettings.model_validate(
            (org.settings if org else None) or {}
        ).retention.report_days
    except ValidationError:
        return DEFAULT_REPORT_RETENTION_DAYS


# ------------------------------------------------------------------ reviews


async def latest_review(db: AsyncSession, session_id: uuid.UUID) -> ReviewDecision | None:
    return await db.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.session_id == session_id)
        .order_by(ReviewDecision.created_at.desc())
        .limit(1)
    )


async def review_count(db: AsyncSession, session_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(ReviewDecision).where(ReviewDecision.session_id == session_id)
        )
        or 0
    )


# ------------------------------------------------------------- state digest


def state_key(session_id: uuid.UUID | str) -> str:
    return f"session:{session_id}:state"


async def load_live_state(session: InterviewSession) -> dict[str, Any]:
    """Hot Redis copy of the InterviewState for live sessions, else the
    Postgres snapshot (whichever has the higher version)."""
    state: dict[str, Any] = dict(session.state or {})
    if session.status in TERMINAL_STATUSES:
        return state
    try:
        from app.core.redis import get_redis

        raw = await get_redis().get(state_key(session.id))
    except (RedisError, OSError, RuntimeError) as exc:
        log.warning("sessions.state_redis_unavailable", session_id=str(session.id), error=type(exc).__name__)
        return state
    if not raw:
        return state
    try:
        hot = json.loads(raw)
    except (TypeError, ValueError):
        return state
    if isinstance(hot, dict) and int(hot.get("version", 0) or 0) >= int(state.get("version", 0) or 0):
        return hot
    return state


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def elapsed_from_state(state: dict[str, Any], session: InterviewSession) -> float:
    """Interview clock (pauses excluded) from InterviewState time fields."""
    from datetime import UTC, datetime

    before = _num(state.get("elapsed_before_pause_s"), 0.0)
    resumed = state.get("last_resumed_at")
    if resumed:
        try:
            resumed_at = datetime.fromisoformat(str(resumed).replace("Z", "+00:00"))
            before += max(0.0, (datetime.now(UTC) - resumed_at).total_seconds())
        except ValueError:
            pass
    return max(before, _num(session.elapsed_seconds, 0.0))


def state_digest(
    state: dict[str, Any], session: InterviewSession, *, conversation_turns: int = 0
) -> dict[str, Any] | None:
    """Compact, staff-facing digest of an InterviewState document."""
    if not state:
        return None
    competencies = []
    for key, comp in (state.get("competencies") or {}).items():
        if not isinstance(comp, dict):
            continue
        assessed = int(comp.get("evidence_count", 0) or 0) > 0 or _num(comp.get("confidence")) > 0
        competencies.append(
            {
                "key": key,
                "name": comp.get("name"),
                "weight": comp.get("weight", 1.0),
                "required": comp.get("required", True),
                # Nothing observed yet: no estimate (the engine's neutral start is not a score).
                "score_estimate": round(_num(comp.get("score_estimate"), 5.0), 2) if assessed else None,
                "confidence": round(_num(comp.get("confidence")), 3),
                "evidence_count": int(comp.get("evidence_count", 0) or 0),
                "sufficient_evidence": bool(comp.get("sufficient_evidence", False)),
            }
        )
    claims = []
    for claim_id, claim in (state.get("claims") or {}).items():
        if not isinstance(claim, dict):
            continue
        inner = claim.get("claim") or {}
        claims.append(
            {"id": claim_id, "claim": inner.get("claim", ""), "status": claim.get("status", "unverified")}
        )
    questions = state.get("questions") or []
    elapsed = elapsed_from_state(state, session)
    time_limit = _num(state.get("time_limit_s"), 0.0)
    integrity = state.get("integrity") or {}
    current = None
    current_id = state.get("current_question_id")
    for q in reversed(questions):
        if isinstance(q, dict) and q.get("id") == current_id:
            current = {
                "id": q.get("id"),
                "text": q.get("text"),
                "competency_key": q.get("competency_key"),
                "difficulty": q.get("difficulty"),
                "topic": q.get("topic"),
            }
            break
    digest: dict[str, Any] = {
        "status": state.get("status", session.status),
        "turn_state": state.get("turn_state", "idle"),
        "avatar_state": state.get("avatar_state", "IDLE"),
        "current_segment": state.get("current_segment"),
        "current_competency": state.get("current_competency"),
        "current_difficulty": state.get("current_difficulty"),
        "current_question": current,
        "question_count": len(questions),
        "answer_count": len(state.get("answers") or []),
        "elapsed_s": round(elapsed, 1),
        "remaining_s": round(max(0.0, time_limit - elapsed), 1) if time_limit else 0.0,
        "competencies": competencies,
        "claims": claims,
        "contradictions": [
            {
                "id": c.get("id"),
                "description": c.get("description"),
                "severity": c.get("severity"),
                "status": c.get("status"),
            }
            for c in (state.get("contradictions") or [])
            if isinstance(c, dict)
        ],
        "integrity": {
            "counts": dict(integrity.get("counts") or {}),
            "warnings_issued": int(integrity.get("warnings_issued", 0) or 0),
            "flagged_for_review": int(integrity.get("flagged_for_review", 0) or 0),
        },
        "coding": {
            "active": bool((state.get("coding") or {}).get("active", False)),
            "completed": bool((state.get("coding") or {}).get("completed", False)),
            "title": (state.get("coding") or {}).get("title"),
            "hints_used": int((state.get("coding") or {}).get("hints_used", 0) or 0),
        },
        "pending_evaluations": len(state.get("pending_evaluations") or []),
        "version": int(state.get("version", 0) or 0),
    }
    if conversation_turns:
        turns = [t for t in (state.get("conversation") or []) if isinstance(t, dict)][-conversation_turns:]
        digest["conversation"] = [
            {
                "speaker": t.get("speaker"),
                "text": t.get("text"),
                "at_s": t.get("at_s"),
                "interrupted": t.get("interrupted", False),
            }
            for t in turns
        ]
        digest["partial_transcript"] = state.get("partial_transcript", "")
    return digest
