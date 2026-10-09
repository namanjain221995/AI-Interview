"""SQL harness: runs a candidate's query against fresh in-memory SQLite databases.

tests mode  stdin ``{"mode": "tests", "query_file", "setup", "max_rows",
            "tests": [{"i", "setup_sql"}]}``; for every test a new in-memory
            database is built from ``setup`` + the test's ``setup_sql``, the
            candidate's single SELECT runs under a read-only authorizer and
            the rows are reported on fd 3 (the service compares them).
run mode    stdin ``{"mode": "run", "query_file", "setup", "max_rows"}``;
            every statement runs in order and result sets are printed as
            text tables on stdout.
"""

import json
import os
import sqlite3
import sys
import time

RESULTS_FD = 3
SOURCE_NAME = "query.sql"
DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
READ_ONLY_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, SQLITE_RECURSIVE}


def emit(record: dict) -> None:
    data = (json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    view = memoryview(data)
    while view:
        view = view[os.write(RESULTS_FD, view) :]


def strip_comments(sql: str) -> str:
    out = []
    i, n = 0, len(sql)
    quote = None
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


def split_statements(sql: str) -> list[str]:
    statements, buf = [], []
    for ch in strip_comments(sql):
        buf.append(ch)
        if ch == ";":
            text = "".join(buf)
            if sqlite3.complete_statement(text):
                if text.strip().rstrip(";").strip():
                    statements.append(text.strip())
                buf = []
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def read_only_authorizer(action, arg1, arg2, dbname, source):
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    if action in READ_ONLY_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def cell(value):
    if isinstance(value, bytes):
        return "x'" + value.hex() + "'"
    return value


def fresh_db(setup: str, extra: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    if setup:
        conn.executescript(setup)
    if extra:
        conn.executescript(extra)
    return conn


def describe_error(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    if "not authorized" in message:
        return "Only read-only SELECT queries are allowed (statement not authorized)"
    return f"{type(exc).__name__}: {message}"


def run_tests(payload: dict, query: str) -> int:
    statements = split_statements(query)
    if len(statements) != 1:
        emit({"t": "compile_error", "message": f"Submit exactly one SELECT statement (found {len(statements)})."})
        return 1
    statement = statements[0].rstrip().rstrip(";")
    setup = payload.get("setup") or ""
    max_rows = int(payload.get("max_rows", 10000))
    tests = payload.get("tests", [])
    if tests:
        try:
            probe = fresh_db(setup, tests[0].get("setup_sql"))
        except sqlite3.Error as exc:
            emit({"t": "fatal", "kind": "harness", "message": f"problem setup SQL failed: {exc}"})
            return 1
        probe.set_authorizer(read_only_authorizer)
        try:
            probe.execute("EXPLAIN " + statement).fetchall()
        except sqlite3.Error as exc:
            emit({"t": "compile_error", "message": describe_error(exc)})
            return 1
        finally:
            probe.close()
    emit({"t": "ready"})
    for test in tests:
        index = test["i"]
        emit({"t": "begin", "i": index})
        try:
            conn = fresh_db(setup, test.get("setup_sql"))
        except sqlite3.Error as exc:
            emit({"t": "result", "i": index, "ok": False, "error_type": "harness", "error": f"test setup SQL failed: {exc}"})
            continue
        conn.set_authorizer(read_only_authorizer)
        started = time.perf_counter()
        try:
            cursor = conn.execute(statement)
            rows = cursor.fetchmany(max_rows + 1)
            columns = [d[0] for d in cursor.description or []]
        except sqlite3.Error as exc:
            emit({"t": "result", "i": index, "ok": False, "error_type": "runtime", "error": describe_error(exc)})
            conn.close()
            continue
        elapsed = (time.perf_counter() - started) * 1000.0
        conn.close()
        if len(rows) > max_rows:
            emit(
                {
                    "t": "result",
                    "i": index,
                    "ok": False,
                    "error_type": "runtime",
                    "error": f"query returned more than {max_rows} rows",
                }
            )
            continue
        actual = {"columns": columns, "rows": [[cell(v) for v in row] for row in rows]}
        emit({"t": "result", "i": index, "ok": True, "actual": actual, "ms": elapsed})
    emit({"t": "done"})
    return 0


def format_table(columns: list[str], rows: list[tuple]) -> str:
    def show(v) -> str:
        if v is None:
            return "NULL"
        text = cell(v) if isinstance(v, bytes) else v
        text = str(text).replace("\n", "\\n")
        return text if len(text) <= 60 else text[:57] + "..."

    body = [[show(v) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in body:
        for k, v in enumerate(row):
            widths[k] = max(widths[k], len(v))
    line = lambda vals: " | ".join(v.ljust(widths[k]) for k, v in enumerate(vals)).rstrip()  # noqa: E731
    out = [line(columns), "-+-".join("-" * w for w in widths)]
    out.extend(line(r) for r in body)
    out.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(out)


def run_script(payload: dict, query: str) -> int:
    max_rows = int(payload.get("max_rows", 10000))
    try:
        conn = fresh_db(payload.get("setup") or "", None)
    except sqlite3.Error as exc:
        sys.stderr.write(f"problem setup SQL failed: {exc}\n")
        return 1
    conn.set_authorizer(
        lambda action, a1, a2, db, src: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION
            and (a2 or "").lower() in DENIED_FUNCTIONS
            or action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
            else sqlite3.SQLITE_OK
        )
    )
    statements = split_statements(query)
    if not statements:
        sys.stderr.write("no SQL statements to run\n")
        return 1
    for number, statement in enumerate(statements, 1):
        try:
            cursor = conn.execute(statement)
            if cursor.description is not None:
                rows = cursor.fetchmany(max_rows + 1)
                columns = [d[0] for d in cursor.description]
                print(format_table(columns, rows[:max_rows]))
                if len(rows) > max_rows:
                    print(f"(output limited to {max_rows} rows)")
            else:
                print(f"OK ({max(cursor.rowcount, 0)} row(s) affected)")
        except sqlite3.Error as exc:
            sys.stderr.write(f"Error in statement {number}: {describe_error(exc)}\n")
            return 1
        if number < len(statements):
            print()
    return 0


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    with open(payload.get("query_file", SOURCE_NAME), encoding="utf-8", errors="replace") as fh:
        query = fh.read()
    if payload.get("mode") == "run":
        return run_script(payload, query)
    return run_tests(payload, query)


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    os._exit(status)
