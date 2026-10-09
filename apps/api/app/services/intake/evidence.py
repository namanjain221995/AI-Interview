"""Evaluator inputs of the intake evaluation (design contract section 8.3).

Everything an evaluator sees is built here from **database rows only** (never from the live ``InterviewState``):
the intake ``questions`` (``segment = "intake:<stage>"``, meta in ``decision.intake``) and their ``answers``, the
activity rows (``intake_activity_results``: the reading, relay and topic-card captures), the stage results
(``intake_stage_results``), the prompt-safe onboarding digest and the stage 2 claims.

* **Aliases**: questions are ``Q1..Qn`` (by sequence) and answers ``A1..An`` (by sequence); the model never sees a
  UUID. :class:`AliasMap` maps every alias back to its ids, stage, item and timestamps.
* **Activity captures**: the answer of an activity shows the activity row's ``transcript_final`` (falling back to
  ``transcript_live``), never the answer row's text (contract 7.7).
* **Privacy**: MQ1 eligibility answers are not part of :class:`EvaluationInputs` at all (the pipeline loads them
  separately, for the human review item only); the onboarding digest is whitelisted field by field and never
  contains the full name; ``kit_original`` text is never read. :func:`assert_prompt_safe` (validator rule V8) checks
  every rendered prompt before it is sent.
* **Selection**: :data:`EVALUATOR_SCOPES` decides which exchanges each evaluator receives; an evaluator may only
  cite answers it was shown (validator rule V2).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.schemas.intake import ONBOARDING_DIGEST_KEYS, PRIMARY_LANGUAGE_LABELS
from app.services.intake.kit import STAGE_KEYS, IntakeKit, load_intake_kit

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

INTAKE_SEGMENT_PREFIX = "intake:"

#: Onboarding fields that may reach a prompt (contract 15.3 ``onboarding_digest``) — never eligibility, never the name.
ONBOARDING_PROMPT_FIELDS: tuple[str, ...] = ONBOARDING_DIGEST_KEYS
_ONBOARDING_LIMITS = {"technical_background": 600, "scripting_details": 200, "data_ml_libraries": 300}
_FORBIDDEN_ONBOARDING_KEYS = frozenset(
    {"eligibility", "work_authorized", "needs_sponsorship", "authorized", "country", "full_name", "email"}
)

ANSWER_TEXT_LIMIT = 2400  # characters of one answer shown to an evaluator (long answers are cut, marked)
ACTIVITY_TEXT_LIMIT = 4000

# Neutral cue descriptions for the résumé evidence evaluator (derived from the kit's résumé probing guidance,
# rewritten so they describe what an answer did, never the person).
CLAIM_DEPTH_CUES: tuple[str, ...] = (
    "The answers became more general, not more specific, as the follow-up questions went deeper.",
    "The answers described only team activity ('we') and no individual contribution ('I').",
    "The answer repeated the résumé wording instead of explaining how the work was done.",
    "The answer could not name the basic parts of a tool listed on the résumé.",
    "The self-rating from the form was not supported by the language check answer.",
)

READING_FOLLOWUP_KEYS: tuple[str, ...] = (
    "reading.followup.least_sure",
    "reading.followup.non_technical",
    "reading.followup.interviewer_question",
)
LADDER_RUNG = re.compile(r"^resume\.C(\d)\.(ownership|mechanics|stress)$")
CHALLENGE_ITEM = re.compile(
    r"(^resume\.C\d\.stress$)|(^reading\.followup\.)|(\.clarify$)|(rating_probe$)|(^topic\.weakest$)"
    r"|(verification_retry$)|(\.followup$)|(^greeting\.followup)|(^learning\.followup)|(^topic\.followup)"
)
CHALLENGE_KINDS = frozenset({"followup", "clarify"})


# ============================================================================================== records


@dataclass(slots=True)
class QuestionRecord:
    id: uuid.UUID
    sequence: int
    text: str
    elapsed_s: float
    stage: str | None
    item_key: str | None
    item_kind: str | None = None
    mandatory: bool = False
    template: bool = False
    claim_ref: str | None = None
    rung: str | None = None
    mq: str | None = None
    language: str | None = None
    activity_id: str | None = None
    move: str | None = None
    scenario: str | None = None
    followup_index: int = 0
    alias: str = ""


@dataclass(slots=True)
class AnswerRecord:
    id: uuid.UUID | None
    question_id: uuid.UUID | None
    sequence: int
    text: str
    started_ms: int | None = None
    ended_ms: int | None = None
    duration_ms: int | None = None
    typed: bool = False
    stt_confidence: float | None = None
    has_audio: bool = False
    activity_id: str | None = None  # set when the answer is an activity capture (text = activity transcript)
    activity_kind: str | None = None
    alias: str = ""

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(slots=True)
class ActivityRecord:
    id: uuid.UUID
    kind: str
    stage_key: str
    status: str
    attempt: int = 1
    material_key: str = ""
    kit_version: str = "v1"
    question_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None
    phases: list[dict[str, Any]] = field(default_factory=list)
    reveal: dict[str, Any] = field(default_factory=dict)
    capture: dict[str, Any] = field(default_factory=dict)
    attempts_log: list[dict[str, Any]] = field(default_factory=list)
    transcript_live: str | None = None
    transcript_final: str | None = None
    transcript_status: str = "pending"
    transcript_source: str | None = None
    audio_media_id: uuid.UUID | None = None
    ended_reason: str | None = None

    @property
    def transcript(self) -> str:
        return (self.transcript_final or self.transcript_live or "").strip()

    @property
    def transcript_origin(self) -> str:
        """Where :attr:`transcript` comes from (labelled in the report)."""
        if self.transcript_final and self.transcript_final.strip():
            return self.transcript_source or "final_pass_live"
        if self.capture.get("typed"):
            return "typed"
        return "live_segments"

    @property
    def captured(self) -> bool:
        return bool(self.capture) and self.capture.get("end_reason") is not None


@dataclass(slots=True)
class StageRecord:
    stage_key: str
    stage_number: int
    status: str
    started_elapsed_s: float | None = None
    ended_elapsed_s: float | None = None
    budget_s: float | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationInputs:
    """Prompt-safe evaluation inputs of one intake session (no eligibility, no full name)."""

    session_id: uuid.UUID
    organization_id: uuid.UUID
    intake_id: uuid.UUID | None
    kit_version: str
    questions: list[QuestionRecord]
    answers: list[AnswerRecord]
    activities: dict[str, ActivityRecord]  # by kind
    stages: dict[str, StageRecord]  # by stage key
    onboarding: dict[str, Any]  # prompt-safe digest
    claims: list[dict[str, Any]]
    language: str | None = None
    self_rating: int | None = None
    session_status: str | None = None
    end_reason: str | None = None
    elapsed_seconds: float = 0.0

    # ------------------------------------------------------------------ lookups
    def question(self, question_id: uuid.UUID | None) -> QuestionRecord | None:
        if question_id is None:
            return None
        return self._questions_by_id().get(question_id)

    def _questions_by_id(self) -> dict[uuid.UUID, QuestionRecord]:
        return {q.id: q for q in self.questions}

    def answers_for(self, question_id: uuid.UUID) -> list[AnswerRecord]:
        return [a for a in self.answers if a.question_id == question_id]

    def answers_for_item(self, item_key: str) -> list[AnswerRecord]:
        ids = {q.id for q in self.questions if q.item_key == item_key}
        return [a for a in self.answers if a.question_id in ids and a.text.strip()]

    def item_answered(self, item_key: str) -> bool:
        return any(a.words >= 1 for a in self.answers_for_item(item_key))

    def activity_answer(self, kind: str) -> AnswerRecord | None:
        activity = self.activities.get(kind)
        if activity is None:
            return None
        return next((a for a in self.answers if a.activity_id == str(activity.id)), None)


# ============================================================================================== onboarding


def prompt_onboarding(onboarding: Mapping[str, Any] | None) -> dict[str, Any]:
    """The whitelisted, size-capped onboarding digest that may reach a prompt (contract 15.3). Accepts the stored
    ``IntakeOnboarding`` document or T3's ``onboarding_digest`` output; anything outside the whitelist (eligibility,
    full name, email) is dropped."""
    if not onboarding:
        return {}
    digest: dict[str, Any] = {}
    for key in ONBOARDING_PROMPT_FIELDS:
        value = onboarding.get(key)
        if key == "primary_language" and isinstance(value, str):
            # Stored onboarding keeps the enum value (+ free text for "other"); the digest carries the label.
            other = onboarding.get("primary_language_other")
            value = other if value == "other" and other else PRIMARY_LANGUAGE_LABELS.get(value, value)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            limit = _ONBOARDING_LIMITS.get(key, 120)
            value = value.strip()[:limit]
        elif not isinstance(value, bool | int | float):
            continue
        digest[key] = value
    return digest


def assert_onboarding_safe(digest: Mapping[str, Any]) -> None:
    """Structural privacy check (rule V8): no eligibility or identity key in the digest."""
    leaked = sorted(set(digest) & _FORBIDDEN_ONBOARDING_KEYS) + sorted(
        set(digest) - set(ONBOARDING_PROMPT_FIELDS)
    )
    if leaked:
        raise AssertionError(f"onboarding digest carries non-prompt fields: {', '.join(leaked)}")


# ============================================================================================== aliases


@dataclass(frozen=True, slots=True)
class AliasTarget:
    alias: str
    kind: str  # question | answer
    question_id: uuid.UUID | None
    answer_id: uuid.UUID | None
    stage: str | None
    item_key: str | None
    start_ms: int | None
    end_ms: int | None
    text: str  # the exact text the evaluators were shown (answers) / the question text
    activity_id: str | None = None


@dataclass(slots=True)
class AliasMap:
    targets: dict[str, AliasTarget] = field(default_factory=dict)

    def get(self, alias: str | None) -> AliasTarget | None:
        if not alias:
            return None
        return self.targets.get(alias.strip().upper())

    def answer_alias(self, answer_id: uuid.UUID | None) -> str | None:
        if answer_id is None:
            return None
        return next(
            (t.alias for t in self.targets.values() if t.kind == "answer" and t.answer_id == answer_id), None
        )


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …[cut]"


def assign_aliases(inputs: EvaluationInputs) -> AliasMap:
    """Give every question ``Q<n>`` and answer ``A<n>`` (in sequence order) and build the reverse map. Answer texts
    are clipped here once, so the validator checks quotes against exactly what the model saw."""
    aliases = AliasMap()
    for number, question in enumerate(sorted(inputs.questions, key=lambda q: q.sequence), start=1):
        question.alias = f"Q{number}"
        aliases.targets[question.alias] = AliasTarget(
            alias=question.alias,
            kind="question",
            question_id=question.id,
            answer_id=None,
            stage=question.stage,
            item_key=question.item_key,
            start_ms=int(question.elapsed_s * 1000) if question.elapsed_s else None,
            end_ms=None,
            text=question.text,
        )
    by_id = {q.id: q for q in inputs.questions}
    for number, answer in enumerate(sorted(inputs.answers, key=lambda a: a.sequence), start=1):
        answer.alias = f"A{number}"
        limit = ACTIVITY_TEXT_LIMIT if answer.activity_id else ANSWER_TEXT_LIMIT
        answer.text = _clip(answer.text, limit)
        asked = by_id.get(answer.question_id) if answer.question_id else None
        aliases.targets[answer.alias] = AliasTarget(
            alias=answer.alias,
            kind="answer",
            question_id=answer.question_id,
            answer_id=answer.id,
            stage=asked.stage if asked else _activity_stage(inputs, answer),
            item_key=asked.item_key if asked else None,
            start_ms=answer.started_ms,
            end_ms=answer.ended_ms,
            text=answer.text,
            activity_id=answer.activity_id,
        )
    return aliases


def _activity_stage(inputs: EvaluationInputs, answer: AnswerRecord) -> str | None:
    if answer.activity_kind:
        activity = inputs.activities.get(answer.activity_kind)
        return activity.stage_key if activity else None
    return None


# ============================================================================================== scopes


@dataclass(frozen=True, slots=True)
class Exchange:
    question: QuestionRecord | None
    answers: tuple[AnswerRecord, ...]
    markers: tuple[str, ...] = ()

    @property
    def stage(self) -> str | None:
        if self.question is not None:
            return self.question.stage
        return None


def _item_in(item_key: str | None, prefixes: Iterable[str]) -> bool:
    if not item_key:
        return False
    return any(item_key == p or item_key.startswith(p + ".") for p in prefixes)


def scenario_group(inputs: EvaluationInputs, question: QuestionRecord) -> str | None:
    """Scenario group letter (A-D) of a scenario item: question meta first, then the stage's ``extra.scenarios``."""
    if question.scenario:
        return question.scenario[:1].upper()
    stage = inputs.stages.get(question.stage or "")
    for entry in (stage.extra.get("scenarios") if stage else None) or []:
        key = entry.get("key") if isinstance(entry, Mapping) else entry
        item = entry.get("item_key") if isinstance(entry, Mapping) else None
        if isinstance(key, str) and key and (item is None or item == question.item_key):
            return key[:1].upper()
    return None


