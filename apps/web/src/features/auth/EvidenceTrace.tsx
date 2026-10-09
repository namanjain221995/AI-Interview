import { CircleMinus, CirclePlus } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";

/**
 * Illustration for the sign-in page: one moment of an interview, showing how
 * an answer turns into attributable evidence. Purely decorative content.
 */
const EVIDENCE = [
  { signal: "positive", text: "Explained write-through invalidation with a concrete failure case", competency: "System design" },
  { signal: "positive", text: "Quantified the p99 latency change after the rollout", competency: "Performance" },
  { signal: "negative", text: "Did not describe a rollback plan for the migration", competency: "Reliability" },
] as const;

// Deterministic waveform heights (0-1) so the illustration renders identically every time.
const WAVE = Array.from({ length: 44 }, (_, i) => {
  const envelope = Math.sin((i / 43) * Math.PI) * 0.55 + 0.25;
  const texture = (Math.sin(i * 1.7) + Math.sin(i * 0.63 + 1)) * 0.18;
  return Math.max(0.12, Math.min(1, envelope + texture));
});

export function EvidenceTrace({ className }: { className?: string }) {
  return (
    <div className={cn("w-full max-w-[460px]", className)} aria-hidden="true">
      <div className="overflow-hidden rounded-xl border border-line bg-surface/90 shadow-[0_30px_80px_-24px_rgb(0_0_0/0.65)] backdrop-blur">
        <div className="flex items-center justify-between border-b border-line-subtle px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <Badge tone="signal" size="sm" dot pulse>
              Live
            </Badge>
            <span className="text-[13px] font-medium text-fg">Technical screening</span>
          </div>
          <span className="font-mono text-xs text-fg-subtle tabular-nums">18:42</span>
        </div>

        <div className="grid gap-4 px-5 py-5">
          <div className="flex gap-3">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent-fg ring-1 ring-accent-line">
              M
            </span>
            <div className="grid gap-1">
              <span className="text-xs font-medium text-fg-muted">Maya, AI interviewer</span>
              <p className="text-[14px] leading-[1.45rem] text-fg">How did you keep the cache consistent while the migration was running?</p>
            </div>
          </div>

          <div className="flex gap-3">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-neutral-soft text-xs font-semibold text-neutral-fg ring-1 ring-neutral-line">
              C
            </span>
            <div className="grid min-w-0 flex-1 gap-1.5">
              <span className="text-xs font-medium text-fg-muted">Candidate, answering</span>
              <div className="flex h-9 items-center justify-between rounded-control bg-surface-subtle px-3 ring-1 ring-line-subtle">
                {WAVE.map((height, index) => (
                  <span
                    key={index}
                    className="trace-bar w-[3px] rounded-full bg-signal"
                    style={{ height: `${Math.round(5 + height * 19)}px`, animationDelay: `${(index % 9) * 95}ms` }}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="border-t border-line-subtle bg-surface-subtle/70 px-5 pt-3.5 pb-4">
          <p className="mb-2.5 text-xs font-medium text-fg-muted">Evidence from this answer</p>
          <ul className="grid gap-2">
            {EVIDENCE.map((item, index) => (
              <li
                key={item.text}
                className="trace-row flex items-start justify-between gap-3 rounded-control bg-surface px-3 py-2.5 ring-1 ring-line-subtle"
                style={{ animationDelay: `${450 + index * 260}ms` }}
              >
                <span className="flex min-w-0 items-start gap-2">
                  {item.signal === "positive" ? (
                    <CirclePlus className="mt-0.5 size-3.5 shrink-0 text-success" />
                  ) : (
                    <CircleMinus className="mt-0.5 size-3.5 shrink-0 text-danger" />
                  )}
                  <span className="text-[12.5px] leading-[1.15rem] text-fg">{item.text}</span>
                </span>
                <span className="mt-px shrink-0 text-[11px] font-medium text-fg-muted">{item.competency}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
