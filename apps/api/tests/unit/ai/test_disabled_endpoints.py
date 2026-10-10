"""AI_DISABLED_ENDPOINTS (S18): disabled endpoints fail fast without I/O, report 'disabled' and never
trip the breaker; required endpoints cannot be disabled."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.ai.base import EndpointDisabled
from app.ai.http import EndpointClient
from app.ai.registry import RealAIServices
from app.core.config import Settings


def _client(disabled: bool, calls: list[str]) -> EndpointClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    return EndpointClient(
        "vision", "http://vision.test/v1", timeout=2.0, max_concurrency=1,
        transport=httpx.MockTransport(handler), disabled=disabled,
    )  # fmt: skip


async def test_disabled_endpoint_raises_before_any_io() -> None:
    calls: list[str] = []
    client = _client(True, calls)
    with pytest.raises(EndpointDisabled) as excinfo:
        await client.request("GET", "/models")
    assert excinfo.value.retryable is False and excinfo.value.endpoint == "vision"
    with pytest.raises(EndpointDisabled):
        async with client.stream("POST", "/chat/completions", json={}):
            pass
    assert calls == []  # nothing left the process
    assert client.breaker.state == "closed"  # the breaker never saw a failure
    await client.aclose()


async def test_enabled_endpoint_still_works() -> None:
    calls: list[str] = []
    client = _client(False, calls)
    result = await client.request("GET", "/models", use_breaker=False, acquire_slot=False)
    assert result.response.status_code == 200 and calls == ["/v1/models"]
    await client.aclose()


def test_config_rejects_required_endpoints_and_normalises(settings: Settings) -> None:
    data = settings.model_dump()
    data["ai_disabled_endpoints"] = "Vision, reranker,vision"
    parsed = Settings.model_validate(data)
    assert parsed.ai_disabled_endpoints == ["reranker", "vision"]
    assert parsed.ai_endpoint_disabled("vision") and not parsed.ai_endpoint_disabled("embedding")
    data["ai_disabled_endpoints"] = "main"
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


async def test_registry_reports_disabled_without_probing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = settings.model_dump()
    data["ai_disabled_endpoints"] = ["vision", "reranker"]
    services = RealAIServices(Settings.model_validate(data))
    probed: list[str] = []

    async def fake_probe(self: Any, client: EndpointClient) -> dict[str, Any]:
        probed.append(client.name)
        return {"status": "ok", "latency_ms": 1.0, "detail": "stub"}

    async def fake_simple(self: Any) -> dict[str, Any]:
        return {"status": "ok", "latency_ms": 1.0, "detail": "stub"}

    monkeypatch.setattr(RealAIServices, "_probe_models", fake_probe)
    monkeypatch.setattr(RealAIServices, "_probe_reranker", fake_simple)
    monkeypatch.setattr(RealAIServices, "_probe_whisper", fake_simple)
    monkeypatch.setattr(RealAIServices, "_probe_tts", fake_simple)
    try:
        report = await services.health()
    finally:
        await services.aclose()
    assert report["vision"]["status"] == "disabled" and report["reranker"]["status"] == "disabled"
    assert report["main"]["status"] == "ok" and "vision" not in probed
    assert services.endpoints["vision"].disabled and not services.endpoints["main"].disabled
