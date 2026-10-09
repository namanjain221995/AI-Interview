/**
 * Interviewer presence, 2D fallback (no WebGL, low power, renderMode "2d"):
 * the same orb drawn with Canvas2D from the same OrbDriver signals (scale,
 * lift, glow, voice pulses, ripple rings, hue leans), so the two modes look
 * like one product. Updates run in a requestAnimationFrame loop capped at
 * 30 fps; nothing re-renders React per frame.
 */
import { useEffect, useRef } from "react";
import type { AvatarInternals } from "../controller";
import { OrbDriver, RIPPLE_LIFE, type OrbSignals } from "./orbSignals";
import { orbFrame } from "./layout";
import { ORB_PALETTE_SPECS, parseOrbModel, type OrbPaletteSpec } from "./palettes";

export interface Orb2DProps {
  internals: AvatarInternals;
  modelUrl: string;
  dimmed?: boolean;
  /** A tile covers this corner: the orb sits off-centre, as in 3D. */
  occludedCorner?: "bottom-right";
  /** Render rate cap (default 30 fps). */
  maxFps?: number;
}

type RGB = [number, number, number];

function hex(h: string): RGB {
  const n = parseInt(h.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mix(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

function css(c: RGB, alpha = 1, gain = 1): string {
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v * gain)));
  return `rgba(${f(c[0])},${f(c[1])},${f(c[2])},${alpha.toFixed(3)})`;
}

/** Palette leaned toward the state's accents. */
function leaned(p: OrbPaletteSpec, s: OrbSignals) {
  const warm = hex(p.warm);
  const cool = hex(p.cool);
  const think = hex(p.think);
  const lean = (c: RGB): RGB => {
    let out = c;
    if (s.warmth > 0) out = mix(out, warm, s.warmth * 0.22);
    if (s.warmth < 0) out = mix(out, cool, -s.warmth * 0.2);
    return mix(out, think, s.violet * 0.32);
  };
  return { flow: p.flow.map((c) => lean(hex(c))) as RGB[], core: hex(p.core), rim: hex(p.rim), glow: lean(hex(p.glow)) };
}

/**
 * Colour masses inside the 2D orb: [palette flow index, orbit radius (R),
 * blob radius (R), angular speed, phase, vertical squash]. Deep and base
 * masses first, the luminous accents on top, like the 3D colour zones.
 */
const BLOBS: ReadonlyArray<readonly [number, number, number, number, number, number]> = [
  [0, 0.7, 0.72, 0.21, 0.4, 0.85],
  [1, 0.55, 0.8, -0.17, 3.5, 0.9],
  [2, 0.45, 0.85, 0.26, 1.9, 0.8],
  [2, 0.72, 0.7, -0.31, 4.9, 0.95],
  [3, 0.68, 0.55, 0.35, 5.8, 0.85],
];

