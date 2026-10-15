"""Prometheus metrics of the TTS service (prefix ``interview_tts_``), defined once here."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest

_SYNTH_BUCKETS = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0)
_REQUEST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0)
_RTF_BUCKETS = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0)

SYNTHESIS_SECONDS = Histogram(
    "interview_tts_synthesis_seconds",
    "Model synthesis wall time per request (cache misses)",
    buckets=_SYNTH_BUCKETS,
)
REAL_TIME_FACTOR = Histogram(
    "interview_tts_real_time_factor", "Synthesis seconds per second of audio produced", buckets=_RTF_BUCKETS
)
AUDIO_SECONDS = Counter("interview_tts_audio_seconds_total", "Seconds of audio synthesised (cache misses)")
CHARACTERS = Counter(
    "interview_tts_characters_total", "Characters of normalised text served", ["source"]
)  # source: synthesized | cache
REQUESTS = Counter("interview_tts_requests_total", "HTTP requests", ["endpoint", "status"])
REQUEST_SECONDS = Histogram(
    "interview_tts_request_seconds", "HTTP request latency", ["endpoint"], buckets=_REQUEST_BUCKETS
)
CACHE_HITS = Counter("interview_tts_cache_hits_total", "Synthesis cache hits")
CACHE_MISSES = Counter("interview_tts_cache_misses_total", "Synthesis cache misses")
CACHE_ENTRIES = Gauge("interview_tts_cache_entries", "Entries in the synthesis cache")
CACHE_BYTES = Gauge("interview_tts_cache_bytes", "Approximate bytes held by the synthesis cache")
QUEUE_DEPTH = Gauge("interview_tts_queue_depth", "Synthesis jobs waiting for a worker")
INFLIGHT = Gauge("interview_tts_inflight", "Synthesis jobs running")
ERRORS = Counter("interview_tts_errors_total", "Failed requests by reason", ["reason"])
READY = Gauge("interview_tts_ready", "1 when the model is loaded and warmed up")
MODEL_LOAD_SECONDS = Gauge("interview_tts_model_load_seconds", "Model load plus warm-up time at startup")
KEEPWARM = Counter("interview_tts_keepwarm_total", "Idle keep-warm syntheses (one per session each time)")


def render_latest() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
