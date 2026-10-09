import { describe, expect, it } from "vitest";
import { Bone, BoxGeometry, Group, Mesh, MeshStandardMaterial, type Object3D } from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { BROW_STRAND_OPACITY, createGlbRig, LASH_STRAND_OPACITY, materialRole } from "../rig/glbRig";

/** A minimal MPFB-style humanoid: Hips > Spine > Spine1 > Spine2 > Neck > Head plus arms. */
function skeleton(): Bone {
  const bone = (name: string, x: number, y: number, z = 0, parent?: Object3D) => {
    const b = new Bone();
    b.name = name;
    b.position.set(x, y, z);
    parent?.add(b);
    return b;
  };
  const hips = bone("Hips", 0, 0.97);
  const spine = bone("Spine", 0, 0.12, 0, hips);
  const spine1 = bone("Spine1", 0, 0.1, 0, spine);
  const spine2 = bone("Spine2", 0, 0.13, 0, spine1);
  const neck = bone("Neck", 0, 0.17, 0, spine2);
  const head = bone("Head", 0, 0.06, 0, neck);
  bone("LeftEye", 0.032, 0.09, 0.1, head);
  bone("RightEye", -0.032, 0.09, 0.1, head);
  for (const [side, sx] of [
    ["Left", 1],
    ["Right", -1],
  ] as const) {
    const shoulder = bone(`${side}Shoulder`, sx * 0.03, 0.08, 0, spine2);
    const arm = bone(`${side}Arm`, sx * 0.15, 0, 0, shoulder);
    const fore = bone(`${side}ForeArm`, sx * 0.17, -0.19, 0, arm);
    bone(`${side}Hand`, sx * 0.13, -0.13, 0.17, fore);
  }
  return hips;
}

describe("GLB material roles", () => {
  it("decides by mesh name first: optimisers merge identical materials", () => {
    expect(materialRole("Humanmindfront_eyelashes_02", "Human.mindfront_eyebrows_02")).toBe("lash");
    expect(materialRole("Humanmindfront_eyebrows_02", "Human.mindfront_eyebrows_02")).toBe("brow");
    expect(materialRole("Human", "Human.body")).toBe("skin");
    expect(materialRole("Humanhigh-poly", "Human.high-poly")).toBe("eye");
    expect(materialRole("Humanteeth_base", "Human.teeth_base")).toBe("teeth");
    expect(materialRole("Humanponytail01", "Human.ponytail01")).toBe("hair");
    expect(materialRole("Humanfemale_casualsuit01", "Human.female_casualsuit01")).toBe("cloth");
  });

  it("gives brows and lashes their own materials even when the asset shares one", () => {
    const scene = new Group();
    scene.add(skeleton());
    const shared = new MeshStandardMaterial({ name: "Human.mindfront_eyebrows_02", color: 0x000000 });
    const brows = new Mesh(new BoxGeometry(0.1, 0.01, 0.01), shared);
    brows.name = "Humanmindfront_eyebrows_02";
    const lashes = new Mesh(new BoxGeometry(0.1, 0.01, 0.01), shared);
    lashes.name = "Humanmindfront_eyelashes_02";
    const body = new Mesh(new BoxGeometry(0.3, 1.7, 0.2), new MeshStandardMaterial({ name: "Human.body" }));
    body.name = "Human";
    scene.add(brows, lashes, body);

    const rig = createGlbRig({ scene } as unknown as GLTF, { name: "synthetic" });
    const brow = brows.material as MeshStandardMaterial;
    const lash = lashes.material as MeshStandardMaterial;
    expect(brow).not.toBe(lash);
    expect(brow.opacity).toBe(BROW_STRAND_OPACITY);
    expect(lash.opacity).toBe(LASH_STRAND_OPACITY);
    for (const m of [brow, lash]) {
      expect(m.transparent).toBe(true);
      expect(m.depthWrite).toBe(false);
      expect(m.forceSinglePass).toBe(true);
    }
    // Skin gets the wrap-lighting shader patch (its own program cache key).
    const skin = body.material as MeshStandardMaterial;
    expect(skin.customProgramCacheKey()).toBe("avatar-skin-wrap-v1");
    const shader = { fragmentShader: "#include <lights_physical_pars_fragment>\nvoid main() {}", vertexShader: "", uniforms: {} };
    skin.onBeforeCompile(shader as never, undefined as never);
    expect(shader.fragmentShader).toContain("SKIN_WRAP");
    expect(shader.fragmentShader).not.toContain("#include <lights_physical_pars_fragment>");
    rig.dispose();
  });
});
