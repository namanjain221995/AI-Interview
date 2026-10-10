"""Candidate portal API (CONTRACTS.md section 4, REALTIME_DESIGN.md section 9).

Every route requires a candidate principal (cookie set by
``/candidate-access/{token}/verify``) and only reaches that candidate's own
interview and sessions; anything else is a 404 (never a 403, so ids cannot be
probed). Recording and analysis endpoints require BOTH the frozen recruiter
policy and the candidate's recorded consent.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.candidate_access import InviteGone, ensure_access, ensure_invite_usable
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import CandidatePrincipal, DbSession
from app.core.errors import (
    AppError,
    Conflict,
    Forbidden,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    UnsupportedMediaType,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.core.queue import enqueue, publish_session_event
from app.core.ratelimit import enforce_rate_limit
from app.core.rbac import Principal
from app.core.security import constant_time_equals, issue_ws_ticket
from app.db.models import (
    Candidate,
    CandidateResume,
    Interview,
    InterviewSession,
    Job,
    MediaAsset,
    Organization,
    Report,
    Round,
)
from app.db.models import Consent as ConsentRow
from app.domain.enums import (
    IntegrityAction,
    InterviewStatus,
    ReportStatus,
    ReportType,
    ResumeStatus,
    SessionStatus,
)
from app.realtime import deps
from app.realtime.bridges import objective_action_allowed
from app.realtime.context import TERMINAL_STATUSES, first_name
from app.realtime.origin import build_ws_url, ws_path
from app.realtime.policy import SessionPolicy, latest_consent, session_policy
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.portal import (
    CandidateReportOut,
    ConsentRequest,
    ConsentResponse,
    DisclosureOut,
    FrameUploadAccepted,
    IntegrityEventsRequest,
    IntegrityEventsResponse,
    IntegrityWarningOut,
    PortalCandidate,
    PortalIntakeBrief,
    PortalInterview,
    PortalJob,
    PortalMe,
    PortalOrganization,
    PortalResume,
    PortalResumeInfo,
    PortalRound,
    PortalSession,
    PortalSessionBrief,
    PortalSessionCreateResponse,
    PortalSessionEndRequest,
    PortalSessionEndResponse,
    RecordingChunkResponse,
    RoundSelectionRequest,
    SessionCreateRequest,
    SystemCheckRequest,
    SystemCheckResponse,
    WsTicketResponse,
)
from app.schemas.api.recordings import RecordingCompleteRequest, RecordingCompleteResponse
from app.services.integrity.contracts import Disclosure
from app.services.jobs import access_window
from app.services.recordings.constants import FINALIZE_JOB
from app.services.recordings.segments import SegmentTiming, accept_segment, base_type, uploads_open
from app.services.recordings.sweep import finalize_dedupe_key
from app.services.reports.queries import resolve_candidate_report_mode
from app.services.storage import ensure_storage_headroom
from app.services.storage.signing import signed_url_with_expiry

log = get_logger(__name__)

router = APIRouter(prefix="/portal", tags=["portal"], responses=ERROR_RESPONSES)

OPEN_SESSION_STATUSES = (
    SessionStatus.CREATED.value,
    SessionStatus.PREPARING.value,
    SessionStatus.READY.value,
    SessionStatus.LIVE.value,
    SessionStatus.PAUSED.value,
)
FRAME_TYPES = {"image/jpeg", "image/png", "image/webp"}
CANDIDATE_REPORT_HIDDEN_KEYS = frozenset(
    {
        "integrity",
        "integrity_summary",
        "recommendation",
        "recruiter_notes",
        "reviewer_notes",
        "claims_to_verify",
        "scores_internal",
    }
)


# =============================================================== helpers


async def _interview(db: AsyncSession, principal: Principal, interview_id: uuid.UUID) -> Interview:
    if principal.interview_id != interview_id:
        raise NotFound(code="interview_not_found")
    interview = await db.get(Interview, interview_id)
    if interview is None or interview.candidate_id != principal.candidate_id:
        raise NotFound(code="interview_not_found")
    return interview


async def _session(
    db: AsyncSession, principal: Principal, session_id: uuid.UUID
) -> tuple[InterviewSession, Interview]:
    row = await db.get(InterviewSession, session_id)
    if (
        row is None
        or row.interview_id != principal.interview_id
        or row.candidate_id != principal.candidate_id
    ):
        raise NotFound(code="session_not_found")
    interview = await db.get(Interview, row.interview_id)
    if interview is None:
        raise NotFound(code="session_not_found")
    return row, interview


def _raise_if_refused(code: str | None) -> None:
    if code == access_window.REFUSAL_CANCELLED:
        raise InviteGone("This interview invitation was cancelled.", code=code)
    if code is not None:
        raise InviteGone("This interview invitation has expired.", code=code)


def _ensure_not_completed(interview: Interview) -> None:
    ensure_invite_usable(interview)
    if interview.status == InterviewStatus.COMPLETED.value:
        raise Conflict("This interview has already been completed.", code="interview_completed")


def _session_status(row: InterviewSession) -> SessionStatus:
    try:
        return SessionStatus(row.status)
    except ValueError:
        return SessionStatus.CREATED


def _ensure_session_open(row: InterviewSession) -> None:
    if _session_status(row) in TERMINAL_STATUSES or row.status == SessionStatus.COMPLETING.value:
        raise Conflict("This interview session has ended.", code="session_ended")


async def _policy(db: AsyncSession, interview: Interview) -> SessionPolicy:
    return session_policy(interview.policy_snapshot, await latest_consent(db, interview.id))


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


def _round_out(round_: Round) -> PortalRound:
    config = round_.config if isinstance(round_.config, dict) else {}
    coding = config.get("coding") if isinstance(config.get("coding"), dict) else {}
    return PortalRound(
        id=round_.id,
        name=round_.name,
        round_type=round_.round_type,
        duration_minutes=round_.duration_minutes,
        expected_difficulty=round_.expected_difficulty,
        instructions=round_.instructions or config.get("instructions"),
        coding_enabled=bool(coding.get("enabled", False)) if isinstance(coding, dict) else False,
        candidate_selectable=round_.candidate_selectable,
    )


async def _offered_rounds(db: AsyncSession, interview: Interview) -> list[Round]:
    if interview.allow_round_selection:
        rows = await db.scalars(
            select(Round)
            .where(
                Round.job_id == interview.job_id,
                Round.is_active.is_(True),
                Round.candidate_selectable.is_(True),
            )
            .order_by(Round.sequence, Round.created_at)
        )
        return list(rows.all())
    if interview.round_id is not None:
        round_ = await db.get(Round, interview.round_id)
        return [round_] if round_ is not None else []
    first = await db.scalar(
        select(Round)
        .where(Round.job_id == interview.job_id, Round.is_active.is_(True))
        .order_by(Round.sequence)
        .limit(1)
    )
    return [first] if first is not None else []


async def _latest_session(db: AsyncSession, interview_id: uuid.UUID) -> InterviewSession | None:
    return await db.scalar(
        select(InterviewSession)
        .where(InterviewSession.interview_id == interview_id)
        .order_by(InterviewSession.created_at.desc())
        .limit(1)
    )


async def _latest_resume(db: AsyncSession, interview: Interview) -> CandidateResume | None:
    stmt = select(CandidateResume).where(CandidateResume.candidate_id == interview.candidate_id)
    from app.services.open_links.hooks import registration_interview_ids

    scope = await registration_interview_ids(db, interview.id)
    if scope is not None:
        # Open-link registrations are not verified identities (S4): only résumés uploaded within this
        # registration, never the person's résumés from other applications.
        stmt = stmt.where(CandidateResume.interview_id.in_(scope or [interview.id]))
    return await db.scalar(
        stmt.order_by(
            case((CandidateResume.interview_id == interview.id, 0), else_=1),
            CandidateResume.created_at.desc(),
        ).limit(1)
    )


async def _released_report(db: AsyncSession, session_id: uuid.UUID) -> Report | None:
    """Newest candidate report released to the candidate
    (``app.services.reports.get_released_candidate_report`` when installed)."""
    try:
        from app.services.reports import get_released_candidate_report
    except ImportError:
        get_released_candidate_report = None  # type: ignore[assignment]
    if get_released_candidate_report is not None:
        report: Report | None = await get_released_candidate_report(db, session_id)
        return report
    return await db.scalar(
        select(Report)
        .where(
            Report.session_id == session_id,
            Report.type == ReportType.CANDIDATE.value,
            Report.released_to_candidate.is_(True),
            Report.status == ReportStatus.READY.value,
        )
        .order_by(Report.version.desc())
        .limit(1)
    )


async def _steps_completed(db: AsyncSession, interview: Interview) -> list[str]:
    steps: list[str] = []
    consent = await latest_consent(db, interview.id)
    if consent is not None:
        steps += ["welcome", "instructions", "consent"]
    resume = await _latest_resume(db, interview)
    if resume is not None and resume.status not in (ResumeStatus.FAILED.value, ResumeStatus.REJECTED.value):
        steps.append("resume")
    steps += await _intake_steps(db, interview)
    if interview.round_id is not None:
        steps.append("round")
    if isinstance(interview.system_check, dict) and interview.system_check.get("passed"):
        steps.append("system-check")
    session = await _latest_session(db, interview.id)
    if session is not None and session.status in (
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
    ):
        steps += ["interview", "complete"]
        if await _released_report(db, session.id) is not None:
            steps.append("report")
    return steps


def _integrity_disclosure_helpers() -> Any | None:
    """``app.services.integrity`` disclosure helpers (None if the module is absent)."""
    try:
        import app.services.integrity as integrity
    except ImportError:
        return None
    return integrity


def _fallback_markdown(d: Disclosure) -> str:
    lines = [f"# {d.title}", "", d.intro, ""]
    for item in d.items:
        status = "Required" if item.required else ("Optional" if item.enabled else "Not used")
        lines += [f"## {item.title} ({status})", "", item.description, ""]
    lines += ["## Purpose", "", d.purpose, "", "## Human decision", "", d.human_review_statement, ""]
    lines += ["## Limitations", "", d.limitations_statement, "", f"Policy version {d.policy_version}"]
    return "\n".join(lines).strip() + "\n"


def disclosure_body(disclosure: Disclosure, snapshot: dict[str, Any]) -> dict[str, Any]:
    helpers = _integrity_disclosure_helpers()
    if helpers is not None:
        return dict(helpers.disclosure_payload(disclosure, snapshot))
    payload = asdict(disclosure)
    payload["text_markdown"] = _fallback_markdown(disclosure)
    payload["consent_items"] = {i.key: i.enabled for i in disclosure.items if i.key != "retention"}
    payload["required_items"] = [i.key for i in disclosure.items if i.required]
    return payload


def missing_required(disclosure: Disclosure, accepted: dict[str, Any]) -> list[str]:
    helpers = _integrity_disclosure_helpers()
    if helpers is not None:
        return list(helpers.missing_required_items(disclosure, accepted))
    return [
        i.key for i in disclosure.items if i.required and i.key != "retention" and not accepted.get(i.key)
    ]


def stored_consent_items(
    disclosure: Disclosure, snapshot: dict[str, Any], accepted: dict[str, Any]
) -> dict[str, Any]:
    """``consent_items(...)`` (what was disclosed and accepted) with optional
    items the candidate explicitly declined set to False."""
    helpers = _integrity_disclosure_helpers()
    if helpers is not None:
        items = dict(helpers.consent_items(disclosure, snapshot))
    else:
        items = {i.key: i.enabled for i in disclosure.items if i.key != "retention"}
    for item in disclosure.items:
        choice = accepted.get(item.key)
        if item.enabled and not item.required and isinstance(choice, bool):
            items[item.key] = choice
    return items


async def _build_disclosure(db: AsyncSession, interview: Interview) -> Disclosure:
    org = await db.get(Organization, interview.organization_id)
    job = await db.get(Job, interview.job_id)
    builder = deps.get_disclosure_builder()
    try:
        disclosure = await _call(
            builder,
            dict(interview.policy_snapshot or {}),
            organization_name=org.name if org else "",
            job_title=job.title if job else "",
        )
    except ValueError as exc:
        log.error("portal.disclosure_invalid_policy", interview_id=str(interview.id), error=str(exc)[:200])
        raise Conflict(
            "This interview's policy could not be shown. Please contact the recruiter.", code="policy_invalid"
        ) from exc
    if not isinstance(disclosure, Disclosure) and is_dataclass(disclosure):
        disclosure = Disclosure(**asdict(disclosure))  # type: ignore[arg-type]
    return disclosure


async def _open_link_resume_guard(db: AsyncSession, interview: Interview) -> None:
    """Open-link interviews that require a resume cannot start without one
    (409 ``resume_required``); any other failure of that layer is only logged."""
    try:
        from app.services.open_links.hooks import ensure_resume_for_session

        async with db.begin_nested():
            await ensure_resume_for_session(db, interview)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - the open-link layer must never break the portal
        log.warning("portal.open_link_guard_failed", interview_id=str(interview.id), error=type(exc).__name__)


async def _intake_steps(db: AsyncSession, interview: Interview) -> list[str]:
    """Intake-only journey steps (``details``, ``resume-review``); never breaks the portal."""
    try:
        from app.services.intake.access import intake_round_of_interview, intake_steps

        if await intake_round_of_interview(db, interview) is None:
            return []
        async with db.begin_nested():
            return await intake_steps(db, interview)
    except Exception as exc:  # noqa: BLE001 - the intake layer must never break the portal
        log.warning("portal.intake_steps_failed", interview_id=str(interview.id), error=type(exc).__name__)
        return []


async def _intake_brief(db: AsyncSession, interview: Interview) -> Any | None:
    """``PortalInterview.intake`` data for intake interviews; never breaks the portal."""
    try:
        from app.services.intake.access import intake_round_of_interview, portal_brief

        if await intake_round_of_interview(db, interview) is None:
            return None
        async with db.begin_nested():
            return await portal_brief(db, interview)
    except Exception as exc:  # noqa: BLE001 - the intake layer must never break the portal
        log.warning("portal.intake_brief_failed", interview_id=str(interview.id), error=type(exc).__name__)
        return None


async def _intake_start_guard(db: AsyncSession, interview: Interview) -> None:
    """Intake interviews start only after the résumé, details and résumé-review steps (409 ``intake_*``,
    ``intake_disabled``); any other failure of the intake layer is only logged."""
    try:
        from app.services.intake.access import enforce_start_guard, intake_round_of_interview

        if await intake_round_of_interview(db, interview) is None:
            return
        async with db.begin_nested():
            await enforce_start_guard(db, interview)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - the intake layer must never break the portal
        log.warning("portal.intake_guard_failed", interview_id=str(interview.id), error=type(exc).__name__)


async def _open_link_resume_uploaded(db: AsyncSession, interview: Interview, resume: Any) -> None:
    """Copy the resume hash to an open-link registration and flag duplicates (never fails the upload)."""
    if resume is None:
        return
    try:
        from app.services.open_links.hooks import on_resume_uploaded

        async with db.begin_nested():
            await on_resume_uploaded(db, interview, resume)
    except Exception as exc:  # noqa: BLE001 - the open-link layer must never break the portal
        log.warning(
            "portal.open_link_resume_hook_failed", interview_id=str(interview.id), error=type(exc).__name__
        )


async def _read_limited(upload: UploadFile, limit: int, label: str) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge(
            f"The {label} is larger than {limit // (1024 * 1024) or 1} MB.", details={"max_bytes": limit}
        )
    if not data:
        raise ValidationFailed(f"The {label} is empty.", code="empty_upload")
    return data


# ================================================================ me


@router.get("/me", response_model=PortalMe)
async def portal_me(principal: CandidatePrincipal, db: DbSession) -> PortalMe:
    candidate = await db.get(Candidate, principal.candidate_id)
    org = await db.get(Organization, principal.organization_id) if principal.organization_id else None
    if candidate is None or principal.interview_id is None:
        raise NotFound(code="candidate_not_found")
    return PortalMe(
        candidate=PortalCandidate(
            id=candidate.id,
            full_name=candidate.full_name,
            first_name=first_name(candidate.full_name),
            email=candidate.email,
        ),
        interview_id=principal.interview_id,
        organization=PortalOrganization(name=org.name if org else "", slug=org.slug if org else None),
    )


# =========================================================== interview


@router.get("/interviews/{interview_id}", response_model=PortalInterview)
async def portal_interview(
    interview_id: uuid.UUID, principal: CandidatePrincipal, db: DbSession
) -> PortalInterview:
    interview = await _interview(db, principal, interview_id)
    org = await db.get(Organization, interview.organization_id)
    job = await db.get(Job, interview.job_id)
    if job is None:
        raise NotFound(code="interview_not_found")
    policy = await _policy(db, interview)
    rounds = await _offered_rounds(db, interview)
    latest = await _latest_session(db, interview.id)
    steps = await _steps_completed(db, interview)
    intake = await _intake_brief(db, interview)
    return PortalInterview(
        id=interview.id,
        status=InterviewStatus(interview.status)
        if interview.status in InterviewStatus._value2member_map_
        else InterviewStatus.INVITED,
        organization=PortalOrganization(name=org.name if org else "", slug=org.slug if org else None),
        job=PortalJob(
            id=job.id,
            title=job.title,
            department=job.department,
            location=job.location,
            description=job.description,
        ),
        rounds_offered=[_round_out(r) for r in rounds],
        selected_round_id=interview.round_id,
        allow_round_selection=interview.allow_round_selection,
        # Intake never produces a candidate report (S21), whatever an older snapshot froze.
        policy={**policy.candidate_safe(), **({"candidate_report_mode": "never"} if intake is not None else {})},
        steps_completed=steps,  # type: ignore[arg-type]
        expires_at=interview.invite_expires_at,
        interviewer_name=policy.interviewer_name,
        latest_session=PortalSessionBrief(id=latest.id, status=_session_status(latest))
        if latest is not None
        else None,
        requires_resume="resume" not in steps and (intake is None or intake.resume_required),
        intake=PortalIntakeBrief(
            intake_id=intake.intake_id,
            title=intake.title,
            estimated_minutes=intake.estimated_minutes,
            presence=intake.presence,
        )
        if intake is not None
        else None,
    )


@router.get("/interviews/{interview_id}/disclosure", response_model=DisclosureOut)
async def portal_disclosure(
    interview_id: uuid.UUID, principal: CandidatePrincipal, db: DbSession
) -> DisclosureOut:
    interview = await _interview(db, principal, interview_id)
    disclosure = await _build_disclosure(db, interview)
    consent = await latest_consent(db, interview.id)
    body = disclosure_body(disclosure, dict(interview.policy_snapshot or {}))
    body["consented"] = consent is not None and constant_time_equals(
        consent.policy_hash, disclosure.policy_hash
    )
    return DisclosureOut.model_validate(body)


@router.post(
    "/interviews/{interview_id}/consent", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED
)
async def portal_consent(
    interview_id: uuid.UUID,
    body: ConsentRequest,
    request: Request,
    principal: CandidatePrincipal,
    db: DbSession,
) -> ConsentResponse:
    interview = await _interview(db, principal, interview_id)
    _ensure_not_completed(interview)
    disclosure = await _build_disclosure(db, interview)
    if not constant_time_equals(body.policy_hash, disclosure.policy_hash):
        raise Conflict(
            "The interview policy changed. Please review the updated information.",
            code="policy_changed",
            details={"policy_hash": disclosure.policy_hash},
        )
    accepted = dict(body.items)
    missing = missing_required(disclosure, accepted)
    if missing:
        raise ValidationFailed(
            "Please accept every required item to continue.",
            code="consent_required",
            details={"missing": missing},
        )
    items = stored_consent_items(disclosure, dict(interview.policy_snapshot or {}), accepted)
    user_agent = request.headers.get("user-agent")
    row = ConsentRow(
        interview_id=interview.id,
        candidate_id=interview.candidate_id,
        policy_version=disclosure.policy_version[:32],
        policy_hash=disclosure.policy_hash,
        items=items,
        ip_address=request.client.host if request.client else None,
        user_agent=user_agent[:512] if user_agent else None,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        principal,
        "candidate.consent",
        resource_type="interview",
        resource_id=str(interview.id),
        details={
            "policy_version": disclosure.policy_version,
            "policy_hash": disclosure.policy_hash,
            "items": items,
        },
        request=request,
    )
    return ConsentResponse(
        consent_id=row.id,
        policy_version=row.policy_version,
        policy_hash=row.policy_hash,
        accepted_at=row.accepted_at or datetime.now(UTC),
        items=items,
    )


# ============================================================== resume


def _resume_info(resume: CandidateResume, progress: Any | None) -> PortalResumeInfo:
    steps: list[dict[str, Any]] = []
    error = (
        resume.error if resume.status in (ResumeStatus.FAILED.value, ResumeStatus.REJECTED.value) else None
    )
    profile_ready = False
    status_value = resume.status
    if progress is not None:
        steps = list(getattr(progress, "steps", []) or [])
        error = getattr(progress, "error", None) or error
        profile_ready = bool(getattr(progress, "profile_ready", False))
        status_value = getattr(progress, "status", None) or status_value
    return PortalResumeInfo(
        id=resume.id,
        original_filename=resume.original_filename,
        size_bytes=resume.size_bytes,
        status=status_value,
        status_detail=resume.status_detail,
        progress=resume.progress if isinstance(resume.progress, dict) else {},
        steps=steps,
        error=error,
        profile_ready=profile_ready,
        created_at=resume.created_at,
    )


async def _resume_payload(
    db: AsyncSession, interview: Interview, resume: CandidateResume | None
) -> PortalResume:
    if resume is None:
        return PortalResume(resume=None, profile_summary=None)
    progress = None
    summary = None
    stage = "none"
    try:
        service = deps.get_resume_service()
    except ServiceUnavailable:
        service = None
    if service is not None:
        try:
            progress = await _call(service.progress, db, resume.id)
        except AppError:
            progress = None
        try:
            profile, stage = await _resume_profile(service, db, interview, resume)
            if profile is not None:
                document = getattr(profile, "profile", profile)
                summary = service.candidate_safe_summary(document if isinstance(document, dict) else {})
                quick = (resume.scan_result or {}).get("quick") if stage == "quick" else None
                if isinstance(quick, dict) and quick.get("partial"):
                    summary = {
                        **summary,
                        "partial": True,
                        "pages_read": quick.get("pages_done"),
                        "pages_total": quick.get("pages_total"),
                    }
        except AppError:
            summary, stage = None, "none"
    sections = [
        {"key": s.get("key"), "label": s.get("label"), "state": s.get("state")}
        for s in (getattr(progress, "sections", None) or [])
        if isinstance(s, dict) and s.get("key") in _CANDIDATE_RESUME_SECTIONS
    ]
    return PortalResume(
        resume=_resume_info(resume, progress),
        profile_summary=summary,
        profile_stage=stage if summary is not None else "none",
        sections=sections,
    )


# Profile sections a candidate sees the state of (never what the analysis found).
_CANDIDATE_RESUME_SECTIONS = frozenset({"basics", "experience", "skills", "education", "interview"})


async def _resume_profile(
    service: Any, db: AsyncSession, interview: Interview, resume: CandidateResume
) -> tuple[Any | None, str]:
    """The profile shown for the candidate's current resume: its model profile,
    else its quick profile (``quick``, shown while the model profile is
    generated); for a ready resume without one, the candidate's latest profile."""
    finder = getattr(service, "profile_for_resume", None)
    if finder is not None:
        profile, stage = await _call(finder, db, resume.id)
        if profile is not None:
            return profile, stage
        if resume.status != ResumeStatus.READY.value:
            return None, "none"
    profile = await _call(service.latest_profile, db, interview.candidate_id, interview.job_id)
    return profile, ("full" if profile is not None else "none")


