"""Batched execution of catalog reference solutions (and starters).

Function problems run in a handful of long-lived child processes instead of
one process per problem: every Python job of a batch runs in one ``python -I -S``
process (standard library only, like the sandbox image) and every JavaScript job in one ``node`` process (sharded across a few
processes for large batches). Each job (one problem x one code: reference or
starter) gets a fresh module namespace / ``vm`` context and a per-problem
timeout; results stream back one JSON line per job, so a crash or a hang only
costs the job that caused it (the parent kills the child, reports that job and
restarts the batch after it).

Semantics follow the sandbox harnesses (``services/sandbox/runtimes``): entry
point resolution (top-level function, ``Solution`` method), argument
conversion and value normalisation (tuples/sets -> arrays, non-finite floats
-> strings), and :func:`app.seed.loaders.compare_results` for comparisons.
SQL problems run on SQLite like the sandbox SQL harness (fresh in-memory
database per test, exactly one read-only statement) and, optionally, on
PostgreSQL inside a throwaway schema and restricted role, each test case in a
transaction that is rolled back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.enums import CodeLanguage
from app.seed.loaders import CodingQuestionSeed, compare_results, compare_sql_rows, to_lower_camel

#: Per-problem wall-clock budget for one code (all of its tests) in a runner.
DEFAULT_TIMEOUT_S = 10.0
#: The sandbox allows 5 s per execution; warn well before that.
SLOW_REFERENCE_S = 2.0
#: Jobs per runner process before the batch is sharded across processes.
JOBS_PER_SHARD = 250
_GRACE_S = 5.0
_POLL_S = 0.02
_PY_MEMORY_LIMIT = 4 * 1024**3
_SQL_MAX_ROWS = 10_000
_SHOWN = 160

RUN_LANGUAGES: tuple[CodeLanguage, ...] = (CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT)


# =============================================================================
# Results
# =============================================================================


@dataclass(slots=True)
class RunReport:
    """Problems found by running code, keyed by problem slug."""

    errors: dict[str, list[str]] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # run-wide notes (missing toolchains...)
    seconds: dict[str, float] = field(default_factory=dict)  # wall time per runner
    jobs: int = 0

    def error(self, slug: str, message: str) -> None:
        self.errors.setdefault(slug, []).append(message)

    def warn(self, slug: str, message: str) -> None:
        self.warnings.setdefault(slug, []).append(message)

    def merge(self, other: RunReport) -> None:
        for slug, messages in other.errors.items():
            self.errors.setdefault(slug, []).extend(messages)
        for slug, messages in other.warnings.items():
            self.warnings.setdefault(slug, []).extend(messages)
        self.notes.extend(n for n in other.notes if n not in self.notes)
        self.seconds.update(other.seconds)
        self.jobs += other.jobs


def show(value: Any, limit: int = _SHOWN) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_line(text: str | None, limit: int = 300) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    if not lines:
        return "unknown error"
    # Prefer the exception line (last line of a traceback).
    line = lines[-1].strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


# =============================================================================
# Child runner sources (standalone: no app imports)
# =============================================================================

_PY_RUNNER = r"""
import copy, json, math, os, signal, sys, time, traceback, types

class _Timeout(BaseException):
    pass

def _alarm(signum, frame):
    signal.setitimer(signal.ITIMER_REAL, 0.05)  # keep firing if user code swallows it
    raise _Timeout()

def _special(v):
    if math.isnan(v):
        return "NaN"
    return "Infinity" if v > 0 else "-Infinity"

