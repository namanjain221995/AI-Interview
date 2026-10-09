#!/usr/bin/env node
/**
 * Optimizes the interviewer avatar for the web.
 *
 *   node scripts/optimize-avatar.mjs [--in <src.glb>] [--out <dst.glb>] [--max-mb 8]
 *        [--blazer-color #303a50] [--shirt-color #d9dee6] [--trouser-color #2b2e34]
 *        [--brow-color #4a3427] [--keep-wardrobe] [--debug <dir>] [--paint-only]
 *
 * Defaults: public/avatars/source/mpfb.glb -> public/avatars/interviewer.glb.
 * (--top-color / --bottom-color are accepted as aliases of --blazer-color /
 * --trouser-color.)
 *
 * Restyle (texture work only; the geometry is untouched). Every paint pass
 * works in 3D: each mesh is rasterized into its UV atlas so every texel knows
 * its bind-pose position and normal, and regions are defined on the body.
 *
 *   wardrobe  the source outfit is a logo T-shirt and jeans. The T-shirt
 *             becomes a navy blazer over a light shirt: a V of shirt front with
 *             a placket and buttons, lapels with a lit fold and an outer
 *             crease, a shirt collar band and spread collar points, baked
 *             contact shadows; the jeans become charcoal trousers. The folds
 *             baked into the fabric are kept, the logo and trims removed.
 *   skin      gentle warm grade; soft painted brows derived from the strand
 *             brow geometry (every strand vertex is projected onto the skin
 *             and splatted, so the painted brow has the strands' shape and
 *             density); a soft lash line from the lash roots; long blazer
 *             sleeves with a shirt cuff on the visible arms (plus a roughness
 *             map so the painted sleeves shade as matte fabric, not skin); a
 *             contact shadow on the neck above the collar.
 *   eyes      the saturated red-brown iris is toned to a warm brown.
 *
 * Then dedup (distinctly named materials kept apart) -> prune -> weld ->
 * per-texture resize + WebP -> meshopt
 * (reorder + quantize + EXT_meshopt_compression). The skeleton, skin weights,
 * inverse bind matrices and every morph target (with its name in
 * mesh.extras.targetNames) are preserved; the script re-reads the output and
 * fails (exit 1) if any of them changed or the file exceeds the size budget.
 * --debug <dir> writes the painted textures as PNG; --paint-only stops there.
 */
import { existsSync, mkdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { dedup, meshopt, prune, textureCompress, weld } from "@gltf-transform/functions";
import { MeshoptDecoder, MeshoptEncoder } from "meshoptimizer";
import sharp from "sharp";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "..");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const input = path.resolve(webRoot, arg("in", "public/avatars/source/mpfb.glb"));
const output = path.resolve(webRoot, arg("out", "public/avatars/interviewer.glb"));
const maxBytes = Number(arg("max-mb", "8")) * 1024 * 1024;
const COLORS = {
  blazer: arg("blazer-color", arg("top-color", "#303a50")),
  shirt: arg("shirt-color", "#d9dee6"),
  trousers: arg("trouser-color", arg("bottom-color", "#2b2e34")),
  brow: arg("brow-color", "#4a3427"),
  lash: "#2a1d17",
};
const keepWardrobe = process.argv.includes("--keep-wardrobe");
const debugDir = arg("debug", null) ? path.resolve(process.cwd(), arg("debug", "")) : null;
const paintOnly = process.argv.includes("--paint-only");

// ------------------------------------------------------------------ colour + image helpers

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) throw new Error(`invalid colour ${hex}`);
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const SRGB_TO_LINEAR = new Float32Array(256).map((_, i) => {
  const c = i / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
});

function linearToSrgbByte(v) {
  const c = v <= 0 ? 0 : v >= 1 ? 1 : v;
  const s = c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055;
  return Math.round(s * 255);
}

const hexToLinear = (hex) => hexToRgb(hex).map((c) => SRGB_TO_LINEAR[c]);
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smoothstep = (a, b, v) => {
  const t = clamp01((v - a) / (b - a));
  return t * t * (3 - 2 * t);
};

/** Decode a texture to linear RGB floats + its alpha bytes. */
async function decodeLinear(texture) {
  const { data, info } = await sharp(Buffer.from(texture.getImage())).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const W = info.width;
  const H = info.height;
  const rgb = new Float32Array(W * H * 3);
  const alpha = new Uint8Array(W * H);
  for (let i = 0; i < W * H; i++) {
    rgb[i * 3] = SRGB_TO_LINEAR[data[i * 4]];
    rgb[i * 3 + 1] = SRGB_TO_LINEAR[data[i * 4 + 1]];
    rgb[i * 3 + 2] = SRGB_TO_LINEAR[data[i * 4 + 2]];
    alpha[i] = data[i * 4 + 3];
  }
  return { rgb, alpha, W, H, srgb: data };
}

async function encodeInto(texture, rgb, alpha, W, H, debugName) {
  const out = Buffer.alloc(W * H * 4);
  for (let i = 0; i < W * H; i++) {
    out[i * 4] = linearToSrgbByte(rgb[i * 3]);
    out[i * 4 + 1] = linearToSrgbByte(rgb[i * 3 + 1]);
    out[i * 4 + 2] = linearToSrgbByte(rgb[i * 3 + 2]);
    out[i * 4 + 3] = alpha[i];
  }
  const png = await sharp(out, { raw: { width: W, height: H, channels: 4 } }).png().toBuffer();
  texture.setImage(new Uint8Array(png));
  texture.setMimeType("image/png");
  if (debugDir) await sharp(png).toFile(path.join(debugDir, `${debugName}.png`));
}

/** Separable box min-filter (dilates low values of a [0,1] map). */
function minFilter(map, W, H, radius) {
  const tmp = new Float32Array(map.length);
  const out = new Float32Array(map.length);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let m = 1;
      for (let k = -radius; k <= radius; k++) {
        const v = map[y * W + Math.min(W - 1, Math.max(0, x + k))];
        if (v < m) m = v;
      }
      tmp[y * W + x] = m;
    }
  }
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let m = 1;
      for (let k = -radius; k <= radius; k++) {
        const v = tmp[Math.min(H - 1, Math.max(0, y + k)) * W + x];
        if (v < m) m = v;
      }
      out[y * W + x] = m;
    }
  }
  return out;
}

