from __future__ import annotations

import pytest

from app.domain.enums import AnswerQuality
from app.services.interview.special import SpecialKind, classify, is_backchannel, normalize

K = SpecialKind


@pytest.mark.parametrize(
    "text",
    [
        "Could you repeat the question?",
        "can you repeat that please",
        "Sorry, could you say that again?",
        "Would you mind repeating that? I didn't catch it",
        "I didn't catch that",
        "Sorry, I didn't hear the question.",
        "What was the question again?",
        "One more time please?",
        "Come again?",
        "Sorry?",
        "Pardon?",
        "You cut out, can you repeat?",
        "Can you rephrase?",
    ],
)
def test_repeat_requests(text: str) -> None:
    result = classify(text)
    assert result.kind is K.REPEAT, (text, result)
    assert result.quality is AnswerQuality.CLARIFICATION_REQUEST
    assert not result.is_answer


@pytest.mark.parametrize(
    "text",
    [
        "What do you mean by scale?",
        "What exactly do you mean?",
        "Could you clarify what you're looking for?",
        "Do you mean the production cluster or staging?",
        "Are you asking about the migration itself?",
        "I'm not sure I understand the question.",
        "Which project do you mean?",
        "Just to clarify, is this about AWS or GCP?",
        "In what context?",
    ],
)
def test_clarification_requests(text: str) -> None:
    assert classify(text).kind is K.CLARIFICATION, text


@pytest.mark.parametrize(
    "text",
    [
        "Let me think for a second.",
        "Give me a moment",
        "Hmm, let me think",
        "Hold on.",
        "Just a sec",
        "Bear with me",
        "Can I take a minute?",
        "That's a good question.",
        "um",
        "hmm",
        "uh...",
    ],
)
def test_wants_time(text: str) -> None:
    result = classify(text)
    assert result.kind is K.WANTS_TIME, (text, result)
    assert result.quality is AnswerQuality.WANTS_TIME


@pytest.mark.parametrize(
    "text",
    [
        "I don't know.",
        "Honestly, I don't know",
        "Hmm, I honestly don't know that one.",
        "I really just don't know",
        "I'm honestly not sure.",
        "No idea.",
        "I'm not familiar with that.",
        "I haven't used Helm.",
        "I have never worked with Kafka",
        "I can't remember",
        "I'm drawing a blank",
        "Pass",
        "Can we skip this one?",
    ],
)
def test_dont_know(text: str) -> None:
    result = classify(text)
    assert result.kind is K.DONT_KNOW, (text, result)
    assert result.quality is AnswerQuality.NO_ANSWER
    assert result.is_answer  # still evaluated: it is evidence


@pytest.mark.parametrize(
    "text",
    ["mm-hmm", "Mhm.", "Yeah.", "okay", "Sure", "Right.", "Got it", "I see", "yeah sure", "OK got it"],
)
def test_backchannels(text: str) -> None:
    result = classify(text)
    assert result.kind is K.BACKCHANNEL, (text, result)
    assert result.quality is None
    assert is_backchannel(text)


@pytest.mark.parametrize(
    "text",
    [
        "Can I get a hint?",
        "Could you give me a hint",
        "Any hints?",
        "I'd like a hint please",
        "I think I need a hint here",
    ],
)
def test_hint_requests(text: str) -> None:
    result = classify(text, coding_active=True)
    assert result.kind is K.WANTS_HINT
    assert result.confidence >= 0.9
    assert classify(text).confidence < result.confidence


@pytest.mark.parametrize(
    "text",
    [
        "What does a typical day look like for the team?",
        "What's the tech stack?",
        "When will I hear back?",
        "How big is the team?",
        "Is this role remote?",
        "What are the next steps?",
        "Who would I be reporting to?",
        "What technologies does the team use?",
    ],
)
def test_candidate_questions(text: str) -> None:
    result = classify(text)
    assert result.kind is K.CANDIDATE_QUESTION, (text, result)
    assert result.quality is AnswerQuality.CANDIDATE_QUESTION


@pytest.mark.parametrize(
    "text",
    [
        "We ran forty nodes on EKS with the cluster autoscaler and Karpenter for spot capacity.",
        "I'm not sure about the exact number, it was around 40 nodes across three AZs",
        "I don't know the exact figure, but I think we saved roughly thirty percent on compute.",
        "Our pipeline pass rate was ninety percent after we added retries",
        "Yes, I led the migration from self-managed Kubernetes to EKS over six months.",
        "So the main problem was that the ingress controller was a single point of failure.",
        "Honestly the hardest part was getting buy-in; let me think about how to explain the rollout plan we used.",
        "We used Terraform modules with remote state in S3 and DynamoDB locking.",
    ],
)
def test_real_answers_are_not_special(text: str) -> None:
    result = classify(text)
    assert result.kind is K.NONE, (text, result)
    assert result.is_answer
    assert result.quality is None


def test_empty_and_noise() -> None:
    assert classify("").kind is K.EMPTY
    assert classify("   ...  ").kind is K.EMPTY
    assert classify("").quality is AnswerQuality.NO_ANSWER
    assert is_backchannel("")


def test_long_text_with_phrase_is_not_special() -> None:
    long_answer = (
        "Let me think about that. In my last role I owned the observability stack: we ran Prometheus with Thanos "
        "for long-term storage, defined SLOs per service and paged only on burn rate alerts."
    )
    assert classify(long_answer).kind is K.NONE


def test_candidate_question_requires_question_form_when_awaiting() -> None:
    statement = "the tech stack at my last company was Go and Postgres"
    assert classify(statement).kind is K.NONE


def test_normalize_unifies_apostrophes_and_dashes() -> None:
    assert normalize("I DON’T know — mm-hmm") == "i don't know   mm hmm".replace("   ", " ")
