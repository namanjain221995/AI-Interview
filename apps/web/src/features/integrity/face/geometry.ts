/**
 * Geometry for the on-device face check. Boxes are normalised to the analysed
 * frame (0-1, origin top-left, NOT mirrored); the camera preview is mirrored
 * with CSS, so overlays mirror x when they draw.
 */

export interface FaceBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Point {
  x: number;
  y: number;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function iou(a: FaceBox, b: FaceBox): number {
  const ix = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const iy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  const inter = ix * iy;
  const union = a.w * a.h + b.w * b.h - inter;
  return union > 0 ? inter / union : 0;
}

export function center(box: FaceBox): Point {
  return { x: box.x + box.w / 2, y: box.y + box.h / 2 };
}

export function area(box: FaceBox): number {
  return Math.max(0, box.w) * Math.max(0, box.h);
}

/** The part of the box inside the frame (null when it lies completely outside). */
export function clipBox(box: FaceBox): FaceBox | null {
  const x0 = clamp(box.x, 0, 1);
  const y0 = clamp(box.y, 0, 1);
  const x1 = clamp(box.x + box.w, 0, 1);
  const y1 = clamp(box.y + box.h, 0, 1);
  if (x1 <= x0 || y1 <= y0) return null;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Box grown by `fraction` of its size on every side (clipped to the frame). */
export function expandBox(box: FaceBox, fraction: number): FaceBox {
  const dx = box.w * fraction;
  const dy = box.h * fraction;
  return clipBox({ x: box.x - dx, y: box.y - dy, w: box.w + 2 * dx, h: box.h + 2 * dy }) ?? box;
}

export function roundBox(box: FaceBox, digits = 3): [number, number, number, number] {
  const f = 10 ** digits;
  return [Math.round(box.x * f) / f, Math.round(box.y * f) / f, Math.round(box.w * f) / f, Math.round(box.h * f) / f];
}

export interface CoverLayout {
  /** Displayed (scaled) video size in CSS pixels. */
  width: number;
  height: number;
  /** Offset of the displayed video inside the container (negative when cropped). */
  offsetX: number;
  offsetY: number;
}

/** Where an `object-fit: cover` video lands inside its container. */
export function coverLayout(containerW: number, containerH: number, videoW: number, videoH: number): CoverLayout {
  if (!(containerW > 0 && containerH > 0 && videoW > 0 && videoH > 0)) return { width: containerW, height: containerH, offsetX: 0, offsetY: 0 };
  const scale = Math.max(containerW / videoW, containerH / videoH);
  const width = videoW * scale;
  const height = videoH * scale;
  return { width, height, offsetX: (containerW - width) / 2, offsetY: (containerH - height) / 2 };
}

/**
 * Position of a normalised box over a cover-fitted video, in percent of the
 * container (so the overlay scales with it). `mirrored` matches a preview
 * flipped with `scale-x: -1`.
 */
export function overlayRect(
  box: FaceBox,
  container: { width: number; height: number },
  video: { width: number; height: number },
  mirrored: boolean,
): { left: number; top: number; width: number; height: number } {
  const layout = coverLayout(container.width, container.height, video.width, video.height);
  const x = layout.offsetX + box.x * layout.width;
  const y = layout.offsetY + box.y * layout.height;
  const w = box.w * layout.width;
  const h = box.h * layout.height;
  const left = mirrored ? container.width - x - w : x;
  const pct = (value: number, total: number) => (total > 0 ? (value / total) * 100 : 0);
  return { left: pct(left, container.width), top: pct(y, container.height), width: pct(w, container.width), height: pct(h, container.height) };
}
