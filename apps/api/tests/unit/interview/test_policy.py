from __future__ import annotations

import json

import pytest

from app.domain.enums import AnswerQuality, ClaimStatus, Difficulty, SegmentKind, SessionEndReason, TurnAction
from app.schemas.ai import AnswerAssessment
from app.services.interview.policy import (
    ANSWER_FLOW_ACTIONS,
    BankCandidate,
    check_decision,
    claim_to_verify,
    coding_due,
    compute_directive,
    count_questions,
    effective_quality,
    followup_limit,
    rank_targets,
    speech_issues,
    time_budget,
)
from app.services.interview.special import SpecialKind, SpecialResult, classify
from app.services.interview.state import ContradictionState, PendingFollowup

from .factories import T0, ask, claim, decision, plan, round_config, state, sufficient

A = TurnAction
Q = AnswerQuality


def quick(q: AnswerQuality, confidence: float = 0.9, **kw) -> AnswerAssessment:
    return AnswerAssessment(quality=q, confidence=confidence, **kw)


def directive(s, **kw):
    kw.setdefault("now", T0)
    kw.setdefault("closing_reserve_s", 75.0)
    return compute_directive(s, **kw)


# ----------------------------------------------------------------- closing


def test_must_close_inside_closing_reserve() -> None:
    s = state(elapsed_s=30 * 60 - 60)
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.must_close and d.close_reason is SessionEndReason.TIME_LIMIT
    assert d.recommended_action is A.CLOSE_INTERVIEW and d.required_action is A.CLOSE_INTERVIEW
    assert d.allowed_actions == [A.CLOSE_INTERVIEW]


def test_must_close_at_max_time_even_if_limit_extended() -> None:
    s = state(elapsed_s=41 * 60)
    s.time_limit_s = 60 * 60  # pathological: limit above max
    assert directive(s).must_close


def test_must_close_on_max_questions() -> None:
    s = state(rc=round_config(stopping={"max_questions": 3, "min_questions": 1}))
    for _ in range(3):
        ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.STRONG))
    assert d.must_close and d.close_reason is SessionEndReason.STOPPING_CONDITION


def test_clarifications_do_not_count_as_questions() -> None:
    s = state()
    ask(s, "kubernetes")
    ask(s, "kubernetes", action=A.CLARIFY_QUESTION)
    ask(s, "kubernetes", action=A.ASK_FOLLOWUP)
    assert count_questions(s) == 2


def test_closing_started_is_sticky() -> None:
    s = state(closing_started=True, end_reason=SessionEndReason.CANDIDATE_ENDED)
    d = directive(s)
    assert d.must_close and d.close_reason is SessionEndReason.CANDIDATE_ENDED


# ----------------------------------------------------------------- wrap-up


def _wrap_ready(**rc_overrides):
    s = state(rc=round_config(**rc_overrides), elapsed_s=12 * 60)
    for _ in range(5):
        ask(s, "kubernetes")
    sufficient(s, "kubernetes", "aws")
    return s


def test_sufficient_evidence_invites_candidate_questions_once() -> None:
    s = _wrap_ready()
    d = directive(s, quick=quick(Q.STRONG))
    assert d.should_wrap_up and d.invite_candidate_questions
    assert d.required_action is A.INVITE_CANDIDATE_QUESTIONS
    s.candidate_questions_invited = True
    d2 = directive(s, quick=quick(Q.ADEQUATE))
    assert d2.must_close and d2.close_reason is SessionEndReason.SUFFICIENT_EVIDENCE


def test_after_invitation_candidate_questions_are_answered_not_closed() -> None:
    s = _wrap_ready()
    s.candidate_questions_invited = True
    d = directive(s, special=classify("What does a typical day look like for the team?"))
    assert not d.must_close
    assert d.recommended_action is A.ANSWER_CANDIDATE_QUESTION
    assert d.allowed_actions == [A.ANSWER_CANDIDATE_QUESTION]


def test_candidate_question_during_invitation_turn() -> None:
    s = _wrap_ready()
    d = directive(s, special=classify("What are the next steps?"))
    assert d.recommended_action is A.ANSWER_CANDIDATE_QUESTION
    assert A.INVITE_CANDIDATE_QUESTIONS in d.allowed_actions


