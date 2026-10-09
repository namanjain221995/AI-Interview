'use strict';
// Compile step for JavaScript / TypeScript submissions.
//
//   node --experimental-vm-modules --no-warnings compile.js <js|ts> <source> <out> [<footer> <harness>]
//
// TypeScript is turned into JavaScript with Node's built-in type stripping
// (module.stripTypeScriptTypes): erasable syntax is replaced by whitespace so
// line/column numbers are preserved; enums, namespaces and parameter
// properties fall back to the "transform" mode with an inline source map.
// No type checking happens (documented trade-off: it would add ~1 s per run).
// The result is syntax-checked as CommonJS first and as an ES module second,
// mirroring Node's own module-type detection. Errors are rethrown uncaught so
// Node prints its usual code frame; the sandbox service trims the stack.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { stripTypeScriptTypes } = require('node:module');

const [lang, sourcePath, outPath, footerPath, harnessPath] = process.argv.slice(2);
const displayName = path.basename(sourcePath);
let code = fs.readFileSync(sourcePath, 'utf8');
if (code.charCodeAt(0) === 0xfeff) code = code.slice(1);

if (lang === 'ts') {
  let mode = 'strip';
  try {
    code = stripTypeScriptTypes(code, { mode });
  } catch (error) {
    mode = error && error.code === 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX' ? 'transform' : 'invalid';
  }
  if (mode === 'transform') {
    code = stripTypeScriptTypes(code, { mode: 'transform', sourceMap: true, sourceUrl: displayName });
  } else if (mode === 'invalid') {
    // Parse again outside the catch block so the error escapes from Node's own
    // parser frame and Node prints the TypeScript code frame (not this file).
    stripTypeScriptTypes(code, { mode: 'strip' });
  }
}

const ESM_HINTS = [
  'Cannot use import statement outside a module',
  "Unexpected token 'export'",
  'Cannot use import.meta outside a module',
  'await is only valid in async functions and the top level bodies of modules',
];

try {
  vm.compileFunction(code, ['exports', 'require', 'module', '__filename', '__dirname'], { filename: displayName });
} catch (cjsError) {
  if (!(cjsError instanceof SyntaxError)) throw cjsError;
  let esmError = null;
  try {
    new vm.SourceTextModule(code, { identifier: displayName });
  } catch (error) {
    esmError = error;
  }
  if (esmError) {
    throw ESM_HINTS.some((hint) => String(cjsError.message).includes(hint)) ? esmError : cjsError;
  }
}

let output = code;
if (footerPath) output += '\n' + fs.readFileSync(footerPath, 'utf8');
if (harnessPath) output += '\n' + fs.readFileSync(harnessPath, 'utf8');
fs.writeFileSync(outPath, output);
