import { Check, Copy, ExternalLink, KeyRound, Pause, Play, RefreshCw, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { formatDateTime } from "@/lib/format";

import { SecretField } from "@/features/recruiter/admin/components/Controls";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";
import { copyText } from "@/features/recruiter/admin/lib/utils";

import { useRegenerateToken, useSetAccessCode, useSetLinkStatus } from "../api";
import { QrCode } from "../QrCode";
import { LINK_STATE_LABELS, LINK_STATE_TONES } from "../status";
import type { OpenLink } from "../types";

export function absoluteUrl(url: string): string {
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  return typeof window === "undefined" ? url : `${window.location.origin}${url.startsWith("/") ? "" : "/"}${url}`;
}

/** Shows a freshly issued access code once. */
export function AccessCodeDialog({ code, onOpenChange }: { code: string | null; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={Boolean(code)} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Access code</DialogTitle>
          <DialogDescription>Share it with candidates together with the link. It's shown only now; generate a new one if you lose it.</DialogDescription>
        </DialogHeader>
        <DialogBody>{code ? <SecretField label="Access code" value={code} testId="link-access-code-value" emphasis /> : null}</DialogBody>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CopyUrlField({ url, label = "Interview link" }: { url: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const copy = async () => {
    if (!(await copyText(url))) {
      toast.error("Couldn't copy. Select the link and copy it.");
      return;
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1800);
  };
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
      <Input
        readOnly
        value={url}
        aria-label={label}
        data-testid="link-url"
        className="font-mono text-[13px]"
        onFocus={(e) => e.currentTarget.select()}
      />
      <Button onClick={() => void copy()} leftIcon={copied ? <Check /> : <Copy />} data-testid="link-copy-button">
        {copied ? "Copied" : "Copy link"}
      </Button>
    </div>
  );
}

export interface ShareLinkCardProps {
  link: OpenLink;
  canManage: boolean;
}

/** Share tab: the URL, QR code, pause/resume/close, new link, access code. */
export function ShareLinkCard({ link, canManage }: ShareLinkCardProps) {
  const setStatus = useSetLinkStatus(link.id);
  const regenerate = useRegenerateToken(link.id);
  const accessCode = useSetAccessCode(link.id);
  const [code, setCode] = useState<string | null>(null);
  const url = absoluteUrl(link.url);
  const closed = link.status === "closed";

  const changeStatus = async (status: "active" | "paused" | "closed") => {
    if (status === "closed") {
      const ok = await confirm({
        title: `Close ${link.name}?`,
        description: "Nobody new can register; people already registered can finish. Closing can't be undone.",
        confirmLabel: "Close link",
        tone: "danger",
      });
      if (!ok) return;
    }
    try {
      await setStatus.mutateAsync(status);
      toast.success(status === "paused" ? "Registration paused" : status === "active" ? "Registration resumed" : "Link closed");
    } catch {
      // The mutation cache shows the error.
    }
  };

  const newLink = async () => {
    const ok = await confirm({
      title: "Make a new link?",
      description: "The old link stops working immediately. Share the new one with everyone who hasn't registered yet. People already registered keep their personal links.",
      confirmLabel: "Make new link",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await regenerate.mutateAsync();
      toast.success("New link ready", { description: "The old link no longer works." });
    } catch {
      // toasted
    }
  };

  const changeCode = async (enabled: boolean) => {
    if (!enabled) {
      const ok = await confirm({ title: "Remove the access code?", description: "Anyone with the link can register without a code.", confirmLabel: "Remove code" });
      if (!ok) return;
    } else if (link.requires_access_code) {
      const ok = await confirm({ title: "Replace the access code?", description: "The current code stops working. Share the new code with candidates who haven't registered yet.", confirmLabel: "New code" });
      if (!ok) return;
    }
    try {
      const result = await accessCode.mutateAsync(enabled);
      if (enabled && result.access_code) setCode(result.access_code);
      else toast.success("Access code removed");
    } catch (error) {
      toast.error("Couldn't change the access code", { description: describeFailure(error) });
    }
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <Card>
        <CardHeader
          title="Share the link"
          description="Anyone with this link can register once, upload a résumé and take the interview."
          action={
            <Badge tone={LINK_STATE_TONES[link.state]} dot>
              {LINK_STATE_LABELS[link.state]}
            </Badge>
          }
        />
        <CardContent className="grid grid-cols-1 gap-5">
          <CopyUrlField url={url} />
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline" size="sm" leftIcon={<ExternalLink />}>
              <a href={url} target="_blank" rel="noopener noreferrer">
                Open in new tab
              </a>
            </Button>
          </div>
          {link.requires_access_code ? (
            <p className="flex items-center gap-2 text-sm text-fg-muted">
              <KeyRound className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
              Candidates also need the access code you shared with the link.
            </p>
          ) : null}
          {link.opens_at || link.closes_at ? (
            <p className="text-sm text-fg-muted">
              {link.opens_at ? `Opens ${formatDateTime(link.opens_at)}. ` : ""}
              {link.closes_at ? `Closes ${formatDateTime(link.closes_at)}.` : ""}
            </p>
          ) : null}

          {canManage && !closed ? (
            <div className="grid grid-cols-1 gap-3 border-t border-line-subtle pt-5">
              <p className="text-sm font-medium text-fg">Manage registration</p>
              <div className="flex flex-wrap gap-2">
                {link.status === "active" ? (
                  <Button variant="outline" leftIcon={<Pause />} onClick={() => void changeStatus("paused")} loading={setStatus.isPending} data-testid="link-pause-button">
                    Pause
                  </Button>
                ) : (
                  <Button variant="outline" leftIcon={<Play />} onClick={() => void changeStatus("active")} loading={setStatus.isPending} data-testid="link-resume-button">
                    Resume
                  </Button>
                )}
                <Button variant="outline" leftIcon={<RefreshCw />} onClick={() => void newLink()} loading={regenerate.isPending} data-testid="link-regenerate-button">
                  Make new link
                </Button>
                <Button variant="outline" leftIcon={<KeyRound />} onClick={() => void changeCode(true)} loading={accessCode.isPending} data-testid="link-access-code-toggle">
                  {link.requires_access_code ? "New access code" : "Add access code"}
                </Button>
                {link.requires_access_code ? (
                  <Button variant="ghost" onClick={() => void changeCode(false)} disabled={accessCode.isPending} data-testid="link-access-code-remove">
                    Remove code
                  </Button>
                ) : null}
                <Button variant="ghost" className="text-danger-fg" leftIcon={<XCircle />} onClick={() => void changeStatus("closed")} disabled={setStatus.isPending} data-testid="link-close-button">
                  Close link
                </Button>
              </div>
              <p className="text-xs text-fg-muted">Pausing stops new registrations until you resume. People already registered can always finish.</p>
            </div>
          ) : closed ? (
            <p className="border-t border-line-subtle pt-5 text-sm text-fg-muted">This link is closed. People who registered before it closed can still finish their rounds.</p>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="QR code" description="For posters and slides at a campus drive." />
        <CardContent>{url ? <QrCode value={url} downloadName={`${link.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "interview-link"}-qr`} /> : null}</CardContent>
      </Card>
      <AccessCodeDialog code={code} onOpenChange={(open) => !open && setCode(null)} />
    </div>
  );
}
