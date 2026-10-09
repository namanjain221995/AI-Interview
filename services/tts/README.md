# Interview TTS service

Local text-to-speech for the interviewer voice: **Kokoro-82M** (fp32 ONNX) on CPU
through `kokoro-onnx` + ONNX Runtime, with an Oculus viseme timeline for the
avatar's lip-sync. One process, `127.0.0.1:8210`, compose service `tts`.

```
POST /v1/tts            contract endpoint used by the API's KokoroTtsProvider
POST /v1/audio/speech   OpenAI-compatible (wav | pcm)
GET  /v1/voices         54 Kokoro voices with language and gender
GET  /health            200 when loaded and warm, 503 while loading or on error
GET  /metrics           Prometheus (interview_tts_*)
```

## Wire contract

`POST /v1/tts`

```json
{"text": "Thanks! Let's begin.", "voice": "af_heart", "speed": 1.0, "sample_rate": 24000}
```

| Field | Rule |
|---|---|
| `text` | 1-1000 characters, otherwise 422 |
| `voice` | Kokoro voice id; `null` uses `TTS_DEFAULT_VOICE`; a unique bare name (`alloy`) resolves to `af_alloy`; anything else is 422 `unknown_voice` |
| `speed` | 0.7-1.3 (default 1.0), otherwise 422 |
| `sample_rate` | 8000, 12000, 16000, 24000 (native), 32000 or 48000, otherwise 422 |

Response (200), exactly these keys:

```json
{
  "audio_b64": "<base64 of PCM16 little-endian mono>",
  "sample_rate": 24000,
  "duration_ms": 1675,
  "visemes": [{"t": 0, "v": "sil", "w": 1.0}, {"t": 20, "v": "TH", "w": 0.8}, {"t": 130, "v": "aa", "w": 1.0}],
  "phonemes": "θˈæŋks! lˈɛts bɪɡˈɪn.",
  "voice": "af_heart",
  "cached": false
}
```

* `duration_ms` equals the PCM sample count at `sample_rate`.
* `visemes`: `t` is in ms from the start of the audio and strictly increasing,
  `v` is one of `sil PP FF TH DD kk CH SS nn RR aa E I O U`, `w` is in 0-1. The
  first cue is `sil` at 0, the first open shape starts where sound starts, and
  the last cue is `sil` where sound ends.
* Text with nothing speakable (emoji, markup only) returns 200 with empty audio,
  `duration_ms: 0` and a single `sil` cue.
* Response headers: `X-TTS-Cache: hit|miss`, `X-TTS-Synthesis-Ms`,
  `X-TTS-Duration-Ms`, `X-TTS-Audible-Ms: start-end`, `X-TTS-Viseme-Timing:
  model|estimated`, `X-Sample-Rate`, `X-Request-ID` (echoed when sent).

`POST /v1/audio/speech` takes `{"model", "input", "voice", "response_format":
"wav"|"pcm", "speed"}` (speed 0.5-2.0, unknown fields such as `instructions` are
ignored) and returns `audio/wav` or `audio/pcm` (raw PCM16 LE, 24 kHz mono).

Errors use the platform envelope `{"error": {"code", "message", "request_id",
"details"?}}`; the submitted text is never echoed back:

| Status | `code` | When |
|---|---|---|
| 401 | `unauthorized` | `TTS_API_KEY` is set and the bearer token is missing or wrong |
| 413 | `payload_too_large` | body over `TTS_MAX_BODY_BYTES` (64 KiB) |
| 422 | `validation_error`, `unknown_voice` | invalid input |
| 500 | `synthesis_failed` | inference error (details only in the service log) |
| 503 | `not_ready` (Retry-After: 2), `busy` (Retry-After: 1) | still loading or failed to load; wait queue full |
| 504 | `timeout` | wait plus synthesis exceeded `TTS_REQUEST_TIMEOUT_SECONDS` |

`GET /health` returns `{"status": "ok"|"loading"|"error", "model", "warm",
"last_latency_ms", ...}` plus `viseme_timing`, `workers`, `threads`,
`queue_depth`, `inflight`, `cache_entries`, `uptime_s` and `detail`.

## How it works

1. **Startup.** The model loads into one ONNX Runtime session per worker
   (`TTS_WORKERS`, default 2; each with `TTS_THREADS` intra-op threads, one
   inter-op thread, `ORT_ENABLE_ALL`). Each session then runs a warm-up
   synthesis. `/health` reports `ok` only after that, and requests get 503
   until then.
