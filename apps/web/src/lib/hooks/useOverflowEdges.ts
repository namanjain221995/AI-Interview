import { useEffect, type RefObject } from "react";

/**
 * Marks a horizontal scroller with `data-overflow-start` / `data-overflow-end` while
 * content is hidden past that edge. Pair with the `edge-fade` utility (globals.css),
 * which fades the matching edge, and `scrollbar-none` for tab bars and chip rows.
 *
 * Attributes are written directly on the element (no React state), so scrolling never
 * re-renders. Re-measures on scroll, resize and child changes (tabs added/removed).
 */
export function useOverflowEdges<T extends HTMLElement>(ref: RefObject<T | null>): void {
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const max = element.scrollWidth - element.clientWidth;
      // scrollLeft is negative in RTL layouts; compare magnitudes.
      const position = Math.abs(element.scrollLeft);
      element.toggleAttribute("data-overflow-start", max > 1 && position > 1);
      element.toggleAttribute("data-overflow-end", max > 1 && position < max - 1);
    };
    const schedule = () => {
      if (frame) return;
      frame = typeof window.requestAnimationFrame === "function" ? window.requestAnimationFrame(measure) : (measure(), 0);
    };
    measure();
    element.addEventListener("scroll", schedule, { passive: true });
    const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(schedule) : null;
    resize?.observe(element);
    const mutations = typeof MutationObserver !== "undefined" ? new MutationObserver(schedule) : null;
    mutations?.observe(element, { childList: true, subtree: true, characterData: true });
    return () => {
      element.removeEventListener("scroll", schedule);
      resize?.disconnect();
      mutations?.disconnect();
      if (frame && typeof window.cancelAnimationFrame === "function") window.cancelAnimationFrame(frame);
    };
  }, [ref]);
}

/**
 * Scrolls a horizontal scroller (only that element, never the page) so `child` is
 * fully visible. When it has to move, it centres the child, so the neighbours on both
 * sides stay visible (and out of the edge fade).
 */
export function revealInScroller(scroller: HTMLElement, child: HTMLElement, padding = 32): void {
  if (scroller.scrollWidth <= scroller.clientWidth + 1) return;
  const scrollerRect = scroller.getBoundingClientRect();
  const childRect = child.getBoundingClientRect();
  const visible = childRect.left >= scrollerRect.left + padding && childRect.right <= scrollerRect.right - padding;
  if (visible) return;
  const childCentre = childRect.left - scrollerRect.left + scroller.scrollLeft + childRect.width / 2;
  const target = childCentre - scroller.clientWidth / 2;
  scroller.scrollLeft = Math.max(0, Math.min(target, scroller.scrollWidth - scroller.clientWidth));
}
