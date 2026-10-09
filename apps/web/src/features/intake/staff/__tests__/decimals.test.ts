import { describe, expect, it } from "vitest";

import { decimalToNumber, formatDecimal, formatHundred, formatScore5, formatTotal, formatWeightPercent, normalizeDecimal, shareOf } from "../lib/decimals";

describe("decimal display helpers", () => {
  it("normalises strings and numbers without float artefacts", () => {
    expect(normalizeDecimal("3.6700")).toBe("3.67");
    expect(normalizeDecimal("0004.2000")).toBe("4.2");
    expect(normalizeDecimal(4.199999999)).toBe("4.2");
    expect(normalizeDecimal("-0.000")).toBe("0");
    expect(normalizeDecimal("abc")).toBeNull();
    expect(normalizeDecimal(null)).toBeNull();
    expect(normalizeDecimal(Number.NaN)).toBeNull();
  });

  it("truncates instead of rounding, so a total never crosses a band edge on screen", () => {
    expect(formatTotal("4.1950")).toBe("4.19");
    expect(formatTotal("4.2000")).toBe("4.20");
    expect(formatTotal("3.2999")).toBe("3.29");
    expect(formatTotal("2.4999")).toBe("2.49");
    expect(formatTotal("5")).toBe("5.00");
    expect(formatTotal(null)).toBe("—");
    expect(formatDecimal("-0.004", 2)).toBe("0.00");
  });

  it("formats competency scores, weights and the out-of-100 conversion", () => {
    expect(formatScore5("3.4")).toBe("3.4");
    expect(formatScore5("4")).toBe("4.0");
    expect(formatWeightPercent("0.20")).toBe("20%");
    expect(formatWeightPercent("0.05")).toBe("5%");
    expect(formatWeightPercent("0.125")).toBe("12.5%");
    expect(formatWeightPercent("1")).toBe("100%");
    expect(formatHundred("72.600")).toBe("72.6");
    expect(formatHundred("73.0")).toBe("73.0");
  });

  it("gives bar widths only from numbers it can parse", () => {
    expect(decimalToNumber("0.8000")).toBe(0.8);
    expect(shareOf("2.5")).toBe(0.5);
    expect(shareOf("9")).toBe(1);
    expect(shareOf(null)).toBe(0);
  });
});
