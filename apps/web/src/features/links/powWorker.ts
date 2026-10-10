/// <reference lib="webworker" />
import { solvePow, type PowChallenge } from "./pow";

const scope = self as unknown as DedicatedWorkerGlobalScope;

scope.onmessage = (event: MessageEvent<PowChallenge>) => {
  solvePow(event.data).then(
    (payload) => scope.postMessage({ payload }),
    (error: unknown) => scope.postMessage({ error: error instanceof Error ? error.message : String(error) }),
  );
};