def _markers(
    inputs: EvaluationInputs, question: QuestionRecord | None, answers: Sequence[AnswerRecord]
) -> tuple[str, ...]:
    out: list[str] = []
    if question is not None:
        if question.item_kind in CHALLENGE_KINDS or (
            question.item_key and CHALLENGE_ITEM.search(question.item_key)
        ):
            out.append("follow-up or challenge")
        if question.rung == "stress":
            out.append("stress question")
    for answer in answers:
        if answer.activity_kind:
            activity = inputs.activities.get(answer.activity_kind)
            if activity and activity.attempts_log:
                out.append(f"restarted {len(activity.attempts_log)} time(s) after a disconnect")
        if answer.typed:
            out.append("typed answer")
    return tuple(dict.fromkeys(out))


def exchanges(
    inputs: EvaluationInputs,
    *,
    stages: Iterable[str] | None = None,
    items: Iterable[str] | None = None,
    exclude_items: Iterable[str] = (),
    activity_kinds: Iterable[str] = (),
    answered_only: bool = False,
) -> list[Exchange]:
    """Question/answer exchanges in interview order, filtered by stage and/or item-key prefix. Activity captures of
    ``activity_kinds`` are attached to their prompt question (or listed on their own when it is missing)."""
    stage_set = set(stages) if stages is not None else None
    item_list = list(items) if items is not None else None
    excluded = list(exclude_items)
    kinds = set(activity_kinds)
    other_captures = {
        id(a) for a in inputs.answers if a.activity_kind is not None and a.activity_kind not in kinds
    }
    kind_by_activity = {str(act.id): kind for kind, act in inputs.activities.items()}
    filtered = stage_set is not None or item_list is not None
    out: list[Exchange] = []
    seen_answers: set[int] = set()
    for question in sorted(inputs.questions, key=lambda q: q.sequence):
        activity_kind = kind_by_activity.get(question.activity_id or "")
        if activity_kind is not None:
            in_scope = activity_kind in kinds
        else:
            in_scope = (
                filtered
                and (stage_set is None or question.stage in stage_set)
                and (item_list is None or _item_in(question.item_key, item_list))
                and not _item_in(question.item_key, excluded)
            )
        if not in_scope:
            continue
        answers = tuple(
            a for a in inputs.answers if a.question_id == question.id and id(a) not in other_captures
        )
        if activity_kind is not None:
            capture = inputs.activity_answer(activity_kind)
            answers = (capture,) if capture is not None else ()
        if answered_only and not any(a.text.strip() for a in answers):
            continue
        seen_answers.update(id(a) for a in answers)
        out.append(Exchange(question, answers, _markers(inputs, question, answers)))
    for kind in sorted(kinds):
        capture = inputs.activity_answer(kind)
        if capture is not None and id(capture) not in seen_answers:
            out.append(Exchange(None, (capture,), _markers(inputs, None, (capture,))))
    return out


