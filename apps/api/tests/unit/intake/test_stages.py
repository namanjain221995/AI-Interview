"""Deterministic intake stage machine (INTAKE_INTERVIEW.md 6.1-6.4): budgets, plan, next_move rules,
compression, time-up, follow-up / clarification budgets, special turns, hedges, C3 gating."""

from __future__ import annotations

import pytest

from app.services.intake import stages as S
from app.services.intake.state import IntakeState

CLAIMS2 = [{"ref": "C1", "probe": "tool_or_framework"}, {"ref": "C2", "probe": "project_or_product"}]
CLAIMS3 = [*CLAIMS2, {"ref": "C3", "probe": "metric_improvement"}]


def make_state(
    *,
    minutes: int = 45,
    claims: list[dict] | None = None,
    target_role: str | None = "ai_ml",
    wa: bool = False,
    limits: dict | None = None,
    digest: dict | None = None,
) -> IntakeState:
    time_limit = minutes * 60.0
    state = IntakeState(
        intake_id="i1",
        kit_version="v1",
        kit_sha256="x" * 64,
        time_limit_s=time_limit,
        limits=limits or {},
        target_role=target_role,
        onboarding_digest=digest or {},
    )
    state.stages = S.build_stages(
        time_limit, claims=claims if claims is not None else CLAIMS2, target_role=target_role,
        work_authorization_enabled=wa,
    )  # fmt: skip
    return state


def answer(words: int = 60, **kw) -> S.AnswerSignal:
    return S.AnswerSignal(kind=kw.pop("kind", "none"), words=words, **kw)


def apply(state: IntakeState, move: S.Move, now: float, *, choose: str = "ask_item") -> None:
    """Apply a move exactly like the conductor does (no I/O)."""
    if move.action == "ask":
        S.apply_resolution(state, move.resolve, now)
        if move.model_choice:
            if choose == "follow_up":
                S.apply_followup(state, move, now)
            else:
                S.apply_ask(state, move, now)
        elif move.offered in (("follow_up",), ("answer_question",)):
            S.apply_followup(state, move, now)
        else:
            S.apply_ask(state, move, now)
    elif move.action in ("clarify", "dont_know_probe"):
        S.apply_clarification(state, move.item_key or "")
    elif move.action == "encourage":
        item = state.current()
        assert item is not None
        item.encouraged = True
    elif move.action == "time_up":
        S.apply_resolution(state, move.resolve, now)
        S.apply_time_up(state, now)
        S.mark_asked(state, "close.questions", now)
    elif move.action == "close":
        S.apply_resolution(state, move.resolve, now)
        S.apply_skips(state, move.skips, now)
        S.finish_close(state, now)


def item(state: IntakeState, key: str):
    found = state.find_item(key)
    assert found is not None, key
    return found[1]


# ============================================================================================ budgets


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (45, {"greeting": 314, "resume": 566, "mandatory": 251, "reading": 471, "topic": 471, "learning": 314, "close": 251}),
        (35, {"greeting": 243, "resume": 437, "mandatory": 194, "reading": 364, "topic": 364, "learning": 243, "close": 194}),
        (50, {"greeting": 350, "resume": 630, "mandatory": 280, "reading": 525, "topic": 525, "learning": 350, "close": 280}),
    ],
)  # fmt: skip
def test_stage_budgets_scale_the_kit_midpoints(minutes: int, expected: dict[str, int]) -> None:
    budgets = S.stage_budgets(minutes * 60)
    assert budgets == {k: float(v) for k, v in expected.items()}
    assert sum(budgets.values()) <= minutes * 60 - S.CLOSING_RESERVE_S + 4  # rounding only


