"""Live intake model output schemas (design contract 6.6).

``IntakeTurn`` is speech-first like ``TurnDecision``: ``speech`` comes first so text-to-speech starts after a
handful of tokens, and every field is required (nullable where it may not apply) so guided decoding always
emits the full record (the required-field wire-schema rule). ``SPEECH_FIRST_TURN`` makes
``app.services.interview.turns.stream_speech_object`` apply the same streaming filters as for
``TurnDecision`` (OpenerFilter, AttributionGuard, QuestionLimiter).

``VerificationGrade`` is the small background grade of the stage 3 language question (6.5.1): it only selects
reading material and is never a score.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, get_args

import json_repair
from pydantic import Field, ValidationError

from app.ai.structured import schema_for, strip_wrappers
from app.domain.enums import AnswerQuality
from app.schemas.ai import AIModel

IntakeMove = Literal["ask_item", "follow_up", "clarify", "answer_question", "redirect"]
ClaimSignal = Literal["hands_on", "team_level", "conceptual", "unsupported", "uncertain"]


class IntakeTurn(AIModel):
    """One live intake utterance phrased by the model inside a move the machine offered."""

    speech: str = Field(
        ..., min_length=1, max_length=600, description="1-2 short spoken sentences, exactly one question"
    )
    move: IntakeMove = Field(..., description="The offered move you made")
    item_key: str = Field(..., max_length=60, description="The offered item key")
    answer_quality: AnswerQuality | None = Field(..., description="Your read of the candidate's last answer")
    claim_signal: ClaimSignal | None = Field(
        ..., description="Stage 2 only: how hands-on the last answer was"
    )
    target_bullet: int | None = Field(
        ..., ge=1, le=4, description="topic.weakest only: the bullet you ask about"
    )
    missing_specifics: list[str] = Field(..., max_length=4)
    reason: str = Field(..., max_length=300)

    SPEECH_FIRST_TURN: ClassVar[bool] = True


INTAKE_TURN_SCHEMA: dict[str, Any] = {"name": "IntakeTurn", "schema": schema_for(IntakeTurn)}


class VerificationGrade(AIModel):
    """Background grade of the MQ3 language question against the kit key points (selection only)."""

    quality: Literal["strong", "adequate", "partial", "vague", "incorrect", "no_answer"]
    matched_key_points: list[int] = Field(
        ..., max_length=6, description="1-based indexes of the key points covered"
    )


def synthesize_turn(
    speech: str,
    *,
    move: str,
    item_key: str,
    reason: str,
) -> IntakeTurn:
    """An ``IntakeTurn`` for speech whose structured tail never arrived (template fallback, interruption)."""
    return IntakeTurn(
        speech=(speech or "Let's continue.")[:600],
        move=move if move in get_args(IntakeMove) else "ask_item",  # type: ignore[arg-type]
        item_key=item_key[:60],
        answer_quality=None,
        claim_signal=None,
        target_bullet=None,
        missing_specifics=[],
        reason=reason[:300],
    )


def recover_turn(raw: str | None, speech: str, *, move: str, item_key: str, reason: str) -> IntakeTurn:
    """Fields of a partial object (json_repair), each kept only when individually valid; the rest default."""
    base = synthesize_turn(speech, move=move, item_key=item_key, reason=reason).model_dump()
    obj: Any = None
    if raw:
        try:
            obj = json_repair.loads(strip_wrappers(raw) or raw)
        except Exception:  # noqa: BLE001 - garbage tail: keep the synthesized turn
            obj = None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "speech" or key not in IntakeTurn.model_fields:
                continue
            trial = dict(base)
            trial[key] = value
            try:
                IntakeTurn.model_validate(trial)
            except ValidationError:
                continue
            base = trial
    return IntakeTurn.model_validate(base)


__all__ = ["INTAKE_TURN_SCHEMA", "IntakeTurn", "VerificationGrade", "recover_turn", "synthesize_turn"]
