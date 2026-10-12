"""Centralised configuration.

Every endpoint URL, model name, secret, threshold and limit used by the
platform is declared here and read from the environment (or the repository
``.env`` file). Nothing else in the code base may hard-code an endpoint URL or
model identifier: import :func:`get_settings` instead.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
API_ROOT = Path(__file__).resolve().parents[2]

AppEnv = Literal["development", "test", "staging", "production"]
TtsProviderName = Literal["kokoro", "openai_compatible", "browser", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "AI Interview Platform"
    app_env: AppEnv = "development"
    log_level: str = "INFO"
    log_json: bool = True
    public_base_url: str = "http://localhost:3200"
    api_host: str = "127.0.0.1"
    api_port: int = 8200
    worker_metrics_port: int = 8201
    enable_api_docs: bool = True
    enable_test_hooks: bool = False
    # Shared secret for /api/v1/test/* (header X-Test-Hooks-Token). Required outside APP_ENV=test.
    test_hooks_token: SecretStr | None = None
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    instance_id: str = Field(default="", description="Stable id of this process; generated when empty")

    # ------------------------------------------------------------- security
    jwt_secret: SecretStr = Field(..., description="HS256 signing key for access tokens")
    jwt_previous_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    candidate_session_ttl_seconds: int = 6 * 3600
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    csrf_enabled: bool = True
    password_min_length: int = 12
    login_max_attempts: int = 5
    login_lockout_seconds: int = 900
    invite_ttl_hours: int = 168
    ws_ticket_ttl_seconds: int = 60
    media_url_secret: SecretStr = Field(..., description="HMAC key for API-signed media URLs")

    # ------------------------------------------------------------- database
    database_url: str = Field(..., description="postgresql+asyncpg://user:pass@host:port/db")
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ---------------------------------------------------------------- redis
    redis_url: str = Field(..., description="redis://:password@host:port/db")

    # ------------------------------------------------------------ storage
    # Local filesystem only (no S3/MinIO). Files are served exclusively by the
    # API after authorization, through short-lived HMAC-signed URLs.
    storage_backend: Literal["local"] = "local"
    storage_local_root: str = str(REPO_ROOT / "data" / "storage")
    media_url_ttl_seconds: int = 300

    # -------------------------------------------------------------- uploads
    max_resume_bytes: int = 10 * 1024 * 1024
    max_frame_bytes: int = 2 * 1024 * 1024
    max_recording_chunk_bytes: int = 16 * 1024 * 1024
    # Recording upload budget per session and kind (S3, audit D2): (round max duration + 15 min) at this
    # bitrate - about 3x the browser recorder's ~650 kbps - and at most this many recorder parts. A session that
    # has not started yet only gets the small pre-start budget. Uploads (recordings, frames) stop with 503
    # storage_full while the storage disk has less than storage_min_free_ratio free.
    recording_max_bitrate_bps: int = Field(2_000_000, ge=100_000)
    recording_max_parts_per_session: int = Field(40, ge=1)
    recording_prestart_budget_bytes: int = Field(48 * 1024 * 1024, ge=1024 * 1024)
    storage_min_free_ratio: float = Field(0.10, ge=0.0, lt=1.0)
    # Interview recordings: `recording.finalize` joins a part's MediaRecorder
    # segments and remuxes them (stream copy) into one seekable file with
    # ffmpeg; without ffmpeg the joined segments are served as they are.
    recording_ffmpeg_path: str = "ffmpeg"
    recording_ffprobe_path: str = "ffprobe"
    recording_finalize_timeout_seconds: int = 900
    # Scratch space for joining segments (default: the system temp dir).
    recording_work_dir: str | None = None
    # Signed playback/download URLs for recordings (the player re-signs on expiry).
    recording_url_ttl_seconds: int = 900
    malware_scan_mode: Literal["off", "optional", "required"] = "optional"
    clamav_host: str | None = None
    clamav_port: int = 3310

    # ------------------------------------------------- local AI endpoints
    main_llm_base_url: str = "http://localhost:8000/v1"
    main_llm_model: str = "Qwen/Qwen3.6-35B-A3B-NVFP4"
    main_llm_api_key: SecretStr | None = None
    main_llm_timeout_seconds: float = 90.0
    main_llm_max_concurrency: int = 24

    vision_llm_base_url: str = "http://localhost:8002/v1"
    vision_llm_model: str = "Qwen/Qwen3-VL-8B-Instruct-FP8"
    vision_llm_api_key: SecretStr | None = None
    vision_llm_timeout_seconds: float = 60.0
    vision_llm_max_concurrency: int = 4

    embedding_base_url: str = "http://localhost:8003/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = 20.0
    embedding_batch_size: int = 32

    reranker_base_url: str = "http://localhost:8005"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_timeout_seconds: float = 20.0
    reranker_max_concurrency: int = 3

    whisper_base_url: str = "http://172.17.0.1:30007/v1"
    whisper_model: str = "openai/whisper-large-v3"
    whisper_extra_base_urls: Annotated[list[str], NoDecode] = Field(default_factory=list)
    whisper_language: str = "en"
    whisper_timeout_seconds: float = 30.0

    # Capability flags (S18): endpoints a node does not have. Calls fail fast with EndpointDisabled
    # (no I/O, no breaker noise, no retries), health reports "disabled" and dependent work takes its
    # fallback on the first attempt. Only optional endpoints may be disabled: main, whisper and tts
    # are required for an interview.
    ai_disabled_endpoints: Annotated[list[str], NoDecode] = Field(default_factory=list)

    ocr_base_url: str = "http://192.168.9.68:30004/v1"
    ocr_model: str = "baidu/Unlimited-OCR"
    ocr_prompt: str = "OCR"
    ocr_timeout_seconds: float = 90.0
    ocr_max_pages: int = 12

    model_routing_overrides: dict[str, str] = Field(default_factory=dict)
    model_call_log_payloads: bool = False
    llm_max_retries: int = 2
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_reset_seconds: float = 30.0

    # ------------------------------------------------------------------ tts
    tts_provider: TtsProviderName = "kokoro"
    tts_base_url: str = "http://127.0.0.1:8210"
    tts_model: str = "kokoro-v1.0"
    tts_voice: str = "af_heart"
    tts_sample_rate: int = 24000
    tts_timeout_seconds: float = 20.0
    tts_max_inflight_per_session: int = 2

    # ------------------------------------------------------------- sandbox
    sandbox_base_url: str = "http://127.0.0.1:8220"
    sandbox_shared_secret: SecretStr = Field(...)
    sandbox_timeout_seconds: float = 45.0

    # ------------------------------------------------- realtime / voice loop
    stt_partials_enabled: bool = True
    # Measured 2026-10-07: whisper-large-v3 here runs at RTF ~0.3 (1.8 s for a 5 s clip),
    # so partials are throttled and segments are bounded to keep end-of-turn latency low.
    stt_partial_interval_ms: int = 1500
    stt_partial_min_audio_ms: int = 1200
    stt_max_segment_seconds: float = 6.0
    # Total concurrent Whisper requests per process; each replica serves one at a time
    # (replica_parallelism=1 in the STT client), so this mostly bounds queued finals.
    stt_max_concurrency: int = 4
    vad_engine: Literal["silero", "energy"] = "silero"
    vad_model_path: str = str(API_ROOT / "models" / "silero_vad.onnx")
    # Server-side face detector run on every received camera frame (OpenCV YuNet,
    # opencv_zoo face_detection_yunet_2023mar, MIT; scripts/download_assets.sh face).
    # ~10 ms per 640x480 frame with 2 threads on the DGX Spark.
    face_detector_model_path: str = str(API_ROOT / "models" / "face_detection_yunet_2023mar.onnx")
    face_detector_score_threshold: float = 0.7
    face_detector_threads: int = 2
    vad_speech_threshold: float = 0.5
    vad_barge_in_threshold: float = 0.8
    barge_in_min_speech_ms: int = 280
    segment_pause_ms: int = 350
    endpoint_silence_ms: int = 850
    endpoint_silence_short_ms: int = 600
    endpoint_silence_long_ms: int = 1700
    # A short fragment ending mid-sentence ("so for that", "and the") waits this long before the turn ends.
    endpoint_fragment_hold_ms: int = 4000
    think_pause_max_seconds: int = 25
    max_utterance_seconds: int = 240
    acknowledgment_enabled: bool = True
    # Platform rule (owner decision 2026-10-12): every interview, whatever the round type, job or organisation
    # settings, requires the camera, records video and analyses camera frames. Frozen into each invitation's
    # policy snapshot at creation (existing invitations keep what the candidate consented to).
    require_camera_for_all_interviews: bool = True
    # New camera-frame analyses wait (<= 6 s) while an interviewer reply is starting (app.services.integrity.reply_window).
    integrity_frames_yield_to_replies: bool = True
    # Tier 2: the model writes the compact turn decision (TurnDecisionCompact: judgement fields right after the
    # speech, no avatar fields, shorter lists) so the metadata arrives before the next turn instead of being cut.
    turn_decision_compact: bool = True
    # Tier 2: a deterministic check of each substantive answer names what it left out (services/interview/gaps.py)
    # and the turn prompt asks the interviewer to follow up on exactly that.
    interview_gap_probes: bool = True
    ws_heartbeat_interval_seconds: int = 10
    ws_disconnect_grace_seconds: int = 180
    session_resume_buffer_messages: int = 300
    session_lock_ttl_seconds: int = 30
    # Session lifecycle (S1): a started session that nobody drives (no runtime holds its lock and
    # nothing wrote to it) for this long is ended offline as disconnect_timeout, so scoring and the
    # report run and recruiters stop seeing it as "in progress". Must exceed the reconnect grace.
    session_abandon_after_seconds: int = 1200
    session_sweep_batch: int = 25
    # First deploy on a long-running database: report what the sweep would end without ending it.
    session_sweep_dry_run: bool = False
    # A session still COMPLETING this long after it ended, with no report job alive, is recovered (S2).
    session_completing_stale_seconds: int = 900

    # ---------------------------------------------------- interview defaults
    interviewer_name: str = "Maya"
    default_round_minutes: int = 30
    closing_reserve_seconds: int = 75

    # ------------------------------------------------------------ retention
    retention_days_default: int = 180
    frame_retention_days: int = 30
    audio_retention_days: int = 90
    video_retention_days: int = 30

    # ----------------------------------------------------- open interview links
    # HMAC key for duplicate signals (device cookie, IP, IP+UA); default derives
    # from MEDIA_URL_SECRET. Raw IPs/user agents are never stored.
    open_link_signal_secret: SecretStr | None = None
    open_link_signal_retention_days: int = 90
    open_link_blocked_retention_days: int = 30
    open_link_register_ip_limit_per_hour: int = 600
    # Proof-of-work on public registration (S15): a signed, single-use puzzle the browser solves while the
    # form is being filled (about a second on a mid-range phone at 100k).
    open_link_pow_enabled: bool = True
    open_link_pow_max_number: int = Field(100_000, ge=10, le=10_000_000)
    open_link_pow_ttl_seconds: int = Field(600, ge=60, le=3600)
    open_link_default_round_window_hours: int = 72

    # ---------------------------------------------------- intake interviews
    # docs/design/INTAKE_INTERVIEW.md section 12. INTAKE_ENABLED=false: intake routes
    # answer 404 intake_disabled, new intake sessions 409; live sessions finish.
    intake_enabled: bool = True
    intake_eval_concurrency: int = 2
    intake_eval_start_delay_seconds: int = 30
    intake_activity_final_pass_wait_seconds: int = 60
    intake_batch_max_rows: int = 200

    # ------------------------------------------------------- observability
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "interview-api"

    # ----------------------------------------------------------- seed data
    seed_superadmin_email: str = "superadmin@demo.local"
    seed_admin_email: str = "admin@demo.local"
    seed_recruiter_email: str = "recruiter@demo.local"
    seed_interviewer_email: str = "interviewer@demo.local"
    seed_password: SecretStr | None = None

    # ------------------------------------------------------------ validators
    @field_validator(
        "cors_allowed_origins", "whisper_extra_base_urls", "ai_disabled_endpoints", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value

    @field_validator("test_hooks_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("model_routing_overrides", mode="before")
    @classmethod
    def _parse_routing(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @field_validator(
        "main_llm_base_url",
        "vision_llm_base_url",
        "embedding_base_url",
        "reranker_base_url",
        "whisper_base_url",
        "ocr_base_url",
        "tts_base_url",
        "sandbox_base_url",
        "public_base_url",
    )
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("storage_local_root")
    @classmethod
    def _resolve_storage_root(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        return str(path)

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production":
            problems: list[str] = []
            for name, secret in (
                ("JWT_SECRET", self.jwt_secret),
                ("MEDIA_URL_SECRET", self.media_url_secret),
                ("SANDBOX_SHARED_SECRET", self.sandbox_shared_secret),
            ):
                if len(secret.get_secret_value()) < 32:
                    problems.append(f"{name} must be at least 32 characters")
            if self.enable_test_hooks:
                problems.append("ENABLE_TEST_HOOKS must be false")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true")
            if not self.csrf_enabled:
                problems.append("CSRF_ENABLED must be true")
            if self.enable_api_docs:
                problems.append("ENABLE_API_DOCS must be false")
            if self.malware_scan_mode == "off":
                problems.append("MALWARE_SCAN_MODE must be optional or required")
            if self.model_call_log_payloads:
                problems.append("MODEL_CALL_LOG_PAYLOADS must be false (candidate data in the ledger)")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        if not self.instance_id:
            import socket
            import uuid

            self.instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        return self

    # --------------------------------------------------------------- helpers
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @model_validator(mode="after")
    def _abandon_window_exceeds_grace(self) -> Settings:
        minimum = self.ws_disconnect_grace_seconds + 60
        if self.session_abandon_after_seconds < minimum:
            raise ValueError(
                f"SESSION_ABANDON_AFTER_SECONDS must be at least WS_DISCONNECT_GRACE_SECONDS + 60 ({minimum})"
            )
        if self.session_sweep_batch < 1:
            raise ValueError("SESSION_SWEEP_BATCH must be >= 1")
        return self

    @field_validator("ai_disabled_endpoints", mode="after")
    @classmethod
    def _disabled_endpoints_allowed(cls, value: list[str]) -> list[str]:
        names = sorted({str(v).strip().lower() for v in value if str(v).strip()})
        allowed = {"vision", "embedding", "reranker", "ocr"}
        bad = [n for n in names if n not in allowed]
        if bad:
            raise ValueError(
                f"AI_DISABLED_ENDPOINTS: {', '.join(bad)} cannot be disabled (allowed: vision, embedding, reranker, ocr)"
            )
        return names

    def ai_endpoint_disabled(self, name: str) -> bool:
        return name in self.ai_disabled_endpoints

    @property
    def whisper_endpoints(self) -> list[str]:
        return [self.whisper_base_url, *[u.rstrip("/") for u in self.whisper_extra_base_urls]]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