2. **Text normalisation** (`tts_service/text.py`). All voices get markdown,
   HTML, emoji, control and zero-width characters stripped. English voices also
   get a small rule set, each rule tested: abbreviations (e.g., i.e., etc., vs.,
   Dr.), dotted acronyms (U.S.), money ($1.5M), percentages, clock times,
   decimals (espeak reads "1.5" as "one. five"), versions, units (ms, GB, Mbps),
   decades, number ranges, big-O and operators (O(n^2), <=, ->), slashes
   (CI/CD, A/B), URLs and emails, and a table of 24 tech terms whose espeak-ng
   pronunciation was checked to be wrong (PostgreSQL, k8s, C#, .NET, Node.js,
   regex, enum, stdin, idempotent...).
3. **Cache.** An in-memory LRU keyed by (voice, speed, sample rate, normalised
   text), bounded by entries (512) and bytes (64 MiB). Clips over 4 MiB are not
   cached. Identical requests that arrive while one is already being
   synthesised share that synthesis.
4. **Scheduling.** At most `TTS_WORKERS` syntheses run at once on a fixed thread
   pool. Up to `TTS_MAX_QUEUE` (32) more wait in FIFO order; beyond that the
   service answers 503 `busy`. `TTS_REQUEST_TIMEOUT_SECONDS` (15 s) bounds
   wait plus work. A queued job whose callers all gave up is dropped. A running
   job finishes and its result is cached, so a retry is instant.
5. **Synthesis.** espeak-ng IPA comes from kokoro-onnx's own tokenizer, and
   texts over 510 phonemes are split at sentence and clause boundaries. Each
   pass is trimmed to its audible region by frame RMS (5 ms frames, -45 dB
   relative to the loudest frame). 20 ms of silence is kept before speech, so
   time to first sound stays low. The silence kept after speech depends on how
   the text ends: 220 ms after `.!?`, 120 ms after `,;:`, 60 ms otherwise,
   divided by speed. Consecutive sentence clips therefore join with natural
   pauses.
6. **Visemes** (`tts_service/visemes.py`). The image runs a model variant that
   also outputs Kokoro's predicted per-phoneme durations
   (`scripts/add_duration_output.py` adds one Identity output; the audio is
   bit-identical). With that variant the timeline uses the model's own phoneme
   timings. On the stock model (local runs without the variant) durations are
   estimated instead:
   * Every token gets a duration from a table calibrated against the model's own
     durations, and the phone before a pause is lengthened.
   * The estimate is fitted to the audible region.
   * Punctuation is anchored to the silent gaps found in the waveform by a
     monotonic matcher.
   * Measured against the model's timings, the mean onset error is about 64 ms.

   In both modes:
   * IPA maps to Oculus visemes: stress marks and word gaps fold into the next
     phone, length marks into the previous one, `tʃ`/`dʒ` become one `CH`, and
     `h` takes the shape of the following vowel.
   * The mouth closes (`sil`) before and after the audible region and in pauses
     of 100 ms or more.
   * Weights scale with stress, reduced vowels, diphthong glides and local
     loudness.

## Configuration (`TTS_*` environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `TTS_HOST` / `TTS_PORT` | 127.0.0.1 (image: 0.0.0.0) / 8210 | bind address |
| `TTS_THREADS` | 8 | intra-op threads per synthesis |
| `TTS_WORKERS` | 2 | concurrent syntheses (one ONNX session each) |
| `TTS_MAX_QUEUE` | 32 | requests allowed to wait for a worker |
| `TTS_REQUEST_TIMEOUT_SECONDS` | 15 | wait + synthesis budget per request |
| `TTS_ALLOW_SPINNING` | true | ORT spin-waits between kernels (2x faster on short text, see below) |
| `TTS_DEFAULT_VOICE` | af_heart | voice when the request has none |
| `TTS_MODEL_PATH` / `TTS_VOICES_PATH` | models/… (image: /app/models/…) | model files |
| `TTS_MODEL_NAME` | kokoro-v1.0 | reported in /health |
| `TTS_CACHE_MAX_ENTRIES` / `TTS_CACHE_MAX_BYTES` / `TTS_CACHE_MAX_ITEM_BYTES` | 512 / 64 MiB / 4 MiB | LRU bounds |
| `TTS_WARMUP_TEXT` | a greeting | warm-up sentence |
| `TTS_API_KEY` | unset | when set, `/v1/*` requires `Authorization: Bearer <key>` |
| `TTS_MAX_BODY_BYTES` | 65536 | request body limit |
| `TTS_ENABLE_DOCS` | true | `/docs` and `/openapi.json` |
| `TTS_LOG_LEVEL` / `TTS_LOG_JSON` | INFO / true | structured logs on stdout |

