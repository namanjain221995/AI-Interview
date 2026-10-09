from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tts_service.scheduler import JobTimeout, QueueFull, Scheduler


def blocking(gate: threading.Event, value: str, log: list[str] | None = None):
    def run() -> str:
        if log is not None:
            log.append(f"start:{value}")
        gate.wait(timeout=5)
        return value

    return run


async def wait_until(predicate, limit: float = 2.0) -> None:
    deadline = time.monotonic() + limit
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


async def test_runs_at_most_workers_jobs_concurrently() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def job() -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return 1

    scheduler: Scheduler[int] = Scheduler(workers=2, max_queue=10)
    results = await asyncio.gather(*(scheduler.submit(i, job, max_wait=5) for i in range(6)))
    assert [r for r, _ in results] == [1] * 6
    assert peak == 2
    assert scheduler.queued == 0 and scheduler.running == 0
    scheduler.shutdown()


async def test_queue_full_rejects_new_jobs() -> None:
    gate = threading.Event()
    scheduler: Scheduler[str] = Scheduler(workers=1, max_queue=1)
    first = asyncio.create_task(scheduler.submit("a", blocking(gate, "a"), max_wait=5))
    second = asyncio.create_task(scheduler.submit("b", blocking(gate, "b"), max_wait=5))
    await wait_until(lambda: scheduler.running == 1 and scheduler.queued == 1)
    with pytest.raises(QueueFull):
        await scheduler.submit("c", blocking(gate, "c"), max_wait=5)
    gate.set()
    assert (await first)[0] == "a" and (await second)[0] == "b"
    scheduler.shutdown()


async def test_identical_jobs_are_shared() -> None:
    gate = threading.Event()
    calls: list[str] = []
    scheduler: Scheduler[str] = Scheduler(workers=1, max_queue=4)
    tasks = [
        asyncio.create_task(scheduler.submit("same", blocking(gate, "x", calls), max_wait=5))
        for _ in range(3)
    ]
    await wait_until(lambda: scheduler.running == 1)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert [r for r, _ in results] == ["x", "x", "x"]
    assert sorted(shared for _, shared in results) == [False, True, True]
    assert calls == ["start:x"]
    scheduler.shutdown()


async def test_timeout_while_running_still_delivers_result_to_on_result() -> None:
    gate = threading.Event()
    recorded: list[str] = []
    scheduler: Scheduler[str] = Scheduler(workers=1, max_queue=4)
    with pytest.raises(JobTimeout) as info:
        await scheduler.submit("k", blocking(gate, "late"), max_wait=0.05, on_result=recorded.append)
    assert info.value.started is True
    gate.set()
    await wait_until(lambda: recorded == ["late"])
    await wait_until(lambda: scheduler.running == 0)
    scheduler.shutdown()


async def test_abandoned_queued_job_never_runs() -> None:
    gate = threading.Event()
    log: list[str] = []
    scheduler: Scheduler[str] = Scheduler(workers=1, max_queue=4)
    running = asyncio.create_task(scheduler.submit("a", blocking(gate, "a", log), max_wait=5))
    await wait_until(lambda: scheduler.running == 1)
    with pytest.raises(JobTimeout) as info:
        await scheduler.submit("b", blocking(gate, "b", log), max_wait=0.05)
    assert info.value.started is False
    await wait_until(lambda: scheduler.queued == 0)
    gate.set()
    await running
    await asyncio.sleep(0.05)
    assert log == ["start:a"]
    scheduler.shutdown()


async def test_job_exception_propagates_and_scheduler_recovers() -> None:
    def boom() -> str:
        raise ValueError("bad input")

    scheduler: Scheduler[str] = Scheduler(workers=1, max_queue=1)
    with pytest.raises(ValueError, match="bad input"):
        await scheduler.submit("x", boom, max_wait=5)
    result, _ = await scheduler.submit("y", lambda: "ok", max_wait=5)
    assert result == "ok"
    assert scheduler.queued == 0 and scheduler.running == 0
    scheduler.shutdown()


async def test_on_change_reports_depth() -> None:
    seen: list[tuple[int, int]] = []
    gate = threading.Event()
    scheduler: Scheduler[str] = Scheduler(workers=1, max_queue=2, on_change=lambda q, r: seen.append((q, r)))
    tasks = [asyncio.create_task(scheduler.submit(k, blocking(gate, k), max_wait=5)) for k in ("a", "b")]
    await wait_until(lambda: (1, 1) in seen)
    gate.set()
    await asyncio.gather(*tasks)
    assert seen[-1] == (0, 0)
    scheduler.shutdown()
