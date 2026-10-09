/**
 * Coding question editor model. Function problems use a language-neutral
 * signature that drives the sandbox harness for every language; SQL problems
 * compare result sets. Tests are edited as JSON text and parsed on save.
 */

import type { BankStatus, CodeLanguage, CodingQuestion, CodingQuestionInput, CodingSignature, CodingTestCase, CodingValueType, Difficulty } from "@/lib/types";

import { cleanList, slugKey, uniqueKey } from "../lib/utils";

/** The backend signature also accepts an optional tolerance for float comparisons. */
export type EditorSignature = CodingSignature & { tolerance?: number | null };

export const VALUE_TYPES: readonly CodingValueType[] = ["int", "long", "float", "bool", "string", "int[]", "long[]", "float[]", "string[]", "bool[]", "int[][]", "string[][]"];
export const COMPARISONS: ReadonlyArray<{ value: CodingSignature["comparison"]; label: string; description: string }> = [
  { value: "exact", label: "Exact match", description: "Deep equality; 1 equals 1.0" },
  { value: "unordered", label: "Any order", description: "Arrays compared as multisets, recursively" },
  { value: "float_tolerance", label: "Approximate", description: "Numbers within 1e-6 relative tolerance" },
];
export const FUNCTION_LANGUAGES: readonly CodeLanguage[] = ["python", "javascript", "typescript", "java", "go", "cpp"];

export type CodingMode = "function" | "sql";

export interface TestRow {
  /** Local key for React; also the test id. */
  id: string;
  hidden: boolean;
  description: string;
  /** Function problems: JSON array of arguments and JSON expected value. */
  argsText: string;
  expectedText: string;
  /** SQL problems. */
  setupSql: string;
  expectedRowsText: string;
  ordered: boolean;
}

export interface CodingDraft {
  title: string;
  slug: string;
  slugTouched: boolean;
  difficulty: Difficulty;
  competency_keys: string[];
  role_tags: string[];
  prompt_markdown: string;
  mode: CodingMode;
  languages: CodeLanguage[];
  signature: EditorSignature;
  starter_code: Partial<Record<CodeLanguage, string>>;
  reference_solutions: Partial<Record<CodeLanguage, string>>;
  sql_setup: string;
  tests: TestRow[];
  expected_concepts: string[];
  time_complexity: string;
  space_complexity: string;
  rubric_excellent: string;
  rubric_good: string;
  rubric_weak: string;
  rubric_criteria: Array<{ key: string; value: string }>;
  /** Three hint levels; empty levels are written live by the interviewer when requested. */
  hints: [string, string, string];
  followups: string[];
  common_errors: string[];
  time_limit_minutes: number;
  status: BankStatus;
}

export function emptySignature(): EditorSignature {
  return { function_name: "solve", params: [{ name: "nums", type: "int[]" }], return_type: "int", comparison: "exact" };
}

export function emptyTest(index: number, hidden: boolean): TestRow {
  return { id: `t${index + 1}`, hidden, description: "", argsText: "", expectedText: "", setupSql: "", expectedRowsText: "", ordered: false };
}

export const DEFAULT_CRITERIA = ["correctness", "efficiency", "code_quality", "edge_cases", "communication"];

export function emptyCodingDraft(mode: CodingMode = "function"): CodingDraft {
  return {
    title: "",
    slug: "",
    slugTouched: false,
    difficulty: "medium",
    competency_keys: [],
    role_tags: [],
    prompt_markdown: "",
    mode,
    languages: mode === "sql" ? ["sql"] : [...FUNCTION_LANGUAGES],
    signature: emptySignature(),
    starter_code: {},
    reference_solutions: {},
    sql_setup: "",
    tests: [emptyTest(0, false), emptyTest(1, false), emptyTest(2, true)],
    expected_concepts: [],
    time_complexity: "",
    space_complexity: "",
    rubric_excellent: "",
    rubric_good: "",
    rubric_weak: "",
    rubric_criteria: DEFAULT_CRITERIA.map((key) => ({ key, value: "" })),
    hints: ["", "", ""],
    followups: [],
    common_errors: [],
    time_limit_minutes: 20,
    status: "draft",
  };
}

