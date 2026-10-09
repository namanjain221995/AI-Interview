from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from app.core.errors import PayloadTooLarge
from app.services.storage import InvalidStorageKey, LocalStorage, ObjectNotFound, get_storage, set_storage
from app.services.storage.base import safe_filename, validate_key


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "root")


@pytest.mark.parametrize(
    "key",
    [
        "orgs/1/resumes/c/r/cv.pdf",
        "orgs/a-b_c/sessions/s/frames/camera/2026-10-07T08:00:00Z-x.jpg",
        "a",
        "orgs/x/video/00001.webm",
    ],
)
def test_valid_keys(key):
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../secret",
        "orgs/../../etc/passwd",
        "orgs/./x",
        "orgs//x",
        "orgs/x/",
        ".hidden",
        "orgs/.x.tmp",
        "orgs\\x",
        "orgs/x y",
        "orgs/\x00",
        "orgs/" + "a" * 300,
        "a/" * 300,
    ],
)
def test_invalid_keys(key):
    with pytest.raises(InvalidStorageKey):
        validate_key(key)


def test_safe_filename():
    assert safe_filename("../../Jane Doe CV (final).PDF") == "Jane_Doe_CV_final_.PDF"
    assert safe_filename("résumé.docx") == "resume.docx"
    assert safe_filename("...") == "file"
    long = safe_filename("x" * 300 + ".pdf")
    assert long.endswith(".pdf") and len(long) <= 120
    validate_key(f"orgs/1/{safe_filename('a/b\\\\c?.txt')}")


async def test_put_read_and_metadata(store: LocalStorage):
    data = os.urandom(1000)
    stored = await store.put("orgs/o/file.bin", data, "application/octet-stream")
    assert stored.size == 1000 and stored.sha256 == hashlib.sha256(data).hexdigest()
    assert await store.read("orgs/o/file.bin") == data
    assert await store.exists("orgs/o/file.bin")
    assert await store.size("orgs/o/file.bin") == 1000
    path = store.local_path("orgs/o/file.bin")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "orgs").stat().st_mode) == 0o700
    assert not any(p.name.endswith(".tmp") for p in path.parent.iterdir())


async def test_overwrite_is_atomic_replace(store: LocalStorage):
    await store.put("k/a.txt", b"first", "text/plain")
    await store.put("k/a.txt", b"second", "text/plain")
    assert await store.read("k/a.txt") == b"second"
    assert [p.name for p in store.local_path("k/a.txt").parent.iterdir()] == ["a.txt"]


async def test_open_streams_chunks_and_ranges(store: LocalStorage):
    data = bytes(range(256)) * 40  # 10240 bytes
    await store.put("r/data.bin", data)
    chunks = [c async for c in store.open("r/data.bin", chunk_size=1000)]
    assert b"".join(chunks) == data and len(chunks) == 11
    part = b"".join([c async for c in store.open("r/data.bin", offset=100, length=50)])
    assert part == data[100:150]
    tail = b"".join([c async for c in store.open("r/data.bin", offset=10000)])
    assert tail == data[10000:]
    with pytest.raises(ObjectNotFound):
        [c async for c in store.open("r/missing.bin")]


async def test_put_stream_with_limit(store: LocalStorage):
    async def gen(n: int):
        for _ in range(n):
            yield b"x" * 600_000

    stored = await store.put_stream("s/big.bin", gen(3), "application/octet-stream", max_bytes=2_000_000)
    assert stored.size == 1_800_000
    assert stored.sha256 == hashlib.sha256(b"x" * 1_800_000).hexdigest()
    with pytest.raises(PayloadTooLarge):
        await store.put_stream("s/too-big.bin", gen(4), max_bytes=2_000_000)
    assert not await store.exists("s/too-big.bin")
    assert not any(p.name.endswith(".tmp") for p in store.local_path("s/big.bin").parent.iterdir())


async def test_missing_objects(store: LocalStorage):
    assert not await store.exists("nope/x")
    with pytest.raises(ObjectNotFound):
        await store.size("nope/x")
    with pytest.raises(ObjectNotFound):
        await store.read("nope/x")
    assert await store.delete("nope/x") is False


async def test_delete_prunes_empty_directories(store: LocalStorage):
    await store.put("orgs/o/sessions/s/audio/a.wav", b"RIFF")
    await store.put("orgs/o/keep.txt", b"k")
    assert await store.delete("orgs/o/sessions/s/audio/a.wav") is True
    assert not (store.root / "orgs/o/sessions").exists()
    assert (store.root / "orgs/o/keep.txt").exists()


async def test_symlink_escape_rejected(store: LocalStorage, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    await store.ensure_root()
    os.symlink(outside, store.root / "link")
    with pytest.raises(InvalidStorageKey):
        await store.read("link/secret.txt")
    with pytest.raises(InvalidStorageKey):
        await store.put("link/new.txt", b"x")
    assert not (outside / "new.txt").exists()
    os.symlink(outside / "secret.txt", store.root / "file-link")
    with pytest.raises(ObjectNotFound):
        await store.read("file-link")


async def test_root_and_probe(store: LocalStorage):
    await store.ensure_root()
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    latency = await store.check()
    assert latency >= 0
    assert list(store.root.iterdir()) == []


def test_get_storage_singleton_and_override(tmp_path: Path):
    custom = LocalStorage(tmp_path)
    set_storage(custom)
    try:
        assert get_storage() is custom
    finally:
        set_storage(None)
    assert isinstance(get_storage(), LocalStorage)
