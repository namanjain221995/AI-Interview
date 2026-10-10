# Deployment and operations

Written for: engineers who deploy, operate and harden the AI Interview Platform. Day-to-day development setup
is in [LOCAL_SETUP.md](LOCAL_SETUP.md); component design in [ARCHITECTURE.md](ARCHITECTURE.md); the threat
model and controls in [SECURITY.md](SECURITY.md).

The reference deployment is a single node (the NVIDIA DGX Spark, arm64, 20 cores) running docker compose project
`interview`, next to an existing production AI stack that owns the model servers. Files are stored on the local
filesystem (`appdata` volume). There is no S3 or MinIO in this deployment; the storage interface leaves room for
another backend later (see [Scaling out](#8-scaling-out)).

## 1. Topology and networking

```
                LAN / candidates
                      |
            :3200 HTTP | :3443 HTTPS            (only listeners on non-loopback interfaces)
              +-----------------+
              |  edge (Caddy)   |  host network: static SPA, /api/* and /ws/* -> 127.0.0.1:8200
              +--------+--------+
                       | loopback
  host network   +-----+------+      +-----------+     +--------------+  +-----------+
  (loopback)     |  api :8200 |      | worker    |     | prometheus   |  | grafana   |
                 +--+---+---+-+      | :8201     |     | :9191        |  | :3301     |
                    |   |   |        +--+--------+     +------+-------+  +-----------+
                    |   |   +-----------+--> local AI endpoints (shared): :8000 :8002 :8003 :8005,
                    |   |                    whisper 172.17.0.1:30007 (+192.168.9.68:30007), OCR 192.168.9.68:30004
   127.0.0.1 ports  |   |
  ------------------+---+------------------------------------------------------------
  bridge network "interview-backend":
     postgres :5442   redis :6390   tts :8210   sandbox :8220   postgres-exporter :9192   redis-exporter :9193
     clamav :3310 (optional, --profile security)
```

* `api`, `worker`, `migrate`, `edge`, `prometheus` and `grafana` use host networking because the model endpoints
  are bound to the host loopback by another stack. The endpoint URLs in `.env` work unchanged in and out of
  containers.
* `postgres`, `redis`, `tts`, `sandbox`, the two exporters and `clamav` run on the isolated bridge network
  `interview-backend` and publish only on `127.0.0.1`.
* Only the edge listens on non-loopback interfaces. Firewall everything else.
* The `appdata` volume is mounted at `/srv/interview-data` in `api` and `worker`
  (`STORAGE_LOCAL_ROOT=/srv/interview-data/storage`); `samples/` is mounted read-only at `/srv/interview/samples`
  (`SAMPLES_DIR`) for the seed CLI. Files are served exclusively by the API after authorisation, through
  short-lived HMAC-signed URLs.

### Ports

| Port | Service | Bind | Notes |
|---|---|---|---|
| 3200 | edge HTTP | all | SPA + API + WebSocket; target for a TLS-terminating load balancer |
| 3443 | edge HTTPS | all | internal CA (LAN testing); replace for production, section 4 |
| 8200 | api | 127.0.0.1 | `/healthz`, `/readyz`, `/metrics` |
| 8201 | worker | 127.0.0.1 | `/healthz`, `/metrics` |
| 8210 | tts | 127.0.0.1 | Kokoro, CPU |
| 8220 | sandbox | 127.0.0.1 | HMAC-authenticated execute API |
| 5442 | postgres | 127.0.0.1 | pgvector |
| 6390 | redis | 127.0.0.1 | password auth, AOF |
| 9191 | prometheus | 127.0.0.1 | 15 d / 8 GB retention |
| 9192, 9193 | postgres / redis exporters | 127.0.0.1 | |
| 3301 | grafana | 127.0.0.1 | reach it over SSH tunnel: `ssh -L 3301:127.0.0.1:3301 host` |
| 3310 | clamav (optional) | 127.0.0.1 | `docker compose --profile security up -d clamav` |

Owned by other systems on the host, never bind: 3000, 3300, 4000, 5050, 5432, 8000, 8002, 8003, 8005, 8080,
9000, 9090, 9100, 9835, 9838. Prometheus reads the DGX GPU exporter (9835) and the host node exporter (9100)
read-only.

## 2. Configuration and secrets

* All configuration is environment variables declared in `apps/api/app/core/config.py`; `.env` (from
  `.env.example`) is read by the application and by docker compose.
* `make env` (`scripts/setup.sh`) generates every secret as 40 random alphanumeric characters (`JWT_SECRET`,
  `MEDIA_URL_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SANDBOX_SHARED_SECRET`, `TEST_HOOKS_TOKEN`,
  `GRAFANA_ADMIN_PASSWORD`;
  `SEED_PASSWORD` has 20) and expands `${VAR}` references, so `DATABASE_URL` and `REDIS_URL` contain the literal
  passwords. The file is mode 0600 and git-ignored. Never commit it, print it or copy it into tickets.
* `APP_ENV=production` makes the API refuse to start when `JWT_SECRET` is shorter than 32 characters,
  `ENABLE_TEST_HOOKS=true` or `COOKIE_SECURE=false`, and makes router or startup-hook import failures fatal
  (`production` and `staging` are strict). It does not check the length of the other secrets or disable API docs:
  set `ENABLE_API_DOCS=false` yourself.
* Values passed as container environment variables are visible to anyone who can run `docker inspect`. Restrict
  Docker access accordingly; for stricter setups move secrets to Docker secrets or Kubernetes Secrets (the
  exporters support `DATA_SOURCE_PASS_FILE` / `REDIS_PASSWORD_FILE`).

Secret rotation:

| Secret | Procedure | Effect |
|---|---|---|
| `JWT_SECRET` | set `JWT_PREVIOUS_SECRET` to the old value, put the new one in `JWT_SECRET`, restart `api` and `worker`; clear `JWT_PREVIOUS_SECRET` after the refresh-token lifetime (7 days) | tokens signed with either key verify during the overlap |
| `MEDIA_URL_SECRET` | change it and restart `api` | outstanding signed media URLs (5 minutes by default) stop working; clients request new ones |
| `SANDBOX_SHARED_SECRET` | change it and recreate `sandbox`, `api` and `worker` together | runs signed with the old key get 401 during the switch; candidates see a retryable "code runner is busy" message |
| `POSTGRES_PASSWORD` | `ALTER ROLE interview PASSWORD '...'` in postgres, then update `POSTGRES_PASSWORD` **and** `DATABASE_URL` in `.env`, then recreate `api`, `worker`, `postgres-exporter` | brief reconnects |
| `REDIS_PASSWORD` | update `REDIS_PASSWORD` **and** `REDIS_URL`, then recreate `redis` (compose passes `--requirepass`), `api`, `worker`, `redis-exporter` | live sessions reconnect and resume |
| `TEST_HOOKS_TOKEN` | change it and restart `api` (production never mounts the hooks) | the end-to-end test reads the new value from `.env` |
| `GRAFANA_ADMIN_PASSWORD` | Grafana applies it only when its database is first created; change it in the Grafana UI afterwards | none |

Edge settings (in `.env`, read by docker compose):

| Variable | Default | Meaning |
|---|---|---|
| `EDGE_TLS_HOSTNAMES` | empty | extra DNS names allowed to receive on-demand internal certificates |
| `EDGE_HSTS` | `max-age=31536000` | Strict-Transport-Security on :3443 (never sent for `localhost`); `max-age=0` disables |
| `TTS_CPUS`, `TTS_MEMORY_LIMIT`, `TTS_THREADS` | `8`, `4g`, `8` (`.env.example` sets `TTS_THREADS=4`) | resource caps and threads of the TTS container |
| `SANDBOX_MAX_CONCURRENCY` | `4` | parallel code executions |

## 3. Build and deploy

```bash
make env download-assets      # first time only
make build                    # api/worker, tts, sandbox, edge images (native arm64)
make sandbox-images           # code runners
make up                       # migrate runs first (one-shot), then services; waits until healthy
make seed                     # first deployment only (host uv environment; see LOCAL_SETUP.md)
make smoke                    # verify
```

First administrator: the seed's core step is the only way to create the first SUPER_ADMIN. For a real deployment
set `SEED_SUPERADMIN_EMAIL` (and the other `SEED_*_EMAIL` values) to real addresses and `SEED_PASSWORD` to a strong
temporary password before `make seed` (or `uv run python -m app.seed --core-only` in `apps/api`), change each
password after the first sign-in (`POST /api/v1/auth/password`), then create the real organisation (super admin:
`POST /api/v1/organizations`; there is no organisations page in the SPA) or rename the seeded "TechSara Solutions"
(`PATCH /api/v1/organizations/{id}`), create users in the recruiter app (Users page), and deactivate the accounts
you do not need.

Upgrades: `make backup-db`, then `make build && make up`. Compose recreates only changed containers; `migrate`
runs before `api` and `worker` start (`alembic upgrade head`; to go back, restore the backup rather than relying on
downgrades). The edge retries a refused upstream for 5 seconds
(`lb_try_duration 5s`), so a quick API restart surfaces as a short delay rather than errors, and candidates'
WebSockets reconnect and resume from the server outbox. `api` has `stop_grace_period: 20s` (live sessions pause
cleanly), `worker` 40 s (in-flight jobs get 30 s to finish).

### Intake interviews (migration 0005)

The Intake Interview module (`docs/design/INTAKE_INTERVIEW.md`) adds migration `0005_intake_interviews` (eight new
`intake_*` tables, purely additive; `migrate` applies it on `make up`, `alembic heads` = `0005`). The kit content is
read from `$SAMPLES_DIR/intake/intake_kit_v1.yaml` (compose mounts `samples/` at `/srv/interview/samples`); without
it intake create/send answers 503 `intake_kit_unavailable`. The seed catalog adds the `intake` round template, five
competencies (`technical_reading`, `structured_answer`, `technical`, `trainability`, `composure`) and the demo job
"AI/ML Training Program — Intake" (`samples/jobs/ai_ml_training_intake.yaml`).

| Variable | Default | Meaning |
|---|---|---|
| `INTAKE_ENABLED` | `true` | `false`: every `/api/v1/intake/*` and `/api/v1/portal/intake/*` route answers 404 `intake_disabled`, new intake sessions 409 `intake_disabled`, generic invitations skip the intake row; sessions already running finish normally |
| `INTAKE_BATCH_MAX_ROWS` | `200` | most candidates per batch create (the request schema also caps it at 200) |
| `INTAKE_EVAL_CONCURRENCY` | `2` | parallel evaluator calls per intake evaluation |
| `INTAKE_EVAL_START_DELAY_SECONDS` | `30` | delay before `intake.evaluate` starts after the closing |
| `INTAKE_ACTIVITY_FINAL_PASS_WAIT_SECONDS` | `60` | wait for a spare Whisper replica before the final transcript of an activity moves to the worker |

### API image (ffmpeg)

`apps/api/Dockerfile` installs Debian's `ffmpeg` (`apt-get install --no-install-recommends ffmpeg`) for the
`recording.finalize` job, which only remuxes (stream copy) the browser's recording segments into one seekable file.
It adds about 411 MB to `interview-api:local` (measured 479 MB -> 891 MB; most of it is `libllvm19` and mesa pulled
in by the ffmpeg libraries). Without ffmpeg (`RECORDING_FFMPEG_PATH` not found) recordings are still stored as joined
segments (playable, seeking limited) and remuxed once ffmpeg is available. The job writes scratch files of up to twice
a part's size to the system temp directory; set `RECORDING_WORK_DIR` (for example a directory on the `appdata`
volume) to keep them off the container layer.

### Edge image

`apps/web/Dockerfile` builds the SPA on `node:22-bookworm-slim` (`npm ci && npm run build`), strips source maps
(build arg `SOURCEMAPS=keep` to ship them), writes `.br`/`.zst`/`.gz` siblings for static files
(`docker/precompress.mjs`), and copies the result into `caddy:2.11.7-alpine` at `/srv/www`. The runtime runs as
uid 10001 with a read-only root filesystem, no capabilities and `no-new-privileges`; `/data` (certificates) is
the `caddydata` volume. The Caddyfile is baked in from `infra/caddy/` (compose passes it as the `edgeconf` build
context) and bind-mounted over at runtime, so `make edge-reload` applies edits without a rebuild. Plain
`docker build` needs `--build-context edgeconf=infra/caddy`. `.dockerignore` keeps the 35 MB avatar source out of
the image; only the optimised `interviewer.glb` ships.

### Resource limits and logs

* `tts` is capped at `TTS_CPUS` cores (default 8 of 20) and 4 GB; `sandbox` at 2 cores, 1 GB and 512 pids (each
  execution gets its own limits from the sandbox); edge 2 cores / 512 MB; Prometheus 1 core / 2 GB; Grafana
  1 core / 1 GB; exporters 0.25 core / 128 MB. `api` and `worker` are not capped: size them per host.
* Every container logs JSON to Docker's `json-file` driver with rotation (5 x 20 MB, compressed). The edge
  access log is JSON and redacts invite tokens, signed media tokens, WebSocket tickets, access codes, Referer and
  the CSRF header (cookies and Authorization are redacted by Caddy). The API access log records route templates,
  never raw paths or query strings. Client IP addresses are logged for security; they age out with the rotation.

## 4. Edge proxy (Caddy) and TLS

Routing (`infra/caddy/Caddyfile`, identical on both listeners):

| Path | Behaviour |
|---|---|
| `/api/*` | proxy to the API; bodies: 25 MiB for resume and recording uploads, 4 MiB for frames, 2 MiB otherwise (413 with a JSON error) |
| `/api/v1/sessions/{id}/live` | server-sent events: unbuffered (`flush_interval -1`), never compressed |
| `/ws/*` | WebSocket proxy; streams survive config reloads for 10 minutes and are capped at 6 hours |
| `/metrics`, `/api/v1/test/*`, `/__edge/metrics` | loopback clients only, 403 otherwise |
| everything else | SPA from `/srv/www`; client routes fall back to `index.html` (`no-cache`); hashed `/assets/*` are immutable for a year; `.glb`/`.wasm` cached 7 days; missing files are real 404s |

Security headers on every response: the Content-Security-Policy (`'self'` only, `wasm-unsafe-eval` for
WebAssembly, `blob:` workers and media; exact value in [SECURITY.md](SECURITY.md#edge-and-browser)),
`Permissions-Policy` limiting camera, microphone, display capture and fullscreen to the site,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
`Cross-Origin-Opener-Policy: same-origin`; `Server`, `Via` and `X-Powered-By` are removed. HSTS is sent on :3443
only (not for `localhost`).

TLS options:

1. **LAN testing (default)**: `:3443` uses Caddy's internal CA with on-demand issuance. Caddy asks its own
   loopback endpoint (`/__edge/tls/ask`) before issuing, which allows loopback/private/CGNAT IPs, `localhost`,
   `*.local`, `*.lan`, `*.internal`, `*.home.arpa`, `*.ts.net` and `EDGE_TLS_HOSTNAMES`. Devices either accept
   the warning or trust the root from `make edge-ca` (see [LOCAL_SETUP.md](LOCAL_SETUP.md#testing-from-other-devices-on-the-lan-https)).
2. **Public name, edge terminates TLS**: replace the `https://:{$EDGE_HTTPS_PORT}` site address with
   `interview.example.com` and drop `tls internal`; Caddy then obtains a publicly trusted certificate via ACME.
   That needs ports 80/443 (change `http_port`/`https_port` and expose 443), or use the DNS challenge.
3. **Corporate certificate**: keep the site address and use `tls /path/cert.pem /path/key.pem` (mount the files
   read-only).
4. **Behind a load balancer / ingress**: terminate TLS there and forward to `:3200` with WebSocket upgrades and
   unbuffered responses for `/api/v1/sessions/*/live`; restrict :3200 to the load balancer. Configure Caddy
   `trusted_proxies` for the balancer's addresses so client IPs survive (the API trusts forwarded headers from
   127.0.0.1 only: `--forwarded-allow-ips 127.0.0.1`).

In production expose only the HTTPS listener: HSTS applies to the whole host name, so browsers that saw it
upgrade later plain-HTTP visits on any port.

## 5. Observability

Prometheus (`infra/prometheus/prometheus.yml`) scrapes, every 15 s:

| Job | Target | Notes |
|---|---|---|
| `prometheus` | itself | rule-evaluation health |
| `interview-api`, `interview-worker`, `interview-tts`, `interview-sandbox` | 127.0.0.1:8200/8201/8210/8220 | platform metrics (`interview_*`, `interview_tts_*`, `interview_sandbox_*`) |
| `interview-edge` | 127.0.0.1:3200/__edge/metrics | Caddy request metrics |
| `postgres`, `redis` | 127.0.0.1:9192/9193 | exporters; Redis job streams via `REDIS_EXPORTER_CHECK_STREAMS=db0=jobs:*` |
| `vllm` | 127.0.0.1:8000/8002/8003/8005 | shared model servers, label `endpoint=main/vision/embedding/reranker`; only `vllm:*` and process series kept |
| `dgx-gpu`, `host` | 127.0.0.1:9835, 127.0.0.1:9100 | shared exporters, read-only |

Whisper exposes no `/metrics`; speech-to-text health comes from `interview_stt_latency_seconds` and
`interview_model_call_failures_total{endpoint="whisper"}`.

Platform metrics are defined once in `apps/api/app/core/metrics.py`: sessions and WebSocket gauges, turn latency
(`interview_turn_latency_seconds`: candidate end of turn -> first interviewer audio sent), time to first audio
(`interview_time_to_first_audio_seconds`), LLM latency/TTFT/tokens/failures per endpoint and task, STT/TTS/
embedding/rerank/OCR latency, coding executions, jobs, queue depth, HTTP requests, auth events, rate-limit
rejections, circuit-breaker state and integrity events.

Grafana provisions the datasource (uid `prometheus`) and three dashboards in the "Interview Platform" folder:
**Interview Platform — Overview** (service health, live sessions, turn latency and time-to-first-audio p50/p95
against the SLOs, barge-ins, integrity signals, HTTP status classes, errors, edge, sign-ins, rate limits),
**AI Models** (LLM latency and TTFT by endpoint and task, tokens, failures, STT/TTS/embedding/rerank/OCR
latency, vLLM running/waiting/KV cache, GPU) and **Workers & Infrastructure** (queues, job outcomes and
durations, coding executions, database and Redis, process and host resources). The JSON is generated:
edit `infra/grafana/generate_dashboards.py`, run `make dashboards`.

Checks: `make prom-check` (promtool config, rules and alert unit tests in `infra/prometheus/tests/`),
`make test-infra` (also verifies every dashboard and alert query against the running Prometheus and that every
metric they use exists in `app/core/metrics.py`).

Health endpoints: `GET /healthz` (liveness, no dependencies), `GET /readyz` (database, Redis, storage; 200 with
per-check latency), `GET /api/v1/system/health` (any signed-in staff user: database, Redis, storage with free disk
space, job queues, live workers from their heartbeats, every AI endpoint, TTS and the sandbox; no URLs or
credentials in the output). The worker serves `/healthz` and `/metrics` on 8201.

### Alerts

Rules live in `infra/prometheus/rules/alerts.yml` (32 rules). No Alertmanager is deployed yet: alerts are visible
in Prometheus (Alerts page) and Grafana. To notify people, add an Alertmanager service and an `alerting:` block to
`prometheus.yml`. Latency and ratio alerts only fire with a minimum amount of traffic. SLO thresholds: turn
latency p95 > 3 s, time to first audio p95 > 2 s, STT p95 > 2 s.

### Alert runbook

| Alert | Severity | First steps |
|---|---|---|
| `InterviewApiDown` / `InterviewWorkerDown` | critical | `make ps`; `make logs SERVICE=api`; `curl 127.0.0.1:8200/readyz` (DB, Redis, storage) |
| `InterviewTtsDown` | critical | `make logs SERVICE=tts`; model files present (`scripts/download_assets.sh --check tts`); interviews fall back to browser speech |
| `InterviewSandboxDown` | critical | `make logs SERVICE=sandbox`; Docker socket reachable; runtime images built (`make sandbox-images`) |
| `InterviewEdgeDown` | critical | `make logs SERVICE=edge`; `make edge-validate` |
| `PostgresDown` / `RedisDown` | critical | `make ps`; disk space; container logs |
| `MonitoringExporterDown` | warning | `make ps`; restart `postgres-exporter` / `redis-exporter` (`make monitoring-up`) |
| `TurnLatencyP95High` / `TimeToFirstAudioP95High` | warning | AI Models dashboard: vLLM waiting requests and TTFT (shared GPU contention), STT latency, TTS latency (CPU) |
| `SttLatencyP95High` | warning | Whisper endpoint load; `WHISPER_EXTRA_BASE_URLS` to spread load |
| `TtsLatencyP95High` | warning | tts CPU usage vs `TTS_CPUS`; host CPU busy; `TTS_THREADS` x workers within the quota |
| `LlmFailureRatioHigh` / `LlmCircuitOpen` / `MainLlmEndpointDown` | warning/critical | the model server belongs to another stack: check it, then the API logs for `reason`; turn tasks degrade to the 8B while the main circuit is open |
| `AuxModelEndpointDown` | warning | vision, embedding or reranker server unreachable (other stack); frame analysis and retrieval degrade, interviews continue |
| `ModelEndpointFailures` | warning | Whisper, embedding, reranker, OCR or TTS calls failing (> 3/min for 10 min); check the endpoint named in `endpoint` and the `reason` label |
| `LlmRequestsQueueing` / `LlmKvCacheSaturated` | warning | shared vLLM saturated; reduce concurrent background work (`MAIN_LLM_MAX_CONCURRENCY`) |
| `QueueDepthGrowing` / `QueueBacklogHigh` | warning/critical | worker logs; `interview_jobs_in_flight`; add worker capacity (section 8) |
| `JobFailureRatioHigh` | warning | more than 20 % of one job type fails (15 min, at least 5 jobs): worker logs for that `type`, then `background_jobs` |
| `DeadLetterJobs` | warning | `redis-cli XRANGE jobs:dead - +` and the `background_jobs` table; fix the cause, then re-enqueue |
| `CodingExecutionErrorsHigh` | warning | sandbox logs; runtime image for that language |
| `HttpServerErrorRatioHigh` / `EdgeServerErrorRatioHigh` / `UnexpectedErrorsSpike` | warning | API logs by `request_id` (the edge sends `X-Request-ID`, the API logs it) |
| `RefreshTokenReuseDetected` / `AccountLockoutSpike` | warning | audit log; source IPs in the edge access log |
| `HostDiskSpaceLow` | warning | Docker volumes and images (`docker system df`); Prometheus retention |
| `PostgresConnectionsHigh` | warning | `DB_POOL_SIZE` (10) + `DB_MAX_OVERFLOW` (20) per process vs `max_connections` |
| `PrometheusRuleEvaluationFailures` | warning | Prometheus logs; a rule references a missing or renamed metric (`make test-infra`) |

## 6. Backups and restore

| What | Command | Output |
|---|---|---|
| Database (online, consistent) | `make backup-db` | `data/backups/db/interview-<UTC>.dump` (pg_dump custom format) + `.sha256` |
| Uploaded files (`appdata` volume) | `make backup-storage` | `data/backups/storage/appdata-<UTC>.tar.gz` + `.sha256` |
| Restore database | `make restore-db FILE=...` | verifies the checksum, asks you to type the database name, stops api/worker, restores in one transaction (`pg_restore --clean --if-exists`), restarts them |
| Restore files | `make restore-storage FILE=...` | verifies the checksum and the archive (rejects absolute and `..` paths), asks you to type `restore`, stops api/worker, empties the volume, extracts, restarts them |
| Restore drill (safe) | `scripts/restore_db.sh <file> --database restore_drill --create --yes` | restores into a scratch database; drop it afterwards |

Backups contain candidate personal data: they are written 0600 under the git-ignored `data/` directory. Copy them
to encrypted storage off the host and delete local copies according to the retention policy. Restore the database
and the files from the same point in time. Schedule both backups together, for example with cron (prunes database
dumps to the newest 14):

```cron
15 2 * * * cd /opt/interview && BACKUP_KEEP=14 scripts/backup_db.sh >>/var/log/interview-backup.log 2>&1 && scripts/backup_storage.sh >>/var/log/interview-backup.log 2>&1
```

`backup_storage.sh` archives the `appdata` volume (read-only mount, no network); in development mode (host
processes) files live in `data/storage/` instead: `scripts/backup_storage.sh --host-path data/storage` writes
`storage-host-<UTC>.tar.gz`. Redis holds only hot copies (session state, queues) and runs with AOF; it is not part of
the backup set. The `caddydata` volume holds the internal CA; losing it only means re-trusting a new root on test
devices.

## 7. Security checklist for production

Configuration:

* `APP_ENV=production`, `ENABLE_TEST_HOOKS=false` (enforced), `ENABLE_API_DOCS=false`, `COOKIE_SECURE=true`
  (enforced), `PUBLIC_BASE_URL=https://<public name>`, `CORS_ALLOWED_ORIGINS` empty (the edge serves one origin).
* Fresh secrets of 32+ characters for every secret (only `JWT_SECRET` length is enforced); rotate on staff
  changes or suspected exposure (table in section 2). Change the Grafana admin password after first login.
* `MALWARE_SCAN_MODE=required` with `docker compose --profile security up -d clamav` and `CLAMAV_HOST=127.0.0.1`
  (uploads are rejected with 503 `scan_unavailable` while ClamAV is unreachable).
* Review organisation settings: integrity policy, recording and retention defaults, candidate report release
  mode.

Network and edge:

* Expose only the edge's HTTPS listener with a publicly trusted certificate (section 4); keep every other port on
  loopback (compose already does); firewall :3200 to the load balancer if one is used.
* Keep `/metrics`, `/api/v1/test/*` and `/__edge/metrics` loopback-only (Caddyfile); reach Grafana and Prometheus
  through an SSH tunnel.
* The developer pages `/dev/ui` and `/dev/avatar` are only in the Vite dev server or builds made with
  `VITE_ENABLE_DEV_ROUTES=true`; production builds leave them out.

Sandbox hardening (the sandbox service mounts the Docker socket, which is root-equivalent on the host; candidate
code never reaches it, but the service itself is a high-value target):

* gVisor: install `runsc` on the host and set `SANDBOX_CONTAINER_RUNTIME=runsc`, so every runtime container gets
  a user-space kernel in addition to the default seccomp profile.
* Rootless Docker or a Docker socket proxy that only permits container create/start/attach/wait/kill/remove on
  the sandbox images, configured through `SANDBOX_DOCKER_URL`.
* A dedicated node (or node pool) for code execution, so a container escape does not reach the platform's data
  or the shared model stack.
* Keep `SANDBOX_MAX_CONCURRENCY` within the node's CPU budget (each run gets one CPU).

Data and operations:

* Encrypt backups off the host; set retention policies per organisation (defaults: media 180 days, frames 30,
  audio 90, video 30) and confirm the hourly `retention.sweep` runs (`interview_jobs_total{type="retention.sweep"}`).
* The postgres exporter uses the application role; for least privilege create a `pg_monitor` role and pass it via
  `DATA_SOURCE_USER`/`DATA_SOURCE_PASS_FILE`.
* Keep images pinned (caddy 2.11.7, prometheus 3.2.1, grafana 11.5.1, postgres-exporter 0.20.1,
  redis_exporter 1.92.1, pgvector 0.8.7/pg17, redis 8.8.3) and upgrade deliberately.
* Add an Alertmanager so critical alerts reach people.

## 8. Scaling out

The single-node compose deployment serves a few concurrent interviews on the shared host; the limits are the
shared model servers (Whisper, 35B), not the platform processes. Scaling steps, from cheapest:

| Component | How it scales | Notes |
|---|---|---|
| Whisper | add replicas to `WHISPER_EXTRA_BASE_URLS` (comma separated) | each Whisper server transcribes one request at a time, so replicas are the main lever for end-of-turn latency. The client sends a final only to an idle replica (best recent speed first), queues it otherwise (up to 30 s), hedges slow finals to a second idle replica and fails over once; partials are dropped rather than queued and never take the last idle replica ([AI_PIPELINE.md](AI_PIPELINE.md#speech-to-text)) |
| API instances | run more `api` processes behind the edge (different hosts, or different `API_PORT`s on one host with an upstream list in the Caddyfile) | any instance can serve any request. A live session is owned by one instance through the Redis lock `session:{id}:lock`; a reconnect that lands on another instance asks the owner to release it (bus message `runtime.takeover`, granted when the owner has no live connection) and resumes from the Redis outbox, so no sticky sessions are required |
| Workers | more worker processes consume the same `jobs:*` consumer groups | on one host each worker needs its own `WORKER_METRICS_PORT` (host networking; `docker compose up --scale worker=2` alone fails because both bind 8201) and a matching scrape target; per-type concurrency is set in each `@job_handler` |
| TTS | `TTS_WORKERS` (2) x `TTS_THREADS` within `TTS_CPUS` inside one container | the API talks to one `TTS_BASE_URL`; several TTS containers need a load balancer in front |
| Vision frame analysis | `VISION_LLM_MAX_CONCURRENCY` (4) concurrent analyses per worker | ~10 s of 8B time per frame (concurrent calls batch); at the policy default of one camera frame every 15 s a worker serves about 5 live sessions with camera analysis |
| Sandbox | `SANDBOX_MAX_CONCURRENCY` per sandbox host; more sandbox hosts behind a load balancer sharing `SANDBOX_SHARED_SECRET` | replay protection is per process (in memory): behind a balancer a captured request could be accepted once by each instance within the 60 s timestamp window, so keep the API-to-sandbox network private |
| PostgreSQL, Redis | managed services or operators | Redis must keep AOF and `noeviction` (streams and locks must not be evicted) |

Kubernetes mapping (all addresses are configuration):

| Compose | Kubernetes |
|---|---|
| `edge` | Deployment (2+ replicas) behind a LoadBalancer/Ingress, or replace with an ingress controller that supports WebSockets and unbuffered SSE; keep the same headers and body limits |
| `api` | Deployment + HPA (CPU and `interview_active_sessions`); no sticky sessions needed (see above) |
| `worker` | Deployment + HPA on `interview_queue_depth`; consumer groups spread jobs |
| `migrate` | Job (or Helm pre-upgrade hook) |
| `tts` | Deployment with CPU requests/limits behind a Service; scale on `interview_tts_latency_seconds` |
| `sandbox` | dedicated node pool with a gVisor/Kata RuntimeClass instead of the Docker socket (needs a Kubernetes-native driver; today the service drives Docker) |
| `postgres`, `redis` | managed services or operators (CloudNativePG, Redis with AOF) |
| `appdata` volume | ReadWriteMany volume (NFS/CephFS) mounted by api and worker; a new `StorageBackend` implementation can replace it later without touching callers |
| `prometheus`, `grafana` | Prometheus Operator with ServiceMonitors for the same jobs; dashboards as ConfigMaps from `infra/grafana/dashboards/` |
| `.env` | Secrets (External Secrets / sealed secrets) and ConfigMaps |
| model endpoints | existing GPU services, addressed through `MAIN_LLM_BASE_URL` and friends |
