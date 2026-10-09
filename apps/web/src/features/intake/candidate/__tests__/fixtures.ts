/** Shared fixtures for the intake candidate tests: realtime messages shaped like INTAKE_INTERVIEW.md §7.1. */
import type { InterviewAction } from "@/features/realtime/store";
import type { ServerMessage } from "@/lib/realtime/protocol";

let seq = 0;

export function server(msg: Record<string, unknown>, at = 0): InterviewAction {
  seq += 1;
  return { type: "server", msg: { seq, ...msg } as unknown as ServerMessage, at };
}

export const PASSAGE_B = [
  "Gradient descent is an optimisation algorithm used to train machine learning models. The goal is to minimise a cost function, which measures how wrong the model's predictions are.",
  "Imagine standing on a foggy hill and wanting to reach the lowest point. You can't see far, so you feel the slope beneath your feet and take a step in the steepest downhill direction, and you repeat this until the ground is flat.",
];

export const CARD_1 = {
  title: "Working From Home vs. Working in an Office",
  instruction: "Speak for about 2 minutes, connecting all four points below into one flowing explanation:",
  bullets: ["One thing working from home is good for", "One thing an office is good for", "The biggest challenge of working remotely", "Which you would choose, and why"],
};

export const STAGES = [
  { key: "greeting", number: 1, title: "Introduction", status: "done" },
  { key: "resume", number: 2, title: "Your experience", status: "done" },
  { key: "mandatory", number: 3, title: "Standard questions", status: "done" },
  { key: "reading", number: 4, title: "Reading exercise", status: "current" },
  { key: "topic", number: 5, title: "Speaking exercise", status: "pending" },
  { key: "learning", number: 6, title: "Learning and growth", status: "pending" },
  { key: "close", number: 7, title: "Your questions", status: "pending" },
];

export function sessionState(overrides: Record<string, unknown> = {}, at = 0): InterviewAction {
  return server(
    {
      type: "session.state",
      session_id: "s1",
      status: "live",
      turn_state: "listening",
      round_name: "Intake Interview",
      round_type: "intake",
      interviewer_name: "Maya",
      elapsed_s: 900,
      remaining_s: 1800,
      time_limit_s: 2700,
      question_count: 14,
      current_question: null,
      show_subtitles: true,
      allow_typed_answers: true,
      integrity: {},
      avatar_state: "LISTENING",
      intake: { stage: "reading", stage_number: 4, stage_title: "Reading exercise", stages: STAGES, activity: null, answer_mode: "standard", soft_limit_s: null },
      ...overrides,
    },
    at,
  );
}

export function activityStart(kind: "reading_aloud" | "read_relay" | "topic_card", overrides: Record<string, unknown> = {}, at = 0): InterviewAction {
  const phases =
    kind === "reading_aloud"
      ? [{ phase: "read", duration_ms: 180_000, capture: true }]
      : kind === "read_relay"
        ? [
            { phase: "read", duration_ms: 30_000, capture: false },
            { phase: "relay", duration_ms: 150_000, capture: true },
          ]
        : [
            { phase: "prepare", duration_ms: 60_000, capture: false },
            { phase: "speak", duration_ms: 120_000, capture: true },
          ];
  const title = kind === "reading_aloud" ? "What Is an API?" : kind === "read_relay" ? "Gradient Descent" : CARD_1.title;
  return server(
    { type: "activity.start", activity_id: `act-${kind}`, kind, stage: kind === "topic_card" ? "topic" : "reading", title, phases, status: "armed", attempt: 1, resumed: false, ...overrides },
    at,
  );
}

export function activityPhase(kind: "reading_aloud" | "read_relay" | "topic_card", phase: string, overrides: Record<string, unknown> = {}, at = 0): InterviewAction {
  const durations: Record<string, number> = { read: kind === "read_relay" ? 30_000 : 180_000, relay: 150_000, prepare: 60_000, speak: 120_000 };
  const capture = phase === "relay" || phase === "speak" || (kind === "reading_aloud" && phase === "read");
  const content =
    kind === "topic_card"
      ? CARD_1
      : phase === "read"
        ? { title: kind === "read_relay" ? "Gradient Descent" : "What Is an API?", paragraphs: PASSAGE_B }
        : null;
  return server(
    {
      type: "activity.phase",
      activity_id: `act-${kind}`,
      kind,
      phase,
      status: "running",
      duration_ms: durations[phase],
      remaining_ms: durations[phase],
      capture,
      can_finish: true,
      content,
      hide_content: phase === "relay",
      server_time_ms: 1_791_000_000_000,
      ...overrides,
    },
    at,
  );
}

export function activityEnd(kind: "reading_aloud" | "read_relay" | "topic_card", reason = "completed", at = 0): InterviewAction {
  return server({ type: "activity.end", activity_id: `act-${kind}`, kind, reason }, at);
}
