"""Normalisation of untrusted browser signals (pure functions).

* Timestamps come from the browser clock: they are clamped to
  ``[now - MAX_CLIENT_LAG, now]`` (the original is kept in details when it
  had to be adjusted).
* ``details`` is reduced to a small set of scalar values; anything that could
  carry clipboard or editor content (``text``, ``content``, ``clipboard``...) is
  dropped: the platform records *that* a paste happened and its size, never
  what was pasted.
* Large pastes into the code editor are derived from ``paste_event`` details.
* Face-check signals (on-device face detector) carry an ``episode`` (one
  interval, or one page run for the summary) that groups them into one event;
  a browser can never claim the server's check (``server_check`` is dropped)
  and head direction is never accepted (it is not a signal on its own).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import IntegrityEventType, IntegritySource
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity.rules import LARGE_PASTE_MIN_CHARS, LARGE_PASTE_MIN_LINES, Signal

MAX_CLIENT_LAG = timedelta(minutes=15)
CLOCK_ADJUST_TOLERANCE_S = 2.0
MAX_DURATION_MS = 6 * 3600 * 1000
MAX_DETAIL_KEYS = 20
MAX_DETAIL_STRING = 200
_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_CONTENT_KEYS = frozenset(
    {
        "text",
        "content",
        "contents",
        "clipboard",
        "clipboard_text",
        "data",
        "value",
        "html",
        "code",
        "pasted",
        "pasted_text",
        "selection",
        "selected_text",
        "snippet",
        "body",
        "source",
        "payload",
    }
)
_CODING_CONTEXTS = frozenset({"coding", "code", "editor", "code_editor", "monaco"})
_EPISODE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
# Never accepted from a browser on face-check signals.
_FACE_DROPPED_KEYS = frozenset(
    {"server_check", "yaw", "pitch", "roll", "head_pose", "head_direction", "gaze", "looking_away"}
)
_FACE_INTERVAL = frozenset({IntegrityEventType.FACE_NOT_VISIBLE, IntegrityEventType.MULTIPLE_FACES_VISIBLE})
_FACE_SUMMARY = IntegrityEventType.FACE_CHECK_SUMMARY


def face_episode(details: Mapping[str, Any]) -> str | None:
    """Validated interval / run id from face-check details."""
    for key in ("episode", "run"):
        value = details.get(key)
        if isinstance(value, str) and _EPISODE.match(value):
            return value
    return None


def parse_client_time(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str) or len(value) > 64:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:  # epoch milliseconds sent as a string
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        seconds = number / 1000.0 if number > 1e11 else number
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clamp_time(
    value: datetime | None, *, now: datetime, max_lag: timedelta = MAX_CLIENT_LAG
) -> tuple[datetime, bool]:
    """Return ``(clamped, adjusted)``: never in the future, never older than ``max_lag``."""
    if value is None:
        return now, True
    clamped = min(value, now)
    clamped = max(clamped, now - max_lag)
    adjusted = abs((clamped - value).total_seconds()) > CLOCK_ADJUST_TOLERANCE_S
    return clamped, adjusted


def clamp_duration(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(min(max(number, 0.0), MAX_DURATION_MS))


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep at most ``MAX_DETAIL_KEYS`` scalar values with safe keys."""
    if not isinstance(details, Mapping):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if len(cleaned) >= MAX_DETAIL_KEYS:
            break
        if not isinstance(key, str) or not _KEY.match(key) or key.lower() in _CONTENT_KEYS:
            continue
        if value is None or isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, int):
            cleaned[key] = max(min(value, 2**53), -(2**53))
        elif isinstance(value, float):
            if math.isfinite(value):
                cleaned[key] = value
        elif isinstance(value, str):
            cleaned[key] = value[:MAX_DETAIL_STRING]
        # lists / dicts / other types are dropped
    return cleaned


def _number(details: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = details.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int | float) and math.isfinite(value):
            return int(value)
    return None


def paste_metrics(details: Mapping[str, Any]) -> tuple[int | None, int | None, bool]:
    """``(chars, lines, in_coding)`` from a paste event's details."""
    chars = _number(details, "length", "chars", "char_count", "characters", "size")
    lines = _number(details, "lines", "line_count")
    context = str(details.get("context") or details.get("target") or "").lower()
    in_coding = (
        context in _CODING_CONTEXTS or details.get("in_coding") is True or details.get("coding") is True
    )
    return chars, lines, in_coding


def is_large_coding_paste(details: Mapping[str, Any]) -> bool:
    chars, lines, in_coding = paste_metrics(details)
    if not in_coding:
        return False
    return (chars is not None and chars >= LARGE_PASTE_MIN_CHARS) or (
        lines is not None and lines >= LARGE_PASTE_MIN_LINES
    )


def client_signal(event: ClientIntegrityEvent, *, now: datetime) -> Signal:
    raw_time = parse_client_time(event.occurred_at)
    occurred_at, adjusted = clamp_time(raw_time, now=now)
    event_type = IntegrityEventType(event.event_type)
    details = sanitize_details(event.details)
    episode: str | None = None
    mode: str = "add"
    if event_type in _FACE_INTERVAL or event_type is _FACE_SUMMARY:
        for key in _FACE_DROPPED_KEYS:
            details.pop(key, None)
        episode = face_episode(details)
        if episode is not None:
            mode = "summary" if event_type is _FACE_SUMMARY else "episode"
    if adjusted:
        details["clock_adjusted"] = True
        details["client_occurred_at"] = (event.occurred_at or "")[:64]
    return Signal(
        event_type=event_type,
        source=IntegritySource.BROWSER,
        occurred_at=occurred_at,
        duration_ms=clamp_duration(event.duration_ms),
        details=details,
        episode=episode,
        duration_mode=mode,  # type: ignore[arg-type]
    )


def derived_large_paste(paste: Signal) -> Signal:
    chars, lines, _ = paste_metrics(paste.details)
    return Signal(
        event_type=IntegrityEventType.LARGE_PASTE_IN_CODING,
        source=IntegritySource.BROWSER,
        occurred_at=paste.occurred_at,
        details={"derived_from": "paste_event", "chars": chars, "lines": lines},
    )
