// Vite dev server of the mock-AI end-to-end stack: the web app's own config on E2E_WEB_PORT (default 5280),
// proxying /api and /ws to the stack's API on E2E_API_PORT (default 8480), with its own dependency cache so it
// never disturbs another Vite server that shares apps/web/node_modules/.vite.
import path from "node:path";

import base from "../../../apps/web/vite.config";

const webRoot = path.resolve(__dirname, "../../../apps/web");
const apiPort = Number(process.env.E2E_API_PORT ?? 8480);
const webPort = Number(process.env.E2E_WEB_PORT ?? 5280);

export default {
  ...base,
  root: webRoot,
  cacheDir: process.env.E2E_VITE_CACHE_DIR ?? path.join(webRoot, "node_modules/.vite-e2e"),
  server: {
    ...base.server,
    host: "127.0.0.1",
    port: webPort,
    strictPort: true,
    proxy: {
      "/api": { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false },
      "/ws": { target: `ws://127.0.0.1:${apiPort}`, ws: true },
    },
  },
};
