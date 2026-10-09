# Platform contracts

Written for: engineers (and build agents) implementing modules of the AI Interview Platform in parallel.
Every module must honour these contracts so the pieces integrate without rework. Code-level contracts
are authoritative where they exist:

| Contract | File |
|---|---|
| Configuration (all env vars, endpoints, limits) | `apps/api/app/core/config.py` |
| Domain enums | `apps/api/app/domain/enums.py` |
| Database schema (37 tables) | `apps/api/app/db/models/*.py` |
| AI structured outputs | `apps/api/app/schemas/ai.py` |
| Round configuration | `apps/api/app/schemas/rounds.py` |
| Integrity / recording / retention policies | `apps/api/app/schemas/policies.py` |
| Realtime WebSocket protocol | `apps/api/app/schemas/realtime.py`, `apps/web/src/lib/realtime/protocol.ts` |
| AI client interfaces | `apps/api/app/ai/base.py` |
| Interview state | `apps/api/app/services/interview/state.py` |
| Engine <-> realtime seam | `apps/api/app/services/interview/contracts.py` |
| Coding service + sandbox wire format | `apps/api/app/services/coding/contracts.py` |
| Integrity service seam | `apps/api/app/services/integrity/contracts.py` |

If a contract file must change, change it minimally, keep backwards compatibility, and say so in your
final report.

## 1. Repository layout

```
apps/api/            FastAPI app, realtime gateway, worker (Python package `app`)
  app/core/          config, logging, security, rbac, deps, errors, metrics, tracing, redis, queue, ratelimit, audit
  app/db/            base, session, models/
  app/schemas/       ai.py, rounds.py, policies.py, realtime.py, api/ (request/response DTOs per domain)
  app/ai/            base.py, registry.py, router.py, structured.py, clients/, prompts/, mock.py
  app/services/      storage/, retrieval/, resume/, jobs/, question_bank/, interview/, scoring/, reports/, coding/, integrity/
  app/realtime/      websocket endpoint, session runtime, audio/VAD/endpointing, STT scheduler, speech streamer, outbox
  app/api/routers/   one module per resource
  app/workers/       job handlers registered with the queue (one module per domain)
  app/seed/          seed + demo data loaders
  alembic/           migrations (never rely on create_all outside tests)
  tests/unit, tests/integration
apps/web/            Vite + React 19 SPA (candidate app + recruiter dashboard), served by the edge (Caddy)
services/tts/        Kokoro TTS microservice (OpenAI-compatible /v1/audio/speech + /v1/tts)
services/sandbox/    Code execution microservice + per-language runtime images
infra/               caddy, prometheus, grafana, postgres init
samples/             demo job, resumes, round configs, question bank, coding questions, completed report
scripts/             setup, assets download, demo helpers
tests/e2e/           Playwright end-to-end tests
```

## 2. Conventions (backend)

* Python 3.12, FastAPI, SQLAlchemy 2.0 async (`AsyncSession`), Pydantic v2, `uv` for dependencies.
  Run everything from `apps/api` with `uv run ...`. Do not edit `pyproject.toml`; list any new
  dependency you need in your final report.
* Never hard-code endpoint URLs, model names or secrets — use `get_settings()`.
* Request handlers get the DB session from `app.core.deps.get_db` (commit on success, rollback on error).
  Background code uses `app.db.session.session_scope()`.
* Errors: raise `app.core.errors.AppError` subclasses (`NotFound`, `Forbidden`, `Unauthorized`,
  `Conflict`, `ValidationFailed`, `RateLimited`, `ServiceUnavailable`). The handler renders
  `{"error": {"code": "not_found", "message": "...", "request_id": "...", "details": {...}}}`.
* Logging: `from app.core.logging import get_logger; log = get_logger(__name__)`; structured key/values
  (`log.info("resume.processed", resume_id=..., duration_ms=...)`). Never log resume text, transcripts,
  tokens or secrets at INFO.
