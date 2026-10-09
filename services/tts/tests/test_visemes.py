from __future__ import annotations

import numpy as np
import pytest
from kokoro_onnx.config import DEFAULT_VOCAB

from tts_service.audio import FRAME_SECONDS
from tts_service.visemes import (
    ASPIRATE,
    CLOSING_MARKS,
    MODIFIERS,
    OPENING_MARKS,
    PAUSE_MARKS,
    STRESS_MARKS,
    VISEME_OF,
    VISEMES,
    Cue,
    PhonemeSpan,
    _match_pauses,
    estimate_spans,
    has_phones,
    is_phone,
    viseme_cues,
)


def flat_levels(total: float, db: float = -6.0) -> np.ndarray:
    return np.full(int(round(total / FRAME_SECONDS)), db)


def spans_of(tokens: list[tuple[str, float, float]]) -> list[PhonemeSpan]:
    return [PhonemeSpan(symbol, start, end) for symbol, start, end in tokens]


def contiguous(symbols: str, step: float = 0.05, start: float = 0.0) -> list[PhonemeSpan]:
    return [PhonemeSpan(s, start + i * step, start + (i + 1) * step) for i, s in enumerate(symbols)]


def cues_without_audio(spans: list[PhonemeSpan]) -> list[Cue]:
    total = spans[-1].end
    return viseme_cues(spans, flat_levels(total), 0.0, total, [], total)


def as_pairs(cues: list[Cue]) -> list[tuple[float, str]]:
    return [(round(cue.time, 3), cue.viseme) for cue in cues]


# ------------------------------------------------------------------ mapping


def test_every_kokoro_vocabulary_symbol_is_classified() -> None:
    unclassified = [
        symbol
        for symbol in DEFAULT_VOCAB
        if symbol not in VISEME_OF
        and symbol != ASPIRATE
        and symbol not in STRESS_MARKS | MODIFIERS | PAUSE_MARKS | OPENING_MARKS | CLOSING_MARKS
        and not symbol.isspace()
    ]
    assert unclassified == []


def test_mapping_targets_are_oculus_visemes() -> None:
    assert set(VISEME_OF.values()) <= set(VISEMES)
    assert len(VISEMES) == 15


@pytest.mark.parametrize(
    ("symbols", "viseme"),
    [
        ("pbm", "PP"),
        ("fv", "FF"),
        ("θð", "TH"),
        ("tdɾ", "DD"),
        ("kɡŋ", "kk"),
        ("ʃʒʧʤ", "CH"),
        ("sz", "SS"),
        ("nl", "nn"),
        ("ɹɚ", "RR"),
        ("ɑæʌɐa", "aa"),
        ("ɛəɜe", "E"),
        ("iɪᵻj", "I"),
        ("ɔoɒ", "O"),
        ("uʊw", "U"),
    ],
)
def test_english_ipa_mapping(symbols: str, viseme: str) -> None:
    for symbol in symbols:
        assert VISEME_OF[symbol] == viseme, symbol


def test_phone_classification() -> None:
    assert is_phone("a") and is_phone("ʃ") and is_phone("h")
    assert not any(is_phone(s) for s in 'ˈˌː ,.!?—…"“”()')
    assert has_phones("həlˈoʊ.") and not has_phones(" , … ")


# ------------------------------------------------------------------ merging


def test_stress_mark_moves_vowel_onset_earlier() -> None:
    cues = cues_without_audio(contiguous("tˈɛl"))
    assert as_pairs(cues) == [(0.0, "DD"), (0.05, "E"), (0.15, "nn"), (0.2, "sil")]


def test_length_mark_extends_previous_vowel() -> None:
    cues = cues_without_audio(contiguous("iːz"))
    assert as_pairs(cues) == [(0.0, "I"), (0.1, "SS"), (0.15, "sil")]


def test_word_gap_belongs_to_next_word() -> None:
    cues = cues_without_audio(contiguous("ɑn mɑ"))
    assert as_pairs(cues) == [(0.0, "aa"), (0.05, "nn"), (0.1, "PP"), (0.2, "aa"), (0.25, "sil")]