def test_without_candidate_questions_sufficiency_closes_directly() -> None:
    s = _wrap_ready(question_strategy={"allow_candidate_questions_at_end": False})
    d = directive(s, quick=quick(Q.STRONG))
    assert d.must_close and d.close_reason is SessionEndReason.SUFFICIENT_EVIDENCE


@pytest.mark.parametrize("elapsed_min,questions", [(5, 5), (12, 2)])
def test_wrap_up_requires_min_duration_and_questions(elapsed_min: int, questions: int) -> None:
    s = state(elapsed_s=elapsed_min * 60)
    for _ in range(questions):
        ask(s, "kubernetes")
    sufficient(s, "kubernetes", "aws")
    d = directive(s, quick=quick(Q.STRONG))
    assert not d.should_wrap_up and not d.must_close


def test_optional_competency_not_needed_for_wrap_up() -> None:
    s = _wrap_ready()
    assert not s.competencies["communication"].sufficient_evidence
    assert directive(s, quick=quick(Q.STRONG)).should_wrap_up


def test_stop_on_sufficient_evidence_disabled() -> None:
    s = _wrap_ready(stopping={"stop_on_sufficient_evidence": False, "min_questions": 1})
    assert not directive(s, quick=quick(Q.STRONG)).should_wrap_up


def test_near_end_window_invites_questions_without_sufficiency() -> None:
    s = state(elapsed_s=30 * 60 - 200)
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.should_wrap_up and d.invite_candidate_questions and not d.must_close


def test_too_little_time_to_invite_closes_instead_of_new_topic() -> None:
    s = state(elapsed_s=30 * 60 - 100)  # 100 s left: above the 75 s reserve, below reserve + 45 s
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.must_close and d.close_reason is SessionEndReason.TIME_LIMIT


def test_coding_outstanding_blocks_sufficiency_wrap_up() -> None:
    s = _wrap_ready(coding={"enabled": True, "duration_minutes": 10})
    d = directive(s, quick=quick(Q.STRONG))
    assert not d.invite_candidate_questions
    assert d.start_coding  # coding is what is left


# ----------------------------------------------------------------- coding


def coding_state(elapsed_min: float = 5, **coding):
    cfg = {"enabled": True, "duration_minutes": 10}
    cfg.update(coding)
    s = state(rc=round_config(coding=cfg), elapsed_s=elapsed_min * 60)
    ask(s, "kubernetes")
    return s


def test_coding_disabled_never_due() -> None:
    s = state()
    assert coding_due(s, elapsed_s=600, remaining_s=1200, closing_reserve_s=75) == (False, None)


def test_coding_not_due_without_time() -> None:
    s = coding_state(elapsed_min=19)  # 11 min left < 10 + reserve
    assert coding_due(s, elapsed_s=19 * 60, remaining_s=11 * 60, closing_reserve_s=75)[0] is False


def test_coding_last_window_is_urgent() -> None:
    s = coding_state(elapsed_min=17)  # 13 min left: 13*60 - 75 - 600 = 105 < 120
    due, reason = coding_due(s, elapsed_s=17 * 60, remaining_s=13 * 60, closing_reserve_s=75)
    assert due and reason == "last_window"


def test_coding_due_at_plan_point() -> None:
    s = coding_state(elapsed_min=12)
    s.plan = plan(
        [
            (SegmentKind.INTRO, 1, []),
            (SegmentKind.TECHNICAL, 10, ["kubernetes"]),
            (SegmentKind.CODING, 10, []),
            (SegmentKind.CLOSING, 9, []),
        ]
    )
    due, reason = coding_due(s, elapsed_s=12 * 60, remaining_s=18 * 60, closing_reserve_s=75)
    assert due and reason == "plan_point"


def test_coding_due_on_technical_evidence() -> None:
    s = coding_state(elapsed_min=4)
    s.competencies["kubernetes"].evidence_count = 2
    s.competencies["aws"].evidence_count = 1
    due, reason = coding_due(s, elapsed_s=240, remaining_s=26 * 60, closing_reserve_s=75)
    assert due and reason == "technical_evidence"  # 3 of 4 needed = 75 %


def test_coding_respects_start_after_and_force() -> None:
    s = coding_state(elapsed_min=4, start_after_minutes=8)
    s.competencies["kubernetes"].evidence_count = 5
    s.competencies["aws"].evidence_count = 5
    assert coding_due(s, elapsed_s=240, remaining_s=26 * 60, closing_reserve_s=75)[0] is False
    s.force_coding = True
    assert coding_due(s, elapsed_s=240, remaining_s=26 * 60, closing_reserve_s=75) == (True, "forced")


