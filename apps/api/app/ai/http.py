"""Resilient HTTP access to one local model endpoint.

One :class:`EndpointClient` per endpoint owns a pooled ``httpx.AsyncClient``,
a concurrency semaphore, a :class:`~app.ai.breaker.CircuitBreaker` and the
retry policy:

* retried (exponential backoff with jitter, ``Retry-After`` honoured):
  connection failures, 408/429/5xx responses and, for idempotent calls only,
  read timeouts and broken responses;
* never retried: other 4xx (the request itself is wrong), an open circuit,
  or anything once the caller's time budget is spent.

``timeout`` is the caller's total budget for the call (queueing for a slot,
every attempt and the backoff sleeps). Final failures map onto the AI error
hierarchy in :mod:`app.ai.base` (``AITimeout``, ``AIUnavailable``,
``AIRejected``); request payloads are never logged.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai.base import AIError, AIOutputInvalid, AIRejected, AITimeout, AIUnavailable, EndpointDisabled
from app.ai.breaker import CircuitBreaker, CircuitOpenError
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus

log = get_logger(__name__)

_MAX_ERROR_SNIPPET = 300


@dataclass(slots=True)
class HttpResult:
    response: httpx.Response
    attempts: int
    latency_ms: float

    def json(self) -> Any:
        try:
            return self.response.json()
        except ValueError as exc:
            raise AIUnavailable(
                f"endpoint returned a non-JSON body (HTTP {self.response.status_code})"
            ) from exc


@dataclass(slots=True)
class StreamHandle:
    """An open streaming response. The owning context manager closes it."""

    response: httpx.Response
    attempts: int
    opened_ms: float  # time until response headers arrived


class EndpointClient:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout: float,
        max_concurrency: int,
        max_retries: int = 2,
        breaker: CircuitBreaker | None = None,
        api_key: str | None = None,
        connect_timeout: float = 5.0,
        backoff_base: float = 0.25,
        backoff_max: float = 4.0,
        breaker_timeout_floor: float = 10.0,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        disabled: bool = False,
    ) -> None:
        self.name = name
        # AI_DISABLED_ENDPOINTS: every call fails fast with EndpointDisabled before any I/O (S18).
        self.disabled = disabled
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_concurrency = max(1, max_concurrency)
        self.max_retries = max(0, max_retries)
        self.breaker = breaker or CircuitBreaker(name)
        self.connect_timeout = connect_timeout
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        # Read timeouts only count against the breaker when the attempt had at
        # least this much time: a caller's deliberately short deadline expiring
        # says little about endpoint health.
        self.breaker_timeout_floor = breaker_timeout_floor
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._inflight = 0
        hdrs = {"Accept": "application/json", "User-Agent": "interview-api/ai"}
        if headers:
            hdrs.update(headers)
        if api_key:
            hdrs["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=hdrs,
            timeout=self._timeout_for(timeout),
            limits=httpx.Limits(
                max_connections=max(4, self.max_concurrency * 2),
                max_keepalive_connections=max(2, self.max_concurrency),
                keepalive_expiry=30.0,
            ),
            transport=transport,
            follow_redirects=False,
            trust_env=False,  # local endpoints: never route through a proxy from the environment
        )

    # ----------------------------------------------------------------- state
    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def saturated(self) -> bool:
        return self._inflight >= self.max_concurrency

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()

    # --------------------------------------------------------------- request
    def _ensure_enabled(self) -> None:
        if self.disabled:
            raise EndpointDisabled(f"{self.name}: disabled by AI_DISABLED_ENDPOINTS", endpoint=self.name)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - total budget incl. retries, handed to httpx
        idempotent: bool = True,
        max_retries: int | None = None,
        use_breaker: bool = True,
        acquire_slot: bool = True,
        task: str = "-",
    ) -> HttpResult:
        """Send a request with retries. ``files`` must hold bytes (not file
        objects) so the body can be re-sent on retry."""
        self._ensure_enabled()
        loop = asyncio.get_running_loop()
        budget = self.timeout if timeout is None else timeout
        deadline = loop.time() + max(budget, 0.001)
        retries = self.max_retries if max_retries is None else max(0, max_retries)
        started = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            if use_breaker:
                self.breaker.before_call()
            remaining = deadline - loop.time()
            try:
                if acquire_slot:
                    await self._acquire(remaining)
                else:
                    self._inflight += 1
            except BaseException:
                if use_breaker:
                    self.breaker.on_neutral()
                raise
            attempt_budget = deadline - loop.time()
            error: AIError
            counts = False
            retryable = False
            retry_after: float | None = None
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    data=data,
                    files=files,
                    params=params,
                    headers=headers,
                    timeout=self._timeout_for(attempt_budget),
                )
            except asyncio.CancelledError:
                if use_breaker:
                    self.breaker.on_neutral()
                raise
            except httpx.TimeoutException as exc:
                connect_phase = isinstance(exc, httpx.ConnectTimeout | httpx.PoolTimeout)
                error = AITimeout(
                    f"{self.name}: {type(exc).__name__} after {attempt_budget:.1f}s", endpoint=self.name
                )
                counts = connect_phase or attempt_budget >= min(self.breaker_timeout_floor, self.timeout)
                retryable = connect_phase or idempotent
            except httpx.TransportError as exc:
                error = AIUnavailable(
                    f"{self.name}: {type(exc).__name__}: {exc}"[:_MAX_ERROR_SNIPPET], endpoint=self.name
                )
                counts = True
                retryable = isinstance(exc, httpx.ConnectError) or idempotent
            else:
                if response.is_success:
                    if use_breaker:
                        self.breaker.on_success()
                    return HttpResult(response, attempt, (time.perf_counter() - started) * 1000)
                error = error_for_status(self.name, response.status_code, _snippet(response))
                counts = response.status_code >= 500
                retryable = response.status_code in (408, 429) or response.status_code >= 500
                retry_after = _retry_after(response)
            finally:
                if acquire_slot:
                    self._release()
                else:
                    self._inflight -= 1

            if use_breaker:
                if counts:
                    self.breaker.on_failure(failure_reason(error))
                else:
                    self.breaker.on_neutral()
            if not retryable or attempt > retries:
                if attempt > 1:
                    log.warning(
                        "ai.http.failed", endpoint=self.name, task=task, attempts=attempt, error=str(error)
                    )
                raise error
            delay = self._backoff(attempt, retry_after)
            if loop.time() + delay >= deadline:
                raise error
            log.warning(
                "ai.http.retry",
                endpoint=self.name,
                task=task,
                attempt=attempt,
                reason=failure_reason(error),
                delay_ms=round(delay * 1000),
            )
            await asyncio.sleep(delay)

    # ---------------------------------------------------------------- stream
    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float | None = None,  # noqa: ASYNC109 - connect/read budget handed to httpx
        max_retries: int | None = None,
        task: str = "-",
    ) -> AsyncIterator[StreamHandle]:
        """Open a streaming response. Retries happen only until response
        headers arrive; nothing is retried once the body is being consumed.
        The concurrency slot is held until the context exits, and exiting
        (normally, on error or on cancellation) closes the HTTP stream."""
        self._ensure_enabled()
        handle = await self._open_stream(
            method, path, json=json, timeout=timeout, max_retries=max_retries, task=task
        )
        try:
            yield handle
        finally:
            try:
                await handle.response.aclose()
            finally:
                self._release()

    async def _open_stream(
        self,
        method: str,
        path: str,
        *,
        json: Any,
        timeout: float | None,  # noqa: ASYNC109 - budget handed to httpx
        max_retries: int | None,
        task: str,
    ) -> StreamHandle:
        loop = asyncio.get_running_loop()
        budget = self.timeout if timeout is None else timeout
        deadline = loop.time() + max(budget, 0.001)
        retries = self.max_retries if max_retries is None else max(0, max_retries)
        started = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            self.breaker.before_call()
            try:
                await self._acquire(deadline - loop.time())
            except BaseException:
                self.breaker.on_neutral()
                raise
            attempt_budget = deadline - loop.time()
            retry_after: float | None = None
            try:
                request = self._client.build_request(
                    method, path, json=json, timeout=self._timeout_for(attempt_budget)
                )
                response = await self._client.send(request, stream=True)
            except asyncio.CancelledError:
                self._release()
                self.breaker.on_neutral()
                raise
            except httpx.TimeoutException as exc:
                self._release()
                connect_phase = isinstance(exc, httpx.ConnectTimeout | httpx.PoolTimeout)
                error: AIError = AITimeout(
                    f"{self.name}: {type(exc).__name__} opening stream", endpoint=self.name
                )
                counts = connect_phase or attempt_budget >= min(self.breaker_timeout_floor, self.timeout)
                retryable = connect_phase
            except httpx.TransportError as exc:
                self._release()
                error = AIUnavailable(
                    f"{self.name}: {type(exc).__name__}: {exc}"[:_MAX_ERROR_SNIPPET], endpoint=self.name
                )
                counts = True
                retryable = isinstance(exc, httpx.ConnectError)
            else:
                if response.is_success:
                    self.breaker.on_success()
                    return StreamHandle(response, attempt, (time.perf_counter() - started) * 1000)
                try:
                    await response.aread()
                except httpx.HTTPError:
                    pass
                finally:
                    await response.aclose()
                    self._release()
                error = error_for_status(self.name, response.status_code, _snippet(response))
                counts = response.status_code >= 500
                retryable = response.status_code in (408, 429) or response.status_code >= 500
                retry_after = _retry_after(response)

            if counts:
                self.breaker.on_failure(failure_reason(error))
            else:
                self.breaker.on_neutral()
            if not retryable or attempt > retries:
                raise error
            delay = self._backoff(attempt, retry_after)
            if loop.time() + delay >= deadline:
                raise error
            log.warning(
                "ai.http.retry",
                endpoint=self.name,
                task=task,
                attempt=attempt,
                reason=failure_reason(error),
                delay_ms=round(delay * 1000),
                stream=True,
            )
            await asyncio.sleep(delay)

    def stream_error(self, exc: BaseException) -> AIError:
        """Map an exception raised while consuming a stream body and record
        it against the breaker."""
        if isinstance(exc, AIError):
            return exc
        if isinstance(exc, httpx.TimeoutException):
            self.breaker.on_failure("timeout")
            return AITimeout(f"{self.name}: stream stalled ({type(exc).__name__})", endpoint=self.name)
        self.breaker.on_failure("unavailable")
        return AIUnavailable(
            f"{self.name}: stream broken: {type(exc).__name__}: {exc}"[:_MAX_ERROR_SNIPPET],
            endpoint=self.name,
        )

    # -------------------------------------------------------------- internal
    async def _acquire(self, budget: float) -> None:
        if budget <= 0:
            raise AITimeout(f"{self.name}: no time left to send the request", endpoint=self.name)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), budget)
        except TimeoutError:
            raise AITimeout(
                f"{self.name}: timed out after {budget:.1f}s waiting for one of {self.max_concurrency} slots",
                endpoint=self.name,
            ) from None
        self._inflight += 1

    def _release(self) -> None:
        self._inflight -= 1
        self._semaphore.release()

    def _timeout_for(self, budget: float) -> httpx.Timeout:
        budget = max(budget, 0.001)
        return httpx.Timeout(budget, connect=min(self.connect_timeout, budget))

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        exp = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
        delay = exp / 2 + random.uniform(0, exp / 2)
        if retry_after is not None:
            delay = max(delay, min(retry_after, self.backoff_max * 2))
        return delay


# ------------------------------------------------------------------ helpers


async def gather_or_cancel(*aws: Awaitable[Any]) -> list[Any]:
    """``asyncio.gather`` that cancels the remaining work on the first error
    (no orphaned requests keep an endpoint busy after the caller gave up)."""
    tasks = [asyncio.ensure_future(aw) for aw in aws]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def error_for_status(endpoint: str, status: int, detail: str) -> AIError:
    message = f"{endpoint}: HTTP {status}" + (f": {detail}" if detail else "")
    if status == 408:
        return AITimeout(message, endpoint=endpoint)
    if status == 429 or status >= 500:
        return AIUnavailable(message, endpoint=endpoint)
    return AIRejected(message, endpoint=endpoint)


def failure_reason(exc: BaseException) -> str:
    """Label value for ``interview_model_call_failures_total{reason}``."""
    if isinstance(exc, CircuitOpenError):
        return "circuit_open"
    if isinstance(exc, AITimeout | TimeoutError):
        return "timeout"
    if isinstance(exc, AIUnavailable):
        return "unavailable"
    if isinstance(exc, AIRejected):
        return "rejected"
    if isinstance(exc, AIOutputInvalid):
        return "invalid_output"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "error"


def call_status(exc: BaseException | None) -> ModelCallStatus:
    """Ledger status for a failed call (``None`` -> OK)."""
    if exc is None:
        return ModelCallStatus.OK
    if isinstance(exc, asyncio.CancelledError):
        return ModelCallStatus.CANCELLED
    if isinstance(exc, CircuitOpenError):
        return ModelCallStatus.CIRCUIT_OPEN
    if isinstance(exc, AITimeout | TimeoutError):
        return ModelCallStatus.TIMEOUT
    return ModelCallStatus.ERROR


def _snippet(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - any undecodable body
        try:
            return response.text[:_MAX_ERROR_SNIPPET].strip()
        except Exception:  # noqa: BLE001
            return ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:_MAX_ERROR_SNIPPET]
        if isinstance(err, str):
            return err[:_MAX_ERROR_SNIPPET]
        for key in ("message", "detail"):
            if body.get(key):
                return str(body[key])[:_MAX_ERROR_SNIPPET]
    return str(body)[:_MAX_ERROR_SNIPPET]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
