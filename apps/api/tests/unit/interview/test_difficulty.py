from __future__ import annotations

import itertools

import pytest

from app.domain.enums import AnswerQuality, Difficulty
from app.schemas.rounds import DifficultyDistribution
from app.services.interview import difficulty as d

E, M, H = Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD
DIST = DifficultyDistribution(easy=0.3, medium=0.4, hard=0.3)


def test_levels_and_steps() -> None:
    assert [d.level(x) for x in (E, M, H)] == [0, 1, 2]
    assert d.step(E, +1) is M and d.step(M, +1) is H and d.step(H, +1) is H
    assert d.step(H, -1) is M and d.step(E, -1) is E
    assert d.from_level(-3) is E and d.from_level(9) is H


@pytest.mark.parametrize(
    "current,proposed,expected", [(E, H, M), (H, E, M), (M, H, H), (M, E, E), (E, E, E), (H, H, H)]
)
def test_clamp_step_never_jumps_two_levels(
    current: Difficulty, proposed: Difficulty, expected: Difficulty
) -> None:
    assert d.clamp_step(current, proposed) is expected


@pytest.mark.parametrize(
    "avg,allowed",
    [
        (None, (E, M, H)),
        (3.0, (E, M)),
        (4.99, (E, M)),
        (5.0, (E, M, H)),
        (6.9, (E, M, H)),
        (7.0, (M, H)),
        (9.5, (M, H)),
    ],
)
def test_allowed_by_average(avg: float | None, allowed: tuple[Difficulty, ...]) -> None:
    assert d.allowed_by_average(avg) == allowed


def test_deficits_follow_target_distribution() -> None:
    gaps = d.deficits(DIST, {"easy": 0, "medium": 0, "hard": 0})
    assert gaps[M] > gaps[E] == pytest.approx(gaps[H])
    gaps = d.deficits(DIST, {"easy": 3, "medium": 1, "hard": 0})
    assert d.largest_deficit(DIST, {"easy": 3, "medium": 1, "hard": 0}) is H
    assert gaps[E] < 0 < gaps[M] < gaps[H]
    assert d.largest_deficit(DIST, {"easy": 3, "medium": 1, "hard": 2}) is M
    assert d.largest_deficit(DIST, {"easy": 1, "medium": 3, "hard": 0}) is H
    assert d.largest_deficit(DIST, {"easy": 0, "medium": 2, "hard": 2}) is E


def test_starting_difficulty_nudges_toward_deficit_one_level() -> None:
    # Plenty of medium asked, no hard: a medium-start competency is nudged to hard.
    assert d.starting_difficulty(M, DIST, {"easy": 1, "medium": 4, "hard": 0}, None) is H
    # Easy start never jumps two levels even when hard is the biggest gap.
    assert d.starting_difficulty(E, DIST, {"easy": 2, "medium": 3, "hard": 0}, None) is M
    # The plan's level already has the largest deficit: no nudge.
    assert d.starting_difficulty(M, DIST, {"easy": 1, "medium": 1, "hard": 1}, None) is M
    assert d.starting_difficulty(M, DIST, {"easy": 0, "medium": 0, "hard": 0}, None) is M
    # Too many easy questions: an easy-start competency moves up.
    assert d.starting_difficulty(E, DIST, {"easy": 3, "medium": 0, "hard": 0}, None) is M


def test_starting_difficulty_capped_by_running_average() -> None:
    # Low average: never hard even if the distribution wants hard; the nudge goes to the most
    # under-represented level the average supports (easy here: medium is over-represented).
    assert d.starting_difficulty(M, DIST, {"easy": 1, "medium": 4, "hard": 0}, 4.0) is E
    assert d.starting_difficulty(H, DIST, {"easy": 0, "medium": 0, "hard": 0}, 4.0) is M
    # High average: never easy.
    assert d.starting_difficulty(E, DIST, {"easy": 0, "medium": 4, "hard": 4}, 8.0) is M


