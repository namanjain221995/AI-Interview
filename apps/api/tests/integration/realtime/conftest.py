"""Realtime integration fixtures: an in-process ASGI WebSocket client, a fake
TurnEngine, fake portal services, and database factories for sessions."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio

from app.core.security import generate_invite_token, hash_access_code, issue_ws_ticket
from app.db.models import Candidate, Consent, Interview, InterviewSession, Job, Round
from app.db.session import session_scope
from app.domain.enums import (
    AvatarState,
    IntegrityAction,
    IntegrityEventType,
    IntegritySeverity,
    RoundType,
    SessionEndReason,
    SessionStatus,
)
from app.realtime import deps
from app.realtime.acknowledgements import get_acknowledgement_cache
from app.realtime.audio.pcm import SAMPLE_RATE, bytes_to_samples, wav_to_pcm16
from app.realtime.registry import get_registry
from app.schemas.policies import PolicySnapshot
from app.schemas.realtime import CodingRunResultMsg, CodingStateMsg, pack_frame
from app.schemas.rounds import RoundConfig
from app.services.integrity.contracts import Disclosure, DisclosureItem, IntegrityOutcome
from app.services.interview.contracts import (
    BehaviorSpec,
    CandidateTurn,
    CodingActivity,
    CodingStarted,
    InterruptedUtterance,
    SessionClosing,
    SpeechDelta,
    StateChanged,
    TimerEvent,
    UtteranceBegin,
    UtteranceMeta,
)
from app.services.interview.state import InterviewState

pytestmark = pytest.mark.integration

AUDIO_DIR = Path(__file__).resolve().parents[5] / "tests" / "e2e" / "fixtures" / "audio"
CLIPS: dict[str, Any] = json.loads((AUDIO_DIR / "clips.json").read_text())


def load_clip(name: str) -> np.ndarray:
    pcm, rate = wav_to_pcm16((AUDIO_DIR / f"{name}.wav").read_bytes())
    assert rate == SAMPLE_RATE
    return bytes_to_samples(pcm)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.int16)


# =============================================================== WS harness


class AsgiWebSocket:
    """Minimal in-process WebSocket client speaking ASGI to the app."""

    def __init__(
        self, app: Any, path: str, *, query: str = "", headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        self.app = app
        self.path = path
        self.query = query
        self.headers = headers or []
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.accepted = False
        self.close_code: int | None = None
        self.task: asyncio.Task[None] | None = None
        self.messages: list[dict[str, Any]] = []
        self.binaries: list[tuple[float, bytes]] = []
        self.timeline: list[tuple[float, str, Any]] = []
        self._reader: asyncio.Task[None] | None = None
        self._new = asyncio.Event()
        self._audio_seq = 0

    async def connect(self, timeout: float = 10.0) -> AsgiWebSocket:  # noqa: ASYNC109
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": self.query.encode(),
            "root_path": "",
            "headers": [(b"host", b"testserver"), *self.headers],
            "client": ("127.0.0.1", 50001),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        self.task = asyncio.create_task(self.app(scope, self._receive, self._send))
        await self._to_app.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._from_app.get(), timeout)
        if first["type"] == "websocket.accept":
            self.accepted = True
            self._reader = asyncio.create_task(self._read())
        elif first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
        else:  # websocket.http.response.start (denial response)
            self.close_code = 403
        return self

    async def _receive(self) -> dict[str, Any]:
        return await self._to_app.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._from_app.put(message)

    async def _read(self) -> None:
        while True:
            message = await self._from_app.get()
            now = time.monotonic()
            kind = message["type"]
            if kind == "websocket.send":
                if message.get("text") is not None:
                    data = json.loads(message["text"])
                    self.messages.append(data)
                    self.timeline.append((now, data.get("type", "?"), data))
                elif message.get("bytes") is not None:
                    self.binaries.append((now, message["bytes"]))
                    self.timeline.append((now, "binary", message["bytes"]))
            elif kind == "websocket.close":
                self.close_code = message.get("code", 1000)
                self.timeline.append((now, "close", self.close_code))
                self._new.set()
                return
            self._new.set()

    # ---------------------------------------------------------------- send
    async def send_json(self, data: dict[str, Any]) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def send_audio(
        self, samples: np.ndarray, *, frame_ms: int = 100, realtime_factor: float = 0.0
    ) -> None:
        step = SAMPLE_RATE * frame_ms // 1000
        for i in range(0, samples.size, step):
            self._audio_seq += 1
            await self.send_bytes(
                pack_frame(0x01, self._audio_seq, samples[i : i + step].astype("<i2").tobytes())
            )
            await asyncio.sleep(frame_ms / 1000 * realtime_factor)

    async def hello(self, last_seq: int = 0, **client: Any) -> None:
        await self.send_json({"type": "hello", "protocol": 1, "last_seq": last_seq, "client": client})

    # ---------------------------------------------------------------- wait
    def of_type(self, *types: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m.get("type") in types]

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 10.0,  # noqa: ASYNC109
        after: int = 0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for message in self.messages[after:]:
                if predicate(message):
                    return message
            if self.close_code is not None and self._reader is not None and self._reader.done():
                raise AssertionError(
                    f"socket closed ({self.close_code}) while waiting; got {[m.get('type') for m in self.messages][-30:]}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timeout waiting; last messages: {[m.get('type') for m in self.messages][-30:]}"
                )
            self._new.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._new.wait(), min(remaining, 0.2))

    async def wait_type(
        self,
        msg_type: str,
        *,
        timeout: float = 10.0,  # noqa: ASYNC109
        after: int = 0,
        **fields: Any,
    ) -> dict[str, Any]:
        return await self.wait_for(
            lambda m: m.get("type") == msg_type and all(m.get(k) == v for k, v in fields.items()),
            timeout=timeout,
            after=after,
        )

    async def wait_closed(self, timeout: float = 10.0) -> int:  # noqa: ASYNC109
        deadline = time.monotonic() + timeout
        while self.close_code is None and time.monotonic() < deadline:
            self._new.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._new.wait(), 0.05)
        assert self.close_code is not None, "socket did not close"
        return self.close_code

    async def disconnect(self, code: int = 1001) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": code})
        if self.task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 10)
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader


# =============================================================== fake engine


class FakeEngine:
    """Scripted TurnEngine. Each reply is streamed word by word."""

    INTRO = "Hi Jane, I'm Maya. Thanks for joining. Let's start with your recent work on Kubernetes at Acme."

    def __init__(
        self,
        session_id: str,
        organization_id: str,
        interview_id: str,
        candidate_id: str,
        job_id: str,
        round_id: str,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.replies: list[str] = []
        self.default_reply = "Thanks. What was the approximate monthly spend before and after, and which change contributed most?"
        self.delta_delay = 0.002
        self.first_event_delay = 0.0
        self.start_delay = 0.0  # the engine is still deciding (e.g. waiting for preparation)
        self.turns: list[CandidateTurn] = []
        self.interruptions: list[InterruptedUtterance] = []
        self.delivered: list[tuple[str, str]] = []
        self.partials: list[str] = []
        self.coding_activities: list[CodingActivity] = []
        self.timers: list[TimerEvent] = []
        self.coding_payload: CodingStateMsg | None = None
        self.state = InterviewState(
            session_id=session_id,
            interview_id=interview_id,
            organization_id=organization_id,
            candidate_id=candidate_id,
            job_id=job_id,
            round_id=round_id,
            status=SessionStatus.READY,
            round_config=RoundConfig(round_type=RoundType.TECHNICAL_SCREENING, name="Technical Screening"),
            time_limit_s=1800,
            max_time_s=2400,
        )
        self.ended: SessionEndReason | None = None
        self._n = 0

    def _uid(self) -> str:
        self._n += 1
        return f"utt_{self._n}"

    async def _speak(self, text: str, kind: str = "question", question_id: str | None = None):
        uid = self._uid()
        behavior = BehaviorSpec(state=AvatarState.QUESTIONING if kind == "question" else AvatarState.SPEAKING)
        yield UtteranceBegin(utterance_id=uid, kind=kind, behavior=behavior, question_id=question_id)  # type: ignore[arg-type]
        words = text.split(" ")
        for i, word in enumerate(words):
            yield SpeechDelta(utterance_id=uid, text=word if i == 0 else " " + word)
            if self.delta_delay:
                await asyncio.sleep(self.delta_delay)
        yield UtteranceMeta(
            utterance_id=uid,
            question_id=question_id,
            behavior=behavior,
            decision={"action": "ask_question"},
            full_text=text,
        )

    async def prepare_session(self, session_id: str) -> None:
        self.calls.append(("prepare_session", session_id))

    async def load_state(self, session_id: str) -> InterviewState:
        return self.state

    async def start(self, session_id: str):
        self.calls.append(("start", session_id))
        if self.start_delay:
            await asyncio.sleep(self.start_delay)
        self.state.status = SessionStatus.LIVE
        self.state.resume()
        async for event in self._speak(self.INTRO, "intro", question_id="q1"):
            yield event

    async def resume(self, session_id: str, *, reconnect: bool):
        self.calls.append(("resume", reconnect))
        self.state.status = SessionStatus.LIVE
        self.state.resume()
        async for event in self._speak(
            "Sorry, we got disconnected for a moment. As I was asking, how large was the cluster?", "resume"
        ):
            yield event

    async def pause(self, session_id: str, *, reason: str) -> None:
        self.calls.append(("pause", reason))
        self.state.status = SessionStatus.PAUSED
        self.state.pause()

    async def on_partial_transcript(self, session_id: str, text: str, *, silence_started: bool) -> None:
        self.partials.append(text)

    async def handle_candidate_turn(self, session_id: str, turn: CandidateTurn):
        self.calls.append(("turn", turn.text))
        self.turns.append(turn)
        reply = self.replies.pop(0) if self.replies else self.default_reply
        if self.first_event_delay:
            delay, self.first_event_delay = self.first_event_delay, 0.0
            await asyncio.sleep(delay)  # slow model: the acknowledgement covers this
        if reply == "__coding__":
            async for event in self._speak(
                "Let's do a short coding exercise. The problem is on your screen.", "transition"
            ):
                yield event
            assert self.coding_payload is not None
            yield CodingStarted(coding=self.coding_payload)
            yield StateChanged(reason="coding_started")
            return
        if reply == "__close__":
            async for event in self._speak("Thank you, that's all for today.", "closing"):
                yield event
            yield SessionClosing(reason=SessionEndReason.SUFFICIENT_EVIDENCE)
            return
        async for event in self._speak(reply, question_id=f"q{len(self.turns) + 1}"):
            yield event

    async def handle_interruption(self, session_id: str, interrupted: InterruptedUtterance) -> None:
        self.calls.append(("interruption", interrupted.utterance_id))
        self.interruptions.append(interrupted)

    async def handle_backchannel_resume(self, session_id: str, interrupted: InterruptedUtterance):
        self.calls.append(("backchannel_resume", interrupted.utterance_id))
        async for event in self._speak("As I was saying, which change contributed most?", "question"):
            yield event

    async def repeat_question(self, session_id: str):
        self.calls.append(("repeat", None))
        async for event in self._speak("Of course. How large was the cluster you ran?", "question"):
            yield event

    async def handle_coding_activity(self, session_id: str, activity: CodingActivity):
        self.calls.append(("coding", activity.kind))
        self.coding_activities.append(activity)
        if activity.kind == "submitted":
            from app.services.interview.contracts import CodingEnded

            yield CodingEnded(coding_session_id=activity.coding_session_id, reason="submitted")
            async for event in self._speak(
                "Thanks for submitting. What is the time complexity of your solution?", "question"
            ):
                yield event
            return
        if activity.kind == "hint_requested":
            from app.services.interview.contracts import CodingHintIssued

            yield CodingHintIssued(
                coding_session_id=activity.coding_session_id,
                level=1,
                text="Think about a hash map.",
                hints_used=1,
                hints_remaining=2,
            )
            async for event in self._speak("Here's a hint: think about a hash map.", "hint"):
                yield event

    async def handle_timer(self, session_id: str, timer: TimerEvent):
        self.calls.append(("timer", timer.kind))
        self.timers.append(timer)
        if timer.kind == "silence_timeout":
            async for event in self._speak(
                "Take your time. Would you like me to repeat the question?", "comment"
            ):
                yield event

    async def end_session(self, session_id: str, reason: SessionEndReason):
        self.calls.append(("end_session", reason))
        self.ended = reason
        async for event in self._speak("Thanks so much for your time today, Jane. Goodbye.", "closing"):
            yield event
        yield SessionClosing(reason=reason)

    async def mark_utterance_delivered(self, session_id: str, utterance_id: str, *, spoken_text: str) -> None:
        self.calls.append(("delivered", utterance_id))
        self.delivered.append((utterance_id, spoken_text))


# ============================================================ fake services


class FakeIntegrity:
    def __init__(self) -> None:
        self.client_events: list[Any] = []
        self.server_events: list[tuple[IntegrityEventType, dict[str, Any]]] = []
        self.frames: list[dict[str, Any]] = []
        self.next_outcomes: list[IntegrityOutcome] = []

    async def record_client_events(self, session_id: str, events: list[Any]) -> list[IntegrityOutcome]:
        self.client_events.extend(events)
        outcomes, self.next_outcomes = self.next_outcomes, []
        return outcomes or [
            IntegrityOutcome(
                event_id=None,
                event_type=e.event_type,
                severity=IntegritySeverity.LOW,
                action=IntegrityAction.LOG_ONLY,
            )
            for e in events
        ]

    async def record_server_event(
        self, session_id: str, event_type: IntegrityEventType, *, details: dict[str, Any] | None = None
    ):
        self.server_events.append((event_type, details or {}))
        return None

    async def submit_frame(
        self, session_id: str, *, kind: str, image: bytes, content_type: str, captured_at: str
    ) -> str:
        self.frames.append(
            {"kind": kind, "bytes": len(image), "content_type": content_type, "captured_at": captured_at}
        )
        return str(uuid.uuid4())

    async def analyze_frame(self, session_id: str, media_id: str) -> list[IntegrityOutcome]:
        return []


class FakeCoding:
    def __init__(self) -> None:
        self.updates: list[tuple[str, int]] = []
        self.runs: list[tuple[str, str]] = []
        self.language_changes: list[str] = []
        self.next_results: list[CodingRunResultMsg] = []  # returned (in order) before the default

    async def record_update(self, coding_session_id: str, *, code: str, language, version: int) -> bool:
        self.updates.append((coding_session_id, version))
        return True

    async def run(
        self, coding_session_id: str, *, kind, code: str, language, stdin: str | None = None
    ) -> CodingRunResultMsg:
        self.runs.append((coding_session_id, kind.value))
        if self.next_results:
            return self.next_results.pop(0)
        return CodingRunResultMsg(
            coding_session_id=coding_session_id,
            run_id=str(uuid.uuid4()),
            kind=kind,
            status="passed",
            passed=2,
            total=2,
            runtime_ms=12,
            tests=[
                {"id": "t1", "passed": True, "actual": [0, 1]},
                {"id": "h1", "passed": True, "hidden": True, "actual": "secret", "error": None},
            ],
        )

    async def state_payload(self, coding_session_id: str) -> CodingStateMsg:
        return coding_payload(coding_session_id)

    async def change_language(self, coding_session_id: str, language) -> CodingStateMsg:
        self.language_changes.append(language.value)
        payload = coding_payload(coding_session_id)
        return payload.model_copy(update={"language": language, "code": "// starter\n"})


def coding_payload(coding_session_id: str = "cs1") -> CodingStateMsg:
    return CodingStateMsg.model_validate(
        {
            "coding_session_id": coding_session_id,
            "question": {
                "coding_question_id": "cq1",
                "title": "Two Sum",
                "prompt_markdown": "Return indices of two numbers adding up to target.",
                "difficulty": "easy",
                "languages": ["python"],
                "starter_code": {"python": "def two_sum(nums, target):\n    pass\n"},
                "visible_tests": [{"id": "t1", "args": [[2, 7], 9]}],
            },
            "language": "python",
            "time_limit_s": 1200,
        }
    )


def build_disclosure(
    policy_snapshot: dict[str, Any], *, organization_name: str, job_title: str
) -> Disclosure:
    snapshot = PolicySnapshot.model_validate(
        {k: v for k, v in policy_snapshot.items() if k in PolicySnapshot.model_fields}
    )
    items = [
        DisclosureItem(
            "microphone", "Microphone", "Your voice is used to conduct the interview.", True, True
        ),
        DisclosureItem("transcript", "Transcript", "Your answers are transcribed.", True, True),
        DisclosureItem(
            "ai_assessment", "AI assessment", "An AI evaluates your answers for a human reviewer.", True, True
        ),
        DisclosureItem(
            "audio_recording",
            "Answer audio",
            "Answer audio is stored.",
            False,
            snapshot.recording.record_answer_audio,
        ),
        DisclosureItem(
            "camera_frames",
            "Camera snapshots",
            "Periodic camera snapshots.",
            False,
            snapshot.integrity.camera_frame_analysis,
        ),
        DisclosureItem(
            "video_recording", "Video", "Video is recorded.", False, snapshot.recording.record_video
        ),
    ]
    text = json.dumps(
        {
            "org": organization_name,
            "job": job_title,
            "items": [i.key for i in items if i.enabled],
            "snapshot": policy_snapshot,
        },
        sort_keys=True,
    )
    return Disclosure(
        policy_version=snapshot.policy_version,
        policy_hash=hashlib.sha256(text.encode()).hexdigest(),
        title=f"{organization_name}: interview information",
        intro=f"Before your {job_title} interview, please review how it works.",
        items=items,
        retention_summary="Recordings are deleted after 90 days.",
        purpose="To assess job-relevant skills.",
        human_review_statement="A human makes every hiring decision.",
        limitations_statement="Browser fullscreen cannot lock your operating system.",
    )


class FakeSessions:
    def __init__(self) -> None:
        self.created: list[uuid.UUID] = []

    async def create_or_resume(
        self, db: Any, *, interview_id: Any, round_id: Any | None = None
    ) -> InterviewSession:
        from sqlalchemy import select

        existing = await db.scalar(
            select(InterviewSession).where(
                InterviewSession.interview_id == interview_id,
                InterviewSession.status.in_(["created", "preparing", "ready", "live", "paused"]),
            )
        )
        if existing is not None:
            return existing
        interview = await db.get(Interview, interview_id)
        round_ = await db.get(Round, round_id) if round_id else None
        row = InterviewSession(
            interview_id=interview.id,
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=round_.id if round_ else round_id,
            status="created",
            round_config=round_.config if round_ else {},
        )
        db.add(row)
        await db.flush()
        self.created.append(row.id)
        return row

    async def candidate_view(self, db: Any, session_id: Any) -> dict[str, Any]:
        return {"status": "created", "coding_enabled": False, "recommendation": "should never leak"}


@dataclass
class FakeResume:
    uploads: list[dict[str, Any]] = field(default_factory=list)

    async def accept_upload(self, db: Any, **kwargs: Any) -> Any:
        from app.db.models import CandidateResume

        self.uploads.append(kwargs)
        row = CandidateResume(
            candidate_id=kwargs["candidate_id"],
            organization_id=kwargs["organization_id"],
            interview_id=kwargs["interview_id"],
            storage_key=f"orgs/{kwargs['organization_id']}/resumes/{uuid.uuid4()}/cv.pdf",
            original_filename=kwargs["filename"],
            content_type="application/pdf",
            size_bytes=len(kwargs["data"]),
            sha256=hashlib.sha256(kwargs["data"]).hexdigest(),
            status="uploaded",
        )
        db.add(row)
        await db.flush()
        return row

    async def progress(self, db: Any, resume_id: uuid.UUID) -> Any:
        from app.services.resume.contracts import ResumeProgress

        return ResumeProgress(
            resume_id=str(resume_id),
            status="extracting",
            steps=[{"key": "extract", "label": "Reading", "state": "active"}],
        )

    async def latest_profile(self, db: Any, candidate_id: uuid.UUID, job_id: uuid.UUID | None = None) -> Any:
        return None

    def candidate_safe_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {"headline": profile.get("headline")}


# ================================================================ factories


@dataclass
class SessionEnv:
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    interview_id: uuid.UUID
    session_id: uuid.UUID
    invite_token: str
    engine: FakeEngine
    integrity: FakeIntegrity
    coding: FakeCoding

    async def ticket(self) -> str:
        return await issue_ws_ticket(self.session_id, self.candidate_id, self.interview_id)


DEFAULT_POLICY: dict[str, Any] = PolicySnapshot().model_dump(mode="json")


async def create_interview(
    org_id: uuid.UUID,
    *,
    policy: dict[str, Any] | None = None,
    consent_items: dict[str, Any] | None = None,
    with_session: bool = True,
    session_status: str = "ready",
    allow_round_selection: bool = False,
    access_code: str | None = None,
    expires_in: timedelta = timedelta(days=7),
    interview_status: str = "invited",
) -> dict[str, Any]:
    token, token_hash = generate_invite_token()
    config = RoundConfig(
        round_type=RoundType.TECHNICAL_SCREENING, name="Technical Screening", candidate_selectable=True
    )
    async with session_scope() as db:
        job = Job(
            organization_id=org_id,
            title="Senior DevOps Engineer",
            description="Run the platform.",
            status="open",
        )
        candidate = Candidate(
            organization_id=org_id, full_name="Jane Doe", email=f"jane-{secrets.token_hex(4)}@example.test"
        )
        db.add_all([job, candidate])
        await db.flush()
        round_ = Round(
            job_id=job.id,
            name="Technical Screening",
            round_type=RoundType.TECHNICAL_SCREENING.value,
            candidate_selectable=True,
            config=config.model_dump(mode="json"),
        )
        other_round = Round(
            job_id=job.id,
            name="Behavioral",
            round_type=RoundType.BEHAVIORAL.value,
            sequence=2,
            candidate_selectable=False,
            config=RoundConfig(round_type=RoundType.BEHAVIORAL, name="Behavioral").model_dump(mode="json"),
        )
        db.add_all([round_, other_round])
        await db.flush()
        interview = Interview(
            organization_id=org_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=None if allow_round_selection else round_.id,
            allow_round_selection=allow_round_selection,
            status=interview_status,
            invite_token_hash=token_hash,
            invite_expires_at=datetime.now(UTC) + expires_in,
            access_code_hash=hash_access_code(access_code) if access_code else None,
            policy_snapshot=policy or DEFAULT_POLICY,
        )
        db.add(interview)
        await db.flush()
        if consent_items is not None:
            db.add(
                Consent(
                    interview_id=interview.id,
                    candidate_id=candidate.id,
                    policy_version="2026-10-01",
                    policy_hash="0" * 64,
                    items=consent_items,
                )
            )
        session_id = None
        if with_session:
            row = InterviewSession(
                interview_id=interview.id,
                organization_id=org_id,
                candidate_id=candidate.id,
                job_id=job.id,
                round_id=round_.id,
                status=session_status,
                round_config=config.model_dump(mode="json"),
            )
            db.add(row)
            await db.flush()
            session_id = row.id
        return {
            "organization_id": org_id,
            "candidate_id": candidate.id,
            "candidate_email": candidate.email,
            "job_id": job.id,
            "round_id": round_.id,
            "other_round_id": other_round.id,
            "interview_id": interview.id,
            "session_id": session_id,
            "invite_token": token,
        }


FULL_CONSENT = {
    "microphone": True,
    "transcript": True,
    "ai_assessment": True,
    "audio_recording": True,
    "camera_frames": True,
    "video_recording": True,
    "camera": True,
    "integrity_monitoring": True,
}


@pytest_asyncio.fixture
async def realtime_env(app, org_factory, mock_ai, settings, monkeypatch) -> AsyncIterator[Callable[..., Any]]:
    """``env = await realtime_env(policy=..., consent=...)`` -> SessionEnv with fakes installed.

    Tests stream recorded audio much faster than real time, so the
    per-connection audio byte budget is relaxed (message limits stay real)."""
    from app.realtime import router as router_module
    from app.realtime.limits import ConnectionLimits, TokenBucket

    monkeypatch.setattr(
        router_module,
        "make_limits",
        lambda: ConnectionLimits(frames=TokenBucket(10_000, 10_000), audio_bytes=TokenBucket(10**8, 10**8)),
    )
    settings.ws_disconnect_grace_seconds = 3
    mock_ai.tts.ms_per_char = 20
    stack = contextlib.ExitStack()
    created: list[SessionEnv] = []

    async def make(
        *,
        policy: dict[str, Any] | None = None,
        consent: dict[str, Any] | None = FULL_CONSENT,
        session_status: str = "ready",
    ) -> SessionEnv:
        org_id = await org_factory()
        data = await create_interview(
            org_id, policy=policy, consent_items=consent, session_status=session_status
        )
        engine = FakeEngine(
            str(data["session_id"]),
            str(org_id),
            str(data["interview_id"]),
            str(data["candidate_id"]),
            str(data["job_id"]),
            str(data["round_id"]),
        )
        integrity, coding = FakeIntegrity(), FakeCoding()
        stack.enter_context(
            deps.override_providers(turn_engine=engine, integrity_service=integrity, coding_service=coding)
        )
        env = SessionEnv(
            organization_id=org_id,
            candidate_id=data["candidate_id"],
            job_id=data["job_id"],
            round_id=data["round_id"],
            interview_id=data["interview_id"],
            session_id=data["session_id"],
            invite_token=data["invite_token"],
            engine=engine,
            integrity=integrity,
            coding=coding,
        )
        created.append(env)
        await get_acknowledgement_cache().warm(mock_ai.tts, settings.tts_voice)
        return env

    try:
        yield make
    finally:
        await get_registry().shutdown_all()
        stack.close()


@pytest_asyncio.fixture
async def ws_connect(app) -> AsyncIterator[Callable[..., Any]]:
    opened: list[AsgiWebSocket] = []

    async def connect(
        session_id: uuid.UUID | str, ticket: str | None, *, origin: str | None = "http://testserver"
    ) -> AsgiWebSocket:
        headers = [(b"origin", origin.encode())] if origin else []
        query = f"ticket={ticket}" if ticket else ""
        ws = AsgiWebSocket(app, f"/ws/sessions/{session_id}", query=query, headers=headers)
        opened.append(ws)
        return await ws.connect()

    yield connect
    for ws in opened:
        if ws.task is not None and not ws.task.done():
            await ws.disconnect()


@pytest.fixture
def portal_fakes() -> Any:
    """Fake session + resume services; the real disclosure builder and the real
    integrity service (app.services.integrity) stay in place."""
    sessions, resume = FakeSessions(), FakeResume()
    with deps.override_providers(session_service=sessions, resume_service=resume):
        yield {"sessions": sessions, "resume": resume}


@pytest.fixture
def fake_integrity() -> Any:
    integrity = FakeIntegrity()
    with deps.override_providers(integrity_service=integrity):
        yield integrity
