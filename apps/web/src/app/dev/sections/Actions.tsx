import {
  Archive,
  Copy,
  Download,
  ExternalLink,
  Filter,
  Link2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  UserPlus,
  XCircle,
} from "lucide-react";
import { useState } from "react";

import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/Dialog";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/DropdownMenu";
import { Form, FormError, FormField, applyServerErrors, useZodForm } from "@/components/ui/Form";
import { IconButton } from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import { Kbd } from "@/components/ui/Kbd";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/Popover";
import { Select } from "@/components/ui/Select";
import { Separator } from "@/components/ui/Separator";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/Sheet";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { toast } from "@/components/ui/Toaster";
import { Tooltip } from "@/components/ui/Tooltip";
import { useConfirm } from "@/components/ui/AlertDialog";
import { ApiError } from "@/lib/api";
import { z } from "zod";

import { JOB_OPTIONS } from "../showcaseData";
import { Row, Section, Specimen } from "../Specimen";

const inviteSchema = z.object({
  email: z.string().trim().min(1, { error: "Enter the candidate's email." }).pipe(z.email({ error: "Enter a valid email address." })),
  job: z.string().min(1, { error: "Choose a job." }),
  expires: z.string(),
  accessCode: z.boolean(),
});

function InviteDialog() {
  const [open, setOpen] = useState(false);
  const form = useZodForm(inviteSchema, { defaultValues: { email: "", job: "", expires: "168", accessCode: false } });

  const onSubmit = async (values: z.output<typeof inviteSchema>) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    if (values.email.endsWith("@taken.test")) {
      applyServerErrors(form, new ApiError({ status: 409, code: "conflict", message: "This candidate already has an open invitation for this job.", details: {} }));
      return;
    }
    setOpen(false);
    form.reset();
    toast.success("Invitation sent", { description: `${values.email} can start any time in the next ${Number(values.expires) / 24} days.` });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button leftIcon={<UserPlus />}>Invite candidate</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite a candidate</DialogTitle>
          <DialogDescription>They get a personal link. You'll see the link once, after sending.</DialogDescription>
        </DialogHeader>
        <Form form={form} onSubmit={onSubmit} className="space-y-0">
          <DialogBody className="grid gap-5">
            <FormError />
            <FormField name="email" label="Candidate email" required description="Try an address ending in @taken.test to see a server error.">
              {(field) => <Input {...field} value={String(field.value ?? "")} type="email" placeholder="name@example.com" autoComplete="off" />}
            </FormField>
            <FormField name="job" label="Job" required>
              {(field, state) => (
                <Select
                  id={field.id}
                  options={JOB_OPTIONS}
                  value={(field.value as string) || null}
                  onValueChange={field.onChange}
                  onBlur={field.onBlur}
                  invalid={state.invalid}
                  placeholder="Choose a job"
                  aria-describedby={field["aria-describedby"]}
                />
              )}
            </FormField>
            <FormField name="expires" label="Link expires after">
              {(field) => (
                <Select
                  id={field.id}
                  value={field.value as string}
                  onValueChange={field.onChange}
                  options={[
                    { value: "48", label: "2 days" },
                    { value: "168", label: "7 days" },
                    { value: "336", label: "14 days" },
                  ]}
                />
              )}
            </FormField>
            <FormField name="accessCode" hideLabel label="Access code">
              {(field) => (
                <Switch
                  checked={Boolean(field.value)}
                  onCheckedChange={field.onChange}
                  label="Require an access code"
                  description="The candidate enters a 6-digit code along with their email."
                />
              )}
            </FormField>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={form.formState.isSubmitting} loadingText="Sending…" leftIcon={<Send />}>
              Send invitation
            </Button>
          </DialogFooter>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

function ProfileSheet() {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline">Open side panel</Button>
      </SheetTrigger>
      <SheetContent size="md">
        <SheetHeader>
          <SheetTitle>Daniel Okafor</SheetTitle>
          <SheetDescription>Senior Platform Engineer, System design round</SheetDescription>
        </SheetHeader>
        <SheetBody className="grid gap-5">
          <div className="flex items-center gap-3">
            <Avatar name="Daniel Okafor" size="lg" />
            <div className="grid gap-0.5">
              <p className="text-sm font-medium text-fg">daniel.okafor@example.com</p>
              <p className="text-xs text-fg-muted">Invited 3 days ago</p>
            </div>
          </div>
          <Separator />
          <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-3 text-sm">
            <dt className="text-fg-muted">Experience</dt>
            <dd className="text-fg">9 years, 6 relevant</dd>
            <dt className="text-fg-muted">Claims to verify</dt>
            <dd className="text-fg">4, 3 verified in interview</dd>
            <dt className="text-fg-muted">Resume</dt>
            <dd>
              <Badge tone="success" dot>
                Ready
              </Badge>
            </dd>
          </dl>
          <Callout tone="neutral" title="Scores support decisions, they don't make them">
            Every number on this profile links to the evidence behind it.
          </Callout>
        </SheetBody>
        <SheetFooter>
          <Button variant="outline">Close</Button>
          <Button>Open report</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function RowActions() {
  const confirm = useConfirm();
  const [showArchived, setShowArchived] = useState(false);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconButton label="Interview actions" icon={<MoreHorizontal />} variant="outline" tooltip={false} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel>Interview</DropdownMenuLabel>
        <DropdownMenuItem>
          <ExternalLink />
          Open session
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => toast("Invite link copied")}>
          <Copy />
          Copy invite link
          <DropdownMenuShortcut>⌘C</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem>
          <RefreshCw />
          Regenerate link
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem checked={showArchived} onCheckedChange={setShowArchived}>
          Show archived rounds
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          destructive
          onSelect={async () => {
            const ok = await confirm({
              title: "Cancel this interview?",
              description: "Daniel's link stops working immediately. You can invite them again later.",
              confirmLabel: "Cancel interview",
              cancelLabel: "Keep interview",
              tone: "danger",
            });
            if (ok) toast.success("Interview cancelled");
          }}
        >
          <XCircle />
          Cancel interview
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function FilterPopover() {
  const [statuses, setStatuses] = useState<string[]>(["live", "completed"]);
  const toggle = (value: string) => setStatuses((prev) => (prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]));
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" leftIcon={<Filter />}>
          Status
          {statuses.length ? (
            <Badge size="sm" tone="neutral">
              {statuses.length}
            </Badge>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-60 p-3">
        <p className="mb-2.5 text-xs font-medium text-fg-muted">Show sessions that are</p>
        <div className="grid gap-2.5">
          {[
            ["live", "Live"],
            ["ready", "Ready to start"],
            ["completed", "Completed"],
            ["aborted", "Ended early"],
          ].map(([value, label]) => (
            <Checkbox key={value} label={label} checked={statuses.includes(value!)} onCheckedChange={() => toggle(value!)} />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ActionsSection() {
  return (
    <Section
      id="actions"
      title="Actions and overlays"
      description="One primary action per view, in ink. Buttons name exactly what happens. Overlays keep focus, close on Escape and return focus to their trigger."
    >
      <Specimen title="Buttons" note="primary, secondary, outline, ghost, danger, link">
        <div className="grid gap-5">
          <Row>
            <Button leftIcon={<Plus />}>Create job</Button>
            <Button variant="secondary">Save draft</Button>
            <Button variant="outline" leftIcon={<Download />}>
              Export
            </Button>
            <Button variant="ghost">Cancel</Button>
            <Button variant="danger" leftIcon={<Trash2 />}>
              Delete template
            </Button>
            <Button variant="link">View transcript</Button>
          </Row>
          <Row>
            <Button size="xs">Extra small</Button>
            <Button size="sm">Small</Button>
            <Button size="md">Medium</Button>
            <Button size="lg">Large</Button>
            <Button size="xl">Start interview</Button>
          </Row>
          <Row>
            <Button loading loadingText="Saving…">
              Save
            </Button>
            <Button variant="outline" loading>
              Regenerate
            </Button>
            <Button disabled>Disabled</Button>
            <span className="mx-1 h-6 w-px bg-line" aria-hidden="true" />
            <IconButton label="Edit" icon={<Pencil />} />
            <IconButton label="Copy link" icon={<Link2 />} variant="outline" />
            <IconButton label="Archive" icon={<Archive />} variant="secondary" />
            <Tooltip content={<span className="flex items-center gap-1.5">Search <Kbd className="border-white/20 bg-white/10 text-current">⌘K</Kbd></span>}>
              <Button variant="outline">Hover for a tooltip</Button>
            </Tooltip>
          </Row>
        </div>
      </Specimen>

      <Specimen title="Overlays" note="Dialog with validated form, side sheet, menu with confirmation, popover filter">
        <Row>
          <InviteDialog />
          <ProfileSheet />
          <RowActions />
          <FilterPopover />
        </Row>
      </Specimen>

      <Specimen title="Feedback" note="Callouts explain what happened and what to do next">
        <div className="grid items-start gap-3 lg:grid-cols-2">
          <Callout tone="info" title="Scores are still being finalised">
            The report regenerates automatically when the last answer is evaluated.
          </Callout>
          <Callout tone="success" title="Report released">
            Daniel can now read his feedback report.
          </Callout>
          <Callout tone="warning" title="Two integrity observations need review">
            They are observations for a person to review, not verdicts.
          </Callout>
          <Callout
            tone="danger"
            title="Resume couldn't be read"
            actions={
              <Button size="xs" variant="outline">
                Upload again
              </Button>
            }
          >
            The PDF is password-protected. Upload an unlocked copy.
          </Callout>
          <Callout tone="signal" title="Interview in progress">
            Priya is 18 minutes into her deep technical round.
          </Callout>
          <Callout tone="neutral" onDismiss={() => toast("Dismissed")}>
            Tip: press <Kbd>⌘</Kbd> <Kbd>K</Kbd> to jump to any page.
          </Callout>
        </div>
        <Row className="mt-5">
          <Button variant="outline" onClick={() => toast.success("Job published", { description: "Candidates can now be invited." })}>
            Success toast
          </Button>
          <Button variant="outline" onClick={() => toast.error("Couldn't save the round", { description: "Request ID 7f3a91c2" })}>
            Error toast
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              toast("Interview archived", {
                action: { label: "Undo", onClick: () => toast.success("Restored") },
              })
            }
          >
            Toast with undo
          </Button>
          <span className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner size="sm" label={null} />
            Generating report
          </span>
        </Row>
      </Specimen>
    </Section>
  );
}
