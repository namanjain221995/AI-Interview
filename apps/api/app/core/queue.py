"""Background jobs (CONTRACTS.md section 5).

Durability model
    ``background_jobs`` (Postgres) is the source of truth; Redis Streams only
    dispatch. ``enqueue`` inserts the row (deduplicated on ``dedupe_key`` while
    the job is unfinished) and then ``XADD``s ``{job_id, type}`` to
    ``jobs:{type}``; delayed jobs and retries wait in the ``jobs:delayed`` ZSET.
    Pass ``db=`` to enqueue inside your own transaction: the row commits with
    your data and the stream message is only sent after that commit.

Delivery
    One consumer group ``workers`` per stream; a single ``XREADGROUP`` call
    reads every stream that has free capacity (per-type concurrency). A worker
    claims a job by atomically moving its row ``queued -> running``, which also
    turns duplicate messages into no-ops. Running jobs heartbeat (``XCLAIM
    JUSTID`` + ``updated_at``), so a crashed worker's messages are reclaimed
    with ``XAUTOCLAIM`` once idle for ``visibility_timeout`` seconds.

Failure handling
    Exceptions retry with exponential backoff (``2 s * 2^(attempt-1)``, jitter,
    cap 5 min) up to ``max_attempts``; then the row becomes ``dead`` and the job
    is copied to the ``jobs:dead`` stream. ``PermanentJobError`` (or any
    exception with ``retryable = False``, e.g. ``AIOutputInvalid``) skips the
    retries (status ``failed``, also dead-lettered). On start the worker
    re-dispatches rows left ``queued``/``running`` and periodically reconciles
    rows whose stream message was lost. SIGTERM stops reading, waits for
    in-flight jobs (grace period) and re-queues whatever had to be cancelled.

Handlers (idempotent!) live in ``app/workers/<domain>.py``::

    @job_handler("resume.process", concurrency=4)
    async def process_resume(payload: dict[str, Any], ctx: JobContext) -> dict | None: ...

``handler(payload)``, ``handler(payload, ctx)``, ``handler(ctx)`` (parameter
annotated ``JobContext``) and keyword-only ``handler(*, resume_id, ...)`` are
all accepted.

Session bus
    ``publish_session_event(session_id, {"type": "evaluation.ready", ...})`` and
    ``subscribe_session_events(session_id)`` use pub/sub channel
    ``session:{id}:bus``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import random
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lifecycle import run_after_commit
from app.core.logging import bind_context, get_logger
from app.core.metrics import ERRORS, JOB_SECONDS, JOBS, JOBS_IN_FLIGHT, QUEUE_DEPTH
from app.db.models import BackgroundJob
from app.db.session import session_scope
from app.domain.enums import JobState

log = get_logger(__name__)

STREAM_PREFIX = "jobs:"
DEAD_STREAM = "jobs:dead"
DELAYED_ZSET = "jobs:delayed"
GROUP = "workers"
RECONCILE_LOCK = "jobs:reconcile:lock"
JOB_TYPES_SET = "jobs:types"  # every job type a worker has consumed (for dashboards)
_RESERVED_TYPES = frozenset({"dead", "delayed", "reconcile", "types", "schedule"})
_JOB_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_MAX_ERROR_CHARS = 2000
_DEAD_STREAM_MAXLEN = 10_000
DEFAULT_MAX_ATTEMPTS = 3


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot help (bad payload, missing row...)."""

    retryable = False


# ------------------------------------------------------------------ helpers


def stream_key(job_type: str) -> str:
    return f"{STREAM_PREFIX}{job_type}"


def validate_job_type(job_type: str) -> str:
    if not isinstance(job_type, str) or len(job_type) > 64 or not _JOB_TYPE.match(job_type):
        raise ValueError(f"Invalid job type {job_type!r} (lowercase dotted identifier, <= 64 chars)")
    if job_type in _RESERVED_TYPES:
        raise ValueError(f"Job type {job_type!r} is reserved")
    return job_type


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):  # enums
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _normalize_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise TypeError("Job payload must be a mapping")
    normalized = json.loads(json.dumps(dict(payload), default=_json_default))
    assert isinstance(normalized, dict)
    return normalized


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= _MAX_ERROR_CHARS else text[:_MAX_ERROR_CHARS] + "...[truncated]"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, PermanentJobError):
        return False
    return getattr(exc, "retryable", True) is not False


def _now() -> datetime:
    return datetime.now(UTC)


def _delayed_member(job_type: str, job_id: str, missing: int = 0) -> str:
    return json.dumps({"job_id": job_id, "missing": missing, "type": job_type}, sort_keys=True, separators=(",", ":"))


# ----------------------------------------------------------------- registry


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: str
    job_type: str
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    message_id: str | None = None
    enqueued_at: datetime | None = None

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts


CallStyle = Literal["payload", "payload_ctx", "ctx", "kwargs"]
ExhaustedHook = Callable[["JobContext", BaseException], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class JobHandlerSpec:
    job_type: str
    fn: Callable[..., Awaitable[Any]]
    concurrency: int
    max_attempts: int | None
    timeout_seconds: float | None
    call_style: CallStyle
    accepts_var_kwargs: bool
    kwarg_names: frozenset[str]
    # Runs once when the job will never run again (permanent failure, attempts exhausted, or a
    # worker killed through every attempt): the owner leaves its domain state decidable (S2).
    on_exhausted: ExhaustedHook | None = None


_HANDLERS: dict[str, JobHandlerSpec] = {}


def _is_context_annotation(annotation: Any) -> bool:
    if annotation is JobContext:
        return True
    if isinstance(annotation, str):  # postponed annotations: "JobContext", "queue.JobContext", ...
        return annotation.strip().rsplit(".", 1)[-1] == "JobContext"
    return False


def _analyze(fn: Callable[..., Any]) -> tuple[CallStyle, bool, frozenset[str]]:
    sig = inspect.signature(fn)
    positional = [
        p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    keyword_only = [p for p in sig.parameters.values() if p.kind is p.KEYWORD_ONLY]
    var_kw = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
    if len(positional) >= 2:
        return "payload_ctx", var_kw, frozenset()
    if len(positional) == 1:
        return ("ctx" if _is_context_annotation(positional[0].annotation) else "payload"), var_kw, frozenset()
    if keyword_only or var_kw:
        return "kwargs", var_kw, frozenset(p.name for p in keyword_only)
    raise TypeError(f"Job handler {fn.__qualname__} must accept the payload")


def job_handler(
    job_type: str,
    *,
    concurrency: int = 1,
    max_attempts: int | None = None,
    timeout_seconds: float | None = None,
    on_exhausted: ExhaustedHook | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Register an async handler for ``job_type`` (see module docstring)."""
    validate_job_type(job_type)
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Job handler {fn.__qualname__} must be an async function")
        existing = _HANDLERS.get(job_type)
        if existing is not None and (existing.fn.__module__, existing.fn.__qualname__) != (fn.__module__, fn.__qualname__):
            raise ValueError(
                f"Job type {job_type!r} already handled by {existing.fn.__module__}.{existing.fn.__qualname__}"
            )
        style, var_kw, names = _analyze(fn)
        _HANDLERS[job_type] = JobHandlerSpec(
            job_type=job_type,
            fn=fn,
            concurrency=concurrency,
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
            call_style=style,
            accepts_var_kwargs=var_kw,
            kwarg_names=names,
            on_exhausted=on_exhausted,
        )
        return fn

    return decorator


def registered_job_types() -> list[str]:
    return sorted(_HANDLERS)


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    job_type: str
    interval_seconds: int
    payload: dict[str, Any]


_PERIODIC: dict[str, PeriodicJob] = {}


def register_periodic_job(job_type: str, interval_seconds: int, payload: Mapping[str, Any] | None = None) -> None:
    """Ask the worker to enqueue ``job_type`` every ``interval_seconds`` (once
    per interval across all workers; only while a handler is registered)."""
    validate_job_type(job_type)
    if interval_seconds < 60:
        raise ValueError("interval_seconds must be >= 60")
    _PERIODIC[job_type] = PeriodicJob(job_type, int(interval_seconds), _normalize_payload(payload))


def periodic_jobs() -> list[PeriodicJob]:
    return list(_PERIODIC.values())


async def enqueue_due_periodic_jobs(*, redis: Redis | None = None, now: float | None = None) -> list[str]:
    """Enqueue every periodic job whose current interval bucket has not run yet."""
    client = _redis(redis)
    current = now if now is not None else time.time()
    enqueued: list[str] = []
    for job in periodic_jobs():
        if job.job_type not in _HANDLERS:
            continue
        bucket = int(current // job.interval_seconds)
        lock = f"jobs:schedule:{job.job_type}:{bucket}"
        if not await client.set(lock, "1", nx=True, ex=job.interval_seconds * 2):
            continue
        enqueued.append(
            await enqueue(job.job_type, job.payload, dedupe_key=f"{job.job_type}:{bucket}", redis=client)
        )
    return enqueued


def get_handler(job_type: str) -> JobHandlerSpec | None:
    return _HANDLERS.get(job_type)


def unregister_handler(job_type: str) -> None:
    _HANDLERS.pop(job_type, None)


async def _invoke(spec: JobHandlerSpec, ctx: JobContext) -> Any:
    if spec.call_style == "payload_ctx":
        return await spec.fn(ctx.payload, ctx)
    if spec.call_style == "payload":
        return await spec.fn(ctx.payload)
    if spec.call_style == "ctx":
        return await spec.fn(ctx)
    kwargs = ctx.payload if spec.accepts_var_kwargs else {k: v for k, v in ctx.payload.items() if k in spec.kwarg_names}
    return await spec.fn(**kwargs)


# ------------------------------------------------------------------ enqueue


def _redis(redis: Redis | None) -> Redis:
    if redis is not None:
        return redis
    from app.core.redis import get_redis

    return get_redis()


async def _insert_job(
    db: AsyncSession,
    *,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str | None,
    max_attempts: int,
    run_after: datetime | None,
) -> tuple[str, bool]:
    for _ in range(3):
        job_id = uuid.uuid4()
        stmt = pg_insert(BackgroundJob).values(
            id=job_id,
            type=job_type,
            status=JobState.QUEUED.value,
            dedupe_key=dedupe_key,
            payload=payload,
            attempts=0,
            max_attempts=max_attempts,
            run_after=run_after,
        )
        if dedupe_key is not None:
            stmt = stmt.on_conflict_do_nothing(index_elements=[BackgroundJob.dedupe_key])
        inserted = (await db.execute(stmt.returning(BackgroundJob.id))).scalar_one_or_none()
        if inserted is not None:
            return str(inserted), True
        existing = (
            await db.execute(
                select(BackgroundJob.id, BackgroundJob.status).where(BackgroundJob.dedupe_key == dedupe_key)
            )
        ).first()
        if existing is None:
            continue  # the holder finished and released the key meanwhile
        if existing.status in (JobState.QUEUED.value, JobState.RUNNING.value):
            return str(existing.id), False
        await db.execute(  # finished but still holding the key: release it
            update(BackgroundJob).where(BackgroundJob.id == existing.id).values(dedupe_key=None)
        )
    raise RuntimeError(f"Could not enqueue {job_type!r} with dedupe key {dedupe_key!r}")


async def dispatch(job_id: str, job_type: str, run_after: datetime | None = None, *, redis: Redis | None = None) -> None:
    """Send the stream message (or schedule it). Failures leave the row queued;
    the worker's reconciliation re-dispatches it."""
    client = _redis(redis)
    try:
        if run_after is not None and run_after.timestamp() > time.time() + 0.05:
            await client.zadd(DELAYED_ZSET, {_delayed_member(job_type, job_id): run_after.timestamp()})
        else:
            await client.xadd(
                stream_key(job_type), {"job_id": job_id, "type": job_type, "enqueued_at": _now().isoformat()}
            )
    except (RedisError, OSError) as exc:
        ERRORS.labels(component="queue").inc()
        log.warning("queue.dispatch_failed", job_id=job_id, job_type=job_type, error=type(exc).__name__)


async def enqueue(
    job_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    dedupe_key: str | None = None,
    delay_seconds: float = 0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    db: AsyncSession | None = None,
    redis: Redis | None = None,
) -> str:
    """Create a job and dispatch it; returns the job id.

    With ``dedupe_key``, an unfinished job holding the same key is returned
    instead of creating a new one. With ``db``, the row is added to that
    session's transaction and dispatched after it commits (nothing is sent if
    it rolls back).
    """
    validate_job_type(job_type)
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if dedupe_key is not None and (not dedupe_key or len(dedupe_key) > 200):
        raise ValueError("dedupe_key must be 1-200 characters")
    body = _normalize_payload(payload)
    run_after = _now() + timedelta(seconds=delay_seconds) if delay_seconds and delay_seconds > 0 else None
    if db is None:
        async with session_scope() as session:
            job_id, created = await _insert_job(
                session, job_type=job_type, payload=body, dedupe_key=dedupe_key, max_attempts=max_attempts, run_after=run_after
            )
        if created:
            await dispatch(job_id, job_type, run_after, redis=redis)
    else:
        job_id, created = await _insert_job(
            db, job_type=job_type, payload=body, dedupe_key=dedupe_key, max_attempts=max_attempts, run_after=run_after
        )
        if created:

            async def _after_commit() -> None:
                await dispatch(job_id, job_type, run_after, redis=redis)

            run_after_commit(db, _after_commit)
    log.info("queue.enqueued", job_id=job_id, job_type=job_type, created=created, delay_seconds=delay_seconds or 0)
    return job_id


async def get_job(job_id: str | uuid.UUID) -> BackgroundJob | None:
    async with session_scope() as session:
        return await session.get(BackgroundJob, uuid.UUID(str(job_id)))


async def wait_for_job(job_id: str | uuid.UUID, *, timeout: float = 10.0, poll_interval: float = 0.05) -> BackgroundJob:  # noqa: ASYNC109
    """Poll until the job reaches a terminal state (tests, admin tooling)."""
    terminal = {JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.DEAD.value}
    deadline = time.monotonic() + timeout
    while True:
        job = await get_job(job_id)
        if job is not None and job.status in terminal:
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Job {job_id} did not finish within {timeout}s (status={getattr(job, 'status', None)})")
        await asyncio.sleep(poll_interval)


# ------------------------------------------------------------------- worker


@dataclass(slots=True)
class _ClaimedJob:
    job_id: str
    job_type: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    created_at: datetime | None


class Worker:
    """Consumes job streams for the registered handlers (``python -m app.worker``)."""

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        consumer_name: str | None = None,
        job_types: Iterable[str] | None = None,
        block_ms: int = 1000,
        visibility_timeout: float = 60.0,
        backoff_base: float = 2.0,
        backoff_cap: float = 300.0,
        shutdown_grace: float = 30.0,
        delayed_poll_interval: float = 0.5,
        claim_interval: float = 15.0,
        reconcile_interval: float = 60.0,
        depth_interval: float = 15.0,
        missing_row_retries: int = 5,
    ) -> None:
        from app.core.config import get_settings

        self.redis = _redis(redis)
        self.consumer = consumer_name or get_settings().instance_id
        self._types_filter = set(job_types) if job_types is not None else None
        self.block_ms = block_ms
        self.visibility_timeout = visibility_timeout
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.shutdown_grace = shutdown_grace
        self.delayed_poll_interval = delayed_poll_interval
        self.claim_interval = claim_interval
        self.reconcile_interval = reconcile_interval
        self.depth_interval = depth_interval
        self.missing_row_retries = missing_row_retries
        self._inflight: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._service_tasks: list[asyncio.Task[None]] = []
        self._slot_freed = asyncio.Event()
        self._stop = asyncio.Event()
        self._stopping = False
        self._groups_ready: set[str] = set()
        self.last_loop_at = time.monotonic()
        self.processed = 0

    # -------------------------------------------------------------- state

    @property
    def job_types(self) -> list[str]:
        types = registered_job_types()
        if self._types_filter is not None:
            types = [t for t in types if t in self._types_filter]
        return types

    def _free(self, job_type: str) -> int:
        spec = _HANDLERS.get(job_type)
        if spec is None:
            return 0
        return spec.concurrency - self._inflight.get(job_type, 0)

    @property
    def inflight(self) -> int:
        return sum(self._inflight.values())

    def healthy(self, max_loop_age: float = 30.0) -> bool:
        return not self._stopping and (time.monotonic() - self.last_loop_at) < max_loop_age

    async def _ensure_group(self, job_type: str) -> None:
        key = stream_key(job_type)
        try:
            await self.redis.xgroup_create(key, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups_ready.add(job_type)

    async def ensure_groups(self) -> None:
        for job_type in self.job_types:
            await self._ensure_group(job_type)
        if self.job_types:
            await self.redis.sadd(JOB_TYPES_SET, *self.job_types)

    # ---------------------------------------------------------- lifecycle

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Run until ``stop`` is set (or :meth:`stop` is called), then shut down gracefully."""
        external_stop = stop or self._stop
        await self.ensure_groups()
        await self.recover()
        self._service_tasks = [
            asyncio.create_task(self._periodic(self.move_due_delayed, self.delayed_poll_interval), name="queue:delayed"),
            asyncio.create_task(self._periodic(self.reclaim_idle, self.claim_interval), name="queue:reclaim"),
            asyncio.create_task(self._periodic(self.reconcile, self.reconcile_interval), name="queue:reconcile"),
            asyncio.create_task(self._periodic(self.refresh_queue_depths, self.depth_interval), name="queue:depth"),
        ]
        log.info("worker.started", consumer=self.consumer, job_types=self.job_types)
        try:
            await self._read_loop(external_stop)
        finally:
            await self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    async def _shutdown(self) -> None:
        self._stopping = True
        for task in self._service_tasks:
            task.cancel()
        for task in self._service_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._service_tasks = []
        if self._tasks:
            log.info("worker.draining", inflight=len(self._tasks), grace_seconds=self.shutdown_grace)
            _, pending = await asyncio.wait(set(self._tasks), timeout=self.shutdown_grace)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        log.info("worker.stopped", consumer=self.consumer, processed=self.processed)

    async def _periodic(self, fn: Callable[[], Awaitable[Any]], interval: float) -> None:
        while not self._stopping:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ERRORS.labels(component="worker").inc()
                log.warning("worker.periodic_failed", task=getattr(fn, "__name__", "?"), error=_error_text(exc))
            await asyncio.sleep(interval)

    async def _wait_any(self, stop: asyncio.Event, timeout: float) -> None:  # noqa: ASYNC109
        self._slot_freed.clear()
        waiters = [asyncio.ensure_future(self._slot_freed.wait()), asyncio.ensure_future(stop.wait())]
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

    async def _read_loop(self, stop: asyncio.Event) -> None:
        backoff = 0.5
        while not stop.is_set() and not self._stop.is_set():
            self.last_loop_at = time.monotonic()
            types = [t for t in self.job_types if self._free(t) > 0]
            if not types:
                await self._wait_any(stop, timeout=1.0)
                continue
            count = max(1, min(self._free(t) for t in types))
            try:
                for job_type in types:
                    if job_type not in self._groups_ready:
                        await self._ensure_group(job_type)
                response: Any = await self.redis.xreadgroup(
                    GROUP, self.consumer, {stream_key(t): ">" for t in types}, count=count, block=self.block_ms
                )
                backoff = 0.5
            except (RedisError, OSError) as exc:
                if isinstance(exc, ResponseError) and "NOGROUP" in str(exc):
                    self._groups_ready.clear()  # stream or group deleted (e.g. FLUSHDB): recreate
                    continue
                ERRORS.labels(component="worker").inc()
                log.warning("worker.read_failed", error=_error_text(exc), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            for stream, messages in response or []:
                job_type = stream[len(STREAM_PREFIX):]
                for message_id, fields in messages:
                    self._start(job_type, message_id, fields)

    def _start(self, job_type: str, message_id: str, fields: Mapping[str, str]) -> None:
        self._inflight[job_type] = self._inflight.get(job_type, 0) + 1
        JOBS_IN_FLIGHT.labels(type=job_type).inc()
        task = asyncio.create_task(self._process(job_type, message_id, dict(fields)), name=f"job:{job_type}")
        self._tasks.add(task)

        def _done(t: asyncio.Task[None]) -> None:
            self._tasks.discard(t)
            self._inflight[job_type] = max(0, self._inflight.get(job_type, 1) - 1)
            JOBS_IN_FLIGHT.labels(type=job_type).dec()
            self._slot_freed.set()

        task.add_done_callback(_done)

    # ------------------------------------------------------ message flow

    async def _ack(self, job_type: str, message_id: str) -> None:
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.xack(stream_key(job_type), GROUP, message_id)
                pipe.xdel(stream_key(job_type), message_id)
                await pipe.execute()
        except (RedisError, OSError) as exc:
            log.warning("worker.ack_failed", job_type=job_type, error=type(exc).__name__)

    async def _claim(self, job_id: str) -> _ClaimedJob | None:
        stale_before = func.now() - timedelta(seconds=self.visibility_timeout)
        async with session_scope() as session:
            row = (
                await session.execute(
                    update(BackgroundJob)
                    .where(
                        BackgroundJob.id == uuid.UUID(job_id),
                        (
                            (BackgroundJob.status == JobState.QUEUED.value)
                            & (
                                BackgroundJob.run_after.is_(None)
                                | (BackgroundJob.run_after <= func.now() + timedelta(seconds=1))
                            )
                        )
                        | ((BackgroundJob.status == JobState.RUNNING.value) & (BackgroundJob.updated_at < stale_before)),
                    )
                    .values(
                        status=JobState.RUNNING.value,
                        attempts=BackgroundJob.attempts + 1,
                        started_at=func.now(),
                        updated_at=func.now(),
                        finished_at=None,
                    )
                    .returning(
                        BackgroundJob.id,
                        BackgroundJob.type,
                        BackgroundJob.payload,
                        BackgroundJob.attempts,
                        BackgroundJob.max_attempts,
                        BackgroundJob.created_at,
                    )
                    .execution_options(synchronize_session=False)
                )
            ).first()
        if row is None:
            return None
        return _ClaimedJob(
            job_id=str(row.id),
            job_type=row.type,
            payload=dict(row.payload or {}),
            attempt=int(row.attempts),
            max_attempts=int(row.max_attempts),
            created_at=row.created_at,
        )

    async def _unclaimable(self, job_type: str, job_id: str, message_id: str, fields: Mapping[str, str]) -> None:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(BackgroundJob.status, BackgroundJob.run_after).where(BackgroundJob.id == uuid.UUID(job_id))
                )
            ).first()
        if row is None:
            missing = int(fields.get("missing", "0") or 0) + 1
            if missing <= self.missing_row_retries:
                # Row not visible yet (enqueuing transaction still open) or deleted.
                await self.redis.zadd(DELAYED_ZSET, {_delayed_member(job_type, job_id, missing): time.time() + missing})
            else:
                log.error("worker.job_row_missing", job_id=job_id, job_type=job_type)
        elif row.status == JobState.QUEUED.value and row.run_after is not None:
            await self.redis.zadd(DELAYED_ZSET, {_delayed_member(job_type, job_id): row.run_after.timestamp()})
        else:
            log.debug("worker.duplicate_message", job_id=job_id, status=row.status)
        await self._ack(job_type, message_id)

    async def _process(self, job_type: str, message_id: str, fields: dict[str, str]) -> None:
        try:
            await self._process_message(job_type, message_id, fields)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # bookkeeping failed (DB/Redis): the unacked message is reclaimed later
            ERRORS.labels(component="worker").inc()
            log.error("worker.message_failed", job_type=job_type, message_id=message_id, error=_error_text(exc), exc_info=exc)

    async def _process_message(self, job_type: str, message_id: str, fields: dict[str, str]) -> None:
        job_id = fields.get("job_id", "")
        try:
            uuid.UUID(job_id)
        except ValueError:
            log.error("worker.malformed_message", job_type=job_type, message_id=message_id)
            await self._ack(job_type, message_id)
            return
        spec = _HANDLERS.get(job_type)
        try:
            claimed = await self._claim(job_id)
        except Exception as exc:  # DB unavailable: leave the message pending (reclaimed later)
            ERRORS.labels(component="worker").inc()
            log.warning("worker.claim_failed", job_id=job_id, error=_error_text(exc))
            return
        if claimed is None:
            await self._unclaimable(job_type, job_id, message_id, fields)
            return
        if spec is None:  # pragma: no cover - only registered streams are read
            await self._finish_failed(claimed, message_id, RuntimeError("no handler registered"), permanent=True)
            return
        max_attempts = spec.max_attempts or claimed.max_attempts
        if claimed.attempt > max_attempts:
            await self._finish_failed(
                claimed, message_id, RuntimeError("attempts exhausted (worker crashed or was killed)"), permanent=False,
                exhausted=True,
            )
            return
        ctx = JobContext(
            job_id=claimed.job_id,
            job_type=job_type,
            attempt=claimed.attempt,
            max_attempts=max_attempts,
            payload=claimed.payload,
            message_id=message_id,
            enqueued_at=claimed.created_at,
        )
        bind_context(job_id=claimed.job_id, job_type=job_type)
        heartbeat = asyncio.create_task(self._heartbeat(job_type, message_id, claimed.job_id))
        started = time.perf_counter()
        try:
            if spec.timeout_seconds:
                async with asyncio.timeout(spec.timeout_seconds):
                    result = await _invoke(spec, ctx)
            else:
                result = await _invoke(spec, ctx)
        except asyncio.CancelledError:
            heartbeat.cancel()
            if self._stopping:
                await self._requeue_cancelled(claimed, job_type, message_id)
            raise
        except Exception as exc:
            heartbeat.cancel()
            JOB_SECONDS.labels(type=job_type).observe(time.perf_counter() - started)
            if _is_retryable(exc) and claimed.attempt < max_attempts:
                await self._schedule_retry(claimed, message_id, exc)
            else:
                await self._finish_failed(claimed, message_id, exc, permanent=not _is_retryable(exc))
            return
        heartbeat.cancel()
        duration = time.perf_counter() - started
        JOB_SECONDS.labels(type=job_type).observe(duration)
        await self._finish_ok(claimed, message_id, result, duration)

    async def _heartbeat(self, job_type: str, message_id: str, job_id: str) -> None:
        interval = max(0.5, self.visibility_timeout / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.redis.xclaim(stream_key(job_type), GROUP, self.consumer, 0, [message_id], justid=True)
                async with session_scope() as session:
                    await session.execute(
                        update(BackgroundJob)
                        .where(BackgroundJob.id == uuid.UUID(job_id), BackgroundJob.status == JobState.RUNNING.value)
                        .values(updated_at=func.now())
                        .execution_options(synchronize_session=False)
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("worker.heartbeat_failed", job_id=job_id, error=type(exc).__name__)

    async def _finish_ok(self, claimed: _ClaimedJob, message_id: str, result: Any, duration: float) -> None:
        stored: dict[str, Any] | None
        if result is None:
            stored = None
        else:
            try:
                stored = _normalize_payload(result if isinstance(result, Mapping) else {"value": result})
            except (TypeError, ValueError):
                stored = {"value": str(result)[:_MAX_ERROR_CHARS]}
        async with session_scope() as session:
            await session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == uuid.UUID(claimed.job_id))
                .values(
                    status=JobState.SUCCEEDED.value,
                    result=stored,
                    finished_at=func.now(),
                    updated_at=func.now(),
                    last_error=None,
                    dedupe_key=None,
                )
                .execution_options(synchronize_session=False)
            )
        await self._ack(claimed.job_type, message_id)
        self.processed += 1
        JOBS.labels(type=claimed.job_type, status=JobState.SUCCEEDED.value).inc()
        log.info("job.succeeded", job_id=claimed.job_id, job_type=claimed.job_type, attempt=claimed.attempt,
                 duration_ms=round(duration * 1000, 1))

    def _backoff(self, attempt: int) -> float:
        delay = min(self.backoff_cap, self.backoff_base * (2 ** max(0, attempt - 1)))
        return delay * random.uniform(0.8, 1.2)

    async def _schedule_retry(self, claimed: _ClaimedJob, message_id: str, exc: BaseException) -> None:
        delay = self._backoff(claimed.attempt)
        run_after = _now() + timedelta(seconds=delay)
        async with session_scope() as session:
            await session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == uuid.UUID(claimed.job_id))
                .values(status=JobState.QUEUED.value, last_error=_error_text(exc), run_after=run_after, updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
        await self.redis.zadd(DELAYED_ZSET, {_delayed_member(claimed.job_type, claimed.job_id): run_after.timestamp()})
        await self._ack(claimed.job_type, message_id)
        JOBS.labels(type=claimed.job_type, status="retried").inc()
        log.warning("job.retry_scheduled", job_id=claimed.job_id, job_type=claimed.job_type, attempt=claimed.attempt,
                    retry_in_seconds=round(delay, 2), error=type(exc).__name__)

    async def _finish_failed(
        self, claimed: _ClaimedJob, message_id: str, exc: BaseException, *, permanent: bool, exhausted: bool = False
    ) -> None:
        status = JobState.FAILED if permanent else JobState.DEAD
        error = _error_text(exc)
        async with session_scope() as session:
            await session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == uuid.UUID(claimed.job_id))
                .values(status=status.value, last_error=error, finished_at=func.now(), updated_at=func.now(), dedupe_key=None)
                .execution_options(synchronize_session=False)
            )
        try:
            await self.redis.xadd(
                DEAD_STREAM,
                {
                    "job_id": claimed.job_id,
                    "type": claimed.job_type,
                    "status": status.value,
                    "attempts": str(claimed.attempt),
                    "error": error,
                    "failed_at": _now().isoformat(),
                },
                maxlen=_DEAD_STREAM_MAXLEN,
                approximate=True,
            )
        except (RedisError, OSError) as redis_exc:
            log.warning("worker.dead_letter_failed", job_id=claimed.job_id, error=type(redis_exc).__name__)
        await self._ack(claimed.job_type, message_id)
        JOBS.labels(type=claimed.job_type, status=status.value).inc()
        ERRORS.labels(component="worker").inc()
        log.error("job.failed", job_id=claimed.job_id, job_type=claimed.job_type, attempt=claimed.attempt,
                  status=status.value, exhausted=exhausted, error=error)
        await self._run_exhausted_hook(claimed, exc)

    async def _run_exhausted_hook(self, claimed: _ClaimedJob, exc: BaseException) -> None:
        """The handler's ``on_exhausted`` runs after the row is dead/failed; its own failure is logged, never raised."""
        spec = _HANDLERS.get(claimed.job_type)
        hook = spec.on_exhausted if spec is not None else None
        if hook is None:
            return
        ctx = JobContext(
            job_id=claimed.job_id,
            job_type=claimed.job_type,
            attempt=claimed.attempt,
            max_attempts=claimed.max_attempts,
            payload=claimed.payload,
            enqueued_at=claimed.created_at,
        )
        try:
            async with asyncio.timeout(60):
                await hook(ctx, exc)
        except Exception as hook_exc:  # noqa: BLE001 - the job is already dead; keep the worker alive
            ERRORS.labels(component="worker").inc()
            log.error(
                "job.exhausted_hook_failed", job_id=claimed.job_id, job_type=claimed.job_type,
                error=_error_text(hook_exc),
            )  # fmt: skip

    async def _requeue_cancelled(self, claimed: _ClaimedJob, job_type: str, message_id: str) -> None:
        """Shutdown interrupted the job: give the attempt back and dispatch it again."""
        try:
            async with session_scope() as session:
                await session.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == uuid.UUID(claimed.job_id), BackgroundJob.status == JobState.RUNNING.value)
                    .values(
                        status=JobState.QUEUED.value,
                        attempts=func.greatest(BackgroundJob.attempts - 1, 0),
                        run_after=None,
                        last_error="interrupted by worker shutdown",
                        updated_at=func.now(),
                    )
                    .execution_options(synchronize_session=False)
                )
            await self.redis.xadd(stream_key(job_type), {"job_id": claimed.job_id, "type": job_type, "enqueued_at": _now().isoformat()})
            await self._ack(job_type, message_id)
            JOBS.labels(type=job_type, status="interrupted").inc()
            log.warning("job.requeued_on_shutdown", job_id=claimed.job_id, job_type=job_type)
        except Exception as exc:  # recovery/reclaim will pick it up
            log.warning("worker.requeue_failed", job_id=claimed.job_id, error=_error_text(exc))

    # --------------------------------------------------- periodic duties

    async def move_due_delayed(self, *, limit: int = 200) -> int:
        """Move due entries of ``jobs:delayed`` to their streams (safe with many workers)."""
        due: Any = await self.redis.zrangebyscore(DELAYED_ZSET, "-inf", time.time(), start=0, num=limit)
        moved = 0
        for member in due:
            if not await self.redis.zrem(DELAYED_ZSET, member):
                continue  # another worker took it
            try:
                data = json.loads(member)
                fields: dict[Any, Any] = {
                    "job_id": str(data["job_id"]),
                    "type": str(data["type"]),
                    "enqueued_at": _now().isoformat(),
                }
                if data.get("missing"):
                    fields["missing"] = str(int(data["missing"]))
                await self.redis.xadd(stream_key(fields["type"]), fields)
                moved += 1
            except (ValueError, KeyError, TypeError):
                log.error("worker.bad_delayed_member")
        return moved

    async def reclaim_idle(self) -> int:
        """Take over messages idle longer than the visibility timeout (crashed consumers)."""
        reclaimed = 0
        idle_ms = int(self.visibility_timeout * 1000)
        for job_type in self.job_types:
            free = self._free(job_type)
            if free <= 0:
                continue
            try:
                result = await self.redis.xautoclaim(
                    stream_key(job_type), GROUP, self.consumer, min_idle_time=idle_ms, start_id="0-0", count=free
                )
            except ResponseError as exc:
                if "NOGROUP" in str(exc):
                    await self._ensure_group(job_type)
                    continue
                raise
            messages = result[1] if len(result) > 1 else []
            deleted = result[2] if len(result) > 2 else []
            for message_id in deleted or []:
                await self.redis.xack(stream_key(job_type), GROUP, message_id)
            for message_id, fields in messages:
                if fields is None:
                    await self._ack(job_type, message_id)
                    continue
                log.warning("worker.message_reclaimed", job_type=job_type, message_id=message_id)
                self._start(job_type, message_id, fields)
                reclaimed += 1
        return reclaimed

    async def recover(self) -> int:
        """Startup: re-dispatch rows left ``queued`` (when their stream is empty)
        and rows left ``running`` without a heartbeat."""
        return await self._redispatch(startup=True)

    async def reconcile(self) -> int:
        """Periodic safety net for stream messages lost after the row was committed."""
        if not await self.redis.set(RECONCILE_LOCK, self.consumer, nx=True, ex=max(1, int(self.reconcile_interval * 0.8))):
            return 0
        return await self._redispatch(startup=False)

    async def _redispatch(self, *, startup: bool) -> int:
        handled = set(self.job_types)
        # Decide which streams are empty *before* dispatching anything below.
        empty_streams = [t for t in sorted(handled) if await self.redis.xlen(stream_key(t)) == 0]
        stale_running = func.now() - timedelta(seconds=self.visibility_timeout * (1 if startup else 2))
        settle = func.now() - timedelta(seconds=0 if startup else 30)
        to_dispatch: list[tuple[str, str, datetime | None]] = []
        async with session_scope() as session:
            stale = (
                await session.execute(
                    update(BackgroundJob)
                    .where(
                        BackgroundJob.status == JobState.RUNNING.value,
                        BackgroundJob.updated_at < stale_running,
                        BackgroundJob.type.in_(handled),
                    )
                    .values(status=JobState.QUEUED.value, run_after=None, updated_at=func.now())
                    .returning(BackgroundJob.id, BackgroundJob.type)
                    .execution_options(synchronize_session=False)
                )
            ).all()
        stale_ids = {row.id for row in stale}
        to_dispatch.extend((str(row.id), row.type, None) for row in stale)
        for job_type in empty_streams:
            async with session_scope() as session:
                stmt = (
                    select(BackgroundJob.id, BackgroundJob.run_after)
                    .where(
                        BackgroundJob.status == JobState.QUEUED.value,
                        BackgroundJob.type == job_type,
                        BackgroundJob.updated_at <= settle,
                    )
                    .order_by(BackgroundJob.created_at)
                    .limit(1000)
                )
                if stale_ids:
                    stmt = stmt.where(BackgroundJob.id.not_in(stale_ids))
                rows = (await session.execute(stmt)).all()
                if not startup:
                    rows = [r for r in rows if r.run_after is None or r.run_after <= _now()]
                if rows:
                    await session.execute(
                        update(BackgroundJob)
                        .where(BackgroundJob.id.in_([r.id for r in rows]))
                        .values(updated_at=func.now())
                        .execution_options(synchronize_session=False)
                    )
            to_dispatch.extend((str(r.id), job_type, r.run_after) for r in rows)
        for job_id, job_type, run_after in to_dispatch:
            await dispatch(job_id, job_type, run_after, redis=self.redis)
        if to_dispatch:
            log.warning("worker.jobs_redispatched", count=len(to_dispatch), startup=startup)
        return len(to_dispatch)

    async def refresh_queue_depths(self) -> dict[str, int]:
        depths: dict[str, int] = {}
        for job_type in self.job_types:
            depths[job_type] = int(await self.redis.xlen(stream_key(job_type)))
            QUEUE_DEPTH.labels(queue=f"jobs:{job_type}").set(depths[job_type])
        depths["delayed"] = int(await self.redis.zcard(DELAYED_ZSET))
        depths["dead"] = int(await self.redis.xlen(DEAD_STREAM))
        QUEUE_DEPTH.labels(queue="jobs:delayed").set(depths["delayed"])
        QUEUE_DEPTH.labels(queue="jobs:dead").set(depths["dead"])
        return depths

    # ------------------------------------------------------------ testing

    async def drain(self, *, timeout: float = 10.0, include_delayed: bool = True) -> int:  # noqa: ASYNC109
        """Process available work until the streams are empty (tests / one-shot runs).

        Delayed entries are honoured: with ``include_delayed`` the call keeps
        going until scheduled retries become due and finish, or the timeout.
        """
        await self.ensure_groups()
        deadline = time.monotonic() + timeout
        start_count = self.processed
        while time.monotonic() < deadline:
            await self.move_due_delayed()
            types = [t for t in self.job_types if self._free(t) > 0]
            got = 0
            if types:
                response: Any = await self.redis.xreadgroup(
                    GROUP, self.consumer, {stream_key(t): ">" for t in types},
                    count=max(1, min(self._free(t) for t in types)),
                )
                for stream, messages in response or []:
                    for message_id, fields in messages:
                        self._start(stream[len(STREAM_PREFIX):], message_id, fields)
                        got += 1
            if self._tasks:
                await asyncio.wait(set(self._tasks), timeout=0.05)
                continue
            if got:
                continue
            pending_delayed = include_delayed and await self._has_delayed_for(self.job_types)
            backlog = any([await self.redis.xlen(stream_key(t)) > 0 for t in self.job_types])
            if not pending_delayed and not backlog:
                break
            await asyncio.sleep(0.02)
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=max(0.0, deadline - time.monotonic()))
        return self.processed - start_count

    async def _has_delayed_for(self, job_types: Iterable[str]) -> bool:
        wanted = set(job_types)
        members: Any = await self.redis.zrange(DELAYED_ZSET, 0, -1)
        for member in members:
            try:
                if json.loads(member).get("type") in wanted:
                    return True
            except ValueError:
                continue
        return False


