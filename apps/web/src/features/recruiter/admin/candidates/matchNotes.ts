/**
 * Job-match notes arrive as one string: a formula ("Score 92.2 = 0.50xcoverage 89.9 +
 * 0.35xmodel 92 + 0.15xexperience 100. Requirements 15: strong 11, partial 3, weak 1,
 * none 0.") followed by the model's narrative. Split them so the narrative reads as
 * prose and the arithmetic can be shown on request, in plain words.
 */

export interface MatchTerm {
  key: string;
  label: string;
  /** 0-1 */
  weight: number;
  value: number;
}

export interface MatchNotes {
  narrative: string;
  score: number | null;
  terms: MatchTerm[];
  requirements: { total: number; strong: number; partial: number; weak: number; none: number } | null;
}

const TERM_LABELS: Record<string, string> = {
  coverage: "Requirements covered by the résumé",
  model: "AI assessment of fit",
  experience: "Relevant experience",
};

/** Build the same view model from the structured ``match_breakdown`` (preferred). */
export function notesFromBreakdown(
  breakdown: {
    score: number | null;
    coverage: number | null;
    model_fit: number | null;
    experience_fit: number | null;
    weights: Record<string, number>;
    bands: Record<string, number>;
  },
  narrative: string | null | undefined,
): MatchNotes {
  const values: Record<string, number | null> = {
    coverage: breakdown.coverage,
    model: breakdown.model_fit,
    experience: breakdown.experience_fit,
  };
  const terms: MatchTerm[] = Object.entries(values)
    .filter(([key, value]) => value !== null && value !== undefined && (breakdown.weights[key] ?? 0) > 0)
    .map(([key, value]) => ({ key, label: TERM_LABELS[key] ?? key, weight: breakdown.weights[key] ?? 0, value: value as number }));
  const b = breakdown.bands ?? {};
  const total = (b.strong ?? 0) + (b.partial ?? 0) + (b.weak ?? 0) + (b.none ?? 0);
  return {
    narrative: (narrative ?? "").trim(),
    score: breakdown.score,
    terms,
    requirements: total ? { total, strong: b.strong ?? 0, partial: b.partial ?? 0, weak: b.weak ?? 0, none: b.none ?? 0 } : null,
  };
}

export function parseMatchNotes(notes: string | null | undefined): MatchNotes {
  const text = (notes ?? "").trim();
  const empty: MatchNotes = { narrative: text, score: null, terms: [], requirements: null };
  const formula = /^Score\s+(\d+(?:\.\d+)?)\s*=\s*([^]*?)\.(?:\s|$)/.exec(text);
  if (!formula) return empty;
  const terms: MatchTerm[] = [];
  for (const part of formula[2]!.split("+")) {
    const m = /^\s*(\d*\.?\d+)\s*[x×*]\s*([a-z_]+)\s+(\d+(?:\.\d+)?)\s*$/i.exec(part);
    if (!m) return empty;
    const key = m[2]!.toLowerCase();
    terms.push({ key, label: TERM_LABELS[key] ?? key.replace(/_/g, " "), weight: Number(m[1]), value: Number(m[3]) });
  }
  let rest = text.slice(formula[0].length).trim();
  let requirements: MatchNotes["requirements"] = null;
  const req = /^Requirements\s+(\d+):\s*strong\s+(\d+),\s*partial\s+(\d+),\s*weak\s+(\d+),\s*none\s+(\d+)\.?\s*/i.exec(rest);
  if (req) {
    requirements = { total: Number(req[1]), strong: Number(req[2]), partial: Number(req[3]), weak: Number(req[4]), none: Number(req[5]) };
    rest = rest.slice(req[0].length).trim();
  }
  return { narrative: rest, score: Number(formula[1]), terms, requirements };
}
