/**
 * Intake staff fixtures shaped exactly like docs/design/INTAKE_INTERVIEW.md section 15
 * (decimals as strings). Passage text, reading metrics and alignment events come from
 * the real F0 reading engine (`align_reading` on passage B with a simulated transcript),
 * so the alignment builder is tested against authentic offsets.
 *
 * Plain data only (types are erased), so the Playwright screenshot harness can bundle it.
 */

import type {
  IntakeAnalytics,
  IntakeConfig,
  IntakeDetail,
  IntakeEvaluation,
  IntakeOrgSettings,
  IntakeReportContent,
  IntakeStats,
  IntakeSummary,
  IntakeTimeline,
  ReadingEvent,
  ReadingMetricsView,
} from "../types";

export const NOW = "2026-10-09T10:00:00Z";
export const IDS = {
  intake: "it-0001-completed",
  session: "se-0001-completed",
  interview: "iv-0001",
  candidate: "ca-0001",
  job: "job-intake",
  round: "round-intake",
  evaluation: "ev-0001",
  reviewer: "u-reviewer",
} as const;

export const PASSAGE_B: string[] = [
  "Gradient descent is an optimisation algorithm used to train machine learning models. The goal is to minimise a cost function, which measures how wrong the model's predictions are.",
  "Imagine standing on a foggy hill and wanting to reach the lowest point. You can't see far, so you feel the slope beneath your feet and take a step in the steepest downhill direction, and you repeat this until the ground is flat.",
  "In the same way, gradient descent measures the slope of the error and adjusts the model's parameters step by step. The size of each step is called the learning rate: too large and you overshoot the valley, too small and training takes forever."
];
export const READ_TRANSCRIPT = "Gradient descent is is an optimization algorithm used to train machine learning models. The goal is to minimize a cost function, which measures how wrong the models predictions are. Imagine standing and wanting to reach the lowest point. You cannot see far, so you feel the slope under your feet and take a step in the deepest downhill direction, and you repeat this until the ground is flat. In the same way, gradient descent measures the slope of the error and adjusts the model perimeters step by step. The size of each step is called the learning rate: too large and you overshoot the valley, too small and um training takes basically forever.";
export const READING_EVENTS: ReadingEvent[] = [
  {
    "kind": "repetition",
    "ref_index": null,
    "ref_word_index": null,
    "ref_start": null,
    "ref_end": null,
    "ref_word": null,
    "hyp_index": 2,
    "hyp_word": "is",
    "sentence": null
  },
  {
    "kind": "omission",
    "ref_index": 30,
    "ref_word_index": 30,
    "ref_start": 198,
    "ref_end": 200,
    "ref_word": "on",
    "hyp_index": null,
    "hyp_word": null,
    "sentence": 2
  },
  {
    "kind": "omission",
    "ref_index": 31,
    "ref_word_index": 31,
    "ref_start": 201,
    "ref_end": 202,
    "ref_word": "a",
    "hyp_index": null,
    "hyp_word": null,
    "sentence": 2
  },
  {
    "kind": "omission",
    "ref_index": 32,
    "ref_word_index": 32,
    "ref_start": 203,
    "ref_end": 208,
    "ref_word": "foggy",
    "hyp_index": null,
    "hyp_word": null,
    "sentence": 2
  },
  {
    "kind": "omission",
    "ref_index": 33,
    "ref_word_index": 33,
    "ref_start": 209,
    "ref_end": 213,
    "ref_word": "hill",
    "hyp_index": null,
    "hyp_word": null,
    "sentence": 2
  },
  {
    "kind": "substitution",
    "ref_index": 51,
    "ref_word_index": 50,
    "ref_start": 294,
    "ref_end": 301,
    "ref_word": "beneath",
    "hyp_index": 48,
    "hyp_word": "under",
    "sentence": 3
  },
  {
    "kind": "substitution",
    "ref_index": 60,
    "ref_word_index": 59,
    "ref_start": 335,
    "ref_end": 343,
    "ref_word": "steepest",
    "hyp_index": 57,
    "hyp_word": "deepest",
    "sentence": 3
  },
  {
    "kind": "asr_variant",
    "ref_index": 88,
    "ref_word_index": 87,
    "ref_start": 501,
    "ref_end": 511,
    "ref_word": "parameters",
    "hyp_index": 85,
    "hyp_word": "perimeters",
    "sentence": 4
  },
  {
    "kind": "insertion",
    "ref_index": null,
    "ref_word_index": null,
    "ref_start": null,
    "ref_end": null,
    "ref_word": null,
    "hyp_index": 111,
    "hyp_word": "basically",
    "sentence": null
  }
];
export const READING_METRICS: ReadingMetricsView = {
  "method": "intake-reading-v1",
  "reference_words": 114,
  "reference_tokens": 115,
  "content_tokens": 63,
  "hypothesis_words": 112,
  "hypothesis_tokens": 113,
  "matched": 108,
  "asr_variants": 1,
  "substitutions": 2,
  "omissions": 4,
  "insertions": 1,
  "repetitions": 1,
  "reordered": 0,
  "fillers": 1,
  "wer": 0.0696,
  "strict_wer": 0.0783,
  "completeness": 0.9365,
  "token_coverage": 0.9478,
  "sentences": [
    {
      "index": 0,
      "text": "Gradient descent is an optimisation algorithm used to train machine learning models.",
      "tokens": 12,
      "covered": 12,
      "omitted": 0,
      "substituted": 0,
      "asr_variants": 0,
      "coverage": 1.0,
      "status": "read",
      "reordered": false
    },
    {
      "index": 1,
      "text": "The goal is to minimise a cost function, which measures how wrong the model's predictions are.",
      "tokens": 16,
      "covered": 16,
      "omitted": 0,
      "substituted": 0,
      "asr_variants": 0,
      "coverage": 1.0,
      "status": "read",
      "reordered": false
    },
    {
      "index": 2,
      "text": "Imagine standing on a foggy hill and wanting to reach the lowest point.",
      "tokens": 13,
      "covered": 9,
      "omitted": 4,
      "substituted": 0,
      "asr_variants": 0,
      "coverage": 0.6923,
      "status": "partial",
      "reordered": false
    },
    {
      "index": 3,
      "text": "You can't see far, so you feel the slope beneath your feet and take a step in the steepest downhill direction, and you repeat this until the ground is flat.",
      "tokens": 31,
      "covered": 29,
      "omitted": 0,
      "substituted": 2,
      "asr_variants": 0,
      "coverage": 0.9355,
      "status": "read",
      "reordered": false
    },
    {
      "index": 4,
      "text": "In the same way, gradient descent measures the slope of the error and adjusts the model's parameters step by step.",
      "tokens": 20,
      "covered": 20,
      "omitted": 0,
      "substituted": 0,
      "asr_variants": 1,
      "coverage": 1.0,
      "status": "read",
      "reordered": false
    },
    {
      "index": 5,
      "text": "The size of each step is called the learning rate: too large and you overshoot the valley, too small and training takes forever.",
      "tokens": 23,
      "covered": 23,
      "omitted": 0,
      "substituted": 0,
      "asr_variants": 0,
      "coverage": 1.0,
      "status": "read",
      "reordered": false
    }
  ],
  "sentences_read": 5,
  "sentences_partial": 1,
  "sentences_skipped": 0,
  "duration_seconds": 47.9,
  "words_per_minute": 140.2923,
  "correct_words_per_minute": 136.5344,
  "pause_source": "vad",
  "pause_count": 2,
  "long_pause_count": 1,
  "longest_pause_seconds": 2.6,
  "total_pause_seconds": 3.4,
  "delivery_score": 4,
  "delivery_basis": [
    "completeness 0.94, word error rate 0.07 -> base 4",
    "pace 140 wpm (within 80-220)",
    "1 long pause(s) of 2+ s (1 allowed)",
    "pronunciation not assessed; accent never affects the score"
  ],
  "pronunciation": "not_assessed",
  "review_flags": []
} as unknown as ReadingMetricsView;
export const PASSAGE_C: string[] = [
  "Overfitting happens when a machine learning model learns the training data too well — including its noise and random quirks — instead of the underlying pattern. Such a model performs beautifully on data it has already seen but fails on new, unseen data.",
  "A useful analogy is a student who memorises the answers to last year's exam without understanding the concepts. They ace the practice test but panic when the real questions are different.",
  "Common defences against overfitting include using more training data, simplifying the model, and a technique called regularisation, which penalises overly complex solutions."
];
export const RELAY_TRANSCRIPT = "So the passage was about overfitting. It is when a model learns the training data too well, including the noise, so it does great on the training set but badly on new data. You check it with a separate validation set. And to fix it you can get more data or make the model simpler. I think there was something about stopping early too but I am not sure.";
export const CARD = {
  "number": 2,
  "title": "The Importance of Time Management",
  "type": "explain_example",
  "type_label": "Explain + example",
  "instruction": "Speak for about 2 minutes, connecting all four points below into one flowing explanation:",
  "bullets": [
    "What good time management actually looks like",
    "One common mistake people make with their time",
    "One technique that genuinely helps",
    "A short example from your own experience"
  ]
};

