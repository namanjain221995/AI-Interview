import { describe, expect, it } from "vitest";

import {
  BUILTIN_FIELDS,
  checkAnswer,
  checkSchema,
  CUSTOM_KEY,
  defaultSchema,
  deriveKey,
  formatAnswer,
  initialValues,
  isoDay,
  moveField,
  newCustomField,
  normalizeSchema,
  publicFields,
  toAnswers,
  validateValues,
} from "../formSchema";
import { draftFromLink, fromLocalInput, newDraft, orderedRoundIds, toCreateBody, toLocalInput, validateDraft } from "../linkDraft";
import type { FormField, PublicFormField } from "../types";

const NOW = new Date("2026-10-08T10:00:00Z");

function pub(partial: Partial<PublicFormField> & Pick<PublicFormField, "key" | "type">): PublicFormField {
  return { label: partial.key, help_text: null, placeholder: null, required: false, options: [], min: null, max: null, max_length: null, ...partial };
}

describe("form schema", () => {
  it("ships the built-in catalog with name and email locked on and phone required", () => {
    const schema = defaultSchema();
    expect(schema.fields.map((f) => f.key)).toEqual(BUILTIN_FIELDS.map((f) => f.key));
    const byKey = Object.fromEntries(schema.fields.map((f) => [f.key, f]));
    expect(byKey.full_name).toMatchObject({ enabled: true, required: true, type: "text", max_length: 200 });
    expect(byKey.email).toMatchObject({ enabled: true, required: true, type: "email" });
    expect(byKey.phone).toMatchObject({ enabled: true, required: true, type: "phone", label: "Mobile number" });
    expect(byKey.location).toMatchObject({ enabled: false, label: "City" });
    expect(byKey.notice_period!.options).toContain("More than 90 days");
    expect(publicFields(schema).map((f) => f.key)).toEqual(["full_name", "email", "phone"]);
    expect(publicFields(schema)[0]).not.toHaveProperty("builtin");
  });

  it("derives unique custom keys within the server pattern", () => {
    expect(deriveKey("Preferred shift", [])).toBe("c_preferred_shift");
    expect(deriveKey("Preferred shift", ["c_preferred_shift"])).toBe("c_preferred_shift_2");
    expect(deriveKey("Café préféré!", [])).toBe("c_cafe_prefere");
    expect(deriveKey("???", [])).toBe("c_field");
    const long = deriveKey("x".repeat(80), []);
    expect(CUSTOM_KEY.test(long)).toBe(true);
    const field = newCustomField("select", ["c_choose_one"]);
    expect(field).toMatchObject({ key: "c_choose_one_2", builtin: false, enabled: true, options: ["Option 1", "Option 2"] });
    expect(newCustomField("checkbox", []).required).toBe(true);
  });

  it("moves fields and reports structural problems per key", () => {
    const schema = defaultSchema();
    const moved = moveField(schema.fields, 2, -1);
    expect(moved.slice(0, 3).map((f) => f.key)).toEqual(["full_name", "phone", "email"]);
    expect(moveField(schema.fields, 0, -1)).toEqual(schema.fields);

    const bad: FormField[] = [
      { ...newCustomField("select", []), key: "c_a", options: ["Day", "day"] },
      { ...newCustomField("text", []), key: "c_b", label: "  " },
      { ...newCustomField("number", []), key: "c_c", min: 5, max: 1 },
      { ...newCustomField("multiselect", []), key: "c_d", options: [] },
      { ...newCustomField("text", []), key: "bad key" },
    ];
    const errors = checkSchema({ version: 1, fields: bad });
    expect(errors.c_a).toMatch(/different/);
    expect(errors.c_b).toMatch(/label/);
    expect(errors.c_c).toMatch(/minimum/);
    expect(errors.c_d).toMatch(/at least one option/);
    expect(errors["bad key"]).toMatch(/key is invalid/);
    const tooMany = { version: 1, fields: Array.from({ length: 31 }, (_, i) => ({ ...newCustomField("text", []), key: `c_f${i}` })) };
    expect(checkSchema(tooMany)._form).toMatch(/at most 30/);
  });

  it("normalises partial server schemas", () => {
    const schema = normalizeSchema({ version: 4, fields: [{ key: "c_x", type: "select", label: "X", options: ["a", 3] }] });
    expect(schema.version).toBe(4);
    expect(schema.fields[0]).toMatchObject({ key: "c_x", options: ["a"], enabled: true, required: false, help_text: null, min: null });
    expect(normalizeSchema(null).fields.length).toBe(BUILTIN_FIELDS.length);
  });
});

