/** Small pure helpers shared by the admin editors. */

import type { Page } from "@/lib/types";

/** `{items,total}` envelope from either a page or a bare list. */
export function asPage<T>(raw: unknown): Page<T> {
  if (Array.isArray(raw)) return { items: raw as T[], total: raw.length };
  if (raw && typeof raw === "object") {
    const record = raw as Record<string, unknown>;
    const items = Array.isArray(record.items) ? (record.items as T[]) : [];
    const total = typeof record.total === "number" ? record.total : items.length;
    return { items, total };
  }
  return { items: [], total: 0 };
}

/** Items from a page, a bare list or `{<key>: [...]}`. */
export function asList<T>(raw: unknown, ...keys: string[]): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (raw && typeof raw === "object") {
    const record = raw as Record<string, unknown>;
    for (const key of ["items", ...keys]) if (Array.isArray(record[key])) return record[key] as T[];
  }
  return [];
}

/** snake_case key from a label ("Technical Knowledge" -> "technical_knowledge"). */
export function slugKey(label: string, max = 60): string {
  return label
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, max)
    .replace(/_+$/g, "");
}

/** URL slug ("Two Sum II" -> "two-sum-ii"). */
export function urlSlug(label: string, max = 120): string {
  return slugKey(label, max).replace(/_/g, "-");
}

/** `base`, `base_2`, `base_3`… not present in `taken`. */
export function uniqueKey(base: string, taken: Iterable<string>, separator = "_"): string {
  const set = new Set(taken);
  const root = base || "item";
  if (!set.has(root)) return root;
  for (let n = 2; n < 10_000; n += 1) {
    const candidate = `${root}${separator}${n}`;
    if (!set.has(candidate)) return candidate;
  }
  return `${root}${separator}${Date.now()}`;
}

/** Locally unique id for list rows that have no server id yet. */
let uidCounter = 0;
export function localId(prefix = "tmp"): string {
  uidCounter += 1;
  return `${prefix}_${Date.now().toString(36)}_${uidCounter}`;
}

/** Structural equality for JSON-like values (key order independent). */
export function jsonEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, index) => jsonEqual(item, b[index]));
  }
  if (typeof a === "object") {
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    const keys = new Set([...Object.keys(ao), ...Object.keys(bo)]);
    for (const key of keys) {
      if (ao[key] === undefined && bo[key] === undefined) continue;
      if (!jsonEqual(ao[key], bo[key])) return false;
    }
    return true;
  }
  return false;
}

export function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Parses a number input's text; empty -> null; invalid -> NaN. */
export function parseNumberInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : Number.NaN;
}

/** Trimmed string, or null when blank. */
export function blankToNull(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/** Removes blank entries and trims a string list. */
export function cleanList(values: readonly string[] | null | undefined): string[] {
  return (values ?? []).map((v) => v.trim()).filter(Boolean);
}

/** Copies text to the clipboard; resolves false when the browser refuses. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy path (insecure origins such as http://<lan-ip>).
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
