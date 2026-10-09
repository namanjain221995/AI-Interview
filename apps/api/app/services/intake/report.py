"""Intake report assembly (design contract section 9.1, shape 15.11).

``assemble_intake_report(data)`` builds the report content deterministically from the stored evaluation, its seven
competency rows, the activity rows, stage results and Q&A, the onboarding answers (never eligibility), the
integrity counts, the training plan and the human decisions at generation time. The content is stored as a
``reports`` row (``type = recruiter``, ``content.kind = "intake"``) by :mod:`app.services.intake.pipeline`.

Rules: decimals are strings; every model-written string goes through
:func:`app.services.reports.wording.neutral` (stored evaluator outputs are never rewritten); MQ1 answers are never
part of the content (the work-authorisation review item is reduced to a neutral note and its resolution notes are
dropped); the AI band is labelled as a recommendation that a human reviewer decides.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.schemas.intake import PRIMARY_LANGUAGE_LABELS, TARGET_ROLE_LABELS
from app.services.intake.evidence import EvaluationInputs
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.reading import METHOD as READING_METHOD
from app.services.intake.review_items import report_safe_items
from app.services.intake.scoring import (
    BAND_LABELS,
    INTAKE_COMPETENCIES,
    NORMALIZED_LABEL,
    DecisionBand,
)
from app.services.reports.wording import neutral

SCHEMA_VERSION = 1
SCORING_VERSION = "intake-scoring-1"
RECOMMENDATION_NOTE = "This is an AI recommendation. A human reviewer makes the decision."
INTEGRITY_NOTE = "Signals are observations, not determinations."
ELIGIBILITY_NOTE = (
    "Work-authorisation answers are reviewed by people only; they are not part of this report or any score."
)
FORMULA = "0.4 × Drill A + 0.6 × Drill B"
EXCERPT_CHARS = 400
TRANSCRIPT_CHARS = 6000
OUTCOME_LABELS = {
    "accept": "Accept",
    "accept_with_notes": "Accept with notes",
    "second_interview": "Second interview",
    "reject": "Reject",
    "request_evidence": "Request evidence",
}


def dec(value: Any) -> str | None:
    """Decimal (or number) as a plain string; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def excerpt(text: str | None, limit: int = EXCERPT_CHARS) -> str | None:
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass(slots=True)
class ReportInputs:
    """Everything the report reads (loaded by :func:`app.services.intake.pipeline.load_report_inputs`)."""

    session: Any
    intake: Any
    candidate_name: str | None
    job_title: str | None
    evaluation: Any
    competency_rows: Sequence[Any]
    inputs: EvaluationInputs
    reading_output: Mapping[str, Any] | None = (
        None  # reading_metrics evaluator run output (full metrics + events)
    )
    profile: Mapping[str, Any] = field(default_factory=dict)  # {headline, summary, experience_years}
    integrity: Sequence[Mapping[str, Any]] = ()  # [{type, title, count, first_at, review_status}]
    integrity_unreviewed: int = 0
    decisions: Sequence[Mapping[str, Any]] = ()  # IntakeDecisionOut dicts, newest first
    training_plan: Mapping[str, Any] | None = None
    generated_at: datetime | None = None


# ============================================================================================== helpers


def _answer_excerpt(inputs: EvaluationInputs, answer_id: Any) -> str | None:
    if answer_id is None:
        return None
    wanted = str(answer_id)
    for answer in inputs.answers:
        if answer.id is not None and str(answer.id) == wanted:
            return excerpt(answer.text)
    return None


def _item_exchanges(inputs: EvaluationInputs, item_key: str) -> list[dict[str, Any]]:
    out = []
    for question in sorted(inputs.questions, key=lambda q: q.sequence):
        if question.item_key != item_key:
            continue
        answers = inputs.answers_for(question.id)
        out.append(
            {
                "question_id": str(question.id),
                "question": question.text,
                "answer_id": str(answers[0].id) if answers and answers[0].id else None,
                "answer_excerpt": excerpt(answers[0].text) if answers else None,
            }
        )
    return out


def _target_role(onboarding: Mapping[str, Any], intake: Any) -> str | None:
    key = onboarding.get("target_role") or getattr(intake, "target_role", None)
    if not key:
        return None
    if key == "other":
        return onboarding.get("target_role_other") or TARGET_ROLE_LABELS["other"]
    return TARGET_ROLE_LABELS.get(key, str(key))


