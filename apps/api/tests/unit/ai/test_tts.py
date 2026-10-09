from __future__ import annotations

import base64
import io
import json
import math
import struct
import wave

import httpx
import numpy as np
import pytest
import respx

from app.ai.base import AIUnavailable, TtsProvider, TtsUnavailable
from app.ai.clients.tts import (
    BrowserTtsProvider,
    KokoroTtsProvider,
    MockTtsProvider,
    OpenAICompatibleTtsProvider,
    create_tts_provider,
    decode_wav,
)
from app.ai.visemes import OCULUS_VISEMES
from app.core.config import get_settings

BASE = "http://tts.test"


def pcm_tone(ms: int, rate: int = 24000) -> bytes:
    n = rate * ms // 1000
    return b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 200 * i / rate))) for i in range(n))


def wav(pcm: bytes, rate: int = 22050, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


@pytest.fixture
def kokoro(endpoint_factory, ledger) -> KokoroTtsProvider:
    return KokoroTtsProvider(
        endpoint_factory("tts", BASE, retries=1), voice="af_heart", sample_rate=24000, ledger=ledger
    )


async def test_kokoro_wire_contract(kokoro: KokoroTtsProvider, ledger) -> None:
    pcm = pcm_tone(500)
    reply = {
        "audio_b64": base64.b64encode(pcm).decode(),
        "sample_rate": 24000,
        "duration_ms": 500,
        "visemes": [
            {"t": 0, "v": "sil", "w": 1.0},
            {"t": 40, "v": "aa", "w": 0.8},
            {"t": 120, "v": "bogus", "w": 1},
            {"t": 200, "v": "PP", "w": 2.0},
        ],
        "phonemes": "hˈɛloʊ",
        "voice": "af_heart",
        "cached": False,
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/tts").respond(200, json=reply)
        result = await kokoro.synthesize("  Hello   there ", speed=5.0)
    body = json.loads(route.calls.last.request.content)
    assert body == {"text": "Hello there", "voice": "af_heart", "speed": 1.3, "sample_rate": 24000}
    assert result.pcm16 == pcm
    assert (result.sample_rate, result.duration_ms, result.provider) == (24000, 500, "kokoro")
    assert [(c.t_ms, c.viseme, c.weight) for c in result.visemes] == [
        (0, "sil", 1.0),
        (40, "aa", 0.8),
        (200, "PP", 1.0),
    ]
    assert result.latency_ms > 0
    assert ledger.records[-1].endpoint == "tts" and ledger.records[-1].task == "tts"


async def test_kokoro_derives_visemes_when_missing(kokoro: KokoroTtsProvider) -> None:
    pcm = pcm_tone(800)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/tts").respond(
            200, json={"audio_b64": base64.b64encode(pcm).decode(), "sample_rate": 24000}
        )
        result = await kokoro.synthesize("Good morning, Alex.", voice="am_adam")
    assert result.duration_ms == 800
    assert result.visemes and result.visemes[-1].viseme == "sil"
    assert result.visemes[-1].t_ms < 800


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(400, json={"error": "text too long"}),
        httpx.Response(200, json={"audio_b64": "!!!not base64!!!"}),
        httpx.Response(200, json={"nope": True}),
    ],
)
async def test_kokoro_failures_become_tts_unavailable(
    kokoro: KokoroTtsProvider, response: httpx.Response
) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/tts").mock(return_value=response)
        with pytest.raises(TtsUnavailable) as info:
            await kokoro.synthesize("Hi")
    assert isinstance(info.value, AIUnavailable)


async def test_kokoro_connection_refused_and_circuit(kokoro: KokoroTtsProvider) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/tts").mock(side_effect=httpx.ConnectError("refused"))
        for _ in range(3):
            with pytest.raises(TtsUnavailable):
                await kokoro.synthesize("Hi")


async def test_kokoro_health(kokoro: KokoroTtsProvider) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/health").respond(200, json={"status": "ok", "voices": 54})
        assert await kokoro.health() is True
    with respx.mock(base_url=BASE) as mock:
        mock.get("/health").respond(200, json={"status": "loading"})
        assert await kokoro.health() is False
    with respx.mock(base_url=BASE) as mock:
        mock.get("/health").mock(side_effect=httpx.ConnectError("refused"))
        assert await kokoro.health() is False


async def test_empty_text_returns_empty_audio(kokoro: KokoroTtsProvider) -> None:
    result = await kokoro.synthesize("   ")
    assert result.pcm16 == b"" and result.duration_ms == 0