/** Separable box blur of a float map. */
function boxBlur(map, W, H, radius) {
  const tmp = new Float32Array(map.length);
  const out = new Float32Array(map.length);
  const n = radius * 2 + 1;
  for (let y = 0; y < H; y++) {
    let acc = 0;
    for (let k = -radius; k <= radius; k++) acc += map[y * W + Math.min(W - 1, Math.max(0, k))];
    for (let x = 0; x < W; x++) {
      tmp[y * W + x] = acc / n;
      acc += map[y * W + Math.min(W - 1, x + radius + 1)] - map[y * W + Math.max(0, x - radius)];
    }
  }
  for (let x = 0; x < W; x++) {
    let acc = 0;
    for (let k = -radius; k <= radius; k++) acc += tmp[Math.min(H - 1, Math.max(0, k)) * W + x];
    for (let y = 0; y < H; y++) {
      out[y * W + x] = acc / n;
      acc += tmp[Math.min(H - 1, y + radius + 1) * W + x] - tmp[Math.max(0, y - radius) * W + x];
    }
  }
  return out;
}

/** Gaussian blur of a float map inside a pixel rectangle (the rest is copied). */
function gaussianBlurRect(map, W, H, sigma, rect) {
  const r = Math.ceil(sigma * 3);
  const kernel = new Float32Array(r * 2 + 1).map((_, i) => Math.exp(-((i - r) ** 2) / (2 * sigma * sigma)));
  const ksum = kernel.reduce((a, b) => a + b, 0);
  const x0 = Math.max(0, rect.x0 - r);
  const x1 = Math.min(W - 1, rect.x1 + r);
  const y0 = Math.max(0, rect.y0 - r);
  const y1 = Math.min(H - 1, rect.y1 + r);
  const tmp = new Float32Array(map.length);
  const out = Float32Array.from(map);
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      let acc = 0;
      for (let k = -r; k <= r; k++) acc += map[y * W + Math.min(W - 1, Math.max(0, x + k))] * kernel[k + r];
      tmp[y * W + x] = acc / ksum;
    }
  }
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      let acc = 0;
      for (let k = -r; k <= r; k++) acc += tmp[Math.min(H - 1, Math.max(0, y + k)) * W + x] * kernel[k + r];
      out[y * W + x] = acc / ksum;
    }
  }
  return out;
}

function rgbToHsv(r, g, b) {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d > 0) {
    if (max === r) h = (60 * ((g - b) / d) + 360) % 360;
    else if (max === g) h = 60 * ((b - r) / d) + 120;
    else h = 60 * ((r - g) / d) + 240;
  }
  return [h, max > 0 ? d / max : 0, max];
}

function hsvToRgb(h, s, v) {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [r + m, g + m, b + m];
}

/** Deterministic value noise in [-1, 1] on a 3D point (fabric texture). */
function hash3(x, y, z) {
  let h = Math.imul(x | 0, 0x8da6b343) ^ Math.imul(y | 0, 0xd8163841) ^ Math.imul(z | 0, 0xcb1ab31f);
  h = Math.imul(h ^ (h >>> 13), 0x5bd1e995);
  return ((h ^ (h >>> 15)) >>> 0) / 4294967296;
}

function valueNoise3(x, y, z) {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const zi = Math.floor(z);
  const fx = x - xi;
  const fy = y - yi;
  const fz = z - zi;
  const s = (t) => t * t * (3 - 2 * t);
  const u = s(fx);
  const v = s(fy);
  const w = s(fz);
  let acc = 0;
  for (let dz = 0; dz < 2; dz++)
    for (let dy = 0; dy < 2; dy++)
      for (let dx = 0; dx < 2; dx++) {
        const weight = (dx ? u : 1 - u) * (dy ? v : 1 - v) * (dz ? w : 1 - w);
        acc += weight * hash3(xi + dx, yi + dy, zi + dz);
      }
  return acc * 2 - 1;
}

// ------------------------------------------------------------------ geometry helpers

/** Plain arrays of a primitive's bind-pose geometry. */
function readGeometry(prim) {
  const pos = prim.getAttribute("POSITION");
  const nor = prim.getAttribute("NORMAL");
  const uv = prim.getAttribute("TEXCOORD_0");
  const ind = prim.getIndices();
  const n = pos.getCount();
  const P = new Float32Array(n * 3);
  const N = new Float32Array(n * 3);
  const T = new Float32Array(n * 2);
  const e = [0, 0, 0];
  const t = [0, 0];
  for (let i = 0; i < n; i++) {
    pos.getElement(i, e);
    P.set(e, i * 3);
    if (nor) {
      nor.getElement(i, e);
      N.set(e, i * 3);
    }
    if (uv) {
      uv.getElement(i, t);
      T.set(t, i * 2);
    }
  }
  const I = new Uint32Array(ind ? ind.getCount() : n);
  for (let i = 0; i < I.length; i++) I[i] = ind ? ind.getScalar(i) : i;
  return { P, N, T, I, vertexCount: n };
}

/**
 * Rasterize a mesh into its UV atlas: per texel bind-pose position, normal and
 * coverage (glTF UV origin is the top-left texel). Islands are then dilated a
 * few texels so paint never leaves a seam under bilinear filtering.
 */
function rasterizeUv(geo, W, H, dilate = 3) {
  const P = new Float32Array(W * H * 3);
  const N = new Float32Array(W * H * 3);
  let C = new Uint8Array(W * H);
  const { P: vp, N: vn, T: vt, I } = geo;
  for (let f = 0; f < I.length; f += 3) {
    const a = I[f];
    const b = I[f + 1];
    const c = I[f + 2];
    const x0 = vt[a * 2] * W;
    const y0 = vt[a * 2 + 1] * H;
    const x1 = vt[b * 2] * W;
    const y1 = vt[b * 2 + 1] * H;
    const x2 = vt[c * 2] * W;
    const y2 = vt[c * 2 + 1] * H;
    const den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
    if (Math.abs(den) < 1e-12) continue;
    const minx = Math.max(0, Math.floor(Math.min(x0, x1, x2)));
    const maxx = Math.min(W - 1, Math.ceil(Math.max(x0, x1, x2)));
    const miny = Math.max(0, Math.floor(Math.min(y0, y1, y2)));
    const maxy = Math.min(H - 1, Math.ceil(Math.max(y0, y1, y2)));
    for (let y = miny; y <= maxy; y++) {
      for (let x = minx; x <= maxx; x++) {
        const px = x + 0.5;
        const py = y + 0.5;
        const l0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / den;
        const l1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / den;
        const l2 = 1 - l0 - l1;
        if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) continue;
        const o = y * W + x;
        C[o] = 1;
        for (let k = 0; k < 3; k++) {
          P[o * 3 + k] = l0 * vp[a * 3 + k] + l1 * vp[b * 3 + k] + l2 * vp[c * 3 + k];
          N[o * 3 + k] = l0 * vn[a * 3 + k] + l1 * vn[b * 3 + k] + l2 * vn[c * 3 + k];
        }
      }
    }
  }
  for (let it = 0; it < dilate; it++) {
    const next = Uint8Array.from(C);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const o = y * W + x;
        if (C[o]) continue;
        const nb = [x > 0 ? o - 1 : -1, x < W - 1 ? o + 1 : -1, y > 0 ? o - W : -1, y < H - 1 ? o + W : -1].find((q) => q >= 0 && C[q]);
        if (nb === undefined) continue;
        for (let k = 0; k < 3; k++) {
          P[o * 3 + k] = P[nb * 3 + k];
          N[o * 3 + k] = N[nb * 3 + k];
        }
        next[o] = 1;
      }
    }
    C = next;
  }
  return { P, N, C, W, H };
}

