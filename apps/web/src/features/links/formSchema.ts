/**
 * Registration form schema helpers (OPEN_LINKS.md section 2): the built-in field
 * catalog (the server's `GET /open-links/form/catalog` is authoritative; this copy
 * is the offline default), key derivation for custom fields, local schema checks
 * for instant builder feedback, and the candidate-side answer validation that
 * mirrors `form_schema.validate_answers`. The server decides in every case.
 */

import type { AnswerValue, FieldType, FormField, FormSchema, PublicFormField } from "./types";

export const MAX_FIELDS = 30;
export const MAX_OPTIONS = 50;
export const LABEL_MAX = 120;
export const HELP_MAX = 300;
export const PLACEHOLDER_MAX = 120;
export const OPTION_MAX = 120;
export const DATE_MAX_DAYS = 730;
export const CUSTOM_KEY = /^c_[a-z0-9_]{1,40}$/;

/** Built-ins that are always collected (identity of the registration). */
export const LOCKED_BUILTINS: ReadonlySet<string> = new Set(["full_name", "email"]);

export const NOTICE_PERIOD_OPTIONS = ["Immediately", "15 days", "30 days", "60 days", "90 days", "More than 90 days"];

export const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  text: "Short text",
  textarea: "Paragraph",
  number: "Number",
  email: "Email",
  phone: "Phone number",
  url: "Web address",
  select: "Single choice",
  multiselect: "Multiple choice",
  checkbox: "Checkbox",
  date: "Availability date",
};

export const FIELD_TYPE_HINTS: Record<FieldType, string> = {
  text: "One line, up to 200 characters",
  textarea: "Several lines, up to 1,000 characters",
  number: "A number, with optional limits",
  email: "An email address",
  phone: "A phone number",
  url: "A link starting with http or https",
  select: "Pick one option from a list",
  multiselect: "Pick any number of options",
  checkbox: "A yes/no tick, e.g. a confirmation",
  date: "A date from today up to two years ahead",
};

function field(partial: Partial<FormField> & Pick<FormField, "key" | "type" | "label">): FormField {
  return {
    builtin: false,
    help_text: null,
    placeholder: null,
    required: false,
    enabled: true,
    options: [],
    min: null,
    max: null,
    max_length: null,
    ...partial,
  };
}

/** Mirror of the server catalog (section 2.1 table), in display order. */
export const BUILTIN_FIELDS: readonly FormField[] = [
  field({ key: "full_name", builtin: true, type: "text", label: "Full name", required: true, enabled: true, max_length: 200 }),
  field({ key: "email", builtin: true, type: "email", label: "Email", required: true, enabled: true }),
  field({ key: "phone", builtin: true, type: "phone", label: "Mobile number", required: true, enabled: true }),
  field({ key: "location", builtin: true, type: "text", label: "City", enabled: false, max_length: 120 }),
  field({ key: "linkedin_url", builtin: true, type: "url", label: "LinkedIn profile", enabled: false }),
  field({ key: "portfolio_url", builtin: true, type: "url", label: "Portfolio or GitHub", enabled: false }),
  field({ key: "current_title", builtin: true, type: "text", label: "Current job title", enabled: false }),
  field({ key: "current_company", builtin: true, type: "text", label: "Current company", enabled: false }),
  field({ key: "years_experience", builtin: true, type: "number", label: "Years of experience", enabled: false, min: 0, max: 60 }),
  field({ key: "notice_period", builtin: true, type: "select", label: "Notice period", enabled: false, options: NOTICE_PERIOD_OPTIONS }),
  field({ key: "available_from", builtin: true, type: "date", label: "Available from", enabled: false }),
];

export function defaultSchema(): FormSchema {
  return { version: 0, fields: BUILTIN_FIELDS.map((f) => ({ ...f, options: [...f.options] })) };
}

/** Fills missing properties so the builder can rely on every key being present. */
export function normalizeField(raw: Partial<FormField> & { key: string }): FormField {
  const type = (raw.type ?? "text") as FieldType;
  return field({
    ...raw,
    key: raw.key,
    type,
    label: raw.label ?? raw.key,
    builtin: Boolean(raw.builtin),
    help_text: raw.help_text ?? null,
    placeholder: raw.placeholder ?? null,
    required: Boolean(raw.required),
    enabled: raw.enabled ?? true,
    options: Array.isArray(raw.options) ? raw.options.filter((o): o is string => typeof o === "string") : [],
    min: typeof raw.min === "number" ? raw.min : null,
    max: typeof raw.max === "number" ? raw.max : null,
    max_length: typeof raw.max_length === "number" ? raw.max_length : null,
  });
}

