/**
 * Keyboard shortcuts recorded as `prohibited_shortcut` observations.
 *
 * Honest limits: the browser never sees operating-system shortcuts such as
 * Alt+Tab, Cmd+Tab, the Windows key or Mission Control, so switching apps is
 * only visible indirectly (focus/visibility loss). PrintScreen is reported by
 * some platforms only on key-up and can never be blocked. Developer-tool
 * shortcuts can be observed but not reliably prevented.
 */

export interface KeyLike {
  key: string;
  code?: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

export type ShortcutKind = "print_screen" | "devtools" | "print" | "save" | "view_source";

export interface ShortcutMatch {
  kind: ShortcutKind;
  /** Human-readable combination, e.g. "Ctrl+Shift+I". */
  combo: string;
  /** Whether the default action can and should be prevented. */
  preventable: boolean;
}

function comboLabel(e: KeyLike, key: string): string {
  const parts: string[] = [];
  if (e.ctrlKey) parts.push("Ctrl");
  if (e.metaKey) parts.push("Cmd");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");
  parts.push(key);
  return parts.join("+");
}

export function classifyShortcut(e: KeyLike): ShortcutMatch | null {
  const key = e.key.length === 1 ? e.key.toUpperCase() : e.key;
  if (key === "PrintScreen" || e.code === "PrintScreen") return { kind: "print_screen", combo: "PrintScreen", preventable: false };
  if (key === "F12") return { kind: "devtools", combo: "F12", preventable: false };
  const mod = e.ctrlKey || e.metaKey;
  if (!mod) return null;
  const letter = e.code?.startsWith("Key") ? e.code.slice(3) : key;
  // Ctrl/Cmd+Shift+I/J/C (Windows/Linux devtools) and Cmd+Option+I/J/C (macOS devtools).
  if ((e.shiftKey || (e.metaKey && e.altKey)) && (letter === "I" || letter === "J" || letter === "C")) {
    return { kind: "devtools", combo: comboLabel(e, letter), preventable: false };
  }
  if (e.shiftKey || e.altKey) return null;
  if (letter === "P") return { kind: "print", combo: comboLabel(e, "P"), preventable: true };
  if (letter === "S") return { kind: "save", combo: comboLabel(e, "S"), preventable: true };
  if (letter === "U") return { kind: "view_source", combo: comboLabel(e, "U"), preventable: true };
  return null;
}