/** JSON with a space after commas ("[[2, 7, 11, 15], 9]") so test data stays readable. */
export function compactJson(value: unknown): string {
  if (value === undefined) return "";
  if (Array.isArray(value)) return `[${value.map((v) => compactJson(v === undefined ? null : v)).join(", ")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${JSON.stringify(k)}: ${compactJson(v === undefined ? null : v)}`)
      .join(", ")}}`;
  }
  return JSON.stringify(value);
}

function json(value: unknown): string {
  return compactJson(value);
}

function isSignature(value: unknown): value is EditorSignature {
  return Boolean(value && typeof value === "object" && typeof (value as CodingSignature).function_name === "string" && Array.isArray((value as CodingSignature).params));
}

export function codingDraftFrom(q: CodingQuestion): CodingDraft {
  const languages = (q.languages ?? []) as CodeLanguage[];
  const mode: CodingMode = languages.includes("sql") || (!isSignature(q.signature) && Boolean(q.sql_setup)) ? "sql" : "function";
  const rubric = (q.rubric ?? {}) as Record<string, unknown>;
  const criteria = rubric.criteria && typeof rubric.criteria === "object" ? (rubric.criteria as Record<string, unknown>) : {};
  const hints = [...(q.hints ?? []), "", "", ""].slice(0, 3) as [string, string, string];
  return {
    title: q.title ?? "",
    slug: q.slug ?? "",
    slugTouched: true,
    difficulty: q.difficulty ?? "medium",
    competency_keys: [...(q.competency_keys ?? [])],
    role_tags: [...(q.role_tags ?? [])],
    prompt_markdown: q.prompt_markdown ?? "",
    mode,
    languages: languages.length ? languages : mode === "sql" ? ["sql"] : [...FUNCTION_LANGUAGES],
    signature: isSignature(q.signature) ? { ...q.signature, params: q.signature.params.map((p) => ({ ...p })) } : emptySignature(),
    starter_code: { ...(q.starter_code ?? {}) },
    reference_solutions: { ...(q.reference_solutions ?? {}) },
    sql_setup: q.sql_setup ?? "",
    tests: (q.tests ?? []).map((t, i) => ({
      id: t.id || `t${i + 1}`,
      hidden: Boolean(t.hidden),
      description: t.description ?? "",
      argsText: t.args !== undefined ? json(t.args) : "",
      expectedText: t.expected !== undefined ? json(t.expected) : "",
      setupSql: t.setup_sql ?? "",
      expectedRowsText: t.expected_rows !== undefined ? json(t.expected_rows) : "",
      ordered: Boolean(t.ordered),
    })),
    expected_concepts: [...(q.expected_concepts ?? [])],
    time_complexity: q.optimal_complexity?.time ?? "",
    space_complexity: q.optimal_complexity?.space ?? "",
    rubric_excellent: typeof rubric.excellent === "string" ? rubric.excellent : "",
    rubric_good: typeof rubric.good === "string" ? rubric.good : "",
    rubric_weak: typeof rubric.weak === "string" ? rubric.weak : "",
    rubric_criteria: Object.keys(criteria).length
      ? Object.entries(criteria).map(([key, value]) => ({ key, value: String(value ?? "") }))
      : DEFAULT_CRITERIA.map((key) => ({ key, value: "" })),
    hints,
    followups: [...(q.followups ?? [])],
    common_errors: [...(q.common_errors ?? [])],
    time_limit_minutes: q.time_limit_minutes ?? 20,
    status: q.status ?? "active",
  };
}

// ----------------------------------------------------------------- parsing

export type ParseResult = { ok: true; value: unknown } | { ok: false; error: string };

export function parseJson(text: string): ParseResult {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, error: "Required." };
  try {
    return { ok: true, value: JSON.parse(trimmed) };
  } catch (error) {
    return { ok: false, error: `Not valid JSON: ${(error as Error).message.replace(/^JSON\.parse: /, "")}` };
  }
}

const INT32: [number, number] = [-(2 ** 31), 2 ** 31 - 1];
const SAFE = Number.MAX_SAFE_INTEGER;

/** Mirrors app.seed.loaders.value_matches_type. */
export function matchesType(value: unknown, type: CodingValueType | string): boolean {
  if (type.endsWith("[]")) return Array.isArray(value) && value.every((v) => matchesType(v, type.slice(0, -2)));
  switch (type) {
    case "bool":
      return typeof value === "boolean";
    case "int":
      return typeof value === "number" && Number.isInteger(value) && value >= INT32[0] && value <= INT32[1];
    case "long":
      return typeof value === "number" && Number.isInteger(value) && Math.abs(value) <= SAFE;
    case "float":
      return typeof value === "number" && Number.isFinite(value);
    case "string":
      return typeof value === "string";
    default:
      return false;
  }
}

/** Mirrors app.seed.loaders (Identifier, Slug, _RESERVED_WORDS). */
const IDENT = /^[a-z][a-z0-9_]*$/;
const SLUG = /^[a-z][a-z0-9_]{1,119}$/;
const TEST_ID = /^[a-z0-9_]{1,40}$/;
const KEY = /^[a-z0-9_]{1,80}$/;
export const RESERVED_WORDS = new Set(
  `and as assert async await break case catch char class const continue def default defer del delete do
  double elif else enum except export extends false final finally float for from func function go goto
  if implements import in instanceof int interface is lambda let long map new nil none nonlocal not null
  or package pass private protected public raise range return select short static string struct super
  switch template this throw true try type typeof typename union unsigned var void volatile while with
  yield chan fallthrough auto register signed sizeof bool boolean byte native operator friend`.split(/\s+/),
);

function reservedForm(identifier: string): string | null {
  for (const form of new Set([identifier, toLowerCamel(identifier)])) if (RESERVED_WORDS.has(form.toLowerCase())) return form;
  return null;
}

export interface CodingIssues {
  errors: Record<string, string>;
  warnings: Record<string, string>;
}

/** Validation mirroring the coding-questions API (`_QuestionFields`), plus advice that never blocks saving. */
export function validateCodingDraft(draft: CodingDraft): CodingIssues {
  const errors: Record<string, string> = {};
  const warnings: Record<string, string> = {};
  const title = draft.title.trim();
  if (title.length < 3) errors.title = "Give the problem a title (at least 3 characters).";
  else if (title.length > 200) errors.title = "Keep the title under 200 characters.";
  if (!SLUG.test(draft.slug)) errors.slug = "2 to 120 characters: a lowercase letter first, then letters, digits or underscores.";
  const prompt = draft.prompt_markdown.trim();
  if (prompt.length < 20) errors.prompt_markdown = "Describe the problem, with at least one example.";
  else if (prompt.length > 20_000) errors.prompt_markdown = "Keep the prompt under 20,000 characters.";
  if (!Number.isInteger(draft.time_limit_minutes) || draft.time_limit_minutes < 5 || draft.time_limit_minutes > 120) errors.time_limit_minutes = "Use 5 to 120 minutes.";
  if (draft.languages.length === 0) errors.languages = "Choose at least one language.";
  if (draft.competency_keys.length > 8) errors.competency_keys = "Link at most 8 competencies.";
  if (draft.role_tags.length > 8) errors.role_tags = "Use at most 8 role tags.";
  else if (draft.role_tags.some((t) => !KEY.test(t))) errors.role_tags = "Role tags use lowercase letters, digits and underscores.";

  if (draft.mode === "function") {
    const sig = draft.signature;
    if (!IDENT.test(sig.function_name) || sig.function_name.length > 60) {
      errors["signature.function_name"] = "Use snake_case, e.g. two_sum (other languages get twoSum).";
    } else {
      const reserved = reservedForm(sig.function_name);
      if (reserved) errors["signature.function_name"] = `“${reserved}” is a reserved word in a supported language.`;
    }
    if (sig.params.length === 0) errors["signature.params"] = "Add at least one parameter.";
    else if (sig.params.length > 6) errors["signature.params"] = "Use at most 6 parameters.";
    const names = new Set<string>();
    sig.params.forEach((p, i) => {
      if (!IDENT.test(p.name) || p.name.length > 60) errors[`signature.params.${i}.name`] = "snake_case: a lowercase letter first, then letters, digits or underscores.";
      else if (names.has(p.name)) errors[`signature.params.${i}.name`] = "Parameter names must be unique.";
      else {
        const reserved = reservedForm(p.name);
        if (reserved) errors[`signature.params.${i}.name`] = `“${reserved}” is a reserved word in a supported language.`;
      }
      names.add(p.name);
    });
    if (sig.comparison === "unordered" && !sig.return_type.endsWith("[]")) errors["signature.comparison"] = "Any-order comparison needs an array return type.";
    if (sig.comparison === "float_tolerance" && sig.return_type !== "float" && sig.return_type !== "float[]") {
      errors["signature.comparison"] = "Approximate comparison needs a float or float[] return type.";
    }
    if (sig.tolerance !== null && sig.tolerance !== undefined && (sig.comparison !== "float_tolerance" || !(sig.tolerance > 0 && sig.tolerance < 1))) {
      errors["signature.tolerance"] = "Tolerance is a number between 0 and 1, used only with approximate comparison.";
    }
  } else if (!/create\s+table/i.test(draft.sql_setup)) {
    errors.sql_setup = "Create the tables the query runs against (CREATE TABLE …).";
  }

  for (const language of draft.languages) {
    if (draft.mode === "sql" && !draft.starter_code[language]?.trim()) warnings[`starter_code.${language}`] = "No starter query: candidates start from an empty editor.";
    if (draft.mode === "function" && !draft.starter_code[language]?.trim()) warnings[`starter_code.${language}`] = "No starter code yet: one is generated from the signature when you save.";
    if (!draft.reference_solutions[language]?.trim()) warnings[`reference_solutions.${language}`] = "No reference solution, so this language can't be validated in the sandbox.";
  }

  const ids = new Set<string>();
  const visible = draft.tests.filter((t) => !t.hidden).length;
  const hidden = draft.tests.length - visible;
  if (draft.tests.length === 0) errors.tests = "Add test cases.";
  else if (draft.tests.length > 100) errors.tests = "Use at most 100 tests.";
  else if (visible < 1) errors.tests = "Candidates need at least one visible test.";
  else if (hidden < 1) warnings.tests = "No hidden tests: submissions are only checked against the examples candidates can see.";
  else if (visible < 2 || hidden < 5) warnings.tests = "We recommend at least 2 visible and 5 hidden tests covering edge cases.";

  draft.tests.forEach((t, i) => {
    const id = t.id.trim();
    if (!id) errors[`tests.${i}.id`] = "Give the test an id.";
    else if (!TEST_ID.test(id)) errors[`tests.${i}.id`] = "Lowercase letters, digits and underscores (max 40).";
    else if (ids.has(id)) errors[`tests.${i}.id`] = "Test ids must be unique.";
    ids.add(id);
    if (t.description.length > 300) errors[`tests.${i}.id`] = "Keep the description under 300 characters.";
    if (draft.mode === "function") {
      const args = parseJson(t.argsText);
      if (!args.ok) errors[`tests.${i}.args`] = args.error;
      else if (!Array.isArray(args.value)) errors[`tests.${i}.args`] = "Arguments are a JSON array, one item per parameter.";
      else if (args.value.length !== draft.signature.params.length) errors[`tests.${i}.args`] = `Expected ${draft.signature.params.length} arguments, got ${args.value.length}.`;
      else {
        const bad = draft.signature.params.findIndex((p, j) => !matchesType((args.value as unknown[])[j], p.type));
        if (bad !== -1) errors[`tests.${i}.args`] = `Argument ${bad + 1} (${draft.signature.params[bad]!.name}) isn't a valid ${draft.signature.params[bad]!.type}.`;
      }
      const expected = parseJson(t.expectedText);
      if (!expected.ok) errors[`tests.${i}.expected`] = expected.error;
      else if (!matchesType(expected.value, draft.signature.return_type)) errors[`tests.${i}.expected`] = `Isn't a valid ${draft.signature.return_type}.`;
    } else {
      const rows = parseJson(t.expectedRowsText);
      if (!rows.ok) errors[`tests.${i}.expected_rows`] = rows.error;
      else if (!Array.isArray(rows.value) || !rows.value.every((r) => Array.isArray(r))) errors[`tests.${i}.expected_rows`] = "Rows are a JSON array of arrays, e.g. [[200]].";
    }
  });

  // Hints are levels: 1 (nudge) to 3 (near-solution). Missing levels are written live, but order matters.
  const filled = draft.hints.map((h) => h.trim().length > 0);
  draft.hints.forEach((hint, i) => {
    if (hint.length > 600) errors[`hints.${i}`] = "Keep hints under 600 characters.";
    else if (filled[i] && filled.slice(0, i).some((f) => !f)) errors[`hints.${i}`] = `Write hint ${filled.indexOf(false) + 1} first: hints are given in order.`;
  });
  const missing = filled.map((f, i) => (f ? null : i + 1)).filter((n): n is number => n !== null);
  if (missing.length && !Object.keys(errors).some((k) => k.startsWith("hints."))) {
    warnings.hints = missing.length === 3 ? "No curated hints: the interviewer writes all three live when a candidate asks." : `Hint ${missing.join(" and ")} will be written live when a candidate asks.`;
  }

  const concepts = cleanList(draft.expected_concepts);
  if (concepts.length === 0) warnings.expected_concepts = "Concepts help the evaluator recognise a strong approach.";
  if (concepts.length > 12) errors.expected_concepts = "List at most 12 concepts.";
  if (concepts.some((c) => c.length > 600)) errors.expected_concepts = "Keep each concept under 600 characters.";
  if (cleanList(draft.followups).length > 8) errors.followups = "Use at most 8 follow-ups.";
  if (cleanList(draft.common_errors).length > 8) errors.common_errors = "List at most 8 common errors.";
  if (draft.time_complexity.length > 60) errors.time_complexity = "Keep it short, e.g. O(n log n).";
  if (draft.space_complexity.length > 60) errors.space_complexity = "Keep it short, e.g. O(n).";
  return { errors, warnings };
}