export function normalizeSchema(raw: unknown): FormSchema {
  const record = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const fields = Array.isArray(record.fields)
    ? (record.fields as unknown[])
        .filter((f): f is Partial<FormField> & { key: string } => Boolean(f) && typeof (f as { key?: unknown }).key === "string")
        .map(normalizeField)
    : [];
  return { version: typeof record.version === "number" ? record.version : 0, fields: fields.length ? fields : defaultSchema().fields };
}

/** `c_<slug>` from a label, unique among `taken`, within the server's key pattern. */
export function deriveKey(label: string, taken: Iterable<string>): string {
  const slug = label
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 36)
    .replace(/_+$/g, "");
  const base = `c_${slug || "field"}`;
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let n = 2; n < 1000; n += 1) {
    const candidate = `${base}_${n}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${base}_${Date.now() % 100_000}`;
}

const NEW_FIELD_LABELS: Record<FieldType, string> = {
  text: "New question",
  textarea: "Tell us more",
  number: "Number",
  email: "Alternate email",
  phone: "Alternate phone",
  url: "Link",
  select: "Choose one",
  multiselect: "Choose any",
  checkbox: "I confirm my details are correct",
  date: "Available from",
};

export function newCustomField(type: FieldType, taken: Iterable<string>): FormField {
  const label = NEW_FIELD_LABELS[type];
  const options = type === "select" || type === "multiselect" ? ["Option 1", "Option 2"] : [];
  return field({ key: deriveKey(label, taken), type, label, options, required: type === "checkbox" });
}

export function moveField(fields: readonly FormField[], index: number, delta: -1 | 1): FormField[] {
  const target = index + delta;
  if (target < 0 || target >= fields.length) return [...fields];
  const next = [...fields];
  [next[index], next[target]] = [next[target]!, next[index]!];
  return next;
}

// ------------------------------------------------------------ schema checks

/** Structural problems the server would reject as `validation_failed`, keyed by field key. */
export function checkSchema(schema: FormSchema): Record<string, string> {
  const errors: Record<string, string> = {};
  if (schema.fields.length > MAX_FIELDS) errors._form = `A form can have at most ${MAX_FIELDS} fields.`;
  const keys = new Set<string>();
  for (const f of schema.fields) {
    const label = f.label.trim();
    let message: string | null = null;
    if (!label) message = "Give this field a label.";
    else if (label.length > LABEL_MAX) message = `Keep the label under ${LABEL_MAX} characters.`;
    else if ((f.help_text ?? "").length > HELP_MAX) message = `Keep the help text under ${HELP_MAX} characters.`;
    else if ((f.placeholder ?? "").length > PLACEHOLDER_MAX) message = `Keep the placeholder under ${PLACEHOLDER_MAX} characters.`;
    else if (!f.builtin && !CUSTOM_KEY.test(f.key)) message = "This field's key is invalid. Delete it and add it again.";
    else if (keys.has(f.key)) message = "Two fields share this key. Rename one of them.";
    else if (f.type === "select" || f.type === "multiselect") {
      const options = f.options.map((o) => o.trim());
      const lower = options.map((o) => o.toLowerCase());
      if (options.length === 0) message = "Add at least one option.";
      else if (options.length > MAX_OPTIONS) message = `A field can have at most ${MAX_OPTIONS} options.`;
      else if (options.some((o) => !o)) message = "Options can't be empty.";
      else if (options.some((o) => o.length > OPTION_MAX)) message = `Keep each option under ${OPTION_MAX} characters.`;
      else if (new Set(lower).size !== lower.length) message = "Each option must be different.";
    }
    if (!message && f.min !== null && f.max !== null && f.min > f.max) message = "The minimum is larger than the maximum.";
    keys.add(f.key);
    if (message) errors[f.key] = message;
  }
  return errors;
}

/** The fields a candidate sees: enabled only, without builder flags. */
export function publicFields(schema: FormSchema): PublicFormField[] {
  return schema.fields.filter((f) => f.enabled).map(({ builtin: _b, enabled: _e, ...rest }) => rest);
}

// ----------------------------------------------------------- answer checks

/** Raw input state of the candidate form (strings while typing). */
export type FormValues = Record<string, string | boolean | string[]>;

export function initialValues(fields: readonly PublicFormField[]): FormValues {
  const out: FormValues = {};
  for (const f of fields) out[f.key] = f.type === "checkbox" ? false : f.type === "multiselect" ? [] : "";
  return out;
}

