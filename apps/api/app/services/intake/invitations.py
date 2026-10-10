"""Intake invitations: single and batch create, drafts, send, cancel, link rotation, updates, and the intake row
of invitations created elsewhere (INTAKE_INTERVIEW.md 10.1).

Every sent intake is an ordinary ``interviews`` row created by
:func:`app.services.jobs.invitations.create_invitation` with ``intake_managed=True`` (so its generic intake hook
does not insert a second row) plus the ``intake_interviews`` row written here with the frozen
:class:`~app.schemas.intake.IntakeInviteSettings`. The invitation's ``policy_snapshot.integrity.max_reconnects``
is patched to the intake setting ("permitted retries", decision 19).

Settings precedence (create/send): a value the request gives > the organisation defaults
(``intake_org_settings.defaults``); the work-authorisation policy is the request's, else the round
configuration's, else the organisation default. Invitations created outside the intake flow (generic job invite,
open-link round) get their row from :func:`ensure_intake_for_interview`, which takes the duration from the intake
round and the reconnect limit from the frozen policy. Drafts store their effective settings at save time;
sending re-freezes the program name and kit version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.base import NO_VALUE

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound, ServiceUnavailable, ValidationFailed
from app.core.logging import get_logger
from app.db.models import Candidate, IntakeInterview, Interview, Job, Round, User
from app.domain.enums import InterviewStatus, Role
from app.schemas.api.candidates import CandidateCreate
from app.schemas.api.intake_admin import (
    IntakeBatchCreate,
    IntakeBatchCreated,
    IntakeBatchFailed,
    IntakeCandidateIn,
    IntakeCreate,
    IntakeUpdate,
)
from app.schemas.api.interviews import InterviewCreate
from app.schemas.intake import (
    INTAKE_DEFAULT_TITLE,
    INTAKE_MAX_MINUTES,
    INTAKE_MIN_MINUTES,
    IntakeInviteSettings,
    IntakeInviteSettingsIn,
    IntakeRoundConfig,
)
from app.services.candidates.service import create_candidate, ensure_active, get_candidate
from app.services.intake.access import (
    intake_enabled,
    intake_entitled,
    intake_for_interview,
    intake_round_of_interview,
    is_intake_round,
    parse_settings,
)
from app.services.intake.kit import IntakeKitError, load_intake_kit
from app.services.intake.org_settings import OrgIntakeSettings, effective_work_authorization, get_org_settings
from app.services.jobs.invitations import (
    CLOSED_JOB_STATUSES,
    IssuedInvite,
    cancel_invitation,
    create_invitation,
    regenerate_link,
)

log = get_logger(__name__)

_DEFAULTED_FIELDS = ("duration_minutes", "max_reconnects", "expires_in_hours", "presence", "resume_required")
_DRAFT_ONLY_FIELDS = frozenset({"title", "job_id", "round_id", "candidate", "settings"})
_REVIEWER_ROLES = frozenset(
    {Role.SUPER_ADMIN.value, Role.ADMIN.value, Role.RECRUITER.value, Role.INTERVIEWER.value}
)


def _now() -> datetime:
    return datetime.now(UTC)


def _loaded(row: Any, name: str) -> Any:
    """An attribute's value only when already loaded (server defaults of a just-flushed row would otherwise
    trigger a lazy load, which async sessions cannot do)."""
    value = inspect(row).attrs[name].loaded_value
    return None if value is NO_VALUE else value


@dataclass(slots=True, frozen=True)
class KitInfo:
    version: str
    sha256: str


def kit_info() -> KitInfo:
    """Version and sha of the intake kit (503 ``intake_kit_unavailable`` when it cannot be loaded)."""
    try:
        kit = load_intake_kit()
    except IntakeKitError as exc:
        log.error("intake.kit_unavailable", error=str(exc)[:200])
        raise ServiceUnavailable(
            "The intake interview kit is not available. Please contact support.",
            code="intake_kit_unavailable",
        ) from exc
    return KitInfo(version=kit.version, sha256=kit.source.sha256)


def round_intake_config(round_: Round | None) -> IntakeRoundConfig | None:
    config = round_.config if round_ is not None and isinstance(round_.config, dict) else {}
    raw = config.get("intake")
    if raw is None:
        return None
    try:
        return IntakeRoundConfig.model_validate(raw)
    except ValidationError:
        log.warning("intake.round_config_unreadable", round_id=str(round_.id) if round_ else None)
        return None


def round_duration(round_: Round | None) -> int | None:
    """The intake round's planned minutes (config wins over the column), when within 35-50."""
    if round_ is None:
        return None
    config = round_.config if isinstance(round_.config, dict) else {}
    value = config.get("duration_minutes") or round_.duration_minutes
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if INTAKE_MIN_MINUTES <= minutes <= INTAKE_MAX_MINUTES else None


