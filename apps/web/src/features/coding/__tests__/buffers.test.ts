import { describe, expect, it } from "vitest";

import type { CodeLanguage, CodingRunResultMsg } from "@/lib/realtime/protocol";

import {
  availableLanguages,
  currentCode,
  decidePaste,
  editBuffer,
  fallbackStarter,
  initialBuffers,
  isPristine,
  loadBuffers,
  saveBuffers,
  starterFor,
  switchLanguage,
} from "../buffers";
import { hiddenCounts, visibleTests } from "../ResultsPanel";

const question = {
  languages: ["python", "javascript", "go", "cobol"] as CodeLanguage[],
  starter_code: { python: "def solve(x):\n    pass\n", javascript: "function solve(x) {\n}\n" } as Record<string, string>,
};

function memoryStorage() {
  const map = new Map<string, string>();
  return { getItem: (k: string) => map.get(k) ?? null, setItem: (k: string, v: string) => void map.set(k, v) };
}

describe("coding buffers", () => {
  it("offers only supported languages and seeds each from its starter", () => {
    expect(availableLanguages(question)).toEqual(["python", "javascript", "go"]);
    const s = initialBuffers("c1", question, "python");
    expect(s.language).toBe("python");
    expect(s.buffers.python).toBe(question.starter_code.python);
    expect(s.buffers.javascript).toBe(question.starter_code.javascript);
    expect(s.buffers.go).toBe(fallbackStarter("go"));
    expect(s.version).toBe(0);
  });

  it("keeps every buffer when switching languages", () => {
    let s = initialBuffers("c1", question, "python");
    s = editBuffer(s, "def solve(x):\n    return 42\n");
    expect(s.version).toBe(1);
    s = switchLanguage(s, question, "javascript");
    expect(currentCode(s)).toBe(question.starter_code.javascript);
    s = editBuffer(s, "function solve(x) { return 1; }");
    s = switchLanguage(s, question, "python");
    expect(currentCode(s)).toBe("def solve(x):\n    return 42\n");
    expect(s.buffers.javascript).toBe("function solve(x) { return 1; }");
    expect(s.version).toBe(4);
    expect(isPristine(s, question, "go")).toBe(true);
    expect(isPristine(s, question, "python")).toBe(false);
  });

  it("does not bump the version for no-op edits or same-language switches", () => {
    const s = initialBuffers("c1", question, "python");
    expect(editBuffer(s, s.buffers.python!)).toBe(s);
    expect(switchLanguage(s, question, "python")).toBe(s);
  });

  it("prefers the server snapshot for the active language on resume", () => {
    const s = initialBuffers("c1", question, "javascript", "function solve(x) { return x; }");
    expect(currentCode(s)).toBe("function solve(x) { return x; }");
    expect(s.buffers.python).toBe(question.starter_code.python);
  });

  it("restores locally saved buffers (newer than the debounced server copy)", () => {
    const storage = memoryStorage();
    let s = initialBuffers("c1", question, "python");
    s = editBuffer(s, "print('local')");
    s = switchLanguage(s, question, "go");
    saveBuffers(s, storage);
    const restored = initialBuffers("c1", question, "python", "server copy", loadBuffers("c1", storage));
    expect(restored.language).toBe("go");
    expect(restored.buffers.python).toBe("print('local')");
    expect(restored.version).toBe(s.version);
    expect(loadBuffers("other", storage)).toBeNull();
  });

  it("falls back to the first offered language and C++ starter aliases", () => {
    const s = initialBuffers("c2", { languages: ["cpp"], starter_code: { "c++": "int main() {}" } }, "python");
    expect(s.language).toBe("cpp");
    expect(starterFor({ starter_code: { "c++": "int main() {}" } }, "cpp")).toBe("int main() {}");
  });

  it("applies the paste policy", () => {
    expect(decidePaste(500, { threshold: 200, block: false })).toEqual({ allow: true, large: true });
    expect(decidePaste(10, { threshold: 200, block: true })).toEqual({ allow: false, large: false });
  });
});

describe("run results", () => {
  const result = (over: Partial<CodingRunResultMsg>): CodingRunResultMsg => ({
    type: "coding.run_result",
    seq: 1,
    coding_session_id: "c1",
    run_id: "r",
    kind: "submit",
    status: "failed",
    stdout: "",
    stderr: "",
    compile_output: "",
    passed: 0,
    total: 0,
    tests: [],
    ...over,
  });

  it("shows visible tests with expected/actual and hidden tests only as counts", () => {
    const r = result({
      passed: 5,
      total: 8,
      tests: [
        { id: "t1", passed: true, args: [[1, 2]], expected: 3, actual: 3 },
        { id: "t2", passed: false, args: [[]], expected: 0, actual: null, error: "IndexError" },
        { id: "h1", hidden: true, passed: false, actual: "SECRET", expected: "SECRET" },
      ],
    });
    const visible = visibleTests(r);
    expect(visible.map((t) => t.id)).toEqual(["t1", "t2"]);
    expect(visible[1]).toMatchObject({ passed: false, error: "IndexError" });
    expect(JSON.stringify(visible)).not.toContain("SECRET");
    expect(hiddenCounts(r)).toEqual({ passed: 4, total: 6 });
  });

  it("has no hidden summary when every test is visible", () => {
    expect(hiddenCounts(result({ passed: 1, total: 1, tests: [{ id: "a", passed: true }] }))).toBeNull();
  });

  it("never exposes hidden test details: passed/total include hidden tests, tests[] are visible only", () => {
    const r = result({ status: "failed", passed: 9, total: 12, tests: [{ id: "v1", passed: true }, { id: "v2", passed: false, expected: 1, actual: 2 }] });
    expect(hiddenCounts(r)).toEqual({ passed: 8, total: 10 });
  });
});
