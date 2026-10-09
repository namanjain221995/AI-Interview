/**
 * 3D interviewer (React Three Fiber): bust framing, portrait lighting (warm
 * key, soft fill, rims that separate the dark blazer from the backdrop) with
 * a code-built studio PMREM for ambient light and eye catchlights, ACES tone
 * mapping, procedural studio backdrop, DPR cap with adaptive quality, and
 * context-loss detection. The model loads asynchronously; any load failure
 * falls back to the procedural bust and is reported through the controller
 * status.
 */
import { Canvas, useFrame, useThree, type RootState } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ACESFilmicToneMapping, PMREMGenerator, SRGBColorSpace, Vector3, type PerspectiveCamera } from "three";
import { AvatarAnimator } from "../animation/animator";
import type { AvatarInternals } from "../controller";
import { DEG } from "../core/math";
import type { AvatarEngine } from "../engine";
import { createProceduralRig } from "../procedural/proceduralAvatar";
import { loadAvatarRig } from "../rig/loader";
import type { AvatarRig, RigMetrics } from "../rig/rig";
import type { AvatarConfig } from "../types";
import { createBackdropTexture } from "./backdrop";
import type { BackdropKind } from "./environment";
import { AdaptiveDpr } from "./quality";
import { createStudioEnvironment, disposeStudioEnvironment } from "./studioEnvironment";

export type AvatarFraming = "bust" | "upper-body";

export interface Avatar3DProps {
  internals: AvatarInternals;
  config: AvatarConfig;
  /** "bust" (default): head, shoulders, upper torso. "upper-body": down to the hips (inspection). */
  framing?: AvatarFraming;
  /** Lower the pixel ratio when FPS drops (default true); false pins it at maxDpr. */
  adaptiveQuality?: boolean;
  /** WebGL context lost or unrecoverable: the stage switches to 2D. */
  onContextLost: () => void;
}

const DEFAULT_METRICS: RigMetrics = {
  headTopY: 1.78,
  eyeY: 1.66,
  neckY: 1.5,
  chestY: 1.36,
  shoulderWidth: 0.44,
  eyeCenter: new Vector3(0, 1.66, 0.08),
};

/**
 * Directions (from the subject) of the studio lights; directional lights aim
 * at the origin. The same directions are baked into the studio PMREM.
 */
const KEY = new Vector3(-2.2, 1.3, 1.6);
const FILL = new Vector3(1.8, 0.5, 1.5);
const RIM = new Vector3(0.9, 1.45, -1.8);
const RIM_2 = new Vector3(-1.1, 0.9, -1.6);

/** Tone mapping exposure (ACES). */
const STAGE_EXPOSURE = 0.92;

function StudioLighting({ background }: { background: BackdropKind }) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  useEffect(() => {
    const pmrem = new PMREMGenerator(gl);
    const studio = createStudioEnvironment();
    const env = pmrem.fromScene(studio, 0.04).texture;
    disposeStudioEnvironment(studio);
    scene.environment = env;
    // Soft wrap-around light from the studio; the key still models the face.
    scene.environmentIntensity = 0.42;
    const backdrop = createBackdropTexture(background);
    scene.background = backdrop;
    return () => {
      scene.environment = null;
      scene.background = null;
      env.dispose();
      pmrem.dispose();
      backdrop?.dispose();
    };
  }, [gl, scene, background]);
  // Warm key, neutral fill (a cool fill turned the shadow side grey), a cool
  // rim and a warm kicker behind, and a warm bounce from below.
  return (
    <>
      <directionalLight position={KEY} intensity={2.7} color="#ffe0c8" />
      <directionalLight position={FILL} intensity={0.55} color="#f4ece6" />
      <directionalLight position={RIM} intensity={1.9} color="#e6eeff" />
      <directionalLight position={RIM_2} intensity={1.0} color="#ffe6d6" />
      <hemisphereLight args={["#f1ebe5", "#332d29", 0.3]} />
    </>
  );
}

