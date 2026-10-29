// The WASM build in Bun: loads the .wasm from disk, runs the shared harness and the SDK checks, exits non-zero on any failure.
//   bun run tests/hosts/bun.mjs
import * as sdk from "../../dist/browser.mjs";
import * as q from "../../js/index.mjs";
import { merge, report, runAll } from "./harness.mjs";
import { runSdk } from "./sdk-harness.mjs";

q.initSync(await Bun.file(new URL("../../pkg/logquill_wasm_bg.wasm", import.meta.url)).arrayBuffer());

const result = merge(runAll(q), await runSdk(sdk, await Bun.file(new URL("../../dist/logquill_wasm_bg.wasm", import.meta.url)).arrayBuffer()));
console.log(report(result).replace(/^unknown:/m, `Bun ${Bun.version}:`));
process.exit(result.failures.length === 0 ? 0 : 1);
