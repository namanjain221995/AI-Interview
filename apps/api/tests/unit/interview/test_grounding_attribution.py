"""Grounding of attributions and inconsistency records; the streaming attribution guard (pure, no I/O).

Harness finding (contradictor persona, judge 27): "Earlier you noted Karpenter configuration for cost
reduction" - the candidate had said "I wasn't involved in the overall cost-reduction initiatives" and "the
existing Karpenter settings"; "cost" came from the interviewer's own question and the resume. The old check
compared the phrase with the union of everything the candidate said and passed it.
"""

from __future__ import annotations

import pytest

from app.ai.base import CallContext
from app.domain.enums import ModelTask
from app.schemas.ai import TurnDecision
from app.services.interview.attribution import (
    AttributionGuard,
    attribution_frame_recent,
    opens_with_attribution,
)
from app.services.interview.config import EngineConfig
from app.services.interview.grounding import (
    GroundingContext,
    find_references,
    inconsistency_issue,
    numbers_to_digits,
    ungrounded_reference,
)
from app.services.interview.state import AnswerRecord, ExchangeTurn
from app.services.interview.turns import TURN_SCHEMA, TurnOutcome, stream_speech_object

from .factories import decision, state

C1 = (
    "Sure. The architecture was centered around Amazon EKS. I handled the day-to-day operations for the cluster "
    "we directly maintained, which had about eight nodes. We used Karpenter for automated node provisioning to "
    "handle the load spikes."
)
C2 = (
    "I should clarify that I was primarily working on the single cluster with about eight nodes. Since I wasn't "
    "involved in the overall cost-reduction initiatives, which were handled at a higher level, I can't speak to "
    "the configuration across all three clusters. I focused on my four services using the existing Karpenter "
    "settings."
)
RESUME = {
    "roles": [
        {
            "title": "Senior DevOps Engineer",
            "company": "Bluepeak",
            "achievements": [
                {
                    "text": "Reduced cloud costs by 40% via rightsizing, Karpenter spot capacity and lifecycle policies."
                },
                {"text": "Designed a production EKS platform (3 clusters, about 120 nodes)."},
            ],
        }
    ]
}
INTERVIEWER_Q = (
    "You mentioned Karpenter for node provisioning. How did you configure it to handle mixed workloads across "
    "those three clusters while keeping costs down?"
)


def contradictor_state():
    s = state(candidate_profile=RESUME)
    for i, text in enumerate((C1, C2), start=1):
        s.answers.append(AnswerRecord(id=f"a{i}", question_id=None, sequence=i, text=text))
        s.conversation.append(ExchangeTurn(speaker="interviewer", text=INTERVIEWER_Q, at_s=float(i)))
        s.conversation.append(
            ExchangeTurn(speaker="candidate", text=text, at_s=float(i) + 0.5, answer_id=f"a{i}")
        )
    return s


# ---------------------------------------------------------------- speech attributions


def test_invented_attribution_from_the_interviewers_own_question_is_ungrounded() -> None:
    s = contradictor_state()
    speech = (
        "Earlier you noted Karpenter configuration for cost reduction, but then said you weren't involved in "
        "cost initiatives. Could you help me reconcile that?"
    )
    assert ungrounded_reference(s, speech) == "Karpenter configuration for cost reduction"
    # The second attribution (negated, as the candidate said it) is supported on its own.
    assert (
        ungrounded_reference(s, "You said you weren't involved in the cost initiatives. Why was that?")
        is None
    )


def test_supported_attributions_pass_per_turn_with_asr_spellings() -> None:
    s = contradictor_state()
    assert (
        ungrounded_reference(s, "You mentioned Karpenter for node provisioning. How did you tune it?") is None
    )
    asr = state(candidate_profile={})
    asr.answers.append(
        AnswerRecord(id="a", question_id=None, sequence=1, text="We used Carpenter with spot capacity.")
    )
    assert (
        ungrounded_reference(
            asr, "You mentioned Karpenter with spot capacity. How were interruptions handled?"
        )
        is None
    )