@router.post(
    "/interviews/{interview_id}/resume", response_model=PortalResume, status_code=status.HTTP_201_CREATED
)
async def portal_resume_upload(
    interview_id: uuid.UUID,
    request: Request,
    principal: CandidatePrincipal,
    db: DbSession,
    file: Annotated[UploadFile, File(description="Resume (PDF, DOCX, TXT, PNG or JPG)")],
) -> PortalResume:
    interview = await _interview(db, principal, interview_id)
    _ensure_not_completed(interview)
    if await latest_consent(db, interview.id) is None:
        raise Conflict("Please review and accept the interview information first.", code="consent_required")
    await enforce_rate_limit("portal_resume_upload", str(interview.id), limit=10, window_s=3600)
    data = await _read_limited(file, get_settings().max_resume_bytes, "resume")
    service = deps.get_resume_service()
    resume = await _call(
        service.accept_upload,
        db,
        organization_id=interview.organization_id,
        candidate_id=interview.candidate_id,
        interview_id=interview.id,
        job_id=interview.job_id,
        filename=file.filename or "resume",
        declared_content_type=file.content_type,
        data=data,
        uploaded_by_candidate=True,
    )
    await audit(
        db,
        principal,
        "candidate.resume_upload",
        resource_type="resume",
        resource_id=str(getattr(resume, "id", "")),
        details={"size_bytes": len(data)},
        request=request,
    )
    if not isinstance(resume, CandidateResume):
        resume = (
            await db.get(CandidateResume, getattr(resume, "id", None))
            if getattr(resume, "id", None)
            else None
        )
    await _open_link_resume_uploaded(db, interview, resume)
    return await _resume_payload(db, interview, resume)