def test_plan_has_seven_stages_in_kit_order_with_budgets() -> None:
    state = make_state(target_role="product_manager_technical", wa=True)
    assert [s.key for s in state.stages] == list(S.STAGE_KEYS)
    assert [s.number for s in state.stages] == [1, 2, 3, 4, 5, 6, 7]
    assert state.stages[1].budget_s == 566
    keys = [i.item_key for i in state.stage_by_key("mandatory").items]
    assert keys[:3] == ["mandatory.mq1", "mandatory.mq2", "mandatory.mq2.pm"]
    mq1 = item(state, "mandatory.mq1")
    assert (mq1.status, mq1.skip_reason, mq1.mandatory) == ("prefilled", "prefilled", True)
    # Ladder: C1 all mandatory, C2 stress compressible.
    assert [(i.item_key, i.mandatory, i.compressible) for i in state.stage_by_key("resume").items] == [
        ("resume.C1.ownership", True, False),
        ("resume.C1.mechanics", True, False),
        ("resume.C1.stress", True, False),
        ("resume.C2.ownership", True, False),
        ("resume.C2.mechanics", True, False),
        ("resume.C2.stress", True, True),
    ]


def test_mq1_is_never_asked_and_not_applicable_when_disabled() -> None:
    state = make_state(wa=False)
    mq1 = item(state, "mandatory.mq1")
    assert (mq1.status, mq1.skip_reason, mq1.mandatory) == ("not_applicable", "policy_disabled", False)
    assert item(state, "mandatory.mq2").mandatory
    assert state.find_item("mandatory.mq2.pm") is None  # not a PM target


def test_item_spec_parses_ladder_and_probe_keys() -> None:
    spec = S.item_spec("resume.C2.stress")
    assert (spec.claim_ref, spec.rung, spec.mandatory, spec.compressible) == ("C2", "stress", True, True)
    probe = S.item_spec("resume.C1.ownership.clarify")
    assert probe.rung == "ownership"
    assert S.item_spec("resume.C3.mechanics").mandatory is False
    with pytest.raises(KeyError):
        S.item_spec("resume.C4.ownership")


# ========================================================================================= the flow


def test_start_asks_the_self_introduction_and_starts_stage_one() -> None:
    state = make_state()
    move = S.next_move(state, None, 0.0)
    assert (move.action, move.item_key, move.offered) == ("ask", "greeting.intro", ())
    apply(state, move, 0.0)
    assert state.stages[0].status == "in_progress"
    assert state.answer_mode == "long" and state.soft_limit_s == 90


def test_greeting_followups_are_offered_with_the_next_model_item_and_capped_at_two() -> None:
    state = make_state()
    for key in ("greeting.intro", "greeting.project"):
        move = S.next_move(state, answer() if state.current_item else None, 30.0)
        assert move.item_key == key
        apply(state, move, 30.0)
    # project answered: the model may follow up, or ask the first ladder rung (new stage, model-phrased).
    move = S.next_move(state, answer(60), 120.0)
    assert move.offered == ("follow_up", "ask_item")
    assert (move.followup_item, move.item_key, move.stage_to) == (
        "greeting.followup",
        "resume.C1.ownership",
        "resume",
    )
    apply(state, move, 120.0, choose="follow_up")
    assert state.current_item == "greeting.followup" and state.stage.key == "greeting"
    move = S.next_move(state, answer(40), 160.0)
    assert move.model_choice
    apply(state, move, 160.0, choose="follow_up")
    assert item(state, "greeting.followup").followups_used == 2
    move = S.next_move(state, answer(40), 200.0)  # budget used up
    assert (move.action, move.item_key, move.offered, move.stage_to) == (
        "ask",
        "resume.C1.ownership",
        ("ask_item",),
        "resume",
    )
    apply(state, move, 200.0)
    assert state.stage.key == "resume" and state.stages[0].status == "completed"