def build_invite_settings(
    settings_in: IntakeInviteSettingsIn,
    org: OrgIntakeSettings,
    round_: Round | None,
    kit: KitInfo,
) -> IntakeInviteSettings:
    """Effective settings of an invitation (request values > organisation defaults)."""
    given = settings_in.model_fields_set
    values: dict[str, Any] = settings_in.model_dump()
    for name in _DEFAULTED_FIELDS:
        if name not in given:
            values[name] = getattr(org.defaults, name)
    values["work_authorization"] = effective_work_authorization(
        round_intake_config(round_), org, settings_in.work_authorization
    )
    values.update(program_name=org.program_name, kit_version=kit.version, kit_sha256=kit.sha256)
    return IntakeInviteSettings.model_validate(values)


# ============================================================================ job / candidate


async def resolve_job_round(
    db: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID, round_id: uuid.UUID | None
) -> tuple[Job, Round]:
    """The job (404 ``job_not_found``) and its intake round: ``round_id`` when that is an active intake round of
    the job, else the job's first active intake round (409 ``job_has_no_intake_round``)."""
    job = await db.get(Job, job_id)
    if job is None or job.organization_id != organization_id:
        raise NotFound("Job not found.", code="job_not_found")
    rounds = (
        await db.scalars(
            select(Round).where(Round.job_id == job.id, Round.is_active.is_(True)).order_by(Round.sequence)
        )
    ).all()
    if round_id is not None:
        round_ = next((r for r in rounds if r.id == round_id), None)
        if round_ is None or not is_intake_round(round_):
            raise Conflict(
                "That round is not an active intake round of this job.",
                code="job_has_no_intake_round",
                details={"round_id": str(round_id), "field": "round_id"},
            )
        return job, round_
    round_ = next((r for r in rounds if is_intake_round(r)), None)
    if round_ is None:
        raise Conflict(
            "This job has no active intake interview round. Add an intake round to the job first.",
            code="job_has_no_intake_round",
            details={"field": "job_id"},
        )
    return job, round_


def ensure_job_open(job: Job) -> None:
    if job.status in CLOSED_JOB_STATUSES:
        raise Conflict(
            "This job is not open for interviews.", code="job_not_open", details={"status": job.status}
        )


async def resolve_candidate(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None,
    candidate_id: uuid.UUID | None,
    candidate: IntakeCandidateIn | None,
) -> tuple[Candidate, bool]:
    """``(candidate, reused)``: the given candidate, else the organisation's candidate with that e-mail, else a
    new one (``source = intake``). Archived candidates answer 409 ``candidate_archived``."""
    if candidate_id is not None:
        row = await get_candidate(db, candidate_id, organization_id)
        ensure_active(row, action="invite them")
        return row, True
    assert candidate is not None  # noqa: S101 - IntakeCreate guarantees exactly one
    existing = await db.scalar(
        select(Candidate).where(
            Candidate.organization_id == organization_id, Candidate.email == candidate.email
        )
    )
    if existing is not None:
        ensure_active(existing, action="invite them")
        return existing, True
    created = await create_candidate(
        db,
        organization_id=organization_id,
        created_by_id=user_id,
        body=CandidateCreate(
            full_name=candidate.full_name, email=candidate.email, phone=candidate.phone, source="intake"
        ),
    )
    return created, False


async def validate_reviewer(
    db: AsyncSession, organization_id: uuid.UUID, reviewer_id: uuid.UUID | None
) -> None:
    """An assigned reviewer must be an active staff user of the organisation (422 ``invalid_reviewer``)."""
    if reviewer_id is None:
        return
    user = await db.get(User, reviewer_id)
    if (
        user is None
        or user.organization_id != organization_id
        or not user.is_active
        or user.role not in _REVIEWER_ROLES
    ):
        raise ValidationFailed(
            "Choose an active team member of this organisation as the reviewer.",
            code="invalid_reviewer",
            details={"field": "assigned_reviewer_id"},
        )