@router.get("/interviews/{interview_id}/resume", response_model=PortalResume)
async def portal_resume_status(
    interview_id: uuid.UUID, principal: CandidatePrincipal, db: DbSession
) -> PortalResume:
    interview = await _interview(db, principal, interview_id)
    return await _resume_payload(db, interview, await _latest_resume(db, interview))


# =============================================================== round


async def _validate_round(db: AsyncSession, interview: Interview, round_id: uuid.UUID) -> Round:
    round_ = await db.get(Round, round_id)
    if round_ is None or round_.job_id != interview.job_id or not round_.is_active:
        raise ValidationFailed("That round is not part of this interview.", code="invalid_round")
    if not round_.candidate_selectable:
        raise ValidationFailed("That round cannot be selected.", code="round_not_selectable")
    return round_


@router.post("/interviews/{interview_id}/round", response_model=PortalInterview)
async def portal_select_round(
    interview_id: uuid.UUID,
    body: RoundSelectionRequest,
    request: Request,
    principal: CandidatePrincipal,
    db: DbSession,
) -> PortalInterview:
    interview = await _interview(db, principal, interview_id)
    _ensure_not_completed(interview)
    if not interview.allow_round_selection:
        raise Conflict(
            "Round selection is not available for this interview.", code="round_selection_disabled"
        )
    latest = await _latest_session(db, interview.id)
    if latest is not None and latest.status not in (
        SessionStatus.CREATED.value,
        SessionStatus.PREPARING.value,
        SessionStatus.READY.value,
    ):
        raise Conflict("The interview has already started.", code="session_already_started")
    round_ = await _validate_round(db, interview, body.round_id)
    if latest is not None and latest.round_id != round_.id:
        raise Conflict("A session was already prepared for another round.", code="session_already_created")
    interview.round_id = round_.id
    await audit(
        db,
        principal,
        "candidate.round_selected",
        resource_type="interview",
        resource_id=str(interview.id),
        details={"round_id": str(round_.id)},
        request=request,
    )
    await db.flush()
    return await portal_interview(interview_id, principal, db)


