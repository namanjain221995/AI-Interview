import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BACKEND_ENUMS, CANDIDATE_STEPS, PERMISSIONS } from "@/lib/types";

const ENUMS_PY = path.resolve(__dirname, "../../../../api/app/domain/enums.py");

/** Parses `class Name(StrEnum):` blocks and their `MEMBER = "value"` lines. */
function parsePythonEnums(source: string): Record<string, Record<string, string>> {
  const result: Record<string, Record<string, string>> = {};
  let current: string | null = null;
  for (const line of source.split("\n")) {
    const classMatch = /^class (\w+)\(StrEnum\):/.exec(line);
    if (classMatch) {
      current = classMatch[1]!;
      result[current] = {};
      continue;
    }
    if (current && /^\S/.test(line) && !line.startsWith("class ")) {
      current = null;
      continue;
    }
    const memberMatch = current ? /^ {4}([A-Z][A-Z0-9_]*) = "([^"]*)"/.exec(line) : null;
    if (current && memberMatch) result[current]![memberMatch[1]!] = memberMatch[2]!;
  }
  return result;
}

describe.skipIf(!existsSync(ENUMS_PY))("backend enum mirror", () => {
  const python = existsSync(ENUMS_PY) ? parsePythonEnums(readFileSync(ENUMS_PY, "utf8")) : {};

  it("mirrors every Python StrEnum", () => {
    expect(Object.keys(python).length).toBeGreaterThan(40);
    expect(Object.keys(BACKEND_ENUMS).sort()).toEqual(Object.keys(python).sort());
  });

  it.each(Object.keys(python))("%s matches member for member", (name) => {
    expect(BACKEND_ENUMS[name as keyof typeof BACKEND_ENUMS]).toEqual(python[name]);
  });
});

describe("static domain lists", () => {
  it("lists each permission once", () => {
    expect(new Set(PERMISSIONS).size).toBe(PERMISSIONS.length);
  });

  it("orders the candidate journey", () => {
    expect(CANDIDATE_STEPS.map((s) => s.key)).toEqual(["welcome", "instructions", "consent", "resume", "round", "system-check", "interview"]);
  });
});