const RELAY_CONCEPTS = [
  {
    "key": "definition",
    "text": "Overfitting is when a model learns the training data too well."
  },
  {
    "key": "noise",
    "text": "It learns the noise and random quirks instead of the underlying pattern."
  },
  {
    "key": "consequence",
    "text": "It does well on data it has seen but fails on new, unseen data."
  },
  {
    "key": "analogy",
    "text": "Like a student who memorises last year's exam answers without understanding the concepts."
  },
  {
    "key": "analogy_outcome",
    "text": "The student aces the practice test but struggles when the real questions are different."
  },
  {
    "key": "more_data",
    "text": "One defence is using more training data."
  },
  {
    "key": "simpler_model",
    "text": "Another defence is simplifying the model."
  },
  {
    "key": "regularisation",
    "text": "Regularisation penalises overly complex solutions."
  }
];
const RELAY_COVERED: Record<string, "covered" | "partial" | "missing"> = {
  definition: "covered",
  noise: "covered",
  consequence: "covered",
  analogy: "missing",
  analogy_outcome: "missing",
  more_data: "covered",
  simpler_model: "covered",
  regularisation: "missing",
};
const RELAY_QUOTES: Record<string, string> = {
  definition: "learns the training data too well",
  noise: "including the noise",
  consequence: "badly on new data",
  more_data: "you can get more data",
  simpler_model: "make the model simpler",
};

// ------------------------------------------------------------------ helpers

type Ref = IntakeEvaluation["competencies"][number]["evidence"][number];

function ref(answer_id: string, quote: string, supports: Ref["supports"], stage: Ref["stage"], item_key: string, question_id: string, start_ms: number): Ref {
  return { source: "answer", stage, item_key, question_id, answer_id, activity_id: null, start_ms, end_ms: start_ms + 20_000, quote, supports };
}

const person = (id: string, full_name: string) => ({ id, full_name });
const REVIEWER = person(IDS.reviewer, "Dana Whitfield");

// ------------------------------------------------------------------- config

export const CONFIG: IntakeConfig = {
  enabled: true,
  kit: {
    version: "v1",
    sha256: "01b9c592",
    title: "INTAKE INTERVIEW KIT",
    duration: { min: 35, max: 50, target: 45 },
    stages: [
      { key: "greeting", number: 1, title: "Greetings & self-presentation", candidate_title: "Introduction", minutes: { min: 5, max: 5 } },
      { key: "resume", number: 2, title: "Résumé-related questions", candidate_title: "Your experience", minutes: { min: 8, max: 10 } },
      { key: "mandatory", number: 3, title: "Mandatory screening questions", candidate_title: "Standard questions", minutes: { min: 3, max: 5 } },
      { key: "reading", number: 4, title: "Technical reading & relay", candidate_title: "Reading exercise", minutes: { min: 7, max: 8 } },
      { key: "topic", number: 5, title: "Structured answer / gap-filling", candidate_title: "Speaking exercise", minutes: { min: 7, max: 8 } },
      { key: "learning", number: 6, title: "Niche-fit & trainability probe", candidate_title: "Learning and growth", minutes: { min: 5, max: 5 } },
      { key: "close", number: 7, title: "Candidate questions & close", candidate_title: "Your questions", minutes: { min: 3, max: 5 } },
    ],
    passages: [
      { key: "A", title: "What Is an API?", level: "easy", drills: ["read_aloud", "relay"] },
      { key: "B", title: "Gradient Descent", level: "intermediate", drills: ["read_aloud", "relay"] },
      { key: "C", title: "Overfitting", level: "intermediate", drills: ["relay"] },
      { key: "D", title: "How a Neural Network Learns", level: "advanced", drills: ["read_aloud"] },
      { key: "E", title: "Transformers and Attention", level: "advanced", drills: ["read_aloud", "relay"] },
      { key: "F", title: "Version Control with Git", level: "intermediate", drills: ["read_aloud", "relay"] },
    ],
    topic_cards: [
      { number: 1, title: "Working From Home vs. Working in an Office", type: "compare_decide", type_label: "Compare + decide" },
      { number: 2, title: "The Importance of Time Management", type: "explain_example", type_label: "Explain + example" },
      { number: 3, title: "Learning a New Skill as an Adult", type: "personal_reasoning", type_label: "Personal + reasoning" },
      { number: 4, title: "Is Social Media More Helpful or More Harmful?", type: "argue_position", type_label: "Argue a position" },
      { number: 5, title: "What Makes Someone a Good Team Member?", type: "explain_example", type_label: "Explain + example" },
      { number: 6, title: "Why Good Customer Service Matters", type: "explain_example", type_label: "Explain + example" },
      { number: 7, title: "A Place You Would Recommend Visiting", type: "personal_narrative", type_label: "Personal narrative" },
      { number: 8, title: "Is It Better to Be a Specialist or a Generalist?", type: "argue_position", type_label: "Argue a position" },
    ],
  },
  defaults: { duration_minutes: 45, max_reconnects: 10, expires_in_hours: 168, presence: "orb", resume_required: true },
  work_authorization_default: { enabled: true, country: "the United States", ask_sponsorship: true, variant: "default" },
  jobs: [{ job_id: IDS.job, title: "AI/ML Training Program — Intake", status: "open", round_id: IDS.round, round_name: "Intake Interview" }],
};

