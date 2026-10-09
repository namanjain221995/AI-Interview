"""Prometheus metrics of the sandbox service (defined once, imported elsewhere).

The API process records the caller-side round trip as
``interview_coding_executions_total`` / ``interview_coding_execution_seconds``;
these ``interview_sandbox_*`` series describe what happens inside the sandbox.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 60.0)
_QUEUE_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0)

EXECUTIONS = Counter(
    "interview_sandbox_executions_total", "Executions by language, mode and final status", ["language", "mode", "status"]
)
EXECUTION_SECONDS = Histogram(
    "interview_sandbox_execution_seconds",
    "Service-side execution time (container lifecycle included, queue wait excluded)",
    ["language", "mode"],
    buckets=_LATENCY_BUCKETS,
)
PHASE_SECONDS = Histogram(
    "interview_sandbox_phase_seconds",
    "Wall time of a runner phase inside the container",
    ["language", "phase"],
    buckets=_LATENCY_BUCKETS,
)
CONTAINER_OVERHEAD_SECONDS = Histogram(
    "interview_sandbox_container_overhead_seconds",
    "Container create/start/teardown overhead (execution time minus runner time)",
    ["language"],
    buckets=_LATENCY_BUCKETS,
)
QUEUE_WAIT_SECONDS = Histogram(
    "interview_sandbox_queue_wait_seconds", "Time spent waiting for an execution slot", buckets=_QUEUE_BUCKETS
)
INFLIGHT = Gauge("interview_sandbox_inflight", "Executions currently running")
QUEUED = Gauge("interview_sandbox_queued", "Requests waiting for an execution slot")
REJECTIONS = Counter(
    "interview_sandbox_rejections_total",
    "Requests refused before execution (auth, replay, size, queue, validation)",
    ["reason"],
)
CONTAINER_FAILURES = Counter(
    "interview_sandbox_container_failures_total",
    "Container-level failures (docker errors, deadline kills, missing runner result)",
    ["reason"],
)
RUNTIME_AVAILABLE = Gauge("interview_sandbox_runtime_available", "1 when the runtime image is present", ["runtime"])
JANITOR_REMOVED = Counter("interview_sandbox_janitor_removed_total", "Stale sandbox containers removed by the janitor")
