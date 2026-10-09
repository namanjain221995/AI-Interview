import { ChevronDown, Database, Plus, SquareCode } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { usePermission } from "@/lib/auth";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { CODE_LANGUAGE_LABELS, DIFFICULTY_LABELS } from "@/lib/labels";
import type { BankStatus, CodeLanguage, CodingQuestion, Difficulty } from "@/lib/types";

import { competencyName, useCompetencies } from "@/features/recruiter/admin/api/catalog";
import { useCodingQuestion, useCodingQuestions } from "@/features/recruiter/admin/api/library";
import { CodingQuestionEditor } from "@/features/recruiter/admin/coding/CodingQuestionEditor";
import { BankStatusBadge, DetailSkeleton, FilterSelect, LoadError, SearchInput } from "@/features/recruiter/admin/components/Controls";

const DIFFICULTY_OPTIONS = (["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }));
const STATUS_OPTIONS: Array<{ value: BankStatus; label: string }> = [
  { value: "active", label: "Active" },
  { value: "draft", label: "Draft" },
  { value: "retired", label: "Retired" },
];
const LANGUAGE_OPTIONS = (Object.keys(CODE_LANGUAGE_LABELS) as CodeLanguage[]).map((l) => ({ value: l, label: CODE_LANGUAGE_LABELS[l] }));
const SHORT_LANGUAGE: Record<CodeLanguage, string> = { python: "Python", javascript: "JS", typescript: "TS", java: "Java", go: "Go", cpp: "C++", sql: "SQL" };

/** /app/coding-questions (editor: ?edit=<id>, ?new=function|sql, ?copy=<id>) */
export default function CodingQuestionsPage() {
  const [params, setParams] = useSearchParams();
  const editId = params.get("edit");
  const copyId = params.get("copy");
  const newMode = params.get("new");
  const competencies = useCompetencies();
  useDocumentTitle(editId || newMode || copyId ? "Edit coding problem" : "Coding questions");

  const open = (next: Record<string, string> | null) => setParams(next ? new URLSearchParams(next) : new URLSearchParams(), { replace: false });

  if (editId || copyId || newMode) {
    return (
      <PageContainer width="full" className="max-w-[1400px]">
        <EditorRoute
          editId={editId}
          copyId={copyId}
          mode={newMode === "sql" ? "sql" : "function"}
          catalog={competencies.data}
          onClose={() => open(null)}
          onSaved={(q) => open({ edit: q.id })}
          onDuplicate={(q) => open({ copy: q.id })}
        />
      </PageContainer>
    );
  }
  return <CodingQuestionList onOpen={open} competencyName={(key) => competencyName(competencies.data, key)} />;
}

function EditorRoute({
  editId,
  copyId,
  mode,
  catalog,
  onClose,
  onSaved,
  onDuplicate,
}: {
  editId: string | null;
  copyId: string | null;
  mode: "function" | "sql";
  catalog: ReturnType<typeof useCompetencies>["data"];
  onClose: () => void;
  onSaved: (q: CodingQuestion) => void;
  onDuplicate: (q: CodingQuestion) => void;
}) {
  const query = useCodingQuestion(editId ?? copyId);
  if ((editId || copyId) && query.error) return <LoadError error={query.error} onRetry={() => void query.refetch()} />;
  if ((editId || copyId) && (query.isLoading || !query.data)) return <DetailSkeleton />;
  const source = query.data ?? null;
  return (
    <CodingQuestionEditor
      key={`${editId ?? ""}-${copyId ?? ""}-${mode}`}
      question={editId ? source : null}
      copyOf={copyId ? source : null}
      mode={mode}
      catalog={catalog}
      onClose={onClose}
      onSaved={onSaved}
      onDuplicate={onDuplicate}
    />
  );
}

function CodingQuestionList({ onOpen, competencyName }: { onOpen: (next: Record<string, string>) => void; competencyName: (key: string) => string }) {
  const canWrite = usePermission("question_bank:write");
  const [search, setSearch] = useState("");
  const [difficulty, setDifficulty] = useState<Difficulty | undefined>();
  const [status, setStatus] = useState<BankStatus | undefined>();
  const [language, setLanguage] = useState<CodeLanguage | undefined>();
  const q = useDebounce(search.trim(), 250);
  const questions = useCodingQuestions({ difficulty, status, q: q || undefined, language });

  // Filter locally as well, in case the API ignores a filter.
  const rows = useMemo(
    () =>
      (questions.data?.items ?? []).filter(
        (item) =>
          (!difficulty || item.difficulty === difficulty) &&
          (!status || item.status === status) &&
          (!language || item.languages.includes(language)) &&
          (!q || `${item.title} ${item.slug} ${item.competency_keys.join(" ")}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [questions.data, difficulty, status, language, q],
  );

  const columns: DataTableColumn<CodingQuestion>[] = [
    {
      id: "title",
      header: "Problem",
      sortValue: (item) => item.title,
      cell: (item) => (
        <div className="grid min-w-0 gap-0.5">
          <span className="flex items-start gap-2 font-medium text-fg">
            {item.languages.includes("sql") ? <Database className="mt-1 size-3.5 shrink-0 text-fg-subtle" aria-label="SQL problem" /> : <SquareCode className="mt-1 size-3.5 shrink-0 text-fg-subtle" aria-label="Function problem" />}
            <span className="line-clamp-2">{item.title}</span>
          </span>
          <span className="truncate text-xs text-fg-muted">{item.competency_keys.map(competencyName).join(", ") || item.slug}</span>
          {/* Phones: difficulty and a non-active status move here from their hidden columns. */}
          <span className="mt-1 flex items-center gap-1.5 sm:hidden">
            <DifficultyBadge value={item.difficulty} size="sm" />
            {item.status !== "active" ? <BankStatusBadge status={item.status} /> : null}
          </span>
        </div>
      ),
    },
    { id: "difficulty", header: "Difficulty", hideBelow: "sm", width: "7.5rem", sortValue: (item) => ["easy", "medium", "hard"].indexOf(item.difficulty), cell: (item) => <DifficultyBadge value={item.difficulty} size="sm" /> },
    {
      id: "languages",
      header: "Languages",
      hideBelow: "md",
      cell: (item) => <span className="text-xs whitespace-nowrap text-fg-muted">{item.languages.map((l) => SHORT_LANGUAGE[l] ?? l).join(", ")}</span>,
    },
    {
      id: "tests",
      header: "Tests",
      hideBelow: "lg",
      numeric: true,
      width: "9rem",
      cell: (item) => {
        const hidden = item.tests.filter((t) => t.hidden).length;
        return (
          <span className="whitespace-nowrap tabular-nums">
            {item.tests.length - hidden} <span className="text-xs text-fg-subtle">shown,</span> {hidden} <span className="text-xs text-fg-subtle">hidden</span>
          </span>
        );
      },
    },
    { id: "time", header: "Time", numeric: true, hideBelow: "lg", width: "5.5rem", cell: (item) => `${item.time_limit_minutes} min` },
    { id: "scope", header: "Source", hideBelow: "xl", width: "7rem", cell: (item) => <span className="text-xs text-fg-muted">{item.organization_id ? (item.source === "generated" ? "AI-generated" : "Your team") : "Built-in"}</span> },
    { id: "status", header: "Status", hideBelow: "sm", width: "6.5rem", cell: (item) => <BankStatusBadge status={item.status} /> },
  ];

  return (
    <PageContainer>
      <PageHeader
        title="Coding questions"
        description="Live coding problems with starter code in each language, hidden tests and up to three graduated hints, validated in the sandbox."
        actions={
          canWrite ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button leftIcon={<Plus />} rightIcon={<ChevronDown />}>
                  New problem
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => onOpen({ new: "function" })}>
                  <SquareCode /> Function problem
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => onOpen({ new: "sql" })}>
                  <Database /> SQL problem
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
          <SearchInput value={search} onChange={setSearch} placeholder="Search problems" className="w-full sm:w-64" />
          <FilterSelect<Difficulty> label="Difficulty" value={difficulty} onChange={setDifficulty} options={DIFFICULTY_OPTIONS} allLabel="Any difficulty" />
          <FilterSelect<CodeLanguage> label="Language" value={language} onChange={setLanguage} options={LANGUAGE_OPTIONS} allLabel="Any language" />
          <FilterSelect<BankStatus> label="Status" value={status} onChange={setStatus} options={STATUS_OPTIONS} allLabel="Any status" />
        </div>
        <DataTable
          columns={columns}
          data={questions.data ? rows : undefined}
          getRowId={(item) => item.id}
          loading={questions.isLoading}
          fetching={questions.isFetching && !questions.isLoading}
          error={questions.error}
          onRetry={() => void questions.refetch()}
          onRowClick={(item) => onOpen({ edit: item.id })}
          rowLabel={(item) => `Open ${item.title}`}
          pageSize={25}
          caption="Coding questions"
          empty={
            <EmptyState
              size="sm"
              icon={<SquareCode />}
              title={search || difficulty || status || language ? "No problems match" : "No coding problems yet"}
              description={search || difficulty || status || language ? "Try fewer filters." : "Create a function or SQL problem to use in coding rounds."}
              actions={!canWrite ? undefined : <Button size="sm" onClick={() => onOpen({ new: "function" })}>New problem</Button>}
            />
          }
        />
      </Card>
    </PageContainer>
  );
}
