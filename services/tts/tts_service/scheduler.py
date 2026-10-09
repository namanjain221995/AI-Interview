"""Bounded scheduler for CPU-bound synthesis jobs.

Jobs run on a fixed pool of ``workers`` threads. Beyond that they wait in a FIFO
queue of at most ``max_queue`` jobs (``QueueFull`` when it is full). Identical
jobs (same key) in flight are shared. Every caller waits at most its own
timeout; a job that already started runs to completion, so its result can still
be cached, while a queued job nobody waits for any more is dropped unstarted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any


class QueueFull(Exception):
    """All workers are busy and the wait queue is full."""


class JobTimeout(Exception):
    """The caller's timeout expired; ``started`` tells whether the job was running."""

    def __init__(self, started: bool) -> None:
        super().__init__("synthesis timed out" + (" while running" if started else " while queued"))
        self.started = started


@dataclass(slots=True)
class _Job:
    future: asyncio.Future[Any]
    task: asyncio.Task[Any] | None = None
    waiters: int = 0
    started: bool = False


def _mark_retrieved(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


class Scheduler[T]:
    def __init__(
        self,
        *,
        workers: int,
        max_queue: int,
        on_change: Callable[[int, int], None] | None = None,
    ) -> None:
        self._workers = workers
        self._max_queue = max_queue
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tts-synth")
        self._slots = asyncio.Semaphore(workers)
        self._jobs: dict[Hashable, _Job] = {}
        self._queued = 0
        self._running = 0
        self._on_change = on_change

    @property
    def queued(self) -> int:
        return self._queued

    @property
    def running(self) -> int:
        return self._running

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change(self._queued, self._running)

    async def submit(
        self,
        key: Hashable,
        fn: Callable[[], T],
        *,
        max_wait: float,
        on_result: Callable[[T], None] | None = None,
    ) -> tuple[T, bool]:
        """Run ``fn`` in the pool (or join the identical job in flight).

        Waits at most ``max_wait`` seconds. Returns ``(result, shared)``. Raises
        ``QueueFull``, ``JobTimeout`` or whatever ``fn`` raised. ``on_result`` runs once on the event loop when the
        job succeeds, even if every caller has timed out by then.
        """
        job = self._jobs.get(key)
        shared = job is not None
        if job is None:
            if self._running + self._queued >= self._workers + self._max_queue:
                raise QueueFull()
            loop = asyncio.get_running_loop()
            job = _Job(future=loop.create_future())
            job.future.add_done_callback(_mark_retrieved)
            self._jobs[key] = job
            self._queued += 1
            self._changed()
            job.task = asyncio.create_task(self._run(job, fn, on_result), name="tts-job")
            job.task.add_done_callback(partial(self._finished, key, job))
        job.waiters += 1
        try:
            async with asyncio.timeout(max_wait):
                result: T = await asyncio.shield(job.future)
            return result, shared
        except TimeoutError:
            raise JobTimeout(started=job.started) from None
        finally:
            job.waiters -= 1
            if job.waiters == 0 and not job.started and job.task is not None and not job.task.done():
                job.task.cancel()

    async def _run[R](self, job: _Job, fn: Callable[[], R], on_result: Callable[[R], None] | None) -> R:
        await self._slots.acquire()
        try:
            job.started = True
            self._queued -= 1
            self._running += 1
            self._changed()
            try:
                result = await asyncio.get_running_loop().run_in_executor(self._executor, fn)
            finally:
                self._running -= 1
                self._changed()
            if on_result is not None:
                on_result(result)
            return result
        finally:
            self._slots.release()

    def _finished(self, key: Hashable, job: _Job, task: asyncio.Task[Any]) -> None:
        if self._jobs.get(key) is job:
            del self._jobs[key]
        if not job.started:  # cancelled while queued (possibly before its first step)
            self._queued -= 1
            self._changed()
        if job.future.done():
            return
        if task.cancelled():
            job.future.cancel()
        elif (error := task.exception()) is not None:
            job.future.set_exception(error)
        else:
            job.future.set_result(task.result())

    def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            if job.task is not None and not job.task.done():
                job.task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