def test_polarity_must_match() -> None:
    s = state(candidate_profile={})
    s.answers.append(
        AnswerRecord(
            id="a",
            question_id=None,
            sequence=1,
            text="We didn't use Argo Rollouts, only plain Helm upgrades.",
        )
    )
    assert ungrounded_reference(s, "You mentioned Argo Rollouts for canaries. How did you set the steps?")
    assert ungrounded_reference(s, "You said you didn't use Argo Rollouts. Why not?") is None


def test_resume_content_counts_only_when_framed_as_the_resume() -> None:
    s = contradictor_state()
    # The resume says it; the candidate never did.
    assert ungrounded_reference(
        s, "You mentioned cutting cloud costs by forty percent. How was that measured?"
    )
    assert (
        ungrounded_reference(
            s, "Your resume mentions cutting cloud costs by forty percent. How was it measured?"
        )
        is None
    )
    assert (
        ungrounded_reference(s, "You mentioned on your resume reducing cloud costs with Karpenter. How?")
        is None
    )


def test_interviewer_turns_and_summary_never_count() -> None:
    s = state(candidate_profile={})
    s.conversation.append(
        ExchangeTurn(speaker="interviewer", text="How did you keep costs down with spot?", at_s=1)
    )
    s.answers.append(
        AnswerRecord(id="a", question_id=None, sequence=1, text="I mostly handled the deploy pipelines.")
    )
    s.memory_summary = "Candidate kept costs down with spot instances."
    assert ungrounded_reference(s, "You mentioned keeping costs down with spot instances. How?")


def test_reference_shapes() -> None:
    refs = find_references(
        "Got it. As you said, the rollback was manual. How did the Karpenter setup you mentioned behave at peak?"
    )
    assert [r.phrase for r in refs] == ["the rollback was manual", "Karpenter setup"]
    refs = find_references("You mentioned S3 and DynamoDB for locking, so how did you version modules?")
    assert refs[0].phrase == "S3 and DynamoDB for locking" and not refs[0].resume
    refs = find_references("Your answer about the outage stayed high level; what failed first?")
    assert refs[0].phrase == "the outage stayed high level"
    assert find_references("How did you size the node groups?") == []
    assert numbers_to_digits("one hundred and twenty nodes, twelve hundred pods, 10,000 users") == (
        "120 nodes, 1200 pods, 10000 users"
    )


# ---------------------------------------------------------------- inconsistency records


def test_inconsistency_records_need_the_candidates_own_words() -> None:
    ctx = GroundingContext.from_state(contradictor_state())
    # Evaluator record: resume claim vs the candidate's quote -> grounded.
    good = (
        "Resume c3: 'Reduced cloud costs by 40% via rightsizing, Karpenter spot capacity' / earlier: Answer: "
        "'Since I wasn't involved in the overall cost-reduction initiatives, I can't speak to the configuration.'"
    )
    assert inconsistency_issue(ctx, good) is None
    # The "candidate" side is the interviewer's question, not something the candidate said.
    invented = "Candidate said they configured Karpenter for mixed workloads to keep costs down; resume says 120 nodes"
    assert inconsistency_issue(ctx, invented)
    # Structured sides (evaluator statement / conflicts_with).
    assert (
        inconsistency_issue(
            ctx, "", sides=["Worked on a single cluster with about eight nodes", "Resume: about 120 nodes"]
        )
        is None
    )
    assert inconsistency_issue(ctx, "", sides=["Resume says 40% cost reduction", "Resume lists Karpenter"])


# ---------------------------------------------------------------- streaming guard


def _guard(s, **kw) -> AttributionGuard:
    return AttributionGuard(lambda: GroundingContext.from_state(s), seed="x", **kw)


def _feed(guard: AttributionGuard, text: str, chunk: int = 4) -> str:
    out = "".join(guard.feed(text[i : i + chunk]) for i in range(0, len(text), chunk))
    return out + guard.flush()


def test_unsupported_preamble_is_dropped_and_the_question_kept() -> None:
    g = _guard(contradictor_state())
    out = _feed(
        g, "You mentioned leading the cost program at Bluepeak. Which tool did you use for spot capacity?"
    )
    assert out == "Which tool did you use for spot capacity?"
    assert g.unsupported is not None and g.regenerate is None and g.dropped


