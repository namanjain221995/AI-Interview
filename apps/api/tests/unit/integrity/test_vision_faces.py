"""Vision observation fused with the deterministic face detector: posters and
drawn figures no longer count as people on the vision model's word alone,
"nobody visible" needs both to agree, and the face detector carries on alone
when no vision observation exists."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.enums import IntegrityAction, IntegrityEventType
from app.schemas.ai import DetectedFace, FaceCheckObservation, FrameObservation
from app.schemas.policies import IntegrityPolicy
from app.services.integrity.rules import decide, qualification, resolve_rules
from app.services.integrity.vision import (
    VISION_HIGH_CONFIDENCE,
    CameraVisionState,
    advance_camera_state,
    camera_facts,
    observation_summary,
)

T = IntegrityEventType
A = IntegrityAction
T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
RULES = resolve_rules(IntegrityPolicy())


def obs(**kw) -> FrameObservation:
    base = {"person_count": 1, "primary_face_visible": True, "confidence": 0.9}
    base.update(kw)
    return FrameObservation(**base)


def faces(*specs: tuple[float, bool] | tuple[float, bool, bool]) -> FaceCheckObservation:
    """``(score, static[, moving])`` per face; non-static faces move by default."""
    detected = []
    for i, spec in enumerate(specs):
        score, static = spec[0], spec[1]
        moving = spec[2] if len(spec) > 2 else not static
        detected.append(
            DetectedFace(box=(0.1 + 0.3 * i, 0.3, 0.12, 0.2), score=score, static=static, moving=moving)
        )
    static_count = sum(1 for d in detected if d.static)
    return FaceCheckObservation(
        face_count=len(detected),
        counted_faces=len(detected) - static_count,
        static_faces=static_count,
        live_faces=sum(1 for d in detected if d.moving),
        faces=detected,
    )


class Run:
    def __init__(self) -> None:
        self.state = CameraVisionState()
        self.windows: dict[str, list[float]] = {}

    def frame(
        self, seconds: float, observation: FrameObservation | None, detected: FaceCheckObservation | None
    ) -> list[tuple[T, A]]:
        step = advance_camera_state(
            self.state,
            observation,
            T0 + timedelta(seconds=seconds),
            RULES,
            evidence_media_id=uuid.uuid4(),
            faces=detected,
        )
        out = []
        for signal in step.signals:
            rule = RULES[signal.event_type]
            occurrences = 1
            if qualification(rule, signal) is None:
                bucket = self.windows.setdefault(signal.event_type.value, [])
                t = signal.occurred_at.timestamp()
                bucket.append(t)
                occurrences = sum(1 for x in bucket if t - rule.window_seconds <= x)
            decision = decide(rule, signal, occurrences=occurrences)
            if decision.persist:
                out.append((signal.event_type, decision.action))
        return out


def test_a_poster_the_model_counts_as_a_person_is_not_a_second_person() -> None:
    run = Run()
    vision = obs(person_count=2, confidence=0.95)
    detected = faces((0.94, False), (0.92, True))  # the candidate + a motionless print
    assert run.frame(0, vision, detected) == []
    assert run.frame(15, vision, detected) == []
    facts = camera_facts(vision, detected, RULES)
    assert not facts.multi and facts.people == 1


def test_drawn_figures_need_a_very_confident_model_when_no_face_agrees() -> None:
    run = Run()
    unsure = obs(person_count=2, confidence=0.85)
    assert run.frame(0, unsure, faces()) == [] and run.frame(15, unsure, faces()) == []
    sure = obs(person_count=2, confidence=VISION_HIGH_CONFIDENCE)
    run2 = Run()
    run2.frame(0, sure, faces())
    assert run2.frame(15, sure, faces()) == [(T.MULTIPLE_PERSONS_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]


def test_a_face_not_seen_moving_yet_is_not_a_second_person() -> None:
    # The first 30 s with a poster: not yet static, but it never moved either.
    run = Run()
    vision = obs(person_count=2, confidence=0.95)
    detected = faces((0.94, False, True), (0.92, False, False))
    assert run.frame(0, vision, detected) == [] and run.frame(15, vision, detected) == []
    assert run.frame(0, None, detected) == []
    # ...while it still counts as a face for "is anyone there".
    assert not camera_facts(
        obs(person_count=0, primary_face_visible=False), faces((0.9, False, False)), RULES
    ).absent


def test_two_live_faces_count_even_when_the_model_sees_one_person() -> None:
    run = Run()
    vision = obs(person_count=1, confidence=0.9)
    detected = faces((0.95, False), (0.81, False))
    assert run.frame(0, vision, detected) == []
    [(event_type, action)] = run.frame(15, vision, detected)
    assert (event_type, action) == (T.MULTIPLE_PERSONS_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)
    facts = camera_facts(vision, detected, RULES)
    assert facts.basis == "face_detector" and facts.multi_confidence == 0.81


def test_nobody_visible_needs_the_face_detector_and_the_model_to_agree() -> None:
    nobody = obs(person_count=0, primary_face_visible=False, confidence=0.9)
    # The model sees nobody but a face is detected: someone is there.
    assert not camera_facts(nobody, faces((0.9, False)), RULES).absent
    # No face (back turned) but the model sees a person: someone is there.
    assert not camera_facts(obs(person_count=1, primary_face_visible=False), faces(), RULES).absent
    # Only a motionless print remains and the model (wrongly) counts it: nobody is there.
    assert camera_facts(obs(person_count=1, primary_face_visible=True), faces((0.9, True)), RULES).absent
    run = Run()
    seen = [run.frame(s, nobody, faces()) for s in (0, 10, 20)]
    assert seen[-1] == [(T.NO_PERSON_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]


def test_without_the_model_the_face_detector_needs_consecutive_frames() -> None:
    run = Run()
    assert run.frame(0, None, faces()) == []  # one empty frame is never enough
    assert run.frame(15, None, faces()) == []  # 15 s < the 20 s rule
    assert run.frame(25, None, faces()) == [(T.NO_PERSON_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]
    run2 = Run()
    two = faces((0.93, False), (0.9, False))
    assert run2.frame(0, None, two) == []
    assert run2.frame(15, None, two) == [(T.MULTIPLE_PERSONS_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)]
    # Phones and gaze need the model: nothing else is produced from faces alone.
    assert {e for e, _ in run2.frame(30, None, two)} <= {T.MULTIPLE_PERSONS_VISIBLE}


def test_frames_without_any_observation_change_nothing() -> None:
    state = CameraVisionState()
    step = advance_camera_state(state, None, T0, RULES)
    assert step.signals == [] and state.frames_analyzed == 0


def test_the_stored_observation_keeps_the_face_counts() -> None:
    summary = observation_summary(obs(), faces((0.9, False), (0.8, True)))
    assert summary["faces"]["counted_faces"] == 1 and summary["faces"]["static_faces"] == 1
    assert observation_summary(None, faces())["vision"] == "unavailable"
    assert "faces" not in observation_summary(obs())
