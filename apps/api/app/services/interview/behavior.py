"""Semantic avatar behaviour for interviewer utterances.

``provisional(action)`` is sent with ``UtteranceBegin`` before the decision
exists (from the policy's recommended action); ``from_decision`` converts the
validated ``TurnDecision.avatar_behavior()`` into the engine contract's
``BehaviorSpec`` for ``UtteranceMeta``.
"""

from __future__ import annotations

from app.domain.enums import (
    AvatarExpression,
    AvatarGesture,
    AvatarState,
    GestureIntensity,
    SpeechTone,
    TurnAction,
)
from app.schemas.ai import ACTION_AVATAR_STATE, AvatarBehavior, TurnDecision
from app.services.interview.contracts import BehaviorSpec

_E = AvatarExpression
_G = AvatarGesture
_I = GestureIntensity

# (expression, gesture, intensity) per action; state comes from ACTION_AVATAR_STATE.
_ACTION_STYLE: dict[TurnAction, tuple[AvatarExpression, AvatarGesture, GestureIntensity]] = {
    TurnAction.INTRODUCE: (_E.WARM, _G.SMALL_OPEN_PALM, _I.LOW),
    TurnAction.ASK_QUESTION: (_E.CURIOUS, _G.QUESTION_GESTURE, _I.LOW),
    TurnAction.ASK_FOLLOWUP: (_E.CURIOUS, _G.HEAD_TILT, _I.LOW),
    TurnAction.REQUEST_SPECIFICITY: (_E.CURIOUS, _G.ONE_HAND_EXPLAIN, _I.LOW),
    TurnAction.CLARIFY_QUESTION: (_E.WARM, _G.ONE_HAND_EXPLAIN, _I.LOW),
    TurnAction.SIMPLIFY: (_E.ENCOURAGING, _G.SMALL_OPEN_PALM, _I.LOW),
    TurnAction.INCREASE_DIFFICULTY: (_E.THOUGHTFUL, _G.ONE_HAND_EXPLAIN, _I.MEDIUM),
    TurnAction.VERIFY_CLAIM: (_E.CURIOUS, _G.QUESTION_GESTURE, _I.LOW),
    TurnAction.VERIFY_CONTRADICTION: (_E.THOUGHTFUL, _G.HEAD_TILT, _I.LOW),
    TurnAction.CHANGE_TOPIC: (_E.WARM, _G.ONE_HAND_EXPLAIN, _I.LOW),
    TurnAction.START_CODING: (_E.WARM, _G.TWO_HAND_EXPLAIN, _I.MEDIUM),
    TurnAction.CODING_COMMENT: (_E.CURIOUS, _G.NONE, _I.LOW),
    TurnAction.GIVE_HINT: (_E.ENCOURAGING, _G.ONE_HAND_EXPLAIN, _I.LOW),
    TurnAction.ACKNOWLEDGE_AND_WAIT: (_E.WARM, _G.NOD, _I.LOW),
    TurnAction.ANSWER_CANDIDATE_QUESTION: (_E.WARM, _G.SMALL_OPEN_PALM, _I.LOW),
    TurnAction.REDIRECT: (_E.NEUTRAL, _G.SMALL_OPEN_PALM, _I.LOW),
    TurnAction.INVITE_CANDIDATE_QUESTIONS: (_E.WARM, _G.SMALL_OPEN_PALM, _I.LOW),
    TurnAction.CLOSE_INTERVIEW: (_E.WARM, _G.GOODBYE, _I.MEDIUM),
}
_SUPPORTIVE = {TurnAction.SIMPLIFY, TurnAction.GIVE_HINT, TurnAction.ACKNOWLEDGE_AND_WAIT}
_FRIENDLY = {TurnAction.INTRODUCE, TurnAction.CLOSE_INTERVIEW, TurnAction.INVITE_CANDIDATE_QUESTIONS}


def provisional(action: TurnAction) -> BehaviorSpec:
    expression, gesture, intensity = _ACTION_STYLE.get(action, (_E.NEUTRAL, _G.NONE, _I.LOW))
    tone = (
        SpeechTone.SUPPORTIVE
        if action in _SUPPORTIVE
        else SpeechTone.FRIENDLY
        if action in _FRIENDLY
        else SpeechTone.PROFESSIONAL
    )
    return BehaviorSpec(
        state=ACTION_AVATAR_STATE.get(action, AvatarState.SPEAKING),
        expression=expression,
        gesture=gesture,
        gesture_intensity=intensity,
        tone=tone,
    )


def from_avatar_behavior(behavior: AvatarBehavior) -> BehaviorSpec:
    return BehaviorSpec(
        state=behavior.state,
        expression=behavior.expression,
        gesture=behavior.gesture,
        gesture_intensity=behavior.gesture_intensity,
        tone=behavior.tone,
    )


def from_decision(decision: TurnDecision) -> BehaviorSpec:
    return from_avatar_behavior(decision.avatar_behavior())


def for_kind(kind: str) -> BehaviorSpec:
    """Behaviour for non-decision utterances (resume, hints, comments...)."""
    mapping = {
        "intro": TurnAction.INTRODUCE,
        "question": TurnAction.ASK_QUESTION,
        "followup": TurnAction.ASK_FOLLOWUP,
        "acknowledgment": TurnAction.ACKNOWLEDGE_AND_WAIT,
        "comment": TurnAction.CODING_COMMENT,
        "hint": TurnAction.GIVE_HINT,
        "transition": TurnAction.START_CODING,
        "closing": TurnAction.CLOSE_INTERVIEW,
        "resume": TurnAction.CLARIFY_QUESTION,
    }
    return provisional(mapping.get(kind, TurnAction.ASK_QUESTION))


def behavior_dict(spec: BehaviorSpec) -> dict[str, str]:
    return {
        "state": spec.state.value,
        "expression": spec.expression.value,
        "gesture": spec.gesture.value,
        "gesture_intensity": spec.gesture_intensity.value,
        "tone": spec.tone.value,
    }


__all__ = ["behavior_dict", "for_kind", "from_avatar_behavior", "from_decision", "provisional"]
