from __future__ import annotations

import pytest

from app.realtime.endpointing import (
    Endpointer,
    EndpointParams,
    asks_for_time,
    ends_complete_sentence,
    required_silence_ms,
)

P = EndpointParams(base_ms=850, short_ms=600, long_ms=1700, think_max_ms=25_000, max_utterance_ms=240_000)


@pytest.mark.parametrize(
    ("text", "expected", "reason"),
    [
        ("We ran three node groups across three availability zones.", 600, "short_complete"),
        ("We ran three node groups across three zones and", 1700, "long_trailing"),
        ("The main reason was cost, so", 1700, "long_trailing"),
        ("I think it was because", 4000, "fragment"),  # a short fragment stopping mid-sentence
        ("Um", 1700, "long_trailing"),
        ("Yes.", 1700, "long_short_text"),
        ("Not sure", 1700, "long_short_text"),
        ("We ran three node groups across three zones", 850, "base"),
        ("Short complete one.", 850, "base"),  # complete but under 6 words
        ("", 850, "base"),
        ("Hmm, let me think about that for a second", 25_000, "think_pause"),
        ("Good question. Give me a moment.", 25_000, "think_pause"),
        ("Hold on, one second", 25_000, "think_pause"),
    ],
)
def test_required_silence(text: str, expected: int, reason: str) -> None:
    assert required_silence_ms(text, P) == (expected, reason)


def test_think_phrase_only_counts_near_the_end() -> None:
    early = (
        "Let me think. "
        + "We migrated every service to Kubernetes over two quarters with zero downtime. " * 3
    )
    assert not asks_for_time(early)
    assert required_silence_ms(early.strip(), P)[1] == "short_complete"


def test_sentence_end_detection() -> None:
    assert ends_complete_sentence("It worked.")
    assert ends_complete_sentence('He said "done."')
    assert not ends_complete_sentence("It worked...")
    assert not ends_complete_sentence("It worked")


def test_endpointer_waits_while_speaking_and_ends_after_required_silence() -> None:
    ep = Endpointer(P)
    text = "We used Karpenter for node autoscaling across three zones."
    assert not ep.decide(text, silence_ms=5000, turn_ms=8000, speaking=True).end
    assert not ep.decide(text, silence_ms=550, turn_ms=8000, speaking=False).end
    decision = ep.decide(text, silence_ms=600, turn_ms=8000, speaking=False)
    assert decision.end and decision.reason == "short_complete"
    trailing = "We used Karpenter for node autoscaling and"
    assert not ep.decide(trailing, silence_ms=1000, turn_ms=8000, speaking=False).end
    assert ep.decide(trailing, silence_ms=1700, turn_ms=8000, speaking=False).end


def test_max_utterance_forces_the_end_even_while_speaking() -> None:
    decision = Endpointer(P).decide("still talking", silence_ms=0, turn_ms=240_000, speaking=True)
    assert decision.end and decision.reason == "max_utterance"


def test_params_from_settings(settings) -> None:
    params = EndpointParams.from_settings(settings)
    assert params.base_ms == settings.endpoint_silence_ms
    assert params.think_max_ms == settings.think_pause_max_seconds * 1000
    assert params.max_utterance_ms == settings.max_utterance_seconds * 1000


def test_unsettled_text_never_gets_the_short_wait() -> None:
    """While the last segment is still being transcribed the newest words are
    unknown: a finished-looking older sentence must not end the turn early."""
    from app.realtime.endpointing import EndpointParams, required_silence_ms

    params = EndpointParams()
    text = "We migrated forty services to EKS over nine months."
    assert required_silence_ms(text, params, settled=True) == (600, "short_complete")
    assert required_silence_ms(text, params, settled=False) == (850, "base")


def test_trailing_conjunction_counts_even_after_a_whisper_period() -> None:
    from app.realtime.endpointing import EndpointParams, required_silence_ms

    params = EndpointParams()
    assert required_silence_ms("We moved the batch jobs to spot instances and.", params)[1] == "long_trailing"
    assert required_silence_ms("I think the main lesson was, um,", params)[1] == "long_trailing"


def test_commit_policy_on_the_final_transcript() -> None:
    from app.realtime.endpointing import EndpointParams, commit_required_ms

    params = EndpointParams()
    assert commit_required_ms("We used Karpenter for node autoscaling.", params) == params.short_ms
    assert commit_required_ms("Our first step was to", params) == params.fragment_ms  # unfinished, short
    assert commit_required_ms("spot instances and then", params) == params.fragment_ms
    assert commit_required_ms("We moved the batch jobs to spot instances and", params) == params.long_ms
    assert commit_required_ms("Yes.", params) == params.long_ms  # very short: wait for more
    assert commit_required_ms("Let me think about that for a second.", params) == params.think_max_ms
    assert commit_required_ms("", params) == 0


@pytest.mark.parametrize(
    "text",
    ["so for that", "So for that.", "and the", "Okay, so", "But because", "We used the", "and about this"],
)
def test_short_fragments_wait_the_fragment_hold(text: str) -> None:
    """Live intake 2026-10-10: "so for that" + a thinking pause got a new question after 1.7 s. A short fragment
    that stops mid-sentence now waits ``fragment_ms`` in both stages."""
    from app.realtime.endpointing import EndpointParams, commit_required_ms, required_silence_ms

    params = EndpointParams()
    assert required_silence_ms(text, params) == (params.fragment_ms, "fragment")
    assert commit_required_ms(text, params) == params.fragment_ms
    ep = Endpointer(params)
    assert not ep.decide(text, silence_ms=params.long_ms + 500, turn_ms=3000, speaking=False).end
    assert ep.decide(text, silence_ms=params.fragment_ms, turn_ms=3000, speaking=False).end


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I did that.", "long_trailing"),  # short but complete: "that" is not a continuation tail
        ("Thanks for that.", "long_trailing"),
        ("It was.", "long_trailing"),
        ("Um", "long_trailing"),  # hesitation, not a fragment
        ("The main reason was cost, so", "long_trailing"),  # Indian English trailing "so" after content
        ("Yes.", "long_short_text"),
    ],
)
def test_complete_short_answers_and_hesitations_keep_their_waits(text: str, expected: str) -> None:
    from app.realtime.endpointing import EndpointParams, required_silence_ms

    assert required_silence_ms(text, EndpointParams())[1] == expected


def test_fragment_hold_comes_from_settings(settings) -> None:
    params = EndpointParams.from_settings(settings)
    assert params.fragment_ms == settings.endpoint_fragment_hold_ms == 4000
