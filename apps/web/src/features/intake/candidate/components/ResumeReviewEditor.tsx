import { Check, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/utils";

import {
  CHIP_LIMITS,
  CHIP_SECTIONS,
  chipRef,
  DATE_MAX,
  emptyEducation,
  emptyProject,
  emptyRole,
  rolePeriod,
  SECTION_LABELS,
  SECTION_SOURCE,
  splitCommas,
  splitLines,
  validateEducation,
  validateProject,
  validateRole,
  type ChipSection,
  type ItemErrors,
} from "../resumeReview";
import { REVIEW_LIMITS, type ResumeReviewDoc, type ReviewEducation, type ReviewProject, type ReviewRole, type ReviewSectionKey, type ReviewSectionState } from "../types";

type ItemSection = "roles" | "projects" | "education";
type Item = ReviewRole | ReviewProject | ReviewEducation;

export interface ResumeReviewEditorProps {
  doc: ResumeReviewDoc;
  onChange: (doc: ResumeReviewDoc) => void;
  /** Server profile state per section key (`experience`, `skills`, ...), for the "Checking" marks. */
  sectionStates: ReadonlyMap<string, ReviewSectionState>;
  /** The profile is checked in full: no more per-section marks. */
  final: boolean;
  disabled?: boolean;
  /** An item form is open (the page holds the confirm button until it is saved or cancelled). */
  onEditingChange?: (editing: boolean) => void;
}

interface Removed {
  section: ItemSection;
  item: Item;
  index: number;
}

function itemLabel(section: ItemSection, item: Item): string {
  if (section === "roles") {
    const r = item as ReviewRole;
    return [r.title, r.company].filter(Boolean).join(" at ") || "this role";
  }
  if (section === "projects") return (item as ReviewProject).name || "this project";
  const e = item as ReviewEducation;
  return [e.degree, e.institution].filter(Boolean).join(", ") || "this entry";
}

/**
 * Editable lists of what the résumé reading produced: roles, projects, skills,
 * programming languages, tools, education and certifications. Items can be
 * edited, added and removed (with undo). Nothing here is scored; the
 * interviewer uses it to ask about real experience.
 */
export function ResumeReviewEditor({ doc, onChange, sectionStates, final, disabled = false, onEditingChange }: ResumeReviewEditorProps) {
  // A new item lives only in the form until it is saved, so cancelling leaves the document untouched.
  const [editing, setEditing] = useState<{ section: ItemSection; ref: string; isNew: boolean; item?: Item } | null>(null);
  const [removed, setRemoved] = useState<Removed[]>([]);

  useEffect(() => onEditingChange?.(editing !== null), [editing, onEditingChange]);

  const items = (section: ItemSection) => doc[section] as Item[];
  const setItems = (section: ItemSection, next: Item[]) => onChange({ ...doc, [section]: next });

  const startAdd = (section: ItemSection) => {
    const fresh = section === "roles" ? emptyRole() : section === "projects" ? emptyProject() : emptyEducation();
    setEditing({ section, ref: fresh.ref, isNew: true, item: fresh });
  };
  const save = (section: ItemSection, item: Item) => {
    if (editing?.isNew) setItems(section, [...items(section), item]);
    else
      setItems(
        section,
        items(section).map((x) => (x.ref === item.ref ? item : x)),
      );
    setEditing(null);
  };
  const cancel = () => setEditing(null);
  const remove = (section: ItemSection, ref: string) => {
    const list = items(section);
    const index = list.findIndex((x) => x.ref === ref);
    if (index === -1) return;
    setRemoved((r) => [...r, { section, item: list[index]!, index }]);
    setItems(
      section,
      list.filter((x) => x.ref !== ref),
    );
  };
  const undo = (entry: Removed) => {
    const list = [...items(entry.section)];
    list.splice(Math.min(entry.index, list.length), 0, entry.item);
    setItems(entry.section, list);
    setRemoved((r) => r.filter((x) => x !== entry));
  };

  const mark = (section: ReviewSectionKey) => (final ? undefined : (sectionStates.get(section) ?? sectionStates.get(SECTION_SOURCE[section])));
  const limitFor = (section: ItemSection) => REVIEW_LIMITS[section];

  const renderItemSection = (section: ItemSection, emptyText: string, addLabel: string) => {
    const list = items(section);
    const gone = removed.filter((r) => r.section === section);
    const adding = editing?.isNew && editing.section === section && editing.item ? editing.item : null;
    return (
      <ReviewSection
        section={section}
        count={list.length}
        state={mark(section)}
        action={
          <Button
            size="sm"
            variant="ghost"
            leftIcon={<Plus />}
            onClick={() => startAdd(section)}
            disabled={disabled || editing !== null || list.length >= limitFor(section)}
            data-testid={`resume-review-add-${section}`}
          >
            {addLabel}
          </Button>
        }
      >
        {list.length || gone.length || adding ? (
          <ul className="grid grid-cols-1 divide-y divide-line-subtle">
            {list.map((item) =>
              editing?.section === section && editing.ref === item.ref && !editing.isNew ? (
                <li key={item.ref} data-testid={`resume-review-item-${item.ref}`} className="py-3">
                  <ItemForm section={section} item={item} onSave={(next) => save(section, next)} onCancel={cancel} isNew={false} />
                </li>
              ) : (
                <ItemRow
                  key={item.ref}
                  section={section}
                  item={item}
                  disabled={disabled || editing !== null}
                  onEdit={() => setEditing({ section, ref: item.ref, isNew: false })}
                  onRemove={() => remove(section, item.ref)}
                />
              ),
            )}
            {adding ? (
              <li key={adding.ref} data-testid={`resume-review-item-${adding.ref}`} className="py-3">
                <ItemForm section={section} item={adding} onSave={(next) => save(section, next)} onCancel={cancel} isNew />
              </li>
            ) : null}
            {gone.map((entry) => (
              <li key={`removed-${entry.item.ref}`} className="flex items-center justify-between gap-3 py-2.5 text-[13px] leading-5 text-fg-muted">
                <span className="min-w-0 truncate">
                  Removed <span className="text-fg line-through decoration-fg-subtle">{itemLabel(section, entry.item)}</span>
                </span>
                <Button size="xs" variant="ghost" onClick={() => undo(entry)} disabled={disabled}>
                  Undo
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm leading-6 text-fg-muted">{emptyText}</p>
        )}
      </ReviewSection>
    );
  };

  return (
    <div className="grid grid-cols-1">
      <SummaryBlock doc={doc} onChange={onChange} disabled={disabled} />
      {renderItemSection("roles", "We didn't find any roles. Add your recent jobs, internships or freelance work.", "Add a role")}
      {renderItemSection("projects", "We didn't find any projects. Add one if you'd like it to come up in the interview.", "Add a project")}
      {CHIP_SECTIONS.map((section) => (
        <ChipList key={section} section={section} values={doc[section]} state={mark(section)} disabled={disabled} onChange={(values) => onChange({ ...doc, [section]: values })} />
      ))}
      {renderItemSection("education", "We didn't find any education. Add a degree, course or school if you'd like.", "Add education")}
    </div>
  );
}

// ------------------------------------------------------------------ section frame

function SectionMark({ state }: { state: ReviewSectionState | undefined }) {
  if (!state || state === "pending" || state === "quick") return null;
  if (state === "done") {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-success-fg">
        <Check className="size-3" strokeWidth={3} aria-hidden="true" />
        Checked
      </span>
    );
  }
  if (state === "fallback") return <span className="text-xs text-fg-subtle">As read from your file</span>;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-fg-subtle">
      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      Checking
    </span>
  );
}

function ReviewSection({ section, count, state, action, children }: { section: ReviewSectionKey; count: number; state: ReviewSectionState | undefined; action?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section data-testid={`resume-review-section-${section}`} aria-labelledby={id} className="grid grid-cols-1 gap-3 border-t border-line py-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 id={id} className="text-[15px] leading-6 font-semibold text-fg">
            {SECTION_LABELS[section]}
            {count ? <span className="ml-2 font-normal text-fg-muted tabular-nums">{count}</span> : null}
          </h2>
          <SectionMark state={state} />
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

// ------------------------------------------------------------------ summary (headline + years)

function SummaryBlock({ doc, onChange, disabled }: { doc: ResumeReviewDoc; onChange: (doc: ResumeReviewDoc) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [headline, setHeadline] = useState(doc.headline ?? "");
  const [years, setYears] = useState(doc.experience_years === null ? "" : String(doc.experience_years));
  const [error, setError] = useState<string | null>(null);
  const baseId = useId();
  if (!open) {
    return (
      <div data-testid="resume-review-item-summary" className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 pb-6">
        <div className="min-w-0">
          <p className="text-[15px] leading-6 font-medium break-words text-fg">{doc.headline || "No headline found"}</p>
          <p className="text-[13px] leading-5 text-fg-muted">
            {doc.experience_years === null ? "Years of experience not found" : `${formatYears(doc.experience_years)} of experience, as read from your résumé`}
          </p>
        </div>
        <Button
          size="sm"
          variant="ghost"
          leftIcon={<Pencil />}
          disabled={disabled}
          onClick={() => {
            setHeadline(doc.headline ?? "");
            setYears(doc.experience_years === null ? "" : String(doc.experience_years));
            setError(null);
            setOpen(true);
          }}
          data-testid="resume-review-edit-summary"
          aria-label="Edit headline and years of experience"
        >
          Edit
        </Button>
      </div>
    );
  }
  const commit = () => {
    const text = years.trim().replace(",", ".");
    const parsed = text ? Number(text) : null;
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0 || parsed > 60)) {
      setError("Enter years from 0 to 60, or leave it empty.");
      return;
    }
    if (headline.length > 200) {
      setError("Keep the headline under 200 characters.");
      return;
    }
    onChange({ ...doc, headline: headline.trim() || null, experience_years: parsed });
    setOpen(false);
  };
  return (
    <div data-testid="resume-review-item-summary" className="pb-6">
      <EditFrame label="Edit headline and years of experience" onSave={commit} onCancel={() => setOpen(false)}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
          <div className="grid gap-1.5">
            <Label htmlFor={`${baseId}-headline`}>Headline</Label>
            <Input id={`${baseId}-headline`} value={headline} maxLength={200} onChange={(e) => setHeadline(e.target.value)} autoFocus />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${baseId}-years`}>Years of experience</Label>
            <Input id={`${baseId}-years`} inputMode="decimal" value={years} onChange={(e) => setYears(e.target.value)} aria-invalid={error ? true : undefined} />
          </div>
        </div>
        {error ? <p className="text-[13px] font-medium text-danger-fg">{error}</p> : null}
      </EditFrame>
    </div>
  );
}

function formatYears(years: number): string {
  const rounded = Math.round(years * 10) / 10;
  return `${rounded} year${rounded === 1 ? "" : "s"}`;
}

// ------------------------------------------------------------------ structured items

function ItemRow({ section, item, disabled, onEdit, onRemove }: { section: ItemSection; item: Item; disabled: boolean; onEdit: () => void; onRemove: () => void }) {
  const label = itemLabel(section, item);
  return (
    <li data-testid={`resume-review-item-${item.ref}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 py-3">
      <div className="min-w-0">
        {section === "roles" ? <RoleSummary role={item as ReviewRole} /> : section === "projects" ? <ProjectSummary project={item as ReviewProject} /> : <EducationSummary item={item as ReviewEducation} />}
      </div>
      <div className="-mr-2 flex shrink-0 items-center">
        <Button size="sm" variant="ghost" leftIcon={<Pencil />} onClick={onEdit} disabled={disabled} aria-label={`Edit ${label}`} data-testid={`resume-review-edit-${item.ref}`}>
          <span className="max-sm:sr-only">Edit</span>
        </Button>
        <Button size="sm" variant="ghost" leftIcon={<Trash2 />} onClick={onRemove} disabled={disabled} aria-label={`Remove ${label}`} data-testid={`resume-review-remove-${item.ref}`} className="hover:text-danger-fg">
          <span className="max-sm:sr-only">Remove</span>
        </Button>
      </div>
    </li>
  );
}

function Bullets({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="mt-1.5 grid list-disc gap-0.5 pl-4 text-[13px] leading-5 text-fg-muted marker:text-fg-subtle">
      {items.map((text, i) => (
        <li key={`${i}-${text}`} className="break-words">
          {text}
        </li>
      ))}
    </ul>
  );
}

function RoleSummary({ role }: { role: ReviewRole }) {
  const period = rolePeriod(role);
  return (
    <>
      <p className="text-[15px] leading-6 break-words text-fg">
        <span className="font-medium">{role.title || "Role"}</span>
        {role.company ? <span className="text-fg-muted"> at {role.company}</span> : null}
      </p>
      {period ? <p className="text-[13px] leading-5 text-fg-subtle tabular-nums">{period}</p> : null}
      {role.technologies.length ? <p className="mt-1 text-[13px] leading-5 break-words text-fg-muted">{role.technologies.join(", ")}</p> : null}
      <Bullets items={uniqueLines([...role.responsibilities, ...role.achievements])} />
    </>
  );
}

/** The quick parse can put the same résumé line in both responsibilities and achievements: show it once. */
function uniqueLines(lines: string[]): string[] {
  const seen = new Set<string>();
  return lines.filter((line) => {
    const key = line.trim().toLowerCase().replace(/\s+/g, " ");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function ProjectSummary({ project }: { project: ReviewProject }) {
  return (
    <>
      <p className="text-[15px] leading-6 break-words text-fg">
        <span className="font-medium">{project.name || "Project"}</span>
        {project.role ? <span className="text-fg-muted">, {project.role}</span> : null}
      </p>
      {project.description ? <p className="mt-0.5 text-[13px] leading-5 break-words text-fg-muted">{project.description}</p> : null}
      {project.technologies.length ? <p className="mt-1 text-[13px] leading-5 break-words text-fg-subtle">{project.technologies.join(", ")}</p> : null}
      <Bullets items={project.outcomes} />
    </>
  );
}

function EducationSummary({ item }: { item: ReviewEducation }) {
  const degree = [item.degree, item.field].filter(Boolean).join(", ");
  return (
    <p className="text-[15px] leading-6 break-words text-fg">
      {degree ? <span className="font-medium">{degree}</span> : null}
      {degree && item.institution ? <span className="text-fg-muted">, </span> : null}
      {item.institution ? <span className={degree ? "text-fg-muted" : "font-medium"}>{item.institution}</span> : null}
    </p>
  );
}

function EditFrame({ label, onSave, onCancel, children, saveLabel = "Save" }: { label: string; onSave: () => void; onCancel: () => void; children: ReactNode; saveLabel?: string }) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onCancel();
    }
  };
  return (
    <div role="group" aria-label={label} onKeyDown={onKeyDown} className="grid grid-cols-1 gap-4 rounded-lg bg-surface-subtle p-4 ring-1 ring-line sm:p-5">
      {children}
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={onSave} data-testid="resume-review-item-save">
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}

function TextField({ id, label, value, onChange, error, hint, optional, autoFocus, maxLength }: { id: string; label: string; value: string; onChange: (v: string) => void; error?: string; hint?: string; optional?: boolean; autoFocus?: boolean; maxLength?: number }) {
  return (
    <div className="grid min-w-0 content-start gap-1.5">
      <Label htmlFor={id} optional={optional}>
        {label}
      </Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={hint ? `${id}-hint` : undefined} autoFocus={autoFocus} maxLength={maxLength} />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs leading-[1.125rem] text-fg-muted">
          {hint}
        </p>
      ) : null}
      {error ? <p className="text-[13px] leading-5 font-medium text-danger-fg">{error}</p> : null}
    </div>
  );
}

function AreaField({ id, label, value, onChange, hint, error, maxLength, rows = 3 }: { id: string; label: string; value: string; onChange: (v: string) => void; hint?: string; error?: string; maxLength?: number; rows?: number }) {
  return (
    <div className="grid min-w-0 content-start gap-1.5">
      <Label htmlFor={id} optional>
        {label}
      </Label>
      <Textarea id={id} value={value} onChange={(e) => onChange(e.target.value)} rows={rows} autoResize maxRows={8} maxLength={maxLength} aria-describedby={hint ? `${id}-hint` : undefined} aria-invalid={error ? true : undefined} />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs leading-[1.125rem] text-fg-muted">
          {hint}
        </p>
      ) : null}
      {error ? <p className="text-[13px] leading-5 font-medium text-danger-fg">{error}</p> : null}
    </div>
  );
}

