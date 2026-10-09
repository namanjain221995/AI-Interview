"""ffmpeg / ffprobe as subprocesses: remux (stream copy, never a transcode) and probe.

The binaries come from ``RECORDING_FFMPEG_PATH`` / ``RECORDING_FFPROBE_PATH``
(default: looked up on ``PATH``; the API image installs Debian's ffmpeg). When
they are missing, :func:`tools` reports ``None`` and the finalize job falls
back to plain concatenation (playable, but browsers cannot seek it well).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_CACHE_TTL_S = 300.0
_STDERR_TAIL = 600
Container = Literal["webm", "mp4"]


@dataclass(frozen=True, slots=True)
class Tools:
    ffmpeg: str | None
    ffprobe: str | None


@dataclass(frozen=True, slots=True)
class Probe:
    duration_ms: int | None
    has_video: bool
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    format_name: str | None = None


class ToolFailed(RuntimeError):
    """ffmpeg/ffprobe exited non-zero, timed out or produced nothing."""

    def __init__(self, tool: str, returncode: int | None, stderr: bytes | str = b"") -> None:
        text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
        self.tool = tool
        self.returncode = returncode
        self.stderr_tail = text.strip()[-_STDERR_TAIL:]
        super().__init__(f"{tool} failed (exit {returncode}): {self.stderr_tail[-200:]}")


_cache: tuple[float, Tools] | None = None


def _which(name: str | None) -> str | None:
    if not name:
        return None
    if os.sep in name:
        return name if os.path.isfile(name) and os.access(name, os.X_OK) else None
    return shutil.which(name)


def tools(*, refresh: bool = False) -> Tools:
    """The available binaries (cached for a few minutes per process)."""
    global _cache
    now = time.monotonic()
    if not refresh and _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        return _cache[1]
    settings = get_settings()
    found = Tools(
        ffmpeg=_which(settings.recording_ffmpeg_path), ffprobe=_which(settings.recording_ffprobe_path)
    )
    _cache = (now, found)
    return found


def reset_tools_cache() -> None:
    global _cache
    _cache = None


async def _run(argv: list[str], *, limit_s: float) -> tuple[int, bytes, bytes]:
    """Run ``argv``; raises ``TimeoutError`` (after killing it) past ``limit_s``."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(limit_s):
            out, err = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return int(proc.returncode or 0), out, err


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


async def remux(src: Path, dst: Path, *, container: Container, limit_s: float) -> None:
    """Copy every stream of ``src`` into a fresh, seekable ``dst`` (cues + duration
    for WebM, ``faststart`` index for MP4)."""
    binary = tools().ffmpeg
    if binary is None:
        raise ToolFailed("ffmpeg", None, "ffmpeg is not installed")
    argv = [
        binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        str(src),
        "-map",
        "0",
        "-c",
        "copy",
    ]
    argv += ["-movflags", "+faststart", "-f", "mp4"] if container == "mp4" else ["-f", "webm"]
    argv.append(str(dst))
    try:
        code, _out, err = await _run(argv, limit_s=limit_s)
    except TimeoutError:
        raise ToolFailed("ffmpeg", None, f"timed out after {limit_s:.0f} s") from None
    if code != 0 or await asyncio.to_thread(_file_size, dst) == 0:
        raise ToolFailed("ffmpeg", code, err)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _duration_ms(value: Any) -> int | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(seconds * 1000)) if seconds > 0 else None


def parse_probe(payload: dict[str, Any]) -> Probe:
    streams = [s for s in payload.get("streams") or [] if isinstance(s, dict)]
    raw_format = payload.get("format")
    fmt: dict[str, Any] = raw_format if isinstance(raw_format, dict) else {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _duration_ms(fmt.get("duration"))
    if duration is None:
        durations = [d for d in (_duration_ms(s.get("duration")) for s in streams) if d]
        duration = max(durations) if durations else None
    return Probe(
        duration_ms=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name")) if video and video.get("codec_name") else None,
        audio_codec=str(audio.get("codec_name")) if audio and audio.get("codec_name") else None,
        width=_int(video.get("width")) if video else None,
        height=_int(video.get("height")) if video else None,
        format_name=str(fmt.get("format_name")) if fmt.get("format_name") else None,
    )


async def probe(path: Path, *, limit_s: float = 60.0) -> Probe | None:
    """Streams and duration of ``path``; ``None`` without ffprobe or on failure."""
    binary = tools().ffprobe
    if binary is None:
        return None
    argv = [
        binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        code, out, err = await _run(argv, limit_s=limit_s)
    except TimeoutError:
        log.warning("recording.probe_timeout", limit_s=limit_s)
        return None
    if code != 0:
        log.warning("recording.probe_failed", code=code, error=err.decode("utf-8", "replace")[-200:])
        return None
    try:
        payload = json.loads(out.decode("utf-8", "replace") or "{}")
    except ValueError:
        return None
    return parse_probe(payload) if isinstance(payload, dict) else None


__all__ = [
    "Container",
    "Probe",
    "ToolFailed",
    "Tools",
    "parse_probe",
    "probe",
    "remux",
    "reset_tools_cache",
    "tools",
]
