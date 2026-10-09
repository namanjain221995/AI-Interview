import { useCallback, useState } from "react";

/**
 * useState mirrored to localStorage for per-viewer conveniences (collapsed
 * sidebar, table density). Storage failures (private mode, blocked storage)
 * degrade to in-memory state.
 */
export function usePersistentState<T>(key: string, initialValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? initialValue : (JSON.parse(raw) as T);
    } catch {
      return initialValue;
    }
  });

  const update = useCallback(
    (value: T | ((prev: T) => T)) => {
      setState((prev) => {
        const next = typeof value === "function" ? (value as (p: T) => T)(prev) : value;
        try {
          window.localStorage.setItem(key, JSON.stringify(next));
        } catch {
          // Storage unavailable: keep the in-memory value.
        }
        return next;
      });
    },
    [key],
  );

  return [state, update];
}
