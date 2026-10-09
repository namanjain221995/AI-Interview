import { describe, expect, it } from "vitest";

import { buildAlignment, describeExtra, describeMark, preparePassage, wordSpans, type MarkedPart } from "../lib/alignment";
import type { ReadingEvent } from "../types";
import { PASSAGE_B, READING_EVENTS } from "./fixtures";

function plainText(alignment: ReturnType<typeof buildAlignment>): string[] {
  return alignment.paragraphs.map((p) => p.parts.map((part) => (part.type === "extra" ? "" : part.text)).join(""));
}

describe("word spans mirror the reading engine's tokenisation", () => {
  it("lands every exact event offset on a word that reads as the event's word", () => {
    const text = preparePassage(PASSAGE_B);
    const spans = wordSpans(text);
    for (const event of READING_EVENTS.filter((e) => e.ref_start !== null)) {
      const span = spans[event.ref_word_index!]!;
      expect(span.start).toBe(event.ref_start);
      expect(text.slice(span.start, span.end)).toBe(event.ref_word);
    }
  });

  it("keeps apostrophes inside words and splits hyphens and numbers like the engine", () => {
    const text = "You can't see far: self-attention, 1,000 steps and 3rd place.";
    const words = wordSpans(text).map((s) => text.slice(s.start, s.end));
    expect(words).toEqual(["You", "can't", "see", "far", "self", "attention", "1,000", "steps", "and", "3rd", "place"]);
  });
});

describe("buildAlignment", () => {
  const alignment = buildAlignment(PASSAGE_B, READING_EVENTS);
  const marks = alignment.paragraphs.flatMap((p) => p.parts.filter((x): x is MarkedPart => x.type === "mark"));

  it("reproduces the passage text exactly, paragraph by paragraph", () => {
    expect(plainText(alignment)).toEqual(PASSAGE_B.map((p) => p.normalize("NFKC")));
  });

  it("merges a run of omitted words into one struck-through mark", () => {
    const omission = marks.find((m) => m.mark === "omission");
    expect(omission).toMatchObject({ text: "on a foggy hill", words: 4, heard: null });
    expect(describeMark(omission!)).toBe("4 words not read");
  });

  it("shows substitutions with the heard word and ASR variants as counted", () => {
    expect(marks.filter((m) => m.mark === "substitution").map((m) => [m.text, m.heard])).toEqual([
      ["beneath", "under"],
      ["steepest", "deepest"],
    ]);
    const variant = marks.find((m) => m.mark === "asr_variant")!;
    expect([variant.text, variant.heard]).toEqual(["parameters", "perimeters"]);
    expect(describeMark(variant)).toContain("counted as read");
  });

  it("places extra words at their estimated position, never out of order", () => {
    const first = alignment.paragraphs[0]!.parts;
    const repIndex = first.findIndex((p) => p.type === "extra");
    // The repeated "is" goes right before the passage's "is" (word 2).
    expect(first[repIndex - 1]).toMatchObject({ type: "text" });
    expect((first[repIndex - 1] as { text: string }).text.endsWith("Gradient descent ")).toBe(true);
    expect(first[repIndex]).toMatchObject({ type: "extra", items: [{ kind: "repetition", word: "is" }] });

    const last = alignment.paragraphs[2]!.parts;
    const extra = last.find((p) => p.type === "extra")!;
    expect(extra).toMatchObject({ items: [{ kind: "insertion", word: "basically" }], approximate: true });
    const after = last[last.indexOf(extra) + 1] as { text: string };
    expect(after.text.startsWith("forever")).toBe(true);
    expect(describeExtra(extra as never)).toContain("approximate");
  });

  it("counts every event kind and lists the extra words", () => {
    expect(alignment.counts).toEqual({ omission: 4, substitution: 2, asr_variant: 1, reordered: 0, insertion: 1, repetition: 1 });
    expect(alignment.extras).toEqual([
      { kind: "repetition", word: "is" },
      { kind: "insertion", word: "basically" },
    ]);
    expect(alignment.unplaced).toBe(0);
  });

  it("returns the plain passage without events and skips offsets outside it", () => {
    const plain = buildAlignment(PASSAGE_B, []);
    expect(plain.paragraphs.every((p) => p.parts.every((x) => x.type === "text"))).toBe(true);
    const bad: ReadingEvent = { kind: "omission", ref_index: 999, ref_word_index: 999, ref_start: 9999, ref_end: 10005, ref_word: "x", hyp_index: null, hyp_word: null, sentence: null };
    expect(buildAlignment(PASSAGE_B, [bad]).unplaced).toBe(1);
  });

  it("puts an extra word after the last word when it was heard at the very end", () => {
    const tail: ReadingEvent = { kind: "insertion", ref_index: null, ref_word_index: null, ref_start: null, ref_end: null, ref_word: null, hyp_index: 500, hyp_word: "done", sentence: null };
    const result = buildAlignment(["One two three."], [tail]);
    const parts = result.paragraphs[0]!.parts;
    expect(parts.map((p) => p.type)).toEqual(["text", "extra", "text"]);
    expect((parts[0] as { text: string }).text).toBe("One two three");
    expect((parts[2] as { text: string }).text).toBe(".");
  });
});
