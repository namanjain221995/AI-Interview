"""Tier 1: the interviewer's instructions carry no domain-specific example sentences. A literal example leaked
into live speech ("Roughly how long did the cutover take?" with no cutover mentioned) and DevOps examples tilted
every interview (frontend, ML, ...) toward DevOps phrasing."""

from __future__ import annotations

import pytest

from app.services.interview import policy, prompts

LEAKY = ("cutover", "karpenter", "zone drops out", "switching to aws", "120 nodes", "eks migration", "forty-service")


def _instruction_texts() -> list[str]:
    texts = [prompts.SPEECH_RULES, prompts.TURN_SYSTEM.template, prompts.TURN_USER.template]
    texts += [str(v) for v in vars(policy).values() if isinstance(v, dict) for v in v.values() if isinstance(v, str)]
    return texts


@pytest.mark.parametrize("phrase", LEAKY)
def test_no_domain_specific_example_in_the_interviewer_instructions(phrase: str) -> None:
    for text in _instruction_texts():
        assert phrase not in text.lower(), phrase
