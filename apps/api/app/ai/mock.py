"""Deterministic in-process implementations of every AI protocol.

Install with ``app.ai.registry.set_ai(MockAIServices())`` (the ``mock_ai``
pytest fixture does this). Nothing here touches the network.

``MockLLM``
    Responses are scripted per task: ``mock_ai.llm.script(ModelTask.TURN_DECISION, decision)``.
    A script item may be a Pydantic instance, a dict/list (sent as JSON), a
    string (sent verbatim, so invalid JSON exercises the repair/retry path),
    an exception (raised), or a callable ``(MockLLMCall) -> item``. Items are
    consumed in order; ``set_default`` gives a sticky response. Unscripted
    ``structured()`` calls return a valid minimal instance generated from the
    schema; unscripted ``chat()`` with ``json_schema`` returns minimal JSON.
    ``stream()`` yields the text in small chunks and honours ``aclose()``.
    Every call is recorded in ``calls``.

``MockEmbeddings``
    Hashed bag-of-words (plus character trigrams), L2-normalised: identical
    texts have cosine 1.0, paraphrases sharing most words score high,
    unrelated texts score near 0.

``MockReranker``
    Score = fraction of query tokens present in the document.

``MockStt`` / ``MockOcr``
    Scripted queues with deterministic defaults.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from app.ai.base import (
    CallContext,
    ChatMessage,
    Endpoint,
    LLMResult,
    OcrResult,
    RerankScore,
    StructuredResult,
    TranscriptionResult,
)
from app.ai.clients.tts import MockTtsProvider
from app.ai.router import ModelRouter
from app.ai.structured import response_format, run_structured, schema_for
from app.domain.enums import ModelTask

DEFAULT_DIMENSIONS = 1024  # Qwen3-Embedding-0.6B; matches app.db.base.EMBEDDING_DIM

# ============================================================================
# Minimal valid instances from JSON schema
# ============================================================================

_STRING_HINTS = {
    "speech": "Could you walk me through a recent project you worked on?",
    "text": "Mock text.",
    "summary": "Mock summary.",
    "reason": "Mock reason.",
}
_FORMATS = {
    "date-time": "2026-01-01T00:00:00Z",
    "date": "2026-01-01",
    "time": "00:00:00",
    "email": "mock@example.com",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "uri": "https://example.com/",
}


def _resolve(node: Mapping[str, Any], defs: Mapping[str, Any]) -> Mapping[str, Any]:
    seen = 0
    while "$ref" in node and seen < 32:
        node = defs[node["$ref"].split("/")[-1]]
        seen += 1
    return node


def _string_value(node: Mapping[str, Any], name: str | None, index: int) -> str:
    if node.get("format") in _FORMATS:
        return _FORMATS[node["format"]]
    base = _STRING_HINTS.get(name or "", f"mock {name}" if name else "mock")
    if index:
        base = f"{base} {index + 1}"
    min_len = int(node.get("minLength", 0))
    max_len = node.get("maxLength")
    if len(base) < min_len:
        base = base + "x" * (min_len - len(base))
    if max_len is not None:
        base = base[: int(max_len)]
        if len(base) < min_len:
            base = "x" * min_len
    return base


def _number_value(node: Mapping[str, Any], *, integer: bool) -> int | float:
    step = 1 if integer else 1e-6
    lo = node.get("minimum")
    if "exclusiveMinimum" in node:
        lo = node["exclusiveMinimum"] + step
    hi = node.get("maximum")
    if "exclusiveMaximum" in node:
        hi = node["exclusiveMaximum"] - step
    value: float = lo if lo is not None else (0 if hi is None or hi >= 0 else hi)
    if hi is not None and value > hi:
        value = hi
    return math.ceil(value) if integer else float(value)


def _generate(
    node: Mapping[str, Any], defs: Mapping[str, Any], name: str | None, index: int = 0, depth: int = 0
) -> Any:
    if depth > 24:
        return None
    node = _resolve(node, defs)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in node:
            options = [_resolve(opt, defs) for opt in node[key]]
            if any(opt.get("type") == "null" for opt in options):
                return None
            return _generate(options[0], defs, name, index, depth + 1)
    if "allOf" in node:
        merged: dict[str, Any] = {}
        for part in node["allOf"]:
            merged.update(_resolve(part, defs))
        return _generate(merged, defs, name, index, depth + 1)
    kind = node.get("type")
    if isinstance(kind, list):
        if "null" in kind:
            return None
        kind = kind[0] if kind else None
    if kind == "object" or "properties" in node:
        props = node.get("properties", {})
        return {
            key: _generate(props.get(key, {}), defs, key, 0, depth + 1) for key in node.get("required", [])
        }
    if kind == "array":
        items = node.get("items", {})
        count = int(node.get("minItems", 0))
        return [_generate(items, defs, name, i, depth + 1) for i in range(count)]
    if kind == "string":
        return _string_value(node, name, index)
    if kind == "integer":
        return _number_value(node, integer=True)
    if kind == "number":
        return _number_value(node, integer=False)
    if kind == "boolean":
        return False
    return None


def minimal_payload_from_json_schema(schema: Mapping[str, Any]) -> Any:
    return _generate(schema, schema.get("$defs", {}), None)


def minimal_payload(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON-compatible dict holding only the required fields, each with the
    smallest valid value (first enum member, lower numeric bound, null where
    allowed, ``minItems`` elements)."""
    return minimal_payload_from_json_schema(schema_for(schema))


