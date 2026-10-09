import { describe, expect, it } from "vitest";

import { checkForm, warningKey } from "../fairness";
import { defaultSchema, newCustomField } from "../formSchema";
import type { FormField, FormSchema } from "../types";

function withField(partial: Partial<FormField>): FormSchema {
  const base = newCustomField(partial.type ?? "text", []);
  return { version: 1, fields: [...defaultSchema().fields, { ...base, key: "c_q", ...partial }] };
}

describe("fairness guard mirror", () => {
  it.each([
    ["Age", "age"],
    ["Your DOB", "age"],
    ["Date of birth", "age"],
    ["Graduation year", "age"],
    ["Passing year", "age"],
    ["Gender", "gender"],
    ["Preferred pronouns", "gender"],
    ["Marital status", "family_status"],
    ["Father's name", "family_status"],
    ["Number of children", "family_status"],
    ["Religion", "religion"],
    ["Caste", "religion"],
    ["Nationality", "nationality"],
    ["Passport number", "nationality"],
    ["Upload a photo link", "appearance"],
    ["Height in cm", "appearance"],
    ["Blood group", "health"],
    ["Any disability?", "health"],
    ["Current CTC", "salary_history"],
    ["Last drawn salary", "salary_history"],
  ])("blocks %s", (label, topic) => {
    const { errors } = checkForm(withField({ label }));
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({ key: "c_q", topic });
    expect(errors[0]!.message).toMatch(/never collect/);
  });

  it("scans keys, help text, placeholders and options, enabled or not", () => {
    expect(checkForm(withField({ key: "c_date_of_birth", label: "When?" })).errors[0]?.topic).toBe("age");
    expect(checkForm(withField({ label: "About you", help_text: "Include your age" })).errors[0]?.topic).toBe("age");
    expect(checkForm(withField({ label: "Title", type: "select", options: ["Mr", "Mrs", "Dr"] })).errors[0]?.topic).toBe("gender");
    expect(checkForm(withField({ label: "Status", type: "select", options: ["Single", "Married"] })).errors[0]?.topic).toBe("family_status");
    expect(checkForm(withField({ label: "Gender", enabled: false })).errors).toHaveLength(1);
  });

  it("does not flag ordinary job questions or the built-in fields", () => {
    for (const label of ["Preferred shift", "Expected start date", "Average response time", "Current role", "Page you found us on", "Engaged with our product?", "Programming languages", "Racing game experience"]) {
      expect(checkForm(withField({ label })).errors, label).toEqual([]);
    }
    expect(checkForm(defaultSchema())).toEqual({ errors: [], warnings: [] });
  });

  it("warns about borderline questions until each is acknowledged", () => {
    const cases: Array<[string, string]> = [
      ["Expected salary", "expected_pay"],
      ["Work authorization for India", "right_to_work"],
      ["Home address", "address"],
      ["PIN code", "address"],
      ["Explain any career break", "employment_gap"],
      ["Mother tongue", "languages"],
    ];
    for (const [label, code] of cases) {
      const result = checkForm(withField({ label }));
      expect(result.errors, label).toEqual([]);
      expect(result.warnings.map((w) => w.code), label).toContain(code);
      expect(result.warnings.every((w) => !w.acknowledged)).toBe(true);
    }
    const acked = checkForm(withField({ label: "Expected salary" }), [warningKey("c_q", "expected_pay")]);
    expect(acked.warnings).toEqual([expect.objectContaining({ code: "expected_pay", acknowledged: true })]);
  });
});
