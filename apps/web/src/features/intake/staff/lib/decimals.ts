/**
 * Display helpers for the decimal strings the intake API sends ("3.6700", "0.20").
 *
 * Totals are exact on the server and bands are decided there. Formatting therefore
 * truncates instead of rounding: 4.1950 shows as "4.19", never "4.20", so a displayed
 * total can never sit on the far side of a band edge from the band it carries. The
 * web never recomputes a total or a band from these values.
 */

export type DecimalInput = string | number | null | undefined;

const DECIMAL = /^\s*([+-])?(\d*)(?:\.(\d*))?\s*$/;

/** Canonical decimal string ("-0.5" style) or null for anything that isn't a number. */
export function normalizeDecimal(value: DecimalInput): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    // Six places keep float artefacts (4.199999999) out of the truncation below.
    return trimTrailing(value.toFixed(6));
  }
  const match = DECIMAL.exec(value);
  if (!match || (!match[2] && !match[3])) return null;
  const sign = match[1] === "-" ? "-" : "";
  const int = (match[2] || "0").replace(/^0+(?=\d)/, "");
  const frac = match[3] ?? "";
  return trimTrailing(`${sign}${int}${frac ? `.${frac}` : ""}`);
}

function trimTrailing(text: string): string {
  if (!text.includes(".")) return text;
  const trimmed = text.replace(/0+$/, "").replace(/\.$/, "");
  return trimmed === "-0" ? "0" : trimmed;
}

/** Number for bar widths and sorting only; never for totals or bands. */
export function decimalToNumber(value: DecimalInput): number | null {
  const text = normalizeDecimal(value);
  if (text === null) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

/** Truncate (toward zero) to `places` decimals and pad: ("4.1950", 2) -> "4.19". */
export function formatDecimal(value: DecimalInput, places = 2, empty = "—"): string {
  const text = normalizeDecimal(value);
  if (text === null) return empty;
  const negative = text.startsWith("-");
  const [int = "0", frac = ""] = (negative ? text.slice(1) : text).split(".");
  const kept = places > 0 ? (frac + "0".repeat(places)).slice(0, places) : "";
  const body = places > 0 ? `${int}.${kept}` : int;
  const isZero = /^0(\.0*)?$/.test(body);
  return negative && !isZero ? `-${body}` : body;
}

/** Weighted total on the 1-5 scale: "3.67". */
export function formatTotal(value: DecimalInput, empty = "—"): string {
  return formatDecimal(value, 2, empty);
}

/** Competency score (Numeric(3,1)): "4.0", "3.4". */
export function formatScore5(value: DecimalInput, empty = "—"): string {
  return formatDecimal(value, 1, empty);
}

/** "0.20" -> "20%"; "0.05" -> "5%". Exact string arithmetic (two places). */
export function formatWeightPercent(value: DecimalInput, empty = "—"): string {
  const text = normalizeDecimal(value);
  if (text === null) return empty;
  const [int = "0", frac = ""] = text.replace(/^-/, "").split(".");
  const padded = (frac + "0000").slice(0, 4);
  const whole = String(Number(int) * 100 + Number(padded.slice(0, 2)));
  const rest = padded.slice(2).replace(/0+$/, "");
  return `${text.startsWith("-") ? "-" : ""}${whole}${rest ? `.${rest}` : ""}%`;
}

/** Out-of-100 conversion: "73.400" -> "73.4". */
export function formatHundred(value: DecimalInput, empty = "—"): string {
  return formatDecimal(value, 1, empty);
}

/** 0-1 share of a 1-5 value, for bar widths (scale minimum 0). */
export function shareOf(value: DecimalInput, max = 5): number {
  const n = decimalToNumber(value);
  if (n === null || max <= 0) return 0;
  return Math.max(0, Math.min(1, n / max));
}