* IDs are UUIDs; API returns them as strings. Timestamps are timezone-aware UTC ISO-8601.
* Organization scoping is mandatory: every staff query filters by `principal.organization_id`
  (SUPER_ADMIN may pass `?organization_id=`). Candidates only reach `/api/v1/portal/*` and only their
  own interview.
* Audit: `await audit(db, principal, "job.create", resource_type="job", resource_id=str(job.id), details={...})`
  for every state-changing staff action and every report/transcript/media view.
* Tests: unit tests use `app.ai.mock.MockAIServices` (fixture `mock_ai`). Integration tests use the running
  postgres/redis: `tests/fixtures/core.py` creates a throwaway **database per pytest process**
  (`interview_test_<pid>_<hex>`, migrated with Alembic) and leases a Redis logical DB; fixtures `db_session`,
  `redis`, `storage`, `app`, `client`, `user_factory`, `auth_client(role)`, `candidate_auth_client`,
  `worker_factory`. Mark live-model tests `@pytest.mark.live`.
* List endpoints return `{"items": [...], "total": n, "limit": l, "offset": o}`.

## 3. Authentication and RBAC

* Staff: `POST /api/v1/auth/login {email, password}` sets cookies:
  `access_token` (HttpOnly, 15 min JWT), `refresh_token` (HttpOnly, path `/api/v1/auth`, 7 days, rotating),
  `csrf_token` (readable by JS). Mutating requests must send header `X-CSRF-Token` equal to the cookie
  (double submit, HMAC-bound to the auth session).
* JWT claims: `sub`, `typ` (`user`|`candidate`), `org`, `role`, `sid` (auth_sessions.id), `iid`
  (candidate's interview id), `exp`, `iat`, `jti`. HS256 with `JWT_SECRET` (`JWT_PREVIOUS_SECRET`
  accepted for verification during rotation).
* Candidates: invite link `{PUBLIC_BASE_URL}/i/{token}` (32-byte url-safe token, only SHA-256 stored).
  `GET /api/v1/candidate-access/{token}` returns public invite info;
  `POST /api/v1/candidate-access/{token}/verify {email, access_code?}` sets candidate cookies scoped to
  that interview (role `CANDIDATE`).
* `app.core.rbac` defines permissions; dependencies `require_permission("jobs:write")`,
  `get_principal`, `get_candidate_principal`. Matrix:

| Permission | SUPER_ADMIN | ADMIN | RECRUITER | INTERVIEWER | CANDIDATE |
|---|---|---|---|---|---|
| organizations:manage | x | | | | |
| users:manage | x | x | | | |
| settings:manage | x | x | | | |
| jobs:read | x | x | x | x | |
| jobs:write | x | x | x | | |
| templates:write / question_bank:write | x | x | x | | |
| candidates:read | x | x | x | x | |
| candidates:write | x | x | x | | |
| interviews:invite | x | x | x | | |
| sessions:read / reports:read / transcripts:read | x | x | x | x | |
| reports:release / reviews:write | x | x | x | x | |
| integrity:review | x | x | x | | |
| analytics:read | x | x | x | x | |
| audit:read | x | x | | | |
| portal:self | | | | | x |

## 4. REST API (prefix `/api/v1`)

All list endpoints accept `?limit=&offset=` and return `{"items": [...], "total": n, "limit": l, "offset": o}`.
Auth error codes: wrong credentials 401 `invalid_credentials`; lockout 423 `account_locked` + Retry-After;
rate limit 429 `rate_limited`; deactivated 403 `account_disabled`. Cookies: `access_token` (HttpOnly,
Path=/api), `refresh_token` (HttpOnly, Path=/api/v1/auth), `csrf_token` (JS-readable, Path=/).

