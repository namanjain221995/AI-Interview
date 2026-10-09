"""Verify the Kokoro model files, downloading any that are missing.

Each file is checked against its pinned SHA-256: a present file that does not
match is an error (never silently replaced); a missing one is downloaded from
the kokoro-onnx release, verified, then moved into place atomically.

Usage: python fetch_models.py MODELS_DIR
Standard library only (runs in the Docker build stage before dependencies).
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path, expected: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "interview-tts-build"})  # noqa: S310 (fixed https URL)
    digest = hashlib.sha256()
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            while block := response.read(CHUNK):
                digest.update(block)
                out.write(block)
        actual = digest.hexdigest()
        if actual != expected:
            raise SystemExit(f"checksum mismatch for {url}: expected {expected}, got {actual}")
        tmp.chmod(0o644)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    models = Path(sys.argv[1])
    models.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        path = models / name
        if path.is_file():
            actual = sha256_of(path)
            if actual != expected:
                raise SystemExit(f"{path}: sha256 {actual} does not match the pinned {expected}")
            print(f"{name}: present, sha256 ok")
            continue
        print(f"{name}: downloading from {RELEASE}")
        download(f"{RELEASE}/{name}", path, expected)
        print(f"{name}: downloaded, sha256 ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
