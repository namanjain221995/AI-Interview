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
("and", "so", "but", "because", "um", "uh", "like") or is under 3 words. A short fragment that stops mid-sentence (at most
5 words ending with a continuation word, or "so for that") waits `endpoint_fragment_hold_ms` (4 s) in both stages, and
the engine answers it with a soft nudge, never a new question. "Let me think" / "give me a moment" extends the
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

## 10. Intake activities (`round_type: intake`)

Contract: [`INTAKE_INTERVIEW.md`](INTAKE_INTERVIEW.md) section 7 (deviations in its section 18). Implemented in
`app/realtime/runtime.py` (activity block, `RuntimeActivity`) on top of the existing transport; no new endpoint.

* **Messages.** Server `activity.start` (armed, title + phase plan, never content), `activity.phase`
  (`running` / `ended`, authoritative `remaining_ms`, `content` only in the message that starts a phase where it may
  be visible, `hide_content` to remove it now), `activity.end`; client `activity.finish {activity_id, phase}`
  (rate 2/s per connection). All three server messages are sequenced but **non-replayable**
  (`outbox.NON_REPLAYABLE_TYPES`): never in the in-memory or Redis outbox. A delayed finish button (reading after
  5 s, relay read after 10 s) is announced by a second `activity.phase {running, can_finish: true}` without content.
  `session.state.intake` carries the stage list (candidate-safe titles), `answer_mode`, `soft_limit_s` and a
  content-free activity snapshot with the remaining time.
* **Lifecycle.** The engine yields the instruction utterance then `ActivityStarted` (`start_after_utterance_id`);
  the runtime posts `activity.start` and starts the phase when that utterance is delivered (`playback.finished` or
  estimated end): deadline on the interview clock, `activity.phase`, a capture for capture phases, and the engine
  event `phase_started` (enqueued with turn priority so it precedes a capture that ends at once). A non-capture
  phase (relay read, card prepare) ends at its deadline or on `activity.finish`: `activity.phase {ended}` first
  (`hide_content: true` for the Drill B passage, which is also dropped from memory), then the engine event
  (`phase_deadline` / `finish_requested`) whose prompt utterance arms the next phase (`ActivityPhaseChanged`).
* **Capture mode.** One `TurnCapture` spans the whole capture phase: speech bursts never open new captures,
  endpointing is replaced by the activity rules, and there are no acknowledgements, commit wait, "didn't catch that"
  prompts, silence prompts or barge-in (the interviewer is silent). VAD `SpeechEnded -> SpeechStarted` gaps
  >= 300 ms are recorded as pauses in session ms (`started_session_ms + (t_ms - capture.start_ms)`). The phase ends
  on the finish button (`control.end_turn` counts as finish), the silence rule (`ACTIVITY_SILENCE`: reading 4 s once
  70 % of the passage's words were heard or 8 s after any speech; relay 6 s after 15 words; topic answer 10 s after
  30 words; never before the candidate spoke), the deadline (grace up to 3 s for a >= 300 ms pause), the 300 s audio
  cap or a disconnect. The capture becomes one `CandidateTurn` with `activity = ActivityCapture` (segments with
  timing and text, pauses, confidence, degraded flag). Speech during a non-capture phase is never captured or
  transcribed. Typed answers: refused during the reading (`typed_not_allowed_for_reading`) and non-capture phases
  (`activity_in_progress`); during relay/speak they become the capture when typed answers are allowed.
* **Final transcript.** After a spoken capture the runtime re-transcribes the audio in windows of at most 25 s cut
  at VAD pauses (`app/services/intake/transcribe.py`), each on a spare Whisper replica (`background=True`), waiting at
  most `INTAKE_ACTIVITY_FINAL_PASS_WAIT_SECONDS`; the result goes to `transcript_final` through a conditional update
  (same attempt, status still pending) and revises the answer text when the pass kept >= 80 % of the words. If it
  cannot run, the status becomes `pending_worker` and `intake.activity_transcribe` repeats the pass from the stored
  WAV 30 s later (`live_only` without a stored WAV or after the last attempt; `unavailable` without speech).
* **Disconnects.** A running capture ends with `disconnect` and still runs offline (kept as an interrupted attempt);
  a non-capture phase stops and its reveal time is accounted (runtime `shown_ms`, engine `reveal_shown_s`). On
  re-attach the client gets `session.state` (no content) and the engine's resume re-arms the activity with
  `activity.start {resumed: true}`: a capture phase restarts in full as a new attempt, a Drill B read phase is
  re-shown only for the time left (capped again by the runtime so the passage is never visible for more than 30 s
  in total; below 3 s it goes straight to the relay), a card prepare phase for the prepare time left. Queued phase
  events are dropped on detach so a stale `phase_started` can never mark a re-armed phase running. A re-armed read
  phase with no reveal time left still sends `phase_started` (duration 0) before its `phase_deadline`.
* **Failure recovery.** When the engine answers a finished phase or an activity capture without a next step (the job
  failed or was stale), the runtime releases the floor (`realtime.activity_stalled`, activity `interrupted`) so the
  candidate's speech is never blocked; the job runner's "something went wrong" notice invites the candidate to
  continue. The engine re-arms the activity on the next turn: a lost non-capture phase moves on to its capture phase
  (the Drill B passage is never re-shown by a recovery), a lost capture phase restarts as a new attempt.
* **Long answers.** `UtteranceMeta.decision.intake.answer_mode = "long"` (self-introduction, project, résumé
  ownership/mechanics rungs, scenarios, three-weeks plan, teach-me) makes the next capture use
  `base 1500 / short 1100 / long 2600 ms`; every other answer keeps the settings values.
* **Glossary.** Intake sessions add the kit passages' technical terms to the session glossary (Whisper ignores
  prompts, so the reading transcript is corrected towards them).