/** Barycentric coordinates of the point on triangle abc closest to p (Ericson). */
function closestBarycentric(p, a, b, c) {
  const sub = (u, v) => [u[0] - v[0], u[1] - v[1], u[2] - v[2]];
  const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  const ab = sub(b, a);
  const ac = sub(c, a);
  const ap = sub(p, a);
  const d1 = dot(ab, ap);
  const d2 = dot(ac, ap);
  if (d1 <= 0 && d2 <= 0) return [1, 0, 0];
  const bp = sub(p, b);
  const d3 = dot(ab, bp);
  const d4 = dot(ac, bp);
  if (d3 >= 0 && d4 <= d3) return [0, 1, 0];
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) {
    const v = d1 / (d1 - d3);
    return [1 - v, v, 0];
  }
  const cp = sub(p, c);
  const d5 = dot(ab, cp);
  const d6 = dot(ac, cp);
  if (d6 >= 0 && d5 <= d6) return [0, 0, 1];
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) {
    const w = d2 / (d2 - d6);
    return [1 - w, 0, w];
  }
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
    const w = (d4 - d3) / (d4 - d3 + (d5 - d6));
    return [0, 1 - w, w];
  }
  const denom = 1 / (va + vb + vc);
  const v = vb * denom;
  const w = vc * denom;
  return [1 - v - w, v, w];
}

/**
 * Project points onto the nearest surface point of `geo` (only triangles near
 * the points are searched). Returns per point the UV and the distance (m).
 */
function projectToSurface(points, geo, margin = 0.015) {
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (const p of points) for (let k = 0; k < 3; k++) {
    lo[k] = Math.min(lo[k], p[k] - margin);
    hi[k] = Math.max(hi[k], p[k] + margin);
  }
  const { P, T, I } = geo;
  const vert = (i) => [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]];
  const inBox = (i) => P[i * 3] >= lo[0] && P[i * 3] <= hi[0] && P[i * 3 + 1] >= lo[1] && P[i * 3 + 1] <= hi[1] && P[i * 3 + 2] >= lo[2] && P[i * 3 + 2] <= hi[2];
  const tris = [];
  for (let f = 0; f < I.length; f += 3) if (inBox(I[f]) || inBox(I[f + 1]) || inBox(I[f + 2])) tris.push(f);
  return points.map((p) => {
    let best = Infinity;
    let uv = [0, 0];
    for (const f of tris) {
      const a = vert(I[f]);
      const b = vert(I[f + 1]);
      const c = vert(I[f + 2]);
      const [l0, l1, l2] = closestBarycentric(p, a, b, c);
      const q = [l0 * a[0] + l1 * b[0] + l2 * c[0], l0 * a[1] + l1 * b[1] + l2 * c[1], l0 * a[2] + l1 * b[2] + l2 * c[2]];
      const d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2;
      if (d < best) {
        best = d;
        uv = [l0 * T[I[f] * 2] + l1 * T[I[f + 1] * 2] + l2 * T[I[f + 2] * 2], l0 * T[I[f] * 2 + 1] + l1 * T[I[f + 1] * 2 + 1] + l2 * T[I[f + 2] * 2 + 1]];
      }
    }
    return { u: uv[0], v: uv[1], dist: Math.sqrt(best) };
  });
}

/**
 * Open edges of a mesh as position pairs. Vertices are first welded by
 * position: glTF splits vertices along UV seams, which would otherwise look
 * like open edges (e.g. the shoulder seams between front and back pieces).
 */
function boundaryEdges(geo) {
  const { P, I, vertexCount } = geo;
  const canonical = new Uint32Array(vertexCount);
  const byPosition = new Map();
  for (let i = 0; i < vertexCount; i++) {
    const key = `${Math.round(P[i * 3] * 1e4)},${Math.round(P[i * 3 + 1] * 1e4)},${Math.round(P[i * 3 + 2] * 1e4)}`;
    if (!byPosition.has(key)) byPosition.set(key, i);
    canonical[i] = byPosition.get(key);
  }
  const count = new Map();
  for (let f = 0; f < I.length; f += 3) {
    for (let k = 0; k < 3; k++) {
      const a = canonical[I[f + k]];
      const b = canonical[I[f + ((k + 1) % 3)]];
      if (a === b) continue;
      const key = a < b ? a * 1e7 + b : b * 1e7 + a;
      count.set(key, (count.get(key) ?? 0) + 1);
    }
  }
  const vertex = (i) => [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]];
  const edges = [];
  for (const [key, c] of count) if (c === 1) edges.push([vertex(Math.floor(key / 1e7)), vertex(key % 1e7)]);
  return edges;
}

/** Distance from p to the nearest of a set of segments. */
function minDistToSegments(p, segments) {
  let best = Infinity;
  for (const [a, b] of segments) {
    const d = distToSegment(p, a, b);
    if (d < best) best = d;
  }
  return best;
}

/** Bind-pose joint positions (translation of inverse(inverseBindMatrix)) by joint name. */
function bindJointPositions(skin) {
  const ibm = skin.getInverseBindMatrices();
  const out = {};
  skin.listJoints().forEach((joint, i) => {
    const m = ibm.getElement(i, new Array(16));
    // Affine inverse: translation = -R^T t for the upper-left 3x3 R (column-major).
    const R = [m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]];
    const t = [m[12], m[13], m[14]];
    // Solve R * p = -t via the inverse of the 3x3 (handles scale too).
    const [a, b, c, d, e, f, g, h, k] = [R[0], R[3], R[6], R[1], R[4], R[7], R[2], R[5], R[8]];
    const det = a * (e * k - f * h) - b * (d * k - f * g) + c * (d * h - e * g);
    const inv = [(e * k - f * h) / det, (c * h - b * k) / det, (b * f - c * e) / det, (f * g - d * k) / det, (a * k - c * g) / det, (c * d - a * f) / det, (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det];
    const nt = [-t[0], -t[1], -t[2]];
    out[joint.getName()] = [inv[0] * nt[0] + inv[1] * nt[1] + inv[2] * nt[2], inv[3] * nt[0] + inv[4] * nt[1] + inv[5] * nt[2], inv[6] * nt[0] + inv[7] * nt[1] + inv[8] * nt[2]];
  });
  return out;
}

