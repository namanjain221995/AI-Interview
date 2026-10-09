/**
 * Image-based lighting for the interviewer: a small portrait studio built in
 * code and baked to a PMREM (no HDR files, CSP 'self'). A large warm softbox
 * camera-left (the key, and the catchlight in the eyes), a dimmer neutral fill
 * camera-right, a cool strip light behind for the rims, a soft top light, a
 * warm dark floor for a little bounce and dark walls so the ambient term
 * models the face instead of flattening it. Directions match the direct
 * lights in Avatar3D (subject at the origin facing +Z).
 */
import { BackSide, BoxGeometry, Color, Mesh, MeshBasicMaterial, PlaneGeometry, Scene, Vector3 } from "three";

interface Panel {
  /** Direction from the subject (normalised internally). */
  dir: [number, number, number];
  distance: number;
  width: number;
  height: number;
  /** Linear RGB radiance (HDR, > 1 is fine). */
  radiance: [number, number, number];
}

const PANELS: Panel[] = [
  { dir: [-2.2, 1.3, 1.6], distance: 3.2, width: 2.4, height: 2.0, radiance: [7.2, 6.1, 5.0] }, // key softbox, warm
  { dir: [1.8, 0.5, 1.5], distance: 3.4, width: 2.0, height: 1.6, radiance: [1.6, 1.55, 1.5] }, // fill, neutral
  { dir: [0.9, 1.4, -1.8], distance: 3.0, width: 0.6, height: 2.2, radiance: [3.4, 3.7, 4.2] }, // rim strip, cool
  { dir: [-1.1, 0.9, -1.6], distance: 3.0, width: 0.5, height: 1.8, radiance: [2.2, 2.0, 1.8] }, // second rim, warm
  { dir: [0, 1, 0.25], distance: 2.8, width: 1.6, height: 1.6, radiance: [1.3, 1.25, 1.2] }, // top
];

export function createStudioEnvironment(): Scene {
  const scene = new Scene();
  // Room: dark warm-grey walls, slightly lighter ceiling, darker floor.
  const room = new Mesh(new BoxGeometry(9, 6, 9), new MeshBasicMaterial({ side: BackSide, color: new Color(0.07, 0.065, 0.062) }));
  room.position.set(0, 1.5, 0);
  scene.add(room);
  const floor = new Mesh(new PlaneGeometry(9, 9), new MeshBasicMaterial({ color: new Color(0.11, 0.085, 0.07) }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -1.4;
  scene.add(floor);
  for (const panel of PANELS) {
    const mesh = new Mesh(new PlaneGeometry(panel.width, panel.height), new MeshBasicMaterial({ color: new Color(...panel.radiance) }));
    const dir = new Vector3(...panel.dir).normalize();
    mesh.position.copy(dir.clone().multiplyScalar(panel.distance));
    mesh.lookAt(0, 0, 0);
    scene.add(mesh);
  }
  return scene;
}

/** Free the GPU-side resources of a scene made by createStudioEnvironment(). */
export function disposeStudioEnvironment(scene: Scene): void {
  scene.traverse((obj) => {
    const mesh = obj as Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry.dispose();
    (mesh.material as MeshBasicMaterial).dispose();
  });
}
