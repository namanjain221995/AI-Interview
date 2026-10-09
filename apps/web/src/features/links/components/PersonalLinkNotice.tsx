import { Check, Copy, KeyRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { cn } from "@/lib/utils";

import { copyText } from "@/features/recruiter/admin/lib/utils";

export interface PersonalLinkNoticeProps {
  url: string;
  organizationName: string;
  onContinue: () => void;
}

/**
 * "Save your personal link": shown once after registration. The link is the only
 * way back from another device, so Continue unlocks after Copy or "I've saved it".
 */
export function PersonalLinkNotice({ url, organizationName, onContinue }: PersonalLinkNoticeProps) {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => headingRef.current?.focus(), []);

  const copy = async () => {
    const ok = await copyText(url);
    setCopied(ok);
    setCopyFailed(!ok);
  };
  const ready = copied || saved;

  return (
    <div className="grid grid-cols-1 gap-6" data-testid="personal-link-notice">
      <div>
        <h1 ref={headingRef} tabIndex={-1} className="text-title text-fg outline-none sm:text-[1.75rem] sm:leading-9">
          You're registered. Save your personal link.
        </h1>
        <p className="mt-2 text-[15px] leading-6 text-fg-muted">
          This link brings you back to your interview with {organizationName}, on this device or any other. It's shown only once, so keep it somewhere safe.
        </p>
      </div>

      <section aria-label="Your personal link" className="overflow-hidden rounded-xl border border-line-strong bg-surface shadow-card">
        <div className="flex items-center gap-2 border-b border-dashed border-line-strong bg-surface-subtle px-4 py-2.5 text-[13px] font-medium text-fg-muted">
          <KeyRound className="size-4 text-fg-subtle" aria-hidden="true" />
          Personal link, only for you
        </div>
        <div className="grid grid-cols-1 gap-4 p-4 sm:p-5">
          <code data-testid="personal-link-url" className="block rounded-md bg-surface-sunken/70 px-3 py-3 font-mono text-[13px] leading-5 break-all text-fg select-all">
            {url}
          </code>
          <Button
            size="lg"
            variant={copied ? "outline" : "primary"}
            leftIcon={copied ? <Check className="text-success" /> : <Copy />}
            onClick={() => void copy()}
            data-testid="personal-link-copy"
            fullWidth
          >
            {copied ? "Copied" : "Copy link"}
          </Button>
          {copyFailed ? <p className="text-xs text-warning-fg">Your browser blocked copying. Select the link above and copy it, or write it down.</p> : null}
          <p className="text-[13px] leading-5 text-fg-muted">Paste it into your notes, or send it to yourself. Anyone with this link and your email can open your interview, so don't share it.</p>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4">
        <Checkbox data-testid="personal-link-saved" checked={saved} onCheckedChange={(checked) => setSaved(checked === true)} label="I've saved my personal link" />
        <Button size="lg" fullWidth disabled={!ready} onClick={onContinue} data-testid="personal-link-continue" className={cn(!ready && "cursor-not-allowed")}>
          Continue to your interview
        </Button>
        {!ready ? <p className="text-center text-xs text-fg-muted">Copy the link or confirm you've saved it to continue.</p> : null}
      </div>
    </div>
  );
}