/** Draw one frame of the orb into a 2D context (exported for tests). */
export function drawOrb(ctx: CanvasRenderingContext2D, width: number, height: number, palette: OrbPaletteSpec, s: OrbSignals, corner?: "bottom-right"): void {
  ctx.clearRect(0, 0, width, height);
  const frame = orbFrame(width, height, corner);
  const compact = frame.compact;
  const R = (frame.diameter * s.scale) / 2;
  const cx = frame.cx;
  const cy = frame.cy - s.lift * R;
  const c = leaned(palette, s);
  const gain = s.brightness * (1 + 0.25 * s.voice);
  const spread = compact ? 0.62 : 1;
  const phase = s.flowPhase * (0.35 + 0.9 * s.swirl);

  // Halo: the accents tint opposite sides of a soft aura.
  const glowA = 0.26 * s.glow;
  for (const [color, dx, dy] of [
    [c.flow[2]!, 0.18, -0.14],
    [c.flow[3]!, -0.16, 0.16],
    [c.glow, 0, 0],
  ] as const) {
    const ox = cx + Math.cos(phase * 0.35) * dx * R;
    const oy = cy + Math.sin(phase * 0.35) * dy * R;
    const halo = ctx.createRadialGradient(ox, oy, R * 0.92, ox, oy, R * (1 + 0.7 * spread));
    halo.addColorStop(0, css(color, glowA * s.saturation, gain));
    halo.addColorStop(0.4, css(color, glowA * 0.32, gain));
    halo.addColorStop(1, css(color, 0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, width, height);
  }

  // Ripple rings (stressed syllables).
  for (const ring of s.ripples) {
    const age = ring.age / RIPPLE_LIFE;
    const radius = R * (1.02 + ring.age * 0.5 * spread);
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.lineWidth = Math.max(1, R * (0.016 + ring.age * 0.05));
    ctx.strokeStyle = css(mix(c.rim, c.flow[2]!, 0.3), 0.32 * ring.strength * (1 - age) ** 2, gain);
    ctx.stroke();
  }

  // Body: the base colour with drifting masses of the palette colours.
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = css(mix(c.flow[1]!, c.flow[2]!, 0.35), 1, gain * 0.85);
  ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  for (const [index, orbit, size, speed, offset, squash] of BLOBS) {
    const angle = phase * speed * 3 + offset;
    const bx = cx + Math.cos(angle) * orbit * R;
    const by = cy + Math.sin(angle * 0.9 + offset * 0.3) * orbit * R * squash;
    const lum = index === 0 ? 0.62 : index === 1 ? 0.9 : 1.08;
    const g = ctx.createRadialGradient(bx, by, 0, bx, by, size * R);
    g.addColorStop(0, css(c.flow[index]!, 0.92, gain * lum));
    g.addColorStop(0.5, css(c.flow[index]!, 0.72, gain * lum));
    g.addColorStop(1, css(c.flow[index]!, 0));
    ctx.fillStyle = g;
    ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  }
  // Volume: thinner light toward the edge, a luminous heart that swells with the voice.
  const edge = ctx.createRadialGradient(cx, cy, R * 0.55, cx, cy, R);
  edge.addColorStop(0, css(c.core, 0));
  edge.addColorStop(1, css(c.core, 0.26));
  ctx.fillStyle = edge;
  ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  if (s.voice > 0.01) {
    const heart = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.75);
    heart.addColorStop(0, css(mix(c.flow[2]!, [255, 255, 255], 0.35), 0.32 * s.voice, gain));
    heart.addColorStop(1, css(c.flow[2]!, 0));
    ctx.fillStyle = heart;
    ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  }
  // Glass: a fine rim, a soft sheen and a small glint (upper left).
  const rim = ctx.createRadialGradient(cx, cy, R * 0.93, cx, cy, R);
  rim.addColorStop(0, css(c.rim, 0));
  rim.addColorStop(1, css(c.rim, 0.34 + 0.2 * s.energy, gain));
  ctx.fillStyle = rim;
  ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  const hx = cx - R * 0.36;
  const hy = cy - R * 0.4;
  const sheen = ctx.createRadialGradient(hx, hy, 0, hx, hy, R * 0.42);
  sheen.addColorStop(0, "rgba(255,252,248,0.16)");
  sheen.addColorStop(1, "rgba(255,252,248,0)");
  ctx.fillStyle = sheen;
  ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
  const glint = ctx.createRadialGradient(hx, hy, 0, hx, hy, R * 0.09);
  glint.addColorStop(0, "rgba(255,253,250,0.85)");
  glint.addColorStop(1, "rgba(255,253,250,0)");
  ctx.fillStyle = glint;
  ctx.fillRect(hx - R * 0.1, hy - R * 0.1, R * 0.2, R * 0.2);
  ctx.restore();
  if (s.saturation < 0.98) {
    // Desaturate (paused / offline) by washing toward grey.
    ctx.save();
    ctx.globalCompositeOperation = "saturation";
    ctx.fillStyle = `rgba(128,128,128,${(1 - s.saturation).toFixed(3)})`;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.02, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
}

export default function Orb2D({ internals, modelUrl, dimmed = false, occludedCorner, maxFps = 30 }: Orb2DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dimRef = useRef(dimmed);
  dimRef.current = dimmed;
  const cornerRef = useRef(occludedCorner);
  cornerRef.current = occludedCorner;

  useEffect(() => {
    internals.reportModelLoaded(`orb:${parseOrbModel(modelUrl)}`);
  }, [internals, modelUrl]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      ctx = null;
    }
    const palette = ORB_PALETTE_SPECS[parseOrbModel(modelUrl)];
    const driver = new OrbDriver();
    let raf = 0;
    let lastRender = -Infinity;
    let frames = 0;
    let since = performance.now();
    const minInterval = 1000 / Math.max(1, maxFps) - 2;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const nowMs = performance.now();
      if (nowMs - lastRender < minInterval || document.visibilityState === "hidden") return;
      lastRender = nowMs;
      try {
        const frame = internals.engine.update();
        driver.setReducedMotion(frame.motionScale < 0.99);
        const s = driver.update(frame, internals.engine.analyserAttached, dimRef.current);
        if (ctx) {
          const dpr = Math.min(1.5, typeof devicePixelRatio === "number" ? devicePixelRatio : 1);
          const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
          const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
          if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w;
            canvas.height = h;
          }
          drawOrb(ctx, w, h, palette, s, cornerRef.current);
        }
      } catch (err) {
        console.error("[avatar] orb 2D frame failed", err);
      }
      frames += 1;
      if (nowMs - since >= 1000) {
        internals.reportFps((frames * 1000) / (nowMs - since));
        frames = 0;
        since = nowMs;
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [internals, modelUrl, maxFps]);

  return <canvas ref={canvasRef} className="absolute inset-0 size-full" aria-hidden="true" data-orb-2d={parseOrbModel(modelUrl)} />;
}