def _decision_for_report(decision: Mapping[str, Any], wa_item_ids: set[str]) -> dict[str, Any]:
    out = copy.deepcopy(dict(decision))
    resolutions = []
    for entry in out.get("review_items_resolution") or []:
        entry = dict(entry)
        if entry.get("item_id") in wa_item_ids:
            entry["note"] = None  # never carry MQ1 details into a report
        resolutions.append(entry)
    out["review_items_resolution"] = resolutions
    return out


# ============================================================================================== sections


def _section_a(data: ReportInputs, kit: IntakeKit) -> dict[str, Any]:
    onboarding = dict(getattr(data.intake, "onboarding", None) or {})
    stages = []
    for stage in kit.stages:
        record = data.inputs.stages.get(stage.key)
        stages.append(
            {"key": stage.key, "title": stage.title, "status": record.status if record else "pending"}
        )
    return {
        "candidate_name": data.candidate_name or onboarding.get("full_name"),
        "target_role": _target_role(onboarding, data.intake),
        "interview_date": _iso(getattr(data.session, "started_at", None)),
        "duration_s": float(getattr(data.session, "elapsed_seconds", 0.0) or 0.0),
        "resume_summary": excerpt(data.profile.get("summary") or data.profile.get("headline"), 600),
        "technical_background": excerpt(onboarding.get("technical_background"), 1500),
        "years_hands_on": onboarding.get("years_hands_on"),
        "resume_experience_years": data.profile.get("experience_years"),
        "completed_stages": stages,
    }


def _section_b(data: ReportInputs, kit: IntakeKit) -> dict[str, Any]:
    evaluation = data.evaluation
    summary = evaluation.summary or {}
    band = evaluation.band or DecisionBand.INSUFFICIENT_EVIDENCE.value
    try:
        guidance = kit.band(band).guidance
    except KeyError:
        guidance = kit.decision_guide.insufficient_evidence.description
    guidance = guidance[:1].upper() + guidance[1:] if guidance else guidance
    latest = data.decisions[0] if data.decisions else None
    partial_label = None
    if evaluation.partial_total is not None:
        scored = [r for r in data.competency_rows if r.status == "scored"]
        weight = sum((Decimal(str(r.weight)) for r in scored), Decimal(0))
        partial_label = (
            f"Partial total re-normalised over {len(scored)} of {len(data.competency_rows)} competencies "
            f"({weight * 100:.0f}% of the weight); not a decision band"
        )
    return {
        "weighted_total": dec(evaluation.weighted_total),
        "normalized_100": dec(evaluation.normalized_100),
        "normalized_label": NORMALIZED_LABEL,
        "band": band,
        "band_label": BAND_LABELS.get(DecisionBand(band), band),
        "band_guidance": guidance,
        "recommendation_note": RECOMMENDATION_NOTE,
        "human_review_status": "decided" if latest else "pending",
        "latest_decision": latest,
        "evidence_sufficiency": evaluation.evidence_sufficiency,
        "partial_total": dec(evaluation.partial_total),
        "partial_label": partial_label,
        "strengths": [neutral(s) for s in summary.get("strengths", [])][:5],
        "improvements": [neutral(s) for s in summary.get("improvements", [])][:5],
    }


def _section_c(data: ReportInputs) -> list[dict[str, Any]]:
    rows = {r.competency_key: r for r in data.competency_rows}
    out = []
    for competency in INTAKE_COMPETENCIES:
        row = rows.get(competency.key)
        if row is None:
            continue
        observations = [neutral(s) for s in (row.strengths or [])]
        if row.status != "scored":
            observations += [f"Missing evidence: {neutral(m)}" for m in (row.missing_evidence or [])]
        out.append(
            {
                "key": competency.key,
                "label": competency.label,
                "weight": dec(row.weight),
                "score": dec(row.score),
                "weighted": dec(row.weighted),
                "status": row.status,
                "observations": observations,
                "explanation": neutral(row.reasoning or ""),
                "improvements": [neutral(s) for s in (row.improvements or [])],
                "evidence": list(row.evidence or []),
                "sub_scores": dict(row.sub_scores or {}),
            }
        )
    return out


