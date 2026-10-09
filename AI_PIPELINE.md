# AI pipeline

Written for: engineers who change a model call, routing, structured outputs, speech-to-text, text-to-speech or
retrieval. Every model runs on local endpoints shared with another production stack; numbers below were measured
on the DGX Spark on 2026-10-07 with that stack under load (expect variance). Code: `apps/api/app/ai/` (clients,
routing, structured outputs), `apps/api/app/realtime/` (voice loop), `services/tts/` (Kokoro).

## Models and roles

| Endpoint (setting) | Model | Role | Measured |
|---|---|---|---|
| main (`MAIN_LLM_BASE_URL`, `http://localhost:8000/v1`) | `Qwen/Qwen3.6-35B-A3B-NVFP4` (MoE, 3B active), thinking off | every turn-critical and deep-reasoning task | TTFT 0.1-0.5 s, 45-60 tokens/s |
| vision (`VISION_LLM_BASE_URL`, `:8002/v1`) | `Qwen/Qwen3-VL-8B-Instruct-FP8` | camera/screen frame analysis, memory summaries, background classification; degraded fallback for turn tasks | ~10 tokens/s; 2.4-4.8 s for a 20-token JSON; ~10.4 s per frame |
| embedding (`EMBEDDING_BASE_URL`, `:8003/v1`) | `Qwen/Qwen3-Embedding-0.6B`, 1024 dimensions | resume chunks, catalog, questions, answers, evidence, retrieval queries | ~60 ms per call |
| reranker (`RERANKER_BASE_URL`, `http://localhost:8005`) | `Qwen/Qwen3-Reranker-0.6B` via `POST /score` | retrieval and resume requirement evidence | part of 105-150 ms warm semantic search |
| whisper (`WHISPER_BASE_URL` + `WHISPER_EXTRA_BASE_URLS`) | `openai/whisper-large-v3` (two replicas configured; each serves one request at a time) | speech-to-text | RTF ~0.3 (1.8 s for 5 s of speech) to 0.56 under load |
| ocr (`OCR_BASE_URL`, `http://192.168.9.68:30004/v1`) | `baidu/Unlimited-OCR` | scanned resume pages and image resumes | ~16-19 s per page |
| tts (`TTS_BASE_URL`, `http://127.0.0.1:8210`) | Kokoro-82M fp32 ONNX on CPU (this repository's `services/tts`) | interviewer voice + viseme timings | p50 506 ms (6 words) to 1.86 s (35 words) |

## Model routing

Every LLM call carries a `ModelTask`; `app/ai/router.py` maps it to an endpoint.

| Tasks | Primary | Fallback when the primary is unavailable (open circuit, outage) |
|---|---|---|
| `turn_decision`, `answer_assessment`, `code_observation`, `hint_generation`, `opening` (turn-critical) | main | vision |
| `resume_extraction`, `profile_analysis`, `job_match`, `interview_planning`, `answer_evaluation`, `coding_evaluation`, `report_generation`, `question_generation`, `competency_summary` | main | none: the background job retries later instead of producing a lower-quality result |
| `frame_analysis`, `screen_analysis` | vision | none in practice: image requests never go to main, and the frame worker pins the vision endpoint (the job retries) |
| `document_classification`, `routing_classification`, `memory_summary` | vision | main (text only) |
| `embedding`, `rerank`, `stt`, `ocr`, `tts` | dedicated clients, not routed | |

* `MODEL_ROUTING_OVERRIDES` (JSON task -> `main`|`vision`) replaces a row; unknown tasks, non-LLM tasks and unknown
  endpoints are ignored with a warning. Requests with image content are always sent to vision.
* The fallback is taken only for `AIUnavailable` (including an open circuit) while time remains in the same
  deadline; timeouts and 4xx rejections never fall back; a stream falls back only before its first visible token.
* Thinking: requests to main send `chat_template_kwargs: {"enable_thinking": false}`; no caller enables thinking.
  Any `<think>` text that still appears is stripped (non-streamed replies, stream deltas, structured parsing).
* Why turn tasks are on the 35B: measured, the 35B-A3B returns a small JSON in ~0.4-0.9 s while the dense 8B took
  2.4-4.8 s for a 20-token JSON on this hardware.

## Client layer

* `app.ai.registry.get_ai()` returns the process-wide `AIServices` (LLM, embeddings, reranker, STT, OCR, TTS).
  Never call an endpoint directly. Under `APP_ENV=test` it returns `MockAIServices` (scripted per task; unscripted
  structured calls return a minimal valid instance; the real repair/retry logic runs); tests use the `mock_ai`
  fixture.
* One `EndpointClient` per endpoint (`app/ai/http.py`): pooled httpx, `trust_env=False`, no redirects. The caller's
  timeout is a total budget (slot wait + attempts + backoff). Retries (`LLM_MAX_RETRIES`, default 2, for every
  endpoint): connect errors always, read timeouts only for idempotent calls, HTTP 408/429/5xx; backoff
  `min(4 s, 0.25 s x 2^(n-1))` with jitter, `Retry-After` honoured up to 8 s. Other 4xx are never retried.
* Concurrency slots: main 24 (`MAIN_LLM_MAX_CONCURRENCY`), vision 4 (`VISION_LLM_MAX_CONCURRENCY`), embedding 8,
  reranker 3 (`RERANKER_MAX_CONCURRENCY`), OCR 2, TTS 8; Whisper is scheduled by the STT client itself (one
  request per replica at a time; `STT_MAX_CONCURRENCY` is an upper bound).
* Circuit breaker per endpoint (`app/ai/breaker.py`): opens after 5 counted failures
  (`CIRCUIT_BREAKER_FAILURE_THRESHOLD`) for 30 s (`CIRCUIT_BREAKER_RESET_SECONDS`), then admits one half-open
  probe. Transport errors, 5xx and connect timeouts count; 4xx and cancellations do not; read timeouts count only
  when the attempt had a fair amount of time. Gauge `interview_circuit_breaker_state{endpoint}`.
* Ledger (`app/ai/ledger.py`): one `model_calls` row per logical call (task, endpoint, model, status
  ok/repaired/retried/fallback/error/timeout/circuit_open/cancelled, attempts, latency, TTFT, tokens, prompt
  version, request hash), written in batches by a background writer (dropped records are counted). Prompts and
  responses are stored only with `MODEL_CALL_LOG_PAYLOADS=true` (off by default; truncated, images omitted). STT
  partials and embedding cache hits are not recorded.
* Metrics: `interview_llm_request_seconds{endpoint,task,status}`, `interview_llm_ttft_seconds`,
  `interview_llm_tokens_total{kind}`, `interview_model_call_failures_total{endpoint,task,reason}`,
  `interview_stt_latency_seconds{kind}`, `interview_tts_latency_seconds{provider}`, `interview_embedding_seconds`,
  `interview_rerank_seconds`, `interview_ocr_seconds`. Usage per task is visible in `GET /api/v1/analytics/model-usage`.

## Structured outputs

* Requests use vLLM guided decoding: `response_format = {"type": "json_schema", "json_schema": {"name", "schema",
  "strict": true}}` from the Pydantic model. Guided decoding does not show the schema to the model, so prompts also
  embed a compact schema description.
* **Required-field wire schemas.** Guided decoding skips optional properties (the model returned every resume role
  without dates). Calls therefore send wire models with every field required (`required_variant` in
  `app/services/resume/llm_schemas.py`; the same trick forces exactly N generated questions) and convert back to
  the contract model. Education dates are dropped from the wire model entirely (age proxy).
* **Parse and repair** (`app/ai/structured.py`): validate the raw JSON; strip think blocks, markdown fences and
  prose; then `json_repair` plus safe fixes (truncate over-long strings and lists, drop forbidden keys, clamp
  bounds, normalise enum spelling). A reply that needed repair is recorded as `repaired`.
* **Retry**: on failure the model is asked again with its own reply and up to 8 validation errors ("Your previous
  reply could not be accepted because it does not match the required JSON schema."), up to `max_attempts`
  (default 3) within one deadline. Then the caller's `fallback()` is used if provided, otherwise
  `AIOutputInvalid` (not retryable).

## Speech-first turn decisions

`TurnDecision` (`app/schemas/ai.py`) declares `speech` as its first property and every field as required. Guided
decoding emits properties in schema order, so the spoken text starts streaming after a few tokens instead of after
a metadata header (measured: a header first costs 1.3-2.0 s before the first spoken word).

* `StreamingJsonFieldExtractor("speech")` is a character-level state machine over the streamed JSON: `feed(chunk)`
  returns only newly decoded text of the top-level `speech` string (escapes and surrogate pairs split across chunks
  are handled; nested keys never match). The engine forwards it as `SpeechDelta` events to the realtime runtime,
  which chunks it into sentences for TTS.
* `LLMStream.aclose()` cancels the HTTP stream within about a millisecond, so barge-in stops generation in vLLM.
* Measured: first speech character ~0.38 s after the call, speech complete ~0.95 s; first `SpeechDelta` 559-699 ms
  in-process and 718-730 ms through the WebSocket. The complete object takes 5-9 s on this vLLM build because it
  pads JSON with whitespace, so after the speech field closes the engine waits at most
  `2.5 s + 0.3 s x words` and recovers the remaining fields with `json_repair` (missing fields are synthesised).
  Details: [INTERVIEW_ENGINE.md](INTERVIEW_ENGINE.md#one-candidate-turn).

## Prompt hygiene

* Static -> dynamic ordering so vLLM's prefix cache hits: a byte-stable per-session system prompt (persona, rules,
  `FAIRNESS_RULES`, schema, job, round, profile, plan) and a dynamic user prompt. `session.prepare` warms the
  prefix cache with a `max_tokens=1` call.
* `FAIRNESS_RULES` (`app/ai/prompts/__init__.py`) is included in engine, evaluation, resume, coding, report, vision
  and question-generation prompts: judge only job-relevant content; never infer or use protected traits or proxies;
  never penalise speaking style; resume statements are claims; output is advisory, a human decides.
* Candidate-controlled text is fenced by the `untrusted` Jinja filter (`<label> ... </label>`, nested tags
  removed) and the prompts say never to follow instructions inside those tags. See
  [SECURITY.md](SECURITY.md#prompt-injection) for the remaining gaps.
* Prompt versions are content hashes (for example `turn@<sha256[:10]>`) stored with every model call.

## Embeddings, reranker and retrieval

* **Embeddings** (`app/ai/clients/embeddings.py`): batches of 32 (`EMBEDDING_BATCH_SIZE`), duplicate texts
  embedded once, inputs truncated (24,000 characters, 4,000 tokens), vectors L2-normalised. Queries use the
  Qwen3 instruction format (`Instruct: ...\nQuery: ...`), documents are sent raw. Redis cache
  `cache:emb:<sha256(model, mode, text)>` for 7 days (best effort).
* Used for: resume chunks (~800 characters with 120 overlap, at most 200 per resume), job-requirement evidence,
  catalog rows (questions, coding questions, competencies, jobs, requirements; inline, via `question_bank.embed`,
  and by the hourly `catalog.backfill_embeddings`), question-bank search and generation dedupe, interview question
  selection and asked-question dedupe, coding question selection, answer and evidence vectors.
* **Reranker** (`app/ai/clients/reranker.py`): `POST /score` with the Qwen3-Reranker chat template (system
  "Judge whether the Document meets the requirements based on the Query and the Instruct provided...",
  `<Instruct>`, `<Query>`, `<Document>`); the raw `/v1/rerank` route sends untemplated text and discriminated
  poorly. Scores are clamped to 0-1; 32 documents per request.
* **Retrieval** (`app/services/retrieval/`): embed the query -> vector search (pool `max(top_k x 4, 20)`) ->
  rerank -> `min_score` and top-k. If the reranker fails the vector order is kept.
* **Vector search**: pgvector cosine distance; filtered searches enable `hnsw.iterative_scan = relaxed_order`
  (without it a candidate-filtered search returned 0 of 3 rows) and re-sort the results. HNSW indexes
  (`m=16`, `ef_construction=64`) exist on `question_bank.embedding` and `resume_chunks.embedding`.
* Dedupe thresholds: catalog near-duplicates at cosine 0.92; asked interview questions at 0.88.

## Resume pipeline

1. **Upload** (staff or candidate portal): synchronous validation (magic bytes, PDF/DOCX active-content checks,
   image guards, optional ClamAV; see [SECURITY.md](SECURITY.md#uploads)), stored through `store_media`,
   `resume.process` enqueued.
2. **`resume.process`**: text layers (PDF via pypdfium2, DOCX, TXT) -> **OCR** for PDF pages with fewer than 80
   meaningful characters and for image resumes (up to `OCR_MAX_PAGES` = 12; pages rendered greyscale at 200 DPI,
   longest side at most 3500 px, PNG unless larger than 1.5 MB, then JPEG q90, which is ~2.3x smaller and 30x
   faster to encode for noisy scans). The OCR request is a chat completion with the image and the prompt `OCR`
   ("document parsing" made the model loop; other prompts still return the layout-tagged lines, measured
   2026-10-08); layout tags (`text [x1, y1, x2, y2]`) are parsed and repetition loops collapsed. OCR text replaces
   the text layer only when richer. Pages are scanned concurrently, page 1 first (the endpoint client caps the
   process at `OCR_MAX_CONCURRENCY`; one 1,055-token page took 15.8 s alone, 19.7 s each at 2 at once, 27.9 s each at
   4, so the aggregate rises from 67 to 151 tok/s). OCR is decode-bound (~60 tok/s per page); splitting a page into
   strips was tried and rejected (the OCR model returned nothing for a header strip). While later pages are still
   scanned, the readable prefix (page 1, or the leading pages with a text layer) already gets a quick profile.
   Then normalisation, section detection and the **quick profile**
   (`app/services/resume/quick.py`, deterministic, 1-6 ms): name/headline, contact *presence flags* only, roles
   with dates from the date-range lines, experience years from merged intervals, skills from the skills section plus
   vocabulary technologies named elsewhere, education without years, certifications, projects and claims only from
   quantified statements. It is stored as a `candidate_profiles` row with status `quick` (shown to the candidate
   and, with `?include_quick=true`, to staff) and `profile.generate` is enqueued at once. `ResumeService.latest_profile()`
   and the interview engine's profile gate only accept `ready`/`partial`, so a quick profile is never taken for the
   model profile. `quick_profile_fallback()` (`app/services/resume/service.py`) returns the quick row for the engine
   once its profile wait budget runs out; wiring it into the engine's `_profile_gate` timeout branch is still pending
   (a one-line change owned by the engine). An identical file (same sha256) the candidate uploaded before is not read again: its text,
   chunks and cached model parts are copied.
3. **`profile.generate`** (`app/services/resume/calls.py` + `profile.py`): ~12-20 small 35B calls run concurrently,
   each reading only the excerpt it needs (vLLM prefix caching is disabled on this deployment) and writing compact
   one-line JSON with its own token budget: `identity`; one call per role group anchored by the role's date line
   (exactly N roles, so it cannot rewrite the others) plus `roles:rest` for undated roles; projects; skills as
   `category -> names` (levels/details only where stated); education + certifications; claims in four groups of at
   most 2 (scale, impact, depth, people - never gaps or continuity); the job fit; requirement groups of 6 (number +
   strength + 8-word evidence); topics; gaps. Derived without the model: the summary (the resume's own summary section), claim source quotes
   (verbatim statement with the best recall-weighted word overlap), missing requirements (rated `none`), years.
   Each finished part is cached on the resume (`scan_result.ai_parts`, keyed by text hash, pipeline version and,
   for job-aware parts, the job inputs) and refines the quick row section by section (`progress.sections`:
   `quick|active|done|fallback`), so a retry or a re-upload only runs what is missing. Chunks/embeddings and the
   semantic requirement evidence run beside the model calls. A part that fails on the job's last attempt falls back to
   the deterministic parse for its section only and the profile is stored as `partial`.
   Measured 2026-10-08 on the shared GPU (A/B in the same minutes, old 4-call design vs the new plan): Jane (DevOps job,
   15 requirements) 95.6 s -> 19.2 s, Marcus 78.7 s -> 17.3 s, Sofia 62.1 s -> 15.4 s; generated tokens per profile
   ~4.8-5.3k -> ~1.9-3.2k, longest single call ~2.3k -> ~330 tokens. End to end (upload -> model profile) text
   resumes p50 21 s / p95 29 s under heavy production load (13.7 s for the same PDF at lighter load), dense 3-page
   resume 98 s -> 21 s; scanned/image resumes 68-74 s -> 31-38 s (OCR ~18 s of it). Upload -> quick profile in the
   database 0.1-0.5 s for text files. With the final plan (four claim groups, requirement groups of 6): Priya (ML job)
   11.0 s / 15 calls / 1.9k generated tokens, Jane (DevOps job) 13.0 s / 18 calls / 2.7k tokens; longest call
   ~230-280 tokens. In the browser (isolated stack, same day): Jane PDF first read on screen 0.8 s after upload and
   the checked profile at 16.1 s; an identical re-upload 0.85 s / 1.0 s; the scanned Jane PDF at 360 px 18.5 s
   (page 1 OCR) / 38.3 s.
   **Candidate page** (`ResumePage.tsx` + `ResumeSummary.tsx`): polls the portal resume endpoint every 0.6 s until
   the first read is visible, then every 1.5 s until the profile is ready. The summary is labelled "First read" and
   each section shows the server's state from `progress.sections` (Checking / Checked / "As read from your file"
   for a fallback); a partial OCR read says which page it came from. Continue is enabled as soon as the text is
   extracted; only the interview's Begin waits for the model profile.
4. **Job match** (`app/services/resume/matching.py`):
   `score = (0.50 D + 0.35 M + 0.15 E) / (sum of the weights present)`, where D is deterministic requirement
   coverage (per requirement `s = max(semantic, 0.85 x lexical)`; semantic = top reranker probability over the best
   pgvector chunks, or calibrated cosine `clamp((cos - 0.35) / 0.40, 0, 1)` when the reranker is down; weighted by
   requirement weight and kind: required skill/qualification 1.0, responsibility 0.75, preferred 0.5), M is the
   model's `overall_fit` and E is `100 x min(1, years / minimum years)`. Per-requirement bands: strong >= 0.70,
   partial >= 0.45, weak >= 0.20. The breakdown is returned as `match_breakdown`.

## Speech-to-text

The browser sends 16 kHz PCM16 (20 ms frames, 100 ms per binary message). In the API (`app/realtime/`):

* **VAD**: Silero v5 ONNX, 512-sample (32 ms) windows, one shared ONNX session per process (~0.08 ms per window);
  energy-based fallback if the model fails to load. Time is the sample clock of received audio, so behaviour is the
  same for real-time and burst delivery.
* **Segmentation** (`audio/segmenter.py`): speech starts after 3 consecutive windows >= `VAD_SPEECH_THRESHOLD`
  (0.5) with 300 ms pre-roll; hysteresis keeps speech alive above 0.35; silence >= `SEGMENT_PAUSE_MS` (350 ms)
  closes a segment; segments longer than `STT_MAX_SEGMENT_SECONDS` (9 s) are split at the lowest-energy window of
  their second half.
* **Finals**: every closed segment is transcribed immediately (concurrently) and the answer is the segment texts
  joined in order, so end-of-turn waits only for the last short segment. One retry after 0.25 s; on failure the
  client sees notice `stt_degraded`, later `stt_recovered`; the turn waits up to 25 s, then retries in the
  background (2, 4, 8, 15, 30 s) and offers typed answers (`stt_unavailable`).
* **Partials** (`stt_partial_*`): while the candidate speaks, the open segment is transcribed when no partial is in
  flight, no final is waiting, at least 1200 ms of new audio exists and the interval has passed
  (`STT_PARTIAL_INTERVAL_MS`: 1500 ms default, 2000 ms in `.env.example`; the interval grows x1.5 after a skip and
  x1.25 when partials are slow, up to 4x the base, and relaxes back). `transcript.partial` = finals so far + the partial.
* **End of turn** (`endpointing.py`): required silence 850 ms (`ENDPOINT_SILENCE_MS`); 600 ms when the text ends a
  sentence and has >= 6 words; 1700 ms when it ends with a conjunction or filler ("and", "so", "because", "um",
  "like", ...) or has fewer than 3 words; 25 s (`THINK_PAUSE_MAX_SECONDS`) after "let me think", "give me a
  moment", "hold on" and similar; 240 s maximum utterance. The "Done answering" button ends the turn at once. At
  silence onset the engine gets the partial text once to run a quick assessment speculatively.
* **Whisper client** (`app/ai/clients/stt.py`): multipart
  `POST /audio/transcriptions` with `response_format=json` by default (`verbose_json` adds ~0.65 s per request),
  language `en`, temperature 0 and a prompt ("Technical terms: ..." from the job, round and profile, the current
  question and the end of the answer; the deployed server ignores prompt and temperature). Measured on the deployed
  server: each Whisper replica transcribes **one request at a time** (a cancelled request still runs to the end),
  roughly 0.65 s + 0.18 s per second of audio with `json`. Scheduling across `WHISPER_BASE_URL` +
  `WHISPER_EXTRA_BASE_URLS`: a final goes to an idle replica (the one with the best recent speed, an EWMA of
  observed vs expected latency, so a replica slowed by another tenant is avoided) or waits in one FIFO queue (up to
  `WHISPER_TIMEOUT_SECONDS`, 30 s), never behind another request on a busy replica; a final running much longer
  than expected (`max(1.2 s, 1.6 x expected)`) is hedged to another idle replica and the first answer wins; an
  unavailable replica gets one failover. Partials never queue and never take the last idle replica: they raise
  `PartialSkipped` (dropped) when finals are waiting, the partial budget (1) is used or no spare replica is idle.
  Silence hallucinations ("Thank you.") are removed when the server reports likely silence, the clip is under
  0.6 s or the audio is below -55 dBFS. Finals are recorded in `model_calls`; partials are only measured.
* Measured: 1.8 s for a 5 s clip, 4.8 s for 17 s, 14.8 s for 33 s (RTF ~0.3); 2.8 s for 5 s (RTF 0.56) and
  outliers of 11.6 s under load. A first live run with one replica averaged ~10 s from end of turn to first audio,
  dominated by `stt_wait_ms` (3.6-11.3 s) because partials and finals competed for the single replica. After the
  second replica, the 2 s partial interval and the scheduling above: end of turn to first audio p50 2.1 s, p95
  2.6 s; turn word error rate 0.059 (was 0.211); technical terms 12/12 (was 6/12).
* Because the server ignores the prompt, vocabulary biasing has no effect; the glossary correction
  (`app/realtime/glossary.py`, rapidfuzz) fixes misheard technical terms against the session vocabulary instead.

## Text-to-speech and visemes

* **Acknowledgement**: when an answer had at least 2.5 s of voiced speech (not typed, not a barge-in, not a repeat
  or clarification request, no coding), a pre-synthesised acknowledgement plays immediately at end of turn
  ("Okay.", "Mm-hmm.", "I see.", "Got it.", "Right.", "Thanks, that helps."; random, avoiding the previous one)
  while STT and the model run. It masks latency without faking content and is not reported to the engine.
* **Chunker** (`chunker.py`): sentence ends (`.?!` with abbreviation, initial, decimal and ellipsis protection).
  The first chunk may break at a comma after >= 24 characters or hard-break at ~90 characters to cut first-audio
  latency; later chunks are up to ~220 characters; very short sentences are merged.
* **Pipeline** (`speech.py`): up to `TTS_MAX_INFLIGHT_PER_SESSION` (2) chunks synthesise ahead per utterance; each
  chunk has a 15 s budget and 2 attempts; chunks are sent strictly in order as a `tts.chunk` JSON message (text,
  duration, visemes) immediately followed by a binary `0x02` frame with the same `chunk_id`. Metrics:
  `interview_time_to_first_audio_seconds` (utterance start -> first chunk sent) and
  `interview_turn_latency_seconds` (end of turn -> first engine audio; the acknowledgement is excluded).
* **Provider** (`app/ai/clients/tts.py`, `TTS_PROVIDER`): `kokoro` (default; `POST /v1/tts`, speed clamped to
  0.7-1.3, text split at sentence boundaries into pieces of at most 1000 characters), `openai_compatible`
  (`/v1/audio/speech`, visemes estimated locally), `browser` (server TTS disabled: every utterance goes to the
  browser fallback) and `mock`. Failures raise `TtsUnavailable`.
* **Kokoro service** (`services/tts`, compose service `tts`): Kokoro-82M fp32 ONNX via kokoro-onnx + ONNX Runtime on
  CPU (`TTS_WORKERS` 2 sessions x `TTS_THREADS` threads; ORT spinning on: a 6-word sentence 279 ms vs 581 ms
  without). Text normalisation for technical speech (abbreviations, units, versions, big-O, a pronunciation table
  for terms such as PostgreSQL and k8s), an in-memory LRU cache (512 entries / 64 MiB; a cache hit takes ~2.4 ms),
  a bounded queue (32; 503 `busy` beyond), trimming to the audible region with natural pauses after sentences
  (220 ms) and clauses (120 ms). `/health` is 503 until the model is loaded and warmed.
* **Visemes**: 15 Oculus visemes (`sil PP FF TH DD kk CH SS nn RR aa E I O U`) as `{t, v, w}` cues. The Docker
  build adds Kokoro's per-phoneme `duration` output to the ONNX model (`services/tts/scripts/add_duration_output.py`; audio is
  bit-identical), so cues use the model's own phoneme timings (`X-TTS-Viseme-Timing: model`). Without that variant
  the service estimates durations (calibrated table, pause anchoring; ~64 ms mean onset error). The mouth closes
  before and after speech and in pauses of 100 ms or more; weights follow stress and loudness. If a response has
  no visemes, the API estimates them from phonemes or text (`app/ai/visemes.py`). The browser maps them to the
  avatar's morph targets ([AVATAR_SYSTEM.md](AVATAR_SYSTEM.md#lip-sync)).
* **Failure**: after the retry the rest of the utterance is sent as `tts.fallback` messages (the browser speaks them
  with `speechSynthesis` or shows text); the runtime marks TTS degraded (notice `tts_degraded`), starts later
  utterances in fallback, probes `/health` every 10 s and announces `tts_recovered`.
* Measured (container, cache miss, `cpus: 8`): p50 506 ms for 6 words, 814 ms for 15 words, 1.86 s for 35 words
  (RTF 0.15-0.28); first chunk of a live utterance ~0.38 s uncached, 5-15 ms cached. With 2 workers,
  `TTS_THREADS=4` keeps two concurrent syntheses at ~1.0 s each where 8 threads throttle to ~2.4 s; the compose
  fallback of 8 threads x 2 workers oversubscribes `TTS_CPUS=8` (the service logs `tts.cpu_oversubscribed`), which
  is why `.env.example` sets 4.

## Vision

Camera and screen frames are analysed by the 8B VL model (`frame_analysis`, `screen_analysis`; temperature 0,
320 tokens, 60 s, 2 attempts) into `FrameObservation` / `ScreenObservation` objects with objective fields only
(person count, face visible, camera obstructed, phone visible, ...). Prompts forbid identifying anyone or describing
appearance, emotion or intent. Results only ever become flags for human review. Details:
[INTEGRITY_MONITORING.md](INTEGRITY_MONITORING.md#vision-analysis).

## Fallbacks

| Failure | Behaviour |
|---|---|
| main LLM unavailable (circuit open) | turn-critical tasks run on the 8B; deep tasks fail and their jobs retry with backoff |
| turn decision fails before any speech | one retry with a trimmed prompt, then a fallback utterance (acknowledgement + cached bank question or template) |
| turn decision metadata slow or truncated | bounded wait, field-by-field `json_repair` recovery, missing fields synthesised |
| structured output invalid | repair, then re-ask with the validation errors (up to 3 attempts), then the caller's fallback |
| interview plan / opening fails | deterministic plan from the round configuration; template introduction |
| answer evaluation fails | neutral evaluation (`is_fallback`, confidence 0.2), no evidence, flagged for human review |
| report narratives fail | deterministic narratives built from the scores |
| profile part fails on the last attempt | that section keeps the deterministic quick parse, profile status `partial` (other parts stay the model's) |
| reranker down | vector order kept; job match uses calibrated cosine similarity |
| OCR down | `resume.process` fails and retries (outages propagate) |
| Whisper partial cannot run | partial skipped (`PartialSkipped`), finals unaffected |
| Whisper final fails | retry, replica failover, `stt_degraded` / `stt_unavailable` notices, background retries, typed answers offered |
| TTS fails | `tts.fallback` -> browser speech synthesis or on-screen text; degraded mode until the health probe succeeds |
| avatar cannot render | 2D portrait or no avatar; audio and subtitles continue |
| vision model fails | frame job retries; frames older than 180 s are skipped and deleted |
| sandbox unavailable | candidate sees "The code runner is busy, please try again"; run recorded with `platform_fault` |

## Measured latencies (summary)

| Step | Measurement |
|---|---|
| Main LLM time to first token | 0.1-0.5 s |
| First spoken text of a turn (first `SpeechDelta`) | 559-699 ms in-process, 718-730 ms through the WebSocket |
| Whisper final | ~1.8 s per 5 s of speech (RTF 0.3-0.56 under load) |
| Kokoro first chunk | ~0.38 s uncached, 5-15 ms cached |
| End of turn -> first interviewer audio | p50 2.1 s, p95 2.6 s (live voice loop, two Whisper replicas, shared GPU) |
| Quick assessment at silence onset | 4 s budget |
| Answer evaluation (`answer.evaluate`) | background, up to 90 s |
| Code observation (coding comments) | 2.2-7 s, median ~4 s |
| Coding evaluation | 6-36 s |
| Resume quick profile (deterministic) | 1-6 ms to build; visible 0.1-0.5 s after upload for text files |
| Profile generation | 15-29 s on the loaded shared GPU (old design 62-96 s in the same minutes); 21 s for a dense 3-page resume |
| Report generation | ~56 s (narratives p50 43 s, p95 54 s) |
| Frame analysis (8B VL) | ~10.4 s per frame |

Alert thresholds: turn latency p95 > 3 s, time to first audio p95 > 2 s, STT p95 > 2 s, TTS p95 > 1.5 s
([DEPLOYMENT.md](DEPLOYMENT.md#alerts)).
