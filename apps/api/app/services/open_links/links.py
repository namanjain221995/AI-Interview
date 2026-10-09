"""Open link CRUD, tokens, computed state, policy summary and serialisation
(OPEN_LINKS.md sections 1.2, 3.2, 9.2)."""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import generate_access_code, hash_access_code_async
from app.db.models import InterviewLink, Job, LinkRegistration, Organization, Round, User
from app.domain.enums import JobStatus
from app.schemas.api.open_links import (
    DataUse,
    DuplicatePolicy,
    FormSchemaOut,
    IdName,
    JobBrief,
    LinkCounts,
    LinkInfo,
    LinkRound,
    OpenLink,
    OpenLinkCreate,
    OpenLinkSummary,
    OpenLinkUpdate,
    PolicySummary,
    PublicForm,
    PublicFormField,
    PublicRound,
    RetentionDays,
)
from app.schemas.policies import PolicySnapshot
from app.services.identity import get_organization_settings
from app.services.jobs.policy import build_policy_snapshot
from app.services.open_links.constants import (
    DEFAULT_DUPLICATE_POLICY,
    LINK_TOKEN_PATTERN,
    MAX_ROUNDS,
    LinkState,
    LinkStatus,
    RegistrationStatus,
)
from app.services.open_links.fairness import enforce_form
from app.services.open_links.form_schema import default_schema, enabled_fields, normalize_schema

TOKEN_RE = re.compile(LINK_TOKEN_PATTERN)
CLOSED_JOB_STATUSES = frozenset({JobStatus.CLOSED.value, JobStatus.ARCHIVED.value})
BLOCKED = RegistrationStatus.BLOCKED.value


def _now() -> datetime:
    return datetime.now(UTC)


def generate_public_token() -> str:
    return secrets.token_urlsafe(24)


def link_url(token: str) -> str:
    return f"{get_settings().public_base_url}/j/{token}"


def personal_url(token: str) -> str:
    return f"{get_settings().public_base_url}/r/{token}"


def valid_token(token: str | None) -> bool:
    return bool(token) and TOKEN_RE.match(token or "") is not None


# ============================================================== lookups