function segmentParam(p, a, b) {
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const len2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2;
  return ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1] + (p[2] - a[2]) * ab[2]) / len2;
}

function distToSegment(p, a, b) {
  const t = clamp01(segmentParam(p, a, b));
  const q = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
}


/** Add a Gaussian splat (sigma in texels) to a float map; returns the touched rect. */
function splat(map, W, H, x, y, sigma, weight, rect) {
  const r = Math.ceil(sigma * 3);
  const cx = Math.floor(x);
  const cy = Math.floor(y);
  for (let yy = Math.max(0, cy - r); yy <= Math.min(H - 1, cy + r); yy++) {
    for (let xx = Math.max(0, cx - r); xx <= Math.min(W - 1, cx + r); xx++) {
      const d2 = (xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2;
      map[yy * W + xx] += weight * Math.exp(-d2 / (2 * sigma * sigma));
    }
  }
  rect.x0 = Math.min(rect.x0, cx - r);
  rect.x1 = Math.max(rect.x1, cx + r);
  rect.y0 = Math.min(rect.y0, cy - r);
  rect.y1 = Math.max(rect.y1, cy + r);
}

function percentile(map, rect, W, q, floor = 1e-6) {
  const values = [];
  for (let y = Math.max(0, rect.y0); y <= rect.y1; y++) for (let x = Math.max(0, rect.x0); x <= rect.x1; x++) {
    const v = map[y * W + x];
    if (v > floor) values.push(v);
  }
  values.sort((a, b) => a - b);
  return values.length ? values[Math.min(values.length - 1, Math.floor(values.length * q))] : 1;
}

/** Soft painted hair (brows / lash line) from strand vertices projected onto the skin. */
function hairAlpha(points, geo, W, H, { maxDist, sigmaFine, sigmaSoft, softShare }) {
  const projected = projectToSurface(points, geo);
  const fine = new Float32Array(W * H);
  const rect = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
  let used = 0;
  for (const p of projected) {
    if (p.dist > maxDist) continue;
    splat(fine, W, H, p.u * W, p.v * H, sigmaFine, 1 - 0.6 * (p.dist / maxDist), rect);
    used += 1;
  }
  if (!used) return { alpha: null, rect, used };
  const soft = gaussianBlurRect(fine, W, H, sigmaSoft, rect);
  const pf = percentile(fine, rect, W, 0.85, 1e-3);
  const ps = percentile(soft, rect, W, 0.85, 1e-3);
  const alpha = new Float32Array(W * H);
  const r = Math.ceil(sigmaSoft * 3);
  for (let y = Math.max(0, rect.y0 - r); y <= Math.min(H - 1, rect.y1 + r); y++) {
    for (let x = Math.max(0, rect.x0 - r); x <= Math.min(W - 1, rect.x1 + r); x++) {
      const o = y * W + x;
      // Soft saturation: dense cores approach full cover, edges feather out.
      const a = softShare * (1 - Math.exp(-1.6 * (soft[o] / ps))) + (1 - softShare) * clamp01(fine[o] / pf);
      alpha[o] = clamp01(a);
    }
  }
  return { alpha, rect, used };
}

// ------------------------------------------------------------------ eyes

/**
 * The source iris is a saturated red-brown that reads as red under studio
 * light: shift saturated reds to a natural warm brown and calm the sclera veins.
 */
async function restyleEyes(texture) {
  const { data, info } = await sharp(Buffer.from(texture.getImage())).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  let changed = 0;
  for (let i = 0; i < info.width * info.height; i++) {
    const r = data[i * 4] / 255;
    const g = data[i * 4 + 1] / 255;
    const b = data[i * 4 + 2] / 255;
    const [h, sat, v] = rgbToHsv(r, g, b);
    const red = h < 30 || h > 330;
    if (!red || sat < 0.12) continue;
    let nh = h;
    let ns = sat;
    let nv = v;
    if (sat > 0.35 && v < 0.75) {
      // Iris: warm brown with slightly less saturation.
      nh = 26 + (h > 330 ? 0 : h * 0.15);
      ns = sat * 0.72;
      nv = v * 0.9;
    } else {
      // Sclera veins and pinkish tints: desaturate.
      ns = sat * 0.45;
    }
    const [nr, ng, nb] = hsvToRgb(nh, ns, nv);
    data[i * 4] = Math.round(nr * 255);
    data[i * 4 + 1] = Math.round(ng * 255);
    data[i * 4 + 2] = Math.round(nb * 255);
    changed++;
  }
  const png = await sharp(data, { raw: { width: info.width, height: info.height, channels: 4 } }).png().toBuffer();
  texture.setImage(new Uint8Array(png));
  texture.setMimeType("image/png");
  if (debugDir) await sharp(png).toFile(path.join(debugDir, "eyes.png"));
  return changed;
}

// ------------------------------------------------------------------ wardrobe

/** Shirt V, lapels and collar points on the blazer front (bind-pose metres, front neckline at y 1.447). */
const NECK_Y = 1.447;
const V_APEX = 1.212;
const V_TOP_HALF_WIDTH = 0.072;
const COLLAR_BAND = 0.016;

/** Half-width of the shirt opening at height y (0 at the apex). */
function vHalfWidth(y) {
  if (y <= V_APEX) return 0;
  return V_TOP_HALF_WIDTH * Math.min(1.15, (y - V_APEX) / (NECK_Y - V_APEX)) ** 0.88;
}

/** Lapel width beyond the opening: widest across the upper chest, tapering to the button. */
function lapelWidth(y) {
  return 0.007 + 0.043 * smoothstep(V_APEX, 1.37, y) * (1 - 0.55 * smoothstep(1.405, 1.445, y));
}

/** Spread collar point (front, one side) as a triangle in the frontal (x, y) plane. */
function collarPoint(sign) {
  return [
    [sign * 0.004, NECK_Y - 0.002],
    [sign * 0.068, 1.474],
    [sign * 0.088, 1.388],
  ];
}

function pointInTriangle(px, py, tri) {
  const [[ax, ay], [bx, by], [cx, cy]] = tri;
  const d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
  const l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d;
  const l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d;
  return l0 >= 0 && l1 >= 0 && 1 - l0 - l1 >= 0;
}

function distToSegment2(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const t = clamp01(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy));
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

/** Distance from a point to the lower (visible) edges of a collar point. */
function collarEdgeDistance(px, py, tri) {
  const [a, b, c] = tri;
  return Math.min(distToSegment2(px, py, a[0], a[1], c[0], c[1]), distToSegment2(px, py, b[0], b[1], c[0], c[1]));
}

/**
 * Classify a texel of the T-shirt (now blazer + shirt) by its bind-pose
 * position. Returns the fabric ("blazer" | "shirt"), a baked shading factor
 * and how much of the source fold detail to keep.
 */
function classifyTop(p, n, neckline) {
  const [x, y, z] = p;
  const ax = Math.abs(x);
  let shade = 1;
  const dNeck = y > 1.36 ? minDistToSegments(p, neckline) : Infinity;
  // Shirt collar band around the neckline: full height at the front, narrower
  // at the sides and back where a real collar stands against the neck.
  const band = COLLAR_BAND * (0.62 + 0.38 * smoothstep(-0.02, 0.07, z));
  if (dNeck < band) {
    const edge = smoothstep(band - 0.003, band, dNeck);
    return { fabric: "shirt", shade: 1 - 0.22 * edge, fold: 0.35 };
  }
  // Contact shadow just under the collar band.
  if (dNeck < band + 0.007) shade *= 0.8 + 0.2 * smoothstep(band, band + 0.007, dNeck);
  const front = z > 0.02 && n[2] > 0.05 && ax < 0.2 && y > V_APEX - 0.03 && y < NECK_Y + 0.04;
  if (!front) return { fabric: "blazer", shade, fold: 1 };

  // Spread collar points lying over the lapels.
  for (const sign of [-1, 1]) {
    const tri = collarPoint(sign);
    const edge = collarEdgeDistance(x, y, tri);
    if (pointInTriangle(x, y, tri)) {
      return { fabric: "shirt", shade: shade * (0.9 + 0.1 * smoothstep(0.0008, 0.0025, edge)), fold: 0.3 };
    }
    // The collar point casts a soft shadow just below its edges.
    if (edge < 0.006 && y < tri[1][1]) shade *= 0.72 + 0.28 * smoothstep(0, 0.006, edge);
  }

  const w = vHalfWidth(y);
  const d = ax - w; // < 0 inside the shirt opening
  if (d < 0 && y > V_APEX) {
    // Shirt front: recessed under the lapels, placket and buttons down the middle.
    shade *= 0.7 + 0.3 * smoothstep(0, 0.009, -d);
    if (ax < 0.0055) shade *= ax > 0.0042 ? 0.84 : 0.95;
    for (let by = V_APEX + 0.035; by < NECK_Y - 0.025; by += 0.068) {
      const r = Math.hypot(x, y - by);
      if (r < 0.0036) shade *= r > 0.0026 ? 0.78 : 0.9;
    }
    return { fabric: "shirt", shade, fold: 0.6 };
  }
  // Lapel: a lit fold along the opening, a crease at its outer edge.
  const L = lapelWidth(y);
  if (d < L + 0.004 && y > V_APEX - 0.01) {
    if (d < L) shade *= 1 + 0.2 * (1 - smoothstep(0, 0.0045, d));
    const crease = Math.abs(d - L);
    shade *= 1 - 0.3 * (1 - smoothstep(0, 0.0026, crease));
    if (d > L) shade *= 1 + 0.07 * (1 - smoothstep(0.0026, 0.004, d - L));
  }
  // The single button where the lapels meet.
  const br = Math.hypot(x, y - (V_APEX - 0.014));
  if (br < 0.0075) shade *= br > 0.0058 ? 0.62 : 0.8;
  return { fabric: "blazer", shade, fold: 1 };
}

/**
 * Restyle the casual-suit diffuse. Fabric pixels (blue, saturated) keep their
 * luminance relative to the fabric median (folds, weave); logo, trims and
 * background become flat colour. The T-shirt pieces (upper atlas half, and the
 * sleeves at lower right) are painted as blazer + shirt by 3D region, the
 * jeans as trousers.
 */
async function restyleGarment(texture, geo, ctx) {
  const { rgb, alpha, W, H, srgb } = await decodeLinear(texture);
  const maps = rasterizeUv(geo, W, H);
  const lum = new Float32Array(W * H);
  const fabric = new Float32Array(W * H);
  const samples = [];
  for (let i = 0; i < W * H; i++) {
    const r = srgb[i * 4] / 255;
    const g = srgb[i * 4 + 1] / 255;
    const b = srgb[i * 4 + 2] / 255;
    const [hue, sat, max] = rgbToHsv(r, g, b);
    lum[i] = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const hueW = Math.max(0, 1 - Math.max(0, Math.abs(hue - 220) - 28) / 10);
    const satW = Math.min(1, Math.max(0, (sat - 0.18) / 0.12));
    fabric[i] = hueW * satW * (max > 0.12 ? 1 : 0);
    if (fabric[i] > 0.99 && (i & 15) === 0) samples.push(lum[i]);
  }
  samples.sort((a, b) => a - b);
  const median = samples[Math.floor(samples.length / 2)] || 0.35;
  // Grow the non-fabric areas a little and feather them so logo edges vanish.
  const weight = boxBlur(minFilter(fabric, W, H, 3), W, H, 2);
  const blazer = hexToLinear(COLORS.blazer);
  const shirt = hexToLinear(COLORS.shirt);
  const trousers = hexToLinear(COLORS.trousers);
  const counts = { blazer: 0, shirt: 0, trousers: 0 };
  const p = [0, 0, 0];
  const n = [0, 0, 0];
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      const isTop = y < H * 0.42 || (x > W * 0.72 && y > H * 0.55);
      const ratio = Math.min(1.4, Math.max(0.55, Math.pow(lum[i] / median, 0.85)));
      let base = trousers;
      let shade = 1;
      let foldShare = 0.6;
      if (isTop) {
        base = blazer;
        foldShare = 1;
        if (maps.C[i]) {
          for (let k = 0; k < 3; k++) {
            p[k] = maps.P[i * 3 + k];
            n[k] = maps.N[i * 3 + k];
          }
          const region = classifyTop(p, n, ctx.neckline);
          base = region.fabric === "shirt" ? shirt : blazer;
          shade = region.shade;
          foldShare = region.fold;
          counts[region.fabric] += 1;
        } else counts.blazer += 1;
      } else counts.trousers += 1;
      const k = (1 + (ratio - 1) * weight[i] * foldShare) * shade;
      rgb[i * 3] = base[0] * k;
      rgb[i * 3 + 1] = base[1] * k;
      rgb[i * 3 + 2] = base[2] * k;
      alpha[i] = 255;
    }
  }
  await encodeInto(texture, rgb, alpha, W, H, "garment");
  return { median, counts };
}