@pytest.mark.parametrize(
    "field,value", [("active", True), ("completed", True), ("unavailable", True), ("problems_done", 1)]
)
def test_coding_not_due_when_done_or_active(field: str, value) -> None:
    s = coding_state()
    s.force_coding = True
    setattr(s.coding, field, value)
    assert coding_due(s, elapsed_s=300, remaining_s=25 * 60, closing_reserve_s=75)[0] is False


def test_start_coding_waits_for_clarification_and_one_followup() -> None:
    s = coding_state(elapsed_min=4)
    s.competencies["kubernetes"].evidence_count = 2
    s.competencies["aws"].evidence_count = 2
    assert directive(s, special=classify("What do you mean?")).start_coding is False
    assert directive(s, quick=quick(Q.VAGUE)).start_coding is False
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.start_coding and d.recommended_action is A.START_CODING


# ----------------------------------------------------------------- first question


def test_first_question_verifies_top_claim() -> None:
    s = state(elapsed_s=30, claims={"c1": claim("c1", 5), "c2": claim("c2", 3)})
    d = directive(s, first_question=True, quick=quick(Q.STRONG))
    assert d.recommended_action is A.VERIFY_CLAIM
    assert d.claim_to_verify is not None and d.claim_to_verify.id == "c1"
    assert set(d.allowed_actions) == {A.ASK_QUESTION, A.VERIFY_CLAIM}
    assert d.answer_quality is None
    assert any("including the greeting, has already been spoken" in i for i in d.instructions)


def test_first_question_without_claims() -> None:
    s = state(elapsed_s=30)
    d = directive(s, first_question=True)
    assert d.recommended_action is A.ASK_QUESTION and d.target_competency == "kubernetes"


# ----------------------------------------------------------------- flow turns


@pytest.mark.parametrize(
    "text,action,required",
    [
        ("What do you mean by that?", A.CLARIFY_QUESTION, True),
        ("Let me think for a second.", A.ACKNOWLEDGE_AND_WAIT, True),
        ("Can I get a hint?", A.CLARIFY_QUESTION, True),
        ("What does a typical day look like for the team?", A.ANSWER_CANDIDATE_QUESTION, False),
    ],
)
def test_flow_turns(text: str, action: TurnAction, required: bool) -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, special=classify(text))
    assert d.recommended_action is action
    assert (d.required_action is action) is required
    assert d.stay_on_topic and d.target_competency == "kubernetes"
    assert d.quality_source == "special"


def test_hint_request_outside_coding_is_declined() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, special=classify("Can I get a hint?"))
    assert any("can't give hints" in i for i in d.instructions)


def test_backchannel_invites_elaboration() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, special=classify("Yeah."))
    assert d.recommended_action is A.ASK_FOLLOWUP and d.answer_quality is None
    assert any("elaborate" in i for i in d.instructions)


# ----------------------------------------------------------------- contradictions


def test_medium_contradiction_is_verified() -> None:
    s = state()
    ask(s, "kubernetes")
    s.contradictions.append(
        ContradictionState(id="x1", description="Said 3 nodes vs resume 40 nodes", severity="medium")
    )
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.recommended_action is A.VERIFY_CONTRADICTION
    assert d.open_contradiction is not None and d.open_contradiction.id == "x1"


def test_low_or_probed_contradictions_are_not_verified() -> None:
    s = state()
    ask(s, "kubernetes")
    s.contradictions.append(ContradictionState(id="x1", description="minor", severity="low"))
    s.contradictions.append(
        ContradictionState(id="x2", description="probed", severity="high", status="verifying")
    )
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.recommended_action is not A.VERIFY_CONTRADICTION
    assert d.open_contradiction is None
    assert A.VERIFY_CONTRADICTION in d.allowed_actions  # the model may still raise the open low one


# ----------------------------------------------------------------- answer quality rules


