import { useEffect, useState } from "react";

import type { AvatarController, AvatarStatus } from "@/features/avatar";

import type { InterviewRuntime } from "./runtime";

/** Re-render every `intervalMs` (timers). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function useAvatarStatus(controller: AvatarController | null): AvatarStatus | null {
  const [status, setStatus] = useState<AvatarStatus | null>(() => controller?.getStatus() ?? null);
  useEffect(() => {
    if (!controller) {
      setStatus(null);
      return;
    }
    setStatus(controller.getStatus());
    return controller.subscribe(setStatus);
  }, [controller]);
  return status;
}

export function useFullscreen(): boolean {
  const [full, setFull] = useState(() => typeof document !== "undefined" && Boolean(document.fullscreenElement));
  useEffect(() => {
    const onChange = () => setFull(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  return full;
}

/** Re-render when the runtime acquires or loses devices. */
export function useRuntimeMedia(runtime: InterviewRuntime | null): { camera: MediaStream | null; microphone: boolean } {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!runtime) return;
    return runtime.onMedia(() => setTick((t) => t + 1));
  }, [runtime]);
  return { camera: runtime?.cameraStream ?? null, microphone: runtime?.hasMicrophone ?? false };
}
