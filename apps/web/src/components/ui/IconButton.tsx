import { forwardRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Button, type ButtonProps } from "./Button";
import { Tooltip } from "./Tooltip";

const SQUARE = {
  xs: "size-7 px-0",
  sm: "size-8 px-0",
  md: "size-9 px-0",
  lg: "size-10 px-0",
  xl: "size-12 px-0",
} as const;

export interface IconButtonProps extends Omit<ButtonProps, "leftIcon" | "rightIcon" | "loadingText" | "children" | "fullWidth"> {
  /** Accessible name; also the tooltip text unless `tooltip` is set. */
  label: string;
  icon: ReactNode;
  /** Tooltip text; `false` disables it. Defaults to `label`. */
  tooltip?: ReactNode | false;
  tooltipSide?: "top" | "right" | "bottom" | "left";
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, tooltip, tooltipSide = "top", variant = "ghost", size = "md", className, ...props },
  ref,
) {
  const button = (
    <Button ref={ref} variant={variant} size={size} aria-label={label} className={cn(SQUARE[size ?? "md"], className)} {...props}>
      {icon}
    </Button>
  );
  if (tooltip === false) return button;
  return (
    <Tooltip content={tooltip ?? label} side={tooltipSide}>
      {button}
    </Tooltip>
  );
});
