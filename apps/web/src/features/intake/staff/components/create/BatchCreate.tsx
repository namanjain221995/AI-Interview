import { AlertTriangle, Check, CircleAlert, Copy, Download, FileUp, ListPlus, Save, TableProperties } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Textarea } from "@/components/ui/Textarea";
import { isApiError } from "@/lib/api";
import { formatDateTime, pluralize } from "@/lib/format";

import { FieldShell, SectionBlock } from "@/features/recruiter/admin/components/Field";
import { copyText } from "@/features/recruiter/admin/lib/utils";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";

import { useBatchCreateIntake } from "../../api";
import { TARGET_ROLE_LABELS } from "../../labels";
import { BATCH_MAX_ROWS, downloadText, linksCsv, mergeBatchResults, parseBatch, type BatchResultRow } from "../../lib/batch";
import { batchCreatePayload, validateSettings, type FormErrors, type InviteSettingsForm } from "../../lib/createForm";
import type { IntakeConfig } from "../../types";
import type { ReviewerOption } from "../reviewers";
import { InviteSettingsFields } from "./InviteSettingsFields";
import { useLeaveGuard } from "./useLeaveGuard";

const SAMPLE = "Asha Rao, asha.rao@example.com, AI/ML\nBilal Khan, bilal.khan@example.com\nChen Li, chen.li@example.com, Data analyst";

export interface BatchCreateProps {
  config: IntakeConfig | undefined;
  configLoading: boolean;
  orgRequiresCode: boolean;
  reviewers: ReviewerOption[];
  settings: InviteSettingsForm;
  onSettingsChange: (settings: InviteSettingsForm) => void;
}

interface BatchOutcome {
  batchId: string;
  rows: BatchResultRow[];
  sent: boolean;
}

