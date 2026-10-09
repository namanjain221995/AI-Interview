/**
 * Deterministic demo data for the recruiter review screens: a completed
 * Senior DevOps interview (Jane Doe, mirroring samples/resumes/README.md), a
 * live Backend interview, a session list, analytics and an integrity queue.
 * Used by component tests and the Playwright screenshot harness; never
 * imported by application code.
 */

import type {
  AnalyticsFunnel,
  CodeRunRecord,
  CodeSnapshotRecord,
  CodingSessionDetail,
  CompetencyAnalytics,
  EvidenceRecord,
  Job,
  MeResponse,
  ModelUsage,
  Page,
  Recommendation,
  Report,
  ReviewDecision,
  ReviewDecisionValue,
  RoundConfig,
  ScoreRecord,
  ScoreSnapshot,
  SessionQuestion,
  SessionStatus,
  SessionSummary,
  TimelineEvent,
} from "@/lib/types";

import type {
  AnalyticsOverviewView,
  CompletionAnalyticsDTO,
  HintUsageAnalyticsDTO,
  IntegrityAnalyticsDTO,
  IntegrityEventView,
  ScoreDistributionsDTO,
  SessionDetailView,
  SessionScoresView,
  TranscriptEntry,
} from "../api/types";

// ------------------------------------------------------------------ helpers

function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

const BASE = Date.parse("2026-10-06T14:02:00Z");
const iso = (offsetSeconds: number) => new Date(BASE + offsetSeconds * 1000).toISOString();
const ORG = "org-demo";

export const DEMO_IDS = {
  completed: "6f1c2a9e-3b7d-4c11-9a2e-5d8f0b1c2a01",
  live: "7a2d3b0f-4c8e-4d22-8b3f-6e9a1c2d3b02",
  pending: "8b3e4c1a-5d9f-4e33-9c4a-7fab2d3e4c03",
  candidate: "c-jane",
  liveCandidate: "c-marcus",
  job: "job-devops",
  round: "round-devops-combined",
};

const JOBS = [
  { id: "job-devops", title: "Senior DevOps Engineer" },
  { id: "job-backend", title: "Backend Engineer (Python)" },
  { id: "job-frontend", title: "Frontend Engineer (React)" },
  { id: "job-ml", title: "ML Engineer (LLM)" },
];

const ROUNDS = {
  "job-devops": { id: "round-devops-combined", name: "Combined AI Round", round_type: "combined" as const },
  "job-backend": { id: "round-backend-tech", name: "Technical screening", round_type: "technical_screening" as const },
  "job-frontend": { id: "round-frontend-tech", name: "Technical screening", round_type: "technical_screening" as const },
  "job-ml": { id: "round-ml-deep", name: "Deep technical", round_type: "deep_technical" as const },
};

// --------------------------------------------------------------------- me

export const demoMe: MeResponse = {
  user: {
    id: "u-sam",
    organization_id: ORG,
    email: "sam.rivera@demo.example.com",
    full_name: "Sam Rivera",
    role: "RECRUITER",
    is_active: true,
    last_login_at: iso(-3600),
    created_at: iso(-86400 * 90),
    updated_at: iso(-3600),
  },
  organization: { id: ORG, name: "Demo Corp", slug: "demo-corp" },
};

// ------------------------------------------------------------ round config

const COMPETENCIES: Array<[string, string, number, boolean]> = [
  ["kubernetes", "Kubernetes", 1.5, true],
  ["aws", "AWS", 1.2, true],
  ["terraform", "Terraform", 1.0, true],
  ["ci_cd", "CI/CD", 0.8, true],
  ["observability", "Observability", 0.8, true],
  ["data_structures", "Data structures", 0.8, true],
  ["ownership", "Ownership", 0.6, true],
  ["incident_response", "Incident response", 0.6, false],
  ["cloud_cost_optimization", "Cloud cost optimization", 0.5, false],
  ["leadership", "Leadership", 0.5, false],
  ["communication", "Communication", 0.4, false],
];

export const demoRoundConfig = {
  round_type: "combined",
  name: "Combined AI Round",
  duration_minutes: 30,
  max_duration_minutes: 35,
  expected_difficulty: "medium",
  difficulty_distribution: { easy: 30, medium: 40, hard: 30 },
  competencies: COMPETENCIES.map(([key, name, weight, required]) => ({
    key,
    name,
    weight,
    required,
    min_evidence: required ? 3 : 1,
    target_difficulty: null,
    focus_areas: [],
  })),
  rubric: [
    { key: "technical_knowledge", label: "Technical knowledge", weight: 30, competency_keys: ["kubernetes", "aws", "terraform", "ci_cd", "observability"], dimensions: ["correctness", "depth"], description: null },
    { key: "practical_experience", label: "Practical experience", weight: 15, competency_keys: ["incident_response", "cloud_cost_optimization"], dimensions: ["practical_experience", "specificity"], description: null },
    { key: "coding", label: "Coding", weight: 20, competency_keys: ["data_structures"], dimensions: ["code_quality"], description: null },
    { key: "problem_solving", label: "Problem solving", weight: 10, competency_keys: [], dimensions: ["reasoning"], description: null },
    { key: "ownership_leadership", label: "Ownership and leadership", weight: 15, competency_keys: ["ownership", "leadership"], dimensions: [], description: null },
    { key: "communication", label: "Communication", weight: 10, competency_keys: ["communication"], dimensions: ["communication_clarity"], description: null },
  ],
} as unknown as RoundConfig;

// -------------------------------------------------------------- questions

interface QSpec {
  id: string;
  seq: number;
  parent?: string;
  t: number;
  kind: SessionQuestion["kind"];
  action: SessionQuestion["action"];
  segment: SessionQuestion["segment"];
  keys: string[];
  difficulty: SessionQuestion["difficulty"];
  text: string;
  focus: string;
  reason: string;
  expected: string[];
  claim?: string;
  answer: string;
  answerSeconds: number;
  quality: NonNullable<SessionQuestion["answers"][number]["evaluation"]>["overall_quality"];
  dims: Partial<Record<string, number>>;
  summary: string;
  missing?: string[];
  unsupported?: string[];
  outcomes?: string[];
  evidence: Array<{ key: string; signal: EvidenceRecord["signal"]; dimension: string; text: string; quote?: string; strength: number; confidence: number }>;
  claimVerification?: { claim_id: string; status: "verified" | "partially_verified" | "contradicted" | "unverified"; note: string };
  contradiction?: { statement: string; conflicts_with: string; severity: "low" | "medium" | "high" };
  interrupted?: boolean;
}