**Auth & users** (platform module): `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`,
`GET /auth/me`, `POST /auth/password`, `POST /users/{id}/unlock`; `GET|POST /users`, `PATCH /users/{id}`, `POST /users/{id}/deactivate`;
`GET|POST /organizations`, `PATCH /organizations/{id}`; `GET|PUT /settings` (OrganizationSettings);
`GET /audit-logs`; `GET /system/health` (model endpoints, DB, Redis, storage, tts, sandbox).

**Media**: `GET /media/{media_id}/url` -> `{url, expires_at}` (authorized; returns API-signed URL);
`GET /media/signed/{token}` streams the file (token = HMAC(media_id, exp)); no other way to read files.
Single HTTP ranges are answered with 206 (video seeking); `?download=1` serves media as an attachment.

**Catalog** (resume-jobs module): `GET|POST /competencies`, `PATCH /competencies/{id}`;
`GET|POST /jobs`, `GET|PATCH /jobs/{id}`, `POST /jobs/{id}/archive`;
`GET|POST /jobs/{id}/requirements`, `PATCH|DELETE /requirements/{id}`;
`GET|POST /jobs/{id}/rounds`, `GET|PATCH|DELETE /rounds/{id}`, `POST /jobs/{id}/rounds/reorder`;
`GET /round-types` (defaults per RoundType); `GET|POST /templates`, `GET|PATCH|DELETE /templates/{id}`;
`GET|POST /question-bank`, `GET|PATCH /question-bank/{id}`, `POST /question-bank/search {query, competency_key?, difficulty?}`,
`POST /question-bank/generate {competency_key, difficulty, count, role}`;
`GET|POST /coding-questions`, `GET|PATCH /coding-questions/{id}`, `POST /coding-questions/{id}/validate`.

**Candidates** (resume-jobs module): `GET|POST /candidates`, `GET|PATCH /candidates/{id}`,
`POST /candidates/{id}/resumes` (multipart), `GET /candidates/{id}/resumes`,
`GET /resumes/{id}` (status, progress, extraction summary), `POST /resumes/{id}/reprocess`,
`GET /candidates/{id}/profile` (latest CandidateProfile), `GET /candidates/{id}/interviews`.
Admin-only permanent deletes (ADMIN/SUPER_ADMIN; counts-only responses; see SECURITY.md "Data protection"):
`DELETE /candidates/{id}?request_reference=&archive_first=` (+ `GET /candidates/{id}/deletion-preview`),
`DELETE /sessions/{id}` (409 `session_busy`), `DELETE /intake/interviews/{id}` (409 `intake_busy`),
`DELETE /open-links/{id}` (409 `link_has_registrations`).

**Interviews** (interview module): `POST /interviews {candidate_id, job_id, round_id?, allow_round_selection, expires_in_hours, require_access_code}`
-> `{interview, invite_url, access_code?}` (link and code shown once); `GET /interviews`, `GET /interviews/{id}`,
`POST /interviews/{id}/cancel`, `POST /interviews/{id}/regenerate-link`.

**Sessions & reports** (interview module): `GET /sessions?status=&job_id=&candidate_id=`,
`GET /sessions/{id}` (summary + plan + state digest), `GET /sessions/{id}/transcript`,
`GET /sessions/{id}/questions` (questions with answers, evaluations, evidence),
`GET /sessions/{id}/evidence?competency=`, `GET /sessions/{id}/scores`, `GET /sessions/{id}/timeline`,
`GET /sessions/{id}/coding` (coding sessions, snapshots, runs, hints), `GET /sessions/{id}/live` (SSE),
`POST /sessions/{id}/recompute-scores`, `GET /sessions/{id}/reports/{type}`,
`POST /sessions/{id}/reports/regenerate`, `POST /sessions/{id}/reports/candidate/release`,
`GET|POST /sessions/{id}/reviews`, `GET /analytics/overview`, `GET /analytics/competencies`,
`GET /analytics/funnel`, `GET /analytics/model-usage`.

