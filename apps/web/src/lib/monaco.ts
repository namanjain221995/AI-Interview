/**
 * The app's single Monaco instance, self-hosted (the CSP allows only 'self') and
 * trimmed to what an interview code editor needs:
 *   - the editor API with the editing features people expect (find/replace, folding,
 *     suggestions, hovers, signature help, comments, multi-cursor, line operations,
 *     bracket matching, formatting, the Tab-focus toggle for keyboard users),
 *   - tokenizers for the seven interview languages,
 *   - the TypeScript/JavaScript language service.
 * Left out on purpose: CSS/HTML/JSON language services, Monaco's other ~75 tokenizers,
 * AI-style inline completions, code-action lightbulbs, go-to-definition/references,
 * rename, inlay hints, colour picker, sticky scroll, links, drag-and-drop and the
 * command palette extras. Workers: the editor worker, plus the TypeScript worker
 * (fetched only when a JS/TS model opens).
 *
 * Import this module lazily (the candidate coding workspace and the staff code editors
 * do), so the editor chunk never reaches pages that don't edit code.
 *
 * vite.config.ts reads the `monaco-editor/...` imports below and pre-bundles exactly
 * those entries for the dev server, so opening an editor never triggers a dependency
 * re-optimisation (which reloads the page, mid-interview). Keep one import per line.
 */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
// Editor contributions (a curated subset of editor.all.js).
import "monaco-editor/esm/vs/editor/browser/coreCommands.js";
import "monaco-editor/esm/vs/editor/browser/widget/codeEditor/codeEditorWidget.js";
import "monaco-editor/esm/vs/editor/contrib/bracketMatching/browser/bracketMatching.js";
import "monaco-editor/esm/vs/editor/contrib/caretOperations/browser/caretOperations.js";
import "monaco-editor/esm/vs/editor/contrib/caretOperations/browser/transpose.js";
import "monaco-editor/esm/vs/editor/contrib/clipboard/browser/clipboard.js";
import "monaco-editor/esm/vs/editor/contrib/comment/browser/comment.js";
import "monaco-editor/esm/vs/editor/contrib/contextmenu/browser/contextmenu.js";
import "monaco-editor/esm/vs/editor/contrib/cursorUndo/browser/cursorUndo.js";
import "monaco-editor/esm/vs/editor/contrib/find/browser/findController.js";
import "monaco-editor/esm/vs/editor/contrib/folding/browser/folding.js";
import "monaco-editor/esm/vs/editor/contrib/fontZoom/browser/fontZoom.js";
import "monaco-editor/esm/vs/editor/contrib/format/browser/formatActions.js";
import "monaco-editor/esm/vs/editor/contrib/gotoError/browser/gotoError.js";
import "monaco-editor/esm/vs/editor/contrib/hover/browser/hoverContribution.js";
import "monaco-editor/esm/vs/editor/contrib/indentation/browser/indentation.js";
import "monaco-editor/esm/vs/editor/contrib/lineSelection/browser/lineSelection.js";
import "monaco-editor/esm/vs/editor/contrib/linesOperations/browser/linesOperations.js";
import "monaco-editor/esm/vs/editor/contrib/multicursor/browser/multicursor.js";
import "monaco-editor/esm/vs/editor/contrib/parameterHints/browser/parameterHints.js";
import "monaco-editor/esm/vs/editor/contrib/placeholderText/browser/placeholderText.contribution.js";
import "monaco-editor/esm/vs/editor/contrib/readOnlyMessage/browser/contribution.js";
import "monaco-editor/esm/vs/editor/contrib/smartSelect/browser/smartSelect.js";
import "monaco-editor/esm/vs/editor/contrib/snippet/browser/snippetController2.js";
import "monaco-editor/esm/vs/editor/contrib/suggest/browser/suggestController.js";
import "monaco-editor/esm/vs/editor/contrib/tokenization/browser/tokenization.js";
import "monaco-editor/esm/vs/editor/contrib/toggleTabFocusMode/browser/toggleTabFocusMode.js";
import "monaco-editor/esm/vs/editor/contrib/wordHighlighter/browser/wordHighlighter.js";
import "monaco-editor/esm/vs/editor/contrib/wordOperations/browser/wordOperations.js";
import "monaco-editor/esm/vs/editor/contrib/wordPartOperations/browser/wordPartOperations.js";
import "monaco-editor/esm/vs/editor/common/standaloneStrings.js";
import "monaco-editor/esm/vs/base/browser/ui/codicons/codiconStyles.js";
// Interview languages.
import "monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution.js";
import "monaco-editor/esm/vs/basic-languages/go/go.contribution.js";
import "monaco-editor/esm/vs/basic-languages/java/java.contribution.js";
import "monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import "monaco-editor/esm/vs/basic-languages/sql/sql.contribution.js";
import "monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution.js";
import "monaco-editor/esm/vs/language/typescript/monaco.contribution.js";
// Workers (bundled by Vite, same origin).
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

/** Monaco language ids registered above (interview `CodeLanguage` values map 1:1). */
export const MONACO_LANGUAGES = ["cpp", "go", "java", "javascript", "python", "sql", "typescript"] as const;

self.MonacoEnvironment ??= {
  getWorker(_workerId: string, label: string) {
    if (label === "typescript" || label === "javascript") return new TsWorker();
    return new EditorWorker();
  },
};

// @monaco-editor/react uses this instance instead of fetching Monaco from a CDN.
loader.config({ monaco });

export { monaco };