async def test_openai_compatible_wav(endpoint_factory) -> None:
    provider = OpenAICompatibleTtsProvider(
        endpoint_factory("tts", BASE), model="qwen3-tts", voice="v1", sample_rate=24000
    )
    stereo = np.array([[1000, 3000]] * 2205, dtype="<i2").tobytes()
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/audio/speech").respond(200, content=wav(stereo, rate=22050, channels=2))
        result = await provider.synthesize("Tell me about it.")
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "model": "qwen3-tts",
        "input": "Tell me about it.",
        "voice": "v1",
        "response_format": "wav",
        "speed": 1.0,
    }
    assert result.sample_rate == 22050
    assert result.duration_ms == 100
    assert np.frombuffer(result.pcm16, dtype="<i2")[0] == 2000  # downmixed
    assert result.visemes and all(c.viseme in OCULUS_VISEMES for c in result.visemes)


async def test_openai_compatible_raw_pcm(endpoint_factory) -> None:
    provider = OpenAICompatibleTtsProvider(
        endpoint_factory("tts", BASE), model="m", response_format="pcm", sample_rate=24000
    )
    pcm = pcm_tone(250)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/audio/speech").respond(200, content=pcm + b"\x00")
        result = await provider.synthesize("Hi there")
    assert result.pcm16 == pcm
    assert result.duration_ms == 250


async def test_openai_compatible_expected_wav_but_got_garbage(endpoint_factory) -> None:
    provider = OpenAICompatibleTtsProvider(endpoint_factory("tts", BASE), model="m")
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/audio/speech").respond(200, content=b"garbage")
        with pytest.raises(TtsUnavailable):
            await provider.synthesize("Hi")


def test_decode_wav_widths() -> None:
    pcm8 = bytes([128, 255, 0])
    out, rate = decode_wav(wav(pcm8, rate=8000, width=1))
    assert rate == 8000 and np.frombuffer(out, dtype="<i2").tolist() == [0, 32512, -32768]
    pcm24 = b"\x00\x00\x40" + b"\x00\x00\xc0"  # +0.5 and -0.5 full scale
    out, _ = decode_wav(wav(pcm24, width=3))
    assert np.frombuffer(out, dtype="<i2").tolist() == [16384, -16384]
    with pytest.raises(TtsUnavailable):
        decode_wav(b"RIFF....WAVEjunk")


async def test_browser_provider_raises_tts_unavailable() -> None:
    provider = BrowserTtsProvider()
    with pytest.raises(TtsUnavailable):
        await provider.synthesize("Hello")
    assert await provider.health() is True
    assert isinstance(provider, TtsProvider)


async def test_mock_provider_is_deterministic() -> None:
    provider = MockTtsProvider(24000)
    a = await provider.synthesize("Hello, world.")
    b = await provider.synthesize("Hello, world.")
    assert a.pcm16 == b.pcm16
    assert a.duration_ms == round(len("Hello, world.") * 60)
    assert len(a.pcm16) == 24000 * a.duration_ms // 1000 * 2
    assert a.visemes[0].t_ms == 0 and a.visemes[-1].viseme == "sil"
    samples = np.frombuffer(a.pcm16, dtype="<i2")
    assert samples.any()  # tone while speaking
    assert provider.calls == ["Hello, world.", "Hello, world."]
    provider.fail_with = TtsUnavailable("down")
    with pytest.raises(TtsUnavailable):
        await provider.synthesize("x")
    assert isinstance(provider, TtsProvider)


def test_factory_selects_provider() -> None:
    settings = get_settings()
    assert isinstance(
        create_tts_provider(settings.model_copy(update={"tts_provider": "mock"})), MockTtsProvider
    )
    assert isinstance(
        create_tts_provider(settings.model_copy(update={"tts_provider": "browser"})), BrowserTtsProvider
    )
    with pytest.raises(ValueError):
        create_tts_provider(settings.model_copy(update={"tts_provider": "kokoro"}))


def test_split_for_tts_respects_kokoro_limit_and_sentence_boundaries() -> None:
    from app.ai.clients.tts import KOKORO_MAX_TEXT_CHARS, split_for_tts

    text = "This is a sentence about Kubernetes autoscaling. " * 40
    parts = split_for_tts(text, KOKORO_MAX_TEXT_CHARS)
    assert len(parts) >= 2
    assert all(len(p) <= KOKORO_MAX_TEXT_CHARS for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert " ".join(parts) == " ".join(text.split())
    assert split_for_tts("   ", 1000) == []
    assert split_for_tts("short text", 1000) == ["short text"]