# ========================================================= system check


@router.post("/interviews/{interview_id}/system-check", response_model=SystemCheckResponse)
async def portal_system_check(
    interview_id: uuid.UUID, body: SystemCheckRequest, principal: CandidatePrincipal, db: DbSession
) -> SystemCheckResponse:
    interview = await _interview(db, principal, interview_id)
    # The lobby's "check your camera and microphone again" works mid-interview, also after the start deadline (S7).
    await ensure_access(db, interview)
    if interview.status == InterviewStatus.COMPLETED.value:
        raise Conflict("This interview has already been completed.", code="interview_completed")
    policy = (await _policy(db, interview)).snapshot.integrity
    issues: list[str] = []
    if not body.microphone:
        issues.append("microphone_unavailable")
    if not body.speakers:
        issues.append("speakers_unavailable")
    if policy.enabled and policy.camera_required and not body.camera:
        issues.append("camera_required")
    if policy.enabled and policy.require_fullscreen and body.fullscreen is False:
        issues.append("fullscreen_unavailable")
    if policy.enabled and policy.screen_share_required and body.screen_share is False:
        issues.append("screen_share_required")
    blocking = set(issues)
    if not body.webgl:
        issues.append("webgl_unavailable_2d_fallback")
    if body.displays is not None and body.displays > 1:
        issues.append("multiple_displays")
    if body.network is not None and (
        (body.network.rtt_ms is not None and body.network.rtt_ms > 800)
        or (body.network.downlink_mbps is not None and body.network.downlink_mbps < 1.0)
    ):
        issues.append("slow_network")
    now = datetime.now(UTC)
    passed = not blocking
    interview.system_check = {
        **body.model_dump(mode="json"),
        "passed": passed,
        "issues": issues,
        "recorded_at": now.isoformat(),
    }
    return SystemCheckResponse(passed=passed, issues=issues, recorded_at=now)