export const ORG_SETTINGS: IntakeOrgSettings = {
  program_name: "AI/ML Training Program",
  work_authorization: { enabled: true, country: "the United States", ask_sponsorship: true, variant: "default" },
  approved_info: [
    { topic: "Training schedule", answer: "The program runs for twelve weeks, Monday to Friday, with live sessions in the mornings." },
    { topic: "After the interview", answer: "A person on the hiring team reviews every interview and contacts candidates within a week." },
  ],
  defaults: { duration_minutes: 45, max_reconnects: 10, expires_in_hours: 168, presence: "orb", resume_required: true },
  updated_at: "2026-10-08T15:30:00Z",
  updated_by: REVIEWER,
};

// ------------------------------------------------------------------ list rows

function row(id: string, name: string, patch: Partial<IntakeSummary>): IntakeSummary {
  const email = `${name.split(" ")[0]!.toLowerCase()}@example.com`;
  return {
    id,
    status: "active",
    bucket: "active",
    title: "Intake Interview",
    batch_id: null,
    candidate: { id: `ca-${id}`, full_name: name, email },
    job: { id: IDS.job, title: "AI/ML Training Program — Intake" },
    round: { id: IDS.round, name: "Intake Interview" },
    target_role: null,
    interview: { id: `iv-${id}`, status: "invited", invite_expires_at: "2026-10-16T10:00:00Z", scheduled_for: null, verified_at: null },
    session: null,
    onboarding_submitted: false,
    resume_confirmed: false,
    evaluation: null,
    decision: null,
    assigned_reviewer: null,
    has_training_plan: false,
    review_items_open: 0,
    integrity_flags: 0,
    created_at: "2026-10-07T09:00:00Z",
    updated_at: "2026-10-07T09:00:00Z",
    ...patch,
  };
}

export const SUMMARY_COMPLETED: IntakeSummary = row(IDS.intake, "Asha Rao", {
  candidate: { id: IDS.candidate, full_name: "Asha Rao", email: "asha.rao@example.com" },
  bucket: "awaiting_review",
  target_role: "ai_ml",
  interview: { id: IDS.interview, status: "completed", invite_expires_at: "2026-10-14T09:00:00Z", scheduled_for: null, verified_at: "2026-10-08T08:55:00Z" },
  session: { id: IDS.session, status: "completed", started_at: "2026-10-08T09:02:00Z", ended_at: "2026-10-08T09:46:30Z", elapsed_seconds: 2650 },
  onboarding_submitted: true,
  resume_confirmed: true,
  evaluation: { id: IDS.evaluation, status: "ready", weighted_total: "3.6300", band: "accept_with_notes", evidence_sufficiency: "sufficient" },
  assigned_reviewer: REVIEWER,
  has_training_plan: true,
  review_items_open: 4,
  integrity_flags: 2,
  created_at: "2026-10-07T08:00:00Z",
  updated_at: "2026-10-08T09:52:00Z",
});

export const ROWS: IntakeSummary[] = [
  SUMMARY_COMPLETED,
  row("it-0002", "Bilal Khan", {
    bucket: "in_progress",
    target_role: "data_analyst",
    interview: { id: "iv-0002", status: "in_progress", invite_expires_at: "2026-10-15T09:00:00Z", scheduled_for: null, verified_at: NOW },
    session: { id: "se-0002", status: "live", started_at: "2026-10-09T09:40:00Z", ended_at: null, elapsed_seconds: 1180 },
    onboarding_submitted: true,
    resume_confirmed: true,
  }),
  row("it-0003", "Chen Li", { bucket: "active", target_role: null }),
  row("it-0004", "Divya Menon-Subramaniam", { bucket: "scheduled", interview: { id: "iv-0004", status: "invited", invite_expires_at: "2026-10-20T09:00:00Z", scheduled_for: "2026-10-12T14:00:00Z", verified_at: null } }),
  row("it-0005", "Emeka Obi", { status: "draft", bucket: "drafts", interview: null, target_role: "product_manager_technical" }),
  row("it-0006", "Farah Siddiqui", {
    bucket: "accepted",
    target_role: "ai_ml",
    interview: { id: "iv-0006", status: "completed", invite_expires_at: "2026-10-10T09:00:00Z", scheduled_for: null, verified_at: "2026-10-06T10:00:00Z" },
    session: { id: "se-0006", status: "completed", started_at: "2026-10-06T10:05:00Z", ended_at: "2026-10-06T10:49:00Z", elapsed_seconds: 2580 },
    onboarding_submitted: true,
    resume_confirmed: true,
    evaluation: { id: "ev-0006", status: "ready", weighted_total: "4.3500", band: "strong_accept", evidence_sufficiency: "sufficient" },
    decision: { outcome: "accept", created_at: "2026-10-07T11:00:00Z", reviewer: REVIEWER },
    has_training_plan: true,
  }),
  row("it-0007", "Gabriel Souza", {
    bucket: "rejected",
    target_role: "product_manager_growth",
    session: { id: "se-0007", status: "completed", started_at: "2026-10-05T10:05:00Z", ended_at: "2026-10-05T10:41:00Z", elapsed_seconds: 2160 },
    evaluation: { id: "ev-0007", status: "ready", weighted_total: "2.3500", band: "reject", evidence_sufficiency: "sufficient" },
    decision: { outcome: "reject", created_at: "2026-10-06T09:00:00Z", reviewer: REVIEWER },
  }),
  row("it-0008", "Hana Kobayashi", { bucket: "expired", interview: { id: "iv-0008", status: "expired", invite_expires_at: "2026-10-01T09:00:00Z", scheduled_for: null, verified_at: null } }),
];

export const STATS: IntakeStats = {
  buckets: { drafts: 1, active: 1, scheduled: 1, in_progress: 1, completed: 3, awaiting_review: 1, accepted: 1, borderline: 0, rejected: 1, training: 2, expired: 1, cancelled: 0 },
  totals: { invitations: 7, started: 4, completed: 3, incomplete: 0, awaiting_review: 1, decided: 2, average_weighted_total: "3.4433", average_duration_s: 2463 },
};

// ------------------------------------------------------------------ timeline

const Q = (n: number) => `q-${String(n).padStart(2, "0")}`;
const A = (n: number) => `a-${String(n).padStart(2, "0")}`;