describe("answer validation (mirror of validate_answers)", () => {
  it("checks every type with candidate-safe messages", () => {
    expect(checkAnswer(pub({ key: "n", type: "text", required: true }), "", NOW)).toBe("Fill in this field.");
    expect(checkAnswer(pub({ key: "n", type: "text", max_length: 5 }), "abcdef", NOW)).toMatch(/under 5/);
    expect(checkAnswer(pub({ key: "n", type: "text" }), "bad\u0007", NOW)).toMatch(/special characters/);
    expect(checkAnswer(pub({ key: "e", type: "email" }), "jane@example", NOW)).toMatch(/email address/);
    expect(checkAnswer(pub({ key: "e", type: "email" }), "jane@example.com", NOW)).toBeNull();
    expect(checkAnswer(pub({ key: "p", type: "phone" }), "+91 98765 43210", NOW)).toBeNull();
    expect(checkAnswer(pub({ key: "p", type: "phone" }), "12345", NOW)).toMatch(/7 to 15 digits/);
    expect(checkAnswer(pub({ key: "p", type: "phone" }), "98765x43210", NOW)).toMatch(/only digits/);
    expect(checkAnswer(pub({ key: "u", type: "url" }), "javascript:alert(1)", NOW)).toMatch(/https/);
    expect(checkAnswer(pub({ key: "u", type: "url" }), "https://github.com/jane", NOW)).toBeNull();
    expect(checkAnswer(pub({ key: "y", type: "number", min: 0, max: 60 }), "61", NOW)).toMatch(/60 or less/);
    expect(checkAnswer(pub({ key: "y", type: "number" }), "1.234", NOW)).toMatch(/two decimal/);
    expect(checkAnswer(pub({ key: "y", type: "number" }), "4.5", NOW)).toBeNull();
    expect(checkAnswer(pub({ key: "s", type: "select", options: ["Day", "Night"], required: true }), "", NOW)).toBe("Choose an option.");
    expect(checkAnswer(pub({ key: "s", type: "select", options: ["Day", "Night"] }), "Evening", NOW)).toMatch(/options shown/);
    expect(checkAnswer(pub({ key: "m", type: "multiselect", options: ["A", "B", "C"], max: 2 }), ["A", "B", "C"], NOW)).toMatch(/at most 2/);
    expect(checkAnswer(pub({ key: "m", type: "multiselect", options: ["A"], required: true }), [], NOW)).toMatch(/at least one/);
    expect(checkAnswer(pub({ key: "c", type: "checkbox", required: true }), false, NOW)).toMatch(/Tick/);
    expect(checkAnswer(pub({ key: "c", type: "checkbox", required: true }), true, NOW)).toBeNull();
  });

  it("allows availability dates from today to two years ahead only (no date of birth)", () => {
    const f = pub({ key: "d", type: "date" });
    expect(isoDay(0, NOW)).toBe("2026-10-08");
    expect(checkAnswer(f, "2026-10-08", NOW)).toBeNull();
    expect(checkAnswer(f, "2026-10-07", NOW)).toBeNull(); // one day tolerance
    expect(checkAnswer(f, "2026-10-01", NOW)).toMatch(/today or a later/);
    expect(checkAnswer(f, "1998-05-04", NOW)).toMatch(/today or a later/);
    expect(checkAnswer(f, "2028-10-07", NOW)).toBeNull();
    expect(checkAnswer(f, "2028-10-09", NOW)).toMatch(/two years/);
    expect(checkAnswer(f, "next week", NOW)).toBe("Enter a date.");
  });

  it("converts typed values to wire answers", () => {
    const fields = [
      pub({ key: "full_name", type: "text", required: true }),
      pub({ key: "email", type: "email", required: true }),
      pub({ key: "years", type: "number" }),
      pub({ key: "langs", type: "multiselect", options: ["English", "Hindi"] }),
      pub({ key: "ok", type: "checkbox" }),
      pub({ key: "city", type: "text" }),
    ];
    const values = { ...initialValues(fields), full_name: "  Jane Doe ", email: "Jane@Example.COM", years: "4.5", langs: ["Hindi"], ok: true };
    expect(validateValues(fields, values, NOW)).toEqual({});
    expect(toAnswers(fields, values)).toEqual({ full_name: "Jane Doe", email: "jane@example.com", years: 4.5, langs: ["Hindi"], ok: true, city: null });
    expect(validateValues(fields, initialValues(fields), NOW)).toEqual({ full_name: "Fill in this field.", email: "Fill in this field." });
    expect(formatAnswer(null)).toBe("Not answered");
    expect(formatAnswer(["a", "b"])).toBe("a, b");
    expect(formatAnswer(false)).toBe("No");
  });
});

