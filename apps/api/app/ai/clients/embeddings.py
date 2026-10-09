"""Embeddings client (Qwen3-Embedding behind the OpenAI-compatible
``/v1/embeddings`` endpoint).

* Documents are embedded raw; queries use the Qwen3-Embedding instruction
  format ``Instruct: {instruction}\\nQuery: {text}``.
* Vectors are L2-normalised float lists (cosine similarity == dot product).
* Requests are batched (``EMBEDDING_BATCH_SIZE``), duplicates inside one call
  are embedded once, and results are cached in Redis under
  ``cache:emb:{sha256(model, mode, text)}`` as little-endian float32 bytes
  for 7 days. The cache is best effort: a slow or failing Redis never fails
  an embedding call.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Sequence
from typing import Any, Literal, Protocol

import numpy as np

from app.ai.base import AIError, AIUnavailable, CallContext
from app.ai.http import EndpointClient, call_status, failure_reason, gather_or_cancel
from app.ai.ledger import LedgerSink, ModelCallRecord, NullLedger
from app.core import metrics
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus, ModelTask

log = get_logger(__name__)

DEFAULT_QUERY_INSTRUCTION = "Given a query, retrieve relevant passages that answer the query"
CACHE_PREFIX = "cache:emb:"
CACHE_TTL_SECONDS = 7 * 24 * 3600
# The endpoint rejects inputs over its context (4096 tokens): vLLM truncates
# server side with this parameter, the character cap bounds request size.
TRUNCATE_PROMPT_TOKENS = 4000
MAX_INPUT_CHARS = 24_000

Mode = Literal["document", "query"]


def format_query(text: str, instruction: str | None = None) -> str:
    return f"Instruct: {instruction or DEFAULT_QUERY_INSTRUCTION}\nQuery: {text}"


def cache_key(model: str, mode: str, text: str) -> str:
    digest = hashlib.sha256(f"{model}\x00{mode}\x00{text}".encode()).hexdigest()
    return CACHE_PREFIX + digest


def l2_normalize(vector: Sequence[float] | np.ndarray) -> list[float]:
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0 and np.isfinite(norm):
        arr = arr / norm
    return arr.astype(np.float32).tolist()


class AsyncKV(Protocol):
    """The subset of ``redis.asyncio.Redis`` the cache uses."""

    async def mget(self, keys: Sequence[str]) -> list[Any]: ...

    def pipeline(self, transaction: bool = ...) -> Any: ...


class EmbeddingCache:
    def __init__(
        self,
        redis: AsyncKV,
        *,
        dimensions: int,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        op_timeout: float = 0.5,
    ):
        self._redis = redis
        self._dimensions = dimensions
        self._ttl = ttl_seconds
        self._op_timeout = op_timeout
        self._last_error_log = 0.0

    async def get_many(self, keys: Sequence[str]) -> list[list[float] | None]:
        if not keys:
            return []
        try:
            raw = await asyncio.wait_for(self._redis.mget(list(keys)), self._op_timeout)
        except Exception as exc:  # noqa: BLE001 - cache is best effort
            self._log_error("get", exc)
            return [None] * len(keys)
        result: list[list[float] | None] = []
        for value in raw:
            if isinstance(value, bytes | bytearray) and len(value) == self._dimensions * 4:
                result.append(np.frombuffer(value, dtype="<f4").astype(np.float32).tolist())
            else:
                result.append(None)
        return result

    async def set_many(self, items: Sequence[tuple[str, Sequence[float]]]) -> None:
        if not items:
            return
        try:
            pipe = self._redis.pipeline(transaction=False)
            for key, vector in items:
                pipe.set(key, np.asarray(vector, dtype="<f4").tobytes(), ex=self._ttl)
            await asyncio.wait_for(pipe.execute(), self._op_timeout * 2)
        except Exception as exc:  # noqa: BLE001 - cache is best effort
            self._log_error("set", exc)

    def _log_error(self, op: str, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._last_error_log > 60:
            self._last_error_log = now
            log.warning("ai.embedding_cache.error", op=op, error=f"{type(exc).__name__}: {exc}"[:200])


class OpenAIEmbeddingsClient:
    """``EmbeddingClient`` with batching, normalisation and a Redis cache."""

    def __init__(
        self,
        http: EndpointClient,
        *,
        model: str,
        dimensions: int,
        batch_size: int = 32,
        cache: EmbeddingCache | None = None,
        ledger: LedgerSink | None = None,
        truncate_prompt_tokens: int | None = TRUNCATE_PROMPT_TOKENS,
    ) -> None:
        self._http = http
        self.model = model
        self.dimensions = dimensions
        self.batch_size = max(1, batch_size)
        self._cache = cache
        self._ledger: LedgerSink = ledger or NullLedger()
        self._truncate = truncate_prompt_tokens

    # ------------------------------------------------------------ protocol
    async def embed(self, texts: Sequence[str], *, context: CallContext | None = None) -> list[list[float]]:
        return await self._embed([_prepare(t) for t in texts], mode="document", context=context)

    async def embed_one(self, text: str, *, context: CallContext | None = None) -> list[float]:
        return (await self.embed([text], context=context))[0]

    async def embed_documents(
        self, texts: Sequence[str], *, context: CallContext | None = None
    ) -> list[list[float]]:
        return await self.embed(texts, context=context)

    async def embed_query(
        self, text: str, instruction: str | None = None, *, context: CallContext | None = None
    ) -> list[float]:
        formatted = format_query(_prepare(text), instruction)
        return (await self._embed([formatted], mode="query", context=context))[0]

    # ------------------------------------------------------------ internal
    async def _embed(
        self, inputs: list[str], *, mode: Mode, context: CallContext | None
    ) -> list[list[float]]:
        if not inputs:
            return []
        started = time.perf_counter()
        unique = list(dict.fromkeys(inputs))
        keys = [cache_key(self.model, mode, text) for text in unique]
        vectors: dict[str, list[float]] = {}
        if self._cache is not None:
            for text, cached in zip(unique, await self._cache.get_many(keys), strict=True):
                if cached is not None:
                    vectors[text] = cached
        missing = [text for text in unique if text not in vectors]
        attempts = 0
        prompt_tokens = 0
        status = ModelCallStatus.OK
        error: BaseException | None = None
        try:
            if missing:
                batches = [missing[i : i + self.batch_size] for i in range(0, len(missing), self.batch_size)]
                results = await gather_or_cancel(*(self._request(batch, context) for batch in batches))
                fresh: list[tuple[str, list[float]]] = []
                for batch, (batch_vectors, batch_attempts, batch_tokens) in zip(
                    batches, results, strict=True
                ):
                    attempts += batch_attempts
                    prompt_tokens += batch_tokens
                    for text, vector in zip(batch, batch_vectors, strict=True):
                        vectors[text] = vector
                        fresh.append((cache_key(self.model, mode, text), vector))
                if self._cache is not None:
                    await self._cache.set_many(fresh)
        except asyncio.CancelledError:
            status = ModelCallStatus.CANCELLED
            raise
        except AIError as exc:
            status, error = call_status(exc), exc
            metrics.MODEL_CALL_FAILURES.labels(
                endpoint="embedding", task=_task(context), reason=failure_reason(exc)
            ).inc()
            raise
        finally:
            if missing:  # cache-only hits are not model calls
                self._record(
                    context, status, started, max(1, attempts), prompt_tokens or None, error, len(missing)
                )
        return [vectors[text] for text in inputs]

    async def _request(
        self, batch: list[str], context: CallContext | None
    ) -> tuple[list[list[float]], int, int]:
        body: dict[str, Any] = {"model": self.model, "input": batch, "encoding_format": "float"}
        if self._truncate:
            body["truncate_prompt_tokens"] = self._truncate
        started = time.perf_counter()
        result = await self._http.request("POST", "/embeddings", json=body, task=_task(context))
        metrics.EMBEDDING_SECONDS.observe(time.perf_counter() - started)
        data = result.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != len(batch):
            raise AIUnavailable(
                f"embedding: expected {len(batch)} vectors, got {len(items) if isinstance(items, list) else 'none'}",
                endpoint="embedding",
            )
        ordered: list[list[float] | None] = [None] * len(batch)
        for position, item in enumerate(items):
            index = item.get("index", position) if isinstance(item, dict) else position
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not isinstance(index, int) or not 0 <= index < len(batch):
                raise AIUnavailable("embedding: malformed vector in response", endpoint="embedding")
            if len(vector) != self.dimensions:
                raise AIUnavailable(
                    f"embedding: expected {self.dimensions} dimensions, got {len(vector)}",
                    endpoint="embedding",
                )
            ordered[index] = l2_normalize(vector)
        if any(v is None for v in ordered):
            raise AIUnavailable("embedding: response is missing vectors", endpoint="embedding")
        usage = data.get("usage") if isinstance(data, dict) else None
        tokens = usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0
        return [v for v in ordered if v is not None], result.attempts, int(tokens or 0)

    def _record(
        self,
        context: CallContext | None,
        status: ModelCallStatus,
        started: float,
        attempts: int,
        prompt_tokens: int | None,
        error: BaseException | None,
        count: int,
    ) -> None:
        try:
            self._ledger.record(
                ModelCallRecord(
                    task=_task(context),
                    endpoint="embedding",
                    model=self.model,
                    status=status.value,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=prompt_tokens,
                    error=None if error is None else f"{type(error).__name__}: {error}",
                    response_payload={"vectors": count} if self._ledger.log_payloads else None,
                    session_id=context.session_id if context else None,
                    organization_id=context.organization_id if context else None,
                    prompt_version=context.prompt_version if context else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai.ledger.record_failed", error=str(exc))


def _prepare(text: str) -> str:
    text = text if isinstance(text, str) else str(text)
    if not text.strip():
        return " "  # the endpoint returns a valid vector for whitespace; never send nothing
    return text[:MAX_INPUT_CHARS]


def _task(context: CallContext | None) -> str:
    return (context.task if context else ModelTask.EMBEDDING).value
