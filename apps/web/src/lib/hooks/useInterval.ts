import { useEffect, useRef } from "react";

/**
 * Calls `callback` every `delayMs`. Pass `null` to pause. The latest callback
 * is always used, so it may close over fresh state without resetting the timer.
 */
export function useInterval(callback: () => void, delayMs: number | null): void {
  const savedCallback = useRef(callback);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (delayMs === null || delayMs < 0) return;
    const id = setInterval(() => savedCallback.current(), delayMs);
    return () => clearInterval(id);
  }, [delayMs]);
}
