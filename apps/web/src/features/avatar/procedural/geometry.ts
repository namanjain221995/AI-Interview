/**
 * Geometry helpers for the procedural interviewer: morph target plumbing,
 * limb segments, a superellipse torso, a sculpted head, and a parametric
 * mouth whose shapes (visemes, smile, pucker...) share one topology.
 */
import {
  BufferAttribute,
  BufferGeometry,
  CapsuleGeometry,
  Float32BufferAttribute,
  Quaternion,
  SphereGeometry,
  Vector3,
} from "three";
import { clamp01, lerp, smoothstep } from "../core/math";

export interface MorphShape {
  name: string;
  positions: Float32Array;
}

/**
 * Attach morph targets (absolute target positions) as relative deltas with
 * matching normal deltas. Targets must share the geometry's topology.
 */
export function addMorphTargets(geometry: BufferGeometry, shapes: MorphShape[]): void {
  const base = geometry.getAttribute("position") as BufferAttribute;
  if (!geometry.getAttribute("normal")) geometry.computeVertexNormals();
  const baseNormal = geometry.getAttribute("normal") as BufferAttribute;
  const positions: BufferAttribute[] = [];
  const normals: BufferAttribute[] = [];
  for (const shape of shapes) {
    if (shape.positions.length !== base.array.length) throw new Error(`morph ${shape.name}: vertex count mismatch`);
    const delta = new Float32Array(shape.positions.length);
    for (let i = 0; i < delta.length; i++) delta[i] = shape.positions[i]! - (base.array[i] as number);
    const pa = new Float32BufferAttribute(delta, 3);
    pa.name = shape.name;
    positions.push(pa);

    const tmp = new BufferGeometry();
    tmp.setAttribute("position", new Float32BufferAttribute(shape.positions, 3));
    if (geometry.index) tmp.setIndex(geometry.index.clone());
    tmp.computeVertexNormals();
    const tn = tmp.getAttribute("normal") as BufferAttribute;
    const nd = new Float32Array(tn.array.length);
    for (let i = 0; i < nd.length; i++) nd[i] = (tn.array[i] as number) - (baseNormal.array[i] as number);
    const na = new Float32BufferAttribute(nd, 3);
    na.name = shape.name;
    normals.push(na);
    tmp.dispose();
  }
  geometry.morphAttributes.position = positions;
  geometry.morphAttributes.normal = normals;
  geometry.morphTargetsRelative = true;
}

/** Capsule from the origin toward `to` (bone-local), radius tapering r0 -> r1. */
export function segmentGeometry(to: Vector3, r0: number, r1 = r0, radial = 16): BufferGeometry {
  // The cylinder spans the whole bone; the hemispherical caps overlap the neighbouring joints.
  const length = to.length();
  const body = Math.max(0.0001, length);
  const g = new CapsuleGeometry(1, body, 6, radial);
  // Taper: radius interpolates along the capsule's Y axis.
  const pos = g.getAttribute("position") as BufferAttribute;
  const half = body / 2 + 1;
  for (let i = 0; i < pos.count; i++) {
    const y = pos.getY(i);
    const t = clamp01((y + half) / (2 * half));
    const r = lerp(r0, r1, t);
    // Hemispherical caps: rescale cap offsets so they stay round at each end's radius.
    const capOffset = Math.sign(y) * Math.max(0, Math.abs(y) - body / 2);
    const core = y - capOffset;
    pos.setXYZ(i, pos.getX(i) * r, core + capOffset * r, pos.getZ(i) * r);
  }
  g.translate(0, length / 2, 0);
  const q = new Quaternion().setFromUnitVectors(new Vector3(0, 1, 0), to.clone().normalize());
  g.applyQuaternion(q);
  g.computeVertexNormals();
  return g;
}

/** Catmull-Rom interpolation through scalar samples. */
function catmull(p0: number, p1: number, p2: number, p3: number, t: number): number {
  const t2 = t * t;
  const t3 = t2 * t;
  return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3);
}

export type ProfileRow = readonly [h: number, halfWidth: number, halfDepth: number, exponent: number];

function sampleProfile(rows: readonly ProfileRow[], h: number): [number, number, number] {
  let i = 0;
  while (i < rows.length - 2 && rows[i + 1]![0] < h) i++;
  const a = rows[Math.max(0, i - 1)]!;
  const b = rows[i]!;
  const c = rows[i + 1]!;
  const d = rows[Math.min(rows.length - 1, i + 2)]!;
  const t = clamp01((h - b[0]) / (c[0] - b[0]));
  return [catmull(a[1], b[1], c[1], d[1], t), catmull(a[2], b[2], c[2], d[2], t), catmull(a[3], b[3], c[3], d[3], t)];
}

