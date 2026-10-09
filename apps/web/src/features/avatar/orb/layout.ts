/**
 * Where the presence orb sits on its stage; shared by the 3D orb and the 2D
 * fallback so both modes frame it identically.
 */

/** Sphere diameter as a share of the stage height (the bust-framing slot). */
export const STAGE_SHARE = 0.43;
/** ...bounded by this share of the width on narrow (portrait) stages. */
export const WIDTH_SHARE = 0.68;
/** Compact stages (coding corner, system-check tile): share of the smaller side. */
export const COMPACT_SHARE = 0.56;
/** Stages smaller than this (px, either side) are compact: lighter mesh, tighter halo. */
export const COMPACT_PX = 240;
/**
 * With a tile over the bottom-right corner (the candidate's camera in the
 * coding corner) the orb moves up and left by these shares of the stage.
 */
export const ORB_CORNER_SHIFT = { x: 0.09, y: 0.05 } as const;

/** Orb centre (px) and diameter share for a stage of this size. */
export function orbFrame(width: number, height: number, corner?: "bottom-right"): { cx: number; cy: number; compact: boolean; diameter: number } {
  const compact = Math.min(width, height) < COMPACT_PX;
  const aspect = width / Math.max(1, height);
  const diameter = compact ? COMPACT_SHARE * Math.min(width, height) : Math.min(STAGE_SHARE, WIDTH_SHARE * aspect) * height;
  const shift = corner ? ORB_CORNER_SHIFT : { x: 0, y: 0 };
  return { cx: width * (0.5 - shift.x), cy: height * (0.5 - shift.y), compact, diameter };
}
