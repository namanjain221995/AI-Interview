# Realtime voice pipeline design

Written for: engineers implementing the realtime runtime (`app/realtime`), the candidate portal API, and the browser
client (`apps/web/src/features/realtime`, `features/interview`). Protocol: `app/schemas/realtime.py` (mirrored in
`apps/web/src/lib/realtime/protocol.ts`). Engine seam: `app/services/interview/contracts.py`.

Status: design intent. The implemented voice loop (segmentation, end-of-turn rules, STT scheduling, TTS, measured
latency) is described in [`AI_PIPELINE.md`](../../AI_PIPELINE.md) and the turn sequence in
[`ARCHITECTURE.md`](../../ARCHITECTURE.md#one-candidate-turn).

## 1. Why WebSocket (and not WebRTC/Pipecat) for v1

Speech recognition is a batch HTTP Whisper endpoint, TTS is our own pluggable provider, and the interview logic is a
custom adaptive engine. A single multiplexed WebSocket (binary PCM + JSON events) is simpler to secure, proxy (Caddy,
tunnels), resume, test and observe than ICE/TURN negotiation, and the browser still provides echo cancellation, noise
suppression and AGC through `getUserMedia` constraints. The transport is isolated in `app/realtime/transport.py` so a
WebRTC transport (aiortc/LiveKit) can be added without touching the pipeline. Pipecat was evaluated: it would not remove
the custom engine, STT scheduling or barge-in policy work, so it adds a dependency without materially simplifying.

## 2. Runtime structure (per live session, inside one API process)

```
WebSocket handler ── SessionRuntime ── AudioIngest (frames -> ring buffer -> VAD -> segments)
                                    ├─ SttScheduler (segment finals in order, throttled partials)
                                    ├─ Endpointer (adaptive end-of-turn)
                                    ├─ TurnController (FSM: ai_speaking/listening/candidate_speaking/processing/coding/paused)
                                    ├─ SpeechStreamer (sentence chunker -> TTS pipeline -> tts.chunk + binary frames)
                                    ├─ Outbox (seq numbers, Redis ring buffer, replay on resume)
                                    ├─ CodingBridge / IntegrityBridge / BusListener (session:{id}:bus)
                                    └─ Timers (heartbeat watchdog, silence prompts, time warnings, max duration)
```

The runtime survives WebSocket reconnects (registry keyed by session id) and owns a Redis lock
`session:{id}:lock` (owner = instance id, PX 30 s, refreshed every 10 s). A second connection for the same session on
another instance is refused with `session_active_elsewhere` unless the lock is stale.

## 3. Audio in

* Browser: AudioWorklet captures mono at the device rate, downsamples to 16 kHz PCM16, 20 ms frames batched per 100 ms,
  binary frame type 0x01 with a sequence number. `getUserMedia` constraints: echoCancellation, noiseSuppression,
  autoGainControl true.
* Server VAD: Silero ONNX (512-sample windows = 32 ms) with an energy-based fallback. Speech start = probability >=
  `vad_speech_threshold` for 3 consecutive windows; 300 ms pre-roll kept.
* Segmentation: silence >= `segment_pause_ms` (350 ms) closes a segment (segments longer than `stt_max_segment_seconds` are split at the lowest-energy point), which is transcribed once ("final" for that
  segment) in order. The answer transcript is the ordered join of segment finals, so end-of-turn only waits for the last
  short segment.
* Partials: while speech continues, every `stt_partial_interval_ms` (1.5 s; Whisper here runs at RTF ~0.3) with >= `stt_partial_min_audio_ms` of new audio and no partial in flight,
  transcribe the open segment (low-priority STT budget; skipped when saturated). `transcript.partial` = finals so far +
  partial.
* Whisper prompt: the current question focus + vocabulary from the candidate profile/job (technologies) + last ~200
  characters of the answer so far.

## 4. End of turn

Base silence `endpoint_silence_ms` (850 ms). Shorter (`endpoint_silence_short_ms`, 600 ms) when the text so far ends in a
complete sentence and has >= 6 words. Longer (`endpoint_silence_long_ms`, 1.7 s) when it ends with a conjunction or filler
("and", "so", "but", "because", "um", "uh", "like") or is under 3 words. "Let me think" / "give me a moment" extends the
wait up to `think_pause_max_seconds`. `control.end_turn` (button) ends immediately; `max_utterance_seconds` forces it.
While silence is accumulating, the runtime calls `engine.on_partial_transcript(text, silence_started=True)` so the
engine can prepare (quick assessment, retrieval) without committing anything.

## 5. Interviewer speech out

1. At end of turn (answers with >= 2.5 s of speech), optionally play a **pre-synthesized acknowledgement** from a varied
   cache ("Okay.", "Mm-hmm.", "I see.", "Got it.", "Right.", "Thanks, that helps.") — never the same one twice in a row,
   skipped for short answers, clarification requests and repeats. Avatar: ACKNOWLEDGING then THINKING. This masks model
   latency without faking content.
2. `handle_candidate_turn` yields `UtteranceBegin` -> `SpeechDelta`* -> `UtteranceMeta`. Deltas are forwarded as
   `ai.text.delta` (subtitles when policy allows) and fed to the sentence chunker.
3. Chunker: emit at sentence ends (`.?!`, with abbreviation/decimal protection) — the first chunk may break at a comma
   after >= 24 characters or hard-break at ~90 characters to cut first-audio latency; later chunks up to ~220 characters.
4. TTS pipeline: up to `tts_max_inflight_per_session` (2) chunks synthesizing ahead; results sent strictly in order as
   `tts.chunk` (text, duration, visemes) immediately followed by the binary 0x02 frame with the same `chunk_id`.
5. Metrics: `interview_time_to_first_audio_seconds` (utterance begin -> first chunk sent),
   `interview_turn_latency_seconds` (end of turn -> first chunk sent).
6. TTS failure: retry once; then `tts.fallback` with the remaining text (browser speechSynthesis or text) and a degraded
   flag with periodic health probes so later utterances skip straight to fallback until TTS recovers.

## 6. Playback tracking and turn ownership

The client reports `playback.started/progress/finished/interrupted` (played_ms). An utterance is delivered on
`playback.finished` (or estimated end + 1.5 s grace) -> `engine.mark_utterance_delivered(spoken_text)` -> LISTENING.
No answer within ~25 s -> `engine.handle_timer(silence_timeout)` (gentle "Take your time — would you like me to repeat the
question?"). The interview clock pauses during disconnects only.

## 7. Barge-in

While AI_SPEAKING, server VAD probability >= `vad_barge_in_threshold` (0.8) sustained for `barge_in_min_speech_ms`
(280 ms) triggers barge-in: close the engine iterator (`aclose`, which cancels the LLM HTTP stream), cancel pending TTS,
send `barge_in`, switch to CANDIDATE_SPEAKING. The browser stops all scheduled audio within one audio quantum, the avatar
goes INTERRUPTED -> LISTENING, and the client reports `played_ms`; the runtime computes the spoken portion from chunk
durations and calls `engine.handle_interruption`. After the candidate's turn: if the transcript is empty or a backchannel
("mm-hmm", "yeah", "okay", "sure", "right", <= 3 words), call `engine.handle_backchannel_resume` (continue from the next
unplayed sentence); otherwise `handle_candidate_turn(turn.interrupted_ai=True)`. The browser additionally ducks playback
volume instantly on local voice activity (`vad.local`) so overlap feels responsive even before server confirmation;
echo-cancelled audio plus the stricter threshold prevents the interviewer's own voice from triggering barge-in.

## 8. Reconnection and failures

* Disconnect: runtime enters PAUSED, cancels the current utterance (it will be re-spoken), pauses the interview clock,
  records a `disconnect` integrity event, keeps running for `ws_disconnect_grace_seconds`. Reconnect with a fresh ticket
  and `hello.last_seq`: replay buffered JSON messages after that seq, send `session.state`, then `engine.resume(reconnect=True)`
  ("Sorry, we got disconnected for a moment. As I was asking, ..."). After the grace period the session stays PAUSED and
  resumable until the invite expires or max duration is reached.
* Browser refresh: same path (page reload detected client-side -> `page_reload` event), honouring
  `allow_page_reload_resume` and `max_reconnects` from the policy.
* Backend restart: state reloads from Redis/Postgres on reconnect; competency state recomputes from evidence.
* STT failure: retry once per segment; persistent failure -> `notice` (stt_degraded) and the UI offers typed answers;
  the turn is never silently dropped (audio is kept until transcribed).
* LLM failure: engine fallback question (see ENGINE_DESIGN.md §4). Redis restart: AOF persistence; runtime re-acquires
  its lock and rewrites the hot state.
* Avatar failure: client switches to 2D/none and reports `client.error`; audio + subtitles continue.

## 9. Portal REST (candidate-facing)

Implemented in `app/api/routers/portal.py` and `candidate_access.py` per CONTRACTS §4: invite access + verification
(email match, optional access code, rate limited, lockout after repeated failures), consent with the disclosure hash
(`DisclosureBuilder` from the integrity module), resume upload (`ResumeService.accept_upload`), round selection (only
when allowed), system-check record, session create/resume (`SessionService.create_or_resume`), WebSocket ticket, frame
upload (`IntegrityService.submit_frame`), integrity event batches, recording chunks (only when policy + consent enable
video recording), candidate report (released only).
