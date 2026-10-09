import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";

import { confirm } from "@/components/ui/AlertDialog";

/**
 * Asks before leaving a page with unsaved edits: in-app navigation shows a
 * confirmation dialog, closing or reloading the tab uses the browser prompt.
 * Call `allowNext()` right before a deliberate navigation (e.g. after saving).
 */
export function useUnsavedChanges(dirty: boolean, message = "Your changes haven't been saved.") {
  const bypass = useRef(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && !bypass.current && currentLocation.pathname !== nextLocation.pathname);

  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    let cancelled = false;
    void confirm({ title: "Leave without saving?", description: message, confirmLabel: "Leave page", cancelLabel: "Keep editing", tone: "danger" }).then((ok) => {
      const current = blockerRef.current;
      if (cancelled || current.state !== "blocked") return;
      if (ok) current.proceed();
      else current.reset();
    });
    return () => {
      cancelled = true;
    };
  }, [blocker.state, message]);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  return {
    allowNext: () => {
      bypass.current = true;
    },
  };
}