# ============================================================== session


@router.post("/interviews/{interview_id}/session", response_model=PortalSessionCreateResponse)
async def portal_create_session(
    interview_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: CandidatePrincipal,
    db: DbSession,
    body: SessionCreateRequest | None = None,
) -> PortalSessionCreateResponse:
    interview = await _interview(db, principal, interview_id)
    # Resuming an in-progress session must work after the invitation's start deadline (S7).
    await ensure_access(db, interview)
    if interview.status == InterviewStatus.COMPLETED.value:
        raise Conflict("This interview has already been completed.", code="interview_completed")
    if await latest_consent(db, interview.id) is None:
        raise Conflict("Please review and accept the interview information first.", code="consent_required")
    await enforce_rate_limit("portal_session_create", str(interview.id), limit=20, window_s=60)
    requested = body.round_id if body is not None else None
    if requested is not None and requested != interview.round_id:
        if not interview.allow_round_selection:
            raise Conflict(
                "Round selection is not available for this interview.", code="round_selection_disabled"
            )
        interview.round_id = (await _validate_round(db, interview, requested)).id
    if interview.allow_round_selection and interview.round_id is None:
        raise Conflict("Please choose a round first.", code="round_required")
    await _open_link_resume_guard(db, interview)
    await _intake_start_guard(db, interview)
    service = deps.get_session_service()
    session = await _call(
        service.create_or_resume, db, interview_id=interview.id, round_id=interview.round_id
    )
    session_id = getattr(session, "id", None) or (session.get("id") if isinstance(session, dict) else None)
    raw_status = getattr(session, "status", None) or (
        session.get("status") if isinstance(session, dict) else None
    )
    if session_id is None:
        raise ServiceUnavailable(
            "The interview could not be prepared.", code="session_unavailable", retry_after=5
        )
    try:
        session_status = SessionStatus(str(raw_status))
    except ValueError:
        session_status = SessionStatus.CREATED
    await audit(
        db,
        principal,
        "candidate.session_start",
        resource_type="session",
        resource_id=str(session_id),
        request=request,
    )
    return PortalSessionCreateResponse(session_id=uuid.UUID(str(session_id)), status=session_status)