# ================================================================================= create / send


@dataclass(slots=True)
class CreatedIntake:
    intake: IntakeInterview
    candidate: Candidate
    candidate_reused: bool
    issued: IssuedInvite | None = None


def _patch_max_reconnects(interview: Interview, max_reconnects: int) -> None:
    snapshot = dict(interview.policy_snapshot or {})
    integrity = dict(snapshot.get("integrity") or {})
    integrity["max_reconnects"] = int(max_reconnects)
    snapshot["integrity"] = integrity
    snapshot["candidate_report_mode"] = "never"  # intake has no candidate report (S21)
    interview.policy_snapshot = snapshot  # new dict: JSONB change detection


def _freeze_no_candidate_report(interview: Interview) -> bool:
    """True when the snapshot changed (the row's ``updated_at`` then needs reloading after the flush)."""
    snapshot = dict(interview.policy_snapshot or {})
    if snapshot.get("candidate_report_mode") == "never":
        return False
    snapshot["candidate_report_mode"] = "never"
    interview.policy_snapshot = snapshot
    return True


async def send_intake(
    db: AsyncSession,
    intake: IntakeInterview,
    *,
    settings: IntakeInviteSettings,
    invited_by_id: uuid.UUID | None,
    org: OrgIntakeSettings,
    kit: KitInfo,
    expires_in_hours: int | None = None,
) -> IssuedInvite:
    """Create the ``interviews`` row (link + optional access code, returned once) and freeze the settings."""
    hours = expires_in_hours or settings.expires_in_hours
    issued = await create_invitation(
        db,
        organization_id=intake.organization_id,
        invited_by_id=invited_by_id,
        body=InterviewCreate(
            candidate_id=intake.candidate_id,
            job_id=intake.job_id,
            round_id=intake.round_id,
            allow_round_selection=False,
            expires_in_hours=hours,
            require_access_code=settings.require_access_code,
            scheduled_for=settings.scheduled_for,
        ),
        intake_managed=True,
    )
    interview = issued.interview
    _patch_max_reconnects(interview, settings.max_reconnects)
    frozen = settings.model_copy(
        update={
            "expires_in_hours": hours,
            "require_access_code": issued.access_code is not None,
            "program_name": org.program_name,
            "kit_version": kit.version,
            "kit_sha256": kit.sha256,
        }
    )
    intake.interview_id = interview.id
    intake.status = "active"
    intake.sent_at = _now()
    intake.settings = frozen.model_dump(mode="json")
    await db.flush()
    log.info("intake.sent", intake_id=str(intake.id), interview_id=str(interview.id))
    return issued


async def create_intake(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    body: IntakeCreate,
    batch_id: uuid.UUID | None = None,
    job_round: tuple[Job, Round] | None = None,
    org: OrgIntakeSettings | None = None,
    kit: KitInfo | None = None,
) -> CreatedIntake:
    """Create an intake (draft when ``body.send`` is false, otherwise sent)."""
    job, round_ = job_round or await resolve_job_round(db, organization_id, body.job_id, body.round_id)
    ensure_job_open(job)
    org = org or await get_org_settings(db, organization_id)
    kit = kit or kit_info()
    await validate_reviewer(db, organization_id, body.settings.assigned_reviewer_id)
    candidate, reused = await resolve_candidate(
        db, organization_id, user_id=user_id, candidate_id=body.candidate_id, candidate=body.candidate
    )
    settings = build_invite_settings(body.settings, org, round_, kit)
    intake = IntakeInterview(
        organization_id=organization_id,
        candidate_id=candidate.id,
        job_id=job.id,
        round_id=round_.id,
        batch_id=batch_id,
        status="draft",
        title=(body.title or "").strip() or INTAKE_DEFAULT_TITLE,
        target_role=body.target_role,
        settings=settings.model_dump(mode="json"),
        assigned_reviewer_id=settings.assigned_reviewer_id,
        created_by_id=user_id,
        notes=body.notes,
    )
    db.add(intake)
    await db.flush()
    issued = None
    if body.send:
        issued = await send_intake(db, intake, settings=settings, invited_by_id=user_id, org=org, kit=kit)
    return CreatedIntake(intake=intake, candidate=candidate, candidate_reused=reused, issued=issued)


