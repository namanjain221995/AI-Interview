"""Local filesystem storage backend.

* Keys map to paths under ``root``; traversal and symlink escapes are rejected.
* Writes are atomic: data goes to a hidden temp file in the target directory
  (``O_EXCL | O_NOFOLLOW``, mode 0600), is fsynced, renamed over the target
  and the directory entry is fsynced. Directories are created with mode 0700.
* All blocking IO runs in worker threads (``asyncio.to_thread``); reads stream
  in chunks so large media never sits in memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import stat
import time
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import BinaryIO

from app.core.errors import PayloadTooLarge
from app.services.storage.base import InvalidStorageKey, ObjectNotFound, StoredObject, validate_key

DEFAULT_CHUNK_SIZE = 256 * 1024
_WRITE_BUFFER = 1024 * 1024
_FILE_MODE = 0o600
_DIR_MODE = 0o700
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class LocalStorage:
    backend_name = "local"

    def __init__(self, root: str | os.PathLike[str], *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        self.root = Path(os.path.abspath(root))
        self.chunk_size = chunk_size

    def __repr__(self) -> str:
        return f"LocalStorage(root={str(self.root)!r})"

    # ------------------------------------------------------------ paths

    def local_path(self, key: str) -> Path:
        """Filesystem path of ``key`` (validated; the file may not exist)."""
        validate_key(key)
        path = self.root.joinpath(*key.split("/"))
        self._assert_inside_root(path.parent)
        return path

    def _assert_inside_root(self, directory: Path) -> None:
        # Resolve the deepest existing ancestor: a symlinked directory inside
        # the root must not redirect writes or reads outside of it.
        probe = directory
        while not probe.exists() and probe != self.root and self.root in probe.parents:
            probe = probe.parent
        real_root = Path(os.path.realpath(self.root))
        real = Path(os.path.realpath(probe))
        if real != real_root and real_root not in real.parents:
            raise InvalidStorageKey("Storage key resolves outside the storage root")

    def _ensure_dir(self, directory: Path) -> None:
        missing: list[Path] = []
        probe = directory
        while not probe.exists():
            missing.append(probe)
            if probe == self.root or probe.parent == probe:
                break
            probe = probe.parent
        for path in reversed(missing):
            try:
                os.mkdir(path, _DIR_MODE)
            except FileExistsError:
                pass
            else:
                os.chmod(path, _DIR_MODE)
        self._assert_inside_root(directory)

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY | _O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _open_for_read(self, key: str) -> BinaryIO:
        path = self.local_path(key)
        try:
            fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
        except FileNotFoundError:
            raise ObjectNotFound() from None
        except OSError as exc:  # ELOOP: final component is a symlink
            raise ObjectNotFound() from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ObjectNotFound()
        return os.fdopen(fd, "rb")

    # ------------------------------------------------------------ writes

    def _new_temp(self, path: Path) -> tuple[Path, BinaryIO]:
        self._ensure_dir(path.parent)
        tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, _FILE_MODE)
        return tmp, os.fdopen(fd, "wb")

    def _commit_temp(self, tmp: Path, handle: BinaryIO, path: Path) -> None:
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp, path)
        self._fsync_dir(path.parent)

    @staticmethod
    def _discard_temp(tmp: Path, handle: BinaryIO) -> None:
        with contextlib.suppress(OSError):
            handle.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)

    def _put_sync(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self.local_path(key)
        tmp, handle = self._new_temp(path)
        try:
            handle.write(data)
            self._commit_temp(tmp, handle, path)
        except BaseException:
            self._discard_temp(tmp, handle)
            raise
        return StoredObject(key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest(), content_type=content_type)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        return await asyncio.to_thread(self._put_sync, key, bytes(data), content_type)

    async def put_stream(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        content_type: str = "application/octet-stream",
        *,
        max_bytes: int | None = None,
    ) -> StoredObject:
        path = self.local_path(key)
        tmp, handle = await asyncio.to_thread(self._new_temp, path)
        digest = hashlib.sha256()
        size = 0
        buffer = bytearray()
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise PayloadTooLarge(f"Upload exceeds the limit of {max_bytes} bytes.")
                digest.update(chunk)
                buffer += chunk
                if len(buffer) >= _WRITE_BUFFER:
                    data, buffer = bytes(buffer), bytearray()
                    await asyncio.to_thread(handle.write, data)
            if buffer:
                await asyncio.to_thread(handle.write, bytes(buffer))
            await asyncio.to_thread(self._commit_temp, tmp, handle, path)
        except BaseException:
            await asyncio.to_thread(self._discard_temp, tmp, handle)
            raise
        return StoredObject(key=key, size=size, sha256=digest.hexdigest(), content_type=content_type)

    # ------------------------------------------------------------- reads

    async def open(
        self, key: str, *, offset: int = 0, length: int | None = None, chunk_size: int | None = None
    ) -> AsyncIterator[bytes]:
        if offset < 0 or (length is not None and length < 0):
            raise ValueError("offset and length must be non-negative")
        step = chunk_size or self.chunk_size
        handle = await asyncio.to_thread(self._open_for_read, key)
        try:
            if offset:
                await asyncio.to_thread(handle.seek, offset)
            remaining = length
            while remaining is None or remaining > 0:
                want = step if remaining is None else min(step, remaining)
                data = await asyncio.to_thread(handle.read, want)
                if not data:
                    break
                if remaining is not None:
                    remaining -= len(data)
                yield data
        finally:
            handle.close()

    def _read_sync(self, key: str) -> bytes:
        with self._open_for_read(key) as handle:
            return handle.read()

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, key)

    def _stat(self, key: str) -> os.stat_result | None:
        path = self.local_path(key)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        return st if stat.S_ISREG(st.st_mode) else None

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._stat, key) is not None

    async def size(self, key: str) -> int:
        st = await asyncio.to_thread(self._stat, key)
        if st is None:
            raise ObjectNotFound()
        return st.st_size

    # ------------------------------------------------------------ delete

    def _delete_sync(self, key: str) -> bool:
        path = self.local_path(key)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return False
        if stat.S_ISDIR(st.st_mode):
            raise InvalidStorageKey("Storage key refers to a directory")
        os.unlink(path)
        # Prune now-empty parent directories (retention sweeps leave many).
        parent = path.parent
        while parent != self.root and self.root in parent.parents:
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = parent.parent
        return True

    async def delete(self, key: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, key)

    # ------------------------------------------------------------- admin

    def _ensure_root_sync(self) -> None:
        self.root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        st = os.stat(self.root)
        if st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) != _DIR_MODE:
            os.chmod(self.root, _DIR_MODE)

    async def ensure_root(self) -> None:
        await asyncio.to_thread(self._ensure_root_sync)

    def _check_sync(self) -> float:
        started = time.perf_counter()
        self._ensure_root_sync()
        probe = self.root / f".probe-{uuid.uuid4().hex}"
        payload = os.urandom(32)
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, _FILE_MODE)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            if probe.read_bytes() != payload:
                raise OSError("storage probe read back different bytes")
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(probe)
        return (time.perf_counter() - started) * 1000

    async def check(self) -> float:
        return await asyncio.to_thread(self._check_sync)
