from __future__ import annotations

import asyncio
import base64
import io
import threading
import time
import wave
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeEngine
from tts_service.app import create_app
from tts_service.config import Settings

CONTRACT_KEYS = {"audio_b64", "sample_rate", "duration_ms", "visemes", "phonemes", "voice", "cached"}
OCULUS = {"sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"}


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"log_json": False, "workers": 2, "threads": 1, "request_timeout_seconds": 5.0}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def wait_ready(client: TestClient, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/health").status_code == 200:
            return
        time.sleep(0.01)
    raise AssertionError("service never became ready")


@pytest.fixture
def client(fake_engine: FakeEngine) -> Iterator[TestClient]:
    with TestClient(create_app(settings(), fake_engine)) as test_client:
        wait_ready(test_client)
        yield test_client


def test_tts_contract(client: TestClient, fake_engine: FakeEngine) -> None:
    response = client.post(
        "/v1/tts", json={"text": "Hello **there**, e.g. you.", "voice": "am_michael", "speed": 1.1}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == CONTRACT_KEYS
    pcm = base64.b64decode(body["audio_b64"])
    assert len(pcm) == 2 * 12000 and body["sample_rate"] == 24000 and body["duration_ms"] == 500
    assert body["voice"] == "am_michael" and body["cached"] is False and body["phonemes"]
    assert all(set(v) == {"t", "v", "w"} and v["v"] in OCULUS and 0 <= v["w"] <= 1 for v in body["visemes"])
    assert [v["t"] for v in body["visemes"]] == sorted(v["t"] for v in body["visemes"])
    # The engine receives normalised text and the requested parameters.
    assert fake_engine.calls == [("Hello there, for example you.", "am_michael", 1.1, 24000)]
    assert response.headers["x-tts-cache"] == "miss" and response.headers["x-request-id"]


def test_repeated_phrase_is_served_from_cache(client: TestClient, fake_engine: FakeEngine) -> None:
    first = client.post("/v1/tts", json={"text": "Great, thank you."})
    second = client.post("/v1/tts", json={"text": "Great,   thank you. "})  # same after normalisation
    assert first.json()["cached"] is False and second.json()["cached"] is True
    assert second.json()["audio_b64"] == first.json()["audio_b64"]
    assert len(fake_engine.calls) == 1
    third = client.post(
        "/v1/tts", json={"text": "Great, thank you.", "speed": 0.9}
    )  # speed is part of the key
    assert third.json()["cached"] is False and len(fake_engine.calls) == 2


def test_default_voice_and_openai_alias(client: TestClient) -> None:
    assert client.post("/v1/tts", json={"text": "Hi.", "voice": None}).json()["voice"] == "af_heart"
    assert client.post("/v1/tts", json={"text": "Hi.", "voice": "alloy"}).json()["voice"] == "af_alloy"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"text": "x" * 1001}, "text"),
        ({"text": ""}, "text"),
        ({"text": "hi", "speed": 1.5}, "speed"),
        ({"text": "hi", "speed": 0.5}, "speed"),
        ({"text": "hi", "sample_rate": 44100}, "sample_rate"),
        ({"voice": "af_heart"}, "text"),
    ],
)
def test_invalid_requests_are_422(client: TestClient, payload: dict[str, object], field: str) -> None:
    response = client.post("/v1/tts", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error" and field in error["message"]
    assert "x" * 50 not in response.text  # submitted text is not echoed back


def test_unknown_voice_is_422_with_available_list(client: TestClient) -> None:
    response = client.post("/v1/tts", json={"text": "hi", "voice": "nope"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "unknown_voice" and "af_heart" in error["details"]["available"]


def test_text_with_nothing_speakable_returns_empty_audio(client: TestClient, fake_engine: FakeEngine) -> None:
    body = client.post("/v1/tts", json={"text": "😀 **"}).json()
    assert body["audio_b64"] == "" and body["duration_ms"] == 0
    assert body["visemes"] == [{"t": 0, "v": "sil", "w": 1.0}]
    assert fake_engine.calls == []


def test_resampled_rate_is_passed_through(client: TestClient, fake_engine: FakeEngine) -> None:
    body = client.post("/v1/tts", json={"text": "Hello.", "sample_rate": 16000}).json()
    assert body["sample_rate"] == 16000 and len(base64.b64decode(body["audio_b64"])) == 2 * 8000
    assert fake_engine.calls[-1][3] == 16000


def test_openai_speech_wav_and_pcm(client: TestClient) -> None:
    wav = client.post("/v1/audio/speech", json={"model": "tts-1", "input": "Hello.", "voice": "alloy"})
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(wav.content)) as reader:
        assert reader.getframerate() == 24000 and reader.getnchannels() == 1 and reader.getnframes() == 12000
    pcm = client.post(
        "/v1/audio/speech",
        json={"model": "kokoro", "input": "Hello.", "response_format": "pcm", "instructions": "x"},
    )
    assert pcm.status_code == 200 and pcm.headers["content-type"] == "audio/pcm" and len(pcm.content) == 24000
    assert pcm.headers["x-sample-rate"] == "24000"
    bad = client.post("/v1/audio/speech", json={"input": "Hello.", "response_format": "mp3"})
    assert bad.status_code == 422


def test_voices(client: TestClient) -> None:
    body = client.get("/v1/voices").json()
    assert body["default"] == "af_heart"
    heart = next(v for v in body["voices"] if v["id"] == "af_heart")
    assert heart == {
        "id": "af_heart",
        "language": "en-us",
        "language_name": "American English",
        "gender": "female",
        "default": True,
    }


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok" and body["warm"] is True and body["model"] == "kokoro-v1.0"
    assert body["last_latency_ms"] == pytest.approx(12.0)
    assert body["viseme_timing"] == "model" and body["workers"] == 2


def test_metrics_exposed(client: TestClient) -> None:
    client.post("/v1/tts", json={"text": "Metrics please."})
    client.post("/v1/tts", json={"text": "Metrics please."})
    text = client.get("/metrics").text
    for name in (
        "interview_tts_synthesis_seconds_bucket",
        "interview_tts_real_time_factor_bucket",
        "interview_tts_characters_total",
        "interview_tts_cache_hits_total",
        "interview_tts_cache_misses_total",
        "interview_tts_queue_depth",
        "interview_tts_errors_total",
        'interview_tts_requests_total{endpoint="tts",status="200"}',
    ):
        assert name in text, name


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
    sanitized = client.get("/health", headers={"X-Request-ID": "bad id\nwith spaces"})
    assert sanitized.headers["x-request-id"] != "bad id\nwith spaces"


def test_oversized_body_is_413(client: TestClient) -> None:
    response = client.post(
        "/v1/tts",
        content=b"{" + b" " * 70000 + b"}",
        headers={"content-type": "application/json", "X-Request-ID": "big-1"},
    )
    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "payload_too_large",
        "message": "request body exceeds 65536 bytes",
        "request_id": "big-1",
    }

    def chunks() -> Iterator[bytes]:
        for _ in range(80):
            yield b" " * 1000

    chunked = client.post("/v1/tts", content=chunks(), headers={"content-type": "application/json"})
    assert chunked.status_code == 413


def test_api_key_required_when_configured(fake_engine: FakeEngine) -> None:
    with TestClient(create_app(settings(api_key="s3cret-token"), fake_engine)) as client:
        wait_ready(client)
        assert client.post("/v1/tts", json={"text": "hi"}).status_code == 401
        assert (
            client.post("/v1/tts", json={"text": "hi"}, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        ok = client.post("/v1/tts", json={"text": "hi"}, headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200
        assert client.get("/health").status_code == 200  # health and metrics stay open for probes


def test_loading_then_ready() -> None:
    gate = threading.Event()
    engine = FakeEngine(load_gate=gate)
    with TestClient(create_app(settings(), engine)) as client:
        loading = client.get("/health")
        assert loading.status_code == 503 and loading.json()["status"] == "loading"
        assert loading.json()["warm"] is False
        busy = client.post("/v1/tts", json={"text": "hi"})
        assert busy.status_code == 503 and busy.json()["error"]["code"] == "not_ready"
        assert busy.headers["retry-after"] == "2"
        gate.set()
        wait_ready(client)


def test_startup_failure_reports_error() -> None:
    engine = FakeEngine(load_error=FileNotFoundError("kokoro-v1.0.onnx missing"))
    with TestClient(create_app(settings(), engine)) as client:
        deadline = time.monotonic() + 5
        while client.get("/health").json()["status"] == "loading" and time.monotonic() < deadline:
            time.sleep(0.01)
        health = client.get("/health")
        assert health.status_code == 503 and health.json()["status"] == "error"
        assert "kokoro-v1.0.onnx missing" in health.json()["detail"]
        assert client.post("/v1/tts", json={"text": "hi"}).status_code == 503


def test_synthesis_failure_is_500() -> None:
    with TestClient(create_app(settings(), FakeEngine(fail=True))) as client:
        wait_ready(client)
        response = client.post("/v1/tts", json={"text": "hi"})
        assert response.status_code == 500 and response.json()["error"]["code"] == "synthesis_failed"
        assert "exploded" not in response.text  # internal errors are not leaked


async def _started_app(engine: FakeEngine, **overrides: object):
    app = create_app(settings(**overrides), engine)
    await app.state.service.start()
    return app


async def test_timeout_is_504() -> None:
    app = await _started_app(FakeEngine(delay=0.5), request_timeout_seconds=0.1)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tts") as client:
        response = await client.post("/v1/tts", json={"text": "slow"})
    assert response.status_code == 504 and response.json()["error"]["code"] == "timeout"
    await asyncio.sleep(0.6)  # the started job finishes and fills the cache
    async with httpx.AsyncClient(transport=transport, base_url="http://tts") as client:
        retry = await client.post("/v1/tts", json={"text": "slow"})
    assert retry.status_code == 200 and retry.json()["cached"] is True
    app.state.service.shutdown()


async def test_queue_full_is_503_and_concurrency_is_bounded() -> None:
    engine = FakeEngine(delay=0.3)
    app = await _started_app(engine, workers=1, max_queue=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tts") as client:
        responses = await asyncio.gather(
            *(client.post("/v1/tts", json={"text": f"phrase {i}"}) for i in range(4))
        )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 503, 503]
    rejected = next(r for r in responses if r.status_code == 503)
    assert rejected.json()["error"]["code"] == "busy" and rejected.headers["retry-after"] == "1"
    app.state.service.shutdown()


async def test_identical_concurrent_requests_synthesise_once() -> None:
    engine = FakeEngine(delay=0.2)
    app = await _started_app(engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tts") as client:
        responses = await asyncio.gather(
            *(client.post("/v1/tts", json={"text": "Thanks!"}) for _ in range(5))
        )
    assert all(r.status_code == 200 for r in responses)
    assert len(engine.calls) == 1
    app.state.service.shutdown()
