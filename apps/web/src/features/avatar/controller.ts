/**
 * createAvatarController: the AvatarController implementation the interview
 * room talks to. Owns the renderer-independent AvatarEngine and the status
 * store; renderers (<AvatarStage/>) reach the engine through a private symbol
 * so the public surface stays exactly the contract in types.ts. No method
 * throws: invalid input is ignored, failures become status.
 */
import type { AvatarState } from "@/lib/realtime/protocol";
import { AvatarEngine } from "./engine";
import { hashString } from "./core/math";
import { estimateWordDurationMs } from "./lipsync/visemes";
import type { AvatarRig } from "./rig/rig";
import type {
  AvatarBehavior,
  AvatarConfig,
  AvatarController,
  AvatarRenderMode,
  AvatarStatus,
  SpeechCue,
} from "./types";

const AVATAR_STATES: ReadonlySet<string> = new Set<AvatarState>([
  "IDLE",
  "LISTENING",
  "ACKNOWLEDGING",
  "THINKING",
  "SPEAKING",
  "QUESTIONING",
  "ENCOURAGING",
  "TRANSITION",
  "INTERRUPTED",
  "CLOSING",
]);

const INTERNALS = Symbol.for("interview.avatar.internals");

/**
 * A loaded rig owned by the controller, so a renderer that remounts (the room
 * re-parents or resizes the stage, a WebGL context is restored) reuses it
 * instead of downloading and parsing the model again. `error` is the status
 * error that came with it (e.g. a procedural fallback after a failed load).
 */
export interface CachedRig {
  key: string;
  rig: AvatarRig;
  error?: string;
}

export interface AvatarInternals {
  readonly config: AvatarConfig;
  readonly engine: AvatarEngine;
  /** Merge a status patch and notify subscribers when something changed. */
  setStatus(patch: Partial<AvatarStatus>): void;
  /** FPS reports are throttled (>= 1 fps change, at most once per second). */
  reportFps(fps: number, dpr?: number): void;
  reportContextLost(): void;
  reportRenderError(error: unknown): void;
  reportModelLoaded(modelName: string, fallbackError?: string): void;
  setRenderMode(mode: AvatarRenderMode, error?: string): void;
  isDisposed(): boolean;
  /** The cached rig for `key`, if any. The renderer must not dispose it. */
  claimRig(key: string): CachedRig | null;
  /** Hand a loaded rig to the controller; a cached rig for another key is disposed. */
  retainRig(entry: CachedRig): void;
}

type InternalController = AvatarController & { [INTERNALS]: AvatarInternals };

export function getAvatarInternals(controller: AvatarController | null | undefined): AvatarInternals | null {
  if (!controller) return null;
  return (controller as Partial<InternalController>)[INTERNALS] ?? null;
}

function initialMode(config: AvatarConfig): AvatarRenderMode {
  if (config.renderMode && config.renderMode !== "auto") return config.renderMode;
  return "3d";
}

