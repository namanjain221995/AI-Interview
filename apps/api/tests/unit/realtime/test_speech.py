"""SpeechStreamer: ordering, inflight bound, retry, fallback + degraded mode, cancel."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.base import TtsUnavailable
from app.ai.clients.tts import MockTtsProvider
from app.core import metrics
from app.realtime.acknowledgements import AcknowledgementCache
from app.realtime.speech import SpeechStreamer, TtsHealth, estimate_speech_ms
from app.schemas.realtime import TtsChunkMsg, TtsFallbackMsg, unpack_frame


class Sink:
    def __init__(self) -> None:
        self.items: list[tuple[str, object, bytes | None]] = []

    async def send_tts_chunk(self, message: TtsChunkMsg, pcm16: bytes, *, final: bool) -> None:
        from app.schemas.realtime import pack_frame

        self.items.append(("chunk", message, pack_frame(0x02, message.chunk_id, pcm16, final=final)))

    async def send_tts_fallback(self, message: TtsFallbackMsg) -> None:
        self.items.append(("fallback", message, None))

    def chunks(self) -> list[TtsChunkMsg]:
        return [m for k, m, _ in self.items if k == "chunk"]  # type: ignore[misc]

    def fallbacks(self) -> list[TtsFallbackMsg]:
        return [m for k, m, _ in self.items if k == "fallback"]  # type: ignore[misc]


class SlowTts(MockTtsProvider):
    """Mock TTS with per-text delays, concurrency tracking and failure injection."""

    def __init__(self, delays: dict[str, float] | None = None, fail_times: int = 0) -> None:
        super().__init__(ms_per_char=10)
        self.delays = delays or {}
        self.fail_times = fail_times
        self.active = 0
        self.max_active = 0

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(next((d for k, d in self.delays.items() if k in text), 0.01))
            if self.fail_times > 0:
                self.fail_times -= 1
                raise TtsUnavailable("boom", endpoint="tts")
            return await super().synthesize(text, voice=voice, speed=speed)
        finally:
            self.active -= 1


TEXT = "First sentence is here. Second sentence follows now. Third sentence closes it out. Fourth one too."


async def speak(streamer: SpeechStreamer, text: str, utterance_id: str = "u1", step: int = 7):
    utt = streamer.start(utterance_id)
    for i in range(0, len(text), step):
        utt.feed(text[i : i + step])
        await asyncio.sleep(0)
    await utt.finish()
    return utt


async def test_chunks_are_sent_in_order_with_binary_frames() -> None:
    tts = SlowTts(delays={"First": 0.08, "Second": 0.01, "Third": 0.03})
    sink = Sink()
    streamer = SpeechStreamer(sink, lambda: tts, voice="af_heart", max_inflight=2)
    utt = await speak(streamer, TEXT)
    chunks = sink.chunks()
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].text == "First sentence is here."
    assert " ".join(c.text for c in chunks) == TEXT
    ids = [c.chunk_id for c in chunks]
    assert len(set(ids)) == len(ids)
    for kind, message, frame in sink.items:
        assert kind == "chunk" and frame is not None
        ftype, flags, chunk_id, payload = unpack_frame(frame)
        assert ftype == 0x02 and chunk_id == message.chunk_id  # type: ignore[attr-defined]
        assert len(payload) == message.sample_rate * message.duration_ms // 1000 * 2  # type: ignore[attr-defined]
    assert utt.total_ms == sum(c.duration_ms for c in chunks)
    assert [c.offset_ms for c in utt.chunks] == [
        sum(c.duration_ms for c in chunks[:i]) for i in range(len(chunks))
    ]
    assert tts.max_active <= 2


async def test_inflight_bound_is_respected() -> None:
    tts = SlowTts(delays={"": 0.02})
    streamer = SpeechStreamer(Sink(), lambda: tts, voice=None, max_inflight=1)
    await speak(streamer, TEXT)
    assert tts.max_active == 1


async def test_single_failure_is_retried_without_fallback() -> None:
    tts = SlowTts(fail_times=1)
    sink = Sink()
    streamer = SpeechStreamer(sink, lambda: tts, voice=None)
    await speak(streamer, "Just one sentence here.")
    assert len(sink.chunks()) == 1 and not sink.fallbacks()
    assert not streamer.health.degraded


async def test_persistent_failure_falls_back_and_degrades_until_probe_recovers() -> None:
    tts = SlowTts(fail_times=100)
    sink = Sink()
    changes: list[bool] = []
    health = TtsHealth(
        lambda: tts, probe_interval=0.05, on_change=lambda degraded, reason: changes.append(degraded)
    )
    streamer = SpeechStreamer(sink, lambda: tts, voice=None, health=health)
    utt = await speak(streamer, TEXT)
    assert utt.fallback
    assert not sink.chunks()
    fallbacks = sink.fallbacks()
    assert " ".join(f.text for f in fallbacks) == TEXT
    assert health.degraded and changes == [True]
    assert utt.total_ms == sum(estimate_speech_ms(f.text) for f in fallbacks)

    # Next utterance skips straight to the fallback (no TTS calls).
    calls_before = len(tts.calls)
    second = streamer.start("u2")
    assert second.fallback_reason == "tts_degraded"
    second.set_text("Another question for you?")
    await second.finish()
    assert len(tts.calls) == calls_before

    # Provider healthy again -> the probe clears degraded mode.
    tts.fail_times = 0
    tts.healthy = True
    for _ in range(50):
        if not health.degraded:
            break
        await asyncio.sleep(0.02)
    assert not health.degraded and changes == [True, False]
    await health.aclose()


async def test_browser_mode_and_browser_provider_use_fallback() -> None:
    sink = Sink()
    streamer = SpeechStreamer(sink, lambda: MockTtsProvider(), voice=None, client_tts_mode="browser")
    await speak(streamer, "Hello there.")
    assert sink.fallbacks()[0].reason == "client_browser"

    from app.ai.clients.tts import BrowserTtsProvider

    sink2 = Sink()
    streamer2 = SpeechStreamer(sink2, lambda: BrowserTtsProvider(), voice=None)
    await speak(streamer2, "Hello there.")
    assert sink2.fallbacks()[0].reason == "server_tts_disabled"


async def test_cancel_stops_sending_remaining_chunks() -> None:
    tts = SlowTts(delays={"Second": 0.5, "Third": 0.5})
    sink = Sink()
    streamer = SpeechStreamer(sink, lambda: tts, voice=None)
    utt = streamer.start("u1")
    utt.feed(TEXT)
    for _ in range(100):
        if sink.chunks():
            break
        await asyncio.sleep(0.01)
    await utt.cancel()
    await asyncio.sleep(0.05)
    assert len(sink.chunks()) == 1
    await utt.finish()  # no-op after cancel


async def test_first_audio_and_turn_latency_metrics_are_observed() -> None:
    def count(hist) -> float:
        return next(s.value for s in hist.collect()[0].samples if s.name.endswith("_count"))

    ttfa, turn = count(metrics.TIME_TO_FIRST_AUDIO), count(metrics.TURN_LATENCY)
    streamer = SpeechStreamer(Sink(), lambda: SlowTts(), voice=None)
    utt = streamer.start("u1", turn_end_at=asyncio.get_running_loop().time() - 0.0)
    utt.set_text("Short one. And another sentence here.")
    await utt.finish()
    assert count(metrics.TIME_TO_FIRST_AUDIO) == ttfa + 1
    assert count(metrics.TURN_LATENCY) == turn + 1
    assert utt.timings.first_chunk_at is not None


async def test_acknowledgement_cache_varies_and_never_repeats() -> None:
    cache = AcknowledgementCache()
    tts = MockTtsProvider()
    assert cache.pick(tts, "af_heart") is None
    await cache.warm(tts, "af_heart")
    assert cache.ready(tts, "af_heart") == len(cache.phrases) + len(cache.prompts)
    from app.realtime.acknowledgements import REPEAT_PROMPT, SOFT_ACKNOWLEDGEMENTS

    assert cache.prompt(tts, "af_heart", REPEAT_PROMPT) is not None  # pre-synthesized, never picked as an ack
    for _ in range(20):
        soft, _audio = cache.pick(tts, "af_heart", soft_only=True)  # type: ignore[misc]
        assert soft in SOFT_ACKNOWLEDGEMENTS
    last = None
    seen = set()
    for _ in range(50):
        phrase, result = cache.pick(tts, "af_heart", last=last)  # type: ignore[misc]
        assert phrase != last and result.pcm16 and phrase != REPEAT_PROMPT
        seen.add(phrase)
        last = phrase
    assert len(seen) >= 4
    failing = MockTtsProvider()
    failing.fail_with = TtsUnavailable("down", endpoint="tts")
    assert await cache.warm(failing, "af_heart") == 0


@pytest.mark.parametrize("text", ["", "Hi.", "A much longer sentence that takes a while to say out loud."])
def test_estimate_speech_ms(text: str) -> None:
    assert estimate_speech_ms(text) >= 500


async def test_first_chunk_synthesizes_alone_then_pipeline_overlaps() -> None:
    """A concurrent second synthesis slows the first ~1.6x on the shared CPU
    (measured), so later chunks start only after the first one is synthesized."""
    tts = SlowTts(delays={"First": 0.08})
    streamer = SpeechStreamer(Sink(), lambda: tts, voice="af_heart", max_inflight=2)
    utt = streamer.start("u-first")
    utt.feed(TEXT)  # all four chunks are known at once
    await asyncio.sleep(0.05)
    assert tts.active == 1  # only the first chunk is synthesizing
    await utt.finish()
    assert [c.index for c in streamer.sink.chunks()] == [0, 1, 2, 3]  # type: ignore[attr-defined]
    assert tts.max_active == 2  # after the first, chunks still synthesize ahead
    timings = utt.timings
    assert timings.first_job_chars == len(TEXT.split(". ")[0]) + 1
    assert timings.first_synth_ms is not None and timings.first_synth_ms >= 70
    assert timings.first_text_at is not None and timings.first_job_at is not None


async def test_opener_warmup_fills_the_tts_cache_until_the_interview_starts() -> None:
    from app.realtime.acknowledgements import opener_phrases

    class RecordingTts(MockTtsProvider):
        name = "kokoro"  # a real provider: warm-up only runs against server TTS

        def __init__(self) -> None:
            super().__init__(ms_per_char=5)

    tts = RecordingTts()
    cache = AcknowledgementCache(phrases=("Okay.",), prompts=())
    phrases = opener_phrases("Jane")
    assert phrases[0] == "Thanks, Jane." and "That makes sense." in phrases
    started = False
    await cache.warm(tts, "af_heart")
    task = cache.warm_openers(tts, "af_heart", phrases, stop=lambda: started)
    assert task is not None
    assert await task == len(phrases)
    assert tts.calls == ["Okay.", *phrases]  # acknowledgements first, then the openers
    assert cache.warm_openers(tts, "af_heart", phrases) is None  # already warm for this voice
    started = True
    task = cache.warm_openers(tts, "af_heart", opener_phrases("Marcus"), stop=lambda: started)
    assert task is not None and await task == 0  # the interview started: nothing more
    assert cache.warm_openers(MockTtsProvider(), "af_heart", phrases) is None  # mock/browser: skipped