function exchange(n: number, text: string, asked_s: number, answer: string | null, answer_s = asked_s + 6, duration_s = 40, item_kind = "prompt", template = true) {
  return {
    question_id: Q(n),
    text,
    asked_ms: asked_s * 1000,
    template,
    item_kind,
    answer: answer === null ? null : { answer_id: A(n), text: answer, started_ms: answer_s * 1000, ended_ms: (answer_s + duration_s) * 1000, duration_ms: duration_s * 1000, typed: false, audio_media_id: `media-${A(n)}` },
  };
}

export const TIMELINE: IntakeTimeline = {
  intake_id: IDS.intake,
  session_id: IDS.session,
  stages: [
    {
      key: "greeting",
      number: 1,
      title: "Greetings & self-presentation",
      status: "completed",
      items: [
        { item_key: "greeting.intro", title: "Self-introduction", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(1, "To start, please introduce yourself in about 90 seconds: who you are and what you do.", 30, "I'm Asha. I've worked as a data analyst at a logistics company for two years, mostly building dashboards and cleaning shipment data in Python and SQL. Before that I studied statistics.", 36, 84)] },
        { item_key: "greeting.project", title: "Proudest project", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(2, "Now walk me through the one project or role you're most proud of, and why.", 125, "A delivery-delay dashboard. I wrote the pipeline that joins carrier events with our orders, and the operations team now uses it every morning to reroute late trucks.", 131, 70)] },
        { item_key: "greeting.followup", title: "Follow-up", mandatory: false, status: "answered", skip_reason: null, exchanges: [exchange(3, "What was the hardest part of joining the carrier events with the orders?", 205, "The carriers send timestamps in local time without the zone, so I had to map each depot to a time zone before joining.", 211, 32, "followup", false)] },
      ],
    },
    {
      key: "resume",
      number: 2,
      title: "Résumé-related questions",
      status: "completed",
      items: [
        { item_key: "resume.C1.ownership", title: "Claim 1: ownership", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(4, "Your résumé says you reduced report refresh time by 60%. What was your part in that, specifically?", 330, "I rewrote the nightly SQL into incremental loads and added indexes on the shipment table. My manager reviewed it, but the change was mine.", 336, 45, "ladder", false)] },
        { item_key: "resume.C1.mechanics", title: "Claim 1: mechanics", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(5, "How did the incremental load decide which rows to reprocess?", 400, "It keeps a watermark of the last updated timestamp and only pulls rows changed since then, plus a small overlap window for late updates.", 406, 38, "ladder", false)] },
        { item_key: "resume.C1.stress", title: "Claim 1: stress", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(6, "What would break if two updates arrived with the same timestamp?", 470, "I think the overlap window covers it, but honestly I'm not sure we tested exactly that case.", 476, 22, "ladder", false)] },
        { item_key: "resume.C2.ownership", title: "Claim 2: ownership", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(7, "You list a churn prediction model. What did you build yourself?", 520, "That was a team project. I mostly prepared the features; a senior colleague trained the model.", 526, 30, "ladder", false)] },
      ],
    },
    {
      key: "mandatory",
      number: 3,
      title: "Mandatory screening questions",
      status: "completed",
      items: [
        { item_key: "mandatory.mq1", title: "Work authorisation", mandatory: false, status: "prefilled", skip_reason: "prefilled", exchanges: [] },
        { item_key: "mandatory.mq2", title: "Target role", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(8, "On the form you said you're aiming for an AI/ML role. What draws you to it?", 905, "I like the modelling side of my analysis work, and I want to build models instead of only reporting on them.", 911, 28, "mq")] },
        { item_key: "mandatory.mq3.verification", title: "Language check", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(9, "In Python, what is the difference between a list and a tuple?", 980, "A list can change after you create it and a tuple can't. Tuples can be dictionary keys.", 986, 18, "verification")] },
        { item_key: "mandatory.mq4", title: "Experience level", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(10, "You mentioned about two years of hands-on experience. This program prepares people for roles that usually expect around five years of hands-on experience. Are you comfortable with the responsibilities and skills expected at that level, and can you commit to the training needed?", 1040, "Yes. I know it's a stretch from two years, and I can commit to the full schedule.", 1046, 20, "mq")] },
      ],
    },
    {
      key: "reading",
      number: 4,
      title: "Technical reading & relay",
      status: "completed",
      items: [
        { item_key: "reading.drill_a", title: "Drill A: read aloud", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(11, "Please read the passage on your screen aloud, at your normal pace.", 1150, READ_TRANSCRIPT, 1162, 48, "activity")] },
        { item_key: "reading.drill_b", title: "Drill B: read and relay", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(12, "Read this passage silently. It disappears after 30 seconds; then explain it in your own words.", 1240, RELAY_TRANSCRIPT, 1278, 52, "activity")] },
        { item_key: "reading.followup.least_sure", title: "Least sure", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(13, "What part were you least sure about?", 1340, "The last part, about the defences. I remembered more data and a simpler model but not the third one.", 1346, 20)] },
        { item_key: "reading.followup.non_technical", title: "Explain simply", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(14, "Now explain the same idea to a non-technical person in two sentences.", 1380, "It's like memorising last year's test answers: you look great on that test and fail the new one.", 1386, 16)] },
      ],
    },
    {
      key: "topic",
      number: 5,
      title: "Structured answer / gap-filling",
      status: "completed",
      items: [
        { item_key: "topic.card", title: "Topic card", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(15, "Here is your topic card. You have one minute to prepare, then two minutes to speak.", 1480, "Good time management means planning the week around a few important outcomes. A common mistake is saying yes to everything. One technique that helps is time-boxing. For example, I block two hours each morning for pipeline work.", 1545, 110, "activity")] },
        { item_key: "topic.weakest", title: "Weakest point", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(16, "You mentioned time-boxing briefly. How do you decide how long each box should be?", 1665, "I look at how long similar tasks took last month and add a buffer, but I don't do it very consistently yet.", 1671, 26, "followup", false)] },
      ],
    },
    {
      key: "learning",
      number: 6,
      title: "Niche-fit & trainability probe",
      status: "completed",
      items: [
        { item_key: "learning.three_weeks", title: "Three-week plan", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(17, "If you had three weeks to get productive with a tool you've never used in analytics, how would you plan it?", 1900, "First week the official tutorial and rebuild one of our dashboards with it. Second week a small real task with a colleague reviewing. Third week document what I learned for the team.", 1906, 60, "prompt", false)] },
        { item_key: "learning.teach", title: "Teach me", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(18, "Teach me something technical you learned recently — anything — in two minutes.", 1990, "Window functions in SQL: they let you compute a running total or a rank over rows without collapsing them like GROUP BY does.", 1996, 95)] },
      ],
    },
    {
      key: "close",
      number: 7,
      title: "Candidate questions & close",
      status: "completed",
      items: [
        { item_key: "close.questions", title: "Your questions", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(19, "What questions do you have for us?", 2560, "How long is the program, and is it remote?", 2566, 8)] },
        { item_key: "close.closing", title: "Closing", mandatory: true, status: "answered", skip_reason: null, exchanges: [exchange(20, "Thank you, Asha. That's everything from my side. The hiring team will review the interview and contact you about next steps.", 2620, null, 0, 0, "closing")] },
      ],
    },
  ],
};

