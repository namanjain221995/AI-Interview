import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogPortal = DialogPrimitive.Portal;

const SIZES = {
  sm: "max-w-[400px]",
  md: "max-w-[520px]",
  lg: "max-w-[680px]",
  xl: "max-w-[880px]",
} as const;

/** Shared overlay: dims the page and centers (and scrolls) the content. */
export const overlayClass = cn(
  "fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-overlay p-4 backdrop-blur-[2px] sm:p-6",
  "data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out",
);

export interface DialogContentProps extends ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  size?: keyof typeof SIZES;
  /** Hide the top-right close button (keep another way to close). */
  hideClose?: boolean;
}

export const DialogContent = forwardRef<ComponentRef<typeof DialogPrimitive.Content>, DialogContentProps>(function DialogContent(
  { className, children, size = "md", hideClose = false, ...props },
  ref,
) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className={overlayClass}>
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            "relative flex w-full flex-col rounded-xl border border-line bg-surface-raised text-fg shadow-overlay outline-none",
            "data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out",
            SIZES[size],
            className,
          )}
          {...props}
        >
          {children}
          {!hideClose ? (
            <DialogPrimitive.Close
              aria-label="Close"
              className="absolute top-4 right-4 flex size-8 items-center justify-center rounded-control text-fg-subtle transition-colors hover:bg-surface-hover hover:text-fg"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Overlay>
    </DialogPrimitive.Portal>
  );
});

export function DialogHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1.5 px-6 pt-6 pr-14", className)} {...props} />;
}

export function DialogBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-6 py-5", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-col-reverse gap-2 rounded-b-xl border-t border-line-subtle bg-surface-subtle px-6 py-4 sm:flex-row sm:items-center sm:justify-end",
        className,
      )}
      {...props}
    />
  );
}

export const DialogTitle = forwardRef<ComponentRef<typeof DialogPrimitive.Title>, ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(
  function DialogTitle({ className, ...props }, ref) {
    return <DialogPrimitive.Title ref={ref} className={cn("text-heading text-fg", className)} {...props} />;
  },
);

export const DialogDescription = forwardRef<
  ComponentRef<typeof DialogPrimitive.Description>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(function DialogDescription({ className, ...props }, ref) {
  return <DialogPrimitive.Description ref={ref} className={cn("text-sm text-fg-muted", className)} {...props} />;
});
