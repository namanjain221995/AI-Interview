/**
 * Line diff for the coding snapshot viewer: longest common subsequence over
 * lines after trimming the shared prefix and suffix. Interview solutions are
 * small; inputs whose middle section would need more than MAX_CELLS table
 * cells degrade to "replace the middle", which is still a correct diff.
 */

export type DiffLine =
  | { kind: "same"; text: string; oldNumber: number; newNumber: number }
  | { kind: "add"; text: string; oldNumber: null; newNumber: number }
  | { kind: "del"; text: string; oldNumber: number; newNumber: null };

export type DiffDisplayLine = DiffLine | { kind: "gap"; count: number; oldStart: number; newStart: number };

const MAX_CELLS = 4_000_000;

export function splitLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  // A trailing newline does not create an extra empty line.
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

export function diffLines(before: string, after: string): DiffLine[] {
  const a = splitLines(before);
  const b = splitLines(after);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }

  const out: DiffLine[] = [];
  for (let i = 0; i < start; i += 1) out.push({ kind: "same", text: a[i]!, oldNumber: i + 1, newNumber: i + 1 });

  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const n = midA.length;
  const m = midB.length;

  if (n > 0 || m > 0) {
    if (n === 0 || m === 0 || (n + 1) * (m + 1) > MAX_CELLS) {
      midA.forEach((text, i) => out.push({ kind: "del", text, oldNumber: start + i + 1, newNumber: null }));
      midB.forEach((text, j) => out.push({ kind: "add", text, oldNumber: null, newNumber: start + j + 1 }));
    } else {
      // lcs[i][j] = LCS length of midA[i..] and midB[j..], stored row-major.
      const width = m + 1;
      const lcs = new Uint32Array((n + 1) * width);
      for (let i = n - 1; i >= 0; i -= 1) {
        for (let j = m - 1; j >= 0; j -= 1) {
          lcs[i * width + j] = midA[i] === midB[j] ? lcs[(i + 1) * width + j + 1]! + 1 : Math.max(lcs[(i + 1) * width + j]!, lcs[i * width + j + 1]!);
        }
      }
      let i = 0;
      let j = 0;
      while (i < n || j < m) {
        if (i < n && j < m && midA[i] === midB[j]) {
          out.push({ kind: "same", text: midA[i]!, oldNumber: start + i + 1, newNumber: start + j + 1 });
          i += 1;
          j += 1;
        } else if (j < m && (i === n || lcs[i * width + j + 1]! >= lcs[(i + 1) * width + j]!)) {
          out.push({ kind: "add", text: midB[j]!, oldNumber: null, newNumber: start + j + 1 });
          j += 1;
        } else {
          out.push({ kind: "del", text: midA[i]!, oldNumber: start + i + 1, newNumber: null });
          i += 1;
        }
      }
    }
  }

  const offsetA = endA;
  const offsetB = endB;
  for (let k = 0; k < a.length - endA; k += 1) {
    out.push({ kind: "same", text: a[offsetA + k]!, oldNumber: offsetA + k + 1, newNumber: offsetB + k + 1 });
  }
  return out;
}

export function diffStats(lines: readonly DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.kind === "add") added += 1;
    else if (line.kind === "del") removed += 1;
  }
  return { added, removed };
}

/** Collapses long unchanged runs, keeping `context` lines around each change. */
export function collapseUnchanged(lines: readonly DiffLine[], context = 3): DiffDisplayLine[] {
  const changed = lines.map((line) => line.kind !== "same");
  if (!changed.some(Boolean)) return [...lines];
  const keep = lines.map((_, index) => {
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k += 1) if (changed[k]) return true;
    return false;
  });
  const out: DiffDisplayLine[] = [];
  let index = 0;
  while (index < lines.length) {
    if (keep[index]) {
      out.push(lines[index]!);
      index += 1;
      continue;
    }
    const start = index;
    while (index < lines.length && !keep[index]) index += 1;
    const first = lines[start]!;
    out.push({ kind: "gap", count: index - start, oldStart: first.oldNumber ?? 0, newStart: first.newNumber ?? 0 });
  }
  return out;
}
