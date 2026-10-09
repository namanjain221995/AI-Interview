"""Building blocks shared by every permanent delete (candidate erasure, session,
intake interview): the "still being worked on" rule, stored-file removal, the
model-call ledger scrub and the Redis clean-up after commit.

Files are removed inside the request transaction, after every reversible step
and before the database rows go: a file that cannot be removed raises
``ServiceUnavailable`` (rolling the transaction back), and file deletion is
idempotent, so the request can simply be retried. ``media_assets`` rows stay as
an anonymous deletion record (``erased/{id}`` key, no file name, hash or details).

Logging hygiene: ids and counts only.
"""

from __future__ import annotations

import functools
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceUnavailable
from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.core.queue import publish_session_event
from app.db.models import Answer, AnswerEvaluation, InterviewSession, MediaAsset, ModelCall, Report
from app.db.models.intake import IntakeEvaluation, IntakeEvaluatorRun
from app.domain.enums import SessionStatus
from app.services.storage import get_storage
from app.services.storage.media import delete_media

log = get_logger(__name__)

# A session in one of these states is being worked on by the realtime runtime
# or a background job, unless nothing touched it for ACTIVE_WINDOW.
RUNNING_SESSION_STATUSES = frozenset(
    {
        SessionStatus.PREPARING.value,
        SessionStatus.LIVE.value,
        SessionStatus.PAUSED.value,
        SessionStatus.COMPLETING.value,
    }
)
# Waiting sessions only count as active while a client is connected
# (the runtime writes ``last_heartbeat_at`` every 30 s).
WAITING_SESSION_STATUSES = frozenset({SessionStatus.CREATED.value, SessionStatus.READY.value})
TERMINAL_SESSION_STATUSES = frozenset(
    {SessionStatus.COMPLETED.value, SessionStatus.ABORTED.value, SessionStatus.FAILED.value}
)
ACTIVE_WINDOW = timedelta(minutes=15)
CONNECTED_WINDOW = timedelta(minutes=2)
ERASED_PREFIX = "erased/"
_REDIS_SCAN_COUNT = 500


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def session_is_active(
    status: str, updated_at: datetime | None, heartbeat_at: datetime | None, *, now: datetime
) -> bool:
    """True while the runtime or a job may still write to the session (deleting it now is refused)."""
    if status in RUNNING_SESSION_STATUSES:
        last = _latest(updated_at, heartbeat_at)
        return last is not None and now - last < ACTIVE_WINDOW
    if status in WAITING_SESSION_STATUSES:
        return heartbeat_at is not None and now - heartbeat_at < CONNECTED_WINDOW
    return False


