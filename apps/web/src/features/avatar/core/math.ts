/**
 * Small numeric toolkit for the avatar engine: easing, frame-rate independent
 * smoothing, a seeded PRNG and smooth 1D value noise. Deliberately free of
 * three.js so the controller, behaviour logic and 2D renderer stay out of the
 * 3D bundle (quaternion helpers live in ./quat).
 */

export const DEG = Math.PI / 180;

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function smoothstep(t: number): number {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
}

export function smootherstep(t: number): number {
  const x = clamp01(t);
  return x * x * x * (x * (x * 6 - 15) + 10);
}

export function easeInOutSine(t: number): number {
  return 0.5 - 0.5 * Math.cos(Math.PI * clamp01(t));
}

export function easeOutCubic(t: number): number {
  const x = 1 - clamp01(t);
  return 1 - x * x * x;
}

export function easeInCubic(t: number): number {
  const x = clamp01(t);
  return x * x * x;
}

/**
 * Frame-rate independent exponential smoothing toward `target`.
 * `lambda` is the decay rate in 1/s (time constant = 1/lambda).
 */
export function damp(current: number, target: number, lambda: number, dt: number): number {
  if (dt <= 0) return current;
  return target + (current - target) * Math.exp(-lambda * dt);
}

/** Critically damped spring (no overshoot), integrated semi-implicitly. */
export class Spring {
  value: number;
  velocity = 0;

  constructor(
    initial = 0,
    /** Angular frequency (rad/s); ~4/omega is the settle time. */
    public omega = 8,
  ) {
    this.value = initial;
  }

  update(target: number, dt: number): number {
    if (dt <= 0) return this.value;
    // Sub-step large frames so long hitches do not destabilise the integrator.
    const steps = Math.max(1, Math.ceil(dt / (1 / 120)));
    const h = dt / steps;
    const w = this.omega;
    for (let i = 0; i < steps; i++) {
      const accel = w * w * (target - this.value) - 2 * w * this.velocity;
      this.velocity += accel * h;
      this.value += this.velocity * h;
    }
    return this.value;
  }

  reset(value: number): void {
    this.value = value;
    this.velocity = 0;
  }
}

/** Deterministic 32-bit PRNG (mulberry32). Returns floats in [0, 1). */
export type Rng = () => number;

export function mulberry32(seed: number): Rng {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** FNV-1a string hash, used to derive seeds from stable identifiers. */
export function hashString(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Derive an independent sub-stream seed. */
export function deriveSeed(seed: number, salt: string): number {
  return (hashString(salt) ^ Math.imul(seed >>> 0, 0x9e3779b1)) >>> 0;
}

export function randRange(rng: Rng, lo: number, hi: number): number {
  return lo + (hi - lo) * rng();
}

/**
 * Pick an element, never repeating `previous` when there is an alternative.
 * Deterministic for a given RNG stream.
 */
export function pickAvoidingRepeat<T>(rng: Rng, options: readonly T[], previous: T | undefined): T {
  if (options.length === 0) throw new Error("pickAvoidingRepeat: no options");
  if (options.length === 1) return options[0]!;
  const pool = previous === undefined ? options : options.filter((o) => o !== previous);
  const list = pool.length > 0 ? pool : options;
  return list[Math.floor(rng() * list.length) % list.length]!;
}

/**
 * Smooth 1D value noise in [-1, 1] (cubic Hermite interpolation between
 * seeded lattice values), summed over a few octaves.
 */
export class ValueNoise1D {
  private readonly table: Float32Array;
  private readonly mask: number;

  constructor(seed: number, size = 256) {
    const rng = mulberry32(seed);
    this.table = new Float32Array(size);
    for (let i = 0; i < size; i++) this.table[i] = rng() * 2 - 1;
    this.mask = size - 1;
  }

  private lattice(i: number): number {
    return this.table[i & this.mask]!;
  }

  sample(x: number): number {
    const i = Math.floor(x);
    const f = x - i;
    const u = f * f * (3 - 2 * f);
    return lerp(this.lattice(i), this.lattice(i + 1), u);
  }

  fractal(x: number, octaves = 3): number {
    let amp = 1;
    let freq = 1;
    let sum = 0;
    let norm = 0;
    for (let o = 0; o < octaves; o++) {
      sum += this.sample(x * freq + o * 17.13) * amp;
      norm += amp;
      amp *= 0.5;
      freq *= 2.03;
    }
    return sum / norm;
  }
}

/** Euler angles in degrees, interpreted in the canonical character frame. */
export type EulerDeg = readonly [number, number, number];

export function mirrorEuler(e: EulerDeg): EulerDeg {
  return [e[0], -e[1], -e[2]];
}

export function radToDeg(rad: number): number {
  return (rad * 180) / Math.PI;
}