function ItemForm({ section, item, onSave, onCancel, isNew }: { section: ItemSection; item: Item; onSave: (item: Item) => void; onCancel: () => void; isNew: boolean }) {
  if (section === "roles") return <RoleForm role={item as ReviewRole} onSave={onSave} onCancel={onCancel} isNew={isNew} />;
  if (section === "projects") return <ProjectForm project={item as ReviewProject} onSave={onSave} onCancel={onCancel} isNew={isNew} />;
  return <EducationForm item={item as ReviewEducation} onSave={onSave} onCancel={onCancel} isNew={isNew} />;
}

function RoleForm({ role, onSave, onCancel, isNew }: { role: ReviewRole; onSave: (r: ReviewRole) => void; onCancel: () => void; isNew: boolean }) {
  const id = useId();
  const [title, setTitle] = useState(role.title);
  const [company, setCompany] = useState(role.company);
  const [start, setStart] = useState(role.start ?? "");
  const [end, setEnd] = useState(role.end ?? "");
  const [tech, setTech] = useState(role.technologies.join(", "));
  const [duties, setDuties] = useState(role.responsibilities.join("\n"));
  const [wins, setWins] = useState(role.achievements.join("\n"));
  const [errors, setErrors] = useState<ItemErrors>({});
  const submit = () => {
    const next: ReviewRole = {
      ...role,
      title: title.trim(),
      company: company.trim(),
      start: start.trim() || null,
      end: end.trim() || null,
      technologies: splitCommas(tech, REVIEW_LIMITS.technologies),
      responsibilities: splitLines(duties, REVIEW_LIMITS.responsibilities),
      achievements: splitLines(wins, REVIEW_LIMITS.achievements),
    };
    const found = validateRole(next);
    setErrors(found);
    if (!Object.keys(found).length) onSave(next);
  };
  return (
    <EditFrame label={isNew ? "Add a role" : `Edit ${role.title || "role"}`} onSave={submit} onCancel={onCancel} saveLabel={isNew ? "Add role" : "Save"}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField id={`${id}-title`} label="Job title" value={title} onChange={setTitle} error={errors.title} autoFocus maxLength={160} />
        <TextField id={`${id}-company`} label="Company" value={company} onChange={setCompany} error={errors.company} maxLength={160} />
        <TextField id={`${id}-start`} label="Started" value={start} onChange={setStart} optional hint="For example 2021-03" error={errors.start} maxLength={DATE_MAX} />
        <TextField id={`${id}-end`} label="Ended" value={end} onChange={setEnd} optional hint="Leave empty if you still work there" error={errors.end} maxLength={DATE_MAX} />
      </div>
      <TextField id={`${id}-tech`} label="Technologies" value={tech} onChange={setTech} optional hint={`Separated by commas, up to ${REVIEW_LIMITS.technologies}`} error={errors.technologies} />
      <AreaField id={`${id}-duties`} label="What you did" value={duties} onChange={setDuties} hint={`One per line, up to ${REVIEW_LIMITS.responsibilities}`} error={errors.responsibilities} />
      <AreaField id={`${id}-wins`} label="Results you're proud of" value={wins} onChange={setWins} hint={`One per line, up to ${REVIEW_LIMITS.achievements}`} error={errors.achievements} rows={2} />
    </EditFrame>
  );
}

