/**
 * The parsed candidate profile: experience timeline, skills by category,
 * projects, achievements, education, claims to verify (with verification
 * status from the latest interview), inconsistencies and the job match.
 * Everything here is what the resume says; claims are not assumed true.
 */

import { AlertTriangle, Award, BookOpen, Briefcase, CheckCircle2, CircleDashed, CircleHelp, FolderGit2, GraduationCap, Quote, Target, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ScoreRing } from "@/components/domain/ScoreRing";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { CLAIM_STATUS_LABELS, labelFor } from "@/lib/labels";
import type {
  CandidateProfileDocument,
  CandidateProfileRecord,
  ClaimStatus,
  ClaimToVerify,
  JobMatch,
  MatchBreakdown,
  RequirementMatch,
  RoleEntry,
  SkillItem,
} from "@/lib/types";
import { cn } from "@/lib/utils";

import { notesFromBreakdown, parseMatchNotes } from "./matchNotes";

// ---------------------------------------------------------------- helpers

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2021-03" / "2021-03-01" / "2021" / "present" -> "Mar 2021" */
export function formatResumeDate(value: string | null | undefined): string {
  if (!value) return "";
  const v = value.trim();
  if (/^(present|current|now)$/i.test(v)) return "Present";
  const m = /^(\d{4})(?:-(\d{1,2}))?/.exec(v);
  if (!m) return v;
  const month = m[2] ? Number(m[2]) : null;
  return month && month >= 1 && month <= 12 ? `${MONTHS[month - 1]} ${m[1]}` : m[1]!;
}

function sortKey(value: string | null | undefined): number {
  if (!value || /^(present|current|now)$/i.test(value.trim())) return Number.POSITIVE_INFINITY;
  const m = /^(\d{4})(?:-(\d{1,2}))?/.exec(value.trim());
  return m ? Number(m[1]) * 12 + (m[2] ? Number(m[2]) - 1 : 0) : 0;
}

export function sortRoles(roles: readonly RoleEntry[]): RoleEntry[] {
  return [...roles].sort((a, b) => sortKey(b.end ?? "present") - sortKey(a.end ?? "present") || sortKey(b.start) - sortKey(a.start));
}

export function formatMonths(months: number | null | undefined): string | null {
  if (!months || months <= 0) return null;
  const years = Math.floor(months / 12);
  const rest = Math.round(months % 12);
  return [years ? `${years} yr${years === 1 ? "" : "s"}` : null, rest ? `${rest} mo` : null].filter(Boolean).join(" ");
}

const LEVELS = ["beginner", "intermediate", "advanced", "expert"] as const;

export function levelIndex(level: string | null | undefined): number {
  const i = LEVELS.indexOf((level ?? "").toLowerCase() as (typeof LEVELS)[number]);
  return i === -1 ? 0 : i + 1;
}

const CATEGORY_LABELS: Record<string, string> = {
  programming_language: "Languages",
  framework: "Frameworks",
  database: "Databases",
  cloud: "Cloud",
  devops: "DevOps and infrastructure",
  ai_ml: "AI and ML",
  tool: "Tools",
  architecture: "Architecture",
  soft: "Working with others",
  other: "Other",
};

export function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

const CLAIM_TONE: Record<ClaimStatus, BadgeTone> = {
  unverified: "neutral",
  in_progress: "accent",
  verified: "success",
  partially_verified: "warning",
  contradicted: "danger",
  not_applicable: "outline",
};

const CLAIM_ICON: Record<ClaimStatus, ReactNode> = {
  unverified: <CircleDashed />,
  in_progress: <CircleHelp />,
  verified: <CheckCircle2 />,
  partially_verified: <CircleHelp />,
  contradicted: <XCircle />,
  not_applicable: <CircleDashed />,
};

const STRENGTH: Record<RequirementMatch["strength"], { label: string; width: string; tone: string }> = {
  strong: { label: "Strong evidence", width: "w-full", tone: "bg-success" },
  partial: { label: "Partial evidence", width: "w-2/3", tone: "bg-accent" },
  weak: { label: "Weak evidence", width: "w-1/3", tone: "bg-warning" },
  none: { label: "No evidence", width: "w-0", tone: "bg-line-strong" },
};

