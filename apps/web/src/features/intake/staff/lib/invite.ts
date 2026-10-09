/**
 * Invitation text for the shown-once intake link. The platform sends no email
 * (design decision 18): staff copy the message or open a draft in their mail app.
 * The access code is never placed in the message; it travels separately.
 */

import { formatDateTime } from "@/lib/format";

export interface InvitationMessageInput {
  candidateName: string | null | undefined;
  programName: string | null | undefined;
  organizationName: string | null | undefined;
  inviteUrl: string;
  durationMinutes: number;
  expiresAt: string | null | undefined;
  hasAccessCode: boolean;
}

export function firstName(name: string | null | undefined): string {
  return (name ?? "").trim().split(/\s+/)[0] ?? "";
}

export function invitationSubject(input: Pick<InvitationMessageInput, "programName">): string {
  return `Your intake interview${input.programName ? ` for the ${input.programName}` : ""}`;
}

export function invitationMessage(input: InvitationMessageInput): string {
  const greeting = firstName(input.candidateName) ? `Hi ${firstName(input.candidateName)},` : "Hello,";
  const program = input.programName ? ` for the ${input.programName}` : "";
  const lines = [
    greeting,
    "",
    `You're invited to an intake interview${program}. It takes about ${input.durationMinutes} minutes and runs in your browser.`,
    "You'll talk with an AI interviewer about your experience, read a short passage aloud, read another one and explain it, and speak for two minutes on a topic. Saying \"I don't know\" is fine.",
    "A person on our team reviews the interview and contacts you about next steps.",
    "",
    "Before you start: find a quiet room, use a working microphone (and camera if asked), and a recent version of Chrome, Edge or Firefox.",
    "",
    `Your personal link (please don't share it): ${input.inviteUrl}`,
  ];
  if (input.expiresAt) lines.push(`It works until ${formatDateTime(input.expiresAt)}.`);
  if (input.hasAccessCode) lines.push("We'll send your access code in a separate message.");
  lines.push("", "Thank you,", input.organizationName?.trim() || "The hiring team");
  return lines.join("\n");
}

/** A mailto: draft (the mail app opens; nothing is sent by the platform). */
export function mailtoHref(email: string | null | undefined, subject: string, body: string): string {
  const to = email ? encodeURIComponent(email) : "";
  return `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
