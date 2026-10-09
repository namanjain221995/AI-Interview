"""Intake report assembly (INTAKE_INTERVIEW.md 9.1, shape 15.11): sections A-K, decimals as strings, partial total
labelled, never MQ1 answers, neutral wording on model-written text."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.intake.demo_content import demo_evaluator_outputs
from app.services.intake.evidence import assign_aliases
from app.services.intake.report import (
    ELIGIBILITY_NOTE,
    RECOMMENDATION_NOTE,
    ReportInputs,
    assemble_intake_report,
    neutral_intake_document,
)
from app.services.intake.training import deterministic_plan, select_modules

from .eval_fixtures import Evaluated, demo_inputs, evaluate_demo, evaluation_rows

SECTIONS = (
    "A_candidate_summary", "B_overall", "C_competencies", "D_resume_verification", "E_technical", "F_reading",
    "G_structured", "H_trainability", "I_integrity", "J_training_plan", "K_human_review",
)  # fmt: skip


def _report_inputs(evaluated: Evaluated, *, decisions=(), plan=None) -> ReportInputs:  # noqa: ANN001
    evaluation, rows = evaluation_rows(evaluated)
    intake = SimpleNamespace(
        id=uuid.uuid4(),
        onboarding=evaluated.rows.onboarding,
        eligibility=evaluated.rows.eligibility,  # present on the row; must never reach the report
        resume_review=None,
        target_role=None,
        settings={"work_authorization": {"enabled": True, "country": "the United States"}},
    )
    session = SimpleNamespace(**vars(evaluated.rows.session))
    return ReportInputs(
        session=session,
        intake=intake,
        candidate_name="Alex Rivera",
        job_title="AI/ML Training Program — Intake",
        evaluation=evaluation,
        competency_rows=rows,
        inputs=evaluated.inputs,
        reading_output=None,
        profile={
            "headline": "Data engineer",
            "summary": "Data engineer, logistics.",
            "experience_years": 3.5,
        },
        integrity=[
            {
                "type": "tab_hidden",
                "title": "Tab hidden",
                "count": 1,
                "first_at": None,
                "review_status": "unreviewed",
            }
        ],
        integrity_unreviewed=1,
        decisions=list(decisions),
        training_plan=plan,
        generated_at=datetime(2026, 10, 9, tzinfo=UTC),
    )


def test_sections_and_values() -> None:
    evaluated = evaluate_demo()
    plan = deterministic_plan(select_modules(evaluated.validation.scores()))
    content = assemble_intake_report(_report_inputs(evaluated, plan=plan))
    assert content["kind"] == "intake" and content["schema_version"] == 1
    assert all(section in content for section in SECTIONS)
    a, b = content["A_candidate_summary"], content["B_overall"]
    assert a["candidate_name"] == "Alex Rivera" and a["target_role"] == "AI/ML" and a["years_hands_on"] == 3.0
    assert [s["key"] for s in a["completed_stages"]] == [
        "greeting", "resume", "mandatory", "reading", "topic", "learning", "close",
    ]  # fmt: skip
    assert b["weighted_total"] == "4.130" and b["normalized_100"] == "82.600"
    assert b["band"] == "accept_with_notes" and b["recommendation_note"] == RECOMMENDATION_NOTE
    assert b["human_review_status"] == "pending" and b["partial_total"] is None
    comps = {c["key"]: c for c in content["C_competencies"]}
    assert comps["technical_reading"]["score"] == "4.4" and comps["technical_reading"]["weight"] == "0.20"
    assert comps["technical_reading"]["sub_scores"]["formula"] == "0.4 × Drill A + 0.6 × Drill B"
    d = {c["claim_ref"]: c for c in content["D_resume_verification"]}
    assert d["C1"]["verdict"] == "hands_on" and d["C2"]["verdict"] == "team_level"
    assert [q["rung"] for q in d["C1"]["questions"]] == ["ownership", "mechanics", "stress"]
    e = content["E_technical"]
    assert (
        e["language"] == "Python" and e["self_rating"] == 7 and e["verification"][0]["quality"] == "correct"
    )
    f = content["F_reading"]
    assert f["drill_a"]["passage_key"] == "B" and f["drill_a"]["pronunciation"] == "not_assessed"
    assert f["drill_a"]["score"] == 5 and f["drill_a"]["events"]
    assert (
        f["drill_b"]["passage_key"] == "C"
        and f["drill_b"]["shown_seconds"] == 30.0
        and f["drill_b"]["score"] == 4
    )
    assert {c["status"] for c in f["drill_b"]["concepts"]} == {"covered", "missing"}
    assert f["technical_reading"] == {"score": "4.4", "formula": "0.4 × Drill A + 0.6 × Drill B"}
    g = content["G_structured"]
    assert g["card_number"] == 2 and len(g["bullets"]) == 4 and g["score"] == "4"
    h = content["H_trainability"]
    assert (
        h["ai_ml_openness"] == "eager" and "Training commitment on the form: full" in h["training_readiness"]
    )
    assert content["I_integrity"]["unreviewed"] == 1
    assert content["J_training_plan"]["modules"][0]["key"] == "ai_ml_fundamentals"
    assert content["K_human_review"]["status"] == "pending"
    assert content["eligibility_note"] == ELIGIBILITY_NOTE


def test_no_eligibility_answers_anywhere() -> None:
    evaluated = evaluate_demo(
        eligibility={"country": "the United States", "authorized": False, "needs_sponsorship": True}
    )
    decision = {
        "id": str(uuid.uuid4()),
        "outcome": "second_interview",
        "outcome_label": "Second interview",
        "rationale": "Discuss the schedule in a second interview.",
        "reviewer": {"id": str(uuid.uuid4()), "full_name": "Rita Reviewer", "email": None},
        "ai_band_at_decision": "accept_with_notes",
        "ai_total_at_decision": "4.1300",
        "overrides_ai": True,
        "review_items_resolution": [
            {
                "item_id": item["id"],
                "resolution": "follow_up",
                "note": "needs sponsorship next year"
                if item["kind"] == "work_authorization_review"
                else "seen",
            }
            for item in evaluated.review_items
        ],
        "competency_notes": {},
        "requested_evidence": [],
        "review_decision_id": str(uuid.uuid4()),
        "evaluation_id": None,
        "created_at": "2026-10-09T10:00:00+00:00",
    }
    content = assemble_intake_report(_report_inputs(evaluated, decisions=[decision]))
    text = json.dumps(content, ensure_ascii=False)
    assert "United States" not in text and "needs_sponsorship" not in text and '"authorized"' not in text
    assert "needs sponsorship next year" not in text  # MQ1 resolution notes are dropped
    notes = [r["note"] for r in content["K_human_review"]["decisions"][0]["review_items_resolution"]]
    assert "seen" in notes  # other resolution notes are kept
    wa = next(
        i for i in content["K_human_review"]["review_items"] if i["kind"] == "work_authorization_review"
    )
    assert wa["severity"] == "info" and wa["evidence"] == []
    assert content["B_overall"]["human_review_status"] == "decided"
    assert content["K_human_review"]["decisions"][0]["overrides_ai"] is True


def test_partial_total_is_labelled_when_evidence_is_insufficient() -> None:
    content = assemble_intake_report(_report_inputs(evaluate_demo("incomplete")))
    b = content["B_overall"]
    assert (
        b["band"] == "insufficient_evidence" and b["weighted_total"] is None and b["normalized_100"] is None
    )
    assert b["partial_total"] is not None and "not a decision band" in b["partial_label"]
    assert content["G_structured"]["card_title"] is None
    statuses = {c["key"]: c["status"] for c in content["C_competencies"]}
    assert statuses["structured_answer"] == "insufficient" and statuses["communication"] == "scored"


def test_model_text_is_neutralised_but_quotes_are_kept() -> None:
    _rows, inputs = demo_inputs()
    assign_aliases(inputs)
    raw = demo_evaluator_outputs(inputs)
    communication = copy.deepcopy(raw["communication"])
    communication["judgement"]["reasoning"] = (
        "The candidate failed to give numbers and ignored the second part."
    )
    communication["judgement"]["improvements"] = ["Struggled to quantify results"]
    evaluated = evaluate_demo(overrides={"communication": communication})
    content = assemble_intake_report(_report_inputs(evaluated))
    comm = next(c for c in content["C_competencies"] if c["key"] == "communication")
    assert "failed to" not in comm["explanation"] and "did not" in comm["explanation"]
    assert all("Struggled" not in s for s in comm["improvements"])
    quotes = [e["quote"] for e in comm["evidence"]]
    assert "Our operations team used to build a daily report by hand in Excel" in quotes
    assert neutral_intake_document(content) == content  # idempotent
