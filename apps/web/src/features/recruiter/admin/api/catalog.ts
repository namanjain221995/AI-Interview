/** Competencies, round-type defaults and interview templates (CONTRACTS §4 Catalog). */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Competency, InterviewTemplate, InterviewTemplateInput, RoundTypeDefault } from "@/lib/types";
import { ROUND_TYPE_LABELS } from "@/lib/labels";

import { asList, asPage } from "../lib/utils";
import { withDefaults } from "../rounds/roundConfig";

const LONG_STALE = 5 * 60_000;

export async function fetchCompetencies(): Promise<Competency[]> {
  const raw = await api.get<unknown>("/competencies", { params: { limit: 200 } });
  return asList<Competency>(raw).filter((c) => c.is_active !== false);
}

/** Every active competency (global + organization), sorted by name. */
export function useCompetencies() {
  return useQuery({
    queryKey: queryKeys.catalog.competencies,
    queryFn: fetchCompetencies,
    staleTime: LONG_STALE,
    select: (items) => [...items].sort((a, b) => a.name.localeCompare(b.name)),
  });
}

export function competencyName(competencies: readonly Competency[] | undefined, key: string): string {
  return competencies?.find((c) => c.key === key)?.name ?? key;
}

export async function fetchRoundTypes(): Promise<RoundTypeDefault[]> {
  const raw = await api.get<unknown>("/round-types");
  return asList<RoundTypeDefault>(raw, "round_types").map((entry) => ({
    ...entry,
    label: entry.label ?? ROUND_TYPE_LABELS[entry.round_type] ?? entry.round_type,
    config: withDefaults(entry.config, { round_type: entry.round_type, name: entry.config?.name || entry.label || ROUND_TYPE_LABELS[entry.round_type] }),
  }));
}

/** `GET /round-types`: the default RoundConfig per RoundType. */
export function useRoundTypes() {
  return useQuery({ queryKey: queryKeys.catalog.roundTypes, queryFn: fetchRoundTypes, staleTime: LONG_STALE });
}

export interface TemplateListParams {
  round_type?: string;
  q?: string;
}

export function useTemplates(params: TemplateListParams = {}) {
  return useQuery({
    queryKey: queryKeys.catalog.templates(params),
    queryFn: async () => {
      const raw = await api.get<unknown>("/templates", { params: { ...params, limit: 200 } });
      const page = asPage<InterviewTemplate>(raw);
      return {
        ...page,
        items: page.items.map((t) => ({ ...t, config: withDefaults(t.config, { round_type: t.round_type, name: t.config?.name || t.name }) })),
      };
    },
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useTemplate(id: string | null | undefined) {
  return useQuery({
    queryKey: [...queryKeys.catalog.templates(), "detail", id ?? ""],
    enabled: Boolean(id),
    queryFn: async () => {
      const t = await api.get<InterviewTemplate>(`/templates/${id}`);
      return { ...t, config: withDefaults(t.config, { round_type: t.round_type, name: t.config?.name || t.name }) };
    },
  });
}

export function useSaveTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string | null; input: InterviewTemplateInput }) =>
      id ? api.patch<InterviewTemplate>(`/templates/${id}`, input) : api.post<InterviewTemplate>("/templates", input),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: ["catalog", "templates"] }),
  });
}

export function useDeleteTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/templates/${id}`),
    meta: { successToast: "Template deleted" },
    onSuccess: (_data, id) => {
      // Drop the deleted detail first so the invalidation below doesn't refetch it (404).
      client.removeQueries({ queryKey: [...queryKeys.catalog.templates(), "detail", id] });
      void client.invalidateQueries({ queryKey: ["catalog", "templates"] });
    },
  });
}
