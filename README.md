# AI Interview Platform

Self-hosted platform that runs adaptive, voice-first job interviews with an animated AI interviewer, an
optional coding round, browser-based integrity monitoring and evidence-referenced reports for recruiters.
All AI runs on local inference endpoints (no cloud models). Files are stored on the local filesystem
(no S3 or MinIO).

Written for: engineers who operate, extend or review the platform.

## Features

| Area | What exists today |
|---|---|
| Open interview links | One shareable link per job and round(s) for many candidates (campus drives, job posts): configurable registration form with a fairness guard (protected-trait fields are blocked), one attempt per person (same email/phone blocked; same device/resume/network flagged for review), personal "continue later" link, multi-round progression (immediately or after recruiter review), registrations table with advance/retake/CSV export, QR code |
| Candidate journey | Invite link or open link -> identity check (email + optional access code) -> consent to a policy-specific disclosure -> resume upload (PDF, DOCX, TXT, PNG/JPEG, scanned PDF via OCR; a quick profile shows in under a second, the full AI profile in ~15-20 s) -> optional round selection -> system check -> live interview -> candidate report once released |
| AI interviewer | Adaptive turn engine: a deterministic policy directive decides what is allowed (time, coverage, difficulty, claims to verify, contradictions, coding), the 35B model phrases one question per turn; speech streams to TTS while the model is still generating |
| Voice loop | Browser AudioWorklet (16 kHz PCM) over one WebSocket -> Silero VAD -> pause-delimited Whisper segments -> adaptive end-of-turn -> Kokoro TTS with viseme timings -> audio-reactive interviewer presence; barge-in, reconnect and resume |
| Interviewer presence | Client-side animated voice orb (three.js shader, three palettes) that breathes when idle, ripples with the candidate's voice while listening, swirls while thinking and pulses in sync with the interviewer's audio (playback loudness + visemes on the audio clock); 2D and text fallbacks; a realistic 3D avatar (CC0 MPFB model with viseme lip-sync) stays selectable |
| Coding round | Monaco editor, Python, JavaScript, TypeScript, Java, Go, C++ and SQL; each run in a fresh network-less container; three hint levels priced into the score; a quiet AI observer that comments only on triggers |
| Integrity monitoring | Disclosed browser signals (focus, tab visibility, fullscreen, clipboard sizes, reloads, disconnects) and periodic camera/screen frames analysed by a vision model; on-device face check (MediaPipe, one face visible / multiple faces / framing and light tips) confirmed server-side by a deterministic face detector (YuNet); rule engine with warn/pause/terminate actions; vision and face signals can never pause or terminate; human review queue |
| Recordings | With consent, the full interview is recorded (camera video plus both voices, interviewer and candidate), finalised into a seekable file; staff watch it in the session Recording tab with transcript sync and integrity markers; downloads admin-only and audited; retention and erasure apply |
| Scoring and reports | Atomic evidence per competency -> scores with confidence -> rubric categories -> assistive recommendation (never an automatic reject) -> candidate and recruiter narratives that cite evidence -> separate human review decision and release policy |
| Question library | 9,780 curated interview questions (at least 1,000 per round type, 25 roles, 63 competencies) and 1,039 validated coding problems (932 function problems with Python + JavaScript references, 107 SQL); validator CLI `python -m app.seed.validate`; the interviewer still writes its own questions from each candidate's resume and answers, using the bank as inspiration |
| Recruiter app | Jobs and rounds editor, invitations, open interview links, candidates with parsed profile and job match, question bank (semantic search, generation), coding question editor with sandbox validation, templates, organisation settings, users, sessions, session detail (transcript, questions, competencies, timeline, coding replay, integrity, plan, review), live monitor, recording playback, printable report, analytics; light/dark/system theme and phone-friendly layouts |
| Operations | docker compose project `interview`, Caddy edge with LAN HTTPS, Prometheus + Grafana with alert rules, database and file backups |

## Quick start

