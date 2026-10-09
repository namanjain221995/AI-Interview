"""Deterministic validator V1-V8 (INTAKE_INTERVIEW.md 8.7)."""

from __future__ import annotations

import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.intake.demo_content import demo_evaluator_outputs
from app.services.intake.eval_schemas import EvidenceCite
from app.services.intake.evidence import (
    PromptPrivacyError,
    assert_prompt_safe,
    assign_aliases,
    forbidden_kit_texts,
)
from app.services.intake.validator import (
    ArithmeticMismatch,
    check_arithmetic,
    mandatory_findings,
    normalize_quote,
    quote_matches,
    required_evidence,
    validate_cites,
)

from .eval_fixtures import demo_inputs, evaluate_demo


def _raw_outputs() -> dict:
    _rows, inputs = demo_inputs()
    assign_aliases(inputs)
    return demo_evaluator_outputs(inputs)


# ------------------------------------------------------------------------------------------- baseline


def test_demo_evaluation_is_valid_and_banded() -> None:
    result = evaluate_demo()
    assert not result.validation.issues
    assert {f.kind for f in result.validation.findings} == set()
    assert result.validation.summary.band.value == "accept_with_notes"
    assert result.validation.summary.total == Decimal("4.130")
    scores = result.validation.scores()
    assert scores["technical_reading"] == Decimal("4.4") and scores["adaptability"] == Decimal(5)


# ------------------------------------------------------------------------------------------- V1 schema


def test_v1_fallback_makes_competencies_insufficient() -> None:
    result = evaluate_demo(drop=("trainability",))
    comps = result.validation.competencies
    assert comps["trainability"].status == comps["adaptability"].status == "insufficient"
    kinds = [f for f in result.validation.findings if f.kind == "evaluator_fallback"]
    assert kinds and set(kinds[0].competency_keys) == {"trainability", "adaptability"}
    assert result.validation.summary.band.value == "insufficient_evidence"
    assert result.validation.summary.partial_total is not None


# ------------------------------------------------------------------------------------------- V2 evidence


def test_v2_quote_matching_is_tolerant_but_never_inventive() -> None:
    answer = (
        "I personally built four of the six pipelines we run in Airflow, including the delivery delay one."
    )
    assert quote_matches("I personally built four of the six pipelines", answer)
    assert quote_matches(
        "“i PERSONALLY built  four of the six pipelines…”", answer
    )  # quotes/case/space/ellipsis
    assert quote_matches("I personaly built four of the six pipelines", answer)  # ASR-style near miss
    assert not quote_matches("I designed the whole Airflow platform myself", answer)
    assert not quote_matches(answer + " and I also led the data science team for two years", answer)  # longer
    assert not quote_matches("ok", answer)
    assert normalize_quote(" 'Hello…' ") == "hello"


def test_v2_citations_outside_the_evaluator_scope_are_dropped() -> None:
    result = evaluate_demo()
    aliases = result.aliases
    relay_alias = result.inputs.activity_answer("read_relay").alias
    intro_alias = result.inputs.answers_for_item("greeting.intro")[0].alias
    cites = [
        EvidenceCite(answer=intro_alias, quote="I've been working as a data engineer", supports="positive"),
        EvidenceCite(answer=relay_alias, quote="learns the training data too well", supports="positive"),
        EvidenceCite(answer="A999", quote="anything at all here", supports="positive"),
        EvidenceCite(answer="ans 1", quote="anything at all here", supports="positive"),
    ]
    refs, dropped = validate_cites(cites, aliases=aliases, allowed=result.allowed["communication"])
    assert [r["answer_id"] for r in refs] == [str(result.inputs.answers_for_item("greeting.intro")[0].id)]
    assert len(dropped) == 3


def test_v2_invalid_quotes_are_dropped_and_reported() -> None:
    raw = _raw_outputs()
    bad = copy.deepcopy(raw["communication"])
    bad["judgement"]["evidence"].append(
        {
            "answer": bad["judgement"]["evidence"][0]["answer"],
            "quote": "I invented this sentence",
            "supports": "positive",
        }
    )
    result = evaluate_demo(overrides={"communication": bad})
    issue = next(i for i in result.validation.issues if i.code == "evidence_dropped")
    assert issue.competency == "communication"
    assert result.validation.competencies["communication"].status == "scored"