@router.get("/sessions/{session_id}", response_model=PortalSession)
async def portal_session(
    session_id: uuid.UUID, principal: CandidatePrincipal, db: DbSession
) -> PortalSession:
    row, _interview_row = await _session(db, principal, session_id)
    config = row.round_config if isinstance(row.round_config, dict) else {}
    details: dict[str, Any] = {}
    try:
        service = deps.get_session_service()
        view = await _call(service.candidate_view, db, row.id)
        if isinstance(view, dict):
            details = view
    except ServiceUnavailable:
        details = {}
    report = await _released_report(db, row.id)
    report_mode = await resolve_candidate_report_mode(db, row)
    return PortalSession(
        id=row.id,
        interview_id=row.interview_id,
        status=_session_status(row),
        round_name=config.get("name"),
        round_type=config.get("round_type"),
        duration_minutes=config.get("duration_minutes"),
        started_at=row.started_at,
        ended_at=row.ended_at,
        end_reason=row.end_reason,
        candidate_report_available=report is not None,
        candidate_report_mode=report_mode.value,
        details={k: v for k, v in details.items() if k not in CANDIDATE_REPORT_HIDDEN_KEYS},
    )


@router.post("/sessions/{session_id}/ws-ticket", response_model=WsTicketResponse)
async def portal_ws_ticket(
    session_id: uuid.UUID, request: Request, principal: CandidatePrincipal, db: DbSession
) -> WsTicketResponse:
    row, interview = await _session(db, principal, session_id)
    _ensure_session_open(row)
    _raise_if_refused(
        access_window.continue_refusal(interview, row)
    )  # reconnects outlive the start deadline (S7)
    if await latest_consent(db, interview.id) is None:
        raise Conflict("Please review and accept the interview information first.", code="consent_required")
    await enforce_rate_limit("portal_ws_ticket", str(row.id), limit=30, window_s=60)
    settings = get_settings()
    ticket = await issue_ws_ticket(row.id, row.candidate_id, row.interview_id)
    return WsTicketResponse(
        ticket=ticket,
        ws_url=build_ws_url(
            scheme=request.url.scheme,
            host=request.headers.get("host"),
            session_id=str(row.id),
            public_base_url=settings.public_base_url,
        ),
        ws_path=ws_path(str(row.id)),
        expires_in=settings.ws_ticket_ttl_seconds,
    )


