import Editor from "@monaco-editor/react";

import { staffMonacoTheme } from "./monacoSetup";

import { Spinner } from "@/components/ui/Spinner";
import { useResolvedTheme } from "@/lib/theme";

export interface MonacoCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language: string;
  height: number | string;
  readOnly?: boolean;
  ariaLabel: string;
}

const MONACO_LANGUAGE: Record<string, string> = { cpp: "cpp", go: "go", java: "java", javascript: "javascript", typescript: "typescript", python: "python", sql: "sql" };

/** Monaco instance (lazy chunk). */
export default function MonacoCodeEditor({ value, onChange, language, height, readOnly, ariaLabel }: MonacoCodeEditorProps) {
  // Follows the app theme (light / dark / system); Monaco themes are global per page.
  const theme = staffMonacoTheme(useResolvedTheme());
  return (
    <Editor
      value={value}
      onChange={(next) => onChange(next ?? "")}
      language={MONACO_LANGUAGE[language] ?? "plaintext"}
      height={height}
      theme={theme}
      loading={<Spinner label="Loading editor" />}
      options={{
        readOnly,
        ariaLabel,
        fontFamily: "'JetBrains Mono Variable', ui-monospace, monospace",
        fontSize: 13,
        lineHeight: 21,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        tabSize: language === "go" ? 4 : language === "python" || language === "java" || language === "cpp" ? 4 : 2,
        insertSpaces: language !== "go",
        renderLineHighlight: "line",
        automaticLayout: true,
        padding: { top: 10, bottom: 10 },
        wordWrap: "off",
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
        fixedOverflowWidgets: true,
      }}
    />
  );
}