// ---------------------------------------------------------------- evaluation

const competency = (key: string, label: string, kit_label: string, weight: string, score: string | null, weighted: string | null, evaluator: string, reasoning: string, evidence: Ref[], extra: Partial<IntakeEvaluation["competencies"][number]> = {}) => ({
  key,
  label,
  kit_label,
  weight,
  status: score === null ? ("insufficient" as const) : ("scored" as const),
  score,
  weighted,
  evaluator,
  reasoning,
  evidence,
  missing_evidence: [] as string[],
  strengths: [] as string[],
  improvements: [] as string[],
  anchors: { "1": "Unclear or off-topic in most answers.", "3": "Generally clear with some gaps in structure.", "5": "Clear, structured and relevant throughout." },
  sub_scores: {} as Record<string, unknown>,
  ...extra,
});

export const EVALUATION: IntakeEvaluation = {
  id: IDS.evaluation,
  version: 1,
  status: "ready",
  reason: "session_completed",
  created_at: "2026-10-08T09:47:00Z",
  finished_at: "2026-10-08T09:51:40Z",
  kit_version: "v1",
  scoring_version: "intake-scoring-1",
  weighted_total: "3.6300",
  partial_total: null,
  partial_label: null,
  normalized_100: "72.600",
  normalized_label: "Out of 100 (conversion)",
  band: "accept_with_notes",
  band_label: "Accept with notes",
  evidence_sufficiency: "sufficient",
  competencies: [
    competency("communication", "Communication & Presentation", "Communication & presentation", "0.20", "4.0", "0.8000", "communication", "Answers were clear and on point; the introduction and the project walk-through each had a clear structure.", [ref(A(2), "the operations team now uses it every morning to reroute late trucks", "positive", "greeting", "greeting.project", Q(2), 131000), ref(A(17), "Second week a small real task with a colleague reviewing", "positive", "learning", "learning.three_weeks", Q(17), 1906000)], { strengths: ["Concrete, outcome-focused examples"], improvements: ["Summarise the point before the detail"] }),
    competency("technical_reading", "Technical Reading & Relay", "Doc-reading / proxy ability", "0.20", "3.4", "0.6800", "relay", "Read the passage nearly completely at a steady pace; the relay covered five of the eight key concepts in their own words, with one statement that wasn't in the passage.", [ref(A(14), "It's like memorising last year's test answers", "positive", "reading", "reading.followup.non_technical", Q(14), 1386000)], { sub_scores: { drill_a: { score: 4, basis: ["completeness 0.94, word error rate 0.07 -> base 4"] }, drill_b: { score: 3, basis: ["5 of 8 concepts -> base 4", "1 factual error -> -1"] }, formula: "0.4 × Drill A + 0.6 × Drill B" } }),
    competency("structured_answer", "Structured Answer / Gap-Filling", "Gap-filling / structured answer", "0.15", "3.0", "0.4500", "structured", "Covered three of the four points, one by one rather than as a single flow; acknowledged the gap on time-boxing honestly.", [ref(A(16), "I don't do it very consistently yet", "context", "topic", "topic.weakest", Q(16), 1671000)], { improvements: ["Connect the points into one explanation"] }),
    competency("technical", "Actual Technical Ability", "Technical ability (actual)", "0.15", "3.0", "0.4500", "technical", "The incremental-load explanation showed hands-on understanding; the churn model was team work with a limited personal part.", [ref(A(5), "keeps a watermark of the last updated timestamp", "positive", "resume", "resume.C1.mechanics", Q(5), 406000), ref(A(7), "a senior colleague trained the model", "negative", "resume", "resume.C2.ownership", Q(7), 526000), ref(A(9), "A list can change after you create it and a tuple can't", "positive", "mandatory", "mandatory.mq3.verification", Q(9), 986000)]),
    competency("trainability", "Trainability & Learning Attitude", "Trainability / learning attitude", "0.15", "4.0", "0.6000", "trainability", "A concrete three-week plan with review loops; the teach-back on window functions was accurate and easy to follow.", [ref(A(18), "compute a running total or a rank over rows without collapsing them", "positive", "learning", "learning.teach", Q(18), 1996000)]),
    competency("composure", "Confidence & Composure", "Confidence & composure", "0.10", "4.0", "0.4000", "composure", "Kept going when unsure on the duplicate-timestamp question and said what hadn't been tested.", [ref(A(6), "honestly I'm not sure we tested exactly that case", "positive", "resume", "resume.C1.stress", Q(6), 476000)]),
    competency("adaptability", "Niche-Fit & Adaptability", "Niche-fit / adaptability", "0.05", "5.0", "0.2500", "trainability", "Clear interest in moving from reporting to modelling, and a realistic view of the gap.", [ref(A(8), "I want to build models instead of only reporting on them", "positive", "mandatory", "mandatory.mq2", Q(8), 911000)]),
  ],
  summary: {
    language: "python",
    strengths: ["Concrete examples from real work", "Honest about gaps"],
    improvements: ["Structure longer answers as one flow", "Deepen hands-on modelling experience"],
    drill_a: { passage: { key: "B", title: "Gradient Descent" }, metrics: READING_METRICS, events: READING_EVENTS, score: 4, basis: [...READING_METRICS.delivery_basis], status: "scored", reason: null, transcript_source: "final_pass_live" },
    drill_b: {
      passage: { key: "C", title: "Overfitting" },
      coverage: 0.625,
      concepts: RELAY_CONCEPTS.map((c: { key: string; text: string }) => ({ key: c.key, text: c.text, status: RELAY_COVERED[c.key] ?? "missing", quote: RELAY_QUOTES[c.key] ?? null, evidence: RELAY_QUOTES[c.key] ? ref(A(12), RELAY_QUOTES[c.key]!, "positive", "reading", "reading.drill_b", Q(12), 1278000) : null })),
      paraphrase: "own_words",
      misstatements: [ref(A(12), "something about stopping early", "negative", "reading", "reading.drill_b", Q(12), 1278000)],
      followups: [
        { question: "What part were you least sure about?", answer: "The last part, about the defences.", note: "Named the gap precisely.", evidence: null },
        { question: "Now explain the same idea to a non-technical person in two sentences.", answer: "It's like memorising last year's test answers.", note: "A fitting analogy in plain words.", evidence: null },
      ],
      score: 3,
      basis: ["5 of 8 concepts relayed (0.62) -> base 4", "1 factual error about the passage -> -1", "relayed in their own words"],
      status: "scored",
      reason: null,
      shown_ms: 30000,
    },
    claims: [
      { claim: "C1", text: "Reduced report refresh time by 60%", probe: "metric_improvement", source: "resume", verdict: "hands_on", note: "Explained the mechanism and their own part.", unresolved: ["Duplicate-timestamp handling not tested"], evidence: [] },
      { claim: "C2", text: "Built a churn prediction model", probe: "project_or_product", source: "resume", verdict: "team_level", note: "Prepared features; did not train the model.", unresolved: [], evidence: [] },
    ],
    verification: { quality: "correct", note: "Mutability and hashability both named.", answer: null },
    rating_supported: "partially",
    bullets: [
      { index: 1, text: CARD.bullets[0]!, status: "covered", quote: "planning the week around a few important outcomes", evidence: ref(A(15), "planning the week around a few important outcomes", "positive", "topic", "topic.card", Q(15), 1545000) },
      { index: 2, text: CARD.bullets[1]!, status: "covered", quote: "saying yes to everything", evidence: ref(A(15), "saying yes to everything", "positive", "topic", "topic.card", Q(15), 1545000) },
      { index: 3, text: CARD.bullets[2]!, status: "partial", quote: "time-boxing", evidence: ref(A(15), "time-boxing", "context", "topic", "topic.card", Q(15), 1545000) },
      { index: 4, text: CARD.bullets[3]!, status: "covered", quote: "I block two hours each morning for pipeline work", evidence: ref(A(15), "I block two hours each morning for pipeline work", "positive", "topic", "topic.card", Q(15), 1545000) },
    ],
    flow: "mechanical",
    examples_used: true,
    gap_handling: "honest",
    followup_analysis: "Asked about the least-developed point, they explained a rough method and said plainly that they don't apply it consistently yet.",
    learning_strategy: "Tutorial first, then a reviewed real task, then documentation for the team.",
    teach_back: "Accurate and well paced; used a concrete contrast with GROUP BY.",
    ai_ml_openness: "eager",
    composure_observations: [{ behaviour: "kept_going_when_unsure", note: "Answered the stress question and named the untested case.", evidence: null }],
    evaluator_notes: [],
  },
  validator_issues: [{ code: "evidence_dropped", competency: "communication", detail: "One quote did not match the answer text and was removed." }],
  review_items: [],
  training_plan: {
    generated_by: "deterministic",
    summary: "Strengthen structured explanations and hands-on modelling while keeping the strong analytics base.",
    modules: [
      { key: "structured_speaking", title: "Structured speaking", priority: 1, competencies: ["structured_answer"], why: "The topic answer listed points without connecting them.", objectives: ["Answer in a point-reason-example shape", "Join points into one flow"], milestones: [{ week: 1, milestone: "Two recorded two-minute answers", measure: "All four points connected" }] },
      { key: "ai_ml_fundamentals", title: "AI/ML fundamentals", priority: 2, competencies: ["adaptability"], why: "Included for every intake candidate.", objectives: ["Train and evaluate a baseline model"], milestones: [{ week: 2, milestone: "First model on course data", measure: "Validation metric reported" }] },
    ],
  },
  error: null,
};