const Q: QSpec[] = [
  {
    id: "q-01",
    seq: 1,
    t: 38,
    kind: "resume_claim",
    action: "verify_claim",
    segment: "resume_validation",
    keys: ["kubernetes"],
    difficulty: "medium",
    text: "To start, could you walk me through the Kubernetes platform you built at Bluepeak? How many clusters were there, and which parts did you personally design?",
    focus: "Bluepeak EKS platform: scale and personal design role",
    reason: "The resume claims a production platform for 10,000 users; this checks architecture and her own role.",
    expected: ["cluster topology", "node groups", "autoscaling", "personal ownership"],
    claim: "c-platform",
    answer:
      "Sure. We ran three EKS clusters, one each for dev, staging and production, around a hundred and twenty nodes at peak in prod. I designed the node group layout, split by workload class, and I owned the autoscaling setup. We started with Cluster Autoscaler and I moved us to Karpenter last year, which cut scale-up time from about four minutes to under one. Networking and the service mesh were mostly a colleague's work, I reviewed it.",
    answerSeconds: 62,
    quality: "strong",
    dims: { correctness: 8.2, depth: 7.8, specificity: 8.5, practical_experience: 8.4, communication_clarity: 8 },
    summary: "Clear, specific description of a three-cluster EKS setup with an honest split of what she owned versus reviewed.",
    outcomes: ["Scale-up time cut from ~4 min to under 1 min after moving to Karpenter"],
    evidence: [
      { key: "kubernetes", signal: "positive", dimension: "practical_experience", text: "Designed node groups by workload class and owned cluster autoscaling, including a migration to Karpenter.", quote: "I designed the node group layout, split by workload class, and I owned the autoscaling setup", strength: 0.82, confidence: 0.86 },
      { key: "kubernetes", signal: "positive", dimension: "specificity", text: "Quantified the autoscaling improvement (about 4 minutes to under 1 minute).", quote: "cut scale-up time from about four minutes to under one", strength: 0.74, confidence: 0.8 },
      { key: "communication", signal: "positive", dimension: "communication_clarity", text: "Separated her own work from a colleague's without being asked.", strength: 0.55, confidence: 0.7 },
    ],
    claimVerification: { claim_id: "c-platform", status: "verified", note: "Described three EKS clusters (~120 nodes) and her design ownership consistently with the resume." },
  },
  {
    id: "q-02",
    seq: 2,
    parent: "q-01",
    t: 132,
    kind: "followup",
    action: "increase_difficulty",
    segment: "technical",
    keys: ["kubernetes"],
    difficulty: "hard",
    text: "When an availability zone had problems, what actually happened to your workloads, and what did you change afterwards?",
    focus: "AZ failure handling",
    reason: "Strong first answer; raising difficulty on availability.",
    expected: ["topology spread", "pod disruption budgets", "stateful workloads", "capacity headroom"],
    answer:
      "We had a partial outage in us-east-1b once. Most stateless services were fine because we use topology spread constraints across three zones and pod disruption budgets, but a couple of services had all replicas in one zone because their spread was only a preference. Afterwards we made zone spread a hard requirement for anything tier one and added about twenty percent headroom per zone. I'm honestly less sure how our Kafka brokers behaved, that was owned by the data team.",
    answerSeconds: 58,
    quality: "adequate",
    dims: { correctness: 7.6, depth: 7, specificity: 7.4, practical_experience: 7.8, reasoning: 7.2 },
    summary: "Good account of the stateless side and the remediation; stateful workloads were outside her experience.",
    missing: ["Stateful workload behaviour (Kafka, databases) during the zone failure"],
    evidence: [
      { key: "kubernetes", signal: "positive", dimension: "depth", text: "Explained topology spread constraints and PDBs, and why a soft preference failed during a zone outage.", quote: "their spread was only a preference", strength: 0.78, confidence: 0.82 },
      { key: "incident_response", signal: "positive", dimension: "practical_experience", text: "Turned an incident into a concrete fix: hard zone spread for tier-one services plus 20% headroom.", quote: "we made zone spread a hard requirement for anything tier one", strength: 0.66, confidence: 0.72 },
      { key: "kubernetes", signal: "negative", dimension: "completeness", text: "Could not describe how stateful workloads behaved during the zone failure.", quote: "I'm honestly less sure how our Kafka brokers behaved", strength: 0.5, confidence: 0.74 },
    ],
  },
  {
    id: "q-03",
    seq: 3,
    t: 238,
    kind: "technical",
    action: "change_topic",
    segment: "technical",
    keys: ["aws"],
    difficulty: "medium",
    text: "Let's shift to AWS. How did you structure IAM across your four accounts, and how did pods get the permissions they needed?",
    focus: "IAM across accounts and pod permissions",
    reason: "AWS is required and had no evidence yet.",
    expected: ["IRSA", "least privilege", "cross-account roles", "permission boundaries"],
    answer:
      "Each account has its own roles managed in Terraform, and engineers assume roles through SSO into the account they need. For pods we use IAM roles for service accounts, so each service account maps to a role with only the policies that service needs. We added permission boundaries so a team can't create a role more powerful than its boundary. Cross-account access goes through a small set of roles in the shared services account.",
    answerSeconds: 49,
    quality: "adequate",
    dims: { correctness: 7.4, depth: 6.6, specificity: 6.8, practical_experience: 7 },
    summary: "Solid, conventional IAM design with IRSA and permission boundaries; light on how access was reviewed over time.",
    missing: ["How access was audited or reviewed"],
    evidence: [
      { key: "aws", signal: "positive", dimension: "correctness", text: "Used IRSA so each service account maps to a least-privilege role.", quote: "each service account maps to a role with only the policies that service needs", strength: 0.7, confidence: 0.8 },
      { key: "aws", signal: "positive", dimension: "depth", text: "Applied permission boundaries to stop teams escalating privileges.", strength: 0.62, confidence: 0.74 },
    ],
  },
  {
    id: "q-04",
    seq: 4,
    parent: "q-03",
    t: 330,
    kind: "followup",
    action: "increase_difficulty",
    segment: "technical",
    keys: ["aws"],
    difficulty: "hard",
    text: "Walk me through how a request from the internet reached a service running in a private subnet, all the way to the pod.",
    focus: "Ingress path from internet to pod",
    reason: "Probe networking depth behind the IAM answer.",
    expected: ["load balancer", "target type", "security groups", "NAT", "VPC endpoints"],
    answer:
      "It goes to an application load balancer in the public subnets, managed by the AWS load balancer controller, and then to the pods. We used IP target mode so it goes straight to pod IPs. Security groups for pods were on for some services. Outbound I think went through NAT gateways, but I'd have to check how the VPC endpoints were set up.",
    answerSeconds: 41,
    quality: "partial",
    dims: { correctness: 6.2, depth: 5.2, specificity: 5.6, reasoning: 5.8 },
    summary: "Knew the ingress path at a high level (ALB controller, IP targets) but was unsure about egress and VPC endpoints.",
    missing: ["Egress design (NAT vs VPC endpoints)", "DNS and TLS termination"],
    evidence: [
      { key: "aws", signal: "positive", dimension: "correctness", text: "Correctly described ALB with IP target mode via the load balancer controller.", quote: "We used IP target mode so it goes straight to pod IPs", strength: 0.5, confidence: 0.72 },
      { key: "aws", signal: "negative", dimension: "depth", text: "Unsure how egress and VPC endpoints were configured.", quote: "I'd have to check how the VPC endpoints were set up", strength: 0.62, confidence: 0.78 },
    ],
  },
  {
    id: "q-05",
    seq: 5,
    t: 410,
    kind: "technical",
    action: "change_topic",
    segment: "technical",
    keys: ["terraform"],
    difficulty: "medium",
    text: "You mention more than sixty Terraform modules across four accounts. How did you keep changes safe?",
    focus: "Safe Terraform change management",
    reason: "Required competency; resume claim about module scale.",
    expected: ["remote state locking", "plan review", "module versioning", "policy checks"],
    answer:
      "State lives in S3 with DynamoDB locking, one state per account and component. Every pull request runs a plan in CI and posts it as a comment, and applies only happen from the pipeline after approval. Modules are versioned with tags, so an account pins a version and upgrades deliberately. We added OPA policies with conftest to block things like public buckets before apply.",
    answerSeconds: 47,
    quality: "strong",
    dims: { correctness: 8.4, depth: 7.8, specificity: 8, practical_experience: 8.2 },
    summary: "Mature change-management practice with locking, CI plans, version pinning and policy checks.",
    evidence: [
      { key: "terraform", signal: "positive", dimension: "practical_experience", text: "Plans run on every pull request and applies only run from the approved pipeline.", quote: "applies only happen from the pipeline after approval", strength: 0.8, confidence: 0.84 },
      { key: "terraform", signal: "positive", dimension: "depth", text: "Pins module versions per account and gates applies with OPA policies.", strength: 0.72, confidence: 0.78 },
    ],
  },
  {
    id: "q-06",
    seq: 6,
    t: 512,
    kind: "technical",
    action: "change_topic",
    segment: "technical",
    keys: ["ci_cd"],
    difficulty: "medium",
    text: "You brought deployment lead time down from two days to forty-five minutes. What contributed most, and how did rollbacks work?",
    focus: "Lead-time reduction and rollback",
    reason: "Resume claim with a measurable outcome.",
    expected: ["bottleneck analysis", "progressive delivery", "automated rollback", "measurement"],
    answer:
      "The biggest win was removing the manual change board for low-risk services, which alone saved about a day. Then we moved to Argo CD with Argo Rollouts, canaries at ten and fifty percent with analysis on error rate and latency from Prometheus. If the analysis fails it rolls back automatically. We measured lead time from merge to production in our deploy dashboard.",
    answerSeconds: 44,
    quality: "adequate",
    dims: { correctness: 7.6, depth: 6.8, specificity: 7.6, practical_experience: 7.4 },
    summary: "Identified the real bottleneck (process, not tooling) and described automated canary rollback.",
    outcomes: ["Lead time from 2 days to 45 minutes, measured merge-to-production"],
    evidence: [
      { key: "ci_cd", signal: "positive", dimension: "practical_experience", text: "Canary rollouts with automated analysis and rollback on error rate and latency.", quote: "If the analysis fails it rolls back automatically", strength: 0.72, confidence: 0.8 },
      { key: "ownership", signal: "positive", dimension: "reasoning", text: "Recognised the change board, not tooling, as the largest bottleneck.", strength: 0.6, confidence: 0.66 },
    ],
  },
  {
    id: "q-07",
    seq: 7,
    t: 600,
    kind: "resume_claim",
    action: "verify_claim",
    segment: "resume_validation",
    keys: ["cloud_cost_optimization"],
    difficulty: "medium",
    text: "Your resume says you reduced cloud costs by forty percent. How did you measure that?",
    focus: "40% cost reduction claim",
    reason: "High-priority claim to verify.",
    expected: ["baseline", "time range", "normalisation for growth", "contributing changes"],
    claim: "c-cost",
    answer: "We looked at the AWS bill and it went down a lot after a bunch of optimisations, things like right-sizing and better instance choices.",
    answerSeconds: 14,
    quality: "vague",
    dims: { specificity: 3.2, depth: 3.6, practical_experience: 4.8 },
    summary: "No baseline, time range or normalisation; the interviewer asked for specifics.",
    missing: ["Baseline spend", "Time range", "Normalisation for traffic growth"],
    unsupported: ["40% reduction"],
    evidence: [{ key: "cloud_cost_optimization", signal: "negative", dimension: "specificity", text: "Gave no baseline, time range or normalisation for the 40% claim.", quote: "it went down a lot after a bunch of optimisations", strength: 0.58, confidence: 0.76 }],
  },
  {
    id: "q-08",
    seq: 8,
    parent: "q-07",
    t: 640,
    kind: "followup",
    action: "request_specificity",
    segment: "resume_validation",
    keys: ["cloud_cost_optimization"],
    difficulty: "medium",
    text: "What was the approximate monthly spend before and after, and which change contributed most to the reduction?",
    focus: "Cost baseline and biggest contributor",
    reason: "Vague first answer; asking for the missing numbers.",
    expected: ["before/after spend", "biggest contributor"],
    claim: "c-cost",
    answer:
      "Roughly a hundred and eighty thousand a month down to about a hundred and ten, over two quarters. Savings Plans were the biggest part, maybe half, then right-sizing node groups with Karpenter. Traffic grew during that time too, but I didn't normalise for it.",
    answerSeconds: 29,
    quality: "partial",
    dims: { specificity: 6.4, depth: 5.4, practical_experience: 6.2 },
    summary: "Gave plausible numbers and contributors; the 40% figure isn't normalised for growth.",
    missing: ["Growth normalisation"],
    evidence: [
      { key: "cloud_cost_optimization", signal: "positive", dimension: "specificity", text: "Gave before/after monthly spend and the main contributors (Savings Plans, right-sizing).", quote: "a hundred and eighty thousand a month down to about a hundred and ten", strength: 0.6, confidence: 0.72 },
      { key: "cloud_cost_optimization", signal: "negative", dimension: "reasoning", text: "Did not normalise the reduction for traffic growth.", quote: "I didn't normalise for it", strength: 0.46, confidence: 0.7 },
    ],
    claimVerification: { claim_id: "c-cost", status: "partially_verified", note: "Numbers given ($180k to $110k per month, about 39%) but not normalised for growth." },
  },
  {
    id: "q-09",
    seq: 9,
    t: 700,
    kind: "coding",
    action: "start_coding",
    segment: "coding",
    keys: ["data_structures"],
    difficulty: "easy",
    text: "Let's do a short coding exercise. The problem is on your screen: find the service with the most error log lines.",
    focus: "Coding: most frequent error service",
    reason: "Coding point in the plan reached.",
    expected: ["hash map counting", "tie-breaking", "edge cases"],
    answer: "Okay, I'll use a dictionary to count errors per service and then pick the max, with ties by name.",
    answerSeconds: 9,
    quality: "adequate",
    dims: { reasoning: 7, correctness: 7 },
    summary: "Explained the counting approach before coding.",
    evidence: [],
  },
  {
    id: "q-10",
    seq: 10,
    t: 1240,
    kind: "followup",
    action: "ask_followup",
    segment: "coding",
    keys: ["data_structures"],
    difficulty: "medium",
    text: "Your solution passes the tests. What's its time complexity, and what happens if the log file doesn't fit in memory?",
    focus: "Complexity and streaming",
    reason: "Follow-up on the submitted solution.",
    expected: ["O(n)", "streaming", "bounded memory"],
    answer:
      "It's linear in the number of lines, and memory is linear in the number of distinct services, not lines. If the file is huge I'd stream it line by line instead of reading it all, the counter stays small. For many files I'd count per file in parallel and merge the counters.",
    answerSeconds: 33,
    quality: "strong",
    dims: { correctness: 8.4, reasoning: 8.2, depth: 7.4 },
    summary: "Correct complexity analysis and a sensible streaming and merge strategy.",
    evidence: [{ key: "data_structures", signal: "positive", dimension: "reasoning", text: "Correct complexity analysis; proposed streaming and merging per-file counters.", quote: "memory is linear in the number of distinct services, not lines", strength: 0.76, confidence: 0.84 }],
  },
  {
    id: "q-11",
    seq: 11,
    t: 1350,
    kind: "technical",
    action: "change_topic",
    segment: "technical",
    keys: ["observability", "incident_response"],
    difficulty: "medium",
    text: "You defined SLOs for twelve services. How did you choose the targets, and what alerted when they were at risk?",
    focus: "SLO design and alerting",
    reason: "Observability is required and untested.",
    expected: ["user journeys", "error budgets", "burn-rate alerts"],
    answer:
      "We started from user journeys, like checkout and search, and picked availability and latency SLIs at the load balancer. Targets came from three months of history, a bit tighter than what we achieved. Alerts were multi-window burn-rate alerts, fast and slow, so on-call only got paged when the budget was actually at risk.",
    answerSeconds: 46,
    quality: "adequate",
    dims: { correctness: 7.4, depth: 6.8, practical_experience: 7.2 },
    summary: "Sound SLO approach with burn-rate alerting; didn't cover how error budgets influenced release decisions.",
    missing: ["Error budget policy"],
    evidence: [
      { key: "observability", signal: "positive", dimension: "depth", text: "Multi-window burn-rate alerts tied to SLOs to reduce paging noise.", quote: "multi-window burn-rate alerts, fast and slow", strength: 0.68, confidence: 0.78 },
      { key: "observability", signal: "negative", dimension: "completeness", text: "Did not describe an error budget policy for releases.", strength: 0.38, confidence: 0.62 },
      { key: "incident_response", signal: "positive", dimension: "practical_experience", text: "Designed alerting so on-call is paged only when the error budget is at risk.", strength: 0.5, confidence: 0.6 },
    ],
  },
  {
    id: "q-12",
    seq: 12,
    t: 1480,
    kind: "behavioral",
    action: "change_topic",
    segment: "leadership",
    keys: ["leadership", "ownership"],
    difficulty: "medium",
    text: "Tell me about a time someone on your team disagreed with a technical decision you made. What happened?",
    focus: "Handling disagreement as a lead",
    reason: "Resume claims leading a team of three.",
    expected: ["situation", "her actions", "outcome", "reflection"],
    answer:
      "When I pushed for Karpenter, one engineer worried about losing predictability for our batch jobs. I asked him to write up the risks, we ran both side by side on staging for two weeks, and his concern was right for one workload, so we kept a dedicated node group for it. I think I could have involved him earlier rather than presenting a near-final plan.",
    answerSeconds: 41,
    quality: "adequate",
    dims: { practical_experience: 7.2, reasoning: 7, consistency: 7.4, communication_clarity: 7.6 },
    summary: "Concrete example with a trial, a compromise and genuine reflection.",
    evidence: [
      { key: "leadership", signal: "positive", dimension: "practical_experience", text: "Ran a side-by-side trial to settle a disagreement and adopted the engineer's point for one workload.", quote: "his concern was right for one workload, so we kept a dedicated node group", strength: 0.62, confidence: 0.7 },
      { key: "ownership", signal: "positive", dimension: "consistency", text: "Reflected honestly on involving the team earlier.", quote: "I could have involved him earlier", strength: 0.52, confidence: 0.64 },
    ],
  },
  {
    id: "q-13",
    seq: 13,
    t: 1640,
    kind: "resume_claim",
    action: "verify_contradiction",
    segment: "resume_validation",
    keys: ["communication"],
    difficulty: "easy",
    text: "Earlier you mentioned joining Bluepeak in January 2023, and your resume lists Harborline until March. Help me reconcile those dates.",
    focus: "Overlapping employment dates",
    reason: "Resume shows a three-month overlap; asked neutrally.",
    expected: ["explanation"],
    claim: "c-dates",
    answer:
      "Yes, that was a part-time handover. Harborline asked me to stay on two days a week until my replacement was ramped up, and Bluepeak agreed to it. I should probably make that clearer on the resume.",
    answerSeconds: 21,
    quality: "adequate",
    dims: { consistency: 8, communication_clarity: 8 },
    summary: "Plausible, consistent explanation (part-time handover).",
    evidence: [{ key: "communication", signal: "positive", dimension: "consistency", text: "Explained the overlapping dates clearly and consistently.", strength: 0.5, confidence: 0.66 }],
    contradiction: { statement: "Joined Bluepeak in January 2023", conflicts_with: "Resume lists Harborline employment until March 2023", severity: "low" },
    claimVerification: { claim_id: "c-dates", status: "verified", note: "Part-time handover explains the overlap." },
  },
];