def _section_d(data: ReportInputs) -> list[dict[str, Any]]:
    summary = data.evaluation.summary or {}
    verdicts = {c.get("claim_ref"): c for c in summary.get("claims", [])}
    out = []
    for claim in data.inputs.claims:
        ref = claim["ref"]
        verdict = verdicts.get(ref, {})
        questions = [q for q in sorted(data.inputs.questions, key=lambda q: q.sequence) if q.claim_ref == ref]
        responses = []
        for question in questions:
            for answer in data.inputs.answers_for(question.id):
                responses.append(
                    {"answer_id": str(answer.id) if answer.id else None, "excerpt": excerpt(answer.text)}
                )
        out.append(
            {
                "claim_ref": ref,
                "claim": claim.get("text"),
                "probe": claim.get("probe"),
                "source": claim.get("source"),
                "questions": [{"rung": q.rung, "question_id": str(q.id), "text": q.text} for q in questions],
                "responses": responses,
                "verdict": verdict.get("verdict") or "not_assessed",
                "note": neutral(verdict.get("note")) if verdict.get("note") else None,
                "supporting_evidence": list(verdict.get("evidence") or []),
                "unresolved": [neutral(u) for u in verdict.get("unresolved", [])],
            }
        )
    return out


def _section_e(data: ReportInputs, kit: IntakeKit) -> dict[str, Any]:
    onboarding = dict(getattr(data.intake, "onboarding", None) or {})
    summary = data.evaluation.summary or {}
    verification = summary.get("verification") or {}
    rows = {r.competency_key: r for r in data.competency_rows}
    technical = rows.get("technical")
    language = data.inputs.language
    check = kit.language_check(language) if language else None
    language_label = (
        check.label
        if check
        else (
            PRIMARY_LANGUAGE_LABELS.get(onboarding.get("primary_language") or "", None)
            or onboarding.get("primary_language_other")
        )
    )
    items = []
    for key in ("mandatory.mq3.verification", "mandatory.mq3.verification_retry"):
        for exchange in _item_exchanges(data.inputs, key):
            first = key == "mandatory.mq3.verification"
            items.append(
                {
                    "question": exchange["question"],
                    "answer_excerpt": exchange["answer_excerpt"],
                    "quality": verification.get("quality") if first else None,
                    "matched_key_points": list(verification.get("matched_key_points") or []) if first else [],
                }
            )
    return {
        "language": language_label,
        "self_rating": onboarding.get("self_rating"),
        "scripting": onboarding.get("scripting"),
        "data_ml_libraries": onboarding.get("data_ml_libraries"),
        "verification": items,
        "rating_supported": summary.get("rating_supported") or "unknown",
        "technical_understanding": neutral(technical.reasoning) if technical is not None else None,
        "accuracy_notes": neutral(verification.get("note")) if verification.get("note") else None,
    }


def _section_f(data: ReportInputs, kit: IntakeKit) -> dict[str, Any]:
    summary = data.evaluation.summary or {}
    drill_a = summary.get("drill_a") or {}
    drill_b = summary.get("drill_b") or {}
    reading = data.inputs.activities.get("reading_aloud")
    relay = data.inputs.activities.get("read_relay")
    passage_a = kit.passage(reading.material_key) if reading and reading.material_key else None
    passage_b = kit.passage(relay.material_key) if relay and relay.material_key else None
    full = dict(data.reading_output or {})
    events = full.get("events") or drill_a.get("events") or []
    metrics = dict(drill_a.get("metrics") or {k: v for k, v in full.items() if k != "events"})
    metrics.pop("events", None)
    rows = {r.competency_key: r for r in data.competency_rows}
    reading_row = rows.get("technical_reading")
    capture = (reading.capture if reading else None) or {}
    duration = metrics.get("duration_seconds")
    if duration is None and capture.get("started_ms") is not None and capture.get("ended_ms") is not None:
        duration = (int(capture["ended_ms"]) - int(capture["started_ms"])) / 1000.0
    reveal = (relay.reveal if relay else None) or {}
    shown_ms = reveal.get("shown_ms")
    return {
        "drill_a": {
            "passage_key": passage_a.key if passage_a else None,
            "passage_title": passage_a.title if passage_a else None,
            "duration_s": duration,
            "transcript": excerpt(reading.transcript, TRANSCRIPT_CHARS) if reading else None,
            "transcript_source": reading.transcript_origin if reading and reading.transcript else None,
            "metrics": metrics,
            "events": list(events),
            "score": drill_a.get("score"),
            "basis": list(drill_a.get("basis") or []),
            "pronunciation": "not_assessed",
        },
        "drill_b": {
            "passage_key": passage_b.key if passage_b else None,
            "passage_title": passage_b.title if passage_b else None,
            "shown_seconds": round(shown_ms / 1000.0, 1) if isinstance(shown_ms, int | float) else None,
            "explanation": excerpt(relay.transcript, TRANSCRIPT_CHARS) if relay else None,
            "concepts": [
                {
                    "key": c.get("key"),
                    "text": c.get("text"),
                    "status": c.get("status"),
                    "quote": c.get("quote"),
                }
                for c in drill_b.get("concepts", [])
            ],
            "coverage": drill_b.get("coverage"),
            "paraphrase": drill_b.get("paraphrase"),
            "misstatements": list(drill_b.get("misstatements") or []),
            "followups": [
                {
                    "question": f.get("question"),
                    "answer_excerpt": f.get("answer_excerpt"),
                    "note": neutral(f.get("note")) if f.get("note") else None,
                }
                for f in drill_b.get("followups", [])
            ],
            "score": drill_b.get("score"),
            "basis": list(drill_b.get("basis") or []),
        },
        "technical_reading": {
            "score": dec(reading_row.score) if reading_row is not None else None,
            "formula": FORMULA,
        },
    }


