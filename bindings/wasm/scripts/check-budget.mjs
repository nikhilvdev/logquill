// Fails when the WASM build gets bigger or slower than budget.json allows. Run after `npm run build` and
// `npm run build:sdk` (it measures dist/ and runs scripts/bench.mjs). `--budget file` checks against another budget. Prints every line so a pass shows its headroom.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const args = process.argv.slice(2);
const budget = JSON.parse(readFileSync(args.includes("--budget") ? args[args.indexOf("--budget") + 1] : here("../budget.json"), "utf8"));

const wasm = readFileSync(here("../dist/logquill_wasm_bg.wasm"));
const bundle = readFileSync(here("../dist/browser.mjs"));
const bench = JSON.parse(execFileSync("node", [here("bench.mjs"), "--json"], { encoding: "utf8" }));

const rows = [
  ["module, gzip (bytes)", gzipSync(wasm, { level: 9 }).length, budget.size.moduleGzipBytes],
  ["module, raw (bytes)", wasm.length, budget.size.moduleRawBytes],
  ["SDK bundle, gzip (bytes)", gzipSync(bundle, { level: 9 }).length, budget.size.sdkBundleGzipBytes],
  ["cold compile (ms)", bench.compileMs, budget.speed.coldCompileMs],
  ...Object.entries(budget.speed.nsPerCall).map(([name, limit]) => {
    if (!(name in bench.nsPerCall)) throw new Error(`budget.json names "${name}", which scripts/bench.mjs does not measure`);
    return [`${name} (ns/call)`, bench.nsPerCall[name], limit];
  }),
];

let over = 0;
for (const [name, got, limit] of rows) {
  const ok = got <= limit;
  if (!ok) over += 1;
  console.log(`${ok ? "ok  " : "OVER"} ${name.padEnd(58)} ${String(got).padStart(8)} / ${String(limit).padStart(8)}  (${Math.round((got / limit) * 100)}%)`);
}
if (over > 0) {
  console.error(`\n${over} over budget. If the growth is deliberate, say why in docs/stages/stage-7.md and raise budget.json in the same change.`);
  process.exit(1);
}
