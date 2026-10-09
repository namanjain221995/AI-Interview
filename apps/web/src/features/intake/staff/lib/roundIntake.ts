/**
 * The `intake` part of a round configuration (design 4.1 `IntakeRoundConfig`), for the
 * round editor. The object stays opaque to the generic round model: these helpers read
 * it with defaults and write changes back without dropping keys they don't know.
 */

import { STAGE_LABELS } from "../labels";
import { STAGE_KEYS, type StageKey, type WorkAuthorizationPolicy } from "../types";

export interface IntakeFollowupLimits {
  greeting: number;
  resume_clarify_per_rung: number;
  mandatory: number;
  topic: number;
  learning: number;
}

export interface IntakeRoundView {
  kit_version: string;
  followup_limits: IntakeFollowupLimits;
  claims: { min_claims: number; max_claims: number };
  scenarios: number;
  passage_drill_a: string;
  passage_drill_b: string;
  topic_card: string;
  /** null = organisation default. */
  work_authorization: WorkAuthorizationPolicy | null;
  closing_questions_max: number;
}

export const INTAKE_ROUND_DEFAULTS: IntakeRoundView = {
  kit_version: "v1",
  followup_limits: { greeting: 2, resume_clarify_per_rung: 1, mandatory: 1, topic: 1, learning: 2 },
  claims: { min_claims: 2, max_claims: 3 },
  scenarios: 2,
  passage_drill_a: "auto",
  passage_drill_b: "auto",
  topic_card: "auto",
  work_authorization: null,
  closing_questions_max: 3,
};

export const FOLLOWUP_FIELDS: ReadonlyArray<{ key: keyof IntakeFollowupLimits; label: string; max: number; description: string }> = [
  { key: "greeting", label: "Introduction", max: 3, description: "Follow-ups on the project they're proudest of." },
  { key: "resume_clarify_per_rung", label: "Résumé, per question", max: 2, description: "Clarifications on each résumé question." },
  { key: "mandatory", label: "Standard questions", max: 2, description: "Extra follow-ups beyond the fixed items." },
  { key: "topic", label: "Topic card", max: 2, description: "Beyond the follow-up on the weakest point." },
  { key: "learning", label: "Learning and growth", max: 3, description: "Follow-ups on the three-week plan and teach-back." },
];

export const PASSAGE_A_KEYS = ["auto", "A", "B", "D", "E", "F"] as const;
export const PASSAGE_B_KEYS = ["auto", "A", "B", "C", "E", "F"] as const;
export const TOPIC_CARD_KEYS = ["auto", "1", "2", "3", "4", "5", "6", "7", "8"] as const;

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {});
const int = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const choice = (v: unknown, allowed: readonly string[], fallback: string): string => (typeof v === "string" && allowed.includes(v) ? v : fallback);

export function readIntakeRound(raw: unknown): IntakeRoundView {
  const r = rec(raw);
  const limits = rec(r.followup_limits);
  const claims = rec(r.claims);
  const wa = r.work_authorization;
  const d = INTAKE_ROUND_DEFAULTS;
  return {
    kit_version: typeof r.kit_version === "string" && r.kit_version ? r.kit_version : d.kit_version,
    followup_limits: {
      greeting: int(limits.greeting, d.followup_limits.greeting),
      resume_clarify_per_rung: int(limits.resume_clarify_per_rung, d.followup_limits.resume_clarify_per_rung),
      mandatory: int(limits.mandatory, d.followup_limits.mandatory),
      topic: int(limits.topic, d.followup_limits.topic),
      learning: int(limits.learning, d.followup_limits.learning),
    },
    claims: { min_claims: int(claims.min_claims, d.claims.min_claims), max_claims: int(claims.max_claims, d.claims.max_claims) },
    scenarios: int(r.scenarios, d.scenarios),
    passage_drill_a: choice(r.passage_drill_a, PASSAGE_A_KEYS, "auto"),
    passage_drill_b: choice(r.passage_drill_b, PASSAGE_B_KEYS, "auto"),
    topic_card: choice(typeof r.topic_card === "number" ? String(r.topic_card) : r.topic_card, TOPIC_CARD_KEYS, "auto"),
    work_authorization:
      wa && typeof wa === "object"
        ? { enabled: (wa as Rec).enabled === true, country: typeof (wa as Rec).country === "string" ? ((wa as Rec).country as string) : null, ask_sponsorship: (wa as Rec).ask_sponsorship !== false, variant: "default" }
        : null,
    closing_questions_max: int(r.closing_questions_max, d.closing_questions_max),
  };
}

