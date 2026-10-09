"""Coding service: the in-process implementation of
:class:`app.services.coding.contracts.CodingService` used by the interview
engine and the realtime runtime.

Every public method opens its own transaction (``session_scope``) because the
callers (engine, realtime runtime) are not request handlers. Mutations of one
coding session are serialised with ``SELECT ... FOR UPDATE`` on its
``coding_sessions`` row (plus an in-process lock for runs and hints), and
sandbox executions / model calls happen *outside* those transactions so no row
lock is held while waiting on the network.

Hot editor state (latest code, version, last edit time) is cached per process
so the ~800 ms ``code.update`` stream does not hit Postgres; it is persisted
with every snapshot and at most every :data:`EDITOR_PERSIST_INTERVAL_S`
seconds otherwise (``coding_sessions.observer_state["editor"]``).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.core.queue import enqueue
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
    BankStatus,
    CodeLanguage,
    CodingSessionStatus,
    Difficulty,
    EvidenceSignal,
    EvidenceSource,
    ModelTask,
    RunKind,
    RunStatus,
    SnapshotTrigger,
)
from app.schemas.ai import HintResponse
from app.schemas.realtime import CodingRunResultMsg, CodingStateMsg
from app.schemas.rounds import CodingSection, RoundConfig
from app.services.coding import observer as observer_module
from app.services.coding.analysis import analyze
from app.services.coding.contracts import CodingContext
from app.services.coding.generation import generate_problem
from app.services.coding.observer import ObservationDecision
from app.services.coding.prompts import HINT_SYSTEM, HINT_USER
from app.services.coding.questions import (
    MAX_STORED_OUTPUT_CHARS,
    allowed_languages,
    build_execute_request,
    candidate_output,
    candidate_test_results,
    choose_language,
    first_error_excerpt,
    public_question,
    staff_test_results,
    starter_code,
    truncate,
)
from app.services.coding.sandbox_client import SandboxClient, get_sandbox_client
from app.services.coding.selection import (
    candidate_pool,
    pinned_question,
    rank_questions,
    used_question_ids,
)
from app.services.coding.snapshots import change_stats, decide_snapshot, diff_counts, unified_diff

log = get_logger(__name__)

EVALUATE_JOB = "coding.evaluate"
MAX_HINTS = 3
HINT_ASSISTANCE_STRENGTH = 0.07  # ENGINE_DESIGN §7: 1 - 0.07 per hint
EDITOR_PERSIST_INTERVAL_S = 5.0
LIVE_CACHE_MAX = 2000
LIVE_CACHE_TTL_S = 3600.0
RUN_SUMMARY_LIMIT = 20
MIN_RUN_INTERVAL_S = 1.0
HINT_TIMEOUT_S = 12.0
LARGE_INSERT_WINDOW_S = 2.0
END_REASON_STATUS: dict[str, CodingSessionStatus] = {
    "submitted": CodingSessionStatus.SUBMITTED,
    "time_limit": CodingSessionStatus.TIMED_OUT,
    "time_up": CodingSessionStatus.TIMED_OUT,
    "timed_out": CodingSessionStatus.TIMED_OUT,
    "moved_on": CodingSessionStatus.ABANDONED,
    "interviewer_moved_on": CodingSessionStatus.ABANDONED,
    "session_ended": CodingSessionStatus.ABANDONED,
    "abandoned": CodingSessionStatus.ABANDONED,
}
ENDED_MESSAGE = "This coding exercise has ended."


def utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid(value: str | uuid.UUID, *, what: str = "id") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"Unknown {what}.") from exc


def _opt_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


def round_config_of(session: InterviewSession) -> RoundConfig | None:
    try:
        return RoundConfig.model_validate(session.round_config) if session.round_config else None
    except ValidationError:
        log.warning("coding.round_config_invalid", session_id=str(session.id))
        return None


def coding_section_of(session: InterviewSession) -> CodingSection:
    config = round_config_of(session)
    return config.coding if config is not None else CodingSection()


def evidence_competencies(question: CodingQuestion, config: RoundConfig | None) -> list[str]:
    """Competencies coding evidence is attributed to: the question's keys that
    the round assesses; otherwise the question's own keys."""
    q_keys = [k for k in question.competency_keys or [] if k]
    if config is not None:
        round_keys = config.competency_keys()
        overlap = [k for k in q_keys if k in round_keys]
        if overlap:
            return overlap
        if not q_keys and round_keys:
            return round_keys[:1]
    return q_keys or ["problem_solving"]


def interview_elapsed_s(session: InterviewSession, now: datetime | None = None) -> float | None:
    """Interview clock (pauses excluded) from the durable state snapshot."""
    now = now or utcnow()
    state = session.state or {}
    try:
        base = float(state.get("elapsed_before_pause_s") or 0.0)
        resumed = state.get("last_resumed_at")
        if resumed:
            resumed_at = datetime.fromisoformat(str(resumed).replace("Z", "+00:00"))
            if resumed_at.tzinfo is None:
                resumed_at = resumed_at.replace(tzinfo=UTC)
            return base + max(0.0, (now - resumed_at).total_seconds())
        if base:
            return base
    except (TypeError, ValueError):
        pass
    if session.started_at is not None:
        return max(0.0, (now - session.started_at).total_seconds())
    return None


@dataclass(slots=True)
class HintResult:
    level: int
    text: str
    speech: str
    source: Literal["curated", "generated"]
    hints_used: int
    hints_remaining: int
    hint_id: str


@dataclass(slots=True)
class _LiveEditor:
    code: str
    language: str
    version: int
    last_edit_at: datetime | None
    last_update_at: datetime | None
    snapshot_code: str
    snapshot_seq: int
    snapshot_at: datetime
    persisted_at: datetime
    touched: float
    languages: list[str]
    paste_threshold: int


