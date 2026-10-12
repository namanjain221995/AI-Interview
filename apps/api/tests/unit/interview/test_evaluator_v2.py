"""Evaluator v2: evidence before the verdict, quotes, gated contradictions (app.schemas.ai.AnswerEvaluationV2)."""

from __future__ import annotations

from app.domain.enums import AnswerQuality, EvidenceSignal
from app.schemas.ai import AnswerEvaluationV2, ContradictionFinding, DimensionScores, EvidenceItem
from app.services.interview.evaluation import clean_evaluation, gate_contradictions, unquoted_confidence

ANSWER = "I ran canaries at five percent and rolled back automatically when the error rate doubled."


def v2(**overrides) -> AnswerEvaluationV2:
    base = {
        "evidence": [
            {
                "quote": "rolled back automatically when the error rate doubled",
                "competency_key": "kubernetes",
                "dimension": "rollout_safety",
                "signal": "positive",
                "evidence": "Automated rollback on an error-rate trigger.",
                "strength": 0.7,
                "confidence": 0.8,
            },
            {
                "quote": None,
                "competency_key": "kubernetes",
                "dimension": "observability",
                "signal": "negative",
                "evidence": "No mention of which metrics were watched besides errors.",
                "strength": 0.3,
                "confidence": 0.6,
            },
        ],
        "missing_details": ["latency metrics in the analysis"],
        "measurable_outcomes": ["five percent canary"],
        "unsupported_claims": [],
        "claim_verifications": [],
        "contradictions": [],
        "scores": {"correctness": 7, "depth": 6, "specificity": 7},
        "overall_quality": "adequate",
        "summary": "Specific canary and rollback approach; observability thin.",
        "suggested_followups": [],
        "evaluation_confidence": 0.7,
    }
    base.update(overrides)
    return AnswerEvaluationV2.model_validate(base)


def test_the_verdict_comes_after_the_evidence() -> None:
    keys = list(AnswerEvaluationV2.model_fields)
    assert keys[0] == "evidence" and keys.index("scores") < keys.index("overall_quality") < keys.index("summary")
    item_keys = list(AnswerEvaluationV2.model_fields["evidence"].annotation.__args__[0].model_fields)
    assert item_keys[0] == "quote"


def test_v2_converts_to_the_stored_evaluation() -> None:
    v1 = v2().to_v1()
    assert v1.overall_quality is AnswerQuality.ADEQUATE and v1.scores == DimensionScores(correctness=7, depth=6, specificity=7)
    assert [e.dimension for e in v1.evidence] == ["rollout_safety", "observability"]
    assert all(isinstance(e, EvidenceItem) for e in v1.evidence)
    assert v1.technical_evidence == ["Automated rollback on an error-rate trigger."]  # positive items only
    assert v1.missing_details == ["latency metrics in the analysis"]


def test_an_invented_quote_loses_its_weight() -> None:
    made_up = v2(
        evidence=[
            {
                "quote": "we used Flagger with a custom webhook",
                "competency_key": "kubernetes",
                "dimension": "tooling",
                "signal": "positive",
                "evidence": "Uses Flagger.",
                "strength": 0.8,
                "confidence": 0.9,
            }
        ]
    ).to_v1()
    cleaned = clean_evaluation(
        made_up, answer_text=ANSWER, round_keys=["kubernetes"], question_keys=["kubernetes"], known_claims=[]
    )
    assert cleaned.evidence[0].quote is None  # not in the answer
    capped = unquoted_confidence(cleaned)
    assert capped.evidence[0].confidence == 0.4
    kept = unquoted_confidence(
        clean_evaluation(v2().to_v1(), answer_text=ANSWER, round_keys=["kubernetes"], question_keys=["kubernetes"], known_claims=[])
    )
    assert kept.evidence[0].quote and kept.evidence[0].confidence == 0.8
    # An absence (negative, no quote by design) keeps its confidence.
    assert kept.evidence[1].confidence == 0.6 and kept.evidence[1].signal is EvidenceSignal.NEGATIVE


def test_contradictions_need_an_answer_with_content() -> None:
    conflict = [ContradictionFinding(statement="only 3 nodes in production", conflicts_with="resume: 120 nodes", severity="medium")]
    specific = v2(contradictions=conflict).to_v1()
    assert gate_contradictions(specific) == (specific, 0)  # short but specific: kept
    vague = v2(contradictions=conflict, overall_quality="vague").to_v1()
    gated, count = gate_contradictions(vague)
    assert count == 1 and gated.contradictions == []
