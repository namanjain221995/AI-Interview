"""Wire schemas of the intake evaluator calls (design contract section 8.4).

Every field is required (no defaults): guided decoding skips optional properties, so a schema with defaults would
let the model omit evidence or the sufficiency flag (see the résumé pipeline's required-field rule). Nullable fields
are ``X | None`` *and* required. The model never sees database ids: answers are cited by alias (``A12``) and
questions by alias (``Q7``); :mod:`app.services.intake.validator` maps them back and checks every quote against the
answer text the evaluator was shown.

Fairness: no field describes or infers emotion, nervousness, voice affect, energy, accent, appearance, honesty or
deception. Composure is recorded as observable behaviours (kept going when unsure, handled a follow-up, ...); claim
verdicts describe how far the answers supported a résumé claim, never whether the person is honest.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.schemas.ai import AIModel

# Aliases are plain bounded strings on the wire (no regex ``pattern``: every guided schema in this codebase sticks to
# keywords the serving stack is proven with); the validator drops any alias that is unknown or out of scope.
AnswerAlias = Annotated[str, Field(max_length=6, description="Answer alias, e.g. A12")]
QuestionAlias = Annotated[str, Field(max_length=6, description="Question alias, e.g. Q7")]
ShortText = Annotated[str, Field(max_length=300)]
NoteText = Annotated[str, Field(max_length=200)]

JudgedCompetency = Literal[
    "communication", "structured_answer", "technical", "trainability", "composure", "adaptability"
]
CoverageStatus = Literal["covered", "partial", "missing"]


class EvidenceCite(AIModel):
    """One verbatim quote from an answer the evaluator was shown."""

    answer: AnswerAlias
    quote: str = Field(..., min_length=3, max_length=300, description="Verbatim words from that answer")
    supports: Literal["positive", "negative", "context"]


class ReviewItemDraft(AIModel):
    """Something a human reviewer should look at (never a decision)."""

    kind: Literal["claims_not_supported", "mandatory_mismatch", "communication_unclear", "evaluator_note"]
    note: str = Field(..., max_length=300)
    answers: list[AnswerAlias] = Field(..., max_length=4)


class CompetencyJudgement(AIModel):
    competency: JudgedCompetency
    sufficient: bool = Field(..., description="false when the listed evidence is not enough to score fairly")
    score: int | None = Field(
        ..., ge=1, le=5, description="1-5 against the anchors; null exactly when not sufficient"
    )
    reasoning: str = Field(..., max_length=700)
    evidence: list[EvidenceCite] = Field(..., max_length=6)
    missing_evidence: list[NoteText] = Field(..., max_length=4)
    strengths: list[NoteText] = Field(..., max_length=3)
    improvements: list[NoteText] = Field(..., max_length=3)


# ---------------------------------------------------------------------------------------------- communication


class CommunicationOutput(AIModel):
    judgement: CompetencyJudgement
    review_items: list[ReviewItemDraft] = Field(..., max_length=3)


# --------------------------------------------------------------------------------------------- résumé evidence

ClaimVerdictValue = Literal[
    "hands_on", "team_level", "conceptual", "unsupported", "uncertain", "inconsistent"
]


class RungAnswers(AIModel):
    """The answer alias given to each rung of the depth ladder (null when the rung was not asked or answered)."""

    # Serialises like the contract's ``{ownership|mechanics|stress: alias | null}`` mapping while staying a plain
    # object with fixed properties for guided decoding.
    ownership: AnswerAlias | None
    mechanics: AnswerAlias | None
    stress: AnswerAlias | None


class ClaimVerdict(AIModel):
    claim: str = Field(..., max_length=4, description="Claim reference, e.g. C1")
    verdict: ClaimVerdictValue
    rung_answers: RungAnswers
    evidence: list[EvidenceCite] = Field(..., max_length=4)
    note: str = Field(
        ..., max_length=300, description="What the answers did, never a judgement of the person"
    )
    unresolved: list[NoteText] = Field(..., max_length=3)


class ResumeEvidenceOutput(AIModel):
    claims: list[ClaimVerdict] = Field(..., max_length=3)
    review_items: list[ReviewItemDraft] = Field(..., max_length=3)


# ------------------------------------------------------------------------------------------------- technical


class VerificationResult(AIModel):
    answer: AnswerAlias | None
    quality: Literal["correct", "partially_correct", "incorrect", "no_answer", "not_asked"]
    matched_key_points: list[Annotated[int, Field(ge=1, le=8)]] = Field(..., max_length=8)
    note: str = Field(..., max_length=300)


class TechnicalOutput(AIModel):
    judgement: CompetencyJudgement
    verification: VerificationResult
    rating_supported: Literal["supported", "partially", "not_supported", "unknown"]
    review_items: list[ReviewItemDraft] = Field(..., max_length=3)


# ------------------------------------------------------------------------------------------- relay (Drill B)


class ConceptJudgement(AIModel):
    key: str = Field(..., max_length=40, description="Concept key from the list")
    status: CoverageStatus
    answer: AnswerAlias | None
    quote: str | None = Field(..., max_length=300)


class RelayFollowup(AIModel):
    question: QuestionAlias
    answer: AnswerAlias | None
    note: str = Field(..., max_length=300)


class RelayOutput(AIModel):
    concepts: list[ConceptJudgement] = Field(..., max_length=8)
    paraphrase: Literal["own_words", "mixed", "recited", "insufficient"]
    misstatements: list[EvidenceCite] = Field(..., max_length=3)
    followups: list[RelayFollowup] = Field(..., max_length=3)
    notes: str = Field(..., max_length=500)


# --------------------------------------------------------------------------------------- structured (topic card)


class BulletJudgement(AIModel):
    index: int = Field(..., ge=1, le=4)
    status: CoverageStatus
    answer: AnswerAlias | None
    quote: str | None = Field(..., max_length=300)


class StructuredOutput(AIModel):
    judgement: CompetencyJudgement
    bullets: list[BulletJudgement] = Field(..., max_length=4)
    flow: Literal["natural", "mechanical", "disjointed"]
    examples_used: bool
    gap_handling: Literal["honest", "bluffed", "not_needed"]
    followup_analysis: str = Field(..., max_length=500)


# ------------------------------------------------------------------------------- trainability + adaptability


class TrainabilityOutput(AIModel):
    trainability: CompetencyJudgement
    adaptability: CompetencyJudgement
    learning_strategy: str = Field(..., max_length=500)
    teach_back: str = Field(..., max_length=500)
    ai_ml_openness: Literal["eager", "open", "hesitant", "refuses", "unknown"]
    review_items: list[ReviewItemDraft] = Field(..., max_length=3)


# ------------------------------------------------------------------------------------------------- composure


class ComposureObservation(AIModel):
    behaviour: Literal[
        "kept_going_when_unsure",
        "handled_followup",
        "recovered_from_mistake",
        "stalled_when_unsure",
        "abandoned_answer",
    ]
    answer: AnswerAlias
    note: str = Field(..., max_length=200)


class ComposureOutput(AIModel):
    judgement: CompetencyJudgement
    observations: list[ComposureObservation] = Field(..., max_length=8)


# --------------------------------------------------------------------------------------------- training plan


class TrainingMilestoneDraft(AIModel):
    week: int = Field(..., ge=1, le=3)
    milestone: str = Field(..., max_length=200)
    measure: str = Field(..., max_length=200)


class TrainingModuleDraft(AIModel):
    key: str = Field(..., max_length=40, description="Module key from the list")
    why: str = Field(..., max_length=400, description="Evidence-based, neutral reason")
    objectives: list[Annotated[str, Field(max_length=200)]] = Field(..., min_length=2, max_length=4)
    milestones: list[TrainingMilestoneDraft] = Field(..., min_length=3, max_length=6)


class TrainingPlanDraft(AIModel):
    summary: str = Field(..., max_length=600)
    modules: list[TrainingModuleDraft] = Field(..., min_length=1, max_length=5)


#: Evaluator key -> output schema (the ``intake_evaluator_runs.evaluator`` values that call a model).
EVALUATOR_SCHEMAS: dict[str, type[AIModel]] = {
    "communication": CommunicationOutput,
    "resume_evidence": ResumeEvidenceOutput,
    "technical": TechnicalOutput,
    "relay": RelayOutput,
    "structured": StructuredOutput,
    "trainability": TrainabilityOutput,
    "composure": ComposureOutput,
}

# ============================================================================================== prompt outline


def describe_nested(model: type[AIModel]) -> str:
    """Field outline of a wire schema for prompts, nested objects expanded (guided decoding does not show the schema
    to the model, and the generic ``describe_schema`` stops at the first level)."""
    from app.ai.structured import schema_for

    schema = schema_for(model)
    defs = schema.get("$defs", {})

    def resolve(node: dict) -> dict:
        seen = 0
        while "$ref" in node and seen < 16:
            node = defs[node["$ref"].split("/")[-1]]
            seen += 1
        return node

    def scalar(node: dict) -> str:
        if "enum" in node:
            return " | ".join(f'"{v}"' for v in node["enum"])
        if "const" in node:
            return f'"{node["const"]}"'
        kind = node.get("type", "any")
        if kind == "string":
            low, high = node.get("minLength"), node.get("maxLength")
            if low and high:
                return f"string ({low}-{high} chars)"
            return f"string (max {high} chars)" if high else "string"
        if kind in ("integer", "number") and ("minimum" in node or "maximum" in node):
            return f"{kind} {node.get('minimum', '')}..{node.get('maximum', '')}"
        return str(kind)

    def render(node: dict, indent: int) -> str:
        node = resolve(node)
        if "anyOf" in node:
            options = [resolve(o) for o in node["anyOf"]]
            nullable = any(o.get("type") == "null" for o in options)
            rest = [o for o in options if o.get("type") != "null"]
            body = " or ".join(render(o, indent) for o in rest) or "null"
            return f"{body} or null" if nullable else body
        if node.get("type") == "array":
            bounds = []
            if node.get("minItems"):
                bounds.append(f"min {node['minItems']}")
            if node.get("maxItems") is not None:
                bounds.append(f"max {node['maxItems']}")
            label = f"array ({', '.join(bounds)})" if bounds else "array"
            return f"{label} of {render(node.get('items', {}), indent)}"
        if "properties" in node:
            pad = "  " * (indent + 1)
            lines = ["{"]
            for name, prop in node["properties"].items():
                note = prop.get("description") or resolve(prop).get("description")
                text = render(prop, indent + 1)
                lines.append(f'{pad}"{name}": {text}' + (f" -- {note}" if note and "{" not in text else ""))
            lines.append("  " * indent + "}")
            return "\n".join(lines)
        return scalar(node)

    return render(schema, 0)


__all__ = [
    "EVALUATOR_SCHEMAS",
    "BulletJudgement",
    "ClaimVerdict",
    "ClaimVerdictValue",
    "CommunicationOutput",
    "CompetencyJudgement",
    "ComposureObservation",
    "ComposureOutput",
    "ConceptJudgement",
    "CoverageStatus",
    "EvidenceCite",
    "JudgedCompetency",
    "RelayFollowup",
    "RelayOutput",
    "ResumeEvidenceOutput",
    "ReviewItemDraft",
    "RungAnswers",
    "StructuredOutput",
    "TechnicalOutput",
    "TrainabilityOutput",
    "TrainingMilestoneDraft",
    "TrainingModuleDraft",
    "TrainingPlanDraft",
    "VerificationResult",
    "describe_nested",
]
