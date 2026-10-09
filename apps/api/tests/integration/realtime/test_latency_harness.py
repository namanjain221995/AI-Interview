"""Live voice-loop latency harness (opt-in: ``-m live``; shared endpoints, one
session of a handful of turns).

Replays the recorded answers in ``tests/e2e/fixtures/audio`` through the real
realtime runtime (in-process ASGI WebSocket) with the REAL interview engine,
the real main LLM, real Whisper (every configured replica) and the real
Kokoro TTS service, and prints the latency breakdown of every candidate turn:

    uv run pytest tests/integration/realtime/test_latency_harness.py -m live -s

The microphone is simulated in real time (100 ms PCM16 frames, silence between
answers, exactly like the browser worklet), interviewer audio is "played" for
its real duration (playback.started / playback.finished like the web client),
and the candidate starts answering ~0.6 s after the question was heard.

Per turn it reports (all wall-clock milliseconds):

* ``end->audio``: end of turn (runtime decided the answer is over) -> first
  interviewer audio chunk sent (``realtime.turn_latency.first_audio_ms``);
* ``speech->audio``: last voiced audio frame sent -> first interviewer audio
  chunk received (what the candidate perceives, includes the end-of-turn
  silence);
* ``stt_wait``: end of turn -> every segment final transcribed;
* ``llm``: STT done -> first speech text from the engine (LLM time to first
  token + engine overhead);
* ``tts``: first chunk text complete -> its audio sent;
* the STT requests of the turn (finals/partials, audio length, latency,
  replica) and the first chunk's text.

Knobs (environment): ``LATENCY_CLIPS`` comma-separated clip names (default: 7
answers of 5-33 s), ``LATENCY_PLAYBACK`` ``realtime`` (default) or ``fast``,
``LATENCY_JSON`` path to write the records, ``LATENCY_LABEL`` a run label.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.core.security import issue_ws_ticket
from app.realtime import deps
from app.realtime.acknowledgements import get_acknowledgement_cache
from app.realtime.audio.pcm import SAMPLE_RATE
from app.realtime.registry import get_registry
from app.schemas.realtime import pack_frame

from .conftest import CLIPS, AsgiWebSocket, FakeCoding, FakeIntegrity, load_clip

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.timeout(1800)]

DEFAULT_CLIPS = (
    "intro",
    "kubernetes_strong",
    "cost_vague",
    "cost_specific",
    "incident",
    "generic_medium",
    "dont_know",
)
FRAME_MS = 100
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
VOICED_DBFS = -42.0
THINK_BEFORE_ANSWER_S = 0.6


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0f}"
    return str(value)


def last_voiced_frame(samples: np.ndarray) -> int:
    """Index of the last 100 ms frame with speech energy."""
    last = 0
    for index in range(0, samples.size, FRAME_SAMPLES):
        frame = samples[index : index + FRAME_SAMPLES].astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0
        if rms > 0 and 20 * np.log10(rms) > VOICED_DBFS:
            last = index // FRAME_SAMPLES
    return last


# ------------------------------------------------------------ instrumentation


class SttProbe:
    """Wraps the real STT client and records every request."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def transcribe(self, audio: bytes, no_speech_check: bool | None = None, **kwargs: Any) -> Any:
        record: dict[str, Any] = {
            "kind": "partial" if kwargs.get("partial") else "final",
            "audio_ms": max(0, (len(audio) - 44) // 32),
            "t0": time.monotonic(),
            "nsc": no_speech_check,
        }
        if no_speech_check is not None:
            kwargs["no_speech_check"] = no_speech_check
        try:
            result = await self.inner.transcribe(audio, **kwargs)
            record.update(ok=True, endpoint=(result.endpoint or "")[7:22], text=result.text)
            return result
        except BaseException as exc:
            record.update(ok=False, error=type(exc).__name__)
            raise
        finally:
            record["t1"] = time.monotonic()
            self.calls.append(record)


class TtsProbe:
    """Wraps the real TTS provider and records every synthesis."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def synthesize(self, text: str, **kwargs: Any) -> Any:
        record: dict[str, Any] = {"text": text, "t0": time.monotonic()}
        try:
            result = await self.inner.synthesize(text, **kwargs)
            record.update(ok=True, audio_ms=result.duration_ms)
            return result
        except BaseException as exc:
            record.update(ok=False, error=type(exc).__name__)
            raise
        finally:
            record["t1"] = time.monotonic()
            self.calls.append(record)


class Microphone:
    """Streams 100 ms frames in real time: queued clips, otherwise silence."""

    def __init__(self, ws: AsgiWebSocket) -> None:
        self.ws = ws
        self._queue: asyncio.Queue[tuple[np.ndarray, asyncio.Future[dict[str, float]]]] = asyncio.Queue()
        self._seq = 0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="harness.mic")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def say(self, samples: np.ndarray) -> asyncio.Future[dict[str, float]]:
        """Queue a clip; the future resolves with its timing once it was sent."""
        future: asyncio.Future[dict[str, float]] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((samples, future))
        return future

    async def _send(self, frame: np.ndarray) -> None:
        self._seq += 1
        await self.ws.send_bytes(pack_frame(0x01, self._seq, frame.astype("<i2").tobytes()))

    async def _run(self) -> None:
        silence = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        next_at = time.monotonic()
        current: tuple[np.ndarray, asyncio.Future[dict[str, float]]] | None = None
        offset = 0
        last_voiced = 0
        started_at = 0.0
        while True:
            if current is None and not self._queue.empty():
                current = self._queue.get_nowait()
                offset = 0
                last_voiced = last_voiced_frame(current[0])
                started_at = time.monotonic()
            if current is None:
                await self._send(silence)
            else:
                samples, future = current
                frame = samples[offset * FRAME_SAMPLES : (offset + 1) * FRAME_SAMPLES]
                if frame.size < FRAME_SAMPLES:
                    frame = np.concatenate((frame, np.zeros(FRAME_SAMPLES - frame.size, dtype=np.int16)))
                await self._send(frame)
                if offset == last_voiced:
                    speech_end = time.monotonic() + FRAME_MS / 1000
                    if not future.done():
                        future.set_result({"started": started_at, "speech_end": speech_end})
                offset += 1
                if offset * FRAME_SAMPLES >= samples.size:
                    current = None
            next_at += FRAME_MS / 1000
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))


@dataclass
class Playback:
    """Simulates the browser's playback queue (utterances play back to back)."""

    ws: AsgiWebSocket
    realtime: bool
    busy_until: float = 0.0
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def play(self, utterance_id: str, duration_ms: int, first_chunk_at: float) -> float:
        start = max(time.monotonic(), first_chunk_at, self.busy_until)
        seconds = duration_ms / 1000 if self.realtime else 0.05
        self.busy_until = start + seconds
        self.tasks.append(asyncio.create_task(self._report(utterance_id, start, seconds, duration_ms)))
        return self.busy_until

    async def _report(self, utterance_id: str, start: float, seconds: float, duration_ms: int) -> None:
        await asyncio.sleep(max(0.0, start - time.monotonic()))
        await self.ws.send_json({"type": "playback.started", "utterance_id": utterance_id, "played_ms": 0})
        await asyncio.sleep(seconds)
        await self.ws.send_json(
            {"type": "playback.finished", "utterance_id": utterance_id, "played_ms": duration_ms}
        )


# ------------------------------------------------------------------ helpers


def utterances_since(ws: AsgiWebSocket, mark: int) -> dict[str, dict[str, Any]]:
    """utterance_id -> {kind, chunks: [(t, msg)], ended} from the timeline."""
    out: dict[str, dict[str, Any]] = {}
    for t, kind, data in ws.timeline[mark:]:
        if not isinstance(data, dict):
            continue
        uid = data.get("utterance_id")
        if kind == "ai.utterance.start":
            out[uid] = {"kind": data.get("kind"), "start": t, "chunks": [], "ended": None, "text": ""}
        elif kind == "tts.chunk" and uid in out:
            out[uid]["chunks"].append((t, data))
        elif kind == "tts.fallback" and uid in out:
            out[uid]["chunks"].append((t, {**data, "duration_ms": len(data.get("text", "")) * 65}))
        elif kind == "ai.utterance.end" and uid in out:
            out[uid]["ended"] = t
            out[uid]["text"] = data.get("text", "")
    return out


async def play_everything(ws: AsgiWebSocket, playback: Playback, mark: int, played: set[str]) -> None:
    """Report playback for every finished interviewer utterance not reported yet."""
    for uid, utt in utterances_since(ws, mark).items():
        if uid in played or utt["ended"] is None:
            continue
        played.add(uid)
        duration = sum(int(msg.get("duration_ms", 0)) for _, msg in utt["chunks"])
        first = utt["chunks"][0][0] if utt["chunks"] else time.monotonic()
        playback.play(uid, max(duration, 1), first)


async def wait_listening(
    ws: AsgiWebSocket, playback: Playback, mark: int, played: set[str], budget_s: float
) -> str:
    """Keep "playing" interviewer speech until the runtime listens again."""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        await play_everything(ws, playback, mark, played)
        for _, kind, data in reversed(ws.timeline[mark:]):
            if kind == "turn.state":
                state = data.get("state")
                if state in ("listening", "coding"):
                    if time.monotonic() >= playback.busy_until:
                        return state
                break
            if kind == "session.ended":
                return "ended"
        await asyncio.sleep(0.05)
    raise AssertionError(f"runtime never returned to listening; last: {[k for _, k, _ in ws.timeline[-15:]]}")


# -------------------------------------------------------------------- test


async def test_live_voice_loop_latency(app, db_session, redis, storage, settings) -> None:  # noqa: PLR0915
    from app.db.session import session_scope
    from app.services.interview.adapters import discover_scoring
    from app.services.interview.config import EngineConfig
    from app.services.interview.engine import InterviewEngine
    from app.services.interview.sessions import SessionServiceImpl

    from ..interview.harness import build_world, round_config_dict

    clips = [
        c.strip() for c in os.environ.get("LATENCY_CLIPS", ",".join(DEFAULT_CLIPS)).split(",") if c.strip()
    ]
    realtime_playback = os.environ.get("LATENCY_PLAYBACK", "realtime") != "fast"
    label = os.environ.get("LATENCY_LABEL", "run")

    ledger = MemoryLedger()
    real = RealAIServices(get_settings(), redis_client=None, ledger=ledger)
    stt_probe, tts_probe = SttProbe(real.stt), TtsProbe(real.tts)
    real.stt, real.tts = stt_probe, tts_probe  # type: ignore[assignment]
    previous = set_ai(real)
    config = EngineConfig(closing_reserve_s=75, default_interviewer_name="Maya")
    engine = InterviewEngine(ai=real, scoring=discover_scoring(), config=config)
    stack = contextlib.ExitStack()
    stack.enter_context(
        deps.override_providers(
            turn_engine=engine, integrity_service=FakeIntegrity(), coding_service=FakeCoding()
        )
    )
    mic: Microphone | None = None
    ws: AsgiWebSocket | None = None
    records: list[dict[str, Any]] = []
    try:
        world = await build_world(
            real,  # type: ignore[arg-type]  # bank rows embedded with the real embedding model
            config=round_config_dict(
                round_type="combined",
                name="Combined AI Round",
                competencies=[
                    {"key": "kubernetes", "name": "Kubernetes", "weight": 1.5, "focus_areas": ["scaling"]},
                    {"key": "aws", "name": "AWS", "weight": 1.2, "focus_areas": ["cost", "networking"]},
                    {"key": "ownership", "name": "Ownership", "weight": 0.6, "focus_areas": ["incidents"]},
                ],
                stopping={
                    "stop_on_sufficient_evidence": False,
                    "min_duration_minutes": 20,
                    "min_questions": 12,
                    "max_questions": 20,
                },
            ),
        )
        async with session_scope() as db:
            session = await SessionServiceImpl(config=config).create_or_resume(
                db, interview_id=world.interview_id
            )
            sid = str(session.id)
        t0 = time.monotonic()
        await engine.prepare_session(sid)
        print(f"\n[latency:{label}] prepare_session {(time.monotonic() - t0) * 1000:.0f} ms")

        ticket = await issue_ws_ticket(session.id, world.candidate_id, world.interview_id)
        ws = AsgiWebSocket(
            app, f"/ws/sessions/{sid}", query=f"ticket={ticket}", headers=[(b"origin", b"http://testserver")]
        )
        await ws.connect()
        assert ws.accepted, ws.close_code
        await ws.hello(user_agent="latency-harness")
        await ws.wait_type("session.state", timeout=20)
        mic = Microphone(ws)
        mic.start()
        playback = Playback(ws, realtime_playback)
        played: set[str] = set()
        mark = len(ws.timeline)
        await ws.send_json({"type": "control.start"})
        t_start = time.monotonic()
        state = await wait_listening(ws, playback, mark, played, budget_s=180)
        intro = [u for u in utterances_since(ws, mark).values() if u["chunks"]]
        if intro:
            print(
                f"[latency:{label}] control.start -> first intro audio "
                f"{(intro[0]['chunks'][0][0] - t_start) * 1000:.0f} ms"
            )
        runtime = get_registry().get(sid)
        assert runtime is not None

        for clip_name in clips:
            if state == "ended":
                break
            await asyncio.sleep(THINK_BEFORE_ANSWER_S)
            samples = load_clip(clip_name)
            mark = len(ws.timeline)
            stt_mark, tts_mark, ledger_mark = len(stt_probe.calls), len(tts_probe.calls), len(ledger.records)
            n_latency = len(runtime.latency)
            spoken = await asyncio.wait_for(mic.say(samples), timeout=120)
            # Wait for the reply's first audio (or a fallback) after this answer.
            reply: dict[str, Any] | None = None
            reply_id: str | None = None
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                await play_everything(ws, playback, mark, played)
                utts = utterances_since(ws, mark)
                found = [(uid, u) for uid, u in utts.items() if u["kind"] != "acknowledgment" and u["chunks"]]
                if found:
                    reply_id, reply = found[0]
                    break
                await asyncio.sleep(0.02)
            assert reply is not None, f"no reply audio after {clip_name}"
            for _ in range(200):
                if len(runtime.latency) > n_latency and "first_audio_ms" in runtime.latency[-1]:
                    break
                await asyncio.sleep(0.01)
            record = dict(runtime.latency[-1]) if len(runtime.latency) > n_latency else {}
            first_t, first_msg = reply["chunks"][0]
            end_t = next(
                (
                    t
                    for t, kind, data in ws.timeline[mark:]
                    if kind == "turn.state" and data.get("state") == "processing"
                ),
                None,
            )
            final = next((d for _, k, d in ws.timeline[mark:] if k == "transcript.final"), None)
            stt_calls = [
                c
                for c in stt_probe.calls[stt_mark:]
                if c["t0"] >= spoken["started"] - 0.5 and c["t0"] <= first_t
            ]
            tts_first = next(
                (
                    c
                    for c in tts_probe.calls[tts_mark:]
                    if c.get("text") == first_msg.get("text") and c["t1"] <= first_t + 0.05
                ),
                None,
            )
            finals_calls = [c for c in stt_calls if c["kind"] == "final"]
            tail = max(finals_calls, key=lambda c: c["t0"]) if finals_calls else None
            row = {
                "label": label,
                "clip": clip_name,
                "speech_s": round(CLIPS[clip_name]["seconds"], 1),
                "end_reason": record.get("end_reason"),
                "required_silence_ms": record.get("required_silence_ms"),
                "segments": record.get("segments"),
                "speech_to_end_ms": round((end_t - spoken["speech_end"]) * 1000) if end_t else None,
                "end_to_audio_ms": record.get("first_audio_ms"),
                "speech_to_audio_ms": round((first_t - spoken["speech_end"]) * 1000),
                "stt_wait_ms": record.get("stt_wait_ms"),
                "engine_first_event_ms": record.get("engine_first_event_ms"),
                "first_delta_ms": record.get("first_delta_ms"),
                # end of turn -> first chunk handed to TTS (cache hits never reach the probe's
                # timing: the TTS service answers them in ~10 ms)
                "first_chunk_ready_ms": record.get("first_chunk_ready_ms")
                or (round((tts_first["t0"] - end_t) * 1000) if tts_first and end_t else None),
                "tts_first_ms": round((tts_first["t1"] - tts_first["t0"]) * 1000) if tts_first else None,
                "first_chunk": first_msg.get("text"),
                "first_chunk_chars": len(first_msg.get("text") or ""),
                "llm_ttft_ms": None,  # filled once the turn's model call is recorded
                "ack_ms": record.get("ack_ms"),
                "tail_audio_ms": record.get("tail_audio_ms") or (tail["audio_ms"] if tail else None),
                "tail_stt_ms": record.get("tail_stt_ms")
                or (round((tail["t1"] - tail["t0"]) * 1000) if tail else None),
                "tail_lead_ms": round((end_t - tail["t0"]) * 1000) if tail and end_t else None,
                "finals": [
                    (c["audio_ms"], round((c["t1"] - c["t0"]) * 1000), c.get("endpoint"))
                    for c in stt_calls
                    if c["kind"] == "final"
                ],
                "partials": sum(1 for c in stt_calls if c["kind"] == "partial" and c.get("ok")),
                "partials_skipped": sum(1 for c in stt_calls if c["kind"] == "partial" and not c.get("ok")),
                "transcript": (final or {}).get("text", ""),
                "expected": CLIPS[clip_name]["text"],
            }
            records.append(row)
            print(
                f"[latency:{label}] {clip_name:18s} {row['speech_s']:5.1f}s end={row['end_reason']}/{row['required_silence_ms']} "
                f"speech->end={_fmt(row['speech_to_end_ms'])} end->audio={_fmt(row['end_to_audio_ms'])} "
                f"speech->audio={_fmt(row['speech_to_audio_ms'])} | stt_wait={_fmt(row['stt_wait_ms'])} "
                f"(tail {_fmt(row['tail_audio_ms'])} ms audio -> {_fmt(row['tail_stt_ms'])} ms) "
                f"engine_first={_fmt(row['engine_first_event_ms'])} first_delta={_fmt(row['first_delta_ms'])} "
                f"chunk_ready={_fmt(row['first_chunk_ready_ms'])} "
                f"tts_first={_fmt(row['tts_first_ms'])} ({row['first_chunk_chars']} ch) ack={_fmt(row['ack_ms'])}"
            )
            print(
                f"[latency:{label}]     finals(audio ms, latency ms, replica)={row['finals']} "
                f"partials={row['partials']} skipped={row['partials_skipped']} first_chunk={row['first_chunk']!r}"
            )
            state = await wait_listening(ws, playback, mark, played, budget_s=120)
            assert reply_id is not None
            llm = [r for r in ledger.records[ledger_mark:] if r.task == "turn_decision"]
            row["llm_ttft_ms"] = round(llm[0].ttft_ms) if llm and llm[0].ttft_ms else None
            print(
                f"[latency:{label}]     llm_ttft={_fmt(row['llm_ttft_ms'])} tail_lead={_fmt(row['tail_lead_ms'])}"
            )

        # ---------------------------------------------------------------- summary
        def col(name: str) -> list[float]:
            return [float(r[name]) for r in records if isinstance(r.get(name), int | float)]

        print(f"\n[latency:{label}] SUMMARY over {len(records)} turns (p50 / p95 / max, ms)")
        for name in (
            "end_to_audio_ms",
            "speech_to_audio_ms",
            "speech_to_end_ms",
            "stt_wait_ms",
            "first_delta_ms",
            "first_chunk_ready_ms",
            "llm_ttft_ms",
            "tts_first_ms",
            "tail_stt_ms",
        ):
            values = col(name)
            if values:
                print(
                    f"[latency:{label}]   {name:20s} p50={_fmt(_p(values, 0.5))} p95={_fmt(_p(values, 0.95))} "
                    f"max={_fmt(max(values))} mean={statistics.mean(values):.0f}"
                )
        finals = [lat for r in records for _, lat, _ in r["finals"]]
        if finals:
            print(
                f"[latency:{label}]   stt final latency    p50={_fmt(_p(finals, 0.5))} p95={_fmt(_p(finals, 0.95))} n={len(finals)}"
            )
        out = os.environ.get("LATENCY_JSON")
        if out:
            await asyncio.to_thread(Path(out).write_text, json.dumps(records, indent=1, default=str))
        assert records, "no turns measured"
    finally:
        if mic is not None:
            await mic.stop()
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.disconnect()
        await get_registry().shutdown_all()
        stack.close()
        set_ai(previous)
        get_acknowledgement_cache().clear()
        await real.aclose()
