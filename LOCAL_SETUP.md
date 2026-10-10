# Local setup

Written for: engineers who run the AI Interview Platform on a workstation or on the DGX Spark host, for
development or demos. Production operation (TLS, backups, scaling, hardening) is in
[DEPLOYMENT.md](DEPLOYMENT.md); the system design is in [ARCHITECTURE.md](ARCHITECTURE.md).

Everything runs locally: the AI models are the existing inference endpoints on the host, and files are stored
on the local filesystem (no S3 or MinIO).

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Docker Engine + Compose v2 | Compose 2.17 or newer (`additional_contexts` in the edge build) | the whole stack (`docker compose`, project name `interview`) |
| GNU make, bash, curl, python3 | any recent | `make` targets and `scripts/` (`scripts/setup.sh` uses python3 to generate secrets) |
| uv | 0.5 or newer (installs Python 3.12 itself) | API, worker, migrations, seed CLI, Python tests |
| Node.js | 20.19+ or 22.12+ (Vite 7 requirement; the edge image builds with Node 22) | web app (Vite) and the Playwright end-to-end test |
| Disk | about 4 GB | images, ~370 MB of model and avatar assets, volumes |

The local AI endpoints must be reachable from the host. They belong to another stack and are shared with
production, so keep load tests small.

| Endpoint | Default URL (`.env`) | Model |
|---|---|---|
| Main LLM | `MAIN_LLM_BASE_URL=http://localhost:8000/v1` | `Qwen/Qwen3.6-35B-A3B-NVFP4` |
| Vision / background LLM | `VISION_LLM_BASE_URL=http://localhost:8002/v1` | `Qwen/Qwen3-VL-8B-Instruct-FP8` |
| Embeddings | `EMBEDDING_BASE_URL=http://localhost:8003/v1` | `Qwen/Qwen3-Embedding-0.6B` (1024 dimensions) |
| Reranker | `RERANKER_BASE_URL=http://localhost:8005` | `Qwen/Qwen3-Reranker-0.6B` |
| Speech-to-text | `WHISPER_BASE_URL=http://172.17.0.1:30007/v1` (+ `WHISPER_EXTRA_BASE_URLS`) | `openai/whisper-large-v3` |
| OCR | `OCR_BASE_URL=http://192.168.9.68:30004/v1` | `baidu/Unlimited-OCR` |

Text-to-speech is not an external endpoint: the platform runs its own Kokoro service (`tts`, CPU).

## Step by step (everything in containers)

1. **Create `.env` and fetch assets.**

   ```bash
   make setup
   ```

   This runs `make env` (`scripts/setup.sh`: copies `.env.example`, replaces every `__GENERATE__` value with a
   random 40-character secret, 20 characters for `SEED_PASSWORD`, file mode 0600), `make download-assets`
   (Kokoro model and voices, Silero VAD, avatar source; each checked against a pinned SHA-256), `make deps`
   (`uv sync` in `apps/api`, `npm ci` in `apps/web` and `tests/e2e` when `node_modules` is missing) and
   `make optimize-avatar` (builds `apps/web/public/avatars/interviewer.glb` only when it is missing).
   `make setup` never overwrites an existing `.env` (`scripts/setup.sh --force` regenerates every secret).

2. **Build the code runner images** (needed before the first coding round):

   ```bash
   make sandbox-images   # interview-sandbox-{python,node,java,go,cpp}:local
   ```

3. **Start the stack.**

   ```bash
   make up
   ```

   `docker compose up -d` builds missing images (`interview-api`, `interview-tts`, `interview-sandbox`,
   `interview-edge`), runs the one-shot `migrate` service (`alembic upgrade head`) before `api` and `worker`,
   and `scripts/wait_for_healthy.sh --timeout 600` waits until every service is healthy (on failure it prints
   the state of each service and the last log lines of the unhealthy ones).

