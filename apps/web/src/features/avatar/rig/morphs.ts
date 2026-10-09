/**
 * Morph target vocabulary (ARKit 52 blendshapes + Oculus visemes) and a
 * dictionary that fans one logical morph out to every mesh carrying it.
 */
import type { Mesh, Object3D } from "three";

export const ARKIT_BLENDSHAPES = [
  "eyeBlinkLeft",
  "eyeLookDownLeft",
  "eyeLookInLeft",
  "eyeLookOutLeft",
  "eyeLookUpLeft",
  "eyeSquintLeft",
  "eyeWideLeft",
  "eyeBlinkRight",
  "eyeLookDownRight",
  "eyeLookInRight",
  "eyeLookOutRight",
  "eyeLookUpRight",
  "eyeSquintRight",
  "eyeWideRight",
  "jawForward",
  "jawLeft",
  "jawRight",
  "jawOpen",
  "mouthClose",
  "mouthFunnel",
  "mouthPucker",
  "mouthLeft",
  "mouthRight",
  "mouthSmileLeft",
  "mouthSmileRight",
  "mouthFrownLeft",
  "mouthFrownRight",
  "mouthDimpleLeft",
  "mouthDimpleRight",
  "mouthStretchLeft",
  "mouthStretchRight",
  "mouthRollLower",
  "mouthRollUpper",
  "mouthShrugLower",
  "mouthShrugUpper",
  "mouthPressLeft",
  "mouthPressRight",
  "mouthLowerDownLeft",
  "mouthLowerDownRight",
  "mouthUpperUpLeft",
  "mouthUpperUpRight",
  "browDownLeft",
  "browDownRight",
  "browInnerUp",
  "browOuterUpLeft",
  "browOuterUpRight",
  "cheekPuff",
  "cheekSquintLeft",
  "cheekSquintRight",
  "noseSneerLeft",
  "noseSneerRight",
  "tongueOut",
] as const;

export type ArkitBlendshape = (typeof ARKIT_BLENDSHAPES)[number];

/** Oculus viseme ids as sent by the server (sil PP FF TH DD kk CH SS nn RR aa E I O U). */
export const VISEME_IDS = ["sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"] as const;
export type VisemeId = (typeof VISEME_IDS)[number];

/** Morph names for the visemes (sil has no shape: all visemes at zero). */
export const VISEME_MORPHS: Record<Exclude<VisemeId, "sil">, string> = {
  PP: "viseme_PP",
  FF: "viseme_FF",
  TH: "viseme_TH",
  DD: "viseme_DD",
  kk: "viseme_kk",
  CH: "viseme_CH",
  SS: "viseme_SS",
  nn: "viseme_nn",
  RR: "viseme_RR",
  aa: "viseme_aa",
  E: "viseme_E",
  I: "viseme_I",
  O: "viseme_O",
  U: "viseme_U",
};

export const VISEME_MORPH_NAMES = Object.values(VISEME_MORPHS);

const CANONICAL: Map<string, string> = new Map(
  [...ARKIT_BLENDSHAPES, ...VISEME_MORPH_NAMES, "viseme_sil"].map((n) => [aliasKey(n), n]),
);

function aliasKey(name: string): string {
  return name
    .replace(/[_\-\s.]l$/i, "left")
    .replace(/[_\-\s.]r$/i, "right")
    .replace(/[^a-z0-9]/gi, "")
    .toLowerCase();
}

/**
 * Canonical morph name for a raw target name: strips "mesh." prefixes
 * ("blendShape1.jawOpen"), tolerates case/separator differences
 * ("JawOpen", "jaw_open", "eyeBlink_L"). Unknown names are returned as-is.
 */
export function canonicalMorphName(raw: string): string {
  const dot = raw.lastIndexOf(".");
  const base = dot >= 0 ? raw.slice(dot + 1) : raw;
  return CANONICAL.get(aliasKey(base)) ?? base;
}

export interface MorphBinding {
  mesh: Mesh;
  index: number;
}

export interface MorphDictionaryOptions {
  /** Return true to skip binding `name` on `mesh` (e.g. eyeball meshes driven by bones). */
  exclude?: (mesh: Mesh, name: string) => boolean;
}

export class MorphDictionary {
  private readonly map = new Map<string, MorphBinding[]>();
  private readonly values = new Map<string, number>();

  static fromObject(root: Object3D, options: MorphDictionaryOptions = {}): MorphDictionary {
    const dict = new MorphDictionary();
    root.traverse((obj) => {
      const mesh = obj as Mesh;
      if (!mesh.isMesh || !mesh.morphTargetDictionary || !mesh.morphTargetInfluences) return;
      for (const [rawName, index] of Object.entries(mesh.morphTargetDictionary)) {
        const name = canonicalMorphName(rawName);
        if (options.exclude?.(mesh, name)) continue;
        dict.bind(name, mesh, index);
      }
    });
    return dict;
  }

  bind(name: string, mesh: Mesh, index: number): void {
    const list = this.map.get(name);
    if (list) {
      if (!list.some((b) => b.mesh === mesh && b.index === index)) list.push({ mesh, index });
    } else this.map.set(name, [{ mesh, index }]);
  }

  has(name: string): boolean {
    return this.map.has(name);
  }

  names(): string[] {
    return [...this.map.keys()].sort();
  }

  /** Meshes carrying a morph, for diagnostics. */
  bindings(name: string): readonly MorphBinding[] {
    return this.map.get(name) ?? [];
  }

  /** Set a morph on every mesh that has it. Returns false when unknown. */
  set(name: string, weight: number): boolean {
    const list = this.map.get(name);
    if (!list) return false;
    const w = !Number.isFinite(weight) || weight < 0 ? 0 : weight > 1 ? 1 : weight;
    const prev = this.values.get(name);
    if (prev !== undefined && Math.abs(prev - w) < 1e-4) return true;
    this.values.set(name, w);
    for (const { mesh, index } of list) {
      const influences = mesh.morphTargetInfluences;
      if (influences && index < influences.length) influences[index] = w;
    }
    return true;
  }

  get(name: string): number {
    return this.values.get(name) ?? 0;
  }

  reset(): void {
    for (const name of this.map.keys()) this.set(name, 0);
  }
}
