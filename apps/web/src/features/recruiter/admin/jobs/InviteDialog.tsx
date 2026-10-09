/**
 * Invite a candidate to a job round. The invite link and access code come
 * back exactly once (only hashes are stored), so the result step makes
 * copying them the obvious next action and warns before closing.
 */

import { AlertTriangle, Check, KeyRound, Link2, Mail, UserPlus, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Combobox } from "@/components/ui/Combobox";
import { confirm } from "@/components/ui/AlertDialog";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { formatDateTime } from "@/lib/format";
import { useDebounce } from "@/lib/hooks";
import { DIFFICULTY_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { Candidate, InterviewCreateResponse, Job, Round } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useOrgSettings } from "../api/admin";
import { useCandidates, useCreateCandidate, useCreateInterview, useRegenerateInviteLink } from "../api/candidates";
import { isApiError } from "@/lib/api";
import { useJobRounds, useJobs } from "../api/jobs";
import { SearchInput, SecretField } from "../components/Controls";
import { FieldShell } from "../components/Field";
import { describeFailure, fieldErrorsOf, type FieldErrors } from "../lib/serverErrors";

const CHOOSE = "__candidate_chooses__";

export const EXPIRY_OPTIONS = [
  { value: "24", label: "24 hours" },
  { value: "72", label: "3 days" },
  { value: "168", label: "7 days" },
  { value: "336", label: "14 days" },
  { value: "720", label: "30 days" },
];

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface InviteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fixed job (job detail page); otherwise the dialog asks for one. */
  job?: Job | null;
  /** Rounds of `job` when already loaded. */
  rounds?: Round[];
  /** Fixed candidate (candidate detail page). */
  candidate?: Candidate | null;
}

