"""Drill A reading engine: normalisation, alignment, timing, delivery score and relay coverage."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.services.intake.kit import PASSAGE_KEYS, IntakeKit, Passage, load_intake_kit
from app.services.intake.reading import (
    ReadingMetrics,
    align_reading,
    canonical_spelling,
    delivery_score,
    normalize_tokens,
    phonetic_key,
    relay_concepts,
    relay_coverage,
    tokenize,
)


@pytest.fixture(scope="module")
def kit() -> IntakeKit:
    return load_intake_kit()


def passage(kit: IntakeKit, key: str) -> Passage:
    return kit.passage(key)


def sentences_of(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.replace("\n\n", " ")) if s]


def assert_consistent(m: ReadingMetrics) -> None:
    """Invariants every result satisfies."""
    errors = m.substitutions + m.omissions + m.insertions + m.repetitions
    assert m.wer == round(errors / m.reference_tokens, 4)
    assert m.matched + m.asr_variants + m.substitutions + m.omissions == m.reference_tokens
    assert m.pronunciation == "not_assessed"
    assert 1 <= m.delivery_score <= 5
    assert any("pronunciation not assessed" in line for line in m.delivery_basis)
    assert m.sentences_read + m.sentences_partial + m.sentences_skipped == len(m.sentences)
    assert sum(s.tokens for s in m.sentences) == m.reference_tokens


# ========================================================================================= normalisation


class TestNormalisation:
    @pytest.mark.parametrize(
        ("text", "tokens"),
        [
            ("Don't stop", ["do", "not", "stop"]),
            ("can't", ["can", "not"]),
            ("cannot", ["can", "not"]),
            ("won't", ["will", "not"]),
            ("It's here", ["it", "is", "here"]),
            ("I'd go", ["i", "would", "go"]),
            ("they've we'll you're I'm", ["they", "have", "we", "will", "you", "are", "i", "am"]),
            ("Let's", ["let", "us"]),
            ("the model's predictions", ["the", "model", "predictions"]),
            ("last year's exam", ["last", "year", "exam"]),
            ("each other's work", ["each", "other", "work"]),
            ("Optimise and optimize", ["optimize", "and", "optimize"]),
            ("regularisation penalises memorises", ["regularization", "penalizes", "memorizes"]),
            ("behaviour colour favourite", ["behavior", "color", "favorite"]),
            ("centre theatre defences", ["center", "theater", "defenses"]),
            ("modelling travelled", ["modeling", "traveled"]),
            ("catalogue analyse learnt", ["catalog", "analyze", "learned"]),
            (
                "self-attention Multi-head subject-verb",
                ["self", "attention", "multi", "head", "subject", "verb"],
            ),
            ("Hello, world! (Yes.)", ["hello", "world", "yes"]),
            ("“Smart” quotes ‘here’ don’t", ["smart", "quotes", "here", "do", "not"]),
            ("2", ["two"]),
            ("21", ["twenty", "one"]),
            ("100", ["one", "hundred"]),
            ("1999", ["one", "thousand", "nine", "hundred", "ninety", "nine"]),
            ("1,000", ["one", "thousand"]),
            ("50,000 rows", ["fifty", "thousand", "rows"]),
            ("15%", ["fifteen", "percent"]),
            ("99 %", ["ninety", "nine", "percent"]),
            ("3rd 21st 12th", ["third", "twenty", "first", "twelfth"]),
            ("3.5", ["three", "point", "five"]),
            ("8–10 min", ["eight", "to", "ten", "min"]),
            ("R&D", ["r", "and", "d"]),
        ],
    )
    def test_tokens(self, text: str, tokens: list[str]) -> None:
        assert normalize_tokens(text) == tokens

    def test_american_spellings_are_fixed_points(self) -> None:
        for word in ("optimize", "color", "behavior", "center", "modeling", "regularization", "analyze"):
            assert canonical_spelling(word) == word
            assert canonical_spelling(canonical_spelling(word)) == word

    def test_ordinary_words_untouched(self) -> None:
        for word in ("four", "hour", "your", "our", "this", "analysis", "basis", "rise"):
            assert canonical_spelling(word) == word

    def test_offsets_and_surfaces(self) -> None:
        text = "The model’s API, self-attention and 1,000 epochs."
        tokens, _ = tokenize(text)
        for token in tokens:
            assert text[token.start : token.end] == token.surface.replace("'", "’")
        assert [t.surface for t in tokens][:3] == ["The", "model's", "API"]

    def test_ambiguous_contractions_accept_both_expansions(self) -> None:
        tokens, _ = tokenize("it's he'd")
        assert tokens[1].norm == "is" and tokens[1].alts == frozenset({"has"})
        assert tokens[3].norm == "would" and tokens[3].alts == frozenset({"had"})

    def test_fillers(self) -> None:
        tokens, fillers = tokenize("um so uh this erm works", drop_fillers=True)
        assert [t.norm for t in tokens] == ["so", "this", "works"]
        assert fillers == 3
        assert len(tokenize("um so", drop_fillers=False)[0]) == 2


class TestPhonetic:
    @pytest.mark.parametrize(
        ("a", "b"), [("carpenter", "karpenter"), ("epochs", "epics"), ("git", "get"), ("phase", "faze")]
    )
    def test_same_key(self, a: str, b: str) -> None:
        assert phonetic_key(a) == phonetic_key(b)

    @pytest.mark.parametrize(
        ("a", "b"), [("minimize", "maximize"), ("neuron", "neutron"), ("epochs", "ethics")]
    )
    def test_different_key(self, a: str, b: str) -> None:
        assert phonetic_key(a) != phonetic_key(b)

    def test_empty(self) -> None:
        assert phonetic_key("123") == ""


# ============================================================================================= alignment


class TestPerfectAndEdgeReads:
    @pytest.mark.parametrize("key", PASSAGE_KEYS)
    def test_perfect_read(self, kit: IntakeKit, key: str) -> None:
        p = passage(kit, key)
        m = align_reading(p, p.text)
        assert_consistent(m)
        assert (m.wer, m.strict_wer, m.completeness, m.token_coverage) == (0.0, 0.0, 1.0, 1.0)
        assert m.matched == m.reference_tokens
        assert m.events == ()
        assert m.sentences_read == len(m.sentences) == len(sentences_of(p.text))
        assert m.delivery_score == 5
        assert m.review_flags == ()

    def test_reference_forms_agree(self, kit: IntakeKit) -> None:
        p = passage(kit, "F")
        hypothesis = p.text.lower()
        as_passage = align_reading(p, hypothesis)
        as_paragraphs = align_reading(list(p.paragraphs), hypothesis)
        as_text = align_reading(p.text, hypothesis)
        assert as_passage.wer == as_paragraphs.wer == as_text.wer == 0.0
        assert as_paragraphs.sentences == as_text.sentences

    def test_punctuation_only_differences(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        stripped = re.sub(r"[^\w\s']", " ", p.text).lower()
        m = align_reading(p, stripped)
        assert (m.wer, m.completeness, m.delivery_score) == (0.0, 1.0, 5)
        noisy = p.text.replace(",", "...").replace(".", "!?")
        assert align_reading(p, noisy).wer == 0.0

    @pytest.mark.parametrize("hypothesis", ["", "   \n ", None])
    def test_empty_hypothesis(self, kit: IntakeKit, hypothesis: str | None) -> None:
        p = passage(kit, "B")
        m = align_reading(p, hypothesis)
        assert_consistent(m)
        assert m.omissions == m.reference_tokens
        assert (m.wer, m.strict_wer, m.completeness, m.token_coverage) == (1.0, 1.0, 0.0, 0.0)
        assert m.delivery_score == 1
        assert m.delivery_basis[0] == "no reading captured -> 1"
        assert m.review_flags == ("reading_not_completed",)
        assert m.sentences_skipped == len(m.sentences)
        assert m.hypothesis_words == 0 and m.words_per_minute is None
        assert len(m.omitted_positions) == m.reference_tokens

    def test_fillers_only_counts_as_empty(self, kit: IntakeKit) -> None:
        m = align_reading(passage(kit, "A"), "um uh erm")
        assert m.fillers == 3 and m.omissions == m.reference_tokens and m.delivery_score == 1

    def test_hypothesis_from_segment_texts(self, kit: IntakeKit) -> None:
        p = passage(kit, "C")
        segments = [{"start_ms": 0, "end_ms": 9000, "text": p.paragraphs[0]}, {"start_ms": 9300, "end_ms": 25000,
                    "text": " ".join(p.paragraphs[1:])}]  # fmt: skip
        m = align_reading(p, None, segments=segments)
        assert (m.wer, m.completeness) == (0.0, 1.0)
        assert m.duration_seconds == 25.0

    def test_invalid_reference(self) -> None:
        with pytest.raises(ValueError, match="no words"):
            align_reading("  ...  ", "anything")
        with pytest.raises(TypeError):
            align_reading(42, "anything")  # type: ignore[arg-type]

    def test_wrong_passage_read(self, kit: IntakeKit) -> None:
        m = align_reading(passage(kit, "F"), passage(kit, "D").text)
        assert_consistent(m)
        assert m.completeness < 0.2 and m.delivery_score == 1
        assert m.review_flags == ("reading_not_completed",)


class TestOmissionsAndInsertions:
    def test_skipped_sentence(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        skipped = "Most modern web and mobile applications rely on APIs to fetch data."
        hypothesis = p.text.replace(skipped + " ", "")
        assert hypothesis != p.text
        m = align_reading(p, hypothesis)
        assert_consistent(m)
        statuses = [s.status for s in m.sentences]
        assert statuses == ["read", "read", "read", "skipped", "read"]
        sentence = m.sentences[3]
        assert sentence.text == skipped
        assert m.omissions == sentence.tokens == sentence.omitted == 12
        assert (m.substitutions, m.insertions, m.repetitions) == (0, 0, 0)
        assert m.completeness < 0.9 and m.wer == round(12 / m.reference_tokens, 4)
        assert all(e.sentence == 3 and e.kind == "omission" for e in m.events)
        text = p.text
        for event in m.events:  # char offsets point at the omitted words in the passage
            assert text[event.ref_start : event.ref_end] == event.ref_word
        assert m.delivery_score == 3

    def test_stopped_halfway(self, kit: IntakeKit) -> None:
        p = passage(kit, "D")
        words = p.text.split()
        m = align_reading(p, " ".join(words[: len(words) // 2]))
        assert_consistent(m)
        assert 0.4 <= m.completeness < 0.6
        assert m.sentences[0].status == "read" and m.sentences[-1].status == "skipped"
        assert m.delivery_score == 2

    def test_most_of_the_passage_missing_is_flagged(self, kit: IntakeKit) -> None:
        p = passage(kit, "E")
        m = align_reading(p, p.paragraphs[-1])
        assert m.completeness < 0.5
        assert m.review_flags == ("reading_not_completed",)
        assert m.delivery_score == 1

    def test_off_script_insertions(self, kit: IntakeKit) -> None:
        p = passage(kit, "F")
        m = align_reading(p, "Okay so " + p.text + " That's it.")
        assert_consistent(m)
        assert m.insertions == 5 and m.repetitions == 0
        assert m.completeness == 1.0 and m.omissions == 0
        assert m.inserted_positions == [
            0,
            1,
            m.hypothesis_tokens - 3,
            m.hypothesis_tokens - 2,
            m.hypothesis_tokens - 1,
        ]
        assert m.delivery_score == 5  # 5 extra words out of ~100: still a clean read

    def test_fillers_are_not_errors(self, kit: IntakeKit) -> None:
        p = passage(kit, "C")
        hypothesis = p.text.replace("A useful analogy", "Um, a useful, uh, analogy")
        m = align_reading(p, hypothesis)
        assert m.fillers == 2 and m.wer == 0.0 and m.delivery_score == 5

    def test_common_word_substitution(self, kit: IntakeKit) -> None:
        p = passage(kit, "B")
        m = align_reading(p, p.text.replace("steepest", "deepest"))
        assert_consistent(m)
        assert m.substitutions == 1 and m.asr_variants == 0
        assert m.substituted_positions == [m.events[0].ref_index]
        assert (m.events[0].ref_word, m.events[0].hyp_word) == ("steepest", "deepest")
        assert m.sentences[3].substituted == 1 and m.sentences[3].status == "read"


class TestRepetitions:
    def test_repeated_phrase(self, kit: IntakeKit) -> None:
        p = passage(kit, "B")
        phrase = "Imagine standing on a foggy hill"
        m = align_reading(p, p.text.replace(phrase, phrase + ", standing on a foggy hill"))
        assert_consistent(m)
        assert m.repetitions == 5 and m.insertions == 0
        assert m.completeness == 1.0 and m.omissions == 0
        assert len(m.repeated_positions) == 5
        assert m.wer == round(5 / m.reference_tokens, 4)
        assert m.delivery_score == 5

    def test_repeated_word_and_false_start(self, kit: IntakeKit) -> None:
        p = passage(kit, "E")
        hypothesis = p.text.replace("the importance", "the the importance").replace(
            "dramatically", "drama- dramatically"
        )
        m = align_reading(p, hypothesis)
        assert m.repetitions == 2 and m.insertions == 0
        assert {e.hyp_word for e in m.events if e.kind == "repetition"} == {"the", "drama"}

    def test_reading_a_sentence_twice(self, kit: IntakeKit) -> None:
        p = passage(kit, "F")
        sentence = (
            "If two people edit the same line, Git flags a merge conflict that must be resolved manually."
        )
        m = align_reading(p, p.text.replace(sentence, sentence + " " + sentence))
        assert m.repetitions == len(normalize_tokens(sentence))
        assert m.insertions == 0 and m.completeness == 1.0


class TestReordering:
    def test_reordered_sentences(self, kit: IntakeKit) -> None:
        p = passage(kit, "B")
        first, second = sentences_of(p.paragraphs[0])
        hypothesis = p.text.replace(p.paragraphs[0], f"{second} {first}")
        m = align_reading(p, hypothesis)
        assert_consistent(m)
        assert m.omissions == 0 and m.insertions == 0 and m.substitutions == 0
        assert m.reordered == len(normalize_tokens(first))
        assert (m.wer, m.completeness) == (0.0, 1.0)
        assert m.strict_wer > 0.1  # plain WER counts the move as errors
        assert [s.reordered for s in m.sentences][:2] == [True, False]
        assert {e.kind for e in m.events} == {"reordered"}
        assert m.delivery_score == 4  # complete, but not read as written

    def test_reordered_paragraphs(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        m = align_reading(p, f"{p.paragraphs[1]} {p.paragraphs[0]}")
        assert_consistent(m)
        assert m.completeness == 1.0 and m.reordered > 0
        assert m.omissions == 0 and m.insertions == 0

    def test_reordered_sentence_with_extra_word_and_repetition_elsewhere(self, kit: IntakeKit) -> None:
        p = passage(kit, "B")
        first, second = sentences_of(p.paragraphs[0])
        moved = first.replace("used to train", "used basically to train")
        hypothesis = p.text.replace(p.paragraphs[0], f"{second} {moved}").replace(
            "the slope of the error", "the slope of the the error"
        )
        m = align_reading(p, hypothesis)
        assert_consistent(m)
        assert (m.reordered, m.insertions, m.repetitions, m.omissions) == (
            len(normalize_tokens(first)),
            1,
            1,
            0,
        )
        assert [(e.kind, e.hyp_word) for e in m.events if e.kind in ("insertion", "repetition")] == [
            ("insertion", "basically"),
            ("repetition", "the"),
        ]

    def test_short_coincidental_runs_are_not_reordering(self) -> None:
        m = align_reading("alpha beta gamma delta", "beta gamma delta alpha")
        assert m.reordered == 0  # a single moved word stays an omission plus an insertion
        assert (m.omissions, m.insertions) == (1, 1)


class TestAsrTolerance:
    def test_carpenter_style_term_errors(self) -> None:
        reference = "We autoscale nodes with Karpenter and scrape metrics with Prometheus."
        m = align_reading(reference, "We autoscale nodes with Carpenter and scrape metrics with Ometheus.")
        assert_consistent(m)
        assert m.asr_variants == 2 and m.substitutions == 0
        assert (m.wer, m.completeness, m.delivery_score) == (0.0, 1.0, 5)
        assert m.strict_wer == pytest.approx(2 / m.reference_tokens, abs=1e-4)
        assert [(e.ref_word, e.hyp_word) for e in m.events] == [
            ("Karpenter", "Carpenter"),
            ("Prometheus", "Ometheus"),
        ]

    def test_passage_terms_get_extra_tolerance(self, kit: IntakeKit) -> None:
        p = passage(kit, "D")
        hypothesis = p.text.replace("epochs", "epics")
        with_terms = align_reading(p, hypothesis)
        assert with_terms.asr_variants == 1 and with_terms.substitutions == 0
        without_terms = align_reading(p, hypothesis, terms=[])
        assert without_terms.asr_variants == 0 and without_terms.substitutions == 1

    def test_split_and_joined_compounds(self, kit: IntakeKit) -> None:
        d, e, f = passage(kit, "D"), passage(kit, "E"), passage(kit, "F")
        m = align_reading(d, d.text.replace("backpropagation", "back propagation").replace(
            "Backpropagation", "Back-propagation").replace("nonlinear", "non-linear"))  # fmt: skip
        assert (m.wer, m.asr_variants, m.completeness) == (0.0, 0, 1.0)
        assert m.strict_wer > 0
        m = align_reading(e, e.text.replace("Multi-head", "Multihead").replace("GPT", "G.P.T."))
        assert (m.wer, m.completeness) == (0.0, 1.0)
        m = align_reading(f, f.text.replace("codebase", "code base"))
        assert (m.wer, m.completeness) == (0.0, 1.0)

    def test_misrecognised_split_term(self, kit: IntakeKit) -> None:
        p = passage(kit, "D")
        m = align_reading(p, p.text.replace("backpropagation", "back propogation"))
        assert m.asr_variants == 1 and m.substitutions == 0 and m.wer == 0.0

    def test_short_term_near_miss(self, kit: IntakeKit) -> None:
        p = passage(kit, "F")
        m = align_reading(p, p.text.replace("Git,", "get,"))
        assert m.asr_variants == 1 and m.wer == 0.0

    def test_accent_never_lowers_the_score(self, kit: IntakeKit) -> None:
        """Recognition near-misses of the right words (typical when an accent confuses the recogniser)
        give the same delivery score as a clean transcript."""
        p = passage(kit, "E")
        accented = (
            p.text.replace("recurrent", "recurring").replace("positional", "positionall")
            .replace("architecture", "architecturee").replace("Transformer", "Transformar")
        )  # fmt: skip
        clean = align_reading(p, p.text)
        noisy = align_reading(p, accented)
        assert noisy.asr_variants == 5 and noisy.substitutions == 0  # "architecture" occurs twice
        assert noisy.delivery_score == clean.delivery_score == 5
        assert noisy.completeness == 1.0

    def test_contractions_both_ways(self, kit: IntakeKit) -> None:
        a, b = passage(kit, "A"), passage(kit, "B")
        assert align_reading(a, a.text.replace("don't", "do not")).wer == 0.0
        assert align_reading(b, b.text.replace("can't", "cannot")).wer == 0.0
        assert (
            align_reading("You do not walk. It has been done.", "You don't walk. It's been done.").wer == 0.0
        )
        assert align_reading("The model learns its weights.", "the model learns it's weights").wer == 0.0

    def test_possessives_and_spelling_variants(self, kit: IntakeKit) -> None:
        b, c = passage(kit, "B"), passage(kit, "C")
        assert align_reading(b, b.text.replace("model's", "models")).wer == 0.0
        american = (
            c.text.replace("memorises", "memorizes").replace("defences", "defenses")
            .replace("regularisation", "regularization").replace("penalises", "penalizes")
        )  # fmt: skip
        m = align_reading(c, american)
        assert (m.wer, m.asr_variants, m.matched) == (0.0, 0, m.reference_tokens)

    def test_numbers(self) -> None:
        m = align_reading(
            "It took 15% of 1,000 runs, 3rd attempt.",
            "it took fifteen percent of one thousand runs third attempt",
        )
        assert (m.wer, m.completeness) == (0.0, 1.0)


# =============================================================================================== timing


class TestTiming:
    def test_segments_in_milliseconds(self, kit: IntakeKit) -> None:
        p = passage(kit, "D")
        segments = [
            {"start_ms": 1000, "end_ms": 10000},
            {"start_ms": 10600, "end_ms": 21000},  # 0.6 s pause
            {"start_ms": 24000, "end_ms": 41000},  # 3.0 s long pause
        ]
        m = align_reading(p, p.text, segments=segments)
        assert m.duration_seconds == 40.0
        assert m.words_per_minute == round(m.hypothesis_words / (40 / 60), 4)
        assert m.correct_words_per_minute == round(m.reference_tokens / (40 / 60), 4)
        assert (m.pause_source, m.pause_count, m.long_pause_count) == ("segments", 2, 1)
        assert m.longest_pause_seconds == 3.0 and m.total_pause_seconds == 3.6
        assert m.delivery_score == 5  # one long pause is allowed

    def test_segment_shapes(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        shapes = [
            [{"start": 0.0, "end": 20.0}, {"start": 20.2, "end": 40.0}],
            [(0.0, 20.0), (20.2, 40.0, "text ignored when a hypothesis is given")],
            [SimpleNamespace(start_ms=0, end_ms=20000), SimpleNamespace(start_ms=20200, end_ms=40000)],
        ]
        for segments in shapes:
            m = align_reading(p, p.text, segments=segments)
            assert m.duration_seconds == 40.0
            assert (m.pause_count, m.long_pause_count) == (0, 0)  # 0.2 s gap is below the pause threshold

    def test_vad_pauses_preferred(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        pauses = [
            0.3,
            0.7,
            2.5,
            (10.0, 12.5),
            {"duration_ms": 3000},
            SimpleNamespace(start_ms=0, end_ms=600),
            None,
        ]
        m = align_reading(p, p.text, segments=[(0, 40.0)], pauses=pauses)
        assert m.pause_source == "vad"
        assert (m.pause_count, m.long_pause_count) == (5, 3)
        assert m.delivery_score == 4  # 2 long pauses beyond the allowance -> -1

    def test_duration_override_and_invalid_duration(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        assert align_reading(p, p.text, duration_seconds=30).duration_seconds == 30.0
        m = align_reading(p, p.text, duration_seconds=0)
        assert m.duration_seconds is None and m.words_per_minute is None

    def test_slow_and_fast_pace(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")  # ~99 words
        slow = align_reading(p, p.text, duration_seconds=90)  # ~66 wpm
        assert slow.words_per_minute is not None and slow.words_per_minute < 80
        assert slow.delivery_score == 4
        fast = align_reading(p, p.text, duration_seconds=25)  # ~238 wpm
        assert fast.words_per_minute is not None and fast.words_per_minute > 220
        assert fast.delivery_score == 4
        natural = align_reading(p, p.text, duration_seconds=40)
        assert natural.delivery_score == 5

    def test_deductions_capped_and_floored(self, kit: IntakeKit) -> None:
        p = passage(kit, "A")
        m = align_reading(p, p.text, duration_seconds=120, pauses=[3.0] * 6)
        assert m.delivery_score == 3  # base 5, slow -1, 5 extra long pauses -2, capped at -2
        bad = align_reading(p, " ".join(p.text.split()[:30]), duration_seconds=120, pauses=[3.0] * 6)
        assert bad.delivery_score == 1

    def test_no_timing(self, kit: IntakeKit) -> None:
        m = align_reading(passage(kit, "A"), passage(kit, "A").text)
        assert m.pause_source == "none"
        assert (m.duration_seconds, m.words_per_minute, m.pause_count, m.long_pause_count) == (
            None,
            None,
            None,
            None,
        )
        assert "pace not measured (no timing)" in m.delivery_basis

    def test_single_segment(self, kit: IntakeKit) -> None:
        m = align_reading(
            passage(kit, "A"), passage(kit, "A").text, segments=[{"start_ms": 0, "end_ms": 40000}]
        )
        assert (m.pause_source, m.pause_count, m.long_pause_count) == ("segments", 0, 0)


class TestDeliveryScore:
    @pytest.mark.parametrize(
        ("completeness", "wer", "base"),
        [
            (1.0, 0.0, 5), (0.95, 0.10, 5), (0.9499, 0.0, 4), (0.95, 0.1001, 4), (0.85, 0.20, 4),
            (0.8499, 0.0, 3), (0.70, 0.35, 3), (0.70, 0.3501, 2), (0.40, 0.95, 2), (0.3999, 0.0, 1), (0.0, 1.0, 1),
        ],
    )  # fmt: skip
    def test_base_thresholds(self, completeness: float, wer: float, base: int) -> None:
        score, basis = delivery_score(
            completeness=completeness, wer=wer, words_per_minute=None, long_pause_count=None
        )
        assert score == base
        assert basis[0].endswith(f"base {base}")

    @pytest.mark.parametrize(
        ("wpm", "long_pauses", "score"),
        [
            (150, 0, 5), (79.9, 0, 4), (80, 0, 5), (220, 0, 5), (220.1, 0, 4),
            (150, 1, 5), (150, 2, 4), (150, 3, 4), (150, 4, 3), (60, 4, 3), (60, 9, 3),
        ],
    )  # fmt: skip
    def test_timing_deductions(self, wpm: float, long_pauses: int, score: int) -> None:
        assert (
            delivery_score(completeness=1.0, wer=0.0, words_per_minute=wpm, long_pause_count=long_pauses)[0]
            == score
        )

    def test_reordered_cap_and_floor(self) -> None:
        assert (
            delivery_score(
                completeness=1.0, wer=0.0, words_per_minute=None, long_pause_count=None, reordered=3
            )[0]
            == 4
        )
        assert delivery_score(completeness=0.1, wer=0.9, words_per_minute=50, long_pause_count=8)[0] == 1


# ================================================================================================= relay


class TestRelay:
    @pytest.mark.parametrize("key", PASSAGE_KEYS)
    def test_concepts(self, kit: IntakeKit, key: str) -> None:
        concepts = relay_concepts(passage(kit, key))
        assert 5 <= len(concepts) <= 8
        assert relay_concepts(key) == concepts  # by key, from the default kit
        assert relay_concepts(key, kit=kit) == concepts

    @pytest.mark.parametrize("key", PASSAGE_KEYS)
    def test_the_passage_itself_covers_every_concept(self, kit: IntakeKit, key: str) -> None:
        coverage = relay_coverage(passage(kit, key), passage(kit, key).text)
        assert coverage.coverage == 1.0, [c.key for c in coverage.concepts if not c.covered]
        assert coverage.verbatim_ratio == 1.0

    def test_paraphrase(self, kit: IntakeKit) -> None:
        relay = (
            "So gradient descent is basically an algorithm for training models. It tries to bring the loss down, "
            "the loss being how wrong the predictions are, kind of like walking down a hill in fog. "
            "The step size is the learning rate, and if it's too big you overshoot."
        )
        coverage = relay_coverage("B", relay, kit=kit)
        covered = {c.key for c in coverage.concepts if c.covered}
        assert covered == {"what", "goal", "cost_meaning", "analogy", "learning_rate", "tradeoff"}
        assert coverage.covered == 6 and coverage.total == 7
        assert coverage.verbatim_ratio < 0.2  # paraphrased, not parroted

    def test_parroting_is_visible(self, kit: IntakeKit) -> None:
        p = passage(kit, "C")
        parrot = relay_coverage(p, p.paragraphs[0])
        assert parrot.verbatim_ratio == 1.0 and parrot.coverage < 1.0

    def test_empty_relay(self, kit: IntakeKit) -> None:
        coverage = relay_coverage("A", "", kit=kit)
        assert (coverage.covered, coverage.coverage, coverage.verbatim_ratio, coverage.relay_words) == (
            0,
            0.0,
            0.0,
            0,
        )

    def test_prefix_cues(self, kit: IntakeKit) -> None:
        coverage = relay_coverage("C", "You can use regularization, which penalizes complexity.", kit=kit)
        assert {c.key for c in coverage.concepts if c.covered} == {"regularisation"}
        coverage = relay_coverage("F", "It keeps a record of changes and records changes over time.", kit=kit)
        assert any(c.key == "definition" and c.covered for c in coverage.concepts)
