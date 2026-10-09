"""Media access: the only way to read stored files.

1. ``GET /media/{id}/url`` authorizes the caller for that object and returns a
   short-lived API-signed URL (audited as ``media.view``).
   * staff: same organization (SUPER_ADMIN: any) and the permission matching
     the kind - resumes need ``candidates:read``, report artifacts
     ``reports:read``, every session recording/frame ``sessions:read``;
   * candidates: only their own resume.
2. ``GET /media/signed/{token}`` verifies the HMAC token and streams the file
   with single-range support (``206`` + ``Content-Range``, so video players
   can seek), the stored content type, ``nosniff``, ``no-store`` and
   ``attachment`` for documents (anything that could render active content is
   never served inline). ``?download=1`` only switches media to ``attachment``
   (the recording download URL, issued to admins and audited, uses it).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.core.audit import audit
from app.core.deps import CurrentPrincipal, DbSession
from app.core.errors import Forbidden, NotFound, error_response
from app.core.rbac import Permission, Principal
from app.db.models import MediaAsset
from app.domain.enums import MediaKind
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.media import MediaUrlResponse
from app.services.storage import get_storage
from app.services.storage.media import get_media
from app.services.storage.signing import InvalidMediaToken, signed_url_with_expiry, verify_media_token

router = APIRouter(prefix="/media", tags=["media"], responses=ERROR_RESPONSES)

_KIND_PERMISSION: dict[MediaKind, Permission] = {
    MediaKind.RESUME: Permission.CANDIDATES_READ,
    MediaKind.REPORT_ARTIFACT: Permission.REPORTS_READ,
}
# Types that browsers may render inline safely (no script execution).
_INLINE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "video/webm",
        "video/mp4",
        "video/ogg",
    }
)
# Types that could execute script if a browser ever rendered them: always
# downloaded as opaque bytes (the file name keeps the extension).
_ACTIVE_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
        "text/javascript",
        "application/javascript",
    }
)
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def required_permission(kind: MediaKind) -> Permission:
    return _KIND_PERMISSION.get(kind, Permission.SESSIONS_READ)


def authorize_media(principal: Principal, asset: MediaAsset) -> None:
    """Raise unless ``principal`` may read ``asset`` (cross-org looks like 404)."""
    kind = MediaKind(asset.kind)
    if principal.is_candidate:
        if kind is MediaKind.RESUME and asset.candidate_id is not None and asset.candidate_id == principal.candidate_id:
            return
        raise NotFound("Media not found.")
    if not principal.is_staff:
        raise Forbidden()
    if not principal.is_super_admin and asset.organization_id != principal.organization_id:
        raise NotFound("Media not found.")
    perm = required_permission(kind)
    if not principal.has(perm):
        raise Forbidden(details={"missing_permissions": [perm.value]})


def _expired(asset: MediaAsset) -> bool:
    return asset.retention_until is not None and asset.retention_until <= datetime.now(UTC)


class RangeNotSatisfiable(ValueError):
    """The Range header cannot be served for this object (HTTP 416)."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """``(start, end)`` inclusive, ``None`` = serve everything; raises
    :class:`RangeNotSatisfiable` for an unsatisfiable range."""
    if not header:
        return None
    match = _RANGE.match(header.strip())
    if match is None:
        return None  # unsupported unit or multiple ranges: RFC 9110 allows ignoring Range
    first, last = match.groups()
    if not first and not last:
        return None
    if size <= 0:
        raise RangeNotSatisfiable()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise RangeNotSatisfiable()
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise RangeNotSatisfiable()
    return start, end


def _disposition(asset: MediaAsset, inline: bool) -> str:
    filename = asset.storage_key.rsplit("/", 1)[-1]
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{filename}\"; filename*=UTF-8''{filename}"


@router.get("/{media_id}/url", response_model=MediaUrlResponse, summary="Get a short-lived signed URL")
async def media_url(media_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: DbSession) -> MediaUrlResponse:
    asset = await get_media(db, media_id)
    authorize_media(principal, asset)
    if _expired(asset):
        raise NotFound("Media not found.")
    url, expires_at = signed_url_with_expiry(asset.id)
    await audit(
        db, principal, "media.view", resource_type="media", resource_id=str(asset.id), request=request,
        organization_id=asset.organization_id,
        details={"kind": asset.kind, "session_id": asset.session_id, "candidate_id": asset.candidate_id},
    )
    return MediaUrlResponse(
        url=url,
        expires_at=expires_at,
        media_id=asset.id,
        kind=MediaKind(asset.kind),
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
    )


@router.get("/signed/{token}", summary="Stream a file through a signed URL", response_class=StreamingResponse)
async def media_signed(token: str, request: Request, db: DbSession, download: bool = False) -> Response:
    return await _serve_signed(token, request, db, download=download)


@router.head("/signed/{token}", include_in_schema=False)
async def media_signed_head(token: str, request: Request, db: DbSession, download: bool = False) -> Response:
    return await _serve_signed(token, request, db, download=download)


async def _serve_signed(token: str, request: Request, db: DbSession, *, download: bool = False) -> Response:
    try:
        media_id = verify_media_token(token)
    except InvalidMediaToken:
        raise NotFound("This link is invalid or has expired.") from None
    asset = await get_media(db, media_id)
    if _expired(asset):
        raise NotFound("Media not found.")
    storage = get_storage()
    size = await storage.size(asset.storage_key)
    content_type = (asset.content_type or "application/octet-stream").split(";")[0].strip().lower()
    inline = content_type in _INLINE_TYPES and not download
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": _disposition(asset, inline),
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if inline:
        media_type = content_type
    elif content_type in _ACTIVE_TYPES:
        media_type = "application/octet-stream"
    else:
        media_type = asset.content_type or "application/octet-stream"
    try:
        byte_range = parse_range(request.headers.get("range"), size)
    except RangeNotSatisfiable:
        return error_response(
            416, "range_not_satisfiable", "The requested range is not satisfiable.",
            request_id=getattr(request.state, "request_id", None), headers={"Content-Range": f"bytes */{size}"},
        )
    head_only = request.method == "HEAD"
    if byte_range is None:
        headers["Content-Length"] = str(size)
        if head_only:
            return Response(status_code=200, media_type=media_type, headers=headers)
        return StreamingResponse(storage.open(asset.storage_key), status_code=200, media_type=media_type, headers=headers)
    start, end = byte_range
    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(length)
    if head_only:
        return Response(status_code=206, media_type=media_type, headers=headers)
    return StreamingResponse(
        storage.open(asset.storage_key, offset=start, length=length), status_code=206, media_type=media_type, headers=headers
    )
