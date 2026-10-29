// Speed of the WASM core, measured where it runs fastest to measure: Node, calling pkg/ directly. Prints ns per call
// for the operations a logger does on every record, plus how long V8 takes to compile the module. `--json` prints
// the numbers for scripts/check-budget.mjs; `--wasm path` measures a different build (the size experiments use it).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import * as q from "../js/index.mjs";

const args = process.argv.slice(2);
const wasmPath = args.includes("--wasm") ? args[args.indexOf("--wasm") + 1] : fileURLToPath(new URL("../pkg/logquill_wasm_bg.wasm", import.meta.url));
const bytes = readFileSync(wasmPath);

// compile time: only the first compile in a process is real, because V8 keeps compiled modules it has already seen
// (a later `new WebAssembly.Module` of the same bytes takes ~0.1 ms), so this is one cold measurement, not a median
const t0 = performance.now();
new WebAssembly.Module(bytes);
const coldCompileMs = performance.now() - t0;
q.initSync(bytes);

const record = JSON.stringify({
  schema_version: "2.0", timestamp: "2026-01-01T00:00:00.000Z", level: "INFO", logger: "app.web", message: "checkout completed",
  meta: { user_id: 42, cart: { items: 3, total: 59.9 }, route: "/checkout", ms: 118 },
});
const keyMeta = JSON.stringify({ user: "a", password: "hunter2", token: "t", nested: { api_key: "k", ok: [1, 2, { secret: "s" }] }, a: 1, b: 2, c: "three", d: [4, 5] });
const piiMeta = JSON.stringify({
  note: "customer alice@example.com asked about order 1234 on 2026-01-01, phone 415-555-0100 for follow up please",
  detail: "no personal data in this sentence at all, just a plain description of what the request was about",
  ids: ["a1b2c3", "d4e5f6"], deep: { text: "Bearer abcdefghijklmnopqrstuvwxyz0123456789 was sent" },
});
const prose = "the quick brown fox jumps over the lazy dog and keeps going without ever naming anyone or anything private. ".repeat(40);
const keys = new q.KeyRedactor();
const pii = new q.PiiRedactor();

const cases = {
  "formatJson (one record)": () => q.formatJson(record),
  "KeyRedactor.redactMeta": () => keys.redactMeta(keyMeta),
  "PiiRedactor.redactMeta (3 strings, 2 hits)": () => pii.redactMeta(piiMeta),
  "PiiRedactor.redactText (4 KB, no hits)": () => pii.redactText(prose),
};

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
function nsPerCall(fn) {
  for (let i = 0; i < 2000; i++) fn(); // warm up
  const rounds = [];
  for (let r = 0; r < 7; r++) {
    let n = 0;
    const start = performance.now();
    while (performance.now() - start < 120) {
      for (let i = 0; i < 200; i++) fn();
      n += 200;
    }
    rounds.push(((performance.now() - start) * 1e6) / n);
  }
  return Math.round(median(rounds));
}

const result = { compileMs: Math.round(coldCompileMs * 10) / 10, nsPerCall: {} };
for (const [name, fn] of Object.entries(cases)) result.nsPerCall[name] = nsPerCall(fn);

if (args.includes("--json")) {
  console.log(JSON.stringify(result));
} else {
  console.log(`module ${(bytes.length / 1024).toFixed(0)} KiB, cold V8 compile ${result.compileMs} ms`);
  for (const [name, ns] of Object.entries(result.nsPerCall)) console.log(`${name.padEnd(46)} ${String(ns).padStart(8)} ns/call`);
}
