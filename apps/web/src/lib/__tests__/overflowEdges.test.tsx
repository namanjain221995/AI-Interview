import { act, render } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";

import { revealInScroller, useOverflowEdges } from "@/lib/hooks/useOverflowEdges";

function setBox(el: HTMLElement, box: { scrollWidth: number; clientWidth: number; scrollLeft?: number }) {
  Object.defineProperty(el, "scrollWidth", { configurable: true, value: box.scrollWidth });
  Object.defineProperty(el, "clientWidth", { configurable: true, value: box.clientWidth });
  el.scrollLeft = box.scrollLeft ?? 0;
}

function Scroller({ onReady }: { onReady: (el: HTMLDivElement) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useOverflowEdges(ref);
  return (
    <div
      data-testid="scroller"
      ref={(node) => {
        ref.current = node;
        if (node) onReady(node);
      }}
    />
  );
}

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

describe("useOverflowEdges", () => {
  it("marks the edges that hide content and updates on scroll", async () => {
    let el!: HTMLDivElement;
    // Give the element its box before the hook's first measurement.
    const { getByTestId } = render(<Scroller onReady={(node) => setBox((el = node), { scrollWidth: 600, clientWidth: 200 })} />);
    const scroller = getByTestId("scroller");
    expect(scroller).toHaveAttribute("data-overflow-end");
    expect(scroller).not.toHaveAttribute("data-overflow-start");

    el.scrollLeft = 150;
    await act(async () => {
      el.dispatchEvent(new Event("scroll"));
      await nextFrame();
    });
    expect(scroller).toHaveAttribute("data-overflow-start");
    expect(scroller).toHaveAttribute("data-overflow-end");

    el.scrollLeft = 400;
    await act(async () => {
      el.dispatchEvent(new Event("scroll"));
      await nextFrame();
    });
    expect(scroller).toHaveAttribute("data-overflow-start");
    expect(scroller).not.toHaveAttribute("data-overflow-end");
  });

  it("sets nothing when everything fits", () => {
    const { getByTestId } = render(<Scroller onReady={(node) => setBox(node, { scrollWidth: 200, clientWidth: 200 })} />);
    expect(getByTestId("scroller")).not.toHaveAttribute("data-overflow-start");
    expect(getByTestId("scroller")).not.toHaveAttribute("data-overflow-end");
  });
});

describe("revealInScroller", () => {
  function rect(left: number, width: number): DOMRect {
    return { left, right: left + width, width, top: 0, bottom: 40, height: 40, x: left, y: 0, toJSON: () => ({}) } as DOMRect;
  }

  it("centres a hidden child inside the scroller only", () => {
    const scroller = document.createElement("div");
    const child = document.createElement("button");
    scroller.appendChild(child);
    setBox(scroller, { scrollWidth: 1000, clientWidth: 300 });
    scroller.getBoundingClientRect = () => rect(0, 300);
    child.getBoundingClientRect = () => rect(700, 80); // far right, hidden
    revealInScroller(scroller, child);
    expect(scroller.scrollLeft).toBe(700 + 40 - 150);
  });

  it("leaves a fully visible child alone", () => {
    const scroller = document.createElement("div");
    const child = document.createElement("button");
    setBox(scroller, { scrollWidth: 1000, clientWidth: 300, scrollLeft: 50 });
    scroller.getBoundingClientRect = () => rect(0, 300);
    child.getBoundingClientRect = () => rect(100, 80);
    revealInScroller(scroller, child);
    expect(scroller.scrollLeft).toBe(50);
  });
});