def _section_g(data: ReportInputs, kit: IntakeKit) -> dict[str, Any]:
    summary = data.evaluation.summary or {}
    activity = data.inputs.activities.get("topic_card")
    card = kit.topic_card(activity.material_key) if activity and activity.material_key else None
    rows = {r.competency_key: r for r in data.competency_rows}
    structured = rows.get("structured_answer")
    bullets = []
    by_index = {b.get("index"): b for b in summary.get("bullets", [])}
    for index, text in enumerate(card.bullets if card else (), start=1):
        item = by_index.get(index, {})
        bullets.append(
            {
                "index": index,
                "text": text,
                "status": item.get("status", "missing"),
                "excerpt": item.get("quote"),
                "quote_verified": bool(item.get("quote_valid")),
            }
        )
    weakest = _item_exchanges(data.inputs, "topic.weakest")
    first = weakest[0] if weakest else {}
    return {
        "card_number": card.number if card else None,
        "card_title": card.title if card else None,
        "bullets": bullets,
        "flow": summary.get("flow"),
        "examples_used": summary.get("examples_used"),
        "gap_handling": summary.get("gap_handling"),
        "followup": {
            "question": first.get("question"),
            "answer_excerpt": first.get("answer_excerpt"),
            "analysis": neutral(summary.get("followup_analysis"))
            if summary.get("followup_analysis")
            else None,
        },
        "score": dec(structured.score) if structured is not None else None,
    }


def _section_h(data: ReportInputs) -> dict[str, Any]:
    summary = data.evaluation.summary or {}
    onboarding = dict(getattr(data.intake, "onboarding", None) or {})
    rows = {r.competency_key: r for r in data.competency_rows}
    trainability = rows.get("trainability")
    adaptability = rows.get("adaptability")
    readiness = []
    if onboarding.get("training_commitment"):
        readiness.append(f"Training commitment on the form: {onboarding['training_commitment']}")
    if onboarding.get("weekly_hours") is not None:
        readiness.append(f"weekly hours: {onboarding['weekly_hours']}")
    if onboarding.get("availability"):
        readiness.append(f"availability: {str(onboarding['availability']).replace('_', ' ')}")
    development = []
    for row in (trainability, adaptability):
        if row is not None:
            development += [neutral(i) for i in (row.improvements or [])]
    return {
        "learning_strategy": neutral(summary.get("learning_strategy"))
        if summary.get("learning_strategy")
        else None,
        "teaching_exercise": neutral(summary.get("teach_back")) if summary.get("teach_back") else None,
        "problem_solving": neutral(trainability.reasoning) if trainability is not None else None,
        "training_readiness": "; ".join(readiness) + "." if readiness else None,
        "ai_ml_openness": summary.get("ai_ml_openness") or "unknown",
        "development_areas": list(dict.fromkeys(development))[:6],
    }


def _section_i(data: ReportInputs) -> dict[str, Any]:
    return {
        "observations": [dict(o) for o in data.integrity],
        "unreviewed": int(data.integrity_unreviewed),
        "note": INTEGRITY_NOTE,
    }