// ------------------------------------------------------------------ skin

/**
 * Body (skin atlas): warm grade, painted brows and lash line, blazer sleeves
 * with a shirt cuff on the visible arms, contact shadow above the collar.
 */
async function restyleSkin(texture, geo, ctx) {
  const { rgb, alpha, W, H } = await decodeLinear(texture);
  const maps = rasterizeUv(geo, W, H);
  const report = {};

  // 1. Warm grade: a touch more red, less blue (the albedo itself is natural;
  //    the pale look mostly came from the lighting, fixed in the renderer).
  for (let i = 0; i < W * H; i++) {
    rgb[i * 3] *= 1.02;
    rgb[i * 3 + 2] *= 0.95;
  }

  // 2. Brows: strand vertices -> skin -> soft density with hair-scale detail.
  const brows = hairAlpha(ctx.browPoints, geo, W, H, { maxDist: 0.006, sigmaFine: 0.8, sigmaSoft: 3.0, softShare: 0.72 });
  const browColor = hexToLinear(COLORS.brow);
  if (brows.alpha) {
    for (let i = 0; i < W * H; i++) {
      const a = brows.alpha[i] * 0.74;
      if (a <= 0) continue;
      for (let k = 0; k < 3; k++) rgb[i * 3 + k] += (browColor[k] - rgb[i * 3 + k]) * a;
    }
  }
  report.browPoints = brows.used;

  // 3. Lash line: only the lash roots (vertices touching the lid margin).
  const lashes = hairAlpha(ctx.lashPoints, geo, W, H, { maxDist: 0.0022, sigmaFine: 0.9, sigmaSoft: 1.3, softShare: 0.7 });
  const lashColor = hexToLinear(COLORS.lash);
  if (lashes.alpha) {
    for (let i = 0; i < W * H; i++) {
      const a = lashes.alpha[i] * 0.3;
      if (a <= 0) continue;
      for (let k = 0; k < 3; k++) rgb[i * 3 + k] += (lashColor[k] - rgb[i * 3 + k]) * a;
    }
  }
  report.lashRoots = lashes.used;

  // 4. Sleeves: the visible arm skin becomes the blazer sleeve down to a shirt
  //    cuff just above the wrist; the hands stay skin.
  const blazer = hexToLinear(COLORS.blazer);
  const shirt = hexToLinear(COLORS.shirt);
  const J = ctx.joints;
  const arms = [
    { sign: 1, shoulder: J.LeftArm, elbow: J.LeftForeArm, wrist: J.LeftHand },
    { sign: -1, shoulder: J.RightArm, elbow: J.RightForeArm, wrist: J.RightHand },
  ];
  let sleeveTexels = 0;
  const fabricMask = new Float32Array(W * H);
  const p = [0, 0, 0];
  for (let i = 0; i < W * H; i++) {
    if (!maps.C[i]) continue;
    p[0] = maps.P[i * 3];
    p[1] = maps.P[i * 3 + 1];
    p[2] = maps.P[i * 3 + 2];
    if (Math.abs(p[0]) < 0.17 || p[1] < 0.95 || p[1] > 1.43) continue;
    const arm = p[0] > 0 ? arms[0] : arms[1];
    if (!arm.shoulder || !arm.elbow || !arm.wrist) continue;
    const near = Math.min(distToSegment(p, arm.shoulder, arm.elbow), distToSegment(p, arm.elbow, arm.wrist));
    if (near > 0.085) continue;
    const s = segmentParam(p, arm.elbow, arm.wrist); // 0 at the elbow, 1 at the wrist
    const sleeveEnd = 0.86;
    const cuffEnd = 0.965;
    if (s >= cuffEnd + 0.01) continue;
    const toCuff = smoothstep(sleeveEnd - 0.006, sleeveEnd + 0.006, s);
    const toSkin = smoothstep(cuffEnd - 0.006, cuffEnd + 0.006, s);
    // Fabric texture: fine weave noise plus soft large-scale variation.
    const grain = 0.035 * valueNoise3(p[0] * 900, p[1] * 900, p[2] * 900) + 0.05 * valueNoise3(p[0] * 40, p[1] * 40, p[2] * 40);
    const sleeveShade = (1 + grain) * (0.94 + 0.06 * smoothstep(0, 0.5, s));
    // The sleeve end casts a thin shadow onto the cuff.
    const cuffShade = (0.98 + 0.4 * grain) * (0.74 + 0.26 * smoothstep(sleeveEnd, sleeveEnd + 0.03, s));
    for (let k = 0; k < 3; k++) {
      const fabricColor = blazer[k] * sleeveShade * (1 - toCuff) + shirt[k] * cuffShade * toCuff;
      rgb[i * 3 + k] += (fabricColor - rgb[i * 3 + k]) * (1 - toSkin);
    }
    fabricMask[i] = 1 - toSkin;
    sleeveTexels += 1;
  }
  report.sleeveTexels = sleeveTexels;

  // 5. Contact shadow on the neck just above the collar.
  let shadowTexels = 0;
  for (let i = 0; i < W * H; i++) {
    if (!maps.C[i]) continue;
    const y = maps.P[i * 3 + 1];
    if (y < 1.38 || y > 1.56 || Math.abs(maps.P[i * 3]) > 0.16) continue;
    p[0] = maps.P[i * 3];
    p[1] = y;
    p[2] = maps.P[i * 3 + 2];
    const d = minDistToSegments(p, ctx.neckline);
    if (d > 0.03) continue;
    const s = 1 - 0.22 * (1 - smoothstep(0.003, 0.028, d));
    rgb[i * 3] *= s;
    rgb[i * 3 + 1] *= s;
    rgb[i * 3 + 2] *= s;
    shadowTexels += 1;
  }
  report.neckShadowTexels = shadowTexels;

  await encodeInto(texture, rgb, alpha, W, H, "skin");
  report.fabricMask = fabricMask;
  report.W = W;
  report.H = H;
  return report;
}

