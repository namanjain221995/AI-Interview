import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Teach tailwind-merge the custom theme scales from globals.css so that, for
// example, `text-title` is merged as a font size (not a colour) and
// `rounded-control` / `shadow-card` replace other radii / shadows.
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["2xs", "heading", "title", "display"],
      radius: ["xs", "control"],
      shadow: ["card", "raised", "overlay"],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Stable, non-cryptographic 32-bit hash (FNV-1a) used for deterministic tints. */
export function hashString(value: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * Accepts only same-origin, path-absolute redirect targets ("/app/jobs?x=1").
 * Rejects protocol-relative ("//evil.com"), backslash tricks and absolute URLs
 * so a crafted `?next=` can never redirect off-site.
 */
export function safeRedirectPath(target: string | null | undefined, fallback: string, allowedPrefixes?: string[]): string {
  if (!target || typeof target !== "string") return fallback;
  if (!target.startsWith("/") || target.startsWith("//") || target.includes("\\")) return fallback;
  if (/[\u0000-\u001f]/.test(target)) return fallback;
  if (allowedPrefixes && !allowedPrefixes.some((prefix) => target === prefix || target.startsWith(`${prefix}/`) || target.startsWith(`${prefix}?`))) {
    return fallback;
  }
  return target;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
