"""Live smoke tests against the real local endpoints (opt-in: ``-m live``).

The endpoints are shared with a production stack: every test makes one
small call. Run with ``-s`` to see the latency table::

    uv run pytest tests/integration/ai/test_live_endpoints.py -m live -s
"""

from __future__ import annotations

import asyncio
import io
import math
import struct
import time
import wave
from collections.abc import AsyncIterator

import numpy as np
import pytest
import pytest_asyncio

from app.ai.base import CallContext
from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices
from app.ai.structured import StreamingJsonFieldExtractor, schema_for
from app.core.config import get_settings
from app.domain.enums import ModelTask
from app.schemas.ai import AnswerAssessment, TurnDecision

pytestmark = pytest.mark.live

RESULTS: dict[str, str] = {}

TURN_MESSAGES = [
    {
        "role": "system",
        "content": (
            "You are Maya, a warm, professional technical interviewer. Reply with one JSON object only. "
            "'speech' is what you say aloud: one or two short sentences ending with exactly one question."
        ),
    },
    {
        "role": "user",
        "content": (
            "Allowed action: ask_followup. Competency: kubernetes. Difficulty: medium.\n"
            "Candidate's last answer: 'I scaled our cluster from 12 to 40 nodes with the cluster autoscaler "
            "and moved stateful services to dedicated node pools.'"
        ),
    },
]


def note(name: str, value: str) -> None:
    RESULTS[name] = value
    print(f"\n[live] {name}: {value}")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def ai() -> AsyncIterator[RealAIServices]:
    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    yield services
    await services.aclose()


async def test_health(ai: RealAIServices) -> None:
    started = time.perf_counter()
    report = await ai.health()
    note("health (all endpoints, concurrent)", f"{(time.perf_counter() - started) * 1000:.0f} ms")
    for name, entry in report.items():
        note(
            f"health.{name}",
            f"{entry['status']} {entry.get('latency_ms')} ms ({entry.get('detail', '')[:60]})",
        )
    for name in ("main", "vision", "embedding", "reranker", "whisper", "ocr"):
        assert report[name]["status"] in ("ok", "degraded"), report[name]


async def test_llm_chat_main(ai: RealAIServices) -> None:
    result = await ai.llm.chat(
        [{"role": "user", "content": "Reply with exactly: ready"}],
        context=CallContext(task=ModelTask.TURN_DECISION),
        max_tokens=8,
        temperature=0,
    )
    note(
        "llm.chat main (8 tokens max)",
        f"{result.latency_ms:.0f} ms, {result.completion_tokens} tokens, text={result.text!r}",
    )
    assert result.endpoint == "main"
    assert "ready" in result.text.lower()


async def test_llm_chat_vision_route(ai: RealAIServices) -> None:
    result = await ai.llm.chat(
        [{"role": "user", "content": "Reply with exactly: ok"}],
        context=CallContext(task=ModelTask.MEMORY_SUMMARY),
        max_tokens=4,
        temperature=0,
    )
    note("llm.chat vision (memory_summary route)", f"{result.latency_ms:.0f} ms, text={result.text!r}")
    assert result.endpoint == "vision"


async def test_llm_stream_ttft_and_barge_in(ai: RealAIServices) -> None:
    stream = ai.llm.stream(
        [{"role": "user", "content": "Count from 1 to 200, separated by spaces."}],
        context=CallContext(task=ModelTask.TURN_DECISION),
        max_tokens=400,
        temperature=0,
    )
    received = []
    async for delta in stream:
        received.append(delta)
        if len(received) >= 5:
            break
    closed_at = time.perf_counter()
    await stream.aclose()
    close_ms = (time.perf_counter() - closed_at) * 1000
    assert stream.ttft_ms is not None
    note("llm.stream TTFT", f"{stream.ttft_ms:.0f} ms")
    note(
        "llm.stream aclose() (barge-in)",
        f"{close_ms:.1f} ms after 5 deltas; status={ai.ledger.records[-1].status}",
    )  # type: ignore[union-attr]
    assert ai.ledger.records[-1].status == "cancelled"  # type: ignore[union-attr]
    assert close_ms < 200


async def test_turn_decision_streaming_speech_first(ai: RealAIServices) -> None:
    started = time.perf_counter()
    stream = ai.llm.stream(
        TURN_MESSAGES,
        context=CallContext(task=ModelTask.TURN_DECISION),
        json_schema=schema_for(TurnDecision),
        max_tokens=500,
        temperature=0.3,
    )
    extractor = StreamingJsonFieldExtractor("speech")
    first_speech_ms = speech_done_ms = None
    async for delta in stream:
        spoken = extractor.feed(delta)
        if spoken and first_speech_ms is None:
            first_speech_ms = (time.perf_counter() - started) * 1000
        if extractor.done and speech_done_ms is None:
            speech_done_ms = (time.perf_counter() - started) * 1000
    total_ms = (time.perf_counter() - started) * 1000
    decision = extractor.finalize(TurnDecision).value
    note(
        "TurnDecision stream",
        f"TTFT {stream.ttft_ms:.0f} ms, first speech char {first_speech_ms:.0f} ms, speech complete {speech_done_ms:.0f} ms, "
        f"full object {total_ms:.0f} ms ({stream.result.completion_tokens if stream.result else '?'} tokens)",
    )
    note("TurnDecision speech", repr(decision.speech))
    assert decision.speech == extractor.value
    assert first_speech_ms is not None and first_speech_ms < 3000


