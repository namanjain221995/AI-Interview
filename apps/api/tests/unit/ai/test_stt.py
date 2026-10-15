from __future__ import annotations

import asyncio
import io
import math
import re
import struct
import wave

import httpx
import pytest
import respx

from app.ai.base import AIUnavailable, PartialSkipped, TranscriptionResult
from app.ai.clients.stt import (
    WhisperSttClient,
    clean_transcription,
    is_hallucination_text,
    truncate_prompt,
    wav_dbfs,
)
from app.core.metrics import REGISTRY

A = "http://whisper-a.test/v1"
B = "http://whisper-b.test/v1"


def wav_bytes(
    seconds: float = 0.5, freq: float | None = 440.0, amplitude: int = 8000, rate: int = 16000
) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / rate)) if freq else 0)
            for i in range(int(seconds * rate))
        )
        w.writeframes(frames)
    return buf.getvalue()


def field(content: bytes, name: str) -> str | None:
    match = re.search(rb'name="' + name.encode() + rb'"\r\n\r\n(.*?)\r\n--', content, re.DOTALL)
    return match.group(1).decode() if match else None


def ok(text: str = "I built the payments service.", **extra) -> dict:
    return {
        "text": text,
        "language": None,
        "duration": 2.0,
        "no_speech_prob": 0.02,
        "segments": [{"start": 0.0, "end": 2.0, "text": text}],
        **extra,
    }


@pytest.fixture
def make_stt(endpoint_factory, ledger):
    def make(
        urls: tuple[str, ...] = (A,),
        concurrency: int = 2,
        partial_budget: int | None = None,
        timeout: float = 5.0,
        **options,
    ) -> WhisperSttClient:
        replicas = [endpoint_factory(f"whisper-{i}", url, retries=1) for i, url in enumerate(urls)]
        options.setdefault("send_prompt", True)  # these tests cover the prompt mechanics
        return WhisperSttClient(
            replicas,
            model="whisper-large",
            language="en",
            max_concurrency=concurrency,
            partial_budget=partial_budget,
            timeout=timeout,
            ledger=ledger,
            **options,
        )

    return make


async def test_multipart_request_and_parsing(make_stt, ledger) -> None:
    stt = make_stt()
    long_prompt = "word " * 400
    with respx.mock(base_url=A) as mock:
        route = mock.post("/audio/transcriptions").respond(200, json=ok())
        result = await stt.transcribe(wav_bytes(), prompt=long_prompt)
    request = route.calls.last.request
    content = request.content
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert field(content, "model") == "whisper-large"
    assert field(content, "response_format") == "json"  # verbose_json costs ~0.65 s per request
    assert field(content, "no_speech_check") == "true"  # finals keep the server's silence check
    assert field(content, "language") == "en"
    assert field(content, "temperature") == "0"
    prompt = field(content, "prompt")
    assert prompt is not None and len(prompt) <= 800 and long_prompt.strip().endswith(prompt)
    assert b'filename="audio.wav"' in content and b"Content-Type: audio/wav" in content
    assert result.text == "I built the payments service."
    assert result.duration_s == 2.0 and result.no_speech_prob == 0.02
    assert result.segments[0].end == 2.0
    assert result.endpoint == A
    assert result.latency_ms > 0
    assert ledger.records[-1].endpoint == "whisper" and ledger.records[-1].status == "ok"


async def test_content_type_extension_and_language_override(make_stt) -> None:
    stt = make_stt()
    with respx.mock(base_url=A) as mock:
        route = mock.post("/audio/transcriptions").respond(200, json=ok())
        await stt.transcribe(b"\x1a\x45\xdf\xa3webm", content_type="audio/webm;codecs=opus", language="de")
    content = route.calls.last.request.content
    assert b'filename="audio.webm"' in content
    assert field(content, "language") == "de"


async def test_partials_are_measured_but_not_ledgered(make_stt, ledger) -> None:
    stt = make_stt()
    before = REGISTRY.get_sample_value("interview_stt_latency_seconds_count", {"kind": "partial"}) or 0
    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").respond(200, json=ok("partial text"))
        result = await stt.transcribe(wav_bytes(), partial=True)
    assert result.text == "partial text"
    assert ledger.records == []
    assert REGISTRY.get_sample_value("interview_stt_latency_seconds_count", {"kind": "partial"}) == before + 1


