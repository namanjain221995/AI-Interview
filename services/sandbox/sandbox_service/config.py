"""Sandbox service configuration (environment variables prefixed ``SANDBOX_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RuntimeName = Literal["python", "node", "java", "go", "cpp"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore", case_sensitive=False)

    # ----------------------------------------------------------------- http
    host: str = "0.0.0.0"  # noqa: S104 - inside the container; compose publishes on 127.0.0.1 only
    port: int = 8220
    shared_secret: SecretStr = Field(..., description="HMAC-SHA256 key shared with the API (SANDBOX_SHARED_SECRET)")
    signature_max_skew_seconds: int = 60
    max_request_bytes: int = 4 * 1024 * 1024
    log_level: str = "INFO"
    log_json: bool = True

    # ---------------------------------------------------------- concurrency
    max_concurrency: int = Field(4, ge=1, le=64)
    max_queue: int = Field(64, ge=0)
    queue_timeout_seconds: float = Field(15.0, gt=0)

    # --------------------------------------------------------------- docker
    docker_url: str = "unix:///var/run/docker.sock"
    docker_api_version: str = "auto"
    docker_timeout_seconds: float = 30.0
    image_python: str = "interview-sandbox-python:local"
    image_node: str = "interview-sandbox-node:local"
    image_java: str = "interview-sandbox-java:local"
    image_go: str = "interview-sandbox-go:local"
    image_cpp: str = "interview-sandbox-cpp:local"
    container_user: str = "10001:10001"
    # Optional OCI runtime for sandbox containers, e.g. "runsc" (gVisor) once installed and
    # registered with dockerd; empty = Docker's default (runc + default seccomp profile).
    container_runtime: str = ""
    image_check_ttl_seconds: float = 10.0

    # ------------------------------------------------------ resource limits
    cpus: float = Field(1.0, gt=0, le=8)
    cpu_shares: int = Field(512, ge=2, le=262144)  # below the default 1024: yield to the host's production stack
    pids_limit: int = Field(64, ge=16, le=4096)
    tmpfs_size_mb: int = Field(64, ge=8, le=1024)
    nofile_limit: int = Field(256, ge=64, le=65536)
    fsize_limit_mb: int = Field(32, ge=1, le=1024)
    stack_limit_mb: int = Field(64, ge=8, le=512)
    runtime_overhead_mb: int = Field(64, ge=16, le=1024)  # runner + tmpfs headroom inside the container cgroup
    deadline_grace_seconds: float = 10.0

    # ------------------------------------------------------------- outputs
    output_capture_bytes: int = 64 * 1024  # per stream returned to the caller
    output_hard_cap_bytes: int = 4 * 1024 * 1024  # stdout+stderr per process before it is killed
    results_cap_bytes: int = 8 * 1024 * 1024  # harness result channel per process
    record_cap_bytes: int = 1024 * 1024  # one harness record (one test's return value)
    container_output_cap_bytes: int = 32 * 1024 * 1024  # raw container stdout before the container is killed
    value_preview_bytes: int = 8 * 1024  # actual/expected values echoed in responses
    sql_max_rows: int = 10_000

    # ------------------------------------------------------------- janitor
    janitor_interval_seconds: float = 60.0
    stale_container_seconds: float = 300.0

    @field_validator("shared_secret")
    @classmethod
    def _secret_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 16:
            raise ValueError("SANDBOX_SHARED_SECRET must be at least 16 characters")
        return value

    def image_for(self, runtime: str) -> str:
        return {
            "python": self.image_python,
            "node": self.image_node,
            "java": self.image_java,
            "go": self.image_go,
            "cpp": self.image_cpp,
        }[runtime]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