# -------------------------------------------------------------- session bus


def session_bus_channel(session_id: uuid.UUID | str) -> str:
    return f"session:{session_id}:bus"


async def publish_session_event(session_id: uuid.UUID | str, event: Mapping[str, Any], *, redis: Redis | None = None) -> int:
    """Publish a JSON event on ``session:{id}:bus``; returns the receiver count."""
    if "type" not in event:
        raise ValueError("Session events need a 'type'")
    message = json.dumps(dict(event), default=_json_default, separators=(",", ":"))
    try:
        return int(await _redis(redis).publish(session_bus_channel(session_id), message))
    except (RedisError, OSError) as exc:
        ERRORS.labels(component="queue").inc()
        log.warning("session_bus.publish_failed", session_id=str(session_id), error=type(exc).__name__)
        return 0


class SessionEventSubscription:
    """Iterate JSON events of one session channel (malformed messages are skipped)."""

    def __init__(self, pubsub: Any, channel: str) -> None:
        self._pubsub = pubsub
        self.channel = channel

    async def get(self, timeout: float = 1.0) -> dict[str, Any] | None:  # noqa: ASYNC109
        message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        if not message or message.get("type") != "message":
            return None
        data = message.get("data")
        try:
            event = json.loads(data)
        except (TypeError, ValueError):
            log.warning("session_bus.malformed_message", channel=self.channel)
            return None
        return event if isinstance(event, dict) else None

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self.get(timeout=1.0)
            if event is not None:
                yield event


@asynccontextmanager
async def subscribe_session_events(
    session_id: uuid.UUID | str, *, redis: Redis | None = None
) -> AsyncIterator[SessionEventSubscription]:
    """``async with subscribe_session_events(sid) as sub: async for event in sub: ...``"""
    channel = session_bus_channel(session_id)
    pubsub = _redis(redis).pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(channel)
    try:
        yield SessionEventSubscription(pubsub, channel)
    finally:
        with contextlib.suppress(RedisError, OSError, RuntimeError):
            await pubsub.unsubscribe(channel)
        with contextlib.suppress(RedisError, OSError, RuntimeError):
            await pubsub.aclose()
