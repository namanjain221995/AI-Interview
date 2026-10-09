"""Server-side face detector (OpenCV YuNet on rendered CC0 fixtures), the
static-face tracker and the face-check hint parser."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.services.integrity.faces import (
    STATIC_SECONDS,
    FaceStatus,
    FaceTrackerState,
    RawFace,
    YuNetDetector,
    build_observation,
    detect_faces,
    iou,
    parse_face_check_hint,
    update_tracks,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "faces"
MODEL = Path(get_settings().face_detector_model_path)

needs_model = pytest.mark.skipif(not MODEL.is_file(), reason="run scripts/download_assets.sh face")


@pytest.fixture(scope="module")
def detector() -> YuNetDetector:
    if not MODEL.is_file():
        pytest.skip("face detector model missing (scripts/download_assets.sh face)")
    return YuNetDetector(MODEL, score_threshold=0.7, threads=2)


def faces_in(detector: YuNetDetector, name: str) -> list[RawFace]:
    result = detect_faces(Image.open(FIXTURES / f"{name}.jpg"), detector)
    assert result is not None
    faces, latency_ms = result
    assert latency_ms >= 0
    return faces


@needs_model
@pytest.mark.parametrize(
    ("name", "count"),
    [
        ("one_front", 1),
        ("one_front_dark", 1),
        ("one_low_light_dark", 1),
        ("one_yaw30", 1),
        ("one_edge", 1),
        ("two_side_by_side", 2),
        ("two_mixed_tones", 2),
        ("none_empty_room", 0),
        ("none_back_turned", 0),
        ("poster_only", 1),
        ("poster_seq_0", 2),
    ],
)
def test_detector_counts_faces_on_rendered_fixtures(detector: YuNetDetector, name: str, count: int) -> None:
    faces = faces_in(detector, name)
    assert len(faces) == count
    for face in faces:
        x, y, w, h = face.box
        assert 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
        assert x + w <= 1.0001 and y + h <= 1.0001  # clipped to the frame
        assert 0.7 <= face.score <= 1


@needs_model
def test_partly_visible_face_box_is_clipped_to_the_frame(detector: YuNetDetector) -> None:
    [face] = faces_in(detector, "one_edge")
    assert face.box[0] + face.box[2] == pytest.approx(1.0, abs=0.01)


@needs_model
def test_poster_face_becomes_static_while_the_live_face_never_does(detector: YuNetDetector) -> None:
    state = FaceTrackerState()
    timeline: list[tuple[int, int]] = []
    for index, t in enumerate((0, 15, 30, 45)):
        image = Image.open(FIXTURES / f"poster_seq_{index}.jpg")
        result = detect_faces(image, detector)
        assert result is not None
        faces, latency = result
        flags = update_tracks(state, faces, 1000.0 + t, frame_width_px=image.width)
        observation = build_observation(faces, flags, latency_ms=latency)
        timeline.append((observation.counted_faces, observation.static_faces))
    # Two faces counted until the print has been motionless for 30 s over 3 frames.
    assert timeline == [(2, 0), (2, 0), (1, 1), (1, 1)]
    assert observation.live_faces == 1 and [f.moving for f in observation.faces].count(True) == 1
    poster = next(t for t in state.tracks if not t.moved)
    assert poster.frames == 4 and poster.still_since == 1000.0


def face(x: float, y: float = 0.3, w: float = 0.12, h: float = 0.2, score: float = 0.9) -> RawFace:
    return RawFace(box=(x, y, w, h), score=score)


UNKNOWN = FaceStatus(static=False, live=False)
STATIC = FaceStatus(static=True, live=False)
LIVE = FaceStatus(static=False, live=True)


def test_tracker_marks_a_never_moving_face_static_only_after_the_window() -> None:
    state = FaceTrackerState()
    assert update_tracks(state, [face(0.6)], 0.0, frame_width_px=640) == [UNKNOWN]
    assert update_tracks(state, [face(0.6)], 15.0, frame_width_px=640) == [UNKNOWN]
    assert update_tracks(state, [face(0.6)], STATIC_SECONDS - 1, frame_width_px=640) == [UNKNOWN]
    assert update_tracks(state, [face(0.6)], STATIC_SECONDS, frame_width_px=640) == [STATIC]


def test_a_face_that_moved_once_is_live_and_never_static() -> None:
    state = FaceTrackerState()
    assert update_tracks(state, [face(0.30)], 0.0, frame_width_px=640) == [UNKNOWN]
    assert update_tracks(state, [face(0.33)], 10.0, frame_width_px=640) == [LIVE]  # moved ~19 px
    for t in (20.0, 40.0, 60.0, 80.0):
        assert update_tracks(state, [face(0.33)], t, frame_width_px=640) == [LIVE]
    assert state.tracks[0].moved


def test_detector_jitter_of_a_pixel_does_not_count_as_movement() -> None:
    state = FaceTrackerState()
    jitter = (0.6, 0.6 + 1 / 640, 0.6, 0.6 - 1 / 640)
    flags = [
        update_tracks(state, [face(x)], t, frame_width_px=640)[0].static
        for t, x in zip((0, 15, 30, 45), jitter)
    ]
    assert flags == [False, False, True, True]


def test_client_static_hint_only_applies_to_faces_not_seen_moving() -> None:
    state = FaceTrackerState()
    hint = [(0.6, 0.3, 0.12, 0.2)]
    assert update_tracks(state, [face(0.6)], 0.0, frame_width_px=640, client_static=hint) == [STATIC]
    # The live face moved between frames: a hint over it is ignored.
    update_tracks(state, [face(0.2)], 0.0, frame_width_px=640)
    update_tracks(state, [face(0.25)], 5.0, frame_width_px=640)
    moving = next(t for t in state.tracks if t.moved)
    assert update_tracks(
        state, [face(moving.box[0])], 6.0, frame_width_px=640, client_static=[moving.box]
    ) == [LIVE]


def test_out_of_order_frames_do_not_update_tracks_and_stale_tracks_expire() -> None:
    state = FaceTrackerState()
    update_tracks(state, [face(0.5)], 100.0, frame_width_px=640)
    update_tracks(state, [face(0.2)], 90.0, frame_width_px=640)  # older frame
    assert len(state.tracks) == 1 and state.last_t == 100.0
    update_tracks(state, [], 200.0, frame_width_px=640)
    assert state.tracks == []


def test_tracker_state_round_trips_and_tolerates_garbage() -> None:
    state = FaceTrackerState()
    update_tracks(state, [face(0.5), face(0.1)], 1.0, frame_width_px=640)
    restored = FaceTrackerState.from_dict(state.to_dict())
    assert len(restored.tracks) == 2 and restored.next_id == 3 and restored.last_t == 1.0
    assert FaceTrackerState.from_dict(None).tracks == []
    assert FaceTrackerState.from_dict({"tracks": [{"id": 1}], "next_id": "x"}).tracks == []


def test_build_observation_excludes_static_faces_from_the_count() -> None:
    obs = build_observation([face(0.1, score=0.95), face(0.6, score=0.8)], [LIVE, STATIC], latency_ms=9.876)
    assert (obs.face_count, obs.counted_faces, obs.static_faces, obs.live_faces) == (2, 1, 1, 1)
    assert obs.faces[1].static and obs.faces[0].moving and obs.latency_ms == 9.88
    assert obs.detector == "yunet-2023mar"
    dumped = obs.model_dump(mode="json")
    assert set(dumped["faces"][0]) == {"box", "score", "static", "moving"}  # geometry only, no landmarks
    assert build_observation([face(0.1)], [], latency_ms=1).faces[0].moving is False


def test_iou() -> None:
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == pytest.approx(1.0)
    assert iou((0, 0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)) == 0.0
    assert iou((0, 0, 0.5, 1), (0.25, 0, 0.5, 1)) == pytest.approx(1 / 3)


def test_face_check_hint_is_validated_and_bounded() -> None:
    hint = parse_face_check_hint(
        '{"reason": "multiple_faces", "episode": "ep_123abc", "duration_ms": 3400.7, '
        '"started_at": "2026-10-08T10:00:00Z", "faces": 2, '
        '"static_boxes": [[0.6, 0.3, 0.1, 0.2], [1, 2], ["a", 0, 0, 0], [0.1, 0.1, 0, 0.2]]}'
    )
    assert hint is not None
    assert (hint.reason, hint.episode, hint.duration_ms, hint.faces) == (
        "multiple_faces",
        "ep_123abc",
        3400,
        2,
    )
    assert hint.static_boxes == ((0.6, 0.3, 0.1, 0.2),)
    bad = parse_face_check_hint({"reason": "smile", "episode": "x", "duration_ms": True, "faces": 99})
    assert bad is not None and bad.reason is None and bad.episode is None and bad.duration_ms is None
    assert bad.faces is None
    assert parse_face_check_hint("not json") is None
    assert parse_face_check_hint("[1, 2]") is None
    assert parse_face_check_hint("x" * 5000) is None
    assert parse_face_check_hint(None) is None
    many = parse_face_check_hint({"static_boxes": [[0.1, 0.1, 0.1, 0.1]] * 30})
    assert many is not None and len(many.static_boxes) == 8


def test_detect_faces_without_a_detector_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.integrity.faces as faces_module

    monkeypatch.setattr(faces_module, "get_face_detector", lambda: None)
    assert detect_faces(Image.new("RGB", (64, 48))) is None