If the container's CPU quota is smaller than `TTS_WORKERS x TTS_THREADS`, the
service logs `tts.cpu_oversubscribed` at startup.

## Metrics

All metric names start with `interview_tts_`:

* `synthesis_seconds` (histogram, cache misses)
* `real_time_factor` (histogram)
* `audio_seconds_total`
* `characters_total{source=synthesized|cache}`
* `cache_hits_total`, `cache_misses_total`, `cache_entries`, `cache_bytes`
* `queue_depth`, `inflight`
* `errors_total{reason=queue_full|timeout|synthesis_failed|not_ready|unknown_voice|invalid_input|unauthorized}`
* `requests_total{endpoint,status}`, `request_seconds{endpoint}`
* `ready`, `model_load_seconds`
* plus the standard `process_*` and `python_*` metrics.

## Running and testing

```bash
cd services/tts
uv sync                                   # .venv with runtime + dev dependencies
uv run python -m tts_service              # serves on 127.0.0.1:8210
uv run pytest                             # unit + API + real-model tests (model tests skip without models/)
uv run pytest -m live                     # against a running service (TTS_LIVE_URL, default :8210)
uv run ruff check . && uv run mypy tts_service scripts
```

Docker (from the repository root):

```bash
docker compose up -d --build tts
curl -s localhost:8210/health
curl -s localhost:8210/v1/tts -H 'content-type: application/json' -d '{"text":"Hello there."}' | jq '.duration_ms, .visemes[:3]'
curl -s localhost:8210/v1/audio/speech -H 'content-type: application/json' \
  -d '{"model":"kokoro","input":"Hello there.","response_format":"wav"}' -o hello.wav
```

How the image is built:

* `python:3.12-slim`, pinned by digest.
* Dependencies are installed with `pip --require-hashes`, from `requirements.txt`
  exported from `uv.lock`.
* Model files are copied from `models/` when present and downloaded from the
  kokoro-onnx release otherwise. Either way the SHA-256 is verified
  (`scripts/fetch_models.py`), and a mismatch fails the build.
* The duration variant is generated in a builder stage that has `onnx`. The
  runtime image needs only onnxruntime.
* espeak-ng is the shared library and data bundled in the `espeakng-loader`
  wheel, checked to load on linux/arm64 during the build. No system packages are
  needed.
* The service runs as uid 10001, with root-owned, read-only application files.

After changing dependencies, refresh the hashed exports:

```bash
uv lock && uv export --format requirements-txt --no-dev --no-emit-project --frozen --no-header -o requirements.txt
```

## Measured performance (DGX Spark, arm64, shared host)

These numbers come from the container, end-to-end over HTTP. Each figure is the
p50 of 3 cache-miss requests, with af_heart at speed 1.0, under the compose
limit `cpus: 8`. The host was shared with a production AI stack (load average
9-12 on 20 cores), so expect variance.

| Text | Audio | p50 latency | RTF |
|---|---|---|---|
| 6 words | 1.78 s | 506 ms | 0.28 |
| 15 words | 4.11 s | 814 ms | 0.20 |
| 35 words | 12.6 s | 1.86 s | 0.15 |
| cache hit (15 words) | | 2.4 ms | |

On the same host, in-process, at a quieter moment, the 6-word sentence took
279 ms. With `TTS_ALLOW_SPINNING=false` it took 581 ms: without spinning, ORT
threads sleep and wake between Kokoro's many small kernels.

With `cpus: 8` and two workers, `TTS_THREADS=4` keeps two concurrent syntheses
inside the CPU quota:

| Requests | `TTS_THREADS=8` p50 | `TTS_THREADS=4` p50 |
|---|---|---|
| One at a time | 656-934 ms | 718-738 ms |
| Two at once | 2.3-2.5 s (throttled) | 0.96-1.06 s |

Startup, including the load and the warm-up of both sessions, takes about 3 s.
RSS is about 1.5 GiB. Idle CPU is about 0.3% of one core.

## Notes

* Fairness: the service only turns text into audio. It never analyses audio
  or text for emotion, traits or identity.
* Privacy: logs carry character counts, durations and voices, never the text.
* Licensing: Kokoro-82M weights are Apache-2.0 and kokoro-onnx is MIT.
  `phonemizer` and espeak-ng (bundled by `espeakng-loader`) are GPL-3.0. Running
  the image as an internal network service is fine. Shipping the image to third
  parties brings GPL obligations for those components.
