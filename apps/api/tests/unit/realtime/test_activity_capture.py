"""Intake activity capture mode of the realtime runtime (INTAKE_INTERVIEW.md 7.2-7.11): content only in the
phase message that may show it, server deadlines (Drill B hidden at 30 s), one capture across speech bursts, VAD
pauses in session ms, silence rules, deadline grace, finish button, typed rules, disconnect, non-replayable
messages, content-free ``session.state``, long-answer endpointing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.domain.enums import SessionStatus, TurnState
from app.realtime.audio.segmenter import AudioSegmenter, SpeechEnded, SpeechStarted
from app.realtime.context import SessionContext
from app.realtime.outbox import NON_REPLAYABLE_TYPES, Outbox
from app.realtime.policy import SessionPolicy
from app.realtime.runtime import (
    ACTIVITY_SILENCE,
    EAGER_SEGMENT_AFTER_MS,
    EAGER_SEGMENT_PAUSE_MS,
    ActiveUtterance,
    SessionRuntime,
)
from app.schemas.policies import PolicySnapshot
from app.schemas.realtime import (
    ActivityEndMsg,
    ActivityPhaseMsg,
    ActivityStartMsg,
    ErrorMsg,
    SessionStateMsg,
    pack_frame,
)
from app.schemas.rounds import RoundConfig
from app.services.intake import stages as S
from app.services.intake.activities import descriptor
from app.services.intake.kit import load_intake_kit
from app.services.intake.state import IntakeActivityState, IntakeState
from app.services.interview.contracts import (
    ActivityEnded,
    ActivityPhaseChanged,
    ActivityStarted,
    BehaviorSpec,
)
from app.services.interview.state import InterviewState

from .conftest import ScriptedVad, tone

SAMPLE_RATE = 16000


async def _empty():
    return
    yield  # pragma: no cover


class FakeEngine:
    def __init__(self) -> None:
        self.turns: list[Any] = []
        self.events: list[Any] = []
        self.delivered: list[str] = []
        self.state: Any = None

    async def load_state(self, session_id: str) -> Any:
        return self.state

    def handle_activity_event(self, session_id: str, event: Any):
        self.events.append(event)
        return _empty()

    def handle_candidate_turn(self, session_id: str, turn: Any):
        self.turns.append(turn)
        return _empty()

    async def mark_utterance_delivered(self, session_id: str, utterance_id: str, *, spoken_text: str) -> None:
        self.delivered.append(utterance_id)

    async def pause(self, session_id: str, *, reason: str) -> None:
        return None

    async def handle_interruption(self, session_id: str, interrupted: Any) -> None:
        return None


class FakeLock:
    def start_refresh(self, on_lost: Any) -> None:
        return None

    async def release(self) -> None:
        return None


class FakeTransport:
    async def send_text(self, text: str) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def close(self, code: int) -> None:
        return None


class Harness:
    def __init__(self, runtime: SessionRuntime, engine: FakeEngine) -> None:
        self.rt = runtime
        self.engine = engine
        self.messages: list[Any] = []
        self.seq = 0

    def of(self, cls: type) -> list[Any]:
        return [m for m in self.messages if isinstance(m, cls)]

    async def audio(self, samples: np.ndarray, frame_ms: int = 100) -> None:
        step = SAMPLE_RATE * frame_ms // 1000
        for i in range(0, samples.size, step):
            self.seq += 1
            await self.rt.handle_audio(
                pack_frame(0x01, self.seq, samples[i : i + step].astype("<i2").tobytes())
            )

    async def run_jobs(self, kind: str | None = None) -> int:
        ran = 0
        for _ in range(20):
            jobs = [j for j in self.rt._jobs if kind is None or j.kind == kind]
            if not jobs:
                break
            job = jobs[0]
            self.rt._jobs.remove(job)
            await job.run()
            ran += 1
        return ran

    def advance(self, seconds: float) -> None:
        self.rt.clock.elapsed_before += seconds


def speech_fn(intervals: list[tuple[int, int]]):
    def fn(t_ms: float) -> float:
        return 0.95 if any(a <= t_ms < b for a, b in intervals) else 0.02

    return fn


@pytest_asyncio.fixture
async def harness(mock_ai) -> Harness:
    settings = get_settings()
    snapshot = PolicySnapshot()
    policy = SessionPolicy(
        snapshot=snapshot, consent={"audio_recording": True, "microphone": True}, consented=True
    )
    ctx = SessionContext(
        session_id=str(uuid.uuid4()),
        interview_id=str(uuid.uuid4()),
        organization_id=str(uuid.uuid4()),
        candidate_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        round_id=str(uuid.uuid4()),
        status=SessionStatus.LIVE,
        round_name="Intake Interview",
        round_type="intake",
        time_limit_s=2700,
        max_time_s=3000,
        policy=policy,
        invite_expires_at=datetime.now(UTC) + timedelta(days=1),
        interview_status="in_progress",
    )
    engine = FakeEngine()
    runtime = SessionRuntime(
        ctx, settings=settings, engine=engine, coding=None, integrity=None, lock=FakeLock(), redis=None
    )  # type: ignore[arg-type]
    h = Harness(runtime, engine)

    def post(message: Any, *, binary: bytes | None = None) -> asyncio.Future[None]:
        message.seq = runtime.outbox.next_seq()
        h.messages.append(message)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    runtime.post = post  # type: ignore[method-assign]
    runtime.transport = FakeTransport()  # type: ignore[assignment]
    runtime.started = True
    runtime.clock.start()
    runtime.turns.to(TurnState.LISTENING, "test")
    mock_ai.stt.default_text = "the passage words"
    return h


def use_vad(h: Harness, intervals: list[tuple[int, int]]) -> None:
    settings = get_settings()
    h.rt.segmenter = AudioSegmenter(
        ScriptedVad(speech_fn(intervals)),
        speech_threshold=settings.vad_speech_threshold,
        barge_in_threshold=settings.vad_barge_in_threshold,
        barge_in_min_speech_ms=settings.barge_in_min_speech_ms,
        segment_pause_ms=settings.segment_pause_ms,
        eager_pause_ms=EAGER_SEGMENT_PAUSE_MS,
        eager_after_ms=EAGER_SEGMENT_AFTER_MS,
        max_segment_ms=6000,
    )


def arm(h: Harness, kind: str, material: str, *, start_after: str | None = None, **kw: Any) -> str:
    activity_id = str(uuid.uuid4())
    data = descriptor(load_intake_kit(), activity_id=activity_id, kind=kind, material_key=material, **kw)
    h.rt._on_activity_started(ActivityStarted(activity=data, start_after_utterance_id=start_after))
    return activity_id


async def stop_timer(h: Harness) -> None:
    act = h.rt.activity
    if act is not None and act.timer is not None:
        act.timer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await act.timer
        act.timer = None


# ================================================================================ protocol + secrecy


def test_activity_messages_are_never_replayed() -> None:
    assert {"activity.start", "activity.phase", "activity.end"} <= NON_REPLAYABLE_TYPES
    outbox = Outbox("s", None)
    outbox.record(
        1, '{"seq":1,"type":"activity.phase","content":{"paragraphs":["secret"]}}', msg_type="activity.phase"
    )
    outbox.record(2, '{"seq":2,"type":"notice"}', msg_type="notice")
    assert [seq for seq, _ in outbox._memory] == [2]


async def test_start_is_content_free_and_the_read_phase_carries_the_passage(harness: Harness) -> None:
    h = harness
    utt = ActiveUtterance(utterance_id="u1", kind="question", behavior=BehaviorSpec())
    h.rt.utterances["u1"] = utt
    activity_id = arm(h, "read_relay", "C", start_after="u1")
    start = h.of(ActivityStartMsg)[0]
    assert "paragraphs" not in start.model_dump_json() and start.status == "armed"
    assert [p.phase for p in start.phases] == ["read", "relay"] and start.phases[0].duration_ms == 30_000
    assert h.of(ActivityPhaseMsg) == []  # nothing visible before the instruction is delivered
    utt.delivered = True
    h.rt._on_delivered(utt)
    phase = h.of(ActivityPhaseMsg)[0]
    assert (phase.activity_id, phase.phase, phase.status, phase.capture) == (
        activity_id,
        "read",
        "running",
        False,
    )
    assert phase.remaining_ms == 30_000 and phase.can_finish is False
    assert phase.content is not None and phase.content.paragraphs == list(
        load_intake_kit().passage("C").paragraphs
    )
    assert h.rt._capture is None  # no capture while reading silently
    await h.run_jobs("activity")
    assert [(e.kind, e.phase) for e in h.engine.events] == [("phase_started", "read")]
    await stop_timer(h)


async def test_drill_b_passage_is_hidden_at_the_server_deadline_and_dropped(harness: Harness) -> None:
    h = harness
    arm(h, "read_relay", "C")
    assert h.rt.activity is not None and h.rt.activity.status == "running"
    h.advance(29.0)
    await asyncio.sleep(0.05)
    assert h.rt.activity.status == "running"
    h.advance(1.05)
    await asyncio.sleep(0.4)
    ended = [m for m in h.of(ActivityPhaseMsg) if m.status == "ended"]
    assert (
        len(ended) == 1
        and ended[0].phase == "read"
        and ended[0].hide_content is True
        and ended[0].content is None
    )
    act = h.rt.activity
    assert act.content is None and act.status == "waiting" and 29_900 <= act.shown_ms <= 30_500
    await h.run_jobs("activity")
    assert [(e.kind, e.phase) for e in h.engine.events][-1] == ("phase_deadline", "read")
    # The engine arms the relay: it starts on delivery, captures, and never shows the passage again.
    h.rt._on_activity_phase_changed(
        ActivityPhaseChanged(
            activity_id=act.activity_id, kind="read_relay", phase="relay", duration_ms=150_000, capture=True,
            can_finish=True, content=None, hide_content=True, start_after_utterance_id=None,
        )
    )  # fmt: skip
    relay = [m for m in h.of(ActivityPhaseMsg) if m.phase == "relay"][0]
    assert relay.status == "running" and relay.capture and relay.content is None and relay.hide_content
    assert h.rt._capture is act.capture is not None and h.rt.turns.state is TurnState.CANDIDATE_SPEAKING
    await stop_timer(h)


async def test_reshown_read_phase_never_exceeds_thirty_seconds_in_total(harness: Harness) -> None:
    h = harness
    activity_id = arm(h, "read_relay", "A")
    act = h.rt.activity
    h.advance(20.0)
    await h.rt._activity_detached()
    assert act.status == "interrupted" and 19_900 <= act.shown_ms <= 20_400 and act.content is None
    # Resume: the engine asks to re-show for the remainder; the runtime caps it by what was already shown.
    data = descriptor(load_intake_kit(), activity_id=activity_id, kind="read_relay", material_key="A",
                      resumed=True, start_phase="read", start_duration_ms=25_000)  # fmt: skip
    h.rt._on_activity_started(ActivityStarted(activity=data, start_after_utterance_id=None))
    phase = [m for m in h.of(ActivityPhaseMsg) if m.status == "running"][-1]
    assert phase.phase == "read" and phase.remaining_ms <= 10_100 and phase.content is not None
    await stop_timer(h)


async def test_session_state_intake_snapshot_has_no_content(harness: Harness) -> None:
    h = harness
    ik = IntakeState(intake_id="i", kit_version="v1", kit_sha256="x", time_limit_s=2700)
    ik.stages = S.build_stages(2700, claims=[{"ref": "C1"}])
    S.mark_asked(ik, "greeting.intro", 0.0)
    ik.activity = IntakeActivityState(
        activity_id="a", kind="read_relay", phase="read", status="running", material_key="C"
    )
    state = InterviewState(
        session_id=h.rt.session_id, interview_id="i", organization_id="o", candidate_id="c", job_id="j", round_id="r",
        round_config=RoundConfig(round_type="intake", name="Intake"), intake=ik,
    )  # fmt: skip
    h.engine.state = state
    await h.rt._refresh_state_cache()
    arm(h, "read_relay", "C")
    msg: SessionStateMsg = await h.rt.build_state()
    dumped = msg.model_dump_json()
    assert (
        msg.intake is not None and msg.intake.stage == "greeting" and msg.intake.stage_title == "Introduction"
    )
    assert [s.status for s in msg.intake.stages][:2] == ["current", "pending"]
    assert msg.intake.activity is not None and msg.intake.activity.status == "running"
    assert 0 < (msg.intake.activity.remaining_ms or 0) <= 30_000
    assert msg.intake.answer_mode == "long" and msg.intake.soft_limit_s == 90
    for paragraph in load_intake_kit().passage("C").paragraphs:
        assert paragraph[:30] not in dumped
    await stop_timer(h)


# =================================================================================== capture mode


async def test_capture_spans_speech_bursts_and_records_pauses_in_session_ms(harness: Harness) -> None:
    h = harness
    # speech 0-2 s, pause 2-2.9 s (900 ms), speech 2.9-5 s, short gap 5-5.2 s (200 ms, not a pause), speech 5.2-7 s
    use_vad(h, [(0, 2000), (2900, 5000), (5200, 7000)])
    h.advance(100.0)  # the interview clock is at ~100 s when the reading starts
    arm(h, "reading_aloud", "B")
    act = h.rt.activity
    capture = act.capture
    assert capture is not None and h.rt._capture is capture
    await h.audio(tone(7000))
    await h.audio(np.zeros(SAMPLE_RATE * 1, dtype=np.int16))
    assert h.rt._capture is capture and not act.ending  # no end of turn inside the reading
    assert len(act.pauses) == 1
    pause = act.pauses[0]
    assert 850 <= pause["duration_ms"] <= 1000
    assert 100_000 + 1800 <= pause["start_ms"] <= 100_000 + 2300  # session clock, not the audio clock
    assert h.of(ErrorMsg) == []
    await stop_timer(h)


@pytest.mark.parametrize(
    ("kind", "material", "phase_after", "silence_ms", "words", "ends"),
    [
        ("reading_aloud", "B", None, 8100, 5, True),  # any speech + 8 s
        ("reading_aloud", "B", None, 4100, 5, False),  # 4 s needs 70 % of the passage
        ("reading_aloud", "B", None, 4100, 90, True),
        ("read_relay", "C", "relay", 6100, 10, False),  # relay needs 15 words
        ("read_relay", "C", "relay", 6100, 16, True),
        ("topic_card", "1", "speak", 9000, 40, False),
        ("topic_card", "1", "speak", 10100, 31, True),
    ],
)
async def test_silence_rules(harness: Harness, kind, material, phase_after, silence_ms, words, ends) -> None:
    h = harness
    use_vad(h, [])
    arm(h, kind, material)
    act = h.rt.activity
    if phase_after is not None:
        act.status = "running"
        await h.rt._end_noncapture_phase(act, "phase_deadline")
        h.rt._on_activity_phase_changed(
            ActivityPhaseChanged(
                activity_id=act.activity_id, kind=kind, phase=phase_after, duration_ms=120_000, capture=True,
                can_finish=True, content=None, hide_content=kind == "read_relay", start_after_utterance_id=None,
            )
        )  # fmt: skip
    capture = act.capture
    assert capture is not None
    capture.transcriber.partial_text = " ".join(["word"] * words)
    act.spoken = True
    h.rt.segmenter._processed = SAMPLE_RATE * (silence_ms // 1000 + 1)
    h.rt.segmenter._last_speech_end = h.rt.segmenter._processed - SAMPLE_RATE * silence_ms // 1000
    await h.rt._check_endpoint()
    assert act.ending is ends
    if ends:
        assert any(j.kind == "turn" for j in h.rt._jobs)
    await stop_timer(h)


async def test_no_silence_end_before_the_candidate_has_spoken(harness: Harness) -> None:
    h = harness
    use_vad(h, [])
    arm(h, "reading_aloud", "B")
    act = h.rt.activity
    await h.audio(np.zeros(SAMPLE_RATE * 12, dtype=np.int16))
    assert not act.ending and h.rt._capture is act.capture
    assert set(ACTIVITY_SILENCE) == {
        ("reading_aloud", "read"),
        ("read_relay", "relay"),
        ("topic_card", "speak"),
    }
    await stop_timer(h)


async def test_deadline_waits_for_a_pause_at_most_three_seconds(harness: Harness, monkeypatch) -> None:
    import app.realtime.runtime as rt_mod

    monkeypatch.setattr(rt_mod, "ACTIVITY_GRACE_MAX_S", 0.3)
    h = harness
    use_vad(h, [(0, 60_000)])
    arm(h, "topic_card", "2", start_phase="speak")
    act = h.rt.activity
    await h.audio(tone(2000))  # speaking now
    assert h.rt.segmenter.in_speech
    started = asyncio.get_running_loop().time()
    await stop_timer(h)
    await h.rt._activity_deadline(act)
    waited = asyncio.get_running_loop().time() - started
    assert act.ending and 0.25 <= waited < 1.5
    assert h.rt.turns.state is TurnState.PROCESSING


async def test_finish_button_rules(harness: Harness) -> None:
    h = harness
    use_vad(h, [])
    activity_id = arm(h, "reading_aloud", "D")
    await h.rt._on_activity_finish(activity_id, "read")
    assert h.of(ErrorMsg)[-1].code == "finish_not_available" and not h.rt.activity.ending
    h.advance(5.2)
    await h.rt._on_activity_finish(activity_id, "relay")  # wrong phase: ignored
    assert not h.rt.activity.ending
    await h.rt._on_activity_finish(activity_id, "read")
    act = h.rt.activity
    assert act.ending and act.status == "waiting"
    turn_job = next(j for j in h.rt._jobs if j.kind == "turn")
    assert turn_job.capture is not None and turn_job.capture.end_reason == "activity_finish_button"


async def test_delayed_finish_button_is_announced(harness: Harness) -> None:
    h = harness
    arm(h, "read_relay", "B")
    h.advance(10.1)
    await asyncio.sleep(0.35)
    updates = [m for m in h.of(ActivityPhaseMsg) if m.status == "running" and m.can_finish]
    assert updates and updates[-1].content is None  # the update never re-sends the passage
    await stop_timer(h)


async def test_typed_answers_during_activities(harness: Harness) -> None:
    h = harness
    use_vad(h, [])
    arm(h, "reading_aloud", "A")
    await h.rt._on_typed_answer("I read it")
    assert h.of(ErrorMsg)[-1].code == "typed_not_allowed_for_reading"
    await stop_timer(h)
    h.rt._stop_activity(h.rt.activity)
    arm(h, "read_relay", "C")
    await h.rt._on_typed_answer("typed during the silent reading")
    assert h.of(ErrorMsg)[-1].code == "activity_in_progress"
    act = h.rt.activity
    await h.rt._end_noncapture_phase(act, "phase_deadline")
    h.rt._on_activity_phase_changed(
        ActivityPhaseChanged(
            activity_id=act.activity_id, kind="read_relay", phase="relay", duration_ms=150_000, capture=True,
            can_finish=True, content=None, hide_content=True, start_after_utterance_id=None,
        )
    )  # fmt: skip
    await h.rt._on_typed_answer("Overfitting means the model memorises noise.")
    assert act.ending and act.capture.typed_text == "Overfitting means the model memorises noise."
    await h.run_jobs("turn")
    turn = h.engine.turns[-1]
    assert turn.typed and turn.activity.typed and turn.activity.segments == [] and turn.audio_wav is None


async def test_non_capture_phase_ignores_speech(harness: Harness) -> None:
    h = harness
    use_vad(h, [(0, 3000)])
    arm(h, "topic_card", "5")
    assert h.rt.activity.status == "running" and not h.rt.activity.capture_phase
    await h.audio(tone(3000))
    assert h.rt._capture is None and h.rt.turns.state is TurnState.LISTENING
    assert not [j for j in h.rt._jobs if j.kind == "turn"]
    await stop_timer(h)


async def test_silence_prompt_is_suppressed_during_an_activity(harness: Harness) -> None:
    h = harness
    arm(h, "topic_card", "5")
    h.rt._listening_since = 0.0
    h.rt._last_rx = asyncio.get_running_loop().time()
    import time as _time

    h.rt._last_rx = _time.monotonic()
    h.rt._listening_since = _time.monotonic() - 100
    await h.rt._tick()
    assert not [j for j in h.rt._jobs if j.kind == "timer"]
    await stop_timer(h)


async def test_disconnect_during_capture_runs_the_turn_offline(harness: Harness) -> None:
    h = harness
    use_vad(h, [(0, 2000)])
    arm(h, "reading_aloud", "E")
    act = h.rt.activity
    await h.audio(tone(2000))
    h.rt.transport = None
    await h.rt._activity_detached()
    assert act.status == "interrupted" and act.capture.end_reason == "activity_disconnect"
    assert not [j for j in h.rt._jobs if j.kind == "activity"]
    await h.run_jobs("turn")
    turn = h.engine.turns[-1]
    assert turn.activity.end_reason == "disconnect" and turn.activity.kind == "reading_aloud"


async def test_activity_turn_carries_segments_and_pauses_without_ack_or_unclear_prompts(
    harness: Harness, mock_ai
) -> None:
    h = harness
    mock_ai.stt.default_text = "gradient descent"
    use_vad(h, [(0, 2000), (3000, 5000)])
    h.advance(50.0)
    activity_id = arm(h, "reading_aloud", "B")
    act = h.rt.activity
    await h.audio(tone(5000))
    await h.audio(np.zeros(SAMPLE_RATE // 2, dtype=np.int16))
    h.advance(5.5)
    await h.rt._on_activity_finish(activity_id, "read")
    await h.run_jobs("turn")
    turn = h.engine.turns[-1]
    capture = turn.activity
    assert (
        capture.activity_id == activity_id
        and capture.phase == "read"
        and capture.end_reason == "finish_button"
    )
    assert capture.segments and all(s["start_ms"] >= 50_000 for s in capture.segments)
    assert capture.segments[0]["text"] == "gradient descent"
    assert len(capture.pauses) == 1 and capture.pauses[0]["duration_ms"] >= 900
    assert turn.audio_wav is not None and turn.audio_wav[:4] == b"RIFF"
    kinds = [type(m).__name__ for m in h.messages]
    assert "NoticeMsg" not in kinds  # no "didn't catch that" prompt
    assert not [m for m in h.messages if getattr(m, "kind", None) == "acknowledgment"]
    assert act.status == "interrupted"  # the fake engine answered without a next step: speech is released


class FailingEngine(FakeEngine):
    def handle_activity_event(self, session_id: str, event: Any):
        self.events.append(event)
        if event.kind == "phase_started":
            return _empty()
        raise RuntimeError("engine down")


async def test_a_failed_or_empty_engine_answer_never_blocks_speech(harness: Harness) -> None:
    h = harness
    arm(h, "topic_card", "3")
    act = h.rt.activity
    await h.run_jobs("activity")  # phase_started: no next step expected, still running
    assert act.status == "running" and h.rt._activity_blocks_speech()
    await stop_timer(h)
    await h.rt._end_noncapture_phase(act, "phase_deadline")
    assert act.status == "waiting" and h.rt._activity_blocks_speech()
    await h.run_jobs("activity")  # the engine yields nothing for the deadline
    assert act.status == "interrupted" and not h.rt._activity_blocks_speech()
    # A raising engine releases the floor the same way (the job runner reports the failure).
    h.engine.__class__ = FailingEngine
    arm(h, "topic_card", "3")
    act = h.rt.activity
    await h.run_jobs("activity")
    await stop_timer(h)
    await h.rt._end_noncapture_phase(act, "phase_deadline")
    job = [j for j in h.rt._jobs if j.kind == "activity"][0]
    h.rt._jobs.remove(job)
    with pytest.raises(RuntimeError):
        await job.run()
    assert act.status == "interrupted" and not h.rt._activity_blocks_speech()


async def test_a_used_up_reveal_still_reports_phase_started_before_the_deadline(harness: Harness) -> None:
    h = harness
    activity_id = arm(h, "read_relay", "A")
    await h.run_jobs("activity")
    await stop_timer(h)
    act = h.rt.activity
    act.shown_ms = 30_000
    await h.rt._activity_detached()
    h.engine.events.clear()
    data = descriptor(load_intake_kit(), activity_id=activity_id, kind="read_relay", material_key="A",
                      resumed=True, start_phase="read", start_duration_ms=5_000)  # fmt: skip
    h.rt._on_activity_started(ActivityStarted(activity=data, start_after_utterance_id=None))
    for _ in range(5):
        await asyncio.sleep(0)
    await h.run_jobs("activity")
    assert [(e.kind, e.phase) for e in h.engine.events] == [
        ("phase_started", "read"),
        ("phase_deadline", "read"),
    ]
    assert h.engine.events[0].detail == {"duration_ms": 0}
    assert not [m for m in h.of(ActivityPhaseMsg) if m.status == "running" and m.content is not None][1:]


async def test_engine_activity_end_stops_and_announces(harness: Harness) -> None:
    h = harness
    activity_id = arm(h, "topic_card", "3")
    h.rt._on_activity_ended(ActivityEnded(activity_id=activity_id, kind="topic_card", reason="completed"))
    assert h.rt.activity is None
    end = h.of(ActivityEndMsg)[-1]
    assert (end.activity_id, end.reason) == (activity_id, "completed")


async def test_long_answer_items_use_patient_endpointing(harness: Harness) -> None:
    h = harness
    base = h.rt._base_endpoint
    utt = ActiveUtterance(utterance_id="q", kind="question", behavior=BehaviorSpec(), answer_mode="long")
    utt.delivered = True
    h.rt._on_delivered(utt)
    h.rt._open_capture(h.rt.segmenter.now_ms, reason="speech")
    assert (h.rt.endpointer.params.base_ms, h.rt.endpointer.params.long_ms) == (1500, 2600)
    assert h.rt.endpointer.params.max_utterance_ms == base.max_utterance_ms
    h.rt._capture = None
    plain = ActiveUtterance(
        utterance_id="p", kind="question", behavior=BehaviorSpec(), answer_mode="standard"
    )
    h.rt._on_delivered(plain)
    h.rt._open_capture(h.rt.segmenter.now_ms, reason="speech")
    assert h.rt.endpointer.params == base
    # Utterances without intake metadata (other round types) never change the mode.
    other = ActiveUtterance(utterance_id="o", kind="question", behavior=BehaviorSpec())
    h.rt._on_delivered(other)
    assert h.rt._answer_mode == "standard"


def test_vad_event_types_used_for_pauses_exist() -> None:
    assert SpeechStarted(t_ms=1, segment_start_ms=0).t_ms == 1 and SpeechEnded(t_ms=2).t_ms == 2
