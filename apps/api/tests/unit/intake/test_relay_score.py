"""Deterministic Drill A / Drill B scores (INTAKE_INTERVIEW.md 8.5)."""

from __future__ import annotations

import uuid

import pytest

from app.services.intake.evidence import ActivityRecord
from app.services.intake.kit import load_intake_kit
from app.services.intake.reading import ConceptCoverage, RelayCoverage, relay_coverage
from app.services.intake.relay import (
    ConceptEvidence,
    capture_problem,
    drill_a_score,
    reading_metrics,
    relay_score,
)
from app.services.intake.scoring import technical_reading_score


def _coverage(covered: list[bool], *, verbatim: float = 0.0, words: int = 60) -> RelayCoverage:
    concepts = tuple(
        ConceptCoverage(key=f"c{i}", text=f"concept {i}", covered=c, matched_cue="cue" if c else None)
        for i, c in enumerate(covered)
    )
    return RelayCoverage(
        passage="C",
        concepts=concepts,
        covered=sum(covered),
        total=len(covered),
        coverage=sum(covered) / len(covered),
        verbatim_ratio=verbatim,
        relay_words=words,
    )


def _model(statuses: list[tuple[str, bool]]) -> list[ConceptEvidence]:
    return [ConceptEvidence(key=f"c{i}", status=s, quote_valid=v) for i, (s, v) in enumerate(statuses)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("covered", "expected"),
    [(5, 5), (4, 5), (3, 4), (2, 3), (1, 2), (0, 1)],  # of 5 concepts: r = 1.0 .8 .6 .4 .2 0
)
def test_thresholds_on_validated_concepts(covered: int, expected: int) -> None:
    statuses = [("covered", True)] * covered + [("missing", False)] * (5 - covered)
    result = relay_score(_coverage([False] * 5), concepts=_model(statuses), paraphrase="own_words")
    assert result.score == expected


def test_partial_and_unverified_concepts_earn_half_credit_only_with_evidence() -> None:
    # 2 covered (1.0 each) + 1 partial with quote (0.5) + 1 covered without quote but cue-confirmed (0.5)
    # + 1 covered without quote and without cue (0) = 3.0 / 5 = 0.6 -> 4
    coverage = _coverage([False, False, False, True, False])
    model = _model(
        [("covered", True), ("covered", True), ("partial", True), ("covered", False), ("covered", False)]
    )
    result = relay_score(coverage, concepts=model, paraphrase="own_words")
    assert result.coverage == pytest.approx(0.6)
    assert result.score == 4
    credits = [c["credit"] for c in result.concepts]
    assert credits == [1.0, 1.0, 0.5, 0.5, 0.0]
    assert result.concepts[3]["source"] == "model_cue"


def test_model_says_missing_beats_lenient_cues() -> None:
    result = relay_score(
        _coverage([True] * 5), concepts=_model([("missing", False)] * 5), paraphrase="own_words"
    )
    assert result.score == 1


def test_cue_only_when_model_unavailable() -> None:
    result = relay_score(_coverage([True, True, True, False, False]), concepts=None)
    assert result.score == 4 and "cue_only" in result.basis


def test_recited_relay_is_capped_at_4() -> None:
    full = _model([("covered", True)] * 5)
    assert relay_score(_coverage([True] * 5), concepts=full, paraphrase="recited").score == 4
    assert relay_score(_coverage([True] * 5, verbatim=0.55), concepts=full, paraphrase="own_words").score == 4
    assert relay_score(_coverage([True] * 5, verbatim=0.49), concepts=full, paraphrase="own_words").score == 5


def test_misstatements_cost_one_each_at_most_two_never_below_one() -> None:
    full = _model([("covered", True)] * 5)
    assert relay_score(_coverage([True] * 5), concepts=full, misstatements=1).score == 4
    assert relay_score(_coverage([True] * 5), concepts=full, misstatements=5).score == 3
    weak = _model([("covered", True)] + [("missing", False)] * 4)
    assert relay_score(_coverage([False] * 5), concepts=weak, misstatements=3).score == 1


