"""Interfaces of the AI layer.

Domain code depends only on these protocols and result types, obtained via
``app.ai.registry.get_ai()``. Real clients (``app.ai.clients.*``) talk to the
local inference endpoints configured in ``app.core.config``; mock clients
(``app.ai.mock``) make every unit test runnable without a GPU.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from app.domain.enums import ModelTask

T = TypeVar("T", bound=BaseModel)

Endpoint = Literal["main", "vision"]
ChatMessage = dict[str, Any]  # OpenAI chat format; content may be str or a list of parts (text / image_url)


class AIError(Exception):
    """Base error. ``retryable`` tells callers whether a retry could help."""

    retryable: bool = False

    def __init__(self, message: str, *, endpoint: str | None = None, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        if retryable is not None:
            self.retryable = retryable


class AITimeout(AIError):
    retryable = True


class AIUnavailable(AIError):
    """Endpoint down, connection refused, 5xx, or circuit breaker open."""

    retryable = True


class AIRejected(AIError):
    """4xx: the request itself is wrong; retrying the same request will not help."""

    retryable = False


class AIOutputInvalid(AIError):
    """Structured output failed validation after repair and retries."""

    retryable = False


class EndpointDisabled(AIUnavailable):
    """The endpoint is switched off by configuration (``AI_DISABLED_ENDPOINTS``): callers take their
    fallback immediately, nothing is retried and the circuit breaker is not touched."""

    retryable = False


class TtsUnavailable(AIUnavailable):
    """Server-side speech synthesis is not possible right now (provider down,
    circuit open, or the ``browser`` provider is configured). The realtime
    runtime reacts by sending ``tts.fallback`` so the browser speaks instead."""


class PartialSkipped(AIError):
    """An interim (partial) transcription was skipped because the low-priority
    STT budget is saturated. Not a failure: drop this partial; the next one
    (or the final transcription) carries the audio anyway."""

    retryable = True


@dataclass(slots=True)
class CallContext:
    """Attribution for metrics and the model_calls ledger."""

    task: ModelTask
    session_id: str | None = None
    organization_id: str | None = None
    prompt_version: str | None = None


@dataclass(slots=True)
class LLMResult:
    text: str
    endpoint: Endpoint
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    finish_reason: str | None = None
    model_call_id: str | None = None


@dataclass(slots=True)
class StructuredResult[T: BaseModel]:
    value: T
    raw_text: str
    repaired: bool = False
    attempts: int = 1
    used_fallback: bool = False
    latency_ms: float = 0.0
    model_call_id: str | None = None


@dataclass(slots=True)
class RerankScore:
    index: int
    score: float


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_s: float | None = None
    no_speech_prob: float | None = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    latency_ms: float = 0.0
    endpoint: str | None = None


@dataclass(slots=True)
class OcrResult:
    text: str
    status: Literal["ok", "empty", "degenerate", "error"]
    raw: str = ""
    latency_ms: float = 0.0


@dataclass(slots=True)
class VisemeCue:
    t_ms: int
    viseme: str  # Oculus id: sil PP FF TH DD kk CH SS nn RR aa E I O U
    weight: float = 1.0


@dataclass(slots=True)
class TtsResult:
    pcm16: bytes  # mono little-endian PCM16
    sample_rate: int
    duration_ms: int
    visemes: list[VisemeCue] = field(default_factory=list)
    word_timings: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    latency_ms: float = 0.0


@runtime_checkable
class LLMClient(Protocol):
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
        timeout: float | None = None,  # noqa: ASYNC109 - per-call budget forwarded to the HTTP client, not an asyncio timeout
    ) -> LLMResult:
        """Non-streaming completion. ``endpoint`` None = router decides by task."""
        ...

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
        timeout: float | None = None,  # noqa: ASYNC109 - per-call budget forwarded to the HTTP client, not an asyncio timeout
    ) -> AsyncIterator[str]:
        """Yield content deltas as they arrive. Cancellation (aclose / task
        cancel) must close the HTTP stream immediately (barge-in)."""
        ...

    async def structured(
        self,
        schema: type[T],
        messages: Sequence[ChatMessage],
        *,
        context: CallContext,
        endpoint: Endpoint | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        thinking: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109 - per-call budget forwarded to the HTTP client, not an asyncio timeout
        max_attempts: int = 3,
        fallback: Callable[[], T] | None = None,
    ) -> StructuredResult[T]:
        """Guided JSON -> parse -> validate -> json_repair -> validate -> retry
        with the validation error fed back -> ``fallback()`` if provided,
        otherwise raise AIOutputInvalid."""
        ...


@runtime_checkable
class EmbeddingClient(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str], *, context: CallContext | None = None) -> list[list[float]]: ...

    async def embed_one(self, text: str, *, context: CallContext | None = None) -> list[float]: ...

    async def embed_documents(self, texts: Sequence[str], *, context: CallContext | None = None) -> list[list[float]]:
        """Documents are embedded raw (same as ``embed``)."""
        ...

    async def embed_query(
        self, text: str, instruction: str | None = None, *, context: CallContext | None = None
    ) -> list[float]:
        """Queries use the Qwen3-Embedding format ``Instruct: {instruction}\\nQuery: {text}``."""
        ...


@runtime_checkable
class RerankerClient(Protocol):
    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        instruction: str | None = None,
        top_k: int | None = None,
        context: CallContext | None = None,
    ) -> list[RerankScore]:
        """Scores in [0,1] (probability the document satisfies the query),
        sorted descending. Uses the Qwen3-Reranker prompt template."""
        ...


@runtime_checkable
class SttClient(Protocol):
    async def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str = "audio/wav",
        prompt: str | None = None,
        language: str | None = None,
        partial: bool = False,
        context: CallContext | None = None,
    ) -> TranscriptionResult: ...


@runtime_checkable
class OcrClient(Protocol):
    async def ocr_image(
        self, image: bytes, *, mime_type: str = "image/png", prompt: str | None = None, context: CallContext | None = None
    ) -> OcrResult: ...


@runtime_checkable
class TtsProvider(Protocol):
    name: str
    sample_rate: int

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> TtsResult: ...

    async def health(self) -> bool: ...


@runtime_checkable
class AIServices(Protocol):
    llm: LLMClient
    embeddings: EmbeddingClient
    reranker: RerankerClient
    stt: SttClient
    ocr: OcrClient
    tts: TtsProvider

    async def health(self) -> dict[str, dict[str, Any]]:
        """Per-endpoint {status: ok|degraded|down, latency_ms, detail}."""
        ...

    async def aclose(self) -> None: ...
