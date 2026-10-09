import { ChevronLeft, ChevronRight, Download, FastForward, Flag, MoreHorizontal, Search, UsersRound, X } from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Textarea } from "@/components/ui/Textarea";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatNumber, formatRelativeOrDate } from "@/lib/format";
import { useDebounce } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { SecretField } from "@/features/recruiter/admin/components/Controls";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";

import { exportRegistrations, useCancelRegistration, useRegistrations, useReissuePersonalLink, useUnblockRegistration } from "../api";
import { FLAG_LABELS, labelOr, REGISTRATION_STATUS_LABELS, REGISTRATION_STATUS_TONES, ROUND_STATE_LABELS, ROUND_STATE_TONES, STATUS_REASON_LABELS } from "../status";
import { REGISTRATION_STATUSES, type OpenLink, type Registration, type RegistrationListQuery, type RegistrationStatus } from "../types";
import { AdvanceDialog } from "./AdvanceDialog";
import { ListLoadError } from "./LinksTable";
import { availableActions, RegistrationDetailSheet, type RegistrationAction } from "./RegistrationDetailSheet";
import { RetakeDialog } from "./RetakeDialog";

const PAGE_SIZE = 50;
const ALL = "__all__";
const INTERACTIVE = "a,button,input,select,textarea,label,[role='menuitem'],[role='checkbox']";

