/**
 * Viseme id normalisation and a grapheme-based text-to-viseme approximation
 * used when the browser's speechSynthesis provides only word boundaries.
 */
import { VISEME_IDS, type VisemeId } from "../rig/morphs";

const LOOKUP: Map<string, VisemeId> = new Map(VISEME_IDS.map((v) => [v.toLowerCase(), v]));

/** Accepts "aa", "AA", "viseme_aa", "sil"...; returns null for unknown ids. */
export function normalizeViseme(raw: string): VisemeId | null {
  if (!raw) return null;
  const key = raw.trim().toLowerCase().replace(/^viseme_/, "");
  return LOOKUP.get(key) ?? null;
}

export interface VisemeStep {
  v: VisemeId;
  /** Peak weight 0..1. */
  w: number;
  /** Relative duration weight. */
  d: number;
}

const VOWEL: Record<string, VisemeId> = { a: "aa", e: "E", i: "I", o: "O", u: "U", y: "I" };

const CONSONANT: Record<string, VisemeId> = {
  b: "PP",
  m: "PP",
  p: "PP",
  f: "FF",
  v: "FF",
  t: "DD",
  d: "DD",
  k: "kk",
  g: "kk",
  c: "kk",
  q: "kk",
  x: "kk",
  s: "SS",
  z: "SS",
  n: "nn",
  l: "nn",
  r: "RR",
  j: "CH",
  w: "U",
};

const DIGRAPH: Record<string, VisemeId> = {
  th: "TH",
  sh: "CH",
  ch: "CH",
  ph: "FF",
  ck: "kk",
  ng: "nn",
  qu: "kk",
  wh: "U",
  ee: "I",
  ea: "I",
  oo: "U",
  ou: "O",
  ow: "O",
  oa: "O",
  ai: "E",
  ay: "E",
};

function isVowelViseme(v: VisemeId): boolean {
  return v === "aa" || v === "E" || v === "I" || v === "O" || v === "U";
}

/**
 * Approximate a word's visemes from its spelling. Good enough to make the
 * mouth move in time with browser TTS; real timelines come from the server.
 */
export function textToVisemes(word: string): VisemeStep[] {
  const text = word
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z]/g, "");
  if (!text) return [];
  const steps: VisemeStep[] = [];
  let i = 0;
  while (i < text.length) {
    const pair = text.slice(i, i + 2);
    let v: VisemeId | undefined;
    let used = 1;
    if (pair.length === 2 && DIGRAPH[pair]) {
      v = DIGRAPH[pair];
      used = 2;
    } else {
      const ch = text[i]!;
      if (ch === "h") {
        i += 1; // mostly silent / aspirated: keep the previous shape
        continue;
      }
      if (ch === "e" && i === text.length - 1 && i > 1) {
        i += 1; // silent final e ("make")
        continue;
      }
      if (ch === "y" && i === 0) v = "I";
      else v = VOWEL[ch] ?? CONSONANT[ch];
    }
    i += used;
    if (!v) continue;
    const vowel = isVowelViseme(v);
    const prev = steps[steps.length - 1];
    if (prev && prev.v === v) {
      prev.d += vowel ? 0.6 : 0.3;
      continue;
    }
    steps.push({ v, w: vowel ? 0.95 : v === "PP" || v === "FF" ? 0.9 : 0.65, d: vowel ? 1 : 0.6 });
  }
  return steps;
}

/** Rough spoken duration for a word when the engine gives no timing (ms). */
export function estimateWordDurationMs(word: string): number {
  const letters = word.replace(/[^\p{L}\p{N}]/gu, "").length;
  return Math.max(120, Math.min(900, 70 * letters + 60));
}
