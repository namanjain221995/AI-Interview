/**
 * Maps API validation errors onto editor field paths.
 *
 * The backend reports problems in three shapes:
 *   - pydantic: `details.errors: [{loc: ["body", "config", "rubric"], msg}]`
 *   - domain:   `details.field: "email"` with the message on the error itself
 *   - map:      `details.fields: {name: message}`
 * Paths are dotted (`config.competencies.0.weight`) after dropping the
 * request part (`body`/`query`/`path`).
 */

import { isApiError } from "@/lib/api";

export type FieldErrors = Record<string, string>;

const VALUE_ERROR_PREFIX = /^(value error|assertion failed),\s*/i;

/** Pydantic prefixes custom validator messages with "Value error, "; people don't need that. */
export function cleanMessage(message: string): string {
  const trimmed = message.replace(VALUE_ERROR_PREFIX, "").trim();
  return trimmed ? trimmed.charAt(0).toUpperCase() + trimmed.slice(1) : message;
}

/** Every field error carried by `error`, keyed by dotted path. */
export function fieldErrorsOf(error: unknown): FieldErrors {
  if (!isApiError(error)) return {};
  const out: FieldErrors = {};
  for (const [path, message] of Object.entries(error.fieldErrors())) out[path] = cleanMessage(message);
  const single = error.details.field;
  if (typeof single === "string" && single && !(single in out)) {
    const problems = error.details.problems;
    const detail = Array.isArray(problems) && problems.every((p) => typeof p === "string") ? (problems as string[]).join(" ") : null;
    out[single] = cleanMessage(detail ?? error.message);
  }
  return out;
}

/**
 * Field errors re-rooted under `prefix` (e.g. "config" for a round payload).
 * Paths outside the prefix are kept unless `onlyPrefixed` is set.
 */
export function rebaseErrors(errors: FieldErrors, prefix: string, { onlyPrefixed = false } = {}): FieldErrors {
  const out: FieldErrors = {};
  const head = `${prefix}.`;
  for (const [path, message] of Object.entries(errors)) {
    if (path === prefix) out[""] = message;
    else if (path.startsWith(head)) out[path.slice(head.length)] = message;
    else if (!onlyPrefixed) out[path] = message;
  }
  return out;
}

/** First error at or below `path` (so a section can show its children's problems). */
export function errorAt(errors: FieldErrors | undefined, path: string): string | undefined {
  if (!errors) return undefined;
  if (errors[path]) return errors[path];
  const head = `${path}.`;
  for (const [key, message] of Object.entries(errors)) if (key.startsWith(head)) return message;
  return undefined;
}

/** A readable line for a failed request: message plus request id for server faults. */
export function describeFailure(error: unknown, fallback = "Something went wrong. Try again."): string {
  if (!isApiError(error)) return error instanceof Error && error.message ? error.message : fallback;
  const suffix = error.isServerError && error.requestId ? ` (request ID ${error.requestId})` : "";
  return `${error.message || fallback}${suffix}`;
}

/**
 * Every validation message in the error, including model-level ones that have
 * no field (pydantic `loc: ["body"]`), as `{path, message}` with "" for the root.
 */
export function serverMessages(error: unknown): Array<{ path: string; message: string }> {
  if (!isApiError(error)) return [];
  const out: Array<{ path: string; message: string }> = [];
  const errors = error.details.errors;
  if (Array.isArray(errors)) {
    for (const item of errors) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      const message = typeof record.msg === "string" ? record.msg : typeof record.message === "string" ? record.message : null;
      if (!message) continue;
      const loc = Array.isArray(record.loc) ? record.loc.filter((p) => p !== "body" && p !== "query" && p !== "path").map(String) : [];
      out.push({ path: typeof record.field === "string" ? record.field : loc.join("."), message: cleanMessage(message) });
    }
  }
  return out;
}

/** Messages for problems the editor can't attach to a visible field. */
export function unplacedMessages(error: unknown, placed: Iterable<string>): string[] {
  const known = new Set(placed);
  return serverMessages(error)
    .filter((m) => !m.path || !known.has(m.path))
    .map((m) => (m.path ? `${m.path}: ${m.message}` : m.message));
}

/**
 * Errors for a round or template payload, keyed by RoundConfig path: pydantic
 * errors under `config.*`, plus the reference check
 * (`unknown_competency_keys`, `unknown_coding_question_ids`).
 */
export function roundConfigErrorsOf(error: unknown): FieldErrors {
  const out = rebaseErrors(fieldErrorsOf(error), "config");
  if (!isApiError(error)) return out;
  const keys = error.details.unknown_competency_keys;
  if (Array.isArray(keys) && keys.length) out.competencies = `Not in your competency catalog: ${keys.join(", ")}.`;
  const ids = error.details.unknown_coding_question_ids;
  if (Array.isArray(ids) && ids.length) out["coding.question_ids"] = `${ids.length} pinned ${ids.length === 1 ? "problem no longer exists" : "problems no longer exist"}. Remove ${ids.length === 1 ? "it" : "them"} and save again.`;
  return out;
}
