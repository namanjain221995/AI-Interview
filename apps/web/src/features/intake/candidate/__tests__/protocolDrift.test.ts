/**
 * The intake activity messages in lib/realtime/protocol.ts mirror apps/api/app/schemas/realtime.py
 * (INTAKE_INTERVIEW.md §7.1). Compares the field names of each message, both ways, so a renamed or
 * added field on either side fails here instead of in the room. Skipped without the API sources.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const REALTIME_PY = path.resolve(__dirname, "../../../../../../api/app/schemas/realtime.py");
const PROTOCOL_TS = path.resolve(__dirname, "../../../../lib/realtime/protocol.ts");

/** Field names of `class Name(...)` bodies (4-space `name: type` lines). */
function pythonFields(source: string, name: string): string[] {
  const lines = source.split("\n");
  const start = lines.findIndex((line) => line.startsWith(`class ${name}(`));
  if (start === -1) return [];
  const fields: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^\S/.test(line)) break;
    const match = /^ {4}(\w+): /.exec(line);
    if (match && !match[1]!.startsWith("_")) fields.push(match[1]!);
  }
  return fields;
}

/** Field names of `export interface Name ... {` bodies (2-space `name?: type` lines). */
function tsFields(source: string, name: string): string[] {
  const lines = source.split("\n");
  const start = lines.findIndex((line) => new RegExp(`^export interface ${name}\\b`).test(line));
  if (start === -1) return [];
  const fields: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith("}")) break;
    const match = /^ {2}(\w+)\??: /.exec(line);
    if (match) fields.push(match[1]!);
  }
  return fields;
}

const MESSAGES = ["ActivityContentMsg", "ActivityPhasePlanMsg", "ActivityStartMsg", "ActivityPhaseMsg", "ActivityEndMsg", "IntakeStageMsg", "ActivitySnapshotMsg", "IntakeStateMsg"];

describe.skipIf(!existsSync(REALTIME_PY))("intake realtime protocol mirror", () => {
  const python = existsSync(REALTIME_PY) ? readFileSync(REALTIME_PY, "utf8") : "";
  const ts = readFileSync(PROTOCOL_TS, "utf8");

  it.each(MESSAGES)("%s has the same fields on both sides", (name) => {
    const py = pythonFields(python, name);
    expect(py.length).toBeGreaterThan(0);
    expect(tsFields(ts, name).sort()).toEqual(py.sort());
  });

  it("session.state carries the intake state and activity.finish matches the client message", () => {
    expect(pythonFields(python, "SessionStateMsg")).toContain("intake");
    expect(tsFields(ts, "SessionStateMsg")).toContain("intake");
    expect(pythonFields(python, "ActivityFinishMsg").sort()).toEqual(["activity_id", "phase", "type"]);
    expect(ts).toContain('{ type: "activity.finish"; activity_id: string; phase: ActivityPhase }');
  });
});
