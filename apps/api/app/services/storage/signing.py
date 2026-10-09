"""API-signed, short-lived media URLs.

Token layout (base64url, no padding)::

    version (1 byte = 0x01) | media_id (16 bytes) | expires_at (uint64 BE, unix s) | HMAC-SHA256 (32 bytes)

The MAC covers ``b"interview/media/v1" + version + media_id + expires_at`` and is
keyed with ``MEDIA_URL_SECRET``. Tokens are bearer capabilities for one media
object until they expire (default ``MEDIA_URL_TTL_SECONDS`` = 300 s); they are
only issued after the API authorized the caller for that object.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time
import uuid
from datetime import UTC, datetime

from app.core.config import get_settings

_VERSION = 1
_CONTEXT = b"interview/media/v1"
_PAYLOAD = struct.Struct(">B16sQ")
_MAC_BYTES = 32
_TOKEN_BYTES = _PAYLOAD.size + _MAC_BYTES
SIGNED_URL_PREFIX = "/api/v1/media/signed/"
MAX_TTL_SECONDS = 24 * 3600


class InvalidMediaToken(ValueError):
    """Malformed, forged or expired media token."""


def _key() -> bytes:
    return get_settings().media_url_secret.get_secret_value().encode("utf-8")


def _mac(payload: bytes) -> bytes:
    return hmac.new(_key(), _CONTEXT + payload, hashlib.sha256).digest()


def make_media_token(media_id: uuid.UUID | str, ttl: int | None = None, *, now: float | None = None) -> str:
    ttl_seconds = int(ttl if ttl is not None else get_settings().media_url_ttl_seconds)
    if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError(f"ttl must be between 1 and {MAX_TTL_SECONDS} seconds")
    mid = media_id if isinstance(media_id, uuid.UUID) else uuid.UUID(str(media_id))
    expires = int(now if now is not None else time.time()) + ttl_seconds
    payload = _PAYLOAD.pack(_VERSION, mid.bytes, expires)
    return base64.urlsafe_b64encode(payload + _mac(payload)).rstrip(b"=").decode("ascii")


def decode_media_token(token: str, *, now: float | None = None) -> tuple[uuid.UUID, datetime]:
    """Return ``(media_id, expires_at)`` or raise :class:`InvalidMediaToken`."""
    if not token or len(token) > 128:
        raise InvalidMediaToken("malformed token")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError):
        raise InvalidMediaToken("malformed token") from None
    if len(raw) != _TOKEN_BYTES:
        raise InvalidMediaToken("malformed token")
    payload, mac = raw[: _PAYLOAD.size], raw[_PAYLOAD.size :]
    if not hmac.compare_digest(mac, _mac(payload)):
        raise InvalidMediaToken("bad signature")
    version, mid, expires = _PAYLOAD.unpack(payload)
    if version != _VERSION:
        raise InvalidMediaToken("unsupported token version")
    current = int(now if now is not None else time.time())
    if expires < current:
        raise InvalidMediaToken("token expired")
    return uuid.UUID(bytes=mid), datetime.fromtimestamp(expires, tz=UTC)


def verify_media_token(token: str, *, now: float | None = None) -> uuid.UUID:
    """Return the media id a valid token grants access to."""
    media_id, _ = decode_media_token(token, now=now)
    return media_id


def signed_url_with_expiry(media_id: uuid.UUID | str, ttl: int | None = None) -> tuple[str, datetime]:
    token = make_media_token(media_id, ttl)
    _, expires_at = decode_media_token(token)
    return f"{SIGNED_URL_PREFIX}{token}", expires_at


def signed_url(media_id: uuid.UUID | str, ttl: int | None = None) -> str:
    """Relative URL ``/api/v1/media/signed/{token}`` (served by the media router)."""
    return signed_url_with_expiry(media_id, ttl)[0]