def minimal_instance[T: BaseModel](schema: type[T]) -> T:
    return schema.model_validate(minimal_payload(schema))


# ============================================================================
# LLM
# ============================================================================


class _Unset:
    pass


_UNSET = _Unset()


@dataclass(slots=True)
class MockLLMCall:
    method: Literal["chat", "stream", "structured"]
    task: ModelTask
    messages: list[ChatMessage]
    endpoint: Endpoint | None
    schema: type[BaseModel] | None = None
    json_schema: dict[str, Any] | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    context: CallContext | None = None


def _schema_properties(json_schema: dict[str, Any] | None) -> list[str] | None:
    if json_schema is None:
        return None
    schema = response_format(json_schema)["json_schema"]["schema"]
    properties = schema.get("properties") if isinstance(schema, dict) else None
    return list(properties) if isinstance(properties, dict) else None


def _to_text(value: Any, json_schema: dict[str, Any] | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, BaseModel):
        properties = _schema_properties(json_schema)
        if properties is not None and set(type(value).model_fields) - set(properties):
            # Guided decoding emits exactly the requested schema's fields, in its order: a scripted richer object
            # (e.g. a full TurnDecision for the compact turn schema) is narrowed the same way.
            data = value.model_dump(mode="json")
            return json.dumps({k: data[k] for k in properties if k in data}, ensure_ascii=False)
        return value.model_dump_json()
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


