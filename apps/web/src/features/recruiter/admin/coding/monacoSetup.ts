/**
 * Staff code editor theme on top of the shared, trimmed Monaco instance
 * (`@/lib/monaco`). Loaded lazily with the code editor, so the Monaco chunk
 * never reaches pages that don't edit code.
 */
import { monaco } from "@/lib/monaco";

monaco.editor.defineTheme("interview-light", {
  base: "vs",
  inherit: true,
  rules: [
    { token: "comment", foreground: "7a89a8", fontStyle: "italic" },
    { token: "keyword", foreground: "1f37d7" },
    { token: "string", foreground: "15803d" },
    { token: "number", foreground: "b45309" },
    { token: "type", foreground: "0e7490" },
  ],
  colors: {
    "editor.background": "#fbfcfe",
    "editor.foreground": "#121830",
    "editorLineNumber.foreground": "#aab6cf",
    "editorLineNumber.activeForeground": "#55648a",
    "editor.lineHighlightBackground": "#f2f5fa",
    "editor.lineHighlightBorder": "#00000000",
    "editor.selectionBackground": "#dbe6fe",
    "editorIndentGuide.background1": "#e9edf5",
    "editorIndentGuide.activeBackground1": "#d3dae8",
    "scrollbarSlider.background": "#55648a22",
    "scrollbarSlider.hoverBackground": "#55648a44",
  },
});

// Dark theme for the staff app (follows the app theme; values mirror the dark tokens:
// field well #0c1122, text #e8ecf6, subtle #8090ad, accent-fg #a9c2fd).
monaco.editor.defineTheme("interview-dark", {
  base: "vs-dark",
  inherit: true,
  rules: [
    { token: "comment", foreground: "8090ad", fontStyle: "italic" },
    { token: "keyword", foreground: "a9c2fd" },
    { token: "string", foreground: "86efac" },
    { token: "number", foreground: "fbbf24" },
    { token: "type", foreground: "67e8f9" },
  ],
  colors: {
    "editor.background": "#0c1122",
    "editor.foreground": "#e8ecf6",
    "editorLineNumber.foreground": "#56627f",
    "editorLineNumber.activeForeground": "#9aa6c2",
    "editor.lineHighlightBackground": "#131a30",
    "editor.lineHighlightBorder": "#00000000",
    "editor.selectionBackground": "#2c3f7a",
    "editor.inactiveSelectionBackground": "#1f2a4d",
    "editorCursor.foreground": "#93b4fd",
    "editorIndentGuide.background1": "#1d2540",
    "editorIndentGuide.activeBackground1": "#3d4a6e",
    "editorWidget.background": "#182039",
    "editorWidget.border": "#2c3757",
    "editorSuggestWidget.background": "#182039",
    "editorSuggestWidget.border": "#2c3757",
    "editorSuggestWidget.selectedBackground": "#1f2a4d",
    "scrollbarSlider.background": "#94a3b822",
    "scrollbarSlider.hoverBackground": "#94a3b844",
  },
});

/** Monaco theme name for the app theme in use. */
export function staffMonacoTheme(resolved: "light" | "dark"): "interview-light" | "interview-dark" {
  return resolved === "dark" ? "interview-dark" : "interview-light";
}

export { monaco };
