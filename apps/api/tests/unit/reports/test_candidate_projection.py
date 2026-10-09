"""Candidate projection: no reviewer-voice evaluator text and nothing about
resume-claim verification reaches the candidate report."""

from __future__ import annotations

import copy
import json
from typing import Any

from app.services.reports.assembly import candidate_safe, candidate_view
from tests.unit.reports.conftest import E1, E2, Q1, Q2


def _with_claim_verification(document: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(document)
    q2_eval = doc["questions"][1]["answers"][0]["evaluation"]
    q2_eval["summary"] = "The candidate completely failed to address the claimed migration."
    q2_eval["missing_details"] = ["policy checks", "Consistency with resume claims"]
    q2_eval["measurable_outcomes"] = ["Claimed 40% cost reduction", "Cut deploy time to 9 minutes"]
    q2_eval["technical_evidence"] = ["Uses remote state with locking"]
    doc["scores"]["competencies"][0]["unanswered_areas"] = [
        "upgrades",
        "Clear explanation of employment history",
        "Data consistency handling",
    ]
    doc["scores"]["rubric"].append(
        {"key": "resume_consistency", "label": "Resume Consistency", "weight": 10, "score": 40.0}
    )
    for item in doc["evidence"]:
        if item["id"] == E2:
            item["evidence"] = "Could not back up the resume claim about policy checks."
    return doc


def test_candidate_feedback_uses_the_narrative_note_not_the_evaluator_summary(document) -> None:
    doc = _with_claim_verification(document)
    narrative = {
        "technical_strengths": [{"title": "Autoscaling", "detail": "Clear.", "evidence_ids": [E1]}],
        "areas_to_improve": [{"title": "Guardrails", "detail": "Add checks.", "evidence_ids": [E2]}],
        "strong_answers": [{"question_id": Q1, "note": "You explained how both autoscalers interact."}],
        "incomplete_answers": [
            {"question_id": Q2, "note": "Your answer skipped the resume claim."},  # unsafe: dropped
        ],
    }
    view = candidate_view(doc, narrative)
    q1, q2 = view["questions"][0]["feedback"], view["questions"][1]["feedback"]
    assert q1["summary"] == "You explained how both autoscalers interact."
    assert q2["summary"] is None  # never "The candidate completely failed ..."
    assert q2["missing_details"] == ["policy checks"]
    assert q2["measurable_outcomes"] == ["Cut deploy time to 9 minutes"]
    assert q2["technical_evidence"] == ["Uses remote state with locking"]
    assert set(q1) == {
        "overall_quality",
        "summary",
        "missing_details",
        "measurable_outcomes",
        "technical_evidence",
    }
    assert [r["key"] for r in view["rubric"]] == [r["key"] for r in document["scores"]["rubric"]]
    kubernetes = next(c for c in view["competencies"] if c["key"] == doc["scores"]["competencies"][0]["key"])
    assert kubernetes["unanswered_areas"] == ["upgrades", "Data consistency handling"]
    assert {e["id"] for e in view["evidence"]} == {E1}  # E2 talks about the resume claim
    projected = {k: v for k, v in view.items() if k != "narrative"}  # the narrative is scrubbed upstream
    raw = json.dumps(projected).lower()
    for forbidden in ("resume", "claim", "employment history", "completely failed"):
        assert forbidden not in raw, forbidden
    assert view["questions"][1]["id"] == Q2


def test_candidate_safe_keeps_technical_consistency_topics() -> None:
    assert candidate_safe("Data consistency handling") and candidate_safe("eventual consistency")
    assert not candidate_safe("Consistency with resume claims")
    assert not candidate_safe("Résumé mismatch") and not candidate_safe("Contradicted earlier answer")
    assert not candidate_safe("") and not candidate_safe(None)


def test_contradiction_probe_keeps_no_evaluator_details(document) -> None:
    doc = copy.deepcopy(document)
    question = doc["questions"][1]
    question["action"] = "verify_contradiction"
    evaluation = question["answers"][0]["evaluation"]
    evaluation["missing_details"] = ["Clarification of the Acme vs. Bluepeak timeline.", "Context on roles."]
    evaluation["technical_evidence"] = ["Named both employers."]
    view = candidate_view(
        doc, {"incomplete_answers": [{"question_id": Q2, "note": "Add dates for each role."}]}
    )
    feedback = view["questions"][1]["feedback"]
    assert feedback == {
        "overall_quality": None,
        "summary": "Add dates for each role.",
        "missing_details": [],
        "measurable_outcomes": [],
        "technical_evidence": [],
    }
    assert not candidate_safe("Clarification of the employment discrepancy")
