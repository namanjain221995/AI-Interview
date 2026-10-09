"""When the coding exercise opens: combined-round start window (talk first,
editor by ~40 %), plan fitting, coding_due guards, the transition wording, and
closings that never promise a report unless the candidate gets one."""

from __future__ import annotations

import pytest

from app.domain.enums import AnswerQuality, CandidateReportMode, RoundType, SegmentKind, SessionEndReason
from app.schemas.ai import AnswerAssessment
from app.services.interview.closing import acceptable_closing, closing_prompt, template_closing
from app.services.interview.coding_bridge import transition_speech
from app.services.interview.planner import fallback_plan
from app.services.interview.policy import (
    coding_due,
    coding_plan_start_s,
    coding_start_window,
    compute_directive,
    plan_segments,
)
from app.services.interview.special import classify

from .factories import T0, ask, plan, round_config, state

Q = AnswerQuality
T = 30 * 60.0
RESERVE = 75.0

COMBINED_NOTES = (
    "Suggested flow: brief introduction (1 min), resume claim validation and technical questions (about 12 min), "
    "a short easy coding exercise (about 10 min), one or two behavioural questions (about 5 min) and the "
    "candidate's questions."
)


def combined_rc(**coding):
    cfg = {"enabled": True, "duration_minutes": 10, "difficulty": "easy", "max_problems": 1}
    cfg.update(coding)
    return round_config(
        round_type=RoundType.COMBINED,
        name="Combined AI Round",
        competencies=[
            {"key": "problem_solving", "name": "Problem Solving", "weight": 1.2},
            {"key": "system_design", "name": "System Design", "weight": 0.8},
            {"key": "algorithms", "name": "Algorithms", "weight": 1.0},
            {"key": "data_structures", "name": "Data Structures", "weight": 0.8},
            {"key": "ownership", "name": "Ownership", "weight": 0.8},
        ],
        coding=cfg,
        interviewer_notes=COMBINED_NOTES,
    )


def combined_state(elapsed_s: float, *, with_plan=None, **coding):
    s = state(rc=combined_rc(**coding), elapsed_s=elapsed_s, plan=with_plan)
    ask(s, "problem_solving")
    return s


LATE_PLAN = plan(
    [
        (SegmentKind.INTRO, 1, []),
        (SegmentKind.RESUME_VALIDATION, 5, []),
        (SegmentKind.TECHNICAL, 9, ["problem_solving", "system_design"]),
        (SegmentKind.BEHAVIORAL, 6, ["ownership"]),
        (SegmentKind.CODING, 8, ["algorithms", "data_structures"]),
        (SegmentKind.CLOSING, 1, []),
    ]
)
EARLY_PLAN = plan(
    [
        (SegmentKind.INTRO, 1, []),
        (SegmentKind.CODING, 10, ["algorithms"]),
        (SegmentKind.TECHNICAL, 16, ["problem_solving", "system_design"]),
        (SegmentKind.CLOSING, 3, []),
    ]
)


def due(s, elapsed_s: float):
    return coding_due(s, elapsed_s=elapsed_s, remaining_s=T - elapsed_s, closing_reserve_s=RESERVE)


def directive(s, **kw):
    kw.setdefault("now", T0)
    kw.setdefault("closing_reserve_s", RESERVE)
    return compute_directive(s, **kw)


# ----------------------------------------------------------------- window


def test_combined_window_defaults_and_explicit_start_after() -> None:
    assert coding_start_window(combined_state(0)) == (0.2 * T, 0.4 * T)
    assert coding_start_window(combined_state(0, start_after_minutes=3)) == (180.0, 0.4 * T)
    # An explicit later start wins over the deadline.
    assert coding_start_window(combined_state(0, start_after_minutes=15)) == (900.0, 900.0)


def test_other_rounds_keep_the_plan_and_have_no_deadline() -> None:
    rc = round_config(coding={"enabled": True, "duration_minutes": 10, "start_after_minutes": 2})
    s = state(rc=rc, plan=LATE_PLAN)
    assert coding_start_window(s) == (120.0, None)
    assert coding_plan_start_s(s) == 21 * 60  # untouched: coding last as planned


# ----------------------------------------------------------------- plan fitting


def test_late_model_plan_is_pulled_to_the_deadline_keeping_order_and_total() -> None:
    s = combined_state(0, with_plan=LATE_PLAN)
    segs = plan_segments(s)
    assert [k for k, *_ in segs] == [
        "intro",
        "resume_validation",
        "technical",
        "behavioral",
        "coding",
        "closing",
    ]
    assert coding_plan_start_s(s) == pytest.approx(0.4 * T)
    assert segs[-1][2] + segs[-1][3] == pytest.approx(T)
    coding = segs[4]
    assert coding[3] == pytest.approx(8 * 60)  # the exercise keeps its budget
    # The time taken from the earlier segments goes to the segments after coding.
    assert segs[5][3] > 60


