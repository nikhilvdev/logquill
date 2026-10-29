// Runs the shared corpus and golden checks against the C library, through Deno's FFI.
//   cargo build -p logquill-ffi && (cd bindings/wasm && npm run build:hosts) &&
//   deno run --allow-ffi --allow-read --allow-env crates/logquill-ffi/tests/deno/run.mjs
// Groups the C ABI does not expose yet are listed here by name; the harness counts only what it runs.
import { report, runAll } from "../../../../bindings/wasm/tests/hosts/harness.mjs";
import * as q from "./adapter.mjs";

// stateful plugin classes: not in the C ABI's first logger config (docs/stages/stage-8.md, open points)
const NOT_EXPOSED = ["sampling", "rate-limit", "adaptive-sampling", "flight-recorder", "run-summary", "run-plugin", "alert-dedupe"];

const result = runAll(q, { skip: NOT_EXPOSED });
console.log(report({ ...result, host: `logquill-ffi through Deno ${Deno.version.deno} (skipping ${NOT_EXPOSED.length} stateful groups)` }));
Deno.exit(result.failures.length === 0 ? 0 : 1);