def advance_to(state: IntakeState, target: str, now: float = 100.0) -> None:
    """Answer every item (no follow-ups) until ``target`` is asked."""
    move = S.next_move(state, None if state.current_item is None else answer(80), now)
    for _ in range(80):
        if move.action == "ask" and move.item_key == target and not move.model_choice:
            apply(state, move, now)
            return
        if move.model_choice and move.item_key == target:
            apply(state, move, now, choose="ask_item")
            return
        apply(state, move, now, choose="ask_item")
        if state.current_item == target:
            return
        move = S.next_move(state, answer(80), now)
    raise AssertionError(f"never reached {target}; at {state.current_item}")


def test_ladder_rung_followup_offer_and_hedge_rules() -> None:
    state = make_state()
    advance_to(state, "resume.C1.ownership", now=300.0)
    move = S.next_move(state, answer(50), 330.0)
    assert move.offered == ("follow_up", "ask_item")
    assert (move.followup_item, move.followup_key, move.item_key) == (
        "resume.C1.ownership", "resume.C1.ownership.clarify", "resume.C1.mechanics"
    )  # fmt: skip
    apply(state, move, 330.0, choose="follow_up")
    assert item(state, "resume.C1.ownership").followups_used == 1
    move = S.next_move(state, answer(50), 360.0)
    assert move.offered == ("ask_item",) and move.item_key == "resume.C1.mechanics"  # rung budget used


def test_hedge_on_ownership_skips_the_stress_rung_and_offers_no_probe() -> None:
    state = make_state()
    advance_to(state, "resume.C1.ownership", now=300.0)
    move = S.next_move(state, answer(30, hedge="not_involved"), 330.0)
    assert move.resolve is not None and move.resolve.signal == "hedge"
    assert move.offered == ("ask_item",) and move.item_key == "resume.C1.mechanics"
    apply(state, move, 330.0)
    assert state.hedges == {"C1": "hedge"}
    move = S.next_move(state, answer(40), 360.0)
    assert move.item_key == "resume.C2.ownership"
    assert ("resume.C1.stress", "not_hands_on") in move.skips


def test_model_team_level_claim_signal_also_skips_stress() -> None:
    state = make_state()
    advance_to(state, "resume.C1.mechanics", now=300.0)
    state.claim_signals["C1"] = "team_level"
    move = S.next_move(state, answer(40), 330.0)
    assert move.item_key == "resume.C2.ownership" and ("resume.C1.stress", "not_hands_on") in move.skips


def test_dont_know_on_a_rung_gets_one_probe_then_moves_on() -> None:
    state = make_state()
    advance_to(state, "resume.C1.ownership", now=300.0)
    move = S.next_move(state, answer(3, kind="dont_know"), 310.0)
    assert move.action == "dont_know_probe"
    apply(state, move, 310.0)
    move = S.next_move(state, answer(3, kind="dont_know"), 320.0)
    assert move.action == "ask" and move.item_key == "resume.C1.mechanics"
    assert move.resolve.signal == "dont_know"
    assert move.offered == ("ask_item",)  # never a follow-up after "I don't know"


def test_unclear_answers_get_one_clarification_per_item() -> None:
    state = make_state()
    advance_to(state, "greeting.intro", now=0.0)
    move = S.next_move(state, answer(2, kind="brief"), 10.0)
    assert (move.action, move.item_key, move.offered) == ("clarify", "greeting.intro", ())
    apply(state, move, 10.0)
    move = S.next_move(state, answer(1, kind="garbled"), 20.0)
    assert move.action == "ask" and move.item_key == "greeting.project"
    assert move.resolve.signal == "unclear"
    # A model item clarifies through the model.
    advance_to(state, "resume.C1.ownership", now=300.0)
    move = S.next_move(state, answer(5, kind="clarification"), 310.0)
    assert move.action == "clarify" and move.offered == ("clarify",)


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        ("repeat", "repeat"),
        ("wants_time", "wait"),
        ("instruction", "redirect"),
        ("candidate_question", "defer_question"),
    ],
)
def test_flow_turns_do_not_resolve_the_item(kind: str, action: str) -> None:
    state = make_state()
    advance_to(state, "greeting.project", now=60.0)
    move = S.next_move(state, answer(8, kind=kind), 70.0)
    assert move.action == action and move.item_key == "greeting.project" and move.resolve is None
    assert item(state, "greeting.project").status == "asked"


