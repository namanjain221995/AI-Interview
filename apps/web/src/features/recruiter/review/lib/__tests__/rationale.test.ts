import { describe, expect, it } from "vitest";

import { readableRationale } from "../rationale";

const NAMES: Record<string, string> = { aws: "Amazon Web Services", ci_cd: "CI/CD", data_structures: "Data Structures" };
const nameOf = (key: string) => NAMES[key] ?? key;

describe("readableRationale", () => {
  it("names competencies and turns shares into percentages", () => {
    const text =
      "Borderline evidence. The interview reached its time limit with partial coverage: confidence 0.17 (minimum 0.60), required coverage 14% (minimum 70%). Competencies still lacking evidence: aws, ci_cd, data_structures.";
    expect(readableRationale(text, nameOf)).toBe(
      "Borderline evidence. The interview reached its time limit with partial coverage: confidence 17% (minimum 60%), required coverage 14% (minimum 70%). Competencies still lacking evidence: Amazon Web Services, CI/CD, Data Structures.",
    );
  });

  it("leaves other text untouched", () => {
    expect(readableRationale("Strong evidence across the required competencies.", nameOf)).toBe("Strong evidence across the required competencies.");
  });
});