const EMAIL_RX = /^[^\s@]+@[^\s@]+\.[^\s@.]{2,}$/;
const PHONE_CHARS = /^[0-9 +\-().]+$/;
const DATE_RX = /^\d{4}-\d{2}-\d{2}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

function textLimit(f: PublicFormField): number {
  if (f.type === "textarea") return Math.min(f.max_length ?? 1000, 2000);
  if (f.type === "url") return 500;
  if (f.type === "email") return 320;
  return Math.min(f.max_length ?? 200, 500);
}

/** `YYYY-MM-DD` in UTC, `days` from today. */
export function isoDay(days = 0, now: Date = new Date()): string {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + days));
  return d.toISOString().slice(0, 10);
}

function decimals(value: number): number {
  const text = String(value);
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

/** Candidate-safe message for one field, or null when the value is acceptable. */
export function checkAnswer(f: PublicFormField, raw: FormValues[string] | undefined, now: Date = new Date()): string | null {
  if (f.type === "checkbox") {
    if (f.required && raw !== true) return "Tick this box to continue.";
    return null;
  }
  if (f.type === "multiselect") {
    const list = Array.isArray(raw) ? raw : [];
    if (f.required && list.length === 0) return "Choose at least one option.";
    if (list.some((v) => !f.options.includes(v))) return "Choose from the options shown.";
    if (f.max !== null && list.length > f.max) return `Choose at most ${f.max}.`;
    return null;
  }
  const value = typeof raw === "string" ? raw.trim() : "";
  if (!value) {
    if (!f.required) return null;
    return f.type === "select" ? "Choose an option." : "Fill in this field.";
  }
  if (CONTROL.test(value)) return "Remove the special characters from this answer.";
  switch (f.type) {
    case "text":
    case "textarea": {
      const limit = textLimit(f);
      return value.length > limit ? `Keep this under ${limit.toLocaleString()} characters.` : null;
    }
    case "email":
      return value.length > 320 || !EMAIL_RX.test(value) ? "Enter an email address like name@example.com." : null;
    case "phone": {
      const digits = value.replace(/\D/g, "").length;
      if (!PHONE_CHARS.test(value)) return "Use only digits, spaces and + - ( ) .";
      return digits < 7 || digits > 15 ? "Enter a phone number with 7 to 15 digits." : null;
    }
    case "url": {
      if (value.length > 500) return "This link is too long.";
      try {
        const url = new URL(value);
        if (!["http:", "https:"].includes(url.protocol) || !url.hostname) throw new Error("bad");
      } catch {
        return "Enter a full link starting with https://";
      }
      return null;
    }
    case "number": {
      const n = Number(value);
      if (!Number.isFinite(n)) return "Enter a number.";
      if (decimals(n) > 2) return "Use at most two decimal places.";
      if (f.min !== null && n < f.min) return `Enter ${f.min} or more.`;
      if (f.max !== null && n > f.max) return `Enter ${f.max} or less.`;
      return null;
    }
    case "select":
      return f.options.includes(value) ? null : "Choose from the options shown.";
    case "date": {
      if (!DATE_RX.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) return "Enter a date.";
      if (value < isoDay(-1, now)) return "Choose today or a later date.";
      if (value > isoDay(DATE_MAX_DAYS, now)) return "Choose a date within the next two years.";
      return null;
    }
    default:
      return null;
  }
}

export function validateValues(fields: readonly PublicFormField[], values: FormValues, now: Date = new Date()): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of fields) {
    const message = checkAnswer(f, values[f.key], now);
    if (message) errors[f.key] = message;
  }
  return errors;
}

/** Wire answers: trimmed strings, numbers, booleans, lists; blanks become null. */
export function toAnswers(fields: readonly PublicFormField[], values: FormValues): Record<string, AnswerValue> {
  const out: Record<string, AnswerValue> = {};
  for (const f of fields) {
    const raw = values[f.key];
    if (f.type === "checkbox") out[f.key] = raw === true;
    else if (f.type === "multiselect") out[f.key] = Array.isArray(raw) ? raw : [];
    else {
      const text = typeof raw === "string" ? raw.trim() : "";
      if (!text) out[f.key] = null;
      else if (f.type === "number") out[f.key] = Number(text);
      else if (f.type === "email") out[f.key] = text.toLowerCase();
      else out[f.key] = text.normalize("NFC");
    }
  }
  return out;
}

/** Display text for a stored answer (staff views). */
export function formatAnswer(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not answered";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "Not answered";
  return String(value);
}
