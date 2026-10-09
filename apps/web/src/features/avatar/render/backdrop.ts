/** Backdrop texture for the 3D scene (painted by ./environment). */
import { CanvasTexture, SRGBColorSpace, type Texture } from "three";
import { paintBackdrop, type BackdropKind } from "./environment";

export function createBackdropTexture(kind: BackdropKind): Texture | null {
  if (typeof document === "undefined") return null;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = 1280;
    canvas.height = 720;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    paintBackdrop(ctx, canvas.width, canvas.height, kind);
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    return texture;
  } catch {
    return null;
  }
}