class MockLLMStream:
    def __init__(
        self, text_factory: Callable[[], str], *, chunk_size: int, delay: float, endpoint: Endpoint
    ) -> None:
        self._factory = text_factory
        self._chunk_size = max(1, chunk_size)
        self._delay = delay
        self._endpoint = endpoint
        self._chunks: deque[str] | None = None
        self._parts: list[str] = []
        self._closed = False
        self.model_call_id = str(uuid.uuid4())
        self.result: LLMResult | None = None
        self.endpoint: Endpoint = endpoint
        self.ttft_ms: float | None = None
        self.finish_reason: str | None = None

    @property
    def text(self) -> str:
        return "".join(self._parts)

    @property
    def closed(self) -> bool:
        return self._closed

    def __aiter__(self) -> MockLLMStream:
        return self

    async def __anext__(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        if self._chunks is None:
            text = self._factory()  # may raise a scripted exception
            self._chunks = deque(
                text[i : i + self._chunk_size] for i in range(0, len(text), self._chunk_size)
            )
            self.ttft_ms = 0.0
        if not self._chunks:
            self.finish_reason = "stop"
            self._finish()
            raise StopAsyncIteration
        if self._delay:
            await asyncio.sleep(self._delay)
            if self._closed:
                raise StopAsyncIteration
        chunk = self._chunks.popleft()
        self._parts.append(chunk)
        return chunk

    async def aclose(self) -> None:
        if not self._closed:
            self._finish()

    async def __aenter__(self) -> MockLLMStream:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _finish(self) -> None:
        self._closed = True
        self.result = LLMResult(
            text=self.text,
            endpoint=self._endpoint,
            model="mock",
            prompt_tokens=None,
            completion_tokens=len(self.text.split()),
            latency_ms=0.0,
            ttft_ms=self.ttft_ms,
            finish_reason=self.finish_reason,
            model_call_id=self.model_call_id,
        )


class MockLLM:
    def __init__(self, *, chunk_size: int = 6, chunk_delay: float = 0.0) -> None:
        self.chunk_size = chunk_size
        self.chunk_delay = chunk_delay
        self.calls: list[MockLLMCall] = []
        self._queues: dict[ModelTask | None, deque[Any]] = defaultdict(deque)
        self._defaults: dict[ModelTask | None, Any] = {}
        self._router = ModelRouter()

    # ------------------------------------------------------------ scripting
    def script(self, task: ModelTask | None, *responses: Any) -> MockLLM:
        """Queue responses for ``task`` (``None`` = any task)."""
        self._queues[task].extend(responses)
        return self

    def fail_next(self, task: ModelTask | None, *errors: BaseException) -> MockLLM:
        """The next calls for ``task`` raise ``errors`` (in order), before anything already scripted."""
        self._queues[task].extendleft(reversed(errors))
        return self

    def set_default(self, task: ModelTask | None, response: Any) -> MockLLM:
        self._defaults[task] = response
        return self

    def reset(self) -> None:
        self.calls.clear()
        self._queues.clear()
        self._defaults.clear()

    def calls_for(self, task: ModelTask) -> list[MockLLMCall]:
        return [call for call in self.calls if call.task == task]

    def pending(self, task: ModelTask | None = None) -> int:
        return len(self._queues.get(task, ()))

    def _next(self, task: ModelTask) -> Any:
        for key in (task, None):
            queue = self._queues.get(key)
            if queue:
                return queue.popleft()
        for key in (task, None):
            if key in self._defaults:
                return self._defaults[key]
        return _UNSET

    @staticmethod
    def _resolve(item: Any, call: MockLLMCall) -> Any:
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("scripted failure")
        if callable(item) and not isinstance(item, type | BaseModel):
            item = item(call)
            if isinstance(item, BaseException):
                raise item
        return item

    def _endpoint(self, task: ModelTask, explicit: Endpoint | None) -> Endpoint:
        return explicit or self._router.select(task).endpoint

    def _text_for(self, call: MockLLMCall) -> str:
        item = self._next(call.task)
        if item is _UNSET:
            if call.json_schema is not None:
                schema = response_format(call.json_schema)["json_schema"]["schema"]
                return json.dumps(minimal_payload_from_json_schema(schema))
            return f"Mock response for {call.task.value}."
        return _to_text(self._resolve(item, call), call.json_schema)

    # -------------------------------------------------------------- client
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        context: CallContext,
        endpoint: Endpoint | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
        thinking: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109 - protocol signature
    ) -> LLMResult:
        call = MockLLMCall(
            "chat",
            context.task,
            list(messages),
            endpoint,
            json_schema=json_schema,
            kwargs={"temperature": temperature, "max_tokens": max_tokens, "thinking": thinking},
            attempts=1,
            context=context,
        )
        self.calls.append(call)
        text = self._text_for(call)
        return LLMResult(
            text=text,
            endpoint=self._endpoint(context.task, endpoint),
            model="mock",
            prompt_tokens=sum(len(str(m.get("content", "")).split()) for m in messages),
            completion_tokens=len(text.split()),
            latency_ms=0.0,
            finish_reason="stop",
            model_call_id=str(uuid.uuid4()),
        )

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        context: CallContext,
        endpoint: Endpoint | None = None,
        temperature: float = 0.4,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
        thinking: bool = False,
        timeout: float | None = None,
    ) -> MockLLMStream:
        call = MockLLMCall(
            "stream",
            context.task,
            list(messages),
            endpoint,
            json_schema=json_schema,
            kwargs={"temperature": temperature, "max_tokens": max_tokens, "thinking": thinking},
            attempts=1,
            context=context,
        )
        self.calls.append(call)
        return MockLLMStream(
            lambda: self._text_for(call),
            chunk_size=self.chunk_size,
            delay=self.chunk_delay,
            endpoint=self._endpoint(context.task, endpoint),
        )

    async def structured[T: BaseModel](
        self,
        schema: type[T],
        messages: Sequence[ChatMessage],
        *,
        context: CallContext,
        endpoint: Endpoint | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        thinking: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109 - protocol signature
        max_attempts: int = 3,
        fallback: Callable[[], T] | None = None,
    ) -> StructuredResult[T]:
        call = MockLLMCall(
            "structured",
            context.task,
            list(messages),
            endpoint,
            schema=schema,
            kwargs={"temperature": temperature, "max_tokens": max_tokens, "thinking": thinking},
            context=context,
        )
        self.calls.append(call)

        async def generate(conversation: list[ChatMessage], attempt: int) -> str:
            call.attempts = attempt
            item = self._next(context.task)
            if item is _UNSET:
                return json.dumps(minimal_payload(schema))
            value = self._resolve(item, call)
            if isinstance(value, schema):
                return value.model_dump_json()
            return _to_text(value, {"name": schema.__name__, "schema": schema_for(schema)})

        outcome = await run_structured(
            schema, messages, generate, max_attempts=max_attempts, fallback=fallback
        )
        return StructuredResult(
            value=outcome.value,
            raw_text=outcome.raw_text,
            repaired=outcome.repaired,
            attempts=outcome.attempts,
            used_fallback=outcome.used_fallback,
            latency_ms=0.0,
            model_call_id=str(uuid.uuid4()),
        )


