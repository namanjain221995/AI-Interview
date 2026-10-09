"""``open_link.sweep`` (every 300 s; OPEN_LINKS.md sections 5.3, 8.4).

1. Stale syncs: registrations still ``registered``/``in_progress``/``expired``
   whose current interview changed recently are re-synced (the worker hook and
   lazy reads normally get there first).
2. Signal retention: ``device_hash``/``ip_hash``/``ip_ua_hash`` are nulled
   ``OPEN_LINK_SIGNAL_RETENTION_DAYS`` after registration, or 30 days after the
   link closed, whichever comes first (flags already raised keep their codes).
3. ``blocked`` rows (no candidate) are deleted after
   ``OPEN_LINK_BLOCKED_RETENTION_DAYS``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Interview, InterviewLink, LinkRegistration, LinkRegistrationRound
from app.services.open_links.constants import RegistrationStatus
from app.services.open_links.progression import SYNCABLE, sync_registration

log = get_logger(__name__)

STALE_BATCH = 500
CLOSED_LINK_SIGNAL_DAYS = 30


@dataclass(slots=True)
class SweepSummary:
    synced: int = 0
    changed: int = 0
    signals_purged: int = 0
    blocked_deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def sync_recent(db: AsyncSession, *, now: datetime | None = None, summary: SweepSummary) -> None:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=1)
    ids = (
        await db.scalars(
            select(LinkRegistration.id)
            .join(LinkRegistrationRound, LinkRegistrationRound.registration_id == LinkRegistration.id)
            .join(Interview, Interview.id == LinkRegistrationRound.interview_id)
            .where(
                LinkRegistration.status.in_(SYNCABLE),
                LinkRegistrationRound.sequence == LinkRegistration.current_round_index,
                or_(Interview.updated_at >= since, Interview.invite_expires_at.between(since, now)),
            )
            .distinct()
            .limit(STALE_BATCH)
        )
    ).all()
    for registration_id in ids:
        result = await sync_registration(db, registration_id)
        summary.synced += 1
        summary.changed += int(result.changed)


async def purge_signals(db: AsyncSession, *, now: datetime | None = None, summary: SweepSummary) -> None:
    now = now or datetime.now(UTC)
    settings = get_settings()
    by_age = now - timedelta(days=settings.open_link_signal_retention_days)
    closed_before = now - timedelta(days=CLOSED_LINK_SIGNAL_DAYS)
    closed_links = select(InterviewLink.id).where(
        InterviewLink.closed_at.is_not(None), InterviewLink.closed_at <= closed_before
    )
    result = await db.execute(
        update(LinkRegistration)
        .where(
            LinkRegistration.signals_purged_at.is_(None),
            or_(LinkRegistration.created_at <= by_age, LinkRegistration.link_id.in_(closed_links)),
        )
        .values(device_hash=None, ip_hash=None, ip_ua_hash=None, signals_purged_at=now)
        .execution_options(synchronize_session=False)
    )
    summary.signals_purged += int(result.rowcount or 0)  # type: ignore[attr-defined]


async def delete_old_blocked(db: AsyncSession, *, now: datetime | None = None, summary: SweepSummary) -> None:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=get_settings().open_link_blocked_retention_days)
    result = await db.execute(
        delete(LinkRegistration)
        .where(
            and_(
                LinkRegistration.status == RegistrationStatus.BLOCKED.value,
                LinkRegistration.created_at <= cutoff,
            )
        )
        .execution_options(synchronize_session=False)
    )
    summary.blocked_deleted += int(result.rowcount or 0)  # type: ignore[attr-defined]


async def sweep(db: AsyncSession, *, now: datetime | None = None) -> SweepSummary:
    summary = SweepSummary()
    await sync_recent(db, now=now, summary=summary)
    await purge_signals(db, now=now, summary=summary)
    await delete_old_blocked(db, now=now, summary=summary)
    log.info("open_link.sweep", **summary.as_dict())
    return summary
