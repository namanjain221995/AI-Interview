/**
 * Drill A alignment highlights (design 11.1 Reading tab), built from the F0 reading
 * engine's `ReadingMetrics.events` over the passage text.
 *
 * Offsets: `ref_start`/`ref_end` are character offsets into `paragraphs.join("\n\n")`
 * after NFKC normalisation and one-for-one apostrophe folding (reading.py
 * `_prepare_text`), so slicing the NFKC text with them is exact.
 *
 * Extra words (insertions, repetitions) carry no passage offset. Their position is
 * estimated from the transcript index (`hyp_index`) and the running offset between
 * passage and transcript positions, then kept between the neighbouring events that do
 * have exact offsets. The view labels these positions as approximate.
 */

import type { ReadingEvent, ReadingEventKind } from "../types";

/** Mirror of reading.py `_WORD_RX` (same alternatives, same order). */
const WORD_RX = /\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\d+(?:\.\d+)?(?:st|nd|rd|th)?%?|[a-z0-9]+(?:'[a-z0-9]+)*|(?<=\d)[–—-](?=\d)|[&%]/gi;
const APOSTROPHES = /[’‘ʼ`]/g;

export type WordMark = Extract<ReadingEventKind, "omission" | "substitution" | "asr_variant" | "reordered">;
export type ExtraKind = Extract<ReadingEventKind, "insertion" | "repetition">;

export interface TextPart {
  type: "text";
  text: string;
}

export interface MarkedPart {
  type: "mark";
  mark: WordMark;
  /** Passage text covered by the mark (one word, or a run of omitted words). */
  text: string;
  /** What the recogniser heard instead (substitution, ASR variant). */
  heard: string | null;
  /** Number of passage words in the run. */
  words: number;
}

export interface ExtraPart {
  type: "extra";
  /** Extra words heard at this point, in transcript order. */
  items: Array<{ kind: ExtraKind; word: string }>;
  /** Always true: extra words have no exact passage position. */
  approximate: true;
}

export type AlignmentPart = TextPart | MarkedPart | ExtraPart;

export interface AlignedParagraph {
  index: number;
  parts: AlignmentPart[];
}

export interface AlignmentCounts {
  omission: number;
  substitution: number;
  asr_variant: number;
  reordered: number;
  insertion: number;
  repetition: number;
}

export interface Alignment {
  paragraphs: AlignedParagraph[];
  counts: AlignmentCounts;
  /** Events whose offsets did not fit the passage (ignored, counted for transparency). */
  unplaced: number;
  /** Extra words, for a plain list next to the passage. */
  extras: Array<{ kind: ExtraKind; word: string }>;
}

interface WordSpan {
  start: number;
  end: number;
}

/** Word spans of the prepared passage text (same tokenisation as the reading engine). */
export function wordSpans(text: string): WordSpan[] {
  const folded = text.replace(APOSTROPHES, "'");
  const spans: WordSpan[] = [];
  for (const match of folded.matchAll(WORD_RX)) {
    if (match.index === undefined || !match[0]) continue;
    spans.push({ start: match.index, end: match.index + match[0].length });
  }
  return spans;
}

/** The passage as the reading engine saw it. */
export function preparePassage(paragraphs: readonly string[]): string {
  return paragraphs.join("\n\n").normalize("NFKC");
}

const isRefEvent = (e: ReadingEvent) => e.ref_start !== null && e.ref_end !== null && e.ref_index !== null;
const isExtra = (e: ReadingEvent): e is ReadingEvent & { kind: ExtraKind } => e.kind === "insertion" || e.kind === "repetition";

interface Anchor {
  /** Insert before this word index (== word count: after the last word). */
  word: number;
  kind: ExtraKind;
  text: string;
  order: number;
}

/**
 * Estimated passage word before which each extra word was heard.
 * `delta` tracks passage-token position minus transcript-token position for the
 * matched words that follow the last event (matches are one-to-one between events).
 */
function anchorExtras(events: readonly ReadingEvent[], wordCount: number): Anchor[] {
  const anchors: Anchor[] = [];
  let delta = 0;
  events.forEach((event, order) => {
    if (event.kind === "omission") {
      delta += 1;
      return;
    }
    if ((event.kind === "substitution" || event.kind === "asr_variant") && event.ref_index !== null && event.hyp_index !== null) {
      // A one-to-many merge consumes several transcript tokens for one passage token.
      const heardWords = event.hyp_word ? event.hyp_word.trim().split(/\s+/).length : 1;
      delta = event.ref_index - event.hyp_index - (heardWords - 1);
      return;
    }
    if (!isExtra(event) || event.hyp_index === null) return;
    const estimatedToken = event.hyp_index + delta;
    delta -= 1;

    // Exact neighbours bound the estimate.
    let prev: ReadingEvent | undefined;
    for (let i = order - 1; i >= 0; i--) if (isRefEvent(events[i]!)) { prev = events[i]; break; }
    let next: ReadingEvent | undefined;
    for (let i = order + 1; i < events.length; i++) if (isRefEvent(events[i]!)) { next = events[i]; break; }
    const near = prev ?? next;
    // Token -> word: contractions and numbers expand to several tokens per word.
    const tokenOffset = near && near.ref_index !== null && near.ref_word_index !== null ? near.ref_index - near.ref_word_index : 0;
    let word = estimatedToken - tokenOffset;
    const low = prev?.ref_word_index !== null && prev?.ref_word_index !== undefined ? prev.ref_word_index + 1 : 0;
    const high = next?.ref_word_index !== null && next?.ref_word_index !== undefined ? next.ref_word_index : wordCount;
    word = Math.max(low, Math.min(high, word));
    word = Math.max(0, Math.min(wordCount, word));
    anchors.push({ word, kind: event.kind, text: event.hyp_word ?? "", order });
  });
  return anchors;
}

function emptyCounts(): AlignmentCounts {
  return { omission: 0, substitution: 0, asr_variant: 0, reordered: 0, insertion: 0, repetition: 0 };
}

/**
 * Splits the passage into text, marked words and extra-word markers per paragraph.
 * Adjacent omitted words merge into one struck-through run.
 */
export function buildAlignment(paragraphs: readonly string[], events: readonly ReadingEvent[] | null | undefined): Alignment {
  const text = preparePassage(paragraphs);
  const spans = wordSpans(text);
  const counts = emptyCounts();
  const list = events ?? [];
  for (const event of list) counts[event.kind] = (counts[event.kind] ?? 0) + 1;

  // Word index -> mark (exact events).
  const marks = new Map<number, { mark: WordMark; heard: string | null }>();
  let unplaced = 0;
  const startToWord = new Map(spans.map((s, i) => [s.start, i]));
  for (const event of list) {
    if (isExtra(event) || event.ref_start === null || event.ref_end === null) continue;
    let word = startToWord.get(event.ref_start);
    if (word === undefined && event.ref_word_index !== null && spans[event.ref_word_index]) word = event.ref_word_index;
    if (word === undefined || event.ref_end > text.length) {
      unplaced += 1;
      continue;
    }
    // Several tokens of one word ("can't" -> can not) keep the strongest mark.
    const existing = marks.get(word);
    if (!existing || rank(event.kind as WordMark) > rank(existing.mark)) {
      marks.set(word, { mark: event.kind as WordMark, heard: event.kind === "omission" ? null : event.hyp_word });
    }
  }

  const anchors = anchorExtras(list, spans.length);
  const extrasAt = new Map<number, ExtraPart["items"]>();
  for (const anchor of anchors) {
    const items = extrasAt.get(anchor.word) ?? [];
    items.push({ kind: anchor.kind, word: anchor.text });
    extrasAt.set(anchor.word, items);
  }

  // Paragraph boundaries in the joined text.
  const bounds: Array<{ start: number; end: number }> = [];
  let cursor = 0;
  for (const paragraph of paragraphs) {
    const length = paragraph.normalize("NFKC").length;
    bounds.push({ start: cursor, end: cursor + length });
    cursor += length + 2;
  }

  const out: AlignedParagraph[] = bounds.map((_, index) => ({ index, parts: [] }));
  const paragraphOf = (offset: number) => {
    const found = bounds.findIndex((b) => offset >= b.start && offset <= b.end);
    return found === -1 ? bounds.length - 1 : found;
  };
  const pushText = (p: number, value: string) => {
    if (!value) return;
    const parts = out[p]!.parts;
    const last = parts.at(-1);
    if (last?.type === "text") last.text += value;
    else parts.push({ type: "text", text: value });
  };
  const pushExtra = (p: number, items: ExtraPart["items"]) => {
    out[p]!.parts.push({ type: "extra", items, approximate: true });
  };

  if (bounds.length === 0) return { paragraphs: [], counts, unplaced, extras: [] };
  let pos = bounds[0]!.start;
  let current = 0;
  // Close paragraphs up to `p`: their trailing text (final punctuation) stays with them.
  const flushTo = (p: number) => {
    while (current < p) {
      pushText(current, text.slice(pos, bounds[current]!.end));
      current += 1;
      pos = bounds[current]!.start;
    }
  };
  let i = 0;
  while (i < spans.length) {
    const span = spans[i]!;
    const p = paragraphOf(span.start);
    flushTo(p);
    pushText(p, text.slice(pos, span.start));
    const extras = extrasAt.get(i);
    if (extras) pushExtra(p, extras);
    const mark = marks.get(i);
    if (!mark) {
      pushText(p, text.slice(span.start, span.end));
      pos = span.end;
      i += 1;
      continue;
    }
    if (mark.mark === "omission") {
      // Merge a run of omitted words within the paragraph.
      let j = i;
      while (
        j + 1 < spans.length &&
        marks.get(j + 1)?.mark === "omission" &&
        !extrasAt.has(j + 1) &&
        paragraphOf(spans[j + 1]!.start) === p &&
        /^[^\S\n]*$/.test(text.slice(spans[j]!.end, spans[j + 1]!.start).replace(/[,;:]/g, ""))
      ) {
        j += 1;
      }
      out[p]!.parts.push({ type: "mark", mark: "omission", text: text.slice(span.start, spans[j]!.end), heard: null, words: j - i + 1 });
      pos = spans[j]!.end;
      i = j + 1;
      continue;
    }
    out[p]!.parts.push({ type: "mark", mark: mark.mark, text: text.slice(span.start, span.end), heard: mark.heard, words: 1 });
    pos = span.end;
    i += 1;
  }
  // Extras after the last word, then the remaining text of every paragraph.
  const tail = extrasAt.get(spans.length);
  if (tail) pushExtra(current, tail);
  flushTo(bounds.length - 1);
  pushText(current, text.slice(pos, bounds[current]!.end));

  const extras = anchors.map((a) => ({ kind: a.kind, word: a.text }));
  return { paragraphs: out, counts, unplaced, extras };
}

function rank(mark: WordMark): number {
  return mark === "omission" ? 4 : mark === "substitution" ? 3 : mark === "reordered" ? 2 : 1;
}

/** Plain-language description of one marked word (screen readers, tooltips). */
export function describeMark(part: MarkedPart): string {
  switch (part.mark) {
    case "omission":
      return part.words > 1 ? `${part.words} words not read` : "Not read";
    case "substitution":
      return part.heard ? `Read as “${part.heard}”` : "Read differently";
    case "asr_variant":
      return part.heard ? `Heard as “${part.heard}”, counted as read` : "Close recognition, counted as read";
    case "reordered":
      return "Read, out of order";
  }
}

export function describeExtra(part: ExtraPart): string {
  const words = part.items.map((item) => `${item.kind === "repetition" ? "repeated" : "extra"} “${item.word}”`);
  return `Heard near here (approximate): ${words.join(", ")}`;
}