def test_silence_scores_one_and_no_capture_is_insufficient() -> None:
    silent = relay_score(_coverage([False] * 5, words=0), concepts=_model([("missing", False)] * 5))
    assert silent.score == 1 and any("anchor 1" in b for b in silent.basis)
    none = relay_score(None, concepts=None, captured=False, problem="interrupted_by_disconnect")
    assert none.score is None and none.status == "insufficient" and none.reason == "interrupted_by_disconnect"


def test_real_passage_coverage_feeds_the_score() -> None:
    kit = load_intake_kit()
    passage = kit.passage("C")
    transcript = (
        "Overfitting is when the model memorizes the training data too well, including the noise, so it fails on "
        "new data. Like a student who memorizes last year's exam. You can use more data or a simpler model."
    )
    coverage = relay_coverage(passage, transcript)
    result = relay_score(coverage, concepts=None)
    assert coverage.total == len(passage.relay_concepts)
    assert result.score is not None and result.score >= 3


# ------------------------------------------------------------------------------------------------ Drill A


def _activity(**overrides) -> ActivityRecord:  # noqa: ANN003
    kit = load_intake_kit()
    passage = kit.passage("B")
    values = {
        "id": uuid.uuid4(),
        "kind": "reading_aloud",
        "stage_key": "reading",
        "status": "completed",
        "material_key": "B",
        "capture": {"end_reason": "finish_button", "voiced_ms": 40000, "segments": [], "pauses": []},
        "transcript_final": passage.text,
        "transcript_status": "final",
        "transcript_source": "final_pass_live",
    }
    values.update(overrides)
    return ActivityRecord(**values)


def test_drill_a_uses_the_delivery_score() -> None:
    kit = load_intake_kit()
    activity = _activity()
    metrics = reading_metrics(activity, kit.passage("B"))
    result = drill_a_score(activity, metrics)
    assert result.score == metrics.delivery_score == 5
    assert any("pronunciation not assessed" in b for b in result.basis)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"capture": {}}, "not_captured"),
        ({"capture": {}, "attempts_log": [{"end_reason": "disconnect"}], "status": "interrupted"}, "interrupted_by_disconnect"),
        ({"capture": {"end_reason": "disconnect", "voiced_ms": 9000}}, "interrupted_by_disconnect"),
        (
            {"transcript_final": None, "transcript_status": "unavailable",
             "capture": {"end_reason": "deadline", "voiced_ms": 30000, "stt_degraded": True}},
            "transcript_unavailable",
        ),
        ({"status": "skipped", "capture": {}}, "skipped"),
    ],
)  # fmt: skip
def test_drill_a_is_insufficient_only_for_technical_reasons(overrides, reason) -> None:  # noqa: ANN001
    activity = _activity(**overrides)
    assert capture_problem(activity) == reason
    assert drill_a_score(activity, None).score is None


def test_drill_a_candidate_who_does_not_read_scores_one_with_flag() -> None:
    kit = load_intake_kit()
    activity = _activity(
        transcript_final=None,
        transcript_status="unavailable",
        capture={"end_reason": "deadline", "voiced_ms": 0, "segments": [], "pauses": []},
    )
    assert capture_problem(activity) is None
    metrics = reading_metrics(activity, kit.passage("B"))
    result = drill_a_score(activity, metrics)
    assert result.score == 1 and "reading_not_completed" in result.flags


def test_drill_a_live_segments_are_labelled() -> None:
    kit = load_intake_kit()
    activity = _activity(
        transcript_final=None, transcript_live=kit.passage("B").text, transcript_status="live_only"
    )
    result = drill_a_score(activity, reading_metrics(activity, kit.passage("B")))
    assert any("live recognition segments" in b for b in result.basis)


def test_technical_reading_combines_both_drills() -> None:
    assert str(technical_reading_score(5, 4)) == "4.4"
    assert technical_reading_score(None, 4) is None
