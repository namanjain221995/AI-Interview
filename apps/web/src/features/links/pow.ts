/**
 * Proof-of-work for open-link registration (S15), in the ALTCHA challenge format: find the number whose
 * SHA-256 with the salt equals the server's challenge. It runs in a Web Worker while the candidate fills in
 * the form (about a second on a mid-range phone), so people never see a puzzle and scripts pay per attempt.
 */

export interface PowChallenge {
  algorithm: string;
  challenge: string;
  salt: string;
  signature: string;
  maxnumber: number;
}

const BATCH = 256;

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function encodePowPayload(challenge: PowChallenge, number: number): string {
  return btoa(JSON.stringify({ algorithm: challenge.algorithm, challenge: challenge.challenge, number, salt: challenge.salt, signature: challenge.signature }));
}

/** Brute-forces the number in batches of parallel digests; returns the base64 payload the server verifies. */
export async function solvePow(challenge: PowChallenge, signal?: AbortSignal): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("Web Crypto is unavailable (insecure context)");
  const encoder = new TextEncoder();
  for (let start = 0; start <= challenge.maxnumber; start += BATCH) {
    if (signal?.aborted) throw new DOMException("Proof of work cancelled", "AbortError");
    const end = Math.min(challenge.maxnumber, start + BATCH - 1);
    const numbers = Array.from({ length: end - start + 1 }, (_, i) => start + i);
    const digests = await Promise.all(numbers.map((n) => subtle.digest("SHA-256", encoder.encode(`${challenge.salt}${n}`))));
    const hit = digests.findIndex((digest) => hex(digest) === challenge.challenge);
    if (hit !== -1) return encodePowPayload(challenge, numbers[hit]!);
  }
  throw new Error("No solution in range");
}

export interface PowRun {
  promise: Promise<string>;
  cancel: () => void;
}

/** Solve off the main thread when the browser can (keeps typing smooth), else in the page. */
export function solveInBackground(challenge: PowChallenge): PowRun {
  if (typeof Worker === "undefined") {
    const controller = new AbortController();
    return { promise: solvePow(challenge, controller.signal), cancel: () => controller.abort() };
  }
  const worker = new Worker(new URL("./powWorker.ts", import.meta.url), { type: "module" });
  const promise = new Promise<string>((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<{ payload?: string; error?: string }>) => {
      worker.terminate();
      if (typeof event.data.payload === "string") resolve(event.data.payload);
      else reject(new Error(event.data.error ?? "Proof of work failed"));
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "Proof of work worker failed"));
    };
  });
  worker.postMessage(challenge);
  return { promise, cancel: () => worker.terminate() };
}
