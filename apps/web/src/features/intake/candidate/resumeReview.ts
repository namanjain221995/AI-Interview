/**
 * Résumé review helpers (INTAKE_INTERVIEW.md §15.4): list parsing for the item
 * forms, per-item validation that mirrors the server limits, and a change count
 * for the confirm button. Pure.
 */
import { formatResumeDate } from "@/features/candidate/api";

import { draftRef } from "./api";
import { REVIEW_LIMITS, type ResumeReviewDoc, type ReviewEducation, type ReviewProject, type ReviewRole, type ReviewSectionKey } from "./types";

export const SECTION_LABELS: Record<ReviewSectionKey, string> = {
  roles: "Work experience",
  projects: "Projects",
  skills: "Skills",
  languages: "Programming languages",
  tools: "Tools",
  education: "Education",
  certifications: "Certifications",
};

/** Which server profile section each review section follows ("Checking…" marks). */
export const SECTION_SOURCE: Record<ReviewSectionKey, string> = {
  roles: "experience",
  projects: "experience",
  skills: "skills",
  languages: "skills",
  tools: "skills",
  education: "education",
  certifications: "education",
};

export const SECTION_ORDER: readonly ReviewSectionKey[] = ["roles", "projects", "skills", "languages", "tools", "education", "certifications"];
export type ChipSection = "skills" | "languages" | "tools" | "certifications";
export const CHIP_SECTIONS: readonly ChipSection[] = ["skills", "languages", "tools", "certifications"];

/** Server limits (ResumeReviewDoc): items per list and characters per item. */
export const CHIP_LIMITS: Record<ChipSection, { items: number; length: number }> = {
  skills: { items: REVIEW_LIMITS.skills, length: 80 },
  languages: { items: REVIEW_LIMITS.languages, length: 60 },
  tools: { items: REVIEW_LIMITS.tools, length: 80 },
  certifications: { items: REVIEW_LIMITS.certifications, length: 200 },
};
/** Characters per line of responsibilities, achievements and outcomes; per technology; per date. */
export const LINE_MAX = 400;
export const TECH_MAX = 80;
export const DATE_MAX = 20;

/** "One per line" textarea -> list (blank lines dropped, capped). */
export function splitLines(text: string, limit: number): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*[-•*]\s*/, "").trim())
    .filter(Boolean)
    .slice(0, limit);
}

/** "Separated by commas" input -> list (duplicates dropped, capped). */
export function splitCommas(text: string, limit: number): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.split(/[,;\n]/)) {
    const value = part.trim();
    if (!value || seen.has(value.toLowerCase())) continue;
    seen.add(value.toLowerCase());
    out.push(value);
  }
  return out.slice(0, limit);
}

export function emptyRole(): ReviewRole {
  return { ref: draftRef(), title: "", company: "", start: null, end: null, responsibilities: [], technologies: [], achievements: [] };
}

export function emptyProject(): ReviewProject {
  return { ref: draftRef(), name: "", role: null, description: "", technologies: [], outcomes: [] };
}

export function emptyEducation(): ReviewEducation {
  return { ref: draftRef(), institution: "", degree: null, field: null };
}

export type ItemErrors = Partial<Record<string, string>>;

const tooLong = (items: string[], max: number) => items.some((item) => item.length > max);

export function validateRole(role: ReviewRole): ItemErrors {
  const errors: ItemErrors = {};
  if (!role.title.trim()) errors.title = "Add the job title.";
  if (role.title.length > 160) errors.title = "Keep the title under 160 characters.";
  if (role.company.length > 160) errors.company = "Keep the company name under 160 characters.";
  if ((role.start ?? "").length > DATE_MAX) errors.start = `Keep it under ${DATE_MAX} characters, for example 2021-03.`;
  if ((role.end ?? "").length > DATE_MAX) errors.end = `Keep it under ${DATE_MAX} characters, for example 2023-11.`;
  if (tooLong(role.technologies, TECH_MAX)) errors.technologies = `Keep each technology under ${TECH_MAX} characters.`;
  if (tooLong(role.responsibilities, LINE_MAX)) errors.responsibilities = `Keep each line under ${LINE_MAX} characters.`;
  if (tooLong(role.achievements, LINE_MAX)) errors.achievements = `Keep each line under ${LINE_MAX} characters.`;
  return errors;
}

export function validateProject(project: ReviewProject): ItemErrors {
  const errors: ItemErrors = {};
  if (!project.name.trim()) errors.name = "Add the project's name.";
  if (project.name.length > 160) errors.name = "Keep the name under 160 characters.";
  if (project.description.length > 600) errors.description = "Keep the description under 600 characters.";
  if (tooLong(project.technologies, TECH_MAX)) errors.technologies = `Keep each technology under ${TECH_MAX} characters.`;
  if (tooLong(project.outcomes, LINE_MAX)) errors.outcomes = `Keep each line under ${LINE_MAX} characters.`;
  return errors;
}

export function validateEducation(item: ReviewEducation): ItemErrors {
  const errors: ItemErrors = {};
  if (!item.institution.trim()) errors.institution = "Add the school or institution.";
  if (item.institution.length > 200) errors.institution = "Keep it under 200 characters.";
  return errors;
}

/** "Jan 2021 – Present" (or null when neither end is known). */
export function rolePeriod(role: Pick<ReviewRole, "start" | "end">): string | null {
  const start = formatResumeDate(role.start);
  const end = formatResumeDate(role.end);
  if (!start && !end) return null;
  return `${start ?? "?"} – ${end ?? "Present"}`;
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** How many items the candidate changed, added or removed (for the confirm summary). */
export function countChanges(original: ResumeReviewDoc | null, draft: ResumeReviewDoc | null): number {
  if (!original || !draft) return 0;
  let changes = 0;
  for (const key of ["roles", "projects", "education"] as const) {
    const before = new Map<string, unknown>((original[key] as Array<{ ref: string }>).map((item) => [item.ref, item]));
    const after = draft[key] as Array<{ ref: string }>;
    for (const item of after) {
      const prev = before.get(item.ref);
      if (prev === undefined || !sameJson(prev, item)) changes += 1;
      before.delete(item.ref);
    }
    changes += before.size;
  }
  for (const key of CHIP_SECTIONS) {
    const before = new Set(original[key].map((v) => v.toLowerCase()));
    const after = new Set(draft[key].map((v) => v.toLowerCase()));
    for (const value of after) if (!before.has(value)) changes += 1;
    for (const value of before) if (!after.has(value)) changes += 1;
  }
  if ((original.headline ?? "") !== (draft.headline ?? "")) changes += 1;
  if (original.experience_years !== draft.experience_years) changes += 1;
  return changes;
}

/** Test-id ref of a chip (strings carry no ref on the wire). */
export function chipRef(section: ChipSection, index: number): string {
  return `${section}-${index}`;
}