function ProjectForm({ project, onSave, onCancel, isNew }: { project: ReviewProject; onSave: (p: ReviewProject) => void; onCancel: () => void; isNew: boolean }) {
  const id = useId();
  const [name, setName] = useState(project.name);
  const [role, setRole] = useState(project.role ?? "");
  const [description, setDescription] = useState(project.description);
  const [tech, setTech] = useState(project.technologies.join(", "));
  const [outcomes, setOutcomes] = useState(project.outcomes.join("\n"));
  const [errors, setErrors] = useState<ItemErrors>({});
  const submit = () => {
    const next: ReviewProject = {
      ...project,
      name: name.trim(),
      role: role.trim() || null,
      description: description.trim(),
      technologies: splitCommas(tech, REVIEW_LIMITS.technologies),
      outcomes: splitLines(outcomes, REVIEW_LIMITS.outcomes),
    };
    const found = validateProject(next);
    setErrors(found);
    if (!Object.keys(found).length) onSave(next);
  };
  return (
    <EditFrame label={isNew ? "Add a project" : `Edit ${project.name || "project"}`} onSave={submit} onCancel={onCancel} saveLabel={isNew ? "Add project" : "Save"}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField id={`${id}-name`} label="Project name" value={name} onChange={setName} error={errors.name} autoFocus maxLength={160} />
        <TextField id={`${id}-role`} label="Your role" value={role} onChange={setRole} optional maxLength={160} />
      </div>
      <AreaField id={`${id}-description`} label="What it was" value={description} onChange={setDescription} error={errors.description} maxLength={600} />
      <TextField id={`${id}-tech`} label="Technologies" value={tech} onChange={setTech} optional hint={`Separated by commas, up to ${REVIEW_LIMITS.technologies}`} error={errors.technologies} />
      <AreaField id={`${id}-outcomes`} label="Outcomes" value={outcomes} onChange={setOutcomes} hint={`One per line, up to ${REVIEW_LIMITS.outcomes}`} error={errors.outcomes} rows={2} />
    </EditFrame>
  );
}

