"""Service configuration, read from ``TTS_*`` environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = SERVICE_ROOT / "models"
# Hard upper bound of the wire contract (POST /v1/tts text <= 1000 characters).
MAX_TEXT_CHARS = 1000


def _default_model_path() -> Path:
    # Prefer the variant that exposes per-phoneme durations (built by
    # scripts/add_duration_output.py, always present in the Docker image).
    timed = MODELS_DIR / "kokoro-v1.0-durations.onnx"
    return timed if timed.is_file() else MODELS_DIR / "kokoro-v1.0.onnx"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTS_", extra="ignore", case_sensitive=False)

    host: str = "127.0.0.1"
    port: int = Field(default=8210, ge=1, le=65535)

    model_path: Path = Field(default_factory=_default_model_path)
    voices_path: Path = MODELS_DIR / "voices-v1.0.bin"
    model_name: str = "kokoro-v1.0"
    default_voice: str = "af_heart"

    # CPU budget: ``workers`` concurrent syntheses (one ONNX session each), each
    # using ``threads`` intra-op threads. Requests beyond that wait in a queue of
    # at most ``max_queue`` entries; ``request_timeout_seconds`` bounds wait + work.
    threads: int = Field(default=8, ge=1, le=64)
    workers: int = Field(default=2, ge=1, le=8)
    max_queue: int = Field(default=32, ge=0, le=4096)
    request_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    # ORT intra-op threads spin-wait between the graph's many small kernels
    # instead of sleeping on futexes. Measured on the DGX Spark (8 threads): a
    # 6-word sentence takes 279 ms with spinning vs 581 ms without, and uses less
    # CPU in total; threads stop spinning shortly after a run, so idle cost is nil.
    allow_spinning: bool = True

    cache_max_entries: int = Field(default=512, ge=0)
    cache_max_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    cache_max_item_bytes: int = Field(default=4 * 1024 * 1024, ge=0)

    warmup_text: str = "Hello, and welcome. Thanks for joining the interview today."
    # Optional bearer token for /v1/*; unset means no authentication (the
    # service is published on 127.0.0.1 only).
    api_key: SecretStr | None = None
    max_body_bytes: int = Field(default=64 * 1024, ge=1024)
    enable_docs: bool = True

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        return None if value in ("", None) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