/**
 * glTF metallic-roughness texture for the body (G = roughness, B = metalness):
 * skin stays satin, the painted sleeves and cuffs get the fabric's matte
 * roughness. Quarter resolution is plenty for these soft regions.
 */
async function bodyRoughnessTexture(document, mask, W, H, { skin = 0.56, fabric = 0.86, scale = 4 } = {}) {
  const w = W / scale;
  const h = H / scale;
  const out = Buffer.alloc(w * h * 3);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let acc = 0;
      for (let yy = 0; yy < scale; yy++) for (let xx = 0; xx < scale; xx++) acc += mask[(y * scale + yy) * W + x * scale + xx];
      const m = acc / (scale * scale);
      const o = (y * w + x) * 3;
      out[o] = 255;
      out[o + 1] = Math.round((skin + (fabric - skin) * m) * 255);
      out[o + 2] = 0;
    }
  }
  const png = await sharp(out, { raw: { width: w, height: h, channels: 3 } }).png().toBuffer();
  if (debugDir) await sharp(png).toFile(path.join(debugDir, "skin_roughness.png"));
  return document.createTexture("body_roughness").setImage(new Uint8Array(png)).setMimeType("image/png");
}

// ------------------------------------------------------------------ compression rules

/**
 * Texture budgets, first match wins (matched against texture name, then the
 * name of the material that uses it). Body/face and the outfit diffuse (lapel
 * and collar edges) <= 2048, hair <= 1024, small facial parts lower still
 * (they cover a few dozen pixels on screen).
 */