# ------------------------------------------------------------------------------------------- V3 support


@pytest.mark.parametrize(
    ("score", "supports", "status"),
    [
        (4, ["context"], "insufficient"),  # >= 4 needs a positive citation
        (4, ["positive"], "scored"),
        (2, ["positive"], "insufficient"),  # <= 2 needs negative or context
        (2, ["negative"], "scored"),
        (3, ["context"], "scored"),
    ],
)
def test_v3_scores_need_supporting_citations(score: int, supports: list[str], status: str) -> None:
    raw = _raw_outputs()
    composure = copy.deepcopy(raw["composure"])
    evidence = composure["judgement"]["evidence"]
    composure["judgement"]["score"] = score
    composure["judgement"]["evidence"] = [dict(evidence[0], supports=s) for s in supports]
    result = evaluate_demo(overrides={"composure": composure})
    assert result.validation.competencies["composure"].status == status
    if status == "insufficient":
        assert any(i.code == "score_unsupported" for i in result.validation.issues)


def test_v3_no_citations_and_sufficient_false() -> None:
    raw = _raw_outputs()
    empty = copy.deepcopy(raw["composure"])
    empty["judgement"]["evidence"] = []
    assert (
        evaluate_demo(overrides={"composure": empty}).validation.competencies["composure"].status
        == "insufficient"
    )
    unsure = copy.deepcopy(raw["composure"])
    unsure["judgement"]["sufficient"] = False
    result = evaluate_demo(overrides={"composure": unsure})
    assert result.validation.competencies["composure"].score is None
    missing = copy.deepcopy(raw["composure"])
    missing["judgement"]["score"] = None
    result = evaluate_demo(overrides={"composure": missing})
    assert any(i.code == "score_missing" for i in result.validation.issues)


# ------------------------------------------------------------------------------------------- V4 required evidence


def test_v4_required_evidence_rules() -> None:
    result = evaluate_demo()
    assert all(v is None for v in required_evidence(result.inputs, result.drill_a, result.drill_b).values())
    incomplete = evaluate_demo("incomplete")
    problems = required_evidence(incomplete.inputs, incomplete.drill_a, incomplete.drill_b)
    assert problems["structured_answer"] and problems["trainability"]
    assert problems["communication"] is None and problems["technical"] is None
    assert incomplete.validation.competencies["structured_answer"].status == "insufficient"


def test_v4_overrides_a_confident_model() -> None:
    result = evaluate_demo()
    inputs = result.inputs
    # Remove the speech capture: structured answer can no longer be scored, whatever the model said.
    speech = inputs.activity_answer("topic_card")
    inputs.answers.remove(speech)
    problems = required_evidence(inputs, result.drill_a, result.drill_b)
    assert problems["structured_answer"].startswith("needs a topic-card answer")


# ------------------------------------------------------------------------------------------- V5 mandatory coverage


def test_v5_mandatory_findings_skip_designed_cases() -> None:
    stage = SimpleNamespace(
        stage_key="resume",
        stage_number=2,
        status="incomplete",
        items=[
            {"item_key": "resume.C1.ownership", "mandatory": True, "status": "answered"},
            {
                "item_key": "resume.C2.stress",
                "mandatory": True,
                "status": "skipped",
                "skip_reason": "not_hands_on",
            },
            {"item_key": "mandatory.mq1", "mandatory": True, "status": "prefilled"},
            {"item_key": "mandatory.mq2.pm", "mandatory": True, "status": "not_applicable"},
            {"item_key": "close.closing", "mandatory": True, "status": "asked"},
            {"item_key": "resume.C2.mechanics", "mandatory": True, "status": "declined"},
            {
                "item_key": "resume.C1.stress",
                "mandatory": True,
                "status": "skipped",
                "skip_reason": "time_budget",
            },
            {"item_key": "greeting.followup", "mandatory": False, "status": "not_reached"},
        ],
        extra={},
    )
    inputs = SimpleNamespace(stages={"resume": stage})
    findings = mandatory_findings(inputs)  # type: ignore[arg-type]
    assert [f.kind for f in findings] == [
        "stage_incomplete",
        "mandatory_item_missing",
        "mandatory_item_missing",
    ]
    assert {f.key for f in findings} == {"stage:resume", "item:resume.C2.mechanics", "item:resume.C1.stress"}


