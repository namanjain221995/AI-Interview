/**
 * Realtime interview protocol — TypeScript mirror of
 * apps/api/app/schemas/realtime.py. Keep both files in sync.
 *
 * Binary frames (little endian):
 *   byte 0     frame type  0x01 mic PCM16 mono 16 kHz (client -> server)
 *                          0x02 TTS PCM16 mono (server -> client)
 *   byte 1     flags       bit0 = final chunk of utterance (0x02)
 *   bytes 2-3  reserved
 *   bytes 4-7  uint32      0x01: audio seq; 0x02: chunk_id from preceding tts.chunk
 *   bytes 8..  PCM16 payload
 */

export const FRAME_MIC_AUDIO = 0x01;
export const FRAME_TTS_AUDIO = 0x02;
export const FRAME_HEADER_BYTES = 8;
export const MIC_SAMPLE_RATE = 16000;
export const PROTOCOL_VERSION = 1;

export type AvatarState =
  | "IDLE"
  | "LISTENING"
  | "ACKNOWLEDGING"
  | "THINKING"
  | "SPEAKING"
  | "QUESTIONING"
  | "ENCOURAGING"
  | "TRANSITION"
  | "INTERRUPTED"
  | "CLOSING";

export type AvatarExpression =
  | "neutral"
  | "warm"
  | "curious"
  | "thoughtful"
  | "encouraging"
  | "serious"
  | "pleased";

export type AvatarGesture =
  | "none"
  | "small_open_palm"
  | "one_hand_explain"
  | "two_hand_explain"
  | "small_emphasis"
  | "question_gesture"
  | "nod"
  | "head_tilt"
  | "goodbye";

export type GestureIntensity = "low" | "medium" | "high";
export type SpeechTone = "professional" | "friendly" | "supportive" | "neutral";

export type TurnState =
  | "idle"
  | "ai_speaking"
  | "listening"
  | "candidate_speaking"
  | "processing"
  | "coding"
  | "paused"
  | "ended";

export type SessionStatus =
  | "created"
  | "preparing"
  | "ready"
  | "live"
  | "paused"
  | "completing"
  | "completed"
  | "aborted"
  | "failed";

export type CodeLanguage = "javascript" | "typescript" | "python" | "java" | "go" | "cpp" | "sql";
export type RunKind = "run" | "test" | "submit";
export type RunStatus =
  | "queued"
  | "running"
  | "passed"
  | "failed"
  | "error"
  | "compile_error"
  | "timeout"
  | "memory_exceeded"
  | "rejected";

export type IntegrityEventType =
  | "fullscreen_exited"
  | "fullscreen_declined"
  | "tab_hidden"
  | "focus_lost"
  | "rapid_focus_switching"
  | "window_minimized"
  | "copy_event"
  | "paste_event"
  | "large_paste_in_coding"
  | "prohibited_shortcut"
  | "context_menu"
  | "page_reload"
  | "disconnect"
  | "reconnect"
  | "unusual_disconnect_pattern"
  | "multiple_displays_detected"
  | "camera_disabled"
  | "camera_blocked"
  | "microphone_disabled"
  | "no_person_visible"
  | "multiple_persons_visible"
  | "person_left_frame"
  | "another_person_entered"
  | "possible_phone_visible"
  | "possible_photo_taking"
  | "looking_away_with_other_signals"
  | "screen_share_started"
  | "screen_share_stopped"
  | "screen_share_not_full_screen"
  | "screen_context_change"
  | "face_not_visible"
  | "multiple_faces_visible"
  | "static_face_ignored"
  | "face_check_summary";

// ---------------------------------------------------------------- client -> server

export interface ClientInfo {
  user_agent?: string;
  webgl: boolean;
  avatar_mode: "3d" | "2d" | "none";
  tts_mode: "server" | "browser" | "text";
  sample_rate: number;
  timezone?: string;
}