const TEXTURE_RULES = [
  { label: "skin", test: /(skin|body|diffuse2|face)/i, size: 2048, quality: 88 },
  { label: "eyes", test: /(eye|iris|cornea|high-poly)/i, size: 512, quality: 90 },
  { label: "teeth", test: /teeth/i, size: 512, quality: 86 },
  { label: "tongue", test: /tongue/i, size: 256, quality: 82 },
  { label: "hair", test: /(hair|ponytail|bun|brow|lash)/i, size: 1024, quality: 86 },
  { label: "clothing-normal", test: /normal/i, size: 1024, quality: 92 },
  { label: "clothing", test: /.*/, size: 2048, quality: 86 },
];

const mb = (bytes) => `${(bytes / 1024 / 1024).toFixed(2)} MB`;

function describeDocument(document) {
  const root = document.getRoot();
  const meshes = root.listMeshes().map((mesh) => {
    const prims = mesh.listPrimitives();
    const names = Array.isArray(mesh.getExtras()?.targetNames) ? mesh.getExtras().targetNames : [];
    return {
      name: mesh.getName(),
      targetNames: names,
      targetCounts: prims.map((p) => p.listTargets().length),
      skinned: prims.every((p) => !!p.getAttribute("JOINTS_0") && !!p.getAttribute("WEIGHTS_0")),
    };
  });
  const skins = root.listSkins().map((skin) => ({
    name: skin.getName(),
    joints: skin.listJoints().map((j) => j.getName()),
    ibm: skin.getInverseBindMatrices()?.getCount() ?? 0,
  }));
  return { meshes, skins };
}

/** Texture name + URI + names of the materials that reference it (graph parent edges). */
function textureOwnerNames(texture) {
  const names = [texture.getName(), texture.getURI()];
  for (const parent of texture.listParents()) {
    if (parent.propertyType === "Material") names.push(parent.getName());
  }
  return names.filter(Boolean).join(" ");
}

function meshByName(root, pattern) {
  return root.listMeshes().find((m) => pattern.test(m.getName()));
}

function vertexPoints(geo) {
  const out = [];
  for (let i = 0; i < geo.vertexCount; i++) out.push([geo.P[i * 3], geo.P[i * 3 + 1], geo.P[i * 3 + 2]]);
  return out;
}

async function restyle(document) {
  const root = document.getRoot();
  const started = performance.now();
  const body = meshByName(root, /^base$|body/i);
  const garment = meshByName(root, /casualsuit|suit|shirt/i);
  const brows = meshByName(root, /eyebrow/i);
  const lashes = meshByName(root, /eyelash/i);
  const skin = root.listSkins()[0];
  if (!body || !garment || !skin) throw new Error("restyle: body, garment or skin not found (use --keep-wardrobe for other assets)");
  const bodyGeo = readGeometry(body.listPrimitives()[0]);
  const garmentGeo = readGeometry(garment.listPrimitives()[0]);
  const joints = bindJointPositions(skin);
  // Neckline: open edges of the garment around the neck (armholes excluded).
  const neckline = boundaryEdges(garmentGeo).filter(([a, b]) => a[1] > 1.4 && b[1] > 1.4 && Math.abs(a[0]) < 0.14 && Math.abs(b[0]) < 0.14);
  if (neckline.length < 12) throw new Error(`restyle: neckline not found (${neckline.length} edges)`);
  const ctx = {
    joints,
    neckline,
    browPoints: brows ? vertexPoints(readGeometry(brows.listPrimitives()[0])) : [],
    lashPoints: lashes ? vertexPoints(readGeometry(lashes.listPrimitives()[0])) : [],
  };

  const garmentTexture = garment.listPrimitives()[0].getMaterial()?.getBaseColorTexture();
  if (garmentTexture) {
    const { median, counts } = await restyleGarment(garmentTexture, garmentGeo, ctx);
    console.log(
      `[optimize-avatar] wardrobe: blazer ${COLORS.blazer} over shirt ${COLORS.shirt}, trousers ${COLORS.trousers} (fabric median ${median.toFixed(3)}; texels blazer ${counts.blazer}, shirt ${counts.shirt}, trousers ${counts.trousers})`,
    );
  }
  const bodyMaterial = body.listPrimitives()[0].getMaterial();
  const skinTexture = bodyMaterial?.getBaseColorTexture();
  if (bodyMaterial && skinTexture) {
    const r = await restyleSkin(skinTexture, bodyGeo, ctx);
    const roughness = await bodyRoughnessTexture(document, r.fabricMask, r.W, r.H);
    bodyMaterial.setMetallicRoughnessTexture(roughness).setRoughnessFactor(1).setMetallicFactor(0);
    console.log(
      `[optimize-avatar] skin: warm grade, brows from ${r.browPoints} strand vertices, lash line from ${r.lashRoots} roots, sleeves ${r.sleeveTexels} texels, neck shadow ${r.neckShadowTexels} texels`,
    );
  }
  for (const material of root.listMaterials()) {
    if (!/(high-poly|eye)/i.test(material.getName())) continue;
    const texture = material.getBaseColorTexture();
    if (!texture) continue;
    const changed = await restyleEyes(texture);
    console.log(`[optimize-avatar] eyes ${material.getName()}: iris toned to warm brown (${changed} px)`);
  }
  console.log(`[optimize-avatar] restyle done in ${((performance.now() - started) / 1000).toFixed(1)} s`);
}

