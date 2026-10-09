import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BRAND_MARK_MAX_SIZE, BrandMark, Logo, PRODUCT_NAME } from "@/components/brand/Logo";

afterEach(cleanup);

describe("BrandMark", () => {
  it("is a decorative raster mark with a size-matched srcset", () => {
    const { container } = render(<BrandMark size={28} />);
    const img = container.querySelector("img[data-brand-mark]") as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.getAttribute("alt")).toBe("");
    expect(img.getAttribute("aria-hidden")).toBe("true");
    expect(img.getAttribute("width")).toBe("28");
    expect(img.getAttribute("sizes")).toBe("28px");
    expect(img.getAttribute("srcset")).toContain("/brand/techsara-mark-32.png 32w");
    expect(img.getAttribute("srcset")).toContain("/brand/techsara-mark-256.png 256w");
    // The theme-aware edge ring comes from the mark-ring token.
    expect(img.className).toContain("ring-mark-ring");
  });

  it("never renders larger than the source artwork allows", () => {
    const { container } = render(<BrandMark size={400} />);
    const img = container.querySelector("img") as HTMLImageElement;
    expect(img.getAttribute("width")).toBe(String(BRAND_MARK_MAX_SIZE));
    expect(img.style.width).toBe(`${BRAND_MARK_MAX_SIZE}px`);
  });

  it("can carry an accessible name when it stands alone", () => {
    render(<BrandMark label="TechSara" />);
    expect(screen.getByRole("img", { name: "TechSara" })).toBeInTheDocument();
  });
});

describe("Logo", () => {
  it("keeps the product name next to the mark, or for screen readers only when collapsed", () => {
    const { rerender } = render(<Logo />);
    expect(screen.getByText(PRODUCT_NAME)).not.toHaveClass("sr-only");
    rerender(<Logo collapsed />);
    expect(screen.getByText(PRODUCT_NAME)).toHaveClass("sr-only");
  });
});