const REVIEW_ITEMS = [
  {
    id: "ri_7c1e0a9b3d",
    kind: "claims_not_supported",
    severity: "important" as const,
    title: "Résumé claim not supported by the answers",
    detail: "The churn prediction model on the résumé was described as team work; the candidate prepared features but did not train the model.",
    competency_keys: ["technical"],
    evidence: [ref(A(7), "a senior colleague trained the model", "negative", "resume", "resume.C2.ownership", Q(7), 526000)],
    source: "evaluator",
    kit_flag: "claims_not_supported",
    status: "open",
    resolution: null,
  },
  {
    id: "ri_2b8d4f6a10",
    kind: "work_authorization_review",
    severity: "info" as const,
    title: "Work authorisation answers for review",
    detail: "Answered on the onboarding form. Never used by the AI or in any score.",
    competency_keys: [],
    evidence: [{ source: "onboarding" as const, stage: null, item_key: null, question_id: null, answer_id: null, activity_id: null, start_ms: null, end_ms: null, quote: null, supports: "context" as const }],
    source: "rule",
    kit_flag: "work_authorization_review",
    status: "open",
    resolution: null,
  },
  {
    id: "ri_5e3a9c7b21",
    kind: "asr_quality_low",
    severity: "attention" as const,
    title: "Speech recognition quality was low during the relay",
    detail: "Listen to the recording before relying on the relay transcript.",
    competency_keys: ["technical_reading"],
    evidence: [],
    source: "rule",
    kit_flag: null,
    status: "open",
    resolution: null,
  },
  {
    id: "ri_9f0b1c2d3e",
    kind: "integrity_observation",
    severity: "attention" as const,
    title: "2 integrity observations not yet reviewed",
    detail: "Observations, not determinations. Review them in the Integrity tab.",
    competency_keys: [],
    evidence: [],
    source: "rule",
    kit_flag: null,
    status: "open",
    resolution: null,
  },
];