const sgnPow = (v: number, p: number) => Math.sign(v) * Math.pow(Math.abs(v), p);

/**
 * Lofted superellipse body: rings follow a (height, half-width, half-depth,
 * exponent) profile. UV: u = angle / 2π (front centre at u = 0.25), v = height.
 * Returns geometry plus per-vertex heights for skinning.
 */
export function loftGeometry(
  rows: readonly ProfileRow[],
  rings: number,
  segments: number,
  capTop = true,
): { geometry: BufferGeometry; heights: Float32Array } {
  const h0 = rows[0]![0];
  const h1 = rows[rows.length - 1]![0];
  const verts: number[] = [];
  const uvs: number[] = [];
  const heights: number[] = [];
  const index: number[] = [];
  for (let r = 0; r <= rings; r++) {
    const v = r / rings;
    const h = lerp(h0, h1, v);
    const [w, d, n] = sampleProfile(rows, h);
    for (let s = 0; s <= segments; s++) {
      const u = s / segments;
      const theta = u * Math.PI * 2;
      const e = 2 / n;
      verts.push(w * sgnPow(Math.cos(theta), e), h, d * sgnPow(Math.sin(theta), e));
      uvs.push(u, v);
      heights.push(h);
    }
  }
  const stride = segments + 1;
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * stride + s;
      const b = a + stride;
      // Counter-clockwise seen from outside (normals point away from the axis).
      index.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  if (capTop) {
    const centre = verts.length / 3;
    verts.push(0, h1, 0);
    uvs.push(0.25, 1);
    heights.push(h1);
    const top = rings * stride;
    for (let s = 0; s < segments; s++) index.push(top + s, top + s + 1, centre);
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(verts, 3));
  g.setAttribute("uv", new Float32BufferAttribute(uvs, 2));
  g.setIndex(index);
  g.computeVertexNormals();
  return { geometry: g, heights: new Float32Array(heights) };
}

// ------------------------------------------------------------------ head

export interface HeadSpec {
  /** Half extents of the cranium ellipsoid. */
  a: number;
  b: number;
  c: number;
  eye: { x: number; y: number; z: number };
  mouth: { y: number };
}

export const HEAD: HeadSpec = {
  a: 0.074,
  b: 0.104,
  c: 0.092,
  eye: { x: 0.031, y: 0.022, z: 0.067 },
  mouth: { y: -0.054 },
};

/** Mouth patch boundary ellipse (mouth-centre space): half width, height above, depth below. */
export const MOUTH_PATCH = { x: 0.037, up: 0.019, down: 0.033 } as const;

/** Elliptical distance from the mouth centre (1 on the patch boundary). */
export function mouthEllipse(x: number, y: number, scale = 1): number {
  const dy = y - HEAD.mouth.y;
  const ry = (dy >= 0 ? MOUTH_PATCH.up : MOUTH_PATCH.down) * scale;
  return Math.sqrt((x / (MOUTH_PATCH.x * scale)) ** 2 + (dy / ry) ** 2);
}

/** Natural head shape (head-centre space) from a unit-sphere direction, before the muzzle. */
function sculptBase(nx: number, ny: number, nz: number, out: Vector3): Vector3 {
  const { a, b, c } = HEAD;
  let x = nx * a;
  const y = ny * b;
  let z = nz * c;
  // Jaw / chin taper, a slightly fuller cranium at the back.
  const lower = Math.max(0, -ny);
  x *= 1 - 0.28 * Math.pow(lower, 1.5) - 0.04 * Math.max(0, ny) ** 2;
  if (nz < 0) z *= 1.06;
  if (nz > 0) {
    // Flatter face plane; the lower face carried forward (an ellipsoid chin recedes).
    z *= 1 - 0.07 * nz * nz;
    z += 0.017 * smoothstep(lower / 0.75) * nz * nz * (1 - smoothstep((lower - 0.72) / 0.28));
    // Rounded chin.
    z += 0.005 * Math.exp(-((ny + 0.8) ** 2) / 0.012) * smoothstep((nz - 0.3) / 0.5);
    // Brow ridge.
    z += 0.004 * Math.exp(-((ny - 0.32) ** 2) / 0.006) * smoothstep((nz - 0.5) / 0.4);
  }
  // Cheekbones.
  const cheek = Math.exp(-((ny + 0.05) ** 2) / 0.02) * Math.exp(-((Math.abs(nx) - 0.62) ** 2) / 0.03);
  x += Math.sign(nx) * 0.004 * cheek;
  // Eye sockets (Gaussian dents) so the eyeballs sit inside the face.
  if (z > 0.03) {
    for (const side of [1, -1]) {
      const dx = x - side * HEAD.eye.x;
      const dy = y - HEAD.eye.y;
      z -= 0.0105 * Math.exp(-(dx * dx) / (2 * 0.0098 ** 2) - (dy * dy) / (2 * 0.0074 ** 2));
    }
  }
  return out.set(x, y, z);
}

