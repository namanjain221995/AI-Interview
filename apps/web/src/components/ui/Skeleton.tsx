import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** Placeholder block shown while content loads. Decorative: hidden from assistive tech. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden="true" className={cn("skeleton-shimmer animate-shimmer rounded-sm", className)} {...props} />;
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("grid gap-2", className)} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className={cn("h-3.5", index === lines - 1 && lines > 1 ? "w-3/5" : "w-full")} />
      ))}
    </div>
  );
}
