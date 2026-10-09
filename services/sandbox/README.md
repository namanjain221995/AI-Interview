# Code execution sandbox

Runs candidate code for the coding round. Every request gets **one fresh,
locked-down, single-use container**; candidate code never runs on the host or
inside the service container. Storage is not involved (no files are kept).

```
API (apps/api, coding module)
   │  POST /v1/execute  (HMAC-signed JSON, contract: apps/api/app/services/coding/contracts.py)
   ▼
sandbox service (this directory, uid 10002, Docker socket group only)
   │  1. validate + plan (driver per language) ─ expected values stay here
   │  2. wait for a slot (SANDBOX_MAX_CONCURRENCY, bounded queue, 503 on timeout)
   │  3. docker create → attach → start; job (code, tests args, nonce) on stdin
   ▼
runtime container (interview-sandbox-<lang>:local, uid 10001, no network, ro rootfs)
   tini ─ runner.py ─ compile step ─ run / test-harness steps (fresh pipes, fd 3 results)
   │  one nonce-framed result on stdout
   ▼
service compares returned values with expected values, builds ExecuteResponse,
container removed in the background (janitor sweeps leftovers)
```

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/v1/execute` | HMAC | `ExecuteRequest` → `ExecuteResponse` |
| GET | `/v1/runtimes` | HMAC (empty body) | languages, toolchains, entry conventions, limits |
| GET | `/health` | none | Docker reachability + per-runtime image availability (503 when Docker is down) |
| GET | `/metrics` | none | Prometheus (`interview_sandbox_*`) |

Auth: `X-Sandbox-Timestamp` (unix seconds) and `X-Sandbox-Signature =
hex(HMAC-SHA256(SANDBOX_SHARED_SECRET, f"{timestamp}.{raw_body}"))`. Requests
more than 60 s off are rejected and each signature is accepted once (replay
protection, in memory). Errors use the platform shape
`{"error": {"code", "message", "request_id", "details?"}}`: 401 unauthorized,
413 body > 4 MiB, 422 schema violation, 503 `queue_full` / `queue_timeout` /
`runtime_unavailable` / `docker_error` (with `Retry-After`). A well-formed
request that cannot run (bad signature definition, missing tests) returns 200
with `status: "rejected"` and the reason in `stderr`.

## Languages and conventions

| Language | Image | Entry point (tests mode) | Notes |
|---|---|---|---|
| python | python 3.13 | top-level `two_sum` (snake_case); `class Solution` method accepted | |
| javascript | node 24 LTS | top-level `twoSum`; async functions and `Solution` methods work | CJS or ESM, `--permission` sandbox |
| typescript | node 24 LTS | top-level `twoSum` | types stripped by Node's built-in `module.stripTypeScriptTypes` (enums/namespaces via transform mode); **no type checking** (tsc would add ~1 s per run) |
| java | Temurin 25 | method `twoSum` of `class Solution` (fresh instance per test); `List<…>`, boxed types accepted | run mode: any public class name, compact source files (`void main()`) |
| go | Go 1.27 | top-level `func twoSum` in `package main` | a candidate `main()` is ignored in tests mode |
| cpp | GCC 14, `-std=c++17 -O2` | `twoSum` method of `class Solution` (vectors may be taken by reference) or a free function | `<bits/stdc++.h>` and `using namespace std;` are pre-included (LeetCode style) |
| sql | SQLite (python image) | one `SELECT` | each test: fresh in-memory DB from `sql_setup` + `setup_sql`; read-only authorizer |

Signature (shared with the question bank): `{"function_name", "params":
[{"name","type"}], "return_type", "comparison"}` with types `int long float
bool string int[] long[] float[] string[] bool[] int[][] string[][]` and
comparison `exact` (default), `unordered` (order-insensitive at every nesting
level, e.g. indices in any order or group anagrams) or `float_tolerance`
(`|a−e| ≤ tolerance·max(1,|e|)`, `tolerance` default 1e-6) — the same
semantics as `compare_results` in `apps/api/app/seed/loaders.py`; a `null`
result for an array return type counts as `[]`. SQL tests compare
`expected_rows` (cells: numbers within 1e-6, booleans equal 1/0) in order when
`ordered` is true.

## Status mapping

`compile_error` (compiler/syntax errors, missing entry point) > `timeout` >
`memory_exceeded` > `error` (exceptions, crashes, output limit) > `failed`
(wrong answers) > `passed`. Run mode: exit 0 → `passed`. Per-test results carry
`actual` (previewed above 8 KiB), `expected`, `error` and `runtime_ms`. After a
crash or an out-of-memory kill the harness restarts so later tests still run;
after a timeout the remaining tests are reported as not executed.

`time_limit_ms` bounds each harness process (visible tests, then hidden tests)
or the program in run mode; compilation has its own `compile_time_limit_ms`.
`memory_limit_mb` is the program's limit; each language adds a fixed runtime
allowance (JVM 96 MB, Node 64 MB, Python/Go 32 MB, C++ 16 MB) and compiled
languages get a larger container for the compiler (Java 384, C++ 448, Go 320 MB).

**Hidden tests**: expected values never enter a container (the service
compares). Hidden tests run in their own process whose stdout/stderr are
discarded, so candidate prints cannot echo hidden inputs. `actual`/`error` of
hidden tests are still returned for staff views — **the API must not forward
them to candidates**.

## Isolation

| Control | Setting |
|---|---|
| Network | `network_mode=none` (loopback only) |
| Filesystem | read-only rootfs; `/tmp` tmpfs `size=64m,nosuid,nodev,exec`; no volumes/binds; no `/dev/shm` (`ipc_mode=none`) |
| Identity | uid/gid 10001, `cap_drop=ALL`, `no-new-privileges`, no setuid binaries, no shells in the images |
| Syscalls | Docker default seccomp profile; optional gVisor via `SANDBOX_CONTAINER_RUNTIME=runsc` |
| Resources | memory = memswap (no swap), 1 CPU quota (`cpu_shares` 512), `pids_limit=64`, ulimits `nofile=256 fsize=32MiB core=0`, `oom_score_adj=1000` |
| Processes | tini as PID 1; every step runs in a new session; after each step every candidate process is SIGKILLed |
| Time | runner wall-clock limit per step + RLIMIT_CPU backstop; service-side hard deadline kills the container |
| Memory | cgroup limit + runner watchdog on the cgroup's anonymous memory (fork-safe); OOM kills reported as `memory_exceeded` |
| Output | 64 KiB per stream returned (head+tail), process killed past 4 MiB, container killed past 64 MiB of raw output, no logging driver |
| Result integrity | job read from stdin before any candidate process exists; runner is non-dumpable; result framed with a per-run 128-bit nonce (candidate code can write to `/proc/1/fd/1` but cannot forge a frame) |
| Environment | `HOME TMPDIR LANG LC_ALL` + toolchain variables only; no secrets ever reach a runtime container |
| Lifecycle | container removed after every run; janitor removes labelled leftovers (> 60 s exited / > 300 s running) |

The service container needs the Docker socket, which is root-equivalent on
the host: it drops to uid 10002 with only the socket's group, accepts only
signed requests, and request fields never influence image, mounts or
privileges. Further hardening: rootless Docker or a socket proxy
(`SANDBOX_DOCKER_URL`), gVisor (`SANDBOX_CONTAINER_RUNTIME`), dedicated nodes.

## Job protocol (service ↔ runner)

stdin `SANDBOX-JOB 1 <len>\n<json>`: `{nonce, workdir, files, copies, env,
steps[], limits, deadline_ms}`; each step has `argv`, `timeout_ms`,
`memory_limit_kb`, `stdin`, `capture` and, for test steps, `harness: {input,
tests: [{i, args | setup_sql}], max_restarts}`. Harnesses write JSON lines to
fd 3: `ready`, `begin{i}`, `result{i, ok, actual|error, error_type, ms}`,
`fatal{kind}`, `compile_error`, `done`. The runner validates and bounds every
record (it is written by untrusted code), restarts the harness after crashes
and prints one result frame. `runtimes/runner.py` is canonical; copies in each
`runtimes/<lang>/` are synced by `scripts/sync_runner.py` (a unit test fails
on drift).

Per-language speed work (measured on the DGX Spark, 1 CPU): Java compiles
only the candidate class (harness/launcher precompiled) with C1 + SerialGC +
a JDK 25 AOT cache (javac 0.8 s → 0.33 s); Go ships a read-only GOCACHE
pre-warmed with the std packages and the harness package (build 3.5 s →
0.2 s); C++ uses a precompiled `<bits/stdc++.h>` prelude and a prebuilt
harness object (1.1 s → 0.4 s); Python skips the separate syntax check in
tests mode.

## Latency (p50 of 5 warm runs, client round trip, 2026-10-07)

`two_sum`, 4 tests (2 visible + 2 hidden) / run mode with stdin, measured with
`scripts/bench.py` on the shared DGX Spark (load average 7-9):

| python | javascript | typescript | java | go | cpp | sql |
|---|---|---|---|---|---|---|
| 278 / 280 ms | 294 / 253 ms | 372 / 354 ms | 683 / 605 ms | 442 / 429 ms | 696 / 506 ms | 319 / 276 ms |

About 200 ms of each run is container lifecycle (create ~55 ms, start ~100 ms).
Attack runs end promptly: a 1 s infinite loop or sleep returns `timeout` in
~1.3 s, fork bombs `timeout` at the limit, memory bombs `memory_exceeded` in
~0.5 s, stdout floods (pipe or `/proc/1/fd/1`) `error` in ~0.3 s.

## Operations

```bash
# from the repository root
docker compose --profile runtimes build runtime-python runtime-node runtime-java runtime-go runtime-cpp
docker compose build sandbox && docker compose up -d sandbox
curl -s 127.0.0.1:8220/health

