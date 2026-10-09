/**
 * Interviewer presence palettes ("orb:<palette>"). Shared by the 3D orb, the
 * 2D fallback and the admin settings; no three.js here.
 *
 * Each palette is four flow colours (the inner aurora, from deep to bright),
 * a core colour (the glassy depth), a rim colour (fresnel glow) and the warm /
 * cool / thinking accents the states lean toward.
 */

export const ORB_PALETTES = ["aurora", "sunrise", "ocean"] as const;
export type OrbPalette = (typeof ORB_PALETTES)[number];
export const DEFAULT_ORB_PALETTE: OrbPalette = "aurora";

export interface OrbPaletteSpec {
  id: OrbPalette;
  /** Staff-facing label and one-line description. */
  label: string;
  description: string;
  /** sRGB hex: inner flow from deep to bright (4 stops). */
  flow: readonly [string, string, string, string];
  core: string;
  rim: string;
  /** Accent the speaking / acknowledging states warm toward. */
  warm: string;
  /** Accent the listening state cools toward. */
  cool: string;
  /** Accent the thinking state leans toward. */
  think: string;
  /** Halo tint. */
  glow: string;
}

export const ORB_PALETTE_SPECS: Record<OrbPalette, OrbPaletteSpec> = {
  aurora: {
    id: "aurora",
    label: "Aurora",
    description: "Teal, indigo and violet with a touch of pink.",
    flow: ["#26299a", "#6447e0", "#2fd8c8", "#ff92c8"],
    core: "#232f86",
    rim: "#8fe9ff",
    warm: "#f39ac8",
    cool: "#5fe3e0",
    think: "#9a6bff",
    glow: "#6f7cff",
  },
  sunrise: {
    id: "sunrise",
    label: "Sunrise",
    description: "Amber, coral and rose.",
    flow: ["#5e1745", "#d33d6c", "#ff8a4c", "#ffd27a"],
    core: "#3d1236",
    rim: "#ffe0b0",
    warm: "#ffb15e",
    cool: "#ff8f9f",
    think: "#d65fd0",
    glow: "#ff8a6a",
  },
  ocean: {
    id: "ocean",
    label: "Ocean",
    description: "Cyan and blue over deep navy.",
    flow: ["#0b1d66", "#2146c8", "#1fcdf5", "#a8fff0"],
    core: "#0a1a58",
    rim: "#a6f4ff",
    warm: "#7fd8ff",
    cool: "#3ff0e0",
    think: "#6b7dff",
    glow: "#3f8dff",
  },
};

/** "orb:sunrise" -> "sunrise"; anything else (including unknown palettes) -> the default. */
export function parseOrbModel(modelUrl: string | null | undefined): OrbPalette {
  const m = /^orb:([a-z]+)$/.exec((modelUrl ?? "").trim());
  return m && (ORB_PALETTES as readonly string[]).includes(m[1]!) ? (m[1] as OrbPalette) : DEFAULT_ORB_PALETTE;
}

export function orbModelUrl(palette: OrbPalette): string {
  return `orb:${palette}`;
}

/** sRGB hex -> linear RGB triple. */
export function hexToLinear(hex: string): [number, number, number] {
  const n = parseInt(hex.replace("#", ""), 16);
  const c = (v: number) => {
    const s = v / 255;
    return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return [c((n >> 16) & 255), c((n >> 8) & 255), c(n & 255)];
}
