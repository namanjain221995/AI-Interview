import { cn } from "@/lib/utils";

/** Segmented microphone level meter. Signal colour = live audio. */
export function LevelMeter({ level, segments = 14, label = "Microphone level", className, tone = "signal" }: { level: number; segments?: number; label?: string; className?: string; tone?: "signal" | "muted" }) {
  const lit = Math.round(Math.max(0, Math.min(1, level)) * segments);
  return (
    <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(level * 100)} className={cn("flex h-4 items-end gap-[3px]", className)}>
      {Array.from({ length: segments }, (_, i) => (
        <span
          key={i}
          className={cn("w-[3px] rounded-full transition-[background-color] duration-75", i < lit ? (tone === "signal" ? "bg-signal" : "bg-fg-muted") : "bg-line-strong")}
          style={{ height: `${30 + (i / (segments - 1)) * 70}%` }}
        />
      ))}
    </div>
  );
}
