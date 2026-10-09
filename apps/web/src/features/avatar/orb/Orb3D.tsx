/**
 * Interviewer presence (3D): a glassy, audio-reactive orb rendered with
 * React Three Fiber on a transparent canvas, so the stage's own backdrop
 * (design tokens, light or dark) shows around it.
 *
 * The orb is driven by the same AvatarEngine frames as the 3D avatar
 * (state, expression, gestures, the voice the candidate hears, the
 * candidate's microphone level) through OrbDriver; see orbSignals.ts.
 * Rendering pauses while the tab is hidden; the pixel ratio adapts under
 * load (cap: config.maxDpr, default 1.75); compact stages (the coding
 * corner) use a lighter mesh and a tighter halo.
 */
import { Canvas, useFrame, useThree, type RootState } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Color,
  CustomBlending,
  Group,
  NoToneMapping,
  OneFactor,
  OneMinusSrcAlphaFactor,
  PlaneGeometry,
  ShaderMaterial,
  SphereGeometry,
  SRGBColorSpace,
  Vector3,
  Vector4,
  type PerspectiveCamera,
} from "three";
import type { AvatarInternals } from "../controller";
import { AdaptiveDpr } from "../render/quality";
import type { AvatarConfig } from "../types";
import { OrbDriver } from "./orbSignals";
import { COMPACT_PX, COMPACT_SHARE, ORB_CORNER_SHIFT, STAGE_SHARE, WIDTH_SHARE } from "./layout";
import { HALO_FRAGMENT, HALO_VERTEX, ORB_FRAGMENT, ORB_VERTEX } from "./orbShaders";
import { hexToLinear, ORB_PALETTE_SPECS, parseOrbModel, type OrbPalette } from "./palettes";

export interface Orb3DProps {
  internals: AvatarInternals;
  config: AvatarConfig;
  /** Session paused or offline: desaturated, slow breathing. */
  dimmed?: boolean;
  /** A tile covers this corner: the orb sits off-centre (see ORB_CORNER_SHIFT). */
  occludedCorner?: "bottom-right";
  adaptiveQuality?: boolean;
  onContextLost: () => void;
}

const FOV = 30;

const lin = (hex: string) => new Color().setRGB(...hexToLinear(hex));

function paletteUniforms(palette: OrbPalette) {
  const p = ORB_PALETTE_SPECS[palette];
  return {
    uFlow0: lin(p.flow[0]),
    uFlow1: lin(p.flow[1]),
    uFlow2: lin(p.flow[2]),
    uFlow3: lin(p.flow[3]),
    uCore: lin(p.core),
    uRim: lin(p.rim),
    uWarm: lin(p.warm),
    uCool: lin(p.cool),
    uThink: lin(p.think),
    glow: lin(p.glow),
  };
}

