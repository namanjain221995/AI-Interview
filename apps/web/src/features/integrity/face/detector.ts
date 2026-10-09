/**
 * On-device face detector: MediaPipe Tasks Vision FaceDetector with the
 * BlazeFace short-range model (both Apache-2.0), self-hosted under
 * `/mediapipe/` (no CDN at runtime; the edge CSP allows only 'self' plus
 * 'wasm-unsafe-eval' for WebAssembly). The library is imported lazily, so it
 * only loads where the face check runs (system check, interview room).
 *
 * Runs on the CPU (XNNPACK): measured ~11-12 ms per 320x240 frame in headless
 * Chromium on the DGX Spark, faster than the GPU delegate there (~23 ms), and
 * it leaves the WebGL context to the interviewer's orb.
 */
import { MIN_FACE_SCORE } from "./analysis";
import type { RawDetection } from "./tracker";

export const DETECTOR_NAME = "blazeface-short-range";

export interface FaceDetectorEngine {
  readonly name: string;
  /** Faces in `source`, best first (boxes normalised to the source; `timestampMs` must increase). */
  detect(source: HTMLCanvasElement | HTMLVideoElement | ImageBitmap | OffscreenCanvas, timestampMs: number): RawDetection[];
  close(): void;
}

/** This browser cannot run the on-device face check (no WebAssembly SIMD). */
export class FaceCheckUnsupportedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FaceCheckUnsupportedError";
  }
}

export function mediapipeBase(): string {
  const base = (import.meta.env?.BASE_URL as string | undefined) ?? "/";
  return `${base.endsWith("/") ? base : `${base}/`}mediapipe`;
}

export const MEDIAPIPE_ASSETS = {
  wasmLoader: "wasm/vision_wasm_internal.js",
  wasmBinary: "wasm/vision_wasm_internal.wasm",
  model: "models/blaze_face_short_range.tflite",
} as const;

function sourceSize(source: HTMLCanvasElement | HTMLVideoElement | ImageBitmap | OffscreenCanvas): { width: number; height: number } {
  if ("videoWidth" in source) return { width: source.videoWidth, height: source.videoHeight };
  return { width: source.width, height: source.height };
}

export async function createBlazeFaceDetector(): Promise<FaceDetectorEngine> {
  if (typeof WebAssembly === "undefined") throw new FaceCheckUnsupportedError("WebAssembly is not available");
  const vision = await import("@mediapipe/tasks-vision");
  // Only the SIMD build is shipped (every supported browser has WebAssembly SIMD).
  if (!(await vision.FilesetResolver.isSimdSupported())) throw new FaceCheckUnsupportedError("WebAssembly SIMD is not available");
  const base = mediapipeBase();
  const detector = await vision.FaceDetector.createFromOptions(
    { wasmLoaderPath: `${base}/${MEDIAPIPE_ASSETS.wasmLoader}`, wasmBinaryPath: `${base}/${MEDIAPIPE_ASSETS.wasmBinary}` },
    {
      baseOptions: { modelAssetPath: `${base}/${MEDIAPIPE_ASSETS.model}`, delegate: "CPU" },
      runningMode: "VIDEO",
      minDetectionConfidence: 0.5,
      minSuppressionThreshold: 0.3,
    },
  );
  let last = 0;
  return {
    name: DETECTOR_NAME,
    detect(source, timestampMs) {
      const ts = Math.max(last + 1, Math.round(timestampMs));
      last = ts;
      const { width, height } = sourceSize(source);
      if (!width || !height) return [];
      const result = detector.detectForVideo(source, ts);
      const faces: RawDetection[] = [];
      for (const detection of result.detections) {
        const score = detection.categories[0]?.score ?? 0;
        const box = detection.boundingBox;
        if (score < MIN_FACE_SCORE || !box) continue;
        faces.push({
          box: { x: box.originX / width, y: box.originY / height, w: box.width / width, h: box.height / height },
          score,
          keypoints: detection.keypoints.map((k) => ({ x: k.x, y: k.y })),
        });
      }
      return faces.sort((a, b) => b.score - a.score);
    },
    close() {
      try {
        detector.close();
      } catch {
        /* already closed */
      }
    },
  };
}
