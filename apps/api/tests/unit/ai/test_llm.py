from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from app.ai.base import AIOutputInvalid, AIRejected, AIUnavailable, CallContext
from app.ai.clients.llm import OpenAICompatibleLLM, SSEDecoder, ThinkFilter, parse_event_data, strip_think
from app.ai.ledger import MemoryLedger
from app.ai.router import ModelRouter
from app.core.metrics import REGISTRY
from app.domain.enums import ModelCallStatus, ModelTask

MAIN = "http://main.test/v1"
VISION = "http://vision.test/v1"
TURN = CallContext(
    task=ModelTask.TURN_DECISION, session_id="5b0f6a52-3c8f-4a4e-9a3c-1f1f1f1f1f1f", prompt_version="turn@abc"
)
EVAL = CallContext(task=ModelTask.ANSWER_EVALUATION)
MSGS = [{"role": "system", "content": "You are an interviewer."}, {"role": "user", "content": "Hi"}]


class Small(BaseModel):
    speech: str = Field(..., min_length=1, max_length=40)
    score: int = Field(..., ge=0, le=10)


@pytest.fixture
def llm(endpoint_factory, ledger) -> OpenAICompatibleLLM:
    main = endpoint_factory("main", MAIN, retries=1)
    vision = endpoint_factory("vision", VISION, retries=1)
    router = ModelRouter(breakers={"main": main.breaker, "vision": vision.breaker})
    return OpenAICompatibleLLM(
        endpoints={"main": main, "vision": vision},
        models={"main": "big-model", "vision": "small-model"},
        router=router,
        ledger=ledger,
    )


def completion(text: str, *, prompt: int = 11, completion_tokens: int = 7, finish: str = "stop") -> dict:
    return {
        "model": "big-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens},
    }


def delta(
    content: str | None = None, *, reasoning: str | None = None, finish: str | None = None, role: bool = False
) -> dict:
    d: dict = {}
    if role:
        d["role"] = "assistant"
        d["content"] = ""
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning"] = reasoning
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def sse_bytes(*events: dict, done: bool = True) -> bytes:
    body = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
    if done:
        body += "data: [DONE]\n\n"
    return body.encode("utf-8")


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, hang_after: int | None = None, delay: float = 0.0) -> None:
        self.chunks = chunks
        self.hang_after = hang_after
        self.delay = delay
        self.closed = False
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if self.hang_after is not None and index >= self.hang_after:
                break
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += 1
            yield chunk
        if self.hang_after is not None:
            await asyncio.sleep(3600)  # the server keeps the stream open

    async def aclose(self) -> None:
        self.closed = True


def split_randomly(data: bytes, rng: random.Random) -> list[bytes]:
    chunks, i = [], 0
    while i < len(data):
        size = rng.randint(1, 23)
        chunks.append(data[i : i + size])
        i += size
    return chunks


def streaming(stream: ChunkStream) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    return handler


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ------------------------------------------------------------------- chat


async def test_chat_body_main_sends_thinking_flag_and_schema(llm, ledger: MemoryLedger) -> None:
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").respond(200, json=completion("Hello there"))
        result = await llm.chat(
            MSGS, context=TURN, json_schema=Small.model_json_schema(), temperature=0.1, max_tokens=50
        )
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "big-model"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "Small"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["stream"] is False
    assert (body["temperature"], body["max_tokens"]) == (0.1, 50)
    assert result.text == "Hello there"
    assert (result.endpoint, result.prompt_tokens, result.completion_tokens) == ("main", 11, 7)
    assert result.finish_reason == "stop"
    record = ledger.records[-1]
    assert record.status == "ok"
    assert record.task == "turn_decision"
    assert record.endpoint == "main"
    assert str(record.id) == result.model_call_id
    assert record.prompt_version == "turn@abc"
    assert record.session_id == TURN.session_id
    assert record.request_hash and len(record.request_hash) == 64
    assert record.request_payload["messages"][1]["content"] == "Hi"
    assert record.response_payload == {"text": "Hello there"}


async def test_chat_vision_endpoint_has_no_thinking_kwargs(llm) -> None:
    with respx.mock(base_url=VISION) as mock:
        route = mock.post("/chat/completions").respond(200, json=completion("ok"))
        await llm.chat(MSGS, context=CallContext(task=ModelTask.MEMORY_SUMMARY), thinking=True)
    body = json.loads(route.calls.last.request.content)
    assert "chat_template_kwargs" not in body
    assert body["model"] == "small-model"


