import { CheckCircle2, Info, Loader2, OctagonAlert, TriangleAlert } from "lucide-react";
import { Toaster as SonnerToaster } from "sonner";

import { useResolvedTheme } from "@/lib/theme";

export { toast } from "sonner";

/** App-wide toast host, styled with the design tokens. Mount once. */
export function Toaster() {
  // Toasts are styled with tokens; the theme prop only keeps Sonner's own defaults in step.
  const theme = useResolvedTheme();
  return (
    <SonnerToaster
      theme={theme}
      position="bottom-right"
      gap={8}
      visibleToasts={4}
      closeButton
      icons={{
        success: <CheckCircle2 className="size-4 text-success" />,
        error: <OctagonAlert className="size-4 text-danger" />,
        warning: <TriangleAlert className="size-4 text-warning" />,
        info: <Info className="size-4 text-accent" />,
        loading: <Loader2 className="size-4 animate-spin text-fg-muted" />,
      }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "group pointer-events-auto relative flex w-[356px] max-w-[calc(100vw-2rem)] items-start gap-3 rounded-md border border-line bg-surface-raised px-4 py-3 text-sm text-fg shadow-overlay",
          title: "font-medium leading-5 text-fg",
          description: "mt-0.5 text-[13px] leading-5 text-fg-muted",
          icon: "mt-0.5 flex size-4 shrink-0 items-center justify-center",
          content: "min-w-0 flex-1",
          actionButton:
            "ml-2 inline-flex h-7 shrink-0 items-center rounded-[7px] bg-primary px-2.5 text-xs font-medium text-primary-fg hover:bg-primary-hover",
          cancelButton:
            "ml-2 inline-flex h-7 shrink-0 items-center rounded-[7px] px-2.5 text-xs font-medium text-fg-muted hover:bg-surface-hover",
          closeButton:
            "absolute -top-2 -left-2 flex size-5 items-center justify-center rounded-full border border-line bg-surface-raised text-fg-subtle opacity-0 shadow-card transition-opacity group-hover:opacity-100 hover:text-fg focus-visible:opacity-100 [&_svg]:size-3",
        },
      }}
    />
  );
}
