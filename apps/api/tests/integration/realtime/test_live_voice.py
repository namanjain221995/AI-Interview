"""Live voice-loop measurements against the real Whisper and Kokoro TTS
services (opt-in: ``-m live``; the endpoints are shared, so this makes only a
handful of small calls). Run with ``-s`` to see the numbers::

    uv run pytest tests/integration/realtime/test_live_voice.py -m live -s
"""

from __future__ import annotations

import asyncio
import random
import re
import time

import numpy as np
import pytest

from app.ai.base import CallContext
from app.ai.ledger import NullLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.domain.enums import ModelTask
from app.realtime.audio.segmenter import AudioSegmenter, SegmentClosed
from app.realtime.audio.vad import create_vad
from app.realtime.registry import get_registry
from app.realtime.speech import SpeechStreamer
from app.realtime.stt import TurnTranscriber, build_whisper_prompt
from app.schemas.realtime import TtsChunkMsg, TtsFallbackMsg

from .conftest import CLIPS, load_clip, silence

pytestmark = [pytest.mark.live, pytest.mark.integration]


def note(name: str, value: str) -> None:
    print(f"\n[live] {name}: {value}")


def word_overlap(expected: str, actual: str) -> float:
    def norm(s: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", s.lower())

    exp, act = norm(expected), set(norm(actual))
    return sum(1 for w in exp if w in act) / max(1, len(exp))


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[float, object]] = []

    async def send_tts_chunk(self, message: TtsChunkMsg, pcm16: bytes, *, final: bool) -> None:
        self.sent.append((time.perf_counter(), message))

    async def send_tts_fallback(self, message: TtsFallbackMsg) -> None:
        self.sent.append((time.perf_counter(), message))


async def test_live_whisper_segments_and_kokoro_first_audio() -> None:
    settings = get_settings()
    ai = RealAIServices(settings, redis_client=None, ledger=NullLedger())
    try:
        # ---- STT: Silero segmentation -> ordered segment finals (real Whisper)
        clip = load_clip("cost_vague")
        segmenter = AudioSegmenter(create_vad(settings.vad_engine, settings.vad_model_path))
        segments: list[SegmentClosed] = []
        audio = np.concatenate((clip, silence(800)))
        for i in range(0, audio.size, 1600):
            segments += [e for e in segmenter.process(audio[i : i + 1600]) if isinstance(e, SegmentClosed)]
        tail = segmenter.flush()
        if tail is not None:
            segments.append(tail)
        prompt_vocab = ["AWS", "rightsizing", "Kubernetes", "EKS"]
        transcriber = TurnTranscriber(
            lambda: ai.stt,
            context=CallContext(task=ModelTask.STT, session_id="live-test"),
            prompt=lambda so_far: build_whisper_prompt(
                question="How did you reduce cloud costs?", vocabulary=prompt_vocab, answer_so_far=so_far
            ),
            language=settings.whisper_language,
        )
        started = time.perf_counter()
        for seg in segments:
            transcriber.submit(seg.samples, start_ms=seg.start_ms, end_ms=seg.end_ms)
        assert await transcriber.wait_finals(timeout=60)
        wall = (time.perf_counter() - started) * 1000
        text = transcriber.text()
        expected = CLIPS["cost_vague"]["text"]
        note(
            "stt.segments",
            ", ".join(
                f"{s.duration_ms} ms audio -> {j.latency_ms:.0f} ms"
                for s, j in zip(segments, transcriber.segments, strict=True)
            ),
        )
        note(
            "stt.ordered_finals_wall",
            f"{wall:.0f} ms for {clip.size / 16:.0f} ms of speech ({len(segments)} segments, concurrent)",
        )
        note("stt.text", text)
        assert word_overlap(expected, text) >= 0.7, text

        # One partial (low priority) on the first 2 s.
        started = time.perf_counter()
        partial = await transcriber.partial(clip[: 16000 * 2])
        note(
            "stt.partial_2s",
            "skipped (budget)"
            if partial is None
            else f"{(time.perf_counter() - started) * 1000:.0f} ms -> {partial.text!r}",
        )

        # ---- TTS: time to first audio for a streamed two-sentence utterance.
        sink = Sink()
        streamer = SpeechStreamer(
            sink, lambda: ai.tts, voice=settings.tts_voice, max_inflight=settings.tts_max_inflight_per_session
        )
        utt = streamer.start("live-utt")
        t0 = time.perf_counter()
        # A varied sentence so the TTS service cache cannot answer (uncached synthesis is measured).
        pct = random.randint(11, 97)
        sentence = f"Thanks, that's helpful. Roughly {pct} percent lower, so what was the monthly spend before and after?"
        for word in sentence.split(" "):
            utt.feed(word + " ")
        await utt.finish()
        assert sink.sent, "no TTS output"
        first_at, first = sink.sent[0]
        note("tts.first_chunk", f"{(first_at - t0) * 1000:.0f} ms for {first.text!r}")  # type: ignore[attr-defined]
        note("tts.all_chunks", ", ".join(f"{(at - t0) * 1000:.0f} ms" for at, _ in sink.sent))
        assert all(isinstance(m, TtsChunkMsg) for _, m in sink.sent), "TTS fell back to browser speech"
        chunk = sink.sent[0][1]
        assert chunk.visemes and chunk.duration_ms > 0  # type: ignore[attr-defined]
    finally:
        await ai.aclose()


