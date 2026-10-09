/**
 * Procedural fallback interviewer: a stylised professional bust (suit jacket,
 * shirt collar, short hair) generated entirely in code, with its own skeleton
 * (canonical T-pose, identity rest rotations) and morph targets for visemes,
 * blinks, smiles and brows. Used when the avatar asset cannot be loaded, so
 * it has no external dependencies at all.
 */
import {
  Bone,
  BufferGeometry,
  CanvasTexture,
  Color,
  CylinderGeometry,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  Mesh,
  MeshStandardMaterial,
  Raycaster,
  Skeleton,
  SkinnedMesh,
  SphereGeometry,
  SRGBColorSpace,
  Uint16BufferAttribute,
  Vector3,
  type Material,
  type Object3D,
  type Texture,
  CatmullRomCurve3,
  TubeGeometry,
  BufferAttribute,
  RepeatWrapping,
} from "three";
import { quatFromEulerDeg } from "../core/quat";
import type { HumanBoneName, Side } from "../rig/humanoid";
import { MorphDictionary } from "../rig/morphs";
import { captureReference, measureRig, type AvatarRig, type BoneMap, type FaceWeights } from "../rig/rig";
import {
  addMorphTargets,
  hairGeometry,
  HEAD,
  headGeometry,
  lidGeometry,
  lidPositions,
  loftGeometry,
  mouthGeometries,
  mouthPatchColors,
  mouthPositions,
  NEUTRAL_MOUTH,
  segmentGeometry,
  type MorphShape,
  type MouthParams,
  type ProfileRow,
} from "./geometry";

const PALETTE = {
  skin: 0xd09f82,
  lip: 0xb46c66,
  hair: 0x33241b,
  suit: 0x34405a,
  shirt: 0xeef1f5,
  iris: 0x5a3b26,
  irisRim: 0x24170f,
  sclera: 0xf1ece6,
  teeth: 0xf0ebe2,
  cavity: 0x3a1618,
  lash: 0x231813,
};

/** Mouth parameter sets for every mouth morph (relative to NEUTRAL_MOUTH). */
const MOUTH_SHAPES: Record<string, Partial<MouthParams>> = {
  viseme_PP: { open: 0, pressL: 1, pressR: 1 },
  viseme_FF: { open: 0.0025, tuck: 1 },
  viseme_TH: { open: 0.0045 },
  viseme_DD: { open: 0.006, widenL: 0.0008, widenR: 0.0008 },
  viseme_kk: { open: 0.0068 },
  viseme_CH: { open: 0.005, pucker: 0.45 },
  viseme_SS: { open: 0.0032, widenL: 0.0016, widenR: 0.0016 },
  viseme_nn: { open: 0.0045 },
  viseme_RR: { open: 0.005, pucker: 0.4 },
  viseme_aa: { open: 0.0135, upShare: 0.25 },
  viseme_E: { open: 0.0075, widenL: 0.0026, widenR: 0.0026, liftL: 0.0008, liftR: 0.0008 },
  viseme_I: { open: 0.005, widenL: 0.003, widenR: 0.003, liftL: 0.0012, liftR: 0.0012 },
  viseme_O: { open: 0.0105, pucker: 0.55 },
  viseme_U: { open: 0.0042, pucker: 1 },
  jawOpen: { open: 0.014, upShare: 0.15 },
  mouthSmileLeft: { liftL: 0.0045, widenL: 0.0028 },
  mouthSmileRight: { liftR: 0.0045, widenR: 0.0028 },
  mouthFrownLeft: { liftL: -0.003 },
  mouthFrownRight: { liftR: -0.003 },
  mouthPucker: { pucker: 1, open: 0 },
  mouthFunnel: { pucker: 0.7, open: 0.006 },
  mouthPressLeft: { pressL: 0.8 },
  mouthPressRight: { pressR: 0.8 },
  mouthClose: { open: 0 },
};

