"""Frame analysis: objective-only prompts and the sustained-signal state machine.

The 8B vision model describes each frame with ``FrameObservation`` (camera) or
``ScreenObservation`` (screen). Single frames are never acted on directly:
:func:`advance_camera_state` keeps per-session state (persisted in Redis by the
service) and turns observations into :class:`~app.services.integrity.rules.Signal`
objects only for sustained or repeated conditions:

* no person visible for >= 20 s (``no_person_visible``), or the person who was
  visible left the view for >= 30 s (``person_left_frame``) - one event per
  absence streak;
* more than one person in >= 2 consecutive frames with confidence >= 0.75
  (``multiple_persons_visible``); a partially visible additional person in >= 2
  consecutive frames (``another_person_entered``);
* a phone-like object in >= 2 frames within 60 s (``possible_phone_visible``,
  counted by the rule engine), and a phone held toward the screen with
  confidence >= 0.75 (``possible_photo_taking``);
* camera covered or too dark for >= 20 s (``camera_blocked``, source vision);
* the primary person looking away is recorded, but only produces
  ``looking_away_with_other_signals`` - which the rule engine drops unless a
  stronger objective signal happened in the same window.

Thresholds come from the effective rules, so recruiter overrides apply.

Face detector fusion: every camera frame is also checked by the deterministic
server-side face detector (:mod:`app.services.integrity.faces`, OpenCV YuNet).
Motionless faces (photos, posters, screens) are set aside first. Then:

* "more than one person" needs the face detector to see >= 2 faces that moved
  between frames, or the vision model to report >= 2 people (minus the faces
  not seen moving) with confidence >= ``VISION_HIGH_CONFIDENCE`` - so a drawn
  figure or a poster the model mistakes for a person no longer counts on its
  own (the on-device check, at ~4 fps, catches a still second person);
* "nobody visible" needs the face detector to count no face AND the vision
  model to see nobody; without a vision observation (analysis disabled or the
  model unavailable) it needs no face on consecutive frames;
* phones, screens, a covered camera and partial people remain vision-only.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.ai.prompts import PromptTemplate
from app.domain.enums import IntegrityEventType, IntegritySource
from app.schemas.ai import FaceCheckObservation, FrameObservation, ScreenObservation
from app.services.integrity.rules import EffectiveRule, Signal

T = IntegrityEventType

# The vision model's "two or more people" counts against a face detector that
# sees fewer faces only at or above this confidence.
VISION_HIGH_CONFIDENCE = 0.9
# Confidence given to observations made by the face detector alone (no vision model).
FACE_DETECTOR_CONFIDENCE = 0.8
# Without a vision observation, "nobody visible" needs this many consecutive frames.
FACE_ONLY_ABSENCE_FRAMES = 2

CAMERA_PROMPT = PromptTemplate(
    "integrity.frame_observation",
    """\
You write an objective log entry for one still image from a candidate's webcam during a remote job interview.
Report only facts that are plainly visible in this single image, as one JSON object.

Fields:
- person_count: number of real people visible (face or body). A person in a photo, poster or on a screen is not a person.
- primary_face_visible: true when the main person's face is clearly visible.
- camera_obstructed: true when the lens is covered or the view is blocked.
- image_too_dark: true when the image is too dark to see the scene.
- phone_like_object_visible: true when a mobile phone or similar handheld device is visible.
- phone_confidence: how sure you are that a phone-like object is visible (0 to 1; 0 when none).
- phone_held_toward_screen: true only when a phone is held up with its back camera pointing toward the computer screen.
- additional_person_partially_visible: true when part of another person (face, hand, shoulder) is visible besides the main person.
- primary_person_looking_away: true when the main person's head is turned well away from the screen (context only).
- notes: empty string, or at most 12 words of objective scene facts.
- confidence: how sure you are about this description overall (0 to 1).

Strict rules:
- Never identify anyone and never describe appearance, clothing, age, gender, ethnicity, skin, hair, body, facial expression, emotion, attention, honesty or intent.
- Never speculate about cheating or behaviour. Describe the scene, not the person.
- When unsure, answer false and lower the confidence.

{{ FAIRNESS_RULES }}""",
)

SCREEN_PROMPT = PromptTemplate(
    "integrity.screen_observation",
    """\
You write an objective log entry for one screenshot of the screen a candidate shares during a remote job interview.
Report only what is plainly visible, as one JSON object.