# tests (from services/sandbox)
uv run pytest                                   # unit tests, no Docker needed
uv run pytest -m integration tests/integration  # against the running service (SANDBOX_TEST_URL)
uv run python scripts/bench.py --runs 5         # latency per language
```

Configuration (`SANDBOX_` prefix): `SHARED_SECRET` (required), `PORT`,
`MAX_CONCURRENCY` (4), `MAX_QUEUE` (64), `QUEUE_TIMEOUT_SECONDS` (15),
`MAX_REQUEST_BYTES`, `CPUS`, `CPU_SHARES`, `PIDS_LIMIT`, `TMPFS_SIZE_MB`,
`NOFILE_LIMIT`, `FSIZE_LIMIT_MB`, `STACK_LIMIT_MB`, `RUNTIME_OVERHEAD_MB`,
`OUTPUT_CAPTURE_BYTES`, `IMAGE_<LANG>`, `CONTAINER_RUNTIME`, `DOCKER_URL`,
`LOG_LEVEL`, `LOG_JSON`. Dependencies: `pyproject.toml` / `uv.lock`;
`requirements.txt` (hash-pinned, used by the Dockerfile) is generated with
`uv export --no-dev --format requirements-txt --no-emit-project`.

Known limitations: TypeScript types are not checked; JavaScript `long` values
above 2^53 lose precision (JS numbers); per-test timing is wall time under a
shared 1-CPU quota; Go on arm64 reports `index out of range [0]` for constant
out-of-range indexes in non-inlined functions (upstream toolchain behaviour).