export type ClientMessage =
  | { type: "hello"; protocol: number; last_seq: number; client: ClientInfo }
  | { type: "control.start" }
  | { type: "control.end_turn" }
  | { type: "control.repeat" }
  | { type: "control.end_interview"; reason?: string }
  | { type: "answer.text"; text: string }
  | {
      type: "playback.started" | "playback.progress" | "playback.finished" | "playback.interrupted";
      utterance_id: string;
      played_ms: number;
      chunk_id?: number;
    }
  | { type: "vad.local"; speaking: boolean }
  | {
      type: "code.update";
      coding_session_id: string;
      language: CodeLanguage;
      code: string;
      version: number;
      cursor_line?: number;
    }
  | {
      type: "code.run";
      coding_session_id: string;
      kind: RunKind;
      language: CodeLanguage;
      code: string;
      stdin?: string;
    }
  | { type: "code.submit"; coding_session_id: string; language: CodeLanguage; code: string }
  | { type: "code.hint_request"; coding_session_id: string }
  | { type: "code.language"; coding_session_id: string; language: CodeLanguage }
  | { type: "integrity.events"; events: ClientIntegrityEvent[] }
  | { type: "heartbeat"; t: number }
  | {
      type: "client.error";
      component: "avatar" | "audio_capture" | "audio_playback" | "editor" | "screen_share" | "camera" | "other";
      message: string;
      fatal?: boolean;
    };

export interface ClientIntegrityEvent {
  event_type: IntegrityEventType;
  occurred_at: string;
  duration_ms?: number;
  details?: Record<string, unknown>;
}

// ---------------------------------------------------------------- server -> client

interface ServerBase {
  seq: number;
}

export interface AvatarBehaviorMsg extends ServerBase {
  type: "avatar.behavior";
  utterance_id?: string | null;
  state: AvatarState;
  expression: AvatarExpression;
  gesture: AvatarGesture;
  gesture_intensity: GestureIntensity;
  tone: SpeechTone;
}

export interface CodingQuestionPublic {
  coding_question_id: string;
  title: string;
  prompt_markdown: string;
  difficulty: string;
  languages: CodeLanguage[];
  starter_code: Record<string, string>;
  visible_tests: Array<Record<string, unknown>>;
  signature?: Record<string, unknown> | null;
}

export interface CodingState {
  coding_session_id: string;
  question: CodingQuestionPublic;
  language: CodeLanguage;
  code?: string | null;
  time_limit_s: number;
  elapsed_s: number;
  hints_available: number;
  hints_used: number;
  hint_texts: string[];
}

export interface SessionStateMsg extends ServerBase {
  type: "session.state";
  session_id: string;
  status: SessionStatus;
  turn_state: TurnState;
  round_name: string;
  round_type: string;
  interviewer_name: string;
  elapsed_s: number;
  remaining_s: number;
  time_limit_s: number;
  segment?: string | null;
  question_count: number;
  current_question?: string | null;
  show_subtitles: boolean;
  allow_typed_answers: boolean;
  integrity: Record<string, unknown>;
  coding?: CodingState | null;
  avatar_state: AvatarState;
}

export interface TurnStateMsg extends ServerBase {
  type: "turn.state";
  state: TurnState;
  reason?: string | null;
}

export interface TranscriptMsg extends ServerBase {
  type: "transcript.partial" | "transcript.final";
  speaker: "candidate" | "interviewer";
  segment_id: string;
  text: string;
  answer_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
}

export type UtteranceKind =
  | "intro"
  | "question"
  | "followup"
  | "acknowledgment"
  | "comment"
  | "hint"
  | "transition"
  | "closing"
  | "resume";

export interface UtteranceStartMsg extends ServerBase {
  type: "ai.utterance.start";
  utterance_id: string;
  kind: UtteranceKind;
  question_id?: string | null;
  behavior?: AvatarBehaviorMsg | null;
}

export interface TextDeltaMsg extends ServerBase {
  type: "ai.text.delta";
  utterance_id: string;
  text: string;
}

