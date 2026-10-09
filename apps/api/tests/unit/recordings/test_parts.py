"""Recording parts: grouping, timing anchors, lifecycle and upload helpers (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.recordings.constants import FINALIZE_SETTLE, MAX_FINALIZE_ATTEMPTS
from app.services.recordings.ffmpeg import parse_probe
from app.services.recordings.parts import (
    compress_anchors,
    group_parts,
    part_closed,
    part_lock_key,
    part_status,
    part_timing,
    to_epoch_ms,
)
from app.services.recordings.segments import (
    SegmentTiming,
    base_type,
    container_ok,
    segment_key,
    uploads_open,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-0000-0000-00000000000a")
SID = uuid.UUID("00000000-0000-0000-0000-00000000000b")


def seg_row(
    index: int,
    *,
    part: str | None = "p1",
    kind: str = "camera",
    received: datetime | None = None,
    sent_offset_ms: int | None = 0,
    media_end_ms: int | None = None,
    session_ms: int | None = None,
    started: datetime | None = None,
    content_type: str = "video/webm",
    extra: dict[str, Any] | None = None,
) -> SimpleNamespace:
    received = received or NOW
    details: dict[str, Any] = {"segment": index, "kind": kind, "received_at": to_epoch_ms(received)}
    if part:
        details["part_id"] = part
    if sent_offset_ms is not None:
        details["client_sent_at"] = to_epoch_ms(received) - sent_offset_ms
    if media_end_ms is not None:
        details["media_end_ms"] = media_end_ms
    if session_ms is not None:
        details["session_ms"] = session_ms
    if started is not None:
        details["part_started_at"] = started.isoformat()
    details.update(extra or {})
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind="video_segment" if kind == "camera" else "screen_segment",
        storage_key=f"orgs/x/sessions/y/video/{part}/{index:05d}.webm",
        size_bytes=100,
        sha256=None,
        content_type=content_type,
        created_at=received,
        retention_until=received + timedelta(days=30),
        deleted_at=None,
        details=details,
    )


def final_row(
    part: str, *, version: int = 1, segment_max: int = 2, closed: bool = False, kind: str = "camera"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind="session_recording",
        storage_key=f"orgs/x/sessions/y/recordings/{kind}-{part}-v{version}.webm",
        size_bytes=300,
        sha256=None,
        content_type="video/webm",
        created_at=NOW,
        retention_until=NOW + timedelta(days=30),
        deleted_at=None,
        details={
            "part_id": part,
            "kind": kind,
            "version": version,
            "segment_max": segment_max,
            "closed": closed,
        },
    )


# ------------------------------------------------------------------ anchors


def test_compress_anchors_keeps_offset_changes_only() -> None:
    # Offset stays 1000 ms, then a 5 s pause (recording kept running, interview clock stopped).
    points = [(10_000, 11_000), (20_000, 21_000), (30_000, 31_000), (40_000, 36_000), (50_000, 46_000)]
    # Both ends of each constant-offset run survive (the pause happened between 30 s and 40 s).
    assert compress_anchors(points) == [
        (10_000, 11_000),
        (30_000, 31_000),
        (40_000, 36_000),
        (50_000, 46_000),
    ]
    steady = [(m, m + 500) for m in range(10_000, 100_001, 10_000)]
    assert compress_anchors(steady) == [(10_000, 10_500), (100_000, 100_500)]
    assert compress_anchors([]) == []
    assert compress_anchors([(5, 7), (5, 7)]) == [(5, 7)]


def test_part_timing_corrects_the_browser_clock() -> None:
    browser_start = NOW - timedelta(seconds=60)
    # The browser clock runs 3 s behind the server: server receive - browser send = 3000 + latency.
    rows = [
        seg_row(
            0,
            received=NOW - timedelta(seconds=40),
            sent_offset_ms=3_400,
            media_end_ms=20_000,
            session_ms=15_000,
            started=browser_start,
        ),
        seg_row(
            1,
            received=NOW - timedelta(seconds=30),
            sent_offset_ms=3_050,
            media_end_ms=30_000,
            session_ms=25_000,
        ),
    ]
    parts = group_parts(rows)
    timing = part_timing(parts[0].segments)
    assert timing.clock_offset_ms == 3_050  # the smallest difference has the least network delay
    assert timing.started_at == browser_start + timedelta(milliseconds=3_050)
    assert timing.duration_ms == 30_000
    assert timing.anchors == [(20_000, 15_000), (30_000, 25_000)]


def test_part_timing_without_browser_times_uses_arrival() -> None:
    rows = [seg_row(0, sent_offset_ms=None, media_end_ms=10_000)]
    timing = part_timing(group_parts(rows)[0].segments)
    assert timing.clock_offset_ms is None
    assert timing.started_at == NOW - timedelta(seconds=10)


# ------------------------------------------------------------------ grouping


def test_group_parts_by_part_id_with_legacy_and_finalized_versions() -> None:
    rows = [
        seg_row(1, part="p1"),
        seg_row(0, part="p1"),
        seg_row(0, part=None),  # an old client without part ids
        seg_row(0, part="s1", kind="screen"),
        final_row("p1", version=1, segment_max=0),
        final_row("p1", version=2, segment_max=1),
    ]
    rows[0].deleted_at = None
    parts = {p.key: p for p in group_parts(rows)}
    assert set(parts) == {"p1", "legacy-camera", "s1"}
    p1 = parts["p1"]
    assert [s.index for s in p1.segments] == [0, 1]
    assert p1.finalized is not None and p1.finalized.version == 2
    assert [r.version for r in p1.superseded] == [1]
    assert p1.covered and not p1.uncovered
    assert parts["s1"].kind == "screen" and parts["s1"].finalized is None


def test_gaps_header_and_audio_only() -> None:
    rows = [
        seg_row(0, content_type="audio/webm"),
        seg_row(2, content_type="audio/webm", extra={"audio_only": True}),
    ]
    part = group_parts(rows)[0]
    assert part.gaps == [1] and part.has_header and part.audio_only
    headless = group_parts([seg_row(3)])[0]
    assert not headless.has_header and headless.gaps == [0, 1, 2]


# ----------------------------------------------------------------- lifecycle


def test_part_closed_rules() -> None:
    old = seg_row(0, part="old", received=NOW - timedelta(minutes=3), started=NOW - timedelta(minutes=10))
    new = seg_row(0, part="new", received=NOW - timedelta(seconds=5), started=NOW - timedelta(minutes=2))
    parts = group_parts([old, new])
    by_key = {p.key: p for p in parts}
    live = {"status": "live", "ended_at": None, "now": NOW}
    # A newer part of the same kind exists and the old one is idle: the page that recorded it is gone.
    assert part_closed(by_key["old"], parts=parts, **live)
    assert not part_closed(by_key["new"], parts=parts, **live)
    assert part_closed(by_key["new"], parts=parts, requested=True, **live)
    # Session over: closed once late uploads had time to arrive.
    ended = NOW - FINALIZE_SETTLE - timedelta(seconds=1)
    assert part_closed(by_key["new"], parts=parts, status="completed", ended_at=ended, now=NOW)
    assert not part_closed(by_key["new"], parts=parts, status="completed", ended_at=NOW, now=NOW)
    # A single part of a live session that went quiet (network outage) stays open.
    lone = group_parts([seg_row(0, received=NOW - timedelta(minutes=6))])
    assert not part_closed(lone[0], parts=lone, **live)
    abandoned = group_parts([seg_row(0, received=NOW - timedelta(minutes=31))])
    assert part_closed(abandoned[0], parts=abandoned, **live)


def test_part_status() -> None:
    recording = group_parts([seg_row(0, received=NOW - timedelta(seconds=10))])[0]
    assert part_status(recording, status="live", ended_at=None, now=NOW) == "recording"
    assert part_status(recording, status="completed", ended_at=NOW, now=NOW) == "processing"
    ready = group_parts([seg_row(0), final_row("p1", segment_max=0)])[0]
    assert part_status(ready, status="completed", ended_at=NOW, now=NOW) == "ready"
    growing = group_parts(
        [
            seg_row(0, received=NOW - timedelta(minutes=2)),
            seg_row(1, received=NOW - timedelta(minutes=1)),
            final_row("p1", segment_max=0),
        ]
    )[0]
    assert part_status(growing, status="live", ended_at=None, now=NOW) == "processing"
    failure = {"error": "missing_start", "attempts": MAX_FINALIZE_ATTEMPTS}
    gave_up = group_parts(
        [seg_row(0, received=NOW - timedelta(minutes=10), extra={"finalize_failure": failure})]
    )[0]
    assert part_status(gave_up, status="completed", ended_at=NOW, now=NOW) == "failed"
    headless = group_parts([seg_row(2, received=NOW - timedelta(minutes=10))])[0]
    assert part_status(headless, status="completed", ended_at=NOW, now=NOW) == "failed"


def test_lock_key_is_stable_and_signed_64_bit() -> None:
    key = part_lock_key(SID, "p1")
    assert key == part_lock_key(str(SID), "p1") and key != part_lock_key(SID, "p2")
    assert -(2**63) <= key < 2**63


# ------------------------------------------------------------------- uploads


@pytest.mark.parametrize(
    ("declared", "kind", "expected"),
    [
        ("video/webm;codecs=vp8,opus", "camera", "video/webm"),
        ("VIDEO/MP4", "camera", "video/mp4"),
        ("audio/webm;codecs=opus", "camera", "audio/webm"),
        ("audio/mp4", "camera", "audio/mp4"),
        ("audio/webm", "screen", None),
        ("audio/ogg", "camera", None),
        (None, "camera", None),
    ],
)
def test_base_type(declared: str | None, kind: str, expected: str | None) -> None:
    assert base_type(declared, kind=kind) == expected  # type: ignore[arg-type]


def test_container_magic_and_keys() -> None:
    assert container_ok(b"\x1a\x45\xdf\xa3rest", "audio/webm")
    assert not container_ok(b"RIFF....", "video/webm")
    assert container_ok(b"\x00\x00\x00\x18ftypmp42", "video/mp4")
    part = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert segment_key(ORG, SID, kind="camera", segment=7, content_type="audio/webm", part_id=part).endswith(
        f"/video/{part}/00007.webm"
    )
    assert segment_key(ORG, SID, kind="screen", segment=3, content_type="video/mp4", part_id=None).endswith(
        "/screen/00003.mp4"
    )


def test_segment_timing_details_are_clamped() -> None:
    details = SegmentTiming(
        part_started_at="2026-10-08T11:59:00Z",
        media_start_ms=-5,
        media_end_ms=10_000,
        session_ms=9_000,
        client_sent_at=12,
        audio_only=True,
    ).details(received_at=NOW)
    assert details["part_started_at"] == "2026-10-08T11:59:00+00:00"
    assert "media_start_ms" not in details and details["media_end_ms"] == 10_000
    assert details["session_ms"] == 9_000 and "client_sent_at" not in details
    assert details["audio_only"] is True and details["received_at"] == to_epoch_ms(NOW)


def test_uploads_open_grace_after_the_session_ended() -> None:
    live = SimpleNamespace(status="live", ended_at=None, updated_at=NOW)
    assert uploads_open(live, now=NOW)
    just_ended = SimpleNamespace(status="completed", ended_at=NOW - timedelta(minutes=9), updated_at=NOW)
    assert uploads_open(just_ended, now=NOW)
    long_ago = SimpleNamespace(status="completed", ended_at=NOW - timedelta(minutes=11), updated_at=NOW)
    assert not uploads_open(long_ago, now=NOW)


def test_parse_probe() -> None:
    probe = parse_probe(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "vp8", "width": 640, "height": 480},
                {"codec_type": "audio", "codec_name": "opus"},
            ],
            "format": {"format_name": "matroska,webm", "duration": "12.345000"},
        }
    )
    assert probe.duration_ms == 12_345 and probe.has_video and probe.has_audio
    assert (probe.video_codec, probe.audio_codec, probe.width, probe.height) == ("vp8", "opus", 640, 480)
    audio = parse_probe({"streams": [{"codec_type": "audio", "duration": "3.5"}], "format": {}})
    assert audio.duration_ms == 3_500 and not audio.has_video
