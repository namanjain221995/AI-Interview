import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * Live content-box width of an element, for layout decisions CSS can't make
 * (chart density, switching a radar to bars on narrow cards). The first measurement
 * happens in a layout effect, so the right variant renders before the first paint.
 * Returns 0 until measured (and in jsdom, which has no layout).
 */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useIsomorphicLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}