def test_supported_preamble_passes_and_ordinary_sentences_are_not_held() -> None:
    s = contradictor_state()
    g = _guard(s)
    text = "You mentioned Karpenter for node provisioning. How did you size the node pools?"
    assert _feed(g, text) == text and g.held == 1
    g = _guard(s)
    assert g.feed("How") == "" and g.feed(" did") == "How did"  # the first complete word decides
    assert opens_with_attribution(["okay", "so", "you", "mentioned"]) is True
    assert opens_with_attribution(["you", "configured"]) is False
    assert opens_with_attribution(["you"]) is None


def test_unsupported_attribution_inside_the_question_or_a_probe_asks_for_regeneration() -> None:
    s = contradictor_state()
    g = _guard(s)
    assert (
        _feed(g, "You mentioned Karpenter configuration for cost reduction, so how did you measure it?") == ""
    )
    assert g.regenerate is not None and g.regenerate.phrase == "Karpenter configuration for cost reduction"
    g = _guard(s, probe=True)
    probe = (
        "Earlier you noted Karpenter configuration for cost reduction, but then said you weren't involved in "
        "cost initiatives. Could you help me reconcile that?"
    )
    assert _feed(g, probe) == "" and g.regenerate is not None
    # Without a regeneration left, the clause is stripped and the question still asked.
    g = _guard(s, allow_regenerate=False)
    out = _feed(
        g, "You mentioned Karpenter configuration for cost reduction, so how did you measure savings?"
    )
    assert out == "How did you measure savings?"


def test_repeated_you_mentioned_frame_becomes_a_specific_acknowledgement() -> None:
    s = contradictor_state()
    s.conversation.append(
        ExchangeTurn(speaker="interviewer", text="You mentioned eight nodes. Why eight?", at_s=9)
    )
    assert attribution_frame_recent(s)
    g = _guard(s, frame_recent=True)
    out = _feed(g, "You mentioned Karpenter for node provisioning. How did you size the node pools?")
    assert out.endswith("Karpenter for node provisioning. How did you size the node pools?")
    assert out.split(",")[0] in ("Right", "Okay", "So") and g.rewritten
    g = _guard(s, frame_recent=True, released_before=True)
    out = _feed(g, "You mentioned Karpenter for node provisioning. How did you size the node pools?")
    assert out == "Karpenter for node provisioning. How did you size the node pools?"


async def test_stream_regenerates_once_when_the_question_depends_on_an_invented_attribution(mock_ai) -> None:
    s = contradictor_state()
    mock_ai.llm.chunk_size = 5
    mock_ai.llm.script(
        ModelTask.TURN_DECISION,
        decision(speech="Earlier you said you cut costs with Karpenter, so how much did you save per month?"),
        decision(speech="The resume mentions a forty percent cost reduction; which part of that was yours?"),
    )
    outcome = TurnOutcome()
    deltas = [
        d
        async for d in stream_speech_object(
            mock_ai,
            [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "x\nReply with the JSON object only."},
            ],
            schema=TurnDecision,
            json_schema=TURN_SCHEMA,
            context=CallContext(task=ModelTask.TURN_DECISION),
            utterance_id="u1",
            outcome=outcome,
            config=EngineConfig(closing_reserve_s=75.0, default_interviewer_name="Maya"),
            guard_factory=lambda allow, before: AttributionGuard(
                lambda: GroundingContext.from_state(s), allow_regenerate=allow, released_before=before
            ),
        )
    ]
    spoken = "".join(d.text for d in deltas).strip()
    assert spoken == "The resume mentions a forty percent cost reduction; which part of that was yours?"
    assert outcome.regenerated and outcome.ungrounded == "you cut costs with Karpenter"
    assert outcome.decision is not None and outcome.decision.speech == spoken
    calls = mock_ai.llm.calls_for(ModelTask.TURN_DECISION)
    assert len(calls) == 2 and "<derived_ungrounded_reference>" in calls[1].messages[-1]["content"]


