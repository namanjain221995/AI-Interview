"""Intake evaluator calls: build the prompt of one evaluator and run it (design contract sections 8.2-8.4).

``build_call(key, inputs, aliases, ...)`` selects the evaluator's exchanges (:func:`evidence.evaluator_exchanges`),
renders its versioned prompt and records which answer aliases it may cite. ``run_call`` checks the prompt with the
privacy rule V8 (:func:`evidence.assert_prompt_safe`) and sends it through a backend:

* :class:`ModelBackend` (production): ``ai.llm.structured(<Output>, messages, context=CallContext(
  task=ANSWER_EVALUATION, session_id, organization_id, prompt_version="intake-eval-<key>@…"), temperature=0.1,
  max_tokens=…, timeout=90, max_attempts=2)``;
* :class:`FixtureBackend` (demo data, tests without a model registry): canned outputs per evaluator.

No ``ModelTask`` values are added: evaluators use ``ANSWER_EVALUATION``, the training plan ``REPORT_GENERATION``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.ai.base import AIError, AIOutputInvalid, CallContext
from app.ai.prompts import render
from app.domain.enums import ModelTask
from app.services.intake import eval_prompts
from app.services.intake.eval_schemas import EVALUATOR_SCHEMAS, describe_nested
from app.services.intake.evidence import (
    CLAIM_DEPTH_CUES,
    AliasMap,
    EvaluationInputs,
    PromptPrivacyError,
    allowed_answer_aliases,
    assert_prompt_safe,
    evaluator_exchanges,
    render_exchanges,
    text_digest,
)
from app.services.intake.kit import IntakeKit, RubricCompetency, load_intake_kit
from app.services.intake.reading import RelayCoverage

EVAL_TEMPERATURE = 0.1
EVAL_TIMEOUT_SECONDS = 90.0
EVAL_MAX_ATTEMPTS = 2
#: Output token budgets: 1200 per single-judgement evaluator (contract); the two-part outputs need more room.
MAX_TOKENS: dict[str, int] = {
    "communication": 1200,
    "resume_evidence": 1800,
    "technical": 1400,
    "relay": 1400,
    "structured": 1400,
    "trainability": 1800,
    "composure": 1400,
}


@dataclass(slots=True)
class EvaluatorCall:
    key: str
    schema: type[BaseModel]
    messages: list[dict[str, str]]
    allowed_answers: frozenset[str]
    allowed_questions: frozenset[str]
    prompt_version: str
    input_digest: str
    max_tokens: int
    empty: bool = False  # nothing to evaluate (no exchanges at all)


@dataclass(slots=True)
class EvaluatorOutcome:
    key: str
    output: BaseModel | None
    model_call_id: str | None = None
    latency_ms: float = 0.0
    attempts: int = 1
    error: str | None = None
    retryable: bool = False
    raw: dict[str, Any] | None = None


class EvaluatorBackend(Protocol):
    async def run(self, call: EvaluatorCall, context: CallContext) -> EvaluatorOutcome: ...


# ============================================================================================== prompt building


def competency_text(competency: RubricCompetency) -> str:
    return eval_prompts.COMPETENCY_BLOCK.render(
        c={
            "key": competency.key,
            "label": competency.label,
            "description": competency.description,
            "anchors": competency.anchors,
            "excluded": ", ".join(competency.excluded_signals),
        }
    )


def _system(key: str, kit: IntakeKit, task_vars: Mapping[str, Any]) -> str:
    task = render(eval_prompts.TASKS[key], **task_vars)
    return eval_prompts.SYSTEM_TEMPLATE.render(
        INTAKE_EVAL_RULES=eval_prompts.INTAKE_EVAL_RULES,
        task=task,
        schema=describe_nested(EVALUATOR_SCHEMAS[key]),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _language_check(kit: IntakeKit, language: str | None) -> tuple[str, str, str | None, str | None, str]:
    check = kit.language_check(language) if language else None
    if check is None:
        return (language or "unknown", "(no language check recorded)", None, None, "(none)")
    first, second = check.questions
    points = "\n".join(f"{n}. {p}" for n, p in enumerate(first.key_points, start=1))
    retry_points = "\n".join(f"{n}. {p}" for n, p in enumerate(second.key_points, start=1))
    return check.label, first.text, second.text, retry_points, points


def build_call(
    key: str,
    inputs: EvaluationInputs,
    aliases: AliasMap,
    *,
    kit: IntakeKit | None = None,
    resume_verdicts: Sequence[Mapping[str, Any]] | None = None,
    relay_coverage: RelayCoverage | None = None,
) -> EvaluatorCall:
    """Prompt + citation scope of one evaluator (aliases must already be assigned)."""
    kit = kit or load_intake_kit()
    selected = evaluator_exchanges(inputs, key)
    transcript = render_exchanges(selected)
    allowed_answers = allowed_answer_aliases(selected)
    allowed_questions = frozenset(ex.question.alias for ex in selected if ex.question is not None)
    task_vars: dict[str, Any] = {}
    user_vars: dict[str, Any] = {"exchanges": transcript or "(no answers recorded)"}
    if key in ("communication", "structured", "technical", "composure"):
        competency_key = {"structured": "structured_answer"}.get(key, key)
        task_vars["competency"] = competency_text(kit.competency(competency_key))
    if key == "resume_evidence":
        task_vars["cues"] = list(CLAIM_DEPTH_CUES)
        user_vars["claims"] = _json(
            [{k: c.get(k) for k in ("ref", "text", "probe", "source", "source_quote")} for c in inputs.claims]
            or [{"note": "no claims were selected"}]
        )
    elif key == "technical":
        task_vars["expected_rating"] = kit.language_verification.expected_from_rating
        label, question, retry, retry_points, points = _language_check(kit, inputs.language)
        user_vars.update(
            language=label,
            verification_question=question,
            key_points=points,
            retry_question=retry
            if any(q.item_key == "mandatory.mq3.verification_retry" for q in inputs.questions)
            else None,
            retry_key_points=retry_points,
            form=_json(
                {
                    k: inputs.onboarding.get(k)
                    for k in (
                        "primary_language",
                        "self_rating",
                        "scripting",
                        "scripting_details",
                        "data_ml_libraries",
                        "years_hands_on",
                    )
                    if inputs.onboarding.get(k) is not None
                }
                or {"note": "no form answers"}
            ),
            verdicts=_json(
                [
                    {k: v.get(k) for k in ("claim_ref", "claim", "verdict", "note")}
                    for v in (resume_verdicts or [])
                ]
                or [{"note": "no verdicts available"}]
            ),
        )
    elif key == "relay":
        activity = inputs.activities.get("read_relay")
        passage = kit.passage(activity.material_key) if activity and activity.material_key else None
        relay_answer = inputs.activity_answer("read_relay")
        relay_alias = relay_answer.alias if relay_answer is not None else "(none)"
        task_vars["relay_alias"] = relay_alias
        hints = (
            ", ".join(c.key for c in relay_coverage.concepts if c.covered) or "none"
            if relay_coverage is not None
            else "not available"
        )
        user_vars.update(
            passage_key=passage.key if passage else "?",
            passage_title=passage.title if passage else "(unknown passage)",
            passage_text=passage.text if passage else "(passage not recorded)",
            concepts="\n".join(f"{c.key}: {c.text}" for c in passage.relay_concepts) if passage else "(none)",
            cue_hint=hints,
        )
    elif key == "structured":
        activity = inputs.activities.get("topic_card")
        card = kit.topic_card(activity.material_key) if activity and activity.material_key else None
        user_vars.update(
            card_number=card.number if card else "?",
            card_title=card.title if card else "(unknown card)",
            card_instruction=card.instruction if card else "",
            bullets="\n".join(f"{n}. {b}" for n, b in enumerate(card.bullets, start=1)) if card else "(none)",
        )
    elif key == "trainability":
        task_vars["competency"] = competency_text(kit.competency("trainability"))
        task_vars["competency_2"] = competency_text(kit.competency("adaptability"))
        user_vars["form"] = _json(
            {
                k: inputs.onboarding.get(k)
                for k in (
                    "target_role",
                    "target_role_other",
                    "open_to_ai_ml",
                    "training_commitment",
                    "weekly_hours",
                    "experience_level_comfort",
                    "years_hands_on",
                )
                if inputs.onboarding.get(k) is not None
            }
            or {"note": "no form answers"}
        )
    system = _system(key, kit, task_vars)
    user = eval_prompts.USER_TEMPLATES[key].render(**user_vars)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    digest = text_digest(key, eval_prompts.combined_version(key), system, user)
    return EvaluatorCall(
        key=key,
        schema=EVALUATOR_SCHEMAS[key],
        messages=messages,
        allowed_answers=allowed_answers,
        allowed_questions=allowed_questions,
        prompt_version=eval_prompts.combined_version(key),
        input_digest=digest,
        max_tokens=MAX_TOKENS[key],
        empty=not allowed_answers,
    )


# ============================================================================================== backends


class ModelBackend:
    """Calls the main model through the AI registry (``get_ai()``)."""

    def __init__(self, services: Any | None = None) -> None:
        self._services = services

    def _ai(self) -> Any:
        if self._services is not None:
            return self._services
        from app.ai.registry import get_ai

        return get_ai()

    async def run(self, call: EvaluatorCall, context: CallContext) -> EvaluatorOutcome:
        started = time.perf_counter()
        try:
            result = await self._ai().llm.structured(
                call.schema,
                call.messages,
                context=context,
                temperature=EVAL_TEMPERATURE,
                max_tokens=call.max_tokens,
                timeout=EVAL_TIMEOUT_SECONDS,
                max_attempts=EVAL_MAX_ATTEMPTS,
            )
        except AIError as exc:
            retryable = bool(getattr(exc, "retryable", False)) or isinstance(exc, AIOutputInvalid)
            return EvaluatorOutcome(
                key=call.key,
                output=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
                retryable=retryable,
            )
        return EvaluatorOutcome(
            key=call.key,
            output=result.value,
            model_call_id=result.model_call_id,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            attempts=result.attempts,
        )


FixtureItem = BaseModel | Mapping[str, Any] | Exception | Callable[[EvaluatorCall], Any]


@dataclass(slots=True)
class FixtureBackend:
    """Canned evaluator outputs (no model): ``{evaluator: output | dict | exception | callable(call)}``."""

    outputs: Mapping[str, FixtureItem]
    calls: list[EvaluatorCall] = field(default_factory=list)

    async def run(self, call: EvaluatorCall, context: CallContext) -> EvaluatorOutcome:  # noqa: ARG002
        self.calls.append(call)
        item: Any = self.outputs.get(call.key)
        if callable(item) and not isinstance(item, BaseModel):
            item = item(call)
        if isinstance(item, Exception):
            return EvaluatorOutcome(
                key=call.key,
                output=None,
                error=f"{type(item).__name__}: {item}",
                retryable=bool(getattr(item, "retryable", True)),
            )
        if item is None:
            return EvaluatorOutcome(key=call.key, output=None, error="no fixture output", retryable=False)
        try:
            value = item if isinstance(item, call.schema) else call.schema.model_validate(item)
        except ValidationError as exc:
            return EvaluatorOutcome(key=call.key, output=None, error=f"invalid fixture: {exc}"[:500])
        return EvaluatorOutcome(key=call.key, output=value, model_call_id=None)


# ============================================================================================== running


def call_context(call: EvaluatorCall, inputs: EvaluationInputs) -> CallContext:
    return CallContext(
        task=ModelTask.ANSWER_EVALUATION,
        session_id=str(inputs.session_id),
        organization_id=str(inputs.organization_id),
        prompt_version=call.prompt_version,
    )


async def run_call(
    call: EvaluatorCall,
    inputs: EvaluationInputs,
    *,
    backend: EvaluatorBackend,
    kit: IntakeKit | None = None,
    privacy_markers: Sequence[str] = (),
    country: str | None = None,
) -> EvaluatorOutcome:
    """V8 check, then the backend call. A privacy violation is never sent (non-retryable failure)."""
    try:
        assert_prompt_safe(call.messages, kit=kit, country=country, extra_forbidden=privacy_markers)
    except PromptPrivacyError as exc:
        return EvaluatorOutcome(
            key=call.key, output=None, error=f"privacy check failed: {exc}", retryable=False
        )
    return await backend.run(call, call_context(call, inputs))


__all__ = [
    "EVAL_MAX_ATTEMPTS",
    "EVAL_TEMPERATURE",
    "EVAL_TIMEOUT_SECONDS",
    "MAX_TOKENS",
    "EvaluatorBackend",
    "EvaluatorCall",
    "EvaluatorOutcome",
    "FixtureBackend",
    "ModelBackend",
    "build_call",
    "call_context",
    "competency_text",
    "run_call",
]
