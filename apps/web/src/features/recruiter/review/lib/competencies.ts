/**
 * Competency naming, ordering and colour. Colour follows the competency's
 * position in the round configuration (never its rank), so the same
 * competency keeps its colour across the overview, timeline and drill-down.
 */

import { seriesColor } from "@/components/charts/chartTheme";
import { humanize } from "@/lib/format";
import type { Competency, CompetencyDigest, EvidenceRecord, RoundConfig, ScoreRecord } from "@/lib/types";

export interface CompetencyMeta {
  key: string;
  name: string;
  color: string;
  weight: number | null;
  required: boolean | null;
  minEvidence: number | null;
  focusAreas: string[];
  index: number;
}

export interface CompetencyIndex {
  order: string[];
  byKey: Map<string, CompetencyMeta>;
  nameOf: (key: string) => string;
  colorOf: (key: string) => string;
}

export interface CompetencyIndexInput {
  roundConfig?: Partial<RoundConfig> | null;
  scores?: readonly ScoreRecord[] | null;
  digest?: readonly CompetencyDigest[] | null;
  evidence?: readonly Pick<EvidenceRecord, "competency_key">[] | null;
  extraKeys?: readonly string[] | null;
  catalog?: readonly Pick<Competency, "key" | "name">[] | null;
}

export function buildCompetencyIndex(input: CompetencyIndexInput): CompetencyIndex {
  const order: string[] = [];
  const names = new Map<string, string>();
  const add = (key: string | null | undefined, name?: string | null) => {
    if (!key) return;
    if (!order.includes(key)) order.push(key);
    if (name && !names.has(key)) names.set(key, name);
  };
  const config = input.roundConfig?.competencies ?? [];
  for (const c of config) add(c.key, c.name);
  for (const s of input.scores ?? []) add(s.key, s.label);
  for (const d of input.digest ?? []) add(d.key, d.name ?? null);
  for (const e of input.evidence ?? []) add(e.competency_key);
  for (const k of input.extraKeys ?? []) add(k);
  const catalog = new Map((input.catalog ?? []).map((c) => [c.key, c.name]));

  const byKey = new Map<string, CompetencyMeta>();
  order.forEach((key, index) => {
    const fromConfig = config.find((c) => c.key === key);
    byKey.set(key, {
      key,
      name: names.get(key) ?? catalog.get(key) ?? humanize(key),
      color: seriesColor(index),
      weight: fromConfig?.weight ?? null,
      required: fromConfig?.required ?? null,
      minEvidence: fromConfig?.min_evidence ?? null,
      focusAreas: fromConfig?.focus_areas ?? [],
      index,
    });
  });

  return {
    order,
    byKey,
    nameOf: (key) => byKey.get(key)?.name ?? catalog.get(key) ?? humanize(key),
    colorOf: (key) => byKey.get(key)?.color ?? "var(--chart-muted)",
  };
}
