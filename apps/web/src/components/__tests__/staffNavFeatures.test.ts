import { describe, expect, it } from "vitest";

import { visibleNav } from "@/components/layout/staffNav";
import { hasFeature, permissionsForRole } from "@/lib/auth";

const keys = (hasIntake: boolean) =>
  visibleNav(permissionsForRole("RECRUITER"), (feature) => feature === "intake" && hasIntake).flatMap((group) => group.items.map((item) => item.key));

describe("plan features in the staff navigation (G8)", () => {
  it("shows intake only to organisations whose plan includes it", () => {
    expect(keys(false)).not.toContain("intake");
    expect(keys(true)).toContain("intake");
    expect(keys(false)).toContain("jobs");
  });

  it("reads features from the organisation; platform staff see every feature", () => {
    const user = { role: "RECRUITER" } as never;
    expect(hasFeature({ user, organization: { id: "o", name: "Acme", slug: "acme" } }, "intake")).toBe(false);
    expect(hasFeature({ user, organization: { id: "o", name: "Acme", slug: "acme", features: { intake: true } } }, "intake")).toBe(true);
    expect(hasFeature({ user: { role: "SUPER_ADMIN" } as never, organization: null }, "intake")).toBe(true);
  });
});
