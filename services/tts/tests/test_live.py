"""Checks against a running service (``-m live``; TTS_LIVE_URL, default http://127.0.0.1:8210)."""

from __future__ import annotations

import base64
import io
import os
import time
import wave

import httpx
import numpy as np
import pytest

from tts_service.audio import audible_bounds, frame_levels

pytestmark = pytest.mark.live

URL = os.environ.get("TTS_LIVE_URL", "http://127.0.0.1:8210")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=URL, timeout=30) as http:
        try:
            health = http.get("/health")
        except httpx.HTTPError as exc:
            pytest.skip(f"tts service not reachable at {URL}: {exc}")
        assert health.status_code == 200, health.text
        yield http


def test_health(client: httpx.Client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["warm"] is True and body["model"]
    assert isinstance(body["last_latency_ms"], (int, float))


def test_voices(client: httpx.Client) -> None:
    body = client.get("/v1/voices").json()
    ids = {voice["id"] for voice in body["voices"]}
    assert body["default"] in ids and {"af_heart", "am_michael", "bf_emma"} <= ids


def test_tts_audio_and_visemes_line_up(client: httpx.Client) -> None:
    text = f"Thanks for that answer. Let's continue with question {int(time.time()) % 1000}."
    response = client.post(
        "/v1/tts", json={"text": text, "voice": "af_heart", "speed": 1.0, "sample_rate": 24000}
    )
    assert response.status_code == 200
    body = response.json()
    samples = np.frombuffer(base64.b64decode(body["audio_b64"]), dtype="<i2").astype(np.float32) / 32767
    assert body["duration_ms"] == round(len(samples) * 1000 / body["sample_rate"])
    start, end = audible_bounds(frame_levels(samples, body["sample_rate"]))  # type: ignore[misc]
    visemes = body["visemes"]
    first_open = next(v for v in visemes if v["v"] != "sil")
    assert abs(first_open["t"] - start * 1000) <= 15
    assert visemes[-1]["v"] == "sil" and abs(visemes[-1]["t"] - end * 1000) <= 15
    assert all(a["t"] < b["t"] for a, b in zip(visemes, visemes[1:], strict=False))


def test_openai_speech_wav(client: httpx.Client) -> None:
    response = client.post(
        "/v1/audio/speech",
        json={"model": "kokoro", "input": "Hello there.", "voice": "af_heart", "response_format": "wav"},
    )
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as reader:
        assert reader.getframerate() == 24000 and reader.getnframes() > 12000


def test_rejects_overlong_text(client: httpx.Client) -> None:
    assert client.post("/v1/tts", json={"text": "a" * 1001}).status_code == 422


def test_metrics(client: httpx.Client) -> None:
    text = client.get("/metrics").text
    assert "interview_tts_synthesis_seconds_count" in text and "interview_tts_ready 1.0" in text
