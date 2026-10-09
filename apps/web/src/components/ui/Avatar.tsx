import * as AvatarPrimitive from "@radix-ui/react-avatar";

import { initials } from "@/lib/format";
import { cn, hashString } from "@/lib/utils";

const SIZES = {
  xs: "size-5 text-[9px]",
  sm: "size-6 text-[10px]",
  md: "size-8 text-xs",
  lg: "size-10 text-sm",
  xl: "size-14 text-lg",
} as const;

// Quiet, evenly weighted tints (tokens --avatar-N, light and dark values in
// globals.css). Chosen from the name hash only so the same person always gets the
// same colour; carries no meaning.
const TINTS = [
  "bg-avatar-1 text-avatar-1-fg",
  "bg-avatar-2 text-avatar-2-fg",
  "bg-avatar-3 text-avatar-3-fg",
  "bg-avatar-4 text-avatar-4-fg",
  "bg-avatar-5 text-avatar-5-fg",
  "bg-avatar-6 text-avatar-6-fg",
] as const;

export interface AvatarProps {
  name: string | null | undefined;
  src?: string | null;
  size?: keyof typeof SIZES;
  className?: string;
  /** Square-ish shape for organisations. */
  shape?: "circle" | "square";
}

export function Avatar({ name, src, size = "md", className, shape = "circle" }: AvatarProps) {
  const tint = TINTS[hashString(name ?? "?") % TINTS.length];
  return (
    <AvatarPrimitive.Root
      className={cn(
        "relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden font-semibold",
        shape === "circle" ? "rounded-full" : "rounded-[7px]",
        SIZES[size],
        className,
      )}
    >
      {src ? <AvatarPrimitive.Image src={src} alt={name ?? ""} className="size-full object-cover" /> : null}
      <AvatarPrimitive.Fallback delayMs={src ? 300 : 0} className={cn("flex size-full items-center justify-center", tint)}>
        <span aria-hidden="true">{initials(name)}</span>
        <span className="sr-only">{name}</span>
      </AvatarPrimitive.Fallback>
    </AvatarPrimitive.Root>
  );
}
