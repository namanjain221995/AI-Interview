"""Pure fixtures of the intake evaluation unit tests (T2): the demo interview as row-like objects, its evaluation
inputs with aliases, the canned evaluator outputs, and a fully validated evaluation (no database, no model)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.services.intake.demo_content import build_demo_rows, demo_evaluator_outputs
from app.services.intake.eval_schemas import EVALUATOR_SCHEMAS
from app.services.intake.evaluators import build_call
from app.services.intake.evidence import AliasMap, EvaluationInputs, assign_aliases, build_inputs
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.pipeline import build_summary
from app.services.intake.reading import RelayCoverage, relay_coverage
from app.services.intake.relay import DrillScore, drill_a_score, reading_metrics, relay_score
from app.services.intake.review_items import build_review_items
from app.services.intake.scoring import INTAKE_WEIGHTS
from app.services.intake.validator import (
    ValidationResult,
    concept_evidence,
    validate_cites,
    validate_evaluation,
)


@dataclass(slots=True)
class Evaluated:
    kit: IntakeKit
    rows: Any
    inputs: EvaluationInputs
    aliases: AliasMap
    outputs: dict[str, Any]
    allowed: dict[str, frozenset[str]]
    drill_a: DrillScore
    drill_b: DrillScore
    coverage: RelayCoverage | None
    validation: ValidationResult
    summary: dict[str, Any]
    review_items: list[dict[str, Any]]


def demo_inputs(
    variant: str = "complete", *, names: tuple[str, ...] = ("Alex Rivera",)
) -> tuple[Any, EvaluationInputs]:
    rows = build_demo_rows(variant=variant)
    inputs = build_inputs(
        session=rows.session,
        intake=SimpleNamespace(id=uuid.uuid4(), onboarding=rows.onboarding),
        questions=rows.questions,
        answers=rows.answers,
        activities=rows.activities,
        stages=rows.stages,
        onboarding_digest=rows.onboarding,
        names=names,
    )
    return rows, inputs


def evaluate_demo(
    variant: str = "complete",
    *,
    overrides: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
    eligibility: dict[str, Any] | None = None,
    eligibility_enabled: bool = True,
) -> Evaluated:
    """Run every pure step of the pipeline on the demo interview (``overrides`` replace evaluator outputs,
    ``drop`` simulates fallbacks)."""
    kit = load_intake_kit()
    rows, inputs = demo_inputs(variant)
    aliases = assign_aliases(inputs)
    raw = demo_evaluator_outputs(inputs)
    raw.update(overrides or {})
    outputs = {k: EVALUATOR_SCHEMAS[k].model_validate(v) for k, v in raw.items() if k not in drop}
    reading = inputs.activities.get("reading_aloud")
    metrics = reading_metrics(reading, kit.passage(reading.material_key)) if reading else None
    drill_a = drill_a_score(reading, metrics)
    relay = inputs.activities.get("read_relay")
    coverage = relay_coverage(kit.passage(relay.material_key), relay.transcript) if relay else None
    allowed = {
        k: build_call(k, inputs, aliases, kit=kit, relay_coverage=coverage).allowed_answers
        for k in EVALUATOR_SCHEMAS
    }
    relay_output = outputs.get("relay")
    relay_answer = inputs.activity_answer("read_relay")
    misstatements = (
        len(validate_cites(relay_output.misstatements, aliases=aliases, allowed=allowed["relay"])[0])
        if relay_output is not None
        else 0
    )
    drill_b = relay_score(
        coverage,
        concepts=concept_evidence(
            relay_output, aliases=aliases, relay_alias=relay_answer.alias if relay_answer else None
        ),
        paraphrase=relay_output.paraphrase if relay_output is not None else None,
        misstatements=misstatements,
        captured=coverage is not None,
    )
    topic = inputs.activities.get("topic_card")
    card = kit.topic_card(topic.material_key) if topic else None
    validation = validate_evaluation(
        inputs,
        aliases,
        outputs=outputs,
        allowed=allowed,
        drill_a=drill_a,
        drill_b=drill_b,
        card_bullets=card.bullets if card else (),
        fallbacks=drop,
    )
    summary = build_summary(
        inputs,
        aliases,
        result=validation,
        outputs=outputs,
        allowed=allowed,
        drill_a=drill_a,
        drill_b=drill_b,
        metrics_value=metrics,
        coverage=coverage,
        kit=kit,
    )
    drafts = {
        k: list(getattr(outputs.get(k), "review_items", None) or [])
        for k in ("communication", "resume_evidence", "technical", "trainability")
    }
    items = build_review_items(
        inputs,
        aliases,
        competencies=validation.competencies,
        claims=validation.claims,
        findings=validation.findings,
        drill_a=drill_a,
        drill_b=drill_b,
        verification=summary.get("verification"),
        drafts=drafts,
        allowed=allowed,
        eligibility=eligibility if eligibility is not None else rows.eligibility,
        eligibility_enabled=eligibility_enabled,
        onboarding=rows.onboarding,
        integrity_unreviewed=1,
        kit=kit,
    )
    return Evaluated(
        kit, rows, inputs, aliases, outputs, allowed, drill_a, drill_b, coverage, validation, summary, items
    )


def evaluation_rows(evaluated: Evaluated) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
    """``(evaluation, competency rows)`` as row-like objects, as the pipeline would store them."""
    summary = evaluated.validation.summary
    scored = all(c.status == "scored" for c in evaluated.validation.competencies.values())
    evaluation = SimpleNamespace(
        id=uuid.uuid4(),
        version=1,
        status="ready",
        reason="session_completed",
        kit_version="v1",
        scoring_version="intake-scoring-1",
        weighted_total=summary.total,
        partial_total=summary.partial_total,
        normalized_100=summary.normalized_100,
        band=summary.band.value,
        evidence_sufficiency="sufficient" if scored else "partial",
        summary=evaluated.summary,
        validator={"issues": [i.as_dict() for i in evaluated.validation.issues]},
        review_items=evaluated.review_items,
        training_plan=None,
        model_call_ids=[],
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        error=None,
    )
    rows = [
        SimpleNamespace(
            competency_key=key,
            status=comp.status,
            score=comp.score,
            weight=INTAKE_WEIGHTS[key],
            weighted=(comp.score * INTAKE_WEIGHTS[key]) if comp.score is not None else None,
            evaluator=comp.evaluator,
            reasoning=comp.reasoning,
            evidence=comp.evidence,
            missing_evidence=comp.missing_evidence,
            strengths=comp.strengths,
            improvements=comp.improvements,
            sub_scores=comp.sub_scores,
        )
        for key, comp in evaluated.validation.competencies.items()
    ]
    return evaluation, rows
