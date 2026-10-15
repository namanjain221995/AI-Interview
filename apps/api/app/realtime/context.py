"""Session context for the realtime runtime, loaded from Postgres.

Everything the transport layer needs that is not part of ``InterviewState``:
identifiers, frozen policy + consent, round label, invite expiry, the
Whisper vocabulary (job requirements + candidate profile technologies), and
the transport bookkeeping columns of ``interview_sessions``
(``reconnect_count``, ``client_info``, ``last_heartbeat_at``) which the
runtime owns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update

from app.core.logging import get_logger
from app.db.models import (
    Candidate,
    CandidateProfile,
    Competency,
    Interview,
    InterviewSession,
    Job,
    JobRequirement,
    Round,
)
from app.db.session import session_scope
from app.domain.enums import SessionStatus
from app.realtime.policy import SessionPolicy, latest_consent, session_policy

log = get_logger(__name__)

MAX_VOCABULARY = 40
MAX_GLOSSARY_TERMS = 300
TERMINAL_STATUSES = frozenset({SessionStatus.COMPLETED, SessionStatus.ABORTED, SessionStatus.FAILED})
ENDED_STATUSES = TERMINAL_STATUSES | {SessionStatus.COMPLETING}


def first_name(full_name: str | None) -> str | None:
    if not full_name:
        return None
    part = full_name.strip().split()
    return part[0][:60] if part else None


@dataclass(slots=True)
class SessionContext:
    session_id: str
    interview_id: str
    organization_id: str
    candidate_id: str
    job_id: str
    round_id: str
    status: SessionStatus
    round_name: str
    round_type: str
    time_limit_s: float
    max_time_s: float
    policy: SessionPolicy
    invite_expires_at: datetime
    interview_status: str
    job_title: str = ""
    candidate_first_name: str | None = None
    vocabulary: list[str] = field(default_factory=list)  # Whisper prompt (bounded)
    glossary_terms: list[str] = field(default_factory=list)  # transcript correction (larger)
    reconnect_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def invite_expired(self) -> bool:
        expires = self.invite_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= datetime.now(UTC)

    @property
    def in_progress(self) -> bool:
        """A started session that is live or paused: reconnects outlive the invitation's start deadline (S7)."""
        return self.started_at is not None and self.status in (SessionStatus.LIVE, SessionStatus.PAUSED)

    @property
    def access_expired(self) -> bool:
        return self.invite_expired and not self.in_progress

    @property
    def ended(self) -> bool:
        return self.status in ENDED_STATUSES