export function toCodingInput(draft: CodingDraft): CodingQuestionInput {
  const tests: CodingTestCase[] = draft.tests.map((t) => {
    const base: CodingTestCase = { id: t.id.trim(), hidden: t.hidden };
    if (t.description.trim()) base.description = t.description.trim();
    if (draft.mode === "function") {
      const args = parseJson(t.argsText);
      const expected = parseJson(t.expectedText);
      base.args = args.ok ? (args.value as unknown[]) : [];
      base.expected = expected.ok ? expected.value : null;
    } else {
      if (t.setupSql.trim()) base.setup_sql = t.setupSql.trim();
      const rows = parseJson(t.expectedRowsText);
      base.expected_rows = rows.ok ? (rows.value as unknown[][]) : [];
      if (t.ordered) base.ordered = true;
    }
    return base;
  });
  const criteria = Object.fromEntries(draft.rubric_criteria.filter((c) => c.key.trim() && c.value.trim()).map((c) => [c.key.trim(), c.value.trim()]));
  const rubric: Record<string, unknown> = {};
  if (draft.rubric_excellent.trim()) rubric.excellent = draft.rubric_excellent.trim();
  if (draft.rubric_good.trim()) rubric.good = draft.rubric_good.trim();
  if (draft.rubric_weak.trim()) rubric.weak = draft.rubric_weak.trim();
  if (Object.keys(criteria).length) rubric.criteria = criteria;
  const pick = (map: Partial<Record<CodeLanguage, string>>) =>
    Object.fromEntries(draft.languages.filter((l) => map[l]?.trim()).map((l) => [l, map[l]!])) as Partial<Record<CodeLanguage, string>>;
  const complexity: { time?: string; space?: string } = {};
  if (draft.time_complexity.trim()) complexity.time = draft.time_complexity.trim();
  if (draft.space_complexity.trim()) complexity.space = draft.space_complexity.trim();
  return {
    title: draft.title.trim(),
    slug: draft.slug,
    difficulty: draft.difficulty,
    competency_keys: [...draft.competency_keys],
    role_tags: cleanList(draft.role_tags),
    prompt_markdown: draft.prompt_markdown.trim(),
    languages: [...draft.languages],
    // SQL problems have no signature (the API rejects anything but null).
    signature: (draft.mode === "function" ? signatureOut(draft.signature) : null) as unknown as CodingQuestionInput["signature"],
    starter_code: pick(draft.starter_code),
    reference_solutions: pick(draft.reference_solutions),
    tests,
    sql_setup: draft.mode === "sql" ? draft.sql_setup.trim() : null,
    expected_concepts: cleanList(draft.expected_concepts),
    optimal_complexity: complexity,
    rubric,
    hints: draft.hints.map((h) => h.trim()).filter(Boolean),
    followups: cleanList(draft.followups),
    common_errors: cleanList(draft.common_errors),
    time_limit_minutes: draft.time_limit_minutes,
    status: draft.status,
  };
}