function OrbScene({ internals, palette, dimmed, corner }: { internals: AvatarInternals; palette: OrbPalette; dimmed: boolean; corner?: "bottom-right" }) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const width = useThree((s) => s.size.width);
  const height = useThree((s) => s.size.height);
  const compact = Math.min(width, height) < COMPACT_PX;
  const group = useRef<Group>(null);
  const driver = useMemo(() => new OrbDriver(), []);
  const dimRef = useRef(dimmed);
  dimRef.current = dimmed;

  const geometry = useMemo(() => (compact ? new SphereGeometry(1, 72, 54) : new SphereGeometry(1, 160, 120)), [compact]);
  const haloGeometry = useMemo(() => new PlaneGeometry(5.4, 5.4), []);
  const orbMaterial = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: ORB_VERTEX,
        fragmentShader: ORB_FRAGMENT,
        uniforms: {
          uTime: { value: 0 },
          uDisplace: { value: 0.012 },
          uRipple: { value: 0 },
          uFlowPhase: { value: 0 },
          uSwirl: { value: 0.1 },
          uWarmth: { value: 0 },
          uViolet: { value: 0 },
          uSweep: { value: 0 },
          uBrightness: { value: 1 },
          uSaturation: { value: 1 },
          uShimmer: { value: 0 },
          uEnergy: { value: 0 },
          uVoice: { value: 0 },
          uCamObj: { value: new Vector3(0, 0, 9) },
          uFlow0: { value: new Color() },
          uFlow1: { value: new Color() },
          uFlow2: { value: new Color() },
          uFlow3: { value: new Color() },
          uCore: { value: new Color() },
          uRim: { value: new Color() },
          uWarm: { value: new Color() },
          uCool: { value: new Color() },
          uThink: { value: new Color() },
        },
      }),
    [],
  );
  const haloMaterial = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: HALO_VERTEX,
        fragmentShader: HALO_FRAGMENT,
        transparent: true,
        depthTest: false,
        depthWrite: false,
        // Premultiplied "over": a glow on dark stages, a tinted aura on light ones.
        blending: CustomBlending,
        blendSrc: OneFactor,
        blendDst: OneMinusSrcAlphaFactor,
        blendSrcAlpha: OneFactor,
        blendDstAlpha: OneMinusSrcAlphaFactor,
        uniforms: {
          uGlowA: { value: new Color() },
          uGlowB: { value: new Color() },
          uGlowC: { value: new Color() },
          uRingColor: { value: new Color() },
          uGlow: { value: 0.5 },
          uSpread: { value: 1 },
          uPhase: { value: 0 },
          uRingAge: { value: new Vector4(0, 0, 0, 0) },
          uRingStrength: { value: new Vector4(0, 0, 0, 0) },
        },
      }),
    [],
  );
  const tints = useRef({ a: new Color(), b: new Color(), c: new Color(), warm: new Color(), cool: new Color(), think: new Color() });

  useEffect(() => {
    const u = paletteUniforms(palette);
    const o = orbMaterial.uniforms;
    for (const key of ["uFlow0", "uFlow1", "uFlow2", "uFlow3", "uCore", "uRim", "uWarm", "uCool", "uThink"] as const) (o[key]!.value as Color).copy(u[key]);
    const t = tints.current;
    t.a.copy(u.uFlow2);
    t.b.copy(u.uFlow3);
    t.c.copy(u.glow);
    t.warm.copy(u.uWarm);
    t.cool.copy(u.uCool);
    t.think.copy(u.uThink);
    (haloMaterial.uniforms.uRingColor!.value as Color).copy(u.uRim).lerp(u.uFlow2, 0.3);
  }, [palette, orbMaterial, haloMaterial]);

  useEffect(
    () => () => {
      geometry.dispose();
    },
    [geometry],
  );
  useEffect(
    () => () => {
      haloGeometry.dispose();
      orbMaterial.dispose();
      haloMaterial.dispose();
    },
    [haloGeometry, orbMaterial, haloMaterial],
  );

  // Fit the camera: the sphere fills its share of the stage, centred.
  useLayoutEffect(() => {
    const aspect = width / Math.max(1, height);
    const tanHalf = Math.tan((FOV * Math.PI) / 360);
    // Diameter as a share of the height; bounded by the width on narrow stages.
    const share = compact ? COMPACT_SHARE * (aspect < 1 ? aspect : 1) : Math.min(STAGE_SHARE, WIDTH_SHARE * aspect);
    const theta = Math.atan(share * tanHalf);
    const distance = 1 / Math.sin(theta);
    camera.fov = FOV;
    camera.position.set(0, 0, distance);
    camera.lookAt(0, 0, 0);
    camera.near = Math.max(0.05, distance - 3);
    camera.far = distance + 3;
    // Off-centre when a tile covers a corner: shifting the view window moves the orb the other way.
    if (corner) camera.setViewOffset(width, height, ORB_CORNER_SHIFT.x * width, ORB_CORNER_SHIFT.y * height, width, height);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    haloMaterial.uniforms.uSpread!.value = compact ? 0.62 : 1;
  }, [camera, width, height, compact, corner, haloMaterial]);

  useEffect(() => {
    // Dev tooling (frame strips, sync measurements); stripped from production builds.
    if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__orbDebug = { driver, internals };
  }, [driver, internals]);

  const ringAge = useMemo(() => new Vector4(), []);
  const ringStrength = useMemo(() => new Vector4(), []);
  useFrame(() => {
    const engine = internals.engine;
    const frame = engine.update();
    driver.setReducedMotion(frame.motionScale < 0.99);
    const s = driver.update(frame, engine.analyserAttached, dimRef.current);
    const o = orbMaterial.uniforms;
    o.uTime!.value = s.time;
    o.uDisplace!.value = s.displace;
    o.uRipple!.value = s.ripple;
    o.uFlowPhase!.value = s.flowPhase;
    o.uSwirl!.value = s.swirl;
    o.uWarmth!.value = s.warmth;
    o.uViolet!.value = s.violet;
    o.uSweep!.value = s.sweep;
    o.uBrightness!.value = s.brightness;
    o.uSaturation!.value = s.saturation;
    o.uShimmer!.value = s.shimmer;
    o.uEnergy!.value = s.energy;
    o.uVoice!.value = s.voice;
    const h = haloMaterial.uniforms;
    h.uGlow!.value = s.glow * (compact ? 0.8 : 1);
    h.uPhase!.value = s.flowPhase * 0.35;
    // The aura follows the body's state leans.
    const t = tints.current;
    const lean = (c: Color, base: Color) => c.copy(base).lerp(t.warm, Math.max(0, s.warmth) * 0.3).lerp(t.cool, Math.max(0, -s.warmth) * 0.28).lerp(t.think, s.violet * 0.4);
    lean(h.uGlowA!.value as Color, t.a);
    lean(h.uGlowB!.value as Color, t.b);
    lean(h.uGlowC!.value as Color, t.c);
    ringAge.set(0, 0, 0, 0);
    ringStrength.set(0, 0, 0, 0);
    s.ripples.forEach((r, i) => {
      if (i > 3) return;
      ringAge.setComponent(i, r.age);
      ringStrength.setComponent(i, r.strength);
    });
    (h.uRingAge!.value as Vector4).copy(ringAge);
    (h.uRingStrength!.value as Vector4).copy(ringStrength);
    const g = group.current;
    if (g) {
      g.position.y = s.lift;
      g.scale.setScalar(s.scale);
      g.updateMatrixWorld();
      // Camera in the orb's object space (the view rays march through the glass).
      g.worldToLocal((o.uCamObj!.value as Vector3).copy(camera.position));
    }
  });

  return (
    <group ref={group}>
      <mesh geometry={haloGeometry} material={haloMaterial} renderOrder={-1} frustumCulled={false} />
      <mesh geometry={geometry} material={orbMaterial} frustumCulled={false} />
    </group>
  );
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

