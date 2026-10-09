/**
 * GLB rig adapter: Mixamo / Ready Player Me / MPFB-style skeletons with ARKit +
 * Oculus viseme morph targets spread across meshes.
 */
import {
  Color,
  DoubleSide,
  FrontSide,
  type Material,
  type Mesh,
  MeshStandardMaterial,
  type Object3D,
  Quaternion,
  ShaderChunk,
  type SkinnedMesh,
  type Texture,
} from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { applyBasis, quatFromEulerDeg } from "../core/quat";
import { isHumanBoneName, normalizeBoneName, type HumanBoneName } from "./humanoid";
import { MorphDictionary } from "./morphs";
import {
  captureReference,
  measureRig,
  poseToCanonicalTPose,
  snapshotPose,
  type AvatarRig,
  type BoneMap,
  type FaceWeights,
} from "./rig";

const EYE_LOOK = /^eyeLook(Up|Down|In|Out)(Left|Right)$/;

/** Share of a skinned mesh's total skin weight carried by the given bones. */
function weightShare(mesh: SkinnedMesh, bones: ReadonlySet<Object3D>): number {
  const skinIndex = mesh.geometry.getAttribute("skinIndex");
  const skinWeight = mesh.geometry.getAttribute("skinWeight");
  const skeletonBones = mesh.skeleton?.bones;
  if (!skinIndex || !skinWeight || !skeletonBones) return 0;
  let total = 0;
  let hit = 0;
  const step = Math.max(1, Math.floor(skinIndex.count / 2000));
  for (let i = 0; i < skinIndex.count; i += step) {
    for (let k = 0; k < 4; k++) {
      const w = skinWeight.getComponent(i, k);
      if (w <= 0) continue;
      total += w;
      const bone = skeletonBones[skinIndex.getComponent(i, k)];
      if (bone && bones.has(bone)) hit += w;
    }
  }
  return total > 0 ? hit / total : 0;
}

function materialsOf(mesh: Mesh): Material[] {
  return Array.isArray(mesh.material) ? mesh.material : [mesh.material];
}

/** Opacity of the strand brows over the brows painted into the skin (optimize-avatar). */
export const BROW_STRAND_OPACITY = 0.38;
/** Opacity of the strand lashes (thin strands alias into scribbles when opaque). */
export const LASH_STRAND_OPACITY = 0.8;

const STOCK_DIRECT_DIFFUSE = "reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );";

/**
 * Skin shading: per-channel wrap lighting for direct lights, a cheap stand-in
 * for subsurface scattering. Light bleeds past the terminator, more in red
 * than in blue, so shadow edges turn warm and soft instead of grey. The lit
 * side is unchanged (the wrap is normalised). Falls back to stock shading if
 * the three.js chunk ever changes.
 */
export function applySkinShading(material: MeshStandardMaterial): boolean {
  const chunk = ShaderChunk.lights_physical_pars_fragment;
  if (typeof chunk !== "string" || !chunk.includes(STOCK_DIRECT_DIFFUSE)) return false;
  const patched = chunk.replace(
    STOCK_DIRECT_DIFFUSE,
    "vec3 skinWrap = saturate( ( dot( geometryNormal, directLight.direction ) + SKIN_WRAP ) / ( 1.0 + SKIN_WRAP ) );\n\treflectedLight.directDiffuse += skinWrap * directLight.color * BRDF_Lambert( material.diffuseColor );",
  );
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader =
      "#define SKIN_WRAP vec3( 0.3, 0.13, 0.085 )\n" + shader.fragmentShader.replace("#include <lights_physical_pars_fragment>", patched);
  };
  material.customProgramCacheKey = () => "avatar-skin-wrap-v1";
  return true;
}

export type MaterialRole = "brow" | "lash" | "eye" | "teeth" | "tongue" | "hair" | "skin" | "cloth";

const ROLE_PATTERNS: ReadonlyArray<readonly [MaterialRole, RegExp]> = [
  ["lash", /lash/],
  ["brow", /brow/],
  ["eye", /(eye|high-poly|cornea|iris)/],
  ["teeth", /(teeth|tooth)/],
  ["tongue", /tongue/],
  ["hair", /(hair|ponytail|bun)/],
  ["skin", /(body|skin|head|face|^base$)/],
];

