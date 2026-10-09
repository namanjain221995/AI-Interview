import { Building2, ChevronDown, Code2, Globe2, LayoutTemplate, Plus, Star } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { usePermission } from "@/lib/auth";
import { formatRelativeOrDate } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { DIFFICULTY_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { InterviewTemplate, RoundType } from "@/lib/types";

import { useCompetencies, useRoundTypes, useTemplate, useTemplates } from "@/features/recruiter/admin/api/catalog";
import { useCodingQuestions } from "@/features/recruiter/admin/api/library";
import { DetailSkeleton, FilterSelect, LoadError, SearchInput } from "@/features/recruiter/admin/components/Controls";
import { defaultRoundConfig, ROUND_TYPES, withDefaults } from "@/features/recruiter/admin/rounds/roundConfig";
import { RoundShapeMini } from "@/features/recruiter/admin/rounds/RoundBlueprint";
import { TemplateEditor, type TemplateDraft } from "@/features/recruiter/admin/templates/TemplateEditor";

const ROUND_TYPE_OPTIONS = ROUND_TYPES.map((t) => ({ value: t, label: ROUND_TYPE_LABELS[t] }));

/** /app/templates (editor: ?edit=<id>, ?new=<round_type>, ?copy=<id>) */
export default function TemplatesPage() {
  const [params, setParams] = useSearchParams();
  const editId = params.get("edit");
  const copyId = params.get("copy");
  const newType = params.get("new") as RoundType | null;
  useDocumentTitle(editId || copyId || newType ? "Edit template" : "Templates");
  const open = (next: Record<string, string> | null) => setParams(next ? new URLSearchParams(next) : new URLSearchParams());

  if (editId || copyId || newType) {
    return (
      <PageContainer>
        <TemplateEditorRoute editId={editId} copyId={copyId} newType={newType} onOpen={open} />
      </PageContainer>
    );
  }
  return <TemplateList onOpen={open} />;
}

function TemplateEditorRoute({ editId, copyId, newType, onOpen }: { editId: string | null; copyId: string | null; newType: RoundType | null; onOpen: (next: Record<string, string> | null) => void }) {
  const canWrite = usePermission("templates:write");
  const source = useTemplate(editId ?? copyId);
  const roundTypes = useRoundTypes();
  const competencies = useCompetencies();
  const problems = useCodingQuestions();

  const initial = useMemo<TemplateDraft | null>(() => {
    if (editId || copyId) {
      if (!source.data) return null;
      const t = source.data;
      return copyId
        ? { name: `${t.name} (copy)`, description: t.description ?? "", is_default: false, config: { ...t.config } }
        : { name: t.name, description: t.description ?? "", is_default: t.is_default, config: t.config };
    }
    const type = newType && ROUND_TYPES.includes(newType) ? newType : "technical_screening";
    const base = roundTypes.data?.find((r) => r.round_type === type)?.config ?? defaultRoundConfig(type);
    return { name: "", description: "", is_default: false, config: withDefaults(base, { round_type: type, name: base.name }) };
  }, [editId, copyId, newType, source.data, roundTypes.data]);

  if ((editId || copyId) && source.error) return <LoadError error={source.error} onRetry={() => void source.refetch()} />;
  if (!initial || (newType && roundTypes.isLoading)) return <DetailSkeleton />;

  return (
    <TemplateEditor
      key={`${editId ?? ""}-${copyId ?? ""}-${newType ?? ""}`}
      template={editId ? (source.data ?? null) : null}
      initial={initial}
      catalog={competencies.data}
      problems={problems.data?.items}
      canWrite={canWrite}
      onClose={() => onOpen(null)}
      onSaved={(t) => onOpen({ edit: t.id })}
      onDuplicate={(t) => onOpen({ copy: t.id })}
    />
  );
}

function TemplateList({ onOpen }: { onOpen: (next: Record<string, string> | null) => void }) {
  const canWrite = usePermission("templates:write");
  const [search, setSearch] = useState("");
  const [roundType, setRoundType] = useState<RoundType | undefined>();
  const [scope, setScope] = useState<"all" | "org" | "global">("all");
  const templates = useTemplates();

  const rows = useMemo(
    () =>
      (templates.data?.items ?? [])
        .filter((t) => (!roundType || t.round_type === roundType) && (scope === "all" || (scope === "org" ? Boolean(t.organization_id) : !t.organization_id)))
        .filter((t) => !search.trim() || `${t.name} ${t.description ?? ""}`.toLowerCase().includes(search.trim().toLowerCase()))
        .sort((a, b) => Number(Boolean(b.organization_id)) - Number(Boolean(a.organization_id)) || a.name.localeCompare(b.name)),
    [templates.data, roundType, scope, search],
  );

  const columns: DataTableColumn<InterviewTemplate>[] = [
    {
      id: "name",
      header: "Template",
      sortValue: (t) => t.name,
      cell: (t) => (
        <div className="grid min-w-0 gap-1 py-0.5">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-fg">{t.name}</span>
            {t.organization_id ? (
              <Badge size="sm" tone="neutral" icon={<Building2 />}>
                Your organization
              </Badge>
            ) : (
              <Badge size="sm" tone="outline" icon={<Globe2 />}>
                Built-in
              </Badge>
            )}
            {t.is_default ? (
              <Badge size="sm" tone="accent" icon={<Star />}>
                Default
              </Badge>
            ) : null}
            {t.config.coding.enabled ? (
              <Badge size="sm" tone="signal" icon={<Code2 />}>
                Coding
              </Badge>
            ) : null}
          </span>
          {t.description ? <span className="line-clamp-1 max-w-2xl text-xs text-fg-muted">{t.description}</span> : null}
        </div>
      ),
    },
    { id: "type", header: "Round type", hideBelow: "md", sortValue: (t) => ROUND_TYPE_LABELS[t.round_type], cell: (t) => <span className="text-fg-muted">{ROUND_TYPE_LABELS[t.round_type]}</span> },
    {
      id: "shape",
      header: "Shape",
      hideBelow: "lg",
      width: "17rem",
      cell: (t) => <RoundShapeMini config={t.config} />,
    },
    {
      id: "duration",
      header: "Duration",
      numeric: true,
      width: "7rem",
      sortValue: (t) => t.config.duration_minutes,
      cell: (t) => (
        <span className="grid text-right">
          <span className="tabular-nums">{t.config.duration_minutes} min</span>
          <span className="text-xs text-fg-subtle">{DIFFICULTY_LABELS[t.config.expected_difficulty]}</span>
        </span>
      ),
    },
    { id: "updated", header: "Updated", hideBelow: "xl", width: "8rem", cell: (t) => <span className="text-fg-muted">{t.organization_id ? formatRelativeOrDate(t.updated_at) : "—"}</span> },
  ];

  return (
    <PageContainer width="full" className="max-w-[1400px]">
      <PageHeader
        title="Templates"
        description="Round setups your team reuses across jobs. Built-in templates cover every round type; duplicate one to adapt it."
        actions={
          canWrite ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button leftIcon={<Plus />} rightIcon={<ChevronDown />}>
                  New template
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="max-h-80 overflow-y-auto">
                <DropdownMenuLabel>Start from a round type</DropdownMenuLabel>
                {ROUND_TYPES.map((type) => (
                  <DropdownMenuItem key={type} onSelect={() => onOpen({ new: type })}>
                    {ROUND_TYPE_LABELS[type]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
          <SearchInput value={search} onChange={setSearch} placeholder="Search templates" className="w-full sm:w-64" />
          <FilterSelect<RoundType> label="Round type" value={roundType} onChange={setRoundType} options={ROUND_TYPE_OPTIONS} allLabel="Any round type" />
          <FilterSelect<"org" | "global">
            label="Source"
            value={scope === "all" ? undefined : scope}
            onChange={(v) => setScope(v ?? "all")}
            options={[
              { value: "org", label: "Your organization" },
              { value: "global", label: "Built-in" },
            ]}
            allLabel="All templates"
          />
        </div>
        <DataTable
          columns={columns}
          data={templates.data ? rows : undefined}
          getRowId={(t) => t.id}
          loading={templates.isLoading}
          error={templates.error}
          onRetry={() => void templates.refetch()}
          onRowClick={(t) => onOpen({ edit: t.id })}
          rowLabel={(t) => `Open ${t.name}`}
          pageSize={30}
          caption="Interview templates"
          empty={<EmptyState size="sm" icon={<LayoutTemplate />} title="No templates match" description="Try another round type or source." />}
        />
      </Card>
    </PageContainer>
  );
}
