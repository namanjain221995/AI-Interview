/**
 * Batch intake rows: paste or CSV (`name,email[,target_role]`), parsed and checked in
 * the browser before anything is sent (email format, duplicates, at most 200 rows),
 * and the shown-once links exported back to CSV without leaving the page.
 */

import { TARGET_ROLE_LABELS } from "../labels";
import { TARGET_ROLES, type IntakeBatchCreated, type IntakeBatchFailed, type IntakeBatchRowIn, type TargetRole } from "../types";

export const BATCH_MAX_ROWS = 200;
export const EMAIL_RX = /^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/;

export interface BatchRow {
  /** 1-based row number in the pasted text (header excluded). */
  row: number;
  full_name: string;
  email: string;
  target_role: TargetRole | null;
  /** Raw role text when it did not match a known role. */
  role_text: string | null;
  error: string | null;
}

export interface BatchParse {
  rows: BatchRow[];
  valid: BatchRow[];
  invalid: BatchRow[];
  /** Problems with the whole input (too many rows, nothing found). */
  problems: string[];
  header: boolean;
}

/** Splits one CSV/TSV line, honouring double quotes ("Doe, Jane"). */
export function splitLine(line: string, delimiter: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!;
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell.trim() === "") {
      quoted = true;
      cell = "";
    } else if (ch === delimiter) {
      cells.push(cell.trim());
      cell = "";
    } else cell += ch;
  }
  cells.push(cell.trim());
  return cells;
}

function detectDelimiter(lines: readonly string[]): string {
  const sample = lines.slice(0, 5).join("\n");
  if (sample.includes("\t")) return "\t";
  const commas = (sample.match(/,/g) ?? []).length;
  const semicolons = (sample.match(/;/g) ?? []).length;
  return semicolons > commas ? ";" : ",";
}

const ROLE_ALIASES: Record<string, TargetRole> = {
  "ai/ml": "ai_ml",
  ai: "ai_ml",
  ml: "ai_ml",
  "ai ml": "ai_ml",
  "ai/ml engineer": "ai_ml",
  "data analyst": "data_analyst",
  analyst: "data_analyst",
  "technical pm": "product_manager_technical",
  "technical product manager": "product_manager_technical",
  "tech pm": "product_manager_technical",
  "growth pm": "product_manager_growth",
  "growth product manager": "product_manager_growth",
  other: "other",
};

/** Role key from a key, a label or a common alias; null when unknown or empty. */
export function parseRole(text: string): TargetRole | null {
  const value = text.trim().toLowerCase();
  if (!value) return null;
  if ((TARGET_ROLES as readonly string[]).includes(value)) return value as TargetRole;
  for (const role of TARGET_ROLES) if (TARGET_ROLE_LABELS[role].toLowerCase() === value) return role;
  return ROLE_ALIASES[value.replace(/\s+/g, " ")] ?? null;
}

function looksLikeHeader(cells: readonly string[]): boolean {
  const joined = cells.join(" ").toLowerCase();
  return /e-?mail/.test(joined) && /name/.test(joined) && !cells.some((c) => EMAIL_RX.test(c));
}

/**
 * Parses pasted rows. Accepts `name, email[, role]` in either column order (the cell
 * that looks like an email is the email), comma, semicolon or tab separated, with an
 * optional header row.
 */
