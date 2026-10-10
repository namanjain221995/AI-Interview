"""Deep answer evaluation (``answer.evaluate`` job, ENGINE_DESIGN §8).

Inputs: the question (text, competency, difficulty, expected evidence, bank
rubric), the answer, the last exchanges, evidence already recorded for the
competency, resume claims touched and earlier answers on the same topic
(consistency check), plus the round's competencies.

Output ``AnswerEvaluation`` (main model) is cleaned (competency keys mapped
into the round, quotes checked against the answer, unknown claim ids
dropped) and applied idempotently by answer id:

* ``answer_evaluations`` row (unique per answer; ``is_fallback`` for the
  neutral fallback, which is flagged for human review),
* ``competency_evidence`` rows with embeddings and transcript references,
* competency state recomputed from all evidence via the scoring contract,
  scores/snapshots (scoring's ``recompute_session_scores`` when available,
  otherwise ``score_snapshots`` written here),
* the result payload stored in ``session:{id}:evals`` (applied by the live
  engine before its next decision) and ``evaluation.ready`` published on
  ``session:{id}:bus``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from rapidfuzz import fuzz
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIOutputInvalid, AIServices, CallContext
from app.ai.registry import get_ai
from app.ai.structured import describe_schema
from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.core.queue import publish_session_event
from app.db.models import (
    Answer,
    BankQuestion,
    CodingQuestion,
    CodingSession,
    CompetencyEvidence,
    InterviewSession,
    Question,
    ScoreSnapshot,
    Transcript,
)
from app.db.models import (
    AnswerEvaluation as AnswerEvaluationRow,
)
from app.db.session import session_scope
from app.domain.enums import (
    AnswerQuality,
    ClaimStatus,
    Difficulty,
    EvidenceSignal,
    EvidenceSource,
    ModelTask,
    SessionStatus,
)
from app.schemas.ai import AnswerEvaluation, CodingEvaluation, DimensionScores, EvidenceItem
from app.schemas.rounds import RoundConfig, load_round_config
from app.services.interview.adapters import ScoringPort, get_scoring
from app.services.interview.config import EngineConfig, get_engine_config
from app.services.interview.contradictions import neutral_description
from app.services.interview.grounding import GroundingContext, inconsistency_issue
from app.services.interview.policy import FLOW_ONLY_ACTION_VALUES, is_coding_competency
from app.services.interview.prompts import EVALUATION_SYSTEM, EVALUATION_USER
from app.services.interview.records import as_uuid, log_event
from app.services.interview.scoring_fallback import assistance_factor, evidence_record_from_row
from app.services.interview.state import CompetencyState, InterviewState
from app.services.interview.transitions import merge_derived
from app.services.retrieval import embed_documents
from app.services.scoring.contracts import Observation

log = get_logger(__name__)

QUOTE_MATCH_THRESHOLD = 85
ENDED_FOR_JOBS = frozenset(
    {SessionStatus.COMPLETING, SessionStatus.COMPLETED, SessionStatus.ABORTED, SessionStatus.FAILED}
)
QUALITY_SIGNAL: dict[AnswerQuality, tuple[EvidenceSignal, float]] = {
    AnswerQuality.STRONG: (EvidenceSignal.POSITIVE, 0.8),
    AnswerQuality.ADEQUATE: (EvidenceSignal.POSITIVE, 0.55),
    AnswerQuality.PARTIAL: (EvidenceSignal.NEUTRAL, 0.4),
    AnswerQuality.VAGUE: (EvidenceSignal.NEGATIVE, 0.4),
    AnswerQuality.INCORRECT: (EvidenceSignal.NEGATIVE, 0.6),
    AnswerQuality.NO_ANSWER: (EvidenceSignal.NEGATIVE, 0.5),
    AnswerQuality.OFF_TOPIC: (EvidenceSignal.NEGATIVE, 0.3),
}
FALLBACK_CONFIDENCE = 0.2
#: Candidate turns that change the flow rather than answer (never evaluated).
FLOW_TURN_QUALITIES = frozenset(
    {
        AnswerQuality.CLARIFICATION_REQUEST,
        AnswerQuality.CANDIDATE_QUESTION,
        AnswerQuality.WANTS_TIME,
        AnswerQuality.WANTS_HINT,
    }
)


def _accepts_kw(fn: Any, name: str) -> bool:
    import inspect

    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def evals_key(session_id: str) -> str:
    return f"session:{session_id}:evals"


def neutral_evaluation(quality: AnswerQuality | None = None, *, reason: str = "") -> AnswerEvaluation:
    q = quality if quality in QUALITY_SIGNAL else AnswerQuality.PARTIAL
    return AnswerEvaluation(
        overall_quality=q,
        scores=DimensionScores(),
        summary=(
            "Automatic evaluation was unavailable for this answer; it is flagged for human review."
            + (f" ({reason})" if reason else "")
        )[:600],
        evaluation_confidence=FALLBACK_CONFIDENCE,
    )


def quote_in_answer(quote: str | None, answer: str) -> bool:
    if not quote:
        return False
    q = quote.strip().lower()
    a = answer.lower()
    if not q:
        return False
    if q in a:
        return True
    return fuzz.partial_ratio(q, a) >= QUOTE_MATCH_THRESHOLD


#: Dimensions that describe the answer rather than job-relevant skill: recorded, never scored.
NON_SKILL_DIMENSIONS = frozenset({"consistency", "relevance", "resume_consistency"})
COMMUNICATION_DIMENSIONS = frozenset({"communication_clarity", "communication", "clarity"})
COMMUNICATION_KEY = "communication"


def clean_evaluation(
    evaluation: AnswerEvaluation,
    *,
    answer_text: str,
    round_keys: Sequence[str],
    question_keys: Sequence[str],
    known_claims: Sequence[str],
) -> AnswerEvaluation:
    """Map evidence into the round and keep it fair:

    * unknown competency keys go to the question's competency; quotes must be in the answer;
    * consistency / relevance observations are kept as neutral records (an inconsistency
      is a question for a human, and being on topic is not skill evidence);
    * communication observations count for the ``communication`` competency only (neutral
      when the round has none), never for a technical competency;
    * a "don't know" answer has no correctness score;
    * contradiction descriptions are worded neutrally.
    """
    keys = set(round_keys)
    primary = next((k for k in question_keys if k in keys), None)
    evidence: list[EvidenceItem] = []
    for item in evaluation.evidence:
        key = item.competency_key if item.competency_key in keys else primary
        if key is None:
            continue
        update: dict[str, Any] = {"competency_key": key}
        update["quote"] = item.quote if quote_in_answer(item.quote, answer_text) else None
        dimension = (item.dimension or "").strip().lower()
        if dimension in NON_SKILL_DIMENSIONS:
            update["signal"] = EvidenceSignal.NEUTRAL
        elif dimension in COMMUNICATION_DIMENSIONS and key != COMMUNICATION_KEY:
            if COMMUNICATION_KEY in keys:
                update["competency_key"] = COMMUNICATION_KEY
            else:
                update["signal"] = EvidenceSignal.NEUTRAL
        evidence.append(item.model_copy(update=update))
    claims = [c for c in evaluation.claim_verifications if c.claim_id in set(known_claims)]
    scores = evaluation.scores
    if evaluation.overall_quality == AnswerQuality.NO_ANSWER and scores.correctness is not None:
        scores = scores.model_copy(update={"correctness": None})
    contradictions = [
        c.model_copy(
            update={
                "statement": neutral_description(c.statement)[:300] or c.statement,
                "conflicts_with": neutral_description(c.conflicts_with)[:300] or c.conflicts_with,
            }
        )
        for c in evaluation.contradictions
    ]
    return evaluation.model_copy(
        update={
            "evidence": evidence,
            "claim_verifications": claims,
            "scores": scores,
            "contradictions": contradictions,
        }
    )


def ground_contradictions(
    evaluation: AnswerEvaluation,
    state: InterviewState | None,
    *,
    answer_text: str,
    conversation: Sequence[dict[str, Any]] = (),
) -> tuple[AnswerEvaluation, list[str]]:
    """Drop reported inconsistencies that are not grounded in the candidate's own words: the side taken
    from an answer must be in what the candidate said (this answer, earlier answers, candidate turns of the
    conversation - never the interviewer's questions); a side citing the resume must be in the resume.
    Returns the evaluation without them and the reasons (logged as ``evaluation.contradiction_dropped``)."""
    if not evaluation.contradictions or state is None:
        return evaluation, []
    ctx = GroundingContext.from_state(
        state,
        extra_candidate=[
            answer_text,
            *(str(t.get("text") or "") for t in conversation if t.get("candidate")),
        ],
    )
    kept, dropped = [], []
    for c in evaluation.contradictions:
        issue = inconsistency_issue(ctx, "", sides=[c.statement, c.conflicts_with])
        if issue is None:
            kept.append(c)
        else:
            dropped.append(issue)
    if not dropped:
        return evaluation, []
    log.info("engine.evaluation_contradiction_dropped", session_id=state.session_id, count=len(dropped))
    return evaluation.model_copy(update={"contradictions": kept}), dropped


def synthetic_evidence(evaluation: AnswerEvaluation, competency_key: str | None) -> list[EvidenceItem]:
    """One overall evidence item when the evaluator returned none."""
    if competency_key is None or evaluation.overall_quality not in QUALITY_SIGNAL:
        return []
    signal, strength = QUALITY_SIGNAL[evaluation.overall_quality]
    return [
        EvidenceItem(
            competency_key=competency_key,
            dimension="overall",
            signal=signal,
            evidence=evaluation.summary[:500],
            strength=strength,
            confidence=evaluation.evaluation_confidence,
        )
    ]


async def _conversation_before(
    db: AsyncSession, session_id: uuid.UUID, answer_id: uuid.UUID, limit: int = 12
) -> list[dict[str, Any]]:
    anchor = (
        await db.execute(
            select(Transcript.seq)
            .where(Transcript.session_id == session_id, Transcript.answer_id == answer_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    stmt = select(Transcript).where(Transcript.session_id == session_id, Transcript.kind == "final")
    if anchor is not None:
        stmt = stmt.where(Transcript.seq < anchor)
    rows = list((await db.execute(stmt.order_by(Transcript.seq.desc()).limit(limit))).scalars().all())
    rows.reverse()
    return [
        {
            "speaker": "Candidate" if r.speaker == "candidate" else "Interviewer",
            "text": r.text[:700],
            "candidate": r.speaker == "candidate",
        }
        for r in rows
    ]


async def _earlier_answers(
    db: AsyncSession, session_id: uuid.UUID, answer: Answer, competency: str | None
) -> list[str]:
    if competency is None:
        return []
    rows = (
        await db.execute(
            select(Answer.transcript_text, Question.question_focus, Question.text)
            .join(Question, Question.id == Answer.question_id)
            .where(Answer.session_id == session_id, Answer.id != answer.id, Answer.sequence < answer.sequence)
            .where(Question.competency_keys.contains([competency]))
            .order_by(Answer.sequence.desc())
            .limit(3)
        )
    ).all()
    return [f"Q: {(r[1] or r[2] or '')[:200]}\nA: {r[0][:900]}" for r in reversed(rows)]


async def _prior_evidence(db: AsyncSession, session_id: uuid.UUID, competency: str | None) -> list[str]:
    if competency is None:
        return []
    rows = (
        await db.execute(
            select(CompetencyEvidence.signal, CompetencyEvidence.dimension, CompetencyEvidence.evidence)
            .where(
                CompetencyEvidence.session_id == session_id, CompetencyEvidence.competency_key == competency
            )
            .order_by(CompetencyEvidence.created_at.desc())
            .limit(6)
        )
    ).all()
    return [f"[{r[0]}] {r[1]}: {r[2][:200]}" for r in rows]


def _state_from_row(row: InterviewSession) -> InterviewState | None:
    try:
        return InterviewState.model_validate(row.state) if row.state else None
    except ValueError:
        return None


CODING_EVALUATION_FIELDS = frozenset(CodingEvaluation.model_fields)


def coding_evaluation_from_document(document: dict[str, Any] | None) -> CodingEvaluation | None:
    """The :class:`CodingEvaluation` inside ``coding_sessions.evaluation``.

    The coding module stores the schema fields *plus* bookkeeping (status,
    adjusted_score, competency_keys, hints_used, evaluated_at, ...); the schema
    forbids extra keys, so only its own fields are validated (same rule as
    ``app.services.scoring.persistence.coding_evaluation``). Pending or failed
    documents yield None."""
    if not document or document.get("status", "completed") != "completed":
        return None
    try:
        return CodingEvaluation.model_validate(
            {k: v for k, v in document.items() if k in CODING_EVALUATION_FIELDS}
        )
    except ValueError:
        log.warning("engine.coding_evaluation_invalid")
        return None


async def build_observations(
    db: AsyncSession, session_id: uuid.UUID, round_config: RoundConfig, scoring: ScoringPort
) -> tuple[list[Observation], dict[str, list[str]]]:
    keys = set(round_config.competency_keys())
    rows = (
        await db.execute(
            select(AnswerEvaluationRow, Question, Answer)
            .join(Answer, Answer.id == AnswerEvaluationRow.answer_id)
            .outerjoin(Question, Question.id == AnswerEvaluationRow.question_id)
            .where(AnswerEvaluationRow.session_id == session_id)
        )
    ).all()
    observations: list[Observation] = []
    expected: dict[str, list[str]] = {}
    for ev_row, question, answer in rows:
        if ev_row.is_fallback:
            continue  # nothing was assessed (the model was down): unassessed, never evidence (S12)
        if question is not None and str(question.action) in FLOW_ONLY_ACTION_VALUES:
            continue  # replies to the invitation / intro / closing are not evidence (S9)
        try:
            ev = AnswerEvaluation.model_validate(ev_row.evaluation)
        except ValueError:
            continue
        q_keys = [k for k in (question.competency_keys if question else []) if k in keys]
        e_keys = [e.competency_key for e in ev.evidence if e.competency_key in keys]
        comp_keys = list(dict.fromkeys(q_keys + e_keys))
        if not comp_keys:
            continue
        try:
            difficulty = Difficulty(question.difficulty) if question else round_config.expected_difficulty
        except ValueError:
            difficulty = round_config.expected_difficulty
        elapsed = (answer.ended_ms or 0) / 1000.0
        observations.extend(
            scoring.api.observations_from_evaluation(
                ev,
                competency_keys=comp_keys,
                difficulty=difficulty,
                answer_id=str(answer.id),
                question_id=str(question.id) if question else None,
                elapsed_s=elapsed,
            )
        )
        if question is not None:
            for k in q_keys:
                bucket = expected.setdefault(k, [])
                for topic in question.expected_evidence or []:
                    if topic not in bucket:
                        bucket.append(topic)
    # Coding evaluations (transparent assistance factor for hints).
    coding_rows = (
        await db.execute(
            select(CodingSession, CodingQuestion)
            .join(CodingQuestion, CodingQuestion.id == CodingSession.coding_question_id)
            .where(CodingSession.session_id == session_id)
        )
    ).all()
    for cs, cq in coding_rows:
        cev = coding_evaluation_from_document(cs.evaluation)
        if cev is None:
            continue
        comp_keys = [k for k in (cq.competency_keys or []) if k in keys] or [
            k for k in keys if is_coding_competency(k, None)
        ]
        try:
            difficulty = Difficulty(cq.difficulty)
        except ValueError:
            difficulty = Difficulty.MEDIUM
        elapsed = 0.0
        if cs.ended_at is not None and cs.started_at is not None:
            elapsed = max(0.0, (cs.ended_at - cs.started_at).total_seconds())
        from_coding = getattr(scoring.api, "observations_from_coding", None)
        if callable(from_coding):
            observations.extend(
                from_coding(
                    cev,
                    competency_keys=comp_keys,
                    difficulty=difficulty,
                    coding_session_id=str(cs.id),
                    question_id=str(cs.question_id) if cs.question_id else None,
                    elapsed_s=elapsed,
                    hints_used=int(cs.hints_used or 0),
                )
            )
            continue
        for k in comp_keys:
            observations.append(
                Observation(
                    competency_key=k,
                    score=float(cev.score_estimate),
                    confidence=float(cev.confidence),
                    difficulty=difficulty,
                    elapsed_s=elapsed,
                    coding_session_id=str(cs.id),
                    dimensions={
                        "code_quality": cev.code_quality,
                        "problem_solving": cev.problem_solving,
                        "edge_case_handling": cev.edge_case_handling,
                    },
                    assistance_factor=assistance_factor(cs.hints_used),
                )
            )
    return observations, expected


async def recompute_competencies(
    db: AsyncSession,
    session_id: str | uuid.UUID,
    round_config: RoundConfig,
    previous: dict[str, CompetencyState] | None,
    *,
    elapsed_s: float = 0.0,
    scoring: ScoringPort | None = None,
    focus_areas: dict[str, list[str]] | None = None,
) -> dict[str, CompetencyState]:
    """Competency state as a pure function of the stored evidence."""
    if focus_areas is None and previous:
        focus_areas = {k: list(v.focus_areas) for k, v in previous.items() if v.focus_areas}
    port = scoring or get_scoring()
    sid = as_uuid(session_id)
    assert sid is not None
    observations, expected = await build_observations(db, sid, round_config, port)
    evidence_rows = (
        (await db.execute(select(CompetencyEvidence).where(CompetencyEvidence.session_id == sid)))
        .scalars()
        .all()
    )
    evidence = [evidence_record_from_row(r) for r in evidence_rows]
    kwargs: dict[str, Any] = {}
    if focus_areas and _accepts_kw(port.api.compute_competency_states, "focus_areas"):
        kwargs["focus_areas"] = focus_areas
    return port.api.compute_competency_states(
        round_config,
        observations,
        evidence,
        previous=previous,
        expected_topics=expected,
        elapsed_s=elapsed_s,
        **kwargs,
    )


async def rebuild_hook(db: AsyncSession, state: InterviewState) -> None:
    """StateStore rebuild hook: recompute derived competency state from evidence."""
    states = await recompute_competencies(
        db, state.session_id, state.round_config, state.competencies, elapsed_s=state.elapsed_seconds()
    )
    merge_derived(state, states)
    evaluated = set(
        str(r[0])
        for r in (
            await db.execute(
                select(AnswerEvaluationRow.answer_id).where(
                    AnswerEvaluationRow.session_id == uuid.UUID(state.session_id)
                )
            )
        ).all()
    )
    state.pending_evaluations = [a for a in state.pending_evaluations if a not in evaluated]
    for a in state.answers:
        if a.id in evaluated:
            a.evaluated = True


async def _write_snapshots(
    db: AsyncSession,
    session_id: uuid.UUID,
    states: dict[str, CompetencyState],
    elapsed_s: float,
    *,
    trigger: str,
) -> int:
    count = 0
    for key, st in states.items():
        if st.evidence_count <= 0 and not st.score_history:
            continue
        db.add(
            ScoreSnapshot(
                session_id=session_id,
                elapsed_seconds=round(elapsed_s, 3),
                competency_key=key,
                score=round(st.score_estimate, 3),
                confidence=round(st.confidence, 4),
                evidence_count=st.evidence_count,
                trigger=trigger,
            )
        )
        count += 1
    await db.flush()
    return count


class EvaluationService:
    def __init__(
        self,
        *,
        ai: AIServices | None = None,
        scoring: ScoringPort | None = None,
        config: EngineConfig | None = None,
        redis: Redis | None = None,
    ) -> None:
        self._ai = ai
        self._scoring = scoring
        self._config = config or get_engine_config()
        self._redis = redis

    @property
    def ai(self) -> AIServices:
        return self._ai or get_ai()

    @property
    def scoring(self) -> ScoringPort:
        return self._scoring or get_scoring()

    @property
    def redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        from app.core.redis import get_redis

        return get_redis()

    async def evaluate(
        self,
        session_id: str,
        answer_id: str,
        *,
        is_last_attempt: bool = True,
        replace_fallback: bool = False,
    ) -> dict[str, Any]:
        """Evaluate one answer (idempotent). ``replace_fallback``: an existing fallback evaluation (the model was
        down through every attempt) is evaluated again and replaced in place when the model answers (S12)."""
        sid = uuid.UUID(str(session_id))
        aid = uuid.UUID(str(answer_id))
        # ---- gather context (short transaction) ----------------------------
        async with session_scope() as db:
            answer = await db.get(Answer, aid)
            session_row = await db.get(InterviewSession, sid)
            if answer is None or session_row is None or answer.session_id != sid:
                from app.core.queue import PermanentJobError

                raise PermanentJobError(f"answer {answer_id} not found in session {session_id}")
            existing = (
                await db.execute(select(AnswerEvaluationRow).where(AnswerEvaluationRow.answer_id == aid))
            ).scalar_one_or_none()
            round_config = load_round_config(session_row.round_config)
            state = _state_from_row(session_row)
            question = await db.get(Question, answer.question_id) if answer.question_id else None
            bank = (
                await db.get(BankQuestion, question.bank_question_id)
                if question and question.bank_question_id
                else None
            )
            organization_id = str(session_row.organization_id)
            q_keys = [
                k
                for k in (question.competency_keys if question else [])
                if k in round_config.competency_keys()
            ]
            primary = q_keys[0] if q_keys else None
            replacing = (
                existing.id if existing is not None and replace_fallback and existing.is_fallback else None
            )
            if existing is not None and replacing is None:
                payload = await self._payload_from_existing(
                    db, session_row, answer, question, existing, round_config, state
                )
                await self._publish(session_id, payload)
                return {"answer_id": answer_id, "idempotent": True}
            flow = self._flow_turn(state, answer_id)
            if flow is None and question is not None and str(question.action) in FLOW_ONLY_ACTION_VALUES:
                flow = "flow_question"  # a reply to the invitation / intro / closing is never scored (S9)
            if flow is not None:
                # The turn model judged this turn a clarifying question, a question about the role or
                # a request for time (a phrasing the fast heuristics missed): it is not an answer and
                # is never scored.
                await log_event(db, sid, "evaluation.skipped", {"answer_id": answer_id, "reason": flow})
                skipped = {
                    "type": "evaluation.ready",
                    "session_id": session_id,
                    "answer_id": answer_id,
                    "question_id": str(answer.question_id) if answer.question_id else None,
                    "skipped": flow,
                    "competencies": {},
                    "claims": [],
                    "contradictions": [],
                    "followups": [],
                }
        if flow is not None:
            await self._publish(session_id, skipped)
            log.info("engine.evaluation_skipped", session_id=session_id, answer_id=answer_id, reason=flow)
            return {"answer_id": answer_id, "skipped": flow}
        async with session_scope() as db:
            conversation = await _conversation_before(db, sid, aid)
            earlier = await _earlier_answers(db, sid, answer, primary)
            prior = await _prior_evidence(db, sid, primary)
            answer_text = answer.transcript_text
            answer_ended_s = (answer.ended_ms or 0) / 1000.0
            question_action = question.action if question else None

        claims_view: list[str] = []
        known_claims: list[str] = []
        known_inconsistencies = ""
        if state is not None and state.contradictions:
            known_inconsistencies = "\n".join(
                f"- {c.description[:240]} [{c.status}]" for c in state.contradictions[-12:]
            )
        if state is not None:
            for cid, cs in state.claims.items():
                touched = (question is not None and question.claim_id == cid) or bool(
                    set(cs.claim.competency_keys) & set(q_keys)
                )
                if touched:
                    claims_view.append(
                        f"{cid}: {cs.claim.claim[:240]} -- {', '.join(cs.claim.evidence_topics[:5])}"
                    )
                    known_claims.append(cid)
        rubric = ""
        expected = list(question.expected_evidence) if question and question.expected_evidence else []
        if bank is not None:
            expected = list(dict.fromkeys(expected + list(bank.expected_concepts or [])))[:10]
            r = bank.rubric or {}
            rubric = "; ".join(
                f"{k}: {str(v)[:200]}" for k, v in r.items() if k in ("excellent", "good", "weak")
            )
        competencies = ", ".join(f"{c.key}: {c.name or c.key}" for c in round_config.competencies)
        system = EVALUATION_SYSTEM.render(schema=describe_schema(AnswerEvaluation))
        user = EVALUATION_USER.render(
            round_name=round_config.name,
            competencies=competencies,
            difficulty=question.difficulty if question else round_config.expected_difficulty.value,
            competency=primary,
            question=(question.text if question else "(no question recorded)")[:900],
            expected=", ".join(expected),
            rubric=rubric,
            claims=claims_view,
            known_inconsistencies=known_inconsistencies,
            earlier=earlier,
            prior_evidence=prior,
            conversation=conversation[-12:],
            answer=answer_text[:6000],
        )
        is_fallback = False
        model_call_id: str | None = None
        try:
            result = await self.ai.llm.structured(
                AnswerEvaluation,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                context=CallContext(
                    task=ModelTask.ANSWER_EVALUATION,
                    session_id=session_id,
                    organization_id=organization_id,
                    prompt_version=EVALUATION_SYSTEM.version,
                ),
                temperature=0.1,
                max_tokens=2200,
                timeout=self._config.evaluation_timeout_s,
                max_attempts=2,
            )
            evaluation = result.value
            model_call_id = result.model_call_id
        except AIOutputInvalid as exc:
            ERRORS.labels(component="engine_evaluation").inc()
            evaluation, is_fallback = (
                neutral_evaluation(self._answer_quality(state, answer_id), reason="invalid output"),
                True,
            )
            log.warning(
                "engine.evaluation_fallback", session_id=session_id, answer_id=answer_id, error=str(exc)[:200]
            )
        except AIError as exc:
            if not is_last_attempt:
                raise
            ERRORS.labels(component="engine_evaluation").inc()
            evaluation, is_fallback = (
                neutral_evaluation(self._answer_quality(state, answer_id), reason=type(exc).__name__),
                True,
            )
            log.warning(
                "engine.evaluation_fallback",
                session_id=session_id,
                answer_id=answer_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )

        if replacing is not None and is_fallback:
            # The model still could not evaluate it: keep the existing fallback (the sweep may try again later).
            log.info("engine.reevaluation_still_fallback", session_id=session_id, answer_id=answer_id)
            return {"answer_id": answer_id, "still_fallback": True}
        evaluation = clean_evaluation(
            evaluation,
            answer_text=answer_text,
            round_keys=round_config.competency_keys(),
            question_keys=q_keys,
            known_claims=known_claims or (list(state.claims) if state else []),
        )
        evaluation, dropped_contradictions = ground_contradictions(
            evaluation, state, answer_text=answer_text, conversation=conversation
        )
        items = list(evaluation.evidence)
        if not items and not is_fallback:
            items = synthetic_evidence(evaluation, primary)
        vectors: list[list[float]] = []
        if items:
            try:
                vectors = await embed_documents(
                    self.ai.embeddings,
                    [f"{i.competency_key} {i.dimension}: {i.evidence}" for i in items],
                    context=CallContext(
                        task=ModelTask.EMBEDDING, session_id=session_id, organization_id=organization_id
                    ),
                )
            except AIError as exc:
                log.warning(
                    "engine.evidence_embedding_failed", session_id=session_id, error=type(exc).__name__
                )
                vectors = []
        answer_vector: list[float] | None = None
        try:
            answer_vector = (
                (
                    await embed_documents(
                        self.ai.embeddings,
                        [answer_text[:4000]],
                        context=CallContext(task=ModelTask.EMBEDDING, session_id=session_id),
                    )
                )[0]
                if answer_text.strip()
                else None
            )
        except AIError:
            answer_vector = None

        # ---- apply (one transaction) ---------------------------------------
        async with session_scope() as db:
            session_row = await db.get(InterviewSession, sid)
            answer = await db.get(Answer, aid)
            assert session_row is not None and answer is not None
            values = {
                "id": uuid.uuid4(),
                "answer_id": aid,
                "question_id": answer.question_id,
                "session_id": sid,
                "model_call_id": as_uuid(model_call_id),
                "overall_quality": evaluation.overall_quality.value,
                "confidence": evaluation.evaluation_confidence,
                "is_fallback": is_fallback,
                # Pure AnswerEvaluation document (other modules validate it with
                # the schema); the fallback flag lives in ``is_fallback`` and the
                # answer's status (``needs_review``).
                "evaluation": evaluation.model_dump(mode="json"),
                **{k: v for k, v in evaluation.scores.model_dump().items()},
            }
            if replacing is not None:
                # In place (evidence and reviews keep pointing at the same row); only while it is still a fallback,
                # so two re-evaluations of the same answer apply once.
                replaced = {k: v for k, v in values.items() if k not in ("id", "answer_id", "session_id")}
                inserted = (
                    await db.execute(
                        update(AnswerEvaluationRow)
                        .where(AnswerEvaluationRow.id == replacing, AnswerEvaluationRow.is_fallback.is_(True))
                        .values(**replaced)
                        .returning(AnswerEvaluationRow.id)
                    )
                ).scalar_one_or_none()
            else:
                inserted = (
                    await db.execute(
                        pg_insert(AnswerEvaluationRow)
                        .values(**values)
                        .on_conflict_do_nothing(index_elements=[AnswerEvaluationRow.answer_id])
                        .returning(AnswerEvaluationRow.id)
                    )
                ).scalar_one_or_none()
            if inserted is None:
                log.info("engine.evaluation_duplicate", session_id=session_id, answer_id=answer_id)
                return {"answer_id": answer_id, "idempotent": True}
            evidence_ids: list[str] = []
            difficulty = question.difficulty if question else None
            start_ms, end_ms = answer.started_ms, answer.ended_ms
            for idx, item in enumerate(items):
                eid = uuid.uuid4()
                evidence_ids.append(str(eid))
                db.add(
                    CompetencyEvidence(
                        id=eid,
                        session_id=sid,
                        competency_key=item.competency_key,
                        question_id=answer.question_id,
                        answer_id=aid,
                        evaluation_id=inserted,
                        source=EvidenceSource.ANSWER.value,
                        signal=item.signal.value,
                        dimension=item.dimension[:80] or "general",
                        evidence=item.evidence,
                        strength=item.strength,
                        confidence=item.confidence,
                        difficulty=difficulty,
                        elapsed_seconds=answer_ended_s,
                        transcript_ref={
                            "answer_id": answer_id,
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "quote": item.quote,
                        },
                        embedding=vectors[idx] if idx < len(vectors) else None,
                    )
                )
            await db.flush()
            await db.execute(
                update(Answer)
                .where(Answer.id == aid)
                .values(
                    status="needs_review" if is_fallback else "evaluated",
                    **({"embedding": answer_vector} if answer_vector is not None else {}),
                )
            )
            live_state = state
            previous = live_state.competencies if live_state else None
            states = await recompute_competencies(
                db, sid, round_config, previous, elapsed_s=answer_ended_s, scoring=self.scoring
            )
            await self._persist_scores(db, sid, states, answer_ended_s)
            claim_updates = [
                {
                    "claim_id": c.claim_id,
                    "status": c.status.value,
                    "note": c.note,
                    "evidence_ids": evidence_ids
                    if c.status
                    in (ClaimStatus.VERIFIED, ClaimStatus.PARTIALLY_VERIFIED, ClaimStatus.CONTRADICTED)
                    else [],
                }
                for c in evaluation.claim_verifications
            ]
            contradictions = [
                {
                    "description": f"{c.statement} / earlier: {c.conflicts_with}"[:300],
                    "severity": c.severity if c.severity in ("low", "medium", "high") else "low",
                    "refs": [answer_id] + (["resume"] if "resume" in c.conflicts_with.lower() else []),
                    "sides": [c.statement[:300], c.conflicts_with[:300]],
                }
                for c in evaluation.contradictions
            ]
            if dropped_contradictions:
                await log_event(
                    db,
                    sid,
                    "evaluation.contradiction_dropped",
                    {"answer_id": answer_id, "reasons": dropped_contradictions[:3]},
                    elapsed_s=answer_ended_s,
                )
            followups = [
                {"text": f.text, "reason": f.reason, "priority": f.priority, "competency_key": primary}
                for f in evaluation.suggested_followups[:3]
            ]
            payload = {
                "type": "evaluation.ready",
                "session_id": session_id,
                "answer_id": answer_id,
                "question_id": str(answer.question_id) if answer.question_id else None,
                "question_action": question_action,
                "quality": evaluation.overall_quality.value,
                "is_fallback": is_fallback,
                "evidence_ids": evidence_ids,
                "competencies": {k: v.model_dump(mode="json") for k, v in states.items()},
                "claims": claim_updates,
                "contradictions": contradictions,
                "followups": followups,
            }
            await log_event(
                db,
                sid,
                "evaluation.applied",
                {
                    "answer_id": answer_id,
                    "quality": evaluation.overall_quality.value,
                    "is_fallback": is_fallback,
                    "evidence": len(items),
                    "contradictions": len(contradictions),
                    "claims": [c["claim_id"] for c in claim_updates],
                },
                elapsed_s=answer_ended_s,
            )
        await self._publish(session_id, payload)
        await self._apply_if_not_live(session_id, payload)
        log.info(
            "engine.answer_evaluated",
            session_id=session_id,
            answer_id=answer_id,
            quality=evaluation.overall_quality.value,
            evidence=len(items),
            fallback=is_fallback,
            replaced_fallback=replacing is not None,
        )
        return {
            "answer_id": answer_id,
            "quality": evaluation.overall_quality.value,
            "evidence": len(items),
            "fallback": is_fallback,
            "replaced_fallback": replacing is not None,
        }

    @staticmethod
    def _flow_turn(state: InterviewState | None, answer_id: str) -> str | None:
        """Why this candidate turn is not an answer (None when it is one)."""
        if state is None:
            return None
        for a in state.answers:
            if a.id == answer_id:
                if a.quality in FLOW_TURN_QUALITIES and a.quality_source in ("model", "quick", "special"):
                    return a.quality.value
                return None
        return None

    @staticmethod
    def _answer_quality(state: InterviewState | None, answer_id: str) -> AnswerQuality | None:
        if state is None:
            return None
        for a in state.answers:
            if a.id == answer_id:
                return a.quality
        return None

    async def _persist_scores(
        self, db: AsyncSession, sid: uuid.UUID, states: dict[str, CompetencyState], elapsed_s: float
    ) -> None:
        port = self.scoring
        if port.recompute is not None:
            try:
                async with db.begin_nested():
                    if _accepts_kw(port.recompute, "trigger"):
                        await port.recompute(db, sid, final=False, trigger="answer_evaluated")
                    else:
                        await port.recompute(db, sid, final=False)
                return
            except Exception as exc:  # noqa: BLE001 - fall back to engine-written snapshots
                ERRORS.labels(component="engine_evaluation").inc()
                log.warning(
                    "engine.scoring_recompute_failed",
                    session_id=str(sid),
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
        await _write_snapshots(db, sid, states, elapsed_s, trigger="answer_evaluated")

    async def _payload_from_existing(
        self,
        db: AsyncSession,
        session_row: InterviewSession,
        answer: Answer,
        question: Question | None,
        existing: AnswerEvaluationRow,
        round_config: RoundConfig,
        state: InterviewState | None,
    ) -> dict[str, Any]:
        states = await recompute_competencies(
            db,
            session_row.id,
            round_config,
            state.competencies if state else None,
            elapsed_s=(answer.ended_ms or 0) / 1000.0,
            scoring=self.scoring,
        )
        claims: list[dict[str, Any]]
        try:
            ev = AnswerEvaluation.model_validate(existing.evaluation)
            claims = [
                {"claim_id": c.claim_id, "status": c.status.value, "note": c.note, "evidence_ids": []}
                for c in ev.claim_verifications
            ]
        except ValueError:
            claims = []
        return {
            "type": "evaluation.ready",
            "session_id": str(session_row.id),
            "answer_id": str(answer.id),
            "question_id": str(answer.question_id) if answer.question_id else None,
            "question_action": question.action if question else None,
            "quality": existing.overall_quality,
            "is_fallback": existing.is_fallback,
            "competencies": {k: v.model_dump(mode="json") for k, v in states.items()},
            "claims": claims,
            "contradictions": [],
            "followups": [],
        }

    async def _apply_if_not_live(self, session_id: str, payload: dict[str, Any]) -> None:
        """Sessions that ended (or are ending) have no live engine to apply the
        result before a next decision: apply it to the stored state directly so
        claim and contradiction statuses read by the reports stay current. The
        payload was written to the evals hash first, and the engine re-checks
        the hash after closing, so no result is lost in between."""
        from app.services.interview.state_store import StateStore
        from app.services.interview.transitions import apply_evaluation

        store = StateStore(redis=self.redis)

        def _mutate(state: InterviewState) -> bool:
            if state.status not in ENDED_FOR_JOBS:
                return False
            return apply_evaluation(state, payload)

        try:
            await store.save_with_retry(session_id, _mutate, durable=True)
        except Exception as exc:  # noqa: BLE001 - the evaluation itself is stored; state refresh is best effort
            ERRORS.labels(component="engine_evaluation").inc()
            log.warning(
                "engine.evaluation_state_apply_failed",
                session_id=session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )

    async def _publish(self, session_id: str, payload: dict[str, Any]) -> None:
        try:
            await self.redis.hset(
                evals_key(session_id), payload["answer_id"], json.dumps(payload, default=str)
            )
            await self.redis.expire(evals_key(session_id), self._config.evals_ttl_s)
        except (RedisError, OSError) as exc:
            ERRORS.labels(component="engine_evaluation").inc()
            log.warning("engine.evaluation_cache_failed", session_id=session_id, error=type(exc).__name__)
        summary = {
            k: {
                "score": round(float(v.get("score_estimate", 0.0)), 2),
                "confidence": round(float(v.get("confidence", 0.0)), 3),
                "evidence_count": int(v.get("evidence_count", 0)),
                "sufficient": bool(v.get("sufficient_evidence", False)),
            }
            for k, v in (payload.get("competencies") or {}).items()
        }
        await publish_session_event(
            session_id,
            {
                "type": "evaluation.ready",
                "session_id": session_id,
                "answer_id": payload["answer_id"],
                "question_id": payload.get("question_id"),
                "quality": payload.get("quality"),
                "is_fallback": payload.get("is_fallback", False),
                "competencies": summary,
            },
        )


async def pop_ready_evaluations(
    redis: Redis, session_id: str, answer_ids: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Read (and remove) evaluation payloads waiting for the live engine."""
    try:
        raw = await redis.hgetall(evals_key(session_id))
    except (RedisError, OSError) as exc:
        log.warning("engine.evaluation_cache_read_failed", session_id=session_id, error=type(exc).__name__)
        return []
    if not raw:
        return []
    payloads: list[dict[str, Any]] = []
    wanted = set(answer_ids) if answer_ids is not None else None
    taken: list[str] = []
    for raw_key, value in raw.items():
        key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        if wanted is not None and key not in wanted:
            continue
        try:
            payloads.append(json.loads(value))
            taken.append(key)
        except (TypeError, ValueError):
            taken.append(key)
    if taken:
        try:
            await redis.hdel(evals_key(session_id), *taken)
        except (RedisError, OSError):
            pass
    return payloads


__all__ = [
    "EvaluationService",
    "build_observations",
    "coding_evaluation_from_document",
    "clean_evaluation",
    "evals_key",
    "neutral_evaluation",
    "pop_ready_evaluations",
    "quote_in_answer",
    "rebuild_hook",
    "recompute_competencies",
    "synthetic_evidence",
]
