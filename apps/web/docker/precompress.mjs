#!/usr/bin/env node
// Writes .br / .zst / .gz siblings next to compressible build outputs so the
// edge (Caddy `file_server { precompressed br zstd gzip }`) serves static
// files without compressing them on every request.
//
//   node docker/precompress.mjs dist
//
// A variant is kept only when it saves at least MIN_SAVING of the original
// size, so already-compressed payloads (meshopt/Draco GLB, KTX2) are skipped.
import { readdir, readFile, writeFile, stat } from "node:fs/promises";
import { extname, join, relative } from "node:path";
import zlib from "node:zlib";

const root = process.argv[2] ?? "dist";
const MIN_BYTES = 1024;
const MIN_SAVING = 0.1;
const LARGE_BYTES = 4 * 1024 * 1024;
const TEXT_EXTENSIONS = new Set([
  ".js", ".mjs", ".css", ".html", ".svg", ".json", ".webmanifest", ".txt", ".xml", ".map",
]);
const BINARY_EXTENSIONS = new Set([".wasm", ".glb", ".gltf", ".vrm", ".bin", ".ttf", ".otf", ".ico"]);
const { constants } = zlib;

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else if (entry.isFile()) yield path;
  }
}

function variants(data, isText) {
  const large = data.length > LARGE_BYTES;
  const out = [
    [
      ".br",
      zlib.brotliCompressSync(data, {
        params: {
          [constants.BROTLI_PARAM_QUALITY]: large ? 9 : 11,
          [constants.BROTLI_PARAM_MODE]: isText ? constants.BROTLI_MODE_TEXT : constants.BROTLI_MODE_GENERIC,
          [constants.BROTLI_PARAM_SIZE_HINT]: data.length,
        },
      }),
    ],
    [".gz", zlib.gzipSync(data, { level: 9 })],
  ];
  if (typeof zlib.zstdCompressSync === "function") {
    out.push([
      ".zst",
      zlib.zstdCompressSync(data, { params: { [constants.ZSTD_c_compressionLevel]: large ? 12 : 19 } }),
    ]);
  }
  return out;
}

let files = 0;
let written = 0;
let before = 0;
let after = 0;
for await (const path of walk(root)) {
  const ext = extname(path).toLowerCase();
  const isText = TEXT_EXTENSIONS.has(ext);
  if (!isText && !BINARY_EXTENSIONS.has(ext)) continue;
  const { size } = await stat(path);
  if (size < MIN_BYTES) continue;
  const data = await readFile(path);
  files += 1;
  let best = size;
  for (const [suffix, compressed] of variants(data, isText)) {
    if (compressed.length > size * (1 - MIN_SAVING)) continue;
    await writeFile(path + suffix, compressed);
    written += 1;
    best = Math.min(best, compressed.length);
  }
  before += size;
  after += best;
  if (size > LARGE_BYTES) {
    console.log(`  ${relative(root, path)}: ${(size / 1048576).toFixed(1)} MiB -> ${(best / 1048576).toFixed(1)} MiB`);
  }
}
const mib = (n) => (n / 1048576).toFixed(2);
console.log(
  `precompress: ${files} files, ${written} variants, ${mib(before)} MiB -> ${mib(after)} MiB (best encoding)` +
    (typeof zlib.zstdCompressSync === "function" ? "" : " [zstd unavailable in this Node]"),
);
