"""T1 helpers for the intake engine / realtime integration tests (Postgres + Redis + mock AI, no live models).

* ``build_intake_world`` - organisation, intake job + round, candidate, résumé profile with claims, consent,
  interview, ``intake_interviews`` row (settings, onboarding, optional MQ1 eligibility) and org intake settings.
* ``ScriptedIntakeModel`` - TURN_DECISION responder for ``intake-turn`` prompts: reads the OFFERED MOVES and
  returns a valid ``IntakeTurn`` (``ask_item`` by default, ``follow_up`` on request).
* ``intake_env`` fixture - an ``InterviewEngine`` with a fake clock, installed as the process engine (test hooks)
  and the realtime turn engine.
* ``Driver`` - plays the candidate (typed answers) and the runtime (activity phase events, captures).
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from sqlalchemy import select

from app.ai.mock import MockLLMCall
from app.core.security import generate_invite_token
from app.db.models import (
    BackgroundJob,
    Candidate,
    CandidateProfile,
    CandidateResume,
    Consent,
    IntakeActivityResult,
    IntakeInterview,
    IntakeOrgSettings,
    IntakeStageResult,
    Interview,
    Job,
    Organization,
    Round,
)
from app.db.session import session_scope
from app.domain.enums import ModelTask, RoundType
from app.realtime import deps as realtime_deps
from app.schemas.intake import IntakeInviteSettings, IntakeOnboarding, WorkAuthorizationPolicy
from app.schemas.policies import OrganizationSettings, PolicySnapshot
from app.schemas.rounds import RoundConfig
from app.services.intake import capture_store
from app.services.intake.kit import load_intake_kit
from app.services.intake.turn_schema import IntakeTurn, VerificationGrade
from app.services.interview.adapters import CodingPort, ScoringPort
from app.services.interview.config import EngineConfig
from app.services.interview.contracts import (
    ActivityCapture,
    ActivityEnded,
    ActivityEvent,
    ActivityPhaseChanged,
    ActivityStarted,
    CandidateTurn,
    SessionClosing,
    UtteranceMeta,
)
from app.services.interview.engine import InterviewEngine, set_engine
from app.services.interview.scoring_fallback import FallbackScoring
from app.services.interview.sessions import SessionServiceImpl

ONBOARDING: dict[str, Any] = {
    "full_name": "Priya Raman",
    "target_role": "ai_ml",
    "open_to_ai_ml": "yes",
    "technical_background": "Three years of data analysis with SQL and Python dashboards.",
    "primary_language": "python",
    "self_rating": 7,
    "scripting": "some",
    "scripting_details": "pandas scripts for reports",
    "data_ml_libraries": "pandas, scikit-learn",
    "years_hands_on": 3,
    "experience_level_comfort": "yes",
    "training_commitment": "full",
    "weekly_hours": 30,
    "availability": "within_2_weeks",
    "accuracy_confirmed": True,
}

PROFILE: dict[str, Any] = {
    "candidate_name": "Priya Raman",
    "headline": "Data analyst",
    "experience_years": 3,
    "roles": [
        {
            "title": "Data Analyst",
            "company": "Acme Retail",
            "start": "2022",
            "end": "present",
            "technologies": ["SQL", "Python", "Tableau"],
        }
    ],
    "projects": [
        {"name": "Churn model", "technologies": ["scikit-learn"], "outcomes": ["Flagged 200 accounts"]}
    ],
    "skills": {"programming_language": [{"name": "Python"}], "ai_ml": [{"name": "scikit-learn"}]},
    "claims_to_verify": [
        {
            "id": "c1",
            "claim": "Built weekly sales dashboards in Tableau",
            "category": "technical",
            "priority": 5,
        },
        {"id": "c2", "claim": "Cut report preparation time by 40%", "category": "impact", "priority": 4},
        {"id": "c3", "claim": "Led a churn analysis project", "category": "leadership", "priority": 2},
    ],
}

APPROVED_INFO = [
    {"topic": "Duration", "answer": "The training program lasts twelve weeks."},
    {"topic": "Schedule", "answer": "Sessions run on weekday mornings."},
]

LONG_ANSWER = (
    "I work as a data analyst at a retail company where I build weekly dashboards, clean data with Python, and "
    "explain the numbers to the sales managers so they can plan their stock and promotions for the next weeks."
)


@dataclass
class IntakeWorld:
    organization_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    candidate_id: uuid.UUID
    interview_id: uuid.UUID
    intake_id: uuid.UUID
    invite_token: str


def intake_round_config(**overrides: Any) -> dict[str, Any]:
    return RoundConfig(round_type=RoundType.INTAKE, name="Intake Interview", **overrides).model_dump(
        mode="json"
    )


async def build_intake_world(
    *,
    minutes: int = 45,
    onboarding: dict[str, Any] | None = None,
    work_authorization: bool = False,
    eligibility: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = PROFILE,
    approved_info: list[dict[str, Any]] | None = None,
    round_intake: dict[str, Any] | None = None,
    consent_audio: bool = True,
    policy: PolicySnapshot | None = None,
) -> IntakeWorld:
    suffix = secrets.token_hex(4)
    kit = load_intake_kit()
    token, token_hash = generate_invite_token()
    async with session_scope() as db:
        org = Organization(
            name=f"Org {suffix}",
            slug=f"org-{suffix}",
            is_active=True,
            settings=OrganizationSettings().model_dump(mode="json"),
        )
        db.add(org)
        await db.flush()
        job = Job(organization_id=org.id, title="AI/ML Training Program — Intake", status="open")
        candidate = Candidate(
            organization_id=org.id, full_name="Priya Raman", email=f"priya-{suffix}@example.test"
        )
        db.add_all([job, candidate])
        await db.flush()
        config = intake_round_config(duration_minutes=45, intake=round_intake or {})
        rnd = Round(
            job_id=job.id,
            name="Intake Interview",
            round_type="intake",
            duration_minutes=45,
            max_duration_minutes=50,
            config=config,
        )
        db.add(rnd)
        await db.flush()
        interview = Interview(
            organization_id=org.id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=rnd.id,
            status="invited",
            invite_token_hash=token_hash,
            invite_expires_at=datetime.now(UTC) + timedelta(days=3),
            policy_snapshot=(policy or PolicySnapshot()).model_dump(mode="json"),
        )
        db.add(interview)
        await db.flush()
        if profile is not None:
            resume = CandidateResume(
                candidate_id=candidate.id,
                organization_id=org.id,
                interview_id=interview.id,
                storage_key=f"orgs/{org.id}/r-{suffix}.pdf",
                original_filename="cv.pdf",
                content_type="application/pdf",
                size_bytes=10,
                sha256=secrets.token_hex(32),
                status="ready",
            )
            db.add(resume)
            await db.flush()
            db.add(
                CandidateProfile(
                    candidate_id=candidate.id,
                    resume_id=resume.id,
                    job_id=job.id,
                    profile={**profile, "candidate_id": str(candidate.id)},
                    status="ready",
                )
            )
        db.add(
            Consent(
                interview_id=interview.id,
                candidate_id=candidate.id,
                policy_version="2026-10-01",
                policy_hash="h" * 64,
                items={"microphone": True, "audio_recording": consent_audio, "transcript": True},
            )
        )
        wa = WorkAuthorizationPolicy(
            enabled=work_authorization, country="Canada" if work_authorization else None
        )
        settings = IntakeInviteSettings(
            duration_minutes=minutes,
            work_authorization=wa,
            program_name="AI/ML Training Program",
            kit_version=kit.version,
            kit_sha256=kit.source.sha256,
        )
        submitted = IntakeOnboarding.model_validate(
            {**(onboarding or ONBOARDING), "submitted_at": datetime.now(UTC).isoformat()}
        )
        intake = IntakeInterview(
            organization_id=org.id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=rnd.id,
            interview_id=interview.id,
            status="active",
            settings=settings.model_dump(mode="json"),
            onboarding=submitted.model_dump(mode="json"),
            onboarding_submitted_at=datetime.now(UTC),
            eligibility=eligibility,
            resume_confirmed_at=datetime.now(UTC),
            sent_at=datetime.now(UTC),
        )
        db.add(intake)
        db.add(
            IntakeOrgSettings(
                organization_id=org.id,
                program_name="AI/ML Training Program",
                approved_info=list(APPROVED_INFO if approved_info is None else approved_info),
                defaults={},
            )
        )
        await db.flush()
        return IntakeWorld(org.id, job.id, rnd.id, candidate.id, interview.id, intake.id, token)


# ======================================================================================= the model

_OFFER = re.compile(
    r"^- (follow_up|ask_item|clarify|answer_question|redirect) \(item_key ([\w.]+)\)", re.MULTILINE
)


@dataclass
class ScriptedIntakeModel:
    """TURN_DECISION responder for intake prompts (``prompt_version`` ``intake-turn@...``)."""

    follow_up: bool = False  # choose follow_up whenever it is offered
    calls: list[MockLLMCall] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    target_bullet: int = 2
    claim_signal: str | None = None
    speech: dict[str, str] = field(default_factory=dict)  # item key -> speech override

    def __call__(self, call: MockLLMCall) -> IntakeTurn:
        self.calls.append(call)
        user = str(call.messages[-1].get("content", ""))
        self.prompts.append("\n".join(str(m.get("content", "")) for m in call.messages))
        offers = _OFFER.findall(user)
        assert offers, "no offered moves in the intake prompt"
        moves = {move: key for move, key in offers}
        if self.follow_up and "follow_up" in moves:
            move = "follow_up"
        elif "ask_item" in moves:
            move = "ask_item"
        else:
            move = offers[0][0]
        key = moves[move]
        speech = self.speech.get(key) or f"Model question about {key.replace('.', ' ')}?"
        if move == "answer_question":
            speech = "The training program lasts twelve weeks. Is there anything else you'd like to ask?"
        return IntakeTurn(
            speech=speech,
            move=move,  # type: ignore[arg-type]
            item_key=key,
            answer_quality="adequate",
            claim_signal=self.claim_signal if key.startswith("resume.") else None,  # type: ignore[arg-type]
            target_bullet=self.target_bullet if key == "topic.weakest" else None,
            missing_specifics=[],
            reason=f"scripted {move}",
        )


def grade_responder(call: MockLLMCall) -> VerificationGrade:
    return VerificationGrade(quality="adequate", matched_key_points=[1, 2])


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest_asyncio.fixture
async def intake_env(db_session, redis, storage, mock_ai) -> AsyncIterator[dict[str, Any]]:
    clock = FakeClock()
    model = ScriptedIntakeModel()
    mock_ai.llm.set_default(ModelTask.TURN_DECISION, model)
    mock_ai.llm.set_default(ModelTask.ANSWER_ASSESSMENT, grade_responder)
    engines: list[InterviewEngine] = []

    def make_engine(*, real_clock: bool = False, **cfg: Any) -> InterviewEngine:
        base: dict[str, Any] = {
            "closing_reserve_s": 60.0,
            "default_interviewer_name": "Maya",
            "start_wait_for_plan_s": 0.2,
            "turn_first_speech_timeout_s": 3.0,
            "turn_total_timeout_s": 8.0,
            "postgres_flush_interval_s": 0.05,
        }
        base.update(cfg)
        engine = InterviewEngine(
            config=EngineConfig(**base),
            ai=mock_ai,
            scoring=ScoringPort(api=FallbackScoring(), recompute=None),
            coding=CodingPort(service=None, observer=None, source="test-none"),
            clock=None if real_clock else clock,
        )
        engines.append(engine)
        return engine

    engine = make_engine()
    previous = set_engine(engine)
    env = {
        "clock": clock,
        "model": model,
        "engine": engine,
        "make_engine": make_engine,
        "mock_ai": mock_ai,
        "sessions": SessionServiceImpl(
            config=EngineConfig(closing_reserve_s=60.0, default_interviewer_name="Maya")
        ),
    }
    try:
        with realtime_deps.override_providers(turn_engine=engine):
            yield env
    finally:
        set_engine(previous)
        for item in engines:
            await item.drain_background(5.0)
            await item.aclose()


async def create_session(env: dict[str, Any], world: IntakeWorld) -> str:
    async with session_scope() as db:
        row = await env["sessions"].create_or_resume(db, interview_id=world.interview_id)
        return str(row.id)


async def collect(stream: Any) -> list[Any]:
    return [event async for event in stream]


def metas(events: list[Any]) -> list[UtteranceMeta]:
    return [e for e in events if isinstance(e, UtteranceMeta)]


def texts(events: list[Any]) -> list[str]:
    return [m.full_text for m in metas(events)]


def item_keys(events: list[Any]) -> list[str | None]:
    return [((m.decision or {}).get("intake") or {}).get("item_key") for m in metas(events)]


def first(events: list[Any], cls: type) -> Any:
    return next((e for e in events if isinstance(e, cls)), None)


# ================================================================================ candidate + runtime

ANSWERS: dict[str, str] = {
    "mandatory.mq2": "AI/ML, because I like building models that help people decide.",
    "mandatory.mq3": "Most recently a pandas script that merges our weekly sales exports into one report.",
    "mandatory.mq3.verification": (
        "A list is an ordered sequence you index by position, a dictionary maps keys to values for fast lookup."
    ),
    "mandatory.mq3.rating_probe": "They would know decorators, generators and how to profile slow code properly.",
    "mandatory.mq4": "Yes, I am comfortable with that and I can commit to the training hours.",
    "close.questions": "No, I think you covered everything, thanks.",
    "close.anything_else": "No, that's all.",
}
CAPTURE_TEXTS: dict[str, str] = {
    "reading_aloud": (
        "Gradient descent is a way to find the lowest point of a function step by step. "
        "The learning rate controls how big each step is."
    ),
    "read_relay": "The passage explains that a model can learn the training data too closely and then fail on new data.",
    "topic_card": (
        "Working from home saves commuting time and helps focus, while the office helps collaboration. I would "
        "decide based on the kind of work, the team and personal discipline, and I prefer a hybrid week overall."
    ),
}


class Driver:
    """Plays the candidate (typed answers) and the realtime runtime (activity events, captures)."""

    def __init__(
        self, env: dict[str, Any], session_id: str, *, engine: InterviewEngine | None = None
    ) -> None:
        self.env = env
        self.sid = session_id
        self.engine = engine or env["engine"]
        self.clock: FakeClock = env["clock"]
        self.log: list[Any] = []

    async def elapsed(self) -> float:
        state = await self.engine.load_state(self.sid)
        return state.elapsed_seconds(self.clock())

    async def say(self, text: str, *, seconds: float = 20.0, typed: bool = True) -> list[Any]:
        start = await self.elapsed()
        self.clock.advance(seconds)
        turn = CandidateTurn(
            text=text, started_ms=int(start * 1000), ended_ms=int((start + seconds) * 1000), typed=typed
        )
        events = await collect(self.engine.handle_candidate_turn(self.sid, turn))
        self.log.extend(events)
        return events

    async def phase_event(self, kind: str, activity_id: str, phase: str, **detail: Any) -> list[Any]:
        events = await collect(
            self.engine.handle_activity_event(
                self.sid,
                ActivityEvent(
                    kind=kind,
                    activity_id=activity_id,
                    phase=phase,
                    elapsed_s=await self.elapsed(),
                    detail=detail,
                ),  # fmt: skip
            )
        )
        self.log.extend(events)
        return events

    async def capture(
        self,
        activity_id: str,
        kind: str,
        phase: str,
        text: str,
        *,
        seconds: float = 40.0,
        end_reason: str = "finish_button",
        typed: bool = False,
        settle: bool = True,
        audio: bytes | None = None,
    ) -> list[Any]:
        start = await self.elapsed()
        self.clock.advance(seconds)
        end = start + seconds
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()] if not typed else []
        span = (end - start) * 1000 / max(1, len(sentences))
        segments = [
            {
                "start_ms": int(start * 1000 + i * span),
                "end_ms": int(start * 1000 + (i + 1) * span - 400),
                "speech_ms": int(span - 500),
                "vad_prob": 0.93,
                "no_speech_prob": 0.02,
                "text": sentence,
            }
            for i, sentence in enumerate(sentences)
        ]
        pauses = [
            {"start_ms": s["end_ms"], "end_ms": s["end_ms"] + 400, "duration_ms": 400} for s in segments[:-1]
        ]
        turn = CandidateTurn(
            text=text,
            started_ms=int(start * 1000),
            ended_ms=int(end * 1000),
            typed=typed,
            audio_wav=audio,
            activity=ActivityCapture(
                activity_id=activity_id,
                kind=kind,  # type: ignore[arg-type]
                phase=phase,  # type: ignore[arg-type]
                end_reason=end_reason,  # type: ignore[arg-type]
                started_ms=int(start * 1000),
                ended_ms=int(end * 1000),
                voiced_ms=sum(s["speech_ms"] for s in segments),
                segments=segments,
                pauses=pauses,
                stt_confidence=None if typed else 0.95,
                typed=typed,
            ),
        )
        state = await self.engine.load_state(self.sid)
        attempt = state.intake.activity.attempt if state.intake and state.intake.activity else 1
        events = await collect(self.engine.handle_candidate_turn(self.sid, turn))
        self.log.extend(events)
        if settle and not typed and end_reason != "disconnect":
            # The live windowed final pass (runtime) would store this; simulate a finished pass.
            await capture_store.store_final_transcript(
                self.sid, activity_id, attempt=attempt, text_value=text, windows=[], source="final_pass_live"
            )
        return events

    async def run_activity(self, started: ActivityStarted, text: str | None = None) -> list[Any]:
        """Like the runtime: start the armed phase, end a non-capture phase at its deadline, capture."""
        data = started.activity
        activity_id, kind = data["activity_id"], data["kind"]
        phase = data["start_phase"]
        await self.phase_event(
            "phase_started", activity_id, phase, duration_ms=data["phases"][0]["duration_ms"]
        )
        if phase in ("read", "prepare") and kind != "reading_aloud":
            self.clock.advance(data["phases"][0]["duration_ms"] / 1000)
            events = await self.phase_event("phase_deadline", activity_id, phase)
            changed = first(events, ActivityPhaseChanged)
            assert changed is not None, events
            phase = changed.phase
            await self.phase_event("phase_started", activity_id, phase, duration_ms=changed.duration_ms)
        return await self.capture(activity_id, kind, phase, text or CAPTURE_TEXTS[kind])

    async def answer_for(self, events: list[Any]) -> list[Any]:
        """Answer the last question in ``events`` (or run the activity it armed)."""
        started = first(events, ActivityStarted)
        if started is not None:
            return await self.run_activity(started)
        meta = metas(events)[-1]
        key = ((meta.decision or {}).get("intake") or {}).get("item_key") or ""
        return await self.say(ANSWERS.get(key, LONG_ANSWER))

    async def run_to_end(self, events: list[Any], *, max_steps: int = 80) -> list[Any]:
        for _ in range(max_steps):
            if first(events, SessionClosing) is not None:
                return events
            events = await self.answer_for(events)
        raise AssertionError("the interview did not close")


async def stage_rows(session_id: str) -> dict[str, IntakeStageResult]:
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(IntakeStageResult).where(IntakeStageResult.session_id == uuid.UUID(session_id))
            )
        ).all()
    return {row.stage_key: row for row in rows}


async def activity_rows(session_id: str) -> dict[str, IntakeActivityResult]:
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(IntakeActivityResult).where(IntakeActivityResult.session_id == uuid.UUID(session_id))
            )
        ).all()
    return {row.kind: row for row in rows}


async def job_types(session_id: str) -> list[str]:
    async with session_scope() as db:
        rows = (await db.scalars(select(BackgroundJob))).all()
    return [row.type for row in rows if str((row.payload or {}).get("session_id")) == session_id]


__all__ = [
    "ANSWERS",
    "APPROVED_INFO",
    "CAPTURE_TEXTS",
    "LONG_ANSWER",
    "ONBOARDING",
    "PROFILE",
    "ActivityEnded",
    "Driver",
    "FakeClock",
    "IntakeWorld",
    "ScriptedIntakeModel",
    "activity_rows",
    "build_intake_world",
    "collect",
    "create_session",
    "first",
    "intake_env",
    "item_keys",
    "job_types",
    "metas",
    "stage_rows",
    "texts",
]
