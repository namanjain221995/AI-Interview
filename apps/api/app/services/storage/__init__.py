"""File storage (local filesystem only; see CONTRACTS.md section 7).

    from app.services.storage import get_storage
    stored = await get_storage().put("orgs/<org>/resumes/<cand>/<resume>/cv.pdf", data, "application/pdf")

Register every stored file with :func:`app.services.storage.media.store_media`
and hand out access only through :mod:`app.services.storage.signing`.
"""

from __future__ import annotations

import shutil

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable
from app.core.logging import get_logger
from app.services.storage.base import (
    InvalidStorageKey,
    ObjectNotFound,
    StorageBackend,
    StoredObject,
    safe_filename,
    validate_key,
)
from app.services.storage.local import LocalStorage

_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Process-wide backend built from ``STORAGE_BACKEND`` / ``STORAGE_LOCAL_ROOT``."""
    global _storage
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend != "local":  # pragma: no cover - Literal["local"] today
            raise RuntimeError(f"Unsupported STORAGE_BACKEND {settings.storage_backend!r}")
        _storage = LocalStorage(settings.storage_local_root)
    return _storage


def set_storage(backend: StorageBackend | None) -> None:
    """Override (tests) or reset (``None``) the process-wide backend."""
    global _storage
    _storage = backend


def storage_free_ratio() -> float | None:
    """Free share of the storage disk (None when it cannot be measured)."""
    settings = get_settings()
    if settings.storage_backend != "local":
        return None
    try:
        usage = shutil.disk_usage(settings.storage_local_root)
    except OSError:
        return None
    return usage.free / usage.total if usage.total else None


def ensure_storage_headroom() -> None:
    """503 ``storage_full`` while the storage disk is nearly full (S3, audit D2): candidate uploads must never
    fill the disk that also holds the database. Unknown usage never blocks (health reports it)."""
    ratio = storage_free_ratio()
    floor = get_settings().storage_min_free_ratio
    if ratio is not None and ratio < floor:
        get_logger(__name__).error("storage.low_headroom", free_ratio=round(ratio, 4), min_free_ratio=floor)
        raise ServiceUnavailable(
            "Uploads are paused because storage is nearly full. The interview itself continues.",
            code="storage_full",
            retry_after=300,
        )


__all__ = [
    "ensure_storage_headroom",
    "storage_free_ratio",
    "InvalidStorageKey",
    "LocalStorage",
    "ObjectNotFound",
    "StorageBackend",
    "StoredObject",
    "get_storage",
    "safe_filename",
    "set_storage",
    "validate_key",
]
