import { Building2, Code2, Globe2, LayoutTemplate, Shapes } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { DIFFICULTY_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { RoundConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useRoundTypes, useTemplates } from "../api/catalog";
import { SearchInput } from "../components/Controls";
import { defaultRoundConfig, withDefaults } from "./roundConfig";

export interface AddRoundDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (config: RoundConfig, templateId: string | null) => void;
}

interface Choice {
  id: string;
  title: string;
  description: string | null;
  config: RoundConfig;
  templateId: string | null;
  scope: "type" | "global" | "org";
}

/** Start a round from a round-type default or a saved template. */
export function AddRoundDialog({ open, onOpenChange, onPick }: AddRoundDialogProps) {
  const [source, setSource] = useState<"types" | "templates">("types");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const roundTypes = useRoundTypes();
  const templates = useTemplates();

  const choices = useMemo<Choice[]>(() => {
    if (source === "types") {
      const fromServer = (roundTypes.data ?? []).map<Choice>((t) => ({
        id: `type:${t.round_type}`,
        title: t.label ?? ROUND_TYPE_LABELS[t.round_type],
        description: t.description ?? null,
        config: t.config,
        templateId: null,
        scope: "type",
      }));
      if (fromServer.length) return fromServer;
      // The API didn't answer: offer schema defaults so the editor still works.
      return (Object.keys(ROUND_TYPE_LABELS) as Array<keyof typeof ROUND_TYPE_LABELS>).map((type) => ({
        id: `type:${type}`,
        title: ROUND_TYPE_LABELS[type],
        description: null,
        config: defaultRoundConfig(type),
        templateId: null,
        scope: "type" as const,
      }));
    }
    return (templates.data?.items ?? []).map<Choice>((t) => ({
      id: `template:${t.id}`,
      title: t.name,
      description: t.description,
      config: t.config,
      templateId: t.id,
      scope: t.organization_id ? "org" : "global",
    }));
  }, [source, roundTypes.data, templates.data]);

  const filtered = choices.filter((c) => !query || `${c.title} ${c.description ?? ""} ${c.config.round_type}`.toLowerCase().includes(query.toLowerCase()));
  const loading = source === "types" ? roundTypes.isLoading : templates.isLoading;
  const picked = filtered.find((c) => c.id === selected) ?? null;

  const confirm = () => {
    if (!picked) return;
    onPick(withDefaults(picked.config, { round_type: picked.config.round_type, name: picked.config.name || picked.title }), picked.templateId);
    onOpenChange(false);
    setSelected(null);
    setQuery("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Add a round</DialogTitle>
          <DialogDescription>Start from a sensible default and adjust it for this job. Changes never affect the template.</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Tabs value={source} onValueChange={(v) => { setSource(v as "types" | "templates"); setSelected(null); }} variant="segmented">
              <TabsList aria-label="Start from">
                <TabsTrigger value="types">
                  <Shapes /> Round types
                </TabsTrigger>
                <TabsTrigger value="templates">
                  <LayoutTemplate /> Templates
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <SearchInput value={query} onChange={setQuery} placeholder="Filter" className="w-48" />
          </div>
          <div role="listbox" aria-label="Round starting points" className="grid max-h-[52vh] gap-1.5 overflow-y-auto pr-1 scrollbar-thin">
            {loading ? (
              Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-16 w-full" />)
            ) : filtered.length === 0 ? (
              <EmptyState
                size="sm"
                icon={<LayoutTemplate />}
                title={source === "templates" ? "No templates yet" : "Nothing matches"}
                description={source === "templates" ? "Save a round configuration as a template from the Templates page." : "Try a different filter."}
              />
            ) : (
              filtered.map((choice) => {
                const active = choice.id === selected;
                return (
                  <button
                    key={choice.id}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => setSelected(choice.id)}
                    className={cn(
                      "grid w-full gap-1 rounded-md border px-3.5 py-3 text-left transition-colors",
                      active ? "border-primary bg-surface shadow-[0_0_0_1px_var(--primary)]" : "border-line bg-surface hover:border-line-strong",
                    )}
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-fg">{choice.title}</span>
                      {choice.scope === "global" ? (
                        <Badge size="sm" tone="outline" icon={<Globe2 />}>
                          Built-in
                        </Badge>
                      ) : choice.scope === "org" ? (
                        <Badge size="sm" tone="neutral" icon={<Building2 />}>
                          Your organization
                        </Badge>
                      ) : null}
                      {choice.config.coding.enabled ? (
                        <Badge size="sm" tone="signal" icon={<Code2 />}>
                          Coding
                        </Badge>
                      ) : null}
                      <span className="ml-auto text-xs text-fg-muted tabular-nums">
                        {choice.config.duration_minutes} min, {DIFFICULTY_LABELS[choice.config.expected_difficulty].toLowerCase()}
                      </span>
                    </span>
                    {choice.description ? <span className="line-clamp-2 text-xs text-fg-muted">{choice.description}</span> : null}
                  </button>
                );
              })
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!picked} onClick={confirm}>
            Add round
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
