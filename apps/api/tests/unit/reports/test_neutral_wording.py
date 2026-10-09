"""Neutral wording of report text (app/services/reports/wording.py).

Examples come from stored evidence and contradiction text of the dev database and the regenerated
samples/reports (legacy text written before the rules existed: "Current answer ignores these specific
triggers entirely", "failing to parse logs", "a failure to engage").
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.services.reports.assembly import candidate_view
from app.services.reports.narratives import fallback_recruiter_narrative
from app.services.reports.rendering import render_candidate_html, render_recruiter_html
from app.services.reports.wording import (
    candidate_voice,
    gerund,
    neutral_candidate_document,
    neutral_recruiter_document,
    neutral_wording,
)

REWRITES = [
    ("The candidate is failing to engage with the follow-up.", "The candidate is not engaging with the follow-up."),
    (
        "The candidate submitted a stub that returned an empty string, failing to parse logs or count errors.",
        "The candidate submitted a stub that returned an empty string, not parsing logs or counting errors.",
    ),
    ("The solution fails to parse the log format.", "The solution does not parse the log format."),
    ("They fail to mention rollbacks.", "They do not mention rollbacks."),
    ("The plan may fail to scale.", "The plan may not scale."),
    (
        "This indicates a failure to engage with the technical question.",
        "This indicates a gap in engaging with the technical question.",
    ),
    (
        "There was also a notable failure to address a direct question.",
        "There was also a notable gap in addressing a direct question.",
    ),
    ("Failure to mention rollback metrics.", "Gap in mentioning rollback metrics."),
    ("Failed to identify or explain HPA/Karpenter triggers.", "Did not identify or explain HPA/Karpenter triggers."),
    ("The candidate completely failed to answer.", "The candidate did not answer."),
    ("The candidate was unable to explain IRSA.", "The candidate did not explain IRSA."),
    ("She seemed unwilling to estimate the cost.", "She did not estimate the cost."),
    ("Refusing to give numbers, the candidate moved on.", "Not giving numbers, the candidate moved on."),
    ("The candidate neglected to mention alerting.", "The candidate did not mention alerting."),
    ("The candidate struggles with Terraform state.", "The candidate has difficulty with Terraform state."),
    ("Struggling to explain the rollback, they paused.", "Not fully explaining the rollback, they paused."),
    (
        "Current answer ignores these specific triggers entirely, providing only cost figures.",
        "Current answer does not address these specific triggers at all, providing only cost figures.",
    ),
    ("Candidate ignored the direct question about the company name.", "Candidate did not address the direct question about the company name."),
    ("Ignoring the question, the candidate described Argo CD.", "Not addressing the question, the candidate described Argo CD."),
    ("She admitted to not knowing the answer.", "She acknowledged not knowing the answer."),
    ("Admitted no HPA tuning.", "Said no HPA tuning."),
    ("He dodged the direct question about cost.", "He did not directly answer the direct question about cost."),
    ("The candidate deflected to a different project.", "The candidate redirected to a different project."),
    ("Answered evasively; the answer was evasive.", "Answered indirectly; the answer was indirect."),
]  # fmt: skip


@pytest.mark.parametrize(("text", "expected"), REWRITES)
def test_judgemental_wording_becomes_neutral(text: str, expected: str) -> None:
    out, count = neutral_wording(text)
    assert out == expected and count >= 1
    assert neutral_wording(out) == (out, 0)  # idempotent


@pytest.mark.parametrize(
    "text",
    [
        "The final code `return ''` ignores all input.",  # a statement about code, not the person
        "The candidate's code ignores the input entirely.",
        "The candidate failed test t5 (hidden: malformed lines ignored).",
        "The admission webhook admits pods that pass the policy.",
        "The alert was ignored by the pager.",  # passive
        "The pod was unable to pull the image.",
        "The candidate said 'I failed to plan the cutover' about the first migration.",  # quoted words stay
        'The resume says "admitted to the bar"; the answer covered EKS.',
        "Failing tests were fixed before the release.",
        "Deployments failed twice during the canary.",
    ],
)
def test_technical_passive_and_quoted_text_is_left_alone(text: str) -> None:
    assert neutral_wording(text) == (text, 0)


def test_system_failures_keep_their_meaning() -> None:
    out, count = neutral_wording("The pod failed to start because the node fails to pull images.")
    assert out == "The pod did not start because the node does not pull images." and count == 2


def test_empty_code_strings_and_apostrophes_are_not_quotations() -> None:
    """Stored coding evidence of the sample session: ``''`` (an empty string in code) once made the
    quote protection swallow the sentence, so "fails to count errors" stayed."""
    text = (
        "The final code `return ''` ignores all input and fails to count errors. "
        "Test t1 (Example 1) expects 'payments-api' but gets ''."
    )
    assert neutral_wording(text) == (text.replace("fails to count", "does not count"), 1)
    assert neutral_wording(
        "Answer: 'I didn't own the SCPs, I failed to get access' and the candidate failed to explain."
    ) == (
        "Answer: 'I didn't own the SCPs, I failed to get access' and the candidate did not explain.",
        1,
    )
    assert neutral_wording("The candidate's answer failed to cover the teams' rollback plan.")[0] == (
        "The candidate's answer did not cover the teams' rollback plan."
    )


@pytest.mark.parametrize(
    ("verb", "form"),
    [("explain", "explaining"), ("parse", "parsing"), ("run", "running"), ("admit", "admitting"), ("tie", "tying"),
     ("see", "seeing"), ("quantify", "quantifying"), ("visit", "visiting"), ("meet", "meeting"), ("fix", "fixing"),
     ("Answer", "Answering")],
)  # fmt: skip
def test_gerund(verb: str, form: str) -> None:
    assert gerund(verb) == form


def test_candidate_voice_is_neutral_and_second_person() -> None:
    assert candidate_voice("The candidate failed to handle ties; the candidate's loop ignores case.") == (
        "You did not handle ties; your loop ignores case."
    )
    assert candidate_voice(None) is None and candidate_voice(3) == 3


# ------------------------------------------------------------------ documents


def _legacy(document: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(document)
    doc["evidence"][1]["evidence"] = "Candidate failed to describe policy checks."
    doc["evidence"][1]["quote"] = "we failed to add policy checks"
    doc["contradictions"][0]["description"] = (
        "Candidate previously stated: 'Karpenter handled node autoscaling.' / earlier: "
        "Current answer ignores these specific triggers entirely."
    )
    doc["claims"][0]["note"] = "Candidate admitted the 40% figure was an estimate."
    q2 = doc["questions"][1]
    q2["reason"] = "The candidate failed to give a baseline."
    q2["answers"][0]["transcript_text"] = "Honestly I failed to measure it."
    q2["answers"][0]["evaluation"]["summary"] = "The candidate struggled to explain guardrails."
    q2["answers"][0]["evaluation"]["missing_details"] = ["Failure to mention plan review."]
    doc["coding"][0]["evaluation"]["correctness_summary"] = (
        "The candidate submitted a stub, failing to parse logs or count errors."
    )
    doc["coding"][0]["evaluation"]["weaknesses"] = ["Fails to handle ties."]
    doc["coding"][0]["evaluation"]["process_observations"] = ["Struggled to start the parser."]
    doc["coding"][0]["evaluation"]["evidence"] = [{"evidence": "The solution fails to parse the log format."}]
    q2["answers"][0]["evaluation"]["contradictions"] = [
        {
            "statement": "I failed to own the SCPs",
            "conflicts_with": "Current answer ignores the SCP question entirely.",
            "severity": "low",
        }
    ]
    doc["interviewer_notes"] = "Candidate failed to show up last time; be patient."  # human-written
    doc["integrity"]["events"][0]["review_note"] = "Reviewer: candidate ignored the prompt."  # human-written
    return doc


def test_recruiter_document_is_rewritten_on_a_copy(document: dict[str, Any]) -> None:
    legacy = _legacy(document)
    before = copy.deepcopy(legacy)
    out = neutral_recruiter_document(legacy)
    assert legacy == before  # the stored content is never modified
    assert out["evidence"][1]["evidence"] == "Candidate did not describe policy checks."
    assert out["evidence"][1]["quote"] == "we failed to add policy checks"  # the candidate's words
    assert out["contradictions"][0]["description"].endswith(
        "Current answer does not address these specific triggers at all."
    )
    assert "'Karpenter handled node autoscaling.'" in out["contradictions"][0]["description"]
    assert out["claims"][0]["note"] == "Candidate said the 40% figure was an estimate."
    q2 = out["questions"][1]
    assert q2["reason"] == "The candidate did not give a baseline."
    assert q2["answers"][0]["transcript_text"] == "Honestly I failed to measure it."
    assert q2["answers"][0]["evaluation"]["summary"] == "The candidate did not fully explain guardrails."
    assert q2["answers"][0]["evaluation"]["missing_details"] == ["Gap in mentioning plan review."]
    ev = out["coding"][0]["evaluation"]
    assert ev["correctness_summary"] == "The candidate submitted a stub, not parsing logs or counting errors."
    assert ev["weaknesses"] == ["Does not handle ties."]
    assert ev["process_observations"] == ["Did not fully start the parser."]
    assert ev["evidence"] == [{"evidence": "The solution does not parse the log format."}]
    finding = q2["answers"][0]["evaluation"]["contradictions"][0]
    assert finding["statement"] == "I failed to own the SCPs"  # the candidate's own words
    assert finding["conflicts_with"] == "Current answer does not address the SCP question at all."
    assert out["interviewer_notes"] == legacy["interviewer_notes"]
    assert out["integrity"]["events"][0]["review_note"] == legacy["integrity"]["events"][0]["review_note"]
    assert neutral_recruiter_document(out) == out


def test_candidate_content_uses_neutral_second_person_text(document: dict[str, Any]) -> None:
    legacy = neutral_recruiter_document(_legacy(document))
    narrative = {
        "overall_summary": "Thanks.",
        "technical_strengths": [],
        "areas_to_improve": [{"title": "Terraform", "detail": "d", "learning_guidance": "g", "evidence_ids": [legacy["evidence"][1]["id"]]}],
    }  # fmt: skip
    content = candidate_view(legacy, narrative)
    coding = content["coding"][0]
    assert coding["correctness_summary"] == "You submitted a stub, not parsing logs or counting errors."
    assert coding["weaknesses"] == ["Does not handle ties."]
    feedback = content["questions"][1]["feedback"]
    assert feedback["missing_details"] == ["Gap in mentioning plan review."]
    assert content["evidence"][0]["evidence"] == "Candidate did not describe policy checks."
    stored = copy.deepcopy(content)
    assert neutral_candidate_document(content) == content and content == stored


def test_rendering_neutralises_stored_legacy_content(document: dict[str, Any]) -> None:
    from app.services.reports.narratives import fallback_candidate_narrative

    legacy = _legacy(document)  # e.g. a report version stored before the rules existed
    legacy["narrative"] = fallback_recruiter_narrative(legacy)
    legacy["narrative"]["executive_summary"] = "The candidate failed to give numbers."
    before = copy.deepcopy(legacy)
    html = render_recruiter_html(
        legacy, {"version": 1, "report_id": "r", "generated_at": "2026-10-08T00:00:00Z"}
    )
    assert legacy == before
    assert "does not address these specific triggers at all" in html
    assert "ignores these specific triggers" not in html and "failed to give numbers" not in html
    assert "The candidate did not give numbers." in html
    cand = candidate_view(legacy, fallback_candidate_narrative(legacy))
    cand["coding"][0]["correctness_summary"] = "The candidate submitted a stub, failing to parse logs."
    html = render_candidate_html(
        cand, {"version": 1, "report_id": "r", "generated_at": "2026-10-08T00:00:00Z"}
    )
    assert "You submitted a stub, not parsing logs." in html and "failing to" not in html


def test_fallback_recruiter_narrative_scopes_sufficiency_and_lists_open_areas(
    document: dict[str, Any],
) -> None:
    doc = copy.deepcopy(document)
    for comp in doc["scores"]["competencies"]:
        comp["sufficient_evidence"] = True
    doc["recommendation"]["factors"] = {"round_scope": 0.4, "min_confidence_target": 0.48}
    doc["scores"]["overall"]["confidence"] = 0.5
    rec = fallback_recruiter_narrative(doc)
    assert all("enough evidence for this round's scope" in f["summary"] for f in rec["competency_findings"])
    followups = rec["areas_for_human_followup"]
    # Inconsistencies first, then areas of sufficient competencies a human can still probe.
    assert followups[0].startswith("Discuss: ")
    assert "Follow up on Kubernetes: upgrades" in followups
    assert "Follow up on Terraform: change safety and module design" in followups
    assert rec["risks"] == ["1 integrity signal(s) await human review (observations only)."]  # 0.5 >= 0.48
    doc["recommendation"]["factors"] = {}
    full = fallback_recruiter_narrative(doc)
    assert all("enough evidence to judge" in f["summary"] for f in full["competency_findings"])
    assert full["risks"][0].startswith("Overall confidence is below the round's minimum")  # 0.5 < 0.6
