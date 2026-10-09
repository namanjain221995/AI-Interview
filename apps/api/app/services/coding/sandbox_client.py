"""HTTP client for the code execution sandbox (``services/sandbox``).

Wire contract: :mod:`app.services.coding.contracts` (``ExecuteRequest`` ->
``ExecuteResponse``), authenticated with
``X-Sandbox-Signature = hex(HMAC-SHA256(secret, f"{timestamp}.{raw_body}"))``.

Behaviour
    * Every attempt is signed with a fresh timestamp. The sandbox accepts a
      signature only once, so retries also carry a distinct ``request_id``
      (``<id>.r2``, ``<id>.r3``): identical bodies signed within the same
      second would otherwise be refused as replays.
    * Retries happen only when the sandbox certainly did not execute the
      request: connection failures (connect / pool / write errors) and
      ``503`` responses that carry ``Retry-After`` (queue full, queue timeout,
      runtime unavailable). A read timeout or a dropped response is never
      retried, because the code may already have run.
    * The HTTP timeout covers the sandbox queue wait, compilation and both
      harness processes (visible + hidden tests):
      ``15 s + compile limit + 2 x time limit + 10 s`` (never below
      ``SANDBOX_TIMEOUT_SECONDS``).
    * A circuit breaker (``interview_circuit_breaker_state{endpoint="sandbox"}``)
      fails fast while the sandbox is unhealthy.
    * Metrics: ``interview_coding_executions_total{language,status}`` with the
      ``RunStatus`` value for candidate outcomes and ``sandbox_error`` for
      platform faults (transport, 5xx, circuit open, malformed responses), and
      ``interview_coding_execution_seconds{language}`` per call.

Platform faults raise :class:`SandboxFault`; :meth:`SandboxClient.execute_safe`
turns them into an ``ExecuteResponse`` with ``status=error`` and the
candidate-safe message :data:`SANDBOX_BUSY_MESSAGE` so callers never show
infrastructure details to candidates.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from app.ai.breaker import CircuitBreaker
from app.core.config import Settings, get_settings
from app.core.lifecycle import on_shutdown
from app.core.logging import get_logger
from app.core.metrics import CODING_EXECUTION_SECONDS, CODING_EXECUTIONS
from app.domain.enums import RunStatus
from app.services.coding.contracts import ExecuteRequest, ExecuteResponse

log = get_logger(__name__)

TIMESTAMP_HEADER = "X-Sandbox-Timestamp"
SIGNATURE_HEADER = "X-Sandbox-Signature"
EXECUTE_PATH = "/v1/execute"
HEALTH_PATH = "/health"

#: Shown to candidates for every platform fault (never infrastructure details).
SANDBOX_BUSY_MESSAGE = "The code runner is busy, please try again"
#: Metric status label for platform faults.
SANDBOX_ERROR_STATUS = "sandbox_error"

QUEUE_WAIT_SECONDS = 15.0  # sandbox SANDBOX_QUEUE_TIMEOUT_SECONDS
OVERHEAD_SECONDS = 10.0  # container lifecycle + transfer
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_RETRY_AFTER = 5.0
_RETRYABLE_TRANSPORT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.WriteError,
    httpx.WriteTimeout,
)
_MAX_LOG_SNIPPET = 200


class SandboxFault(Exception):
    """The sandbox could not execute the request (never the candidate's fault)."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        status_code: int | None = None,
        retry_after: float | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.retry_after = retry_after
        self.attempts = attempts

    def as_meta(self) -> dict[str, Any]:
        return {
            "platform_fault": True,
            "reason": self.reason,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "detail": str(self)[:_MAX_LOG_SNIPPET],
        }


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """``hex(HMAC-SHA256(secret, f"{timestamp}.{body}"))`` (sandbox auth)."""
    return hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


def compute_timeout(request: ExecuteRequest, floor: float = 0.0) -> float:
    """HTTP timeout for one execution: queue wait + compile + 2 x run + overhead."""
    budget = (
        QUEUE_WAIT_SECONDS
        + request.compile_time_limit_ms / 1000.0
        + 2 * request.time_limit_ms / 1000.0
        + OVERHEAD_SECONDS
    )
    return max(floor, budget)


def platform_error_response(request: ExecuteRequest, fault: SandboxFault) -> ExecuteResponse:
    """Candidate-safe ``ExecuteResponse`` for a platform fault."""
    total = len(request.tests) if request.mode == "tests" else 0
    return ExecuteResponse(
        request_id=request.request_id,
        status=RunStatus.ERROR,
        stderr=SANDBOX_BUSY_MESSAGE,
        total=total,
        meta=fault.as_meta(),
    )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}"
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("code") or f"http_{response.status_code}")[:60]
    return f"http_{response.status_code}"