4. **Seed accounts and the catalog.**

   ```bash
   make seed    # or: make demo (adds demo jobs, two demo candidates and their invite links)
   ```

   See [What the seed creates](#what-the-seed-creates). The password is printed at the end.

5. **Check the deployment.**

   ```bash
   make smoke
   ```

6. **Open the app** at http://localhost:3200/login and sign in as `recruiter@demo.local`. To run an interview:
   Jobs > Senior DevOps Engineer > Invite candidate, then open the invite link in a private window (or on another
   device, see [LAN HTTPS](#testing-from-other-devices-on-the-lan-https)).

| What | URL |
|---|---|
| Web app (recruiter dashboard, candidate links) | http://localhost:3200 |
| Same app over HTTPS (LAN devices) | `https://<host-ip>:3443` |
| Grafana (user `admin`, password `GRAFANA_ADMIN_PASSWORD` in `.env`) | http://127.0.0.1:3301 |
| Prometheus | http://127.0.0.1:9191 |
| API docs (when `ENABLE_API_DOCS=true`; open directly, the edge CSP blocks Swagger's CDN assets) | http://127.0.0.1:8200/api/docs |

`.env` contains every secret. It is git-ignored; do not print it or paste it into tickets.

## What the seed creates

`python -m app.seed` (in `apps/api`; `make seed` adds `--print-credentials`) runs idempotent steps, each in its
own transaction:

| Step | Runs with | Creates |
|---|---|---|
| `core` | always | organisation "TechSara Solutions" and four staff accounts (`superadmin@`, `admin@`, `recruiter@`, `interviewer@demo.local`, password `SEED_PASSWORD`) |
| `catalog` | default and `--demo` | global catalog from `samples/`: 68 competencies, 13 round templates (incl. intake), 25 roles, 9,780 questions, 1,039 coding problems (932 function problems + 107 SQL); counts: `uv run python -m app.seed.validate --stats` |
| `catalog_embeddings` | default and `--demo` | enqueues `question_bank.embed` jobs; the worker computes the vectors (and re-checks on start and hourly) |
| `demo_jobs` | `--demo` only | 5 demo jobs with requirements and rounds (incl. "AI/ML Training Program — Intake") plus 2 demo candidates with invite links (Jane Doe, Marcus Okafor); "Senior DevOps Engineer" (combined round, 30 minutes, with a coding problem) is the main demo |

`--core-only` runs only the first step. `--reset-passwords` resets the seeded accounts to `SEED_PASSWORD` and
clears lockouts. The seed does not create candidates, interviews or reports.

## Development mode (hot reload)

Run postgres and redis in containers and the application processes on the host:

```bash
make infra-up        # postgres (127.0.0.1:5442) + redis (127.0.0.1:6390)
make migrate         # alembic upgrade head
make seed
make api-dev         # terminal 1: uvicorn --reload on 127.0.0.1:8200
make worker-dev      # terminal 2: background jobs, metrics/health on 127.0.0.1:8201
make web-dev         # terminal 3: Vite on http://localhost:5173 (proxies /api and /ws to 127.0.0.1:8200)
docker compose up -d tts sandbox   # TTS and the code sandbox always run as containers
make monitoring-up   # optional: Prometheus + Grafana + exporters (--no-deps)
```

In this mode uploaded files go to `data/storage/` (`STORAGE_LOCAL_ROOT=./data/storage`, git-ignored). In
container mode they live in the `appdata` volume at `/srv/interview-data/storage`. The API and the
containerised `api` service both bind 127.0.0.1:8200, so stop the container (`docker compose stop api worker`)
before `make api-dev`.

`CORS_ALLOWED_ORIGINS=http://localhost:5173` in `.env.example` exists only for the Vite dev server; through the
edge, the SPA and the API share one origin.

## Ports

Ports owned by this platform (everything except the edge listens on loopback only):

| Port | Service | Bound to |
|---|---|---|
| 3200 | edge, HTTP (SPA, `/api`, `/ws`) | all interfaces |
| 3443 | edge, HTTPS with Caddy's internal CA | all interfaces |
| 8200 | api (`/healthz`, `/readyz`, `/metrics`, `/api/*`, `/ws/*`) | 127.0.0.1 |
| 8201 | worker metrics and health | 127.0.0.1 |
| 8210 | tts | 127.0.0.1 |
| 8220 | sandbox | 127.0.0.1 |
| 5442 | postgres | 127.0.0.1 |
| 6390 | redis | 127.0.0.1 |
| 9191 | prometheus | 127.0.0.1 |
| 9192 | postgres exporter | 127.0.0.1 |
| 9193 | redis exporter | 127.0.0.1 |
| 3301 | grafana | 127.0.0.1 |
| 3310 | clamav (optional, `--profile security`) | 127.0.0.1 |
| 5173 | Vite dev server (development only) | localhost |

Ports used by other systems on the DGX host. Never bind them: 3000, 3300, 4000, 5050, 5432, 8000, 8002, 8003,
8005, 8080, 9000, 9090, 9100, 9835, 9838.

## Testing from other devices on the LAN (HTTPS)

Browsers only allow camera and microphone on secure origins. `http://localhost:3200` counts as secure on the
host itself, but another laptop or phone must use HTTPS (the system check fails with "This page needs a secure
(https) connection" otherwise):

1. Open `https://<host-ip>:3443` (for example `https://192.168.9.54:3443`). The edge issues a certificate from
   Caddy's internal CA on the first connection. Names that qualify: loopback, private (10/8, 172.16/12,
   192.168/16) and Tailscale/CGNAT (100.64/10) addresses, `localhost`, and host names ending in `.local`, `.lan`,
   `.internal`, `.home.arpa` or `.ts.net`. Other DNS names must be listed in `EDGE_TLS_HOSTNAMES` (space
   separated) in `.env`, followed by `docker compose up -d edge`.
2. Either accept the certificate warning once per device, or trust the CA: `make edge-ca`
   (`scripts/export_edge_ca.sh`) writes the public root certificate to `data/edge-root-ca.crt` and prints install
   steps for macOS, Windows, Linux, Android and iOS. The CA private key never leaves the `caddydata` volume.
   Remove the certificate from the device when testing is done, and never give it to real candidates.
3. Set `PUBLIC_BASE_URL=https://<host-ip>:3443` in `.env` (then `docker compose up -d api worker`) so invite links
   point at an address the candidate's device can reach.

Keep `COOKIE_SECURE=true`: browsers accept Secure cookies on `http://localhost` and on HTTPS. Only plain-HTTP LAN
access would need `COOKIE_SECURE=false`, and that setup cannot use the camera anyway.

## Settings you may want to change

All settings are environment variables declared in `apps/api/app/core/config.py` (read from the repository `.env`).

| Variable | Default | When to change |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:3200` | invite links for other devices (see above) |
| `ENABLE_TEST_HOOKS`, `TEST_HOOKS_TOKEN` | `false`, generated | `true` only for the end-to-end test (time travel and forced coding endpoints); every call must send the token as `X-Test-Hooks-Token`, and the edge serves `/api/v1/test/*` to loopback clients only. An `.env` created before the token existed needs a `TEST_HOOKS_TOKEN` line added by hand (`make env` never overwrites `.env`) |
| `ENABLE_API_DOCS` | `true` | `false` outside development |
| `WHISPER_EXTRA_BASE_URLS` | empty | comma-separated extra Whisper replicas; each replica transcribes one request at a time, so a second replica cuts end-of-turn waits |
| `STT_PARTIAL_INTERVAL_MS` | `1500` in code, `2000` in `.env.example` | interim transcript interval; raise it when Whisper is busy |
| `TTS_THREADS`, `TTS_CPUS` | `TTS_THREADS=4` in `.env.example` (compose falls back to 8 when unset); `TTS_CPUS` 8 | keep `TTS_THREADS x TTS_WORKERS (2) <= TTS_CPUS` (the service logs `tts.cpu_oversubscribed` otherwise) |
| `MODEL_ROUTING_OVERRIDES` | `{}` | move an LLM task to another endpoint, e.g. `{"answer_assessment":"vision"}` |
| `MALWARE_SCAN_MODE` | `optional` | `required` with `docker compose --profile security up -d clamav` and `CLAMAV_HOST=127.0.0.1` |
| `INTERVIEWER_NAME`, `TTS_VOICE` | `Maya`, `af_heart` | fallbacks only: the interviewer's name and voice come from the organisation's avatar settings (recruiter app > Settings), which default to the same values |

## Make targets

Run `make help` for the full list. The most used ones:

| Target | What it does |
|---|---|
| `setup` | `env`, `download-assets`, `deps`, `optimize-avatar` |
| `up` / `down` / `ps` / `logs SERVICE=api` / `restart SERVICE=api` | stack lifecycle; `down` never deletes volumes |
| `build`, `build-edge`, `sandbox-images` | build application images, the edge (SPA) image, the runtime images |
| `migrate`, `migrate-docker`, `seed`, `demo` | schema and data (`demo` = seed + demo jobs) |
| `api-dev`, `worker-dev`, `web-dev`, `infra-up` | development processes |
| `test`, `test-api`, `test-web`, `test-integration`, `test-live`, `test-e2e`, `test-infra` | test suites (see [README](README.md#running-tests)) |
| `lint`, `typecheck`, `smoke` | ruff + shellcheck + Caddyfile format, mypy + tsc, post-deploy checks |
| `backup-db`, `restore-db FILE=...`, `backup-storage`, `restore-storage FILE=...` | backups ([DEPLOYMENT.md](DEPLOYMENT.md#6-backups-and-restore)) |
| `edge-validate`, `edge-reload`, `edge-ca` | edge proxy |
| `prom-check`, `prom-reload`, `dashboards`, `dashboards-check`, `monitoring-up` | monitoring |

## Model and avatar assets

`make download-assets` (`scripts/download_assets.sh`) fetches files that are too large for git and checks each
against a pinned SHA-256; files that already match are skipped, damaged files are replaced.
`scripts/download_assets.sh --check` verifies without downloading; `tts`, `vad`, `avatar` or `face` limits it to one group.

| Asset | Path | Size |
|---|---|---|
| Kokoro v1.0 ONNX model | `services/tts/models/kokoro-v1.0.onnx` | 310 MiB |
| Kokoro voices | `services/tts/models/voices-v1.0.bin` | 27 MiB |
| Silero VAD | `apps/api/models/silero_vad.onnx` | 2.2 MiB |
| MPFB avatar source (CC0) | `apps/web/public/avatars/source/mpfb.glb` | 35 MiB |
| YuNet face detector (opencv_zoo, MIT) | `apps/api/models/face_detection_yunet_2023mar.onnx` | 227 KiB |
| BlazeFace short-range (Apache-2.0) | `apps/web/public/mediapipe/models/blaze_face_short_range.tflite` | 224 KiB |
| MediaPipe Tasks Vision 0.10.17 WASM (Apache-2.0) | `apps/web/public/mediapipe/wasm/vision_wasm_internal.{js,wasm}` | 9.3 MiB |

The shipped avatar is the optimised `apps/web/public/avatars/interviewer.glb` (about 2.9 MiB, built by
`make optimize-avatar`); the source file is git-ignored and never enters the edge image. The TTS image
build adds Kokoro's per-phoneme duration output to the model (see [AI_PIPELINE.md](AI_PIPELINE.md#text-to-speech-and-visemes)).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make up` waits, then prints logs of one service | read the printed log lines; `make logs SERVICE=<name>` |
| `make seed` fails with `SEED_PASSWORD is not set` | `.env` predates the seed settings: add `SEED_PASSWORD` (12+ characters, satisfying the password policy) or regenerate with `scripts/setup.sh --force` (rotates every secret) |
| Edge answers 502 with `upstream_unavailable` | the API is down or restarting (the edge retries for 5 s first): `make logs SERVICE=api` |
| Frontend changes do not show up at :3200 | the edge serves the built SPA: `make build-edge && docker compose up -d edge` (or use `make web-dev`) |
| Caddyfile edit not applied | `make edge-reload` (validates, then reloads without dropping interview WebSockets) |
| `bind: address already in use` | another process owns the port; see the port tables above |
| Camera/microphone blocked on another device | use `https://<host-ip>:3443`, not port 3200 |
| System check picks the 2D interviewer | the 3D probe rendered below 24 fps or WebGL is unavailable; audio and subtitles are unaffected |
| System check reports a slow connection | WebSocket heartbeat round trip above 600 ms; the interview still works |
| Coding runs fail with "The code runner is busy" | sandbox down or runtime image missing: `make logs SERVICE=sandbox`, `make sandbox-images` |
| Interviewer replies take several seconds | the model servers and Whisper are shared: check the AI Models dashboard (vLLM waiting requests, STT latency); add a Whisper replica with `WHISPER_EXTRA_BASE_URLS` |
| Resume stays "analyzing" for minutes | profile generation takes about 3 minutes on the shared 35B model; check the worker (`make logs SERVICE=worker`) |
| Grafana panels say "No data" | the service has not served traffic yet, or its target is down: Prometheus > Status > Targets |