def test_early_plan_waits_for_the_earliest_start() -> None:
    s = combined_state(0, with_plan=EARLY_PLAN)
    assert coding_plan_start_s(s) == pytest.approx(0.2 * T)
    segs = plan_segments(s)
    assert segs[-1][2] + segs[-1][3] == pytest.approx(T)


def test_fallback_plan_of_the_combined_template_starts_coding_in_window() -> None:
    s = combined_state(0)
    s.plan = fallback_plan(s)
    start = coding_plan_start_s(s)
    assert 0.2 * T <= start <= 0.4 * T


# ----------------------------------------------------------------- coding_due


def test_combined_never_before_the_earliest_start() -> None:
    s = combined_state(240, with_plan=EARLY_PLAN)
    for key in ("problem_solving", "system_design"):
        s.competencies[key].evidence_count = 3  # technical evidence complete
    assert due(s, 240) == (False, None)
    assert due(s, 0.2 * T) == (True, "plan_point")


def test_combined_deadline_without_plan_or_evidence() -> None:
    s = combined_state(0)  # no plan at all, no evidence
    assert due(s, 0.4 * T - 1) == (False, None)
    assert due(s, 0.4 * T) == (True, "round_point")


def test_combined_late_plan_starts_by_the_deadline() -> None:
    s = combined_state(0, with_plan=LATE_PLAN)
    assert due(s, 0.4 * T)[0] is True


def test_combined_guards_still_apply() -> None:
    s = combined_state(0)
    # Not enough time left for the exercise plus the closing reserve.
    assert coding_due(s, elapsed_s=0.4 * T, remaining_s=10 * 60, closing_reserve_s=RESERVE) == (False, None)
    for field, value in (("unavailable", True), ("completed", True), ("active", True), ("problems_done", 1)):
        s = combined_state(0)
        setattr(s.coding, field, value)
        assert due(s, 0.4 * T) == (False, None), field


def test_deadline_start_is_not_deferred_for_a_followup_but_waits_for_a_clarification() -> None:
    at_plan_point = combined_state(0.25 * T, with_plan=EARLY_PLAN)
    d = directive(at_plan_point, quick=AnswerAssessment(quality=Q.VAGUE, confidence=0.9))
    assert d.coding_due and d.coding_reason == "plan_point" and d.start_coding is False  # one follow-up first

    at_deadline = combined_state(0.4 * T)
    d = directive(at_deadline, quick=AnswerAssessment(quality=Q.VAGUE, confidence=0.9))
    assert d.start_coding and d.coding_reason == "round_point"
    assert directive(combined_state(0.4 * T), special=classify("What do you mean?")).start_coding is False


# ----------------------------------------------------------------- spoken text


def test_transition_announces_the_editor_opening() -> None:
    s = combined_state(0.4 * T)
    text = transition_speech(s, title="Most Frequent Error Service", minutes=10, ack="Okay, thank you.")
    assert text.startswith("Okay, thank you. Let's move to a short coding exercise; the editor is opening")
    assert "Most Frequent Error Service" in text and "about 10 minutes" in text and "hints" in text


@pytest.mark.parametrize("mode", [CandidateReportMode.NEVER, CandidateReportMode.AFTER_REVIEW, None])
def test_closings_never_promise_a_report_unless_immediate(mode) -> None:
    s = state(candidate_report_mode=mode.value if mode else None)
    for reason in (SessionEndReason.TIME_LIMIT, SessionEndReason.SUFFICIENT_EVIDENCE):
        text = template_closing(s, reason).lower()
        assert "feedback" not in text and "summary" not in text and "portal" not in text
    assert "Do not promise feedback" in closing_prompt(s, SessionEndReason.TIME_LIMIT, None)
    promise = "Thank you, Jane. The hiring team will follow up, and you'll get a feedback report soon."
    assert not acceptable_closing(promise)
    assert acceptable_closing(
        "Thank you, Jane. The hiring team will review our conversation and follow up soon."
    )


def test_immediate_mode_may_mention_the_summary() -> None:
    s = state(candidate_report_mode=CandidateReportMode.IMMEDIATE.value)
    assert "feedback summary in the portal" in template_closing(s, SessionEndReason.TIME_LIMIT)
    assert "Do not promise feedback" not in closing_prompt(s, SessionEndReason.TIME_LIMIT, None)
    assert acceptable_closing(
        "Thank you, Jane. The hiring team will follow up, and a short feedback summary will be in the portal.",
        report_allowed=True,
    )