def to_jsonable(value, depth=0):
    if depth > 200:
        raise ValueError("return value is nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else _special(value)
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [to_jsonable(v, depth + 1) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if hasattr(value, "tolist"):
        return to_jsonable(value.tolist(), depth + 1)
    if hasattr(value, "__iter__"):
        return [to_jsonable(v, depth + 1) for v in value]
    raise TypeError("cannot report a value of type " + type(value).__name__)

def _to_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value

def convert(value, type_name):
    if value is None:
        return None
    if type_name in ("int", "long"):
        return _to_int(value)
    if type_name == "float":
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    if type_name.endswith("[]") and isinstance(value, list):
        return [convert(v, type_name[:-2]) for v in value]
    return value

def describe(exc, filename):
    if isinstance(exc, SyntaxError):
        return "%s: %s (line %s)" % (type(exc).__name__, exc.msg, exc.lineno)
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == filename]
    where = " (line %d)" % frames[-1].lineno if frames else ""
    text = "".join(traceback.format_exception_only(type(exc), exc)).strip().splitlines()
    return (text[-1] if text else type(exc).__name__) + where

def resolve(namespace, candidates):
    for name in candidates:
        fn = namespace.get(name)
        if callable(fn) and not isinstance(fn, type):
            return lambda args, fn=fn: fn(*args)
    cls = namespace.get("Solution")
    if isinstance(cls, type):
        for name in candidates:
            if callable(getattr(cls, name, None)):
                return lambda args, cls=cls, name=name: getattr(cls(), name)(*args)
    return None

def run(job):
    filename = "<%s:%s>" % (job["slug"], job["kind"])
    result = {"id": job["id"], "status": "ok", "message": None, "cases": []}
    started = time.perf_counter()
    signal.setitimer(signal.ITIMER_REAL, job["timeout"])
    try:
        try:
            code = compile(job["code"], filename, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            result.update(status="compile_error", message=describe(exc, filename))
            return result
        module = types.ModuleType("solution")
        sys.modules["solution"] = module
        try:
            exec(code, module.__dict__)
        except _Timeout:
            raise
        except BaseException as exc:
            result.update(status="fatal", message=describe(exc, filename))
            return result
        entry = resolve(module.__dict__, job["candidates"])
        if entry is None:
            result.update(status="fatal", message="entry point %r is not defined" % job["candidates"][0])
            return result
        types_ = job["types"]
        for test in job["tests"]:
            args = [convert(a, types_[k]) if k < len(types_) else a for k, a in enumerate(copy.deepcopy(test["args"]))]
            t0 = time.perf_counter()
            try:
                value = entry(args)
                actual = to_jsonable(value)
                json.dumps(actual, allow_nan=False)
                result["cases"].append({"id": test["id"], "ok": True, "actual": actual,
                                        "ms": (time.perf_counter() - t0) * 1000})
            except _Timeout:
                raise
            except BaseException as exc:
                result["cases"].append({"id": test["id"], "ok": False, "error": describe(exc, filename)})
    except _Timeout:
        result.update(status="timeout", message="exceeded %.1f s" % job["timeout"])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        sys.modules.pop("solution", None)
        result["seconds"] = time.perf_counter() - started
    return result

def main():
    memory_limit, jobs_path, results_path = int(sys.argv[1]), sys.argv[-2], sys.argv[-1]
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    except (ImportError, ValueError, OSError):
        pass
    with open(jobs_path, encoding="utf-8") as fh:
        jobs = json.load(fh)
    signal.signal(signal.SIGALRM, _alarm)
    out = os.open(results_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    for job in jobs:
        try:
            result = run(job)
        except BaseException as exc:  # e.g. a timeout raised between two jobs
            result = {"id": job["id"], "status": "fatal", "message": repr(exc), "cases": [], "seconds": 0.0}
        line = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
        view = memoryview(line)
        while view:
            view = view[os.write(out, view):]
    os.close(out)
    sys.stdout.flush()
    os._exit(0)

main()
"""

_JS_RUNNER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [jobsPath, resultsPath] = process.argv.slice(-2);
const jobs = JSON.parse(fs.readFileSync(jobsPath, 'utf8'));
const out = fs.openSync(resultsPath, 'a');
const noop = () => {};
const quietConsole = { log: noop, info: noop, warn: noop, error: noop, debug: noop, trace: noop, dir: noop, table: noop };

const DRIVER = `
;(function () {
  const special = (n) => (Number.isNaN(n) ? 'NaN' : n > 0 ? 'Infinity' : '-Infinity');
  const toJsonable = (value, depth) => {
    if (depth > 200) throw new Error('return value is nested too deeply');
    if (value === undefined || value === null) return null;
    switch (typeof value) {
      case 'number': return Number.isFinite(value) ? value : special(value);
      case 'bigint': return Number.isSafeInteger(Number(value)) ? Number(value) : value.toString();
      case 'string': case 'boolean': return value;
      case 'function': case 'symbol': throw new Error('cannot report a value of type ' + typeof value);
      default: break;
    }
    if (typeof value.then === 'function') throw new Error('returned a Promise (reference solutions must be synchronous)');
    if (Array.isArray(value)) return value.map((v) => toJsonable(v, depth + 1));
    if (ArrayBuffer.isView(value)) return Array.from(value, (v) => toJsonable(v, depth + 1));
    if (value instanceof Set) return Array.from(value, (v) => toJsonable(v, depth + 1));
    if (value instanceof Map) { const o = {}; for (const [k, v] of value) o[String(k)] = toJsonable(v, depth + 1); return o; }
    if (typeof value[Symbol.iterator] === 'function') return Array.from(value, (v) => toJsonable(v, depth + 1));
    const o = {}; for (const k of Object.keys(value)) o[k] = toJsonable(value[k], depth + 1); return o;
  };
  const convert = (value, type) => {
    if (value === null || value === undefined) return value;
    if (type && type.endsWith('[]') && Array.isArray(value)) return value.map((v) => convert(v, type.slice(0, -2)));
    return value;
  };
  const describe = (e) => {
    if (!(e instanceof Error)) return 'Uncaught ' + String(e);
    const lines = String(e.stack || (e.name + ': ' + e.message)).split('\\n');
    const where = lines.slice(1).map((l) => /:(\\d+):\\d+\\)?$/.exec(l)).find(Boolean);
    return lines[0] + (where ? ' (line ' + (Number(where[1]) - 1) + ')' : '');
  };
  const entry = (function () { __RESOLVE__ return undefined; })();
  const job = JSON.parse(globalThis.__job);
  if (typeof entry !== 'function') { globalThis.__fatal = 'entry point ' + JSON.stringify(job.candidates[0]) + ' is not defined'; return; }
  for (const test of job.tests) {
    const args = test.args.map((a, k) => convert(a, job.types[k]));
    const t0 = Date.now();
    try {
      const value = entry(args);
      const actual = toJsonable(value, 0);
      globalThis.__cases.push(JSON.stringify({ id: test.id, ok: true, actual, ms: Date.now() - t0 }));
    } catch (e) {
      globalThis.__cases.push(JSON.stringify({ id: test.id, ok: false, error: describe(e) }));
    }
  }
})();
`;

function resolver(candidates) {
  const parts = [];
  for (const name of candidates) {
    parts.push(`try { if (typeof ${name} === 'function' && !/^class[\\s{]/.test(Function.prototype.toString.call(${name}))) return (args) => ${name}(...args); } catch (e) {}`);
  }
  for (const name of candidates) {
    parts.push(`try { if (typeof Solution === 'function' && Solution.prototype && typeof Solution.prototype.${name} === 'function') return (args) => new Solution().${name}(...args); } catch (e) {}`);
    parts.push(`try { if (typeof Solution === 'function' && typeof Solution.${name} === 'function') return (args) => Solution.${name}(...args); } catch (e) {}`);
    parts.push(`try { if (typeof module === 'object' && module && module.exports && typeof module.exports.${name} === 'function') return (args) => module.exports.${name}(...args); } catch (e) {}`);
  }
  return parts.join('\n');
}

for (const job of jobs) {
  const started = process.hrtime.bigint();
  const result = { id: job.id, status: 'ok', message: null, cases: [] };
  const sandbox = { console: quietConsole, module: { exports: {} }, __job: JSON.stringify(job), __cases: [], __fatal: null };
  sandbox.exports = sandbox.module.exports;
  const context = vm.createContext(sandbox);
  let script = null;
  try {
    script = new vm.Script('"use strict";\n' + job.code + '\n' + DRIVER.replace('__RESOLVE__', () => resolver(job.candidates)),
                           { filename: job.slug + '.' + job.kind + '.js' });
  } catch (e) {
    result.status = 'compile_error';
    result.message = String((e && e.name) || 'SyntaxError') + ': ' + String(e && e.message);
  }
  if (script) {
    try {
      script.runInContext(context, { timeout: Math.max(1, Math.round(job.timeout * 1000)) });
    } catch (e) {
      if (e && e.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT') {
        result.status = 'timeout';
        result.message = 'exceeded ' + job.timeout.toFixed(1) + ' s';
      } else {
        result.status = 'fatal';
        const lines = String((e && e.stack) || e).split('\n');
        result.message = lines[0];
      }
    }
    if (result.status === 'ok' && sandbox.__fatal) { result.status = 'fatal'; result.message = sandbox.__fatal; }
    result.cases = Array.from(sandbox.__cases, (s) => JSON.parse(s));
  }
  result.seconds = Number(process.hrtime.bigint() - started) / 1e9;
  fs.writeSync(out, JSON.stringify(result) + '\n');
}
fs.closeSync(out);
"""


# =============================================================================
# Batch execution
# =============================================================================


def node_binary() -> str | None:
    return shutil.which("node")


def _entry_candidates(function_name: str, language: CodeLanguage) -> list[str]:
    """Names to try, the convention's name first (as the sandbox does)."""
    snake = function_name
    camel = to_lower_camel(function_name)
    pascal = camel[:1].upper() + camel[1:]
    primary = snake if language == CodeLanguage.PYTHON else camel
    return list(dict.fromkeys([primary, snake, camel, pascal]))


@dataclass(slots=True)
class _Job:
    id: int
    problem: CodingQuestionSeed
    language: CodeLanguage
    kind: str  # "reference" | "starter"
    code: str

    def payload(self, timeout: float) -> dict[str, Any]:
        sig = self.problem.signature
        assert sig is not None
        return {
            "id": self.id,
            "slug": self.problem.slug,
            "kind": self.kind,
            "code": self.code,
            "candidates": _entry_candidates(sig.function_name, self.language),
            "types": [p.type for p in sig.params],
            "tests": [{"id": t.id, "args": t.args or []} for t in self.problem.tests],
            "timeout": timeout,
        }


def _node_command(node: str) -> list[str]:
    """``node`` with the sandbox's 16 MB JS stack when the OS stack can be
    raised to 64 MB first (a shell ``ulimit`` before ``exec``: no
    ``preexec_fn``, which is unsafe in threaded parents); plain ``node``
    otherwise."""
    flags = "--max-old-space-size=2048"
    script = (
        f'ulimit -s 65536 2>/dev/null && exec "$0" --stack-size=16000 {flags} "$@"; exec "$0" {flags} "$@"'
    )
    return ["/bin/sh", "-c", script, node]


def _read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = data[: end + 1]
    return chunk.decode("utf-8", "replace").splitlines(), offset + len(chunk)


def _kill(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def _run_shard(
    command: list[str],
    jobs: list[dict[str, Any]],
    workdir: Path,
    timeout: float,
    label: str,
) -> dict[int, dict[str, Any]]:
    """Run ``jobs`` in one child (restarting after a crash or hang); returns
    results by job id."""
    results: dict[int, dict[str, Any]] = {}
    remaining = list(jobs)
    attempt = 0
    while remaining:
        attempt += 1
        jobs_path = workdir / f"{label}-{attempt}.jobs.json"
        results_path = workdir / f"{label}-{attempt}.results.jsonl"
        stderr_path = workdir / f"{label}-{attempt}.stderr"
        jobs_path.write_text(json.dumps(remaining), encoding="utf-8")
        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(  # noqa: S603 - fixed interpreter + generated runner
                [*command, str(jobs_path), str(results_path)],
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
            )
            offset = 0
            done_here = 0
            last_progress = time.monotonic()
            hung = False
            while True:
                exited = proc.poll() is not None
                lines, offset = _read_new_lines(results_path, offset)
                for line in lines:
                    with contextlib.suppress(json.JSONDecodeError):
                        record = json.loads(line)
                        results[int(record["id"])] = record
                        done_here += 1
                        last_progress = time.monotonic()
                if exited:
                    break
                if time.monotonic() - last_progress > timeout + _GRACE_S:
                    hung = True
                    _kill(proc)
                    lines, offset = _read_new_lines(results_path, offset)
                    for line in lines:
                        with contextlib.suppress(json.JSONDecodeError):
                            record = json.loads(line)
                            results[int(record["id"])] = record
                    break
                time.sleep(_POLL_S)
        remaining = [job for job in remaining if job["id"] not in results]
        if not remaining:
            break
        culprit = remaining.pop(0)
        if hung:
            status, message = "timeout", f"killed after {timeout + _GRACE_S:.0f} s without finishing"
        else:
            tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            status, message = "crashed", f"runner exited ({proc.returncode}): {_first_line(tail)}"
        results[culprit["id"]] = {"id": culprit["id"], "status": status, "message": message, "cases": []}
    return results


def _shards(jobs: list[dict[str, Any]], count: int) -> list[list[dict[str, Any]]]:
    count = max(1, min(count, len(jobs)))
    # Round-robin keeps heavy and light problems mixed across shards.
    return [jobs[i::count] for i in range(count)]


def _shard_count(jobs: int, workers: int | None) -> int:
    if workers is not None:
        return max(1, workers)
    limit = max(1, (os.cpu_count() or 2) // 4)
    return max(1, min(limit, math.ceil(jobs / JOBS_PER_SHARD)))


_MAX_LINES_PER_JOB = 5


def _ids(ids: Sequence[str]) -> str:
    return ", ".join(ids) if len(ids) <= 4 else f"{', '.join(ids[:3])} (+{len(ids) - 3} more)"


def _check_cases(job: _Job, record: dict[str, Any], report: RunReport) -> None:
    problem = job.problem
    sig = problem.signature
    assert sig is not None
    tag = f"[{job.language.value} {job.kind}]"
    status = record.get("status")
    if status != "ok":
        what = {
            "compile_error": "does not compile",
            "fatal": "fails to load",
            "timeout": "timed out",
            "crashed": "crashed the runner",
        }.get(str(status), str(status))
        report.error(problem.slug, f"{tag} {what}: {record.get('message') or 'no details'}")
        if status != "timeout" or not record.get("cases"):
            return
    by_id = {str(case.get("id")): case for case in record.get("cases") or []}
    passed = 0
    raised: dict[str, list[str]] = {}  # error text -> test ids
    mismatches: list[str] = []
    missing: list[str] = []
    for test in problem.tests:
        case = by_id.get(test.id)
        if case is None:
            missing.append(test.id)
            continue
        if not case.get("ok"):
            raised.setdefault(str(case.get("error")), []).append(test.id)
            continue
        if compare_results(
            case.get("actual"),
            test.expected,
            return_type=sig.return_type,
            comparison=sig.comparison,
            tolerance=sig.tolerance,
        ):
            passed += 1
        elif job.kind == "reference":
            mismatches.append(
                f"{tag} {test.id}: expected {show(test.expected)}, got {show(case.get('actual'))}"
                + (f" ({test.description})" if test.description else "")
            )
    messages = [f"{tag} {_ids(ids)}: raised {error}" for error, ids in raised.items()]
    if job.kind == "reference":
        messages += mismatches
        if missing and status == "ok":
            messages.append(f"{tag} {_ids(missing)}: no result")
    elif not messages and status == "ok" and passed == len(problem.tests):
        messages.append(f"{tag} the starter already passes every test (add discriminating tests)")
    for message in messages[:_MAX_LINES_PER_JOB]:
        report.error(problem.slug, message)
    if len(messages) > _MAX_LINES_PER_JOB:
        report.error(problem.slug, f"{tag} ... and {len(messages) - _MAX_LINES_PER_JOB} more failing tests")
    seconds = float(record.get("seconds") or 0.0)
    if job.kind == "reference" and status == "ok" and seconds > SLOW_REFERENCE_S:
        report.warn(
            problem.slug,
            f"{tag} slow: {seconds:.2f} s for {len(problem.tests)} tests (the sandbox allows 5 s per run)",
        )


def run_function_problems(
    problems: Sequence[CodingQuestionSeed],
    *,
    languages: Iterable[CodeLanguage] = RUN_LANGUAGES,
    starters: bool = True,
    timeout: float = DEFAULT_TIMEOUT_S,
    workers: int | None = None,
) -> RunReport:
    """Run every reference solution (and starter) of ``problems`` in
    ``languages`` (Python and JavaScript are supported) against all tests."""
    report = RunReport()
    function_problems = [p for p in problems if not p.is_sql and p.signature is not None]
    if not function_problems:
        return report
    wanted = [CodeLanguage(lang) for lang in languages]
    plans: list[tuple[CodeLanguage, list[str], str]] = []
    if CodeLanguage.PYTHON in wanted:
        plans.append((CodeLanguage.PYTHON, [sys.executable, "-I", "-S", "-B", "-X", "utf8"], _PY_RUNNER))
    if CodeLanguage.JAVASCRIPT in wanted:
        node = node_binary()
        if node is None:
            report.notes.append("javascript references were not run: node is not installed")
        else:
            plans.append((CodeLanguage.JAVASCRIPT, _node_command(node), _JS_RUNNER))
    jobs_by_id: dict[int, _Job] = {}
    batches: list[tuple[CodeLanguage, list[str], str, list[dict[str, Any]]]] = []
    for language, command, source in plans:
        payloads = []
        for problem in function_problems:
            codes = [("reference", problem.reference_solutions.get(language))]
            if starters:
                codes.append(("starter", problem.starter_code.get(language)))
            for kind, code in codes:
                if not code:
                    continue
                job = _Job(len(jobs_by_id), problem, language, kind, code)
                jobs_by_id[job.id] = job
                payloads.append(job.payload(timeout))
        if payloads:
            batches.append((language, command, source, payloads))
    report.jobs = len(jobs_by_id)
    with tempfile.TemporaryDirectory(prefix="seed-runner-") as tmp:
        workdir = Path(tmp)
        tasks: list[tuple[CodeLanguage, list[str], list[dict[str, Any]], str]] = []
        for language, command, source, payloads in batches:
            script = workdir / ("runner.py" if language == CodeLanguage.PYTHON else "runner.js")
            script.write_text(source, encoding="utf-8")
            extra = [str(_PY_MEMORY_LIMIT)] if language == CodeLanguage.PYTHON else []
            shards = _shards(payloads, _shard_count(len(payloads), workers))
            for index, shard in enumerate(shards):
                tasks.append((language, [*command, str(script), *extra], shard, f"{language.value}-{index}"))
        started: dict[CodeLanguage, float] = {}
        finished: dict[CodeLanguage, float] = {}
        lock = threading.Lock()

        def execute(
            task: tuple[CodeLanguage, list[str], list[dict[str, Any]], str],
        ) -> dict[int, dict[str, Any]]:
            language, command, shard, label = task
            with lock:
                started.setdefault(language, time.perf_counter())
            out = _run_shard(command, shard, workdir, timeout, label)
            with lock:
                finished[language] = time.perf_counter()
            return out

        records: dict[int, dict[str, Any]] = {}
        if tasks:
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                for result in pool.map(execute, tasks):
                    records.update(result)
        for language, begin in started.items():
            report.seconds[language.value] = round(finished.get(language, begin) - begin, 3)
    for job_id, job in jobs_by_id.items():
        record = records.get(job_id) or {"status": "crashed", "message": "no result", "cases": []}
        _check_cases(job, record, report)
    return report


# =============================================================================
# SQL
# =============================================================================

_DENIED_SQLITE_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_READ_ONLY_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, _SQLITE_RECURSIVE}


def _strip_sql_comments(sql: str) -> str:
    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote and quote != "]":
                    out.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "[":
            quote = "]"
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_sql_statements(sql: str) -> list[str]:
    """Statements of ``sql`` (comments removed), like the sandbox SQL harness."""
    statements: list[str] = []
    buffer: list[str] = []
    for ch in _strip_sql_comments(sql):
        buffer.append(ch)
        if ch == ";":
            text = "".join(buffer)
            if sqlite3.complete_statement(text):
                if text.strip().rstrip(";").strip():
                    statements.append(text.strip())
                buffer = []
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def single_statement(sql: str) -> str:
    """The one statement of a submission (trailing ``;`` removed); raises
    ``ValueError`` like the sandbox when there is not exactly one."""
    statements = split_sql_statements(sql)
    if len(statements) != 1:
        raise ValueError(f"submit exactly one SELECT statement (found {len(statements)})")
    return statements[0].rstrip().rstrip(";")


def _read_only_authorizer(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_SQLITE_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    if action in _READ_ONLY_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_sql_sqlite(
    problem: CodingQuestionSeed, query: str, *, timeout: float = 5.0
) -> list[tuple[str, list[list[Any]] | None, str | None]]:
    """``(test_id, rows, error)`` for ``query`` on a fresh in-memory SQLite
    database per test (problem setup + test setup), read-only like the sandbox."""
    try:
        statement = single_statement(query)
    except ValueError as exc:
        return [(test.id, None, str(exc)) for test in problem.tests]
    results: list[tuple[str, list[list[Any]] | None, str | None]] = []
    for test in problem.tests:
        conn = sqlite3.connect(":memory:", isolation_level=None)
        try:
            try:
                if problem.sql_setup:
                    conn.executescript(problem.sql_setup)
                if test.setup_sql:
                    conn.executescript(test.setup_sql)
            except sqlite3.Error as exc:
                results.append((test.id, None, f"setup SQL failed: {exc}"))
                continue
            conn.set_authorizer(_read_only_authorizer)

            def interrupted(deadline: float = time.monotonic() + timeout) -> int:
                return int(time.monotonic() > deadline)

            conn.set_progress_handler(interrupted, 10_000)
            try:
                cursor = conn.execute(statement)
                rows = cursor.fetchmany(_SQL_MAX_ROWS + 1)
            except sqlite3.Error as exc:
                message = str(exc)
                if "interrupted" in message:
                    message = f"exceeded {timeout:.1f} s"
                elif "not authorized" in message:
                    message = "only read-only SELECT statements are allowed"
                results.append((test.id, None, message))
                continue
            if len(rows) > _SQL_MAX_ROWS:
                results.append((test.id, None, f"returned more than {_SQL_MAX_ROWS} rows"))
                continue
            results.append((test.id, [list(r) for r in rows], None))
        finally:
            conn.close()
    return results


def sql_failures(
    problem: CodingQuestionSeed, results: Sequence[tuple[str, Any, str | None]], tag: str
) -> list[str]:
    by_id = {test.id: test for test in problem.tests}
    out: list[str] = []
    for test_id, rows, error in results:
        test = by_id[test_id]
        if error is not None:
            out.append(f"{tag} {test_id}: {error}")
        elif not compare_sql_rows(rows, test.expected_rows or [], ordered=test.ordered):
            out.append(
                f"{tag} {test_id}: expected rows {show(test.expected_rows)}, got {show(rows)}"
                + (f" ({test.description})" if test.description else "")
            )
    return out


def run_sql_problems_sqlite(
    problems: Sequence[CodingQuestionSeed], *, starters: bool = True, timeout: float = 5.0
) -> RunReport:
    report = RunReport()
    started = time.perf_counter()
    for problem in problems:
        if not problem.is_sql:
            continue
        reference = problem.reference_solutions.get(CodeLanguage.SQL, "")
        report.jobs += 1
        for message in sql_failures(
            problem, run_sql_sqlite(problem, reference, timeout=timeout), "[sqlite reference]"
        ):
            report.error(problem.slug, message)
        starter = problem.starter_code.get(CodeLanguage.SQL)
        if starters and starter:
            report.jobs += 1
            results = run_sql_sqlite(problem, starter, timeout=timeout)
            errors = [f"[sqlite starter] {tid}: {error}" for tid, _, error in results if error]
            for message in errors[:3]:
                report.error(problem.slug, message)
    report.seconds["sqlite"] = round(time.perf_counter() - started, 3)
    return report


# PostgreSQL ------------------------------------------------------------------

_PG_DENIED = re.compile(
    r"\b(?:copy|program|lo_import|lo_export|dblink\w*|pg_read\w*|pg_ls_dir|pg_stat_file|pg_sleep\w*|"
    r"set_config|pg_terminate_backend|pg_cancel_backend|grant|revoke|vacuum|listen|notify)\b"
    r"|\balter\s+(?:system|role|user|database)\b|\bcreate\s+(?:extension|function|procedure|role|user|database|"
    r"trigger|rule|event|server|publication|subscription)\b|\bdo\s+\$|\b(?:set|reset)\s+(?:role|session)\b"
    r"|\bsecurity\s+definer\b|\b(?:public|pg_catalog|information_schema)\s*\.",
    re.IGNORECASE,
)


def postgres_refusal(*sql: str | None) -> str | None:
    """Why the SQL must not run on the shared PostgreSQL server (``None`` = ok)."""
    for text in sql:
        if not text:
            continue
        match = _PG_DENIED.search(_strip_sql_comments(text))
        if match:
            return f"refusing to run on PostgreSQL: {match.group(0)!r} is not allowed in problem SQL"
    return None


def _pg_ident(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


async def _pg_connect(database_url: str | None = None) -> Any:
    import asyncpg
    from sqlalchemy.engine import make_url

    from app.core.config import Settings

    url = make_url(database_url or Settings().database_url)  # type: ignore[call-arg]
    return await asyncio.wait_for(
        asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
            command_timeout=60,
        ),
        timeout=10,
    )


async def run_sql_problems_postgres_async(
    problems: Sequence[CodingQuestionSeed],
    *,
    starters: bool = True,
    timeout: float = 5.0,  # noqa: ASYNC109 - per-statement statement_timeout, not a call budget
    database_url: str | None = None,
) -> RunReport:
    """Run SQL references (and starters) on the configured PostgreSQL server
    (``database_url`` or ``DATABASE_URL``; credentials are never printed) inside a throwaway schema
    owned by a throwaway NOLOGIN role. Every test case runs in its own
    transaction (``SET LOCAL ROLE``/``search_path``/``statement_timeout``) that
    is always rolled back; the schema and role are dropped afterwards."""
    report = RunReport()
    sql_problems = [p for p in problems if p.is_sql]
    if not sql_problems:
        return report
    started = time.perf_counter()
    try:
        conn = await _pg_connect(database_url)
    except Exception as exc:  # noqa: BLE001 - reported, never with the DSN
        report.notes.append(f"postgres checks skipped: cannot connect ({type(exc).__name__})")
        return report
    schema = _pg_ident("seedcheck")
    role: str | None = _pg_ident("seedcheck")
    try:
        try:
            await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
            await conn.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{role}"')
        except Exception:  # noqa: BLE001 - no CREATEROLE: keep the denylist only
            role = None
            report.notes.append("postgres checks run without a restricted role (no CREATEROLE privilege)")
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        timeout_ms = int(timeout * 1000)
        for problem in sql_problems:
            codes = [("reference", problem.reference_solutions.get(CodeLanguage.SQL, ""))]
            if starters and problem.starter_code.get(CodeLanguage.SQL):
                codes.append(("starter", problem.starter_code[CodeLanguage.SQL]))
            for kind, code in codes:
                report.jobs += 1
                tag = f"[postgres {kind}]"
                refusal = postgres_refusal(problem.sql_setup, code, *(t.setup_sql for t in problem.tests))
                if refusal:
                    report.error(problem.slug, f"{tag} {refusal}")
                    continue
                try:
                    statement = single_statement(code)
                except ValueError as exc:
                    report.error(problem.slug, f"{tag} {exc}")
                    continue
                results: list[tuple[str, list[list[Any]] | None, str | None]] = []
                for test in problem.tests:
                    transaction = conn.transaction()
                    await transaction.start()
                    try:
                        await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                        await conn.execute(f'SET LOCAL search_path TO "{schema}"')
                        if role:
                            await conn.execute(f'SET LOCAL ROLE "{role}"')
                        try:
                            if problem.sql_setup:
                                await conn.execute(problem.sql_setup)
                            if test.setup_sql:
                                await conn.execute(test.setup_sql)
                        except Exception as exc:  # noqa: BLE001 - asyncpg error classes vary
                            results.append((test.id, None, f"setup SQL failed: {_first_line(str(exc))}"))
                            continue
                        try:
                            records = await conn.fetch(statement)
                        except Exception as exc:  # noqa: BLE001
                            results.append((test.id, None, _first_line(str(exc))))
                            continue
                        results.append((test.id, [list(r.values()) for r in records], None))
                    finally:
                        await transaction.rollback()
                if kind == "reference":
                    for message in sql_failures(problem, results, tag):
                        report.error(problem.slug, message)
                else:
                    for test_id, _, error in results:
                        if error:
                            report.error(problem.slug, f"{tag} {test_id}: {error}")
    finally:
        with contextlib.suppress(Exception):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        if role:
            with contextlib.suppress(Exception):
                await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
        await conn.close()
    report.seconds["postgres"] = round(time.perf_counter() - started, 3)
    return report


def run_sql_problems_postgres(
    problems: Sequence[CodingQuestionSeed],
    *,
    starters: bool = True,
    timeout: float = 5.0,
    database_url: str | None = None,
) -> RunReport:
    return asyncio.run(
        run_sql_problems_postgres_async(
            problems, starters=starters, timeout=timeout, database_url=database_url
        )
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "RUN_LANGUAGES",
    "SLOW_REFERENCE_S",
    "RunReport",
    "node_binary",
    "postgres_refusal",
    "run_function_problems",
    "run_sql_problems_postgres",
    "run_sql_problems_postgres_async",
    "run_sql_problems_sqlite",
    "run_sql_sqlite",
    "show",
    "single_statement",
    "split_sql_statements",
    "sql_failures",
]
