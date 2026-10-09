"""Regression tests for the report-narrative tone fixes (app/services/reports/narratives.py and
prompts.py are owned by the interview-quality pass). Evidence: samples/reports/completed_report.json
generated from E2E session 6224b4b4 before the fix (third-person, judgemental candidate summary that
mentioned the resume-consistency probe; empty claims/contradictions/coding sections)."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

import pytest

from app.domain.enums import ModelTask, Recommendation
from app.schemas.ai import (
    CandidateReportNarrative,
    CompetencyFinding,
    EvidenceBackedPoint,
    ImprovementArea,
    RecruiterReportNarrative,
)
from app.services.reports.assembly import ReportData
from app.services.reports.narratives import (
    build_aliases,
    candidate_messages,
    fallback_candidate_narrative,
    fallback_recruiter_narrative,
    generate_narratives,
    neutral_wording,
    record_text,
    recruiter_messages,
    scrub_candidate_text,
    second_person,
    validate_candidate_narrative,
    validate_recruiter_narrative,
)
from tests.unit.reports.conftest import E1, E2, make_document

PROBE_Q = str(uuid.uuid4())
PROBE_E = str(uuid.uuid4())


def _document() -> dict[str, Any]:
    doc = make_document()
    # A resume-consistency probe and its (negative) evidence: recruiter-only.
    probe = copy.deepcopy(doc["questions"][1])
    probe.update(
        id=PROBE_Q,
        sequence=3,
        action="verify_contradiction",
        text="Earlier you mentioned Acme; the resume lists Bluepeak. Help me reconcile that?",
    )
    probe["answers"][0]["evaluation"]["summary"] = "Did not address the employment history discrepancy."
    doc["questions"].append(probe)
    doc["evidence"].append(
        {
            **doc["evidence"][1],
            "id": PROBE_E,
            "question_id": PROBE_Q,
            "evidence": "Did not reconcile the Acme vs Bluepeak employment history.",
        }
    )
    doc["scores"]["competencies"][1]["unanswered_areas"].append("resume consistency")
    doc["scores"]["competencies"].append(
        {
            **doc["scores"]["competencies"][1],
            "key": "ci_cd",
            "name": "CI/CD",
            "evidence_count": 0,
            "unanswered_areas": ["rollbacks"],
        }
    )
    return doc


def _data(doc: dict[str, Any]) -> ReportData:
    return ReportData(
        document=doc,
        evidence_ids={e["id"] for e in doc["evidence"]},
        question_ids={q["id"] for q in doc["questions"]},
        competency_keys={c["key"] for c in doc["scores"]["competencies"]},
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )


def test_candidate_record_drops_resume_consistency_probes_and_internal_numbers() -> None:
    doc = _document()
    aliases = build_aliases(doc)
    candidate = record_text(doc, aliases, audience="candidate")
    assert "Bluepeak" not in candidate and "employment history" not in candidate
    assert aliases.evidence_alias[PROBE_E] + " [" not in candidate
    assert "Bluepeak" in record_text(doc, aliases, audience="recruiter")  # the reviewer still sees it
    user = candidate_messages(doc, aliases)[1]["content"]
    assert "never quote these numbers" in user and "confidence" not in user.split("<interview_record>")[0]
    assert "CI/CD [ci_cd]" in user and "resume consistency" not in user.lower()


def test_candidate_prompt_sets_a_warm_second_person_voice() -> None:
    system = candidate_messages(_document(), build_aliases(_document()))[0]["content"]
    for phrase in ('as "you"', '"failed"', "the resume or its verification", "coding_feedback summarises"):
        assert phrase in system


def test_recruiter_prompt_fences_claims_and_inconsistencies_and_asks_for_neutral_wording() -> None:
    doc = _document()
    rec = recruiter_messages(doc, build_aliases(doc))
    assert "<derived_claims>" in rec[1]["content"] and "<derived_inconsistencies>" in rec[1]["content"]
    assert '"did not address"' in rec[0]["content"] and '"ignored"' in rec[0]["content"]


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        (
            "You explained Karpenter well. There was also a notable failure to address a direct question "
            "regarding employment history consistency.",
            "You explained Karpenter well.",
        ),
        ("There is a disconnect between their claimed senior-level experience and the answers.", ""),
        (
            "You moved on after the preceding question about resume consistency. Practise HPA.",
            "Practise HPA.",
        ),
        ("You described rollbacks with Argo Rollouts.", "You described rollbacks with Argo Rollouts."),
        ("Your overall score of 32 reflects gaps. Practise HPA.", "Practise HPA."),
        ("Kubernetes came out at 7.6 out of 10. You passed 2/10 tests.", "You passed 2/10 tests."),
    ],
)
def test_candidate_text_never_mentions_resume_verification(text: str, kept: str) -> None:
    assert scrub_candidate_text(text)[0] == kept


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The candidate demonstrated awareness of Karpenter.", "You demonstrated awareness of Karpenter."),
        ("The candidate explains HPA. The candidate has used Argo.", "You explain HPA. You have used Argo."),
        ("Notably, the candidate's answer covered PDBs.", "Notably, your answer covered PDBs."),
        ("The candidate is familiar with IRSA.", "You are familiar with IRSA."),
    ],
)
def test_third_person_candidate_text_becomes_second_person(text: str, expected: str) -> None:
    assert second_person(text)[0] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A question which the candidate ignored during the interview.", "did not address"),
        ("The candidate failed to explain the trigger.", "did not explain"),
        ("They admitted they had not used Terraform.", "said they had not used Terraform"),
        ("You struggled to connect the metrics to scaling.", "did not fully connect"),
    ],
)
def test_judgemental_verbs_are_rewritten_neutrally(text: str, expected: str) -> None:
    out, count = neutral_wording(text)
    assert expected in out and count == 1
    # Passive uses are left alone rather than rewritten into broken grammar.
    assert neutral_wording("The alert was ignored by the pager.")[1] == 0


def test_candidate_validation_applies_voice_and_neutral_wording() -> None:
    doc = _document()
    narrative = CandidateReportNarrative(
        overall_summary=(
            "The candidate demonstrated awareness of Kubernetes autoscaling. The candidate failed to explain "
            "Terraform guardrails. There was a discrepancy with the resume."
        ),
        technical_strengths=[
            EvidenceBackedPoint(title="Kubernetes", detail="Karpenter.", evidence_ids=["E1"])
        ],
        areas_to_improve=[
            ImprovementArea(
                title="ci_cd depth",
                detail="ci_cd rollbacks were not covered.",
                learning_guidance="Practise.",
                evidence_ids=["E2"],
            )
        ],  # fmt: skip
    )
    out, stats = validate_candidate_narrative(narrative, build_aliases(doc), _data(doc))
    assert out["overall_summary"] == (
        "You demonstrated awareness of Kubernetes autoscaling. You did not explain Terraform guardrails."
    )
    assert out["areas_to_improve"][0]["title"] == "CI/CD depth"
    assert out["areas_to_improve"][0]["detail"] == "CI/CD rollbacks were not covered."
    assert stats["scrubbed_sentences"] == 1 and stats["rewritten_phrases"] == 3


def test_recruiter_validation_rewrites_judgemental_verbs() -> None:
    doc = _document()
    narrative = RecruiterReportNarrative(
        executive_summary="The candidate ignored the ownership question and failed to give numbers.",
        competency_findings=[
            CompetencyFinding(
                competency_key="kubernetes", summary="Admitted no HPA tuning.", evidence_ids=["E1"]
            )
        ],
        recommendation_rationale="ci_cd was not assessed.",
        suggested_recommendation=Recommendation.HUMAN_REVIEW,
    )
    out, stats = validate_recruiter_narrative(narrative, build_aliases(doc), _data(doc))
    assert out["executive_summary"] == (
        "The candidate did not address the ownership question and did not give numbers."
    )
    assert out["competency_findings"][0]["summary"] == "Said no HPA tuning."  # sentence case kept
    assert out["recommendation_rationale"] == "CI/CD was not assessed."
    assert stats["rewritten_phrases"] == 3


def test_fallback_narratives_are_humanized_and_candidate_safe() -> None:
    doc = _document()
    cand = fallback_candidate_narrative(doc)
    text = json.dumps(cand)
    assert "/100" not in cand["overall_summary"] and "Thank you for interviewing" in cand["overall_summary"]
    assert "Missing:" not in text and "could go further on: policy checks" in text
    assert "resume" not in text.lower() and "Bluepeak" not in text
    assert all(h["question_id"] != PROBE_Q for h in cand["incomplete_answers"])
    rec = fallback_recruiter_narrative(doc)
    summaries = [f["summary"] for f in rec["competency_findings"]]
    assert summaries[0].startswith("7.6 out of 10 from 1 piece of evidence")
    assert "Not assessed in this interview" in summaries[-1]
    assert "required competencies have enough evidence" in rec["executive_summary"]
    assert "%" not in rec["executive_summary"]
    assert rec["claims_summary"].startswith("Resume claims discussed: 1 not yet verified")
    assert "passed" in rec["coding_summary"] and "hint" in rec["coding_summary"]


async def test_empty_text_sections_are_filled_from_the_deterministic_narrative(mock_ai) -> None:
    """Backend-polish finding: the model returned claims_summary "", contradictions_summary "",
    coding_summary null and candidate coding_feedback null although the session had content."""
    doc = _document()

    def respond(call: Any) -> Any:
        if issubclass(call.schema, CandidateReportNarrative):
            return CandidateReportNarrative(
                overall_summary="   ",
                technical_strengths=[EvidenceBackedPoint(title="K8s", detail="d", evidence_ids=["E1"])],
                areas_to_improve=[
                    ImprovementArea(title="T", detail="d", learning_guidance="g", evidence_ids=["E2"])
                ],
                coding_feedback=None,
            )
        return RecruiterReportNarrative(
            executive_summary="Exec.",
            competency_findings=[
                CompetencyFinding(competency_key="kubernetes", summary="s", evidence_ids=["E1"])
            ],
            claims_summary="",
            contradictions_summary="",
            coding_summary=None,
            areas_for_human_followup=["Probe Terraform guardrails."],
            recommendation_rationale="r",
            suggested_recommendation=Recommendation.HUMAN_REVIEW,
        )

    mock_ai.llm.set_default(ModelTask.REPORT_GENERATION, respond)
    outcome = await generate_narratives(_data(doc), allow_fallback=False)
    assert outcome.source == {"candidate": "model", "recruiter": "model"}
    assert outcome.stats["candidate"]["filled_text_from_deterministic"] == [
        "overall_summary",
        "coding_feedback",
    ]
    assert outcome.candidate["coding_feedback"] and outcome.candidate["overall_summary"].startswith(
        "Thank you"
    )
    assert outcome.stats["recruiter"]["filled_text_from_deterministic"] == [
        "claims_summary",
        "contradictions_summary",
        "coding_summary",
    ]
    assert outcome.recruiter["executive_summary"] == "Exec."
    assert outcome.candidate["technical_strengths"][0]["evidence_ids"] == [E1]
    assert outcome.candidate["areas_to_improve"][0]["evidence_ids"] == [E2]
