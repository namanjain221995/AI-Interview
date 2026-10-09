"""Throwaway mock-AI stack for the browser end-to-end tests (no model calls; on the shared servers it only uses
a throwaway database and a leased Redis DB index).

    cd apps/api && uv run python ../../tests/e2e/mock-stack/stack.py                    # start, wait for Ctrl+C
    cd apps/api && uv run python ../../tests/e2e/mock-stack/stack.py --run specs/intake.spec.ts

What it starts, all on 127.0.0.1 and all stopped (by process group) on exit:

* an ``interview_test_<pid>_<hex>`` database on the development Postgres (created, migrated, seeded with
  ``python -m app.seed --demo``, dropped on exit), a leased Redis DB index (1-14, released on exit) and a temporary
  storage root: the isolation of ``apps/api/tests/fixtures/core.py``;
* the API on ``--api-port`` (8480) with ``ENABLE_TEST_HOOKS=true`` and the mock AI registry (``mock_ai.py``);
* the worker (mock AI, metrics on ``--worker-port``, 8481);
* the Vite dev server on ``--web-port`` (5280) proxying ``/api`` and ``/ws`` to the API (``vite.config.mts``).

Model endpoint settings point at a closed local port, so a code path that bypassed the registry would fail instead
of reaching the shared GPU. Logs and ``mock-stack.json`` (ports, pids, database name; no secrets) go to
``tests/e2e/mock-stack/.out/`` (git-ignored). With ``--run`` the Playwright specs run against the stack
(``E2E_BASE_URL``), the stack stops afterwards and the exit code is Playwright's.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
E2E_ROOT = HERE.parent
REPO = HERE.parents[2]
API_ROOT = REPO / "apps" / "api"
WEB_ROOT = REPO / "apps" / "web"
# Not under test-results/: Playwright empties that directory when a run starts.
OUT = HERE / ".out"
DEAD_ENDPOINT = "http://127.0.0.1:9/v1"  # discard port: nothing listens, connections fail at once

sys.path.insert(0, str(API_ROOT))
os.chdir(API_ROOT)

# Isolation first (sets DATABASE_URL / REDIS_URL / STORAGE_LOCAL_ROOT and leases a Redis DB on import).
import tests.fixtures.core as core  # noqa: E402


def log(message: str) -> None:
    print(f"[mock-stack] {message}", flush=True)


async def _admin(sql: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(core._asyncpg_dsn(core._ADMIN_DATABASE_URL))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def create_database() -> None:
    await _admin(f'CREATE DATABASE "{core.TEST_DB_NAME}"')
    await core.run_migrations(core.TEST_DATABASE_URL)


def drop_database() -> None:
    try:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{core.TEST_DB_NAME}" WITH (FORCE)'))
    except Exception as exc:  # noqa: BLE001 - report and keep tearing down
        log(f"could not drop {core.TEST_DB_NAME}: {type(exc).__name__}: {exc}")


def stack_env(args: argparse.Namespace) -> dict[str, str]:
    base_url = f"http://127.0.0.1:{args.web_port}"
    env = os.environ.copy()
    env.update(
        {
            "ENABLE_TEST_HOOKS": "true",
            "INTAKE_ENABLED": "true",
            "OTEL_ENABLED": "false",
            "COOKIE_SECURE": "false",
            "PUBLIC_BASE_URL": base_url,
            "CORS_ALLOWED_ORIGINS": base_url,
            "WORKER_METRICS_PORT": str(args.worker_port),
            "TTS_PROVIDER": "mock",
            "MAIN_LLM_BASE_URL": DEAD_ENDPOINT,
            "VISION_LLM_BASE_URL": DEAD_ENDPOINT,
            "EMBEDDING_BASE_URL": DEAD_ENDPOINT,
            "RERANKER_BASE_URL": DEAD_ENDPOINT.removesuffix("/v1"),
            "WHISPER_BASE_URL": DEAD_ENDPOINT,
            "WHISPER_EXTRA_BASE_URLS": "",
            "OCR_BASE_URL": DEAD_ENDPOINT,
            "TTS_BASE_URL": DEAD_ENDPOINT.removesuffix("/v1"),
            # The evaluation starts soon after the closing (production waits for late transcripts).
            "INTAKE_EVAL_START_DELAY_SECONDS": os.environ.get("INTAKE_EVAL_START_DELAY_SECONDS", "3"),
            "E2E_API_PORT": str(args.api_port),
            "E2E_WEB_PORT": str(args.web_port),
        }
    )
    return env


def spawn(name: str, cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    logfile = open(OUT / f"{name}.log", "wb")  # noqa: SIM115 - closed with the process
    proc = subprocess.Popen(  # noqa: S603 - fixed local commands (uv, npx) from this script
        cmd, cwd=cwd, env=env, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True
    )
    log(f"{name} pid {proc.pid} (log {OUT / (name + '.log')})")
    return proc


def stop(procs: dict[str, subprocess.Popen[bytes]]) -> None:
    for name, proc in reversed(list(procs.items())):
        if proc.poll() is not None:
            continue
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        try:
            proc.wait(timeout=40)  # the worker gives in-flight jobs 30 s (compose stop_grace_period 40 s)
        except subprocess.TimeoutExpired:
            log(f"{name} did not stop in 40 s: SIGKILL")
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        log(f"{name} stopped")


def wait_http(url: str, *, what: str, timeout: float, procs: dict[str, subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for name, proc in procs.items():
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited with {proc.returncode} (see {OUT / (name + '.log')})")
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - local URL
                if response.status < 500:
                    return
        except Exception:  # noqa: BLE001, S110 - not up yet: poll again
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{what} not ready after {timeout:.0f} s ({url})")


def main() -> int:  # noqa: PLR0915 - linear start-up script
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api-port", type=int, default=8480)
    parser.add_argument("--worker-port", type=int, default=8481)
    parser.add_argument("--web-port", type=int, default=5280)
    parser.add_argument("--run", nargs="*", metavar="SPEC", help="run these Playwright specs, then stop")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    env = stack_env(args)
    procs: dict[str, subprocess.Popen[bytes]] = {}
    stopping = False

    def on_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_signal)
    exit_code = 0
    started = time.monotonic()
    try:
        log(f"database {core.TEST_DB_NAME}, redis db {core._LEASE.db}, storage {core.TEST_STORAGE_ROOT}")
        asyncio.run(create_database())
        log(f"migrated in {time.monotonic() - started:.1f} s")
        seed_started = time.monotonic()
        with open(OUT / "seed.log", "wb") as seed_log:
            seeded = subprocess.run(  # noqa: S603
                ["uv", "run", "python", "-m", "app.seed", "--demo"],  # noqa: S607 - uv from PATH, like make
                cwd=API_ROOT,
                env=env,
                stdout=seed_log,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=False,
            )
        if seeded.returncode != 0:
            raise RuntimeError(f"seeding failed (see {OUT / 'seed.log'})")
        log(f"seeded in {time.monotonic() - seed_started:.1f} s")

        serve = str(HERE / "serve.py")
        procs["api"] = spawn(
            "api",
            ["uv", "run", "python", serve, "api", "--port", str(args.api_port)],
            cwd=API_ROOT,
            env={**env, "INSTANCE_ID": f"e2e-api-{os.getpid()}"},
        )
        procs["worker"] = spawn(
            "worker",
            ["uv", "run", "python", serve, "worker"],
            cwd=API_ROOT,
            env={**env, "INSTANCE_ID": f"e2e-worker-{os.getpid()}"},
        )
        wait_http(f"http://127.0.0.1:{args.api_port}/api/v1/health", what="API", timeout=90, procs=procs)
        wait_http(f"http://127.0.0.1:{args.worker_port}/healthz", what="worker", timeout=90, procs=procs)
        vite_env = {**env, "E2E_VITE_CACHE_DIR": str(WEB_ROOT / "node_modules" / ".vite-e2e")}
        procs["web"] = spawn(
            "web",
            ["npx", "vite", "--config", str(HERE / "vite.config.mts")],
            cwd=WEB_ROOT,
            env=vite_env,
        )
        base_url = f"http://127.0.0.1:{args.web_port}"
        wait_http(f"{base_url}/api/v1/health", what="Vite proxy", timeout=120, procs=procs)
        wait_http(f"{base_url}/", what="Vite", timeout=120, procs=procs)
        state = {
            "base_url": base_url,
            "api": f"http://127.0.0.1:{args.api_port}",
            "worker_metrics": f"http://127.0.0.1:{args.worker_port}",
            "database": core.TEST_DB_NAME,
            "redis_db": core._LEASE.db,
            "pids": {name: proc.pid for name, proc in procs.items()},
            "launcher_pid": os.getpid(),
        }
        (OUT / "mock-stack.json").write_text(json.dumps(state, indent=2) + "\n")
        log(f"READY in {time.monotonic() - started:.1f} s: {base_url} (Ctrl+C or SIGTERM stops everything)")

        if args.run is not None:
            run_env = {**os.environ, "E2E_BASE_URL": base_url, "E2E_MOCK_STACK": "1"}
            # Its own process group, so an interrupted run can stop Playwright, its workers and the browser.
            runner = subprocess.Popen(  # noqa: S603
                ["npx", "playwright", "test", *args.run],  # noqa: S607 - npx from PATH, like make
                cwd=E2E_ROOT,
                env=run_env,
                start_new_session=True,
            )
            procs["playwright"] = runner
            exit_code = runner.wait()
        else:
            while True:
                for name, proc in procs.items():
                    if proc.poll() is not None:
                        raise RuntimeError(f"{name} exited with {proc.returncode}")
                time.sleep(1)
    except KeyboardInterrupt:
        log("stopping" + (" (SIGTERM)" if stopping else ""))
        if args.run is not None:
            exit_code = 143 if stopping else 130  # an interrupted test run is not a pass
    except Exception as exc:  # noqa: BLE001 - always tear down
        log(f"FAILED: {exc}")
        exit_code = 1
    finally:
        stop(procs)
        drop_database()
        core._LEASE.release()
        shutil.rmtree(core.TEST_STORAGE_ROOT, ignore_errors=True)
        log(f"database {core.TEST_DB_NAME} dropped, redis lease released, storage removed")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