const _sv = new Vector3();

/**
 * Depth of the natural (pre-muzzle) head surface at (x, y), found by
 * fixed-point inversion of sculptBase over the front of the head.
 */
export function naturalHeadZ(x: number, y: number): number {
  let nx = x / HEAD.a;
  let ny = y / HEAD.b;
  for (let i = 0; i < 8; i++) {
    const nz = Math.sqrt(Math.max(0, 1 - Math.min(0.999, nx * nx + ny * ny)));
    sculptBase(nx, ny, nz, _sv);
    nx += (x - _sv.x) / HEAD.a;
    ny += (y - _sv.y) / HEAD.b;
  }
  const nz = Math.sqrt(Math.max(0, 1 - Math.min(0.999, nx * nx + ny * ny)));
  return sculptBase(nx, ny, nz, _sv).z;
}

/** Soft protrusion of the mouth region (lips and teeth arch push the skin forward). */
function muzzle(x: number, y: number): number {
  const dy = y - HEAD.mouth.y;
  return 0.0028 * Math.exp(-(x * x) / (2 * 0.021 ** 2) - (dy * dy) / (2 * 0.017 ** 2));
}

/** Perioral skin surface depth shared by the head and the mouth patch (keeps them flush). */
export function mouthSurfaceZ(x: number, y: number): number {
  return naturalHeadZ(x, y) + muzzle(x, y);
}

/** Sculpt a unit-sphere vertex into the head (head-centre space). */
function sculptHead(p: Vector3): void {
  sculptBase(p.x, p.y, p.z, p);
  if (p.z > 0.035) p.z += muzzle(p.x, p.y);
}

/** Head mesh with an opening behind the mouth patch. */
export function headGeometry(widthSegments = 112, heightSegments = 84): BufferGeometry {
  const g = new SphereGeometry(1, widthSegments, heightSegments);
  const pos = g.getAttribute("position") as BufferAttribute;
  const v = new Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    sculptHead(v);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  g.deleteAttribute("uv");
  // Cut the mouth opening: drop front triangles inside a slightly smaller ellipse.
  const index = g.getIndex()!;
  const kept: number[] = [];
  const c = new Vector3();
  const t = new Vector3();
  for (let i = 0; i < index.count; i += 3) {
    const ia = index.getX(i);
    const ib = index.getX(i + 1);
    const ic = index.getX(i + 2);
    c.fromBufferAttribute(pos, ia).add(t.fromBufferAttribute(pos, ib)).add(t.fromBufferAttribute(pos, ic)).divideScalar(3);
    if (c.z > 0.04 && mouthEllipse(c.x, c.y) < 0.86) continue;
    kept.push(ia, ib, ic);
  }
  g.setIndex(kept);
  g.computeVertexNormals();
  return g;
}

/** Smooth interpolation through (x, y) control points (Catmull-Rom, clamped ends). */
function curveAt(points: ReadonlyArray<readonly [number, number]>, x: number): number {
  if (x <= points[0]![0]) return points[0]![1];
  const last = points[points.length - 1]!;
  if (x >= last[0]) return last[1];
  let i = 0;
  while (points[i + 1]![0] < x) i++;
  const p0 = points[Math.max(0, i - 1)]![1];
  const p1 = points[i]![1];
  const p2 = points[i + 1]![1];
  const p3 = points[Math.min(points.length - 1, i + 2)]![1];
  const t = (x - points[i]![0]) / (points[i + 1]![0] - points[i]![0]);
  return catmull(p0, p1, p2, p3, t);
}