function Section({ icon, title, count, children, action }: { icon: ReactNode; title: string; count?: number; children: ReactNode; action?: ReactNode }) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <span className="text-fg-subtle [&_svg]:size-4" aria-hidden="true">
              {icon}
            </span>
            {title}
            {count !== undefined ? <span className="text-xs font-normal text-fg-subtle tabular-nums">{count}</span> : null}
          </span>
        }
        action={action}
      />
      <CardContent>{children}</CardContent>
    </Card>
  );
}

// -------------------------------------------------------------- job match

export function JobMatchPanel({
  match,
  score,
  breakdown,
}: {
  match: JobMatch | null;
  score: number | null;
  breakdown?: MatchBreakdown | null;
}) {
  const value = score ?? match?.overall_fit ?? null;
  if (!match && value === null) return null;
  return (
    <Section icon={<Target />} title="Job match">
      <div className="grid grid-cols-1 gap-6 md:grid-cols-[auto_1fr]">
        <div className="flex flex-col items-center gap-2">
          <ScoreRing value={value} max={100} size="lg" tone="accent" label="Resume fit" ariaLabel="Resume match with the job" />
          {match?.relevant_experience_years ? <p className="text-xs text-fg-muted tabular-nums">{match.relevant_experience_years} yrs relevant</p> : null}
        </div>
        <div className="grid grid-cols-1 min-w-0 gap-5">
          {match?.matched_requirements?.length ? (
            <div className="grid grid-cols-1 gap-2">
              <h4 className="text-[13px] font-medium text-fg">Requirements with evidence</h4>
              <ul className="grid grid-cols-1 gap-2.5">
                {match.matched_requirements.map((r, i) => (
                  <li key={`${r.requirement}-${i}`} className="grid grid-cols-1 gap-1">
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate font-medium text-fg">{r.requirement}</span>
                      <span className="shrink-0 text-xs text-fg-muted">{STRENGTH[r.strength]?.label ?? r.strength}</span>
                    </div>
                    <div className="h-1 w-full rounded-full bg-track" aria-hidden="true">
                      <div className={cn("h-full rounded-full", STRENGTH[r.strength]?.width, STRENGTH[r.strength]?.tone)} />
                    </div>
                    {r.evidence ? <p className="text-xs text-fg-muted">{r.evidence}</p> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {match?.missing_requirements?.length ? (
            <div className="grid grid-cols-1 gap-1.5">
              <h4 className="text-[13px] font-medium text-fg">Not found on the resume</h4>
              <ul className="flex flex-wrap gap-1.5">
                {match.missing_requirements.map((m) => (
                  <li key={m}>
                    <Badge tone="outline" size="sm">
                      {m}
                    </Badge>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-fg-muted">Missing from the resume doesn't mean missing in practice. Worth asking about.</p>
            </div>
          ) : null}
          {match?.strengths?.length || match?.gaps?.length ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <BulletList title="Strengths for this role" items={match?.strengths ?? []} />
              <BulletList title="Gaps to explore" items={match?.gaps ?? []} />
            </div>
          ) : null}
          {match?.notes || breakdown ? <MatchNotesBlock notes={match?.notes ?? ""} breakdown={breakdown} /> : null}
        </div>
      </div>
    </Section>
  );
}

/** The model's narrative as prose; the score arithmetic in plain words, on request. */
function MatchNotesBlock({ notes, breakdown }: { notes: string; breakdown?: MatchBreakdown | null }) {
  const parsed = breakdown ? notesFromBreakdown(breakdown, notes) : parseMatchNotes(notes);
  if (!parsed.terms.length) return <p className="max-w-[72ch] text-sm leading-6 text-fg-muted">{notes}</p>;
  return (
    <div className="grid grid-cols-1 gap-3">
      {parsed.narrative ? <p className="max-w-[72ch] text-sm leading-6 text-fg-muted">{parsed.narrative}</p> : null}
      <details className="group rounded-md border border-line-subtle bg-surface-subtle px-3 py-2 text-[13px]">
        <summary className="cursor-pointer font-medium text-fg-muted hover:text-fg">How the {Math.round(parsed.score ?? 0)} is calculated</summary>
        <ul className="mt-2 grid grid-cols-1 gap-1 text-fg-muted">
          {parsed.terms.map((term) => (
            <li key={term.key} className="flex items-baseline justify-between gap-3">
              <span>
                {term.label} <span className="text-fg-subtle tabular-nums">({Math.round(term.weight * 100)}%)</span>
              </span>
              <span className="font-medium text-fg tabular-nums">{Math.round(term.value)}</span>
            </li>
          ))}
        </ul>
        {parsed.requirements ? (
          <p className="mt-2 border-t border-line-subtle pt-2 text-fg-subtle">
            {parsed.requirements.total} requirements: {parsed.requirements.strong} strong, {parsed.requirements.partial} partial, {parsed.requirements.weak} weak,{" "}
            {parsed.requirements.none} not found.
          </p>
        ) : null}
      </details>
    </div>
  );
}

function BulletList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="grid grid-cols-1 content-start gap-1.5">
      <h4 className="text-[13px] font-medium text-fg">{title}</h4>
      <ul className="grid grid-cols-1 content-start gap-1 text-sm text-fg-muted">
        {items.map((item) => (
          <li key={item} className="flex gap-2">
            <span className="mt-2 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

// -------------------------------------------------------------- timeline

export function ExperienceTimeline({ roles }: { roles: RoleEntry[] }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const sorted = sortRoles(roles);
  if (sorted.length === 0) return <p className="text-sm text-fg-muted">No roles found on the resume.</p>;
  return (
    <ol className="relative grid grid-cols-1 gap-0" aria-label="Experience">
      {sorted.map((role, index) => {
        const open = expanded[index] ?? index === 0;
        const range = [formatResumeDate(role.start), formatResumeDate(role.end) || (role.start ? "Present" : "")].filter(Boolean).join(" to ");
        const duration = formatMonths(role.duration_months);
        return (
          <li key={`${role.company}-${role.title}-${index}`} className="relative grid grid-cols-[1.25rem_1fr] gap-x-4 pb-6 last:pb-0">
            <span className="relative flex justify-center" aria-hidden="true">
              <span className={cn("z-10 mt-1.5 size-2.5 rounded-full border-2", index === 0 ? "border-primary bg-primary" : "border-line-emphasis bg-surface")} />
              {index < sorted.length - 1 ? <span className="absolute top-4 bottom-[-0.375rem] w-px bg-line" /> : null}
            </span>
            <div className="grid grid-cols-1 min-w-0 gap-2">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
                <h4 className="text-sm font-semibold text-fg">
                  {role.title}
                  <span className="font-normal text-fg-muted"> at </span>
                  {role.company}
                </h4>
                <p className="text-xs text-fg-muted tabular-nums">
                  {range}
                  {duration ? <span className="text-fg-subtle"> ({duration})</span> : null}
                </p>
              </div>
              {role.location || role.leadership_scope ? (
                <p className="text-xs text-fg-muted">{[role.location, role.leadership_scope].filter(Boolean).join(". ")}</p>
              ) : null}
              {open ? (
                <>
                  {role.responsibilities.length ? (
                    <ul className="grid grid-cols-1 gap-1 text-sm text-fg-muted">
                      {role.responsibilities.map((r, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="mt-2 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden="true" />
                          {r}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {role.achievements.length ? (
                    <ul className="grid grid-cols-1 gap-1.5">
                      {role.achievements.map((a, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-fg">
                          <Award className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
                          <span>
                            {a.text}
                            {a.measurable && (a.metric || a.value) ? (
                              <Badge tone="neutral" size="sm" className="ml-2 align-middle">
                                {[a.metric, a.value].filter(Boolean).join(": ")}
                              </Badge>
                            ) : null}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </>
              ) : null}
              {role.technologies.length ? (
                <ul className="flex flex-wrap gap-1">
                  {role.technologies.map((t) => (
                    <li key={t} className="rounded-xs bg-surface-sunken/70 px-1.5 py-0.5 text-2xs font-medium text-fg-muted">
                      {t}
                    </li>
                  ))}
                </ul>
              ) : null}
              {role.responsibilities.length || role.achievements.length ? (
                <button type="button" onClick={() => setExpanded((e) => ({ ...e, [index]: !open }))} className="w-fit text-xs font-medium text-fg-muted hover:text-fg">
                  {open ? "Show less" : `Show details (${role.responsibilities.length + role.achievements.length})`}
                </button>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------- skills

function LevelDots({ level }: { level: string }) {
  const filled = levelIndex(level);
  if (!filled) return null;
  return (
    <span className="flex items-center gap-0.5" role="img" aria-label={`Claimed level: ${level}`}>
      {LEVELS.map((_, i) => (
        <span key={i} className={cn("size-1 rounded-full", i < filled ? "bg-fg-muted" : "bg-line-strong")} />
      ))}
    </span>
  );
}

export function SkillsByCategory({ skills }: { skills: Record<string, SkillItem[]> }) {
  const groups = Object.entries(skills ?? {}).filter(([, items]) => items?.length);
  if (groups.length === 0) return <p className="text-sm text-fg-muted">No skills found on the resume.</p>;
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-5 md:grid-cols-2">
      {groups.map(([category, items]) => (
        <div key={category} className="grid grid-cols-1 content-start gap-2">
          <h4 className="text-[13px] font-medium text-fg">{categoryLabel(category)}</h4>
          <ul className="grid grid-cols-1 gap-1">
            {items.map((skill) => (
              <li key={skill.name} className="flex items-center justify-between gap-3 rounded-sm px-1 py-0.5 text-sm hover:bg-surface-hover" title={skill.evidence ?? undefined}>
                <span className="min-w-0 truncate text-fg">{skill.name}</span>
                <span className="flex shrink-0 items-center gap-2 text-xs text-fg-muted tabular-nums">
                  {skill.years ? `${skill.years} yr${skill.years === 1 ? "" : "s"}` : null}
                  <LevelDots level={skill.claimed_level} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- claims

export function ClaimsList({ claims, statuses, sessionLabel }: { claims: ClaimToVerify[]; statuses: Record<string, ClaimStatus>; sessionLabel?: string | null }) {
  if (claims.length === 0) return <p className="text-sm text-fg-muted">No claims flagged for verification.</p>;
  const sorted = [...claims].sort((a, b) => b.priority - a.priority);
  return (
    <div className="grid grid-cols-1 gap-3">
      {sessionLabel ? <p className="text-xs text-fg-muted">Verification status from {sessionLabel}.</p> : <p className="text-xs text-fg-muted">The interviewer verifies these during the interview. Status appears here afterwards.</p>}
      <ul className="grid grid-cols-1 divide-y divide-line-subtle">
        {sorted.map((claim) => {
          const status = statuses[claim.id] ?? "unverified";
          return (
            <li key={claim.id} className="grid grid-cols-1 gap-1.5 py-3 first:pt-0 last:pb-0">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-medium text-fg">{claim.claim}</p>
                <Badge tone={CLAIM_TONE[status]} size="sm" icon={CLAIM_ICON[status]} className="shrink-0">
                  {labelFor(CLAIM_STATUS_LABELS, status)}
                </Badge>
              </div>
              {claim.source_quote ? (
                <p className="flex gap-1.5 text-xs text-fg-muted italic">
                  <Quote className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
                  {claim.source_quote}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
                <span className="mr-1 tabular-nums" title="Priority, 5 is most important">
                  Priority {claim.priority}
                </span>
                {claim.evidence_topics.map((t) => (
                  <span key={t} className="rounded-xs bg-surface-sunken/70 px-1.5 py-0.5 text-2xs font-medium">
                    {t}
                  </span>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------- full view

export interface ProfileViewProps {
  record: CandidateProfileRecord;
  claimStatuses: Record<string, ClaimStatus>;
  claimSource?: string | null;
}

/** Defensive copy: older or partial profile documents may miss list fields. */
export function normalizeProfile(raw: Partial<CandidateProfileDocument> | null | undefined): CandidateProfileDocument {
  const doc = (raw ?? {}) as Partial<CandidateProfileDocument>;
  const list = <T,>(value: T[] | undefined | null): T[] => (Array.isArray(value) ? value : []);
  return {
    candidate_id: doc.candidate_id ?? "",
    candidate_name: doc.candidate_name ?? null,
    headline: doc.headline ?? null,
    summary: doc.summary ?? null,
    experience_years: doc.experience_years ?? null,
    relevant_experience_years: doc.relevant_experience_years ?? null,
    roles: list(doc.roles).map((r) => ({ ...r, responsibilities: list(r.responsibilities), achievements: list(r.achievements), technologies: list(r.technologies) })),
    companies: list(doc.companies),
    skills: doc.skills && typeof doc.skills === "object" ? doc.skills : {},
    projects: list(doc.projects).map((pr) => ({ ...pr, technologies: list(pr.technologies), outcomes: list(pr.outcomes), scale_indicators: list(pr.scale_indicators) })),
    achievements: list(doc.achievements),
    education: list(doc.education),
    certifications: list(doc.certifications),
    leadership_experience: list(doc.leadership_experience),
    career_progression: doc.career_progression ?? null,
    inconsistencies: list(doc.inconsistencies),
    claims_to_verify: list(doc.claims_to_verify).map((c) => ({ ...c, competency_keys: list(c.competency_keys), evidence_topics: list(c.evidence_topics) })),
    job_match: doc.job_match ? { ...doc.job_match, matched_requirements: list(doc.job_match.matched_requirements), missing_requirements: list(doc.job_match.missing_requirements), strengths: list(doc.job_match.strengths), gaps: list(doc.job_match.gaps) } : null,
    recommended_topics: list(doc.recommended_topics),
    potential_skill_gaps: list(doc.potential_skill_gaps),
    areas_requiring_verification: list(doc.areas_requiring_verification),
    job_relevant_strengths: list(doc.job_relevant_strengths),
    job_relevant_missing_areas: list(doc.job_relevant_missing_areas),
  };
}

export function ProfileView({ record, claimStatuses, claimSource }: ProfileViewProps) {
  const p: CandidateProfileDocument = normalizeProfile(record.profile);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const projects = showAllProjects ? p.projects : p.projects.slice(0, 4);

  return (
    <div className="grid grid-cols-1 gap-6">
      {record.status === "quick" ? (
        <div data-testid="profile-quick-banner" role="status" className="flex items-start gap-3 rounded-md border border-line bg-surface-subtle px-3.5 py-3 text-sm">
          <CircleDashed className="mt-0.5 size-4 shrink-0 animate-spin text-fg-muted motion-reduce:animate-none" aria-hidden="true" />
          <div className="grid grid-cols-1 gap-0.5">
            <p className="font-medium text-fg">First read of the resume</p>
            <p className="text-fg-muted">
              Read straight from the file, without AI. The AI analysis (roles, claims to verify, job match and interview topics) is still running and replaces this view in a few seconds.
            </p>
          </div>
        </div>
      ) : null}
      {p.summary || p.headline ? (
        <Card>
          <CardContent className="grid grid-cols-1 gap-2 py-5">
            {p.headline ? <p className="text-[15px] font-semibold text-fg">{p.headline}</p> : null}
            {p.summary ? <p className="max-w-[70ch] text-sm leading-6 text-fg-muted">{p.summary}</p> : null}
            {p.career_progression ? <p className="max-w-[70ch] text-sm leading-6 text-fg-muted">{p.career_progression}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      <JobMatchPanel match={p.job_match} score={record.job_match_score} breakdown={record.match_breakdown} />

      <Section icon={<Briefcase />} title="Experience" count={p.roles.length}>
        <ExperienceTimeline roles={p.roles} />
      </Section>

      <Section icon={<BookOpen />} title="Skills" count={Object.values(p.skills ?? {}).reduce((n, items) => n + (items?.length ?? 0), 0)}>
        <SkillsByCategory skills={p.skills} />
        <p className="mt-4 text-xs text-fg-muted">Levels are as claimed on the resume (beginner to expert), not assessed.</p>
      </Section>

      <Section icon={<CircleHelp />} title="Claims to verify" count={p.claims_to_verify.length}>
        <ClaimsList claims={p.claims_to_verify} statuses={claimStatuses} sessionLabel={claimSource} />
      </Section>

      {p.inconsistencies.length ? (
        <Section icon={<AlertTriangle />} title="Inconsistencies" count={p.inconsistencies.length}>
          <ul className="grid grid-cols-1 gap-2">
            {p.inconsistencies.map((item, i) => (
              <li key={i} className="flex items-start justify-between gap-3 text-sm">
                <span className="text-fg">{item.description}</span>
                <Badge tone={item.severity === "high" ? "danger" : item.severity === "medium" ? "warning" : "neutral"} size="sm" className="shrink-0">
                  {item.severity === "high" ? "High" : item.severity === "medium" ? "Medium" : "Low"}
                </Badge>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-fg-muted">Dates and details that don't line up. Ask about them; there is often a simple explanation.</p>
        </Section>
      ) : null}

      {p.projects.length ? (
        <Section
          icon={<FolderGit2 />}
          title="Projects"
          count={p.projects.length}
          action={
            p.projects.length > 4 ? (
              <Button size="xs" variant="ghost" onClick={() => setShowAllProjects((v) => !v)}>
                {showAllProjects ? "Show fewer" : `Show all ${p.projects.length}`}
              </Button>
            ) : undefined
          }
        >
          <ul className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {projects.map((project, i) => (
              <li key={`${project.name}-${i}`} className="grid grid-cols-1 content-start gap-1.5 rounded-md border border-line-subtle p-3.5">
                <p className="text-sm font-semibold text-fg">
                  {project.name}
                  {project.role ? <span className="font-normal text-fg-muted">, {project.role}</span> : null}
                </p>
                <p className="text-sm text-fg-muted">{project.description}</p>
                {project.outcomes.length ? (
                  <ul className="grid grid-cols-1 gap-0.5 text-xs text-fg">
                    {project.outcomes.map((o) => (
                      <li key={o} className="flex gap-1.5">
                        <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-success" aria-hidden="true" />
                        {o}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {project.technologies.length ? (
                  <p className="text-2xs font-medium text-fg-subtle">{project.technologies.join(", ")}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {p.achievements.length ? (
        <Section icon={<Award />} title="Achievements" count={p.achievements.length}>
          <ul className="grid grid-cols-1 gap-2">
            {p.achievements.map((a, i) => (
              <li key={i} className="flex items-start justify-between gap-3 text-sm">
                <span className="text-fg">{a.text}</span>
                {a.measurable && (a.metric || a.value) ? (
                  <Badge tone="success" size="sm" className="shrink-0">
                    {[a.metric, a.value].filter(Boolean).join(": ")}
                  </Badge>
                ) : (
                  <span className="shrink-0 text-xs text-fg-subtle">Not quantified</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {p.education.length || p.certifications.length ? (
        <Section icon={<GraduationCap />} title="Education and certifications">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <ul className="grid grid-cols-1 content-start gap-3">
              {p.education.length === 0 ? <li className="text-sm text-fg-muted">No education listed.</li> : null}
              {p.education.map((e, i) => (
                <li key={i} className="grid grid-cols-1 gap-0.5">
                  <span className="text-sm font-medium text-fg">{e.institution}</span>
                  <span className="text-xs text-fg-muted">
                    {[e.degree, e.field].filter(Boolean).join(", ")}
                    {e.start || e.end ? ` (${[formatResumeDate(e.start), formatResumeDate(e.end)].filter(Boolean).join(" to ")})` : ""}
                  </span>
                </li>
              ))}
            </ul>
            <ul className="grid grid-cols-1 content-start gap-3">
              {p.certifications.length === 0 ? <li className="text-sm text-fg-muted">No certifications listed.</li> : null}
              {p.certifications.map((c, i) => (
                <li key={i} className="grid grid-cols-1 gap-0.5">
                  <span className="text-sm font-medium text-fg">{c.name}</span>
                  <span className="text-xs text-fg-muted">{[c.issuer, c.year].filter(Boolean).join(", ") || "Issuer not given"}</span>
                </li>
              ))}
            </ul>
          </div>
        </Section>
      ) : null}
    </div>
  );
}