def _section_k(data: ReportInputs) -> dict[str, Any]:
    decisions = list(data.decisions)  # already scrubbed by assemble_intake_report
    return {
        "review_items": report_safe_items(data.evaluation.review_items or []),
        "decisions": decisions,
        "status": "decided" if decisions else "pending",
    }


# ============================================================================================== assembly


def assemble_intake_report(data: ReportInputs, *, kit: IntakeKit | None = None) -> dict[str, Any]:
    """The ``IntakeReport`` content (contract 15.11) for one ready evaluation."""
    kit = kit or load_intake_kit()
    evaluation = data.evaluation
    wa_ids = {
        str(item.get("id"))
        for item in (evaluation.review_items or [])
        if item.get("kind") == "work_authorization_review"
    }
    data = replace(data, decisions=[_decision_for_report(d, wa_ids) for d in data.decisions])
    content = {
        "kind": "intake",
        "schema_version": SCHEMA_VERSION,
        "kit_version": evaluation.kit_version,
        "scoring_version": evaluation.scoring_version or SCORING_VERSION,
        "reading_method": READING_METHOD,
        "generated_at": _iso(data.generated_at),
        "session_id": str(data.session.id),
        "intake_id": str(data.intake.id) if data.intake is not None else None,
        "evaluation_id": str(evaluation.id),
        "evaluation_version": evaluation.version,
        "A_candidate_summary": _section_a(data, kit),
        "B_overall": _section_b(data, kit),
        "C_competencies": _section_c(data),
        "D_resume_verification": _section_d(data),
        "E_technical": _section_e(data, kit),
        "F_reading": _section_f(data, kit),
        "G_structured": _section_g(data, kit),
        "H_trainability": _section_h(data),
        "I_integrity": _section_i(data),
        "J_training_plan": dict(data.training_plan) if data.training_plan else None,
        "K_human_review": _section_k(data),
        "eligibility_note": ELIGIBILITY_NOTE,
    }
    return neutral_intake_document(content)


_TEXT_PATHS: tuple[tuple[str, ...], ...] = (
    ("B_overall", "strengths"),
    ("B_overall", "improvements"),
    ("E_technical", "technical_understanding"),
    ("E_technical", "accuracy_notes"),
    ("G_structured", "followup", "analysis"),
    ("H_trainability", "learning_strategy"),
    ("H_trainability", "teaching_exercise"),
    ("H_trainability", "problem_solving"),
    ("H_trainability", "development_areas"),
)


def _rewrite(value: Any) -> Any:
    if isinstance(value, str):
        return neutral(value)
    if isinstance(value, list):
        return [_rewrite(v) for v in value]
    return value


def neutral_intake_document(content: Mapping[str, Any]) -> dict[str, Any]:
    """A copy with neutral wording on every model-written field (idempotent; quotes are never rewritten)."""
    doc = copy.deepcopy(dict(content))
    for path in _TEXT_PATHS:
        node: Any = doc
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and path[-1] in node:
            node[path[-1]] = _rewrite(node[path[-1]])
    for competency in doc.get("C_competencies") or []:
        for key in ("observations", "explanation", "improvements"):
            if key in competency:
                competency[key] = _rewrite(competency[key])
    for claim in doc.get("D_resume_verification") or []:
        for key in ("note", "unresolved"):
            if key in claim:
                claim[key] = _rewrite(claim[key])
    for followup in ((doc.get("F_reading") or {}).get("drill_b") or {}).get("followups") or []:
        if "note" in followup:
            followup["note"] = _rewrite(followup["note"])
    plan = doc.get("J_training_plan")
    if isinstance(plan, dict):
        plan["summary"] = _rewrite(plan.get("summary"))
        for module in plan.get("modules") or []:
            for key in ("why", "objectives"):
                if key in module:
                    module[key] = _rewrite(module[key])
    return doc


def report_meta(report_id: uuid.UUID, version: int, generated_at: datetime) -> dict[str, Any]:
    return {"version": version, "report_id": str(report_id), "generated_at": generated_at.isoformat()}


__all__ = [
    "ELIGIBILITY_NOTE",
    "FORMULA",
    "INTEGRITY_NOTE",
    "OUTCOME_LABELS",
    "RECOMMENDATION_NOTE",
    "SCHEMA_VERSION",
    "SCORING_VERSION",
    "ReportInputs",
    "assemble_intake_report",
    "dec",
    "excerpt",
    "neutral_intake_document",
    "report_meta",
]
