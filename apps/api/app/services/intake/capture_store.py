"""Persistence of the live intake checkpoints: ``intake_stage_results`` and ``intake_activity_results``
(design contract 3.3, 6.9, 7.6, 7.7). T1 is the only writer of both tables.

Column ownership inside T1 (so a final pass that finishes before the conductor commits a capture is never
overwritten):

* the conductor writes the activity row's ``status``, ``phases``, ``reveal``, ``capture`` (all keys except
  ``windows``), ``attempts_log``, ``answer_id``, ``ended_reason``, ``transcript_live``, ``audio_media_id`` and
  resets ``transcript_*`` when a new attempt starts (``reset_attempt``);
* the final-pass helpers here alone write ``transcript_final`` / ``transcript_status`` / ``transcript_source``
  and ``capture.windows``, always as conditional updates (``attempt`` matches and the status is still
  ``pending`` / ``pending_worker``).

Never logs transcript text, passage text or onboarding values (ids and counts only).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import IntakeActivityResult, IntakeStageResult
from app.db.session import session_scope
from app.services.intake.state import IntakeStageState

log = get_logger(__name__)

PENDING_STATUSES = ("pending", "pending_worker")
SETTLED_STATUSES = ("final", "live_only", "unavailable")


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _clean_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``StageItemResult`` records with uuid strings only (JSONB)."""
    out = []
    for item in items:
        record = dict(item)
        record["question_ids"] = [str(q) for q in record.get("question_ids") or []]
        record["answer_ids"] = [str(a) for a in record.get("answer_ids") or []]
        out.append(record)
    return out


# ===================================================================================== stage results


