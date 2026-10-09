import { useLayoutEffect, useRef, useState } from "react";

/**
 * One DOM node that lives as long as the component that owns it. Render into it
 * with a single `createPortal` and attach it where it is needed with `<PersistentSlot>`:
 * moving between layouts re-parents the node instead of remounting what is inside
 * (the interviewer's WebGL canvas, its animation and lip-sync state survive).
 */
export function usePersistentNode(className = "size-full"): HTMLDivElement {
  const [node] = useState(() => {
    const element = document.createElement("div");
    element.className = className;
    return element;
  });
  return node;
}

/** A place in the layout where the persistent node is attached while this slot is mounted. */
export function PersistentSlot({ node, className }: { node: HTMLDivElement; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const slot = ref.current;
    if (slot && node.parentElement !== slot) slot.appendChild(node);
  }, [node]);
  return <div ref={ref} className={className} />;
}