async function main() {
  if (!existsSync(input)) {
    console.error(`[optimize-avatar] input not found: ${input}`);
    process.exit(1);
  }
  if (debugDir) mkdirSync(debugDir, { recursive: true });
  await MeshoptEncoder.ready;
  await MeshoptDecoder.ready;

  const io = new NodeIO()
    .registerExtensions(ALL_EXTENSIONS)
    .registerDependencies({
      "meshopt.encoder": MeshoptEncoder,
      "meshopt.decoder": MeshoptDecoder,
    });

  const started = performance.now();
  const document = await io.read(input);
  const before = describeDocument(document);
  const inputBytes = statSync(input).size;

  if (!keepWardrobe) await restyle(document);
  if (paintOnly) {
    console.log(`[optimize-avatar] --paint-only: textures written to ${debugDir ?? "(no --debug dir)"}, GLB not written`);
    return;
  }

  // keepUniqueNames: the brow and lash materials are identical (black, untextured)
  // and would otherwise be merged into one, so the renderer could not style
  // them separately. Accessor dedup does not depend on names.
  await document.transform(dedup({ keepUniqueNames: true }), prune({ keepAttributes: false, keepLeaves: false }), weld());

  // Assign each texture to its rule, then compress per rule (one pass per
  // texture so each gets its own resize limit and quality).
  const textures = document.getRoot().listTextures();
  for (const texture of textures) {
    const owner = textureOwnerNames(texture);
    const rule = TEXTURE_RULES.find((r) => r.test.test(owner)) ?? TEXTURE_RULES[TEXTURE_RULES.length - 1];
    const name = texture.getName() || texture.getURI() || "texture";
    const src = texture.getSize();
    // Give the texture a unique, regex-safe name so the pattern matches it alone.
    const tag = `__opt_${textures.indexOf(texture)}__`;
    texture.setName(tag);
    await document.transform(
      textureCompress({
        encoder: sharp,
        targetFormat: "webp",
        resize: [rule.size, rule.size],
        quality: rule.quality,
        effort: 90,
        pattern: new RegExp(`^${tag}$`),
      }),
    );
    texture.setName(name);
    const dst = texture.getSize();
    console.log(
      `[optimize-avatar] texture ${name.padEnd(36)} ${rule.label.padEnd(15)} ${src?.join("x")} -> ${dst?.join("x")} webp q${rule.quality} (${(texture.getImage()?.byteLength / 1024).toFixed(0)} KB)`,
    );
  }

  // "scene" quantization volume keeps one shared skin (per-mesh volumes would clone it per mesh).
  await document.transform(meshopt({ encoder: MeshoptEncoder, level: "high", quantizationVolume: "scene" }));
  await io.write(output, document);
  const outputBytes = statSync(output).size;

  // ---- verification: re-read the output and compare structure.
  const check = await io.read(output);
  const after = describeDocument(check);
  const problems = [];
  for (const src of before.meshes) {
    const dst = after.meshes.find((m) => m.name === src.name);
    if (!dst) {
      problems.push(`mesh ${src.name} missing`);
      continue;
    }
    if (src.targetNames.join(",") !== dst.targetNames.join(",")) problems.push(`mesh ${src.name}: targetNames changed`);
    if (src.targetCounts.join(",") !== dst.targetCounts.join(",")) problems.push(`mesh ${src.name}: target count changed`);
    if (src.skinned !== dst.skinned) problems.push(`mesh ${src.name}: skin attributes changed`);
  }
  for (const src of before.skins) {
    const dst = after.skins.find((s) => s.name === src.name);
    if (!dst) problems.push(`skin ${src.name} missing`);
    else {
      if (src.joints.join(",") !== dst.joints.join(",")) problems.push(`skin ${src.name}: joints changed`);
      if (src.ibm !== dst.ibm) problems.push(`skin ${src.name}: inverse bind matrices changed`);
    }
  }
  const allNames = new Set(after.meshes.flatMap((m) => m.targetNames));
  const visemes = ["PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"].map((v) => `viseme_${v}`);
  const arkitSample = ["eyeBlinkLeft", "eyeBlinkRight", "jawOpen", "mouthSmileLeft", "mouthSmileRight", "browInnerUp", "mouthClose"];
  for (const name of [...visemes, ...arkitSample]) if (!allNames.has(name)) problems.push(`morph target ${name} missing`);
  if (outputBytes > maxBytes) problems.push(`output ${mb(outputBytes)} exceeds budget ${mb(maxBytes)}`);
  // Brows and lashes are styled differently by the renderer: they must keep their own materials.
  const materialOf = (pattern) => meshByName(check.getRoot(), pattern)?.listPrimitives()[0]?.getMaterial() ?? null;
  const browMaterial = materialOf(/eyebrow/i);
  const lashMaterial = materialOf(/eyelash/i);
  if (browMaterial && lashMaterial && browMaterial === lashMaterial) problems.push("brow and lash meshes share one material");

  const totalTargets = after.meshes.reduce((n, m) => n + m.targetNames.length, 0);
  console.log(`[optimize-avatar] input  ${path.relative(webRoot, input)}  ${mb(inputBytes)}`);
  console.log(`[optimize-avatar] output ${path.relative(webRoot, output)}  ${mb(outputBytes)}  (${((1 - outputBytes / inputBytes) * 100).toFixed(1)}% smaller)`);
  console.log(
    `[optimize-avatar] meshes ${after.meshes.length}, morph targets ${totalTargets} (${allNames.size} unique names), joints ${after.skins[0]?.joints.length ?? 0}, ${((performance.now() - started) / 1000).toFixed(1)} s`,
  );
  for (const m of after.meshes) {
    if (m.targetNames.length) console.log(`[optimize-avatar]   ${m.name}: ${m.targetNames.length} targets`);
  }
  if (problems.length) {
    for (const p of problems) console.error(`[optimize-avatar] FAIL ${p}`);
    process.exit(1);
  }
  console.log("[optimize-avatar] OK: skeleton, skin weights and all named morph targets preserved");
}

main().catch((err) => {
  console.error("[optimize-avatar] error:", err);
  process.exit(1);
});