@pytest.mark.parametrize(
    "speech",
    [
        "Got it. How did you size the node pools?",
        "Switching to AWS for a moment: how is the VPC laid out?",
        "Thanks, Jane. Earlier in your career, which on-call tools did you use?",
    ],
)
def test_text_without_attributions_streams_unchanged(speech: str) -> None:
    g = _guard(contradictor_state())
    assert _feed(g, speech, chunk=3) == speech and g.held == 0


# ---------------------------------------------------------------- inconsistency records at every entry point


def test_ungrounded_possible_contradiction_is_removed_by_the_post_check() -> None:
    from app.services.interview.policy import check_decision, compute_directive

    from .factories import T0, ask

    s = contradictor_state()
    ask(s, "kubernetes")
    d = compute_directive(s, now=T0, closing_reserve_s=75.0)
    invented = decision(
        possible_contradiction="Candidate said they tuned Karpenter to keep costs down; resume says 120 nodes"
    )
    check = check_decision(d, invented, s)
    assert check.decision.possible_contradiction is None and "ungrounded_contradiction" in check.deviations
    grounded = decision(
        possible_contradiction=(
            "Resume claims a 40% cost reduction via Karpenter; candidate said they weren't involved in the "
            "cost-reduction initiatives"
        )
    )
    check = check_decision(d, grounded, s)
    assert check.decision.possible_contradiction and "ungrounded_contradiction" not in check.deviations


def test_apply_evaluation_drops_records_not_in_the_candidates_words() -> None:
    from app.services.interview.transitions import apply_evaluation

    s = contradictor_state()
    before = s.deviations
    payload = {
        "answer_id": "a2",
        "contradictions": [
            {
                "description": "Configured Karpenter for mixed workloads / earlier: Resume says 120 nodes",
                "sides": [
                    "Configured Karpenter for mixed workloads while keeping costs down",
                    "Resume: about 120 nodes",
                ],
                "severity": "high",
            },
            {
                "description": "Worked on a single cluster with about eight nodes / earlier: Resume says about 120 nodes",
                "sides": ["Worked on a single cluster with about eight nodes", "Resume says about 120 nodes"],
                "severity": "medium",
            },
        ],
    }
    assert apply_evaluation(s, payload)
    assert [c.severity for c in s.contradictions] == ["medium"]
    assert s.deviations == before + 1


def test_evaluation_job_grounds_reported_contradictions() -> None:
    from app.schemas.ai import AnswerEvaluation, ContradictionFinding, DimensionScores
    from app.services.interview.evaluation import ground_contradictions

    s = contradictor_state()
    evaluation = AnswerEvaluation(
        overall_quality="partial",
        scores=DimensionScores(),
        summary="x",
        contradictions=[
            ContradictionFinding(
                statement="Says they configured Karpenter for cost reduction",
                conflicts_with="Earlier answer: kept costs down across three clusters",
                severity="high",
            ),
            ContradictionFinding(
                statement="I wasn't involved in the overall cost-reduction initiatives",
                conflicts_with="Resume c3: Reduced cloud costs by 40% via rightsizing and Karpenter spot capacity",
                severity="medium",
            ),
        ],
        evaluation_confidence=0.8,
    )
    cleaned, dropped = ground_contradictions(evaluation, s, answer_text=C2)
    assert [c.severity for c in cleaned.contradictions] == ["medium"] and len(dropped) == 1


def test_hedged_answers_count_toward_the_limited_pattern_unless_strong() -> None:
    from app.domain.enums import AnswerQuality
    from app.services.interview.policy import PATTERN_LIMITED, answer_pattern

    hedge = "Honestly, that was handled by the platform team, so I can't speak to the details."
    s = state(candidate_profile={})
    for i, quality in enumerate((None, AnswerQuality.PARTIAL, AnswerQuality.ADEQUATE), start=1):
        s.answers.append(AnswerRecord(id=f"a{i}", question_id=None, sequence=i, text=hedge, quality=quality))
    s.answers.append(AnswerRecord(id="a9", question_id=None, sequence=9, text="current"))
    assert answer_pattern(s) == PATTERN_LIMITED
    for a in s.answers[:3]:
        a.quality = AnswerQuality.STRONG
    assert answer_pattern(s) is None