def allowed_answer_aliases(selected: Iterable[Exchange]) -> frozenset[str]:
    return frozenset(a.alias for ex in selected for a in ex.answers if a.alias)


def render_exchanges(selected: Sequence[Exchange], *, with_stage: bool = True) -> str:
    """Plain-text transcript of the exchanges (fenced by the prompt templates with the ``untrusted`` filter)."""
    lines: list[str] = []
    for exchange in selected:
        question = exchange.question
        if question is not None:
            head = f"{question.alias}"
            meta = []
            if with_stage and question.stage:
                meta.append(f"stage {question.stage}")
            if question.item_key:
                meta.append(f"item {question.item_key}")
            if exchange.markers:
                meta.extend(exchange.markers)
            lines.append(f"{head} [{'; '.join(meta)}] Interviewer: {question.text.strip()}")
        elif exchange.markers:
            lines.append(f"[{'; '.join(exchange.markers)}]")
        if not exchange.answers:
            lines.append("  (no answer recorded)")
        for answer in exchange.answers:
            timing = f", {answer.duration_ms / 1000:.0f} s" if answer.duration_ms else ""
            source = f", {answer.activity_kind} capture" if answer.activity_kind else ""
            typed = ", typed" if answer.typed else ""
            text = answer.text.strip() or "(silence / nothing recognised)"
            lines.append(f"  {answer.alias} [{answer.words} words{timing}{source}{typed}] Candidate: {text}")
        lines.append("")
    return "\n".join(lines).strip()


