import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;

const sheetVariants = cva(
  "fixed z-50 flex flex-col border-line bg-surface-raised text-fg shadow-overlay outline-none",
  {
    variants: {
      side: {
        right:
          "inset-y-0 right-0 h-full w-full border-l data-[state=open]:animate-sheet-in-right data-[state=closed]:animate-sheet-out-right",
        left: "inset-y-0 left-0 h-full w-full border-r data-[state=open]:animate-sheet-in-left data-[state=closed]:animate-sheet-out-left",
        bottom:
          "inset-x-0 bottom-0 max-h-[85dvh] w-full rounded-t-xl border-t data-[state=open]:animate-sheet-in-bottom data-[state=closed]:animate-sheet-out-bottom",
      },
      size: {
        sm: "",
        md: "",
        lg: "",
        xl: "",
      },
    },
    compoundVariants: [
      { side: ["left", "right"], size: "sm", className: "max-w-[360px]" },
      { side: ["left", "right"], size: "md", className: "max-w-[440px]" },
      { side: ["left", "right"], size: "lg", className: "max-w-[560px]" },
      { side: ["left", "right"], size: "xl", className: "max-w-[760px]" },
    ],
    defaultVariants: { side: "right", size: "md" },
  },
);

export interface SheetContentProps
  extends ComponentPropsWithoutRef<typeof DialogPrimitive.Content>,
    VariantProps<typeof sheetVariants> {
  hideClose?: boolean;
}

export const SheetContent = forwardRef<ComponentRef<typeof DialogPrimitive.Content>, SheetContentProps>(function SheetContent(
  { side, size, className, children, hideClose = false, ...props },
  ref,
) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-overlay data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
      <DialogPrimitive.Content ref={ref} className={cn(sheetVariants({ side, size }), className)} {...props}>
        {children}
        {!hideClose ? (
          <DialogPrimitive.Close
            aria-label="Close"
            className="absolute top-3.5 right-3.5 flex size-8 items-center justify-center rounded-control text-fg-subtle transition-colors hover:bg-surface-hover hover:text-fg"
          >
            <X className="size-4" />
          </DialogPrimitive.Close>
        ) : null}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});

export function SheetHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 border-b border-line-subtle px-5 py-4 pr-14", className)} {...props} />;
}

export function SheetBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-h-0 flex-1 overflow-y-auto px-5 py-5 scrollbar-thin", className)} {...props} />;
}

export function SheetFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-center justify-end gap-2 border-t border-line-subtle bg-surface-subtle px-5 py-3.5", className)} {...props} />;
}

export const SheetTitle = forwardRef<ComponentRef<typeof DialogPrimitive.Title>, ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(
  function SheetTitle({ className, ...props }, ref) {
    return <DialogPrimitive.Title ref={ref} className={cn("text-heading text-fg", className)} {...props} />;
  },
);

export const SheetDescription = forwardRef<
  ComponentRef<typeof DialogPrimitive.Description>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(function SheetDescription({ className, ...props }, ref) {
  return <DialogPrimitive.Description ref={ref} className={cn("text-sm text-fg-muted", className)} {...props} />;
});
