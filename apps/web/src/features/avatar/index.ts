/**
 * Public API of the avatar feature. Everything else under features/avatar is
 * private to the feature.
 *
 * Integration (interview room):
 *
 *   // Create in an effect so React StrictMode's double mount disposes the
 *   // first instance and keeps a live one. The default interviewer is the
 *   // animated presence orb ({ modelFormat: "orb", modelUrl: "orb:aurora" });
 *   // resolveAvatarModel() maps the organisation's avatar settings.
 *   useEffect(() => {
 *     const c = createAvatarController({ ...resolveAvatarModel(settings.model_format, settings.model_url), interviewerName });
 *     setController(c);
 *     return () => c.dispose();
 *   }, [interviewerName]);
 *   <AvatarStage controller={controller} className="h-full w-full" />
 *
 * Protocol mapping (lib/realtime/protocol.ts):
 *   avatar.behavior / ai.utterance.start.behavior -> controller.applyBehavior(msg)
 *   tts.chunk + its 0x02 audio frame               -> schedule the PCM on the playback
 *       AudioContext at time T, then controller.enqueueSpeech({ utteranceId, chunkId,
 *       startTime: T, durationMs, visemes, text })
 *   playback chain ready                           -> controller.attachAudio(ctx, analyser)
 *   barge_in / local barge-in                      -> controller.interrupt()
 *   tts.fallback (browser speechSynthesis)         -> on each "word" boundary event
 *       controller.speakWordBoundary(word, estimateWordDurationMs(word))
 *   vad.local / mic level                          -> controller.setCandidateSpeaking(speaking, level)
 *   session.state.avatar_state                     -> controller.setState(state)
 *
 * Status: controller.subscribe() reports { mode: "3d" | "2d" | "none", loaded,
 * fps, error?, modelName?, dpr? }. A WebGL failure or context loss switches to
 * the 2D portrait by itself; `error` explains why (e.g. "webgl_context_lost",
 * "model_load_failed: ..."), suitable for a client.error { component: "avatar" }
 * report. After a context loss the stage returns to 3D once a fresh context
 * can be created (it stays 2D if the GPU keeps failing). Nothing here ever
 * throws into the caller.
 *
 * Layout: <AvatarStage/> can be resized, re-parented or remounted with the
 * same controller (e.g. switching to the coding layout); the controller keeps
 * the loaded model, so a remount does not download or parse it again. Dispose
 * the controller only when the interviewer goes away for good.
 */
import { orbModelUrl, parseOrbModel } from "./orb/palettes";
import type { AvatarConfig } from "./types";

export { AvatarStage, type AvatarStageProps } from "./AvatarStage";
export { createAvatarController } from "./controller";
export { estimateWordDurationMs } from "./lipsync/visemes";
export {
  DEFAULT_ORB_PALETTE,
  ORB_PALETTES,
  ORB_PALETTE_SPECS,
  orbModelUrl,
  parseOrbModel,
  type OrbPalette,
  type OrbPaletteSpec,
} from "./orb/palettes";
export type {
  AvatarBehavior,
  AvatarConfig,
  AvatarController,
  AvatarRenderMode,
  AvatarStatus,
  SpeechCue,
} from "./types";

/** The realistic 3D interviewer asset served by the web app (same origin; optional, "glb" format). */
export const DEFAULT_AVATAR_URL = "/avatars/interviewer.glb";

/** The default interviewer: the animated presence orb. */
export const DEFAULT_PRESENCE: Readonly<Pick<AvatarConfig, "modelUrl" | "modelFormat">> = { modelUrl: "orb:aurora", modelFormat: "orb" };

/**
 * Map an organisation's avatar settings (model_format, model_url) to the
 * controller config. "orb" (the default, also used for unknown or retired
 * formats such as "cartoon") keeps a known palette; "glb"/"vrm" need a
 * same-origin path (else the bundled realistic asset); "procedural" is the
 * built-in figure.
 */
export function resolveAvatarModel(format: string | null | undefined, url: string | null | undefined): Pick<AvatarConfig, "modelUrl" | "modelFormat"> {
  const path = typeof url === "string" ? url.trim() : "";
  if (format === "procedural") return { modelUrl: "procedural", modelFormat: "procedural" };
  if (format === "glb" || format === "vrm") {
    const sameOrigin = path.startsWith("/") && !path.startsWith("//");
    return { modelUrl: sameOrigin ? path : DEFAULT_AVATAR_URL, modelFormat: sameOrigin ? format : "glb" };
  }
  return { modelUrl: orbModelUrl(parseOrbModel(path)), modelFormat: "orb" };
}

/** Warm the HTTP cache for the avatar asset (e.g. during the system check). The presence orb needs no download. */
export function preloadAvatar(url: string = DEFAULT_PRESENCE.modelUrl): void {
  if (typeof document === "undefined" || url === "procedural" || url.startsWith("orb:")) return;
  try {
    const href = new URL(url, location.href);
    if (href.origin !== location.origin) return;
    if (document.head.querySelector(`link[rel="preload"][href="${href.pathname}"]`)) return;
    const link = document.createElement("link");
    link.rel = "preload";
    link.as = "fetch";
    link.href = href.pathname;
    link.crossOrigin = "anonymous";
    document.head.appendChild(link);
  } catch {
    /* best effort */
  }
}
