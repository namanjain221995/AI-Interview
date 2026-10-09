/**
 * The scoring service writes the recommendation rationale for machines first
 * ("confidence 0.17 (minimum 0.60) ... lacking evidence: aws, ci_cd"). Make it
 * read like the rest of the review screen: competency names instead of keys and
 * shares as percentages. Text that doesn't follow the pattern passes through.
 */
export function readableRationale(text: string, nameOf: (key: string) => string): string {
  return text
    .replace(/(lacking evidence|without evidence|not covered|missing evidence):\s*([a-z0-9_, ]+?)(\.|$)/gi, (_m, lead: string, list: string, end: string) => {
      const names = list
        .split(",")
        .map((token) => token.trim())
        .filter(Boolean)
        .map((token) => (/^[a-z0-9_]+$/.test(token) ? nameOf(token) : token));
      return `${lead}: ${names.join(", ")}${end}`;
    })
    .replace(/\b(confidence|coverage)\s+(0?\.\d+|1(?:\.0+)?)\b/gi, (_m, word: string, value: string) => `${word} ${Math.round(Number(value) * 100)}%`)
    .replace(/\(minimum\s+(0?\.\d+|1(?:\.0+)?)\)/gi, (_m, value: string) => `(minimum ${Math.round(Number(value) * 100)}%)`);
}