function RoundChips({ registration }: { registration: Registration }) {
  return (
    <ol className="flex flex-wrap gap-1" aria-label="Rounds">
      {registration.rounds.map((round, i) => {
        const label = `Round ${i + 1}, ${round.name}: ${ROUND_STATE_LABELS[round.state]}${round.attempt > 1 ? `, attempt ${round.attempt}` : ""}`;
        const chip = (
          <Badge tone={ROUND_STATE_TONES[round.state]} size="sm" className="tabular-nums">
            <span className="font-semibold">{i + 1}</span>
            <span className="ml-1 max-xl:sr-only">{ROUND_STATE_LABELS[round.state]}</span>
          </Badge>
        );
        return (
          <li key={`${round.sequence}-${round.attempt}`} className="min-w-0">
            {round.state === "completed" && round.session_id ? (
              <Tooltip content={label}>
                <Link to={`/app/sessions/${round.session_id}`} aria-label={label} className="rounded-sm">
                  {chip}
                </Link>
              </Tooltip>
            ) : (
              <Tooltip content={label}>
                <span tabIndex={0} aria-label={label} className="rounded-sm">
                  {chip}
                </span>
              </Tooltip>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function FlagsCell({ registration }: { registration: Registration }) {
  if (!registration.flags.length) return <span className="text-fg-subtle">0</span>;
  const high = registration.flags.some((f) => f.severity === "high");
  const reasons = registration.flags.map((f) => `${labelOr(FLAG_LABELS, f.code)}: ${f.message}`).join("\n");
  const reviewed = registration.flag_review === "cleared" ? " (reviewed: not a duplicate)" : registration.flag_review === "confirmed" ? " (reviewed: likely duplicate)" : "";
  return (
    <Tooltip content={<span className="whitespace-pre-line">{reasons + reviewed}</span>}>
      <span tabIndex={0} className="inline-flex rounded-sm">
        <Badge tone={high && registration.flag_review === "open" ? "warning" : "neutral"} size="sm" icon={<Flag />} data-testid="registration-flags">
          {registration.flags.length}
        </Badge>
      </span>
    </Tooltip>
  );
}

/** Reason prompt for cancel (optional) and unblock (required). */
function ReasonDialog({
  open,
  title,
  description,
  confirmLabel,
  required,
  tone,
  testId,
  pending,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  required: boolean;
  tone: "danger" | "primary";
  testId: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setReason("");
      setError(null);
    }
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-1.5">
          <Label htmlFor={`${testId}-reason`} required={required} optional={!required}>
            Reason
          </Label>
          <Textarea id={`${testId}-reason`} value={reason} maxLength={500} rows={3} onChange={(e) => setReason(e.target.value)} aria-invalid={error ? true : undefined} />
          {error ? <p className="text-xs font-medium text-danger-fg">{error}</p> : <p className="text-xs text-fg-muted">Recorded in the audit log.</p>}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Back
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            loading={pending}
            data-testid={`${testId}-confirm`}
            onClick={() => {
              if (required && reason.trim().length < 3) {
                setError("Write a short reason.");
                return;
              }
              onConfirm(reason.trim());
            }}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PersonalUrlDialog({ value, onClose }: { value: { name: string; url: string } | null; onClose: () => void }) {
  return (
    <Dialog open={Boolean(value)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>New personal link for {value?.name}</DialogTitle>
          <DialogDescription>Send it to the candidate yourself. It's shown only now, and their previous personal link no longer works.</DialogDescription>
        </DialogHeader>
        <DialogBody>{value ? <SecretField label="Personal link" value={value.url} testId="registration-personal-url" /> : null}</DialogBody>
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export interface RegistrationsTableProps {
  link: OpenLink;
  canManage: boolean;
  canReviewFlags: boolean;
}

/** Registrations tab: filters, export, bulk advance, per-row actions and the detail sheet. */
export function RegistrationsTable({ link, canManage, canReviewFlags }: RegistrationsTableProps) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<RegistrationStatus | undefined>();
  const [roundIndex, setRoundIndex] = useState<number | undefined>();
  const [needsReview, setNeedsReview] = useState(false);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState<Registration[] | null>(null);
  const [retaking, setRetaking] = useState<Registration | null>(null);
  const [cancelling, setCancelling] = useState<Registration | null>(null);
  const [unblocking, setUnblocking] = useState<Registration | null>(null);
  const [personalUrl, setPersonalUrl] = useState<{ name: string; url: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const q = useDebounce(search.trim(), 300);

  const params = useMemo<RegistrationListQuery>(
    () => ({ q: q || undefined, status, round_index: roundIndex, needs_review: needsReview || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [q, status, roundIndex, needsReview, page],
  );
  const query = useRegistrations(link.id, params);
  const rows = query.data?.items;
  const total = query.data?.total ?? 0;
  useEffect(() => {
    setSelected(new Set());
    setPage((p) => (p === 0 ? p : 0));
  }, [q, status, roundIndex, needsReview]);

  const cancel = useCancelRegistration(link.id);
  const unblock = useUnblockRegistration(link.id);
  const reissue = useReissuePersonalLink(link.id);

  const selectedRows = (rows ?? []).filter((r) => selected.has(r.id));
  const advanceable = selectedRows.filter((r) => r.status === "awaiting_review");
  const allOnPage = Boolean(rows?.length) && rows!.every((r) => selected.has(r.id));
  const filtered = Boolean(q || status || roundIndex !== undefined || needsReview);

  const toggle = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const act = async (action: RegistrationAction, reg: Registration) => {
    if (action === "advance") setAdvancing([reg]);
    else if (action === "retake") setRetaking(reg);
    else if (action === "cancel") setCancelling(reg);
    else if (action === "unblock") setUnblocking(reg);
    else if (action === "reissue") {
      const ok = await confirm({
        title: `New personal link for ${reg.full_name}?`,
        description: "Their current personal link stops working. You'll see the new one once, to send it to them.",
        confirmLabel: "Make new link",
      });
      if (!ok) return;
      try {
        const result = await reissue.mutateAsync(reg.id);
        setPersonalUrl({ name: reg.full_name, url: result.personal_url });
      } catch {
        // toasted by the mutation cache
      }
    }
  };

  const doExport = async () => {
    setExporting(true);
    try {
      const name = await exportRegistrations(link.id, { status, needs_review: needsReview || undefined });
      toast.success("Export downloaded", { description: name });
    } catch (error) {
      toast.error("Couldn't export", { description: describeFailure(error) });
    } finally {
      setExporting(false);
    }
  };

  const activate = (event: MouseEvent<HTMLTableRowElement> | KeyboardEvent<HTMLTableRowElement>, reg: Registration) => {
    const target = event.target as HTMLElement;
    if (target !== event.currentTarget && target.closest(INTERACTIVE)) return;
    setOpenId(reg.id);
  };
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const statusOptions = [{ value: ALL, label: "All statuses" }, ...REGISTRATION_STATUSES.map((s) => ({ value: s, label: REGISTRATION_STATUS_LABELS[s] }))];
  const roundOptions = [{ value: ALL, label: "Any round" }, ...link.rounds.map((r, i) => ({ value: String(i), label: `Current: ${i + 1}. ${r.name}` }))];

  return (
    <div className="grid grid-cols-1" data-testid="registrations-table">
      <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
        <Input
          type="text"
          role="searchbox"
          inputMode="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name, email or phone"
          aria-label="Search registrations"
          data-testid="registrations-search"
          leading={<Search />}
          trailing={
            search ? (
              <button type="button" aria-label="Clear search" onClick={() => setSearch("")} className="flex size-6 items-center justify-center rounded-sm text-fg-subtle hover:bg-surface-hover hover:text-fg">
                <X className="size-3.5" />
              </button>
            ) : null
          }
          wrapperClassName="w-full sm:w-64"
        />
        <Select
          aria-label="Filter by status"
          data-testid="registrations-status-filter"
          value={status ?? ALL}
          onValueChange={(v) => setStatus(v === ALL ? undefined : (v as RegistrationStatus))}
          options={statusOptions}
          className="w-auto min-w-36 max-sm:flex-1"
        />
        {link.rounds.length > 1 ? (
          <Select
            aria-label="Filter by current round"
            data-testid="registrations-round-filter"
            value={roundIndex === undefined ? ALL : String(roundIndex)}
            onValueChange={(v) => setRoundIndex(v === ALL ? undefined : Number(v))}
            options={roundOptions}
            className="w-auto min-w-36 max-sm:flex-1"
          />
        ) : null}
        <Switch data-testid="registrations-needs-review" checked={needsReview} onCheckedChange={setNeedsReview} label="Needs review" size="sm" />
        <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
          {canManage ? (
            <Button
              size="sm"
              leftIcon={<FastForward />}
              disabled={advanceable.length === 0}
              onClick={() => setAdvancing(selectedRows)}
              data-testid="registrations-advance-button"
              title={advanceable.length === 0 ? "Select people who are awaiting review" : undefined}
            >
              Advance{advanceable.length ? ` ${advanceable.length}` : ""}
            </Button>
          ) : null}
          {canManage ? (
            <Button size="sm" variant="outline" leftIcon={<Download />} onClick={() => void doExport()} loading={exporting} data-testid="registrations-export">
              Export CSV
            </Button>
          ) : null}
        </div>
      </div>

      {query.error && !rows?.length ? (
        <div className="p-5">
          <ListLoadError error={query.error} onRetry={() => void query.refetch()} title="Couldn't load registrations" />
        </div>
      ) : (
        <Table aria-busy={query.isLoading || query.isFetching || undefined} className={cn("transition-opacity", query.isFetching && !query.isLoading && "opacity-60")}>
          <caption className="sr-only">Registrations</caption>
          <TableHeader>
            <tr>
              {canManage ? (
                <TableHead className="w-10">
                  <Checkbox
                    aria-label="Select every registration on this page"
                    checked={allOnPage ? true : selectedRows.length ? "indeterminate" : false}
                    onCheckedChange={(on) => setSelected(on === true ? new Set((rows ?? []).map((r) => r.id)) : new Set())}
                    disabled={!rows?.length}
                  />
                </TableHead>
              ) : null}
              <TableHead>Candidate</TableHead>
              <TableHead className="max-md:hidden">Registered</TableHead>
              <TableHead>Rounds</TableHead>
              <TableHead className="max-sm:hidden">Status</TableHead>
              <TableHead align="right" className="max-sm:hidden">
                Signals
              </TableHead>
              <TableHead className="w-10">
                <span className="sr-only">Actions</span>
              </TableHead>
            </tr>
          </TableHeader>
          <TableBody>
            {query.isLoading && !rows?.length
              ? Array.from({ length: 5 }, (_, i) => (
                  <TableRow key={`s-${i}`}>
                    <TableCell colSpan={canManage ? 7 : 6}>
                      <Skeleton className="h-8 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              : (rows ?? []).map((reg) => {
                  const actions = canManage ? availableActions(reg) : [];
                  return (
                    <TableRow
                      key={reg.id}
                      data-testid="registration-row"
                      data-registration-id={reg.id}
                      data-status={reg.status}
                      interactive
                      selected={selected.has(reg.id)}
                      tabIndex={0}
                      aria-label={`Open the registration of ${reg.full_name}`}
                      onClick={(e) => activate(e, reg)}
                      onKeyDown={(e) => {
                        if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
                          e.preventDefault();
                          activate(e, reg);
                        }
                      }}
                    >
                      {canManage ? (
                        <TableCell>
                          <Checkbox data-testid={`registration-select-${reg.id}`} aria-label={`Select ${reg.full_name}`} checked={selected.has(reg.id)} onCheckedChange={(on) => toggle(reg.id, on === true)} />
                        </TableCell>
                      ) : null}
                      <TableCell>
                        <span className="grid min-w-[11rem] max-w-[18rem]">
                          {reg.candidate ? (
                            <Link to={`/app/candidates/${reg.candidate.id}`} className="truncate font-medium text-fg hover:underline">
                              {reg.full_name}
                            </Link>
                          ) : (
                            <span className="truncate font-medium text-fg">{reg.full_name}</span>
                          )}
                          <span className="truncate text-xs text-fg-muted">{reg.email}</span>
                          {reg.phone ? <span className="truncate text-xs text-fg-muted">{reg.phone}</span> : null}
                          <span className="mt-1 sm:hidden">
                            <Badge tone={REGISTRATION_STATUS_TONES[reg.status]} size="sm">
                              {REGISTRATION_STATUS_LABELS[reg.status]}
                            </Badge>
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-fg-muted max-md:hidden">{formatRelativeOrDate(reg.created_at)}</TableCell>
                      <TableCell>
                        <RoundChips registration={reg} />
                      </TableCell>
                      <TableCell className="max-sm:hidden">
                        <span className="grid justify-items-start gap-0.5">
                          <Badge tone={REGISTRATION_STATUS_TONES[reg.status]} size="sm" dot>
                            {REGISTRATION_STATUS_LABELS[reg.status]}
                          </Badge>
                          {reg.status_reason ? <span className="max-w-[12rem] truncate text-xs text-fg-muted">{labelOr(STATUS_REASON_LABELS, reg.status_reason)}</span> : null}
                        </span>
                      </TableCell>
                      <TableCell numeric className="max-sm:hidden">
                        <FlagsCell registration={reg} />
                      </TableCell>
                      <TableCell>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Actions for ${reg.full_name}`} data-testid="registration-actions">
                              <MoreHorizontal />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={() => setOpenId(reg.id)}>View details</DropdownMenuItem>
                            {actions.length ? <DropdownMenuSeparator /> : null}
                            {actions.includes("advance") ? (
                              <DropdownMenuItem onSelect={() => void act("advance", reg)} data-testid="registration-advance">
                                Advance to next round
                              </DropdownMenuItem>
                            ) : null}
                            {actions.includes("unblock") ? (
                              <DropdownMenuItem onSelect={() => void act("unblock", reg)} data-testid="registration-unblock">
                                Allow registration
                              </DropdownMenuItem>
                            ) : null}
                            {actions.includes("retake") ? (
                              <DropdownMenuItem onSelect={() => void act("retake", reg)} data-testid="registration-retake">
                                Allow another attempt
                              </DropdownMenuItem>
                            ) : null}
                            {actions.includes("reissue") ? (
                              <DropdownMenuItem onSelect={() => void act("reissue", reg)} data-testid="registration-reissue-link">
                                New personal link
                              </DropdownMenuItem>
                            ) : null}
                            {actions.includes("cancel") ? (
                              <DropdownMenuItem destructive onSelect={() => void act("cancel", reg)} data-testid="registration-cancel">
                                Cancel registration
                              </DropdownMenuItem>
                            ) : null}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  );
                })}
          </TableBody>
        </Table>
      )}
      {!query.isLoading && !query.error && !rows?.length ? (
        <div className="border-t border-line-subtle">
          {filtered ? (
            <EmptyState size="sm" icon={<UsersRound />} title="Nobody matches" description="Try another search or filter." />
          ) : (
            <EmptyState icon={<UsersRound />} title="No registrations yet" description="Share the link from the Share tab. Registrations appear here as people sign up." />
          )}
        </div>
      ) : null}
      {total > PAGE_SIZE ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle px-5 py-3 text-[13px] text-fg-muted">
          <p className="tabular-nums">
            {formatNumber(page * PAGE_SIZE + 1)}–{formatNumber(Math.min((page + 1) * PAGE_SIZE, total))} of {formatNumber(total)}
          </p>
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}>
              <ChevronLeft />
            </Button>
            <span className="tabular-nums">
              {page + 1} / {pageCount}
            </span>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Next page" disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>
              <ChevronRight />
            </Button>
          </div>
        </div>
      ) : null}

      <RegistrationDetailSheet
        linkId={link.id}
        registrationId={openId}
        onOpenChange={(open) => !open && setOpenId(null)}
        canManage={canManage}
        canReviewFlags={canReviewFlags}
        onAction={(action, reg) => void act(action, reg)}
      />
      <AdvanceDialog
        linkId={link.id}
        registrations={advancing ?? []}
        open={Boolean(advancing)}
        onOpenChange={(open) => !open && setAdvancing(null)}
        onDone={() => setSelected(new Set())}
      />
      <RetakeDialog linkId={link.id} registration={retaking} onOpenChange={(open) => !open && setRetaking(null)} />
      <ReasonDialog
        open={Boolean(cancelling)}
        title={`Cancel ${cancelling?.full_name ?? "this registration"}?`}
        description="Their open interview is cancelled and their personal link stops working. Finished rounds, reports and recordings stay."
        confirmLabel="Cancel registration"
        required={false}
        tone="danger"
        testId="registration-cancel"
        pending={cancel.isPending}
        onCancel={() => setCancelling(null)}
        onConfirm={async (reason) => {
          try {
            await cancel.mutateAsync({ registrationId: cancelling!.id, reason: reason || undefined });
            toast.success("Registration cancelled");
            setCancelling(null);
          } catch (error) {
            toast.error("Couldn't cancel", { description: describeFailure(error) });
          }
        }}
      />
      <ReasonDialog
        open={Boolean(unblocking)}
        title={`Allow ${unblocking?.full_name ?? "this registration"}?`}
        description="They get their first-round interview and a personal link you'll send them."
        confirmLabel="Allow registration"
        required
        tone="primary"
        testId="registration-unblock"
        pending={unblock.isPending}
        onCancel={() => setUnblocking(null)}
        onConfirm={async (reason) => {
          try {
            const result = await unblock.mutateAsync({ registrationId: unblocking!.id, reason });
            const name = unblocking!.full_name;
            setUnblocking(null);
            if (result.personal_url) setPersonalUrl({ name, url: result.personal_url });
            else toast.success("Registration allowed");
          } catch (error) {
            toast.error("Couldn't allow the registration", { description: describeFailure(error) });
          }
        }}
      />
      <PersonalUrlDialog value={personalUrl} onClose={() => setPersonalUrl(null)} />
    </div>
  );
}
