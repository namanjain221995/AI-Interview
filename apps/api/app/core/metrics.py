"""Prometheus metrics — the single definition point (see CONTRACTS.md §8).

Import the metric objects from here; never create metrics elsewhere so names
and label sets stay consistent across the api, the worker and dashboards.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client import CONTENT_TYPE_LATEST as PROM_CONTENT_TYPE  # noqa: N811
from prometheus_client import REGISTRY as DEFAULT_REGISTRY

REGISTRY: CollectorRegistry = DEFAULT_REGISTRY

_LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 60.0)
_FAST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0)
_TURN_BUCKETS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0, 15.0)

ACTIVE_SESSIONS = Gauge("interview_active_sessions", "Interview sessions currently live in this process")
WS_CONNECTIONS = Gauge("interview_ws_connections", "Open interview WebSocket connections")

TURN_LATENCY = Histogram(
    "interview_turn_latency_seconds",
    "Candidate end-of-turn to first interviewer audio chunk sent",
    buckets=_TURN_BUCKETS,
)
TIME_TO_FIRST_AUDIO = Histogram(
    "interview_time_to_first_audio_seconds",
    "Interviewer utterance start to first TTS chunk sent",
    buckets=_TURN_BUCKETS,
)
BARGE_INS = Counter("interview_barge_ins_total", "Candidate interruptions of interviewer speech")

LLM_REQUEST_SECONDS = Histogram(
    "interview_llm_request_seconds",
    "LLM request duration",
    ["endpoint", "task", "status"],
    buckets=_LATENCY_BUCKETS,
)
LLM_TTFT_SECONDS = Histogram(
    "interview_llm_ttft_seconds", "LLM time to first token", ["endpoint", "task"], buckets=_FAST_BUCKETS
)
LLM_TOKENS = Counter("interview_llm_tokens_total", "LLM tokens", ["endpoint", "task", "kind"])
MODEL_CALL_FAILURES = Counter(
    "interview_model_call_failures_total", "Failed model calls", ["endpoint", "task", "reason"]
)
STT_LATENCY = Histogram("interview_stt_latency_seconds", "Whisper transcription latency", ["kind"], buckets=_LATENCY_BUCKETS)
TTS_LATENCY = Histogram("interview_tts_latency_seconds", "TTS synthesis latency per chunk", ["provider"], buckets=_LATENCY_BUCKETS)
EMBEDDING_SECONDS = Histogram("interview_embedding_seconds", "Embedding request latency", buckets=_FAST_BUCKETS)
RERANK_SECONDS = Histogram("interview_rerank_seconds", "Reranker request latency", buckets=_FAST_BUCKETS)
OCR_SECONDS = Histogram("interview_ocr_seconds", "OCR latency per page", buckets=_LATENCY_BUCKETS)

CODING_EXECUTIONS = Counter("interview_coding_executions_total", "Sandbox executions", ["language", "status"])
CODING_EXECUTION_SECONDS = Histogram(
    "interview_coding_execution_seconds", "Sandbox execution round trip", ["language"], buckets=_LATENCY_BUCKETS
)

ERRORS = Counter("interview_errors_total", "Unexpected errors by component", ["component"])
QUEUE_DEPTH = Gauge("interview_queue_depth", "Pending messages per job stream", ["queue"])
JOBS = Counter("interview_jobs_total", "Background jobs processed", ["type", "status"])
JOB_SECONDS = Histogram("interview_job_seconds", "Background job duration", ["type"], buckets=_LATENCY_BUCKETS)
DB_QUERY_SECONDS = Histogram("interview_db_query_seconds", "Database operation latency", ["operation"], buckets=_FAST_BUCKETS)
INTEGRITY_EVENTS = Counter("interview_integrity_events_total", "Integrity events recorded", ["type", "source"])

HTTP_REQUESTS = Counter("interview_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_REQUEST_SECONDS = Histogram(
    "interview_http_request_seconds", "HTTP request latency", ["method", "route"], buckets=_FAST_BUCKETS
)

# Platform additions (auth, rate limiting, worker). Additive: no existing name changed.
AUTH_EVENTS = Counter(
    "interview_auth_events_total",
    "Authentication events (login_succeeded, login_failed, account_locked, refresh, refresh_reuse_detected, logout)",
    ["event"],
)
RATE_LIMITED = Counter("interview_rate_limited_total", "Requests rejected by a rate limit", ["scope"])
JOBS_IN_FLIGHT = Gauge("interview_jobs_in_flight", "Background jobs currently executing in this worker", ["type"])

CIRCUIT_BREAKER_STATE = Gauge(
    "interview_circuit_breaker_state", "Circuit state per endpoint (0 closed, 1 half-open, 2 open)", ["endpoint"]
)
MODEL_CALL_LEDGER_DROPPED = Counter(
    "interview_model_call_ledger_dropped_total", "model_calls ledger records dropped (queue full or closed)"
)
OPEN_LINK_REGISTRATIONS = Counter(
    "interview_open_link_registrations_total",
    "Open interview link registration attempts by outcome "
    "(registered, resumed, already_completed, already_registered, already_invited, blocked, rejected, unavailable)",
    ["outcome"],
)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), PROM_CONTENT_TYPE
