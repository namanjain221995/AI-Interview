/** Question bank and coding questions (CONTRACTS §4 Catalog). */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  BankQuestion,
  BankQuestionInput,
  BankStatus,
  CodeLanguage,
  CodingQuestion,
  CodingQuestionInput,
  CodingTestCase,
  Difficulty,
  QuestionGenerateRequest,
  QuestionKind,
  QuestionSource,
} from "@/lib/types";

import type { CodingQuestionSummary, CodingValidationRow, CodingValidationSummary, QuestionGenerateResult, QuestionSearchResult, SkippedQuestion, ValidationFailure } from "../types";
import { asList, asPage } from "../lib/utils";

// ------------------------------------------------------------ question bank

export interface QuestionBankQuery {
  competency_key?: string;
  difficulty?: Difficulty;
  kind?: QuestionKind;
  source?: QuestionSource;
  status?: BankStatus;
  q?: string;
  limit: number;
  offset: number;
}

export function useQuestionBank(params: QuestionBankQuery, enabled = true) {
  return useQuery({
    queryKey: queryKeys.catalog.questionBank(params),
    queryFn: async () => asPage<BankQuestion>(await api.get<unknown>("/question-bank", { params: { ...params } })),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export interface SemanticSearchQuery {
  query: string;
  competency_key?: string;
  difficulty?: Difficulty;
  kind?: QuestionKind;
  /** `null` searches every status (the API defaults to active). */
  status?: BankStatus | null;
  limit?: number;
}

export function normalizeSearchResult(raw: unknown): QuestionSearchResult {
  const record = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  return {
    items: asList<BankQuestion>(raw, "results"),
    mode: typeof record.mode === "string" ? record.mode : "semantic",
    reranked: record.reranked === true,
  };
}

/** `POST /question-bank/search`: embeddings + reranker (lexical fallback); items carry `score`. */
export function useQuestionSearch(params: SemanticSearchQuery | null) {
  return useQuery({
    queryKey: queryKeys.catalog.questionBank({ semantic: params }),
    enabled: Boolean(params && params.query.trim().length >= 3),
    queryFn: async () => {
      const body: Record<string, unknown> = { query: params!.query, limit: params?.limit ?? 25, status: params?.status ?? null };
      if (params?.competency_key) body.competency_key = params.competency_key;
      if (params?.difficulty) body.difficulty = params.difficulty;
      if (params?.kind) body.kind = params.kind;
      return normalizeSearchResult(await api.post<unknown>("/question-bank/search", body));
    },
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function useSaveQuestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string | null; input: Partial<BankQuestionInput> }) =>
      id ? api.patch<BankQuestion>(`/question-bank/${id}`, input) : api.post<BankQuestion>("/question-bank", input),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: ["catalog", "question-bank"] }),
  });
}

/** Bulk status change (activate / retire / back to draft), one PATCH per question. */
export function useSetQuestionStatus() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ ids, status }: { ids: string[]; status: BankStatus }) => {
      const results = await Promise.allSettled(ids.map((id) => api.patch<BankQuestion>(`/question-bank/${id}`, { status })));
      const failed = results.filter((r) => r.status === "rejected").length;
      if (failed === ids.length && failed > 0) throw (results.find((r) => r.status === "rejected") as PromiseRejectedResult).reason;
      return { updated: ids.length - failed, failed };
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["catalog", "question-bank"] }),
  });
}

export function normalizeGenerateResult(raw: unknown): QuestionGenerateResult {
  const items = asList<BankQuestion>(raw, "created", "questions");
  let skipped = 0;
  let skippedItems: SkippedQuestion[] = [];
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const record = raw as Record<string, unknown>;
    const value = record.skipped ?? record.skipped_duplicates ?? record.duplicates;
    if (typeof value === "number") skipped = value;
    else if (Array.isArray(value)) {
      skipped = value.length;
      skippedItems = value
        .filter((v): v is Record<string, unknown> => Boolean(v) && typeof v === "object")
        .map((v) => ({
          text: typeof v.text === "string" ? v.text : "",
          reason: typeof v.reason === "string" ? v.reason : "",
          duplicate_of: typeof v.duplicate_of === "string" ? v.duplicate_of : null,
          similarity: typeof v.similarity === "number" ? v.similarity : null,
        }));
    }
  }
  return { items, skipped, skippedItems };
}

export function useGenerateQuestions() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: QuestionGenerateRequest) =>
      normalizeGenerateResult(await api.post<unknown>("/question-bank/generate", input, { timeoutMs: 180_000 })),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: ["catalog", "question-bank"] }),
  });
}

// --------------------------------------------------------- coding questions

export interface CodingQuestionQuery {
  difficulty?: Difficulty;
  status?: BankStatus;
  q?: string;
  language?: CodeLanguage;
  competency?: string;
  source?: QuestionSource;
  scope?: "all" | "organization" | "global";
  limit?: number;
}

/**
 * A list row as a `CodingQuestion` for the tables and pickers. The list endpoint
 * returns summaries: the heavy fields come back empty and `tests` holds one
 * id-only placeholder per test (only the count and the hidden flag are real).
 * Load the problem itself with `useCodingQuestion`. Full rows pass through.
 */
