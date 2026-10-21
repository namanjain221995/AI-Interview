import { PRODUCT_NAME } from "@/lib/brand";
import { cn } from "@/lib/utils";

export { PRODUCT_FULL_NAME, PRODUCT_NAME } from "@/lib/brand";
/** Company behind the product; the mark is its logo. */
export const COMPANY_NAME = "TechSara";

/**
 * The source artwork is only ~164 px, so the mark is never drawn larger than this
 * (CSS px). Larger requests are clamped rather than upscaled into a blur.
 */
export const BRAND_MARK_MAX_SIZE = 96;

const BASE = (import.meta.env.BASE_URL as string | undefined) ?? "/";
const MARK_WIDTHS = [32, 48, 64, 128, 192, 256] as const;
const markUrl = (width: number) => `${BASE}brand/techsara-mark-${width}.png`;
const MARK_SRCSET = MARK_WIDTHS.map((w) => `${markUrl(w)} ${w}w`).join(", ");

export interface BrandMarkProps {
  /** Rendered size in CSS px (clamped to BRAND_MARK_MAX_SIZE). */
  size?: number;
  className?: string;
  /**
   * Accessible name. Leave empty (default) when the mark sits next to visible text
   * such as the product name; it is then hidden from assistive technology.
   */
  label?: string;
}

/**
 * Company mark: the TechSara paper plane in a light-blue disc (raster, from public/brand).
 * The disc is light in both themes; on dark surfaces a faint light ring (--mark-ring)
 * keeps its edge from dissolving into the canvas, and in light a hairline does the same
 * against white cards. The browser picks the PNG for the size and pixel density.
 */
export function BrandMark({ size = 24, className, label }: BrandMarkProps) {
  const px = Math.max(12, Math.min(size, BRAND_MARK_MAX_SIZE));
  return (
    <img
      src={markUrl(px <= 24 ? 48 : 128)}
      srcSet={MARK_SRCSET}
      sizes={`${px}px`}
      width={px}
      height={px}
      alt={label ?? ""}
      aria-hidden={label ? undefined : true}
      decoding="async"
      draggable={false}
      data-brand-mark=""
      className={cn("shrink-0 rounded-full ring-1 ring-mark-ring select-none", className)}
      style={{ width: px, height: px }}
    />
  );
}

export function Logo({ collapsed = false, className }: { collapsed?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <BrandMark size={28} />
      {!collapsed ? (
        <span className="text-[15px] leading-none font-semibold tracking-[-0.012em] text-fg">{PRODUCT_NAME}</span>
      ) : (
        <span className="sr-only">{PRODUCT_NAME}</span>
      )}
    </span>
  );
}