class SandboxClient:
    """Signed, retrying, circuit-broken client for ``POST /v1/execute``."""

    def __init__(
        self,
        base_url: str,
        secret: str | bytes,
        *,
        timeout_floor: float = 0.0,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
        connect_timeout: float = 3.0,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        backoff_base: float = 0.25,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.base_url = base_url.rstrip("/")
        self._secret = secret.encode() if isinstance(secret, str) else secret
        if len(self._secret) < 16:
            raise ValueError("sandbox shared secret is too short")
        self.timeout_floor = timeout_floor
        self.max_attempts = max_attempts
        self.max_retry_after = max_retry_after
        self.connect_timeout = connect_timeout
        self.breaker = breaker or CircuitBreaker("sandbox")
        self._clock = clock
        self._sleep = sleep
        self._backoff_base = backoff_base
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=httpx.Timeout(timeout_floor or 60.0, connect=connect_timeout),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
            headers={"User-Agent": "interview-api/coding"},
        )

    # ------------------------------------------------------------------ api
    async def execute(self, request: ExecuteRequest) -> ExecuteResponse:
        """Execute ``request``; raises :class:`SandboxFault` on platform faults."""
        language = request.language.value
        started = time.perf_counter()
        try:
            response = await self._execute(request)
        except SandboxFault as fault:
            CODING_EXECUTIONS.labels(language=language, status=SANDBOX_ERROR_STATUS).inc()
            CODING_EXECUTION_SECONDS.labels(language=language).observe(time.perf_counter() - started)
            log.warning(
                "coding.sandbox.fault",
                request_id=request.request_id,
                language=language,
                reason=fault.reason,
                status_code=fault.status_code,
                attempts=fault.attempts,
            )
            raise
        elapsed = time.perf_counter() - started
        CODING_EXECUTIONS.labels(language=language, status=response.status.value).inc()
        CODING_EXECUTION_SECONDS.labels(language=language).observe(elapsed)
        log.info(
            "coding.sandbox.executed",
            request_id=request.request_id,
            language=language,
            mode=request.mode,
            status=response.status.value,
            passed=response.passed,
            total=response.total,
            duration_ms=round(elapsed * 1000, 1),
        )
        return response

    async def execute_safe(self, request: ExecuteRequest) -> tuple[ExecuteResponse, SandboxFault | None]:
        """Like :meth:`execute` but maps platform faults to a candidate-safe
        ``RunStatus.ERROR`` response (``meta.platform_fault = true``)."""
        try:
            return await self.execute(request), None
        except SandboxFault as fault:
            return platform_error_response(request, fault), fault

    async def health(self) -> dict[str, Any]:
        """``GET /health`` (unauthenticated); raises :class:`SandboxFault` when unreachable."""
        try:
            response = await self._client.get(HEALTH_PATH, timeout=5.0)
        except httpx.HTTPError as exc:
            raise SandboxFault(
                f"sandbox health check failed: {type(exc).__name__}", reason="transport"
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise SandboxFault("sandbox health returned a non-JSON body", reason="bad_response") from exc
        return body if isinstance(body, dict) else {"status": "unknown"}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- internals
    def _signed(self, request: ExecuteRequest, attempt: int) -> tuple[bytes, dict[str, str]]:
        if attempt > 1:
            request = request.model_copy(update={"request_id": f"{request.request_id}.r{attempt}"})
        body = request.model_dump_json().encode("utf-8")
        timestamp = str(int(self._clock()))
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(self._secret, timestamp, body),
        }
        return body, headers

    def _backoff(self, attempt: int) -> float:
        return min(2.0, self._backoff_base * (2 ** (attempt - 1)))

    async def _execute(self, request: ExecuteRequest) -> ExecuteResponse:
        timeout = compute_timeout(request, self.timeout_floor)
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            attempt += 1
            try:
                self.breaker.before_call()
            except Exception as exc:  # CircuitOpenError
                raise SandboxFault("sandbox circuit open", reason="circuit_open", attempts=attempt) from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                self.breaker.on_neutral()
                raise SandboxFault("sandbox time budget exhausted", reason="timeout", attempts=attempt)
            body, headers = self._signed(request, attempt)
            try:
                response = await self._client.post(
                    EXECUTE_PATH,
                    content=body,
                    headers=headers,
                    timeout=httpx.Timeout(remaining, connect=min(self.connect_timeout, remaining)),
                )
            except _RETRYABLE_TRANSPORT as exc:
                # The request never reached the executor: safe to retry.
                self.breaker.on_failure("transport")
                if attempt < self.max_attempts and deadline - time.monotonic() > 1.0:
                    await self._sleep(self._backoff(attempt))
                    continue
                raise SandboxFault(
                    f"sandbox unreachable: {type(exc).__name__}", reason="transport", attempts=attempt
                ) from exc
            except httpx.TimeoutException as exc:
                # Read timeout: the code may have run - never re-run it.
                self.breaker.on_failure("timeout")
                raise SandboxFault(
                    "sandbox did not answer in time", reason="timeout", attempts=attempt
                ) from exc
            except httpx.HTTPError as exc:
                self.breaker.on_failure("transport")
                raise SandboxFault(
                    f"sandbox transport error: {type(exc).__name__}", reason="transport", attempts=attempt
                ) from exc

            status = response.status_code
            if status == 200:
                try:
                    parsed = ExecuteResponse.model_validate_json(response.content)
                except (ValidationError, ValueError) as exc:
                    self.breaker.on_failure("bad_response")
                    raise SandboxFault(
                        "sandbox returned a malformed response",
                        reason="bad_response",
                        status_code=200,
                        attempts=attempt,
                    ) from exc
                self.breaker.on_success()
                return parsed

            code = _error_code(response)
            if status == 503:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                self.breaker.on_failure(code)
                if (
                    retry_after is not None
                    and attempt < self.max_attempts
                    and retry_after <= self.max_retry_after
                    and deadline - time.monotonic() > retry_after + 1.0
                ):
                    log.info(
                        "coding.sandbox.retry",
                        request_id=request.request_id,
                        attempt=attempt,
                        code=code,
                        retry_after=retry_after,
                    )
                    await self._sleep(max(retry_after, self._backoff(attempt)))
                    continue
                raise SandboxFault(
                    f"sandbox unavailable ({code})",
                    reason=code,
                    status_code=503,
                    retry_after=retry_after,
                    attempts=attempt,
                )
            if status >= 500:
                self.breaker.on_failure(f"http_{status}")
                raise SandboxFault(
                    f"sandbox error ({code})", reason=code, status_code=status, attempts=attempt
                )
            if status == 401:
                # Shared secret mismatch or clock skew: an operator problem.
                self.breaker.on_failure("unauthorized")
                raise SandboxFault(
                    "sandbox rejected the signature", reason="unauthorized", status_code=401, attempts=attempt
                )
            # 413 / 422 / other 4xx: the request itself was refused; not a health signal.
            self.breaker.on_neutral()
            raise SandboxFault(
                f"sandbox refused the request ({code})", reason=code, status_code=status, attempts=attempt
            )


# --------------------------------------------------------------- singleton

_client: SandboxClient | None = None


def build_sandbox_client(settings: Settings | None = None) -> SandboxClient:
    settings = settings or get_settings()
    return SandboxClient(
        settings.sandbox_base_url,
        settings.sandbox_shared_secret.get_secret_value(),
        timeout_floor=settings.sandbox_timeout_seconds,
        breaker=CircuitBreaker(
            "sandbox",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            reset_seconds=settings.circuit_breaker_reset_seconds,
        ),
    )


def get_sandbox_client() -> SandboxClient:
    """Process-wide client (created lazily from settings)."""
    global _client
    if _client is None:
        _client = build_sandbox_client()
    return _client


def set_sandbox_client(client: Any | None) -> Any | None:
    """Swap the process-wide client (tests); returns the previous one."""
    global _client
    previous = _client
    _client = client
    return previous


@on_shutdown("coding.sandbox_client", processes=("api", "worker"))
async def close_sandbox_client() -> None:
    global _client
    client, _client = _client, None
    if client is not None and hasattr(client, "aclose"):
        await client.aclose()
