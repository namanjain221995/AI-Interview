"""Integrity rule engine (pure functions, no I/O).

Every integrity signal is an *observation for a human reviewer*, never a
verdict. This module decides, for one signal, whether it is recorded at all,
which action it maps to and whether the candidate sees a gentle reminder.

Resolution order for the effective rule of an event type:

1. Built-in defaults (:func:`default_rules`) derived from the frozen
   :class:`~app.schemas.policies.IntegrityPolicy` toggles. A toggle that turns
   a kind of monitoring off (e.g. ``monitor_clipboard=False``) disables the
   matching rules entirely: what was not disclosed is not recorded.
2. Recruiter overrides from ``IntegrityPolicy.rules`` (only the fields present
   in the override replace the default; a fully dumped override replaces all
   configurable fields). Overrides can never re-enable a type the toggles
   turned off.
3. Enforcement: VISION/SCREEN sources and the face-check types can never
   produce PAUSE/TERMINATE (they are downgraded to FLAG_FOR_HUMAN_REVIEW), and
   gaze alone never creates an event (``looking_away_with_other_signals`` needs
   a stronger objective signal in the same window).

Face presence (on-device face check): the browser reports ``face_not_visible``
/ ``multiple_faces_visible`` intervals once they persisted (hysteresis) and
sends one snapshot when an interval starts; the server's deterministic face
detector checks that snapshot (``details.server_check.status``). A confirmed
observation can raise the action to ``confirmed_action`` (a flag for human
review), a long one (``escalate_after_seconds``) is flagged unless the server
check contradicted it. Neither can ever pause, end or fail an interview.

Thresholds per rule: ``min_confidence`` (model confidence; browser/server
signals carry none and pass), ``min_duration_seconds`` (sustained signals; a
browser signal without a reported duration does not count as sustained),
``min_occurrences`` within ``window_seconds``. Below a threshold, browser and
server signals are still logged (action ``log_only``, severity ``info``) so
the session timeline is complete; vision/screen guesses below the confidence
or duration threshold are not recorded at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity, IntegritySource
from app.schemas.policies import IntegrityPolicy, IntegrityRule
from app.services.integrity.contracts import NON_TERMINATING_SOURCES

T = IntegrityEventType
A = IntegrityAction
S = IntegritySeverity

ACTION_RANK: dict[IntegrityAction, int] = {
    A.LOG_ONLY: 0,
    A.WARN_CANDIDATE: 1,
    A.FLAG_FOR_HUMAN_REVIEW: 2,
    A.PAUSE_INTERVIEW: 3,
    A.TERMINATE_INTERVIEW: 4,
}
SEVERITY_RANK: dict[IntegritySeverity, int] = {S.INFO: 0, S.LOW: 1, S.MEDIUM: 2, S.HIGH: 3}

# Actions a human reviewer should look at (the review queue's "actionable" filter).
REVIEWABLE_ACTIONS: frozenset[IntegrityAction] = frozenset(
    {A.FLAG_FOR_HUMAN_REVIEW, A.PAUSE_INTERVIEW, A.TERMINATE_INTERVIEW}
)
ACTIONABLE_ACTIONS: frozenset[IntegrityAction] = REVIEWABLE_ACTIONS | {A.WARN_CANDIDATE}
POLICY_ACTIONS: frozenset[IntegrityAction] = frozenset({A.PAUSE_INTERVIEW, A.TERMINATE_INTERVIEW})

# Produced only by the frame-analysis worker (never accepted from a browser).
VISION_TYPES: frozenset[IntegrityEventType] = frozenset(
    {
        T.NO_PERSON_VISIBLE,
        T.MULTIPLE_PERSONS_VISIBLE,
        T.PERSON_LEFT_FRAME,
        T.ANOTHER_PERSON_ENTERED,
        T.POSSIBLE_PHONE_VISIBLE,
        T.POSSIBLE_PHOTO_TAKING,
        T.LOOKING_AWAY_WITH_OTHER_SIGNALS,
    }
)
SCREEN_ANALYSIS_TYPES: frozenset[IntegrityEventType] = frozenset({T.SCREEN_CONTEXT_CHANGE})
# On-device face check (the browser's face detector; consent + policy required).
FACE_INTERVAL_TYPES: frozenset[IntegrityEventType] = frozenset({T.FACE_NOT_VISIBLE, T.MULTIPLE_FACES_VISIBLE})
FACE_CHECK_TYPES: frozenset[IntegrityEventType] = FACE_INTERVAL_TYPES | frozenset(
    {T.STATIC_FACE_IGNORED, T.FACE_CHECK_SUMMARY}
)
# Observed by the server (realtime runtime), never accepted from a browser.
SERVER_ONLY_TYPES: frozenset[IntegrityEventType] = frozenset(
    {T.DISCONNECT, T.RECONNECT, T.UNUSUAL_DISCONNECT_PATTERN}
)
# What a browser may report (page_reload is detected client-side; camera_blocked
# may come from the client's luminance check as well as from vision).
CLIENT_EVENT_TYPES: frozenset[IntegrityEventType] = (
    frozenset(IntegrityEventType) - VISION_TYPES - SCREEN_ANALYSIS_TYPES - SERVER_ONLY_TYPES
)
# Types the server may record through ``record_server_event``.
SERVER_EVENT_TYPES: frozenset[IntegrityEventType] = (
    frozenset(IntegrityEventType) - VISION_TYPES - SCREEN_ANALYSIS_TYPES
)

FOCUS_SWITCH_TYPES: frozenset[IntegrityEventType] = frozenset(
    {T.FOCUS_LOST, T.TAB_HIDDEN, T.WINDOW_MINIMIZED}
)
# Types that can never pause or end an interview, whatever the policy says:
# model-derived observations and the face check (a detector, not a verdict).
NON_TERMINATING_TYPES: frozenset[IntegrityEventType] = VISION_TYPES | SCREEN_ANALYSIS_TYPES | FACE_CHECK_TYPES
# Objective signals strong enough that a simultaneous "looking away" adds context.
STRONG_SIGNAL_TYPES: frozenset[IntegrityEventType] = frozenset(
    {
        T.POSSIBLE_PHONE_VISIBLE,
        T.POSSIBLE_PHOTO_TAKING,
        T.MULTIPLE_PERSONS_VISIBLE,
        T.ANOTHER_PERSON_ENTERED,
        T.TAB_HIDDEN,
        T.FOCUS_LOST,
        T.WINDOW_MINIMIZED,
        T.RAPID_FOCUS_SWITCHING,
        T.LARGE_PASTE_IN_CODING,
        T.SCREEN_CONTEXT_CHANGE,
        T.MULTIPLE_FACES_VISIBLE,
    }
)

LABELS: dict[IntegrityEventType, str] = {
    T.FULLSCREEN_EXITED: "Left fullscreen",
    T.FULLSCREEN_DECLINED: "Fullscreen declined",
    T.TAB_HIDDEN: "Interview tab hidden",
    T.FOCUS_LOST: "Interview window lost focus",
    T.RAPID_FOCUS_SWITCHING: "Rapid switching away from the interview",
    T.WINDOW_MINIMIZED: "Window minimized",
    T.COPY_EVENT: "Copy",
    T.PASTE_EVENT: "Paste",
    T.LARGE_PASTE_IN_CODING: "Large paste into the code editor",
    T.PROHIBITED_SHORTCUT: "Blocked keyboard shortcut",
    T.CONTEXT_MENU: "Right-click menu",
    T.PAGE_RELOAD: "Page reloaded",
    T.DISCONNECT: "Disconnected",
    T.RECONNECT: "Reconnected",
    T.UNUSUAL_DISCONNECT_PATTERN: "Unusual disconnect pattern",
    T.MULTIPLE_DISPLAYS_DETECTED: "More than one display connected",
    T.CAMERA_DISABLED: "Camera turned off",
    T.CAMERA_BLOCKED: "Camera covered or too dark",
    T.MICROPHONE_DISABLED: "Microphone turned off",
    T.NO_PERSON_VISIBLE: "No person visible on camera",
    T.MULTIPLE_PERSONS_VISIBLE: "More than one person visible",
    T.PERSON_LEFT_FRAME: "Person left the camera view",
    T.ANOTHER_PERSON_ENTERED: "Another person partially visible",
    T.POSSIBLE_PHONE_VISIBLE: "Possible phone visible",
    T.POSSIBLE_PHOTO_TAKING: "Possible phone pointed at the screen",
    T.LOOKING_AWAY_WITH_OTHER_SIGNALS: "Looking away together with other signals",
    T.SCREEN_SHARE_STARTED: "Screen sharing started",
    T.SCREEN_SHARE_STOPPED: "Screen sharing stopped",
    T.SCREEN_SHARE_NOT_FULL_SCREEN: "Shared surface is not the entire screen",
    T.SCREEN_CONTEXT_CHANGE: "Screen shows a possibly prohibited context",
    T.FACE_NOT_VISIBLE: "No face visible on camera (face check)",
    T.MULTIPLE_FACES_VISIBLE: "More than one face visible (face check)",
    T.STATIC_FACE_IGNORED: "Motionless face set aside (photo, poster or screen)",
    T.FACE_CHECK_SUMMARY: "Face check summary",
}

GENERIC_WARNING = "Please keep your attention on the interview window. Thank you."
LOG_ONLY_AGGREGATE_SECONDS = 300.0
DEFAULT_AGGREGATE_SECONDS = 120.0

# Derivation thresholds that are not part of IntegrityRule.
LARGE_PASTE_MIN_CHARS = 200
LARGE_PASTE_MIN_LINES = 8


def label_for(event_type: IntegrityEventType | str) -> str:
    try:
        return LABELS[IntegrityEventType(event_type)]
    except ValueError:
        return str(event_type).replace("_", " ").capitalize()


def default_source(event_type: IntegrityEventType) -> IntegritySource:
    if event_type in VISION_TYPES:
        return IntegritySource.VISION
    if event_type in SCREEN_ANALYSIS_TYPES:
        return IntegritySource.SCREEN
    if event_type in SERVER_ONLY_TYPES:
        return IntegritySource.SERVER
    return IntegritySource.BROWSER


@dataclass(frozen=True, slots=True)
class EffectiveRule:
    """Fully resolved handling of one event type for one interview."""

    event_type: IntegrityEventType
    enabled: bool
    severity: IntegritySeverity
    action: IntegrityAction
    min_confidence: float = 0.0
    min_duration_seconds: float = 0.0
    min_occurrences: int = 1
    window_seconds: float = 600.0
    candidate_message: str | None = None
    escalate_after: int | None = None  # occurrences in window that escalate to a flag
    escalate_severity: IntegritySeverity = S.MEDIUM
    aggregate_seconds: float = DEFAULT_AGGREGATE_SECONDS
    # Face check: action once the server's deterministic detector confirmed the
    # observation, and the duration after which it is flagged unless the server
    # check contradicted it.
    confirmed_action: IntegrityAction | None = None
    escalate_after_seconds: float | None = None
    overridden: bool = False
    disabled_reason: str | None = None
    downgraded_from: IntegrityAction | None = None

    def thresholds(self) -> dict[str, Any]:
        return {
            "min_confidence": self.min_confidence,
            "min_duration_seconds": self.min_duration_seconds,
            "min_occurrences": self.min_occurrences,
            "window_seconds": self.window_seconds,
        }


def _rule(
    event_type: IntegrityEventType,
    severity: IntegritySeverity,
    action: IntegrityAction,
    *,
    enabled: bool = True,
    disabled_reason: str | None = None,
    **kwargs: Any,
) -> EffectiveRule:
    return EffectiveRule(
        event_type=event_type,
        enabled=enabled,
        severity=severity,
        action=action,
        disabled_reason=None if enabled else disabled_reason,
        **kwargs,
    )


def default_rules(policy: IntegrityPolicy) -> dict[IntegrityEventType, EffectiveRule]:
    """Built-in rule per event type, shaped by the policy toggles."""
    on = policy.enabled
    fullscreen = on and policy.require_fullscreen
    focus = on and policy.monitor_focus
    clipboard = on and policy.monitor_clipboard
    shortcuts = on and policy.monitor_shortcuts
    camera_needed = on and (policy.camera_required or policy.camera_frame_analysis)
    vision = on and policy.camera_frame_analysis
    screen_share = on and (policy.screen_share_required or policy.screen_share_analysis)
    screen_analysis = on and policy.screen_share_analysis
    face_check = camera_needed and policy.face_presence_check
    off = "integrity monitoring disabled" if not on else None

    def why(flag: str) -> str:
        return off or f"policy.{flag} is false"

    required_share = policy.screen_share_required
    if policy.terminate_on_required_screen_share_stop:
        stop_action = A.TERMINATE_INTERVIEW
        stop_message = (
            "Screen sharing was stopped. As described in this interview's rules, the interview has ended."
        )
    else:
        stop_action = A.PAUSE_INTERVIEW
        stop_message = "Screen sharing is required for this interview. Please share your entire screen again to continue."
    reload_allowed = policy.allow_page_reload_resume
    reload_quiet = reload_allowed or not on
    rules = [
        # --- browser: fullscreen / focus ------------------------------------------------
        _rule(
            T.FULLSCREEN_EXITED,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=fullscreen,
            disabled_reason=why("require_fullscreen"),
            escalate_after=3,
            candidate_message="You've left fullscreen. Please return to fullscreen to continue your interview.",
        ),
        _rule(
            T.FULLSCREEN_DECLINED,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=fullscreen,
            disabled_reason=why("require_fullscreen"),
            escalate_after=2,
            candidate_message="This interview runs in fullscreen. Please allow fullscreen when you're ready.",
        ),
        _rule(
            T.TAB_HIDDEN,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=focus,
            disabled_reason=why("monitor_focus"),
            min_duration_seconds=2,
            escalate_after=3,
            candidate_message="Please keep the interview tab open and visible.",
        ),
        _rule(
            T.FOCUS_LOST,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=focus,
            disabled_reason=why("monitor_focus"),
            min_duration_seconds=3,
            escalate_after=4,
            candidate_message="Please keep the interview window in focus.",
        ),
        _rule(
            T.WINDOW_MINIMIZED,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=focus,
            disabled_reason=why("monitor_focus"),
            min_duration_seconds=2,
            escalate_after=3,
            candidate_message="Please keep the interview window open and visible.",
        ),
        # Derived server-side from focus_lost/tab_hidden/window_minimized bursts:
        # min_occurrences switches inside window_seconds.
        _rule(
            T.RAPID_FOCUS_SWITCHING,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=focus,
            disabled_reason=why("monitor_focus"),
            min_occurrences=5,
            window_seconds=60,
        ),
        _rule(
            T.MULTIPLE_DISPLAYS_DETECTED,
            S.LOW,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=focus or (on and required_share),
            disabled_reason=why("monitor_focus"),
            candidate_message="We noticed more than one display. If you can, please use a single screen during the interview.",
        ),
        # --- browser: clipboard / shortcuts -----------------------------------------
        _rule(T.COPY_EVENT, S.INFO, A.LOG_ONLY, enabled=clipboard, disabled_reason=why("monitor_clipboard")),
        _rule(T.PASTE_EVENT, S.INFO, A.LOG_ONLY, enabled=clipboard, disabled_reason=why("monitor_clipboard")),
        _rule(
            T.LARGE_PASTE_IN_CODING,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=clipboard,
            disabled_reason=why("monitor_clipboard"),
        ),
        _rule(
            T.PROHIBITED_SHORTCUT,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=shortcuts,
            disabled_reason=why("monitor_shortcuts"),
            escalate_after=3,
            candidate_message="That keyboard shortcut isn't available during the interview.",
        ),
        _rule(
            T.CONTEXT_MENU, S.INFO, A.LOG_ONLY, enabled=shortcuts, disabled_reason=why("monitor_shortcuts")
        ),
        # --- devices ----------------------------------------------------------------
        _rule(
            T.CAMERA_DISABLED,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW if policy.camera_required else A.LOG_ONLY,
            enabled=camera_needed,
            disabled_reason=why("camera_required"),
            candidate_message="Your camera seems to be off. Please turn it back on to continue.",
        ),
        _rule(
            T.CAMERA_BLOCKED,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW if policy.camera_required else A.LOG_ONLY,
            enabled=camera_needed,
            disabled_reason=why("camera_required"),
            min_confidence=0.6,
            min_duration_seconds=20,
            candidate_message="Your camera view seems to be covered or too dark. Please check your camera.",
        ),
        # A muted microphone is a practical problem, not an integrity signal: the candidate is told, never
        # flagged (S22; repeated mutes used to escalate to a human-review flag).
        _rule(
            T.MICROPHONE_DISABLED,
            S.MEDIUM,
            A.WARN_CANDIDATE,
            candidate_message="Your microphone seems to be off. Please turn it back on so we can hear your answers.",
        ),
        # --- screen share (browser-observed, objective) ------------------------------------
        _rule(
            T.SCREEN_SHARE_STARTED,
            S.INFO,
            A.LOG_ONLY,
            enabled=screen_share,
            disabled_reason=why("screen_share_required"),
        ),
        _rule(
            T.SCREEN_SHARE_STOPPED,
            S.HIGH if required_share else S.INFO,
            stop_action if required_share else A.LOG_ONLY,
            enabled=screen_share,
            disabled_reason=why("screen_share_required"),
            candidate_message=stop_message if required_share else None,
        ),
        _rule(
            T.SCREEN_SHARE_NOT_FULL_SCREEN,
            S.MEDIUM if required_share else S.INFO,
            A.WARN_CANDIDATE if required_share else A.LOG_ONLY,
            enabled=screen_share,
            disabled_reason=why("screen_share_required"),
            escalate_after=2 if required_share else None,
            candidate_message="Please share your entire screen, not just a window or tab."
            if required_share
            else None,
        ),
        # --- server-observed -----------------------------------------------------------
        # Allowed reloads (or any reload while monitoring is off) are only logged and never escalate (S22).
        _rule(
            T.PAGE_RELOAD,
            S.LOW if reload_quiet else S.MEDIUM,
            A.LOG_ONLY if reload_quiet else A.FLAG_FOR_HUMAN_REVIEW,
        ),
        _rule(T.DISCONNECT, S.INFO, A.LOG_ONLY),
        _rule(T.RECONNECT, S.INFO, A.LOG_ONLY),
        # Derived: min_occurrences disconnects inside window_seconds, or reconnects > max_reconnects.
        _rule(
            T.UNUSUAL_DISCONNECT_PATTERN,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW if on else A.LOG_ONLY,
            min_occurrences=3,
            window_seconds=600,
        ),
        # --- vision (camera frames; consent + policy required) ----------------------------
        _rule(
            T.NO_PERSON_VISIBLE,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.6,
            min_duration_seconds=20,
            candidate_message="We can't see you on camera. Please make sure you're in view.",
        ),
        _rule(
            T.PERSON_LEFT_FRAME,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.6,
            min_duration_seconds=30,
            candidate_message="We can't see you on camera. Please make sure you're in view.",
        ),
        # min_occurrences = consecutive frames for the two "other person" rules.
        _rule(
            T.MULTIPLE_PERSONS_VISIBLE,
            S.HIGH,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.75,
            min_occurrences=2,
            window_seconds=120,
            candidate_message="Please make sure only you are visible on camera during the interview.",
        ),
        _rule(
            T.ANOTHER_PERSON_ENTERED,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.75,
            min_occurrences=2,
            window_seconds=120,
            candidate_message="Please make sure only you are visible on camera during the interview.",
        ),
        _rule(
            T.POSSIBLE_PHONE_VISIBLE,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.6,
            min_occurrences=2,
            window_seconds=60,
        ),
        _rule(
            T.POSSIBLE_PHOTO_TAKING,
            S.HIGH,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.75,
        ),
        _rule(
            T.LOOKING_AWAY_WITH_OTHER_SIGNALS,
            S.LOW,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=vision,
            disabled_reason=why("camera_frame_analysis"),
            min_confidence=0.6,
            window_seconds=60,
        ),
        # --- face presence (on-device face check; consent + policy required) ------------
        # Intervals the browser reports once they persisted (hysteresis on the device);
        # min_duration_seconds is enforced again here. Never pause or end an interview.
        _rule(
            T.FACE_NOT_VISIBLE,
            S.LOW,
            A.WARN_CANDIDATE,
            enabled=face_check,
            disabled_reason=why("face_presence_check")
            if camera_needed
            else (off or "the camera is not used"),
            min_duration_seconds=5,
            window_seconds=600,
            escalate_after_seconds=30,
            candidate_message="We can't see your face on camera. Please make sure you're in view and your face is lit.",
        ),
        _rule(
            T.MULTIPLE_FACES_VISIBLE,
            S.MEDIUM,
            A.WARN_CANDIDATE,
            enabled=face_check,
            disabled_reason=why("face_presence_check")
            if camera_needed
            else (off or "the camera is not used"),
            min_duration_seconds=3,
            window_seconds=600,
            confirmed_action=A.FLAG_FOR_HUMAN_REVIEW,
            escalate_after_seconds=15,
            escalate_severity=S.HIGH,
            candidate_message="Please make sure only you are visible on camera during the interview.",
        ),
        _rule(
            T.STATIC_FACE_IGNORED,
            S.INFO,
            A.LOG_ONLY,
            enabled=face_check,
            disabled_reason=why("face_presence_check")
            if camera_needed
            else (off or "the camera is not used"),
        ),
        _rule(
            T.FACE_CHECK_SUMMARY,
            S.INFO,
            A.LOG_ONLY,
            enabled=face_check,
            disabled_reason=why("face_presence_check")
            if camera_needed
            else (off or "the camera is not used"),
        ),
        # --- screen frames -------------------------------------------------------------
        _rule(
            T.SCREEN_CONTEXT_CHANGE,
            S.MEDIUM,
            A.FLAG_FOR_HUMAN_REVIEW,
            enabled=screen_analysis,
            disabled_reason=why("screen_share_analysis"),
            min_confidence=0.7,
        ),
    ]
    resolved = {rule.event_type: rule for rule in rules}
    missing = set(IntegrityEventType) - set(resolved)
    assert not missing, f"no default rule for {sorted(missing)}"  # every enum member must be handled
    return resolved


_OVERRIDABLE = (
    "enabled",
    "min_confidence",
    "min_duration_seconds",
    "min_occurrences",
    "window_seconds",
    "action",
    "candidate_message",
)


def _apply_override(rule: EffectiveRule, override: IntegrityRule) -> EffectiveRule:
    fields_set = override.model_fields_set or set(_OVERRIDABLE)
    changes: dict[str, Any] = {name: getattr(override, name) for name in _OVERRIDABLE if name in fields_set}
    if not rule.enabled:
        changes.pop("enabled", None)  # toggles win: never re-enable what the policy switched off
    updated = replace(rule, overridden=True, **changes)
    if "action" in changes:
        action = IntegrityAction(changes["action"])
        # A recruiter-chosen action replaces escalation; policy actions are always high severity.
        updated = replace(updated, escalate_after=None, confirmed_action=None, escalate_after_seconds=None)
        if action in POLICY_ACTIONS and SEVERITY_RANK[updated.severity] < SEVERITY_RANK[S.HIGH]:
            updated = replace(updated, severity=S.HIGH)
        elif action is A.FLAG_FOR_HUMAN_REVIEW and updated.severity is S.INFO:
            updated = replace(updated, severity=S.LOW)
    if updated.candidate_message is not None and not updated.candidate_message.strip():
        updated = replace(updated, candidate_message=None)
    return updated


def enforce_rule(rule: EffectiveRule) -> EffectiveRule:
    """Model-derived and face-check types can never pause or terminate an interview."""
    source = default_source(rule.event_type)
    non_terminating = source in NON_TERMINATING_SOURCES or rule.event_type in NON_TERMINATING_TYPES
    if non_terminating and rule.action in POLICY_ACTIONS:
        return replace(rule, action=A.FLAG_FOR_HUMAN_REVIEW, downgraded_from=rule.action)
    return rule


def resolve_rules(policy: IntegrityPolicy) -> dict[IntegrityEventType, EffectiveRule]:
    """Defaults + recruiter overrides + enforcement, for every event type."""
    rules = default_rules(policy)
    for override in policy.rules:
        current = rules.get(override.event_type)
        if current is not None:
            rules[override.event_type] = _apply_override(current, override)
    return {event_type: enforce_rule(rule) for event_type, rule in rules.items()}


# ---------------------------------------------------------------------------- signals


@dataclass(slots=True)
class Signal:
    """One observation before rules are applied."""

    event_type: IntegrityEventType
    source: IntegritySource
    occurred_at: datetime
    confidence: float | None = None
    duration_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence_media_id: uuid.UUID | None = None
    # Vision streaks aggregate into one event per streak (duration = streak length).
    streak_id: str | None = None
    # "add": durations of aggregated occurrences add up; "max": one streak (longest
    # duration wins); "episode": face-check intervals - each interval (``episode``)
    # keeps its longest reported duration and the event total is their sum;
    # "summary": per page-run face-check counters (``episode`` = run id).
    duration_mode: Literal["add", "max", "episode", "summary"] = "add"
    episode: str | None = None
    # The occurrence threshold was already applied by the producer (consecutive
    # frames for vision streaks, burst detection for derived signals).
    occurrences_satisfied: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    persist: bool
    qualifies: bool
    action: IntegrityAction
    severity: IntegritySeverity
    candidate_message: str | None
    reason: str  # disabled | gaze_alone | below_confidence | below_duration | duration_unknown | below_occurrences | triggered | escalated | confirmed
    downgraded_from: IntegrityAction | None = None


def _not_actioned(reason: str, *, persist: bool) -> Decision:
    """Dropped (``persist=False``) or merely logged signal."""
    return Decision(
        persist=persist,
        qualifies=False,
        action=A.LOG_ONLY,
        severity=S.INFO,
        candidate_message=None,
        reason=reason,
    )


def qualification(rule: EffectiveRule, signal: Signal) -> str | None:
    """``None`` when the signal passes the confidence and duration thresholds,
    otherwise the reason it does not."""
    if signal.confidence is not None and signal.confidence < rule.min_confidence:
        return "below_confidence"
    if rule.min_duration_seconds > 0:
        if signal.duration_ms is None:
            return "duration_unknown"
        if signal.duration_ms < rule.min_duration_seconds * 1000:
            return "below_duration"
    return None


def enforce_action(
    source: IntegritySource, action: IntegrityAction, event_type: IntegrityEventType | None = None
) -> tuple[IntegrityAction, IntegrityAction | None]:
    """Return ``(action, downgraded_from)``: VISION/SCREEN sources and the
    face-check types never pause or terminate."""
    non_terminating = source in NON_TERMINATING_SOURCES or event_type in NON_TERMINATING_TYPES
    if non_terminating and action in POLICY_ACTIONS:
        return A.FLAG_FOR_HUMAN_REVIEW, action
    return action, None


def server_check_status(signal: Signal) -> str | None:
    """``confirmed`` / ``not_confirmed`` / ``unavailable`` from the server's face
    check of the snapshot linked to a face-check interval (never from the browser)."""
    check = signal.details.get("server_check") if isinstance(signal.details, dict) else None
    if isinstance(check, Mapping):
        status = check.get("status")
        return status if isinstance(status, str) else None
    return None


def decide(
    rule: EffectiveRule | None,
    signal: Signal,
    *,
    occurrences: int = 1,
    strong_signal_recent: bool = False,
    warn_candidate: bool = True,
) -> Decision:
    """Apply one effective rule to one signal.

    ``occurrences`` counts qualifying occurrences of this type inside
    ``rule.window_seconds`` *including this one*; ``strong_signal_recent`` says
    whether a strong objective signal was observed in the gaze rule's window.
    """
    if rule is None or not rule.enabled:
        return _not_actioned("disabled", persist=False)
    if signal.event_type is T.LOOKING_AWAY_WITH_OTHER_SIGNALS and not strong_signal_recent:
        return _not_actioned("gaze_alone", persist=False)
    model_source = signal.source in NON_TERMINATING_SOURCES
    reason = qualification(rule, signal)
    if reason is not None:
        # Model guesses below threshold are not recorded; browser/server facts are logged.
        return _not_actioned(reason, persist=not model_source)
    if not signal.occurrences_satisfied and occurrences < rule.min_occurrences:
        return Decision(
            persist=True,
            qualifies=True,
            action=A.LOG_ONLY,
            severity=S.INFO,
            candidate_message=None,
            reason="below_occurrences",
        )
    action, severity, why = rule.action, rule.severity, "triggered"
    if (
        rule.escalate_after is not None
        and occurrences >= rule.escalate_after
        and ACTION_RANK[action] < ACTION_RANK[A.FLAG_FOR_HUMAN_REVIEW]
    ):
        action, why = A.FLAG_FOR_HUMAN_REVIEW, "escalated"
        if SEVERITY_RANK[rule.escalate_severity] > SEVERITY_RANK[severity]:
            severity = rule.escalate_severity
    status = server_check_status(signal) if signal.event_type in FACE_INTERVAL_TYPES else None
    if (
        rule.confirmed_action is not None
        and status == "confirmed"
        and ACTION_RANK[rule.confirmed_action] > ACTION_RANK[action]
    ):
        action, why = rule.confirmed_action, "confirmed"
        if SEVERITY_RANK[rule.escalate_severity] > SEVERITY_RANK[severity]:
            severity = rule.escalate_severity
    elif (
        rule.escalate_after_seconds is not None
        and status != "not_confirmed"
        and signal.duration_ms is not None
        and signal.duration_ms >= rule.escalate_after_seconds * 1000
        and ACTION_RANK[action] < ACTION_RANK[A.FLAG_FOR_HUMAN_REVIEW]
    ):
        action, why = A.FLAG_FOR_HUMAN_REVIEW, "escalated"
        if SEVERITY_RANK[rule.escalate_severity] > SEVERITY_RANK[severity]:
            severity = rule.escalate_severity
    if action is A.WARN_CANDIDATE and not warn_candidate:
        action = A.LOG_ONLY
    action, downgraded = enforce_action(signal.source, action, signal.event_type)
    message: str | None = None
    if warn_candidate and action is not A.LOG_ONLY:
        message = rule.candidate_message or (GENERIC_WARNING if action is A.WARN_CANDIDATE else None)
    return Decision(
        persist=True,
        qualifies=True,
        action=action,
        severity=severity,
        candidate_message=message,
        reason=why,
        downgraded_from=downgraded or rule.downgraded_from,
    )


def max_severity(a: IntegritySeverity | str, b: IntegritySeverity | str) -> IntegritySeverity:
    sa, sb = IntegritySeverity(a), IntegritySeverity(b)
    return sa if SEVERITY_RANK[sa] >= SEVERITY_RANK[sb] else sb


def rules_summary(rules: Mapping[IntegrityEventType, EffectiveRule]) -> dict[str, dict[str, Any]]:
    """Compact JSON view of the effective rules (API / debugging)."""
    return {
        event_type.value: {
            "enabled": rule.enabled,
            "severity": rule.severity.value,
            "action": rule.action.value,
            **rule.thresholds(),
            "overridden": rule.overridden,
            **({"disabled_reason": rule.disabled_reason} if rule.disabled_reason else {}),
            **({"downgraded_from": rule.downgraded_from.value} if rule.downgraded_from else {}),
            **({"confirmed_action": rule.confirmed_action.value} if rule.confirmed_action else {}),
            **(
                {"escalate_after_seconds": rule.escalate_after_seconds}
                if rule.escalate_after_seconds is not None
                else {}
            ),
        }
        for event_type, rule in sorted(rules.items(), key=lambda item: item[0].value)
    }
