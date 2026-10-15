"""``IntegrityService`` implementation.

Integrity signals are observations for a human reviewer, never verdicts.
This service normalises, rate-limits, evaluates (``rules.decide``), aggregates
and persists them (``integrity_events``), counts them
(``interview_integrity_events_total{type,source}``) and announces them on the
session bus (``session:{id}:bus``)::

    {"type": "integrity.event", "event_id", "event_type", "source", "severity",
     "action", "confidence", "count", "aggregated", "occurred_at",
     "elapsed_seconds", "evidence_media_id", "delivered_to_caller"}
    {"type": "integrity.warning", "event_id", "event_type", "message"}

Delivery of candidate reminders and policy actions: ``record_client_events``
and ``record_server_event`` *return* outcomes to their caller (the realtime
runtime sends ``integrity.warning`` / pauses / terminates from those), so they
only publish ``integrity.warning`` when asked (``publish_warnings=True``, e.g.
the REST fallback of the portal). Frame analysis runs in the worker, where
nobody can deliver an outcome, so its warnings are always published.
``integrity.event`` carries ``delivered_to_caller`` so bus listeners can skip
outcomes that were already handled.

Face presence: every camera frame is checked by the deterministic face
detector (:mod:`app.services.integrity.faces`) when it is received; motionless
faces are tracked per session. A ``face_check`` snapshot (sent by the browser
when its on-device check starts a ``face_not_visible`` / ``multiple_faces_visible``
interval) is confirmed right away, in the request, and linked to that interval;
periodic frames feed the result to the vision state machine.

Concurrency: every evaluation of one session runs inside a transaction that
holds a Postgres advisory lock for that session, so aggregation and the
Redis-backed windows/vision state are consistent across API, realtime and
worker processes.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIOutputInvalid, AITimeout, AIUnavailable, CallContext
from app.ai.registry import get_ai
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, RateLimited, ValidationFailed
from app.core.logging import get_logger
from app.core.metrics import INTEGRITY_EVENTS
from app.core.queue import enqueue, publish_session_event
from app.core.ratelimit import hit
from app.db.models import Consent, IntegrityEvent, Interview, InterviewSession, MediaAsset
from app.db.session import session_scope
from app.domain.enums import (
    IntegrityAction,
    IntegrityEventType,
    IntegrityReviewStatus,
    IntegritySource,
    MediaKind,
    ModelTask,
    SessionStatus,
)
from app.schemas.ai import FaceCheckObservation, FrameObservation, ScreenObservation
from app.schemas.policies import IntegrityPolicy, RetentionPolicy
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity.contracts import IntegrityOutcome
from app.services.integrity.faces import (
    DETECTOR_NAME,
    FaceCheckHint,
    FaceTrackerState,
    RawFace,
    build_observation,
    detect_faces,
    parse_face_check_hint,
    update_tracks,
)
from app.services.integrity.frames import PreparedFrame, prepare_frame_image, validate_frame_bytes
from app.services.integrity.normalize import (
    clamp_time,
    client_signal,
    derived_large_paste,
    is_large_coding_paste,
    parse_client_time,
    sanitize_details,
)
from app.services.integrity.policy import ResolvedPolicy, resolve_policy
from app.services.integrity.queries import NOTICE
from app.services.integrity.rules import (
    ACTION_RANK,
    CLIENT_EVENT_TYPES,
    FACE_CHECK_TYPES,
    FACE_INTERVAL_TYPES,
    FOCUS_SWITCH_TYPES,
    LOG_ONLY_AGGREGATE_SECONDS,
    SERVER_EVENT_TYPES,
    SEVERITY_RANK,
    STRONG_SIGNAL_TYPES,
    Decision,
    EffectiveRule,
    Signal,
    decide,
    label_for,
    max_severity,
    qualification,
)
from app.services.integrity.store import IntegrityStore
from app.services.integrity.vision import (
    CAMERA_PROMPT,
    CAMERA_USER_TEXT,
    SCREEN_PROMPT,
    SCREEN_USER_TEXT,
    CameraVisionState,
    advance_camera_state,
    screen_signals,
    vision_messages,
)
from app.services.storage import get_storage
from app.services.storage.base import ObjectNotFound
from app.services.storage.media import delete_media, store_media

log = get_logger(__name__)

T = IntegrityEventType
A = IntegrityAction

ANALYZE_FRAME_JOB = "integrity.analyze_frame"
SIGNALS_PER_MINUTE = 120  # per session, all types (excess signals are dropped)
WARNING_COOLDOWN_SECONDS = 60
TRANSIENT_FRAME_TTL_SECONDS = 600
MAX_FRAME_AGE_SECONDS = 180  # older frames are skipped so analysis keeps up with live sessions
# Focus losses shorter than this never count toward rapid switching (blips); longer ones do (S22 and its review).
RAPID_SWITCH_MIN_MS = 1000
LATE_EVENT_GRACE = timedelta(minutes=10)
VISION_MAX_TOKENS = 320
TERMINAL_STATUSES = frozenset(
    {SessionStatus.COMPLETED.value, SessionStatus.ABORTED.value, SessionStatus.FAILED.value}
)
FRAME_KINDS: dict[str, MediaKind] = {"camera": MediaKind.CAMERA_FRAME, "screen": MediaKind.SCREEN_FRAME}
FRAME_TRIGGERS = frozenset({"periodic", "face_check"})
FACE_CHECK_FRAMES_PER_MINUTE = 6
FACE_CHECK_EVENT_TYPES: dict[str, IntegrityEventType] = {
    "no_face": T.FACE_NOT_VISIBLE,
    "multiple_faces": T.MULTIPLE_FACES_VISIBLE,
}
MAX_EPISODES_PER_EVENT = 60
MAX_RUNS_PER_SUMMARY = 20
# Counters of a face_check_summary run (cumulative per page run; summed over runs).
SUMMARY_COUNTERS = (
    "monitored_ms",
    "one_face_ms",
    "no_face_ms",
    "multi_face_ms",
    "static_faces",
    "frames",
)
CHECK_RANK = {"confirmed": 3, "not_confirmed": 2, "unavailable": 1}

FrameKind = Literal["camera", "screen"]


def _prepare_and_detect(
    image: bytes, content_type: str, max_bytes: int, detect: bool
) -> tuple[PreparedFrame, tuple[list[RawFace], float] | None]:
    """Thread body: validate + re-encode the frame, then run the face detector on
    the same decoded pixels (camera frames only)."""
    prepared, pixels = prepare_frame_image(image, content_type, max_bytes=max_bytes)
    return prepared, (detect_faces(pixels) if detect else None)


def faces_from_meta(meta: Mapping[str, Any]) -> FaceCheckObservation | None:
    raw = meta.get("faces")
    if not isinstance(raw, Mapping):
        return None
    try:
        return FaceCheckObservation.model_validate(raw)
    except ValueError:
        return None


def _uuid(value: uuid.UUID | str, what: str = "session_id") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise NotFound(f"Unknown {what}.") from None


def _lock_key(session_id: uuid.UUID) -> int:
    digest = hashlib.sha256(b"integrity-session:" + session_id.bytes).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class SessionContext:
    session_id: uuid.UUID
    organization_id: uuid.UUID
    interview_id: uuid.UUID
    candidate_id: uuid.UUID
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    reconnect_count: int
    resolved: ResolvedPolicy

    @property
    def policy(self) -> IntegrityPolicy:
        return self.resolved.integrity

    @property
    def retention(self) -> RetentionPolicy:
        return self.resolved.snapshot.retention

    @property
    def rules(self) -> Mapping[IntegrityEventType, EffectiveRule]:
        return self.resolved.rules

    def accepts_signals(self, now: datetime) -> bool:
        if self.status not in TERMINAL_STATUSES:
            return True
        return self.ended_at is not None and now - self.ended_at <= LATE_EVENT_GRACE

    def elapsed_seconds(self, at: datetime) -> float | None:
        if self.started_at is None:
            return None
        return max(0.0, (at - self.started_at).total_seconds())


@dataclass(slots=True)
class _Applied:
    outcome: IntegrityOutcome
    event_id: uuid.UUID
    source: IntegritySource
    confidence: float | None
    count: int
    aggregated: bool
    occurred_at: datetime
    elapsed_seconds: float | None
    evidence_media_id: uuid.UUID | None


SignalProducer = Callable[[AsyncSession, SessionContext], Awaitable[list[Signal]]]


class DefaultIntegrityService:
    """Production implementation of :class:`~app.services.integrity.contracts.IntegrityService`."""

    def __init__(
        self, store: IntegrityStore | None = None, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.store = store or IntegrityStore()
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self._clock()

    # ================================================================ context
    async def load_context(self, db: AsyncSession, session_id: uuid.UUID) -> SessionContext:
        row = (
            await db.execute(
                select(InterviewSession, Interview.policy_snapshot)
                .join(Interview, Interview.id == InterviewSession.interview_id)
                .where(InterviewSession.id == session_id)
            )
        ).first()
        if row is None:
            raise NotFound("Interview session not found.")
        session, snapshot = row
        return SessionContext(
            session_id=session.id,
            organization_id=session.organization_id,
            interview_id=session.interview_id,
            candidate_id=session.candidate_id,
            status=session.status,
            started_at=session.started_at,
            ended_at=session.ended_at,
            reconnect_count=int(session.reconnect_count or 0),
            resolved=resolve_policy(snapshot),
        )

    # ========================================================== client events
    async def record_client_events(
        self, session_id: str, events: list[ClientIntegrityEvent], *, publish_warnings: bool = False
    ) -> list[IntegrityOutcome]:
        """Normalise, rate-limit, aggregate and persist browser signals.

        Vision, screen-analysis and server-only types are rejected (a browser
        cannot claim them). Returns one outcome per recorded signal (including
        derived ``rapid_focus_switching`` / ``large_paste_in_coding``)."""
        sid = _uuid(session_id)
        if not events:
            return []
        now = self.now()

        async def produce(db: AsyncSession, ctx: SessionContext) -> list[Signal]:
            if not ctx.accepts_signals(now):
                log.info("integrity.events_after_end_dropped", session_id=str(sid), count=len(events))
                return []
            signals: list[Signal] = []
            explicit_large_paste = any(e.event_type == T.LARGE_PASTE_IN_CODING for e in events)
            face_consented: bool | None = None
            for event in events[:200]:
                event_type = IntegrityEventType(event.event_type)
                if event_type not in CLIENT_EVENT_TYPES:
                    log.warning(
                        "integrity.client_type_rejected", session_id=str(sid), event_type=event_type.value
                    )
                    continue
                if event_type in FACE_CHECK_TYPES:
                    # The on-device face check runs only under a consent that described it.
                    if face_consented is None:
                        face_consented = await self._face_check_consented(db, ctx)
                    if not face_consented:
                        log.info(
                            "integrity.face_check_not_consented",
                            session_id=str(sid),
                            event_type=event_type.value,
                        )
                        continue
                signal = client_signal(event, now=now)
                if event_type in FACE_INTERVAL_TYPES and signal.episode:
                    check = await self.store.get_face_check(sid, signal.episode)
                    if check:
                        signal.details["server_check"] = check
                signals.append(signal)
                if (
                    event_type is T.PASTE_EVENT
                    and not explicit_large_paste
                    and is_large_coding_paste(signal.details)
                ):
                    signals.append(derived_large_paste(signal))
            signals.sort(key=lambda s: s.occurred_at)
            return signals

        return await self._process(sid, produce, delivered_to_caller=True, publish_warnings=publish_warnings)

    # ========================================================== server events
    async def record_server_event(
        self,
        session_id: str,
        event_type: IntegrityEventType,
        *,
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        publish_warnings: bool = False,
    ) -> IntegrityOutcome | None:
        """Server-observed signals (disconnect, reconnect, page_reload, ...).

        Derives ``unusual_disconnect_pattern`` (too many disconnects inside the
        rule window, or reconnects beyond ``max_reconnects``). Returns the most
        significant outcome, or ``None`` when nothing was recorded."""
        sid = _uuid(session_id)
        event_type = IntegrityEventType(event_type)
        if event_type not in SERVER_EVENT_TYPES:
            raise ValueError(f"{event_type.value} cannot be recorded as a server event")
        now = self.now()
        at, _ = clamp_time(occurred_at, now=now) if occurred_at else (now, False)
        source = IntegritySource.SERVER

        async def produce(db: AsyncSession, ctx: SessionContext) -> list[Signal]:
            if not ctx.accepts_signals(now):
                return []
            signal = Signal(
                event_type=event_type, source=source, occurred_at=at, details=sanitize_details(details)
            )
            signals = [signal]
            pattern = ctx.rules.get(T.UNUSUAL_DISCONNECT_PATTERN)
            if pattern is not None and pattern.enabled:
                if event_type is T.DISCONNECT:
                    count = await self.store.record_occurrence(
                        sid, "disconnects", at.timestamp(), pattern.window_seconds
                    )
                    if count >= pattern.min_occurrences:
                        signals.append(
                            Signal(
                                event_type=T.UNUSUAL_DISCONNECT_PATTERN,
                                source=IntegritySource.SERVER,
                                occurred_at=at,
                                occurrences_satisfied=True,
                                details={
                                    "reason": "frequent_disconnects",
                                    "disconnects_in_window": count,
                                    "window_seconds": pattern.window_seconds,
                                },
                            )
                        )
                elif event_type is T.RECONNECT:
                    own = await self.store.incr_reconnects(sid)
                    reconnects = max(own, ctx.reconnect_count)
                    if reconnects > ctx.policy.max_reconnects:
                        signals.append(
                            Signal(
                                event_type=T.UNUSUAL_DISCONNECT_PATTERN,
                                source=IntegritySource.SERVER,
                                occurred_at=at,
                                occurrences_satisfied=True,
                                details={
                                    "reason": "max_reconnects_exceeded",
                                    "reconnects": reconnects,
                                    "max_reconnects": ctx.policy.max_reconnects,
                                },
                            )
                        )
            return signals

        outcomes = await self._process(
            sid, produce, delivered_to_caller=True, publish_warnings=publish_warnings
        )
        if not outcomes:
            return None
        return max(outcomes, key=lambda o: (ACTION_RANK[o.action], SEVERITY_RANK[o.severity]))

    # ================================================================ frames
    async def submit_frame(
        self,
        session_id: str,
        *,
        kind: Literal["camera", "screen"],
        image: bytes,
        content_type: str,
        captured_at: str,
        trigger: str = "periodic",
        face_check: str | Mapping[str, Any] | None = None,
    ) -> str:
        """Validate and normalise a frame, check camera frames with the face
        detector, store the frame per policy (evidence frames in
        ``media_assets`` with frame retention, or transiently in Redis) and
        enqueue ``integrity.analyze_frame`` when vision analysis applies.

        ``trigger="face_check"``: the one snapshot the browser sends when its
        on-device face check starts an interval (``face_check`` names the
        reason and the interval); it is confirmed here, right away, and linked
        to that interval. Returns the media id."""
        settings = get_settings()
        sid = _uuid(session_id)
        if kind not in FRAME_KINDS:
            raise ValidationFailed("kind must be 'camera' or 'screen'.", details={"field": "kind"})
        if trigger not in FRAME_TRIGGERS:
            raise ValidationFailed(
                "trigger must be 'periodic' or 'face_check'.", details={"field": "trigger"}
            )
        hint = parse_face_check_hint(face_check) if kind == "camera" else None
        if trigger == "face_check" and (
            kind != "camera" or hint is None or not hint.reason or not hint.episode
        ):
            raise ValidationFailed(
                "A face check snapshot needs a camera frame, a reason and an interval id.",
                details={"field": "face_check"},
            )
        validate_frame_bytes(image, content_type, max_bytes=settings.max_frame_bytes)
        now = self.now()
        async with session_scope() as db:
            ctx = await self.load_context(db, sid)
            if ctx.status in TERMINAL_STATUSES:
                raise Conflict("This interview session has ended.", code="session_ended")
            self._check_frame_policy(ctx, kind, trigger)
            await self._check_frame_consent(db, ctx, kind, trigger)
        await self._enforce_frame_rate(ctx, kind, trigger)
        prepared, detection = await asyncio.to_thread(
            _prepare_and_detect, image, content_type, settings.max_frame_bytes, kind == "camera"
        )
        # Frames are uploaded right after capture: the server receive time is the
        # authoritative timestamp (a skewed browser clock must not make every
        # frame look stale or stretch a streak); the client value is kept.
        captured = now
        client_time = parse_client_time(captured_at)
        faces = await self._track_faces(sid, detection, captured, prepared.width, hint)
        meta: dict[str, Any] = {
            "session_id": str(sid),
            "organization_id": str(ctx.organization_id),
            "kind": kind,
            "captured_at": _iso(captured),
            "client_captured_at": _iso(client_time) if client_time else None,
            "width": prepared.width,
            "height": prepared.height,
            "source_type": prepared.source_type,
            "source_bytes": prepared.source_bytes,
            "trigger": trigger,
        }
        if faces is not None:
            meta["faces"] = faces.model_dump(mode="json")
        if trigger == "face_check" and hint is not None:
            meta["face_check"] = {"reason": hint.reason, "episode": hint.episode}
        analyze = kind == "screen" or ctx.policy.camera_frame_analysis
        payload = {"session_id": str(sid), "kind": kind}
        stored = ctx.policy.store_evidence_frames
        if stored:
            key = (
                f"orgs/{ctx.organization_id}/sessions/{sid}/frames/{kind}/"
                f"{captured.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}.jpg"
            )
            async with session_scope() as db:
                asset = await store_media(
                    db,
                    organization_id=ctx.organization_id,
                    kind=FRAME_KINDS[kind],
                    key=key,
                    data=prepared.data,
                    content_type=prepared.content_type,
                    session_id=sid,
                    candidate_id=ctx.candidate_id,
                    retention_days=ctx.retention.frame_days,
                    details={**meta, "analysis": "pending" if analyze else "face_check"},
                )
                media_id = asset.id
                if analyze and trigger == "periodic":
                    await enqueue(
                        ANALYZE_FRAME_JOB,
                        {**payload, "media_id": str(media_id)},
                        dedupe_key=f"{ANALYZE_FRAME_JOB}:{media_id}",
                        db=db,
                    )
        else:
            media_id = uuid.uuid4()
            await self.store.put_frame(
                media_id, prepared.data, {**meta, "stored": False}, TRANSIENT_FRAME_TTL_SECONDS
            )
            if analyze and trigger == "periodic":
                await enqueue(
                    ANALYZE_FRAME_JOB,
                    {**payload, "media_id": str(media_id)},
                    dedupe_key=f"{ANALYZE_FRAME_JOB}:{media_id}",
                )
        if trigger == "face_check" and hint is not None:
            # Confirm (and link the evidence) before the vision analysis can discard the frame.
            await self._confirm_face_check(sid, media_id if stored else None, faces, hint, captured)
            if analyze:
                await enqueue(
                    ANALYZE_FRAME_JOB,
                    {**payload, "media_id": str(media_id)},
                    dedupe_key=f"{ANALYZE_FRAME_JOB}:{media_id}",
                )
            elif stored:
                await self._delete_if_not_evidence(media_id, reason="face_check_not_evidence")
            else:
                await self.store.delete_frame(media_id)
        log.info(
            "integrity.frame_accepted",
            session_id=str(sid),
            kind=kind,
            trigger=trigger,
            media_id=str(media_id),
            stored=stored,
            bytes=len(prepared.data),
            faces=faces.face_count if faces is not None else None,
            counted_faces=faces.counted_faces if faces is not None else None,
            face_detect_ms=faces.latency_ms if faces is not None else None,
        )
        return str(media_id)

    async def _track_faces(
        self,
        sid: uuid.UUID,
        detection: tuple[list[RawFace], float] | None,
        captured: datetime,
        frame_width: int,
        hint: FaceCheckHint | None,
    ) -> FaceCheckObservation | None:
        """Static-face tracking of the session (under the session's advisory lock)."""
        if detection is None:
            return None
        raw, latency_ms = detection
        async with session_scope() as db:
            await db.execute(select(func.pg_advisory_xact_lock(_lock_key(sid))))
            tracker = FaceTrackerState.from_dict(await self.store.get_face_tracks(sid))
            flags = update_tracks(
                tracker,
                raw,
                captured.timestamp(),
                frame_width_px=frame_width,
                client_static=hint.static_boxes if hint is not None else (),
            )
            await self.store.set_face_tracks(sid, tracker.to_dict())
        return build_observation(raw, flags, latency_ms=latency_ms)

    async def _confirm_face_check(
        self,
        sid: uuid.UUID,
        media_id: uuid.UUID | None,
        faces: FaceCheckObservation | None,
        hint: FaceCheckHint,
        captured: datetime,
    ) -> list[IntegrityOutcome]:
        """Server check of a face-check snapshot: the deterministic detector's
        count agrees with the on-device interval (``confirmed``), disagrees
        (``not_confirmed``) or could not run (``unavailable``)."""
        event_type = FACE_CHECK_EVENT_TYPES[hint.reason or ""]
        episode = hint.episode or ""
        if faces is None:
            status = "unavailable"
        elif event_type is T.MULTIPLE_FACES_VISIBLE:
            status = "confirmed" if faces.counted_faces >= 2 else "not_confirmed"
        else:
            status = "confirmed" if faces.counted_faces == 0 else "not_confirmed"
        check: dict[str, Any] = {
            "status": status,
            "detector": DETECTOR_NAME,
            "checked_at": _iso(captured),
        }
        if faces is not None:
            check.update(faces=faces.counted_faces, static_faces=faces.static_faces)
        evidence = media_id if status == "confirmed" else None
        if evidence is not None:
            check["media_id"] = str(evidence)
        await self.store.put_face_check(sid, episode, check)
        now = self.now()
        started, _ = clamp_time(parse_client_time(hint.started_at), now=captured)
        log.info(
            "integrity.face_check_confirmation",
            session_id=str(sid),
            event_type=event_type.value,
            status=status,
            faces=check.get("faces"),
        )

        async def produce(db: AsyncSession, ctx: SessionContext) -> list[Signal]:
            if not ctx.accepts_signals(now):
                return []
            details: dict[str, Any] = {"episode": episode, "phase": "server_check", "server_check": check}
            if hint.faces is not None:
                details["faces"] = hint.faces
            return [
                Signal(
                    event_type=event_type,
                    source=IntegritySource.BROWSER,
                    occurred_at=started,
                    duration_ms=hint.duration_ms,
                    details=details,
                    evidence_media_id=evidence,
                    episode=episode,
                    duration_mode="episode",
                )
            ]

        return await self._process(sid, produce, delivered_to_caller=False, publish_warnings=True)

    @staticmethod
    def _check_frame_policy(ctx: SessionContext, kind: FrameKind, trigger: str = "periodic") -> None:
        policy = ctx.policy
        if not policy.enabled:
            raise Forbidden(
                "Integrity monitoring is not enabled for this interview.", code="frames_not_enabled"
            )
        if kind == "camera" and trigger == "face_check":
            rule = ctx.rules.get(T.FACE_NOT_VISIBLE)
            if rule is None or not rule.enabled:
                raise Forbidden(
                    "The face check is not enabled for this interview.", code="frames_not_enabled"
                )
            return
        if kind == "camera" and not policy.camera_frame_analysis:
            raise Forbidden(
                "Camera frame analysis is not enabled for this interview.", code="frames_not_enabled"
            )
        if kind == "screen" and not policy.screen_share_analysis:
            raise Forbidden("Screen analysis is not enabled for this interview.", code="frames_not_enabled")

    @staticmethod
    async def _latest_consent(db: AsyncSession, ctx: SessionContext) -> Consent | None:
        return await db.scalar(
            select(Consent)
            .where(Consent.interview_id == ctx.interview_id, Consent.withdrawn_at.is_(None))
            .order_by(Consent.accepted_at.desc())
            .limit(1)
        )

    @classmethod
    async def _face_check_consented(cls, db: AsyncSession, ctx: SessionContext) -> bool:
        """The latest consent was given to a disclosure that described the face
        check (consent item ``face_check``) and the camera was not declined."""
        consent = await cls._latest_consent(db, ctx)
        items = (consent.items or {}) if consent is not None else {}
        return items.get("face_check") is True and items.get("camera") is not False

    @classmethod
    async def _check_frame_consent(
        cls, db: AsyncSession, ctx: SessionContext, kind: FrameKind, trigger: str = "periodic"
    ) -> None:
        consent = await cls._latest_consent(db, ctx)
        if consent is None:
            raise Forbidden("Consent is required before frames can be submitted.", code="consent_required")
        items = consent.items or {}
        if kind == "camera" and trigger == "face_check":
            if items.get("face_check") is not True or items.get("camera") is False:
                raise Forbidden("Consent for the face check was not given.", code="consent_required")
            return
        keys = ("camera", "camera_frames") if kind == "camera" else ("screen_share",)
        if any(items.get(key) is False for key in keys):
            raise Forbidden("Consent for this kind of frame was not given.", code="consent_required")

    @staticmethod
    async def _enforce_frame_rate(ctx: SessionContext, kind: FrameKind, trigger: str = "periodic") -> None:
        if trigger == "face_check":
            result = await hit(
                "integrity_frames_face_check", str(ctx.session_id), FACE_CHECK_FRAMES_PER_MINUTE, 60
            )
        else:
            interval = (
                ctx.policy.camera_frame_interval_seconds
                if kind == "camera"
                else ctx.policy.screen_frame_interval_seconds
            )
            limit = math.ceil(60 / max(interval, 1)) * 2 + 2
            result = await hit(f"integrity_frames_{kind}", str(ctx.session_id), limit, 60)
        if not result.allowed:
            raise RateLimited("Frames are being sent too often.", retry_after=result.retry_after)

    # =========================================================== frame analysis
    async def analyze_frame(
        self, session_id: str, media_id: str, *, vision_optional: bool = False
    ) -> list[IntegrityOutcome]:
        """Worker: vision observation (+ the face detection made when the frame
        was received) -> sustained-signal rules -> events.

        AI outages (``AIUnavailable``/``AITimeout``) propagate so the job is
        retried - except with ``vision_optional`` (the job's last attempt), when
        a camera frame with a face detection is analysed on the face detector
        alone. Invalid model output is logged and skipped. A stored frame that
        does not become evidence for any event is deleted (data minimisation);
        transient frames are dropped after analysis."""
        sid = _uuid(session_id)
        mid = _uuid(media_id, "media_id")
        frame = await self._load_frame(sid, mid)
        if frame is None:
            log.info("integrity.frame_missing", session_id=str(sid), media_id=str(mid))
            return []
        data, meta, stored = frame
        kind: FrameKind = "screen" if meta.get("kind") == "screen" else "camera"
        now = self.now()
        captured = parse_client_time(meta.get("captured_at")) or now
        async with session_scope() as db:
            ctx = await self.load_context(db, sid)
        enabled = ctx.policy.enabled and (
            ctx.policy.camera_frame_analysis if kind == "camera" else ctx.policy.screen_share_analysis
        )
        if not enabled:
            await self._discard_frame(mid, stored, reason="analysis_disabled")
            return []
        age = (now - captured).total_seconds()
        if age > MAX_FRAME_AGE_SECONDS:
            log.info(
                "integrity.frame_stale_skipped", session_id=str(sid), media_id=str(mid), age_s=round(age, 1)
            )
            await self._discard_frame(mid, stored, reason="stale")
            return []
        evidence_id = mid if stored else None
        faces = faces_from_meta(meta) if kind == "camera" else None
        observation: FrameObservation | ScreenObservation | None
        try:
            if kind == "camera":
                observation = await self._observe(ctx, data, FrameObservation)
            else:
                observation = await self._observe(ctx, data, ScreenObservation)
        except AIOutputInvalid as exc:
            log.warning(
                "integrity.frame_output_invalid", session_id=str(sid), media_id=str(mid), error=str(exc)[:200]
            )
            await self._discard_frame(mid, stored, reason="model_output_invalid")
            return []
        except (AIUnavailable, AITimeout) as exc:
            if not (vision_optional and kind == "camera" and faces is not None):
                raise
            log.warning(
                "integrity.vision_unavailable_face_only",
                session_id=str(sid),
                media_id=str(mid),
                error=type(exc).__name__,
            )
            observation = None

        async def produce(db: AsyncSession, live_ctx: SessionContext) -> list[Signal]:
            if kind == "camera":
                camera_obs = observation if isinstance(observation, FrameObservation) else None
                state = CameraVisionState.from_dict(await self.store.get_vision_state(sid, "camera"))
                step = advance_camera_state(
                    state, camera_obs, captured, live_ctx.rules, evidence_media_id=evidence_id, faces=faces
                )
                await self.store.set_vision_state(sid, "camera", state.to_dict())
                summary = step.observation
                signals = step.signals
            else:
                assert isinstance(observation, ScreenObservation)
                signals = screen_signals(observation, captured, evidence_media_id=evidence_id)
                summary = observation.model_dump(mode="json")
            if stored:
                asset = await db.get(MediaAsset, mid)
                if asset is not None:
                    asset.details = {**(asset.details or {}), "analysis": "done", "observation": summary}
            return signals

        outcomes = await self._process(sid, produce, delivered_to_caller=False, publish_warnings=True)
        if stored:
            await self._delete_if_not_evidence(mid)
        else:
            await self.store.delete_frame(mid)
        return outcomes

    async def _observe(self, ctx: SessionContext, image: bytes, schema: type[Any]) -> Any:
        settings = get_settings()
        camera = schema is FrameObservation
        prompt = CAMERA_PROMPT if camera else SCREEN_PROMPT
        messages = vision_messages(prompt, CAMERA_USER_TEXT if camera else SCREEN_USER_TEXT, image)
        result = await get_ai().llm.structured(
            schema,
            messages,
            context=CallContext(
                task=ModelTask.FRAME_ANALYSIS if camera else ModelTask.SCREEN_ANALYSIS,
                session_id=str(ctx.session_id),
                organization_id=str(ctx.organization_id),
                prompt_version=prompt.version,
            ),
            endpoint="vision",
            temperature=0.0,
            max_tokens=VISION_MAX_TOKENS,
            timeout=settings.vision_llm_timeout_seconds,
            max_attempts=2,
        )
        return result.value

    async def _load_frame(self, sid: uuid.UUID, mid: uuid.UUID) -> tuple[bytes, dict[str, Any], bool] | None:
        transient = await self.store.get_frame(mid)
        if transient is not None:
            data, meta = transient
            if meta.get("session_id") != str(sid):
                log.warning("integrity.frame_session_mismatch", media_id=str(mid))
                return None
            return data, meta, False
        async with session_scope() as db:
            asset = await db.get(MediaAsset, mid)
            if (
                asset is None
                or asset.deleted_at is not None
                or asset.session_id != sid
                or asset.kind not in (MediaKind.CAMERA_FRAME.value, MediaKind.SCREEN_FRAME.value)
            ):
                return None
            key, details = asset.storage_key, dict(asset.details or {})
        try:
            data = await get_storage().read(key)
        except ObjectNotFound:
            return None
        details.setdefault("kind", "screen" if asset.kind == MediaKind.SCREEN_FRAME.value else "camera")
        return data, details, True

    async def _discard_frame(self, mid: uuid.UUID, stored: bool, *, reason: str) -> None:
        if stored:
            await self._delete_if_not_evidence(mid, reason=reason)
        else:
            await self.store.delete_frame(mid)

    async def _delete_if_not_evidence(self, mid: uuid.UUID, *, reason: str = "not_evidence") -> None:
        async with session_scope() as db:
            linked = await db.scalar(
                select(func.count())
                .select_from(IntegrityEvent)
                .where(IntegrityEvent.evidence_media_id == mid)
            )
            if linked:
                return
            await delete_media(db, mid, reason=reason)

    # ============================================================== pipeline
    async def _process(
        self,
        sid: uuid.UUID,
        produce: SignalProducer,
        *,
        delivered_to_caller: bool,
        publish_warnings: bool,
    ) -> list[IntegrityOutcome]:
        applied: list[_Applied] = []
        async with session_scope() as db:
            await db.execute(select(func.pg_advisory_xact_lock(_lock_key(sid))))
            ctx = await self.load_context(db, sid)
            for signal in await produce(db, ctx):
                result = await self._apply(db, ctx, signal)
                if result is not None:
                    applied.append(result)
                if signal.event_type in FOCUS_SWITCH_TYPES:
                    derived = await self._rapid_switching(db, ctx, signal)
                    if derived is not None:
                        applied.append(derived)
        for item in applied:
            INTEGRITY_EVENTS.labels(type=item.outcome.event_type.value, source=item.source.value).inc()
        await self._announce(
            sid, applied, delivered_to_caller=delivered_to_caller, publish_warnings=publish_warnings
        )
        return [item.outcome for item in applied]

    async def _rapid_switching(
        self, db: AsyncSession, ctx: SessionContext, signal: Signal
    ) -> _Applied | None:
        rule = ctx.rules.get(T.RAPID_FOCUS_SWITCHING)
        if rule is None or not rule.enabled:
            return None
        if signal.duration_ms is not None and signal.duration_ms < RAPID_SWITCH_MIN_MS:
            # A blip (a notification, an OS focus glitch, a permission prompt), not a switch (S22). Longer glances
            # still add up even when each is below its own rule's minimum: five 1-2 s looks a minute is the signal.
            return None
        count = await self.store.record_occurrence(
            ctx.session_id, "switches", signal.occurred_at.timestamp(), rule.window_seconds
        )
        if count < rule.min_occurrences:
            return None
        derived = Signal(
            event_type=T.RAPID_FOCUS_SWITCHING,
            source=IntegritySource.BROWSER,
            occurred_at=signal.occurred_at,
            occurrences_satisfied=True,
            details={
                "derived_from": "focus_changes",
                "switches_in_window": count,
                "window_seconds": rule.window_seconds,
            },
        )
        return await self._apply(db, ctx, derived)

    async def _apply(self, db: AsyncSession, ctx: SessionContext, signal: Signal) -> _Applied | None:
        rule = ctx.rules.get(signal.event_type)
        if rule is None or not rule.enabled:
            return None
        allowed = await hit("integrity_signals", str(ctx.session_id), SIGNALS_PER_MINUTE, 60)
        if not allowed.allowed:
            log.warning(
                "integrity.signal_rate_limited",
                session_id=str(ctx.session_id),
                event_type=signal.event_type.value,
            )
            return None
        t = signal.occurred_at.timestamp()
        occurrences = 1
        is_gaze = signal.event_type is T.LOOKING_AWAY_WITH_OTHER_SIGNALS
        if qualification(rule, signal) is None and not is_gaze:
            occurrences = await self.store.record_occurrence(
                ctx.session_id, signal.event_type.value, t, rule.window_seconds
            )
        strong = False
        if is_gaze:
            strong = await self.store.count_recent(ctx.session_id, "strong", t, rule.window_seconds) > 0
        decision = decide(
            rule,
            signal,
            occurrences=occurrences,
            strong_signal_recent=strong,
            warn_candidate=ctx.policy.warn_candidate,
        )
        if not decision.persist:
            return None
        if decision.qualifies and signal.event_type in STRONG_SIGNAL_TYPES:
            await self.store.record_occurrence(ctx.session_id, "strong", t, 3600)
        message = decision.candidate_message
        if message and not await self.store.claim_warning(
            ctx.session_id, signal.event_type.value, WARNING_COOLDOWN_SECONDS
        ):
            message = None
        event, aggregated = await self._persist(
            db, ctx, signal, decision, rule, occurrences, warned=message is not None
        )
        return _Applied(
            outcome=IntegrityOutcome(
                event_id=str(event.id),
                event_type=signal.event_type,
                severity=decision.severity,
                action=decision.action,
                candidate_message=message,
            ),
            event_id=event.id,
            source=signal.source,
            confidence=event.confidence,
            count=int(event.count or 1),
            aggregated=aggregated,
            occurred_at=event.occurred_at,
            elapsed_seconds=event.elapsed_seconds,
            evidence_media_id=event.evidence_media_id,
        )

    async def _persist(
        self,
        db: AsyncSession,
        ctx: SessionContext,
        signal: Signal,
        decision: Decision,
        rule: EffectiveRule,
        occurrences: int,
        *,
        warned: bool,
    ) -> tuple[IntegrityEvent, bool]:
        window = rule.aggregate_seconds if decision.action is not A.LOG_ONLY else LOG_ONLY_AGGREGATE_SECONDS
        if signal.duration_mode in ("episode", "summary") and signal.episode:
            return await self._persist_grouped(db, ctx, signal, decision, rule, occurrences, warned=warned)
        streak = IntegrityEvent.details["streak_id"].astext
        stmt = (
            select(IntegrityEvent)
            .where(
                IntegrityEvent.session_id == ctx.session_id,
                IntegrityEvent.type == signal.event_type.value,
                IntegrityEvent.source == signal.source.value,
                IntegrityEvent.action == decision.action.value,
                IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
                streak == signal.streak_id if signal.streak_id else streak.is_(None),
            )
            .order_by(IntegrityEvent.created_at.desc(), IntegrityEvent.occurred_at.desc())
            .limit(1)
        )
        existing = await db.scalar(stmt)
        at = signal.occurred_at
        if existing is not None:
            last = parse_client_time((existing.details or {}).get("last_occurred_at")) or existing.occurred_at
            if signal.streak_id or (at - last).total_seconds() <= window:
                self._aggregate(existing, signal, decision, occurrences, last=last, warned=warned)
                await db.flush()
                return existing, True
        details: dict[str, Any] = {
            **signal.details,
            "label": label_for(signal.event_type),
            "first_occurred_at": _iso(at),
            "last_occurred_at": _iso(at),
            "occurrences_in_window": occurrences,
            "decision": decision.reason,
            "rule": rule.thresholds(),
        }
        if signal.streak_id:
            details["streak_id"] = signal.streak_id
        if decision.downgraded_from is not None:
            details["downgraded_from"] = decision.downgraded_from.value
        if warned:
            details["candidate_warnings"] = 1
        event = IntegrityEvent(
            session_id=ctx.session_id,
            organization_id=ctx.organization_id,
            type=signal.event_type.value,
            source=signal.source.value,
            severity=decision.severity.value,
            confidence=signal.confidence,
            occurred_at=at,
            elapsed_seconds=ctx.elapsed_seconds(at),
            duration_ms=signal.duration_ms,
            count=1,
            details=details,
            evidence_media_id=signal.evidence_media_id,
            action=decision.action.value,
            review_status=IntegrityReviewStatus.UNREVIEWED.value,
        )
        db.add(event)
        await db.flush()
        return event, False

    async def _persist_grouped(
        self,
        db: AsyncSession,
        ctx: SessionContext,
        signal: Signal,
        decision: Decision,
        rule: EffectiveRule,
        occurrences: int,
        *,
        warned: bool,
    ) -> tuple[IntegrityEvent, bool]:
        """Face-check signals: one row per burst of intervals (``episode``) or
        one row per session for the summary (``summary``, keyed by page run).
        The row keeps the strongest action any of its signals reached."""
        existing = await db.scalar(
            select(IntegrityEvent)
            .where(
                IntegrityEvent.session_id == ctx.session_id,
                IntegrityEvent.type == signal.event_type.value,
                IntegrityEvent.source == signal.source.value,
                IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
            )
            .order_by(IntegrityEvent.created_at.desc(), IntegrityEvent.occurred_at.desc())
            .limit(1)
        )
        key = "runs" if signal.duration_mode == "summary" else "episodes"
        at = signal.occurred_at
        if existing is not None:
            details = existing.details or {}
            bucket = details.get(key) if isinstance(details.get(key), dict) else {}
            last = parse_client_time(details.get("last_occurred_at")) or existing.occurred_at
            if (
                signal.duration_mode == "summary"
                or signal.episode in bucket
                or (at - last).total_seconds() <= rule.aggregate_seconds
            ):
                self._aggregate_grouped(existing, signal, decision, occurrences, last=last, warned=warned)
                await db.flush()
                return existing, True
        details_new: dict[str, Any] = {
            **{k: v for k, v in signal.details.items() if k not in ("episode", "run")},
            "label": label_for(signal.event_type),
            "first_occurred_at": _iso(at),
            "last_occurred_at": _iso(at),
            "occurrences_in_window": occurrences,
            "decision": decision.reason,
            "rule": rule.thresholds(),
        }
        self._merge_bucket(details_new, signal)
        if decision.downgraded_from is not None:
            details_new["downgraded_from"] = decision.downgraded_from.value
        if warned:
            details_new["candidate_warnings"] = 1
        event = IntegrityEvent(
            session_id=ctx.session_id,
            organization_id=ctx.organization_id,
            type=signal.event_type.value,
            source=signal.source.value,
            severity=decision.severity.value,
            confidence=signal.confidence,
            occurred_at=at,
            elapsed_seconds=ctx.elapsed_seconds(at),
            duration_ms=self._grouped_duration(details_new, signal),
            count=self._grouped_count(details_new, signal),
            details=details_new,
            evidence_media_id=signal.evidence_media_id,
            action=decision.action.value,
            review_status=IntegrityReviewStatus.UNREVIEWED.value,
        )
        db.add(event)
        await db.flush()
        return event, False

    @staticmethod
    def _merge_bucket(details: dict[str, Any], signal: Signal) -> None:
        """Fold one face-check signal into ``details`` (episodes / runs / checks)."""
        episode = signal.episode or ""
        if signal.duration_mode == "summary":
            runs = dict(details.get("runs") or {})
            runs[episode] = {
                name: signal.details[name]
                for name in SUMMARY_COUNTERS
                if isinstance(signal.details.get(name), int | float)
                and not isinstance(signal.details.get(name), bool)
            }
            while len(runs) > MAX_RUNS_PER_SUMMARY:
                runs.pop(next(iter(runs)))
            details["runs"] = runs
            for name in SUMMARY_COUNTERS:
                details[name] = sum(int(run.get(name, 0) or 0) for run in runs.values())
            return
        episodes = dict(details.get("episodes") or {})
        previous = int(episodes.get(episode, 0) or 0)
        episodes[episode] = max(previous, int(signal.duration_ms or 0))
        while len(episodes) > MAX_EPISODES_PER_EVENT:
            episodes.pop(next(iter(episodes)))
        details["episodes"] = episodes
        check = signal.details.get("server_check")
        if isinstance(check, Mapping) and isinstance(check.get("status"), str):
            checks = dict(details.get("server_checks") or {})
            checks[episode] = check["status"]
            details["server_checks"] = checks
            current = details.get("server_check")
            current_rank = CHECK_RANK.get(current.get("status"), 0) if isinstance(current, Mapping) else 0
            if CHECK_RANK.get(check["status"], 0) >= current_rank:
                details["server_check"] = dict(check)

    @staticmethod
    def _grouped_duration(details: Mapping[str, Any], signal: Signal) -> int | None:
        if signal.duration_mode == "summary":
            value = details.get("monitored_ms")
            return int(value) if isinstance(value, int | float) else None
        episodes = details.get("episodes") or {}
        return int(sum(int(v or 0) for v in episodes.values())) if episodes else signal.duration_ms

    @staticmethod
    def _grouped_count(details: Mapping[str, Any], signal: Signal) -> int:
        bucket = details.get("runs" if signal.duration_mode == "summary" else "episodes") or {}
        return max(1, len(bucket))

    def _aggregate_grouped(
        self,
        event: IntegrityEvent,
        signal: Signal,
        decision: Decision,
        occurrences: int,
        *,
        last: datetime,
        warned: bool,
    ) -> None:
        details = dict(event.details or {})
        for key, value in signal.details.items():
            if key in ("episode", "run", "server_check"):
                continue
            details[key] = value
        self._merge_bucket(details, signal)
        details["last_occurred_at"] = _iso(max(last, signal.occurred_at))
        details["occurrences_in_window"] = max(int(details.get("occurrences_in_window", 1) or 1), occurrences)
        if warned:
            details["candidate_warnings"] = int(details.get("candidate_warnings", 0) or 0) + 1
        if ACTION_RANK[decision.action] > ACTION_RANK[IntegrityAction(event.action)]:
            event.action = decision.action.value
            details["decision"] = decision.reason
            if decision.downgraded_from is not None:
                details["downgraded_from"] = decision.downgraded_from.value
        event.severity = max_severity(event.severity, decision.severity).value
        if signal.confidence is not None:
            event.confidence = max(float(event.confidence or 0.0), signal.confidence)
        if event.evidence_media_id is None and signal.evidence_media_id is not None:
            event.evidence_media_id = signal.evidence_media_id
        event.duration_ms = self._grouped_duration(details, signal)
        event.count = self._grouped_count(details, signal)
        event.details = details

    @staticmethod
    def _aggregate(
        event: IntegrityEvent,
        signal: Signal,
        decision: Decision,
        occurrences: int,
        *,
        last: datetime,
        warned: bool,
    ) -> None:
        event.count = int(event.count or 1) + 1
        if signal.duration_ms is not None:
            if signal.duration_mode == "max":
                event.duration_ms = max(int(event.duration_ms or 0), signal.duration_ms)
            else:
                event.duration_ms = int(event.duration_ms or 0) + signal.duration_ms
        if signal.confidence is not None:
            event.confidence = max(float(event.confidence or 0.0), signal.confidence)
        event.severity = max_severity(event.severity, decision.severity).value
        if event.evidence_media_id is None and signal.evidence_media_id is not None:
            event.evidence_media_id = signal.evidence_media_id
        details = dict(event.details or {})
        for key, value in signal.details.items():
            if key == "observation" and "observation" in details:
                continue  # keep the observation of the evidence frame
            details[key] = value
        details["last_occurred_at"] = _iso(max(last, signal.occurred_at))
        details["occurrences_in_window"] = max(int(details.get("occurrences_in_window", 1) or 1), occurrences)
        if warned:
            details["candidate_warnings"] = int(details.get("candidate_warnings", 0) or 0) + 1
        event.details = details

    async def _announce(
        self,
        sid: uuid.UUID,
        applied: Sequence[_Applied],
        *,
        delivered_to_caller: bool,
        publish_warnings: bool,
    ) -> None:
        for item in applied:
            outcome = item.outcome
            await publish_session_event(
                sid,
                {
                    "type": "integrity.event",
                    "event_id": str(item.event_id),
                    "event_type": outcome.event_type.value,
                    "source": item.source.value,
                    "severity": outcome.severity.value,
                    "action": outcome.action.value,
                    "confidence": item.confidence,
                    "count": item.count,
                    "aggregated": item.aggregated,
                    "occurred_at": _iso(item.occurred_at),
                    "elapsed_seconds": item.elapsed_seconds,
                    "evidence_media_id": str(item.evidence_media_id) if item.evidence_media_id else None,
                    "delivered_to_caller": delivered_to_caller,
                },
            )
            if outcome.candidate_message and (publish_warnings or not delivered_to_caller):
                await publish_session_event(
                    sid,
                    {
                        "type": "integrity.warning",
                        "event_id": str(item.event_id),
                        "event_type": outcome.event_type.value,
                        "message": outcome.candidate_message,
                    },
                )


# ---------------------------------------------------------------- provider

_service: Any = None


def get_integrity_service() -> DefaultIntegrityService:
    """Process-wide service (the realtime runtime, portal and worker use this)."""
    global _service
    if _service is None:
        _service = DefaultIntegrityService()
    return _service  # type: ignore[no-any-return]


def set_integrity_service(service: Any) -> Any:
    """Swap the service (tests / integration wiring); returns the previous one."""
    global _service
    previous, _service = _service, service
    return previous


__all__ = [
    "ANALYZE_FRAME_JOB",
    "NOTICE",
    "DefaultIntegrityService",
    "SessionContext",
    "get_integrity_service",
    "set_integrity_service",
]
