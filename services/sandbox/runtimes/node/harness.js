// ---- sandbox test harness (appended after the candidate's code) ----------
// Runs in the candidate's module scope. The service-generated footer placed
// just above stores a callable in globalThis.__sandboxEntry. Results go to
// fd 3 as JSON lines; expected values are never given to this process.
;(async function __sandboxHarness() {
  'use strict';
  const fs = process.getBuiltinModule('node:fs');
  const RESULTS_FD = 3;
  const emit = (record) => {
    const data = Buffer.from(JSON.stringify(record) + '\n', 'utf8');
    let offset = 0;
    while (offset < data.length) offset += fs.writeSync(RESULTS_FD, data, offset);
  };
  const special = (n) => (Number.isNaN(n) ? 'NaN' : n > 0 ? 'Infinity' : '-Infinity');
  const toJsonable = (value, depth) => {
    if (depth > 200) throw new Error('return value is nested too deeply');
    if (value === undefined || value === null) return null;
    switch (typeof value) {
      case 'number':
        return Number.isFinite(value) ? value : special(value);
      case 'bigint':
        return Number.isSafeInteger(Number(value)) ? Number(value) : value.toString();
      case 'string':
      case 'boolean':
        return value;
      case 'function':
      case 'symbol':
        throw new Error(`cannot report a value of type ${typeof value}`);
      default:
        break;
    }
    if (Array.isArray(value)) return value.map((v) => toJsonable(v, depth + 1));
    if (ArrayBuffer.isView(value)) return Array.from(value, (v) => toJsonable(v, depth + 1));
    if (value instanceof Set) return Array.from(value, (v) => toJsonable(v, depth + 1));
    if (value instanceof Map) {
      const out = {};
      for (const [k, v] of value) out[String(k)] = toJsonable(v, depth + 1);
      return out;
    }
    if (typeof value[Symbol.iterator] === 'function') return Array.from(value, (v) => toJsonable(v, depth + 1));
    const out = {};
    for (const k of Object.keys(value)) out[k] = toJsonable(value[k], depth + 1);
    return out;
  };
  const describe = (error) => {
    if (!(error instanceof Error)) return `Uncaught ${typeof error === 'string' ? error : JSON.stringify(error) ?? String(error)}`;
    const stack = String(error.stack || `${error.name}: ${error.message}`).split('\n');
    const kept = stack.filter((line, idx) => idx === 0 || !/__sandbox|node:internal|\(native\)|<anonymous>/.test(line));
    return kept.join('\n');
  };
  const convert = (value, type) => {
    if (value === null || value === undefined) return value;
    if (type && type.endsWith('[]') && Array.isArray(value)) {
      const inner = type.slice(0, -2);
      return value.map((v) => convert(v, inner));
    }
    return value;
  };

  let payload;
  try {
    payload = JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch (error) {
    emit({ t: 'fatal', kind: 'harness', message: `could not read test input: ${error && error.message}` });
    process.exit(1);
  }
  const entry = globalThis.__sandboxEntry;
  if (typeof entry !== 'function') {
    const wanted = (payload.entry_candidates || [])[0] || '?';
    emit({ t: 'fatal', kind: 'entry', message: `Function '${wanted}' is not defined (declare a top-level function named ${wanted}).` });
    process.exit(1);
  }
  const types = (payload.params || []).map((p) => p.type);
  emit({ t: 'ready' });
  for (const test of payload.tests || []) {
    const index = test.i;
    emit({ t: 'begin', i: index });
    const args = (test.args || []).map((a, k) => convert(a, types[k]));
    const started = performance.now();
    try {
      let value = entry(args);
      if (value && typeof value.then === 'function') value = await value;
      const ms = performance.now() - started;
      let actual;
      try {
        actual = toJsonable(value, 0);
      } catch (error) {
        emit({ t: 'result', i: index, ok: false, error_type: 'runtime', error: `invalid return value: ${error.message}` });
        continue;
      }
      emit({ t: 'result', i: index, ok: true, actual, ms });
    } catch (error) {
      const ms = performance.now() - started;
      const heap = error instanceof RangeError && /Array buffer allocation failed/.test(String(error.message));
      emit({ t: 'result', i: index, ok: false, error_type: heap ? 'memory' : 'runtime', error: describe(error), ms });
    }
  }
  emit({ t: 'done' });
  process.exit(0);
})();
