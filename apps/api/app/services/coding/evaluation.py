"""``coding.evaluate``: deep evaluation of a finished coding exercise.

Inputs: the final code, every run (ground-truth test results), the snapshot
timeline (how the approach evolved, with structural summaries), the hints used
and what the candidate explained while coding. Output: a
:class:`~app.schemas.ai.CodingEvaluation` stored in
``coding_sessions.evaluation`` plus attributable evidence rows
(``competency_evidence.source = coding``) for the competencies the problem
assesses within the round, then ``coding.evaluated`` on ``session:{id}:bus``.

Deterministic guardrails around the model:

* ``tests_passed`` / ``tests_total`` always come from the final submission
  (hidden tests included). If the candidate never submitted the final code,
  the job runs every test on it first (``code_runs`` row with
  ``sandbox_meta.system = true``).
* ``score_estimate`` is capped at ``3 + 7 x pass_ratio`` so a solution that
  fails most tests cannot be scored as excellent; the cap is recorded.
* Assistance is transparent: ``assistance_factor = 1 - 0.07 x hints_used``
  (at most 3 hints count, floor 0.79) and ``adjusted_score = score_estimate x
  assistance_factor`` (hints lower the weight, never fail a candidate).
* Model failure after retries -> deterministic fallback evaluation from the
  test results (``is_fallback = true``, low confidence) for human review.

Idempotent: a completed evaluation is not recomputed unless ``force``; coding
evidence rows of the session are replaced, never duplicated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.core.queue import PermanentJobError, publish_session_event
from app.db.models import (
    CodeRun,
    CodeSnapshot,
    CodingHint,
    CodingQuestion,
    CodingSession,
    CompetencyEvidence,
    InterviewSession,
    Transcript,
)
from app.db.session import session_scope
from app.domain.enums import (
    CodeLanguage,
    CodingSessionStatus,
    EvidenceSignal,
    EvidenceSource,
    ModelTask,
    RunKind,
)
from app.schemas.ai import CodingEvaluation, EvidenceItem
from app.services.coding.analysis import analyze
from app.services.coding.prompts import EVALUATION_SYSTEM, EVALUATION_USER
from app.services.coding.questions import (
    MAX_STORED_OUTPUT_CHARS,
    build_execute_request,
    staff_test_results,
    truncate,
)
from app.services.coding.sandbox_client import SandboxClient, get_sandbox_client
from app.services.coding.service import MAX_HINTS, evidence_competencies, interview_elapsed_s, round_config_of
from app.services.retrieval import embed_and_store

log = get_logger(__name__)

HINT_PENALTY = 0.07
EVALUATION_TIMEOUT_S = 150.0
SCORE_CAP_BASE = 3.0
SCORE_CAP_SPAN = 7.0


class EvaluationRetry(Exception):
    """Transient failure: let the queue retry the job."""


@dataclass(slots=True)
class _Inputs:
    cs: CodingSession
    question: CodingQuestion
    session: InterviewSession
    runs: list[CodeRun]
    snapshots: list[CodeSnapshot]
    hints: list[CodingHint]
    explanations: list[str]


def assistance_factor(hints_used: int, max_hints: int = MAX_HINTS) -> float:
    """``1 - 0.07 x hints``, counting at most ``max_hints`` (3) hints: 1.0 -> 0.79.

    Same rule as :func:`app.services.scoring.compute.assistance_factor`, so a
    corrupt or replayed ``hints_used`` can never push the factor lower.
    """
    hints = max(0, min(int(hints_used or 0), max_hints))
    return round(1.0 - HINT_PENALTY * hints, 4)


def score_cap(passed: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(SCORE_CAP_BASE + SCORE_CAP_SPAN * (passed / total), 2)


def fallback_evaluation(passed: int, total: int, hints_used: int) -> CodingEvaluation:
    ratio = passed / total if total else 0.0
    return CodingEvaluation(
        correctness_summary=(
            f"Automatic summary: {passed} of {total} tests passed on the final code. "
            "The detailed AI evaluation was unavailable; a human reviewer should review the code."
        )[:500],
        tests_passed=passed,
        tests_total=total,
        complexity_assessment="Not assessed (AI evaluation unavailable).",
        code_quality=5.0,
        problem_solving=round(2 + 6 * ratio, 1),
        edge_case_handling=round(2 + 6 * ratio, 1),
        process_observations=[],
        hints_impact=f"{hints_used} hint(s) used." if hints_used else "No hints used.",
        strengths=[],
        weaknesses=[],
        evidence=[],
        score_estimate=round(2 + 6 * ratio, 1),
        confidence=0.25,
    )


async def _load_inputs(coding_session_id: uuid.UUID) -> _Inputs:
    async with session_scope() as db:
        cs = await db.get(CodingSession, coding_session_id)
        if cs is None:
            raise PermanentJobError(f"coding session {coding_session_id} not found")
        question = await db.get(CodingQuestion, cs.coding_question_id)
        session = await db.get(InterviewSession, cs.session_id)
        if question is None or session is None:
            raise PermanentJobError("coding session is missing its question or interview session")
        runs = list(
            (
                await db.scalars(
                    select(CodeRun)
                    .where(CodeRun.coding_session_id == cs.id)
                    .order_by(CodeRun.created_at, CodeRun.id)
                )
            ).all()
        )
        snapshots = list(
            (
                await db.scalars(
                    select(CodeSnapshot)
                    .where(CodeSnapshot.coding_session_id == cs.id)
                    .order_by(CodeSnapshot.seq)
                )
            ).all()
        )
        hints = list(
            (
                await db.scalars(
                    select(CodingHint).where(CodingHint.coding_session_id == cs.id).order_by(CodingHint.level)
                )
            ).all()
        )
        stmt = select(Transcript.text).where(
            Transcript.session_id == cs.session_id,
            Transcript.speaker == "candidate",
            Transcript.created_at >= cs.started_at,
        )
        if cs.ended_at is not None:
            stmt = stmt.where(Transcript.created_at <= cs.ended_at)
        explanations = [t for t in (await db.scalars(stmt.order_by(Transcript.seq).limit(40))).all() if t]
        return _Inputs(cs, question, session, runs, snapshots, hints, explanations)


def _real(run: CodeRun) -> bool:
    return not (run.sandbox_meta or {}).get("platform_fault")


def _final_run(inputs: _Inputs) -> CodeRun | None:
    """The submission that evaluated the final code (or ``None``)."""
    final_code = inputs.cs.final_code or ""
    by_snapshot = {s.id: s for s in inputs.snapshots}
    for run in reversed(inputs.runs):
        if run.kind != RunKind.SUBMIT.value or not _real(run):
            continue
        snapshot = by_snapshot.get(run.snapshot_id) if run.snapshot_id else None
        if snapshot is None or snapshot.code == final_code:
            return run
        return None  # the latest submission is for older code
    return None


async def _system_submit(inputs: _Inputs, sandbox: SandboxClient, *, is_last_attempt: bool) -> CodeRun | None:
    """Run every test on the final code when the candidate never submitted it."""
    code = inputs.cs.final_code or ""
    if not code.strip():
        return None
    run_id = uuid.uuid4()
    request = build_execute_request(
        inputs.question,
        kind=RunKind.SUBMIT,
        code=code,
        language=CodeLanguage(inputs.cs.language),
        request_id=f"eval-{run_id.hex}",
    )
    response, fault = await sandbox.execute_safe(request)
    if fault is not None:
        if not is_last_attempt:
            raise EvaluationRetry(f"sandbox unavailable for the final run ({fault.reason})")
        log.warning(
            "coding.evaluate.final_run_unavailable", coding_session_id=str(inputs.cs.id), reason=fault.reason
        )
        return None
    snapshot_id = inputs.snapshots[-1].id if inputs.snapshots and inputs.snapshots[-1].code == code else None
    elapsed = (
        (inputs.cs.ended_at - inputs.cs.started_at).total_seconds()
        if inputs.cs.ended_at and inputs.cs.started_at
        else None
    )
    run = CodeRun(
        id=run_id,
        coding_session_id=inputs.cs.id,
        snapshot_id=snapshot_id,
        kind=RunKind.SUBMIT.value,
        language=inputs.cs.language,
        status=response.status.value,
        stdout=truncate(response.stdout, MAX_STORED_OUTPUT_CHARS) or None,
        stderr=truncate(response.stderr, MAX_STORED_OUTPUT_CHARS) or None,
        compile_output=truncate(response.compile_output, MAX_STORED_OUTPUT_CHARS) or None,
        exit_code=response.exit_code,
        test_results=staff_test_results(inputs.question, response.tests),
        passed_count=response.passed,
        total_count=response.total,
        runtime_ms=response.runtime_ms,
        memory_kb=response.memory_kb,
        elapsed_seconds=round(elapsed, 2) if elapsed is not None else None,
        sandbox_meta={
            **(response.meta or {}),
            "system": True,
            "reason": "final_code_not_submitted",
            "request_id": request.request_id,
        },
    )
    async with session_scope() as db:
        db.add(run)
        cs = await db.get(CodingSession, inputs.cs.id, with_for_update=True)
        if cs is not None:
            cs.final_run_id = run_id
    inputs.cs.final_run_id = run_id
    inputs.runs.append(run)
    return run


def _mmss(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def build_evaluation_messages(
    inputs: _Inputs, final_run: CodeRun | None, competency_keys: list[str]
) -> list[dict[str, str]]:
    cs, question = inputs.cs, inputs.question
    real_runs = [r for r in inputs.runs if _real(r)]
    runs_text = (
        "; ".join(
            f"{_mmss(r.elapsed_seconds)} {r.kind} {r.status} {r.passed_count}/{r.total_count}"
            for r in real_runs[-25:]
        )
        or "no runs"
    )
    timeline_parts = []
    for snap in inputs.snapshots[-30:]:
        features = analyze(snap.code, snap.language).summary()
        timeline_parts.append(
            f"v{snap.seq} at {_mmss(snap.elapsed_seconds)} [{snap.trigger}] +{snap.lines_added}/-{snap.lines_removed}: {features}"
        )
    hints_text = (
        "; ".join(
            f"level {h.level} at {_mmss(h.elapsed_seconds)} ({h.source}): {h.content}" for h in inputs.hints
        )
        or "none"
    )
    failing = ""
    passed = total = 0
    if final_run is not None:
        passed, total = final_run.passed_count, final_run.total_count
        failing = "; ".join(
            f"{t.get('id')} ({'hidden' if t.get('hidden') else 'visible'}{': ' + str(t.get('description')) if t.get('description') else ''})"
            for t in final_run.test_results or []
            if not t.get("passed")
        )[:1200]
    limit_min = round((cs.time_limit_seconds or 0) / 60, 1)
    used_min = round(
        ((cs.ended_at - cs.started_at).total_seconds() / 60) if cs.ended_at and cs.started_at else 0.0, 1
    )
    rubric = question.rubric or {}
    rubric_text = "; ".join(f"{k}: {v}" for k, v in rubric.items() if isinstance(v, str))
    if isinstance(rubric.get("criteria"), dict):
        rubric_text += "; criteria: " + "; ".join(f"{k}: {v}" for k, v in rubric["criteria"].items())
    outcome = (cs.observer_state or {}).get("end_reason") or cs.status
    system = EVALUATION_SYSTEM.render(competency_keys=", ".join(competency_keys))
    user = EVALUATION_USER.render(
        title=question.title,
        difficulty=question.difficulty,
        language=cs.language,
        prompt_excerpt=(question.prompt_markdown or "")[:3000],
        expected_concepts=", ".join(question.expected_concepts or []) or "n/a",
        optimal_complexity=", ".join(f"{k} {v}" for k, v in (question.optimal_complexity or {}).items())
        or "n/a",
        rubric=rubric_text[:2000] or "n/a",
        outcome=outcome,
        time_used_min=used_min,
        limit_min=limit_min,
        tests_passed=passed,
        tests_total=total,
        failing=failing,
        runs=runs_text,
        timeline="; ".join(timeline_parts)[:4000] or "no snapshots",
        hints=hints_text[:2000],
        final_code=(cs.final_code or "")[:15000] or "(empty)",
        explanations="\n".join(inputs.explanations)[-4000:],
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _evidence_rows(
    evaluation: CodingEvaluation,
    *,
    inputs: _Inputs,
    competency_keys: list[str],
    final_run: CodeRun | None,
    elapsed_s: float | None,
) -> list[CompetencyEvidence]:
    cs, question = inputs.cs, inputs.question
    primary = competency_keys[0]
    base_ref = {"coding_session_id": str(cs.id), "final_run_id": str(final_run.id) if final_run else None}
    rows: list[CompetencyEvidence] = []
    items: list[EvidenceItem] = list(evaluation.evidence)
    for item in items:
        key = item.competency_key if item.competency_key in competency_keys else primary
        rows.append(
            CompetencyEvidence(
                id=uuid.uuid4(),
                session_id=cs.session_id,
                competency_key=key,
                question_id=cs.question_id,
                coding_session_id=cs.id,
                source=EvidenceSource.CODING.value,
                signal=item.signal.value,
                dimension=item.dimension[:80] or "general",
                evidence=item.evidence,
                strength=item.strength,
                confidence=item.confidence,
                difficulty=question.difficulty,
                elapsed_seconds=elapsed_s,
                transcript_ref={**base_ref, "quote": item.quote} if item.quote else dict(base_ref),
            )
        )
    if final_run is not None and final_run.total_count > 0:
        ratio = final_run.passed_count / final_run.total_count
        if ratio >= 0.8:
            signal, strength = EvidenceSignal.POSITIVE, round(ratio, 3)
        elif ratio < 0.5:
            signal, strength = EvidenceSignal.NEGATIVE, round(1 - ratio, 3)
        else:
            signal, strength = EvidenceSignal.NEUTRAL, 0.5
        rows.append(
            CompetencyEvidence(
                id=uuid.uuid4(),
                session_id=cs.session_id,
                competency_key=primary,
                question_id=cs.question_id,
                coding_session_id=cs.id,
                source=EvidenceSource.CODING.value,
                signal=signal.value,
                dimension="correctness",
                evidence=(
                    f"Final solution to '{question.title}' in {cs.language} passed {final_run.passed_count} of "
                    f"{final_run.total_count} tests (including hidden tests)."
                ),
                strength=strength,
                confidence=0.9,
                difficulty=question.difficulty,
                elapsed_seconds=elapsed_s,
                transcript_ref=dict(base_ref),
            )
        )
    return rows


async def evaluate_coding_session(
    coding_session_id: str | uuid.UUID,
    *,
    ai: AIServices | None = None,
    sandbox: SandboxClient | None = None,
    force: bool = False,
    is_last_attempt: bool = True,
) -> dict[str, Any]:
    """Evaluate one finished coding session (see module docstring)."""
    cs_id = (
        coding_session_id if isinstance(coding_session_id, uuid.UUID) else uuid.UUID(str(coding_session_id))
    )
    inputs = await _load_inputs(cs_id)
    cs, question, session = inputs.cs, inputs.question, inputs.session
    if cs.status == CodingSessionStatus.ACTIVE.value:
        raise PermanentJobError("coding session is still active; finish it first")
    if (cs.evaluation or {}).get("status") == "completed" and not force:
        log.info("coding.evaluate.skipped", coding_session_id=str(cs_id), reason="already_evaluated")
        return {"skipped": True, "coding_session_id": str(cs_id)}

    final_run = _final_run(inputs)
    if final_run is None:
        final_run = await _system_submit(
            inputs, sandbox or get_sandbox_client(), is_last_attempt=is_last_attempt
        )
    passed = final_run.passed_count if final_run else 0
    total = final_run.total_count if final_run else 0

    config = round_config_of(session)
    competency_keys = evidence_competencies(question, config)
    services = ai or get_ai()
    messages = build_evaluation_messages(inputs, final_run, competency_keys)
    is_fallback = False
    model_call_id: str | None = None
    try:
        result = await services.llm.structured(
            CodingEvaluation,
            messages,
            context=CallContext(
                task=ModelTask.CODING_EVALUATION,
                session_id=str(session.id),
                organization_id=str(session.organization_id),
                prompt_version=EVALUATION_USER.version,
            ),
            temperature=0.2,
            max_tokens=2500,
            timeout=EVALUATION_TIMEOUT_S,
            max_attempts=3,
        )
        evaluation = result.value
        model_call_id = result.model_call_id
    except AIError as exc:
        if exc.retryable and not is_last_attempt:
            raise EvaluationRetry(f"model unavailable: {type(exc).__name__}") from exc
        log.warning("coding.evaluate.fallback", coding_session_id=str(cs_id), error=type(exc).__name__)
        evaluation = fallback_evaluation(passed, total, cs.hints_used)
        is_fallback = True

    raw_score = evaluation.score_estimate
    cap = score_cap(passed, total) if final_run is not None else None
    cap_applied = cap is not None and raw_score > cap
    evaluation = evaluation.model_copy(
        update={
            "tests_passed": passed,
            "tests_total": total,
            "score_estimate": cap if cap is not None and cap_applied else raw_score,
        }
    )
    factor = assistance_factor(cs.hints_used)
    now = datetime.now(UTC)
    elapsed_s = interview_elapsed_s(session, cs.ended_at or now)

    async with session_scope() as db:
        locked = await db.get(CodingSession, cs_id, with_for_update=True)
        if locked is None:  # pragma: no cover - deleted meanwhile
            raise PermanentJobError("coding session disappeared")
        await db.execute(
            delete(CompetencyEvidence).where(
                CompetencyEvidence.coding_session_id == cs_id,
                CompetencyEvidence.source == EvidenceSource.CODING.value,
            )
        )
        rows = _evidence_rows(
            evaluation,
            inputs=inputs,
            competency_keys=competency_keys,
            final_run=final_run,
            elapsed_s=elapsed_s,
        )
        db.add_all(rows)
        await db.flush()
        try:
            await embed_and_store(
                db,
                rows,
                text="evidence",
                ai=services,
                context=CallContext(task=ModelTask.EMBEDDING, session_id=str(session.id)),
            )
        except AIError as exc:  # embeddings are optional for scoring; keep the evidence
            log.warning(
                "coding.evaluate.embedding_failed", coding_session_id=str(cs_id), error=type(exc).__name__
            )
        document = {
            **evaluation.model_dump(mode="json"),
            "status": "completed",
            "is_fallback": is_fallback,
            "raw_score_estimate": raw_score,
            "score_cap": cap,
            "score_cap_applied": cap_applied,
            "hints_used": cs.hints_used,
            "assistance_factor": factor,
            "adjusted_score": round(evaluation.score_estimate * factor, 2),
            "competency_keys": competency_keys,
            "difficulty": question.difficulty,
            "language": cs.language,
            "final_run_id": str(final_run.id) if final_run else None,
            "final_run_status": final_run.status if final_run else None,
            "model_call_id": model_call_id,
            "prompt_version": EVALUATION_USER.version,
            "evaluated_at": now.isoformat(),
            "evidence_ids": [str(r.id) for r in rows],
            "needs_human_review": is_fallback,
        }
        locked.evaluation = document

    event = {
        "type": "coding.evaluated",
        "session_id": str(session.id),
        "coding_session_id": str(cs_id),
        "coding_question_id": str(question.id),
        "score_estimate": document["score_estimate"],
        "adjusted_score": document["adjusted_score"],
        "assistance_factor": factor,
        "hints_used": cs.hints_used,
        "tests_passed": passed,
        "tests_total": total,
        "competency_keys": competency_keys,
        "evidence_ids": document["evidence_ids"],
        "is_fallback": is_fallback,
    }
    try:
        await publish_session_event(session.id, event)
    except Exception as exc:  # the bus is a notification; the evaluation is already durable
        log.warning("coding.evaluate.publish_failed", coding_session_id=str(cs_id), error=type(exc).__name__)
    log.info(
        "coding.evaluated",
        coding_session_id=str(cs_id),
        score=document["score_estimate"],
        adjusted=document["adjusted_score"],
        tests=f"{passed}/{total}",
        hints=cs.hints_used,
        fallback=is_fallback,
        evidence=len(document["evidence_ids"]),
    )
    return {
        "coding_session_id": str(cs_id),
        "score_estimate": document["score_estimate"],
        "is_fallback": is_fallback,
    }


__all__ = [
    "EvaluationRetry",
    "assistance_factor",
    "build_evaluation_messages",
    "evaluate_coding_session",
    "fallback_evaluation",
    "score_cap",
]
