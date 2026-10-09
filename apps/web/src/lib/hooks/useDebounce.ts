import { useEffect, useMemo, useRef, useState } from "react";

/** Returns `value` after it has stopped changing for `delayMs`. */
export function useDebounce<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export interface DebouncedFunction<Args extends unknown[]> {
  (...args: Args): void;
  cancel(): void;
  flush(): void;
}

/** A stable debounced callback that always invokes the latest `callback`. */
export function useDebouncedCallback<Args extends unknown[]>(
  callback: (...args: Args) => void,
  delayMs = 300,
): DebouncedFunction<Args> {
  const callbackRef = useRef(callback);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingArgs = useRef<Args | null>(null);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  const debounced = useMemo(() => {
    const fn = ((...args: Args) => {
      pendingArgs.current = args;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        const pending = pendingArgs.current;
        pendingArgs.current = null;
        if (pending) callbackRef.current(...pending);
      }, delayMs);
    }) as DebouncedFunction<Args>;
    fn.cancel = () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      pendingArgs.current = null;
    };
    fn.flush = () => {
      if (!timerRef.current) return;
      clearTimeout(timerRef.current);
      timerRef.current = null;
      const pending = pendingArgs.current;
      pendingArgs.current = null;
      if (pending) callbackRef.current(...pending);
    };
    return fn;
  }, [delayMs]);

  useEffect(() => () => debounced.cancel(), [debounced]);
  return debounced;
}