function EducationForm({ item, onSave, onCancel, isNew }: { item: ReviewEducation; onSave: (e: ReviewEducation) => void; onCancel: () => void; isNew: boolean }) {
  const id = useId();
  const [institution, setInstitution] = useState(item.institution);
  const [degree, setDegree] = useState(item.degree ?? "");
  const [field, setField] = useState(item.field ?? "");
  const [errors, setErrors] = useState<ItemErrors>({});
  const submit = () => {
    const next: ReviewEducation = { ...item, institution: institution.trim(), degree: degree.trim() || null, field: field.trim() || null };
    const found = validateEducation(next);
    setErrors(found);
    if (!Object.keys(found).length) onSave(next);
  };
  return (
    <EditFrame label={isNew ? "Add education" : `Edit ${item.institution || "education"}`} onSave={submit} onCancel={onCancel} saveLabel={isNew ? "Add" : "Save"}>
      <TextField id={`${id}-institution`} label="School or institution" value={institution} onChange={setInstitution} error={errors.institution} autoFocus maxLength={200} />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField id={`${id}-degree`} label="Degree or course" value={degree} onChange={setDegree} optional maxLength={160} />
        <TextField id={`${id}-field`} label="Field of study" value={field} onChange={setField} optional maxLength={160} />
      </div>
    </EditFrame>
  );
}