_NOT_STARTED_VALUES = frozenset(
    {SessionStatus.CREATED.value, SessionStatus.PREPARING.value, SessionStatus.READY.value}
)


@router.post("/sessions/{session_id}/end", response_model=PortalSessionEndResponse)
async def portal_end_session(
    session_id: uuid.UUID, body: PortalSessionEndRequest, principal: CandidatePrincipal, db: DbSession
) -> PortalSessionEndResponse:
    """The candidate ends their interview while the WebSocket is down (S6, audit B6).

    The room that holds the session (on any instance) hears ``session.end_requested`` on the session bus and
    ends it exactly like a ``control.end_interview`` message; when no room is attached anywhere, the lifecycle
    job ends it offline a few seconds later. Idempotent: an ended session answers ``already_ended``. Before the
    interview has started there is nothing to end (``not_started``): the candidate leaves and may come back to
    the link before its deadline, as the WebSocket path already behaved.
    """
    row, _interview_row = await _session(db, principal, session_id)
    if _session_status(row) in TERMINAL_STATUSES or row.status == SessionStatus.COMPLETING.value:
        return PortalSessionEndResponse(status="already_ended")
    if row.status in _NOT_STARTED_VALUES:
        return PortalSessionEndResponse(status="not_started")
    await enforce_rate_limit("portal_end_session", str(row.id), limit=10, window_s=60)
    sid = str(row.id)
    await publish_session_event(
        sid, {"type": "session.end_requested", "reason": body.reason, "actor": "candidate"}
    )
    await enqueue(
        "session.end",
        {"session_id": sid, "reason": body.reason, "actor": "candidate"},
        dedupe_key=f"session.end:{sid}:candidate",
        delay_seconds=5,
        db=db,
    )
    log.info("portal.end_requested", session_id=sid, reason=body.reason)
    return PortalSessionEndResponse(status="ending")


# ============================================================ integrity


