/**
 * Avatar loading: same-origin GLB/VRM via GLTFLoader + MeshoptDecoder (VRM
 * plugin loaded on demand), or the in-code procedural bust. Fetches the file
 * itself so loads can be aborted and report progress.
 */
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import type { AvatarConfig } from "../types";
import { createProceduralRig } from "../procedural/proceduralAvatar";
import { createGlbRig } from "./glbRig";
import type { AvatarRig } from "./rig";

export class AvatarLoadError extends Error {
  constructor(
    message: string,
    readonly code: "cross_origin" | "http" | "parse" | "rig" | "aborted",
  ) {
    super(message);
    this.name = "AvatarLoadError";
  }
}

/** Resolve `url` against the page and require the same origin (CSP 'self'; no third-party CDNs). */
export function resolveSameOrigin(url: string, base: string = globalThis.location?.href ?? "http://localhost/"): URL {
  let resolved: URL;
  try {
    resolved = new URL(url, base);
  } catch {
    throw new AvatarLoadError(`invalid avatar URL: ${url}`, "cross_origin");
  }
  const origin = new URL(base).origin;
  if (resolved.origin !== origin || !/^https?:$/.test(resolved.protocol)) {
    throw new AvatarLoadError("avatar assets must be served from the same origin", "cross_origin");
  }
  return resolved;
}

export interface LoadOptions {
  signal?: AbortSignal;
  onProgress?: (fraction: number) => void;
}

async function fetchArrayBuffer(url: URL, options: LoadOptions): Promise<ArrayBuffer> {
  let response: Response;
  try {
    // The URL is already verified same-origin above. Use the default "cors" mode so the
    // request matches the <link rel="preload" as="fetch" crossorigin> warm-up in
    // preloadAvatar(); a "same-origin" mode request would not reuse the preload and the
    // 3 MB asset would be downloaded twice.
    response = await fetch(url, { signal: options.signal, credentials: "same-origin" });
  } catch (err) {
    if (options.signal?.aborted) throw new AvatarLoadError("avatar load aborted", "aborted");
    throw new AvatarLoadError(`avatar download failed: ${(err as Error).message}`, "http");
  }
  if (!response.ok) throw new AvatarLoadError(`avatar download failed: HTTP ${response.status}`, "http");
  const total = Number(response.headers.get("content-length")) || 0;
  if (!response.body || !total || !options.onProgress) return response.arrayBuffer();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.byteLength;
    options.onProgress(Math.min(1, received / total));
  }
  const out = new Uint8Array(received);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out.buffer;
}

export async function loadAvatarRig(
  config: Pick<AvatarConfig, "modelUrl" | "modelFormat" | "interviewerName">,
  options: LoadOptions = {},
): Promise<AvatarRig> {
  if (config.modelFormat === "procedural" || config.modelUrl === "procedural") {
    return createProceduralRig({ name: "procedural" });
  }
  const url = resolveSameOrigin(config.modelUrl);
  const buffer = await fetchArrayBuffer(url, options);
  if (options.signal?.aborted) throw new AvatarLoadError("avatar load aborted", "aborted");

  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  loader.setCrossOrigin("same-origin");
  let vrmUtils: typeof import("@pixiv/three-vrm").VRMUtils | null = null;
  if (config.modelFormat === "vrm") {
    const vrmModule = await import("@pixiv/three-vrm");
    vrmUtils = vrmModule.VRMUtils;
    loader.register((parser) => new vrmModule.VRMLoaderPlugin(parser));
  }
  const resourcePath = new URL(".", url).href;
  let gltf;
  try {
    gltf = await loader.parseAsync(buffer, resourcePath);
  } catch (err) {
    throw new AvatarLoadError(`avatar parse failed: ${(err as Error).message}`, "parse");
  }
  if (options.signal?.aborted) throw new AvatarLoadError("avatar load aborted", "aborted");

  const name = url.pathname.split("/").pop() ?? config.modelFormat;
  try {
    if (config.modelFormat === "vrm" && vrmUtils) {
      const { createVrmRig } = await import("./vrmRig");
      return createVrmRig(gltf, vrmUtils, { name });
    }
    return createGlbRig(gltf, { name });
  } catch (err) {
    throw new AvatarLoadError(`avatar rig unsupported: ${(err as Error).message}`, "rig");
  }
}

