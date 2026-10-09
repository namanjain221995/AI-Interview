"""Media registry: every stored file gets a ``media_assets`` row.

``store_media`` writes the bytes through the storage backend and adds the row
(sha256, size, retention deadline) to the caller's transaction. If that
transaction is rolled back, the written files are removed again so no
orphaned bytes outlive their metadata.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from collections.abc import AsyncIterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.db.models import MediaAsset
from app.domain.enums import MediaKind
from app.services.storage import get_storage
from app.services.storage.base import ObjectNotFound, validate_key

log = get_logger(__name__)

_PENDING_KEY = "storage.pending_files"
_HOOKED_KEY = "storage.cleanup_hooked"
_background: set[asyncio.Task[Any]] = set()


def default_retention_days(kind: MediaKind | str) -> int:
    settings = get_settings()
    kind = MediaKind(kind)
    if kind in (MediaKind.CAMERA_FRAME, MediaKind.SCREEN_FRAME):
        return settings.frame_retention_days
    if kind in (MediaKind.ANSWER_AUDIO, MediaKind.SESSION_AUDIO):
        return settings.audio_retention_days
    if kind in (MediaKind.VIDEO_SEGMENT, MediaKind.SCREEN_SEGMENT):
        return settings.video_retention_days
    return settings.retention_days_default


def retention_deadline(kind: MediaKind | str, retention_days: int | None = None, *, now: datetime | None = None) -> datetime:
    days = retention_days if retention_days is not None else default_retention_days(kind)
    if days <= 0:
        raise ValueError("retention_days must be positive")
    return (now or datetime.now(UTC)) + timedelta(days=days)


async def _file_digest(key: str) -> str | None:
    digest = hashlib.sha256()
    try:
        async for chunk in get_storage().open(key):
            digest.update(chunk)
    except ObjectNotFound:
        return None
    return digest.hexdigest()


def _on_rollback(session: Session) -> None:
    written: list[tuple[str, str]] = session.info.pop(_PENDING_KEY, [])
    if not written:
        return
    storage = get_storage()

    async def _cleanup() -> None:
        for key, sha256 in written:
            try:
                # Only remove the bytes this transaction wrote: a concurrent
                # writer may own the key by now.
                if await _file_digest(key) == sha256:
                    await storage.delete(key)
                    log.info("media.rollback_cleanup", key_count=1)
            except Exception as exc:
                log.warning("media.rollback_cleanup_failed", error=type(exc).__name__)

    try:
        task = asyncio.get_running_loop().create_task(_cleanup())
    except RuntimeError:  # no running loop (sync context): nothing safe to do here
        log.warning("media.rollback_cleanup_skipped", files=len(written))
        return
    _background.add(task)
    task.add_done_callback(_background.discard)


def _on_commit(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def _track_written_file(db: AsyncSession, key: str, sha256: str) -> None:
    sync_session = db.sync_session
    if not sync_session.info.get(_HOOKED_KEY):
        event.listen(sync_session, "after_rollback", _on_rollback)
        event.listen(sync_session, "after_commit", _on_commit)
        sync_session.info[_HOOKED_KEY] = True
    sync_session.info.setdefault(_PENDING_KEY, []).append((key, sha256))


async def wait_for_cleanup() -> None:
    """Await pending rollback cleanups (tests and graceful shutdown)."""
    if _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


async def store_media(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    kind: MediaKind | str,
    key: str,
    data: bytes | bytearray | memoryview | AsyncIterable[bytes],
    content_type: str,
    session_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    retention_days: int | None = None,
    details: Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
) -> MediaAsset:
    """Store bytes at ``key`` and register them; returns the flushed ``MediaAsset``.

    The row is inserted first (inside a savepoint) so the unique ``storage_key``
    is reserved before any byte touches the filesystem; a failed write rolls
    back only that savepoint, leaving the caller's transaction usable.
    """
    media_kind = MediaKind(kind)
    validate_key(key)
    existing = await db.scalar(select(MediaAsset).where(MediaAsset.storage_key == key))
    if existing is not None and existing.deleted_at is None:
        raise Conflict("A media object is already stored at this key.", details={"media_id": str(existing.id)})
    retention_until = retention_deadline(media_kind, retention_days)
    storage = get_storage()
    async with db.begin_nested():
        if existing is not None:  # key previously deleted: reuse its row (storage_key is unique)
            asset = existing
            asset.organization_id = organization_id
            asset.session_id = session_id
            asset.candidate_id = candidate_id
            asset.kind = media_kind.value
            asset.content_type = content_type
            asset.size_bytes = 0
            asset.sha256 = None
            asset.retention_until = retention_until
            asset.deleted_at = None
            asset.details = dict(details or {})
        else:
            asset = MediaAsset(
                organization_id=organization_id,
                session_id=session_id,
                candidate_id=candidate_id,
                kind=media_kind.value,
                storage_key=key,
                content_type=content_type,
                size_bytes=0,
                retention_until=retention_until,
                details=dict(details or {}),
            )
            db.add(asset)
        await db.flush()  # reserves the key; a concurrent writer fails here, before writing
        if isinstance(data, bytes | bytearray | memoryview):
            stored = await storage.put(key, bytes(data), content_type)
        else:
            stored = await storage.put_stream(key, data, content_type, max_bytes=max_bytes)
        try:
            asset.size_bytes = stored.size
            asset.sha256 = stored.sha256
            await db.flush()
        except BaseException:
            with contextlib.suppress(Exception):
                await storage.delete(key)  # our row still holds the key: these are our bytes
            raise
    _track_written_file(db, key, stored.sha256)
    log.info("media.stored", media_id=str(asset.id), kind=media_kind.value, size_bytes=stored.size)
    return asset


async def get_media(db: AsyncSession, media_id: uuid.UUID, *, include_deleted: bool = False) -> MediaAsset:
    asset = await db.get(MediaAsset, media_id)
    if asset is None or (asset.deleted_at is not None and not include_deleted):
        raise NotFound("Media not found.")
    return asset


async def delete_media(db: AsyncSession, media: MediaAsset | uuid.UUID, *, reason: str = "deleted") -> bool:
    """Remove the file and soft-delete the row (kept for the audit trail).

    Returns ``True`` when something was deleted, ``False`` if already gone.
    """
    if isinstance(media, MediaAsset):
        # Works for expired/detached instances too (no lazy load in async code).
        identity = inspect(media).identity
        media_id = identity[0] if identity else media.id
    else:
        media_id = media
    asset = await db.get(MediaAsset, media_id)
    if asset is None or asset.deleted_at is not None:
        return False
    removed = await get_storage().delete(asset.storage_key)
    asset.deleted_at = datetime.now(UTC)
    asset.details = {**(asset.details or {}), "deleted_reason": reason}
    await db.flush()
    log.info("media.deleted", media_id=str(asset.id), file_removed=removed, reason=reason)
    return True
