"""Storage interface, key rules and errors (CONTRACTS.md section 7).

Keys are relative POSIX paths such as
``orgs/{org_id}/sessions/{session_id}/audio/{answer_id}.wav``. Every segment
must match ``[A-Za-z0-9_+=@,~:-][A-Za-z0-9._+=@,~:-]*`` (no leading dot, so
``.``/``..``, hidden files and temp files are unaddressable), the key is at
most 512 characters (the ``media_assets.storage_key`` column) and each segment
at most 255 bytes. Use :func:`safe_filename` for user-supplied file names.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.errors import NotFound

MAX_KEY_LENGTH = 512
MAX_SEGMENT_BYTES = 255
_SEGMENT = re.compile(r"^[A-Za-z0-9_+=@,~:-][A-Za-z0-9._+=@,~:-]*$")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class InvalidStorageKey(ValueError):
    """The key is absolute, traverses outside the root or uses forbidden characters."""


class ObjectNotFound(NotFound):
    status_code = 404
    code = "not_found"
    message = "The stored object was not found."


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    sha256: str
    content_type: str


def validate_key(key: str) -> str:
    """Return ``key`` unchanged if it is a safe relative key, else raise."""
    if not isinstance(key, str) or not key:
        raise InvalidStorageKey("Storage key must be a non-empty string")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidStorageKey(f"Storage key longer than {MAX_KEY_LENGTH} characters")
    if key.startswith("/") or "\\" in key or "\x00" in key:
        raise InvalidStorageKey("Storage key must be a relative POSIX path")
    for segment in key.split("/"):
        if not segment:
            raise InvalidStorageKey("Storage key contains an empty path segment")
        if not _SEGMENT.match(segment):
            raise InvalidStorageKey(f"Storage key segment {segment[:40]!r} contains forbidden characters")
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            raise InvalidStorageKey("Storage key segment is too long")
    return key


def safe_filename(name: str, *, default: str = "file", max_length: int = 120) -> str:
    """Reduce a client-supplied file name to a safe single key segment.

    ``"../../Jane Doe CV (final).PDF"`` -> ``"Jane_Doe_CV_final_.PDF"``.
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    base = _FILENAME_UNSAFE.sub("_", base).strip("._-")
    if not base:
        base = default
    if len(base) > max_length:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            base = f"{stem[: max_length - len(ext) - 1]}.{ext}"
        else:
            base = base[:max_length]
    return base


@runtime_checkable
class StorageBackend(Protocol):
    """Async object storage. ``LocalStorage`` is the only implementation today."""

    backend_name: str

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject: ...

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        content_type: str = "application/octet-stream",
        *,
        max_bytes: int | None = None,
    ) -> StoredObject: ...

    def open(self, key: str, *, offset: int = 0, length: int | None = None, chunk_size: int | None = None) -> AsyncIterator[bytes]:
        """Async iterator over the object's bytes (optionally a byte range)."""
        ...

    async def read(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def size(self, key: str) -> int: ...

    async def delete(self, key: str) -> bool: ...

    async def ensure_root(self) -> None: ...

    async def check(self) -> float:
        """Write/read/delete a probe; returns the round trip in milliseconds."""
        ...
