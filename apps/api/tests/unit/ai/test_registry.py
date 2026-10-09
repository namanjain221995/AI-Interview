from __future__ import annotations

import httpx
import pytest
import respx

from app.ai import registry
from app.ai.base import AIServices
from app.ai.clients.tts import BrowserTtsProvider, KokoroTtsProvider
from app.ai.ledger import ModelCallLedger, ModelCallRecord
from app.ai.mock import MockAIServices
from app.ai.registry import RealAIServices, get_ai, init_ai, set_ai, shutdown_ai
from app.core.config import get_settings
from app.domain.enums import ModelTask

URLS = {
    "main_llm_base_url": "http://main.test/v1",
    "vision_llm_base_url": "http://vision.test/v1",
    "embedding_base_url": "http://emb.test/v1",
    "reranker_base_url": "http://rerank.test",
    "whisper_base_url": "http://whisper.test/v1",
    "whisper_extra_base_urls": [],
    "ocr_base_url": "http://ocr.test/v1",
    "tts_base_url": "http://tts.test",
    "tts_provider": "kokoro",
    "model_routing_overrides": {"answer_assessment": "vision"},
}


@pytest.fixture
async def services():
    settings = get_settings().model_copy(update=URLS)
    written: list[list[dict]] = []

    async def writer(rows: list[dict]) -> None:
        written.append(rows)

    svc = RealAIServices(
        settings, redis_client=None, ledger=ModelCallLedger(writer=writer, flush_interval=0.05)
    )
    svc.written = written  # type: ignore[attr-defined]
    yield svc
    await svc.aclose()


def mock_all_healthy(mock: respx.MockRouter) -> None:
    for base, model in (
        ("http://main.test/v1", "Qwen/Qwen3.6-35B-A3B-NVFP4"),
        ("http://vision.test/v1", "Qwen/Qwen3-VL-8B-Instruct-FP8"),
        ("http://emb.test/v1", "Qwen/Qwen3-Embedding-0.6B"),
        ("http://ocr.test/v1", "baidu/Unlimited-OCR"),
    ):
        mock.get(f"{base}/models").respond(200, json={"data": [{"id": model}]})
    mock.post("http://rerank.test/score").respond(200, json={"data": [{"index": 0, "score": 0.42}]})
    mock.get("http://whisper.test/health").respond(
        200, json={"ready": True, "model": "openai/whisper-large-v3"}
    )
    mock.get("http://tts.test/health").respond(200, json={"status": "ok"})


async def test_real_services_wiring(services: RealAIServices) -> None:
    assert isinstance(services, AIServices)
    assert services.endpoints["main"].max_concurrency == services.settings.main_llm_max_concurrency
    assert services.endpoints["reranker"].max_concurrency == services.settings.reranker_max_concurrency
    assert services.whisper_replicas[0].base_url == "http://whisper.test/v1"
    assert services.router.primary(ModelTask.ANSWER_ASSESSMENT) == "vision"  # MODEL_ROUTING_OVERRIDES
    assert isinstance(services.tts, KokoroTtsProvider)
    assert services.embeddings.dimensions == 1024
    assert set(services.breakers) >= {"main", "vision", "embedding", "reranker", "whisper", "ocr", "tts"}


async def test_health_all_ok(services: RealAIServices) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock_all_healthy(mock)
        report = await services.health()
    assert set(report) == {"main", "vision", "embedding", "reranker", "whisper", "ocr", "tts"}
    assert all(entry["status"] == "ok" for entry in report.values()), report
    assert report["main"]["detail"] == "Qwen/Qwen3.6-35B-A3B-NVFP4"
    assert report["whisper"]["detail"] == "openai/whisper-large-v3"
    assert report["reranker"]["detail"].startswith("score ok")


async def test_health_reports_down_and_degraded(services: RealAIServices) -> None:
    services.breakers["vision"].on_failure()
    for _ in range(services.breakers["vision"].failure_threshold):
        services.breakers["vision"].on_failure()
    with respx.mock(assert_all_called=False) as mock:
        mock_all_healthy(mock)
        mock.get("http://main.test/v1/models").mock(side_effect=httpx.ConnectError("refused"))
        mock.get("http://whisper.test/health").respond(200, json={"ready": False, "model": "w"})
        mock.get("http://tts.test/health").respond(503)
        report = await services.health()
    assert report["main"]["status"] == "down" and "ConnectError" in report["main"]["detail"]
    assert report["vision"]["status"] == "degraded" and "circuit open" in report["vision"]["detail"]
    assert report["whisper"]["status"] == "degraded"
    assert report["tts"]["status"] == "down"
    assert report["embedding"]["status"] == "ok"


async def test_whisper_health_falls_back_to_models(services: RealAIServices) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock_all_healthy(mock)
        mock.get("http://whisper.test/health").respond(404)
        mock.get("http://whisper.test/v1/models").respond(200, json={"data": [{"id": "whisper"}]})
        report = await services.health()
    assert report["whisper"]["status"] == "ok"


async def test_browser_tts_health_is_ok() -> None:
    settings = get_settings().model_copy(update={**URLS, "tts_provider": "browser"})
    svc = RealAIServices(settings, redis_client=None, ledger=ModelCallLedger(writer=lambda rows: None))  # type: ignore[arg-type]
    try:
        assert isinstance(svc.tts, BrowserTtsProvider)
        assert (await svc._probe_tts())["status"] == "ok"
    finally:
        await svc.aclose()


async def test_set_get_init_shutdown(services: RealAIServices) -> None:
    previous = set_ai(services)
    try:
        assert get_ai() is services
        assert await init_ai() is services
        services.ledger.record(ModelCallRecord(task="t", endpoint="main", model="m", status="ok"))
        await shutdown_ai()
        assert services.written  # type: ignore[attr-defined]  # flushed on shutdown
        assert registry._services is None
    finally:
        set_ai(previous)


def test_get_ai_defaults_to_mock_under_test_env() -> None:
    previous = set_ai(None)
    try:
        services = get_ai()
        assert isinstance(services, MockAIServices)
        assert get_ai() is services
    finally:
        set_ai(previous)


async def test_mock_fixture_restores_previous(mock_ai: MockAIServices) -> None:
    assert get_ai() is mock_ai