const DURATION = 1838;

function buildQuestions(): { questions: SessionQuestion[]; evidence: EvidenceRecord[]; transcript: TranscriptEntry[] } {
  const evidence: EvidenceRecord[] = [];
  const transcript: TranscriptEntry[] = [];
  let seq = 1;
  transcript.push({
    id: "t-intro",
    session_id: DEMO_IDS.completed,
    seq: seq++,
    speaker: "interviewer",
    kind: "final",
    text: "Hi Jane, I'm Maya, the AI interviewer for the Senior DevOps Engineer role at Demo Corp. This conversation takes about thirty minutes, with a short coding exercise in the middle. Feel free to ask me to repeat anything or to take a moment to think.",
    start_ms: 2_000,
    end_ms: 18_000,
    confidence: null,
    question_id: null,
    answer_id: null,
    interrupted: false,
    created_at: iso(2),
  });

  const questions: SessionQuestion[] = Q.map((spec) => {
    const answerId = `a-${spec.id.slice(2)}`;
    const askedMs = spec.t * 1000;
    const answerStart = askedMs + Math.min(14_000, 4_000 + spec.text.length * 40);
    const answerEnd = answerStart + spec.answerSeconds * 1000;
    transcript.push({
      id: `t-${spec.id}-q`,
      session_id: DEMO_IDS.completed,
      seq: seq++,
      speaker: "interviewer",
      kind: "final",
      text: spec.text,
      start_ms: askedMs,
      end_ms: answerStart - 800,
      confidence: null,
      question_id: spec.id,
      answer_id: null,
      interrupted: spec.id === "q-04",
      created_at: iso(spec.t),
    });
    // Split the answer into two segments so the transcript shows merged turns.
    const sentences = spec.answer.split(/(?<=\.)\s+/);
    const half = Math.max(1, Math.ceil(sentences.length / 2));
    [sentences.slice(0, half).join(" "), sentences.slice(half).join(" ")].filter(Boolean).forEach((text, i) => {
      transcript.push({
        id: `t-${spec.id}-a${i}`,
        session_id: DEMO_IDS.completed,
        seq: seq++,
        speaker: "candidate",
        kind: "final",
        text,
        start_ms: i === 0 ? answerStart : answerStart + Math.round((answerEnd - answerStart) * 0.55),
        end_ms: i === 0 ? answerStart + Math.round((answerEnd - answerStart) * 0.5) : answerEnd,
        confidence: spec.id === "q-08" && i === 0 ? 0.54 : 0.93,
        question_id: spec.id,
        answer_id: answerId,
        interrupted: false,
        created_at: iso(spec.t + 10),
      });
    });

    const items: EvidenceRecord[] = spec.evidence.map((e, i) => {
      const quoteAt = e.quote ? spec.answer.indexOf(e.quote) : -1;
      const startMs = quoteAt >= 0 ? answerStart + Math.round((quoteAt / spec.answer.length) * (answerEnd - answerStart)) : answerStart;
      return {
        id: `ev-${spec.id.slice(2)}-${i + 1}`,
        session_id: DEMO_IDS.completed,
        competency_key: e.key,
        question_id: spec.id,
        answer_id: answerId,
        evaluation_id: `eval-${spec.id.slice(2)}`,
        coding_session_id: null,
        source: "answer",
        signal: e.signal,
        dimension: e.dimension,
        evidence: e.text,
        strength: e.strength,
        confidence: e.confidence,
        difficulty: spec.difficulty,
        elapsed_seconds: Math.round(answerEnd / 1000) + 4,
        transcript_ref: e.quote ? { answer_id: answerId, start_ms: startMs, end_ms: startMs + 4000, quote: e.quote } : { answer_id: answerId },
        created_at: iso(Math.round(answerEnd / 1000) + 6),
      };
    });
    evidence.push(...items);

    return {
      id: spec.id,
      session_id: DEMO_IDS.completed,
      sequence: spec.seq,
      parent_question_id: spec.parent ?? null,
      bank_question_id: spec.seq % 3 === 0 ? `bank-${spec.seq}` : null,
      coding_question_id: spec.kind === "coding" ? "cq-most-frequent-error-service" : null,
      kind: spec.kind,
      action: spec.action,
      source: spec.parent ? "followup" : spec.seq % 3 === 0 ? "curated" : "generated",
      segment: spec.segment,
      topic: spec.focus,
      competency_keys: spec.keys,
      difficulty: spec.difficulty,
      text: spec.text,
      spoken_text: spec.text,
      question_focus: spec.focus,
      reason: spec.reason,
      expected_evidence: spec.expected,
      claim_id: spec.claim ?? null,
      interrupted: spec.id === "q-04",
      asked_at: iso(spec.t),
      elapsed_seconds: spec.t,
      answers: [
        {
          id: answerId,
          question_id: spec.id,
          sequence: spec.seq,
          transcript_text: spec.answer,
          typed: false,
          started_ms: answerStart,
          ended_ms: answerEnd,
          duration_ms: answerEnd - answerStart,
          word_count: spec.answer.split(/\s+/).length,
          stt_confidence: 0.93,
          interrupted_ai: spec.id === "q-04",
          status: "evaluated",
          audio_media_id: `media-audio-${spec.id}`,
          evaluation: {
            id: `eval-${spec.id.slice(2)}`,
            answer_id: answerId,
            question_id: spec.id,
            session_id: DEMO_IDS.completed,
            overall_quality: spec.quality,
            correctness: spec.dims.correctness ?? null,
            relevance: spec.dims.relevance ?? null,
            completeness: spec.dims.completeness ?? null,
            depth: spec.dims.depth ?? null,
            specificity: spec.dims.specificity ?? null,
            practical_experience: spec.dims.practical_experience ?? null,
            reasoning: spec.dims.reasoning ?? null,
            consistency: spec.dims.consistency ?? null,
            architecture_awareness: null,
            tradeoff_understanding: null,
            communication_clarity: spec.dims.communication_clarity ?? null,
            confidence: spec.quality === "vague" ? 0.62 : 0.78,
            is_fallback: false,
            evaluation: {
              summary: spec.summary,
              missing_details: spec.missing ?? [],
              unsupported_claims: spec.unsupported ?? [],
              measurable_outcomes: spec.outcomes ?? [],
              contradictions: spec.contradiction ? [spec.contradiction] : [],
              claim_verifications: spec.claimVerification ? [spec.claimVerification] : [],
            },
            created_at: iso(Math.round(answerEnd / 1000) + 6),
          },
          evidence: items,
        },
      ],
    };
  });

  // Coding evidence (from the coding evaluation) and hint usage, recorded transparently.
  evidence.push(
    {
      id: "ev-coding-1",
      session_id: DEMO_IDS.completed,
      competency_key: "data_structures",
      question_id: "q-09",
      answer_id: null,
      evaluation_id: null,
      coding_session_id: "cs-1",
      source: "coding",
      signal: "positive",
      dimension: "code_quality",
      evidence: "Clean single-pass counting with Counter and deterministic tie-breaking by name.",
      strength: 0.74,
      confidence: 0.82,
      difficulty: "easy",
      elapsed_seconds: 1215,
      transcript_ref: {},
      created_at: iso(1215),
    },
    {
      id: "ev-coding-2",
      session_id: DEMO_IDS.completed,
      competency_key: "data_structures",
      question_id: "q-09",
      answer_id: null,
      evaluation_id: null,
      coding_session_id: "cs-1",
      source: "coding",
      signal: "negative",
      dimension: "edge_case_handling",
      evidence: "First version crashed on malformed log lines; fixed after a hint.",
      strength: 0.4,
      confidence: 0.76,
      difficulty: "easy",
      elapsed_seconds: 1015,
      transcript_ref: {},
      created_at: iso(1015),
    },
    {
      id: "ev-hint-1",
      session_id: DEMO_IDS.completed,
      competency_key: "data_structures",
      question_id: "q-09",
      answer_id: null,
      evaluation_id: null,
      coding_session_id: "cs-1",
      source: "hint_usage",
      signal: "neutral",
      dimension: "assistance",
      evidence: "Used one hint (level 1: consider lines that don't match the expected format).",
      strength: 0.3,
      confidence: 1,
      difficulty: "easy",
      elapsed_seconds: 980,
      transcript_ref: {},
      created_at: iso(980),
    },
  );

  transcript.push({
    id: "t-close",
    session_id: DEMO_IDS.completed,
    seq: seq++,
    speaker: "interviewer",
    kind: "final",
    text: "Thanks, Jane, that's everything from my side. A member of the hiring team will review the interview and get back to you. Have a good rest of your day.",
    start_ms: 1_800_000,
    end_ms: 1_812_000,
    confidence: null,
    question_id: null,
    answer_id: null,
    interrupted: false,
    created_at: iso(1800),
  });
  return { questions, evidence, transcript };
}

const built = buildQuestions();
export const demoQuestions = built.questions;
export const demoEvidence = built.evidence;
export const demoTranscript = built.transcript;

// ------------------------------------------------------------------- scores

const FINAL: Record<string, [number, number, number, boolean, string[]]> = {
  kubernetes: [7.8, 0.82, 5, true, []],
  aws: [6.4, 0.71, 4, true, ["Egress and VPC endpoint design", "Access reviews"]],
  terraform: [7.6, 0.66, 2, false, ["Drift detection"]],
  ci_cd: [7.1, 0.58, 1, false, []],
  observability: [6.6, 0.55, 2, false, ["Error budget policy"]],
  data_structures: [7.2, 0.7, 3, true, []],
  ownership: [7.0, 0.5, 2, false, []],
  incident_response: [6.4, 0.42, 2, true, ["Postmortem practice"]],
  cloud_cost_optimization: [5.4, 0.52, 3, true, ["Growth normalisation"]],
  leadership: [6.8, 0.45, 1, true, ["Delegation"]],
  communication: [7.4, 0.6, 2, true, []],
};

