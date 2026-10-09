"""Interview recordings for staff (session detail -> Recording tab).

* ``GET  /sessions/{id}/recordings`` parts, status, timing anchors (``sessions:read``).
* ``GET  /sessions/{id}/recordings/{part_id}/url`` short-lived signed playback URL;
  every call is audited as a sensitive read (``recording.view``).
* ``GET  /sessions/{id}/recordings/{part_id}/download`` signed URL served as an
  attachment: ADMIN and SUPER_ADMIN only, audited (``recording.download``).
* ``POST /sessions/{id}/recordings/finalize`` process now (also retries parts
  the background sweep gave up on), audited.

Sessions of another organization look like a 404, as everywhere else.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import DbSession, require_permission, require_role
from app.core.errors import Conflict, NotFound
from app.core.queue import enqueue
from app.core.rbac import Permission, Principal
from app.domain.enums import Role
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.recordings import RecordingFinalizeOut, RecordingUrlOut, SessionRecordingsOut
from app.services.recordings.constants import FINALIZE_JOB
from app.services.recordings.parts import Part, load_parts
from app.services.recordings.sweep import finalize_dedupe_key
from app.services.recordings.views import recordings_view
from app.services.reports.queries import get_session_for_principal
from app.services.storage.signing import signed_url_with_expiry

router = APIRouter(prefix="/sessions", tags=["recordings"], responses=ERROR_RESPONSES)

SessionReader = Annotated[Principal, Depends(require_permission(Permission.SESSIONS_READ))]
RecordingProcessor = Annotated[
    Principal, Depends(require_permission(Permission.SESSIONS_READ, Permission.REVIEWS_WRITE))
]
RecordingDownloader = Annotated[
    Principal, Depends(require_role(Role.ADMIN, Role.SUPER_ADMIN, perms=(Permission.SESSIONS_READ,)))
]
PartId = Annotated[str, Path(pattern=r"^[A-Za-z0-9-]{1,64}$", description="Recording part id")]


async def _playable_part(db: DbSession, session_id: uuid.UUID, part_id: str) -> Part:
    part = next((p for p in await load_parts(db, session_id) if p.key == part_id), None)
    if part is None:
        raise NotFound("This recording does not exist.", code="recording_not_found")
    finalized = part.finalized
    if finalized is None:
        raise Conflict(
            "This part of the recording is still being processed.",
            code="recording_processing",
            details={"pending_segments": len(part.segments)},
        )
    if finalized.retention_until is not None and finalized.retention_until <= datetime.now(UTC):
        raise NotFound("This recording has passed its retention period.", code="recording_expired")
    return part


@router.get(
    "/{session_id}/recordings", response_model=SessionRecordingsOut, summary="List the session's recordings"
)
async def list_recordings(
    session_id: uuid.UUID, principal: SessionReader, db: DbSession
) -> SessionRecordingsOut:
    session = await get_session_for_principal(db, principal, session_id)
    return await recordings_view(db, session, principal)


@router.get(
    "/{session_id}/recordings/{part_id}/url",
    response_model=RecordingUrlOut,
    summary="Signed playback URL for one recording part (audited)",
)
async def recording_url(
    session_id: uuid.UUID, part_id: PartId, request: Request, principal: SessionReader, db: DbSession
) -> RecordingUrlOut:
    session = await get_session_for_principal(db, principal, session_id)
    part = await _playable_part(db, session.id, part_id)
    assert part.finalized is not None
    url, expires_at = signed_url_with_expiry(part.finalized.id, get_settings().recording_url_ttl_seconds)
    await audit(
        db,
        principal,
        "recording.view",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={
            "sensitive": True,
            "part_id": part.key,
            "kind": part.kind,
            "media_id": str(part.finalized.id),
            "candidate_id": str(session.candidate_id),
        },
    )
    return RecordingUrlOut(
        part_id=part.key,
        media_id=part.finalized.id,
        url=url,
        expires_at=expires_at,
        content_type=part.finalized.content_type,
    )


@router.get(
    "/{session_id}/recordings/{part_id}/download",
    response_model=RecordingUrlOut,
    summary="Signed download URL for one recording part (ADMIN / SUPER_ADMIN, audited)",
)
async def recording_download(
    session_id: uuid.UUID, part_id: PartId, request: Request, principal: RecordingDownloader, db: DbSession
) -> RecordingUrlOut:
    session = await get_session_for_principal(db, principal, session_id)
    part = await _playable_part(db, session.id, part_id)
    finalized = part.finalized
    assert finalized is not None
    url, expires_at = signed_url_with_expiry(finalized.id, get_settings().recording_url_ttl_seconds)
    filename = finalized.storage_key.rsplit("/", 1)[-1]
    await audit(
        db,
        principal,
        "recording.download",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={
            "sensitive": True,
            "part_id": part.key,
            "kind": part.kind,
            "media_id": str(finalized.id),
            "candidate_id": str(session.candidate_id),
            "size_bytes": finalized.size_bytes,
        },
    )
    return RecordingUrlOut(
        part_id=part.key,
        media_id=finalized.id,
        url=f"{url}?download=1",
        expires_at=expires_at,
        content_type=finalized.content_type,
        download=True,
        filename=filename,
    )


@router.post(
    "/{session_id}/recordings/finalize",
    response_model=RecordingFinalizeOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Process the session's recording now",
)
async def recording_finalize(
    session_id: uuid.UUID, request: Request, principal: RecordingProcessor, db: DbSession
) -> RecordingFinalizeOut:
    session = await get_session_for_principal(db, principal, session_id)
    queued: list[str] = []
    for part in await load_parts(db, session.id):
        pending = bool(part.uncovered) or (part.finalized is not None and not part.finalized.remuxed)
        if not pending:
            continue
        await enqueue(
            FINALIZE_JOB,
            {"session_id": str(session.id), "part_id": part.key, "force": True, "reason": "staff"},
            dedupe_key=finalize_dedupe_key(session.id, part.key),
            db=db,
        )
        queued.append(part.key)
    await audit(
        db,
        principal,
        "recording.finalize_requested",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={"parts": queued},
    )
    return RecordingFinalizeOut(queued=queued)
