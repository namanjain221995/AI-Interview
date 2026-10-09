/**
 * Saves a job draft in the order the API needs: job row, skill requirements,
 * rounds (delete, update, create), then the round order. Each failure is
 * mapped to the editor field or round it belongs to; work that succeeded is
 * kept (round ids are returned so a retry never duplicates rounds).
 */

import { api } from "@/lib/api";
import type { CandidateReportMode, Competency, Job, JobRequirement, Round } from "@/lib/types";

import { isApiError } from "@/lib/api";

import { describeFailure, fieldErrorsOf, roundConfigErrorsOf, type FieldErrors } from "../lib/serverErrors";
import { normalizeRound } from "../api/jobs";
import { planRequirements, planRounds, roundCreatePayload, roundUpdatePayload, toJobInput, type JobDraft } from "./jobDraft";

export interface SaveJobContext {
  jobId: string | null;
  draft: JobDraft;
  requirements: readonly JobRequirement[];
  rounds: readonly Round[];
  catalog: readonly Competency[];
  fallbackReportMode: CandidateReportMode;
}

export interface SaveJobResult {
  job: Job | null;
  /** Job-level field errors (title, description…). */
  jobErrors: FieldErrors;
  /** Round errors by round uid, keyed by config path. */
  roundErrors: Record<string, FieldErrors>;
  /** Server ids assigned to new rounds, by uid. */
  createdRoundIds: Record<string, string>;
  /** Failures that don't map to a field. */
  messages: string[];
  /** Things that happened differently from what was asked (e.g. a round deactivated instead of deleted). */
  notices: string[];
  ok: boolean;
}

const JOB_FIELDS = new Set([
  "title",
  "department",
  "location",
  "employment_type",
  "seniority",
  "description",
  "experience_min_years",
  "experience_max_years",
  "status",
  "interview_instructions",
  "integrity_settings",
  "recording_settings",
  "candidate_report_mode",
  "allow_candidate_round_selection",
]);

function splitJobErrors(error: unknown): { fields: FieldErrors; message: string | null } {
  const all = fieldErrorsOf(error);
  const fields: FieldErrors = {};
  for (const [path, message] of Object.entries(all)) {
    const head = path.split(".")[0] ?? path;
    if (JOB_FIELDS.has(head)) fields[path] = message;
  }
  return { fields, message: Object.keys(fields).length ? null : describeFailure(error) };
}

export async function saveJobDraft(ctx: SaveJobContext): Promise<SaveJobResult> {
  const result: SaveJobResult = { job: null, jobErrors: {}, roundErrors: {}, createdRoundIds: {}, messages: [], notices: [], ok: false };
  const input = toJobInput(ctx.draft, ctx.fallbackReportMode);

  // 1. Job row. Open is applied last so a job never opens without its rounds.
  const wantsOpen = input.status === "open";
  const firstStatus = wantsOpen && !ctx.jobId ? "draft" : input.status;
  try {
    result.job = ctx.jobId
      ? await api.patch<Job>(`/jobs/${ctx.jobId}`, { ...input, status: wantsOpen && ctx.rounds.length === 0 ? "draft" : input.status })
      : await api.post<Job>("/jobs", { ...input, status: firstStatus });
  } catch (error) {
    const { fields, message } = splitJobErrors(error);
    result.jobErrors = fields;
    if (message) result.messages.push(message);
    return result;
  }
  const jobId = result.job.id;

  // 2. Requirements (skills). Best effort per row.
  const reqPlan = planRequirements(ctx.draft, ctx.requirements, ctx.catalog);
  const reqOps: Array<Promise<unknown>> = [
    ...reqPlan.remove.map((id) => api.delete(`/requirements/${id}`)),
    ...reqPlan.update.map(({ id, input: body }) => api.patch(`/requirements/${id}`, body)),
    ...reqPlan.create.map((body) => api.post(`/jobs/${jobId}/requirements`, body)),
  ];
  const reqResults = await Promise.allSettled(reqOps);
  const reqFailures = reqResults.filter((r): r is PromiseRejectedResult => r.status === "rejected");
  if (reqFailures.length) {
    result.jobErrors.required_skills = `${reqFailures.length} skill ${reqFailures.length === 1 ? "change" : "changes"} didn't save: ${describeFailure(reqFailures[0]!.reason)}`;
  }

  // 3. Rounds.
  const plan = planRounds(ctx.draft, ctx.jobId ? ctx.rounds : []);
  for (const id of plan.remove) {
    const name = ctx.rounds.find((r) => r.id === id)?.name ?? "A round";
    try {
      await api.delete(`/rounds/${id}`);
    } catch (error) {
      if (isApiError(error) && error.code === "round_in_use") {
        // Rounds with interviews are kept for history: deactivate instead.
        try {
          await api.patch<Round>(`/rounds/${id}`, { is_active: false });
          result.notices.push(`${name} already has interviews, so it was deactivated instead of deleted.`);
        } catch (inner) {
          result.messages.push(`${name} couldn't be removed: ${describeFailure(inner)}`);
        }
      } else {
        result.messages.push(`${name} couldn't be deleted: ${describeFailure(error)}`);
      }
    }
  }
  const roundFailure = (uid: string, error: unknown) => {
    const errors = roundConfigErrorsOf(error);
    result.roundErrors[uid] = Object.keys(errors).length ? errors : { "": describeFailure(error) };
  };
  for (const round of plan.update) {
    try {
      await api.patch<Round>(`/rounds/${round.id}`, roundUpdatePayload(round));
    } catch (error) {
      roundFailure(round.uid, error);
    }
  }
  for (const round of plan.create) {
    try {
      const created = normalizeRound(await api.post<Round>(`/jobs/${jobId}/rounds`, roundCreatePayload(round)));
      result.createdRoundIds[round.uid] = created.id;
    } catch (error) {
      roundFailure(round.uid, error);
    }
  }

  const roundsFailed = Object.keys(result.roundErrors).length > 0;
  if (plan.reorder && !roundsFailed) {
    const order = ctx.draft.rounds.map((r) => r.id ?? result.createdRoundIds[r.uid]).filter((id): id is string => Boolean(id));
    try {
      await api.post(`/jobs/${jobId}/rounds/reorder`, { round_ids: order });
    } catch (error) {
      result.messages.push(`Rounds saved, but their order didn't: ${describeFailure(error)}`);
    }
  }

  // 4. Open the job once its rounds exist.
  if (wantsOpen && result.job.status !== "open" && !roundsFailed) {
    try {
      result.job = await api.patch<Job>(`/jobs/${jobId}`, { status: "open" });
    } catch (error) {
      result.jobErrors.status = describeFailure(error);
    }
  }

  result.ok = Object.keys(result.jobErrors).length === 0 && !roundsFailed && result.messages.length === 0;
  return result;
}