function scoreRecord(category: ScoreRecord["category"], key: string, label: string, score: number, confidence: number, extra: Partial<ScoreRecord> = {}): ScoreRecord {
  return {
    id: `score-${key}`,
    session_id: DEMO_IDS.completed,
    category,
    key,
    label,
    score,
    confidence,
    weight: null,
    evidence_count: 0,
    positive_count: 0,
    negative_count: 0,
    sufficient_evidence: true,
    unanswered_areas: [],
    supporting_question_ids: [],
    positive_evidence_ids: [],
    negative_evidence_ids: [],
    transcript_refs: [],
    details: {},
    is_final: true,
    created_at: iso(DURATION + 90),
    ...extra,
  };
}

export const demoScores: SessionScoresView = {
  overall: scoreRecord("overall", "overall", "Overall", 69, 0.68, {
    details: {
      rationale:
        "Overall 69/100 with required coverage at 86%. Strong Kubernetes and Terraform evidence; AWS networking and cost measurement are the main gaps. One integrity observation is still unreviewed.",
    },
  }),
  competencies: COMPETENCIES.map(([key, name, weight]) => {
    const [score, confidence, count, sufficient, unanswered] = FINAL[key]!;
    const own = demoEvidence.filter((e) => e.competency_key === key);
    const supporting = [...new Set(own.map((e) => e.question_id).filter((id): id is string => !!id))];
    const positives = own.filter((e) => e.signal === "positive");
    const negatives = own.filter((e) => e.signal === "negative");
    const observations = supporting.map((qid) => {
      const q = demoQuestions.find((x) => x.id === qid);
      const dims = q?.answers[0]?.evaluation;
      const values = dims ? [dims.correctness, dims.depth, dims.specificity, dims.practical_experience, dims.reasoning].filter((v): v is number => typeof v === "number") : [];
      const raw = values.length ? values.reduce((a, b) => a + b, 0) / values.length : score;
      return { answer_id: q?.answers[0]?.id ?? null, question_id: qid, coding_session_id: null, difficulty: q?.difficulty ?? "medium", score: Math.round(raw * 1000) / 1000, effective_score: Math.round(raw * 1000) / 1000, assistance_factor: 1, evaluation_confidence: 0.78, weight: q?.difficulty === "hard" ? 0.94 : q?.difficulty === "easy" ? 0.66 : 0.78, elapsed_seconds: (q?.elapsed_seconds ?? 0) + 60 };
    });
    const balance = Math.round((positives.length - negatives.length) * 0.12 * 100) / 100;
    return {
      ...scoreRecord("competency", key, name, score, confidence, {
        weight,
        evidence_count: own.filter((e) => e.signal !== "neutral").length || count,
        positive_count: positives.length,
        negative_count: negatives.length,
        sufficient_evidence: sufficient,
        unanswered_areas: unanswered,
        supporting_question_ids: supporting,
        positive_evidence_ids: positives.map((e) => e.id),
        negative_evidence_ids: negatives.map((e) => e.id),
      }),
      why: {
        positive_evidence: positives,
        negative_evidence: negatives,
        unanswered_areas: unanswered,
        supporting_questions: supporting.map((qid) => {
          const q = demoQuestions.find((x) => x.id === qid)!;
          return { id: q.id, sequence: q.sequence, text: q.text, focus: q.question_focus, difficulty: q.difficulty };
        }),
        transcript_refs: own.filter((e) => e.transcript_ref?.quote).map((e) => ({ ...e.transcript_ref, question_id: e.question_id })),
        calculation: { score, bayesian_mean: Math.round((score - balance) * 1000) / 1000, evidence_balance: balance, positive_weight: positives.length * 0.55, negative_weight: negatives.length * 0.4, confidence, total_weight: observations.reduce((a, o) => a + o.weight, 0), strong_evidence: positives.filter((e) => e.strength >= 0.7).length, score_std: 0.6, consistency_penalty: key === "aws" ? 0.04 : 0, difficulty_levels: new Set(observations.map((o) => o.difficulty)).size, evidence_count: count },
        observations,
      },
    };
  }),
  rubric: [
    scoreRecord("rubric_category", "technical_knowledge", "Technical knowledge", 71, 0.7, { weight: 30 }),
    scoreRecord("rubric_category", "practical_experience", "Practical experience", 58, 0.5, { weight: 15 }),
    scoreRecord("rubric_category", "coding", "Coding", 69, 0.7, { weight: 20 }),
    scoreRecord("rubric_category", "problem_solving", "Problem solving", 70, 0.62, { weight: 10 }),
    scoreRecord("rubric_category", "ownership_leadership", "Ownership and leadership", 69, 0.48, { weight: 15 }),
    scoreRecord("rubric_category", "communication", "Communication", 74, 0.6, { weight: 10 }),
  ],
  recommendation: "proceed",
  rationale:
    "Overall 69/100 with required coverage at 86%. Strong Kubernetes and Terraform evidence; AWS networking and cost measurement are the main gaps. One integrity observation is still unreviewed.",
  factors: { overall_score: 69, required_coverage: 0.86, unreviewed_integrity_flags: 1 },
  source: "persisted",
  isFinal: true,
  computedAt: iso(DURATION + 90),
};

// ----------------------------------------------------------------- timeline

export const demoSnapshots: ScoreSnapshot[] = (() => {
  const out: ScoreSnapshot[] = [];
  const seen = new Map<string, number>();
  const evaluated = [...demoQuestions].sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
  for (const q of evaluated) {
    const answer = q.answers[0];
    if (!answer?.evaluation) continue;
    const at = Math.round((answer.ended_ms ?? 0) / 1000) + 8;
    for (const key of q.competency_keys) {
      const target = FINAL[key]![0];
      const count = (seen.get(key) ?? 0) + 1;
      seen.set(key, count);
      // Converges from the neutral prior (5.0) toward the final score as evidence accumulates.
      const blend = 1 - Math.exp(-count / 1.4);
      const wobble = key === "aws" && count === 2 ? -0.4 : key === "cloud_cost_optimization" && count === 1 ? -1.1 : 0;
      out.push({ elapsed_seconds: at, competency_key: key, score: Math.round((5 + (target - 5) * blend + wobble) * 10) / 10, confidence: Math.round(FINAL[key]![1] * blend * 100) / 100, evidence_count: count, trigger: "answer_evaluated" });
    }
  }
  out.push({ elapsed_seconds: 1225, competency_key: "data_structures", score: 7.0, confidence: 0.6, evidence_count: 2, trigger: "coding_evaluated" });
  for (const [key] of COMPETENCIES) out.push({ elapsed_seconds: DURATION, competency_key: key, score: FINAL[key]![0], confidence: FINAL[key]![1], evidence_count: FINAL[key]![2], trigger: "final" });
  return out.sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
})();

/** GET /sessions/{id}/timeline as the API returns it (server windows + raw snapshots). */
export function demoTimelineResponse(windowMinutes = 10) {
  const width = windowMinutes * 60;
  let count = Math.max(1, Math.ceil(DURATION / width));
  if (demoSnapshots.length && demoSnapshots[demoSnapshots.length - 1]!.elapsed_seconds >= count * width) count += 1;
  const windows = Array.from({ length: count }, (_, index) => ({ index, start_seconds: index * width, end_seconds: (index + 1) * width, label: `Minute ${index * windowMinutes}-${(index + 1) * windowMinutes}`, scores: {} as Record<string, number>, details: {} as Record<string, Record<string, number>>, summary: "" }));
  for (const snap of demoSnapshots) {
    const w = windows[Math.min(count - 1, Math.floor(snap.elapsed_seconds / width))]!;
    w.scores[snap.competency_key] = snap.score;
    w.details[snap.competency_key] = { score: snap.score, confidence: snap.confidence, evidence_count: snap.evidence_count, elapsed_seconds: snap.elapsed_seconds };
  }
  return {
    session_id: DEMO_IDS.completed,
    window_minutes: windowMinutes,
    duration_seconds: DURATION,
    source: "snapshots",
    competencies: COMPETENCIES.map(([key, name]) => ({ key, name })),
    windows,
    snapshots: demoSnapshots,
    series: {},
    events: demoTimelineEvents,
  };
}

export const demoTimelineEvents: TimelineEvent[] = [
  { seq: 1, type: "session.started", elapsed_seconds: 0, payload: {}, created_at: iso(0) },
  ...demoQuestions.map((q, i) => ({ seq: i + 2, type: "question.asked", elapsed_seconds: q.elapsed_seconds, payload: { text: q.question_focus }, created_at: q.asked_at })),
  { seq: 40, type: "coding.started", elapsed_seconds: 712, payload: { text: "Most frequent error service" }, created_at: iso(712) },
  { seq: 41, type: "hint.issued", elapsed_seconds: 980, payload: { text: "Level 1" }, created_at: iso(980) },
  { seq: 42, type: "disconnect", elapsed_seconds: 1102, payload: { reason: "Network dropped for 9 seconds" }, created_at: iso(1102) },
  { seq: 43, type: "reconnect", elapsed_seconds: 1111, payload: {}, created_at: iso(1111) },
  { seq: 44, type: "coding.ended", elapsed_seconds: 1228, payload: { text: "Submitted" }, created_at: iso(1228) },
  { seq: 60, type: "session.completed", elapsed_seconds: DURATION, payload: { reason: "Enough evidence gathered" }, created_at: iso(DURATION) },
].sort((a, b) => (a.elapsed_seconds ?? 0) - (b.elapsed_seconds ?? 0));

// ------------------------------------------------------------------- coding

const CODE_V1 = `def most_frequent_error_service(lines):
    counts = {}
    for line in lines:
        ts, service, level, message = line.split(" ", 3)
        if level == "ERROR":
            counts[service] = counts.get(service, 0) + 1
    return max(counts, key=counts.get)
`;
const CODE_V2 = `def most_frequent_error_service(lines):
    counts = {}
    for line in lines:
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        ts, service, level = parts[0], parts[1], parts[2]
        if level == "ERROR":
            counts[service] = counts.get(service, 0) + 1
    if not counts:
        return ""
    return max(counts, key=counts.get)
`;
const CODE_FINAL = `from collections import Counter


def most_frequent_error_service(lines: list[str]) -> str:
    counts: Counter[str] = Counter()
    for line in lines:
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue  # skip malformed lines
        _, service, level = parts[0], parts[1], parts[2]
        if level == "ERROR":
            counts[service] += 1
    if not counts:
        return ""
    # Highest count first; ties broken alphabetically.
    return min(counts, key=lambda s: (-counts[s], s))
`;

function snapshot(seq: number, code: string, trigger: CodeSnapshotRecord["trigger"], t: number, added: number, removed: number): CodeSnapshotRecord {
  return { id: `snap-${seq}`, coding_session_id: "cs-1", seq, language: "python", code, diff_from_previous: null, lines_added: added, lines_removed: removed, char_count: code.length, trigger, elapsed_seconds: t, created_at: iso(t) };
}

function run(id: string, kind: CodeRunRecord["kind"], status: CodeRunRecord["status"], t: number, passed: number, total: number, tests: CodeRunRecord["test_results"], extra: Partial<CodeRunRecord> = {}): CodeRunRecord {
  return { id, coding_session_id: "cs-1", snapshot_id: null, kind, language: "python", status, stdout: null, stderr: null, compile_output: null, exit_code: status === "passed" ? 0 : 1, test_results: tests, passed_count: passed, total_count: total, runtime_ms: 41 + passed * 3, memory_kb: 9_420, elapsed_seconds: t, created_at: iso(t), ...extra };
}

