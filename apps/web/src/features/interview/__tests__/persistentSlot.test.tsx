import { fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { describe, expect, it } from "vitest";

import { PersistentSlot, usePersistentNode } from "../components/PersistentSlot";

let mounts = 0;

function Interviewer() {
  useEffect(() => {
    mounts += 1;
  }, []);
  return <canvas data-testid="avatar-canvas" />;
}

function Room({ coding }: { coding: boolean }) {
  const node = usePersistentNode();
  return (
    <div>
      {createPortal(<Interviewer />, node)}
      {coding ? (
        <aside data-testid="coding-aside">
          <PersistentSlot node={node} />
        </aside>
      ) : (
        <main data-testid="stage">
          <PersistentSlot node={node} />
        </main>
      )}
    </div>
  );
}

function Harness() {
  const [coding, setCoding] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setCoding((v) => !v)}>
        toggle
      </button>
      <Room coding={coding} />
    </>
  );
}

describe("PersistentSlot", () => {
  it("moves the interviewer between layouts without remounting it", () => {
    mounts = 0;
    render(<Harness />);
    const canvas = screen.getByTestId("avatar-canvas");
    expect(screen.getByTestId("stage")).toContainElement(canvas);
    fireEvent.click(screen.getByText("toggle"));
    expect(screen.getByTestId("coding-aside")).toContainElement(canvas);
    fireEvent.click(screen.getByText("toggle"));
    expect(screen.getByTestId("stage")).toContainElement(canvas);
    // Same element, mounted once: the WebGL context and animation state survive.
    expect(screen.getByTestId("avatar-canvas")).toBe(canvas);
    expect(mounts).toBe(1);
  });
});
