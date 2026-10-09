import * as TabsPrimitive from "@radix-ui/react-tabs";
import { createContext, forwardRef, useCallback, useContext, useEffect, useRef, type ComponentPropsWithoutRef, type ComponentRef } from "react";

import { revealInScroller, useOverflowEdges } from "@/lib/hooks/useOverflowEdges";
import { cn } from "@/lib/utils";

type TabsVariant = "underline" | "segmented";
const VariantContext = createContext<TabsVariant>("underline");

export interface TabsProps extends ComponentPropsWithoutRef<typeof TabsPrimitive.Root> {
  /** `underline` for page sections, `segmented` for compact view switches. */
  variant?: TabsVariant;
}

export const Tabs = forwardRef<ComponentRef<typeof TabsPrimitive.Root>, TabsProps>(function Tabs(
  { variant = "underline", className, ...props },
  ref,
) {
  return (
    <VariantContext.Provider value={variant}>
      <TabsPrimitive.Root ref={ref} className={cn("flex flex-col", variant === "underline" ? "gap-5" : "gap-4", className)} {...props} />
    </VariantContext.Provider>
  );
});

/**
 * Tab bar. Both variants scroll horizontally when the tabs don't fit: no scrollbar,
 * a fade on the edge that hides tabs, arrow keys move between tabs (Radix roving
 * focus) and the active tab is scrolled into view, including when the URL selects it.
 * The underline baseline is an inset shadow, not a border, so the list never
 * overflows vertically (a 1px overflow used to show a stray vertical scrollbar).
 */
export const TabsList = forwardRef<ComponentRef<typeof TabsPrimitive.List>, ComponentPropsWithoutRef<typeof TabsPrimitive.List>>(
  function TabsList({ className, ...props }, forwardedRef) {
    const variant = useContext(VariantContext);
    const innerRef = useRef<HTMLDivElement | null>(null);
    const setRef = useCallback(
      (node: HTMLDivElement | null) => {
        innerRef.current = node;
        if (typeof forwardedRef === "function") forwardedRef(node);
        else if (forwardedRef) forwardedRef.current = node;
      },
      [forwardedRef],
    );
    useOverflowEdges(innerRef);
    useEffect(() => {
      const list = innerRef.current;
      if (!list) return;
      const reveal = () => {
        const active = list.querySelector<HTMLElement>('[role="tab"][data-state="active"]');
        if (active) revealInScroller(list, active);
      };
      reveal();
      // Web fonts change tab widths after the first layout; reveal again once they're in.
      let cancelled = false;
      void document.fonts?.ready.then(() => {
        if (!cancelled) reveal();
      });
      if (typeof MutationObserver === "undefined") return;
      const observer = new MutationObserver((records) => {
        if (records.some((r) => r.type === "attributes" && (r.target as HTMLElement).getAttribute("data-state") === "active")) reveal();
      });
      observer.observe(list, { subtree: true, attributes: true, attributeFilter: ["data-state"] });
      return () => {
        cancelled = true;
        observer.disconnect();
      };
    }, []);
    return (
      <TabsPrimitive.List
        ref={setRef}
        className={cn(
          "overflow-x-auto overflow-y-hidden overscroll-x-contain scrollbar-none edge-fade",
          variant === "underline"
            ? "flex items-center gap-5 shadow-[inset_0_-1px_0_var(--border)]"
            : "inline-flex w-fit max-w-full items-center gap-0.5 rounded-control bg-surface-sunken/70 p-0.5",
          className,
        )}
        {...props}
      />
    );
  },
);

export const TabsTrigger = forwardRef<
  ComponentRef<typeof TabsPrimitive.Trigger>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(function TabsTrigger({ className, ...props }, ref) {
  const variant = useContext(VariantContext);
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4",
        // Inset focus ring: the list is a scroll container and would clip an outset one.
        "focus-visible:outline-offset-[-2px]",
        variant === "underline"
          ? [
              "relative h-10 rounded-t-sm border-b-2 border-transparent px-1 text-sm text-fg-muted hover:text-fg",
              "data-[state=active]:border-primary data-[state=active]:text-fg",
            ]
          : [
              "h-7 rounded-[6px] px-3 text-[13px] text-fg-muted hover:text-fg",
              "data-[state=active]:bg-surface data-[state=active]:text-fg data-[state=active]:shadow-[0_1px_2px_rgb(16_24_40/0.08),0_0_0_1px_var(--border)]",
            ],
        className,
      )}
      {...props}
    />
  );
});

export const TabsContent = forwardRef<
  ComponentRef<typeof TabsPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(function TabsContent({ className, ...props }, ref) {
  return <TabsPrimitive.Content ref={ref} className={cn("outline-none focus-visible:focus-ring rounded-sm", className)} {...props} />;
});