def test_strong_running_average_reaches_hard_questions() -> None:
    """Harness finding: an easy/hard deficit tie resolved to "easy", the high average clamped it back to
    medium, and a strong candidate never got a hard question. The deficit is now taken among the levels
    the average supports."""
    assert d.starting_difficulty(M, DIST, {"easy": 0, "medium": 2, "hard": 0}, 7.8) is H
    assert d.starting_difficulty(M, DIST, {"easy": 0, "medium": 2, "hard": 0}, 4.0) is E


def test_starting_difficulty_non_adaptive_keeps_plan() -> None:
    assert d.starting_difficulty(E, DIST, {"easy": 9, "medium": 0, "hard": 0}, 9.0, adaptive=False) is E


@pytest.mark.parametrize("start", [E, M, H])
def test_strong_answer_steps_up_once(start: Difficulty) -> None:
    result = d.adapt(start, AnswerQuality.STRONG, 1)
    assert result.difficulty is d.step(start, +1)
    assert result.weak_at_level == 0
    assert result.changed is (start is not H)


@pytest.mark.parametrize(
    "weak", [AnswerQuality.INCORRECT, AnswerQuality.NO_ANSWER, AnswerQuality.VAGUE, AnswerQuality.OFF_TOPIC]
)
def test_two_weak_answers_step_down(weak: AnswerQuality) -> None:
    first = d.adapt(H, weak, 0)
    assert first.difficulty is H and first.weak_at_level == 1 and not first.changed
    second = d.adapt(H, weak, first.weak_at_level)
    assert second.difficulty is M and second.weak_at_level == 0 and second.changed


@pytest.mark.parametrize("q", [AnswerQuality.ADEQUATE, AnswerQuality.PARTIAL])
def test_middling_answers_hold(q: AnswerQuality) -> None:
    result = d.adapt(M, q, 1)
    assert result.difficulty is M and result.weak_at_level == 1 and not result.changed


@pytest.mark.parametrize(
    "q",
    [
        None,
        AnswerQuality.CLARIFICATION_REQUEST,
        AnswerQuality.WANTS_TIME,
        AnswerQuality.CANDIDATE_QUESTION,
        AnswerQuality.WANTS_HINT,
    ],
)
def test_flow_turns_do_not_change_difficulty(q: AnswerQuality | None) -> None:
    assert d.adapt(M, q, 1) == d.Adaptation(M, 1, False, "no_change")


def test_strong_capped_by_average() -> None:
    result = d.adapt(M, AnswerQuality.STRONG, 0, running_average=4.0)
    assert result.difficulty is M and result.reason == "capped_by_average"


def test_non_adaptive_never_changes() -> None:
    assert d.adapt(M, AnswerQuality.STRONG, 0, adaptive=False).difficulty is M
    assert d.adapt(M, AnswerQuality.INCORRECT, 5, adaptive=False).difficulty is M


def test_adapt_never_jumps_two_levels_exhaustive() -> None:
    qualities = [None, *AnswerQuality]
    for start, q, weak in itertools.product((E, M, H), qualities, range(4)):
        result = d.adapt(start, q, weak)
        assert abs(d.level(result.difficulty) - d.level(start)) <= 1


def test_running_average() -> None:
    assert d.running_average([]) is None
    assert d.running_average([AnswerQuality.STRONG, AnswerQuality.PARTIAL]) == pytest.approx(7.0)
    assert d.running_average([AnswerQuality.WANTS_TIME]) is None  # flow turns carry no score
    # Evaluated competency scores dominate, weighted by confidence.
    assert d.running_average([AnswerQuality.STRONG], [(6.0, 0.5), (8.0, 0.5)]) == pytest.approx(7.0)


def test_easy_hard_tie_follows_the_candidates_running_average() -> None:
    """Harness finding: a strong candidate (average 6.8) was given easy questions on new competencies
    because an easy/hard deficit tie always resolved to "easy"."""
    counts = {"easy": 0, "medium": 3, "hard": 0}
    assert d.starting_difficulty(M, DIST, counts, 6.8) is H
    assert d.starting_difficulty(M, DIST, counts, 5.5) is E
    assert d.starting_difficulty(M, DIST, counts, None) is E
