import { useEffect, useMemo, useRef, useState } from "react";

import { FACE_GUIDANCE_COPY, FACE_OK_COPY } from "@/features/integrity/face/guidance";
import type { FaceMonitorStatus, FaceMonitorView, FacePresenceMonitor } from "@/features/integrity/face/monitor";
import type { FaceGuidance } from "@/features/integrity/face/states";
import { overlayRect } from "@/features/integrity/face/geometry";
import { cn } from "@/lib/utils";

import type { FaceCheckSnapshot } from "./faceCheck";

/** One face, no tip, for this long: "clearly visible". */
const STEADY_MS = 1500;

/**
 * Runs the on-device face check on the system-check preview (nothing is sent
 * anywhere: `report: false`) and draws what it sees over the mirrored preview:
 * the candidate's face box, other faces, motionless pictures it set aside, and
 * one short tip. Reports a coarse snapshot (status, tip, face count) upward.
 */
export function FaceCheckOverlay({
  video,
  enabled,
  onSnapshot,
  createMonitor,
}: {
  video: HTMLVideoElement | null;
  enabled: boolean;
  onSnapshot: (snapshot: FaceCheckSnapshot) => void;
  createMonitor?: (options: ConstructorParameters<typeof FacePresenceMonitor>[0]) => FacePresenceMonitor;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [view, setView] = useState<FaceMonitorView | null>(null);
  const [status, setStatus] = useState<FaceMonitorStatus>("idle");
  const [guidance, setGuidance] = useState<FaceGuidance | null>(null);
  const [steady, setSteady] = useState(false);
  const goodSince = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || !video) return;
    let monitor: FacePresenceMonitor | null = null;
    let cancelled = false;
    setStatus("loading");
    const options = { video, report: false, onView: setView, onGuidance: setGuidance, onStatus: (s: FaceMonitorStatus) => setStatus(s) };
    const start = async () => {
      try {
        if (createMonitor) monitor = createMonitor(options);
        else {
          const { FacePresenceMonitor } = await import("@/features/integrity/face/monitor");
          monitor = new FacePresenceMonitor(options);
        }
        if (cancelled) {
          monitor.stop();
          return;
        }
        await monitor.start();
      } catch {
        if (!cancelled) setStatus("error");
      }
    };
    void start();
    return () => {
      cancelled = true;
      monitor?.stop();
      setView(null);
      setGuidance(null);
      setSteady(false);
      goodSince.current = null;
      setStatus("idle");
    };
  }, [video, enabled, createMonitor]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const counted = view?.counted ?? 0;
  const live = view?.live ?? 0;
  // A face that has not moved yet (e.g. a poster before it is set aside) does not hold up the check.
  const good = status === "running" && counted >= 1 && live <= 1 && guidance === null;
  useEffect(() => {
    if (!good) {
      goodSince.current = null;
      setSteady(false);
      return;
    }
    const now = performance.now();
    goodSince.current ??= now;
    if (now - goodSince.current >= STEADY_MS) setSteady(true);
    else {
      const timer = setTimeout(() => setSteady(true), STEADY_MS - (now - goodSince.current));
      return () => clearTimeout(timer);
    }
  }, [good]);

  useEffect(() => {
    onSnapshot({ status, guidance, counted, steady });
  }, [status, guidance, counted, steady, onSnapshot]);

  const boxes = useMemo(() => {
    if (!view || !size.width || !size.height) return [];
    return view.faces.map((face) => ({
      face,
      rect: overlayRect(face.box, size, { width: view.videoWidth, height: view.videoHeight }, true),
    }));
  }, [view, size]);

  if (!enabled) return null;
  const tip = status === "running" ? (guidance ? FACE_GUIDANCE_COPY[guidance] : steady ? FACE_OK_COPY : null) : status === "loading" ? "Starting the face check…" : null;

  return (
    <div ref={rootRef} data-testid="syscheck-face-overlay" data-status={status} data-faces={counted} className="pointer-events-none absolute inset-0" aria-hidden={tip ? undefined : true}>
      {boxes.map(({ face, rect }) => (
        <span
          key={face.id}
          data-testid="syscheck-face-box"
          data-kind={face.static ? "static" : face.primary ? "primary" : face.live ? "other" : "pending"}
          className={cn(
            "absolute rounded-lg transition-[left,top,width,height] duration-200 ease-out motion-reduce:transition-none",
            // White on the camera picture is a theme-independent exception (drawn over video).
            face.static ? "border border-dashed border-white/70" : face.primary ? "border-2 border-accent" : face.live ? "border-2 border-warning" : "border-2 border-white/60",
          )}
          style={{ left: `${rect.left}%`, top: `${rect.top}%`, width: `${rect.width}%`, height: `${rect.height}%` }}
        >
          {face.static ? (
            <span className="absolute -top-6 left-0 rounded-sm bg-surface-raised/90 px-1.5 py-0.5 text-[11px] leading-4 whitespace-nowrap text-fg-muted">Picture · not counted</span>
          ) : null}
        </span>
      ))}
      {tip ? (
        <div className="absolute inset-x-3 bottom-3 flex justify-center">
          <p
            data-testid="syscheck-face-guidance"
            data-guidance={guidance ?? (steady ? "ok" : "none")}
            role="status"
            className={cn(
              "max-w-full rounded-full px-3 py-1.5 text-center text-[13px] leading-5 font-medium shadow-raised backdrop-blur-md",
              guidance ? "bg-surface-raised/95 text-fg" : "bg-surface-raised/85 text-fg-muted",
            )}
          >
            {tip}
          </p>
        </div>
      ) : null}
    </div>
  );
}
