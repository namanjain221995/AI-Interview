"""Face-check rules: toggles, never pause/terminate, confirmation and duration
escalation, normalisation of on-device face signals."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity, IntegritySource
from app.schemas.policies import IntegrityPolicy, IntegrityRule
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity.normalize import client_signal, face_episode
from app.services.integrity.rules import (
    CLIENT_EVENT_TYPES,
    FACE_CHECK_TYPES,
    NON_TERMINATING_TYPES,
    Signal,
    decide,
    enforce_action,
    label_for,
    resolve_rules,
    rules_summary,
)

T = IntegrityEventType
A = IntegrityAction
S = IntegritySeverity
NOW = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def signal(event_type: T, duration_ms: int | None, **details) -> Signal:
    return Signal(
        event_type=event_type,
        source=IntegritySource.BROWSER,
        occurred_at=NOW,
        duration_ms=duration_ms,
        details=details,
        episode="ep_0001",
        duration_mode="episode",
    )


def test_face_types_are_browser_types_that_never_terminate() -> None:
    assert FACE_CHECK_TYPES <= CLIENT_EVENT_TYPES
    assert FACE_CHECK_TYPES <= NON_TERMINATING_TYPES
    assert label_for(T.MULTIPLE_FACES_VISIBLE) == "More than one face visible (face check)"


def test_defaults_follow_the_camera_and_the_policy_toggle() -> None:
    on = resolve_rules(IntegrityPolicy())
    assert on[T.FACE_NOT_VISIBLE].enabled and on[T.FACE_NOT_VISIBLE].min_duration_seconds == 5
    assert on[T.MULTIPLE_FACES_VISIBLE].min_duration_seconds == 3
    assert on[T.MULTIPLE_FACES_VISIBLE].confirmed_action is A.FLAG_FOR_HUMAN_REVIEW
    assert on[T.STATIC_FACE_IGNORED].action is A.LOG_ONLY and on[T.FACE_CHECK_SUMMARY].action is A.LOG_ONLY
    toggled = resolve_rules(IntegrityPolicy(face_presence_check=False))
    assert not any(toggled[t].enabled for t in FACE_CHECK_TYPES)
    assert toggled[T.FACE_NOT_VISIBLE].disabled_reason == "policy.face_presence_check is false"
    no_camera = resolve_rules(IntegrityPolicy(camera_required=False, camera_frame_analysis=False))
    assert not no_camera[T.MULTIPLE_FACES_VISIBLE].enabled
    assert no_camera[T.MULTIPLE_FACES_VISIBLE].disabled_reason == "the camera is not used"
    off = resolve_rules(IntegrityPolicy(enabled=False))
    assert off[T.FACE_NOT_VISIBLE].disabled_reason == "integrity monitoring disabled"
    # Overrides cannot re-enable a face check the policy switched off.
    override = IntegrityRule(event_type=T.FACE_NOT_VISIBLE, enabled=True)
    assert not resolve_rules(IntegrityPolicy(face_presence_check=False, rules=[override]))[
        T.FACE_NOT_VISIBLE
    ].enabled
    summary = rules_summary(on)
    assert summary["multiple_faces_visible"]["escalate_after_seconds"] == 15


@pytest.mark.parametrize("action", [A.PAUSE_INTERVIEW, A.TERMINATE_INTERVIEW])
@pytest.mark.parametrize("event_type", sorted(FACE_CHECK_TYPES))
def test_face_signals_never_pause_or_terminate(event_type: T, action: A) -> None:
    rules = resolve_rules(IntegrityPolicy(rules=[IntegrityRule(event_type=event_type, action=action)]))
    rule = rules[event_type]
    assert rule.action is A.FLAG_FOR_HUMAN_REVIEW and rule.downgraded_from is action
    decision = decide(rule, signal(event_type, 60_000, server_check={"status": "confirmed"}))
    assert decision.action not in (A.PAUSE_INTERVIEW, A.TERMINATE_INTERVIEW)
    assert enforce_action(IntegritySource.BROWSER, action, event_type) == (A.FLAG_FOR_HUMAN_REVIEW, action)


def test_multiple_faces_warns_then_is_flagged_once_the_server_confirms() -> None:
    rule = resolve_rules(IntegrityPolicy())[T.MULTIPLE_FACES_VISIBLE]
    below = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 2000))
    assert below.persist and below.action is A.LOG_ONLY and below.reason == "below_duration"
    warned = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 3500))
    assert warned.action is A.WARN_CANDIDATE and warned.severity is S.MEDIUM
    assert warned.candidate_message == "Please make sure only you are visible on camera during the interview."
    pending = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 3500, server_check={"status": "pending"}))
    assert pending.action is A.WARN_CANDIDATE
    confirmed = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 3500, server_check={"status": "confirmed"}))
    assert confirmed.action is A.FLAG_FOR_HUMAN_REVIEW and confirmed.reason == "confirmed"
    assert confirmed.severity is S.HIGH
    # A long interval is flagged even without a snapshot...
    long = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 16_000))
    assert long.action is A.FLAG_FOR_HUMAN_REVIEW and long.reason == "escalated"
    # ...unless the server check of its snapshot contradicted the device.
    contradicted = decide(
        rule, signal(T.MULTIPLE_FACES_VISIBLE, 60_000, server_check={"status": "not_confirmed"})
    )
    assert contradicted.action is A.WARN_CANDIDATE


def test_no_face_is_a_reminder_and_only_a_long_interval_is_flagged() -> None:
    rule = resolve_rules(IntegrityPolicy())[T.FACE_NOT_VISIBLE]
    assert decide(rule, signal(T.FACE_NOT_VISIBLE, 4000)).action is A.LOG_ONLY
    short = decide(rule, signal(T.FACE_NOT_VISIBLE, 6000, server_check={"status": "confirmed"}))
    assert short.action is A.WARN_CANDIDATE  # confirmation alone does not flag an absence
    assert short.candidate_message and "face" in short.candidate_message
    long = decide(rule, signal(T.FACE_NOT_VISIBLE, 31_000, server_check={"status": "confirmed"}))
    assert long.action is A.FLAG_FOR_HUMAN_REVIEW and long.severity is S.MEDIUM
    seen = decide(rule, signal(T.FACE_NOT_VISIBLE, 90_000, server_check={"status": "not_confirmed"}))
    assert seen.action is A.WARN_CANDIDATE


def test_no_candidate_warnings_when_the_policy_turns_them_off() -> None:
    rule = resolve_rules(IntegrityPolicy(warn_candidate=False))[T.MULTIPLE_FACES_VISIBLE]
    decision = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 4000), warn_candidate=False)
    assert decision.action is A.LOG_ONLY and decision.candidate_message is None
    flagged = decide(
        rule,
        signal(T.MULTIPLE_FACES_VISIBLE, 4000, server_check={"status": "confirmed"}),
        warn_candidate=False,
    )
    assert flagged.action is A.FLAG_FOR_HUMAN_REVIEW and flagged.candidate_message is None


def test_recruiter_chosen_action_replaces_face_escalation() -> None:
    rules = resolve_rules(
        IntegrityPolicy(rules=[IntegrityRule(event_type=T.MULTIPLE_FACES_VISIBLE, action=A.LOG_ONLY)])
    )
    rule = rules[T.MULTIPLE_FACES_VISIBLE]
    assert rule.confirmed_action is None and rule.escalate_after_seconds is None
    decision = decide(rule, signal(T.MULTIPLE_FACES_VISIBLE, 60_000, server_check={"status": "confirmed"}))
    assert decision.action is A.LOG_ONLY
    thresholds = resolve_rules(
        IntegrityPolicy(rules=[IntegrityRule(event_type=T.FACE_NOT_VISIBLE, min_duration_seconds=12)])
    )[T.FACE_NOT_VISIBLE]
    assert thresholds.min_duration_seconds == 12 and thresholds.escalate_after_seconds == 30
    assert decide(thresholds, signal(T.FACE_NOT_VISIBLE, 8000)).reason == "below_duration"


def client(event_type: str, **details) -> ClientIntegrityEvent:
    return ClientIntegrityEvent(
        event_type=event_type, occurred_at=NOW.isoformat(), duration_ms=5200, details=details
    )


def test_client_face_signals_carry_their_episode_and_never_the_server_check() -> None:
    sig = client_signal(
        client(
            "multiple_faces_visible",
            episode="mf_k2j3h4",
            phase="start",
            faces=2,
            server_check="confirmed",
            yaw=35.0,
            gaze="left",
        ),
        now=NOW,
    )
    assert sig.episode == "mf_k2j3h4" and sig.duration_mode == "episode" and sig.duration_ms == 5200
    assert sig.details == {"episode": "mf_k2j3h4", "phase": "start", "faces": 2}
    summary = client_signal(client("face_check_summary", run="run_abcd", monitored_ms=60000), now=NOW)
    assert summary.episode == "run_abcd" and summary.duration_mode == "summary"
    plain = client_signal(client("tab_hidden", episode="ep_12345"), now=NOW)
    assert plain.episode is None and plain.duration_mode == "add"
    invalid = client_signal(client("face_not_visible", episode="bad id!"), now=NOW)
    assert invalid.episode is None and invalid.duration_mode == "add"
    assert face_episode({"run": "r_1234"}) == "r_1234" and face_episode({"episode": 5}) is None
