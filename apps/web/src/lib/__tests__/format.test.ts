import { describe, expect, it } from "vitest";

import {
  confidenceLabel,
  EMPTY,
  formatBytes,
  formatClock,
  formatCompact,
  formatDuration,
  formatLatency,
  formatList,
  formatMinutes,
  formatNumber,
  formatPercent,
  formatRelative,
  formatRelativeOrDate,
  formatScore,
  greeting,
  humanize,
  initials,
  pluralize,
  truncate,
} from "@/lib/format";

const L = "en-US";

describe("numbers", () => {
  it("formats numbers, compact numbers and percentages", () => {
    expect(formatNumber(1234.56, {}, L)).toBe("1,234.6");
    expect(formatCompact(1284, L)).toBe("1.3K");
    expect(formatCompact(4_200_000, L)).toBe("4.2M");
    expect(formatPercent(0.734, 0, L)).toBe("73%");
    expect(formatPercent(0.7349, 1, L)).toBe("73.5%");
  });

  it("formats scores on their scale", () => {
    expect(formatScore(7.44, 10, L)).toBe("7.4");
    expect(formatScore(7, 10, L)).toBe("7.0");
    expect(formatScore(72.6, 100, L)).toBe("73");
  });

  it("formats bytes with familiar units", () => {
    expect(formatBytes(512, L)).toBe("512 B");
    expect(formatBytes(1536, L)).toBe("1.5 KB");
    expect(formatBytes(10 * 1024 * 1024, L)).toBe("10 MB");
    expect(formatBytes(3.25 * 1024 * 1024 * 1024, L)).toBe("3.3 GB");
  });

  it("returns the placeholder for missing values", () => {
    for (const fn of [formatNumber, formatCompact, formatPercent, formatBytes, formatDuration, formatClock, formatLatency]) {
      expect(fn(null as never)).toBe(EMPTY);
      expect(fn(undefined as never)).toBe(EMPTY);
    }
    expect(formatScore(Number.NaN)).toBe(EMPTY);
  });

  it("words confidence", () => {
    expect(confidenceLabel(0.9)).toBe("High");
    expect(confidenceLabel(0.6)).toBe("Moderate");
    expect(confidenceLabel(0.3)).toBe("Low");
    expect(confidenceLabel(0.1)).toBe("Very low");
  });
});

describe("durations", () => {
  it("formats durations", () => {
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(120)).toBe("2m");
    expect(formatDuration(750)).toBe("12m 30s");
    expect(formatDuration(3900)).toBe("1h 05m");
    expect(formatDuration(-5)).toBe("0s");
  });

  it("formats clocks and minutes", () => {
    expect(formatClock(725)).toBe("12:05");
    expect(formatClock(3723)).toBe("1:02:03");
    expect(formatClock(-30)).toBe("-0:30");
    expect(formatMinutes(1830)).toBe("31 min");
    expect(formatMinutes(5400)).toBe("1h 30m");
    expect(formatLatency(180, L)).toBe("180 ms");
    expect(formatLatency(2430, L)).toBe("2.4 s");
  });
});

describe("dates", () => {
  const now = new Date("2026-10-07T12:00:00Z");

  it("formats relative times", () => {
    expect(formatRelative("2026-10-07T11:59:30Z", now, L)).toBe("just now");
    expect(formatRelative("2026-10-07T11:55:00Z", now, L)).toBe("5 minutes ago");
    expect(formatRelative("2026-10-07T15:00:00Z", now, L)).toBe("in 3 hours");
    expect(formatRelative("2026-10-06T12:00:00Z", now, L)).toBe("yesterday");
    expect(formatRelative("not a date", now, L)).toBe(EMPTY);
  });

  it("switches to a calendar date after a week", () => {
    expect(formatRelativeOrDate("2026-10-05T12:00:00Z", now, L)).toBe("2 days ago");
    expect(formatRelativeOrDate("2026-09-01T12:00:00Z", now, L)).toMatch(/Sep 1, 2026/);
  });

  it("greets by local time of day", () => {
    expect(greeting(new Date(2026, 9, 7, 9))).toBe("Good morning");
    expect(greeting(new Date(2026, 9, 7, 14))).toBe("Good afternoon");
    expect(greeting(new Date(2026, 9, 7, 20))).toBe("Good evening");
  });
});

describe("text", () => {
  it("derives initials", () => {
    expect(initials("Priya Shah")).toBe("PS");
    expect(initials("  ana maria de la cruz ")).toBe("AC");
    expect(initials("Madonna")).toBe("M");
    expect(initials("")).toBe("?");
    expect(initials(null)).toBe("?");
  });

  it("humanizes, pluralizes, truncates and lists", () => {
    expect(humanize("system_design")).toBe("System design");
    expect(humanize("in-progress")).toBe("In progress");
    expect(pluralize(1, "question", undefined, L)).toBe("1 question");
    expect(pluralize(3, "person", "people", L)).toBe("3 people");
    expect(truncate("Distributed systems", 12)).toBe("Distributed…");
    expect(truncate("Short", 12)).toBe("Short");
    expect(formatList(["Go", "Rust", "Python"], L)).toBe("Go, Rust, and Python");
  });
});
