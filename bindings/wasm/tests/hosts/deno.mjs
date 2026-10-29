// The WASM build in Deno: loads the .wasm from disk, runs the shared harness and the SDK checks, exits non-zero on any failure.
//   deno run --allow-read tests/hosts/deno.mjs
import * as sdk from "../../dist/browser.mjs";
import * as q from "../../js/index.mjs";
import { merge, report, runAll } from "./harness.mjs";
import { runSdk } from "./sdk-harness.mjs";

q.initSync(await Deno.readFile(new URL("../../pkg/logquill_wasm_bg.wasm", import.meta.url)));

const result = merge(runAll(q), await runSdk(sdk, await Deno.readFile(new URL("../../dist/logquill_wasm_bg.wasm", import.meta.url))));
console.log(report(result));
Deno.exit(result.failures.length === 0 ? 0 : 1);