/** Paste or CSV, preview with per-row checks, shared settings, then shown-once results. */
export function BatchCreate({ config, configLoading, orgRequiresCode, reviewers, settings, onSettingsChange }: BatchCreateProps) {
  const [text, setText] = useState("");
  const parsed = useMemo(() => parseBatch(text), [text]);
  const jobs = config?.jobs ?? [];
  const [jobId, setJobId] = useState<string | null>(jobs.length === 1 ? jobs[0]!.job_id : null);
  const [errors, setErrors] = useState<FormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<BatchOutcome | null>(null);
  const [saved, setSaved] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const batch = useBatchCreateIntake();

  const createdWithLinks = outcome?.rows.filter((r) => r.invite_url).length ?? 0;
  const guard = useLeaveGuard(Boolean(outcome) && createdWithLinks > 0 && !saved, {
    title: "Leave without saving the links?",
    description: "These invite links and access codes can't be shown again. Download them as a CSV first.",
  });

  const effectiveJob = jobId ?? (jobs.length === 1 ? jobs[0]!.job_id : null);
  const roundId = jobs.find((j) => j.job_id === effectiveJob)?.round_id ?? null;

  const readFile = async (file: File) => {
    if (file.size > 1024 * 1024) {
      setFormError("That file is larger than 1 MB. Paste the rows instead, or split the file.");
      return;
    }
    setText((await file.text()).trim());
    setFormError(null);
  };

  const submit = async (send: boolean) => {
    setFormError(null);
    const found: FormErrors = send ? validateSettings(settings) : {};
    if (!effectiveJob) found.job_id = "Choose the program's job.";
    setErrors(found);
    if (parsed.problems.length) {
      setFormError(parsed.problems[0]!);
      return;
    }
    if (parsed.valid.length === 0) {
      setFormError("Add at least one row with a name and a valid email.");
      return;
    }
    if (Object.keys(found).length) {
      setFormError("Check the highlighted fields.");
      return;
    }
    if (parsed.invalid.length) {
      const ok = await confirm({
        title: `Leave out ${pluralize(parsed.invalid.length, "row")} with problems?`,
        description: `${pluralize(parsed.valid.length, "invitation")} will be created. Fix the other rows and add them in another batch.`,
        confirmLabel: send ? `Create ${pluralize(parsed.valid.length, "invitation")}` : `Save ${pluralize(parsed.valid.length, "draft")}`,
      });
      if (!ok) return;
    }
    const payload = batchCreatePayload(effectiveJob!, roundId, settings, parsed.valid, send);
    try {
      const response = await batch.mutateAsync(payload);
      setOutcome({ batchId: response.batch_id, rows: mergeBatchResults(payload.rows, response.created, response.failed), sent: send });
      setSaved(false);
    } catch (error) {
      const code = isApiError(error) ? error.code : undefined;
      setFormError(
        isApiError(error) && error.isRateLimited
          ? "Too many batches in a minute. Wait a moment and try again."
          : code === "batch_too_large"
            ? `A batch holds up to ${BATCH_MAX_ROWS} rows. Split it into smaller batches.`
            : code === "job_not_open"
              ? "This job isn't open. Open the job, or choose another one."
              : code === "invalid_reviewer"
                ? "The assigned reviewer isn't an active staff member of this organisation. Choose someone else."
                : code === "intake_kit_unavailable"
                  ? "The intake kit couldn't be loaded on the server. Try again in a moment, or ask an administrator."
                  : describeFailure(error),
      );
    }
  };

  const download = () => {
    if (!outcome) return;
    downloadText(`intake-batch-${outcome.batchId.slice(0, 8) || "links"}.csv`, linksCsv(outcome.rows));
    setSaved(true);
  };

  const reset = async () => {
    if (outcome && createdWithLinks > 0 && !saved) {
      const ok = await confirm({ title: "Start over without the links?", description: "These links can't be shown again.", confirmLabel: "Start over", cancelLabel: "Go back", tone: "danger" });
      if (!ok) return;
    }
    setOutcome(null);
    setText("");
    setSaved(false);
  };

  if (outcome) {
    const created = outcome.rows.filter((r) => r.status === "created").length;
    const failed = outcome.rows.length - created;
    return (
      <Card className="grid grid-cols-1 gap-5 p-5 sm:p-6" data-testid="intake-batch-results">
        <div className="grid gap-1">
          <h2 className="text-[17px] leading-6 font-semibold text-fg">{outcome.sent ? `${pluralize(created, "invitation")} created` : `${pluralize(created, "draft")} saved`}</h2>
          <p className="text-sm text-fg-muted">
            {failed ? `${pluralize(failed, "row")} couldn't be created; the reasons are listed below. ` : ""}
            {outcome.sent ? "Each candidate gets their own link." : "Send the drafts from the intake list when you're ready."}
          </p>
        </div>
        {outcome.sent && createdWithLinks > 0 ? (
          <Callout tone="warning" icon={<AlertTriangle />} title="Download the links now. They won't be shown again.">
            The CSV holds every link and access code from this batch. Send each access code separately from its link.
          </Callout>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {createdWithLinks > 0 ? (
            <Button leftIcon={saved ? <Check /> : <Download />} onClick={download} data-testid="intake-batch-download">
              {saved ? "Links downloaded" : "Download links (CSV)"}
            </Button>
          ) : null}
          {outcome.batchId ? (
            <Button variant="outline" leftIcon={<TableProperties />} asChild>
              <Link to={`/app/intake?batch=${encodeURIComponent(outcome.batchId)}`} onClick={() => guard.allowNext()}>
                View this batch
              </Link>
            </Button>
          ) : null}
          <Button variant="outline" leftIcon={<ListPlus />} onClick={() => void reset()}>
            Start a new batch
          </Button>
        </div>
        <Table>
          <caption className="sr-only">Batch results</caption>
          <TableHeader>
            <tr>
              <TableHead align="right">Row</TableHead>
              <TableHead>Candidate</TableHead>
              <TableHead>Result</TableHead>
              <TableHead className="max-md:hidden">Link</TableHead>
            </tr>
          </TableHeader>
          <TableBody>
            {outcome.rows.map((row) => (
              <TableRow key={row.row} data-testid="intake-batch-row" data-row={row.row} data-status={row.status}>
                <TableCell numeric className="text-fg-subtle tabular-nums">
                  {row.row}
                </TableCell>
                <TableCell>
                  <span className="grid min-w-[10rem]">
                    <span className="truncate font-medium text-fg">{row.full_name}</span>
                    <span className="truncate text-xs text-fg-muted">{row.email}</span>
                  </span>
                </TableCell>
                <TableCell>
                  {row.status === "created" ? (
                    <span className="grid gap-0.5">
                      <Badge tone="success" size="sm" dot>
                        {outcome.sent ? "Invited" : "Draft saved"}
                      </Badge>
                      {row.message ? <span className="text-xs text-fg-subtle">{row.message}</span> : null}
                      {row.access_code ? <span className="font-mono text-xs text-fg-muted">Code {row.access_code}</span> : null}
                    </span>
                  ) : (
                    <span className="grid max-w-[18rem] gap-0.5">
                      <Badge tone="warning" size="sm" dot>
                        Not created
                      </Badge>
                      <span className="text-xs text-fg-muted">{row.message}</span>
                    </span>
                  )}
                </TableCell>
                <TableCell className="max-md:hidden">{row.invite_url ? <RowLinkCopy url={row.invite_url} expiresAt={row.expires_at} /> : <span className="text-fg-subtle">—</span>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-5">
      <SectionBlock
        title="Candidates"
        description={`One per line: name, email and an optional target role. Up to ${BATCH_MAX_ROWS} rows; a header row is fine.`}
        actions={
          <>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.txt,text/csv,text/plain"
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void readFile(file);
                e.target.value = "";
              }}
            />
            <Button variant="outline" size="sm" leftIcon={<FileUp />} onClick={() => fileInput.current?.click()}>
              Upload a CSV
            </Button>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-4">
          <Textarea
            data-testid="intake-batch-input"
            aria-label="Candidates, one per line"
            rows={6}
            autoResize
            maxRows={14}
            spellCheck={false}
            className="font-mono text-[13px]"
            placeholder={SAMPLE}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {parsed.rows.length ? (
            <div className="grid grid-cols-1 gap-2" data-testid="intake-batch-preview">
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]" aria-live="polite">
                <span className="font-medium text-fg">{pluralize(parsed.valid.length, "row")} ready</span>
                {parsed.invalid.length ? (
                  <span className="inline-flex items-center gap-1 font-medium text-warning-fg">
                    <CircleAlert className="size-3.5" aria-hidden="true" />
                    {pluralize(parsed.invalid.length, "row")} to fix
                  </span>
                ) : null}
                {parsed.header ? <span className="text-fg-subtle">Header row detected</span> : null}
              </p>
              {parsed.problems.map((p) => (
                <Callout key={p} tone="danger">
                  {p}
                </Callout>
              ))}
              <div className="max-h-80 overflow-auto rounded-md border border-line scrollbar-thin">
                <Table>
                  <caption className="sr-only">Batch preview</caption>
                  <TableHeader>
                    <tr>
                      <TableHead align="right">Row</TableHead>
                      <TableHead>Name</TableHead>
                      <TableHead>Email</TableHead>
                      <TableHead className="max-sm:hidden">Target role</TableHead>
                      <TableHead>Check</TableHead>
                    </tr>
                  </TableHeader>
                  <TableBody>
                    {parsed.rows.map((row) => (
                      <TableRow key={row.row} data-testid="intake-batch-row" data-row={row.row} data-status={row.error ? "invalid" : "valid"}>
                        <TableCell numeric className="text-fg-subtle tabular-nums">
                          {row.row}
                        </TableCell>
                        <TableCell>
                          <span className="block max-w-[12rem] truncate">{row.full_name || <span className="text-fg-subtle">—</span>}</span>
                        </TableCell>
                        <TableCell>
                          <span className="block max-w-[14rem] truncate">{row.email || <span className="text-fg-subtle">—</span>}</span>
                        </TableCell>
                        <TableCell className="max-sm:hidden">{row.target_role ? TARGET_ROLE_LABELS[row.target_role] : <span className="text-fg-subtle">Candidate chooses</span>}</TableCell>
                        <TableCell>
                          {row.error ? (
                            <span className="block max-w-[16rem] text-xs font-medium text-warning-fg">{row.error}</span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-xs text-success-fg">
                              <Check className="size-3.5" aria-hidden="true" />
                              Ready
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          ) : null}
        </div>
      </SectionBlock>

      <SectionBlock title="Program" description="Every row joins the same job's intake round. Rows without a target role let the candidate choose.">
        {configLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : jobs.length === 0 ? (
          <Callout tone="info" title="No job has an intake round yet">
            Add an Intake interview round to a job, then come back. <Link to="/app/jobs">Go to jobs</Link>
          </Callout>
        ) : (
          <FieldShell label="Job" required error={errors.job_id} className="sm:max-w-sm">
            {(control) => (
              <Select<string>
                id={control.id}
                data-testid="intake-job-select"
                placeholder="Choose a job"
                invalid={Boolean(errors.job_id)}
                value={effectiveJob}
                onValueChange={setJobId}
                options={jobs.map((j) => ({ value: j.job_id, label: j.title, description: j.round_name ?? undefined }))}
              />
            )}
          </FieldShell>
        )}
      </SectionBlock>

      <InviteSettingsFields value={settings} onChange={onSettingsChange} errors={errors} orgWorkAuthorization={config?.work_authorization_default ?? null} orgRequiresCode={orgRequiresCode} reviewers={reviewers} />

      <div className="sticky bottom-0 z-10 -mx-4 border-t border-line-subtle bg-surface/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-lg sm:border sm:px-5">
        {formError ? (
          <p className="mb-2 text-[13px] font-medium text-danger-fg" role="alert">
            {formError}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="outline" leftIcon={<Save />} onClick={() => void submit(false)} disabled={batch.isPending || parsed.valid.length === 0}>
            Save as drafts
          </Button>
          <Button onClick={() => void submit(true)} loading={batch.isPending} loadingText="Creating invitations…" disabled={parsed.valid.length === 0} data-testid="intake-batch-submit">
            {parsed.valid.length ? `Create ${pluralize(parsed.valid.length, "invitation")}` : "Create invitations"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function RowLinkCopy({ url, expiresAt }: { url: string; expiresAt: string | null }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="flex min-w-0 items-center gap-2">
      <code className="block max-w-[16rem] truncate font-mono text-xs text-fg-muted" title={url}>
        {url}
      </code>
      <Button
        variant="ghost"
        size="xs"
        leftIcon={copied ? <Check /> : <Copy />}
        aria-label={copied ? "Link copied" : "Copy link"}
        onClick={async () => {
          if (await copyText(url)) setCopied(true);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
      {expiresAt ? <span className="sr-only">Valid until {formatDateTime(expiresAt)}</span> : null}
    </span>
  );
}
