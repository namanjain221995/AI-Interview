import { Captions, Hand, Mic, PhoneOff, RotateCcw } from "lucide-react";

import { ScoreRing } from "@/components/domain/ScoreRing";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { IconButton } from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import { Progress } from "@/components/ui/Progress";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";

import { Section } from "../Specimen";

export function StageSection() {
  return (
    <Section
      id="stage"
      title="Stage surface"
      description="The interview room runs on a dark, immersive surface. The same components re-theme through tokens; nothing is restyled by hand."
    >
      <div className="theme-stage overflow-hidden rounded-xl border border-line bg-surface-sunken">
        <div className="flex h-12 items-center justify-between border-b border-line px-5">
          <div className="flex items-center gap-3">
            <Badge tone="signal" size="sm" dot pulse>
              Live
            </Badge>
            <span className="text-sm font-medium text-fg">Deep technical</span>
          </div>
          <span className="font-mono text-xs text-fg-muted tabular-nums">18:42 / 30:00</span>
        </div>
        <Progress value={62} size="xs" tone="signal" className="rounded-none" aria-label="Interview time used" />
        <div className="grid gap-6 p-6 lg:grid-cols-[1fr_17rem]">
          <div className="grid content-start gap-5">
            <div className="flex aspect-[16/8] items-center justify-center rounded-lg border border-line bg-surface">
              <div className="text-center">
                <div className="mx-auto flex size-20 items-center justify-center rounded-full border border-line-strong bg-surface-muted">
                  <svg viewBox="0 0 64 64" className="size-10 text-fg-subtle" aria-hidden="true">
                    <circle cx="32" cy="24" r="11" fill="currentColor" />
                    <path d="M12 56c3-11 10.5-16 20-16s17 5 20 16" fill="currentColor" />
                  </svg>
                </div>
                <p className="mt-3 text-sm text-fg-muted">Avatar renders here</p>
              </div>
            </div>
            <div className="rounded-lg border border-line bg-surface px-4 py-3">
              <p className="text-xs font-medium text-accent-fg">Maya</p>
              <p className="mt-1 text-[15px] leading-6 text-fg">What would you change about the failover process if you ran it again today?</p>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <IconButton label="Mute microphone" icon={<Mic />} variant="secondary" size="lg" />
              <IconButton label="Ask to repeat" icon={<RotateCcw />} variant="secondary" size="lg" />
              <IconButton label="Captions" icon={<Captions />} variant="secondary" size="lg" />
              <Button variant="outline" size="lg" leftIcon={<Hand />}>
                I'm done answering
              </Button>
              <Button variant="danger" size="lg" leftIcon={<PhoneOff />}>
                End interview
              </Button>
            </div>
          </div>
          <div className="grid content-start gap-4">
            <Tabs defaultValue="notes" variant="segmented">
              <TabsList aria-label="Side panel">
                <TabsTrigger value="notes">Notes</TabsTrigger>
                <TabsTrigger value="code">Code</TabsTrigger>
              </TabsList>
            </Tabs>
            <Input placeholder="Type an answer instead" aria-label="Typed answer" />
            <Callout tone="warning" title="You left full screen">
              Return to full screen to continue. This was noted for the reviewer.
            </Callout>
            <div className="flex items-center gap-4 rounded-lg border border-line bg-surface p-4">
              <ScoreRing value={7.6} max={10} size="sm" tone="accent" ariaLabel="Example score" />
              <p className="text-xs text-fg-muted">Charts and score visuals keep their contrast on the dark surface.</p>
            </div>
            <div className="flex gap-2">
              <Button className="flex-1">Primary</Button>
              <Button variant="ghost" className="flex-1">
                Ghost
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Section>
  );
}