/** Hairline height (unit-sphere y) by |azimuth| from the front: forehead, temples, sideburns, around the ears, nape. */
const HAIRLINE: ReadonlyArray<readonly [number, number]> = [
  [0, 0.53],
  [0.45, 0.47],
  [0.85, 0.36],
  [1.12, 0.06],
  [1.32, -0.06],
  [1.5, 0.3],
  [1.72, 0.3],
  [1.95, 0.0],
  [2.4, -0.36],
  [Math.PI, -0.52],
];

/** Hair shell over the cranium; vertices below the hairline tuck inside the head. */
export function hairGeometry(): BufferGeometry {
  const g = new SphereGeometry(1, 120, 90);
  const pos = g.getAttribute("position") as BufferAttribute;
  const v = new Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    const az = Math.atan2(v.x, v.z); // 0 front, ±π back
    const front = Math.max(0, Math.cos(az));
    const back = Math.max(0, -Math.cos(az));
    let line = curveAt(HAIRLINE, Math.abs(az));
    // Side part on the character's left with a soft sweep of the fringe.
    line += 0.04 * Math.exp(-((v.x - 0.3) ** 2) / 0.008) * front;
    const cover = smoothstep((v.y - line) / 0.085);
    // Volume: fuller crown and a fringe swept toward the character's right.
    const sweep = 0.035 * Math.exp(-((az + 0.35) ** 2) / 0.25) * smoothstep((v.y - 0.3) / 0.4);
    const crown = 1 + 0.05 * smoothstep(v.y) + 0.025 * back + sweep;
    const r = lerp(0.92, 1.065 * crown, cover);
    const x = v.x * HEAD.a * r * (1 - 0.18 * Math.max(0, -v.y) ** 1.5);
    const y = v.y * HEAD.b * r + 0.004;
    const z = v.z * HEAD.c * (v.z < 0 ? 1.06 : 0.98) * r - 0.003;
    pos.setXYZ(i, x, y, z);
  }
  g.computeVertexNormals();
  return g;
}

// ------------------------------------------------------------------ mouth

export interface MouthParams {
  /** Total opening at the centre (m). */
  open: number;
  /** Share of the opening taken by the upper lip (the rest moves the lower lip / jaw). */
  upShare: number;
  liftL: number;
  liftR: number;
  widenL: number;
  widenR: number;
  pucker: number;
  pressL: number;
  pressR: number;
  /** Lower lip tucked under the upper teeth (FF). */
  tuck: number;
}

export const NEUTRAL_MOUTH: MouthParams = {
  open: 0.0002,
  upShare: 0.3,
  liftL: 0,
  liftR: 0,
  widenL: 0,
  widenR: 0,
  pucker: 0,
  pressL: 0,
  pressR: 0,
  tuck: 0,
};

/** Points per closed contour loop (upper half M/2 + 1 incl. corners, lower half shares the corners). */
export const MOUTH_LOOP = 48;
const LIP_LEVELS = 5;
const SKIN_LEVELS = 5;
const CAVITY_LEVELS = 5;
const TEETH_COLS = 24;

export interface MouthBuffers {
  /** Skin ring + lips, one closed loop per level (inner lip line -> patch boundary). */
  patch: Float32Array;
  cavity: Float32Array;
  teeth: Float32Array;
}

interface LoopPoint {
  x: number;
  yInner: number;
  yOuter: number;
  zOffset: number;
  ellipseX: number;
  ellipseY: number;
  upper: boolean;
  u: number;
}

function contour(p: MouthParams, u: number, upper: boolean) {
  const W = 0.0235;
  const left = u > 0;
  const au = Math.min(1, Math.abs(u));
  const widen = left ? p.widenL : p.widenR;
  const lift = left ? p.liftL : p.liftR;
  const press = left ? p.pressL : p.pressR;
  const halfW = W * (1 - 0.32 * p.pucker) + widen * au;
  const g = Math.pow(Math.max(0, 1 - u * u), 0.75);
  const corner = lift * au ** 3;
  const open = Math.max(0, p.open);
  const zPuck = 0.008 * p.pucker * (1 - u * u);
  if (upper) {
    const yInner = open * p.upShare * g + corner - p.tuck * 0.0004;
    const cupid = -0.0011 * Math.exp(-((u / 0.12) ** 2)) + 0.0007 * Math.exp(-(((au - 0.24) / 0.1) ** 2));
    const h = 0.0063 * (1 - 0.45 * press) * (0.3 + 0.7 * Math.sqrt(Math.max(0, 1 - u * u))) * (1 + 0.15 * p.pucker);
    return { x: halfW * u, yInner, yOuter: yInner + h + cupid * (1 - au), zPuck, tuck: 0 };
  }
  const yInner = -open * (1 - p.upShare) * g + corner + p.tuck * 0.003 * g;
  const h = 0.0078 * (1 - 0.4 * press) * (0.26 + 0.74 * Math.pow(Math.max(0, 1 - u * u), 0.6)) * (1 + 0.12 * p.pucker);
  return { x: halfW * u, yInner, yOuter: yInner - h + p.tuck * 0.002 * g, zPuck, tuck: -0.0045 * p.tuck * g };
}

