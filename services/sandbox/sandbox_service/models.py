"""Wire contract of ``POST /v1/execute``.

Mirror of the authoritative models in
``apps/api/app/services/coding/contracts.py`` (and the ``CodeLanguage`` /
``RunStatus`` enums in ``apps/api/app/domain/enums.py``). The sandbox is a
separate deployable and cannot import the API package; the unit test
``test_contract_sync`` compares both JSON schemas so the copies cannot drift.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CodeLanguage(StrEnum):
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    JAVA = "java"
    GO = "go"
    CPP = "cpp"
    SQL = "sql"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    COMPILE_ERROR = "compile_error"
    TIMEOUT = "timeout"
    MEMORY_EXCEEDED = "memory_exceeded"
    REJECTED = "rejected"


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