export const demoCoding: CodingSessionDetail[] = [
  {
    id: "cs-1",
    session_id: DEMO_IDS.completed,
    coding_question_id: "cq-most-frequent-error-service",
    question_id: "q-09",
    language: "python",
    status: "submitted",
    started_at: iso(712),
    ended_at: iso(1228),
    time_limit_seconds: 600,
    hints_available: 3,
    hints_used: 1,
    paste_event_count: 0,
    final_code: CODE_FINAL,
    final_run_id: "run-3",
    created_at: iso(712),
    updated_at: iso(1228),
    evaluation: {
      correctness_summary: "Correct on all visible tests and 9 of 10 hidden tests; the failing hidden test has a trailing-whitespace service name.",
      tests_passed: 9,
      tests_total: 10,
      time_complexity: "O(n)",
      space_complexity: "O(k) distinct services",
      complexity_assessment: "Optimal single pass; memory bounded by the number of services.",
      code_quality: 7.8,
      problem_solving: 7.4,
      edge_case_handling: 6.2,
      testing_approach: 6.6,
      communication: 7.6,
      process_observations: ["Explained the approach before coding", "Ran the visible tests after each change", "Handled malformed lines only after a hint"],
      hints_impact: "One level-1 hint pointed at malformed lines; the rest of the solution was her own.",
      strengths: ["Idiomatic Counter usage", "Deterministic tie-breaking"],
      weaknesses: ["Edge cases considered late", "Didn't strip whitespace in service names"],
      score_estimate: 7.6,
      confidence: 0.8,
      evidence: [],
    },
    question: {
      id: "cq-most-frequent-error-service",
      title: "Most frequent error service",
      difficulty: "easy",
      prompt_markdown:
        "You are given log lines in the format `timestamp service level message`.\n\nReturn the **service with the most `ERROR` lines**. Break ties by choosing the alphabetically smallest service name. Return an empty string when there are no errors.\n\n```text\n2026-10-06T10:00:01Z checkout ERROR payment declined\n2026-10-06T10:00:02Z search INFO query ok\n```\n\nMalformed lines should be ignored.",
    },
    snapshots: [
      snapshot(1, "def most_frequent_error_service(lines):\n    pass\n", "autosave", 730, 2, 0),
      snapshot(2, CODE_V1, "run", 860, 6, 1),
      snapshot(3, CODE_V1, "hint", 980, 0, 0),
      snapshot(4, CODE_V2, "run", 1050, 6, 1),
      snapshot(5, CODE_FINAL, "run", 1180, 9, 6),
      snapshot(6, CODE_FINAL, "submit", 1226, 0, 0),
    ],
    runs: [
      run("run-1", "test", "error", 862, 3, 5, [
        { id: "visible-1", passed: true, hidden: false, args: [["t checkout ERROR x", "t search INFO y"]], expected: "checkout", actual: "checkout" },
        { id: "visible-2", passed: true, hidden: false, args: [["t a ERROR x", "t b ERROR y"]], expected: "a", actual: "a" },
        { id: "visible-3", passed: true, hidden: false, args: [["t api ERROR x", "t api ERROR y", "t web ERROR z"]], expected: "api", actual: "api" },
        { id: "visible-4", passed: false, hidden: false, args: [["garbage"]], expected: "", error: "ValueError: not enough values to unpack (expected 4, got 1)" },
        { id: "visible-5", passed: false, hidden: false, args: [[]], expected: "", error: "ValueError: max() arg is an empty sequence" },
      ], { stderr: "Traceback (most recent call last):\n  File \"solution.py\", line 4, in most_frequent_error_service\nValueError: not enough values to unpack (expected 4, got 1)" }),
      run("run-2", "test", "passed", 1182, 5, 5, [
        { id: "visible-1", passed: true, hidden: false },
        { id: "visible-2", passed: true, hidden: false },
        { id: "visible-3", passed: true, hidden: false },
        { id: "visible-4", passed: true, hidden: false },
        { id: "visible-5", passed: true, hidden: false },
      ]),
      run("run-3", "submit", "failed", 1226, 9, 10, [
        ...Array.from({ length: 5 }, (_, i) => ({ id: `visible-${i + 1}`, passed: true, hidden: false })),
        ...Array.from({ length: 4 }, (_, i) => ({ id: `hidden-${i + 1}`, passed: true, hidden: true, runtime_ms: 3 + i })),
        { id: "hidden-5", passed: false, hidden: true, args: [["t api  ERROR x", "t api ERROR y"]], expected: "api", actual: "" },
      ]),
    ],
    hints: [
      { id: "hint-1", coding_session_id: "cs-1", level: 1, content: "Think about what happens when a line doesn't have all four parts, for example a blank or truncated line.", source: "curated", requested_via: "button", elapsed_seconds: 980, delivered_at: iso(980), created_at: iso(980) },
    ],
  },
];

// ---------------------------------------------------------------- integrity

function integrity(id: string, over: Partial<IntegrityEventView>): IntegrityEventView {
  return {
    id,
    session_id: DEMO_IDS.completed,
    organization_id: ORG,
    type: "tab_hidden",
    source: "browser",
    severity: "low",
    confidence: null,
    occurred_at: iso(over.elapsed_seconds ?? 0),
    elapsed_seconds: 0,
    duration_ms: null,
    count: 1,
    details: {},
    evidence_media_id: null,
    action: "log_only",
    review_status: "unreviewed",
    reviewed_by_id: null,
    reviewed_at: null,
    review_note: null,
    created_at: iso(over.elapsed_seconds ?? 0),
    candidate: { id: DEMO_IDS.candidate, full_name: "Jane Doe" },
    job: { id: "job-devops", title: "Senior DevOps Engineer" },
    ...over,
  };
}

export const demoIntegrity: IntegrityEventView[] = [
  integrity("ie-1", { type: "tab_hidden", elapsed_seconds: 448, duration_ms: 6_200, count: 2, review_status: "noted", reviewed_by_id: "u-alex", reviewed_by: { id: "u-alex", full_name: "Alex Kim" }, reviewed_at: iso(DURATION + 3600), review_note: "Twice, a few seconds each, during the IAM answer. Looks like a notification; no concern." }),
  integrity("ie-2", { type: "disconnect", source: "server", severity: "info", elapsed_seconds: 1102, duration_ms: 9_000, details: { reason: "network" } }),
  integrity("ie-3", { type: "paste_event", elapsed_seconds: 1135, details: { chars: 48, surface: "code editor" } }),
  integrity("ie-4", { type: "possible_phone_visible", source: "vision", severity: "medium", confidence: 0.71, elapsed_seconds: 1505, action: "flag_for_human_review", evidence_media_id: "media-frame-1", details: { frames: 2, observation: "Rectangular object held near the lower edge of the frame" } }),
];

// ------------------------------------------------------------------ reviews

export const demoReviews: ReviewDecision[] = [
  {
    id: "rev-1",
    session_id: DEMO_IDS.completed,
    reviewer_id: "u-alex",
    reviewer: { id: "u-alex", full_name: "Alex Kim" },
    decision: "needs_more_information",
    notes: "Strong Kubernetes and Terraform answers. I'd like a short networking follow-up (egress, VPC endpoints) and to check the phone observation at 25:05 before deciding.",
    ai_recommendation_at_review: "proceed",
    overrides_ai: false,
    created_at: iso(DURATION + 5400),
  },
];

// ------------------------------------------------------------------- detail

const janeSummary: SessionSummary = {
  id: DEMO_IDS.completed,
  interview_id: "int-jane",
  organization_id: ORG,
  candidate_id: DEMO_IDS.candidate,
  job_id: "job-devops",
  round_id: DEMO_IDS.round,
  status: "completed",
  started_at: iso(0),
  ended_at: iso(DURATION),
  end_reason: "sufficient_evidence",
  elapsed_seconds: DURATION,
  reconnect_count: 1,
  created_at: iso(-300),
  updated_at: iso(DURATION + 120),
  candidate: { id: DEMO_IDS.candidate, full_name: "Jane Doe", email: "jane.doe@example.com" },
  job: { id: "job-devops", title: "Senior DevOps Engineer" },
  round: ROUNDS["job-devops"],
  overall_score: 69,
  confidence: 0.68,
  recommendation: "proceed",
  report_status: "ready",
  review_decision: "needs_more_information",
  integrity_flags: 3,
  question_count: demoQuestions.length,
};

export const demoSessionDetail: SessionDetailView = {
  ...janeSummary,
  live: false,
  round_config: demoRoundConfig,
  durations: { elapsed_seconds: DURATION, wall_seconds: DURATION + 41, paused_seconds: 41, scheduled_seconds: 1800, max_seconds: 2100, remaining_seconds: null },
  recommendation_rationale: "Overall 69/100 with required coverage at 86%. Strong Kubernetes and Terraform evidence; AWS networking and cost measurement are the main gaps.",
  human_decision_note: "This recommendation is assistive. A human reviewer owns the hiring decision.",
  scores_final: true,
  scores_computed_at: iso(DURATION + 90),
  rubric: [],
  reports: {
    recruiter: { id: "report-jane-recruiter", version: 2, status: "ready", generated_at: iso(DURATION + 180), released_to_candidate: false },
    candidate: { id: "report-jane-candidate", version: 2, status: "ready", generated_at: iso(DURATION + 180), released_to_candidate: false },
  },
  candidate_report_mode: "after_review",
  contradictions: [{ id: "ct-1", description: "Joined Bluepeak in January 2023 — conflicts with: Resume lists Harborline employment until March 2023", severity: "low", status: "resolved" }],
  review_count: 1,
  plan: {
    opening_focus: "Verify the Bluepeak EKS platform claim and her personal design role",
    segments: [
      { key: "intro", kind: "intro", competency_keys: [], time_budget_minutes: 1, goals: ["Introduce the format"] },
      { key: "resume", kind: "resume_validation", competency_keys: ["kubernetes", "cloud_cost_optimization"], time_budget_minutes: 6, goals: ["Platform scale and ownership", "40% cost claim"] },
      { key: "tech", kind: "technical", competency_keys: ["kubernetes", "aws", "terraform", "ci_cd"], time_budget_minutes: 9, goals: ["Availability", "IAM and networking", "Safe change management"] },
      { key: "coding", kind: "coding", competency_keys: ["data_structures"], time_budget_minutes: 9, goals: ["Most frequent error service"] },
      { key: "leadership", kind: "leadership", competency_keys: ["leadership", "ownership", "observability"], time_budget_minutes: 4, goals: ["Leading a team of three", "SLOs"] },
      { key: "close", kind: "closing", competency_keys: [], time_budget_minutes: 1, goals: ["Candidate questions and close"] },
    ],
    competency_targets: COMPETENCIES.slice(0, 7).map(([key], i) => ({
      key,
      priority: 5 - Math.min(4, Math.floor(i / 2)),
      starting_difficulty: i < 2 ? "medium" : "easy",
      focus_areas: key === "kubernetes" ? ["availability", "scaling", "troubleshooting"] : key === "aws" ? ["networking", "iam"] : [],
      rationale: key === "kubernetes" ? "Core of the role; resume claims a 120-node platform." : key === "aws" ? "Resume lists 4 accounts but little networking detail." : null,
    })),
    claims_priority: ["c-platform", "c-cost", "c-dates"],
    initial_difficulty: "medium",
    difficulty_strategy: "Start at medium; raise difficulty after strong answers on required competencies, keep behavioural questions at medium.",
    coding_plan: "Pinned problem 'most frequent error service' after the technical block, about 9 minutes.",
    risks_to_probe: ["Personal role versus team role on the platform", "Whether the 40% cost reduction is normalised for growth", "Overlapping employment dates (ask neutrally)"],
  },
  state_digest: {
    turn_state: "ended",
    avatar_state: "IDLE",
    current_segment: "closing",
    question_count: demoQuestions.length,
    elapsed_s: DURATION,
    remaining_s: 0,
    competencies: [],
    claims: [
      { id: "c-platform", claim: "Designed a production Kubernetes platform supporting 10,000 users", status: "verified" },
      { id: "c-migration", claim: "Led the migration of 40 microservices to EKS with no downtime", status: "unverified" },
      { id: "c-cost", claim: "Reduced cloud costs by 40%", status: "partially_verified" },
      { id: "c-team", claim: "Leads a team of 3 DevOps engineers", status: "in_progress" },
      { id: "c-dates", claim: "Bluepeak (Jan 2023 to present) and Harborline (Aug 2021 to Mar 2023)", status: "verified" },
    ],
  },
  client_info: {},
  error: null,
};