# Harness (contradictor persona r2): C1 explained the resume's 120 nodes ("were managed by a separate platform
# team. My direct experience was on a single EKS cluster") but was not detected as a hedge, so the interviewer
# still asked the candidate to "reconcile" the discrepancy two turns later.
EXPLAINED = (
    "Well, to be transparent, the 120 nodes actually belonged to the entire company footprint and were managed "
    "by a separate platform team. My direct experience was on a single EKS cluster with about 8 nodes."
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (EXPLAINED, "not_involved"),
        ("My hands-on responsibility was limited to a single EKS cluster with eight nodes.", "not_involved"),
        ("I didn't architect the large-scale scaling policies for the bigger cluster.", "not_involved"),
        # Tools and services after "managed by" are not people: no hedge.
        ("The cluster was managed by Terraform and Argo CD.", None),
        ("The databases were run by RDS with multi-AZ failover.", None),
        ("My role was to lead the migration of twelve services.", None),
        ("My experience is mostly with Kubernetes on AWS.", None),
    ],
)
def test_hedge_kind_scope_limiting_statements(text: str, kind: str | None) -> None:
    from app.services.interview.special import hedge_kind

    assert hedge_kind(text) == kind


def test_discrepancy_already_explained_by_the_candidate_is_not_probed() -> None:
    from app.services.interview.policy import explained_by_candidate, open_contradiction
    from app.services.interview.state import ContradictionState

    s = state(candidate_profile={})
    s.answers.append(AnswerRecord(id="a1", question_id=None, sequence=1, text=EXPLAINED))
    s.contradictions.append(
        ContradictionState(
            id="x1",
            description="Resume says 120 nodes; candidate ran 8",
            refs=["a1", "resume"],
            severity="medium",
        )
    )
    assert explained_by_candidate(s, ["a1", "resume"])
    assert open_contradiction(s) is None
    # An unexplained discrepancy (plain statement, no "someone else's part") is still probed once.
    s.answers[0].text = "I ran a single EKS cluster with about 8 nodes."
    assert not explained_by_candidate(s, ["a1", "resume"])
    found = open_contradiction(s)
    assert found is not None and found.id == "x1"
    # Without referenced answers nothing counts as explained.
    assert not explained_by_candidate(s, ["resume"])


@pytest.mark.parametrize(("minutes", "expected"), [(10, 2), (15, 2), (30, 3)])
def test_short_rounds_cap_followups_per_topic(minutes: int, expected: int) -> None:
    from app.services.interview.policy import followup_limit

    from .factories import ask, round_config

    rc = round_config(duration_minutes=minutes, max_duration_minutes=minutes + 5)
    s = state(rc=rc, candidate_profile={})
    ask(s, "kubernetes")
    # Competency still uncertain: the round's depth (3) applies unless the round is short.
    assert followup_limit(s, s.competencies["kubernetes"]) == expected



# ------------------------------------------------- "what you meant by X" / "what you were about to say regarding X"


def test_meant_and_about_to_say_frames_are_attributions_checked_against_the_candidate() -> None:
    """Live intake 2026-10-10: after "Exactly." the model asked "what you meant by exactly regarding the retries or
    deduplication" - a topic from the interviewer's own question, never said by the candidate."""
    from app.services.interview.grounding import GroundingContext, find_references

    meant = "Could you clarify what you meant by exactly regarding the retries or deduplication in that platform?"
    about = "Could you clarify what you were about to say regarding the retries or deduplication?"
    assert [r.phrase for r in find_references(meant)] == [
        "exactly regarding the retries or deduplication in that platform"
    ]
    assert [r.phrase for r in find_references(about)] == ["the retries or deduplication"]
    ctx = GroundingContext(candidate=["so for that", "Exactly.", "Yeah, that's it."], resume=[])
    assert ctx.unsupported(meant) is not None and ctx.unsupported(about) is not None
    said = GroundingContext(candidate=["We handled retries with an idempotency key for deduplication."], resume=[])
    assert said.unsupported(about) is None  # grounded when they did say it