def test_skip_request_on_a_mandatory_item_is_encouraged_once_then_declined() -> None:
    state = make_state()
    advance_to(state, "mandatory.mq2", now=900.0)
    move = S.next_move(state, answer(4, kind="dont_know", skip_request=True), 910.0)
    assert move.action == "encourage"
    apply(state, move, 910.0)
    move = S.next_move(state, answer(4, kind="dont_know", skip_request=True), 920.0)
    assert (
        move.action == "ask" and move.resolve.status == "declined" and move.resolve.skip_reason == "declined"
    )


def test_brief_yes_is_a_valid_answer_to_the_commitment_question() -> None:
    state = make_state(digest={"training_commitment": "full"})
    state.years_hands_on = 3.0
    advance_to(state, "mandatory.mq4", now=1000.0)
    move = S.next_move(state, answer(1, kind="backchannel"), 1010.0)
    assert move.action == "ask" and move.resolve.status == "answered" and move.resolve.signal == "brief"


def test_verification_dont_know_activates_the_retry_once() -> None:
    state = make_state()
    state.language = "python"
    advance_to(state, "mandatory.mq3.verification", now=1000.0)
    move = S.next_move(state, answer(3, kind="dont_know"), 1010.0)
    assert (move.action, move.item_key, move.dynamic_after) == (
        "ask", "mandatory.mq3.verification_retry", "mandatory.mq3.verification"
    )  # fmt: skip
    apply(state, move, 1010.0)
    keys = [i.item_key for i in state.stage_by_key("mandatory").items]
    assert keys.index("mandatory.mq3.verification_retry") == keys.index("mandatory.mq3.verification") + 1
    move = S.next_move(state, answer(3, kind="dont_know"), 1020.0)
    assert move.item_key == "mandatory.mq3.rating_probe"  # no second retry


def test_language_clarifier_when_the_form_and_answer_name_no_language() -> None:
    state = make_state()
    advance_to(state, "mandatory.mq3", now=900.0)
    move = S.next_move(state, answer(20), 910.0)
    assert move.item_key == "mandatory.mq3.language"
    state2 = make_state()
    advance_to(state2, "mandatory.mq3", now=900.0)
    move = S.next_move(state2, answer(20, language="java"), 910.0)
    assert move.item_key == "mandatory.mq3.verification"


def test_pm_follow_up_is_added_when_the_spoken_target_is_a_pm_role() -> None:
    state = make_state(target_role=None)
    advance_to(state, "mandatory.mq2", now=900.0)
    move = S.next_move(state, answer(20, mentions_pm=True), 910.0)
    assert move.item_key == "mandatory.mq2.pm" and move.dynamic_after == "mandatory.mq2"


@pytest.mark.parametrize(
    ("years", "commitment", "variant", "mandatory", "compressible"),
    [
        (None, "full", "years", True, False),
        (4.0, "partial", "commit", True, False),
        (4.0, "full", "gap", False, True),
    ],
)
def test_mq4_followup_variants(years, commitment, variant, mandatory, compressible) -> None:
    state = make_state(digest={"training_commitment": commitment})
    state.years_hands_on = years
    assert S.mq4_followup_variant(state) == variant
    assert S.effective_flags(state, item(state, "mandatory.mq4.followup")) == (mandatory, compressible)


def test_rating_probe_variants() -> None:
    state = make_state()
    assert S.rating_probe_variant(state) == "unknown"
    state.self_rating = 6
    assert S.rating_probe_variant(state) == "normal"
    state.self_rating = 9
    assert S.rating_probe_variant(state) == "high"


# ================================================================================= time and budget


