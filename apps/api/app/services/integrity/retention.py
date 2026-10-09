"""``retention.sweep``: enforce media retention deadlines.

Every stored file has a ``media_assets`` row with ``retention_until``. The
sweep deletes the files of expired rows from storage and soft-deletes the rows
(``deleted_at`` + ``details.deleted_reason``; the row stays for the audit
trail), purges transient frames that somehow lost their Redis expiry, and
writes a summary to ``system_notes`` when it changed anything.

Rows are claimed with ``FOR UPDATE SKIP LOCKED`` in small batches (one
transaction each), so concurrent sweeps never fight and a crash loses at most
one batch of bookkeeping (the next run picks the rows up again).
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.db.models import MediaAsset, SystemNote
from app.db.session import session_scope
from app.services.integrity.store import IntegrityStore
from app.services.storage import get_storage

log = get_logger(__name__)

SWEEP_BATCH_SIZE = 200
SWEEP_MAX_ITEMS = 10_000
TRANSIENT_FRAME_MAX_TTL_S = 3600


@dataclass(slots=True)
class SweepSummary:
    deleted: int = 0
    files_removed: int = 0
    files_missing: int = 0
    bytes_freed: int = 0
    errors: int = 0
    transient_frames_purged: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0
    capped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def changed(self) -> bool:
        return bool(self.deleted or self.errors or self.transient_frames_purged)


async def sweep_expired_media(
    *,
    now: datetime | None = None,
    batch_size: int = SWEEP_BATCH_SIZE,
    max_items: int = SWEEP_MAX_ITEMS,
    store: IntegrityStore | None = None,
    write_note: bool = True,
) -> SweepSummary:
    started = time.perf_counter()
    cutoff = now or datetime.now(UTC)
    storage = get_storage()
    summary = SweepSummary()
    kinds: Counter[str] = Counter()
    failed: set[uuid.UUID] = set()
    processed = 0
    while processed < max_items:
        async with session_scope() as db:
            stmt = (
                select(MediaAsset)
                .where(
                    MediaAsset.deleted_at.is_(None),
                    MediaAsset.retention_until.is_not(None),
                    MediaAsset.retention_until <= cutoff,
                )
                .order_by(MediaAsset.retention_until)
                .limit(min(batch_size, max_items - processed))
                .with_for_update(skip_locked=True)
            )
            if failed:
                stmt = stmt.where(MediaAsset.id.not_in(failed))
            batch = list((await db.scalars(stmt)).all())
            if not batch:
                break
            for asset in batch:
                processed += 1
                try:
                    removed = await storage.delete(asset.storage_key)
                except Exception as exc:  # keep the row; retried on the next run
                    failed.add(asset.id)
                    summary.errors += 1
                    ERRORS.labels(component="retention").inc()
                    log.warning("retention.delete_failed", media_id=str(asset.id), error=type(exc).__name__)
                    continue
                asset.deleted_at = datetime.now(UTC)
                asset.details = {**(asset.details or {}), "deleted_reason": "retention_expired"}
                summary.deleted += 1
                summary.bytes_freed += int(asset.size_bytes or 0)
                if removed:
                    summary.files_removed += 1
                else:
                    summary.files_missing += 1
                kinds[asset.kind] += 1
    summary.capped = processed >= max_items
    summary.transient_frames_purged = await (store or IntegrityStore()).purge_stale_frames(
        max_ttl_s=TRANSIENT_FRAME_MAX_TTL_S
    )
    summary.by_kind = dict(sorted(kinds.items()))
    summary.duration_ms = round((time.perf_counter() - started) * 1000, 1)
    if write_note and summary.changed:
        async with session_scope() as db:
            db.add(
                SystemNote(
                    kind="retention.sweep",
                    body=(
                        f"Retention sweep removed {summary.deleted} expired media file(s) "
                        f"({summary.bytes_freed} bytes; {summary.files_missing} already missing), "
                        f"purged {summary.transient_frames_purged} transient frame(s), {summary.errors} error(s)."
                    ),
                    details={**summary.to_dict(), "cutoff": cutoff.isoformat()},
                )
            )
    log.info("retention.sweep_done", **{k: v for k, v in summary.to_dict().items() if k != "by_kind"})
    return summary