async def test_live_turn_latency_through_the_runtime(realtime_env, ws_connect, mock_ai) -> None:
    """End-to-end through the WebSocket with real Whisper + Kokoro and the fake engine."""
    from .test_ws_session import deliver, wait_call

    settings = get_settings()
    env = await realtime_env()
    env.engine.INTRO = f"Hi Jane, I'm Maya. Thanks for joining from team {random.randint(11, 97)}. Let's start with your recent work."
    env.engine.default_reply = f"Thanks. About {random.randint(11, 97)} percent then. Which change contributed most to the reduction?"
    real = RealAIServices(settings, redis_client=None, ledger=NullLedger())
    previous = set_ai(real)
    try:
        ws = await ws_connect(env.session_id, await env.ticket())
        await ws.hello()
        await ws.wait_type("session.state")
        await ws.send_json({"type": "control.start"})
        t_start = time.perf_counter()
        intro = await ws.wait_type("ai.utterance.start", kind="intro", timeout=30)
        first_intro_chunk = await ws.wait_type("tts.chunk", utterance_id=intro["utterance_id"], timeout=30)
        note(
            "runtime.intro_first_audio",
            f"{(time.perf_counter() - t_start) * 1000:.0f} ms (control.start -> first tts.chunk, {first_intro_chunk['text']!r})",
        )
        await ws.wait_type("ai.utterance.end", utterance_id=intro["utterance_id"], timeout=60)
        await deliver(ws, intro["utterance_id"])
        await wait_call(env.engine, "delivered", timeout=10)
        mark = len(ws.messages)
        # Real-time pacing: earlier segments are transcribed while the candidate keeps talking.
        await ws.send_audio(
            np.concatenate((silence(300), load_clip("cost_vague"), silence(1500))), realtime_factor=1.0
        )
        final = await ws.wait_type("transcript.final", after=mark, timeout=60)
        reply = await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=60)
        await ws.wait_type("tts.chunk", utterance_id=reply["utterance_id"], timeout=60)
        runtime = get_registry().get(env.session_id)
        record = runtime.latency[-1]
        for _ in range(100):
            if "first_audio_ms" in record:
                break
            await asyncio.sleep(0.02)
        note("runtime.transcript", final["text"])
        note(
            "runtime.turn",
            f"end_reason={record.get('end_reason')} stt_wait={record.get('stt_wait_ms')} ms ack={record.get('ack_ms')} ms "
            f"engine_first_event={record.get('engine_first_event_ms')} ms first_audio={record.get('first_audio_ms')} ms "
            f"(end of turn -> first interviewer audio chunk)",
        )
        assert word_overlap(CLIPS["cost_vague"]["text"], final["text"]) >= 0.6
        assert record["first_audio_ms"] < 10_000
    finally:
        set_ai(previous)
        await get_registry().shutdown_all()
        await real.aclose()