// --------------------------------------------------------------- live session

export const demoLiveDetail: SessionDetailView = {
  id: DEMO_IDS.live,
  interview_id: "int-marcus",
  organization_id: ORG,
  candidate_id: DEMO_IDS.liveCandidate,
  job_id: "job-backend",
  round_id: ROUNDS["job-backend"].id,
  status: "live",
  started_at: new Date(Date.now() - 742_000).toISOString(),
  ended_at: null,
  end_reason: null,
  elapsed_seconds: 742,
  reconnect_count: 0,
  created_at: iso(-600),
  updated_at: new Date().toISOString(),
  candidate: { id: DEMO_IDS.liveCandidate, full_name: "Marcus Okafor", email: "marcus.okafor@example.com" },
  job: { id: "job-backend", title: "Backend Engineer (Python)" },
  round: ROUNDS["job-backend"],
  overall_score: null,
  confidence: null,
  recommendation: null,
  report_status: null,
  review_decision: null,
  integrity_flags: 1,
  question_count: 5,
  live: true,
  durations: { elapsed_seconds: 742, wall_seconds: 760, paused_seconds: 18, scheduled_seconds: 1500, max_seconds: 1800, remaining_seconds: 758 },
  candidate_report_mode: "after_review",
  round_config: {
    ...demoRoundConfig,
    name: "Technical screening",
    duration_minutes: 25,
    competencies: [
      { key: "python", name: "Python", weight: 1.4, required: true, min_evidence: 3, target_difficulty: null, focus_areas: [] },
      { key: "api_design", name: "API design", weight: 1.2, required: true, min_evidence: 3, target_difficulty: null, focus_areas: [] },
      { key: "databases", name: "Databases", weight: 1.0, required: true, min_evidence: 2, target_difficulty: null, focus_areas: [] },
      { key: "testing", name: "Testing", weight: 0.8, required: false, min_evidence: 1, target_difficulty: null, focus_areas: [] },
      { key: "system_design", name: "System design", weight: 0.8, required: false, min_evidence: 1, target_difficulty: null, focus_areas: [] },
    ],
  } as RoundConfig,
  plan: null,
  state_digest: {
    turn_state: "candidate_speaking",
    avatar_state: "LISTENING",
    current_segment: "technical",
    question_count: 5,
    elapsed_s: 742,
    remaining_s: 758,
    competencies: [
      { key: "python", name: "Python", score_estimate: 7.1, confidence: 0.58, evidence_count: 4, sufficient_evidence: false },
      { key: "api_design", name: "API design", score_estimate: 6.3, confidence: 0.41, evidence_count: 2, sufficient_evidence: false },
      { key: "databases", name: "Databases", score_estimate: 5.6, confidence: 0.32, evidence_count: 1, sufficient_evidence: false },
      { key: "testing", name: "Testing", score_estimate: 5.0, confidence: 0.05, evidence_count: 0, sufficient_evidence: false },
      { key: "system_design", name: "System design", score_estimate: 5.0, confidence: 0.05, evidence_count: 0, sufficient_evidence: false },
    ],
    claims: [],
  },
};

const LIVE_Q: Array<[string, number, string[], SessionQuestion["difficulty"], string, string]> = [
  ["lq-1", 30, ["python"], "medium", "Tell me about the Python service you're proudest of. What did it do and what did you own?", "We built an order ingestion service in FastAPI that handled about two thousand requests per second at peak, I owned the async worker pipeline."],
  ["lq-2", 160, ["python"], "hard", "How did you keep the async workers from overwhelming the database during spikes?", "We put a bounded queue in front with backpressure, and each worker had a semaphore limiting concurrent DB sessions to the pool size."],
  ["lq-3", 300, ["api_design"], "medium", "How do you version a public API without breaking existing clients?", "Mostly additive changes; for breaking ones we used a new path version and ran both for a deprecation window with usage metrics per client."],
  ["lq-4", 470, ["databases"], "medium", "A query that used to take 20 milliseconds now takes two seconds. How do you investigate?", "First EXPLAIN ANALYZE to see the plan, then check whether statistics are stale or an index stopped being used after a data shape change."],
  ["lq-5", 640, ["api_design", "databases"], "medium", "Design an endpoint that lets clients page through ten million orders efficiently.", ""],
];

export const demoLiveQuestions: SessionQuestion[] = LIVE_Q.map(([id, t, keys, difficulty, text, answer], i) => ({
  id,
  session_id: DEMO_IDS.live,
  sequence: i + 1,
  parent_question_id: id === "lq-2" ? "lq-1" : null,
  bank_question_id: null,
  coding_question_id: null,
  kind: "technical",
  action: id === "lq-2" ? "increase_difficulty" : "ask_question",
  source: "generated",
  segment: "technical",
  topic: null,
  competency_keys: keys,
  difficulty,
  text,
  spoken_text: text,
  question_focus: null,
  reason: null,
  expected_evidence: [],
  claim_id: null,
  interrupted: false,
  asked_at: iso(t),
  elapsed_seconds: t,
  answers: answer
    ? [{ id: `la-${i}`, question_id: id, sequence: i + 1, transcript_text: answer, typed: false, started_ms: (t + 8) * 1000, ended_ms: (t + 40) * 1000, duration_ms: 32_000, word_count: answer.split(" ").length, stt_confidence: 0.92, interrupted_ai: false, status: "evaluated", evaluation: null, evidence: [] }]
    : [],
}));

export const demoLiveEvidence: EvidenceRecord[] = [
  ["lq-1", "python", "positive", 0.7, 75],
  ["lq-2", "python", "positive", 0.8, 205],
  ["lq-3", "api_design", "positive", 0.6, 350],
  ["lq-3", "api_design", "negative", 0.4, 352],
  ["lq-4", "databases", "positive", 0.5, 520],
].map(([q, key, signal, strength, t], i) => ({
  id: `lev-${i}`,
  session_id: DEMO_IDS.live,
  competency_key: key as string,
  question_id: q as string,
  answer_id: null,
  evaluation_id: null,
  coding_session_id: null,
  source: "answer" as const,
  signal: signal as EvidenceRecord["signal"],
  dimension: "depth",
  evidence: "Live evidence",
  strength: strength as number,
  confidence: 0.7,
  difficulty: "medium" as const,
  elapsed_seconds: t as number,
  transcript_ref: {},
  created_at: iso(t as number),
}));

export const demoLiveTranscript: TranscriptEntry[] = demoLiveQuestions.flatMap((q, i) => {
  const out: TranscriptEntry[] = [
    { id: `lt-${i}-q`, session_id: DEMO_IDS.live, seq: i * 2 + 1, speaker: "interviewer", kind: "final", text: q.text, start_ms: q.elapsed_seconds * 1000, end_ms: (q.elapsed_seconds + 6) * 1000, confidence: null, question_id: q.id, answer_id: null, interrupted: false, created_at: q.asked_at },
  ];
  if (q.answers[0]) out.push({ id: `lt-${i}-a`, session_id: DEMO_IDS.live, seq: i * 2 + 2, speaker: "candidate", kind: "final", text: q.answers[0].transcript_text, start_ms: q.answers[0].started_ms, end_ms: q.answers[0].ended_ms, confidence: 0.92, question_id: q.id, answer_id: q.answers[0].id, interrupted: false, created_at: q.asked_at });
  return out;
});

export const demoLiveIntegrity: IntegrityEventView[] = [
  integrity("lie-1", { session_id: DEMO_IDS.live, type: "focus_lost", severity: "low", elapsed_seconds: 512, duration_ms: 4_100, candidate: { id: DEMO_IDS.liveCandidate, full_name: "Marcus Okafor" }, job: { id: "job-backend", title: "Backend Engineer (Python)" } }),
];

/** SSE frames the screenshot harness streams for the live session. */
export const demoLiveEvents: Array<{ event?: string; data: Record<string, unknown> }> = [
  { event: "state", data: { state_digest: demoLiveDetail.state_digest, status: "live" } },
  { event: "transcript.partial", data: { speaker: "candidate", segment_id: "lt-live-partial", text: "I'd use keyset pagination on the created_at and id columns instead of offset, so each page", start_ms: 668_000 } },
  { event: "evaluation.ready", data: { answer_id: "la-3", competency_keys: ["databases"] } },
];

// ------------------------------------------------------------------ list

const NAMES = [
  "Jane Doe", "Marcus Okafor", "Sofia Martinez", "Priya Raman", "Daniel Novak", "Amara Nwosu", "Kenji Watanabe", "Lucia Ferreira",
  "Tomás Herrera", "Mei Lin", "Oliver Bennett", "Fatima Zahra", "Noah Fischer", "Isabel Cruz", "Arjun Mehta", "Hannah Kowalski",
  "Samuel Adeyemi", "Elena Petrova", "Lukas Brandt", "Grace Liu", "Mateo Rossi", "Aisha Bello", "Ethan Park", "Chloe Dubois",
];

const RECS: Recommendation[] = ["strong_proceed", "proceed", "proceed", "human_review", "borderline_evidence", "insufficient_evidence"];
const DECISIONS: Array<ReviewDecisionValue | null> = ["advance", null, "hold", null, "advance", "needs_more_information", null, "reject"];

export const demoSessions: SessionSummary[] = (() => {
  const random = rng(42);
  const list: SessionSummary[] = [janeSummary];
  const liveSummary: SessionSummary = { ...demoLiveDetail };
  list.push(liveSummary);
  list.push({
    ...janeSummary,
    id: DEMO_IDS.pending,
    interview_id: "int-sofia",
    candidate_id: "c-sofia",
    candidate: { id: "c-sofia", full_name: "Sofia Martinez" },
    job_id: "job-frontend",
    job: JOBS[2]!,
    round_id: ROUNDS["job-frontend"].id,
    round: ROUNDS["job-frontend"],
    status: "paused",
    started_at: new Date(Date.now() - 1_260_000).toISOString(),
    ended_at: null,
    end_reason: null,
    elapsed_seconds: 1_090,
    overall_score: null,
    confidence: null,
    recommendation: null,
    report_status: null,
    review_decision: null,
    integrity_flags: 2,
    question_count: 9,
  });
  for (let i = 3; i < 40; i += 1) {
    const job = JOBS[Math.floor(random() * JOBS.length)]!;
    const round = ROUNDS[job.id as keyof typeof ROUNDS];
    const statusRoll = random();
    const status: SessionStatus = statusRoll < 0.78 ? "completed" : statusRoll < 0.86 ? "aborted" : statusRoll < 0.92 ? "failed" : "completing";
    const score = status === "completed" ? Math.round(38 + random() * 52) : null;
    const rec = score === null ? null : score >= 80 ? "strong_proceed" : score >= 65 ? "proceed" : score >= 50 ? RECS[3 + Math.floor(random() * 2)]! : RECS[5]!;
    const daysAgo = Math.floor(random() * 26) + 1;
    const start = Date.now() - daysAgo * 86_400_000 - Math.floor(random() * 8) * 3_600_000;
    const elapsed = status === "aborted" ? Math.round(240 + random() * 600) : Math.round(1_200 + random() * 900);
    const name = NAMES[i % NAMES.length]!;
    list.push({
      id: `sess-${String(i).padStart(3, "0")}`,
      interview_id: `int-${i}`,
      organization_id: ORG,
      candidate_id: `c-${i}`,
      job_id: job.id,
      round_id: round.id,
      status,
      started_at: new Date(start).toISOString(),
      ended_at: status === "completing" ? null : new Date(start + elapsed * 1000).toISOString(),
      end_reason: status === "aborted" ? "candidate_ended" : status === "failed" ? "technical_failure" : status === "completed" ? (random() < 0.6 ? "sufficient_evidence" : "time_limit") : null,
      elapsed_seconds: elapsed,
      reconnect_count: random() < 0.2 ? 1 : 0,
      created_at: new Date(start - 600_000).toISOString(),
      updated_at: new Date(start + elapsed * 1000).toISOString(),
      candidate: { id: `c-${i}`, full_name: name },
      job,
      round,
      overall_score: score,
      confidence: score === null ? null : Math.round((0.45 + random() * 0.45) * 100) / 100,
      recommendation: rec,
      report_status: status === "completed" ? "ready" : status === "completing" ? "generating" : null,
      review_decision: status === "completed" ? DECISIONS[i % DECISIONS.length]! : null,
      integrity_flags: random() < 0.35 ? Math.ceil(random() * 3) : 0,
      question_count: Math.round(8 + random() * 7),
    });
  }
  return list.sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""));
})();

