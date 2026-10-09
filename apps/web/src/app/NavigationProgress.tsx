import { useEffect, useState } from "react";
import { useNavigation } from "react-router";

import { cn } from "@/lib/utils";

/** Thin top bar while a route chunk or loader is pending (shown after 120 ms to avoid flicker). */
export function NavigationProgress() {
  const navigation = useNavigation();
  const busy = navigation.state !== "idle";
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!busy) {
      setVisible(false);
      return;
    }
    const timer = setTimeout(() => setVisible(true), 120);
    return () => clearTimeout(timer);
  }, [busy]);

  return (
    <div
      aria-hidden="true"
      className={cn("pointer-events-none fixed inset-x-0 top-0 z-[80] h-0.5 overflow-hidden transition-opacity duration-300", visible ? "opacity-100" : "opacity-0")}
    >
      {visible ? <div className="h-full w-full origin-left animate-indeterminate bg-accent" /> : null}
    </div>
  );
}