**Recordings** (recordings module, `sessions:read`): `GET /sessions/{id}/recordings` (parts: status, timing anchors,
finalized media), `GET /sessions/{id}/recordings/{part_id}/url` (signed playback URL, audited `recording.view`),
`GET /sessions/{id}/recordings/{part_id}/download` (ADMIN/SUPER_ADMIN, audited `recording.download`),
`POST /sessions/{id}/recordings/finalize` (process now; also `reviews:write`).

**Integrity** (integrity module): `GET /integrity/events?status=unreviewed&session_id=&type=`,
`PATCH /integrity/events/{id} {review_status, review_note}`, `GET /sessions/{id}/integrity`.

**Candidate portal** (realtime module owns routes; principal = candidate):
`GET /candidate-access/{token}`, `POST /candidate-access/{token}/verify`,
`GET /portal/me`, `GET /portal/interviews/{id}` (job, rounds offered, policy, steps completed),
`GET /portal/interviews/{id}/disclosure` (exact consent text + policy hash),
`POST /portal/interviews/{id}/consent {policy_hash, items}`,
`POST /portal/interviews/{id}/resume` (multipart) -> resume status,
`GET /portal/interviews/{id}/resume` (status + candidate-safe profile summary),
`POST /portal/interviews/{id}/round {round_id}`, `POST /portal/interviews/{id}/system-check {...}`,
`POST /portal/interviews/{id}/session` (create or resume; enqueues session.prepare) -> `{session_id, status}`,
`GET /portal/sessions/{id}`, `POST /portal/sessions/{id}/ws-ticket` -> `{ticket, ws_url}`,
`POST /portal/sessions/{id}/frames` (multipart image, `kind=camera|screen`, `captured_at`) -> 202,
`POST /portal/sessions/{id}/integrity-events` (batch; also accepted over WS),
`POST /portal/sessions/{id}/recording` (chunked video upload when policy enables it; optional `part_id`,
`part_started_at`, `media_start_ms`, `media_end_ms`, `session_ms`, `client_sent_at`, `audio_only`; accepted until
10 minutes after the session ended), `POST /portal/sessions/{id}/recording/complete {part_id, kind, segments}` -> 202,
`GET /portal/sessions/{id}/report` (candidate report when released).

**Open interview links** (docs/design/OPEN_LINKS.md section 9; one shareable link per job + round pipeline):
staff `GET|POST /open-links`, `GET /open-links/form/catalog`, `POST /open-links/form/validate`,
`GET|PATCH /open-links/{id}`, `POST /open-links/{id}/status|regenerate-token|access-code`,
`GET /open-links/{id}/registrations` (+ `/export` CSV, `/{registration_id}`), `POST /open-links/{id}/registrations/advance`,
`POST /open-links/{id}/registrations/{rid}/retake|cancel|unblock|personal-link|flag-review`,
`GET /open-links/registrations?candidate_id=`; public (token, no cookie principal, Origin/Referer allowlist)
`GET /link-access/{token}`, `POST /link-access/{token}/register`, `GET /link-return/{token}`,
`POST /link-return/{token}/verify`; candidate `GET /portal/link/progress`, `POST /portal/link/continue`.