describe("link draft", () => {
  it("round-trips local datetimes and validates limits", () => {
    const iso = "2026-10-20T04:30:00.000Z";
    expect(fromLocalInput(toLocalInput(iso))).toBe(iso);
    const draft = { ...newDraft("j1"), name: "Campus drive", round_ids: ["r1"] };
    expect(validateDraft(draft)).toEqual({});
    expect(validateDraft({ ...draft, name: "" }).name).toBeTruthy();
    expect(validateDraft({ ...draft, round_ids: [] }).round_ids).toBeTruthy();
    expect(validateDraft({ ...draft, opens_at: "2026-10-20T10:00", closes_at: "2026-10-20T09:00" }).closes_at).toMatch(/after opening/);
    expect(validateDraft({ ...draft, max_registrations: 0 }).max_registrations).toBeTruthy();
    expect(validateDraft({ ...draft, round_window_hours: 721 }).round_window_hours).toBeTruthy();
    expect(validateDraft({ ...draft, per_ip_hourly_limit: 4 }).per_ip_hourly_limit).toBeTruthy();
    const withAge = { ...draft, form_schema: { version: 0, fields: [...draft.form_schema.fields, { ...newCustomField("number", []), key: "c_age", label: "Age" }] } };
    expect(validateDraft(withAge).form).toMatch(/never collect/);
  });

  it("builds the create body with single-round progression and job order", () => {
    const rounds = [
      { id: "r2", sequence: 2 },
      { id: "r1", sequence: 1 },
    ] as never;
    expect(orderedRoundIds(["r2", "r1", "r2"], rounds)).toEqual(["r1", "r2"]);
    const body = toCreateBody({ ...newDraft("j1"), name: " Drive ", round_ids: ["r1"], progression: "after_review", require_access_code: true, opens_at: "" });
    expect(body).toMatchObject({ job_id: "j1", name: "Drive", round_ids: ["r1"], progression: "immediate", require_access_code: true, opens_at: null, round_window_hours: 72, per_ip_hourly_limit: 120 });
    expect(body.duplicate_policy).toEqual({ scope: "job", device: "flag", network: "flag" });
  });

  it("starts the editor from an existing link", () => {
    const draft = draftFromLink({
      id: "l1",
      name: "Drive",
      job: { id: "j1", title: "Dev", status: "open" },
      rounds: [
        { id: "r1", name: "A", round_type: "coding", sequence: 0, duration_minutes: 30, missing: false },
        { id: "r2", name: "B", round_type: "behavioral", sequence: 1, duration_minutes: 30, missing: false },
      ],
      description: null,
      progression: "after_review",
      opens_at: null,
      closes_at: null,
      max_registrations: 100,
      round_window_hours: 48,
      per_ip_hourly_limit: 1000,
      require_resume: false,
      requires_access_code: true,
      duplicate_policy: { scope: "link", device: "block", network: "off" },
      form_schema: defaultSchema(),
      acknowledged_warnings: ["c_x:address"],
    } as never);
    expect(draft).toMatchObject({ roundMode: "pipeline", round_ids: ["r1", "r2"], progression: "after_review", require_resume: false, max_registrations: 100, per_ip_hourly_limit: 1000 });
  });
});
