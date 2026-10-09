# Wave B brief: web-recruiter-review

FINAL ANSWER FORMAT: a concise report with sections SUMMARY, FILES CREATED, FILES MODIFIED, TESTS (commands + pass/fail counts), VERIFICATION (measured numbers), PUBLIC INTERFACES (signatures), DEVIATIONS FROM CONTRACT, REQUESTS FOR OTHER OWNERS, DEPENDENCIES NEEDED, KNOWN ISSUES.

You are a senior engineer building ONE module of a production-grade, enterprise AI animated-avatar interview platform.
Repository: /home/techsphere/Documents/project/Interview. Wave A (platform core, AI layer, sandbox service, TTS service, web foundation/design system, avatar engine,
infrastructure, catalog/samples) is DONE and verified. You are in Wave B: several agents build domain modules IN PARALLEL with STRICTLY
DISJOINT file ownership, coding against the contracts.

READ FIRST: /home/techsphere/Documents/project/Interview/docs/design/CONTRACTS.md, docs/design/ENGINE_DESIGN.md, docs/design/REALTIME_DESIGN.md, docs/design/UI_TEST_IDS.md (frontend agents MUST add every listed data-testid), the contract files they list
(config, enums, DB models, app/schemas/ai.py, rounds.py, policies.py, realtime.py, app/ai/base.py, app/services/*/contracts.py,
app/services/interview/state.py, apps/web/src/lib/realtime/protocol.ts, apps/web/src/features/avatar/types.ts), and the Wave A code you depend
on. Contracts are authoritative; if one truly must change, make the smallest backwards-compatible change and report it.

WAVE A FACTS (public interfaces you should use):
BACKEND PLATFORM (apps/api/app/core, app/services/storage, app/services/identity) — all verified, 784 API tests green:
- Errors: app.core.errors AppError(message=None, *, code=None, details=None, headers=None); BadRequest 400, Unauthorized 401, Forbidden 403,
  NotFound 404, Conflict 409, PayloadTooLarge 413, UnsupportedMediaType 415, ValidationFailed 422, RateLimited(retry_after=) 429,
  ServiceUnavailable(retry_after=None) 503. Envelope {"error":{code,message,request_id,details}}; Postgres unique/FK violations -> 409.
- Deps (app.core.deps): DbSession (get_db; commits BEFORE the response; the session is closed before StreamingResponse/SSE bodies run — open
  app.db.session.session_scope() inside streaming generators), RedisClient, CurrentPrincipal, OptionalPrincipal, StaffPrincipal,
  CandidatePrincipal (principal.interview_id, principal.candidate_id), require_permission(*perms) (dependency returning Principal),
  resolve_org_id(principal, requested), OrgScope, Pagination/PaginationParams (limit 1-200 default 50, offset). CSRF enforced automatically
  for cookie-auth unsafe methods (exempt /api/v1/auth/login, /api/v1/candidate-access/*).
- RBAC (app.core.rbac): Permission StrEnum (e.g. Permission.JOBS_WRITE == "jobs:write"), ROLE_PERMISSIONS, Principal(subject_type, user_id,
  candidate_id, organization_id, role, auth_session_id, interview_id, email, display_name) with has/has_all/has_any, is_staff, is_candidate.
- Security (app.core.security): generate_invite_token() -> (token, sha256); hash_token(); generate_access_code(); hash_access_code_async /
  verify_access_code_async; issue_ws_ticket(session_id, candidate_id, interview_id) -> str; consume_ws_ticket(ticket) -> WsTicket | None.
- Candidate sessions (app.services.identity.sessions): tokens = await start_candidate_session(db, candidate_id=, interview_id=,
  organization_id=, request=request); set_auth_cookies(response, tokens). Org settings: app.services.identity.get_organization_settings(db,
  org_id) -> OrganizationSettings.
- Queue (app.core.queue): await enqueue(job_type, payload, *, dedupe_key=None, delay_seconds=0, max_attempts=3, db=None) — pass db=session to
  create the job in the same transaction (dispatched after commit). @job_handler(type, *, concurrency=1, max_attempts=None,
  timeout_seconds=None) on functions shaped (payload) | (payload, ctx) | (ctx: JobContext) | keyword-only fields; PermanentJobError stops
  retries. Put handlers in app/workers/<domain>.py (auto-imported by the worker). Session bus: publish_session_event(session_id, {"type":
  ...}); async with subscribe_session_events(session_id) as sub: async for event in sub / await sub.get(timeout). register_periodic_job().
  Metrics labels: interview_jobs_total{type,status in succeeded|failed|retried|dead}; interview_queue_depth{queue="jobs:<type>"}.
- Rate limit (app.core.ratelimit): rate_limit(scope, limit, window_s, key_func=client_ip) dependency; enforce_rate_limit(scope, key, *,
  limit, window_s).
- Audit: await audit(db, principal|None, action, *, resource_type=None, resource_id=None, details=None, request=None, organization_id=None).
- Lifecycle (app.core.lifecycle): @on_startup(name, processes=("api",)), @on_shutdown, @background_task(name) fn(stop_event),
  run_after_commit(db, async_cb). Routers: every module in app/api/routers with a module-level "router" is auto-included under /api/v1
  (declare your own prefix/tags); modules named test_hooks* are included only when ENABLE_TEST_HOOKS=true; app.realtime.router.router is
  auto-included at the root. In dev/test a router that fails to import is skipped with an error log; production aborts.
- Storage: get_storage() -> LocalStorage (put/put_stream/open/read/exists/size/delete; keys validated; safe_filename(name));
  store_media(db, *, organization_id, kind, key, data, content_type, session_id=None, candidate_id=None, retention_days=None, details=None,
  max_bytes=None) -> MediaAsset; delete_media(db, media|id, *, reason); signed_url(media_id, ttl) -> "/api/v1/media/signed/{token}";
  signed_url_with_expiry(). Media authorization by kind already implemented in /api/v1/media/*.
- Seed: python -m app.seed [--core-only|--demo] [--print-credentials]; @seed_step(name, order=50, demo_only=False) fn(db, context). Demo org
  "Demo Corp" + users per role exist in the dev DB. Catalog seeded by app.seed.catalog (order 20) and demo jobs (order 30, demo only).
- Test fixtures (tests/fixtures/core.py): settings, db_session, redis, storage, app, client, org_factory(), user_factory(role, ...),
  auth_client(role|user=) -> AuthedClient (CSRF header set), candidate_auth_client(organization_id=, interview_status=) -> CandidateClient,
  worker_factory(**kwargs). Per-process throwaway database migrated with Alembic (0001 + 0002).

AI LAYER (app/ai) — 386 tests + live suite green:
- get_ai() from app.ai.registry -> .llm .embeddings .reranker .stt .ocr .tts ; tests use fixture mock_ai (MockAIServices: .llm.script(task,
  *items), .llm.set_default(task, item), .llm.calls_for(task), .stt.script(), .ocr.script(), .tts.fail_with, minimal_instance(Model)).
- llm.chat(messages, *, context: CallContext(task=ModelTask.X, session_id=, organization_id=, prompt_version=), endpoint=None, temperature,
  max_tokens, json_schema=None, thinking=False, timeout=None) -> LLMResult; llm.stream(...) -> LLMStream (async iterator of deltas; .text,
  .result, .ttft_ms, async aclose() safe from any task — closes HTTP within ~1 ms); llm.structured(Model, messages, *, context, ...,
  max_attempts=3, fallback=None) -> StructuredResult(value, raw_text, repaired, attempts, used_fallback, model_call_id). timeout = total budget.
- app.ai.structured: schema_for(Model) (pass as json_schema=), describe_schema(Model), StreamingJsonFieldExtractor("speech") with .feed(chunk)
  -> newly decoded text, .found, .done, .value, .finalize(Model) -> ParsedOutput(value, repaired) (raises AIOutputInvalid).
  Measured live: TurnDecision first speech char ~0.38 s, speech complete ~0.95 s, full object 5-9 s (~350 tokens) — stream speech, validate the
  rest at the end.
- embeddings.embed_documents(texts), embed_query(text, instruction) (1024-dim, normalized, Redis cached); reranker.rerank(query, docs, *,
  instruction=None, top_k=None) -> sorted [RerankScore(index, score)] (raises AIUnavailable on failure — keep your own order);
  stt.transcribe(audio, *, content_type="audio/wav", prompt=None, language=None, partial=False, context=None) -> TranscriptionResult (partial=True
  may raise PartialSkipped: drop it); ocr.ocr_image(image, *, mime_type, prompt=None) -> OcrResult(status ok|empty|degenerate|error) (outage
  raises AIUnavailable/AITimeout -> let the job retry); tts.synthesize(text, *, voice=None, speed=1.0) -> TtsResult(pcm16, sample_rate,
  duration_ms, visemes[VisemeCue(t_ms, viseme, weight)], provider, latency_ms) — every failure raises TtsUnavailable -> send tts.fallback.
- app.ai.prompts: FAIRNESS_RULES; render(template, **vars) (Jinja2 strict; "{{ FAIRNESS_RULES }}" and filter "| untrusted('label')" fences
  candidate-provided text against prompt injection — USE IT for every resume/answer/code text inserted into prompts); PromptTemplate(name,
  template).version / .render().
- app.ai.visemes: text_to_visemes(text, duration_ms=None), phonemes_to_visemes(ipa, duration_ms=None).
- Retrieval (app.services.retrieval): vector_search(db, Model, query_vector, *, filters=dict|[exprs], limit=10, max_distance=None) ->
  [VectorHit(item, distance, similarity)]; retrieve(query, *, candidates=DbCandidateSource(db, Model, text="text", filters=...) | callable |
  [RetrievalCandidate], top_k=5, rerank=True, instruction=None, rerank_instruction=None, pool_size=None, min_score=None) -> [RetrievedItem(item,
  text, score, vector_similarity, rerank_score, rank)]; find_near_duplicate(db, Model, vector, threshold=0.92, filters=);
  embed_and_store(db, rows, *, text="text"|callable) ; embed_and_store_by_ids(db, Model, ids, text="text").
- Model routing: turn-critical tasks -> main 35B (8B router is 2.4-4.8 s per small JSON here); vision/memory_summary/document_classification
  -> 8B; fallback main->vision when main's circuit is open.

SERVICES (running, healthy):
- TTS http://127.0.0.1:8210 (Kokoro, 54 voices, default af_heart): /v1/tts measured 0.5 s for 6 words, 0.8-1.2 s for 15 words, cache hit 3 ms;
  max 2 concurrent syntheses (queue 32). Use short first chunks for low first-audio latency.
- Sandbox http://127.0.0.1:8220: all 7 languages verified (p50 tests mode: python 278 ms, js 294, ts 372, java 683, go 442, cpp 696, sql 319);
  ExecuteRequest/ExecuteResponse exactly per app/services/coding/contracts.py; HMAC headers X-Sandbox-Timestamp + X-Sandbox-Signature (fresh
  timestamp per attempt — identical signatures are rejected as replays); semantic problems return 200 status "rejected"; 503 with Retry-After
  on capacity; hidden tests still carry actual/error/runtime_ms in the response — NEVER forward those to candidates; store meta as
  code_runs.sandbox_meta; HTTP timeout >= 15 s + compile limit + 2 x time limit + 10 s.
- Catalog in dev DB after seeding: 43 competencies, 12 global default templates (one per RoundType, valid RoundConfig), 181 curated questions
  (embeddings NULL until a question_bank.embed job runs), 16 coding questions (13 function problems with 6-language starters/references,
  3 SQL), 4 demo jobs (Senior DevOps Engineer: Combined AI Round 30 min with coding pinned to most_frequent_error_service, candidate report
  immediate; Backend Engineer (Python) allows candidate round selection). Loader helpers: app.seed.loaders (compare_results reference
  semantics, entry_point_name, resolve_job_rounds), app.seed.catalog.default_round_type_templates() for GET /round-types.
  Sample resumes: samples/resumes/ (Jane Doe PDF/DOCX/TXT/PNG/JPG/scanned-PDF + 3 others, README lists intended claims/gaps).

WEB FOUNDATION (apps/web) — typecheck/build/256 tests green:
- api (@/lib/api): api.get/post/put/patch/delete<T>(path, opts?), api.upload<T>(path, FormData, {onProgress, fields, signal}), ApiError
  {status, code, message, requestId, details, retryAfterSeconds, fieldErrors()}, errorMessage(err). Only relative /api/v1 paths.
- @/lib/types: TS mirrors of all backend enums + DTOs (Page<T>, Job, Round, RoundConfig, Candidate, CandidateResume, Interview, SessionSummary,
  SessionDetail, TranscriptSegment, SessionQuestion, EvidenceRecord, ScoreRecord, SessionTimeline, CodingSessionDetail, Report,
  ReviewDecision, AnalyticsOverview, IntegrityEvent, InviteInfo, PortalInterview, Disclosure, ConsentRequest, WsTicketResponse...). Refine in
  your own files when the backend shape differs.
- @/lib/auth: useAuthStore, useCurrentUser, usePermission, RequireAuth/RequireRole/RequirePermission (staff only; candidates use their own
  portal guard via /api/v1/portal/me). @/lib/query: queryKeys.*, createQueryClient(); mutation meta successToast/silentError.
- @/components/ui: full design system incl. Button, Input, Select, MultiSelect, Tabs, Dialog, Sheet, DataTable, FileDropzone, Stepper,
  Markdown, CodeBlock, useZodForm/Form/FormField/applyServerErrors, confirm(), toast. @/components/domain: ScoreRing, ScoreBar,
  ConfidenceMeter, RecommendationBadge (always shows HUMAN_DECISION_NOTE), DifficultyBadge, StatusBadge, EvidenceSignalIcon,
  IntegritySeverityBadge. @/components/charts: CompetencyRadar({data}), ScoreTimelineChart({snapshots, competencies, durationSeconds}),
  BarList, DistributionBar, Sparkline. @/components/layout: StaffShell/PageContainer/PageHeader, CandidateShell + useCandidateChrome({orgName,
  subtitle, steps, completed, hideStepper, width}), StageShell({header, footer}) (dark interview room).
- Routes exist (lazy) for every page in CONTRACTS §10; /candidate/:interviewId/interview is mounted OUTSIDE the candidate frame (full-screen
  StageShell). Placeholder pages at the fixed paths are yours to replace (default export, same file names).
- Avatar (@/features/avatar): createAvatarController(config) and <AvatarStage controller={c} framing="bust"|"upper-body" />, preloadAvatar(),
  estimateWordDurationMs(word), DEFAULT_AVATAR_URL="/avatars/interviewer.glb". Mapping: avatar.behavior / ai.utterance.start.behavior ->
  applyBehavior; session.state.avatar_state -> setState; tts.chunk + audio scheduled at AudioContext time T -> enqueueSpeech({utteranceId,
  chunkId, startTime: T, durationMs, visemes, text}); playback chain ready -> attachAudio(ctx, analyser); barge_in -> interrupt();
  tts.fallback -> speakWordBoundary(word, estimateWordDurationMs(word)) on speechSynthesis boundaries; mic VAD -> setCandidateSpeaking(speaking,
  level). 60 fps on the GB10 GPU; 2D fallback on WebGL loss.

INFRA: edge Caddy (:3200/:3443) proxies /api and /ws to 127.0.0.1:8200 (do not use 8200 for your own test servers), CSP 'self' only
(Monaco must be self-hosted; no CDNs), body limits 25 MiB resume/recording uploads, 4 MiB frames, 2 MiB others. Prometheus 127.0.0.1:9191,
Grafana 127.0.0.1:3301. The WebSocket Origin header must be validated by the API (edge does not); build ws_url from the request host or let
the SPA use location.host (candidates may use https://<lan-ip>:3443).


ENVIRONMENT
- DGX Spark aarch64, shared with another production AI stack. Compose project "interview": postgres 127.0.0.1:5442, redis 127.0.0.1:6390,
  tts 127.0.0.1:8210 and sandbox 127.0.0.1:8220 containers are running; prometheus/grafana may be running. Credentials in /home/techsphere/Documents/project/Interview/.env
  (never print secrets). Local AI endpoints are live but shared (keep live tests tiny): main LLM :8000 (35B, fast; thinking off for latency),
  vision :8002 (8B, slow), embeddings :8003, reranker :8005 (/score, templated), whisper 172.17.0.1:30007 (RTF ~0.3: 1.8 s per 5 s clip),
  OCR 192.168.9.68:30004 (prompt "OCR").
- Python from /home/techsphere/Documents/project/Interview/apps/api with "uv run ..."; Node from /home/techsphere/Documents/project/Interview/apps/web with npm scripts. Do NOT edit pyproject.toml/package.json or
  lockfiles: report needed dependencies.
- Storage is LOCAL FILESYSTEM ONLY via app.services.storage (user requirement: no S3/MinIO/boto3).
- Run the API locally for manual checks with: cd apps/api && uv run uvicorn app.main:app --host 127.0.0.1 --port <free port 8300-8399>
  (do NOT use 8200 — reserved for the integrated stack) and the worker with uv run python -m app.worker if you need jobs to run
  (use a dedicated Redis DB or job namespace if the platform supports it; otherwise be careful not to process other agents' jobs).
- Tests: shared fixtures from tests/fixtures/{core,ai}.py (per-session throwaway database, redis, app/client, auth clients, mock AI). Put your
  own fixtures in your own test package (e.g. tests/unit/<module>/conftest.py). Mark live-model tests @pytest.mark.live, DB tests
  @pytest.mark.integration.

ENGINEERING RULES
- Production quality; real implementations; secure by default; typed; structured logs (app.core.logging); metrics only from app.core.metrics.
- Never hard-code endpoints/models/secrets. Fairness rules from app.ai.prompts.FAIRNESS_RULES in every evaluative prompt; never infer protected
  traits; integrity signals are observations for humans, never verdicts.
- Write real tests and RUN them until they pass; verify end to end where you can (run the API on a spare port, call endpoints, drive the
  flow with mocks or tiny live calls). Report measured numbers.
- Touch ONLY files you own (create new files inside owned dirs freely). If another owner's file needs a change, describe it precisely in
  requests_for_other_owners instead of editing. Never edit Wave A files except where explicitly allowed below.
- Run formatters/linters ONLY on files you own (e.g. "uv run ruff format app/services/coding tests/unit/coding"), never on shared
  directories — a formatter run over app/seed in Wave A rewrote another owner's files.
- Do not leave dev servers or background processes running when you finish (kill what you started, by PID).
- Safety: never "docker compose down -v", never remove volumes, never touch other compose projects or anything outside /home/techsphere/Documents/project/Interview. No git.
- Final answer: the structured report.

YOUR MODULE: recruiter review/insight UI: dashboard, sessions list + session detail (the "why" behind every score), live session monitor,
recruiter report (printable), integrity review queue, analytics. Load the "frontend-design" skill first; premium enterprise quality.

YOU OWN: apps/web/src/features/recruiter/review/** and these page files (replace the placeholders): src/features/recruiter/pages/{DashboardPage,
SessionsPage,SessionDetailPage,RecruiterReportPage,IntegrityReviewPage,AnalyticsPage}.tsx. Another agent concurrently owns
src/features/recruiter/admin/** and the other recruiter pages. Use the design system in src/components (request additions; do not edit),
src/lib/api.ts, src/lib/types.ts (add your own types in your own files).
BUILD
1. Dashboard: KPIs (active/live interviews, completed this week, average overall score, pending human reviews, unreviewed integrity flags),
   recent sessions, live sessions, funnel (invited -> started -> completed -> reviewed), score distribution, quick links.
2. Sessions list (filters: status, job, round, recommendation, date; live indicator) and Session detail with tabs: Overview (overall score,
   rubric categories, recommendation shown as ASSISTIVE with confidence and the human-decision note, duration, round, end reason),
   Transcript (speaker-labelled, timestamps, interruptions, per-answer audio playback via signed URLs, search, jump to question),
   Questions & Evidence (each question -> answer -> evaluation dimensions -> evidence items with signal/strength/confidence and quotes;
   follow-up chains; difficulty badges), Competencies ("why this score": score + confidence + positive/negative evidence + unanswered areas +
   supporting questions + transcript references, drill-down), Timeline (ScoreTimelineChart from /sessions/{id}/timeline with window selector:
   e.g. Minute 0-10 AWS 7.2, Kubernetes 6.5...), Coding (problem, final code, snapshot slider with diff view, runs with test results, hints used
   with levels/timestamps, coding evaluation incl. assistance impact), Integrity (events timeline with severity, confidence, evidence frame
   thumbnails via signed URLs, review controls; banner: "Signals are observations, not determinations"), Plan & claims (interview plan, claims
   verified/unverified, contradictions), Review (human decision form: advance/hold/reject/needs more info + notes; history; shows the AI
   recommendation at review time and whether the reviewer overrode it).
3. Live monitor: read-only near-real-time view of a live session via SSE (/api/v1/sessions/{id}/live): current question, streaming
   transcript, competency estimates updating, integrity events.
4. Recruiter report page: complete printable report (print CSS) with every section from the report API (candidate, job, round, duration,
   competency scores, question-by-question evaluation, transcript excerpts, coding results, hints, claims verified/not verified,
   contradictions, competency evidence, AI confidence, integrity summary, recommendation (assistive), areas for human follow-up), buttons:
   regenerate, release candidate report (when mode requires), export JSON, print/PDF.
5. Integrity review queue: org-wide unreviewed events with filters, evidence viewer, bulk review with notes, clear non-accusatory language.
6. Analytics: funnel, score distributions by job/round, competency averages, completion rates, durations, integrity event rates, hint usage,
   model usage and latency (from /analytics endpoints).
7. Tests (vitest) for data transforms (timeline bucketing display, evidence grouping) and npm run typecheck/test/build. Playwright screenshots
   of every page with mocked API data (port 5177) — inspect and iterate until it looks premium.