function prefersReducedMotion(): boolean {
  try {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : "unknown error";
}

export function createAvatarController(config: AvatarConfig, options: { wallClock?: () => number } = {}): AvatarController {
  const reducedMotion = config.reducedMotion ?? prefersReducedMotion();
  const seed = config.seed ?? hashString(`${config.interviewerName || "interviewer"}:${config.modelUrl}`);
  const engine = new AvatarEngine({ seed, reducedMotion, wallClock: options.wallClock });
  const listeners = new Set<(status: AvatarStatus) => void>();
  let status: AvatarStatus = {
    mode: initialMode(config),
    loaded: initialMode(config) === "none",
    fps: 0,
  };
  let disposed = false;
  let cachedRig: CachedRig | null = null;
  let lastFpsNotify = -Infinity;
  let lastFpsValue = 0;

  const notify = () => {
    const snapshot = { ...status };
    for (const listener of [...listeners]) {
      try {
        listener(snapshot);
      } catch (err) {
        // A faulty subscriber must not break the avatar or other subscribers.
        console.error("[avatar] status listener failed", err);
      }
    }
  };

  const setStatus = (patch: Partial<AvatarStatus>) => {
    if (disposed) return;
    let changed = false;
    const next = { ...status };
    for (const [key, value] of Object.entries(patch) as [keyof AvatarStatus, unknown][]) {
      if (next[key] !== value) {
        (next as Record<string, unknown>)[key] = value;
        changed = true;
      }
    }
    if (!changed) return;
    status = next;
    notify();
  };

  const guard =
    <A extends unknown[]>(fn: (...args: A) => void) =>
    (...args: A) => {
      if (disposed) return;
      try {
        fn(...args);
      } catch (err) {
        console.error("[avatar] controller call failed", err);
      }
    };

  const internals: AvatarInternals = {
    config,
    engine,
    setStatus,
    reportFps(fps: number, dpr?: number) {
      if (disposed) return;
      const now = engine.now();
      const rounded = Math.round(fps);
      if (Math.abs(rounded - lastFpsValue) >= 1 && now - lastFpsNotify >= 1) {
        lastFpsNotify = now;
        lastFpsValue = rounded;
        setStatus(dpr === undefined ? { fps: rounded } : { fps: rounded, dpr: Math.round(dpr * 100) / 100 });
      }
    },
    reportContextLost() {
      setStatus({ mode: "2d", error: "webgl_context_lost", loaded: true });
    },
    reportRenderError(error: unknown) {
      setStatus({ mode: "2d", error: `render_error: ${errorMessage(error)}`, loaded: true });
    },
    reportModelLoaded(modelName: string, fallbackError?: string) {
      setStatus(fallbackError ? { loaded: true, modelName, error: fallbackError } : { loaded: true, modelName, error: undefined });
    },
    setRenderMode(mode: AvatarRenderMode, error?: string) {
      setStatus(error === undefined ? { mode } : { mode, error });
    },
    isDisposed: () => disposed,
    claimRig(key: string) {
      return !disposed && cachedRig && cachedRig.key === key ? cachedRig : null;
    },
    retainRig(entry: CachedRig) {
      if (disposed) {
        entry.rig.dispose();
        return;
      }
      if (cachedRig && cachedRig.rig !== entry.rig) cachedRig.rig.dispose();
      cachedRig = entry;
    },
  };

  const now = () => engine.now();

  const controller: InternalController = {
    setState: guard((state: AvatarState) => {
      if (!AVATAR_STATES.has(state)) return;
      engine.behavior.setState(state, now());
    }),
    applyBehavior: guard((behavior: AvatarBehavior) => {
      if (!behavior || !AVATAR_STATES.has(behavior.state)) return;
      engine.behavior.applyBehavior(behavior, now());
    }),
    attachAudio: guard((context: AudioContext, analyser: AnalyserNode | null) => {
      if (!context) return;
      engine.attachAudio(context, analyser ?? null);
    }),
    enqueueSpeech: guard((cue: SpeechCue) => {
      if (!cue) return;
      engine.lipSync.enqueue(cue);
    }),
    speakWordBoundary: guard((word: string, durationMs: number) => {
      if (!word) return;
      const ms = Number.isFinite(durationMs) && durationMs > 0 ? durationMs : estimateWordDurationMs(word);
      engine.lipSync.speakWord(word, ms);
    }),
    interrupt: guard(() => {
      engine.lipSync.interrupt();
      engine.behavior.interrupt(now());
    }),
    setCandidateSpeaking: guard((speaking: boolean, level?: number) => {
      engine.behavior.setCandidateSpeaking(!!speaking, typeof level === "number" ? level : undefined, now());
      engine.setCandidateLevel(!!speaking, level);
    }),
    getStatus: () => ({ ...status }),
    subscribe(listener: (status: AvatarStatus) => void) {
      if (disposed || typeof listener !== "function") return () => {};
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      listeners.clear();
      engine.lipSync.interrupt();
      try {
        cachedRig?.rig.dispose();
      } catch (err) {
        console.error("[avatar] rig dispose failed", err);
      }
      cachedRig = null;
    },
    [INTERNALS]: internals,
  };
  return controller;
}