// ------------------------------------------------------------------ chip lists

const CHIP_COPY: Record<ChipSection, { placeholder: string; empty: string; noun: string }> = {
  skills: { placeholder: "Add a skill", empty: "No skills found yet.", noun: "skill" },
  languages: { placeholder: "Add a language, e.g. Python", empty: "No programming languages found yet.", noun: "language" },
  tools: { placeholder: "Add a tool, e.g. Git", empty: "No tools found yet.", noun: "tool" },
  certifications: { placeholder: "Add a certification", empty: "No certifications found.", noun: "certification" },
};

function ChipList({ section, values, state, disabled, onChange }: { section: ChipSection; values: string[]; state: ReviewSectionState | undefined; disabled: boolean; onChange: (values: string[]) => void }) {
  const inputId = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const limit = CHIP_LIMITS[section];
  const copy = CHIP_COPY[section];

  const add = () => {
    const value = text.trim();
    if (!value) return;
    if (value.length > limit.length) return setError(`Keep each ${copy.noun} under ${limit.length} characters.`);
    if (values.some((v) => v.toLowerCase() === value.toLowerCase())) return setError(`"${value}" is already in the list.`);
    if (values.length >= limit.items) return setError(`You can list up to ${limit.items}.`);
    onChange([...values, value]);
    setText("");
    setError(null);
  };

  return (
    <ReviewSection section={section} count={values.length} state={state}>
      {values.length ? (
        <ul className="flex flex-wrap gap-2">
          {values.map((value, index) =>
            editingIndex === index ? (
              <ChipEditor
                key={`${index}-${value}`}
                refId={chipRef(section, index)}
                value={value}
                maxLength={limit.length}
                onDone={(next) => {
                  setEditingIndex(null);
                  const trimmed = next.trim();
                  if (!trimmed) onChange(values.filter((_, i) => i !== index));
                  else if (trimmed !== value) onChange(values.map((v, i) => (i === index ? trimmed : v)));
                }}
                onCancel={() => setEditingIndex(null)}
              />
            ) : (
              <li key={`${index}-${value}`} data-testid={`resume-review-item-${chipRef(section, index)}`} className="inline-flex max-w-full min-w-0 items-center rounded-full border border-line bg-surface text-sm text-fg">
                <button
                  type="button"
                  className="min-w-0 truncate rounded-full py-1 pr-1 pl-3 text-left hover:text-accent-fg disabled:pointer-events-none"
                  onClick={() => setEditingIndex(index)}
                  disabled={disabled}
                  aria-label={`Edit ${value}`}
                  title={value}
                  data-testid={`resume-review-edit-${chipRef(section, index)}`}
                >
                  {value}
                </button>
                <button
                  type="button"
                  className="mr-1 flex size-7 shrink-0 items-center justify-center rounded-full text-fg-subtle hover:bg-surface-hover hover:text-fg disabled:pointer-events-none"
                  onClick={() => onChange(values.filter((_, i) => i !== index))}
                  disabled={disabled}
                  aria-label={`Remove ${value}`}
                  data-testid={`resume-review-remove-${chipRef(section, index)}`}
                >
                  <X className="size-3.5" aria-hidden="true" />
                </button>
              </li>
            ),
          )}
        </ul>
      ) : (
        <p className="text-sm leading-6 text-fg-muted">{copy.empty}</p>
      )}
      <div className="grid gap-1.5">
        <label htmlFor={inputId} className="sr-only">
          {copy.placeholder}
        </label>
        <div className="flex max-w-md gap-2">
          <Input
            id={inputId}
            value={text}
            placeholder={copy.placeholder}
            maxLength={limit.length + 1}
            onChange={(e) => {
              setText(e.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            disabled={disabled || values.length >= limit.items}
            aria-invalid={error ? true : undefined}
          />
          <Button variant="outline" leftIcon={<Plus />} onClick={add} disabled={disabled || !text.trim()} data-testid={`resume-review-add-${section}`}>
            Add
          </Button>
        </div>
        {error ? (
          <p role="alert" className="text-[13px] leading-5 font-medium text-danger-fg">
            {error}
          </p>
        ) : null}
      </div>
    </ReviewSection>
  );
}

function ChipEditor({ refId, value, maxLength, onDone, onCancel }: { refId: string; value: string; maxLength: number; onDone: (value: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(value);
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    if (save) onDone(text);
    else onCancel();
  };
  return (
    <li data-testid={`resume-review-item-${refId}`} className="inline-flex max-w-full">
      <input
        ref={ref}
        value={text}
        maxLength={maxLength}
        aria-label={`Edit ${value}`}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            finish(true);
          } else if (e.key === "Escape") {
            e.preventDefault();
            finish(false);
          }
        }}
        className={cn("h-8 w-48 max-w-full rounded-full border border-accent bg-field px-3 text-sm text-fg outline-none ring-3 ring-accent/15")}
      />
    </li>
  );
}