# ============================================================================================== scopes per evaluator

EVALUATOR_KEYS: tuple[str, ...] = (
    "communication",
    "resume_evidence",
    "technical",
    "relay",
    "structured",
    "trainability",
    "composure",
)

#: Competencies each evaluator judges (``technical_reading`` comes from ``relay`` + ``reading_metrics``).
EVALUATOR_COMPETENCIES: dict[str, tuple[str, ...]] = {
    "communication": ("communication",),
    "resume_evidence": (),
    "technical": ("technical",),
    "relay": (),
    "structured": ("structured_answer",),
    "trainability": ("trainability", "adaptability"),
    "composure": ("composure",),
}


def evaluator_exchanges(inputs: EvaluationInputs, evaluator: str) -> list[Exchange]:
    """The exchanges one evaluator receives (contract 8.3 table)."""
    if evaluator == "communication":
        return exchanges(
            inputs,
            stages=("greeting", "resume", "topic", "learning"),
            activity_kinds=("topic_card",),
            answered_only=True,
        )
    if evaluator == "resume_evidence":
        return exchanges(inputs, stages=("resume",))
    if evaluator == "technical":
        selected = [
            ex
            for ex in exchanges(inputs, stages=("resume",))
            if ex.question is not None and ex.question.rung in ("mechanics", "stress")
        ]
        selected += exchanges(inputs, items=("mandatory.mq3",))
        selected += exchanges(inputs, activity_kinds=("topic_card",))
        selected += [
            ex
            for ex in exchanges(inputs, items=("topic.scenario",))
            if ex.question is not None and scenario_group(inputs, ex.question) == "B"
        ]
        return _ordered(selected)
    if evaluator == "relay":
        return exchanges(inputs, items=READING_FOLLOWUP_KEYS, activity_kinds=("read_relay",))
    if evaluator == "structured":
        selected = exchanges(
            inputs, items=("topic.weakest", "topic.followup"), activity_kinds=("topic_card",)
        )
        selected += [
            ex
            for ex in exchanges(inputs, items=("topic.scenario",))
            if ex.question is not None and scenario_group(inputs, ex.question) == "B"
        ]
        return _ordered(selected)
    if evaluator == "trainability":
        selected = exchanges(inputs, stages=("learning",))
        selected += exchanges(inputs, items=("mandatory.mq2", "mandatory.mq4"))
        selected += exchanges(inputs, items=("close.questions", "close.answer", "close.anything_else"))
        return _ordered(selected)
    if evaluator == "composure":
        return exchanges(
            inputs,
            stages=STAGE_KEYS,
            activity_kinds=("read_relay", "topic_card"),
            answered_only=True,
        )
    raise KeyError(f"unknown evaluator {evaluator!r}")


