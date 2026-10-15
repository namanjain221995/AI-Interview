"""Scoring persistence: load a session's evidence, compute, write ``scores``
and ``score_snapshots``.

``recompute_session_scores(db, session_id, final=...)`` runs inside the
caller's transaction (it never commits). Runs for one session are serialised
with a transaction-scoped advisory lock. Every row written by one run shares
one ``created_at`` so readers can pick "the latest set" unambiguously:

* final run     -> every previous score row is replaced, rows are ``is_final``;
* non-final run -> previous non-final rows are replaced (a final set, if any,
  stays but is older, so readers still see the newest computation).

Snapshots: non-final runs append one ``score_snapshots`` row per competency
whose (score, confidence, evidence count) changed since its last snapshot.
Final runs replace earlier ``final``/``backfill`` rows, backfill the history
of competencies that never got a live snapshot (replayed from evidence in
time order) and add one ``final`` row per competency.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.logging import get_logger
from app.db.models import (
    Answer,
    AnswerEvaluation,
    BackgroundJob,
    CodingQuestion,
    CodingSession,
    Competency,
    CompetencyEvidence,
    InterviewSession,
    Question,
    Score,
    ScoreSnapshot,
)
from app.domain.enums import (
    AnswerQuality,
    Difficulty,
    EvidenceSignal,
    EvidenceSource,
    JobState,
    ScoreCategory,
)
from app.schemas.ai import AnswerEvaluation as AnswerEvaluationDoc
from app.schemas.ai import CodingEvaluation, DimensionScores, InterviewPlan
from app.schemas.rounds import RoundConfig, load_round_config
from app.services.integrity.queries import count_unreviewed_flags
from app.services.integrity.rules import REVIEWABLE_ACTIONS
from app.services.interview.policy import FLOW_ONLY_ACTION_VALUES
from app.services.interview.state import CompetencyState
from app.services.scoring.compute import (
    CompetencyCalculation,
    ReplayPoint,
    aggregate_rubric,
    competency_calculations,
    compute_competency_states,
    effective_score,
    evidence_coverage,
    is_assessed,
    observation_weight,
    observations_from_coding,
    observations_from_evaluation,
    recommend,
    replay,
    round_scope,
)
from app.services.scoring.contracts import (
    EvidenceRecord,
    Observation,
    RecommendationResult,
    RubricResult,
    ScoringResult,
)

log = get_logger(__name__)

ANSWER_DIMENSIONS: tuple[str, ...] = tuple(DimensionScores.model_fields)
CODING_DIMENSIONS: tuple[str, ...] = (
    "code_quality",
    "problem_solving",
    "edge_case_handling",
    "testing_approach",
)
# Integrity actions a human must review (single definition owned by the integrity module).
FLAG_ACTIONS = frozenset(action.value for action in REVIEWABLE_ACTIONS)
OPEN_CONTRADICTION_STATUSES = frozenset({"open", "verifying"})
TRIGGER_ANSWER = "answer_evaluated"
TRIGGER_FINAL = "final"
TRIGGER_BACKFILL = "backfill"
MAX_TRANSCRIPT_REFS = 25


def _uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _difficulty(value: str | None) -> Difficulty:
    try:
        return Difficulty(value or "medium")
    except ValueError:
        return Difficulty.MEDIUM


_SMALL_WORDS = frozenset({"and", "or", "of", "the", "for", "to", "in", "on", "with"})
_KNOWN_LABELS = {"ci_cd": "CI/CD", "go": "Go", "golang": "Go"}


def humanize_key(key: str) -> str:
    """Readable label for a competency key without a catalog name
    ("aws" -> "AWS", "api_design" -> "API Design", "cloud_cost_optimization" -> "Cloud Cost Optimization")."""
    if key in _KNOWN_LABELS:
        return _KNOWN_LABELS[key]
    words = [w for w in key.replace("-", "_").split("_") if w]
    out = []
    for i, word in enumerate(words):
        if i and word in _SMALL_WORDS:
            out.append(word)
        elif len(word) <= 3:
            out.append(word.upper())
        else:
            out.append(word.capitalize())
    return " ".join(out) or key


def advisory_lock_key(namespace: str, session_id: uuid.UUID | str) -> int:
    digest = hashlib.sha256(f"{namespace}:{session_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(slots=True)
class SessionScoringInputs:
    session: InterviewSession
    round_config: RoundConfig
    plan: InterviewPlan | None
    questions: list[Question]
    answers: list[Answer]
    evaluations: list[AnswerEvaluation]
    evidence_rows: list[CompetencyEvidence]
    coding_sessions: list[CodingSession]
    coding_questions: dict[uuid.UUID, CodingQuestion]
    observations: list[Observation] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    expected_topics: dict[str, list[str]] = field(default_factory=dict)
    focus_areas: dict[str, list[str]] = field(default_factory=dict)
    previous: dict[str, CompetencyState] = field(default_factory=dict)
    dimension_averages: dict[str, float] = field(default_factory=dict)
    unreviewed_integrity_flags: int = 0
    open_high_contradictions: int = 0
    competency_names: dict[str, str] = field(default_factory=dict)
    unevaluated_answer_ids: list[str] = field(default_factory=list)
    pending_jobs: int = 0

    @property
    def question_by_id(self) -> dict[uuid.UUID, Question]:
        return {q.id: q for q in self.questions}

    @property
    def answer_by_id(self) -> dict[uuid.UUID, Answer]:
        return {a.id: a for a in self.answers}


def parse_round_config(raw: dict[str, Any] | None) -> RoundConfig:
    return load_round_config(raw)


def parse_plan(raw: dict[str, Any] | None) -> InterviewPlan | None:
    if not raw:
        return None
    try:
        return InterviewPlan.model_validate(raw)
    except ValidationError:
        log.warning("scoring.plan_invalid")
        return None


def answer_elapsed_seconds(answer: Answer | None, question: Question | None) -> float:
    if answer is not None:
        if answer.ended_ms is not None:
            return max(0.0, answer.ended_ms / 1000.0)
        if answer.started_ms is not None:
            return max(0.0, answer.started_ms / 1000.0)
    if question is not None:
        return float(question.elapsed_seconds or 0.0)
    return 0.0


def evaluation_document(row: AnswerEvaluation) -> AnswerEvaluationDoc:
    """The evaluation as the AI schema, with the row's columns authoritative
    for dimension scores, quality and confidence."""
    scores = {name: getattr(row, name) for name in ANSWER_DIMENSIONS}
    try:
        quality = AnswerQuality(row.overall_quality)
    except ValueError:
        quality = AnswerQuality.PARTIAL
    base: dict[str, Any] = {}
    if row.evaluation:
        try:
            base = AnswerEvaluationDoc.model_validate(row.evaluation).model_dump()
        except ValidationError:
            base = {}
    base.update(
        {
            "overall_quality": quality,
            "scores": scores,
            "evaluation_confidence": max(0.0, min(1.0, float(row.confidence or 0.0))),
        }
    )
    base.setdefault("summary", "")
    return AnswerEvaluationDoc.model_validate(base)


CODING_EVALUATION_FIELDS = frozenset(CodingEvaluation.model_fields)
# Bookkeeping the coding module stores next to the CodingEvaluation fields.
CODING_DOCUMENT_EXTRAS = (
    "status",
    "is_fallback",
    "raw_score_estimate",
    "score_cap",
    "score_cap_applied",
    "hints_used",
    "assistance_factor",
    "adjusted_score",
    "competency_keys",
    "difficulty",
    "language",
    "final_run_status",
    "needs_human_review",
    "evaluated_at",
)


def coding_evaluation(row: CodingSession) -> CodingEvaluation | None:
    """The completed :class:`CodingEvaluation` stored by ``coding.evaluate``.

    ``coding_sessions.evaluation`` holds the schema fields plus bookkeeping
    (``adjusted_score``, ``competency_keys``, ``difficulty``...); only the
    schema fields are validated here. Pending/failed documents yield None.
    """
    document = row.evaluation or {}
    if not document or document.get("status", "completed") != "completed":
        return None
    try:
        return CodingEvaluation.model_validate(
            {k: v for k, v in document.items() if k in CODING_EVALUATION_FIELDS}
        )
    except ValidationError:
        log.warning("scoring.coding_evaluation_invalid", coding_session_id=str(row.id))
        return None


def coding_extras(row: CodingSession) -> dict[str, Any]:
    document = row.evaluation or {}
    return {k: document[k] for k in CODING_DOCUMENT_EXTRAS if k in document}


def evidence_record(
    row: CompetencyEvidence, answers: dict[uuid.UUID, Answer], questions: dict[uuid.UUID, Question]
) -> EvidenceRecord:
    try:
        signal = EvidenceSignal(row.signal)
    except ValueError:
        signal = EvidenceSignal.NEUTRAL
    try:
        source = EvidenceSource(row.source)
    except ValueError:
        source = EvidenceSource.ANSWER
    elapsed = row.elapsed_seconds
    if elapsed is None:
        answer = answers.get(row.answer_id) if row.answer_id else None
        question = questions.get(row.question_id) if row.question_id else None
        elapsed = answer_elapsed_seconds(answer, question)
    return EvidenceRecord(
        id=str(row.id),
        competency_key=row.competency_key,
        signal=signal,
        dimension=row.dimension or "general",
        strength=float(row.strength),
        confidence=float(row.confidence),
        source=source,
        difficulty=_difficulty(row.difficulty) if row.difficulty else None,
        question_id=str(row.question_id) if row.question_id else None,
        answer_id=str(row.answer_id) if row.answer_id else None,
        elapsed_s=float(elapsed or 0.0),
        text=row.evidence or "",
        coding_session_id=str(row.coding_session_id) if row.coding_session_id else None,
    )


def _weighted_mean(pairs: Iterable[tuple[float, float]]) -> float | None:
    items = [(v, w) for v, w in pairs if v is not None]
    total = sum(w for _, w in items)
    if not items:
        return None
    if total <= 0:
        return sum(v for v, _ in items) / len(items)
    return sum(v * w for v, w in items) / total


def dimension_averages(
    evaluations: Sequence[AnswerEvaluation], coding: Sequence[CodingSession]
) -> dict[str, float]:
    """Confidence-weighted session averages of every scored dimension
    (neutral fallback evaluations only count when nothing else exists)."""
    rows = [e for e in evaluations if not e.is_fallback] or list(evaluations)
    out: dict[str, float] = {}
    for name in ANSWER_DIMENSIONS:
        value = _weighted_mean(
            (float(getattr(r, name)), max(float(r.confidence or 0.0), 0.05))
            for r in rows
            if getattr(r, name) is not None
        )
        if value is not None:
            out[name] = round(value, 3)
    coding_docs = [(c, coding_evaluation(c)) for c in coding]
    for name in CODING_DIMENSIONS:
        value = _weighted_mean(
            (float(getattr(doc, name)), max(float(doc.confidence), 0.05))
            for _, doc in coding_docs
            if doc is not None and getattr(doc, name) is not None
        )
        if value is not None:
            out[name] = round(value, 3)
    return out


async def competency_names(
    db: AsyncSession, organization_id: uuid.UUID, keys: Iterable[str]
) -> dict[str, str]:
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(Competency.key, Competency.name, Competency.organization_id).where(
                Competency.key.in_(wanted),
                or_(Competency.organization_id == organization_id, Competency.organization_id.is_(None)),
            )
        )
    ).all()
    names: dict[str, str] = {}
    for key, name, _org in sorted(rows, key=lambda r: r[2] is not None):  # org-specific rows win
        names[key] = name
    return names


def _contradiction_count(session: InterviewSession, evaluations: Sequence[AnswerEvaluation]) -> int:
    state = session.state or {}
    if isinstance(state.get("contradictions"), list):
        return sum(
            1
            for c in state["contradictions"]
            if isinstance(c, dict)
            and str(c.get("severity", "")).lower() == "high"
            and str(c.get("status", "open")).lower() in OPEN_CONTRADICTION_STATUSES
        )
    count = 0
    for row in evaluations:
        for finding in (row.evaluation or {}).get("contradictions", []) or []:
            if isinstance(finding, dict) and str(finding.get("severity", "")).lower() == "high":
                count += 1
    return count


def _previous_states(session: InterviewSession) -> dict[str, CompetencyState]:
    raw = (session.state or {}).get("competencies") or {}
    out: dict[str, CompetencyState] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                out[key] = CompetencyState.model_validate(value)
            except ValidationError:
                continue
    return out


EVALUATION_JOB_TYPES = ("answer.evaluate", "coding.evaluate")


async def pending_evaluation_jobs(db: AsyncSession, session_id: uuid.UUID | str) -> int:
    """Unfinished ``answer.evaluate`` / ``answer.reevaluate`` / ``coding.evaluate`` jobs of a session."""
    sid = _uuid(session_id)
    coding_ids = [
        str(c)
        for c in (await db.scalars(select(CodingSession.id).where(CodingSession.session_id == sid))).all()
    ]
    conditions = [
        (BackgroundJob.type.in_(("answer.evaluate", "answer.reevaluate")))
        & (BackgroundJob.payload["session_id"].astext == str(sid)),
    ]
    if coding_ids:
        conditions.append(
            (BackgroundJob.type == "coding.evaluate")
            & (BackgroundJob.payload["coding_session_id"].astext.in_(coding_ids))
        )
        conditions.append(
            (BackgroundJob.type == "coding.evaluate")
            & (BackgroundJob.payload["session_id"].astext == str(sid))
        )
    return int(
        await db.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.status.in_([JobState.QUEUED.value, JobState.RUNNING.value]),
                or_(*conditions),
            )
        )
        or 0
    )


async def load_session(db: AsyncSession, session_id: uuid.UUID | str) -> InterviewSession:
    session = await db.get(InterviewSession, _uuid(session_id))
    if session is None:
        raise NotFound("Session not found.")
    return session


async def load_scoring_inputs(db: AsyncSession, session_id: uuid.UUID | str) -> SessionScoringInputs:
    session = await load_session(db, session_id)
    sid = session.id
    round_config = parse_round_config(session.round_config)
    plan = parse_plan(session.plan)
    questions = list(
        (
            await db.scalars(select(Question).where(Question.session_id == sid).order_by(Question.sequence))
        ).all()
    )
    answers = list(
        (await db.scalars(select(Answer).where(Answer.session_id == sid).order_by(Answer.sequence))).all()
    )
    evaluations = list(
        (
            await db.scalars(
                select(AnswerEvaluation)
                .where(AnswerEvaluation.session_id == sid)
                .order_by(AnswerEvaluation.created_at)
            )
        ).all()
    )
    evidence_rows = list(
        (
            await db.scalars(
                select(CompetencyEvidence)
                .where(CompetencyEvidence.session_id == sid)
                .order_by(
                    CompetencyEvidence.elapsed_seconds.nulls_last(),
                    CompetencyEvidence.created_at,
                    CompetencyEvidence.id,
                )
            )
        ).all()
    )
    coding_sessions = list(
        (
            await db.scalars(
                select(CodingSession)
                .where(CodingSession.session_id == sid)
                .order_by(CodingSession.started_at)
            )
        ).all()
    )
    coding_ids = {c.coding_question_id for c in coding_sessions}
    coding_questions = (
        {
            q.id: q
            for q in (await db.scalars(select(CodingQuestion).where(CodingQuestion.id.in_(coding_ids)))).all()
        }
        if coding_ids
        else {}
    )
    flags = await count_unreviewed_flags(db, sid)
    inputs = SessionScoringInputs(
        session=session,
        round_config=round_config,
        plan=plan,
        questions=questions,
        answers=answers,
        evaluations=evaluations,
        evidence_rows=evidence_rows,
        coding_sessions=coding_sessions,
        coding_questions=coding_questions,
        unreviewed_integrity_flags=flags,
        open_high_contradictions=_contradiction_count(session, evaluations),
        previous=_previous_states(session),
    )
    _build_observations(inputs)
    inputs.pending_jobs = await pending_evaluation_jobs(db, sid)
    inputs.dimension_averages = dimension_averages(evaluations, coding_sessions)
    keys = (
        set(round_config.competency_keys())
        | {o.competency_key for o in inputs.observations}
        | {e.competency_key for e in inputs.evidence}
    )
    inputs.competency_names = await competency_names(db, session.organization_id, keys)
    return inputs


def _build_observations(inputs: SessionScoringInputs) -> None:
    questions = inputs.question_by_id
    answers = inputs.answer_by_id
    round_keys = set(inputs.round_config.competency_keys())
    observations: list[Observation] = []
    evaluated: set[uuid.UUID] = set()
    for row in inputs.evaluations:
        evaluated.add(row.answer_id)
        if row.is_fallback:
            continue  # the model was down: unassessed, not "quality x 10 at weight 0.2" evidence (S12)
        answer = answers.get(row.answer_id)
        question_id = row.question_id or (answer.question_id if answer else None)
        question = questions.get(question_id) if question_id else None
        if question is not None and str(question.action) in FLOW_ONLY_ACTION_VALUES:
            continue  # replies to the invitation / intro / closing are not evidence (S9; cleans old sessions on recompute)
        keys = list(question.competency_keys or []) if question is not None else []
        difficulty = _difficulty(question.difficulty if question is not None else None)
        try:
            doc = evaluation_document(row)
        except ValidationError:
            log.warning("scoring.evaluation_invalid", evaluation_id=str(row.id))
            continue
        observations.extend(
            observations_from_evaluation(
                doc,
                competency_keys=keys,
                difficulty=difficulty,
                answer_id=str(row.answer_id),
                question_id=str(question_id) if question_id else None,
                elapsed_s=answer_elapsed_seconds(answer, question),
            )
        )
    session_start = inputs.session.started_at
    for coding in inputs.coding_sessions:
        coding_doc = coding_evaluation(coding)
        if coding_doc is None:
            continue
        cq = inputs.coding_questions.get(coding.coding_question_id)
        extras = coding_extras(coding)
        question_keys = list(cq.competency_keys or []) if cq is not None else []
        # The coding module already restricted its competency keys to the round's.
        keys = [k for k in extras.get("competency_keys") or [] if k in round_keys]
        if not keys:
            keys = [k for k in question_keys if k in round_keys]
        if not keys:
            keys = [e.competency_key for e in coding_doc.evidence if e.competency_key in round_keys]
        if not keys:
            keys = list(extras.get("competency_keys") or []) or question_keys
        if session_start is not None and coding.ended_at is not None:
            elapsed = max(0.0, (coding.ended_at - session_start).total_seconds())
        else:
            elapsed = float(inputs.session.elapsed_seconds or 0.0)
        observations.extend(
            observations_from_coding(
                coding_doc,
                competency_keys=keys,
                difficulty=extras.get("difficulty") or (cq.difficulty if cq is not None else "medium"),
                coding_session_id=str(coding.id),
                question_id=str(coding.question_id) if coding.question_id else None,
                elapsed_s=elapsed,
                hints_used=int(extras.get("hints_used", coding.hints_used) or 0),
            )
        )
    inputs.observations = observations
    inputs.evidence = [evidence_record(row, answers, questions) for row in inputs.evidence_rows]
    inputs.unevaluated_answer_ids = [str(a.id) for a in inputs.answers if a.id not in evaluated]

    expected: dict[str, list[str]] = {}
    for question in inputs.questions:
        for key in question.competency_keys or []:
            expected.setdefault(key, []).extend(question.expected_evidence or [])
    inputs.expected_topics = expected
    focus: dict[str, list[str]] = {}
    if inputs.plan is not None:
        for target in inputs.plan.competency_targets:
            focus.setdefault(target.key, []).extend(target.focus_areas)
    inputs.focus_areas = focus


# --------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ScoringComputation:
    inputs: SessionScoringInputs
    states: dict[str, CompetencyState]
    rubric: RubricResult
    recommendation: RecommendationResult
    calculations: dict[str, CompetencyCalculation]
    replays: dict[str, list[ReplayPoint]]
    evidence_coverage: float

    def result(self, *, snapshot_written: bool = False) -> ScoringResult:
        return ScoringResult(
            competencies=self.states,
            rubric=self.rubric,
            recommendation=self.recommendation,
            snapshot_written=snapshot_written,
        )


def compute_from_inputs(inputs: SessionScoringInputs) -> ScoringComputation:
    states = compute_competency_states(
        inputs.round_config,
        inputs.observations,
        inputs.evidence,
        previous=inputs.previous,
        expected_topics=inputs.expected_topics,
        elapsed_s=float(inputs.session.elapsed_seconds or 0.0),
        focus_areas=inputs.focus_areas,
    )
    for key, state in states.items():
        if not state.name and inputs.competency_names.get(key):
            state.name = inputs.competency_names[key]
    rubric = aggregate_rubric(inputs.round_config, states, inputs.dimension_averages)
    recommendation = recommend(
        inputs.round_config,
        rubric,
        states,
        unreviewed_integrity_flags=inputs.unreviewed_integrity_flags,
        open_high_contradictions=inputs.open_high_contradictions,
        end_reason=inputs.session.end_reason,
    )
    replays = {
        key: replay(
            [o for o in inputs.observations if o.competency_key == key],
            [e for e in inputs.evidence if e.competency_key == key],
        )
        for key in states
    }
    return ScoringComputation(
        inputs=inputs,
        states=states,
        rubric=rubric,
        recommendation=recommendation,
        calculations=competency_calculations(inputs.observations, inputs.evidence),
        replays=replays,
        evidence_coverage=evidence_coverage(inputs.round_config, states),
    )


async def compute_session_scores(db: AsyncSession, session_id: uuid.UUID | str) -> ScoringComputation:
    """Compute without writing anything (live views, previews)."""
    return compute_from_inputs(await load_scoring_inputs(db, session_id))


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def _transcript_refs(inputs: SessionScoringInputs, key: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in inputs.evidence_rows:
        if row.competency_key != key or row.signal == EvidenceSignal.NEUTRAL.value:
            continue
        ref = dict(row.transcript_ref or {})
        if not ref and row.answer_id is None:
            continue
        ref.setdefault("answer_id", str(row.answer_id) if row.answer_id else None)
        ref["evidence_id"] = str(row.id)
        ref["question_id"] = str(row.question_id) if row.question_id else None
        ref["signal"] = row.signal
        refs.append(ref)
        if len(refs) >= MAX_TRANSCRIPT_REFS:
            break
    return refs


def _supporting_questions(inputs: SessionScoringInputs, key: str) -> list[str]:
    ids: set[str] = set()
    for obs in inputs.observations:
        if obs.competency_key == key and obs.question_id:
            ids.add(obs.question_id)
    for ev in inputs.evidence:
        if ev.competency_key == key and ev.question_id:
            ids.add(ev.question_id)
    order = {str(q.id): q.sequence for q in inputs.questions}
    return sorted(ids, key=lambda qid: (order.get(qid, 1_000_000), qid))


def _observation_details(inputs: SessionScoringInputs, key: str) -> list[dict[str, Any]]:
    out = []
    for obs in inputs.observations:
        if obs.competency_key != key:
            continue
        out.append(
            {
                "answer_id": obs.answer_id,
                "question_id": obs.question_id,
                "coding_session_id": obs.coding_session_id,
                "difficulty": obs.difficulty.value
                if isinstance(obs.difficulty, Difficulty)
                else str(obs.difficulty),
                "score": round(obs.score, 3),
                "effective_score": round(effective_score(obs), 3),
                "assistance_factor": obs.assistance_factor,
                "evaluation_confidence": round(obs.confidence, 3),
                "weight": round(observation_weight(obs), 3),
                "elapsed_seconds": round(obs.elapsed_s, 1),
            }
        )
    return out


def build_score_rows(
    computation: ScoringComputation, *, final: bool, created_at: datetime, trigger: str | None
) -> list[Score]:
    inputs = computation.inputs
    sid = inputs.session.id
    config = inputs.round_config
    scope = round_scope(config)
    rows: list[Score] = []
    total_pos = total_neg = 0
    for key, state in computation.states.items():
        calc = computation.calculations.get(key)
        total_pos += state.positive_evidence
        total_neg += state.negative_evidence
        rows.append(
            Score(
                id=uuid.uuid4(),
                session_id=sid,
                category=ScoreCategory.COMPETENCY.value,
                key=key,
                label=state.name or inputs.competency_names.get(key) or humanize_key(key),
                # No observation and no evidence: no score (the estimate is only the neutral starting point).
                score=round(state.score_estimate, 2) if is_assessed(state) else None,
                confidence=round(state.confidence, 3),
                weight=state.weight,
                evidence_count=state.evidence_count,
                positive_count=state.positive_evidence,
                negative_count=state.negative_evidence,
                sufficient_evidence=state.sufficient_evidence,
                unanswered_areas=list(state.unanswered_questions),
                supporting_question_ids=_supporting_questions(inputs, key),
                positive_evidence_ids=list(state.positive_evidence_ids),
                negative_evidence_ids=list(state.negative_evidence_ids),
                transcript_refs=_transcript_refs(inputs, key),
                details={
                    "assessed": is_assessed(state),
                    "required": state.required,
                    "min_evidence": config.min_evidence_for(key),
                    "confidence_threshold": config.confidence_threshold,
                    "confidence_target": scope.confidence_target,
                    "extra_evidence": scope.extra_evidence,
                    "difficulty_seen": state.difficulty_seen,
                    "dimensions_covered": state.dimensions_covered,
                    "score_history": state.score_history,
                    "calculation": calc.as_dict() if calc is not None else None,
                    "observations": _observation_details(inputs, key),
                },
                is_final=final,
                created_at=created_at,
            )
        )
    for category in computation.rubric.categories:
        config_category = next((c for c in config.rubric if c.key == category.key), None)
        dims = list(config_category.dimensions) if config_category is not None else []
        rows.append(
            Score(
                id=uuid.uuid4(),
                session_id=sid,
                category=ScoreCategory.RUBRIC_CATEGORY.value,
                key=category.key,
                label=category.label,
                score=category.score,
                confidence=category.confidence,
                weight=category.weight,
                evidence_count=sum(
                    computation.states[k].evidence_count for k in category.contributing_competencies
                ),
                positive_count=sum(
                    computation.states[k].positive_evidence for k in category.contributing_competencies
                ),
                negative_count=sum(
                    computation.states[k].negative_evidence for k in category.contributing_competencies
                ),
                sufficient_evidence=all(
                    computation.states[k].sufficient_evidence for k in category.contributing_competencies
                )
                if category.contributing_competencies
                else False,
                details={
                    "contributing_competencies": category.contributing_competencies,
                    "dimensions": dims,
                    "dimension_values": {
                        d: inputs.dimension_averages[d] for d in dims if d in inputs.dimension_averages
                    },
                    "description": config_category.description if config_category is not None else None,
                },
                is_final=final,
                created_at=created_at,
            )
        )
    rec = computation.recommendation
    rows.append(
        Score(
            id=uuid.uuid4(),
            session_id=sid,
            category=ScoreCategory.OVERALL.value,
            key="overall",
            label="Overall",
            score=computation.rubric.overall_score,
            confidence=computation.rubric.overall_confidence,
            weight=100.0,
            evidence_count=total_pos + total_neg,
            positive_count=total_pos,
            negative_count=total_neg,
            sufficient_evidence=computation.rubric.required_coverage
            >= config.passing_guidance.min_required_coverage,
            details={
                "recommendation": rec.recommendation.value,
                "rationale": rec.rationale,
                "factors": rec.factors,
                "human_decision_note": rec.human_decision_note,
                "required_coverage": computation.rubric.required_coverage,
                "evidence_coverage": round(computation.evidence_coverage, 3),
                "assessed_weight_share": computation.rubric.assessed_weight_share,
                "insufficient_reason": "no_evidence" if computation.rubric.overall_score is None else None,
                "round_scope": scope.as_dict(),
                "dimension_averages": inputs.dimension_averages,
                "pending_evaluation_jobs": inputs.pending_jobs,
                "unevaluated_answers": len(inputs.unevaluated_answer_ids),
                "trigger": trigger,
                "end_reason": inputs.session.end_reason,
            },
            is_final=final,
            created_at=created_at,
        )
    )
    return rows


async def _latest_snapshots(db: AsyncSession, session_id: uuid.UUID) -> dict[str, ScoreSnapshot]:
    rows = (
        await db.scalars(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.session_id == session_id)
            .order_by(
                ScoreSnapshot.competency_key,
                ScoreSnapshot.elapsed_seconds.desc(),
                ScoreSnapshot.created_at.desc(),
            )
        )
    ).all()
    latest: dict[str, ScoreSnapshot] = {}
    for row in rows:
        latest.setdefault(row.competency_key, row)
    return latest


def _snapshot_changed(previous: ScoreSnapshot | None, state: CompetencyState) -> bool:
    if previous is None:
        return state.evidence_count > 0 or state.confidence > 0
    return (
        abs(previous.score - state.score_estimate) >= 0.005
        or abs(previous.confidence - state.confidence) >= 0.0005
        or previous.evidence_count != state.evidence_count
    )


async def _write_snapshots(
    db: AsyncSession,
    computation: ScoringComputation,
    *,
    final: bool,
    trigger: str | None,
    created_at: datetime,
) -> bool:
    sid = computation.inputs.session.id
    session_elapsed = float(computation.inputs.session.elapsed_seconds or 0.0)
    written = False
    if final:
        await db.execute(
            delete(ScoreSnapshot).where(
                ScoreSnapshot.session_id == sid, ScoreSnapshot.trigger.in_([TRIGGER_FINAL, TRIGGER_BACKFILL])
            )
        )
        latest = await _latest_snapshots(db, sid)
        for key, points in computation.replays.items():
            if key in latest:
                continue
            for point in points:
                db.add(
                    ScoreSnapshot(
                        session_id=sid,
                        elapsed_seconds=round(point.elapsed_s, 2),
                        competency_key=key,
                        score=point.score,
                        confidence=point.confidence,
                        evidence_count=point.evidence_count,
                        trigger=TRIGGER_BACKFILL,
                        created_at=created_at,
                    )
                )
                written = True
        for key, state in computation.states.items():
            if state.evidence_count == 0 and state.confidence == 0 and not computation.replays.get(key):
                continue
            last_point = computation.replays[key][-1].elapsed_s if computation.replays.get(key) else 0.0
            db.add(
                ScoreSnapshot(
                    session_id=sid,
                    elapsed_seconds=round(max(session_elapsed, last_point), 2),
                    competency_key=key,
                    score=round(state.score_estimate, 3),
                    confidence=round(state.confidence, 3),
                    evidence_count=state.evidence_count,
                    trigger=TRIGGER_FINAL,
                    created_at=created_at,
                )
            )
            written = True
        return written
    latest = await _latest_snapshots(db, sid)
    for key, state in computation.states.items():
        if not _snapshot_changed(latest.get(key), state):
            continue
        elapsed = state.last_updated_s or session_elapsed
        db.add(
            ScoreSnapshot(
                session_id=sid,
                elapsed_seconds=round(float(elapsed), 2),
                competency_key=key,
                score=round(state.score_estimate, 3),
                confidence=round(state.confidence, 3),
                evidence_count=state.evidence_count,
                trigger=trigger or TRIGGER_ANSWER,
                created_at=created_at,
            )
        )
        written = True
    return written


async def recompute_session_scores(
    db: AsyncSession,
    session_id: uuid.UUID | str,
    *,
    final: bool,
    trigger: str | None = None,
    write_snapshots: bool = True,
) -> ScoringResult:
    """Recompute and persist every score of a session (caller commits)."""
    sid = _uuid(session_id)
    await db.execute(select(func.pg_advisory_xact_lock(advisory_lock_key("scores", sid))))
    computation = await compute_session_scores(db, sid)
    created_at = datetime.now(UTC)
    if final:
        await db.execute(delete(Score).where(Score.session_id == sid))
    else:
        await db.execute(delete(Score).where(Score.session_id == sid, Score.is_final.is_(False)))
    rows = build_score_rows(computation, final=final, created_at=created_at, trigger=trigger)
    db.add_all(rows)
    written = False
    if write_snapshots:
        written = await _write_snapshots(db, computation, final=final, trigger=trigger, created_at=created_at)
    await db.flush()
    log.info(
        "scoring.recomputed",
        session_id=str(sid),
        final=final,
        competencies=len(computation.states),
        overall=computation.rubric.overall_score,
        confidence=computation.rubric.overall_confidence,
        recommendation=computation.recommendation.recommendation.value,
        snapshots=written,
    )
    return computation.result(snapshot_written=written)


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


async def latest_score_rows(db: AsyncSession, session_id: uuid.UUID | str) -> list[Score]:
    """The newest computed set of score rows (empty when never computed)."""
    sid = _uuid(session_id)
    latest = await db.scalar(select(func.max(Score.created_at)).where(Score.session_id == sid))
    if latest is None:
        return []
    return list(
        (
            await db.scalars(
                select(Score)
                .where(Score.session_id == sid, Score.created_at == latest)
                .order_by(Score.category, Score.key)
            )
        ).all()
    )


def latest_scores_subquery():  # noqa: ANN201 - SQLAlchemy subquery
    """``(session_id, created_at)`` of every session's newest score set.

    Aggregates the whole ``scores`` table; prefer :func:`latest_scores_lateral`
    when the outer query is already restricted to some sessions.
    """
    return (
        select(Score.session_id.label("session_id"), func.max(Score.created_at).label("created_at"))
        .group_by(Score.session_id)
        .subquery("latest_scores")
    )


def latest_scores_lateral():  # noqa: ANN201 - SQLAlchemy lateral subquery
    """``created_at`` of the newest score set of the outer ``InterviewSession``
    row, as a LATERAL subquery (one index probe per session on
    ``ix_scores_session_created``)::

        latest = latest_scores_lateral()
        select(...).select_from(InterviewSession).join(latest, true()).join(
            Score, and_(Score.session_id == InterviewSession.id, Score.created_at == latest.c.created_at)
        )
    """
    return (
        select(func.max(Score.created_at).label("created_at"))
        .where(Score.session_id == InterviewSession.id)
        .correlate(InterviewSession)
        .lateral("latest_scores")
    )
