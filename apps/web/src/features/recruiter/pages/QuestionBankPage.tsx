import { Archive, CheckCircle2, LibraryBig, MoreHorizontal, Pencil, Plus, Sparkles, Wand2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Combobox } from "@/components/ui/Combobox";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { usePermission } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { DIFFICULTY_LABELS, QUESTION_KIND_LABELS } from "@/lib/labels";
import type { BankQuestion, BankStatus, Difficulty, QuestionKind, QuestionSource } from "@/lib/types";

import { competencyName, useCompetencies } from "@/features/recruiter/admin/api/catalog";
import { useQuestionBank, useQuestionSearch, useSetQuestionStatus } from "@/features/recruiter/admin/api/library";
import { BankStatusBadge, FilterSelect, SearchInput } from "@/features/recruiter/admin/components/Controls";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";
import { GenerateDialog } from "@/features/recruiter/admin/question-bank/GenerateDialog";
import { BANK_KINDS } from "@/features/recruiter/admin/question-bank/questionModel";
import { QuestionSheet } from "@/features/recruiter/admin/question-bank/QuestionSheet";

const PAGE_SIZE = 25;
const DIFFICULTY_OPTIONS = (["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }));
const KIND_OPTIONS = BANK_KINDS.map((k) => ({ value: k, label: QUESTION_KIND_LABELS[k] }));
const SOURCE_OPTIONS: Array<{ value: QuestionSource; label: string }> = [
  { value: "curated", label: "Curated" },
  { value: "generated", label: "AI-generated" },
];
const STATUS_OPTIONS: Array<{ value: BankStatus; label: string }> = [
  { value: "active", label: "Active" },
  { value: "draft", label: "Draft" },
  { value: "retired", label: "Retired" },
];

/** /app/question-bank */
export default function QuestionBankPage() {
  useDocumentTitle("Question bank");
  const canWrite = usePermission("question_bank:write");
  const competencies = useCompetencies();
  const [mode, setMode] = useState<"keyword" | "semantic">("keyword");
  const [search, setSearch] = useState("");
  const [competency, setCompetency] = useState<string | undefined>();
  const [difficulty, setDifficulty] = useState<Difficulty | undefined>();
  const [kind, setKind] = useState<QuestionKind | undefined>();
  const [source, setSource] = useState<QuestionSource | undefined>();
  const [status, setStatus] = useState<BankStatus | undefined>();
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<BankQuestion | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [generateOpen, setGenerateOpen] = useState(false);
  const term = useDebounce(search.trim(), mode === "semantic" ? 450 : 250);
  const setQuestionStatus = useSetQuestionStatus();

  const semantic = mode === "semantic" && term.length >= 3;
  const listQuery = useMemo(
    () => ({ competency_key: competency, difficulty, kind, source, status, q: mode === "keyword" ? term || undefined : undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [competency, difficulty, kind, source, status, term, mode, page],
  );
  const list = useQuestionBank(listQuery, !semantic);
  const searchResults = useQuestionSearch(semantic ? { query: term, competency_key: competency, difficulty, kind, status: status ?? null, limit: 15 } : null);

  // Search ranks by relevance; the API has no source filter, so apply it here.
  const semanticRows = useMemo(() => (searchResults.data?.items ?? []).filter((q) => !source || q.source === source), [searchResults.data, source]);
  const rows = semantic ? semanticRows : list.data?.items;
  const loading = semantic ? searchResults.isLoading : list.isLoading;

  const resetPage = () => setPage(0);
  const openEditor = (question: BankQuestion | null) => {
    setEditing(question);
    setSheetOpen(true);
  };

  const changeStatus = async (question: BankQuestion, next: BankStatus) => {
    try {
      await setQuestionStatus.mutateAsync({ ids: [question.id], status: next });
      toast.success(next === "active" ? "Question activated" : next === "retired" ? "Question retired" : "Moved to drafts");
    } catch (error) {
      toast.error("Couldn't update the question", { description: describeFailure(error) });
    }
  };

  const columns: DataTableColumn<BankQuestion>[] = [
    {
      id: "question",
      header: "Question",
      cell: (q) => (
        <div className="grid min-w-0 max-w-[46rem] gap-1">
          <p className="line-clamp-2 text-sm text-fg">{q.text}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-fg-muted">
            {q.topic ? <span>{q.topic}</span> : null}
            {q.source === "generated" ? (
              <span className="inline-flex items-center gap-1 text-accent-fg">
                <Sparkles className="size-3" aria-hidden="true" />
                AI-generated
              </span>
            ) : null}
            {!q.organization_id ? <span className="text-fg-subtle">Built-in</span> : null}
            {/* Phones: difficulty and a non-active status move here from their hidden columns. */}
            <span className="flex items-center gap-1.5 sm:hidden">
              <DifficultyBadge value={q.difficulty} size="sm" />
              {q.status !== "active" ? <BankStatusBadge status={q.status} /> : null}
            </span>
          </p>
        </div>
      ),
    },
    {
      id: "competency",
      header: "Competency",
      hideBelow: "md",
      sortValue: semantic ? undefined : (q) => competencyName(competencies.data, q.competency_key),
      cell: (q) => <span className="text-sm text-fg">{competencyName(competencies.data, q.competency_key)}</span>,
    },
    { id: "difficulty", header: "Difficulty", hideBelow: "sm", width: "7.5rem", cell: (q) => <DifficultyBadge value={q.difficulty} size="sm" /> },
    { id: "kind", header: "Kind", hideBelow: "lg", width: "7.5rem", cell: (q) => <span className="text-fg-muted">{QUESTION_KIND_LABELS[q.kind] ?? q.kind}</span> },
    ...(semantic
      ? [
          {
            id: "score",
            header: "Relevance",
            numeric: true,
            width: "6.5rem",
            cell: (q: BankQuestion) =>
              typeof q.score === "number" ? (
                <Badge tone={q.score >= 0.5 ? "accent" : q.score >= 0.15 ? "neutral" : "outline"} size="sm">
                  {Math.round(q.score * 100)}%
                </Badge>
              ) : (
                "—"
              ),
          } satisfies DataTableColumn<BankQuestion>,
        ]
      : [
          { id: "usage", header: "Used", numeric: true, hideBelow: "xl", width: "5rem", cell: (q: BankQuestion) => formatNumber(q.usage_count ?? 0) } satisfies DataTableColumn<BankQuestion>,
        ]),
    { id: "status", header: "Status", hideBelow: "sm", width: "6.5rem", cell: (q) => <BankStatusBadge status={q.status} /> },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      width: "3.5rem",
      cell: (q) =>
        canWrite && q.organization_id ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="size-8 px-0" aria-label="Question actions" data-row-action>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => openEditor(q)}>
                <Pencil /> Edit
              </DropdownMenuItem>
              {q.status !== "active" ? (
                <DropdownMenuItem onSelect={() => void changeStatus(q, "active")}>
                  <CheckCircle2 /> Activate
                </DropdownMenuItem>
              ) : null}
              {q.status !== "retired" ? (
                <DropdownMenuItem destructive onSelect={() => void changeStatus(q, "retired")}>
                  <Archive /> Retire
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null,
    },
  ];

  const filtered = Boolean(term || competency || difficulty || kind || source || status);

  return (
    <PageContainer width="full" className="max-w-[1400px]">
      <PageHeader
        title="Question bank"
        description="Curated questions the interviewer can draw on, matched to competencies by meaning. It still adapts wording to each conversation."
        actions={
          canWrite ? (
            <>
              <Button variant="outline" leftIcon={<Wand2 />} onClick={() => setGenerateOpen(true)}>
                Generate with AI
              </Button>
              <Button leftIcon={<Plus />} onClick={() => openEditor(null)}>
                New question
              </Button>
            </>
          ) : null
        }
      />
      <Card className="overflow-hidden">
        <div className="grid gap-3 border-b border-line-subtle px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex w-full min-w-0 items-center gap-2 lg:w-[30rem]">
              <SearchInput
                value={search}
                onChange={(v) => {
                  setSearch(v);
                  resetPage();
                }}
                placeholder={mode === "semantic" ? "Describe what you're looking for, e.g. debugging a slow query" : "Search question text or topic"}
                className="min-w-0 flex-1"
              />
              <Tabs value={mode} onValueChange={(v) => setMode(v as "keyword" | "semantic")} variant="segmented">
                <TabsList aria-label="Search mode">
                  <TabsTrigger value="keyword">Keyword</TabsTrigger>
                  <TabsTrigger value="semantic">
                    <Sparkles /> Meaning
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
            <div className="w-full sm:w-56">
              <Combobox
                aria-label="Filter by competency"
                options={(competencies.data ?? []).map((c) => ({ value: c.key, label: c.name }))}
                value={competency ?? null}
                onChange={(v) => {
                  setCompetency(v ?? undefined);
                  resetPage();
                }}
                placeholder="All competencies"
              />
            </div>
            <FilterSelect<Difficulty> label="Difficulty" value={difficulty} onChange={(v) => { setDifficulty(v); resetPage(); }} options={DIFFICULTY_OPTIONS} allLabel="Any difficulty" />
            <FilterSelect<QuestionKind> label="Kind" value={kind} onChange={(v) => { setKind(v); resetPage(); }} options={KIND_OPTIONS} allLabel="Any kind" />
            <FilterSelect<QuestionSource> label="Source" value={source} onChange={(v) => { setSource(v); resetPage(); }} options={SOURCE_OPTIONS} allLabel="Any source" />
            <FilterSelect<BankStatus> label="Status" value={status} onChange={(v) => { setStatus(v); resetPage(); }} options={STATUS_OPTIONS} allLabel="Any status" />
          </div>
          {mode === "semantic" ? (
            <p className="text-xs text-fg-muted">
              {term.length < 3
                ? "Type at least three characters. Results are ranked by meaning, not exact words."
                : searchResults.data?.mode === "lexical"
                  ? `Showing word matches for “${term}”: meaning-based search is available once question embeddings are ready.`
                  : `Top matches for “${term}”, ranked by relevance${searchResults.data?.reranked ? " (reranked)" : ""}.`}
            </p>
          ) : null}
        </div>
        <DataTable
          columns={columns}
          data={rows}
          getRowId={(q) => q.id}
          loading={loading}
          fetching={semantic ? searchResults.isFetching && !searchResults.isLoading : list.isFetching && !list.isLoading}
          error={semantic ? searchResults.error : list.error}
          onRetry={() => void (semantic ? searchResults.refetch() : list.refetch())}
          onRowClick={(q) => openEditor(q)}
          rowLabel={(q) => `Open question: ${q.text.slice(0, 60)}`}
          pageSize={PAGE_SIZE}
          {...(semantic ? {} : { page, total: list.data?.total ?? 0, onPageChange: setPage })}
          caption="Question bank"
          density="compact"
          empty={
            filtered ? (
              <EmptyState size="sm" icon={<LibraryBig />} title="No questions match" description="Try fewer filters, or search by meaning instead of exact words." />
            ) : (
              <EmptyState
                icon={<LibraryBig />}
                title="The bank is empty"
                description="Add your own questions or generate drafts for a competency, then review them."
                actions={
                  canWrite ? (
                    <Button size="sm" leftIcon={<Wand2 />} onClick={() => setGenerateOpen(true)}>
                      Generate with AI
                    </Button>
                  ) : undefined
                }
              />
            )
          }
        />
      </Card>

      <QuestionSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        question={editing}
        catalog={competencies.data}
        defaults={editing ? undefined : { competency_key: competency ?? "", difficulty: difficulty ?? "medium", kind: kind ?? "technical" }}
      />
      <GenerateDialog open={generateOpen} onOpenChange={setGenerateOpen} catalog={competencies.data} defaultCompetency={competency} onEdit={(q) => openEditor(q)} />
    </PageContainer>
  );
}
