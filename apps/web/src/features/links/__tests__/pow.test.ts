import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { solvePow, type PowChallenge } from "../pow";

async function sha256Hex(text: string): Promise<string> {
  const digest = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function challengeFor(number: number, maxnumber = 1000): Promise<PowChallenge> {
  const salt = "0a1b2c?scope=link:abc&expires=9999999999";
  return { algorithm: "SHA-256", challenge: await sha256Hex(`${salt}${number}`), salt, signature: "f".repeat(64), maxnumber };
}

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());

describe("solvePow (S15)", () => {
  it("finds the number and encodes the ALTCHA payload the server verifies", async () => {
    const challenge = await challengeFor(777);
    const payload = JSON.parse(atob(await solvePow(challenge)));
    expect(payload).toEqual({ algorithm: "SHA-256", challenge: challenge.challenge, number: 777, salt: challenge.salt, signature: challenge.signature });
  });

  it("finds numbers at the edges of a batch and of the range", async () => {
    for (const n of [0, 255, 256, 1000]) {
      expect(JSON.parse(atob(await solvePow(await challengeFor(n)))).number).toBe(n);
    }
  });

  it("stops when cancelled and fails without Web Crypto", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(solvePow(await challengeFor(900), controller.signal)).rejects.toThrow(/cancelled/);
    vi.stubGlobal("crypto", {});
    await expect(solvePow(await challengeFor(1))).rejects.toThrow(/Web Crypto/);
  });
});