async def upsert_stage_rows(
    db: AsyncSession,
    *,
    organization_id: Any,
    intake_id: Any,
    session_id: Any,
    stages: Sequence[IntakeStageState],
    keys: Iterable[str] | None = None,
    extra: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Insert or update the stage rows (``keys`` = only these stages). ``extra`` is merged into the stored
    ``extra`` (top-level keys replaced)."""
    wanted = set(keys) if keys is not None else None
    rows = []
    for stage in stages:
        if wanted is not None and stage.key not in wanted:
            continue
        rows.append(
            {
                "organization_id": _uuid(organization_id),
                "intake_id": _uuid(intake_id),
                "session_id": _uuid(session_id),
                "stage_key": stage.key,
                "stage_number": stage.number,
                "status": stage.status,
                "started_elapsed_s": stage.started_s,
                "ended_elapsed_s": stage.ended_s,
                "budget_s": stage.budget_s,
                "items": _clean_items(item.record() for item in stage.items),
                "extra": dict((extra or {}).get(stage.key) or {}),
            }
        )
    if not rows:
        return
    stmt = pg_insert(IntakeStageResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_intake_stage_results_session_stage",
        set_={
            "status": stmt.excluded.status,
            "started_elapsed_s": stmt.excluded.started_elapsed_s,
            "ended_elapsed_s": stmt.excluded.ended_elapsed_s,
            "budget_s": stmt.excluded.budget_s,
            "items": stmt.excluded["items"],
            "extra": IntakeStageResult.extra.op("||")(stmt.excluded.extra),
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)


# ================================================================================== activity rows


async def ensure_activity_row(
    db: AsyncSession,
    *,
    organization_id: Any,
    intake_id: Any,
    session_id: Any,
    kind: str,
    stage_key: str,
    material_key: str,
    kit_version: str,
    question_id: Any | None,
    phases: list[dict[str, Any]],
) -> uuid.UUID:
    """The activity row of (session, kind), created ``armed`` (its id is the protocol ``activity_id``)."""
    existing = await db.scalar(
        select(IntakeActivityResult).where(
            IntakeActivityResult.session_id == _uuid(session_id), IntakeActivityResult.kind == kind
        )
    )
    if existing is not None:
        existing.status = "armed"
        existing.material_key = material_key
        existing.question_id = _uuid(question_id) if question_id else existing.question_id
        if not existing.phases:
            existing.phases = phases
        await db.flush()
        return existing.id
    row = IntakeActivityResult(
        id=uuid.uuid4(),
        organization_id=_uuid(organization_id),
        intake_id=_uuid(intake_id),
        session_id=_uuid(session_id),
        kind=kind,
        stage_key=stage_key,
        status="armed",
        attempt=1,
        material_key=material_key,
        kit_version=kit_version,
        question_id=_uuid(question_id) if question_id else None,
        phases=phases,
        reveal={"shown_ms": 0, "windows": [], "interrupted": False} if kind == "read_relay" else {},
        capture={},
        attempts_log=[],
        transcript_status="pending",
    )
    db.add(row)
    await db.flush()
    return row.id


async def update_activity(db: AsyncSession, activity_id: Any, **values: Any) -> None:
    """Conductor-owned columns (status, phases, reveal, attempts_log, answer_id, ended_reason, ...)."""
    if not values:
        return
    values.setdefault("updated_at", func.now())
    await db.execute(
        update(IntakeActivityResult).where(IntakeActivityResult.id == _uuid(activity_id)).values(**values)
    )


async def load_activity(db: AsyncSession, activity_id: Any) -> IntakeActivityResult | None:
    return await db.get(IntakeActivityResult, _uuid(activity_id))


async def reset_attempt(db: AsyncSession, activity_id: Any, *, attempt: int) -> None:
    """A new capture attempt starts: clear the previous attempt's transcript columns and capture."""
    await db.execute(
        update(IntakeActivityResult)
        .where(IntakeActivityResult.id == _uuid(activity_id))
        .values(
            attempt=attempt,
            status="running",
            transcript_status="pending",
            transcript_final=None,
            transcript_source=None,
            transcript_live=None,
            capture={},
            answer_id=None,
            ended_reason=None,
            updated_at=func.now(),
        )
    )


async def write_capture(
    db: AsyncSession,
    activity_id: Any,
    *,
    capture: Mapping[str, Any],
    status: str,
    ended_reason: str,
    answer_id: Any | None,
    transcript_live: str | None,
    audio_media_id: Any | None,
    phases: list[dict[str, Any]] | None = None,
) -> None:
    """Store the committed capture (keeps ``capture.windows`` a final pass may already have written)."""
    record = {k: v for k, v in capture.items() if k != "windows"}
    params: dict[str, Any] = {
        "id": _uuid(activity_id),
        "capture": json.dumps(record),
        "status": status,
        "ended_reason": ended_reason,
        "answer_id": _uuid(answer_id) if answer_id else None,
        "live": transcript_live,
        "media": _uuid(audio_media_id) if audio_media_id else None,
    }
    params["phases"] = json.dumps(phases) if phases is not None else None
    await db.execute(
        text(
            "UPDATE intake_activity_results SET "
            "capture = CAST(:capture AS jsonb) || CASE WHEN capture -> 'windows' IS NOT NULL "
            "THEN jsonb_build_object('windows', capture -> 'windows') ELSE '{}'::jsonb END, "
            "status = :status, ended_reason = :ended_reason, answer_id = :answer_id, transcript_live = :live, "
            "audio_media_id = COALESCE(:media, audio_media_id), "
            "phases = COALESCE(CAST(:phases AS jsonb), phases), updated_at = now() WHERE id = :id"
        ),
        params,
    )


async def append_attempt_log(db: AsyncSession, activity_id: Any, entry: Mapping[str, Any]) -> None:
    await db.execute(
        text(
            "UPDATE intake_activity_results SET attempts_log = attempts_log || CAST(:entry AS jsonb), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": _uuid(activity_id), "entry": json.dumps([dict(entry)])},
    )


# ========================================================================= transcript (final pass)


async def store_final_transcript(
    session_id: Any,
    activity_id: Any,
    *,
    attempt: int,
    text_value: str,
    windows: list[dict[str, Any]],
    source: str,
    db: AsyncSession | None = None,
) -> bool:
    """``transcript_final`` + status ``final`` + ``capture.windows``, only while the attempt matches and the status
    is ``pending``/``pending_worker`` (never overwrites a settled transcript). True when stored."""

    async def _run(session: AsyncSession) -> bool:
        result = await session.execute(
            text(
                "UPDATE intake_activity_results SET transcript_final = :final, transcript_status = 'final', "
                "transcript_source = :source, capture = jsonb_set(capture, '{windows}', CAST(:windows AS jsonb), "
                "true), updated_at = now() WHERE id = :id AND session_id = :sid AND attempt = :attempt "
                "AND transcript_status IN ('pending', 'pending_worker') RETURNING id"
            ),
            {
                "final": text_value,
                "source": source,
                "windows": json.dumps(windows),
                "id": _uuid(activity_id),
                "sid": _uuid(session_id),
                "attempt": int(attempt),
            },
        )
        return result.first() is not None

    if db is not None:
        return await _run(db)
    async with session_scope() as session:
        return await _run(session)


async def mark_transcript_status(
    session_id: Any,
    activity_id: Any,
    *,
    attempt: int,
    status: str,
    source: str | None = None,
    from_statuses: Sequence[str] = PENDING_STATUSES,
    db: AsyncSession | None = None,
) -> bool:
    """Conditional status change (``pending`` -> ``pending_worker`` / ``live_only`` / ``unavailable``)."""

    async def _run(session: AsyncSession) -> bool:
        values: dict[str, Any] = {"transcript_status": status, "updated_at": func.now()}
        if source is not None:
            values["transcript_source"] = source
        result = await session.execute(
            update(IntakeActivityResult)
            .where(
                IntakeActivityResult.id == _uuid(activity_id),
                IntakeActivityResult.session_id == _uuid(session_id),
                IntakeActivityResult.attempt == int(attempt),
                IntakeActivityResult.transcript_status.in_(list(from_statuses)),
            )
            .values(**values)
            .returning(IntakeActivityResult.id)
        )
        return result.first() is not None

    if db is not None:
        return await _run(db)
    async with session_scope() as session:
        return await _run(session)


async def store_typed(
    db: AsyncSession, session_id: Any, activity_id: Any, *, attempt: int, text_value: str
) -> bool:
    """A typed relay / topic answer is final at once (``transcript_source = typed``)."""
    return await store_final_transcript(
        session_id, activity_id, attempt=attempt, text_value=text_value, windows=[], source="typed", db=db
    )


async def settled(session_id: Any) -> bool:
    """Every activity transcript of the session is settled (``final``/``live_only``/``unavailable``)."""
    async with session_scope() as db:
        statuses = (
            await db.scalars(
                select(IntakeActivityResult.transcript_status).where(
                    IntakeActivityResult.session_id == _uuid(session_id),
                    IntakeActivityResult.answer_id.is_not(None),
                )
            )
        ).all()
    return all(status in SETTLED_STATUSES for status in statuses)


__all__ = [
    "PENDING_STATUSES",
    "SETTLED_STATUSES",
    "append_attempt_log",
    "ensure_activity_row",
    "load_activity",
    "mark_transcript_status",
    "reset_attempt",
    "settled",
    "store_final_transcript",
    "store_typed",
    "update_activity",
    "upsert_stage_rows",
    "write_capture",
]
