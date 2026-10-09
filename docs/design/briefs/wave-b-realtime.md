# Wave B brief: realtime

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

YOUR MODULE: the realtime interview runtime (REALTIME_DESIGN.md) and the candidate portal REST API.

YOU OWN: apps/api/app/realtime/** (router.py exposing "router" with the WebSocket route /ws/sessions/{session_id} — the app factory
auto-includes app.realtime.router.router), app/api/routers/{portal,candidate_access}.py, app/schemas/api/portal.py, tests/unit/realtime/**,
tests/integration/realtime/**.
DEPENDENCIES BUILT CONCURRENTLY (code against contracts; adapters + stubs for tests): TurnEngine + SessionService (engine agent,
app/services/interview), CodingService (coding agent), IntegrityService + DisclosureBuilder (integrity agent, app/services/integrity/disclosure.py
exposing build_disclosure(policy_snapshot, organization_name=..., job_title=...) -> Disclosure), ResumeService (resume agent,
app/services/resume/service.py). Get implementations through small provider functions (e.g. app/realtime/deps.py get_turn_engine()) so the
integrator can wire them and tests can inject fakes.
BUILD
1. WebSocket endpoint: ticket auth (consume_ws_ticket), Origin header validation (the edge does NOT check it): allow
   settings.public_base_url, settings.cors_allowed_origins, and same-host origins (scheme+host:port equal to the request Host, so
   https://<lan-ip>:3443 and http://localhost:3200 both work); return ws_url built from the request host in the ws-ticket response (or a
   relative path the SPA resolves against location.host), protocol per
   app/schemas/realtime.py (validate every client message with client_message_adapter; reject oversized frames; per-connection rate limits),
   hello/last_seq replay from the outbox, session lock (session:{id}:lock with instance id, refresh, stale takeover), registry of runtimes
   surviving reconnects, heartbeat watchdog, graceful close codes.
2. Audio ingest: binary 0x01 frames (sequence gaps tolerated), PCM ring buffer, Silero VAD via onnxruntime (settings.vad_model_path; 512-sample
   windows with state handling; energy VAD fallback), pre-roll, segmentation with max segment length split at the lowest-energy point,
   ordered segment finals via the AI layer STT client with the Whisper prompt (question focus + profile/job vocabulary), throttled partials
   (low priority, skipped when saturated, adaptive interval), adaptive end-of-turn (REALTIME_DESIGN.md §4), think-pause extension, manual
   end_turn, max utterance, typed answers (policy allow_typed_answers).
3. Turn control FSM + engine integration: CandidateTurn with timings + WAV audio (when recording policy + consent allow), consume TurnEvent
   streams (UtteranceBegin/SpeechDelta/UtteranceMeta/CodingStarted/CodingHintIssued/CodingEnded/SessionClosing/StateChanged) and translate to
   protocol messages; engine.on_partial_transcript during silence; mark_utterance_delivered from playback reports; silence timeout timer.
4. SpeechStreamer: sentence chunker (abbreviations, decimals, first-chunk early split), TTS pipeline (AI layer TtsProvider; up to
   settings.tts_max_inflight_per_session ahead; strict ordering), tts.chunk (text, duration, visemes, chunk_id) + binary 0x02 frames,
   ai.text.delta subtitles (policy show_live_subtitles), metrics time-to-first-audio and turn latency, TTS failure -> retry once -> tts.fallback +
   degraded mode with health probe recovery. Acknowledgement backchannels: pre-synthesize a varied set per voice at startup (cache), play at end
   of turn per REALTIME_DESIGN.md §5.
5. Barge-in per REALTIME_DESIGN.md §7 (stricter threshold while AI speaks, cancel engine iterator + TTS, barge_in message, spoken-portion
   computation from played chunk durations, handle_interruption, backchannel resume vs new turn); vad.local hints logged.
6. Coding bridge: code.update/run/submit/hint_request/language -> CodingService + engine.handle_coding_activity; run results ->
   coding.run_result; idle detection timer -> CodingActivity(kind="idle").
7. Integrity bridge: integrity.events -> IntegrityService.record_client_events -> integrity.warning messages when outcomes say so; server events
   (disconnect/reconnect/page_reload) via record_server_event; session:{id}:bus listener -> integrity.warning / report.ready / state refresh;
   policy actions pause/terminate only for objective conditions.
8. Reconnection + failures exactly per REALTIME_DESIGN.md §8 (grace period, clock pause, replay, re-speak, resumable PAUSED sessions,
   max_reconnects), notices for STT/TTS degradation, never silently drop a turn.
9. Portal REST per CONTRACTS §4 / REALTIME_DESIGN.md §9 (after verification call
   app.services.identity.sessions.start_candidate_session(db, candidate_id=, interview_id=, organization_id=, request=request) then
   set_auth_cookies(response, tokens); /portal routes use CandidatePrincipal): candidate-access GET (public invite info: org name, job title, candidate first name,
   requires_access_code, expired/cancelled flags) and POST verify (rate limited per IP + per invite, lockout after 5 failures, email must match
   case-insensitively, access code verify, sets candidate cookies via the platform auth helpers, marks invite opened/verified, audit), portal
   me/interview detail (steps completed: consent, resume, round, system check), disclosure (DisclosureBuilder), consent (policy_hash must match
   the current disclosure; store items), resume upload/status (ResumeService), round selection (only when allowed; validates round belongs to
   job and is candidate_selectable), system-check record, session create/resume (SessionService), ws-ticket, frames (IntegrityService.submit_frame;
   size/type limits; only when consent + policy enable camera analysis), integrity-events batch, recording chunks (only when policy + consent
   enable video recording; stored via storage with retention), candidate report (released only).
10. Tests: unit tests for chunker, endpointing, segmentation, VAD wrapper (synthetic audio), barge-in decisions, outbox replay; an integration
    test that drives a full WebSocket session through the ASGI app with a FAKE engine + real audio from tests/e2e/fixtures/audio/*.wav (16 kHz
    PCM WAVs of candidate answers; convert to frames) and a mocked STT, asserting message order, latency bookkeeping and barge-in; plus one
    @pytest.mark.live test using the real Whisper and TTS service on one clip (report STT and TTS latencies).