**Intake interviews** (docs/design/INTAKE_INTERVIEW.md section 10; round type `intake`; every route answers 404
`intake_disabled` when `INTAKE_ENABLED=false`; other organisations' ids answer 404 `intake_not_found`):
staff admin (T3, `intake.py`) `GET /intake/config` (`jobs:read`), `GET /intake/settings` (`jobs:read`),
`PUT /intake/settings` (`settings:manage`), `POST /intake/interviews` and `POST /intake/interviews/batch`
(`interviews:invite` + `candidates:write`; links and access codes shown once; batch rate limit
`intake_batch_create` 10/min/user, at most `INTAKE_BATCH_MAX_ROWS` rows, per-row `created`/`failed{code}`),
`GET /intake/interviews` (`sessions:read`; `bucket`, `q`, `job_id`, `target_role`, `band`, `outcome`, `reviewer_id`,
`batch_id`, `created_from`, `created_to`, `sort`, paging), `GET /intake/stats` (`sessions:read`),
`PATCH /intake/interviews/{id}`, `POST /intake/interviews/{id}/send|cancel|regenerate-link` (`interviews:invite`);
staff review (T2, `intake_review.py`) `GET /intake/interviews/{id}`, `/timeline`, `/evaluation`,
`POST /intake/interviews/{id}/evaluate`, `GET|POST /intake/interviews/{id}/decisions`, `GET /intake/analytics`;
candidate (T3, `portal_intake.py`, own interview only, 409 `intake_not_applicable` for other round types)
`GET /portal/intake/{interview_id}`, `PUT /portal/intake/{interview_id}/onboarding` (rate limit
`portal_intake_onboarding` 30/min/interview), `GET|PUT /portal/intake/{interview_id}/resume-review` (rate limit
`portal_intake_resume_review` 30/min/interview). `POST /portal/interviews/{id}/session` answers 409
`intake_disabled`, `intake_resume_required`, `intake_onboarding_required` or `intake_resume_review_required` before a
new intake session; `PortalInterview.intake`, `InviteInfo.round_type` and the step keys `details` and `resume-review`
are additive. Validation errors of these routes carry `details.field`; intake-specific types
(`work_authorization_country_required`, `target_role_other_required`, ...) become the error code.

**Health**: `GET /healthz` (liveness, no dependencies), `GET /readyz` (DB, Redis, storage),
`GET /metrics` (Prometheus; the edge blocks it from the outside).

**Test hooks** (only when `ENABLE_TEST_HOOKS=true` and never in production):
`POST /api/v1/test/sessions/{id}/advance-clock {seconds}`, `POST /api/v1/test/sessions/{id}/force-coding`,
`POST /api/v1/test/interviews/{id}/complete {end_reason?}` (open links: end an interview without models, then sync).

**WebSocket**: `GET /ws/sessions/{session_id}?ticket=...` — protocol in `app/schemas/realtime.py`.

## 5. Background jobs (Redis Streams + `background_jobs` table)

API: `await enqueue("resume.process", {"resume_id": ...}, dedupe_key="resume.process:<id>")` from
`app.core.queue`. Handlers register with `@job_handler("resume.process")` in `app/workers/<domain>.py`
and must be idempotent. The worker (`python -m app.worker`) runs a consumer group per stream, retries
with exponential backoff (max 3), moves exhausted jobs to `jobs:dead`, reclaims stuck messages
(XAUTOCLAIM), and on start re-enqueues `background_jobs` rows left `queued`/`running`.

| Job | Payload | Owner |
|---|---|---|
| `resume.process` | resume_id, job_id? | resume-jobs |
| `profile.generate` | candidate_id, resume_id, job_id? | resume-jobs |
| `question_bank.embed` | question_ids | resume-jobs |
| `session.prepare` | session_id | interview |
| `answer.evaluate` | session_id, answer_id | interview |
| `memory.summarize` | session_id | interview |
| `scoring.finalize` | session_id | interview |
| `report.generate` | session_id | interview |
| `coding.evaluate` | coding_session_id | coding |
| `integrity.analyze_frame` | session_id, media_id | integrity |
| `retention.sweep` | (scheduled hourly) | integrity |
| `recording.finalize` | session_id, part_id?, close?, force? | recordings |
| `recording.sweep` | (scheduled every 2 minutes) | recordings |
| `open_link.round_finished` | session_id (enqueued by `scoring.finalize`) | open-links |
| `open_link.sweep` | (scheduled every 5 minutes) | open-links |
| `intake.activity_transcribe` | session_id, activity_id | intake (T1, `app/workers/intake_capture.py`) |
| `intake.evaluate` | session_id, reason, requested_by?, deferrals? (enqueued by the intake closing; replaces `scoring.finalize` + `report.generate` for intake sessions) | intake (T2, `app/workers/intake.py`) |
| `intake.report` | session_id, evaluation_id, reason? | intake (T2, `app/workers/intake.py`) |

Session-scoped results are announced on Redis pub/sub channel `session:{id}:bus` as JSON
`{"type": "evaluation.ready"|"integrity.event"|"report.ready"|"coding.evaluated", ...}` so the live
runtime refreshes its state without polling.

## 6. Redis keys

| Key | Purpose | TTL |
|---|---|---|
| `session:{id}:state` | InterviewState JSON (hot copy) | 24 h after end |
| `session:{id}:lock` | runtime ownership token (SET NX PX) | 30 s, refreshed |
| `session:{id}:outbox` | last N server messages (LIST) for resume | 6 h |
| `session:{id}:seq` | outbox sequence counter | 6 h |
| `session:{id}:bus` | pub/sub channel | — |
| `session:{id}:prep` | speculative preparation cache | 10 min |
| `ws:ticket:{ticket}` | ws ticket -> {session_id, candidate_id} | 60 s |
| `rl:{scope}:{key}` | sliding-window rate limits | window |
| `cache:emb:{sha256}` | embedding cache | 7 d |
| `jobs:{type}` | streams; consumer group `workers` | — |
| `jobs:dead` | dead-letter stream | — |
| `openlink:lock:{link_id}:{ip_hmac}` | open-link access-code lockout | `LOGIN_LOCKOUT_SECONDS` |
| `openlink:codefail:{link_id}:{ip_hmac}` | wrong access codes per (link, IP) | 10 min |
| `openlink:personal:lock:{registration_id}` | personal-link verification lockout | `LOGIN_LOCKOUT_SECONDS` |

## 7. File storage (local filesystem, no S3)

`app.services.storage.get_storage()` returns a `StorageBackend` (only `LocalStorage` today; the
interface leaves room for another backend later). Keys are relative POSIX paths under
`STORAGE_LOCAL_ROOT`; path traversal is rejected; files are written atomically (temp + rename) with
0600 permissions; every stored file gets a `media_assets` row with `retention_until`.

```
orgs/{org_id}/resumes/{candidate_id}/{resume_id}/{safe_filename}
orgs/{org_id}/sessions/{session_id}/audio/{answer_id}.wav
orgs/{org_id}/sessions/{session_id}/frames/{camera|screen}/{timestamp}-{uuid}.jpg
orgs/{org_id}/sessions/{session_id}/video/{segment:05d}.webm                 (uploads without a part id)
orgs/{org_id}/sessions/{session_id}/{video|screen}/{part_id}/{segment:05d}.{webm|mp4}
orgs/{org_id}/sessions/{session_id}/recordings/{camera|screen}-{part_id}-v{n}.{webm|mp4}
orgs/{org_id}/sessions/{session_id}/reports/{type}-v{n}.html
```

Interface: `put(key, data, content_type) -> StoredObject`, `put_stream(...)`, `open(key)` (async
iterator of bytes), `read(key) -> bytes`, `exists`, `delete`, `size`. Use
`app.services.storage.media.store_media(db, ...)` to store + register a file (`media_assets` row, retention).
`app.services.storage.signing.signed_url(media_id, ttl) -> str` returns `/api/v1/media/signed/{token}`;
`signed_url_with_expiry()` also returns `expires_at`.

## 8. Metrics (Prometheus, prefix `interview_`)

Defined once in `app/core/metrics.py` and imported elsewhere:
`interview_active_sessions` (gauge), `interview_ws_connections` (gauge),
`interview_turn_latency_seconds` (histogram: candidate end-of-turn -> first interviewer audio sent),
`interview_time_to_first_audio_seconds` (histogram: utterance start -> first TTS chunk sent),
`interview_llm_request_seconds{endpoint,task,status}`, `interview_llm_ttft_seconds{endpoint,task}`,
`interview_llm_tokens_total{endpoint,task,kind}`, `interview_model_call_failures_total{endpoint,task,reason}`,
`interview_stt_latency_seconds{kind}`, `interview_tts_latency_seconds{provider}`,
`interview_embedding_seconds`, `interview_rerank_seconds`, `interview_ocr_seconds`,
`interview_coding_executions_total{language,status}`, `interview_coding_execution_seconds{language}`,
`interview_errors_total{component}`, `interview_queue_depth{queue}`, `interview_jobs_total{type,status}`,
`interview_job_seconds{type}`, `interview_db_query_seconds{operation}`, `interview_barge_ins_total`,
`interview_integrity_events_total{type,source}`, `interview_http_requests_total{method,route,status}`,
`interview_http_request_seconds{method,route}`, `interview_auth_events_total{event}`,
`interview_rate_limited_total{scope}`, `interview_jobs_in_flight{type}`, `interview_circuit_breaker_state{endpoint}`,
`interview_model_call_ledger_dropped_total` (added during the build),
`interview_open_link_registrations_total{outcome}` (open links),
`interview_intake_invitations_total{mode}` (single, batch, draft_sent), `interview_intake_activities_total{kind,end_reason}`,
`interview_intake_stage_seconds{stage}` (histogram), `interview_intake_evaluations_total{status}`,
`interview_intake_evaluation_seconds` (histogram), `interview_intake_decisions_total{outcome,overrides_ai}` (intake).
The worker serves `/metrics` and `/healthz` on `WORKER_METRICS_PORT`; tts and sandbox expose `/metrics`.

## 9. Model routing (measured, see AI_PIPELINE.md)

| Task | Endpoint | Notes |
|---|---|---|
| turn_decision, answer_assessment, code_observation, hint_generation, opening | main (35B, thinking off) | Turn-critical. Measured TTFT ~0.1-0.3 s; the 8B router needed 2.4-4.8 s for a 20-token JSON on this hardware. |
| resume_extraction, profile_analysis, job_match, interview_planning, answer_evaluation, coding_evaluation, report_generation, question_generation, competency_summary | main | Deep reasoning, asynchronous. |
| frame_analysis, screen_analysis | vision (8B VL) | Asynchronous, low frequency. |
| document_classification, routing_classification, memory_summary | vision (8B) | Background, keeps load off the main model. |
| fallback | vision | When the main endpoint's circuit is open, turn tasks degrade to the 8B. |
| fallback (text-only vision tasks) | main | When the vision endpoint's circuit is open, text-only vision tasks (classification, memory summary) move to main; image requests never do. |

`MODEL_ROUTING_OVERRIDES` (JSON task->endpoint) overrides any row.

## 10. Frontend conventions

* `apps/web`: Vite 7, React 19, TypeScript 5.9 strict, React Router 7 (library mode,
  `createBrowserRouter`), TanStack Query 5, Zustand 5, Tailwind CSS 4 (`@tailwindcss/vite`), Radix
  primitives wrapped in `src/components/ui/*`, lucide-react icons, Recharts 3, Monaco (self-hosted,
  workers via Vite `?worker`), React Three Fiber 9 + drei 10 + three 0.18x, motion 12, sonner.
* API calls only through `src/lib/api.ts` (`api.get/post/patch/delete`, adds `X-CSRF-Token`, refreshes
  once on 401, throws `ApiError`). Shared domain types in `src/lib/types.ts` mirror backend enums.
* Routes: candidate app under `/i/:token` (entry) and `/candidate/*`; staff app under `/app/*`;
  `/login`. Code-split per area with `React.lazy`.
* No secrets in the frontend; no third-party CDNs (CSP is `self` only); the avatar and Monaco assets are
  served from the same origin.