async def test_chat_thinking_true_and_think_block_stripped(llm) -> None:
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").respond(
            200, json=completion("<think>\nreasoning\n</think>\n\nFinal answer")
        )
        result = await llm.chat(MSGS, context=EVAL, thinking=True)
    assert json.loads(route.calls.last.request.content)["chat_template_kwargs"] == {"enable_thinking": True}
    assert result.text == "Final answer"


async def test_chat_turn_task_falls_back_to_vision(llm, ledger: MemoryLedger) -> None:
    with respx.mock(assert_all_called=False) as mock:
        main = mock.post(f"{MAIN}/chat/completions").respond(503)
        vision = mock.post(f"{VISION}/chat/completions").respond(200, json=completion("from vision"))
        result = await llm.chat(MSGS, context=TURN)
    assert main.call_count == 2  # first attempt + one retry
    assert vision.call_count == 1
    assert result.endpoint == "vision"
    assert result.text == "from vision"
    assert ledger.records[-1].endpoint == "vision"
    assert "chat_template_kwargs" not in json.loads(vision.calls.last.request.content)


async def test_chat_deep_task_does_not_fall_back(llm, ledger: MemoryLedger) -> None:
    before = sample(
        "interview_model_call_failures_total",
        {"endpoint": "main", "task": "answer_evaluation", "reason": "unavailable"},
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{MAIN}/chat/completions").respond(503)
        vision = mock.post(f"{VISION}/chat/completions").respond(200, json=completion("x"))
        with pytest.raises(AIUnavailable):
            await llm.chat(MSGS, context=EVAL)
    assert vision.call_count == 0
    assert ledger.records[-1].status == "error"
    after = sample(
        "interview_model_call_failures_total",
        {"endpoint": "main", "task": "answer_evaluation", "reason": "unavailable"},
    )
    assert after == before + 1


async def test_chat_open_circuit_routes_turn_task_to_vision(llm) -> None:
    main_breaker = llm.endpoint_client("main").breaker
    for _ in range(main_breaker.failure_threshold):
        main_breaker.on_failure()
    with respx.mock(assert_all_called=False) as mock:
        main = mock.post(f"{MAIN}/chat/completions").respond(200, json=completion("main"))
        mock.post(f"{VISION}/chat/completions").respond(200, json=completion("vision"))
        result = await llm.chat(MSGS, context=TURN)
    assert result.endpoint == "vision"
    assert main.call_count == 0


async def test_chat_rejected_is_not_retried_or_rerouted(llm, ledger: MemoryLedger) -> None:
    with respx.mock(assert_all_called=False) as mock:
        main = mock.post(f"{MAIN}/chat/completions").respond(400, json={"error": {"message": "bad request"}})
        vision = mock.post(f"{VISION}/chat/completions").respond(200, json=completion("x"))
        with pytest.raises(AIRejected):
            await llm.chat(MSGS, context=TURN)
    assert (main.call_count, vision.call_count) == (1, 0)
    assert ledger.records[-1].status == "error"


async def test_chat_records_metrics(llm) -> None:
    labels = {"endpoint": "main", "task": "turn_decision", "status": "ok"}
    before = sample("interview_llm_request_seconds_count", labels)
    tokens_before = sample(
        "interview_llm_tokens_total", {"endpoint": "main", "task": "turn_decision", "kind": "completion"}
    )
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").respond(200, json=completion("hi", completion_tokens=9))
        await llm.chat(MSGS, context=TURN)
    assert sample("interview_llm_request_seconds_count", labels) == before + 1
    assert (
        sample(
            "interview_llm_tokens_total", {"endpoint": "main", "task": "turn_decision", "kind": "completion"}
        )
        == tokens_before + 9
    )


# ----------------------------------------------------------------- stream


@pytest.mark.parametrize("seed", range(12))
async def test_stream_parses_split_frames_and_ignores_reasoning(llm, ledger: MemoryLedger, seed: int) -> None:
    events = [
        delta(role=True),
        delta(reasoning="let me think"),
        delta("Hel"),
        delta("lo — "),
        delta("naïve 😀 "),
        delta(reasoning="more"),
        delta("world", finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 21, "completion_tokens": 6}},
    ]
    stream_body = ChunkStream(split_randomly(sse_bytes(*events), random.Random(seed)))
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").mock(side_effect=streaming(stream_body))
        stream = llm.stream(MSGS, context=TURN)
        parts = [part async for part in stream]
    body = json.loads(route.calls.last.request.content)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "".join(parts) == "Hello — naïve 😀 world"
    assert stream.result is not None
    assert stream.result.text == "Hello — naïve 😀 world"
    assert (stream.result.prompt_tokens, stream.result.completion_tokens) == (21, 6)
    assert stream.result.finish_reason == "stop"
    assert stream.result.ttft_ms is not None
    assert stream.result.model_call_id == stream.model_call_id
    record = ledger.records[-1]
    assert record.status == "ok"
    assert record.ttft_ms is not None
    assert record.completion_tokens == 6
    assert stream_body.closed


