"""OpenAI-compatible chat client for the local vLLM endpoints (raw httpx).

* ``chat()``: one completion, routed by task, with a fallback endpoint for
  turn-critical tasks when the primary is unavailable.
* ``stream()``: returns an :class:`LLMStream` (an async iterator of content
  deltas). Reasoning deltas are ignored, usage arrives through
  ``stream_options.include_usage``, and ``aclose()`` / task cancellation
  closes the HTTP stream immediately so vLLM aborts the generation
  (barge-in).
* ``structured()``: guided JSON (``response_format`` json_schema, strict) +
  the parse/repair/retry pipeline in :mod:`app.ai.structured`.

Requests to the main endpoint carry ``chat_template_kwargs.enable_thinking``.
Every logical call is measured (``interview_llm_*``) and written to the
model_calls ledger.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from app.ai.base import (
    AIError,
    AIOutputInvalid,
    AIRejected,
    AITimeout,
    AIUnavailable,
    CallContext,
    ChatMessage,
    Endpoint,
    LLMResult,
    StructuredResult,
)
from app.ai.http import EndpointClient, call_status, failure_reason
from app.ai.ledger import LedgerSink, ModelCallRecord, NullLedger, messages_payload, text_payload
from app.ai.router import ModelRouter, RouteDecision, has_image_content
from app.ai.structured import response_format, run_structured, schema_for
from app.core import metrics
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus

log = get_logger(__name__)

_CHAT_PATH = "/chat/completions"


# ============================================================================
# SSE decoding
# ============================================================================


@dataclass(slots=True)
class SSEEvent:
    data: str
    event: str | None = None


class SSEDecoder:
    """Line-oriented Server-Sent Events decoder (WHATWG rules: ``data:``
    lines accumulate until a blank line; comments start with ``:``)."""

    def __init__(self) -> None:
        self._data: list[str] = []
        self._event: str | None = None

    def feed_line(self, line: str) -> SSEEvent | None:
        line = line.rstrip("\r")
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        return None

    def flush(self) -> SSEEvent | None:
        return self._dispatch()

    def _dispatch(self) -> SSEEvent | None:
        if not self._data:
            self._event = None
            return None
        event = SSEEvent("\n".join(self._data), self._event)
        self._data = []
        self._event = None
        return event


def parse_event_data(data: str) -> list[dict[str, Any]]:
    """JSON objects carried by one SSE event (tolerates servers that omit the
    blank line between events, which merges several data lines)."""
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        objects: list[dict[str, Any]] = []
        for part in data.split("\n"):
            part = part.strip()
            if not part or part == "[DONE]":
                continue
            try:
                parsed = json.loads(part)
            except json.JSONDecodeError:
                log.warning("ai.llm.sse_undecodable", size=len(part))
                continue
            if isinstance(parsed, dict):
                objects.append(parsed)
        return objects
    return [obj] if isinstance(obj, dict) else []


class ThinkFilter:
    """Suppresses a leading ``<think>...</think>`` block in streamed content
    (servers without a reasoning parser inline it). Tags may be split across
    deltas."""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._state = "detect"  # detect | thinking | after | pass
        self._buffer = ""

    def feed(self, text: str) -> str:
        if self._state == "pass":
            return text
        if self._state == "after":  # drop whitespace between </think> and the answer
            text = text.lstrip()
            if text:
                self._state = "pass"
            return text
        self._buffer += text
        if self._state == "detect":
            stripped = self._buffer.lstrip()
            if not stripped:
                return ""
            if stripped.startswith(self._OPEN):
                self._state = "thinking"
                self._buffer = stripped[len(self._OPEN) :]
            elif self._OPEN.startswith(stripped):
                return ""  # could still become "<think>"
            else:
                self._state = "pass"
                out, self._buffer = self._buffer, ""
                return out
        if self._state == "thinking":
            idx = self._buffer.find(self._CLOSE)
            if idx < 0:
                self._buffer = self._buffer[-(len(self._CLOSE) - 1) :]
                return ""
            out = self._buffer[idx + len(self._CLOSE) :].lstrip()
            self._buffer = ""
            self._state = "pass" if out else "after"
            return out
        return ""

    def flush(self) -> str:
        if self._state == "detect":
            out, self._buffer = self._buffer, ""
            return out
        return ""


def strip_think(text: str) -> str:
    if "<think>" not in text and "</think>" not in text:
        return text
    f = ThinkFilter()
    return f.feed(text) + f.flush()


# ============================================================================
# Client
# ============================================================================


@dataclass(slots=True)
class _Completion:
    text: str
    endpoint: Endpoint
    model: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    attempts: int
    latency_ms: float
    fell_back: bool = False


def request_hash(model: str, messages: Sequence[ChatMessage], extra: Mapping[str, Any]) -> str:
    payload = json.dumps(
        [model, list(messages), dict(extra)], sort_keys=True, default=str, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _usage(obj: Mapping[str, Any]) -> tuple[int | None, int | None]:
    usage = obj.get("usage")
    if not isinstance(usage, Mapping):
        return None, None
    return _int_or_none(usage.get("prompt_tokens")), _int_or_none(usage.get("completion_tokens"))


def _record_tokens(endpoint: str, task: str, prompt: int | None, completion: int | None) -> None:
    if prompt:
        metrics.LLM_TOKENS.labels(endpoint=endpoint, task=task, kind="prompt").inc(prompt)
    if completion:
        metrics.LLM_TOKENS.labels(endpoint=endpoint, task=task, kind="completion").inc(completion)


COMPACT_JSON_HINT = (
    "Output format: write the JSON object on a single line with no indentation, no line breaks "
    "and no spaces between tokens outside string values."
)


def with_compact_json_hint(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    """Ask for minified JSON under guided decoding.

    vLLM's guided decoding lets the model pick any whitespace, and this build
    ignores ``disable_any_whitespace``: measured 2026-10-07, pretty-printed JSON
    was about half of all generated tokens (1891 -> 933 tokens for a resume
    extraction with the hint). The hint goes into the first system message so it
    sits in the cached prompt prefix."""
    out = [dict(m) for m in messages]
    if out and out[0].get("role") == "system" and isinstance(out[0].get("content"), str):
        if COMPACT_JSON_HINT not in out[0]["content"]:
            out[0]["content"] = f"{out[0]['content']}\n\n{COMPACT_JSON_HINT}"
    else:
        out.insert(0, {"role": "system", "content": COMPACT_JSON_HINT})
    return out


def _schema_name(json_schema: Mapping[str, Any] | None) -> str | None:
    if json_schema is None:
        return None
    return str(response_format(json_schema)["json_schema"]["name"])


class OpenAICompatibleLLM:
    """``LLMClient`` over the main and vision vLLM endpoints."""

    def __init__(
        self,
        *,
        endpoints: Mapping[str, EndpointClient],
        models: Mapping[str, str],
        router: ModelRouter,
        ledger: LedgerSink | None = None,
        thinking_endpoints: Collection[str] = ("main",),
    ) -> None:
        missing = {"main", "vision"} - set(endpoints)
        if missing:
            raise ValueError(f"missing LLM endpoints: {sorted(missing)}")
        self._endpoints = dict(endpoints)
        self._models = dict(models)
        self._router = router
        self._ledger: LedgerSink = ledger or NullLedger()
        self._thinking_endpoints = frozenset(thinking_endpoints)

    @property
    def router(self) -> ModelRouter:
        return self._router

    def endpoint_client(self, endpoint: Endpoint) -> EndpointClient:
        return self._endpoints[endpoint]

    # ------------------------------------------------------------ payloads
    def build_body(
        self,
        endpoint: Endpoint,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: Mapping[str, Any] | None,
        thinking: bool,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._models[endpoint],
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if endpoint in self._thinking_endpoints:
            body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
        if json_schema is not None:
            body["response_format"] = response_format(json_schema)
            body["messages"] = with_compact_json_hint(body["messages"])
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    # ----------------------------------------------------------- one call
    async def _complete(
        self,
        endpoint: Endpoint,
        messages: Sequence[ChatMessage],
        *,
        task: str,
        temperature: float,
        max_tokens: int,
        json_schema: Mapping[str, Any] | None,
        thinking: bool,
        timeout: float,  # noqa: ASYNC109 - deadline budget handed to httpx
    ) -> _Completion:
        client = self._endpoints[endpoint]
        body = self.build_body(
            endpoint,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=json_schema,
            thinking=thinking,
            stream=False,
        )
        started = time.perf_counter()
        try:
            # idempotent=False: a read timeout on a generation is not retried
            # (it would double the caller's latency budget).
            result = await client.request(
                "POST", _CHAT_PATH, json=body, timeout=timeout, idempotent=False, task=task
            )
            data = result.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices or not isinstance(choices[0], Mapping):
                raise AIUnavailable(f"{endpoint}: malformed completion response", endpoint=endpoint)
        except asyncio.CancelledError:
            metrics.LLM_REQUEST_SECONDS.labels(endpoint=endpoint, task=task, status="cancelled").observe(
                time.perf_counter() - started
            )
            raise
        except AIError as exc:
            metrics.LLM_REQUEST_SECONDS.labels(
                endpoint=endpoint, task=task, status=call_status(exc).value
            ).observe(time.perf_counter() - started)
            raise
        elapsed = time.perf_counter() - started
        choice = choices[0]
        message = choice.get("message") or {}
        text = strip_think(str(message.get("content") or ""))
        prompt_tokens, completion_tokens = _usage(data)
        metrics.LLM_REQUEST_SECONDS.labels(endpoint=endpoint, task=task, status="ok").observe(elapsed)
        _record_tokens(endpoint, task, prompt_tokens, completion_tokens)
        return _Completion(
            text=text,
            endpoint=endpoint,
            model=str(data.get("model") or self._models[endpoint]),
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            attempts=result.attempts,
            latency_ms=elapsed * 1000,
        )

    async def _complete_routed(
        self,
        route: RouteDecision,
        messages: Sequence[ChatMessage],
        *,
        task: str,
        temperature: float,
        max_tokens: int,
        json_schema: Mapping[str, Any] | None,
        thinking: bool,
        deadline: float,
    ) -> _Completion:
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AITimeout(f"{task}: time budget exhausted", endpoint=route.endpoint)
        kwargs: dict[str, Any] = {
            "task": task,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
            "thinking": thinking,
        }
        try:
            return await self._complete(route.endpoint, messages, timeout=remaining, **kwargs)
        except AIUnavailable as exc:
            remaining = deadline - loop.time()
            if route.fallback is None or remaining <= 0.05:
                raise
            log.warning(
                "ai.llm.fallback", task=task, primary=route.endpoint, fallback=route.fallback, error=str(exc)
            )
            completion = await self._complete(route.fallback, messages, timeout=remaining, **kwargs)
            completion.fell_back = True
            return completion

    def _deadline(self, route: RouteDecision, timeout: float | None) -> float:
        budget = timeout if timeout is not None else self._endpoints[route.endpoint].timeout
        return asyncio.get_running_loop().time() + max(budget, 0.001)

    # ------------------------------------------------------------- ledger
    def _record(
        self,
        *,
        call_id: uuid.UUID,
        context: CallContext,
        endpoint: str,
        status: ModelCallStatus,
        started: float,
        messages: Sequence[ChatMessage],
        json_schema: Mapping[str, Any] | None,
        temperature: float,
        max_tokens: int,
        attempts: int = 1,
        ttft_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        repaired: bool = False,
        used_fallback: bool = False,
        error: BaseException | str | None = None,
        response_text: str | None = None,
    ) -> None:
        model = self._models.get(endpoint, endpoint)
        schema_name = _schema_name(json_schema)
        log_payloads = getattr(self._ledger, "log_payloads", False)
        try:
            record = ModelCallRecord(
                id=call_id,
                task=context.task.value,
                endpoint=endpoint,
                model=model,
                status=status.value,
                attempts=attempts,
                latency_ms=(time.perf_counter() - started) * 1000,
                ttft_ms=ttft_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                repaired=repaired,
                used_fallback=used_fallback,
                prompt_version=context.prompt_version,
                request_hash=request_hash(
                    model,
                    messages,
                    {"schema": schema_name, "temperature": temperature, "max_tokens": max_tokens},
                ),
                error=None
                if error is None
                else (error if isinstance(error, str) else f"{type(error).__name__}: {error}"),
                request_payload=(
                    {
                        "messages": messages_payload(messages),
                        "schema": schema_name,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                    if log_payloads
                    else None
                ),
                response_payload=text_payload(response_text)
                if log_payloads and response_text is not None
                else None,
                session_id=context.session_id,
                organization_id=context.organization_id,
            )
            self._ledger.record(record)
        except Exception as exc:  # noqa: BLE001 - bookkeeping must never fail a model call
            log.warning("ai.ledger.record_failed", error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _count_failure(endpoint: str, task: str, exc: BaseException) -> None:
        metrics.MODEL_CALL_FAILURES.labels(endpoint=endpoint, task=task, reason=failure_reason(exc)).inc()

    # --------------------------------------------------------------- chat
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
        timeout: float | None = None,  # noqa: ASYNC109 - protocol signature (total budget)
    ) -> LLMResult:
        task = context.task.value
        route = self._router.select(context.task, has_images=has_image_content(messages), explicit=endpoint)
        deadline = self._deadline(route, timeout)
        call_id = uuid.uuid4()
        started = time.perf_counter()
        common: dict[str, Any] = {
            "call_id": call_id,
            "context": context,
            "started": started,
            "messages": messages,
            "json_schema": json_schema,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            completion = await self._complete_routed(
                route,
                messages,
                task=task,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=json_schema,
                thinking=thinking,
                deadline=deadline,
            )
        except asyncio.CancelledError:
            self._record(endpoint=route.endpoint, status=ModelCallStatus.CANCELLED, **common)
            raise
        except AIError as exc:
            failed_on = exc.endpoint if exc.endpoint in ("main", "vision") else route.endpoint
            self._count_failure(failed_on, task, exc)
            self._record(endpoint=failed_on, status=call_status(exc), error=exc, **common)
            raise
        self._record(
            endpoint=completion.endpoint,
            status=ModelCallStatus.RETRIED if completion.attempts > 1 else ModelCallStatus.OK,
            attempts=completion.attempts,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            response_text=completion.text,
            **common,
        )
        return LLMResult(
            text=completion.text,
            endpoint=completion.endpoint,
            model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=completion.finish_reason,
            model_call_id=str(call_id),
        )

    # ------------------------------------------------------------- stream
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
    ) -> LLMStream:
        return LLMStream(
            self,
            messages,
            context=context,
            endpoint=endpoint,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=json_schema,
            thinking=thinking,
            timeout=timeout,
        )

    # --------------------------------------------------------- structured
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
        timeout: float | None = None,  # noqa: ASYNC109 - protocol signature (total budget)
        max_attempts: int = 3,
        fallback: Callable[[], T] | None = None,
    ) -> StructuredResult[T]:
        task = context.task.value
        route = self._router.select(context.task, has_images=has_image_content(messages), explicit=endpoint)
        deadline = self._deadline(route, timeout)
        fmt = {"name": schema.__name__, "schema": schema_for(schema), "strict": True}
        call_id = uuid.uuid4()
        started = time.perf_counter()
        stats: dict[str, Any] = {"endpoint": route.endpoint, "http_attempts": 0, "prompt": 0, "completion": 0}
        last_exc: list[AIError] = []

        async def generate(conversation: list[ChatMessage], attempt: int) -> str:
            try:
                completion = await self._complete_routed(
                    route,
                    conversation,
                    task=task,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_schema=fmt,
                    thinking=thinking,
                    deadline=deadline,
                )
            except AIError as exc:
                last_exc.append(exc)
                raise
            stats["endpoint"] = completion.endpoint
            stats["http_attempts"] += completion.attempts
            stats["prompt"] += completion.prompt_tokens or 0
            stats["completion"] += completion.completion_tokens or 0
            if completion.finish_reason == "length":
                log.warning("ai.llm.structured_truncated", task=task, attempt=attempt, max_tokens=max_tokens)
            return completion.text

        common: dict[str, Any] = {
            "call_id": call_id,
            "context": context,
            "started": started,
            "messages": messages,
            "json_schema": fmt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            outcome = await run_structured(
                schema, messages, generate, max_attempts=max_attempts, fallback=fallback
            )
        except asyncio.CancelledError:
            self._record(endpoint=stats["endpoint"], status=ModelCallStatus.CANCELLED, **common)
            raise
        except AIError as exc:
            self._count_failure(stats["endpoint"], task, exc)
            self._record(
                endpoint=stats["endpoint"],
                status=call_status(exc),
                attempts=max(1, stats["http_attempts"]),
                prompt_tokens=stats["prompt"] or None,
                completion_tokens=stats["completion"] or None,
                error=exc,
                **common,
            )
            raise
        if outcome.used_fallback:
            status = ModelCallStatus.FALLBACK
            self._count_failure(stats["endpoint"], task, last_exc[-1] if last_exc else _INVALID)
            log.warning("ai.llm.structured_fallback", task=task, schema=schema.__name__, reason=outcome.error)
        elif outcome.repaired:
            status = ModelCallStatus.REPAIRED
        elif outcome.attempts > 1 or stats["http_attempts"] > outcome.attempts:
            status = ModelCallStatus.RETRIED
        else:
            status = ModelCallStatus.OK
        self._record(
            endpoint=stats["endpoint"],
            status=status,
            attempts=max(outcome.attempts, stats["http_attempts"]),
            prompt_tokens=stats["prompt"] or None,
            completion_tokens=stats["completion"] or None,
            repaired=outcome.repaired,
            used_fallback=outcome.used_fallback,
            error=outcome.error,
            response_text=outcome.raw_text,
            **common,
        )
        return StructuredResult(
            value=outcome.value,
            raw_text=outcome.raw_text,
            repaired=outcome.repaired,
            attempts=outcome.attempts,
            used_fallback=outcome.used_fallback,
            latency_ms=(time.perf_counter() - started) * 1000,
            model_call_id=str(call_id),
        )


_INVALID = AIOutputInvalid("structured output invalid")


# ============================================================================
# Streaming
# ============================================================================


class LLMStream:
    """Async iterator of content deltas for one streamed completion.

    Use ``async for delta in stream`` (or ``async with stream:``). After the
    iteration ends, ``result`` holds the :class:`LLMResult` (full text,
    usage, TTFT). ``aclose()`` may be called from any task: it closes the
    HTTP response at once, which makes vLLM abort the generation.
    """

    def __init__(
        self,
        owner: OpenAICompatibleLLM,
        messages: Sequence[ChatMessage],
        *,
        context: CallContext,
        endpoint: Endpoint | None,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None,
        thinking: bool,
        timeout: float | None,
    ) -> None:
        self._owner = owner
        self._messages = list(messages)
        self._context = context
        self._explicit = endpoint
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._json_schema = json_schema
        self._thinking = thinking
        self._timeout = timeout
        self._call_id = uuid.uuid4()
        self._parts: list[str] = []
        self._response: httpx.Response | None = None
        self._pending_read: asyncio.Future[str | None] | None = None
        self._closed = False
        self._gen = self._run()
        self.result: LLMResult | None = None
        self.endpoint: Endpoint | None = None
        self.ttft_ms: float | None = None
        self.finish_reason: str | None = None

    @property
    def model_call_id(self) -> str:
        return str(self._call_id)

    @property
    def text(self) -> str:
        """Content received so far."""
        return "".join(self._parts)

    @property
    def closed(self) -> bool:
        return self._closed

    def __aiter__(self) -> LLMStream:
        return self

    async def __anext__(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        return await self._gen.__anext__()

    async def __aenter__(self) -> LLMStream:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        read = self._pending_read
        if read is not None and not read.done():
            # A reader is parked in another task: cancel just that read; the
            # generator sees the stream closed and finishes on its own.
            read.cancel()
        response = self._response
        if response is not None:
            # Drop the connection so the server stops generating right away.
            with contextlib.suppress(Exception):
                await response.aclose()
        try:
            await self._gen.aclose()
        except RuntimeError:
            pass  # generator is running in another task (see above)

    async def _run(self) -> Any:
        owner = self._owner
        context = self._context
        task = context.task.value
        route = owner._router.select(
            context.task, has_images=has_image_content(self._messages), explicit=self._explicit
        )
        candidates: list[Endpoint] = [route.endpoint] + ([route.fallback] if route.fallback else [])
        started = time.perf_counter()
        status = ModelCallStatus.OK
        error: BaseException | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        attempts = 0
        current: Endpoint = route.endpoint
        think = ThinkFilter()
        try:
            for index, endpoint in enumerate(candidates):
                current = endpoint
                self.endpoint = endpoint
                client = owner.endpoint_client(endpoint)
                body = owner.build_body(
                    endpoint,
                    self._messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    json_schema=self._json_schema,
                    thinking=self._thinking,
                    stream=True,
                )
                try:
                    async with client.stream(
                        "POST", _CHAT_PATH, json=body, timeout=self._timeout, task=task
                    ) as handle:
                        attempts += handle.attempts
                        self._response = handle.response
                        if self._closed:
                            return
                        decoder = SSEDecoder()
                        lines = handle.response.aiter_lines()
                        done = False
                        while not done:
                            # Each read is its own future so aclose() from any
                            # task can interrupt it, whatever the transport.
                            read = asyncio.ensure_future(anext(lines, None))
                            self._pending_read = read
                            try:
                                line = await read
                            except asyncio.CancelledError:
                                current_task = asyncio.current_task()
                                if self._closed and (current_task is None or current_task.cancelling() == 0):
                                    return  # closed from another task
                                raise
                            except Exception as exc:
                                if self._closed:
                                    return
                                raise client.stream_error(exc) from exc
                            finally:
                                self._pending_read = None
                            if line is None:
                                event = decoder.flush()
                                done = True
                                if event is None:
                                    break
                            else:
                                event = decoder.feed_line(line)
                                if event is None:
                                    continue
                            if event.data.strip() == "[DONE]":
                                break
                            for obj in parse_event_data(event.data):
                                err = obj.get("error")
                                if err:
                                    raise _inband_error(endpoint, err)
                                p_tok, c_tok = _usage(obj)
                                if p_tok is not None:
                                    prompt_tokens = p_tok
                                if c_tok is not None:
                                    completion_tokens = c_tok
                                for choice in obj.get("choices") or ():
                                    if not isinstance(choice, Mapping):
                                        continue
                                    if choice.get("finish_reason"):
                                        self.finish_reason = choice["finish_reason"]
                                    delta = choice.get("delta") or {}
                                    content = delta.get("content") if isinstance(delta, Mapping) else None
                                    if not content:
                                        continue  # role / reasoning / empty deltas
                                    visible = think.feed(str(content))
                                    if not visible:
                                        continue
                                    if self.ttft_ms is None:
                                        self.ttft_ms = (time.perf_counter() - started) * 1000
                                        metrics.LLM_TTFT_SECONDS.labels(endpoint=endpoint, task=task).observe(
                                            self.ttft_ms / 1000
                                        )
                                    self._parts.append(visible)
                                    yield visible
                        tail = think.flush()
                        if tail:
                            self._parts.append(tail)
                            yield tail
                except AIUnavailable as exc:
                    if index + 1 < len(candidates) and not self._parts and not self._closed:
                        log.warning(
                            "ai.llm.fallback",
                            task=task,
                            primary=endpoint,
                            fallback=candidates[index + 1],
                            error=str(exc),
                            stream=True,
                        )
                        continue
                    raise
                break
        except (asyncio.CancelledError, GeneratorExit):
            status = ModelCallStatus.CANCELLED
            raise
        except AIError as exc:
            status = call_status(exc)
            error = exc
            owner._count_failure(current, task, exc)
            raise
        finally:
            self._response = None
            if self._closed and status is ModelCallStatus.OK and self.finish_reason is None:
                status = ModelCallStatus.CANCELLED
            elapsed = time.perf_counter() - started
            metrics.LLM_REQUEST_SECONDS.labels(endpoint=current, task=task, status=status.value).observe(
                elapsed
            )
            _record_tokens(current, task, prompt_tokens, completion_tokens)
            text = self.text
            if status is ModelCallStatus.OK and attempts > 1:
                status = ModelCallStatus.RETRIED
            owner._record(
                call_id=self._call_id,
                context=context,
                endpoint=current,
                status=status,
                started=started,
                messages=self._messages,
                json_schema=self._json_schema,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                attempts=max(1, attempts),
                ttft_ms=self.ttft_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error,
                response_text=text,
            )
            self.result = LLMResult(
                text=text,
                endpoint=current,
                model=owner._models.get(current, current),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=elapsed * 1000,
                ttft_ms=self.ttft_ms,
                finish_reason=self.finish_reason,
                model_call_id=str(self._call_id),
            )
            self._closed = True


def _inband_error(endpoint: str, err: Any) -> AIError:
    if isinstance(err, Mapping):
        message = str(err.get("message") or err)[:300]
        code = err.get("code")
    else:
        message, code = str(err)[:300], None
    try:
        status = int(code) if code is not None else 500
    except (TypeError, ValueError):
        status = 500
    if 400 <= status < 500 and status not in (408, 429):
        return AIRejected(f"{endpoint}: stream error {status}: {message}", endpoint=endpoint)
    return AIUnavailable(f"{endpoint}: stream error {status}: {message}", endpoint=endpoint)
