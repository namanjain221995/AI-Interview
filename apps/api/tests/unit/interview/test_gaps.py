"""Gap-targeted follow-ups: what a substantive answer left out (app.services.interview.gaps)."""

from __future__ import annotations

from app.domain.enums import TurnAction
from app.services.interview.gaps import answer_gaps, gap_instruction, uncovered_points

DESIGN_Q = "How did you design the rollout so that a bad release could not take down every cluster?"


def kinds(answer: str, **kw) -> list[str]:
    return [g.kind for g in answer_gaps(answer, **kw)]


def test_a_complete_answer_has_no_gap() -> None:
    answer = (
        "I split the fleet into three waves and ran canaries at five percent first, because a regional "
        "failure was our biggest risk. I chose Argo Rollouts over plain deployments since it gave us automated "
        "analysis, and the error budget alert cut our rollback time from forty minutes to six."
    )
    assert kinds(answer, question=DESIGN_Q) == []


def test_good_answers_from_the_live_run_have_no_gap() -> None:
    cost = (
        "Monthly spend went from about $120,000 to roughly 70,000 over six months. The biggest single win was "
        "moving our batch processing to spot instances, then right-sizing the RDS databases and adding savings "
        "plans for the steady baseline. We tracked it weekly with Cost Explorer."
    )
    assert kinds(cost, question="How did you measure the baseline before the migration to verify that saving?") == []
    short = "We reduced cloud costs by 40%, mostly by rightsizing."
    assert kinds(short, question="How did you cut the cloud bill?") == []  # short: the policy's paths handle it


def test_team_only_answer_asks_for_their_own_part() -> None:
    answer = (
        "We moved the services to EKS over two quarters. Our platform group wrote the Terraform modules and we "
        "migrated forty services, and the team ran the cutover in three waves with our on-call watching."
    )
    assert kinds(answer, question="Tell me about the EKS migration.")[0] == "own_role"
    mine = answer.replace("Our platform group wrote", "I wrote")
    assert "own_role" not in kinds(mine, question="Tell me about the EKS migration.")


def test_an_outcome_claim_without_any_number_asks_for_one() -> None:
    answer = (
        "I profiled the ingestion path and found the JSON parsing was the hot spot, so I moved it to a streaming "
        "parser and batched the writes, which reduced latency a lot and made the nightly job much faster."
    )
    assert "number" in kinds(answer, question="What did you do about the slow ingestion job?")
    with_number = answer.replace("a lot", "from 900 to 250 milliseconds")
    assert "number" not in kinds(with_number, question="What did you do about the slow ingestion job?")


def test_a_design_answer_without_reasons_asks_why() -> None:
    answer = (
        "I set up three clusters in separate regions with Argo CD syncing each one from its own branch, and the "
        "pipeline promoted a release from staging to the first region and then the other two after a manual "
        "approval in the deployment dashboard."
    )
    assert kinds(answer, question=DESIGN_Q) == ["reasoning"]
    reasoned = answer + " I did it that way because a single control plane was the risk we wanted to remove."
    assert kinds(reasoned, question=DESIGN_Q) == []


def test_an_uncovered_expected_point_comes_first() -> None:
    answer = (
        "I put the canary analysis on the error rate and the p99 latency from Prometheus, because those two "
        "caught every bad release in the previous quarter; the analysis ran for ten minutes per wave."
    )
    expected = ["canary analysis metrics", "rollback trigger and how fast it fires", "blast radius per wave"]
    gaps = answer_gaps(answer, question=DESIGN_Q, expected_evidence=expected)
    assert gaps[0].kind == "expected_point"
    assert "rollback trigger" in gaps[0].ask


def test_an_off_topic_answer_gets_no_expected_point() -> None:
    answer = (
        "Honestly I mostly worked on the billing service that year, writing Python APIs and some SQL reports, and "
        "I helped onboard two new engineers to the codebase and the on-call rotation, which went well."
    )
    missing, covered = uncovered_points(answer, ["canary analysis metrics", "rollback trigger"])
    assert covered == 0 and len(missing) == 2
    assert "expected_point" not in kinds(answer, question=DESIGN_Q, expected_evidence=["canary analysis metrics"])


def test_behavioural_answers_without_an_outcome_ask_for_it() -> None:
    answer = (
        "There was a disagreement about the on-call rota, so I set up a meeting with both leads, listened to "
        "their concerns and proposed splitting the rota by service ownership with a shared escalation path."
    )
    assert kinds(answer, question="Tell me about a conflict in your team.", technical=False) == ["outcome"]
    told = answer + " As a result pages per person dropped and nobody asked to leave the rota after that."
    assert kinds(told, question="Tell me about a conflict in your team.", technical=False) == []


def test_the_instruction_only_appears_when_the_move_can_stay_on_the_answer() -> None:
    gaps = answer_gaps(
        "We moved the services to EKS over two quarters. Our platform group wrote the modules and we migrated "
        "forty services, and the team ran the cutover in three waves with our on-call watching.",
        question="Tell me about the EKS migration.",
    )
    line = gap_instruction(gaps, recommended=TurnAction.ASK_FOLLOWUP)
    assert line is not None and "personally did" in line and "<derived_answer_gap" in line
    assert gap_instruction(gaps, recommended=TurnAction.CHANGE_TOPIC) is None
    assert gap_instruction(
        gaps, recommended=TurnAction.CHANGE_TOPIC, alternatives=[TurnAction.ASK_FOLLOWUP]
    ) is not None
    # A follow-up the policy no longer allows (follow-up limit reached) gets no probe.
    assert (
        gap_instruction(
            gaps,
            recommended=TurnAction.CHANGE_TOPIC,
            alternatives=[TurnAction.ASK_FOLLOWUP],
            allowed=[TurnAction.CHANGE_TOPIC],
        )
        is None
    )
    assert gap_instruction([], recommended=TurnAction.ASK_FOLLOWUP) is None


def test_a_follow_up_that_asked_for_the_reason_is_not_asked_a_third_time() -> None:
    """Live run: "what trade-off did you make?" was asked, not answered, and the gap probe asked it again."""
    question = "What specific trade-off did you make between cluster complexity and operational overhead?"
    answer = (
        "Sure. We ran Amazon EKS with three managed node groups across three availability zones. Karpenter handled "
        "node autoscaling, and the horizontal pod autoscaler scaled deployments on CPU and request rate."
    )
    assert "reasoning" in kinds(answer, question=question)  # first time: one follow-up may ask for it
    assert "reasoning" not in kinds(answer, question=question, followup=True)
    numbers = "How many nodes did the cluster run at peak, roughly?"
    vague = "It was a fairly large cluster with a lot of nodes across the zones, and we scaled it up during the sales."
    assert "number" in kinds(vague, question=numbers)
    assert "number" not in kinds(vague, question=numbers, followup=True)
