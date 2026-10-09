"""Server-side face presence: a deterministic face detector run on every
received camera frame, and the per-session face tracker that sets aside
motionless faces (photos, posters, screens).

The detector is OpenCV's YuNet (``face_detection_yunet_2023mar.onnx`` from
opencv_zoo, MIT licence, fetched with a pinned SHA-256 by
``scripts/download_assets.sh face``). It answers one objective question per
frame - how many faces are visible and where - in ~10 ms on CPU. Only the
geometry is kept: boxes normalised to the frame and the detector score. No
landmarks, crops, embeddings, identity or attributes are stored or derived.

Static faces: a face whose box has not moved (beyond a pixel of detector
jitter) for ``STATIC_SECONDS`` across at least ``STATIC_MIN_FRAMES`` frames,
and was never seen moving, is a picture rather than a person and is excluded
from the counted faces. The browser's on-device check runs at ~4 fps and may
report faces it already found motionless (``client_static``); such hints are
honoured only for faces the server has not seen move.

Everything here is CPU-bound and synchronous: call it from a worker thread.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.ai import DetectedFace, FaceCheckObservation

log = get_logger(__name__)

DETECTOR_NAME = "yunet-2023mar"
NMS_THRESHOLD = 0.3
TOP_K = 50
MAX_FACES = 20

# Static-face rule (server side, sparse frames).
STATIC_SECONDS = 30.0
STATIC_MIN_FRAMES = 3
TRACK_MATCH_IOU = 0.3
TRACK_TTL_SECONDS = 45.0
CLIENT_HINT_IOU = 0.4
# A box counts as unchanged while it stays within this much of its anchor:
# max(STILL_PIXELS of the frame, STILL_FRACTION of the face size).
STILL_PIXELS = 2.0
STILL_FRACTION = 0.02
MAX_TRACKS = 12

Box = tuple[float, float, float, float]  # x, y, w, h (fractions of the frame)

FACE_CHECK_REASONS = ("no_face", "multiple_faces")
MAX_HINT_BOXES = 8
MAX_HINT_BYTES = 2048
_EPISODE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")


class FaceDetectorUnavailable(RuntimeError):
    """OpenCV or the model file is missing (the face check then relies on the
    browser and the vision model only)."""


@dataclass(frozen=True, slots=True)
class RawFace:
    box: Box
    score: float


class FaceStatus(NamedTuple):
    """Per-face result of the tracker: ``static`` (a picture, not counted) and
    ``live`` (seen moving between frames)."""

    static: bool
    live: bool


class YuNetDetector:
    """Thread-safe wrapper: one ``cv2.FaceDetectorYN`` per thread (the OpenCV
    object keeps per-call state and is not safe to share)."""

    def __init__(self, model_path: str | Path, *, score_threshold: float = 0.7, threads: int = 2) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - dependency is in uv.lock
            raise FaceDetectorUnavailable("opencv-python-headless is not installed") from exc
        path = Path(model_path)
        if not path.is_file():
            raise FaceDetectorUnavailable(f"face detector model not found: {path}")
        self._cv2 = cv2
        self.model_path = str(path)
        self.score_threshold = float(score_threshold)
        self._local = threading.local()
        try:
            cv2.setNumThreads(max(1, int(threads)))
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:  # noqa: BLE001,S110 - logging/threads are best effort across OpenCV versions
            pass
        self._instance(320, 240)  # fail fast on a broken model file

    def _instance(self, width: int, height: int) -> Any:
        detector = getattr(self._local, "detector", None)
        if detector is None:
            detector = self._cv2.FaceDetectorYN.create(
                self.model_path, "", (width, height), self.score_threshold, NMS_THRESHOLD, TOP_K
            )
            self._local.detector = detector
            self._local.size = (width, height)
        elif self._local.size != (width, height):
            detector.setInputSize((width, height))
            self._local.size = (width, height)
        return detector

    def detect(self, bgr: Any) -> list[RawFace]:
        """Faces in a BGR ``uint8`` array (H x W x 3), best first."""
        height, width = int(bgr.shape[0]), int(bgr.shape[1])
        if width < 16 or height < 16:
            return []
        _, rows = self._instance(width, height).detect(bgr)
        if rows is None:
            return []
        faces: list[RawFace] = []
        for row in rows[:MAX_FACES]:
            x, y, w, h = (float(v) for v in row[:4])
            score = float(row[14])
            if not all(math.isfinite(v) for v in (x, y, w, h, score)) or w <= 0 or h <= 0:
                continue
            # YuNet may return boxes that extend past the frame edge (partly visible faces).
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(width), x + w), min(float(height), y + h)
            if x1 <= x0 or y1 <= y0:
                continue
            faces.append(
                RawFace(
                    box=(x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height),
                    score=min(1.0, max(0.0, score)),
                )
            )
        faces.sort(key=lambda f: f.score, reverse=True)
        return faces


_detector: YuNetDetector | None = None
_detector_failed = False
_detector_lock = threading.Lock()


def get_face_detector() -> YuNetDetector | None:
    """Process-wide detector (lazy); ``None`` when OpenCV or the model is missing
    (logged once - frames are still analysed without it)."""
    global _detector, _detector_failed
    if _detector is not None or _detector_failed:
        return _detector
    with _detector_lock:
        if _detector is None and not _detector_failed:
            settings = get_settings()
            try:
                _detector = YuNetDetector(
                    settings.face_detector_model_path,
                    score_threshold=settings.face_detector_score_threshold,
                    threads=settings.face_detector_threads,
                )
            except (FaceDetectorUnavailable, Exception) as exc:  # noqa: BLE001 - never break frame intake
                _detector_failed = True
                log.warning("integrity.face_detector_unavailable", error=str(exc)[:200])
    return _detector


def reset_face_detector(detector: YuNetDetector | None = None) -> None:
    """Tests: replace (or forget) the process-wide detector."""
    global _detector, _detector_failed
    with _detector_lock:
        _detector = detector
        _detector_failed = False


def pil_to_bgr(image: Any) -> Any:
    """PIL RGB image -> contiguous BGR ``uint8`` array for OpenCV."""
    import numpy as np

    rgb = np.asarray(image.convert("RGB") if image.mode != "RGB" else image, dtype=np.uint8)
    return np.ascontiguousarray(rgb[:, :, ::-1])


@dataclass(frozen=True, slots=True)
class FaceCheckHint:
    """What the browser's on-device check says about the frame it uploads
    (``face_check`` form field). Untrusted: used only to link the snapshot to
    the interval it belongs to and as a hint about motionless faces."""

    reason: str | None = None  # no_face | multiple_faces (only on face_check snapshots)
    episode: str | None = None
    duration_ms: int | None = None
    started_at: str | None = None
    faces: int | None = None
    static_boxes: tuple[Box, ...] = ()


def parse_face_check_hint(raw: str | Mapping[str, Any] | None) -> FaceCheckHint | None:
    """Parse and bound the ``face_check`` JSON of a frame upload; invalid
    parts are dropped, anything unusable gives ``None``."""
    if raw is None or raw == "":
        return None
    data: Any = raw
    if isinstance(raw, str):
        if len(raw) > MAX_HINT_BYTES:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(data, Mapping):
        return None
    reason = data.get("reason") if data.get("reason") in FACE_CHECK_REASONS else None
    episode = data.get("episode") if isinstance(data.get("episode"), str) else None
    if episode is not None and not _EPISODE.match(episode):
        episode = None
    duration = data.get("duration_ms")
    duration_ms = (
        int(min(max(duration, 0), 6 * 3600 * 1000))
        if isinstance(duration, int | float) and not isinstance(duration, bool) and math.isfinite(duration)
        else None
    )
    started = data.get("started_at") if isinstance(data.get("started_at"), str) else None
    faces = data.get("faces")
    faces_n = (
        int(faces) if isinstance(faces, int) and not isinstance(faces, bool) and 0 <= faces <= 50 else None
    )
    boxes: list[Box] = []
    for box in data.get("static_boxes") or []:
        if not isinstance(box, list | tuple) or len(box) != 4:
            continue
        try:
            values = tuple(float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(v) and -0.5 <= v <= 1.5 for v in values) and values[2] > 0 and values[3] > 0:
            boxes.append(values)  # type: ignore[arg-type]
        if len(boxes) >= MAX_HINT_BOXES:
            break
    return FaceCheckHint(
        reason=reason,
        episode=episode,
        duration_ms=duration_ms,
        started_at=started[:64] if started else None,
        faces=faces_n,
        static_boxes=tuple(boxes),
    )


# --------------------------------------------------------------------- tracking


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass(slots=True)
class FaceTrack:
    id: int
    anchor: Box  # box when the face last moved
    box: Box
    first: float
    still_since: float
    last: float
    frames: int = 1
    moved: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["anchor"] = [round(v, 5) for v in self.anchor]
        data["box"] = [round(v, 5) for v in self.box]
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FaceTrack | None:
        try:
            anchor = tuple(float(v) for v in raw["anchor"])
            box = tuple(float(v) for v in raw["box"])
            if len(anchor) != 4 or len(box) != 4:
                return None
            return cls(
                id=int(raw["id"]),
                anchor=anchor,  # type: ignore[arg-type]
                box=box,  # type: ignore[arg-type]
                first=float(raw["first"]),
                still_since=float(raw["still_since"]),
                last=float(raw["last"]),
                frames=int(raw.get("frames", 1)),
                moved=bool(raw.get("moved", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(slots=True)
class FaceTrackerState:
    tracks: list[FaceTrack] = field(default_factory=list)
    next_id: int = 1
    last_t: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"tracks": [t.to_dict() for t in self.tracks], "next_id": self.next_id, "last_t": self.last_t}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> FaceTrackerState:
        if not data:
            return cls()
        tracks = [t for t in (FaceTrack.from_dict(r) for r in data.get("tracks") or []) if t is not None]
        try:
            next_id = int(data.get("next_id", len(tracks) + 1))
        except (TypeError, ValueError):
            next_id = len(tracks) + 1
        last_t = data.get("last_t")
        return cls(
            tracks=tracks[:MAX_TRACKS],
            next_id=max(next_id, 1),
            last_t=float(last_t) if isinstance(last_t, int | float) else None,
        )


def _unchanged(anchor: Box, box: Box, frame_width_px: int) -> bool:
    pixel = STILL_PIXELS / max(frame_width_px, 1)
    tolerance = max(pixel, STILL_FRACTION * max(anchor[2], anchor[3]))
    acx, acy = anchor[0] + anchor[2] / 2, anchor[1] + anchor[3] / 2
    bcx, bcy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return (
        abs(acx - bcx) <= tolerance
        and abs(acy - bcy) <= tolerance
        and abs(anchor[2] - box[2]) <= tolerance
        and abs(anchor[3] - box[3]) <= tolerance
    )


def update_tracks(
    state: FaceTrackerState,
    faces: Sequence[RawFace],
    t: float,
    *,
    frame_width_px: int,
    client_static: Sequence[Sequence[float]] = (),
) -> list[FaceStatus]:
    """Match ``faces`` to the session's tracks and return their status.

    Frames arriving out of order (older than the last one) do not update the
    tracks; their faces are judged against the current tracks only."""
    in_order = state.last_t is None or t >= state.last_t - 0.001
    pairs = sorted(
        (
            (iou(face.box, track.box), fi, ti)
            for fi, face in enumerate(faces)
            for ti, track in enumerate(state.tracks)
        ),
        reverse=True,
    )
    matched_face: dict[int, int] = {}
    used_tracks: set[int] = set()
    for overlap, fi, ti in pairs:
        if overlap < TRACK_MATCH_IOU:
            break
        if fi in matched_face or ti in used_tracks:
            continue
        matched_face[fi] = ti
        used_tracks.add(ti)

    hints = [tuple(float(v) for v in box[:4]) for box in client_static if len(box) >= 4]
    flags: list[FaceStatus] = []
    for fi, face in enumerate(faces):
        ti = matched_face.get(fi)
        track = state.tracks[ti] if ti is not None else None
        if track is not None and in_order:
            if _unchanged(track.anchor, face.box, frame_width_px):
                track.box = face.box
            else:
                track.anchor = face.box
                track.box = face.box
                track.still_since = t
                track.moved = True
            track.last = t
            track.frames += 1
        elif track is None and in_order:
            track = FaceTrack(
                id=state.next_id, anchor=face.box, box=face.box, first=t, still_since=t, last=t, frames=1
            )
            state.next_id += 1
            state.tracks.append(track)
        static = bool(
            track is not None
            and not track.moved
            and track.frames >= STATIC_MIN_FRAMES
            and t - track.still_since >= STATIC_SECONDS
        )
        if not static and hints and (track is None or not track.moved):
            static = any(iou(face.box, hint) >= CLIENT_HINT_IOU for hint in hints)  # type: ignore[arg-type]
        flags.append(FaceStatus(static=static, live=bool(track is not None and track.moved and not static)))

    if in_order:
        state.last_t = t
        state.tracks = [tr for tr in state.tracks if t - tr.last <= TRACK_TTL_SECONDS]
        if len(state.tracks) > MAX_TRACKS:
            state.tracks = sorted(state.tracks, key=lambda tr: tr.last, reverse=True)[:MAX_TRACKS]
    return flags


def build_observation(
    faces: Sequence[RawFace], statuses: Sequence[FaceStatus], *, latency_ms: float
) -> FaceCheckObservation:
    unknown = FaceStatus(static=False, live=False)
    detected = []
    for i, f in enumerate(faces[:MAX_FACES]):
        status = statuses[i] if i < len(statuses) else unknown
        detected.append(
            DetectedFace(
                box=[round(f.box[0], 4), round(f.box[1], 4), round(f.box[2], 4), round(f.box[3], 4)],
                score=round(f.score, 3),
                static=status.static,
                moving=status.live,
            )
        )
    static_count = sum(1 for d in detected if d.static)
    return FaceCheckObservation(
        detector=DETECTOR_NAME,
        face_count=len(detected),
        counted_faces=len(detected) - static_count,
        static_faces=static_count,
        live_faces=sum(1 for d in detected if d.moving),
        faces=detected,
        latency_ms=round(latency_ms, 2),
    )


def detect_faces(image: Any, detector: YuNetDetector | None = None) -> tuple[list[RawFace], float] | None:
    """Run the detector on a PIL image; returns ``(faces, latency_ms)`` or
    ``None`` when no detector is available or it failed (logged)."""
    detector = detector or get_face_detector()
    if detector is None:
        return None
    started = time.perf_counter()
    try:
        faces = detector.detect(pil_to_bgr(image))
    except Exception as exc:  # noqa: BLE001 - a detector failure must not reject the frame
        log.warning("integrity.face_detect_failed", error=type(exc).__name__)
        return None
    return faces, (time.perf_counter() - started) * 1000.0


def face_summary(observation: FaceCheckObservation | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Compact JSON for event details / frame metadata."""
    if observation is None:
        return None
    if isinstance(observation, FaceCheckObservation):
        return observation.model_dump(mode="json")
    try:
        return FaceCheckObservation.model_validate(observation).model_dump(mode="json")
    except ValueError:
        return None


__all__ = [
    "DETECTOR_NAME",
    "FACE_CHECK_REASONS",
    "STATIC_SECONDS",
    "FaceCheckHint",
    "FaceDetectorUnavailable",
    "FaceStatus",
    "FaceTrack",
    "FaceTrackerState",
    "RawFace",
    "YuNetDetector",
    "build_observation",
    "detect_faces",
    "face_summary",
    "get_face_detector",
    "iou",
    "parse_face_check_hint",
    "pil_to_bgr",
    "reset_face_detector",
    "update_tracks",
]
