"""Names, limits and timing rules shared by the recording modules."""

from __future__ import annotations

from datetime import timedelta

from app.domain.enums import MediaKind

FINALIZE_JOB = "recording.finalize"
SWEEP_JOB = "recording.sweep"
SWEEP_INTERVAL_SECONDS = 120

SEGMENT_KINDS: tuple[str, ...] = (MediaKind.VIDEO_SEGMENT.value, MediaKind.SCREEN_SEGMENT.value)
RECORDING_KIND = MediaKind.SESSION_RECORDING.value

# Segments still arriving after the session ended (the browser uploads its last
# timeslice when the interview closes) are accepted for this long.
UPLOAD_GRACE = timedelta(minutes=10)
# The sweep closes a part this long after its session ended (late uploads first).
FINALIZE_SETTLE = timedelta(minutes=2)
# A part that received nothing for this long is over (page reload, crash).
PART_IDLE = timedelta(minutes=5)
# A part that received a segment this recently is shown as "recording".
RECORDING_ACTIVE = timedelta(seconds=45)
# Failed finalize attempts before the sweep stops retrying a part (until new
# segments arrive or staff press "Process now").
MAX_FINALIZE_ATTEMPTS = 3
# Sessions the sweep looks at: still open, or ended/updated this recently.
SWEEP_LOOKBACK = timedelta(days=7)
SWEEP_MAX_SESSIONS = 300

# Segment timing values from the browser are clamped to sane ranges.
MAX_MEDIA_MS = 6 * 3600 * 1000
# Anchors whose session-clock offset moves less than this are merged.
ANCHOR_TOLERANCE_MS = 250

# Legacy uploads (before part ids) are grouped per kind under this key.
LEGACY_PART_PREFIX = "legacy-"