function signatureOut(sig: EditorSignature): EditorSignature {
  const out: EditorSignature = {
    function_name: sig.function_name,
    params: sig.params.map((p) => ({ name: p.name.trim(), type: p.type })),
    return_type: sig.return_type,
    comparison: sig.comparison,
  };
  if (sig.comparison === "float_tolerance" && typeof sig.tolerance === "number") out.tolerance = sig.tolerance;
  return out;
}

export function nextTestId(tests: readonly TestRow[]): string {
  return uniqueKey(`t${tests.length + 1}`, tests.map((t) => t.id), "_");
}

export function slugFromTitle(title: string): string {
  return slugKey(title, 120);
}

// ------------------------------------------------------- signature preview

export function toLowerCamel(name: string): string {
  const [head, ...rest] = name.split("_");
  return (head ?? "") + rest.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join("");
}

export function entryPointName(functionName: string, language: CodeLanguage): string {
  return language === "python" ? functionName : toLowerCamel(functionName);
}

const TYPE_MAP: Record<Exclude<CodeLanguage, "sql">, Record<CodingValueType, string>> = {
  python: { int: "int", long: "int", float: "float", bool: "bool", string: "str", "int[]": "list[int]", "long[]": "list[int]", "float[]": "list[float]", "string[]": "list[str]", "bool[]": "list[bool]", "int[][]": "list[list[int]]", "string[][]": "list[list[str]]" },
  javascript: { int: "number", long: "number", float: "number", bool: "boolean", string: "string", "int[]": "number[]", "long[]": "number[]", "float[]": "number[]", "string[]": "string[]", "bool[]": "boolean[]", "int[][]": "number[][]", "string[][]": "string[][]" },
  typescript: { int: "number", long: "number", float: "number", bool: "boolean", string: "string", "int[]": "number[]", "long[]": "number[]", "float[]": "number[]", "string[]": "string[]", "bool[]": "boolean[]", "int[][]": "number[][]", "string[][]": "string[][]" },
  java: { int: "int", long: "long", float: "double", bool: "boolean", string: "String", "int[]": "int[]", "long[]": "long[]", "float[]": "double[]", "string[]": "String[]", "bool[]": "boolean[]", "int[][]": "int[][]", "string[][]": "String[][]" },
  go: { int: "int", long: "int64", float: "float64", bool: "bool", string: "string", "int[]": "[]int", "long[]": "[]int64", "float[]": "[]float64", "string[]": "[]string", "bool[]": "[]bool", "int[][]": "[][]int", "string[][]": "[][]string" },
  cpp: { int: "int", long: "long long", float: "double", bool: "bool", string: "string", "int[]": "vector<int>", "long[]": "vector<long long>", "float[]": "vector<double>", "string[]": "vector<string>", "bool[]": "vector<bool>", "int[][]": "vector<vector<int>>", "string[][]": "vector<vector<string>>" },
};

