"""Session lifecycle for the candidate portal (``SessionService`` contract).

``create_or_resume`` returns the open session of an interview (status
created/preparing/ready/live/paused) or creates one: the round's
``RoundConfig`` is frozen into ``interview_sessions.round_config``, the
interview's frozen policy snapshot decides recording/monitoring/report
behaviour, the latest ready ``CandidateProfile`` for the candidate (and job)
is linked, ``InterviewState`` is initialised with the round's competencies and
the profile's claims to verify, and ``session.prepare`` is enqueued (in the
same transaction). ``candidate_view`` is the candidate-safe summary.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import get_logger
from app.core.queue import enqueue
from app.db.models import (
    Candidate,
    CandidateProfile,
    Competency,
    Consent,
    Interview,
    InterviewSession,
    Job,
    JobRequirement,
    Organization,
    Report,
    Round,
)
from app.domain.enums import (
    CandidateReportMode,
    ClaimStatus,
    Difficulty,
    InterviewStatus,
    ReportStatus,
    ReportType,
    SessionStatus,
)
from app.schemas.ai import ClaimToVerify
from app.schemas.policies import PolicySnapshot
from app.schemas.rounds import RoundConfig
from app.services.interview.config import EngineConfig, get_engine_config
from app.services.interview.records import log_event
from app.services.interview.state import ClaimState, CodingState, CompetencyState, InterviewState
from app.services.interview.state_store import StateStore

log = get_logger(__name__)

OPEN_STATUSES = (
    SessionStatus.CREATED.value,
    SessionStatus.PREPARING.value,
    SessionStatus.READY.value,
    SessionStatus.LIVE.value,
    SessionStatus.PAUSED.value,
)
DONE_STATUSES = (SessionStatus.COMPLETING.value, SessionStatus.COMPLETED.value)
MAX_CLAIMS_IN_STATE = 15
_MAX_DESCRIPTION = 4000


def load_policy_snapshot(raw: dict[str, Any] | None) -> PolicySnapshot:
    """Tolerant parse of interviews.policy_snapshot (unknown keys ignored)."""
    data = dict(raw or {})
    allowed = set(PolicySnapshot.model_fields)
    cleaned = {k: v for k, v in data.items() if k in allowed}
    try:
        return PolicySnapshot.model_validate(cleaned)
    except ValueError:
        log.warning("engine.policy_snapshot_invalid")
        return PolicySnapshot()


def monitoring_enabled(policy: PolicySnapshot) -> bool:
    integrity = policy.integrity
    return bool(
        integrity.enabled
        and (
            integrity.monitor_focus
            or integrity.require_fullscreen
            or integrity.camera_frame_analysis
            or integrity.screen_share_required
            or integrity.monitor_clipboard
        )
    )


def round_config_from_row(round_row: Round) -> RoundConfig:
    """The round's ``RoundConfig``. A malformed stored config (for example a
    legacy row whose ``round_type`` is outside the enum) is a 409
    ``policy_invalid`` for the candidate, never a 500."""
    config = dict(round_row.config or {})
    for key, value in (
        ("round_type", round_row.round_type),
        ("name", round_row.name),
        ("duration_minutes", round_row.duration_minutes),
        ("max_duration_minutes", round_row.max_duration_minutes),
        ("expected_difficulty", round_row.expected_difficulty),
    ):
        if value is not None:
            config.setdefault(key, value)
    if round_row.instructions and "instructions" not in config:
        config["instructions"] = round_row.instructions
    try:
        return RoundConfig.model_validate(config)
    except ValidationError as exc:
        log.error(
            "engine.round_config_invalid",
            round_id=str(round_row.id),
            errors=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()][:10],
        )
        raise Conflict(
            "This interview round is misconfigured. Please contact the recruiter.", code="policy_invalid"
        ) from exc


USABLE_PROFILE_STATUSES = ("ready", "partial")  # partial = deterministic fallback profile
PENDING_RESUME_STATUSES = ("uploaded", "validating", "extracting", "ocr", "analyzing", "ready")


async def latest_profile(
    db: AsyncSession, candidate_id: uuid.UUID, job_id: uuid.UUID | None
) -> CandidateProfile | None:
    """Latest usable (ready|partial) profile, preferring the job-specific one.
    Uses the resume module's ``ResumeService.latest_profile`` when available."""
    try:
        from app.services.resume.service import get_resume_service

        service = get_resume_service()
    except Exception:  # noqa: BLE001 - resume module missing or broken: query directly
        service = None
    if service is not None:
        try:
            return await service.latest_profile(db, candidate_id, job_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("engine.resume_service_profile_failed", error=f"{type(exc).__name__}: {exc}"[:200])
    base = (
        select(CandidateProfile)
        .where(
            CandidateProfile.candidate_id == candidate_id,
            CandidateProfile.status.in_(USABLE_PROFILE_STATUSES),
        )
        .order_by(CandidateProfile.version.desc(), CandidateProfile.created_at.desc())
        .limit(1)
    )
    if job_id is not None:
        row = (await db.execute(base.where(CandidateProfile.job_id == job_id))).scalar_one_or_none()
        if row is not None:
            return row
    return (await db.execute(base)).scalar_one_or_none()


async def resume_pending(db: AsyncSession, candidate_id: uuid.UUID) -> bool:
    """True when a resume for the candidate is still being processed (or
    processed but its profile not generated yet), i.e. a profile is coming."""
    from app.db.models import CandidateResume

    status = (
        await db.execute(
            select(CandidateResume.status)
            .where(CandidateResume.candidate_id == candidate_id)
            .order_by(CandidateResume.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return status in PENDING_RESUME_STATUSES


def claims_from_profile(profile: dict[str, Any], round_config: RoundConfig) -> dict[str, ClaimState]:
    """Resume claims to verify (all kept; the policy probes those >= min_priority)."""
    claims: dict[str, ClaimState] = {}
    if not round_config.resume_validation.enabled:
        return claims
    parsed: list[ClaimToVerify] = []
    for item in profile.get("claims_to_verify") or []:
        try:
            parsed.append(ClaimToVerify.model_validate(item))
        except ValueError:
            continue
    parsed.sort(key=lambda c: (-c.priority, c.id))
    for claim in parsed[:MAX_CLAIMS_IN_STATE]:
        claims[claim.id] = ClaimState(claim=claim, status=ClaimStatus.UNVERIFIED)
    return claims


def attach_profile(state: InterviewState, profile: dict[str, Any], *, profile_status: str | None) -> None:
    """Attach a (late) candidate profile to a session that is not live yet."""
    state.candidate_profile = dict(profile or {})
    if profile.get("candidate_name"):
        state.candidate_name = str(profile["candidate_name"])
    state.claims = claims_from_profile(profile, state.round_config)
    state.profile_status = profile_status


class SessionServiceImpl:
    """Implements ``app.services.interview.contracts.SessionService``."""

    def __init__(self, *, store: StateStore | None = None, config: EngineConfig | None = None) -> None:
        self.config = config or get_engine_config()
        self.store = store or StateStore(config=self.config)

    # ------------------------------------------------------------- create
    async def create_or_resume(
        self, db: AsyncSession, *, interview_id: Any, round_id: Any | None = None
    ) -> InterviewSession:
        iid = uuid.UUID(str(interview_id))
        interview = (
            await db.execute(
                select(Interview)
                .where(Interview.id == iid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if interview is None:
            raise NotFound("Interview not found.")
        existing = (
            (
                await db.execute(
                    select(InterviewSession)
                    .where(InterviewSession.interview_id == iid)
                    .order_by(InterviewSession.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for row in existing:
            if row.status in OPEN_STATUSES:
                if row.status == SessionStatus.CREATED.value:
                    await enqueue(
                        "session.prepare",
                        {"session_id": str(row.id)},
                        dedupe_key=f"session.prepare:{row.id}",
                        db=db,
                    )
                return row
        if any(row.status in DONE_STATUSES for row in existing):
            raise Conflict("This interview has already been completed.", code="interview_completed")
        if interview.status in (
            InterviewStatus.CANCELLED.value,
            InterviewStatus.EXPIRED.value,
            InterviewStatus.COMPLETED.value,
        ):
            raise Conflict(f"This interview is {interview.status}.", code=f"interview_{interview.status}")
        if interview.invite_expires_at is not None and interview.invite_expires_at <= datetime.now(UTC):
            raise Conflict("This interview invitation has expired.", code="interview_expired")

        round_row = await self._resolve_round(db, interview, round_id)
        round_config = round_config_from_row(round_row)
        policy = load_policy_snapshot(interview.policy_snapshot)
        candidate = await db.get(Candidate, interview.candidate_id)
        job = await db.get(Job, interview.job_id)
        organization = await db.get(Organization, interview.organization_id)
        if candidate is None or job is None:
            raise NotFound("Interview candidate or job no longer exists.")
        profile_row = await self._latest_profile(db, interview.candidate_id, interview.job_id)
        profile = dict(profile_row.profile or {}) if profile_row is not None else {}
        consent = await self._latest_consent(db, iid)

        session_id = uuid.uuid4()
        state = await self._initial_state(
            db,
            session_id=session_id,
            interview=interview,
            round_row=round_row,
            round_config=round_config,
            policy=policy,
            candidate=candidate,
            job=job,
            organization=organization,
            profile=profile,
            consent=consent,
            profile_status=profile_row.status if profile_row is not None else None,
        )
        row = InterviewSession(
            id=session_id,
            interview_id=iid,
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=round_row.id,
            profile_id=profile_row.id if profile_row is not None else None,
            status=SessionStatus.CREATED.value,
            round_config=round_config.model_dump(mode="json"),
            state=state.model_dump(mode="json"),
            state_version=state.version,
        )
        db.add(row)
        if interview.round_id is None:
            interview.round_id = round_row.id
        await db.flush()
        await log_event(
            db,
            session_id,
            "session.created",
            {
                "round_id": str(round_row.id),
                "round_type": round_config.round_type.value,
                "profile_id": str(profile_row.id) if profile_row else None,
                "claims": len(state.claims),
                "competencies": list(state.competencies),
                "audio_recording": state.audio_recording_allowed,
            },
            elapsed_s=0.0,
        )
        await enqueue(
            "session.prepare",
            {"session_id": str(session_id)},
            dedupe_key=f"session.prepare:{session_id}",
            db=db,
        )
        log.info(
            "engine.session_created",
            session_id=str(session_id),
            interview_id=str(iid),
            round_id=str(round_row.id),
        )
        return row

    async def _resolve_round(self, db: AsyncSession, interview: Interview, round_id: Any | None) -> Round:
        wanted = uuid.UUID(str(round_id)) if round_id else interview.round_id
        if wanted is not None:
            row = await db.get(Round, wanted)
            if row is None or row.job_id != interview.job_id or not row.is_active:
                raise ValidationFailed(
                    "The selected round is not available for this interview.", code="invalid_round"
                )
            if (
                round_id
                and interview.round_id
                and wanted != interview.round_id
                and not interview.allow_round_selection
            ):
                raise ValidationFailed(
                    "Round selection is not allowed for this interview.", code="round_selection_not_allowed"
                )
            if (
                round_id
                and interview.allow_round_selection
                and interview.round_id is None
                and not row.candidate_selectable
            ):
                raise ValidationFailed("This round cannot be selected.", code="round_not_selectable")
            return row
        if interview.allow_round_selection:
            raise ValidationFailed("Please choose a round before starting.", code="round_required")
        row = (
            await db.execute(
                select(Round)
                .where(Round.job_id == interview.job_id, Round.is_active.is_(True))
                .order_by(Round.sequence.asc(), Round.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValidationFailed("This job has no active interview round.", code="no_round")
        return row

    async def _latest_profile(
        self, db: AsyncSession, candidate_id: uuid.UUID, job_id: uuid.UUID
    ) -> CandidateProfile | None:
        return await latest_profile(db, candidate_id, job_id)

    async def _latest_consent(self, db: AsyncSession, interview_id: uuid.UUID) -> Consent | None:
        return (
            await db.execute(
                select(Consent)
                .where(Consent.interview_id == interview_id, Consent.withdrawn_at.is_(None))
                .order_by(Consent.accepted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _initial_state(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        interview: Interview,
        round_row: Round,
        round_config: RoundConfig,
        policy: PolicySnapshot,
        candidate: Candidate,
        job: Job,
        organization: Organization | None,
        profile: dict[str, Any],
        consent: Consent | None,
        profile_status: str | None = None,
    ) -> InterviewState:
        keys = round_config.competency_keys()
        catalog: dict[str, Competency] = {}
        rows = (
            (
                await db.execute(
                    select(Competency)
                    .where(Competency.key.in_(keys))
                    .where(
                        or_(
                            Competency.organization_id == interview.organization_id,
                            Competency.organization_id.is_(None),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        # Organization-specific competencies override the global catalog.
        for row in sorted(rows, key=lambda r: r.organization_id is not None):
            catalog[row.key] = row
        competencies: dict[str, CompetencyState] = {}
        for c in round_config.competencies:
            cat = catalog.get(c.key)
            competencies[c.key] = CompetencyState(
                key=c.key,
                name=c.name or (cat.name if cat else c.key.replace("_", " ").title()),
                weight=c.weight,
                required=c.required,
                category=cat.category if cat else None,
                focus_areas=[f.replace("_", " ") for f in c.focus_areas],
                current_difficulty=c.target_difficulty or round_config.expected_difficulty,
            )
        claims = claims_from_profile(profile, round_config)
        requirements = (
            (
                await db.execute(
                    select(JobRequirement)
                    .where(JobRequirement.job_id == job.id)
                    .order_by(JobRequirement.sort_order, JobRequirement.created_at)
                )
            )
            .scalars()
            .all()
        )
        job_summary = {
            "title": job.title,
            "company": organization.name if organization else None,
            "department": job.department,
            "location": job.location,
            "employment_type": job.employment_type,
            "seniority": job.seniority,
            "description": (job.description or "")[:_MAX_DESCRIPTION],
            "interview_instructions": job.interview_instructions,
            "requirements": [
                {"kind": r.kind, "name": r.name, "competency_key": r.competency_key, "weight": r.weight}
                for r in requirements[:25]
            ],
        }
        items = dict(consent.items or {}) if consent is not None else {}
        audio_allowed = bool(policy.recording.record_answer_audio and items.get("audio_recording") is True)
        coding_cfg = round_config.coding
        coding = CodingState(
            time_limit_s=int(coding_cfg.duration_minutes * 60),
            hints_available=int(coding_cfg.max_hints) if coding_cfg.hints_enabled else 0,
        )
        state = InterviewState(
            session_id=str(session_id),
            interview_id=str(interview.id),
            organization_id=str(interview.organization_id),
            candidate_id=str(interview.candidate_id),
            job_id=str(interview.job_id),
            round_id=str(round_row.id),
            status=SessionStatus.CREATED,
            candidate_name=(profile.get("candidate_name") or candidate.full_name or None),
            candidate_profile=profile,
            job_summary=job_summary,
            round_config=round_config,
            interviewer_name=(policy.avatar.interviewer_name or self.config.default_interviewer_name),
            time_limit_s=float(round_config.duration_minutes * 60),
            max_time_s=float(round_config.max_duration_minutes * 60),
            current_difficulty=round_config.expected_difficulty
            if isinstance(round_config.expected_difficulty, Difficulty)
            else Difficulty.MEDIUM,
            competencies=competencies,
            claims=claims,
            skills_uncovered=list(keys),
            coding=coding,
            audio_recording_allowed=audio_allowed,
            monitoring_enabled=monitoring_enabled(policy),
            candidate_report_mode=policy.candidate_report_mode.value,
            tts_voice=policy.avatar.voice,
            audio_retention_days=policy.retention.audio_days,
            profile_status=profile_status,
        )
        # Prefer the candidate's own name from the candidate record when the
        # profile did not extract one (first name only is used in speech).
        if not state.candidate_name:
            state.candidate_name = candidate.full_name
        from app.services.intake.session_init import build_initial_intake_state, is_intake_round

        if is_intake_round(round_config):
            # Intake mode (INTAKE_INTERVIEW.md 6.8): the deterministic stage machine; the time limit comes from
            # the invitation's intake settings (35-50 minutes) + 5 minutes of hard maximum.
            state.intake, state.time_limit_s, state.max_time_s = await build_initial_intake_state(
                db, interview=interview, round_config=round_config, candidate_name=candidate.full_name
            )
        return state

    # --------------------------------------------------------------- view
    async def candidate_view(self, db: AsyncSession, session_id: Any) -> dict[str, Any]:
        sid = uuid.UUID(str(session_id))
        row = await db.get(InterviewSession, sid)
        if row is None:
            raise NotFound("Session not found.")
        state = await self.store.load_cached(str(sid))
        if state is None and row.state:
            try:
                state = InterviewState.model_validate(row.state)
            except ValueError:
                state = None
        round_config = RoundConfig.model_validate(row.round_config) if row.round_config else None
        interview = await db.get(Interview, row.interview_id)
        policy = load_policy_snapshot(interview.policy_snapshot if interview else None)
        report = (
            await db.execute(
                select(Report)
                .where(Report.session_id == sid, Report.type == ReportType.CANDIDATE.value)
                .order_by(Report.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        status = row.status
        elapsed = state.elapsed_seconds() if state is not None else float(row.elapsed_seconds or 0.0)
        time_limit = (
            state.time_limit_s
            if state is not None
            else float((round_config.duration_minutes if round_config else 30) * 60)
        )
        coding = None
        if round_config is not None and round_config.coding.enabled:
            coding = {
                "enabled": True,
                "duration_minutes": round_config.coding.duration_minutes,
                "languages": [lang.value for lang in round_config.coding.languages],
                "hints_enabled": round_config.coding.hints_enabled,
                "max_hints": round_config.coding.max_hints,
                "active": bool(state.coding.active) if state else False,
                "completed": bool(state.coding.completed) if state else False,
                "coding_session_id": state.coding.coding_session_id
                if state and state.coding.active
                else None,
            }
        report_available = bool(
            report is not None and report.status == ReportStatus.READY.value and report.released_to_candidate
        )
        questions = 0
        if state is not None:
            from app.services.interview.policy import count_questions

            questions = count_questions(state)
        return {
            "session_id": str(sid),
            "interview_id": str(row.interview_id),
            "status": status,
            "round": {
                "id": str(row.round_id),
                "name": round_config.name if round_config else None,
                "round_type": round_config.round_type.value if round_config else None,
                "duration_minutes": round_config.duration_minutes if round_config else None,
                "instructions": round_config.instructions if round_config else None,
            },
            "interviewer_name": state.interviewer_name if state else self.config.default_interviewer_name,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
            "end_reason": row.end_reason,
            "elapsed_s": round(elapsed, 1),
            "remaining_s": round(max(0.0, time_limit - elapsed), 1),
            "time_limit_s": time_limit,
            "question_count": questions,
            "coding": coding,
            "show_subtitles": policy.recording.show_live_subtitles,
            "allow_typed_answers": policy.recording.allow_typed_answers,
            "ready": status
            in (SessionStatus.READY.value, SessionStatus.LIVE.value, SessionStatus.PAUSED.value),
            "report": {
                "mode": policy.candidate_report_mode.value,
                "available": report_available,
                "status": report.status
                if report is not None
                else (
                    "not_applicable"
                    if policy.candidate_report_mode == CandidateReportMode.NEVER
                    else "pending"
                ),
            },
        }


async def mark_session_completed(db: AsyncSession, session_id: Any) -> bool:
    """Move a COMPLETING session to COMPLETED (called once reports are done)."""
    from sqlalchemy import update

    result = await db.execute(
        update(InterviewSession)
        .where(
            InterviewSession.id == uuid.UUID(str(session_id)),
            InterviewSession.status == SessionStatus.COMPLETING.value,
        )
        .values(status=SessionStatus.COMPLETED.value)
    )
    completed = bool(getattr(result, "rowcount", 0))
    if completed:
        store = StateStore()

        def _complete(state: InterviewState) -> bool:
            if state.status != SessionStatus.COMPLETING:
                return False
            state.status = SessionStatus.COMPLETED
            return True

        await store.save_with_retry(str(session_id), _complete)
    return completed


_service: SessionServiceImpl | None = None


def get_session_service() -> SessionServiceImpl:
    global _service
    if _service is None:
        _service = SessionServiceImpl()
    return _service


def set_session_service(service: SessionServiceImpl | None) -> SessionServiceImpl | None:
    global _service
    previous = _service
    _service = service
    return previous


__all__ = [
    "SessionServiceImpl",
    "get_session_service",
    "load_policy_snapshot",
    "mark_session_completed",
    "monitoring_enabled",
    "round_config_from_row",
    "set_session_service",
]
