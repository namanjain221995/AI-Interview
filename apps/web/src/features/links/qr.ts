/**
 * Tiny dependency-free QR Code encoder (ISO/IEC 18004) for share links:
 * byte mode (UTF-8), error correction level M, versions 1-10 (up to 213 bytes),
 * mask chosen by the standard penalty score. Enough for any `/j/<token>` URL.
 */

// ------------------------------------------------------------ GF(256) + RS

const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i += 1) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i += 1) EXP[i] = EXP[i - 255]!;
})();

function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[LOG[a]! + LOG[b]!]!;
}

/** Generator polynomial coefficients (highest degree first, leading 1 dropped). */
function rsGenerator(degree: number): number[] {
  let poly = [1];
  for (let i = 0; i < degree; i += 1) {
    const next = new Array<number>(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j += 1) {
      next[j] = next[j]! ^ poly[j]!;
      next[j + 1] = next[j + 1]! ^ gfMul(poly[j]!, EXP[i]!);
    }
    poly = next;
  }
  return poly.slice(1);
}

/** Reed-Solomon error-correction codewords for `data`. */
export function rsEncode(data: readonly number[], ecLength: number): number[] {
  const gen = rsGenerator(ecLength);
  const result = new Array<number>(ecLength).fill(0);
  for (const byte of data) {
    const factor = byte ^ result.shift()!;
    result.push(0);
    for (let i = 0; i < ecLength; i += 1) result[i] = result[i]! ^ gfMul(gen[i]!, factor);
  }
  return result;
}

// --------------------------------------------------------------- tables (M)

const TOTAL_CODEWORDS = [0, 26, 44, 70, 100, 134, 172, 196, 242, 292, 346];
const EC_PER_BLOCK_M = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26];
const BLOCKS_M = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5];
const ALIGNMENT = [[], [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];
export const MAX_VERSION = 10;
const FORMAT_ECC_M = 0b00;

export function dataCodewords(version: number): number {
  return TOTAL_CODEWORDS[version]! - EC_PER_BLOCK_M[version]! * BLOCKS_M[version]!;
}

/** 15-bit format information for level M and `mask` (BCH(15,5) + 0x5412 mask). */
export function formatBits(mask: number): number {
  const data = (FORMAT_ECC_M << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i += 1) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

/** 18-bit version information (versions >= 7). */
export function versionBits(version: number): number {
  let rem = version;
  for (let i = 0; i < 12; i += 1) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  return (version << 12) | rem;
}

// ---------------------------------------------------------------- encoding

function utf8(text: string): number[] {
  return Array.from(new TextEncoder().encode(text));
}

function chooseVersion(byteLength: number): number {
  for (let v = 1; v <= MAX_VERSION; v += 1) {
    const countBits = v <= 9 ? 8 : 16;
    if (4 + countBits + byteLength * 8 <= dataCodewords(v) * 8) return v;
  }
  throw new RangeError(`qr: text too long (${byteLength} bytes; the limit is 213)`);
}

/** Data codewords (mode, count, payload, terminator, pad bytes) for `bytes` at `version`. */
export function encodeData(bytes: readonly number[], version: number): number[] {
  const bits: number[] = [];
  const push = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i -= 1) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4);
  push(bytes.length, version <= 9 ? 8 : 16);
  for (const b of bytes) push(b, 8);
  const capacity = dataCodewords(version) * 8;
  push(0, Math.min(4, capacity - bits.length));
  while (bits.length % 8) bits.push(0);
  const out: number[] = [];
  for (let i = 0; i < bits.length; i += 8) out.push(bits.slice(i, i + 8).reduce((acc, bit) => (acc << 1) | bit, 0));
  for (let pad = 0xec; out.length < capacity / 8; pad ^= 0xec ^ 0x11) out.push(pad);
  return out;
}