@router.post(
    "/sessions/{session_id}/frames", response_model=FrameUploadAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def portal_frame(
    session_id: uuid.UUID,
    principal: CandidatePrincipal,
    db: DbSession,
    file: Annotated[UploadFile, File(description="JPEG/PNG/WebP frame")],
    kind: Annotated[Literal["camera", "screen"], Form()] = "camera",
    captured_at: Annotated[str | None, Form(max_length=64)] = None,
    trigger: Annotated[Literal["periodic", "face_check"], Form()] = "periodic",
    face_check: Annotated[str | None, Form(max_length=2048)] = None,
) -> FrameUploadAccepted:
    row, _interview_row = await _session(db, principal, session_id)
    _ensure_session_open(row)
    ensure_storage_headroom()  # S3: never fill the disk that also holds the database
    # Policy, consent (explicit declines block), size/type validation and frame
    # rate are enforced by IntegrityService.submit_frame; its errors (400, 403
    # frames_not_enabled / consent_required, 409, 413, 415, 422, 429) pass
    # through unchanged. Read at most one byte over the limit.
    # ``trigger=face_check``: the one snapshot of an on-device face-check interval
    # (``face_check`` JSON: reason, interval id, duration, motionless face boxes).
    data = await file.read(get_settings().max_frame_bytes + 1)
    service = deps.get_integrity_service()
    extra: dict[str, Any] = {}
    if trigger != "periodic":
        extra["trigger"] = trigger
    if face_check:
        extra["face_check"] = face_check
    media_id = await _call(
        service.submit_frame,
        str(row.id),
        kind=kind,
        image=data,
        content_type=file.content_type or "application/octet-stream",
        captured_at=captured_at or datetime.now(UTC).isoformat(),
        **extra,
    )
    return FrameUploadAccepted(accepted=True, media_id=str(media_id) if media_id else None)


@router.post("/sessions/{session_id}/integrity-events", response_model=IntegrityEventsResponse)
async def portal_integrity_events(
    session_id: uuid.UUID, body: IntegrityEventsRequest, principal: CandidatePrincipal, db: DbSession
) -> IntegrityEventsResponse:
    row, interview = await _session(db, principal, session_id)
    _ensure_session_open(row)
    await enforce_rate_limit("portal_integrity_events", str(row.id), limit=60, window_s=60)
    service = deps.get_integrity_service()
    outcomes = await _call(service.record_client_events, str(row.id), list(body.events)) or []
    policy = session_policy(interview.policy_snapshot, None).snapshot.integrity
    warnings: list[IntegrityWarningOut] = []
    for outcome in outcomes:
        if outcome.candidate_message and policy.warn_candidate:
            warnings.append(
                IntegrityWarningOut(event_type=outcome.event_type, message=outcome.candidate_message[:500])
            )
        if outcome.action in (
            IntegrityAction.PAUSE_INTERVIEW,
            IntegrityAction.TERMINATE_INTERVIEW,
        ) and objective_action_allowed(outcome.event_type):
            # The live runtime (possibly on another instance) applies the action.
            await publish_session_event(
                row.id,
                {
                    "type": "integrity.action",
                    "event_type": outcome.event_type.value,
                    "action": outcome.action.value,
                    "candidate_message": outcome.candidate_message,
                    "source": "browser",
                },
            )
    return IntegrityEventsResponse(accepted=len(body.events), warnings=warnings)


# ============================================================ recording


def _ensure_recording_open(row: InterviewSession) -> None:
    """Recording uploads continue while the session closes and for a short
    grace period after it ended (the browser sends its last timeslice then)."""
    if not uploads_open(row):
        raise Conflict("This interview session has ended.", code="session_ended")


async def _recording_policy(db: AsyncSession, interview: Interview, kind: str) -> SessionPolicy:
    policy = await _policy(db, interview)
    allowed = policy.video_recording_allowed if kind == "camera" else policy.screen_recording_allowed
    if not allowed:
        raise Forbidden("Video recording is not enabled for this interview.", code="recording_not_enabled")
    return policy


@router.post(
    "/sessions/{session_id}/recording",
    response_model=RecordingChunkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def portal_recording_chunk(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: CandidatePrincipal,
    db: DbSession,
    file: Annotated[
        UploadFile,
        File(
            description="MediaRecorder chunk (video/webm, video/mp4; audio/webm, audio/mp4 without a camera)"
        ),
    ],
    segment: Annotated[int, Form(ge=0, le=99_999)],
    kind: Annotated[Literal["camera", "screen"], Form()] = "camera",
    part_id: Annotated[uuid.UUID | None, Form(description="One id per browser recorder run")] = None,
    part_started_at: Annotated[str | None, Form(max_length=64)] = None,
    media_start_ms: Annotated[int | None, Form(ge=0, le=6 * 3600 * 1000)] = None,
    media_end_ms: Annotated[int | None, Form(ge=0, le=6 * 3600 * 1000)] = None,
    session_ms: Annotated[int | None, Form(ge=0, le=48 * 3600 * 1000)] = None,
    client_sent_at: Annotated[int | None, Form(ge=0)] = None,
    audio_only: Annotated[bool, Form()] = False,
) -> RecordingChunkResponse:
    row, interview = await _session(db, principal, session_id)
    _ensure_recording_open(row)
    policy = await _recording_policy(db, interview, kind)
    # Normal load is ~6 chunks a minute per kind (10 s timeslices); 30 leaves room for retries (S3).
    await enforce_rate_limit("portal_recording", str(row.id), limit=30, window_s=60)
    ensure_storage_headroom()  # before reading the body
    settings = get_settings()
    content_type = base_type(file.content_type, kind=kind)
    if content_type is None:
        raise UnsupportedMediaType(
            "Recording chunks must be video/webm or video/mp4 (audio/webm or audio/mp4 without a camera).",
            code="unsupported_recording_type",
        )
    data = await _read_limited(file, settings.max_recording_chunk_bytes, "recording chunk")
    result = await accept_segment(
        db,
        row=row,
        kind=kind,
        segment=segment,
        data=data,
        content_type=content_type,
        declared_type=file.content_type,
        part_id=part_id,
        timing=SegmentTiming(
            part_started_at=part_started_at,
            media_start_ms=media_start_ms,
            media_end_ms=media_end_ms,
            session_ms=session_ms,
            client_sent_at=client_sent_at,
            audio_only=audio_only or content_type.startswith("audio/"),
        ),
        retention_days=policy.snapshot.retention.video_days,
        max_bytes=settings.max_recording_chunk_bytes,
    )
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return RecordingChunkResponse(
        media_id=result.media.id,
        segment=segment,
        kind=kind,
        size_bytes=result.media.size_bytes,
        duplicate=result.duplicate,
        part_id=part_id,
    )


@router.post(
    "/sessions/{session_id}/recording/complete",
    response_model=RecordingCompleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def portal_recording_complete(
    session_id: uuid.UUID,
    body: RecordingCompleteRequest,
    principal: CandidatePrincipal,
    db: DbSession,
) -> RecordingCompleteResponse:
    """The browser stopped a recorder and uploaded what it kept: process that part now."""
    row, interview = await _session(db, principal, session_id)
    _ensure_recording_open(row)
    await _recording_policy(db, interview, body.kind)
    await enforce_rate_limit("portal_recording_complete", str(row.id), limit=30, window_s=60)
    await enqueue(
        FINALIZE_JOB,
        {"session_id": str(row.id), "part_id": str(body.part_id), "close": True, "reason": "browser"},
        dedupe_key=finalize_dedupe_key(row.id, str(body.part_id)),
        delay_seconds=2,
        db=db,
    )
    log.info(
        "portal.recording_complete", session_id=str(row.id), part=str(body.part_id), segments=body.segments
    )
    return RecordingCompleteResponse(part_id=body.part_id, queued=True)


# =============================================================== report


def _candidate_safe_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {}
    return {k: v for k, v in content.items() if k not in CANDIDATE_REPORT_HIDDEN_KEYS}


@router.get("/sessions/{session_id}/report", response_model=CandidateReportOut)
async def portal_report(
    session_id: uuid.UUID, request: Request, principal: CandidatePrincipal, db: DbSession
) -> CandidateReportOut:
    row, _interview_row = await _session(db, principal, session_id)
    report = await _released_report(db, row.id)
    if report is None:
        pending = await db.scalar(
            select(Report.status)
            .where(Report.session_id == row.id, Report.type == ReportType.CANDIDATE.value)
            .order_by(Report.version.desc())
            .limit(1)
        )
        raise NotFound(
            "Your report is not available yet.",
            code="report_not_available",
            details={
                "status": "not_released" if pending == ReportStatus.READY.value else (pending or "pending")
            },
        )
    artifact_url = None
    artifact_expires = None
    if report.artifact_storage_key:
        media = await db.scalar(
            select(MediaAsset).where(
                MediaAsset.storage_key == report.artifact_storage_key, MediaAsset.deleted_at.is_(None)
            )
        )
        if media is not None:
            artifact_url, artifact_expires = signed_url_with_expiry(media.id)
    await audit(
        db,
        principal,
        "report.view",
        resource_type="report",
        resource_id=str(report.id),
        details={"type": "candidate"},
        request=request,
    )
    return CandidateReportOut(
        session_id=row.id,
        report_id=report.id,
        version=report.version,
        generated_at=report.generated_at,
        released_at=report.released_at,
        content=_candidate_safe_content(report.content),
        artifact_url=artifact_url,
        artifact_expires_at=artifact_expires,
    )


__all__ = ["InviteGone", "router"]
