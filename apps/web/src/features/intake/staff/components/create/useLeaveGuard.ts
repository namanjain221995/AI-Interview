import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";

import { confirm } from "@/components/ui/AlertDialog";

/**
 * Asks before leaving while shown-once secrets are on screen and not yet copied:
 * in-app navigation gets a confirmation dialog, closing the tab the browser prompt.
 */
export function useLeaveGuard(active: boolean, options: { title: string; description: string; confirmLabel?: string }) {
  const bypass = useRef(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => active && !bypass.current && (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search));
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;
  const { title, description, confirmLabel = "Leave anyway" } = options;

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    let cancelled = false;
    void confirm({ title, description, confirmLabel, cancelLabel: "Go back", tone: "danger" }).then((ok) => {
      const current = blockerRef.current;
      if (cancelled || current.state !== "blocked") return;
      if (ok) current.proceed();
      else current.reset();
    });
    return () => {
      cancelled = true;
    };
  }, [blocker.state, title, description, confirmLabel]);

  useEffect(() => {
    if (!active) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [active]);

  return {
    /** Call right before a deliberate navigation (after the user confirmed or copied). */
    allowNext: () => {
      bypass.current = true;
    },
  };
}
