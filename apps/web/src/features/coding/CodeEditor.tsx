import "./monacoSetup";

import Editor, { type OnMount } from "@monaco-editor/react";
import { useRef } from "react";

import type { CodeLanguage } from "@/lib/realtime/protocol";

import { MONACO_LANGUAGE } from "./buffers";

export interface CodeEditorProps {
  /** One Monaco model per path keeps undo history per language. */
  path: string;
  language: CodeLanguage;
  defaultValue: string;
  readOnly?: boolean;
  onChange: (code: string) => void;
  onCursorLine?: (line: number) => void;
  onReady?: () => void;
}

const TAB_SIZE: Partial<Record<CodeLanguage, number>> = { python: 4, java: 4, cpp: 4, go: 4 };

export default function CodeEditor({ path, language, defaultValue, readOnly, onChange, onCursorLine, onReady }: CodeEditorProps) {
  const cursorCb = useRef(onCursorLine);
  cursorCb.current = onCursorLine;

  const onMount: OnMount = (editor) => {
    editor.onDidChangeCursorPosition((e) => cursorCb.current?.(e.position.lineNumber));
    editor.focus();
    onReady?.();
  };

  return (
    <Editor
      path={path}
      language={MONACO_LANGUAGE[language]}
      defaultValue={defaultValue}
      theme="interview-stage"
      onChange={(value) => onChange(value ?? "")}
      onMount={onMount}
      loading={<div className="flex size-full items-center justify-center text-sm text-fg-muted">Loading the editor…</div>}
      options={{
        readOnly,
        fontFamily: '"JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 14,
        lineHeight: 22,
        fontLigatures: false,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        contextmenu: false,
        dragAndDrop: false,
        padding: { top: 14, bottom: 14 },
        tabSize: TAB_SIZE[language] ?? 2,
        insertSpaces: true,
        renderLineHighlight: "line",
        smoothScrolling: true,
        cursorBlinking: "smooth",
        bracketPairColorization: { enabled: true },
        guides: { indentation: true, bracketPairs: false },
        overviewRulerLanes: 0,
        hideCursorInOverviewRuler: true,
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
        accessibilitySupport: "auto",
        ariaLabel: "Code editor",
      }}
    />
  );
}