@pytest.mark.parametrize(
    "q,expected",
    [
        (Q.VAGUE, A.REQUEST_SPECIFICITY),
        (Q.PARTIAL, A.ASK_FOLLOWUP),
        (Q.ADEQUATE, A.ASK_FOLLOWUP),
        (Q.STRONG, A.INCREASE_DIFFICULTY),
        (Q.INCORRECT, A.SIMPLIFY),
        (Q.NO_ANSWER, A.SIMPLIFY),
        (Q.OFF_TOPIC, A.REDIRECT),
    ],
)
def test_quality_rules_on_fresh_topic(q: AnswerQuality, expected: TurnAction) -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, quick=quick(q))
    assert d.recommended_action is expected
    assert d.stay_on_topic and d.target_competency == "kubernetes"
    assert d.answer_quality is q and d.quality_source == "quick"


@pytest.mark.parametrize("q", [Q.VAGUE, Q.PARTIAL, Q.ADEQUATE, Q.STRONG])
def test_max_followups_moves_on(q: AnswerQuality) -> None:
    s = state()
    ask(s, "kubernetes", followups=3)  # depth 3 reached, competency uncertain
    d = directive(s, quick=quick(q))
    assert d.max_followups_reached
    assert d.recommended_action is A.CHANGE_TOPIC
    assert d.target_competency == "aws"
    assert A.ASK_FOLLOWUP not in d.allowed_actions and A.REQUEST_SPECIFICITY not in d.allowed_actions


def test_followup_limit_shrinks_when_confident() -> None:
    s = state()
    ask(s, "kubernetes", followups=2)
    assert followup_limit(s, s.competencies["kubernetes"]) == 3
    s.competencies["kubernetes"].confidence = 0.9
    assert followup_limit(s, s.competencies["kubernetes"]) == 2
    assert directive(s, quick=quick(Q.PARTIAL)).max_followups_reached


def test_strong_on_sufficient_competency_changes_topic() -> None:
    s = state()
    ask(s, "kubernetes")
    sufficient(s, "kubernetes")
    d = directive(s, quick=quick(Q.STRONG))
    assert d.recommended_action is A.CHANGE_TOPIC and d.target_competency == "aws"
    assert "kubernetes" in d.blocked_competencies
    assert any("sufficient evidence" in i for i in d.instructions)


def test_strong_at_hard_moves_on_while_required_areas_are_untouched() -> None:
    """Harness finding: strong answers at the top level kept producing follow-ups on an
    already clear competency while a required one (CI/CD, AWS) was never asked."""
    s = state()
    ask(s, "kubernetes", difficulty=Difficulty.HARD)
    d = directive(s, quick=quick(Q.STRONG))
    assert d.recommended_action is A.CHANGE_TOPIC and d.target_competency == "aws"


def test_strong_at_hard_asks_followup_when_nothing_else_is_uncovered_and_difficulty_rises() -> None:
    s = state()
    ask(s, "aws")
    ask(s, "kubernetes", difficulty=Difficulty.HARD)
    d = directive(s, quick=quick(Q.STRONG))
    assert d.recommended_action is A.ASK_FOLLOWUP and d.target_difficulty is Difficulty.HARD
    s2 = state()
    ask(s2, "kubernetes", difficulty=Difficulty.MEDIUM)
    assert directive(s2, quick=quick(Q.STRONG)).target_difficulty is Difficulty.HARD


def test_incorrect_two_attempts_then_move_on() -> None:
    s = state()
    ask(s, "kubernetes")
    s.simplify_attempts = 1
    assert directive(s, quick=quick(Q.INCORRECT)).recommended_action is A.SIMPLIFY
    s.simplify_attempts = 2
    assert directive(s, quick=quick(Q.INCORRECT)).recommended_action is A.CHANGE_TOPIC


def test_no_answer_simplify_once_then_move_on() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, special=classify("I don't know."))
    assert d.recommended_action is A.SIMPLIFY and d.target_difficulty is Difficulty.EASY
    assert d.required_action is A.SIMPLIFY  # deterministic heuristic -> required
    s.simplify_attempts = 1
    d2 = directive(s, special=classify("I don't know."))
    assert d2.recommended_action is A.CHANGE_TOPIC and d2.required_action is A.CHANGE_TOPIC
    assert any("fine not to know" in i for i in d2.instructions)
    # From a quick assessment the same quality stays a recommendation.
    assert directive(s, quick=quick(Q.NO_ANSWER)).required_action is None


def test_simplify_steps_difficulty_down() -> None:
    s = state()
    ask(s, "kubernetes", difficulty=Difficulty.HARD)
    assert directive(s, quick=quick(Q.INCORRECT)).target_difficulty is Difficulty.MEDIUM