class CodingServiceImpl:
    """Implements the ``CodingService`` protocol (see ``contracts.py``)."""

    def __init__(
        self,
        *,
        sandbox: SandboxClient | None = None,
        ai: AIServices | None = None,
        clock: Any = utcnow,
    ) -> None:
        self._sandbox = sandbox
        self._ai = ai
        self._clock = clock
        self._live: OrderedDict[str, _LiveEditor] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_run_at: dict[str, tuple[float, tuple[Any, ...]]] = {}

    # ------------------------------------------------------------- plumbing
    @property
    def sandbox(self) -> SandboxClient:
        return self._sandbox or get_sandbox_client()

    @property
    def ai(self) -> AIServices:
        return self._ai or get_ai()

    def _now(self) -> datetime:
        return self._clock()

    def _lock(self, coding_session_id: str) -> asyncio.Lock:
        lock = self._locks.get(coding_session_id)
        if lock is None:
            if len(self._locks) > LIVE_CACHE_MAX:
                for key in [k for k, v in self._locks.items() if not v.locked()][: len(self._locks) // 2]:
                    self._locks.pop(key, None)
            lock = self._locks[coding_session_id] = asyncio.Lock()
        return lock

    def _cache_get(self, coding_session_id: str) -> _LiveEditor | None:
        entry = self._live.get(coding_session_id)
        if entry is None:
            return None
        if time.monotonic() - entry.touched > LIVE_CACHE_TTL_S:
            self._live.pop(coding_session_id, None)
            return None
        entry.touched = time.monotonic()
        self._live.move_to_end(coding_session_id)
        return entry

    def _cache_put(self, coding_session_id: str, entry: _LiveEditor) -> None:
        entry.touched = time.monotonic()
        self._live[coding_session_id] = entry
        self._live.move_to_end(coding_session_id)
        while len(self._live) > LIVE_CACHE_MAX:
            self._live.popitem(last=False)

    def forget(self, coding_session_id: str) -> None:
        self._live.pop(coding_session_id, None)
        self._last_run_at.pop(coding_session_id, None)

    async def _load(
        self, db: AsyncSession, coding_session_id: str | uuid.UUID, *, lock: bool = False
    ) -> tuple[CodingSession, CodingQuestion, InterviewSession]:
        cs_id = _uuid(coding_session_id, what="coding session")
        stmt = select(CodingSession).where(CodingSession.id == cs_id)
        if lock:
            stmt = stmt.with_for_update()
        cs = (await db.scalars(stmt)).one_or_none()
        if cs is None:
            raise NotFound("Coding session not found.", code="coding_session_not_found")
        question = await db.get(CodingQuestion, cs.coding_question_id)
        session = await db.get(InterviewSession, cs.session_id)
        if question is None or session is None:  # pragma: no cover - FK guarantees
            raise NotFound("Coding session is incomplete.", code="coding_session_not_found")
        return cs, question, session

    async def _latest_snapshot(self, db: AsyncSession, cs_id: uuid.UUID) -> CodeSnapshot | None:
        stmt = (
            select(CodeSnapshot)
            .where(CodeSnapshot.coding_session_id == cs_id)
            .order_by(CodeSnapshot.seq.desc())
            .limit(1)
        )
        return (await db.scalars(stmt)).one_or_none()

    async def _editor(self, db: AsyncSession, cs: CodingSession) -> _LiveEditor:
        """Live editor state (cache, else rebuilt from the database)."""
        key = str(cs.id)
        entry = self._cache_get(key)
        if entry is not None:
            return entry
        snapshot = await self._latest_snapshot(db, cs.id)
        editor = (cs.observer_state or {}).get("editor") or {}
        snap_code = snapshot.code if snapshot else ""
        snap_at = snapshot.created_at if snapshot and snapshot.created_at else cs.started_at
        state = cs.observer_state or {}
        code = str(editor["code"]) if isinstance(editor.get("code"), str) else snap_code
        last_edit = editor.get("last_edit_at")
        entry = _LiveEditor(
            code=code,
            language=str(editor.get("language") or cs.language),
            version=int(editor.get("version", -1)),
            last_edit_at=datetime.fromisoformat(last_edit) if last_edit else None,
            last_update_at=None,
            snapshot_code=snap_code,
            snapshot_seq=snapshot.seq if snapshot else 0,
            snapshot_at=snap_at,
            persisted_at=self._now(),
            touched=time.monotonic(),
            languages=[str(x) for x in state.get("languages") or [cs.language]],
            paste_threshold=int(state.get("paste_threshold") or CodingSection().large_paste_threshold_chars),
        )
        self._cache_put(key, entry)
        return entry

    @staticmethod
    async def _set_state_key(db: AsyncSession, cs_id: uuid.UUID, key: str, value: Any) -> None:
        """Atomically replace one top-level key of ``observer_state`` (jsonb_set)."""
        await db.execute(
            update(CodingSession)
            .where(CodingSession.id == cs_id)
            .values(
                observer_state=func.jsonb_set(CodingSession.observer_state, [key], cast(value, JSONB), True)
            )
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    def _editor_state(entry: _LiveEditor) -> dict[str, Any]:
        return {
            "code": entry.code,
            "language": entry.language,
            "version": entry.version,
            "last_edit_at": entry.last_edit_at.isoformat() if entry.last_edit_at else None,
        }

    def _elapsed(self, cs: CodingSession, now: datetime | None = None) -> float:
        now = now or self._now()
        return max(0.0, (now - cs.started_at).total_seconds()) if cs.started_at else 0.0

    async def _add_snapshot(
        self,
        db: AsyncSession,
        cs: CodingSession,
        entry: _LiveEditor,
        *,
        code: str,
        language: str,
        trigger: SnapshotTrigger,
        now: datetime,
    ) -> CodeSnapshot:
        last_seq = int(
            await db.scalar(
                select(func.coalesce(func.max(CodeSnapshot.seq), 0)).where(
                    CodeSnapshot.coding_session_id == cs.id
                )
            )
            or 0
        )
        previous = entry.snapshot_code if entry.snapshot_seq == last_seq else None
        if previous is None:
            latest = await self._latest_snapshot(db, cs.id)
            previous = latest.code if latest else ""
        seq = last_seq + 1
        diff = unified_diff(previous, code, from_label=f"v{seq - 1}", to_label=f"v{seq}") if seq > 1 else None
        added, removed = diff_counts(diff) if diff else (len(code.splitlines()), 0)
        snapshot = CodeSnapshot(
            id=uuid.uuid4(),
            coding_session_id=cs.id,
            seq=seq,
            language=language,
            code=code,
            diff_from_previous=diff,
            lines_added=added,
            lines_removed=removed,
            char_count=len(code),
            trigger=trigger.value,
            elapsed_seconds=round(self._elapsed(cs, now), 2),
        )
        db.add(snapshot)
        await db.flush()
        entry.snapshot_code = code
        entry.snapshot_seq = seq
        entry.snapshot_at = now
        return snapshot

    # ===================================================== select_question
    async def select_question(
        self,
        session_id: str,
        *,
        competency_keys: list[str],
        difficulty: Difficulty,
        languages: list[CodeLanguage],
    ) -> str | None:
        sid = _uuid(session_id, what="session")
        async with session_scope() as db:
            session = await db.get(InterviewSession, sid)
            if session is None:
                raise NotFound("Interview session not found.", code="session_not_found")
            config = round_config_of(session)
            section = config.coding if config is not None else CodingSection()
            langs = [CodeLanguage(lang) for lang in languages] if languages else list(section.languages)
            keys = [k for k in competency_keys if k] or (config.competency_keys() if config else [])
            difficulty = Difficulty(difficulty) if difficulty else section.difficulty
            used = await used_question_ids(db, sid)

            pinned = await pinned_question(
                db, section, organization_id=session.organization_id, used=used, languages=langs
            )
            if pinned is not None:
                log.info("coding.select.pinned", session_id=session_id, coding_question_id=str(pinned.id))
                return str(pinned.id)

            pool = await candidate_pool(
                db, organization_id=session.organization_id, used=used, languages=langs, section=section
            )
            ranked = await rank_questions(
                db,
                pool,
                competency_keys=keys,
                difficulty=difficulty,
                ai=self._ai,
                session_id=session_id,
                organization_id=str(session.organization_id),
            )
            if ranked:
                best = ranked[0]
                log.info(
                    "coding.select.retrieved",
                    session_id=session_id,
                    coding_question_id=str(best.question.id),
                    score=round(best.score, 3),
                    candidates=len(ranked),
                )
                return str(best.question.id)

            allow_generated = section.allow_generated
            avoid = [q.title for q in pool][:30]
            organization_id = session.organization_id
            minutes = section.duration_minutes
        if allow_generated:
            generated = await generate_problem(
                organization_id=organization_id,
                competency_keys=keys,
                difficulty=difficulty,
                minutes=minutes,
                avoid_titles=avoid,
                ai=self._ai,
                sandbox=self._sandbox,
                session_id=session_id,
            )
            if generated is not None:
                log.info(
                    "coding.select.generated", session_id=session_id, coding_question_id=str(generated.id)
                )
                return str(generated.id)
        log.info("coding.select.none", session_id=session_id)
        return None

    # =============================================================== start
    async def start(
        self, session_id: str, coding_question_id: str, *, question_id: str | None, language: CodeLanguage
    ) -> CodingStateMsg:
        sid = _uuid(session_id, what="session")
        qid = _uuid(coding_question_id, what="coding question")
        now = self._now()
        async with session_scope() as db:
            session = (
                await db.scalars(select(InterviewSession).where(InterviewSession.id == sid).with_for_update())
            ).one_or_none()
            if session is None:
                raise NotFound("Interview session not found.", code="session_not_found")
            question = await db.get(CodingQuestion, qid)
            if (
                question is None
                or question.status == BankStatus.RETIRED.value
                or (
                    question.organization_id is not None
                    and question.organization_id != session.organization_id
                )
            ):
                raise NotFound("Coding question not found.", code="coding_question_not_found")
            existing = (
                await db.scalars(
                    select(CodingSession).where(
                        CodingSession.session_id == sid,
                        CodingSession.status == CodingSessionStatus.ACTIVE.value,
                    )
                )
            ).first()
            if existing is not None:
                if existing.coding_question_id == qid:
                    return await self._state_payload(db, existing, question, session)
                raise Conflict("Another coding exercise is still in progress.", code="coding_session_active")
            config = round_config_of(session)
            section = config.coding if config is not None else CodingSection()
            allowed = allowed_languages(question, section)
            lang = choose_language(question, section, language)
            per_problem_s = max(300, int(section.duration_minutes * 60 / max(1, section.max_problems)))
            hints_available = min(MAX_HINTS, section.max_hints) if section.hints_enabled else 0
            starter = starter_code(question, lang)
            cs = CodingSession(
                id=uuid.uuid4(),
                session_id=sid,
                coding_question_id=qid,
                question_id=_opt_uuid(question_id),
                language=lang.value,
                status=CodingSessionStatus.ACTIVE.value,
                started_at=now,
                time_limit_seconds=per_problem_s,
                hints_available=hints_available,
                hints_used=0,
                observer_state={
                    "languages": [x.value for x in allowed],
                    "paste_threshold": section.large_paste_threshold_chars,
                    "editor": {"code": starter, "language": lang.value, "version": -1, "last_edit_at": None},
                    "observer": {
                        "baseline_features": analyze(starter, lang).as_dict(),
                        "min_seconds_between_comments": section.min_seconds_between_comments,
                        "proactive_comments": section.proactive_comments,
                    },
                    "runs": [],
                },
            )
            db.add(cs)
            await db.flush()
            snapshot = CodeSnapshot(
                id=uuid.uuid4(),
                coding_session_id=cs.id,
                seq=1,
                language=lang.value,
                code=starter,
                diff_from_previous=None,
                lines_added=len(starter.splitlines()),
                lines_removed=0,
                char_count=len(starter),
                trigger=SnapshotTrigger.AUTOSAVE.value,
                elapsed_seconds=0.0,
            )
            db.add(snapshot)
            await db.flush()
            self._cache_put(
                str(cs.id),
                _LiveEditor(
                    starter,
                    lang.value,
                    -1,
                    None,
                    None,
                    starter,
                    1,
                    now,
                    now,
                    time.monotonic(),
                    [x.value for x in allowed],
                    section.large_paste_threshold_chars,
                ),
            )
            log.info(
                "coding.started",
                session_id=session_id,
                coding_session_id=str(cs.id),
                coding_question_id=str(qid),
                language=lang.value,
                time_limit_s=per_problem_s,
            )
            return CodingStateMsg(
                coding_session_id=str(cs.id),
                question=public_question(question, allowed),
                language=lang,
                code=starter,
                time_limit_s=per_problem_s,
                elapsed_s=0,
                hints_available=hints_available,
                hints_used=0,
                hint_texts=[],
            )

    # ======================================================= record_update
    async def record_update(
        self, coding_session_id: str, *, code: str, language: CodeLanguage, version: int
    ) -> bool:
        now = self._now()
        lang = CodeLanguage(language)
        key = str(_uuid(coding_session_id, what="coding session"))
        entry = self._cache_get(key)
        if entry is None:
            async with session_scope() as db:
                cs, _, _ = await self._load(db, key)
                if cs.status != CodingSessionStatus.ACTIVE.value:
                    return False
                entry = await self._editor(db, cs)
        if version <= entry.version:
            return False  # stale or duplicate update
        language_changed = lang.value != entry.language
        if language_changed and lang.value not in entry.languages:
            log.warning("coding.language_not_allowed", coding_session_id=key, language=lang.value)
            return False
        if code != entry.code or language_changed:
            if not language_changed and entry.last_update_at is not None:
                inserted = change_stats(entry.code, code)
                window = (now - entry.last_update_at).total_seconds()
                if inserted.chars_changed >= entry.paste_threshold and window <= LARGE_INSERT_WINDOW_S:
                    await self._note_large_insert(key, inserted.chars_changed)
            entry.last_edit_at = now
        entry.code = code
        entry.version = version
        entry.language = lang.value
        entry.last_update_at = now
        seconds_since = (now - entry.snapshot_at).total_seconds() if entry.snapshot_at else None
        decision = decide_snapshot(entry.snapshot_code, code, seconds_since_snapshot=seconds_since)
        store = decision.store or language_changed
        if not store and (now - entry.persisted_at).total_seconds() < EDITOR_PERSIST_INTERVAL_S:
            return False
        async with session_scope() as db:
            cs, _question, _session = await self._load(db, key, lock=True)
            if cs.status != CodingSessionStatus.ACTIVE.value:
                self.forget(key)
                return False
            if language_changed:
                cs.language = lang.value
            stored = False
            if store:
                trigger = SnapshotTrigger.LANGUAGE_CHANGE if language_changed else SnapshotTrigger.AUTOSAVE
                await self._add_snapshot(
                    db, cs, entry, code=code, language=lang.value, trigger=trigger, now=now
                )
                stored = True
            await self._set_state_key(db, cs.id, "editor", self._editor_state(entry))
            entry.persisted_at = now
        if stored:
            log.debug(
                "coding.snapshot", coding_session_id=key, reason=decision.reason, seq=entry.snapshot_seq
            )
        return stored

    async def _note_large_insert(self, key: str, chars: int) -> None:
        """Objective observation for reviewers (never a verdict): a large block of
        text appeared between two editor updates."""
        async with session_scope() as db:
            await db.execute(
                update(CodingSession)
                .where(CodingSession.id == uuid.UUID(key))
                .values(paste_event_count=CodingSession.paste_event_count + 1)
                .execution_options(synchronize_session=False)
            )
        log.info("coding.large_insert", coding_session_id=key, chars=chars)

    # ================================================================= run
    async def run(
        self,
        coding_session_id: str,
        *,
        kind: RunKind,
        code: str,
        language: CodeLanguage,
        stdin: str | None = None,
    ) -> CodingRunResultMsg:
        key = str(_uuid(coding_session_id, what="coding session"))
        kind = RunKind(kind)
        lang = CodeLanguage(language)
        run_id = uuid.uuid4()
        lock = self._lock(key)
        if lock.locked():
            return self._rejected(key, run_id, kind, "A run is already in progress.")
        async with lock:
            fingerprint = (kind.value, lang.value, hash(code), stdin)
            last = self._last_run_at.get(key)
            if (
                last is not None
                and last[1] == fingerprint
                and time.monotonic() - last[0] < MIN_RUN_INTERVAL_S
            ):
                # Double click: the identical run was just executed.
                return self._rejected(
                    key,
                    run_id,
                    kind,
                    "That exact run just finished. Please wait a moment before running it again.",
                )
            now = self._now()
            # Phase 1: validate + snapshot (short transaction).
            async with session_scope() as db:
                cs, question, session = await self._load(db, key, lock=True)
                if cs.status != CodingSessionStatus.ACTIVE.value:
                    return self._rejected(key, run_id, kind, ENDED_MESSAGE)
                section = coding_section_of(session)
                if lang not in allowed_languages(question, section):
                    return self._rejected(
                        key, run_id, kind, f"{lang.value} is not available for this exercise."
                    )
                entry = await self._editor(db, cs)
                if lang.value != cs.language:
                    cs.language = lang.value
                trigger = SnapshotTrigger.SUBMIT if kind == RunKind.SUBMIT else SnapshotTrigger.RUN
                snapshot: CodeSnapshot | None
                if code != entry.snapshot_code or lang.value != entry.language:
                    snapshot = await self._add_snapshot(
                        db, cs, entry, code=code, language=lang.value, trigger=trigger, now=now
                    )
                else:
                    snapshot = await self._latest_snapshot(db, cs.id)
                if code != entry.code:
                    entry.last_edit_at = now
                entry.code = code
                entry.language = lang.value
                await self._set_state_key(db, cs.id, "editor", self._editor_state(entry))
                entry.persisted_at = now
                snapshot_id = snapshot.id if snapshot else None
                elapsed = round(self._elapsed(cs, now), 2)
                request = build_execute_request(
                    question, kind=kind, code=code, language=lang, stdin=stdin, request_id=run_id.hex
                )
            # Phase 2: execute (no transaction held).
            response, fault = await self.sandbox.execute_safe(request)
            self._last_run_at[key] = (time.monotonic(), fingerprint)
            # Phase 3: persist the run.
            staff_tests = staff_test_results(question, response.tests)
            visible = candidate_test_results(question, response.tests)
            meta = dict(response.meta or {})
            meta.update(
                {"request_id": request.request_id, "mode": request.mode, "truncated": response.truncated}
            )
            if fault is not None:
                meta.update(fault.as_meta())
            summary = {
                "run_id": str(run_id),
                "kind": kind.value,
                "status": response.status.value,
                "passed": response.passed,
                "total": response.total,
                "at_s": elapsed,
                "error_excerpt": None
                if fault
                else first_error_excerpt(
                    response.status.value, response.stderr, response.compile_output, visible
                ),
                "platform_fault": fault is not None,
            }
            async with session_scope() as db:
                cs = (
                    await db.scalars(
                        select(CodingSession).where(CodingSession.id == uuid.UUID(key)).with_for_update()
                    )
                ).one()
                db.add(
                    CodeRun(
                        id=run_id,
                        coding_session_id=cs.id,
                        snapshot_id=snapshot_id,
                        kind=kind.value,
                        language=lang.value,
                        status=response.status.value,
                        stdout=truncate(response.stdout, MAX_STORED_OUTPUT_CHARS) or None,
                        stderr=truncate(response.stderr, MAX_STORED_OUTPUT_CHARS) or None,
                        compile_output=truncate(response.compile_output, MAX_STORED_OUTPUT_CHARS) or None,
                        exit_code=response.exit_code,
                        test_results=staff_tests,
                        passed_count=response.passed,
                        total_count=response.total,
                        runtime_ms=response.runtime_ms,
                        memory_kb=response.memory_kb,
                        elapsed_seconds=elapsed,
                        sandbox_meta=meta,
                    )
                )
                runs = list((cs.observer_state or {}).get("runs") or [])
                runs.append(summary)
                await self._set_state_key(db, cs.id, "runs", runs[-RUN_SUMMARY_LIMIT:])
                if kind == RunKind.SUBMIT and fault is None:
                    cs.final_run_id = run_id
                    cs.final_code = code
            output = candidate_output(response)
            log.info(
                "coding.run",
                coding_session_id=key,
                run_id=str(run_id),
                kind=kind.value,
                language=lang.value,
                status=response.status.value,
                passed=response.passed,
                total=response.total,
                platform_fault=fault is not None,
            )
            return CodingRunResultMsg(
                coding_session_id=key,
                run_id=str(run_id),
                kind=kind,
                status=response.status,
                stdout=output["stdout"],
                stderr=output["stderr"],
                compile_output=output["compile_output"],
                exit_code=response.exit_code,
                runtime_ms=response.runtime_ms,
                passed=response.passed if kind != RunKind.RUN else 0,
                total=response.total if kind != RunKind.RUN else 0,
                tests=visible,
            )

    @staticmethod
    def _rejected(key: str, run_id: uuid.UUID, kind: RunKind, message: str) -> CodingRunResultMsg:
        return CodingRunResultMsg(
            coding_session_id=key, run_id=str(run_id), kind=kind, status=RunStatus.REJECTED, stderr=message
        )

    # =========================================================== hints
    async def request_hint(
        self, coding_session_id: str, *, requested_via: Literal["button", "voice"]
    ) -> tuple[int, str] | None:
        result = await self.request_hint_detailed(coding_session_id, requested_via=requested_via)
        return (result.level, result.text) if result is not None else None

    async def request_hint_detailed(
        self, coding_session_id: str, *, requested_via: Literal["button", "voice"] = "button"
    ) -> HintResult | None:
        """Issue the next hint level (1 -> 2 -> 3) or ``None`` when no hint is
        available. Also returns the spoken version and the source."""
        key = str(_uuid(coding_session_id, what="coding session"))
        via = "voice" if requested_via == "voice" else "button"
        async with self._lock(f"hint:{key}"):
            now = self._now()
            # Phase 1: gate + context.
            async with session_scope() as db:
                cs, question, session = await self._load(db, key)
                section = coding_section_of(session)
                available = min(MAX_HINTS, cs.hints_available)
                if (
                    cs.status != CodingSessionStatus.ACTIVE.value
                    or not section.hints_enabled
                    or cs.hints_used >= available
                ):
                    return None
                level = cs.hints_used + 1
                entry = await self._editor(db, cs)
                current_code = entry.code
                previous = [
                    h.content
                    for h in (
                        await db.scalars(
                            select(CodingHint)
                            .where(CodingHint.coding_session_id == cs.id)
                            .order_by(CodingHint.level)
                        )
                    ).all()
                ]
                runs = list((cs.observer_state or {}).get("runs") or [])
                curated = list(question.hints or [])
            # Phase 2: content (curated, else generated outside any transaction).
            if len(curated) >= level and str(curated[level - 1]).strip():
                text = str(curated[level - 1]).strip()
                speech = text
                source: Literal["curated", "generated"] = "curated"
            else:
                text, speech = await self._generate_hint(
                    question,
                    session,
                    level=level,
                    code=current_code,
                    language=entry.language,
                    previous=previous,
                    last_run=runs[-1] if runs else None,
                    allow_code=section.hint3_may_reveal_solution,
                )
                source = "generated"
            # Phase 3: record (row lock; unique (session, level) guards races).
            async with session_scope() as db:
                cs, question, session = await self._load(db, key, lock=True)
                if cs.status != CodingSessionStatus.ACTIVE.value or cs.hints_used != level - 1:
                    return None
                entry = await self._editor(db, cs)
                hint_snapshot: CodeSnapshot | None
                if entry.code != entry.snapshot_code:
                    hint_snapshot = await self._add_snapshot(
                        db,
                        cs,
                        entry,
                        code=entry.code,
                        language=entry.language,
                        trigger=SnapshotTrigger.HINT,
                        now=now,
                    )
                else:
                    hint_snapshot = await self._latest_snapshot(db, cs.id)
                elapsed = round(self._elapsed(cs, now), 2)
                hint = CodingHint(
                    id=uuid.uuid4(),
                    coding_session_id=cs.id,
                    level=level,
                    content=text,
                    source=source,
                    requested_via=via,
                    snapshot_id=hint_snapshot.id if hint_snapshot else None,
                    elapsed_seconds=elapsed,
                    delivered_at=now,
                )
                db.add(hint)
                cs.hints_used = level
                config = round_config_of(session)
                interview_elapsed = interview_elapsed_s(session, now)
                for competency in evidence_competencies(question, config):
                    db.add(
                        CompetencyEvidence(
                            session_id=session.id,
                            competency_key=competency,
                            question_id=cs.question_id,
                            coding_session_id=cs.id,
                            source=EvidenceSource.HINT_USAGE.value,
                            signal=EvidenceSignal.NEUTRAL.value,
                            dimension="assistance",
                            evidence=(
                                f"Requested hint {level} of {MAX_HINTS} ({source}) on '{question.title}' "
                                f"after {int(elapsed // 60)}m{int(elapsed % 60):02d}s of coding."
                            ),
                            strength=HINT_ASSISTANCE_STRENGTH,
                            confidence=1.0,
                            difficulty=question.difficulty,
                            elapsed_seconds=interview_elapsed,
                            transcript_ref={
                                "coding_session_id": str(cs.id),
                                "hint_id": str(hint.id),
                                "hint_level": level,
                            },
                        )
                    )
                await db.flush()
                hints_used = level
                remaining = max(0, available - hints_used)
            log.info("coding.hint", coding_session_id=key, level=level, source=source, requested_via=via)
            return HintResult(level, text, speech, source, hints_used, remaining, str(hint.id))

    async def _generate_hint(
        self,
        question: CodingQuestion,
        session: InterviewSession,
        *,
        level: int,
        code: str,
        language: str,
        previous: list[str],
        last_run: dict[str, Any] | None,
        allow_code: bool,
    ) -> tuple[str, str]:
        settings = get_settings()
        structure = analyze(code, language).summary()
        run_text = (
            f"{last_run.get('kind')} {last_run.get('status')} ({last_run.get('passed')}/{last_run.get('total')})"
            if last_run
            else "not run yet"
        )
        messages = [
            {
                "role": "system",
                "content": HINT_SYSTEM.render(
                    interviewer_name=settings.interviewer_name, allow_code=allow_code and level == MAX_HINTS
                ),
            },
            {
                "role": "user",
                "content": HINT_USER.render(
                    title=question.title,
                    difficulty=question.difficulty,
                    language=language,
                    prompt_excerpt=(question.prompt_markdown or "")[:2000],
                    expected_concepts=", ".join(question.expected_concepts or []) or "n/a",
                    optimal_complexity=", ".join(
                        f"{k} {v}" for k, v in (question.optimal_complexity or {}).items()
                    )
                    or "n/a",
                    common_errors="; ".join(question.common_errors or []) or "n/a",
                    structure=structure,
                    last_run=run_text,
                    code=code[:12000],
                    previous_hints=" | ".join(f"{i + 1}. {h}" for i, h in enumerate(previous)),
                    level=level,
                ),
            },
        ]
        try:
            result = await self.ai.llm.structured(
                HintResponse,
                messages,
                context=CallContext(
                    task=ModelTask.HINT_GENERATION,
                    session_id=str(session.id),
                    organization_id=str(session.organization_id),
                    prompt_version=HINT_USER.version,
                ),
                temperature=0.3,
                max_tokens=500,
                timeout=HINT_TIMEOUT_S,
                max_attempts=2,
            )
            text = result.value.hint_text.strip()
            speech = observer_module.clean_speech(result.value.speech) or text
            if text and (allow_code and level == MAX_HINTS or not _contains_code(text)):
                return text, speech
            log.warning("coding.hint.rejected_output", level=level)
        except AIError as exc:
            log.warning("coding.hint.model_failed", level=level, error=type(exc).__name__)
        fallback = fallback_hint(question, level)
        return fallback, fallback

    # =========================================================== finish
    async def finish(self, coding_session_id: str, *, reason: str) -> None:
        key = str(_uuid(coding_session_id, what="coding session"))
        status = END_REASON_STATUS.get(reason, CodingSessionStatus.ABANDONED)
        now = self._now()
        async with session_scope() as db:
            cs, _question, _session = await self._load(db, key, lock=True)
            if cs.status != CodingSessionStatus.ACTIVE.value:
                if (cs.evaluation or {}).get("status") != "completed":
                    await enqueue(
                        EVALUATE_JOB, {"coding_session_id": key}, dedupe_key=f"{EVALUATE_JOB}:{key}", db=db
                    )
                return
            entry = await self._editor(db, cs)
            if entry.code != entry.snapshot_code:
                trigger = (
                    SnapshotTrigger.SUBMIT
                    if status == CodingSessionStatus.SUBMITTED
                    else SnapshotTrigger.AUTOSAVE
                )
                await self._add_snapshot(
                    db, cs, entry, code=entry.code, language=entry.language, trigger=trigger, now=now
                )
            cs.status = status.value
            cs.ended_at = now
            cs.final_code = entry.code
            if cs.final_run_id is None:
                last_submit = (
                    await db.scalars(
                        select(CodeRun.id)
                        .where(CodeRun.coding_session_id == cs.id, CodeRun.kind == RunKind.SUBMIT.value)
                        .order_by(CodeRun.created_at.desc())
                        .limit(1)
                    )
                ).first()
                cs.final_run_id = last_submit
            await self._set_state_key(db, cs.id, "editor", self._editor_state(entry))
            await self._set_state_key(db, cs.id, "end_reason", reason)
            await enqueue(EVALUATE_JOB, {"coding_session_id": key}, dedupe_key=f"{EVALUATE_JOB}:{key}", db=db)
        self.forget(key)
        log.info("coding.finished", coding_session_id=key, reason=reason, status=status.value)

    # ========================================================== context
    async def context(self, coding_session_id: str) -> CodingContext:
        key = str(_uuid(coding_session_id, what="coding session"))
        async with session_scope() as db:
            cs, question, _session = await self._load(db, key)
            return await self._context(db, cs, question)

    async def _context(self, db: AsyncSession, cs: CodingSession, question: CodingQuestion) -> CodingContext:
        now = self._now()
        entry = await self._editor(db, cs)
        snapshots = (
            await db.scalars(
                select(CodeSnapshot)
                .where(CodeSnapshot.coding_session_id == cs.id)
                .order_by(CodeSnapshot.seq.desc())
                .limit(2)
            )
        ).all()
        count = int(
            await db.scalar(
                select(func.count()).select_from(CodeSnapshot).where(CodeSnapshot.coding_session_id == cs.id)
            )
            or 0
        )
        current = entry.code
        latest = snapshots[0] if snapshots else None
        if latest is not None and current != latest.code:
            previous_code: str | None = latest.code
            recent_diff: str | None = unified_diff(
                latest.code, current, from_label=f"v{latest.seq}", to_label="current"
            )
        else:
            previous_code = snapshots[1].code if len(snapshots) > 1 else None
            recent_diff = latest.diff_from_previous if latest is not None else None
        run_rows = (
            await db.scalars(
                select(CodeRun)
                .where(CodeRun.coding_session_id == cs.id)
                .order_by(CodeRun.created_at.desc(), CodeRun.id)
                .limit(10)
            )
        ).all()
        runs: list[dict[str, Any]] = []
        for run in reversed(run_rows):
            visible = [t for t in run.test_results or [] if not t.get("hidden")]
            platform_fault = bool((run.sandbox_meta or {}).get("platform_fault"))
            runs.append(
                {
                    "run_id": str(run.id),
                    "kind": run.kind,
                    "status": run.status,
                    "passed": run.passed_count,
                    "total": run.total_count,
                    "at_s": run.elapsed_seconds or 0.0,
                    "error_excerpt": None
                    if platform_fault
                    else first_error_excerpt(run.status, run.stderr, run.compile_output, visible),
                    "platform_fault": platform_fault,
                }
            )
        real_runs = [r for r in runs if not r["platform_fault"]]
        last_compile = last_runtime = None
        if real_runs:
            latest_run = real_runs[-1]
            if latest_run["status"] == RunStatus.COMPILE_ERROR.value:
                last_compile = latest_run["error_excerpt"]
            elif latest_run["status"] in (
                RunStatus.ERROR.value,
                RunStatus.TIMEOUT.value,
                RunStatus.MEMORY_EXCEEDED.value,
            ):
                last_runtime = latest_run["error_excerpt"] or latest_run["status"]
        hints = (
            await db.scalars(
                select(CodingHint).where(CodingHint.coding_session_id == cs.id).order_by(CodingHint.level)
            )
        ).all()
        transcripts = (
            await db.scalars(
                select(Transcript.text)
                .where(
                    Transcript.session_id == cs.session_id,
                    Transcript.speaker == "candidate",
                    Transcript.created_at >= cs.started_at,
                )
                .order_by(Transcript.seq.desc())
                .limit(8)
            )
        ).all()
        last_edit = entry.last_edit_at or cs.started_at
        return CodingContext(
            coding_session_id=str(cs.id),
            question_title=question.title,
            prompt_markdown=question.prompt_markdown,
            difficulty=Difficulty(question.difficulty),
            language=CodeLanguage(entry.language or cs.language),
            current_code=current,
            previous_code=previous_code,
            recent_diff=recent_diff,
            snapshots_count=count,
            runs=runs,
            last_compile_error=last_compile,
            last_runtime_error=last_runtime,
            hints_used=cs.hints_used,
            hints=[h.content for h in hints],
            elapsed_s=round(self._elapsed(cs, now), 2),
            time_limit_s=cs.time_limit_seconds,
            seconds_since_last_edit=max(0.0, (now - last_edit).total_seconds()) if last_edit else 0.0,
            expected_concepts=list(question.expected_concepts or []),
            optimal_complexity=dict(question.optimal_complexity or {}),
            candidate_explanations=[t for t in reversed(transcripts) if t],
        )

    # ==================================================== state_payload
    async def state_payload(self, coding_session_id: str) -> CodingStateMsg:
        key = str(_uuid(coding_session_id, what="coding session"))
        async with session_scope() as db:
            cs, question, session = await self._load(db, key)
            return await self._state_payload(db, cs, question, session)

    async def _state_payload(
        self, db: AsyncSession, cs: CodingSession, question: CodingQuestion, session: InterviewSession
    ) -> CodingStateMsg:
        section = coding_section_of(session)
        allowed = allowed_languages(question, section)
        entry = await self._editor(db, cs)
        hints = (
            await db.scalars(
                select(CodingHint).where(CodingHint.coding_session_id == cs.id).order_by(CodingHint.level)
            )
        ).all()
        return CodingStateMsg(
            coding_session_id=str(cs.id),
            question=public_question(question, allowed),
            language=CodeLanguage(entry.language or cs.language),
            code=entry.code,
            time_limit_s=cs.time_limit_seconds,
            elapsed_s=round(self._elapsed(cs), 2),
            hints_available=cs.hints_available,
            hints_used=cs.hints_used,
            hint_texts=[h.content for h in hints],
        )

    # ================================================== language change
    async def change_language(self, coding_session_id: str, language: CodeLanguage) -> CodingStateMsg:
        """Handle ``code.language``: switch the session language. When the
        editor still holds the previous language's untouched starter (or is
        blank), the code is replaced with the new language's starter; edited
        code is kept. Snapshots with trigger ``language_change`` and returns the
        candidate-visible state so the client can refresh the editor."""
        key = str(_uuid(coding_session_id, what="coding session"))
        lang = CodeLanguage(language)
        now = self._now()
        async with session_scope() as db:
            cs, question, session = await self._load(db, key, lock=True)
            section = coding_section_of(session)
            if cs.status == CodingSessionStatus.ACTIVE.value and lang in allowed_languages(question, section):
                entry = await self._editor(db, cs)
                if lang.value != entry.language:
                    previous_starter = (question.starter_code or {}).get(entry.language, "")
                    if not entry.code.strip() or entry.code.strip() == previous_starter.strip():
                        entry.code = starter_code(question, lang)
                    entry.language = lang.value
                    cs.language = lang.value
                    await self._add_snapshot(
                        db,
                        cs,
                        entry,
                        code=entry.code,
                        language=lang.value,
                        trigger=SnapshotTrigger.LANGUAGE_CHANGE,
                        now=now,
                    )
                    await self._set_state_key(db, cs.id, "editor", self._editor_state(entry))
                    entry.persisted_at = now
                    log.info("coding.language_changed", coding_session_id=key, language=lang.value)
            else:
                log.warning(
                    "coding.language_change_refused",
                    coding_session_id=key,
                    language=lang.value,
                    status=cs.status,
                )
            return await self._state_payload(db, cs, question, session)

    # ========================================================= observer
    async def observe(
        self, coding_session_id: str, trigger: str, *, candidate_utterance: str | None = None
    ) -> ObservationDecision | None:
        """Convenience for the engine: build the context, run
        :func:`app.services.coding.observer.decide` with the persisted observer
        state (``coding_sessions.observer_state["observer"]``) and save it."""
        key = str(_uuid(coding_session_id, what="coding session"))
        async with session_scope() as db:
            cs, question, session = await self._load(db, key)
            if cs.status != CodingSessionStatus.ACTIVE.value:
                return None
            context = await self._context(db, cs, question)
            section = coding_section_of(session)
            state = dict((cs.observer_state or {}).get("observer") or {})
            session_id, org_id = str(session.id), str(session.organization_id)
        decision = await observer_module.decide(
            context,
            trigger,
            state,
            section=section,
            candidate_utterance=candidate_utterance,
            ai=self._ai,
            session_id=session_id,
            organization_id=org_id,
        )
        async with session_scope() as db:
            await self._set_state_key(db, uuid.UUID(key), "observer", state)
        return decision


def _contains_code(text: str) -> bool:
    return "```" in text or bool(observer_module._CODE_LIKE.search(text))


def fallback_hint(question: CodingQuestion, level: int) -> str:
    """Deterministic hint when no curated hint exists and the model is down."""
    concepts = [c for c in question.expected_concepts or [] if c]
    complexity = (question.optimal_complexity or {}).get("time")
    if level == 1:
        return "Start from a simple, correct approach on the examples, then look for work you repeat that you could avoid."
    if level == 2:
        if concepts:
            return f"Think about {concepts[0]}." + (f" Aim for {complexity} time." if complexity else "")
        return (
            "Consider which data structure would let you look up what you have already seen in constant time."
        )
    if concepts:
        listed = "; ".join(concepts[:3])
        return f"Outline the steps around these ideas: {listed}. Then handle the edge cases before running the tests."
    return "Write the steps in plain language first: process the input once, keep the state you need, and return as soon as you have the answer."


# ----------------------------------------------------------------- registry

_service: CodingServiceImpl | None = None


def get_coding_service() -> CodingServiceImpl:
    """Process-wide coding service (used by the engine and realtime runtime)."""
    global _service
    if _service is None:
        _service = CodingServiceImpl()
    return _service


def set_coding_service(service: CodingServiceImpl | None) -> CodingServiceImpl | None:
    global _service
    previous = _service
    _service = service
    return previous
