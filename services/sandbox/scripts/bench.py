#!/usr/bin/env python3
"""Measure end-to-end sandbox latency per language through the HTTP API.

    python services/sandbox/scripts/bench.py [--runs 5] [--url http://127.0.0.1:8220]

Reports p50 / min / max client round trip for a two_sum "tests" request with
4 tests (2 visible, 2 hidden) and for a "run" request with stdin, plus the
p50 of the service-side phase timings. The secret comes from
SANDBOX_SHARED_SECRET or the repository .env and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from tests.integration.conftest import REPO_ENV, SandboxClient  # noqa: E402
from tests.integration.programs import CORRECT, LANGUAGES, STDIN_PROGRAM, TWO_SUM_SIGNATURE, TWO_SUM_TESTS  # noqa: E402


def secret() -> str:
    if os.environ.get("SANDBOX_SHARED_SECRET"):
        return os.environ["SANDBOX_SHARED_SECRET"]
    for line in REPO_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("SANDBOX_SHARED_SECRET="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("SANDBOX_SHARED_SECRET not found")


def measure(client: SandboxClient, runs: int, **payload) -> dict:
    client.execute(**payload)  # warm-up (page cache), not counted
    totals, phases = [], {}
    for _ in range(runs):
        started = time.perf_counter()
        result = client.execute(**payload)
        totals.append((time.perf_counter() - started) * 1000)
        assert result["status"] == "passed", result
        for key, value in result["meta"]["timings_ms"].items():
            phases.setdefault(key, []).append(value)
    return {
        "p50_ms": round(statistics.median(totals)),
        "min_ms": round(min(totals)),
        "max_ms": round(max(totals)),
        "phases_p50_ms": {k: round(statistics.median(v)) for k, v in phases.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--url", default=os.environ.get("SANDBOX_TEST_URL", "http://127.0.0.1:8220"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    client = SandboxClient(args.url, secret())
    report = {}
    for language in LANGUAGES:
        report[language] = {
            "tests": measure(
                client,
                args.runs,
                language=language,
                code=CORRECT[language],
                mode="tests",
                signature=TWO_SUM_SIGNATURE,
                tests=TWO_SUM_TESTS,
            ),
            "run": measure(client, args.runs, language=language, code=STDIN_PROGRAM[language], mode="run", stdin="2 3\n"),
        }
    sql_setup = "CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1), (2);"
    sql_tests = [
        {"id": "s1", "expected_rows": [[3]]},
        {"id": "s2", "setup_sql": "INSERT INTO t VALUES (3);", "expected_rows": [[6]], "hidden": True},
    ]
    report["sql"] = {
        "tests": measure(
            client, args.runs, language="sql", code="SELECT SUM(x) FROM t", mode="tests", tests=sql_tests, sql_setup=sql_setup
        ),
        "run": measure(client, args.runs, language="sql", code="SELECT * FROM t", mode="run", sql_setup=sql_setup),
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"{'language':<11} {'tests p50':>9} {'run p50':>8}   tests phases (p50 ms)")
    for language, data in report.items():
        phases = data["tests"]["phases_p50_ms"]
        detail = " ".join(
            f"{k}={phases[k]}" for k in ("create", "start", "compile", "tests", "hidden_tests", "runner") if k in phases
        )
        print(f"{language:<11} {data['tests']['p50_ms']:>7} ms {data['run']['p50_ms']:>5} ms   {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