function loopPoints(p: MouthParams): LoopPoint[] {
  const M = MOUTH_LOOP;
  const half = M / 2;
  const pts: LoopPoint[] = [];
  for (let k = 0; k < M; k++) {
    const upper = k <= half;
    // Upper: u from +1 (character's left corner) to -1; lower: back from -1 to +1.
    const u = upper ? 1 - (2 * k) / half : -1 + (2 * (k - half)) / half;
    const phi = upper ? (Math.PI * k) / half : Math.PI + (Math.PI * (k - half)) / half;
    let c = contour(p, u, upper);
    if (k === 0 || k === half) {
      // Corners are shared by both lips: average the two outer contours.
      const lo = contour(p, u, false);
      c = { ...c, yOuter: (c.yOuter + lo.yOuter) / 2, yInner: (c.yInner + lo.yInner) / 2 };
    }
    const ry = Math.sin(phi) >= 0 ? MOUTH_PATCH.up : MOUTH_PATCH.down;
    pts.push({
      x: c.x,
      yInner: c.yInner,
      yOuter: c.yOuter,
      zOffset: c.zPuck + c.tuck,
      ellipseX: MOUTH_PATCH.x * Math.cos(phi),
      ellipseY: ry * Math.sin(phi),
      upper,
      u,
    });
  }
  return pts;
}

/** Evaluate the mouth patch, cavity and teeth for one parameter set (fixed topology). */
export function mouthPositions(p: MouthParams): MouthBuffers {
  const cy = HEAD.mouth.y;
  const pts = loopPoints(p);
  const patch: number[] = [];
  // Lip cross-section: curls in at the lip line, fullest past the middle, meets the skin at the border.
  const lipBulge = (t: number) => 0.0006 + 0.0028 * Math.sin(Math.PI * (0.08 + 0.8 * t)) * (1 - 0.6 * t * t);
  for (let l = 0; l < LIP_LEVELS; l++) {
    const t = l / (LIP_LEVELS - 1);
    for (const q of pts) {
      const y = lerp(q.yInner, q.yOuter, t);
      const fade = 1 - t * 0.6;
      const z = mouthSurfaceZ(q.x, cy + y) + lipBulge(t) * (q.upper ? 1 : 1.08) + q.zOffset * fade;
      patch.push(q.x, cy + y, z);
    }
  }
  for (let s = 1; s <= SKIN_LEVELS; s++) {
    const t = s / SKIN_LEVELS;
    const e = t * t * (3 - 2 * t) * 0.7 + t * 0.3;
    for (const q of pts) {
      const x = lerp(q.x, q.ellipseX, e);
      const y = lerp(q.yOuter, q.ellipseY, e);
      // Flush with the skin (a hair proud, covering the head's cut edge), then
      // tucked just behind the head where the two overlap at the boundary.
      const border = lipBulge(1) + q.zOffset * 0.4;
      const offset = e < 0.8 ? lerp(border, 0.0002, e / 0.8) : lerp(0.0002, -0.0015, (e - 0.8) / 0.2);
      const z = mouthSurfaceZ(x, cy + y) + offset;
      patch.push(x, cy + y, z);
    }
  }

  // Cavity: from the lip line inward to the mid line, receding into the mouth.
  const cavity: number[] = [];
  for (let l = 0; l < CAVITY_LEVELS; l++) {
    const t = l / (CAVITY_LEVELS - 1);
    for (const q of pts) {
      const mate = contour(p, q.u, !q.upper);
      const mid = (q.yInner + mate.yInner) / 2;
      const y = lerp(q.yInner, mid, t);
      const x = q.x * (1 - 0.12 * t);
      const z = mouthSurfaceZ(q.x, cy + q.yInner) + 0.0004 + q.zOffset - 0.013 * Math.sin((Math.PI / 2) * t);
      cavity.push(x, cy + y, z);
    }
  }

  // Teeth: upper band hangs behind the upper lip, lower band rises behind the lower lip.
  const teeth: number[] = [];
  const band = (upper: boolean, row: 0 | 1) => {
    for (let i = 0; i < TEETH_COLS; i++) {
      const u = -0.92 + (1.84 * i) / (TEETH_COLS - 1);
      const c = contour(p, u, upper);
      const tx = c.x * 0.86;
      const curve = 0.0045 * u * u;
      const base = mouthSurfaceZ(tx, cy + c.yInner) - (upper ? 0.0026 : 0.0032) + c.zPuck * 0.4 + c.tuck * 0.3 - curve;
      const len = (upper ? 0.0058 : 0.0036) * (1 - 0.45 * u * u);
      const y = upper ? (row === 0 ? c.yInner + 0.0025 : c.yInner - len) : row === 0 ? c.yInner + len : c.yInner - 0.0025;
      teeth.push(tx, cy + y, base - (row === 1 && upper ? 0.0004 : 0) - (row === 0 && !upper ? 0.0004 : 0));
    }
  };
  band(true, 0);
  band(true, 1);
  band(false, 0);
  band(false, 1);

  return { patch: new Float32Array(patch), cavity: new Float32Array(cavity), teeth: new Float32Array(teeth) };
}