export const DETAIL: IntakeDetail = {
  ...SUMMARY_COMPLETED,
  settings: {
    duration_minutes: 45,
    presence: "orb",
    max_reconnects: 10,
    expires_in_hours: 168,
    scheduled_for: null,
    require_access_code: false,
    resume_required: true,
    assigned_reviewer_id: IDS.reviewer,
    notify_on_completion: true,
    work_authorization: { enabled: true, country: "the United States", ask_sponsorship: true, variant: "default" },
    program_name: "AI/ML Training Program",
    kit_version: "v1",
    kit_sha256: "01b9c592",
  },
  notes: "Referred by the analytics community meetup.",
  onboarding: {
    version: 1,
    full_name: "Asha Rao",
    target_role: "ai_ml",
    target_role_other: null,
    open_to_ai_ml: "yes",
    technical_background: "Two years as a data analyst: SQL pipelines, Python cleaning scripts, dashboards for operations.",
    primary_language: "python",
    primary_language_other: null,
    self_rating: 7,
    scripting: "regular",
    scripting_details: "Daily Python scripts for data cleaning",
    data_ml_libraries: "pandas, scikit-learn (basic)",
    years_hands_on: 2,
    experience_level_comfort: "yes",
    training_commitment: "full",
    weekly_hours: 40,
    availability: "within_2_weeks",
    available_from: "2026-10-20",
    accuracy_confirmed: true,
    submitted_at: "2026-10-08T08:58:00Z",
  },
  eligibility: { enabled: true, country: "the United States", ask_sponsorship: true, authorized: true, needs_sponsorship: false, answered_at: "2026-10-08T08:58:00Z" },
  resume_review: {
    action: "confirmed",
    source: { resume_id: "res-0001", profile_id: "prof-0001", profile_status: "ready" },
    original: null,
    document: null,
    corrections: [
      { path: "roles[r1].title", op: "changed", before: "Data Analyst Intern", after: "Data Analyst" },
      { path: "skills", op: "added", before: null, after: "dbt" },
    ],
    confirmed_at: "2026-10-08T08:59:30Z",
  },
  resume: { id: "res-0001", original_filename: "asha-rao-resume.pdf", status: "ready" },
  stages: [
    { key: "greeting", number: 1, title: "Greetings & self-presentation", status: "completed", started_s: 28, ended_s: 300, budget_s: 314, items: [], extra: {} },
    { key: "resume", number: 2, title: "Résumé-related questions", status: "completed", started_s: 300, ended_s: 900, budget_s: 566, items: [], extra: {} },
    { key: "mandatory", number: 3, title: "Mandatory screening questions", status: "completed", started_s: 900, ended_s: 1140, budget_s: 251, items: [], extra: {} },
    { key: "reading", number: 4, title: "Technical reading & relay", status: "completed", started_s: 1140, ended_s: 1470, budget_s: 471, items: [], extra: {} },
    { key: "topic", number: 5, title: "Structured answer / gap-filling", status: "completed", started_s: 1470, ended_s: 1880, budget_s: 471, items: [], extra: {} },
    { key: "learning", number: 6, title: "Niche-fit & trainability probe", status: "completed", started_s: 1880, ended_s: 2550, budget_s: 314, items: [], extra: {} },
    { key: "close", number: 7, title: "Candidate questions & close", status: "completed", started_s: 2550, ended_s: 2650, budget_s: 251, items: [], extra: {} },
  ].map((stage) => ({
    ...stage,
    items: TIMELINE.stages.find((s) => s.key === stage.key)!.items.map((item) => ({
      item_key: item.item_key,
      title: item.title,
      mandatory: item.mandatory,
      compressible: false,
      status: item.status,
      question_ids: item.exchanges.map((e) => e.question_id),
      answer_ids: item.exchanges.flatMap((e) => (e.answer ? [e.answer.answer_id] : [])),
      followups_used: 0,
      followup_limit: 2,
      clarifications: 0,
      skip_reason: item.skip_reason,
      started_s: null,
      ended_s: null,
    })),
  })) as IntakeDetail["stages"],
  activities: [
    {
      id: "act-read",
      kind: "reading_aloud",
      stage_key: "reading",
      status: "completed",
      attempt: 1,
      material: { key: "B", title: "Gradient Descent", level: "intermediate", paragraphs: PASSAGE_B },
      phases: [],
      reveal: null,
      capture: { started_ms: 1162000, ended_ms: 1210000, voiced_ms: 44000, end_reason: "finish_button", typed: false, pauses_count: 2, segments_count: 3 },
      transcript: { final: READ_TRANSCRIPT, live: READ_TRANSCRIPT, status: "final", source: "final_pass_live" },
      question_id: Q(11),
      answer_id: A(11),
      audio_media_id: "media-act-read",
    },
    {
      id: "act-relay",
      kind: "read_relay",
      stage_key: "reading",
      status: "completed",
      attempt: 1,
      material: { key: "C", title: "Overfitting", level: "intermediate", paragraphs: PASSAGE_C },
      phases: [],
      reveal: { shown_ms: 30000, windows: [{ from_elapsed_s: 1246, to_elapsed_s: 1276 }], interrupted: false },
      capture: { started_ms: 1278000, ended_ms: 1330000, voiced_ms: 47000, end_reason: "finish_button", typed: false, pauses_count: 4, segments_count: 5 },
      transcript: { final: RELAY_TRANSCRIPT, live: RELAY_TRANSCRIPT, status: "final", source: "final_pass_worker" },
      question_id: Q(12),
      answer_id: A(12),
      audio_media_id: null,
    },
    {
      id: "act-card",
      kind: "topic_card",
      stage_key: "topic",
      status: "completed",
      attempt: 1,
      material: { key: "2", title: CARD.title, type: CARD.type, type_label: CARD.type_label, bullets: CARD.bullets, instruction: CARD.instruction },
      phases: [],
      reveal: null,
      capture: { started_ms: 1545000, ended_ms: 1655000, voiced_ms: 98000, end_reason: "finish_button", typed: false, pauses_count: 6, segments_count: 8 },
      transcript: { final: TIMELINE.stages[4]!.items[0]!.exchanges[0]!.answer!.text, live: null, status: "final", source: "final_pass_live" },
      question_id: Q(15),
      answer_id: A(15),
      audio_media_id: null,
    },
  ],
  evaluation: EVALUATION,
  review_items: REVIEW_ITEMS,
  decisions: [],
  report: { version: 1, status: "ready", artifact_media_id: "media-report-1", generated_at: "2026-10-08T09:53:00Z" },
  integrity: { flags: 2, unreviewed: 2 },
  recordings: { parts: 1, ready: true },
  evaluation_ref: SUMMARY_COMPLETED.evaluation,
};

export const DECISION = {
  id: "dec-0001",
  outcome: "accept_with_notes" as const,
  outcome_label: "Accept with notes",
  rationale: "Solid analytics base and honest about gaps. Flag structured speaking for the trainers.",
  reviewer: { ...REVIEWER, email: "dana@acme.test" },
  ai_band_at_decision: "accept_with_notes" as const,
  ai_total_at_decision: "3.6300",
  overrides_ai: false,
  review_items_resolution: [{ item_id: "ri_7c1e0a9b3d", resolution: "confirmed" as const, note: "" }],
  competency_notes: { structured_answer: "Pair with the speaking module in week one." },
  requested_evidence: [],
  review_decision_id: "rd-0001",
  evaluation_id: IDS.evaluation,
  created_at: "2026-10-08T12:00:00Z",
};

export const DECIDED_DETAIL: IntakeDetail = { ...DETAIL, bucket: "accepted", decision: { outcome: "accept_with_notes", created_at: DECISION.created_at, reviewer: REVIEWER }, decisions: [DECISION] };

// ------------------------------------------------------------------- report