/** Writes the view back onto the stored object, keeping keys this editor doesn't know. */
export function writeIntakeRound(raw: unknown, view: IntakeRoundView): Rec {
  const base = rec(raw);
  return {
    ...base,
    kit_version: view.kit_version,
    followup_limits: { ...rec(base.followup_limits), ...view.followup_limits },
    claims: { ...rec(base.claims), ...view.claims },
    scenarios: view.scenarios,
    passage_drill_a: view.passage_drill_a,
    passage_drill_b: view.passage_drill_b,
    topic_card: view.topic_card,
    work_authorization: view.work_authorization
      ? { enabled: view.work_authorization.enabled, country: view.work_authorization.enabled ? (view.work_authorization.country?.trim() || null) : null, ask_sponsorship: view.work_authorization.ask_sponsorship, variant: "default" }
      : null,
    closing_questions_max: view.closing_questions_max,
  };
}

export interface IntakeRoundIssue {
  path: string;
  message: string;
  severity: "error" | "warning";
}

export function validateIntakeRound(view: IntakeRoundView): IntakeRoundIssue[] {
  const issues: IntakeRoundIssue[] = [];
  const range = (path: string, label: string, value: number, min: number, max: number) => {
    if (!Number.isInteger(value) || value < min || value > max) issues.push({ path, message: `${label} must be between ${min} and ${max}.`, severity: "error" });
  };
  for (const field of FOLLOWUP_FIELDS) range(`intake.followup_limits.${field.key}`, field.label, view.followup_limits[field.key], 0, field.max);
  range("intake.claims.min_claims", "Minimum claims", view.claims.min_claims, 1, 3);
  range("intake.claims.max_claims", "Maximum claims", view.claims.max_claims, 1, 3);
  if (view.claims.min_claims > view.claims.max_claims) issues.push({ path: "intake.claims.max_claims", message: "The maximum can't be lower than the minimum.", severity: "error" });
  range("intake.scenarios", "Scenario questions", view.scenarios, 0, 3);
  range("intake.closing_questions_max", "Questions answered at the end", view.closing_questions_max, 0, 5);
  if (view.passage_drill_a !== "auto" && view.passage_drill_a === view.passage_drill_b) {
    issues.push({ path: "intake.passage_drill_b", message: "Drill B needs a different passage from Drill A.", severity: "error" });
  }
  if (view.work_authorization?.enabled) {
    const country = view.work_authorization.country?.trim() ?? "";
    if (!country) issues.push({ path: "intake.work_authorization.country", message: "Name the country for the work authorisation question.", severity: "error" });
    else if (country.length > 80) issues.push({ path: "intake.work_authorization.country", message: "Keep it under 80 characters.", severity: "error" });
  }
  return issues;
}

/** Kit stage midpoints in minutes (design 6.3); they total 42. */
const MIDPOINTS: Record<StageKey, number> = { greeting: 5, resume: 9, mandatory: 4, reading: 7.5, topic: 7.5, learning: 5, close: 4 };

/** Planned stage budgets for a duration: the server's formula, for display. */
export function planBudgets(durationMinutes: number): Array<{ key: StageKey; title: string; seconds: number }> {
  const k = (durationMinutes * 60 - 60) / 2520;
  return STAGE_KEYS.map((key) => ({ key, title: STAGE_LABELS[key], seconds: Math.round(MIDPOINTS[key] * 60 * k) }));
}