async def test_one_request_per_replica_and_fifo_queue_for_finals(make_stt) -> None:
    """The deployed Whisper serves one request at a time per replica: a final is
    never sent to a busy replica; it waits for the first replica that frees up."""
    stt = make_stt(urls=(A, B), concurrency=4, hedge=False)
    gates = {A: asyncio.Event(), B: asyncio.Event()}
    order: list[tuple[str, str]] = []

    def handler(base: str):
        async def respond(request: httpx.Request) -> httpx.Response:
            tag = field(request.content, "prompt") or "?"
            order.append((base, tag))
            if tag in ("one", "two"):
                await gates[base].wait()
            return httpx.Response(200, json=ok(tag))

        return respond

    with respx.mock() as mock:
        mock.post(f"{A}/audio/transcriptions").mock(side_effect=handler(A))
        mock.post(f"{B}/audio/transcriptions").mock(side_effect=handler(B))
        one = asyncio.create_task(stt.transcribe(wav_bytes(), prompt="one"))
        two = asyncio.create_task(stt.transcribe(wav_bytes(), prompt="two"))
        await asyncio.sleep(0.05)
        three = asyncio.create_task(stt.transcribe(wav_bytes(), prompt="three"))
        await asyncio.sleep(0.05)
        assert stt.inflight == {A: 1, B: 1} and stt.queued == 1  # not sent to a busy replica
        first_free = order[1][0]  # free whichever replica got "two"
        gates[first_free].set()
        assert (await three).text == "three"
        assert order[-1] == (first_free, "three")  # went to the replica that freed up first
        for gate in gates.values():
            gate.set()
        await asyncio.gather(one, two)
    assert stt.inflight == {A: 0, B: 0} and stt.queued == 0


async def test_faster_replica_preferred(make_stt) -> None:
    stt = make_stt(urls=(A, B), hedge=False)
    stt._replicas[0].speed = 4.0  # A was slow recently (another tenant)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{A}/audio/transcriptions").respond(200, json=ok("from a"))
        mock.post(f"{B}/audio/transcriptions").respond(200, json=ok("from b"))
        result = await stt.transcribe(wav_bytes())
    assert result.endpoint == B
    assert stt.replica_speeds[B] < 1.0  # a fast mocked answer lowers B's ratio


async def test_slow_final_is_hedged_on_an_idle_replica(make_stt, ledger, monkeypatch) -> None:
    import app.ai.clients.stt as stt_module

    monkeypatch.setattr(stt_module, "HEDGE_MIN_SECONDS", 0.05)
    monkeypatch.setattr(stt_module, "HEDGE_FACTOR", 0.01)
    stt = make_stt(urls=(A, B))
    stt._replicas[1].speed = 2.0  # A is picked first
    release_a = asyncio.Event()

    async def slow_a(request: httpx.Request) -> httpx.Response:
        await release_a.wait()
        return httpx.Response(200, json=ok("from a"))

    with respx.mock() as mock:
        mock.post(f"{A}/audio/transcriptions").mock(side_effect=slow_a)
        mock.post(f"{B}/audio/transcriptions").respond(200, json=ok("from b"))
        result = await stt.transcribe(wav_bytes())
        assert result.text == "from b" and result.endpoint == B
        assert stt.hedges == 1 and stt.hedge_wins == 1
        assert stt.inflight == {A: 1, B: 0}  # A's server is still busy: no new work for it
        release_a.set()
        for _ in range(50):
            if stt.inflight[A] == 0:
                break
            await asyncio.sleep(0.01)
    assert stt.inflight == {A: 0, B: 0}
    assert [r.status for r in ledger.records] == ["ok"]  # one ledger row per transcription


async def test_cancelled_caller_keeps_replica_reserved_until_server_answers(make_stt) -> None:
    stt = make_stt(urls=(A,), hedge=False)
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=ok())

    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").mock(side_effect=slow)
        task = asyncio.create_task(stt.transcribe(wav_bytes()))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stt.inflight == {A: 1}  # the server keeps working on it (measured)
        release.set()
        for _ in range(50):
            if stt.inflight[A] == 0:
                break
            await asyncio.sleep(0.01)
    assert stt.inflight == {A: 0}


async def test_partial_skipped_when_budget_or_slots_exhausted(make_stt) -> None:
    stt = make_stt(concurrency=2, partial_budget=1, replica_parallelism=2)
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=ok())

    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").mock(side_effect=slow)
        first_partial = asyncio.create_task(stt.transcribe(wav_bytes(), partial=True))
        await asyncio.sleep(0.02)
        with pytest.raises(PartialSkipped, match="budget"):
            await stt.transcribe(wav_bytes(), partial=True)
        final = asyncio.create_task(stt.transcribe(wav_bytes()))
        await asyncio.sleep(0.02)
        assert stt.inflight == {A: 2}
        stt.partial_budget = 5
        with pytest.raises(PartialSkipped, match="busy"):
            await stt.transcribe(wav_bytes(), partial=True)
        gate.set()
        await asyncio.gather(first_partial, final)
    assert stt.partials_inflight == 0