/**
 * What a mesh's material renders. The mesh name decides: optimisers merge
 * identical materials, so a material name alone can be shared by several
 * meshes (the brow and lash strands were once one material). The material
 * name only helps when the mesh name says nothing.
 */
export function materialRole(meshName: string, materialName: string): MaterialRole {
  for (const name of [meshName.toLowerCase(), materialName.toLowerCase()]) {
    for (const [role, pattern] of ROLE_PATTERNS) if (pattern.test(name)) return role;
  }
  return "cloth";
}

/**
 * Light-touch material tuning for consistent studio rendering: physically
 * plausible roughness, skin wrap shading, translucent strand brows/lashes
 * over the brows painted into the skin, alpha-to-coverage for alpha-tested
 * hair so edges stay smooth with MSAA.
 */
function tuneMaterial(material: Material, role: MaterialRole): void {
  const m = material as MeshStandardMaterial;
  if (!(m instanceof MeshStandardMaterial)) return;
  if (role === "brow" || role === "lash") {
    // Thin strand geometry aliases into black scribbles at low pixel ratios.
    // The brow shape is painted into the skin; the strands only add a soft
    // hair texture on top, so they render translucent and never write depth.
    const lash = role === "lash";
    if (!m.map) m.color = new Color(lash ? 0x241913 : 0x3a281e);
    m.emissive = new Color(0x000000);
    m.transparent = true;
    m.opacity = lash ? LASH_STRAND_OPACITY : BROW_STRAND_OPACITY;
    m.depthWrite = false;
    m.roughness = 0.8;
    m.side = DoubleSide;
    // No back-then-front double pass for transparent double-sided strands
    // (they write no depth; the extra pass doubled their 43K triangles).
    m.forceSinglePass = true;
  } else if (role === "eye") {
    m.roughness = 0.12;
    m.envMapIntensity = 1.35;
  } else if (role === "teeth") {
    // Inside the mouth the teeth only see fill light; a little self-illumination
    // from their own texture keeps them from reading grey.
    m.roughness = 0.3;
    m.emissive = new Color(0x2a2724);
    if (m.map) m.emissiveMap = m.map;
  } else if (role === "tongue") {
    m.roughness = 0.5;
  } else if (role === "hair") {
    m.roughness = 0.62;
    if (m.alphaTest > 0 || m.transparent) {
      m.alphaToCoverage = true;
      m.transparent = false;
      m.depthWrite = true;
    }
    m.side = DoubleSide;
  } else if (role === "skin") {
    // A roughness map (optimize-avatar) keeps skin satin and the painted
    // sleeves matte; without one, a uniform satin skin.
    m.roughness = m.roughnessMap ? 1 : 0.56;
    m.metalness = 0;
    m.envMapIntensity = 0.75;
    applySkinShading(m);
    // Opaque body never needs back faces; halves fill cost on the largest mesh.
    if (m.alphaTest === 0 && !m.transparent) m.side = FrontSide;
  } else {
    // Outfit (blazer, shirt, trousers): matte fabric.
    m.roughness = Math.max(m.roughness, 0.78);
    m.metalness = Math.min(m.metalness, 0.05);
    m.envMapIntensity = 0.8;
  }
  m.needsUpdate = true;
}

export interface GlbRigOptions {
  name?: string;
}