/** True while the document is visible (rendering pauses in hidden tabs). */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState !== "hidden");
  useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

export default function Orb3D({ internals, config, dimmed = false, occludedCorner, adaptiveQuality = true, onContextLost }: Orb3DProps) {
  const palette = parseOrbModel(config.modelUrl);
  const maxDpr = config.maxDpr ?? 1.75;
  const adaptive = useMemo(
    () => new AdaptiveDpr({ max: maxDpr, initial: Math.min(typeof devicePixelRatio === "number" ? devicePixelRatio : 1, maxDpr) }),
    [maxDpr],
  );
  const [dpr, setDpr] = useState(adaptive.dpr);
  useEffect(() => setDpr(adaptive.dpr), [adaptive]);
  const visible = usePageVisible();
  const lostRef = useRef(onContextLost);
  lostRef.current = onContextLost;

  useEffect(() => {
    internals.reportModelLoaded(`orb:${palette}`);
  }, [internals, palette]);

  const glProps = useMemo(
    () => ({
      antialias: true,
      alpha: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance" as const,
      toneMapping: NoToneMapping,
      outputColorSpace: SRGBColorSpace,
    }),
    [],
  );

  // As in Avatar3D: R3F force-loses the context when an unmounting canvas is
  // disposed, which is not a GPU failure, so the listener is detached first.
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
    state.gl.setClearColor(0x000000, 0);
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

  return (
    <div className="absolute inset-0">
      <Canvas
        dpr={dpr}
        gl={glProps}
        camera={{ fov: FOV, near: 0.1, far: 40, position: [0, 0, 9] }}
        frameloop={visible ? "always" : "never"}
        onCreated={onCreated}
        style={{ position: "absolute", inset: 0 }}
        aria-hidden="true"
      >
        <OrbScene internals={internals} palette={palette} dimmed={dimmed} corner={occludedCorner} />
        <PerformanceProbe onSample={onSample} />
      </Canvas>
    </div>
  );
}
