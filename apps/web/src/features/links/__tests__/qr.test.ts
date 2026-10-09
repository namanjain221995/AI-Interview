import { describe, expect, it } from "vitest";

import { dataCodewords, encodeData, encodeQr, formatBits, interleave, penalty, qrPath, rsEncode, versionBits } from "../qr";

describe("qr encoder", () => {
  it("computes Reed-Solomon codewords for the published 1-M HELLO WORLD example", () => {
    const data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17];
    expect(rsEncode(data, 10)).toEqual([196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
  });

  it("produces the standard format strings for level M (BCH 15,5 masked with 0x5412)", () => {
    expect(formatBits(0).toString(2).padStart(15, "0")).toBe("101010000010010");
    expect(formatBits(1).toString(2).padStart(15, "0")).toBe("101000100100101");
    expect(formatBits(5).toString(2).padStart(15, "0")).toBe("100000011001110");
    expect(formatBits(7).toString(2).padStart(15, "0")).toBe("100101010100000");
  });

  it("produces the standard version information for version 7", () => {
    expect(versionBits(7).toString(2).padStart(18, "0")).toBe("000111110010010100");
  });

  it("encodes byte-mode data with terminator and alternating pad bytes", () => {
    const words = encodeData([0x41], 1);
    expect(words).toHaveLength(dataCodewords(1));
    // 0100 | 00000001 | 01000001 | 0000 -> 0x40 0x14 0x10, then 0xEC 0x11...
    expect(words.slice(0, 5)).toEqual([0x40, 0x14, 0x10, 0xec, 0x11]);
  });

  it("interleaves blocks of unequal length (version 8: 2x38 + 2x39 data codewords)", () => {
    const data = Array.from({ length: dataCodewords(8) }, (_, i) => i % 256);
    const out = interleave(data, 8);
    expect(out).toHaveLength(242);
    // First column takes the first byte of each block in order.
    expect(out.slice(0, 4)).toEqual([0, 38, 76, 115]);
  });

  it("draws finder patterns, timing patterns and the dark module", () => {
    const qr = encodeQr("https://interview.example.com/j/AbCdEfGhIjKlMnOpQrStUvWxYz012345");
    const { modules, size } = qr;
    expect(size).toBe(qr.version * 4 + 17);
    for (const [r0, c0] of [
      [0, 0],
      [0, size - 7],
      [size - 7, 0],
    ] as const) {
      for (let dr = 0; dr < 7; dr += 1) {
        for (let dc = 0; dc < 7; dc += 1) {
          const ring = Math.max(Math.abs(dr - 3), Math.abs(dc - 3));
          expect(modules[r0 + dr]![c0 + dc]).toBe(ring !== 2);
        }
      }
    }
    for (let i = 8; i < size - 8; i += 1) {
      expect(modules[6]![i]).toBe(i % 2 === 0);
      expect(modules[i]![6]).toBe(i % 2 === 0);
    }
    expect(modules[size - 8]![8]).toBe(true);
    // Format bits around the top-left finder decode to level M and the chosen mask.
    let bits = 0;
    for (let i = 0; i <= 5; i += 1) bits |= (modules[i]![8] ? 1 : 0) << i;
    bits |= (modules[7]![8] ? 1 : 0) << 6;
    bits |= (modules[8]![8] ? 1 : 0) << 7;
    bits |= (modules[8]![7] ? 1 : 0) << 8;
    for (let i = 9; i < 15; i += 1) bits |= (modules[8]![14 - i] ? 1 : 0) << i;
    expect(bits).toBe(formatBits(qr.mask));
  });

  it("picks the version by length and refuses text that does not fit version 10", () => {
    expect(encodeQr("hi").version).toBe(1);
    expect(encodeQr("x".repeat(62)).version).toBe(4);
    expect(encodeQr("x".repeat(213)).version).toBe(10);
    expect(() => encodeQr("x".repeat(214))).toThrow(RangeError);
  });

  it("chooses the mask with the lowest penalty", () => {
    const text = "https://interview.example.com/j/token";
    const best = encodeQr(text);
    const scores = Array.from({ length: 8 }, (_, mask) => penalty(encodeQr(text, { mask }).modules));
    expect(penalty(best.modules)).toBe(Math.min(...scores));
  });

  it("renders run-length SVG path data inside the quiet zone", () => {
    const path = qrPath(encodeQr("hi"), 4);
    expect(path.startsWith("M4 4h7v1h-7z")).toBe(true);
  });
});