def _profile_terms(profile: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    skills = profile.get("skills")
    if isinstance(skills, dict):
        for items in skills.values():
            for item in items or []:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    if item.get("category") != "soft":
                        terms.append(item["name"])
    for role in profile.get("roles") or []:
        if isinstance(role, dict):
            terms.extend(t for t in role.get("technologies") or [] if isinstance(t, str))
    for project in profile.get("projects") or []:
        if isinstance(project, dict):
            terms.extend(t for t in project.get("technologies") or [] if isinstance(t, str))
    return terms


def intake_glossary_terms() -> list[str]:
    """Technical terms of the intake kit passages (Drill A / Drill B): Whisper ignores prompts, so the reading
    transcript is corrected towards these terms by the session glossary (INTAKE_INTERVIEW.md 7.4)."""
    try:
        from app.services.intake.kit import load_intake_kit

        kit = load_intake_kit()
    except Exception as exc:  # noqa: BLE001 - the glossary is an optimisation
        log.warning("realtime.intake_terms_unavailable", error=type(exc).__name__)
        return []
    return [term for passage in kit.passages for term in passage.terms]


def merge_vocabulary(*groups: list[str], limit: int = MAX_VOCABULARY) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for term in group:
            cleaned = " ".join(str(term).split())[:60]
            key = cleaned.lower()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            out.append(cleaned)
            if len(out) >= limit:
                return out
    return out


async def load_session_context(session_id: uuid.UUID) -> SessionContext | None:
    async with session_scope() as db:
        row = await db.get(InterviewSession, session_id)
        if row is None:
            return None
        interview = await db.get(Interview, row.interview_id)
        if interview is None:
            return None
        round_ = await db.get(Round, row.round_id)
        job = await db.get(Job, row.job_id)
        candidate = await db.get(Candidate, row.candidate_id)
        consent = await latest_consent(db, interview.id)
        requirement_names = list(
            (
                await db.scalars(
                    select(JobRequirement.name)
                    .where(JobRequirement.job_id == row.job_id)
                    .order_by(JobRequirement.sort_order, JobRequirement.weight.desc())
                    .limit(30)
                )
            ).all()
        )
        catalog_names = list(
            (
                await db.scalars(
                    select(Competency.name)
                    .where(
                        Competency.is_active.is_(True),
                        or_(
                            Competency.organization_id.is_(None),
                            Competency.organization_id == row.organization_id,
                        ),
                    )
                    .limit(200)
                )
            ).all()
        )
        profile_terms: list[str] = []
        if row.profile_id is not None:
            profile = await db.get(CandidateProfile, row.profile_id)
            if profile is not None and isinstance(profile.profile, dict):
                profile_terms = _profile_terms(profile.profile)
        config = row.round_config if isinstance(row.round_config, dict) else {}
        competency_names = [
            str(c.get("name") or c.get("key"))
            for c in config.get("competencies") or []
            if isinstance(c, dict) and (c.get("name") or c.get("key"))
        ]
        duration_min = float(config.get("duration_minutes") or (round_.duration_minutes if round_ else 30))
        max_min = float(
            config.get("max_duration_minutes") or (round_.max_duration_minutes if round_ else duration_min)
        )
        try:
            status = SessionStatus(row.status)
        except ValueError:
            status = SessionStatus.CREATED
        round_type = str(config.get("round_type") or (round_.round_type if round_ else "custom"))
        intake_terms = intake_glossary_terms() if round_type == "intake" else []
        return SessionContext(
            session_id=str(row.id),
            interview_id=str(row.interview_id),
            organization_id=str(row.organization_id),
            candidate_id=str(row.candidate_id),
            job_id=str(row.job_id),
            round_id=str(row.round_id),
            status=status,
            round_name=str(config.get("name") or (round_.name if round_ else "Interview")),
            round_type=str(config.get("round_type") or (round_.round_type if round_ else "custom")),
            time_limit_s=duration_min * 60.0,
            max_time_s=max(max_min, duration_min) * 60.0,
            policy=session_policy(interview.policy_snapshot, consent),
            invite_expires_at=interview.invite_expires_at,
            interview_status=interview.status,
            job_title=job.title if job else "",
            candidate_first_name=first_name(candidate.full_name if candidate else None),
            vocabulary=merge_vocabulary(requirement_names, competency_names, profile_terms),
            glossary_terms=merge_vocabulary(
                intake_terms,
                profile_terms,
                requirement_names,
                competency_names,
                catalog_names,
                limit=MAX_GLOSSARY_TERMS,
            ),
            reconnect_count=row.reconnect_count or 0,
            started_at=row.started_at,
            ended_at=row.ended_at,
            end_reason=row.end_reason,
            elapsed_seconds=float(row.elapsed_seconds or 0.0),
        )


async def refresh_status(session_id: uuid.UUID) -> tuple[SessionStatus | None, str | None]:
    async with session_scope() as db:
        result = (
            await db.execute(
                select(InterviewSession.status, InterviewSession.end_reason).where(
                    InterviewSession.id == session_id
                )
            )
        ).first()
    if result is None:
        return None, None
    try:
        return SessionStatus(result[0]), result[1]
    except ValueError:
        return None, result[1]


async def record_connection(
    session_id: uuid.UUID, *, reconnect: bool, client_info: dict[str, Any] | None = None
) -> int:
    """Bump ``reconnect_count`` (on reconnects) and store ``client_info``; returns the count."""
    values: dict[str, Any] = {"last_heartbeat_at": datetime.now(UTC)}
    if client_info is not None:
        values["client_info"] = client_info
    if reconnect:
        values["reconnect_count"] = InterviewSession.reconnect_count + 1
    async with session_scope() as db:
        result = await db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(**values)
            .returning(InterviewSession.reconnect_count)
        )
        count = result.scalar_one_or_none()
    return int(count or 0)


async def touch_heartbeat(session_id: uuid.UUID) -> None:
    async with session_scope() as db:
        await db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session_id)
            .values(last_heartbeat_at=datetime.now(UTC))
        )