def test_affricate_merges_into_one_ch() -> None:
    cues = cues_without_audio(contiguous("ɑtʃɑ"))
    assert as_pairs(cues) == [(0.0, "aa"), (0.05, "CH"), (0.15, "aa"), (0.2, "sil")]


def test_h_takes_the_following_vowel_shape_weaker() -> None:
    cues = cues_without_audio(contiguous("hˈɑ"))
    assert [cue.viseme for cue in cues] == ["aa", "sil"]  # h and the vowel share the shape
    h_only = cues_without_audio(contiguous("hɑ"))
    assert h_only[0].viseme == "aa"


def test_diphthong_glide_is_weaker_than_onset() -> None:
    cues = cues_without_audio(contiguous("ɡɹˈeɪt"))
    by_viseme = {cue.viseme: cue.weight for cue in cues}
    assert by_viseme["I"] < by_viseme["E"]


def test_reduced_vowels_are_weaker_than_stressed() -> None:
    stressed = cues_without_audio(contiguous("mˈɑ"))[1]
    reduced = cues_without_audio(contiguous("mɐ"))[1]
    assert stressed.viseme == reduced.viseme == "aa"
    assert reduced.weight < stressed.weight


def test_pause_marks_close_the_mouth_and_repeats_merge() -> None:
    cues = cues_without_audio(contiguous("ɑpbm,ɑ"))
    assert as_pairs(cues) == [(0.0, "aa"), (0.05, "PP"), (0.2, "sil"), (0.25, "aa"), (0.3, "sil")]


def test_pieces_too_short_to_animate_are_folded() -> None:
    spans = spans_of([("ɑ", 0.0, 0.1), ("t", 0.1, 0.11), ("u", 0.11, 0.2)])
    assert as_pairs(cues_without_audio(spans)) == [(0.0, "aa"), (0.11, "U"), (0.2, "sil")]


def test_unknown_symbol_extends_previous_phone() -> None:
    spans = spans_of([("ɑ", 0.0, 0.1), ("☃", 0.1, 0.2), ("m", 0.2, 0.3)])
    assert as_pairs(cues_without_audio(spans)) == [(0.0, "aa"), (0.2, "PP"), (0.3, "sil")]


# ------------------------------------------------------------------ gating


def test_gating_with_silence_and_pause() -> None:
    total = 2.0
    levels = flat_levels(total)
    levels[: int(0.3 / FRAME_SECONDS)] = -120  # leading silence
    levels[int(0.8 / FRAME_SECONDS) : int(1.0 / FRAME_SECONDS)] = -90  # pause
    levels[int(1.6 / FRAME_SECONDS) :] = -120  # trailing silence
    # Model-style timings: first phone a little after the audible onset, the
    # phone before the comma stretched into the pause.
    spans = spans_of(
        [
            ("h", 0.33, 0.4),
            ("ˈ", 0.4, 0.45),
            ("ɛ", 0.45, 0.55),
            ("l", 0.55, 0.65),
            ("oʊ"[0], 0.65, 0.7),
            ("ʊ", 0.7, 0.95),
            (",", 0.95, 1.0),
            (" ", 1.0, 1.05),
            ("w", 1.05, 1.1),
            ("ɜ", 1.1, 1.3),
            ("l", 1.3, 1.4),
            ("d", 1.4, 1.75),
            (".", 1.75, 1.9),
        ]
    )
    cues = viseme_cues(spans, levels, 0.3, 1.6, [(0.8, 1.0)], total)
    pairs = as_pairs(cues)
    assert pairs[0] == (0.0, "sil")
    assert pairs[1] == (0.3, "E")  # pulled back to the audible onset; h takes the vowel shape
    assert (0.8, "sil") in pairs  # mouth closes for the pause
    assert (1.0, "U") in pairs  # speech resumes at the end of the pause (word gap carried into "w")
    assert pairs[-1] == (1.6, "sil")  # closed at the audible end even though "d" is longer
    times = [cue.time for cue in cues]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert all(a.viseme != b.viseme for a, b in zip(cues, cues[1:], strict=False))