export function codingSummaryAsQuestion(item: CodingQuestionSummary | CodingQuestion): CodingQuestion {
  if ("tests" in item && Array.isArray(item.tests)) return item;
  const summary = item as CodingQuestionSummary;
  const tests: CodingTestCase[] = [
    ...Array.from({ length: summary.visible_test_count ?? 0 }, (_, i) => ({ id: `visible-${i + 1}`, hidden: false })),
    ...Array.from({ length: summary.hidden_test_count ?? 0 }, (_, i) => ({ id: `hidden-${i + 1}`, hidden: true })),
  ];
  return {
    ...summary,
    prompt_markdown: "",
    signature: {},
    starter_code: {},
    tests,
    sql_setup: null,
    expected_concepts: [],
    optimal_complexity: {},
    rubric: {},
    hints: [],
    followups: [],
    common_errors: [],
  };
}

/** Coding problems list (summary rows, see `codingSummaryAsQuestion`). */
export function useCodingQuestions(params: CodingQuestionQuery = {}) {
  return useQuery({
    queryKey: queryKeys.catalog.codingQuestions(params),
    queryFn: async () => {
      const page = asPage<CodingQuestionSummary | CodingQuestion>(
        await api.get<unknown>("/coding-questions", { params: { limit: 200, view: "summary", ...params } }),
      );
      return { ...page, items: page.items.map(codingSummaryAsQuestion) };
    },
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function useCodingQuestion(id: string | null | undefined) {
  return useQuery({
    queryKey: [...queryKeys.catalog.codingQuestions(), "detail", id ?? ""],
    queryFn: () => api.get<CodingQuestion>(`/coding-questions/${id}`),
    enabled: Boolean(id),
  });
}

export function useSaveCodingQuestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string | null; input: CodingQuestionInput }) =>
      id ? api.patch<CodingQuestion>(`/coding-questions/${id}`, input) : api.post<CodingQuestion>("/coding-questions", input),
    meta: { silentError: true },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ["catalog", "coding-questions"] });
      if (saved?.id) client.setQueryData([...queryKeys.catalog.codingQuestions(), "detail", saved.id], saved);
    },
  });
}

const RUN_STATUSES = new Set<string>(["queued", "running", "passed", "failed", "error", "compile_error", "timeout", "memory_exceeded", "rejected", "skipped"]);

function validationRows(value: unknown): CodingValidationRow[] {
  let rows: unknown[] = [];
  if (Array.isArray(value)) rows = value;
  else if (value && typeof value === "object") rows = Object.entries(value as Record<string, unknown>).map(([language, v]) => ({ language, ...(v as object) }));
  return rows
    .filter((r): r is Record<string, unknown> => Boolean(r) && typeof r === "object")
    .map((r) => {
      const passed = typeof r.passed === "number" ? r.passed : typeof r.passed_count === "number" ? r.passed_count : 0;
      const total = typeof r.total === "number" ? r.total : typeof r.total_count === "number" ? r.total_count : 0;
      const status = (typeof r.status === "string" && RUN_STATUSES.has(r.status) ? r.status : passed === total && total > 0 ? "passed" : "failed") as CodingValidationRow["status"];
      const failures: ValidationFailure[] = Array.isArray(r.failures)
        ? r.failures
            .filter((f): f is Record<string, unknown> => Boolean(f) && typeof f === "object")
            .map((f) => ({ id: String(f.id ?? ""), hidden: f.hidden === true, error: typeof f.error === "string" ? f.error : null, expected: f.expected, actual: f.actual }))
        : [];
      return {
        language: String(r.language) as CodeLanguage,
        status,
        passed,
        total,
        runtime_ms: typeof r.runtime_ms === "number" ? r.runtime_ms : null,
        error:
          typeof r.error === "string" && r.error
            ? r.error
            : typeof r.compile_output === "string" && r.compile_output
              ? r.compile_output
              : typeof r.stderr === "string" && r.stderr
                ? r.stderr
                : null,
        failures,
      };
    });
}

/** Accepts `{ok, results:[...], starters:[...]}`, `{results: {python: {...}}}` or a bare list. */
export function normalizeValidation(raw: unknown): CodingValidationSummary {
  const record = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null;
  const results = validationRows(record ? (record.results ?? record.languages) : raw);
  const starters = validationRows(record?.starters ?? []);
  const ok = typeof record?.ok === "boolean" ? record.ok : results.length > 0 && results.every((r) => r.status === "passed" || r.status === "skipped");
  return {
    ok,
    results,
    starters,
    duration_ms: typeof record?.duration_ms === "number" ? record.duration_ms : null,
    validated_at: typeof record?.validated_at === "string" ? record.validated_at : null,
  };
}

/** Runs every reference solution (and, by default, every starter) against all tests in the sandbox. */
export function useValidateCodingQuestion() {
  return useMutation({
    mutationFn: async ({ id, includeStarters = true }: { id: string; includeStarters?: boolean }) =>
      normalizeValidation(await api.post<unknown>(`/coding-questions/${id}/validate`, undefined, { params: { include_starters: includeStarters }, timeoutMs: 240_000 })),
    meta: { silentError: true },
  });
}