async def get_link(
    db: AsyncSession, link_id: uuid.UUID, organization_id: uuid.UUID, *, for_update: bool = False
) -> InterviewLink:
    stmt = select(InterviewLink).where(InterviewLink.id == link_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    link = await db.scalar(stmt)
    if link is None or link.organization_id != organization_id:
        raise NotFound("Interview link not found.", code="not_found")
    return link


async def find_link_by_token(db: AsyncSession, token: str) -> InterviewLink | None:
    if not valid_token(token):
        return None
    return await db.scalar(select(InterviewLink).where(InterviewLink.public_token == token))


async def lock_link(db: AsyncSession, link_id: uuid.UUID) -> InterviewLink:
    link = await db.scalar(
        select(InterviewLink)
        .where(InterviewLink.id == link_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if link is None:
        raise NotFound("This link isn't valid.", code="link_not_found")
    return link


async def job_rounds(db: AsyncSession, job_id: uuid.UUID) -> list[Round]:
    """Active rounds of a job in job order."""
    return list(
        (
            await db.scalars(
                select(Round)
                .where(Round.job_id == job_id, Round.is_active.is_(True))
                .order_by(Round.sequence, Round.created_at)
            )
        ).all()
    )


def round_minutes(round_: Round) -> int:
    config = round_.config if isinstance(round_.config, dict) else {}
    value = config.get("duration_minutes") or round_.duration_minutes
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 0
    return minutes if minutes > 0 else int(round_.duration_minutes or 30)


@dataclass(slots=True)
class PipelineRound:
    sequence: int
    round_id: uuid.UUID
    round: Round | None  # None or inactive/foreign = missing

    @property
    def available(self) -> bool:
        return self.round is not None

    @property
    def name(self) -> str:
        return self.round.name if self.round is not None else "Unavailable round"


async def pipeline(db: AsyncSession, link: InterviewLink) -> list[PipelineRound]:
    ids = list(link.round_ids or [])
    rows = {r.id: r for r in (await db.scalars(select(Round).where(Round.id.in_(ids)))).all()} if ids else {}
    out: list[PipelineRound] = []
    for index, round_id in enumerate(ids):
        row = rows.get(round_id)
        usable = row is not None and row.is_active and row.job_id == link.job_id
        out.append(PipelineRound(index, round_id, row if usable else None))
    return out


async def pipeline_names(db: AsyncSession, link: InterviewLink) -> dict[uuid.UUID, Round]:
    ids = list(link.round_ids or [])
    if not ids:
        return {}
    return {r.id: r for r in (await db.scalars(select(Round).where(Round.id.in_(ids)))).all()}


def next_available(rounds: Sequence[PipelineRound], after: int) -> PipelineRound | None:
    for item in rounds:
        if item.sequence > after and item.available:
            return item
    return None


def first_available(rounds: Sequence[PipelineRound]) -> PipelineRound | None:
    return next_available(rounds, -1)


# ================================================================ state


async def active_registration_count(db: AsyncSession, link_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(LinkRegistration)
            .where(LinkRegistration.link_id == link_id, LinkRegistration.status != BLOCKED)
        )
        or 0
    )


def compute_state(
    link: InterviewLink, job_status: str | None, registrations: int, now: datetime | None = None
) -> str:
    now = now or _now()
    if link.status == LinkStatus.CLOSED.value or (job_status in CLOSED_JOB_STATUSES):
        return LinkState.CLOSED.value
    if link.closes_at is not None and link.closes_at <= now:
        return LinkState.EXPIRED.value
    if link.status == LinkStatus.PAUSED.value:
        return LinkState.PAUSED.value
    if link.opens_at is not None and link.opens_at > now:
        return LinkState.SCHEDULED.value
    if link.max_registrations is not None and registrations >= link.max_registrations:
        return LinkState.FULL.value
    return LinkState.OPEN.value


async def link_state(db: AsyncSession, link: InterviewLink, job: Job | None = None) -> str:
    job = job or await db.get(Job, link.job_id)
    count = await active_registration_count(db, link.id) if link.max_registrations is not None else 0
    return compute_state(link, job.status if job else JobStatus.ARCHIVED.value, count)


async def counts_for(db: AsyncSession, link_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LinkCounts]:
    out = {lid: LinkCounts() for lid in link_ids}
    if not link_ids:
        return out
    rows = (
        await db.execute(
            select(
                LinkRegistration.link_id,
                LinkRegistration.status,
                func.count(),
                func.count().filter(LinkRegistration.flag_review == "open"),
            )
            .where(LinkRegistration.link_id.in_(list(link_ids)))
            .group_by(LinkRegistration.link_id, LinkRegistration.status)
        )
    ).all()
    for link_id, status, count, flagged in rows:
        counts = out[link_id]
        if status == BLOCKED:
            counts.blocked += int(count)
            continue
        counts.registrations += int(count)
        counts.flagged += int(flagged)
        if status in (RegistrationStatus.REGISTERED.value, RegistrationStatus.IN_PROGRESS.value):
            counts.in_progress += int(count)
        elif status == RegistrationStatus.AWAITING_REVIEW.value:
            counts.awaiting_review += int(count)
        elif status == RegistrationStatus.COMPLETED.value:
            counts.completed += int(count)
    return out


# =============================================================== policy


async def effective_policy(db: AsyncSession, organization_id: uuid.UUID, job: Job | None) -> PolicySnapshot:
    return build_policy_snapshot(await get_organization_settings(db, organization_id), job)


def policy_summary(snapshot: PolicySnapshot) -> PolicySummary:
    integrity, recording, retention = snapshot.integrity, snapshot.recording, snapshot.retention
    return PolicySummary(
        video_recording=recording.record_video,
        audio_recording=recording.record_answer_audio,
        screen_recording=recording.record_screen,
        camera_monitoring=integrity.enabled
        and (integrity.camera_required or integrity.camera_frame_analysis),
        screen_monitoring=integrity.enabled
        and (integrity.screen_share_required or integrity.screen_share_analysis),
        candidate_report_mode=str(snapshot.candidate_report_mode.value),
        retention_days=RetentionDays(
            resume=retention.resume_days,
            audio=retention.audio_days,
            video=retention.video_days,
            transcript=retention.transcript_days,
            report=retention.report_days,
        ),
    )


# =========================================================== validation


async def _validate_rounds(db: AsyncSession, job: Job, round_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    if not round_ids:
        raise ValidationFailed("Choose at least one round.", code="no_rounds")
    rounds = await job_rounds(db, job.id)
    order = {r.id: index for index, r in enumerate(rounds)}
    duplicates = {str(r) for r in round_ids if list(round_ids).count(r) > 1}
    bad = sorted({str(r) for r in round_ids if r not in order} | duplicates)
    if bad or len(round_ids) > MAX_ROUNDS:
        raise ValidationFailed(
            f"Choose 1 to {MAX_ROUNDS} different active rounds of this job.",
            code="invalid_rounds",
            details={"round_ids": bad},
        )
    return sorted(round_ids, key=lambda r: order[r])


def _validate_window(opens_at: datetime | None, closes_at: datetime | None) -> None:
    if opens_at is not None and closes_at is not None and closes_at <= opens_at:
        raise ValidationFailed(
            "The closing time must be after the opening time.",
            details={"fields": {"closes_at": "after opens_at"}},
        )


def _form(raw: Any, acknowledged: list[str], *, version: int) -> dict[str, Any]:
    schema = normalize_schema(raw if raw is not None else default_schema(), version=version)
    enforce_form(schema, acknowledged)
    return schema


def _clean_acks(values: Sequence[str]) -> list[str]:
    return sorted({v.strip()[:120] for v in values if isinstance(v, str) and v.strip()})


# ================================================================= CRUD


async def create_link(
    db: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID | None, body: OpenLinkCreate
) -> tuple[InterviewLink, str | None]:
    job = await db.get(Job, body.job_id)
    if job is None or job.organization_id != organization_id:
        raise NotFound("Job not found.")
    if job.status in CLOSED_JOB_STATUSES:
        raise Conflict(
            "This job is not open for interviews.", code="job_not_open", details={"status": job.status}
        )
    round_ids = await _validate_rounds(db, job, body.round_ids)
    _validate_window(body.opens_at, body.closes_at)
    acks = _clean_acks(body.acknowledged_warnings)
    schema = _form(body.form_schema.model_dump() if body.form_schema else None, acks, version=1)
    access_code = generate_access_code() if body.require_access_code else None
    link = InterviewLink(
        organization_id=organization_id,
        job_id=job.id,
        name=body.name,
        description=body.description or None,
        public_token=generate_public_token(),
        status=LinkStatus.ACTIVE.value,
        round_ids=round_ids,
        progression=body.progression,
        opens_at=body.opens_at,
        closes_at=body.closes_at,
        max_registrations=body.max_registrations,
        round_window_hours=body.round_window_hours or get_settings().open_link_default_round_window_hours,
        require_resume=body.require_resume,
        access_code_hash=await hash_access_code_async(access_code) if access_code else None,
        per_ip_hourly_limit=body.per_ip_hourly_limit,
        duplicate_policy=(body.duplicate_policy or DuplicatePolicy()).model_dump(),
        form_schema=schema,
        acknowledged_warnings=acks,
        created_by_id=user_id,
        updated_by_id=user_id,
    )
    db.add(link)
    await db.flush()
    return link, access_code


async def _rounds_in_use(db: AsyncSession, link: InterviewLink) -> list[tuple[int, uuid.UUID]]:
    from app.db.models import LinkRegistrationRound

    rows = (
        await db.execute(
            select(LinkRegistrationRound.sequence, LinkRegistrationRound.round_id)
            .join(LinkRegistration, LinkRegistration.id == LinkRegistrationRound.registration_id)
            .where(LinkRegistration.link_id == link.id)
            .distinct()
        )
    ).all()
    return [(int(r.sequence), r.round_id) for r in rows]


async def update_link(
    db: AsyncSession, link: InterviewLink, body: OpenLinkUpdate, *, user_id: uuid.UUID | None
) -> dict[str, Any]:
    """Apply a partial update; returns ``{"changed": [...], "form_version": n, "new_acks": [...]}``."""
    if link.status == LinkStatus.CLOSED.value:
        raise Conflict("This link is closed and can no longer be changed.", code="link_closed")
    sent = body.model_fields_set
    changed: list[str] = []
    job = await db.get(Job, link.job_id)
    if job is None:
        raise NotFound("Job not found.")
    if "round_ids" in sent and body.round_ids is not None:
        new_ids = await _validate_rounds(db, job, body.round_ids)
        if new_ids != list(link.round_ids):
            used = await _rounds_in_use(db, link)
            broken = sorted({str(rid) for seq, rid in used if seq >= len(new_ids) or new_ids[seq] != rid})
            if broken:
                raise Conflict(
                    "Rounds that candidates already started cannot be removed or reordered.",
                    code="round_in_use",
                    details={"round_ids": broken},
                )
            link.round_ids = new_ids
            changed.append("round_ids")
    opens_at = body.opens_at if "opens_at" in sent else link.opens_at
    closes_at = body.closes_at if "closes_at" in sent else link.closes_at
    _validate_window(opens_at, closes_at)
    simple = {
        "name": body.name,
        "description": body.description,
        "progression": body.progression,
        "opens_at": body.opens_at,
        "closes_at": body.closes_at,
        "max_registrations": body.max_registrations,
        "round_window_hours": body.round_window_hours,
        "require_resume": body.require_resume,
        "per_ip_hourly_limit": body.per_ip_hourly_limit,
    }
    nullable = {"description", "opens_at", "closes_at", "max_registrations"}
    for name, value in simple.items():
        if name not in sent or (value is None and name not in nullable):
            continue
        if name == "description":
            value = value or None
        if getattr(link, name) != value:
            setattr(link, name, value)
            changed.append(name)
    if "duplicate_policy" in sent and body.duplicate_policy is not None:
        policy = body.duplicate_policy.model_dump()
        if policy != {**DEFAULT_DUPLICATE_POLICY, **(link.duplicate_policy or {})}:
            link.duplicate_policy = policy
            changed.append("duplicate_policy")
    acks = (
        _clean_acks(body.acknowledged_warnings)
        if body.acknowledged_warnings is not None
        else list(link.acknowledged_warnings or [])
    )
    new_acks = sorted(set(acks) - set(link.acknowledged_warnings or []))
    if "form_schema" in sent and body.form_schema is not None:
        candidate = normalize_schema(
            body.form_schema.model_dump(), version=int(link.form_schema.get("version", 1))
        )
        if candidate["fields"] != link.form_schema.get("fields"):
            candidate["version"] = int(link.form_schema.get("version", 1)) + 1
            enforce_form(candidate, acks)
            link.form_schema = candidate
            changed.append("form_schema")
        else:
            enforce_form(link.form_schema, acks)
    if acks != list(link.acknowledged_warnings or []):
        link.acknowledged_warnings = acks
        changed.append("acknowledged_warnings")
    if changed:
        link.updated_by_id = user_id
        await db.flush()
    return {"changed": changed, "form_version": int(link.form_schema.get("version", 1)), "new_acks": new_acks}


async def set_status(db: AsyncSession, link: InterviewLink, status: str) -> str | None:
    """Returns the previous status when it changed."""
    previous = link.status
    if previous == LinkStatus.CLOSED.value:
        raise Conflict("This link is closed; closing is final.", code="link_closed")
    if status == previous:
        return None
    if status not in (s.value for s in LinkStatus):
        raise Conflict("That status change is not possible.", code="invalid_transition")
    link.status = status
    if status == LinkStatus.CLOSED.value:
        link.closed_at = _now()
    await db.flush()
    return previous


async def regenerate_token(db: AsyncSession, link: InterviewLink) -> None:
    if link.status == LinkStatus.CLOSED.value:
        raise Conflict("This link is closed.", code="link_closed")
    link.public_token = generate_public_token()
    link.token_rotated_at = _now()
    await db.flush()


async def set_access_code(db: AsyncSession, link: InterviewLink, enabled: bool) -> str | None:
    code = generate_access_code() if enabled else None
    link.access_code_hash = await hash_access_code_async(code) if code else None
    await db.flush()
    return code


# ======================================================== serialisation


def _link_rounds(link: InterviewLink, rows: dict[uuid.UUID, Round]) -> list[LinkRound]:
    out: list[LinkRound] = []
    for index, round_id in enumerate(link.round_ids or []):
        row = rows.get(round_id)
        missing = row is None or not row.is_active or row.job_id != link.job_id
        out.append(
            LinkRound(
                id=round_id,
                name=row.name if row is not None else "Unavailable round",
                round_type=row.round_type if row is not None else "unknown",
                sequence=index,
                duration_minutes=round_minutes(row) if row is not None else 0,
                missing=missing,
            )
        )
    return out


async def serialize_links(
    db: AsyncSession, links: Sequence[InterviewLink], *, detail: bool = False
) -> list[OpenLinkSummary] | list[OpenLink]:
    if not links:
        return []
    jobs = {
        j.id: j
        for j in (await db.scalars(select(Job).where(Job.id.in_({lnk.job_id for lnk in links})))).all()
    }
    round_ids = {rid for lnk in links for rid in (lnk.round_ids or [])}
    rounds = (
        {r.id: r for r in (await db.scalars(select(Round).where(Round.id.in_(round_ids)))).all()}
        if round_ids
        else {}
    )
    counts = await counts_for(db, [lnk.id for lnk in links])
    users: dict[uuid.UUID, str] = {}
    if detail:
        creator_ids = {lnk.created_by_id for lnk in links if lnk.created_by_id}
        if creator_ids:
            users = {
                row.id: row.full_name
                for row in (
                    await db.execute(select(User.id, User.full_name).where(User.id.in_(creator_ids)))
                ).all()
            }
    now = _now()
    out: list[Any] = []
    for link in links:
        job = jobs.get(link.job_id)
        link_rounds = _link_rounds(link, rounds)
        link_counts = counts[link.id]
        base = {
            "id": link.id,
            "name": link.name,
            "job": JobBrief(
                id=link.job_id, title=job.title if job else "", status=job.status if job else "archived"
            ),
            "status": link.status,
            "state": compute_state(
                link, job.status if job else JobStatus.ARCHIVED.value, link_counts.registrations, now
            ),
            "url": link_url(link.public_token),
            "rounds": link_rounds,
            "progression": link.progression,
            "opens_at": link.opens_at,
            "closes_at": link.closes_at,
            "max_registrations": link.max_registrations,
            "counts": link_counts,
            "created_at": link.created_at,
            "updated_at": link.updated_at,
        }
        if not detail:
            out.append(OpenLinkSummary(**base))
            continue
        snapshot = await effective_policy(db, link.organization_id, job)
        summary = policy_summary(snapshot)
        warnings: list[str] = []
        if any(r.missing for r in link_rounds):
            warnings.append("round_missing")
        if not summary.video_recording:
            warnings.append("video_recording_off")
        if job is not None and job.status in CLOSED_JOB_STATUSES:
            warnings.append("job_not_open")
        out.append(
            OpenLink(
                **base,
                description=link.description,
                round_window_hours=link.round_window_hours,
                require_resume=link.require_resume,
                requires_access_code=link.access_code_hash is not None,
                per_ip_hourly_limit=link.per_ip_hourly_limit,
                duplicate_policy=DuplicatePolicy(
                    **{**DEFAULT_DUPLICATE_POLICY, **(link.duplicate_policy or {})}
                ),
                form_schema=FormSchemaOut.model_validate(link.form_schema),
                acknowledged_warnings=list(link.acknowledged_warnings or []),
                policy_summary=summary,
                warnings=warnings,
                created_by=IdName(id=link.created_by_id, full_name=users[link.created_by_id])
                if link.created_by_id in users
                else None,
            )
        )
    return out


async def serialize_link(db: AsyncSession, link: InterviewLink) -> OpenLink:
    await db.refresh(link)
    [out] = await serialize_links(db, [link], detail=True)
    assert isinstance(out, OpenLink)
    return out


# =============================================================== public


async def data_use(db: AsyncSession, link: InterviewLink, job: Job | None) -> DataUse:
    org_settings = await get_organization_settings(db, link.organization_id)
    snapshot = build_policy_snapshot(org_settings, job)
    return DataUse(
        **policy_summary(snapshot).model_dump(),
        notice_markdown=org_settings.data_processing_notice,
    )


async def public_info(db: AsyncSession, link: InterviewLink) -> LinkInfo:
    job = await db.get(Job, link.job_id)
    org = await db.get(Organization, link.organization_id)
    state = await link_state(db, link, job)
    rounds = [p for p in await pipeline(db, link) if p.round is not None]
    use = await data_use(db, link, job)
    org_settings = await get_organization_settings(db, link.organization_id)
    form = None
    if state == LinkState.OPEN.value:
        form = PublicForm(
            fields=[PublicFormField.model_validate(f) for f in enabled_fields(link.form_schema)]
        )
    return LinkInfo(
        organization_name=org.name if org else "",
        job_title=job.title if job else "",
        job_location=job.location if job else None,
        link_name=link.name,
        description=link.description,
        state=state,  # type: ignore[arg-type]
        opens_at=link.opens_at,
        closes_at=link.closes_at,
        rounds=[
            PublicRound(
                name=p.round.name,
                round_type=p.round.round_type,
                duration_minutes=round_minutes(p.round),
                sequence=p.sequence,
            )
            for p in rounds
            if p.round is not None
        ],
        progression=link.progression,  # type: ignore[arg-type]
        requires_access_code=link.access_code_hash is not None,
        require_resume=link.require_resume,
        interviewer_name=org_settings.avatar.interviewer_name,
        form=form,
        data_use=use,
    )
