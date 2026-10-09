/**
 * Candidate coding workspace theme on top of the shared, trimmed Monaco instance
 * (`@/lib/monaco`: self-hosted editor, seven interview languages, editor + TS workers).
 *
 * Imported lazily by the coding workspace, so the editor chunk only loads when a
 * coding exercise starts.
 */
import { monaco } from "@/lib/monaco";

monaco.editor.defineTheme("interview-stage", {
  base: "vs-dark",
  inherit: true,
  rules: [
    { token: "comment", foreground: "6b7896", fontStyle: "italic" },
    { token: "keyword", foreground: "a9c2fd" },
    { token: "string", foreground: "86efac" },
    { token: "number", foreground: "fbbf24" },
    { token: "type", foreground: "67e8f9" },
  ],
  colors: {
    "editor.background": "#0b0f1e",
    "editor.foreground": "#e8ecf6",
    "editorLineNumber.foreground": "#4b5675",
    "editorLineNumber.activeForeground": "#9aa6c2",
    "editor.lineHighlightBackground": "#141a30",
    "editor.lineHighlightBorder": "#00000000",
    "editor.selectionBackground": "#2c3f7a",
    "editor.inactiveSelectionBackground": "#1f2a4d",
    "editorCursor.foreground": "#93b4fd",
    "editorIndentGuide.background1": "#1d2540",
    "editorIndentGuide.activeBackground1": "#3d4a6e",
    "editorWidget.background": "#141a30",
    "editorWidget.border": "#2c3757",
    "editorSuggestWidget.background": "#141a30",
    "editorSuggestWidget.border": "#2c3757",
    "editorSuggestWidget.selectedBackground": "#1f2a4d",
    "scrollbarSlider.background": "#94a3b822",
    "scrollbarSlider.hoverBackground": "#94a3b844",
  },
});

monaco.languages.typescript.typescriptDefaults.setCompilerOptions({
  target: monaco.languages.typescript.ScriptTarget.ES2020,
  allowNonTsExtensions: true,
  strict: false,
  noEmit: true,
});
monaco.languages.typescript.javascriptDefaults.setDiagnosticsOptions({ noSemanticValidation: true, noSyntaxValidation: false });

export { monaco };