const ZERO: Record<Exclude<CodeLanguage, "sql">, (type: CodingValueType) => string> = {
  python: (t) => (t.endsWith("]") ? "[]" : t === "bool" ? "False" : t === "string" ? '""' : t === "float" ? "0.0" : "0"),
  javascript: (t) => (t.endsWith("]") ? "[]" : t === "bool" ? "false" : t === "string" ? '""' : "0"),
  typescript: (t) => (t.endsWith("]") ? "[]" : t === "bool" ? "false" : t === "string" ? '""' : "0"),
  java: (t) => (t.endsWith("]") ? `new ${TYPE_MAP.java[t].replace(/\[\]/, "[0]")}` : t === "bool" ? "false" : t === "string" ? '""' : t === "float" ? "0.0" : "0"),
  go: (t) => (t.endsWith("]") ? `${TYPE_MAP.go[t]}{}` : t === "bool" ? "false" : t === "string" ? '""' : "0"),
  cpp: (t) => (t.endsWith("]") ? "{}" : t === "bool" ? "false" : t === "string" ? '""' : "0"),
};

/** The entry-point declaration candidates see for `language`. */
export function signaturePreview(sig: CodingSignature, language: Exclude<CodeLanguage, "sql">): string {
  const name = entryPointName(sig.function_name || "solve", language);
  const map = TYPE_MAP[language];
  const params = sig.params;
  switch (language) {
    case "python":
      return `def ${name}(${params.map((p) => `${p.name}: ${map[p.type]}`).join(", ")}) -> ${map[sig.return_type]}:`;
    case "javascript":
      return `function ${name}(${params.map((p) => p.name).join(", ")})`;
    case "typescript":
      return `function ${name}(${params.map((p) => `${p.name}: ${map[p.type]}`).join(", ")}): ${map[sig.return_type]}`;
    case "java":
      return `public ${map[sig.return_type]} ${name}(${params.map((p) => `${map[p.type]} ${p.name}`).join(", ")})`;
    case "go":
      return `func ${name}(${params.map((p) => `${p.name} ${map[p.type]}`).join(", ")}) ${map[sig.return_type]}`;
    case "cpp":
      return `${map[sig.return_type]} ${name}(${params.map((p) => `${p.type.endsWith("]") || p.type === "string" ? `${map[p.type]}&` : map[p.type]} ${p.name}`).join(", ")})`;
  }
}