async def test_stream_aclose_closes_http_stream_immediately(llm, ledger: MemoryLedger) -> None:
    body = ChunkStream(
        [sse_bytes(delta("one "), done=False), sse_bytes(delta("two "), done=False)], hang_after=2
    )
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").mock(side_effect=streaming(body))
        stream = llm.stream(MSGS, context=TURN)
        first = await stream.__anext__()
        assert first == "one "
        await stream.aclose()
    assert body.closed
    assert stream.closed
    assert ledger.records[-1].status == "cancelled"
    assert stream.result is not None and stream.result.text == "one "
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


async def test_stream_aclose_from_another_task_unblocks_reader(llm, ledger: MemoryLedger) -> None:
    body = ChunkStream([sse_bytes(delta("partial "), done=False)], hang_after=1)
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").mock(side_effect=streaming(body))
        stream = llm.stream(MSGS, context=TURN)
        received: list[str] = []

        async def consume() -> None:
            async for part in stream:
                received.append(part)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # reader is now parked waiting for the next chunk
        loop = asyncio.get_running_loop()
        started = loop.time()
        await stream.aclose()
        await asyncio.wait_for(consumer, 1.0)
        assert loop.time() - started < 0.5
    assert received == ["partial "]
    assert body.closed
    assert ledger.records[-1].status == "cancelled"


async def test_stream_task_cancellation_closes_stream(llm, ledger: MemoryLedger) -> None:
    body = ChunkStream([sse_bytes(delta("a"), done=False)], hang_after=1)
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").mock(side_effect=streaming(body))
        stream = llm.stream(MSGS, context=TURN)

        async def consume() -> None:
            async for _ in stream:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert body.closed
    assert ledger.records[-1].status == "cancelled"


async def test_stream_inband_error(llm, ledger: MemoryLedger) -> None:
    body = ChunkStream([sse_bytes(delta("x"), {"error": {"message": "grammar failed", "code": 400}})])
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").mock(side_effect=streaming(body))
        with pytest.raises(AIRejected, match="grammar failed"):
            async for _ in llm.stream(MSGS, context=TURN):
                pass
    assert ledger.records[-1].status == "error"


async def test_stream_falls_back_before_first_token(llm, ledger: MemoryLedger) -> None:
    body = ChunkStream([sse_bytes(delta("vision says hi", finish="stop"))])
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{MAIN}/chat/completions").respond(503)
        mock.post(f"{VISION}/chat/completions").mock(side_effect=streaming(body))
        stream = llm.stream(MSGS, context=TURN)
        text = "".join([p async for p in stream])
    assert text == "vision says hi"
    assert stream.endpoint == "vision"
    assert ledger.records[-1].endpoint == "vision"


async def test_stream_broken_midway_is_not_rerouted(llm) -> None:
    class Broken(ChunkStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield sse_bytes(delta("half "), done=False)
            raise httpx.ReadError("connection reset")

    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{MAIN}/chat/completions").mock(side_effect=streaming(Broken([])))
        vision = mock.post(f"{VISION}/chat/completions").respond(200)
        parts: list[str] = []
        with pytest.raises(AIUnavailable):
            async for part in llm.stream(MSGS, context=TURN):
                parts.append(part)
    assert parts == ["half "]
    assert vision.call_count == 0


async def test_stream_with_json_schema_sends_response_format(llm) -> None:
    body = ChunkStream([sse_bytes(delta('{"speech": "Hi", "score": 3}', finish="stop"))])
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").mock(side_effect=streaming(body))
        text = "".join(
            [p async for p in llm.stream(MSGS, context=TURN, json_schema=Small.model_json_schema())]
        )
    assert json.loads(route.calls.last.request.content)["response_format"]["json_schema"]["name"] == "Small"
    assert Small.model_validate_json(text).score == 3


# ------------------------------------------------------------- structured


async def test_structured_first_try(llm, ledger: MemoryLedger) -> None:
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").respond(
            200, json=completion('{"speech": "Tell me more.", "score": 4}')
        )
        result = await llm.structured(Small, MSGS, context=TURN)
    assert result.value == Small(speech="Tell me more.", score=4)
    assert (result.attempts, result.repaired, result.used_fallback) == (1, False, False)
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["json_schema"]["schema"]["properties"]["speech"]["maxLength"] == 40
    assert ledger.records[-1].status == "ok"
    assert str(ledger.records[-1].id) == result.model_call_id