Fields:
- active_context: the application or page in the foreground, in a few generic words (e.g. "interview tab", "code editor", "terminal", "chat application", "search results").
- interview_window_visible: true when the interview web page is visible.
- other_applications_visible: generic kinds of other visible applications or pages (at most 6), e.g. "messaging app", "AI chat assistant".
- possible_prohibited_context: true only when the screen clearly shows something that could supply answers: an AI chat assistant, a messaging or chat conversation, a web search or Q&A site, or notes about the interview questions. The interview page, a code editor or terminal, the desktop and ordinary system windows are not prohibited.
- description: at most 25 objective words. Never quote names, messages, e-mails, file contents, personal data or anything typed.
- confidence: how sure you are (0 to 1).

Strict rules:
- Describe the screen, never the person. Do not speculate about intent or cheating.
- When unsure, answer false and lower the confidence.

{{ FAIRNESS_RULES }}""",
)

CAMERA_USER_TEXT = "Describe this webcam frame as the JSON object specified."
SCREEN_USER_TEXT = "Describe this shared-screen frame as the JSON object specified."


def vision_messages(
    prompt: PromptTemplate, user_text: str, image: bytes, content_type: str = "image/jpeg"
) -> list[dict[str, Any]]:
    data_url = f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"
    return [
        {"role": "system", "content": prompt.render()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


# ----------------------------------------------------------------------- state


@dataclass(slots=True)
class Streak:
    start: float  # epoch seconds of the first frame (captured_at)
    last: float
    frames: int = 1
    kind: str | None = None
    max_confidence: float = 0.0

    def extend(self, t: float, confidence: float) -> None:
        self.last = max(self.last, t)
        self.frames += 1
        self.max_confidence = max(self.max_confidence, confidence)

    @property
    def duration_ms(self) -> int:
        return max(0, int(round((self.last - self.start) * 1000)))

    @property
    def streak_id(self) -> str:
        return f"{self.kind or 'streak'}:{int(self.start * 1000)}"


@dataclass(slots=True)
class CameraVisionState:
    last_captured_at: float | None = None
    frames_analyzed: int = 0
    seen_person: bool = False
    absence: Streak | None = None
    multi: Streak | None = None
    partial: Streak | None = None
    obstructed: Streak | None = None
    looking_away_frames: int = 0
    phone_frames: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> CameraVisionState:
        if not data:
            return cls()
        state = cls()
        for name in (
            "last_captured_at",
            "frames_analyzed",
            "seen_person",
            "looking_away_frames",
            "phone_frames",
        ):
            if name in data:
                setattr(state, name, data[name])
        for name in ("absence", "multi", "partial", "obstructed"):
            raw = data.get(name)
            if isinstance(raw, Mapping):
                try:
                    setattr(
                        state,
                        name,
                        Streak(
                            **{
                                k: raw[k]
                                for k in ("start", "last", "frames", "kind", "max_confidence")
                                if k in raw
                            }
                        ),
                    )
                except TypeError:
                    setattr(state, name, None)
        return state


@dataclass(slots=True)
class CameraStep:
    signals: list[Signal] = field(default_factory=list)
    in_order: bool = True
    observation: dict[str, Any] = field(default_factory=dict)


def observation_summary(
    obs: FrameObservation | None, faces: FaceCheckObservation | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = obs.model_dump(mode="json") if obs is not None else {"vision": "unavailable"}
    if obs is not None:
        data["notes"] = (data.get("notes") or "")[:200]
    if faces is not None:
        data["faces"] = faces.model_dump(mode="json")
    return data


@dataclass(frozen=True, slots=True)
class CameraFacts:
    """What one camera frame shows, after fusing the vision observation with
    the face detector (see the module docstring)."""

    vision: bool
    confidence: float
    blocked: bool = False
    blocked_reason: str | None = None
    absent: bool = False
    present: bool = False
    multi: bool = False
    multi_confidence: float = 0.0
    people: int = 0
    basis: str = "vision"
    partial: bool = False


def camera_facts(
    obs: FrameObservation | None,
    faces: FaceCheckObservation | None,
    rules: Mapping[IntegrityEventType, EffectiveRule],
) -> CameraFacts:
    multi_rule = rules[T.MULTIPLE_PERSONS_VISIBLE]
    if obs is None and faces is None:
        return CameraFacts(vision=False, confidence=0.0)
    if obs is None:
        assert faces is not None
        counted = faces.counted_faces
        second = sorted((f.score for f in faces.faces if f.moving), reverse=True)
        return CameraFacts(
            vision=False,
            confidence=FACE_DETECTOR_CONFIDENCE,
            absent=counted == 0,
            present=counted >= 1,
            multi=faces.live_faces >= 2,
            multi_confidence=second[1] if len(second) >= 2 else 0.0,
            people=counted,
            basis="face_detector",
        )
    blocked = obs.camera_obstructed or obs.image_too_dark
    reason = "obstructed" if obs.camera_obstructed else ("too_dark" if obs.image_too_dark else None)
    if faces is None:
        absent = (not blocked) and obs.person_count == 0 and not obs.primary_face_visible
        multi = obs.person_count >= 2 and obs.confidence >= multi_rule.min_confidence
        return CameraFacts(
            vision=True,
            confidence=obs.confidence,
            blocked=blocked,
            blocked_reason=reason,
            absent=absent,
            present=obs.person_count >= 1 or obs.primary_face_visible,
            multi=multi,
            multi_confidence=obs.confidence,
            people=obs.person_count,
            basis="vision",
            partial=obs.additional_person_partially_visible and obs.person_count <= 1,
        )
    counted = faces.counted_faces
    # Faces not seen moving yet (a picture, or someone who just appeared) do not make
    # "more than one person" on their own; motionless faces the vision model may
    # have counted as people are set aside.
    still = faces.face_count - faces.live_faces
    vision_people = max(0, obs.person_count - still)
    scores = sorted((f.score for f in faces.faces if f.moving), reverse=True)
    by_faces = faces.live_faces >= 2
    by_vision = vision_people >= 2 and obs.confidence >= max(
        VISION_HIGH_CONFIDENCE, multi_rule.min_confidence
    )
    if by_faces and by_vision:
        basis, multi_conf = "face_detector+vision", max(obs.confidence, scores[1])
    elif by_faces:
        basis, multi_conf = "face_detector", scores[1]
    else:
        basis, multi_conf = "vision", obs.confidence
    vision_face = obs.primary_face_visible and faces.static_faces == 0
    people = max(faces.live_faces, vision_people)
    absent = (not blocked) and counted == 0 and vision_people == 0 and not vision_face
    return CameraFacts(
        vision=True,
        confidence=obs.confidence,
        blocked=blocked,
        blocked_reason=reason,
        absent=absent,
        present=counted >= 1 or vision_people >= 1,
        multi=by_faces or by_vision,
        multi_confidence=multi_conf,
        people=people,
        basis=basis,
        partial=obs.additional_person_partially_visible and people <= 1,
    )


def screen_summary(obs: ScreenObservation) -> dict[str, Any]:
    data = obs.model_dump(mode="json")
    data["description"] = (data.get("description") or "")[:300]
    data["active_context"] = (data.get("active_context") or "")[:120]
    return data


def _ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=UTC)


def advance_camera_state(
    state: CameraVisionState,
    obs: FrameObservation | None,
    captured_at: datetime,
    rules: Mapping[IntegrityEventType, EffectiveRule],
    *,
    evidence_media_id: uuid.UUID | None = None,
    faces: FaceCheckObservation | None = None,
) -> CameraStep:
    """Update ``state`` with one frame (vision observation and/or face
    detection); returns the signals to evaluate.

    Frames analysed out of order (retries, concurrency) only contribute
    order-independent signals (phone sightings); streaks use in-order frames.
    """
    t = captured_at.timestamp()
    step = CameraStep(observation=observation_summary(obs, faces))
    in_order = state.last_captured_at is None or t >= state.last_captured_at - 0.001
    step.in_order = in_order
    summary = step.observation
    facts = camera_facts(obs, faces, rules)
    if obs is None and faces is None:
        return step

    def make(event_type: IntegrityEventType, occurred: float, **kwargs: Any) -> Signal:
        details = {"observation": summary, **kwargs.pop("details", {})}
        return Signal(
            event_type=event_type,
            source=IntegritySource.VISION,
            occurred_at=_ts(occurred),
            evidence_media_id=evidence_media_id,
            details=details,
            **kwargs,
        )

    blocked = facts.blocked
    absent = facts.absent
    confidence = facts.confidence

    if in_order:
        state.last_captured_at = t
        state.frames_analyzed += 1
        # Camera covered / too dark: sustained -> camera_blocked (vision).
        blocked_rule = rules[T.CAMERA_BLOCKED]
        if blocked and confidence >= blocked_rule.min_confidence:
            reason = facts.blocked_reason or "obstructed"
            if state.obstructed is None:
                state.obstructed = Streak(start=t, last=t, kind="obstructed", max_confidence=confidence)
            else:
                state.obstructed.extend(t, confidence)
            streak = state.obstructed
            step.signals.append(
                make(
                    T.CAMERA_BLOCKED,
                    streak.start,
                    confidence=confidence,
                    duration_ms=streak.duration_ms,
                    streak_id=streak.streak_id,
                    duration_mode="max",
                    details={"reason": reason, "frames": streak.frames},
                )
            )
        elif not blocked:
            state.obstructed = None
        if blocked:
            # Nothing about people can be seen: person streaks are interrupted, not extended.
            state.absence = state.multi = state.partial = None
        else:
            # Absence: no_person_visible (never seen yet) / person_left_frame (seen before).
            absence_type = T.PERSON_LEFT_FRAME if state.seen_person else T.NO_PERSON_VISIBLE
            if absent:
                kind = (state.absence.kind if state.absence is not None else None) or absence_type.value
                rule = rules[IntegrityEventType(kind)]
                if confidence >= rule.min_confidence:
                    if state.absence is None:
                        state.absence = Streak(start=t, last=t, kind=kind, max_confidence=confidence)
                    else:
                        state.absence.extend(t, confidence)
                    streak = state.absence
                    # Without the vision model, the face detector alone needs consecutive frames.
                    if facts.vision or streak.frames >= FACE_ONLY_ABSENCE_FRAMES:
                        step.signals.append(
                            make(
                                IntegrityEventType(kind),
                                streak.start,
                                confidence=confidence,
                                duration_ms=streak.duration_ms,
                                streak_id=streak.streak_id,
                                duration_mode="max",
                                details={"frames": streak.frames, "basis": facts.basis},
                            )
                        )
                # low-confidence "nobody there" frames neither extend nor end a streak
            else:
                state.absence = None
                if facts.present:
                    state.seen_person = True

            # More than one person in consecutive confident frames.
            multi_rule = rules[T.MULTIPLE_PERSONS_VISIBLE]
            if facts.multi and facts.multi_confidence >= multi_rule.min_confidence:
                if state.multi is None:
                    state.multi = Streak(start=t, last=t, kind="multi", max_confidence=facts.multi_confidence)
                else:
                    state.multi.extend(t, facts.multi_confidence)
                streak = state.multi
                if streak.frames >= multi_rule.min_occurrences:
                    step.signals.append(
                        make(
                            T.MULTIPLE_PERSONS_VISIBLE,
                            streak.start,
                            confidence=facts.multi_confidence,
                            duration_ms=streak.duration_ms,
                            streak_id=streak.streak_id,
                            duration_mode="max",
                            occurrences_satisfied=True,
                            details={
                                "person_count": facts.people,
                                "consecutive_frames": streak.frames,
                                "basis": facts.basis,
                            },
                        )
                    )
            else:
                state.multi = None

            # A partially visible additional person in consecutive confident frames.
            partial_rule = rules[T.ANOTHER_PERSON_ENTERED]
            if facts.partial and confidence >= partial_rule.min_confidence:
                if state.partial is None:
                    state.partial = Streak(start=t, last=t, kind="partial", max_confidence=confidence)
                else:
                    state.partial.extend(t, confidence)
                streak = state.partial
                if streak.frames >= partial_rule.min_occurrences:
                    step.signals.append(
                        make(
                            T.ANOTHER_PERSON_ENTERED,
                            streak.start,
                            confidence=confidence,
                            duration_ms=streak.duration_ms,
                            streak_id=streak.streak_id,
                            duration_mode="max",
                            occurrences_satisfied=True,
                            details={"consecutive_frames": streak.frames},
                        )
                    )
            else:
                state.partial = None

    if obs is None:
        return step  # phones, photo-taking posture and gaze need the vision model
    # Order-independent, instantaneous observations (window counting in the rule engine).
    if not blocked and obs.phone_like_object_visible and obs.phone_confidence > 0:
        state.phone_frames += 1
        step.signals.append(make(T.POSSIBLE_PHONE_VISIBLE, t, confidence=obs.phone_confidence))
        if obs.phone_held_toward_screen:
            step.signals.append(
                make(
                    T.POSSIBLE_PHOTO_TAKING,
                    t,
                    confidence=min(obs.phone_confidence, obs.confidence),
                    details={"phone_held_toward_screen": True},
                )
            )
    # Gaze: recorded; evaluated last so same-frame strong signals count. The rule
    # engine drops it unless a stronger objective signal is in the window.
    if not blocked and not absent and obs.primary_person_looking_away:
        state.looking_away_frames += 1
        step.signals.append(
            make(
                T.LOOKING_AWAY_WITH_OTHER_SIGNALS,
                t,
                confidence=obs.confidence,
                details={"gaze_alone_never_flags": True},
            )
        )
    return step


def screen_signals(
    obs: ScreenObservation,
    captured_at: datetime,
    *,
    evidence_media_id: uuid.UUID | None = None,
) -> list[Signal]:
    """``screen_context_change`` only for a possibly prohibited context (the
    rule engine applies ``min_confidence``)."""
    if not obs.possible_prohibited_context:
        return []
    return [
        Signal(
            event_type=T.SCREEN_CONTEXT_CHANGE,
            source=IntegritySource.SCREEN,
            occurred_at=captured_at,
            confidence=obs.confidence,
            evidence_media_id=evidence_media_id,
            details={"observation": screen_summary(obs)},
        )
    ]
