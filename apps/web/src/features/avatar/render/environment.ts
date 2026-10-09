/**
 * Environment helpers: WebGL capability probe and procedural backdrops
 * (painted studio canvas, blurred office impression, plain). Everything is
 * generated in code; no external images or HDRs. No three.js imports here:
 * the stage uses this module before the 3D chunk is loaded.
 *
 * The backdrops are neutral slate so the warm skin and the navy blazer read
 * against them; a light pool sits behind the head and shoulders and the edges
 * fall off into the stage navy of the interview room.
 */
import type { AvatarConfig } from "../types";

export type BackdropKind = NonNullable<AvatarConfig["background"]>;

let webglProbe: boolean | null = null;

/**
 * True when a WebGL (2 or 1) context can be created. Cached; `fresh` probes
 * again (e.g. after a context loss, to see whether the GPU is back). Never throws.
 */
export function isWebGLAvailable(fresh = false): boolean {
  if (webglProbe !== null && !fresh) return webglProbe;
  try {
    if (typeof document === "undefined" || typeof WebGLRenderingContext === "undefined") {
      webglProbe = false;
      return false;
    }
    const canvas = document.createElement("canvas");
    const gl = (canvas.getContext("webgl2") ?? canvas.getContext("webgl")) as WebGLRenderingContext | null;
    webglProbe = !!gl;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    webglProbe = false;
  }
  return webglProbe;
}

export const BACKDROP_COLORS: Record<BackdropKind, { base: string; glow: string; edge: string }> = {
  studio: { base: "#2d333d", glow: "#5c6168", edge: "#121726" },
  office: { base: "#30353f", glow: "#5a5f69", edge: "#141a27" },
  plain: { base: "#262c38", glow: "#323946", edge: "#171c29" },
};

/** Seeded PRNG so the painted backdrop is identical on every load. */
function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Paint the backdrop into a 2D context (shared by the 3D texture and tests). */
export function paintBackdrop(ctx: CanvasRenderingContext2D, width: number, height: number, kind: BackdropKind): void {
  const c = BACKDROP_COLORS[kind];
  const size = Math.max(width, height);
  ctx.fillStyle = c.base;
  ctx.fillRect(0, 0, width, height);
  if (kind === "studio") {
    // Painted canvas: large, very soft light and dark patches (hand-mottled muslin).
    const rand = seededRandom(20261007);
    for (let i = 0; i < 34; i++) {
      const x = rand() * width;
      const y = rand() * height;
      const r = (0.1 + rand() * 0.22) * size;
      const light = rand() < 0.5;
      const a = 0.025 + rand() * 0.045;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, light ? `rgba(232,226,218,${a})` : `rgba(8,10,16,${a * 1.3})`);
      g.addColorStop(1, light ? "rgba(232,226,218,0)" : "rgba(8,10,16,0)");
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
    }
  }
  // Light pool behind the head and shoulders, wider than tall, falling off to the edges.
  ctx.save();
  ctx.translate(width * 0.5, height * 0.4);
  ctx.scale(1.35, 1);
  const pool = ctx.createRadialGradient(0, 0, 0, 0, 0, size * 0.46);
  pool.addColorStop(0, c.glow);
  pool.addColorStop(0.5, c.base);
  pool.addColorStop(1, "rgba(0,0,0,0)");
  ctx.globalAlpha = 0.9;
  ctx.fillStyle = pool;
  ctx.fillRect(-size, -size, size * 2, size * 2);
  ctx.restore();
  const vignette = ctx.createRadialGradient(width * 0.5, height * 0.42, size * 0.3, width * 0.5, height * 0.42, size * 0.72);
  vignette.addColorStop(0, "rgba(0,0,0,0)");
  vignette.addColorStop(1, c.edge);
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, width, height);

  if (kind === "office") {
    const blob = (x: number, y: number, r: number, color: string) => {
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, color);
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
    };
    // Window light (cool) on the left, warm lamp and shelf glints on the right, all
    // defocused: every shape is a soft-edged gradient, no hard lines.
    ctx.save();
    ctx.translate(width * 0.16, height * 0.38);
    ctx.scale(1, 2.4);
    const r = width * 0.15;
    const win = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
    win.addColorStop(0, "rgba(175,198,232,0.26)");
    win.addColorStop(0.6, "rgba(175,198,232,0.12)");
    win.addColorStop(1, "rgba(175,198,232,0)");
    ctx.fillStyle = win;
    ctx.fillRect(-r, -r, 2 * r, 2 * r); // cover the whole gradient: no visible rect edge
    ctx.restore();
    blob(width * 0.86, height * 0.3, width * 0.12, "rgba(255,196,140,0.35)");
    blob(width * 0.72, height * 0.62, width * 0.06, "rgba(255,214,170,0.18)");
    blob(width * 0.93, height * 0.66, width * 0.05, "rgba(200,220,255,0.12)");
    const floor = ctx.createLinearGradient(0, height * 0.68, 0, height);
    floor.addColorStop(0, "rgba(10,12,16,0)");
    floor.addColorStop(1, "rgba(10,12,16,0.24)");
    ctx.fillStyle = floor;
    ctx.fillRect(0, height * 0.68, width, height * 0.32);
  }

  // Fine noise dithering prevents banding in the dark gradient.
  const image = ctx.getImageData(0, 0, width, height);
  const data = image.data;
  let seed = 1234567;
  for (let i = 0; i < data.length; i += 4) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const n = ((seed >>> 24) / 255 - 0.5) * 3;
    data[i] = clampByte(data[i]! + n);
    data[i + 1] = clampByte(data[i + 1]! + n);
    data[i + 2] = clampByte(data[i + 2]! + n);
  }
  ctx.putImageData(image, 0, 0);
}

function clampByte(v: number): number {
  return v < 0 ? 0 : v > 255 ? 255 : v;
}
