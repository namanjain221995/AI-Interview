"""Python and SQL (SQLite) drivers; both run in the python runtime image."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ..compare import rows_equal
from ..models import CodeLanguage, ExecuteRequest
from ..signature import Signature
from .base import Driver, Plan, Rejected

PYTHON = ["python3", "-I", "-B"]


class PythonDriver(Driver):
    language = CodeLanguage.PYTHON
    runtime = "python"
    source_name = "solution.py"
    baseline_mb = 32
    memory_patterns: ClassVar[tuple[str, ...]] = ("MemoryError",)

    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return [{"path": self.source_name, "content": req.code}], []

    def compile_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any] | None:
        if req.mode == "tests":
            return None  # the harness compiles the module itself and reports syntax errors
        return {"argv": [*PYTHON, "/opt/sandbox/check.py", self.source_name, self.source_name]}

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        return {"argv": [*PYTHON, "-u", self.source_name], "stdin": req.stdin or ""}

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": [*PYTHON, "-u", "/opt/sandbox/harness.py"]}

    def harness_input(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        assert sig is not None
        return {
            "source": self.source_name,
            "entry_candidates": sig.entry_candidates(sig.snake_name),
            "params": sig.to_payload(),
            "return_type": sig.return_type,
        }


class SqlDriver(Driver):
    language = CodeLanguage.SQL
    runtime = "python"
    source_name = "query.sql"
    baseline_mb = 32
    memory_patterns: ClassVar[tuple[str, ...]] = ("MemoryError", "out of memory")
    uses_signature = False

    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return [{"path": self.source_name, "content": req.code}], []

    def validate_tests(self, req: ExecuteRequest, sig: Signature | None) -> None:
        super().validate_tests(req, sig)
        for test in req.tests:
            if test.expected_rows is None:
                raise Rejected(f"SQL test {test.id!r} has no expected_rows")

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        payload = {
            "mode": "run",
            "query_file": self.source_name,
            "setup": req.sql_setup or "",
            "max_rows": self.settings.sql_max_rows,
        }
        return {"argv": [*PYTHON, "-u", "/opt/sandbox/sql_harness.py"], "stdin": json.dumps(payload)}

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": [*PYTHON, "-u", "/opt/sandbox/sql_harness.py"]}

    def harness_input(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {
            "mode": "tests",
            "query_file": self.source_name,
            "setup": req.sql_setup or "",
            "max_rows": self.settings.sql_max_rows,
        }

    def test_item(self, index: int, req: ExecuteRequest) -> dict[str, Any]:
        return {"i": index, "setup_sql": req.tests[index].setup_sql or ""}

    def actual_value(self, outcome: dict[str, Any]) -> Any:
        actual = outcome.get("actual")
        return actual.get("rows") if isinstance(actual, dict) else actual

    def expected_value(self, req: ExecuteRequest, index: int) -> Any:
        return req.tests[index].expected_rows

    def is_correct(self, req: ExecuteRequest, plan: Plan, index: int, actual: Any) -> bool:
        test = req.tests[index]
        if not isinstance(actual, list) or test.expected_rows is None:
            return False
        return rows_equal(test.expected_rows, actual, test.ordered)