export function demoSessionsPage(params: URLSearchParams): Page<SessionSummary> {
  let items = demoSessions;
  const statuses = params.getAll("status");
  if (statuses.length) items = items.filter((s) => statuses.includes(s.status));
  const job = params.get("job_id");
  if (job) items = items.filter((s) => s.job_id === job);
  const limit = Number(params.get("limit") ?? 50);
  const offset = Number(params.get("offset") ?? 0);
  return { items: items.slice(offset, offset + limit), total: items.length };
}

// ---------------------------------------------------------------- reports

/** Recruiter report content in the shape report.generate stores (schema_version 1). */
function reportDocument(): Record<string, unknown> {
  const integrityByType: Record<string, { label: string; events: number; occurrences: number; max_severity: string }> = {};
  for (const e of demoIntegrity) {
    const entry = integrityByType[e.type] ?? { label: e.type, events: 0, occurrences: 0, max_severity: e.severity };
    entry.events += 1;
    entry.occurrences += e.count;
    integrityByType[e.type] = entry;
  }
  return {
    schema_version: 1,
    report_type: "recruiter",
    session: { id: DEMO_IDS.completed, status: "completed", started_at: iso(0), ended_at: iso(DURATION), end_reason: "sufficient_evidence", elapsed_seconds: DURATION, wall_seconds: DURATION + 41, reconnect_count: 1 },
    candidate: { id: DEMO_IDS.candidate, full_name: "Jane Doe", email: "jane.doe@example.com" },
    job: { id: "job-devops", title: "Senior DevOps Engineer", department: "Platform Engineering", seniority: "senior" },
    round: { id: DEMO_IDS.round, name: "Combined AI Round", round_type: "combined", duration_minutes: 30, expected_difficulty: "medium" },
    scores: {
      overall: { score: 69, confidence: 0.68, required_coverage: 0.86, evidence_coverage: 0.8 },
      rubric: demoScores.rubric.map((r) => ({ key: r.key, label: r.label, weight: r.weight, score: r.score, confidence: r.confidence, contributing_competencies: [] })),
      competencies: demoScores.competencies.map((c) => ({
        key: c.key,
        name: c.label,
        weight: c.weight,
        required: COMPETENCIES.find(([k]) => k === c.key)?.[3] ?? true,
        score: c.score,
        confidence: c.confidence,
        evidence_count: c.evidence_count,
        positive_count: c.positive_count,
        negative_count: c.negative_count,
        sufficient_evidence: c.sufficient_evidence,
        unanswered_areas: c.unanswered_areas,
        positive_evidence_ids: c.positive_evidence_ids,
        negative_evidence_ids: c.negative_evidence_ids,
        supporting_question_ids: c.supporting_question_ids,
        calculation: c.why?.calculation ?? null,
      })),
    },
    recommendation: { value: "proceed", rationale: demoScores.rationale, factors: demoScores.factors, human_decision_note: "This recommendation is assistive. A human reviewer owns the hiring decision." },
    evidence: demoEvidence.map((e) => ({ id: e.id, competency_key: e.competency_key, signal: e.signal, dimension: e.dimension, evidence: e.evidence, quote: e.transcript_ref?.quote ?? null, strength: e.strength, confidence: e.confidence, source: e.source, difficulty: e.difficulty, question_id: e.question_id, answer_id: e.answer_id, elapsed_seconds: e.elapsed_seconds, transcript_ref: e.transcript_ref })),
    questions: demoQuestions.map((q) => ({
      id: q.id,
      sequence: q.sequence,
      text: q.text,
      focus: q.question_focus,
      kind: q.kind,
      action: q.action,
      competency_keys: q.competency_keys,
      difficulty: q.difficulty,
      parent_question_id: q.parent_question_id,
      reason: q.reason,
      elapsed_seconds: q.elapsed_seconds,
      answers: q.answers.map((a) => ({
        id: a.id,
        sequence: a.sequence,
        transcript_text: a.transcript_text,
        started_ms: a.started_ms,
        ended_ms: a.ended_ms,
        evaluation: a.evaluation
          ? {
              id: a.evaluation.id,
              overall_quality: a.evaluation.overall_quality,
              dimensions: Object.fromEntries(
                (["correctness", "depth", "specificity", "practical_experience", "reasoning", "consistency", "communication_clarity"] as const)
                  .map((k) => [k, a.evaluation![k]] as const)
                  .filter(([, v]) => typeof v === "number"),
              ),
              summary: a.evaluation.evaluation.summary,
              missing_details: a.evaluation.evaluation.missing_details ?? [],
              contradictions: a.evaluation.evaluation.contradictions ?? [],
              confidence: a.evaluation.confidence,
              is_fallback: a.evaluation.is_fallback,
            }
          : null,
        evidence_ids: (a.evidence ?? []).map((e) => e.id),
      })),
    })),
    transcript_excerpts: demoQuestions.slice(0, 3).map((q) => ({ question_id: q.id, sequence: q.sequence, question: q.text, answer_id: q.answers[0]?.id, answer_excerpt: q.answers[0]?.transcript_text, start_ms: q.answers[0]?.started_ms, end_ms: q.answers[0]?.ended_ms })),
    coding: demoCoding.map((c) => ({
      id: c.id,
      title: c.question?.title,
      difficulty: c.question?.difficulty,
      language: c.language,
      status: c.status,
      hints_used: c.hints_used,
      hints_available: c.hints_available,
      assistance_factor: 0.93,
      final_code: c.final_code,
      final_result: { status: "failed", kind: "submit", passed: 9, total: 10, runtime_ms: 68 },
      hints: c.hints.map((h) => ({ level: h.level, requested_via: h.requested_via, elapsed_seconds: h.elapsed_seconds, source: h.source })),
      evaluation: c.evaluation,
    })),
    claims: (demoSessionDetail.state_digest?.claims ?? []).map((c) => ({
      id: c.id,
      claim: c.claim,
      status: c.status,
      note: c.id === "c-cost" ? "Numbers given ($180k to $110k per month) but not normalised for growth." : c.id === "c-platform" ? "Consistent description of three EKS clusters and her design role." : null,
    })),
    claims_summary: { verified: 2, partially_verified: 1, unverified: 1, in_progress: 1 },
    contradictions: [{ id: "ct-1", description: "Joined Bluepeak in January 2023 — conflicts with: Resume lists Harborline employment until March 2023", severity: "low", status: "resolved", refs: ["a-13"] }],
    integrity: {
      notice: "Integrity signals are automated observations for a human reviewer. They are not determinations of misconduct.",
      total_events: demoIntegrity.length,
      flagged_for_review: 1,
      unreviewed_flags: 1,
      by_type: integrityByType,
      by_severity: { info: 1, low: 2, medium: 1 },
      by_review_status: { noted: 1, unreviewed: 3 },
      events: [],
    },
    narrative: {
      executive_summary:
        "Jane gave specific, first-hand accounts of running a three-cluster EKS platform, including a real availability-zone incident and the fix that followed. Terraform change management and CI/CD practice look mature. The clearest gaps are AWS networking depth (egress, VPC endpoints) and how the 40% cost reduction was measured, which she partly quantified but did not normalise for growth. The coding exercise was solved cleanly with one small hint.",
      competency_findings: [
        { competency_key: "kubernetes", summary: "Hands-on platform ownership with honest boundaries; stateful workloads were outside her experience.", strengths: ["Autoscaling design and Karpenter migration", "Zone-failure remediation"], weaknesses: ["Stateful workload resilience"], unanswered_areas: [], evidence_ids: ["ev-01-1", "ev-02-1", "ev-02-3"] },
        { competency_key: "aws", summary: "Conventional, sound IAM design; networking depth is the main open question.", strengths: ["IRSA and permission boundaries"], weaknesses: ["Egress and VPC endpoints"], unanswered_areas: ["Egress and VPC endpoint design"], evidence_ids: ["ev-03-1", "ev-04-2"] },
        { competency_key: "cloud_cost_optimization", summary: "Quantified the reduction after a prompt; not normalised for growth.", strengths: ["Knows the main levers"], weaknesses: ["Measurement rigour"], unanswered_areas: ["Growth normalisation"], evidence_ids: ["ev-07-1", "ev-08-1", "ev-08-2"] },
      ],
      claims_summary: "Two of five prioritised claims were supported (platform scale, employment dates), one partly (cost reduction). The 40-service migration and team-lead claims were not discussed in depth.",
      contradictions_summary: "One date overlap was raised neutrally and explained as a part-time handover.",
      coding_summary: "Solved 'most frequent error service' in about 9 minutes with one level-1 hint; 9 of 10 hidden tests passed.",
      areas_for_human_followup: [
        "AWS networking: egress design, NAT versus VPC endpoints",
        "How the cost reduction was normalised for traffic growth",
        "The 40-microservice migration: sequencing and her personal share",
        "Review the possible-phone observation at 25:05 (currently unreviewed)",
      ],
      recommendation_rationale: "Overall 69/100 with good coverage of required competencies; suggestion is to proceed, pending the networking follow-up.",
      suggested_recommendation: "proceed",
      risks: ["Limited evidence for incident leadership beyond one example", "Networking depth unclear for a senior platform role"],
    },
    narrative_source: "model",
    narrative_disagrees: false,
  };
}

export const demoReport: Report = {
  id: "report-jane-recruiter",
  session_id: DEMO_IDS.completed,
  type: "recruiter",
  status: "ready",
  version: 2,
  recommendation: "proceed",
  overall_score: 69,
  confidence: 0.68,
  generated_at: iso(DURATION + 180),
  released_to_candidate: false,
  released_at: null,
  released_by_id: null,
  error: null,
  created_at: iso(DURATION + 120),
  updated_at: iso(DURATION + 180),
  content: {},
};

/** GET /sessions/{id}/reports/recruiter as the API returns it. */
export function demoReportResponse(): Report & Record<string, unknown> {
  return {
    ...demoReport,
    content: reportDocument() as Report["content"],
    candidate_report_mode: "after_review",
    versions: [
      { id: "report-jane-recruiter", type: "recruiter", version: 2, status: "ready", generated_at: iso(DURATION + 180), released_to_candidate: false, released_at: null, created_at: iso(DURATION + 120) },
      { id: "report-jane-recruiter-v1", type: "recruiter", version: 1, status: "ready", generated_at: iso(DURATION + 95), released_to_candidate: false, released_at: null, created_at: iso(DURATION + 92) },
    ],
  };
}

export const demoCandidateReport: Report = {
  ...demoReport,
  id: "report-jane-candidate",
  type: "candidate",
  recommendation: null,
  released_to_candidate: false,
  content: {},
};

export const demoInterview = {
  id: "int-jane",
  organization_id: ORG,
  candidate_id: DEMO_IDS.candidate,
  job_id: "job-devops",
  round_id: DEMO_IDS.round,
  status: "completed",
  policy_snapshot: { candidate_report_mode: "after_review" },
};

// --------------------------------------------------------------- analytics

