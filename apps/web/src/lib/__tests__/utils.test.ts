import { describe, expect, it } from "vitest";

import { cn, hashString, safeRedirectPath } from "@/lib/utils";

describe("cn", () => {
  it("merges conflicting Tailwind classes, including custom theme scales", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("text-title", "text-fg-muted")).toBe("text-title text-fg-muted");
    expect(cn("text-sm", "text-title")).toBe("text-title");
    expect(cn("rounded-md", "rounded-control")).toBe("rounded-control");
    expect(cn("shadow-card", "shadow-overlay")).toBe("shadow-overlay");
    expect(cn("bg-surface", false && "hidden", undefined, "bg-primary")).toBe("bg-primary");
  });
});

describe("safeRedirectPath", () => {
  const fallback = "/app/dashboard";

  it("accepts same-origin app paths", () => {
    expect(safeRedirectPath("/app/jobs?status=open", fallback, ["/app"])).toBe("/app/jobs?status=open");
    expect(safeRedirectPath("/app", fallback, ["/app"])).toBe("/app");
  });

  it("rejects open redirects and paths outside the allowed area", () => {
    for (const target of ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/candidate/1", "/application", "", null]) {
      expect(safeRedirectPath(target, fallback, ["/app"])).toBe(fallback);
    }
  });
});

describe("hashString", () => {
  it("is stable and unsigned", () => {
    expect(hashString("Priya Shah")).toBe(hashString("Priya Shah"));
    expect(hashString("a")).not.toBe(hashString("b"));
    expect(hashString("anything")).toBeGreaterThanOrEqual(0);
  });
});
