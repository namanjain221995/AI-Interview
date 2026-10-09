"""``InterviewEngine``: the adaptive interview engine behind ``TurnEngine``.

Every operation on a session runs under a per-session ``asyncio.Lock``
(lock-free only for ``mark_utterance_delivered`` while a turn is in flight,
which is queued and applied at the end of that turn). Each operation loads
the hot state from Redis (rebuilding from Postgres when missing), applies
results that arrived from background jobs (deep evaluations, memory
summaries), does its work and persists: Redis immediately (version-guarded
CAS), Postgres at every committed turn (and debounced otherwise). Every
decision is recorded in ``interview_events``.

Event-stream methods (``start``, ``handle_candidate_turn``, ...) return an
:class:`EventStream`: iterate it directly (``async for ev in
engine.start(sid)``) - awaiting it first also works. ``aclose()`` on the
stream (barge-in) closes the LLM HTTP stream immediately; whatever was
already spoken is persisted with a synthesized decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine
from contextlib import aclosing, asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIServices, CallContext
from app.ai.prompts import prompt_version
from app.ai.registry import get_ai
from app.core.logging import bind_context, get_logger
from app.core.metrics import ERRORS
from app.core.queue import enqueue, publish_session_event
from app.db.models import CodingSession, Interview, InterviewSession, Question
from app.db.session import session_scope
from app.domain.enums import (
    AvatarState,
    CodeLanguage,
    Difficulty,
    InterviewStatus,
    ModelTask,
    SessionEndReason,
    SessionStatus,
    TurnAction,
    TurnState,
)
from app.schemas.ai import InterviewPlan, TurnDecision
from app.services.interview import behavior as beh
from app.services.interview.adapters import CodingPort, ScoringPort, get_scoring
from app.services.interview.attribution import guard_for_turn
from app.services.interview.closing import (
    GENERATED_REASONS,
    SPOKEN_REASONS,
    acceptable_closing,
    closing_prompt,
    report_note,
    template_closing,
)
from app.services.interview.coding_bridge import (
    CodingBridge,
    coding_competencies,
    preferred_language,
    run_summary,
    short_hint,
    transition_speech,
)
from app.services.interview.config import EngineConfig, get_engine_config
from app.services.interview.context import coding_user_prompt, system_prompt, turn_messages, user_prompt
from app.services.interview.contracts import (
    ActivityEvent,
    BehaviorSpec,
    CandidateTurn,
    CodingActivity,
    CodingEnded,
    CodingHintIssued,
    CodingStarted,
    InterruptedUtterance,
    SessionClosing,
    SpeechDelta,
    StateChanged,
    TimerEvent,
    TurnEvent,
    UtteranceBegin,
    UtteranceKind,
    UtteranceMeta,
)
from app.services.interview.evaluation import (
    coding_evaluation_from_document,
    pop_ready_evaluations,
    rebuild_hook,
    recompute_competencies,
)
from app.services.interview.grounding import ungrounded_reference
from app.services.interview.memory import apply_memory, needs_summary
from app.services.interview.opening import SpokenText, generate_opening, template_opening
from app.services.interview.planner import fallback_plan, generate_plan
from app.services.interview.policy import (
    CODING_FLOW_ACTIONS,
    FOLLOWUP_ACTIONS,
    NON_QUESTION_ACTIONS,
    BankCandidate,
    PolicyDirective,
    check_decision,
    compute_directive,
)
from app.services.interview.prompts import TURN_SYSTEM, TURN_USER
from app.services.interview.records import (
    add_answer,
    add_question,
    add_transcript,
    log_event,
    mark_interviewer_transcript_interrupted,
    mark_question_interrupted,
    store_answer_audio,
)
from app.services.interview.selection import (
    AskedVector,
    bank_candidates,
    bank_ids_missing_embeddings,
    embed_question,
    job_role_tags,
    load_asked_vectors,
)
from app.services.interview.sessions import attach_profile, latest_profile, resume_pending
from app.services.interview.special import SpecialKind, SpecialResult, classify
from app.services.interview.speculative import (
    PrepEntry,
    clear_prep,
    load_prep,
    normalize_text,
    quick_assess,
    store_prep,
)
from app.services.interview.state import (
    InterviewState,
    UtteranceRecord,
    utcnow,
)
from app.services.interview.state_store import ENDED_STATUSES, StateConflict, StateStore
from app.services.interview.transitions import (
    add_utterance,
    apply_decision,
    apply_evaluation,
    mark_delivered,
    merge_derived,
    record_answer,
    record_interviewer_text,
)
from app.services.interview.turns import (
    TURN_SCHEMA,
    TurnOutcome,
    fallback_utterance,
    pick,
    question_core,
    question_kind,
    recover_decision,
    stream_speech_object,
    synthesize_decision,
    turn_context,
)
from app.services.retrieval import cosine_similarity

log = get_logger(__name__)

PROMPT_VERSION = prompt_version("turn", TURN_SYSTEM.template + "\x00" + TURN_USER.template)
QUESTION_KINDS_FOR_UTTERANCE = {
    TurnAction.ASK_FOLLOWUP: "followup",
    TurnAction.REQUEST_SPECIFICITY: "followup",
    TurnAction.SIMPLIFY: "followup",
    TurnAction.VERIFY_CONTRADICTION: "followup",
    TurnAction.INCREASE_DIFFICULTY: "followup",
    TurnAction.ACKNOWLEDGE_AND_WAIT: "acknowledgment",
    TurnAction.ANSWER_CANDIDATE_QUESTION: "acknowledgment",
    TurnAction.CODING_COMMENT: "comment",
    TurnAction.GIVE_HINT: "hint",
    TurnAction.START_CODING: "transition",
    TurnAction.CLOSE_INTERVIEW: "closing",
    TurnAction.INTRODUCE: "intro",
}
REPEAT_LEADS = ("Sure.", "Of course.", "No problem.", "Sure, here it is again.")
SILENCE_PROMPTS = (
    "Take your time. Would you like me to repeat the question?",
    "No worries if this one isn't clicking. Would you like to give it a try, or shall we move on to something else?",
)


def turn_key(turn: CandidateTurn, text: str) -> str:
    """Stable identity of a candidate turn (duplicate delivery detection)."""
    return hashlib.blake2b(f"{turn.started_ms}:{turn.ended_ms}:{text}".encode(), digest_size=10).hexdigest()


def utterance_kind(action: TurnAction) -> UtteranceKind:
    return cast(UtteranceKind, QUESTION_KINDS_FOR_UTTERANCE.get(action, "question"))


class EventStream:
    """Async iterator of ``TurnEvent``; ``await stream`` returns the stream
    itself so both calling conventions work. ``aclose()`` cancels the turn."""

    def __init__(self, agen: AsyncGenerator[TurnEvent, None]) -> None:
        self._agen = agen

    def __aiter__(self) -> EventStream:
        return self

    async def __anext__(self) -> TurnEvent:
        return await self._agen.__anext__()

    async def aclose(self) -> None:
        await self._agen.aclose()

    async def athrow(self, *args: Any) -> TurnEvent:
        return await self._agen.athrow(*args)

    def __await__(self):  # noqa: ANN204
        async def _self() -> EventStream:
            return self

        return _self().__await__()

    async def collect(self) -> list[TurnEvent]:
        return [event async for event in self]


@dataclass(slots=True)
class PendingAnswer:
    answer_id: uuid.UUID
    turn: CandidateTurn
    special: SpecialResult
    quick: dict[str, Any] | None
    question_id: str | None
    evaluate: bool


class SessionNotFound(LookupError):
    pass


class InterviewEngine:
    """Implements ``app.services.interview.contracts.TurnEngine``."""

    def __init__(
        self,
        *,
        store: StateStore | None = None,
        config: EngineConfig | None = None,
        ai: AIServices | None = None,
        scoring: ScoringPort | None = None,
        coding: CodingPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or get_engine_config()
        self.store = store or StateStore(config=self.config, rebuild_hook=rebuild_hook)
        self._ai = ai
        self._scoring = scoring
        self.coding = CodingBridge(coding)
        self._clock = clock or utcnow
        self._locks: dict[str, asyncio.Lock] = {}
        self._prep: dict[str, PrepEntry] = {}
        self._prep_tasks: dict[str, asyncio.Task[None]] = {}
        self._partial: dict[str, str] = {}
        self._pending_delivery: dict[str, list[tuple[str, str]]] = {}
        self._bank_cache: dict[tuple[str, str, str], tuple[float, list[BankCandidate]]] = {}
        self._asked_vectors: dict[str, list[AskedVector]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ================================================================ plumbing
    @property
    def ai(self) -> AIServices:
        return self._ai or get_ai()

    @property
    def scoring(self) -> ScoringPort:
        return self._scoring or get_scoring()

    @property
    def intake(self) -> Any:
        """Intake mode (``app.services.intake.conductor.IntakeConductor``), used only when ``state.intake``."""
        conductor = getattr(self, "_intake_conductor", None)
        if conductor is None:
            from app.services.intake.conductor import IntakeConductor

            conductor = self._intake_conductor = IntakeConductor(self)
        return conductor

    @property
    def redis(self):  # noqa: ANN201
        return self.store.redis

    def now(self) -> datetime:
        return self._clock()

    def _spawn(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                ERRORS.labels(component="engine_background").inc()
                log.warning(
                    "engine.background_failed",
                    task=name,
                    error=f"{type(t.exception()).__name__}: {t.exception()}"[:200],
                )

        task.add_done_callback(_done)
        return task

    async def drain_background(self, timeout: float = 10.0) -> None:  # noqa: ASYNC109
        """Wait for background work (embeddings, prefetch, speculative prep)."""
        deadline = time.monotonic() + timeout
        while self._tasks and time.monotonic() < deadline:
            pending = list(self._tasks)
            await asyncio.wait(pending, timeout=max(0.01, deadline - time.monotonic()))

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.store.drain()

    @asynccontextmanager
    async def _session_lock(self, session_id: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        acquired = False
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self.config.lock_timeout_s)
            acquired = True
        except TimeoutError:
            ERRORS.labels(component="engine_lock").inc()
            log.error("engine.lock_timeout", session_id=session_id, timeout_s=self.config.lock_timeout_s)
        try:
            yield
        finally:
            if acquired:
                lock.release()

    async def _load(self, session_id: str) -> InterviewState:
        state = await self.store.load(session_id)
        if state is None:
            raise SessionNotFound(f"interview session {session_id} not found")
        bind_context(session_id=session_id)
        await self._sync(state)
        return state

    async def _sync(self, state: InterviewState) -> None:
        """Apply results produced outside the live turn (evaluations, memory,
        deliveries queued while a turn was running)."""
        payloads = await pop_ready_evaluations(self.redis, state.session_id)
        if payloads:
            known = {a.id for a in state.answers}
            for payload in sorted(payloads, key=lambda p: str(p.get("answer_id"))):
                if payload.get("answer_id") in known:
                    apply_evaluation(state, payload)
        await apply_memory(state, self.redis)
        await self._apply_coding_evaluations(state)
        for utterance_id, spoken in self._pending_delivery.pop(state.session_id, []):
            mark_delivered(state, utterance_id, spoken, elapsed_s=state.elapsed_seconds(self.now()))

    async def _apply_coding_evaluations(self, state: InterviewState) -> bool:
        """Refresh competency state once ``coding.evaluate`` has stored a coding
        evaluation (announced as ``coding.evaluated`` on the bus): reload the
        evaluation rows and recompute from evidence."""
        cs = state.coding
        pending = [
            i
            for i in cs.coding_session_ids
            if i not in cs.evaluated_session_ids and not (cs.active and i == cs.coding_session_id)
        ]
        if not pending:
            return False
        ids = [uuid.UUID(i) for i in pending]
        try:
            async with session_scope() as db:
                rows = (
                    await db.execute(
                        select(CodingSession.id, CodingSession.evaluation).where(CodingSession.id.in_(ids))
                    )
                ).all()
                ready = [str(r[0]) for r in rows if coding_evaluation_from_document(r[1]) is not None]
                if not ready:
                    return False
                states = await recompute_competencies(
                    db,
                    state.session_id,
                    state.round_config,
                    state.competencies,
                    elapsed_s=self._elapsed(state),
                    scoring=self.scoring,
                )
                merge_derived(state, states)
                cs.evaluated_session_ids.extend(ready)
                await log_event(
                    db,
                    state.session_id,
                    "coding.evaluation_applied",
                    {"coding_session_ids": ready},
                    elapsed_s=self._elapsed(state),
                )
        except Exception as exc:  # noqa: BLE001 - never block a turn on this refresh
            ERRORS.labels(component="engine_coding").inc()
            log.warning(
                "engine.coding_evaluation_refresh_failed",
                session_id=state.session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return False
        return True

    async def _save(
        self,
        state: InterviewState,
        *,
        durable: bool = False,
        db: AsyncSession | None = None,
        extra_columns: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.store.save(state, durable=durable, db=db, extra_columns=extra_columns)
        except StateConflict as exc:
            # Another process wrote meanwhile; the session lock makes this rare. Last writer wins
            # but the version stays monotonic.
            ERRORS.labels(component="engine_state").inc()
            log.warning(
                "engine.state_conflict", session_id=state.session_id, expected=exc.expected, stored=exc.stored
            )
            state.version = max(exc.stored, state.version)
            await self.store.save(state, durable=durable, db=db, force=True, extra_columns=extra_columns)

    def _elapsed(self, state: InterviewState) -> float:
        return state.elapsed_seconds(self.now())

    # ============================================================ preparation
    async def prepare_session(self, session_id: str) -> None:
        """session.prepare: wait for the candidate profile (bounded), then plan,
        opening, prepared first question and initial bank candidates; status
        preparing -> ready. Idempotent; re-enqueues itself while a resume is
        still being processed."""
        state = await self.store.load(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        if state.status not in (SessionStatus.CREATED, SessionStatus.PREPARING):
            log.info("engine.prepare_skipped", session_id=session_id, status=state.status.value)
            return
        now = self.now()

        def _mark(s: InterviewState) -> bool:
            if s.status not in (SessionStatus.CREATED, SessionStatus.PREPARING):
                return False
            changed = False
            if s.status == SessionStatus.CREATED:
                s.status = SessionStatus.PREPARING
                changed = True
            if s.profile_wait_started_at is None:
                s.profile_wait_started_at = now
                changed = True
            return changed

        state = await self.store.save_with_retry(session_id, _mark, durable=True) or state
        if state.status not in (SessionStatus.CREATED, SessionStatus.PREPARING):
            return
        gate, profile_row = await self._profile_gate(state)
        if gate == "wait":
            attempt = state.prepare_attempts + 1

            def _bump(s: InterviewState) -> bool:
                s.prepare_attempts = attempt
                return True

            await self.store.save_with_retry(session_id, _bump)
            await enqueue(
                "session.prepare",
                {"session_id": session_id},
                dedupe_key=f"session.prepare:{session_id}:wait:{attempt}",
                delay_seconds=self.config.profile_wait_retry_s,
            )
            log.info("engine.prepare_waiting_for_profile", session_id=session_id, attempt=attempt)
            return
        profile_doc: dict[str, Any] | None = None
        profile_status = state.profile_status
        if profile_row is not None:
            profile_doc = dict(profile_row.profile or {})
            profile_status = profile_row.status
            attach_profile(state, profile_doc, profile_status=profile_status)
            async with session_scope() as db:
                await db.execute(
                    update(InterviewSession)
                    .where(InterviewSession.id == uuid.UUID(session_id))
                    .values(profile_id=profile_row.id)
                )
        elif gate == "timeout":
            profile_status = "timeout"
            log.warning(
                "engine.profile_wait_exceeded",
                session_id=session_id,
                budget_s=self.config.profile_wait_budget_s,
            )
        elif not state.candidate_profile:
            profile_status = profile_status or "missing"
        state.profile_status = profile_status
        if state.intake is not None:
            await self._prepare_intake(state, profile_doc, profile_status)
            return

        started = time.monotonic()
        timings: dict[str, float] = {}

        async def _timed(name: str, coro: Any) -> Any:
            t0 = time.monotonic()
            try:
                return await coro
            finally:
                timings[name] = round((time.monotonic() - t0) * 1000.0, 1)

        async def _fallback_plan() -> tuple[InterviewPlan, str]:
            return fallback_plan(state), "fallback"

        # The opening and the first question only need what the deterministic plan
        # already provides (segment kinds, top claim): generate them concurrently
        # with the (bounded) model plan.
        draft = state.model_copy(deep=True)
        draft.plan = fallback_plan(draft)
        plan_coro = (
            _fallback_plan()
            if gate == "timeout"
            else generate_plan(state, ai=self.ai, timeout_s=self.config.plan_timeout_s)
        )
        (plan, plan_source), (opening, opening_source), prepared_first = await asyncio.gather(
            _timed("plan_ms", plan_coro),
            _timed(
                "opening_ms", generate_opening(draft, ai=self.ai, timeout_s=self.config.opening_timeout_s)
            ),
            _timed("first_question_ms", self._prepare_first_question(draft)),
        )
        state.plan = plan
        await asyncio.gather(
            _timed("prefix_warm_ms", self._warm_prefix(state)),
            _timed("initial_bank_ms", self._initial_bank_candidates(state)),
        )
        missing: list[str] = []
        try:
            async with session_scope() as db:
                missing = await bank_ids_missing_embeddings(
                    db, state.organization_id, state.round_config.competency_keys()
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("engine.bank_embedding_check_failed", session_id=session_id, error=type(exc).__name__)
        if missing:
            digest = hashlib.sha1(",".join(sorted(missing)).encode()).hexdigest()[:16]  # noqa: S324 - dedupe key only
            with contextlib.suppress(Exception):
                await enqueue(
                    "question_bank.embed",
                    {"question_ids": missing[:500]},
                    dedupe_key=f"question_bank.embed:{digest}",
                )

        applied: dict[str, Any] = {}

        def _apply(s: InterviewState) -> bool:
            if s.status not in (SessionStatus.CREATED, SessionStatus.PREPARING):
                applied["skipped"] = s.status.value
                return False
            if profile_doc is not None:
                attach_profile(s, profile_doc, profile_status=profile_status)
            s.profile_status = profile_status
            s.plan = plan
            s.plan_source = plan_source
            s.opening_text = opening
            s.prepared_first_question = prepared_first
            s.prepared_at = self.now()
            s.status = SessionStatus.READY
            return True

        saved = await self.store.save_with_retry(session_id, _apply, durable=True)
        duration_ms = round((time.monotonic() - started) * 1000.0, 1)
        if applied.get("skipped"):
            log.info("engine.prepare_discarded", session_id=session_id, status=applied["skipped"])
            return
        async with session_scope() as db:
            await log_event(
                db,
                session_id,
                "session.prepared",
                {
                    "plan_source": plan_source,
                    "opening_source": opening_source,
                    "first_question_prepared": prepared_first is not None,
                    "profile_status": profile_status,
                    "profile_wait_attempts": state.prepare_attempts,
                    "duration_ms": duration_ms,
                    **timings,
                    "bank_missing_embeddings": len(missing),
                },
            )
        await publish_session_event(session_id, {"type": "session.ready", "session_id": session_id})
        log.info(
            "engine.session_prepared",
            session_id=session_id,
            plan_source=plan_source,
            opening_source=opening_source,
            profile_status=profile_status,
            duration_ms=duration_ms,
            version=saved.version if saved else None,
        )

    async def _prepare_intake(
        self, state: InterviewState, profile_doc: dict[str, Any] | None, profile_status: str | None
    ) -> None:
        """session.prepare for intake rounds (INTAKE_INTERVIEW.md 6.8): claims, stage rows, plan mirror and
        opening from the conductor; no model plan, first question, prefix warm-up or bank retrieval."""
        session_id = state.session_id
        started = time.monotonic()
        intake, plan, opening = await self.intake.prepare(state, profile_doc)
        applied: dict[str, Any] = {}

        def _apply(s: InterviewState) -> bool:
            if s.status not in (SessionStatus.CREATED, SessionStatus.PREPARING):
                applied["skipped"] = s.status.value
                return False
            if profile_doc is not None:
                attach_profile(s, profile_doc, profile_status=profile_status)
            s.profile_status = profile_status
            s.intake = intake
            s.plan = plan
            s.plan_source = "intake"
            s.opening_text = opening
            s.prepared_first_question = None
            s.prepared_at = self.now()
            s.status = SessionStatus.READY
            return True

        await self.store.save_with_retry(session_id, _apply, durable=True)
        if applied.get("skipped"):
            log.info("engine.prepare_discarded", session_id=session_id, status=applied["skipped"])
            return
        duration_ms = round((time.monotonic() - started) * 1000.0, 1)
        async with session_scope() as db:
            await log_event(
                db,
                session_id,
                "session.prepared",
                {
                    "plan_source": "intake",
                    "opening_source": "template",
                    "profile_status": profile_status,
                    "claims": len(intake.claims),
                    "duration_ms": duration_ms,
                },
            )
        await publish_session_event(session_id, {"type": "session.ready", "session_id": session_id})
        log.info("engine.session_prepared", session_id=session_id, plan_source="intake", duration_ms=duration_ms)

    async def _profile_gate(self, state: InterviewState) -> tuple[str, Any | None]:
        """("ok"|"wait"|"timeout", newly found profile row). Waits only while a
        resume for the candidate is still being processed; when the wait budget
        runs out the latest usable profile or the candidate's quick profile
        (status "quick") is used if one exists."""
        if state.candidate_profile:
            return "ok", None
        async with session_scope() as db:
            row = await latest_profile(db, uuid.UUID(state.candidate_id), uuid.UUID(state.job_id))
            if row is not None:
                return "ok", row
            pending = await resume_pending(db, uuid.UUID(state.candidate_id))
        if not pending:
            return "ok", None
        started = state.profile_wait_started_at or self.now()
        if (self.now() - started).total_seconds() < self.config.profile_wait_budget_s:
            return "wait", None
        from app.services.resume.service import quick_profile_fallback

        fallback = await quick_profile_fallback(state.candidate_id, state.job_id)
        log.info(
            "engine.profile_wait_fallback",
            session_id=state.session_id,
            profile_status=getattr(fallback, "status", None),
        )
        return "timeout", fallback

    async def _initial_bank_candidates(self, state: InterviewState) -> None:
        """Retrieve bank candidates for the leading targets (shared via Redis so
        the API process serves the first turns from cache)."""
        directive = compute_directive(state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s)
        for target in directive.targets[:3]:
            try:
                await self._retrieve_bank_raw(
                    state, target.key, target.target_difficulty.value, target.name, target.uncovered_topics
                )
            except Exception as exc:  # noqa: BLE001 - optional warm-up
                log.info("engine.initial_bank_skipped", session_id=state.session_id, error=type(exc).__name__)
                return

    async def _warm_prefix(self, state: InterviewState) -> None:
        """Prefill the static per-session system prompt once so vLLM's prefix
        cache serves the first live turn (max_tokens=1)."""
        try:
            await self.ai.llm.chat(
                [{"role": "system", "content": system_prompt(state)}, {"role": "user", "content": "Ready."}],
                context=CallContext(
                    task=ModelTask.OPENING,
                    session_id=state.session_id,
                    organization_id=state.organization_id,
                    prompt_version="prefix-warmup",
                ),
                endpoint="main",
                temperature=0.0,
                max_tokens=1,
                timeout=8.0,
            )
        except AIError as exc:
            log.info("engine.prefix_warmup_skipped", session_id=state.session_id, reason=type(exc).__name__)

    async def _prepare_first_question(self, state: InterviewState) -> dict[str, Any] | None:
        directive = compute_directive(
            state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s, first_question=True
        )
        directive.candidate_bank_questions = await self._fetch_bank(state, directive, bounded=False)
        messages = turn_messages(system_prompt(state), user_prompt(state, directive, last_answer=None))
        try:
            result = await self.ai.llm.structured(
                TurnDecision,
                messages,
                context=turn_context(state, PROMPT_VERSION),
                temperature=self.config.turn_temperature,
                max_tokens=self.config.turn_max_tokens,
                timeout=self.config.first_question_timeout_s,
                max_attempts=2,
            )
        except AIError as exc:
            log.info("engine.first_question_live", session_id=state.session_id, reason=type(exc).__name__)
            return None
        check = check_decision(
            directive, result.value, state, valid_bank_ids=[b.id for b in directive.candidate_bank_questions]
        )
        decision = check.decision
        if decision.action in NON_QUESTION_ACTIONS or "?" not in decision.speech:
            return None
        return {
            "decision": decision.model_dump(mode="json"),
            "deviations": check.deviations,
            "directive": directive.summary(),
        }

    async def load_state(self, session_id: str) -> InterviewState:
        state = await self.store.load(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        return state

    # ================================================================== start
    def start(self, session_id: str) -> EventStream:
        return EventStream(self._start(session_id))

    async def _start(self, session_id: str) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            if state.status in (SessionStatus.LIVE, SessionStatus.PAUSED) and state.utterances:
                async with aclosing(self._resume_events(state, reconnect=False)) as _relay:
                    async for event in _relay:
                        yield event
                return
            async with aclosing(self._start_events(state)) as _relay:
                async for event in _relay:
                    yield event

    async def _wait_for_preparation(self, state: InterviewState) -> InterviewState:
        deadline = time.monotonic() + self.config.start_wait_for_plan_s
        current = state
        while (
            current.status in (SessionStatus.CREATED, SessionStatus.PREPARING) and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.25)
            latest = await self.store.load_cached(state.session_id)
            if latest is not None:
                current = latest
        if current is not state:
            await self._sync(current)
        return current

    async def _start_events(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        if state.status in (SessionStatus.CREATED, SessionStatus.PREPARING):
            state = await self._wait_for_preparation(state)
        if state.status in (SessionStatus.CREATED, SessionStatus.PREPARING):
            # Do not begin while the candidate's profile is still being generated
            # (unless the wait budget is exhausted); the client retries on session.ready.
            gate, profile_row = await self._profile_gate(state)
            if gate == "wait":
                log.info("engine.start_deferred_profile", session_id=state.session_id)
                yield StateChanged(reason="preparing")
                return
            if profile_row is not None:
                attach_profile(state, dict(profile_row.profile or {}), profile_status=profile_row.status)
                async with session_scope() as db:
                    await db.execute(
                        update(InterviewSession)
                        .where(InterviewSession.id == uuid.UUID(state.session_id))
                        .values(profile_id=profile_row.id)
                    )
            elif gate == "timeout":
                state.profile_status = "timeout"
        if state.intake is not None:
            await self.intake.ensure_prepared(state)
        if state.plan is None:
            state.plan = fallback_plan(state)
            state.plan_source = "fallback"
        if not state.opening_text:
            state.opening_text = template_opening(state)
        now = self.now()
        state.status = SessionStatus.LIVE
        state.resume(now)
        state.turn_state = TurnState.AI_SPEAKING
        intro_id = str(uuid.uuid4())
        intro_text = state.opening_text
        intro_behavior = beh.provisional(TurnAction.INTRODUCE)
        state.avatar_state = intro_behavior.state
        async with session_scope() as db:
            row = await add_transcript(
                db, state.session_id, speaker="interviewer", text_value=intro_text, start_ms=0, end_ms=0
            )
            add_utterance(
                state,
                UtteranceRecord(
                    id=intro_id,
                    kind="intro",
                    action=TurnAction.INTRODUCE,
                    text=intro_text,
                    at_s=0.0,
                    transcript_id=str(row.id),
                ),
            )
            record_interviewer_text(state, text=intro_text, at_s=0.0, question_id=None)
            await log_event(
                db,
                state.session_id,
                "session.started",
                {"plan_source": state.plan_source, "prepared": state.prepared_first_question is not None},
                elapsed_s=0.0,
            )
            await db.execute(
                update(Interview)
                .where(
                    Interview.id == uuid.UUID(state.interview_id),
                    Interview.status == InterviewStatus.INVITED.value,
                )
                .values(status=InterviewStatus.IN_PROGRESS.value)
            )
            await self._save(state, durable=True, db=db, extra_columns={"started_at": state.started_at})
        yield UtteranceBegin(utterance_id=intro_id, kind="intro", behavior=intro_behavior, text=intro_text)
        yield UtteranceMeta(
            utterance_id=intro_id,
            question_id=None,
            behavior=intro_behavior,
            decision={"action": TurnAction.INTRODUCE.value},
            full_text=intro_text,
        )
        if state.intake is not None:
            async with aclosing(self.intake.first_item(state)) as _relay:
                async for event in _relay:
                    yield event
            return

        prepared = state.prepared_first_question
        directive = compute_directive(
            state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s, first_question=True
        )
        if prepared:
            try:
                decision = TurnDecision.model_validate(prepared["decision"])
            except (KeyError, ValueError):
                decision = None
            if decision is not None:
                state.prepared_first_question = None
                async with aclosing(
                    self._emit_known_decision(
                        state,
                        directive,
                        decision,
                        deviations=list(prepared.get("deviations") or []),
                        source="prepared",
                    )
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
        directive.candidate_bank_questions = await self._cached_bank(state, directive)
        async with aclosing(self._decision_turn(state, directive, last_answer=None)) as _relay:
            async for event in _relay:
                yield event

    # ========================================================= candidate turn
    def handle_candidate_turn(self, session_id: str, turn: CandidateTurn) -> EventStream:
        return EventStream(self._handle_candidate_turn(session_id, turn))

    async def _handle_candidate_turn(
        self, session_id: str, turn: CandidateTurn
    ) -> AsyncGenerator[TurnEvent, None]:
        turn_started = time.monotonic()
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            self._partial.pop(session_id, None)
            if state.status in ENDED_STATUSES:
                return
            text = (turn.text or "").strip()
            replay = turn_key(turn, text) == state.last_turn_key
            if replay and state.conversation and state.conversation[-1].speaker != "candidate":
                log.info("engine.duplicate_turn_ignored", session_id=session_id)
                return
            if state.status == SessionStatus.PAUSED and state.intake is not None:
                await self.intake.paused_turn(state, turn, text, replay=replay)
                log.info("engine.answer_while_paused", session_id=session_id, intake=True)
                return
            if state.status == SessionStatus.PAUSED:
                # Transcription finished just after a disconnect: keep the answer
                # (and its evaluation) and reply when the session resumes.
                if not replay:
                    special = classify(
                        text,
                        awaiting_answer=True,
                        coding_active=state.coding.active,
                        questions_invited=state.candidate_questions_invited,
                    )
                    pending = await self._accept_answer(state, turn, text, special, None, persist=False)
                    await self._persist_pending_answer(state, pending)
                    log.info("engine.answer_while_paused", session_id=session_id)
                return
            if state.status != SessionStatus.LIVE:
                state.status = SessionStatus.LIVE
                state.resume(self.now())
            if state.intake is not None:
                async with aclosing(self.intake.candidate_turn(state, turn, text, replay=replay)) as _relay:
                    async for event in _relay:
                        yield event
                return
            if state.coding.active:
                async with aclosing(self._coding_turn(state, turn, text, replay=replay)) as _relay:
                    async for event in _relay:
                        yield event
                return
            awaiting = state.awaiting_answer or state.current_question_id is not None
            special = classify(
                text, awaiting_answer=awaiting, questions_invited=state.candidate_questions_invited
            )
            if special.kind is SpecialKind.REPEAT and not replay:
                await self._record_candidate_utterance(state, turn, text)
                async with aclosing(self._repeat_events(state)) as _relay:
                    async for event in _relay:
                        yield event
                return
            prep = await self._take_prep_async(session_id, text, state.current_question_id)
            self._cancel_speculation(session_id)  # free the model for the turn's own stream
            quick = prep.quick if prep is not None else None
            pending: PendingAnswer | None = None
            if not replay:
                pending = await self._accept_answer(state, turn, text, special, quick, persist=False)
            directive = compute_directive(
                state,
                quick=quick,
                special=special,
                now=self.now(),
                closing_reserve_s=self.config.closing_reserve_s,
            )
            if directive.must_close or directive.start_coding:
                if pending is not None:
                    await self._persist_pending_answer(state, pending)
                    pending = None
            if directive.must_close:
                async with aclosing(
                    self._closing_events(
                        state, directive.close_reason or SessionEndReason.TIME_LIMIT, last_answer=text
                    )
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
            if directive.start_coding:
                started = False
                async with aclosing(self._start_coding_events(state, ack=True)) as _relay:
                    async for event in _relay:
                        started = True
                        yield event
                if started:
                    return
                directive = compute_directive(
                    state,
                    quick=quick,
                    special=special,
                    now=self.now(),
                    closing_reserve_s=self.config.closing_reserve_s,
                )
            directive.candidate_bank_questions = await self._cached_bank(state, directive)
            persist_task: asyncio.Task[Any] | None = None
            if pending is not None and not turn.speculative:
                persist_task = asyncio.get_running_loop().create_task(
                    self._persist_pending_answer(state, pending), name="engine.persist_answer"
                )
            current = state.current_question()
            async with aclosing(
                self._decision_turn(
                    state,
                    directive,
                    last_answer=text,
                    current_question=current.text if current else None,
                    interruption=self._interruption_note(state, turn),
                    pending_answer=pending if turn.speculative else None,
                    speculative=turn.speculative,
                    persist_task=persist_task,
                    turn_started=turn_started,
                )
            ) as _relay:
                async for event in _relay:
                    yield event

    async def _accept_answer(
        self,
        state: InterviewState,
        turn: CandidateTurn,
        text: str,
        special: SpecialResult,
        quick: Any,
        *,
        persist: bool = True,
    ) -> PendingAnswer:
        answer_id = uuid.uuid4()
        quality = special.quality if special.is_special else (quick.quality if quick is not None else None)
        source = (
            "special"
            if special.is_special and special.quality is not None
            else ("quick" if quick is not None else None)
        )
        question_id = state.current_question_id
        evaluate = bool(question_id and text and (special.kind in (SpecialKind.NONE, SpecialKind.DONT_KNOW)))
        if state.intake is not None:
            evaluate = False  # intake answers are judged by intake.evaluate after the session (8.1)
        record_answer(
            state,
            answer_id=str(answer_id),
            text=text,
            typed=turn.typed,
            started_s=turn.started_ms / 1000.0,
            ended_s=turn.ended_ms / 1000.0,
            interrupted_ai=turn.interrupted_ai,
            special=special.kind.value if special.is_special else None,
            quality=quality,
            quality_source=source,
        )
        state.last_turn_key = turn_key(turn, text)
        if evaluate:
            state.pending_evaluations.append(str(answer_id))
        pending = PendingAnswer(
            answer_id=answer_id,
            turn=turn,
            special=special,
            quick=quick.model_dump(mode="json") if quick is not None else None,
            question_id=question_id,
            evaluate=evaluate,
        )
        if persist and not turn.speculative:
            await self._persist_pending_answer(state, pending)
        return pending

    async def _persist_pending_answer(
        self, state: InterviewState, pending: PendingAnswer, *, db: AsyncSession | None = None
    ) -> None:
        async def _write(session: AsyncSession) -> None:
            turn = pending.turn
            quick_meta: dict[str, Any] = {"special": pending.special.kind.value}
            if pending.quick:
                quick_meta["assessment"] = pending.quick
            await add_answer(
                session,
                state.session_id,
                answer_id=pending.answer_id,
                question_id=pending.question_id,
                text_value=turn.text.strip(),
                typed=turn.typed,
                started_ms=turn.started_ms,
                ended_ms=turn.ended_ms,
                stt_confidence=turn.stt_confidence,
                interrupted_ai=turn.interrupted_ai,
                quick_assessment=quick_meta,
            )
            await add_transcript(
                session,
                state.session_id,
                speaker="candidate",
                text_value=turn.text.strip() or "(no speech)",
                start_ms=turn.started_ms,
                end_ms=turn.ended_ms,
                question_id=pending.question_id,
                answer_id=pending.answer_id,
                confidence=turn.stt_confidence,
            )
            if turn.audio_wav and state.audio_recording_allowed:
                await store_answer_audio(
                    session,
                    organization_id=state.organization_id,
                    session_id=state.session_id,
                    candidate_id=state.candidate_id,
                    answer_id=pending.answer_id,
                    wav=turn.audio_wav,
                    retention_days=state.audio_retention_days,
                )
            if pending.evaluate:
                # Delayed a few seconds so the live turn's stream gets the model
                # first (the evaluation only feeds the next decision anyway).
                await enqueue(
                    "answer.evaluate",
                    {"session_id": state.session_id, "answer_id": str(pending.answer_id)},
                    dedupe_key=f"answer.evaluate:{pending.answer_id}",
                    delay_seconds=(
                        self.config.evaluation_delay_s
                        if turn.typed
                        else self.config.evaluation_delay_spoken_s
                    ),
                    db=session,
                )
            await log_event(
                session,
                state.session_id,
                "answer.received",
                {
                    "answer_id": str(pending.answer_id),
                    "question_id": pending.question_id,
                    "words": len(turn.text.split()),
                    "typed": turn.typed,
                    "interrupted_ai": turn.interrupted_ai,
                    "special": pending.special.kind.value,
                    "quick_quality": (pending.quick or {}).get("quality"),
                    "evaluate": pending.evaluate,
                },
                elapsed_s=turn.ended_ms / 1000.0,
            )

        if db is not None:
            await _write(db)
            return
        async with session_scope() as session:
            await _write(session)
            await self._save(state, db=session)

    async def _record_candidate_utterance(
        self, state: InterviewState, turn: CandidateTurn, text: str
    ) -> None:
        async with session_scope() as db:
            await add_transcript(
                db,
                state.session_id,
                speaker="candidate",
                text_value=text or "(no speech)",
                start_ms=turn.started_ms,
                end_ms=turn.ended_ms,
                question_id=state.current_question_id,
                confidence=turn.stt_confidence,
            )
        from app.services.interview.memory import append_exchange
        from app.services.interview.state import ExchangeTurn

        append_exchange(
            state,
            ExchangeTurn(
                speaker="candidate",
                text=text,
                at_s=turn.ended_ms / 1000.0,
                question_id=state.current_question_id,
            ),
        )

    def _interruption_note(self, state: InterviewState, turn: CandidateTurn) -> str | None:
        notes: list[str] = []
        last = state.utterances[-1] if state.utterances else None
        if turn.interrupted_ai and last is not None and last.interrupted:
            heard = (last.spoken_text or "").strip()
            notes.append(
                "Note: the candidate interrupted you while you were speaking. They heard only: "
                f'"{heard[:300] or "(nothing)"}". Respond to what they said.'
            )
        current = state.current_question()
        if current is not None and (current.interrupted and "?" not in (current.spoken_text or "")):
            notes.append(
                "The candidate has not heard your current question yet; respond to what they said, then ask it."
            )
        return " ".join(notes) or None

    # ======================================================= decision turns
    async def _decision_turn(
        self,
        state: InterviewState,
        directive: PolicyDirective,
        *,
        last_answer: str | None,
        current_question: str | None = None,
        interruption: str | None = None,
        pending_answer: PendingAnswer | None = None,
        speculative: bool = False,
        coding_prompt: str | None = None,
        persist_task: asyncio.Task[Any] | None = None,
        turn_started: float | None = None,
    ) -> AsyncGenerator[TurnEvent, None]:
        """One speech-first decision. UtteranceBegin is yielded before the model
        call; nothing slower than prompt rendering happens before the stream
        starts (the answer row is persisted concurrently by ``persist_task``)."""
        t0 = turn_started or time.monotonic()
        utterance_id = str(uuid.uuid4())
        provisional = beh.provisional(directive.recommended_action)
        state.avatar_state = provisional.state
        yield UtteranceBegin(
            utterance_id=utterance_id, kind=utterance_kind(directive.recommended_action), behavior=provisional
        )
        outcome = TurnOutcome()
        system = system_prompt(state)
        if coding_prompt is not None:
            messages = turn_messages(system, coding_prompt)
            retry = None
        else:
            user = user_prompt(
                state,
                directive,
                last_answer=last_answer,
                current_question=current_question,
                interruption=interruption,
            )
            messages = turn_messages(system, user)
            retry = turn_messages(
                system,
                user_prompt(
                    state,
                    directive,
                    last_answer=last_answer,
                    current_question=current_question,
                    interruption=interruption,
                    trimmed=True,
                ),
            )
        outcome.timings["prepare_ms"] = (time.monotonic() - t0) * 1000.0
        outcome.timings["prompt_chars"] = float(sum(len(str(m.get("content", ""))) for m in messages))
        probe = directive.recommended_action == TurnAction.VERIFY_CONTRADICTION

        def guard_factory(allow_regenerate: bool, released_before: bool) -> Any:
            return guard_for_turn(
                state, probe=probe, allow_regenerate=allow_regenerate, released_before=released_before
            )

        spoken: list[str] = []
        completed = False
        try:
            async with aclosing(
                stream_speech_object(
                    self.ai,
                    messages,
                    schema=TurnDecision,
                    json_schema=TURN_SCHEMA,
                    context=turn_context(state, PROMPT_VERSION),
                    utterance_id=utterance_id,
                    outcome=outcome,
                    config=self.config,
                    retry_messages=retry,
                    guard_factory=guard_factory,
                )
            ) as gen:
                async for delta in gen:
                    if not spoken:
                        outcome.timings["first_delta_from_call_ms"] = (time.monotonic() - t0) * 1000.0
                    spoken.append(delta.text)
                    yield delta
            completed = True
        finally:
            if not completed and not speculative:
                # Barge-in / cancellation mid-utterance: the answer row must exist,
                # and what was spoken is persisted with the fields received so far.
                await self._settle(persist_task, state)
                partial = "".join(spoken).strip()
                if partial:
                    decision = recover_decision(
                        outcome.raw_partial,
                        partial,
                        directive,
                        state,
                        reason="interrupted before the decision completed",
                    )
                    try:
                        await self._commit_decision(
                            state,
                            directive,
                            decision,
                            utterance_id=utterance_id,
                            deviations=["interrupted_before_complete"],
                            outcome=outcome,
                            pending_answer=pending_answer,
                        )
                    except Exception as exc:  # noqa: BLE001 - never mask the cancellation itself
                        ERRORS.labels(component="engine_turn").inc()
                        log.error(
                            "engine.interrupted_commit_failed",
                            session_id=state.session_id,
                            error=f"{type(exc).__name__}: {exc}"[:300],
                        )
        bank: BankCandidate | None = None
        fallback_text: str | None = None
        extra_deviations: list[str] = []
        if outcome.decision is not None:
            decision = outcome.decision
        elif outcome.speech:
            decision = recover_decision(
                outcome.raw_partial,
                outcome.speech,
                directive,
                state,
                reason="decision recovered from a partial object",
            )
            extra_deviations.append("tail_cut" if outcome.tail_cut else "decision_recovered")
        else:
            fallback_text, action, bank = fallback_utterance(
                state, directive, current_question=current_question
            )
            decision = synthesize_decision(
                fallback_text,
                directive,
                state,
                action=action,
                reason="fallback: model unavailable",
                bank=bank,
            )
            outcome.fallback = True
            outcome.speech = fallback_text
            state.fallbacks_used += 1
            log.warning(
                "engine.fallback_question",
                session_id=state.session_id,
                action=action.value,
                bank=bool(bank),
                errors=outcome.errors[:3],
            )
        await self._settle(persist_task, state)
        valid_bank = [b.id for b in directive.candidate_bank_questions] + ([bank.id] if bank else [])
        check = check_decision(directive, decision, state, valid_bank_ids=valid_bank)
        if coding_prompt is not None:
            check.deviations[:] = [d for d in check.deviations if not d.startswith("speech:no_question")]
        # Unsupported attributions: caught while streaming (dropped / regenerated before they were spoken)
        # or, for the rest (mid-sentence references), by the post-check on what was actually said.
        grounding = self._grounding_issue(state, check.decision.speech) or outcome.ungrounded
        if grounding:
            extra_deviations.append("ungrounded_reference")
        if outcome.dropped_attribution and outcome.ungrounded:
            extra_deviations.append("speech:ungrounded_attribution_dropped")
        if outcome.regenerated:
            extra_deviations.append("speech:attribution_regenerated")
        if outcome.rewritten_frame:
            extra_deviations.append("speech:attribution_frame_rewritten")
        if outcome.dropped_opener:
            extra_deviations.append("speech:evaluative_opener_dropped")
        if outcome.dropped_tail:
            extra_deviations.append("speech:second_question_dropped")
        state.ungrounded_reference = grounding  # read by the next turn's directive
        meta = await self._commit_decision(
            state,
            directive,
            check.decision,
            utterance_id=utterance_id,
            deviations=extra_deviations + check.deviations,
            outcome=outcome,
            pending_answer=pending_answer,
        )
        if fallback_text is not None:
            # Non-streamed: the whole fallback text in one delta after the begin.
            yield SpeechDelta(utterance_id=utterance_id, text=fallback_text)
        yield meta

    @staticmethod
    def _grounding_issue(state: InterviewState, speech: str) -> str | None:
        issue = ungrounded_reference(state, speech)
        if issue:
            log.info("engine.ungrounded_reference", session_id=state.session_id)
        return issue

    async def _settle(self, task: asyncio.Task[Any] | None, state: InterviewState) -> None:
        """Wait for the concurrent answer persistence (never lose the answer)."""
        if task is None:
            return
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - logged; the decision still commits
            ERRORS.labels(component="engine_persist").inc()
            log.error(
                "engine.answer_persist_failed",
                session_id=state.session_id,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

    async def _emit_known_decision(
        self,
        state: InterviewState,
        directive: PolicyDirective,
        decision: TurnDecision,
        *,
        deviations: list[str],
        source: str,
    ) -> AsyncGenerator[TurnEvent, None]:
        """Emit a pre-generated decision (first question prepared in session.prepare)."""
        utterance_id = str(uuid.uuid4())
        check = check_decision(
            directive,
            decision,
            state,
            valid_bank_ids=[decision.bank_question_id] if decision.bank_question_id else [],
        )
        outcome = TurnOutcome(decision=check.decision, speech=check.decision.speech)
        spec = beh.provisional(check.decision.action)
        meta = await self._commit_decision(
            state,
            directive,
            check.decision,
            utterance_id=utterance_id,
            deviations=deviations + check.deviations,
            outcome=outcome,
            source=source,
        )
        yield UtteranceBegin(
            utterance_id=utterance_id,
            kind=utterance_kind(check.decision.action),
            behavior=spec,
            question_id=meta.question_id,
            text=check.decision.speech,  # type: ignore[arg-type]
        )
        yield meta

    async def _commit_decision(
        self,
        state: InterviewState,
        directive: PolicyDirective,
        decision: TurnDecision,
        *,
        utterance_id: str,
        deviations: list[str],
        outcome: TurnOutcome,
        pending_answer: PendingAnswer | None = None,
        source: str = "live",
    ) -> UtteranceMeta:
        elapsed = self._elapsed(state)
        action = decision.action
        created = action not in NON_QUESTION_ACTIONS
        question_id = str(uuid.uuid4()) if created else state.current_question_id
        kind = question_kind(action, directive.time.segment_kind, decision.competency_key)
        parent = (
            state.current_question_id
            if action in FOLLOWUP_ACTIONS or action == TurnAction.INCREASE_DIFFICULTY
            else None
        )
        bank_id = decision.bank_question_id
        source_label = "curated" if bank_id else ("followup" if action in FOLLOWUP_ACTIONS else "generated")
        final_behavior = beh.from_decision(decision)
        decision_record = {
            **decision.model_dump(mode="json"),
            "deviations": deviations,
            "synthesized": outcome.synthesized or outcome.decision is None,
            "fallback": outcome.fallback,
            "source": source,
            "latency": outcome.latency(),
            "errors": outcome.errors[:5],
            "directive": directive.summary(),
        }
        coding_question_id = None
        async with session_scope() as db:
            if pending_answer is not None:
                await self._persist_pending_answer(state, pending_answer, db=db)
            if created and question_id:
                await add_question(
                    db,
                    state.session_id,
                    question_id=uuid.UUID(question_id),
                    kind=kind.value,
                    action=action.value,
                    source=source_label,
                    text_value=decision.speech,
                    segment=directive.time.segment_kind,
                    topic=decision.topic,
                    competency_keys=[decision.competency_key] if decision.competency_key else [],
                    difficulty=decision.difficulty.value,
                    question_focus=decision.question_focus,
                    reason=decision.reason,
                    expected_evidence=list(decision.expected_evidence),
                    claim_id=decision.claim_id,
                    decision=decision_record,
                    elapsed_s=elapsed,
                    parent_question_id=parent,
                    bank_question_id=bank_id,
                    coding_question_id=coding_question_id,
                )
            transcript = await add_transcript(
                db,
                state.session_id,
                speaker="interviewer",
                text_value=decision.speech,
                start_ms=int(elapsed * 1000),
                end_ms=int(elapsed * 1000),
                question_id=question_id,
            )
            apply_decision(
                state,
                decision,
                directive,
                utterance_id=utterance_id,
                question_id=question_id,
                created_question=created,
                elapsed_s=elapsed,
                kind=utterance_kind(action),
                bank_question_id=bank_id,
                parent_question_id=parent,
                transcript_id=str(transcript.id),
            )
            state.avatar_state = final_behavior.state
            state.turn_state = TurnState.AI_SPEAKING
            if deviations:
                state.deviations += len([d for d in deviations if not d.startswith("speech:")])
            await log_event(
                db,
                state.session_id,
                "turn.decision",
                {
                    "utterance_id": utterance_id,
                    "question_id": question_id if created else None,
                    "action": action.value,
                    "competency": decision.competency_key,
                    "difficulty": decision.difficulty.value,
                    "answer_quality": decision.answer_quality.value if decision.answer_quality else None,
                    "deviations": deviations,
                    "fallback": outcome.fallback,
                    "synthesized": decision_record["synthesized"],
                    "latency": outcome.latency(),
                    "directive": directive.summary(),
                },
                elapsed_s=elapsed,
            )
            await self._save(state, durable=True, db=db)
        if deviations:
            log.info("engine.decision_deviations", session_id=state.session_id, deviations=deviations[:6])
        if created and question_id:
            self._spawn(
                self._after_question(state.session_id, question_id, decision.speech, decision.question_focus),
                name="engine.after_question",
            )
        self._spawn(self._prefetch(state.model_copy(deep=True)), name="engine.prefetch")
        if state.intake is None and needs_summary(state, self.config):
            self._spawn(self._enqueue_memory(state.session_id, len(state.answers)), name="engine.memory")
        return UtteranceMeta(
            utterance_id=utterance_id,
            question_id=question_id,
            behavior=final_behavior,
            decision=decision_record,
            full_text=decision.speech,
        )

    async def _after_question(self, session_id: str, question_id: str, text: str, focus: str | None) -> None:
        """Store the new question's embedding and log near-duplicates of
        questions already asked (cosine >= threshold)."""
        asked = self._asked_vectors.get(session_id)
        async with session_scope() as db:
            if asked is None:
                asked = await load_asked_vectors(db, session_id)
                self._asked_vectors[session_id] = asked
            vector = await embed_question(db, question_id, text, focus=focus, ai=self.ai)
            if vector is None:
                return
            others = [a for a in asked if a.question_id != question_id]
            if others:
                best = max(others, key=lambda a: cosine_similarity(vector, a.vector))
                similarity = cosine_similarity(vector, best.vector)
                if similarity >= self.config.near_duplicate_threshold:
                    await log_event(
                        db,
                        session_id,
                        "question.near_duplicate",
                        {
                            "question_id": question_id,
                            "similar_to": best.question_id,
                            "similarity": round(similarity, 3),
                        },
                    )
        self._asked_vectors.setdefault(session_id, []).append(AskedVector(question_id, vector))

    async def _enqueue_memory(self, session_id: str, answers: int) -> None:
        await enqueue(
            "memory.summarize",
            {"session_id": session_id},
            dedupe_key=f"memory.summarize:{session_id}:{answers // 4}",
        )

    # ============================================================ bank cache
    async def _cached_bank(self, state: InterviewState, directive: PolicyDirective) -> list[BankCandidate]:
        """Bank candidates for the turn path: cache only - this process's memory,
        then the shared Redis copy written by speculative preparation, the
        post-decision prefetch and session.prepare (a sub-millisecond read, no
        model calls). A miss schedules a prefetch and the turn proceeds without."""
        key = directive.target_competency
        if key is None or directive.recommended_action in (
            TurnAction.CLOSE_INTERVIEW,
            TurnAction.ACKNOWLEDGE_AND_WAIT,
            TurnAction.CLARIFY_QUESTION,
            TurnAction.ANSWER_CANDIDATE_QUESTION,
            TurnAction.INVITE_CANDIDATE_QUESTIONS,
        ):
            return []
        difficulty = directive.target_difficulty.value
        cached = self._bank_cache_get(state.session_id, key, difficulty)
        if cached is None:
            cached = await self._bank_cache_shared_get(state.session_id, key, difficulty)
        if cached is None:
            self._spawn(
                self._prefetch_one(state.model_copy(deep=True), key, difficulty),
                name="engine.prefetch_one",
            )
            return []
        asked_bank = {q.bank_question_id for q in state.questions if q.bank_question_id}
        return [c for c in cached if c.id not in asked_bank][: self.config.bank_candidates]

    @staticmethod
    def _bank_key(session_id: str) -> str:
        return f"session:{session_id}:bank"

    async def _bank_cache_shared_get(
        self, session_id: str, key: str, difficulty: str
    ) -> list[BankCandidate] | None:
        try:
            raw = await self.redis.hget(self._bank_key(session_id), f"{key}:{difficulty}")
        except Exception:  # noqa: BLE001 - cache only
            return None
        if not raw:
            return None
        try:
            found = [
                BankCandidate(**{**item, "difficulty": Difficulty(item["difficulty"])})
                for item in json.loads(raw)
            ]
        except (TypeError, ValueError, KeyError):
            return None
        self._bank_cache[(session_id, key, difficulty)] = (time.monotonic() + self.config.prep_ttl_s, found)
        return found

    async def _bank_cache_put(
        self, session_id: str, key: str, difficulty: str, found: list[BankCandidate]
    ) -> None:
        self._bank_cache[(session_id, key, difficulty)] = (time.monotonic() + self.config.prep_ttl_s, found)
        payload = json.dumps([{**asdict(c), "difficulty": c.difficulty.value} for c in found])
        with contextlib.suppress(Exception):  # cache only
            await self.redis.hset(self._bank_key(session_id), f"{key}:{difficulty}", payload)
            await self.redis.expire(self._bank_key(session_id), self.config.prep_ttl_s)

    def _cancel_speculation(self, session_id: str) -> None:
        task = self._prep_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _bank_cache_get(self, session_id: str, key: str, difficulty: str) -> list[BankCandidate] | None:
        entry = self._bank_cache.get((session_id, key, difficulty))
        if entry is None or entry[0] < time.monotonic():
            return None
        return entry[1]

    async def _fetch_bank(
        self, state: InterviewState, directive: PolicyDirective, *, bounded: bool = True
    ) -> list[BankCandidate]:
        key = directive.target_competency
        if key is None or directive.recommended_action in (
            TurnAction.CLOSE_INTERVIEW,
            TurnAction.ACKNOWLEDGE_AND_WAIT,
            TurnAction.CLARIFY_QUESTION,
        ):
            return []
        asked_bank = {q.bank_question_id for q in state.questions if q.bank_question_id}
        cached = self._bank_cache_get(state.session_id, key, directive.target_difficulty.value)
        if cached is not None:
            return [c for c in cached if c.id not in asked_bank][: self.config.bank_candidates]
        try:
            if bounded:
                async with asyncio.timeout(self.config.turn_retrieval_timeout_s):
                    found = await self._retrieve_bank(state, key, directive)
            else:
                found = await self._retrieve_bank(state, key, directive)
        except TimeoutError:
            log.info("engine.bank_retrieval_timeout", session_id=state.session_id, competency=key)
            self._spawn(
                self._prefetch_one(state.model_copy(deep=True), key, directive.target_difficulty.value),
                name="engine.prefetch_one",
            )
            return []
        except Exception as exc:  # noqa: BLE001 - retrieval never blocks a turn
            ERRORS.labels(component="engine_selection").inc()
            log.warning(
                "engine.bank_retrieval_failed",
                session_id=state.session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return []
        return [c for c in found if c.id not in asked_bank][: self.config.bank_candidates]

    async def _retrieve_bank(
        self, state: InterviewState, key: str, directive: PolicyDirective
    ) -> list[BankCandidate]:
        target = directive.target()
        topics = target.uncovered_topics if target else []
        name = target.name if target else key
        return await self._retrieve_bank_raw(state, key, directive.target_difficulty.value, name, topics)

    async def _retrieve_bank_raw(
        self, state: InterviewState, key: str, difficulty: str, name: str, topics: list[str]
    ) -> list[BankCandidate]:
        from app.domain.enums import Difficulty

        asked = self._asked_vectors.get(state.session_id)
        async with session_scope() as db:
            if asked is None:
                asked = await load_asked_vectors(db, state.session_id)
                self._asked_vectors[state.session_id] = asked
            found = await bank_candidates(
                db,
                organization_id=state.organization_id,
                competency_key=key,
                competency_name=name,
                difficulty=Difficulty(difficulty),
                topics=topics,
                job_title=(state.job_summary or {}).get("title"),
                round_type=state.round_config.round_type.value,
                role_tags=job_role_tags(state.job_summary),
                exclude_ids=[q.bank_question_id for q in state.questions if q.bank_question_id],
                asked_vectors=[a.vector for a in asked],
                limit=self.config.bank_candidates + 2,
                threshold=self.config.near_duplicate_threshold,
                session_id=state.session_id,
                ai=self.ai,
            )
        await self._bank_cache_put(state.session_id, key, difficulty, found)
        return found

    async def _prefetch_one(self, state: InterviewState, key: str, difficulty: str) -> None:
        comp = state.competencies.get(key)
        name = (comp.name if comp and comp.name else None) or key.replace("_", " ")
        from app.services.interview.policy import uncovered_topics

        await self._retrieve_bank_raw(state, key, difficulty, name, uncovered_topics(state, key))

    async def _prefetch(self, state: InterviewState) -> None:
        """Warm the bank cache for the likely next targets (current topic + top target)."""
        directive = compute_directive(state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s)
        wanted: list[tuple[str, str]] = []
        for target in directive.targets[:2]:
            wanted.append((target.key, target.target_difficulty.value))
        if state.current_competency:
            comp = state.competencies.get(state.current_competency)
            if comp is not None:
                wanted.append((state.current_competency, comp.current_difficulty.value))
        for key, difficulty in dict.fromkeys(wanted):
            if self._bank_cache_get(state.session_id, key, difficulty) is None:
                with contextlib.suppress(Exception):
                    await self._prefetch_one(state, key, difficulty)

    # ======================================================== speculative
    async def on_partial_transcript(self, session_id: str, text: str, *, silence_started: bool) -> None:
        self._partial[session_id] = text
        if not silence_started:
            return
        normalized = normalize_text(text)
        n = len(normalized.split())
        if n < self.config.prep_min_words:
            return
        previous = self._prep.get(session_id)
        if previous is not None and previous.text and normalized.startswith(previous.text):
            if n - len(previous.text.split()) < self.config.prep_min_new_words:
                return
        task = self._prep_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._prep_tasks[session_id] = self._spawn(self._speculate(session_id, text), name="engine.speculate")

    async def _speculate(self, session_id: str, text: str) -> None:
        state = await self.store.load(session_id)
        if state is None or state.status in ENDED_STATUSES or state.coding.active or state.intake is not None:
            return
        special = classify(text, awaiting_answer=True, questions_invited=state.candidate_questions_invited)
        quick = None
        if not special.is_special and self.config.quick_assessment_enabled:
            quick = await quick_assess(self.ai, state, text, timeout_s=self.config.quick_assessment_timeout_s)
        entry = PrepEntry(
            text=normalize_text(text), quick=quick, created=time.time(), question_id=state.current_question_id
        )
        self._prep[session_id] = entry
        await store_prep(self.redis, session_id, entry, self.config.prep_ttl_s)
        # Retrieval for the move this answer most likely leads to (never commits state).
        directive = compute_directive(
            state,
            quick=quick,
            special=special,
            now=self.now(),
            closing_reserve_s=self.config.closing_reserve_s,
        )
        if directive.target_competency and not directive.must_close and not directive.start_coding:
            if (
                self._bank_cache_get(
                    session_id, directive.target_competency, directive.target_difficulty.value
                )
                is None
            ):
                with contextlib.suppress(Exception):
                    await self._retrieve_bank(state, directive.target_competency, directive)

    async def wait_speculative(self, session_id: str, timeout: float = 10.0) -> None:  # noqa: ASYNC109
        task = self._prep_tasks.get(session_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout)

    def _take_prep(self, session_id: str, text: str, question_id: str | None) -> PrepEntry | None:
        entry = self._prep.pop(session_id, None)
        if entry is not None and entry.matches(text, question_id):
            return entry
        return None

    async def _take_prep_async(self, session_id: str, text: str, question_id: str | None) -> PrepEntry | None:
        entry = self._take_prep(session_id, text, question_id)
        if entry is not None:
            return entry
        remote = await load_prep(self.redis, session_id)
        if remote is not None and remote.matches(text, question_id):
            await clear_prep(self.redis, session_id)
            return remote
        return None

    # ================================================================ repeat
    def repeat_question(self, session_id: str) -> EventStream:
        return EventStream(self._repeat(session_id))

    async def _repeat(self, session_id: str) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            async with aclosing(self._repeat_events(state)) as _relay:
                async for event in _relay:
                    yield event

    async def _repeat_events(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        question = state.current_question()
        if question is not None:
            core = question_core(question.text)
            question_id: str | None = question.id
        elif state.utterances:
            core = state.utterances[-1].text
            question_id = state.utterances[-1].question_id
        else:
            return
        lead = pick(REPEAT_LEADS, f"{state.session_id}:{state.turn_index}:{len(state.utterances)}")
        text = f"{lead} {core}"
        utterance_id = str(uuid.uuid4())
        spec = beh.provisional(TurnAction.CLARIFY_QUESTION)
        elapsed = self._elapsed(state)
        async with session_scope() as db:
            row = await add_transcript(
                db,
                state.session_id,
                speaker="interviewer",
                text_value=text,
                start_ms=int(elapsed * 1000),
                end_ms=int(elapsed * 1000),
                question_id=question_id,
            )
            add_utterance(
                state,
                UtteranceRecord(
                    id=utterance_id,
                    kind="question",
                    action=TurnAction.CLARIFY_QUESTION,
                    question_id=question_id,
                    text=text,
                    at_s=elapsed,
                    transcript_id=str(row.id),
                ),
            )
            record_interviewer_text(state, text=text, at_s=elapsed, question_id=question_id)
            await log_event(
                db, state.session_id, "question.repeated", {"question_id": question_id}, elapsed_s=elapsed
            )
            await self._save(state, db=db)
        yield UtteranceBegin(
            utterance_id=utterance_id, kind="question", behavior=spec, question_id=question_id, text=text
        )
        yield UtteranceMeta(
            utterance_id=utterance_id,
            question_id=question_id,
            behavior=spec,
            decision={"action": "repeat_question"},
            full_text=text,
        )

    # ========================================================= interruptions
    async def handle_interruption(self, session_id: str, interrupted: InterruptedUtterance) -> None:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            record = state.utterance_by_id(interrupted.utterance_id)
            spoken = (interrupted.spoken_text or "").strip()
            transcript_id = record.transcript_id if record else None
            if record is not None:
                record.interrupted = True
                record.spoken_text = spoken
            question_id = interrupted.question_id or (record.question_id if record else None)
            q = state.question_by_id(question_id)
            if q is not None:
                q.interrupted = True
                q.spoken_text = spoken
            for entry in reversed(state.conversation):
                if entry.speaker == "interviewer" and (
                    entry.text == interrupted.full_text or (record and entry.text == record.text)
                ):
                    entry.text = spoken or "(interrupted before speaking)"
                    entry.interrupted = True
                    break
            state.awaiting_answer = "?" in spoken
            elapsed = self._elapsed(state)
            async with session_scope() as db:
                if q is not None:
                    await mark_question_interrupted(db, q.id, spoken_text=spoken, transcript_id=transcript_id)
                elif transcript_id:
                    await mark_interviewer_transcript_interrupted(db, transcript_id, spoken_text=spoken)
                await log_event(
                    db,
                    session_id,
                    "utterance.interrupted",
                    {
                        "utterance_id": interrupted.utterance_id,
                        "question_id": question_id,
                        "played_ms": interrupted.played_ms,
                        "spoken_chars": len(spoken),
                    },
                    elapsed_s=elapsed,
                )
                await self._save(state, db=db)

    def handle_backchannel_resume(self, session_id: str, interrupted: InterruptedUtterance) -> EventStream:
        return EventStream(self._backchannel_resume(session_id, interrupted))

    async def _backchannel_resume(
        self, session_id: str, interrupted: InterruptedUtterance
    ) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            record = state.utterance_by_id(interrupted.utterance_id)
            full = interrupted.full_text or (record.text if record else "")
            remaining = remaining_text(full, interrupted.spoken_text)
            truncated = bool(remaining) and remaining.rstrip()[-1:] not in ".?!"
            if truncated:
                # Generation was cut off mid-sentence (barge-in in the first moments):
                # keep complete sentences only, or regenerate the move.
                cut = max(remaining.rfind(". "), remaining.rfind("? "), remaining.rfind("! "))
                remaining = remaining[: cut + 1].strip() if cut > 0 else ""
            question_id = interrupted.question_id or (record.question_id if record else None)
            q = state.question_by_id(question_id)
            elapsed = self._elapsed(state)
            if record is not None:
                record.interrupted = False
                record.spoken_text = full
            if q is not None:
                q.interrupted = False
                q.spoken_text = full
            for entry in reversed(state.conversation):
                if entry.speaker == "interviewer" and entry.interrupted:
                    entry.text = full
                    entry.interrupted = False
                    break
            async with session_scope() as db:
                if q is not None:
                    await db.execute(
                        update(Question)
                        .where(Question.id == uuid.UUID(q.id))
                        .values(interrupted=False, spoken_text=full)
                    )
                if record is not None and record.transcript_id:
                    from app.db.models import Transcript

                    await db.execute(
                        update(Transcript)
                        .where(Transcript.id == uuid.UUID(record.transcript_id))
                        .values(interrupted=False, text=full)
                    )
                await log_event(
                    db,
                    session_id,
                    "utterance.backchannel_resume",
                    {"utterance_id": interrupted.utterance_id, "remaining_chars": len(remaining)},
                    elapsed_s=elapsed,
                )
                if not remaining and not truncated:
                    state.awaiting_answer = bool(q)
                    await self._save(state, db=db)
                    return
                if not remaining:
                    await self._save(state, db=db)
                utterance_id = str(uuid.uuid4())
                kind = record.kind if record else "question"
                if remaining:
                    add_utterance(
                        state,
                        UtteranceRecord(
                            id=utterance_id,
                            kind=kind,
                            action=record.action if record else None,
                            question_id=question_id,
                            text=remaining,
                            at_s=elapsed,
                        ),
                    )
                    await self._save(state, db=db)
            if not remaining and state.intake is not None:
                async with aclosing(self.intake.regenerate_events(state)) as _relay:
                    async for event in _relay:
                        yield event
                return
            if not remaining:
                last_answer = next((a.text for a in reversed(state.answers)), None)
                directive = compute_directive(
                    state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s
                )
                directive.candidate_bank_questions = await self._cached_bank(state, directive)
                note = "You were cut off by a brief acknowledgement from the candidate before finishing; say your move again in full."
                async with aclosing(
                    self._decision_turn(state, directive, last_answer=last_answer, interruption=note)
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
            spec = beh.for_kind(kind)
            yield UtteranceBegin(
                utterance_id=utterance_id, kind=kind, behavior=spec, question_id=question_id, text=remaining
            )  # type: ignore[arg-type]
            yield UtteranceMeta(
                utterance_id=utterance_id,
                question_id=question_id,
                behavior=spec,
                decision={"action": "resume_after_backchannel", "resumes": interrupted.utterance_id},
                full_text=remaining,
            )
            if state.intake is not None:
                rearmed = self.intake.rearm_after(state, utterance_id, interrupted.utterance_id)
                if rearmed is not None:
                    await self._save(state)
                    yield rearmed

    # ============================================================== delivery
    async def mark_utterance_delivered(self, session_id: str, utterance_id: str, *, spoken_text: str) -> None:
        lock = self._locks.get(session_id)
        if lock is not None and lock.locked():
            self._pending_delivery.setdefault(session_id, []).append((utterance_id, spoken_text))
            return
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            record = mark_delivered(state, utterance_id, spoken_text, elapsed_s=self._elapsed(state))
            if record is None:
                return
            if state.status == SessionStatus.LIVE and not state.coding.active:
                state.turn_state = TurnState.LISTENING
                state.avatar_state = AvatarState.LISTENING
            await self._save(state)

    # ================================================================= timers
    def handle_timer(self, session_id: str, timer: TimerEvent) -> EventStream:
        return EventStream(self._timer(session_id, timer))

    async def _timer(self, session_id: str, timer: TimerEvent) -> AsyncGenerator[TurnEvent, None]:
        if timer.kind == "resume_after_disconnect":
            async with aclosing(self._resume(session_id, reconnect=True)) as _relay:
                async for event in _relay:
                    yield event
            return
        if timer.kind == "disconnect":
            await self.pause(session_id, reason=str(timer.detail.get("reason") or "disconnect"))
            yield StateChanged(reason="paused")
            return
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            elapsed = self._elapsed(state)
            if timer.kind == "max_time" or (
                timer.kind in ("tick", "time_up") and elapsed >= state.max_time_s
            ):
                async with aclosing(
                    self._closing_events(
                        state, SessionEndReason.TIME_LIMIT, last_answer=None, generated=False
                    )
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
            if timer.kind == "time_up" and state.intake is not None:
                async with aclosing(self.intake.time_up_events(state)) as _relay:
                    async for event in _relay:
                        yield event
                return
            if timer.kind == "time_up":
                async with aclosing(
                    self._closing_events(state, SessionEndReason.TIME_LIMIT, last_answer=None)
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
            if timer.kind == "time_warning":
                remaining = int(timer.detail.get("remaining_s") or round(state.remaining_seconds(self.now())))
                threshold = 60 if remaining <= 90 else 300
                if threshold not in state.time_warnings:
                    state.pending_time_notice = (
                        f"about {'one minute' if threshold == 60 else 'five minutes'} left"
                    )
                await self._save(state)
                yield StateChanged(reason="time_warning")
                return
            if timer.kind == "silence_timeout" and self.intake_activity_active(state):
                return  # intake activity armed/running: the runtime owns its timing (7.2)
            if timer.kind == "silence_timeout":
                async with aclosing(self._silence_events(state)) as _relay:
                    async for event in _relay:
                        yield event
                return
            if state.coding.active:
                # The engine owns the coding clock: time-up, then observer / time warnings.
                if self._coding_remaining(state) <= 0:
                    async with aclosing(self._finish_coding_events(state, "time_limit")) as _relay:
                        async for event in _relay:
                            yield event
                    return
                async with aclosing(self._coding_observe_events(state, "tick")) as _relay:
                    async for event in _relay:
                        yield event
                return
            # tick: nothing to say; keep the debounced snapshot fresh
            await self._save(state)

    async def _silence_events(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        if state.coding.active or not state.awaiting_answer:
            return
        state.silence_prompts += 1
        elapsed = self._elapsed(state)
        if state.silence_prompts <= self.config.silence_prompts_before_move_on:
            text = SILENCE_PROMPTS[min(state.silence_prompts, len(SILENCE_PROMPTS)) - 1]
            utterance_id = str(uuid.uuid4())
            spec = beh.provisional(TurnAction.ACKNOWLEDGE_AND_WAIT)
            async with session_scope() as db:
                row = await add_transcript(
                    db,
                    state.session_id,
                    speaker="interviewer",
                    text_value=text,
                    start_ms=int(elapsed * 1000),
                    end_ms=int(elapsed * 1000),
                    question_id=state.current_question_id,
                )
                add_utterance(
                    state,
                    UtteranceRecord(
                        id=utterance_id,
                        kind="acknowledgment",
                        action=TurnAction.ACKNOWLEDGE_AND_WAIT,
                        question_id=state.current_question_id,
                        text=text,
                        at_s=elapsed,
                        transcript_id=str(row.id),
                    ),
                )
                record_interviewer_text(state, text=text, at_s=elapsed, question_id=state.current_question_id)
                await log_event(
                    db,
                    state.session_id,
                    "silence.prompt",
                    {"count": state.silence_prompts},
                    elapsed_s=elapsed,
                )
                await self._save(state, db=db)
            yield UtteranceBegin(
                utterance_id=utterance_id,
                kind="acknowledgment",
                behavior=spec,
                question_id=state.current_question_id,
                text=text,
            )
            yield UtteranceMeta(
                utterance_id=utterance_id,
                question_id=state.current_question_id,
                behavior=spec,
                decision={
                    "action": TurnAction.ACKNOWLEDGE_AND_WAIT.value,
                    "silence_prompt": state.silence_prompts,
                },
                full_text=text,
            )
            return
        # Move on: treat the silence as "no answer" after the gentle prompts.
        if state.intake is not None:
            async with aclosing(self.intake.silence_move_on(state)) as _relay:
                async for event in _relay:
                    yield event
            return
        state.simplify_attempts = max(state.simplify_attempts, 2)
        special = SpecialResult(SpecialKind.DONT_KNOW, 1.0, "silence")
        directive = compute_directive(
            state, special=special, now=self.now(), closing_reserve_s=self.config.closing_reserve_s
        )
        if directive.must_close:
            async with aclosing(
                self._closing_events(
                    state, directive.close_reason or SessionEndReason.TIME_LIMIT, last_answer=None
                )
            ) as _relay:
                async for event in _relay:
                    yield event
            return
        if directive.start_coding:
            started = False
            async with aclosing(self._start_coding_events(state, ack=False)) as _relay:
                async for event in _relay:
                    started = True
                    yield event
            if started:
                return
        directive.instructions.append(
            "The candidate did not answer after two gentle prompts. Move on kindly to a different topic."
        )
        directive.candidate_bank_questions = await self._cached_bank(state, directive)
        current = state.current_question()
        async with aclosing(
            self._decision_turn(
                state,
                directive,
                last_answer="(no response)",
                current_question=current.text if current else None,
            )
        ) as _relay:
            async for event in _relay:
                yield event

    # ============================================================ pause/resume
    async def pause(self, session_id: str, *, reason: str = "disconnect") -> None:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status != SessionStatus.LIVE:
                return
            now = self.now()
            state.pause(now)
            state.status = SessionStatus.PAUSED
            state.turn_state = TurnState.PAUSED
            last = state.utterances[-1] if state.utterances else None
            if last is not None and not last.delivered:
                last.interrupted = True  # will be re-spoken on resume
            async with session_scope() as db:
                if state.intake is not None:
                    await self.intake.on_pause(state, db)
                await log_event(
                    db, session_id, "session.paused", {"reason": reason}, elapsed_s=state.elapsed_seconds(now)
                )
                await self._save(state, durable=True, db=db)

    def resume(self, session_id: str, *, reconnect: bool) -> EventStream:
        return EventStream(self._resume(session_id, reconnect=reconnect))

    async def _resume(self, session_id: str, *, reconnect: bool) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            if (
                state.status in (SessionStatus.CREATED, SessionStatus.PREPARING, SessionStatus.READY)
                or not state.utterances
            ):
                async with aclosing(self._start_events(state)) as _relay:
                    async for event in _relay:
                        yield event
                return
            async with aclosing(self._resume_events(state, reconnect=reconnect)) as _relay:
                async for event in _relay:
                    yield event

    async def _resume_events(
        self, state: InterviewState, *, reconnect: bool
    ) -> AsyncGenerator[TurnEvent, None]:
        now = self.now()
        if state.status == SessionStatus.PAUSED:
            state.status = SessionStatus.LIVE
        state.resume(now)
        if reconnect:
            state.reconnects += 1
        elapsed = state.elapsed_seconds(now)
        mid_answer = bool(self._partial.get(state.session_id))
        if state.intake is not None:
            async with session_scope() as db:
                await log_event(
                    db,
                    state.session_id,
                    "session.resumed",
                    {"reconnect": reconnect, "mid_answer": mid_answer, "intake": True},
                    elapsed_s=elapsed,
                )
                await self._save(
                    state, durable=True, db=db, extra_columns={"reconnect_count": state.reconnects} if reconnect else None
                )
            async with aclosing(
                self.intake.resume_events(state, reconnect=reconnect, mid_answer=mid_answer)
            ) as _relay:
                async for event in _relay:
                    yield event
            return
        text: str | None = None
        question_id: str | None = None
        regenerate = False
        if mid_answer:
            text = None
        elif state.coding.active:
            text = (
                "Welcome back. Your code is right where you left it, so feel free to pick up where you stopped."
                if reconnect
                else None
            )
        else:
            current = state.current_question()
            last_entry = state.conversation[-1] if state.conversation else None
            if last_entry is not None and last_entry.speaker == "candidate":
                regenerate = True  # our reply never made it out; answer it now
            elif current is not None and not current.answered:
                lead = "Sorry, we got disconnected for a moment. " if reconnect else ""
                text = f"{lead}As I was asking: {question_core(current.text)}"
                question_id = current.id
            elif state.utterances:
                last = state.utterances[-1]
                lead = "Sorry, we got disconnected for a moment. " if reconnect else ""
                text = f"{lead}{last.text}"
                question_id = last.question_id
        async with session_scope() as db:
            await log_event(
                db,
                state.session_id,
                "session.resumed",
                {"reconnect": reconnect, "mid_answer": mid_answer, "regenerate": regenerate},
                elapsed_s=elapsed,
            )
            if text:
                row = await add_transcript(
                    db,
                    state.session_id,
                    speaker="interviewer",
                    text_value=text,
                    start_ms=int(elapsed * 1000),
                    end_ms=int(elapsed * 1000),
                    question_id=question_id,
                )
                utterance_id = str(uuid.uuid4())
                add_utterance(
                    state,
                    UtteranceRecord(
                        id=utterance_id,
                        kind="resume",
                        question_id=question_id,
                        text=text,
                        at_s=elapsed,
                        transcript_id=str(row.id),
                    ),
                )
                record_interviewer_text(state, text=text, at_s=elapsed, question_id=question_id)
            extra: dict[str, Any] = {}
            if reconnect:
                extra["reconnect_count"] = state.reconnects
            await self._save(state, durable=True, db=db, extra_columns=extra or None)
        if state.coding.active:
            yield StateChanged(reason="resume")
        if text:
            spec = beh.for_kind("resume")
            yield UtteranceBegin(
                utterance_id=utterance_id, kind="resume", behavior=spec, question_id=question_id, text=text
            )
            yield UtteranceMeta(
                utterance_id=utterance_id,
                question_id=question_id,
                behavior=spec,
                decision={"action": "resume", "reconnect": reconnect},
                full_text=text,
            )
        elif regenerate:
            last_answer = state.conversation[-1].text
            directive = compute_directive(
                state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s
            )
            if directive.must_close:
                async with aclosing(
                    self._closing_events(
                        state, directive.close_reason or SessionEndReason.TIME_LIMIT, last_answer=last_answer
                    )
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
            directive.candidate_bank_questions = await self._cached_bank(state, directive)
            async with aclosing(self._decision_turn(state, directive, last_answer=last_answer)) as _relay:
                async for event in _relay:
                    yield event

    # ================================================================ closing
    def end_session(self, session_id: str, reason: SessionEndReason) -> EventStream:
        return EventStream(self._end_session(session_id, reason))

    async def _end_session(
        self, session_id: str, reason: SessionEndReason
    ) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES:
                return
            reason = SessionEndReason(reason)
            async with aclosing(
                self._closing_events(
                    state,
                    reason,
                    last_answer=None,
                    generated=False,
                    spoken=reason in SPOKEN_REASONS and state.status == SessionStatus.LIVE,
                )
            ) as _relay:
                async for event in _relay:
                    yield event

    async def _closing_text(
        self, state: InterviewState, reason: SessionEndReason, last_answer: str | None, generated: bool
    ) -> tuple[str, str]:
        if state.intake is not None:
            return self.intake.closing_text(state, reason), "template"
        if generated and reason in GENERATED_REASONS:
            try:
                result = await self.ai.llm.structured(
                    SpokenText,
                    [{"role": "user", "content": closing_prompt(state, reason, last_answer)}],
                    context=CallContext(
                        task=ModelTask.TURN_DECISION,
                        session_id=state.session_id,
                        organization_id=state.organization_id,
                        prompt_version="closing",
                    ),
                    temperature=0.5,
                    max_tokens=200,
                    timeout=6.0,
                    max_attempts=1,
                )
                text = " ".join(result.value.speech.split())
                if acceptable_closing(text, report_allowed=report_note(state) is not None):
                    return text, "model"
            except AIError as exc:
                log.info("engine.closing_template", session_id=state.session_id, reason=type(exc).__name__)
        return template_closing(state, reason), "template"

    async def _closing_events(
        self,
        state: InterviewState,
        reason: SessionEndReason,
        *,
        last_answer: str | None,
        generated: bool = True,
        spoken: bool = True,
    ) -> AsyncGenerator[TurnEvent, None]:
        if state.coding.active and state.coding.coding_session_id:
            cs_id = state.coding.coding_session_id
            await self.coding.finish(cs_id, reason="session_ended")
            state.coding.active = False
            state.coding.ended_reason = "session_ended"
            state.coding.problems_done += 1
            state.coding.completed = True
            yield CodingEnded(coding_session_id=cs_id, reason="session_ended")
        if state.intake is not None:
            for ended in await self.intake.before_closing(state):
                yield ended
        now = self.now()
        elapsed = state.elapsed_seconds(now)
        text: str | None = None
        text_source = "none"
        if spoken:
            text, text_source = await self._closing_text(state, reason, last_answer, generated)
        state.closing_started = True
        state.end_reason = reason
        state.pause(now)
        aborted = reason == SessionEndReason.CANCELLED
        state.status = SessionStatus.ABORTED if aborted else SessionStatus.COMPLETING
        state.turn_state = TurnState.ENDED
        state.awaiting_answer = False
        utterance_id = str(uuid.uuid4())
        spec = beh.provisional(TurnAction.CLOSE_INTERVIEW)
        async with session_scope() as db:
            if text:
                row = await add_transcript(
                    db,
                    state.session_id,
                    speaker="interviewer",
                    text_value=text,
                    start_ms=int(elapsed * 1000),
                    end_ms=int(elapsed * 1000),
                )
                add_utterance(
                    state,
                    UtteranceRecord(
                        id=utterance_id,
                        kind="closing",
                        action=TurnAction.CLOSE_INTERVIEW,
                        text=text,
                        at_s=elapsed,
                        transcript_id=str(row.id),
                    ),
                )
                record_interviewer_text(state, text=text, at_s=elapsed, question_id=None)
                state.closing_utterance_id = utterance_id
                state.avatar_state = spec.state
            await log_event(
                db,
                state.session_id,
                "session.closing",
                {
                    "reason": reason.value,
                    "spoken": bool(text),
                    "text_source": text_source,
                    "questions": len(state.questions),
                },
                elapsed_s=elapsed,
            )
            await db.execute(
                update(Interview)
                .where(
                    Interview.id == uuid.UUID(state.interview_id),
                    Interview.status.in_([InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value]),
                )
                .values(
                    status=InterviewStatus.CANCELLED.value if aborted else InterviewStatus.COMPLETED.value,
                    completed_at=now if not aborted else None,
                )
            )
            if state.intake is not None:
                await self.intake.closing_db(state, db, reason=reason, spoken=bool(text), aborted=aborted)
            elif not aborted:
                await enqueue(
                    "scoring.finalize",
                    {"session_id": state.session_id},
                    dedupe_key=f"scoring.finalize:{state.session_id}",
                    db=db,
                )
                await enqueue(
                    "report.generate",
                    {"session_id": state.session_id},
                    dedupe_key=f"report.generate:{state.session_id}",
                    db=db,
                )
            await self._save(
                state,
                durable=True,
                db=db,
                extra_columns={
                    "ended_at": now,
                    "end_reason": reason.value,
                    "elapsed_seconds": round(elapsed, 3),
                },
            )
        # Double-check: an evaluation finishing while we were closing found the
        # session still live and only queued its result; apply it now.
        late = await pop_ready_evaluations(self.redis, state.session_id)
        if late:
            known = {a.id for a in state.answers}
            applied = [p for p in late if p.get("answer_id") in known and apply_evaluation(state, p)]
            if applied:
                await self._save(state, durable=True)
        await self.store.expire(state.session_id, self.config.state_ttl_ended_s)
        await publish_session_event(
            state.session_id,
            {"type": "session.closing", "session_id": state.session_id, "reason": reason.value},
        )
        if text:
            yield UtteranceBegin(utterance_id=utterance_id, kind="closing", behavior=spec, text=text)
            yield UtteranceMeta(
                utterance_id=utterance_id,
                question_id=None,
                behavior=spec,
                decision={"action": TurnAction.CLOSE_INTERVIEW.value, "reason": reason.value},
                full_text=text,
            )
        yield SessionClosing(reason=reason)
        self._forget(state.session_id)

    def _forget(self, session_id: str) -> None:
        self._prep.pop(session_id, None)
        self._partial.pop(session_id, None)
        self._asked_vectors.pop(session_id, None)
        for key in [k for k in self._bank_cache if k[0] == session_id]:
            self._bank_cache.pop(key, None)

    # ================================================================= coding
    async def _start_coding_events(
        self, state: InterviewState, *, ack: bool
    ) -> AsyncGenerator[TurnEvent, None]:
        """Select a problem, create the question row, start the coding session,
        speak the transition. Yields nothing when coding is unavailable."""
        if not self.coding.available:
            state.coding.unavailable = True
            state.force_coding = False
            log.info("engine.coding_unavailable", session_id=state.session_id, reason="no_service")
            return
        cq_id = await self.coding.select(state)
        info = await self.coding.question_info(cq_id) if cq_id else None
        if not cq_id or info is None:
            state.coding.unavailable = True
            state.force_coding = False
            log.info("engine.coding_unavailable", session_id=state.session_id, reason="no_problem")
            return
        elapsed = self._elapsed(state)
        question_id = str(uuid.uuid4())
        minutes = int(state.round_config.coding.duration_minutes)
        ack_text = (
            pick(
                ("Thanks for walking me through that.", "Thanks, that's helpful.", "Okay, thank you."),
                f"{state.session_id}:c",
            )
            if ack
            else None
        )
        text = transition_speech(state, title=info["title"], minutes=minutes, ack=ack_text)
        language = preferred_language(state)
        comp_keys = [
            k for k in (info.get("competency_keys") or []) if k in state.round_config.competency_keys()
        ] or coding_competencies(state)
        decision = TurnDecision(
            speech=text,
            action=TurnAction.START_CODING,
            answer_quality=state.answers[-1].quality if state.answers else None,
            competency_key=comp_keys[0] if comp_keys else None,
            topic="coding exercise",
            difficulty=state.round_config.coding.difficulty,
            kind=question_kind(TurnAction.START_CODING, "coding", None),
            expression=beh.provisional(TurnAction.START_CODING).expression,
            gesture=beh.provisional(TurnAction.START_CODING).gesture,
            gesture_intensity=beh.provisional(TurnAction.START_CODING).gesture_intensity,
            question_focus=f"Coding exercise: {info['title']}"[:200],
            expected_evidence=["correct solution", "complexity analysis", "edge cases"],
            claim_id=None,
            bank_question_id=None,
            missing_specifics=[],
            new_claims=[],
            possible_contradiction=None,
            reason="Coding exercise due per policy",
        )
        async with session_scope() as db:
            await add_question(
                db,
                state.session_id,
                question_id=uuid.UUID(question_id),
                kind="coding",
                action=TurnAction.START_CODING.value,
                source="curated",
                text_value=text,
                segment="coding",
                topic="coding exercise",
                competency_keys=comp_keys,
                difficulty=state.round_config.coding.difficulty.value,
                question_focus=decision.question_focus,
                reason=decision.reason,
                expected_evidence=list(decision.expected_evidence),
                claim_id=None,
                decision={**decision.model_dump(mode="json"), "coding_question_id": cq_id},
                elapsed_s=elapsed,
                coding_question_id=cq_id,
            )
        coding_state = await self.coding.start(state, cq_id, question_id=question_id, language=language)
        if coding_state is None:
            async with session_scope() as db:
                await db.execute(
                    update(Question)
                    .where(Question.id == uuid.UUID(question_id))
                    .values(decision={**decision.model_dump(mode="json"), "coding_start_failed": True})
                )
                await log_event(
                    db,
                    state.session_id,
                    "coding.start_failed",
                    {"coding_question_id": cq_id},
                    elapsed_s=elapsed,
                )
            state.coding.unavailable = True
            state.force_coding = False
            return
        utterance_id = str(uuid.uuid4())
        spec = beh.provisional(TurnAction.START_CODING)
        directive = compute_directive(state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s)
        async with session_scope() as db:
            transcript = await add_transcript(
                db,
                state.session_id,
                speaker="interviewer",
                text_value=text,
                start_ms=int(elapsed * 1000),
                end_ms=int(elapsed * 1000),
                question_id=question_id,
            )
            apply_decision(
                state,
                decision,
                directive,
                utterance_id=utterance_id,
                question_id=question_id,
                created_question=True,
                elapsed_s=elapsed,
                kind="transition",
                transcript_id=str(transcript.id),
                coding_question_id=cq_id,
            )
            cs = state.coding
            cs.active = True
            cs.coding_session_id = coding_state.coding_session_id
            if coding_state.coding_session_id not in cs.coding_session_ids:
                cs.coding_session_ids.append(coding_state.coding_session_id)
            cs.coding_question_id = cq_id
            cs.question_id = question_id
            cs.title = coding_state.question.title
            cs.language = coding_state.language
            cs.started_at_s = elapsed
            cs.time_limit_s = int(coding_state.time_limit_s)
            cs.hints_available = int(coding_state.hints_available)
            cs.hints_used = int(coding_state.hints_used)
            cs.comments_made = 0
            cs.last_comment_at_s = elapsed
            cs.last_edit_at_s = elapsed
            cs.observer_state = {}
            cs.time_warnings_spoken = []
            state.force_coding = False
            state.turn_state = TurnState.CODING
            state.current_segment = "coding"
            await log_event(
                db,
                state.session_id,
                "coding.started",
                {
                    "coding_session_id": cs.coding_session_id,
                    "coding_question_id": cq_id,
                    "language": language.value,
                },
                elapsed_s=elapsed,
            )
            await self._save(state, durable=True, db=db)
        yield UtteranceBegin(
            utterance_id=utterance_id, kind="transition", behavior=spec, question_id=question_id, text=text
        )
        yield UtteranceMeta(
            utterance_id=utterance_id,
            question_id=question_id,
            behavior=spec,
            decision={
                **decision.model_dump(mode="json"),
                "coding_session_id": coding_state.coding_session_id,
            },
            full_text=text,
        )
        yield CodingStarted(coding=coding_state)

    def handle_coding_activity(self, session_id: str, activity: CodingActivity) -> EventStream:
        return EventStream(self._coding_activity(session_id, activity))

    async def _coding_activity(
        self, session_id: str, activity: CodingActivity
    ) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            cs = state.coding
            if (
                state.status in ENDED_STATUSES
                or not cs.active
                or activity.coding_session_id != cs.coding_session_id
            ):
                return
            elapsed = self._elapsed(state)
            kind = activity.kind
            if activity.version is not None:
                cs.last_code_version = max(cs.last_code_version, activity.version)
            if kind == "edit":
                cs.last_edit_at_s = elapsed
            if activity.language:
                try:
                    cs.language = CodeLanguage(activity.language)
                except ValueError:
                    pass
            if kind == "language_changed":
                await self._save(state)
                return
            if kind == "hint_requested":
                async with aclosing(self._hint_events(state, requested_via=activity.requested_via)) as _relay:
                    async for event in _relay:
                        yield event
                return
            if kind in ("submitted", "time_up"):
                async with aclosing(
                    self._finish_coding_events(state, "submitted" if kind == "submitted" else "time_limit")
                ) as _relay:
                    async for event in _relay:
                        yield event
                return
            if self._coding_remaining(state) <= 0:
                # The engine owns the coding clock (pauses excluded): time is up.
                async with aclosing(self._finish_coding_events(state, "time_limit")) as _relay:
                    async for event in _relay:
                        yield event
                return
            if kind == "run_completed" and activity.run is not None:
                summary = run_summary(activity.run)
                from app.services.interview.state import CodingRunSummary

                cs.last_run = CodingRunSummary(
                    run_id=summary["run_id"],
                    kind=summary["kind"],
                    status=summary["status"],
                    passed=summary["passed"],
                    total=summary["total"],
                    at_s=elapsed,
                    error_excerpt=summary["error_excerpt"],
                )
                cs.runs += 1
            trigger = {
                "edit": "edit",
                "run_completed": "run_completed",
                "idle": "idle",
                "time_warning": "time_warning",
            }.get(kind, kind)
            async with aclosing(self._coding_observe_events(state, trigger)) as _relay:
                async for event in _relay:
                    yield event

    def _coding_remaining(self, state: InterviewState) -> float:
        cs = state.coding
        if not cs.active:
            return float("inf")
        return float(cs.time_limit_s) - (self._elapsed(state) - cs.started_at_s)

    async def _coding_observe_events(
        self, state: InterviewState, trigger: str
    ) -> AsyncGenerator[TurnEvent, None]:
        """Proactive comment while coding: the coding observer decides (silence
        is the default); deterministic time warnings at 5 and 1 minute(s) left
        when the observer stays silent."""
        cs = state.coding
        elapsed = self._elapsed(state)
        observation = await self.coding.observe(state, trigger=trigger)
        text: str | None = None
        spec: BehaviorSpec | None = None
        focus = "time"
        remaining = self._coding_remaining(state)
        threshold = 60 if remaining <= 90 else (300 if remaining <= 330 else None)
        if observation is not None:
            text, spec, focus = observation.speech, observation.behavior, observation.focus
            if observation.kind == "time_warning" and threshold and threshold not in cs.time_warnings_spoken:
                cs.time_warnings_spoken.append(threshold)
        elif trigger in ("time_warning", "tick"):
            if trigger == "time_warning" and threshold is None:
                threshold = 300
            if threshold and threshold not in cs.time_warnings_spoken and remaining > 0:
                cs.time_warnings_spoken.append(threshold)
                text = f"Just a heads-up: about {'one minute' if threshold == 60 else 'five minutes'} left on this exercise."
        if not text:
            await self._save(state)
            return
        spec = spec or beh.provisional(TurnAction.CODING_COMMENT)
        utterance_id = str(uuid.uuid4())
        question_id = await self._record_coding_comment(state, text, utterance_id, elapsed, focus)
        yield UtteranceBegin(
            utterance_id=utterance_id, kind="comment", behavior=spec, question_id=question_id, text=text
        )
        yield UtteranceMeta(
            utterance_id=utterance_id,
            question_id=question_id,
            behavior=spec,
            decision={"action": TurnAction.CODING_COMMENT.value, "trigger": trigger, "focus": focus},
            full_text=text,
        )

    async def _record_coding_comment(
        self, state: InterviewState, text: str, utterance_id: str, elapsed: float, focus: str
    ) -> str | None:
        cs = state.coding
        is_question = "?" in text
        question_id = str(uuid.uuid4()) if is_question else None
        async with session_scope() as db:
            comp = coding_competencies(state)
            if question_id:
                await add_question(
                    db,
                    state.session_id,
                    question_id=uuid.UUID(question_id),
                    kind="coding",
                    action=TurnAction.CODING_COMMENT.value,
                    source="generated",
                    text_value=text,
                    segment="coding",
                    topic=f"coding: {focus}"[:120],
                    competency_keys=comp[:1],
                    difficulty=state.round_config.coding.difficulty.value,
                    question_focus=text[:200],
                    reason=f"Code observation ({focus})",
                    expected_evidence=[],
                    claim_id=None,
                    decision={"action": TurnAction.CODING_COMMENT.value, "focus": focus},
                    elapsed_s=elapsed,
                    parent_question_id=cs.question_id,
                    coding_question_id=cs.coding_question_id,
                )
            row = await add_transcript(
                db,
                state.session_id,
                speaker="interviewer",
                text_value=text,
                start_ms=int(elapsed * 1000),
                end_ms=int(elapsed * 1000),
                question_id=question_id,
            )
            from app.services.interview.state import QuestionRecord

            if question_id:
                state.questions.append(
                    QuestionRecord(
                        id=question_id,
                        sequence=len(state.questions) + 1,
                        action=TurnAction.CODING_COMMENT,
                        text=text,
                        focus=text[:200],
                        competency_key=comp[0] if comp else None,
                        topic=f"coding: {focus}",
                        difficulty=state.round_config.coding.difficulty,
                        parent_id=cs.question_id,
                        asked_at_s=elapsed,
                        kind="comment",
                        coding_question_id=cs.coding_question_id,
                    )
                )
                state.current_question_id = question_id
            add_utterance(
                state,
                UtteranceRecord(
                    id=utterance_id,
                    kind="comment",
                    action=TurnAction.CODING_COMMENT,
                    question_id=question_id,
                    text=text,
                    at_s=elapsed,
                    transcript_id=str(row.id),
                ),
            )
            record_interviewer_text(state, text=text, at_s=elapsed, question_id=question_id)
            cs.comments_made += 1
            cs.last_comment_at_s = elapsed
            await log_event(
                db,
                state.session_id,
                "coding.comment",
                {"focus": focus, "question_id": question_id},
                elapsed_s=elapsed,
            )
            await self._save(state, db=db)
        return question_id

    async def _hint_events(
        self, state: InterviewState, *, requested_via: str
    ) -> AsyncGenerator[TurnEvent, None]:
        cs = state.coding
        elapsed = self._elapsed(state)
        cfg = state.round_config.coding
        result = None
        if cfg.hints_enabled and cfg.max_hints > 0 and cs.coding_session_id:
            result = await self.coding.hint(cs.coding_session_id, requested_via=requested_via)
        if result is not None:
            level, hint_text = result.level, result.text
            cs.hints_used = (
                int(result.hints_used) if result.hints_used is not None else max(cs.hints_used + 1, level)
            )
            cs.last_hint_level = level
            state.hints_used_total += 1
            remaining = (
                int(result.hints_remaining)
                if result.hints_remaining is not None
                else max(0, cs.hints_available - cs.hints_used)
            )
            yield CodingHintIssued(
                coding_session_id=cs.coding_session_id or "",
                level=level,
                text=hint_text,
                hints_used=cs.hints_used,
                hints_remaining=remaining,
            )
            if result.speech and 0 < len(result.speech.split()) <= 60:
                spoken = result.speech.strip()
            else:
                spoken = f"Sure, here's a nudge: {short_hint(hint_text)} It's in the hint panel too."
        elif not cfg.hints_enabled or cfg.max_hints == 0:
            spoken = "Hints aren't available for this exercise, but feel free to talk me through where you're stuck."
        elif cs.hints_used >= cs.hints_available:
            spoken = "You've used all the hints for this problem. Talk me through where you're stuck, and take it step by step."
        else:
            spoken = "I can't pull up a hint right now. Try talking me through your current approach, step by step."
        utterance_id = str(uuid.uuid4())
        spec = beh.provisional(TurnAction.GIVE_HINT)
        async with session_scope() as db:
            row = await add_transcript(
                db,
                state.session_id,
                speaker="interviewer",
                text_value=spoken,
                start_ms=int(elapsed * 1000),
                end_ms=int(elapsed * 1000),
                question_id=cs.question_id,
            )
            add_utterance(
                state,
                UtteranceRecord(
                    id=utterance_id,
                    kind="hint",
                    action=TurnAction.GIVE_HINT,
                    question_id=cs.question_id,
                    text=spoken,
                    at_s=elapsed,
                    transcript_id=str(row.id),
                ),
            )
            record_interviewer_text(state, text=spoken, at_s=elapsed, question_id=cs.question_id)
            await log_event(
                db,
                state.session_id,
                "coding.hint",
                {
                    "level": result.level if result else None,
                    "requested_via": requested_via,
                    "issued": result is not None,
                },
                elapsed_s=elapsed,
            )
            await self._save(state, db=db)
        yield UtteranceBegin(utterance_id=utterance_id, kind="hint", behavior=spec, text=spoken)
        yield UtteranceMeta(
            utterance_id=utterance_id,
            question_id=cs.question_id,
            behavior=spec,
            decision={"action": TurnAction.GIVE_HINT.value, "level": result.level if result else None},
            full_text=spoken,
        )

    async def _finish_coding_events(
        self, state: InterviewState, reason: str
    ) -> AsyncGenerator[TurnEvent, None]:
        cs = state.coding
        cs_id = cs.coding_session_id or ""
        await self.coding.finish(cs_id, reason=reason)
        cs.active = False
        cs.problems_done += 1
        cs.completed = cs.problems_done >= state.round_config.coding.max_problems
        cs.ended_reason = reason
        state.turn_state = TurnState.AI_SPEAKING
        elapsed = self._elapsed(state)
        async with session_scope() as db:
            await log_event(
                db,
                state.session_id,
                "coding.ended",
                {"coding_session_id": cs_id, "reason": reason, "hints_used": cs.hints_used, "runs": cs.runs},
                elapsed_s=elapsed,
            )
            await self._save(state, db=db)
        ended_reason = reason if reason in ("submitted", "time_limit") else "interviewer_moved_on"
        yield CodingEnded(coding_session_id=cs_id, reason=ended_reason)  # type: ignore[arg-type]
        # Resume the interview: a follow-up about the solution when time permits.
        directive = compute_directive(state, now=self.now(), closing_reserve_s=self.config.closing_reserve_s)
        if directive.must_close:
            async with aclosing(
                self._closing_events(
                    state, directive.close_reason or SessionEndReason.TIME_LIMIT, last_answer=None
                )
            ) as _relay:
                async for event in _relay:
                    yield event
            return
        remaining = directive.time.remaining_s - self.config.closing_reserve_s
        note = (
            "(The candidate submitted their solution.)"
            if reason == "submitted"
            else "(The coding time ran out.)"
        )
        if remaining >= self.config.coding_followup_min_remaining_s:
            comp = coding_competencies(state)
            target = comp[0] if comp else directive.target_competency
            run = cs.last_run
            run_text = (
                f"Last run: {run.status}, {run.passed}/{run.total} tests passed."
                if run
                else "No test run was recorded."
            )
            directive = replace(
                directive,
                recommended_action=TurnAction.ASK_FOLLOWUP,
                required_action=None,
                allowed_actions=[
                    TurnAction.ASK_FOLLOWUP,
                    TurnAction.ASK_QUESTION,
                    TurnAction.REQUEST_SPECIFICITY,
                ],
                target_competency=target,
                stay_on_topic=True,
                candidate_bank_questions=[],
                instructions=[
                    f"The coding exercise '{cs.title}' just ended ({'submitted' if reason == 'submitted' else 'time limit'}). {run_text}",
                    "Acknowledge briefly (never say whether it was right), then ask one question about their solution: its time and space complexity, a trade-off they made, or an edge case their tests may not cover.",
                ],
            )
            cs.followup_pending = False
            state.current_competency = target
            async with aclosing(self._decision_turn(state, directive, last_answer=note)) as _relay:
                async for event in _relay:
                    yield event
            return
        directive.instructions.append(
            "The coding exercise just ended; transition back to the conversation naturally."
        )
        directive.candidate_bank_questions = await self._cached_bank(state, directive)
        async with aclosing(self._decision_turn(state, directive, last_answer=note)) as _relay:
            async for event in _relay:
                yield event

    async def _coding_turn(
        self, state: InterviewState, turn: CandidateTurn, text: str, *, replay: bool
    ) -> AsyncGenerator[TurnEvent, None]:
        """The candidate spoke while coding: hints, repeats, or a brief reply."""
        special = classify(text, coding_active=True)
        if special.kind is SpecialKind.WANTS_HINT:
            await self._record_candidate_utterance(state, turn, text)
            async with aclosing(self._hint_events(state, requested_via="voice")) as _relay:
                async for event in _relay:
                    yield event
            return
        if special.kind is SpecialKind.REPEAT:
            await self._record_candidate_utterance(state, turn, text)
            async with aclosing(self._repeat_events(state)) as _relay:
                async for event in _relay:
                    yield event
            return
        current = state.current_question()
        evaluate_answer = bool(
            current is not None
            and current.action == TurnAction.CODING_COMMENT
            and not current.answered
            and special.is_answer
        )
        pending: PendingAnswer | None = None
        if not replay:
            answer_id = uuid.uuid4()
            record_answer(
                state,
                answer_id=str(answer_id),
                text=text,
                typed=turn.typed,
                started_s=turn.started_ms / 1000.0,
                ended_s=turn.ended_ms / 1000.0,
                interrupted_ai=turn.interrupted_ai,
                special=special.kind.value if special.is_special else None,
                quality=special.quality,
                quality_source="special" if special.quality else None,
            )
            if evaluate_answer:
                state.pending_evaluations.append(str(answer_id))
            pending = PendingAnswer(
                answer_id=answer_id,
                turn=turn,
                special=special,
                quick=None,
                question_id=current.id if current else None,
                evaluate=evaluate_answer,
            )
            if not turn.speculative:
                await self._persist_pending_answer(state, pending)
        if self._coding_remaining(state) <= 0:
            async with aclosing(self._finish_coding_events(state, "time_limit")) as _relay:
                async for event in _relay:
                    yield event
            return
        if special.kind in (SpecialKind.BACKCHANNEL, SpecialKind.EMPTY, SpecialKind.WANTS_TIME):
            await self._save(state)
            return  # stay silent while they code
        context = await self.coding.context(state.coding.coding_session_id or "")
        code = getattr(context, "current_code", "") if context is not None else ""
        last_run = None
        if state.coding.last_run is not None:
            r = state.coding.last_run
            last_run = f"{r.kind} {r.status}, {r.passed}/{r.total} passed" + (
                f"; error: {r.error_excerpt[:160]}" if r.error_excerpt else ""
            )
        asks = text.rstrip().endswith("?")
        recommended = TurnAction.CLARIFY_QUESTION if asks else TurnAction.ACKNOWLEDGE_AND_WAIT
        base = compute_directive(
            state, special=special, now=self.now(), closing_reserve_s=self.config.closing_reserve_s
        )
        directive = replace(
            base,
            recommended_action=recommended,
            required_action=None,
            allowed_actions=sorted(CODING_FLOW_ACTIONS, key=lambda a: a.value),
            target_competency=next(iter(coding_competencies(state)), base.target_competency),
            stay_on_topic=True,
            must_close=False,
            start_coding=False,
            candidate_bank_questions=[],
            instructions=[],
        )
        prompt = coding_user_prompt(
            state,
            last_answer=text,
            code=code or "",
            title=state.coding.title or "the coding problem",
            difficulty=state.round_config.coding.difficulty.value,
            language=(state.coding.language.value if state.coding.language else "unknown"),
            elapsed_s=self._elapsed(state) - state.coding.started_at_s,
            limit_s=float(state.coding.time_limit_s),
            last_run=last_run,
            recommended=recommended.value,
            allowed=", ".join(a.value for a in directive.allowed_actions),
        )
        async with aclosing(
            self._decision_turn(
                state,
                directive,
                last_answer=text,
                pending_answer=pending if turn.speculative else None,
                speculative=turn.speculative,
                coding_prompt=prompt,
            )
        ) as _relay:
            async for event in _relay:
                yield event

    # ========================================================= intake activities
    def intake_activity_active(self, state: InterviewState) -> bool:
        return state.intake is not None and self.intake.activity_active(state)

    def handle_activity_event(self, session_id: str, event: ActivityEvent) -> EventStream:
        """Intake activity phase events from the runtime (INTAKE_INTERVIEW.md 7.3). Other rounds: nothing."""
        return EventStream(self._activity_event(session_id, event))

    async def _activity_event(self, session_id: str, event: ActivityEvent) -> AsyncGenerator[TurnEvent, None]:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.status in ENDED_STATUSES or state.intake is None:
                return
            async with aclosing(self.intake.activity_event(state, event)) as _relay:
                async for item in _relay:
                    yield item

    async def intake_jump(self, session_id: str, stage: str) -> InterviewState:
        """Test hook: move an intake session to the start of ``stage``."""
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            if state.intake is None:
                raise SessionNotFound(f"session {session_id} is not an intake session")
            await self.intake.jump(state, stage)
        return state

    # ============================================================ test hooks
    async def advance_clock(self, session_id: str, seconds: float) -> InterviewState:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            state.elapsed_before_pause_s = max(0.0, state.elapsed_before_pause_s + float(seconds))
            async with session_scope() as db:
                await log_event(
                    db,
                    session_id,
                    "test.clock_advanced",
                    {"seconds": seconds},
                    elapsed_s=self._elapsed(state),
                )
                await self._save(state, durable=True, db=db)
        await publish_session_event(
            session_id, {"type": "clock.advanced", "session_id": session_id, "seconds": seconds}
        )
        return state

    async def force_coding(self, session_id: str) -> InterviewState:
        async with self._session_lock(session_id):
            state = await self._load(session_id)
            state.force_coding = True
            state.coding.unavailable = False
            async with session_scope() as db:
                await log_event(db, session_id, "test.force_coding", {}, elapsed_s=self._elapsed(state))
                await self._save(state, durable=True, db=db)
        return state


def remaining_text(full_text: str, spoken_text: str | None) -> str:
    """The unplayed part of an utterance, resuming at the start of the first
    sentence that was not completely spoken."""
    import re

    full = full_text.strip()
    spoken = " ".join((spoken_text or "").split())
    if not spoken:
        return full
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", full) if s.strip()]
    consumed = 0
    for sentence in sentences:
        norm = " ".join(sentence.split())
        if norm and norm in spoken:
            consumed += 1
            continue
        break
    return " ".join(sentences[consumed:]).strip()


_engine: InterviewEngine | None = None


def get_engine() -> InterviewEngine:
    """Process-wide engine (realtime runtime, workers, test hooks)."""
    global _engine
    if _engine is None:
        _engine = InterviewEngine()
    return _engine


def set_engine(engine: InterviewEngine | None) -> InterviewEngine | None:
    global _engine
    previous = _engine
    _engine = engine
    return previous


__all__ = ["EventStream", "InterviewEngine", "SessionNotFound", "get_engine", "remaining_text", "set_engine"]