export const demoOverview: AnalyticsOverviewView = {
  period: { days: 30, since: new Date(Date.now() - 30 * 86_400_000).toISOString(), until: new Date().toISOString() },
  period_days: 30,
  active_interviews: 21,
  sessions_started: 44,
  average_confidence: 0.64,
  integrity_events: 41,
  review_decision_distribution: { advance: 12, hold: 6, needs_more_information: 5, reject: 6 },
  ai_override_rate: 0.17,
  interviews_invited: 64,
  interviews_completed: 38,
  sessions_live: 2,
  sessions_completed: 38,
  awaiting_review: 7,
  average_overall_score: 66.4,
  completion_rate: 0.86,
  integrity_unreviewed: 5,
  completed_this_week: 11,
  recommendation_distribution: { strong_proceed: 6, proceed: 14, human_review: 7, borderline_evidence: 6, insufficient_evidence: 5 },
  sessions_by_day: Array.from({ length: 30 }, (_, i) => ({ date: new Date(Date.now() - (29 - i) * 86_400_000).toISOString().slice(0, 10), count: [0, 1, 2, 1, 0, 0, 2, 3, 1, 2, 1, 0, 1, 2, 2, 1, 3, 2, 0, 0, 1, 2, 3, 2, 1, 2, 1, 0, 1, 2][i]! })),
  average_duration_seconds: 1_712,
  score_distribution: [
    { min: 30, max: 40, count: 2 },
    { min: 40, max: 50, count: 4 },
    { min: 50, max: 60, count: 7 },
    { min: 60, max: 70, count: 10 },
    { min: 70, max: 80, count: 9 },
    { min: 80, max: 90, count: 5 },
    { min: 90, max: 100, count: 1 },
  ],
  integrity_events_total: 41,
  integrity_events_per_session: 0.87,
  integrity_by_type: { tab_hidden: 14, focus_lost: 9, disconnect: 6, paste_event: 5, possible_phone_visible: 3, multiple_persons_visible: 2, large_paste_in_coding: 2 },
  hint_usage: { coding_sessions: 24, sessions_with_hints: 13, average_hints: 0.9, distribution: { "0": 11, "1": 7, "2": 4, "3": 2 } },
};

export const demoFunnel: AnalyticsFunnel = {
  stages: [
    { stage: "invited", label: "Invited", count: 64 },
    { stage: "started", label: "Started", count: 47 },
    { stage: "completed", label: "Completed", count: 38 },
    { stage: "reviewed", label: "Reviewed", count: 29 },
  ],
};

const bucketsOf = (scores: number[]) => {
  const out = Array.from({ length: 10 }, () => 0);
  for (const s of scores) out[Math.min(9, Math.floor(s / 10))]! += 1;
  return out;
};
const groupOf = (key: string, label: string, scores: number[]) => {
  const sorted = [...scores].sort((a, b) => a - b);
  return { key, label, count: scores.length, mean: scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null, median: sorted.length ? sorted[Math.floor(sorted.length / 2)]! : null, min: sorted[0] ?? null, max: sorted[sorted.length - 1] ?? null, buckets: bucketsOf(scores) };
};

export const demoScoreDistributions: ScoreDistributionsDTO = (() => {
  const completed = demoSessions.filter((s) => s.status === "completed" && typeof s.overall_score === "number");
  const byJob = JOBS.map((j) => groupOf(j.id, j.title, completed.filter((s) => s.job_id === j.id).map((s) => s.overall_score as number)));
  const types = [...new Set(completed.map((s) => s.round!.round_type))];
  const roundLabels: Record<string, string> = { combined: "Combined", technical_screening: "Technical screening", deep_technical: "Deep technical" };
  return {
    bucket_labels: Array.from({ length: 10 }, (_, i) => `${i * 10}-${i === 9 ? 100 : i * 10 + 9}`),
    overall: groupOf("overall", "All interviews", completed.map((s) => s.overall_score as number)),
    by_job: byJob,
    by_round: types.map((t) => groupOf(t, roundLabels[t] ?? t, completed.filter((s) => s.round!.round_type === t).map((s) => s.overall_score as number))),
  };
})();

export const demoCompletion: CompletionAnalyticsDTO = {
  overall: { key: "overall", label: "All interviews", started: 44, completed: 38, completion_rate: 0.86, average_duration_seconds: 1712, median_duration_seconds: 1690, p90_duration_seconds: 2040 },
  by_round: [
    { key: "combined", label: "Combined", started: 14, completed: 12, completion_rate: 0.857, average_duration_seconds: 1790, median_duration_seconds: 1810, p90_duration_seconds: 2080 },
    { key: "technical_screening", label: "Technical screening", started: 22, completed: 19, completion_rate: 0.864, average_duration_seconds: 1520, median_duration_seconds: 1490, p90_duration_seconds: 1720 },
    { key: "deep_technical", label: "Deep technical", started: 8, completed: 7, completion_rate: 0.875, average_duration_seconds: 2410, median_duration_seconds: 2390, p90_duration_seconds: 2700 },
  ],
  by_job: JOBS.map((j, i) => ({ key: j.id, label: j.title, started: [14, 12, 10, 8][i]!, completed: [12, 10, 9, 7][i]!, completion_rate: [0.857, 0.833, 0.9, 0.875][i]!, average_duration_seconds: [1790, 1530, 1480, 2410][i]!, median_duration_seconds: [1810, 1500, 1460, 2390][i]!, p90_duration_seconds: [2080, 1760, 1700, 2700][i]! })),
  end_reasons: { sufficient_evidence: 21, time_limit: 14, candidate_ended: 4, technical_failure: 2, disconnect_timeout: 1 },
  status_counts: { completed: 38, aborted: 4, failed: 2 },
};

export const demoIntegrityAnalytics: IntegrityAnalyticsDTO = {
  note: "Observations for human review, not determinations.",
  sessions: 44,
  sessions_with_events: 27,
  sessions_with_flags: 6,
  flag_rate: 0.136,
  events_total: 41,
  events_per_session: 0.93,
  by_type: { tab_hidden: 14, focus_lost: 9, disconnect: 6, paste_event: 5, possible_phone_visible: 3, multiple_persons_visible: 2, large_paste_in_coding: 2 },
  by_severity: { high: 2, medium: 7, low: 21, info: 11 },
  by_source: { browser: 30, server: 6, vision: 5 },
  review_status: { unreviewed: 5, noted: 11, dismissed: 22, confirmed_concern: 3 },
};

export const demoHints: HintUsageAnalyticsDTO = {
  coding_sessions: 24,
  sessions_with_hints: 13,
  hint_rate: 0.54,
  average_hints: 0.9,
  distribution: { "0": 11, "1": 7, "2": 4, "3": 2 },
  by_level: { "1": 13, "2": 6, "3": 2 },
  by_problem: [],
};

export const demoCompetencyAnalytics: CompetencyAnalytics = {
  items: [
    ["kubernetes", "Kubernetes", 6.9, 21, 98],
    ["aws", "AWS", 6.2, 21, 84],
    ["terraform", "Terraform", 6.6, 19, 61],
    ["python", "Python", 7.1, 14, 72],
    ["api_design", "API design", 6.0, 14, 49],
    ["databases", "Databases", 5.8, 17, 52],
    ["data_structures", "Data structures", 6.8, 24, 70],
    ["observability", "Observability", 5.9, 18, 40],
    ["communication", "Communication", 7.3, 38, 66],
    ["leadership", "Leadership", 6.1, 22, 31],
  ].map(([competency_key, name, average_score, sessions, evidence_count], i) => ({ competency_key: competency_key as string, name: name as string, average_score: average_score as number, sessions: sessions as number, evidence_count: evidence_count as number, average_confidence: 0.5 + (i % 4) * 0.08, sufficient_rate: [0.86, 0.71, 0.64, 0.79, 0.52, 0.58, 0.83, 0.47, 0.92, 0.41][i]! })),
};

export const demoModelUsage: ModelUsage & { totals: Record<string, number> } = {
  totals: { calls: 11_836, errors: 47 },
  items: [
    { task: "turn_decision", endpoint: "main", calls: 512, errors: 3, p50_ms: 960, p95_ms: 1_840, avg_ttft_ms: 210, prompt_tokens: 1_884_000, completion_tokens: 141_000 },
    { task: "answer_evaluation", endpoint: "main", calls: 470, errors: 6, p50_ms: 6_200, p95_ms: 9_400, avg_ttft_ms: 260, prompt_tokens: 1_310_000, completion_tokens: 188_000 },
    { task: "interview_planning", endpoint: "main", calls: 47, errors: 0, p50_ms: 7_900, p95_ms: 11_200, avg_ttft_ms: 300, prompt_tokens: 160_000, completion_tokens: 38_000 },
    { task: "report_generation", endpoint: "main", calls: 76, errors: 1, p50_ms: 12_400, p95_ms: 18_900, avg_ttft_ms: 340, prompt_tokens: 410_000, completion_tokens: 96_000 },
    { task: "frame_analysis", endpoint: "vision", calls: 2_960, errors: 22, p50_ms: 2_300, p95_ms: 4_100, avg_ttft_ms: null, prompt_tokens: 0, completion_tokens: 0 },
    { task: "stt", endpoint: "whisper", calls: 3_120, errors: 9, p50_ms: 1_700, p95_ms: 2_600, avg_ttft_ms: null, prompt_tokens: 0, completion_tokens: 0 },
    { task: "tts", endpoint: "kokoro", calls: 2_210, errors: 4, p50_ms: 640, p95_ms: 1_150, avg_ttft_ms: null, prompt_tokens: 0, completion_tokens: 0 },
    { task: "embedding", endpoint: "embeddings", calls: 1_830, errors: 0, p50_ms: 38, p95_ms: 95, avg_ttft_ms: null, prompt_tokens: 0, completion_tokens: 0 },
    { task: "rerank", endpoint: "reranker", calls: 610, errors: 2, p50_ms: 120, p95_ms: 260, avg_ttft_ms: null, prompt_tokens: 0, completion_tokens: 0 },
  ],
};

export const demoIntegrityQueue: IntegrityEventView[] = [
  ...demoIntegrity.filter((e) => e.review_status === "unreviewed"),
  ...demoLiveIntegrity,
  integrity("qe-1", { session_id: "sess-004", type: "multiple_persons_visible", source: "vision", severity: "high", confidence: 0.83, elapsed_seconds: 914, occurred_at: new Date(Date.now() - 2 * 86_400_000).toISOString(), action: "flag_for_human_review", evidence_media_id: "media-frame-2", candidate: { id: "c-4", full_name: "Daniel Novak" }, job: { id: "job-ml", title: "ML Engineer (LLM)" }, details: { frames: 3, persons: 2 } }),
  integrity("qe-2", { session_id: "sess-007", type: "large_paste_in_coding", severity: "medium", elapsed_seconds: 1_288, occurred_at: new Date(Date.now() - 3 * 86_400_000).toISOString(), action: "flag_for_human_review", candidate: { id: "c-7", full_name: "Lucia Ferreira" }, job: { id: "job-backend", title: "Backend Engineer (Python)" }, details: { chars: 412, surface: "code editor" } }),
  integrity("qe-3", { session_id: "sess-009", type: "rapid_focus_switching", severity: "low", elapsed_seconds: 604, occurred_at: new Date(Date.now() - 4 * 86_400_000).toISOString(), count: 6, candidate: { id: "c-9", full_name: "Mei Lin" }, job: { id: "job-frontend", title: "Frontend Engineer (React)" }, details: { window_seconds: 60 } }),
  integrity("qe-4", { session_id: "sess-012", type: "screen_share_stopped", source: "screen", severity: "medium", elapsed_seconds: 1_402, occurred_at: new Date(Date.now() - 5 * 86_400_000).toISOString(), candidate: { id: "c-12", full_name: "Isabel Cruz" }, job: { id: "job-devops", title: "Senior DevOps Engineer" } }),
].sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));

export const demoJobs: Job[] = JOBS.map((j) => ({
  id: j.id,
  organization_id: ORG,
  title: j.title,
  department: null,
  location: null,
  employment_type: null,
  seniority: null,
  description: "",
  experience_min_years: null,
  experience_max_years: null,
  status: "open",
  interview_instructions: null,
  coding_requirements: {},
  integrity_settings: {},
  recording_settings: {},
  candidate_report_mode: "after_review",
  allow_candidate_round_selection: false,
  created_by_id: null,
  created_at: iso(-86400 * 30),
  updated_at: iso(-86400 * 2),
}));

export const demoCatalog = COMPETENCIES.map(([key, name]) => ({ key, name }));
