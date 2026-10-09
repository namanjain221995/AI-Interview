# Wave C brief: latency + throughput (voice loop, resume pipeline, TTS)

Read `wave-c-common.md` first.

YOU OWN: `apps/api/app/realtime/**` (runtime, stt scheduling, audio/segmenter, speech streamer), `apps/api/app/ai/clients/stt.py`,
`apps/api/app/ai/clients/tts.py`, `apps/api/app/services/resume/**`, `services/tts/**` (container rebuild allowed:
`docker compose up -d --build tts` — keep it healthy), and the matching tests (`tests/unit/realtime/**`,
`tests/integration/realtime/**`, `tests/unit/resume/**`, `tests/integration/resume/**`, `tests/unit/ai/test_stt*`, `tests/unit/ai/test_tts*`).
Also own `apps/api/app/api/routers/{portal,candidate_access}.py` and `apps/api/app/schemas/api/portal.py`, and handle these realtime leftovers: connections made before `control.start` (system-check probe, reloads before Begin)
must not count as reconnects/reloads; accept optional `avatar_mode` / `avatar_fps` in `SystemCheckRequest`
(`app/schemas/api/portal.py`).

MEASURED PROBLEM (live E2E 2026-10-07, realtime.turn_latency log lines): end of candidate turn -> first interviewer audio
averaged ~10 s. Breakdown per turn: `stt_wait_ms` 3.6-11.3 s (Whisper: each replica processes ONE request at a time,
RTF 0.3-0.5 under shared load; 10 partial requests averaging 3.5 s competed with 28 segment finals averaging 7 s), then
engine first event -> first audio 1.1-3.5 s (LLM TTFT 1.6 s avg under load + TTS ~1.7 s per chunk). A second Whisper
replica is now configured (WHISPER_EXTRA_BASE_URLS) and partial interval raised to 2 s — verify they are used well.

Targets on this shared host: p50 end-of-turn -> first audio <= 2.5 s, p95 <= 4.5 s; resume profile generation <= 100 s.
Ideas to evaluate with measurements (keep what helps): shorter max segment and earlier segment closing on short pauses;
send finals to the least-busy replica and never queue a final behind a partial (pin partials to one replica or cancel
in-flight partials when a final is due); skip partials entirely while finals are pending; transcribe the tail segment
speculatively at silence onset; first TTS chunk at the first clause (~20-40 chars) and cache common openers; TTS thread /
worker tuning under the CPU cap; resume profile: split extraction into parallel calls and/or run analysis concurrently
(keep output quality — compare profiles before/after on samples/resumes).

Method: build a latency harness (live, opt-in) that replays the WAV clips in tests/e2e/fixtures/audio through the real
runtime (in-process ASGI WebSocket like tests/integration/realtime) with real Whisper + TTS + LLM, and prints the
per-turn breakdown; run it before and after each change. Report the numbers.

UPDATE after enabling the second Whisper replica + 2 s partial interval (E2E run #2, 5 voiced turns of 4-28 s):
stt_wait_ms = 5816, 2781, 2316, 2546, 3159; engine first event -> first audio = 1818, 3333, 3769, 2676, 21 ms;
end-of-turn -> first audio = 7.7, 6.1, 6.1, 5.2, 3.3 s. Both legs still need work: STT tail wait and LLM-first-token +
first TTS chunk (the engine alone measured first SpeechDelta 0.56-0.70 s when the model was quiet; under shared load TTFT
was ~1.6 s). Note the acknowledgement backchannel already plays 1-5 ms after end of turn.