async def test_partial_never_takes_the_last_idle_replica(make_stt) -> None:
    """With several replicas a partial only runs while another one stays idle,
    so a closing segment's final never waits behind a partial."""
    stt = make_stt(urls=(A, B), hedge=False, partial_budget=2)
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=ok())

    with respx.mock() as mock:
        mock.post(f"{A}/audio/transcriptions").mock(side_effect=slow)
        mock.post(f"{B}/audio/transcriptions").mock(side_effect=slow)
        assert stt.has_spare_capacity()
        partial = asyncio.create_task(stt.transcribe(wav_bytes(), partial=True))
        await asyncio.sleep(0.02)
        assert not stt.has_spare_capacity()
        with pytest.raises(PartialSkipped, match="last free"):
            await stt.transcribe(wav_bytes(), partial=True)
        final = asyncio.create_task(stt.transcribe(wav_bytes()))
        await asyncio.sleep(0.02)
        assert stt.inflight == {A: 1, B: 1} and stt.queued == 0  # the final got the spare replica
        gate.set()
        await asyncio.gather(partial, final)


async def test_partial_skips_server_silence_check_and_reports_it_unknown(make_stt) -> None:
    stt = make_stt()
    with respx.mock(base_url=A) as mock:
        route = mock.post("/audio/transcriptions").respond(200, json={"text": "so we", "no_speech_prob": 0.0})
        result = await stt.transcribe(wav_bytes(), partial=True)
        assert field(route.calls.last.request.content, "no_speech_check") == "false"
        assert result.no_speech_prob is None  # not computed by the server
        final = await stt.transcribe(wav_bytes(), no_speech_check=False)
        assert field(route.calls.last.request.content, "no_speech_check") == "false"
        assert final.no_speech_prob is None and final.duration_s == 0.5  # duration from the WAV header


async def test_finals_queue_and_block_partials_while_waiting(make_stt) -> None:
    stt = make_stt(concurrency=1, partial_budget=1)
    gate = asyncio.Event()
    order: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        tag = field(request.content, "prompt") or "?"
        order.append(tag)
        if tag == "first":
            await gate.wait()
        return httpx.Response(200, json=ok(tag))

    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").mock(side_effect=handler)
        first = asyncio.create_task(stt.transcribe(wav_bytes(), prompt="first"))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(stt.transcribe(wav_bytes(), prompt="second"))
        await asyncio.sleep(0.02)
        with pytest.raises(PartialSkipped):
            await stt.transcribe(wav_bytes(), partial=True, prompt="partial")
        gate.set()
        results = await asyncio.gather(first, second)
    assert [r.text for r in results] == ["first", "second"]
    assert order == ["first", "second"]


async def test_final_wait_times_out(make_stt) -> None:
    stt = make_stt(concurrency=1, timeout=0.2)
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=ok())

    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").mock(side_effect=slow)
        first = asyncio.create_task(stt.transcribe(wav_bytes()))
        await asyncio.sleep(0.02)
        from app.ai.base import AITimeout

        with pytest.raises(AITimeout):
            await stt.transcribe(wav_bytes())
        gate.set()
        await first
    assert stt.inflight == {A: 0}


async def test_failover_to_other_replica(make_stt) -> None:
    stt = make_stt(urls=(A, B), hedge=False)
    stt._replicas[1].speed = 2.0  # A first
    with respx.mock() as mock:
        mock.post(f"{A}/audio/transcriptions").respond(503)
        mock.post(f"{B}/audio/transcriptions").respond(200, json=ok("from b"))
        result = await stt.transcribe(wav_bytes())
    assert result.text == "from b"
    assert result.endpoint == B
    assert stt.inflight == {A: 0, B: 0}


async def test_errors_propagate_and_are_counted(make_stt, ledger) -> None:
    stt = make_stt()
    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").respond(503)
        with pytest.raises(AIUnavailable):
            await stt.transcribe(wav_bytes())
    assert ledger.records[-1].status == "error"
    assert stt.inflight == {A: 0}


async def test_silence_hallucination_removed_end_to_end(make_stt) -> None:
    stt = make_stt()
    with respx.mock(base_url=A) as mock:
        mock.post("/audio/transcriptions").respond(
            200,
            json={
                "text": " Thank you.",
                "duration": 1.0,
                "no_speech_prob": 0.82,
                "segments": [{"start": 0, "end": 1, "text": " Thank you."}],
            },
        )
        result = await stt.transcribe(wav_bytes(freq=None))
    assert result.text == ""
    assert result.segments == []