/** Splits into blocks, appends EC codewords and interleaves (short blocks first). */
export function interleave(data: readonly number[], version: number): number[] {
  const numBlocks = BLOCKS_M[version]!;
  const ecLen = EC_PER_BLOCK_M[version]!;
  const raw = TOTAL_CODEWORDS[version]!;
  const numShort = numBlocks - (raw % numBlocks);
  const shortLen = Math.floor(raw / numBlocks);
  const blocks: number[][] = [];
  let k = 0;
  for (let i = 0; i < numBlocks; i += 1) {
    const dat = data.slice(k, k + shortLen - ecLen + (i < numShort ? 0 : 1));
    k += dat.length;
    const ec = rsEncode(dat, ecLen);
    if (i < numShort) dat.push(0);
    blocks.push([...dat, ...ec]);
  }
  const out: number[] = [];
  for (let i = 0; i < blocks[0]!.length; i += 1) {
    blocks.forEach((block, j) => {
      if (i !== shortLen - ecLen || j >= numShort) out.push(block[i]!);
    });
  }
  return out;
}

// ------------------------------------------------------------------ matrix

export interface QrMatrix {
  version: number;
  size: number;
  mask: number;
  /** modules[row][col]: true = dark. */
  modules: boolean[][];
}

class Builder {
  readonly size: number;
  readonly modules: boolean[][];
  readonly isFunction: boolean[][];

  constructor(readonly version: number) {
    this.size = version * 4 + 17;
    this.modules = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
    this.isFunction = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
  }

  set(col: number, row: number, dark: boolean) {
    this.modules[row]![col] = dark;
    this.isFunction[row]![col] = true;
  }

  drawFunctionPatterns() {
    const { size } = this;
    for (let i = 0; i < size; i += 1) {
      this.set(6, i, i % 2 === 0);
      this.set(i, 6, i % 2 === 0);
    }
    this.finder(3, 3);
    this.finder(size - 4, 3);
    this.finder(3, size - 4);
    const pos = ALIGNMENT[this.version]!;
    const n = pos.length;
    for (let i = 0; i < n; i += 1) {
      for (let j = 0; j < n; j += 1) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue;
        this.alignment(pos[i]!, pos[j]!);
      }
    }
    this.drawFormat(0);
    this.drawVersion();
  }

  finder(cx: number, cy: number) {
    for (let dy = -4; dy <= 4; dy += 1) {
      for (let dx = -4; dx <= 4; dx += 1) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        const x = cx + dx;
        const y = cy + dy;
        if (x >= 0 && x < this.size && y >= 0 && y < this.size) this.set(x, y, dist !== 2 && dist !== 4);
      }
    }
  }

  alignment(cx: number, cy: number) {
    for (let dy = -2; dy <= 2; dy += 1) {
      for (let dx = -2; dx <= 2; dx += 1) this.set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }

  drawFormat(mask: number) {
    const bits = formatBits(mask);
    const bit = (i: number) => ((bits >>> i) & 1) === 1;
    const { size } = this;
    for (let i = 0; i <= 5; i += 1) this.set(8, i, bit(i));
    this.set(8, 7, bit(6));
    this.set(8, 8, bit(7));
    this.set(7, 8, bit(8));
    for (let i = 9; i < 15; i += 1) this.set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i += 1) this.set(size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i += 1) this.set(8, size - 15 + i, bit(i));
    this.set(8, size - 8, true); // the dark module
  }

  drawVersion() {
    if (this.version < 7) return;
    const bits = versionBits(this.version);
    for (let i = 0; i < 18; i += 1) {
      const dark = ((bits >>> i) & 1) === 1;
      const a = this.size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      this.set(a, b, dark);
      this.set(b, a, dark);
    }
  }

  drawCodewords(codewords: readonly number[]) {
    const { size } = this;
    let i = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert += 1) {
        for (let j = 0; j < 2; j += 1) {
          const x = right - j;
          const upward = ((right + 1) & 2) === 0;
          const y = upward ? size - 1 - vert : vert;
          if (!this.isFunction[y]![x] && i < codewords.length * 8) {
            this.modules[y]![x] = ((codewords[i >>> 3]! >>> (7 - (i & 7))) & 1) === 1;
            i += 1;
          }
        }
      }
    }
  }

  applyMask(mask: number) {
    for (let y = 0; y < this.size; y += 1) {
      for (let x = 0; x < this.size; x += 1) {
        if (this.isFunction[y]![x]) continue;
        if (maskBit(mask, x, y)) this.modules[y]![x] = !this.modules[y]![x];
      }
    }
  }
}