async def test_structured_answer_assessment(ai: RealAIServices) -> None:
    result = await ai.llm.structured(
        AnswerAssessment,
        [
            {"role": "system", "content": "Classify the candidate turn. JSON only."},
            {
                "role": "user",
                "content": "Question: How do you handle Kafka consumer lag?\nCandidate: 'mm-hmm, yeah'",
            },
        ],
        context=CallContext(task=ModelTask.ANSWER_ASSESSMENT),
        max_tokens=200,
    )
    note(
        "structured AnswerAssessment",
        f"{result.latency_ms:.0f} ms, attempts={result.attempts}, repaired={result.repaired}, value={result.value.model_dump()}",
    )
    assert not result.used_fallback


async def test_structured_turn_decision(ai: RealAIServices) -> None:
    result = await ai.llm.structured(
        TurnDecision, TURN_MESSAGES, context=CallContext(task=ModelTask.TURN_DECISION), max_tokens=600
    )
    note(
        "structured TurnDecision (non-streaming)",
        f"{result.latency_ms:.0f} ms, attempts={result.attempts}, repaired={result.repaired}",
    )
    assert isinstance(result.value, TurnDecision)
    assert not result.used_fallback


async def test_embeddings(ai: RealAIServices) -> None:
    started = time.perf_counter()
    docs = await ai.embeddings.embed_documents(
        [
            "Scaled a Kubernetes cluster to 40 nodes",
            "Baked sourdough bread every weekend",
            "Tuned PostgreSQL autovacuum",
        ]
    )
    docs_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    query = await ai.embeddings.embed_query(
        "container orchestration at scale", "Given an interview topic, retrieve resume evidence"
    )
    query_ms = (time.perf_counter() - started) * 1000
    sims = [float(np.dot(query, d)) for d in docs]
    note(
        "embeddings",
        f"3 docs {docs_ms:.0f} ms, 1 query {query_ms:.0f} ms, cosine={[round(s, 3) for s in sims]}",
    )
    assert len(query) == 1024 and math.isclose(float(np.linalg.norm(query)), 1.0, rel_tol=1e-4)
    assert int(np.argmax(sims)) == 0


async def test_reranker(ai: RealAIServices) -> None:
    started = time.perf_counter()
    scores = await ai.reranker.rerank(
        "What is the capital of France?",
        ["Bananas are yellow.", "Paris is the capital of France.", "Berlin is in Germany."],
    )
    note(
        "reranker /score",
        f"{(time.perf_counter() - started) * 1000:.0f} ms, {[(s.index, round(s.score, 4)) for s in scores]}",
    )
    assert scores[0].index == 1 and scores[0].score > 0.9


def _wav(seconds: float, freq: float | None) -> bytes:
    rate = 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / rate)) if freq else 0)
                for i in range(int(seconds * rate))
            )
        )
    return buf.getvalue()


async def test_whisper(ai: RealAIServices) -> None:
    final = await ai.stt.transcribe(_wav(1.5, None), prompt="Interview about Kubernetes.")
    partial = await ai.stt.transcribe(_wav(0.8, 440.0), partial=True)
    note(
        "whisper",
        f"final (1.5 s silence) {final.latency_ms:.0f} ms text={final.text!r} no_speech={final.no_speech_prob}; "
        f"partial (0.8 s tone) {partial.latency_ms:.0f} ms text={partial.text!r}",
    )
    assert final.text == ""  # no hallucinated "Thank you."
    assert final.duration_s == pytest.approx(1.5, abs=0.05)


async def test_ocr(ai: RealAIServices) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 320), "white")
    draw = ImageDraw.Draw(image)
    try:
        bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        regular = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except OSError:
        bold = regular = ImageFont.load_default()
    draw.text((40, 30), "JANE DOE", fill="black", font=bold)
    draw.text((40, 100), "Senior Software Engineer", fill="black", font=regular)
    draw.text((40, 160), "Python, Kubernetes, PostgreSQL, Kafka", fill="black", font=regular)
    draw.text((40, 220), "Reduced cloud cost by 40% in 2024", fill="black", font=regular)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    result = await ai.ocr.ocr_image(buf.getvalue(), mime_type="image/png")
    note("ocr", f"{result.latency_ms:.0f} ms status={result.status} text={result.text!r}")
    assert result.status == "ok"
    assert "JANE DOE" in result.text and "Kubernetes" in result.text
    assert "[" not in result.text  # layout tags stripped


async def test_tts_if_available(ai: RealAIServices) -> None:
    if not await ai.tts.health():
        note("tts", f"skipped: {ai.tts.name} service not reachable at {get_settings().tts_base_url}")
        pytest.skip("TTS service not running")
    result = await ai.tts.synthesize("Could you walk me through how you sized the node pools?")
    note(
        "tts",
        f"{result.latency_ms:.0f} ms for {result.duration_ms} ms of audio, {len(result.visemes)} visemes",
    )
    assert result.duration_ms > 500 and result.pcm16


async def test_zz_summary() -> None:
    await asyncio.sleep(0)
    print("\n\n======== live AI endpoint latencies ========")
    for name, value in RESULTS.items():
        print(f"{name:45s} {value}")
