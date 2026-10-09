import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center gap-0.5 rounded-[5px] border border-line bg-surface-subtle px-1 font-mono text-[11px] leading-none font-medium text-fg-muted",
        "shadow-[inset_0_-1px_0_var(--border)]",
        className,
      )}
      {...props}
    />
  );
}

/** "⌘" on Apple platforms, "Ctrl" elsewhere. */
export function modKeyLabel(): string {
  if (typeof navigator === "undefined") return "Ctrl";
  const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform ?? "";
  return /mac|iphone|ipad|ipod/i.test(platform) ? "⌘" : "Ctrl";
}
