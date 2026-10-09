import { useEffect, useRef, useState } from "react";

import { AvatarStage, createAvatarController, DEFAULT_PRESENCE, preloadAvatar, resolveAvatarModel, type AvatarController, type AvatarStatus } from "@/features/avatar";

export interface AvatarProbeResult {
  mode: "3d" | "2d" | "none";
  fps: number;
  error?: string;
}

/**
 * Renders the interviewer for a few seconds and reports the measured frame
 * rate, so the room can pick 3D or the lighter 2D mode for this device. The
 * default interviewer is the presence orb; a GLB/VRM path or "procedural"
 * (optionally with an explicit `modelFormat`) probes the 3D avatar instead.
 */
export function AvatarProbe({
  interviewerName,
  modelUrl = DEFAULT_PRESENCE.modelUrl,
  modelFormat,
  durationMs = 3000,
  onResult,
  className,
}: {
  interviewerName: string;
  modelUrl?: string;
  modelFormat?: string;
  durationMs?: number;
  onResult: (result: AvatarProbeResult) => void;
  className?: string;
}) {
  const [controller, setController] = useState<AvatarController | null>(null);
  const done = useRef(false);
  const callback = useRef(onResult);
  callback.current = onResult;

  useEffect(() => {
    // Without an explicit format, infer it from the URL: a path is the 3D avatar, "orb:*" (or nothing) the orb.
    const format = modelFormat ?? (modelUrl === "procedural" ? "procedural" : modelUrl.startsWith("/") ? (modelUrl.endsWith(".vrm") ? "vrm" : "glb") : "orb");
    const model = resolveAvatarModel(format, modelUrl);
    preloadAvatar(model.modelUrl);
    const c = createAvatarController({ ...model, interviewerName, background: "studio" });
    setController(c);
    done.current = false;
    const samples: number[] = [];
    let latest: AvatarStatus = c.getStatus();
    let measureTimer: ReturnType<typeof setTimeout> | null = null;
    const finish = () => {
      if (done.current) return;
      done.current = true;
      // Median of the steady-state samples: the first report often includes shader compilation.
      const steady = samples.length > 1 ? samples.slice(1) : samples;
      const sorted = [...steady].sort((a, b) => a - b);
      const fps = sorted.length ? sorted[Math.floor(sorted.length / 2)]! : latest.fps;
      callback.current({ mode: latest.mode, fps: Number.isFinite(fps) ? fps : 0, error: latest.error });
    };
    const startMeasuring = () => {
      if (measureTimer) return;
      measureTimer = setTimeout(finish, durationMs);
    };
    const unsubscribe = c.subscribe((status) => {
      latest = status;
      if (status.mode !== "3d") {
        // No WebGL or a render failure: nothing to measure.
        if (status.loaded) startMeasuring();
        return;
      }
      if (status.loaded) {
        startMeasuring();
        if (status.fps > 0) samples.push(status.fps);
      }
    });
    // Never block the check on a model that won't load.
    const hardStop = setTimeout(finish, durationMs + 9000);
    return () => {
      unsubscribe();
      if (measureTimer) clearTimeout(measureTimer);
      clearTimeout(hardStop);
      c.dispose();
    };
  }, [interviewerName, modelUrl, modelFormat, durationMs]);

  if (!controller) return null;
  return <AvatarStage controller={controller} framing="bust" className={className} />;
}
