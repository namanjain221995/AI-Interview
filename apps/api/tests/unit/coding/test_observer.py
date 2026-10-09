"""Code observer: deterministic triggers and the rate-limit matrix. The model
is called only when a trigger fires and every limit allows it."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.base import AITimeout
from app.domain.enums import AvatarState, ModelTask
from app.schemas.ai import AvatarBehavior, CodeObservation
from app.schemas.rounds import CodingSection
from app.services.coding.analysis import analyze
from app.services.coding.observer import (
    LONG_IDLE_S,
    MAX_COMMENTS_PER_PROBLEM,
    PERSISTENT_FAILURE_S,
    QUIET_TYPING_S,
    clean_speech,
    decide,
    evaluate_triggers,
    select_trigger,
)

from .conftest import make_context

STARTER = "def two_sum(nums, target):\n    return []\n"
NESTED = (
    "def two_sum(nums, target):\n"
    "    for i in range(len(nums)):\n"
    "        for j in range(i + 1, len(nums)):\n"
    "            if nums[i] + nums[j] == target:\n"
    "                return [i, j]\n"
    "    return []\n"
)
HASHMAP = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, v in enumerate(nums):\n"
    "        if target - v in seen:\n"
    "            return [seen[target - v], i]\n"
    "        seen[v] = i\n"
    "    return []\n"
)


def fresh_state(code: str = STARTER, **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {"baseline_features": analyze(code, "python").as_dict()}
    state.update(extra)
    return state


def run(
    status: str, at_s: float, kind: str = "test", passed: int = 0, total: int = 3, **extra: Any
) -> dict[str, Any]:
    return {
        "run_id": f"r{at_s}",
        "kind": kind,
        "status": status,
        "passed": passed,
        "total": total,
        "at_s": at_s,
        **extra,
    }


def names(results) -> list[str]:
    return [r.name for r in results]


# ----------------------------------------------------------------- triggers


def test_silence_by_default_without_triggers() -> None:
    ctx = make_context(current_code=STARTER, elapsed_s=100, seconds_since_last_edit=10)
    assert evaluate_triggers(ctx, "idle", fresh_state()) == []


def test_structural_change_fires_only_after_a_quiet_period() -> None:
    typing = make_context(current_code=NESTED, seconds_since_last_edit=2)
    assert "structural_change" not in names(evaluate_triggers(typing, "edit", fresh_state()))
    quiet = make_context(current_code=NESTED, seconds_since_last_edit=QUIET_TYPING_S + 1)
    results = evaluate_triggers(quiet, "idle", fresh_state())
    structural = next(r for r in results if r.name == "structural_change")
    assert structural.focus == "complexity"
    assert "nested loop" in structural.detail
    assert structural.template == "You've chosen a nested loop here. What is the current time complexity?"


def test_switch_to_hash_map_is_detected_against_the_baseline() -> None:
    ctx = make_context(current_code=HASHMAP, seconds_since_last_edit=10)
    result = next(
        r for r in evaluate_triggers(ctx, "idle", fresh_state(NESTED)) if r.name == "structural_change"
    )
    assert "introduced a hash map" in result.detail and "removed the nested loop" in result.detail
    assert result.template == "You changed the data structure to a hash map. Explain why."


def test_repeated_compile_errors() -> None:
    one = make_context(runs=[run("compile_error", 100)])
    assert "repeated_compile_errors" not in names(evaluate_triggers(one, "run_completed", fresh_state()))
    two = make_context(runs=[run("compile_error", 100), run("compile_error", 130)])
    results = evaluate_triggers(two, "run_completed", fresh_state())
    assert results[0].name == "repeated_compile_errors" and results[0].bypass_typing
    # Already commented on this streak length: no repeat.
    assert "repeated_compile_errors" not in names(
        evaluate_triggers(two, "run_completed", fresh_state(compile_streak_commented=2))
    )


def test_tests_passed_asks_about_edge_cases_once() -> None:
    ctx = make_context(runs=[run("failed", 100), run("passed", 160, passed=3)])
    result = next(
        r
        for r in evaluate_triggers(ctx, "run_completed", fresh_state(runs_seen=1))
        if r.name == "tests_passed"
    )
    assert result.focus == "edge_cases"
    assert result.template == "Your tests pass for the standard cases. What happens with an empty input?"
    assert "tests_passed" not in names(
        evaluate_triggers(ctx, "run_completed", fresh_state(runs_seen=1, passed_commented=True))
    )


def test_timeout_and_regression_and_runtime_error() -> None:
    timeout = make_context(runs=[run("timeout", 100)])
    assert "run_timeout" in names(evaluate_triggers(timeout, "run_completed", fresh_state()))
    regression = make_context(runs=[run("passed", 100, passed=3), run("failed", 200, passed=1)])
    assert "regression" in names(
        evaluate_triggers(regression, "run_completed", fresh_state(runs_seen=1, passed_commented=True))
    )
    crash = make_context(runs=[run("error", 100)])
    assert "run_error" in names(evaluate_triggers(crash, "run_completed", fresh_state()))


def test_ordinary_failing_run_is_remembered_without_a_trigger() -> None:
    state = fresh_state()
    ctx = make_context(runs=[run("failed", 100, passed=1)], elapsed_s=120)
    assert evaluate_triggers(ctx, "run_completed", state) == []
    assert state["runs_seen"] == 1


def test_platform_faults_are_ignored() -> None:
    ctx = make_context(runs=[run("error", 100, platform_fault=True), run("error", 110, platform_fault=True)])
    assert evaluate_triggers(ctx, "run_completed", fresh_state()) == []


def test_persistent_failures_after_three_silent_minutes() -> None:
    runs = [run("failed", 100), run("failed", 200)]
    early = make_context(runs=runs, elapsed_s=100 + PERSISTENT_FAILURE_S - 10)
    assert "persistent_failures" not in names(evaluate_triggers(early, "idle", fresh_state(runs_seen=2)))
    late = make_context(runs=runs, elapsed_s=100 + PERSISTENT_FAILURE_S + 5)
    assert "persistent_failures" in names(evaluate_triggers(late, "idle", fresh_state(runs_seen=2)))
    # The interviewer spoke after the failures started: not "silent".
    spoke = fresh_state(runs_seen=2, last_comment_at_s=150)
    assert "persistent_failures" not in names(evaluate_triggers(late, "idle", spoke))


def test_long_idle_fires_once_per_idle_stretch() -> None:
    ctx = make_context(seconds_since_last_edit=LONG_IDLE_S + 1, elapsed_s=400)
    state = fresh_state()
    result = next(r for r in evaluate_triggers(ctx, "idle", state) if r.name == "long_idle")
    state.update(result.marks)
    assert "long_idle" not in names(evaluate_triggers(ctx, "idle", state))


@pytest.mark.parametrize(
    ("limit", "elapsed", "expected"),
    [
        (1200, 599, None),
        (1200, 600, "half"),
        (1200, 901, "five_min"),
        (1200, 1141, "one_min"),
        (600, 300, "five_min"),  # 10-minute exercise: half-way == 5 minutes left -> one warning
        (300, 150, "half"),  # short exercise: no 5-minute warning
        (300, 241, "one_min"),
    ],
)
def test_time_warnings(limit: int, elapsed: float, expected: str | None) -> None:
    ctx = make_context(time_limit_s=limit, elapsed_s=elapsed)
    results = [r for r in evaluate_triggers(ctx, "tick", fresh_state()) if r.name == "time_warning"]
    if expected is None:
        assert results == []
    else:
        assert expected.replace("_", " ") in results[0].detail
        assert results[0].use_model is False and results[0].bypass_gap


def test_time_warnings_are_issued_once_and_lesser_ones_are_skipped() -> None:
    state = fresh_state()
    ctx = make_context(time_limit_s=1200, elapsed_s=1150)
    warning = next(r for r in evaluate_triggers(ctx, "tick", state) if r.name == "time_warning")
    state.update(warning.marks)
    assert state["time_warnings"] == ["half", "five_min", "one_min"]
    assert not [r for r in evaluate_triggers(ctx, "tick", state) if r.name == "time_warning"]


@pytest.mark.parametrize("trigger", ["submitted", "time_up", "hint_requested"])
def test_flows_owned_elsewhere_never_comment(trigger: str) -> None:
    ctx = make_context(current_code=NESTED, seconds_since_last_edit=60, runs=[run("passed", 1, passed=3)])
    assert evaluate_triggers(ctx, trigger, fresh_state()) == []


# --------------------------------------------------------------- rate limits

MATRIX = [
    # (description, trigger, ctx overrides, state overrides, section kwargs, expected trigger or None, suppression reason)
    (
        "structural, quiet, first comment",
        "idle",
        {"current_code": NESTED, "seconds_since_last_edit": 10},
        {},
        {},
        "structural_change",
        None,
    ),
    (
        "structural, typing",
        "idle",
        {"current_code": NESTED, "seconds_since_last_edit": 2},
        {},
        {},
        None,
        None,
    ),
    (
        "structural, within min gap",
        "idle",
        {"current_code": NESTED, "seconds_since_last_edit": 10, "elapsed_s": 300},
        {"last_comment_at_s": 260},
        {"min_seconds_between_comments": 75},
        None,
        "min_gap",
    ),
    (
        "structural, gap elapsed",
        "idle",
        {"current_code": NESTED, "seconds_since_last_edit": 10, "elapsed_s": 340},
        {"last_comment_at_s": 260},
        {"min_seconds_between_comments": 75},
        "structural_change",
        None,
    ),
    (
        "max comments reached",
        "idle",
        {"current_code": NESTED, "seconds_since_last_edit": 10},
        {"comments_made": MAX_COMMENTS_PER_PROBLEM},
        {},
        None,
        "max_comments",
    ),
    (
        "proactive comments disabled",
        "idle",
        {"current_code": NESTED, "seconds_since_last_edit": 10},
        {"proactive_comments": False},
        {},
        None,
        "proactive_comments_disabled",
    ),
    (
        "run just finished while typing",
        "run_completed",
        {"runs": [run("passed", 290, passed=3)], "seconds_since_last_edit": 1, "elapsed_s": 300},
        {},
        {},
        "tests_passed",
        None,
    ),
    (
        "run result seen later while typing",
        "edit",
        {"runs": [run("passed", 290, passed=3)], "seconds_since_last_edit": 1, "elapsed_s": 300},
        {},
        {},
        None,
        "typing",
    ),
    (
        "run finished but min gap",
        "run_completed",
        {"runs": [run("passed", 290, passed=3)], "seconds_since_last_edit": 1, "elapsed_s": 300},
        {"last_comment_at_s": 280},
        {},
        None,
        "min_gap",
    ),
    (
        "candidate speech bypasses everything",
        "candidate_speech",
        {"seconds_since_last_edit": 0, "candidate_explanations": ["Can I assume the array is sorted?"]},
        {"last_comment_at_s": 299, "comments_made": 99, "proactive_comments": False},
        {},
        "candidate_speech",
        None,
    ),
    (
        "one-minute warning while typing and over the cap",
        "edit",
        {"time_limit_s": 1200, "elapsed_s": 1150, "seconds_since_last_edit": 0},
        {"comments_made": 99, "last_comment_at_s": 1140},
        {},
        "time_warning",
        None,
    ),
    (
        "five-minute warning waits for typing to pause",
        "edit",
        {"time_limit_s": 1200, "elapsed_s": 910, "seconds_since_last_edit": 0},
        {"time_warnings": ["half"]},
        {},
        None,
        "typing",
    ),
    (
        "time warning ignores proactive=false",
        "tick",
        {"time_limit_s": 1200, "elapsed_s": 910, "seconds_since_last_edit": 30},
        {"time_warnings": ["half"], "proactive_comments": False},
        {},
        "time_warning",
        None,
    ),
]


@pytest.mark.parametrize(
    ("label", "trigger", "ctx", "state", "section", "expected", "reason"), MATRIX, ids=[m[0] for m in MATRIX]
)
def test_rate_limit_matrix(label, trigger, ctx, state, section, expected, reason) -> None:
    context = make_context(**ctx)
    st = fresh_state(**state)
    if section:
        st.pop("min_seconds_between_comments", None)
    chosen, suppressed = select_trigger(
        context, trigger, st, section=CodingSection(**section) if section else None
    )
    assert (chosen.name if chosen else None) == expected, (label, suppressed)
    if reason is not None:
        assert reason in [r for _, r in suppressed], suppressed


# ----------------------------------------------------------- decide (model)


async def test_decide_is_silent_and_never_calls_the_model_without_a_trigger(mock_ai) -> None:
    ctx = make_context(current_code=STARTER, seconds_since_last_edit=10)
    assert await decide(ctx, "idle", fresh_state(), ai=mock_ai) is None
    assert mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION) == []


async def test_decide_rate_limited_trigger_does_not_call_the_model(mock_ai) -> None:
    ctx = make_context(current_code=NESTED, seconds_since_last_edit=10, elapsed_s=300)
    state = fresh_state(last_comment_at_s=290)
    assert await decide(ctx, "idle", state, ai=mock_ai) is None
    assert mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION) == []
    assert state["suppressed"] >= 1


async def test_decide_speaks_with_model_phrasing_and_updates_state(mock_ai) -> None:
    mock_ai.llm.script(
        ModelTask.CODE_OBSERVATION,
        CodeObservation(
            should_speak=True,
            kind="question",
            focus="complexity",
            behavior=AvatarBehavior(),
            speech="You've chosen a nested loop here. What is the current time complexity?",
            reason="nested loop introduced",
        ),
    )
    ctx = make_context(current_code=NESTED, seconds_since_last_edit=10, elapsed_s=300)
    state = fresh_state()
    decision = await decide(ctx, "idle", state, ai=mock_ai, section=CodingSection())
    assert decision is not None and decision.should_speak and decision.used_model
    assert decision.speech.startswith("You've chosen a nested loop")
    assert decision.behavior.state == AvatarState.QUESTIONING
    assert decision.trigger == "structural_change"
    assert state["comments_made"] == 1 and state["last_comment_at_s"] == 300
    assert analyze(NESTED, "python").as_dict() == state["baseline_features"]
    call = mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION)[0]
    prompt = call.messages[1]["content"]
    assert "<candidate_code>" in prompt and "nested loop" in prompt
    assert "Fairness and evidence rules" in call.messages[0]["content"]
    # Immediately afterwards the same change does not fire again (baseline moved, min gap).
    assert await decide(ctx, "idle", state, ai=mock_ai) is None


async def test_decide_model_chooses_silence(mock_ai) -> None:
    mock_ai.llm.script(
        ModelTask.CODE_OBSERVATION, CodeObservation(should_speak=False, reason="candidate is making progress")
    )
    ctx = make_context(current_code=NESTED, seconds_since_last_edit=10)
    state = fresh_state()
    decision = await decide(ctx, "idle", state, ai=mock_ai)
    assert decision is not None and decision.should_speak is False and decision.speech == ""
    assert state.get("comments_made", 0) == 0
    assert state["baseline_features"]["nested_loops"] is True  # marks applied: do not re-trigger immediately


async def test_decide_stays_silent_when_model_times_out(mock_ai) -> None:
    """One attempt within the engine's 8 s wait; a proactive comment is
    optional, so a slow model means silence (no late canned line, no retry)."""
    from app.services.coding import observer as observer_module

    seen: dict = {}
    structured = mock_ai.llm.structured

    async def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.update(timeout=kwargs.get("timeout"), max_attempts=kwargs.get("max_attempts"))
        return await structured(*args, **kwargs)

    mock_ai.llm.structured = spy
    mock_ai.llm.script(
        ModelTask.CODE_OBSERVATION,
        AITimeout("slow"),
        CodeObservation(should_speak=True, kind="nudge", speech="Would be a retry.", reason="x"),
    )
    ctx = make_context(current_code=HASHMAP, seconds_since_last_edit=10)
    state = fresh_state(NESTED)
    decision = await decide(ctx, "idle", state, ai=mock_ai)
    assert decision is not None and decision.should_speak is False and decision.speech == ""
    assert decision.reason == "model_unavailable" and decision.trigger == "structural_change"
    assert seen == {"timeout": observer_module.OBSERVATION_TIMEOUT_S, "max_attempts": 1}
    assert observer_module.OBSERVATION_TIMEOUT_S < 8.0  # coding_bridge.OBSERVER_TIMEOUT_S
    [call] = mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION)
    assert call.attempts == 1
    assert state.get("comments_made", 0) == 0
    # Marked: the same change does not hit the model again on the next tick.
    assert await decide(ctx, "idle", state, ai=mock_ai) is None
    assert len(mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION)) == 1


async def test_candidate_question_still_answered_when_model_times_out(mock_ai) -> None:
    mock_ai.llm.script(ModelTask.CODE_OBSERVATION, AITimeout("slow"))
    ctx = make_context(
        seconds_since_last_edit=0, candidate_explanations=["Can I assume the input is sorted?"]
    )
    decision = await decide(ctx, "candidate_speech", fresh_state(), ai=mock_ai)
    assert decision is not None and decision.should_speak and decision.speech
    assert not decision.used_model


async def test_decide_rejects_code_in_speech(mock_ai) -> None:
    mock_ai.llm.script(
        ModelTask.CODE_OBSERVATION,
        CodeObservation(
            should_speak=True, kind="nudge", speech="Try this: ```seen = {}``` and return early;", reason="x"
        ),
    )
    ctx = make_context(current_code=NESTED, seconds_since_last_edit=10)
    decision = await decide(ctx, "idle", fresh_state(), ai=mock_ai)
    assert decision is not None and "```" not in decision.speech and not decision.used_model


async def test_candidate_speech_always_gets_an_answer_even_if_model_declines(mock_ai) -> None:
    mock_ai.llm.script(ModelTask.CODE_OBSERVATION, CodeObservation(should_speak=False))
    ctx = make_context(
        seconds_since_last_edit=0, candidate_explanations=["Can I assume there is exactly one answer?"]
    )
    decision = await decide(ctx, "candidate_speech", fresh_state(comments_made=99), ai=mock_ai)
    assert decision is not None and decision.should_speak and decision.speech
    prompt = mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION)[0].messages[1]["content"]
    assert "MUST speak" in prompt and "exactly one answer" in prompt


async def test_time_warning_uses_fixed_phrasing_without_model(mock_ai) -> None:
    ctx = make_context(time_limit_s=1200, elapsed_s=1150, seconds_since_last_edit=0)
    state = fresh_state()
    decision = await decide(ctx, "tick", state, ai=mock_ai)
    assert decision is not None and decision.kind == "time_warning" and "one minute" in decision.speech
    assert mock_ai.llm.calls_for(ModelTask.CODE_OBSERVATION) == []
    assert state.get("comments_made", 0) == 0  # warnings do not count toward the comment cap


def test_clean_speech_strips_markdown_and_bounds_length() -> None:
    assert clean_speech("**Why** a `dict`?") == "Why a dict?"
    long = " ".join(["word"] * 80) + "."
    assert len(clean_speech(long).split()) <= 45