async def send_draft(
    db: AsyncSession,
    intake: IntakeInterview,
    *,
    invited_by_id: uuid.UUID | None,
    expires_in_hours: int | None = None,
) -> IssuedInvite:
    """Draft -> active (409 ``intake_not_draft`` otherwise)."""
    if intake.status != "draft":
        raise Conflict("Only draft intake interviews can be sent.", code="intake_not_draft")
    job, round_ = await resolve_job_round(db, intake.organization_id, intake.job_id, intake.round_id)
    ensure_job_open(job)
    intake.round_id = round_.id  # a draft whose round was removed falls back to the job's intake round
    candidate = await db.get(Candidate, intake.candidate_id)
    if candidate is not None:
        ensure_active(candidate, action="invite them")
    org = await get_org_settings(db, intake.organization_id)
    return await send_intake(
        db,
        intake,
        settings=parse_settings(intake),
        invited_by_id=invited_by_id,
        org=org,
        kit=kit_info(),
        expires_in_hours=expires_in_hours,
    )


@dataclass(slots=True)
class BatchResult:
    batch_id: uuid.UUID
    created: list[tuple[IntakeBatchCreated, IntakeInterview]] = field(default_factory=list)
    failed: list[IntakeBatchFailed] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return sum(1 for item, _row in self.created if item.invite_url)


async def create_batch(
    db: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID | None, body: IntakeBatchCreate
) -> BatchResult:
    """One intake per row, each in its own savepoint: a failing row (``failed[{row, email, code, message}]``)
    never affects the others. Job, round and reviewer problems fail the whole request."""
    limit = int(get_settings().intake_batch_max_rows)
    if len(body.rows) > limit:
        raise ValidationFailed(
            f"A batch can contain at most {limit} candidates.",
            code="batch_too_large",
            details={"field": "rows", "max_rows": limit},
        )
    job, round_ = await resolve_job_round(db, organization_id, body.job_id, body.round_id)
    ensure_job_open(job)
    await validate_reviewer(db, organization_id, body.settings.assigned_reviewer_id)
    org = await get_org_settings(db, organization_id)
    kit = kit_info()
    result = BatchResult(batch_id=uuid.uuid4())
    seen: set[str] = set()
    for index, row in enumerate(body.rows):
        if row.email in seen:
            result.failed.append(
                IntakeBatchFailed(
                    row=index,
                    email=row.email,
                    code="duplicate_row",
                    message="This e-mail address appears earlier in the batch.",
                )
            )
            continue
        seen.add(row.email)
        request = IntakeCreate(
            candidate=IntakeCandidateIn(full_name=row.full_name, email=row.email),
            job_id=job.id,
            round_id=round_.id,
            target_role=row.target_role,
            settings=body.settings,
            notes=row.notes,
            send=body.send,
        )
        try:
            async with db.begin_nested():
                created = await create_intake(
                    db,
                    organization_id=organization_id,
                    user_id=user_id,
                    body=request,
                    batch_id=result.batch_id,
                    job_round=(job, round_),
                    org=org,
                    kit=kit,
                )
        except AppError as exc:
            result.failed.append(
                IntakeBatchFailed(row=index, email=row.email, code=exc.code, message=exc.message)
            )
            continue
        except IntegrityError:
            result.failed.append(
                IntakeBatchFailed(
                    row=index,
                    email=row.email,
                    code="conflict",
                    message="This candidate was changed by another request. Please try again.",
                )
            )
            continue
        issued = created.issued
        result.created.append(
            (
                IntakeBatchCreated(
                    row=index,
                    intake_id=created.intake.id,
                    candidate_id=created.candidate.id,
                    email=created.candidate.email,
                    candidate_reused=created.candidate_reused,
                    invite_url=issued.invite_url if issued else None,
                    access_code=issued.access_code if issued else None,
                    expires_at=issued.interview.invite_expires_at if issued else None,
                ),
                created.intake,
            )
        )
    log.info(
        "intake.batch_created",
        batch_id=str(result.batch_id),
        created=len(result.created),
        failed=len(result.failed),
    )
    return result


# ========================================================================= cancel / link / update


