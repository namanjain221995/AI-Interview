/**
 * Display formatting. Every formatter accepts null/undefined and returns the
 * EMPTY placeholder, so tables never render "NaN" or "undefined".
 * Locale defaults to the browser's; pass one explicitly in tests.
 */

export const EMPTY = "—";

type Maybe<T> = T | null | undefined;
type DateInput = Maybe<string | number | Date>;

function isMissingNumber(value: Maybe<number>): value is null | undefined {
  return value === null || value === undefined || Number.isNaN(value);
}

export function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

// ----------------------------------------------------------------- numbers

export function formatNumber(value: Maybe<number>, options: Intl.NumberFormatOptions = {}, locale?: string): string {
  if (isMissingNumber(value)) return EMPTY;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1, ...options }).format(value);
}

/** 1284 -> "1.3K", 4_200_000 -> "4.2M". */
export function formatCompact(value: Maybe<number>, locale?: string): string {
  if (isMissingNumber(value)) return EMPTY;
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

/** Ratio 0-1 -> "73%". */
export function formatPercent(ratio: Maybe<number>, fractionDigits = 0, locale?: string): string {
  if (isMissingNumber(ratio)) return EMPTY;
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(ratio);
}

/**
 * Scores: competency scores use a 0-10 scale (one decimal), overall and rubric
 * scores use 0-100 (integer).
 */
export function formatScore(score: Maybe<number>, scale: 10 | 100 = 100, locale?: string): string {
  if (isMissingNumber(score)) return EMPTY;
  const digits = scale === 10 ? 1 : 0;
  return new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(score);
}

/** 1536 -> "1.5 KB". Uses binary multiples with familiar unit names. */
export function formatBytes(bytes: Maybe<number>, locale?: string): string {
  if (isMissingNumber(bytes)) return EMPTY;
  if (bytes < 1024) return `${formatNumber(bytes, { maximumFractionDigits: 0 }, locale)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${formatNumber(value, { maximumFractionDigits: value < 10 ? 1 : 0 }, locale)} ${units[unit]}`;
}

/** 0.82 -> "High". Words for model/evidence confidence (0-1). */
export function confidenceLabel(confidence: Maybe<number>): string {
  if (isMissingNumber(confidence)) return EMPTY;
  if (confidence >= 0.75) return "High";
  if (confidence >= 0.5) return "Moderate";
  if (confidence >= 0.25) return "Low";
  return "Very low";
}

// --------------------------------------------------------------- durations

/** 750 -> "12m 30s"; 3900 -> "1h 05m"; 42 -> "42s". */
export function formatDuration(totalSeconds: Maybe<number>): string {
  if (isMissingNumber(totalSeconds)) return EMPTY;
  const seconds = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  return `${s}s`;
}

/** 725 -> "12:05"; 3723 -> "1:02:03". For live timers and transcript offsets. */
export function formatClock(totalSeconds: Maybe<number>): string {
  if (isMissingNumber(totalSeconds)) return EMPTY;
  const negative = totalSeconds < 0;
  const seconds = Math.floor(Math.abs(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const body = h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  return negative ? `-${body}` : body;
}

/** Milliseconds offset -> clock ("1:05"). */
export function formatOffsetMs(ms: Maybe<number>): string {
  if (isMissingNumber(ms)) return EMPTY;
  return formatClock(ms / 1000);
}

/** 1830 -> "31 min" (rounded). */
export function formatMinutes(totalSeconds: Maybe<number>): string {
  if (isMissingNumber(totalSeconds)) return EMPTY;
  const minutes = Math.round(totalSeconds / 60);
  return minutes < 60 ? `${minutes} min` : formatDuration(totalSeconds);
}

/** Milliseconds latency -> "180 ms" / "2.4 s". */
export function formatLatency(ms: Maybe<number>, locale?: string): string {
  if (isMissingNumber(ms)) return EMPTY;
  if (ms < 1000) return `${formatNumber(ms, { maximumFractionDigits: 0 }, locale)} ms`;
  return `${formatNumber(ms / 1000, { maximumFractionDigits: 1 }, locale)} s`;
}

// ------------------------------------------------------------------- dates

export function formatDate(value: DateInput, options: Intl.DateTimeFormatOptions = { dateStyle: "medium" }, locale?: string): string {
  const date = toDate(value);
  return date ? new Intl.DateTimeFormat(locale, options).format(date) : EMPTY;
}

export function formatDateTime(value: DateInput, locale?: string): string {
  return formatDate(value, { dateStyle: "medium", timeStyle: "short" }, locale);
}

export function formatTime(value: DateInput, locale?: string): string {
  return formatDate(value, { timeStyle: "short" }, locale);
}

const RELATIVE_UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "just now", "5 minutes ago", "in 3 hours", "yesterday". */
export function formatRelative(value: DateInput, now: Date = new Date(), locale?: string): string {
  const date = toDate(value);
  if (!date) return EMPTY;
  const diffSeconds = (date.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diffSeconds);
  if (abs < 45) return "just now";
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  for (const [unit, size] of RELATIVE_UNITS) {
    if (abs >= size || unit === "minute") {
      return rtf.format(Math.round(diffSeconds / size), unit);
    }
  }
  return rtf.format(Math.round(diffSeconds / 60), "minute");
}

/** Relative within a week, otherwise a calendar date. */
export function formatRelativeOrDate(value: DateInput, now: Date = new Date(), locale?: string): string {
  const date = toDate(value);
  if (!date) return EMPTY;
  return Math.abs(date.getTime() - now.getTime()) < 7 * 24 * 3600 * 1000
    ? formatRelative(date, now, locale)
    : formatDate(date, { dateStyle: "medium" }, locale);
}

/** "Good morning" / "Good afternoon" / "Good evening" for the viewer's local time. */
export function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

// ------------------------------------------------------------------- text

/** "Priya Shah" -> "PS"; "madonna" -> "M"; "" -> "?". */
export function initials(name: Maybe<string>): string {
  if (!name) return "?";
  const parts = name
    .replace(/[^\p{L}\p{N}\s'-]/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0]!.charAt(0);
  const last = parts.length > 1 ? parts[parts.length - 1]!.charAt(0) : "";
  return (first + last).toUpperCase();
}

/** "first_name" -> "First name". */
export function humanize(value: Maybe<string>): string {
  if (!value) return EMPTY;
  const spaced = value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** pluralize(1, "question") -> "1 question"; pluralize(3, "person", "people") -> "3 people". */
export function pluralize(count: number, singular: string, plural?: string, locale?: string): string {
  const word = count === 1 ? singular : (plural ?? `${singular}s`);
  return `${formatNumber(count, { maximumFractionDigits: 0 }, locale)} ${word}`;
}

export function truncate(text: Maybe<string>, max: number): string {
  if (!text) return "";
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** ["a","b","c"] -> "a, b, and c" (locale-aware conjunction). */
export function formatList(items: string[], locale?: string): string {
  return new Intl.ListFormat(locale, { style: "long", type: "conjunction" }).format(items);
}
