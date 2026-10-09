from __future__ import annotations

import random

import pytest

from app.ai.visemes import (
    BASE_DURATION_MS,
    OCULUS_VISEMES,
    VOWEL_VISEMES,
    estimate_duration_ms,
    number_to_words,
    phonemes_to_visemes,
    text_to_visemes,
    word_to_visemes,
)

SENTENCES = [
    "Hello! Could you walk me through how you scaled the Kubernetes cluster?",
    "That's great; thanks.",
    "We handled 10,000 requests per second at 99.9% uptime.",
    "Why?",
    "Mm-hmm.",
    "Tell me about a time you disagreed with your manager — what happened next?",
    "",
    "...",
]


@pytest.mark.parametrize("text", SENTENCES)
@pytest.mark.parametrize("duration", [None, 350, 2400, 9000])
def test_timeline_invariants(text: str, duration: int | None) -> None:
    cues = text_to_visemes(text, duration)
    assert cues, "always at least one cue"
    assert cues[0].t_ms == 0
    assert all(c.viseme in OCULUS_VISEMES for c in cues)
    assert all(0.0 <= c.weight <= 1.0 for c in cues)
    times = [c.t_ms for c in cues]
    assert times == sorted(times) and len(set(times)) == len(times), "strictly increasing"
    assert cues[-1].viseme == "sil", "mouth closes at the end"
    if duration:
        assert cues[-1].t_ms < duration
    for prev, cur in zip(cues, cues[1:], strict=False):
        assert prev.viseme != cur.viseme, "consecutive duplicates are merged"


def test_scaling_matches_audio_duration() -> None:
    text = SENTENCES[0]
    natural = text_to_visemes(text)
    fast = text_to_visemes(text, 1500)
    slow = text_to_visemes(text, 6000)
    assert [c.viseme for c in fast] == [c.viseme for c in slow] == [c.viseme for c in natural]
    assert slow[-1].t_ms > fast[-1].t_ms
    assert fast[-1].t_ms < 1500 and slow[-1].t_ms < 6000
    ratio = slow[len(slow) // 2].t_ms / fast[len(fast) // 2].t_ms
    assert 3.5 < ratio < 4.5


def test_vowels_are_longer_than_consonants() -> None:
    assert min(BASE_DURATION_MS[v] for v in VOWEL_VISEMES) > max(
        d for v, d in BASE_DURATION_MS.items() if v not in VOWEL_VISEMES
    )
    cues = text_to_visemes("papa")  # PP aa PP aa
    spans = [b.t_ms - a.t_ms for a, b in zip(cues, cues[1:], strict=False)]
    assert [c.viseme for c in cues] == ["PP", "aa", "PP", "aa", "sil"]
    assert spans[1] > spans[0] and spans[3] > spans[2]


def test_punctuation_pauses() -> None:
    cues = text_to_visemes("Yes. No")
    labels = [c.viseme for c in cues]
    assert labels.index("sil") > 0
    sil = cues[labels.index("sil")]
    nxt = cues[labels.index("sil") + 1]
    assert nxt.t_ms - sil.t_ms >= 250  # sentence pause
    comma = text_to_visemes("Yes, no")
    comma_sil = next(c for c in comma if c.viseme == "sil")
    after = comma[comma.index(comma_sil) + 1]
    assert 100 <= after.t_ms - comma_sil.t_ms < 250


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("think", ["TH", "I", "nn", "kk"]),
        ("ship", ["CH", "I", "PP"]),
        ("phone", ["FF", "O", "nn"]),
        ("make", ["PP", "aa", "kk"]),
        ("city", ["SS", "I", "DD", "I"]),
        ("queue", ["kk", "U", "E", "U"]),
        ("knight", ["nn", "aa", "I", "DD"]),
        ("box", ["PP", "O", "kk", "SS"]),
        ("hmm", ["PP"]),
    ],
)
def test_grapheme_rules(word: str, expected: list[str]) -> None:
    assert word_to_visemes(word) == expected


def test_numbers_are_spelled_out() -> None:
    assert number_to_words(10000) == "ten thousand"
    assert number_to_words(1234567) == "one million two hundred thirty four thousand five hundred sixty seven"
    assert number_to_words(42) == "forty two"
    with_digits = text_to_visemes("I have 3 cats")
    with_words = text_to_visemes("I have three cats")
    assert [c.viseme for c in with_digits] == [c.viseme for c in with_words]


def test_estimate_duration_scales_with_speed() -> None:
    base = estimate_duration_ms("Could you explain the trade-offs?")
    assert 1200 < base < 4500
    assert estimate_duration_ms("Could you explain the trade-offs?", speed=2.0) == pytest.approx(
        base / 2, abs=1
    )
    assert estimate_duration_ms("") == 0


def test_ipa_mapping() -> None:
    cues = phonemes_to_visemes("həlˈoʊ wˈɜːld.", 1000)
    assert [c.viseme for c in cues] == ["E", "nn", "O", "U", "E", "nn", "DD", "sil"]
    assert cues[-1].t_ms < 1000
    misaki = phonemes_to_visemes("ðə kwˈɪk bɹˈWn fˈɑks")  # misaki "W" = aʊ
    labels = [c.viseme for c in misaki]
    assert labels[:2] == ["TH", "E"] and "RR" in labels and "aa" in labels
    long_vowel = phonemes_to_visemes("ɑː")
    short_vowel = phonemes_to_visemes("ɑ")
    assert long_vowel[1].t_ms > short_vowel[1].t_ms


@pytest.mark.parametrize("seed", range(5))
def test_random_text_never_breaks(seed: int) -> None:
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz ABCXYZ0123456789.,!?;:'\"()-—–…\n%&$#@=+/*é中😀"
    for _ in range(200):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        duration = rng.choice([None, 1, 50, 1000, 20000])
        cues = text_to_visemes(text, duration)
        times = [c.t_ms for c in cues]
        assert times == sorted(times) and len(set(times)) == len(times)
        assert cues[-1].viseme == "sil"
