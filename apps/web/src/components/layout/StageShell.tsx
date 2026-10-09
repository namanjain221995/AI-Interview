import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface StageShellProps {
  /** Thin top bar (round name, timer, connection state). */
  header?: ReactNode;
  /** Bottom controls (mic, end interview). */
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Browser UI colour while the room is open (the stage canvas, --surface-sunken). */
const STAGE_THEME_COLOR = "#0b0f1e";

/**
 * Full-viewport dark surface for the interview room. Everything inside uses
 * the dark palette (`.theme-stage`), whatever the app theme is, so the regular
 * components render dark.
 */
export function StageShell({ header, footer, children, className }: StageShellProps) {
  useEffect(() => {
    document.body.classList.add("stage-active");
    // Match the mobile browser bar to the dark room; restore the app theme's colour after.
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    const previous = meta?.getAttribute("content") ?? null;
    meta?.setAttribute("content", STAGE_THEME_COLOR);
    return () => {
      document.body.classList.remove("stage-active");
      if (meta && previous !== null) meta.setAttribute("content", previous);
    };
  }, []);

  return (
    <div className={cn("theme-stage relative flex h-dvh w-full flex-col overflow-hidden bg-surface-sunken text-fg", className)}>
      {header ? <div className="relative z-10 shrink-0">{header}</div> : null}
      <main id="stage-main" className="relative min-h-0 flex-1">
        {children}
      </main>
      {footer ? <div className="relative z-10 shrink-0">{footer}</div> : null}
    </div>
  );
}