async def cancel_intake(db: AsyncSession, intake: IntakeInterview, *, reason: str | None) -> str | None:
    """Cancel (drafts directly; active: the invitation too, which ends a live session). Returns the previous
    status, or ``None`` when it was already cancelled."""
    if intake.status == "cancelled":
        return None
    previous = intake.status
    if intake.status == "active" and intake.interview_id is not None:
        interview = await db.get(Interview, intake.interview_id)
        if interview is not None and interview.status != InterviewStatus.CANCELLED.value:
            await cancel_invitation(db, interview, reason=reason)
    intake.status = "cancelled"
    intake.cancelled_at = _now()
    await db.flush()
    return previous


async def regenerate_intake_link(
    db: AsyncSession, intake: IntakeInterview, *, expires_in_hours: int | None
) -> IssuedInvite:
    if intake.status == "cancelled":
        raise Conflict("This intake interview was cancelled.", code="intake_cancelled")
    if intake.status != "active" or intake.interview_id is None:
        raise Conflict("Send the intake interview before creating a new link.", code="intake_not_sent")
    interview = await db.get(Interview, intake.interview_id)
    if interview is None:
        raise Conflict("Send the intake interview before creating a new link.", code="intake_not_sent")
    issued = await regenerate_link(db, interview, expires_in_hours=expires_in_hours, rotate_access_code=None)
    if expires_in_hours:
        settings = parse_settings(intake).model_copy(update={"expires_in_hours": expires_in_hours})
        intake.settings = settings.model_dump(mode="json")
    await db.flush()
    return issued


async def update_intake(
    db: AsyncSession, intake: IntakeInterview, body: IntakeUpdate, *, user_id: uuid.UUID | None
) -> list[str]:
    """Apply a PATCH; returns the changed field names."""
    given = set(body.model_fields_set)
    if intake.status == "cancelled" and given - {"notes"}:
        raise Conflict("A cancelled intake interview cannot be changed.", code="intake_cancelled")
    if intake.status != "draft":
        not_allowed = sorted(given & _DRAFT_ONLY_FIELDS)
        if not_allowed:
            raise Conflict(
                "These fields can only be changed while the intake interview is a draft.",
                code="intake_not_draft",
                details={"fields": not_allowed},
            )
        if "target_role" in given and intake.onboarding_submitted_at is not None:
            raise Conflict(
                "The candidate already submitted the details form; the target role can no longer change.",
                code="target_role_locked",
                details={"field": "target_role"},
            )
    changed: list[str] = []
    settings = parse_settings(intake)
    settings_changed = False
    if "title" in given and body.title and body.title != intake.title:
        intake.title = body.title
        changed.append("title")
    if "target_role" in given and body.target_role != intake.target_role:
        intake.target_role = body.target_role
        changed.append("target_role")
    if "notes" in given and body.notes != intake.notes:
        intake.notes = body.notes
        changed.append("notes")
    if "assigned_reviewer_id" in given and body.assigned_reviewer_id != intake.assigned_reviewer_id:
        await validate_reviewer(db, intake.organization_id, body.assigned_reviewer_id)
        intake.assigned_reviewer_id = body.assigned_reviewer_id
        settings = settings.model_copy(update={"assigned_reviewer_id": body.assigned_reviewer_id})
        settings_changed = True
        changed.append("assigned_reviewer_id")
    if "notify_on_completion" in given and body.notify_on_completion is not None:
        if body.notify_on_completion != settings.notify_on_completion:
            settings = settings.model_copy(update={"notify_on_completion": body.notify_on_completion})
            settings_changed = True
            changed.append("notify_on_completion")
    if intake.status == "draft":
        if {"job_id", "round_id"} & given:
            job_id = body.job_id or intake.job_id
            round_id = (
                body.round_id
                if "round_id" in given
                else (intake.round_id if job_id == intake.job_id else None)
            )
            job, round_ = await resolve_job_round(db, intake.organization_id, job_id, round_id)
            if job.id != intake.job_id or round_.id != intake.round_id:
                intake.job_id, intake.round_id = job.id, round_.id
                changed.append("job")
        if "candidate" in given and body.candidate is not None:
            candidate, _reused = await resolve_candidate(
                db, intake.organization_id, user_id=user_id, candidate_id=None, candidate=body.candidate
            )
            if candidate.id != intake.candidate_id:
                intake.candidate_id = candidate.id
                changed.append("candidate")
        if "settings" in given and body.settings is not None:
            await validate_reviewer(db, intake.organization_id, body.settings.assigned_reviewer_id)
            org = await get_org_settings(db, intake.organization_id)
            draft_round = await db.get(Round, intake.round_id) if intake.round_id else None
            settings = build_invite_settings(body.settings, org, draft_round, kit_info())
            intake.assigned_reviewer_id = settings.assigned_reviewer_id
            settings_changed = True
            changed.append("settings")
    if settings_changed:
        intake.settings = settings.model_dump(mode="json")
    await db.flush()
    return changed