export function createGlbRig(gltf: GLTF, options: GlbRigOptions = {}): AvatarRig {
  const root = gltf.scene;
  root.updateMatrixWorld(true);

  // ---- humanoid bones (first match wins; prefer actual Bones over helper nodes)
  const bones: BoneMap = {};
  root.traverse((obj) => {
    const canonical = normalizeBoneName(obj.name);
    if (!canonical || !isHumanBoneName(canonical)) return;
    const existing = bones[canonical];
    if (!existing || (!(existing as { isBone?: boolean }).isBone && (obj as { isBone?: boolean }).isBone)) {
      bones[canonical] = obj;
    }
  });
  const required: HumanBoneName[] = ["hips", "spine", "neck", "head", "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm"];
  const missing = required.filter((b) => !bones[b]);
  if (missing.length) throw new Error(`avatar rig is missing bones: ${missing.join(", ")}`);
  if (!bones.chest && bones.upperChest) bones.chest = bones.upperChest;

  // ---- meshes: culling, materials, eye-bone detection for morph exclusion
  const eyeBones = new Set<Object3D>([bones.leftEye, bones.rightEye].filter(Boolean) as Object3D[]);
  const eyeballMeshes = new Set<Mesh>();
  const ownedTextures = new Set<Texture>();
  // One tuned material per (source material, role): a material shared by
  // meshes with different roles is cloned so each role keeps its own look.
  const tuned = new Map<Material, Map<MaterialRole, Material>>();
  const tunedFor = (mesh: Mesh, source: Material): Material => {
    const role = materialRole(mesh.name, source.name);
    let byRole = tuned.get(source);
    if (!byRole) {
      byRole = new Map();
      tuned.set(source, byRole);
    }
    let material = byRole.get(role);
    if (!material) {
      material = byRole.size === 0 ? source : source.clone();
      tuneMaterial(material, role);
      byRole.set(role, material);
    }
    return material;
  };
  root.traverse((obj) => {
    const mesh = obj as Mesh;
    if (!mesh.isMesh) return;
    mesh.frustumCulled = false; // skinned bounds are bind-pose only
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map((m) => tunedFor(mesh, m)) : tunedFor(mesh, mesh.material);
    for (const mat of materialsOf(mesh)) {
      for (const value of Object.values(mat)) {
        if (value && (value as Texture).isTexture) ownedTextures.add(value as Texture);
      }
    }
    if (eyeBones.size && (mesh as SkinnedMesh).isSkinnedMesh && weightShare(mesh as SkinnedMesh, eyeBones) > 0.3) {
      eyeballMeshes.add(mesh);
    }
  });

  const morphs = MorphDictionary.fromObject(root, {
    // Eyeballs follow the eye bones; their eyeLook* shapes would double the rotation.
    exclude: (mesh, name) => eyeballMeshes.has(mesh) && EYE_LOOK.test(name),
  });

  // ---- reference pose: canonical T-pose measured from the bind pose
  const restore = snapshotPose(Object.values(bones) as Object3D[]);
  poseToCanonicalTPose(root, bones);
  const reference = captureReference(root, bones);
  restore();
  root.updateMatrixWorld(true);
  const metrics = measureRig(root, bones);

  const useEyeBones = !!(bones.leftEye && bones.rightEye && reference.basis.leftEye && reference.basis.rightEye);
  const gazeQuat = new Quaternion();
  const tmp = new Quaternion();

  return {
    kind: "glb",
    name: options.name ?? "glb",
    root,
    bones,
    basis: reference.basis,
    referenceWorld: reference.referenceWorld,
    metrics,
    morphs,
    gazeMode: useEyeBones ? "bones" : "morphs",
    applyFace(weights: Readonly<FaceWeights>) {
      for (const name in weights) morphs.set(name, weights[name]!);
    },
    setGaze(yaw: number, pitch: number, _target, vergence = 0) {
      if (!useEyeBones) return;
      // Ry(yaw) * Rx(-pitch): turn toward +X for +yaw, up for +pitch; each eye turns inward by `vergence`.
      for (const side of ["leftEye", "rightEye"] as const) {
        const eyeYaw = side === "leftEye" ? yaw - vergence : yaw + vergence;
        quatFromEulerDeg([(-pitch * 180) / Math.PI, (eyeYaw * 180) / Math.PI, 0], "YXZ", gazeQuat);
        bones[side]!.quaternion.copy(applyBasis(reference.basis[side]!, gazeQuat, tmp));
      }
    },
    update() {
      /* GLB bones are driven directly */
    },
    dispose() {
      root.traverse((obj) => {
        const mesh = obj as Mesh;
        if (!mesh.isMesh) return;
        mesh.geometry?.dispose();
        for (const mat of materialsOf(mesh)) mat.dispose();
      });
      for (const tex of ownedTextures) tex.dispose();
    },
  };
}
