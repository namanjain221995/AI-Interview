/**
 * Human decision form (design 8.9, 10.2 #17): validation mirroring the server, the
 * request payload, and the "differs from the AI recommendation" hint. The hint uses the
 * same aligned sets as the server's `overrides_ai` rule; the stored flag comes from the
 * server.
 */

import type { DecisionBand, DecisionOutcome, IntakeDecisionIn, ReviewItem, ReviewResolution } from "../types";

export const RATIONALE_MIN = 10;
export const RATIONALE_MAX = 5000;
export const NOTE_MAX = 1000;
export const REQUESTED_EVIDENCE_MAX = 10;

const ALIGNED: Record<DecisionBand, readonly DecisionOutcome[]> = {
  strong_accept: ["accept", "accept_with_notes"],
  accept_with_notes: ["accept", "accept_with_notes"],
  borderline: ["second_interview", "request_evidence", "accept_with_notes"],
  reject: ["reject"],
  insufficient_evidence: ["request_evidence", "second_interview"],
};

/** True when the outcome falls outside what the AI band suggests (no band: never). */
export function differsFromBand(outcome: DecisionOutcome | null | undefined, band: DecisionBand | null | undefined): boolean {
  if (!outcome || !band) return false;
  return !(ALIGNED[band] ?? []).includes(outcome);
}

export interface DecisionDraft {
  outcome: DecisionOutcome | null;
  rationale: string;
  resolutions: Record<string, { resolution: ReviewResolution | null; note: string }>;
  competencyNotes: Record<string, string>;
  requestedEvidence: string[];
}

export function emptyDecisionDraft(): DecisionDraft {
  return { outcome: null, rationale: "", resolutions: {}, competencyNotes: {}, requestedEvidence: [""] };
}

export type DecisionErrors = Partial<Record<"outcome" | "rationale" | "requested_evidence" | "form", string>> & { notes?: Record<string, string> };

export function validateDecision(draft: DecisionDraft): DecisionErrors {
  const errors: DecisionErrors = {};
  if (!draft.outcome) errors.outcome = "Choose an outcome.";
  const rationale = draft.rationale.trim();
  if (rationale.length < RATIONALE_MIN) errors.rationale = `Explain the decision in at least ${RATIONALE_MIN} characters. It is stored with your name.`;
  else if (rationale.length > RATIONALE_MAX) errors.rationale = `Keep the rationale under ${RATIONALE_MAX.toLocaleString()} characters.`;
  const evidence = draft.requestedEvidence.map((e) => e.trim()).filter(Boolean);
  if (draft.outcome === "request_evidence" && evidence.length === 0) errors.requested_evidence = "List at least one thing you need from the candidate.";
  else if (evidence.length > REQUESTED_EVIDENCE_MAX) errors.requested_evidence = `Request up to ${REQUESTED_EVIDENCE_MAX} items.`;
  const notes: Record<string, string> = {};
  for (const [key, note] of Object.entries(draft.competencyNotes)) if (note.length > NOTE_MAX) notes[`competency:${key}`] = `Keep notes under ${NOTE_MAX} characters.`;
  for (const [id, value] of Object.entries(draft.resolutions)) if (value.note.length > NOTE_MAX) notes[`item:${id}`] = `Keep notes under ${NOTE_MAX} characters.`;
  if (Object.keys(notes).length) errors.notes = notes;
  return errors;
}

export function hasDecisionErrors(errors: DecisionErrors): boolean {
  return Boolean(errors.outcome || errors.rationale || errors.requested_evidence || (errors.notes && Object.keys(errors.notes).length));
}

/** Request body: trimmed text, only resolved items, only non-empty notes. */
export function decisionPayload(draft: DecisionDraft, items: readonly ReviewItem[]): IntakeDecisionIn {
  const known = new Set(items.map((i) => i.id));
  return {
    outcome: draft.outcome as DecisionOutcome,
    rationale: draft.rationale.trim(),
    review_items: Object.entries(draft.resolutions)
      .filter(([id, value]) => known.has(id) && value.resolution)
      .map(([id, value]) => ({ item_id: id, resolution: value.resolution as ReviewResolution, ...(value.note.trim() ? { note: value.note.trim() } : {}) })),
    competency_notes: Object.fromEntries(Object.entries(draft.competencyNotes).map(([k, v]) => [k, v.trim()] as const).filter(([, v]) => v)),
    requested_evidence: draft.outcome === "request_evidence" ? draft.requestedEvidence.map((e) => e.trim()).filter(Boolean) : [],
  };
}

/** Review items still without a resolution in the draft (open ones only). */
export function unresolvedItems(draft: DecisionDraft, items: readonly ReviewItem[]): ReviewItem[] {
  return items.filter((item) => !item.resolution && !draft.resolutions[item.id]?.resolution);
}

/** Maps server validation codes onto form fields. */
export function decisionServerError(code: string | undefined, message: string): DecisionErrors {
  switch (code) {
    case "rationale_required":
      return { rationale: message || `Explain the decision in at least ${RATIONALE_MIN} characters.` };
    case "requested_evidence_required":
      return { requested_evidence: message || "List at least one thing you need from the candidate." };
    case "unknown_review_item":
      return { form: "A review item changed since you opened this page. Reload and try again." };
    case "session_not_finished":
      return { form: "The interview hasn't finished yet. Decisions open when it ends." };
    case "evaluation_running":
      return { form: "A new evaluation is running. Wait for it to finish, then decide on the updated results." };
    default:
      return { form: message || "Couldn't record the decision. Try again." };
  }
}
