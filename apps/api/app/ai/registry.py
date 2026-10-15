"""Process-wide access to the AI services.

* ``await init_ai()`` at startup (API lifespan, worker boot) builds
  :class:`RealAIServices` from settings and starts the model-call ledger.
* ``get_ai()`` returns the current services. If nothing was initialised it
  builds them lazily, except under ``APP_ENV=test`` where it installs
  :class:`~app.ai.mock.MockAIServices` so a forgotten fixture can never send
  test traffic to the shared production models.
* ``set_ai(services)`` swaps the services (tests) and returns the previous.
* ``await shutdown_ai()`` flushes the ledger and closes every connection.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from pydantic import SecretStr

from app.ai.base import AIError, AIServices
from app.ai.breaker import CircuitBreaker, CircuitState
from app.ai.clients.embeddings import EmbeddingCache, OpenAIEmbeddingsClient
from app.ai.clients.llm import OpenAICompatibleLLM
from app.ai.clients.ocr import UnlimitedOcrClient
from app.ai.clients.reranker import QwenRerankerClient, format_document, format_query
from app.ai.clients.stt import WhisperSttClient
from app.ai.clients.tts import BrowserTtsProvider, MockTtsProvider, create_tts_provider
from app.ai.http import EndpointClient
from app.ai.ledger import LedgerSink, ModelCallLedger
from app.ai.router import ModelRouter
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Concurrency for endpoints without a dedicated setting.
EMBEDDING_MAX_CONCURRENCY = 8
OCR_MAX_CONCURRENCY = 2
TTS_MAX_CONCURRENCY = 8
HEALTH_TIMEOUT_SECONDS = 3.0
SLOW_PROBE_MS = 1500.0


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def _whisper_root(base_url: str) -> str:
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url


class RealAIServices:
    """Clients for the local inference endpoints, wired from settings."""

    def __init__(
        self,
        settings: Settings,
        *,
        redis_client: Any | None = None,
        ledger: LedgerSink | None = None,
    ) -> None:
        self.settings = settings
        self.breakers: dict[str, CircuitBreaker] = {}
        retries = settings.llm_max_retries

        def endpoint(
            name: str, base_url: str, *, timeout: float, concurrency: int, api_key: str | None = None
        ) -> EndpointClient:
            breaker = CircuitBreaker(
                name,
                failure_threshold=settings.circuit_breaker_failure_threshold,
                reset_seconds=settings.circuit_breaker_reset_seconds,
            )
            self.breakers[name] = breaker
            return EndpointClient(
                name,
                base_url,
                timeout=timeout,
                max_concurrency=concurrency,
                max_retries=retries,
                breaker=breaker,
                api_key=api_key,
                disabled=settings.ai_endpoint_disabled(name),
            )

        self.ledger: LedgerSink = ledger or ModelCallLedger(log_payloads=settings.model_call_log_payloads)
        self.endpoints: dict[str, EndpointClient] = {
            "main": endpoint(
                "main",
                settings.main_llm_base_url,
                timeout=settings.main_llm_timeout_seconds,
                concurrency=settings.main_llm_max_concurrency,
                api_key=_secret(settings.main_llm_api_key),
            ),
            "vision": endpoint(
                "vision",
                settings.vision_llm_base_url,
                timeout=settings.vision_llm_timeout_seconds,
                concurrency=settings.vision_llm_max_concurrency,
                api_key=_secret(settings.vision_llm_api_key),
            ),
            "embedding": endpoint(
                "embedding",
                settings.embedding_base_url,
                timeout=settings.embedding_timeout_seconds,
                concurrency=EMBEDDING_MAX_CONCURRENCY,
            ),
            "reranker": endpoint(
                "reranker",
                settings.reranker_base_url,
                timeout=settings.reranker_timeout_seconds,
                concurrency=settings.reranker_max_concurrency,
            ),
            "ocr": endpoint(
                "ocr",
                settings.ocr_base_url,
                timeout=settings.ocr_timeout_seconds,
                concurrency=OCR_MAX_CONCURRENCY,
            ),
            "tts": endpoint(
                "tts",
                settings.tts_base_url,
                timeout=settings.tts_timeout_seconds,
                concurrency=TTS_MAX_CONCURRENCY,
            ),
        }
        self.whisper_replicas: list[EndpointClient] = [
            endpoint(
                "whisper" if index == 0 else f"whisper-{index + 1}",
                url,
                timeout=settings.whisper_timeout_seconds,
                concurrency=settings.stt_max_concurrency,
            )
            for index, url in enumerate(dict.fromkeys(settings.whisper_endpoints))
        ]
        self.router = ModelRouter(
            breakers={"main": self.breakers["main"], "vision": self.breakers["vision"]},
            overrides=settings.model_routing_overrides,
        )
        self.llm = OpenAICompatibleLLM(
            endpoints={"main": self.endpoints["main"], "vision": self.endpoints["vision"]},
            models={"main": settings.main_llm_model, "vision": settings.vision_llm_model},
            router=self.router,
            ledger=self.ledger,
        )
        self._redis = redis_client
        cache = (
            EmbeddingCache(redis_client, dimensions=settings.embedding_dimensions)
            if redis_client is not None
            else None
        )
        self.embeddings = OpenAIEmbeddingsClient(
            self.endpoints["embedding"],
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            cache=cache,
            ledger=self.ledger,
        )
        self.reranker = QwenRerankerClient(
            self.endpoints["reranker"], model=settings.reranker_model, ledger=self.ledger
        )
        self.stt = WhisperSttClient(
            self.whisper_replicas,
            model=settings.whisper_model,
            language=settings.whisper_language or None,
            max_concurrency=settings.stt_max_concurrency,
            timeout=settings.whisper_timeout_seconds,
            ledger=self.ledger,
            replica_slots=[settings.whisper_base_url_parallelism],
            send_prompt=settings.whisper_send_prompt,
        )
        self.ocr = UnlimitedOcrClient(
            self.endpoints["ocr"], model=settings.ocr_model, prompt=settings.ocr_prompt, ledger=self.ledger
        )
        self.tts = create_tts_provider(settings, http=self.endpoints["tts"], ledger=self.ledger)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if isinstance(self.ledger, ModelCallLedger):
            await self.ledger.start()

    async def aclose(self) -> None:
        if isinstance(self.ledger, ModelCallLedger):
            await self.ledger.aclose()
        clients = [*self.endpoints.values(), *self.whisper_replicas]
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
        if self._redis is not None:
            closer = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
            if closer is not None:
                try:
                    await closer()
                except Exception as exc:  # noqa: BLE001
                    log.warning("ai.redis_close_failed", error=str(exc))

    # --------------------------------------------------------------- health
    async def health(self) -> dict[str, dict[str, Any]]:
        """Probe every endpoint concurrently (short timeouts, no retries,
        bypassing the breakers) -> {name: {status, latency_ms, detail}}."""
        probes: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
            "main": lambda: self._probe_models(self.endpoints["main"]),
            "vision": lambda: self._probe_models(self.endpoints["vision"]),
            "embedding": lambda: self._probe_models(self.endpoints["embedding"]),
            "reranker": self._probe_reranker,
            "whisper": self._probe_whisper,
            "ocr": lambda: self._probe_models(self.endpoints["ocr"]),
            "tts": self._probe_tts,
        }
        disabled = {name for name in probes if name in self.endpoints and self.endpoints[name].disabled}
        probes = {name: probe for name, probe in probes.items() if name not in disabled}
        results = await asyncio.gather(*(probe() for probe in probes.values()), return_exceptions=True)
        report: dict[str, dict[str, Any]] = {
            name: {"status": "disabled", "latency_ms": None, "detail": "disabled by AI_DISABLED_ENDPOINTS"}
            for name in disabled
        }
        for name, result in zip(probes, results, strict=True):
            if isinstance(result, BaseException):
                report[name] = {
                    "status": "down",
                    "latency_ms": None,
                    "detail": f"{type(result).__name__}: {result}"[:200],
                }
            else:
                report[name] = result
        return report

    @staticmethod
    def _status(client: EndpointClient, latency_ms: float, detail: str) -> dict[str, Any]:
        circuit = client.breaker.state
        status = "ok"
        if circuit is not CircuitState.CLOSED:
            status, detail = "degraded", f"{detail}; circuit {circuit.value}"
        elif latency_ms > SLOW_PROBE_MS:
            status, detail = "degraded", f"{detail}; slow probe"
        return {
            "status": status,
            "latency_ms": round(latency_ms, 1),
            "detail": detail,
            "circuit": circuit.value,
        }

    @staticmethod
    def _down(client: EndpointClient, started: float, exc: BaseException) -> dict[str, Any]:
        return {
            "status": "down",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"{type(exc).__name__}: {exc}"[:200],
            "circuit": client.breaker.state.value,
        }

    async def _probe_models(self, client: EndpointClient) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = await client.request(
                "GET",
                "/models",
                timeout=HEALTH_TIMEOUT_SECONDS,
                max_retries=0,
                use_breaker=False,
                acquire_slot=False,
                task="health",
            )
            data = result.json()
        except AIError as exc:
            return self._down(client, started, exc)
        models = (
            [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
            if isinstance(data, dict)
            else []
        )
        return self._status(
            client, (time.perf_counter() - started) * 1000, ", ".join(str(m) for m in models) or "reachable"
        )

    async def _probe_reranker(self) -> dict[str, Any]:
        client = self.endpoints["reranker"]
        started = time.perf_counter()
        body = {
            "model": self.settings.reranker_model,
            "text_1": format_query("health"),
            "text_2": [format_document("health")],
        }
        try:
            result = await client.request(
                "POST",
                "/score",
                json=body,
                timeout=HEALTH_TIMEOUT_SECONDS,
                max_retries=0,
                use_breaker=False,
                acquire_slot=False,
                task="health",
            )
            data = result.json()
            score = data["data"][0]["score"]
        except AIError as exc:
            return self._down(client, started, exc)
        except (KeyError, IndexError, TypeError) as exc:
            return self._down(client, started, exc)
        return self._status(client, (time.perf_counter() - started) * 1000, f"score ok ({float(score):.3f})")

    async def _probe_whisper(self) -> dict[str, Any]:
        async def one(client: EndpointClient) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                result = await client.request(
                    "GET",
                    _whisper_root(client.base_url) + "/health",
                    timeout=HEALTH_TIMEOUT_SECONDS,
                    max_retries=0,
                    use_breaker=False,
                    acquire_slot=False,
                    task="health",
                )
                data = result.json()
            except AIError:
                return await self._probe_models(client)
            report = self._status(
                client,
                (time.perf_counter() - started) * 1000,
                str(data.get("model", "reachable")) if isinstance(data, dict) else "reachable",
            )
            if isinstance(data, dict) and data.get("ready") is False:
                report["status"], report["detail"] = "degraded", f"{report['detail']}; not ready"
            return report

        reports = await asyncio.gather(*(one(c) for c in self.whisper_replicas))
        if len(reports) == 1:
            return reports[0]
        statuses = {r["status"] for r in reports}
        overall = "ok" if statuses == {"ok"} else ("down" if statuses == {"down"} else "degraded")
        latencies = [r["latency_ms"] for r in reports if r.get("latency_ms") is not None]
        return {
            "status": overall,
            "latency_ms": max(latencies) if latencies else None,
            "detail": "; ".join(
                f"{c.base_url}: {r['status']}" for c, r in zip(self.whisper_replicas, reports, strict=True)
            ),
            "replicas": reports,
        }

    async def _probe_tts(self) -> dict[str, Any]:
        provider = self.tts
        if isinstance(provider, BrowserTtsProvider | MockTtsProvider):
            return {"status": "ok", "latency_ms": 0.0, "detail": f"{provider.name} provider (no server TTS)"}
        started = time.perf_counter()
        try:
            healthy = await asyncio.wait_for(provider.health(), HEALTH_TIMEOUT_SECONDS)
        except (TimeoutError, AIError) as exc:
            return self._down(self.endpoints["tts"], started, exc)
        latency = (time.perf_counter() - started) * 1000
        if not healthy:
            return {
                "status": "down",
                "latency_ms": round(latency, 1),
                "detail": f"{provider.name} health check failed",
            }
        return self._status(self.endpoints["tts"], latency, provider.name)


# ============================================================================
# Registry
# ============================================================================

_services: AIServices | None = None
_lock = threading.Lock()


def _redis_for(settings: Settings) -> Any | None:
    try:
        from redis.asyncio import Redis

        return Redis.from_url(
            settings.redis_url,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            max_connections=16,
            health_check_interval=30,
        )
    except Exception as exc:  # noqa: BLE001 - the embedding cache is optional
        log.warning("ai.embedding_cache.disabled", error=f"{type(exc).__name__}: {exc}"[:200])
        return None


def build_real_ai(settings: Settings | None = None, *, with_cache: bool = True) -> RealAIServices:
    settings = settings or get_settings()
    return RealAIServices(settings, redis_client=_redis_for(settings) if with_cache else None)


def get_ai() -> AIServices:
    global _services
    if _services is None:
        with _lock:
            if _services is None:
                settings = get_settings()
                if settings.is_test:
                    from app.ai.mock import MockAIServices

                    log.warning(
                        "ai.registry.mock_in_tests", hint="use the mock_ai fixture or set_ai() explicitly"
                    )
                    _services = cast(AIServices, MockAIServices(dimensions=settings.embedding_dimensions))
                else:
                    _services = cast(AIServices, build_real_ai(settings))
    return _services


async def init_ai(settings: Settings | None = None) -> AIServices:
    """Build (if needed) and start the real services. Idempotent."""
    global _services
    if _services is None:
        _services = cast(AIServices, build_real_ai(settings))
    if isinstance(_services, RealAIServices):
        await _services.start()
    log.info("ai.initialised", services=type(_services).__name__)
    return _services


def set_ai(services: AIServices | None) -> AIServices | None:
    global _services
    previous, _services = _services, services
    return previous


async def shutdown_ai() -> None:
    global _services
    services, _services = _services, None
    if services is not None:
        await services.aclose()