/** Starter code skeleton matching the harness conventions (class Solution for Java/C++, package main for Go). */
export function starterTemplate(sig: CodingSignature, language: Exclude<CodeLanguage, "sql">): string {
  const decl = signaturePreview(sig, language);
  const ret = ZERO[language](sig.return_type);
  const map = TYPE_MAP[language];
  switch (language) {
    case "python":
      return `${decl}\n    # Write your solution here.\n    return ${ret}\n`;
    case "javascript":
      return `/**\n${sig.params.map((p) => ` * @param {${map[p.type]}} ${p.name}`).join("\n")}\n * @return {${map[sig.return_type]}}\n */\n${decl} {\n  // Write your solution here.\n  return ${ret};\n}\n`;
    case "typescript":
      return `${decl} {\n  // Write your solution here.\n  return ${ret};\n}\n`;
    case "java":
      return `import java.util.*;\n\nclass Solution {\n    ${decl} {\n        // Write your solution here.\n        return ${ret};\n    }\n}\n`;
    case "go":
      return `package main\n\n${decl} {\n\t// Write your solution here.\n\treturn ${ret}\n}\n`;
    case "cpp":
      return `#include <string>\n#include <vector>\nusing namespace std;\n\nclass Solution {\npublic:\n    ${decl} {\n        // Write your solution here.\n        return ${ret};\n    }\n};\n`;
  }
}
