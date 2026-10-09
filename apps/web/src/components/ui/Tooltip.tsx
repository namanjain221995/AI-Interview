import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;
export const TooltipRoot = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = forwardRef<
  ComponentRef<typeof TooltipPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(function TooltipContent({ className, sideOffset = 6, children, ...props }, ref) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        collisionPadding={8}
        className={cn(
          "z-[60] max-w-72 rounded-sm bg-primary px-2 py-1 text-xs leading-[1.125rem] font-medium text-primary-fg shadow-raised",
          "origin-(--radix-tooltip-content-transform-origin) animate-float-in data-[side=top]:[--float-y:3px] data-[side=bottom]:[--float-y:-3px]",
          className,
        )}
        {...props}
      >
        {children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
});

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  delayDuration?: number;
  /** Render the child without a tooltip (e.g. when a label is already visible). */
  disabled?: boolean;
  className?: string;
}

/** Hover/focus hint. The trigger must be focusable and keep its own accessible name. */
export function Tooltip({ content, children, side = "top", align = "center", delayDuration, disabled, className }: TooltipProps) {
  if (disabled || content === null || content === undefined || content === "") return <>{children}</>;
  return (
    <TooltipPrimitive.Root delayDuration={delayDuration}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipContent side={side} align={align} className={className}>
        {content}
      </TooltipContent>
    </TooltipPrimitive.Root>
  );
}
