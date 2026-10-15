"""At most one re-ask of the same point (owner session: "a specific Prometheus metric" asked four times)."""

from __future__ import annotations

import pytest

from app.domain.enums import TurnAction
from app.services.interview.policy import compute_directive, same_point, same_point_asks
from app.services.interview.prompts import SPEECH_RULES
from app.services.interview.special import classify

from .factories import T0, ask, state


@pytest.mark.parametrize(
    "a,b",
    [
        ("Prometheus health metric", "Specific Prometheus metric"),
        ("Specific Prometheus metric", "Specific Prometheus metric for health"),
        ("pipeline stages and protection", "pipeline stages and protection"),
    ],
)
def test_the_same_point_asked_again(a: str, b: str) -> None:
    assert same_point(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        # Real follow-ups from the same session that go one level deeper on the topic: not repeats.
        ("Terraform provisioning time reduction details", "Baseline duration for Terraform time savings"),
        ("Baseline duration for Terraform time savings", "Personal role in Terraform implementation"),
        ("pipeline structure and safety", "pipeline stages and protection"),
        ("availability implementation details", "node failure handling mechanism"),
        ("canary rollout design", "metric that gated the canary rollout"),
    ],
)
def test_a_deeper_follow_up_is_not_a_repeat(a: str, b: str) -> None:
    assert not same_point(a, b)


def _asked(s, focus: str, action: TurnAction = TurnAction.ASK_FOLLOWUP) -> None:
    q = ask(s, "kubernetes", action=action)
    q.focus = focus


def test_after_two_asks_of_the_same_point_the_interview_moves_on() -> None:
    s = state()
    _asked(s, "SLO and alerting design", TurnAction.CHANGE_TOPIC)
    _asked(s, "Prometheus health metric", TurnAction.SIMPLIFY)
    assert same_point_asks(s) == 1
    d = compute_directive(s, special=classify("I check the logs to see if the site is up and running."), now=T0)
    assert not any("asked for this point twice" in i for i in d.instructions)
    _asked(s, "Specific Prometheus metric", TurnAction.REQUEST_SPECIFICITY)
    assert same_point_asks(s) == 2
    d = compute_directive(s, special=classify("The health service, I look at that one in the dashboard."), now=T0)
    assert d.max_followups_reached
    assert TurnAction.REQUEST_SPECIFICITY not in d.allowed_actions and TurnAction.ASK_FOLLOWUP not in d.allowed_actions
    assert any("asked for this point twice" in i for i in d.instructions)


def test_a_repeat_request_may_still_hear_the_question_again() -> None:
    s = state()
    _asked(s, "Prometheus health metric", TurnAction.SIMPLIFY)
    _asked(s, "Specific Prometheus metric", TurnAction.REQUEST_SPECIFICITY)
    d = compute_directive(s, special=classify("Sorry, could you repeat the question?"), now=T0)
    assert not any("asked for this point twice" in i for i in d.instructions)


def test_speech_rules_forbid_answer_hints_and_labels() -> None:
    rules = " ".join(SPEECH_RULES.split())
    assert "never offer candidates or examples of the answer" in rules
    assert 'never challenge with "actually"' in rules
