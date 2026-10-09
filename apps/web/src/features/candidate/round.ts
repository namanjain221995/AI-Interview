import type { PortalInterviewView, PortalRoundView } from "./api";

/** The round this interview will run: the selected one, else the only/first offered. */
export function selectedRound(interview: Pick<PortalInterviewView, "rounds" | "selectedRoundId">): PortalRoundView | null {
  if (interview.selectedRoundId) {
    const match = interview.rounds.find((r) => r.id === interview.selectedRoundId);
    if (match) return match;
  }
  return interview.rounds.length === 1 ? interview.rounds[0]! : (interview.rounds[0] ?? null);
}