def test_unknown_quality_gives_rules_to_the_model() -> None:
    """The move rules for an unassessed answer are sent once, in the static system prompt (they used to be
    repeated in every turn's instructions as well)."""
    from app.services.interview.context import system_prompt

    s = state()
    ask(s, "kubernetes")
    d = directive(s)
    assert d.answer_quality is None and d.quality_source is None
    assert d.recommended_action is A.ASK_FOLLOWUP
    assert not any("vague -> request_specificity" in i for i in d.instructions)
    assert system_prompt(s).count("vague -> request_specificity") == 1
    assert set(ANSWER_FLOW_ACTIONS) <= set(d.allowed_actions)


def test_low_confidence_or_backchannel_quick_is_ignored() -> None:
    assert effective_quality(quick(Q.STRONG, confidence=0.4), None) == (None, None)
    assert effective_quality(
        AnswerAssessment(quality=Q.STRONG, confidence=0.9, is_backchannel=True), None
    ) == (None, None)
    assert effective_quality(quick(Q.STRONG), SpecialResult(SpecialKind.DONT_KNOW, 0.9)) == (
        Q.NO_ANSWER,
        "special",
    )


def test_pending_followup_from_evaluation() -> None:
    s = state()
    ask(s, "kubernetes")
    s.pending_followups.append(
        PendingFollowup(
            text="Ask how the HPA metrics were chosen", reason="gap", competency_key="kubernetes", priority=4
        )
    )
    d = directive(s)
    assert d.recommended_action is A.ASK_FOLLOWUP
    assert d.pending_followup == "Ask how the HPA metrics were chosen"
    assert any("HPA metrics" in i for i in d.instructions)


def test_missing_specifics_from_quick_assessment() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.VAGUE, missing_specifics=["node count", "time range"]))
    assert d.missing_specifics == ["node count", "time range"]
    assert any("node count" in i for i in d.instructions)


# ----------------------------------------------------------------- claims


def test_change_topic_becomes_claim_verification() -> None:
    s = state(claims={"c1": claim("c1", 5, keys=["aws"])})
    ask(s, "kubernetes", followups=3)
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.recommended_action is A.VERIFY_CLAIM
    assert d.target_competency == "aws"


def test_claim_rules() -> None:
    s = state(claims={"c1": claim("c1", 5), "c2": claim("c2", 2), "c3": claim("c3", 4)})
    first = claim_to_verify(s, elapsed_s=60)
    assert first is not None and first.id == "c1"
    s.claims["c1"].attempts = 1
    s.claims["c1"].status = ClaimStatus.IN_PROGRESS
    assert claim_to_verify(s, elapsed_s=60).id == "c3"  # c2 below min_priority 3
    s.claims["c3"].attempts = 1
    assert claim_to_verify(s, elapsed_s=60) is None  # max_claims 2 reached


def test_claim_plan_order_and_window() -> None:
    s = state(claims={"c1": claim("c1", 5), "c3": claim("c3", 4, keys=["aws"])})
    s.plan = plan(
        [
            (SegmentKind.INTRO, 1, []),
            (SegmentKind.RESUME_VALIDATION, 5, ["kubernetes"]),
            (SegmentKind.TECHNICAL, 22, ["aws"]),
            (SegmentKind.CLOSING, 2, []),
        ],
        claims=["c3", "c1"],
    )
    assert claim_to_verify(s, elapsed_s=60).id == "c3"
    # After the resume-validation window only claims matching the target remain.
    assert claim_to_verify(s, elapsed_s=15 * 60) is None
    assert claim_to_verify(s, elapsed_s=15 * 60, target_key="kubernetes").id == "c1"


def test_resume_validation_disabled() -> None:
    s = state(rc=round_config(resume_validation={"enabled": False}), claims={"c1": claim("c1", 5)})
    assert claim_to_verify(s, elapsed_s=60) is None
    assert A.VERIFY_CLAIM not in directive(s, first_question=True).allowed_actions


# ----------------------------------------------------------------- targets / time


