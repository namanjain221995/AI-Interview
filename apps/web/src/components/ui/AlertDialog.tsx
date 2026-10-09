import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import { useState, type MouseEvent, type ReactNode } from "react";
import { create } from "zustand";

import { cn } from "@/lib/utils";

import { Button } from "./Button";
import { overlayClass } from "./Dialog";

export const AlertDialog = AlertDialogPrimitive.Root;
export const AlertDialogTrigger = AlertDialogPrimitive.Trigger;

export interface ConfirmOptions {
  title: ReactNode;
  description?: ReactNode;
  /** Verb that names the action, e.g. "Archive job". */
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
  /** Optional async work run before closing; a rejection keeps the dialog open. */
  onConfirm?: () => Promise<unknown> | unknown;
}

export interface ConfirmDialogProps extends ConfirmOptions {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirmed?: () => void;
  trigger?: ReactNode;
  children?: ReactNode;
}

/** Controlled confirmation dialog. Focus starts on Cancel; Escape cancels. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  onConfirm,
  onConfirmed,
  trigger,
  children,
}: ConfirmDialogProps) {
  const [pending, setPending] = useState(false);

  const handleConfirm = async (event: MouseEvent) => {
    event.preventDefault();
    if (!onConfirm) {
      onConfirmed?.();
      onOpenChange(false);
      return;
    }
    setPending(true);
    try {
      await onConfirm();
      onConfirmed?.();
      onOpenChange(false);
    } catch {
      // Errors are surfaced by the caller (e.g. mutation toasts); stay open.
    } finally {
      setPending(false);
    }
  };

  return (
    <AlertDialogPrimitive.Root open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      {trigger ? <AlertDialogPrimitive.Trigger asChild>{trigger}</AlertDialogPrimitive.Trigger> : null}
      <AlertDialogPrimitive.Portal>
        <AlertDialogPrimitive.Overlay className={overlayClass}>
          <AlertDialogPrimitive.Content
            className={cn(
              "relative w-full max-w-[440px] rounded-xl border border-line bg-surface-raised text-fg shadow-overlay outline-none",
              "data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out",
            )}
          >
            <div className="flex flex-col gap-2 px-6 pt-6 pb-5">
              <AlertDialogPrimitive.Title className="text-heading text-fg">{title}</AlertDialogPrimitive.Title>
              {description ? (
                <AlertDialogPrimitive.Description className="text-sm text-fg-muted">{description}</AlertDialogPrimitive.Description>
              ) : null}
              {children}
            </div>
            <div className="flex flex-col-reverse gap-2 rounded-b-xl border-t border-line-subtle bg-surface-subtle px-6 py-4 sm:flex-row sm:justify-end">
              <AlertDialogPrimitive.Cancel asChild>
                <Button variant="outline" disabled={pending}>
                  {cancelLabel}
                </Button>
              </AlertDialogPrimitive.Cancel>
              <AlertDialogPrimitive.Action asChild>
                <Button variant={tone === "danger" ? "danger" : "primary"} loading={pending} onClick={handleConfirm}>
                  {confirmLabel}
                </Button>
              </AlertDialogPrimitive.Action>
            </div>
          </AlertDialogPrimitive.Content>
        </AlertDialogPrimitive.Overlay>
      </AlertDialogPrimitive.Portal>
    </AlertDialogPrimitive.Root>
  );
}

// ------------------------------------------------- imperative confirm()

interface PendingConfirm extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

interface ConfirmStore {
  pending: PendingConfirm | null;
  open: boolean;
  ask: (options: ConfirmOptions) => Promise<boolean>;
  settle: (confirmed: boolean) => void;
}

const useConfirmStore = create<ConfirmStore>()((set, get) => ({
  pending: null,
  open: false,
  ask: (options) =>
    new Promise<boolean>((resolve) => {
      get().pending?.resolve(false);
      set({ pending: { ...options, resolve }, open: true });
    }),
  settle: (confirmed) => {
    const { pending } = get();
    pending?.resolve(confirmed);
    set({ open: false });
  },
}));

/** `if (await confirm({ title: "Archive this job?", tone: "danger" })) …` — requires <ConfirmDialogHost/>. */
export function confirm(options: ConfirmOptions): Promise<boolean> {
  return useConfirmStore.getState().ask(options);
}

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  return useConfirmStore((s) => s.ask);
}

/** Mount once near the app root. */
export function ConfirmDialogHost() {
  const pending = useConfirmStore((s) => s.pending);
  const open = useConfirmStore((s) => s.open);
  const settle = useConfirmStore((s) => s.settle);
  if (!pending) return null;
  return (
    <ConfirmDialog
      {...pending}
      open={open}
      onOpenChange={(next) => {
        if (!next) settle(false);
      }}
      onConfirmed={() => settle(true)}
    />
  );
}
