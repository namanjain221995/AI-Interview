/** Fictional sample data for the /dev/ui design-system showcase only. */

import type { RadarDatum } from "@/components/charts/CompetencyRadar";
import type { TimelineCompetency } from "@/components/charts/ScoreTimelineChart";
import type { Recommendation, RoundType, ScoreSnapshot, SessionStatus } from "@/lib/types";

export interface SampleSession {
  id: string;
  candidate: string;
  job: string;
  round: string;
  roundType: RoundType;
  status: SessionStatus;
  score: number | null;
  recommendation: Recommendation | null;
  minutes: number;
  startedAt: string;
}

const now = Date.now();
const ago = (minutes: number) => new Date(now - minutes * 60_000).toISOString();

export const SAMPLE_SESSIONS: SampleSession[] = [
  { id: "s01", candidate: "Priya Shah", job: "Senior Platform Engineer", round: "Platform internals", roundType: "deep_technical", status: "live", score: null, recommendation: null, minutes: 18, startedAt: ago(18) },
  { id: "s02", candidate: "Daniel Okafor", job: "Senior Platform Engineer", round: "Architecture deep dive", roundType: "system_design", status: "completed", score: 84, recommendation: "strong_proceed", minutes: 41, startedAt: ago(130) },
  { id: "s03", candidate: "Mei Lin", job: "Data Engineer", round: "Pipelines live coding", roundType: "coding", status: "completing", score: null, recommendation: null, minutes: 35, startedAt: ago(52) },
  { id: "s04", candidate: "Lucas Moreau", job: "Frontend Engineer", round: "Frontend fundamentals", roundType: "technical_screening", status: "completed", score: 71, recommendation: "proceed", minutes: 29, startedAt: ago(300) },
  { id: "s05", candidate: "Amara Nwosu", job: "Engineering Manager", round: "Leading through change", roundType: "leadership", status: "completed", score: 58, recommendation: "borderline_evidence", minutes: 38, startedAt: ago(1500) },
  { id: "s06", candidate: "Jonas Berg", job: "Data Engineer", round: "Data modelling screen", roundType: "technical_screening", status: "paused", score: null, recommendation: null, minutes: 12, startedAt: ago(14) },
  { id: "s07", candidate: "Sofia Alvarez", job: "Frontend Engineer", round: "Collaboration and ownership", roundType: "behavioral", status: "completed", score: 77, recommendation: "proceed", minutes: 26, startedAt: ago(2900) },
  { id: "s08", candidate: "Kenji Watanabe", job: "Senior Platform Engineer", round: "Concurrency exercise", roundType: "coding", status: "ready", score: null, recommendation: null, minutes: 0, startedAt: ago(5) },
  { id: "s09", candidate: "Hannah Fischer", job: "Site Reliability Engineer", round: "Incident deep dive", roundType: "deep_technical", status: "completed", score: 46, recommendation: "insufficient_evidence", minutes: 19, startedAt: ago(4400) },
  { id: "s10", candidate: "Omar Haddad", job: "Site Reliability Engineer", round: "Resilient systems design", roundType: "system_design", status: "aborted", score: null, recommendation: null, minutes: 7, startedAt: ago(6100) },
  { id: "s11", candidate: "Grace Kim", job: "Engineering Manager", round: "People leadership", roundType: "managerial", status: "completed", score: 66, recommendation: "human_review", minutes: 33, startedAt: ago(7300) },
  { id: "s12", candidate: "Mateo Rossi", job: "Frontend Engineer", round: "UI component exercise", roundType: "coding", status: "failed", score: null, recommendation: null, minutes: 3, startedAt: ago(8800) },
];

export const RADAR_DATA: RadarDatum[] = [
  { key: "distributed_systems", label: "Distributed systems", score: 8.1, benchmark: 7 },
  { key: "kubernetes", label: "Kubernetes", score: 6.4, benchmark: 7 },
  { key: "system_design", label: "System design", score: 7.8, benchmark: 7 },
  { key: "incident_response", label: "Incident response", score: 5.6, benchmark: 6 },
  { key: "coding", label: "Coding", score: 7.1, benchmark: 7 },
  { key: "communication", label: "Communication", score: 8.4, benchmark: 7 },
];

export const TIMELINE_COMPETENCIES: TimelineCompetency[] = [
  { key: "distributed_systems", label: "Distributed systems" },
  { key: "kubernetes", label: "Kubernetes" },
  { key: "system_design", label: "System design" },
  { key: "incident_response", label: "Incident response" },
];

const series: Record<string, Array<[number, number]>> = {
  distributed_systems: [[3, 5.6], [7, 6.4], [12, 7.2], [19, 7.6], [27, 8.1]],
  kubernetes: [[5, 5.2], [9, 6.1], [14, 5.7], [22, 6.2], [30, 6.4]],
  system_design: [[10, 6.0], [16, 6.9], [21, 7.4], [28, 7.8]],
  incident_response: [[13, 5.0], [18, 4.6], [25, 5.3], [31, 5.6]],
};

export const TIMELINE_SNAPSHOTS: Pick<ScoreSnapshot, "elapsed_seconds" | "competency_key" | "score">[] = Object.entries(series).flatMap(
  ([key, points]) => points.map(([minute, score]) => ({ competency_key: key, elapsed_seconds: minute * 60, score })),
);

export const SPARK_COMPLETED = [3, 5, 4, 6, 8, 7, 9, 6, 8, 11, 9, 12];

export const MARKDOWN_SAMPLE = `### Merge overlapping intervals

Given an array of \`intervals\` where \`intervals[i] = [start, end]\`, merge all overlapping intervals and return the
non-overlapping intervals that cover every input.

- Intervals may arrive **unsorted**.
- Touching intervals such as \`[1, 4]\` and \`[4, 5]\` overlap.

\`\`\`python
def merge(intervals: list[list[int]]) -> list[list[int]]:
    intervals.sort(key=lambda pair: pair[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
\`\`\`

| Input | Output |
| --- | --- |
| \`[[1,3],[2,6],[8,10]]\` | \`[[1,6],[8,10]]\` |
| \`[[1,4],[4,5]]\` | \`[[1,5]]\` |
`;

export const SKILL_OPTIONS = [
  { value: "kubernetes", label: "Kubernetes" },
  { value: "terraform", label: "Terraform" },
  { value: "postgres", label: "PostgreSQL" },
  { value: "kafka", label: "Kafka" },
  { value: "go", label: "Go" },
  { value: "python", label: "Python" },
  { value: "typescript", label: "TypeScript" },
  { value: "react", label: "React" },
  { value: "aws", label: "AWS" },
  { value: "observability", label: "Observability", keywords: "prometheus grafana tracing" },
];

export const JOB_OPTIONS = [
  { value: "platform", label: "Senior Platform Engineer", description: "Infrastructure" },
  { value: "data", label: "Data Engineer", description: "Data" },
  { value: "frontend", label: "Frontend Engineer", description: "Product" },
  { value: "sre", label: "Site Reliability Engineer", description: "Infrastructure" },
  { value: "em", label: "Engineering Manager", description: "Leadership" },
];
