/**
 * Avatar engine public contract. The interview room (features/interview)
 * talks to the avatar ONLY through AvatarController; everything else inside
 * features/avatar is private. Implemented by createAvatarController() in
 * features/avatar/controller.ts and rendered by <AvatarStage/>.
 *
 * The LLM never controls bones: the server sends semantic behaviour
 * (state/expression/gesture/intensity/tone) and viseme timelines; the
 * behaviour controller maps them deterministically to animation choices.
 * Blinking, breathing, saccades and idle sway are automatic.
 */

import type {
  AvatarExpression,
  AvatarGesture,
  AvatarState,
  GestureIntensity,
  SpeechTone,
  Viseme,
} from "@/lib/realtime/protocol";

export interface AvatarBehavior {
  state: AvatarState;
  expression?: AvatarExpression;
  gesture?: AvatarGesture;
  gesture_intensity?: GestureIntensity;
  tone?: SpeechTone;
}

/** One scheduled chunk of interviewer speech. */
export interface SpeechCue {
  utteranceId: string;
  chunkId: number;
  /** AudioContext time (seconds) at which this chunk starts playing. */
  startTime: number;
  durationMs: number;
  /** Viseme cues relative to the chunk start (ms). */
  visemes: Viseme[];
  text: string;
}

export type AvatarRenderMode = "3d" | "2d" | "none";

export interface AvatarStatus {
  mode: AvatarRenderMode;
  loaded: boolean;
  fps: number;
  error?: string;
  modelName?: string;
  /** Current render pixel ratio after adaptive quality (3D only). */
  dpr?: number;
}

export interface AvatarConfig {
  /**
   * GLB/VRM URL served from the same origin, "procedural", or an interviewer
   * presence palette ("orb:aurora" | "orb:sunrise" | "orb:ocean", with modelFormat "orb").
   */
  modelUrl: string;
  modelFormat: "glb" | "vrm" | "procedural" | "orb";
  interviewerName: string;
  background?: "studio" | "office" | "plain";
  /** Cap device pixel ratio for performance (default 1.75). */
  maxDpr?: number;
  reducedMotion?: boolean;
  /**
   * Rendering: "auto" (default) uses 3D when WebGL is available and falls
   * back to the 2D portrait otherwise; "2d" / "none" force a mode ("none" =
   * audio + text only, nothing is drawn).
   */
  renderMode?: "auto" | AvatarRenderMode;
  /** Seed for micro-behaviour and variant selection (default: derived from interviewerName + modelUrl). */
  seed?: number;
}

export interface AvatarController {
  /** Set the high-level state; transitions crossfade (no snapping). */
  setState(state: AvatarState): void;
  /** Apply semantic behaviour from the server (avatar.behavior / utterance meta). */
  applyBehavior(behavior: AvatarBehavior): void;
  /** Bind the AudioContext + analyser of the TTS playback chain (lip-sync amplitude). */
  attachAudio(context: AudioContext, analyser: AnalyserNode | null): void;
  /** Queue a speech chunk; lip-sync follows the AudioContext clock. */
  enqueueSpeech(cue: SpeechCue): void;
  /** Browser speechSynthesis fallback: drive visemes from word boundaries. */
  speakWordBoundary(word: string, durationMs: number): void;
  /** Stop speaking immediately (barge-in): closes mouth smoothly, drops queued cues. */
  interrupt(): void;
  /** Candidate is talking: attentive listening with occasional nods. */
  setCandidateSpeaking(speaking: boolean, level?: number): void;
  getStatus(): AvatarStatus;
  subscribe(listener: (status: AvatarStatus) => void): () => void;
  dispose(): void;
}