/**
 * Frames the interviewer for any aspect ratio. "bust" is a medium close-up:
 * a little headroom, the frame bottom at the blazer button so gesturing hands
 * (chest height) enter the shot; narrow viewports back off to keep the
 * shoulders in.
 */
function BustCamera({ metrics, framing }: { metrics: RigMetrics; framing: AvatarFraming }) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const width = useThree((s) => s.size.width);
  const height = useThree((s) => s.size.height);
  useLayoutEffect(() => {
    const m = metrics;
    const top = m.headTopY + (framing === "upper-body" ? 0.07 : 0.03);
    const bottom = framing === "upper-body" ? m.eyeY - 0.95 : m.eyeY - 0.45;
    const span = top - bottom;
    const aspect = width / Math.max(1, height);
    const vfov = camera.fov * DEG;
    let distance = span / 2 / Math.tan(vfov / 2);
    // Narrow (portrait) viewports: keep the shoulders and gesturing hands in frame.
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * aspect);
    distance = Math.max(distance, (m.shoulderWidth * 1.3) / 2 / Math.tan(hfov / 2));
    // When the camera backs off for width, keep the head near the top and let
    // the extra height show more of the torso instead of empty space above.
    const centreY = Math.min((top + bottom) / 2, top - distance * Math.tan(vfov / 2));
    camera.position.set(0, m.eyeY - 0.03, m.eyeCenter.z + distance);
    camera.lookAt(0, centreY, m.eyeCenter.z * 0.5);
    camera.near = 0.05;
    camera.far = 30;
    camera.updateProjectionMatrix();
  }, [camera, metrics, width, height, framing]);
  return null;
}

function RigView({ rig, engine }: { rig: AvatarRig; engine: AvatarEngine }) {
  // Created in an effect (not useMemo) so StrictMode's mount/unmount/mount cycle
  // never leaves a disposed animator in use.
  const [animator, setAnimator] = useState<AvatarAnimator | null>(null);
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const cameraPos = useMemo(() => new Vector3(), []);
  useEffect(() => {
    // The rig is owned by the controller and outlives this canvas (re-parented
    // stage, restored context), so it is added by hand rather than as an R3F
    // <primitive> that would carry instance state from a previous root.
    scene.add(rig.root);
    const a = new AvatarAnimator(rig);
    setAnimator(a);
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__avatarDebug = { rig, animator: a, camera, gl, scene };
    return () => {
      a.dispose();
      setAnimator(null);
      scene.remove(rig.root);
    };
  }, [rig, camera, gl, scene]);
  useFrame(() => {
    if (!animator) return;
    const frame = engine.update();
    camera.getWorldPosition(cameraPos);
    animator.update(frame, cameraPos);
  });
  return null;
}

function PerformanceProbe({ onSample }: { onSample: (fps: number) => void }) {
  const acc = useRef({ frames: 0, since: 0 });
  useFrame(() => {
    const now = performance.now();
    const a = acc.current;
    if (a.since === 0) a.since = now;
    a.frames += 1;
    if (now - a.since >= 500) {
      onSample((a.frames * 1000) / (now - a.since));
      a.frames = 0;
      a.since = now;
    }
  });
  return null;
}

