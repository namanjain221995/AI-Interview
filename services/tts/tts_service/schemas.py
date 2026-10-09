"""Wire models. ``TtsRequest``/``TtsResponse`` are the contract the API's KokoroTtsProvider uses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tts_service.audio import SUPPORTED_SAMPLE_RATES
from tts_service.config import MAX_TEXT_CHARS

VisemeId = Literal["sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"]


class TtsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = Field(default=None, max_length=64)
    speed: float = Field(default=1.0, ge=0.7, le=1.3)
    sample_rate: int = 24000

    @field_validator("sample_rate")
    @classmethod
    def _supported_rate(cls, value: int) -> int:
        if value not in SUPPORTED_SAMPLE_RATES:
            raise ValueError(f"sample_rate must be one of {', '.join(map(str, SUPPORTED_SAMPLE_RATES))}")
        return value


class Viseme(BaseModel):
    t: int = Field(description="Milliseconds from the start of the audio")
    v: VisemeId
    w: float = Field(ge=0, le=1)


class TtsResponse(BaseModel):
    audio_b64: str = Field(description="Base64 of mono little-endian PCM16 samples")
    sample_rate: int
    duration_ms: int
    visemes: list[Viseme]
    phonemes: str
    voice: str
    cached: bool


class SpeechRequest(BaseModel):
    """OpenAI ``POST /v1/audio/speech`` subset; unknown fields (instructions, stream_format) are ignored."""

    model_config = ConfigDict(extra="ignore")

    model: str | None = Field(default=None, max_length=64)
    input: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str | None = Field(default=None, max_length=64)
    response_format: Literal["wav", "pcm"] = "wav"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class VoiceOut(BaseModel):
    id: str
    language: str
    language_name: str
    gender: Literal["female", "male"]
    default: bool


class VoicesResponse(BaseModel):
    default: str
    voices: list[VoiceOut]


class HealthResponse(BaseModel):
    status: Literal["ok", "loading", "error"]
    model: str
    warm: bool
    last_latency_ms: float | None
    viseme_timing: Literal["model", "estimated"] | None = None
    workers: int
    threads: int
    queue_depth: int
    inflight: int
    cache_entries: int
    uptime_s: float
    detail: str | None = None