def test_target_ranking_formula() -> None:
    s = state()
    targets = rank_targets(s, segment_key=None)
    assert [t.key for t in targets] == ["kubernetes", "aws", "communication"]
    k = targets[0]
    assert k.priority == pytest.approx(1.5)  # weight x required x (1 - 0)
    assert targets[2].priority == pytest.approx(0.25)  # optional halves
    s.competencies["kubernetes"].confidence = 0.5
    sufficient(s, "aws", confidence=0.8)
    targets = {t.key: t for t in rank_targets(s, segment_key=None)}
    assert targets["kubernetes"].priority == pytest.approx(0.75)
    assert targets["aws"].priority == pytest.approx(1.2 * 0.2 * 0.15)


def test_segment_boost_and_coding_deprioritised() -> None:
    rc = round_config(
        competencies=[
            {"key": "kubernetes", "weight": 1.0},
            {"key": "ownership", "weight": 1.0},
            {"key": "data_structures", "weight": 1.0},
        ],
        coding={"enabled": True, "duration_minutes": 10},
    )
    s = state(rc=rc)
    s.plan = plan(
        [
            (SegmentKind.INTRO, 1, []),
            (SegmentKind.BEHAVIORAL, 10, ["ownership"]),
            (SegmentKind.TECHNICAL, 19, ["kubernetes"]),
        ]
    )
    order = [t.key for t in rank_targets(s, segment_key="behavioral")]
    assert order[0] == "ownership" and order[-1] == "data_structures"


def test_time_budget_segments_and_overrun() -> None:
    s = state(elapsed_s=9 * 60)
    s.plan = plan(
        [
            (SegmentKind.INTRO, 1, []),
            (SegmentKind.TECHNICAL, 5, ["kubernetes"]),
            (SegmentKind.BEHAVIORAL, 4, []),
            (SegmentKind.CLOSING, 5, []),
        ]
    )
    tb = time_budget(s, now=T0, closing_reserve_s=75)
    # Budgets scale from 15 planned minutes to the 30-minute limit: technical spans 2-12 min.
    assert tb.segment_key == "technical" and tb.segment_budget_s == pytest.approx(600)
    assert not tb.segment_overrun and tb.next_segment_key == "behavioral"
    s2 = state(elapsed_s=9 * 60)
    s2.plan = plan(
        [
            (SegmentKind.INTRO, 1, []),
            (SegmentKind.TECHNICAL, 5, ["kubernetes"]),
            (SegmentKind.BEHAVIORAL, 24, []),
        ]
    )
    s2.time_limit_s = 30 * 60
    tb2 = time_budget(s2, now=T0, closing_reserve_s=75)
    assert tb2.segment_key == "behavioral"


def test_segment_overrun_forces_topic_change() -> None:
    segments = [
        (SegmentKind.INTRO, 1, []),
        (SegmentKind.TECHNICAL, 5, ["kubernetes"]),
        (SegmentKind.BEHAVIORAL, 24, ["aws"]),
    ]
    # Still on kubernetes at minute 8: the technical window (1-6 min) overran.
    s = state(elapsed_s=8 * 60, plan=plan(segments))
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert d.time.segment_key == "behavioral" and d.time.segment_overrun
    assert d.recommended_action is A.CHANGE_TOPIC and d.target_competency == "aws"
    assert any("over its time budget" in i for i in d.instructions)
    # Inside the window the follow-up stands.
    s2 = state(elapsed_s=4 * 60, plan=plan(segments))
    ask(s2, "kubernetes")
    d2 = directive(s2, quick=quick(Q.ADEQUATE))
    assert not d2.time.segment_overrun and d2.recommended_action is A.ASK_FOLLOWUP


def test_time_notice_uses_actual_remaining_minutes() -> None:
    s = state(elapsed_s=30 * 60 - 130)
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.ADEQUATE))
    assert any("about 2 minutes left" in i for i in d.instructions)
    s2 = state(elapsed_s=30 * 60 - 290)
    ask(s2, "kubernetes")
    assert any("about 5 minutes left" in i for i in directive(s2, quick=quick(Q.ADEQUATE)).instructions)


def test_time_warnings() -> None:
    s = state(elapsed_s=25.5 * 60)
    assert time_budget(s, now=T0, closing_reserve_s=75).warning == "5_minutes"
    s.time_warnings.append(300)
    assert time_budget(s, now=T0, closing_reserve_s=75).warning is None
    s2 = state(elapsed_s=29.2 * 60)
    assert time_budget(s2, now=T0, closing_reserve_s=75).warning == "1_minute"


