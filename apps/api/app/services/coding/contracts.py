"""Seam between the interview engine / realtime runtime and the coding
module (``app.services.coding``), plus the sandbox service wire contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import CodeLanguage, Difficulty, RunKind, RunStatus
from app.schemas.realtime import CodingRunResultMsg, CodingStateMsg

# =============================================================================
# Sandbox service HTTP contract (services/sandbox)
#
#   POST {SANDBOX_BASE_URL}/v1/execute     body: ExecuteRequest -> ExecuteResponse
#   GET  {SANDBOX_BASE_URL}/health         -> {"status": "ok", "runtimes": {...}}
#   GET  {SANDBOX_BASE_URL}/metrics        Prometheus
#
#   Auth: header X-Sandbox-Signature = hex(HMAC-SHA256(SANDBOX_SHARED_SECRET,
#         f"{X-Sandbox-Timestamp}.{raw_body}")), X-Sandbox-Timestamp = unix
#         seconds; requests older than 60 s are rejected.
# =============================================================================


class SandboxTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    args: list[Any] | None = None  # function problems (positional arguments)
    expected: Any = None
    setup_sql: str | None = None  # SQL problems (in addition to ExecuteRequest.sql_setup)
    expected_rows: list[list[Any]] | None = None
    ordered: bool = False
    hidden: bool = False


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    language: CodeLanguage
    code: str = Field(..., max_length=200_000)
    mode: Literal["run", "tests"]
    stdin: str | None = Field(None, max_length=100_000)
    signature: dict[str, Any] | None = None  # CodingQuestion.signature for "tests" mode
    tests: list[SandboxTestCase] = Field(default_factory=list, max_length=100)
    sql_setup: str | None = None
    time_limit_ms: int = Field(5000, ge=500, le=20000, description="Per execution (compile excluded)")
    compile_time_limit_ms: int = Field(15000, ge=1000, le=60000)
    memory_limit_mb: int = Field(256, ge=64, le=1024)


class SandboxTestResult(BaseModel):
    id: str
    passed: bool
    actual: Any = None
    expected: Any = None
    error: str | None = None
    runtime_ms: float | None = None
    hidden: bool = False


class ExecuteResponse(BaseModel):
    request_id: str
    status: RunStatus
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    exit_code: int | None = None
    runtime_ms: int | None = None
    memory_kb: int | None = None
    tests: list[SandboxTestResult] = Field(default_factory=list)
    passed: int = 0
    total: int = 0
    truncated: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)  # image, container id hash, limits applied


# =============================================================================
# Coding service (in-process) used by the engine and the realtime runtime
# =============================================================================


@dataclass(slots=True)
class CodingContext:
    """Everything the AI needs to discuss the candidate's code."""

    coding_session_id: str
    question_title: str
    prompt_markdown: str
    difficulty: Difficulty
    language: CodeLanguage
    current_code: str
    previous_code: str | None
    recent_diff: str | None
    snapshots_count: int
    runs: list[dict[str, Any]] = field(default_factory=list)  # recent runs summarized
    last_compile_error: str | None = None
    last_runtime_error: str | None = None
    hints_used: int = 0
    hints: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    time_limit_s: int = 1200
    seconds_since_last_edit: float = 0.0
    expected_concepts: list[str] = field(default_factory=list)
    optimal_complexity: dict[str, str] = field(default_factory=dict)
    candidate_explanations: list[str] = field(default_factory=list)  # transcript since coding began


@runtime_checkable
class CodingService(Protocol):
    async def select_question(
        self,
        session_id: str,
        *,
        competency_keys: list[str],
        difficulty: Difficulty,
        languages: list[CodeLanguage],
    ) -> str | None:
        """Pick a coding_questions id (pinned > retrieved > generated) not used before in this session."""
        ...

    async def start(
        self, session_id: str, coding_question_id: str, *, question_id: str | None, language: CodeLanguage
    ) -> CodingStateMsg:
        """Create the coding_sessions row and initial snapshot; returns the candidate-visible payload."""
        ...

    async def record_update(
        self, coding_session_id: str, *, code: str, language: CodeLanguage, version: int
    ) -> bool:
        """Persist a snapshot when the change is significant (returns True if stored)."""
        ...

    async def run(
        self,
        coding_session_id: str,
        *,
        kind: RunKind,
        code: str,
        language: CodeLanguage,
        stdin: str | None = None,
    ) -> CodingRunResultMsg:
        """Execute in the sandbox (visible tests for TEST, all tests for SUBMIT); never on the host."""
        ...

    async def request_hint(
        self, coding_session_id: str, *, requested_via: Literal["button", "voice"]
    ) -> tuple[int, str] | None:
        """Return (level, hint_text) or None when 3 hints are used. Records coding_hints."""
        ...

    async def finish(self, coding_session_id: str, *, reason: str) -> None:
        """Close the coding session (submitted / time_limit / moved_on) and enqueue coding.evaluate."""
        ...

    async def context(self, coding_session_id: str) -> CodingContext: ...

    async def state_payload(self, coding_session_id: str) -> CodingStateMsg:
        """Candidate-visible state for reconnects."""
        ...
