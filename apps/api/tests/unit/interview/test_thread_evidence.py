"""Tier 1 "thread memory": the turn prompt shows what the current question thread already holds."""

from __future__ import annotations

import uuid

from app.domain.enums import TurnAction
from app.services.interview.context import thread_evidence, user_prompt
from app.services.interview.policy import compute_directive
from app.services.interview.state import AnswerRecord

from .factories import ask, round_config, state


def _answer(s, question_id: str, text: str) -> None:
    s.answers.append(
        AnswerRecord(id=str(uuid.uuid4()), question_id=question_id, sequence=len(s.answers) + 1, text=text)
    )


def test_the_thread_shows_expected_coverage_and_earlier_answers_but_not_other_topics() -> None:
    s = state(rc=round_config(), elapsed_s=6 * 60)
    other = ask(s, "aws")
    _answer(s, other.id, "We used reserved instances for the baseline.")
    root = ask(s, "kubernetes", topic="rollbacks")
    _answer(s, root.id, "We rolled back with Argo Rollouts when the error rate doubled.")
    follow = ask(s, "kubernetes", action=TurnAction.ASK_FOLLOWUP, topic="rollbacks")
    follow.parent_id = root.id
    follow.expected_evidence = ["the metric that triggered it", "how long it took"]
    last = "It took about four minutes end to end."
    _answer(s, follow.id, last)
    items = thread_evidence(s, last_answer=last)
    assert (
        items[0]
        == "A complete answer to your current question covers: the metric that triggered it; how long it took"
    )
    assert any("Argo Rollouts" in item for item in items)
    assert not any("reserved instances" in item for item in items)  # another thread
    assert not any("four minutes" in item for item in items)  # shown as LAST ANSWER instead


def test_the_thread_reaches_the_prompt_as_fenced_data() -> None:
    s = state(rc=round_config(), elapsed_s=6 * 60)
    q = ask(s, "kubernetes")
    q.expected_evidence = ["node pool sizing"]
    prompt = user_prompt(s, compute_directive(s), last_answer="Some answer.", evidence=thread_evidence(s))
    assert "THIS QUESTION THREAD" in prompt and "node pool sizing" in prompt
    assert "<derived_evidence" in prompt  # data, never instructions


def test_no_current_question_means_no_thread() -> None:
    assert thread_evidence(state(rc=round_config())) == []
