"""Sustained-signal state machine for camera frames, end to end through the
rule engine (with an in-memory occurrence window)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySource
from app.schemas.ai import FrameObservation, ScreenObservation
from app.schemas.policies import IntegrityPolicy, IntegrityRule
from app.services.integrity.rules import STRONG_SIGNAL_TYPES, Signal, decide, qualification, resolve_rules
from app.services.integrity.vision import (
    CAMERA_PROMPT,
    SCREEN_PROMPT,
    CameraVisionState,
    advance_camera_state,
    screen_signals,
    vision_messages,
)

T = IntegrityEventType
A = IntegrityAction
T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def obs(**kw) -> FrameObservation:
    base = {"person_count": 1, "primary_face_visible": True, "confidence": 0.9}
    base.update(kw)
    return FrameObservation(**base)


class Harness:
    """State machine + rule engine with an in-memory window store."""

    def __init__(self, policy: IntegrityPolicy | None = None) -> None:
        self.rules = resolve_rules(policy or IntegrityPolicy())
        self.state = CameraVisionState()
        self.occ: dict[str, list[float]] = {}
        self.events: list[tuple[T, A, Signal]] = []

    def _count(self, name: str, t: float, window: float, add: bool) -> int:
        bucket = self.occ.setdefault(name, [])
        if add:
            bucket.append(t)
        return sum(1 for x in bucket if t - window <= x <= t + 0.001)

    def frame(self, seconds: float, observation: FrameObservation) -> list[tuple[T, A]]:
        at = T0 + timedelta(seconds=seconds)
        step = advance_camera_state(self.state, observation, at, self.rules, evidence_media_id=uuid.uuid4())
        out = []
        for signal in step.signals:
            rule = self.rules[signal.event_type]
            t = signal.occurred_at.timestamp()
            occurrences = 1
            gaze = signal.event_type is T.LOOKING_AWAY_WITH_OTHER_SIGNALS
            if qualification(rule, signal) is None and not gaze:
                occurrences = self._count(signal.event_type.value, t, rule.window_seconds, add=True)
            strong = gaze and self._count("strong", t, rule.window_seconds, add=False) > 0
            decision = decide(rule, signal, occurrences=occurrences, strong_signal_recent=strong)
            if decision.persist:
                if decision.qualifies and signal.event_type in STRONG_SIGNAL_TYPES:
                    self._count("strong", t, 3600, add=True)
                self.events.append((signal.event_type, decision.action, signal))
                out.append((signal.event_type, decision.action))
        return out


def test_no_person_sustained_20s_once_per_streak() -> None:
    h = Harness()
    empty = obs(person_count=0, primary_face_visible=False)
    assert h.frame(0, empty) == []
    assert h.frame(10, empty) == []
    assert h.frame(20, empty) == [(T.NO_PERSON_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]
    h.frame(30, empty)
    signals = [s for (_, _, s) in h.events]
    assert {s.streak_id for s in signals} == {signals[0].streak_id}  # one streak -> one aggregated event
    assert signals[-1].duration_ms == 30_000 and signals[-1].duration_mode == "max"
    assert signals[0].occurred_at == T0  # event starts when the absence started


def test_person_left_frame_after_being_seen_needs_30s() -> None:
    h = Harness()
    h.frame(0, obs())
    empty = obs(person_count=0, primary_face_visible=False)
    assert h.frame(10, empty) == []
    assert h.frame(30, empty) == []  # 20 s: not yet (person_left_frame threshold is 30 s)
    assert h.frame(40, empty) == [(T.PERSON_LEFT_FRAME, A.FLAG_FOR_HUMAN_REVIEW)]
    assert all(t is not T.NO_PERSON_VISIBLE for t, _, _ in h.events)


def test_absence_streak_resets_when_person_returns() -> None:
    h = Harness()
    empty = obs(person_count=0, primary_face_visible=False)
    h.frame(0, empty)
    h.frame(10, empty)
    h.frame(15, obs())  # back in view
    h.frame(25, empty)
    assert h.frame(35, empty) == []  # new streak, 10 s only
    assert h.events == []


def test_low_confidence_absence_frames_do_not_count() -> None:
    h = Harness()
    unsure = obs(person_count=0, primary_face_visible=False, confidence=0.3)
    for t in (0, 10, 20, 30, 40):
        assert h.frame(t, unsure) == []
    assert h.state.absence is None


def test_multiple_persons_two_consecutive_confident_frames() -> None:
    h = Harness()
    two = obs(person_count=2, confidence=0.8)
    assert h.frame(0, two) == []
    assert h.frame(10, two) == [(T.MULTIPLE_PERSONS_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]


def test_multiple_persons_not_consecutive_or_low_confidence_never_flag() -> None:
    h = Harness()
    two = obs(person_count=2, confidence=0.8)
    h.frame(0, two)
    h.frame(10, obs())  # breaks the streak
    assert h.frame(20, two) == []
    h2 = Harness()
    weak = obs(person_count=2, confidence=0.7)
    for t in (0, 10, 20, 30):
        assert h2.frame(t, weak) == []


def test_override_requires_three_consecutive_frames() -> None:
    policy = IntegrityPolicy(rules=[IntegrityRule(event_type=T.MULTIPLE_PERSONS_VISIBLE, min_occurrences=3)])
    h = Harness(policy)
    two = obs(person_count=2, confidence=0.9)
    assert h.frame(0, two) == [] and h.frame(10, two) == []
    assert h.frame(20, two) == [(T.MULTIPLE_PERSONS_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]


def test_another_person_partially_visible() -> None:
    h = Harness()
    partial = obs(additional_person_partially_visible=True, confidence=0.8)
    assert h.frame(0, partial) == []
    assert h.frame(10, partial) == [(T.ANOTHER_PERSON_ENTERED, A.FLAG_FOR_HUMAN_REVIEW)]


def test_phone_two_sightings_within_60s() -> None:
    h = Harness()
    phone = obs(phone_like_object_visible=True, phone_confidence=0.74)
    assert h.frame(0, phone) == [(T.POSSIBLE_PHONE_VISIBLE, A.LOG_ONLY)]
    assert h.frame(30, phone) == [(T.POSSIBLE_PHONE_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]
    h2 = Harness()
    h2.frame(0, phone)
    assert h2.frame(90, phone) == [(T.POSSIBLE_PHONE_VISIBLE, A.LOG_ONLY)]  # outside the 60 s window


def test_photo_taking_requires_held_toward_screen_and_075() -> None:
    h = Harness()
    held_weak = obs(phone_like_object_visible=True, phone_confidence=0.7, phone_held_toward_screen=True)
    assert (T.POSSIBLE_PHOTO_TAKING, A.FLAG_FOR_HUMAN_REVIEW) not in h.frame(0, held_weak)
    held = obs(phone_like_object_visible=True, phone_confidence=0.85, phone_held_toward_screen=True)
    assert (T.POSSIBLE_PHOTO_TAKING, A.FLAG_FOR_HUMAN_REVIEW) in h.frame(100, held)
    not_held = obs(phone_like_object_visible=True, phone_confidence=0.95, phone_held_toward_screen=False)
    assert all(t is not T.POSSIBLE_PHOTO_TAKING for t, _ in h.frame(200, not_held))


def test_gaze_alone_never_flags_even_when_sustained() -> None:
    h = Harness()
    away = obs(primary_person_looking_away=True, confidence=0.95)
    for t in range(0, 600, 10):
        assert h.frame(t, away) == []
    assert h.state.looking_away_frames == 60  # recorded, not acted on


def test_gaze_with_phone_in_same_frame_is_flagged() -> None:
    h = Harness()
    frame = obs(primary_person_looking_away=True, phone_like_object_visible=True, phone_confidence=0.8)
    result = h.frame(0, frame)
    assert (T.LOOKING_AWAY_WITH_OTHER_SIGNALS, A.FLAG_FOR_HUMAN_REVIEW) in result
    # far outside the 60 s gaze window: gaze alone again
    assert h.frame(400, obs(primary_person_looking_away=True)) == []


def test_camera_obstructed_sustained_and_interrupts_person_streaks() -> None:
    h = Harness()
    h.frame(0, obs(person_count=0, primary_face_visible=False))
    covered = obs(person_count=0, primary_face_visible=False, camera_obstructed=True)
    assert h.frame(10, covered) == []
    assert h.state.absence is None  # cannot see: absence streak interrupted
    assert h.frame(20, covered) == []
    assert h.frame(30, covered) == [(T.CAMERA_BLOCKED, A.FLAG_FOR_HUMAN_REVIEW)]
    _, _, signal = h.events[-1]
    assert signal.source is IntegritySource.VISION and signal.details["reason"] == "obstructed"
    dark = Harness()
    too_dark = obs(person_count=0, primary_face_visible=False, image_too_dark=True)
    for t in (0, 10):
        dark.frame(t, too_dark)
    assert dark.frame(20, too_dark) == [(T.CAMERA_BLOCKED, A.FLAG_FOR_HUMAN_REVIEW)]
    assert dark.events[-1][2].details["reason"] == "too_dark"


def test_out_of_order_frame_only_contributes_instant_signals() -> None:
    h = Harness()
    h.frame(100, obs())
    empty_with_phone = obs(
        person_count=0, primary_face_visible=False, phone_like_object_visible=True, phone_confidence=0.9
    )
    result = h.frame(50, empty_with_phone)  # older than the last processed frame
    assert result == [(T.POSSIBLE_PHONE_VISIBLE, A.LOG_ONLY)]
    assert h.state.absence is None and h.state.last_captured_at == (T0 + timedelta(seconds=100)).timestamp()


def test_state_roundtrip() -> None:
    h = Harness()
    h.frame(0, obs(person_count=0, primary_face_visible=False))
    h.frame(5, obs(person_count=2, confidence=0.9))
    restored = CameraVisionState.from_dict(h.state.to_dict())
    assert restored == h.state
    assert CameraVisionState.from_dict(None) == CameraVisionState()
    assert CameraVisionState.from_dict({"absence": {"bogus": 1}}).absence is None


def test_screen_signals_only_for_prohibited_context() -> None:
    clean = ScreenObservation(active_context="code editor", interview_window_visible=True, confidence=0.9)
    assert screen_signals(clean, T0) == []
    bad = ScreenObservation(
        active_context="chat application",
        interview_window_visible=True,
        other_applications_visible=["AI chat assistant"],
        possible_prohibited_context=True,
        description="An AI chat assistant window is open next to the interview tab.",
        confidence=0.82,
    )
    [signal] = screen_signals(bad, T0, evidence_media_id=uuid.uuid4())
    assert signal.event_type is T.SCREEN_CONTEXT_CHANGE and signal.source is IntegritySource.SCREEN
    rules = resolve_rules(IntegrityPolicy(screen_share_analysis=True))
    assert decide(rules[T.SCREEN_CONTEXT_CHANGE], signal).action is A.FLAG_FOR_HUMAN_REVIEW


def test_prompts_are_objective_only_and_include_fairness_rules() -> None:
    for prompt in (CAMERA_PROMPT, SCREEN_PROMPT):
        text = prompt.render()
        assert "Fairness and evidence rules" in text
        assert "Never" in text
        assert prompt.version.startswith("integrity.")
    camera = CAMERA_PROMPT.render().lower()
    for forbidden_topic in ("appearance", "emotion", "ethnicity", "gender", "age"):
        assert forbidden_topic in camera  # explicitly prohibited in the instructions
    messages = vision_messages(CAMERA_PROMPT, "describe", b"\xff\xd8\xffdata")
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
