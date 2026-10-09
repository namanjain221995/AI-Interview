"""Run complete interviews in-process against the real models.

One persona interview = ``session.prepare`` (plan, opening, prepared first
question) -> ``start`` -> candidate turns until the engine closes the session
(or a safety cap) -> deep evaluation of every substantive answer (run inline
right after each turn, as the worker would) -> final scores (computed, not
persisted) -> optional report narratives.

Time is simulated with a fake clock: before each answer the clock advances by
the time the interviewer's utterance and the candidate's answer take to speak,
so time-based policy (wrap-up, closing reserve) behaves like a live session.
The realtime runtime's speculative quick assessment during the end-of-turn
pause is emulated (``on_partial_transcript(silence_started=True)``) for
answers of six words or more.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import SessionEndReason
from app.services.interview.adapters import discover_scoring
from app.services.interview.config import EngineConfig
from app.services.interview.contracts import (
    CandidateTurn,
    SessionClosing,
    SpeechDelta,
    UtteranceMeta,
)
from app.services.interview.engine import InterviewEngine
from app.services.interview.evaluation import EvaluationService
from app.services.interview.sessions import SessionServiceImpl

from .personas import Exchange, Persona, PersonaResponder
from .world import PersonaWorld, QualityWorld, add_persona

SPEECH_WPS = 2.6  # interviewer speaking rate (words per second)
ANSWER_WPS = 2.3  # candidate speaking rate
TURN_GAP_S = 1.5


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass(slots=True)
class Entry:
    """One utterance in the transcript (interviewer or candidate)."""

    speaker: str  # interviewer | candidate
    text: str
    index: int  # 1-based per speaker
    elapsed_s: float = 0.0
    # interviewer
    action: str | None = None
    kind: str | None = None
    question_id: str | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    streamed: bool = False
    first_delta_ms: float | None = None
    responds_to: int | None = None  # candidate turn index this utterance replies to
    # candidate
    move: str | None = None
    speculated: bool = False

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(slots=True)
class PersonaRun:
    persona: str
    label: str
    session_id: str
    entries: list[Entry]
    end_reason: str | None
    closed_by_engine: bool
    candidate_turns: int
    duration_s: float
    plan_source: str | None
    state: dict[str, Any]
    scores: dict[str, Any]
    report: dict[str, Any] | None
    latency_ms: list[float]
    wall_s: dict[str, float]
    errors: list[str] = field(default_factory=list)

    def interviewer(self) -> list[Entry]:
        return [e for e in self.entries if e.speaker == "interviewer"]

    def candidate(self) -> list[Entry]:
        return [e for e in self.entries if e.speaker == "candidate"]

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return out


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, round(0.95 * (len(ordered) - 1))))
    return {
        "n": len(ordered),
        "p50": round(statistics.median(ordered), 1),
        "p95": round(ordered[p95_index], 1),
        "max": round(ordered[-1], 1),
    }


def _state_digest(state: Any) -> dict[str, Any]:
    return {
        "status": state.status.value,
        "end_reason": state.end_reason.value if state.end_reason else None,
        "questions": [
            {
                "id": q.id,
                "action": q.action.value,
                "competency": q.competency_key,
                "difficulty": q.difficulty.value,
                "topic": q.topic,
                "claim_id": q.claim_id,
                "text": q.text,
            }
            for q in state.questions
        ],
        "answers": [
            {
                "id": a.id,
                "quality": a.quality.value if a.quality else None,
                "source": a.quality_source,
                "special": a.special,
                "evaluated": a.evaluated,
                "words": a.word_count,
            }
            for a in state.answers
        ],
        "claims": {
            cid: {"status": c.status.value, "attempts": c.attempts, "claim": c.claim.claim, "note": c.note}
            for cid, c in state.claims.items()
        },
        "contradictions": [
            {"id": c.id, "description": c.description, "severity": c.severity, "status": c.status}
            for c in state.contradictions
        ],
        "competencies": {
            k: {
                "score": round(c.score_estimate, 2),
                "confidence": round(c.confidence, 3),
                "evidence": c.evidence_count,
                "sufficient": c.sufficient_evidence,
                "asked": c.questions_asked,
                "difficulty_seen": dict(c.difficulty_seen),
            }
            for k, c in state.competencies.items()
        },
        "difficulty_counts": dict(state.difficulty_counts),
        "deviations": state.deviations,
        "fallbacks_used": state.fallbacks_used,
    }


async def _evaluations(session_id: str) -> list[dict[str, Any]]:
    """Deep evaluation per answer (for scoring analysis in the results)."""
    import uuid

    from sqlalchemy import select

    from app.db.models import Answer, AnswerEvaluation
    from app.db.session import session_scope

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AnswerEvaluation, Answer.sequence)
                .join(Answer, Answer.id == AnswerEvaluation.answer_id)
                .where(AnswerEvaluation.session_id == uuid.UUID(session_id))
                .order_by(Answer.sequence)
            )
        ).all()
    out = []
    for row, sequence in rows:
        doc = row.evaluation or {}
        out.append(
            {
                "answer_sequence": sequence,
                "quality": row.overall_quality,
                "confidence": row.confidence,
                "fallback": row.is_fallback,
                "summary": doc.get("summary"),
                "scores": {k: v for k, v in (doc.get("scores") or {}).items() if v is not None},
                "evidence": [
                    f"{e.get('competency_key')}/{e.get('dimension')}/{e.get('signal')} {e.get('strength')}"
                    for e in doc.get("evidence") or []
                ],
                "missing": doc.get("missing_details"),
                "contradictions": doc.get("contradictions"),
                "claims": doc.get("claim_verifications"),
            }
        )
    return out


async def _scores(session_id: str) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.scoring.persistence import compute_session_scores

    async with session_scope() as db:
        comp = await compute_session_scores(db, session_id)
    rubric = comp.rubric
    return {
        "overall": round(rubric.overall_score, 2),
        "confidence": round(rubric.overall_confidence, 3),
        "required_coverage": round(rubric.required_coverage, 3),
        "recommendation": comp.recommendation.recommendation.value,
        "rationale": comp.recommendation.rationale,
        "competencies": {
            k: {
                "score": round(s.score_estimate, 2),
                "confidence": round(s.confidence, 3),
                "evidence": s.evidence_count,
                "sufficient": s.sufficient_evidence,
            }
            for k, s in comp.states.items()
        },
    }


class InterviewRunner:
    def __init__(
        self,
        ai: Any,
        world: QualityWorld,
        *,
        max_turns: int = 12,
        speculate: bool = True,
        reports: bool = False,
        engine_overrides: dict[str, Any] | None = None,
        wall_budget_s: float | None = None,
        log: Any = print,
    ) -> None:
        self.ai = ai
        self.world = world
        self.max_turns = max_turns
        self.speculate = speculate
        self.reports = reports
        self.engine_overrides = engine_overrides or {}
        self.wall_budget_s = wall_budget_s
        self.log = log

    def _engine(self, clock: FakeClock) -> InterviewEngine:
        config = EngineConfig(
            closing_reserve_s=75.0, default_interviewer_name="Maya", **self.engine_overrides
        )
        return InterviewEngine(ai=self.ai, scoring=discover_scoring(), config=config, clock=clock)

    async def _evaluate_pending(
        self, engine: InterviewEngine, sid: str, errors: list[str], *, enabled: bool = True
    ) -> int:
        if not enabled:
            return 0
        state = await engine.load_state(sid)
        done = 0
        service = EvaluationService(ai=self.ai)
        for answer_id in list(state.pending_evaluations):
            # Like the worker: retryable model failures are retried (3 attempts); only the
            # last attempt falls back to the neutral, human-review evaluation.
            for attempt in range(1, 4):
                try:
                    await service.evaluate(sid, answer_id, is_last_attempt=attempt == 3)
                    done += 1
                    break
                except Exception as exc:  # noqa: BLE001 - recorded in the run, the interview goes on
                    if attempt == 3:
                        errors.append(f"evaluate {answer_id}: {type(exc).__name__}: {exc}"[:300])
        return done

    async def _reply(
        self,
        responder: PersonaResponder,
        history: list[Exchange],
        last: Entry | None,
        turn: int,
        persona: Persona,
        errors: list[str],
    ) -> tuple[str, str]:
        question = last.text if last else ""
        hint = None
        if last is not None and "?" not in last.text and last.action not in ("close_interview",):
            hint = "The interviewer is waiting for your answer to their earlier question; answer it now."
        try:
            text, move = await responder.reply(list(history), question, turn_index=turn, hint=hint)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"persona turn {turn}: {type(exc).__name__}: {exc}"[:300])
            text, move = "Sorry, could you repeat the question?", "error"
        if last is not None and last.action == "invite_candidate_questions" and persona.end_question:
            move = "end_question"
        return text, move

    async def run(self, persona: Persona) -> PersonaRun:
        from app.db.session import session_scope

        started = time.perf_counter()
        wall: dict[str, float] = {"persona_s": 0.0, "turn_s": 0.0, "evaluation_s": 0.0, "speculation_s": 0.0}
        errors: list[str] = []
        pw: PersonaWorld = await add_persona(self.world, persona)
        clock = FakeClock()
        engine = self._engine(clock)
        sessions = SessionServiceImpl(config=engine.config)
        async with session_scope() as db:
            sid = str((await sessions.create_or_resume(db, interview_id=pw.interview_id)).id)
        t0 = time.perf_counter()
        await engine.prepare_session(sid)
        wall["prepare_s"] = time.perf_counter() - t0
        responder = PersonaResponder(self.ai, persona, job_title=self.world.job_title)
        entries: list[Entry] = []
        history: list[Exchange] = []
        latencies: list[float] = []
        counters = {"interviewer": 0, "candidate": 0}
        end_reason: str | None = None

        def elapsed() -> float:
            return (clock.now - start_time).total_seconds()

        def record_interviewer(events: list[Any], first_ms: float | None, responds_to: int | None) -> None:
            nonlocal end_reason
            streamed_ids = {e.utterance_id for e in events if isinstance(e, SpeechDelta)}
            for ev in events:
                if isinstance(ev, UtteranceMeta):
                    counters["interviewer"] += 1
                    action = ev.decision.get("action") if isinstance(ev.decision, dict) else None
                    entry = Entry(
                        speaker="interviewer",
                        text=ev.full_text,
                        index=counters["interviewer"],
                        elapsed_s=round(elapsed(), 1),
                        action=action,
                        kind=None,
                        question_id=ev.question_id,
                        decision=dict(ev.decision) if isinstance(ev.decision, dict) else {},
                        streamed=ev.utterance_id in streamed_ids,
                        first_delta_ms=first_ms if ev.utterance_id in streamed_ids else None,
                        responds_to=responds_to,
                    )
                    entries.append(entry)
                    history.append(Exchange("interviewer", ev.full_text))
                elif isinstance(ev, SessionClosing):
                    end_reason = ev.reason.value

        start_time = clock.now
        events = [e async for e in engine.start(sid)]
        record_interviewer(events, None, None)
        for ev in events:
            if isinstance(ev, UtteranceMeta):
                clock.advance(len(ev.full_text.split()) / SPEECH_WPS)
                await engine.mark_utterance_delivered(sid, ev.utterance_id, spoken_text=ev.full_text)
        await engine.drain_background(15)

        turn = 0
        prefetched: asyncio.Task[tuple[str, str]] | None = None
        while end_reason is None and turn < self.max_turns:
            if self.wall_budget_s is not None and time.perf_counter() - started > self.wall_budget_s:
                errors.append(f"wall budget {self.wall_budget_s:.0f}s reached after {turn} candidate turns")
                break
            last = next((e for e in reversed(entries) if e.speaker == "interviewer"), None)
            t0 = time.perf_counter()
            if prefetched is not None:
                text, move = await prefetched
                prefetched = None
            else:
                text, move = await self._reply(responder, history, last, turn, persona, errors)
            wall["persona_s"] += time.perf_counter() - t0
            words = len(text.split())
            answer_s = max(1.5, words / ANSWER_WPS)
            began_ms = int(elapsed() * 1000 + TURN_GAP_S * 1000)
            clock.advance(TURN_GAP_S + answer_s)
            ended_ms = int(elapsed() * 1000)
            speculated = False
            if self.speculate and words >= 6:
                t0 = time.perf_counter()
                await engine.on_partial_transcript(sid, text, silence_started=True)
                await engine.wait_speculative(sid, timeout=10)
                wall["speculation_s"] += time.perf_counter() - t0
                speculated = True
            counters["candidate"] += 1
            entries.append(
                Entry(
                    speaker="candidate",
                    text=text,
                    index=counters["candidate"],
                    elapsed_s=round(elapsed(), 1),
                    move=move,
                    speculated=speculated,
                )
            )
            history.append(Exchange("candidate", text))
            call = time.perf_counter()
            first_ms: float | None = None
            events = []
            try:
                async for ev in engine.handle_candidate_turn(
                    sid, CandidateTurn(text=text, started_ms=began_ms, ended_ms=ended_ms)
                ):
                    if first_ms is None and isinstance(ev, SpeechDelta):
                        first_ms = (time.perf_counter() - call) * 1000.0
                    events.append(ev)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"turn {turn}: {type(exc).__name__}: {exc}"[:300])
            wall["turn_s"] += time.perf_counter() - call
            if first_ms is not None:
                latencies.append(first_ms)
            record_interviewer(events, first_ms, counters["candidate"])
            for ev in events:
                if isinstance(ev, UtteranceMeta):
                    clock.advance(len(ev.full_text.split()) / SPEECH_WPS)
                    await engine.mark_utterance_delivered(sid, ev.utterance_id, spoken_text=ev.full_text)
            await engine.drain_background(15)
            # The worker evaluates while the candidate prepares the next answer;
            # the evaluation is always applied before the next turn.
            pending_eval = asyncio.get_running_loop().create_task(
                self._evaluate_pending(engine, sid, errors, enabled=persona.evaluate)
            )
            eval_started = time.perf_counter()
            prefetched = None
            if not any(isinstance(ev, SessionClosing) for ev in events) and turn + 1 < self.max_turns:
                nxt = next((e for e in reversed(entries) if e.speaker == "interviewer"), None)
                if nxt is not None:
                    prefetched = asyncio.get_running_loop().create_task(
                        self._reply(responder, history, nxt, turn + 1, persona, errors)
                    )
            await pending_eval
            wall["evaluation_s"] += time.perf_counter() - eval_started
            self.log(
                f"[{persona.key}] t{turn} {move:<18} cand {words:>3}w | "
                + " || ".join(
                    f"{e.action}: {e.text[:140]}"
                    for e in entries
                    if e.speaker == "interviewer" and e.responds_to == counters["candidate"]
                )
                + (f" | first {first_ms:.0f} ms" if first_ms is not None else "")
            )
            turn += 1
        closed_by_engine = end_reason is not None
        if not closed_by_engine:
            events = [e async for e in engine.end_session(sid, SessionEndReason.TIME_LIMIT)]
            record_interviewer(events, None, None)
            if turn >= self.max_turns:
                errors.append(f"safety cap: {self.max_turns} candidate turns without a close")
        t0 = time.perf_counter()
        await self._evaluate_pending(engine, sid, errors, enabled=persona.evaluate)
        wall["evaluation_s"] += time.perf_counter() - t0
        await engine.drain_background(15)
        state = await engine.load_state(sid)
        digest = _state_digest(state)
        digest["evaluations"] = await _evaluations(sid)
        scores = await _scores(sid)
        report = None
        if self.reports:
            from app.services.reports.demo import build_demo_report

            t0 = time.perf_counter()
            try:
                report = await build_demo_report(sid, use_llm=True, persist=False)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"report: {type(exc).__name__}: {exc}"[:300])
            wall["report_s"] = time.perf_counter() - t0
        await engine.aclose()
        wall["total_s"] = time.perf_counter() - started
        return PersonaRun(
            persona=persona.key,
            label=persona.label,
            session_id=sid,
            entries=entries,
            end_reason=end_reason or (state.end_reason.value if state.end_reason else None),
            closed_by_engine=closed_by_engine,
            candidate_turns=turn,
            duration_s=round(elapsed(), 1),
            plan_source=state.plan_source,
            state=digest,
            scores=scores,
            report=report,
            latency_ms=[round(x, 1) for x in latencies],
            wall_s={k: round(v, 1) for k, v in wall.items()},
            errors=errors,
        )


__all__ = ["Entry", "FakeClock", "InterviewRunner", "PersonaRun", "latency_summary"]
