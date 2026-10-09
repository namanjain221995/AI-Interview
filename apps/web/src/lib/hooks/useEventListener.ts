import { useEffect, useRef, type RefObject } from "react";

type Target = Window | Document | HTMLElement | MediaQueryList | null | undefined;

export function useEventListener<K extends keyof WindowEventMap>(
  type: K,
  handler: (event: WindowEventMap[K]) => void,
  target?: Window | null,
  options?: boolean | AddEventListenerOptions,
): void;
export function useEventListener<K extends keyof DocumentEventMap>(
  type: K,
  handler: (event: DocumentEventMap[K]) => void,
  target: Document | null,
  options?: boolean | AddEventListenerOptions,
): void;
export function useEventListener<K extends keyof HTMLElementEventMap, E extends HTMLElement = HTMLElement>(
  type: K,
  handler: (event: HTMLElementEventMap[K]) => void,
  target: RefObject<E | null> | E | null,
  options?: boolean | AddEventListenerOptions,
): void;
/**
 * Subscribes to a DOM event for the component's lifetime. Defaults to
 * `window`; accepts a document, an element or a ref. The handler may change
 * on every render without re-subscribing.
 */
export function useEventListener(
  type: string,
  handler: (event: Event) => void,
  target?: Target | RefObject<HTMLElement | null>,
  options?: boolean | AddEventListenerOptions,
): void {
  const savedHandler = useRef(handler);

  useEffect(() => {
    savedHandler.current = handler;
  }, [handler]);

  const capture = typeof options === "boolean" ? options : options?.capture;
  const passive = typeof options === "object" ? options.passive : undefined;
  const once = typeof options === "object" ? options.once : undefined;

  useEffect(() => {
    const resolved: Target =
      target === undefined
        ? typeof window !== "undefined"
          ? window
          : null
        : target && "current" in target
          ? target.current
          : (target as Target);
    if (!resolved?.addEventListener) return;
    const listener = (event: Event) => savedHandler.current(event);
    const opts: AddEventListenerOptions = { capture, passive, once };
    resolved.addEventListener(type, listener, opts);
    return () => resolved.removeEventListener(type, listener, opts);
  }, [type, target, capture, passive, once]);
}
