"""Rule engine matrix: thresholds, sustained windows, escalation, policy
toggles, overrides, enforcement (vision never pauses/terminates), gaze alone
never flags, required screen share stop -> configured policy action."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity, IntegritySource
from app.schemas.policies import IntegrityPolicy, IntegrityRule
from app.services.integrity.rules import (
    CLIENT_EVENT_TYPES,
    SCREEN_ANALYSIS_TYPES,
    SERVER_ONLY_TYPES,
    VISION_TYPES,
    Signal,
    decide,
    default_rules,
    label_for,
    qualification,
    resolve_rules,
)

T = IntegrityEventType
A = IntegrityAction
S = IntegritySeverity
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def sig(event_type: T, source: IntegritySource = IntegritySource.BROWSER, **kw) -> Signal:
    return Signal(event_type=event_type, source=source, occurred_at=NOW, **kw)


def rules(**policy_kwargs):
    return resolve_rules(IntegrityPolicy(**policy_kwargs))


def test_every_event_type_has_a_rule_and_label() -> None:
    resolved = rules()
    assert set(resolved) == set(IntegrityEventType)
    for event_type in IntegrityEventType:
        assert label_for(event_type)


def test_source_partitions_are_disjoint_and_complete() -> None:
    assert VISION_TYPES.isdisjoint(CLIENT_EVENT_TYPES)
    assert SERVER_ONLY_TYPES.isdisjoint(CLIENT_EVENT_TYPES)
    assert SCREEN_ANALYSIS_TYPES.isdisjoint(CLIENT_EVENT_TYPES)
    assert VISION_TYPES | CLIENT_EVENT_TYPES | SERVER_ONLY_TYPES | SCREEN_ANALYSIS_TYPES == set(
        IntegrityEventType
    )


# --------------------------------------------------------------- thresholds


@pytest.mark.parametrize(
    ("duration_ms", "expected_action", "reason"),
    [
        (None, A.LOG_ONLY, "duration_unknown"),
        (1_000, A.LOG_ONLY, "below_duration"),
        (2_999, A.LOG_ONLY, "below_duration"),
        (3_000, A.WARN_CANDIDATE, "triggered"),
        (60_000, A.WARN_CANDIDATE, "triggered"),
    ],
)
def test_focus_lost_sustained_duration(duration_ms, expected_action, reason) -> None:
    rule = rules()[T.FOCUS_LOST]
    decision = decide(rule, sig(T.FOCUS_LOST, duration_ms=duration_ms))
    assert decision.persist  # browser facts are always logged
    assert decision.action is expected_action
    assert decision.reason == reason
    if expected_action is A.LOG_ONLY:
        assert decision.severity is S.INFO and decision.candidate_message is None
    else:
        assert decision.candidate_message == rule.candidate_message


@pytest.mark.parametrize(
    ("occurrences", "action", "severity", "reason"),
    [
        (1, A.WARN_CANDIDATE, S.LOW, "triggered"),
        (3, A.WARN_CANDIDATE, S.LOW, "triggered"),
        (4, A.FLAG_FOR_HUMAN_REVIEW, S.MEDIUM, "escalated"),
        (9, A.FLAG_FOR_HUMAN_REVIEW, S.MEDIUM, "escalated"),
    ],
)
def test_repeated_focus_loss_escalates_to_flag(occurrences, action, severity, reason) -> None:
    decision = decide(rules()[T.FOCUS_LOST], sig(T.FOCUS_LOST, duration_ms=5000), occurrences=occurrences)
    assert (decision.action, decision.severity, decision.reason) == (action, severity, reason)


def test_vision_below_confidence_is_not_recorded() -> None:
    rule = rules()[T.POSSIBLE_PHONE_VISIBLE]
    decision = decide(
        rule, sig(T.POSSIBLE_PHONE_VISIBLE, IntegritySource.VISION, confidence=0.5), occurrences=5
    )
    assert not decision.persist and decision.reason == "below_confidence"


@pytest.mark.parametrize(
    ("occurrences", "action"), [(1, A.LOG_ONLY), (2, A.FLAG_FOR_HUMAN_REVIEW), (3, A.FLAG_FOR_HUMAN_REVIEW)]
)
def test_phone_needs_two_sightings_in_window(occurrences, action) -> None:
    rule = rules()[T.POSSIBLE_PHONE_VISIBLE]
    assert (rule.min_occurrences, rule.window_seconds) == (2, 60)
    decision = decide(
        rule, sig(T.POSSIBLE_PHONE_VISIBLE, IntegritySource.VISION, confidence=0.74), occurrences=occurrences
    )
    assert decision.persist and decision.action is action
    assert decision.candidate_message is None  # never a candidate-facing accusation


@pytest.mark.parametrize(("confidence", "persist"), [(0.74, False), (0.75, True), (0.9, True)])
def test_photo_taking_requires_high_confidence(confidence, persist) -> None:
    decision = decide(
        rules()[T.POSSIBLE_PHOTO_TAKING],
        sig(T.POSSIBLE_PHOTO_TAKING, IntegritySource.VISION, confidence=confidence),
    )
    assert decision.persist is persist
    if persist:
        assert decision.action is A.FLAG_FOR_HUMAN_REVIEW and decision.severity is S.HIGH


@pytest.mark.parametrize(
    ("event_type", "seconds", "persist"),
    [
        (T.NO_PERSON_VISIBLE, 10, False),
        (T.NO_PERSON_VISIBLE, 19.9, False),
        (T.NO_PERSON_VISIBLE, 20, True),
        (T.PERSON_LEFT_FRAME, 20, False),
        (T.PERSON_LEFT_FRAME, 30, True),
        (T.CAMERA_BLOCKED, 10, False),
        (T.CAMERA_BLOCKED, 20, True),
    ],
)
def test_vision_sustained_thresholds(event_type, seconds, persist) -> None:
    decision = decide(
        rules()[event_type],
        sig(event_type, IntegritySource.VISION, confidence=0.9, duration_ms=int(seconds * 1000)),
    )
    assert decision.persist is persist
    if persist:
        assert decision.action is A.FLAG_FOR_HUMAN_REVIEW


def test_multiple_persons_consecutive_frames_flag() -> None:
    rule = rules()[T.MULTIPLE_PERSONS_VISIBLE]
    assert rule.min_confidence == 0.75 and rule.min_occurrences == 2
    decision = decide(
        rule,
        sig(T.MULTIPLE_PERSONS_VISIBLE, IntegritySource.VISION, confidence=0.8, occurrences_satisfied=True),
    )
    assert decision.action is A.FLAG_FOR_HUMAN_REVIEW and decision.severity is S.HIGH


# --------------------------------------------------------------------- gaze


@pytest.mark.parametrize("confidence", [0.6, 0.9, 1.0])
def test_gaze_alone_never_creates_an_event(confidence) -> None:
    rule = rules()[T.LOOKING_AWAY_WITH_OTHER_SIGNALS]
    decision = decide(
        rule,
        sig(T.LOOKING_AWAY_WITH_OTHER_SIGNALS, IntegritySource.VISION, confidence=confidence),
        occurrences=50,
        strong_signal_recent=False,
    )
    assert not decision.persist and decision.reason == "gaze_alone"


def test_gaze_with_strong_signal_is_flagged() -> None:
    decision = decide(
        rules()[T.LOOKING_AWAY_WITH_OTHER_SIGNALS],
        sig(T.LOOKING_AWAY_WITH_OTHER_SIGNALS, IntegritySource.VISION, confidence=0.8),
        strong_signal_recent=True,
    )
    assert decision.persist and decision.action is A.FLAG_FOR_HUMAN_REVIEW and decision.severity is S.LOW


# -------------------------------------------------------------- enforcement


@pytest.mark.parametrize("override_action", [A.PAUSE_INTERVIEW, A.TERMINATE_INTERVIEW])
@pytest.mark.parametrize(
    ("event_type", "source"),
    [
        (T.MULTIPLE_PERSONS_VISIBLE, IntegritySource.VISION),
        (T.POSSIBLE_PHONE_VISIBLE, IntegritySource.VISION),
        (T.NO_PERSON_VISIBLE, IntegritySource.VISION),
        (T.SCREEN_CONTEXT_CHANGE, IntegritySource.SCREEN),
    ],
)
def test_vision_and_screen_never_pause_or_terminate(event_type, source, override_action) -> None:
    policy = IntegrityPolicy(
        screen_share_analysis=True,
        rules=[
            IntegrityRule(
                event_type=event_type,
                action=override_action,
                min_confidence=0.1,
                min_duration_seconds=0,
                min_occurrences=1,
            )
        ],
    )
    rule = resolve_rules(policy)[event_type]
    assert rule.action is A.FLAG_FOR_HUMAN_REVIEW and rule.downgraded_from is override_action
    decision = decide(
        rule, sig(event_type, source, confidence=0.99, duration_ms=60_000, occurrences_satisfied=True)
    )
    assert decision.action is A.FLAG_FOR_HUMAN_REVIEW
    assert decision.downgraded_from is override_action


def test_vision_sourced_camera_blocked_cannot_terminate_but_browser_can() -> None:
    policy = IntegrityPolicy(
        rules=[
            IntegrityRule(event_type=T.CAMERA_BLOCKED, action=A.TERMINATE_INTERVIEW, min_duration_seconds=0)
        ]
    )
    rule = resolve_rules(policy)[T.CAMERA_BLOCKED]
    assert rule.action is A.TERMINATE_INTERVIEW  # objective, recruiter-configured for browser signals
    vision = decide(rule, sig(T.CAMERA_BLOCKED, IntegritySource.VISION, confidence=0.99))
    browser = decide(rule, sig(T.CAMERA_BLOCKED, IntegritySource.BROWSER))
    assert vision.action is A.FLAG_FOR_HUMAN_REVIEW and vision.downgraded_from is A.TERMINATE_INTERVIEW
    assert browser.action is A.TERMINATE_INTERVIEW and browser.severity is S.HIGH


# --------------------------------------------------------------- screen share


@pytest.mark.parametrize(
    ("policy_kwargs", "action", "severity"),
    [
        (
            {"screen_share_required": True, "terminate_on_required_screen_share_stop": True},
            A.TERMINATE_INTERVIEW,
            S.HIGH,
        ),
        (
            {"screen_share_required": True, "terminate_on_required_screen_share_stop": False},
            A.PAUSE_INTERVIEW,
            S.HIGH,
        ),
        ({"screen_share_required": False, "screen_share_analysis": True}, A.LOG_ONLY, S.INFO),
    ],
)
def test_required_screen_share_stop_maps_to_policy_action(policy_kwargs, action, severity) -> None:
    rule = rules(**policy_kwargs)[T.SCREEN_SHARE_STOPPED]
    decision = decide(rule, sig(T.SCREEN_SHARE_STOPPED))
    assert decision.persist and decision.action is action and decision.severity is severity
    if action in (A.TERMINATE_INTERVIEW, A.PAUSE_INTERVIEW):
        assert decision.candidate_message


def test_screen_share_not_used_disables_share_events() -> None:
    rule = rules(screen_share_required=False, screen_share_analysis=False)[T.SCREEN_SHARE_STOPPED]
    assert not rule.enabled
    assert not decide(rule, sig(T.SCREEN_SHARE_STOPPED)).persist


def test_screen_context_change_threshold() -> None:
    rule = rules(screen_share_analysis=True)[T.SCREEN_CONTEXT_CHANGE]
    low = decide(rule, sig(T.SCREEN_CONTEXT_CHANGE, IntegritySource.SCREEN, confidence=0.6))
    high = decide(rule, sig(T.SCREEN_CONTEXT_CHANGE, IntegritySource.SCREEN, confidence=0.8))
    assert not low.persist
    assert high.persist and high.action is A.FLAG_FOR_HUMAN_REVIEW


# ------------------------------------------------------------ policy toggles


@pytest.mark.parametrize(
    ("toggle", "types"),
    [
        ({"monitor_clipboard": False}, [T.COPY_EVENT, T.PASTE_EVENT, T.LARGE_PASTE_IN_CODING]),
        ({"monitor_focus": False}, [T.FOCUS_LOST, T.TAB_HIDDEN, T.WINDOW_MINIMIZED, T.RAPID_FOCUS_SWITCHING]),
        ({"require_fullscreen": False}, [T.FULLSCREEN_EXITED, T.FULLSCREEN_DECLINED]),
        ({"monitor_shortcuts": False}, [T.PROHIBITED_SHORTCUT, T.CONTEXT_MENU]),
        ({"camera_frame_analysis": False}, sorted(VISION_TYPES)),
        ({"screen_share_analysis": False}, [T.SCREEN_CONTEXT_CHANGE]),
        ({"enabled": False}, [T.FOCUS_LOST, T.PASTE_EVENT, T.NO_PERSON_VISIBLE, T.FULLSCREEN_EXITED]),
    ],
)
def test_toggles_disable_rules_and_overrides_cannot_reenable(toggle, types) -> None:
    overrides = [IntegrityRule(event_type=t, enabled=True) for t in types]
    resolved = resolve_rules(IntegrityPolicy(**toggle, rules=overrides))
    for event_type in types:
        assert not resolved[event_type].enabled, event_type
        assert resolved[event_type].disabled_reason


def test_server_events_recorded_even_when_monitoring_disabled() -> None:
    resolved = rules(enabled=False)
    for event_type in (T.DISCONNECT, T.RECONNECT, T.PAGE_RELOAD, T.UNUSUAL_DISCONNECT_PATTERN):
        assert resolved[event_type].enabled
        assert resolved[event_type].action in (A.LOG_ONLY,)


def test_page_reload_flags_when_resume_not_allowed() -> None:
    assert rules(allow_page_reload_resume=True)[T.PAGE_RELOAD].action is A.LOG_ONLY
    assert rules(allow_page_reload_resume=False)[T.PAGE_RELOAD].action is A.FLAG_FOR_HUMAN_REVIEW


def test_allowed_reloads_and_mic_mutes_never_escalate_to_a_flag() -> None:
    """S22: allowed reloads, reloads while monitoring is off and microphone mutes are never review flags."""
    for reload_rule in (
        rules(allow_page_reload_resume=True)[T.PAGE_RELOAD],
        rules(enabled=False, allow_page_reload_resume=False)[T.PAGE_RELOAD],
    ):
        assert reload_rule.action is A.LOG_ONLY and reload_rule.escalate_after is None
    mic = rules()[T.MICROPHONE_DISABLED]
    assert mic.action is A.WARN_CANDIDATE and mic.escalate_after is None and mic.candidate_message


def test_camera_events_only_flag_when_camera_required() -> None:
    assert rules(camera_required=True)[T.CAMERA_DISABLED].action is A.FLAG_FOR_HUMAN_REVIEW
    assert rules(camera_required=False, camera_frame_analysis=True)[T.CAMERA_DISABLED].action is A.LOG_ONLY
    assert not rules(camera_required=False, camera_frame_analysis=False)[T.CAMERA_DISABLED].enabled


# --------------------------------------------------------------- overrides


def test_partial_override_keeps_other_defaults() -> None:
    policy = IntegrityPolicy.model_validate(
        {"rules": [{"event_type": "focus_lost", "min_duration_seconds": 10}]}
    )
    rule = resolve_rules(policy)[T.FOCUS_LOST]
    default = default_rules(IntegrityPolicy())[T.FOCUS_LOST]
    assert rule.min_duration_seconds == 10 and rule.overridden
    assert rule.action is default.action and rule.candidate_message == default.candidate_message
    assert decide(rule, sig(T.FOCUS_LOST, duration_ms=9000)).action is A.LOG_ONLY
    assert decide(rule, sig(T.FOCUS_LOST, duration_ms=10_000)).action is A.WARN_CANDIDATE


def test_override_action_replaces_escalation_and_disable_works() -> None:
    policy = IntegrityPolicy(
        rules=[
            IntegrityRule(event_type=T.FULLSCREEN_EXITED, action=A.PAUSE_INTERVIEW),
            IntegrityRule(event_type=T.COPY_EVENT, enabled=False),
        ]
    )
    resolved = resolve_rules(policy)
    fullscreen = resolved[T.FULLSCREEN_EXITED]
    assert (
        fullscreen.action is A.PAUSE_INTERVIEW
        and fullscreen.escalate_after is None
        and fullscreen.severity is S.HIGH
    )
    assert decide(fullscreen, sig(T.FULLSCREEN_EXITED), occurrences=10).action is A.PAUSE_INTERVIEW
    assert not resolved[T.COPY_EVENT].enabled
    assert not decide(resolved[T.COPY_EVENT], sig(T.COPY_EVENT)).persist


# --------------------------------------------------------------- warnings


def test_warn_candidate_false_suppresses_messages_and_warn_actions() -> None:
    resolved = rules(warn_candidate=False)
    warn = decide(resolved[T.FULLSCREEN_EXITED], sig(T.FULLSCREEN_EXITED), warn_candidate=False)
    assert warn.action is A.LOG_ONLY and warn.candidate_message is None
    flagged = decide(
        resolved[T.NO_PERSON_VISIBLE],
        sig(T.NO_PERSON_VISIBLE, IntegritySource.VISION, confidence=0.9, duration_ms=25_000),
        warn_candidate=False,
    )
    assert flagged.action is A.FLAG_FOR_HUMAN_REVIEW and flagged.candidate_message is None
    escalated = decide(
        resolved[T.FULLSCREEN_EXITED], sig(T.FULLSCREEN_EXITED), occurrences=3, warn_candidate=False
    )
    assert escalated.action is A.FLAG_FOR_HUMAN_REVIEW and escalated.candidate_message is None


def test_messages_only_when_rule_asks() -> None:
    resolved = rules()
    paste = decide(resolved[T.LARGE_PASTE_IN_CODING], sig(T.LARGE_PASTE_IN_CODING))
    assert paste.action is A.FLAG_FOR_HUMAN_REVIEW and paste.candidate_message is None
    copy = decide(resolved[T.COPY_EVENT], sig(T.COPY_EVENT))
    assert copy.action is A.LOG_ONLY and copy.candidate_message is None


def test_qualification_reasons() -> None:
    rule = rules()[T.NO_PERSON_VISIBLE]
    assert (
        qualification(
            rule, sig(T.NO_PERSON_VISIBLE, IntegritySource.VISION, confidence=0.5, duration_ms=30_000)
        )
        == "below_confidence"
    )
    assert (
        qualification(rule, sig(T.NO_PERSON_VISIBLE, IntegritySource.VISION, confidence=0.9))
        == "duration_unknown"
    )
    assert (
        qualification(rule, sig(T.NO_PERSON_VISIBLE, IntegritySource.VISION, confidence=0.9, duration_ms=1))
        == "below_duration"
    )
    assert (
        qualification(
            rule, sig(T.NO_PERSON_VISIBLE, IntegritySource.VISION, confidence=0.9, duration_ms=20_000)
        )
        is None
    )


def test_disabled_rule_drops_signal() -> None:
    assert not decide(None, sig(T.COPY_EVENT)).persist