async def test_structured_repairs_fenced_trailing_comma(llm, ledger: MemoryLedger) -> None:
    raw = 'Sure!\n```json\n{"speech": "Go on.", "score": 5,}\n```'
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").respond(200, json=completion(raw))
        result = await llm.structured(Small, MSGS, context=TURN)
    assert result.value.score == 5
    assert result.repaired
    assert ledger.records[-1].status == "repaired"
    assert ledger.records[-1].repaired


async def test_structured_retries_with_validation_feedback(llm, ledger: MemoryLedger) -> None:
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json=completion('{"speech": "", "score": "high"}')),
                httpx.Response(200, json=completion('{"speech": "Better.", "score": 7}', prompt=30)),
            ]
        )
        result = await llm.structured(Small, MSGS, context=TURN)
    assert result.attempts == 2
    assert result.value.score == 7
    second = json.loads(route.calls[1].request.content)["messages"]
    assert second[-2] == {"role": "assistant", "content": '{"speech": "", "score": "high"}'}
    assert second[-1]["role"] == "user"
    assert "speech" in second[-1]["content"] and "score" in second[-1]["content"]
    record = ledger.records[-1]
    assert record.status == "retried"
    assert record.prompt_tokens == 41  # summed over attempts


async def test_structured_invalid_raises_after_max_attempts(llm, ledger: MemoryLedger) -> None:
    with respx.mock(base_url=MAIN) as mock:
        route = mock.post("/chat/completions").respond(200, json=completion("not json at all"))
        with pytest.raises(AIOutputInvalid):
            await llm.structured(Small, MSGS, context=TURN, max_attempts=2)
    assert route.call_count == 2
    assert ledger.records[-1].status == "error"


async def test_structured_fallback_on_invalid_output(llm, ledger: MemoryLedger) -> None:
    with respx.mock(base_url=MAIN) as mock:
        mock.post("/chat/completions").respond(200, json=completion("{}"))
        result = await llm.structured(
            Small,
            MSGS,
            context=TURN,
            max_attempts=2,
            fallback=lambda: Small(speech="Let's continue.", score=0),
        )
    assert result.used_fallback
    assert result.value.speech == "Let's continue."
    assert ledger.records[-1].status == ModelCallStatus.FALLBACK.value
    assert ledger.records[-1].used_fallback


async def test_structured_fallback_when_endpoints_down(llm) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{MAIN}/chat/completions").respond(503)
        mock.post(f"{VISION}/chat/completions").respond(503)
        result = await llm.structured(
            Small, MSGS, context=TURN, fallback=lambda: Small(speech="fallback", score=1)
        )
    assert result.used_fallback
    assert result.value.speech == "fallback"


async def test_structured_endpoint_down_without_fallback_raises(llm) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{MAIN}/chat/completions").respond(503)
        with pytest.raises(AIUnavailable):
            await llm.structured(Small, MSGS, context=EVAL)


# ------------------------------------------------------------ SSE helpers


def test_sse_decoder_multiline_comments_and_event_names() -> None:
    decoder = SSEDecoder()
    assert decoder.feed_line(": keep-alive") is None
    assert decoder.feed_line("event: message") is None
    assert decoder.feed_line('data: {"a":') is None
    assert decoder.feed_line("data: 1}") is None
    event = decoder.feed_line("")
    assert event is not None and event.event == "message" and event.data == '{"a":\n1}'
    assert decoder.feed_line("") is None
    decoder.feed_line("data:[DONE]")
    assert decoder.flush().data == "[DONE]"


def test_parse_event_data_tolerates_missing_blank_lines() -> None:
    merged = '{"choices": [1]}\n{"choices": [2]}'
    assert [o["choices"][0] for o in parse_event_data(merged)] == [1, 2]
    assert parse_event_data('{"x": 1}') == [{"x": 1}]


@pytest.mark.parametrize("seed", range(20))
def test_think_filter_with_random_splits(seed: int) -> None:
    rng = random.Random(seed)
    text = '<think>\nplan {not json}\n</think>\n\n{"speech": "hi"}'
    f = ThinkFilter()
    out, i = [], 0
    while i < len(text):
        size = rng.randint(1, 5)
        out.append(f.feed(text[i : i + size]))
        i += size
    out.append(f.flush())
    assert "".join(out) == '{"speech": "hi"}'


def test_think_filter_passthrough_and_strip_think() -> None:
    f = ThinkFilter()
    assert f.feed("<th") == ""
    assert f.feed("ought") == "<thought"
    assert strip_think("plain") == "plain"
    assert strip_think("<think>x</think>answer") == "answer"
