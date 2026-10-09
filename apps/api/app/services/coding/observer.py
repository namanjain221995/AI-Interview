"""Code observer: decides *when* the interviewer should say something while the
candidate codes, and (only then) asks the main model *what* to say.

``await decide(context, trigger, state)`` returns ``None`` when nothing should
be said (the default), or an :class:`ObservationDecision`.

Deterministic triggers (evaluated on every call, newest information first):

==========================  =====================================================
``candidate_speech``        the candidate said something while coding - always answered
``time_warning``            50 % of the time used, 5 minutes left, 1 minute left (once each)
``repeated_compile_errors`` two or more compile errors in a row
``run_timeout``             the latest run timed out (complexity)
``regression``              tests passed before and fail now
``tests_passed``            the visible/all tests pass for the first time (edge cases)
``structural_change``       an algorithmic pattern changed after a quiet period
                            (nested loops introduced/removed, hash map/set, recursion,
                            sorting, heap, binary search; SQL joins/subqueries/...)
``run_error``               the latest run raised a runtime error
``persistent_failures``     tests have been failing for > 3 minutes while the
                            interviewer stayed silent
``long_idle``               no edits for 2 minutes
==========================  =====================================================

Rate limits: ``CodingSection.min_seconds_between_comments`` between proactive
comments, never while the candidate typed in the last ~6 s (unless a run just
finished), at most ~6 proactive comments per problem, ``proactive_comments=False``
silences everything except answers to the candidate and time warnings. Time
warnings use fixed phrasing (no model call); every other trigger asks the main
model for a :class:`~app.schemas.ai.CodeObservation` (one attempt, 7 s). When
the model is unavailable a proactive trigger stays silent; an answer owed to
the candidate falls back to a deterministic phrasing.

``state`` is a JSON-serialisable dict owned by the caller (persist it between
calls, e.g. in ``coding_sessions.observer_state["observer"]``); ``decide``
reads and updates it in place. Time is ``context.elapsed_s`` (seconds since the
coding exercise started), so the observer is deterministic and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.enums import (
    AvatarExpression,
    AvatarGesture,
    AvatarState,
    GestureIntensity,
    ModelTask,
    SpeechTone,
)
from app.schemas.ai import CodeObservation
from app.schemas.rounds import CodingSection
from app.services.coding.analysis import CodeFeatures, analyze, describe_changes, features_from_dict
from app.services.coding.contracts import CodingContext
from app.services.coding.prompts import OBSERVATION_SYSTEM, OBSERVATION_USER
from app.services.interview.contracts import BehaviorSpec

log = get_logger(__name__)

QUIET_TYPING_S = 6.0
LONG_IDLE_S = 120.0
PERSISTENT_FAILURE_S = 180.0
MAX_COMMENTS_PER_PROBLEM = 6
DEFAULT_MIN_GAP_S = 75.0
# One attempt, capped below the engine's wait for the whole decision
# (``coding_bridge.OBSERVER_TIMEOUT_S`` = 8 s, which also covers the DB round
# trips): a later answer would be cancelled and lost anyway. Measured
# CodeObservation latency on the shared main model: 2.2-7 s, median ~4 s.
OBSERVATION_TIMEOUT_S = 7.0
OBSERVATION_MAX_ATTEMPTS = 1
MAX_SPEECH_WORDS = 45
# A normal CodeObservation is 110-160 tokens; a low cap makes a runaway generation fail fast.
OBSERVATION_MAX_TOKENS = 260
_FAILING = frozenset({"failed", "error", "compile_error", "timeout", "memory_exceeded"})
_RUN_TRIGGERS = frozenset(
    {"repeated_compile_errors", "run_timeout", "regression", "tests_passed", "run_error"}
)
_SILENT_TRIGGERS = frozenset({"submitted", "time_up", "hint_requested"})
_SPEECH_TRIGGERS = frozenset({"candidate_speech", "speech", "candidate_turn"})
_CHANGE_PRIORITY = (
    "nested_loops",
    "hash_map",
    "hash_set",
    "recursion",
    "sorting",
    "heap",
    "binary_search",
    "stack_or_queue",
    "sql_join",
    "sql_subquery",
    "sql_group_by",
    "sql_window",
    "sql_cte",
)
_CODE_LIKE = re.compile(
    r"```|\bdef\s+\w+\s*\(|\bfunction\s+\w+\s*\(|=>\s*\{|[{};]\s*$|\bfor\s*\([^)]*;[^)]*;", re.MULTILINE
)


@dataclass(slots=True)
class ObservationDecision:
    should_speak: bool
    speech: str
    kind: str
    focus: str
    behavior: BehaviorSpec
    # Additive diagnostics (not required by consumers):
    trigger: str = ""
    reason: str = ""
    used_model: bool = False
    model_call_id: str | None = None


@dataclass(slots=True)
class TriggerResult:
    name: str
    detail: str
    focus: str
    kind: str
    priority: int
    bypass_gap: bool = False
    bypass_typing: bool = False
    bypass_cap: bool = False
    must_answer: bool = False
    use_model: bool = True
    template: str = ""
    counts_as_comment: bool = True
    marks: dict[str, Any] = field(default_factory=dict)  # state updates applied when it fires


# =============================================================================
# Deterministic triggers
# =============================================================================


def _last_edit_marker(context: CodingContext) -> float:
    return round(context.elapsed_s - context.seconds_since_last_edit, 1)


def _time_warning(context: CodingContext, state: dict[str, Any]) -> TriggerResult | None:
    limit = float(context.time_limit_s or 0)
    if limit <= 0:
        return None
    elapsed = context.elapsed_s
    remaining = limit - elapsed
    issued: list[str] = list(state.get("time_warnings", []))
    long_exercise = limit > 7 * 60
    due: list[str] = []
    # The half-way warning is skipped when it would land within a minute of the 5-minute warning.
    if elapsed >= 0.5 * limit and remaining > 60 and not (long_exercise and remaining - 300 < 60):
        due.append("half")
    if long_exercise and 60 < remaining <= 300:
        due.append("five_min")
    if 0 < remaining <= 60:
        due.append("one_min")
    pending = [w for w in due if w not in issued]
    if not pending:
        return None
    level = pending[-1]  # most urgent
    minutes = max(1, round(remaining / 60))
    templates = {
        "half": f"Quick time check: you're about halfway through, with roughly {minutes} minutes left for this exercise.",
        "five_min": "About five minutes left. Make sure your solution runs, and tell me about any edge cases you still want to handle.",
        "one_min": "We have about one minute left, so let's make sure what you have runs.",
    }
    order = ["half", "five_min", "one_min"]
    marks = {"time_warnings": sorted(set(issued) | set(order[: order.index(level) + 1]), key=order.index)}
    return TriggerResult(
        name="time_warning",
        detail=f"{level.replace('_', ' ')} warning, {int(remaining)} s remaining",
        focus="other",
        kind="time_warning",
        priority=90 if level == "one_min" else 80,
        bypass_gap=True,
        bypass_cap=True,
        bypass_typing=level == "one_min",
        use_model=False,
        template=templates[level],
        counts_as_comment=False,
        marks=marks,
    )


def _trailing(runs: list[dict[str, Any]], predicate: Any) -> list[dict[str, Any]]:
    streak: list[dict[str, Any]] = []
    for run in reversed(runs):
        if not predicate(run):
            break
        streak.append(run)
    return list(reversed(streak))


def _run_triggers(context: CodingContext, state: dict[str, Any], *, just_ran: bool) -> list[TriggerResult]:
    runs = [r for r in context.runs if not r.get("platform_fault")]
    if not runs:
        return []
    out: list[TriggerResult] = []
    latest = runs[-1]
    status = str(latest.get("status"))
    runs_seen = int(state.get("runs_seen", 0))
    new_run = len(runs) > runs_seen
    marks_seen = {"runs_seen": len(runs)}

    compile_streak = _trailing(runs, lambda r: r.get("status") == "compile_error")
    if len(compile_streak) >= 2 and len(compile_streak) > int(state.get("compile_streak_commented", 0)):
        out.append(
            TriggerResult(
                "repeated_compile_errors",
                f"{len(compile_streak)} compile errors in a row",
                "bug",
                "nudge",
                60,
                bypass_typing=just_ran,
                template="The compiler is still unhappy. Want to walk me through what the error message is telling you?",
                marks={**marks_seen, "compile_streak_commented": len(compile_streak)},
            )
        )
    if new_run and status == "timeout":
        out.append(
            TriggerResult(
                "run_timeout",
                "the latest run exceeded the time limit",
                "complexity",
                "question",
                55,
                bypass_typing=just_ran,
                template="That run hit the time limit. What's the time complexity of your current approach?",
                marks=marks_seen,
            )
        )
    passed_before = any(
        r.get("status") == "passed" and r.get("kind") in ("test", "submit") for r in runs[:-1]
    )
    if (
        new_run
        and status in _FAILING
        and status != "compile_error"
        and passed_before
        and not state.get("regression_commented_at")
    ):
        out.append(
            TriggerResult(
                "regression",
                "tests passed earlier but fail after the latest change",
                "bug",
                "question",
                50,
                bypass_typing=just_ran,
                template="Your tests were passing a moment ago. What changed in the last edit?",
                marks={**marks_seen, "regression_commented_at": context.elapsed_s},
            )
        )
    if (
        new_run
        and status == "passed"
        and latest.get("kind") in ("test", "submit")
        and not state.get("passed_commented")
    ):
        out.append(
            TriggerResult(
                "tests_passed",
                f"{latest.get('passed')}/{latest.get('total')} tests pass",
                "edge_cases",
                "question",
                45,
                bypass_typing=just_ran,
                template="Your tests pass for the standard cases. What happens with an empty input?",
                marks={**marks_seen, "passed_commented": True},
            )
        )
    if (
        new_run
        and status in ("error", "memory_exceeded")
        and not state.get("error_commented_run") == len(runs)
    ):
        out.append(
            TriggerResult(
                "run_error",
                "the latest run raised a runtime error"
                if status == "error"
                else "the latest run ran out of memory",
                "bug",
                "question",
                35,
                bypass_typing=just_ran,
                template="That run crashed with an error. Which input do you think triggers it?",
                marks={**marks_seen, "error_commented_run": len(runs)},
            )
        )
    failing = _trailing(runs, lambda r: r.get("status") in _FAILING)
    if failing:
        started = float(failing[0].get("at_s", context.elapsed_s))
        last_comment = state.get("last_comment_at_s")
        silent_since = last_comment is None or float(last_comment) < started
        if (
            context.elapsed_s - started >= PERSISTENT_FAILURE_S
            and silent_since
            and state.get("persistent_commented_start") != started
        ):
            out.append(
                TriggerResult(
                    "persistent_failures",
                    f"tests failing for {int(context.elapsed_s - started)} s",
                    "bug",
                    "nudge",
                    30,
                    template="Some tests are still failing. Which case do you think is breaking, and why?",
                    marks={"persistent_commented_start": started},
                )
            )
    if not out and new_run:
        state["runs_seen"] = len(runs)  # ordinary run: remember it without speaking
    return out


def _structural_trigger(
    context: CodingContext, state: dict[str, Any], features: CodeFeatures
) -> TriggerResult | None:
    baseline = features_from_dict(state.get("baseline_features")) or analyze("", context.language)
    changes = describe_changes(baseline, features)
    if not changes:
        return None
    if context.seconds_since_last_edit < QUIET_TYPING_S:
        return None  # wait for the quiet period after typing
    # Something newly introduced is more interesting than something removed.
    changes.sort(
        key=lambda c: (not c.introduced, _CHANGE_PRIORITY.index(c.key) if c.key in _CHANGE_PRIORITY else 99)
    )
    top = changes[0]
    templates = {
        ("nested_loops", True): "You've chosen a nested loop here. What is the current time complexity?",
        ("nested_loops", False): "You removed the nested loop. How does that change the complexity?",
        ("hash_map", True): "You changed the data structure to a hash map. Explain why.",
        ("hash_set", True): "You've added a set here. What does it buy you?",
        (
            "recursion",
            True,
        ): "You've gone with recursion. How deep can the recursion get for the largest input?",
        ("sorting", True): "You're sorting the input now. How does that affect the overall complexity?",
        ("heap", True): "You introduced a heap. What does each operation cost here?",
        (
            "binary_search",
            True,
        ): "You're using a binary search now. What has to be true about the data for that to work?",
    }
    template = templates.get(
        (top.key, top.introduced), f"I see you {top.description}. What made you change the approach?"
    )
    return TriggerResult(
        "structural_change",
        "; ".join(c.description for c in changes[:3]),
        top.focus,
        "question",
        40,
        template=template,
        marks={"baseline_features": features.as_dict()},
    )


def _idle_trigger(context: CodingContext, state: dict[str, Any]) -> TriggerResult | None:
    if context.seconds_since_last_edit < LONG_IDLE_S:
        return None
    if context.time_limit_s and context.time_limit_s - context.elapsed_s <= 60:
        return None
    marker = _last_edit_marker(context)
    if state.get("idle_commented_marker") == marker:
        return None
    return TriggerResult(
        "long_idle",
        f"no edits for {int(context.seconds_since_last_edit)} s",
        "approach",
        "encouragement",
        20,
        template="Take your time. How are you thinking about approaching it? Feel free to think out loud.",
        marks={"idle_commented_marker": marker},
    )


def evaluate_triggers(
    context: CodingContext,
    trigger: str,
    state: dict[str, Any],
    *,
    features: CodeFeatures | None = None,
    candidate_utterance: str | None = None,
) -> list[TriggerResult]:
    """Every trigger that currently fires, highest priority first (no rate limits)."""
    if trigger in _SILENT_TRIGGERS:
        return []
    features = features or analyze(context.current_code, context.language)
    results: list[TriggerResult] = []
    if trigger in _SPEECH_TRIGGERS:
        said = (
            candidate_utterance
            or (context.candidate_explanations[-1] if context.candidate_explanations else "")
        ).strip()
        results.append(
            TriggerResult(
                "candidate_speech",
                f"the candidate said: {said[:300]}" if said else "the candidate addressed the interviewer",
                "other",
                "clarification",
                100,
                bypass_gap=True,
                bypass_typing=True,
                bypass_cap=True,
                must_answer=True,
                template="Got it. Take your time, and feel free to talk me through your approach as you go.",
                counts_as_comment=False,
            )
        )
    warning = _time_warning(context, state)
    if warning:
        results.append(warning)
    results.extend(_run_triggers(context, state, just_ran=trigger == "run_completed"))
    structural = _structural_trigger(context, state, features)
    if structural:
        results.append(structural)
    idle = _idle_trigger(context, state)
    if idle:
        results.append(idle)
    results.sort(key=lambda r: -r.priority)
    return results


def _blocked(
    result: TriggerResult,
    context: CodingContext,
    state: dict[str, Any],
    *,
    min_gap: float,
    max_comments: int,
    proactive: bool,
) -> str | None:
    """Reason the trigger is rate limited, or ``None`` when it may speak."""
    if not proactive and result.name not in ("candidate_speech", "time_warning"):
        return "proactive_comments_disabled"
    if not result.bypass_cap and int(state.get("comments_made", 0)) >= max_comments:
        return "max_comments"
    last = state.get("last_comment_at_s")
    if not result.bypass_gap and last is not None and context.elapsed_s - float(last) < min_gap:
        return "min_gap"
    if not result.bypass_typing and context.seconds_since_last_edit < QUIET_TYPING_S:
        return "typing"
    return None


def select_trigger(
    context: CodingContext,
    trigger: str,
    state: dict[str, Any],
    *,
    section: CodingSection | None = None,
    features: CodeFeatures | None = None,
    candidate_utterance: str | None = None,
) -> tuple[TriggerResult | None, list[tuple[str, str]]]:
    """The highest-priority trigger allowed by the rate limits, plus the
    ``(trigger, reason)`` pairs that were suppressed."""
    section = section or CodingSection()
    min_gap = float(
        state.get("min_seconds_between_comments") or section.min_seconds_between_comments or DEFAULT_MIN_GAP_S
    )
    max_comments = int(state.get("max_comments") or MAX_COMMENTS_PER_PROBLEM)
    proactive = bool(state.get("proactive_comments", section.proactive_comments))
    suppressed: list[tuple[str, str]] = []
    for result in evaluate_triggers(
        context, trigger, state, features=features, candidate_utterance=candidate_utterance
    ):
        reason = _blocked(
            result, context, state, min_gap=min_gap, max_comments=max_comments, proactive=proactive
        )
        if reason is None:
            return result, suppressed
        suppressed.append((result.name, reason))
    return None, suppressed


# =============================================================================
# Model call + post-processing
# =============================================================================


def _numbered(code: str, limit: int = 12_000) -> str:
    lines = code.splitlines() or [""]
    text = "\n".join(f"{i + 1:>3} | {line}" for i, line in enumerate(lines))
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def _runs_text(context: CodingContext) -> str:
    runs = [r for r in context.runs if not r.get("platform_fault")][-6:]
    if not runs:
        return "none yet"
    return "; ".join(
        f"{r.get('kind')} at {int(float(r.get('at_s', 0)))}s: {r.get('status')} ({r.get('passed', 0)}/{r.get('total', 0)})"
        for r in runs
    )


def clean_speech(text: str) -> str:
    """Spoken text only: no markdown, no code fences, bounded length."""
    cleaned = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)
    cleaned = re.sub(r"[*_#>`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split(" ")
    if len(words) > MAX_SPEECH_WORDS:
        clipped = " ".join(words[:MAX_SPEECH_WORDS])
        end = max(clipped.rfind("?"), clipped.rfind("."), clipped.rfind("!"))
        cleaned = clipped[: end + 1] if end > 20 else clipped.rstrip(",;:") + "."
    return cleaned


def _looks_like_code(text: str) -> bool:
    return bool(_CODE_LIKE.search(text or ""))


_SPEAKING_STATES = frozenset(
    {AvatarState.SPEAKING, AvatarState.QUESTIONING, AvatarState.ENCOURAGING, AvatarState.ACKNOWLEDGING}
)


def behavior_for(kind: str, model_behavior: Any | None = None) -> BehaviorSpec:
    if model_behavior is not None:
        if model_behavior.state not in _SPEAKING_STATES:
            # e.g. INTERRUPTED / LISTENING / CLOSING make no sense for a spoken comment.
            fallback = behavior_for(kind)
            model_behavior = model_behavior.model_copy(update={"state": fallback.state})
        spec = BehaviorSpec(
            state=model_behavior.state,
            expression=model_behavior.expression,
            gesture=model_behavior.gesture,
            gesture_intensity=model_behavior.gesture_intensity,
            tone=model_behavior.tone,
        )
        if kind == "question" and spec.state == AvatarState.SPEAKING:
            spec.state = AvatarState.QUESTIONING
        return spec
    if kind == "question":
        return BehaviorSpec(
            AvatarState.QUESTIONING,
            AvatarExpression.CURIOUS,
            AvatarGesture.QUESTION_GESTURE,
            GestureIntensity.LOW,
            SpeechTone.PROFESSIONAL,
        )
    if kind == "encouragement":
        return BehaviorSpec(
            AvatarState.ENCOURAGING,
            AvatarExpression.ENCOURAGING,
            AvatarGesture.SMALL_OPEN_PALM,
            GestureIntensity.LOW,
            SpeechTone.SUPPORTIVE,
        )
    if kind == "time_warning":
        return BehaviorSpec(
            AvatarState.SPEAKING,
            AvatarExpression.NEUTRAL,
            AvatarGesture.NONE,
            GestureIntensity.LOW,
            SpeechTone.FRIENDLY,
        )
    return BehaviorSpec(
        AvatarState.SPEAKING,
        AvatarExpression.THOUGHTFUL,
        AvatarGesture.SMALL_OPEN_PALM,
        GestureIntensity.LOW,
        SpeechTone.PROFESSIONAL,
    )


def build_observation_messages(
    context: CodingContext,
    result: TriggerResult,
    state: dict[str, Any],
    features: CodeFeatures,
    *,
    interviewer_name: str,
) -> list[dict[str, str]]:
    limit_min = round(context.time_limit_s / 60, 1)
    remaining = max(0.0, context.time_limit_s - context.elapsed_s)
    baseline = features_from_dict(state.get("baseline_features")) or analyze("", context.language)
    changes = "; ".join(c.description for c in describe_changes(baseline, features))
    system = OBSERVATION_SYSTEM.render(interviewer_name=interviewer_name)
    user = OBSERVATION_USER.render(
        title=context.question_title,
        difficulty=context.difficulty.value
        if hasattr(context.difficulty, "value")
        else str(context.difficulty),
        language=context.language.value if hasattr(context.language, "value") else str(context.language),
        prompt_excerpt=(context.prompt_markdown or "")[:1800],
        expected_concepts=", ".join(context.expected_concepts) or "n/a",
        optimal_complexity=", ".join(f"{k} {v}" for k, v in context.optimal_complexity.items()) or "n/a",
        elapsed_min=round(context.elapsed_s / 60, 1),
        limit_min=limit_min,
        remaining_min=round(remaining / 60, 1),
        hints_used=context.hints_used,
        idle_s=int(context.seconds_since_last_edit),
        structure=features.summary(),
        changes=changes,
        runs=_runs_text(context),
        last_error=context.last_compile_error or context.last_runtime_error or "",
        code=_numbered(context.current_code or ""),
        diff=(context.recent_diff or "")[:3000],
        explanations="\n".join(context.candidate_explanations[-4:])[:2000],
        previous_comments=" | ".join(state.get("previous_comments", [])[-4:]),
        trigger=result.name,
        trigger_detail=result.detail,
        focus=result.focus,
        kind=result.kind,
        must_answer=result.must_answer,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _apply_marks(state: dict[str, Any], result: TriggerResult, context: CodingContext, speech: str) -> None:
    for key, value in result.marks.items():
        state[key] = value
    if result.counts_as_comment:
        state["comments_made"] = int(state.get("comments_made", 0)) + 1
    if result.name != "time_warning":
        state["last_comment_at_s"] = context.elapsed_s
    comments = list(state.get("previous_comments", []))
    comments.append(speech[:200])
    state["previous_comments"] = comments[-6:]
    state["last_trigger"] = result.name


async def decide(
    context: CodingContext,
    trigger: str,
    state: dict[str, Any],
    *,
    section: CodingSection | None = None,
    candidate_utterance: str | None = None,
    interviewer_name: str | None = None,
    ai: AIServices | None = None,
    session_id: str | None = None,
    organization_id: str | None = None,
    timeout: float = OBSERVATION_TIMEOUT_S,  # noqa: ASYNC109 - model call budget
) -> ObservationDecision | None:
    """Decide whether to speak now. ``None`` = stay silent (the default).

    ``trigger`` is the activity kind (``edit``, ``run_completed``, ``idle``,
    ``time_warning``, ``language_changed``, ``candidate_speech``, ``tick``;
    ``submitted``/``time_up``/``hint_requested`` never produce a comment).
    Returns ``ObservationDecision(should_speak=False)`` when a trigger fired
    but the model chose silence.
    """
    features = analyze(context.current_code, context.language)
    if "baseline_features" not in state:
        state["baseline_features"] = analyze("", context.language).as_dict()
    result, suppressed = select_trigger(
        context, trigger, state, section=section, features=features, candidate_utterance=candidate_utterance
    )
    if suppressed:
        state["suppressed"] = int(state.get("suppressed", 0)) + len(suppressed)
    if result is None:
        if suppressed:
            log.debug(
                "coding.observer.suppressed",
                coding_session_id=context.coding_session_id,
                suppressed=suppressed,
            )
        return None

    if not result.use_model:
        speech = result.template
        _apply_marks(state, result, context, speech)
        log.info(
            "coding.observer.speak",
            coding_session_id=context.coding_session_id,
            trigger=result.name,
            used_model=False,
        )
        return ObservationDecision(
            True,
            speech,
            result.kind,
            result.focus,
            behavior_for(result.kind),
            trigger=result.name,
            reason=result.detail,
        )

    services = ai or get_ai()
    name = interviewer_name or get_settings().interviewer_name
    messages = build_observation_messages(context, result, state, features, interviewer_name=name)
    observation: CodeObservation | None = None
    model_call_id: str | None = None
    try:
        structured = await services.llm.structured(
            CodeObservation,
            messages,
            context=CallContext(
                task=ModelTask.CODE_OBSERVATION,
                session_id=session_id,
                organization_id=organization_id,
                prompt_version=OBSERVATION_USER.version,
            ),
            temperature=0.4,
            max_tokens=OBSERVATION_MAX_TOKENS,
            timeout=timeout,
            max_attempts=OBSERVATION_MAX_ATTEMPTS,
        )
        observation = structured.value
        model_call_id = structured.model_call_id
    except AIError as exc:
        log.warning(
            "coding.observer.model_failed",
            coding_session_id=context.coding_session_id,
            trigger=result.name,
            error=type(exc).__name__,
        )
        if not result.must_answer:
            # Proactive comments are optional: stay silent rather than speak a
            # canned line late. Mark the trigger so it does not fire again on
            # the next tick and hit the (busy) model once more.
            for key, value in result.marks.items():
                state[key] = value
            return ObservationDecision(
                False,
                "",
                "none",
                result.focus,
                behavior_for("none"),
                trigger=result.name,
                reason="model_unavailable",
            )
        # The candidate asked something: answer with the deterministic phrasing.

    if observation is not None and not observation.should_speak and not result.must_answer:
        # The model judged that speaking now would not help: stay silent but
        # remember the trigger so it does not fire again immediately.
        for key, value in result.marks.items():
            state[key] = value
        log.info(
            "coding.observer.model_silent", coding_session_id=context.coding_session_id, trigger=result.name
        )
        return ObservationDecision(
            False,
            "",
            observation.kind or "none",
            observation.focus or result.focus,
            behavior_for("none"),
            trigger=result.name,
            reason=observation.reason or result.detail,
            used_model=True,
            model_call_id=model_call_id,
        )

    raw = observation.speech if observation is not None else ""
    speech = "" if _looks_like_code(raw) else clean_speech(raw)
    used_model = bool(speech)
    if not used_model:
        speech = result.template
    kind = (
        observation.kind
        if observation is not None and used_model and observation.kind != "none"
        else result.kind
    )
    focus = observation.focus if observation is not None and used_model else result.focus
    behavior = behavior_for(kind, observation.behavior if observation is not None and used_model else None)
    _apply_marks(state, result, context, speech)
    log.info(
        "coding.observer.speak",
        coding_session_id=context.coding_session_id,
        trigger=result.name,
        used_model=used_model,
        comments_made=state.get("comments_made"),
    )
    return ObservationDecision(
        True,
        speech,
        kind,
        focus,
        behavior,
        trigger=result.name,
        reason=result.detail,
        used_model=used_model,
        model_call_id=model_call_id,
    )