def _ordered(selected: Iterable[Exchange]) -> list[Exchange]:
    unique: dict[Any, Exchange] = {}
    for ex in selected:
        key = ex.question.id if ex.question is not None else ("activity", tuple(a.alias for a in ex.answers))
        unique.setdefault(key, ex)
    return sorted(
        unique.values(),
        key=lambda ex: ex.question.sequence if ex.question is not None else 10**6,
    )


# ============================================================================================== digests


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def input_digest(inputs: EvaluationInputs) -> str:
    """sha256 of the canonical evaluator inputs: identical inputs -> identical digest (duplicate delivery no-op)."""
    payload = {
        "kit": inputs.kit_version,
        "questions": [
            [str(q.id), q.sequence, q.text, q.stage, q.item_key, q.item_kind, q.rung, q.claim_ref]
            for q in sorted(inputs.questions, key=lambda q: q.sequence)
        ],
        "answers": [
            [str(a.id), a.sequence, a.text, a.started_ms, a.ended_ms, a.typed, a.activity_id]
            for a in sorted(inputs.answers, key=lambda a: a.sequence)
        ],
        "activities": {
            kind: [
                str(act.id),
                act.status,
                act.attempt,
                act.material_key,
                act.transcript,
                act.transcript_status,
                act.capture.get("end_reason"),
                len(act.capture.get("segments") or []),
                len(act.capture.get("pauses") or []),
            ]
            for kind, act in sorted(inputs.activities.items())
        },
        "stages": {k: [s.status, s.items] for k, s in sorted(inputs.stages.items())},
        "onboarding": inputs.onboarding,
        "claims": inputs.claims,
        "language": inputs.language,
        "self_rating": inputs.self_rating,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def text_digest(*parts: Any) -> str:
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


# ============================================================================================== privacy (V8)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _strip_kit_original(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_kit_original(v) for k, v in value.items() if not str(k).startswith("kit_original")}
    if isinstance(value, list | tuple):
        return [_strip_kit_original(v) for v in value]
    return value


def _kit_original_blocks(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).startswith("kit_original") and item:
                yield item
            else:
                yield from _kit_original_blocks(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _kit_original_blocks(item)


# id(kit) -> (kit, texts): the kit reference keeps the id from being reused while it is cached.
_FORBIDDEN_CACHE: dict[int, tuple[IntakeKit, tuple[str, ...]]] = {}


def forbidden_kit_texts(kit: IntakeKit | None = None) -> tuple[str, ...]:
    """Every ``kit_original`` string (>= 16 chars) that does not also occur in the usable kit text."""
    kit = kit or load_intake_kit()
    cached = _FORBIDDEN_CACHE.get(id(kit))
    if cached is not None and cached[0] is kit:
        return cached[1]
    dumped = kit.model_dump(mode="json")
    usable = " ".join(" ".join(_strings(_strip_kit_original(dumped))).split()).lower()
    forbidden: set[str] = set()
    for block in _kit_original_blocks(dumped):
        payload = block.get("text") if isinstance(block, Mapping) else block
        for text in _strings(payload):
            cleaned = " ".join(text.split())
            if len(cleaned) >= 16 and cleaned.lower() not in usable:
                forbidden.add(cleaned)
    texts = tuple(sorted(forbidden))
    if len(_FORBIDDEN_CACHE) > 8:
        _FORBIDDEN_CACHE.clear()
    _FORBIDDEN_CACHE[id(kit)] = (kit, texts)
    return texts


ELIGIBILITY_MARKERS: tuple[str, ...] = (
    '"needs_sponsorship"',
    '"work_authorized"',
    '"authorized":',
    '"eligibility"',
    "legally authorised to work",
    "legally authorized to work",
    "need sponsorship now or in the future",
)


def eligibility_markers(kit: IntakeKit | None = None, *, country: str | None = None) -> tuple[str, ...]:
    """Strings that must never appear in a model payload: the MQ1 questions as worded for this interview (they are
    asked only on the onboarding form) and the stored eligibility field names."""
    kit = kit or load_intake_kit()
    markers = list(ELIGIBILITY_MARKERS)
    mq1 = kit.mandatory_question("mq1")
    for line in mq1.ask:
        text = line.replace("{country}", country) if country else line
        for part in re.split(r"\{[a-z_]+\}", text):
            part = " ".join(part.split())
            if len(part) >= 24:
                markers.append(part)
    return tuple(dict.fromkeys(markers))


class PromptPrivacyError(AssertionError):
    """A prompt carried MQ1 eligibility or kit_original text (validator rule V8). A bug: never sent."""


def _message_text(messages: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(p.get("text", "")) for p in content if isinstance(p, Mapping))
    return "\n".join(parts)


#: Fence labels of candidate-provided text in evaluator prompts (answers, résumé claims, the whitelisted form
#: digest). Their content is the candidate's own words (data): it is excluded from the eligibility-marker check,
#: because a candidate may mention their own situation unprompted. The form digest is protected structurally
#: (:func:`assert_onboarding_safe`).
CANDIDATE_SPEECH_FENCES: tuple[str, ...] = (
    "interview_answers",
    "resume_claims",
    "candidate_form",
    "interview_notes",
)


def _strip_fences(text: str, labels: Iterable[str]) -> str:
    for label in labels:
        text = re.sub(rf"<{label}>.*?</{label}>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    return text


def assert_prompt_safe(
    messages: Sequence[Mapping[str, Any]],
    *,
    kit: IntakeKit | None = None,
    country: str | None = None,
    extra_forbidden: Iterable[str] = (),
) -> None:
    """Rule V8: raise :class:`PromptPrivacyError` when a prompt carries MQ1 wording or eligibility field names
    (outside the fenced candidate speech) or ``kit_original`` text anywhere. Structural guarantees come first (the
    inputs never hold eligibility answers; the onboarding digest is whitelisted); this is the last line of defence
    against a builder bug."""
    full = _message_text(messages)
    ours = " ".join(_strip_fences(full, CANDIDATE_SPEECH_FENCES).split()).lower()
    for marker in (*eligibility_markers(kit, country=country), *extra_forbidden):
        cleaned = " ".join(str(marker).split()).lower()
        if cleaned and cleaned in ours:
            raise PromptPrivacyError("prompt contains MQ1 / eligibility text")
    everything = " ".join(full.split()).lower()
    for original in forbidden_kit_texts(kit):
        if original.lower() in everything:
            raise PromptPrivacyError("prompt contains kit_original text")


# ============================================================================================== DB loading


NAME_PLACEHOLDER = "[name]"


def name_redactor(names: Sequence[str]) -> Any:
    """``text -> text`` replacing the candidate's full name (any case) and each capitalised name part of 3+ letters
    (as written) with ``[name]``. Lower-case words are left alone ("will" for a candidate named Will)."""
    full: set[str] = set()
    parts: set[str] = set()
    for name in names:
        cleaned = " ".join(str(name or "").split())
        if len(cleaned) >= 3:
            full.add(cleaned)
        for token in re.split(r"[\s\-]+", cleaned):
            token = token.strip(".,'\"()")
            if len(token) >= 3 and token[:1].isupper():
                parts.add(token)
    if not full and not parts:
        return lambda text: text
    full_rx = (
        re.compile(
            r"\b(" + "|".join(re.escape(n) for n in sorted(full, key=len, reverse=True)) + r")\b", re.I
        )
        if full
        else None
    )
    part_rx = (
        re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(parts, key=len, reverse=True)) + r")\b")
        if parts
        else None
    )

    def redact(text: str) -> str:
        if not text:
            return text
        if full_rx is not None:
            text = full_rx.sub(NAME_PLACEHOLDER, text)
        if part_rx is not None:
            text = part_rx.sub(NAME_PLACEHOLDER, text)
        return text

    return redact


def _intake_meta(decision: Mapping[str, Any] | None) -> dict[str, Any]:
    meta = (decision or {}).get("intake")
    return dict(meta) if isinstance(meta, Mapping) else {}


def _stage_from_segment(segment: str | None) -> str | None:
    if segment and segment.startswith(INTAKE_SEGMENT_PREFIX):
        return segment[len(INTAKE_SEGMENT_PREFIX) :] or None
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_inputs(
    *,
    session: Any,
    intake: Any | None,
    questions: Sequence[Any],
    answers: Sequence[Any],
    activities: Sequence[Any],
    stages: Sequence[Any],
    onboarding_digest: Mapping[str, Any] | None = None,
    kit: IntakeKit | None = None,
    names: Sequence[str] = (),
) -> EvaluationInputs:
    """Assemble :class:`EvaluationInputs` from ORM rows (or any objects with the same attributes). Pure.

    ``names``: the candidate's name(s); they are replaced by ``[name]`` in every question and answer text
    (a name is a proxy for protected traits and is never evidence)."""
    kit = kit or load_intake_kit()
    redact = name_redactor(names)
    question_records: list[QuestionRecord] = []
    for row in questions:
        meta = _intake_meta(row.decision)
        stage = meta.get("stage") or _stage_from_segment(row.segment)
        if stage is None:
            continue  # not an intake question
        question_records.append(
            QuestionRecord(
                id=row.id,
                sequence=int(row.sequence),
                text=redact((row.spoken_text or row.text or "").strip()),
                elapsed_s=float(row.elapsed_seconds or 0.0),
                stage=stage,
                item_key=meta.get("item_key"),
                item_kind=meta.get("item_kind"),
                mandatory=bool(meta.get("mandatory", False)),
                template=bool(meta.get("template", False)),
                claim_ref=meta.get("claim_ref"),
                rung=meta.get("rung"),
                mq=meta.get("mq"),
                language=meta.get("language"),
                activity_id=str(meta["activity_id"]) if meta.get("activity_id") else None,
                move=meta.get("move"),
                scenario=meta.get("scenario") if isinstance(meta.get("scenario"), str) else None,
                followup_index=int(meta.get("followup_index") or 0),
            )
        )
    question_ids = {q.id for q in question_records}
    activity_records: dict[str, ActivityRecord] = {}
    for row in activities:
        record = ActivityRecord(
            id=row.id,
            kind=row.kind,
            stage_key=row.stage_key,
            status=row.status,
            attempt=int(row.attempt or 1),
            material_key=str(row.material_key or ""),
            kit_version=row.kit_version or kit.version,
            question_id=row.question_id,
            answer_id=row.answer_id,
            phases=list(row.phases or []),
            reveal=dict(row.reveal or {}),
            capture=dict(row.capture or {}),
            attempts_log=list(row.attempts_log or []),
            transcript_live=row.transcript_live,
            transcript_final=row.transcript_final,
            transcript_status=row.transcript_status or "pending",
            transcript_source=row.transcript_source,
            audio_media_id=row.audio_media_id,
            ended_reason=row.ended_reason,
        )
        activity_records[record.kind] = record
    by_answer_id = {act.answer_id: act for act in activity_records.values() if act.answer_id is not None}
    answer_records: list[AnswerRecord] = []
    for row in answers:
        activity = by_answer_id.get(row.id)
        if row.question_id not in question_ids and activity is None:
            continue
        text = redact(activity.transcript if activity is not None else (row.transcript_text or ""))
        answer_records.append(
            AnswerRecord(
                id=row.id,
                question_id=row.question_id,
                sequence=int(row.sequence),
                text=text,
                started_ms=_int_or_none(row.started_ms),
                ended_ms=_int_or_none(row.ended_ms),
                duration_ms=_int_or_none(row.duration_ms),
                typed=bool(row.typed),
                stt_confidence=row.stt_confidence,
                has_audio=bool(row.audio_storage_key),
                activity_id=str(activity.id) if activity is not None else None,
                activity_kind=activity.kind if activity is not None else None,
            )
        )
    # An activity whose capture has no answer row yet still contributes its transcript.
    present = {a.activity_id for a in answer_records if a.activity_id}
    next_sequence = max((a.sequence for a in answer_records), default=0) + 1000
    for activity in activity_records.values():
        if str(activity.id) in present or not activity.transcript:
            continue
        capture = activity.capture
        start_ms = _int_or_none(capture.get("started_ms"))
        end_ms = _int_or_none(capture.get("ended_ms"))
        answer_records.append(
            AnswerRecord(
                id=None,
                question_id=activity.question_id,
                sequence=next_sequence,
                text=redact(activity.transcript),
                started_ms=start_ms,
                ended_ms=end_ms,
                duration_ms=end_ms - start_ms if start_ms is not None and end_ms is not None else None,
                typed=bool(capture.get("typed")),
                activity_id=str(activity.id),
                activity_kind=activity.kind,
            )
        )
        next_sequence += 1
    stage_records = {
        row.stage_key: StageRecord(
            stage_key=row.stage_key,
            stage_number=int(row.stage_number),
            status=row.status,
            started_elapsed_s=row.started_elapsed_s,
            ended_elapsed_s=row.ended_elapsed_s,
            budget_s=row.budget_s,
            items=list(row.items or []),
            extra=dict(row.extra or {}),
        )
        for row in stages
    }
    digest = prompt_onboarding(onboarding_digest)
    assert_onboarding_safe(digest)
    resume_extra = stage_records["resume"].extra if "resume" in stage_records else {}
    claims = [
        {
            "ref": str(c.get("ref") or ""),
            "text": str(c.get("text") or "")[:400],
            "probe": c.get("probe"),
            "source": c.get("source"),
            "source_quote": (str(c.get("source_quote"))[:300] if c.get("source_quote") else None),
        }
        for c in (resume_extra.get("claims") or [])
        if isinstance(c, Mapping) and c.get("ref")
    ]
    mandatory_extra = stage_records["mandatory"].extra if "mandatory" in stage_records else {}
    language = mandatory_extra.get("language")
    if isinstance(language, Mapping):
        language = language.get("key") or language.get("language")
    if not language:
        language = next((q.language for q in question_records if q.language), None)
    self_rating = _int_or_none(digest.get("self_rating"))
    if self_rating is None:
        self_rating = _int_or_none(mandatory_extra.get("self_rating"))
    return EvaluationInputs(
        session_id=session.id,
        organization_id=session.organization_id,
        intake_id=getattr(intake, "id", None),
        kit_version=kit.version,
        questions=question_records,
        answers=answer_records,
        activities=activity_records,
        stages=stage_records,
        onboarding=digest,
        claims=claims,
        language=str(language) if language else None,
        self_rating=self_rating,
        session_status=session.status,
        end_reason=session.end_reason,
        elapsed_seconds=float(session.elapsed_seconds or 0.0),
    )


async def load_evaluation_inputs(db: AsyncSession, session_id: uuid.UUID) -> EvaluationInputs:
    """Load every row the evaluators read and build the prompt-safe inputs (read-only)."""
    from sqlalchemy import select

    from app.db.models import Answer, Candidate, InterviewSession, Question
    from app.db.models.intake import IntakeActivityResult, IntakeInterview, IntakeStageResult

    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise LookupError(f"session {session_id} not found")
    intake = await db.scalar(
        select(IntakeInterview).where(IntakeInterview.interview_id == session.interview_id)
    )
    questions = (
        await db.scalars(
            select(Question).where(Question.session_id == session_id).order_by(Question.sequence)
        )
    ).all()
    answers = (
        await db.scalars(select(Answer).where(Answer.session_id == session_id).order_by(Answer.sequence))
    ).all()
    activities = (
        await db.scalars(select(IntakeActivityResult).where(IntakeActivityResult.session_id == session_id))
    ).all()
    stages = (
        await db.scalars(
            select(IntakeStageResult)
            .where(IntakeStageResult.session_id == session_id)
            .order_by(IntakeStageResult.stage_number)
        )
    ).all()
    digest = onboarding_digest_for(intake)
    candidate = await db.get(Candidate, session.candidate_id)
    names = [
        n
        for n in (
            getattr(candidate, "full_name", None),
            ((getattr(intake, "onboarding", None) or {}).get("full_name") if intake is not None else None),
        )
        if n
    ]
    return build_inputs(
        session=session,
        intake=intake,
        questions=questions,
        answers=answers,
        activities=activities,
        stages=stages,
        onboarding_digest=digest,
        names=names,
    )


def onboarding_digest_for(intake: Any | None) -> dict[str, Any]:
    """Prompt-safe digest of the intake row's onboarding (T3's ``onboarding_digest`` when available, always
    re-whitelisted here)."""
    onboarding = getattr(intake, "onboarding", None) if intake is not None else None
    if not onboarding:
        return {}
    try:
        from app.services.intake.onboarding import onboarding_digest  # T3

        digest = onboarding_digest(onboarding)
        if hasattr(digest, "model_dump"):
            digest = digest.model_dump(mode="json")
    except Exception:  # noqa: BLE001 - T3 helper missing or rejecting old data: fall back to the whitelist
        digest = onboarding
    return prompt_onboarding(digest if isinstance(digest, Mapping) else onboarding)


__all__ = [
    "ACTIVITY_TEXT_LIMIT",
    "ANSWER_TEXT_LIMIT",
    "CANDIDATE_SPEECH_FENCES",
    "CLAIM_DEPTH_CUES",
    "EVALUATOR_COMPETENCIES",
    "EVALUATOR_KEYS",
    "LADDER_RUNG",
    "ONBOARDING_PROMPT_FIELDS",
    "READING_FOLLOWUP_KEYS",
    "ActivityRecord",
    "AliasMap",
    "AliasTarget",
    "AnswerRecord",
    "EvaluationInputs",
    "Exchange",
    "PromptPrivacyError",
    "QuestionRecord",
    "StageRecord",
    "allowed_answer_aliases",
    "assert_onboarding_safe",
    "assert_prompt_safe",
    "assign_aliases",
    "build_inputs",
    "eligibility_markers",
    "evaluator_exchanges",
    "exchanges",
    "forbidden_kit_texts",
    "input_digest",
    "load_evaluation_inputs",
    "name_redactor",
    "onboarding_digest_for",
    "prompt_onboarding",
    "render_exchanges",
    "scenario_group",
    "text_digest",
]
