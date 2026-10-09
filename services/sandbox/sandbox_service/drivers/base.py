"""Language driver base: turns an ExecuteRequest into a runner job and the
runner's report back into an ExecuteResponse.

Status mapping (tests mode, highest first): compile_error (compiler/syntax
errors, missing entry point) > timeout > memory_exceeded > error (runtime
exceptions, crashes) > failed (wrong answers) > passed. In run mode a program
that exits 0 is ``passed``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..compare import values_equal
from ..config import Settings
from ..models import CodeLanguage, ExecuteRequest, ExecuteResponse, RunStatus, SandboxTestResult
from ..signature import Signature, SignatureError, parse_signature

WORKDIR = "/tmp/work"
MAX_RESTARTS = 3

SIGNAL_TEXT = {
    "SIGSEGV": "segmentation fault (invalid memory access or stack overflow)",
    "SIGABRT": "aborted",
    "SIGFPE": "arithmetic error (e.g. integer division by zero)",
    "SIGBUS": "bus error",
    "SIGILL": "illegal instruction",
    "SIGKILL": "killed",
    "SIGXFSZ": "file size limit exceeded",
    "SIGXCPU": "CPU time limit exceeded",
    "SIGPIPE": "broken pipe",
    "SIGTRAP": "trap",
}


class Rejected(Exception):
    """The request is well-formed but cannot be executed (status ``rejected``)."""


@dataclass(slots=True)
class Plan:
    runtime: str
    job: dict[str, Any]
    container_memory_mb: int
    run_memory_mb: int
    hard_deadline_s: float
    test_groups: list[tuple[str, list[int]]] = field(default_factory=list)
    signature: Signature | None = None
    limits: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContainerOutcome:
    runner: dict[str, Any] | None
    image: str
    container: str = ""
    exit_code: int | None = None
    oom_killed: bool = False
    deadline_exceeded: bool = False
    output_overflow: bool = False
    error: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)


def _ms(value: Any) -> float:
    return round(float(value), 3) if isinstance(value, (int, float)) else 0.0


class Driver:
    language: ClassVar[CodeLanguage]
    runtime: ClassVar[str]
    source_name: ClassVar[str]
    compile_floor_mb: ClassVar[int] = 0
    baseline_mb: ClassVar[int] = 32
    memory_patterns: ClassVar[tuple[str, ...]] = ()
    uses_signature: ClassVar[bool] = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------ hooks
    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        raise NotImplementedError

    def compile_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any] | None:
        return None

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        raise NotImplementedError

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        raise NotImplementedError

    def harness_input(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {}

    def test_item(self, index: int, req: ExecuteRequest) -> dict[str, Any]:
        return {"i": index, "args": req.tests[index].args or []}

    def env(self, req: ExecuteRequest) -> dict[str, str]:
        return {}

    def clean(self, text: str) -> str:
        return text.replace(f"{WORKDIR}/", "")

    # ----------------------------------------------------------- planning
    def signature_for(self, req: ExecuteRequest) -> Signature | None:
        if not self.uses_signature:
            return None
        try:
            return parse_signature(req.signature)
        except SignatureError as exc:
            raise Rejected(str(exc)) from exc

    def validate_tests(self, req: ExecuteRequest, sig: Signature | None) -> None:
        if not req.tests:
            raise Rejected("tests mode requires at least one test case")
        if sig is not None:
            for test in req.tests:
                if test.args is not None and len(test.args) != len(sig.params):
                    raise Rejected(f"test {test.id!r} has {len(test.args)} arguments; the signature declares {len(sig.params)}")

    def plan(self, req: ExecuteRequest) -> Plan:
        settings = self.settings
        sig = self.signature_for(req) if req.mode == "tests" else None
        if req.mode == "tests":
            self.validate_tests(req, sig)
        run_memory_mb = req.memory_limit_mb + self.baseline_mb
        container_memory_mb = max(run_memory_mb, self.compile_floor_mb) + settings.runtime_overhead_mb
        files, copies = self.files(req, sig)
        steps: list[dict[str, Any]] = []
        compile_step = self.compile_step(req, sig)
        if compile_step is not None:
            steps.append(
                {
                    "name": "compile",
                    **compile_step,
                    "timeout_ms": req.compile_time_limit_ms,
                    "stop_on_failure": True,
                    "capture": True,
                }
            )
        groups: list[tuple[str, list[int]]] = []
        if req.mode == "run":
            steps.append(
                {
                    "name": "run",
                    **self.run_step(req),
                    "timeout_ms": req.time_limit_ms,
                    "memory_limit_kb": run_memory_mb * 1024,
                    "capture": True,
                }
            )
        else:
            visible = [i for i, t in enumerate(req.tests) if not t.hidden]
            hidden = [i for i, t in enumerate(req.tests) if t.hidden]
            harness_input = self.harness_input(req, sig)
            for name, indices, capture in (("tests:visible", visible, True), ("tests:hidden", hidden, False)):
                if not indices:
                    continue
                steps.append(
                    {
                        "name": name,
                        **self.harness_step(req, sig),
                        "timeout_ms": req.time_limit_ms,
                        "memory_limit_kb": run_memory_mb * 1024,
                        # Output produced while hidden tests run is never returned (it could echo hidden inputs).
                        "capture": capture,
                        "harness": {
                            "input": harness_input,
                            "tests": [self.test_item(i, req) for i in indices],
                            "max_restarts": MAX_RESTARTS,
                        },
                    }
                )
                groups.append((name, indices))
        exec_steps = sum(1 for s in steps if s["name"] != "compile")
        compile_s = req.compile_time_limit_ms / 1000 if compile_step is not None else 0.0
        hard_deadline = compile_s + exec_steps * req.time_limit_ms / 1000 + settings.deadline_grace_seconds
        job = {
            "protocol": 1,
            "workdir": WORKDIR,
            "files": files,
            "copies": copies,
            "env": self.env(req),
            "steps": steps,
            "deadline_ms": int((hard_deadline - min(2.0, settings.deadline_grace_seconds / 2)) * 1000),
            "limits": {
                "capture_bytes": settings.output_capture_bytes,
                "output_hard_cap_bytes": settings.output_hard_cap_bytes,
                "results_cap_bytes": settings.results_cap_bytes,
                "record_cap_bytes": settings.record_cap_bytes,
                "stack_bytes": settings.stack_limit_mb * 1024 * 1024,
            },
        }
        limits = {
            "time_limit_ms": req.time_limit_ms,
            "compile_time_limit_ms": req.compile_time_limit_ms,
            "memory_limit_mb": req.memory_limit_mb,
            "process_memory_mb": run_memory_mb,
            "container_memory_mb": container_memory_mb,
            "cpus": settings.cpus,
            "pids": settings.pids_limit,
            "tmpfs_mb": settings.tmpfs_size_mb,
            "network": "none",
        }
        return Plan(self.runtime, job, container_memory_mb, run_memory_mb, hard_deadline, groups, sig, limits)

    # ------------------------------------------------------ interpretation
    def memory_in(self, text: str) -> bool:
        return any(p in text for p in self.memory_patterns)

    def preview(self, value: Any) -> tuple[Any, bool]:
        limit = self.settings.value_preview_bytes
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = repr(value)
        if len(text) <= limit:
            return value, False
        return f"{text[:limit]}... [truncated, {len(text)} characters]", True

    def interpret(self, req: ExecuteRequest, plan: Plan, outcome: ContainerOutcome) -> ExecuteResponse:
        meta: dict[str, Any] = {
            "runtime": plan.runtime,
            "image": outcome.image,
            "container": outcome.container,
            "limits": plan.limits,
            "timings_ms": dict(outcome.timings_ms),
        }
        response = ExecuteResponse(
            request_id=req.request_id, status=RunStatus.ERROR, total=len(req.tests) if req.mode == "tests" else 0, meta=meta
        )
        meta = response.meta  # pydantic copied the dict: keep writing to the response's own copy
        runner = outcome.runner
        if runner is None:
            return self._container_failure(req, response, outcome)
        if not runner.get("ok"):
            response.stderr = "The sandbox could not run this submission."
            meta["error"] = str(runner.get("error") or "runner failure")[:500]
            return response
        steps: dict[str, dict[str, Any]] = {str(s.get("name")): s for s in runner.get("steps", []) if isinstance(s, dict)}
        meta["timings_ms"]["runner"] = _ms(runner.get("runner_ms"))
        meta["steps"] = [
            {
                k: (_ms(s[k]) if k in ("wall_ms", "cpu_ms") else s[k])
                for k in (
                    "name",
                    "wall_ms",
                    "cpu_ms",
                    "max_rss_kb",
                    "exit_code",
                    "signal",
                    "timed_out",
                    "memory_exceeded",
                    "attempts",
                    "skipped",
                )
                if k in s
            }
            for s in runner.get("steps", [])
            if isinstance(s, dict)
        ]
        if runner.get("cgroup_peak_kb") is not None:
            meta["container_peak_kb"] = runner["cgroup_peak_kb"]

        compile_res = steps.get("compile")
        if compile_res is not None:
            meta["timings_ms"]["compile"] = _ms(compile_res.get("wall_ms"))
            if self._step_failed(compile_res):
                return self._compile_failure(req, response, compile_res)
        if req.mode == "run":
            return self._interpret_run(req, response, steps.get("run"), outcome)
        return self._interpret_tests(req, plan, response, steps, outcome)

    @staticmethod
    def _step_failed(step: dict[str, Any]) -> bool:
        return bool(
            step.get("skipped")
            or step.get("exit_code") != 0
            or step.get("timed_out")
            or step.get("memory_exceeded")
            or step.get("spawn_error")
        )

    def _container_failure(self, req: ExecuteRequest, response: ExecuteResponse, outcome: ContainerOutcome) -> ExecuteResponse:
        if outcome.deadline_exceeded:
            response.status = RunStatus.TIMEOUT
            response.stderr = "Time limit exceeded."
        elif outcome.oom_killed:
            response.status = RunStatus.MEMORY_EXCEEDED
            response.stderr = f"Memory limit exceeded ({req.memory_limit_mb} MB)."
        elif outcome.output_overflow:
            response.status = RunStatus.ERROR
            response.stderr = "Output limit exceeded."
            response.truncated = True
        else:
            response.status = RunStatus.ERROR
            response.stderr = "The program terminated the sandbox runner unexpectedly."
        response.meta["error"] = outcome.error or "no runner result"
        return response

    def _compile_failure(self, req: ExecuteRequest, response: ExecuteResponse, step: dict[str, Any]) -> ExecuteResponse:
        output = self.clean_compile_output(str(step.get("stdout", "")) + str(step.get("stderr", ""))).rstrip()
        if step.get("skipped"):
            output = "Compilation did not start: the time budget was exhausted."
        elif step.get("spawn_error"):
            output = f"The compiler could not be started: {step['spawn_error']}"
        elif step.get("timed_out"):
            output = (output + "\n" if output else "") + f"Compilation exceeded the {req.compile_time_limit_ms / 1000:g} s limit."
        elif step.get("memory_exceeded"):
            output = (output + "\n" if output else "") + "The compiler ran out of memory."
        response.status = RunStatus.COMPILE_ERROR
        response.compile_output = output or "Compilation failed."
        response.exit_code = step.get("exit_code")
        response.truncated = bool(step.get("truncated"))
        return response

    def clean_compile_output(self, text: str) -> str:
        return self.clean(text)

    def _notice(self, text: str, notice: str) -> str:
        text = text.rstrip("\n")
        return f"{text}\n{notice}" if text else notice

    def _interpret_run(
        self, req: ExecuteRequest, response: ExecuteResponse, step: dict[str, Any] | None, outcome: ContainerOutcome
    ) -> ExecuteResponse:
        if step is None or step.get("skipped"):
            response.status = RunStatus.TIMEOUT
            response.stderr = "Time limit exceeded before the program started."
            return response
        stdout = str(step.get("stdout", ""))  # program output is returned verbatim
        stderr = self.clean(str(step.get("stderr", "")))
        signal = step.get("signal")
        if step.get("spawn_error"):
            status = RunStatus.ERROR
            stderr = f"The program could not be started: {step['spawn_error']}"
        elif step.get("timed_out"):
            status = RunStatus.TIMEOUT
            stderr = self._notice(stderr, f"[sandbox] time limit exceeded ({req.time_limit_ms / 1000:g} s)")
        elif (
            step.get("memory_exceeded")
            or (step.get("oom_killed") and signal == "SIGKILL")
            or (step.get("exit_code") != 0 and self.memory_in(stderr))
        ):
            status = RunStatus.MEMORY_EXCEEDED
            stderr = self._notice(stderr, f"[sandbox] memory limit exceeded ({req.memory_limit_mb} MB)")
        elif step.get("output_limit_exceeded"):
            status = RunStatus.ERROR
            stderr = self._notice(stderr, "[sandbox] output limit exceeded; the program was stopped")
        elif step.get("exit_code") == 0:
            status = RunStatus.PASSED
        else:
            status = RunStatus.ERROR
            if signal:
                stderr = self._notice(stderr, f"[sandbox] terminated by {signal}: {SIGNAL_TEXT.get(signal, 'signal')}")
        response.status = status
        response.stdout = stdout
        response.stderr = stderr
        response.exit_code = step.get("exit_code")
        response.runtime_ms = round(_ms(step.get("wall_ms")))
        response.memory_kb = step.get("max_rss_kb") or None
        response.truncated = bool(step.get("truncated") or step.get("output_limit_exceeded"))
        response.meta["timings_ms"]["run"] = _ms(step.get("wall_ms"))
        return response

    # ------------------------------------------------------------------ tests
    def actual_value(self, outcome: dict[str, Any]) -> Any:
        return outcome.get("actual")

    def is_correct(self, req: ExecuteRequest, plan: Plan, index: int, actual: Any) -> bool:
        test = req.tests[index]
        sig = plan.signature
        comparison = sig.comparison if sig else "exact"
        tolerance = sig.tolerance if sig else 1e-6
        return values_equal(test.expected, actual, comparison, tolerance, sig.return_type if sig else None)

    def expected_value(self, req: ExecuteRequest, index: int) -> Any:
        return req.tests[index].expected

    def _test_error(self, outcome: dict[str, Any], req: ExecuteRequest) -> tuple[str, str]:
        kind = str(outcome.get("error_type") or "runtime")
        message = self.clean(str(outcome.get("error") or ""))
        if kind == "timeout":
            return "timeout", f"Time limit exceeded ({req.time_limit_ms / 1000:g} s for the test run)"
        if kind == "memory" or (kind == "crash" and self.memory_in(message)):
            detail = f": {message.splitlines()[0]}" if message and kind == "memory" and "limit" not in message else ""
            return "memory", f"Memory limit exceeded ({req.memory_limit_mb} MB){detail}"
        if kind == "not_run":
            reason = message.split(":", 1)[1].strip() if ":" in message else ""
            if reason == "time limit exceeded":
                return "timeout", "Not executed: an earlier test exhausted the time limit"
            return "error", f"Not executed: {reason or 'the test run stopped early'}"
        if kind == "output_limit":
            return "error", message or "Output limit exceeded"
        if kind == "crash":
            first, _, rest = message.partition("\n")
            for sig_name, text in SIGNAL_TEXT.items():
                if sig_name in first:
                    message = f"Process crashed ({sig_name}: {text})" + (f"\n{rest}" if rest else "")
                    break
            return "error", message or "Process crashed"
        if kind == "harness":
            return "error", message or "invalid test data"
        return "error", message or "runtime error"

    def _interpret_tests(
        self,
        req: ExecuteRequest,
        plan: Plan,
        response: ExecuteResponse,
        steps: dict[str, dict[str, Any]],
        outcome: ContainerOutcome,
    ) -> ExecuteResponse:
        results: dict[int, tuple[SandboxTestResult, bool]] = {}
        categories: set[str] = set()
        truncated = False
        runtime_total = 0.0
        max_rss = 0
        exit_code: int | None = None
        for name, indices in plan.test_groups:
            step = steps.get(name)
            hidden_group = name.endswith("hidden")
            if step is None or step.get("skipped"):
                categories.add("timeout")
                for i in indices:
                    results[i] = self._result(req, i, False, error="Not executed: the time budget was exhausted")
                continue
            runtime_total += _ms(step.get("wall_ms"))
            max_rss = max(max_rss, int(step.get("max_rss_kb") or 0))
            if exit_code is None or not hidden_group:
                exit_code = step.get("exit_code")
            if not hidden_group:
                response.stdout = str(step.get("stdout", ""))  # program output is returned verbatim
                response.stderr = self.clean(str(step.get("stderr", "")))
                truncated = truncated or bool(step.get("truncated"))
                response.meta["timings_ms"]["tests"] = _ms(step.get("wall_ms"))
            else:
                response.meta["timings_ms"]["hidden_tests"] = _ms(step.get("wall_ms"))
            fatal = step.get("fatal")
            if fatal:
                kind = str(fatal.get("kind") or "")
                message = self.clean_compile_output(str(fatal.get("message") or "")).rstrip()
                if fatal.get("t") == "compile_error" or kind in ("entry", "compile_error"):
                    response.status = RunStatus.COMPILE_ERROR
                    response.compile_output = message or "Compilation failed."
                    response.tests = []
                    response.passed = 0
                    return response
                if kind == "timeout":
                    categories.add("timeout")
                    error = f"Time limit exceeded ({req.time_limit_ms / 1000:g} s)"
                elif kind == "memory" or self.memory_in(message):
                    categories.add("memory")
                    error = f"Memory limit exceeded ({req.memory_limit_mb} MB)"
                else:
                    categories.add("error")
                    error = message or "The program failed before running the tests"
                    if not hidden_group:
                        response.stderr = self._notice(response.stderr, error)
                for i in indices:
                    results[i] = self._result(req, i, False, error=error)
                continue
            outcomes = {o.get("i"): o for o in step.get("tests", []) if isinstance(o, dict)}
            for i in indices:
                result = outcomes.get(i)
                if result is None:
                    categories.add("error")
                    results[i] = self._result(req, i, False, error="no result reported")
                    continue
                ms = result.get("ms")
                if result.get("ok"):
                    actual = self.actual_value(result)
                    try:
                        passed = self.is_correct(req, plan, i, actual)
                    except Exception:
                        passed = False
                    shown, cut = self.preview(actual)
                    truncated = truncated or cut
                    categories.add("passed" if passed else "failed")
                    results[i] = self._result(req, i, passed, actual=shown, runtime_ms=ms)
                else:
                    category, message = self._test_error(result, req)
                    categories.add(category)
                    results[i] = self._result(req, i, False, error=message, runtime_ms=ms)
        ordered = [results[i][0] for i in range(len(req.tests)) if i in results]
        truncated = truncated or any(results[i][1] for i in results)
        response.tests = ordered
        response.passed = sum(1 for r in ordered if r.passed)
        response.total = len(req.tests)
        response.exit_code = exit_code
        response.runtime_ms = round(runtime_total)
        response.memory_kb = max_rss or None
        response.truncated = truncated
        if "timeout" in categories:
            response.status = RunStatus.TIMEOUT
        elif "memory" in categories:
            response.status = RunStatus.MEMORY_EXCEEDED
        elif "error" in categories:
            response.status = RunStatus.ERROR
        elif response.passed == response.total and response.total > 0:
            response.status = RunStatus.PASSED
        else:
            response.status = RunStatus.FAILED
        return response

    def _result(
        self,
        req: ExecuteRequest,
        index: int,
        passed: bool,
        *,
        actual: Any = None,
        error: str | None = None,
        runtime_ms: Any = None,
    ) -> tuple[SandboxTestResult, bool]:
        test = req.tests[index]
        expected, cut = self.preview(self.expected_value(req, index))
        result = SandboxTestResult(
            id=test.id,
            passed=passed,
            actual=actual,
            expected=expected,
            error=error,
            runtime_ms=round(float(runtime_ms), 3)
            if isinstance(runtime_ms, (int, float)) and not isinstance(runtime_ms, bool)
            else None,
            hidden=test.hidden,
        )
        return result, cut


def strip_comments_c_like(code: str) -> str:
    """Blank out // and /* */ comments and string/char literal contents.

    Length- and line-preserving (every removed character becomes a space,
    newlines are kept), so match positions in the result are valid in ``code``.
    Used only for regex scans of candidate code, never for compilation.
    """
    out = list(code)
    i, n = 0, len(code)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        if code.startswith("//", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
            continue
        ch = code[i]
        if ch in ('"', "'", "`"):
            j = i + 1
            while j < n and code[j] != ch:
                if code[j] == "\\":
                    j += 1
                elif code[j] == "\n" and ch != "`":
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
            continue
        i += 1
    return "".join(out)


def find_declared(code: str, names: list[str], pattern: str) -> str | None:
    """First name in ``names`` for which ``pattern.format(name=...)`` matches the code."""
    for name in names:
        if re.search(pattern.format(name=re.escape(name)), code, re.MULTILINE):
            return name
    return None
