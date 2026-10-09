/**
 * Coding score helpers. Hints lower the counted coding score transparently
 * (1 - 0.07 per hint, see ENGINE_DESIGN.md §7); they never fail a candidate.
 */

export const HINT_PENALTY = 0.07;

/** Multiplier applied to the coding score estimate for the hints used. */
export function assistanceFactor(hintsUsed: number): number {
  return Math.max(0, 1 - HINT_PENALTY * Math.max(0, hintsUsed));
}