export const REPORT_CONTENT: IntakeReportContent = {
  kind: "intake",
  schema_version: 1,
  kit_version: "v1",
  scoring_version: "intake-scoring-1",
  reading_method: "intake-reading-v1",
  generated_at: "2026-10-08T09:53:00Z",
  session_id: IDS.session,
  intake_id: IDS.intake,
  evaluation_id: IDS.evaluation,
  evaluation_version: 1,
  A_candidate_summary: {
    candidate_name: "Asha Rao",
    target_role: "ai_ml",
    interview_date: "2026-10-08T09:02:00Z",
    duration_s: 2650,
    resume_summary: "Data analyst with two years of SQL and Python pipeline work in logistics; statistics degree.",
    technical_background: DETAIL.onboarding!.technical_background,
    years_hands_on: 2,
    resume_experience_years: 2,
    completed_stages: DETAIL.stages.map((s) => ({ key: s.key, title: s.title, status: s.status })),
  },
  B_overall: {
    weighted_total: "3.6300",
    normalized_100: "72.600",
    normalized_label: "Out of 100 (conversion)",
    band: "accept_with_notes",
    band_label: "Accept with notes",
    band_guidance: "flag weak areas for the trainer team",
    recommendation_note: "This is an AI recommendation. A human reviewer makes the decision.",
    human_review_status: "pending",
    latest_decision: null,
    evidence_sufficiency: "sufficient",
    partial_total: null,
    partial_label: null,
    strengths: EVALUATION.summary.strengths,
    improvements: EVALUATION.summary.improvements,
  },
  C_competencies: EVALUATION.competencies.map((c) => ({ key: c.key, label: c.label, weight: c.weight, score: c.score, weighted: c.weighted, status: c.status, observations: c.strengths, explanation: c.reasoning, improvements: c.improvements, evidence: c.evidence, sub_scores: c.sub_scores })),
  D_resume_verification: [
    { claim_ref: "C1", claim: "Reduced report refresh time by 60%", probe: "metric_improvement", source: "resume", questions: [{ rung: "ownership", question_id: Q(4), text: TIMELINE.stages[1]!.items[0]!.exchanges[0]!.text }], responses: [{ answer_id: A(4), excerpt: "I rewrote the nightly SQL into incremental loads" }], verdict: "hands_on", supporting_evidence: [], unresolved: ["Duplicate-timestamp handling not tested"] },
    { claim_ref: "C2", claim: "Built a churn prediction model", probe: "project_or_product", source: "resume", questions: [], responses: [{ answer_id: A(7), excerpt: "a senior colleague trained the model" }], verdict: "team_level", supporting_evidence: [], unresolved: [] },
  ],
  E_technical: { language: "Python", self_rating: 7, scripting: "Regularly", data_ml_libraries: "pandas, scikit-learn (basic)", verification: [{ question: "In Python, what is the difference between a list and a tuple?", answer_excerpt: "A list can change after you create it and a tuple can't.", quality: "correct", matched_key_points: [0, 1] }], rating_supported: "partially", technical_understanding: "Hands-on with data pipelines; modelling experience is mostly second-hand.", accuracy_notes: null },
  F_reading: {
    drill_a: { passage_key: "B", passage_title: "Gradient Descent", duration_s: 47.9, transcript: READ_TRANSCRIPT, transcript_source: "final_pass_live", metrics: READING_METRICS, events: READING_EVENTS, score: 4, basis: [...READING_METRICS.delivery_basis], pronunciation: "not_assessed" },
    drill_b: { passage_key: "C", passage_title: "Overfitting", shown_seconds: 30, explanation: RELAY_TRANSCRIPT, concepts: EVALUATION.summary.drill_b!.concepts.map(({ key, text, status, quote }) => ({ key, text, status, quote })), coverage: 0.625, paraphrase: "own_words", misstatements: EVALUATION.summary.drill_b!.misstatements, followups: [{ question: "What part were you least sure about?", answer_excerpt: "The last part, about the defences.", note: "Named the gap precisely." }], score: 3, basis: EVALUATION.summary.drill_b!.basis },
    technical_reading: { score: "3.4", formula: "0.4 × Drill A + 0.6 × Drill B" },
  },
  G_structured: { card_number: 2, card_title: CARD.title, bullets: EVALUATION.summary.bullets.map((b) => ({ index: b.index, text: b.text ?? "", status: b.status, excerpt: b.quote })), flow: "mechanical", examples_used: true, gap_handling: "honest", followup: { question: TIMELINE.stages[4]!.items[1]!.exchanges[0]!.text, answer_excerpt: "I don't do it very consistently yet.", analysis: EVALUATION.summary.followup_analysis }, score: "3.0" },
  H_trainability: { learning_strategy: EVALUATION.summary.learning_strategy, teaching_exercise: EVALUATION.summary.teach_back, problem_solving: "Reasoned through the timestamp edge case and named what wasn't tested.", training_readiness: "Committed to the full schedule.", ai_ml_openness: "eager", development_areas: ["Structured speaking", "Model training end to end"] },
  I_integrity: { observations: [{ type: "tab_hidden", title: "Switched away from the tab", count: 2, first_at: "2026-10-08T09:20:00Z", review_status: "unreviewed" }], unreviewed: 2, note: "Signals are observations, not determinations." },
  J_training_plan: EVALUATION.training_plan,
  K_human_review: { review_items: REVIEW_ITEMS, decisions: [], status: "pending" },
  eligibility_note: "Work-authorisation answers are reviewed by people only; they are not part of this report or any score.",
};

// ---------------------------------------------------------------- analytics

export const ANALYTICS: IntakeAnalytics = {
  period: { since: "2026-09-09T00:00:00Z", until: NOW, days: 30, job_id: null, target_role: null, reviewer_id: null },
  funnel: { invitations: 42, onboarded: 37, started: 33, completed: 29, incomplete: 4, evaluated: 28, decided: 21 },
  average_weighted_total: "3.4120",
  average_duration_s: 2610,
  stage_completion: CONFIG.kit.stages.map((s, i) => ({ stage: s.key, title: s.title, completed: 33 - i, incomplete: i, rate: (33 - i) / 33 })),
  bands: [
    { band: "strong_accept", label: "Strong accept", count: 6 },
    { band: "accept_with_notes", label: "Accept with notes", count: 11 },
    { band: "borderline", label: "Borderline", count: 6 },
    { band: "reject", label: "Reject", count: 3 },
    { band: "insufficient_evidence", label: "Insufficient evidence", count: 2 },
  ],
  decisions: [
    { outcome: "accept", label: "Accept", count: 7 },
    { outcome: "accept_with_notes", label: "Accept with notes", count: 8 },
    { outcome: "second_interview", label: "Second interview", count: 3 },
    { outcome: "reject", label: "Reject", count: 2 },
    { outcome: "request_evidence", label: "Request evidence", count: 1 },
  ],
  overrides: { count: 3, rate: 0.1429 },
  competency_averages: EVALUATION.competencies.map((c, i) => ({ key: c.key, label: c.label, average: ["3.8", "3.3", "3.1", "3.0", "3.9", "3.7", "4.2"][i]!, scored: 28 - (i === 1 ? 2 : 0), insufficient: i === 1 ? 2 : 0 })),
  technical_weaknesses: [
    { key: "claim_team_level", label: "Résumé claim was team-level work", count: 9 },
    { key: "self_rating_not_supported", label: "Self-rating not supported", count: 5 },
    { key: "verification_incorrect:python", label: "Python check answered incorrectly", count: 4 },
    { key: "drill_b_low", label: "Relay covered few concepts", count: 3 },
  ],
  training_needs: [
    { module: "ai_ml_fundamentals", label: "AI/ML fundamentals", count: 28 },
    { module: "structured_speaking", label: "Structured speaking", count: 12 },
    { module: "technical_reading_practice", label: "Technical reading practice", count: 7 },
  ],
  reviewers: [{ id: IDS.reviewer, full_name: "Dana Whitfield", decisions: 14 }, { id: "u-2", full_name: "Ravi Iyer", decisions: 7 }],
  by_target_role: [
    { target_role: "ai_ml", label: "AI/ML", count: 19, average_weighted_total: "3.5210" },
    { target_role: "data_analyst", label: "Data analyst", count: 12, average_weighted_total: "3.3900" },
    { target_role: "product_manager_technical", label: null, count: 7, average_weighted_total: "3.2200" },
    { target_role: "product_manager_growth", label: null, count: 4, average_weighted_total: "3.0500" },
  ],
};