@pytest.mark.parametrize(
    ("text", "no_speech", "duration", "dbfs", "cleaned"),
    [
        ("Thank you.", 0.8, 2.0, None, True),
        ("Thanks for watching!", 0.6, 3.0, None, True),
        ("Thank you. Thank you.", 0.7, 2.0, None, True),
        ("you", 0.1, 0.3, None, True),  # tiny clip
        ("Thank you.", 0.1, 2.0, -70.0, True),  # silent WAV
        ("Thank you.", 0.1, 2.0, -20.0, False),  # a real "thank you"
        ("Yes.", 0.9, 0.4, None, False),  # real short answer, never dropped
        ("I scaled Kafka to 40 brokers.", 0.9, 0.3, None, False),
    ],
)
def test_clean_transcription_rules(
    text: str, no_speech: float, duration: float, dbfs: float | None, cleaned: bool
) -> None:
    result = TranscriptionResult(text=text, no_speech_prob=no_speech, duration_s=duration)
    out, changed = clean_transcription(result, audio_dbfs=dbfs)
    assert changed is cleaned
    assert (out.text == "") is cleaned


def test_helpers() -> None:
    assert is_hallucination_text("...")
    assert not is_hallucination_text("thank you for the question, I think")
    assert truncate_prompt(None) is None
    assert truncate_prompt("short  prompt") == "short prompt"
    tail = truncate_prompt("alpha " * 300)
    assert tail is not None and len(tail) <= 800 and tail.startswith("alpha")
    assert wav_dbfs(wav_bytes(freq=None)) == -120.0
    loud = wav_dbfs(wav_bytes(amplitude=16000))
    assert loud is not None and -10 < loud < -5
    assert wav_dbfs(b"not a wav") is None


async def test_empty_audio_short_circuits(make_stt) -> None:
    result = await make_stt().transcribe(b"")
    assert result.text == "" and result.duration_s == 0.0


async def test_a_batching_replica_takes_previews_and_finals_next_to_a_slow_fallback(make_stt) -> None:
    """Owner report (slow STT): an interview-only Whisper large-v3-turbo on vLLM (several requests at once, ~5x
    faster) is the primary replica, the shared one-at-a-time large-v3 server the fallback. Previews and finals
    both go to the fast replica while it has room; the slow one is only used when the fast one is full."""
    stt = make_stt(urls=(A, B), hedge=False, partial_budget=2, concurrency=4, replica_slots=[3])
    assert [r.slots for r in stt._replicas] == [3, 1]
    stt._replicas[0].speed, stt._replicas[1].speed = 0.25, 1.2  # as measured: turbo vs the shared server
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=ok())

    with respx.mock() as mock:
        mock.post(f"{A}/audio/transcriptions").mock(side_effect=slow)
        mock.post(f"{B}/audio/transcriptions").mock(side_effect=slow)
        partial = asyncio.create_task(stt.transcribe(wav_bytes(), partial=True))
        await asyncio.sleep(0.02)
        assert stt.inflight == {A: 1, B: 0}  # the preview went to the fast replica (it still has room)
        first = asyncio.create_task(stt.transcribe(wav_bytes()))
        await asyncio.sleep(0.02)
        assert stt.inflight == {A: 2, B: 0}  # a busy fast replica beats an idle one five times slower
        second = asyncio.create_task(stt.transcribe(wav_bytes()))
        third = asyncio.create_task(stt.transcribe(wav_bytes()))
        await asyncio.sleep(0.02)
        assert stt.inflight == {A: 3, B: 1} and stt.queued == 0  # full: the fallback takes the overflow
        gate.set()
        await asyncio.gather(partial, first, second, third)


def test_replica_slots_default_to_one_for_the_shared_server(make_stt) -> None:
    stt = make_stt(urls=(A, B))
    assert [r.slots for r in stt._replicas] == [1, 1]



async def test_no_prompt_is_sent_by_default(make_stt) -> None:
    """Whisper large-v3-turbo on vLLM returned an empty transcript when the prompt ended with the interviewer's
    question (a whole live e2e run lost every answer) and echoed or looped on the term list."""
    stt = make_stt(send_prompt=False)
    with respx.mock(base_url=A) as mock:
        route = mock.post("/audio/transcriptions").respond(200, json=ok())
        await stt.transcribe(wav_bytes(), prompt="Technical terms: Karpenter. How did you scale the nodes?")
    assert field(route.calls[0].request.content, "prompt") is None