# ------------------------------------------------------------------------------------------- V6 consistency


def test_v6_structured_five_without_four_covered_points_is_lowered() -> None:
    raw = _raw_outputs()
    structured = copy.deepcopy(raw["structured"])
    structured["judgement"]["score"] = 5
    structured["bullets"][3]["status"] = "partial"
    result = evaluate_demo(overrides={"structured": structured})
    assert result.validation.competencies["structured_answer"].score == Decimal(4)
    assert any(i.code == "score_lowered" for i in result.validation.issues)


def test_v6_structured_four_with_unverified_points_is_flagged() -> None:
    raw = _raw_outputs()
    structured = copy.deepcopy(raw["structured"])
    for bullet in structured["bullets"][1:]:
        bullet["quote"] = "words the candidate never said"
    result = evaluate_demo(overrides={"structured": structured})
    disagreements = [f for f in result.validation.findings if f.kind == "evaluator_disagreement"]
    assert disagreements and disagreements[0].competency_keys == ("structured_answer",)
    assert result.validation.competencies["structured_answer"].score == Decimal(4)  # unchanged


def test_v6_technical_high_with_unsupported_claims_and_score_gap() -> None:
    raw = _raw_outputs()
    resume = copy.deepcopy(raw["resume_evidence"])
    for claim in resume["claims"]:
        claim["verdict"] = "unsupported"
    communication = copy.deepcopy(raw["communication"])
    communication["judgement"]["score"] = 1
    communication["judgement"]["evidence"] = [
        dict(e, supports="negative") for e in communication["judgement"]["evidence"]
    ]
    result = evaluate_demo(overrides={"resume_evidence": resume, "communication": communication})
    keys = {f.key for f in result.validation.findings if f.kind == "evaluator_disagreement"}
    assert keys == {"v6:technical_claims", "v6:communication_structured"}
    assert result.validation.competencies["technical"].score == Decimal(4)


# ------------------------------------------------------------------------------------------- V7 arithmetic


def test_v7_arithmetic_check() -> None:
    result = evaluate_demo()
    rows = [{"competency_key": k, "score": v} for k, v in result.validation.scores().items()]
    summary = check_arithmetic(rows, total=Decimal("4.130"), partial_total=None, band="accept_with_notes")
    assert summary.total == Decimal("4.130")
    with pytest.raises(ArithmeticMismatch):
        check_arithmetic(rows, total=Decimal("4.2"), partial_total=None, band="accept_with_notes")
    with pytest.raises(ArithmeticMismatch):
        check_arithmetic(rows, total=Decimal("4.130"), partial_total=None, band="strong_accept")
    with pytest.raises(ArithmeticMismatch):
        check_arithmetic(rows[:-1], total=None, partial_total=None, band=None)
    partial_rows = [dict(r, score=None) if r["competency_key"] == "composure" else r for r in rows]
    partial = check_arithmetic(
        partial_rows, total=None, partial_total=Decimal("4.1444"), band="insufficient_evidence"
    )
    assert partial.partial_total is not None


# ------------------------------------------------------------------------------------------- V8 privacy


def test_v8_prompt_privacy_guard() -> None:
    safe = [
        {"role": "system", "content": "Evaluate."},
        {"role": "user", "content": "Q1: tell me about yourself"},
    ]
    assert_prompt_safe(safe)
    with pytest.raises(PromptPrivacyError):
        assert_prompt_safe(
            [{"role": "user", "content": "Are you legally authorised to work in Canada for this role?"}]
        )
    with pytest.raises(PromptPrivacyError):
        assert_prompt_safe([{"role": "user", "content": 'form: {"needs_sponsorship": true}'}])
    original = forbidden_kit_texts()[0]
    with pytest.raises(PromptPrivacyError):
        assert_prompt_safe([{"role": "system", "content": f"Rubric: {original}"}])
    # The candidate's own words are data: a volunteered remark never blocks the evaluation.
    spoken = "<interview_answers>\nA3: I am legally authorised to work here already.\n</interview_answers>"
    assert_prompt_safe([{"role": "user", "content": spoken}])
