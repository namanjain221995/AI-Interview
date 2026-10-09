"""Function-call harness for Python submissions.

Runs as the untrusted process. Reads ``{"source", "entry_candidates",
"params", "return_type", "tests": [{"i", "args"}]}`` from stdin, loads the
candidate module, calls the entry point once per test and writes one JSON
record per event to fd 3. Expected values are never given to this process:
the sandbox service compares the returned values.
"""

import io
import json
import math
import os
import sys
import time
import traceback
import types

RESULTS_FD = 3
SOURCE_NAME = "solution.py"


class Channel:
    def __init__(self) -> None:
        self._fd = RESULTS_FD

    def emit(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]


def _special_float(value: float):
    if math.isnan(value):
        return "NaN"
    return "Infinity" if value > 0 else "-Infinity"


def to_jsonable(value, depth: int = 0):
    if depth > 200:
        raise ValueError("return value is nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else _special_float(value)
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
    raise TypeError(f"cannot report a value of type {type(value).__name__}")


def _to_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def convert(value, type_name: str):
    if value is None:
        return None
    if type_name in ("int", "long"):
        return _to_int(value)
    if type_name == "float":
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    if type_name.endswith("[]"):
        inner = type_name[:-2]
        if isinstance(value, list):
            return [convert(v, inner) for v in value]
    return value


def format_exception(exc: BaseException) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == SOURCE_NAME]
    lines = []
    if frames:
        lines.append("Traceback (most recent call last):\n")
        lines.extend(traceback.format_list(frames))
    lines.extend(traceback.format_exception_only(type(exc), exc))
    return "".join(lines).rstrip()


def resolve_entry(module: types.ModuleType, candidates: list[str]):
    namespace = vars(module)
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


def main() -> int:
    channel = Channel()
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    # Candidate code gets an empty stdin in test mode.
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdin = io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8")

    source_path = payload.get("source", SOURCE_NAME)
    with open(source_path, "rb") as fh:
        source = fh.read()
    try:
        code = compile(source, SOURCE_NAME, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        channel.emit({"t": "compile_error", "message": "".join(traceback.format_exception_only(type(exc), exc))})
        return 1

    module = types.ModuleType("solution")
    module.__file__ = SOURCE_NAME
    sys.modules["solution"] = module
    try:
        exec(code, module.__dict__)  # noqa: S102 - this *is* the sandboxed candidate code
    except BaseException as exc:  # noqa: BLE001
        channel.emit({"t": "fatal", "kind": "runtime", "message": format_exception(exc)})
        return 1

    candidates = [c for c in payload.get("entry_candidates", []) if isinstance(c, str)]
    entry = resolve_entry(module, candidates)
    if entry is None:
        wanted = candidates[0] if candidates else "?"
        channel.emit({"t": "fatal", "kind": "entry", "message": f"Function '{wanted}' is not defined in {SOURCE_NAME}"})
        return 1

    params = [p.get("type", "") for p in payload.get("params", [])]
    channel.emit({"t": "ready"})
    for test in payload.get("tests", []):
        index = test["i"]
        args = test.get("args") or []
        channel.emit({"t": "begin", "i": index})
        try:
            converted = [convert(a, params[k]) if k < len(params) else a for k, a in enumerate(args)]
        except Exception as exc:  # noqa: BLE001
            channel.emit({"t": "result", "i": index, "ok": False, "error_type": "harness", "error": f"bad arguments: {exc}"})
            continue
        started = time.perf_counter()
        try:
            value = entry(converted)
            elapsed = (time.perf_counter() - started) * 1000.0
        except MemoryError:
            channel.emit({"t": "result", "i": index, "ok": False, "error_type": "memory", "error": "MemoryError"})
            continue
        except BaseException as exc:  # noqa: BLE001 - SystemExit inside a function is a failure too
            elapsed = (time.perf_counter() - started) * 1000.0
            channel.emit(
                {"t": "result", "i": index, "ok": False, "error_type": "runtime", "error": format_exception(exc), "ms": elapsed}
            )
            continue
        try:
            actual = to_jsonable(value)
            channel.emit({"t": "result", "i": index, "ok": True, "actual": actual, "ms": elapsed})
        except Exception as exc:  # noqa: BLE001
            channel.emit(
                {"t": "result", "i": index, "ok": False, "error_type": "runtime", "error": f"invalid return value: {exc}"}
            )
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    channel.emit({"t": "done"})
    return 0


if __name__ == "__main__":
    code = main()
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)