def test_loud_vowels_open_wider_than_quiet_ones() -> None:
    total = 0.4
    levels = flat_levels(total, db=-3.0)
    levels[int(0.2 / FRAME_SECONDS) :] = -28.0
    cues = viseme_cues(contiguous("mˈɑmˈɑ", 0.0666), levels, 0.0, total, [], total)
    vowels = [cue.weight for cue in cues if cue.viseme == "aa"]
    assert len(vowels) == 2 and vowels[1] < vowels[0]


def test_no_spans_gives_closed_mouth() -> None:
    assert as_pairs(viseme_cues([], flat_levels(0.5), 0.0, 0.5, [], 0.5)) == [(0.0, "sil")]


# ------------------------------------------------------------------ estimation


def test_match_pauses_is_monotonic_and_tolerant() -> None:
    pairs = _match_pauses([1.0, 2.0], [(0.9, 1.1), (1.5, 1.6), (1.95, 2.1)], tolerance=0.3)
    assert pairs == [(0, 0), (1, 2)]
    assert _match_pauses([1.0], [(3.0, 3.2)], tolerance=0.3) == []
    assert _match_pauses([], [(1.0, 1.2)], tolerance=0.3) == []
    # Crossing matches are impossible: the later unit cannot take the earlier pause.
    assert _match_pauses([1.0, 1.1], [(1.05, 1.2)], tolerance=0.3) in ([(0, 0)], [(1, 0)])


def test_estimated_spans_cover_the_audible_region() -> None:
    phonemes = "tˈɛl mˌiː ɐbˌaʊt jɔːɹsˈɛlf."
    spans = estimate_spans(phonemes, 0.3, 2.0, [], 2.4)
    assert [s.symbol for s in spans] == list(phonemes)
    starts = [s.start for s in spans]
    assert starts == sorted(starts)
    phones = [s for s in spans if is_phone(s.symbol)]
    assert phones[0].start == pytest.approx(0.3)
    assert phones[-1].end == pytest.approx(2.0)
    assert (
        spans[-1].symbol == "."
        and spans[-1].start == pytest.approx(2.0)
        and spans[-1].end == pytest.approx(2.4)
    )
    for span in spans:
        assert span.end >= span.start


def test_estimated_durations_follow_the_calibrated_table() -> None:
    spans = {s.symbol: s for s in estimate_spans("ʒjʒ", 0.0, 1.0, [], 1.0) if s.symbol == "j"}
    j = spans["j"]
    assert (j.end - j.start) < 0.2  # j (30 ms) is much shorter than ʒ (80 ms, final one doubled)


def test_punctuation_is_anchored_to_detected_pause() -> None:
    phonemes = "həlˈoʊ, wˈɜːld."
    spans = estimate_spans(phonemes, 0.2, 1.6, [(0.7, 0.95)], 1.8)
    comma = next(s for s in spans if s.symbol == ",")
    w = next(s for s in spans if s.symbol == "w")
    assert comma.start == pytest.approx(0.7)
    assert w.start == pytest.approx(0.95)  # first phone after the pause starts when sound resumes
    assert all(s.end <= 0.7 + 1e-9 for s in spans[: phonemes.index(",")])


def test_far_away_pause_is_not_used_as_anchor() -> None:
    phonemes = "həlˈoʊ, wˈɜːld."
    # The only "pause" is a short gap right at the start: nowhere near the comma.
    spans = estimate_spans(phonemes, 0.2, 3.0, [(0.25, 0.36)], 3.2)
    comma = next(s for s in spans if s.symbol == ",")
    assert comma.start > 0.5


def test_estimate_without_phones() -> None:
    spans = estimate_spans("…", 0.1, 0.5, [], 0.6)
    assert [(s.symbol, s.start, s.end) for s in spans] == [("…", 0.1, 0.1)]