export interface Viseme {
  t: number;
  v: string;
  w: number;
}

export interface TtsChunkMsg extends ServerBase {
  type: "tts.chunk";
  utterance_id: string;
  chunk_id: number;
  index: number;
  text: string;
  sample_rate: number;
  duration_ms: number;
  visemes: Viseme[];
  word_timings: Array<Record<string, unknown>>;
}

export interface TtsFallbackMsg extends ServerBase {
  type: "tts.fallback";
  utterance_id: string;
  text: string;
  reason: string;
}

export interface UtteranceEndMsg extends ServerBase {
  type: "ai.utterance.end";
  utterance_id: string;
  text: string;
  interrupted: boolean;
}

export interface BargeInMsg extends ServerBase {
  type: "barge_in";
  utterance_id: string;
}

export interface CodingStartMsg extends ServerBase {
  type: "coding.start";
  coding: CodingState;
}

export interface CodingRunResultMsg extends ServerBase {
  type: "coding.run_result";
  coding_session_id: string;
  run_id: string;
  kind: RunKind;
  status: RunStatus;
  stdout: string;
  stderr: string;
  compile_output: string;
  exit_code?: number | null;
  runtime_ms?: number | null;
  passed: number;
  total: number;
  tests: Array<Record<string, unknown>>;
}

export interface CodingHintMsg extends ServerBase {
  type: "coding.hint";
  coding_session_id: string;
  level: number;
  text: string;
  hints_used: number;
  hints_remaining: number;
}

export interface CodingEndMsg extends ServerBase {
  type: "coding.end";
  coding_session_id: string;
  reason: "submitted" | "time_limit" | "interviewer_moved_on" | "session_ended";
}

export interface IntegrityWarningMsg extends ServerBase {
  type: "integrity.warning";
  event_type: IntegrityEventType;
  message: string;
}

export interface NoticeMsg extends ServerBase {
  type: "notice";
  level: "info" | "warning" | "error";
  code: string;
  message: string;
}

export interface ErrorMsg extends ServerBase {
  type: "error";
  code: string;
  message: string;
  recoverable: boolean;
}

export interface SessionEndedMsg extends ServerBase {
  type: "session.ended";
  reason: string;
  report_status: string;
  candidate_report_available: boolean;
}

export interface HeartbeatAckMsg extends ServerBase {
  type: "heartbeat.ack";
  t: number;
  server_time: number;
}

export type ServerMessage =
  | AvatarBehaviorMsg
  | SessionStateMsg
  | TurnStateMsg
  | TranscriptMsg
  | UtteranceStartMsg
  | TextDeltaMsg
  | TtsChunkMsg
  | TtsFallbackMsg
  | UtteranceEndMsg
  | BargeInMsg
  | CodingStartMsg
  | CodingRunResultMsg
  | CodingHintMsg
  | CodingEndMsg
  | IntegrityWarningMsg
  | NoticeMsg
  | ErrorMsg
  | SessionEndedMsg
  | HeartbeatAckMsg;

export function packMicFrame(seq: number, pcm16: Int16Array): ArrayBuffer {
  const buf = new ArrayBuffer(FRAME_HEADER_BYTES + pcm16.byteLength);
  const view = new DataView(buf);
  view.setUint8(0, FRAME_MIC_AUDIO);
  view.setUint8(1, 0);
  view.setUint16(2, 0, true);
  view.setUint32(4, seq >>> 0, true);
  new Int16Array(buf, FRAME_HEADER_BYTES).set(pcm16);
  return buf;
}

export function unpackFrame(data: ArrayBuffer): {
  frameType: number;
  flags: number;
  id: number;
  payload: Int16Array;
} {
  const view = new DataView(data);
  const frameType = view.getUint8(0);
  const flags = view.getUint8(1);
  const id = view.getUint32(4, true);
  const payload = new Int16Array(data.slice(FRAME_HEADER_BYTES));
  return { frameType, flags, id, payload };
}