def test_compression_skips_optional_and_compressible_items_but_never_mandatory_ones() -> None:
    state = make_state(digest={"training_commitment": "full"})
    state.years_hands_on = 5.0
    state.language = "python"
    advance_to(state, "resume.C2.mechanics", now=600.0)
    # Very little time left: compression must be on.
    late = 2300.0
    assert S.compression_needed(state, late)
    move = S.next_move(state, answer(60), late)
    assert move.compression and "follow_up" not in move.offered  # no follow-up under compression
    assert move.item_key == "mandatory.mq2"
    assert ("resume.C2.stress", "time_budget") in move.skips
    apply(state, move, late)
    # rating probe (compressible) and the MQ4 gap follow-up (compressible variant) are skipped too.
    seen = []
    move = S.next_move(state, answer(60), late)
    for _ in range(12):
        seen.extend(move.skips)
        if move.item_key == "reading.drill_a":
            break
        apply(state, move, late)
        move = S.next_move(state, answer(60), late)
    assert ("mandatory.mq3.rating_probe", "time_budget") in seen
    assert ("mandatory.mq4.followup", "time_budget") in seen
    assert item(state, "mandatory.mq3.verification").status == "answered"
    assert move.item_key == "reading.drill_a"
    assert (move.resolve.item_key, move.resolve.status) == ("mandatory.mq4", "answered")


def test_no_compression_at_the_start_of_a_35_minute_interview() -> None:
    state = make_state(minutes=35)
    assert not S.compression_needed(state, 0.0)


def test_time_up_moves_to_the_compressed_close_and_marks_the_rest_not_reached() -> None:
    state = make_state()
    advance_to(state, "reading.followup.least_sure", now=1800.0)
    move = S.next_move(state, None, 2700.0)
    assert move.action == "time_up" and move.resolve.status == "not_reached"
    apply(state, move, 2700.0)
    assert state.time_up and state.stage.key == "close"
    assert item(state, "reading.followup.non_technical").status == "not_reached"
    assert item(state, "topic.card").skip_reason == "time_up"
    assert state.stage_by_key("reading").status == "incomplete"
    assert state.stage_by_key("topic").status == "incomplete"
    assert item(state, "close.anything_else").status == "skipped"
    # One question at most, then the closing.
    move = S.next_move(state, answer(12, kind="candidate_question"), 2710.0)
    assert move.offered == ("answer_question",)
    apply(state, move, 2710.0)
    assert state.closing_answers == 1
    move = S.next_move(state, answer(10, kind="candidate_question"), 2730.0)
    assert move.action == "close" and move.variant == "hiring_team"


def test_soft_cap_stops_followups_in_a_stage() -> None:
    state = make_state()
    advance_to(state, "greeting.project", now=10.0)
    stage = state.stage
    late = stage.started_s + stage.budget_s * 1.3
    move = S.next_move(state, answer(60), late)
    assert move.offered == ("ask_item",) and move.item_key == "resume.C1.ownership"


def test_c3_runs_only_with_enough_stage_budget_left() -> None:
    state = make_state(claims=CLAIMS3)
    advance_to(state, "resume.C2.stress", now=300.0)
    resume = state.stage_by_key("resume")
    roomy = resume.started_s + 100.0
    move = S.next_move(state, answer(40), roomy)
    assert move.item_key == "resume.C3.ownership"
    state2 = make_state(claims=CLAIMS3)
    advance_to(state2, "resume.C2.stress", now=300.0)
    resume2 = state2.stage_by_key("resume")
    tight = resume2.started_s + resume2.budget_s - 100.0
    move = S.next_move(state2, answer(40), tight)
    assert move.item_key == "mandatory.mq2"
    assert {("resume.C3.ownership", "time_budget")} <= set(move.skips)


