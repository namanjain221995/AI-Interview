"""Interview rounds of a job.

A round is created from a template (organization or global) or from the
round type's default configuration; the request's partial ``config`` is
deep-merged on top, explicit fields (name, durations, difficulty,
selectability, instructions) win, and the result must validate as
:class:`~app.schemas.rounds.RoundConfig` with known competency keys and
coding questions. The table columns are always re-derived from the config.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Interview, InterviewSession, Job, Round
from app.domain.enums import InterviewStatus, RoundType
from app.schemas.api.catalog import RoundCreate, RoundUpdate
from app.services.jobs.catalog import (
    deep_merge,
    get_template,
    overlay_columns,
    parse_round_config,
    parse_round_type,
    round_type_default,
    round_type_label,
    sync_round_columns,
    validate_config_references,
)

OPEN_INTERVIEW_STATUSES = (InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value)


async def list_rounds(db: AsyncSession, job_id: uuid.UUID, *, include_inactive: bool = True) -> list[Round]:
    stmt = select(Round).where(Round.job_id == job_id)
    if not include_inactive:
        stmt = stmt.where(Round.is_active.is_(True))
    return list((await db.scalars(stmt.order_by(Round.sequence, Round.created_at))).all())


async def get_round(db: AsyncSession, round_id: uuid.UUID, organization_id: uuid.UUID) -> tuple[Round, Job]:
    row = await db.get(Round, round_id)
    if row is None:
        raise NotFound("Round not found.")
    job = await db.get(Job, row.job_id)
    if job is None or job.organization_id != organization_id:
        raise NotFound("Round not found.")
    return row, job


async def _ensure_intake_entitled(db: AsyncSession, organization_id: uuid.UUID) -> None:
    from app.services.intake.access import intake_entitled

    if not await intake_entitled(db, organization_id):
        raise NotFound("Intake interviews are not part of this workspace.", code="intake_disabled")


async def create_round(db: AsyncSession, job: Job, body: RoundCreate) -> Round:
    template_id: uuid.UUID | None = None
    if body.template_id is not None:
        template = await get_template(db, body.template_id, job.organization_id)
        round_type = body.round_type or parse_round_type(template.round_type)
        base = dict(template.config)
        template_id = template.id
    elif body.round_type is not None:
        round_type = body.round_type
        base, _, _ = await round_type_default(db, round_type)
    else:
        raise ValidationFailed("round_type or template_id is required.", details={"field": "round_type"})
    if round_type is RoundType.INTAKE:
        await _ensure_intake_entitled(db, job.organization_id)
    data = deep_merge(base, body.config or {})
    data["round_type"] = round_type.value
    values = body.model_dump(exclude={"config", "template_id", "is_active"})
    data = overlay_columns(data, values)
    if not data.get("name"):
        data["name"] = round_type_label(round_type)
    config = parse_round_config(data)
    await validate_config_references(db, config, job.organization_id)
    sequence = (
        int(
            await db.scalar(select(func.coalesce(func.max(Round.sequence), 0)).where(Round.job_id == job.id))
            or 0
        )
        + 1
    )
    row = Round(
        job_id=job.id,
        template_id=template_id,
        sequence=sequence,
        is_active=body.is_active,
        name=config.name,
        round_type=round_type.value,
    )
    sync_round_columns(row, config)
    db.add(row)
    await db.flush()
    return row


async def update_round(db: AsyncSession, row: Round, job: Job, body: RoundUpdate) -> dict[str, Any]:
    fields = set(body.model_fields_set) - {"replace_config"}
    before = dict(row.config or {})
    if "config" in fields and body.config is not None:
        data = dict(body.config) if body.replace_config else deep_merge(before, body.config)
    else:
        data = dict(before)
    values = {name: getattr(body, name) for name in fields if name not in ("config", "is_active")}
    data = overlay_columns(data, values)
    if body.replace_config and "round_type" not in data:
        data["round_type"] = row.round_type
    if not data.get("name"):
        data["name"] = row.name
    config = parse_round_config(data)
    if config.round_type is RoundType.INTAKE and row.round_type != RoundType.INTAKE.value:
        await _ensure_intake_entitled(db, job.organization_id)  # G8: same rule as creating one
    await validate_config_references(db, config, job.organization_id)
    sync_round_columns(row, config)
    if "is_active" in fields and body.is_active is not None:
        row.is_active = body.is_active
    await db.flush()
    after = row.config
    return {
        "fields": sorted(fields),
        "config_keys_changed": sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)),
    }


async def delete_round(db: AsyncSession, row: Round) -> None:
    sessions = await db.scalar(
        select(func.count()).select_from(InterviewSession).where(InterviewSession.round_id == row.id)
    )
    open_interviews = await db.scalar(
        select(func.count())
        .select_from(Interview)
        .where(Interview.round_id == row.id, Interview.status.in_(OPEN_INTERVIEW_STATUSES))
    )
    if sessions or open_interviews:
        raise Conflict(
            "This round has interviews or sessions. Deactivate it instead (is_active=false).",
            code="round_in_use",
            details={"sessions": int(sessions or 0), "open_interviews": int(open_interviews or 0)},
        )
    job_id = row.job_id
    await db.delete(row)
    await db.flush()
    await _renumber(db, job_id)


async def _renumber(db: AsyncSession, job_id: uuid.UUID) -> None:
    for index, item in enumerate(await list_rounds(db, job_id), start=1):
        if item.sequence != index:
            item.sequence = index
    await db.flush()


async def reorder_rounds(db: AsyncSession, job: Job, round_ids: list[uuid.UUID]) -> list[Round]:
    rounds = await list_rounds(db, job.id)
    existing = {r.id: r for r in rounds}
    if len(set(round_ids)) != len(round_ids) or set(round_ids) != set(existing):
        raise ValidationFailed(
            "round_ids must list every round of the job exactly once.",
            details={"expected": sorted(str(i) for i in existing), "received": [str(i) for i in round_ids]},
        )
    for index, round_id in enumerate(round_ids, start=1):
        existing[round_id].sequence = index
    await db.flush()
    return [existing[i] for i in round_ids]