def test_bank_candidates_filtered_to_target() -> None:
    s = state()
    ask(s, "kubernetes")
    bank = [
        BankCandidate(
            id="b1",
            text="How does the HPA decide?",
            competency_key="kubernetes",
            difficulty=Difficulty.MEDIUM,
        ),
        BankCandidate(id="b2", text="Explain IAM roles", competency_key="aws", difficulty=Difficulty.MEDIUM),
    ]
    d = directive(s, quick=quick(Q.PARTIAL), bank_candidates=bank)
    assert [b.id for b in d.candidate_bank_questions] == ["b1"]


def test_summary_is_json_serialisable() -> None:
    s = state(claims={"c1": claim("c1", 5)})
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.VAGUE))
    json.dumps(d.summary())
    json.dumps(d.to_dict(), default=str)


# ----------------------------------------------------------------- post-check


def test_check_decision_accepts_compliant_decision() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.PARTIAL))
    check = check_decision(d, decision(action="ask_followup", answer_quality="partial"), s)
    assert check.deviations == []
    assert check.decision.action is A.ASK_FOLLOWUP


def test_check_decision_flags_ignored_required_action() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, special=classify("What do you mean?"))
    check = check_decision(d, decision(action="ask_followup"), s)
    assert any(x.startswith("required_action_ignored:clarify_question") for x in check.deviations)
    assert check.decision.action is A.ASK_FOLLOWUP  # state reflects what was said


def test_check_decision_replaces_impossible_actions() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s, quick=quick(Q.PARTIAL))
    for impossible in ("close_interview", "start_coding", "give_hint", "introduce"):
        check = check_decision(d, decision(action=impossible), s)
        assert check.decision.action is A.ASK_FOLLOWUP, impossible
        assert any(x.startswith("action_not_allowed") for x in check.deviations)


def test_check_decision_corrections() -> None:
    s = state(claims={"c1": claim("c1", 5)})
    ask(s, "kubernetes", difficulty=Difficulty.EASY)
    d = directive(s, quick=quick(Q.STRONG))
    check = check_decision(
        d,
        decision(
            action="increase_difficulty",
            competency_key="nonexistent",
            difficulty="hard",
            claim_id="zz",
            bank_question_id="b9",
        ),
        s,
        valid_bank_ids=["b1"],
    )
    c = check.decision
    assert c.competency_key == d.target_competency == "kubernetes"
    assert c.difficulty is Difficulty.MEDIUM  # never two levels
    assert c.claim_id is None and c.bank_question_id is None
    assert {"unknown_competency:nonexistent", "unknown_claim_id", "unknown_bank_question_id"} <= set(
        check.deviations
    )
    assert any(x.startswith("difficulty_jump") for x in check.deviations)


def test_verify_claim_gets_directive_claim_id() -> None:
    s = state(claims={"c1": claim("c1", 5)})
    d = directive(s, first_question=True)
    check = check_decision(d, decision(action="verify_claim", claim_id=None, answer_quality=None), s)
    assert check.decision.claim_id == "c1"


def test_quality_rule_post_check_when_model_assessed() -> None:
    s = state()
    ask(s, "kubernetes")
    d = directive(s)  # no pre-assessment
    bad = check_decision(d, decision(action="change_topic", answer_quality="vague", competency_key="aws"), s)
    assert "quality_rule:vague->change_topic" in bad.deviations
    good = check_decision(d, decision(action="request_specificity", answer_quality="vague"), s)
    assert not any(x.startswith("quality_rule") for x in good.deviations)


@pytest.mark.parametrize(
    "speech,action,issue",
    [
        ("Thanks for that.", A.ASK_QUESTION, "no_question"),
        ("What did you do? And why?", A.ASK_QUESTION, "multiple_questions"),
        (" ".join(["word"] * 70) + "?", A.ASK_QUESTION, "too_long"),
        ("Great answer! What next?", A.ASK_FOLLOWUP, "evaluative_language"),
        ("**Bold** question?", A.ASK_QUESTION, "markdown"),
    ],
)
def test_speech_issues(speech: str, action: TurnAction, issue: str) -> None:
    assert issue in speech_issues(speech, action)


def test_speech_issues_allow_statements_for_flow_moves() -> None:
    assert speech_issues("Of course, take your time.", A.ACKNOWLEDGE_AND_WAIT) == []
    assert speech_issues("Thank you, Jane. The hiring team will follow up.", A.CLOSE_INTERVIEW) == []
