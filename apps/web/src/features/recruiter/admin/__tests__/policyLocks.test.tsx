import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PolicyFieldsEditor } from "../policies/PolicyFieldsEditor";
import { INTEGRITY_FIELDS } from "../policies/policyFields";

type Key = (typeof INTEGRITY_FIELDS)[number]["key"];
const values = Object.fromEntries(INTEGRITY_FIELDS.map((f) => [f.key, f.kind.type === "boolean" ? false : 15])) as Record<Key, unknown>;

describe("PolicyFieldsEditor locked fields", () => {
  it("shows a platform-required switch as on and locked, and leaves the others editable", () => {
    const onChange = vi.fn();
    render(<PolicyFieldsEditor fields={INTEGRITY_FIELDS} values={{ ...values, enabled: true }} onChange={onChange} locked={new Set(["camera_required", "camera_frame_analysis"])} />);
    const camera = screen.getByRole("switch", { name: /Require the camera/ });
    expect(camera).toBeChecked();
    expect(camera).toBeDisabled();
    expect(screen.getAllByText("Always on")).toHaveLength(2);
    fireEvent.click(camera);
    expect(onChange).not.toHaveBeenCalled();
    const focus = screen.getByRole("switch", { name: /Collect integrity signals/ });
    expect(focus).not.toBeDisabled();
  });
});