export default function Avatar3D({ internals, config, onContextLost, framing = "bust", adaptiveQuality = true }: Avatar3DProps) {
  const [rig, setRig] = useState<AvatarRig | null>(null);
  const [progress, setProgress] = useState(0);
  const maxDpr = config.maxDpr ?? 1.75;
  const adaptive = useMemo(
    () => new AdaptiveDpr({ max: maxDpr, initial: Math.min(typeof devicePixelRatio === "number" ? devicePixelRatio : 1, maxDpr) }),
    [maxDpr],
  );
  const [dpr, setDpr] = useState(adaptive.dpr);
  useEffect(() => setDpr(adaptive.dpr), [adaptive]);
  const lostRef = useRef(onContextLost);
  lostRef.current = onContextLost;

  useEffect(() => {
    // The controller keeps the loaded rig: remounting this canvas (layout
    // switch, re-parenting, context restore) reuses it without a reload.
    const key = `${config.modelFormat}:${config.modelUrl}`;
    const cached = internals.claimRig(key);
    if (cached) {
      setRig(cached.rig);
      internals.reportModelLoaded(cached.rig.name, cached.error);
      return () => setRig(null);
    }
    const abort = new AbortController();
    let cancelled = false;
    internals.setStatus({ loaded: false });
    setProgress(0);
    loadAvatarRig(config, { signal: abort.signal, onProgress: setProgress })
      .then((loaded) => {
        if (cancelled) {
          loaded.dispose();
          return;
        }
        internals.retainRig({ key, rig: loaded });
        setRig(loaded);
        internals.reportModelLoaded(loaded.name);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const reason = err instanceof Error ? err.message : String(err);
        console.warn("[avatar] model failed to load, using the procedural avatar:", reason);
        try {
          const fallback = createProceduralRig({ name: "procedural" });
          const error = `model_load_failed: ${reason}`;
          internals.retainRig({ key, rig: fallback, error });
          setRig(fallback);
          internals.reportModelLoaded("procedural", error);
        } catch (fatal) {
          internals.reportRenderError(fatal);
          lostRef.current();
        }
      });
    return () => {
      cancelled = true;
      abort.abort();
      setRig(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when the asset changes
  }, [internals, config.modelUrl, config.modelFormat]);

  const glProps = useMemo(
    () => ({
      antialias: true,
      alpha: false,
      powerPreference: "high-performance" as const,
      toneMapping: ACESFilmicToneMapping,
      toneMappingExposure: STAGE_EXPOSURE,
      outputColorSpace: SRGBColorSpace,
    }),
    [],
  );

  // R3F force-loses the context when it disposes an unmounting canvas (layout
  // switch, re-parenting, 2D request): that is not a GPU failure, so the
  // listener is detached first and late events are ignored.
  const contextWatch = useRef<{ canvas: HTMLCanvasElement; handler: (event: Event) => void } | null>(null);
  const unmounted = useRef(false);
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
      const watch = contextWatch.current;
      watch?.canvas.removeEventListener("webglcontextlost", watch.handler);
      contextWatch.current = null;
    };
  }, []);

  const onCreated = (state: RootState) => {
    const canvas = state.gl.domElement;
    const previous = contextWatch.current;
    previous?.canvas.removeEventListener("webglcontextlost", previous.handler);
    const handler = (event: Event) => {
      event.preventDefault();
      if (unmounted.current) return;
      internals.reportContextLost();
      lostRef.current();
    };
    canvas.addEventListener("webglcontextlost", handler, { once: true });
    contextWatch.current = { canvas, handler };
  };

  const onSample = (fps: number) => {
    const next = adaptiveQuality ? adaptive.update(fps, performance.now() / 1000) : null;
    if (next !== null) setDpr(next);
    internals.reportFps(fps, adaptive.dpr);
  };

  const metrics = rig?.metrics ?? DEFAULT_METRICS;
  return (
    <div className="absolute inset-0">
      <Canvas
        dpr={dpr}
        gl={glProps}
        camera={{ fov: 24, near: 0.05, far: 30, position: [0, 1.63, 1.6] }}
        frameloop="always"
        onCreated={onCreated}
        style={{ position: "absolute", inset: 0 }}
        aria-hidden="true"
      >
        <StudioLighting background={config.background ?? "studio"} />
        <BustCamera metrics={metrics} framing={framing} />
        {rig && <RigView rig={rig} engine={internals.engine} />}
        <PerformanceProbe onSample={onSample} />
      </Canvas>
      {!rig && (
        <div className="pointer-events-none absolute inset-x-0 bottom-6 flex justify-center">
          <div className="rounded-full bg-black/40 px-3 py-1 text-xs text-white/80 backdrop-blur">
            Loading interviewer{progress > 0 && progress < 1 ? ` ${Math.round(progress * 100)}%` : "…"}
          </div>
        </div>
      )}
    </div>
  );
}