function loopIndex(levels: number, M: number, flip: boolean): number[] {
  const index: number[] = [];
  for (let l = 0; l < levels - 1; l++) {
    for (let k = 0; k < M; k++) {
      const a = l * M + k;
      const b = l * M + ((k + 1) % M);
      const c = a + M;
      const d = b + M;
      if (flip) index.push(a, c, b, b, c, d);
      else index.push(a, b, c, b, d, c);
    }
  }
  return index;
}

function stripIndex(rows: number, cols: number, rowOffset: number): number[] {
  const index: number[] = [];
  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < cols - 1; c++) {
      const a = (rowOffset + r) * cols + c;
      const b = a + cols;
      index.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  return index;
}

/** Per-vertex colours for the patch: lip colour on the lips, skin outside, soft border. */
export function mouthPatchColors(lip: [number, number, number], skin: [number, number, number]): Float32Array {
  const M = MOUTH_LOOP;
  const levels = LIP_LEVELS + SKIN_LEVELS;
  const out = new Float32Array(levels * M * 3);
  for (let l = 0; l < levels; l++) {
    // Lip levels 0..4 (4 = vermilion border), skin from 5.
    const t = l <= LIP_LEVELS - 2 ? 0 : l === LIP_LEVELS - 1 ? 0.35 : l === LIP_LEVELS ? 0.85 : 1;
    for (let k = 0; k < M; k++) {
      const o = (l * M + k) * 3;
      out[o] = lerp(lip[0], skin[0], t);
      out[o + 1] = lerp(lip[1], skin[1], t);
      out[o + 2] = lerp(lip[2], skin[2], t);
    }
  }
  return out;
}

export function mouthGeometries(base: MouthBuffers): { patch: BufferGeometry; cavity: BufferGeometry; teeth: BufferGeometry } {
  const M = MOUTH_LOOP;
  const make = (arr: Float32Array, index: number[]) => {
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(arr, 3));
    g.setIndex(index);
    g.computeVertexNormals();
    return g;
  };
  return {
    // Patch levels grow outward, cavity levels inward: opposite windings, both facing +Z.
    patch: make(base.patch, loopIndex(LIP_LEVELS + SKIN_LEVELS, M, true)),
    cavity: make(base.cavity, loopIndex(CAVITY_LEVELS, M, false)),
    teeth: make(base.teeth, [...stripIndex(2, TEETH_COLS, 0), ...stripIndex(2, TEETH_COLS, 2)]),
  };
}

/** Eyelid shell: a sphere segment over the front of the eye from polar angle thetaStart. */
export function lidPositions(radius: number, thetaStart: number, thetaLength: number, widthSegments = 28, heightSegments = 10): Float32Array {
  const g = lidGeometry(radius, thetaStart, thetaLength, widthSegments, heightSegments);
  const out = new Float32Array((g.getAttribute("position") as BufferAttribute).array as Float32Array);
  g.dispose();
  return out;
}

export function lidGeometry(radius: number, thetaStart: number, thetaLength: number, widthSegments = 28, heightSegments = 10): BufferGeometry {
  // Sphere cap around +Y rotated so the shell wraps the front (+Z) of the eye.
  return new SphereGeometry(radius, widthSegments, heightSegments, Math.PI * 0.04, Math.PI * 0.92, thetaStart, thetaLength);
}