# ============================================================================
# Embeddings / reranker
# ============================================================================

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have how i if in into is it its "
    "me my of on or our so that the their them then there these they this to us was we were what when where "
    "which who why will with would you your".split()
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in _WORD.findall(text.lower()):
        if token in STOPWORDS:
            continue
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def _bucket(feature: str, dims: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest[:4], "little") % dims, (1.0 if digest[4] & 1 else -1.0)


class MockEmbeddings:
    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def vector(self, text: str) -> list[float]:
        vec = np.zeros(self.dimensions, dtype=np.float64)
        tokens = tokenize(text)
        for token in tokens:
            idx, sign = _bucket("w:" + token, self.dimensions)
            vec[idx] += sign
            padded = f"#{token}#"
            for i in range(len(padded) - 2):
                idx, sign = _bucket("c:" + padded[i : i + 3], self.dimensions)
                vec[idx] += 0.25 * sign
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            vec[0] = 1.0
            norm = 1.0
        return (vec / norm).astype(np.float32).tolist()

    async def embed(self, texts: Sequence[str], *, context: CallContext | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vector(text) for text in texts]

    async def embed_one(self, text: str, *, context: CallContext | None = None) -> list[float]:
        return (await self.embed([text], context=context))[0]

    async def embed_documents(
        self, texts: Sequence[str], *, context: CallContext | None = None
    ) -> list[list[float]]:
        return await self.embed(texts, context=context)

    async def embed_query(
        self, text: str, instruction: str | None = None, *, context: CallContext | None = None
    ) -> list[float]:
        return (await self.embed([text], context=context))[0]


class MockReranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.fail_with: BaseException | None = None

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        instruction: str | None = None,
        top_k: int | None = None,
        context: CallContext | None = None,
    ) -> list[RerankScore]:
        self.calls.append((query, list(documents)))
        if self.fail_with is not None:
            raise self.fail_with
        query_tokens = set(tokenize(query))
        scores = []
        for index, document in enumerate(documents):
            doc_tokens = set(tokenize(document))
            overlap = len(query_tokens & doc_tokens) / len(query_tokens) if query_tokens else 0.0
            scores.append(RerankScore(index=index, score=round(overlap, 6)))
        scores.sort(key=lambda s: (-s.score, s.index))
        return scores[:top_k] if top_k is not None else scores


# ============================================================================
# STT / OCR
# ============================================================================


class MockStt:
    def __init__(self, default_text: str = "This is a mock transcription.") -> None:
        self.default_text = default_text
        self.calls: list[dict[str, Any]] = []
        self._finals: deque[Any] = deque()
        self._partials: deque[Any] = deque()

    def script(self, *items: Any) -> MockStt:
        """Queue results for final transcriptions (str, TranscriptionResult,
        exception or callable ``(audio, kwargs) -> item``)."""
        self._finals.extend(items)
        return self

    def script_partial(self, *items: Any) -> MockStt:
        self._partials.extend(items)
        return self

    async def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str = "audio/wav",
        prompt: str | None = None,
        language: str | None = None,
        partial: bool = False,
        context: CallContext | None = None,
    ) -> TranscriptionResult:
        kwargs = {"content_type": content_type, "prompt": prompt, "language": language, "partial": partial}
        self.calls.append({"bytes": len(audio), **kwargs})
        queue = self._partials if partial else self._finals
        item: Any = queue.popleft() if queue else self.default_text
        if callable(item) and not isinstance(item, type):
            item = item(audio, kwargs)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, TranscriptionResult):
            return item
        duration = max(0.0, (len(audio) - 44) / 32000) if audio else 0.0  # 16 kHz PCM16 mono estimate
        return TranscriptionResult(
            text=str(item),
            language=language or "en",
            duration_s=round(duration, 3),
            no_speech_prob=0.0,
            endpoint="mock",
        )


class MockOcr:
    def __init__(self, default_text: str = "Mock OCR text") -> None:
        self.default_text = default_text
        self.calls: list[dict[str, Any]] = []
        self._queue: deque[Any] = deque()

    def script(self, *items: Any) -> MockOcr:
        self._queue.extend(items)
        return self

    async def ocr_image(
        self,
        image: bytes,
        *,
        mime_type: str = "image/png",
        prompt: str | None = None,
        context: CallContext | None = None,
    ) -> OcrResult:
        self.calls.append({"bytes": len(image), "mime_type": mime_type, "prompt": prompt})
        item: Any = self._queue.popleft() if self._queue else self.default_text
        if callable(item) and not isinstance(item, type):
            item = item(image)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, OcrResult):
            return item
        text = str(item)
        return OcrResult(text=text, status="ok" if text.strip() else "empty", raw=text)


# ============================================================================
# Services
# ============================================================================


class MockAIServices:
    def __init__(self, *, dimensions: int = DEFAULT_DIMENSIONS, tts_sample_rate: int = 24000) -> None:
        self.llm = MockLLM()
        self.embeddings = MockEmbeddings(dimensions)
        self.reranker = MockReranker()
        self.stt = MockStt()
        self.ocr = MockOcr()
        self.tts = MockTtsProvider(tts_sample_rate)
        self.closed = False

    async def health(self) -> dict[str, dict[str, Any]]:
        names = ("main", "vision", "embedding", "reranker", "whisper", "ocr", "tts")
        return {name: {"status": "ok", "latency_ms": 0.0, "detail": "mock"} for name in names}

    async def aclose(self) -> None:
        self.closed = True

    def reset(self) -> None:
        self.llm.reset()
        self.embeddings.calls.clear()
        self.reranker.calls.clear()
        self.reranker.fail_with = None
        self.stt = MockStt()
        self.ocr = MockOcr()
        self.tts = MockTtsProvider(self.tts.sample_rate)