export function InviteDialog({ open, onOpenChange, job: fixedJob, rounds: fixedRounds, candidate: fixedCandidate }: InviteDialogProps) {
  const [result, setResult] = useState<InterviewCreateResponse | null>(null);
  const [copied, setCopied] = useState<{ link: boolean; code: boolean }>({ link: false, code: false });

  useEffect(() => {
    if (!open) {
      setResult(null);
      setCopied({ link: false, code: false });
    }
  }, [open]);

  const requestClose = async () => {
    if (result) {
      const needsCode = Boolean(result.access_code);
      if (!copied.link || (needsCode && !copied.code)) {
        const ok = await confirm({
          title: "Close without copying?",
          description: "The invite link and access code can't be shown again. You'd need to generate a new link.",
          confirmLabel: "Close anyway",
          cancelLabel: "Go back",
          tone: "danger",
        });
        if (!ok) return;
      }
    }
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : void requestClose())}>
      <DialogContent size="lg" onInteractOutside={(event) => result && event.preventDefault()}>
        {result ? (
          <InviteSecretsView
            title="Invitation ready"
            description={`${result.interview.candidate?.full_name ?? "The candidate"} is invited${result.interview.job?.title ? ` to ${result.interview.job.title}` : ""}. Link valid until ${formatDateTime(result.interview.invite_expires_at)}.`}
            inviteUrl={result.invite_url}
            accessCode={result.access_code ?? null}
            onCopied={(kind) => setCopied((c) => ({ ...c, [kind]: true }))}
            onDone={() => void requestClose()}
          />
        ) : (
          <InviteForm
            open={open}
            fixedJob={fixedJob ?? null}
            fixedRounds={fixedRounds}
            fixedCandidate={fixedCandidate ?? null}
            onCancel={() => onOpenChange(false)}
            onCreated={setResult}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

// -------------------------------------------------------------------- form

interface InviteFormProps {
  open: boolean;
  fixedJob: Job | null;
  fixedRounds?: Round[];
  fixedCandidate: Candidate | null;
  onCancel: () => void;
  onCreated: (result: InterviewCreateResponse) => void;
}

function InviteForm({ open, fixedJob, fixedRounds, fixedCandidate, onCancel, onCreated }: InviteFormProps) {
  const settings = useOrgSettings();
  const orgRequiresCode = settings.data?.require_access_code ?? false;

  const [jobId, setJobId] = useState<string | null>(fixedJob?.id ?? null);
  const jobsQuery = useJobs({ status: "open", limit: 100, offset: 0 }, { enabled: open && !fixedJob });
  const job = fixedJob ?? jobsQuery.data?.items.find((j) => j.id === jobId) ?? null;
  const roundsQuery = useJobRounds(fixedRounds ? undefined : (jobId ?? undefined));
  const rounds = (fixedRounds ?? roundsQuery.data ?? []).filter((r) => r.is_active !== false);

  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [search, setSearch] = useState("");
  const q = useDebounce(search.trim(), 250);
  const [candidateId, setCandidateId] = useState<string | null>(fixedCandidate?.id ?? null);
  const [picked, setPicked] = useState<Candidate | null>(fixedCandidate);
  const [newCandidate, setNewCandidate] = useState({ full_name: "", email: "", phone: "" });
  const candidates = useCandidates({ q: q || undefined, limit: 6, offset: 0 }, open && !fixedCandidate && mode === "existing");

  const selectableRounds = rounds.filter((r) => r.candidate_selectable || r.config?.candidate_selectable);
  // The API offers a choice among rounds marked candidate-selectable (at least one).
  const allowChoice = Boolean(job?.allow_candidate_round_selection) && selectableRounds.length > 0;
  const [roundChoice, setRoundChoice] = useState<string | null>(null);
  useEffect(() => {
    // Default: let the candidate choose when the job allows it, else the first round.
    if (roundChoice && (roundChoice === CHOOSE ? allowChoice : rounds.some((r) => r.id === roundChoice))) return;
    setRoundChoice(allowChoice ? CHOOSE : (rounds[0]?.id ?? null));
  }, [allowChoice, rounds, roundChoice]);

  const [expires, setExpires] = useState("168");
  const [requireCode, setRequireCode] = useState(false);
  useEffect(() => {
    if (orgRequiresCode) setRequireCode(true);
  }, [orgRequiresCode]);

  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [existingInterviewId, setExistingInterviewId] = useState<string | null>(null);
  const createCandidate = useCreateCandidate();
  const createInterview = useCreateInterview();
  const regenerate = useRegenerateInviteLink();
  const busy = createCandidate.isPending || createInterview.isPending || regenerate.isPending;

  const validate = (): FieldErrors => {
    const out: FieldErrors = {};
    if (!job) out.job = "Choose a job.";
    if (mode === "existing" && !candidateId) out.candidate = "Choose a candidate, or add a new one.";
    if (mode === "new") {
      if (!newCandidate.full_name.trim()) out.full_name = "Enter the candidate's name.";
      if (!EMAIL.test(newCandidate.email.trim())) out.email = "Enter a valid email address. The candidate confirms it to open the link.";
    }
    if (!roundChoice) out.round = rounds.length === 0 ? "This job has no active rounds yet. Add one before inviting." : "Choose a round.";
    return out;
  };

  const submit = async () => {
    setFormError(null);
    setExistingInterviewId(null);
    const found = validate();
    setErrors(found);
    if (Object.keys(found).length) return;
    let targetId = candidateId;
    if (mode === "new") {
      try {
        const created = await createCandidate.mutateAsync({
          full_name: newCandidate.full_name.trim(),
          email: newCandidate.email.trim().toLowerCase(),
          phone: newCandidate.phone.trim() || null,
        });
        targetId = created.id;
        setCandidateId(created.id);
        setPicked(created);
        setMode("existing");
      } catch (error) {
        const fields = fieldErrorsOf(error);
        setErrors(fields.email || fields.full_name || fields.phone ? fields : {});
        if (!fields.email && !fields.full_name && !fields.phone) setFormError(describeFailure(error));
        return;
      }
    }
    try {
      const response = await createInterview.mutateAsync({
        candidate_id: targetId!,
        job_id: job!.id,
        round_id: roundChoice === CHOOSE ? null : roundChoice,
        allow_round_selection: roundChoice === CHOOSE,
        expires_in_hours: Number(expires),
        require_access_code: requireCode,
      });
      onCreated({
        ...response,
        interview: {
          ...response.interview,
          candidate: response.interview.candidate ?? (picked ? { id: picked.id, full_name: picked.full_name, email: picked.email } : undefined),
          job: response.interview.job ?? (job ? { id: job.id, title: job.title } : undefined),
        },
      });
    } catch (error) {
      setFormError(describeFailure(error));
      const existing = isApiError(error) && error.code === "interview_exists" ? error.details.interview_id : null;
      if (typeof existing === "string") setExistingInterviewId(existing);
    }
  };

  const regenerateExisting = async () => {
    if (!existingInterviewId) return;
    try {
      const response = await regenerate.mutateAsync(existingInterviewId);
      onCreated({ ...response, invite_url: response.invite_url, access_code: response.access_code ?? null });
    } catch (error) {
      setFormError(describeFailure(error));
    }
  };

  const jobOptions = useMemo(() => (jobsQuery.data?.items ?? []).map((j) => ({ value: j.id, label: j.title, description: j.department ?? undefined })), [jobsQuery.data]);

  return (
    <>
      <DialogHeader>
        <DialogTitle>Invite a candidate</DialogTitle>
        <DialogDescription>
          {fixedJob ? `For ${fixedJob.title}. ` : ""}You'll get a private link to send the candidate yourself; it's shown only once.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="grid gap-5">
        {!fixedJob ? (
          <FieldShell label="Job" required error={errors.job}>
            {(control) => (
              <Combobox
                {...control}
                options={jobOptions}
                value={jobId}
                onChange={(v) => {
                  setJobId(v);
                  setRoundChoice(null);
                }}
                placeholder={jobsQuery.isLoading ? "Loading open jobs…" : "Search open jobs"}
                emptyMessage="No open jobs. Open a job to invite candidates."
              />
            )}
          </FieldShell>
        ) : null}

        {/* Candidate */}
        {fixedCandidate ? (
          <div className="flex items-center gap-3 rounded-md border border-line bg-surface-subtle px-3 py-2.5">
            <Avatar name={fixedCandidate.full_name} size="sm" />
            <div className="grid min-w-0">
              <span className="truncate text-sm font-medium text-fg">{fixedCandidate.full_name}</span>
              <span className="truncate text-xs text-fg-muted">{fixedCandidate.email}</span>
            </div>
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[13px] font-medium text-fg">Candidate</span>
              <Tabs value={mode} onValueChange={(v) => setMode(v as "existing" | "new")} variant="segmented">
                <TabsList aria-label="Candidate source">
                  <TabsTrigger value="existing">
                    <Users /> Existing
                  </TabsTrigger>
                  <TabsTrigger value="new">
                    <UserPlus /> New
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
            {mode === "existing" ? (
              <div className="grid gap-2">
                <SearchInput value={search} onChange={setSearch} placeholder="Search by name or email" autoFocus />
                <div role="radiogroup" aria-label="Candidates" className="grid max-h-60 gap-1 overflow-y-auto scrollbar-thin">
                  {candidates.isLoading ? (
                    Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-12 w-full" />)
                  ) : (candidates.data?.items ?? []).length === 0 ? (
                    <p className="px-1 py-3 text-sm text-fg-muted">
                      {q ? `No candidates match “${q}”.` : "No candidates yet."}{" "}
                      <button type="button" className="font-medium text-accent hover:underline" onClick={() => setMode("new")}>
                        Add a new candidate
                      </button>
                    </p>
                  ) : (
                    (candidates.data?.items ?? []).map((c) => {
                      const active = c.id === candidateId;
                      return (
                        <button
                          key={c.id}
                          type="button"
                          role="radio"
                          aria-checked={active}
                          onClick={() => {
                            setCandidateId(c.id);
                            setPicked(c);
                          }}
                          className={cn(
                            "flex items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                            active ? "border-primary bg-surface shadow-[0_0_0_1px_var(--primary)]" : "border-transparent hover:bg-surface-hover",
                          )}
                        >
                          <Avatar name={c.full_name} size="sm" />
                          <span className="grid min-w-0 flex-1">
                            <span className="truncate text-sm font-medium text-fg">{c.full_name}</span>
                            <span className="truncate text-xs text-fg-muted">{c.email}</span>
                          </span>
                          {c.latest_interview_status ? (
                            <span title="Latest interview" className="shrink-0">
                              <StatusBadge kind="interview" value={c.latest_interview_status} size="sm" />
                            </span>
                          ) : null}
                          {active ? <Check className="size-4 text-fg" aria-hidden="true" /> : null}
                        </button>
                      );
                    })
                  )}
                </div>
                {errors.candidate ? <p className="text-xs font-medium text-danger-fg">{errors.candidate}</p> : null}
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <FieldShell label="Full name" required error={errors.full_name}>
                  {(control) => <Input {...control} autoFocus value={newCandidate.full_name} onChange={(e) => setNewCandidate((c) => ({ ...c, full_name: e.target.value }))} />}
                </FieldShell>
                <FieldShell label="Email" required error={errors.email}>
                  {(control) => (
                    <Input {...control} type="email" leading={<Mail />} value={newCandidate.email} onChange={(e) => setNewCandidate((c) => ({ ...c, email: e.target.value }))} />
                  )}
                </FieldShell>
                <FieldShell label="Phone" optional className="sm:col-span-2" error={errors.phone}>
                  {(control) => <Input {...control} type="tel" value={newCandidate.phone} onChange={(e) => setNewCandidate((c) => ({ ...c, phone: e.target.value }))} />}
                </FieldShell>
              </div>
            )}
          </div>
        )}

        {/* Round */}
        <FieldShell label="Round" required error={errors.round}>
          {() =>
            job && roundsQuery.isLoading && !fixedRounds ? (
              <Skeleton className="h-20 w-full" />
            ) : rounds.length === 0 ? (
              <p className="rounded-md border border-dashed border-line-strong px-3 py-3 text-sm text-fg-muted">{job ? "This job has no active rounds yet." : "Choose a job first."}</p>
            ) : (
              <RadioGroup value={roundChoice ?? ""} onValueChange={setRoundChoice} className="gap-2" aria-label="Round">
                {allowChoice ? (
                  <RadioGroupItem
                    value={CHOOSE}
                    label="Let the candidate choose"
                    description={`They pick from ${selectableRounds.map((r) => r.name).join(", ")} when they open the link.`}
                  />
                ) : null}
                {rounds.map((round) => (
                  <RadioGroupItem
                    key={round.id}
                    value={round.id}
                    label={round.name}
                    description={`${ROUND_TYPE_LABELS[round.round_type]}, ${round.duration_minutes} min, starts ${DIFFICULTY_LABELS[round.expected_difficulty].toLowerCase()}${round.config?.coding?.enabled ? ", includes coding" : ""}`}
                  />
                ))}
              </RadioGroup>
            )
          }
        </FieldShell>

        <div className="grid gap-4 sm:grid-cols-2">
          <FieldShell label="Link expires after">
            {(control) => <Select<string> id={control.id} value={expires} onValueChange={setExpires} options={EXPIRY_OPTIONS} />}
          </FieldShell>
          <div className="grid content-start gap-1.5">
            <span className="text-[13px] font-medium text-fg">Access code</span>
            <div className="flex min-h-9 items-center">
              <Switch
                label="Require an access code"
                description={orgRequiresCode ? "Required by your organization's settings." : "A short code you send separately from the link."}
                checked={requireCode}
                disabled={orgRequiresCode}
                onCheckedChange={setRequireCode}
              />
            </div>
          </div>
        </div>

        {formError ? (
          <Callout
            tone={existingInterviewId ? "warning" : "danger"}
            actions={
              existingInterviewId ? (
                <Button size="sm" variant="outline" loading={regenerate.isPending} onClick={() => void regenerateExisting()}>
                  Generate a new link for it
                </Button>
              ) : undefined
            }
          >
            {formError}
            {existingInterviewId ? " The current link stops working when you generate a new one." : null}
          </Callout>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button onClick={submit} loading={busy} loadingText="Creating invitation…" leftIcon={<Link2 />}>
          Create invitation
        </Button>
      </DialogFooter>
    </>
  );
}

// ----------------------------------------------------------------- secrets

export interface InviteSecretsViewProps {
  title: string;
  description: string;
  inviteUrl: string;
  accessCode: string | null;
  onCopied: (kind: "link" | "code") => void;
  onDone: () => void;
}

/** Shows the invite link and access code once, with copy buttons. */
export function InviteSecretsView({ title, description, inviteUrl, accessCode, onCopied, onDone }: InviteSecretsViewProps) {
  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-full bg-success-soft text-success" aria-hidden="true">
            <Check className="size-4" />
          </span>
          {title}
        </DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <DialogBody className="grid gap-5">
        <Callout tone="warning" icon={<AlertTriangle />} title="Copy these now. They won't be shown again.">
          For the candidate's security only a fingerprint is stored. If they're lost, generate a new link from the job's pipeline. Send the access code through a different channel than the link.
        </Callout>
        <SecretField label="Invite link" value={inviteUrl} testId="invite-link" onCopied={() => onCopied("link")} />
        {accessCode ? (
          <div className="grid gap-1.5">
            <SecretField label="Access code" value={accessCode} testId="invite-access-code" emphasis onCopied={() => onCopied("code")} />
            <p className="flex items-center gap-1.5 text-xs text-fg-muted">
              <KeyRound className="size-3.5" aria-hidden="true" />
              The candidate enters this with their email after opening the link.
            </p>
          </div>
        ) : (
          <p className="text-xs text-fg-muted">No access code: the candidate confirms their email address to start.</p>
        )}
      </DialogBody>
      <DialogFooter>
        <Button onClick={onDone}>Done</Button>
      </DialogFooter>
    </>
  );
}