export function maskBit(mask: number, x: number, y: number): boolean {
  switch (mask) {
    case 0:
      return (x + y) % 2 === 0;
    case 1:
      return y % 2 === 0;
    case 2:
      return x % 3 === 0;
    case 3:
      return (x + y) % 3 === 0;
    case 4:
      return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
    case 5:
      return ((x * y) % 2) + ((x * y) % 3) === 0;
    case 6:
      return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
    default:
      return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
  }
}

const FINDER_LIKE = [
  [true, false, true, true, true, false, true, false, false, false, false],
  [false, false, false, false, true, false, true, true, true, false, true],
];

/** Standard penalty score (rules N1-N4); lower is better. */
export function penalty(modules: readonly (readonly boolean[])[]): number {
  const size = modules.length;
  let score = 0;
  const line = (get: (i: number) => boolean) => {
    let run = 1;
    for (let i = 1; i <= size; i += 1) {
      if (i < size && get(i) === get(i - 1)) run += 1;
      else {
        if (run >= 5) score += 3 + (run - 5);
        run = 1;
      }
    }
    for (let i = 0; i + 11 <= size; i += 1) {
      for (const pattern of FINDER_LIKE) if (pattern.every((dark, k) => get(i + k) === dark)) score += 40;
    }
  };
  for (let r = 0; r < size; r += 1) line((c) => modules[r]![c]!);
  for (let c = 0; c < size; c += 1) line((r) => modules[r]![c]!);
  let dark = 0;
  for (let r = 0; r < size; r += 1) {
    for (let c = 0; c < size; c += 1) {
      const m = modules[r]![c]!;
      if (m) dark += 1;
      if (r + 1 < size && c + 1 < size && m === modules[r]![c + 1] && m === modules[r + 1]![c] && m === modules[r + 1]![c + 1]) score += 3;
    }
  }
  const percent = (dark * 100) / (size * size);
  score += Math.floor(Math.abs(percent - 50) / 5) * 10;
  return score;
}

/** Encodes `text` into a QR matrix (level M, smallest version that fits, best mask). */
export function encodeQr(text: string, options: { mask?: number } = {}): QrMatrix {
  const bytes = utf8(text);
  const version = chooseVersion(bytes.length);
  const codewords = interleave(encodeData(bytes, version), version);
  const builder = new Builder(version);
  builder.drawFunctionPatterns();
  builder.drawCodewords(codewords);
  let best = options.mask ?? -1;
  if (best < 0) {
    let bestScore = Infinity;
    for (let mask = 0; mask < 8; mask += 1) {
      builder.applyMask(mask);
      builder.drawFormat(mask);
      const score = penalty(builder.modules);
      if (score < bestScore) {
        bestScore = score;
        best = mask;
      }
      builder.applyMask(mask); // undo (XOR)
    }
  }
  builder.applyMask(best);
  builder.drawFormat(best);
  return { version, size: builder.size, mask: best, modules: builder.modules.map((row) => [...row]) };
}

/** SVG path data for the dark modules, offset by `quiet` modules of margin. */
export function qrPath(matrix: QrMatrix, quiet = 4): string {
  const parts: string[] = [];
  matrix.modules.forEach((row, r) => {
    let c = 0;
    while (c < row.length) {
      if (!row[c]) {
        c += 1;
        continue;
      }
      const start = c;
      while (c < row.length && row[c]) c += 1;
      parts.push(`M${start + quiet} ${r + quiet}h${c - start}v1h${start - c}z`);
    }
  });
  return parts.join("");
}
