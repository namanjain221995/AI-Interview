/**
 * Per-language editor buffers for a coding exercise. Switching language keeps
 * every buffer; each starts from the problem's starter code. Buffers persist in
 * sessionStorage per coding session, so a reload or reconnect never loses work.
 */
import type { CodeLanguage, CodingQuestionPublic } from "@/lib/realtime/protocol";

export const SUPPORTED_LANGUAGES: readonly CodeLanguage[] = ["python", "javascript", "typescript", "java", "go", "cpp", "sql"];

export const LANGUAGE_LABELS: Record<CodeLanguage, string> = {
  python: "Python",
  javascript: "JavaScript",
  typescript: "TypeScript",
  java: "Java",
  go: "Go",
  cpp: "C++",
  sql: "SQL",
};

/** Monaco language id per platform language. */
export const MONACO_LANGUAGE: Record<CodeLanguage, string> = {
  python: "python",
  javascript: "javascript",
  typescript: "typescript",
  java: "java",
  go: "go",
  cpp: "cpp",
  sql: "sql",
};

const COMMENT: Record<CodeLanguage, string> = {
  python: "#",
  javascript: "//",
  typescript: "//",
  java: "//",
  go: "//",
  cpp: "//",
  sql: "--",
};

export function fallbackStarter(language: CodeLanguage): string {
  return `${COMMENT[language]} Write your solution here\n`;
}

export interface BufferState {
  codingSessionId: string;
  language: CodeLanguage;
  buffers: Partial<Record<CodeLanguage, string>>;
  /** Monotonic editor version (code.update). */
  version: number;
}

export function availableLanguages(question: Pick<CodingQuestionPublic, "languages">): CodeLanguage[] {
  const list = (question.languages ?? []).filter((l): l is CodeLanguage => SUPPORTED_LANGUAGES.includes(l));
  return list.length ? list : ["python"];
}

export function starterFor(question: Pick<CodingQuestionPublic, "starter_code">, language: CodeLanguage): string {
  const starters = question.starter_code ?? {};
  const value = starters[language] ?? (language === "cpp" ? starters["c++"] : undefined);
  return typeof value === "string" && value.length ? value : fallbackStarter(language);
}

export interface SavedBuffers {
  language: CodeLanguage;
  buffers: Partial<Record<CodeLanguage, string>>;
  version: number;
}

export function initialBuffers(
  codingSessionId: string,
  question: Pick<CodingQuestionPublic, "languages" | "starter_code">,
  language: CodeLanguage,
  serverCode?: string | null,
  saved?: SavedBuffers | null,
): BufferState {
  const languages = availableLanguages(question);
  const start = languages.includes(language) ? language : languages[0]!;
  const buffers: Partial<Record<CodeLanguage, string>> = {};
  for (const lang of languages) buffers[lang] = starterFor(question, lang);
  if (serverCode) buffers[start] = serverCode;
  let current = start;
  let version = 0;
  if (saved) {
    for (const [lang, code] of Object.entries(saved.buffers) as Array<[CodeLanguage, string]>) {
      if (languages.includes(lang) && typeof code === "string") buffers[lang] = code;
    }
    if (languages.includes(saved.language)) current = saved.language;
    version = saved.version;
  }
  return { codingSessionId, language: current, buffers, version };
}

export function editBuffer(state: BufferState, code: string): BufferState {
  if (state.buffers[state.language] === code) return state;
  return { ...state, buffers: { ...state.buffers, [state.language]: code }, version: state.version + 1 };
}

export function switchLanguage(state: BufferState, question: Pick<CodingQuestionPublic, "starter_code">, next: CodeLanguage): BufferState {
  if (next === state.language) return state;
  const buffers = { ...state.buffers };
  if (buffers[next] === undefined) buffers[next] = starterFor(question, next);
  return { ...state, language: next, buffers, version: state.version + 1 };
}

export function currentCode(state: BufferState): string {
  return state.buffers[state.language] ?? "";
}

export function isPristine(state: BufferState, question: Pick<CodingQuestionPublic, "starter_code">, language: CodeLanguage = state.language): boolean {
  return (state.buffers[language] ?? "") === starterFor(question, language);
}

const STORAGE_PREFIX = "coding:buffers:";

export function saveBuffers(state: BufferState, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(STORAGE_PREFIX + state.codingSessionId, JSON.stringify({ language: state.language, buffers: state.buffers, version: state.version } satisfies SavedBuffers));
  } catch {
    /* quota or privacy mode: the server still has the debounced snapshots */
  }
}

export function loadBuffers(codingSessionId: string, storage: Pick<Storage, "getItem"> | null = safeStorage()): SavedBuffers | null {
  try {
    const raw = storage?.getItem(STORAGE_PREFIX + codingSessionId);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SavedBuffers;
    return parsed && typeof parsed === "object" && parsed.buffers ? parsed : null;
  } catch {
    return null;
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Paste policy inside the editor. */
export function decidePaste(chars: number, options: { threshold: number; block: boolean }): { allow: boolean; large: boolean } {
  return { allow: !options.block, large: chars >= options.threshold };
}