def test_followup_before_a_template_item_depends_on_the_answer_length() -> None:
    state = make_state()
    state.self_rating = 7
    advance_to(state, "learning.three_weeks", now=1500.0)
    long = S.next_move(state, answer(120), 1510.0)
    assert long.item_key == "learning.teach" and long.offered == ()
    short = S.next_move(state, answer(12), 1510.0)
    assert short.offered == ("follow_up",) and short.item_key == "learning.followup"


def test_scenarios_respect_the_round_limit() -> None:
    state = make_state(limits={"scenarios": 0})
    advance_to(state, "topic.weakest", now=1400.0)
    move = S.next_move(state, answer(80), 1410.0)
    assert ("topic.scenario", "policy_disabled") in move.skips


# ================================================================================== closing stage


def test_closing_stage_answers_questions_then_closes() -> None:
    state = make_state(limits={"closing_questions_max": 2})
    advance_to(state, "close.questions", now=2400.0)
    move = S.next_move(state, answer(9, kind="candidate_question"), 2410.0)
    assert (move.item_key, move.offered, move.followup_item) == (
        "close.answer",
        ("answer_question",),
        "close.answer",
    )
    apply(state, move, 2410.0)
    move = S.next_move(state, answer(9, kind="candidate_question"), 2420.0)
    apply(state, move, 2420.0)
    assert state.closing_answers == 2
    move = S.next_move(state, answer(9, kind="candidate_question"), 2430.0)
    assert move.action == "close" and move.variant == "hiring_team"


def test_no_questions_goes_to_anything_else_then_closing() -> None:
    state = make_state()
    advance_to(state, "close.questions", now=2400.0)
    move = S.next_move(state, answer(3, kind="no_questions"), 2410.0)
    assert move.item_key == "close.anything_else"
    apply(state, move, 2410.0)
    move = S.next_move(state, answer(2, kind="no_questions"), 2420.0)
    assert move.action == "close"
    apply(state, move, 2420.0)
    assert state.closed
    assert item(state, "close.closing").status == "answered"
    assert all(s.status == "completed" for s in state.stages)


def test_finish_close_marks_unreached_stages_incomplete() -> None:
    state = make_state()
    advance_to(state, "resume.C1.ownership", now=300.0)
    S.finish_close(state, 400.0, reached=True)
    assert state.stage_by_key("resume").status == "incomplete"
    assert state.stage_by_key("learning").status == "incomplete"
    assert item(state, "topic.card").status == "not_reached"
    assert state.stage_by_key("greeting").status == "completed"


def test_next_move_never_mutates_the_state() -> None:
    state = make_state()
    advance_to(state, "resume.C1.ownership", now=300.0)
    before = state.model_dump()
    for signal in (answer(40), answer(3, kind="dont_know"), answer(30, hedge="no_detail"), None):
        S.next_move(state, signal, 400.0)
    assert state.model_dump() == before


# ======================================================================================= text helpers


@pytest.mark.parametrize(
    ("text", "rating"),
    [
        ("Python, and I'd rate myself seven out of ten.", 7),
        ("Java, maybe 6/10", 6),
        ("I would rate myself a 8 honestly", 8),
        ("about 7.5 out of 10", 8),
        ("I have done some scripting", None),
    ],
)
def test_parse_self_rating(text: str, rating: int | None) -> None:
    assert S.parse_self_rating(text) == rating


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("About three years of hands-on work", 3.0),
        ("2.5 years", 2.5),
        ("half a year", 0.5),
        ("none really", None),
    ],
)
def test_parse_years(text: str, years: float | None) -> None:
    assert S.parse_years(text) == years


@pytest.mark.parametrize(
    ("text", "skip"),
    [
        ("Can we skip this one?", True),
        ("I'd rather not answer that", True),
        ("pass", True),
        ("We skipped tests", False),
    ],
)
def test_wants_skip(text: str, skip: bool) -> None:
    assert S.wants_skip(text) is skip


def test_mentions_pm() -> None:
    assert S.mentions_pm("I want to be a product manager, technical PM ideally")
    assert not S.mentions_pm("I want to be a data analyst")