# =============================================================== rows for other invitations


async def ensure_intake_for_interview(
    db: AsyncSession, interview: Interview, *, round_: Round | None = None
) -> IntakeInterview | None:
    """The interview's intake row, created when missing (generic invite, open-link round, legacy rows).
    ``None`` when intake is disabled or the interview's round is not an intake round. Safe under concurrency
    (``INSERT ... ON CONFLICT DO NOTHING`` on the unique interview index). T1's session init uses it too."""
    if not intake_enabled():
        return None
    existing = await intake_for_interview(db, interview.id)
    if existing is None and not await intake_entitled(db, interview.organization_id):
        return None  # G8: never start intake for an organisation whose plan does not include it
    if existing is not None:
        return existing
    round_ = round_ if is_intake_round(round_) else await intake_round_of_interview(db, interview)
    if round_ is None:
        return None
    org = await get_org_settings(db, interview.organization_id)
    kit = kit_info()
    frozen_report_mode = _freeze_no_candidate_report(interview)  # S21: generic invites / open-link rounds too
    snapshot = interview.policy_snapshot if isinstance(interview.policy_snapshot, dict) else {}
    raw_integrity = snapshot.get("integrity")
    integrity: dict[str, Any] = raw_integrity if isinstance(raw_integrity, dict) else {}
    try:
        max_reconnects = int(integrity.get("max_reconnects", org.defaults.max_reconnects))
    except (TypeError, ValueError):
        max_reconnects = org.defaults.max_reconnects
    created_at = _loaded(interview, "created_at") or _now()
    hours = (
        round((interview.invite_expires_at - created_at).total_seconds() / 3600)
        if interview.invite_expires_at
        else None
    )
    settings = IntakeInviteSettings(
        duration_minutes=round_duration(round_) or org.defaults.duration_minutes,
        presence=org.defaults.presence,
        max_reconnects=min(max(max_reconnects, 0), 20),
        expires_in_hours=min(max(hours or org.defaults.expires_in_hours, 1), 1440),
        scheduled_for=interview.scheduled_for,
        require_access_code=interview.access_code_hash is not None,
        resume_required=org.defaults.resume_required,
        work_authorization=effective_work_authorization(round_intake_config(round_), org),
        program_name=org.program_name,
        kit_version=kit.version,
        kit_sha256=kit.sha256,
    )
    await db.flush()
    if frozen_report_mode:
        # The UPDATE expired the server-maintained updated_at; callers serialise the interview right after
        # (a lazy load would fail in an async session).
        await db.refresh(interview, attribute_names=["updated_at"])
    await db.execute(
        pg_insert(IntakeInterview)
        .values(
            id=uuid.uuid4(),
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=round_.id,
            interview_id=interview.id,
            status="active",
            title=INTAKE_DEFAULT_TITLE,
            settings=settings.model_dump(mode="json"),
            created_by_id=interview.invited_by_id,
            sent_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=["interview_id"], index_where=text("interview_id IS NOT NULL"))
    )
    row = await intake_for_interview(db, interview.id)
    if row is not None:
        log.info("intake.row_ensured", intake_id=str(row.id), interview_id=str(interview.id))
    return row


__all__ = [
    "BatchResult",
    "CreatedIntake",
    "KitInfo",
    "build_invite_settings",
    "cancel_intake",
    "create_batch",
    "create_intake",
    "ensure_intake_for_interview",
    "ensure_job_open",
    "kit_info",
    "regenerate_intake_link",
    "resolve_candidate",
    "resolve_job_round",
    "round_duration",
    "round_intake_config",
    "send_draft",
    "send_intake",
    "update_intake",
    "validate_reviewer",
]
