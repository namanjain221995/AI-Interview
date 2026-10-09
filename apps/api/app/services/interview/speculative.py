"""Speculative preparation while the candidate pauses (never commits state).

``on_partial_transcript(..., silence_started=True)`` runs the fast special-turn
heuristics and, for real answers, a quick ``AnswerAssessment`` on the main
model (bounded timeout), and prefetches bank candidates. The result is cached
in memory and in ``session:{id}:prep`` (10 min TTL) keyed by the normalized
text; ``handle_candidate_turn`` uses it only when the final transcript
matches (identical, or the prepared text plus at most a few trailing words).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.ai.base import AIError, AIServices, CallContext
from app.ai.structured import describe_schema
from app.core.logging import get_logger
from app.domain.enums import ModelTask
from app.schemas.ai import AnswerAssessment
from app.services.interview.prompts import QUICK_ASSESSMENT
from app.services.interview.state import InterviewState

log = get_logger(__name__)

MAX_EXTRA_WORDS = 3


def prep_key(session_id: str) -> str:
    return f"session:{session_id}:prep"


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower().replace("’", "'"))
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass(slots=True)
class PrepEntry:
    text: str  # normalized
    quick: AnswerAssessment | None
    created: float
    question_id: str | None = None

    def matches(self, final_text: str, question_id: str | None) -> bool:
        if question_id != self.question_id:
            return False
        final = normalize_text(final_text)
        if final == self.text:
            return True
        if not final.startswith(self.text):
            return False
        extra = final[len(self.text) :].split()
        return len(extra) <= MAX_EXTRA_WORDS

    def to_json(self) -> str:
        return json.dumps(
            {
                "text": self.text,
                "quick": self.quick.model_dump(mode="json") if self.quick else None,
                "created": self.created,
                "question_id": self.question_id,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> PrepEntry | None:
        try:
            data = json.loads(raw)
            quick = AnswerAssessment.model_validate(data["quick"]) if data.get("quick") else None
            return cls(
                text=str(data["text"]),
                quick=quick,
                created=float(data.get("created", 0)),
                question_id=data.get("question_id"),
            )
        except (KeyError, TypeError, ValueError):
            return None


async def quick_assess(
    ai: AIServices, state: InterviewState, text: str, *, timeout_s: float
) -> AnswerAssessment | None:
    question = state.current_question()
    prompt = QUICK_ASSESSMENT.render(
        question=(question.text if question else "(introductory question)")[:600],
        expected=", ".join(question.expected_evidence[:5]) if question else "",
        answer=text[-3000:],
        schema=describe_schema(AnswerAssessment),
    )
    started = time.monotonic()
    try:
        result = await ai.llm.structured(
            AnswerAssessment,
            [{"role": "user", "content": prompt}],
            context=CallContext(
                task=ModelTask.ANSWER_ASSESSMENT,
                session_id=state.session_id,
                organization_id=state.organization_id,
                prompt_version=QUICK_ASSESSMENT.version,
            ),
            temperature=0.0,
            max_tokens=220,
            timeout=timeout_s,
            max_attempts=1,
        )
    except AIError as exc:
        log.info("engine.quick_assessment_skipped", session_id=state.session_id, reason=type(exc).__name__)
        return None
    log.debug(
        "engine.quick_assessment",
        session_id=state.session_id,
        ms=round((time.monotonic() - started) * 1000, 1),
    )
    return result.value


async def store_prep(redis: Redis, session_id: str, entry: PrepEntry, ttl_s: int) -> None:
    try:
        await redis.set(prep_key(session_id), entry.to_json(), ex=ttl_s)
    except (RedisError, OSError):
        pass


async def load_prep(redis: Redis, session_id: str) -> PrepEntry | None:
    try:
        raw = await redis.get(prep_key(session_id))
    except (RedisError, OSError):
        return None
    if not raw:
        return None
    return PrepEntry.from_json(raw.decode() if isinstance(raw, bytes) else str(raw))


async def clear_prep(redis: Redis, session_id: str) -> None:
    try:
        await redis.delete(prep_key(session_id))
    except (RedisError, OSError):
        pass


__all__ = ["PrepEntry", "clear_prep", "load_prep", "normalize_text", "prep_key", "quick_assess", "store_prep"]