/** Torso profile: (height above hips, half width, half depth, superellipse exponent). */
const TORSO_PROFILE: ProfileRow[] = [
  [0.0, 0.152, 0.104, 2.4],
  [0.08, 0.148, 0.1, 2.4],
  [0.16, 0.156, 0.106, 2.5],
  [0.24, 0.168, 0.112, 2.6],
  [0.32, 0.18, 0.115, 2.7],
  [0.38, 0.19, 0.11, 2.8],
  [0.42, 0.198, 0.102, 3.0],
  [0.45, 0.196, 0.093, 3.0],
  [0.47, 0.182, 0.084, 2.7],
  [0.485, 0.15, 0.074, 2.4],
  [0.497, 0.1, 0.062, 2.2],
  [0.507, 0.062, 0.054, 2.0],
];

const HIPS_Y = 1.0;

function linear(hex: number): [number, number, number] {
  const c = new Color(hex);
  return [c.r, c.g, c.b];
}

/** Painted jacket: lapels, shirt V, buttons and a breast pocket in UV space (front centre u = 0.25). */
function createSuitTexture(): Texture | null {
  if (typeof document === "undefined") return null;
  let ctx: CanvasRenderingContext2D | null = null;
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 512;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return null;
  const W = canvas.width;
  const H = canvas.height;
  const X = (u: number) => u * W;
  const Y = (v: number) => (1 - v) * H;
  const suit = "#34405a";
  ctx.fillStyle = suit;
  ctx.fillRect(0, 0, W, H);
  // Subtle fabric texture.
  for (let i = 0; i < 9000; i++) {
    const x = (i * 97.13) % W;
    const y = (i * 53.71 + (i % 7) * 13) % H;
    ctx.fillStyle = i % 2 ? "rgba(255,255,255,0.025)" : "rgba(0,0,0,0.05)";
    ctx.fillRect(x, y, 2, 1);
  }
  const cx = 0.25;
  // Shirt V.
  ctx.fillStyle = "#eef1f5";
  ctx.beginPath();
  ctx.moveTo(X(cx - 0.036), Y(1.0));
  ctx.lineTo(X(cx + 0.036), Y(1.0));
  ctx.lineTo(X(cx + 0.005), Y(0.68));
  ctx.lineTo(X(cx - 0.005), Y(0.68));
  ctx.closePath();
  ctx.fill();
  // Shirt placket shadow + collar shading.
  const grad = ctx.createLinearGradient(0, Y(1.0), 0, Y(0.85));
  grad.addColorStop(0, "rgba(40,48,64,0.35)");
  grad.addColorStop(1, "rgba(40,48,64,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(X(cx - 0.06), Y(1.0), X(0.12), Y(0.85) - Y(1.0));
  ctx.strokeStyle = "rgba(150,160,175,0.6)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(X(cx), Y(0.97));
  ctx.lineTo(X(cx), Y(0.69));
  ctx.stroke();
  // Lapels.
  for (const side of [-1, 1]) {
    ctx.fillStyle = "#2f3a52";
    ctx.beginPath();
    ctx.moveTo(X(cx + side * 0.036), Y(1.0));
    ctx.lineTo(X(cx + side * 0.005), Y(0.68));
    ctx.lineTo(X(cx + side * 0.024), Y(0.72));
    ctx.lineTo(X(cx + side * 0.07), Y(0.88));
    ctx.lineTo(X(cx + side * 0.058), Y(0.91));
    ctx.lineTo(X(cx + side * 0.07), Y(0.975));
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = "rgba(10,14,22,0.55)";
    ctx.lineWidth = 3;
    ctx.stroke();
  }
  // Jacket opening below the V and buttons.
  ctx.strokeStyle = "rgba(10,14,22,0.6)";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(X(cx + 0.004), Y(0.68));
  ctx.lineTo(X(cx + 0.012), Y(0.0));
  ctx.stroke();
  ctx.fillStyle = "#151a24";
  for (const v of [0.47, 0.3]) {
    ctx.beginPath();
    ctx.arc(X(cx + 0.03), Y(v), 6, 0, Math.PI * 2);
    ctx.fill();
  }
  // Breast pocket on the character's left (u < 0.25).
  ctx.strokeStyle = "rgba(10,14,22,0.5)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(X(0.165), Y(0.74));
  ctx.lineTo(X(0.205), Y(0.745));
  ctx.stroke();
  const tex = new CanvasTexture(canvas);
  tex.colorSpace = SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

interface Builder {
  root: Group;
  bones: BoneMap;
  materials: Material[];
  geometries: BufferGeometry[];
  textures: Texture[];
}

function makeBone(b: Builder, name: HumanBoneName, parent: Object3D, x: number, y: number, z: number): Bone {
  const bone = new Bone();
  bone.name = name;
  bone.position.set(x, y, z);
  parent.add(bone);
  b.bones[name] = bone;
  return bone;
}

function attach(b: Builder, parent: Object3D, geometry: BufferGeometry, material: Material, name: string): Mesh {
  const mesh = new Mesh(geometry, material);
  mesh.name = name;
  mesh.frustumCulled = false;
  parent.add(mesh);
  b.geometries.push(geometry);
  return mesh;
}

function buildSkeleton(b: Builder): void {
  const hips = makeBone(b, "hips", b.root, 0, HIPS_Y, 0);
  const spine = makeBone(b, "spine", hips, 0, 0.1, 0);
  const chest = makeBone(b, "chest", spine, 0, 0.12, 0);
  const upper = makeBone(b, "upperChest", chest, 0, 0.12, 0);
  const neck = makeBone(b, "neck", upper, 0, 0.135, -0.008);
  const head = makeBone(b, "head", neck, 0, 0.08, 0.008);
  makeBone(b, "leftEye", head, HEAD.eye.x, 0.085 + HEAD.eye.y, 0.006 + HEAD.eye.z);
  makeBone(b, "rightEye", head, -HEAD.eye.x, 0.085 + HEAD.eye.y, 0.006 + HEAD.eye.z);
  for (const side of ["left", "right"] as Side[]) {
    const s = side === "left" ? 1 : -1;
    const shoulder = makeBone(b, `${side}Shoulder`, upper, 0.022 * s, 0.085, -0.006);
    const arm = makeBone(b, `${side}UpperArm`, shoulder, 0.15 * s, 0.005, 0);
    const fore = makeBone(b, `${side}LowerArm`, arm, 0.27 * s, 0, 0);
    const hand = makeBone(b, `${side}Hand`, fore, 0.245 * s, 0, 0);
    const finger = (name: string, x: number, z: number, lengths: [number, number, number]) => {
      const p = makeBone(b, `${side}${name}Proximal` as HumanBoneName, hand, x * s, 0, z);
      const m = makeBone(b, `${side}${name}Intermediate` as HumanBoneName, p, lengths[0] * s, 0, 0);
      makeBone(b, `${side}${name}Distal` as HumanBoneName, m, lengths[1] * s, 0, 0);
    };
    finger("Index", 0.088, 0.024, [0.042, 0.026, 0.02]);
    finger("Middle", 0.091, 0.004, [0.046, 0.028, 0.021]);
    finger("Ring", 0.087, -0.016, [0.043, 0.026, 0.02]);
    finger("Little", 0.079, -0.034, [0.034, 0.022, 0.017]);
    const t1 = makeBone(b, `${side}ThumbMetacarpal`, hand, 0.022 * s, -0.008, 0.026);
    const t2 = makeBone(b, `${side}ThumbProximal`, t1, 0.03 * s, -0.002, 0.022);
    makeBone(b, `${side}ThumbDistal`, t2, 0.026 * s, -0.002, 0.012);
  }
  b.root.updateMatrixWorld(true);
}

function skinTo(geometry: BufferGeometry, weightsFor: (y: number) => Array<[number, number]>): void {
  const pos = geometry.getAttribute("position") as BufferAttribute;
  const idx = new Uint16Array(pos.count * 4);
  const wts = new Float32Array(pos.count * 4);
  for (let i = 0; i < pos.count; i++) {
    const list = weightsFor(pos.getY(i));
    list.forEach(([bone, w], k) => {
      idx[i * 4 + k] = bone;
      wts[i * 4 + k] = w;
    });
  }
  geometry.setAttribute("skinIndex", new Uint16BufferAttribute(idx, 4));
  geometry.setAttribute("skinWeight", new Float32BufferAttribute(wts, 4));
}

function bandWeights(centres: number[]): (y: number) => Array<[number, number]> {
  return (y) => {
    if (y <= centres[0]!) return [[0, 1]];
    for (let i = 0; i < centres.length - 1; i++) {
      const a = centres[i]!;
      const c = centres[i + 1]!;
      if (y <= c) {
        const t = (y - a) / (c - a);
        const s = t * t * (3 - 2 * t);
        return [
          [i, 1 - s],
          [i + 1, s],
        ];
      }
    }
    return [[centres.length - 1, 1]];
  };
}

/** Surface depth of the head along -Z at (x, y) in head-centre (mesh-local) space, by raycast. */
function headSurfaceZ(head: Mesh, x: number, y: number, fallback: number): number {
  head.updateMatrixWorld(true);
  const origin = head.localToWorld(new Vector3(x, y, 0.3));
  const towards = head.localToWorld(new Vector3(x, y, 0)).sub(origin).normalize();
  const hit = new Raycaster(origin, towards).intersectObject(head, false)[0];
  if (!hit) return fallback;
  return head.worldToLocal(hit.point.clone()).z;
}

/** Fine strand texture for the hair (colour variation + bump), generated in code. */
function createHairTexture(): Texture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return null;
  ctx.fillStyle = "#b0b0b0";
  ctx.fillRect(0, 0, 256, 256);
  let seed = 9871;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  for (let i = 0; i < 1400; i++) {
    const x = rnd() * 256;
    const shade = Math.floor(120 + rnd() * 120);
    ctx.strokeStyle = `rgba(${shade},${shade},${shade},0.55)`;
    ctx.lineWidth = 0.6 + rnd() * 1.2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.bezierCurveTo(x + (rnd() - 0.5) * 10, 85, x + (rnd() - 0.5) * 10, 170, x + (rnd() - 0.5) * 6, 256);
    ctx.stroke();
  }
  const tex = new CanvasTexture(canvas);
  tex.wrapS = RepeatWrapping;
  tex.wrapT = RepeatWrapping;
  tex.repeat.set(6, 1);
  return tex;
}

export interface ProceduralOptions {
  name?: string;
}

export function createProceduralRig(options: ProceduralOptions = {}): AvatarRig {
  const root = new Group();
  root.name = "procedural-avatar";
  const b: Builder = { root, bones: {}, materials: [], geometries: [], textures: [] };
  buildSkeleton(b);
  const bones = b.bones;

  // ---- materials
  // MeshStandardMaterial keeps the fallback cheap (it exists for weak devices too).
  const skin = new MeshStandardMaterial({ color: PALETTE.skin, roughness: 0.62 });
  const skinVertex = skin.clone();
  skinVertex.color = new Color(0xffffff);
  skinVertex.vertexColors = true;
  skinVertex.side = DoubleSide;
  const suitTexture = createSuitTexture();
  if (suitTexture) b.textures.push(suitTexture);
  const suit = new MeshStandardMaterial({ color: suitTexture ? 0xffffff : PALETTE.suit, map: suitTexture, roughness: 0.82 });
  const sleeve = new MeshStandardMaterial({ color: PALETTE.suit, roughness: 0.8 });
  const shirt = new MeshStandardMaterial({ color: PALETTE.shirt, roughness: 0.62, side: DoubleSide });
  const hairTexture = createHairTexture();
  if (hairTexture) b.textures.push(hairTexture);
  const hair = new MeshStandardMaterial({
    color: PALETTE.hair,
    roughness: 0.66,
    map: hairTexture,
    bumpMap: hairTexture,
    bumpScale: 0.6,
  });
  const brow = new MeshStandardMaterial({ color: 0x3a2a20, roughness: 0.8 });
  const sclera = new MeshStandardMaterial({ color: PALETTE.sclera, roughness: 0.18 });
  const iris = new MeshStandardMaterial({ color: 0xffffff, vertexColors: true, roughness: 0.12 });
  const pupil = new MeshStandardMaterial({ color: 0x060404, roughness: 0.08 });
  const teeth = new MeshStandardMaterial({ color: PALETTE.teeth, roughness: 0.32, side: DoubleSide });
  const cavity = new MeshStandardMaterial({ color: PALETTE.cavity, roughness: 0.9, side: DoubleSide });
  const nostril = new MeshStandardMaterial({ color: 0x4a2a24, roughness: 0.9 });
  b.materials.push(skin, skinVertex, suit, sleeve, shirt, hair, brow, sclera, iris, pupil, teeth, cavity, nostril);

  // ---- torso (skinned to hips/spine/chest/upperChest) and neck
  const torso = loftGeometry(TORSO_PROFILE, 44, 72, true);
  torso.geometry.translate(0, HIPS_Y, 0);
  skinTo(torso.geometry, bandWeights([HIPS_Y, HIPS_Y + 0.1, HIPS_Y + 0.22, HIPS_Y + 0.34]));
  const torsoMesh = new SkinnedMesh(torso.geometry, suit);
  torsoMesh.name = "torso";
  torsoMesh.frustumCulled = false;
  root.add(torsoMesh);
  b.geometries.push(torso.geometry);
  torsoMesh.bind(new Skeleton([bones.hips as Bone, bones.spine as Bone, bones.chest as Bone, bones.upperChest as Bone]));

  const neckGeo = new CylinderGeometry(0.044, 0.052, 0.13, 32, 8, true);
  neckGeo.translate(0, 1.535, -0.004);
  skinTo(neckGeo, bandWeights([1.49, 1.52, 1.585]));
  const neckMesh = new SkinnedMesh(neckGeo, skin);
  neckMesh.name = "neck";
  neckMesh.frustumCulled = false;
  root.add(neckMesh);
  b.geometries.push(neckGeo);
  neckMesh.bind(new Skeleton([bones.upperChest as Bone, bones.neck as Bone, bones.head as Bone]));

  // Shirt collar around the neck base, open at the front.
  const collar = new CylinderGeometry(0.056, 0.061, 0.032, 40, 1, true, Math.PI * 0.16, Math.PI * 1.68);
  collar.translate(0, 0.158, -0.006);
  attach(b, bones.upperChest!, collar, shirt, "collar");

  // ---- head
  const headGroup = new Group();
  headGroup.name = "head-centre";
  headGroup.position.set(0, 0.085, 0.006);
  bones.head!.add(headGroup);
  const headMesh = attach(b, headGroup, headGeometry(), skin, "head");
  attach(b, headGroup, hairGeometry(), hair, "hair");
  root.updateMatrixWorld(true);

  // Ears.
  for (const s of [1, -1]) {
    const ear = new SphereGeometry(1, 16, 12);
    ear.scale(0.009, 0.027, 0.017);
    const m = attach(b, headGroup, ear, skin, "ear");
    m.position.set(s * 0.071, 0.004, -0.01);
    m.rotation.y = s * 0.35;
  }

  // Nose: bridge to a soft tip, plus nostrils.
  const noseGeo = new SphereGeometry(1, 24, 18);
  {
    const pos = noseGeo.getAttribute("position") as BufferAttribute;
    for (let i = 0; i < pos.count; i++) {
      let x = pos.getX(i);
      const y = pos.getY(i);
      let z = pos.getZ(i);
      if (y < -0.2) {
        // Alar width at the base, a soft rounded tip.
        const k = Math.min(1, (-y - 0.2) / 0.6);
        x *= 1 + 0.6 * k;
        z *= 1 + 0.25 * k;
      }
      if (y > 0.3) x *= 1 - 0.3 * Math.min(1, (y - 0.3) / 0.7);
      pos.setXYZ(i, x * 0.0085, y * 0.0175, z * 0.0105);
    }
    noseGeo.computeVertexNormals();
  }
  const noseZ = headSurfaceZ(headMesh, 0, -0.004, 0.085);
  const nose = attach(b, headGroup, noseGeo, skin, "nose");
  nose.position.set(0, -0.006, noseZ + 0.002);
  nose.rotation.x = -0.24;
  // Nostrils: dark, flattened, tucked into the underside of the nose tip.
  for (const s of [1, -1]) {
    const n = attach(b, headGroup, new SphereGeometry(0.003, 10, 8), nostril, "nostril");
    n.scale.set(1.1, 0.42, 0.9);
    n.position.set(s * 0.0046, -0.0226, noseZ + 0.0058);
  }

  // ---- eyes (on eye bones, so gaze rotates them) and lids (on the head)
  const irisColors = (geo: BufferGeometry) => {
    const pos = geo.getAttribute("position") as BufferAttribute;
    const colors = new Float32Array(pos.count * 3);
    const inner = linear(0x7b5234);
    const rim = linear(PALETTE.irisRim);
    const mid = linear(PALETTE.iris);
    const maxR = 0.0118 * Math.sin(Math.PI * 0.17);
    for (let i = 0; i < pos.count; i++) {
      const r = Math.hypot(pos.getX(i), pos.getY(i)) / maxR;
      const c = r < 0.6 ? inner.map((v, k) => v + (mid[k]! - v) * (r / 0.6)) : mid.map((v, k) => v + (rim[k]! - v) * ((r - 0.6) / 0.4));
      colors.set(c, i * 3);
    }
    geo.setAttribute("color", new Float32BufferAttribute(colors, 3));
  };
  for (const side of ["left", "right"] as Side[]) {
    const eyeBone = bones[`${side}Eye`]!;
    attach(b, eyeBone, new SphereGeometry(0.0118, 32, 24), sclera, "sclera");
    const irisGeo = new SphereGeometry(0.01188, 32, 6, 0, Math.PI * 2, 0, Math.PI * 0.17);
    irisGeo.rotateX(Math.PI / 2);
    irisColors(irisGeo);
    attach(b, eyeBone, irisGeo, iris, "iris");
    const pupilGeo = new SphereGeometry(0.01193, 24, 4, 0, Math.PI * 2, 0, Math.PI * 0.07);
    pupilGeo.rotateX(Math.PI / 2);
    attach(b, eyeBone, pupilGeo, pupil, "pupil");

    const S = side === "left" ? "Left" : "Right";
    const eyePos = new Vector3((side === "left" ? 1 : -1) * HEAD.eye.x, HEAD.eye.y, HEAD.eye.z);
    const R = 0.0128;
    const upper = lidGeometry(R, 0, Math.PI * 0.39);
    const upperShapes: MorphShape[] = [
      { name: `eyeBlink${S}`, positions: lidPositions(R, 0, Math.PI * 0.62) },
      { name: `eyeSquint${S}`, positions: lidPositions(R, 0, Math.PI * 0.45) },
      { name: `eyeWide${S}`, positions: lidPositions(R, 0, Math.PI * 0.33) },
      { name: `eyeLookUp${S}`, positions: lidPositions(R, 0, Math.PI * 0.345) },
      { name: `eyeLookDown${S}`, positions: lidPositions(R, 0, Math.PI * 0.47) },
    ];
    addMorphTargets(upper, upperShapes);
    {
      const pos = upper.getAttribute("position") as BufferAttribute;
      const colors = new Float32Array(pos.count * 3);
      const skinC = linear(PALETTE.skin);
      const lashC = linear(PALETTE.lash);
      const rows = 11; // heightSegments + 1
      const cols = pos.count / rows;
      for (let i = 0; i < pos.count; i++) {
        const row = Math.floor(i / cols);
        const t = row >= rows - 1 ? 1 : row === rows - 2 ? 0.45 : 0;
        colors.set(skinC.map((v, k) => v + (lashC[k]! - v) * t), i * 3);
      }
      upper.setAttribute("color", new Float32BufferAttribute(colors, 3));
    }
    const upperMesh = attach(b, headGroup, upper, skinVertex, `lid-upper-${side}`);
    upperMesh.position.copy(eyePos);
    upperMesh.updateMorphTargets();

    const lowerStart = Math.PI * 0.68;
    const lower = lidGeometry(R * 0.995, lowerStart, Math.PI - lowerStart);
    addMorphTargets(lower, [
      { name: `eyeBlink${S}`, positions: lidPositions(R * 0.995, Math.PI * 0.66, Math.PI * 0.34) },
      { name: `eyeSquint${S}`, positions: lidPositions(R * 0.995, Math.PI * 0.63, Math.PI * 0.37) },
      { name: `cheekSquint${S}`, positions: lidPositions(R * 0.995, Math.PI * 0.665, Math.PI * 0.335) },
    ]);
    const lowerMesh = attach(b, headGroup, lower, skin, `lid-lower-${side}`);
    lowerMesh.position.copy(eyePos);
    lowerMesh.updateMorphTargets();

    // Brow: tapered, flattened tube following the forehead.
    const s = side === "left" ? 1 : -1;
    const browPts = [
      [0.011, 0.047],
      [0.022, 0.0525],
      [0.035, 0.054],
      [0.051, 0.047],
    ].map(([x, y]) => new Vector3(s * x!, y! + 0.002, headSurfaceZ(headMesh, s * x!, y! + 0.002, 0.08) + 0.001));
    const curve = new CatmullRomCurve3(browPts);
    const tubular = 24;
    const radial = 8;
    const browGeo = new TubeGeometry(curve, tubular, 0.0015, radial, false);
    {
      const pos = browGeo.getAttribute("position") as BufferAttribute;
      const centre = new Vector3();
      const p = new Vector3();
      for (let i = 0; i < pos.count; i++) {
        const ring = Math.floor(i / (radial + 1));
        const u = ring / tubular;
        curve.getPointAt(u, centre);
        p.fromBufferAttribute(pos, i).sub(centre);
        const taper = 1 - 0.6 * u;
        p.multiplyScalar(taper);
        p.z *= 0.4;
        p.add(centre);
        pos.setXYZ(i, p.x, p.y, p.z);
      }
      browGeo.computeVertexNormals();
      const base = pos.array as Float32Array;
      const shape = (fn: (u: number) => [number, number, number]) => {
        const out = new Float32Array(base);
        for (let i = 0; i < pos.count; i++) {
          const u = Math.floor(i / (radial + 1)) / tubular;
          const [dx, dy, dz] = fn(u);
          out[i * 3] = base[i * 3]! + dx;
          out[i * 3 + 1] = base[i * 3 + 1]! + dy;
          out[i * 3 + 2] = base[i * 3 + 2]! + dz;
        }
        return out;
      };
      addMorphTargets(browGeo, [
        { name: "browInnerUp", positions: shape((u) => [0, 0.0062 * (1 - u) ** 1.4, 0.0008 * (1 - u)]) },
        { name: `browOuterUp${S}`, positions: shape((u) => [0, 0.005 * u ** 1.5, 0]) },
        { name: `browDown${S}`, positions: shape((u) => [-s * 0.0018 * (1 - u), -0.0038 - 0.0012 * (1 - u), -0.0008]) },
      ]);
    }
    const browMesh = attach(b, headGroup, browGeo, brow, `brow-${side}`);
    browMesh.updateMorphTargets();
  }

  // ---- mouth: morphing patch (skin ring + lips), cavity and teeth
  const neutral = mouthPositions(NEUTRAL_MOUTH);
  const mouth = mouthGeometries(neutral);
  const shapes = Object.entries(MOUTH_SHAPES).map(([name, params]) => ({ name, buffers: mouthPositions({ ...NEUTRAL_MOUTH, ...params }) }));
  addMorphTargets(mouth.patch, shapes.map((s) => ({ name: s.name, positions: s.buffers.patch })));
  addMorphTargets(mouth.cavity, shapes.map((s) => ({ name: s.name, positions: s.buffers.cavity })));
  addMorphTargets(mouth.teeth, shapes.map((s) => ({ name: s.name, positions: s.buffers.teeth })));
  mouth.patch.setAttribute("color", new Float32BufferAttribute(mouthPatchColors(linear(PALETTE.lip), linear(PALETTE.skin)), 3));
  const cavityMesh = attach(b, headGroup, mouth.cavity, cavity, "mouth-cavity");
  const teethMesh = attach(b, headGroup, mouth.teeth, teeth, "teeth");
  const patchMesh = attach(b, headGroup, mouth.patch, skinVertex, "mouth");
  for (const m of [cavityMesh, teethMesh, patchMesh]) m.updateMorphTargets();
  cavityMesh.renderOrder = 0;
  teethMesh.renderOrder = 1;
  patchMesh.renderOrder = 2;

  // ---- arms and hands (rigid segments on bones)
  for (const side of ["left", "right"] as Side[]) {
    const s = side === "left" ? 1 : -1;
    attach(b, bones[`${side}UpperArm`]!, new SphereGeometry(0.046, 24, 16), sleeve, "deltoid");
    attach(b, bones[`${side}UpperArm`]!, segmentGeometry(new Vector3(0.27 * s, 0, 0), 0.044, 0.041, 20), sleeve, "upper-arm");
    attach(b, bones[`${side}LowerArm`]!, segmentGeometry(new Vector3(0.215 * s, 0, 0), 0.041, 0.036, 20), sleeve, "forearm");
    const cuff = new CylinderGeometry(0.0352, 0.0352, 0.02, 24, 1, true);
    cuff.rotateZ(Math.PI / 2);
    cuff.translate(0.228 * s, 0, 0);
    attach(b, bones[`${side}LowerArm`]!, cuff, shirt, "cuff");
    attach(b, bones[`${side}LowerArm`]!, segmentGeometry(new Vector3(0.03 * s, 0, 0), 0.026, 0.029, 16).translate(0.222 * s, 0, 0), skin, "wrist");

    const palm = new SphereGeometry(1, 24, 16);
    palm.scale(0.05, 0.016, 0.04);
    palm.translate(0.045 * s, 0, 0.002);
    attach(b, bones[`${side}Hand`]!, palm, skin, "palm");
    for (const finger of ["Index", "Middle", "Ring", "Little"] as const) {
      const r = finger === "Little" ? 0.0074 : 0.0085;
      const p = bones[`${side}${finger}Proximal`]!;
      const m = bones[`${side}${finger}Intermediate`]!;
      const d = bones[`${side}${finger}Distal`]!;
      attach(b, p, segmentGeometry(m.position.clone(), r, r * 0.94, 12), skin, "finger");
      attach(b, m, segmentGeometry(d.position.clone(), r * 0.93, r * 0.86, 12), skin, "finger");
      attach(b, d, segmentGeometry(new Vector3((finger === "Little" ? 0.017 : 0.02) * s, 0, 0), r * 0.85, r * 0.72, 12), skin, "finger");
    }
    const t1 = bones[`${side}ThumbMetacarpal`]!;
    const t2 = bones[`${side}ThumbProximal`]!;
    const t3 = bones[`${side}ThumbDistal`]!;
    attach(b, t1, segmentGeometry(t2.position.clone(), 0.012, 0.0105, 12), skin, "thumb");
    attach(b, t2, segmentGeometry(t3.position.clone(), 0.0102, 0.0094, 12), skin, "thumb");
    attach(b, t3, segmentGeometry(new Vector3(0.019 * s, -0.001, 0.008), 0.0092, 0.008, 12), skin, "thumb");
  }

  root.updateMatrixWorld(true);
  const reference = captureReference(root, bones);
  const metrics = measureRig(root, bones);
  const morphs = MorphDictionary.fromObject(root);
  const gaze = quatFromEulerDeg([0, 0, 0]);

  return {
    kind: "procedural",
    name: options.name ?? "procedural",
    root,
    bones,
    basis: reference.basis,
    referenceWorld: reference.referenceWorld,
    metrics,
    morphs,
    gazeMode: "bones",
    applyFace(weights: Readonly<FaceWeights>) {
      for (const name in weights) morphs.set(name, weights[name]!);
    },
    setGaze(yaw: number, pitch: number, _target, vergence = 0) {
      for (const side of ["leftEye", "rightEye"] as const) {
        const eyeYaw = side === "leftEye" ? yaw - vergence : yaw + vergence;
        quatFromEulerDeg([(-pitch * 180) / Math.PI, (eyeYaw * 180) / Math.PI, 0], "YXZ", gaze);
        // Identity rest rotations: the canonical rotation is the local rotation.
        bones[side]!.quaternion.copy(gaze);
      }
    },
    update() {
      /* bones are driven directly */
    },
    dispose() {
      for (const g of b.geometries) g.dispose();
      for (const m of b.materials) m.dispose();
      for (const t of b.textures) t.dispose();
    },
  };
}
