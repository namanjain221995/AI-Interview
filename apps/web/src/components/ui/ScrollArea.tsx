import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef } from "react";

import { cn } from "@/lib/utils";

export interface ScrollAreaProps extends ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> {
  orientation?: "vertical" | "horizontal" | "both";
  viewportClassName?: string;
}

export const ScrollArea = forwardRef<ComponentRef<typeof ScrollAreaPrimitive.Root>, ScrollAreaProps>(function ScrollArea(
  { className, children, orientation = "vertical", viewportClassName, ...props },
  ref,
) {
  return (
    <ScrollAreaPrimitive.Root ref={ref} className={cn("relative overflow-hidden", className)} {...props}>
      <ScrollAreaPrimitive.Viewport className={cn("size-full rounded-[inherit]", viewportClassName)}>{children}</ScrollAreaPrimitive.Viewport>
      {orientation !== "horizontal" ? <ScrollBar orientation="vertical" /> : null}
      {orientation !== "vertical" ? <ScrollBar orientation="horizontal" /> : null}
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
});

export const ScrollBar = forwardRef<
  ComponentRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>,
  ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>
>(function ScrollBar({ className, orientation = "vertical", ...props }, ref) {
  return (
    <ScrollAreaPrimitive.ScrollAreaScrollbar
      ref={ref}
      orientation={orientation}
      className={cn(
        "flex touch-none select-none p-0.5 transition-colors",
        orientation === "vertical" ? "h-full w-2.5" : "h-2.5 flex-col",
        className,
      )}
      {...props}
    >
      <ScrollAreaPrimitive.ScrollAreaThumb className="relative flex-1 rounded-full bg-line-strong hover:bg-line-emphasis" />
    </ScrollAreaPrimitive.ScrollAreaScrollbar>
  );
});
