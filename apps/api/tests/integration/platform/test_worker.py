"""Queue + worker: enqueue, execution, dedupe, delays, retries, dead letters,
post-commit dispatch, recovery, reclaim, graceful shutdown, session bus and the
``python -m app.worker`` runtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

import app.workers.system  # noqa: F401  (registers system.ping)
from app.core import queue
from app.core.metrics import JOBS, QUEUE_DEPTH
from app.db.models import BackgroundJob
from app.db.session import session_scope
from app.domain.enums import JobState

pytestmark = pytest.mark.integration

PING = "system.ping"


@pytest.fixture
def test_handlers():
    calls: dict[str, int] = {}

    @queue.job_handler("test.permanent")
    async def permanent(payload: dict[str, Any]) -> None:
        calls["permanent"] = calls.get("permanent", 0) + 1
        raise queue.PermanentJobError("bad payload")

    @queue.job_handler("test.slow", concurrency=2, timeout_seconds=0.2)
    async def slow(payload: dict[str, Any], ctx: queue.JobContext) -> dict[str, int]:
        calls["slow"] = calls.get("slow", 0) + 1
        await asyncio.sleep(float(payload.get("sleep", 0)))
        return {"attempt": ctx.attempt}

    @queue.job_handler("test.kwargs")
    async def kwargs_style(*, value: int) -> int:
        return value * 2

    exhausted: list[tuple[str, int, str]] = []

    async def on_exhausted(ctx: queue.JobContext, exc: BaseException) -> None:
        exhausted.append((ctx.job_id, ctx.attempt, type(exc).__name__))
        if ctx.payload.get("hook_raises"):
            raise RuntimeError("hook exploded")

    @queue.job_handler("test.exhausted", max_attempts=2, on_exhausted=on_exhausted)
    async def always_fails(payload: dict[str, Any], ctx: queue.JobContext) -> None:
        calls["exhausted"] = calls.get("exhausted", 0) + 1
        if payload.get("permanent"):
            raise queue.PermanentJobError("never retry")
        raise RuntimeError("transient failure")

    calls["_exhausted_hook"] = exhausted  # type: ignore[assignment]
    yield calls
    for job_type in ("test.permanent", "test.slow", "test.kwargs", "test.exhausted"):
        queue.unregister_handler(job_type)


async def _job(job_id: str) -> BackgroundJob:
    async with session_scope() as session:
        job = await session.get(BackgroundJob, uuid.UUID(job_id))
        assert job is not None
        return job


async def test_enqueue_and_execute(worker_factory, redis):
    before = JOBS.labels(type=PING, status="succeeded")._value.get()
    job_id = await queue.enqueue(PING, {"echo": "hello", "when": datetime(2026, 1, 1, tzinfo=UTC)})
    assert await redis.xlen(queue.stream_key(PING)) == 1
    job = await _job(job_id)
    assert job.status == JobState.QUEUED.value and job.payload["when"] == "2026-01-01T00:00:00+00:00"
    worker = worker_factory(job_types=[PING])
    assert await worker.drain() == 1
    job = await _job(job_id)
    assert job.status == "succeeded" and job.attempts == 1
    assert job.result == {"pong": True, "echo": "hello", "attempt": 1}
    assert job.started_at is not None and job.finished_at is not None
    assert await redis.xlen(queue.stream_key(PING)) == 0  # acked and deleted
    assert JOBS.labels(type=PING, status="succeeded")._value.get() == before + 1
    pending = await redis.xpending(queue.stream_key(PING), queue.GROUP)
    assert pending["pending"] == 0


async def test_dedupe_while_unfinished(worker_factory):
    first = await queue.enqueue(PING, {"n": 1}, dedupe_key="ping:abc")
    second = await queue.enqueue(PING, {"n": 2}, dedupe_key="ping:abc")
    assert first == second
    await worker_factory(job_types=[PING]).drain()
    assert (await _job(first)).dedupe_key is None  # released on completion
    third = await queue.enqueue(PING, {"n": 3}, dedupe_key="ping:abc")
    assert third != first


async def test_delayed_job(worker_factory, redis):
    job_id = await queue.enqueue(PING, {}, delay_seconds=0.4)
    assert await redis.zcard(queue.DELAYED_ZSET) == 1 and await redis.xlen(queue.stream_key(PING)) == 0
    worker = worker_factory(job_types=[PING])
    assert await worker.drain(include_delayed=False) == 0
    assert (await _job(job_id)).status == "queued"
    assert await worker.drain(timeout=5) == 1
    assert (await _job(job_id)).status == "succeeded"


async def test_retry_with_backoff_then_success(worker_factory):
    before = JOBS.labels(type=PING, status="retried")._value.get()
    job_id = await queue.enqueue(PING, {"fail_until_attempt": 3}, max_attempts=3)
    await worker_factory(job_types=[PING]).drain(timeout=10)
    job = await _job(job_id)
    assert job.status == "succeeded" and job.attempts == 3 and job.result["attempt"] == 3
    assert JOBS.labels(type=PING, status="retried")._value.get() == before + 2


async def test_dead_letter_after_max_attempts(worker_factory, redis):
    job_id = await queue.enqueue(PING, {"fail_until_attempt": 99}, max_attempts=2)
    await worker_factory(job_types=[PING]).drain(timeout=10)
    job = await _job(job_id)
    assert job.status == "dead" and job.attempts == 2
    assert "PingFailure" in job.last_error and job.finished_at is not None
    dead = await redis.xrange(queue.DEAD_STREAM)
    assert len(dead) == 1
    fields = dead[0][1]
    assert fields["job_id"] == job_id and fields["status"] == "dead" and fields["attempts"] == "2"


async def test_permanent_failure_skips_retries(worker_factory, redis, test_handlers):
    job_id = await queue.enqueue("test.permanent", {}, max_attempts=5)
    await worker_factory(job_types=["test.permanent"]).drain()
    job = await _job(job_id)
    assert job.status == "failed" and job.attempts == 1 and test_handlers["permanent"] == 1
    assert (await redis.xrange(queue.DEAD_STREAM))[0][1]["status"] == "failed"


async def test_handler_timeout_is_retried(worker_factory, test_handlers):
    job_id = await queue.enqueue("test.slow", {"sleep": 1}, max_attempts=2)
    await worker_factory(job_types=["test.slow"]).drain(timeout=10)
    job = await _job(job_id)
    assert job.status == "dead" and job.attempts == 2 and "TimeoutError" in job.last_error


async def test_keyword_only_handlers(worker_factory, test_handlers):
    job_id = await queue.enqueue("test.kwargs", {"value": 21, "ignored": True})
    await worker_factory(job_types=["test.kwargs"]).drain()
    assert (await _job(job_id)).result == {"value": 42}


async def test_enqueue_inside_transaction_dispatches_after_commit(redis, db_session, test_database):
    job_id = await queue.enqueue(PING, {"n": 1}, db=db_session)
    assert await redis.xlen(queue.stream_key(PING)) == 0  # not yet committed
    await db_session.commit()
    await asyncio.sleep(0.05)
    from app.core.lifecycle import drain_after_commit

    await drain_after_commit()
    assert await redis.xlen(queue.stream_key(PING)) == 1
    assert (await _job(job_id)).status == "queued"
    rolled_back = await queue.enqueue(PING, {"n": 2}, db=db_session)
    await db_session.rollback()
    await drain_after_commit()
    assert await redis.xlen(queue.stream_key(PING)) == 1
    async with session_scope() as session:
        assert await session.get(BackgroundJob, uuid.UUID(rolled_back)) is None


async def test_startup_recovery_and_stale_running_rows(worker_factory, redis, db_session):
    lost = BackgroundJob(
        id=uuid.uuid4(), type=PING, status="queued", payload={"echo": "lost"}, attempts=0, max_attempts=3
    )
    stale = BackgroundJob(
        id=uuid.uuid4(), type=PING, status="running", payload={"echo": "stale"}, attempts=1, max_attempts=3
    )
    db_session.add_all([lost, stale])
    await db_session.commit()
    await db_session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == stale.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=10))
    )
    await db_session.commit()
    worker = worker_factory(job_types=[PING])
    await worker.ensure_groups()
    assert await worker.recover() == 2
    assert await worker.drain() == 2
    assert (await _job(str(lost.id))).status == "succeeded"
    stale_job = await _job(str(stale.id))
    assert stale_job.status == "succeeded" and stale_job.attempts == 2


async def test_reclaims_messages_of_a_crashed_consumer(worker_factory, redis):
    job_id = await queue.enqueue(PING, {"echo": "orphan"})
    crashed = worker_factory(job_types=[PING], consumer_name="crashed-consumer")
    await crashed.ensure_groups()
    messages = await redis.xreadgroup(queue.GROUP, "crashed-consumer", {queue.stream_key(PING): ">"}, count=1)
    assert messages  # delivered, never acked: the consumer "died"
    rescuer = worker_factory(job_types=[PING], visibility_timeout=0.3)
    await asyncio.sleep(0.4)
    assert await rescuer.reclaim_idle() == 1
    await rescuer.drain()
    assert (await _job(job_id)).status == "succeeded"
    assert (await redis.xpending(queue.stream_key(PING), queue.GROUP))["pending"] == 0


async def test_duplicate_and_orphan_messages_are_harmless(worker_factory, redis):
    job_id = await queue.enqueue(PING, {})
    await redis.xadd(queue.stream_key(PING), {"job_id": job_id, "type": PING})  # duplicate dispatch
    await redis.xadd(queue.stream_key(PING), {"job_id": "not-a-uuid", "type": PING})
    missing = str(uuid.uuid4())
    await redis.xadd(queue.stream_key(PING), {"job_id": missing, "type": PING})
    worker = worker_factory(job_types=[PING], missing_row_retries=1)
    await worker.drain(timeout=4)
    job = await _job(job_id)
    assert job.status == "succeeded" and job.attempts == 1
    assert await redis.xlen(queue.stream_key(PING)) == 0
    assert await redis.zcard(queue.DELAYED_ZSET) == 0


async def test_graceful_shutdown_requeues_interrupted_jobs(worker_factory, redis):
    job_id = await queue.enqueue(PING, {"sleep_seconds": 10})
    worker = worker_factory(job_types=[PING], shutdown_grace=0.2)
    stop = asyncio.Event()
    runner = asyncio.create_task(worker.run(stop))
    for _ in range(100):
        if (await _job(job_id)).status == "running":
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(runner, timeout=10)
    job = await _job(job_id)
    assert job.status == "queued" and job.attempts == 0 and job.last_error == "interrupted by worker shutdown"
    assert await redis.xlen(queue.stream_key(PING)) == 1  # re-dispatched for the next worker


async def test_worker_run_loop_and_queue_depth(worker_factory, redis):
    worker = worker_factory(job_types=[PING], depth_interval=0.1)
    stop = asyncio.Event()
    runner = asyncio.create_task(worker.run(stop))
    try:
        ids = [await queue.enqueue(PING, {"i": i}) for i in range(5)]
        results = [await queue.wait_for_job(job_id, timeout=10) for job_id in ids]
        assert all(r.status == "succeeded" for r in results)
        await asyncio.sleep(0.25)
        assert QUEUE_DEPTH.labels(queue=PING)._value.get() == 0
        assert worker.healthy() and worker.processed == 5
    finally:
        stop.set()
        await asyncio.wait_for(runner, timeout=10)
    assert not worker.healthy()


async def test_periodic_jobs_enqueue_once_per_interval(redis, test_database, _db_cleanup):
    # Other modules register their own periodic jobs at import time; isolate the registry.
    saved = dict(queue._PERIODIC)
    queue._PERIODIC.clear()
    queue.register_periodic_job(PING, 3600, {"echo": "periodic"})
    try:
        now = 1_800_000_000.0
        first = await queue.enqueue_due_periodic_jobs(redis=redis, now=now)
        again = await queue.enqueue_due_periodic_jobs(redis=redis, now=now + 60)
        later = await queue.enqueue_due_periodic_jobs(redis=redis, now=now + 3600)
        assert len(first) == 1 and again == [] and len(later) == 1
    finally:
        queue._PERIODIC.clear()
        queue._PERIODIC.update(saved)


async def test_session_bus_roundtrip(redis):
    session_id = uuid.uuid4()
    async with queue.subscribe_session_events(session_id, redis=redis) as subscription:
        receivers = await queue.publish_session_event(
            session_id, {"type": "evaluation.ready", "answer_id": uuid.uuid4()}, redis=redis
        )
        assert receivers == 1
        event = None
        for _ in range(20):
            event = await subscription.get(timeout=0.2)
            if event:
                break
        assert event is not None and event["type"] == "evaluation.ready"
        await redis.publish(queue.session_bus_channel(session_id), "not json")
        assert await subscription.get(timeout=0.2) is None
    with pytest.raises(ValueError):
        await queue.publish_session_event(session_id, {"no_type": True}, redis=redis)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_worker_process_runtime(settings, redis, storage, _db_cleanup):
    from app.worker import HEARTBEAT_PREFIX, run_worker

    settings.worker_metrics_port = _free_port()
    settings.api_host = "127.0.0.1"
    stop = asyncio.Event()
    runtime = asyncio.create_task(run_worker(settings, stop=stop))
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{settings.worker_metrics_port}") as http:
            for _ in range(100):
                with contextlib.suppress(httpx.TransportError):
                    if (await http.get("/healthz")).status_code == 200:
                        break
                await asyncio.sleep(0.05)
            health = (await http.get("/healthz")).json()
            assert health["status"] == "ok" and PING in health["job_types"]
            job_id = await queue.enqueue(PING, {"echo": "runtime"})
            assert (await queue.wait_for_job(job_id, timeout=10)).status == "succeeded"
            metrics = await http.get("/metrics")
            assert metrics.status_code == 200 and "interview_jobs_total" in metrics.text
            assert (await http.get("/nope")).status_code == 404
        beat = await redis.get(f"{HEARTBEAT_PREFIX}{settings.instance_id}")
        assert beat and json.loads(beat)["id"] == settings.instance_id
        assert settings.instance_id in await redis.smembers("worker:ids")
        assert PING in await redis.smembers(queue.JOB_TYPES_SET)
    finally:
        stop.set()
        await asyncio.wait_for(runtime, timeout=20)
    assert await redis.get(f"{HEARTBEAT_PREFIX}{settings.instance_id}") is None
    assert settings.instance_id not in await redis.smembers("worker:ids")
    async with session_scope() as session:
        rows = (await session.scalars(select(BackgroundJob.status).where(BackgroundJob.type == PING))).all()
    assert rows == ["succeeded"]


# ------------------------------------------------------------- on_exhausted hook (S2)


async def test_on_exhausted_runs_once_when_retries_are_exhausted(worker_factory, redis, test_handlers):
    job_id = await queue.enqueue("test.exhausted", {})
    await worker_factory(job_types=["test.exhausted"]).drain(timeout=10)
    assert (await _job(job_id)).status == "dead" and test_handlers["exhausted"] == 2
    assert test_handlers["_exhausted_hook"] == [(job_id, 2, "RuntimeError")]


async def test_on_exhausted_runs_for_permanent_failures(worker_factory, test_handlers):
    job_id = await queue.enqueue("test.exhausted", {"permanent": True})
    await worker_factory(job_types=["test.exhausted"]).drain(timeout=10)
    assert (await _job(job_id)).status == "failed"
    assert test_handlers["_exhausted_hook"] == [(job_id, 1, "PermanentJobError")]


async def test_on_exhausted_runs_when_a_crashed_consumer_used_every_attempt(
    worker_factory, redis, test_handlers
):
    job_id = await queue.enqueue("test.exhausted", {}, max_attempts=1)
    crashed = worker_factory(job_types=["test.exhausted"], consumer_name="crashed-consumer")
    await crashed.ensure_groups()
    # Deliver, bump the attempt counter as a real claim would, never ack: the consumer "died" mid-job.
    assert await redis.xreadgroup(
        queue.GROUP, "crashed-consumer", {queue.stream_key("test.exhausted"): ">"}, count=1
    )
    # The handler is registered with max_attempts=2: two consumed attempts, so the reclaim is attempt 3.
    async with session_scope() as session:
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == uuid.UUID(job_id))
            .values(attempts=2, status="running")
        )
    rescuer = worker_factory(job_types=["test.exhausted"], visibility_timeout=0.3)
    await asyncio.sleep(0.4)
    assert await rescuer.reclaim_idle() == 1
    await rescuer.drain(timeout=10)
    job = await _job(job_id)
    assert job.status == "dead" and "attempts exhausted" in (job.last_error or "")
    assert test_handlers.get("exhausted") is None  # the handler itself never ran again
    assert [(a, name) for _, a, name in test_handlers["_exhausted_hook"]] == [(3, "RuntimeError")]


async def test_a_failing_hook_still_dead_letters_the_job(worker_factory, redis, test_handlers):
    job_id = await queue.enqueue("test.exhausted", {"hook_raises": True})
    await worker_factory(job_types=["test.exhausted"]).drain(timeout=10)
    assert (await _job(job_id)).status == "dead"
    assert len(test_handlers["_exhausted_hook"]) == 1
    assert (await redis.xrange(queue.DEAD_STREAM))[0][1]["job_id"] == job_id


# ------------------------------------------------------------------ outage-aware retries (S11, audit B3)


async def test_retry_schedule_and_retry_after_hint(worker_factory):
    from app.ai.breaker import CircuitOpenError

    @queue.job_handler("test.scheduled", max_attempts=4, retry_schedule=(100.0, 200.0))
    async def scheduled(payload: dict[str, Any], ctx: queue.JobContext) -> dict[str, int]:
        if ctx.attempt < int(payload.get("succeed_at", 99)):
            raise RuntimeError("model unavailable")
        return {"attempt": ctx.attempt}

    try:
        worker = worker_factory(job_types=["test.scheduled"], retry_schedule_scale=1.0)
        first = worker._retry_delay("test.scheduled", 1, RuntimeError())
        assert 80.0 <= first <= 120.0
        assert 160.0 <= worker._retry_delay("test.scheduled", 2, RuntimeError()) <= 240.0
        assert (
            160.0 <= worker._retry_delay("test.scheduled", 7, RuntimeError()) <= 240.0
        )  # the last delay repeats
        # An open circuit says when it lets calls through again: never retry earlier (also when wrapped).
        assert worker._retry_delay("test.scheduled", 1, CircuitOpenError("main", 500.0)) >= 500.0
        try:
            try:
                raise CircuitOpenError("main", 700.0)
            except CircuitOpenError as inner:
                raise RuntimeError("evaluation failed") from inner
        except RuntimeError as wrapped:
            assert worker._retry_delay("test.scheduled", 1, wrapped) >= 700.0
        assert (
            worker._retry_delay("test.scheduled", 1, CircuitOpenError("main", 10**9))
            <= queue.MAX_RETRY_AFTER_SECONDS
        )
        # Handlers without a schedule keep the exponential backoff.
        assert worker._retry_delay(PING, 1, RuntimeError()) <= worker.backoff_cap * 1.2

        # End to end with the test scale (100 s -> 0.1 s): retried on the schedule until it succeeds.
        job_id = await queue.enqueue("test.scheduled", {"succeed_at": 3})
        await worker_factory(job_types=["test.scheduled"]).drain(timeout=10)
        job = await _job(job_id)
        assert job.status == "succeeded" and job.attempts == 3
    finally:
        queue.unregister_handler("test.scheduled")


def test_model_backed_jobs_wait_out_an_outage():
    import importlib

    for module in ("interview", "coding", "intake", "resume"):
        importlib.import_module(f"app.workers.{module}")  # handlers register themselves on import
    specs = {spec.job_type: spec for spec in (queue._HANDLERS[t] for t in queue.registered_job_types())}
    for job_type in ("answer.evaluate", "coding.evaluate", "intake.evaluate", "intake.report"):
        assert specs[job_type].retry_schedule == queue.OUTAGE_RETRY_SCHEDULE, job_type
        assert specs[job_type].max_attempts == len(queue.OUTAGE_RETRY_SCHEDULE) + 1, job_type
    assert sum(queue.OUTAGE_RETRY_SCHEDULE) >= 30 * 60  # outlasts a ~30 min outage before falling back
    assert specs["profile.generate"].retry_schedule == (30.0, 120.0, 600.0, 1200.0)
    assert specs["session.prepare"].retry_schedule is None  # live latency matters there
