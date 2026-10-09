"""Speech-first turn decisions.

One streaming call to the main model per turn. The ``speech`` field of
``TurnDecision`` comes first in the schema, so ``StreamingJsonFieldExtractor``
yields spoken text after a handful of tokens; every ``SpeechDelta`` is
forwarded immediately (subtitles + TTS chunker). When the stream completes
the full object is validated; the policy post-check runs in the engine.

Failure handling (never stall):

* failure before any speech (timeout to first speech, circuit open, invalid
  output): retry once with a trimmed context, then the **fallback question**
  (top bank question for the directive's target, or a competency template)
  prefixed with a short, varied acknowledgement - emitted non-streamed;
* failure after speech started (stream cut, invalid tail): the spoken text is
  kept and the rest of the decision is synthesized from the directive
  (``synthesized=True``) so state stays consistent.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import json_repair
from pydantic import BaseModel, ValidationError

from app.ai.base import AIError, AIOutputInvalid, AIServices, CallContext
from app.ai.structured import StreamingJsonFieldExtractor, schema_for, strip_wrappers
from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.domain.enums import (
    AnswerQuality,
    Difficulty,
    ModelTask,
    QuestionKind,
    SegmentKind,
    TurnAction,
)
from app.schemas.ai import TurnDecision
from app.services.interview.behavior import provisional
from app.services.interview.config import EngineConfig
from app.services.interview.contracts import SpeechDelta
from app.services.interview.policy import (
    FOLLOWUP_ACTIONS,
    NON_TECHNICAL_KEYS,
    BankCandidate,
    PolicyDirective,
)
from app.services.interview.prompts import fence
from app.services.interview.speech import OpenerFilter, QuestionLimiter
from app.services.interview.state import InterviewState

if TYPE_CHECKING:
    from app.services.interview.attribution import AttributionGuard
    from app.services.interview.grounding import Reference

log = get_logger(__name__)

TURN_SCHEMA: dict[str, Any] = {"name": "TurnDecision", "schema": schema_for(TurnDecision)}

_MARKDOWN = re.compile(r"[*`#]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

ACKNOWLEDGEMENTS = (
    "Thanks for that.",
    "Okay, thank you.",
    "Got it.",
    "Alright, thanks.",
    "Okay.",
    "Right.",
)
#: Builds the attribution guard of one stream attempt: (regeneration allowed, speech already spoken).
GuardFactory = Callable[[bool, bool], "AttributionGuard"]


def sanitize_delta(text: str) -> str:
    """Strip markdown emphasis characters and newlines from spoken deltas."""
    cleaned = _MARKDOWN.sub("", text)
    return cleaned.replace("\r", " ").replace("\n", " ")


def pick(options: Sequence[str], seed: str) -> str:
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]  # noqa: S324 - variation only


def question_core(text: str) -> str:
    """The question part of an utterance (drops leading acknowledgements)."""
    sentences = [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]
    if not sentences:
        return text.strip()
    for i, s in enumerate(sentences):
        if s.endswith("?"):
            # Keep a short lead-in sentence immediately before the question (context).
            start = (
                i - 1
                if i > 0 and len(sentences[i - 1].split()) >= 6 and not sentences[i - 1].endswith("?")
                else i
            )
            return " ".join(sentences[start : i + 1])
    return sentences[-1]


def question_kind(action: TurnAction, segment_kind: str | None, competency_key: str | None) -> QuestionKind:
    if action == TurnAction.VERIFY_CLAIM:
        return QuestionKind.RESUME_CLAIM
    if action in FOLLOWUP_ACTIONS or action == TurnAction.INCREASE_DIFFICULTY:
        return QuestionKind.FOLLOWUP
    if action == TurnAction.START_CODING or action == TurnAction.CODING_COMMENT:
        return QuestionKind.CODING
    if action == TurnAction.CLOSE_INTERVIEW:
        return QuestionKind.CLOSING
    if action == TurnAction.INTRODUCE:
        return QuestionKind.INTRO
    mapping = {
        SegmentKind.BEHAVIORAL.value: QuestionKind.BEHAVIORAL,
        SegmentKind.LEADERSHIP.value: QuestionKind.LEADERSHIP,
        SegmentKind.SYSTEM_DESIGN.value: QuestionKind.SYSTEM_DESIGN,
        SegmentKind.RECRUITER.value: QuestionKind.RECRUITER,
        SegmentKind.RESUME_VALIDATION.value: QuestionKind.RESUME_CLAIM,
    }
    if segment_kind in mapping:
        return mapping[segment_kind]
    if competency_key in NON_TECHNICAL_KEYS:
        return QuestionKind.BEHAVIORAL
    return QuestionKind.TECHNICAL


@dataclass(slots=True)
class TurnOutcome:
    decision: TurnDecision | None = None
    speech: str = ""
    synthesized: bool = False
    fallback: bool = False
    fallback_bank: BankCandidate | None = None
    attempts: int = 0
    first_speech_ms: float | None = None
    total_ms: float | None = None
    model_call_id: str | None = None
    errors: list[str] = field(default_factory=list)
    speech_started: bool = False
    extra: Any = None  # validated object for non-TurnDecision speech schemas
    speech_done_ms: float | None = None
    tail_cut: bool = False
    raw_partial: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    dropped_opener: str | None = None  # evaluative first sentence removed before it was spoken
    dropped_tail: str | None = None  # text after the first question (a second question) not spoken
    # Attribution guard (see attribution.py): what it held back, dropped, rewrote or regenerated.
    ungrounded: str | None = None  # first attributed phrase the candidate's turns do not support
    dropped_attribution: list[str] = field(default_factory=list)
    rewritten_frame: str | None = None  # repeated "You mentioned X." turned into an acknowledgement
    regenerated: bool = False

    def latency(self) -> dict[str, Any]:
        return {
            "first_speech_ms": round(self.first_speech_ms, 1) if self.first_speech_ms is not None else None,
            "speech_done_ms": round(self.speech_done_ms, 1) if self.speech_done_ms is not None else None,
            "total_ms": round(self.total_ms, 1) if self.total_ms is not None else None,
            "attempts": self.attempts,
            "tail_cut": self.tail_cut,
            "dropped_opener": bool(self.dropped_opener),
            "dropped_tail": bool(self.dropped_tail),
            "regenerated": self.regenerated,
            **{k: round(v, 1) for k, v in self.timings.items()},
        }

    def note_guard(self, guard: AttributionGuard | None) -> None:
        if guard is None:
            return
        if guard.unsupported is not None and self.ungrounded is None:
            self.ungrounded = guard.unsupported.phrase[:160]
        self.dropped_attribution.extend(d for d in guard.dropped if d not in self.dropped_attribution)
        if guard.rewritten and self.rewritten_frame is None:
            self.rewritten_frame = guard.rewritten
        if guard.held:
            self.timings["attribution_hold_ms"] = self.timings.get("attribution_hold_ms", 0.0) + guard.hold_ms
            self.timings["attribution_held"] = self.timings.get("attribution_held", 0.0) + guard.held


def synthesize_decision(
    speech: str,
    directive: PolicyDirective,
    state: InterviewState,
    *,
    action: TurnAction | None = None,
    reason: str,
    bank: BankCandidate | None = None,
) -> TurnDecision:
    act = action or (directive.required_action or directive.recommended_action)
    if act == TurnAction.START_CODING:
        act = TurnAction.ASK_QUESTION
    spec = provisional(act)
    target = directive.target()
    topic = None
    if bank is not None and bank.topic:
        topic = bank.topic
    elif target is not None and target.uncovered_topics:
        topic = target.uncovered_topics[0]
    elif directive.stay_on_topic:
        topic = state.current_topic
    return TurnDecision(
        speech=speech[:700] or "Let's continue.",
        action=act,
        answer_quality=directive.answer_quality,
        competency_key=directive.target_competency,
        topic=(topic or None) and topic[:120],
        difficulty=bank.difficulty if bank is not None else directive.target_difficulty,
        kind=question_kind(act, directive.time.segment_kind, directive.target_competency),
        expression=spec.expression,
        gesture=spec.gesture,
        gesture_intensity=spec.gesture_intensity,
        question_focus=question_core(speech)[:200] or None,
        expected_evidence=(
            list(bank.expected_concepts[:5])
            if bank is not None
            else (target.uncovered_topics[:4] if target else [])
        ),
        claim_id=directive.claim_to_verify.id
        if act == TurnAction.VERIFY_CLAIM and directive.claim_to_verify
        else None,
        bank_question_id=bank.id if bank is not None else None,
        missing_specifics=list(directive.missing_specifics[:6]),
        new_claims=[],
        possible_contradiction=None,
        reason=reason[:400],
    )


def _template_question(state: InterviewState, directive: PolicyDirective) -> str:
    target = directive.target()
    if target is None:
        return "Can you walk me through a recent project you're proud of, what your role was and how it turned out?"
    name = target.name
    topic = target.uncovered_topics[0].replace("_", " ") if target.uncovered_topics else None
    if target.key in NON_TECHNICAL_KEYS:
        focus = topic or name.lower()
        return f"Tell me about a time when {focus} really mattered in your work: what was the situation, what did you do, and what was the result?"
    if topic:
        return f"Let's talk about {name}. Can you walk me through a recent situation where you dealt with {topic}: what you did, the key decisions, and how it turned out?"
    return f"Let's talk about {name}. Can you walk me through a recent project where {name} played a big part, and what your role in it was?"


def fallback_utterance(
    state: InterviewState, directive: PolicyDirective, *, current_question: str | None
) -> tuple[str, TurnAction, BankCandidate | None]:
    """Deterministic utterance honouring the directive (LLM unavailable)."""
    seed = f"{state.session_id}:{state.turn_index}"
    action = directive.required_action or directive.recommended_action
    core = question_core(current_question) if current_question else None
    if action == TurnAction.ACKNOWLEDGE_AND_WAIT:
        return (
            pick(
                [
                    "Of course, take your time.",
                    "Sure, no rush. Take a moment.",
                    "Take all the time you need.",
                ],
                seed,
            ),
            action,
            None,
        )
    if action == TurnAction.CLARIFY_QUESTION and core:
        if directive.answer_quality == AnswerQuality.WANTS_HINT:
            return f"I can't give hints on this one, but let me put it another way. {core}", action, None
        return f"Sure, let me put it another way. {core}", action, None
    if action == TurnAction.REDIRECT and core:
        return f"Let's come back to the question. {core}", action, None
    if action == TurnAction.ANSWER_CANDIDATE_QUESTION:
        base = "That's a great question for the hiring team, and they'll be glad to cover it when they follow up."
        if state.candidate_questions_invited or directive.invite_candidate_questions:
            return f"{base} Is there anything else you'd like to ask?", action, None
        if core:
            return f"{base} Coming back to my question: {core}", action, None
        return base, action, None
    if action == TurnAction.INVITE_CANDIDATE_QUESTIONS:
        return (
            "Before we wrap up, what questions do you have for me about the role, the team or the process?",
            action,
            None,
        )
    if action == TurnAction.CLOSE_INTERVIEW:
        return (
            "Thank you for your time today. The hiring team will review our conversation and follow up about next steps.",
            action,
            None,
        )
    ack = "" if directive.first_question else pick(ACKNOWLEDGEMENTS, seed) + " "
    bank = directive.candidate_bank_questions[0] if directive.candidate_bank_questions else None
    if action in (TurnAction.SIMPLIFY, TurnAction.CLARIFY_QUESTION) and core:
        return f"{ack}Let me narrow that down a little. {core}", TurnAction.SIMPLIFY, None
    if bank is not None:
        lead = "" if directive.stay_on_topic or directive.first_question else "Let's shift gears. "
        return (
            f"{ack}{lead}{bank.text}".strip(),
            (
                TurnAction.CHANGE_TOPIC
                if action in (TurnAction.CHANGE_TOPIC,)
                else TurnAction.ASK_QUESTION
                if action
                not in (TurnAction.ASK_FOLLOWUP, TurnAction.VERIFY_CLAIM, TurnAction.INCREASE_DIFFICULTY)
                else action
            ),
            bank,
        )
    if action == TurnAction.VERIFY_CLAIM and directive.claim_to_verify is not None:
        claim = directive.claim_to_verify
        topics = ", ".join(claim.evidence_topics[:3])
        text = f"{ack}Your resume mentions that {claim.claim.rstrip('.')}. Could you walk me through what you personally did there"
        text += f", including {topics}?" if topics else "?"
        return text, action, None
    return (
        f"{ack}{_template_question(state, directive)}",
        (
            TurnAction.CHANGE_TOPIC
            if state.current_competency and not directive.stay_on_topic
            else TurnAction.ASK_QUESTION
        ),
        None,
    )


@dataclass(slots=True)
class SpeechFilters:
    """The streaming filters of one attempt, in order: OpenerFilter -> AttributionGuard -> QuestionLimiter."""

    opener: OpenerFilter | None = None
    guard: AttributionGuard | None = None
    limiter: QuestionLimiter | None = None

    def run(self, text: str, outcome: TurnOutcome, *, final: bool) -> str:
        """New speech text through the filters; ``final`` flushes what they still hold."""
        out = text
        if self.opener is not None:
            out = self.opener.feed(out) if out else ""
            if final:
                out += self.opener.flush()
                outcome.dropped_opener = outcome.dropped_opener or self.opener.dropped
        if self.guard is not None:
            out = self.guard.feed(out) if out else ""
            if final and self.guard.regenerate is None:
                out += self.guard.flush()
        if self.limiter is not None and out:
            out = self.limiter.feed(out)
        return out


def correction_messages(messages: list[dict[str, Any]], ref: Reference, spoken: str) -> list[dict[str, Any]]:
    """The turn prompt plus a correction for a regeneration after an unsupported attribution."""
    note = (
        "CORRECTION: your draft attributed this to the candidate, but they never said it (check RECENT "
        "CONVERSATION and LAST ANSWER): "
        + fence(ref.phrase, "derived_ungrounded_reference")
        + "\nDo not attribute it to them. Ask your question without that reference; content from the resume is "
        '"the resume mentions ...".'
    )
    if spoken:
        note += (
            " You have already said the words below; continue right after them without repeating them:\n"
            + fence(spoken, "derived_spoken_so_far")
        )
    out = [dict(m) for m in messages]
    out[-1]["content"] = f"{out[-1]['content']}\n\n{note}\nReply with the JSON object only."
    return out


async def stream_speech_object(
    ai: AIServices,
    messages: list[dict[str, Any]],
    *,
    schema: type[BaseModel],
    json_schema: dict[str, Any],
    context: CallContext,
    utterance_id: str,
    outcome: TurnOutcome,
    config: EngineConfig,
    retry_messages: list[dict[str, Any]] | None = None,
    limit_questions: bool = True,
    guard_factory: GuardFactory | None = None,
) -> AsyncGenerator[SpeechDelta, None]:
    """Stream ``schema`` (speech-first) and yield its speech as deltas.

    Fills ``outcome`` with the validated object (``outcome.decision`` holds it
    for TurnDecision; other schemas use ``outcome.extra``).

    Spoken text passes OpenerFilter -> AttributionGuard (``guard_factory``) ->
    QuestionLimiter before it is yielded. When the guard finds an unsupported
    attribution the question depends on, the attempt is abandoned and the turn
    is regenerated once with a correction; text already spoken is kept and the
    regeneration continues after it.

    Tail budget: once the speech is complete, the remaining (metadata) fields
    may take several seconds on this deployment. The realtime runtime waits for
    the stream to end before taking the next turn, so the tail is bounded by
    roughly the time the speech takes to play (``turn_tail_min_s`` +
    ``turn_tail_s_per_word`` per spoken word); a longer tail is cut and the
    fields received so far are recovered from the partial object
    (``outcome.raw_partial``)."""
    attempts = [messages] + ([retry_messages] if retry_messages and config.turn_retry_trimmed else [])
    started = time.monotonic()
    emitted: list[str] = []  # across attempts: a regeneration continues the utterance
    regenerations_left = 1 if guard_factory is not None else 0
    index = 0
    while index < len(attempts):
        msgs = attempts[index]
        index += 1
        outcome.attempts += 1
        speech_turn = schema is TurnDecision
        extractor = StreamingJsonFieldExtractor("speech")
        filters = SpeechFilters(
            opener=OpenerFilter() if speech_turn else None,
            guard=(
                guard_factory(regenerations_left > 0, bool(emitted))
                if guard_factory is not None and speech_turn
                else None
            ),
            limiter=QuestionLimiter() if speech_turn and limit_questions else None,
        )
        guard = filters.guard
        finalized = False
        restart = False
        stream = ai.llm.stream(
            msgs,
            context=context,
            json_schema=json_schema,
            temperature=config.turn_temperature,
            max_tokens=config.turn_max_tokens,
            thinking=False,
            timeout=config.turn_total_timeout_s,
        )
        outcome.model_call_id = getattr(stream, "model_call_id", None)
        attempt_start = time.monotonic()
        tail_deadline: float | None = None
        try:
            iterator = stream.__aiter__()
            while True:
                now = time.monotonic()
                if emitted:
                    budget = max(0.5, config.turn_total_timeout_s - (now - attempt_start))
                else:
                    budget = config.turn_first_speech_timeout_s - (now - attempt_start)
                    if budget <= 0:
                        raise TimeoutError("no speech before the first-speech deadline")
                if extractor.done and not finalized:
                    finalized = True
                    held = filters.run("", outcome, final=True)
                    if guard is not None and guard.regenerate is not None:
                        restart = True
                        break
                    if held:
                        if not emitted:
                            outcome.first_speech_ms = (time.monotonic() - started) * 1000.0
                            outcome.speech_started = True
                        emitted.append(held)
                        yield SpeechDelta(utterance_id=utterance_id, text=held)
                if extractor.done and tail_deadline is None:
                    words = len("".join(emitted).split())
                    tail_deadline = now + config.turn_tail_min_s + config.turn_tail_s_per_word * words
                    outcome.speech_done_ms = (now - started) * 1000.0
                if tail_deadline is not None:
                    remaining = tail_deadline - now
                    if remaining <= 0:
                        outcome.tail_cut = True
                        break
                    budget = min(budget, remaining)
                try:
                    async with asyncio.timeout(budget):
                        chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    if tail_deadline is not None:
                        outcome.tail_cut = True
                        break
                    raise
                text = extractor.feed(chunk)
                if text:
                    clean = sanitize_delta(text)
                    clean = filters.run(clean, outcome, final=False) if clean else ""
                    if guard is not None and guard.regenerate is not None:
                        restart = True
                        break
                    if not clean:
                        continue
                    if not emitted:
                        outcome.first_speech_ms = (time.monotonic() - started) * 1000.0
                        outcome.speech_started = True
                    emitted.append(clean)
                    yield SpeechDelta(utterance_id=utterance_id, text=clean)
            if not restart and not finalized:
                finalized = True
                held = filters.run("", outcome, final=True)
                if guard is not None and guard.regenerate is not None:
                    restart = True
                elif held:
                    if not emitted:
                        outcome.first_speech_ms = (time.monotonic() - started) * 1000.0
                        outcome.speech_started = True
                    emitted.append(held)
                    yield SpeechDelta(utterance_id=utterance_id, text=held)
            outcome.note_guard(guard)
            spoken = "".join(emitted).strip()
            if restart and guard is not None and guard.regenerate is not None:
                regenerations_left -= 1
                outcome.regenerated = True
                outcome.tail_cut = False
                attempts.insert(index, correction_messages(msgs, guard.regenerate, spoken))
                log.info(
                    "engine.attribution_regenerate",
                    session_id=context.session_id,
                    spoken_before=bool(spoken),
                )
                continue
            if filters.limiter is not None and filters.limiter.dropped.strip():
                outcome.dropped_tail = filters.limiter.dropped.strip()
            if outcome.tail_cut:
                outcome.speech = spoken
                outcome.raw_partial = extractor.raw_text
                outcome.synthesized = True
                return
            try:
                parsed = extractor.finalize(schema).value
            except AIOutputInvalid as exc:
                outcome.errors.append(f"invalid:{str(exc)[:160]}")
                if emitted:
                    outcome.speech = spoken
                    outcome.raw_partial = extractor.raw_text
                    outcome.synthesized = True
                    return
                continue
            if isinstance(parsed, TurnDecision):
                if not spoken and guard is not None and guard.dropped:
                    # Everything the model said was a dropped attribution: nothing was spoken, so the turn
                    # is regenerated (or retried / falls back) instead of committing words nobody heard.
                    outcome.errors.append("attribution:all_speech_dropped")
                    if regenerations_left > 0 and guard.unsupported is not None:
                        regenerations_left -= 1
                        outcome.regenerated = True
                        attempts.insert(index, correction_messages(msgs, guard.unsupported, ""))
                    continue
                if spoken and parsed.speech.strip() != spoken:
                    parsed = parsed.model_copy(update={"speech": spoken})
                outcome.decision = parsed
                outcome.speech = spoken or parsed.speech
            else:
                outcome.speech = spoken or str(getattr(parsed, "speech", ""))
                outcome.extra = parsed
            return
        except (AIError, TimeoutError) as exc:
            ERRORS.labels(component="engine_turn").inc()
            outcome.errors.append(f"{type(exc).__name__}:{str(exc)[:160]}")
            outcome.note_guard(guard)
            log.warning(
                "engine.turn_stream_failed",
                session_id=context.session_id,
                attempt=outcome.attempts,
                speech_started=bool(emitted),
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            if emitted:
                outcome.speech = "".join(emitted).strip()
                outcome.raw_partial = extractor.raw_text
                outcome.synthesized = True
                return
            continue
        finally:
            outcome.total_ms = (time.monotonic() - started) * 1000.0
            outcome.raw_partial = outcome.raw_partial or extractor.raw_text
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer()
    outcome.fallback = True


def recover_decision(
    raw: str | None, speech: str, directive: PolicyDirective, state: InterviewState, *, reason: str
) -> TurnDecision:
    """Decision for speech whose object did not complete: the fields that did
    arrive (json_repair of the partial object) are kept when individually
    valid; the rest come from the directive."""
    base = synthesize_decision(speech, directive, state, reason=reason).model_dump()
    obj: Any = None
    if raw:
        try:
            obj = json_repair.loads(strip_wrappers(raw) or raw)
        except Exception:  # noqa: BLE001 - garbage tail: keep the synthesized decision
            obj = None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (
                key == "speech"
                or key not in TurnDecision.model_fields
                or value is None
                and key in ("action", "difficulty")
            ):
                continue
            trial = dict(base)
            trial[key] = value
            try:
                TurnDecision.model_validate(trial)
            except ValidationError:
                continue
            base = trial
    return TurnDecision.model_validate(base)


def turn_context(
    state: InterviewState, prompt_version: str, task: ModelTask = ModelTask.TURN_DECISION
) -> CallContext:
    return CallContext(
        task=task,
        session_id=state.session_id,
        organization_id=state.organization_id,
        prompt_version=prompt_version,
    )


def default_difficulty(value: Any) -> Difficulty:
    try:
        return Difficulty(value)
    except ValueError:
        return Difficulty.MEDIUM


__all__ = [
    "ACKNOWLEDGEMENTS",
    "TURN_SCHEMA",
    "GuardFactory",
    "correction_messages",
    "TurnOutcome",
    "fallback_utterance",
    "pick",
    "question_core",
    "question_kind",
    "recover_decision",
    "sanitize_delta",
    "stream_speech_object",
    "synthesize_decision",
    "turn_context",
]