export function parseBatch(input: string): BatchParse {
  const lines = input.replace(/\r\n?/g, "\n").split("\n").filter((l) => l.trim() !== "");
  const problems: string[] = [];
  if (lines.length === 0) return { rows: [], valid: [], invalid: [], problems, header: false };
  const delimiter = detectDelimiter(lines);
  let cellsList = lines.map((line) => splitLine(line, delimiter));
  const header = looksLikeHeader(cellsList[0]!);
  let roleColumn: number | null = null;
  let nameColumn: number | null = null;
  let emailColumn: number | null = null;
  if (header) {
    const head = cellsList[0]!.map((c) => c.toLowerCase());
    emailColumn = head.findIndex((c) => /e-?mail/.test(c));
    nameColumn = head.findIndex((c) => /name/.test(c));
    const role = head.findIndex((c) => /role|track|target/.test(c));
    roleColumn = role === -1 ? null : role;
    cellsList = cellsList.slice(1);
  }

  const seen = new Map<string, number>();
  const rows: BatchRow[] = cellsList.map((cells, index) => {
    const row = index + 1;
    const found = cells.findIndex((c) => EMAIL_RX.test(c));
    // No valid email anywhere: read the usual column (second, or one with an @) so the row says what's wrong.
    const fallback = found === -1 ? (cells.findIndex((c) => c.includes("@")) !== -1 ? cells.findIndex((c) => c.includes("@")) : cells.length >= 2 ? 1 : -1) : found;
    const emailIndex = emailColumn !== null && emailColumn >= 0 ? emailColumn : fallback;
    const email = (emailIndex >= 0 ? cells[emailIndex] ?? "" : "").trim().toLowerCase();
    const rest = cells.filter((_, i) => i !== emailIndex);
    const name = (nameColumn !== null && nameColumn >= 0 ? cells[nameColumn] ?? "" : rest[0] ?? "").replace(/\s+/g, " ").trim();
    const roleText = (roleColumn !== null ? cells[roleColumn] ?? "" : nameColumn !== null && nameColumn >= 0 ? "" : rest[1] ?? "").trim();
    const role = parseRole(roleText);

    let error: string | null = null;
    if (!name) error = "Add the candidate's name.";
    else if (name.length < 2 || name.length > 200) error = "Names are 2 to 200 characters.";
    else if (!email) error = "Add an email address.";
    else if (!EMAIL_RX.test(email)) error = "This email address doesn't look right.";
    else if (seen.has(email)) error = `Same email as row ${seen.get(email)}.`;
    else if (roleText && !role) error = `Unknown role “${roleText}”. Use AI/ML, Data analyst, Technical PM, Growth PM or Other, or leave it empty.`;
    if (email && !seen.has(email)) seen.set(email, row);
    return { row, full_name: name, email, target_role: role, role_text: roleText && !role ? roleText : null, error };
  });

  if (rows.length > BATCH_MAX_ROWS) problems.push(`A batch holds up to ${BATCH_MAX_ROWS} rows; this one has ${rows.length}. Split it into smaller batches.`);
  const valid = rows.filter((r) => r.error === null);
  const invalid = rows.filter((r) => r.error !== null);
  return { rows, valid, invalid, problems, header };
}

/** Payload rows for POST /intake/interviews/batch, in input order. */
export function batchPayload(rows: readonly BatchRow[]): IntakeBatchRowIn[] {
  return rows.map((r) => ({ full_name: r.full_name, email: r.email, target_role: r.target_role }));
}

function csvCell(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? "" : String(value);
  // Quote anything with separators/quotes, and neutralise spreadsheet formulas.
  const safe = /^[=+\-@\t]/.test(text) ? `'${text}` : text;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export interface BatchResultRow {
  row: number;
  full_name: string;
  email: string;
  status: "created" | "failed";
  invite_url: string | null;
  access_code: string | null;
  expires_at: string | null;
  message: string | null;
}

/** Results in input order, joining what was sent with what the server answered. */
export function mergeBatchResults(sent: readonly IntakeBatchRowIn[], created: readonly IntakeBatchCreated[], failed: readonly IntakeBatchFailed[]): BatchResultRow[] {
  // The server numbers rows by their position in the request (0- or 1-based); match on email first.
  const createdByEmail = new Map(created.map((c) => [c.email.toLowerCase(), c]));
  const failedByEmail = new Map(failed.map((f) => [f.email.toLowerCase(), f]));
  return sent.map((row, index) => {
    const email = row.email.toLowerCase();
    const ok = createdByEmail.get(email);
    if (ok) return { row: index + 1, full_name: row.full_name, email: row.email, status: "created", invite_url: ok.invite_url, access_code: ok.access_code, expires_at: ok.expires_at, message: ok.candidate_reused ? "Existing candidate" : null };
    const bad = failedByEmail.get(email);
    return { row: index + 1, full_name: row.full_name, email: row.email, status: "failed", invite_url: null, access_code: null, expires_at: null, message: bad ? bad.message || bad.code : "Not created" };
  });
}

/** CSV of the batch results (links are shown once: this is the copy to keep). */
export function linksCsv(rows: readonly BatchResultRow[]): string {
  const header = ["row", "name", "email", "status", "invite_link", "access_code", "expires_at", "note"];
  const lines = rows.map((r) => [r.row, r.full_name, r.email, r.status, r.invite_url, r.access_code, r.expires_at, r.message].map(csvCell).join(","));
  return [header.join(","), ...lines].join("\r\n");
}

/** Saves text as a file in the browser (no server round trip). */
export function downloadText(filename: string, text: string, type = "text/csv;charset=utf-8"): void {
  const blob = new Blob([`﻿${text}`], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
