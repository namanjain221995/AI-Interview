import { cn } from "@/lib/utils";

const SIZES = { xs: 12, sm: 14, md: 16, lg: 20, xl: 28 } as const;

export interface SpinnerProps {
  size?: keyof typeof SIZES;
  className?: string;
  /** Accessible label; pass null when the parent already announces progress. */
  label?: string | null;
}

export function Spinner({ size = "md", className, label = "Loading" }: SpinnerProps) {
  const px = SIZES[size];
  return (
    <svg
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill="none"
      className={cn("shrink-0 animate-spin", className)}
      role={label ? "status" : undefined}
      aria-label={label ?? undefined}
      aria-hidden={label ? undefined : true}
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.18" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

/** Centered spinner for whole panels and pages. */
export function LoadingBlock({ label = "Loading", className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex min-h-40 flex-col items-center justify-center gap-3 text-fg-muted", className)}>
      <Spinner size="lg" label={null} />
      <p className="text-sm" role="status">
        {label}
      </p>
    </div>
  );
}
