"""Pure state transitions applied by the engine (no I/O).

* :func:`record_answer` - a candidate turn becomes an ``AnswerRecord`` and a
  conversation entry; the pending question is marked answered.
* :func:`apply_decision` - a validated (post-checked) turn decision updates
  questions, difficulty counts, current competency/topic, follow-up counters,
  claim/contradiction status, pending follow-ups, utterance tracking.
* :func:`apply_evaluation` - an ``evaluation.ready`` payload (computed by the
  ``answer.evaluate`` job) updates derived competency state, claims,
  contradictions and pending follow-ups; flow fields are kept.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.domain.enums import AnswerQuality, ClaimStatus, Difficulty, TurnAction
from app.schemas.ai import TurnDecision
from app.services.interview import difficulty as diff
from app.services.interview.config import get_engine_config
from app.services.interview.contradictions import is_external_conflict, is_similar, neutral_description
from app.services.interview.grounding import GroundingContext, inconsistency_issue
from app.services.interview.memory import append_exchange
from app.services.interview.policy import (
    FOLLOWUP_ACTIONS,
    NON_QUESTION_ACTIONS,
    UNCOUNTED_ACTIONS,
    PolicyDirective,
)
from app.services.interview.state import (
    AnswerRecord,
    ClaimState,
    CompetencyState,
    ContradictionState,
    ExchangeTurn,
    InterviewState,
    PendingFollowup,
    QuestionRecord,
    UtteranceRecord,
)

log = get_logger(__name__)

#: CompetencyState fields that are derived from evidence (replaced by evaluations).
DERIVED_FIELDS = (
    "score_estimate",
    "confidence",
    "evidence_count",
    "positive_evidence",
    "negative_evidence",
    "positive_evidence_ids",
    "negative_evidence_ids",
    "dimensions_covered",
    "unanswered_questions",
    "sufficient_evidence",
    "score_history",
    "last_updated_s",
)
MAX_PENDING_FOLLOWUPS = 12
MAX_CONTRADICTIONS = 20


def add_utterance(state: InterviewState, record: UtteranceRecord) -> None:
    state.utterances.append(record)
    limit = get_engine_config().utterances_max
    if len(state.utterances) > limit:
        del state.utterances[: len(state.utterances) - limit]


def record_answer(
    state: InterviewState,
    *,
    answer_id: str,
    text: str,
    typed: bool,
    started_s: float,
    ended_s: float,
    interrupted_ai: bool,
    special: str | None,
    quality: AnswerQuality | None,
    quality_source: str | None,
) -> AnswerRecord:
    record = AnswerRecord(
        id=answer_id,
        question_id=state.current_question_id,
        sequence=len(state.answers) + 1,
        text=text,
        quality=quality,
        typed=typed,
        started_s=started_s,
        ended_s=ended_s,
        interrupted_ai=interrupted_ai,
        special=special,
        word_count=len(text.split()),
        quality_source=quality_source,
    )
    state.answers.append(record)
    q = state.current_question()
    if q is not None:
        q.answered = True
    append_exchange(
        state,
        ExchangeTurn(
            speaker="candidate",
            text=text,
            at_s=ended_s,
            question_id=state.current_question_id,
            answer_id=answer_id,
        ),
    )
    state.last_final_transcript = text
    state.partial_transcript = ""
    state.awaiting_answer = False
    state.awaiting_since_s = None
    state.silence_prompts = 0
    return record


def record_interviewer_text(
    state: InterviewState, *, text: str, at_s: float, question_id: str | None, interrupted: bool = False
) -> None:
    append_exchange(
        state,
        ExchangeTurn(
            speaker="interviewer", text=text, at_s=at_s, question_id=question_id, interrupted=interrupted
        ),
    )


def _ensure_competency(state: InterviewState, key: str) -> CompetencyState:
    comp = state.competencies.get(key)
    if comp is None:
        rc = next((c for c in state.round_config.competencies if c.key == key), None)
        comp = CompetencyState(
            key=key,
            name=rc.name if rc else None,
            weight=rc.weight if rc else 1.0,
            required=rc.required if rc else False,
            current_difficulty=(
                rc.target_difficulty
                if rc and rc.target_difficulty
                else state.round_config.expected_difficulty
            ),
            focus_areas=list(rc.focus_areas) if rc else [],
        )
        state.competencies[key] = comp
    return comp


def apply_decision(
    state: InterviewState,
    decision: TurnDecision,
    directive: PolicyDirective | None,
    *,
    utterance_id: str,
    question_id: str | None,
    created_question: bool,
    elapsed_s: float,
    kind: str,
    bank_question_id: str | None = None,
    parent_question_id: str | None = None,
    transcript_id: str | None = None,
    coding_question_id: str | None = None,
) -> None:
    action = decision.action
    key = decision.competency_key
    state.turn_index += 1

    # Last answer's quality as reported by the model (unless already known).
    reported = decision.answer_quality
    if state.answers and reported is not None:
        last = state.answers[-1]
        if last.quality is None:
            last.quality = reported
            last.quality_source = "model"
    last_quality = state.answers[-1].quality if state.answers else None

    # Difficulty adaptation of the competency that was just answered.
    previous_key = state.current_competency
    if (
        previous_key
        and previous_key in state.competencies
        and last_quality is not None
        and not (directive and directive.first_question)
    ):
        prev = state.competencies[previous_key]
        prev.last_quality = last_quality
        adapted = diff.adapt(
            prev.current_difficulty,
            last_quality,
            prev.weak_answers_at_level,
            adaptive=state.round_config.question_strategy.adaptive_difficulty,
        )
        prev.weak_answers_at_level = adapted.weak_at_level
        if adapted.changed and not created_question:
            prev.current_difficulty = adapted.difficulty

    if created_question and key:
        comp = _ensure_competency(state, key)
        same_topic = key == previous_key and (
            action in FOLLOWUP_ACTIONS
            or (
                action == TurnAction.INCREASE_DIFFICULTY
                and (decision.topic is None or decision.topic == state.current_topic)
            )
        )
        if action not in UNCOUNTED_ACTIONS:
            state.difficulty_counts[decision.difficulty.value] = (
                state.difficulty_counts.get(decision.difficulty.value, 0) + 1
            )
            comp.difficulty_seen[decision.difficulty.value] = (
                comp.difficulty_seen.get(decision.difficulty.value, 0) + 1
            )
            comp.questions_asked += 1
        if comp.current_difficulty != decision.difficulty:
            comp.weak_answers_at_level = 0
        comp.current_difficulty = decision.difficulty
        if same_topic:
            comp.followups_on_current_topic += 1
        else:
            comp.followups_on_current_topic = 0
            if previous_key and previous_key != key and previous_key in state.competencies:
                state.competencies[previous_key].followups_on_current_topic = 0
        if not same_topic:
            state.simplify_attempts = 0
        elif action in (TurnAction.SIMPLIFY, TurnAction.CLARIFY_QUESTION):
            state.simplify_attempts += 1
        state.current_competency = key
        state.current_topic = decision.topic or state.current_topic if same_topic else decision.topic
        state.current_difficulty = decision.difficulty
    elif action == TurnAction.CLARIFY_QUESTION:
        state.simplify_attempts += 1

    if created_question and question_id:
        state.questions.append(
            QuestionRecord(
                id=question_id,
                sequence=len(state.questions) + 1,
                action=action,
                text=decision.speech,
                focus=decision.question_focus,
                competency_key=key,
                topic=decision.topic,
                difficulty=decision.difficulty,
                parent_id=parent_question_id,
                claim_id=decision.claim_id,
                bank_question_id=bank_question_id,
                asked_at_s=elapsed_s,
                kind=kind,
                expected_evidence=list(decision.expected_evidence),
                coding_question_id=coding_question_id,
            )
        )
        state.current_question_id = question_id
        state.silence_prompts = 0

    # Claims: the asked claim is now in progress.
    if decision.claim_id and decision.claim_id in state.claims:
        claim = state.claims[decision.claim_id]
        if claim.status == ClaimStatus.UNVERIFIED:
            claim.status = ClaimStatus.IN_PROGRESS
        claim.attempts += 1
        if question_id and question_id not in claim.question_ids:
            claim.question_ids.append(question_id)

    # Contradictions: the probed one is now being verified.
    if action == TurnAction.VERIFY_CONTRADICTION:
        target_id = directive.open_contradiction.id if directive and directive.open_contradiction else None
        for c in state.contradictions:
            if (target_id and c.id == target_id) or (target_id is None and c.status == "open"):
                c.status = "verifying"
                break

    # Pending follow-ups from deep evaluation are consumed by a follow-up on that competency.
    if action in FOLLOWUP_ACTIONS and key:
        for i, p in enumerate(state.pending_followups):
            if p.competency_key == key:
                del state.pending_followups[i]
                break

    # Model-reported observations about the last answer.
    if decision.possible_contradiction and state.answers:
        _add_contradiction(
            state,
            description=decision.possible_contradiction,
            refs=[state.answers[-1].id],
            severity="low",
        )
    if decision.new_claims and key:
        claimed_comp = state.competencies.get(key)
        if claimed_comp is not None and not claimed_comp.sufficient_evidence:
            claim_text = decision.new_claims[0]
            if not any(p.text.endswith(claim_text) for p in state.pending_followups):
                state.pending_followups.append(
                    PendingFollowup(
                        text=f"Ask for specifics about: {claim_text}"[:300],
                        reason="new_claim",
                        competency_key=key,
                        priority=2,
                        source_answer_id=state.answers[-1].id if state.answers else None,
                    )
                )
                del state.pending_followups[:-MAX_PENDING_FOLLOWUPS]

    if action == TurnAction.INVITE_CANDIDATE_QUESTIONS:
        state.candidate_questions_invited = True
    if action == TurnAction.CLOSE_INTERVIEW:
        state.closing_started = True

    add_utterance(
        state,
        UtteranceRecord(
            id=utterance_id,
            kind=kind,
            action=action,
            question_id=question_id if (created_question or action in NON_QUESTION_ACTIONS) else None,
            text=decision.speech,
            at_s=elapsed_s,
            transcript_id=transcript_id,
        ),
    )
    record_interviewer_text(state, text=decision.speech, at_s=elapsed_s, question_id=question_id)
    state.pending_time_notice = None
    if directive is not None and directive.time.warning:
        threshold = 300 if directive.time.warning == "5_minutes" else 60
        if threshold not in state.time_warnings:
            state.time_warnings.append(threshold)


def find_similar_contradiction(state: InterviewState, description: str) -> ContradictionState | None:
    for c in state.contradictions:
        if c.description.strip().lower() == description.strip().lower() or is_similar(
            c.description, description
        ):
            return c
    return None


def _add_contradiction(
    state: InterviewState,
    *,
    description: str,
    refs: list[str],
    severity: str,
    sides: list[str] | None = None,
) -> ContradictionState | None:
    """Record an inconsistency once. The same discrepancy described in other words
    merges into the existing record (refs are added; severity can only rise while it
    is still open) - it never reopens or duplicates a record that was already probed.
    A record that is not grounded in the candidate's own words (``grounding.inconsistency_issue``;
    ``sides`` = the evaluator's statement and what it conflicts with) is dropped and counted as a
    deviation."""
    description = neutral_description(description)[:300]
    if not description or is_external_conflict(description):
        return None
    issue = inconsistency_issue(GroundingContext.from_state(state), description, sides=sides or ())
    if issue is not None:
        state.deviations += 1
        log.info("engine.ungrounded_contradiction_dropped", session_id=state.session_id, reason=issue[:160])
        return None
    existing = find_similar_contradiction(state, description)
    if existing is not None:
        if existing.status == "open" and _rank(severity) > _rank(existing.severity):
            existing.severity = severity
        for ref in refs:
            if ref not in existing.refs:
                existing.refs.append(ref)
        return existing
    item = ContradictionState(
        id=f"x{uuid.uuid4().hex[:8]}", description=description, refs=refs, severity=severity
    )
    state.contradictions.append(item)
    del state.contradictions[:-MAX_CONTRADICTIONS]
    return item


def _rank(severity: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(severity, 0)


def merge_derived(state: InterviewState, states: dict[str, CompetencyState]) -> None:
    """Replace evidence-derived competency fields; keep engine flow fields."""
    for key, incoming in states.items():
        current = state.competencies.get(key)
        if current is None:
            state.competencies[key] = incoming
            continue
        for name in DERIVED_FIELDS:
            setattr(current, name, getattr(incoming, name))
        for level, count in (incoming.difficulty_seen or {}).items():
            current.difficulty_seen[level] = max(current.difficulty_seen.get(level, 0), count)


def apply_evaluation(state: InterviewState, payload: dict[str, Any]) -> bool:
    """Apply one ``answer.evaluate`` result payload. Returns True if applied."""
    answer_id = str(payload.get("answer_id") or "")
    if not answer_id:
        return False
    competencies = payload.get("competencies") or {}
    for key, data in competencies.items():
        try:
            incoming = CompetencyState.model_validate(data)
        except ValueError:
            continue
        current = state.competencies.get(key)
        if current is None:
            state.competencies[key] = incoming
            continue
        for name in DERIVED_FIELDS:
            setattr(current, name, getattr(incoming, name))
        for level, count in (incoming.difficulty_seen or {}).items():
            current.difficulty_seen[level] = max(current.difficulty_seen.get(level, 0), count)
    for item in payload.get("claims") or []:
        cid = str(item.get("claim_id") or "")
        claim: ClaimState | None = state.claims.get(cid)
        if claim is None:
            continue
        try:
            status = ClaimStatus(item.get("status"))
        except ValueError:
            continue
        claim.status = status
        claim.note = (item.get("note") or claim.note or "")[:300] or None
        for eid in item.get("evidence_ids") or []:
            if eid not in claim.evidence_ids:
                claim.evidence_ids.append(str(eid))
    reported = [str(item.get("description") or "") for item in payload.get("contradictions") or []]
    # A probed inconsistency is settled by the evaluation of the answer to the probe: the
    # evaluator restates it when the answer does not reconcile it (confirmed), otherwise
    # it is resolved. Settled before new findings are merged, so a restatement cannot
    # count as a new record.
    if payload.get("question_action") == TurnAction.VERIFY_CONTRADICTION.value:
        for c in state.contradictions:
            if c.status == "verifying":
                restated = any(is_similar(c.description, text) for text in reported if text)
                c.status = "confirmed" if restated else "resolved"
    if payload.get("resolved_contradictions"):
        for cid in payload["resolved_contradictions"]:
            for c in state.contradictions:
                if c.id == cid:
                    c.status = "resolved"
    for item in payload.get("contradictions") or []:
        _add_contradiction(
            state,
            description=str(item.get("description") or "")[:300],
            refs=[str(r) for r in item.get("refs") or [answer_id]],
            severity=str(item.get("severity") or "low"),
            sides=[str(x) for x in item.get("sides") or [] if x],
        )
    existing = {p.text for p in state.pending_followups}
    for item in payload.get("followups") or []:
        text = str(item.get("text") or "")[:300]
        if not text or text in existing:
            continue
        state.pending_followups.append(
            PendingFollowup(
                text=text,
                reason=str(item.get("reason") or "evaluation")[:200],
                competency_key=item.get("competency_key"),
                priority=int(item.get("priority") or 3),
                source_answer_id=answer_id,
            )
        )
    del state.pending_followups[:-MAX_PENDING_FOLLOWUPS]
    quality = payload.get("quality")
    for a in state.answers:
        if a.id == answer_id:
            a.evaluated = True
            if quality:
                try:
                    a.quality = AnswerQuality(quality)
                    a.quality_source = "evaluation"
                except ValueError:
                    pass
    if answer_id in state.pending_evaluations:
        state.pending_evaluations.remove(answer_id)
    covered = set(state.skills_covered)
    for key, comp in state.competencies.items():
        if comp.evidence_count > 0:
            covered.add(key)
    state.skills_covered = sorted(covered)
    state.skills_uncovered = sorted(k for k in state.round_config.competency_keys() if k not in covered)
    return True


def mark_delivered(
    state: InterviewState, utterance_id: str, spoken_text: str, *, elapsed_s: float
) -> UtteranceRecord | None:
    record = state.utterance_by_id(utterance_id)
    if record is None:
        return None
    record.delivered = True
    if spoken_text and not record.interrupted:
        record.spoken_text = spoken_text
    if record.question_id:
        q = state.question_by_id(record.question_id)
        if q is not None:
            q.delivered = True
    if record.kind in ("question", "followup", "resume", "transition", "comment") or (
        record.action is not None
        and record.action not in UNCOUNTED_ACTIONS | {TurnAction.ACKNOWLEDGE_AND_WAIT}
    ):
        state.awaiting_answer = True
        state.awaiting_since_s = elapsed_s
    elif record.action in (
        TurnAction.CLARIFY_QUESTION,
        TurnAction.REDIRECT,
        TurnAction.ANSWER_CANDIDATE_QUESTION,
        TurnAction.INVITE_CANDIDATE_QUESTIONS,
    ):
        state.awaiting_answer = True
        state.awaiting_since_s = elapsed_s
    return record


def difficulty_of(value: Any) -> Difficulty:
    try:
        return Difficulty(value)
    except ValueError:
        return Difficulty.MEDIUM


__all__ = [
    "DERIVED_FIELDS",
    "add_utterance",
    "find_similar_contradiction",
    "apply_decision",
    "apply_evaluation",
    "mark_delivered",
    "merge_derived",
    "record_answer",
    "record_interviewer_text",
]
