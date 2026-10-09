"""``IntakeConductor``: the intake mode of ``InterviewEngine`` (design contract sections 6 and 7.3-7.8).

The engine calls the conductor from its hook points (all guarded by ``state.intake is not None``) while it holds
the session lock; the conductor turns ``stages.next_move()`` decisions into utterances, live model calls
(``IntakeTurn``, speech first), activity events and persistence:

* every committed utterance = ``questions`` row (``segment = "intake:<stage>"``, ``decision.intake`` meta 15.9)
  + interviewer ``transcripts`` row + ``intake_stage_results`` upsert + durable state save, one transaction;
* candidate answers = ``answers`` + candidate ``transcripts`` rows (+ answer audio when consented), never
  ``answer.evaluate`` (no per-answer evaluation for intake);
* activities: the activity row (``intake_activity_results``) is armed with its instruction utterance and
  ``ActivityStarted``; the runtime owns phase deadlines; captures arrive as ``CandidateTurn.activity``.

Fairness and privacy: the live model only phrases model items inside offered moves; MQ1 answers never enter the
state or any prompt; logs carry ids and counts only.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.ai.base import AIError, CallContext
from app.core import metrics
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.queue import enqueue
from app.db.models import IntakeOrgSettings
from app.db.session import session_scope
from app.domain.enums import (
    Difficulty,
    ModelTask,
    QuestionKind,
    SegmentKind,
    SessionEndReason,
    TurnAction,
    TurnState,
)
from app.schemas.ai import InterviewPlan, PlanSegment
from app.schemas.intake import IntakeQuestionMeta
from app.services.intake import activities as acts
from app.services.intake import capture_store, selection
from app.services.intake import prompts as P
from app.services.intake import stages as S
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.session_init import apply_row, effective_profile, intake_row
from app.services.intake.state import IntakeActivityState, IntakeState
from app.services.intake.turn_schema import (
    INTAKE_TURN_SCHEMA,
    IntakeTurn,
    VerificationGrade,
    recover_turn,
    synthesize_turn,
)
from app.services.interview import behavior as beh
from app.services.interview import understanding as U
from app.services.interview.attribution import guard_for_turn
from app.services.interview.contracts import (
    ActivityEnded,
    ActivityEvent,
    ActivityPhaseChanged,
    ActivityStarted,
    CandidateTurn,
    SpeechDelta,
    StateChanged,
    TurnEvent,
    UtteranceBegin,
    UtteranceKind,
    UtteranceMeta,
)
from app.services.interview.policy import speech_issues
from app.services.interview.prompts import fence
from app.services.interview.records import (
    add_answer,
    add_question,
    add_transcript,
    log_event,
    store_answer_audio,
)
from app.services.interview.special import SpecialResult, classify, hedge_kind
from app.services.interview.state import InterviewState, QuestionRecord, UtteranceRecord
from app.services.interview.transitions import add_utterance, record_answer, record_interviewer_text
from app.services.interview.turns import TurnOutcome, question_core, stream_speech_object

if TYPE_CHECKING:
    from app.services.interview.engine import InterviewEngine

log = get_logger(__name__)

SEGMENT_PREFIX = "intake:"
GRADE_BUDGET_S = 4.0
CLAIM_SPOKEN_MAX = 160
INTAKE_EVALUATE_JOB = "intake.evaluate"
_QUESTION_LIKE = re.compile(
    r"\b(?:i(?:'d| would) like to (?:know|ask|understand|hear)|i(?:'m| am) (?:curious|wondering)|i wonder|"
    r"(?:could|can) you (?:tell|explain|say)|tell me (?:more )?about|what about|how about|is there|are there|"
    r"how (?:long|many|much|does|do|will)|what (?:is|are|does|do|will))\b",
    re.IGNORECASE,
)
STAGE_SEGMENT_KINDS: dict[str, SegmentKind] = {
    "greeting": SegmentKind.INTRO,
    "resume": SegmentKind.RESUME_VALIDATION,
    "mandatory": SegmentKind.RECRUITER,
    "reading": SegmentKind.TECHNICAL,
    "topic": SegmentKind.BEHAVIORAL,
    "learning": SegmentKind.BEHAVIORAL,
    "close": SegmentKind.CANDIDATE_QUESTIONS,
}


def _question_like(text: str) -> bool:
    return bool(_QUESTION_LIKE.search(text or ""))


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _spoken_claim(text: str) -> str:
    text = " ".join(str(text or "").split()).rstrip(".")
    if len(text) <= CLAIM_SPOKEN_MAX:
        return text
    cut = text[:CLAIM_SPOKEN_MAX].rsplit(" ", 1)[0]
    return cut.rstrip(",;:")


class IntakeConductor:
    """One per ``InterviewEngine`` (``engine.intake``). Stateless across sessions apart from background grades."""

    def __init__(self, engine: InterviewEngine) -> None:
        self.engine = engine
        self._grades: dict[str, tuple[float, asyncio.Task[Any]]] = {}
        self._stage_changed: set[str] = set()

    def _stage_event(self, state: InterviewState) -> StateChanged | None:
        """``StateChanged("intake_stage")`` once after a commit that entered a new stage (7.10)."""
        if state.session_id in self._stage_changed:
            self._stage_changed.discard(state.session_id)
            return StateChanged(reason="intake_stage")
        return None

    # ============================================================== plumbing
    @property
    def kit(self) -> IntakeKit:
        return load_intake_kit()

    def _now(self, state: InterviewState) -> float:
        return self.engine._elapsed(state)

    @staticmethod
    def _ik(state: InterviewState) -> IntakeState:
        assert state.intake is not None
        return state.intake

    # ============================================================== prepare
    async def prepare(
        self, state: InterviewState, profile_doc: dict[str, Any] | None
    ) -> tuple[IntakeState, InterviewPlan, str]:
        """``session.prepare`` for intake: claims, ladder items, stage rows (pending), plan mirror, opening."""
        ik = self._ik(state).model_copy(deep=True)
        async with session_scope() as db:
            row = await intake_row(db, state.interview_id)
            org = await db.get(IntakeOrgSettings, uuid.UUID(state.organization_id))
            if row is not None:
                apply_row(ik, row, org=org)
            profile = effective_profile(profile_doc or state.candidate_profile, row)
            claim_limits = ik.limits.get("claims") or {}
            ik.claims = selection.select_claims(
                profile,
                min_claims=int(claim_limits.get("min_claims", 2)),
                max_claims=int(claim_limits.get("max_claims", 3)),
            )
            resume = ik.stage_by_key("resume")
            if resume is not None and resume.status == "pending":
                S.set_ladder(resume, ik.claims, clarify_per_rung=ik.limit("resume_clarify_per_rung", 1))
            pm = ik.stage_by_key("mandatory")
            if pm is not None and pm.status == "pending" and S.is_pm_role(ik.target_role):
                if pm.item("mandatory.mq2.pm") is None:
                    S.ensure_item(ik, "mandatory.mq2.pm", after="mandatory.mq2")
            ik.prepared = True
            await capture_store.upsert_stage_rows(
                db,
                organization_id=state.organization_id,
                intake_id=ik.intake_id,
                session_id=state.session_id,
                stages=ik.stages,
                extra={
                    "resume": {"claims": ik.claims},
                    "mandatory": {
                        "language": ik.language,
                        "language_source": "form" if ik.language else None,
                    },
                },
            )
        minutes = round(ik.time_limit_s / 60)
        opening = P.opening_text(
            first_name=ik.first_name or state.candidate_first_name,
            interviewer=state.interviewer_name,
            program_name=ik.program_name,
            minutes=minutes,
        )
        return ik, self.plan(ik), opening

    def plan(self, ik: IntakeState) -> InterviewPlan:
        """An ``InterviewPlan`` mirroring the seven stages (for the session detail page)."""
        segments = [
            PlanSegment(
                key=stage.key,
                kind=STAGE_SEGMENT_KINDS[stage.key],
                competency_keys=list(self.kit.stage(stage.key).competencies)[:10],
                time_budget_minutes=max(0.5, round(stage.budget_s / 60.0, 2)),
                goals=[S.CANDIDATE_TITLES[stage.key]],
            )
            for stage in ik.stages
        ]
        return InterviewPlan(
            opening_focus="Seven-stage intake interview (deterministic plan)",
            segments=segments,
            claims_priority=[],
            difficulty_strategy="Intake mode: material level from the stage 3 answers; never a score.",
        )

    async def ensure_prepared(self, state: InterviewState) -> None:
        """Start without a finished ``session.prepare``: run the deterministic preparation inline."""
        ik = self._ik(state)
        if ik.prepared and state.opening_text:
            return
        prepared, plan, opening = await self.prepare(state, state.candidate_profile)
        state.intake = prepared
        state.plan = state.plan or plan
        state.plan_source = state.plan_source or "intake"
        if not state.opening_text:
            state.opening_text = opening

    # ================================================================ start
    async def first_item(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        move = S.next_move(ik, None, self._now(state))
        async with aclosing(self._execute(state, move)) as relay:
            async for event in relay:
                yield event

    # ======================================================= candidate turns
    def signal(
        self,
        state: InterviewState,
        text: str,
        turn: CandidateTurn | None = None,
        *,
        special: SpecialResult | None = None,
    ) -> S.AnswerSignal:
        ik = self._ik(state)
        stage = ik.stage.key if ik.stage else ""
        if special is None:
            special = classify(text, awaiting_answer=True, questions_invited=stage == "close")
        language = selection.language_in_text(self.kit, text)
        return S.AnswerSignal(
            kind=special.kind.value,
            words=S.words(text),
            hedge=hedge_kind(text),
            skip_request=S.wants_skip(text),
            typed=bool(turn.typed) if turn is not None else False,
            mentions_pm=S.mentions_pm(text),
            language=language.key if language else None,
            question_like=_question_like(text),
        )

    def understand(
        self, state: InterviewState, text: str, turn: CandidateTurn | None = None
    ) -> U.Understanding:
        """What the turn IS (``understanding.py``) before the pending item may be consumed."""
        ik = self._ik(state)
        stage = ik.stage.key if ik.stage else ""
        current = state.current_question()
        spec = self._spec(ik.current_item)
        return U.understand(
            text,
            question_text=current.text if current is not None else None,
            awaiting_answer=True,
            questions_invited=stage == "close",
            accepts_brief=bool(spec is not None and spec.accepts_brief),
            stt_confidence=turn.stt_confidence if turn is not None and not turn.typed else None,
        )

    @staticmethod
    def _spec(key: str | None) -> S.ItemSpec | None:
        if not key:
            return None
        try:
            return S.item_spec(key)
        except KeyError:
            return None

    def _flow_turn(self, state: InterviewState, heard: U.Understanding) -> bool:
        """Answer the turn with a flow reply and keep the pending item (audio checks, questions about the
        interview, "okay", "let me think", fragments, interruptions). Job/company questions keep the deferral to
        the last part; "what are the next steps?" gets the approved closing wording before stage 7."""
        ik = self._ik(state)
        item = ik.current()
        if item is None or item.status != "asked" or ik.stage is None:
            return False
        if not ik.time_up and self._now(state) >= ik.time_limit_s:
            return False  # the time-up close comes first (next_move)
        if heard.intent is U.TurnIntent.CANDIDATE_QUESTION:
            return heard.topic == "next_steps" and ik.stage.key != "close"
        return U.flow_allowed(state, heard)

    async def _flow_reply(
        self, state: InterviewState, heard: U.Understanding
    ) -> AsyncGenerator[TurnEvent, None]:
        """A deterministic, human reply that leaves the pending item pending (no answer row, no new question)."""
        ik = self._ik(state)
        key = ik.current_item
        spec = self._spec(key)
        current = state.current_question()
        streak = U.note_flow(state, heard.intent)
        text = U.flow_reply(
            heard,
            question_text=current.text if current is not None else None,
            reinvite=P.REINVITE.get(key or ""),
            streak=streak,
            seed=f"{state.session_id}:{state.turn_index}:{len(state.utterances)}",
            remaining_s=max(0.0, ik.time_limit_s - self._now(state)),
            soft_limit_s=spec.soft_limit_s if spec is not None else None,
        )
        if heard.intent is U.TurnIntent.THINKING:
            text = P.WAIT
        state.silence_prompts = 0
        log.info(
            "intake.flow_turn", session_id=state.session_id, intent=heard.intent.value, topic=heard.topic,
            item_key=key, streak=streak,
        )  # fmt: skip
        async for event in self._say(
            state, text, item_key=key, item_kind="prompt", kind="acknowledgment", action=U.flow_action(heard),
            creates_question=False, flow=heard.intent.value,
        ):  # fmt: skip
            yield event

    def _unclear_text(self, state: InterviewState, key: str, reason: str) -> str:
        """The deterministic clarification after a very short or unclear answer: a lead and the question again
        (a rephrased template for template items), never the model's guess at what the candidate meant."""
        lead = P.UNCLEAR_GARBLED_LEAD if reason in ("garbled", "empty") else P.UNCLEAR_BRIEF_LEAD
        body: str | None = None
        rephrase = P.CLARIFY.get(key)
        if rephrase and "{core}" not in rephrase:
            body = P.strip_ack(rephrase)
        if body is None:
            body = P.REINVITE.get(key)
        if body is None:
            current = state.current_question()
            body = U.short_reinvite(current.text if current is not None else None)
        return P.join_speech(lead, body)

    async def candidate_turn(
        self, state: InterviewState, turn: CandidateTurn, text: str, *, replay: bool
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        if turn.activity is not None:
            async with aclosing(self._capture_turn(state, turn, text, replay=replay)) as relay:
                async for event in relay:
                    yield event
            return
        activity = ik.activity
        if activity is not None and activity.status in ("armed", "running"):
            # The candidate spoke while an activity instruction was playing (or typed during a non-capture
            # phase): record it, then explain again and re-arm (7.3 step 5). "Can you hear me?" is answered first;
            # flow turns stay in the transcript only (never an answer of the activity item).
            heard = self.understand(state, text, turn) if text else None
            if not replay and text:
                if heard is not None and heard.flow:
                    await self.engine._record_candidate_utterance(state, turn, text)
                    state.last_turn_key = self._turn_key(turn, text)
                else:
                    await self._persist_answer(state, turn, text, item_key=ik.current_item)
            if activity.status == "running":
                await self._recover_running(state)  # the runtime lost the running phase (a failed job)
            lead = P.REEXPLAIN_LEAD
            if heard is not None and heard.intent is U.TurnIntent.AUDIO_CHECK:
                lead = P.AUDIO_REEXPLAIN_LEAD
            async with aclosing(self._rearm(state, lead=lead)) as relay:
                async for event in relay:
                    yield event
            return
        if ik.closed:
            return
        heard = self.understand(state, text, turn)
        signal = self.signal(state, text, turn, special=heard.special)
        if signal.kind == "repeat":
            if not replay:
                await self.engine._record_candidate_utterance(state, turn, text)
            async with aclosing(self.engine._repeat_events(state)) as relay:
                async for event in relay:
                    yield event
            return
        if self._flow_turn(state, heard):
            # Not an answer: the candidate's words stay in the transcript, the item stays pending.
            if not replay and text:
                await self.engine._record_candidate_utterance(state, turn, text)
                state.last_turn_key = self._turn_key(turn, text)
            async with aclosing(self._flow_reply(state, heard)) as relay:
                async for event in relay:
                    yield event
            return
        current = ik.current_item
        if not replay and text:
            await self._persist_answer(state, turn, text, item_key=current)
        self._answer_facts(state, current, text, signal)
        move = S.next_move(ik, signal, self._now(state))
        # An answer that opened with an audio check: "Yes, I can hear you clearly." before the next item.
        ack_lead = heard.ack if move.action == "ask" else None
        async with aclosing(self._execute(state, move, last_answer=text, lead=ack_lead)) as relay:
            async for event in relay:
                yield event

    async def paused_turn(
        self, state: InterviewState, turn: CandidateTurn, text: str, *, replay: bool
    ) -> None:
        """A turn that finished transcribing after a disconnect: keep it; the resume continues from it."""
        ik = self._ik(state)
        if replay:
            return
        if turn.activity is not None:
            async with aclosing(self._capture_turn(state, turn, text, replay=False, paused=True)) as relay:
                async for _event in relay:
                    pass
            return
        if not text:
            return
        heard = self.understand(state, text, turn)
        if heard.flow or heard.intent is U.TurnIntent.REPEAT:
            # "Can you hear me?" transcribed after a disconnect: the resume re-asks the item anyway.
            await self.engine._record_candidate_utterance(state, turn, text)
            state.last_turn_key = self._turn_key(turn, text)
            await self.engine._save(state, durable=True)
            return
        current = ik.current_item
        await self._persist_answer(state, turn, text, item_key=current)
        signal = self.signal(state, text, turn, special=heard.special)
        self._answer_facts(state, current, text, signal)
        ik.pending_candidate_turn = True
        ik.pending_signal = {
            "kind": signal.kind,
            "words": signal.words,
            "hedge": signal.hedge,
            "skip_request": signal.skip_request,
            "typed": signal.typed,
            "mentions_pm": signal.mentions_pm,
            "language": signal.language,
            "question_like": signal.question_like,
        }
        await self.engine._save(state, durable=True)

    def _answer_facts(
        self, state: InterviewState, item_key: str | None, text: str, signal: S.AnswerSignal
    ) -> None:
        """Facts the machine needs from specific answers (stated values, never adjusted)."""
        ik = self._ik(state)
        if not item_key:
            return
        if item_key in ("mandatory.mq3", "mandatory.mq3.rating_probe") and ik.self_rating is None:
            ik.self_rating = S.parse_self_rating(text)
        if (
            item_key in ("mandatory.mq3", "mandatory.mq3.language")
            and ik.language is None
            and signal.language
        ):
            ik.language = signal.language
        if item_key in ("mandatory.mq4", "mandatory.mq4.followup") and ik.years_hands_on is None:
            ik.years_hands_on = S.parse_years(text)
        if item_key in ("mandatory.mq3.verification", "mandatory.mq3.verification_retry"):
            if signal.kind in ("dont_know", "brief", "empty") or signal.words < 6:
                if not ik.verification:
                    ik.verification = {"quality": "no_answer", "matched_key_points": [], "source": "rule"}
            else:
                self._spawn_grade(state, text, retry=item_key.endswith("retry"))

    async def _persist_answer(
        self, state: InterviewState, turn: CandidateTurn, text: str, *, item_key: str | None
    ) -> tuple[uuid.UUID, str | None]:
        """``answers`` + candidate ``transcripts`` rows (+ audio when consented). Returns (answer id, media id)."""
        ik = self._ik(state)
        answer_id = uuid.uuid4()
        question_id = state.current_question_id
        record_answer(
            state,
            answer_id=str(answer_id),
            text=text,
            typed=turn.typed,
            started_s=turn.started_ms / 1000.0,
            ended_s=turn.ended_ms / 1000.0,
            interrupted_ai=turn.interrupted_ai,
            special=None,
            quality=None,
            quality_source=None,
        )
        state.last_turn_key = self._turn_key(turn, text)
        found = ik.find_item(item_key) if item_key else None
        if found is not None:
            found[1].answer_ids.append(str(answer_id))
        media_id: str | None = None
        async with session_scope() as db:
            await add_answer(
                db,
                state.session_id,
                answer_id=answer_id,
                question_id=question_id,
                text_value=text.strip(),
                typed=turn.typed,
                started_ms=turn.started_ms,
                ended_ms=turn.ended_ms,
                stt_confidence=turn.stt_confidence,
                interrupted_ai=turn.interrupted_ai,
                quick_assessment={"intake": True},
            )
            await add_transcript(
                db,
                state.session_id,
                speaker="candidate",
                text_value=text.strip() or "(no speech)",
                start_ms=turn.started_ms,
                end_ms=turn.ended_ms,
                question_id=question_id,
                answer_id=answer_id,
                confidence=turn.stt_confidence,
            )
            if turn.audio_wav and state.audio_recording_allowed:
                media_id = await store_answer_audio(
                    db,
                    organization_id=state.organization_id,
                    session_id=state.session_id,
                    candidate_id=state.candidate_id,
                    answer_id=answer_id,
                    wav=turn.audio_wav,
                    retention_days=state.audio_retention_days,
                )
            await log_event(
                db,
                state.session_id,
                "answer.received",
                {
                    "answer_id": str(answer_id),
                    "question_id": question_id,
                    "words": len(text.split()),
                    "typed": turn.typed,
                    "item_key": item_key,
                    "activity": turn.activity.kind if turn.activity is not None else None,
                    "evaluate": False,
                },
                elapsed_s=turn.ended_ms / 1000.0,
            )
            await self.engine._save(state, db=db)
        return answer_id, media_id

    @staticmethod
    def _turn_key(turn: CandidateTurn, text: str) -> str:
        from app.services.interview.engine import turn_key

        return turn_key(turn, text)

    # ============================================================ execution
    async def _execute(  # noqa: PLR0912
        self, state: InterviewState, move: S.Move, *, last_answer: str | None = None, lead: str | None = None
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        now = self._now(state)
        action = move.action
        current = state.current_question()
        core = question_core(current.text) if current is not None else ""
        if action == "none":
            return
        if action == "repeat":
            async with aclosing(self.engine._repeat_events(state)) as relay:
                async for event in relay:
                    yield event
            return
        if action == "wait":
            async for event in self._say(
                state, P.WAIT, item_key=move.item_key, item_kind="prompt", kind="acknowledgment",
                action=TurnAction.ACKNOWLEDGE_AND_WAIT, creates_question=False,
            ):  # fmt: skip
                yield event
            return
        if action in ("redirect", "defer_question", "encourage", "dont_know_probe"):
            item = ik.current()
            if action == "encourage" and item is not None:
                item.encouraged = True
            if action == "dont_know_probe":
                S.apply_clarification(ik, move.item_key or "")
            text = {
                "redirect": P.join_speech(P.REDIRECT_LEAD, core),
                "defer_question": P.join_speech(P.DEFER_LEAD, core),
                "encourage": P.join_speech(P.ENCOURAGE_LEAD, core),
                "dont_know_probe": P.DONT_KNOW_PROBE,
            }[action]
            kind_map = {
                "redirect": ("redirect", TurnAction.REDIRECT),
                "defer_question": ("answer_question", TurnAction.ANSWER_CANDIDATE_QUESTION),
                "encourage": ("clarify", TurnAction.CLARIFY_QUESTION),
                "dont_know_probe": ("clarify", TurnAction.CLARIFY_QUESTION),
            }
            item_kind, turn_action = kind_map[action]
            async for event in self._say(
                state, text, item_key=move.item_key, item_kind=item_kind, kind="followup", action=turn_action
            ):
                yield event
            return
        if action == "clarify":
            key = move.item_key or ""
            S.apply_clarification(ik, key)
            if move.reason in ("brief", "garbled", "backchannel", "empty"):
                async for event in self._say(
                    state, self._unclear_text(state, key, move.reason), item_key=key, item_kind="clarify",
                    kind="followup", action=TurnAction.CLARIFY_QUESTION,
                ):  # fmt: skip
                    yield event
                return
            spec = S.item_spec(key) if key else None
            if spec is not None and spec.kind == "M":
                async with aclosing(
                    self._model_utterance(
                        state, move, last_answer=last_answer, offers=[("clarify", key, P.ITEM_INTENTS["clarify"])],
                        fallback=P.MODEL_CLARIFY_FALLBACK.format(core=core), meta_key=key, item_kind="clarify",
                    )
                ) as relay:  # fmt: skip
                    async for event in relay:
                        yield event
                return
            template = P.CLARIFY.get(key, P.MODEL_CLARIFY_FALLBACK)
            async for event in self._say(
                state, template.format(core=core), item_key=key, item_kind="clarify", kind="followup",
                action=TurnAction.CLARIFY_QUESTION,
            ):  # fmt: skip
                yield event
            return
        if action == "time_up":
            S.apply_resolution(ik, move.resolve, now)
            S.apply_time_up(ik, now)
            S.mark_asked(ik, "close.questions", now)
            found = ik.find_item("close.questions")
            if found is not None:
                found[1].variant = "time_up"
            async for event in self._say(
                state, P.TIME_UP_QUESTION, item_key="close.questions", item_kind="prompt", kind="question",
                action=TurnAction.INVITE_CANDIDATE_QUESTIONS, all_stages=True,
            ):  # fmt: skip
                yield event
            return
        if action == "close":
            S.apply_resolution(ik, move.resolve, now)
            S.apply_skips(ik, move.skips, now)
            if move.variant == "hiring_team":
                ik.closing_lead = P.HIRING_TEAM_ANSWER
            reason = SessionEndReason.TIME_LIMIT if ik.time_up else SessionEndReason.SUFFICIENT_EVIDENCE
            async with aclosing(
                self.engine._closing_events(state, reason, last_answer=last_answer, generated=False)
            ) as relay:
                async for event in relay:
                    yield event
            return
        # ask
        assert action == "ask" and move.item_key is not None
        S.apply_resolution(ik, move.resolve, now)
        spec = S.item_spec(move.item_key)
        if spec.kind == "A" and not move.offered:
            async with aclosing(self._arm_activity(state, move, lead=lead)) as relay:
                async for event in relay:
                    yield event
            return
        if spec.kind == "M" or "follow_up" in move.offered or "answer_question" in move.offered:
            async with aclosing(self._ask_model(state, move, last_answer=last_answer, lead=lead)) as relay:
                async for event in relay:
                    yield event
            return
        async with aclosing(self._ask_template(state, move, lead=lead)) as relay:
            async for event in relay:
                yield event

    # ------------------------------------------------------------ templates
    def _profile(self, state: InterviewState) -> dict[str, Any]:
        return dict(state.candidate_profile or {})

    def _language(self, state: InterviewState):  # noqa: ANN202 - LanguageCheck
        ik = self._ik(state)
        kit = self.kit
        found = kit.language_check(ik.language) if ik.language else None
        if found is None:
            found = selection.fallback_language(kit, self._profile(state))
            ik.language = found.key
            ik.language_fallback = True
        return found

    def _claim(self, ik: IntakeState, ref: str | None) -> dict[str, Any] | None:
        return next((c for c in ik.claims if c.get("ref") == ref), None) if ref else None

    def item_text(self, state: InterviewState, key: str, variant: str | None = None) -> str:  # noqa: PLR0911
        """The template text of an item (also the fallback of model items)."""
        ik = self._ik(state)
        kit = self.kit
        digest = ik.onboarding_digest or {}
        if key == "mandatory.mq2":
            return P.mq2_text(
                kit,
                variant=variant or S.mq2_variant(ik),
                target_role=ik.target_role,
                target_role_other=digest.get("target_role_other"),
            )
        if key == "mandatory.mq3":
            language = kit.language_check(ik.language) if ik.language else None
            return P.mq3_text(
                kit,
                variant=variant or S.mq3_variant(ik),
                language_label=(
                    language.label.split(" /")[0] if language else digest.get("primary_language")
                ),
                self_rating=ik.self_rating,
                scripting=digest.get("scripting"),
            )
        if key in ("mandatory.mq3.verification", "mandatory.mq3.verification_retry"):
            return P.verification_text(self._language(state), retry=key.endswith("retry"))
        if key == "mandatory.mq3.rating_probe":
            return P.rating_probe_text(
                variant=variant or S.rating_probe_variant(ik), self_rating=ik.self_rating
            )
        if key == "mandatory.mq4":
            return P.mq4_text(kit, years=ik.years_hands_on)
        if key == "mandatory.mq4.followup":
            return P.mq4_followup_text(kit, variant=variant or S.mq4_followup_variant(ik))
        if key in ("topic.scenario", "learning.scenario"):
            scenario = ik.scenario_keys.get(key)
            if scenario is None:
                self._select_scenarios(state)
                scenario = ik.scenario_keys.get(key) or ("B1" if key == "topic.scenario" else "C1")
            return P.scenario_text(kit, scenario)
        if key == "close.questions" and (variant == "time_up" or ik.time_up):
            return P.TIME_UP_QUESTION
        if key == "learning.three_weeks":
            return P.three_weeks_text(self._field(state))
        if key == "close.answer":
            return P.join_speech(P.HIRING_TEAM_ANSWER, P.CLOSE_ANSWER_INVITE)
        if S.is_ladder(key):
            return self._ladder_text(ik, key)
        template = P.item_template(key)
        if template is None:
            raise KeyError(f"no template for {key}")
        return template

    def _field(self, state: InterviewState) -> str | None:
        profile = self._profile(state)
        roles = [r for r in profile.get("roles") or [] if isinstance(r, dict)]
        if roles and roles[0].get("title"):
            return str(roles[0]["title"])[:80]
        if profile.get("headline"):
            return str(profile["headline"])[:80]
        return None

    def _ladder_text(self, ik: IntakeState, key: str) -> str:
        spec = S.item_spec(key)
        claim = self._claim(ik, spec.claim_ref) or {}
        probe = claim.get("probe")
        if spec.rung == "ownership":
            if probe == "thin_or_vague":
                return (
                    "Tell me about the most technical thing you've ever done, in detail — even a course or "
                    "personal project."
                )
            return (
                f"Let's look at this from your résumé: {_spoken_claim(claim.get('text') or '')}. What did you "
                "personally build or do there, versus the team?"
            )
        if spec.rung == "mechanics":
            if (
                ik.hedges.get(spec.claim_ref or "")
                or ik.claim_signals.get(spec.claim_ref or "") == "team_level"
            ):
                return "What did you see or learn about how it actually worked?"
            return "Walk me through how it actually worked, or how you measured the result."
        return "What would break if the data doubled, a key assumption changed, or you had to redo it today?"

    async def _ask_template(
        self, state: InterviewState, move: S.Move, *, lead: str | None = None
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        now = self._now(state)
        ended = ik.stage.key if ik.stage and move.stage_to and ik.stage.key != move.stage_to else None
        item = S.apply_ask(ik, move, now)
        text = P.join_speech(
            lead, P.transition_prefix(move.stage_to), self.item_text(state, item.item_key, item.variant)
        )
        spec = S.item_spec(item.item_key)
        action = (
            TurnAction.INVITE_CANDIDATE_QUESTIONS
            if item.item_key == "close.questions"
            else TurnAction.ASK_QUESTION
        )
        async for event in self._say(
            state, text, item_key=item.item_key, item_kind=spec.item_kind if spec.item_kind != "activity" else "prompt",
            kind="question", action=action, ended_stage=ended,
        ):  # fmt: skip
            yield event

    # ---------------------------------------------------------------- model
    async def _ask_model(
        self, state: InterviewState, move: S.Move, *, last_answer: str | None, lead: str | None = None
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        offers: list[tuple[str, str, str]] = []
        notes: list[str] = []
        key = move.item_key or ""
        followup_key = move.followup_key or key
        if "follow_up" in move.offered:
            target = move.followup_item or key
            intent = (
                P.ITEM_INTENTS["clarify_probe"] if S.is_ladder(target) else P.ITEM_INTENTS.get(target, "")
            )
            offers.append(("follow_up", followup_key, f"one follow-up question. {intent}"))
        if "ask_item" in move.offered:
            offers.append(("ask_item", key, self._intent(ik, key)))
            if move.stage_to and "follow_up" in move.offered:
                notes.append(
                    f'If you choose ask_item, start with this bridge to the next part: "{P.transition_prefix(move.stage_to)}"'
                )
        if "answer_question" in move.offered:
            offers.append(("answer_question", "close.answer", P.ITEM_INTENTS["close.answer"]))
        prefix = None
        if move.stage_to and "follow_up" not in move.offered:
            prefix = P.transition_prefix(move.stage_to)
        if lead:
            prefix = P.join_speech(lead, prefix)
        if prefix:
            notes.append(f'Your speech follows this already-spoken sentence: "{prefix}". Do not repeat it.')
        meta_key = key
        item_kind = S.item_spec(key).item_kind if key and not S.is_ladder(key) else "ladder"
        if "follow_up" in move.offered and "ask_item" not in move.offered:
            meta_key = followup_key
            item_kind = "followup"
        if "answer_question" in move.offered:
            meta_key, item_kind = "close.answer", "answer_question"
        fallback_key = move.item_key if "ask_item" in move.offered else (move.followup_item or key)
        try:
            fallback = self.item_text(state, fallback_key or key)
        except KeyError:
            fallback = "Could you tell me a little more about that?"
        if "ask_item" in move.offered and move.stage_to and prefix is None:
            # The model would have spoken the bridge itself (follow-up offered); the template must too.
            fallback = P.join_speech(P.transition_prefix(move.stage_to), fallback)
        if "answer_question" in move.offered and not ik.approved_info:
            # Nothing approved to answer from: the deterministic answer, no model call.
            S.apply_resolution(ik, move.resolve, self._now(state))
            S.apply_followup(ik, move, self._now(state))
            async for event in self._say(
                state, P.join_speech(P.HIRING_TEAM_ANSWER, P.CLOSE_ANSWER_INVITE), item_key="close.answer",
                item_kind="answer_question", kind="acknowledgment", action=TurnAction.ANSWER_CANDIDATE_QUESTION,
                followup_index=self._followups(ik, "close.answer"),
            ):  # fmt: skip
                yield event
            return
        async with aclosing(
            self._model_utterance(
                state, move, last_answer=last_answer, offers=offers, notes=notes, fallback=fallback,
                meta_key=meta_key, item_kind=item_kind, prefix=prefix,
            )
        ) as relay:  # fmt: skip
            async for event in relay:
                yield event

    def _intent(self, ik: IntakeState, key: str) -> str:
        if S.is_ladder(key):
            spec = S.item_spec(key)
            claim = self._claim(ik, spec.claim_ref) or {}
            if spec.rung == "ownership" and claim.get("probe") == "thin_or_vague":
                return P.ITEM_INTENTS["thin_or_vague"]
            if spec.rung == "mechanics" and (
                ik.hedges.get(spec.claim_ref or "")
                or ik.claim_signals.get(spec.claim_ref or "") == "team_level"
            ):
                return P.ITEM_INTENTS["mechanics_observed"]
            return P.ITEM_INTENTS[spec.rung or "ownership"]
        return P.ITEM_INTENTS.get(key, S.item_spec(key).title if key else "")

    @staticmethod
    def _followups(ik: IntakeState, key: str) -> int:
        found = ik.find_item(key)
        return found[1].followups_used if found else 0

    async def _model_utterance(  # noqa: PLR0912, PLR0915
        self,
        state: InterviewState,
        move: S.Move,
        *,
        last_answer: str | None,
        offers: list[tuple[str, str, str]],
        fallback: str,
        meta_key: str,
        item_kind: str,
        notes: list[str] | None = None,
        prefix: str | None = None,
    ) -> AsyncGenerator[TurnEvent, None]:
        """One streamed ``IntakeTurn``: UtteranceBegin first, the (template) prefix, the model speech deltas, then
        the commit (question row, state) and UtteranceMeta. Barge-in commits what was spoken."""
        ik = self._ik(state)
        engine = self.engine
        utterance_id = str(uuid.uuid4())
        action = TurnAction.ANSWER_CANDIDATE_QUESTION if "answer_question" in move.offered else (
            TurnAction.ASK_FOLLOWUP if item_kind in ("followup", "clarify") else TurnAction.ASK_QUESTION
        )  # fmt: skip
        behavior = beh.provisional(action)
        state.avatar_state = behavior.state
        kind: UtteranceKind = "followup" if action == TurnAction.ASK_FOLLOWUP else (
            "acknowledgment" if action == TurnAction.ANSWER_CANDIDATE_QUESTION else "question"
        )  # fmt: skip
        yield UtteranceBegin(utterance_id=utterance_id, kind=kind, behavior=behavior)
        if prefix:
            yield SpeechDelta(utterance_id=utterance_id, text=prefix + " ")
        messages = self._messages(state, move, offers=offers, notes=notes or [], last_answer=last_answer)
        outcome = TurnOutcome()
        spoken: list[str] = []
        completed = False
        use_template = ik.template_next
        default_move = offers[-1][0] if offers else "ask_item"
        try:
            if not use_template:
                async with aclosing(
                    stream_speech_object(
                        engine.ai,
                        messages,
                        schema=IntakeTurn,
                        json_schema=INTAKE_TURN_SCHEMA,
                        context=CallContext(
                            task=ModelTask.TURN_DECISION,
                            session_id=state.session_id,
                            organization_id=state.organization_id,
                            prompt_version=P.INTAKE_TURN_PROMPT_VERSION,
                        ),
                        utterance_id=utterance_id,
                        outcome=outcome,
                        config=engine.config,
                        guard_factory=lambda allow, before: guard_for_turn(
                            state, probe=False, allow_regenerate=allow, released_before=before
                        ),
                    )
                ) as gen:
                    async for delta in gen:
                        spoken.append(delta.text)
                        yield delta
            completed = True
        finally:
            if not completed:
                text = P.join_speech(prefix, "".join(spoken))
                if "".join(spoken).strip():
                    turn = recover_turn(
                        outcome.raw_partial, "".join(spoken), move=default_move, item_key=meta_key,
                        reason="interrupted before the turn completed",
                    )  # fmt: skip
                    try:
                        await self._finish_model(
                            state, move, turn, utterance_id=utterance_id, text=text, meta_key=meta_key,
                            item_kind=item_kind, action=action, kind=kind, deviations=["interrupted_before_complete"],
                            outcome=outcome,
                        )  # fmt: skip
                    except Exception as exc:  # noqa: BLE001 - never mask the cancellation
                        log.error(
                            "intake.interrupted_commit_failed", error=f"{type(exc).__name__}: {exc}"[:200]
                        )
        deviations: list[str] = []
        speech = "".join(spoken).strip()
        if isinstance(outcome.extra, IntakeTurn):
            turn = outcome.extra
            speech = speech or turn.speech
        elif speech:
            turn = recover_turn(outcome.raw_partial, speech, move=default_move, item_key=meta_key,
                                reason="recovered from a partial object")  # fmt: skip
            deviations.append("tail_cut" if outcome.tail_cut else "turn_recovered")
        else:
            speech = fallback
            turn = synthesize_turn(fallback, move=default_move, item_key=meta_key,
                                   reason="template" if use_template else "fallback: model unavailable")  # fmt: skip
            deviations.append("template_after_issue" if use_template else "fallback")
            state.fallbacks_used += 0 if use_template else 1
            yield SpeechDelta(utterance_id=utterance_id, text=fallback)
        text = P.join_speech(prefix, speech)
        meta = await self._finish_model(
            state, move, turn, utterance_id=utterance_id, text=text, meta_key=meta_key, item_kind=item_kind,
            action=action, kind=kind, deviations=deviations, outcome=outcome, model_speech=speech,
        )  # fmt: skip
        yield meta
        changed = self._stage_event(state)
        if changed is not None:
            yield changed

    def _messages(
        self,
        state: InterviewState,
        move: S.Move,
        *,
        offers: list[tuple[str, str, str]],
        notes: list[str],
        last_answer: str | None,
    ) -> list[dict[str, str]]:
        ik = self._ik(state)
        kit = self.kit
        key = move.item_key or ""
        stage_key = S.item_spec(key).stage if key else (ik.stage.key if ik.stage else "greeting")
        if move.stage_to and "follow_up" in move.offered and ik.stage is not None:
            stage_key = ik.stage.key
        claim = None
        claim_probes = None
        ref = None
        for candidate in (key, move.followup_item or ""):
            if S.is_ladder(candidate):
                ref = S.item_spec(candidate).claim_ref
                break
        if ref:
            claim = self._claim(ik, ref)
            if claim is not None:
                probe = next(
                    (p for p in kit.resume_framework.claim_probes if p.key == claim.get("probe")), None
                )
                claim_probes = " / ".join(probe.questions) if probe else None
        card = None
        if stage_key == "topic" and ik.card:
            topic_card = kit.topic_card(ik.card)
            card = {"title": topic_card.title, "bullets": list(topic_card.bullets)}
        approved = P.approved_info_text(ik.approved_info) if stage_key == "close" else None
        elapsed = self._now(state)
        if state.ungrounded_reference:
            notes = [
                *notes,
                "Your previous question credited the candidate with something they never said (quoted below); do "
                "not refer to it again. " + fence(state.ungrounded_reference, "derived_ungrounded_reference"),
            ]
        return P.render_turn(
            interviewer_name=state.interviewer_name,
            program_name=ik.program_name,
            conduct=list(kit.conduct),
            stage_key=stage_key,
            elapsed=f"{int(elapsed // 60)} min",
            limit=f"{int(ik.time_limit_s // 60)} min",
            offers=offers,
            claim=claim,
            claim_probes=claim_probes,
            resume=P.resume_excerpt(self._profile(state))
            if stage_key in ("greeting", "resume", "learning")
            else None,
            digest=ik.onboarding_digest if stage_key in ("greeting", "resume", "learning") else None,
            card=card,
            approved=approved,
            conversation=P.conversation_text(state.conversation, limit=6),
            last_answer=last_answer,
            notes=notes,
        )

    async def _finish_model(
        self,
        state: InterviewState,
        move: S.Move,
        turn: IntakeTurn,
        *,
        utterance_id: str,
        text: str,
        meta_key: str,
        item_kind: str,
        action: TurnAction,
        kind: UtteranceKind,
        deviations: list[str],
        outcome: TurnOutcome,
        model_speech: str | None = None,
    ) -> UtteranceMeta:
        ik = self._ik(state)
        now = self._now(state)
        offered = move.offered
        chosen: str = turn.move
        if chosen not in offered:
            deviations.append("move_not_offered")
            if chosen == "follow_up" and move.followup_item:
                pass  # a follow-up the machine did not offer is still charged (6.4)
            elif "ask_item" in offered:
                chosen = "ask_item"
            elif offered:
                chosen = offered[0]
        if turn.item_key and chosen in ("ask_item", "follow_up", "clarify"):
            expected = (
                move.item_key
                if chosen == "ask_item"
                else (move.followup_key if chosen == "follow_up" else meta_key)
            )
            if expected and turn.item_key != expected:
                deviations.append("item_mismatch")
        # The model's read of the last answer (stored on the answer record, never a score).
        if state.answers and turn.answer_quality is not None and state.answers[-1].quality is None:
            state.answers[-1].quality = turn.answer_quality
            state.answers[-1].quality_source = "model"
        resolved = move.resolve.item_key if move.resolve else None
        if turn.claim_signal and resolved and S.is_ladder(resolved):
            spec = S.item_spec(resolved)
            if spec.rung == "ownership" and spec.claim_ref:
                ik.claim_signals[spec.claim_ref] = turn.claim_signal
        if turn.target_bullet and (meta_key == "topic.weakest" or move.item_key == "topic.weakest"):
            ik.weakest_bullet = turn.target_bullet
        # State: which item this utterance asked.
        followup_index = 0
        if item_kind == "clarify" or chosen == "clarify":
            meta_key = meta_key or (move.item_key or "")
        elif chosen == "follow_up" or "answer_question" in offered:
            S.apply_resolution(ik, move.resolve, now)
            item = S.apply_followup(ik, move, now)
            followup_index = item.followups_used
            meta_key = move.followup_key or meta_key
            item_kind = "answer_question" if "answer_question" in offered else "followup"
            if action == TurnAction.ASK_QUESTION:
                action = TurnAction.ASK_FOLLOWUP
        else:
            S.apply_resolution(ik, move.resolve, now)
            ended = ik.stage.key if ik.stage and move.stage_to and ik.stage.key != move.stage_to else None
            item = S.apply_ask(ik, move, now)
            meta_key = item.item_key
            if ended:
                self._stage_metric(ik, ended)
        # Post-checks (logged as deviations; an intake issue switches the next model utterance to its template).
        checked = model_speech if model_speech is not None else text
        issues = [f"speech:{i}" for i in speech_issues(checked, action)]
        stage_key = ik.stage.key if ik.stage else ""
        terms = P.material_terms(self.kit, passage_keys=ik.passages.values(), card=ik.card)
        intake_issues = P.intake_speech_issues(
            checked,
            stage_key=stage_key,
            material_terms=terms,
            approved_info=P.approved_info_text(ik.approved_info),
        )
        grounding = self.engine._grounding_issue(state, checked)
        if grounding:
            deviations.append("ungrounded_reference")
        state.ungrounded_reference = grounding
        ik.template_next = bool(intake_issues)
        deviations.extend(issues + intake_issues)
        if outcome.dropped_opener:
            deviations.append("speech:evaluative_opener_dropped")
        if outcome.dropped_tail:
            deviations.append("speech:second_question_dropped")
        return await self._commit(
            state,
            utterance_id=utterance_id,
            text=text,
            kind=kind,
            item_key=meta_key,
            item_kind=item_kind,
            template=False,
            action=action,
            move=chosen,
            followup_index=followup_index,
            deviations=deviations,
            latency=outcome.latency(),
        )

    # ------------------------------------------------------------- say/commit
    async def _say(
        self,
        state: InterviewState,
        text: str,
        *,
        item_key: str | None,
        item_kind: str,
        kind: UtteranceKind,
        action: TurnAction,
        creates_question: bool = True,
        followup_index: int = 0,
        activity_id: str | None = None,
        ended_stage: str | None = None,
        all_stages: bool = False,
        flow: str | None = None,
    ) -> AsyncGenerator[TurnEvent, None]:
        """A template utterance: commit first, then UtteranceBegin (with the text) + UtteranceMeta."""
        utterance_id = str(uuid.uuid4())
        if ended_stage:
            self._stage_metric(self._ik(state), ended_stage)
        meta = await self._commit(
            state,
            utterance_id=utterance_id,
            text=text,
            kind=kind,
            item_key=item_key,
            item_kind=item_kind,
            template=True,
            action=action,
            creates_question=creates_question,
            followup_index=followup_index,
            activity_id=activity_id,
            all_stages=all_stages,
            flow=flow,
        )
        yield UtteranceBegin(
            utterance_id=utterance_id,
            kind=kind,
            behavior=meta.behavior,
            question_id=meta.question_id,
            text=text,
        )
        yield meta
        changed = self._stage_event(state)
        if changed is not None:
            yield changed

    def _meta(
        self,
        state: InterviewState,
        item_key: str | None,
        *,
        item_kind: str,
        template: bool,
        move: str | None,
        followup_index: int,
        deviations: list[str],
        activity_id: str | None,
    ) -> dict[str, Any]:
        ik = self._ik(state)
        key = item_key or ik.current_item or ""
        base = key[: -len(".clarify")] if key.endswith(".clarify") else key
        try:
            spec: S.ItemSpec | None = S.item_spec(base)
        except KeyError:
            spec = None
        stage = spec.stage if spec else (ik.stage.key if ik.stage else "greeting")
        extra_kinds = ("followup", "clarify", "answer_question", "redirect")
        meta = {
            "stage": stage,
            "item_key": key[:60],
            "item_kind": item_kind,
            "mandatory": bool(spec.mandatory) and item_kind not in extra_kinds if spec else False,
            "template": template,
            "claim_ref": spec.claim_ref if spec else None,
            "rung": spec.rung if spec else None,
            "mq": spec.mq if spec and spec.mq in ("mq2", "mq3", "mq4") else None,
            "language": ik.language if spec and spec.item_kind == "verification" else None,
            "activity_id": activity_id,
            "followup_index": int(followup_index),
            "answer_mode": spec.answer_mode if spec and item_kind not in extra_kinds else "standard",
            "soft_limit_s": spec.soft_limit_s if spec and item_kind not in extra_kinds else None,
            "move": move,
            "deviations": [d[:80] for d in deviations][:12],
        }
        try:
            IntakeQuestionMeta.model_validate(meta)
        except ValidationError as exc:  # contract drift: log, never break the interview
            log.error("intake.question_meta_invalid", errors=[e.get("loc") for e in exc.errors()][:5])
        return meta

    def _question_kind(self, item_key: str, item_kind: str) -> QuestionKind:
        if item_kind in ("followup", "clarify"):
            return QuestionKind.FOLLOWUP
        if item_kind == "closing" or item_key == "close.questions":
            return QuestionKind.CLOSING
        if item_kind == "ladder":
            return QuestionKind.RESUME_CLAIM
        if item_kind in ("mq", "answer_question", "redirect"):
            return QuestionKind.RECRUITER
        if item_kind in ("verification",) or item_key.startswith("reading."):
            return QuestionKind.TECHNICAL
        if item_key.endswith(".scenario"):
            return QuestionKind.SCENARIO
        if item_key == "greeting.intro":
            return QuestionKind.INTRO
        return QuestionKind.BEHAVIORAL

    def _competencies(self, stage_key: str, item_key: str) -> list[str]:
        kit = self.kit
        spec = None
        base = item_key[: -len(".clarify")] if item_key.endswith(".clarify") else item_key
        try:
            spec = S.item_spec(base)
        except KeyError:
            pass
        if spec is not None and spec.mq:
            return list(kit.mandatory_question(spec.mq).informs)
        try:
            return list(kit.stage(stage_key).competencies)
        except KeyError:
            return []

    async def _commit(
        self,
        state: InterviewState,
        *,
        utterance_id: str,
        text: str,
        kind: UtteranceKind,
        item_key: str | None,
        item_kind: str,
        template: bool,
        action: TurnAction,
        move: str | None = None,
        creates_question: bool = True,
        followup_index: int = 0,
        deviations: list[str] | None = None,
        activity_id: str | None = None,
        latency: dict[str, Any] | None = None,
        all_stages: bool = False,
        flow: str | None = None,
    ) -> UtteranceMeta:
        """Persist one interviewer utterance (questions + transcripts rows, stage rows, durable state)."""
        ik = self._ik(state)
        elapsed = self._now(state)
        deviations = list(deviations or [])
        meta = self._meta(
            state, item_key, item_kind=item_kind, template=template, move=move, followup_index=followup_index,
            deviations=deviations, activity_id=activity_id,
        )  # fmt: skip
        stage_key = meta["stage"]
        question_id = str(uuid.uuid4()) if creates_question else None
        behavior = beh.provisional(action)
        decision = {"action": action.value, "intake": meta, "template": template, "latency": latency or {}}
        if flow:
            decision["flow"] = flow  # a flow reply (understanding.py): the item stays pending
        key = meta["item_key"]
        parent = (
            state.current_question_id
            if item_kind in ("followup", "clarify", "redirect", "answer_question")
            else None
        )
        async with session_scope() as db:
            if question_id:
                await add_question(
                    db,
                    state.session_id,
                    question_id=uuid.UUID(question_id),
                    kind=self._question_kind(key, item_kind).value,
                    action=action.value,
                    source="curated"
                    if template
                    else ("followup" if item_kind == "followup" else "generated"),
                    text_value=text,
                    segment=f"{SEGMENT_PREFIX}{stage_key}",
                    topic=P.item_title(key)[:120] or None,
                    competency_keys=self._competencies(stage_key, key),
                    difficulty=Difficulty.MEDIUM.value,
                    question_focus=question_core(text)[:300] or None,
                    reason=f"intake {key} ({item_kind})",
                    expected_evidence=[],
                    claim_id=meta.get("claim_ref"),
                    decision=decision,
                    elapsed_s=elapsed,
                    parent_question_id=parent,
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
            if question_id:
                state.questions.append(
                    QuestionRecord(
                        id=question_id,
                        sequence=len(state.questions) + 1,
                        action=action,
                        text=text,
                        focus=question_core(text)[:200] or None,
                        competency_key=None,
                        topic=key,
                        difficulty=Difficulty.MEDIUM,
                        parent_id=parent,
                        asked_at_s=elapsed,
                        kind="followup" if item_kind in ("followup", "clarify") else "question",
                    )
                )
                state.current_question_id = question_id
                found = ik.find_item(item_key.removesuffix(".clarify") if item_key else None)
                if found is not None:
                    found[1].question_ids.append(question_id)
            add_utterance(
                state,
                UtteranceRecord(
                    id=utterance_id,
                    kind=kind,
                    action=action,
                    question_id=question_id or state.current_question_id,
                    text=text,
                    at_s=elapsed,
                    transcript_id=str(row.id),
                ),
            )
            record_interviewer_text(state, text=text, at_s=elapsed, question_id=question_id)
            state.turn_index += 1
            state.turn_state = TurnState.AI_SPEAKING
            state.avatar_state = behavior.state
            if state.current_segment != stage_key:
                self._stage_changed.add(state.session_id)
            state.current_segment = stage_key
            if deviations:
                state.deviations += len([d for d in deviations if not d.startswith("speech:")])
            await self._upsert_stages(state, db, all_stages=all_stages)
            await log_event(
                db,
                state.session_id,
                "intake.utterance",
                {
                    "utterance_id": utterance_id,
                    "question_id": question_id,
                    "stage": stage_key,
                    "item_key": key,
                    "item_kind": item_kind,
                    "template": template,
                    "move": move,
                    "flow": flow,
                    "deviations": deviations[:8],
                },
                elapsed_s=elapsed,
            )
            await self.engine._save(state, durable=True, db=db)
        if deviations:
            log.info("intake.utterance_deviations", session_id=state.session_id, deviations=deviations[:6])
        return UtteranceMeta(
            utterance_id=utterance_id,
            question_id=question_id,
            behavior=behavior,
            decision=decision,
            full_text=text,
        )

    async def _upsert_stages(self, state: InterviewState, db: Any, *, all_stages: bool = False) -> None:
        ik = self._ik(state)
        keys: set[str] | None = None
        if not all_stages:
            keys = {s.key for s in ik.stages if s.status in ("in_progress",)}
            keys |= {
                s.key
                for s in ik.stages
                if s.ended_s is not None and ik.stage is not None and s.number <= ik.stage.number
            }
        await capture_store.upsert_stage_rows(
            db,
            organization_id=state.organization_id,
            intake_id=ik.intake_id,
            session_id=state.session_id,
            stages=ik.stages,
            keys=keys,
            extra=self._stage_extra(ik),
        )

    def _stage_extra(self, ik: IntakeState) -> dict[str, dict[str, Any]]:
        extra: dict[str, dict[str, Any]] = {
            "resume": {"claims": ik.claims, "claim_signals": ik.claim_signals, "hedges": ik.hedges},
            "mandatory": {
                "language": ik.language,
                "language_fallback": ik.language_fallback,
                "level": ik.level,
                "verification": ik.verification or None,
                "self_rating": ik.self_rating,
            },
        }
        if ik.passages:
            extra["reading"] = {"passages": dict(ik.passages)}
        topic: dict[str, Any] = {}
        if ik.card:
            topic["card"] = ik.card
        if ik.weakest_bullet:
            topic["target_bullet"] = ik.weakest_bullet
        scenarios = [{"key": v, "item_key": k} for k, v in ik.scenario_keys.items()]
        if scenarios:
            topic["scenarios"] = [s for s in scenarios if s["item_key"].startswith("topic.")]
            extra["learning"] = {"scenarios": [s for s in scenarios if s["item_key"].startswith("learning.")]}
        if topic:
            extra["topic"] = topic
        return extra

    def _stage_metric(self, ik: IntakeState, stage_key: str) -> None:
        stage = ik.stage_by_key(stage_key)
        if stage is None or stage.started_s is None:
            return
        end = stage.ended_s if stage.ended_s is not None else stage.started_s
        with contextlib.suppress(Exception):  # metrics never break a turn
            metrics.INTAKE_STAGE_SECONDS.labels(stage=stage_key).observe(max(0.0, end - stage.started_s))

    # ============================================================ material
    def _spawn_grade(self, state: InterviewState, text: str, *, retry: bool = False) -> None:
        ik = self._ik(state)
        language = self.kit.language_check(ik.language) if ik.language else None
        if language is None:
            return
        messages = P.render_verification(language, text, retry=retry)
        task = self.engine._spawn(
            self._grade(state.session_id, state.organization_id, messages), name="intake.verification_grade"
        )
        self._grades[state.session_id] = (time.monotonic(), task)

    async def _grade(
        self, session_id: str, organization_id: str, messages: list[dict[str, str]]
    ) -> VerificationGrade | None:
        try:
            result = await self.engine.ai.llm.structured(
                VerificationGrade,
                messages,
                context=CallContext(
                    task=ModelTask.ANSWER_ASSESSMENT,
                    session_id=session_id,
                    organization_id=organization_id,
                    prompt_version=P.VERIFICATION_PROMPT_VERSION,
                ),
                temperature=0.0,
                max_tokens=120,
                timeout=GRADE_BUDGET_S,
                max_attempts=1,
            )
        except AIError as exc:
            log.info("intake.verification_grade_skipped", session_id=session_id, reason=type(exc).__name__)
            return None
        return result.value

    async def _await_grade(self, state: InterviewState) -> None:
        ik = self._ik(state)
        entry = self._grades.pop(state.session_id, None)
        if entry is None:
            return
        started, task = entry
        wait = max(0.0, GRADE_BUDGET_S - (time.monotonic() - started))
        grade = None
        try:
            grade = await asyncio.wait_for(asyncio.shield(task), wait)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # the turn itself is being cancelled (barge-in, shutdown)
            grade = None  # only the background grade was cancelled
        except Exception:  # noqa: BLE001 - TimeoutError or a failed grade: selection works without it
            grade = None
        if isinstance(grade, VerificationGrade):
            ik.verification = {
                "quality": grade.quality,
                "matched_key_points": list(grade.matched_key_points),
                "source": "quick_grade",
            }

    async def select_material(self, state: InterviewState) -> None:
        """Level estimate (6.5.1), passages (6.5.2), card (6.5.3) and scenarios (6.5.6); idempotent."""
        ik = self._ik(state)
        if ik.passages and ik.card:
            return
        await self._await_grade(state)
        kit = self.kit
        profile = self._profile(state)
        quality = (ik.verification or {}).get("quality")
        ik.level = selection.level_estimate(
            self_rating=ik.self_rating,
            verification_quality=quality,
            resume_ai_ml=selection.profile_mentions_ai_ml(profile),
            years=ik.years_hands_on if ik.years_hands_on is not None else selection.experience_years(profile),
            thin=selection.thin_resume(profile),
        )
        if not ik.passages:
            a, b = selection.select_passages(
                kit,
                ik.level,
                state.session_id,
                drill_a=ik.overrides.get("passage_drill_a", "auto"),
                drill_b=ik.overrides.get("passage_drill_b", "auto"),
            )
            ik.passages = {"drill_a": a, "drill_b": b}
        if not ik.card:
            ik.card = selection.select_card(
                ik.level, state.session_id, ik.overrides.get("topic_card", "auto")
            )
        self._select_scenarios(state)
        log.info(
            "intake.material_selected",
            session_id=state.session_id,
            level=ik.level,
            drill_a=ik.passages.get("drill_a"),
            drill_b=ik.passages.get("drill_b"),
            card=ik.card,
        )

    def _select_scenarios(self, state: InterviewState) -> None:
        ik = self._ik(state)
        if ik.scenario_keys:
            return
        ik.scenario_keys = selection.select_scenarios(
            self.kit, ik.level or "intermediate", state.session_id, limit=S.scenario_limit(ik)
        )

    # =========================================================== activities
    async def _arm_activity(
        self, state: InterviewState, move: S.Move, *, lead: str | None = None
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        now = self._now(state)
        kit = self.kit
        ended = ik.stage.key if ik.stage and move.stage_to and ik.stage.key != move.stage_to else None
        item = S.apply_ask(ik, move, now)
        kind = acts.ACTIVITY_ITEMS[item.item_key]
        await self.select_material(state)
        material = ik.passages["drill_a"] if kind == "reading_aloud" else (
            ik.passages["drill_b"] if kind == "read_relay" else str(ik.card)
        )  # fmt: skip
        plans = acts.phase_plans(kit, kind)
        phases = [{"phase": p.phase, "status": "planned", "planned_ms": p.duration_ms} for p in plans]
        async with session_scope() as db:
            activity_id = await capture_store.ensure_activity_row(
                db,
                organization_id=state.organization_id,
                intake_id=ik.intake_id,
                session_id=state.session_id,
                kind=kind,
                stage_key=acts.ACTIVITY_STAGES[kind],
                material_key=material,
                kit_version=ik.kit_version,
                question_id=None,
                phases=phases,
            )
        utterance_id = str(uuid.uuid4())
        ik.activity = IntakeActivityState(
            activity_id=str(activity_id),
            kind=kind,
            phase=plans[0].phase,
            status="armed",
            material_key=material,
            attempt=1,
            start_after_utterance_id=utterance_id,
            item_key=item.item_key,
        )
        text = P.join_speech(lead, P.transition_prefix(move.stage_to), P.ACTIVITY_INSTRUCTIONS[kind])
        if ended:
            self._stage_metric(ik, ended)
        meta = await self._commit(
            state,
            utterance_id=utterance_id,
            text=text,
            kind="question",
            item_key=item.item_key,
            item_kind="activity",
            template=True,
            action=TurnAction.ASK_QUESTION,
            activity_id=str(activity_id),
        )
        ik.activity.question_id = meta.question_id
        async with session_scope() as db:
            await capture_store.update_activity(db, activity_id, question_id=_uuid(meta.question_id))
        await self.engine._save(state)
        yield UtteranceBegin(
            utterance_id=utterance_id,
            kind="question",
            behavior=meta.behavior,
            question_id=meta.question_id,
            text=text,
        )
        yield meta
        yield ActivityStarted(
            activity=acts.descriptor(
                kit, activity_id=str(activity_id), kind=kind, material_key=material, item_key=item.item_key
            ),
            start_after_utterance_id=utterance_id,
        )
        changed = self._stage_event(state)
        yield changed if changed is not None else StateChanged(reason="intake_activity")

    async def _rearm(self, state: InterviewState, *, lead: str | None) -> AsyncGenerator[TurnEvent, None]:
        """Speak the instruction (or the armed phase's prompt) again and re-arm the activity."""
        ik = self._ik(state)
        activity = ik.activity
        if activity is None:
            return
        kit = self.kit
        plans = acts.phase_plans(kit, activity.kind)
        first = activity.phase == plans[0].phase
        prompt = P.ACTIVITY_INSTRUCTIONS[activity.kind] if first else P.PHASE_PROMPTS.get(
            (activity.kind, activity.phase), P.ACTIVITY_INSTRUCTIONS[activity.kind]
        )  # fmt: skip
        utterance_id = str(uuid.uuid4())
        activity.start_after_utterance_id = utterance_id
        activity.status = "armed"
        meta = await self._commit(
            state,
            utterance_id=utterance_id,
            text=P.join_speech(lead, prompt),
            kind="question",
            item_key=activity.item_key,
            item_kind="activity",
            template=True,
            action=TurnAction.ASK_QUESTION,
            activity_id=activity.activity_id,
        )
        yield UtteranceBegin(
            utterance_id=utterance_id, kind="question", behavior=meta.behavior, question_id=meta.question_id,
            text=meta.full_text,
        )  # fmt: skip
        yield meta
        yield ActivityStarted(
            activity=acts.descriptor(
                kit, activity_id=activity.activity_id, kind=activity.kind, material_key=activity.material_key,
                attempt=activity.attempt, resumed=True, start_phase=activity.phase, item_key=activity.item_key,
            ),
            start_after_utterance_id=utterance_id,
        )  # fmt: skip

    async def _recover_running(self, state: InterviewState) -> None:
        """A normal candidate turn while a phase runs means the runtime lost that phase (a failed activity job:
        the runtime never forwards speech or typed text during a running phase). Re-arm safely: a non-capture
        phase is closed (at its deadline at the latest) and the activity moves on to its capture phase, so a
        Drill B passage is never shown again by a recovery; a capture phase restarts as a new attempt, so a late
        final pass of the lost attempt can never land on it."""
        ik = self._ik(state)
        activity = ik.activity
        if activity is None or activity.status != "running":
            return
        now = self._now(state)
        end_s = now
        if activity.phase_started_s is not None and activity.phase_duration_s:
            end_s = min(now, activity.phase_started_s + activity.phase_duration_s)
        await self._end_phase_record(state, activity, end_s=end_s, reason="recovered")
        values: dict[str, Any] = {"status": "armed"}
        if acts.is_capture(activity.kind, activity.phase):
            activity.attempt += 1
            values["attempt"] = activity.attempt
        else:
            nxt = acts.next_phase(self.kit, activity.kind, activity.phase)
            if nxt is not None:
                activity.phase = nxt.phase
        activity.status = "armed"
        activity.phase_started_s = None
        async with session_scope() as db:
            await capture_store.update_activity(db, activity.activity_id, **values)
        log.warning(
            "intake.activity_recovered", session_id=state.session_id, kind=activity.kind, phase=activity.phase
        )

    def rearm_after(
        self, state: InterviewState, utterance_id: str, interrupted_id: str
    ) -> ActivityStarted | None:
        """Backchannel resume of an interrupted activity instruction: the remainder ``utterance_id`` arms it."""
        ik = self._ik(state)
        activity = ik.activity
        if (
            activity is None
            or activity.status != "armed"
            or activity.start_after_utterance_id != interrupted_id
        ):
            return None
        activity.start_after_utterance_id = utterance_id
        return ActivityStarted(
            activity=acts.descriptor(
                self.kit, activity_id=activity.activity_id, kind=activity.kind, material_key=activity.material_key,
                attempt=activity.attempt, resumed=True, start_phase=activity.phase, item_key=activity.item_key,
            ),
            start_after_utterance_id=utterance_id,
        )  # fmt: skip

    async def activity_event(
        self, state: InterviewState, event: ActivityEvent
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        activity = ik.activity
        if activity is None or activity.activity_id != event.activity_id:
            log.info("intake.activity_event_stale", session_id=state.session_id, kind=event.kind)
            return
        kit = self.kit
        if event.kind == "phase_started":
            if activity.status == "running" and activity.phase == event.phase:
                return
            plan = acts.phase_plan(kit, activity.kind, event.phase)
            activity.status = "running"
            activity.phase = event.phase
            activity.phase_started_s = float(event.elapsed_s)
            activity.phase_duration_s = float(event.detail.get("duration_ms") or plan.duration_ms) / 1000.0
            activity.interrupted_phase = None
            activity.phase_remaining_s = None
            async with session_scope() as db:
                row = await capture_store.load_activity(db, activity.activity_id)
                phases = list(row.phases or []) if row is not None else []
                phases.append(
                    {
                        "phase": event.phase,
                        "status": "running",
                        "attempt": activity.attempt,
                        "planned_ms": int(activity.phase_duration_s * 1000),
                        "started_elapsed_s": round(float(event.elapsed_s), 3),
                        "ended_elapsed_s": None,
                        "end_reason": None,
                    }
                )
                values: dict[str, Any] = {"phases": phases, "status": "running"}
                if activity.kind == "read_relay" and event.phase == "read" and row is not None:
                    reveal = dict(row.reveal or {})
                    windows = list(reveal.get("windows") or [])
                    windows.append({"from_elapsed_s": round(float(event.elapsed_s), 3), "to_elapsed_s": None})
                    reveal["windows"] = windows
                    values["reveal"] = reveal
                await capture_store.update_activity(db, activity.activity_id, **values)
                if plan.capture:
                    await capture_store.reset_attempt(db, activity.activity_id, attempt=activity.attempt)
                await log_event(
                    db,
                    state.session_id,
                    "intake.activity_phase",
                    {"activity_id": activity.activity_id, "kind": activity.kind, "phase": event.phase,
                     "status": "running", "attempt": activity.attempt},
                    elapsed_s=float(event.elapsed_s),
                )  # fmt: skip
                await self.engine._save(state, db=db)
            return
        # phase_deadline / finish_requested: only non-capture phases end here (captures end as turns).
        if (
            activity.status != "running"
            or activity.phase != event.phase
            or acts.is_capture(activity.kind, event.phase)
        ):
            return
        nxt = acts.next_phase(kit, activity.kind, event.phase)
        if nxt is None:
            return
        reason = "finish_button" if event.kind == "finish_requested" else "deadline"
        shown_ms = event.detail.get("shown_ms")
        await self._end_phase_record(
            state,
            activity,
            end_s=float(event.elapsed_s),
            reason=reason,
            runtime_shown_ms=int(shown_ms) if isinstance(shown_ms, int | float) else None,
        )
        activity.phase = nxt.phase
        activity.status = "armed"
        activity.phase_started_s = None
        prompts = P.PHASE_PROMPTS_EARLY if reason == "finish_button" else P.PHASE_PROMPTS
        text = prompts[(activity.kind, nxt.phase)]
        utterance_id = str(uuid.uuid4())
        activity.start_after_utterance_id = utterance_id
        meta = await self._commit(
            state,
            utterance_id=utterance_id,
            text=text,
            kind="question",
            item_key=activity.item_key,
            item_kind="activity",
            template=True,
            action=TurnAction.ASK_QUESTION,
            activity_id=activity.activity_id,
        )
        yield UtteranceBegin(
            utterance_id=utterance_id,
            kind="question",
            behavior=meta.behavior,
            question_id=meta.question_id,
            text=text,
        )
        yield meta
        yield ActivityPhaseChanged(
            activity_id=activity.activity_id,
            kind=activity.kind,
            phase=nxt.phase,
            duration_ms=nxt.duration_ms,
            capture=nxt.capture,
            can_finish=nxt.finish_after_ms == 0,
            content=acts.material_content(kit, activity.kind, activity.material_key)
            if nxt.content_visible
            else None,
            hide_content=nxt.hide_on_entry,
            start_after_utterance_id=utterance_id,
        )

    async def _end_phase_record(
        self,
        state: InterviewState,
        activity: IntakeActivityState,
        *,
        end_s: float,
        reason: str,
        runtime_shown_ms: int | None = None,
    ) -> None:
        """Close the running phase record (and the read_relay reveal window). ``runtime_shown_ms`` is the
        runtime's own total display time for the passage; the larger of the two accounts is kept."""
        shown = 0.0
        if activity.phase_started_s is not None:
            shown = max(0.0, end_s - activity.phase_started_s)
        if activity.kind == "read_relay" and activity.phase == "read":
            activity.reveal_shown_s += shown
            if runtime_shown_ms is not None:
                activity.reveal_shown_s = max(activity.reveal_shown_s, runtime_shown_ms / 1000.0)
        async with session_scope() as db:
            row = await capture_store.load_activity(db, activity.activity_id)
            if row is None:
                return
            phases = list(row.phases or [])
            for record in reversed(phases):
                if record.get("phase") == activity.phase and record.get("status") == "running":
                    record.update(status="ended", ended_elapsed_s=round(end_s, 3), end_reason=reason)
                    break
            values: dict[str, Any] = {"phases": phases}
            if activity.kind == "read_relay" and activity.phase == "read":
                reveal = dict(row.reveal or {})
                windows = list(reveal.get("windows") or [])
                if windows and windows[-1].get("to_elapsed_s") is None:
                    windows[-1]["to_elapsed_s"] = round(end_s, 3)
                reveal["windows"] = windows
                reveal["shown_ms"] = int(round(activity.reveal_shown_s * 1000))
                if reason == "disconnect":
                    reveal["interrupted"] = True
                values["reveal"] = reveal
            await capture_store.update_activity(db, activity.activity_id, **values)

    async def _capture_turn(  # noqa: PLR0915
        self, state: InterviewState, turn: CandidateTurn, text: str, *, replay: bool, paused: bool = False
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        capture = turn.activity
        assert capture is not None
        if replay:
            return
        activity = ik.activity
        if activity is None or activity.activity_id != capture.activity_id:
            log.info("intake.capture_stale", session_id=state.session_id, kind=capture.kind)
            return
        now = self._now(state)
        summary = {
            "attempt": activity.attempt,
            "phase": capture.phase,
            "started_ms": int(capture.started_ms),
            "ended_ms": int(capture.ended_ms),
            "voiced_ms": int(capture.voiced_ms),
            "end_reason": capture.end_reason,
            "segments": len(capture.segments),
            "pauses": len(capture.pauses),
        }
        if capture.end_reason == "disconnect":
            # Interrupted attempt: kept in attempts_log (never scored); the resume restarts the phase (7.8).
            activity.interrupted_phase = capture.phase
            if activity.phase_started_s is not None:
                activity.phase_started_s = None
            async with session_scope() as db:
                await capture_store.append_attempt_log(
                    db,
                    activity.activity_id,
                    {"attempt": activity.attempt, "end_reason": "disconnect", "capture": summary,
                     "transcript_live": text or None},
                )  # fmt: skip
                row = await capture_store.load_activity(db, activity.activity_id)
                if row is not None:
                    phases = list(row.phases or [])
                    for record in reversed(phases):
                        if record.get("phase") == capture.phase and record.get("status") == "running":
                            record.update(status="ended", ended_elapsed_s=round(capture.ended_ms / 1000.0, 3),
                                          end_reason="disconnect")  # fmt: skip
                            break
                    await capture_store.update_activity(db, activity.activity_id, phases=phases)
                await log_event(
                    db, state.session_id, "intake.activity_interrupted",
                    {"activity_id": activity.activity_id, "kind": activity.kind, "attempt": activity.attempt,
                     "phase": capture.phase}, elapsed_s=now,
                )  # fmt: skip
                await self.engine._save(state, durable=True, db=db)
            self._activity_metric(activity.kind, "disconnect")
            return
        answer_id, media_id = await self._persist_answer(state, turn, text, item_key=activity.item_key)
        record = {
            **{k: v for k, v in summary.items() if k not in ("segments", "pauses")},
            "segments": [dict(s) for s in capture.segments],
            "pauses": [dict(p) for p in capture.pauses],
            "stt_confidence": capture.stt_confidence,
            "stt_degraded": capture.stt_degraded,
            "typed": capture.typed,
            "audio_stored": bool(media_id),
        }
        async with session_scope() as db:
            row = await capture_store.load_activity(db, activity.activity_id)
            phases = list(row.phases or []) if row is not None else []
            for item in reversed(phases):
                if item.get("phase") == capture.phase and item.get("status") == "running":
                    item.update(status="ended", ended_elapsed_s=round(capture.ended_ms / 1000.0, 3),
                                end_reason=capture.end_reason)  # fmt: skip
                    break
            await capture_store.write_capture(
                db,
                activity.activity_id,
                capture=record,
                status="completed",
                ended_reason=capture.end_reason,
                answer_id=answer_id,
                transcript_live=text or None,
                audio_media_id=_uuid(media_id),
                phases=phases,
            )
            if capture.typed:
                await capture_store.store_typed(
                    db, state.session_id, activity.activity_id, attempt=activity.attempt, text_value=text
                )
            elif not capture.segments:
                await capture_store.mark_transcript_status(
                    state.session_id,
                    activity.activity_id,
                    attempt=activity.attempt,
                    status="unavailable",
                    db=db,
                )
            await log_event(
                db, state.session_id, "intake.activity_completed",
                {"activity_id": activity.activity_id, "kind": activity.kind, "attempt": activity.attempt,
                 "end_reason": capture.end_reason, "segments": len(capture.segments), "typed": capture.typed},
                elapsed_s=now,
            )  # fmt: skip
        self._activity_metric(activity.kind, capture.end_reason)
        ended = ActivityEnded(
            activity_id=activity.activity_id, kind=activity.kind, reason=acts.end_reason(capture.end_reason)
        )
        ik.activity = None
        await self.engine._save(state, durable=True)
        if paused:
            ik.pending_candidate_turn = True
            ik.pending_signal = {
                "kind": "none",
                "words": S.words(text),
                "activity": True,
                "ended": {"activity_id": ended.activity_id, "kind": ended.kind, "reason": ended.reason},
            }
            await self.engine._save(state, durable=True)
            return
        yield ended
        async with aclosing(self._after_activity(state, text)) as relay:
            async for event in relay:
                yield event

    async def _after_activity(self, state: InterviewState, text: str) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        now = self._now(state)
        if ik.time_up_pending and not ik.time_up:
            move = S.next_move(
                ik, S.AnswerSignal(kind="none", words=S.words(text)), max(now, ik.time_limit_s)
            )
        else:
            move = S.next_move(ik, S.AnswerSignal(kind="none", words=S.words(text)), now)
        last = text if ik.current_item == "topic.card" else None
        async with aclosing(self._execute(state, move, last_answer=last)) as relay:
            async for event in relay:
                yield event

    @staticmethod
    def _activity_metric(kind: str, end_reason: str) -> None:
        with contextlib.suppress(Exception):  # metrics never break a turn
            metrics.INTAKE_ACTIVITIES.labels(kind=kind, end_reason=end_reason).inc()

    # ================================================================ pause
    async def on_pause(self, state: InterviewState, db: Any) -> None:
        """The interview clock paused (disconnect): account the running phase (reveal time, remaining time)."""
        ik = self._ik(state)
        activity = ik.activity
        if activity is None or activity.status != "running" or activity.phase_started_s is None:
            return
        now = state.elapsed_seconds(self.engine.now())
        used = max(0.0, now - activity.phase_started_s)
        if acts.is_capture(activity.kind, activity.phase):
            activity.interrupted_phase = activity.phase
            return
        duration = (
            activity.phase_duration_s
            or acts.phase_plan(self.kit, activity.kind, activity.phase).duration_ms / 1000
        )
        remaining = max(0.0, duration - used)
        await self._end_phase_record(state, activity, end_s=now, reason="disconnect")
        if activity.kind == "read_relay" and activity.phase == "read":
            read_s = self.kit.timers.drill_b_read_seconds
            remaining = min(remaining, max(0.0, read_s - activity.reveal_shown_s))
        activity.phase_remaining_s = remaining
        activity.interrupted_phase = activity.phase
        activity.phase_started_s = None
        del db

    # =============================================================== resume
    async def resume_events(
        self, state: InterviewState, *, reconnect: bool, mid_answer: bool
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        if ik.closed:
            return
        if ik.activity is not None:
            async with aclosing(self._activity_resume(state, reconnect=reconnect)) as relay:
                async for event in relay:
                    yield event
            return
        if ik.pending_candidate_turn:
            ik.pending_candidate_turn = False
            data = dict(ik.pending_signal or {})
            ik.pending_signal = {}
            if data.pop("activity", False):
                ended = data.get("ended") or {}
                if ended.get("activity_id"):
                    # The capture was committed while paused: the client still needs the activity end.
                    yield ActivityEnded(
                        activity_id=str(ended["activity_id"]),
                        kind=str(ended.get("kind") or ""),
                        reason=str(ended.get("reason") or "completed"),
                    )
                async with aclosing(self._after_activity(state, "")) as relay:
                    async for event in relay:
                        yield event
                return
            signal = S.AnswerSignal(
                **{k: v for k, v in data.items() if k in S.AnswerSignal.__dataclass_fields__}
            )
            last = next((a.text for a in reversed(state.answers)), None)
            move = S.next_move(ik, signal, self._now(state))
            async with aclosing(self._execute(state, move, last_answer=last)) as relay:
                async for event in relay:
                    yield event
            return
        if mid_answer:
            return
        current = state.current_question()
        item = ik.current()
        if current is not None and item is not None and item.status == "asked" and not current.answered:
            lead = P.RESUME_LEAD if reconnect else "As I was asking:"
            text = P.join_speech(lead, question_core(current.text))
            async for event in self._say_resume(state, text, question_id=current.id):
                yield event
            return
        move = S.next_move(ik, None, self._now(state))
        async with aclosing(self._execute(state, move)) as relay:
            async for event in relay:
                yield event

    async def _say_resume(
        self, state: InterviewState, text: str, *, question_id: str | None
    ) -> AsyncGenerator[TurnEvent, None]:
        """Re-ask the pending question (same question id; a transcript row, no new question row)."""
        utterance_id = str(uuid.uuid4())
        elapsed = self._now(state)
        spec = beh.for_kind("resume")
        async with session_scope() as db:
            row = await add_transcript(
                db, state.session_id, speaker="interviewer", text_value=text, start_ms=int(elapsed * 1000),
                end_ms=int(elapsed * 1000), question_id=question_id,
            )  # fmt: skip
            add_utterance(
                state,
                UtteranceRecord(
                    id=utterance_id, kind="resume", question_id=question_id, text=text, at_s=elapsed,
                    transcript_id=str(row.id),
                ),
            )  # fmt: skip
            record_interviewer_text(state, text=text, at_s=elapsed, question_id=question_id)
            await self.engine._save(state, durable=True, db=db)
        ik = self._ik(state)
        meta_intake = self._meta(
            state, ik.current_item, item_kind="prompt", template=True, move=None, followup_index=0, deviations=[],
            activity_id=None,
        )  # fmt: skip
        yield UtteranceBegin(
            utterance_id=utterance_id, kind="resume", behavior=spec, question_id=question_id, text=text
        )
        yield UtteranceMeta(
            utterance_id=utterance_id,
            question_id=question_id,
            behavior=spec,
            decision={"action": "resume", "intake": meta_intake},
            full_text=text,
        )

    async def _activity_resume(
        self, state: InterviewState, *, reconnect: bool
    ) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        activity = ik.activity
        assert activity is not None
        kit = self.kit
        if activity.status == "armed" and activity.interrupted_phase is None:
            async with aclosing(
                self._rearm(state, lead="Sorry, we got disconnected for a moment." if reconnect else None)
            ) as relay:
                async for event in relay:
                    yield event
            return
        phase = activity.interrupted_phase or activity.phase
        if acts.is_capture(activity.kind, phase):
            if activity.restarts >= S.MAX_ACTIVITY_RESTARTS:
                async with aclosing(self._abandon_activity(state)) as relay:
                    async for event in relay:
                        yield event
                return
            activity.restarts += 1
            activity.attempt += 1
            activity.phase = phase
            activity.status = "armed"
            activity.interrupted_phase = None
            text = P.RESTART_PROMPTS[(activity.kind, phase)]
            start_phase, duration = phase, None
        else:
            remaining = activity.phase_remaining_s
            if remaining is None:
                remaining = acts.phase_plan(kit, activity.kind, phase).duration_ms / 1000.0
            activity.interrupted_phase = None
            if remaining < acts.RESHOW_MIN_S:
                nxt = acts.next_phase(kit, activity.kind, phase)
                assert nxt is not None
                activity.phase = nxt.phase
                start_phase, duration = nxt.phase, None
                text = P.SKIP_TO_CAPTURE_PROMPTS[(activity.kind, nxt.phase)]
            else:
                activity.phase = phase
                start_phase = phase
                duration = int(max(0.0, remaining - acts.RESHOW_MARGIN_S) * 1000)
                text = P.RESHOW_PROMPTS[(activity.kind, phase)]
            activity.status = "armed"
        utterance_id = str(uuid.uuid4())
        activity.start_after_utterance_id = utterance_id
        meta = await self._commit(
            state,
            utterance_id=utterance_id,
            text=text,
            kind="question",
            item_key=activity.item_key,
            item_kind="activity",
            template=True,
            action=TurnAction.ASK_QUESTION,
            activity_id=activity.activity_id,
        )
        async with session_scope() as db:
            await capture_store.update_activity(
                db, activity.activity_id, attempt=activity.attempt, status="armed"
            )
        yield UtteranceBegin(
            utterance_id=utterance_id,
            kind="question",
            behavior=meta.behavior,
            question_id=meta.question_id,
            text=text,
        )
        yield meta
        yield ActivityStarted(
            activity=acts.descriptor(
                kit, activity_id=activity.activity_id, kind=activity.kind, material_key=activity.material_key,
                attempt=activity.attempt, resumed=True, start_phase=start_phase, start_duration_ms=duration,
                item_key=activity.item_key,
            ),
            start_after_utterance_id=utterance_id,
        )  # fmt: skip

    async def _abandon_activity(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        """Restarts exhausted: the activity ends ``interrupted`` (review item ``activity_interrupted``)."""
        ik = self._ik(state)
        activity = ik.activity
        assert activity is not None
        now = self._now(state)
        async with session_scope() as db:
            await capture_store.update_activity(
                db, activity.activity_id, status="interrupted", ended_reason="disconnect"
            )
            await capture_store.mark_transcript_status(
                state.session_id, activity.activity_id, attempt=activity.attempt, status="unavailable", db=db
            )
        found = ik.find_item(activity.item_key)
        if found is not None:
            found[1].status = "interrupted"
            found[1].skip_reason = "activity_interrupted"
            found[1].ended_s = now
        ended = ActivityEnded(activity_id=activity.activity_id, kind=activity.kind, reason="interrupted")
        ik.activity = None
        self._activity_metric(activity.kind, "interrupted")
        yield ended
        move = S.next_move(ik, None, now)
        async with aclosing(self._execute(state, move, lead=P.MOVE_ON)) as relay:
            async for event in relay:
                yield event

    async def regenerate_events(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        """Barge-in by a backchannel before anything usable was said: say the move again."""
        ik = self._ik(state)
        if ik.activity is not None and ik.activity.status == "armed":
            async with aclosing(self._rearm(state, lead=None)) as relay:
                async for event in relay:
                    yield event
            return
        async with aclosing(self.engine._repeat_events(state)) as relay:
            async for event in relay:
                yield event

    # ================================================================ timers
    def activity_active(self, state: InterviewState) -> bool:
        ik = state.intake
        return ik is not None and ik.activity is not None and ik.activity.status in ("armed", "running")

    async def time_up_events(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        ik = self._ik(state)
        if ik.time_up or ik.closed or (ik.stage is not None and ik.stage.key == "close"):
            return
        if self.activity_active(state):
            ik.time_up_pending = True  # activity timers are never shortened (6.3)
            await self.engine._save(state, durable=True)
            return
        move = S.next_move(ik, None, max(self._now(state), ik.time_limit_s))
        async with aclosing(self._execute(state, move)) as relay:
            async for event in relay:
                yield event

    async def silence_move_on(self, state: InterviewState) -> AsyncGenerator[TurnEvent, None]:
        """No answer after the gentle silence prompts: the item is closed as unanswered and the plan moves on."""
        ik = self._ik(state)
        if self.activity_active(state) or ik.closed:
            return
        move = S.next_move(ik, S.AnswerSignal(kind="silence"), self._now(state))
        async with aclosing(self._execute(state, move, lead=P.SILENCE_MOVE_ON)) as relay:
            async for event in relay:
                yield event

    # =============================================================== closing
    def closing_text(self, state: InterviewState, reason: SessionEndReason) -> str:
        ik = self._ik(state)
        first = ik.first_name or state.candidate_first_name
        reached_close = ik.stage is not None and ik.stage.key == "close"
        if reason in (SessionEndReason.SUFFICIENT_EVIDENCE, SessionEndReason.STOPPING_CONDITION) or (
            reason == SessionEndReason.TIME_LIMIT and reached_close
        ):
            text = P.closing_text(first)
        else:
            text = P.early_closing_text(first, reason.value)
        lead, ik.closing_lead = ik.closing_lead, None
        return P.join_speech(lead, text)

    async def before_closing(self, state: InterviewState) -> list[TurnEvent]:
        """End an active activity (``session_ended``; the row stays ``interrupted`` unless its capture was
        committed)."""
        ik = self._ik(state)
        activity = ik.activity
        if activity is None:
            return []
        async with session_scope() as db:
            row = await capture_store.load_activity(db, activity.activity_id)
            if row is not None and row.status != "completed":
                await capture_store.update_activity(
                    db, activity.activity_id, status="interrupted", ended_reason="session_end"
                )
                if row.answer_id is None:
                    await capture_store.mark_transcript_status(
                        state.session_id,
                        activity.activity_id,
                        attempt=activity.attempt,
                        status="unavailable",
                        db=db,
                    )
        found = ik.find_item(activity.item_key)
        if found is not None and found[1].status in ("asked", "pending"):
            found[1].status = "interrupted"
            found[1].skip_reason = "activity_interrupted"
        ik.activity = None
        self._activity_metric(activity.kind, "session_end")
        return [ActivityEnded(activity_id=activity.activity_id, kind=activity.kind, reason="session_ended")]

    async def closing_db(
        self, state: InterviewState, db: Any, *, reason: SessionEndReason, spoken: bool, aborted: bool
    ) -> None:
        """Inside the engine's closing transaction: final stage statuses + rows, ``intake.evaluate``."""
        ik = self._ik(state)
        now = state.elapsed_seconds(self.engine.now())
        current = ik.stage.key if ik.stage else None
        S.finish_close(ik, now, reached=spoken)
        if current:
            self._stage_metric(ik, current)
        await self._upsert_stages(state, db, all_stages=True)
        if aborted:
            return
        await enqueue(
            INTAKE_EVALUATE_JOB,
            {"session_id": state.session_id, "reason": "session_completed"},
            dedupe_key=f"{INTAKE_EVALUATE_JOB}:{state.session_id}:initial",
            delay_seconds=float(get_settings().intake_eval_start_delay_seconds),
            db=db,
        )
        log.info("intake.evaluate_enqueued", session_id=state.session_id, reason=reason.value)

    # ============================================================ test hooks
    async def jump(self, state: InterviewState, stage_key: str) -> None:
        """Test hook: skip to the start of ``stage_key`` (earlier open items become ``skipped``)."""
        ik = self._ik(state)
        target = ik.stage_by_key(stage_key)
        if target is None:
            raise KeyError(stage_key)
        now = self._now(state)
        if ik.activity is not None:
            await self.before_closing(state)
        for stage in ik.stages:
            if stage.number >= target.number:
                break
            for item in stage.items:
                if item.status in ("pending", "asked"):
                    item.status = "skipped"
                    item.ended_s = now
            if stage.status in ("pending", "in_progress"):
                stage.status = "completed" if not S.stage_incomplete(stage) else "incomplete"
                stage.started_s = stage.started_s if stage.started_s is not None else now
                stage.ended_s = now
        ik.stage_index = target.number - 1
        ik.current_item = None
        state.current_question_id = None
        if stage_key in ("reading", "topic", "learning", "close"):
            await self.select_material(state)
        async with session_scope() as db:
            await self._upsert_stages(state, db, all_stages=True)
            await self.engine._save(state, durable=True, db=db)


__all__ = ["INTAKE_EVALUATE_JOB", "IntakeConductor"]
