"""Non-blocking writer for the ``model_calls`` ledger.

Model clients call :meth:`ModelCallLedger.record` from the hot path; it never
awaits. Records go into a bounded ``asyncio.Queue`` and a background task
batch-inserts them about once per second through
``app.db.session.session_scope``. When the queue is full the record is
dropped and counted (the voice loop must never wait on the database).
Pending records are flushed on shutdown.

Request/response payloads are only kept when ``MODEL_CALL_LOG_PAYLOADS`` is
true, and then truncated (images are never stored).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core import metrics
from app.core.logging import get_logger

log = get_logger(__name__)

_DROPPED = getattr(metrics, "MODEL_CALL_LEDGER_DROPPED", None)  # optional counter (requested)

MAX_ERROR_CHARS = 2000
MAX_MESSAGE_CHARS = 4000
MAX_MESSAGES = 30
MAX_RESPONSE_CHARS = 8000

Writer = Callable[[list[dict[str, Any]]], Awaitable[None]]


def _uuid_or_none(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


@dataclass(slots=True)
class ModelCallRecord:
    task: str
    endpoint: str  # main | vision | embedding | reranker | whisper | ocr | tts
    model: str
    status: str  # ModelCallStatus value
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    attempts: int = 1
    latency_ms: float | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    repaired: bool = False
    used_fallback: bool = False
    prompt_version: str | None = None
    request_hash: str | None = None
    error: str | None = None
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None
    session_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "organization_id": _uuid_or_none(self.organization_id),
            "session_id": _uuid_or_none(self.session_id),
            "task": str(self.task)[:48],
            "endpoint": str(self.endpoint)[:32],
            "model": str(self.model)[:200],
            "status": str(self.status)[:24],
            "attempts": max(1, int(self.attempts)),
            "latency_ms": None if self.latency_ms is None else round(float(self.latency_ms), 2),
            "ttft_ms": None if self.ttft_ms is None else round(float(self.ttft_ms), 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "repaired": bool(self.repaired),
            "used_fallback": bool(self.used_fallback),
            "prompt_version": None if self.prompt_version is None else str(self.prompt_version)[:40],
            "request_hash": self.request_hash,
            "error": None if self.error is None else str(self.error)[:MAX_ERROR_CHARS],
            "request_payload": self.request_payload,
            "response_payload": self.response_payload,
        }


class LedgerSink(Protocol):
    log_payloads: bool

    def record(self, record: ModelCallRecord) -> None: ...


class NullLedger:
    """Discards records (unit tests, scripts)."""

    log_payloads = False

    def record(self, record: ModelCallRecord) -> None:
        return None


class MemoryLedger:
    """Keeps records in a list (tests)."""

    def __init__(self, *, log_payloads: bool = False) -> None:
        self.log_payloads = log_payloads
        self.records: list[ModelCallRecord] = []

    def record(self, record: ModelCallRecord) -> None:
        self.records.append(record)


async def _db_writer(rows: list[dict[str, Any]]) -> None:
    from sqlalchemy import insert

    from app.db.models.system import ModelCall
    from app.db.session import session_scope

    async with session_scope() as session:
        await session.execute(insert(ModelCall), rows)


class ModelCallLedger:
    def __init__(
        self,
        *,
        writer: Writer | None = None,
        max_queue: int = 10_000,
        batch_size: int = 500,
        flush_interval: float = 1.0,
        log_payloads: bool = False,
    ) -> None:
        self.log_payloads = log_payloads
        self._writer: Writer = writer or _db_writer
        self._queue: asyncio.Queue[ModelCallRecord] = asyncio.Queue(maxsize=max_queue)
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._closing = False
        self.dropped = 0
        self.written = 0
        self.failed = 0

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------- hot path
    def record(self, record: ModelCallRecord) -> None:
        if self._closing:
            self._drop(record, "closed")
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._drop(record, "queue_full")
            return
        self._ensure_task()

    def _drop(self, record: ModelCallRecord, reason: str) -> None:
        self.dropped += 1
        if _DROPPED is not None:
            _DROPPED.inc()
        else:
            metrics.ERRORS.labels(component="ai_ledger").inc()
        if self.dropped == 1 or self.dropped % 1000 == 0:
            log.warning("ai.ledger.dropped", reason=reason, dropped_total=self.dropped, task=record.task)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._closing = False
        self._ensure_task()

    def _ensure_task(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop yet: records wait in the queue until start()
        self._wake = asyncio.Event()
        self._lock = self._lock or asyncio.Lock()
        self._task = loop.create_task(self._run(), name="model-call-ledger")

    async def _run(self) -> None:
        assert self._wake is not None
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._flush_interval)
            self._wake.clear()
            await self.flush()
            if self._closing:
                return

    async def flush(self) -> None:
        """Write everything queued so far."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while not self._queue.empty():
                batch: list[ModelCallRecord] = []
                while len(batch) < self._batch_size and not self._queue.empty():
                    batch.append(self._queue.get_nowait())
                rows: list[dict[str, Any]] = []
                for item in batch:
                    try:
                        rows.append(item.to_row())
                    except Exception as exc:  # noqa: BLE001 - one malformed record must not block the rest
                        self.failed += 1
                        log.warning(
                            "ai.ledger.bad_record",
                            task=str(item.task)[:48],
                            error=f"{type(exc).__name__}: {exc}"[:200],
                        )
                if not rows:
                    continue
                try:
                    await self._writer(rows)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the ledger must never take a caller down
                    self.failed += len(rows)
                    metrics.ERRORS.labels(component="ai_ledger").inc()
                    log.warning(
                        "ai.ledger.write_failed", rows=len(rows), error=f"{type(exc).__name__}: {exc}"[:300]
                    )
                else:
                    self.written += len(rows)

    async def aclose(self, grace: float = 5.0) -> None:
        self._closing = True
        task = self._task
        if task is not None and not task.done():
            if self._wake is not None:
                self._wake.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), grace)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if not self._queue.empty():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.flush(), grace)
        self._task = None


# ------------------------------------------------------------------ payloads


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [truncated {len(text) - limit} chars]"


def messages_payload(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy of chat messages safe to store: images removed, text truncated."""
    result: list[dict[str, Any]] = []
    for message in list(messages)[-MAX_MESSAGES:]:
        content = message.get("content")
        if isinstance(content, str):
            stored: Any = _clip(content, MAX_MESSAGE_CHARS)
        elif isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                if isinstance(part, Mapping) and part.get("type") == "text":
                    parts.append(
                        {"type": "text", "text": _clip(str(part.get("text", "")), MAX_MESSAGE_CHARS)}
                    )
                elif isinstance(part, Mapping):
                    parts.append({"type": str(part.get("type", "unknown")), "omitted": True})
            stored = parts
        else:
            stored = None
        result.append({"role": str(message.get("role", "")), "content": stored})
    return result


def text_payload(text: str | None) -> dict[str, Any]:
    return {"text": _clip(text or "", MAX_RESPONSE_CHARS)}
