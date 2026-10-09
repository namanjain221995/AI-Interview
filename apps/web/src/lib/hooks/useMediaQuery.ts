import { useCallback, useSyncExternalStore } from "react";

/** Live `matchMedia` result. Returns `defaultValue` where matchMedia is unavailable. */
export function useMediaQuery(query: string, defaultValue = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );
  const getSnapshot = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : defaultValue;
  return useSyncExternalStore(subscribe, getSnapshot, () => defaultValue);
}

/** Tailwind `lg` breakpoint and up. */
export function useIsDesktop(): boolean {
  return useMediaQuery("(min-width: 1024px)", true);
}

export function usePrefersReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}