Prerequisites: Docker Engine with Compose v2.17+, GNU make, bash, curl, python3, [uv](https://docs.astral.sh/uv/),
Node.js 20.19+ or 22.12+, about 4 GB of disk, and network access to the local model endpoints (see
[LOCAL_SETUP.md](LOCAL_SETUP.md#prerequisites)).

```bash
make setup            # .env with fresh secrets, model/avatar assets (sha256-checked), uv + npm dependencies
make sandbox-images   # per-language code runner images (python, node, java, go, cpp)
make up               # docker compose up -d, then wait until every service is healthy
make seed             # organisation, one account per staff role, global catalog; prints the logins
make demo             # optional: the same plus 4 demo jobs, 2 demo candidates and their invite links
make smoke            # health, edge headers, Prometheus targets
```

`make seed` and `make demo` run on the host (`uv run python -m app.seed ...` in `apps/api`), so run
`make setup` (which installs the uv environment) first. `make demo` also creates two demo candidates with
14-day invitations and prints their interview links: Jane Doe (Senior DevOps Engineer, combined AI round) and
Marcus Okafor (Backend Engineer (Python), candidate picks the round). Running it again rotates the links. Other
candidates and interviews are created through the recruiter app (Jobs > job > Invite candidate).

## URLs

| What | URL |
|---|---|
| Web app (staff sign-in at `/login`, staff app under `/app`) | http://localhost:3200 |
| Same app over HTTPS for other devices on the LAN | `https://<host-ip>:3443` ([LAN HTTPS](LOCAL_SETUP.md#testing-from-other-devices-on-the-lan-https)) |
| Candidate invite links | `<PUBLIC_BASE_URL>/i/<token>` (shown once when the invite is created) |
| API docs (when `ENABLE_API_DOCS=true`; open directly, not through the edge) | http://127.0.0.1:8200/api/docs |
| Grafana (user `admin`, password `GRAFANA_ADMIN_PASSWORD` in `.env`) | http://127.0.0.1:3301 |
| Prometheus | http://127.0.0.1:9191 |
| Design-system and interviewer-presence playgrounds (development aids) | http://localhost:3200/dev/ui, http://localhost:3200/dev/avatar |

Everything except the edge (`:3200`, `:3443`) listens on `127.0.0.1` only. Port map:
[LOCAL_SETUP.md](LOCAL_SETUP.md#ports).

## Development credentials

`make seed` creates four staff accounts in the organisation "Demo Corp":

| Role | Email (overridable with `SEED_*_EMAIL` in `.env`) |
|---|---|
| SUPER_ADMIN | `superadmin@demo.local` |
| ADMIN | `admin@demo.local` |
| RECRUITER | `recruiter@demo.local` |
| INTERVIEWER | `interviewer@demo.local` |

All four share the password `SEED_PASSWORD` from `.env` (generated by `make setup`, printed by `make seed`).
`uv run python -m app.seed --reset-passwords` (in `apps/api`) resets them to that value and clears lockouts.
Never reuse these accounts or the generated `.env` outside development.

## Project layout

```
apps/api/            FastAPI app (package `app`): REST API, WebSocket interview runtime, worker, Alembic
  app/ai/            model clients (LLM, embeddings, reranker, Whisper, OCR, TTS), routing, structured outputs
  app/realtime/      voice runtime: VAD, segmentation, STT scheduling, end-of-turn, TTS streaming, barge-in
  app/services/      interview engine, scoring, reports, coding, integrity, resume, jobs, question bank, storage
  app/api/routers/   one module per REST resource
  app/workers/       background job handlers (Redis Streams)
  app/seed/          seed CLI and catalog loaders
apps/web/            Vite + React 19 SPA (candidate app, recruiter app, interviewer presence), served by the edge
services/tts/        Kokoro TTS microservice (CPU) with viseme timings
services/sandbox/    code execution service + per-language runtime images
infra/               Caddy edge, Prometheus rules and tests, Grafana dashboards, Postgres init
samples/             validated catalog: competencies, round templates, question bank, coding problems, demo jobs, resumes
scripts/             setup, asset download, backups and restores, smoke test, edge CA export
tests/e2e/           Playwright end-to-end test of the full candidate journey
docs/design/         design-time contracts (CONTRACTS, ENGINE_DESIGN, REALTIME_DESIGN, UI_TEST_IDS)
```

## Running tests

| Suite | Command | Needs |
|---|---|---|
| API unit tests (mock AI) | `make test-api` (`uv run pytest -m "not live and not integration"` in `apps/api`) | nothing running |
| Web unit tests | `make test-web` (`npm test` in `apps/web`); also `npm run typecheck`, `npm run build` | nothing running |
| API integration tests | `make test-integration` (`-m integration`) | postgres + redis (`make infra-up`); each pytest process creates and drops its own `interview_test_*` database |
| Live model tests | `make test-live` (`-m live`) | the local model endpoints; they are shared with production, keep runs small |
| Coding sandbox tests | `cd services/sandbox && uv run pytest` (unit); `uv run pytest -m integration tests/integration` | integration: running sandbox + runtime images |
| TTS service tests | `cd services/tts && uv run pytest`; `uv run pytest -m live` | live: running tts service |
| End-to-end | `make test-e2e` (Playwright in `tests/e2e`, base URL `E2E_BASE_URL`, default `http://localhost:3200`) | full stack, `ENABLE_TEST_HOOKS=true`, `make demo`, live models; one spec (`full-interview.spec.ts`, 30-minute timeout) that drives a voice interview through a fake microphone fed with recorded WAV clips; credentials from `E2E_RECRUITER_EMAIL` / `E2E_PASSWORD` or `SEED_PASSWORD` in `.env`, test-hook token from `E2E_TEST_HOOKS_TOKEN` or `TEST_HOOKS_TOKEN` in `.env` |
| Infrastructure | `make test-infra` | Docker (Caddy validate, promtool, compose invariants, dashboards, shellcheck, edge behaviour tests) |

Pytest markers (`apps/api/pyproject.toml`): `integration`, `live`, `sandbox`; `live` tests are excluded unless
selected with `-m live`.

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | services, networking, data stores, data model, key decisions with measured numbers |
| [LOCAL_SETUP.md](LOCAL_SETUP.md) | prerequisites, step-by-step setup, development mode, ports, LAN HTTPS, troubleshooting |
| [DEPLOYMENT.md](DEPLOYMENT.md) | production configuration, TLS, observability and alerts, backups, scaling, security checklist |
| [AI_PIPELINE.md](AI_PIPELINE.md) | every model's role, routing, structured outputs, streaming, STT/TTS, measured latencies, fallbacks |
| [INTERVIEW_ENGINE.md](INTERVIEW_ENGINE.md) | interview state, policy directive, difficulty, scoring formulas as implemented, reports, human review |
| [AVATAR_SYSTEM.md](AVATAR_SYSTEM.md) | interviewer presence: voice orb states, speaking sync, fallbacks, the optional 3D avatar |
| [CODING_INTERVIEW.md](CODING_INTERVIEW.md) | languages, signatures, sandbox isolation and attack tests, hints, observer, evaluation |
| [INTEGRITY_MONITORING.md](INTEGRITY_MONITORING.md) | signals, rules, vision constraints, consent, retention, limitations, reviewer workflow |
| [SECURITY.md](SECURITY.md) | threat model, controls, data protection, responsible-AI constraints |
| [docs/](docs/README.md) | design-time contracts and the documentation index |
