import { FileSearch, Inbox, Plus } from "lucide-react";

import { RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/Card";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { Markdown } from "@/components/ui/Markdown";
import { Progress } from "@/components/ui/Progress";
import { ScrollArea } from "@/components/ui/ScrollArea";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { StatCard, StatGroup } from "@/components/ui/StatCard";
import { Stepper } from "@/components/ui/Stepper";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { toast } from "@/components/ui/Toaster";
import { formatRelativeOrDate, formatScore } from "@/lib/format";
import { ROUND_TYPE_LABELS } from "@/lib/labels";
import { CANDIDATE_STEPS } from "@/lib/types";

import { MARKDOWN_SAMPLE, SAMPLE_SESSIONS, SPARK_COMPLETED, type SampleSession } from "../showcaseData";
import { Row, Section, Specimen } from "../Specimen";

const columns: DataTableColumn<SampleSession>[] = [
  {
    id: "candidate",
    header: "Candidate",
    sortValue: (s) => s.candidate,
    cell: (s) => (
      <div className="flex min-w-0 items-center gap-3">
        <Avatar name={s.candidate} />
        <div className="grid min-w-0">
          <span className="truncate font-medium text-fg">{s.candidate}</span>
          <span className="truncate text-xs text-fg-muted">{s.job}</span>
        </div>
      </div>
    ),
  },
  {
    id: "round",
    header: "Round",
    hideBelow: "md",
    sortValue: (s) => s.round,
    cell: (s) => (
      <div className="grid">
        <span className="text-fg">{s.round}</span>
        <span className="text-xs text-fg-muted">{ROUND_TYPE_LABELS[s.roundType]}</span>
      </div>
    ),
  },
  { id: "status", header: "Status", sortValue: (s) => s.status, cell: (s) => <StatusBadge kind="session" value={s.status} /> },
  {
    id: "score",
    header: "Score",
    numeric: true,
    sortValue: (s) => s.score,
    cell: (s) =>
      s.score === null ? (
        <span className="text-fg-subtle">—</span>
      ) : (
        <span className="font-medium text-fg">
          {formatScore(s.score)}
          <span className="text-xs font-normal text-fg-subtle"> /100</span>
        </span>
      ),
  },
  {
    id: "when",
    header: "Started",
    align: "right",
    hideBelow: "lg",
    sortValue: (s) => s.startedAt,
    cell: (s) => <span className="whitespace-nowrap text-fg-muted">{formatRelativeOrDate(s.startedAt)}</span>,
  },
];

const TRANSCRIPT = [
  ["Maya", "Let's start with the platform you described. How many clusters did you operate?"],
  ["Candidate", "Four production clusters across two regions, about 600 nodes at peak."],
  ["Maya", "How did you handle a regional failover?"],
  ["Candidate", "We kept a warm standby and shifted traffic at the load balancer after health checks failed for 90 seconds."],
  ["Maya", "What would you change about that approach today?"],
  ["Candidate", "I'd automate the database promotion. It was the slowest manual step, around eight minutes."],
  ["Maya", "Walk me through how you measured that."],
  ["Candidate", "We had a game-day runbook with timestamps for every step, and we compared three drills."],
];

export function DataDisplaySection() {
  return (
    <Section
      id="data"
      title="Navigation and data"
      description="Hairlines over heavy boxes. Numbers align in tables, missing values read as a dash, and every list has a loading, empty and error state."
    >
      <Specimen title="Tabs, breadcrumbs and journey">
        <div className="grid gap-7">
          <Breadcrumbs items={[{ label: "Jobs", to: "/dev/ui" }, { label: "Senior Platform Engineer", to: "/dev/ui" }, { label: "System design round" }]} />
          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="transcript">Transcript</TabsTrigger>
              <TabsTrigger value="evidence">Evidence</TabsTrigger>
              <TabsTrigger value="coding">Coding</TabsTrigger>
              <TabsTrigger value="integrity" disabled>
                Integrity
              </TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="text-sm text-fg-muted">
              Underline tabs organise sections of a page.
            </TabsContent>
            <TabsContent value="transcript" className="text-sm text-fg-muted">
              The full conversation with timestamps.
            </TabsContent>
            <TabsContent value="evidence" className="text-sm text-fg-muted">
              Evidence grouped by competency.
            </TabsContent>
            <TabsContent value="coding" className="text-sm text-fg-muted">
              Code snapshots, runs and hints.
            </TabsContent>
          </Tabs>
          <Tabs defaultValue="week" variant="segmented">
            <TabsList aria-label="Period">
              <TabsTrigger value="day">Today</TabsTrigger>
              <TabsTrigger value="week">7 days</TabsTrigger>
              <TabsTrigger value="month">30 days</TabsTrigger>
              <TabsTrigger value="quarter">90 days</TabsTrigger>
            </TabsList>
          </Tabs>
          <Stepper steps={CANDIDATE_STEPS} current="consent" aria-label="Candidate journey" />
          <div className="grid gap-6 md:grid-cols-2">
            <Stepper
              orientation="vertical"
              aria-label="Report pipeline"
              current="scoring"
              steps={[
                { key: "interview", label: "Interview completed", description: "41 minutes, 14 questions" },
                { key: "evaluation", label: "Answers evaluated", description: "38 evidence items" },
                { key: "scoring", label: "Scoring", description: "Combining evidence per competency" },
                { key: "report", label: "Report ready for review" },
              ]}
            />
            <div className="grid content-start gap-4">
              <Progress value={64} label="Resume processing" valueLabel="64%" />
              <Progress value={100} tone="success" label="Answers evaluated" valueLabel="14 of 14" />
              <Progress value={null} tone="accent" label="Generating report" valueLabel="Working" />
              <Progress value={35} tone="signal" size="xs" aria-label="Interview time used" />
            </div>
          </div>
        </div>
      </Specimen>

      <Specimen title="Badges and avatars">
        <div className="grid gap-4">
          <Row>
            <Badge>Neutral</Badge>
            <Badge tone="accent">Accent</Badge>
            <Badge tone="success" dot>
              Open
            </Badge>
            <Badge tone="warning" dot>
              Needs review
            </Badge>
            <Badge tone="danger" dot>
              Failed
            </Badge>
            <Badge tone="signal" dot pulse>
              Live
            </Badge>
            <Badge tone="outline">Archived</Badge>
            <Badge size="sm" tone="neutral">
              Small
            </Badge>
          </Row>
          <Row>
            <StatusBadge kind="session" value="preparing" />
            <StatusBadge kind="session" value="live" />
            <StatusBadge kind="session" value="completed" />
            <StatusBadge kind="interview" value="invited" />
            <StatusBadge kind="resume" value="analyzing" />
            <StatusBadge kind="report" value="ready" />
            <StatusBadge kind="job" value="draft" />
            <StatusBadge kind="run" value="compile_error" />
          </Row>
          <Row>
            {["Priya Shah", "Daniel Okafor", "Mei Lin", "Lucas Moreau", "Amara Nwosu", "Jonas Berg"].map((name, index) => (
              <Avatar key={name} name={name} size={(["xs", "sm", "md", "lg", "xl", "md"] as const)[index]} />
            ))}
            <Avatar name="Acme Robotics" shape="square" size="lg" />
          </Row>
        </div>
      </Specimen>

      <Specimen title="Stats" note="One framed strip with hairline dividers" bodyClassName="p-0 border-0">
        <StatGroup className="rounded-none border-0 shadow-none">
          <StatCard bare label="Completed interviews" value="128" trend={SPARK_COMPLETED} trendLabel="Completed per day" delta={{ label: "+18%", direction: "up", period: "vs last 30 days" }} />
          <StatCard bare label="Waiting for a decision" value="9" hint="Reports ready for a reviewer" />
          <StatCard bare label="Average overall score" value="71" unit="/ 100" delta={{ label: "-2", direction: "down", period: "vs last 30 days" }} info="Mean of overall scores for completed interviews." />
          <StatCard bare label="Median time to decision" value="1.6" unit="days" delta={{ label: "-0.4 days", direction: "down", upIsGood: false, period: "faster" }} />
        </StatGroup>
      </Specimen>

      <Specimen title="Data table" note="Sortable, paginated, keyboard row activation" bodyClassName="p-0">
        <DataTable
          columns={columns}
          data={SAMPLE_SESSIONS}
          getRowId={(s) => s.id}
          pageSize={6}
          defaultSort={{ columnId: "when", direction: "desc" }}
          onRowClick={(s) => toast(`Open ${s.candidate}'s session`)}
          rowLabel={(s) => `Open session for ${s.candidate}`}
          caption="Sample interview sessions"
        />
      </Specimen>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader title="Default card" description="Bordered, quiet elevation." />
          <CardContent className="text-sm text-fg-muted">Use for grouped content inside a page.</CardContent>
          <CardFooter>
            <Button size="sm" variant="outline">
              Secondary
            </Button>
          </CardFooter>
        </Card>
        <Card variant="interactive" tabIndex={0} role="link" aria-label="Open Senior Platform Engineer">
          <CardHeader>
            <div className="grid gap-0.5">
              <CardTitle>Senior Platform Engineer</CardTitle>
              <CardDescription>Infrastructure, Remote</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="flex items-center justify-between">
            <StatusBadge kind="job" value="open" />
            <span className="text-xs text-fg-muted">12 candidates</span>
          </CardContent>
        </Card>
        <Card variant="muted">
          <CardHeader title="Muted card" description="For secondary panels and summaries." />
          <CardContent>
            <RecommendationBadge value="proceed" size="sm" />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Specimen title="Loading" note="Skeletons mirror the final layout">
          <div className="grid gap-4">
            <div className="flex items-center gap-3">
              <Skeleton className="size-8 rounded-full" />
              <div className="grid flex-1 gap-1.5">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-3 w-24" />
              </div>
              <Skeleton className="h-6 w-16" />
            </div>
            <SkeletonText lines={3} />
          </div>
        </Specimen>
        <Specimen title="Scrollable region" bodyClassName="p-0">
          <ScrollArea className="h-48">
            <ol className="grid gap-3 p-5">
              {TRANSCRIPT.map(([speaker, text], index) => (
                <li key={index} className="grid grid-cols-[5.5rem_1fr] gap-3 text-sm">
                  <span className={speaker === "Maya" ? "font-medium text-accent-fg" : "font-medium text-fg"}>{speaker}</span>
                  <span className="text-fg-muted">{text}</span>
                </li>
              ))}
            </ol>
          </ScrollArea>
        </Specimen>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Specimen title="Empty states" note="An invitation to act">
          <EmptyState
            size="sm"
            icon={<Inbox />}
            title="No interviews waiting for review"
            description="Completed interviews appear here when their report is ready."
            actions={
              <Button size="sm" variant="outline" leftIcon={<Plus />}>
                Invite a candidate
              </Button>
            }
          />
        </Specimen>
        <Specimen title="No results">
          <EmptyState size="sm" icon={<FileSearch />} title="Nothing matches “kubernets”" description="Check the spelling or clear a filter." />
        </Specimen>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Specimen title="Markdown" note="GitHub-flavoured, raw HTML ignored">
          <Markdown>{MARKDOWN_SAMPLE}</Markdown>
        </Specimen>
        <Specimen title="Code block">
          <CodeBlock
            language="typescript"
            showLineNumbers
            code={`export function mergeIntervals(intervals: number[][]): number[][] {
  const sorted = [...intervals].sort((a, b) => a[0] - b[0]);
  const merged: number[][] = [];
  for (const [start, end] of sorted) {
    const last = merged.at(-1);
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}`}
          />
        </Specimen>
      </div>
    </Section>
  );
}
