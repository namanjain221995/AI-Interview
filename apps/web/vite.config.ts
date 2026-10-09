import { readFileSync } from "node:fs";
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Long-lived vendor chunks. React and friends are pinned to "vendor" first so
// heavy feature chunks (three, monaco, charts) never absorb them, which would
// force those chunks onto the initial page load.
const CHUNK_GROUPS: Array<[string, RegExp]> = [
  // Shared by the app shell AND feature libraries (recharts pulls in clsx, react-is...);
  // pinning them here keeps them out of the feature chunks.
  ["vendor", /[\\/]node_modules[\\/](react|react-dom|react-is|scheduler|react-router|@tanstack|zustand|use-sync-external-store|clsx|tailwind-merge|class-variance-authority|tiny-invariant)[\\/]/],
  ["three", /[\\/]node_modules[\\/](three|@react-three|three-stdlib|troika-[^\\/]+|camera-controls|maath|meshline|stats-gl|its-fine|suspend-react|tunnel-rat)[\\/]/],
  ["monaco", /[\\/]node_modules[\\/](monaco-editor|@monaco-editor)[\\/]/],
  ["charts", /[\\/]node_modules[\\/](recharts|d3-[^\\/]+|victory-vendor|internmap|decimal\.js-light|@reduxjs|redux|redux-thunk|reselect|immer|es-toolkit|eventemitter3)[\\/]/],
];

// The trimmed Monaco build lives in src/lib/monaco.ts. The dev server must pre-bundle
// every `monaco-editor/...` entry it imports: a deep import the optimizer has not seen
// triggers a re-optimisation and a full page reload, in the middle of an interview.
// Reading them from that file keeps this list in sync by construction.
function monacoEntries(): string[] {
  const source = readFileSync(path.resolve(__dirname, "src/lib/monaco.ts"), "utf8");
  const entries = [...source.matchAll(/^import\s+(?:[^"';]+\s+from\s+)?["'](monaco-editor\/[^"'?]+)["'];?\s*$/gm)].map((m) => m[1]!);
  if (entries.length < 3) throw new Error("vite.config.ts: no monaco-editor imports found in src/lib/monaco.ts");
  return entries;
}

// Dev server proxies the API + WebSocket to the FastAPI app so the browser
// sees a single origin (same as production behind the Caddy edge).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8200", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8200", ws: true },
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
  },
  build: {
    target: "es2022",
    sourcemap: true,
    // Just above the lazily loaded Monaco chunk (~3.04 MB): growth or a new large chunk still warns.
    chunkSizeWarningLimit: 3100,
    rollupOptions: {
      onwarn(warning, defaultHandler) {
        // zod ships comments Rollup cannot place; they are harmless and only add noise.
        if (warning.code === "INVALID_ANNOTATION" && warning.id?.includes("node_modules")) return;
        defaultHandler(warning);
      },
      output: {
        manualChunks(id) {
          // Vite's preload helpers must live in the shared vendor chunk; otherwise they land
          // in whichever lazy chunk first needs them (monaco) and every page preloads it.
          if (id.includes("vite/preload-helper") || id.includes("commonjsHelpers") || id.includes("vite/modulepreload-polyfill")) {
            return "vendor";
          }
          if (!id.includes("node_modules")) return undefined;
          for (const [name, pattern] of CHUNK_GROUPS) {
            if (pattern.test(id)) return name;
          }
          return undefined;
        },
      },
    },
  },
  // Pre-bundle libraries that only lazy routes import, so the first visit to
  // those routes does not trigger a dev-server re-optimisation and reload.
  optimizeDeps: {
    include: [
      "recharts",
      "react-markdown",
      "remark-gfm",
      "react-hook-form",
      "@hookform/resolvers/zod",
      "zod",
      "date-fns",
      "three",
      "@react-three/fiber",
      "@react-three/drei",
      "@pixiv/three-vrm",
      "@monaco-editor/react",
      // Exactly the trimmed Monaco entries imported by src/lib/monaco.ts.
      ...monacoEntries(),
    ],
  },
  worker: { format: "es" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
    // The first test of a page suite pays for transforming its lazy route chunk; on a
    // loaded machine (parallel workers + other jobs) that alone can pass the 5 s default.
    testTimeout: 15_000,
  },
});
