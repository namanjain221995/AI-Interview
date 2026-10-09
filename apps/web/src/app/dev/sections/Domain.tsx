import { BarList } from "@/components/charts/BarList";
import { CompetencyRadar } from "@/components/charts/CompetencyRadar";
import { DistributionBar } from "@/components/charts/DistributionBar";
import { ScoreTimelineChart } from "@/components/charts/ScoreTimelineChart";
import { Sparkline } from "@/components/charts/Sparkline";
import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { EvidenceSignalIcon } from "@/components/domain/EvidenceSignalIcon";
import { IntegritySeverityBadge } from "@/components/domain/IntegritySeverityBadge";
import { RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { ScoreBar } from "@/components/domain/ScoreBar";
import { ScoreRing } from "@/components/domain/ScoreRing";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { formatScore } from "@/lib/format";
import type { EvidenceSignal, Recommendation } from "@/lib/types";

import { RADAR_DATA, SPARK_COMPLETED, TIMELINE_COMPETENCIES, TIMELINE_SNAPSHOTS } from "../showcaseData";
import { Row, Section, Specimen } from "../Specimen";

const RECOMMENDATIONS: Recommendation[] = ["strong_proceed", "proceed", "human_review", "borderline_evidence", "insufficient_evidence"];

const EVIDENCE: Array<{ signal: EvidenceSignal; text: string; competency: string; quote?: string }> = [
  {
    signal: "positive",
    text: "Explained write-through invalidation and why it beat TTL expiry for their read pattern.",
    competency: "System design",
    quote: "We invalidated on write because stale prices were worse than a cache miss.",
  },
  { signal: "positive", text: "Quantified p99 latency before and after: 420 ms to 95 ms.", competency: "Performance" },
  { signal: "negative", text: "Did not describe how a failed migration would be rolled back.", competency: "Reliability" },
  { signal: "neutral", text: "Mentioned Terraform without detail on module structure.", competency: "Infrastructure as code" },
];

export function DomainSection() {
  return (
    <Section
      id="domain"
      title="Evidence and scoring"
      description="Scores always show their scale and the evidence behind them. Suggestions are assistive and always carry the note that a person decides."
    >
      <Specimen title="Scores" note="Banded by the default passing guidance; the number is always printed">
        <div className="grid gap-8 lg:grid-cols-[auto_1fr]">
          <Row className="gap-6">
            <ScoreRing value={84} label="Overall" size="lg" />
            <ScoreRing value={67} label="Technical" />
            <ScoreRing value={52} label="Leadership" />
            <ScoreRing value={null} label="Coding" />
            <ScoreRing value={7.4} max={10} size="sm" ariaLabel="Kubernetes" />
          </Row>
          <div className="grid gap-5 sm:grid-cols-2">
            <ScoreBar label="Distributed systems" value={8.1} confidence={0.84} evidenceCount={9} target={7} />
            <ScoreBar label="Kubernetes" value={6.4} confidence={0.62} evidenceCount={5} target={7} />
            <ScoreBar label="Incident response" value={5.6} confidence={0.31} evidenceCount={2} insufficient target={6} />
            <ScoreBar label="Communication" value={8.4} confidence={0.9} evidenceCount={11} />
          </div>
        </div>
      </Specimen>

      <div className="grid gap-5 lg:grid-cols-2">
        <Specimen title="Suggestion" note="Wording describes the evidence, never the person">
          <div className="grid gap-4">
            {RECOMMENDATIONS.map((value) => (
              <RecommendationBadge key={value} value={value} layout="inline" size="sm" />
            ))}
          </div>
        </Specimen>
        <Specimen title="Signals and labels">
          <div className="grid gap-4">
            <Row>
              <ConfidenceMeter value={0.9} />
              <ConfidenceMeter value={0.6} />
              <ConfidenceMeter value={0.3} />
              <ConfidenceMeter value={0} />
            </Row>
            <Row>
              <DifficultyBadge value="easy" />
              <DifficultyBadge value="medium" />
              <DifficultyBadge value="hard" />
              <DifficultyBadge value="hard" iconOnly />
            </Row>
            <Row>
              <IntegritySeverityBadge severity="info" />
              <IntegritySeverityBadge severity="low" />
              <IntegritySeverityBadge severity="medium" />
              <IntegritySeverityBadge severity="high" />
            </Row>
            <Row>
              <StatusBadge kind="integrity_review" value="unreviewed" />
              <StatusBadge kind="integrity_review" value="noted" />
              <StatusBadge kind="integrity_review" value="dismissed" />
              <StatusBadge kind="integrity_review" value="confirmed_concern" />
            </Row>
          </div>
        </Specimen>
      </div>

      <Specimen title="Evidence" note="Shape and colour both encode the signal; quotes link back to the transcript">
        <ul className="grid divide-y divide-line-subtle">
          {EVIDENCE.map((item) => (
            <li key={item.text} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
              <EvidenceSignalIcon signal={item.signal} className="mt-0.5" />
              <div className="grid min-w-0 flex-1 gap-1">
                <p className="text-sm text-fg">{item.text}</p>
                {item.quote ? <p className="border-l-2 border-line-strong pl-3 text-[13px] text-fg-muted italic">“{item.quote}”</p> : null}
              </div>
              <span className="shrink-0 text-xs font-medium text-fg-muted">{item.competency}</span>
            </li>
          ))}
        </ul>
      </Specimen>

      <div className="grid gap-5 xl:grid-cols-2">
        <Specimen title="Competency radar" note="Target level as a dashed threshold">
          <CompetencyRadar data={RADAR_DATA} title="Competency profile" description="Scores out of 10 against the round's target level." />
        </Specimen>
        <Specimen title="Score timeline" note="Click a legend item to hide its line">
          <ScoreTimelineChart
            snapshots={TIMELINE_SNAPSHOTS}
            competencies={TIMELINE_COMPETENCIES}
            durationSeconds={33 * 60}
            title="Estimates over the interview"
            description="How each competency estimate moved as evidence arrived."
          />
        </Specimen>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Specimen title="Bar list">
          <BarList
            aria-label="Average score by competency"
            valueFormatter={(v) => formatScore(v, 10)}
            max={10}
            items={[
              { key: "communication", label: "Communication", value: 8.4, hint: "31 interviews" },
              { key: "distributed", label: "Distributed systems", value: 7.6, hint: "24 interviews" },
              { key: "system_design", label: "System design", value: 7.1, hint: "19 interviews" },
              { key: "kubernetes", label: "Kubernetes", value: 6.2, hint: "22 interviews" },
            ]}
          />
        </Specimen>
        <Specimen title="Distribution">
          <DistributionBar
            segments={[
              { key: "strong", label: "Strong evidence", value: 14, color: "var(--success)" },
              { key: "proceed", label: "Supports proceeding", value: 31, color: "var(--chart-1)" },
              { key: "review", label: "Closer review", value: 9, color: "var(--chart-muted)" },
              { key: "borderline", label: "Borderline", value: 7, color: "var(--warning)" },
              { key: "insufficient", label: "Not enough evidence", value: 5, color: "var(--border-strong)" },
            ]}
          />
        </Specimen>
        <Specimen title="Sparklines">
          <div className="grid gap-4">
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm text-fg-muted">Completed per day</span>
              <Sparkline data={SPARK_COMPLETED} label="Completed interviews per day, trending up" />
            </div>
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm text-fg-muted">Median turn latency</span>
              <Sparkline data={[0.9, 0.82, 0.86, 0.74, 0.7, 0.71, 0.66, 0.62, 0.64, 0.6]} color="var(--chart-3)" />
            </div>
          </div>
        </Specimen>
      </div>
    </Section>
  );
}