async def session_blockers(
    db: AsyncSession, condition: ColumnElement[bool], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Sessions matching ``condition`` that something is still working on."""
    now = now or datetime.now(UTC)
    rows = (
        await db.execute(
            select(
                InterviewSession.id,
                InterviewSession.status,
                InterviewSession.updated_at,
                InterviewSession.last_heartbeat_at,
            ).where(condition, InterviewSession.status.not_in(TERMINAL_SESSION_STATUSES))
        )
    ).all()
    return [
        {"type": "session", "id": str(sid), "status": status}
        for sid, status, updated_at, heartbeat_at in rows
        if session_is_active(status, updated_at, heartbeat_at, now=now)
    ]


def ids_of(values: Iterable[Any]) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for value in values:
        if value is None:
            continue
        try:
            out.add(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


# ------------------------------------------------------------------ ledger


async def session_model_call_ids(db: AsyncSession, session_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """Ledger rows referenced by what the sessions produced (reports, evaluations, intake evaluators)."""
    ids: set[uuid.UUID] = set()
    if not session_ids:
        return ids
    for (call_ids,) in (
        await db.execute(select(Report.model_call_ids).where(Report.session_id.in_(session_ids)))
    ).all():
        ids |= ids_of(call_ids or [])
    for (call_ids,) in (
        await db.execute(
            select(IntakeEvaluation.model_call_ids).where(IntakeEvaluation.session_id.in_(session_ids))
        )
    ).all():
        ids |= ids_of(call_ids or [])
    ids |= ids_of(
        (
            await db.scalars(
                select(AnswerEvaluation.model_call_id).where(
                    AnswerEvaluation.session_id.in_(session_ids),
                    AnswerEvaluation.model_call_id.is_not(None),
                )
            )
        ).all()
    )
    ids |= ids_of(
        (
            await db.scalars(
                select(IntakeEvaluatorRun.model_call_id).where(
                    IntakeEvaluatorRun.session_id.in_(session_ids),
                    IntakeEvaluatorRun.model_call_id.is_not(None),
                )
            )
        ).all()
    )
    return ids


async def scrub_model_calls(
    db: AsyncSession, call_ids: set[uuid.UUID], session_ids: Sequence[uuid.UUID]
) -> int:
    """Keep the ledger metrics (latency, tokens, status), drop stored payloads and error text."""
    conditions: list[ColumnElement[bool]] = []
    if call_ids:
        conditions.append(ModelCall.id.in_(call_ids))
    if session_ids:
        conditions.append(ModelCall.session_id.in_(session_ids))
    if not conditions:
        return 0
    result = await db.execute(
        update(ModelCall)
        .where(or_(*conditions))
        .values(request_payload=None, response_payload=None, error=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ------------------------------------------------------------------- files


@dataclass(slots=True)
class FilePurge:
    """Counts of one file purge (never file names or keys)."""

    files_deleted: int = 0
    bytes_deleted: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    records_anonymized: int = 0
    unregistered_deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def session_file_keys(db: AsyncSession, session_ids: Sequence[uuid.UUID]) -> set[str]:
    """Storage keys referenced by session rows (answer audio, report artifacts), registered or not."""
    if not session_ids:
        return set()
    keys = {
        k
        for k in (
            await db.scalars(
                select(Answer.audio_storage_key).where(
                    Answer.session_id.in_(session_ids), Answer.audio_storage_key.is_not(None)
                )
            )
        ).all()
        if k
    }
    keys |= {
        k
        for k in (
            await db.scalars(
                select(Report.artifact_storage_key).where(
                    Report.session_id.in_(session_ids), Report.artifact_storage_key.is_not(None)
                )
            )
        ).all()
        if k
    }
    return keys


async def purge_files(
    db: AsyncSession,
    *,
    media_conditions: Sequence[ColumnElement[bool]],
    extra_keys: Iterable[str] = (),
    reason: str,
    error_code: str = "deletion_incomplete",
) -> FilePurge:
    """Delete every file of the ``media_assets`` rows matching any condition (rows are
    anonymized and kept) plus ``extra_keys`` that were never registered.

    Raises ``ServiceUnavailable(error_code)`` when a file cannot be removed; the
    caller's transaction then rolls back and nothing is deleted from the database.
    """
    out = FilePurge()
    assets: list[MediaAsset] = []
    if media_conditions:
        assets = list(
            (
                await db.scalars(
                    select(MediaAsset).where(or_(*media_conditions)).order_by(MediaAsset.created_at)
                )
            ).all()
        )
    registered = {a.storage_key for a in assets}
    storage = get_storage()
    kinds: Counter[str] = Counter()
    failures = 0
    for asset in assets:
        if asset.storage_key.startswith(ERASED_PREFIX):
            continue  # anonymized by an earlier deletion
        if asset.deleted_at is None:
            size = int(asset.size_bytes or 0)
            try:
                deleted = await delete_media(db, asset, reason=reason)
            except OSError as exc:
                failures += 1
                log.error("deletion.file_failed", media_id=str(asset.id), error=type(exc).__name__)
                continue
            except ValueError:  # InvalidStorageKey: no file can exist there
                asset.deleted_at = datetime.now(UTC)
                deleted = True
            if deleted:
                out.files_deleted += 1
                out.bytes_deleted += size
                kinds[asset.kind] += 1
        else:
            # Soft-deleted earlier (e.g. retention): make sure no bytes survived.
            try:
                await storage.delete(asset.storage_key)
            except ValueError:
                pass
            except OSError as exc:
                failures += 1
                log.error("deletion.file_failed", media_id=str(asset.id), error=type(exc).__name__)
                continue
        # Keep the row as an anonymous deletion record (no file name, hash or details).
        asset.storage_key = f"{ERASED_PREFIX}{asset.id}"
        asset.sha256 = None
        asset.details = {"deleted_reason": reason, "erased": True}
        out.records_anonymized += 1
    # Files referenced by a row but never registered as media (defensive).
    for key in sorted(set(extra_keys) - registered):
        if key.startswith(ERASED_PREFIX):
            continue
        try:
            if await storage.delete(key):
                out.unregistered_deleted += 1
        except (OSError, ValueError) as exc:  # ValueError: InvalidStorageKey
            if isinstance(exc, OSError):
                failures += 1
            log.error("deletion.unregistered_file_failed", error=type(exc).__name__)
    if failures:
        raise ServiceUnavailable(
            "Some stored files could not be deleted. Nothing was deleted from the database; please retry.",
            code=error_code,
            details={"failed_files": failures},
        )
    await db.flush()
    out.by_kind = dict(sorted(kinds.items()))
    return out


# ------------------------------------------------------------------- redis


async def clear_session_redis(session_ids: Sequence[uuid.UUID]) -> None:
    """Remove the sessions' Redis keys (state, outbox, preparation caches); best effort."""
    if not session_ids:
        return
    from app.core.redis import get_redis

    redis = get_redis()
    removed = 0
    try:
        for sid in session_ids:
            keys = [key async for key in redis.scan_iter(match=f"session:{sid}:*", count=_REDIS_SCAN_COUNT)]
            for start in range(0, len(keys), 500):
                removed += int(await redis.delete(*keys[start : start + 500]))
    except (RedisError, OSError) as exc:  # keys expire on their own (<= 24 h)
        log.warning("deletion.redis_failed", error=type(exc).__name__, sessions=len(session_ids))
        return
    if removed:
        log.info("deletion.redis_cleared", keys=removed, sessions=len(session_ids))


def after_commit_session_cleanup(
    db: AsyncSession,
    session_ids: Sequence[uuid.UUID],
    open_session_ids: Sequence[uuid.UUID],
    *,
    reason: str,
) -> None:
    """After the commit: drop Redis keys and tell a runtime still attached to a stale session to end it."""
    ids = list(session_ids)

    async def _clear() -> None:
        await clear_session_redis(ids)

    async def _notify(sid: uuid.UUID) -> None:
        await publish_session_event(sid, {"type": "session.cancelled", "reason": reason})

    run_after_commit(db, _clear)
    for sid in open_session_ids:
        run_after_commit(db, functools.partial(_notify, sid))
