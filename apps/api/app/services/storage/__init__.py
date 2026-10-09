"""File storage (local filesystem only; see CONTRACTS.md section 7).

    from app.services.storage import get_storage
    stored = await get_storage().put("orgs/<org>/resumes/<cand>/<resume>/cv.pdf", data, "application/pdf")

Register every stored file with :func:`app.services.storage.media.store_media`
and hand out access only through :mod:`app.services.storage.signing`.
"""

from __future__ import annotations

from app.core.config import get_settings
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


__all__ = [
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
