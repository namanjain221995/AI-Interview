/**
 * Client mirror of the form fairness guard (OPEN_LINKS.md section 2.3,
 * `app/services/open_links/fairness.py`). It gives the builder instant feedback;
 * the server runs the same rules (plus the catalog lint) and decides.
 *
 * Only custom fields are scanned (key, label, help text, placeholder, options),
 * enabled or not. Text is lower-cased and `_`/`-` become spaces, so the key
 * `c_date_of_birth` reads as "c date of birth".
 */

import type { FormField, FormIssue, FormSchema, FormWarning } from "./types";

interface Rule {
  topic: string;
  pattern: RegExp;
}

const rx = (...parts: string[]) => new RegExp(parts.map((p) => `(?:${p})`).join("|"));

/** Topic -> what the builder says the form is asking for. */
export const TOPIC_LABELS: Record<string, string> = {
  age: "age or date of birth",
  gender: "gender",
  family_status: "marital or family status",
  religion: "religion, caste or ethnicity",
  nationality: "nationality or citizenship",
  appearance: "photos or physical appearance",
  health: "health or disability",
  salary_history: "pay history",
};

export const BLOCKED_RULES: readonly Rule[] = [
  {
    topic: "age",
    pattern: rx(
      String.raw`\bage\b`,
      String.raw`\bdob\b`,
      String.raw`\bdates? of birth\b`,
      String.raw`\bbirth ?dates?\b`,
      String.raw`\bbirthdays?\b`,
      String.raw`\byear of birth\b`,
      String.raw`\bgraduation year\b`,
      String.raw`\bpassing year\b`,
      String.raw`\bbatch year\b`,
    ),
  },
  { topic: "gender", pattern: rx(String.raw`\bgender\b`, String.raw`\bsex\b`, String.raw`\bpronouns?\b`) },
  {
    topic: "family_status",
    pattern: rx(
      String.raw`\bmarital\b`,
      String.raw`\bmarried\b`,
      String.raw`\bspouses?\b`,
      String.raw`\bhusbands?\b`,
      String.raw`\bwi(?:fe|ves)\b`,
      String.raw`\bchildren\b`,
      String.raw`\bkids\b`,
      String.raw`\bdependents?\b`,
      String.raw`\bpregnan`,
      String.raw`\bfather(?:'?s)? name\b`,
      String.raw`\bmother(?:'?s)? name\b`,
      String.raw`\bparents?(?:'?s)? name\b`,
      String.raw`\bguardian`,
    ),
  },
  {
    topic: "religion",
    pattern: rx(
      String.raw`\breligio`,
      String.raw`\bfaith\b`,
      String.raw`\bcaste\b`,
      String.raw`\bsub caste\b`,
      String.raw`\bcommunity\b`,
      String.raw`\btribes?\b`,
      String.raw`\brace\b`,
      String.raw`\bethnic`,
    ),
  },
  {
    topic: "nationality",
    pattern: rx(
      String.raw`\bnationality\b`,
      String.raw`\bcitizenship\b`,
      String.raw`\bcitizens?\b`,
      String.raw`\bpassports?\b`,
      String.raw`\bcountry of birth\b`,
      String.raw`\bplace of birth\b`,
      String.raw`\bvisa (?:status|type)\b`,
    ),
  },
  {
    topic: "appearance",
    pattern: rx(
      String.raw`\bphotos?\b`,
      String.raw`\bphotograph`,
      String.raw`\bpictures?\b`,
      String.raw`\bselfies?\b`,
      String.raw`\bheadshots?\b`,
      String.raw`\bheight\b`,
      String.raw`\bweight\b`,
      String.raw`\bcomplexion\b`,
    ),
  },
  {
    topic: "health",
    pattern: rx(String.raw`\bhealth`, String.raw`\bmedical\b`, String.raw`\bdisab`, String.raw`\bhandicap`, String.raw`\bblood group\b`, String.raw`\billness`),
  },
  {
    topic: "salary_history",
    pattern: rx(
      String.raw`\bcurrent (?:salary|ctc|pay|compensation)\b`,
      String.raw`\blast drawn\b`,
      String.raw`\bprevious (?:salary|ctc)\b`,
      String.raw`\bsalary history\b`,
    ),
  },
];

/** Honorific options reveal gender ("Mr", "Mrs", "Ms", "Miss"). */
const HONORIFIC = /^(?:mr|mrs|ms|miss)\.?$/;

export const WARNING_RULES: ReadonlyArray<{ code: string; pattern: RegExp; message: string }> = [
  { code: "expected_pay", pattern: rx(String.raw`\bexpected (?:salary|ctc|pay)\b`), message: "Ask only for expectations, never pay history." },
  {
    code: "right_to_work",
    pattern: rx(String.raw`\bwork authori[sz]ation\b`, String.raw`\bright to work\b`, String.raw`\blegally (?:authori[sz]ed|allowed)\b`),
    message: "Ask only a yes/no about the job's location; never ask for nationality or visa type.",
  },
  {
    code: "address",
    pattern: rx(String.raw`\baddress`, String.raw`\bpin ?code\b`, String.raw`\bpostal\b`, String.raw`\bzip\b`),
    message: "A city is enough; a full address is personal data you probably don't need.",
  },
  { code: "employment_gap", pattern: rx(String.raw`\bgaps?\b`, String.raw`\bcareer break`), message: "Gaps can reveal health, caregiving or family status." },
  {
    code: "languages",
    pattern: rx(String.raw`\bmother tongue\b`, String.raw`\bnative language\b`),
    message: "Can reveal national or ethnic origin; ask about job-required languages instead.",
  },
];

export function normalizeText(text: string | null | undefined): string {
  return (text ?? "").toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
}

function texts(f: FormField): string[] {
  return [f.key, f.label, f.help_text ?? "", f.placeholder ?? "", ...f.options].map(normalizeText).filter(Boolean);
}

export function warningKey(fieldKey: string, code: string): string {
  return `${fieldKey}:${code}`;
}

export function blockedMessage(topic: string): string {
  return `This asks for ${TOPIC_LABELS[topic] ?? topic}, which we never collect. Remove the field or reword it.`;
}

export interface FairnessResult {
  errors: FormIssue[];
  warnings: FormWarning[];
}

/** Errors block saving; warnings need an "I understand" per `"<key>:<code>"`. */
export function checkForm(schema: FormSchema, acknowledged: readonly string[] = []): FairnessResult {
  const errors: FormIssue[] = [];
  const warnings: FormWarning[] = [];
  const acked = new Set(acknowledged);
  for (const f of schema.fields) {
    if (f.builtin) continue;
    const parts = texts(f);
    const blocked = BLOCKED_RULES.find((rule) => parts.some((t) => rule.pattern.test(t)));
    const honorific = !blocked && f.options.some((o) => HONORIFIC.test(normalizeText(o)));
    const topic = blocked?.topic ?? (honorific ? "gender" : null);
    if (topic) {
      errors.push({ key: f.key, label: f.label, topic, message: blockedMessage(topic) });
      continue;
    }
    for (const rule of WARNING_RULES) {
      if (parts.some((t) => rule.pattern.test(t))) {
        warnings.push({ key: f.key, code: rule.code, message: rule.message, acknowledged: acked.has(warningKey(f.key, rule.code)) });
      }
    }
  }
  return { errors, warnings };
}
